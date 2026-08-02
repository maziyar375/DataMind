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
   caveat. ~~Bump `PROMPT_VERSION`.~~ **Corrected during implementation:**
   `prompts/__init__.py` states the opposite convention right below
   `CHART_USER` — *"PROMPT_VERSION does not move for chart-prompt changes: the
   eval scores generated SQL, and nothing on the SQL-producing path changed"*,
   the same reasoning as the `CLARIFY_SYSTEM` note. Moving it would invalidate
   every historical run comparison for a change no run's SQL can see. It stays
   at v7.
6. ~~Re-key `sales_v1.json` expectations; re-run the eval and record a new
   baseline.~~ **Not needed:** the suite contains no `horizontal_bar`
   expectation (34 `bar`, 2 `line`, 14 `none`), and `expected_chart_type` is
   declared on `GoldRecord` as a plain `str` that **no scorer reads** — it is
   inert data. Worth knowing on its own: any future phase that wants chart
   accuracy measured has to write that metric first.
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
| ✅ | **1. Consolidate** | `horizontal_bar` folded into `bar` + `orientation`; `table only` removed from the chart picker | `charts/__init__.py`, `0007_chart_orientation.py`, `prompts/__init__.py`, `tile-editor.tsx`, `VegaChart.tsx`, `test_charts.py`, `test_tile_charts.py` | **done** — 596 backend tests, ruff + 5 import-linter contracts, typecheck, build, migration round-trip verified against live Postgres |
| ✅ | **2. Encoder** | stack mode (stacked/grouped/100%), temporal axis format **and tick interval** (the "2025" bug), SI axis numbers, formatted tooltips, `size` → bubble | `charts/__init__.py`, `prompts/__init__.py`, `tile-editor.tsx`, `VegaChart.tsx`, both chart test files | **done** — 617 backend tests, ruff + contracts, typecheck, build, and 12 specs compiled and *rendered* through the installed Vega with their axis labels read back |
| ✅ | **3. New types** | heatmap, combo, histogram (box plot deferred) | `charts/__init__.py`, `prompts/__init__.py`, `tile-editor.tsx`, `VegaChart.tsx`, both chart test files | **done** — 633 backend tests, ruff + contracts, typecheck, build, and all 17 specs (12 from Phase 2 + 5 new) compiled and rendered through the installed Vega |
| ✅ | **4. Big number** | `KPI` artifact; single-row results stop being a dead end; replaces the bespoke `METRIC` renderer; delta + sparkline when the data carries a time axis | `charts/__init__.py`, `pipeline/{nodes,state}`, `domain/value_objects`, `services/{run,query,dashboard}_service`, `api/schemas.py`, `types.ts`, `ui.tsx`, `chat.tsx`, `dashboard.tsx` | **done** — 643 backend tests, ruff + contracts, typecheck, build; one `plan_kpi` and one `<Kpi>` serve both surfaces |
| ✅ | **5. Selection** | deterministic shape router; the model picks from 2–4 candidates only, an off-list pick is declined | `charts/__init__.py` (`chart_candidates`, `plan_chart`), `prompts/__init__.py`, `pipeline/nodes/` | **done** — 659 backend tests, ruff + contracts, typecheck, build; every signature has a unit test |
| ✅ | **6. Colour** | diverging ramp, semantic +/- pair, `npm run test:palette` (50 checks) | new `theme/color.ts`, `theme/palette.ts`, `theme/palette.test.ts`; `charts/__init__.py`, `VegaChart.tsx` | **done** — 664 backend tests, ruff + contracts, typecheck, build, all three frontend test scripts; every colour path rendered through the installed Vega |
| ✅ | **7. Picker UI** | icon grid, options filtered by what the result supports, "change chart" in chat | `charts/__init__.py` (`chart_options`, `intent_for`), `run_service.py`, `conversations.py`, `sql_draft_service.py`, `ui.tsx`, `tile-editor.tsx`, `chat.tsx` | **done** — 684 backend tests, ruff + contracts, typecheck, build, all three frontend test scripts; 15 picker-reachable specs rendered, and the redraw driven against the live app database |

