"""The three algorithms, on fixtures whose answer is known before the code runs.

`docs/plans/deep-analysis-mode.md` Phase 2. Every fixture here is built so that
the right answer is a property of how it was *constructed*, not of what the
module returned — which is the only way to test arithmetic. A test whose
expectation was read off the implementation agrees with whatever the
implementation does, including its bugs, and for a module whose entire claim is
*"no model computed this number"* that would be the worst possible test.

So each case below states its answer in the fixture:

* SIMPLE — four segments, three flat and one down by a known amount. The
  contribution of that one is 1.0 because the others contributed nothing.
* RATIO — the **mix-shift** case, which is the one a difference analysis gets
  wrong: no segment's own average moves at all, yet the overall average falls,
  because a cheaper segment tripled in volume. The right answer is a segment
  whose `change` is exactly zero, and any method that ranked by difference
  would report that nothing happened.
* COMPLEX — eleven flat segments and one that moved, at a cardinality where the
  threshold is attainable, so the flag is a real flag.

**No provider, no database, no fixtures directory.** Literals in, dataclasses
out — the package cannot reach anything else, and the ninth import-linter
contract is what proves it rather than this file.
"""
from __future__ import annotations

from datetime import date

from app.analysis import (
    Column,
    MeasureClass,
    Refusal,
    RefusalCode,
    classify,
    compare,
    contribution,
    distribution,
    threshold_for,
)

SIMPLE_COLUMNS = [Column("region", "nominal"), Column("total_revenue", "quantitative")]
RATIO_COLUMNS = [
    Column("region", "nominal"),
    Column("avg_order_value", "quantitative"),
    Column("orders", "quantitative"),
]
COMPLEX_COLUMNS = [
    Column("region", "nominal"),
    Column("distinct_customers", "quantitative"),
]


# ── the classifier, which decides everything downstream ──────────────────
def test_a_measure_is_classified_by_what_it_is_not_by_its_type() -> None:
    """Every one of these comes back `numeric` from the database and
    `quantitative` from the connector. The name is all there is."""
    assert classify(Column("total_revenue", "quantitative")) is MeasureClass.SIMPLE
    assert classify(Column("orders", "quantitative")) is MeasureClass.SIMPLE
    assert classify(Column("avg_order_value", "quantitative")) is MeasureClass.RATIO
    assert classify(Column("conversion_rate", "quantitative")) is MeasureClass.RATIO
    assert classify(Column("distinct_customers", "quantitative")) is MeasureClass.COMPLEX


def test_an_aggregate_word_beats_a_sum_word_and_not_the_other_way_round() -> None:
    """`avg_total_revenue` is an average of totals; `total_price` is a total.

    The asymmetry is the module's one safety margin: calling a sum a ratio
    costs an answer, calling a ratio a sum costs a *wrong* one.
    """
    assert classify(Column("avg_total_revenue", "quantitative")) is MeasureClass.RATIO
    assert classify(Column("total_price", "quantitative")) is MeasureClass.SIMPLE
    assert classify(Column("sum_cost", "quantitative")) is MeasureClass.SIMPLE
    assert classify(Column("unit_price", "quantitative")) is MeasureClass.RATIO


# ── SIMPLE ───────────────────────────────────────────────────────────────
def test_a_sum_decomposes_and_the_one_segment_that_moved_is_the_whole_of_it() -> None:
    before = [("north", 1000.0), ("south", 1000.0), ("east", 1000.0), ("west", 1000.0)]
    after = [("north", 1000.0), ("south", 400.0), ("east", 1000.0), ("west", 1000.0)]

    result = contribution(
        columns=SIMPLE_COLUMNS, before_rows=before, after_rows=after
    )

    assert result
    assert result.measure_class is MeasureClass.SIMPLE
    assert result.total_before == 4000.0
    assert result.total_change == -600.0
    assert result.total_change_pct == -15.0
    leader = result.top(1)[0]
    assert leader.value == "south"
    assert leader.change == -600.0
    # Known by construction: the other three contributed nothing, so this one
    # contributed all of it.
    assert leader.contribution == 1.0
    assert any("100%" in note for note in result.notes)


def test_the_shares_of_a_sum_add_to_one_however_the_change_is_spread() -> None:
    """The property that makes a share meaningful, asserted rather than assumed.

    Two segments down and one up, so the shares include a negative one — a
    segment that moved *against* the total is a real finding and its share has
    to be signed for the arithmetic to hold.
    """
    before = [("a", 500.0), ("b", 500.0), ("c", 500.0)]
    after = [("a", 300.0), ("b", 350.0), ("c", 600.0)]

    result = contribution(columns=SIMPLE_COLUMNS, before_rows=before, after_rows=after)

    assert result
    assert result.total_change == -250.0
    shares = [d.contribution for d in result.drivers]
    assert all(s is not None for s in shares)
    assert abs(sum(shares) - 1.0) < 1e-9          # type: ignore[arg-type]
    assert any(s < 0 for s in shares)             # type: ignore[operator]


