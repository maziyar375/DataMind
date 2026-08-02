# Chart types — consolidation and expansion plan

**Status:** proposal, not yet implemented. Written 2026-08-02.

The goal: a clean, intuitive set of chart types that covers the vast majority
of BI and data-analysis needs, with no two options meaning the same thing.
Measured against Tableau, Power BI, Looker, Metabase, Superset and Grafana.

Everything proposed here is a **native Vega-Lite mark or transform** — no
hand-drawn SVG, no second charting library. That constraint eliminates several
otherwise-tempting chart types, and the exclusions are listed with their
reasons rather than left silent.

---

## Phase 0 — Findings

### The premise, corrected

There aren't many chart types — there are six. `ChartType` in
`backend/app/charts/__init__.py:46` defines `line, bar, horizontal_bar, area,
scatter, pie, none`; the tile editor shows those plus `auto` and `table only`
(`frontend/src/components/tile-editor.tsx:57-66`). Chat has no picker at all —
it is fully automatic.

So the real problem is not a bloated list. It is **one genuine duplicate, one
control that lies to the user, and a set that is too narrow for BI.**

### Genuine redundancy — 3 items

**1. `bar` vs `horizontal_bar` — the same Vega mark, and the pick doesn't stick.**

Both compile to `mark: "bar"` (`charts/__init__.py:76`); only the channel
differs. Worse, `_fit` **overrides the user's choice**:
`charts/__init__.py:351-355` calls `_bar_type(distinct)` and flips to
horizontal above `HORIZONTAL_BAR_FROM = 8`. A user who picks "Bar" for 12
categories gets a horizontal bar *plus* a note saying their pick "does not fit
this result" (`services/query_service.py:433-437`). A control the system
silently reverses is worse than no control.

**2. "Table only" in the chart picker duplicates the `TABLE` tile type.**

The editor's own header comment (`tile-editor.tsx:16-20`) documents that it is
not a chart type — it reroutes to `tile_type`. Two controls, one outcome.

**3. `area` vs `line` — same shape rules, one identical branch**
(`charts/__init__.py:345-350`). Area is a fill variant of line, not a distinct
analysis.

**Recommendation: consolidate 1 and 2. Keep `area` as a separate type.** It is
in every BI tool's core set and it earns its keep the moment stacking exists —
stacked area is a distinct, well-understood reading of composition over time.
Consolidating it would be tidiness at the cost of a standard capability.

`pie` is near-vestigial — `MAX_PIE_SLICES = 6` plus the negative-value rule
demotes most pies to bar (`charts/__init__.py:339-344`) — but keep it. Users
ask for pies by name.

### The bigger gap: capabilities, not types

The most serious shortfall is not a missing chart, it is **stacking**. When
`series` is set, Vega-Lite stacks bars and areas *by default* and nothing in
`compile_vega_lite` can say otherwise. Grouped bars and 100%-stacked are
unreachable. Every tool named above treats grouped/stacked/normalized as a
first-class control. That single gap costs more analytical coverage than
heatmap, histogram and combo put together.

### Blast radius (this constrains sequencing)

| Thing | Why it constrains the plan |
|---|---|
| `chart_config` JSONB on `dashboard_tiles` (`infra/db/models.py:517`) | Stored per tile. Any rename needs an Alembic **data** migration, not just a code change. |
| `expected_chart_type` in `eval/suites/sales_v1.json` + `sales_v1.baseline.json` | A rename invalidates the recorded baseline. Must be re-keyed and re-run, or the eval silently reports regressions that aren't. |
| `PROMPT_VERSION` | Must move whenever `CHART_SYSTEM` changes (`pipeline/prompts/__init__.py:287-323`). |
| `tests/unit/test_charts.py` (582 lines) | The regression net for `_fit` / `plan_chart`. Every phase extends it. |
| `VegaChart.tsx:151` sniffs orientation | `mark === 'bar' && encoding.y?.type === 'nominal'` drives per-bar height and scroll behaviour. Adding stacked-horizontal and `rect` marks makes sniffing fragile — replace with explicit metadata in the compiled spec. |

---

## The target set — 9 chart types + 2 presentations

