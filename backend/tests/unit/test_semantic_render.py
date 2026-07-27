"""What the generator prompt actually receives.

Two invariants matter more than the wording: a connection with no layer must
render *nothing* (so the eval baseline stays comparable), and an invalid or
policy-gated entry must never appear.
"""
from __future__ import annotations

from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.pipeline.state import RetrievedContext
from app.semantic import (
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticJoin,
    SemanticMetric,
    TimeSemantics,
    render_semantic,
)

SAMPLE = HintBudget.from_policy(DisclosurePolicy.SAMPLE)
NONE = HintBudget.from_policy(DisclosurePolicy.NONE)

TABLES = [
    {
        "schema": "sales",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "int", "is_primary_key": True},
            {"name": "status", "data_type": "varchar"},
        ],
    }
]


def _doc() -> SemanticDocument:
    return SemanticDocument(
        business_context="A retail order book.",
        time=TimeSemantics(fiscal_year_start_month=4, relative_windows="calendar"),
        entities=[
            SemanticEntity(
                table="sales.orders",
                label="Orders",
                grain="one row per customer order",
                role="fact",
                default_time_column="ordered_at",
                columns=[
                    SemanticColumn(
                        name="status",
                        description="fulfilment state",
                        value_meanings={"P": "pending", "S": "shipped"},
                    )
                ],
                metrics=[
                    SemanticMetric(
                        name="revenue",
                        expression="SUM(sales.orders.total)",
                        filters=["sales.orders.status <> 'CANCELLED'"],
                        synonyms=["net sales"],
                    )
                ],
            )
        ],
    )


def test_empty_document_renders_nothing() -> None:
    assert render_semantic(SemanticDocument(), tables=["sales.orders"], budget=SAMPLE) == ""


def test_schema_block_is_unchanged_when_there_is_no_layer() -> None:
    """The whole feature must be invisible until someone generates a layer."""
    without = RetrievedContext(dialect="postgres", tables=TABLES).render("SAMPLE")
    with_none = RetrievedContext(
        dialect="postgres", tables=TABLES, semantic=None
    ).render("SAMPLE")
    assert without == with_none
    assert "metric" not in without


def test_layer_is_appended_after_the_schema() -> None:
    block = RetrievedContext(
        dialect="postgres", tables=TABLES, semantic=_doc().model_dump(mode="json")
    ).render("SAMPLE")
    assert block.index("Tables:") < block.index("What these tables mean")
    assert "one row per customer order" in block


def test_only_retrieved_tables_are_rendered() -> None:
    out = render_semantic(_doc(), tables=["sales.customers"], budget=SAMPLE)
    assert "sales.orders" not in out


def test_metric_carries_its_definitional_filter() -> None:
    out = render_semantic(_doc(), tables=["sales.orders"], budget=SAMPLE)
    assert "metric revenue = SUM(sales.orders.total)" in out
    assert "WHERE sales.orders.status <> 'CANCELLED'" in out
    assert "net sales" in out


def test_invalid_entries_never_reach_the_prompt() -> None:
    doc = _doc()
    doc.entities[0].metrics[0].valid = False
    doc.entities[0].columns[0].valid = False
    out = render_semantic(doc, tables=["sales.orders"], budget=SAMPLE)
    assert "revenue" not in out
    assert "fulfilment state" not in out
    assert "one row per customer order" in out   # the entity itself is fine


def test_excluded_entity_is_omitted() -> None:
    doc = _doc()
    doc.entities[0].exclude = True
    assert render_semantic(doc, tables=["sales.orders"], budget=SAMPLE) == (
        "About this database: A retail order book.\n\n"
        + _time_line()
    )


def test_value_meanings_ride_the_disclosure_gate() -> None:
    """Keys are real column values, so NONE must not leak them."""
    shown = render_semantic(_doc(), tables=["sales.orders"], budget=SAMPLE)
    hidden = render_semantic(_doc(), tables=["sales.orders"], budget=NONE)
    assert "P = pending" in shown
    assert "P = pending" not in hidden
    assert "fulfilment state" in hidden      # the description is not data


def test_fan_out_warning_needs_both_ends_retrieved() -> None:
    doc = _doc()
    doc.joins = [
        SemanticJoin(
            left="sales.order_items",
            right="sales.orders",
            on="sales.order_items.order_id = sales.orders.id",
            fan_out_warning="orders rows repeat",
        )
    ]
    assert "orders rows repeat" not in render_semantic(
        doc, tables=["sales.orders"], budget=SAMPLE
    )
    assert "orders rows repeat" in render_semantic(
        doc, tables=["sales.orders", "sales.order_items"], budget=SAMPLE
    )


def test_block_is_clipped_to_the_budget() -> None:
    doc = _doc()
    doc.business_context = "x" * 200
    out = render_semantic(doc, tables=["sales.orders"], budget=SAMPLE, max_chars=250)
    assert len(out) <= 250


def _time_line() -> str:
    return (
        "Time conventions: the fiscal year starts in April; weeks start on "
        'Monday; phrases like "last month" mean whole calendar periods.'
    )
