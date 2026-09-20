"""Drift: a schema sync moves the ground under a saved section.

`docs/plans/retrieval-sections.md` §1.4, Phase 3. The rule is the one the rest
of the product already applies to a semantic layer and a stale template —
**flag it, never delete it**: deleting a person's work to hide drift is worse
than showing it.

Two directions, and they fail differently. A member the snapshot no longer has
degrades recall and can do nothing worse: it cannot be rendered and it cannot
pass the guard, so it is marked and skipped. A table the snapshot has *gained*
is in no section, is therefore in nothing the router can pick, and would
silently never be retrieved — so the set says which unassigned tables are new,
and the screen can say so.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.infra.db import models
from app.services.section_service import SectionService, SectionWrite
from tests.unit.conftest import ACTOR, AsyncSessionShim, _connection
from tests.unit.test_access_behaviour import _every_table  # noqa: F401 — fixture


def _t(name: str) -> dict[str, Any]:
    return {"schema": "public", "name": name, "approx_row_count": 10, "columns": [
        {"name": "id", "data_type": "bigint"},
        {"name": f"{name}_label", "data_type": "text"},
    ]}


WAS = ["orders", "order_items", "customers"]
NOW = ["orders", "customers", "refunds", "shipments"]  # order_items gone, two new


def _snapshot(db: AsyncSessionShim, connection_id: Any, version: int, names: list[str]):
    row = models.SchemaSnapshotRow(
        id=uuid4(), connection_id=connection_id, version=version, dialect="postgres",
        tables=[_t(n) for n in names], relationships=[], table_count=len(names),
        catalog_meta={},
    )
    db._session.add(row)
    db._session.flush()
    return row


def _section(
    db: AsyncSessionShim, connection_id: Any, name: str, tables: list[str],
    *, version: int | None,
) -> models.ConnectionSection:
    row = models.ConnectionSection(
        id=uuid4(), connection_id=connection_id, name=name,
        description=f"{name} things.", tables=tables, origin="CURATED",
        schema_version=version, position=0,
    )
    db._session.add(row)
    db._session.flush()
    return row


async def _drifted(db: AsyncSessionShim) -> tuple[Any, Any]:
    """A connection whose sections were saved against v1 and whose schema is
    now v2: one member dropped, two tables added."""
    connection = _connection(db._session, owner_id=ACTOR)
    _snapshot(db, connection.id, 1, WAS)
    _snapshot(db, connection.id, 2, NOW)
    _section(db, connection.id, "Sales", ["public.orders", "public.order_items"],
             version=1)
    return connection, await SectionService(db).read(connection)


# ── a member that has left the schema ────────────────────────────────────
async def test_a_dropped_member_is_flagged_and_kept(db: AsyncSessionShim) -> None:
    _connection_, result = await _drifted(db)
    (sales,) = result.sections

    # Kept, in the saved order — nothing was rewritten to hide the drift.
    assert sales.tables == ["public.orders", "public.order_items"]
    assert sales.missing == ["public.order_items"]


async def test_a_dropped_member_costs_nothing_and_sizes_nothing(
    db: AsyncSessionShim,
) -> None:
    """It cannot be rendered and cannot pass the guard, so it is not part of
    what the section costs — the badge would otherwise warn about characters
    nothing will ever send."""
    _connection_, result = await _drifted(db)
    (sales,) = result.sections

    from app.pipeline.metadata import table_chars

    assert sales.chars == table_chars(_t("orders"))
    assert sales.fit == "FITS"


# ── a table the schema has gained ────────────────────────────────────────
async def test_tables_added_since_the_save_are_marked_new(
    db: AsyncSessionShim,
) -> None:
    _connection_, result = await _drifted(db)

    assert result.unassigned == ["public.customers", "public.refunds", "public.shipments"]
    # `customers` was there when the sections were saved and was left out on
    # purpose. The other two nobody has ever seen.
    assert result.new_tables == ["public.refunds", "public.shipments"]


async def test_a_new_table_somebody_has_already_placed_is_not_new(
    db: AsyncSessionShim,
) -> None:
    connection = _connection(db._session, owner_id=ACTOR)
    _snapshot(db, connection.id, 1, WAS)
    _snapshot(db, connection.id, 2, NOW)
    # Saved against v1, but holding a table only v2 has: somebody added it
    # after the sync and before this read.
    _section(db, connection.id, "Sales", ["public.orders", "public.refunds"], version=1)

    result = await SectionService(db).read(connection)

    assert result.new_tables == ["public.shipments"]


async def test_current_sections_mark_nothing(db: AsyncSessionShim) -> None:
    """Unassigned is not drift. A table left out on purpose must not be
    marked as one somebody has not seen."""
    connection = _connection(db._session, owner_id=ACTOR)
    _snapshot(db, connection.id, 2, NOW)
    _section(db, connection.id, "Sales", ["public.orders"], version=2)

    result = await SectionService(db).read(connection)

    assert result.unassigned != []
    assert result.new_tables == []


async def test_a_set_with_no_stamp_marks_nothing(db: AsyncSessionShim) -> None:
    """`schema_version` is NULL on nothing this codebase writes — `save`
    always stamps — but a row from a hand-edited database must not turn every
    table in the connection into a new one."""
    connection = _connection(db._session, owner_id=ACTOR)
    _snapshot(db, connection.id, 2, NOW)
    _section(db, connection.id, "Sales", ["public.orders"], version=None)

    assert (await SectionService(db).read(connection)).new_tables == []


async def test_a_pruned_snapshot_marks_nothing(db: AsyncSessionShim) -> None:
    """The question is "which tables are new since v1?", and without v1 there
    is no answer. Marking everything would be an answer, and a wrong one."""
    connection = _connection(db._session, owner_id=ACTOR)
    _snapshot(db, connection.id, 2, NOW)
    _section(db, connection.id, "Sales", ["public.orders"], version=1)

    assert (await SectionService(db).read(connection)).new_tables == []


async def test_saving_takes_the_drift_as_seen(db: AsyncSessionShim) -> None:
    """A save stamps the current snapshot, so the markers clear — which is
    what makes them a to-do list rather than a permanent complaint."""
    connection, before = await _drifted(db)
    assert before.new_tables != []

    after = await SectionService(db).save(
        connection,
        [SectionWrite(name="Sales", description="Orders.", tables=["public.orders"])],
    )

    assert after.new_tables == []
    assert after.sections[0].schema_version == 2


# ── the date the screen says it ──────────────────────────────────────────
async def test_the_set_says_when_the_schema_it_measured_landed(
    db: AsyncSessionShim,
) -> None:
    """*"not in the current schema — re-synced 12 Sept"*: the sentence needs
    the date of the snapshot being measured against, not the connection's
    `last_synced_at`, which is about the newest one only."""
    connection = _connection(db._session, owner_id=ACTOR)
    current = _snapshot(db, connection.id, 2, NOW)

    result = await SectionService(db).read(connection)

    assert result.synced_at == current.created_at
