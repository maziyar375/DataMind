"""PostgreSQL connector.

Three defences layered on top of the SQL guard, because the guard is a
correctness argument and these are containment:

1. The connection is expected to use a role with no write grants. `probe()`
   verifies this by attempting a write inside a transaction it always rolls
   back, and reports `readonly_confirmed` honestly either way.
2. Every statement runs inside `BEGIN READ ONLY`, so even a guard bypass
   cannot mutate.
3. `statement_timeout` is set per session, so a pathological query is the
   database's problem for a bounded number of milliseconds.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from app.core.errors import ConnectorError
from app.domain.ports.database import (
    ColumnInfo, ConnectionProbe, QueryResult, RelationshipInfo,
    ResultColumn, SchemaSnapshot, TableInfo,
)

_NUMERIC_TYPES = {
    "smallint", "integer", "bigint", "decimal", "numeric", "real",
    "double precision", "money", "int2", "int4", "int8", "float4", "float8",
}
_TEMPORAL_TYPES = {
    "date", "timestamp", "timestamptz", "timestamp with time zone",
    "timestamp without time zone", "time", "timetz", "interval",
}

_TABLE_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
       c.is_nullable, c.ordinal_position
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE t.table_type = 'BASE TABLE'
  AND c.table_schema = ANY($1::text[])
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

_PK_SQL = """
SELECT tc.table_schema, tc.table_name, kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name
 AND kcu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND tc.table_schema = ANY($1::text[])
"""

_FK_SQL = """
SELECT tc.table_schema AS from_schema, tc.table_name AS from_table,
       kcu.column_name AS from_column,
       ccu.table_schema AS to_schema, ccu.table_name AS to_table,
       ccu.column_name AS to_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name
 AND kcu.table_schema = tc.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = ANY($1::text[])
