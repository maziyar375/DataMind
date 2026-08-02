"""The contract between the tile editor's chart pickers and `ChartIntent`.

`dashboard_tiles.chart_config` is a JSON column, and the thing that writes it
is a TypeScript file with no compile-time knowledge of the Pydantic model on
this side. There is no error when the two disagree: `_chart_intent` catches the
`ValidationError`, logs it, and returns `None`, so a chart_config the model
refuses is indistinguishable from "Auto" — the tile keeps working and the
user's explicit pick is silently discarded. That silence is why the exact
payloads `components/tile-editor.tsx` builds are pinned here.

The other half is the reverse direction: what the editor is *not* allowed to
express. "Show me the numbers" looks like `chart_type: "none"` and is not — see
`test_a_none_intent_does_not_suppress_the_chart`.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.charts import (
    AxisSpec,
    ChartIntent,
    ChartType,
    compile_vega_lite,
    plan_chart,
    profile_result,
    validate_intent,
)
from app.domain.ports.database import ResultColumn

# Every chart type the editor's picker offers, minus `auto` — which is not a
# type at all: it stores null and asks `plan_chart` to re-decide per result.
EDITOR_CHART_TYPES = [
    "bar", "line", "area", "combo", "scatter", "pie", "heatmap", "histogram",
]

# The types whose x/y are not "the axes", and what the editor labels them. Kept
# here because a mislabelled picker is not a rendering bug — it is a user
# choosing a column for a channel the chart does not have.
EDITOR_AXIS_LABELS = {
    "pie": ("Slices", "Size"),
    "heatmap": ("Rows", "Columns"),
    "histogram": ("Values", None),
    "combo": ("X", "Bars"),
}

# The `Bars` control, which the editor shows for `bar` alone. "auto" is the
# default and is never sent — writing it would store a preference nobody
# expressed — so only these two ever reach the column.
EDITOR_ORIENTATIONS = ["vertical", "horizontal"]

# The `Split` control, shown for a bar or area that has a series. As above,
# the default is never sent.
EDITOR_STACKS = ["grouped", "normalize"]


def editor_payload(
    chart_type: str,
    *,
    series: bool = False,
    orientation: str | None = None,
    stack: str | None = None,
    size: bool = False,
) -> dict[str, Any]:
    """Exactly what `chartConfig()` in `tile-editor.tsx` puts on the wire.

    Keep this function and that one identical. `type` is copied straight from
    the preview column's `semantic_type`, and `aggregation` is always "none"
    because the SQL has already done the aggregating.
    """
    payload: dict[str, Any] = {
        "chart_type": chart_type,
        "x_axis": {"field": "status", "type": "nominal", "aggregation": "none"},
    }
    # A histogram counts its own rows, so the editor sends no y_axis: there is
    # no column to name, and an empty one would fail validation and land the
    # tile on Auto.
    if EDITOR_AXIS_LABELS.get(chart_type, ("X", "Y"))[1] is not None:
        payload["y_axis"] = {"field": "total", "type": "quantitative", "aggregation": "none"}
    if chart_type == "heatmap":
        payload["color"] = {"field": "total", "type": "quantitative", "aggregation": "none"}
    if chart_type == "combo":
        payload["y2_axis"] = {"field": "other", "type": "quantitative", "aggregation": "none"}
    if orientation is not None:
        payload["orientation"] = orientation
    if series:
        payload["series"] = {"field": "region", "type": "nominal", "aggregation": "none"}
        if stack is not None:
            payload["stack"] = stack
    if size:
        payload["size"] = {"field": "weight", "type": "quantitative", "aggregation": "none"}
    return payload


# ── the payload validates ────────────────────────────────────────────────
@pytest.mark.parametrize("chart_type", EDITOR_CHART_TYPES)
def test_every_type_the_picker_offers_round_trips(chart_type: str) -> None:
    intent = ChartIntent.model_validate(editor_payload(chart_type))

    assert intent.chart_type == chart_type
    assert intent.x_axis is not None and intent.x_axis.field == "status"
    if EDITOR_AXIS_LABELS.get(chart_type, ("X", "Y"))[1] is None:
        assert intent.y_axis is None      # a histogram derives its own
    else:
        assert intent.y_axis is not None and intent.y_axis.field == "total"


# The types that offer a Series picker. The rest have already spent the colour
# channel on something else — a pie's slices, a heatmap's measure, a combo's
# two layers — so offering a split there would be a control with nowhere to go.
EDITOR_SPLITTABLE = ["bar", "line", "area", "scatter"]


@pytest.mark.parametrize("chart_type", EDITOR_SPLITTABLE)
def test_the_series_picker_round_trips_too(chart_type: str) -> None:
    intent = ChartIntent.model_validate(editor_payload(chart_type, series=True))

    assert intent.series is not None
    assert intent.series.field == "region"


def test_the_types_that_spend_colour_elsewhere_offer_no_series() -> None:
    """Not a rendering rule — a picker rule, pinned here because the editor is
    the only place it is written down."""
    assert set(EDITOR_SPLITTABLE) | {"pie", "heatmap", "histogram", "combo"} == set(
        EDITOR_CHART_TYPES
    )


def test_the_picker_offers_no_type_the_model_does_not_have() -> None:
    """A renamed `ChartType` member would otherwise reach a tile as Auto."""
    assert set(EDITOR_CHART_TYPES) <= set(ChartType.__args__)  # type: ignore[attr-defined]


@pytest.mark.parametrize("orientation", EDITOR_ORIENTATIONS)
def test_the_orientation_control_round_trips(orientation: str) -> None:
    from app.charts import Orientation

    assert orientation in set(Orientation.__args__)  # type: ignore[attr-defined]
    intent = ChartIntent.model_validate(editor_payload("bar", orientation=orientation))
    assert intent.orientation == orientation


def test_orientation_defaults_to_auto_when_the_editor_omits_it() -> None:
    """Which is what it does for every non-bar type, and for `Bars: Auto`."""
    assert ChartIntent.model_validate(editor_payload("line")).orientation == "auto"


@pytest.mark.parametrize("stack", EDITOR_STACKS)
def test_the_split_control_round_trips(stack: str) -> None:
    from app.charts import StackMode

    assert stack in set(StackMode.__args__)  # type: ignore[attr-defined]
    payload = editor_payload("bar", series=True, stack=stack)
    assert ChartIntent.model_validate(payload).stack == stack


def test_stack_defaults_to_stacked_when_the_editor_omits_it() -> None:
    """The editor shows the control only for a split bar or area, and never
    sends the default — so an unsplit chart's payload has no `stack` key, and
    must land on the value Vega-Lite would have used anyway."""
    assert ChartIntent.model_validate(editor_payload("bar")).stack == "stacked"


def test_the_size_control_round_trips() -> None:
    intent = ChartIntent.model_validate(editor_payload("scatter", size=True))
    assert intent.size is not None and intent.size.field == "weight"


def test_a_tile_stored_before_the_consolidation_still_draws_sideways() -> None:
    """`horizontal_bar` was a `chart_type` until migration 0007 folded it into
    bar + orientation.

    The migration rewrites the rows it can reach. This is the other defence,
    for the rows it cannot: an older API instance writing during a rollout, a
    restored backup. Without it `extra="forbid"` would reject the stored config
    and `_chart_intent` would log it and return None — the tile would keep
    working and quietly stop honouring the pick, which is the exact failure
    this whole module exists to catch.
    """
    legacy = editor_payload("bar")
    legacy["chart_type"] = "horizontal_bar"

    intent = ChartIntent.model_validate(legacy)

    assert intent.chart_type == "bar"
    assert intent.orientation == "horizontal"


def test_the_semantic_types_a_connector_emits_are_all_valid_axis_types() -> None:
    """The editor copies the result column's `semantic_type` into the axis.

    All four connectors classify a value as one of these three. A fifth word
    added to `_semantic_type` in any connector would make every stored chart on
    a column of that type fall back to Auto, quietly.
    """
    from app.charts import AxisType

    emitted = {"quantitative", "temporal", "nominal"}
    assert emitted <= set(AxisType.__args__)  # type: ignore[attr-defined]


def test_an_extra_key_is_refused_which_is_why_the_editor_sends_only_these() -> None:
    """`ChartIntent` is `extra="forbid"`: a stray field is not ignored."""
    payload = editor_payload("bar")
    payload["colour"] = "blue"

    with pytest.raises(ValidationError):
        ChartIntent.model_validate(payload)


def test_a_chart_type_needs_both_axes() -> None:
    """Which is why the picker will not build a config without them, and
    stores Auto instead."""
    with pytest.raises(ValidationError):
        ChartIntent.model_validate({"chart_type": "bar"})


# ── what the editor may not express ──────────────────────────────────────
def _profile() -> Any:
    columns = [
        ResultColumn(name="status", db_type="text", semantic_type="nominal"),
        ResultColumn(name="total", db_type="numeric", semantic_type="quantitative"),
    ]
    rows: list[list[Any]] = [["new", 10.0], ["paid", 22.0], ["shipped", 7.0]]
    return profile_result(columns, rows, truncated=False), columns, rows


def test_a_none_intent_does_not_suppress_the_chart() -> None:
    """"Table only" is a tile type, not a chart intent.

    Storing `chart_type: "none"` reads like "draw nothing" and does the
    opposite: `validate_intent` refuses it, so `plan_chart` falls through to
    the heuristic and draws whatever the shape suggests. The editor therefore
    maps its "Table only" option to `tile_type = TABLE`, which is the only
    statement that actually means "show me the numbers".
    """
    profile, _, _ = _profile()
    declined = ChartIntent(chart_type="none")

    assert validate_intent(declined, profile)[0] is False

    plan = plan_chart(profile, declined)
    assert plan.intent is not None
    assert plan.source == "heuristic"


def test_a_picked_type_survives_a_shape_that_fits() -> None:
    """The user's pick is honoured when the data can carry it — the demotion
    path exists for the picks it cannot."""
    profile, columns, rows = _profile()

    plan = plan_chart(profile, ChartIntent.model_validate(editor_payload("bar")))

    assert plan.intent is not None
    assert plan.intent.chart_type == "bar"
    assert plan.source in ("model", "model_adjusted")
    # And it compiles: a stored intent that plans but will not compile would
    # surface as "the chart could not be built" on every refresh.
    spec = compile_vega_lite(plan.intent, profile, columns, rows)
    assert spec["mark"]["type"] == "bar"


def test_an_axis_naming_a_column_the_result_lacks_is_refused_by_name() -> None:
    """The pickers are populated from the preview's columns for this reason:
    a name the result does not have loses the chart, not the numbers."""
    profile, _, _ = _profile()
    typo = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="statuz", type="nominal"),
        y_axis=AxisSpec(field="total", type="quantitative"),
    )

    ok, reason = validate_intent(typo, profile)

    assert ok is False
    assert reason is not None and "statuz" in reason