| # | Type | Options | Covers | Vega-Lite mechanism |
|---|---|---|---|---|
| 1 | **Bar** | orientation `auto\|vertical\|horizontal`; stack `none\|stacked\|normalize` | ranking, comparison, composition, grouped comparison | `mark: bar`, `xOffset` for grouped |
| 2 | **Line** | — | trend, multi-series trend | `mark: line` |
| 3 | **Area** | stack `none\|stacked\|normalize` | volume trend, composition over time | `mark: area` |
| 4 | **Combo** *(new)* | dual axis | measures on different units (revenue + margin %) | `layer` + `resolve.scale.y: independent` |
| 5 | **Scatter** | optional `size` → bubble | correlation, 3-variable comparison | `mark: point` + `size` channel |
| 6 | **Pie** | style `pie\|donut` | parts of a whole (≤6) | `mark: arc`, `innerRadius` |
| 7 | **Heatmap** *(new)* | — | dimension × dimension × measure; cohort/matrix | `mark: rect` |
| 8 | **Histogram** *(new)* | bin count | distribution of one measure | `bin` transform |
| 9 | **Box plot** *(new, optional)* | — | spread / outliers by category | `mark: boxplot` (native) |
| — | **Big number (KPI)** | delta, sparkline | the single-value result | not a chart — own renderer |
| — | **Table** | — | records to read | existing `ResultTable` |

Net picker size: **10 options + Auto**, versus 8 today. Barely more choice, far
more coverage, and no two options mean the same thing.

### Deliberately excluded — and why

Each of these would mean dropping to raw Vega or hand-rolling geometry, which
contradicts the "use a well-known, stable component" constraint:

- **Funnel** — no VL mark. In practice it is a descending horizontal bar, which
  type 1 already draws.
- **Treemap / sunburst / sankey / chord** — no VL marks; need raw Vega or a
  second library.
- **Gauge** — no VL mark, and redundant with Big number.
- **Waterfall** — expressible via window transforms, but fiddly and almost
  never selected correctly by a model. Revisit after the router proves out.
- **Maps / choropleth** — needs TopoJSON, geo-column detection and a geocoding
  story. A project of its own, not a phase.
- **Radar, bullet, pyramid, candlestick** — niche; each is one more thing the
  model can pick wrongly.

---

## The phases

Each phase ships backend + frontend + tests **together**, so the picker is
never out of sync with what the compiler can draw.

### Phase 1 — Consolidate (no new capability)

The cleanup phase. Nothing here adds a chart type.

1. `ChartType` becomes `line | bar | area | scatter | pie | none`; add
   `orientation: Literal["auto","vertical","horizontal"] = "auto"` to
   `ChartIntent`.
2. `_fit`'s `_bar_type` logic moves behind `orientation == "auto"`. An explicit
   pick is **honoured**, not overridden — that is the whole point.
3. Alembic revision: rewrite stored `chart_config` where
   `chart_type = "horizontal_bar"` → `{chart_type: "bar", orientation:
   "horizontal"}`. Forward-compatible read path in
   `dashboard_service._stored_intent` for anything the migration missed.
4. Drop `table only` from `CHART_TYPES`; the tile-type control keeps that job.
   Delete the special case in the editor's save path.
5. `CHART_SYSTEM` loses the `horizontal_bar` bullet and its "do not pre-swap"
   caveat; bump `PROMPT_VERSION`.
6. Re-key `sales_v1.json` expectations; re-run the eval and record a new
   baseline.
7. Replace `VegaChart.tsx:151` mark-sniffing with an explicit hint written into
   the spec by `compile_vega_lite`.

**Verify:** `make test`; `npm run typecheck && npm run build`; a manual
migration round-trip on a tile saved as horizontal bar.

### Phase 2 — Fix the encoder (highest value per line changed)

All in `compile_vega_lite`'s `encode()` (`charts/__init__.py:576-582`), which
currently emits only `{field, type}`:

1. **Stack mode** on the intent → `stack: "zero" | "normalize" | null`, plus
   `xOffset` for grouped bars.
2. **Temporal axis format.** This is the "why is 2025 on my axis" bug — with no
   `format`, Vega falls back to D3 multi-scale time formatting and labels the
   January tick with the bare year. Derive the format from the data's
   granularity (`%b %Y` for month starts, `%b %d, %Y` daily, `%Y` yearly) and
   set `labelOverlap`.
3. **Number formatting** on quantitative axes and tooltips — SI / thousands
   separators. Currently raw.
