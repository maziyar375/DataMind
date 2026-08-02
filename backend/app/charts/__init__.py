"""Chart intent → shape fit → Vega-Lite.

The model proposes a constrained `ChartIntent`; it never emits a Vega-Lite spec
directly, and — the point of this module — its proposal is never trusted on
*shape*. Column names existing is not enough: a bar chart of 1,000 categories,
a pie of 200 slices, a line across unordered text, or any chart of a column
whose every row holds the same value are all well-formed intents that produce a
useless picture.

So the same posture the SQL guard takes with generated SQL applies here, one
notch softer: the model proposes, a deterministic policy disposes.
`plan_chart` is that policy. It measures the result first (`profile_result`),
declines outright when the data cannot say anything (`unchartable_reason`),
repairs a salvageable intent (`_fit`), and falls back to a shape heuristic when
the model's pick cannot be repaired. The difference from the guard is the
failure direction: a rejected chart costs a picture, not an answer, so every
path here ends in "no chart", never in a failed run.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.ports.database import ResultColumn

# ── budgets ──────────────────────────────────────────────────────────────
# What a reader can actually take in, not what Vega will happily draw.
MAX_CATEGORY_MARKS = 25   # bars past this are a texture, not a comparison
MAX_PIE_SLICES = 6        # angles stop being comparable well before this
MAX_SERIES = 8            # matches the categorical palette in VegaChart.tsx
HORIZONTAL_BAR_FROM = 8   # above this, labels stack better down the side
TOOLTIP_FIELDS = 8        # a hover is read at a glance or not at all
MAX_TIME_TICKS = 12        # dated labels are wide; a year of months is plenty

# Columns whose *name* says they are an identifier: never a measure, however
# numeric they look. Charting `customer_id` as a quantity is the classic
# position-based mistake ("the first numeric column must be the measure").
_ID_NAME = re.compile(
    r"(^|_)(id|ids|no|num|number|code|key|pk|fk|uuid|guid|zip|zipcode|postcode|year)$"
)


ChartType = Literal["line", "bar", "area", "scatter", "pie", "none"]
AxisType = Literal["quantitative", "temporal", "nominal", "ordinal"]

# Which way the bars run. Not a chart type: a vertical and a horizontal bar
# chart are the same mark, the same comparison and the same reading — only the
# label budget differs. Modelling the flip as a second `chart_type` meant the
# platform's own cardinality rule silently *replaced* whatever the user picked,
# and then told them their pick "does not fit this result". Here the two are
# separable: `auto` asks the platform to decide, and an explicit pick is kept.
Orientation = Literal["auto", "vertical", "horizontal"]

# How a split shares the space. Only meaningful with a `series` — one series
# has nothing to stack against — and only for the marks that have area to
# divide, so lines and points ignore it.
#
# "stacked" is the default because it is what Vega-Lite already did: a bar or
# area with a colour channel stacks unless told otherwise, so an intent that
# says nothing compiles to the spec it compiled to before this field existed.
# The other two were simply unreachable, which is the gap that mattered — every
# comparable tool treats grouped and 100% as first-class.
StackMode = Literal["stacked", "grouped", "normalize"]


class AxisSpec(BaseModel):
    field: str
    type: AxisType = "nominal"
    aggregation: Literal["sum", "avg", "min", "max", "count", "none"] = "none"
    label: str | None = None


class ChartIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: ChartType = "none"
    x_axis: AxisSpec | None = None
    y_axis: AxisSpec | None = None
    series: AxisSpec | None = None
    #: Scatter only: a third measure, read as the mark's area. A bubble chart
    #: is a scatter with this set, not a chart type of its own.
    size: AxisSpec | None = None
    #: Bars only; ignored by every other type. A *fitted* bar intent never
    #: carries "auto" — the plan states which way the chart actually runs.
    orientation: Orientation = "auto"
    #: Bars and areas with a `series`; ignored otherwise.
    stack: StackMode = "stacked"
    title: str | None = Field(default=None, max_length=120)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_horizontal_bar(cls, data: Any) -> Any:
        """Read the pre-consolidation spelling as bar + horizontal.

        Two sources still produce it: a `dashboard_tiles.chart_config` row the
        migration did not reach (an older API instance writing during a rollout,
        a restored backup), and a model whose priors are stronger than the
        prompt. Both used to fail `extra="forbid"` and land as Auto, throwing
        away a pick this class can perfectly well understand.
        """
        if isinstance(data, dict) and data.get("chart_type") == "horizontal_bar":
            data = {**data, "chart_type": "bar", "orientation": "horizontal"}
        return data

    @model_validator(mode="after")
    def _axes_required(self) -> ChartIntent:
        if self.chart_type != "none" and (self.x_axis is None or self.y_axis is None):
            raise ValueError("x_axis and y_axis are required unless chart_type is 'none'")
        return self


_MARKS = {
    "line": "line", "bar": "bar",
    "area": "area", "scatter": "point", "pie": "arc",
}

_SEM_TO_VEGA: dict[str, AxisType] = {
    "quantitative": "quantitative",
    "temporal": "temporal",
    "ordinal": "ordinal",
    "nominal": "nominal",
}


# ── the result profile ───────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """What one result column actually contains, not just what type it is.

    Cardinality is the fact every chart decision turns on and the one the
    column type does not carry: `region` and `customer_name` are both nominal
    text, but one is six bars and the other is a thousand.
    """

    name: str
    semantic_type: str
    distinct: int
    non_null: int
    minimum: float | None = None
    maximum: float | None = None

    @property
    def is_numeric(self) -> bool:
        return self.semantic_type == "quantitative"

    @property
    def is_temporal(self) -> bool:
        return self.semantic_type == "temporal"

    @property
    def is_categorical(self) -> bool:
        return self.semantic_type in ("nominal", "ordinal")

    @property
    def is_constant(self) -> bool:
        """One distinct value (or none): there is nothing to compare."""
        return self.distinct <= 1

    @property
    def is_id_like(self) -> bool:
        return bool(_ID_NAME.search(self.name.lower()))

    def describe(self) -> str:
        parts = [f"{self.distinct} distinct"]
        if self.is_numeric and self.minimum is not None and self.maximum is not None:
            parts.append(
                f"min {_fmt_number(self.minimum)}, max {_fmt_number(self.maximum)}"
            )
        if self.is_constant:
            parts.append("SAME VALUE IN EVERY ROW")
        return f"- {self.name} ({self.semantic_type}; {'; '.join(parts)})"


@dataclass(frozen=True, slots=True)
class ResultProfile:
    row_count: int
    columns: tuple[ColumnProfile, ...]
    truncated: bool = False

    def get(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)

    def describe(self) -> str:
        """The column block the chart prompt shows the model.

        Types alone let the model pick a bar chart for a thousand categories,
        because nothing in the prompt said there were a thousand. Cardinality,
        range and the constant flag are what make "none" a reachable answer.
        """
        return "\n".join(c.describe() for c in self.columns)


def _distinct(values: Sequence[Any]) -> int:
    """How many different values, counting the unhashable ones too.

    JSON and array columns come back as dicts and lists, which a set refuses.
    Falling back to their text form counts them as a reader would.
    """
    seen: set[Any] = set()
    for value in values:
        try:
            seen.add(value)
        except TypeError:
            seen.add(str(value))
    return len(seen)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _fmt_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.2f}"


def profile_result(
    columns: Sequence[ResultColumn],
    rows: Sequence[Sequence[Any]],
    *,
    truncated: bool = False,
) -> ResultProfile:
    """Measure the result once, so every downstream decision shares one view."""
    profiles: list[ColumnProfile] = []
    for i, col in enumerate(columns):
        values = [row[i] for row in rows if i < len(row)]
        non_null = [v for v in values if v is not None]

        numbers = [f for f in (_as_float(v) for v in non_null) if f is not None]
        profiles.append(
            ColumnProfile(
                name=col.name,
                semantic_type=col.semantic_type,
                distinct=_distinct(non_null),
                non_null=len(non_null),
                minimum=min(numbers) if numbers else None,
                maximum=max(numbers) if numbers else None,
            )
        )
    return ResultProfile(
        row_count=len(rows), columns=tuple(profiles), truncated=truncated
    )


# ── what can be charted at all ───────────────────────────────────────────
def _measure_candidates(profile: ResultProfile) -> list[ColumnProfile]:
    """Numeric columns that could carry meaning as a magnitude.

    Excludes identifiers by name and columns that never vary — the two things
    that look like a measure to a position-based rule and to a model reading
    only column types.
    """
    return [
        c for c in profile.columns
        if c.is_numeric and not c.is_constant and not c.is_id_like
    ]


def _dimension_candidates(profile: ResultProfile) -> list[ColumnProfile]:
    return [
        c for c in profile.columns
        if (c.is_categorical or c.is_temporal) and not c.is_constant
    ]


def unchartable_reason(profile: ResultProfile) -> str | None:
    """Why this result cannot be charted at all, or None if it can.

    Checked before the model is asked, so a hopeless result costs no tokens and
    no latency — and so the reason shown in the step trail is a fact about the
    data rather than "the model declined".
    """
    if profile.row_count < 2:
        return "A single row is a value, not a chart."

    numeric = [c for c in profile.columns if c.is_numeric]
    if not numeric:
        return "No numeric column to measure."

    measures = _measure_candidates(profile)
    if not measures:
        flat = [c for c in numeric if c.is_constant]
        if flat:
            return (
                f"Every row has the same {flat[0].name} — "
                "a chart would show one flat level."
            )
        return "The only numeric columns are identifiers, not measures."

    if not _dimension_candidates(profile) and len(measures) < 2:
        return "No second column to compare the measure across."
    return None


# ── validation & fitting ─────────────────────────────────────────────────
def validate_intent(
    intent: ChartIntent, profile: ResultProfile
) -> tuple[bool, str | None]:
    """The name gate: does this intent reference columns the result has?

    Deliberately separate from `_fit`, which judges shape. A name failure means
    the model hallucinated a column; a shape failure means it misread the data.
    """
    if intent.chart_type == "none":
        return False, "The model declined to chart this result."
    for axis in (intent.x_axis, intent.y_axis, intent.series):
        if axis is not None and profile.get(axis.field) is None:
            return False, f"Chart referenced unknown column {axis.field!r}."
    return True, None


def _bar_orientation(distinct: int) -> Orientation:
    """Which way bars run when nobody has said. Past a handful of categories
    the labels stack better down the side than angled under the axis."""
    return "horizontal" if distinct > HORIZONTAL_BAR_FROM else "vertical"


def _axis_for(col: ColumnProfile, template: AxisSpec | None = None) -> AxisSpec:
    """An axis whose `type` is the column's real semantic type.

    A model that labels a date column "nominal" turns a trend into a row of
    unordered ticks, so the platform overrides the type it was given rather
    than trusting the label.
    """
    return AxisSpec(
        field=col.name,
        type=_SEM_TO_VEGA.get(col.semantic_type, "nominal"),
        aggregation=template.aggregation if template else "none",
        label=template.label if template else None,
    )


def _fit(
    intent: ChartIntent, profile: ResultProfile
) -> tuple[ChartIntent | None, bool]:
    """Repair an intent against the data's real shape.

    Returns `(fitted, changed)`, or `(None, False)` when the intent is beyond
    repair and the caller should fall back. Every rule here answers the same
    question — would a reader learn anything from this picture? — and prefers
    demotion (pie → bar, line → bar) over rejection, because a legible chart of
    the wrong family still shows the numbers.
    """
    assert intent.x_axis is not None and intent.y_axis is not None
    px = profile.get(intent.x_axis.field)
    py = profile.get(intent.y_axis.field)
    if px is None or py is None:
        return None, False

    changed = False
    chart_type = intent.chart_type
    x_tpl, y_tpl = intent.x_axis, intent.y_axis

    if chart_type == "scatter":
        if not (px.is_numeric and py.is_numeric):
            return None, False
        if px.is_constant or py.is_constant:
            return None, False
    else:
        # Category/time on x, measure on y. A model that swaps them is common
        # enough to be worth fixing rather than discarding.
        if not py.is_numeric and px.is_numeric:
            px, py = py, px
            x_tpl, y_tpl = y_tpl, x_tpl
            changed = True
        if not py.is_numeric:
            return None, False
        # Nothing to compare: one bar, or a thousand bars all the same height.
        if py.is_constant or px.is_constant:
            return None, False
        if py.is_id_like:
            return None, False

        # Bars and slices are *per category*: a continuous x has no categories
        # to be per, so Vega bins or gradients it into something nobody asked
        # for. Reject and let the heuristic pick a form that suits two
        # measures — usually a scatter.
        if chart_type in ("bar", "pie") and px.is_numeric:
            return None, False

        if chart_type == "pie":
            # A pie reads parts of a whole: too many slices, or any negative
            # part, and the angles stop meaning anything.
            if px.distinct > MAX_PIE_SLICES or (py.minimum is not None and py.minimum < 0):
                chart_type = "bar"
                changed = True
        # A line between unordered categories draws a continuity that does not
        # exist in the data.
        elif chart_type in ("line", "area") and px.semantic_type == "nominal":
            chart_type = "bar"
            changed = True

    x_axis = _axis_for(px, x_tpl)
    y_axis = _axis_for(py, y_tpl)
    if x_axis.type != x_tpl.type or y_axis.type != y_tpl.type:
        changed = True

    # The SQL already aggregated when there is one row per category; a further
    # roll-up there is a no-op at best and a double count at worst.
    if px.distinct == profile.row_count and y_axis.aggregation != "none":
        y_axis = y_axis.model_copy(update={"aggregation": "none"})
        changed = True
    if x_axis.aggregation != "none":
        x_axis = x_axis.model_copy(update={"aggregation": "none"})
        changed = True

    # A pie's "x" is a colour channel, not a position one — same discreteness
    # rule as `series` below.
    if chart_type == "pie" and x_axis.type not in ("nominal", "ordinal"):
        x_axis = x_axis.model_copy(update={"type": "ordinal"})
        changed = True

    # Resolve the orientation once, here, so the plan *states* which way the
    # chart runs and nothing downstream re-derives it from the encoding.
    #
    # An explicit pick survives — that is the whole point of separating it from
    # the type — but only when the user asked for bars in the first place. An
    # orientation riding along on a pie that got demoted to bars was never a
    # choice about bars, so it is re-decided rather than obeyed. Note what is
    # deliberately absent: no cardinality veto on an explicit pick. Category
    # charts are already capped at `MAX_CATEGORY_MARKS`, so the worst an
    # awkward pick can produce is 25 angled labels, and overriding the user to
    # spare them that is exactly the behaviour this change exists to remove.
    orientation: Orientation = "auto"
    if chart_type == "bar":
        asked = intent.orientation if intent.chart_type == "bar" else "auto"
        orientation = asked if asked != "auto" else _bar_orientation(px.distinct)

    series: AxisSpec | None = None
    if intent.series is not None:
        ps = profile.get(intent.series.field)
        # A legend with dozens of entries is noise, and colouring by the axis
        # it is already split by says nothing.
        if (
            ps is not None
            and ps.name not in (x_axis.field, y_axis.field)
            and 1 < ps.distinct <= MAX_SERIES
        ):
            series = _axis_for(ps, intent.series)
            # Colour on a split is *identity*, which is always discrete. Left
            # continuous — a split by year, by rating — Vega picks a different
            # scale family (`ramp`) with a gradient legend, and that family
            # carries a different default palette, which is how one product
            # ends up with purple charts and blue ones. Ordinal keeps the
            # reading order of a numeric split and one set of colours.
            if series.type not in ("nominal", "ordinal"):
                series = series.model_copy(update={"type": "ordinal"})
                changed = True
        else:
            changed = True

    # A third measure read as the mark's area — what makes a scatter a bubble
    # chart. Same disqualifications as any other measure (an id is not a
    # magnitude, a constant is not a comparison), plus one of its own: a column
    # already carrying a position says nothing extra by also carrying an area.
    size: AxisSpec | None = None
    if intent.size is not None:
        ps = profile.get(intent.size.field)
        if (
            chart_type == "scatter"
            and ps is not None
            and ps.is_numeric
            and not ps.is_constant
            and not ps.is_id_like
            and ps.name not in (x_axis.field, y_axis.field)
        ):
            size = _axis_for(ps, intent.size)
        else:
            changed = True

    # Stacking needs something to stack: a split, and a mark with area to
    # divide. Where it cannot apply it is reset rather than carried, so a
    # stored intent never claims a layout the chart does not have. That is not
    # an *adjustment* — the picture is identical either way — so `changed`
    # stays where it is and the tile reports nothing.
    stack: StackMode = "stacked"
    if series is not None and chart_type in ("bar", "area"):
        stack = intent.stack

    fitted = ChartIntent(
        chart_type=chart_type,
        x_axis=x_axis,
        y_axis=y_axis,
        series=series,
        size=size,
        orientation=orientation,
        stack=stack,
        title=intent.title,
    )
    return fitted, changed


def heuristic_intent(profile: ResultProfile) -> ChartIntent | None:
    """Pick a chart from the result's shape, with no model call.

    Both the fallback for when the model declines or hallucinates, and the
    reference for what "a sensible default" means. It chooses columns by what
    they *contain* — a non-identifier measure that actually varies, a dimension
    that actually splits the rows — rather than by their position in the
    SELECT, which is how an id column ends up plotted as a quantity.
    """
    measures = _measure_candidates(profile)
    if not measures:
        return None
    # Rightmost measure: SQL convention puts the thing being measured last.
    measure = measures[-1]

    temporal = [c for c in profile.columns if c.is_temporal and not c.is_constant]
    categorical = [
        c for c in profile.columns
        if c.is_categorical and not c.is_constant and c.name != measure.name
    ]

    # A time axis reads as a trend, optionally split by a small dimension.
    if temporal:
        series = next((c for c in categorical if 1 < c.distinct <= MAX_SERIES), None)
        return ChartIntent(
            chart_type="line",
            x_axis=_axis_for(temporal[0]),
            y_axis=_axis_for(measure),
            series=_axis_for(series) if series is not None else None,
        )

    # A measure across categories: bars. The orientation is left to `_fit`,
    # which every path through `plan_chart` runs afterwards — deciding it twice
    # is how the two paths drift apart.
    if categorical:
        return ChartIntent(
            chart_type="bar",
            x_axis=_axis_for(categorical[0]),
            y_axis=_axis_for(measure),
        )

    # Two measures and nothing to group by: show their relationship. A third
    # goes to the mark's area rather than being dropped — the reader gets it
    # for free, and the alternative is a chart that silently ignores a column.
    if len(measures) >= 2:
        return ChartIntent(
            chart_type="scatter",
            x_axis=_axis_for(measures[0]),
            y_axis=_axis_for(measures[1]),
            size=_axis_for(measures[2]) if len(measures) >= 3 else None,
        )
    return None


@dataclass(frozen=True, slots=True)
class ChartPlan:
    """The decision: an intent to draw, or a reason there is nothing to draw."""

    intent: ChartIntent | None
    source: str            # model | model_adjusted | heuristic | none
    reason: str | None = None


def plan_chart(
    profile: ResultProfile, suggestion: ChartIntent | None = None
) -> ChartPlan:
    """Decide what to draw for this result. The one entry point.

    Order matters: the data's own veto comes first (no model opinion can make a
    constant column interesting), then the model's suggestion if it survives
    fitting, then the deterministic heuristic — itself fitted, so one set of
    rules governs both paths.
    """
    blocked = unchartable_reason(profile)
    if blocked is not None:
        return ChartPlan(None, "none", blocked)

    if suggestion is not None and validate_intent(suggestion, profile)[0]:
        fitted, changed = _fit(suggestion, profile)
        if fitted is not None:
            return ChartPlan(fitted, "model_adjusted" if changed else "model")

    guess = heuristic_intent(profile)
    if guess is not None:
        fitted, _ = _fit(guess, profile)
        if fitted is not None:
            return ChartPlan(fitted, "heuristic")

    return ChartPlan(None, "none", "No chart fits this result's shape.")


# ── how a value is written on an axis ────────────────────────────────────
# Vega will label an axis without being told how, and both of its defaults are
# wrong often enough to be worth overriding.
def _temporal_axis(values: Sequence[Any]) -> dict[str, Any] | None:
    """A complete time axis — format *and* tick placement — from the data grain.

    Left to itself, Vega labels a temporal axis with D3's *multi-scale* time
    format, which writes each tick in the largest unit that changes there. On a
    monthly series that reads "September, November, **2025**, March": the
    January tick is labelled with its year rather than its month, because
    January is where the year turns over. Not a stray value and not wrong —
    just an axis that changes units halfway along, which nobody reads that way.
    It also leaves a bare "September" saying nothing about *which* September,
    and any series long enough to contain a January spans more than one year by
    definition.

    **The format alone is not the fix, and shipping it alone would swap one bug
    for a worse one.** Ticks are placed before they are labelled, and Vega
    spaces them for the pixels available, not for the data's grain: a
    three-month series gets four ticks inside every month, and `%b %Y` renders
    that as `Jan 2025, Jan 2025, Jan 2025, Jan 2025, Feb 2025`. Measured, not
    reasoned about. So the interval is pinned to the grain as well, and the
    step is chosen to keep the count near `MAX_TIME_TICKS` — which is what
    makes a five-year daily series land on ~12 ticks instead of 1,800.

    Returns None when the column holds something other than dates — a connector
    handing back pre-formatted strings, say — because a time format applied to
    a string is how an axis goes blank.
    """
    stamps = [v for v in values if isinstance(v, (date, datetime))]
    if not stamps:
        return None

    axis: dict[str, Any] = {
        # Explicit formats are wider than the bare month names Vega picked for
        # itself, and a band scale — a bar chart's own time axis — does not
        # thin labels unless it is told to.
        "labelOverlap": "greedy",
    }

    if any(
        isinstance(v, datetime) and (v.hour or v.minute or v.second or v.microsecond)
        for v in stamps
    ):
        # Sub-day ticks are the one case left to Vega. `vega-time` has no
        # "hour", "minute" or "second" interval — `timeInterval("hour")` is
        # undefined, and Vega then throws inside its own tick generator, which
        # in the browser means `embed()` rejects and the chart vanishes
        # entirely. The format alone is safe: it carries minutes, so ticks
        # collide only on a sub-minute domain.
        axis["format"] = "%b %d, %H:%M"
        return axis

    # Every value on a year boundary is a yearly series; on a month boundary, a
    # monthly (or quarterly) one. Anything else is dated to the day.
    if all(v.month == 1 and v.day == 1 for v in stamps):
        axis["format"], floor = "%Y", "year"
    elif all(v.day == 1 for v in stamps):
        axis["format"], floor = "%b %Y", "month"
    else:
        axis["format"], floor = "%b %d, %Y", "date"

    days = [v.date() if isinstance(v, datetime) else v for v in stamps]
    axis["tickCount"] = {"interval": _tick_interval(floor, (max(days) - min(days)).days)}
    return axis


# Approximate days per tick, finest first. `date` rather than `day`: in Vega's
# time vocabulary those are day-of-month and day-of-week respectively, and only
# the first is a calendar stride.
_TICK_LADDER: tuple[tuple[str, float], ...] = (
    ("date", 1.0), ("week", 7.0), ("month", 30.44), ("quarter", 91.31), ("year", 365.25),
)


def _tick_interval(floor: str, span_days: int) -> str:
    """The finest interval that keeps the tick count readable, never finer than
    the data's own grain.

    Escalating the *interval* rather than stepping one interval is deliberate,
    and the reason is a d3 subtlety worth writing down: `interval.every(n)`
    does not stride by n, it **filters** the interval's values by
    divisibility. `timeDay.every(30)` therefore yields Jan 1, Jan 31, Feb 1,
    Mar 1 — clumped, not regular — because it keeps the days whose day-of-month
    divides by 30 and the month boundary resets the count. Choosing a coarser
    unit gives evenly spaced ticks with no such trap.

    The floor is what keeps labels distinct: ticks never fall between the
    points the data actually has, so a monthly series cannot be ticked daily
    and print "Jan 2025" four times in a row.
    """
    start = next(i for i, (name, _) in enumerate(_TICK_LADDER) if name == floor)
    for name, per_tick in _TICK_LADDER[start:]:
        if span_days / per_tick <= MAX_TIME_TICKS:
            return name
    return "year"


def _axis_number_format(values: Sequence[Any]) -> str | None:
    """SI notation for a large axis, and *nothing at all* for a small one.

    An axis has room for a shape, not a figure: `1.2M` is read at a glance
    where `1,247,318` is counted. Below the threshold there is nothing to fix —
    Vega already groups thousands and picks a sensible precision per tick — so
    this returns None and stays out of the way.

    That restraint is not a preference, it is a correctness rule, and it was
    measured rather than assumed. An axis format reaches d3 through
    `scale.tickFormat(count, specifier)`, which **fills in a precision when the
    specifier has no type**, and a typeless specifier with a precision formats
    like `g` — so the obvious `","` turns a perfectly good `100, 150, 200` axis
    into `1e+2, 1.5e+2, 2e+2`. The obvious repair, `",d"`, is worse in the
    other direction: forcing integers onto a 0-to-1 axis labels its ticks
    `0, 0, 0, 1, 1, 1`. `~s` carries an explicit type, so it survives the round
    trip intact — which is exactly why it is the only one used here.

    The known wart: d3's SI prefix for 10^9 is `G`, so a billion reads `1.2G`
    rather than the `1.2B` a finance reader expects. Still an enormous
    improvement on ten unseparated digits, and the alternative — a bespoke
    suffix table — is a formatting library nobody asked this module to become.
    """
    numbers = [f for f in (_as_float(v) for v in values) if f is not None]
    if not numbers or max(abs(n) for n in numbers) < 10_000:
        return None
    return "~s"


def _exact_number_format(numbers: Sequence[float]) -> str:
    """Full precision with separators — for tooltips, which have the room."""
    return ",.2f" if any(n != int(n) for n in numbers) else ","


def _tooltip_number_format(values: Sequence[Any]) -> str | None:
    numbers = [f for f in (_as_float(v) for v in values) if f is not None]
    return _exact_number_format(numbers) if numbers else None


# ── compilation ──────────────────────────────────────────────────────────
def _monotonic(values: list[float | None]) -> str | None:
    """"descending" / "ascending" if the rows already arrive ordered by value.

    The query's own ORDER BY is the user's intent — "highest" versus "lowest" —
    so a reduction must keep the end the question asked about, and the chart
    must not re-sort a ranking into the opposite reading.
    """
    seen = [v for v in values if v is not None]
    if len(seen) < 2:
        return None
    if all(a >= b for a, b in zip(seen, seen[1:], strict=False)):
        return "descending"
    if all(a <= b for a, b in zip(seen, seen[1:], strict=False)):
        return "ascending"
    return None


def _layout(
    intent: ChartIntent,
    profile: ResultProfile,
    columns: Sequence[ResultColumn],
    rows: Sequence[Sequence[Any]],
) -> tuple[list[list[Any]], str | None, str | None]:
    """Reduce rows to the mark budget and decide the category sort order.

    Returns `(rows, sort_order, note)`. Continuous charts (line/area/scatter)
    keep every row — a thousand points is a shape, a thousand bars is a smear —
    so only category charts are capped, and the cap is reported in the title
    rather than applied silently.
    """
    all_rows = [list(r) for r in rows]
    assert intent.x_axis is not None and intent.y_axis is not None

    if intent.chart_type in ("line", "area", "scatter"):
        note = f"first {profile.row_count:,} rows" if profile.truncated else None
        return all_rows, None, note

    px = profile.get(intent.x_axis.field)
    names = [c.name for c in columns]
    if px is None or intent.y_axis.field not in names:
        return all_rows, None, None

    m_idx = names.index(intent.y_axis.field)
    measures = [_as_float(r[m_idx]) if m_idx < len(r) else None for r in all_rows]
    order = _monotonic(measures) or "descending"

    budget = MAX_PIE_SLICES if intent.chart_type == "pie" else MAX_CATEGORY_MARKS
    if px.distinct <= budget:
        note = f"all {profile.row_count:,} rows" if profile.truncated else None
        return all_rows, None if px.is_temporal else order, note

    if _monotonic(measures) is not None:
        kept = all_rows[:budget]          # the query already ranked them
    else:
        kept = sorted(
            all_rows,
            key=lambda r: (_as_float(r[m_idx]) if m_idx < len(r) else None) or float("-inf"),
            reverse=True,
        )[:budget]
        order = "descending"

    lead = "top" if order == "descending" else "lowest"
    total = f"{profile.row_count:,}{'+' if profile.truncated else ''}"
    return kept, order, f"{lead} {budget} of {total}"


def _apply_stack(
    encoding: dict[str, Any],
    intent: ChartIntent,
    category_channel: str,
    measure_channel: str,
) -> None:
    """Say how a split shares the space, when Vega's default is not the answer.

    Only bars and areas have area to divide; a split line or scatter is drawn
    once per series whatever this says. "stacked" writes nothing at all, since
    that is already what Vega-Lite does with a colour channel — an intent that
    expresses no preference must compile to the bytes it compiled to before
    this existed.
    """
    if intent.chart_type not in ("bar", "area") or intent.stack == "stacked":
        return

    measure = encoding[measure_channel]
    if intent.stack == "normalize":
        measure["stack"] = "normalize"
        # The axis now runs 0-1 and means proportion, so the measure's own
        # format — currency, SI-abbreviated counts — would be a lie about it.
        measure["axis"] = {"format": "%"}
        return

    # Grouped: stop stacking, then give each series its own slot beside the
    # category rather than on top of it. Without the offset the bars overplot
    # and only the last series drawn is visible.
    measure["stack"] = None
    assert intent.series is not None
    if intent.chart_type == "bar":
        offset = "xOffset" if category_channel == "x" else "yOffset"
        encoding[offset] = {"field": intent.series.field}


def _tooltip(
    intent: ChartIntent,
    columns: Sequence[ResultColumn],
    shown: dict[str, list[Any]],
) -> list[dict[str, Any]] | None:
    """Every column, formatted — or None to keep Vega's own bare listing.

    `mark.tooltip: true` shows all of the datum's fields and formats none of
    them, so a hovered bar reads `1247318.4` and `2025-03-01T00:00:00`. Naming
    the fields is the only way to attach formats, and the cost of naming them
    is that anything unnamed disappears — so this lists *every* result column,
    not only the encoded ones, and the hover keeps saying what it used to say.

    Returns None when an axis aggregates. The tooltip would then be reporting
    raw row values beside a mark that shows their roll-up, which is worse than
    an unformatted number: it is a different number.
    """
    axes = (intent.x_axis, intent.y_axis, intent.series, intent.size)
    if any(a is not None and a.aggregation != "none" for a in axes):
        return None

    # Encoded fields first — what the reader is pointing at should be the first
    # line of what they read — then the rest, up to a hover nobody scrolls.
    encoded = [a.field for a in axes if a is not None]

    def rank(column: ResultColumn) -> int:
        return encoded.index(column.name) if column.name in encoded else len(encoded)

    ordered = sorted(columns, key=rank)

    entries: list[dict[str, Any]] = []
    for column in ordered[:TOOLTIP_FIELDS]:
        axis_type = _SEM_TO_VEGA.get(column.semantic_type, "nominal")
        entry: dict[str, Any] = {"field": column.name, "type": axis_type}
        values = shown.get(column.name, [])
        if axis_type == "temporal":
            # The format only — a tooltip has no ticks to place, and a
            # `tickCount` on this channel is a schema violation.
            config = _temporal_axis(values)
            if config:
                entry["format"] = config["format"]
        elif axis_type == "quantitative":
            fmt = _tooltip_number_format(values)
            if fmt:
                entry["format"] = fmt
        entries.append(entry)
    return entries


def compile_vega_lite(
    intent: ChartIntent,
    profile: ResultProfile,
    columns: Sequence[ResultColumn],
    rows: Sequence[Sequence[Any]],
) -> dict[str, Any]:
    names = [c.name for c in columns]
    kept, order, note = _layout(intent, profile, columns, rows)
    data = [dict(zip(names, row, strict=False)) for row in kept]
    # The values actually plotted, not the whole result: a capped chart's axis
    # is labelled for the 25 bars on it, not the 400 rows behind them.
    shown = {name: [row.get(name) for row in data] for name in names}

    assert intent.x_axis is not None and intent.y_axis is not None

    def encode(axis: AxisSpec, *, positional: bool = True) -> dict[str, Any]:
        enc: dict[str, Any] = {"field": axis.field, "type": axis.type}
        if axis.aggregation != "none":
            enc["aggregate"] = axis.aggregation
        if axis.label:
            enc["title"] = axis.label
        # `axis` is a property of positional channels alone. Setting it on a
        # colour, size or theta channel is not merely ignored — it is a schema
        # violation, and Vega-Lite refuses the whole spec.
        if positional:
            values = shown.get(axis.field, ())
            if axis.type == "temporal":
                config = _temporal_axis(values)
                if config:
                    enc["axis"] = config
            elif axis.type == "quantitative":
                fmt = _axis_number_format(values)
                if fmt:
                    enc["axis"] = {"format": fmt}
        return enc

    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": data},
        "mark": {"type": _MARKS[intent.chart_type]},
    }

    encoding: dict[str, Any] = {}
    spec["encoding"] = encoding

    if intent.chart_type == "pie":
        # An `arc` mark is encoded by angle (the measure) and colour (the
        # category), not by x/y — an x/y encoding renders nothing.
        encoding["theta"] = encode(intent.y_axis, positional=False)
        encoding["color"] = encode(intent.x_axis, positional=False)
    else:
        x, y = intent.x_axis, intent.y_axis
        category_channel = "x"
        if intent.chart_type == "bar" and intent.orientation == "horizontal":
            x, y = y, x
            category_channel = "y"
        measure_channel = "y" if category_channel == "x" else "x"
        encoding["x"] = encode(x)
        encoding["y"] = encode(y)
        # Rank the bars by the measure. Without this Vega orders categories
        # alphabetically, which hides the very thing a "highest/lowest"
        # question asked for.
        if order is not None and encoding[category_channel]["type"] in (
            "nominal",
            "ordinal",
        ):
            encoding[category_channel]["sort"] = (
                f"-{measure_channel}" if order == "descending" else measure_channel
            )
        if intent.series is not None:
            encoding["color"] = encode(intent.series, positional=False)
            _apply_stack(encoding, intent, category_channel, measure_channel)
        if intent.size is not None and intent.chart_type == "scatter":
            encoding["size"] = encode(intent.size, positional=False)

    tooltip = _tooltip(intent, columns, shown)
    if tooltip is not None:
        encoding["tooltip"] = tooltip
    else:
        # No formatting to add, so let Vega show every field of the datum —
        # which is more than an explicit list would have named.
        spec["mark"]["tooltip"] = True

    # What the renderer needs to know that the encoding does not say.
    #
    # `usermeta` is Vega-Lite's own free-form slot: the compiler carries it
    # through untouched and never interprets it. The browser sizes horizontal
    # bars per category and scrolls them, so it has to recognise one — and it
    # used to do that by sniffing `mark === 'bar' && encoding.y.type ===
    # 'nominal'`, which is a guess that a stacked bar or a `rect` mark can
    # satisfy by accident. Stating the decision is cheaper than re-deriving it,
    # and it stays right when the mark table grows.
    #
    # `categories` is the count of *marks along the category axis*, which is
    # not `len(data)` once a series is in play: eight regions over twelve
    # months is 96 rows and twelve columns. The browser gives each column a
    # minimum width and scrolls past a dozen of them, so reading that off the
    # row count made a split chart demand eight times the width it needed.
    #
    # It is always `x_axis` regardless of orientation: the horizontal swap is a
    # rendering detail of this function, and a fitted intent always carries the
    # category on x.
    spec["usermeta"] = {
        "datamind": {
            "chart_type": intent.chart_type,
            "orientation": intent.orientation,
            "stack": intent.stack,
            "categories": _distinct(shown.get(intent.x_axis.field, ())),
        }
    }

    # The reduction is part of what the chart claims, so it is shown, never
    # implied: a capped chart that looks whole is a lie about the data.
    title = intent.title
    if note:
        title = f"{title} — {note}" if title else note[:1].upper() + note[1:]
    if title:
        spec["title"] = title
    return spec