**Dependencies:** 1 → 2 → 3 → 5 → 7 is the spine. Phase 4 depends only on 1.
Phase 6 is independent of everything except 3 (the diverging ramp exists for
the heatmap) and can be done at any point after it.

**Log completions here** — date and commit, so the table stays honest:

| Phase | Completed | Commit | Notes |
|---|---|---|---|
| 0 | 2026-08-02 | — | Findings above. The premise "many duplicate chart types" turned out to be one real duplicate; the wider problem is a *narrow* set plus a missing stacking control. |
| 1 | 2026-08-02 | `6fde6d7` | Two plan steps corrected against the code — see the struck-through items in Phase 1. Migration **0007 applies on the next `make up`**: the running `api` container has the pre-change image baked in (no volume mount), and its start command is `alembic upgrade head`. The dev DB has zero affected rows, so it is a no-op there either way. |
| 2 | 2026-08-02 | `cabfd0f` | Three defects caught by rendering rather than reasoning — see "What rendering caught" below. Scope went slightly past the plan's four bullets: the `Split` and `Size` controls in the tile editor, and a `categories` count in `usermeta`, because stack and size were otherwise reachable only from chat, and the browser's width rule counted rows where a split chart needs columns. |
| 6 | 2026-08-02 | *(uncommitted)* | Writing the validator found two things the palette was claiming and not doing — see the design note. The palette moved to `theme/palette.ts` so a DOM-free test can import it without React. |
| 5 | 2026-08-02 | `4dc6e18` | The candidate lists came out **more generous** than the plan's table — see the design note. `CHART_SYSTEM` no longer enumerates chart types at all; the shortlist arrives per-result in `CHART_USER`. The eval suite was not touched: `expected_chart_type` is still inert (Phase 1), so the signatures are pinned as unit tests instead, which is what the plan expected. |
| 4 | 2026-08-02 | `ede4ccb` | Scoped **narrower** than the plan implied: only the single-row veto is rescued by a big number, not every veto — see the design note below. The `METRIC` tile keeps its "first of N rows" reading and gains delta + sparkline when its query has a time axis. |
| 7 | 2026-08-02 | *(uncommitted)* | Went **wider than the plan's "Touches"**, which named only three frontend files. A picker that filters by what the result supports needs the backend to say what that is, and a "change chart" in chat needs somewhere to recompile — drawing a second spec in the browser is the thing `chat.tsx` already refuses. So this phase added `chart_options`/`intent_for` to the charts module, `chart_options` to the SQL draft, and one new route, `POST /runs/{run_id}/chart`. See the design note. |
| 3 | 2026-08-02 | `db8b7fb` | Box plot deferred, per the open decision. Two design calls worth knowing about: a new `color` channel distinct from `series` (colour-as-magnitude vs colour-as-identity), and a **heuristic change** — a result with two dimensions now becomes a split bar or a heatmap instead of a bar that silently dropped the second dimension. That last one changes what existing `Auto` tiles draw. |

### What rendering caught (Phase 2)

Each of these passed unit tests and would have shipped broken. The check that
found them was compiling the spec with the installed `vega-lite` and reading
the axis labels back out of the scenegraph — worth repeating for any future
encoder change.

1. **A time format alone makes things worse.** Ticks are placed before they are
   labelled, and Vega spaces them for pixels, not for the data's grain. A
   three-month series gets four ticks per month, which `%b %Y` renders as
   `Jan 2025, Jan 2025, Jan 2025, Jan 2025, Feb 2025`. The format needs a
   matching tick interval or it trades a confusing axis for a wrong one.
2. **`interval.every(n)` filters, it does not stride.** `date` every 30 yields
   Jan 1, Jan 31, Feb 1, Mar 1 — d3 keeps the values whose unit divides by n,
   and the month boundary resets the count. Escalating to a coarser interval
   (day → week → month → quarter → year) is the only evenly-spaced option.
