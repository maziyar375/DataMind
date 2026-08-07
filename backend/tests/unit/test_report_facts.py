"""The figures a section quotes, computed rather than estimated.

Two properties carry this module, and both are pinned here:

* **A partial result gets nothing.** A total over the first fifty rows of 1,240
  is not approximately right, it is wrong, and the paragraph would state it in
  the same confident sentence it states a correct one in. That rule is what
  keeps `facts.py` from becoming a hallucination source of its own.
* **Every figure stated is returned by `values()`.** That list is the pool
  `checks.py` matches the prose against, so a growth rate the model quoted
  correctly stops being flagged as invented.
"""
from __future__ import annotations

from app.reports import checks
from app.reports.facts import FactColumn, compute

MONTH = FactColumn(name="month", semantic_type="temporal")
REVENUE = FactColumn(name="revenue", semantic_type="quantitative")
REGION = FactColumn(name="region", semantic_type="nominal")
AVG_ORDER = FactColumn(name="avg_order_value", semantic_type="quantitative")
CUSTOMER_ID = FactColumn(name="customer_id", semantic_type="quantitative")

SERIES = [
    ["2026-01-01", 100.0],
    ["2026-02-01", 110.0],
    ["2026-03-01", 120.0],
    ["2026-04-01", 130.0],
    ["2026-05-01", 140.0],
    ["2026-06-01", 150.0],
]


def _text(sheet: object) -> str:
    return sheet.render()  # type: ignore[attr-defined]


# ── the safety rule ──────────────────────────────────────────────────────
def test_a_partial_result_yields_no_facts_at_all() -> None:
    """The rule the module exists to keep: a total over a prefix is a wrong
    total, and no caption makes it a right one."""
    sheet = compute(columns=[MONTH, REVENUE], rows=SERIES, complete=False)

    assert not sheet
    assert sheet.facts == ()
    assert sheet.values() == []
    assert sheet.render() == ""


def test_a_complete_result_yields_facts() -> None:
    assert compute(columns=[MONTH, REVENUE], rows=SERIES, complete=True)


def test_no_rows_and_no_columns_are_survivable() -> None:
    assert not compute(columns=[MONTH, REVENUE], rows=[], complete=True)
    assert not compute(columns=[], rows=SERIES, complete=True)


def test_a_result_with_no_measure_yields_nothing() -> None:
    """Two text columns is a list, and a list has no arithmetic in it."""
    sheet = compute(
        columns=[REGION, FactColumn(name="manager", semantic_type="nominal")],
        rows=[["North", "Sara"], ["South", "Ali"]],
        complete=True,
    )
    assert not sheet


# ── a series ─────────────────────────────────────────────────────────────
def test_a_series_states_its_ends_and_the_change_between_them() -> None:
    sheet = compute(columns=[MONTH, REVENUE], rows=SERIES, complete=True)
    text = _text(sheet)

    assert "2026-01-01" in text and "2026-06-01" in text
    assert "6 periods" in text
    # 100 -> 150 is +50, +50.0%. Both are stated, and both are exact.
    assert "+50" in text
    assert "+50.0%" in text
    assert 50.0 in sheet.values()


def test_a_series_is_ordered_before_it_is_read() -> None:
    """A query without ORDER BY returns whatever the engine found convenient,
    and "fell from 150 to 100" read off it is a fabricated trend made of real
    numbers — the worst kind."""
    scrambled = [SERIES[3], SERIES[0], SERIES[5], SERIES[1], SERIES[4], SERIES[2]]
    sheet = compute(columns=[MONTH, REVENUE], rows=scrambled, complete=True)
    text = _text(sheet)

    assert "revenue was 100 at 2026-01-01 and 150 at 2026-06-01" in text.lower()


def test_a_rising_series_is_called_rising_and_a_noisy_one_is_not() -> None:
    rising = compute(columns=[MONTH, REVENUE], rows=SERIES, complete=True)
    assert "rising" in _text(rising)

    flat_rows = [[f"2026-0{i + 1}-01", 100.0 + (i % 2)] for i in range(6)]
    flat = compute(columns=[MONTH, REVENUE], rows=flat_rows, complete=True)
    assert "broadly flat" in _text(flat)


def test_a_short_series_is_not_given_a_direction() -> None:
    """Two halves of two readings each is not a trend, and a model handed the
    word "rising" will write one into the noise."""
    short = compute(columns=[MONTH, REVENUE], rows=SERIES[:4], complete=True)

    assert "Direction over the whole series" not in _text(short)


def test_a_midnight_timestamp_is_read_as_a_date() -> None:
    rows = [[f"2026-0{i + 1}-01 00:00:00", 100.0 + i] for i in range(3)]
    sheet = compute(columns=[MONTH, REVENUE], rows=rows, complete=True)

    assert "2026-01-01 00:00:00" not in _text(sheet)
    assert "2026-01-01" in _text(sheet)


# ── a ranking ────────────────────────────────────────────────────────────
def test_a_ranking_states_the_concentration_a_model_never_volunteers() -> None:
    """"The leading region is North" is a caption. "North is 40% of revenue and
    the top three are 80%" is the finding, and it is one division away."""
    rows = [["North", 40.0], ["South", 30.0], ["East", 20.0], ["West", 10.0]]
    sheet = compute(columns=[REGION, REVENUE], rows=rows, complete=True)
    text = _text(sheet)

    assert "North accounts for 40.0% of revenue" in text
    assert "top 3 together account for 90.0%" in text
    assert "revenue across all 4 region values: 100" in text


