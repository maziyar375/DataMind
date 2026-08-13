"""Moving a dashboard as a file.

Three claims carry this feature, and the rest of the file is detail:

* **A document is a definition, never an extract.** No ids, no results, and
  nothing from inside a connection — a file leaves the system, and whoever ends
  up holding it must not be holding a way into someone's database.
* **An imported statement is hostile input.** `sql` in a `.json` file is typed
  as easily as `sql` in the editor's textarea, so import is a *fourth* entry
  point to the guard and gets no exemption. The hostile-corpus replay below is
  what proves it.
* **A refused import creates nothing.** Every tile is validated before the
  dashboard row exists, so the failure mode is a sentence naming the tiles —
  not half a dashboard the user has to notice is half.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import NotFoundError, SqlRejectedError, ValidationError
from app.infra.db.models import Dashboard, DashboardTile, DatabaseConnection
from app.services.dashboard_service import DashboardService
from app.services.dashboard_transfer import (
    DOCUMENT_FORMAT,
    DOCUMENT_VERSION,
    build_document,
    parse_document,
)

OWNER = uuid4()
CONNECTION_ID = uuid4()
OTHER_CONNECTION_ID = uuid4()

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
    """Enough session to run an import, and no more.

    The statement text is matched rather than the ORM entity because that is
    what the other service tests do, and because the two selects over
    `dashboards` here are told apart by their *where* clause: the free-name scan
    reads the name column, the duplicate check filters on it.
    """

    def __init__(
        self,
        *,
        dashboards: list[Dashboard] | None = None,
        connections: list[DatabaseConnection] | None = None,
        snapshot_tables: list[dict] | None = SNAPSHOT_TABLES,
    ) -> None:
        self.dashboards = dashboards or []
        self.connections = connections or []
        self.snapshot_tables = snapshot_tables
        self.tiles: list[DashboardTile] = []
        self.added: list[Any] = []
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
        if "llm_configs" in sql:
            return FakeResult([])
        if sql.startswith("select database_connections.id, database_connections.name"):
            return FakeResult([(c.id, c.name) for c in self.connections])
        if "database_connections" in sql:
            # `_owned_connection` — a row is only visible to its owner, which is
            # what makes a connection id in a request body harmless.
            return FakeResult([c for c in self.connections if c.owner_id == OWNER])
        if sql.startswith("select dashboards.name"):
            return FakeResult([d.name for d in self.dashboards])
        if "dashboards.name =" in sql:
            # The duplicate-name check. It has to answer for the *name asked
            # about*, or "Ops (3) is free" and "Ops (3) already exists" are the
            # same query.
            wanted = statement.compile().params.get("name_1")
            return FakeResult([d for d in self.dashboards if d.name == wanted])
        return FakeResult(list(self.dashboards))

    def add(self, entity: Any) -> None:
        self.added.append(entity)
        if isinstance(entity, DashboardTile):
            self.tiles.append(entity)
        if isinstance(entity, Dashboard):
            self.dashboards.append(entity)

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, entity: Any) -> None:
        pass

    async def delete(self, entity: Any) -> None:
        pass


def _dashboard(name: str = "Ops", owner_id: UUID = OWNER) -> Dashboard:
    return Dashboard(
        id=uuid4(),
        owner_id=owner_id,
        name=name,
        description="Revenue and orders",
        status="ACTIVE",
        grid_columns=12,
        row_height_px=60,
        gap_px=12,
        compact_mode="NONE",
        palette="default",
        theme_override="DARK",
        default_refresh_interval_seconds=300,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _tile(dashboard: Dashboard, **overrides: Any) -> DashboardTile:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "dashboard_id": dashboard.id,
        "connection_id": CONNECTION_ID,
        "llm_config_id": uuid4(),
        "title": "Orders",
        "tile_type": "CHART",
        "question": "orders by status",
        "sql": "SELECT status FROM public.orders",
        "sql_origin": "HANDWRITTEN",
        "chart_config": {"chart_type": "bar"},
        "table_config": None,
        "max_rows": 500,
        "refresh_interval_seconds": 30,
        "grid_x": 0,
        "grid_y": 0,
        "grid_w": 4,
        "grid_h": 4,
        "position": 0,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    return DashboardTile(**{**fields, **overrides})


def _connection(
    connection_id: UUID = CONNECTION_ID,
    name: str = "sales",
    owner_id: UUID = OWNER,
) -> DatabaseConnection:
    return DatabaseConnection(
        id=connection_id,
        owner_id=owner_id,
        name=name,
        database_type="postgres",
        host="db.internal",
        port=5432,
        database_name="sales",
        username="analytics_ro",
        encrypted_password="ciphertext",
        max_rows=1000,
        statement_timeout_ms=30_000,
    )


def _service(db: FakeDb) -> DashboardService:
    return DashboardService(db, object())


def _document(**tile_overrides: Any) -> dict[str, Any]:
    """A minimal valid file, written the way an export writes one."""
    tile = {
        "connection_ref": "c1",
        "title": "Orders",
        "tile_type": "CHART",
        "sql": "SELECT status FROM public.orders",
        "sql_origin": "HANDWRITTEN",
        "grid_w": 4,
        "grid_h": 4,
        **tile_overrides,
    }
    return {
        "format": DOCUMENT_FORMAT,
        "version": DOCUMENT_VERSION,
        "dashboard": {"name": "Ops"},
        "connections": [{"ref": "c1", "name": "sales", "database_type": "postgres"}],
        "tiles": [tile],
    }


# ── what leaves ──────────────────────────────────────────────────────────
def test_an_export_names_each_connection_once_and_by_ref() -> None:
    dashboard = _dashboard()
    tiles = [_tile(dashboard), _tile(dashboard, title="Revenue")]

    document = build_document(dashboard, tiles, {CONNECTION_ID: _connection()})

    assert [c.ref for c in document.connections] == ["c1"]
    assert {t.connection_ref for t in document.tiles} == {"c1"}


def test_an_export_carries_no_connection_internals() -> None:
    """A file outlives the account that wrote it. A name and an engine are what
    the importer needs; the address and the credentials are not theirs."""
    dashboard = _dashboard()

    document = build_document(
        dashboard, [_tile(dashboard)], {CONNECTION_ID: _connection()}
    )

    dumped = document.model_dump_json()
    for leak in ("db.internal", "analytics_ro", "ciphertext", str(CONNECTION_ID)):
        assert leak not in dumped
    assert document.connections[0].name == "sales"
    assert document.connections[0].database_type == "postgres"


def test_a_tile_whose_connection_is_gone_exports_unmapped() -> None:
    """`SET NULL` is a real state, not a corrupt file: the tile survives its
    connection, and the importer is asked for a new one."""
    dashboard = _dashboard()
    orphan = _tile(dashboard, connection_id=None, tile_type="TEXT", sql="")

    document = build_document(dashboard, [orphan], {})

    assert document.connections == []
    assert document.tiles[0].connection_ref is None


def test_a_status_this_version_does_not_know_exports_as_the_default() -> None:
    """The status columns are plain strings so a new member needs no DDL. That
    freedom stops at the file: a document its own importer would reject is
    worse than one that says VERTICAL."""
    dashboard = _dashboard()
    dashboard.compact_mode = "DIAGONAL"

    document = build_document(dashboard, [], {})

    assert document.dashboard.compact_mode == "VERTICAL"


# ── what comes back ──────────────────────────────────────────────────────
def test_a_file_that_is_not_an_export_is_refused_by_name() -> None:
    with pytest.raises(ValidationError, match="not a dashboard export"):
        parse_document({"hello": "world"})


def test_a_newer_format_says_so_rather_than_listing_field_errors() -> None:
    """The user is holding a file they did not write. "Update DataMind" is
    actionable; twenty complaints about a schema it was never written against
    are not."""
    document = _document()
    document["version"] = DOCUMENT_VERSION + 1

    with pytest.raises(ValidationError, match="Update DataMind"):
        parse_document(document)


def test_a_malformed_document_points_at_the_field() -> None:
    document = _document(grid_w=0)

    with pytest.raises(ValidationError, match=r"tiles\.0\.grid_w"):
        parse_document(document)


# ── the guard, on the import road ────────────────────────────────────────
async def test_hostile_sql_in_a_file_is_refused() -> None:
    """The guard-bypass test for this entry point. A statement typed into a
    `.json` file is the same hostile input as one typed into the editor."""
    db = FakeDb(connections=[_connection()])

    with pytest.raises(ValidationError):
        await _service(db).import_document(
            OWNER,
            document=_document(sql="DROP TABLE public.orders"),
            connection_map={"c1": CONNECTION_ID},
        )


async def test_a_refused_import_creates_nothing_at_all() -> None:
    """Not even the dashboard: every tile is validated before the row exists,
    so a bad file leaves no shell behind for the user to clean up."""
    db = FakeDb(connections=[_connection()])

    with pytest.raises(ValidationError):
        await _service(db).import_document(
            OWNER,
            document=_document(sql="SELECT * FROM information_schema.tables"),
            connection_map={"c1": CONNECTION_ID},
        )

    assert db.added == []


async def test_a_refusal_names_every_tile_it_refused() -> None:
    """One file, one decision: the user needs the whole list to make it."""
    db = FakeDb(connections=[_connection()])
    document = _document()
    document["tiles"] = [
        {"connection_ref": "c1", "title": "Good", "sql": "SELECT status FROM public.orders"},
        {"connection_ref": "c1", "title": "Bad", "sql": "DROP TABLE public.orders"},
        {"connection_ref": "c1", "title": "Worse", "sql": "SELECT * FROM pg_shadow"},
    ]

    with pytest.raises(ValidationError) as caught:
        await _service(db).import_document(
            OWNER, document=document, connection_map={"c1": CONNECTION_ID}
        )

    assert caught.value.detail["tiles"] == ["Bad", "Worse"]
    assert "2 tiles" in caught.value.message


async def test_skip_invalid_imports_the_rest_and_reports_the_loss() -> None:
    """Importing a board against a database that has moved on genuinely loses
    tiles. Dropping them silently would be a dashboard that looks complete."""
    db = FakeDb(connections=[_connection()])
    document = _document()
    document["tiles"] = [
        {"connection_ref": "c1", "title": "Good", "sql": "SELECT status FROM public.orders"},
        {"connection_ref": "c1", "title": "Bad", "sql": "DROP TABLE public.orders"},
    ]

    dashboard, skipped = await _service(db).import_document(
        OWNER,
        document=document,
        connection_map={"c1": CONNECTION_ID},
        skip_invalid=True,
    )

    assert [tile.title for tile in db.tiles] == ["Good"]
    assert [(s.title, s.code) for s in skipped] == [("Bad", "E_SQL_REJECTED")]
    assert dashboard.name == "Ops"


async def test_a_tile_with_no_connection_chosen_is_refused_not_stored() -> None:
    db = FakeDb(connections=[_connection(name="something else")])

    _dash, skipped = await _service(db).import_document(
        OWNER, document=_document(), skip_invalid=True
    )

    assert db.tiles == []
    assert "needs a database connection" in skipped[0].reason


# ── whose connections ────────────────────────────────────────────────────
async def test_a_connection_the_caller_does_not_own_is_not_found() -> None:
    """The id comes out of a request body, so this check is the wall: another
    user's connection is 404, and nothing is decrypted or dialled."""
    db = FakeDb(connections=[_connection(OTHER_CONNECTION_ID, owner_id=uuid4())])

    with pytest.raises(NotFoundError):
        await _service(db).import_document(
            OWNER, document=_document(), connection_map={"c1": OTHER_CONNECTION_ID}
        )


