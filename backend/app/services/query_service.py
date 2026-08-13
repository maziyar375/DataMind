"""Executing stored SQL — the second entry point into the guarded path.

Until now the only route from SQL to a driver ran through the pipeline: a model
wrote the statement, `validate` guarded it, `execute` ran it. A dashboard tile
needs the other half of that — *"run this stored statement against this
connection, no model involved"* — and a second entry point is a second chance
to bypass the guard.

It does not get one. Stored SQL is re-validated on **every** execution against
the connection's **current** snapshot; nothing here reads how the SQL was
authored. `dashboard_tiles.sql` is user-typed text by design — the tile editor
hands the user a textarea — so it is hostile input, not model output that
happens to be user-visible, and `sql_origin` grants nothing.

This module is also the home of the helpers `run_service` had kept private: the
guard policy builder, the snapshot loader, the LLM resolver and the connector
binder. Two copies of the policy builder is how one of them silently stops
matching the guard, so they were lifted here rather than copied; `run_service`
and `semantic_service` import them from this module.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AppError, ConnectorError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector, ResultColumn
from app.domain.ports.llm import ProviderCapabilities, ResolvedLLM
from app.domain.ports.secrets import SecretBox
from app.domain.value_objects import DatabaseKind
from app.infra.connectors.factory import build_connector
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import DatabaseConnection, LlmConfig, SchemaSnapshotRow
from app.sqlguard import GuardPolicy, guard

log = get_logger(__name__)

# Twelve tiles on one database must not open twelve connections, and must not
# run twelve statements at once either — the cap is on the customer's database,
# not on ours.
MAX_CONCURRENT_TILES = 4

# Guard rejections that, for SQL which was valid when it was saved, mean the
# schema moved underneath the tile rather than that the SQL was ever wrong.
# Reported apart from an outright rejection because the fix is different: one
# is "re-sync the connection", the other is "the query is not allowed".
_DRIFT_RULES = frozenset({"E_TABLE_NOT_ALLOWED", "E_UNKNOWN_COLUMN"})

_NO_SNAPSHOT = "This connection has no schema snapshot. Sync it, then try again."


# ── lifted from run_service ──────────────────────────────────────────────
async def latest_snapshot(db: AsyncSession, connection_id: UUID) -> dict[str, Any]:
    """The connection's current schema snapshot, or an empty one.

    Empty is not an error here — it is the input that makes the guard reject
    everything, which is exactly right for an unsynced connection.
    """
    result = await db.execute(
        select(SchemaSnapshotRow)
        .where(SchemaSnapshotRow.connection_id == connection_id)
        .order_by(SchemaSnapshotRow.version.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {
            "tables": [], "relationships": [],
            "dialect": "postgres", "catalog_meta": {},
        }
    return {
        "tables": row.tables,
        "relationships": row.relationships,
        "dialect": row.dialect,
        # Database and schema descriptions, for the schema block. A pre-0012
        # row reads back as `{}`, which is the same absence a database with no
        # comments produces — nothing downstream tells the two apart.
        "catalog_meta": row.catalog_meta or {},
    }


def policy_from_snapshot(
    snapshot: dict[str, Any],
    connection: DatabaseConnection,
    *,
    max_rows: int | None = None,
) -> GuardPolicy:
    """Build the guard's allowlist from what the connection currently has.

    `max_rows` overrides the connection's row cap for this one statement. It is
    only ever passed a *lower* number (see `effective_max_rows`): the cap the
    rewriter applies has to be the cap the executor applies, or the LIMIT in the
    rewritten SQL and the row budget stop agreeing.
    """
    allowed_tables: set[str] = set()
    allowed_columns: dict[str, set[str]] = {}
    for table in snapshot.get("tables", []):
        qualified = f"{table['schema']}.{table['name']}".lower()
        allowed_tables.add(qualified)
        allowed_columns[qualified] = {c["name"].lower() for c in table.get("columns", [])}
    return GuardPolicy(
        # sqlglot names the SQL Server dialect `tsql`, not `mssql`, so the
        # connection's own kind cannot be handed over unmapped.
        dialect=DatabaseKind(connection.database_type).sqlglot_dialect,
        max_rows=connection.max_rows if max_rows is None else max_rows,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
    )


def resolve_llm(config: LlmConfig, box: SecretBox, *, min_max_tokens: int = 0) -> ResolvedLLM:
    """Decrypt a provider's key and hand back a callable model description.

    `min_max_tokens` raises the floor on the output budget for callers whose
    output is prose rather than SQL (the semantic-layer generator); it defaults
    to no floor, which is the run pipeline's behaviour unchanged.
    """
    api_key = ""
    if config.encrypted_api_key:
        api_key = box.decrypt(config.encrypted_api_key, aad=f"llm_config:{config.id}")
    caps = config.capabilities or {}
    return ResolvedLLM(
        config_id=config.id,
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        api_key=api_key,
        temperature=config.temperature,
        max_tokens=max(config.max_tokens, min_max_tokens),
        capabilities=ProviderCapabilities(
            supports_structured_output=caps.get("supports_structured_output", False),
            supports_streaming=caps.get("supports_streaming", True),
        ),
    )


def bind_connector(connection: DatabaseConnection, box: SecretBox) -> DatabaseConnector:
    """Decrypt the stored password and build the engine's connector.

    The AAD is the row identity, so a ciphertext copied between connection rows
    fails to decrypt rather than quietly working.
    """
    return build_connector(
        kind=connection.database_type,
        host=connection.host,
        port=connection.port,
        database=connection.database_name,
        username=connection.username,
        password=box.decrypt(connection.encrypted_password, aad=f"connection:{connection.id}"),
        ssl_mode=connection.ssl_mode,
    )


def effective_max_rows(connection: DatabaseConnection, override: int | None) -> int:
    """Containment belongs to the connection; a tile may only tighten it."""
    if override is None or override <= 0:
        return connection.max_rows
    return min(override, connection.max_rows)


# ── the result of running one tile ───────────────────────────────────────
@dataclass(slots=True)
class TileResult:
    """One tile's outcome. A failure is a value here, never an exception.

    Twelve tiles refresh together; one broken tile must show its own error
    without taking the other eleven — and the dashboard response — with it.
    """

    status: Literal["OK", "ERROR"] = "OK"
    columns: list[ResultColumn] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    computed_at: datetime = field(default_factory=utcnow)
    vega_spec: dict[str, Any] | None = None
    # `plan_chart`'s own word for who decided: model | model_adjusted |
    # heuristic | none. "model" means the tile's *stored* intent, which may
    # equally be a choice the user made in the editor — the plan does not know
    # the difference and must not, since both get the same shape check.
    chart_source: str = "none"
    chart_note: str | None = None
    # A serialised `KpiSpec` for a METRIC tile. Computed here rather than in the
    # browser so the tile and a chat turn showing the same number agree on
    # which column it is, how it is written, and what it is compared against.
    kpi: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """The wire shape, in one place.

        The API maps this to a DTO and the cache stores it; both need the same
        bytes, and `ResultColumn` is a slots dataclass with no `__dict__`, so
        `asdict` is what actually serialises the columns.
        """
        return {
            "status": self.status,
            "columns": [asdict(c) for c in self.columns],
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "computed_at": self.computed_at,
            "vega_spec": self.vega_spec,
            "chart_source": self.chart_source,
            "chart_note": self.chart_note,
            "kpi": self.kpi,
            "error": (
                None
                if self.error_code is None
                else {"code": self.error_code, "message": self.error_message or ""}
            ),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TileResult:
        """Rebuild a result the cache wrote. The inverse of `to_payload`.

        A served-from-cache tile has to be indistinguishable from a freshly
        computed one — including `computed_at`, which is the whole point of
        serving it: the reader is told how old the number is.
        """
        error = payload.get("error") or {}
        computed_at = payload.get("computed_at")
        return cls(
            status=payload.get("status", "OK"),
            columns=[ResultColumn(**c) for c in payload.get("columns", [])],
            rows=[list(r) for r in payload.get("rows", [])],
            row_count=payload.get("row_count", 0),
            truncated=payload.get("truncated", False),
            duration_ms=payload.get("duration_ms", 0),
            computed_at=(
                datetime.fromisoformat(computed_at)
                if isinstance(computed_at, str)
                else (computed_at or utcnow())
            ),
            vega_spec=payload.get("vega_spec"),
            chart_source=payload.get("chart_source", "none"),
            chart_note=payload.get("chart_note"),
            # `.get`, so a payload the cache wrote before big numbers existed
            # rebuilds as a tile without one rather than raising.
            kpi=payload.get("kpi"),
            error_code=error.get("code"),
            error_message=error.get("message"),
        )


def _failed(code: str, message: str, *, duration_ms: int = 0) -> TileResult:
    return TileResult(
        status="ERROR",
        duration_ms=duration_ms,
        error_code=code,
        error_message=message,
    )


# ── the one entry point ──────────────────────────────────────────────────
async def execute_saved_sql(
    db: AsyncSession,
    settings: Settings,
    *,
    sql: str,
    connection: DatabaseConnection,
    owner_id: UUID,
    chart_intent: Any | None = None,
    want_kpi: bool = False,
    max_rows: int | None = None,
    connector: DatabaseConnector | None = None,
    snapshot: dict[str, Any] | None = None,
) -> TileResult:
    """Run stored SQL against a connection and return everything a tile needs.

    `connector` and `snapshot` exist for the batch path: refreshing a dashboard
    loads each connection's snapshot once and opens one connector per
    connection, then runs its tiles through them. When either is omitted this
    function does that work itself — and, when it opened the connector, closes
    it. A caller that passes one owns it.

    `chart_intent` is a `ChartIntent | None`; it is typed loosely because
    `app.charts` is imported lazily, as the pipeline's chart node does, to keep
    the chart machinery off the import path of everything that touches SQL.

    `want_kpi` is asked for rather than inferred: only a METRIC tile draws a
    big number, and profiling a five-thousand-row TABLE tile to build one
    nobody will look at is work with no reader.
    """
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    # Re-checked at execution, not only at tile save: the connection may have
    # been deleted and its id reused, or the tile row edited underneath us.
    if connection.owner_id != owner_id:
        log.warning(
            "tile_connection_not_owned",
            connection_id=str(connection.id),
            owner_id=str(owner_id),
        )
        return _failed(
            "E_FORBIDDEN",
            "This query's database connection is not available to you.",
            duration_ms=elapsed(),
        )

    if not sql or not sql.strip():
        return _failed("E_SQL_REJECTED", "There is no SQL to run.", duration_ms=elapsed())

    owns_connector = connector is None
    try:
        if snapshot is None:
            snapshot = await latest_snapshot(db, connection.id)
        if not snapshot.get("tables"):
            # Fail closed and say why. The guard would reject every statement
            # here anyway, but "table not allowed" is the wrong sentence to
            # show someone whose connection has simply never been synced.
            return _failed("E_NO_SNAPSHOT", _NO_SNAPSHOT, duration_ms=elapsed())

        row_cap = effective_max_rows(connection, max_rows)
        report, executable = guard(
            sql, policy_from_snapshot(snapshot, connection, max_rows=row_cap)
        )
        if report.status != "VALID" or executable is None:
            return _guard_failure(report, duration_ms=elapsed())

        if owns_connector:
            connector = bind_connector(connection, secret_box(settings))
        assert connector is not None

        try:
            result = await connector.execute(
                executable,
                max_rows=row_cap,
                statement_timeout_ms=connection.statement_timeout_ms,
            )
        except ConnectorError as err:
            return _failed("E_QUERY_FAILED", err.message, duration_ms=elapsed())

        spec, source, note = _chart(
            chart_intent, result.columns, result.rows, truncated=result.truncated
        )
        return TileResult(
            status="OK",
            columns=list(result.columns),
            rows=[list(row) for row in result.rows],
            row_count=result.row_count,
            truncated=result.truncated,
            duration_ms=elapsed(),
            computed_at=utcnow(),
            vega_spec=spec,
            chart_source=source,
            chart_note=note,
            kpi=_kpi(result.columns, result.rows) if want_kpi else None,
        )
    except AppError as err:
        return _failed(err.code, err.message, duration_ms=elapsed())
    except Exception as err:  # noqa: BLE001 — one tile may never fail a dashboard
        log.exception("tile_execution_crashed", connection_id=str(connection.id))
        return _failed("E_INTERNAL", str(err)[:500], duration_ms=elapsed())
    finally:
        # Mirrors the run path: the connector is closed on every exit, and only
        # by whoever opened it.
        if owns_connector and connector is not None:
            await connector.close()


def secret_box(settings: Settings) -> AesGcmSecretBox:
    """The one place a `SecretBox` is built from settings outside a service's
    `__init__`. Callers that already hold one (`run_service`) pass theirs."""
    return AesGcmSecretBox(
        settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
    )


def _guard_failure(report: Any, *, duration_ms: int) -> TileResult:
    """Turn a rejection into a tile error, telling drift apart from bad SQL."""
    errors = report.errors
    if not errors:
        return _failed(
            "E_SQL_REJECTED",
            "The guard rejected this SQL.",
            duration_ms=duration_ms,
        )

    first = errors[0]
    if first.rule_id in _DRIFT_RULES:
        return _failed(
            "E_SCHEMA_CHANGED",
            f"{first.message} The database schema has changed since this query "
            f"was saved — re-sync the connection, then check it again.",
            duration_ms=duration_ms,
        )
    return _failed(
        first.rule_id,
        first.message + (f" {first.hint}" if first.hint else ""),
        duration_ms=duration_ms,
    )


# ── the chart ────────────────────────────────────────────────────────────
def _label(chart_type: str) -> str:
    return chart_type.replace("_", " ")


def _chart(
    intent: Any | None,
    columns: list[ResultColumn],
    rows: list[list[Any]],
    *,
    truncated: bool,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Decide the chart here, not in the browser.

    The stored intent goes in as a *suggestion*, so the user's explicit pick
    gets the same name-check and shape-repair a model's suggestion gets. An
    intent the data cannot support degrades to the table plus a note — never an
    error: the numbers are correct whatever picture was asked for.
    """
    from app.charts import compile_vega_lite, plan_chart, profile_result

    if not rows or len(columns) < 2:
        return None, "none", None

    try:
        profile = profile_result(columns, rows, truncated=truncated)
        plan = plan_chart(profile, intent)
        if plan.intent is None:
            return None, "none", plan.reason
        spec = compile_vega_lite(plan.intent, profile, columns, rows)
    except Exception:  # noqa: BLE001 — a chart is never worth the result
        log.exception("tile_chart_failed")
        return None, "none", "The chart could not be built for this result."

    return spec, plan.source, _chart_note(intent, plan)