3. **`vega-time` has no `hour` interval.** Only `year, quarter, month, week,
   date, day` exist; `timeInterval("hour")` returns undefined and Vega throws
   inside its own tick generator. In the browser that means `embed()` rejects
   and the chart disappears entirely. Intraday axes set a format and no
   interval.

A fourth, found the same way: the obvious `","` axis format produces
`1e+2, 1.5e+2, 2e+2`, because a d3 specifier with no type gets a precision
filled in by `tickFormat` and then formats like `g`. `",d"` fixes that and
breaks fractional axes instead (`0, 0, 0, 1, 1, 1`). Hence the rule that ended
up in the code: `~s` above ten thousand, and *nothing at all* below it, where
Vega's own default was already right.

### Design notes (Phase 3)

**`color` is a separate channel from `series`, deliberately.** They compile to
the same Vega channel and do opposite jobs: `series` colours by *identity* (one
hue per region, categorical palette), `color` colours by *magnitude* (one ramp,
low to high). Vega picks the scale family — and therefore the palette — from
the encoding type, so conflating them is exactly how a chart ends up with eight
unrelated hues standing for a quantity. `VegaChart.tsx` already documents the
same split on the palette side, which is why the heatmap's measure is kept
`quantitative` rather than made ordinal.

**A histogram cannot be identified from its column, only from its result.**
Nothing distinguishes raw observations from group totals: `SELECT total FROM
orders` and `SELECT customer, SUM(total) … GROUP BY customer` produce columns
that profile identically. There is no test to write. What does the work is
*where* the check is consulted — the heuristic only reaches for a histogram
when the result has no dimension at all, which is the one thing a GROUP BY
never produces, since grouping leaves its key in the result. The two conditions
in `_histogram_candidate` only rule out results too small or too repetitive to
have a distribution.

**A combo's two y axes are conditional.** They separate only when the measures'
magnitudes differ by `DUAL_AXIS_RATIO` or more. Sharing a scale that did not
need sharing is the lesser evil: two independent axes let a reader see a
crossover that is purely an artefact of where the scales happened to land. Both
branches were verified by rendering — three axes in the dual case, two in the
shared one.

**One heuristic change is visible to existing tiles.** A result with two
dimensions and a measure used to fall through to a bar of the *first*
dimension, drawing several rows per category on top of each other with nothing
saying a column had been ignored. It now becomes a split bar (second dimension
≤ `MAX_SERIES`) or a heatmap (larger, and within `MAX_HEATMAP_CELLS`). Tiles
set to `Auto` will redraw accordingly.

### Design notes (Phase 4)

**Only the single-row veto is rescued.** The plan said "a single-row result is
the canonical KPI", and the tempting generalisation is to answer *every* veto
with a big number. It is wrong: a thousand tied totals drawn large is still a
thousand tied totals, and an id is not a metric however large it is set. Those
vetoes describe results whose numbers are not worth enlarging either, so
rescuing them would trade "no picture" for a confident wrong one. The node
therefore checks `row_count == 1` before reaching for `plan_kpi`.

**A one-row result defeats `_measure_candidates`.** It excludes constant
columns — correctly, for charts — and a single row has exactly one distinct
value in *every* column, so it rejects the very thing a KPI is made of.
`_kpi_measure` picks differently: numeric, not an identifier, rightmost. This
is the kind of trap that only shows up when the same helper is reused across
two questions that sound alike.

**The delta is drawn, never coloured.** Green-for-up is a judgement the data
does not carry — a rising refund rate is not good news, and nothing in the
result says which metric this is. The direction gets an arrow and neutral text.
A semantic positive/negative pair is Phase 6, where it can be measured against
the palette rather than guessed at in a component.

**The sparkline is hand-drawn SVG, and that is the one deliberate exception**
to "use the stable component". A sparkline has no axes, ticks, legend or
readable scale — it is a shape, not a chart — while a Vega view brings a
dataflow runtime, a `ResizeObserver` and a `finalize()` per tile for a strip 64
pixels wide. Twelve lines of `<polyline>` is the smaller commitment. If it ever
grows an axis, it should become a real chart instead.