"""

_ROWCOUNT_SQL = """
SELECT n.nspname AS schema, c.relname AS name, c.reltuples::bigint AS approx
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = ANY($1::text[])
"""


class PostgresConnector:
    dialect = "postgres"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        ssl_mode: str | None = None,
        connect_timeout: int = 10,
    ) -> None:
        self._dsn_parts = {
            "host": host, "port": port, "database": database,
            "user": username, "password": password,
        }
        self._ssl = None if ssl_mode in (None, "disable") else "require"
        self._connect_timeout = connect_timeout
        self._pool: asyncpg.Pool | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    async def _acquire(self) -> asyncpg.Pool:
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    **self._dsn_parts,
                    ssl=self._ssl,
                    min_size=1,
                    max_size=4,
                    timeout=self._connect_timeout,
                    command_timeout=60,
                )
            except Exception as err:
                raise ConnectorError(f"Could not connect: {_clean(err)}") from err
        assert self._pool is not None
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ── probe ────────────────────────────────────────────────────────────
    async def probe(self) -> ConnectionProbe:
        started = time.perf_counter()
        try:
            pool = await self._acquire()
            async with pool.acquire() as conn:
                version = await conn.fetchval("SELECT version()")
                readonly = await self._verify_readonly(conn)
        except ConnectorError as err:
            return ConnectionProbe(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                message=err.message,
            )
        except Exception as err:
            return ConnectionProbe(
                ok=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                message=_clean(err),
            )
        return ConnectionProbe(
            ok=True,
            latency_ms=int((time.perf_counter() - started) * 1000),
            server_version=str(version).split(" on ")[0] if version else None,
            readonly_confirmed=readonly,
            message="Connected" + (" · read-only role confirmed" if readonly else ""),
        )

    async def _verify_readonly(self, conn: asyncpg.Connection) -> bool:
        """True only if the role genuinely cannot write.

        A superuser pointed at this method returns False. That asymmetry is
        the point: the milestone asserts both directions.
        """
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE TEMP TABLE _raymand_probe (x int)")
        except asyncpg.InsufficientPrivilegeError:
            await tx.rollback()
            return True
        except Exception:
            await tx.rollback()
            return False
        else:
            await tx.rollback()
            return False

    # ── introspection ────────────────────────────────────────────────────
    async def introspect(self, *, schema_allowlist: list[str]) -> SchemaSnapshot:
        schemas = schema_allowlist or ["public"]
        pool = await self._acquire()
        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
            col_rows = await conn.fetch(_TABLE_SQL, schemas)
            pk_rows = await conn.fetch(_PK_SQL, schemas)
            fk_rows = await conn.fetch(_FK_SQL, schemas)
            count_rows = await conn.fetch(_ROWCOUNT_SQL, schemas)

        pks = {(r["table_schema"], r["table_name"], r["column_name"]) for r in pk_rows}
        fks = {
            (r["from_schema"], r["from_table"], r["from_column"]):
                f"{r['to_schema']}.{r['to_table']}.{r['to_column']}"
            for r in fk_rows
        }
        counts = {(r["schema"], r["name"]): int(r["approx"] or 0) for r in count_rows}

        grouped: dict[tuple[str, str], list[ColumnInfo]] = {}
        for r in col_rows:
            key = (r["table_schema"], r["table_name"])
            ident = (r["table_schema"], r["table_name"], r["column_name"])
            grouped.setdefault(key, []).append(
                ColumnInfo(
                    name=r["column_name"],
                    data_type=r["data_type"],
                    nullable=r["is_nullable"] == "YES",
                    is_primary_key=ident in pks,
                    is_foreign_key=ident in fks,
                    references=fks.get(ident),
                )
            )

        tables = [
            TableInfo(
                schema=schema, name=name, columns=cols,
                approx_row_count=counts.get((schema, name)),
            )
            for (schema, name), cols in sorted(grouped.items())
        ]
        relationships = [
            RelationshipInfo(
                from_table=f"{r['from_schema']}.{r['from_table']}",
                from_column=r["from_column"],
                to_table=f"{r['to_schema']}.{r['to_table']}",
                to_column=r["to_column"],
            )
            for r in fk_rows
        ]
        return SchemaSnapshot(
            dialect=self.dialect,
            tables=tables,
            relationships=relationships,
            server_version=str(version).split(" on ")[0] if version else None,
        )

    # ── execution ────────────────────────────────────────────────────────
    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        pool = await self._acquire()
        started = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    await conn.execute(
                        f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"
                    )
                    records = await conn.fetch(sql)
        except asyncpg.QueryCanceledError as err:
            raise ConnectorError(
                f"Query exceeded the {statement_timeout_ms}ms statement timeout."
            ) from err
        except asyncpg.PostgresError as err:
            raise ConnectorError(_clean(err)) from err

        duration_ms = int((time.perf_counter() - started) * 1000)
        if not records:
            return QueryResult(columns=[], rows=[], row_count=0, duration_ms=duration_ms)

        columns = [
            ResultColumn(
                name=key,
                db_type=_python_to_db_type(records[0][key]),
                semantic_type=_semantic_type(records[0][key]),
            )
            for key in records[0].keys()
        ]
        truncated = len(records) > max_rows
        rows = [
            [_json_safe(value) for value in record.values()]
            for record in records[:max_rows]
        ]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=duration_ms,
        )

    async def explain(self, sql: str) -> int | None:
        """Estimated rows scanned. Powers the metadata chip in the chat UI."""
        pool = await self._acquire()
        try:
            async with pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    plan = await conn.fetchval(
                        f"EXPLAIN (FORMAT JSON) {sql}"
                    )
        except Exception:
            return None

        try:
            import json

            parsed = json.loads(plan) if isinstance(plan, str) else plan
            return int(parsed[0]["Plan"]["Plan Rows"])
        except Exception:
            return None


# ── helpers ──────────────────────────────────────────────────────────────
def _semantic_type(value: Any) -> str:
    if isinstance(value, bool):
        return "nominal"
    if isinstance(value, (int, float, Decimal)):
        return "quantitative"
    if isinstance(value, (datetime, date)):
        return "temporal"
    return "nominal"


def _python_to_db_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, (float, Decimal)):
        return "numeric"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    return "text"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return "<binary>"
    return value


def _clean(err: Exception) -> str:
    """Driver messages can echo the DSN. Never let a password reach a log."""
    text = str(err)
    for secret in ("password=", "PASSWORD="):
        if secret in text:
            return "Database error (details withheld to avoid leaking credentials)."
    return text[:400]
