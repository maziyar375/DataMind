"""Chart intent → validation → Vega-Lite.

The model proposes a constrained `ChartIntent`; it never emits a Vega-Lite
spec directly. An intent referencing a column that is not in the result is
dropped, and the answer is kept. Fail closed, but only for the chart.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.ports.database import ResultColumn


class AxisSpec(BaseModel):
    field: str
    type: Literal["quantitative", "temporal", "nominal", "ordinal"] = "nominal"
    aggregation: Literal["sum", "avg", "min", "max", "count", "none"] = "none"
    label: str | None = None


class ChartIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: Literal[
        "line", "bar", "horizontal_bar", "area", "scatter", "pie", "none"
    ] = "none"
    x_axis: AxisSpec | None = None
    y_axis: AxisSpec | None = None
    series: AxisSpec | None = None
    title: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _axes_required(self) -> ChartIntent:
        if self.chart_type != "none" and (self.x_axis is None or self.y_axis is None):
            raise ValueError("x_axis and y_axis are required unless chart_type is 'none'")
        return self


_MARKS = {
    "line": "line", "bar": "bar", "horizontal_bar": "bar",
    "area": "area", "scatter": "point", "pie": "arc",
}


def validate_intent(
    intent: ChartIntent, columns: list[ResultColumn]
) -> tuple[bool, str | None]:
    if intent.chart_type == "none":
        return False, "The model declined to chart this result."
    known = {c.name for c in columns}
    for axis in (intent.x_axis, intent.y_axis, intent.series):
        if axis is not None and axis.field not in known:
            return False, f"Chart referenced unknown column {axis.field!r}."
    return True, None


_SEM_TO_VEGA = {
    "quantitative": "quantitative",
    "temporal": "temporal",
    "ordinal": "ordinal",
    "nominal": "nominal",
}


def heuristic_intent(
    columns: list[ResultColumn], row_count: int
) -> ChartIntent | None:
    """Pick a chart from the result's column shapes, with no model call.

    The safety net for when the model declines or cannot produce a valid
    ChartIntent (common with small or non-JSON-native models). Because the
    choice follows the *data shape*, different questions still yield different
    chart types — a time column trends as a line, two measures scatter, a
    category breakdown bars. Returns None when nothing sensible fits (e.g. a
    single value), which is the correct "no chart" outcome.
    """
    quantitative = [c for c in columns if c.semantic_type == "quantitative"]
    temporal = [c for c in columns if c.semantic_type == "temporal"]
    categorical = [c for c in columns if c.semantic_type in ("nominal", "ordinal")]

    if not quantitative:
        return None
    measure = quantitative[0]

    def axis(col: ResultColumn) -> AxisSpec:
        return AxisSpec(field=col.name, type=_SEM_TO_VEGA.get(col.semantic_type, "nominal"))

    # A time axis reads as a trend.
    if temporal:
        return ChartIntent(chart_type="line", x_axis=axis(temporal[0]), y_axis=axis(measure))

    # Two measures and nothing to group by: show their relationship.
    if len(quantitative) >= 2 and not categorical:
        return ChartIntent(
            chart_type="scatter", x_axis=axis(quantitative[0]), y_axis=axis(quantitative[1])
        )

    # A measure across categories: bars. Flip to horizontal when there are many
    # bars, where long labels stack better vertically.
    if categorical:
        chart_type = "horizontal_bar" if row_count > 8 else "bar"
        return ChartIntent(
            chart_type=chart_type, x_axis=axis(categorical[0]), y_axis=axis(measure)
        )

    return None


def compile_vega_lite(
    intent: ChartIntent, columns: list[ResultColumn], rows: list[list[Any]]
) -> dict[str, Any]:
    names = [c.name for c in columns]
    data = [dict(zip(names, row, strict=True)) for row in rows]

    assert intent.x_axis is not None and intent.y_axis is not None

    def encode(axis: AxisSpec) -> dict[str, Any]:
        enc: dict[str, Any] = {"field": axis.field, "type": axis.type}
        if axis.aggregation != "none":
            enc["aggregate"] = axis.aggregation
        if axis.label:
            enc["title"] = axis.label
        return enc

    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": data},
        "mark": {"type": _MARKS[intent.chart_type], "tooltip": True},
    }

    if intent.chart_type == "pie":
        # An `arc` mark is encoded by angle (the measure) and colour (the
        # category), not by x/y — an x/y encoding renders nothing.
        spec["encoding"] = {
            "theta": encode(intent.y_axis),
            "color": encode(intent.x_axis),
        }
    else:
        x, y = intent.x_axis, intent.y_axis
        if intent.chart_type == "horizontal_bar":
            x, y = y, x
        spec["encoding"] = {"x": encode(x), "y": encode(y)}
        if intent.series is not None:
            spec["encoding"]["color"] = encode(intent.series)

    if intent.title:
        spec["title"] = intent.title
    return spec