**Formatting happens on the backend.** `value` and `delta.text` cross the wire
already written out. Two implementations formatting the same number is how a
tile ends up saying `1,247,318.4` while the axis beside it says `1.2M`; one
planner is also what stops the tile and a chat turn from disagreeing about
which column the number even is.

### Design notes (Phase 5)

**The candidate lists are more generous than the plan's table.** The first
implementation followed it literally, and a test immediately caught the cost: a
pie over four quarters was declined, because the plan's temporal row offers
only line → area → bar. But "share of revenue by quarter" is an ordinary
request, and refusing it would overrule a user who picked **Pie** in the tile
editor — the exact failure Phase 1 existed to remove. So the rule became:
**offer any type a reader might reasonably want, and let `_fit` catch the ones
the data cannot carry.** Being too strict here costs a user their choice; being
too loose costs one extra line in a prompt.

**An off-list pick is declined, not repaired.** Previously any suggestion went
to `_fit`, which would salvage what it could. Now a type the shape does not
offer causes rank 1 to answer instead — because a model that asked for a
scatter of one categorical column misread the shape, and its *column*
assignment is no more trustworthy than its type. The user still sees why:
`_chart_note` reports "a scatter does not fit this result; showing a bar
chart."

**The title survives a declined type.** It is the one part of a suggestion that
comes from reading the question rather than the shape, so it is carried onto
the fallback intent.

**`CHART_SYSTEM` no longer lists chart types.** It describes the job; the
shortlist arrives per-result in `CHART_USER`. That is what makes the strategy
scale — a free choice from nine types has nine ways to be wrong and grows a
tenth with every type added, while a choice from three does not.

**The `present` → `chart` ordering was left alone,** as recommended. The check
that made it safe: `ANSWER_SYSTEM` never mentions a chart, so the narration
cannot promise a picture the pipeline may not produce. That is now pinned by a
test rather than left as an observation.

### What the validator caught (Phase 6)

The plan said "add a validator so the claims stay checkable". Writing it found
that two of them were already false.

**1. The recorded CVD numbers were deuteranopia only.** The palette's table
listed one colour-blindness figure per mode — light 9.8, dark 15.5 — without
naming a deficiency, implying all of them had been checked. Both reproduce
*exactly* under deuteranopia and neither is the worst case. Measured against
all three, the dark set's violet/olive pair separates by **7.3 under
tritanopia**, below the ≥8 bar the file claimed to hold.

It was **not** re-tuned, and that is a judgement worth stating. Tritanopia is
roughly 1 in 10,000 and not sex-linked; red-green deficiency is about 1 man in
12. Buying a blue-yellow margin with a measured red-green one would help far
fewer readers than it hurt. So the table now names each deficiency, records the
tritanopia column, and the validator holds it to an explicitly lower floor. The
number is visible and bounded instead of unmeasured.

**2. Every heatmap has been rendering in viridis since Phase 3.** The config
overrode `range.category`, `range.ramp` and `range.ordinal` — but a `rect`
mark's continuous colour scale asks Vega for the **`heatmap`** range, which was
still on its built-in green-to-yellow. Phase 3's tests asserted the colour
channel stayed `quantitative` (so it would reach a ramp family at all) and
never checked which colours came out; rendering the spec and reading the
painted fills did. This is the second time this exact bug has appeared in this
file, which is why the comment now says to read the compiled scale rather than
assume `ramp` covers a new mark.

### Design notes (Phase 6)

**The diverging ramp does not chase the accent,** unlike the sequential one.
Its job is to encode *sign*, and ember ↔ blue is the reading a business
audience already has. A plum-anchored diverging ramp in light mode would be
on-brand and unreadable. The midpoint is a near-neutral that recedes toward
each mode's own surface, so "no change" is the value that disappears.