def test_the_pareto_count_is_stated_when_it_says_something() -> None:
    rows = [["A", 80.0], ["B", 8.0], ["C", 6.0], ["D", 6.0]]
    sheet = compute(columns=[REGION, REVENUE], rows=rows, complete=True)

    assert "1 of the 4 region values make up 80.0%" in _text(sheet)


def test_shares_are_withheld_where_they_would_be_nonsense() -> None:
    """A percentage of a sum of negative numbers can exceed 100% or flip sign,
    and the sentence around it would read as if it had not."""
    rows = [["North", 40.0], ["South", -30.0], ["East", 20.0]]
    sheet = compute(columns=[REGION, REVENUE], rows=rows, complete=True)

    assert "%" not in _text(sheet)
    # The extremes are still true and still stated.
    assert "Highest revenue: North at 40" in _text(sheet)


# ── which columns are safe to add up ─────────────────────────────────────
def test_a_non_additive_column_is_never_totalled() -> None:
    """"Total avg_order_value: 41,203" is arithmetically fine and semantically
    nonsense, and a model handed it will put it in a sentence."""
    rows = [["North", 40.0], ["South", 30.0], ["East", 20.0]]
    sheet = compute(columns=[REGION, AVG_ORDER], rows=rows, complete=True)
    text = _text(sheet)

    assert "summed" not in text
    assert "across all" not in text
    # But the extremes and the mean are still real facts about it.
    assert "Highest avg order value" in text
    assert "Mean avg order value" in text


def test_a_name_that_says_total_wins_over_the_non_additive_rule() -> None:
    rows = [["North", 40.0], ["South", 30.0]]
    column = FactColumn(name="total_price", semantic_type="quantitative")
    sheet = compute(columns=[REGION, column], rows=rows, complete=True)

    assert "across all 2 region values: 70" in _text(sheet)


def test_an_identifier_is_not_a_measure() -> None:
    """`SELECT customer_id, region` has a numeric column and no measure, and
    "the largest customer_id" is not a finding."""
    rows = [["North", 4001.0], ["South", 4002.0]]
    sheet = compute(columns=[REGION, CUSTOMER_ID], rows=rows, complete=True)

    assert not sheet


# ── the shapes without a dimension ───────────────────────────────────────
def test_a_single_row_states_each_figure_it_holds() -> None:
    columns = [
        FactColumn(name="orders", semantic_type="quantitative"),
        FactColumn(name="revenue", semantic_type="quantitative"),
    ]
    sheet = compute(columns=columns, rows=[[812, 1_240_533]], complete=True)
    text = _text(sheet)

    assert "orders: 812" in text
    assert "revenue: 1,240,533" in text


def test_bare_observations_are_described_as_a_distribution() -> None:
    rows = [[value] for value in (5.0, 1.0, 3.0, 9.0, 7.0)]
    sheet = compute(columns=[REVENUE], rows=rows, complete=True)
    text = _text(sheet)

    assert "lowest 1, highest 9" in text
    assert "Mean revenue: 5. Median: 5." in text


# ── the values, and the check they feed ──────────────────────────────────
def test_every_stated_figure_is_returned_for_the_numeric_pool() -> None:
    sheet = compute(columns=[MONTH, REVENUE], rows=SERIES, complete=True)
    values = sheet.values()

    for expected in (100.0, 150.0, 50.0, 750.0):
        assert any(abs(value - expected) < 1e-6 for value in values), expected


def test_a_correctly_computed_total_stops_being_flagged() -> None:
    """The check's residual false positive, and the reason this module feeds it.

    `_derived` already excuses a figure that is a ratio of *two* pool values, so
    a simple growth rate was never the problem. What it cannot excuse is
    arithmetic over many rows — a total, a mean, a top-three share — because
    those are in no cell and are not a ratio of any pair. Before the fact sheet
    existed, a paragraph that summed the series *correctly* wore a marker saying
    the number might be invented.
    """
    sheet = compute(columns=[MONTH, REVENUE], rows=SERIES, complete=True)
    prose = "Revenue totalled 750 over the six months, averaging 125 a month."

    rows_only = checks.check_prose(prose, checks.numeric_values(SERIES))
    with_facts = checks.check_prose(
        prose, [*checks.numeric_values(SERIES), *sheet.values()]
    )

    assert {finding.value for finding in rows_only.findings} == {750.0, 125.0}
    assert with_facts.findings == []


def test_an_invented_figure_is_still_caught_with_the_facts_in_the_pool() -> None:
    """The pool grew; the check did not stop working. A number that is in
    neither the rows nor the arithmetic is still unaccounted for."""
    sheet = compute(columns=[MONTH, REVENUE], rows=SERIES, complete=True)
    prose = "Revenue reached 987,654 in the final month."

    result = checks.check_prose(
        prose, [*checks.numeric_values(SERIES), *sheet.values()]
    )

    assert [finding.value for finding in result.findings] == [987_654.0]


# ── bounds ───────────────────────────────────────────────────────────────
def test_the_sheet_is_bounded() -> None:
    """Past a dozen facts the model stops reading them as the figures for this
    paragraph and starts treating them as another table to summarise."""
    rows = [[f"cat-{i}", float(i)] for i in range(200)]
    sheet = compute(columns=[REGION, REVENUE], rows=rows, complete=True)

    assert 0 < len(sheet.facts) <= 14


def test_unparseable_cells_do_not_raise() -> None:
    rows = [["North", None], ["South", "n/a"], ["East", 20.0], ["West", 10.0]]
    sheet = compute(columns=[REGION, REVENUE], rows=rows, complete=True)

    assert "Highest revenue: East at 20" in _text(sheet)
