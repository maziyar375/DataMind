# Reports

A third top-level section, peer to **Chat** and **Dashboards**: the user
describes what they need in plain language, approves a proposed outline, and
gets a written analytical document — prose, tables and charts — built from the
database, saved, re-runnable months later against fresh data, and printable to
PDF. Persian and English.

Companion to [dashboards.md](dashboards.md) (the grid), [pipeline.md](pipeline.md)
(the AI run), [charts.md](charts.md) (what gets drawn), [security.md](security.md)
(the guard and disclosure) and [architecture.md](architecture.md) (the why).

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

Two things belong to Reports alone and exist nowhere else in the codebase:

1. **An approval gate before generation.** Nothing else asks the user to
   approve a *plan* before spending model calls. `clarify` asks a question; it
   does not show a structure.
2. **Prose as the deliverable.** `ANSWER_SYSTEM` writes two sentences for a
   chat bubble. A report section needs a paragraph that reads as part of a
   document, in a pinned language, aware of every result under its heading.

Everything else — the guard, the connectors, the chart planner, the semantic
layer, the long-job pattern, RTL handling — already exists and is reused.
**Reports share no table and no code path with Dashboards.** Deleting the
Dashboards feature would leave Reports working.

---

## 2. The user's journey

1. **Create.** Pick a **connection** (pinned forever), an **LLM config**
   (changeable at any time), and a **language** (`fa` | `en`, pinned). Type the
   request: *«یک گزارش تحلیلی از عملکرد فروش سه ماه گذشته می‌خواهم»*.
2. **Outline.** The model proposes headings, each with one or more **blocks** —
   a block being one question that will become one query and one chart. The
   user confirms, edits, removes, reorders, or adds.
3. **Feasibility.** Each block is checked against the real schema. Feasible,
   feasible-but-empty, or infeasible with the guard's own reason. The user
   fixes what is blocked before generating.
4. **Generate.** A run starts. Sections appear **as they finish**, not after
   ninety seconds of spinner. A failed section is retried alone.
5. **Refine.** Edit any paragraph. Change any chart type, from the types the
   result can actually support.
6. **Keep.** The run is saved. Print to PDF. Months later, open the report,
   adjust a heading if needed, and generate again — same structure, current
   data, previous runs untouched.

---

## 3. The eight decisions this design rests on

| Decision | Why |
| --- | --- |
| **Connection pinned, model swappable** | Byte-for-byte the conversation rule (`_bind_connection`, 422). A report keyed to one connection cannot cross disclosure policies. The model decides who writes the prose, not what is in it. |
| **A section owns N blocks** | *«روند درآمد و محصولات پرفروش»* is two queries under one heading. Prose is per-**section**, data is per-**block** — one paragraph narrating several results together is the entire payoff. |
| **Time windows relativize in SQL itself** | Not stored parameters, not regenerated SQL: the generator writes `CURRENT_DATE - INTERVAL '3 months'` and the *database* resolves it every run. §6. |
| **Reports refuse narrow disclosure** | A document whose charts carry real numbers and whose paragraphs carry none is worse than no document. Checked at creation **and** at every generation. §7. |
| **A run is not atomic** | Its status is derived from its sections. That is what makes progressive rendering and per-section retry fall out for free rather than needing resume machinery. §8. |
| **Numbers come from the result, not the model** | The headline figure is computed by `plan_kpi`; prose figures are verified against the result rows and flagged when they do not match. §9. |
| **PDF is printed by the browser** | Charts are already SVG, fonts are already loaded, `dirOf` already got the bidi right. Server-side rendering means shipping Chromium in the API image and starting Persian shaping from zero. §11. |
| **Polled, not streamed** | A report is minutes of model calls, the same trade `semantic_jobs` makes. Each block result is persisted the moment it lands, so the poll response *is* the progressive render. |

---

## 4. Data model

Six tables, migration `0008_reports.py`.

The `reports` / `report_runs` split is the one the codebase has already reached
twice — `SemanticLayerRow` vs `SemanticJobRow`, and `Dashboard` vs
`DashboardTileCache`. The `SemanticLayerRow` docstring states the reason and it
applies verbatim to report headings:

> *"this document is edited by hand, and a user who fixes a grain statement
> expects to have fixed it, not to have forked it"*

### `reports` — the template

| column | note |
| --- | --- |
| `id`, `owner_id` | FK users **CASCADE**. Owner-only in v1 — §13 |
| `name`, `description` | `name` unique per owner, as `dashboards` is |
| `prompt` | the user's original request, kept verbatim; it is what the outline was proposed from |
| `connection_id` | FK **SET NULL**, not CASCADE — a deleted connection must leave a readable report that cannot regenerate, never delete the user's work. **Immutable after creation** (422) |
| `llm_config_id` | FK SET NULL. Changeable at any time |
| `language` | `fa` \| `en`. Pinned at creation and sent explicitly in every prose prompt |
| `status` | `ACTIVE` \| `ARCHIVED` |

### `report_sections` — the outline

`id` · `report_id` (CASCADE) · `position` · `heading` · `intent` (one line on
what this section's paragraph should cover, written by the outline model and
editable) · `kind` (`NORMAL` | `EXECUTIVE_SUMMARY`).

### `report_blocks` — one question, one query, one chart

