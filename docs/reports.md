# Reports

A third top-level section, peer to **Chat** and **Dashboards**: the user
describes what they need in plain language, approves a proposed outline, and
gets a written analytical document — prose, tables and charts — built from the
database, saved, re-runnable months later against fresh data, and printable to
PDF. Persian and English.

Companion to [dashboards.md](dashboards.md) (the grid), [pipeline.md](pipeline.md)
(the AI run), [charts.md](charts.md) (what gets drawn), [security.md](security.md)
(the guard and disclosure) and [architecture.md](architecture.md) (the why).

This describes what is **built**. [reports-plan.md](reports-plan.md) is the
record of what was intended and why, phase by phase, including the things the
build settled differently.

---

## 1. What this is, and what it is not

Chat answers one question and moves on. A dashboard watches numbers that are
always current. **A report is a document**: a structure a human approved, a
narrative written over real results, and a snapshot of a moment that stays
readable after the data has moved on.

|  | Chat | Dashboards | **Reports** |
| --- | --- | --- | --- |
| Shape | conversation | live grid | **document** |
| Time | now, once | always fresh | **a point in time, kept** |
| Prose | two or three sentences | none | **the primary artifact** |
| Structure | emergent | user-arranged | **proposed, then approved** |
| Re-run | no | continuously | **deliberately, on demand** |

Two things belong to Reports alone:

1. **An approval gate before generation.** Nothing else asks the user to
   approve a *plan* before spending model calls.
2. **Prose as the deliverable.** `ANSWER_SYSTEM` writes two sentences for a
   chat bubble; it is deliberately not reused. A report section needs a
   paragraph that reads as part of a document, in a pinned language, aware of
   every result under its heading and of what the sections before it
   established.

Everything else — the guard, the connectors, the chart planner, the semantic
layer, the long-job pattern, RTL handling — already existed and is reused.
**Reports share no table and no code path with Dashboards.** Deleting the
Dashboards feature would leave Reports working.

## 2. The journey

1. **Create.** Pick a **connection** (pinned forever), an **LLM config**
   (changeable at any time), and a **language** (`fa` | `en`, pinned). Type the
   request: *«یک گزارش تحلیلی از عملکرد فروش سه ماه گذشته می‌خواهم»*.
2. **Outline.** One model call proposes headings, each with one or more
   **blocks** — a block being one question that becomes one query and one
   figure. The user confirms, edits, removes, reorders, or adds.
3. **Check.** Each block is turned into SQL and run against the real schema:
   feasible, feasible-but-empty, or infeasible with the guard's own reason. Or
   the SQL is written by hand — §5.
4. **Generate.** A run starts. Sections appear **as they finish**. A failed
   section is retried alone.
5. **Refine.** Edit any paragraph. Change any chart, from the types the result
   can actually support.
6. **Keep.** Print to PDF. Months later, open the report, adjust it if needed,
   and generate again — same structure, current data, previous runs untouched.

---

## 3. The decisions this rests on

| Decision | Why |
| --- | --- |
| **Connection pinned, model swappable** | Byte-for-byte the conversation rule (`_bind_connection`, 422). A report keyed to one connection cannot cross disclosure policies. The model decides who writes the prose, not what is in it. |
| **A section owns N blocks** | *«روند درآمد و محصولات پرفروش»* is two queries under one heading. Prose is per-**section**, data is per-**block** — one paragraph narrating several results together is the entire payoff. |
| **Time windows relativize in SQL itself** | The generator writes `CURRENT_DATE - INTERVAL '3 months'` and the *database* resolves it every run. §4. |
| **Reports refuse narrow disclosure** | A document whose charts carry real numbers and whose paragraphs carry none is worse than no document. Checked at creation **and** at every generation. §7. |
| **A run is not atomic** | Its status is *derived* from its sections, which is what makes progressive rendering and per-section retry fall out for free rather than needing resume machinery. §6. |
| **Numbers come from the result, not the model** | The headline figure is computed by `plan_kpi`; the arithmetic a paragraph needs is computed by `reports/facts.py`; what the prose does say is verified against the rows. §8. |
| **PDF is printed by the browser** | Charts are already SVG, the font is self-hosted, `dirOf` already got the bidi right. §12. |
| **Polled, not streamed** | A report is minutes of model calls, the same trade `semantic_jobs` makes. Each result is persisted the moment it lands, so the poll response *is* the progressive render. |

