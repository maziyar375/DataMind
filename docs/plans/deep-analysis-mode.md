# Deep analysis in chat — build plan

> **Status: the gate was run on 2026-09-22 and it did not open — and Phases 4–7
> were started on 2026-09-23 anyway, on the project owner's direct instruction.**
> §0.3's number is still **0.42**, the middle band; nothing has moved it. The
> override is recorded here rather than the gate being re-read as open: the mode
> stays behind `deep_enabled = False`, and no deep number may be quoted as
> evidence the mode *works* until §0.3's threshold is met. Phases 4–7 are
> complete (**73 of 85**); 8 (governance) and 9 (documentation) are open. Written 2026-09-22
> against `main`. The argument for *why* lives in
> [research/deep-analysis-mode.md](../research/deep-analysis-mode.md) — read it
> first; this document does not re-argue it. The feature is
> [mvp2.md](mvp2.md) **F3** (bounded multi-step analysis) and **F4** (root-cause
> / key drivers), currently Tier 3 and deferred in
> [status.md §5](../status.md#5-deferred-on-purpose-with-triggers).
>
> **Phase 0 is a gate, not a warm-up.** Its trigger is F3's own written one —
> *"Tier 1 accuracy is credible and users start asking why"*. When this was
> written the only number the repo had was **0.36 at `PROMPT_VERSION` v2, July
> 2026** — stale, not necessarily wrong, and never re-measured. **It has now
> been re-measured: 0.42 at v10 with the semantic layer on**
> (`5df63738-43a9-4b45-8e6d-b0b68e7ba9b6`, 2026-09-22), which lands in §0.3's
> middle band and closes Phases 4–9 until A1/A5/B2 move it. **A deep mode over a generator that is wrong two times in
> three does not produce a better answer; it produces a longer, more confident,
> more expensive wrong answer with eight queries' worth of surface area for the
> error to hide in.** §3.1 says what measurement opens the gate and what to
> build instead if it does not.
>
> **Phases 1–3 are useful whether or not the mode ships.** That is deliberate:
> they are the three things [research §6.2](../research/deep-analysis-mode.md)
> identifies as standing on their own — cache-token accounting, the contribution
> module, and claim→SQL citations — and each improves something that exists
> today. If Phase 0 closes the gate, Phases 1–3 still get built.
>
> **[§12](#12-progress-ledger--what-is-done-what-is-not) is the ledger.** One
> checkbox per deliverable per phase, each with the check that proves its
> state. **Tick a box in the commit that lands the work**, never ahead of it and
> never in a batch afterwards — for the same reason the eval's charter says
> *"an eval you are allowed to edit measures your willingness to edit it."*

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

*A reader asks "why did revenue drop in March?", chooses **Deep** in the
composer, watches a declared plan being worked through query by query, can stop
it at any point and get an answer from what it has, and receives a short report
whose every number resolves to the SQL that produced it.*

### 0.2 The six decisions, taken before writing this

Recorded rather than re-argued. Everything below assumes them; each is from
[research §2](../research/deep-analysis-mode.md).

| # | Decision | Because |
|:--:|---|---|
| D1 | **An explicit mode the reader chooses**, never a silent escalation | Nobody in the field silently turns a 5-second answer into a 4-minute one. Chat's 5–60s answer is a feature that cannot regress |
| D2 | **A declared plan that may be revised, under a hard step ceiling** | Plan-and-execute is budgetable and inspectable; ReAct adapts but cannot be bounded. The field converged on the hybrid |
| D3 | **A second compiled graph**, not a longer chat graph | The chat graph's twelve-node `ORDER` is a straight line whose shape is load-bearing for the SSE contract. The deep loop is a cycle with a budget. **Nodes are reused; the graph is not** |
| D4 | **A closed set of pure functions for computation** — no DuckDB, no Python sandbox, in version one | A sandbox is a *new execution surface*, and [mvp2 Part 5 §1](mvp2.md) makes every new door replay the hostile corpus. A pure module over already-executed rows is not a new door at all |
| D5 | **The unit of work is a sub-question carrying an intent**, not a query and not a hypothesis | Keeps the plan readable by a human, keeps every query on the existing guarded road, and makes the intent the routing key for D4's functions |
| D6 | **It streams; it does not poll** | Reports are polled because they are minutes long with nothing to watch. A deep run is minutes long with the most interesting thing in the product to watch |

### 0.3 The gate, stated as a rule before it can be argued with

Phase 0 produces one number: **single-shot execution accuracy at v10 on
`sales_v1`, with the semantic layer on.**

- **≥ 0.55** — the gate opens. Build Phases 1–9 in order.
- **0.36 – 0.55** — the gate opens for Phases 1–3 only. Phases 4–9 wait on
  [mvp2](mvp2.md) A1/A5/B2 moving the number, and this plan is re-read, not
  re-argued.
- **≤ 0.36** — the gate stays shut. Build A1/A5/B2. Phases 1–3 still ship,
  because they stand alone.

**The threshold is written down now, before the number exists,** which is the
only time it can be written honestly. Changing it later requires the same
evidence standard `suites/CHANGELOG.md` demands of a `gold_sql` edit.

### 0.4 The phases

| # | Phase | Size | Stands alone? | Gated on |
|:--:|---|:--:|:--:|---|
| 0 | **The gate** — one measurement, and a frozen deep set | S | — | nothing. Blocking |
| 1 | **Cache tokens** — widen `Usage` | S | ✅ yes | nothing |
| 2 | **`app/analysis/`** — the arithmetic, pure and token-free | M | ✅ yes | nothing |
| 3 | **Citations as structure** — claim → step → SQL | M | ✅ yes | nothing |
| 4 | **The plan, and a graph that stops** | L | ❌ | 0, 2 |
| 5 | **The loop** — steps, evidence, compute | L | ❌ | 4 |
| 6 | **The surface** — toggle, trail, *Answer now* | M | ❌ | 5, 3 |
| 7 | **Scoring it** | M | ❌ | 6, 0 |
| 8 | **Governance** — the budget an operator sets | S | ❌ | 6 |
| 9 | **Documentation** | S | ❌ | 8 |

Phases 1, 2 and 3 are independent of each other and of Phase 0's result. They
can be built in any order, or in parallel, and each ships something.

### 0.5 What this reuses, and what is genuinely new

The honest inventory, from [research §4](../research/deep-analysis-mode.md),
confirmed against the tree on 2026-09-22.

**Reused verbatim — six nodes and one region:**

| | |
|---|---|
| `scope`, `retrieve` | the schema narrowing. MAC-SQL's *Selector*, and better: FK-aware and section-aware rather than model-guessed |
| `generate ⇄ validate` | [`_add_repair_region`](../../backend/app/pipeline/graph.py) — already built into two graphs, so this is its third caller, not its second implementation |
| `execute`, `inspect` | the read-only execution and the result check |
| `disclose()` | [disclosure.py:41](../../backend/app/pipeline/disclosure.py#L41) — every intermediate result, at render time |
| `app/reports/narrate.py` + `checks.py` | prose from disclosed results, with numeric consistency checked in Persian and Latin numerals |
| The SSE bus | `LISTEN`/`NOTIFY`, `subscribe(after_seq=…)`, cancel-as-a-row — survives replicas already |

**Genuinely new — and three of the six are pure:**

| | Pure? | Phase |
|---|:--:|:--:|
| A planner / decomposer | ❌ model call | 4 |
| A run-level budget that fails closed | ✅ | 4 |
| A contribution / driver module | ✅ | 2 |
| Citations as structure | ✅ | 3 |
| *Answer now* | ❌ | 6 |
| A scorer for a multi-query answer | ✅ | 7 |

---

## 1. The artifact — what a deep run *is*

### 1.1 The plan

A structured-output artifact, on the same `structured()` road `clarify`,
`generate` and `chart` already use. It is what the reader watches while waiting,
what *Answer now* truncates, and what the plan-adherence metric scores against.

```python
class PlanStep(BaseModel):
    question: str                     # in the reader's language, human-readable
    intent: Literal["CONFIRM", "DECOMPOSE", "COMPARE", "DRILL", "CHECK"]
    why: str                          # why this step, given what is known
    tool: Literal["SQL", "CONTRIBUTION", "COMPARE_PERIODS", "OUTLIERS"] = "SQL"
    depends_on: list[int] = []        # earlier step indices

class AnalysisPlan(BaseModel):
    restatement: str                  # what the system understood
    steps: list[PlanStep]             # <= budget.max_steps
    stop_when: str                    # the declared success condition
```

`tool` is the one field worth defending. It is Cortex Agents' *tool assignment
per subtask*, typed — and it is what makes the loop bounded and inspectable
rather than free-form. **The planner selects a function; it never writes one.**

### 1.2 The step, and its evidence

One `PlanStep` produces one `StepEvidence`. The evidence is what `synthesize`
reads, and it is the only thing it reads.

```python
class StepEvidence(BaseModel):
    index: int
    step: PlanStep
    attempts: list[SqlAttempt]        # this step's generate ⇄ validate history
    execution: ExecutionResult | None
    disclosed: DisclosedResult | None # what may reach a prompt — never `execution`
    computed: AnalysisResult | None   # `app/analysis/` output, when tool != SQL
    status: Literal["DONE", "SKIPPED", "FAILED"]
```

**`synthesize` is handed `disclosed` and `computed`, never `execution`.** That
is [mvp2 Part 5 §2](mvp2.md) applied per step rather than once at the start, and
it is the invariant a multi-step loop stresses hardest.

### 1.3 The answer

A short report, not a table: findings, a chart where one is warranted, the
supporting tables, and **a citation edge from every claim to the step and the
SQL that produced it**. DataMind has built this artifact once already — a report
is an outline, a statement per block, prose per section, and a summary. **A deep
chat answer is a report whose outline was proposed by the system instead of by a
person.** That is the single largest piece of reuse available, and Phase 3 is
what makes it citable.

### 1.4 The budget

```python
@dataclass(frozen=True, slots=True)
class DeepBudget:
    max_steps: int = 5
    max_queries: int = 12          # steps may repair; repairs are queries
    max_rows_total: int = 20_000
    max_prompt_tokens: int = 400_000
    deadline_at: datetime          # not the chat run's
```

**Everything else in this product fails open; this fails closed.** Checked
before each step by the same discipline `_run_deadline` already applies before
each node. Exhausting it is a **normal termination that synthesizes from what
exists** — the same path *Answer now* takes — never an error.

---

## 2. The data model

Three migrations, and one constraint that needs widening before anything else
works.

### 2.1 `0037` — cache tokens (Phase 1)

`runs.cache_read_tokens`, `runs.cache_write_tokens`, and the same two on
`run_steps`. **Nullable, never defaulted to `0`** — the rule the existing token
columns were added under: a node that called no model and a provider that
reported nothing are different facts, and `NULL` reads as *not measured*.

This is load-bearing rather than tidy. A deep answer costs roughly **an order of
magnitude more tokens** than a chat answer, most of it re-sending the schema
block once per step — [langgraph-migration.md](langgraph-migration.md) Phase 4
measured **88 KB of state per node, 97% of it that block**. That is precisely
the workload prompt caching exists for, and precisely the workload this repo
cannot currently measure. **Widening `Usage` is a prerequisite, not a
follow-up.**

### 2.2 `0038` — claims (Phase 3)

A claim is a sentence, the figures it quotes, and the query it is drawn from.
Whether it lands as a `report_claims` table or a JSONB column on `report_blocks`
is Phase 3's first decision — §11 Q1.

### 2.3 `0039` — the run's depth, and its plan (Phase 6)

`runs.depth` (`QUICK` | `DEEP`, default `QUICK`, so every existing row is
correct without a backfill) and `runs.answer_now_requested` — durable, and read
on the heartbeat, exactly as `cancel_requested` is, because **the replica
holding the `asyncio.Task` is not necessarily the one the request arrived at**.
The plan itself is an artifact, not a column.

### 2.4 The constraint that must widen first

[`generated_queries`](../../backend/app/infra/db/models.py#L1052) carries
`UniqueConstraint("run_id", "attempt_no")`, and `attempt_no` is assigned as
`len(state.attempts) + 1`
([nodes/__init__.py:1281](../../backend/app/pipeline/nodes/__init__.py#L1281)).

**This works unchanged for a deep run, and only if the deep state keeps
`attempts` as the run-global concatenation** rather than resetting it per step.
Do that, and every sub-query lands in `generated_queries` as an ordinary row —
which is the whole of §5.4: metric attribution, the knowledge store's backlog
and usage accounting all pick it up for free, with no new writer.

**One derived property breaks, and it is not the constraint.**
`RunState.repair_count` is `len(attempts) - 1`
([state.py](../../backend/app/pipeline/state.py)), which on a deep run would
report seven repairs where there were seven *steps*. It must be recomputed as
the sum of per-step repairs. Ledger item 5.8; it is the kind of thing that ships
green and is wrong for a year.

---

## 3. The phases

Each phase carries **the goal**, **backend**, **frontend**, **tests**, **done
when**, and **the risk it carries**. A phase is not done until its "done when"
is a fact somebody checked, not an intention. Ledger: [§12](#12-progress-ledger--what-is-done-what-is-not).

### 3.1 Phase 0 — The gate · Size S · **Blocking**

**Nothing in Phases 4–9 means anything until this lands.** Ledger: §12.2.

**The measurement.** Re-run the golden set at the current `PROMPT_VERSION`
(**v10**) with the semantic layer on, and fill the three empty cells
[eval.md §6](../reference/eval.md) already holds a table for:

```
cd backend && python -m app.eval.runner --suite sales_v1
cd backend && python -m app.eval.runner --suite sales_v1 --semantic on
cd backend && python -m app.eval.runner --suite sales_v1 --retrieve-budget 8000
```

Each calls a real provider and needs an `llm_configs` row with a working key.
**This is the same blocker that holds
[learning-loop.md §13.2](learning-loop.md#132-phase-0--fix-the-ruler)'s last box
open**, and the same run closes both. Record the `eval_run` UUID beside each
number, and write the result up in `app/eval/reports/` to the standard the
2026-07-31 report set — which is the one that threw out its own headline.

**The frozen deep set.** Twenty questions in
`backend/app/eval/suites/deep_v1.json`, written **now**, before any of the
implementation exists — which is the only moment they can be written without
being fitted to it. Each is a *why* or *what-drove-it* question the single-shot
path demonstrably cannot answer in one statement. Its `_README` records the
freeze date and the rule; `suites/CHANGELOG.md` records what would justify an
edit, and the answer is the same as for `gold_sql`: demonstrable wrongness, with
evidence, never convenience.

**Done when:** three numbers are on paper in `eval.md §6` with their run UUIDs,
`deep_v1.json` exists and is frozen, and §0.3's rule has been applied out loud —
written into [status.md](../status.md) as a sentence saying which branch was
taken.

**Risk:** none technical. The risk is skipping it because it is unglamorous and
the mode is interesting, and then being unable to defend any number the mode
produces for the next six months.

---

### 3.2 Phase 1 — Cache tokens · Size S · **stands alone**

**Goal.** `Usage` can say what a cached prompt cost. Ledger: §12.3.

**Backend.** [`Usage`](../../backend/app/domain/ports/llm.py#L106) — a frozen
slots dataclass with `prompt_tokens`, `completion_tokens`, `latency_ms`, `model`
— gains `cache_read_tokens` and `cache_write_tokens`. The gateway reads them off
the provider response where the provider sends them and records nothing where it
does not, because *a zero is not always "no tokens"* is already this file's own
rule and an estimate in the same column as a measurement is indistinguishable
from one. `NodeUsage`, `RunState.record_usage`, `RunStepRead` and
`usage_service`'s series follow. Migration `0037`.

**Frontend.** The usage chart's stack is already a list of series; two more join
it. `usage-chart.ts` and its test.

**Tests.** `test_token_accounting.py` — per-node sums still equal run totals with
four columns rather than two. `test_token_accounting_schema.py` — the NULL-vs-0
distinction holds for the new columns.

**Done when:** a run against a caching provider shows four numbers per node, and
`make test` is green.

**Risk:** low. The trap is defaulting the new columns to `0`, which silently
converts *"this provider does not report caching"* into *"this provider cached
nothing"* — and those drive opposite decisions about whether the deep mode is
affordable.

---

### 3.3 Phase 2 — `app/analysis/` · Size M · **stands alone**

**Goal.** The arithmetic a *why* question needs, computed here instead of by a
model. Ledger: §12.4.

**This is the whole of F4's value, and it is buildable today** — pure,
token-free, provider-free, unit-testable, and immediately useful as a *check* on
ordinary answers even before any deep run exists. It is the cleanest case in the
product for [mvp2 Part 5 §4](mvp2.md) — *no model is asked to do arithmetic* —
because the model picks **which dimensions to test** and the module computes
**what actually moved**.

**Do not invent the math.** [research §3.4](../research/deep-analysis-mode.md)
has it from ThoughtSpot SpotIQ, and the insight is that **it is not one
algorithm but three, chosen by measure type** — a single
`contribution = Δ(segment) / Δ(total)` is wrong for two of the three:

| Measure class | Examples | Method |
|---|---|---|
| **Simply decomposable** | `SUM`, `COUNT` | Threshold-based outlier detection over the top ten absolute changes; stop when one timestamp's contribution exceeds 50%; flag values outside the derived thresholds |
| **Ratio-based** | `AVG`, `SUM/SUM` | Difference analysis is mathematically unstable here. Compute a **hypothetical percentage change** per dimension value — *what the overall change would have been had this value not changed*. Smaller hypothetical ⇒ stronger explanation |
| **Complex** | `COUNT(DISTINCT)` | Z-score over the change distribution, with N between **2.0 and 5.0 chosen by dimension cardinality** — higher cardinality, stricter threshold |

**Backend.** A new `app/analysis/` package, sibling to `app/reports/` and under
the same charter:

- `measures.py` — classify a result column into `SIMPLE` / `RATIO` / `COMPLEX`
- `contribution.py` — the three algorithms above
- `periods.py` — period-over-period alignment
- `outliers.py` — the z-score work
- `__init__.py` — the public surface, and the **refusals as typed results, not
  exceptions**: SpotIQ's own documented limits ("growth of" / "versus"
  phrasings, complex `group_*` formulas, mixed attribute types) are copied as
  refusals rather than rediscovered

A **tenth import-linter contract**, *"analysis is self-contained"* — no
`app.infra`, no `app.services`, no provider, joining the nine in
`pyproject.toml`. This is what keeps the package honest: the reason it can be
trusted is that it *cannot* call anything.

**Frontend.** None. The package ships inert.

**Tests.** `test_contribution.py` — the three classes against fixtures with a
known answer, no provider anywhere. `test_analysis_refusals.py` — every
documented refusal, refused.

**Done when:** `make test` and `lint-imports` are green, and the module can name
the top three drivers of a change in a fixture where the answer is known by
construction. **No answer in the product behaves differently.**

**Risk:** medium, and it is mathematical rather than architectural. Getting the
ratio case wrong produces confident nonsense, which is worse than producing
nothing. The mitigation is the fixture with a known answer, and that the module
has no way to reach a database.

---

### 3.4 Phase 3 — Citations as structure · Size M · **stands alone**

**Goal.** A number in the prose resolves to the query that produced it. Ledger:
§12.5.

**Prose that merely *mentions* a number is not a citation.** Reports today cite
by section; this makes the edge **claim → step → SQL → result**, which is what
the traceability metric reads and is the row in the competitive matrix that
nobody else has. It improves reports the day it lands and is the deep mode's
trust mechanism later.

**Backend.** A `Claim` type in `app/reports/`: the sentence, the figures it
quotes, and the query it is drawn from. `narrate.py` emits claims rather than
only prose. `checks.py` — which already parses figures in Persian and Latin
numerals — then checks each claim against **its own cited result**, not against
the union of every result in the report. That is strictly stricter, and it is
where the existing known false positive goes away. Migration `0038`.

**Frontend.** A claim's number is clickable in `report.tsx`; it opens the SQL
behind it. `report-document.test.ts` updated.

**Tests.** `test_report_citations.py` — a claim whose figure appears in a
*different* section's result is flagged, where today it passes.

**Done when:** every figure in a generated report resolves to one query, and the
numeric check runs per claim rather than per section.

**Risk:** medium. The check getting stricter will fail reports that pass today,
and some of those failures will be the check being right. Budget for reading
them rather than loosening the check.

---

### 3.5 Phase 4 — The plan, and a graph that stops · Size L

**Goal.** A planner, a budget that fails closed, and a compiled graph that runs
the loop — with **nothing in the product able to reach any of it**. Ledger:
§12.6.

That inertness is the point, and it is why this is a separate phase from the
loop: the budget, the ceiling and the termination path go into the tree and get
proven *before* anything can start a run that uses them.

**Backend.**

```
deep:  route → plan ⇄ [ step → (scope → retrieve → generate ⇄ validate) → execute → inspect → compute ] → synthesize → chart
                 ↑______________ revise, while budget remains ______________|
```

- `AnalysisPlan` / `PlanStep` / `StepEvidence` in `app/pipeline/state.py`; a
  `DeepState` that is `RunState` plus plan, evidence and budget, with `attempts`
  staying the **run-global concatenation** (§2.4)
- `DEEP_PROMPT_VERSION = "d1"`, its own constant beside `PROMPT_VERSION` and
  `REPORT_PROMPT_VERSION` — the deep planner's prompt versions independently of
  the chat generator's, or neither number can be read
- `nodes.plan` — one `structured()` call, the road `clarify` and `chart` use
- `DeepBudget` (§1.4), and `_check_budget` before every step on the
  `_run_deadline` pattern
- Four new `StepName`s — `PLAN`, `STEP`, `COMPUTE`, `SYNTHESIZE`. Repeated names
  in the trail are already normal: the repair region re-runs `generate` and
  `validate` today, and `run_steps`' uniqueness is on `(run_id, seq)`, which the
  adapter's counter already guarantees
- `_build_deep()` in `graph.py`, reusing `_add_repair_region` — its **third**
  caller, not its second implementation
- Budget exhaustion routes to `synthesize`, never to an error
- `deep_enabled: bool = False` in `core/config.py`

**Frontend.** None.

**Tests.** `test_deep_graph.py` — the edges, the ceiling, and that exhaustion
synthesizes rather than raising. `test_deep_plan.py` — the planner's structured
output, including a plan longer than `max_steps` being truncated rather than
honoured.

**Done when:** a test can drive a deep run end to end with stubbed nodes, and
the budget cannot be exceeded by any path through the graph.

**Risk:** the highest in the plan, and it is one specific thing — **the adapter
owning `seq`, timing, the `run_steps` write and both `emit` calls is what makes
the SSE sequence identical run after run**, and `test_pipeline_events.py`
predates the graph file for that reason. A deep node that emits its own events
is a deep node that can forget to. Reuse `_adapt` or the contract is gone.

---

### 3.6 Phase 5 — The loop · Size L

**Goal.** The steps actually run, the evidence accumulates, and the answer gets
written. Ledger: §12.7.

**Backend.**

- `nodes.step` — one sub-question onto the existing
  `scope → retrieve → generate ⇄ validate → execute → inspect` road. **No new
  guard entry point** for SQL: a step's statement is an ordinary statement
- `nodes.compute` — dispatch on `PlanStep.tool` into `app/analysis/`. The
  planner *selects*; it never writes. A `tool` the dispatch does not know is a
  skipped step, not a crash
- `disclose()` per step, at render time, on the evidence about to reach a prompt
- Plan revision: the executor may **replace a remaining step** from what it
  found; it may never add one beyond the ceiling
- `nodes.synthesize` — `reports/narrate.py` and `reports/checks.py` over the
  evidence, emitting Phase 3's claims
- `repair_count` recomputed as the sum of per-step repairs (§2.4)

**Frontend.** None yet.

**Tests.** Three, and the first is non-negotiable:

- **`test_deep_guard.py` — the guard's sixth entry point.** The hostile corpus
  imported from `test_sqlguard_hostile.py` and replayed through a deep run's
  sub-queries, the way `test_query_service.py`, `test_report_guard.py`,
  `test_dashboard_transfer.py` and `test_knowledge_guard.py` each do for theirs.
  **The moment one door is special, the guarantee is gone**
- `test_deep_disclosure.py` — every intermediate result passes `disclose()`
  before any part of it reaches a prompt, asserted per step rather than once
- `test_deep_loop.py` — revision, the ceiling, the compute dispatch, an unknown
  tool

**Done when:** a deep run against the `sales` fixture answers a *why* question
with a report, every sub-query is a row in `generated_queries`, and `make guard`
is green with the corpus replayed through the new path.

**Risk:** the disclosure one. A loop has many more places to leak a raw result
into a prompt than a straight line does, and the leak is invisible in the output
— the answer looks *better*. The per-step assertion is the only defence, and it
has to be written as a test that fails on a deliberately-broken build.

---

### 3.7 Phase 6 — The surface · Size M

**Goal.** A reader can start one, watch it, and stop it. Ledger: §12.8.

**Backend.**

- `MessageCreate.depth: Literal["QUICK", "DEEP"] = "QUICK"` — beside
  `skip_templates` and `scope`, which already establish that the composer
  carries per-question routing decisions
- Migration `0039` — `runs.depth`, `runs.answer_now_requested`
- Four `RunEventType`s: `PLAN_PROPOSED`, `PLAN_REVISED`, `STEP_EVIDENCE`,
  `BUDGET_SPENT`. **The first three are durable; `BUDGET_SPENT` joins
  `TRANSIENT_RUN_EVENTS`** — it is a gauge that arrives constantly, and the
  final figure is on the run row. That is the same trade `RESULT_PREVIEW` makes
- `POST /runs/{run_id}/answer-now` — **cooperative, and not cancel.** Cancel
  stops the run; *Answer now* skips the remaining plan steps and jumps to
  `synthesize` over the evidence already collected. Written as a durable flag
  and read on the heartbeat, because the replica that can stop the loop is not
  necessarily the one the click arrived at

**Frontend.**

- A **Deep** toggle in the composer beside *Ask within…*
- Four new `case` arms in `ChatPage.tsx`'s event switch, beside the seven there
  now
- The plan panel — the restatement, the steps, and which one is running. **This
  is what the reader looks at for two minutes**, and it is the difference
  between a mode that feels deliberate and one that feels hung
- The deep turn renders report-shaped, with each claim's number opening its SQL
- A step's evidence expands to its statement and its rows
- *Answer now* as a primary control on a running deep turn, not hidden beside
  cancel

**Tests.** `test_deep_api.py` — depth, answer-now, and that both obey the same
authorization as the run they belong to. `deep-plan.test.ts` for the panel's
pure logic.

**Done when:** a reader can ask a *why* question in Deep, watch the plan fill
in, press *Answer now* at step three, and get an answer built from three steps'
evidence that says so.

**Risk:** the latency contract. If the plan panel is not compelling the mode
gets interrupted, and §6's interruption rate will say so. That is a product
finding, not a bug, and Phase 7 is what makes it visible.

---

### 3.8 Phase 7 — Scoring it · Size M

**Goal.** Know whether it works, without an LLM judge. Ledger: §12.9.

**Scoring a deep answer is not execution accuracy** — there is no single
`gold_sql` to compare against. The field's default is LLM-as-judge, whose
documented failure modes are length bias, position bias and self-preference, and
which costs a model call per judgment. **Five of the six metrics need no
provider at all, and they catch most of what will go wrong:**

| Metric | Computed from | Provider |
|---|---|:--:|
| Guard pass rate across all sub-queries | `validate` verdicts | no |
| Execution success rate across all sub-queries | `execute` outcomes | no |
| **Claim traceability** — every number in the prose appears in some cited result | Phase 3's per-claim check | **no** |
| Plan adherence — steps executed vs steps declared | the plan artifact vs `run_steps` | no |
| Queries, tokens and wall-clock per answer | usage accounting + `runs` | no |
| Final-answer correctness on `deep_v1` | comparator or judge | yes |

**Claim traceability is the important one.** It is the property that
distinguishes a deep answer from a long hallucination, this repo already has the
machinery to compute it after Phase 3, and no competitor documents having it.

**The sixth metric is the product one, and no eval will give it:** of the deep
runs a reader starts, **what fraction do they read to the end** rather than
pressing *Answer now* or cancelling? A mode people interrupt is a mode whose
latency contract is wrong.

**Done when:** `--suite deep_v1 --mode deep` produces a scorecard with five
numbers on it, and the interruption rate is a query somebody can run.

**Risk:** scoring four axes on a Pareto frontier — accuracy, cost, latency,
safety — exists so that **a high accuracy number cannot hide a 50× cost
increase**. Reporting accuracy alone would do exactly that.

---

### 3.9 Phase 8 — Governance · Size S

**Goal.** An operator sets the budget, and the cap fails closed. Ledger: §12.10.

This is the Metabase lesson: v61 shipped **per-group token and message limits**
around single-shot AI, and DataMind's usage subsystem deliberately
[*"measures, it does not enforce"*](../status.md#2-what-mvp2-has-landed). **A
deep mode is the trigger to revisit that**, because an unbounded loop is exactly
what a cap that fails closed is for.

**Backend.** `DeepBudget` settable per connection by an administrator; a
`deep.run` capability on roles; audit-log entries for a budget change and for a
refusal. **A refusal to start is a refusal, not a silently smaller run.**

**Frontend.** The connection's settings tab gains the five numbers.

**Tests.** `test_deep_budget_api.py` — the capability, the audit row, and that a
budget of zero steps refuses rather than degrading.

**Done when:** an administrator can cap deep runs on a connection, a
non-capability holder cannot start one, and both facts are in the audit log.

**Risk:** low. The trap is a cap that fails *open* under load, which is the one
failure mode that makes the whole phase pointless.

---

### 3.10 Phase 9 — Documentation · Size S

Ledger: §12.11. The convention is that each phase updates the documents it
touches; this phase exists for the one document that does not exist yet.

- **`docs/reference/pipeline-deep.md`** — the fourth pipeline, written to the
  same shape as the other three: node by node, every error code, what it refuses
- `pipeline-chat.md §0` maps **four** pipelines, not three
- `security.md` — the guard's **sixth** entry point, and what it is not exempt
  from
- `status.md` §2 and §5 — the deferral is closed, with the date and the number
  that closed it
- `decisions.md` — D1–D6 from §0.2
- `docs/README.md`'s index and routing table, and `CLAUDE.md`'s map

---

## 4. UI and UX — the three screens that matter

### 4.1 The toggle

In the composer, beside *Ask within…*, which already establishes that the
composer carries per-question routing. Two states, and the second one says what
it costs: **Quick** / **Deep — a few minutes**. Naming the latency in the
control is the cheapest honesty available, and it is what stops the mode being
experienced as a hang.

**Auto-escalation is deliberately not built** (§10). The most it may ever do is
*suggest*, on a finished shallow answer — *"this looks like a why question — run
a deep analysis?"* — and not until the interruption rate from §3.8 is known.

### 4.2 The plan panel — the screen the reader actually watches

For two minutes this is the product. It shows the restatement first (*"what the
system understood"* — the cheapest way to catch a misread question before
spending five queries on it), then the steps, each with its intent and its
`why`, with the running one marked and finished ones expandable to their SQL and
rows.

A revised step is shown **as a revision**, with what it replaced struck through.
Hiding the revision would make the plan look prescient; showing it makes the
mode look like it is working.

### 4.3 The answer

Report-shaped. Findings, a chart where one is warranted, supporting tables, and
every number clickable to the query behind it. It must not look like a long chat
message, because it is not one and the reader's standard for it is different.

### 4.4 *Answer now*

A primary control on a running deep turn — not tucked beside cancel, because it
is not cancel. The distinction has to survive the copy: **cancel throws the run
away; *Answer now* keeps what it has.** If a reader presses cancel when they
meant *Answer now*, the control failed.

---

## 5. Security, disclosure, and the guard

Restating [mvp2 Part 5](mvp2.md) against this specific feature, because these
are the five ways it will go wrong.

1. **No privileged door.** Every sub-query replays the hostile corpus in
   `test_deep_guard.py`, as the five existing entry points each do. A deep mode
   multiplies generated statements per question by five to ten — **this is the
   row where DataMind's lead widens rather than narrows as the mode gets
   deeper**, and it is the argument for building this rather than against.
2. **Every intermediate result through `disclose()`** — at render time, per
   step, never once at the start.
3. **No model call on any refresh path.** A deep run is a run. It must never
   become something a dashboard or a schedule triggers.
4. **No model does arithmetic.** `app/analysis/` computes; the model narrates.
5. **Every query in the trail, with its SQL.** *"Agents can be difficult to
   control if they are working in a black box"* — and a multi-step loop is
   exactly where a product stops showing its work.

---

## 6. Measurement — what each phase must produce

| Phase | The number it puts on paper |
|:--:|---|
| 0 | Execution accuracy at v10, layer on and off; retrieval recall at a budget that can miss |
| 1 | Cache read/write tokens per node on a caching provider — **and the ratio to uncached**, which is what decides whether the mode is affordable |
| 2 | Top-three driver accuracy on a fixture whose answer is known by construction |
| 3 | Claims per report, and how many resolve to exactly one query |
| 4 | — (nothing runs) |
| 5 | Guard pass rate and execution success rate across sub-queries |
| 6 | Wall-clock to first plan, and to the answer |
| 7 | All five free metrics on `deep_v1`, plus the interruption rate |
| 8 | Refusals per budget setting |

---

## 7. API surface

| Method | Path | Phase | Notes |
|---|---|:--:|---|
| `POST` | `/conversations/{id}/messages` | 6 | gains `depth` |
| `POST` | `/runs/{id}/answer-now` | 6 | 202; cooperative; not cancel |
| `GET` | `/runs/{id}/events` | 6 | four new event types over the existing stream |
| `GET` | `/runs/{id}/plan` | 6 | the plan artifact, for a reader arriving late |
| `GET`/`PUT` | `/connections/{id}/deep-budget` | 8 | administrator only |

No new stream, no new poll, no new worker. **A deep run is a run** — it claims,
heartbeats, cancels and reconciles exactly as every other run does.

---

## 8. File-by-file change map

**New:**

```
backend/app/analysis/{__init__,measures,contribution,periods,outliers}.py   Phase 2
backend/app/pipeline/prompts/deep.py                                       Phase 4
backend/app/eval/suites/deep_v1.json                                       Phase 0
backend/tests/unit/test_contribution.py                                    Phase 2
backend/tests/unit/test_analysis_refusals.py                               Phase 2
backend/tests/unit/test_report_citations.py                                Phase 3
backend/tests/unit/test_deep_{graph,plan}.py                               Phase 4
backend/tests/unit/test_deep_{guard,disclosure,loop}.py                    Phase 5
backend/tests/unit/test_deep_api.py                                        Phase 6
backend/tests/unit/test_deep_budget_api.py                                 Phase 8
frontend/src/components/deep-plan.{ts,test.ts}                             Phase 6
docs/reference/pipeline-deep.md                                            Phase 9
```

**Changed:**

```
backend/app/domain/ports/llm.py            Usage gains two fields        Phase 1
backend/app/domain/value_objects/__init__.py  StepName, RunEventType,
                                              DeepBudget                 Phases 1,4,6
backend/app/pipeline/state.py              DeepState, plan types,
                                           repair_count                  Phases 4,5
backend/app/pipeline/graph.py              _build_deep, third caller
                                           of _add_repair_region         Phase 4
backend/app/pipeline/nodes/__init__.py     plan, step, compute,
                                           synthesize                    Phases 4,5
backend/app/reports/{narrate,checks}.py    claims                        Phase 3
backend/app/services/run_service.py        depth, answer-now             Phase 6
backend/app/api/v1/conversations.py        depth, answer-now, plan       Phase 6
backend/app/api/schemas.py                 MessageCreate.depth, reads    Phase 6
backend/pyproject.toml                     tenth contract                Phase 2
frontend/src/pages/ChatPage.tsx            toggle, four event arms       Phase 6
frontend/src/components/chat.tsx           the deep turn                 Phase 6
frontend/src/components/report.tsx         clickable claims              Phase 3
```

---

## 9. Risks, and what we do about each

| Risk | What we do |
|---|---|
| **The generator is not good enough, and the mode dresses that up** | Phase 0 is a gate with a threshold written before the number exists |
| **Cost.** Roughly 15× the tokens of a chat interaction | Phase 1 first, so it is measurable; Phase 8 so it is capped; §6 reports cost beside accuracy always |
| **A raw result leaks into a prompt inside the loop** | Per-step `disclose()` assertions in `test_deep_disclosure.py`, written to fail on a deliberately-broken build |
| **The SSE contract drifts** | Deep nodes go through `_adapt`. No node emits its own step events |
| **The ratio-case arithmetic is subtly wrong** | Fixtures with answers known by construction; the module cannot reach a database |
| **Readers interrupt the mode** | §3.8's interruption rate. It is a product finding, and the answer may be to make the mode shorter rather than better |
| **The plan looks prescient because revisions are hidden** | §4.2 shows revisions as revisions |
| **The ledger drifts** | Tick in the commit that lands the work. A box that undercounts sends the next reader off to build something that exists |

---

## 10. Deliberately not building

Each with a written trigger, from
[research §6.4](../research/deep-analysis-mode.md). A deferral is a decision.

| Deferred | Trigger to reopen |
|---|---|
| DuckDB / a Python sandbox as the compute step | a question in the frozen `deep_v1` set that the closed function set demonstrably cannot express |
| Parallel sub-agents (orchestrator-worker) | a measured step-DAG where **≥3 steps are genuinely independent**, *and* the 15× token cost is affordable. Note that *"why did revenue drop"* decomposes into **causally ordered** steps — you cannot choose which dimensions to test before you have confirmed the drop and seen its shape. The parallelism condition is not met by most BI questions |
| Unstructured files (PDFs, decks) in a deep run | a named customer, not a roadmap slot |
| Semantic operators / per-row model calls | its own disclosure-ladder decision in [security.md](../reference/security.md), exactly as entity dictionaries |
| Auto-escalation from a shallow answer | after the mode ships **and** the interruption rate is known |
| A human review-and-confirm loop on the produced report | after `deep_v1` has been scored twice |

---

## 11. Open questions

| # | Question | Who decides, and when |
|:--:|---|---|
| Q1 | ~~A `report_claims` table, or JSONB on `report_blocks`?~~ **Answered 2026-09-22: JSONB, on `report_section_results` rather than `report_blocks`** — a claim is a property of the prose, not of a figure. Nothing queries across claims (traceability is per run), and a table would add a join, a cascade and an ordering column to the one read path that has all three. `jsonb_to_recordset` is the migration when something does | closed |
| Q2 | Does a deep run get its own `llm_config`, so the planner can be a stronger model than the generator? | Phase 4. It is cheap to allow and awkward to retrofit |
| Q3 | Does `deep_v1` reuse the `sales` fixture, or need a messier one with a real drop in it? | Phase 0, and it must be answered before the set is frozen |
| Q4 | Is *Answer now* available before the first step finishes? | Phase 6. Probably yes, and it synthesizes a refusal that says why |
| Q5 | Should a deep answer be savable to a report? | After Phase 7. It is the natural composition and it is not free |

---

## 12. Progress ledger — what is done, what is not

> **Nothing here is built.** This plan was written 2026-09-22 and **no phase has
> started**. Every box below is open, and §12.1 lists what the plan *depends* on
> rather than what it builds — confirmed against the tree on that date.
>
> **85 items across ten phases.** Tick a box in the same commit that lands the
> work, never in advance and never in a batch afterwards. Beside each item,
> record the check that proves its state, the way
> [learning-loop.md §13](learning-loop.md#13-progress-ledger--what-is-done-what-is-not)
> does — that is the half of a ledger that survives being read six months later.

### 12.1 Already in the tree — what this plan depends on (not built by it)

The plan would be several times larger if any of these were missing.

| ✅ | What | Verified at |
|:--:|---|---|
| ✅ | **The guard, with five unprivileged entry points**, each replaying the hostile corpus | `test_sqlguard_hostile.py`, `test_query_service.py`, `test_report_guard.py`, `test_dashboard_transfer.py`, `test_knowledge_guard.py` |
| ✅ | **`disclose()` and `HintBudget`** — the policy over what result values reach a model | [disclosure.py:41](../../backend/app/pipeline/disclosure.py#L41), [value_objects/__init__.py:313](../../backend/app/domain/value_objects/__init__.py#L313) |
| ✅ | **`_add_repair_region`** — `generate ⇄ validate` written down once, already built into two graphs | [graph.py:523](../../backend/app/pipeline/graph.py#L523) |
| ✅ | **`_adapt`** — the adapter that owns `seq`, timing, the `run_steps` write and both `emit` calls | [graph.py:422](../../backend/app/pipeline/graph.py#L422), contract in `test_pipeline_events.py` |
| ✅ | **`scope` + `retrieve`** — FK-aware, section-aware, budgeted schema narrowing | [nodes/__init__.py](../../backend/app/pipeline/nodes/__init__.py) |
| ✅ | **`reports/facts.py` + `reports/checks.py`** — arithmetic over result rows, token-free, and the precedent `app/analysis/` copies | [facts.py](../../backend/app/reports/facts.py), [checks.py](../../backend/app/reports/checks.py) |
| ✅ | **`app/reports/` + `workers/report_graph.py`** — multi-figure document assembly with a summary | [reports/](../../backend/app/reports) |
| ✅ | **SSE over `LISTEN`/`NOTIFY`, with `subscribe(after_seq=…)` and cancel-as-a-row** | [cross-replica.md](../reference/cross-replica.md) |
| ✅ | **The report worker's cooperative-then-hard cancel** — the nearest existing pattern to *Answer now* | [workers/report.py:171](../../backend/app/workers/report.py#L171) |
| ✅ | **`TRANSIENT_RUN_EVENTS`** — the precedent `BUDGET_SPENT` joins | [value_objects/__init__.py:410](../../backend/app/domain/value_objects/__init__.py#L410) |
| ✅ | **Per-node token accounting that sums to the run's totals** | `RunState.record_usage`, asserted in `test_token_accounting.py` |
| ✅ | **Nine import-linter contracts**, and the self-contained-package pattern `app/analysis/` joins | [pyproject.toml](../../backend/pyproject.toml) |
| ✅ | **`app/semantic/` and `app/knowledge/`** — the vocabulary a planner plans in, and the verified pairs Microsoft feeds to its *deep* tier | [semantic/](../../backend/app/semantic), [knowledge/](../../backend/app/knowledge) |
| ⚠️ | **`Usage` counts input and output only** — no cache read/write. Phase 1 fixes it | [ports/llm.py:106](../../backend/app/domain/ports/llm.py#L106) |
| ⚠️ | **`usage_service` measures; it does not enforce.** Phase 8 is the first caller that needs it to | [status.md §2](../status.md#2-what-mvp2-has-landed) |
| ⚠️ | **`generated_queries` is unique on `(run_id, attempt_no)`** — safe only while deep keeps `attempts` run-global. §2.4 | [models.py:1052](../../backend/app/infra/db/models.py#L1052) |
| ❌ | **No planner, no run-level budget, no contribution module, no claim citations, no *Answer now*, no multi-query scorer** | the six things this plan builds |

### 12.2 Phase 0 — The gate · **5 / 5** · blocking ✅ **and the gate did not open**

- [x] Execution accuracy at v10, **layer off**, written into [eval.md §6](../reference/eval.md) with its `eval_run` UUID — `--suite sales_v1` → **42.0 %**, `d8c1035d-a03b-4526-8345-dfb82bc7fde9`
- [x] Execution accuracy at v10, **layer on**, same table, same rule — `--suite sales_v1 --semantic on` → **42.0 %**, `5df63738-43a9-4b45-8e6d-b0b68e7ba9b6`. Same headline as the arm above and **not the same questions**: fourteen changed verdict, seven each way, which is what a 50-question suite can and cannot see
- [x] Retrieval recall at a budget that can actually miss — `--suite sales_v1 --retrieve-budget 8000` → **mean 80.2 % / full-hit 62.0 %**, `dc2ea4fd-9164-4524-9b51-bc74d992c8ff`. eval.md's row 3 said 12,000; **8,000 was run** — the divergence and why are recorded in §6 rather than smoothed over
- [x] `backend/app/eval/suites/deep_v1.json` — twenty *why* questions, frozen the day it is written, with a `_README` recording the freeze date and why each question is in it — frozen 2026-09-22, **no `gold_sql` and none intended**; `tests/eval/test_golden_set.py` fails if one appears. **§11 Q3 answered in the same commit:** it reuses `sales`, because the fixture has no planted movement anywhere — see `suites/CHANGELOG.md` for the evidence and for what this set therefore cannot score
- [x] §0.3's rule applied **out loud**: a sentence in [status.md](../status.md) naming which branch was taken and on what number — §6 bullet 4, and §5's Tier 3 row now says the trigger was tested and did not fire

> **The gate was applied on 2026-09-22 and it did not open.** The number §0.3
> names — execution accuracy at v10 with the layer on — is **0.42**, the middle
> band: *"the gate opens for Phases 1–3 only. Phases 4–9 wait on
> [mvp2](mvp2.md) A1/A5/B2 moving the number, and this plan is re-read, not
> re-argued."* Phases 1–3 have landed. **Nothing from Phase 4 onward may
> start.** 0.42 is 13 points under the opening threshold and 6 over the closing
> one, so the verdict is not close enough to be worth arguing about.
>
> The write-up is
> [`app/eval/reports/sales_v1_deepseek_2026-09-22_phase0.md`](../../backend/app/eval/reports/sales_v1_deepseek_2026-09-22_phase0.md).
> The same run closed
> [learning-loop.md §13.2](learning-loop.md#132-phase-0--fix-the-ruler)'s last
> open box, and it caught a bug in the ruler itself: `eval_runs.git_sha` was
> read at *write* time, so arm 1 was filed under a commit made thirty minutes
> into its own run. Fixed, with two tests that fail on the old code.

### 12.3 Phase 1 — Cache tokens · **8 / 8** · stands alone ✅

- [x] `Usage` gains `cache_read_tokens` and `cache_write_tokens` — [ports/llm.py](../../backend/app/domain/ports/llm.py), `int | None` and **not** `int = 0`; `Completion` carries them too, or `route`'s row is null for ever
- [x] The gateway reads them where the provider sends them, and **records nothing where it does not** — no estimate in a measurement's column. Four places are looked in (`prompt_tokens_details.cached_tokens` / `.cache_creation_tokens`, and the usage block's own `cache_read_input_tokens` / `cache_creation_input_tokens`), because LiteLLM normalises Anthropic's pair to different places on different paths. `test_token_accounting.py` pins **both** halves: an unreported count is `None`, a reported `0` stays `0`
- [x] `NodeUsage` + `RunState.record_usage` accumulate both, through `add_reported()` in `ports/llm.py` — one call reporting nothing does not erase a figure another call measured, and the first call of a run does not raise
- [x] Migration `0037` — two columns on `runs`, two on `run_steps`, **nullable, no `0` default**, no backfill. Applied and rolled back against a throwaway database, not only replayed against a recorder
- [x] `RunStepRead` and `usage_service`'s series carry them — and **no aggregate over them is coalesced to `0`**, unlike every aggregate beside them, or the whole distinction dies in the `SUM`. `report_runs` and `semantic_jobs` contribute a typed NULL, which is what those rows actually have to say
- [x] `usage-chart.ts` gains two series; `usage-chart.test.ts` updated — as a **subdivision of the input bar, not a fourth and fifth thing stacked on it**. A cache read is part of the prompt it arrived with, so stacking would draw a column taller than the tokens it stands for; `stackSegments` cuts input into cached / written / read and the column's height is unchanged
- [x] `test_token_accounting.py` — per-node sums still equal run totals across four columns. The invariant lives in `test_run_token_accounting.py` (where the run-vs-steps assertions are) and is asserted **separately** from the two-column one, because the nullable arithmetic is genuinely different and a build using `(x or 0)` would pass the old test and fail only the new one
- [x] `test_token_accounting_schema.py` — the NULL-vs-0 distinction holds for the new columns, and a test asserts the columns are **only** where `0037` put them, so a later widening is a migration rather than a quiet ORM edit

### 12.4 Phase 2 — `app/analysis/` · **10 / 10** · stands alone ✅

- [x] `app/analysis/measures.py` — classify a measure column `SIMPLE` / `RATIO` / `COMPLEX`, by **name**, because a name is all a result column carries: every one of these comes back `numeric` from the database and `quantitative` from the connector. The precedence is asymmetric on purpose — a strong ratio word beats an additive one (`avg_total_revenue` is an average), because calling a sum a ratio costs an answer and calling a ratio a sum costs a *wrong* one
- [x] `contribution.py` — **SIMPLE**: threshold outlier detection over the top ten absolute changes, stopping at a 50% single-timestamp contribution. Shares are signed and sum to 1.0, asserted rather than assumed
- [x] `contribution.py` — **RATIO**: hypothetical percentage change per dimension value; smaller hypothetical ⇒ stronger explanation. The hypothetical replaces the segment's **ratio and its weight together**, so a pure mix shift is found — a segment whose own average never moved but which tripled in volume is the whole of the change, and a difference analysis would report that nothing happened. That case is the phase's headline test
- [x] `contribution.py` — **COMPLEX**: z-score with N ∈ [2.0, 5.0] chosen by dimension cardinality, and `contribution` is `None` on every driver because a distinct count has no share to give
- [x] `periods.py` — period-over-period alignment, reporting **per calendar day beside the total** and saying which to read. The commonest false driver in the product is a shorter month: January-to-February on the `sales` fixture is −9.7% in total and **0.0% per trading day**, which is `deep_v1`'s first question
- [x] `outliers.py` — z-score over a change distribution, with `discriminating` naming the ceiling nobody mentions: |z| can be at most `(n-1)/√n`, so on a four-value dimension **nothing can ever be flagged whatever the data does**. Without that flag an empty result reads as a finding
- [x] `__init__.py` — the public surface, with SpotIQ's documented limits copied as **typed refusals, not exceptions**. `Refusal` is falsey and carries a sentence written for a reader, so `if not result: return result.reason` is the whole calling convention
- [x] A **ninth** import-linter contract, *"analysis is self-contained"* — `lint-imports` 9/9 green, and proven to go red on a deliberately-added `import sqlalchemy` rather than assumed. (The plan said *tenth*, and §12.1 said nine already existed; there were eight. Counted, not remembered)
- [x] `test_contribution.py` — the three classes on fixtures with answers known by construction, **no provider**, no database, no fixtures directory. 16 tests
- [x] `test_analysis_refusals.py` — every documented refusal, refused, plus one test asserting over every entry point that **nothing is ever raised**. Writing them found two real defects: a non-finite cell propagated to `mean=inf` and every z-score `nan`, so the module reported a quiet distribution in which nothing stood out; and the `max(scale, 1.0)` floor made "flat" an *absolute* test below 1.0, calling a conversion rate that doubled from 0.002 to 0.004 unchanged. 23 tests

**Done when:** the module names the top three drivers of a known change, and
**no answer in the product behaves differently.**

### 12.5 Phase 3 — Citations as structure · **8 / 8** · stands alone ✅

- [x] A `Claim` type in `app/reports/checks.py` — sentence, figures quoted, result cited, and the figures that result does not support. It lives in `checks.py` rather than `narrate.py` because parsing a citation needs `figures_in`, and putting it the other way round would make `narrate` import `checks`
- [x] `narrate.py` **numbers** the results, `REPORT_SECTION_SYSTEM` asks for a citation per sentence, and `parse_claims` lifts the markers out before the prose is stored. `REPORT_PROMPT_VERSION` r4 → r5 — the wording moved, so the constant did
- [x] `checks.py` checks each claim against **its own cited result**, not the union — the pool shrinks from every result in the section to one. An uncited sentence falls back to the union and is counted in `uncited`, so a provider that ignores the instruction leaves the check exactly as strong as it was; a citation to a result that does not exist is kept as uncited rather than dropped, because a fabricated source is worth seeing
- [x] Migration `0038` — **§11 Q1 answered: JSONB**, by the question's own rule. A table the moment something queries across claims, and nothing does: traceability is per run, and a claim is only ever read with the prose it belongs to. NULL = predates citations, `[]` = the writer cited nothing. Applied and rolled back against a throwaway database
- [x] The API exposes the SQL behind a claim — as `block_result_id`, resolved against the `blocks` already in the same response rather than as SQL on the claim itself. That is what keeps the intersection rule intact for free: a reader who may not see a figure gets a `restricted` block with no `sql_text`, and the footnote pointing at it resolves to nothing instead of to a statement naming a database they were never given
- [x] `report.tsx` — a claim's footnote opens its statement and scrolls to it. A footnote after the **sentence**, not a link on the digit: a claim is an assertion made from a result, and underlining the number would say the number is sourced while leaving the claim around it unsourced
- [x] `test_report_citations.py` — a figure drawn from a different result is **flagged**, and the same test asserts the old union check **passes** it, because "stricter" is a claim about the difference. 12 tests, both numeral systems, no provider
- [x] `report-document.test.ts` — `claimSpans`, and the rule it exists for: **an edited paragraph loses its footnotes.** The claims record what the model wrote, and a citation still attached to a sentence somebody has rephrased points at a source that sentence no longer draws on — worse than no footnote, because it looks checked

### 12.6 Phase 4 — The plan, and a graph that stops · **12 / 12** ✅ *(started over the gate — see the status banner)*

- [x] `AnalysisPlan` / `PlanStep` / `StepEvidence` in `pipeline/state.py` — every `PlanStep` field **required in the schema** and filled by a `before` validator, `ClarificationProposal`'s lesson: a defaulted `tool` drops out of `required` and every step would silently become plain SQL. `test_deep_plan.py` asserts the `required` set
- [x] `DeepState` — `RunState` plus plan, evidence, budget; `attempts` stays the **run-global concatenation** (§2.4). `repair_count` is overridden to count **the current step's** attempts only, so `validate`/`execute`/`inspect` run unmodified and each sub-question gets a chat question's repair allowance — asserted by a run where both steps need a repair
- [x] `DEEP_PROMPT_VERSION = "d1"`, its own constant beside `PROMPT_VERSION` and `REPORT_PROMPT_VERSION`
- [x] The planner prompt in `pipeline/prompts/deep.py` — the planner sees **the generator's own schema block under the policy in force** (a value list reaches it under SAMPLE and not under NONE, asserted), narrowed like a schema question above the retrieve budget
- [x] `nodes.plan` — one `structured()` call. Lives in `pipeline/nodes/deep.py` beside the other three deep nodes rather than in `nodes/__init__.py`, which is 2,200 lines of chat nodes the deep graph reuses unchanged
- [x] `DeepBudget` in `domain/value_objects/` — five bounds, frozen, slots. Its docstring says which bounds are refused **before** they are spent (steps, queries, rows) and which are checked before each call and so can be overrun by the one call in flight (tokens, time)
- [x] `_check_budget` before every step — **on the edges rather than in the adapter's deadline hook**, because the hook raises and exhaustion must route. Every way into `step` goes through `_next_step`, every way back into `generate` through `may_repair`, and `step` narrows each query's row cap to what the run's row budget has left
- [x] Four new `StepName`s — `PLAN`, `STEP`, `COMPUTE`, `SYNTHESIZE`; `test_pipeline_graph.py`'s name test now pins both graphs rather than the chat chain alone
- [x] `_build_deep()` in `graph.py`, **reusing `_adapt` and `_add_repair_region`** — its third caller. The region gained a `failed` exit and a `may_repair` gate; chat and draft take the defaults (`END`, no gate), and `test_pipeline_graph.py` / `test_pipeline_events.py` pass unedited on that
- [x] Budget exhaustion routes to `synthesize`; it is a normal termination, never an error — one test per bound, each crossing it on purpose. A **failed step** is evidence too: the region's give-ups lead to `compute`, not `END`. A node *crash* still ends the run
- [x] `deep_enabled: bool = False` in `core/config.py`, and nothing in the product reaches the graph — asserted by walking every import under `app/` outside `app/pipeline/`
- [x] `test_deep_graph.py` + `test_deep_plan.py` — the edges, the ceiling, exhaustion-synthesizes, and a plan longer than `max_steps` truncated rather than honoured. 27 tests, no provider; the worst-case path (every statement refused by the database) is driven to the query ceiling and fits `deep_recursion_limit`

**Done when:** the budget cannot be exceeded by any path through the graph, and
**nothing in the product can start a deep run.**

### 12.7 Phase 5 — The loop · **11 / 11** ✅ *(the "against `sales`" clause is Phase 7's run — see below)*

- [x] `nodes.step` — one sub-question onto the existing `scope → retrieve → generate ⇄ validate → execute → inspect` road. The generator is asked the sub-question **plus the row shape its tool needs** (`evidence.SHAPE_HINTS`); a plain SQL step is asked exactly its own question
- [x] `nodes.compute` — dispatch on `PlanStep.tool` into `app/analysis/` (`pipeline/evidence.py`). A refusal is a result — `NO_CHANGE` closes the step DONE with the refusal as its finding — and a capped result is refused rather than computed over. **An unknown tool is SKIPPED in `step`, before any query is spent**, through a `step → step` edge that goes back through `_next_step`, so the budget is read on it too
- [x] `disclose()` per step — in `compute`, under the run's policy, **before anything downstream can read the result**: the reviser and the writer read only what it returned
- [x] `StepEvidence` accumulation — `synthesize` reads `disclosed` and `computed`, **never `execution`**. `evidence.narration` is the one function that builds a prompt block from a step, and it does not touch `execution`; the result's row count, truncation and column types are copied onto the evidence as shape so it never has a reason to. Computed figures reach the writer **only when it was handed every row they came from** — `reports/facts.py`'s rule, applied to `app/analysis/`
- [x] Plan revision — **replace** a remaining step; never add beyond the ceiling. Only a step that names dependencies, only when all of them produced a result, only under `SAMPLE`/`FULL`; one structured call that fails open. Revisions are kept as `PlanRevision(replaced, by)` for §4.2's strike-through
- [x] `nodes.synthesize` — `reports/narrate.py`'s section prompt with the steps as its numbered results, streamed; `parse_claims` + `check_claims` produce Phase 3's claims, each checked against **its own step's** result. A preface written by the product (not the writer) opens any answer built from part of the plan. Under `NONE`/`AGGREGATE`, with nothing to write from, or when the writer fails, the answer is **written without a model** from the evidence
- [x] Every sub-query lands in `generated_queries` as an ordinary row — through the real `_finalise`, on the conftest's SQLite schema: one row per statement, and each `query_executions` row filed against **the result its own statement produced** (`RunState.execution_for`; a chat run is unchanged). The fixture gained `query_executions` and `run_events`
- [x] `repair_count` recomputed as the sum of per-step repairs — `total_repairs`, which is what `_finalise` now writes; identical to `repair_count` on a chat run
- [x] **`test_deep_guard.py` — the guard's sixth entry point.** The corpus replayed through a first draft, a repair after the guard refused, a repair after the database refused, and a **revised** step — 96 cases, no bypass. `make guard` now runs it beside the corpus
- [x] `test_deep_disclosure.py` — sentinel strings in every row, every byte sent to any model scanned for them, per step and per policy. **The canary replaces `disclose()` with a pass-through and asserts the scan finds the leak.** Writing it found one: a model-free answer quoted figures computed from rows past the fifty the model was allowed, which `disclose_history` would have replayed verbatim on the next turn under `SAMPLE`. Fixed in `_plain`, with its own test
- [x] `test_deep_loop.py` — revision, the ceiling, the compute dispatch (a contribution whose driver is known by construction: EMEA, 90.0% of the change), an unknown tool, per-claim checking, the partial-plan preface, the failed-writer fallback, and the rows `_finalise` writes. 15 tests

**Done when:** a deep run answers a *why* question against the `sales` fixture,
every sub-query is a `generated_queries` row, and `make guard` is green with the
corpus replayed through the new path.

> **Two of the three are facts; the first is proven only in a scripted world.**
> Every node is real and every provider call and query result is scripted, so
> what is shown is that the loop *works*, not that it answers well. The run
> against the real `sales` fixture is Phase 7's `--suite deep_v1 --mode deep`,
> and it is recorded there.

### 12.8 Phase 6 — The surface · **12 / 12** ✅

**Backend**

- [x] `MessageCreate.depth: "QUICK" | "DEEP"` — beside `skip_templates` and `scope`. **DEEP is refused (422) while `deep_enabled` is off**, before anything is written; a retry of a deep run is refused the same way rather than silently answered as a chat question. `/auth/me` gains `features: ["deep"]` so the composer only offers what exists
- [x] Migration **`0041`** (the plan said `0039`; hybrid retrieval took `0039`/`0040`) — `runs.depth` (NOT NULL, default QUICK, `ck_runs_depth`), `runs.answer_now_requested` (NOT NULL, default false). Applied, rolled back and re-applied against a throwaway Postgres 16
- [x] `PLAN_PROPOSED`, `PLAN_REVISED`, `STEP_EVIDENCE` — durable `RunEventType`s, emitted by `plan`, the reviser and `compute`. The durable record once the run ends is an **`ANALYSIS` artifact** (plan, revisions, every step with its statement and computed summary, the claims, traceability, the budget, `DEEP_PROMPT_VERSION`) — an artifact rather than a column so it is withheld with the others from a reader who may not see the run's data
- [x] `BUDGET_SPENT` — and it joins `TRANSIENT_RUN_EVENTS`, for `RESULT_PREVIEW`'s reason
- [x] `POST /runs/{id}/answer-now` — durable flag, read on the heartbeat (the same `UPDATE … RETURNING` as `cancel_requested`), **cooperative and not cancel**: the graph reads `pipeline/signals.py` on the edge into the next step and on every edge back into `generate`, so the step in flight finishes, no other starts, and `synthesize` writes from what exists. `modify` on the conversation, as cancel; 409 on a quick or finished run
- [x] `GET /runs/{id}/plan` — for a reader arriving late: the `ANALYSIS` artifact once the run has ended, the durable events folded (`services/deep_plan.fold`) while it runs. A reader with the thread but not the connection gets the plan's questions and **nothing any step found**

**Frontend**

- [x] The **Deep** toggle in the composer, naming its latency in the control — *Quick* / *Deep — a few minutes*, a radio pair beside *Ask within…*, shown only when `/auth/me` names the feature, and back to Quick after every send
- [x] Four new `case` arms in `ChatPage.tsx`'s event switch, one fold (`deep-plan.ts`'s `applyDeepEvent`, the same fold the server runs)
- [x] The plan panel — restatement, steps, the running one marked, **revisions shown as revisions** (the old wording struck through above the new, and *revised*); a step's row opens onto its statement and what was computed from it; steps never reached read *Not run*, never *Waiting*
- [x] The deep turn renders report-shaped, each claim opening its SQL — a footnote after the **sentence** (Phase 3's rule), which opens that step in the panel and scrolls to it. The product's own opening sentence ("built from 3 of 5 planned steps…") is recognised and never footnoted
- [x] *Answer now* as a primary control in the panel header — "Answer now · keep what's found", its tooltip naming the difference from the composer's stop — which turns into "Finishing this step, then writing the answer…" the moment it is pressed

**Tests**

- [x] `test_deep_api.py` + `deep-plan.test.ts` — 14 backend tests through the real app (`World`): depth refused while off, recorded when on, *Answer now* recorded and signalled and **not** cancel, refused to a stranger and to a reader with `select` only, 409 on quick and finished runs, the heartbeat handing the flag to the graph without stopping it, the plan folded from events and read from the artifact, withheld to a reader without the data. `npm run test:deep` pins which step is running, revisions, *Answer now*'s availability, the stop sentence and the footnote alignment

**Done when:** a reader asks a *why* question in Deep, watches the plan fill in,
presses *Answer now* at step three, and gets an answer built from three steps'
evidence **that says so**.

> **Seen, in the real SPA, against fixtures.** Vite on the host, every
> `/api/v1/**` answered from a script (nothing reached `.data/db`), SSE refused
> so the client polled, and the poll releasing a five-step analysis a batch at a
> time. The toggle sends `depth: "DEEP"`; the plan fills in; step three arrives
> revised and struck through; *Answer now* pressed during step three shows
> *Finishing this step…*; the answer opens *"This answer is built from 3 of 5
> planned steps: the analysis stopped early because you asked for an answer
> now"*, steps 4 and 5 read *Not run*, and footnote [3] opens step three's
> statement — dark and light. **What that does not show is a real provider
> writing the plan**; that is Phase 7's run.

### 12.9 Phase 7 — Scoring it · **7 / 7** ✅ *(the full twenty-question run was still in flight when this landed)*

- [x] A `--suite deep_v1 --mode deep` arm on the runner — `app/eval/deep.py` runs each question through `DeepPipeline` on the throwaway `sales` fixture at the shipped budget and deadlines; the suite and the mode **refuse each other** when they disagree, before the app database is touched. Filed under `prompt_version = "v12+d1"`; gold-shaped columns NULL, never `False`
- [x] Guard pass rate across all sub-queries — no provider; pooled over every statement, repairs included
- [x] Execution success rate across all sub-queries — no provider; of the statements the guard accepted, the ones the database ran
- [x] **Claim traceability** — every number in the prose appears in some cited result. No provider: Phase 3's per-claim check, pooled over the claims that state a figure, with the uncited count beside it
- [x] Plan adherence — steps done / steps declared, and `plan_reached` (attempted / declared) beside it, plus how many runs stopped early and why
- [x] Queries, tokens and wall-clock per answer, **reported beside accuracy, never instead of it** — as distributions (mean, p50, p95, max). The card's answer-correctness field is `null` with the sentence saying why: this scorecard uses no judge, and `deep_v1` has no gold
- [x] **The interruption rate** — `services.deep_plan.interruption()` over `runs`, and the same query as SQL in [eval.md](../reference/eval.md) §1. Failures are the product's, not the reader's, and are kept out of the denominator

**Done when:** `--suite deep_v1 --mode deep` produces a scorecard with five
numbers on it, and the interruption rate is a query somebody can run.

> **The first real run, on one question** — `bc3bd6d3-d8b5-45b6-a291-2d4e6c2b0baa`,
> 2026-09-24, DeepSeek V4 **Flash** (`3b42e44b`), `deep-001`. This is a Flash
> number and may not share a sentence with the Pro baselines (eval.md §6).
> Guard pass 100% over 3 statements, execution success 100%, **claim
> traceability 25.0%** over 4 claims (1 uncited), plan adherence 60% — **stopped
> on the soft deadline after 3 of 5 steps, in 547 s.** Two findings, recorded
> rather than tuned away:
>
> - **The latency contract does not hold on this model.** Two calls were 78% of
>   the wall clock: the plan (7,110 completion tokens, 224 s) and the first
>   step's `generate` (13,946 completion tokens, 202 s) — a reasoning model
>   thinking at length, not a loop. At the shipped 600 s deadline a five-step
>   plan cannot finish, so on Flash the mode answers from partial evidence by
>   construction. This is the §3.8 risk arriving before any reader has seen the
>   mode, and the answer may be a shorter plan rather than a longer deadline.
> - **The answer named a driver the fixture does not have.** `deep-001`'s
>   `known_by_construction` is that February is short only by three days —
>   nothing moved per trading day. The run decomposed the fall by channel
>   ("web … 62.8% of the total change") and category, which is arithmetic that
>   is true of the rows and an explanation that is false, and it never reached
>   the per-day check. This is precisely the failure `deep_v1` was frozen to
>   catch, and it is the gate's warning (§0.3) made concrete.

### 12.10 Phase 8 — Governance · **0 / 6**

- [ ] `DeepBudget` settable per connection by an administrator
- [ ] The cap **fails closed** — including under load
- [ ] A `deep.run` capability on roles
- [ ] Audit-log rows for a budget change and for a refusal
- [ ] The connection settings tab gains the five numbers
- [ ] `test_deep_budget_api.py` — the capability, the audit row, and a zero-step budget **refusing** rather than degrading

### 12.11 Phase 9 — Documentation · **0 / 6**

- [ ] `docs/reference/pipeline-deep.md` — the fourth pipeline, to the same shape as the other three
- [ ] `pipeline-chat.md §0` maps four pipelines
- [ ] `security.md` — the sixth entry point, and what it is not exempt from
- [ ] `status.md` §2 and §5 — the deferral closed, with the date and the number that closed it
- [ ] `decisions.md` — D1–D6 from §0.2
- [ ] `docs/README.md` index and routing table, and `CLAUDE.md`'s map

### 12.12 Totals

| Phase | Done | Total |
|---|:--:|:--:|
| 0 — The gate | 5 | 5 |
| 1 — Cache tokens | 8 | 8 |
| 2 — `app/analysis/` | 10 | 10 |
| 3 — Citations | 8 | 8 |
| 4 — The plan and the graph | 12 | 12 |
| 5 — The loop | 11 | 11 |
| 6 — The surface | 12 | 12 |
| 7 — Scoring | 7 | 7 |
| 8 — Governance | 0 | 6 |
| 9 — Documentation | 0 | 6 |
| **Total** | **73** | **85** |

### 12.13 Change log

| Date | What changed |
|---|---|
| 2026-09-22 | Written. No phase started. |
| 2026-09-24 | **Phase 7 complete, 7/7.** `--suite deep_v1 --mode deep` runs the frozen set through `DeepPipeline` on the real fixture and prints a scorecard of the five provider-free numbers — guard pass rate, execution success, claim traceability, plan adherence, cost per answer — pooled over statements and claims, with answer correctness stated as not scored. The interruption rate is `services.deep_plan.interruption()` and its SQL twin in eval.md. First real run, one question on Flash (`bc3bd6d3`): 100% / 100% / **25.0%** / 60%, **547 s, cut by the soft deadline at step 3**, and an answer that attributed a calendar effect to channels — both recorded above as findings. The full twenty-question run was in flight when this landed. `make test` green. | 73 of 85 |
| 2026-09-24 | **Phase 6 complete, 12/12.** A reader can start one, watch it, and stop it early. `depth` on the message (refused while `deep_enabled` is off), `0041` for `runs.depth` and `runs.answer_now_requested`, three durable plan events and a transient budget gauge, `POST /runs/{id}/answer-now` (cooperative, read on the heartbeat, **not** cancel) and `GET /runs/{id}/plan` (the `ANALYSIS` artifact, or the events folded; withheld to a reader without the data). The composer offers *Quick* / *Deep — a few minutes* where `/auth/me` names the feature; the plan panel shows the running step, revisions struck through, and *Answer now* as its primary control; the answer's footnotes open each step's statement. Seen end to end in the SPA against fixtures, both themes. `make test` 3,452 green, `npm test` 22 suites + build green. | 66 of 85 |
| 2026-09-24 | **Phase 5 complete, 11/11.** The steps run, the evidence accumulates, the answer gets written. `compute` discloses each step's result the moment it closes and dispatches its tool into `app/analysis/` (`pipeline/evidence.py`); `step` revises a dependent step from what its dependencies found — replace only, never add — and asks the generator for the row shape the tool needs; `synthesize` is `reports/narrate.py`'s section prompt over the steps, with Phase 3's claims checked per step. The guard's sixth entry point replays the corpus through four kinds of deep statement (96 cases); the disclosure test scans every prompt for sentinel rows and **proves the scan works on a deliberately broken `disclose()`** — and found a real leak through the next turn's history, fixed. `_finalise` files each sub-query against its own result and records the run's total repairs. `make test` 3,438 green, `make guard` 140, `lint-imports` 9/9. **Not yet run against a real provider or the real `sales` fixture** — that is Phase 7. | 54 of 85 |
| 2026-09-23 | **Phase 4 complete, 12/12 — started over the gate, on the owner's instruction.** §0.3 still reads 0.42 and still says Phases 4–9 wait; the owner asked for 4–7 regardless, and that is recorded in the status banner rather than the gate being re-read. `DEEP_GRAPH` is compiled at import and reachable from nothing: `route → plan → [step → scope → retrieve → generate ⇄ validate → execute → inspect → compute] → synthesize → chart`. The budget is read **on the edges** — into `step` and back into `generate` — so exhaustion routes to `synthesize` rather than raising, and a failed sub-question closes its step as evidence instead of ending the run. `compute` and `synthesize` are their Phase 4 minimum (record the step; list what ran); Phase 5 fills them. `make test` 3,318 green, `lint-imports` 9/9. | 43 of 85 |
| 2026-09-22 | **Phase 0 complete, 5/5 — and the gate did not open.** The three arms ran back to back on one model (DeepSeek V4 Pro, temp 0.0, v10, 2h 13m): **42.0 % layer off** (`d8c1035d`), **42.0 % layer on** (`5df63738`), **recall 80.2 % / 62.0 % at budget 8,000** (`dc2ea4fd`). 0.42 is §0.3's middle band, so **Phases 4–9 wait**; §0.3's rule is now written out loud in `status.md` §6 and its Tier 3 deferral row. Three findings the headline hides: the two 42 % arms share only 14 of their 21 correct answers (14 questions moved, 7 each way — this suite cannot see a difference under ±14 points); two budget-8,000 questions scored recall 0.000 and answered **correctly**, so recall@k scores the annotation rather than the model; and `eval_runs.git_sha` was read at write time, filing arm 1 under a commit made 30 minutes into its own run — **fixed, with two tests that fail on the old code**. Cache tokens came out of the same logs: **60.4 % of a layer-on prompt served from cache**, which is Phase 1's §6 measurement. Write-up: `app/eval/reports/sales_v1_deepseek_2026-09-22_phase0.md`. | 31 of 85 |
| 2026-09-22 | **Phase 3 complete, 8/8.** `REPORT_PROMPT_VERSION` r4 → r5: the section's results are numbered and every sentence stating a figure cites the one it came from. The markers never reach a reader — `parse_claims` lifts them into `Claim` rows on `report_section_results.claims` (`0038`, **JSONB**, §11 Q1 closed), and the numeric check now matches each sentence against **its own** result instead of the union of the section's. A figure borrowed from another result is flagged where it used to pass, and the test asserts both halves. A footnote opens the statement behind the figure; an **edited** paragraph is rendered without footnotes, because the claims describe what the model wrote. `make test` 3,196 green, `npm test` + build green, `0038` applied and rolled back against a throwaway database. | 27 of 85 |
| 2026-09-22 | **Phase 2 complete, 10/10.** `app/analysis/` — five modules, the three SpotIQ algorithms chosen by measure class, period alignment that reports per calendar day, and z-scores at a cardinality-chosen threshold. Refusals are values; `NO_CHANGE` is the commonest one and the honest answer on this fixture. The **ninth** import-linter contract (there were eight, not nine) keeps it unable to reach a model or a database, and was proven to break on a deliberate violation. 39 new tests, no provider anywhere. **Nothing in the product calls it** — the package ships inert, exactly as the phase asks. | 19 of 85 |
| 2026-09-22 | **Phase 1 complete, 8/8.** `Usage` and `Completion` gained `cache_read_tokens` / `cache_write_tokens` as `int | None`; the gateway reads them from the four places providers and LiteLLM put them and **records nothing where they are absent**; `add_reported()` is the one piece of nullable arithmetic, shared by the pipeline and the usage service; migration `0037` on `runs` and `run_steps`, nullable and applied against a real database both ways. The chart **subdivides** the input bar rather than stacking on it, because a cached token is part of the prompt it arrived with. `make test` 3,145 green, `lint-imports` 8/8, `npm test` + build green. | 9 of 85 |
| 2026-09-22 | **Phase 0, the frozen set.** `deep_v1.json` — twenty questions, no gold answers, frozen the day it was written; five static checks in `tests/eval/test_golden_set.py` hold the freeze, one of them failing if a `gold_sql` ever appears. §11 **Q3 answered**: reuse `sales`, because it has no planted movement and a fabricated driver is therefore detectable — evidence in `suites/CHANGELOG.md`. The three measurements were still running when this landed. | 1 of 85 |

---

## 13. The one-line acceptance test

*A reader asks "why did revenue drop in March?" in Deep mode, watches a
five-step plan being worked through, presses **Answer now** at step three, and
gets a short report whose every number opens the SQL that produced it — with all
three sub-queries in the trail, every intermediate result having passed
`disclose()`, and not one figure computed by a model.*
