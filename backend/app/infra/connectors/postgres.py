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
    ColumnInfo,
    ConnectionProbe,
    QueryResult,
    RelationshipInfo,
    ResultColumn,
    SchemaSnapshot,
    TableInfo,
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

# Constraints come from pg_catalog, not information_schema.
#
# information_schema.table_constraints and constraint_column_usage only show
# rows for tables owned by a currently enabled role. DataMind is meant to
# connect as a read-only role that owns nothing, so those views return empty
# and every key silently disappears — no PK markers, no foreign keys, and an
# edgeless graph. pg_catalog carries no such filter.
#
# unnest(conkey, confkey) WITH ORDINALITY pairs each source column with the
# target column at the same position, which is what keeps a composite key
# from being expanded into a cross product.
_PK_SQL = """
SELECT ns.nspname AS table_schema, cls.relname AS table_name,
       att.attname AS column_name
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
WHERE con.contype = 'p'
  AND ns.nspname = ANY($1::text[])
ORDER BY table_schema, table_name, k.ord
"""

_FK_SQL = """
SELECT src_ns.nspname AS from_schema, src_cls.relname AS from_table,
       src_att.attname AS from_column,
       tgt_ns.nspname AS to_schema, tgt_cls.relname AS to_table,
       tgt_att.attname AS to_column
FROM pg_constraint con
JOIN pg_class src_cls ON src_cls.oid = con.conrelid
JOIN pg_namespace src_ns ON src_ns.oid = src_cls.relnamespace
JOIN pg_class tgt_cls ON tgt_cls.oid = con.confrelid
JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt_cls.relnamespace
JOIN LATERAL unnest(con.conkey, con.confkey)
     WITH ORDINALITY AS cols(src_attnum, tgt_attnum, ord) ON TRUE
JOIN pg_attribute src_att
  ON src_att.attrelid = con.conrelid AND src_att.attnum = cols.src_attnum
JOIN pg_attribute tgt_att
  ON tgt_att.attrelid = con.confrelid AND tgt_att.attnum = cols.tgt_attnum
WHERE con.contype = 'f'
  AND src_ns.nspname = ANY($1::text[])
ORDER BY from_schema, from_table, cols.ord
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
            async with pool.acquire() as conn, conn.transaction(readonly=True):
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
            # NB: iterate .keys() — an asyncpg Record iterates its *values*, so
            # `for key in records[0]` would yield data, not column names.
            for key in records[0].keys()  # noqa: SIM118
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
            async with pool.acquire() as conn, conn.transaction(readonly=True):
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
