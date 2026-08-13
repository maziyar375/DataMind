"""MySQL connector.

The same three defences as the PostgreSQL connector, expressed in MySQL's
own terms:

1. `probe()` attempts a write inside a transaction it always rolls back, and
   reports `readonly_confirmed` honestly either way.
2. Every statement runs in a `START TRANSACTION READ ONLY`, so even a guard
   bypass cannot mutate.
3. `max_execution_time` bounds a pathological SELECT, in milliseconds. It
   applies to read-only SELECTs, which is exactly the population the guard
   lets through. One documented exception is worth knowing when reading a
   test: MySQL does not interrupt `SLEEP()`, so a sleeping statement is not
   a valid check that the budget works — a genuinely expensive read is.

Introspection reads `information_schema`, which in MySQL is filtered by
privilege rather than by ownership: a role with SELECT on a table sees that
table's constraints. That is the opposite of PostgreSQL, where the same views
are owner-filtered and forced the pg_catalog route.
"""
from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import aiomysql

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
    PROBE_TIMEOUT_MS,
    ColumnHints,
    apply_probe,
    clean_values,
    enforce_budget,
    parse_enum_members,
    plan_probes,
)

# ER_QUERY_TIMEOUT: the statement outlived max_execution_time.
_ER_QUERY_TIMEOUT = 3024

_TABLE_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
       c.is_nullable, c.ordinal_position, c.column_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE t.table_type = 'BASE TABLE'
  AND c.table_schema IN ({placeholders})
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

# MySQL has no `pg_stats` equivalent, but it has two cheaper sources.
#
# `information_schema.STATISTICS.CARDINALITY` is the number of distinct values
# an index sees, which for the first column of an index is the column's own
# distinct count. Only indexed columns have it, which is exactly the subset
# worth knowing about anyway.
_INDEX_CARDINALITY_SQL = """
SELECT table_schema, table_name, column_name, cardinality
FROM information_schema.statistics
WHERE table_schema IN ({placeholders})
  AND seq_in_index = 1
  AND cardinality IS NOT NULL
"""

# MySQL 8.0 histograms, present only where someone ran
# `ANALYZE TABLE … UPDATE HISTOGRAM ON …`. A "singleton" histogram stores one
# bucket per distinct value, so its buckets are the complete domain; an
# "equi-height" one is a summary and is ignored here.
_HISTOGRAM_SQL = """
SELECT schema_name, table_name, column_name, histogram
FROM information_schema.column_statistics
WHERE schema_name IN ({placeholders})
"""

_TEXT_TYPES = frozenset({"char", "varchar", "text", "tinytext", "mediumtext",
                         "longtext", "enum", "set"})


def _singleton_histogram(raw: Any) -> tuple[list[str], float | None]:
    """Values and null fraction from a MySQL 8 histogram, if it is a singleton.

    An equi-height histogram summarises ranges rather than enumerating values,
    so it cannot produce a complete domain and is dropped.
    """
    if raw is None:
        return [], None
    try:
        doc = json.loads(raw) if isinstance(raw, str | bytes) else raw
    except (ValueError, TypeError):
        return [], None
    if not isinstance(doc, dict):
        return [], None

    null_fraction = doc.get("null-values")
    fraction = (
        round(float(null_fraction), 4)
        if isinstance(null_fraction, int | float)
        else None
    )
    if doc.get("histogram-type") != "singleton":
        return [], fraction

    buckets = doc.get("buckets")
    if not isinstance(buckets, list):
        return [], fraction
    # Each bucket is [value, cumulative_frequency].
    values = [b[0] for b in buckets if isinstance(b, list) and b]
    return clean_values(values), fraction


def _build_hints(
    *,
    col_rows: list[Any],
    cardinality_rows: list[Any],
    histogram_rows: list[Any],
) -> dict[tuple[str, str, str], ColumnHints]:
    """Combine the three MySQL sources, most authoritative first.

    A declared `enum`/`set` beats every statistic: the type *is* the domain,
    so it is complete by construction and free to read.
    """
    hints: dict[tuple[str, str, str], ColumnHints] = {}

    for schema, table, column, cardinality in cardinality_rows:
        if cardinality is None:
            continue
        hints[(schema, table, column)] = ColumnHints(
            distinct_count=int(cardinality) or None
        )

    for schema, table, column, raw in histogram_rows:
        values, null_fraction = _singleton_histogram(raw)
        ident = (schema, table, column)
        current = hints.get(ident, ColumnHints())
        hints[ident] = replace(
            current,
            null_fraction=(
                current.null_fraction if current.null_fraction is not None
                else null_fraction
            ),
            sample_values=(
                values
                if values and not is_sensitive_column(column)
                else current.sample_values
            ),
            distinct_count=(
                current.distinct_count
                if current.distinct_count is not None
                else (len(values) or None)
            ),
        )

    for schema, table, column, _type, _nullable, _pos, column_type in col_rows:
        members = parse_enum_members(column_type)
        if not members:
            continue
        ident = (schema, table, column)
        current = hints.get(ident, ColumnHints())
        hints[ident] = replace(
            current,
            distinct_count=len(members),
            sample_values=[] if is_sensitive_column(column) else members,
        )

    return hints


