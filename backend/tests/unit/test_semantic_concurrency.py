"""Nothing overwrites anything silently.

`docs/plans/semantic-layer-model.md` §1.2.5. `execute_job` read the layer when a
generation **started**, spent minutes at the provider, and wrote
`merge_documents(existing, generated)` over the row. The editor does not disable
Save while a job runs and the API did not refuse the save, so:

* a save made **during** a generation was overwritten when the job committed;
* an editor holding unsaved changes when the job ended kept its old document,
  and its next Save overwrote what the job wrote.

Neither left a trace — and since access control a layer can be granted to a
team, so two people in one layer is normal. Two fixes, one per direction: the
job merges into the **current** row under a lock, and every person's write
carries the revision it read and is refused (409) when that is not current.

Since Phase 2 both the person and the job write the **draft**: "the current
row" is the draft when there is one, and nothing either writes reaches a
question until somebody publishes.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest

from app.api.schemas import SemanticSaveRequest
from app.api.v1 import semantic as semantic_routes
from app.core.errors import SemanticBaseRevisionRequiredError, SemanticConflictError
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.infra.db.models import SemanticJobRow
from app.semantic import GenerationStats, SemanticDocument, SemanticEntity
from app.services import audit, semantic_service
from app.services.semantic_service import (
    SEMANTIC_CONFLICT,
    SEMANTIC_GENERATION_SAVED,
)
from tests.unit.semantic_world import (
    AUTHOR,
    OTHER,
    assert_head_is_its_published_version,
    audit_rows,
    connection,
    ctx,
    customers,
    db,  # noqa: F401 - fixture
    engine,  # noqa: F401 - fixture
    head,
    layer,
    llm_config,
    orders,
    service,
    sync,
    versions,
)


# ── a person's write against a stale revision ────────────────────────────
async def test_a_stale_base_revision_is_refused_and_writes_nothing(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())

    edited = layer()
    edited.entities[1].label = "Buyers"
    with pytest.raises(SemanticConflictError) as refused:
        await svc.save(conn, edited, base_revision=0, ctx=ctx(OTHER))

    assert refused.value.code == "E_SEMANTIC_CONFLICT"
    assert refused.value.http_status == 409
    detail = refused.value.detail
    assert (detail["revision"], detail["published_version"]) == (1, 1)
    assert detail["updated_by"] == str(AUTHOR)
    assert detail["updated_by_name"] == "Sara Karimi"
    assert detail["updated_at"]
    assert len(versions(db, conn)) == 1
    row = head(db, conn)
    assert row is not None and row.revision == 1


async def test_a_conflict_writes_its_audit_row(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    with pytest.raises(SemanticConflictError):
        await svc.save(conn, layer(), base_revision=0, ctx=ctx(OTHER))

    [row] = audit_rows(db, SEMANTIC_CONFLICT)
    assert row.outcome == audit.FAILED
    assert row.actor_user_id == OTHER
    assert row.detail == {"base_revision": 0, "revision": 1}


async def test_the_route_returns_the_409_so_the_audit_row_commits(db) -> None:  # noqa: F811
    """Raised, the refusal would roll the request's transaction back in `get_db`
    and take its audit row with it. The route returns it instead."""
    conn = connection(db)
    await service(db).save(conn, layer(), base_revision=0, ctx=ctx())

    response = await semantic_routes.save_semantic_layer(
        conn.id,
        SemanticSaveRequest(document=layer().model_dump(mode="json"), base_revision=0),
        ctx=ctx(), db=db, settings=semantic_service.Settings(), authz=OwnerOnlyAuthorizer(),
    )
    assert response.status_code == 409  # type: ignore[union-attr]
    body = json.loads(response.body)  # type: ignore[union-attr]
    assert body["code"] == "E_SEMANTIC_CONFLICT"
    assert (body["revision"], body["published_version"]) == (1, 1)
    assert len(audit_rows(db, SEMANTIC_CONFLICT)) == 1


async def test_a_put_without_a_base_revision_is_refused_not_treated_as_overwrite(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    with pytest.raises(SemanticBaseRevisionRequiredError) as refused:
        await semantic_routes.save_semantic_layer(
            conn.id,
            SemanticSaveRequest(document=layer().model_dump(mode="json")),
            ctx=ctx(), db=db, settings=semantic_service.Settings(),
            authz=OwnerOnlyAuthorizer(),
        )
    assert refused.value.code == "E_SEMANTIC_BASE_REVISION_REQUIRED"
    assert refused.value.http_status == 422
    assert versions(db, conn) == []


async def test_a_restore_against_a_stale_revision_is_refused_too(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    edited = layer()
    edited.entities[1].label = "Buyers"
    await svc.save(conn, edited, base_revision=1, ctx=ctx())
    with pytest.raises(SemanticConflictError):
        await svc.restore(conn, 1, base_revision=1, ctx=ctx(OTHER))
    assert len(versions(db, conn)) == 2


# ── a generation and a save, at the same time ────────────────────────────
class BlockingGeneration:
    """`generate_document`, holding at the provider until the test lets go."""

    def __init__(self, generated: SemanticDocument) -> None:
        self.generated = generated
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, **_kwargs: Any) -> tuple[SemanticDocument, GenerationStats]:
        self.entered.set()
        await self.release.wait()
        return self.generated, GenerationStats(tables_described=len(self.generated.entities))


def _job(db, conn, config, *, mode: str = "MERGE") -> SemanticJobRow:  # noqa: F811
    job = SemanticJobRow(
        id=uuid4(), connection_id=conn.id, owner_id=AUTHOR, actor_id=OTHER,
        llm_config_id=config.id, mode=mode, only_tables=[], status="QUEUED",
    )
    db.add(job)
    db._session.flush()
    return job


def _wire(monkeypatch: pytest.MonkeyPatch, generation: BlockingGeneration) -> None:
    class _Llm:
        def snapshot(self) -> dict[str, Any]:
            return {"provider": "openai", "model": "gpt-4o-mini"}

    class _Gateways:
        @staticmethod
        def from_settings(_settings: Any) -> object:
            return object()

    monkeypatch.setattr(semantic_service, "generate_document", generation)
    monkeypatch.setattr(semantic_service, "resolve_llm", lambda *_a, **_k: _Llm())
    monkeypatch.setattr(semantic_service, "LiteLLMGateway", _Gateways)


async def test_a_save_made_during_a_generation_survives_the_jobs_commit(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The first direction of §1.2.5, and the phase's sharpest test.

    A curator edits `orders` in the draft while a generation is at the
    provider; the generation then lands with a description of a table nobody
    had described. Both must be in the draft afterwards — and neither in what
    a question reads until it is published.
    """
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())

    generated = SemanticDocument(entities=[
        SemanticEntity(table="public.orders", label="Orders (generated)"),
        SemanticEntity(table="public.customers", label="Customers (generated)"),
    ])
    generation = BlockingGeneration(generated)
    _wire(monkeypatch, generation)
    job = _job(db, conn, llm_config(db))

    running = asyncio.create_task(svc.execute_job(job.id, asyncio.Event()))
    await asyncio.wait_for(generation.entered.wait(), timeout=5)

    # Mid-generation: a person saves an edit to `orders` into the draft.
    edited = layer()
    edited.entities[0].grain = "one row per order line"
    edited.entities[0].provenance.edited = True
    await service(db).save_draft(conn, edited, base_revision=1, ctx=ctx())

    generation.release.set()
    await asyncio.wait_for(running, timeout=5)

    row = head(db, conn)
    assert row is not None and row.draft_document is not None
    draft = SemanticDocument.model_validate(row.draft_document)
    orders_entity = draft.entity("public.orders")
    customers_entity = draft.entity("public.customers")
    assert orders_entity is not None and customers_entity is not None
    assert orders_entity.grain == "one row per order line", "the save survived the job"
    assert customers_entity.label == "Customers (generated)", "and the job landed"
    assert row.draft_origin == {"generated_job_ids": [str(job.id)]}
    assert row.revision == 3
    assert [r.detail for r in audit_rows(db, SEMANTIC_GENERATION_SAVED)] == [
        {"job_id": str(job.id), "revision": 3, "delegated": True}
    ]
    assert row.prompt_version and row.generated_at is not None

    # Neither the save nor the job reached what a question reads…
    [v1] = versions(db, conn)
    assert_head_is_its_published_version(db, conn)
    # …until it is published, when the version carries the job it came from.
    await service(db).publish(conn, base_revision=3, ctx=ctx(), note="Reviewed.")
    _, v2 = versions(db, conn)
    assert v2.origin == {"generated_job_ids": [str(job.id)]}
    assert (v2.parent_version, v2.published_by) == (1, AUTHOR)
    final = SemanticDocument.model_validate(head(db, conn).document)  # type: ignore[union-attr]
    assert final.entity("public.orders").grain == "one row per order line"  # type: ignore[union-attr]
    assert_head_is_its_published_version(db, conn)


