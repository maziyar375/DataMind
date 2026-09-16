"""An edit stops reaching the model the moment it is saved.

Phase 2 of `docs/plans/semantic-layer-model.md`. Before it, every save was a
version and every version was what the next question read — and a generation
reached every answer the moment its job ended, unreviewed. Now a save, a
generation and a restore write `draft_document`, which **no loader reads**, and
publishing is a separate act that writes the version.

Run against a real schema on SQLite (`semantic_world.py`), because the claims
are about which column a write lands in and which one a reader reads.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import re
from uuid import uuid4

import pytest

from app.api.schemas import SemanticPublishRequest
from app.api.v1 import semantic as semantic_routes
from app.core.errors import (
    SemanticConflictError,
    SemanticNoChangesError,
    SemanticNoteRequiredError,
)
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.infra.db.models import BenchmarkRun, BenchmarkSet
from app.semantic import SemanticDocument
from app.semantic import diff as d
from app.services import semantic_service
from app.services.semantic_service import (
    SEMANTIC_DRAFT_DISCARDED,
    SEMANTIC_DRAFT_SAVED,
    SEMANTIC_PUBLISHED,
    SEMANTIC_RESTORED,
    DraftMovedError,
    load_document,
    load_draft,
    load_layer,
)
from tests.unit.semantic_world import (
    AUTHOR,
    OTHER,
    assert_head_is_its_published_version,
    audit_rows,
    connection,
    ctx,
    db,  # noqa: F401 - fixture
    engine,  # noqa: F401 - fixture
    head,
    layer,
    llm_config,
    service,
    versions,
)

APP = pathlib.Path(semantic_service.__file__).resolve().parents[1]


async def _published(db, conn) -> None:  # noqa: F811
    """v1, published through the one-step save, at revision 1."""
    await service(db).save(conn, layer(), base_revision=0, ctx=ctx())


def _refunds() -> SemanticDocument:
    """`layer()` with a number-changing edit: refunds are not revenue either."""
    edited = layer()
    edited.entities[0].metrics[0].filters.append("status <> 'REFUNDED'")
    return edited


# ── a draft is saved and nothing a question reads moves ──────────────────
async def test_saving_a_draft_moves_the_revision_and_not_the_published_document(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    await _published(db, conn)
    before = dict(head(db, conn).document)  # type: ignore[union-attr]

    written = await service(db).save_draft(conn, _refunds(), base_revision=1, ctx=ctx(OTHER))

    row = head(db, conn)
    assert row is not None
    assert row.document == before, "the published copy did not move"
    assert (row.revision, row.published_version) == (2, 1)
    assert row.draft_document is not None
    assert row.draft_updated_by == OTHER and row.draft_updated_at is not None
    assert len(versions(db, conn)) == 1, "a draft is not a version"
    assert [(c.kind, c.item_key) for c in written.changes] == [
        (d.METRIC_FILTERS_CHANGED, "revenue")
    ]
    assert [r.detail for r in audit_rows(db, SEMANTIC_DRAFT_SAVED)] == [
        {"revision": 2, "changes": 1}
    ]
    assert_head_is_its_published_version(db, conn)


async def test_the_run_path_reads_the_published_document_while_a_draft_differs(
    db,  # noqa: F811
) -> None:
    """The gate's own claim, at the loader every question goes through."""
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())

    snapshot = await svc._snapshot(conn.id)
    loaded = await load_layer(db, conn, snapshot=snapshot)
    assert loaded.version == 1
    assert loaded.document is not None
    assert loaded.document.entities[0].metrics[0].filters == ["status <> 'CANCELLED'"]
    document = await load_document(db, conn, snapshot=snapshot)
    assert document is not None
    assert "status <> 'REFUNDED'" not in json.dumps(document.model_dump(mode="json"))


async def test_the_editor_reads_the_draft_with_its_unpublished_changes(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx(OTHER))

    doc, row, facts = await svc.read(conn)
    assert doc.entities[0].metrics[0].filters == ["status <> 'CANCELLED'", "status <> 'REFUNDED'"]
    assert facts["has_draft"] and facts["published_exists"]
    assert facts["draft_updated_by_name"] == "Ali Rezaei"
    assert [(c.kind, c.affects_sql) for c in facts["unpublished_changes"]] == [
        (d.METRIC_FILTERS_CHANGED, True)
    ]
    assert row is not None and row.revision == 2


