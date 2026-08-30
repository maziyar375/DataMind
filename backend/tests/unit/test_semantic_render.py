"""What the generator prompt actually receives.

Two invariants matter more than the wording: a connection with no layer must
render *nothing* (so the eval baseline stays comparable), and an invalid or
policy-gated entry must never appear.
"""
from __future__ import annotations

from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.pipeline.state import RetrievedContext
from app.semantic import (
    GlossaryTerm,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticJoin,
    SemanticMetric,
    TimeSemantics,
    covered_keys,
    render_semantic,
    render_with_coverage,
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


# ── what survives the cap ────────────────────────────────────────────────
# Regression tests for the bug that made the layer inert: the table
# descriptions were one section, so the first trim dropped every one of them
# and a 42-table layer reached the model as its `business_context` alone.
def _wide(n: int) -> SemanticDocument:
    """`n` described tables, each far too detailed to all fit under the cap."""
    return SemanticDocument(
        business_context="A retail order book.",
        entities=[
            SemanticEntity(
                table=f"sales.t{i:02d}",
                label=f"Table {i}",
                grain="one row per customer order",
                role="fact",
                default_time_column="ordered_at",
                columns=[
                    SemanticColumn(
                        name=f"col{j}",
                        # Unique per table, so a coverage key can be matched
                        # against the one line that would have carried it.
                        description=f"the fulfilment state of a t{i:02d} line",
                    )
                    for j in range(6)
                ],
                metrics=[
                    SemanticMetric(
                        name=f"revenue{i}",
                        expression=f"SUM(sales.t{i:02d}.total)",
                        filters=[f"sales.t{i:02d}.status <> 'CANCELLED'"],
                    )
                ],
            )
            for i in range(n)
        ],
    )


def _tables(n: int) -> list[str]:
    return [f"sales.t{i:02d}" for i in range(n)]


def test_every_table_is_still_described_when_the_block_is_over_budget() -> None:
    """The bug: past ~5 tables the whole section was popped and the model was
    told nothing about any of them."""
    block = render_semantic(_wide(42), tables=_tables(42), budget=SAMPLE)

    assert len(block) <= 8_000
    for i in range(42):
        assert f"- sales.t{i:02d}" in block
        assert block.count(f"- sales.t{i:02d}") == 1
    assert "one row per customer order" in block


def test_there_is_no_cliff_between_five_tables_and_six() -> None:
    """Five rendered and six rendered nothing. Every step must now be monotone
    in what it says about tables."""
    described = [
        len(covered_keys(_wide(n), tables=_tables(n), budget=SAMPLE)[0])
        for n in range(1, 13)
    ]
    assert described == list(range(1, 13))


def test_the_cap_is_spent_on_grain_before_detail() -> None:
    """Tiers, not document order: naming all 42 tables beats fully describing
    six of them, because a table the model cannot name it cannot pick."""
    block = render_semantic(_wide(42), tables=_tables(42), budget=SAMPLE)
    assert block.count("one row per customer order") == 42     # tier 1, whole
    assert block.count("the fulfilment state") < 42 * 6        # tier 3, cut


def test_metrics_outrank_column_meanings() -> None:
    """A metric changes the SQL; a column description changes the reading of
    it. Every metric is funded before the column tier is finished."""
    block = render_semantic(_wide(42), tables=_tables(42), budget=SAMPLE)
    assert block.count("metric revenue") == 42
    assert block.count("the fulfilment state") < 42 * 6


def test_detail_is_shared_out_rather_than_spent_front_to_back() -> None:
    """One wide table must not eat the room the other tables needed."""
    doc = _wide(12)
    doc.entities[0].columns = [
        SemanticColumn(name=f"wide{j}", description="a column on the first table")
        for j in range(200)
    ]
    block = render_semantic(doc, tables=_tables(12), budget=SAMPLE)

    assert block.count("a column on the first table") < 200
    assert "metric revenue11" in block                  # the last table still funded


def test_a_line_is_never_cut_in_half() -> None:
    """Half a metric definition is worse than none — the `WHERE` clause is
    where 'cancelled orders are not revenue' lives."""
    block = render_semantic(_wide(42), tables=_tables(42), budget=SAMPLE)
    for line in block.splitlines():
        if line.lstrip().startswith("metric "):
            assert line.rstrip().endswith(".")
        if line.startswith("- sales."):
            assert line.rstrip().endswith(".")


def test_coverage_is_exactly_what_the_block_said() -> None:
    """The rule the DDL comments depend on. A column whose line did not fit was
    never described, so its comment is still the only sentence about it — and a
    column whose line *did* fit must not be described twice in different words.
    Partial entities are what make this sharp: coverage cannot be re-derived
    from the document, only read off the fit."""
    doc = _wide(42)
    block, tables, columns = render_with_coverage(
        doc, tables=_tables(42), budget=SAMPLE
    )
    assert tables == {f"sales.t{i:02d}" for i in range(42)}
    assert 0 < len(columns) < 42 * 6                  # some detail was cut

    for i in range(42):
        for j in range(6):
            line = f"    col{j}: the fulfilment state of a t{i:02d} line."
            assert (f"sales.t{i:02d}.col{j}" in columns) == (line in block)


def test_joins_and_glossary_are_fitted_too() -> None:
    """They sit behind the tables, but 'behind' means 'gets what is left', not
    'is deleted' — the same rule the table section now follows."""
    doc = _doc()
    doc.glossary = [
        GlossaryTerm(term=f"term{i}", meaning="x" * 400) for i in range(20)
    ]
    block = render_semantic(doc, tables=["sales.orders"], budget=SAMPLE)

    assert len(block) <= 8_000
    assert "Business terms:" in block
    assert 0 < block.count("- term") < 20


def _time_line() -> str:
    return (
        "Time conventions: the fiscal year starts in April; weeks start on "
        'Monday; phrases like "last month" mean whole calendar periods.'
    )


# ── rows that should not count ───────────────────────────────────────────
def test_default_exclusions_reach_the_prompt_as_an_instruction() -> None:
    doc = _doc()
    doc.default_exclusions = "Rows where is_archived is true."
    block = render_semantic(doc, tables=["sales.orders"], budget=SAMPLE)

    assert "Rows to leave out unless the question asks for them:" in block
    assert "is_archived" in block


def test_default_exclusions_sit_ahead_of_the_table_descriptions() -> None:
    """The block is trimmed from the back when it is over budget, so a rule
    that silently doubles a total may not be the first thing dropped."""
    doc = _doc()
    doc.default_exclusions = "Rows where is_archived is true."
    block = render_semantic(doc, tables=["sales.orders"], budget=SAMPLE)

    assert block.index("Rows to leave out") < block.index("What these tables mean")


def test_no_exclusions_means_no_line() -> None:
    """Absent is silent — an empty layer must still render byte-nothing."""
    assert "leave out" not in render_semantic(_doc(), tables=["sales.orders"], budget=SAMPLE)
