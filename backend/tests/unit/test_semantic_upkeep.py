"""Upkeep: regenerating chosen tables, and what needs attention.

Phase 5 of `docs/plans/semantic-layer-model.md`. Two halves:

* **A generation over chosen tables** changes those tables and nothing else,
  in either of the editor's modes — *Fill gaps* (`FILL_GAPS`), which never
  overwrites a field a person wrote, and *Rewrite* (`REPLACE`). Both land in the
  draft. The merge rules themselves are in `test_semantic_validate.py`; these
  run them through the job, against the real write path.
* ***Needs attention*** — six reasons, each built from something already
  stored. The rules are pure (`app/semantic/attention.py`) and tested against a
  fixture here; the facts are gathered by the service, and those tests run the
  real statements against the SQLite world.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.api.v1 import semantic as semantic_routes
from app.core.clock import utcnow
from app.core.errors import ValidationError
from app.domain.value_objects.authz import Privilege
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.infra.db.models import (
    Conversation,
    GeneratedQuery,
    KnowledgeTemplateHit,
    Message,
    Run,
    SchemaSnapshotRow,
    SemanticJobRow,
    SemanticLayerChangeRow,
)
from app.semantic import (
    COLUMNS_CHANGED,
    DRAFT_OLD,
    INVALID,
    METRIC_IGNORED,
    UNDESCRIBED,
    UNREVIEWED_RELIED_ON,
    Baseline,
    GlossaryTerm,
    MetricCount,
    Provenance,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    column_drift,
    needs_attention,
)
from app.services.semantic_service import SemanticService
from tests.unit.semantic_world import (
    AUTHOR,
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
from tests.unit.test_semantic_concurrency import BlockingGeneration, _wire


# ── a generation over chosen tables ──────────────────────────────────────
def _job(db, conn, *, mode: str, tables: list[str]) -> SemanticJobRow:  # noqa: F811
    job = SemanticJobRow(
        id=uuid4(), connection_id=conn.id, owner_id=AUTHOR, actor_id=AUTHOR,
        llm_config_id=llm_config(db).id, mode=mode, only_tables=tables, status="QUEUED",
    )
    db.add(job)
    db._session.flush()
    return job


def _curated() -> SemanticDocument:
    """A published layer a person wrote: an edited `orders`, a glossary, a context."""
    doc = layer()
    doc.business_context = "An online shop's order book."
    doc.context_provenance = Provenance(source="human", edited=True)
    doc.glossary = [GlossaryTerm(term="AOV", meaning="Average order value.")]
    written = doc.entities[0]
    written.provenance = Provenance(source="human", edited=True, reviewed=True)
    return doc


def _generated() -> SemanticDocument:
    """What the model wrote over the whole schema, disagreeing with everything."""
    return SemanticDocument(
        business_context="A context written from one table.",
        glossary=[GlossaryTerm(term="Basket", meaning="Generated.")],
        entities=[
            SemanticEntity(
                table="public.orders", label="Generated", description="Every order.",
                grain="generated grain",
                columns=[
                    SemanticColumn(name="amount", label="Generated", description="Value."),
                    SemanticColumn(name="discount", description="Money taken off."),
                ],
                metrics=[
                    SemanticMetric(name="revenue", expression="SUM(amount)"),
                    SemanticMetric(name="discounts", expression="SUM(discount)"),
                ],
            ),
            SemanticEntity(table="public.customers", label="Customers (generated)"),
        ],
    )


async def _run_job(db, monkeypatch, conn, *, mode: str, tables: list[str]) -> SemanticDocument:  # noqa: F811
    generation = BlockingGeneration(_generated())
    generation.release.set()
    _wire(monkeypatch, generation)
    await service(db).execute_job(_job(db, conn, mode=mode, tables=tables).id, asyncio.Event())
    row = head(db, conn)
    assert row is not None and row.draft_document is not None
    return SemanticDocument.model_validate(row.draft_document)


async def test_fill_gaps_over_a_grown_table_fills_it_and_touches_nothing_else(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """The case drift detection surfaces and, before this, nothing could act on:
    `orders` gained a column after a person described it."""
    conn = connection(db)
    await service(db).save(conn, _curated(), base_revision=0, ctx=ctx())
    sync(db, conn, orders("id", "amount", "status", "discount"), customers())

    draft = await _run_job(db, monkeypatch, conn, mode="FILL_GAPS", tables=["public.orders"])

    mine = draft.entity("public.orders")
    assert mine is not None
    assert (mine.label, mine.grain) == ("Orders", "one row per order"), "written fields kept"
    assert mine.description == "Every order.", "an empty field filled"
    assert [(c.name, c.label) for c in mine.columns] == [
        ("amount", "Order value"), ("discount", ""),
    ]
    [revenue, discounts] = mine.metrics
    assert (revenue.expression, revenue.filters) == ("SUM(amount)", ["status <> 'CANCELLED'"])
    assert discounts.name == "discounts" and discounts.valid
    assert mine.provenance.reviewed and mine.provenance.edited

    # Nothing the person did not choose moved.
    other = draft.entity("public.customers")
    assert other is not None and other.label == "Customers"
    assert draft.business_context == "An online shop's order book."
    assert [g.term for g in draft.glossary] == ["AOV", "Basket"]
    # …and nothing reached a question: one version, as before.
    assert len(versions(db, conn)) == 1


async def test_rewrite_over_a_chosen_table_replaces_that_entity_only(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    conn = connection(db)
    await service(db).save(conn, _curated(), base_revision=0, ctx=ctx())

    draft = await _run_job(db, monkeypatch, conn, mode="REPLACE", tables=["public.orders"])

    mine = draft.entity("public.orders")
    assert mine is not None and mine.label == "Generated"
    assert [m.name for m in mine.metrics] == ["revenue", "discounts"]
    assert draft.entity("public.customers").label == "Customers"  # type: ignore[union-attr]
    assert draft.business_context == "An online shop's order book."
    assert len(versions(db, conn)) == 1


async def test_a_generation_names_only_tables_the_schema_has_and_a_known_mode(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    config = llm_config(db)
    svc = SemanticService(db, service(db)._settings, OwnerOnlyAuthorizer())
    with pytest.raises(ValidationError, match="not in this connection's schema"):
        await svc.create_job(
            ctx=ctx(), connection=conn, llm_config_id=config.id,
            mode="FILL_GAPS", only_tables=["public.orders", "public.ghost"],
        )
    with pytest.raises(ValidationError, match="mode"):
        await svc.create_job(
            ctx=ctx(), connection=conn, llm_config_id=config.id,
            mode="OVERWRITE", only_tables=[],
        )
    job = await svc.create_job(
        ctx=ctx(), connection=conn, llm_config_id=config.id,
        mode="FILL_GAPS", only_tables=["PUBLIC.ORDERS", "public.orders"],
    )
    assert (job.mode, job.only_tables, job.progress_total) == ("FILL_GAPS", ["public.orders"], 3)


# ── the rules, over a fixture ────────────────────────────────────────────
NOW = utcnow()

BEFORE = [
    {"schema": "public", "name": "orders", "columns": [
        {"name": "id", "data_type": "bigint"},
        {"name": "amount", "data_type": "integer"},
        {"name": "legacy", "data_type": "text"},
    ]},
]
AFTER = [
    {"schema": "public", "name": "orders", "columns": [
        {"name": "id", "data_type": "bigint"},
        {"name": "AMOUNT", "data_type": "numeric"},
        {"name": "discount", "data_type": "numeric"},
    ]},
    {"schema": "public", "name": "customers", "columns": [
        {"name": "id", "data_type": "bigint"},
    ]},
    {"schema": "public", "name": "refunds", "columns": [
        {"name": "id", "data_type": "bigint"}, {"name": "order_id", "data_type": "bigint"},
    ]},
]


def test_column_drift_names_what_was_added_removed_and_retyped() -> None:
    drift = column_drift(BEFORE, AFTER, "PUBLIC.orders")
    assert drift is not None
    assert (drift.added, drift.removed, drift.retyped) == (("discount",), ("legacy",), ("amount",))
    assert not column_drift(AFTER, AFTER, "public.orders")
    # A table missing on either side has no shape to compare.
    assert column_drift(BEFORE, AFTER, "public.customers") is None
    assert column_drift(AFTER, BEFORE, "public.customers") is None


def _fixture() -> SemanticDocument:
    return SemanticDocument(entities=[
        SemanticEntity(
            table="public.orders",
            metrics=[
                SemanticMetric(name="revenue", expression="SUM(amount)"),
                SemanticMetric(name="gone", expression="SUM(legacy)", valid=False,
                               issue="`legacy` is not a column of public.orders."),
            ],
            provenance=Provenance(source="llm"),
        ),
        SemanticEntity(
            table="public.customers",
            provenance=Provenance(source="human", edited=True, reviewed=False),
        ),
        # Set aside: broken, drifted and relied on, and none of it is a reason.
        SemanticEntity(table="public.staging", exclude=True, valid=False, issue="gone"),
    ])


def test_every_reason_is_listed_once_most_urgent_first() -> None:
    items = needs_attention(
        _fixture(),
        tables=AFTER,
        baselines={
            "public.orders": Baseline(version=3, tables=BEFORE),
            "public.staging": Baseline(version=3, tables=BEFORE),
        },
        metric_counts=[
            MetricCount("revenue", "public.orders", used=1, ignored=4),
            MetricCount("gone", "public.orders", used=3, ignored=3),       # a tie is not a gap
            MetricCount("old_metric", "public.orders", used=0, ignored=9),  # no longer defined
        ],
        relied_on={"public.orders": 5, "public.customers": 2, "public.staging": 9},
        draft_updated_at=NOW - timedelta(days=9),
        now=NOW,
        days=30,
    )
    assert [(i.reason, i.table, i.item) for i in items] == [
        (DRAFT_OLD, "", ""),
        (INVALID, "public.orders", ""),
        (COLUMNS_CHANGED, "public.orders", ""),
        (METRIC_IGNORED, "public.orders", "revenue"),
        (UNREVIEWED_RELIED_ON, "public.orders", ""),
        (UNDESCRIBED, "public.refunds", ""),
    ]
    by_reason = {i.reason: i.detail for i in items}
    assert by_reason[DRAFT_OLD] == {"days": 9}
    assert by_reason[INVALID] == {
        "entity": False, "columns": 0, "metrics": 1,
        "issue": "`legacy` is not a column of public.orders.",
    }
    assert by_reason[COLUMNS_CHANGED] == {
        "version": 3, "added": ["discount"], "removed": ["legacy"], "retyped": ["amount"],
    }
    assert by_reason[METRIC_IGNORED] == {"used": 1, "ignored": 4, "days": 30}
    assert by_reason[UNREVIEWED_RELIED_ON] == {"answers": 5, "days": 30}
    assert by_reason[UNDESCRIBED] == {"columns": 2}


def test_a_healthy_layer_needs_nothing() -> None:
    doc = SemanticDocument(entities=[
        SemanticEntity(table=t["schema"] + "." + t["name"], provenance=Provenance(reviewed=True))
        for t in AFTER
    ])
    assert needs_attention(
        doc, tables=AFTER, baselines={"public.orders": Baseline(1, AFTER)},
        metric_counts=[], relied_on={"public.orders": 3},
        draft_updated_at=NOW - timedelta(days=6, hours=23), now=NOW, days=30,
    ) == []


# ── the facts, gathered ──────────────────────────────────────────────────
def _snapshot_at(db, conn, *, days_ago: int) -> None:  # noqa: F811
    """Backdate the newest snapshot, so a draft can be saved after it."""
    latest = db._session.scalar(
        sa.select(SchemaSnapshotRow)
        .where(SchemaSnapshotRow.connection_id == conn.id)
        .order_by(SchemaSnapshotRow.version.desc())
        .limit(1)
    )
    latest.created_at = utcnow() - timedelta(days=days_ago)
    db._session.flush()


async def _reasons(db, conn) -> list[tuple[str, str, str]]:  # noqa: F811
    return [(i.reason, i.table, i.item) for i in await service(db).attention(conn)]


async def test_a_table_that_grew_since_its_entity_last_changed_needs_attention(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    assert await _reasons(db, conn) == []

    sync(db, conn, orders("id", "amount", "status", "discount"), customers())
    [item] = await svc.attention(conn)
    assert (item.reason, item.table) == (COLUMNS_CHANGED, "public.orders")
    assert item.detail == {"version": 1, "added": ["discount"], "removed": [], "retyped": []}

    # A version that changes `orders` after the sync is its new baseline.
    described = layer()
    described.entities[0].columns.append(SemanticColumn(name="discount", label="Discount"))
    await svc.save(conn, described, base_revision=1, ctx=ctx())
    assert await _reasons(db, conn) == []


async def test_an_entity_no_change_row_names_is_compared_with_the_first_version(
    db,  # noqa: F811
) -> None:
    """A migrated version 1 writes no change rows; its entities date from it."""
    conn = connection(db)
    await service(db).save(conn, layer(), base_revision=0, ctx=ctx())
    db._session.execute(sa.delete(SemanticLayerChangeRow))
    db._session.flush()

    sync(db, conn, {**orders("id", "amount", "status"), "columns": [
        {"name": "id", "data_type": "bigint"},
        {"name": "amount", "data_type": "numeric"},
        {"name": "status", "data_type": "text"},
    ]}, customers())
    [item] = await service(db).attention(conn)
    assert item.reason == COLUMNS_CHANGED and item.detail["version"] == 1
    assert item.detail["retyped"] == ["id", "status"]


async def test_a_draft_saved_after_the_sync_is_the_baseline_for_what_it_changes(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    sync(db, conn, orders("id", "amount", "status", "discount"), customers())
    _snapshot_at(db, conn, days_ago=1)

    edited = layer()
    edited.entities[0].label = "Orders, checked after the sync"
    await svc.save_draft(conn, edited, base_revision=1, ctx=ctx())
    assert await _reasons(db, conn) == []

    # A draft older than the sync is not: its edit predates the new column.
    _snapshot_at(db, conn, days_ago=-1)
    assert await _reasons(db, conn) == [(COLUMNS_CHANGED, "public.orders", "")]


async def test_what_the_schema_broke_and_what_is_undescribed_need_attention(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    await service(db).save(conn, layer(), base_revision=0, ctx=ctx())
    sync(db, conn, orders("id", "total_cents"), customers(), {
        "schema": "public", "name": "refunds", "columns": [{"name": "id"}],
    })

    items = await service(db).attention(conn)
    assert [(i.reason, i.table) for i in items] == [
        (INVALID, "public.orders"),
        # Two readings of one re-sync, and both are true: what it broke, and
        # how the table's shape moved under the description.
        (COLUMNS_CHANGED, "public.orders"),
        (UNDESCRIBED, "public.refunds"),
    ]
    assert items[0].detail["metrics"] == 1 and items[0].detail["columns"] == 1
    assert items[1].detail["added"] == ["total_cents"]
    assert items[1].detail["removed"] == ["amount", "status"]


async def test_a_draft_left_for_a_week_needs_attention(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await svc.save(conn, layer(), base_revision=0, ctx=ctx())
    edited = layer()
    edited.entities[0].label = "Orders (draft)"
    await svc.save_draft(conn, edited, base_revision=1, ctx=ctx())
    assert await _reasons(db, conn) == []

    row = head(db, conn)
    assert row is not None
    row.draft_updated_at = utcnow() - timedelta(days=8)
    db._session.flush()
    [item] = await svc.attention(conn)
    assert (item.reason, item.detail) == (DRAFT_OLD, {"days": 8})


def _run(
    db, conn, tables: list[str], *,  # noqa: F811
    version: int | None = 1, status: str = "SUCCEEDED", days_ago: int = 0,
    verified: bool = False, metric_use: dict[str, Any] | None = None,
) -> UUID:
    conversation = Conversation(id=uuid4(), owner_id=AUTHOR, title="t")
    db.add(conversation)
    db._session.flush()
    message = Message(id=uuid4(), conversation_id=conversation.id, seq=1, role="USER", content="q")
    db.add(message)
    db._session.flush()
    run = Run(
        id=uuid4(), conversation_id=conversation.id, user_message_id=message.id,
        owner_id=AUTHOR, connection_id=conn.id, status=status,
        semantic_layer_version=version, created_at=utcnow() - timedelta(days=days_ago),
    )
    db.add(run)
    db._session.flush()
    db.add(GeneratedQuery(
        id=uuid4(), run_id=run.id, attempt_no=1, raw_sql="SELECT 1", dialect="postgres",
        validation_status="VALID", referenced_tables=tables, metric_use=metric_use,
    ))
    if verified:
        db.add(KnowledgeTemplateHit(id=uuid4(), run_id=run.id, outcome="SHORT_CIRCUIT"))
    db._session.flush()
    return run.id


async def test_unreviewed_model_text_that_grounded_answers_stood_on_needs_attention(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    doc = layer()                         # both entities: written by a model, unreviewed
    doc.entities[1].provenance = Provenance(source="llm", reviewed=True)
    await service(db).save(conn, doc, base_revision=0, ctx=ctx())

    _run(db, conn, ["public.orders"])                          # Grounded: counts
    _run(db, conn, ["public.orders", "public.customers"])      # Grounded: counts
    _run(db, conn, ["public.orders", "public.refunds"])        # a table undescribed: Generated
    _run(db, conn, ["public.orders"], verified=True)           # Verified stood on a template
    _run(db, conn, ["public.orders"], version=0)               # no layer reached it
    _run(db, conn, ["public.orders"], status="FAILED")         # no answer
    _run(db, conn, ["public.orders"], days_ago=45)             # outside the window
    _run(db, conn, ["public.orders"], version=None)            # before versions: today's layer

    [item] = await service(db).attention(conn)
    assert (item.reason, item.table, item.detail) == (
        UNREVIEWED_RELIED_ON, "public.orders", {"answers": 3, "days": 30},
    )


async def test_a_definition_answers_keep_leaving_out_needs_attention(db) -> None:  # noqa: F811
    conn = connection(db)
    doc = layer()
    for entity in doc.entities:
        entity.provenance = Provenance(source="human", edited=True, reviewed=True)
    await service(db).save(conn, doc, base_revision=0, ctx=ctx())

    def verdict(value: str) -> dict[str, Any]:
        return {"version": 1, "verdicts": [
            {"metric": "revenue", "entity": "public.orders", "verdict": value},
        ]}

    _run(db, conn, ["public.customers"], metric_use=verdict("used"))
    _run(db, conn, ["public.customers"], metric_use=verdict("ignored"))
    assert await _reasons(db, conn) == []
    _run(db, conn, ["public.customers"], metric_use=verdict("ignored"))
    item = next(i for i in await service(db).attention(conn) if i.reason == METRIC_IGNORED)
    assert (item.table, item.item, item.detail) == (
        "public.orders", "revenue", {"used": 1, "ignored": 2, "days": 30},
    )


def test_the_attention_route_asks_select_on_the_layer() -> None:
    source = inspect.getsource(semantic_routes.get_semantic_attention)
    asked = [p for p in Privilege if f"Privilege.{p.name}" in source]
    assert asked == [Privilege.SELECT]
    assert "_authorized(db, authz, connection_id, ctx," in source


def test_the_attention_route_is_published() -> None:
    from app.main import create_app
    from tests.unit.test_authz_conformance import _routes

    paths = {(m, r.path) for r in _routes(create_app()) for m in r.methods or set()}
    assert ("GET", "/connections/{connection_id}/semantic/attention") in paths