_PK_SQL = """
SELECT k.table_schema, k.table_name, k.column_name
FROM information_schema.key_column_usage k
JOIN information_schema.table_constraints t
  ON t.constraint_name = k.constraint_name
 AND t.table_schema = k.table_schema
 AND t.table_name = k.table_name
WHERE t.constraint_type = 'PRIMARY KEY'
  AND k.table_schema IN ({placeholders})
ORDER BY k.table_schema, k.table_name, k.ordinal_position
"""

_FK_SQL = """
SELECT k.table_schema AS from_schema, k.table_name AS from_table,
       k.column_name AS from_column,
       k.referenced_table_schema AS to_schema,
       k.referenced_table_name AS to_table,
       k.referenced_column_name AS to_column
FROM information_schema.key_column_usage k
WHERE k.referenced_table_name IS NOT NULL
  AND k.table_schema IN ({placeholders})
ORDER BY k.table_schema, k.table_name, k.ordinal_position
"""

_ROWCOUNT_SQL = """
SELECT table_schema AS `schema`, table_name AS name, table_rows AS approx
FROM information_schema.tables
WHERE table_type = 'BASE TABLE' AND table_schema IN ({placeholders})
"""

# Catalog comments. `information_schema` is the right source on MySQL for the
# reason in the module docstring — it is privilege-filtered, not
# ownership-filtered — and `TABLE_COMMENT` reads `VIEW` for a view, which the
# `table_type` filter removes along with the view itself.
_TABLE_COMMENT_SQL = """
SELECT t.table_schema, t.table_name, t.table_comment
FROM information_schema.tables t
WHERE t.table_schema IN ({placeholders})
  AND t.table_type = 'BASE TABLE'
  AND t.table_comment <> ''
"""

# The join to `tables` is not decoration: `information_schema.columns` carries a
# **view's** columns too, and a view inherits the column comments of the table
# under it. Without the filter, every commented column came back once per view
# over it (verified on 8.0.46) and the fold would have attached comments to
# relations the snapshot does not contain.
_COLUMN_COMMENT_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.column_comment
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema IN ({placeholders})
  AND t.table_type = 'BASE TABLE'
  AND c.column_comment <> ''