| column | note |
| --- | --- |
| `section_id`, `position` | CASCADE |
| `question` | the plain-language question. **This is what the user edits in v1**, not the SQL |
| `sql`, `sql_hash` | produced by the feasibility check. `sql_hash` is what makes run-to-run comparison honest — comparing two runs whose SQL differs is a lie |
| `sql_origin` | `GENERATED` \| `GENERATED_EDITED` \| `HANDWRITTEN`. Provenance only, **never a trust signal** — same rule as `dashboard_tiles.sql_origin` |
| `block_type` | `CHART` \| `TABLE` \| `METRIC` |
| `chart_config` | serialised `ChartIntent`. **NULL means Auto** — right for a report re-run on differently-shaped data |
| `time_window` | a *label* (`last_3_months`, `previous_quarter`, `ytd`, `custom`), driving the prompt and the UI. Never substituted into SQL at runtime — §6 |
| `feasibility_status` | `UNCHECKED` \| `FEASIBLE` \| `EMPTY` \| `INFEASIBLE` |
| `feasibility_reason`, `feasibility_checked_at` | the guard's own message, shown verbatim |
| `max_rows` | may only *lower* the connection's cap — `effective_max_rows` enforces it |

### `report_runs` — one generation

`id` · `report_id` (CASCADE) · `owner_id` · `status` (`QUEUED` | `RUNNING` |
`SUCCEEDED` | `PARTIAL` | `FAILED` | `CANCELLED`) · `phase` ·
`progress_current` · `progress_total` · `llm_config_id` · `model_snapshot` ·
`prompt_version` · `language` · `error_message` · `started_at` · `finished_at`
· `created_at`.

`PARTIAL` is the honest terminal state for a non-atomic run: some sections
succeeded, some did not. Nothing else in the codebase has it, because nothing
else generates independently-failable parts.

`model_snapshot` is copied from `SemanticLayerRow`'s reasoning: *"a layer
generated by a weak model is a different artefact from one generated by a
strong one, and six months later nobody remembers which."* Doubly true here.

### `report_block_results` — the numbers, snapshotted

`run_id` (CASCADE) · `block_id` (**SET NULL**) · `heading_snapshot`,
`question_snapshot`, `sql_text`, `sql_hash` · `columns`, `rows`, `row_count`,
`truncated` · `vega_spec`, `chart_source`, `chart_note`, `kpi` · `computed_at`,
`duration_ms` · `status`, `error_code`, `error_message`.

The snapshotted heading, question and SQL are why `block_id` may be SET NULL:
a run must stay readable after the block it came from is deleted. A historical
document that silently loses a section is not a historical document.

### `report_section_results` — the prose

`run_id` (CASCADE) · `section_id` (SET NULL) · `heading_snapshot` · `prose`
(model-written) · `edited_prose` (**NULL = not edited**) · `numeric_check`
(JSONB, §9) · `status` (`OK` | `FAILED` | `SKIPPED_NO_DATA`) · `error_message`.

**Two columns, not one.** A regeneration writes `prose` and leaves
`edited_prose` NULL on the *new* run; the previous run keeps both. Editing
never destroys, regenerating never overwrites, and the user's writing is
always recoverable from the run it belongs to.

---

## 5. Module layout

`app/reports/` is **self-contained and pure**, on the same terms as
`app/semantic/`: no fastapi, no sqlalchemy, no litellm, no `app.infra`, no
`app.api`, no `app.services`. That is what lets the outline validator, the
narration prompt builder and the numeric check run in a test against a dict and
a fake gateway.

```
app/reports/
  prompts.py    REPORT_PROMPT_VERSION + the four prompts (§10)
  outline.py    the proposed-outline document: parse, validate, bind to a snapshot
  narrate.py    build the per-section prose prompt from its blocks' results
  checks.py     the numeric consistency check — pure, DOM-free, token-free

app/services/report_service.py   CRUD, the disclosure gate, feasibility,
                                 run creation. Owns the transaction boundary.
app/workers/report.py            the generation executor — mirrors
                                 workers/semantic.py, does not share it
app/api/v1/reports.py            HTTP shape only
```

Two `pyproject.toml` changes:

```toml
layers = ["app.api", "app.services", "app.pipeline", "app.reports",
          "app.semantic", "app.domain"]
```

plus a `reports is self-contained` forbidden contract copied from the semantic
one. `make lint` must pass.

**Frontend**

```
frontend/src/pages/ReportsPage.tsx     list, create, open
frontend/src/components/report.tsx     the outline editor and the viewer
frontend/src/components/report-print.ts   the print handoff (§11)
frontend/src/styles.css                @media print block
```

`App.tsx` gains one `View` member, one sidebar item, one route line. That is
the whole integration surface.

---

## 6. Relative time windows — the SQL relativizes itself

A report generated in Farvardin and re-run in Mehr must describe Mehr. The
naive design stores `time_window: last_3_months` beside SQL that already reads
`WHERE order_date >= '2026-01-01'` — which changes nothing, because something
would have to rewrite the statement.

Three doors, two of them closed:

- **Bind parameters** — [dashboards.md §9](dashboards.md) already closed it:
  *"`QueryExecutor.execute` takes no bind parameters. Filters need the port
  extended across all four connectors. Never by string interpolation."*
- **Regenerate the SQL each run** — breaks the promise the feature is built on
  (*structure preserved, only data changes*) and makes `sql_hash` comparison
  meaningless.
- **Let the database resolve it.** The generator writes relative date
  arithmetic and the engine evaluates it at execution time:

```sql
WHERE order_date >= CURRENT_DATE - INTERVAL '3 months'
```

The guard already permits this. From [sqlguard/policy.py](../backend/app/sqlguard/policy.py):

```python
exp.Substring, exp.Concat, exp.Extract, exp.DateTrunc, exp.DateAdd,
exp.DateDiff, exp.DateSub, exp.CurrentDate, exp.CurrentTimestamp,
... exp.Interval, ...
```

No guard change, no port change, no interpolation, and `sql_hash` stays
**stable across runs** — which is exactly what run comparison needs.

Three things this requires:

1. **Dialect.** Postgres `INTERVAL`, MySQL `DATE_SUB`, T-SQL `DATEADD`, Oracle
   `ADD_MONTHS`. The generator is already dialect-aware; this is prompt
   wording, per dialect, in the same place `GENERATE_SYSTEM` already handles
   dialect.