async def test_an_unmapped_ref_falls_back_to_a_connection_of_the_same_name() -> None:
    """What makes re-importing your own export one click. Connection names are
    unique per owner, so the match is never ambiguous."""
    db = FakeDb(connections=[_connection(name="Sales")])

    await _service(db).import_document(OWNER, document=_document())

    assert [tile.connection_id for tile in db.tiles] == [CONNECTION_ID]


async def test_an_explicit_map_beats_a_name_that_happens_to_match() -> None:
    db = FakeDb(
        connections=[_connection(name="sales"), _connection(OTHER_CONNECTION_ID, "warehouse")]
    )

    await _service(db).import_document(
        OWNER, document=_document(), connection_map={"c1": OTHER_CONNECTION_ID}
    )

    assert [tile.connection_id for tile in db.tiles] == [OTHER_CONNECTION_ID]


# ── the dashboard it lands as ────────────────────────────────────────────
async def test_a_name_already_taken_gets_a_number() -> None:
    """The ordinary case is importing a file back into the account that wrote
    it. Refusing that after every statement has passed the guard would be a
    wall in front of the common road."""
    db = FakeDb(dashboards=[_dashboard("Ops"), _dashboard("Ops (2)")],
                connections=[_connection()])

    dashboard, _skipped = await _service(db).import_document(
        OWNER, document=_document(), connection_map={"c1": CONNECTION_ID}
    )

    assert dashboard.name == "Ops (3)"