async def test_an_editor_holding_the_pre_generation_revision_gets_a_conflict(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The second direction: the job moved the revision, so an editor that read
    the layer before it cannot save over what the job wrote to the draft."""
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    generation = BlockingGeneration(SemanticDocument(entities=[
        SemanticEntity(table="public.customers", label="Customers (generated)"),
    ]))
    generation.release.set()
    _wire(monkeypatch, generation)

    await svc.execute_job(_job(db, conn, llm_config(db)).id, asyncio.Event())

    stale = layer()
    stale.entities[0].label = "My orders"
    with pytest.raises(SemanticConflictError):
        await svc.save_draft(conn, stale, base_revision=1, ctx=ctx())
    with pytest.raises(SemanticConflictError):
        await svc.save(conn, stale, base_revision=1, ctx=ctx())
    customers_entity = SemanticDocument.model_validate(
        head(db, conn).draft_document  # type: ignore[union-attr]
    ).entity("public.customers")
    assert customers_entity is not None and customers_entity.label == "Customers (generated)"


async def test_a_generation_that_changes_nothing_writes_no_version(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    human = layer()
    for entity in human.entities:
        entity.provenance.edited = True
    await svc.save(conn, human, base_revision=0, ctx=ctx())

    # Every entity is a person's, so the merge keeps them all.
    generation = BlockingGeneration(layer())
    generation.release.set()
    _wire(monkeypatch, generation)
    await svc.execute_job(_job(db, conn, llm_config(db)).id, asyncio.Event())

    # No second version and no revision bump — an open editor is not refused
    # for a write that changed nothing it could see.
    assert len(versions(db, conn)) == 1
    row = head(db, conn)
    assert row is not None and row.revision == 1 and row.generated_at is not None


async def test_the_first_generation_on_a_new_connection_is_a_draft_not_a_version(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """§12 open question 1, decided: a first generation does not publish itself.

    A model's guess about a schema is exactly what a draft puts in front of a
    person. It is the one place the new flow is slower than the old one."""
    conn = connection(db)
    sync(db, conn, orders("id", "amount", "status"), customers())
    generation = BlockingGeneration(SemanticDocument(entities=[
        SemanticEntity(table="public.orders", label="Orders"),
    ]))
    generation.release.set()
    _wire(monkeypatch, generation)
    await service(db).execute_job(_job(db, conn, llm_config(db)).id, asyncio.Event())

    assert versions(db, conn) == []
    row = head(db, conn)
    assert row is not None
    assert (row.revision, row.published_version, row.document) == (1, None, {})
    assert row.draft_updated_by == OTHER

    await service(db).publish(conn, base_revision=1, ctx=ctx())
    [v1] = versions(db, conn)
    assert v1.version == 1 and v1.published_by == AUTHOR
    assert_head_is_its_published_version(db, conn)
