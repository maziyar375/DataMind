# The deep analysis pipeline, node by node

What happens between a reader choosing **Deep** and a short, cited answer
appearing. It is the fourth pipeline in the product, and the one built most
recently. [pipeline-chat.md §0](pipeline-chat.md#0-the-four-pipelines-and-what-they-share)
maps all four. The plan behind it is
[deep-analysis-mode.md](../plans/deep-analysis-mode.md), and the argument for
building it is [research/deep-analysis-mode.md](../research/deep-analysis-mode.md).

> **It is built, and it is off.** `settings.deep_enabled` defaults to `False`.
> The gate the plan wrote before any code existed (§0.3: single-shot accuracy
> ≥ 0.55) was measured on 2026-09-22 at **0.42**, and it has not moved since.
> Phases 4–9 were built anyway, on the project owner's instruction, and the plan's
> status banner records that. **No deep number may be quoted as evidence that
> the mode works until the gate opens.** The first real run on Flash (§8)
> shows why: it answered from partial evidence, and it named a driver the
> fixture does not have.

Code: [`backend/app/pipeline/`](../../backend/app/pipeline):
- `graph.py`: `DEEP_GRAPH`, `DeepPipeline`, the four routers, `deep_recursion_limit`.
- `nodes/deep.py`: `plan`, `step`, `compute`, `synthesize`.
- `evidence.py`: a step's rows turned into figures, and into what a writer may see.
- `prompts/deep.py`: `DEEP_PROMPT_VERSION`.
- `state.py`: `DeepState`, `AnalysisPlan`, `PlanStep`, `StepEvidence`.
- `signals.py`: *Answer now*, as the running graph sees it.

The governance half lives in
[`services/deep_budget.py`](../../backend/app/services/deep_budget.py).

---

## 1. What a deep run is

A reader asks *"why did revenue drop in March?"* and chooses **Deep — a few
minutes** in the composer. The system:

1. states what it understood;
2. plans up to five sub-questions;
3. answers each one with one ordinary, guarded query;
4. computes the arithmetic from the rows with `app/analysis/`, never with a model;
5. writes a short answer in which every number cites the step it came from.

The reader watches the plan fill in and can press **Answer now** at any point.
The answer is then written from what has been found, and it opens by saying
how much of the plan it stands on.

**A deep run is a run.** It is a row in `runs` with `depth = 'DEEP'`, and like
every other run it is claimed, heartbeated, cancelled, reconciled and streamed.
It uses the same executor, the same SSE bus and the same `run_steps` table.
No new stream, poll or worker was added. What differs is the graph, the
deadline, and a budget that fails closed.

| | Chat | **Deep** |
|---|---|---|
| Entry | `POST /conversations/{id}/messages` | the same, with `depth: "DEEP"` |
| Orchestrator | `CHAT_GRAPH` via `AnalyticsPipeline` | `DEEP_GRAPH` via `DeepPipeline` |
| Shape | a straight line with one repair loop | a **cycle**: one pass per step, under a budget |
| Wall clock | 5–60 s | minutes; hard deadline `deep_deadline_seconds` (600) |
| Model calls | about 4 | `route` and the plan once; per step, `scope` (on a sectioned connection), `generate` and its repairs, and sometimes a revision; then the answer and the chart once |
| Statements | 1 (+ repairs) | up to `max_queries` (12), repairs included |
| Arithmetic | the model narrates rows | `app/analysis/` computes; the model narrates the computed figures |
| Result → model | `present`, per policy | per step through `disclose()`, then `synthesize`, both per policy |
| Record | `runs`, `run_steps`, artifacts | the same, plus an `ANALYSIS` artifact and every sub-query in `generated_queries` |

---

## 2. Who may start one, and how far it may go

Everything here is checked **before anything is written**. A refused deep
question leaves no message in the thread and no run.

| In order | Refusal | HTTP | Code |
|---|---|:--:|---|
| `deep_enabled` is off | *"Deep analysis is not enabled on this installation."* | 422 | `E_VALIDATION` |
| The conversation, connection or model is out of reach | the ordinary 404/403 rule (`services/policy.require`) | 404/403 | `E_NOT_FOUND` / `E_FORBIDDEN` |
| The asker lacks **`deep.run`** | names the capability and who can grant it | 403 | `E_DEEP_REFUSED`, `reason: "capability"` |
| The connection's stored budget cannot be read | says it is refused until saved again | 403 | `E_DEEP_REFUSED`, `reason: "unreadable"` |
| A bound in the connection's budget is **0** | names the bound: *"allows no steps"* | 403 | `E_DEEP_REFUSED`, `reason: "max_steps"` (etc.) |

**`deep.run`** is the twentieth capability. Migration `0042` seeds it to
**Administrator alone**, because a deep answer costs roughly fifteen chat
answers' worth of tokens and takes minutes. An installation that turns the mode
on decides who gets it by putting the word on a role of its own. A retry asks
for it again, from whoever pressed Retry.

**The budget** is five numbers set per connection, under `manage`, at
`GET`/`PUT /connections/{id}/deep-budget`. It is shown on the connection's
**Policy** tab.

| Bound | Ceiling (the installation's) | Checked |
|---|---|---|
| `max_steps` | 5 | before every step |
| `max_queries` | 12, repairs included | before every step and every repair |
| `max_rows_total` | 20,000 | before every step; each query's row cap is narrowed to what is left |
| `max_prompt_tokens` | 400,000 | before every step and every repair; can be overrun by the one call in flight |
| `deadline_seconds` | `deep_deadline_seconds` (600) | the **hard** deadline; the soft one is a fifth earlier (§5) |

A connection's budget **narrows the ceiling and never widens it**. A number
above the ceiling is a 422 naming the bound, not a value quietly clipped. A
ceiling lowered later clips every stored budget at once. A **zero refuses**: it
never produces a smaller run. A deadline between 1 and 59 seconds is a 422,
because it could not finish a single step.

**The budget is snapshotted onto the run** (`runs.deep_budget`) when the
question is asked, and the executor reads that and nothing else. A run
claimed by another replica, taken over after a lapsed heartbeat, or executed
after an operator narrowed the connection spends what it was started under.
A DEEP row whose snapshot is missing, damaged or zero is failed with
`E_DEEP_BUDGET` before the connector opens. It is never run on the defaults.

Both refusals are audit rows (`deep.refused`, outcome `DENIED`), and so is every
change (`deep.budget.changed`). The two routes that start a run **return** the
refusal rather than raise it: raising rolls the request's transaction back,
and the audit row with it. [security.md §6](security.md#6-credentials-and-identity)
has the full argument.

---

## 3. The graph

```
route ─┬─ HALT (small talk, unsupported) ─────────────────────────────────▶ END
       └▶ plan ──▶ ┌─────────────────────────── one pass per step ───────────────────────────┐
                   │ step ─▶ scope ─▶ retrieve ─▶ generate ⇄ validate ─▶ execute ─▶ inspect │
                   │   │                            ▲    └── repair ──┘      │          │     │
                   │   │                            └──────── repair ────────┴──────────┘     │
                   │   └─ unknown tool: SKIPPED, no query                                     │
                   │                                                             ─▶ compute ──┘
                   └──── budget spent · plan complete · Answer now ────▶ synthesize ─▶ chart ─▶ END
```

**Reused, not re-implemented.** `route`, `scope`, `retrieve`, `generate`,
`validate`, `execute`, `inspect` and `chart` are the chat graph's own node
functions, wired by the same `_adapt`. `generate ⇄ validate` is
`_add_repair_region` again: this is its third caller, after chat and draft. So
a sub-query is an ordinary statement, through the ordinary guard, with no new
door. **What the deep graph adds is four nodes and four routers.** The nodes
never decide whether the budget allows another step. The routers do, on the
edges, so a node cannot forget to check.

Not in the deep graph, deliberately:
- **`match`**: a stored answer is one statement, and a deep run is a plan.
- **`describe`**: a schema question is not an analysis. METADATA is planned like anything else.
- **`clarify`**: the plan states what it understood, and the reader sees that first.
- **`present`**: the prose is `synthesize`'s.

### Node summary

| Node | Model call | Writes | Fails as |
|---|---|---|---|
| `route` | `complete` (chat's #1) | `intent` | HALT on small talk; fails open to analytical |
| `plan` | `structured(AnalysisPlan)` | the plan; `PLAN_PROPOSED` | **the run** (`E_PLAN`): there is nothing to answer from |
| `step` | `structured(StepRevision)`, sometimes | the step's question; `PLAN_REVISED` | revision fails **open**: the step runs as written |
| `scope`…`inspect` | chat's own | one sub-query | a failed sub-question closes its step as FAILED **evidence**, not a failed run |
| `compute` | **none** | `StepEvidence`; `STEP_EVIDENCE`, `BUDGET_SPENT` | cannot fail a run: an analysis refusal is a value |
| `synthesize` | `stream`, under `SAMPLE`/`FULL` | the answer, its claims | **backwards**: a writer failure becomes a product-written answer |
| `chart` | chat's #6 | the chart over the last step that ran | fail open, as in chat |

---

## 4. Each node in detail

### `route`: halt the cheap cases

The chat node, unchanged. CHITCHAT and UNSUPPORTED halt with the canned reply,
as in chat. Anything with data in it goes to `plan`, METADATA included.

### `plan`: one structured call, and the only one that can fail the run

`DEEP_PLAN_SYSTEM` asks for three things:
- a **restatement**: what was understood. The reader sees it first, so a misreading is caught before any query runs.
- up to `budget.max_steps` **steps**, each with `question`, `intent`, `why`, `tool` and `depends_on`.
- a **`stop_when`** condition.

The planner writes no SQL and does no arithmetic.

- **The schema it plans against is `generate`'s own block**, rendered under the
  same disclosure policy. When the snapshot is over the retrieve budget, it is
  narrowed the way `describe` narrows one: by what the question names, then by
  size. A plan is therefore never written against a column the generator will
  not be shown.
- **`intent`** is one of `CONFIRM`, `DECOMPOSE`, `COMPARE`, `DRILL`, `CHECK`.
  **`tool`** is one of the closed set `SQL`, `COMPARE_PERIODS`, `CONTRIBUTION`,
  `OUTLIERS` (plan D4: no sandbox, no DuckDB).
- **`_tidy` truncates and never honours.** A plan longer than the ceiling
  keeps its first `max_steps` steps, since order is the planner's statement of
  what must come first. Blank steps are dropped, and so is any `depends_on`
  that points forward, at itself, or past the end.
- **An empty plan, or a provider error, fails the run with `E_PLAN`.** There
  is no evidence yet, and an answer written without a plan would be a chat
  answer in a deep run's clothes.

### `step`: point the chat road at the next sub-question

It is entered only through a router that has already read the budget. It does
three things:

1. **Revise, sometimes.** If the step names dependencies, *every* one of them
   answered, and the policy is `SAMPLE`/`FULL`, one `structured(StepRevision)`
   call may **replace** the step with a sharper question (for example, naming
   the region step 2 singled out). It can never add one, so it can never carry
   the plan past its ceiling. It reads the dependencies' `narration`, the same
   disclosed view the writer gets (see `compute`). A provider error keeps the
   step as written. A replacement emits `PLAN_REVISED`, and the panel shows it
   **as a revision**, with the old wording struck through.
2. **Refuse an unknown tool without spending a query.** A step whose tool is
   outside the closed set is closed as SKIPPED evidence and the router asks for
   the next one.
3. **Set the question the generator sees.** That is the sub-question, plus
   `SHAPE_HINTS[tool]`: the row shape `compute` needs, such as "one row per
   period and segment". A plain `SQL` step is asked exactly its own question.

`begin_step` also clears everything a chat run starts without, so nothing
from step two reaches step three's prompt except through the disclosed
evidence. It narrows the row cap to `min(connection max_rows, rows left in
the budget)`.

### `scope` → `retrieve` → `generate ⇄ validate` → `execute` → `inspect`

These are the chat nodes, and they behave exactly as
[pipeline-chat.md §3](pipeline-chat.md#3-each-node-in-detail) says. The
differences are all in the routers:

- `END` and the chat restore (`present`) both route to **`compute`**. A
  give-up closes the *step*, not the run.
- Every repair back into `generate` (from `validate`, `execute` or `inspect`)
  goes through `_deep_may_repair`. **A repair is a query**, so it spends
  `max_queries`, and it is refused once tokens or time are spent or *Answer
  now* has been pressed. The step then closes with what it has.
- `attempts` stays **the run-global list**, so every sub-query lands in
  `generated_queries` with a unique `attempt_no`, and the existing constraint
  holds (plan §2.4). `run.repair_count` is the **total** of per-step repairs
  (`total_repairs`), not `len(attempts) - 1`, which on a deep run would count
  steps as repairs.

### `compute`: close the step

It is reached from `inspect` on success and from anywhere in the step's road on
failure. **One failed sub-question is evidence, not a failed analysis:** the
error moves onto the step and is cleared from the run.

- **`disclose()` runs here, per step**, under the run's policy, before anything
  downstream can read the result.
- **The tool is dispatched into `app/analysis/`** (`evidence.compute`):
  - `COMPARE_PERIODS`: two periods compared in total and **per calendar day**,
    because the commonest false driver is a shorter month.
  - `CONTRIBUTION`: SpotIQ's three algorithms, chosen by measure class.
  - `OUTLIERS`: z-scores at a cardinality-chosen threshold.

  A refusal such as `NO_CHANGE` or a wrong shape is a **value**. It is shown
  and cited, never raised.
- **Two views of one step.** `computed` is for the *reader*, whatever the
  policy, because it is their own data on their own screen. `narration(e)` is
  what a *prompt* may read: the disclosed rows, and the computed figures
  **only when every row behind them was disclosed** (`disclosed_in_full`). A
  total over the first fifty rows of a `SAMPLE` is a wrong total. A driver
  computed from withheld rows would carry their values out.
- Emits `STEP_EVIDENCE` (durable) and `BUDGET_SPENT` (transient, like
  `RESULT_PREVIEW`), then moves the cursor.

### `synthesize`: the one terminal node every road reaches

A finished plan, a spent budget and *Answer now* all end here.

1. **The preface, written by the product and never by the model.** When the run
   stopped early, the answer opens with *"This answer is built from 3 of 5
   planned steps: the analysis stopped early because you asked for an answer
   now."* It is the first thing streamed.
2. **Under `NONE`/`AGGREGATE`, or with no step answered, no model is called.**
   `_plain` writes the answer: each step's question and what it found. Under a
   wide policy it omits any computed figure the model could not have seen
   every row of, because the answer is stored as the assistant message and
   `disclose_history` replays it to the next turn verbatim.
   `test_deep_disclosure.py` found that leak and pins the fix.
3. **Otherwise it uses `reports/narrate.py`'s section prompt**, with the steps as
   the section's numbered results. Every sentence stating a figure cites its
   step. `parse_claims` lifts the markers into `Claim`s and `check_claims`
   checks each one against **its own** step (Phase 3's machinery, reused). The
   live stream carried the markers, so they are replaced with the clean prose
   (`TEXT_RESET` + `TEXT_DELTA`).
4. **Fail backwards.** If the writer errors or returns nothing, `TEXT_RESET` and
   the `_plain` answer replace it. The evidence is correct, and a failed writer
   may not lose it.

It also restores the reader's own question and puts the last successful
step's result into `execution`, so `chart` and the TABLE artifact describe a
real result.

---

## 5. Control flow rules

**The budget is read on the edges, into `step` and back into `generate`.**
`DeepState.exhausted(now)` returns the first spent bound, in the order a reader
would want to be told: `steps`, `queries`, `rows`, `tokens`, `time`.
`may_repair(now)` asks the three bounds a repair spends.

**Running out is a normal ending.** It routes to `synthesize`, the same road as
*Answer now*, and records `stop_reason`. *Answer now* is checked **first**, so
a reader who pressed it at the moment a bound ran out is told the decision was
theirs.

**A finished plan is checked before the budget.** Five steps planned under a
five-step ceiling is a plan that completed, and it must not be reported as cut.

**Two deadlines.**
- The **soft** one (`budget.deadline_at`, 80% of the snapshot's
  `deadline_seconds`) is read on the edges and ends in an answer.
- The **hard** one (`state.deadline_at`) is `_run_deadline`, checked before
  every node exactly as in chat, and ends in `E_TIMEOUT`.

The gap between them is the time `synthesize` has to write.

**The recursion limit is counted, not guessed.** `deep_recursion_limit` is
`2 + 4·max_steps + 4·max_queries + 2 + 1`, which is every node the budget
allows plus one. A limit below the budget's own ceiling would turn a
legitimately long analysis into `E_PIPELINE_LOOP`.

### *Answer now*

`POST /runs/{id}/answer-now` returns 202. It needs `modify` on the
conversation, like cancel. It is **cooperative, and it is not cancel**:
- Cancel throws the run away.
- *Answer now* lets the step in flight finish, starts no further step and no
  further repair, and has `synthesize` write from the evidence.

The ask is durable (`runs.answer_now_requested`) because the replica holding
the loop is not necessarily the one the click reached. It arrives by cancel's
two roads ([cross-replica.md](cross-replica.md)): the route sets
`signals.request_answer_now` directly when the run is local, and the owning
process's heartbeat sets it otherwise, from the same `UPDATE … RETURNING` that
reads `cancel_requested`. A 409 comes back on a quick run or a finished one.

---

## 6. What it writes, and what a reader sees

| Event | Durable? | From | Carries |
|---|:--:|---|---|
| `PLAN_PROPOSED` | ✅ | `plan` | restatement, steps, stop condition, `max_steps` |
| `PLAN_REVISED` | ✅ | `step` | the index, the step replaced, its replacement |
| `STEP_EVIDENCE` | ✅ | `step` (skipped), `compute` | status, row count, the statement, the computed summary |
| `BUDGET_SPENT` | ❌ transient | `compute` | steps / queries / rows / prompt tokens against each bound |
| `TEXT_DELTA`, `TEXT_RESET`, `THINKING_*`, step events | as in chat | as in chat | as in chat |

`run_steps` holds one row per node execution, so `generate` appears once per
statement and `step`/`compute` once per step. `_adapt` writes them exactly as
it does for chat, and `(run_id, seq)` has always allowed a name to repeat.

**The `ANALYSIS` artifact** is written by `_finalise` once the run has ended. It
holds the plan, the revisions, every step with its statement and computed
summary, the stop reason, the claims and their traceability, the budget spent,
and `DEEP_PROMPT_VERSION`. It is an artifact rather than a column so that it is
withheld, with the others, from a reader who may not see the run's data.

**`GET /runs/{id}/plan`** serves a reader who arrives late. Once the run has
ended it returns the artifact. While the run is in flight it folds the durable
events (`services/deep_plan.fold`, the same fold the SPA runs in
`deep-plan.ts`). A reader with the thread but not the connection gets the
restatement and the questions, which are the transcript, and **nothing any
step found**.

**The surface.**
- The composer offers *Quick* / *Deep — a few minutes* only where `/auth/me`
  names the `deep` feature **and** the reader holds `deep.run`. It resets to
  Quick after every send.
- The plan panel shows the restatement, then the steps with the running one
  marked, revisions struck through, and steps never reached as *Not run*.
- *Answer now* is the panel's primary control, deliberately not placed beside
  the composer's stop.
- Each footnote in the answer opens the step behind it.

---

## 7. Every way a deep run ends

| Ending | Status | Code | The reader gets |
|---|---|---|---|
| Plan completed | `SUCCEEDED` | — | the answer, no preface |
| A bound spent, or *Answer now* | `SUCCEEDED` | — | the answer, prefaced with *built from N of M steps … because …* |
| A step failed | `SUCCEEDED` (the run goes on) | — | that step's line reads *not answered*; later steps still run |
| Writer failed | `SUCCEEDED` | — | the product-written answer |
| Refused before it started | no run | `E_DEEP_REFUSED` / 422 | the refusal sentence; nothing in the thread (§2) |
| No usable budget snapshot | `FAILED` | `E_DEEP_BUDGET` | *"This analysis has no budget it can be held to, so it was not started."* |
| Access revoked, or source deleted, since it was queued | `FAILED` | `E_FORBIDDEN` / `E_NOT_FOUND` | as in chat |
| Could not be planned | `FAILED` | `E_PLAN` | *"The analysis could not be planned."* |
| Hard deadline | `TIMED_OUT` | `E_TIMEOUT` | as in chat |
| Graph did not converge | `FAILED` | `E_PIPELINE_LOOP` | *"The analysis did not converge and was stopped."* |
| A node raised | `FAILED` | `E_NODE_FAILED` | as in chat |
| Cancelled | `CANCELLED` | — | the evidence already paid for is still written |

**The interruption rate**, meaning how many deep runs readers stopped early, is
`services.deep_plan.interruption()`, and its SQL is in
[eval.md](eval.md#the-deep-arm--a-different-suite-a-different-pipeline-a-different-scorecard).
Failures are the product's, not the reader's, and are kept out of its
denominator.

---

## 8. Prompt versioning, and what the one real run showed

`DEEP_PROMPT_VERSION = "d1"` versions the planner and the reviser. The
sub-queries are written by the chat generator, so they move with
`PROMPT_VERSION`. One number for both would make neither readable. The
`ANALYSIS` artifact records `d1`, and an eval run is filed as
`"<PROMPT_VERSION>+<DEEP_PROMPT_VERSION>"`, for example `v12+d1`. The writer is
`reports/narrate.py`'s section prompt, so a change there moves
`REPORT_PROMPT_VERSION`.

`--suite deep_v1 --mode deep` scores five numbers without a judge: guard pass
rate, execution success, claim traceability, plan adherence, and cost per
answer ([eval.md](eval.md)). **The first real run, one question on DeepSeek V4
Flash** (`bc3bd6d3`, 2026-09-24), showed two things.

1. **The latency contract does not hold on that model.** The plan and the
   first `generate` took 78% of 547 s between them, as a reasoning model
   thinking at length. The soft deadline cut the run at step 3 of 5.
2. **It named a driver the fixture does not have.** It attributed a fall to
   channels when the known answer is a three-day-shorter month. That is
   arithmetic true of the rows and an explanation false of the data, and it is
   precisely what `deep_v1` was frozen to catch.

This is a Flash number. It may not share a sentence with the Pro baselines.

---

## 9. What it deliberately does not have

Each item has a written trigger in [the plan §10](../plans/deep-analysis-mode.md#10-deliberately-not-building).

- **No sandbox and no DuckDB.** Computation is the closed function set in
  `app/analysis/`. A sandbox would be a new execution surface, and every new
  door replays the hostile corpus.
- **No parallel sub-agents.** "Why did X drop" decomposes into causally
  ordered steps.
- **No auto-escalation.** Nothing turns a five-second answer into a
  four-minute one without the reader choosing it (D1). At most, a finished
  shallow answer may one day *suggest* a deep run, and not before the
  interruption rate is known.
- **No per-row model calls, and no files.**
- **No model on any refresh path.** A dashboard or a schedule can never
  trigger a deep run.

---

## 10. Known gaps (2026-09-24)

1. **The gate has not opened** (0.42 against 0.55). This is the gap everything
   else sits under.
2. **On a reasoning model the mode answers from partial evidence by
   construction.** At the shipped 600 s, a five-step plan cannot finish on Flash
   (§8). The plan's own risk table says the answer may be a shorter plan, not a
   longer deadline.
3. **Nothing has been refused yet.** The number Phase 8 was to produce,
   refusals per budget setting, is a query over `audit_logs`
   (`action = 'deep.refused'`, grouped by `detail->>'reason'`), and it is zero
   because no deep run can start while the flag is off.
4. **[llm-calls.md](llm-calls.md) does not yet carry the three deep calls.**
   It writes out every other prompt verbatim with what fills each placeholder.
   The deep calls are listed in [security.md §2](security.md#2-every-place-data-leaves-for-a-model-provider)
   and [pipeline-chat.md §0.4](pipeline-chat.md#04-every-place-a-model-is-called-in-the-whole-product),
   and their prompts are in `pipeline/prompts/deep.py`. The verbatim write-up
   is owed.