4. `size` channel for scatter → bubble.

**Verify:** extend `test_charts.py` with compile-level assertions on the
emitted spec. These are pure-function tests — cheap and exact.

### Phase 3 — The three new types

Heatmap, combo, histogram. Each needs: a `ChartType` member, a
`_MARKS`/compile branch, `_fit` rules, a `heuristic_intent` path, a
`CHART_SYSTEM` bullet, a picker entry, and tests.

- **Heatmap** — requires two non-constant dimensions plus a measure. New veto:
  reject when `dim_a.distinct × dim_b.distinct` exceeds a legibility budget
  (~400 cells).
- **Combo** — requires two measures whose ranges differ by more than ~10×. That
  ratio *is* the case for a second axis, and `ColumnProfile` already carries
  min/max, so the check is free.
- **Histogram** — requires a raw, unaggregated numeric column. Note the
  tension: DataMind aggregates in SQL, so most results will not qualify. The
  `_fit` veto must be strict or the model will pick histogram for
  pre-aggregated data and produce nonsense.

Box plot ships here or slips to a follow-up — it is the least-requested of the
four.

### Phase 4 — Big number in chat

Today `unchartable_reason` returns *"A single row is a value, not a chart"*
(`charts/__init__.py:234`) and the turn ends with no visual. The veto is
correct and the outcome is wrong — a single-row result is the canonical KPI.

Turn that dead end into a presentation: a `KPI` artifact kind carrying value,
label, optional comparison delta and sparkline series. It renders in chat *and*
replaces the bespoke `METRIC` tile renderer, so one component serves both
surfaces.

### Phase 5 — Selection strategy (the "which chart" question)

This decides whether the wider set helps or hurts. **Adding four types to
`CHART_SYSTEM` multiplies the ways the model can be wrong.** So invert the
current arrangement.

- Today: model proposes freely → `plan_chart` vetoes/repairs → heuristic
  fallback.
- Proposed: **shape-first, model-second.**

**1. Deterministic shape signature.** Classify the `ResultProfile` with no
model call — measure count, dimension count, dimension kinds, cardinalities,
range ratios:

| Signature | Ranked candidates |
|---|---|
| 1 temporal dim + 1 measure | line → area → bar |
| 1 temporal + 1 measure + small split | line (multi) → stacked area |
| 1 nominal dim (≤6) + 1 measure | bar → pie |
| 1 nominal dim (7–25) + 1 measure | bar (horizontal) |
| 2 nominal dims + 1 measure | heatmap → grouped bar → stacked bar |
| 1 dim + 2 measures, ratio >10× | combo → scatter |
| 0 dims + 2 measures | scatter |
| 1 raw numeric column | histogram → box plot |
| 1 row, 1 measure | **KPI** |
| anything else | none |

**2. The model chooses among candidates only** — a pick from 2–3, plus field
assignment and a title. Answers outside the candidate set are rejected and
rank-1 wins. Shorter prompt, far smaller error surface, same fail-open posture.

**3. `_fit` is unchanged.** It is the guard-analogue for charts and should not
absorb selection logic.

**4. Eval.** Add `expected_chart_type` cases for each signature. The router is
deterministic, so most become plain unit tests rather than model-dependent
evals.

**One decision to flag:** `chart` runs *after* `present` in the pipeline, so
narration cannot reference the picture ("as the chart shows…"). Passing the
plan forward would let prose and chart agree. **Recommendation: leave the order
alone** — the reordering risk is not worth it — and instead ensure
`PRESENT_SYSTEM` never claims anything about a chart that may not exist.

### Phase 6 — Colour

**Do not rebuild the palette.** The set in `VegaChart.tsx:69-98` is already
accent-derived per mode (dark anchors on blue OKLCH 250, light on plum OKLCH
315), dual-mode, and colourblind-validated with recorded ΔE figures. It is the
most rigorously specified thing in the frontend and it is already modern and
on-brand.

What the new types genuinely need and do not have:

1. **A diverging ramp** — heatmaps of change/variance need +/- around a neutral
   midpoint. The `diverging` scale family is currently unset, so Vega falls
   back to its built-in `blueorange` — *exactly* the bug the file's own comment
   describes for `ramp` and `ordinal` at `VegaChart.tsx:196-202`. One product,
   two palettes, again.
