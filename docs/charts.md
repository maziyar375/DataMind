# Charts

How DataMind decides what picture to draw, what it refuses to draw, and how a
decision becomes a Vega-Lite spec.

Everything here is implemented. The chart system is `backend/app/charts/`
(one module, ~1,500 lines), the renderer is
[`VegaChart.tsx`](../frontend/src/components/VegaChart.tsx), and the picker is
[`chart-picker.tsx`](../frontend/src/components/chart-picker.tsx).

Companion to [pipeline.md](pipeline.md) (where the `chart` node sits),
[dashboards.md](dashboards.md) (how a tile stores a chart) and
[security.md](security.md) (what the chart prompt may say about a result).

---

## 1. The shape of the decision

```
result ──▶ profile_result ──▶ unchartable_reason ──▶ [model proposes] ──▶ plan_chart ──▶ compile_vega_lite
             (measure)            (veto, free)          ChartIntent         (fit/repair)      (Vega-Lite)
```

Three properties hold at every step:

1. **The model proposes; the platform decides.** A `ChartIntent` from the model
   is a *suggestion*. `plan_chart` vetoes what the data cannot support, repairs
   what is salvageable, and falls back to a deterministic heuristic when the
   model fails or returns nonsense.
2. **The veto runs before the model is asked.** `unchartable_reason` is pure
   arithmetic over the profile, so a hopeless result costs no tokens and no
   latency — and the reason in the step trail is a fact about the data rather
   than "the model declined".
3. **Charting is fail-open.** This is the deliberate opposite of the SQL guard.
   Any failure yields *no chart*, never a failed run: the answer and the table
   are already correct and persisted.

## 2. The chart types

Eight types plus `none`, and two presentations that are not charts.

| Type | Options | Covers | Vega-Lite mechanism |
|------|---------|--------|---------------------|
| **Bar** | `orientation`, `stack` | ranking, comparison, composition | `mark: bar`, `xOffset` grouped |
| **Line** | — | trend, multi-series trend | `mark: line` |
| **Area** | `stack` | volume trend, composition over time | `mark: area` |
| **Combo** | dual axis | two measures on different units | `layer` + independent y scale |
| **Scatter** | `size` → bubble | correlation, three variables | `mark: point` + `size` |
| **Pie** | `pie \| donut` | parts of a whole (≤ 6) | `layer`: `arc` + `text` labels |
| **Heatmap** | — | dimension × dimension × measure | `mark: rect` |
| **Histogram** | bin count | distribution of one raw measure | `bin` transform |
| **Big number** | delta, sparkline | the single-value result | own renderer, not Vega |
| **Table** | column config | records to read | `ResultTable` |

The picker offers **Auto plus these eight**. `Auto` is not a type — it stores
`chart_config = NULL` and re-decides on every refresh.

**Orientation is not a chart type.** A vertical and a horizontal bar chart are
the same mark, the same comparison and the same reading; only the label budget
differs. Modelling the flip as a second `chart_type` meant the platform's
cardinality rule silently *replaced* the user's pick and then told them their
pick did not fit. As a separate field, `auto` asks the platform to decide and an
explicit pick is honoured.

**Bubble is not a chart type either** — it is a scatter with `size` set.

### Not supported, and why

Each would mean dropping to raw Vega or hand-rolling geometry, against the
"use a well-known, stable component" constraint:

- **Funnel** — no Vega-Lite mark; in practice a descending horizontal bar,
  which Bar already draws.
- **Treemap, sunburst, sankey, chord** — no marks; need raw Vega or a second
  library.
- **Gauge** — no mark, and redundant with Big number.
- **Waterfall** — expressible via window transforms, but fiddly and almost
  never selected correctly by a model.
- **Maps / choropleth** — needs TopoJSON, geo-column detection and a geocoding
  story. A project of its own.