2. **The rules reach `generate` as an addendum.** `draft_sql` sends
   `GENERATE_SYSTEM`. Reports need one extra paragraph of time rules without
   changing what chat sends. So `NodeDeps` gains an optional `extra_rules`
   string, empty by default — **with no report in play, every SQL prompt is
   byte-identical to today**, and `PROMPT_VERSION` does not move. That is the
   same discipline `semantic_layer_enabled` and `clarify_enabled` follow.
3. **Conventions come from the semantic layer.** `time_conventions` already
   records fiscal-year start, week start, and whether "last month" is calendar
   or rolling. Render it into the report's SQL prompt. For a Persian
   deployment whose fiscal year starts in Farvardin, that field is the only
   correct place for it — per connection, not per report.

The stored `time_window` label is metadata: it drives the prompt when a block
is (re)checked and it gives the UI something to show and let the user change.
It is never substituted into a statement at runtime.

---

## 7. Disclosure — the gate, and the hole it has to cover

A report is *entirely* narration written from result rows. Under `NONE` or
`AGGREGATE`, `disclosure.disclose()` hands the model no values at all — only
`"1,240 rows across columns: month, revenue. Individual values were not
shared."` Charts still render real numbers in the browser, because that is the
owner's own data reaching the owner's own screen. The result is a document
whose paragraphs and pictures disagree in kind.

**Reports require `SAMPLE` or `FULL`.** Enforced in two places, and the second
is the one that is easy to forget:

1. **At creation.** The connection picker *disables* ineligible connections
   and shows the reason — the same posture `chart_options` takes, where the
   picker greys out what will not work rather than accepting it and
   apologising later.
2. **At the start of every generation.** CLAUDE.md invariant #4 is explicit
   that disclosure filters *"at render time, never only at write time, so
   tightening a policy takes effect on the next question"*. A report created on
   a `SAMPLE` connection whose policy is later tightened to `NONE` must fail
   its run with a clear message — never quietly produce hollow paragraphs.

```
E_DISCLOSURE_TOO_NARROW
"This connection's disclosure policy is AGGREGATE. A report's analysis is
 written from result values, so it needs SAMPLE or FULL. Change the policy on
 the data source, or generate this report against a different connection."
```

Nothing here widens disclosure. Report generation reads results under the same
`disclose()` the pipeline uses, and the schema block under the same
`HintBudget`.

---

## 8. Generation — a run is a set of parts

`workers/report.py` mirrors `workers/semantic.py`: a job that is minutes rather
than seconds, so it gets a low concurrency ceiling, no heartbeat, durability
from the row, `sweep_orphans` at startup, and **cooperative-then-hard
cancellation** — the flag is checked between sections so an in-flight provider
call finishes rather than being abandoned, with a hard cancel
`llm_request_timeout + 5s` later.

The order of work:

```
1. re-check disclosure                     → fail the run if narrow (§7)
2. execute every block                     → execute_many, grouped by connection,
                                             one connector, semaphore of 4
3. per section, in order:
     narrate from its blocks' results      → one model call
     numeric consistency check             → free, no tokens (§9)
     write report_section_results          → the UI sees it on its next poll
4. the executive summary, last             → from the finished sections' prose
5. derive the run status from its sections → SUCCEEDED | PARTIAL | FAILED
```

Every result row is written **the moment it lands**. That is the whole
progressive-rendering design: the poll response is a snapshot of what exists so
far, so the frontend needs no special protocol, and a browser that reloads
mid-run resumes exactly where it was.

`phase`, `progress_current` and `progress_total` already exist on
`SemanticJobRow` and are copied here, so the header renders *«در حال تولید
بخش ۳ از ۷»* from fields the poll already returns.

**Per-section retry** re-executes that section's blocks and rewrites those
rows. Because the run's status is *derived*, a successful retry turns a
`PARTIAL` run into a `SUCCEEDED` one with no state machine.

**A section with no data is not a failure.** Every block empty →
`SKIPPED_NO_DATA` with a sentence saying so. A report that says "no returns
were recorded in this period" is correct; one that hallucinates returns is not.

---

## 9. Numbers come from the database

Three tiers. Ship one and two; design the prompt so three is a later swap.

**Tier 1 — the headline figure is never model-written.** `plan_kpi` already
computes it deterministically from the result rows, and is the same planner
chat and tiles use. Render it as an element of the section; let the prose be
qualitative around it.

**Tier 2 — verify what the prose does say.** After generation, extract every
numeral from the paragraph and check it against that section's block results,
allowing for rounding and unit scaling (`1.2M` against `1,234,567`). Unmatched
figures are recorded in `report_section_results.numeric_check` and marked in
the UI for the user to look at.

It **flags, never blocks**. Percentages and deltas the model derived correctly
from two result values are expected false positives, and a check that refused
to save the section over one would be worse than the hallucination it was
guarding against. This is the posture `pipeline/checks.py` already argues for
at length: *"A finding is a suspicion, never a verdict."*

The check lives in `app/reports/checks.py`, pure and token-free, and handles
both Persian (`۱۲۳`) and Latin (`123`) numerals.

**Tier 3 — make it structural.** The model writes `{{b2.total_revenue}}` and
the renderer substitutes the real value. Hallucination becomes impossible
rather than detected, and Persian numeral formatting becomes a render-time
decision instead of something the model has to get right. Left for later
because it needs the prose prompt to be reliable at emitting tokens, and the
Tier 2 data tells you whether it is.

---

## 10. Prompts

Four, in `app/reports/prompts.py`, versioned as `REPORT_PROMPT_VERSION` and
recorded on every run. They live in the reports module rather than
`pipeline/prompts/` for the same reason the semantic ones live in
`app/semantic/prompts.py`: reports sit below the pipeline — a report reads a
pipeline node, a node knows nothing about a report.

