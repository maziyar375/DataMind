"""The service's contract with the session.

Two rules here are invisible to every other test in the suite, because a
detached ORM object in a fake happily answers questions a real one cannot:

* **Every write that UPDATEs must `refresh` before the row is serialised.**
  `updated_at` carries an `onupdate`, which an UPDATE does not fetch back, so
  the attribute is expired; reading it while building the response is a lazy
  load in a context that cannot await. That is `MissingGreenlet` — a 500 that
  appears only against a real database. It is in CLAUDE.md's gotchas, and it
  still happened twice while this phase was built.
* **Saving runs the guard.** The editor's preview and the save are two
  requests, and the second carries whatever the client chose to send.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import NotFoundError, SqlRejectedError, ValidationError
from app.infra.db.models import Dashboard, DashboardTile, DatabaseConnection
from app.services.dashboard_service import DashboardService

OWNER = uuid4()
CONNECTION_ID = uuid4()

SNAPSHOT_TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [{"name": "id"}, {"name": "status"}, {"name": "total_amount"}],
    }
]


class FakeSnapshotRow:
    def __init__(self, tables: list[dict]) -> None:
        self.tables = tables
        self.relationships: list[dict] = []
        self.dialect = "postgres"
        self.catalog_meta: dict[str, Any] = {}


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
    def __init__(
        self,
        *,
        dashboard: Dashboard,
        tiles: list[DashboardTile] | None = None,
        connection: DatabaseConnection | None = None,
        snapshot_tables: list[dict] | None = SNAPSHOT_TABLES,
    ) -> None:
        self.dashboard = dashboard
        self.tiles = tiles or []
        self.connection = connection
        self.snapshot_tables = snapshot_tables
        self.added: list[Any] = []
        self.refreshed: list[Any] = []
        self.flushes = 0

    async def execute(self, statement: Any) -> FakeResult:
        sql = str(statement).lower()
        if "schema_snapshots" in sql:
            return FakeResult(
                [FakeSnapshotRow(self.snapshot_tables)] if self.snapshot_tables else []
            )
        if "dashboard_tile_cache" in sql:
            return FakeResult([])
        if "dashboard_tiles" in sql:
            return FakeResult(list(self.tiles))
        if "database_connections" in sql:
            return FakeResult([self.connection] if self.connection else [])
        if "llm_configs" in sql:
            return FakeResult([])
        return FakeResult([self.dashboard] if self.dashboard else [])

    def add(self, entity: Any) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, entity: Any) -> None:
        self.refreshed.append(entity)

    async def delete(self, entity: Any) -> None:
        self.tiles = [t for t in self.tiles if t is not entity]


def _dashboard(owner_id: UUID = OWNER) -> Dashboard:
    return Dashboard(
        id=uuid4(),
        owner_id=owner_id,
        name="Ops",
        status="ACTIVE",
        grid_columns=12,
        row_height_px=60,
        gap_px=12,
        compact_mode="VERTICAL",
        palette="default",
        theme_override="INHERIT",
        default_refresh_interval_seconds=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _tile(dashboard: Dashboard, **overrides: Any) -> DashboardTile:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "dashboard_id": dashboard.id,
        "connection_id": CONNECTION_ID,
        "title": "Orders",
        "tile_type": "CHART",
        "sql": "SELECT status FROM public.orders",
        "sql_origin": "HANDWRITTEN",
        "chart_config": None,
        "max_rows": None,
        "refresh_interval_seconds": None,
        "grid_x": 0,
        "grid_y": 0,
        "grid_w": 4,
        "grid_h": 4,
        "position": 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    return DashboardTile(**{**fields, **overrides})


def _connection(max_rows: int = 1000) -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID,
        owner_id=OWNER,
        name="sales",
        database_type="postgres",
        host="db",
        port=5432,
        database_name="sales",
        username="readonly",
        encrypted_password="enc",
        max_rows=max_rows,
        statement_timeout_ms=30_000,
    )


def _service(db: FakeDb) -> DashboardService:
    return DashboardService(db, object())


# ── refresh-after-update ─────────────────────────────────────────────────
async def test_renaming_a_dashboard_refreshes_the_row_before_it_is_read() -> None:
    dashboard = _dashboard()
    db = FakeDb(dashboard=dashboard)

    # The duplicate-name check runs the same select, so the "existing" row it
    # finds is this dashboard itself; renaming to its own name is the case
    # that must not raise.
    await _service(db).update(dashboard.id, OWNER, gap_px=20)

    assert db.flushes == 1
    assert db.refreshed == [dashboard]


async def test_editing_a_tile_refreshes_it_too() -> None:
    dashboard = _dashboard()
    tile = _tile(dashboard)
    db = FakeDb(dashboard=dashboard, tiles=[tile], connection=_connection())

    await _service(db).update_tile(dashboard.id, tile.id, OWNER, title="Renamed")

    assert db.refreshed == [tile]


async def test_a_layout_save_refreshes_every_tile_it_moved() -> None:
    """The bug this test exists for: `set_layout` flushed and returned rows
    whose `updated_at` was expired, and the response then tried to read it."""
    dashboard = _dashboard()
    moved, untouched = _tile(dashboard), _tile(dashboard)
    db = FakeDb(dashboard=dashboard, tiles=[moved, untouched])

    await _service(db).set_layout(
        dashboard.id, OWNER, [{"tile_id": moved.id, "grid_x": 6, "position": 1}]
    )

    assert db.refreshed == [moved]
    assert moved.grid_x == 6 and moved.position == 1


async def test_a_layout_entry_for_a_tile_that_is_gone_is_ignored() -> None:
    """A drag that raced a delete in another tab finishes; it does not fail the
    whole layout save."""
    dashboard = _dashboard()
    tile = _tile(dashboard)
    db = FakeDb(dashboard=dashboard, tiles=[tile])

    tiles = await _service(db).set_layout(
        dashboard.id, OWNER, [{"tile_id": uuid4(), "grid_x": 6}]
    )

    assert [t.id for t in tiles] == [tile.id]
    assert db.refreshed == []


# ── the guard runs on the way in ─────────────────────────────────────────
async def test_saving_hostile_sql_is_refused() -> None:
    dashboard = _dashboard()
    db = FakeDb(dashboard=dashboard, connection=_connection())

    with pytest.raises(SqlRejectedError):
        await _service(db).add_tile(
            dashboard.id,
            OWNER,
            tile_type="CHART",
            connection_id=CONNECTION_ID,
            sql="SELECT * FROM public.orders; DROP TABLE public.orders",
        )

    assert db.added == []


async def test_saving_sql_against_an_unsynced_connection_is_refused() -> None:
    dashboard = _dashboard()
    db = FakeDb(dashboard=dashboard, connection=_connection(), snapshot_tables=[])

    with pytest.raises(ValidationError):
        await _service(db).add_tile(
            dashboard.id,
            OWNER,
            tile_type="CHART",
            connection_id=CONNECTION_ID,
            sql="SELECT status FROM public.orders",
        )


async def test_a_tile_row_cap_is_stored_already_clamped() -> None:
    """So the editor never shows a cap the connection would not honour."""
    dashboard = _dashboard()
    db = FakeDb(dashboard=dashboard, connection=_connection(max_rows=1000))

    tile = await _service(db).add_tile(
        dashboard.id,
        OWNER,
        tile_type="CHART",
        connection_id=CONNECTION_ID,
        sql="SELECT status FROM public.orders",
        max_rows=5000,
    )

    assert tile.max_rows == 1000


async def test_a_text_tile_needs_no_connection_and_keeps_no_sql() -> None:
    """And may not smuggle a statement in that a later type change would make
    executable."""
    dashboard = _dashboard()
    db = FakeDb(dashboard=dashboard)

    tile = await _service(db).add_tile(
        dashboard.id,
        OWNER,
        tile_type="TEXT",
        title="Notes",
        sql="SELECT * FROM public.orders",
    )

    assert tile.sql == ""
    assert tile.connection_id is None


async def test_a_chart_tile_without_a_connection_is_refused() -> None:
    dashboard = _dashboard()
    db = FakeDb(dashboard=dashboard)

    with pytest.raises(ValidationError):
        await _service(db).add_tile(
            dashboard.id, OWNER, tile_type="CHART", sql="SELECT 1"
        )


async def test_a_tile_may_not_borrow_another_users_connection() -> None:
    dashboard = _dashboard()
    # The owner-scoped lookup finds nothing, which is the point.
    db = FakeDb(dashboard=dashboard, connection=None)

    with pytest.raises(NotFoundError):
        await _service(db).add_tile(
            dashboard.id,
            OWNER,
            tile_type="CHART",
            connection_id=CONNECTION_ID,
            sql="SELECT status FROM public.orders",
        )


async def test_another_users_dashboard_is_not_found() -> None:
    db = FakeDb(dashboard=None)  # type: ignore[arg-type]

    with pytest.raises(NotFoundError):
        await _service(db).get(uuid4(), OWNER)


# ── duplicate ────────────────────────────────────────────────────────────
async def test_a_duplicate_copies_the_tile_but_not_its_cache() -> None:
    """A copy has its own clock and its own first refresh."""
    dashboard = _dashboard()
    tile = _tile(dashboard, title="Revenue", refresh_interval_seconds=30)
    db = FakeDb(dashboard=dashboard, tiles=[tile])

    copy = await _service(db).duplicate_tile(dashboard.id, tile.id, OWNER)

    assert copy.id != tile.id
    assert copy.title == "Revenue (copy)"
    assert copy.sql == tile.sql
    assert copy.refresh_interval_seconds == 30
    assert copy.grid_y == tile.grid_y + tile.grid_h
    assert [type(a).__name__ for a in db.added] == ["DashboardTile"]
