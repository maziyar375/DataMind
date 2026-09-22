"""Every documented refusal, refused — and refused as a value.

`docs/plans/deep-analysis-mode.md` Phase 2. The other half of
`test_contribution.py`: that file checks the arithmetic is right, this one
checks the module knows when there is no arithmetic to do.

Two properties, and the second is the one that matters for a deep run.

**Every refusal is returned, never raised.** A *why* question the data cannot
answer is the ordinary case, and an exception would make the honest answer look
like a malfunction — which invites the `try` that swallows it, after which the
module silently returns nothing where it used to explain itself.

**A refusal is falsey and carries a sentence.** `if not result: return
result.reason` is how a caller renders it, and the sentence is written for a
business reader rather than a developer, because that is where it ends up.

The first three codes are ThoughtSpot SpotIQ's own documented limits, copied
rather than rediscovered ([research §3.4]). The rest were found by writing the
arithmetic down. `NO_CHANGE` is the important one: asked why revenue fell, a
language model will find a reason, and **the flat `sales` fixture this product
is evaluated against has no drivers anywhere in it** — every series in it is
constant to within the length of the month. A module that names one is
fabricating it.
"""
from __future__ import annotations

import pytest

from app.analysis import Column, Refusal, RefusalCode, compare, contribution, distribution

COLUMNS = [Column("region", "nominal"), Column("total_revenue", "quantitative")]


def _refusal(result: object) -> Refusal:
    assert isinstance(result, Refusal), f"expected a refusal, got {result!r}"
    assert not result, "a refusal must be falsey"
    assert result.reason.strip(), "a refusal with no sentence explains nothing"
    return result


# ── SpotIQ's three ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "measure",
    ["revenue_growth", "sales_change", "revenue_yoy", "orders_delta", "revenue_vs_plan"],
)
def test_a_measure_that_is_already_a_change_is_refused(measure: str) -> None:
    """SpotIQ refuses "growth of" / "versus" phrasings, and the reason is
    arithmetic: the second difference of a series is not what anybody asked
    about. The answer is to attribute the measure it was computed from."""
    columns = [Column("region", "nominal"), Column(measure, "quantitative")]

    result = _refusal(
        contribution(
            columns=columns,
            before_rows=[("a", 10.0), ("b", 10.0)],
            after_rows=[("a", 90.0), ("b", 10.0)],
        )
    )

    assert result.code is RefusalCode.COMPARATIVE_MEASURE
    assert result.subject == measure


@pytest.mark.parametrize("measure", ["group_sum_revenue", "group_avg_price"])
def test_a_group_formula_is_refused(measure: str) -> None:
    """Already aggregated across a grouping this module cannot see, so it
    cannot be re-aggregated across one it can."""
    columns = [Column("region", "nominal"), Column(measure, "quantitative")]

    result = _refusal(
        contribution(
            columns=columns,
            before_rows=[("a", 10.0), ("b", 10.0)],
            after_rows=[("a", 90.0), ("b", 10.0)],
        )
    )

    assert result.code is RefusalCode.GROUP_FORMULA


def test_a_dimension_holding_both_numbers_and_text_is_refused() -> None:
    """SpotIQ's third documented limit. `"42"` and `42` in one column are two
    segments a reader will read as one, and silently stringifying them merges
    two things the database kept apart."""
    result = _refusal(
        contribution(
            columns=COLUMNS,
            before_rows=[("north", 10.0), (42, 10.0)],
            after_rows=[("north", 90.0), (42, 10.0)],
        )
    )

    assert result.code is RefusalCode.MIXED_ATTRIBUTE_TYPES


# ── the ones the arithmetic produced ─────────────────────────────────────
def test_nothing_moved_is_a_refusal_and_it_is_the_important_one() -> None:
    """The commonest true answer to a *why* question, and the one no model
    will give. Every series in the `sales` fixture this product is evaluated
    against is flat, so this is the path most of `deep_v1` takes."""
    rows = [("north", 1000.0), ("south", 1000.0), ("east", 1000.0)]

    result = _refusal(
        contribution(columns=COLUMNS, before_rows=rows, after_rows=list(rows))
    )

    assert result.code is RefusalCode.NO_CHANGE
    assert "did not move" in result.reason


def test_flatness_is_relative_so_it_holds_for_a_rate_as_well_as_for_revenue() -> None:
    """An absolute epsilon is a unit the module does not know. 0.01 is noise in
    rials and a doubling in a conversion rate, and a fixed cut-off would call
    one of those wrong."""
    tiny = [("a", 1e-12), ("b", 2e-12)]
    moved = [("a", 1e-12), ("b", 4e-12)]

    # The same proportional move, at a scale a fixed epsilon would swallow.
    assert contribution(columns=COLUMNS, before_rows=tiny, after_rows=moved)