| prompt | what it does |
| --- | --- |
| `REPORT_OUTLINE_*` | request + schema block + semantic layer → sections, each with an `intent` and one or more block questions. Returns structured JSON. |
| `REPORT_TIME_RULES` | the addendum §6.2 describes — dialect-specific relative-date rules, appended to `GENERATE_SYSTEM` **only** for report blocks |
| `REPORT_SECTION_*` | heading + intent + every block's disclosed result + language + the report's own prompt → one or two paragraphs |
| `REPORT_SUMMARY_*` | the finished sections' prose → the executive summary |

Rules `REPORT_SECTION_SYSTEM` must carry, each of which is a failure mode
someone will otherwise report as a bug:

- Write in **{language}**, whatever language the headings or column names are
  in. Language is pinned per report, never inferred per section — otherwise
  section 3 comes back in English because its heading happened to be a metric
  name.
- Use **only** the given values. Never invent a figure.
- Two to four sentences per section, prose, no markdown headings — the heading
  is already rendered.
- If several blocks are given, relate them to each other. That is why they are
  in one section.
- If a caveat is given (a capped result, an empty one), work it into a clause.
- If every result is empty, say so plainly and stop.

`ANSWER_SYSTEM` is **not** reused. It is tuned for a two-sentence chat bubble
and reusing it is how a report ends up reading like a chat transcript.

---

## 11. PDF

The browser prints it. Charts are already SVG —
[VegaChart.tsx](../frontend/src/components/VegaChart.tsx) embeds with
`renderer: 'svg'` — so they print at full resolution, and `dirOf` has already
solved the bidi problem that server-side rendering would start from zero.

Six things it needs:

1. **Self-host Vazirmatn.** [index.html](../frontend/index.html) loads it from
   `fonts.googleapis.com`. A BI tool pointed at a production database is
   routinely deployed behind a firewall, and there the Persian font silently
   falls back and the *deliverable* renders wrong. Drop the woff2 in `public/`
   and `@font-face` it.
2. **`await document.fonts.ready` before `window.print()`**, or the first print
   fires against fallback metrics.
3. **Force the light token set** in print regardless of the app theme — the
   oklch variables in `theme/tokens.ts` make this a small override block.
4. **`break-inside: avoid`** on section blocks, so a chart never splits across
   a page.
5. **A definite print width.** Vega's `width: 'container'` depends on the
   `ResizeObserver` dance in `VegaChart.tsx`, which does not run meaningfully
   in print context. The print container needs a fixed width in mm.
6. **Print the saved run, not live state.** A document that changes while it is
   being exported is not a document.

Server-side PDF is refused on purpose: it means shipping headless Chromium in
the API image, and Persian shaping in pure-Python PDF libraries is where this
reliably breaks.

---

## 12. API

Literal paths are declared **above** `/{id}` routes, as everywhere else.

```
GET    /reports                                  list (owner-scoped)
POST   /reports                                  create — disclosure gate here
GET    /reports/{id}                             report + outline
PATCH  /reports/{id}                             name, description, llm_config_id,
                                                 status. connection_id → 422
DELETE /reports/{id}

POST   /reports/{id}/outline                     propose (one model call, sync)
PUT    /reports/{id}/outline                     replace, after the user edits
POST   /reports/{id}/sections                    add one
PATCH  /reports/{id}/sections/{sid}              heading, intent, position
DELETE /reports/{id}/sections/{sid}
POST   /reports/{id}/sections/{sid}/blocks       add one
PATCH  /reports/{id}/blocks/{bid}                question, chart_config,
                                                 time_window, block_type
DELETE /reports/{id}/blocks/{bid}
POST   /reports/{id}/blocks/{bid}/check          feasibility — sync, one block

POST   /reports/{id}/runs                        start → 202
GET    /reports/{id}/runs                        history
GET    /reports/{id}/runs/{rid}                  run + every result — the poll target
POST   /reports/{id}/runs/{rid}/cancel
POST   /reports/{id}/runs/{rid}/sections/{sid}/retry
PATCH  /reports/{id}/runs/{rid}/sections/{sid}   edit prose → edited_prose
```

**Feasibility is synchronous and per block.** One `draft_sql` call is five to
ten seconds and the user is watching one heading — the same shape as the tile
editor's debounced guard check. Checking a whole outline is the frontend
looping over blocks with visible per-block progress, which reads better than a
job with a progress bar and needs no extra table.

Every route that returns a report or a run **resolves display names** and
returns the written row, so the page can splice it into state instead of
re-reading — the read-after-write race documented at the end of
[dashboards.md](dashboards.md) is app-wide and this feature must not walk into
it.

---

## 13. How the four invariants apply

1. **AST validation fails closed.** A third entry point to the guard, and it
   gets no exemption: `report_blocks.sql` is re-validated against the
   connection's *current* snapshot on every execution, through
   `execute_saved_sql` — the same function dashboards use. `sql_origin` grants
   nothing. A test replays the hostile corpus through a report block.
2. **Containment underneath correctness.** Blocks execute through the same
   connectors, read-only transaction, statement timeout and row cap. A block's
   `max_rows` may only tighten.
3. **Credentials encrypted, never exposed.** A report carries ids and display
   names. `test_openapi_has_no_secrets.py` walks every new DTO.
4. **Disclosure is explicit and visible.** §7. The create dialog shows the
   policy in force, the way the chat header does. Generation re-checks it.

**Owner-only in v1**, for the reason [dashboards.md §9](dashboards.md) gives: a
shared report means user B reads data pulled with user A's stored credentials
through a connection B does not own. That is an authorization model, not a UI
feature.

---

## 14. Decisions, and what would reopen them

