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
    #: Bars only; ignored by every other type. A *fitted* bar intent never
    #: carries "auto" — the plan states which way the chart actually runs.
    orientation: Orientation = "auto"
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

        seen: set[Any] = set()
        for v in non_null:
            try:
                seen.add(v)
            except TypeError:  # JSON/array columns are not hashable
                seen.add(str(v))

        numbers = [f for f in (_as_float(v) for v in non_null) if f is not None]
        profiles.append(
            ColumnProfile(
                name=col.name,
                semantic_type=col.semantic_type,
                distinct=len(seen),
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

    fitted = ChartIntent(
        chart_type=chart_type,
        x_axis=x_axis,
        y_axis=y_axis,
        series=series,
        orientation=orientation,
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

    # Two measures and nothing to group by: show their relationship.
    if len(measures) >= 2:
        return ChartIntent(
            chart_type="scatter",
            x_axis=_axis_for(measures[0]),
            y_axis=_axis_for(measures[1]),
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


def compile_vega_lite(
    intent: ChartIntent,
    profile: ResultProfile,
    columns: Sequence[ResultColumn],
    rows: Sequence[Sequence[Any]],
) -> dict[str, Any]:
    names = [c.name for c in columns]
    kept, order, note = _layout(intent, profile, columns, rows)
    data = [dict(zip(names, row, strict=False)) for row in kept]

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
        category_channel = "x"
        if intent.chart_type == "bar" and intent.orientation == "horizontal":
            x, y = y, x
            category_channel = "y"
        spec["encoding"] = {"x": encode(x), "y": encode(y)}
        # Rank the bars by the measure. Without this Vega orders categories
        # alphabetically, which hides the very thing a "highest/lowest"
        # question asked for.
        if order is not None and spec["encoding"][category_channel]["type"] in (
            "nominal",
            "ordinal",
        ):
            measure_channel = "y" if category_channel == "x" else "x"
            spec["encoding"][category_channel]["sort"] = (
                f"-{measure_channel}" if order == "descending" else measure_channel
            )
        if intent.series is not None:
            spec["encoding"]["color"] = encode(intent.series)

    # What the renderer needs to know that the encoding does not say.
    #
    # `usermeta` is Vega-Lite's own free-form slot: the compiler carries it
    # through untouched and never interprets it. The browser sizes horizontal
    # bars per category and scrolls them, so it has to recognise one — and it
    # used to do that by sniffing `mark === 'bar' && encoding.y.type ===
    # 'nominal'`, which is a guess that a stacked bar or a `rect` mark can
    # satisfy by accident. Stating the decision is cheaper than re-deriving it,
    # and it stays right when the mark table grows.
    spec["usermeta"] = {
        "datamind": {"chart_type": intent.chart_type, "orientation": intent.orientation}
    }

    # The reduction is part of what the chart claims, so it is shown, never
    # implied: a capped chart that looks whole is a lie about the data.
    title = intent.title
    if note:
        title = f"{title} — {note}" if title else note[:1].upper() + note[1:]
    if title:
        spec["title"] = title
    return spec
