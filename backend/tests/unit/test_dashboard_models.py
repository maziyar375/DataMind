"""The dashboard tables, and the promises their DDL makes.

Two definitions of the same three tables now exist — the ORM in `models.py` and
migration `0005_dashboards.py` — and nothing but a running database usually
notices when they drift apart. So the migration is replayed here against a
recorder instead of a connection, and compared to `Base.metadata` column by
column.

The rest of the file pins the choices in §4 of `docs/dashboards.md` that are
easy to "tidy" into a bug: a tile survives its connection, NULL means Auto,
NULL means inherit, and every enum-shaped column is a plain string.
"""
from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import sqlalchemy as sa

from app.domain.value_objects import DashboardStatus, SqlOrigin, TileType
from app.infra.db.models import Base

# A revision module imports `alembic.op` and nothing else from alembic, and
# this test replaces `op` regardless — so a stub is enough to read the DDL
# without alembic installed, which keeps the drift check running in the plain
# test environment as well as in the container that actually migrates.
if "alembic" not in sys.modules:
    try:
        import alembic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("alembic")
        stub.op = None  # type: ignore[attr-defined]
        sys.modules["alembic"] = stub

# Not an `import` statement: the module's name starts with a digit, as every
# revision in `versions/` does.
MIGRATION = importlib.import_module("app.infra.db.migrations.versions.0005_dashboards")

TABLES = ("dashboards", "dashboard_tiles", "dashboard_tile_cache")


class OpRecorder:
    """Stands in for `alembic.op`: records the DDL instead of emitting it."""

    def __init__(self) -> None:
        self.tables: dict[str, sa.Table] = {}
        self.indexes: list[tuple[str, str, list[str]]] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []
        self._metadata = sa.MetaData()

    def create_table(self, name: str, *columns: Any, **_kw: Any) -> None:
        self.tables[name] = sa.Table(name, self._metadata, *columns)

    def create_index(
        self, name: str, table: str, columns: list[str], **_kw: Any
    ) -> None:
        self.indexes.append((name, table, list(columns)))

    def drop_table(self, name: str, **_kw: Any) -> None:
        self.dropped_tables.append(name)

    def drop_index(self, name: str, **_kw: Any) -> None:
        self.dropped_indexes.append(name)


def _replay(direction: str = "upgrade") -> OpRecorder:
    recorder = OpRecorder()
    original = MIGRATION.op
    MIGRATION.op = recorder
    try:
        getattr(MIGRATION, direction)()
    finally:
        MIGRATION.op = original
    return recorder


def _recorded() -> OpRecorder:
    return _replay()


def _ondelete(column: sa.Column[Any]) -> str | None:
    return next((fk.ondelete for fk in column.foreign_keys), None)


# ── the two definitions must agree ───────────────────────────────────────
def test_the_migration_creates_exactly_the_orm_tables() -> None:
    assert set(_recorded().tables) == set(TABLES)


def test_the_migration_and_the_orm_agree_column_for_column() -> None:
    recorded = _recorded().tables
    for name in TABLES:
        orm = Base.metadata.tables[name]
        assert {c.name for c in recorded[name].columns} == {
            c.name for c in orm.columns
        }, name


def test_they_agree_on_what_may_be_null() -> None:
    """The nullability *is* the feature in three places here; a column that is
    NOT NULL in the database and optional in the ORM fails at INSERT time, in
    production, on a path nobody exercised."""
    recorded = _recorded().tables
    for name in TABLES:
        orm = Base.metadata.tables[name]
        for column in recorded[name].columns:
            assert column.nullable == orm.columns[column.name].nullable, (
                f"{name}.{column.name}"
            )


def test_they_agree_on_every_foreign_key_and_its_delete_rule() -> None:
    recorded = _recorded().tables
    for name in TABLES:
        orm = Base.metadata.tables[name]
        for column in recorded[name].columns:
            mirror = orm.columns[column.name]
            assert {fk.target_fullname for fk in column.foreign_keys} == {
                fk.target_fullname for fk in mirror.foreign_keys
            }, f"{name}.{column.name}"
            assert _ondelete(column) == _ondelete(mirror), f"{name}.{column.name}"


def test_the_revision_chain_is_unbroken() -> None:
    assert MIGRATION.revision == "0005"
    assert MIGRATION.down_revision == "0004"


def test_the_downgrade_drops_exactly_what_the_upgrade_created() -> None:
    up, down = _replay("upgrade"), _replay("downgrade")

    assert set(down.dropped_tables) == set(up.tables)
    assert set(down.dropped_indexes) == {name for name, _t, _c in up.indexes}


