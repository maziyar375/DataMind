"""The per-tile cache, and the clock arithmetic underneath it.

Without a cache, five people with a 30-second tile open is a load generator
pointed at the customer's database. With a careless one, an edited tile keeps
serving the old answer until its interval happens to elapse. The rules are
small enough to be pure functions, so they are tested as pure functions — and
then once more through `refresh`, which is where they actually decide whether a
query runs.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.domain.ports.database import ResultColumn
from app.infra.db.models import (
    Dashboard,
    DashboardTile,
    DashboardTileCache,
    DatabaseConnection,
)
from app.services import dashboard_service
from app.services.dashboard_service import (
    DashboardService,
    effective_refresh_interval,
    is_fresh,
    result_fingerprint,
)
from app.services.query_service import TileResult

OWNER = uuid4()


def _dashboard(default_interval: int = 0) -> Dashboard:
    return Dashboard(
        id=uuid4(),
        owner_id=OWNER,
        name="Ops",
        status="ACTIVE",
        grid_columns=12,
        row_height_px=60,
        gap_px=12,
        compact_mode="VERTICAL",
        palette="default",
        theme_override="INHERIT",
        default_refresh_interval_seconds=default_interval,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _tile(
    dashboard: Dashboard,
    *,
    sql: str = "SELECT status FROM public.orders",
    interval: int | None = None,
    connection_id: UUID | None = None,
    chart_config: dict[str, Any] | None = None,
    max_rows: int | None = None,
) -> DashboardTile:
    return DashboardTile(
        id=uuid4(),
        dashboard_id=dashboard.id,
        connection_id=connection_id or uuid4(),
        title="Orders",
        tile_type="CHART",
        sql=sql,
        sql_origin="HANDWRITTEN",
        chart_config=chart_config,
        max_rows=max_rows,
        refresh_interval_seconds=interval,
        grid_x=0,
        grid_y=0,
        grid_w=4,
        grid_h=4,
        position=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _cached(tile: DashboardTile, *, age_seconds: float = 0.0, rows: int = 3) -> DashboardTileCache:
    computed_at = utcnow() - timedelta(seconds=age_seconds)
    result = TileResult(
        status="OK",
        columns=[ResultColumn(name="status", db_type="text")],
        rows=[["paid"]] * rows,
        row_count=rows,
        computed_at=computed_at,
    )
    payload = result.to_payload()
    payload["computed_at"] = computed_at.isoformat()
    return DashboardTileCache(
        tile_id=tile.id,
        sql_hash=result_fingerprint(tile),
        result=payload,
        row_count=rows,
        computed_at=computed_at,
        duration_ms=5,
    )


# ── which clock a tile is on ─────────────────────────────────────────────
def test_a_tile_without_a_rate_inherits_the_dashboards() -> None:
    dashboard = _dashboard(default_interval=300)

    assert effective_refresh_interval(_tile(dashboard, interval=None), dashboard) == 300


def test_zero_is_manual_and_is_not_the_same_as_inheriting() -> None:
    """The distinction this column exists for: `NULL` asks the dashboard, `0`
    says "never, unless I press refresh"."""
    dashboard = _dashboard(default_interval=300)

    assert effective_refresh_interval(_tile(dashboard, interval=0), dashboard) == 0
    assert effective_refresh_interval(_tile(dashboard, interval=None), dashboard) == 300


def test_a_tile_rate_overrides_the_dashboards_in_both_directions() -> None:
    slow, fast = _dashboard(default_interval=3600), _dashboard(default_interval=15)

    assert effective_refresh_interval(_tile(slow, interval=30), slow) == 30
    assert effective_refresh_interval(_tile(fast, interval=3600), fast) == 3600


# ── what invalidates a cached result ─────────────────────────────────────
def test_a_result_inside_its_interval_is_served() -> None:
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=60)

    assert is_fresh(
        _cached(tile, age_seconds=10),
        interval_seconds=60,
        fingerprint=result_fingerprint(tile),
    )


def test_a_result_past_its_interval_is_not() -> None:
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=60)

    assert not is_fresh(
        _cached(tile, age_seconds=61),
        interval_seconds=60,
        fingerprint=result_fingerprint(tile),
    )


def test_a_changed_statement_invalidates_regardless_of_the_ttl() -> None:
    """An hour-long interval must not keep serving yesterday's question."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=3600)
    row = _cached(tile, age_seconds=1)

    tile.sql = "SELECT status, total_amount FROM public.orders"

    assert not is_fresh(
        row, interval_seconds=3600, fingerprint=result_fingerprint(tile)
    )