- **Box plot** — has a native mark and was specified, but is not built.
- **Radar, bullet, pyramid, candlestick** — niche, and each is one more thing
  the model can pick wrongly.

## 3. Profiling a result

`profile_result` measures the result before anyone reasons about it. Per column:
name, semantic type, whether it is numeric, whether it is an identifier,
distinct count, whether it is constant, numeric min/max, and for dates the
**temporal grain** (`intraday | daily | monthly | yearly`) and span.

Two derived distinctions carry most of the weight:

- **Measure vs dimension.** A numeric column is not automatically a measure —
  an `order_id` is numeric and means nothing as a bar height. `_ID_NAME`
  recognises identifier naming, and constant columns are excluded.
- **`rows == distinct`** on a dimension is the one available signal for whether
  a column is a group key or a repeated attribute. The histogram and series
  rules hinge on it.

## 4. What is refused, and what is repaired

### Vetoes — no chart at all

`unchartable_reason` returns a sentence the UI shows verbatim:

| Condition | Reason given |
|---|---|
| Fewer than 2 rows | *"A single row is a value, not a chart."* → routed to Big number |
| No numeric column | *"No numeric column to measure."* |
| Every numeric column constant | *"Every row has the same X — a chart would show one flat level."* |
| Only identifiers are numeric | *"The only numeric columns are identifiers, not measures."* |
| One measure, nothing to compare across | not charted, unless the column is a raw distribution (a histogram is a chart of itself) |

### Repairs — a different chart than asked for

`plan_chart` fixes a salvageable intent rather than refusing it, and records
`chart_note` so the tile says what happened:

- Pie past `MAX_PIE_SLICES` (6) → **bar**. Angles stop being comparable well
  before six slices.
- Line over unordered text → **bar**. A line implies continuity that a nominal
  axis does not have.
- Swapped axes, or an axis typed wrongly → corrected against the profile.
- Categories past `MAX_CATEGORY_MARKS` (25) → capped, and the chart is
  **labelled with what was dropped**.
- Bars past `HORIZONTAL_BAR_FROM` (8) under `orientation: auto` → horizontal,
  because labels stack better down the side.
- Heatmap whose grid exceeds `MAX_HEATMAP_CELLS` (400) → refused; past that,
  cells are smaller than the eye resolves.
- Combo whose two measures differ by less than `DUAL_AXIS_RATIO` (10×) →
  refused. That ratio *is* the case for a second axis.
- Histogram with fewer than `MIN_HISTOGRAM_ROWS` (20) observations or
  `MIN_HISTOGRAM_LEVELS` (10) distinct values → refused. DataMind aggregates in
  SQL, so most results are pre-aggregated and a histogram of them is nonsense;
  the veto has to be strict.

An intent naming a column the result no longer has **degrades to the table with
a note** — never an error.

### The constants

All in `charts/__init__.py`, all with the reasoning beside them:

```
MAX_CATEGORY_MARKS   25    bars past this are a texture, not a comparison
MAX_PIE_SLICES        6    angles stop being comparable well before this
MAX_SERIES            8    matches the categorical palette in VegaChart.tsx
HORIZONTAL_BAR_FROM   8    above this, labels stack better down the side
TOOLTIP_FIELDS        8    a hover is read at a glance or not at all
MAX_TIME_TICKS       12    dated labels are wide; a year of months is plenty
MAX_HEATMAP_CELLS   400    past this the cells are smaller than the eye resolves
DUAL_AXIS_RATIO      10    when one measure dwarfs another, share no scale
HISTOGRAM_BINS       20    Vega's target, not a promise; it snaps to round edges
MIN_HISTOGRAM_ROWS   20    fewer observations than this is a list, not a shape
MIN_HISTOGRAM_LEVELS 10    ten repeated values are categories, not a spread
SPARKLINE_POINTS     60    a strip 60px wide has no room for more
PIE_LABEL_MIN_SHARE 3%     thinner than this and two labels print on top of
                           each other; the tail of a ranked pie is where those
                           slices always are
```