---

## 4. Relative time windows

A report generated in Farvardin and re-run in Mehr must describe Mehr. Bind
parameters were closed off by [dashboards.md §9](dashboards.md) (`QueryExecutor
.execute` takes none), and regenerating the SQL each run would break the
promise the feature rests on. So **the database resolves the window**:

```sql
WHERE order_date >= CURRENT_DATE - INTERVAL '3 months'
```

The guard already permits this — `exp.CurrentDate`, `exp.Interval`,
`exp.DateAdd`, `exp.DateSub` are on the allowlist. No guard change, no port
change, no interpolation, and `sql_hash` stays **stable across runs**, which is
what run comparison needs.

Three things make it work:

1. **Dialect.** `DIALECT_DATE_ARITHMETIC` in `app/reports/prompts.py` carries
   one example per engine — Postgres `INTERVAL`, MySQL `DATE_SUB`, T-SQL
   `DATEADD`, Oracle `TRUNC`/`INTERVAL`. A test guards every example *through
   the guard*, which is how Oracle's obvious spelling (`ADD_MONTHS`) was caught:
   it parses to a node the allowlist does not carry.
2. **The rules reach `generate` as an addendum.** `NodeDeps.extra_rules` is an
   optional string, empty by default — **with no report in play every SQL
   prompt is byte-identical to before the feature existed**, and
   `PROMPT_VERSION` did not move. A test asserts it.
3. **Conventions come from the semantic layer.** `time_conventions` already
   records fiscal-year start, week start, and whether "last month" is calendar
   or rolling. It is rendered into the report's SQL prompt — per connection,
   never per report.

`report_blocks.time_window` is a *label* (`last_3_months`, `ytd`, …). It drives
the prompt when a block is checked and gives the UI something to change. It is
never substituted into a statement at runtime.

---

## 5. Two roads to a block's SQL

Exactly the dashboards posture, and for the same reason: **neither path is
privileged and neither is trusted.**

1. **`POST /reports/{id}/blocks/{bid}/check`** — one `draft_sql` call:
   `retrieve` → `generate` → `validate`, plus a preview through
   `execute_saved_sql`. Synchronous and per block, because the user is watching
   one heading. Sets `sql_origin = GENERATED`.
2. **`PUT /reports/{id}/blocks/{bid}/sql`** — `validate_sql`: guard, preview,
   no model at all. The same response shape as `/check`, because it answers the
   same question by the other road, so the editor renders either without
   knowing which. Sets `GENERATED_EDITED` or `HANDWRITTEN`.

Every outcome is a **stored verdict**, never an exception — including the model
failing to produce anything. A question the guard refuses is not a failed
request; it is a block that says `INFEASIBLE`, in the guard's own words, with a
Generate button that stays disabled until it is fixed.

| verdict | means |
| --- | --- |
| `UNCHECKED` | never been near the guard, or the question changed since |
| `FEASIBLE` | valid SQL, and the preview returned rows |
| `EMPTY` | valid SQL, no rows in this window. **Not a failure** — a report may legitimately say "no returns were recorded" |
| `INFEASIBLE` | the guard refused, the database refused, or no SQL was produced |

Three rules about what survives an edit:

- **Provenance is derived, never asserted.** The client sends no `sql_origin`
  and could gain nothing by sending one. A block that never held a generated
  statement becomes `HANDWRITTEN`; one that did becomes `GENERATED_EDITED` and
  stays there, because "I started from what the model wrote" is a fact about
  the past.
- **Editing the question or the window always resets the verdict** to
  `UNCHECKED`. The stored statement answered the previous question, and a run
  producing the right numbers under the wrong heading is the failure this
  exists to prevent.
