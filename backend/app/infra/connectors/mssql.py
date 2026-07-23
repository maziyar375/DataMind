"""SQL Server connector.

pymssql is chosen over an ODBC binding deliberately: it ships wheels with
FreeTDS bundled, so the image needs no unixODBC stack and no Microsoft driver
repository. The cost is that the driver is synchronous, so every call is run
through `asyncio.to_thread` — the event loop is never blocked, which matters
because a single worker process serves the API and the run pipeline together.

SQL Server has no `READ ONLY` transaction mode of the kind PostgreSQL and
Oracle offer, so containment rests on two of the three layers rather than
three: the role's own grants, and a per-connection query timeout. `probe()`
still reports `readonly_confirmed` honestly by attempting a write and rolling
it back, which is how a misconfigured role is surfaced to the user.

Catalogue reads use `sys.*` rather than `INFORMATION_SCHEMA`: the former is
filtered by permission, so a reader sees the constraints on tables it can
select from, and the foreign-key views pair source and target columns
directly instead of requiring a join through a constraint name.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pymssql

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

_TABLE_SQL = """
SELECT s.name AS table_schema, t.name AS table_name, c.name AS column_name,
       ty.name AS data_type, c.is_nullable, c.column_id
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE s.name IN ({placeholders})
ORDER BY s.name, t.name, c.column_id
"""

_PK_SQL = """
SELECT s.name AS table_schema, t.name AS table_name, c.name AS column_name
FROM sys.key_constraints kc
JOIN sys.tables t ON t.object_id = kc.parent_object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic
  ON ic.object_id = t.object_id AND ic.index_id = kc.unique_index_id
JOIN sys.columns c
  ON c.object_id = t.object_id AND c.column_id = ic.column_id
WHERE kc.type = 'PK' AND s.name IN ({placeholders})
ORDER BY s.name, t.name, ic.key_ordinal
"""

_FK_SQL = """
SELECT ss.name AS from_schema, st.name AS from_table, sc.name AS from_column,
       ts.name AS to_schema, tt.name AS to_table, tc.name AS to_column
FROM sys.foreign_key_columns fkc
JOIN sys.tables st ON st.object_id = fkc.parent_object_id
JOIN sys.schemas ss ON ss.schema_id = st.schema_id
JOIN sys.columns sc
  ON sc.object_id = fkc.parent_object_id
 AND sc.column_id = fkc.parent_column_id
JOIN sys.tables tt ON tt.object_id = fkc.referenced_object_id
JOIN sys.schemas ts ON ts.schema_id = tt.schema_id
JOIN sys.columns tc
  ON tc.object_id = fkc.referenced_object_id
 AND tc.column_id = fkc.referenced_column_id
WHERE ss.name IN ({placeholders})
ORDER BY ss.name, st.name, fkc.constraint_column_id
"""

_ROWCOUNT_SQL = """
SELECT s.name AS table_schema, t.name AS table_name, SUM(p.rows) AS approx
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.partitions p
  ON p.object_id = t.object_id AND p.index_id IN (0, 1)