async def test_the_requested_name_overrides_the_files() -> None:
    db = FakeDb(connections=[_connection()])

    dashboard, _skipped = await _service(db).import_document(
        OWNER,
        document=_document(),
        name="Ops (from Ana)",
        connection_map={"c1": CONNECTION_ID},
    )

    assert dashboard.name == "Ops (from Ana)"


async def test_an_import_keeps_the_layout_the_rates_and_the_provenance() -> None:
    """A dashboard that arrives rearranged, or on one clock, is not the
    dashboard that was sent."""
    db = FakeDb(connections=[_connection()])

    await _service(db).import_document(
        OWNER,
        document=_document(
            grid_x=6, grid_y=2, grid_w=6, grid_h=8, position=3,
            refresh_interval_seconds=30, max_rows=250,
            chart_config={"chart_type": "bar"},
        ),
        connection_map={"c1": CONNECTION_ID},
    )

    tile = db.tiles[0]
    assert (tile.grid_x, tile.grid_y, tile.grid_w, tile.grid_h) == (6, 2, 6, 8)
    assert tile.position == 3
    assert tile.refresh_interval_seconds == 30
    assert tile.max_rows == 250
    assert tile.chart_config == {"chart_type": "bar"}
    assert tile.sql_origin == "HANDWRITTEN"


