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
from collections.abc import Mapping
from dataclasses import replace
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
from app.domain.value_objects import (
    HINT_MAX_CARDINALITY,
    HintBudget,
    is_sensitive_column,
)
from app.infra.connectors.comments import (
    business_schemas,
    fold_column_comments,
    fold_table_comments,
)
from app.infra.connectors.hints import (
    ColumnHints,
    apply_probe,
    clean_values,
    enforce_budget,
    normalise_distinct,
    null_fraction_from_counts,
    plan_probes,
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

# Catalog comments, from the ALL_* views for the reason at the top of this
# module: they show exactly what the connecting role was granted. Verified on
# 23.26 as a user holding nothing but CREATE SESSION and SELECT on one table —
# it saw that table's comments and no others, which is the read this feature
# most needed to be true.
#
# `table_type = 'TABLE'` because ALL_TAB_COMMENTS covers views and synonyms
# too, and the snapshot holds base tables only: without it a view's comment
# would be attached to nothing.
_TABLE_COMMENT_SQL = """
SELECT owner, table_name, comments
FROM all_tab_comments
WHERE owner IN ({placeholders})
  AND table_type = 'TABLE'
  AND comments IS NOT NULL
"""

_COLUMN_COMMENT_SQL = """
SELECT owner, table_name, column_name, comments
FROM all_col_comments
WHERE owner IN ({placeholders})
  AND comments IS NOT NULL
"""

# Oracle keeps the richest column statistics of the four engines: a distinct
# count and a null count per column, gathered by DBMS_STATS. Both are visible
# through ALL_* views to any role with SELECT on the table, so a read-only
# analytics user sees exactly its own tables.
_STATS_SQL = """
SELECT owner, table_name, column_name, num_distinct, num_nulls
FROM all_tab_col_statistics
WHERE owner IN ({placeholders})
"""

# For a *frequency* histogram — the kind Oracle builds precisely when a column
# has few distinct values — every endpoint is a real value, so this is the
# complete domain rather than a sample. ENDPOINT_ACTUAL_VALUE is only
# populated for character columns, which is the only place a value list is
# wanted anyway.
_HISTOGRAM_SQL = """
SELECT h.owner, h.table_name, h.column_name, h.endpoint_actual_value
FROM all_tab_histograms h
JOIN all_tab_col_statistics s
  ON s.owner = h.owner AND s.table_name = h.table_name
 AND s.column_name = h.column_name
WHERE h.owner IN ({placeholders})
  AND h.endpoint_actual_value IS NOT NULL
  AND s.histogram = 'FREQUENCY'
"""

_TEXT_TYPES = frozenset({
    "varchar2", "nvarchar2", "char", "nchar", "varchar", "clob", "nclob",
})


def _build_hints(
    *,
    col_rows: list[Any],
    stat_rows: list[Any],
    histogram_rows: list[Any],
    counts: dict[tuple[str, str], int],
) -> dict[tuple[str, str, str], ColumnHints]:
    """Fold DBMS_STATS counts and frequency histograms into hint records.

    Oracle reports a null *count* against the table's own `num_rows`, so the
    fraction is derived rather than read. Min/max are deliberately skipped:
    ALL_TAB_COLUMNS stores LOW_VALUE/HIGH_VALUE as type-encoded RAW, and
    decoding it correctly per type is far more machinery than a range hint is
    worth.
    """
    endpoints: dict[tuple[str, str, str], list[str]] = {}
    for owner, table, column, value in histogram_rows:
        endpoints.setdefault((owner, table, column), []).append(value)

    types = {(r[0], r[1], r[2]): str(r[3]).lower() for r in col_rows}
    hints: dict[tuple[str, str, str], ColumnHints] = {}

    for owner, table, column, num_distinct, num_nulls in stat_rows:
        ident = (owner, table, column)
        distinct = normalise_distinct(num_distinct, counts.get((owner, table)))
        record = ColumnHints(
            distinct_count=distinct,
            null_fraction=null_fraction_from_counts(
                int(num_nulls) if num_nulls is not None else None,
                counts.get((owner, table)),
            ),
        )
        if (
            types.get(ident) in _TEXT_TYPES
            and not is_sensitive_column(column)
            and distinct is not None
            and distinct <= HINT_MAX_CARDINALITY
        ):
            values = clean_values(endpoints.get(ident))
            # A frequency histogram has one endpoint per distinct value, so
            # anything short of that is not the whole domain.
            if len(values) == distinct:
                record = replace(record, sample_values=values)
        hints[ident] = record

    return hints


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

    async def _probe_values(
        self,
        hints: dict[tuple[str, str, str], ColumnHints],
        *,
        columns: Mapping[tuple[str, str, str], str],
        row_counts: Mapping[tuple[str, str], int | None],
    ) -> None:
        """Fill value lists DBMS_STATS could not, one bounded query each."""
        targets = plan_probes(
            columns=columns, known=hints, row_counts=row_counts,
            text_types=_TEXT_TYPES,
        )
        if not targets:
            return

        pool = await self._acquire()
        async with pool.acquire() as conn:
            with conn.cursor() as cur:
                rows_only = f"FETCH FIRST {HINT_MAX_CARDINALITY + 1} ROWS ONLY"
                for target in targets:
                    table, column = target.quoted()
                    # Escaped identifiers, and no user value in the statement.
                    sql = f"SELECT DISTINCT {column} FROM {table} {rows_only}"  # noqa: S608
                    rows = None
                    with contextlib.suppress(Exception):
                        await cur.execute(sql)
                        rows = await cur.fetchall()
                    if rows is not None:
                        apply_probe(hints, target, [r[0] for r in rows])

    # ── introspection ────────────────────────────────────────────────────
    async def introspect(
        self, *, schema_allowlist: list[str], hints: HintBudget = HintBudget()
    ) -> SchemaSnapshot:
        # A schema here is a *user*, so a production instance carries dozens of
        # Oracle's own: SYS, XDB, MDSYS, CTXSYS, APEX_*… Left in, the generator
        # would spend one model call per dictionary table and produce a semantic
        # layer describing Oracle. This is the engine that made the filter
        # necessary; every engine gets it.
        schemas = business_schemas(
            self.dialect,
            [s.upper() for s in schema_allowlist] or [self._default_schema],
        )
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

                stat_rows, histogram_rows = [], []
                # Skipped entirely when the policy could never emit them.
                # Hints are an accuracy aid, never a correctness dependency:
                # a schema whose statistics were never gathered, or a role
                # without access to the ALL_* stats views, simply yields none.
                if hints.stats:
                    with contextlib.suppress(Exception):
                        await cur.execute(
                            _STATS_SQL.format(placeholders=marks), schemas
                        )
                        stat_rows = await cur.fetchall()
                if hints.value_lists:
                    with contextlib.suppress(Exception):
                        await cur.execute(
                            _HISTOGRAM_SQL.format(placeholders=marks), schemas
                        )
                        histogram_rows = await cur.fetchall()

                # Comments regardless of the hint budget: a comment is DDL a
                # human wrote, not a statistic read out of the rows.
                table_comment_rows: list[Any] = []
                column_comment_rows: list[Any] = []
                with contextlib.suppress(Exception):
                    await cur.execute(
                        _TABLE_COMMENT_SQL.format(placeholders=marks), schemas
                    )
                    table_comment_rows = list(await cur.fetchall())
                with contextlib.suppress(Exception):
                    await cur.execute(
                        _COLUMN_COMMENT_SQL.format(placeholders=marks), schemas
                    )
                    column_comment_rows = list(await cur.fetchall())

        pks = {(r[0], r[1], r[2]) for r in pk_rows}
        fks = {(r[0], r[1], r[2]): f"{r[3]}.{r[4]}.{r[5]}" for r in fk_rows}
        counts = {(r[0], r[1]): int(r[2] or 0) for r in count_rows}

        captured = _build_hints(
            col_rows=col_rows, stat_rows=stat_rows,
            histogram_rows=histogram_rows, counts=counts,
        )
        if hints.value_lists:
            await self._probe_values(
                captured,
                columns={(r[0], r[1], r[2]): r[3] for r in col_rows},
                row_counts=counts,
            )
        captured = enforce_budget(captured, hints)

        table_comments = fold_table_comments(table_comment_rows)
        column_comments = fold_column_comments(column_comment_rows)

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
                    comment=column_comments.get(ident),
                    **captured.get(ident, ColumnHints()).as_kwargs(),
                )
            )

        tables = [
            TableInfo(
                schema=owner, name=name, columns=cols,
                approx_row_count=counts.get((owner, name)),
                comment=table_comments.get((owner, name)),
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