def _kpi(
    columns: list[ResultColumn], rows: list[list[Any]]
) -> dict[str, Any] | None:
    """The big number for a METRIC tile — the same planner a chat turn uses.

    Which column is the value, how it is written, and whether the extra rows
    are context or clutter were decided in the browser until now, by a
    component that knew nothing about the one answering the same question in
    chat. One planner is what keeps the two from drifting.
    """
    from app.charts import plan_kpi, profile_result

    if not rows:
        return None
    try:
        spec = plan_kpi(profile_result(columns, rows), columns, rows)
    except Exception:  # noqa: BLE001 — a tile's numbers outrank its presentation
        log.exception("tile_kpi_failed")
        return None
    return spec.model_dump(mode="json") if spec is not None else None


def _chart_note(intent: Any | None, plan: Any) -> str | None:
    if intent is None or plan.intent is None:
        return None
    if plan.intent.chart_type != intent.chart_type:
        return (
            f"A {_label(intent.chart_type)} chart does not fit this result; "
            f"showing a {_label(plan.intent.chart_type)} chart."
        )
    if plan.source == "model_adjusted":
        return "The chart was adjusted to fit this result's columns."
    return None


# ── the batch path ───────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class TileRequest:
    """One tile to run: what `dashboard_service` maps a `dashboard_tiles` row to.

    It carries the connection *object* rather than an id so the batch never
    re-reads a row per tile, and so ownership is checked against the same
    object the connector is built from.
    """

    tile_id: UUID
    sql: str
    connection: DatabaseConnection
    chart_intent: Any | None = None
    want_kpi: bool = False
    max_rows: int | None = None