def test_a_segment_that_appeared_is_named_as_new_not_as_grown() -> None:
    """A segment with no "before" has a change equal to its whole size, which
    reads as spectacular growth unless the sentence says it is new."""
    before = [("north", 1000.0)]
    after = [("north", 1000.0), ("online", 400.0)]

    result = contribution(columns=SIMPLE_COLUMNS, before_rows=before, after_rows=after)

    assert result
    leader = result.top(1)[0]
    assert (leader.value, leader.appeared, leader.before) == ("online", True, 0.0)
    assert any("new in the second period" in note for note in result.notes)


# ── RATIO: the case a difference analysis gets wrong ─────────────────────
def test_a_pure_mix_shift_is_found_though_no_segments_own_average_moved() -> None:
    """The test this whole class of algorithm exists for.

    Constructed so that every segment's average is **identical** before and
    after. The overall average still falls, because the cheapest segment
    tripled in volume and the overall figure is a weighted one. A method that
    ranked by each segment's own difference would rank three zeroes and report
    that nothing happened; the hypothetical names `cheap`, because holding it
    still is what removes the change.
    """
    before = [("rich", 200.0, 100), ("mid", 100.0, 100), ("cheap", 50.0, 100)]
    after = [("rich", 200.0, 100), ("mid", 100.0, 100), ("cheap", 50.0, 300)]

    result = contribution(
        columns=RATIO_COLUMNS, before_rows=before, after_rows=after
    )

    assert result
    assert result.measure_class is MeasureClass.RATIO
    # (200+100+50)/3 = 116.67 before; (200*100 + 100*100 + 50*300)/500 = 90 after.
    assert abs(result.total_before - 350.0 / 3.0) < 1e-9
    assert result.total_after == 90.0

    leader = result.top(1)[0]
    assert leader.value == "cheap"
    assert leader.change == 0.0, "its own average did not move — that is the point"
    # Held still, the overall average would not have moved at all. Written
    # without `or`, because `0.0 or x` is `x` and that is the value under test.
    assert leader.hypothetical_change_pct is not None
    assert abs(leader.hypothetical_change_pct) < 1e-9
    # And the segments that did nothing leave the whole change in place.
    others = [d for d in result.drivers if d.value != "cheap"]
    assert all(
        abs((d.hypothetical_change_pct or 0.0) - (result.total_change_pct or 0.0)) < 1e-9
        for d in others
    )


def test_a_ratio_carries_a_hypothetical_and_never_a_share() -> None:
    """Segment averages do not add to the overall average, so a `contribution`
    here would be a number with no referent. It is `None` on every driver, and
    that `None` is a statement rather than a gap."""
    before = [("a", 100.0, 100), ("b", 100.0, 100)]
    after = [("a", 60.0, 100), ("b", 100.0, 100)]

    result = contribution(columns=RATIO_COLUMNS, before_rows=before, after_rows=after)

    assert result
    assert all(d.contribution is None for d in result.drivers)
    assert all(d.hypothetical_change_pct is not None for d in result.drivers)
    assert "hypotheticals" in result.notes[0]


def test_a_weight_column_is_found_without_being_named() -> None:
    """`region, avg_order_value, orders` is the shape a query returns, and
    making the caller name the weight every time is how the weight stops being
    passed."""
    before = [("a", 100.0, 10), ("b", 100.0, 10)]
    after = [("a", 50.0, 10), ("b", 100.0, 10)]

    assert contribution(columns=RATIO_COLUMNS, before_rows=before, after_rows=after)


# ── COMPLEX ──────────────────────────────────────────────────────────────
def test_a_distinct_count_is_scored_and_never_shared_out() -> None:
    """Twelve segments, eleven flat and one up by 80.

    Twelve is above the eight where the threshold first becomes attainable, so
    the flag means something. `contribution` stays `None` on every driver: a
    customer can be in two regions, so the per-segment counts do not add up to
    the overall one and no share of it exists to compute.
    """
    before = [(f"r{i}", 100.0) for i in range(12)]
    after = [(f"r{i}", 100.0) for i in range(11)] + [("r11", 180.0)]

    result = contribution(
        columns=COMPLEX_COLUMNS, before_rows=before, after_rows=after
    )

    assert result
    assert result.measure_class is MeasureClass.COMPLEX
    leader = result.top(1)[0]
    assert (leader.value, leader.change, leader.flagged) == ("r11", 80.0, True)
    assert all(d.contribution is None for d in result.drivers)
    assert "do not add up" in result.notes[0]


