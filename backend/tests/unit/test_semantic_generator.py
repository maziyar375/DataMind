"""Generation, driven by a scripted gateway — no provider, no cost.

The generator's job is not to be clever; it is to be *safe with a model that
is not*. These tests are mostly about what happens when the model gets it
wrong: a hallucinated column, an unparseable expression, a table it cannot
answer for at all.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from app.core.errors import LLMError
from app.domain.ports.llm import ChatMessage, Completion, ProviderCapabilities, ResolvedLLM
from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.semantic import generate_document

T = TypeVar("T", bound=BaseModel)

TABLES = [
    {
        "schema": "sales",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "int", "is_primary_key": True},
            {"name": "status", "data_type": "varchar",
             "sample_values": ["PAID", "CANCELLED"]},
            {"name": "ordered_at", "data_type": "timestamp"},
        ],
    },
    {
        "schema": "sales",
        "name": "order_items",
        "columns": [
            {"name": "id", "data_type": "int", "is_primary_key": True},
            {"name": "order_id", "data_type": "int", "is_foreign_key": True},
            {"name": "quantity", "data_type": "int"},
            {"name": "unit_price", "data_type": "numeric"},
        ],
    },
]

RELATIONSHIPS = [
    {"from_table": "sales.order_items", "from_column": "order_id",
     "to_table": "sales.orders", "to_column": "id"},
]

LLM = ResolvedLLM(
    config_id="test", provider="OpenAI-compatible", model="fake",
    base_url=None, capabilities=ProviderCapabilities(),
)
SAMPLE = HintBudget.from_policy(DisclosurePolicy.SAMPLE)


class ScriptedGateway:
    """Answers by prompt shape. `table_reply` is called per table."""

    def __init__(self, table_reply: Any, overview: dict[str, Any] | None = None) -> None:
        self._table_reply = table_reply
        self._overview = overview or {"business_context": "A shop."}
        self.calls: list[str] = []

    async def complete(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> Completion:  # pragma: no cover - unused
        return Completion(text="")

    def stream(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]:  # pragma: no cover - unused
        raise NotImplementedError

    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities:  # pragma: no cover
        return ProviderCapabilities()

    async def structured(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage], schema: type[T]
    ) -> T:
        name = schema.__name__
        self.calls.append(name)
        if name == "_Overview":
            return schema.model_validate(self._overview)
        if name == "_GlossaryDraft":
            return schema.model_validate({"terms": []})
        user = next(m.content for m in messages if m.role == "user")
        table = next(
            line.split(": ", 1)[1]
            for line in user.splitlines()
            if line.startswith("Describe this table: ")
        )
        reply = self._table_reply(table)
        if isinstance(reply, Exception):
            raise reply
        return schema.model_validate(reply)


async def _run(gateway: Any, **kwargs: Any):
    return await generate_document(
        tables=TABLES,
        relationships=RELATIONSHIPS,
        dialect="postgres",
        gateway=gateway,
        llm=LLM,
        budget=SAMPLE,
        **kwargs,
    )


GOOD_ORDER_ITEMS = {
    "label": "Order lines",
    "grain": "one row per line item on an order",
    "role": "fact",
    "default_time_column": "",
    "columns": [
        {"name": "quantity", "description": "units sold", "role": "measure"}
    ],
    "metrics": [
        {
            "name": "Net Revenue",
            "expression": (
                "SUM(sales.order_items.quantity * sales.order_items.unit_price)"
            ),
            "additive": "Additive",
            "synonyms": ["revenue"],
        }
    ],
}


@pytest.mark.asyncio
async def test_describes_every_table_and_keeps_valid_metrics() -> None:
    gateway = ScriptedGateway(
        lambda table: GOOD_ORDER_ITEMS if "order_items" in table else {
            "grain": "one row per order", "role": "fact",
            "default_time_column": "ordered_at",
        }
    )
    doc, stats = await _run(gateway)

    assert stats.tables_described == 2
    assert stats.tables_failed == []
    assert stats.metrics_kept == 1 and stats.metrics_dropped == 0

    items = doc.entity("sales.order_items")
    assert items is not None
    assert items.grain == "one row per line item on an order"
    # Names are slugged and enums coerced, so a model's casing cannot leak out.
    assert items.metrics[0].name == "net_revenue"
    assert items.metrics[0].additive == "additive"


@pytest.mark.asyncio
async def test_a_metric_naming_a_column_that_does_not_exist_is_dropped() -> None:
    gateway = ScriptedGateway(
        lambda table: {
            "grain": "one row",
            "metrics": [
                {"name": "bogus", "expression": "SUM(sales.orders.total_amount)"}
            ],
        }
    )
    doc, stats = await _run(gateway)
    assert stats.metrics_kept == 0
    assert stats.metrics_dropped == 2
    assert all(not e.metrics for e in doc.entities)


@pytest.mark.asyncio
async def test_a_hallucinated_column_description_is_dropped() -> None:
    gateway = ScriptedGateway(
        lambda table: {
            "grain": "one row",
            "columns": [
                {"name": "id"}, {"name": "not_a_real_column", "description": "x"}
            ],
        }
    )
    doc, _ = await _run(gateway)
    for entity in doc.entities:
        assert [c.name for c in entity.columns] == ["id"]


@pytest.mark.asyncio
async def test_a_time_column_that_does_not_exist_is_cleared() -> None:
    gateway = ScriptedGateway(
        lambda table: {"grain": "one row", "default_time_column": "created_at"}
    )
    doc, _ = await _run(gateway)
    assert all(e.default_time_column == "" for e in doc.entities)


@pytest.mark.asyncio
async def test_one_failing_table_does_not_fail_the_job() -> None:
    gateway = ScriptedGateway(
        lambda table: LLMError("the model returned garbage")
        if "orders" in table and "items" not in table
        else GOOD_ORDER_ITEMS
    )
    doc, stats = await _run(gateway)

    assert stats.tables_failed == ["sales.orders"]
    assert stats.tables_described == 1
    assert doc.entity("sales.order_items") is not None


@pytest.mark.asyncio
async def test_overview_failure_still_yields_a_document() -> None:
    class NoOverview(ScriptedGateway):
        async def structured(self, llm, messages, schema):  # type: ignore[override]
            if schema.__name__ == "_Overview":
                raise LLMError("provider down")
            return await super().structured(llm, messages, schema)

    doc, stats = await _run(NoOverview(lambda table: {"grain": "one row"}))
    assert doc.business_context == ""
    assert stats.tables_described == 2


@pytest.mark.asyncio
async def test_only_tables_narrows_the_work() -> None:
    gateway = ScriptedGateway(lambda table: {"grain": "one row"})
    doc, stats = await _run(gateway, only_tables=["sales.orders"])
    assert stats.tables_described == 1
    assert {e.table for e in doc.entities} == {"sales.orders"}


@pytest.mark.asyncio
async def test_joins_are_derived_without_asking_the_model() -> None:
    gateway = ScriptedGateway(lambda table: {"grain": "one row"})
    doc, _ = await _run(gateway)
    assert [j.on for j in doc.joins] == [
        "sales.order_items.order_id = sales.orders.id"
    ]
    assert all(j.provenance.source == "derived" for j in doc.joins)


@pytest.mark.asyncio
async def test_progress_is_reported_monotonically_to_completion() -> None:
    seen: list[tuple[str, int, int]] = []

    async def on_progress(p: Any) -> None:
        seen.append((p.phase, p.current, p.total))

    await _run(ScriptedGateway(lambda t: {"grain": "one row"}), on_progress=on_progress)

    assert [c for _, c, _ in seen] == sorted(c for _, c, _ in seen)
    assert seen[-1][1] == seen[-1][2] == len(TABLES) + 2


@pytest.mark.asyncio
async def test_cancellation_stops_before_the_glossary() -> None:
    gateway = ScriptedGateway(lambda table: {"grain": "one row"})
    doc, stats = await _run(gateway, cancelled=lambda: True)

    assert stats.tables_described == 0
    assert doc.entities == []
    assert "_GlossaryDraft" not in gateway.calls


@pytest.mark.asyncio
async def test_value_meanings_are_limited_to_values_in_the_snapshot() -> None:
    """The model cannot widen a disclosure by inventing a value."""
    gateway = ScriptedGateway(
        lambda table: {
            "grain": "one row",
            "columns": [
                {
                    "name": "status",
                    "value_meanings": {"PAID": "settled", "REFUNDED": "invented"},
                }
            ],
        }
    )
    doc, _ = await _run(gateway)
    orders = doc.entity("sales.orders")
    assert orders is not None
    assert orders.columns[0].value_meanings == {"PAID": "settled"}


# ── a model that answers in the wrong shape ──────────────────────────────
# Defaults cover a field the model *omitted*. They do nothing for one it
# returned in the wrong shape, and pydantic fails the whole object on the
# first of those — so a single `maps_to` written as prose used to throw away
# an entire glossary, and one malformed `synonyms` a whole table.
def test_a_list_written_as_prose_is_read_as_a_list() -> None:
    from app.semantic.generator import _GlossaryDraft

    draft = _GlossaryDraft.model_validate(
        {
            "terms": [
                {
                    "term": "active customer",
                    "meaning": "Ordered in the last 365 days.",
                    # The shape every provider tested returns it in.
                    "maps_to": "sales.customers, sales.orders",
                }
            ]
        }
    )
    assert [t.maps_to for t in draft.terms] == [["sales.customers", "sales.orders"]]


def test_a_predicate_carrying_commas_is_not_split_on_them() -> None:
    """The coercion must not turn a recoverable answer into a wrong one: a
    name list is comma-separated and so is `IN ('a','b')`."""
    from app.semantic.generator import _MetricDraft

    draft = _MetricDraft.model_validate(
        {"expression": "SUM(o.total)", "filters": "o.status IN ('paid','shipped')"}
    )
    assert draft.filters == ["o.status IN ('paid','shipped')"]


def test_an_unusable_field_costs_that_field_and_nothing_else() -> None:
    from app.semantic.generator import _TableDraft

    draft = _TableDraft.model_validate(
        {
            "label": "Orders",
            "grain": "one row per order",
            "synonyms": {"not": "a list"},
            "metrics": [
                {"name": "revenue", "expression": "SUM(o.total)"},
                {"name": "broken", "expression": ["not", "a", "string"]},
            ],
        }
    )
    assert draft.label == "Orders"
    assert draft.grain == "one row per order"
    assert draft.synonyms == []
    # The unusable metric keeps its place with an empty expression, which
    # `_to_entity` then drops and counts — one bad metric, not five lost ones.
    assert [m.name for m in draft.metrics] == ["revenue", "broken"]
    assert draft.metrics[0].expression == "SUM(o.total)"
    assert draft.metrics[1].expression == ""


@pytest.mark.asyncio
async def test_a_glossary_returned_in_the_wrong_shape_still_lands() -> None:
    """End to end: the failure that emptied the glossary in production."""

    async def structured(llm: Any, messages: Sequence[ChatMessage], schema: type[T]) -> T:
        if schema.__name__ == "_GlossaryDraft":
            return schema.model_validate(
                {"terms": [{"term": "AOV", "meaning": "Average order value.",
                            "maps_to": "sales.orders"}]}
            )
        if schema.__name__ == "_Overview":
            return schema.model_validate({"business_context": "A shop."})
        return schema.model_validate({"grain": "one row"})

    gateway = ScriptedGateway(lambda table: {"grain": "one row"})
    gateway.structured = structured  # type: ignore[method-assign]

    doc, _ = await _run(gateway)
    assert [(t.term, t.maps_to) for t in doc.glossary] == [("aov", ["sales.orders"])]