async def execute_many(
    db: AsyncSession,
    settings: Settings,
    *,
    requests: list[TileRequest],
    owner_id: UUID,
) -> dict[UUID, TileResult]:
    """Run a set of tiles, one connector per connection.

    Grouping is the point: twelve tiles on one database open one connection,
    not twelve. Tiles run under a semaphore so a wide dashboard cannot turn a
    refresh into a load test of the customer's database.

    Every database read happens *before* the tiles fan out — one snapshot per
    connection, awaited in sequence — because an `AsyncSession` is not safe for
    concurrent use. After that point nothing here touches `db`.
    """
    if not requests:
        return {}

    groups: dict[UUID, list[TileRequest]] = defaultdict(list)
    for request in requests:
        groups[request.connection.id].append(request)

    results: dict[UUID, TileResult] = {}
    runnable: list[tuple[list[TileRequest], dict[str, Any]]] = []

    for connection_id, group in groups.items():
        connection = group[0].connection
        if connection.owner_id != owner_id:
            # Not one connector is opened for a connection the caller does not
            # own — the check happens before anything is decrypted or dialled.
            log.warning(
                "tile_connection_not_owned",
                connection_id=str(connection_id),
                owner_id=str(owner_id),
            )
            for request in group:
                results[request.tile_id] = _failed(
                    "E_FORBIDDEN",
                    "This query's database connection is not available to you.",
                )
            continue
        runnable.append((group, await latest_snapshot(db, connection_id)))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TILES)

    async def run_group(
        group: list[TileRequest], snapshot: dict[str, Any]
    ) -> dict[UUID, TileResult]:
        connection = group[0].connection
        if not snapshot.get("tables"):
            # Nothing this connection holds can pass the guard, so it is not
            # worth dialling the database to find that out twelve times.
            return {r.tile_id: _failed("E_NO_SNAPSHOT", _NO_SNAPSHOT) for r in group}

        connector: DatabaseConnector | None = None
        try:
            connector = bind_connector(connection, secret_box(settings))
        except Exception as err:  # noqa: BLE001
            log.warning(
                "tile_connector_failed",
                connection_id=str(connection.id),
                error=str(err)[:200],
            )
            message = err.message if isinstance(err, AppError) else str(err)[:500]
            code = err.code if isinstance(err, AppError) else "E_CONNECTOR"
            return {r.tile_id: _failed(code, message) for r in group}

        async def run_one(request: TileRequest) -> tuple[UUID, TileResult]:
            async with semaphore:
                return request.tile_id, await execute_saved_sql(
                    db,
                    settings,
                    sql=request.sql,
                    connection=request.connection,
                    owner_id=owner_id,
                    chart_intent=request.chart_intent,
                    want_kpi=request.want_kpi,
                    max_rows=request.max_rows,
                    connector=connector,
                    snapshot=snapshot,
                )

        try:
            return dict(await asyncio.gather(*(run_one(r) for r in group)))
        finally:
            await connector.close()

    for group_results in await asyncio.gather(
        *(run_group(group, snapshot) for group, snapshot in runnable)
    ):
        results.update(group_results)
    return results
