"""Oracle connector.

Uses python-oracledb in thin mode, so no Oracle Instant Client is required —
the driver speaks the wire protocol directly, which is what keeps the image
free of a vendor client install.

Two Oracle-specific mappings are worth stating plainly:

* `database_name` is the **service name**, not a catalogue. Oracle reaches a
  database through a listener service (`host:port/service`), so that is how
  the field is used here.
* A *schema* is a *user*. `ALL_TAB_COLUMNS.OWNER` is the schema, and the
  allowlist therefore defaults to the connecting user's own schema rather
  than to a name like `public`.

The `ALL_*` catalogue views are used rather than `USER_*` or `DBA_*`: they
show exactly what the connecting role has been granted, which both respects
a read-only grant and still sees tables owned by another schema.
"""
from __future__ import annotations

import contextlib
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import oracledb

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
SELECT c.owner, c.table_name, c.column_name, c.data_type,
       c.nullable, c.column_id
FROM all_tab_columns c
JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
WHERE c.owner IN ({placeholders})
ORDER BY c.owner, c.table_name, c.column_id
"""

_PK_SQL = """
SELECT cc.owner, cc.table_name, cc.column_name
FROM all_constraints con
JOIN all_cons_columns cc
  ON cc.owner = con.owner AND cc.constraint_name = con.constraint_name
WHERE con.constraint_type = 'P'
  AND con.owner IN ({placeholders})
ORDER BY cc.owner, cc.table_name, cc.position
"""

# R-type constraints name the unique/primary constraint they point at, so the
# target column comes from resolving r_constraint_name, matched on position to
# keep composite keys paired rather than crossed.
_FK_SQL = """
SELECT src.owner AS from_schema, src.table_name AS from_table,
       src.column_name AS from_column,
       tgt.owner AS to_schema, tgt.table_name AS to_table,
       tgt.column_name AS to_column
FROM all_constraints con
JOIN all_cons_columns src
  ON src.owner = con.owner AND src.constraint_name = con.constraint_name
JOIN all_cons_columns tgt
  ON tgt.owner = con.r_owner
 AND tgt.constraint_name = con.r_constraint_name
 AND tgt.position = src.position
WHERE con.constraint_type = 'R'
  AND con.owner IN ({placeholders})
