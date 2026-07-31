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
from datetime import date, timedelta

import pytest

from app.charts import (
    MAX_CATEGORY_MARKS,
    AxisSpec,
    ChartIntent,
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
        ("horizontal_bar", "bar"),
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
    assert set(spec["encoding"]) == {"theta", "color"}
    assert spec["encoding"]["theta"]["field"] == "total"
    assert spec["encoding"]["color"]["field"] == "name"


def test_horizontal_bar_swaps_axes() -> None:
    spec = compile_vega_lite(_intent("horizontal_bar"), PROFILE, COLUMNS, ROWS)
    # The measure goes on x, the category on y.
    assert spec["encoding"]["x"]["field"] == "total"
    assert spec["encoding"]["y"]["field"] == "name"
    # ROWS ascend, so the bars keep that reading rather than being flipped.
    assert spec["encoding"]["y"]["sort"] == "x"

    ranked = _ranked(6)
    spec = compile_vega_lite(
        _intent("horizontal_bar"), profile_result(COLUMNS, ranked), COLUMNS, ranked
    )
    assert spec["encoding"]["y"]["sort"] == "-x"


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


# ── the mark budget ──────────────────────────────────────────────────────
def _ranked(n: int, *, descending: bool = True) -> list[list]:
    values = range(n, 0, -1) if descending else range(1, n + 1)
    return [[f"C{i}", float(v)] for i, v in enumerate(values)]


def test_compile_caps_category_marks_and_says_so() -> None:
    rows = _ranked(400)
    spec = compile_vega_lite(
        _intent("horizontal_bar"), profile_result(COLUMNS, rows), COLUMNS, rows
    )
    assert len(spec["data"]["values"]) == MAX_CATEGORY_MARKS
    assert "top 25 of 400" in spec["title"].lower()


def test_cap_keeps_the_end_the_query_ranked_by() -> None:
    # "lowest inventory" ranks ascending; taking the largest would answer the
    # opposite question, so the cap follows the result's own order.
    rows = _ranked(400, descending=False)
    profile = profile_result(COLUMNS, rows)
    spec = compile_vega_lite(_intent("horizontal_bar"), profile, COLUMNS, rows)
    kept = [v["total"] for v in spec["data"]["values"]]
    assert kept == [float(v) for v in range(1, MAX_CATEGORY_MARKS + 1)]
    assert "lowest 25 of 400" in spec["title"].lower()
    assert spec["encoding"]["y"]["sort"] == "x"  # ascending, as the query had it


def test_cap_takes_the_largest_when_rows_are_unordered() -> None:
    rows = [[f"C{i}", float((i * 37) % 400)] for i in range(400)]
    profile = profile_result(COLUMNS, rows)
    spec = compile_vega_lite(_intent("horizontal_bar"), profile, COLUMNS, rows)
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
    rows = _ranked(40)
    plan = plan_chart(profile_result(COLUMNS, rows), _intent("bar"))
    assert plan.intent is not None
    assert plan.intent.chart_type == "horizontal_bar"
    assert plan.source == "model_adjusted"


def test_plan_demotes_a_pie_with_too_many_slices() -> None:
    rows = _ranked(30)
    plan = plan_chart(profile_result(COLUMNS, rows), _intent("pie"))
    assert plan.intent is not None and plan.intent.chart_type == "horizontal_bar"


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


def test_heuristic_uses_horizontal_bar_for_many_categories() -> None:
    intent = heuristic_intent(profile_result(COLUMNS, _ranked(40)))
    assert intent is not None and intent.chart_type == "horizontal_bar"


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