| Decision | Why | Reopen when |
| --- | --- | --- |
| Connection pinned, model swappable | The conversation rule, for the same reason | never |
| N blocks per section | One paragraph over several results is the point | never |
| Relative dates in SQL, not parameters | The guard already allows the functions; `sql_hash` stays stable | Someone extends `QueryExecutor` with bind parameters — then filters and this share a mechanism |
| Reports refuse `NONE`/`AGGREGATE` | Prose and charts disagreeing in kind is worse than no report | Tier 3 substitution lands — then aggregate-only reports become coherent again |
| Run status derived, not set | Progressive rendering and per-section retry fall out for free | never |
| Prose edited on the run, not the template | A regeneration must not destroy writing; a template must not carry one run's wording | never |
| Browser PDF | SVG charts, loaded fonts, solved bidi, no new deployment unit | Scheduled server-side delivery is wanted — that needs a renderer anyway |
| v1 edits the question, not the SQL | The outline is the primary interaction; the tile editor is 1,300 lines | Phase 11 |
| Polled, not streamed | Minutes-long, and every partial result is already persisted | A run gets fast enough to feel like chat |
| Owner-only | An authorization model, not a UI feature | There is a real answer for "who may read through this connection" |

## 15. Not built

Sharing, scheduled generation and email delivery, run-to-run comparison, export
to Word/PowerPoint, and "add to report" from a chat run.

Two are cheap once the rest exists, and are the natural v2:

- **Run comparison.** `sql_hash` is on every block result precisely so this is
  possible — two runs whose block SQL matches can be diffed honestly. Nobody
  generates the same report twice without wanting the delta.
- **"Add to report" from chat.** A succeeded run already has validated SQL, a
  connection and a chart spec to copy into a block. dashboards.md calls the
  equivalent "the cheapest of these", and for reports it may be the most
  natural entry point of all: users explore in chat, then want the good turns
  collected into a document.

---
---

# Implementation plan

Eleven phases. Each is independently completable, independently verifiable, and
sized for one focused session. Each ends with the verification loop from
CLAUDE.md — **`make test` and `make lint` for backend, `npm run typecheck` and
`npm run build` for frontend** — and nothing is "done" until that passes.

Phases 1–6 are backend and leave the app fully working with no visible change.
Phases 7–9 are the UI. Phases 10–11 are polish and the deferred editor.

Standing rules for every phase:

- **Never `import litellm` outside `app/infra/llm/`.** CI greps for it.
- **`await db.refresh(obj)` after a PATCH** before `model_validate`, or the
  expired attribute raises `MissingGreenlet`.
- **Literal routes above `/{id}` routes.**
- **Return the written row** so the page can splice it — never force a re-read.
- Frozen dataclasses have no `__dict__`; serialise with `dataclasses.asdict`.

---

## Phase 1 — Data model and migration

**Goal.** Six tables exist and match the ORM. Nothing else changes.

**Build**
- The six models of §4 in `app/infra/db/models.py`, in a `# ── reports ──`
  block after the dashboards one.
- `alembic` migration `0008_reports.py`.
- `tests/unit/test_report_models.py` — replay the migration against a recorder
  and diff it against the ORM column by column, exactly as
  `test_dashboard_models.py` does. This test is why the two cannot drift.

**Do not build.** Services, API, DTOs, prompts.

**Watch.** FK ondelete is load-bearing and differs per column: `owner_id`
CASCADE, `connection_id` SET NULL, `block_id`/`section_id` on result tables SET
NULL with snapshot columns beside them.

**Done when** `make migrate` applies cleanly, `make test` passes, `make lint`
passes.

---

## Phase 2 — CRUD, the disclosure gate, and connection pinning

**Goal.** A user can create a report, hand-build an outline, and edit it. No
model is involved anywhere.

**Build**
- `app/services/report_service.py` — create / list / get / update / delete for
  reports, sections and blocks. Owner-scoped, transaction-owning.
- The disclosure gate: creation on a `NONE`/`AGGREGATE` connection is refused
  with `E_DISCLOSURE_TOO_NARROW` and §7's message.
- `connection_id` in a PATCH → **422**, mirroring `_bind_connection`.
- DTOs in `api/schemas.py`; router `api/v1/reports.py`; register it.
- `pyproject.toml`: add `app.reports` to the layers contract and add the
  self-contained forbidden contract.
- `tests/integration/test_reports_api.py` — CRUD, ownership isolation, the
  disclosure refusal, the 422, cascade behaviour.

**Do not build.** Outline proposal, feasibility, runs, prompts.

**Done when** the full CRUD surface works through the API, `make test` and
`make lint` pass, and `test_openapi_has_no_secrets.py` still passes.

---

## Phase 3 — Outline proposal

**Goal.** `POST /reports/{id}/outline` turns the user's request into a proposed
structure.

**Build**
- `app/reports/prompts.py` — `REPORT_PROMPT_VERSION`, `REPORT_OUTLINE_SYSTEM`,
  `REPORT_OUTLINE_USER`.
- `app/reports/outline.py` — the proposed-outline document as a Pydantic model
  with `extra="forbid"`; parse the model's JSON, validate it, drop malformed
  sections rather than failing the whole proposal.
- Service: build the schema block and the semantic layer render the same way
  `retrieve` does, one model call, persist sections and blocks.
- The executive-summary section is added automatically at position 0 with
  `kind = EXECUTIVE_SUMMARY`, and is removable.
- `tests/unit/test_report_outline.py` against a fake gateway — a good reply, a
  truncated reply, a reply with an unknown field, an empty reply.

**Do not build.** Feasibility, SQL, runs.

**Watch.** `app/reports/` is self-contained — the parser takes a string and a
dict, never a session. That is what makes this test cheap.

**Done when** a request produces a persisted, editable outline; `make lint`
proves the module stayed pure.

---

## Phase 4 — Feasibility, and relative time windows

**Goal.** `POST /reports/{id}/blocks/{bid}/check` answers *"can this be
produced, and if not, why"* — mechanically, from the guard.