ORDER BY src.owner, src.table_name, src.position
"""

_ROWCOUNT_SQL = """
SELECT owner, table_name, num_rows
FROM all_tables
WHERE owner IN ({placeholders})
"""


class OracleConnector:
    dialect = "oracle"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        ssl_mode: str | None = None,
        connect_timeout: int = 15,
    ) -> None:
        self._dsn = f"{host}:{port}/{database}"
        self._username = username
        self._password = password
        # Oracle folds unquoted identifiers to upper case, so the connecting
        # user's own schema is its upper-cased name.
        self._default_schema = username.upper()
        self._connect_timeout = connect_timeout
        self._pool: Any = None

    # ── lifecycle ────────────────────────────────────────────────────────
    async def _acquire(self) -> Any:
        if self._pool is None:
            try:
                self._pool = oracledb.create_pool_async(
                    user=self._username,
                    password=self._password,
                    dsn=self._dsn,
                    min=1,
                    max=4,
                    increment=1,
                )
            except Exception as err:
                raise ConnectorError(f"Could not connect: {_clean(err)}") from err
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            with contextlib.suppress(Exception):
                await self._pool.close()
            self._pool = None

    # ── probe ────────────────────────────────────────────────────────────
    async def probe(self) -> ConnectionProbe:
        started = time.perf_counter()
        try:
            pool = await self._acquire()
            async with pool.acquire() as conn:
                conn.call_timeout = self._connect_timeout * 1000
                with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT banner FROM v$version WHERE ROWNUM = 1"
                    )
                    row = await cur.fetchone()
                    version = row[0] if row else None
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
            server_version=str(version) if version else None,
            readonly_confirmed=readonly,
            message="Connected" + (" · read-only role confirmed" if readonly else ""),
        )

    async def _verify_readonly(self, conn: Any) -> bool:
        """True only if the role genuinely cannot write.

        CREATE TABLE needs an explicit privilege in Oracle and is rolled back
        either way, so a role that can run it is not read-only.
        """
        try:
            with conn.cursor() as cur:
                await cur.execute("CREATE TABLE raymand_probe_tmp (x NUMBER)")
        except Exception:
            await conn.rollback()
            return True
        with contextlib.suppress(Exception), conn.cursor() as cur:
            await cur.execute("DROP TABLE raymand_probe_tmp")
        await conn.rollback()
        return False

    # ── introspection ────────────────────────────────────────────────────
    async def introspect(self, *, schema_allowlist: list[str]) -> SchemaSnapshot:
        schemas = [s.upper() for s in schema_allowlist] or [self._default_schema]
        # Oracle binds by name; positional :1 style keeps the IN list simple.
        marks = ", ".join(f":{i + 1}" for i in range(len(schemas)))

        pool = await self._acquire()
        async with pool.acquire() as conn:
            with conn.cursor() as cur:
                await cur.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
                row = await cur.fetchone()
                version = row[0] if row else None

                await cur.execute(_TABLE_SQL.format(placeholders=marks), schemas)
                col_rows = await cur.fetchall()
                await cur.execute(_PK_SQL.format(placeholders=marks), schemas)
                pk_rows = await cur.fetchall()
                await cur.execute(_FK_SQL.format(placeholders=marks), schemas)
                fk_rows = await cur.fetchall()
                await cur.execute(_ROWCOUNT_SQL.format(placeholders=marks), schemas)
                count_rows = await cur.fetchall()

        pks = {(r[0], r[1], r[2]) for r in pk_rows}
        fks = {(r[0], r[1], r[2]): f"{r[3]}.{r[4]}.{r[5]}" for r in fk_rows}
        counts = {(r[0], r[1]): int(r[2] or 0) for r in count_rows}

        grouped: dict[tuple[str, str], list[ColumnInfo]] = {}
        for owner, table, column, data_type, nullable, _pos in col_rows:
            ident = (owner, table, column)
            grouped.setdefault((owner, table), []).append(
                ColumnInfo(
                    name=column,
                    data_type=data_type,
                    nullable=nullable == "Y",
                    is_primary_key=ident in pks,
                    is_foreign_key=ident in fks,
                    references=fks.get(ident),
                )
            )

        tables = [
            TableInfo(
                schema=owner, name=name, columns=cols,
                approx_row_count=counts.get((owner, name)),
            )
            for (owner, name), cols in sorted(grouped.items())
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
            server_version=str(version) if version else None,
        )

    # ── execution ────────────────────────────────────────────────────────
    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        pool = await self._acquire()
        started = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                # call_timeout is the driver-side bound; SET TRANSACTION
                # READ ONLY is the server-side one.
                conn.call_timeout = int(statement_timeout_ms)
                with conn.cursor() as cur:
                    await cur.execute("SET TRANSACTION READ ONLY")
                    try:
                        await cur.execute(sql)
                        description = cur.description
                        records = await cur.fetchmany(max_rows + 1)
                    finally:
                        await conn.rollback()
        except Exception as err:
            message = _clean(err)
            # DPY-4011/ORA-03156 surface a call_timeout as a cancelled call.
            if "timeout" in message.lower() or "DPY-4011" in message:
                raise ConnectorError(
                    f"Query exceeded the {statement_timeout_ms}ms statement timeout."
                ) from err
            raise ConnectorError(message) from err

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

    async def explain(self, sql: str) -> int | None:
        """Estimated rows scanned.

        EXPLAIN PLAN writes to PLAN_TABLE, which a genuinely read-only role
        cannot do. Returning None then is correct: the estimate is a nicety,
        and a read-only grant is the configuration we recommend.
        """
        pool = await self._acquire()
        try:
            async with pool.acquire() as conn:
                with conn.cursor() as cur:
                    await cur.execute(f"EXPLAIN PLAN FOR {sql}")
                    await cur.execute(
                        "SELECT cardinality FROM plan_table "
                        "WHERE id = 0 ORDER BY timestamp DESC FETCH FIRST 1 ROWS ONLY"
                    )
                    row = await cur.fetchone()
                    await conn.rollback()
        except Exception:
            return None
        try:
            return int(row[0]) if row and row[0] is not None else None
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
        return "number"
    if isinstance(value, float):
        return "binary_double"
    if isinstance(value, Decimal):
        return "number"
    if isinstance(value, datetime):
        return "timestamp"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "blob"
    return "varchar2"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, oracledb.LOB):
        return None
    return value


def _clean(err: Exception) -> str:
    text = str(err).strip() or err.__class__.__name__
    return text.splitlines()[0][:300]
