"""Every document that reached the model is kept, attributed, and restorable.

Phase 1 of `docs/plans/semantic-layer-model.md`. Before it, `PUT /semantic`
overwrote one row in place: no version, no author, no note, no restore, and no
audit row — a `REPLACE` generation or a wrong save could not be undone.

Run against a real schema on SQLite (`semantic_world.py`), because the claims
are about what lands in three tables in one transaction.
"""
from __future__ import annotations

import importlib
import inspect

import pytest
import sqlalchemy as sa

from app.api.v1 import semantic as semantic_routes
from app.core.errors import NotFoundError, SemanticNoChangesError, ValidationError
from app.domain.value_objects.authz import Privilege
from app.infra.db.models import SemanticLayerChangeRow
from app.semantic import SemanticColumn, SemanticDocument
from app.semantic import diff as d
from app.services import audit
from app.services.semantic_service import (
    SEMANTIC_DELETED,
    SEMANTIC_RESTORED,
    SEMANTIC_SAVED,
    document_sha256,
    load_document,
    load_layer,
)
from tests.unit.semantic_world import (
    AUTHOR,
    OTHER,
    assert_head_is_its_published_version,
    audit_rows,
    change_rows,
    connection,
    ctx,
    db,  # noqa: F401 - fixture
    engine,  # noqa: F401 - fixture
    head,
    layer,
    orders,
    service,
    sync,
    versions,
)


# ── a save is a version ──────────────────────────────────────────────────
async def test_the_first_save_is_version_one(db) -> None:  # noqa: F811
    conn = connection(db)
    published = await service(db).save(conn, layer(), base_revision=0, ctx=ctx())

    [v1] = versions(db, conn)
    assert (v1.version, v1.parent_version, v1.published_by) == (1, None, AUTHOR)
    row = head(db, conn)
    assert row is not None
    assert (row.revision, row.published_version) == (1, 1)
    assert (d.ENTITY_ADDED, "public.orders", "", False) in change_rows(db, v1)
    assert (d.METRIC_ADDED, "public.orders", "revenue", True) in change_rows(db, v1)
    assert published.version is v1
    assert_head_is_its_published_version(db, conn)


async def test_a_save_writes_the_next_version_with_its_typed_changes(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())

    edited = layer()
    edited.entities[0].metrics[0].filters.append("status <> 'REFUNDED'")
    await svc.save(
        conn, edited, base_revision=1, ctx=ctx(OTHER),
        note="Refunds are not revenue either.",
    )

    v1, v2 = versions(db, conn)
    assert (v2.version, v2.parent_version, v2.published_by) == (2, 1, OTHER)
    assert v2.note == "Refunds are not revenue either."
    assert v2.origin == {}
    assert change_rows(db, v2) == [
        (d.METRIC_FILTERS_CHANGED, "public.orders", "revenue", True)
    ]
    row = head(db, conn)
    assert row is not None and (row.revision, row.published_version) == (2, 2)
    # v1 is untouched: versions are immutable.
    assert v1.document["entities"][0]["metrics"][0]["filters"] == ["status <> 'CANCELLED'"]
    assert_head_is_its_published_version(db, conn)


async def test_a_save_is_audited_with_ids_and_counts_only(db) -> None:  # noqa: F811
    conn = connection(db)
    await service(db).save(conn, layer(), base_revision=0, ctx=ctx())

    [row] = audit_rows(db, SEMANTIC_SAVED)
    assert row.resource_type == audit.SEMANTIC_LAYER == "semantic_layer"
    assert row.resource_id == conn.id
    assert row.detail == {
        "version": 1, "entities": 2, "metrics": 1, "issues": 0,
        "changes": len(change_rows(db, versions(db, conn)[0])), "affects_sql": True,
    }
    # Rule 3: no expression and no entry name reaches the log.
    assert "SUM" not in str(row.detail) and "revenue" not in str(row.detail)


async def test_an_empty_save_is_refused_and_writes_nothing(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())

    with pytest.raises(SemanticNoChangesError, match="Nothing changed"):
        await svc.save(conn, layer(), base_revision=1, ctx=ctx())
    assert len(versions(db, conn)) == 1
    row = head(db, conn)
    assert row is not None and row.revision == 1


async def test_a_note_longer_than_the_cap_is_refused(db) -> None:  # noqa: F811
    conn = connection(db)
    with pytest.raises(ValidationError):
        await service(db).save(conn, layer(), base_revision=0, ctx=ctx(), note="x" * 2_001)
    assert versions(db, conn) == []