**Build**
- `NodeDeps` gains an optional `extra_rules: str = ""`, appended to
  `GENERATE_SYSTEM`. **Empty by default; a test asserts the chat prompt is
  byte-identical to before.** `PROMPT_VERSION` does not move.
- `REPORT_TIME_RULES` in `app/reports/prompts.py`, dialect-aware, plus the
  connection's `time_conventions` from the semantic layer.
- The check calls `sql_draft_service.draft_sql` with those rules and maps the
  outcome onto `feasibility_status`:
  - `VALID` + preview has rows → `FEASIBLE`
  - `VALID` + preview empty → `EMPTY`, with a sentence saying the query works
    but no data falls in the window
  - guard rejected, or no SQL produced → `INFEASIBLE`, `feasibility_reason` set
    from `ValidationReport.errors[0].message` + `.hint`, **verbatim**
- Persist `sql`, `sql_hash`, `sql_origin = GENERATED`, and the chart
  suggestion; leave `chart_config` NULL (Auto).
- `tests/integration/test_report_feasibility.py` — each of the three outcomes,
  and that an unsynced connection is refused before a model call is spent.

**Do not build.** Bulk checking (the frontend loops), runs, prose.

**Done when** all three outcomes are reachable and reported with the guard's
own words, and the byte-identical-prompt test passes.

---

## Phase 5 — The generation worker: data only

**Goal.** A run executes every block and persists results progressively. No
prose yet.

**Build**
- `app/workers/report.py`, mirroring `workers/semantic.py`: `MAX_CONCURRENT_JOBS`,
  no heartbeat, cooperative-then-hard cancel, `sweep_orphans` at startup.
  Register both in `main.py`'s lifespan.
- `POST /reports/{id}/runs` → 202. `GET /reports/{id}/runs/{rid}` returns the
  run and every result written so far — the poll target.
- The disclosure re-check at run start (§7), failing the run with a clear
  message.
- Block execution through `query_service.execute_many`, grouped by connection.
  `want_kpi=True` for `METRIC` blocks.
- Write each `report_block_results` row **as it lands**, updating `phase` and
  `progress_current`.
- Run status derived: all OK → `SUCCEEDED`, some → `PARTIAL`, none → `FAILED`.
- `POST .../cancel`.
- `tests/integration/test_report_runs.py` — a run produces results with vega
  specs; a failing block does not fail its neighbours; cancellation lands;
  disclosure tightened after creation fails the run.
- `tests/unit/test_report_guard.py` — the hostile corpus written into
  `report_blocks.sql` is rejected at execution. **This is the guard-bypass
  test**, the equivalent of `test_query_service.py` for dashboards.

**Done when** a run completes end to end and `make guard` still passes.

---

## Phase 6 — Prose, the executive summary, and the numeric check

**Goal.** The run produces a readable document.

**Build**
- `REPORT_SECTION_SYSTEM` / `_USER` and `REPORT_SUMMARY_SYSTEM` / `_USER`
  (§10), with the language rules.
- `app/reports/narrate.py` — pure: given a section, its blocks' *disclosed*
  results, the language and the report's prompt, build the messages. Results go
  through `disclosure.disclose()`, never raw.
- `app/reports/checks.py` — the Tier 2 numeric check (§9). Pure, token-free,
  Persian and Latin numerals, rounding and scale tolerance. Returns findings;
  never raises, never blocks.
- The worker narrates section by section after the blocks land, writing each
  `report_section_results` row immediately, then the executive summary last.
- `SKIPPED_NO_DATA` when every block in a section is empty.
- `POST .../sections/{sid}/retry` and `PATCH .../sections/{sid}` (edit prose →
  `edited_prose`).
- `tests/unit/test_report_narrate.py` (prompt shape, disclosure applied,
  language pinned) and `tests/unit/test_report_checks.py` (matched, scaled,
  Persian numerals, a hallucinated figure flagged).

**Done when** a generated report reads as a document and a deliberately
hallucinated figure is flagged rather than silently kept.

---

## Phase 7 — Frontend: the section shell

**Goal.** Reports exists in the UI. A user can create one and see the list.

**Build**
- `App.tsx`: `'reports'` in the `View` union, a sidebar item, one route line.
- `api/types.ts` and `api/client.ts`: the report types and calls.
- `pages/ReportsPage.tsx`: list, create dialog, rename, delete, archive.
- The create dialog: connection picker with **ineligible connections disabled
  and the reason shown** (§7), model picker, language picker, the request
  textarea with `dir="auto"`.

**Do not build.** The outline editor, the viewer.

**Done when** a report can be created and listed; `npm run typecheck` and
`npm run build` pass.

---

## Phase 8 — Frontend: the outline editor

**Goal.** Propose, edit and validate a structure.

**Build**
- `components/report.tsx`: the outline editor — propose, then add / edit /
  remove / reorder sections and blocks.
- Per-block feasibility with an inline status chip and the guard's reason shown
  verbatim, the way `semantic.tsx` renders metric-expression errors.
- "Check all" loops the blocks with visible per-block progress.
- Generate is disabled while any block is `INFEASIBLE`, with the count and the
  first reason on the button's tooltip.
- `time_window` picker per block.

**Watch.** `semantic.tsx` is the precedent for a large editable document and is
worth reading before starting. `dir="auto"` on every free-text field.

**Done when** an outline can be built, validated and made generate-ready.

---

## Phase 9 — Frontend: the viewer

**Goal.** Watch a report build itself, then refine it.

**Build**
- Start a run, poll `GET /runs/{rid}`, render sections **as they arrive** with
  a progress header from `phase` / `progress_current` / `progress_total`.
- A section renders: heading, prose, and each block as chart / table / KPI —
  reusing `VegaChart`, `ResultTable` and the KPI component unchanged.
