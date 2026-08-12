# The report pipelines, node by node

What happens between "user types a request" and "a document with figures,
paragraphs and an executive summary". Companion to
[pipeline.md](pipeline.md) (the chat run, and the shared machinery every
pipeline sits on), [pipeline-dashboard.md](pipeline-dashboard.md) (tiles), and
[reports.md](reports.md) (the data model, the API, the UI and the print
handoff). **This file is only the pipelines**: the flows, the nodes, the exact
prompts, and what happens when each step fails.

Code: [`backend/app/reports/`](../backend/app/reports/) —
`outline.py` (propose a structure and read the reply), `narrate.py` (the prose
prompts), `facts.py` (the arithmetic), `checks.py` (the numeric check),
`language.py`, `prompts.py` (`REPORT_PROMPT_VERSION`);
[`workers/report.py`](../backend/app/workers/report.py) (the generation run);
[`services/report_service.py`](../backend/app/services/report_service.py) (the
gates, the outline write, the feasibility check);
[`services/sql_draft_service.py`](../backend/app/services/sql_draft_service.py)
(question → SQL, shared with tiles).

---

## 1. Four flows, not one

A report is the only surface in the product where the model is asked to do work
at **two different times**: once while the user is building the template
(structure, then SQL per block), and again months later while a run is being
generated (prose over fresh numbers). Keeping those apart is the whole design —
the structure a human approved does not get re-proposed on every run, and the
statement that produced last quarter's figure is the statement that produces
this quarter's.

| | A. Propose outline | B. Check a block | C. Generate a run | D. Retry a section |
|---|---|---|---|---|
| **Trigger** | `POST /reports/{id}/outline` | `POST /reports/{id}/blocks/{bid}/check`, or `PUT .../sql` | `POST /reports/{id}/runs` | `POST /reports/{id}/runs/{rid}/sections/{sid}/retry` |
| **Shape** | synchronous, one request | synchronous, one request per block | **202 + poll** | 202 + the poll already running |
| **Model calls** | 1 (`complete`) | 2 (`route` + `generate`), +1 per repair · 0 on the hand-written road | 1 per section + 1 summary | 1 |
| **Database** | none (schema snapshot only) | 1 preview query (≤50 rows) | 1 query per block | 1 query per block of that section |
| **Writes** | replaces `report_sections` + `report_blocks` | `report_blocks.sql`, `sql_hash`, `feasibility_*` | `report_runs`, `report_block_results`, `report_section_results` | replaces that section's rows |
| **Failure posture** | raise, nothing written | **store the verdict** — never raise for "no" | per-section; status derived | per-section; status re-derived |
| **Prompts** | `REPORT_OUTLINE_SYSTEM` / `_USER` | `GENERATE_SYSTEM` + `REPORT_TIME_RULES` | `REPORT_SECTION_*`, `REPORT_SUMMARY_*` | same as C |

Two rules cut across all four, and both are re-checked rather than inherited:

- **Disclosure is refused at `NONE` and `AGGREGATE`.** Prose written from no
  values beside charts drawn from real ones is a document that disagrees with
  itself. `assert_wide_enough` is checked at report creation, at run creation,
  at the *start of the generation*, and at the start of a retry — four times,
  because a policy tightened between any two of them must stop the work
  ([report_service.py:197-213](../backend/app/services/report_service.py#L197-L213)).
- **The connection is pinned.** A report is bound to the connection it was
  created against; re-sending the same one is a no-op, naming a different one
  is 422. Same rule, same reason, as `run_service._bind_connection`: a template
  that could cross connections could cross disclosure policies.

---

## 2. Flow A — propose an outline

**One model call turns a sentence into a structure the user can approve.**
Everything downstream — the SQL, the prose, the summary — is written against
what this call returns, which is why it is the one flow that is allowed to
replace the user's work outright (proposing is the "start again" button; the
section and block routes are how an outline is *edited*).

### 2.1 The gates, before a token is spent

`ReportService.propose_outline`
([report_service.py:435-507](../backend/app/services/report_service.py#L435-L507))
refuses in this order, each with its own message:

| # | Refused when | Why here rather than later |
|---|---|---|
| 1 | report not owned by the caller | 404, not 403 — someone else's report is indistinguishable from a missing one |
| 2 | `report.prompt` is empty | the request *is* the input; there is nothing to propose from |
| 3 | `report.connection_id` is NULL | the connection was deleted; past runs stay readable, new work does not start |
| 4 | `report.llm_config_id` is NULL | no model chosen |
| 5 | the connection's snapshot has no tables | an outline proposed against an unsynced connection would be **invention** — every question would name a table nobody can prove exists |

### 2.2 What is sent

The prompt is built from the same block the SQL generator will see, under the
same disclosure budget:

```python
context = RetrievedContext(
    dialect=snapshot["dialect"],
    tables=snapshot["tables"],              # the whole snapshot, not a retrieval
    relationships=snapshot["relationships"],
    semantic=semantic.model_dump() if semantic else None,
)
schema_block = context.render(connection.disclosure_policy)
```

Note what this is **not**: there is no `retrieve` node here. An outline is about
the whole database, so the whole snapshot goes — `HintBudget`-gated exactly as
everywhere else, with the semantic layer scoped to those tables by
`render_semantic`. There is no `_RETRIEVE_BUDGET_CHARS` ceiling on this path,
which is the one place a very wide schema can produce a very large prompt (§8).

**System prompt — `REPORT_OUTLINE_SYSTEM`**
([prompts.py:65-157](../backend/app/reports/prompts.py#L65-L157)). Its rules,
and what each one is protecting against:

| Rule | The failure it prevents |
|---|---|
| a **block** is one question → one query → one figure; a **section** groups blocks one paragraph can narrate together | a section whose blocks have nothing to do with each other produces a paragraph that changes subject mid-sentence |
| the outline is an **argument**, not an inventory: level → movement → composition → concentration → quality/risk, *in that order* | "one chart per table" is a dashboard someone has to interpret |
| return **exactly** the number of sections the request asks for | the length is the user's; house style baked into a constant is what r3 removed |
| 1–3 blocks per section; a section with no blocks is not a section | a heading rendered above empty space |
| write `heading`, `intent`, `title`, `question` in the language named in the user message | section three coming back in English because its heading was a metric name |
| `title` is a **statement**, never a question ("Monthly revenue, last twelve months") | a figure captioned with its question reads as a transcript of the session that produced it |
| `question` is what must be *asked of the database*, in full | the title and the question say the same thing in two registers; a title naming a different measure is a mislabelled figure |
| `intent` is an instruction to the writer, never a subtitle | the reader never sees it; it is what the section's paragraph must establish |
| at least one `METRIC` block | the document opens on a headline figure rather than a chart |
| two sections must not ask the same question in different words | the commonest complaint about generated reports: "it repeats itself" |
| every question answerable by one SQL query over the tables shown; never name a table or column that is not there | a block that can never pass the feasibility check |
| no executive summary, introduction, methodology or conclusion | the summary is written *for* the model in flow C, from the finished sections |
| JSON only, no markdown, no SQL | see §2.3 — it arrives fenced anyway |

**User prompt — `REPORT_OUTLINE_USER`**: language (as an endonym, not a code —
`LANGUAGE_NAMES`), the exact section count, the dialect, the request verbatim,
then the schema block. The count rides in the *user* message deliberately: the
system prompt is house style and identical for every report, so it stays
cacheable ([outline.py:146-176](../backend/app/reports/outline.py#L146-L176)).

**The model is asked with `complete`, not `structured`** — the one deliberate
exception in the product. `structured` fails the whole reply when the JSON will
not parse, and the reply this call gets is the longest thing the feature asks
for: "will not parse" here usually means "was cut off after four good
sections". The output budget is floored at `OUTLINE_MIN_MAX_TOKENS = 6_144`
whatever the provider row says.

### 2.3 Reading the reply — a malformed part costs that part, never the proposal

`outline.parse` ([outline.py:216-246](../backend/app/reports/outline.py#L216-L246))
is a salvage parser, in three descending readings
(`_candidates` → `_salvage`):

1. **The document as asked for** — `{"sections": [...]}`. Also accepts
   `outline` / `report` as the wrapper key, a bare list, or a single section
   object with a `heading`.
2. **A fence stripped** — `_unfence` removes ```` ```json ````, because models
   add one however firmly asked not to.
3. **Object-by-object recovery** — `_salvage` scans with `json.JSONDecoder.raw_decode`
   and keeps every object that closed. A document truncated inside its fifth
   section still yields the four before it. Objects with no `heading` are the
   *blocks* of the section that never closed, and are dropped — a block promoted
   to a section arrives with no heading and nothing under it.

Then, per candidate ([`_section`](../backend/app/reports/outline.py#L256-L311)):

| Condition | Cost | Why |
|---|---|---|
| block fails `ProposedBlock` validation (`extra="forbid"`, bad enum) | **that block** | its section keeps the questions that were fine |
| block has an empty question after `_clean` | that block | |
| more than `MAX_BLOCKS_PER_SECTION` (4) blocks | the excess | every block is a query *and* a model call at generation time |
| section fails `ProposedSection` validation | **that section** | an answer in a shape we did not ask for has fields we cannot safely guess the meaning of |
| section has no heading, or no surviving blocks | that section | |
| heading duplicates an earlier one (casefolded) | that section | two sections with one heading render as the same heading twice |
| more sections than the user asked for | the excess, trimmed | the extra sections are the model's opinion, not the user's |
| *fewer* sections than asked for | **kept as-is** | four good sections the user can add a fifth to beats a refusal |

`dropped_sections` / `dropped_blocks` are counted and logged
(`report_outline_salvaged`) — the only signal that a model is answering in a
shape the parser keeps having to rescue.

**The single failure**: `proposal.is_empty` → `LLMError`, *"The model did not
return an outline that could be read."* Nothing is written; the existing
outline survives untouched.

### 2.4 What is written

`_replace_outline` deletes every existing section (blocks cascade) and writes
the proposal, with the **executive summary inserted at position 0**
(`kind = EXECUTIVE_SUMMARY`, no blocks, heading and intent from
`outline.EXECUTIVE_SUMMARY` in the report's language). It is an ordinary
section otherwise — removable, editable, reorderable — which is why its wording
lives in `outline.py` and not in a prompt.

Every block is written with **no SQL and `feasibility_status = UNCHECKED`**. A
proposed question has never been near the guard, and flow B is what changes
that.

---

## 3. Flow B — a block's question becomes a statement

Two roads, one guard, one stored verdict. Both end in the same four columns:
`sql`, `sql_hash`, `sql_origin`, `feasibility_*`.

```
POST .../blocks/{id}/check          PUT .../blocks/{id}/sql
        │ a model writes it                 │ a human wrote it
        ▼                                   ▼
   draft_sql(classify=True,            validate_sql(sql)
             extra_rules=TIME_RULES)        │ no model at all
        │                                   │
        ├── route ──► not ANALYTICAL ──► QuestionOutOfScopeError ──┐
        ├── retrieve                                               │
        ├── generate ◄──┐                                          │
        ├── validate ───┘ one repair                               │
        ▼                                                          ▼
   guard verdict ──► preview (execute_saved_sql, ≤50 rows) ──► _verdict()
                                                                   │
                             FEASIBLE · EMPTY · INFEASIBLE ────────┘ stored on the block
```

### 3.1 `route` runs here, and only here outside chat

`draft_sql(..., classify=True)`
([sql_draft_service.py:169-178](../backend/app/services/sql_draft_service.py#L169-L178))
runs the chat pipeline's `route` node before anything else, and refuses
anything that is not `ANALYTICAL`.

**Why a report block needs this and a dashboard tile does not:** the guard reads
a *statement* — is it a SELECT, do its names resolve, is it safe — and *"how is
the weather"* produces a statement that passes all three. Nothing downstream of
`generate` ever asks whether the question had a data answer to begin with; chat
escapes this only because `route` halts the run before any SQL is written. A
report block is the one other place where the answer is **stored rather than
shown**, so without this check the block goes green and reaches a run as a
figure nobody asked for.

The refusals are written for a stored verdict, not for a chat reply
(`_OUT_OF_SCOPE`, [sql_draft_service.py:75-91](../backend/app/services/sql_draft_service.py#L75-L91)):
CHITCHAT, UNSUPPORTED and **METADATA** each get their own sentence — a schema
question is refused because "a list of table names is not a figure".

`route` fails open to ANALYTICAL on a provider error, so a flaky model refuses
nothing and widens nothing.

### 3.2 The borrowed nodes

After `route`, the draft path calls the chat pipeline's own nodes directly —
not through `AnalyticsPipeline`:

```python
await retrieve(state, deps)
for _ in range(DRAFT_MAX_REPAIRS + 1):        # 2 attempts, max
    result = await generate(state, deps)
    if result.status == "FAILED":             # LLMError → raise LLMError
        raise LLMError(...)
    if (await validate(state, deps)).goto != "generate":
        break
```

So a block's statement is written against the **same** schema block, the same
`HintBudget`, the same semantic layer and the same `_SQL_RULES` a chat question
gets — and is refused by the same guard. What differs, deliberately:

| | chat run | report block draft |
|---|---|---|
| history | last ≤6 turns | **`history=[]`** — a block has no conversation, and inventing one would put another connection's answers in the prompt |
| `max_rows` | connection's cap | `PREVIEW_MAX_ROWS = 50` |
| repairs | `max_repairs` (1) | `DRAFT_MAX_REPAIRS` (1) |
| extra rules | none | `REPORT_TIME_RULES` (§3.3) |
| persistence | run, steps, events, messages | **nothing** until the verdict is stored |
| deadline | enforced by the executor before every node | `DRAFT_DEADLINE_SECONDS` (120s), checked before each `generate` (§8, note 1) |

### 3.3 `REPORT_TIME_RULES` — the feature turns on this paragraph

Appended to `GENERATE_SYSTEM` (and to `REPAIR_SYSTEM` / `REVIEW_SYSTEM`, since
a repair is a fresh conversation) through `NodeDeps.extra_rules`
([prompts.py:208-220](../backend/app/reports/prompts.py#L208-L220)):

- names the block's window as a phrase (`TIME_WINDOW_PHRASES`: "the last 3
  months", "the previous quarter", "this year, to date"…);
- shows the **dialect's own** date arithmetic (`DIALECT_DATE_ARITHMETIC`) —
  Postgres `CURRENT_DATE - INTERVAL '3 months'`, MySQL `DATE_SUB(...)`, SQL
  Server `DATEADD(...)`, Oracle `TRUNC(SYSDATE) - INTERVAL '3' MONTH` (**not**
  `ADD_MONTHS`, which parses to a node the AST allowlist does not carry and is
  rejected `E_NODE_NOT_ALLOWED`);
- forbids a literal date, year, month name or quarter as a constant;
- appends the connection's **own** time conventions from the semantic layer
  (`_render_time`) — fiscal year start, week start, whether "last month" is
  calendar or rolling. Per connection, never per report: that is a fact about
  the database.

Why it matters: the same statement runs again months from now and must describe
the period *then*. Regenerating the SQL instead would break `sql_hash`
comparison and the promise that the approved structure is preserved.

`extra_rules` is empty for every other caller, and empty means *byte-identical*
— which is why `PROMPT_VERSION` does not move for this feature, and there is a
test that says so.

### 3.4 The preview

A statement the guard accepted is run immediately through
`execute_saved_sql` — the same function that will run it at 03:00 in a
generation — capped at 50 rows. What the user approves is what will actually
run. The preview carries a `chart_suggestion` (the *heuristic*, not a model —
`plan_chart` over the profile) and `chart_options` (per-type verdicts, so the
picker disables what will not work rather than offering it and apologising
later).

**The heuristic here is a choice, not a limitation.** `draft_sql` can spend a
second call asking a model what the result should be *drawn* as
(`compose_chart`), and a dashboard tile does exactly that — see
[pipeline-dashboard.md §2 A1](pipeline-dashboard.md). A report block leaves it
off, and the reason is the rule in §4.3 that a block persists none of its three
chart fields: `chart_config` stays NULL — the common case and the right default
— so a run months from now may re-decide over a differently-shaped result. An answer to "what did they mean to see" would be computed,
returned once to the picker, and then discarded unread — a token spent on a
value nothing keeps.

So the two opt-ins on this shared function run **opposite ways**, and each
caller pays for the question whose answer it stores: a block passes
`classify=True` because its answer is stored and read months later, while a
tile passes `compose_chart=True` because its *picture* is stored and redrawn
for as long as the tile lives.

### 3.5 The verdict ladder

`_verdict(draft)`
([report_service.py:120-168](../backend/app/services/report_service.py#L120-L168))
turns everything above into one stored answer:

| Outcome | Stored status | Reason stored |
|---|---|---|
| guard rejected | `INFEASIBLE` | the **guard's own words** — first ERROR-severity issue's `message` + `hint`, verbatim. A rewritten explanation is a second vocabulary that drifts from the rule that produced it |
| valid, but the preview errored (timeout, permission, broken view) | `INFEASIBLE` | the database's message. Not the guard's doing and not fixable by rewording, but still "this cannot be produced" |
| valid, preview returned **0 rows** | `EMPTY` | *not a failure*: the query works and the answer is "nothing happened", which a report may legitimately want to say |
| valid, preview returned rows | `FEASIBLE` | — |
| `route` said not-ANALYTICAL | `INFEASIBLE` | the `_OUT_OF_SCOPE` sentence. Deliberately lands in the same row a rejected statement does, so the block reads the same way whichever gate stopped it |
| `LLMError` (model produced nothing usable) | `INFEASIBLE` | *"the model could not produce a query"* **is** the answer to the question this route asks |

**Every outcome is a stored answer, never an exception.** A 502 here would
leave the block saying `UNCHECKED` with the reason only in a toast — and the
question this route asks ("can this be produced?") has a legitimate negative
answer. A `VALID` draft also writes `sql`, `sql_hash` and
`sql_origin = GENERATED`.

> One thing worth knowing: `ValidationReport.errors` is a *property* over
> `issues`, and `model_dump()` emits declared fields only. Reading `"errors"`
> off the serialised report found nothing, so every rejection once wore the
> fallback sentence instead of the guard's. The filter is applied on `issues`
> for that reason — don't "simplify" it back.

### 3.6 The hand-written road

`PUT /reports/{id}/blocks/{bid}/sql` → `edit_block_sql` → `validate_sql`:
guard, preview, verdict, stored row — the same shape with **no model asked**.
This is what makes a report buildable by someone who knows SQL better than they
know their own question, and buildable with no LLM provider configured at all.

One deliberate asymmetry: **a rejected hand-written statement is kept**, where
a rejected *draft* is thrown away. Not an inconsistency — the semantic layer
already settled it: an invalid generated metric is dropped, an invalid
human-written one is flagged and kept, because *deleting a person's work to
hide drift is worse than showing it*. A model draft costs one click to
reproduce; what somebody typed does not. `sql_origin` becomes `HANDWRITTEN`, or
`GENERATED_EDITED` if the block ever held a generated statement — provenance
only, **never a trust signal**, and the guard reads the statement again at
execution whatever is stored beside it.

### 3.7 Editing a question un-checks it

`update_block`: if `question` or `time_window` changed
(`_SQL_INVALIDATING`), the verdict is always reset to `UNCHECKED` — a run must
not produce the right numbers under the wrong heading. Whether the *statement*
survives depends on who wrote it: `sql_origin == GENERATED` → dropped;
hand-written or hand-edited → kept, because losing an hour of SQL to a typo fix
is the kind of thing a person never forgives a tool for. Either way nothing
runs on it until the user says the two still belong together.

---

## 4. Flow C — the generation run

The one long-running pipeline in the product: minutes of database and provider
latency, so it is queued (**202**) and polled rather than streamed.
`POST /reports/{id}/runs` commits the row *before* submitting to the executor,
or the worker would race the transaction that created the row it is about to
load.

### 4.1 Everything refused before a run is queued

`create_run` ([report_service.py:694-763](../backend/app/services/report_service.py#L694-L763)):
a second concurrent run (409 `ConflictError`), a removed connection, a
disclosure policy that has since narrowed, a missing model configuration, and —
last — **an outline where no block has SQL** (*"an unchecked block has no SQL to
run"*), because a run of nothing but those produces a document of error
messages.

The row snapshots `llm_config_id`, `model_snapshot` (provider + model),
`prompt_version` (`REPORT_PROMPT_VERSION`), `language`, and `progress_total`
(blocks + sections, the summary counted as a section because writing it is a
step too).

### 4.2 The executor

`ReportRunExecutor` ([workers/report.py](../backend/app/workers/report.py)) —
mirrors `workers/semantic.py`, deliberately does not share it:

- `MAX_CONCURRENT_JOBS = 2`. A generation already runs its blocks concurrently
  against the customer's database; two whole reports at once is a load test of
  it, not a speed-up.
- **No heartbeat.** Durability is the `report_runs` row plus `sweep_orphans()`
  at startup, which turns a run stranded by a dead process into `FAILED` with
  *"the server restarted… whatever had already been computed was kept"*. Honest,
  because the results that did land are still there to read.
- **Cancellation is cooperative *then* hard**: the flag is checked between
  phases so an in-flight query finishes rather than being abandoned; the task is
  cancelled outright `llm_request_timeout_seconds + 5` later if it has not
  stopped. The API writes `CANCELLED` on the row itself so the next poll says so
  immediately — and `_touch` refuses to write progress over a run that is
  already `CANCELLED`.
- A crash anywhere is caught: `_finish(FAILED, error=str(err)[:500])`. **Never a
  bare 500, never a run left `RUNNING`.**

### 4.3 The run, node by node

`generate_run` → `_generate`
([workers/report.py:279-425](../backend/app/workers/report.py#L279-L425)). Five
phases; the ordering is the design.

#### C1 · Load and re-check

Report gone → FAILED (*"the report was removed"*). Connection gone → FAILED
(*"…past runs stay readable"*). Then `assert_wide_enough(connection)` again —
the gate at creation says what was true *then*.

#### C2 · Read the outline

`_outline(db, report_id)` returns sections in reading order and blocks in
document order. **The section travels with the block** because its heading is
copied onto the result row: a run has to stay readable after the section it came
from is renamed or deleted.

Progress is set here: `progress_total = len(blocks) + len(sections)`, phase
`"Running N queries"`.

#### C3 · Execute every block — one pass, one connector

`_execute_blocks` maps each block with a non-empty `sql` to a `TileRequest` and
calls `execute_many` — **the same batch path a dashboard refresh uses**:
grouped by connection, one connector per group, `MAX_CONCURRENT_TILES = 4`
under a semaphore, every snapshot read *before* the fan-out (an `AsyncSession`
is not safe for concurrent use).

Per block, `execute_saved_sql` re-validates the statement against the
connection's **current** snapshot. `report_blocks.sql` is a third entry point to
the guard and gets **no exemption**; `sql_origin` grants nothing.
`tests/unit/test_report_guard.py` replays the hostile corpus through it.

- `want_kpi = (block_type == METRIC)` → `plan_kpi` computes the headline figure
  here, from the rows, so no model is ever asked to do arithmetic.
- `chart_intent` is the block's stored `ChartIntent`, or **None for Auto** —
  NULL is the common case and the right default, because a run months from now
  may see a differently-shaped result and `plan_chart` should re-decide. A
  malformed stored intent is treated as Auto (logged
  `report_block_chart_config_unreadable`), for the same reason a tile does: the
  numbers are correct whatever is wrong with the picture.
- A block with **no SQL** never reaches the database; it gets
  `_no_sql_result()` → `E_SQL_MISSING`, *"check it in the outline, then generate
  again"*.

#### C4 · Write every result row, immediately

`_block_result` snapshots, per block: `heading_snapshot`, `title_snapshot`,
`question_snapshot`, `sql_text`, `sql_hash`, columns, rows, `row_count`,
`truncated`, `vega_spec`, `chart_source`, `chart_note`, `kpi`, `computed_at`,
`duration_ms`, and `OK`/`FAILED` with the error code and message.

Copied, not referenced: `block_id` is `SET NULL` on delete precisely so this row
survives the block being deleted. **Every row is written the moment it exists,
each in its own commit** — the poll response is then a snapshot of what has
landed, which is the whole progressive-rendering design. No special protocol,
and a browser that reloads mid-run resumes exactly where it was.

Cancellation is checked here: results already paid for are **kept**, because a
cancelled run that threw them away would be a slower way of doing nothing.

#### C5 · Per section, in order — the paragraph

For each non-summary section (`_narrate`,
[workers/report.py:825-917](../backend/app/workers/report.py#L825-L917)):

**1. Disclose.** `_narration(result, policy)` runs each stored result through
`disclose()` — at *narration* time, under the policy in force **now**, not the
one in force when the query ran. This is the only place a report's results meet
the disclosure gate. (`app.reports` sits *below* `app.pipeline` in the layer
order, so `narrate.py` **cannot** call `disclose()` even if it wanted to — the
import-linter contract enforces the stricter reading of invariant #4 for free.)

**2. Compute the facts.** `facts.compute(columns, rows, complete=...)` —
totals, changes, growth rates, shares, ranks, concentration, spread. Handed to
the writer under `FACTS_HEADER`: *"Figures computed from this result — these are
exact. Quote them rather than working the arithmetic out yourself."*

> **`complete=(not truncated and len(rows) == row_count)`** is the safety rule
> of that module. Under `SAMPLE`, `disclose()` hands over the first 50 rows of
> more; under the platform's row cap, the query itself was truncated. **A
> partial result yields no facts at all** — a total over a prefix is not an
> approximate total, it is a wrong one. It also means facts widen no disclosure:
> every value is computed from rows the model was already given in full.

**3. Three outcomes before a token is spent:**

| Condition | Status | What is written |
|---|---|---|
| every block returned 0 rows (`has_no_data`), **or** the section has no blocks at all | `SKIPPED_NO_DATA` | `no_data_sentence(language)` — a fixed sentence in `prompts.py`, in Persian or English. Written here rather than by a model: it costs no call, it cannot hallucinate the returns that were not there, and it is the one sentence in a report whose wording must never vary |
| nothing produced rows and something broke (`has_nothing_to_say`) | `FAILED` | the first block error. A paragraph written over three failures would be fiction |
| the run has no narrator (model config deleted mid-flight; `llm_config_id` is SET NULL) | `FAILED` | `_NO_MODEL` — *"choose a model for the report and generate again"* |

**4. One `complete` call.** `narrate.section_messages`:

- **`REPORT_SECTION_SYSTEM`** — deliberately *not* `ANSWER_SYSTEM`. That prompt
  is tuned for a two-sentence chat bubble leading with the number, and reusing
  it is exactly how a report ends up reading like a chat transcript: a heading,
  then "Revenue was $1.24M.", seven times. It asks for the four moves an analyst
  makes — **finding (quantified) → size and shape → account for it → the
  consequence** — plus a house style (4–7 sentences, one paragraph, third
  person, no markdown, no raw column names, at most one decimal, no hedging
  without a reason) so seven independently-written paragraphs read as one
  document. "Prefer the computed figures over arithmetic of your own" is the
  line `facts.py` exists to make true.
- **`REPORT_SECTION_USER`** — language (endonym), the user's own request, the
  heading, the `intent`, then `{neighbours}` and `{results}`.
- **`{neighbours}`** is two optional blocks, rendered only when non-empty so a
  one-section report sends no empty scaffolding: `REPORT_SECTION_NEIGHBOURS`
  (the other sections' headings — "which you must not duplicate") and
  `REPORT_SECTION_ESTABLISHED` (the findings already stated, capped at
  `MAX_ESTABLISHED = 5` sections × `MAX_ESTABLISHED_CHARS = 240`, each trimmed
  by `_gist` to its opening sentences). Without these, every section is written
  by a writer who has never seen the rest of the report — three paragraphs each
  opening on total revenue because that was the largest number each was handed.
  Bounded because this text grows with every section written.
- **`{results}`** is each block rendered by `BlockNarration.render()`: the
  question; then either *"This query could not be run: …"* (named rather than
  hidden — a paragraph written as if all four results existed quietly narrates
  three quarters of the picture), or *"No rows"*, or the headline KPI line, the
  column header, up to `MAX_PROMPT_ROWS = 50` rows with cells clipped at
  `MAX_CELL_CHARS = 120`, the disclosure note, and last the computed facts.
  Facts go last on purpose: the model reads the source table before the summary
  of it, and the instruction to prefer them sits closest to the sentence it
  writes.

The narrator's output budget is floored at `NARRATE_MIN_MAX_TOKENS = 4_096` —
the paragraph is a few hundred tokens, but the budget must also cover a
reasoning model's scratchpad, and the product default on `llm_configs.max_tokens`
is 2,048.

**5. Truncation is read from the provider, not guessed.** `_prose(completion)`
checks `Completion.truncated` (`finish_reason == "length"`) and cuts the text
back to its **last complete sentence** — recognising `. ! ?` *and* `؟ ۔ । …`,
because a Persian paragraph a Latin-only trim did not understand would be thrown
away whole. Logged as `report_prose_truncated` with kept/dropped char counts. If
there is no sentence boundary at all the budget is not merely tight but far too
small, and the empty string becomes `_TRUNCATED`: *"Raise max_tokens on this
report's model configuration — 4096 or more is a sound floor."* Actionable,
because nothing else in the run will fix it.

**6. The numeric check** (`_numeric_check` → `checks.check_prose`). Every
numeral in the paragraph is extracted and looked for in a pool of: every numeric
cell the model was **given** (not what the query returned — a figure matched
against a row the model never saw would be excused as a coincidence), each
block's `row_count` ("across 13 months" is the most natural sentence in the
world to write from a row count), and every computed fact. It reads Persian and
Arabic-Indic digits, `٫`/`٬` separators, and scale words in both languages
("۱٫۲ میلیون" and "1.2M" are the same claim). Tolerance `REL_TOLERANCE = 0.005`.

**It flags, it never blocks.** Same posture as `pipeline/checks.py`: a finding
is a suspicion, never a verdict. The result is stored on
`report_section_results.numeric_check` and marked in the UI.

**7. Commit, then carry forward.** The row is committed immediately (progressive
render), and its prose joins `established` for the sections after it.

#### C6 · The executive summary, last

`_summarise` runs after every other section, and is given **the finished prose
and no data of its own**. That is the design: a summary that could reach the
rows would be a second place for a figure to be invented; one that can only
quote the sections can be *checked* against them — which is exactly what happens
(`checks.figures_in(body)` over the sections' own text as the pool).

`REPORT_SUMMARY_SYSTEM` asks for exactly two parts: one paragraph of 2–4
sentences opening on **the most consequential finding wherever it sits** (not
the first section's), then a blank line, then 3–5 `- ` lines ordered by
importance, each carrying at least one figure. Every figure must already appear
in a section below. No structure talk ("this report examines…"), no
recommendations the data cannot support.

Two ordering details: **`written` prefers `edited_prose` over `prose`** — a
summary written over a draft the user has since rewritten would summarise a
document nobody is looking at. And its *position* is wherever the user put it,
usually first, which is the point of writing it last.

Failure of the summary is a failed **section**, never a failed run — and
`narrator is None` is checked *before* emptiness, so a missing model
configuration never produces a summary blaming the database for "no data".

#### C7 · Derive the status

```python
derive_status(outcomes)   # outcomes: one bool per block result + per section result
  all true  → SUCCEEDED
  any true  → PARTIAL
  none/empty→ FAILED
```

**The status is read off the parts, never set.** That is what lets a per-section
retry turn `PARTIAL` into `SUCCEEDED` with no state machine, and it is why
`PARTIAL` can exist at all: a run of seven sections where one failed is neither
a success nor a failure, and calling it either is a lie the user has to open the
document to catch.

### 4.4 What the poller sees

`_touch` commits after every phase change, so `GET /reports/{id}/runs/{rid}`
returns `status`, `phase` ("Running 7 queries" → "Wrote result 3 of 7" →
"Writing Revenue trend" → "Writing the summary"), `progress_current` /
`progress_total`, plus **every block and section row written so far**. The
half-finished document *is* the response; there is no separate progress channel.

### 4.5 Every failure in flow C, and what it costs

| Failure | Detected in | Costs | Run status |
|---|---|---|---|
| report or connection deleted | C1 | the run | `FAILED` |
| policy narrowed to `NONE`/`AGGREGATE` since creation | C1 (`assert_wide_enough`) | the run | `FAILED` (message names the policy and both ways out) |
| block has no SQL | C3 | that figure | block `FAILED`, `E_SQL_MISSING` |
| guard rejects a stored statement | C3 (`execute_saved_sql`) | that figure | `E_SCHEMA_CHANGED` when the rule is `E_TABLE_NOT_ALLOWED`/`E_UNKNOWN_COLUMN` — *"re-sync the connection"* — else the rule id verbatim |
| connection has no snapshot | C3 | every figure on it | `E_NO_SNAPSHOT`, without dialling the database |
| database error / timeout | C3 | that figure | `E_QUERY_FAILED` |
| chart cannot be built | C3 | the picture only | result is `OK` with `vega_spec = None` and a note |
| every block empty | C5 | the paragraph | section `SKIPPED_NO_DATA`, fixed sentence |
| every block failed | C5 | the paragraph | section `FAILED`, first error |
| model config deleted mid-run | C5/C6 | every paragraph | sections `FAILED` with `_NO_MODEL`; **the numbers still land** |
| provider error on one section | C5 | that paragraph | section `FAILED`; the other six stand → run `PARTIAL` |
| reply hit `max_tokens` | C5/C6 | the tail of a paragraph | trimmed to the last sentence; `FAILED` + `_TRUNCATED` only if no sentence completed |
| figures in the prose match nothing | C5/C6 | nothing | recorded in `numeric_check`, marked in the UI |
| user cancels | between phases | nothing already computed | `CANCELLED` |
| process dies | `sweep_orphans` at startup | nothing already committed | `FAILED`, "generate again for the rest" |
| any uncaught exception | `generate_run` | the run | `FAILED` with `str(err)[:500]`, logged `report_run_failed` |

---

## 5. Flow D — retry one section

`request_section_retry` refuses first (`ConflictError` if the run is still
`QUEUED`/`RUNNING`; a section not on this report is 404; a removed or narrowed
connection is refused), sets the run back to `RUNNING` with
`phase = "Retrying {heading}"`, and hands off. The viewer's existing poll
renders the section rebuilding itself — no new endpoint, no new protocol.

`retry_section` → `_retry`
([workers/report.py:464-571](../backend/app/workers/report.py#L464-L571)):

1. `_clear_section` deletes that section's block-result and section-result rows
   and **returns the position the old rows sat at**, so the replacements reuse
   it and a retried section stays where the reader left it instead of jumping to
   the end of the document.
2. Re-executes only that section's blocks, through the same `execute_many`.
3. Re-narrates, with two differences from the first pass:
   - `established` is `_written_sections(...)` — the paragraphs that exist
     **now**, including ones written *after* this section, which the first pass
     could not see;
   - the **executive summary is excluded** from that list. Handing it over would
     be circular: it already contains this section's own finding, so the section
     would dutifully avoid restating it and write around the thing it exists to
     say.
4. **The summary is not rewritten.** It is a paragraph the user may have edited,
   and silently replacing it because a section below was retried would destroy
   writing. It can be retried on its own — and when it is, `_summarise` runs with
   `_written_sections(db, run.id, section_id)` as its input.
5. `_rederive` reads the run's status off every row it now holds. A section that
   deleted between the request and the worker picking it up also lands here,
   rather than leaving the run `RUNNING`.

---

## 6. Where a number can come from, in one list

Four sources, in descending trust — worth knowing because "the report is wrong"
almost always means one of the last two:

1. **The database**, via a guarded statement, snapshotted onto
   `report_block_results.rows`. The table under a figure is this.
2. **`plan_kpi`** — the headline number, computed from those rows in Python.
   The same planner a chat turn uses, so a tile and a report showing "total
   revenue" agree on which column it is and how it is written.
3. **`facts.py`** — totals, deltas, growth rates, shares, ranks, Pareto counts,
   spread. Computed exactly, from complete results only, capped at `MAX_FACTS`
   (14) per block and `MAX_ROWS` (20,000) scanned. Non-additive columns (rates,
   averages, unit prices) are never summed — matched on the column *name*,
   because the database type says `numeric` for all of them.
4. **The model's sentence** — everything else. Checked by `checks.py`, never
   blocked by it.

**No model is asked to do arithmetic anywhere in this pipeline.** See
[reports.md §8](reports.md) for the tiering argument in full.

---

## 7. Prompt versioning

`REPORT_PROMPT_VERSION` (currently **r4**) lives in
[`app/reports/prompts.py`](../backend/app/reports/prompts.py) and is recorded on
every `report_runs` row. It is separate from the pipeline's `PROMPT_VERSION` for
a structural reason, not a filing one: `app.reports` sits *below* the pipeline —
a report reads a pipeline node, and a node knows nothing about a report.

| version | what changed |
|---|---|
| r4 | every block is asked for a `title` as well as a `question`. A model that ignores the field costs nothing — an empty title falls back to the question, so r3 and r4 documents differ in caption wording only |
| r3 | the section count stopped being the prompt's opinion and became the user's (`reports.section_target`) — which also changes what the five themes mean: with fewer sections than themes the model must *choose* between them |
| r2 | the analyst rewrite: the four moves, the house style, the neighbours/established blocks, and computed figures instead of arithmetic over a text table |
| r1 | "two to four sentences per section" — correct, grounded, and reading like a chat transcript with headings on top |

**`PROMPT_VERSION` (the pipeline's) does not move for report work**, because
`extra_rules` is empty for every non-report caller and empty is byte-identical.
`REPORT_PROMPT_VERSION` does not move when the *outline* schema block changes
either — but it should, and does, when any of the three prompt texts change,
because a document generated before a wording change must never be silently
compared with one generated after it.

---

## 8. Sharp edges, verified in code (2026-08-12)

1. **The draft path's deadline bounds the repair, not the whole call.**
   `deadline_at = now + DRAFT_DEADLINE_SECONDS` (120s) is checked by
   `_check_deadline` **before each `generate` attempt** — the draft path calls
   the nodes directly, so `AnalyticsPipeline`'s own check never applies to it.
   What that buys is the case that actually hurts: one `structured` call can
   take minutes once the gateway's transient-failure retries and their backoff
   are counted, and a repair is no longer started on top of a first attempt
   that already spent the budget. It raises `LLMError`, so `check_block` stores
   it as the block's reason like any other "the model could not produce a
   query". A call already in flight is **not** interrupted — that bound is
   `llm_request_timeout_seconds`, and the preview after it is bounded by the
   connection's `statement_timeout_ms`.

2. **The outline prompt has no retrieval budget.** Flow A renders the *whole*
   snapshot with no `_RETRIEVE_BUDGET_CHARS` ceiling and no selection, so a
   schema well past 50k chars produces an outline prompt no other path would
   send. `OUTLINE_MIN_MAX_TOKENS` protects the *reply*, not the request; a
   context-length error surfaces as `LLMError` and the user sees "the model did
   not return an outline that could be read", which is a true statement about a
   cause it does not name.

3. **`previous_block_hashes` compares against the last run that computed
   something**, not the last run. A cancelled run that wrote no rows is not a
   previous version of the report — comparing against it would report every
   figure as unchanged, which is worse than saying nothing because it is an
   answer.

4. **A retry can outrun the document around it.** Retried prose reads the
   paragraphs that exist now, so retrying two sections in sequence gives the
   second one a view of the first that the first did not have of it. Intended,
   but it means two retries are not commutative.

5. **The report call sites are now in [security.md §2](security.md)** — fixed
   2026-08-12, along with a §2.3 covering what each of the three sends. They had
   been missing since the feature landed, which made the section's own claim
   (*"this list cannot silently grow"*) false for as long as nobody re-read it.
   Three prompts and one table: when you add an LLM call site to
   `app/reports/`, security.md §2 and [pipeline.md §0.4](pipeline.md) are the
   two lists that have to grow with it.