async def test_a_draft_equal_to_the_published_document_leaves_no_draft(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    # Typed back to what is published: nothing is unpublished any more.
    written = await svc.save_draft(conn, layer(), base_revision=2, ctx=ctx())

    assert written.changes == []
    row = head(db, conn)
    assert row is not None
    assert row.draft_document is None and row.draft_updated_by is None
    assert row.revision == 3
    _, _, facts = await svc.read(conn)
    assert not facts["has_draft"] and facts["unpublished_changes"] == []


async def test_saving_the_same_draft_twice_is_nothing_to_do(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    with pytest.raises(SemanticNoChangesError):
        await svc.save_draft(conn, _refunds(), base_revision=2, ctx=ctx())
    assert head(db, conn).revision == 2  # type: ignore[union-attr]


async def test_a_draft_on_a_connection_with_no_layer_creates_the_head_unpublished(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    await service(db).save_draft(conn, layer(), base_revision=0, ctx=ctx())
    row = head(db, conn)
    assert row is not None
    assert (row.revision, row.published_version, row.document) == (1, None, {})
    snapshot = await service(db)._snapshot(conn.id)
    assert (await load_layer(db, conn, snapshot=snapshot)).document is None


async def test_a_stale_draft_save_is_a_conflict_naming_the_draft_author(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx(OTHER))

    edited = layer()
    edited.entities[1].label = "Buyers"
    with pytest.raises(SemanticConflictError) as refused:
        await svc.save_draft(conn, edited, base_revision=1, ctx=ctx())
    # The newest write is Ali's draft, not Sara's v1 — so the note names Ali.
    assert refused.value.detail["updated_by_name"] == "Ali Rezaei"
    assert refused.value.detail["updated_by"] == str(OTHER)
    assert refused.value.detail["revision"] == 2


# ── discard ──────────────────────────────────────────────────────────────
async def test_discarding_the_draft_leaves_the_published_document(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())

    await svc.discard_draft(conn, base_revision=2, ctx=ctx())

    row = head(db, conn)
    assert row is not None
    assert row.draft_document is None and row.draft_origin == {}
    assert (row.revision, row.published_version) == (3, 1)
    assert [r.detail for r in audit_rows(db, SEMANTIC_DRAFT_DISCARDED)] == [{"revision": 3}]
    assert_head_is_its_published_version(db, conn)


async def test_discarding_with_no_draft_or_a_stale_revision_is_refused(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    with pytest.raises(SemanticNoChangesError):
        await svc.discard_draft(conn, base_revision=1, ctx=ctx())
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    with pytest.raises(SemanticConflictError):
        await svc.discard_draft(conn, base_revision=1, ctx=ctx(OTHER))
    assert head(db, conn).draft_document is not None  # type: ignore[union-attr]


# ── publish ──────────────────────────────────────────────────────────────
async def test_publishing_writes_the_next_version_and_clears_the_draft(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx(OTHER))

    published = await svc.publish(
        conn, base_revision=2, ctx=ctx(), note="Refunds are not revenue either."
    )

    _, v2 = versions(db, conn)
    assert published.version is v2
    assert (v2.version, v2.parent_version, v2.published_by) == (2, 1, AUTHOR)
    assert v2.note == "Refunds are not revenue either."
    row = head(db, conn)
    assert row is not None
    assert row.draft_document is None and row.draft_updated_by is None
    assert (row.revision, row.published_version) == (3, 2)
    assert [r.detail for r in audit_rows(db, SEMANTIC_PUBLISHED)] == [
        {"version": 2, "changes": 1, "affects_sql": True, "scored_run_id": None}
    ]
    assert_head_is_its_published_version(db, conn)
    snapshot = await svc._snapshot(conn.id)
    loaded = await load_layer(db, conn, snapshot=snapshot)
    assert loaded.version == 2
    assert "status <> 'REFUNDED'" in loaded.document.entities[0].metrics[0].filters  # type: ignore[union-attr]


async def test_a_number_changing_publish_without_a_note_is_refused(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())

    for blank in ("", "   "):
        with pytest.raises(SemanticNoteRequiredError) as refused:
            await svc.publish(conn, base_revision=2, ctx=ctx(), note=blank)
        assert refused.value.code == "E_SEMANTIC_NOTE_REQUIRED"
        assert refused.value.http_status == 422
    assert len(versions(db, conn)) == 1
    assert head(db, conn).draft_document is not None  # type: ignore[union-attr]


async def test_a_wording_only_publish_needs_no_note(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    wording = layer()
    wording.entities[1].label = "Buyers"
    await svc.save_draft(conn, wording, base_revision=1, ctx=ctx())
    await svc.publish(conn, base_revision=2, ctx=ctx())
    assert versions(db, conn)[-1].note == ""


async def test_publishing_with_a_stale_revision_is_a_conflict_and_writes_nothing(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    with pytest.raises(SemanticConflictError):
        await svc.publish(conn, base_revision=1, ctx=ctx(OTHER), note="Stale.")
    assert len(versions(db, conn)) == 1


async def test_the_publish_route_returns_the_409_so_the_audit_row_commits(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    response = await semantic_routes.publish_semantic_draft(
        conn.id, SemanticPublishRequest(base_revision=1, note="x"),
        ctx=ctx(), db=db, settings=semantic_service.Settings(), authz=OwnerOnlyAuthorizer(),
    )
    assert response.status_code == 409  # type: ignore[union-attr]


async def test_an_empty_publish_is_refused(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    with pytest.raises(SemanticNoChangesError, match="no unpublished changes"):
        await svc.publish(conn, base_revision=1, ctx=ctx())
    with pytest.raises(SemanticNoChangesError):
        await svc.publish(connection(db), base_revision=0, ctx=ctx())


async def test_a_publish_names_the_benchmark_run_that_scored_exactly_this_draft(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())

    config = llm_config(db)
    bench = BenchmarkSet(id=uuid4(), connection_id=conn.id, name="core", template_ids=[])
    db.add(bench)
    db._session.flush()
    for revision, status in ((1, "SUCCEEDED"), (2, "FAILED"), (2, "SUCCEEDED")):
        db.add(BenchmarkRun(
            id=uuid4(), set_id=bench.id, connection_id=conn.id, llm_config_id=config.id,
            status=status, semantic_source="DRAFT", semantic_revision=revision,
        ))
    db._session.flush()
    scored = db._session.query(BenchmarkRun).filter_by(
        semantic_revision=2, status="SUCCEEDED"
    ).one()

    await svc.publish(conn, base_revision=2, ctx=ctx(), note="Scored first.")
    [row] = audit_rows(db, SEMANTIC_PUBLISHED)
    assert row.detail["scored_run_id"] == str(scored.id)


# ── what else writes the draft ───────────────────────────────────────────
async def test_a_restore_lands_in_the_draft_and_a_later_edit_keeps_its_origin(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    wording = layer()
    wording.entities[1].label = "Buyers"
    await svc.save(conn, wording, base_revision=1, ctx=ctx())

    await svc.restore(conn, 1, base_revision=2, ctx=ctx(OTHER))
    row = head(db, conn)
    assert row is not None
    assert row.draft_origin == {"restored_from": 1}
    assert row.published_version == 2, "a restore publishes nothing"
    assert [r.detail for r in audit_rows(db, SEMANTIC_RESTORED)] == [
        {"revision": 3, "restored_from": 1}
    ]

    # A person's own edit on top of the restored draft is still that restore.
    on_top = layer()
    on_top.entities[0].label = "Customer orders"
    await svc.save_draft(conn, on_top, base_revision=3, ctx=ctx())
    assert head(db, conn).draft_origin == {"restored_from": 1}  # type: ignore[union-attr]
    await svc.publish(conn, base_revision=4, ctx=ctx())
    assert versions(db, conn)[-1].origin == {"restored_from": 1}


async def test_the_one_step_save_publishes_and_replaces_any_draft(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx(OTHER))

    wording = layer()
    wording.entities[1].label = "Buyers"
    await svc.save(conn, wording, base_revision=2, ctx=ctx())

    row = head(db, conn)
    assert row is not None and row.draft_document is None
    assert row.published_version == 2
    assert "REFUNDED" not in json.dumps(row.document)
    assert_head_is_its_published_version(db, conn)


async def test_deleting_a_layer_that_is_only_a_draft_writes_no_version(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save_draft(conn, layer(), base_revision=0, ctx=ctx())
    assert await svc.delete(conn, ctx=ctx()) is None
    row = head(db, conn)
    assert row is not None and row.draft_document is None and row.revision == 2
    assert versions(db, conn) == []
    assert [r.detail for r in audit_rows(db, SEMANTIC_DRAFT_DISCARDED)] == [{"revision": 2}]


async def test_deleting_a_published_layer_also_drops_its_draft(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    await svc.delete(conn, ctx=ctx())
    row = head(db, conn)
    assert row is not None and row.draft_document is None and row.document == {}
    assert versions(db, conn)[-1].origin == {"deleted": True}


# ── a benchmark run scoring the draft ────────────────────────────────────
async def test_load_draft_reads_the_draft_at_its_pinned_revision_and_no_other(
    db,  # noqa: F811
) -> None:
    conn = connection(db, enabled=False)
    svc = service(db)
    await _published(db, conn)
    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    snapshot = await svc._snapshot(conn.id)

    # Switched off, a question reads no layer — but scoring the draft was asked
    # for explicitly, so the draft is what is read.
    assert (await load_layer(db, conn, snapshot=snapshot)).document is None
    loaded = await load_draft(db, conn, snapshot=snapshot, revision=2)
    assert loaded.version == 1
    assert "status <> 'REFUNDED'" in loaded.document.entities[0].metrics[0].filters  # type: ignore[union-attr]

    await svc.discard_draft(conn, base_revision=2, ctx=ctx())
    with pytest.raises(DraftMovedError, match="Score the draft again"):
        await load_draft(db, conn, snapshot=snapshot, revision=2)
    with pytest.raises(DraftMovedError):
        await load_draft(db, conn, snapshot=snapshot, revision=3)


# ── no loader reads a draft ──────────────────────────────────────────────
#: Where `draft_document` may be named, and why. A new file here is a new
#: reader of unpublished content and needs a reason written down.
DRAFT_READERS = {
    "infra/db/models.py": "the column",
    "infra/db/migrations/versions/0033_semantic_drafts.py": "the migration",
    "services/semantic_service.py": "the editor's read, the writers, and load_draft",
    "services/benchmark_service.py": "refusing to queue a DRAFT run with no draft",
    "api/v1/semantic.py": "the editor payload's `exists`",
}

#: Functions in `semantic_service` that may touch a draft. Every other function
#: in the module — `load_layer` and `load_document` above all — must not.
DRAFT_FUNCTIONS = {
    "SemanticService.read", "SemanticService.delete", "SemanticService.discard_draft",
    "SemanticService.publish", "SemanticService._persist_generated",
    "_require_revision", "_write_draft", "_working", "_clear_draft", "load_draft",
}


def test_only_the_named_modules_mention_the_draft_column() -> None:
    mentions = {
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if "draft_document" in path.read_text(encoding="utf-8")
    }
    assert mentions == set(DRAFT_READERS), mentions ^ set(DRAFT_READERS)


def test_no_loader_in_the_semantic_service_reads_the_draft() -> None:
    source = inspect.getsource(semantic_service)
    touching: set[str] = set()
    for name, member in inspect.getmembers(semantic_service):
        if (
            inspect.isfunction(member)
            and member.__module__ == semantic_service.__name__
            and "draft_document" in inspect.getsource(member)
        ):
            touching.add(name)
    for name, member in inspect.getmembers(semantic_service.SemanticService):
        if inspect.isfunction(member) and "draft_document" in inspect.getsource(member):
            touching.add(f"SemanticService.{name}")
    assert touching <= DRAFT_FUNCTIONS, touching - DRAFT_FUNCTIONS
    for loader in ("load_layer", "load_document"):
        assert "draft" not in inspect.getsource(getattr(semantic_service, loader))
    assert "draft_document" in source  # the scan above is scanning something


@pytest.mark.parametrize(
    "module",
    ["services/run_service.py", "services/sql_draft_service.py",
     "services/report_service.py", "workers/benchmark.py"],
)
def test_every_answering_surface_loads_the_layer_through_the_published_loaders(
    module: str,
) -> None:
    """A chat run, a SQL draft, a report and a benchmark: each reads the layer
    through `load_layer` or `load_document`, and none reads the column itself.
    The benchmark's `load_draft` is behind `semantic_source == DRAFT`."""
    text = (APP / module).read_text(encoding="utf-8")
    assert re.search(r"\bload_(layer|document)\(", text), module
    assert "draft_document" not in text
    if module == "workers/benchmark.py":
        assert "if run.semantic_source == SEMANTIC_DRAFT:" in text


# ── every new route resolves to one cell ─────────────────────────────────
@pytest.mark.parametrize(
    "name", ["save_semantic_draft", "discard_semantic_draft", "publish_semantic_draft"]
)
def test_each_draft_route_asks_modify_on_the_layer(name: str) -> None:
    from app.domain.value_objects.authz import Privilege

    source = inspect.getsource(getattr(semantic_routes, name))
    asked = [p for p in Privilege if f"Privilege.{p.name}" in source]
    assert asked == [Privilege.MODIFY]
    assert "_authorized(db, authz, connection_id, ctx," in source


def test_the_draft_routes_are_published() -> None:
    from app.main import create_app
    from tests.unit.test_authz_conformance import _routes

    paths = {
        (method, route.path)
        for route in _routes(create_app())
        for method in route.methods or set()
    }
    base = "/connections/{connection_id}/semantic"
    for method, tail in [("PUT", "/draft"), ("DELETE", "/draft"), ("POST", "/publish")]:
        assert (method, base + tail) in paths, (method, tail)


def test_the_privilege_meanings_say_what_select_and_modify_now_cover() -> None:
    from app.domain.value_objects.authz import (
        PRIVILEGE_MEANINGS,
        Privilege,
        ResourceType,
    )

    meanings = PRIVILEGE_MEANINGS[ResourceType.SEMANTIC_LAYER]
    assert "draft" in meanings[Privilege.SELECT] and "history" in meanings[Privilege.SELECT]
    for verb in ("draft", "publish", "restore", "import", "generation"):
        assert verb in meanings[Privilege.MODIFY]


# ── queuing a run that scores the draft ──────────────────────────────────
async def _bench_set(db, conn) -> BenchmarkSet:  # noqa: F811
    row = BenchmarkSet(id=uuid4(), connection_id=conn.id, name="core", template_ids=[])
    db.add(row)
    db._session.flush()
    return row


async def test_a_draft_run_is_pinned_to_the_revision_it_was_queued_at(db) -> None:  # noqa: F811
    from app.core.config import Settings
    from app.core.errors import ValidationError
    from app.services.benchmark_service import BenchmarkService

    conn = connection(db)
    svc = service(db)
    await _published(db, conn)
    bench = await _bench_set(db, conn)
    config = llm_config(db)
    benchmarks = BenchmarkService(db, Settings())  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="no unpublished changes to score"):
        await benchmarks.queue_run(
            conn, bench, actor_id=AUTHOR, llm_config_id=config.id, semantic_source="DRAFT"
        )

    await svc.save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    run = await benchmarks.queue_run(
        conn, bench, actor_id=AUTHOR, llm_config_id=config.id, semantic_source="DRAFT"
    )
    assert (run.semantic_source, run.semantic_revision) == ("DRAFT", 2)


async def test_the_score_strip_lists_published_runs_and_keeps_draft_runs_apart(
    db,  # noqa: F811
) -> None:
    """§12 open question 4, decided: a draft run is not in the history strip."""
    from app.core.config import Settings
    from app.services.benchmark_service import BenchmarkService

    conn = connection(db)
    bench = await _bench_set(db, conn)
    config = llm_config(db)
    for source in ("PUBLISHED", "DRAFT", "PUBLISHED"):
        db.add(BenchmarkRun(
            id=uuid4(), set_id=bench.id, connection_id=conn.id, llm_config_id=config.id,
            status="SUCCEEDED", semantic_source=source,
        ))
    db._session.flush()
    benchmarks = BenchmarkService(db, Settings())  # type: ignore[arg-type]

    assert {r.semantic_source for r in await benchmarks.runs(bench)} == {"PUBLISHED"}
    assert len(await benchmarks.runs(bench)) == 2
    [draft] = await benchmarks.runs(bench, source="DRAFT")
    assert draft.semantic_source == "DRAFT"


class _KnowledgeButNotTheLayer:
    """An authorizer granting everything on `knowledge` and only `select` on the
    semantic layer — a curator of templates who may not edit the layer."""

    async def allowed(self, ctx, ref, privilege):  # noqa: ANN001, ANN201
        from app.domain.ports.authz import Decision
        from app.domain.value_objects.authz import Privilege, ResourceType

        if ref.type == ResourceType.SEMANTIC_LAYER:
            return Decision(privilege in (Privilege.DESCRIBE, Privilege.SELECT))
        return Decision(True)

    async def privileges_on(self, ctx, ref):  # noqa: ANN001, ANN201
        from app.domain.value_objects.authz import Privilege

        return frozenset({Privilege.DESCRIBE, Privilege.SELECT})


async def test_scoring_a_draft_also_needs_modify_on_the_layer(db) -> None:  # noqa: F811
    from app.api.v1 import knowledge as knowledge_routes
    from app.core.errors import ForbiddenError

    conn = connection(db)
    await _published(db, conn)
    await service(db).save_draft(conn, _refunds(), base_revision=1, ctx=ctx())
    bench = await _bench_set(db, conn)

    with pytest.raises(ForbiddenError):
        await knowledge_routes.run_benchmark(
            conn.id, bench.id, request=None, ctx=ctx(), db=db,  # type: ignore[arg-type]
            authz=_KnowledgeButNotTheLayer(), settings=semantic_service.Settings(),
            llm_config_id=llm_config(db).id, semantic_source="DRAFT",
        )
    assert db._session.query(BenchmarkRun).count() == 0