- Per-section retry on a failed section; the rest of the document stays.
- Chart type picker per block, populated from `chart_options` so impossible
  types are **disabled, not offered**.
- Prose editing in place, saving to `edited_prose`.
- Numeric-check findings shown as a subtle, dismissible marker.
- The poll **pauses on `document.hidden`**, as the dashboard scheduler does.

**Done when** a report generates progressively, a failed section retries alone,
and both charts and prose are editable.

---

## Phase 10 — PDF

**Goal.** A printable document that looks right in Persian.

**Build**
- Self-host Vazirmatn: woff2 in `public/`, `@font-face` in `styles.css`, drop
  it from the Google Fonts link.
- `@media print` in `styles.css`: forced light tokens, hidden chrome,
  `break-inside: avoid` on sections, a fixed print width.
- `components/report-print.ts`: `await document.fonts.ready`, re-embed charts
  at the print width, then `window.print()`.
- Print the **saved run**, never live state.
- Manual check on both a Persian and an English report, both themes.

**Done when** a Persian report prints with correct shaping, correct direction,
and unbroken charts.

---

## Phase 11 — Run history, regeneration, and the SQL editor

**Goal.** Close the loop the whole feature exists for.

**Build**
- Run history per report: pick a past run and read it, unchanged.
- Regenerate: same outline, new run, previous runs untouched. Show at a glance
  which blocks' `sql_hash` changed since the last run — the groundwork for
  comparison.
- The deferred SQL editor: edit a block's SQL directly, guarded and previewed
  through `sql_draft_service.validate_sql`, setting `sql_origin` to
  `GENERATED_EDITED` or `HANDWRITTEN`. This is the tile-editor pattern and can
  reuse much of its shape.
- `docs/reports.md` — the *shipped* reference doc, written from this plan and
  describing what actually exists, peer to `dashboards.md`. This file
  (`reports-plan.md`) stays as the record of what was intended and why.
- A Reports paragraph in `README.md` and in `CLAUDE.md`'s code map.

**Done when** a report created today can be regenerated against tomorrow's data
with its structure intact and its history readable.

---
---

# Progress checklist

Tick each item as it lands. A phase is **not** complete until its verification
gate passes — that line is the gate, not a formality.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Phase 1 — Data model and migration

- [ ] Six models in `infra/db/models.py` under a `# ── reports ──` block
- [ ] `reports` · `report_sections` · `report_blocks`
- [ ] `report_runs` · `report_block_results` · `report_section_results`
- [ ] FK `ondelete` correct per column (owner CASCADE, connection SET NULL,
      result-table back-refs SET NULL with snapshot columns beside them)
- [ ] Migration `0008_reports.py`
- [ ] `tests/unit/test_report_models.py` — migration vs ORM, column by column
- [ ] **Gate:** `make migrate` · `make test` · `make lint`

## Phase 2 — CRUD, disclosure gate, connection pinning

- [ ] `services/report_service.py` — reports / sections / blocks CRUD
- [ ] Disclosure gate at creation → `E_DISCLOSURE_TOO_NARROW`
- [ ] `connection_id` in PATCH → 422
- [ ] DTOs in `api/schemas.py`, router `api/v1/reports.py`, registered
- [ ] Literal routes declared above `/{id}` routes
- [ ] `pyproject.toml` — `app.reports` in the layers contract
- [ ] `pyproject.toml` — `reports is self-contained` forbidden contract
- [ ] `tests/integration/test_reports_api.py` — CRUD, ownership isolation,
      disclosure refusal, the 422, cascades
- [ ] **Gate:** `make test` · `make lint` · `test_openapi_has_no_secrets` passes

## Phase 3 — Outline proposal

- [ ] `app/reports/prompts.py` — `REPORT_PROMPT_VERSION` + `REPORT_OUTLINE_*`
- [ ] `app/reports/outline.py` — Pydantic doc, `extra="forbid"`, malformed
      sections dropped rather than failing the proposal
- [ ] `POST /reports/{id}/outline` — schema block + semantic layer, one call
- [ ] Executive-summary section auto-added at position 0, removable
- [ ] `tests/unit/test_report_outline.py` — good / truncated / unknown-field /
      empty replies against a fake gateway
- [ ] **Gate:** `make test` · `make lint` (proves the module stayed pure)

## Phase 4 — Feasibility and relative time windows

- [ ] `NodeDeps.extra_rules: str = ""`, appended to `GENERATE_SYSTEM`
- [ ] Test asserting the chat prompt is **byte-identical** with it empty
- [ ] `PROMPT_VERSION` deliberately **not** moved
- [ ] `REPORT_TIME_RULES`, dialect-aware, + `time_conventions` from the
      semantic layer
- [ ] `POST /reports/{id}/blocks/{bid}/check` via `draft_sql`
- [ ] `FEASIBLE` — valid SQL, preview has rows
- [ ] `EMPTY` — valid SQL, no rows in the window, with a sentence saying so
- [ ] `INFEASIBLE` — reason from `ValidationReport.errors[0]` **verbatim**
- [ ] Persists `sql`, `sql_hash`, `sql_origin`; leaves `chart_config` NULL
- [ ] Unsynced connection refused **before** a model call is spent
- [ ] `tests/integration/test_report_feasibility.py`
- [ ] **Gate:** `make test` · `make lint`

## Phase 5 — Generation worker: data only

- [ ] `workers/report.py` — concurrency cap, no heartbeat,
      cooperative-then-hard cancel, `sweep_orphans`