- **Whether the statement itself survives depends on who wrote it.** A
  generated draft is dropped — reproducing it costs one click. A hand-written
  or hand-edited one is kept, because losing an hour of SQL to a typo fix in
  the heading above it is not something a person forgives a tool for. This is
  the semantic layer's rule (`generated invalid → dropped; human invalid →
  flagged and kept`), applied to the same question. For the same reason, a
  statement the guard *refuses* is kept when a person typed it and thrown away
  when a model did.

`sql_origin` is **provenance only, never a trust signal** — identical to
`dashboard_tiles.sql_origin`. `execute_saved_sql` re-validates every statement
against the connection's *current* snapshot on every execution, and
`tests/unit/test_report_guard.py` replays the hostile corpus through
`report_blocks.sql`. That test is what proves reports did not open a bypass.

---

## 6. Generation

`app/workers/report.py` mirrors `workers/semantic.py`: minutes rather than
seconds, so `MAX_CONCURRENT_JOBS = 2`, no heartbeat, durability from the row,
`sweep_orphans` at startup, and **cooperative-then-hard cancellation** — the
flag is checked between sections so an in-flight provider call finishes rather
than being abandoned, with a hard cancel `llm_request_timeout + 5s` later.

```
1. re-check disclosure                     → fail the run if narrow (§7)
2. execute every block                     → execute_many, grouped by connection
3. per section, in order:
     compute facts from its rows           → free, exact (§8)
     narrate                               → one model call
     numeric consistency check             → free, no tokens (§8)
     write report_section_results          → the UI sees it on its next poll
4. the executive summary, last             → from the finished sections' prose
5. derive the run status from its sections → SUCCEEDED | PARTIAL | FAILED
```

Every row is written **the moment it lands**. That is the whole progressive
render: the poll response is a snapshot of what exists so far, so the frontend
needs no protocol of its own and a browser that reloads mid-run resumes where
it was. `phase`, `progress_current` and `progress_total` come straight off the
run row, so the header renders *«در حال تولید بخش ۳ از ۷»* from fields the poll
already returns.

`PARTIAL` is the honest terminal state for a non-atomic run. Because status is
**derived** rather than transitioned, a successful **per-section retry** turns a
`PARTIAL` run into a `SUCCEEDED` one with no state machine. A retry runs
asynchronously onto the *same run row*, so the viewer's existing poll renders
it. It does **not** rewrite the executive summary: that is a paragraph the user
may have edited, and it can be retried on its own.

**A section with no data is not a failure.** Every block empty →
`SKIPPED_NO_DATA` with a sentence saying so. A report that says "no returns
were recorded in this period" is correct; one that hallucinates returns is not.

**Truncation is detected, not hoped against.** `Completion.truncated` is read
off `finish_reason`; a paragraph that hit `max_tokens` is cut back to its last
complete sentence, and when there is no complete sentence the section fails
with the one message that names the setting to change.

---

## 7. Disclosure

A report is *entirely* narration written from result rows. Under `NONE` or
`AGGREGATE`, `disclose()` hands the model no values at all — only `"1,240 rows
across columns: month, revenue."` — while the charts on the same page render
real numbers in the browser. The result is a document whose paragraphs and
pictures disagree in kind.

**Reports require `SAMPLE` or `FULL`**, enforced in two places:

1. **At creation.** The connection picker *disables* ineligible connections and
   shows the reason, so the refusal arrives as `E_DISCLOSURE_TOO_NARROW` rather
   than as prose to match on.
2. **At the start of every generation.** CLAUDE.md invariant #4 requires
   filtering *at render time*, so a report created on a `SAMPLE` connection
   whose policy is later tightened must **fail its run** with a clear message,
   never quietly produce hollow paragraphs.

Nothing here widens disclosure. `narrate.py` is *handed* disclosed results and
cannot disclose them itself — `disclose()` lives in `app.pipeline`, which sits
*above* `app.reports` in the layer order, so the worker discloses under the
policy in force **at narration time**. That is the stricter reading of
invariant #4, and the import contract enforces it for free.