async def test_a_run_records_the_version_it_loaded(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    edited = layer()
    edited.entities[0].grain = "one row per order line"
    await svc.save(conn, edited, base_revision=1, ctx=ctx())

    snapshot = await svc._snapshot(conn.id)
    loaded = await load_layer(db, conn, snapshot=snapshot)
    assert loaded.version == 2 and loaded.document is not None

    # Switched off, nothing reached the prompt: version 0, not the head's 2.
    conn.semantic_layer_enabled = False
    assert (await load_layer(db, conn, snapshot=snapshot)).version == 0


# ── restore ──────────────────────────────────────────────────────────────
async def test_restoring_a_version_whose_table_was_dropped_brings_it_back_flagged(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    without = layer()
    without.entities.pop(1)
    await svc.save(conn, without, base_revision=1, ctx=ctx())

    # `customers` is dropped by a re-sync; then somebody restores v1.
    sync(db, conn, orders("id", "amount", "status"))
    await svc.restore(conn, 1, base_revision=2, ctx=ctx(OTHER), note="Undo.")

    v3 = versions(db, conn)[-1]
    assert (v3.version, v3.parent_version, v3.origin) == (3, 2, {"restored_from": 1})
    restored = SemanticDocument.model_validate(v3.document)
    customers_entity = restored.entity("public.customers")
    assert customers_entity is not None, "flag, don't drop"
    assert not customers_entity.valid
    assert "not in the current schema snapshot" in customers_entity.issue
    assert [r.detail for r in audit_rows(db, SEMANTIC_RESTORED)] == [
        {"version": 3, "restored_from": 1}
    ]
    assert_head_is_its_published_version(db, conn)


async def test_restoring_the_published_version_is_nothing_to_do(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    with pytest.raises(SemanticNoChangesError):
        await svc.restore(conn, 1, base_revision=1, ctx=ctx())


async def test_restoring_a_version_that_does_not_exist_is_a_404(db) -> None:  # noqa: F811
    conn = connection(db)
    with pytest.raises(NotFoundError):
        await service(db).restore(conn, 7, base_revision=0, ctx=ctx())


# ── delete is a tombstone ────────────────────────────────────────────────
async def test_delete_publishes_an_empty_version_and_keeps_history(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    await svc.delete(conn, ctx=ctx())

    v1, v2 = versions(db, conn)
    assert (v2.version, v2.origin, v2.document) == (2, {"deleted": True}, {})
    assert (d.ENTITY_REMOVED, "public.orders", "", False) in change_rows(db, v2)
    row = head(db, conn)
    assert row is not None and row.document == {} and row.revision == 2
    assert [r.detail for r in audit_rows(db, SEMANTIC_DELETED)] == [{"version": 2}]
    assert_head_is_its_published_version(db, conn)

    # Nothing reaches a run, exactly as if there had never been a layer…
    snapshot = await svc._snapshot(conn.id)
    assert await load_document(db, conn, snapshot=snapshot) is None
    assert (await load_layer(db, conn, snapshot=snapshot)).version == 0
    # …and it can be undone.
    await svc.restore(conn, 1, base_revision=2, ctx=ctx())
    assert await load_document(db, conn, snapshot=snapshot) is not None


async def test_deleting_an_empty_layer_writes_nothing(db) -> None:  # noqa: F811
    conn = connection(db)
    assert await service(db).delete(conn, ctx=ctx()) is None
    assert versions(db, conn) == []


# ── reading the history ──────────────────────────────────────────────────
async def test_one_metrics_history_lists_its_changes_newest_first(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    grain = layer()
    grain.entities[0].grain = "one row per order line"
    await svc.save(conn, grain, base_revision=1, ctx=ctx())
    filters = grain.model_copy(deep=True)
    filters.entities[0].metrics[0].filters = []
    await svc.save(conn, filters, base_revision=2, ctx=ctx(OTHER), note="Count cancelled too.")

    entries = await svc.history(conn.id, entity_key="public.orders", item_key="revenue")
    assert [(e.version.version, e.change.kind, e.author) for e in entries] == [
        (3, d.METRIC_FILTERS_CHANGED, "Ali Rezaei"),
        (1, d.METRIC_ADDED, "Sara Karimi"),
    ]
    assert entries[0].version.note == "Count cancelled too."

    table = await svc.history(conn.id, entity_key="PUBLIC.ORDERS")
    assert {e.version.version for e in table} == {1, 2, 3}


async def test_the_version_list_counts_kinds_and_pages(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    current = layer()
    for revision in range(1, 4):
        current = current.model_copy(deep=True)
        current.entities[0].columns.append(SemanticColumn(name="status", label=f"s{revision}"))
        current.entities[0].columns = current.entities[0].columns[:1] + current.entities[0].columns[-1:]
        await svc.save(conn, current, base_revision=revision, ctx=ctx())

    page = await svc.versions(conn.id, limit=2)
    assert [v.row.version for v in page] == [4, 3]
    assert page[0].kinds == {d.COLUMN_DESCRIBED: 1}
    assert page[0].author == "Sara Karimi"
    rest = await svc.versions(conn.id, limit=2, before=3)
    assert [v.row.version for v in rest] == [2, 1]


async def test_a_versions_changes_are_recomputed_with_their_detail(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    edited = layer()
    edited.entities[0].grain = "one row per order line"
    await svc.save(conn, edited, base_revision=1, ctx=ctx())

    _, against, changes = await svc.changes(conn.id, 2)
    assert against == 1
    [change] = changes
    assert change.before == {"grain": "one row per order"}
    assert change.after == {"grain": "one row per order line"}
    # Against an explicit older version, and against nothing for v1.
    _, against, first = await svc.changes(conn.id, 1)
    assert against is None and first[0].kind == d.ENTITY_ADDED


async def test_diff_binds_both_sides_before_comparing(db) -> None:  # noqa: F811
    conn = connection(db)
    before = layer().model_dump(mode="json")
    after = layer().model_dump(mode="json")
    # An unqualified name the binder resolves is not an edit anybody made.
    after["entities"][1]["table"] = "customers"
    assert await service(db).diff(conn, before, after) == []


# ── one writer: the head equals its version on every path ────────────────
async def test_the_head_matches_its_published_version_after_every_write_path(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    assert_head_is_its_published_version(db, conn)
    edited = layer()
    edited.entities[1].label = "Buyers"
    await svc.save(conn, edited, base_revision=1, ctx=ctx())
    assert_head_is_its_published_version(db, conn)
    await svc.restore(conn, 1, base_revision=2, ctx=ctx())
    assert_head_is_its_published_version(db, conn)
    await svc.delete(conn, ctx=ctx())
    assert_head_is_its_published_version(db, conn)
    # The generation path is held to the same rule in test_semantic_concurrency.


async def test_change_rows_are_the_index_and_not_a_second_copy(db) -> None:  # noqa: F811
    conn = connection(db)
    await service(db).save(conn, layer(), base_revision=0, ctx=ctx())
    columns = set(SemanticLayerChangeRow.__table__.c.keys())
    assert columns == {
        "id", "version_id", "connection_id", "kind", "entity_key", "item_key", "affects_sql",
    }


# ── the migration ────────────────────────────────────────────────────────
def test_the_migration_and_the_service_hash_the_same_bytes() -> None:
    migration = importlib.import_module(
        "app.infra.db.migrations.versions.0032_semantic_versions"
    )
    document = layer().model_dump(mode="json")
    document["business_context"] = "دفتر سفارش‌ها"  # non-ASCII is hashed as written
    assert migration.canonical_sha256(document) == document_sha256(document)
    assert migration.canonical_sha256({}) == document_sha256({})


def test_the_backfill_invents_no_author_and_no_changes() -> None:
    """Read off the migration's own SQL: `published_by` is NULL, the note says
    nothing before it was kept, and no `semantic_layer_changes` row is written
    for a version nobody saw being made. Rehearsed against a clone of the demo
    database on Postgres 16 when this landed (one layer → one v1, revision 1)."""
    migration = importlib.import_module(
        "app.infra.db.migrations.versions.0032_semantic_versions"
    )
    source = inspect.getsource(migration._backfill)
    assert ":schema_version, NULL, :note" in source
    assert "semantic_layer_changes" not in source
    assert '{"migrated": True}' in source
    assert migration.MIGRATED_NOTE.startswith("Recorded when versioning was introduced.")


# ── every route resolves to one cell ─────────────────────────────────────
ROUTES = {
    "get_semantic_layer": Privilege.SELECT,
    "save_semantic_layer": Privilege.MODIFY,
    "delete_semantic_layer": Privilege.DELETE,
    "diff_semantic_documents": Privilege.SELECT,
    "list_semantic_versions": Privilege.SELECT,
    "get_semantic_version": Privilege.SELECT,
    "get_semantic_version_changes": Privilege.SELECT,
    "restore_semantic_version": Privilege.MODIFY,
    "get_semantic_history": Privilege.SELECT,
}


@pytest.mark.parametrize(("name", "privilege"), sorted(ROUTES.items()))
def test_each_layer_route_asks_exactly_one_privilege_on_the_layer(
    name: str, privilege: Privilege
) -> None:
    source = inspect.getsource(getattr(semantic_routes, name))
    asked = [p for p in Privilege if f"Privilege.{p.name}" in source]
    assert asked == [privilege], f"{name} asks {asked}"
    # …through `_authorized`, which names `semantic_layer`, never `connection`.
    assert "_authorized(db, authz, connection_id, ctx," in source


def test_the_version_routes_are_published() -> None:
    from app.main import create_app
    from tests.unit.test_authz_conformance import _routes

    paths = {
        (method, route.path)
        for route in _routes(create_app())
        for method in route.methods or set()
    }
    base = "/connections/{connection_id}/semantic"
    for method, tail in [
        ("POST", "/diff"), ("GET", "/versions"), ("GET", "/versions/{number}"),
        ("GET", "/versions/{number}/changes"), ("POST", "/versions/{number}/restore"),
        ("GET", "/history"),
    ]:
        assert (method, base + tail) in paths, (method, tail)


def test_versions_go_with_their_connection_and_outlive_their_author() -> None:
    from app.infra.db.models import SemanticLayerVersionRow

    fks = {
        fk.column.table.name: fk.ondelete
        for fk in SemanticLayerVersionRow.__table__.foreign_keys
    }
    assert fks == {"database_connections": "CASCADE", "users": "SET NULL"}
    assert sa.inspect(SemanticLayerVersionRow).columns["published_by"].nullable