- [ ] Both registered in `main.py` lifespan
- [ ] `POST /reports/{id}/runs` → 202
- [ ] `GET /reports/{id}/runs/{rid}` — run + everything written so far
- [ ] Disclosure **re-checked at run start**, run fails with a clear message
- [ ] Blocks executed via `execute_many`, grouped by connection
- [ ] `want_kpi=True` for `METRIC` blocks
- [ ] Each block result written **as it lands**; `phase` / `progress_*` updated
- [ ] Run status **derived**: `SUCCEEDED` / `PARTIAL` / `FAILED`
- [ ] `POST .../cancel`
- [ ] `tests/integration/test_report_runs.py` — end to end, one failing block
      does not fail its neighbours, cancellation, tightened disclosure
- [ ] `tests/unit/test_report_guard.py` — **the guard-bypass test**: the
      hostile corpus written into `report_blocks.sql` is rejected at execution
- [ ] **Gate:** `make test` · `make guard` · `make lint`

## Phase 6 — Prose, executive summary, numeric check

- [ ] `REPORT_SECTION_*` and `REPORT_SUMMARY_*` prompts
- [ ] Language pinned per report and stated explicitly in the prompt
- [ ] `ANSWER_SYSTEM` deliberately **not** reused
- [ ] `app/reports/narrate.py` — pure; results pass through `disclose()`,
      never raw
- [ ] `app/reports/checks.py` — Tier 2 numeric check, token-free, Persian and
      Latin numerals, rounding and scale tolerance
- [ ] Check **flags, never blocks**
- [ ] Worker narrates section by section, writing each row immediately
- [ ] Executive summary generated **last**, from finished sections
- [ ] `SKIPPED_NO_DATA` when every block in a section is empty
- [ ] `POST .../sections/{sid}/retry`
- [ ] `PATCH .../sections/{sid}` → `edited_prose`
- [ ] `tests/unit/test_report_narrate.py` · `tests/unit/test_report_checks.py`
- [ ] **Gate:** `make test` · `make lint`

## Phase 7 — Frontend: the section shell

- [ ] `App.tsx` — `'reports'` in `View`, sidebar item, route line
- [ ] `api/types.ts` and `api/client.ts` — report types and calls
- [ ] `pages/ReportsPage.tsx` — list, create, rename, delete, archive
- [ ] Create dialog: connection picker with ineligible connections **disabled
      and the reason shown**
- [ ] Model picker, language picker, request textarea with `dir="auto"`
- [ ] Returned rows spliced into state, never re-read (the read-after-write race)
- [ ] **Gate:** `npm run typecheck` · `npm run build`

## Phase 8 — Frontend: the outline editor

- [ ] `components/report.tsx` — propose, add / edit / remove / reorder sections
- [ ] Blocks add / edit / remove / reorder within a section
- [ ] Per-block feasibility chip with the guard's reason shown verbatim
- [ ] "Check all" loops blocks with visible per-block progress
- [ ] Generate disabled while any block is `INFEASIBLE`, with count + first reason
- [ ] `time_window` picker per block
- [ ] `dir="auto"` on every free-text field
- [ ] **Gate:** `npm run typecheck` · `npm run build`

## Phase 9 — Frontend: the viewer

- [ ] Start a run, poll `GET /runs/{rid}`
- [ ] Sections render **as they arrive**; progress header from `phase` /
      `progress_current` / `progress_total`
- [ ] Blocks render as chart / table / KPI, reusing `VegaChart`,
      `ResultTable` and the KPI component unchanged
- [ ] Per-section retry; the rest of the document stays on screen
- [ ] Chart type picker from `chart_options` — impossible types **disabled,
      not offered**
- [ ] Prose editing in place → `edited_prose`
- [ ] Numeric-check findings as a subtle, dismissible marker
- [ ] Poll **pauses on `document.hidden`**
- [ ] **Gate:** `npm run typecheck` · `npm run build`

## Phase 10 — PDF

- [ ] Vazirmatn woff2 self-hosted in `public/` with `@font-face`
- [ ] Dropped from the Google Fonts link in `index.html`
- [ ] `@media print` — forced light tokens, chrome hidden
- [ ] `break-inside: avoid` on sections
- [ ] Fixed print width in mm
- [ ] `components/report-print.ts` — `await document.fonts.ready`, re-embed
      charts at print width, then `window.print()`
- [ ] Prints the **saved run**, never live state
- [ ] Manual check: Persian report, correct shaping and direction
- [ ] Manual check: English report
- [ ] Manual check: both themes, charts unbroken across pages
- [ ] **Gate:** `npm run typecheck` · `npm run build` + the manual checks

## Phase 11 — Run history, regeneration, SQL editor

- [ ] Run history per report; a past run reads back unchanged
- [ ] Regenerate: same outline, new run, previous runs untouched
- [ ] Show which blocks' `sql_hash` changed since the last run
- [ ] SQL editor per block via `sql_draft_service.validate_sql`
- [ ] `sql_origin` → `GENERATED_EDITED` / `HANDWRITTEN`
- [ ] `docs/reports.md` written — the shipped reference doc
- [ ] `README.md` — a Reports paragraph
- [ ] `CLAUDE.md` — Reports in the code map
- [ ] **Gate:** `make test` · `make guard` · `make lint` ·
      `npm run typecheck` · `npm run build`

---

## Cross-cutting — verify before calling the feature done

- [ ] `make guard` — zero bypasses, with report blocks in the corpus
- [ ] `make lint` — import-linter contracts hold for `app.reports`
- [ ] `test_openapi_has_no_secrets.py` — walks every report DTO
- [ ] No `import litellm` outside `app/infra/llm/`
- [ ] A report on a `NONE`/`AGGREGATE` connection cannot be created **and**
      cannot be generated if the policy was tightened afterwards
- [ ] A chat run's prompt is byte-identical to pre-feature (the `extra_rules`
      test)
- [ ] A deleted connection leaves past runs readable
- [ ] A deleted block leaves past runs readable
- [ ] Regeneration never overwrites `edited_prose` on a previous run
- [ ] Persian and English reports both generate, render, and print correctly