2. **A semantic positive/negative pair** for KPI deltas and negative bars. Must
   come from outside the categorical wheel — a delta painted in "series colour
   2" is meaningless.
3. **A palette validator script.** There isn't one. The ΔE and Machado numbers
   in that comment are a manual measurement nobody can re-run, and the file
   *instructs* the next person to re-run it. Add `npm run test:palette`
   asserting the documented gates (adjacent CVD ΔE ≥ 8, normal ΔE ≥ 15, ≥3:1 vs
   surface, monotone L on ramps) so the claims stay true.

New values get validated by that script for **both** modes before landing.

### Phase 7 — The picker UI

Type selection becomes a small icon grid rather than a `<select>` — ten options
with names alone is a guessing game. Options are **filtered by what the current
result can support**: the shape signature from Phase 5 already knows, so an
unsupported type is disabled with a reason on hover, rather than offered and
then demoted with an apology note.

Chat gets a "change chart" affordance for the first time — same component, same
candidate filtering.

---

## Open decisions

A recommendation on each; none blocks starting Phase 1.

1. **Box plot** — in the initial set, or deferred? *Recommend: defer.* Least
   requested of the four; nothing else depends on it.
2. **`area` as a type vs a style flag on line** — *recommend: keep as a type*,
   per Phase 0.
3. **Combo axis assignment** — model-chosen or deterministic (larger-range
   measure to bars)? *Recommend: deterministic.* Two-axis charts are the
   easiest thing to render misleadingly.
4. **Eval baseline** — re-record in Phase 1, or hold the old one and accept
   known-failing cases until Phase 5? *Recommend: re-record in Phase 1*, so
   every later phase is measured against a clean line.

---

## Progress

Tick a phase only when its **whole** row is done — backend, frontend and tests
together. A phase half-landed leaves the picker offering something the compiler
cannot draw, which is worse than not having started it.

| ✔ | Phase | What it delivers | Touches | Done when |
|---|---|---|---|---|
| ☐ | **1. Consolidate** | `horizontal_bar` folded into `bar` + `orientation`; `table only` removed from the chart picker | `charts/__init__.py`, new Alembic revision, `dashboard_service.py`, `prompts/__init__.py`, `tile-editor.tsx`, `VegaChart.tsx`, `sales_v1.json` + baseline | `make test`, `npm run typecheck && npm run build`, migration round-trip on a saved horizontal-bar tile, eval baseline re-recorded |
| ☐ | **2. Encoder** | stack mode, temporal axis format (the "2025" bug), number formatting, `size` channel | `charts/__init__.py` (`encode`, `compile_vega_lite`) | `make test` with new compile-level spec assertions |
| ☐ | **3. New types** | heatmap, combo, histogram (box plot optional) | `charts/__init__.py`, `prompts/__init__.py`, `tile-editor.tsx`, `test_charts.py` | each type has a `_fit` veto, a heuristic path and tests; `make test` |
| ☐ | **4. Big number** | `KPI` artifact; single-row results stop being a dead end; replaces the bespoke `METRIC` renderer | `charts/__init__.py`, `pipeline/nodes/`, `api/schemas.py`, `chat.tsx`, `dashboard.tsx` | a one-row result renders a KPI in **both** chat and a tile, from one component |
| ☐ | **5. Selection** | deterministic shape router; model picks from 2–3 candidates only | `charts/__init__.py` (new router), `prompts/__init__.py`, `eval/suites/` | every signature row in the Phase 5 table has a unit test; eval ≥ baseline |
| ☐ | **6. Colour** | diverging ramp, semantic +/- pair, `npm run test:palette` | `VegaChart.tsx`, `theme/tokens.ts`, new validator script | validator passes for **both** dark and light |
| ☐ | **7. Picker UI** | icon grid, options filtered by what the result supports, "change chart" in chat | `tile-editor.tsx`, `chat.tsx`, `ui.tsx` | an unsupported type is disabled with a reason, never offered then demoted |

**Dependencies:** 1 → 2 → 3 → 5 → 7 is the spine. Phase 4 depends only on 1.
Phase 6 is independent of everything except 3 (the diverging ramp exists for
the heatmap) and can be done at any point after it.

**Log completions here** — date and commit, so the table stays honest:

| Phase | Completed | Commit | Notes |
|---|---|---|---|
| — | — | — | *(nothing landed yet)* |
