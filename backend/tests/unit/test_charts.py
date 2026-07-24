"""Chart intent compilation and the chart pipeline node.

Covers the compiler for every chart_type (including the pie arc encoding that
x/y would render blank), the validation gate, and the node's fail-closed
behaviour: a decline, an empty result, or a provider error must leave the
answer and table untouched and simply produce no chart.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

import pytest

from app.charts import (
    AxisSpec,
    ChartIntent,
    compile_vega_lite,
    heuristic_intent,
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


def _intent(chart_type: str) -> ChartIntent:
    return ChartIntent(
        chart_type=chart_type,
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
    )


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
    spec = compile_vega_lite(_intent(chart_type), COLUMNS, ROWS)
    assert spec["mark"]["type"] == mark
    assert spec["data"]["values"] == [
        {"name": "A", "total": 10},
        {"name": "B", "total": 20},
        {"name": "C", "total": 30},
    ]


def test_pie_uses_theta_and_color_not_xy() -> None:
    spec = compile_vega_lite(_intent("pie"), COLUMNS, ROWS)
    assert set(spec["encoding"]) == {"theta", "color"}
    assert spec["encoding"]["theta"]["field"] == "total"
    assert spec["encoding"]["color"]["field"] == "name"


def test_horizontal_bar_swaps_axes() -> None:
    spec = compile_vega_lite(_intent("horizontal_bar"), COLUMNS, ROWS)
    # The measure goes on x, the category on y.
    assert spec["encoding"]["x"]["field"] == "total"
    assert spec["encoding"]["y"]["field"] == "name"


def test_bar_keeps_axes_and_adds_aggregate_only_when_asked() -> None:
    plain = compile_vega_lite(_intent("bar"), COLUMNS, ROWS)
    assert plain["encoding"]["x"]["field"] == "name"
    assert "aggregate" not in plain["encoding"]["y"]

    rolled = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative", aggregation="sum"),
    )
    spec = compile_vega_lite(rolled, COLUMNS, ROWS)
    assert spec["encoding"]["y"]["aggregate"] == "sum"


def test_series_adds_color_for_cartesian_charts() -> None:
    intent = ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="name", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
        series=AxisSpec(field="name", type="nominal"),
    )
    spec = compile_vega_lite(intent, COLUMNS, ROWS)
    assert spec["encoding"]["color"]["field"] == "name"


# ── validation gate ──────────────────────────────────────────────────────
def test_validate_declines_none() -> None:
    ok, reason = validate_intent(ChartIntent(chart_type="none"), COLUMNS)
    assert ok is False and reason


def test_validate_rejects_unknown_column() -> None:
    intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="missing", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
    )
    ok, reason = validate_intent(intent, COLUMNS)
    assert ok is False and "missing" in (reason or "")


def test_validate_accepts_known_columns() -> None:
    ok, reason = validate_intent(_intent("bar"), COLUMNS)
    assert ok is True and reason is None


# ── the node ─────────────────────────────────────────────────────────────
class _Gateway:
    """Minimal stand-in for LLMGateway: only `structured` is exercised here."""

    def __init__(self, *, returns: ChartIntent | None = None, raises: bool = False):
        self._returns = returns
        self._raises = raises

    async def structured(self, llm, messages: Sequence[ChatMessage], schema):  # type: ignore[no-untyped-def]
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
    two_text = [
        ResultColumn(name="city", db_type="text", semantic_type="nominal"),
        ResultColumn(name="country", db_type="text", semantic_type="nominal"),
    ]
    deps, _ = _deps(_Gateway(raises=True))
    state = _state(rows=[["Paris", "FR"]], columns=two_text)

    result = await chart(state, deps)

    assert result.status == "SKIPPED"
    assert state.chart is None


# ── heuristic fallback ───────────────────────────────────────────────────
def test_heuristic_bars_a_category_measure() -> None:
    intent = heuristic_intent(COLUMNS, row_count=3)
    assert intent is not None
    assert intent.chart_type == "bar"
    assert intent.x_axis and intent.x_axis.field == "name"
    assert intent.y_axis and intent.y_axis.field == "total"


def test_heuristic_uses_horizontal_bar_for_many_categories() -> None:
    intent = heuristic_intent(COLUMNS, row_count=40)
    assert intent is not None and intent.chart_type == "horizontal_bar"


def test_heuristic_lines_a_time_series() -> None:
    cols = [
        ResultColumn(name="day", db_type="date", semantic_type="temporal"),
        ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
    ]
    intent = heuristic_intent(cols, row_count=30)
    assert intent is not None and intent.chart_type == "line"
    assert intent.x_axis and intent.x_axis.type == "temporal"


def test_heuristic_scatters_two_measures() -> None:
    cols = [
        ResultColumn(name="price", db_type="numeric", semantic_type="quantitative"),
        ResultColumn(name="units", db_type="bigint", semantic_type="quantitative"),
    ]
    intent = heuristic_intent(cols, row_count=100)
    assert intent is not None and intent.chart_type == "scatter"


def test_heuristic_declines_without_a_measure() -> None:
    cols = [ResultColumn(name="city", db_type="text", semantic_type="nominal")]
    assert heuristic_intent(cols, row_count=5) is None
