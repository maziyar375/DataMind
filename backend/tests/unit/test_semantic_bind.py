"""One binder, and every loader goes through it.

`docs/plans/semantic-layer-model.md` §1.2.1, reproduced before a line was
changed: a layer saved against `orders(id, amount, status)`, then the table
re-synced as `orders(id, total_cents)`. The editor bound the document on read
and showed the `revenue` metric red. The run path deserialised the stored JSON,
trusted the `valid` flags from the save, and sent the model

    metric revenue = SUM(amount) WHERE status <> 'CANCELLED'.

The guard still refused any SQL naming `amount`, so this was never a safety
hole. It was a repair loop spent on a definition the product already knew was
broken, and it made *"flagged entries never reach the prompt"* true only of
flags computed before the schema moved.

Two properties, and the second is what licenses the first:

* **a drifted entry is gone from every run-path render**;
* **a layer that has not drifted renders byte-identically** after binding, so
  the only prompts `PROMPT_VERSION` v10 changed are prompts that were wrong.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.infra.db.models import DatabaseConnection, SemanticLayerRow
from app.pipeline.state import RetrievedContext
from app.semantic import (
    GlossaryTerm,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    TimeSemantics,
    bind_layer,
    render_semantic,
)
from app.services.semantic_service import load_document

SAMPLE = HintBudget.from_policy(DisclosurePolicy.SAMPLE)


def _orders(*columns: str) -> dict[str, Any]:
    return {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": name, "data_type": "numeric", "is_primary_key": name == "id"}
            for name in columns
        ],
    }


def _customers() -> dict[str, Any]:
    return {
        "schema": "public",
        "name": "customers",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "region", "data_type": "text"},
        ],
    }


def _snapshot(*tables: dict[str, Any]) -> dict[str, Any]:
    return {"dialect": "postgres", "tables": list(tables), "relationships": []}


#: What the layer was saved against, and what a re-sync turned it into.
SAVED = _snapshot(_orders("id", "amount", "status"))
RESYNCED = _snapshot(_orders("id", "total_cents"))


def _layer() -> SemanticDocument:
    return SemanticDocument(
        entities=[
            SemanticEntity(
                table="public.orders",
                label="Orders",
                grain="one row per order",
                columns=[
                    SemanticColumn(name="amount", description="order value; in USD"),
                ],
                metrics=[
                    SemanticMetric(
                        name="revenue",
                        expression="SUM(amount)",
                        filters=["status <> 'CANCELLED'"],
                    )
                ],
            )
        ]
    )


def _bind(raw: Any, snapshot: dict[str, Any]) -> SemanticDocument:
    return bind_layer(
        raw,
        tables=snapshot["tables"],
        relationships=snapshot["relationships"],
        dialect=snapshot["dialect"],
    )


def _stored() -> dict[str, Any]:
    """The row as `save` wrote it: bound to `SAVED`, flags and all."""
    return _bind(_layer(), SAVED).model_dump(mode="json")


def _run_path(semantic: dict[str, Any] | None, snapshot: dict[str, Any]) -> str:
    """The schema block a run renders, which is where the layer reaches a model."""
    return RetrievedContext(
        dialect="postgres", tables=snapshot["tables"], semantic=semantic
    ).render(DisclosurePolicy.SAMPLE)


# ── the probe, as a test ─────────────────────────────────────────────────
def test_the_stored_flags_say_the_metric_is_fine() -> None:
    # The precondition that made this a bug: nothing about the stored row is
    # wrong *as stored*. It was true when it was written.
    stored = SemanticDocument.model_validate(_stored())
    assert stored.entities[0].metrics[0].valid
    assert stored.issue_count == 0


def test_trusting_the_stored_flags_renders_a_dropped_column() -> None:
    """What `load_document` did before it bound: the defect, pinned.

    Kept as a test so the reason for the binder cannot be argued away later —
    an unbound read of this row really does put a dead definition in front of
    the model.
    """
    block = _run_path(_stored(), RESYNCED)
    assert "SUM(amount)" in block
    assert "order value" in block


def test_a_metric_over_a_dropped_column_is_absent_from_a_bound_render() -> None:
    bound = _bind(_stored(), RESYNCED)
    block = _run_path(bound.model_dump(mode="json"), RESYNCED)

    assert "revenue" not in block
    assert "SUM(amount)" not in block
    assert "order value" not in block
    # And the editor's sentence is what the binder derived, not a copy of it.
    metric = bound.entities[0].metrics[0]
    assert not metric.valid
    assert metric.issue == "`amount` is not a column of public.orders."


def test_a_dropped_table_takes_its_whole_entity_out() -> None:
    bound = _bind(_stored(), _snapshot(_customers()))
    assert not bound.entities[0].valid
    assert "Orders" not in render_semantic(
        bound, tables=["public.customers"], budget=SAMPLE
    )


def test_a_validator_rule_added_after_the_save_applies_on_load() -> None:
    """Binding on load also applies rules the stored flags predate.

    Found on the demo `aurora` layer while measuring the binder: `total_revenue`
    is defined on both `orders` and `order_items`, stored with no issue because
    it was saved before `_refuse_ambiguous_metrics` existed, and the run path
    rendered both definitions — the exact ambiguity the rule was written to
    stop. Bound, both are out.
    """
    doc = SemanticDocument(
        entities=[
            SemanticEntity(
                table="public.orders",
                metrics=[SemanticMetric(name="revenue", expression="SUM(amount)")],
            ),
            SemanticEntity(
                table="public.customers",
                metrics=[SemanticMetric(name="revenue", expression="COUNT(id)")],
            ),
        ]
    )
    # Flags as an older validator would have left them: every metric valid.
    stored = doc.model_dump(mode="json")
    snapshot = _snapshot(_orders("id", "amount"), _customers())

    assert "revenue" in _run_path(stored, snapshot)
    bound = _bind(stored, snapshot)
    assert all(not m.valid for e in bound.entities for m in e.metrics)
    assert "revenue" not in _run_path(bound.model_dump(mode="json"), snapshot)


# ── byte identity when nothing moved ─────────────────────────────────────
def _rich_layer() -> SemanticDocument:
    """Every section the renderer has, so identity is tested where it can fail."""
    return SemanticDocument(
        business_context="An order book for a coffee roaster.",
        default_exclusions="test accounts (customers.region = 'TEST')",
        time=TimeSemantics(fiscal_year_start_month=4, relative_windows="rolling"),
        entities=[
            SemanticEntity(
                table="public.orders",
                label="Orders",
                grain="one row per order",
                role="fact",
                # Not a column: the binder clears it and says so, which is a
                # mutation a second bind must not undo or repeat differently.
                default_time_column="ordered_at",
                synonyms=["purchases"],
                columns=[
                    SemanticColumn(name="status", value_meanings={"C": "cancelled"}),
                    SemanticColumn(name="amount", label="Order value", unit="USD"),
                    SemanticColumn(name="gone", description="dropped long ago"),
                ],
                metrics=[
                    SemanticMetric(
                        name="revenue",
                        label="Net revenue",
                        expression="SUM(amount)",
                        filters=["status <> 'C'"],
                        synonyms=["takings"],
                    ),
                    SemanticMetric(name="broken", expression="SUM(nope)"),
                ],
            ),
            SemanticEntity(
                table="customers",  # unqualified: the binder resolves the suffix
                label="Customers",
                columns=[SemanticColumn(name="region", synonyms=["territory"])],
            ),
            SemanticEntity(table="public.staging_orders", exclude=True),
        ],
        glossary=[GlossaryTerm(term="AOV", meaning="average order value", maps_to=["revenue"])],
    )


FK_SNAPSHOT = {
    "dialect": "postgres",
    "tables": [
        {
            **_orders("id", "amount", "status"),
            "columns": [
                *_orders("id", "amount", "status")["columns"],
                {"name": "customer_id", "data_type": "bigint"},
            ],
        },
        _customers(),
    ],
    "relationships": [
        {"from_table": "public.orders", "from_column": "customer_id",
         "to_table": "public.customers", "to_column": "id"},
    ],
}


def test_an_undrifted_layer_renders_the_same_bytes_after_binding() -> None:
    """The promise that makes v10 a relabel for every layer that was right.

    `saved` is what `save` stores. The run path used to render it as stored;
    it now renders `bind_layer(saved)`. Against the same snapshot, those must be
    the same prompt — not similar, the same.
    """
    saved = _bind(_rich_layer(), FK_SNAPSHOT).model_dump(mode="json")
    rebound = _bind(saved, FK_SNAPSHOT).model_dump(mode="json")

    before = _run_path(saved, FK_SNAPSHOT)
    after = _run_path(rebound, FK_SNAPSHOT)
    assert before == after
    # A render that says nothing is trivially identical; this one says a lot.
    assert "metric revenue = SUM(amount) WHERE status <> 'C'" in after
    assert "Join cautions" in after and "values: C = cancelled" in after
    assert "broken" not in after and "dropped long ago" not in after


def test_binding_is_idempotent_on_everything_the_renderer_reads() -> None:
    once = _bind(_rich_layer(), FK_SNAPSHOT)
    twice = _bind(once, FK_SNAPSHOT)
    assert twice.joins == once.joins
    assert [e.table for e in twice.entities] == [e.table for e in once.entities]
    assert [(e.valid, [m.valid for m in e.metrics], [c.valid for c in e.columns])
            for e in twice.entities] == [
        (e.valid, [m.valid for m in e.metrics], [c.valid for c in e.columns])
        for e in once.entities
    ]


def test_bind_layer_does_not_mutate_its_input() -> None:
    doc = _layer()
    before = doc.model_dump(mode="json")
    _bind(doc, RESYNCED)
    assert doc.model_dump(mode="json") == before


def test_joins_are_derived_from_the_catalog_not_the_stored_copy() -> None:
    stored = _bind(_rich_layer(), FK_SNAPSHOT).model_dump(mode="json")
    stored["joins"] = [
        {"left": "public.orders", "right": "public.customers",
         "on": "invented", "cardinality": "many_to_many"}
    ]
    bound = _bind(stored, FK_SNAPSHOT)
    assert [j.on for j in bound.joins] == [
        "public.orders.customer_id = public.customers.id"
    ]


# ── the loader ───────────────────────────────────────────────────────────
class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _LayerDb:
    """Answers the one query `load_document` makes."""

    def __init__(self, document: dict[str, Any] | None) -> None:
        self.row = (
            None if document is None
            else SemanticLayerRow(id=uuid4(), connection_id=uuid4(), document=document)
        )
        self.queries = 0

    async def execute(self, _statement: Any) -> _Result:
        self.queries += 1
        return _Result(self.row)


def _connection(*, enabled: bool = True) -> DatabaseConnection:
    return DatabaseConnection(
        id=uuid4(), owner_id=uuid4(), name="sales", database_type="postgres",
        semantic_layer_enabled=enabled,
    )


@pytest.mark.asyncio
async def test_load_document_binds_against_the_snapshot_it_is_given() -> None:
    db = _LayerDb(_stored())
    doc = await load_document(db, _connection(), snapshot=RESYNCED)  # type: ignore[arg-type]

    assert doc is not None
    assert not doc.entities[0].metrics[0].valid
    assert "SUM(amount)" not in _run_path(doc.model_dump(mode="json"), RESYNCED)


@pytest.mark.asyncio
async def test_a_switched_off_layer_is_not_read_at_all() -> None:
    db = _LayerDb(_stored())
    assert await load_document(
        db, _connection(enabled=False), snapshot=SAVED  # type: ignore[arg-type]
    ) is None
    assert db.queries == 0


@pytest.mark.asyncio
async def test_an_unreadable_document_fails_open() -> None:
    # The semantic layer's posture: the feature is dropped, the question runs.
    db = _LayerDb({"entities": "not a list"})
    assert await load_document(db, _connection(), snapshot=SAVED) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_row_and_an_empty_row_are_both_no_layer() -> None:
    for document in (None, {}):
        db = _LayerDb(document)
        assert await load_document(db, _connection(), snapshot=SAVED) is None  # type: ignore[arg-type]