def test_a_changed_chart_or_row_cap_invalidates_too() -> None:
    """`sql_hash` is named for the case that matters, but the intent and the
    cap shape the payload as well — a pie switched to a line may not keep
    serving the pie."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=3600)
    row = _cached(tile, age_seconds=1)

    tile.chart_config = {"chart_type": "line"}
    assert not is_fresh(row, interval_seconds=3600, fingerprint=result_fingerprint(tile))

    tile.chart_config = None
    assert is_fresh(row, interval_seconds=3600, fingerprint=result_fingerprint(tile))

    tile.max_rows = 10
    assert not is_fresh(row, interval_seconds=3600, fingerprint=result_fingerprint(tile))


def test_a_table_setting_does_not_re_run_the_query() -> None:
    """The other half of the rule above, and the one that is easy to get wrong
    by symmetry: `table_config` changes how the *browser* draws rows it already
    has. Renaming a column header must not invalidate the cache and send a
    query to the customer's database."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=3600)
    row = _cached(tile, age_seconds=1)

    tile.table_config = {
        "columns": [{"name": "status", "label": "Order status", "hidden": False}],
        "sort_column": "total",
        "sort_direction": "desc",
    }

    assert is_fresh(row, interval_seconds=3600, fingerprint=result_fingerprint(tile))


def test_a_manual_tile_serves_its_cache_until_someone_presses_refresh() -> None:
    """`force` never reaches `is_fresh`; a manual tile has no expiry of its own."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=0)

    assert is_fresh(
        _cached(tile, age_seconds=86_400),
        interval_seconds=0,
        fingerprint=result_fingerprint(tile),
    )


def test_nothing_cached_is_never_fresh() -> None:
    dashboard = _dashboard()
    assert not is_fresh(
        None, interval_seconds=60, fingerprint=result_fingerprint(_tile(dashboard))
    )


def test_a_cached_result_survives_the_round_trip_intact() -> None:
    """A served-from-cache tile must be indistinguishable from a computed one —
    especially `computed_at`, which is how the reader knows how old it is."""
    dashboard = _dashboard()
    tile = _tile(dashboard)
    row = _cached(tile, age_seconds=42)

    restored = TileResult.from_payload(row.result)

    assert restored.status == "OK"
    assert restored.row_count == 3
    assert restored.columns[0].name == "status"
    assert restored.columns[0].db_type == "text"
    assert abs((restored.computed_at - row.computed_at).total_seconds()) < 0.001


# ── the same rules, through `refresh` ────────────────────────────────────
class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(self._rows)


class FakeDb:
    """Answers the four selects `refresh` makes, by the table they name."""

    def __init__(
        self,
        *,
        dashboard: Dashboard,
        tiles: list[DashboardTile],
        cache: list[DashboardTileCache],
        connections: list[DatabaseConnection],
    ) -> None:
        self.dashboard = dashboard
        self.tiles = tiles
        self.cache = cache
        self.connections = connections
        self.added: list[Any] = []
        self.flushes = 0

    async def execute(self, statement: Any) -> FakeResult:
        sql = str(statement).lower()
        # Checked longest-name-first: "dashboard_tile_cache" contains
        # "dashboard_tile", which contains "dashboards".
        if "dashboard_tile_cache" in sql:
            return FakeResult(list(self.cache))
        if "dashboard_tiles" in sql:
            return FakeResult(list(self.tiles))
        if "database_connections" in sql:
            return FakeResult(list(self.connections))
        return FakeResult([self.dashboard])

    def add(self, entity: Any) -> None:
        self.added.append(entity)
        if isinstance(entity, DashboardTileCache):
            self.cache.append(entity)

    async def flush(self) -> None:
        self.flushes += 1


def _connection(connection_id: UUID) -> DatabaseConnection:
    return DatabaseConnection(
        id=connection_id,
        owner_id=OWNER,
        name="sales",
        database_type="postgres",
        host="db",
        port=5432,
        database_name="sales",
        username="readonly",
        encrypted_password="enc",
        max_rows=1000,
        statement_timeout_ms=30_000,
    )


def _executor(monkeypatch: pytest.MonkeyPatch, result: TileResult | None = None) -> list[Any]:
    """Replace `execute_many`, recording which tiles were actually run."""
    ran: list[Any] = []

    async def fake(_db: Any, _settings: Any, *, requests: list[Any], owner_id: UUID) -> dict:
        ran.extend(requests)
        return {
            request.tile_id: (
                result
                if result is not None
                else TileResult(status="OK", row_count=1, rows=[[1]])
            )
            for request in requests
        }

    monkeypatch.setattr(dashboard_service, "execute_many", fake)
    return ran


async def test_a_fresh_tile_is_served_from_cache_without_touching_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=300)
    db = FakeDb(
        dashboard=dashboard,
        tiles=[tile],
        cache=[_cached(tile, age_seconds=5)],
        connections=[_connection(tile.connection_id)],
    )
    ran = _executor(monkeypatch)

    results = await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    assert ran == []
    assert results[tile.id].row_count == 3


async def test_force_runs_the_query_however_fresh_the_cache_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The kebab's "Refresh now" is the one thing the cache may not override."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=300)
    db = FakeDb(
        dashboard=dashboard,
        tiles=[tile],
        cache=[_cached(tile, age_seconds=1)],
        connections=[_connection(tile.connection_id)],
    )
    ran = _executor(monkeypatch)

    await DashboardService(db, object()).refresh(dashboard.id, OWNER, force=True)

    assert [r.tile_id for r in ran] == [tile.id]


async def test_two_tiles_with_different_rates_expire_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 30-second tile and an hourly one on the same dashboard, both cached 60
    seconds ago: exactly one of them is due."""
    dashboard = _dashboard()
    connection_id = uuid.uuid4()
    quick = _tile(dashboard, interval=30, connection_id=connection_id)
    slow = _tile(dashboard, interval=3600, connection_id=connection_id)
    db = FakeDb(
        dashboard=dashboard,
        tiles=[quick, slow],
        cache=[_cached(quick, age_seconds=60), _cached(slow, age_seconds=60)],
        connections=[_connection(connection_id)],
    )
    ran = _executor(monkeypatch)

    results = await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    assert [r.tile_id for r in ran] == [quick.id]
    assert set(results) == {quick.id, slow.id}