# ── the choices that must survive a tidy-up ──────────────────────────────
def test_a_deleted_connection_leaves_the_tile_standing() -> None:
    """SET NULL, not CASCADE. Deleting a connection must leave a tile saying
    "connection removed" — never silently delete the layout the user built."""
    tiles = Base.metadata.tables["dashboard_tiles"]

    assert _ondelete(tiles.c.connection_id) == "SET NULL"
    assert tiles.c.connection_id.nullable
    assert _ondelete(tiles.c.llm_config_id) == "SET NULL"
    assert tiles.c.llm_config_id.nullable


def test_a_deleted_dashboard_takes_its_tiles_and_their_cache() -> None:
    tiles = Base.metadata.tables["dashboard_tiles"]
    cache = Base.metadata.tables["dashboard_tile_cache"]
    dashboards = Base.metadata.tables["dashboards"]

    assert _ondelete(tiles.c.dashboard_id) == "CASCADE"
    assert _ondelete(cache.c.tile_id) == "CASCADE"
    assert _ondelete(dashboards.c.owner_id) == "CASCADE"


def test_null_chart_config_means_auto() -> None:
    """Not an empty object: `plan_chart` re-decides on every result, so the
    column has to be able to say "nothing stored" rather than "no chart"."""
    chart_config = Base.metadata.tables["dashboard_tiles"].c.chart_config

    assert chart_config.nullable
    assert chart_config.default is None
    assert chart_config.server_default is None


def test_null_refresh_interval_means_inherit_and_zero_means_manual() -> None:
    """The per-tile rate is the feature. NULL and 0 are different answers, so
    the column may not carry a default that collapses them."""
    tiles = Base.metadata.tables["dashboard_tiles"]
    dashboards = Base.metadata.tables["dashboards"]

    assert tiles.c.refresh_interval_seconds.nullable
    assert tiles.c.refresh_interval_seconds.server_default is None
    # The dashboard's fallback is not nullable, and defaults to manual: a
    # dashboard nobody configured must not start polling on its own.
    assert not dashboards.c.default_refresh_interval_seconds.nullable
    assert dashboards.c.default_refresh_interval_seconds.default.arg == 0  # type: ignore[union-attr]
    # And in the database too, for rows the ORM did not write.
    recorded = _recorded().tables["dashboards"]
    assert recorded.c.default_refresh_interval_seconds.server_default.arg == "0"  # type: ignore[union-attr]


def test_a_tile_max_rows_override_is_optional() -> None:
    """Unset, not "unlimited": the connection's cap applies when this is NULL."""
    assert Base.metadata.tables["dashboard_tiles"].c.max_rows.nullable


def test_one_dashboard_name_per_owner() -> None:
    constraints = {
        c.name for c in Base.metadata.tables["dashboards"].constraints
    }
    assert "uq_dashboard_owner_name" in constraints


def test_layout_lives_per_tile_not_in_one_blob() -> None:
    """One row per drag, so two open tabs cannot lose each other's edits."""
    columns = Base.metadata.tables["dashboard_tiles"].columns

    for column in ("grid_x", "grid_y", "grid_w", "grid_h", "position"):
        assert isinstance(columns[column].type, sa.Integer)


def test_enum_shaped_columns_are_plain_strings() -> None:
    """Like `runs.status`: a new tile type or status needs no DDL."""
    for name in TABLES:
        for column in Base.metadata.tables[name].columns:
            assert not isinstance(column.type, sa.Enum), f"{name}.{column.name}"

    tiles = Base.metadata.tables["dashboard_tiles"]
    assert isinstance(tiles.c.tile_type.type, sa.String)
    assert isinstance(tiles.c.sql_origin.type, sa.String)
    assert isinstance(Base.metadata.tables["dashboards"].c.status.type, sa.String)


def test_the_defaults_are_members_of_the_enums_that_describe_them() -> None:
    dashboards = Base.metadata.tables["dashboards"]
    tiles = Base.metadata.tables["dashboard_tiles"]

    assert dashboards.c.status.default.arg == DashboardStatus.ACTIVE  # type: ignore[union-attr]
    assert tiles.c.tile_type.default.arg == TileType.CHART  # type: ignore[union-attr]
    assert tiles.c.sql_origin.default.arg == SqlOrigin.GENERATED  # type: ignore[union-attr]


def test_sql_origin_never_reaches_the_executor() -> None:
    """Three origins, one guard. The enum exists for the editor, not for the
    executor: what Phase 1 runs a tile from carries no provenance at all, so
    there is nothing there to grant a tile trust with."""
    import dataclasses

    from app.services.query_service import TileRequest

    assert set(SqlOrigin) == {
        SqlOrigin.GENERATED,
        SqlOrigin.GENERATED_EDITED,
        SqlOrigin.HANDWRITTEN,
    }
    fields = {f.name for f in dataclasses.fields(TileRequest)}
    assert "sql_origin" not in fields and "question" not in fields