"""


class MySqlConnector:
    dialect = "mysql"

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
        # aiomysql wants an SSLContext or None; "disable" is the only value
        # that means "no TLS".
        self._ssl = None
        if ssl_mode not in (None, "disable"):
            import ssl as ssl_module

            context = ssl_module.create_default_context()
            if ssl_mode != "verify-full":
                context.check_hostname = False
                context.verify_mode = ssl_module.CERT_NONE
            self._ssl = context
        self._connect_timeout = connect_timeout
        self._pool: aiomysql.Pool | None = None

    # ── lifecycle ────────────────────────────────────────────────────────
    async def _acquire(self) -> aiomysql.Pool:
        if self._pool is None:
            try:
                self._pool = await aiomysql.create_pool(
                    host=self._host, port=self._port, db=self._database,
                    user=self._username, password=self._password,
                    ssl=self._ssl, minsize=1, maxsize=4,
                    connect_timeout=self._connect_timeout,
                    autocommit=True,
                )
            except Exception as err:
                raise ConnectorError(f"Could not connect: {_clean(err)}") from err
        assert self._pool is not None
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    # ── probe ────────────────────────────────────────────────────────────
    async def probe(self) -> ConnectionProbe:
        started = time.perf_counter()
        try:
            pool = await self._acquire()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT VERSION()")
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
            server_version=f"MySQL {version}" if version else None,
            readonly_confirmed=readonly,
            message="Connected" + (" · read-only role confirmed" if readonly else ""),
        )

    async def _verify_readonly(self, conn: Any) -> bool:
        """True only if the role genuinely cannot write.

        A temporary table is the cheapest write MySQL offers that needs a
        real privilege, and it is dropped again on the way out.
        """
        try:
            async with conn.cursor() as cur:
                await cur.execute("CREATE TEMPORARY TABLE _raymand_probe (x INT)")
                await cur.execute("DROP TEMPORARY TABLE _raymand_probe")
        except Exception:
            return True
        return False

    async def _probe_values(
        self,
        hints: dict[tuple[str, str, str], ColumnHints],
        *,
        columns: Mapping[tuple[str, str, str], str],
        row_counts: Mapping[tuple[str, str], int | None],
    ) -> None:
        """Fill value lists the catalog could not, one bounded query each."""
        targets = plan_probes(
            columns=columns, known=hints, row_counts=row_counts,
            text_types=_TEXT_TYPES,
        )
        if not targets:
            return

        pool = await self._acquire()
        async with pool.acquire() as conn, conn.cursor() as cur:
            with contextlib.suppress(Exception):
                await cur.execute(
                    f"SET SESSION max_execution_time = {PROBE_TIMEOUT_MS}"
                )
            cap = HINT_MAX_CARDINALITY + 1
            for target in targets:
                table, column = target.quoted("`")
                # Escaped identifiers, and no user value in the statement.
                sql = f"SELECT DISTINCT {column} FROM {table} LIMIT {cap}"  # noqa: S608
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
        # MySQL has no schema/database distinction, so the connected database
        # is the schema unless the caller narrowed it further. System schemas
        # are dropped first: `mysql`, `sys` and `performance_schema` are the
        # server describing itself.
        schemas = business_schemas(
            self.dialect, schema_allowlist or [self._database]
        )
        marks = ", ".join(["%s"] * len(schemas))

        pool = await self._acquire()
        async with pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute("SELECT VERSION()")
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

            # Hints are an accuracy aid, never a correctness dependency:
            # MariaDB and MySQL 5.7 have no `column_statistics` view at all,
            # and a restricted role may not see `statistics` either.
            cardinality_rows: list[Any] = []
            histogram_rows: list[Any] = []
            if hints.stats:
                with contextlib.suppress(Exception):
                    await cur.execute(
                        _INDEX_CARDINALITY_SQL.format(placeholders=marks), schemas
                    )
                    cardinality_rows = list(await cur.fetchall())
                with contextlib.suppress(Exception):
                    await cur.execute(
                        _HISTOGRAM_SQL.format(placeholders=marks), schemas
                    )
                    histogram_rows = list(await cur.fetchall())

            # Comments are captured whatever the hint budget says: a comment is
            # DDL a human wrote, not something read out of the rows. Suppressed
            # like the reads above — documentation never fails a sync.
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
        fks = {
            (r[0], r[1], r[2]): f"{r[3]}.{r[4]}.{r[5]}"
            for r in fk_rows
        }
        counts = {(r[0], r[1]): int(r[2] or 0) for r in count_rows}

        captured = _build_hints(
            col_rows=list(col_rows),
            cardinality_rows=cardinality_rows,
            histogram_rows=histogram_rows,
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
        for schema, table, column, data_type, nullable, _pos, _column_type in col_rows:
            ident = (schema, table, column)
            grouped.setdefault((schema, table), []).append(
                ColumnInfo(
                    name=column,
                    data_type=data_type,
                    nullable=nullable == "YES",
                    is_primary_key=ident in pks,
                    is_foreign_key=ident in fks,
                    references=fks.get(ident),
                    comment=column_comments.get(ident),
                    **captured.get(ident, ColumnHints()).as_kwargs(),
                )
            )

        tables = [
            TableInfo(
                schema=schema, name=name, columns=cols,
                approx_row_count=counts.get((schema, name)),
                comment=table_comments.get((schema, name)),
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
            server_version=f"MySQL {version}" if version else None,
        )

    # ── execution ────────────────────────────────────────────────────────
    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        pool = await self._acquire()
        started = time.perf_counter()
        try:
            async with pool.acquire() as conn, conn.cursor() as cur:
                # `SET SESSION max_execution_time`, not MariaDB's
                # `SET STATEMENT ... FOR ...`, which MySQL rejects as a
                # syntax error. It bounds read-only SELECTs, which is
                # exactly the population the guard lets through.
                await cur.execute(
                    "SET SESSION max_execution_time = %s",
                    (int(statement_timeout_ms),),
                )
                await cur.execute("START TRANSACTION READ ONLY")
                try:
                    await cur.execute(sql)
                    records = await cur.fetchall()
                    description = cur.description
                finally:
                    await cur.execute("ROLLBACK")
        except Exception as err:
            # 3024 is the only code that means the statement outlived its
            # budget. Matching on message text instead would let an unrelated
            # error mentioning the variable masquerade as a timeout.
            if _error_code(err) == _ER_QUERY_TIMEOUT:
                raise ConnectorError(
                    f"Query exceeded the {statement_timeout_ms}ms statement timeout."
                ) from err
            raise ConnectorError(_clean(err)) from err

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
        """Estimated rows scanned. Powers the metadata chip in the chat UI."""
        pool = await self._acquire()
        try:
            async with pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute(f"EXPLAIN {sql}")
                rows = await cur.fetchall()
                names = [d[0] for d in cur.description]
        except Exception:
            return None

        try:
            index = names.index("rows")
        except ValueError:
            return None
        # One row per accessed table; the product is the plan's row estimate.
        total = 0
        for row in rows:
            value = row[index]
            if value is not None:
                total += int(value)
        return total or None


# ── helpers ──────────────────────────────────────────────────────────────
def _semantic_type(value: Any) -> str:
    if isinstance(value, bool):
        return "nominal"
    if isinstance(value, (int, float, Decimal)):
        return "quantitative"
    if isinstance(value, (datetime, date, timedelta)):
        return "temporal"
    return "nominal"


def _python_to_db_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, float):
        return "double"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (bytes, bytearray)):
        return "blob"
    return "text"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _error_code(err: Exception) -> int | None:
    """MySQL server error number, when the driver carried one."""
    args = getattr(err, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _clean(err: Exception) -> str:
    text = str(err).strip() or err.__class__.__name__
    return text.splitlines()[0][:300]
