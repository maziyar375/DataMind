"""Request/response DTOs: SQL drafts & tile results."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# These are the dashboard's two shared shapes. `TileResultRead` is what a tile
# returns after a refresh *and* what the editor previews with — one shape,
# because a preview that could differ from a refresh is a preview that lies.
class TileErrorRead(BaseModel):
    code: str
    message: str = ""


class TileColumnRead(BaseModel):
    name: str
    db_type: str = ""
    semantic_type: str = "nominal"


class ChartRedrawRequest(BaseModel):
    """Redraw a finished run's result as a different chart type.

    Only the type: a reader picking "heatmap" from a grid has not picked
    columns, and the platform already knows which columns a heatmap of this
    result would use — it is the same choice it would have made itself.
    """

    chart_type: str


class ChartRedrawRead(BaseModel):
    """A recompiled spec, plus the verdicts the picker needs to stay honest.

    The options travel with every response so a reader who has just redrawn a
    chart is looking at a picker describing the same result, without a second
    round trip.

    Nothing here is persisted. A transcript records what a run produced, and
    quietly rewriting yesterday's chart artifact because someone flipped a
    picker today would make the step trail ("bar chart (model)") a lie about
    the row beside it. The new spec lives in the browser for as long as the
    reader is looking at it.
    """

    spec: dict[str, Any] | None = None
    chart_type: str
    reason: str | None = None
    options: list[ChartOptionRead] = Field(default_factory=list)


class ChartOptionRead(BaseModel):
    """Whether one chart type fits a given result, and if not, why not.

    `supported` is computed by asking the real planner for that type and seeing
    whether it comes back unchanged, so it cannot drift from what the compiler
    would actually do. `reason` is prose for a tooltip and decides nothing.

    `columns` is the channel → column map that made the verdict true, and it is
    what keeps "supported" from being a promise about columns the caller then
    does not use.
    """

    chart_type: str
    supported: bool
    reason: str | None = None
    columns: dict[str, str] | None = None


class TileResultRead(BaseModel):
    status: Literal["OK", "ERROR"] = "OK"
    columns: list[TileColumnRead] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    # Not optional: with every tile on its own clock, "as of 14:32" is the only
    # way a reader tells a 30-second tile from the hourly one beside it.
    computed_at: datetime
    vega_spec: dict[str, Any] | None = None
    # Who chose the chart — model | model_adjusted | heuristic | none — and, when
    # the pick was overruled, what happened. A demoted chart says so out loud
    # rather than quietly drawing something else.
    chart_source: str = "none"
    chart_note: str | None = None
    # A `KpiSpec` for a METRIC tile: the value already written out, its label,
    # and whatever comparison the result supported. Decided on this side so a
    # tile and a chat turn showing the same number agree about it.
    kpi: dict[str, Any] | None = None
    error: TileErrorRead | None = None


#: What the editor's type picker is set to while a draft is being made. It is a
#: *hint about the destination*, never a promise — nothing is saved here, and
#: the tile save path validates the real type independently. Optional so a
#: client that does not send it behaves exactly as one written before this
#: existed.
TileTypeHint = Literal["CHART", "TABLE", "METRIC", "TEXT"] | None


class SqlDraftRequest(BaseModel):
    connection_id: UUID
    llm_config_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    # METRIC earns two things: SQL rules that ask for a series rather than a
    # lone figure, and a KPI on the preview so the editor shows the big number
    # it will actually draw.
    tile_type: TileTypeHint = None


class SqlValidateRequest(BaseModel):
    """The hand-written path *and* the "I edited the model's draft" path."""

    connection_id: UUID
    sql: str = Field(min_length=1, max_length=100_000)
    # No prompt on this road, so this buys the preview's KPI alone — which is
    # what lets someone writing their own `SELECT month, SUM(...)` see the
    # delta and the sparkline before saving.
    tile_type: TileTypeHint = None


class SqlDraftRead(BaseModel):
    """A statement, why the guard accepted or refused it, and what it returns.

    `validation_status` is REJECTED for a refused draft and the response is
    still a 200: the editor renders the guard's reasons inline the way the
    metric editor does, and a 4xx would make "the model wrote SQL I can show
    you" indistinguishable from "your request was malformed".
    """

    sql: str
    validation_status: str
    validation_report: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str] = Field(default_factory=list)
    # A `ChartIntent` for the editor's pickers to default from; null when the
    # preview's shape suggests nothing.
    chart_suggestion: dict[str, Any] | None = None
    # Who chose it: `model` / `model_adjusted` when a model read the question,
    # `heuristic` when only the column shape was consulted, null when nothing
    # was decided. The editor pre-selects a chart type for the first two and
    # leaves *Auto* alone for the rest.
    chart_source: str | None = None
    # Per-type verdicts for the picker: `{chart_type, supported, reason}`.
    # Empty means "no opinion" — the editor leaves every type enabled.
    chart_options: list[ChartOptionRead] = Field(default_factory=list)
    preview: TileResultRead | None = None
    question: str | None = None
    llm_config_id: UUID | None = None