# ── the thresholds ───────────────────────────────────────────────────────
def test_the_threshold_gets_stricter_as_the_dimension_gets_wider() -> None:
    """Testing four regions for an extreme is four chances to see one; testing
    four thousand SKUs is four thousand. A fixed N manufactures findings on
    wide dimensions, which is where nobody can check them by eye."""
    assert threshold_for(4) == 2.0
    assert threshold_for(40) == 3.0
    assert threshold_for(5_000) == 5.0
    widths = [4, 15, 40, 150, 900, 5_000]
    assert [threshold_for(w) for w in widths] == sorted(threshold_for(w) for w in widths)


def test_a_narrow_dimension_cannot_clear_its_threshold_and_says_so() -> None:
    """The ceiling nobody mentions: |z| can be at most `(n-1)/sqrt(n)`.

    For four values that is 1.5, below even the most permissive threshold — so
    an empty `flagged` on a four-value dimension says nothing whatever about
    the data. `discriminating` is what lets a caller tell the two apart, and
    without it "no outliers" would be arithmetic reported as a finding.
    """
    narrow = distribution(["a", "b", "c", "d"], [0.0, 0.0, 0.0, 900.0])

    assert narrow
    assert narrow.flagged == ()
    assert not narrow.discriminating
    assert narrow.max_attainable_z < narrow.threshold

    wide = distribution([str(i) for i in range(30)], [0.0] * 29 + [900.0])
    assert wide and wide.discriminating and wide.flagged


def test_a_simple_contribution_on_a_narrow_dimension_says_why_nothing_is_flagged() -> None:
    """Four regions is the commonest dimension there is, and the ranking is
    still worth having — it is the *flag* that cannot fire."""
    before = [("north", 100.0), ("south", 100.0), ("east", 100.0), ("west", 100.0)]
    after = [("north", 100.0), ("south", 100.0), ("east", 100.0), ("west", 20.0)]

    result = contribution(columns=SIMPLE_COLUMNS, before_rows=before, after_rows=after)

    assert result
    assert result.top(1)[0].value == "west"
    assert not any(d.flagged for d in result.drivers)
    assert any("whatever the data does" in note for note in result.notes)


# ── periods: the calendar, which is the commonest false driver ───────────
def test_two_months_of_the_same_daily_rate_are_not_a_fall() -> None:
    """The `sales` fixture's own shape, and `deep_v1`'s first question.

    January holds 31 days at 8,000 and February 28 at 8,000. The total is 9.7%
    lower and the business did exactly the same amount of work every day. A
    comparison that reports only the total hands a reader a fall to explain,
    and every explanation of it will be invented.
    """
    columns = [Column("day", "temporal"), Column("revenue", "quantitative")]
    rows = [(date(2026, 1, d), 8000.0) for d in range(1, 32)]
    rows += [(date(2026, 2, d), 8000.0) for d in range(1, 29)]

    pair = compare(columns=columns, rows=rows, split_at=date(2026, 2, 1))

    assert pair
    assert round(pair.change_pct or 0.0, 1) == -9.7
    assert pair.per_day_change_pct == 0.0
    assert not pair.comparable
    assert "31 days against 28" in pair.note
    assert "read the first one" in pair.note


def test_two_equal_periods_compare_without_a_caveat() -> None:
    columns = [Column("day", "temporal"), Column("revenue", "quantitative")]
    rows = [(date(2026, 3, d), 100.0) for d in range(1, 16)]
    rows += [(date(2026, 3, d), 120.0) for d in range(16, 31)]

    pair = compare(columns=columns, rows=rows, split_at=date(2026, 3, 16))

    assert pair
    assert pair.comparable
    assert pair.note == ""
    assert pair.change_pct == 20.0


def test_a_series_is_sorted_before_it_is_split() -> None:
    """A query without `ORDER BY` returns whatever the engine found
    convenient, and a before/after read off that order is a comparison of two
    arbitrary halves."""
    columns = [Column("day", "temporal"), Column("revenue", "quantitative")]
    ordered = [(date(2026, 4, d), float(d)) for d in range(1, 11)]
    shuffled = [ordered[i] for i in (7, 2, 9, 0, 5, 3, 8, 1, 6, 4)]

    assert compare(columns=columns, rows=shuffled, split_at=date(2026, 4, 6)) == compare(
        columns=columns, rows=ordered, split_at=date(2026, 4, 6)
    )


# ── the shape of a refusal ───────────────────────────────────────────────
def test_a_refusal_is_falsey_so_a_caller_reads_it_as_one() -> None:
    result = contribution(
        columns=SIMPLE_COLUMNS,
        before_rows=[("a", 1.0)],
        after_rows=[("a", 1.0)],
    )

    assert isinstance(result, Refusal)
    assert not result
    assert result.code is RefusalCode.NO_CHANGE
    assert result.reason