async def test_a_tile_asked_for_by_id_is_the_only_one_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With per-tile rates this is the normal call, not an optimisation."""
    dashboard = _dashboard()
    connection_id = uuid.uuid4()
    first = _tile(dashboard, interval=30, connection_id=connection_id)
    second = _tile(dashboard, interval=30, connection_id=connection_id)
    db = FakeDb(
        dashboard=dashboard,
        tiles=[first, second],
        cache=[],
        connections=[_connection(connection_id)],
    )
    ran = _executor(monkeypatch)

    results = await DashboardService(db, object()).refresh(
        dashboard.id, OWNER, tile_ids=[second.id]
    )

    assert [r.tile_id for r in ran] == [second.id]
    assert set(results) == {second.id}


async def test_a_computed_result_is_written_to_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=30)
    db = FakeDb(
        dashboard=dashboard,
        tiles=[tile],
        cache=[],
        connections=[_connection(tile.connection_id)],
    )
    _executor(monkeypatch)

    await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    written = [row for row in db.added if isinstance(row, DashboardTileCache)]
    assert len(written) == 1
    assert written[0].tile_id == tile.id
    assert written[0].sql_hash == result_fingerprint(tile)
    assert written[0].result["status"] == "OK"


async def test_a_failure_is_cached_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise a tile with a broken query re-runs it on every tick of every
    open browser — the worst thing a dashboard can do to a database."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=30)
    db = FakeDb(
        dashboard=dashboard,
        tiles=[tile],
        cache=[],
        connections=[_connection(tile.connection_id)],
    )
    _executor(
        monkeypatch,
        TileResult(
            status="ERROR", error_code="E_QUERY_FAILED", error_message="boom"
        ),
    )

    await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    written = [row for row in db.added if isinstance(row, DashboardTileCache)][0]
    assert written.error_code == "E_QUERY_FAILED"
    assert written.result["error"] == {"code": "E_QUERY_FAILED", "message": "boom"}


async def test_a_tile_whose_connection_was_deleted_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SET NULL, not CASCADE (§4): the tile outlives the connection, and the
    refresh has to explain itself rather than run anything."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=30)
    tile.connection_id = None
    db = FakeDb(dashboard=dashboard, tiles=[tile], cache=[], connections=[])
    ran = _executor(monkeypatch)

    results = await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    assert ran == []
    assert results[tile.id].status == "ERROR"
    assert results[tile.id].error_code == "E_CONNECTION_REMOVED"


async def test_a_text_tile_computes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=30)
    tile.tile_type = "TEXT"
    tile.sql = ""
    db = FakeDb(dashboard=dashboard, tiles=[tile], cache=[], connections=[])
    ran = _executor(monkeypatch)

    results = await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    assert ran == []
    assert results == {}


async def test_a_stored_chart_intent_reaches_the_executor_as_a_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = _dashboard()
    tile = _tile(
        dashboard,
        interval=30,
        chart_config={
            "chart_type": "pie",
            "x_axis": {"field": "status", "type": "nominal"},
            "y_axis": {"field": "total", "type": "quantitative"},
        },
    )
    db = FakeDb(
        dashboard=dashboard,
        tiles=[tile],
        cache=[],
        connections=[_connection(tile.connection_id)],
    )
    ran = _executor(monkeypatch)

    await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    assert ran[0].chart_intent is not None
    assert ran[0].chart_intent.chart_type == "pie"


async def test_an_unreadable_stored_intent_falls_back_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The numbers are correct whatever is wrong with the picture."""
    dashboard = _dashboard()
    tile = _tile(dashboard, interval=30, chart_config={"chart_type": "sunburst"})
    db = FakeDb(
        dashboard=dashboard,
        tiles=[tile],
        cache=[],
        connections=[_connection(tile.connection_id)],
    )
    ran = _executor(monkeypatch)

    await DashboardService(db, object()).refresh(dashboard.id, OWNER)

    assert ran[0].chart_intent is None