def test_an_average_without_its_weight_is_refused_rather_than_guessed() -> None:
    """The refusal this phase's stated risk turns on.

    Averaging segment averages as if every segment were the same size produces
    a number that looks exactly like a measurement. Refusing costs an answer;
    guessing costs the trustworthiness of every answer the module gives.
    """
    columns = [Column("region", "nominal"), Column("avg_order_value", "quantitative")]

    result = _refusal(
        contribution(
            columns=columns,
            before_rows=[("a", 100.0), ("b", 100.0)],
            after_rows=[("a", 60.0), ("b", 100.0)],
        )
    )

    assert result.code is RefusalCode.RATIO_NEEDS_WEIGHT
    assert "do not add up" in result.reason


def test_a_ratio_that_started_at_zero_is_refused() -> None:
    columns = [
        Column("region", "nominal"),
        Column("avg_order_value", "quantitative"),
        Column("orders", "quantitative"),
    ]

    result = _refusal(
        contribution(
            columns=columns,
            before_rows=[("a", 0.0, 10), ("b", 0.0, 10)],
            after_rows=[("a", 50.0, 10), ("b", 0.0, 10)],
        )
    )

    assert result.code is RefusalCode.ZERO_BASELINE


def test_a_result_with_nothing_to_break_down_by_is_refused() -> None:
    result = _refusal(
        contribution(
            columns=[Column("total_revenue", "quantitative")],
            before_rows=[(100.0,)],
            after_rows=[(200.0,)],
        )
    )

    assert result.code is RefusalCode.NO_DIMENSION
    assert "cannot be attributed" in result.reason


def test_a_result_with_no_measure_is_refused() -> None:
    result = _refusal(
        contribution(
            columns=[Column("region", "nominal")],
            before_rows=[("a",)],
            after_rows=[("b",)],
        )
    )

    assert result.code is RefusalCode.NO_MEASURE


def test_a_column_the_result_does_not_have_is_refused_not_ignored() -> None:
    """Naming a measure that is not there is a caller's mistake, and answering
    it with a different column's arithmetic would be worse than saying so."""
    result = _refusal(
        contribution(
            columns=COLUMNS,
            before_rows=[("a", 1.0)],
            after_rows=[("a", 2.0)],
            measure=Column("profit", "quantitative"),
        )
    )

    assert result.subject == "profit"


def test_a_result_past_the_row_ceiling_is_refused() -> None:
    """O(n log n) per call, and a dimension with more values than this is not
    one anybody reads the answer to."""
    rows = [(f"v{i}", 1.0) for i in range(20_001)]

    result = _refusal(
        contribution(columns=COLUMNS, before_rows=rows, after_rows=rows)
    )

    assert result.code is RefusalCode.TOO_MANY_ROWS


# ── periods ──────────────────────────────────────────────────────────────
def test_a_series_with_no_period_column_is_refused() -> None:
    result = _refusal(compare(columns=COLUMNS, rows=[("a", 1.0), ("b", 2.0)]))

    assert result.code is RefusalCode.NO_DIMENSION


def test_one_dated_row_is_not_two_periods() -> None:
    columns = [Column("day", "temporal"), Column("revenue", "quantitative")]

    result = _refusal(compare(columns=columns, rows=[("2026-01-01", 5.0)]))

    assert result.code is RefusalCode.NOT_ENOUGH_ROWS


def test_a_split_that_leaves_one_side_empty_is_refused() -> None:
    columns = [Column("day", "temporal"), Column("revenue", "quantitative")]
    rows = [(f"2026-01-{d:02d}", 5.0) for d in range(1, 6)]

    result = _refusal(compare(columns=columns, rows=rows, split_at="2026-03-01"))

    assert result.code is RefusalCode.NOT_ENOUGH_ROWS


# ── outliers ─────────────────────────────────────────────────────────────
def test_a_distribution_with_no_spread_is_refused_not_divided_by_zero() -> None:
    result = _refusal(distribution(["a", "b", "c"], [5.0, 5.0, 5.0]))

    assert result.code is RefusalCode.NO_CHANGE


def test_a_single_value_has_nothing_to_be_unusual_against() -> None:
    result = _refusal(distribution(["a"], [5.0]))

    assert result.code is RefusalCode.NOT_ENOUGH_VALUES


def test_labels_and_values_of_different_lengths_are_refused() -> None:
    result = _refusal(distribution(["a", "b"], [5.0]))

    assert result.code is RefusalCode.NOT_ENOUGH_VALUES


# ── the posture itself ───────────────────────────────────────────────────
def test_no_refusal_is_ever_raised() -> None:
    """The property, stated once over every entry point.

    Each of these is a call that cannot produce an answer. None of them may
    raise, because a deep step that crashes on an unanswerable sub-question
    loses the whole run's evidence — where one that refuses contributes a
    sentence to the report.
    """
    calls = [
        lambda: contribution(columns=COLUMNS, before_rows=[], after_rows=[]),
        lambda: contribution(
            columns=[], before_rows=[("a", 1.0)], after_rows=[("a", 2.0)]
        ),
        lambda: compare(columns=[], rows=[]),
        lambda: distribution([], []),
        lambda: distribution(["a", "b"], [float("inf"), 1.0]),
    ]

    for call in calls:
        assert not call(), "an unanswerable call returned something truthy"
