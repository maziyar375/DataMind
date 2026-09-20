"""`connection_sections`: the migration and the ORM describe the same table.

Replayed against a recorder rather than a database, as the dashboard tables
are (`test_dashboard_models.py`), so the drift check runs in the plain test
environment. `alembic upgrade head` against Postgres is the other half, run by
hand for this revision (`docs/plans/retrieval-sections.md` §14).
"""
from __future__ import annotations

import importlib

import sqlalchemy as sa

from app.infra.db.models import Base
from tests.unit.test_dashboard_models import OpRecorder

MIGRATION = importlib.import_module(
    "app.infra.db.migrations.versions.0035_connection_sections"
)


def _replay(direction: str) -> OpRecorder:
    recorder = OpRecorder()
    original = MIGRATION.op
    try:
        MIGRATION.op = recorder
        getattr(MIGRATION, direction)()
    finally:
        MIGRATION.op = original
    return recorder


def test_the_revision_follows_0034() -> None:
    assert (MIGRATION.revision, MIGRATION.down_revision) == ("0035", "0034")


def test_the_migration_and_the_orm_agree_column_for_column() -> None:
    recorded = _replay("upgrade").tables["connection_sections"]
    orm = Base.metadata.tables["connection_sections"]
    assert {c.name for c in recorded.columns} == {c.name for c in orm.columns}
    for column in recorded.columns:
        mirror = orm.columns[column.name]
        assert column.nullable == mirror.nullable, column.name
        assert {fk.target_fullname for fk in column.foreign_keys} == {
            fk.target_fullname for fk in mirror.foreign_keys
        }, column.name


def test_a_section_dies_with_its_connection() -> None:
    """CASCADE, like the semantic layer: a section describes one connection's
    schema and means nothing without it. (Runs keep section *names*, not ids,
    so deleting sections rewrites no history.)"""
    fk = next(iter(Base.metadata.tables["connection_sections"].c.connection_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_the_name_is_unique_per_connection_ignoring_case() -> None:
    """The name is the token the router replies with; *Sales* and *sales*
    would be one reply naming two sections."""
    up = _replay("upgrade")
    assert ("uq_connection_sections_name", "connection_sections") in {
        (name, table) for name, table, _cols in up.indexes
    }
    (index,) = [
        i for i in Base.metadata.tables["connection_sections"].indexes
        if i.name == "uq_connection_sections_name"
    ]
    assert index.unique
    rendered = [str(e) for e in index.expressions]
    assert rendered[0].endswith("connection_id") and "lower(name)" in rendered[1]


def test_the_downgrade_drops_what_the_upgrade_created() -> None:
    up, down = _replay("upgrade"), _replay("downgrade")
    assert set(down.dropped_tables) == set(up.tables)
    assert set(down.dropped_indexes) == {name for name, _t, _c in up.indexes}


def test_tables_is_a_text_array_that_references_nothing() -> None:
    column = Base.metadata.tables["connection_sections"].c.tables
    assert isinstance(column.type, sa.ARRAY)
    assert not column.foreign_keys