The pie's two radii are in that file too, as Vega *expressions* rather than
numbers (`min(width, height) / 2`, and `0.68` of it for the labels): the same
spec is drawn at a tile's size, a chat column's and the page's, so the labels
have to follow the pie into whatever box it lands in.

## 5. Why the model keeps the choice

There is no deterministic router. The model proposes, the platform vetoes and
repairs, the heuristic catches failure.

A router would take a judgement the model makes well — *this question is about
a share, so normalize* — and replace it with a shape lookup that cannot read
the question at all. Two results with identical shapes want different charts
depending on what was asked.

What actually costs accuracy is asking the model to choose **without telling it
the things the decision turns on**. So `ResultProfile.describe()` carries the
facts each rule is written in terms of: temporal grain and span *length*, the
range ratio between measures, the prospective cell count against the heatmap
budget, `rows == distinct` per dimension, and what the mark budget will drop.

### Prompt/type parity

The rule this leaves behind: **a chart type is not "added" when the compiler
draws it.** It is added when `CHART_SYSTEM` describes when to pick it and when
not to, *and* `describe()` carries the facts that rule is stated in terms of.
Any change to `ChartType`, `_fit`, or a threshold constant is unfinished until
both are updated. A bullet describing behaviour the code no longer has is a bug
in the prompt.

### What the prompt may say — and may not

The chart block is gated by the connection's disclosure policy, via
`describe(policy)` mirroring `HintBudget.from_policy` and defaulting to the
narrowest.

| Fact | Nature | `NONE` / `AGGREGATE` | `SAMPLE` / `FULL` |
|---|---|---|---|
| column name, semantic type | schema, not result | ✅ | ✅ |
| distinct count, row count, `rows == distinct` | a count | ✅ | ✅ |
| cell count, truncation note, mark budget | platform arithmetic | ✅ | ✅ |
| temporal grain, span **length** | a shape | ✅ | ✅ |
| range **ratio** between measures | derived, dimensionless | ✅ | ✅ |
| `min` / `max` **values** | a row value | ❌ withheld | ✅ `FULL` only |
| date first/last | a row value | ❌ never | ❌ never |

The narrow column costs the decision nothing: every rule is written in counts,
ratios and grain, and no rule asks what the largest revenue *is*.
`DUAL_AXIS_RATIO` in particular is better served by the ratio than by two
endpoint pairs — which is why this is the more useful form, not a trade-off.

> `PROMPT_VERSION` does **not** move for chart-prompt changes. The eval scores
> generated SQL, and nothing on the SQL-producing path changes; moving it would
> invalidate every historical run comparison for a change no run's SQL can see.
> Same convention as the `CLARIFY_SYSTEM` note in `pipeline/prompts`.

## 6. Compiling to Vega-Lite

`compile_vega_lite` emits more than `{field, type}`, because the defaults are
wrong often enough to matter:

- **Stack mode** → `stack: "zero" | "normalize" | null`, plus `xOffset` for
  grouped bars.
- **Temporal axis format**, derived from the data's own grain — `%b %Y` for
  month starts, `%b %d, %Y` daily, `%Y` yearly — with `labelOverlap`. Without
  an explicit format Vega falls back to D3 multi-scale time formatting and
  labels the January tick with a bare year, which is the "why is 2025 on my
  axis" bug.
- **Number formatting** on quantitative axes and tooltips: SI units and
  thousands separators rather than raw floats.
- **An explicit mark hint** written into the spec, so the renderer does not have
  to sniff the mark back out of it.