**The semantic pair is sign, not judgement.** Nothing in it says a rise is good
news — a climbing refund rate is not, and the platform cannot know which metric
it is looking at. So it paints *negative values*, and a KPI's delta stays an
arrow with neutral text. `positive` is deliberately the same value as
`category[0]`: a chart with negatives keeps the colour it would have had, and
only the negatives depart from it. Note what is **not** used: green/red, the
conventional pair and the classic red-green failure.

**The split of responsibility is the same one `usermeta` already established.**
The backend answers "does this measure cross zero" and "which field, escaped,
does a negative test read" — facts about the data. The browser answers "what
colour is a negative" — a fact about the theme. Neither could answer the
other's question.

### What rendering caught (Phase 7)

Nothing, this time — and that is the finding worth recording, because it is the
first phase where it was true. Fifteen specs, one per type the picker can reach
across six shapes, compiled and rendered: 3 lines × 12 points for a split
trend, 4 arcs for a pie of quarters, 24 rects for the matrix built out of a
split bar, 8 symbols for a scatter of two measures. All drew.

The reason is structural rather than lucky. `intent_for` builds nothing new; it
moves columns between channels and hands the result to `_fit`, which is the
same last gate every other path already goes through. The rendering probe was
still worth running: it is the only check that would have caught a translation
putting a measure on a channel that compiles and paints nothing, which is
exactly what the heatmap translation does for a living.

The redraw was also driven against the **live app database** — 21 stored TABLE
artifacts, every offered type compiled for six real results, inside a
rolled-back transaction. That is where the SQLAlchemy statement and the
`ArtifactKind` comparison were exercised for real; the unit tests fake the
session and would not have caught a `where` clause that matched nothing.

### Design notes (Phase 7)

**The reason lives on the backend.** "Needs two dimensions crossed by one
measure. This result is a measure across categories." is two facts, and only
the backend holds both: what a type requires, and what this result *is*. The
signature the shape router already computes for the prompt turned out to be
exactly the second half. The browser composes nothing — it renders a string and
sets a `title`.

**Shown and greyed, never hidden.** A list that shrank between one result and
the next reads as a bug in the app rather than a fact about the data, and the
hover reason is the only place a user ever learns *why* a heatmap needs two
dimensions. This is also the phase's stated bar, from the progress table: an
unsupported type is disabled with a reason, never offered and then demoted.

**`aria-disabled`, not `disabled`.** A `disabled` button swallows pointer
events in every browser, and the tooltip goes with them — which would have
removed the entire point of drawing the refused option. The click is refused in
the handler instead.

**The picker sends a type and nothing else.** Someone changing a chart in chat
is saying "draw this differently", not re-assigning columns, so the columns
come from rank 1 — the intent the shape router already worked out — and only
the channels that mean something different under the new type move. Three do:
scatter takes the combo's two measures off y and y2 and plots them against each
other; heatmap takes the split bar's legend to the other axis and its height to
the colour; pie drops the split, because slices of slices is a sunburst.

**The redraw is a round trip.** Compiling a second spec in the browser would
put a chart on screen the backend never approved — the same objection
`chat.tsx` already records against a client-side fallback renderer. Re-running
the SQL would be worse: the picture could drift away from the table printed
underneath it. So the server recompiles from the rows already stored in the
run's TABLE artifact, which are the same rows the `chart` node profiled.

**The CHART artifact is replaced, not appended.** It is a presentation of the
TABLE, not a record of what happened; two of them would leave nothing saying
which one the reader is looking at. What the pipeline decided on its own stays
legible in the step trail and the `ARTIFACT_CREATED` event, both untouched.

### Carried forward

- `docs/architecture.md:1174` still shows `horizontal_bar` in the `ChartIntent`
  contract. Left deliberately: that file is the *original proposal*, and
  rewriting a proposal to match what was later built erases the record. If it
  should instead read as a live contract reference, it needs a pass — but that
  is a decision about the document, not a leftover from this phase.
- The `sort` a bar chart gets is still keyed off the *category channel*, so a
  horizontal bar sorts by `-x` and a vertical one by `-y`. Correct today, and
  worth re-reading in Phase 2 when stacking arrives — a stacked bar's sort and
  its stack order are different questions.
