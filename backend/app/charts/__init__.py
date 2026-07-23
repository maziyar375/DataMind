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


def compile_vega_lite(
    intent: ChartIntent, columns: list[ResultColumn], rows: list[list[Any]]
) -> dict[str, Any]:
    names = [c.name for c in columns]
    data = [dict(zip(names, row, strict=True)) for row in rows]

    assert intent.x_axis is not None and intent.y_axis is not None
    x, y = intent.x_axis, intent.y_axis
    if intent.chart_type == "horizontal_bar":
        x, y = y, x

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
        "encoding": {"x": encode(x), "y": encode(y)},
    }
    if intent.title:
        spec["title"] = intent.title
    if intent.series is not None:
        spec["encoding"]["color"] = encode(intent.series)
    return spec