- **Numbers on the pie itself.** A pie is the one chart with no axis to carry
  its values, so before this it said them only through the tooltip — and the
  printed report, where a chart has to stand alone, has no hover. The arcs are
  now a layer of two: the wedges, then the measure written across each one.
  Three details are load-bearing. `theta` is explicitly `stack: true`, which
  puts theta *and* theta2 on the text layer and so lands each label at the
  middle of its own slice; `color` therefore stays in the *shared* encoding,
  because split between the layers the two stacks come out in different orders
  and every label lands on a neighbour's wedge. The format carries an explicit
  precision (`.3~s`, not the axis's bare `~s`) since nothing supplies one to a
  `format`, and d3's default is six significant digits — `1.24732M` across a
  wedge. And a slice under `PIE_LABEL_MIN_SHARE` of the whole goes unlabelled,
  flagged per row by the compiler rather than filtered out of the layer:
  dropping the row would change that layer's stacking and move every other
  label. Labels are placed here and **painted in the browser** — see §8.

`color` and `series` are separate fields on purpose. Both map to the same Vega
channel and do different jobs: `series` colours by *identity* (one hue per
region, categorical palette), `color` colours by *magnitude* (one ramp). Vega
picks the scale family — and therefore the palette — from the encoding type, so
conflating them is how a chart ends up with eight unrelated hues standing for a
quantity.

## 7. Big number

A single-row result is the canonical KPI, and the veto that calls it
unchartable is correct — the outcome was what was wrong. It is now a `KPI`
artifact carrying value, label, optional comparison delta and sparkline series.

It is planned on the **backend** (`plan_kpi`): which column, how the figure is
written, whether extra rows are a comparison or clutter. One renderer,
[`Kpi`](../frontend/src/components/ui.tsx), serves both a chat turn and a
dashboard `METRIC` tile — which is what stops the two from disagreeing about
the same number.

**Direction is drawn, never coloured.** Green-for-up is a judgement the data
does not carry: a rising refund rate is not good news, and the backend cannot
know which metric this is. The delta shows ▲ ▼ — with a semantic colour pair
deferred until it can be measured against the palette rather than guessed at.

## 8. Colour

The palette in [`VegaChart.tsx`](../frontend/src/components/VegaChart.tsx) is
**measured, not chosen**: OKLab ΔE separation, Machado-2009 CVD simulation,
contrast against the chart's own surface, per-mode accent anchoring. The
numbers are in that file's header, and
[`palette.test.ts`](../frontend/src/components/palette.test.ts) re-checks them
(`npm run test:palette`).

A free hex picker destroys all of that silently, so there isn't one.

**Ink on a mark is chosen per mark, not per chart.** The pie's slice numbers
are the first label the product draws *on top of* a fill, and no single ink
serves: the categorical slots vary in lightness on purpose (that is the CVD
mechanism), so white bottoms out at 3.3:1 in dark and 3.1:1 in light. The
compiler leaves the labels unpainted and `VegaChart` gives them a `contrast()`
expression over the two `CATEGORY_INK` values, so each label takes whichever
reads better against the slice under it — resolved at draw time, and so
correct through a theme flip without either side knowing which wedge got which
hue. The worst slot then measures 5.1:1 dark, 4.6:1 light, both asserted in
`palette.test.ts`.

The dashboard settings drawer offers exactly **one** palette, "Default
(measured)", and says why. The column, the API field and the picker are all in
place for more — but adding one means re-running the validator **in both
themes** first. A hue swapped by eye is how a palette quietly stops being
readable.

## 9. Tests

```bash
make test                  # includes test_charts.py — profile, veto, fit, compile
npm run test:palette       # the palette's own measurements
```

`test_charts.py` asserts at the compile level, on the emitted spec — these are
pure-function tests, cheap and exact. `test_tile_charts.py` pins every
`chart_config` payload the editor can build against `ChartIntent`, which is
`extra="forbid"`: a config it refuses is **indistinguishable from Auto**, so
the user's explicit pick would vanish without an error.

`expected_chart_type` on `GoldRecord` is inert — declared, never read by any
scorer. Any future work wanting chart accuracy measured has to write that
metric first.