---

## 8. Where the numbers come from

Three mechanisms, none of which asks a model to do arithmetic.

**`plan_kpi`** computes the headline figure deterministically from the result
rows — the same planner chat and tiles use. The document renders a band of them
above the body.

**`app/reports/facts.py`** computes the arithmetic a paragraph needs, exactly,
from the same rows: series ends and the change between them, peak and trough,
direction over halves, totals, concentration (top-1/3/5 share, the Pareto
count), means. It is stated in the prompt so the model can write *about* the
numbers instead of estimating them. **A partial result yields no facts at all**
— under `SAMPLE`, or a truncated query, a total over a prefix is a wrong total
and there is no honest way to caption it. That rule is also what keeps the
module from widening disclosure: every value it states comes from rows the
model already holds in full.

**`app/reports/checks.py`** is the Tier-2 check: after generation, every
numeral in the paragraph is matched against that section's rows *and its fact
sheet*, allowing for rounding, truncation-to-written-precision, and unit
scaling (`1.2M` against `1,234,567`), in Persian (`۱۲۳`) and Latin numerals.
Unmatched figures are recorded in `report_section_results.numeric_check` and
marked in the UI.

It **flags, never blocks** — `pipeline/checks.py`'s posture, *"a finding is a
suspicion, never a verdict."* A check that refused to save a section over a
correctly-derived percentage would be worse than the hallucination it guards
against. Pure, DOM-free and token-free, so it costs nothing and behaves
identically under every disclosure policy.

*(The structural Tier 3 — the model emitting `{{b2.total_revenue}}` for the
renderer to substitute — is not built. The `numeric_check` data is what tells
you whether the prose prompt is reliable enough to try it.)*

---

## 9. Data model

Six tables, migration `0008_reports.py`. The `reports` / `report_runs` split is
the one the codebase reached twice before — `SemanticLayerRow` vs
`SemanticJobRow`, `Dashboard` vs `DashboardTileCache` — and for the reason the
`SemanticLayerRow` docstring gives:

> *"this document is edited by hand, and a user who fixes a grain statement
> expects to have fixed it, not to have forked it"*

### `reports` — the template

`owner_id` (CASCADE) · `name` (unique per owner) · `description` · `prompt`
(the request, verbatim — what the outline was proposed from) · `connection_id`
(**SET NULL**, and immutable after creation → 422) · `llm_config_id` (SET NULL,
changeable) · `language` (`fa` | `en`, pinned) · `status`.

`connection_id` is SET NULL rather than CASCADE on purpose: a deleted
connection must leave a **readable report that cannot regenerate**, never
delete the user's work.

### `report_sections` — the outline

`report_id` (CASCADE) · `position` · `heading` · `intent` (one line on what the
paragraph should cover) · `kind` (`NORMAL` | `EXECUTIVE_SUMMARY`).

### `report_blocks` — one question, one query, one figure