WHERE s.name IN ({placeholders})
GROUP BY s.name, t.name
"""


class MsSqlConnector:
    dialect = "mssql"

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
        self._host = host
        self._port = port
        self._database = database
        self._username = username
        self._password = password
        self._connect_timeout = connect_timeout

    # ── lifecycle ────────────────────────────────────────────────────────
    def _connect(self, *, query_timeout_s: int | None = None) -> Any:
        """Blocking. Only ever called inside a worker thread."""
        try:
            return pymssql.connect(
                server=self._host,
                port=str(self._port),
                user=self._username,
                password=self._password,
                database=self._database,
                login_timeout=self._connect_timeout,
                timeout=query_timeout_s or 0,
                as_dict=False,
            )
        except Exception as err:
            raise ConnectorError(f"Could not connect: {_clean(err)}") from err

    async def close(self) -> None:
        """Connections are per-operation and closed by their own thread."""
        return None

    # ── probe ────────────────────────────────────────────────────────────
    async def probe(self) -> ConnectionProbe:
        started = time.perf_counter()
        try:
            version, readonly = await asyncio.to_thread(self._probe_blocking)
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
            server_version=version,
            readonly_confirmed=readonly,
            message="Connected" + (" · read-only role confirmed" if readonly else ""),
        )

    def _probe_blocking(self) -> tuple[str | None, bool]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT @@VERSION")
                row = cur.fetchone()
                version = row[0].splitlines()[0] if row and row[0] else None

            readonly = False
            try:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE dbo.raymand_probe_tmp (x INT)")
            except Exception:
                readonly = True
            else:
                with contextlib.suppress(Exception), conn.cursor() as cur:
                    cur.execute("DROP TABLE dbo.raymand_probe_tmp")
            conn.rollback()
            return version, readonly
        finally:
            conn.close()

    # ── introspection ────────────────────────────────────────────────────
    async def introspect(self, *, schema_allowlist: list[str]) -> SchemaSnapshot:
        schemas = schema_allowlist or ["dbo"]
        return await asyncio.to_thread(self._introspect_blocking, schemas)

    def _introspect_blocking(self, schemas: list[str]) -> SchemaSnapshot:
        marks = ", ".join(["%s"] * len(schemas))
        params = tuple(schemas)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT @@VERSION")
                row = cur.fetchone()
                version = row[0].splitlines()[0] if row and row[0] else None

                cur.execute(_TABLE_SQL.format(placeholders=marks), params)
                col_rows = cur.fetchall()
                cur.execute(_PK_SQL.format(placeholders=marks), params)
                pk_rows = cur.fetchall()
                cur.execute(_FK_SQL.format(placeholders=marks), params)
                fk_rows = cur.fetchall()
                cur.execute(_ROWCOUNT_SQL.format(placeholders=marks), params)
                count_rows = cur.fetchall()
        finally:
            conn.close()

        pks = {(r[0], r[1], r[2]) for r in pk_rows}
        fks = {(r[0], r[1], r[2]): f"{r[3]}.{r[4]}.{r[5]}" for r in fk_rows}
        counts = {(r[0], r[1]): int(r[2] or 0) for r in count_rows}

        grouped: dict[tuple[str, str], list[ColumnInfo]] = {}
        for schema, table, column, data_type, nullable, _pos in col_rows:
            ident = (schema, table, column)
            grouped.setdefault((schema, table), []).append(
                ColumnInfo(
                    name=column,
                    data_type=data_type,
                    nullable=bool(nullable),
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
                from_table=f"{r[0]}.{r[1]}",
                from_column=r[2],
                to_table=f"{r[3]}.{r[4]}",
                to_column=r[5],
            )
            for r in fk_rows
        ]
        return SchemaSnapshot(
            dialect=self.dialect,
            tables=tables,
            relationships=relationships,
            server_version=version,
        )

    # ── execution ────────────────────────────────────────────────────────
    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        started = time.perf_counter()
        description, records = await asyncio.to_thread(
            self._execute_blocking, sql, max_rows, statement_timeout_ms
        )
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not description:
            return QueryResult(columns=[], rows=[], row_count=0, duration_ms=duration_ms)

        names = [d[0] for d in description]
        first = records[0] if records else None
        columns = [
            ResultColumn(
                name=name,
                db_type=_python_to_db_type(first[i]) if first else "text",
                semantic_type=_semantic_type(first[i]) if first else "nominal",
            )
            for i, name in enumerate(names)
        ]
        truncated = len(records) > max_rows
        rows = [[_json_safe(v) for v in record] for record in records[:max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=duration_ms,
        )

    def _execute_blocking(
        self, sql: str, max_rows: int, statement_timeout_ms: int
    ) -> tuple[Any, list[Any]]:
        # pymssql's query timeout is whole seconds, so round up rather than
        # down: a sub-second budget must not become "no timeout at all".
        timeout_s = max(1, math.ceil(statement_timeout_ms / 1000))
        conn = self._connect(query_timeout_s=timeout_s)
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(sql)
                    description = cur.description
                    records = cur.fetchmany(max_rows + 1) if description else []
                except Exception as err:
                    message = _clean(err)
                    if "timeout" in message.lower() or "timed out" in message.lower():
                        raise ConnectorError(
                            f"Query exceeded the {statement_timeout_ms}ms "
                            f"statement timeout."
                        ) from err
                    raise ConnectorError(message) from err
            conn.rollback()
            return description, list(records)
        finally:
            conn.close()

    async def explain(self, sql: str) -> int | None:
        """Estimated rows scanned. Powers the metadata chip in the chat UI."""
        try:
            return await asyncio.to_thread(self._explain_blocking, sql)
        except Exception:
            return None

    def _explain_blocking(self, sql: str) -> int | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SET SHOWPLAN_ALL ON")
                try:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    names = [d[0] for d in cur.description]
                finally:
                    cur.execute("SET SHOWPLAN_ALL OFF")
            index = names.index("EstimateRows")
            # Row 0 is the statement node and carries the plan's estimate.
            return int(float(rows[0][index])) if rows else None
        except Exception:
            return None
        finally:
            conn.close()


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
        return "bit"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, float):
        return "float"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, datetime):
        return "datetime2"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "varbinary"
    return "nvarchar"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _clean(err: Exception) -> str:
    text = str(err).strip() or err.__class__.__name__
    return text.splitlines()[0][:300]
