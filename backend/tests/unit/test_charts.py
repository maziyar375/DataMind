"""Chart planning, compilation, and the chart pipeline node.

Two things are under test here. The compiler: every chart_type (including the
pie arc encoding that x/y would render blank), the mark budget, and the sort
that keeps a ranking readable. And the planner: that a well-formed intent from
the model is still refused or repaired when the *data* says the picture would
be unreadable — a thousand bars, one repeated value, an id charted as a
quantity — because column names existing was never the interesting question.

The node's contract is unchanged and still the outer guarantee: a decline, an
empty result, or a provider error must leave the answer and table untouched and
simply produce no chart.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta

import pytest

from app.charts import (
    MAX_CATEGORY_MARKS,
    AxisSpec,
    ChartIntent,
    _fit,
    compile_vega_lite,
    heuristic_intent,
    plan_chart,
    profile_result,
    unchartable_reason,
    validate_intent,
)
from app.core.clock import utcnow
from app.core.errors import LLMError
from app.domain.ports.database import ResultColumn
from app.domain.ports.llm import ChatMessage, ResolvedLLM
from app.pipeline.nodes import NodeDeps, chart
from app.pipeline.state import ExecutionResult, RunState

COLUMNS = [
    ResultColumn(name="name", db_type="text", semantic_type="nominal"),
    ResultColumn(name="total", db_type="bigint", semantic_type="quantitative"),
]
ROWS = [["A", 10], ["B", 20], ["C", 30]]
PROFILE = profile_result(COLUMNS, ROWS)


def _intent(chart_type: str, **kw) -> ChartIntent:
    return ChartIntent(
        chart_type=chart_type,
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
        **kw,
    )


def _sideways() -> ChartIntent:
    """A bar chart that runs sideways — what `horizontal_bar` used to name."""
    return _intent("bar", orientation="horizontal")


def _cols(*pairs: tuple[str, str]) -> list[ResultColumn]:
    return [ResultColumn(name=n, db_type="text", semantic_type=t) for n, t in pairs]


# ── the profile ──────────────────────────────────────────────────────────
def test_profile_counts_distinct_and_range() -> None:
    profile = profile_result(COLUMNS, ROWS)
    name, total = profile.columns
    assert (name.distinct, total.distinct) == (3, 3)
    assert (total.minimum, total.maximum) == (10.0, 30.0)
    assert profile.row_count == 3


def test_profile_flags_a_constant_column() -> None:
    rows = [["A", 5], ["B", 5], ["C", 5]]
    profile = profile_result(COLUMNS, rows)
    assert profile.columns[1].is_constant
    assert "distinct" in profile.describe() and "SAME VALUE" in profile.describe()


def test_profile_survives_unhashable_values() -> None:
    cols = _cols(("payload", "nominal"), ("n", "quantitative"))
    profile = profile_result(cols, [[{"a": 1}, 1], [{"a": 2}, 2]])
    assert profile.columns[0].distinct == 2


# ── the data's veto ──────────────────────────────────────────────────────
def test_unchartable_single_row() -> None:
    assert unchartable_reason(profile_result(COLUMNS, [["A", 10]])) is not None


def test_unchartable_when_every_measure_is_the_same() -> None:
    # The bug this whole module exists for: 1,000 customers, all tied.
    rows = [[f"Customer {i}", 3881.64] for i in range(1000)]
    reason = unchartable_reason(profile_result(COLUMNS, rows))
    assert reason is not None and "flat" in reason


def test_unchartable_when_the_only_number_is_an_id() -> None:
    cols = _cols(("city", "nominal"), ("city_id", "quantitative"))
    reason = unchartable_reason(profile_result(cols, [["Paris", 1], ["Rome", 2]]))
    assert reason is not None and "identifier" in reason


def test_unchartable_without_a_dimension() -> None:
    cols = _cols(("revenue", "quantitative"))
    reason = unchartable_reason(profile_result(cols, [[1], [2]]))
    assert reason is not None


def test_chartable_result_has_no_veto() -> None:
    assert unchartable_reason(PROFILE) is None


# ── compiler ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "chart_type,mark",
    [
        ("bar", "bar"),
        ("line", "line"),
        ("area", "area"),
        ("scatter", "point"),
        ("pie", "arc"),
    ],
)
def test_compile_marks(chart_type: str, mark: str) -> None:
    spec = compile_vega_lite(_intent(chart_type), PROFILE, COLUMNS, ROWS)
    assert spec["mark"]["type"] == mark
    assert spec["data"]["values"] == [
        {"name": "A", "total": 10},
        {"name": "B", "total": 20},
        {"name": "C", "total": 30},
    ]


def test_pie_uses_theta_and_color_not_xy() -> None:
    spec = compile_vega_lite(_intent("pie"), PROFILE, COLUMNS, ROWS)
    assert set(spec["encoding"]) == {"theta", "color", "tooltip"}
    assert spec["encoding"]["theta"]["field"] == "total"
    assert spec["encoding"]["color"]["field"] == "name"
    # `axis` belongs to positional channels alone. On theta or colour it is not
    # ignored — Vega-Lite rejects the spec, and the tile loses its chart.
    assert "axis" not in spec["encoding"]["theta"]
    assert "axis" not in spec["encoding"]["color"]


def test_horizontal_bar_swaps_axes() -> None:
    spec = compile_vega_lite(_sideways(), PROFILE, COLUMNS, ROWS)
    # The measure goes on x, the category on y.
    assert spec["encoding"]["x"]["field"] == "total"
    assert spec["encoding"]["y"]["field"] == "name"
    # ROWS ascend, so the bars keep that reading rather than being flipped.
    assert spec["encoding"]["y"]["sort"] == "x"

    ranked = _ranked(6)
    spec = compile_vega_lite(
        _sideways(), profile_result(COLUMNS, ranked), COLUMNS, ranked
    )
    assert spec["encoding"]["y"]["sort"] == "-x"


def test_a_vertical_bar_keeps_the_category_on_x() -> None:
    """The other half of the pair, and the one that used to be unreachable: a
    bar chart of 40 categories was rewritten to horizontal whatever was asked
    for."""
    rows = _ranked(40)
    spec = compile_vega_lite(
        _intent("bar", orientation="vertical"), profile_result(COLUMNS, rows), COLUMNS, rows
    )
    assert spec["encoding"]["x"]["field"] == "name"
    assert spec["encoding"]["y"]["field"] == "total"


def test_the_spec_states_the_chart_it_is() -> None:
    """The renderer sizes horizontal bars per category and scrolls them. It
    reads that decision here rather than inferring it from mark plus axis type,
    which a stacked bar or a future `rect` mark could satisfy by accident."""
    assert compile_vega_lite(_sideways(), PROFILE, COLUMNS, ROWS)["usermeta"] == {
        "datamind": {
            "chart_type": "bar",
            "orientation": "horizontal",
            "stack": "stacked",
            "categories": 3,
        }
    }
    upright = compile_vega_lite(_intent("bar", orientation="vertical"), PROFILE, COLUMNS, ROWS)
    assert upright["usermeta"]["datamind"]["orientation"] == "vertical"
    pie = compile_vega_lite(_intent("pie"), PROFILE, COLUMNS, ROWS)
    assert pie["usermeta"]["datamind"]["chart_type"] == "pie"


def test_bar_keeps_axes_and_adds_aggregate_only_when_asked() -> None:
    plain = compile_vega_lite(_intent("bar"), PROFILE, COLUMNS, ROWS)
    assert plain["encoding"]["x"]["field"] == "name"
    assert "aggregate" not in plain["encoding"]["y"]

    rolled = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative", aggregation="sum"),
    )
    spec = compile_vega_lite(rolled, PROFILE, COLUMNS, ROWS)
    assert spec["encoding"]["y"]["aggregate"] == "sum"


def test_series_adds_color_for_cartesian_charts() -> None:
    intent = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
        series=AxisSpec(field="name", type="nominal"),
    )
    spec = compile_vega_lite(intent, PROFILE, COLUMNS, ROWS)
    assert spec["encoding"]["color"]["field"] == "name"


# ── how a value is written on an axis ────────────────────────────────────
# Every expectation below was read back out of a real Vega render, not reasoned
# about: `vl.compile` then `view.scenegraph()`, then the axis-label texts. Three
# of these rules exist *because* the obvious version was wrong on screen.
def _dated(stamps: list) -> dict:
    """The x-axis config of a line chart over `stamps`, or {} if it got none."""
    cols = _cols(("when", "temporal"), ("amount", "quantitative"))
    rows: list[list] = [[s, float(i)] for i, s in enumerate(stamps, start=1)]
    intent = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="when", type="temporal"),
        y_axis=AxisSpec(field="amount", type="quantitative"),
    )
    spec = compile_vega_lite(intent, profile_result(cols, rows), cols, rows)
    return spec["encoding"]["x"].get("axis", {})


def test_a_monthly_axis_is_labelled_in_months_not_a_bare_year() -> None:
    """The reported bug. Unformatted, Vega uses D3's multi-scale time format,
    which labels each tick with the largest unit that changes there — so the
    January tick of a monthly series reads "2025" while its neighbours read
    "September" and "November"."""
    months = [date(2024, 10, 1), date(2024, 11, 1), date(2024, 12, 1), date(2025, 1, 1)]
    assert _dated(months)["format"] == "%b %Y"


def test_a_temporal_axis_ticks_on_its_own_grain() -> None:
    """The half that a format alone does not fix, and would have made worse.

    Ticks are placed before they are labelled, and Vega spaces them for the
    pixels available: a three-month series gets four ticks inside every month,
    which `%b %Y` renders as "Jan 2025" four times in a row. Pinning the
    interval to the grain is what keeps every label distinct.
    """
    quarter = [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]
    assert _dated(quarter)["tickCount"] == {"interval": "month"}


def test_a_long_axis_escalates_the_interval_rather_than_stepping_it() -> None:
    """`interval.every(n)` filters by divisibility instead of striding, so
    `date` every 30 yields Jan 1, Jan 31, Feb 1, Mar 1 — clumped. A coarser
    unit is evenly spaced and cannot clump."""
    five_years = [date(2021, 1, 1) + timedelta(days=i) for i in range(1800)]
    assert _dated(five_years)["tickCount"] == {"interval": "year"}
    assert _dated(five_years)["format"] == "%b %d, %Y"  # still dated to the day

    nineteen_months = [date(2024 + (9 + i) // 12, (9 + i) % 12 + 1, 1) for i in range(19)]
    assert _dated(nineteen_months)["tickCount"] == {"interval": "quarter"}


def test_an_intraday_axis_sets_no_interval_at_all() -> None:
    """`vega-time` has no "hour" interval — `timeInterval("hour")` is
    undefined and Vega throws inside its own tick generator, which in the
    browser means `embed()` rejects and the chart disappears. Format only."""
    hours = [datetime(2025, 3, 1, h, 30) for h in range(24)]
    axis = _dated(hours)
    assert axis["format"] == "%b %d, %H:%M"
    assert "tickCount" not in axis


def test_a_yearly_axis_says_only_the_year() -> None:
    assert _dated([date(2019 + i, 1, 1) for i in range(6)])["format"] == "%Y"


def test_a_column_of_strings_gets_no_time_format() -> None:
    """A connector that pre-formats dates hands back text. A time format
    applied to text is how an axis goes blank."""
    assert "format" not in _dated(["last week", "this week"])


def test_a_big_measure_axis_is_abbreviated_and_a_small_one_is_left_alone() -> None:
    """`~s` past ten thousand. Below it, nothing — and that restraint is the
    measured part: an axis format reaches d3 via `tickFormat`, which fills in a
    precision when the specifier has no type, so a plain "," turns 100, 150,
    200 into 1e+2, 1.5e+2, 2e+2. Vega's own default is already right there.
    """
    cols = _cols(("name", "nominal"), ("amount", "quantitative"))
    intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="amount", type="quantitative"),
    )

    def measure_axis(rows: list[list]) -> dict:
        spec = compile_vega_lite(intent, profile_result(cols, rows), cols, rows)
        return spec["encoding"]["y"].get("axis", {})

    assert measure_axis([["A", 1_000_000.0], ["B", 2_500_000.0]])["format"] == "~s"
    assert measure_axis([["A", 50.0], ["B", 350.0]]) == {}


# ── stacking ─────────────────────────────────────────────────────────────
def _split(stack: str, **kw) -> dict:
    cols = _cols(("region", "nominal"), ("quarter", "nominal"), ("sales", "quantitative"))
    rows = [[f"R{i}", f"Q{j}", float(100 * i + j)] for i in range(4) for j in range(3)]
    intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="region", type="nominal"),
        y_axis=AxisSpec(field="sales", type="quantitative"),
        series=AxisSpec(field="quarter", type="nominal"),
        stack=stack,
        **kw,
    )
    fitted, _ = _fit(intent, profile_result(cols, rows))
    assert fitted is not None
    return compile_vega_lite(fitted, profile_result(cols, rows), cols, rows)["encoding"]


def test_stacked_is_the_default_and_writes_nothing() -> None:
    """Vega-Lite already stacks a bar with a colour channel, so an intent that
    expresses no preference must compile to the bytes it compiled to before
    this field existed."""
    encoding = _split("stacked")
    assert "stack" not in encoding["y"]
    assert "xOffset" not in encoding


def test_grouped_stops_stacking_and_offsets_the_split() -> None:
    """Without the offset the bars overplot and only the last series drawn is
    visible — a chart that looks finished and is missing most of its data."""
    encoding = _split("grouped")
    assert encoding["y"]["stack"] is None
    assert encoding["xOffset"] == {"field": "quarter"}


def test_grouped_horizontal_bars_offset_on_the_other_channel() -> None:
    encoding = _split("grouped", orientation="horizontal")
    assert encoding["x"]["stack"] is None
    assert encoding["yOffset"] == {"field": "quarter"}


def test_normalize_relabels_the_axis_as_a_proportion() -> None:
    """The axis now runs 0-1 and means share, so the measure's own format —
    currency, an SI-abbreviated count — would be a lie about it."""
    encoding = _split("normalize")
    assert encoding["y"]["stack"] == "normalize"
    assert encoding["y"]["axis"]["format"] == "%"


def test_stacking_needs_a_split_to_stack() -> None:
    """A lone series has nothing to share the space with, so the field is reset
    rather than carried — a stored intent never claims a layout it does not
    have. It is not an *adjustment*, though: the picture is identical, so the
    tile reports nothing."""
    plan = plan_chart(PROFILE, _intent("bar", stack="grouped"))
    assert plan.intent is not None
    assert plan.intent.stack == "stacked"
    assert plan.source == "model"


# ── the size channel ─────────────────────────────────────────────────────
def test_a_third_measure_becomes_the_bubble_area() -> None:
    cols = _cols(("spend", "quantitative"), ("revenue", "quantitative"),
                 ("orders", "quantitative"))
    rows = [[float(i * 10), float(i * i), float(i + 1)] for i in range(1, 20)]
    profile = profile_result(cols, rows)

    intent = heuristic_intent(profile)
    assert intent is not None and intent.chart_type == "scatter"
    assert intent.size is not None and intent.size.field == "orders"

    spec = compile_vega_lite(plan_chart(profile).intent, profile, cols, rows)  # type: ignore[arg-type]
    assert spec["encoding"]["size"]["field"] == "orders"
    # `axis` is positional-only; on a size channel it is a schema violation.
    assert "axis" not in spec["encoding"]["size"]


def test_a_size_that_is_not_a_measure_is_dropped() -> None:
    """Same disqualifications as any measure — an id is not a magnitude — plus
    one of its own: a column already carrying a position says nothing extra by
    also carrying an area."""
    cols = _cols(("spend", "quantitative"), ("revenue", "quantitative"),
                 ("customer_id", "quantitative"))
    rows = [[float(i), float(i * 2), float(i)] for i in range(1, 20)]
    profile = profile_result(cols, rows)

    def sized(field: str) -> ChartIntent:
        return ChartIntent(
            chart_type="scatter",
            x_axis=AxisSpec(field="spend", type="quantitative"),
            y_axis=AxisSpec(field="revenue", type="quantitative"),
            size=AxisSpec(field=field, type="quantitative"),
        )

    assert plan_chart(profile, sized("customer_id")).intent.size is None  # type: ignore[union-attr]
    assert plan_chart(profile, sized("spend")).intent.size is None  # type: ignore[union-attr]


def test_size_is_scatter_only() -> None:
    cols = _cols(("name", "nominal"), ("total", "quantitative"), ("weight", "quantitative"))
    rows = [["A", 10.0, 1.0], ["B", 20.0, 2.0], ["C", 30.0, 3.0]]
    intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
        size=AxisSpec(field="weight", type="quantitative"),
    )
    plan = plan_chart(profile_result(cols, rows), intent)
    assert plan.intent is not None and plan.intent.size is None


# ── the tooltip ──────────────────────────────────────────────────────────
def test_the_tooltip_names_every_column_and_formats_them() -> None:
    """`mark.tooltip: true` shows all of the datum's fields and formats none,
    so a hovered bar reads `1247318.4`. Naming fields is the only way to attach
    a format, and the cost is that anything unnamed disappears — hence *every*
    column, not only the encoded ones."""
    cols = _cols(("when", "temporal"), ("amount", "quantitative"), ("note", "nominal"))
    rows = [[date(2025, 1, 1), 1234.5, "a"], [date(2025, 2, 1), 99.0, "b"]]
    intent = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="when", type="temporal"),
        y_axis=AxisSpec(field="amount", type="quantitative"),
    )
    spec = compile_vega_lite(intent, profile_result(cols, rows), cols, rows)

    tooltip = {t["field"]: t for t in spec["encoding"]["tooltip"]}
    assert set(tooltip) == {"when", "amount", "note"}
    assert tooltip["amount"]["format"] == ",.2f"   # full precision, unlike the axis
    assert tooltip["when"]["format"] == "%b %Y"
    assert "format" not in tooltip["note"]
    assert "tooltip" not in spec["mark"]           # the encoding channel owns it


def test_an_aggregating_axis_keeps_vegas_own_tooltip() -> None:
    """A named-field tooltip would report raw row values beside a mark showing
    their roll-up. That is worse than an unformatted number: it is a different
    number."""
    rolled = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative", aggregation="sum"),
    )
    spec = compile_vega_lite(rolled, PROFILE, COLUMNS, ROWS)
    assert spec["mark"]["tooltip"] is True
    assert "tooltip" not in spec["encoding"]


# ── the mark budget ──────────────────────────────────────────────────────
def _ranked(n: int, *, descending: bool = True) -> list[list]:
    values = range(n, 0, -1) if descending else range(1, n + 1)
    return [[f"C{i}", float(v)] for i, v in enumerate(values)]


def test_compile_caps_category_marks_and_says_so() -> None:
    rows = _ranked(400)
    spec = compile_vega_lite(
        _sideways(), profile_result(COLUMNS, rows), COLUMNS, rows
    )
    assert len(spec["data"]["values"]) == MAX_CATEGORY_MARKS
    assert "top 25 of 400" in spec["title"].lower()


def test_cap_keeps_the_end_the_query_ranked_by() -> None:
    # "lowest inventory" ranks ascending; taking the largest would answer the
    # opposite question, so the cap follows the result's own order.
    rows = _ranked(400, descending=False)
    profile = profile_result(COLUMNS, rows)
    spec = compile_vega_lite(_sideways(), profile, COLUMNS, rows)
    kept = [v["total"] for v in spec["data"]["values"]]
    assert kept == [float(v) for v in range(1, MAX_CATEGORY_MARKS + 1)]
    assert "lowest 25 of 400" in spec["title"].lower()
    assert spec["encoding"]["y"]["sort"] == "x"  # ascending, as the query had it


def test_cap_takes_the_largest_when_rows_are_unordered() -> None:
    rows = [[f"C{i}", float((i * 37) % 400)] for i in range(400)]
    profile = profile_result(COLUMNS, rows)
    spec = compile_vega_lite(_sideways(), profile, COLUMNS, rows)
    kept = [v["total"] for v in spec["data"]["values"]]
    assert kept == sorted(kept, reverse=True) and len(kept) == MAX_CATEGORY_MARKS


def test_continuous_charts_keep_every_row() -> None:
    cols = _cols(("day", "temporal"), ("revenue", "quantitative"))
    rows = [[date(2024, 1, 1) + timedelta(days=i), float(i)] for i in range(400)]
    profile = profile_result(cols, rows)
    intent = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="day", type="temporal"),
        y_axis=AxisSpec(field="revenue", type="quantitative"),
    )
    spec = compile_vega_lite(intent, profile, cols, rows)
    assert len(spec["data"]["values"]) == 400
    assert "sort" not in spec["encoding"]["x"]


def test_truncated_result_is_labelled() -> None:
    profile = profile_result(COLUMNS, ROWS, truncated=True)
    spec = compile_vega_lite(_intent("bar"), profile, COLUMNS, ROWS)
    assert "3 rows" in spec["title"]


# ── validation gate ──────────────────────────────────────────────────────
def test_validate_declines_none() -> None:
    ok, reason = validate_intent(ChartIntent(chart_type="none"), PROFILE)
    assert ok is False and reason


def test_validate_rejects_unknown_column() -> None:
    intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="missing", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
    )
    ok, reason = validate_intent(intent, PROFILE)
    assert ok is False and "missing" in (reason or "")


def test_validate_accepts_known_columns() -> None:
    ok, reason = validate_intent(_intent("bar"), PROFILE)
    assert ok is True and reason is None


# ── fitting the model's suggestion ───────────────────────────────────────
def test_plan_keeps_a_sound_model_choice_verbatim() -> None:
    plan = plan_chart(PROFILE, _intent("bar"))
    assert plan.source == "model" and plan.intent is not None
    assert plan.intent.chart_type == "bar"


def test_plan_refuses_a_chart_of_a_constant_measure() -> None:
    rows = [[f"Customer {i}", 3881.64] for i in range(1000)]
    plan = plan_chart(profile_result(COLUMNS, rows), _intent("bar"))
    assert plan.intent is None and plan.reason


def test_plan_flips_a_crowded_bar_to_horizontal() -> None:
    """Still the default — but now a *layout* decision, not a different chart.

    `source` stays "model": the platform choosing how to lay out bars nobody
    had an opinion about is not an adjustment to the model's answer, and
    calling it one made the tile apologise for a chart that was exactly right.
    """
    rows = _ranked(40)
    plan = plan_chart(profile_result(COLUMNS, rows), _intent("bar"))
    assert plan.intent is not None
    assert plan.intent.chart_type == "bar"
    assert plan.intent.orientation == "horizontal"
    assert plan.source == "model"


def test_an_explicit_orientation_survives_the_cardinality_rule() -> None:
    """The bug this consolidation exists for. 40 categories is well past
    `HORIZONTAL_BAR_FROM`, and the old code rewrote the pick and then told the
    user it "does not fit this result"."""
    rows = _ranked(40)
    plan = plan_chart(
        profile_result(COLUMNS, rows), _intent("bar", orientation="vertical")
    )
    assert plan.intent is not None
    assert plan.intent.orientation == "vertical"
    assert plan.source == "model"


def test_a_demoted_pie_does_not_inherit_its_orientation() -> None:
    """An orientation riding along on a pie was never a choice about bars, so
    the platform re-decides it rather than obeying it."""
    rows = _ranked(30)
    plan = plan_chart(
        profile_result(COLUMNS, rows), _intent("pie", orientation="vertical")
    )
    assert plan.intent is not None and plan.intent.chart_type == "bar"
    assert plan.intent.orientation == "horizontal"  # 30 categories


def test_plan_demotes_a_pie_with_too_many_slices() -> None:
    rows = _ranked(30)
    plan = plan_chart(profile_result(COLUMNS, rows), _intent("pie"))
    assert plan.intent is not None and plan.intent.chart_type == "bar"


def test_plan_keeps_a_small_pie() -> None:
    rows = _ranked(4)
    plan = plan_chart(profile_result(COLUMNS, rows), _intent("pie"))
    assert plan.intent is not None and plan.intent.chart_type == "pie"


def test_plan_demotes_a_line_over_unordered_categories() -> None:
    plan = plan_chart(PROFILE, _intent("line"))
    assert plan.intent is not None and plan.intent.chart_type == "bar"


def test_plan_swaps_reversed_axes() -> None:
    reversed_intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="total", type="quantitative"),
        y_axis=AxisSpec(field="name", type="nominal"),
    )
    plan = plan_chart(PROFILE, reversed_intent)
    assert plan.intent is not None
    assert plan.intent.x_axis and plan.intent.x_axis.field == "name"
    assert plan.intent.y_axis and plan.intent.y_axis.field == "total"


def test_plan_corrects_a_mislabelled_axis_type() -> None:
    cols = _cols(("day", "temporal"), ("revenue", "quantitative"))
    rows = [[date(2024, 1, 1) + timedelta(days=i), float(i)] for i in range(10)]
    mislabelled = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="day", type="nominal"),  # a date called text
        y_axis=AxisSpec(field="revenue", type="quantitative"),
    )
    plan = plan_chart(profile_result(cols, rows), mislabelled)
    assert plan.intent is not None and plan.intent.x_axis
    assert plan.intent.x_axis.type == "temporal"
    assert plan.intent.chart_type == "line"  # still a trend, not demoted


def test_plan_makes_a_numeric_series_discrete() -> None:
    # Colour encodes identity, so it must be discrete. Left quantitative, Vega
    # switches scale family (`ramp`) and paints the chart from a different
    # default palette than every other chart in the app.
    cols = _cols(("month", "temporal"), ("year", "quantitative"), ("revenue", "quantitative"))
    rows = [
        [date(2024, m, 1), float(y), float(m * y)]
        for y in (2022, 2023, 2024)
        for m in range(1, 13)
    ]
    split = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="month", type="temporal"),
        y_axis=AxisSpec(field="revenue", type="quantitative"),
        series=AxisSpec(field="year", type="quantitative"),
    )
    plan = plan_chart(profile_result(cols, rows), split)
    assert plan.intent is not None and plan.intent.series is not None
    assert plan.intent.series.field == "year"
    assert plan.intent.series.type == "ordinal"


def test_pie_colour_axis_is_always_discrete() -> None:
    cols = _cols(("month", "temporal"), ("revenue", "quantitative"))
    rows = [[date(2024, m, 1), float(m)] for m in range(1, 5)]
    pie = ChartIntent(
        chart_type="pie",
        x_axis=AxisSpec(field="month", type="temporal"),
        y_axis=AxisSpec(field="revenue", type="quantitative"),
    )
    plan = plan_chart(profile_result(cols, rows), pie)
    assert plan.intent is not None and plan.intent.x_axis
    assert plan.intent.x_axis.type == "ordinal"


def test_plan_refuses_bars_over_a_continuous_axis() -> None:
    # Two measures is a scatter, not a bar chart of a continuous x.
    cols = _cols(("price", "quantitative"), ("units", "quantitative"))
    rows = [[float(i), float(i * 3 % 11)] for i in range(20)]
    bad = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="price", type="quantitative"),
        y_axis=AxisSpec(field="units", type="quantitative"),
    )
    plan = plan_chart(profile_result(cols, rows), bad)
    assert plan.intent is not None and plan.intent.chart_type == "scatter"
    assert plan.source == "heuristic"


def test_plan_drops_a_high_cardinality_series() -> None:
    rows = _ranked(40)
    crowded = _intent("bar", series=AxisSpec(field="name", type="nominal"))
    plan = plan_chart(profile_result(COLUMNS, rows), crowded)
    assert plan.intent is not None and plan.intent.series is None


def test_plan_falls_back_when_the_model_charts_an_id_as_a_measure() -> None:
    cols = _cols(("city", "nominal"), ("city_id", "quantitative"), ("sales", "quantitative"))
    rows = [["Paris", 1, 10.0], ["Rome", 2, 20.0], ["Oslo", 3, 30.0]]
    bad = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="city", type="nominal"),
        y_axis=AxisSpec(field="city_id", type="quantitative"),
    )
    plan = plan_chart(profile_result(cols, rows), bad)
    assert plan.source == "heuristic"
    assert plan.intent is not None and plan.intent.y_axis
    assert plan.intent.y_axis.field == "sales"


# ── heuristic fallback ───────────────────────────────────────────────────
def test_heuristic_bars_a_category_measure() -> None:
    intent = heuristic_intent(PROFILE)
    assert intent is not None
    assert intent.chart_type == "bar"
    assert intent.x_axis and intent.x_axis.field == "name"
    assert intent.y_axis and intent.y_axis.field == "total"


def test_heuristic_leaves_the_orientation_to_the_fit() -> None:
    """The heuristic names the chart; `_fit` — which every path through
    `plan_chart` runs afterwards — lays it out. Deciding it in both places is
    how the two paths drift apart."""
    profile = profile_result(COLUMNS, _ranked(40))
    intent = heuristic_intent(profile)
    assert intent is not None and intent.chart_type == "bar"
    assert intent.orientation == "auto"

    plan = plan_chart(profile)
    assert plan.intent is not None and plan.intent.orientation == "horizontal"


def test_heuristic_lines_a_time_series() -> None:
    cols = _cols(("day", "temporal"), ("revenue", "quantitative"))
    rows = [[date(2024, 1, 1) + timedelta(days=i), float(i)] for i in range(30)]
    intent = heuristic_intent(profile_result(cols, rows))
    assert intent is not None and intent.chart_type == "line"
    assert intent.x_axis and intent.x_axis.type == "temporal"


def test_heuristic_splits_a_trend_by_a_small_dimension() -> None:
    cols = _cols(("day", "temporal"), ("region", "nominal"), ("revenue", "quantitative"))
    rows = [
        [date(2024, 1, 1) + timedelta(days=i), r, float(i)]
        for i in range(20)
        for r in ("EU", "US")
    ]
    intent = heuristic_intent(profile_result(cols, rows))
    assert intent is not None and intent.series and intent.series.field == "region"


def test_heuristic_scatters_two_measures() -> None:
    cols = _cols(("price", "quantitative"), ("units", "quantitative"))
    rows = [[float(i), float(i * 2 % 7)] for i in range(100)]
    intent = heuristic_intent(profile_result(cols, rows))
    assert intent is not None and intent.chart_type == "scatter"


def test_heuristic_declines_without_a_measure() -> None:
    cols = _cols(("city", "nominal"))
    assert heuristic_intent(profile_result(cols, [["Paris"], ["Rome"]])) is None


def test_heuristic_skips_an_id_column_as_the_measure() -> None:
    cols = _cols(("city", "nominal"), ("city_id", "quantitative"), ("sales", "quantitative"))
    rows = [["Paris", 1, 10.0], ["Rome", 2, 20.0]]
    intent = heuristic_intent(profile_result(cols, rows))
    assert intent is not None and intent.y_axis and intent.y_axis.field == "sales"


# ── the node ─────────────────────────────────────────────────────────────
class _Gateway:
    """Minimal stand-in for LLMGateway: only `structured` is exercised here."""

    def __init__(self, *, returns: ChartIntent | None = None, raises: bool = False):
        self._returns = returns
        self._raises = raises
        self.calls = 0

    async def structured(self, llm, messages: Sequence[ChatMessage], schema):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self._raises:
            raise LLMError("provider exploded")
        return self._returns


def _deps(gateway: _Gateway) -> tuple[NodeDeps, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []

    async def emit(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    deps = NodeDeps(
        llm_gateway=gateway,  # type: ignore[arg-type]
        llm=ResolvedLLM(
            config_id=uuid.uuid4(), provider="OpenAI-compatible",
            model="test", base_url=None,
        ),
        connector=None,  # type: ignore[arg-type]  (unused by the chart node)
        snapshot={},
        history=[],
        policy=None,  # type: ignore[arg-type]  (unused by the chart node)
        emit=emit,
    )
    return deps, events


def _state(*, rows: list[list] = ROWS, columns: list[ResultColumn] = COLUMNS) -> RunState:
    return RunState(
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        question="Which products have the lowest inventory?",
        deadline_at=utcnow() + timedelta(seconds=60),
        execution=ExecutionResult(
            columns=columns, rows=rows, row_count=len(rows)
        ),
    )


@pytest.mark.asyncio
async def test_chart_node_sets_spec_on_success() -> None:
    deps, events = _deps(_Gateway(returns=_intent("bar")))
    state = _state()

    result = await chart(state, deps)

    assert result.status == "OK"
    assert state.chart is not None
    assert state.chart["mark"]["type"] == "bar"
    assert (
        "ARTIFACT_CREATED",
        {"kind": "CHART", "chart_type": "bar", "source": "model"},
    ) in events


@pytest.mark.asyncio
async def test_chart_node_falls_back_to_heuristic_on_decline() -> None:
    # A category + measure result is chartable even when the model declines.
    deps, events = _deps(_Gateway(returns=ChartIntent(chart_type="none")))
    state = _state()

    result = await chart(state, deps)

    assert result.status == "OK"
    assert state.chart is not None
    assert ("ARTIFACT_CREATED", {"kind": "CHART", "chart_type": "bar", "source": "heuristic"}) in events


@pytest.mark.asyncio
async def test_chart_node_falls_back_to_heuristic_on_provider_error() -> None:
    deps, events = _deps(_Gateway(raises=True))
    state = _state()

    result = await chart(state, deps)

    assert result.status == "OK"
    assert state.chart is not None
    assert any(e[1].get("source") == "heuristic" for e in events)


@pytest.mark.asyncio
async def test_chart_node_skips_empty_result() -> None:
    deps, _ = _deps(_Gateway(returns=_intent("bar")))
    state = _state(rows=[])

    result = await chart(state, deps)

    assert result.status == "SKIPPED"
    assert state.chart is None


@pytest.mark.asyncio
async def test_chart_node_skips_when_nothing_fits() -> None:
    # Two text columns, no measure: neither the model nor the heuristic can chart.
    two_text = _cols(("city", "nominal"), ("country", "nominal"))
    deps, _ = _deps(_Gateway(raises=True))
    state = _state(rows=[["Paris", "FR"], ["Rome", "IT"]], columns=two_text)

    result = await chart(state, deps)

    assert result.status == "SKIPPED"
    assert state.chart is None


@pytest.mark.asyncio
async def test_chart_node_does_not_call_the_model_for_a_hopeless_result() -> None:
    # A thousand tied totals: no chart is possible, so no tokens are spent
    # discovering that.
    gateway = _Gateway(returns=_intent("bar"))
    deps, _ = _deps(gateway)
    state = _state(rows=[[f"Customer {i}", 3881.64] for i in range(1000)])

    result = await chart(state, deps)

    assert result.status == "SKIPPED"
    assert state.chart is None
    assert gateway.calls == 0
    assert "flat" in (result.detail or "")


@pytest.mark.asyncio
async def test_chart_node_skips_a_single_row() -> None:
    deps, _ = _deps(_Gateway(returns=_intent("bar")))
    state = _state(rows=[["A", 10]])

    result = await chart(state, deps)

    assert result.status == "SKIPPED"
    assert state.chart is None