`section_id` (CASCADE) · `position` · `question` · `sql`, `sql_hash` ·
`sql_origin` · `block_type` (`CHART` | `TABLE` | `METRIC`) · `chart_config`
(**NULL means Auto**) · `time_window` · `feasibility_status`,
`feasibility_reason`, `feasibility_checked_at` · `max_rows` (may only *lower*
the connection's cap; `effective_max_rows` enforces it).

### `report_runs` — one generation

`report_id` (CASCADE) · `owner_id` · `status` (`QUEUED` | `RUNNING` |
`SUCCEEDED` | `PARTIAL` | `FAILED` | `CANCELLED`) · `phase` ·
`progress_current` / `progress_total` · `llm_config_id` · `model_snapshot` ·
`prompt_version` · `language` · `error_message` · timestamps.

`model_snapshot` and `language` are copied, not read through the report: *"a
layer generated by a weak model is a different artefact from one generated by a
strong one, and six months later nobody remembers which"* — and a past run
stays readable in the language it was written in.

### `report_block_results` — the numbers, snapshotted

`run_id` (CASCADE) · `block_id` (**SET NULL**) · `heading_snapshot`,
`question_snapshot`, `sql_text`, `sql_hash` · `columns`, `rows`, `row_count`,
`truncated` · `vega_spec`, `chart_source`, `chart_note`, `kpi` · `computed_at`,
`duration_ms` · `status`, `error_code`, `error_message`.

The snapshots are why `block_id` may be SET NULL: a run must stay readable
after the block it came from is deleted. A historical document that silently
loses a section is not a historical document.

`sql_hash` on the *result* is what makes run-to-run comparison possible — the
block carries what its statement is *now*, and a document has to be comparable
with the document before it. `GET /runs/{id}` computes `sql_changed` per figure
against the previous generation, where **null means there was nothing to
compare with** (a first run, or a block that did not exist last time), which is
a different answer from `false`.

### `report_section_results` — the prose

`run_id` (CASCADE) · `section_id` (SET NULL) · `heading_snapshot` · `prose` ·
`edited_prose` (**NULL = not edited**) · `numeric_check` · `status` (`OK` |
`FAILED` | `SKIPPED_NO_DATA`) · `error_message`.

**Two prose columns, not one.** A regeneration writes `prose` on the *new* run
and leaves `edited_prose` NULL; the previous run keeps both. Editing never
destroys, regenerating never overwrites, and the user's writing is always
recoverable from the run it belongs to. Sending `null` is the revert, which is
why NULL has to keep meaning *not edited* rather than *edited to nothing*.

---

## 10. The API

Literal paths are declared **above** `/{id}` routes. Every write returns the
written row, resolved, so the page splices it into state instead of re-reading
— the read-after-write race documented at the end of
[dashboards.md](dashboards.md) is app-wide and this feature does not walk into
it.

```
GET    /reports                                   list (owner-scoped)
POST   /reports                                   create — disclosure gate here
GET    /reports/{id}                              report + outline
PATCH  /reports/{id}                              name, description, prompt,
                                                  llm_config_id, status.
                                                  connection_id → 422
DELETE /reports/{id}

POST   /reports/{id}/outline                      propose (one model call, sync)
POST   /reports/{id}/sections                     add one
PATCH  /reports/{id}/sections/{sid}               heading, intent, position
DELETE /reports/{id}/sections/{sid}
POST   /reports/{id}/sections/{sid}/blocks        add one
PATCH  /reports/{id}/blocks/{bid}                 question, chart_config,
                                                  time_window, block_type
DELETE /reports/{id}/blocks/{bid}
POST   /reports/{id}/blocks/{bid}/check           feasibility — sync, one block
PUT    /reports/{id}/blocks/{bid}/sql             write the statement by hand

POST   /reports/{id}/runs                         start → 202
GET    /reports/{id}/runs                         history (rows only)
GET    /reports/{id}/runs/{rid}                   run + every result — the poll
POST   /reports/{id}/runs/{rid}/cancel
POST   /reports/{id}/runs/{rid}/sections/{sid}/retry            → 202
PATCH  /reports/{id}/runs/{rid}/sections/{sid}                  edit prose
POST   /reports/{id}/runs/{rid}/blocks/{result_id}/chart        redraw
```

Two notes:

- **Blocks are addressed flatly** (`/blocks/{bid}`) while they are *created*
  under their section. Editing a block never needs its section, and the outline
  editor moves blocks often enough that a path carrying the section id would go
  stale in the client's hands.
- **The chart redraw persists**, where chat's deliberately does not. Chat argues
  a transcript must keep what the run produced; a report argues the other way,
  because §12 prints the *saved run* and a chart living only in the browser
  would be lost on the way to the PDF. It is written onto the run and onto the
  run **only** — the same rule that put `edited_prose` there. `chart_source`
  gains a fifth value, `user`.

`tests/integration/test_reports_api.py` sweeps the whole route table and fails
on any route that reaches the service without the caller's own id, and on any
route missing from the sweep — so a route added later is covered the day it is
added.

---

## 11. The UI

```
pages/ReportsPage.tsx        list, create, rename, delete, archive
components/report.tsx        the outline editor and the document viewer
components/report-history.tsx   run history, from the editor and the viewer
components/report-document.ts   merging a run into a document (+ .test.ts)
components/report-print.ts      the print handoff (+ .test.ts)
```

**The outline editor is a workflow, not a form.** `OutlineStatus` names the
sequence the API already enforces — **Describe → Structure → Check →
Generate** — with a count under each read off the outline, so the panel is also
the answer to "how much is left". A question row wears a **status rail** in the
verdict's colour, the one cue that survives skim-reading a twelve-question
outline. Generate is disabled while any block is `INFEASIBLE`, with the count
and the first reason on the button.

**The viewer renders a document**, not a result list: a cover stating the data
source, the moment and the model; numbered sections with rules; the executive
summary set apart with its findings as findings; a band of `plan_kpi` figures;
numbered figure captions with a source line under each; and a *Method and data
notes* appendix listing every question, row count, timestamp and statement —
assembled from rows the run already holds, so it costs no tokens and cannot
drift from the body. The document's furniture is localised and the article
carries `dir` from the report's language.

Three fiddly things live in `report-document.ts` and are tested apart from
React (`npm run test:report`), because they only show up mid-generation: the
numbers arrive before the prose (so a section renders half-drawn, which is the
point), a block result's `position` counts across the whole run while a section
result's counts the outline, and the executive summary has no blocks at all —
it arrives last and belongs first.

The poll **pauses on `document.hidden`**, as the dashboard scheduler does.

---

## 12. PDF

The browser prints it. `@media print` in `styles.css` forces the light token
set over `applyTheme`'s inline styles, hides the app chrome, keeps a figure
whole across a page break, opens the appendix a collapsed disclosure would drop,
and gives the article a definite page width (182mm — A4 less the `@page`
margins).

`report-print.ts` does the two halves a stylesheet cannot reach, because a Vega
plot is sized in its spec and coloured from `data-theme` rather than from the
CSS variables:

- **Width.** `width: 'container'` re-measures through a `ResizeObserver` that
  print never fires, so every chart in the article is redrawn at the width the
  page will actually give it. The inset between the article's edge and a
  chart's box is *measured on screen*, where the layout exists, and subtracted
  from the page, where it does not yet.
- **Theme.** The print redraw pins the light palette, so a dark-theme reader
  does not print near-white axis labels onto white paper.

Then `await document.fonts.ready` — with the Arabic subset explicitly requested
first when the document has Persian in it — and `window.print()`, restoring
everything afterwards. The watch for the dialog closing is armed **before** the
call, not after: `window.print()` blocks and `afterprint` fires inside it, so a
listener attached afterwards waits for an event that has already happened. **Vazirmatn is self-hosted** from `public/fonts` for
exactly this reason: a BI tool pointed at a production database is routinely
deployed behind a firewall, and there a Persian report would print in a
fallback face with the wrong metrics — the *deliverable* rendering wrong while
the app merely looks different.

Server-side PDF is refused on purpose: it means shipping headless Chromium in
the API image, and Persian shaping in pure-Python PDF libraries is where this
reliably breaks.

### The page margin, and who owns it

Left to itself the browser prints its own header and footer into the margin:
the date and time, the document `<title>` ("DataMind"), and the page's URL.
None of it is in the document, so none of it can be hidden — a report somebody
sends to somebody else with `localhost:5173/…` across the foot is the tell that
it came out of a tool rather than a press.

It is removed by **taking the margin**, not by clearing it. A margin box the
document declares belongs to the document, and the browser stops printing its
own there. So all six are claimed:

```css
@page {
  margin: 16mm 14mm;

  @top-left    { content: ""; }   /* the date and time */
  @top-center  { content: ""; }
  @top-right   { content: ""; }   /* the title */
  @bottom-left { content: ""; }   /* the URL */
  @bottom-right{ content: ""; }

  @bottom-center { content: counter(page); … }   /* ours */
}
```

The five empty ones are load-bearing — `content: ""` is what makes a box the
document's rather than the browser's, so deleting them as no-ops brings the
header straight back. `test:print` asserts all six.

The folio itself is 8.5pt, tabular figures so digits stay on axis from page 9
to page 10, in the dim ink the appendix uses, sitting in the upper half of the
bottom margin.

Where margin boxes are not implemented the whole block is ignored and the
browser's own set returns, and there the print dialog's *More settings →
Headers and footers* checkbox is the only switch — it takes all four away
together, page number included. Nothing else in the print path depends on any
of this.

### The wash does not print

Light mode paints `.rm-app` with four radial gradients so the open areas read
as warm paper. On actual paper that is a yellow cast over the page, and the
print block flattens it — with the selector `:root[data-theme='light'] .rm-app`
rather than a bare `.rm-app`, because the wash is `!important` and the more
specific of two `!important` declarations wins. Dark mode never showed it,
which is what made a specificity bug look like a theme bug.

---

## 13. Module layout

`app/reports/` is **self-contained and pure**, on the same terms as
`app/semantic/`: no fastapi, no sqlalchemy, no litellm, no `app.infra`, no
`app.api`, no `app.services`. That is what lets the outline validator, the
narration prompt builder, the fact sheet and the numeric check run in a test
against a dict and a fake gateway — and it is enforced by a `reports is
self-contained` import-linter contract, not by discipline.

```
app/reports/
  prompts.py    REPORT_PROMPT_VERSION (r2) + the four prompts
  outline.py    the proposed-outline document: parse, validate, bind
  facts.py      the arithmetic a paragraph needs, computed from the rows
  narrate.py    the per-section prose prompt, from disclosed results
  checks.py     the numeric consistency check — pure, DOM-free, token-free

app/services/report_service.py   CRUD, the disclosure gate, feasibility,
                                 the SQL editor, run creation
app/workers/report.py            the generation executor
app/api/v1/reports.py            HTTP shape only
```

The prompts live here rather than in `pipeline/prompts/` for the reason the
semantic ones live in `app/semantic/prompts.py`: reports sit *below* the
pipeline — a report reads a pipeline node, a node knows nothing about a report.

---

## 14. How the four invariants apply

1. **AST validation fails closed.** A third entry point to the guard, and it
   gets no exemption: `report_blocks.sql` is re-validated against the
   connection's *current* snapshot on every execution, through
   `execute_saved_sql` — the same function dashboards use. `sql_origin` grants
   nothing. `tests/unit/test_report_guard.py` replays the hostile corpus.
2. **Containment underneath correctness.** Blocks execute through the same
   connectors, read-only transaction, statement timeout and row cap. A block's
   `max_rows` may only tighten.
3. **Credentials encrypted, never exposed.** A report carries ids and display
   names. `test_openapi_has_no_secrets.py` walks every report DTO.
4. **Disclosure is explicit and visible.** §7. The create dialog shows the
   policy in force; generation re-checks it.

**Owner-only**, for the reason [dashboards.md §9](dashboards.md) gives: a
shared report means user B reading data pulled with user A's stored credentials
through a connection B does not own. That is an authorization model, not a UI
feature.

---

## 15. Not built

Sharing, scheduled generation and email delivery, run-to-run **comparison**,
export to Word/PowerPoint, Tier-3 structural number substitution, and "add to
report" from a chat run.

Two are cheap once the rest exists, and are the natural next step:

- **Run comparison.** `sql_hash` is on every block result precisely so this is
  possible, and `sql_changed` already answers "are these two figures the same
  measurement" per figure. Nobody generates the same report twice without
  wanting the delta.
- **"Add to report" from chat.** A succeeded run already has validated SQL, a
  connection and a chart spec to copy into a block. Users explore in chat, then
  want the good turns collected into a document.