async def test_an_imported_tile_is_attributed_to_no_model() -> None:
    """`llm_config_id` is a row in the installation the file came from. Which
    model drafted the SQL is provenance, and it does not survive the trip."""
    db = FakeDb(connections=[_connection()])

    await _service(db).import_document(
        OWNER, document=_document(), connection_map={"c1": CONNECTION_ID}
    )

    assert db.tiles[0].llm_config_id is None


async def test_an_export_reimports_as_the_same_dashboard() -> None:
    """The round trip, which is the only claim a user actually makes about
    this feature."""
    source = _dashboard()
    tiles = [
        _tile(source),
        _tile(source, title="Note", tile_type="TEXT", sql="", connection_id=None),
    ]
    document = build_document(source, tiles, {CONNECTION_ID: _connection()})
    db = FakeDb(connections=[_connection()])

    imported, skipped = await _service(db).import_document(
        OWNER,
        document=document.model_dump(mode="json"),
        connection_map={"c1": CONNECTION_ID},
    )

    assert skipped == []
    assert imported.compact_mode == "NONE" and imported.theme_override == "DARK"
    assert imported.default_refresh_interval_seconds == 300
    assert [t.title for t in db.tiles] == ["Orders", "Note"]
    assert [t.tile_type for t in db.tiles] == ["CHART", "TEXT"]
    assert db.tiles[0].sql == "SELECT status FROM public.orders"
    # A TEXT tile carries no statement and no connection, on the way out and on
    # the way back.
    assert db.tiles[1].sql == "" and db.tiles[1].connection_id is None


async def test_the_guard_runs_against_the_importers_snapshot_not_the_files() -> None:
    """A tile is only as valid as the database it lands on. An unsynced
    connection can hold no SQL at all."""
    db = FakeDb(connections=[_connection()], snapshot_tables=[])

    with pytest.raises(ValidationError, match="Sync this connection"):
        await _service(db).import_document(
            OWNER, document=_document(), connection_map={"c1": CONNECTION_ID}
        )


async def test_a_row_cap_above_the_connections_is_lowered_to_it() -> None:
    """Containment belongs to the connection. A file may not raise it."""
    db = FakeDb(connections=[_connection()])

    await _service(db).import_document(
        OWNER,
        document=_document(max_rows=100_000),
        connection_map={"c1": CONNECTION_ID},
    )

    assert db.tiles[0].max_rows == 1000


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT status FROM public.orders; DROP TABLE public.orders",
        "SELECT * FROM pg_catalog.pg_shadow",
        "UPDATE public.orders SET status = 'paid'",
        "SELECT status FROM public.orders FOR UPDATE",
        "WITH x AS (DELETE FROM public.orders RETURNING *) SELECT * FROM x",
        "SELECT pg_read_file('/etc/passwd')",
        "COPY public.orders TO '/tmp/out.csv'",
    ],
)
async def test_the_hostile_corpus_does_not_survive_a_file(sql: str) -> None:
    """A sample of `test_sqlguard_hostile.py`, replayed through the import
    road. The full corpus lives there; this asserts the road reaches it."""
    db = FakeDb(connections=[_connection()])

    with pytest.raises(ValidationError):
        await _service(db).import_document(
            OWNER, document=_document(sql=sql), connection_map={"c1": CONNECTION_ID}
        )

    assert db.tiles == []


def test_a_rejection_is_a_sql_rejection_not_a_generic_one() -> None:
    """`SqlRejectedError` subclasses nothing the API maps to a 500, and the
    frontend branches on the code."""
    assert issubclass(SqlRejectedError, Exception)
    assert SqlRejectedError("x").code == "E_SQL_REJECTED"
