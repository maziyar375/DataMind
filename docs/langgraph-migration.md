# Migrating to LangGraph

A decision record and a phased plan. The decision half comes first because the
plan's scope depends on it: **three orchestrations move, four call sites stay
put**, and moving those four would be churn.

Written against LangGraph **1.2.10** (`langgraph-checkpoint` 4.2.0,
`langgraph-checkpoint-postgres` 3.1.2), and against the code as of
`f4f9578` — ten chat nodes, `PROMPT_VERSION = "v7"`, `REPORT_PROMPT_VERSION`
at r4.

Companion to [pipeline.md](pipeline.md) (the chat run, node by node — its §6 is
the short port map this file is the long form of),
[pipeline-dashboard.md](pipeline-dashboard.md) and
[pipeline-report.md](pipeline-report.md) (the other two pipelines, which is
where the callers that reuse the chat nodes are described),
[architecture.md](architecture.md) (which deferred LangGraph, and on what
triggers) and [eval.md](eval.md) (which is how each phase is proved safe).

> **Re-planned against the current code.** The first version of this record was
> written before `describe`, before the composed chart call, and before the
> draft path grew a deadline. What that re-reading changed, in order of how much
> it moves the plan:
>
> 1. **The chat graph is not "linear with one back-edge".** There are three
>    edges back into `generate` and two forward jumps that *skip* two nodes
>    (`_restore_superseded`). Phase 1's wiring and Phase 0's snapshot test both
>    change — §1 and Phase 1.
> 2. **The two executors now share a second call site.** `propose_chart_intent`
>    is the `chart` node's model call, extracted so a tile draft can reach it
>    too, with a deliberately different disclosure posture. Phase 2 has a second
>    thing to keep from merging wrongly — §1 and non-negotiable #6.
> 3. **The draft loop grew a deadline** (`DRAFT_DEADLINE_SECONDS`). One of
>    Phase 2's open questions is now answered in code, and the answer is "yes,
>    and it is deliberately not the run's".
> 4. **The report worker has the same two-drivers problem the dashboard has.**
>    `_generate` and `retry_section` each drive the same nodes. Phase 3 is no
>    longer a durability argument alone.
> 5. **The Phase 0 import-linter contract as first written would fail the build
>    in Phase 1** — for the wrong reason. It needs `allow_indirect_imports`.
> 6. `RunState` now carries a second full result set (`superseded_execution`)
>    and a KPI. Phase 4's checkpoint-size audit has more to audit.

---

## 1. Every LLM call site, and the verdict on each

| # | Surface | Call site | Orchestration today | Migrate? |
| --- | --- | --- | --- | :--: |
| 1 | Chat | `nodes.route` | pipeline node | **Yes** |
| 2 | Chat | `nodes.describe` | pipeline node, streamed, one intent only | **Yes** |
| 3 | Chat | `nodes.clarify` | pipeline node | **Yes** |
| 4 | Chat | `nodes.generate` | pipeline node, inside the repair region | **Yes** |
| 5 | Chat | `nodes.present` | pipeline node, streamed | **Yes** |
| 6 | Chat | `nodes.chart` → `propose_chart_intent` | pipeline node, after a data veto | **Yes** |
| 7 | Chat | `run_service.suggest_followups` | one-shot, off the run entirely | No |
| 8 | Dashboard | `sql_draft_service.draft_sql` | **its own hand-rolled loop over the chat nodes** | **Yes** |
| 9 | Dashboard | `propose_chart_intent` via `compose_chart=True` | a second call in that same service function, after the preview | **Yes**, via #8 |
| 10 | Report | `reports/outline.propose` | one-shot | No |
| 11 | Report | per-block SQL | `draft_sql(classify=True, extra_rules=…, tile_type=…)` | **Yes**, via #8 |
| 12 | Report | `workers/report._narrate` | sequential `for` loop in `_generate` | **Yes** |
| 13 | Report | `workers/report._summarise` | after that loop | **Yes**, via #12 |
| 14 | Report | the same two, from `retry_section` | **a second driver over the same nodes** | **Yes**, via #12 |
| 15 | Semantic | `semantic/generator.py` | `asyncio.gather` + semaphore, 4 concurrent | No |
| 16 | Platform | `llm_configs` capability probe | health check | No |

**This table counts orchestration positions, not prompts.** #6 and #9 are the
same function reached from two places; #12 and #14 are the same two functions
driven by two executors. [pipeline.md §0.4](pipeline.md) counts *prompts* — 11
plus the probe — and [security.md §2](security.md) counts *use cases* — twelve,
across fourteen sites. Nothing below adds to either count, and no phase may:
see non-negotiable #6.

### Why the three that move, move

**The chat pipeline (1–6).** This is the only thing in the codebase already
shaped like a graph, and it is a more interesting graph than the first version
of this record claimed. It is not linear-with-one-back-edge; the real control
flow is:

| edge | from | when |
|---|---|---|
| repair | `validate` → `generate` | the guard rejected the statement, budget left |
| repair | `execute` → `generate` | the database refused it, budget left |
| repair | `inspect` → `generate` | a `retry=True` structural finding, **once per run** |
| restore | `validate` → `present` | a check-driven retry failed; the earlier result is put back |
| restore | `execute` → `present` | same, one node later |
| halt | `route`, `describe`, `clarify` → `END` | chitchat/unsupported, a schema answer, a question asked |

The two restore edges are the ones worth reading twice: `_restore_superseded`
returns `goto="present"` from inside `validate` and `execute`, which **skips
`execute` and `inspect` entirely**. So `pipeline.py` is a `while` loop doing
index arithmetic over `ORDER`, resolving both backward and forward jumps with
`next(i for i, (n, _) in enumerate(ORDER) if n == result.goto)` — a linear
search for a label, run on every jump. That is an edge list written as a list
search, and the list is now doing real work. The node signature
`(state, deps) -> NodeResult` was deliberately built LangGraph-shaped, so this
is the wiring change the architecture doc predicted.

**The dashboard draft path (8, 9, 11) — the strongest case, and an unplanned
one.** `sql_draft_service.draft_sql` imports `route`, `retrieve`, `generate`,
`validate` **and now `propose_chart_intent`** from `app.pipeline.nodes`, and
drives them with its own loop:

```python
if classify:                                    # report blocks only
    await route(state, deps)
    if state.intent is not None and state.intent != "ANALYTICAL":
        raise QuestionOutOfScopeError(_OUT_OF_SCOPE[state.intent], intent=state.intent)

await retrieve(state, deps)
for _ in range(DRAFT_MAX_REPAIRS + 1):
    _check_deadline(state)                      # added since this record was written
    result = await generate(state, deps)
    if result.status == "FAILED":
        raise LLMError(...)
    if (await validate(state, deps)).goto != "generate":
        break
# …then a 50-row preview through execute_saved_sql, then, for a tile:
#    _check_deadline(state); await propose_chart_intent(deps, …, composed=True)
```

There are **two executors over one node set**, and the drift between them is
now documented well enough to enumerate:

| | chat run | draft |
|---|---|---|
| deadline | `deadline_at`, checked by the executor before every node | `DRAFT_DEADLINE_SECONDS = 120`, checked by `_check_deadline` before each `generate` and before the chart ask |
| repair ceiling | `settings`-driven `max_repairs` | `DRAFT_MAX_REPAIRS = 1` |
| transition ceiling | `_MAX_TRANSITIONS = 24` | none — a bounded `for` cannot cycle |
| step persistence | `on_step` → `run_steps` | none |
| events | `deps.emit` → SSE | `_no_emit` |
| history | the thread's tail | `[]`, always |
| a refused question | `route` HALTs with a canned reply | `QuestionOutOfScopeError`, stored as the block's verdict |
| the chart ask | run policy passed to `propose_chart_intent` | **no policy passed** — narrowest budget at every policy |

**Read the deadline row as evidence for the merge, not against it.** It did not
exist when this record was first written: `RunState.deadline_at` was inert on
the draft path until someone noticed and closed the gap by hand, in its own
commit, with its own docstring explaining why the value differs. That is the
predicted failure mode happening in slow motion — every change to repair
semantics has to be made twice or it silently diverges. A compiled subgraph
invoked by both callers is the fix, and it is worth doing even if nothing else
on this list happened.

The second shared call site raises the stakes rather than the cost.
`propose_chart_intent` is one function with two triggers *on purpose* — it is
what keeps security.md's inventory at fourteen sites instead of fifteen — and
the two triggers differ in exactly one argument: chat passes
`state.disclosure_policy`, a tile draft passes nothing and gets `NONE`. That
single omission is what makes "no result value ever reaches a model on the
dashboard path" true at **every** policy, including `FULL`
([pipeline-dashboard.md §5](pipeline-dashboard.md)). It is one keyword argument
standing between a documented guarantee and a quiet regression, which is
precisely the kind of thing a rewiring loses.

Because a report block's SQL is `draft_sql` with `classify=True`, `extra_rules`
and `tile_type` set, migrating #8 migrates #11 for free — and `_sql_rules_for`
composes those rules rather than overriding, so the merge must compose too.

**Report generation (12, 13, 14).** Two arguments now, where the first version
of this record had one.

The durability argument is unchanged and still the strong one: a report run is
minutes long, sections are narrated sequentially, each result is committed as
it lands, and a process death fails the whole run — the written sections
survive, but the run cannot continue. Cancellation is an in-process
`asyncio.Event` (cooperative between phases, then a hard `task.cancel()` after
`llm_request_timeout_seconds + 5`), which does not survive a restart and does
not exist on another replica.

The second argument is the one the dashboard path already made: **`_generate`
and `_retry` are two drivers over one node set.** Both run
`assert_wide_enough → _outline → _execute_blocks → _block_result rows →
_narrate | _summarise`, with their own sequencing, their own progress writes,
and a deliberately different `established` — the first pass sees only the
sections written before it, a retry reads the whole document *including the
sections written after it*, minus the summary. That difference is correct and
documented; the duplication around it is not.

Note what the argument is *not*: sections are deliberately sequential, because
each one receives `established` prose so section five can contrast with section
two instead of restating it. **Do not parallelise them.** Fan-out is not the
reason to migrate this; resume-after-crash and one driver are.

### Why the four that stay, stay

**Follow-up suggestions (7)** — `run_service.suggest_followups`: one prompt, one
completion, no state, fails open and returns `[]`, and it fires when the SPA
refreshes a thread rather than when a user asks. A graph adds a compile step and
buys nothing.

**Report outline (10)** — one `complete` call, deliberately not even
`structured` (the docstring explains why). One node is not a graph.

**Semantic generation (15)** — `asyncio.gather` with a semaphore over
independent per-table calls, then a merge. That is map/reduce, and LangGraph's
`Send` API would express the same thing with more machinery and a new failure
mode. It is already the cleanest concurrency in the codebase; leave it.

**The capability probe (16)** — a health check that happens to call a model, and
the one call site that sends no customer data at all.

> Migrating these four anyway would mean touching working code with no test that
> could tell you it got better. If a future feature turns one of them into a real
> graph — say, an outline that iterates with the user — revisit that one then.

---

## 2. What this costs, stated up front

- **A second Postgres driver.** `langgraph-checkpoint-postgres` requires
  `psycopg` 3 and `psycopg-pool`; the app runs on `postgresql+asyncpg://`. From
  Phase 4 the process holds two pools against the same database. There is no way
  around this short of writing a custom `BaseCheckpointSaver` over SQLAlchemy —
  which is a real option if two pools is unacceptable, and is called out in
  Phase 4.
- **A heavy dependency on the request path.** LangGraph pulls in the LangChain
  core object model. The mitigation is the same one used for LiteLLM: confine it
  behind a boundary and let CI prove the boundary holds (Phase 0).
- **A compiled graph inside a synchronous request handler.** From Phase 2 the
  draft path is a graph, and `draft_sql` is called from `POST /sql/drafts` and
  `POST /reports/{id}/blocks/{id}/check` — both request/response, both with a
  user watching. Compile **once at module scope**, never per call; a per-request
  `.compile()` turns a sub-second authoring step into a measurable one.
- **Streaming needs care.** `present` and `describe` both stream tokens through
  `deps.emit`, and both emit `TEXT_RESET` before falling back when a stream
  breaks. LangGraph has its own streaming model; the migration must not route
  prose through it, or the SSE contract changes underneath the SPA.
- **The deadline is the executor's job, and LangGraph has no opinion about it.**
  `AnalyticsPipeline.run` checks `deadline_at` **before every node** and raises
  `RunTimeoutError`; the draft path re-implements the same check in
  `_check_deadline` for the same reason (one `structured` call can take minutes
  once the gateway's retries and backoff are counted). Both checks become the
  node adapter's responsibility — there is no framework hook that does this.
- **`NodeDeps` is not serializable.** It holds a live `DatabaseConnector` and an
  `emit` callable. It cannot live in checkpointed state and must travel through
  the graph's runtime config instead.

---

## 3. Non-negotiables

Every phase is judged against these. If a phase cannot hold all six, the phase
is wrong, not the rule.

1. **Not one prompt byte changes.** `PROMPT_VERSION` stays `"v7"` for the whole
   migration, and `REPORT_PROMPT_VERSION` stays where it is. That includes the
   composition rules: `_with_extra_rules` appends `_sql_rules_for(extra_rules,
   tile_type)` *after* the prompt's own mandatory rules, chat passes an empty
   string, and a test asserts a chat run's SQL prompt is byte-identical to
   pre-feature. The eval gate below only isolates the orchestrator if this holds.
2. **The SSE event sequence is identical.** Same types, same `seq` numbering,
   same order, same `run_steps` rows — `RUN_STARTED`, `STEP_STARTED`,
   `STEP_FINISHED`, `SQL_GENERATED`, `SQL_REJECTED`, `SQL_VALIDATED`,
   `QUERY_COMPLETED`, `RESULT_CHECKED`, `CLARIFICATION_REQUESTED`, `TEXT_DELTA`,
   `TEXT_RESET`, `ARTIFACT_CREATED` (`CHART` **and** `KPI`), `ERROR`,
   `RUN_FINISHED`. The live step trail is a valued feature and the SPA parses
   this contract.
3. **The guard is untouched.** Three entry points, no exemptions, and
   `test_sqlguard_hostile.py` / `test_query_service.py` / `test_report_guard.py`
   stay green throughout.
4. **The dependency rule holds.** `import-linter` passes at every phase — all
   six existing contracts, plus the new one confining `langgraph` (Phase 0).
5. **Disclosure behaviour is unchanged.** `disclose()`, `HintBudget` and
   `disclose_history()` all still filter at render time; reports still refuse
   `NONE`/`AGGREGATE` at creation *and* at the start of every generation *and*
   at every retry; and `propose_chart_intent` still receives the run's policy
   from chat and **no policy at all** from a tile draft.
6. **The LLM call-site inventory does not grow.** Twelve use cases across
   fourteen sites ([security.md §2](security.md)), eleven prompts plus a probe
   ([pipeline.md §0.4](pipeline.md)). A rewiring that gives the chart ask two
   node identities, or that turns a shared function into two graph nodes with
   two prompts, adds a row to a security document — which is a decision, not a
   refactor.

---

## 4. The phases

### Phase 0 — Groundwork and the safety net

No behaviour change. This phase exists so every later phase can be proved.

- Add `langgraph>=1.2,<2` to `[project.dependencies]`. Do **not** add the
  checkpointer yet — it arrives in Phase 4 with its driver.
- Add a seventh `import-linter` contract confining it:

  ```toml
  [[tool.importlinter.contracts]]
  name = "langgraph stays in the orchestration layer"
  type = "forbidden"
  source_modules = ["app.domain", "app.sqlguard", "app.semantic", "app.reports",
                    "app.charts", "app.api"]
  forbidden_modules = ["langgraph", "langchain_core"]
  # Direct imports only. `app.api → app.services → app.pipeline.graph →
  # langgraph` is a real chain the moment Phase 1 lands, and it is not a
  # violation: the rule is that these packages do not *know* about langgraph,
  # which is the same rule the CI grep enforces.
  allow_indirect_imports = true
  ```

  **`allow_indirect_imports` is not optional here.** A forbidden contract
  reports indirect chains by default, so without it this contract goes red in
  Phase 1 for a reason that has nothing to do with layering, and someone deletes
  the contract instead of the import. Note also that `app.reports` is in the
  source list deliberately: the report graph built in Phase 3 goes in
  `app/workers/`, **not** in `app/reports/`, which is self-contained by contract
  and sits below the pipeline.
- Add a CI grep in the same spirit as the LiteLLM one — `import langgraph`
  outside `app/pipeline/` and `app/workers/` fails the build.
- **Capture the eval baseline now.** Run `sales_v1` on a fixed model at
  temperature 0, record the `eval_run` UUID, the accuracy, and the companion
  metrics. Every later phase compares against this run, not against
  `sales_v1.baseline.json` (which was measured on a different model). Two
  things to record by hand while you are there:
  - **The prompt version is `v7`, whatever the row says.** `runs.prompt_version`
    is written from `settings.prompt_version` (default `"v2"`), not from the
    `PROMPT_VERSION` constant — [pipeline.md §7](pipeline.md) records this drift.
    Write the constant into the baseline note or the comparison is meaningless.
  - **The negative suite is no longer cheap.** Since `describe` landed, each of
    the 3 METADATA records costs a second schema-bearing call
    ([eval.md](eval.md)). Expect the token line to be higher than the record
    count suggests; that is not a regression.
- **Write the SSE snapshot test.** Drive a run end to end with a scripted fake
  gateway and assert the full ordered list of `(seq, type, name, status)` events
  plus the `run_steps` rows. This test is the contract for non-negotiable #2 and
  it must exist before anything moves. **Three runs, not one** — one per control
  flow that is not the happy path:

  | run | what it pins |
  |---|---|
  | analytical, clean | the ten-node order, and that `describe` and `clarify` write `SKIPPED` rows |
  | METADATA | the only path that ends at `describe` — the halt the tempting Phase 1 "improvement" would silently change |
  | check-driven retry that fails | `inspect → generate`, then `_restore_superseded` jumping **forward** to `present` and skipping `execute`/`inspect` — two `seq` sequences that no linear reading of `ORDER` predicts |

  Assert the artifact and text events too, not only the step pair: a single-row
  result emits `ARTIFACT_CREATED {"kind": "KPI"}` from the `chart` node instead
  of a chart, and that branch runs *before* any model call.

### Phase 1 — The chat pipeline as a compiled graph (wrap, don't rewrite)

The single most important decision in this migration: **the ten node functions
are not modified.** They keep mutating `RunState` and returning `NodeResult`. A
thin adapter turns each into a LangGraph node.

- New `app/pipeline/graph.py`. State schema is a `TypedDict` with one key,
  `run: RunState` — the existing model carried whole rather than decomposed into
  per-field reducers. Mutation-in-place keeps working; the adapter returns the
  mutated object as the update.
- `NodeDeps` travels in `config["configurable"]`, never in state.
- The adapter owns what `pipeline.py` owns today: **the deadline check**, timing,
  the `on_step` persistence call, and the two `emit` calls. That is how the event
  sequence stays byte-identical and how `RunTimeoutError` keeps being raised
  before a node rather than inside one.
- Sketch:

  ```python
  def _adapt(name: str, fn: NodeFn):
      async def node(state: GraphState, config: RunnableConfig) -> Command:
          deps = config["configurable"]["deps"]
          # …check deadline_at → RunTimeoutError, emit STEP_STARTED, time it,
          #    call fn, persist the run_step, emit STEP_FINISHED — exactly as
          #    pipeline.py does now, including the crash handler…
          result = await fn(state["run"], deps)
          goto = END if result.status in ("HALT", "FAILED") else (result.goto or _next(name))
          return Command(goto=goto, update={"run": state["run"]})
      return node
  ```

  Note `result.goto or _next(name)` reads a *label*, not a direction. That is the
  whole trick: the same expression carries the three repair edges backward and
  the two restore edges forward, so no edge needs to be special-cased and none
  can be forgotten.
- The port map, edge by edge:

  | today | LangGraph |
  |---|---|
  | `ORDER` + `index += 1` | `add_edge` chain |
  | `validate`/`execute`/`inspect` → `generate` | conditional edges (three of them) |
  | `validate`/`execute` → `present` via `_restore_superseded` | conditional edges that **skip `execute` and `inspect`** |
  | `status="HALT"` (`route`, `describe`, `clarify`) | edge to `END` |
  | `status="FAILED"` | edge to `END` |
  | `_MAX_TRANSITIONS = 24` | `recursion_limit`, **plus a handler** |
  | `deadline_at` before each node | the adapter, before calling `fn` |

  The ceiling needs the handler because the two mechanisms fail differently:
  today an overrun writes `RunError(code="E_PIPELINE_LOOP")` and returns the
  state, and the run ends like any other failed run. LangGraph raises
  `GraphRecursionError` out of `invoke`. Catch it at the facade and write the
  same `E_PIPELINE_LOOP` error, or a runaway graph becomes a 500 — which is the
  one thing `pipeline.py` has never done.
- **`describe` stays a node, not a conditional edge.** It is the one node that
  runs for a single intent — METADATA, where it halts — and reports `SKIPPED`
  for every other, which is genuinely an edge in disguise: an obvious
  "improvement" is a conditional edge out of `retrieve` that routes METADATA to
  `describe` and everything else to `clarify`. **Do not do it in this phase.**
  A skipped node still persists a `run_steps` row and still emits its
  `STEP_STARTED`/`STEP_FINISHED` pair, and an edge that routes around it emits
  neither — which breaks non-negotiable #2 and shifts every later `seq` on
  every analytical run. Same reasoning as `clarify`, which reports `SKIPPED`
  the same way when the connection has clarification off. If the trail should
  stop showing skipped nodes, that is a product decision, made on its own, with
  the SSE snapshot test updated deliberately — not a side effect of a rewiring.
- **`AnalyticsPipeline.run` keeps its exact signature** and delegates to the
  compiled graph. Nothing above the pipeline — `run_service`, the workers, the
  API — changes at all.
- The node crash handler stays: a node exception is still a run failure with an
  `E_NODE_FAILED` step, never a bare 500.

**Exit criteria:** all three SSE snapshot runs unchanged; full backend suite
green; `make guard` green; eval `sales_v1` within noise of the Phase 0 baseline
(same model, temperature 0); `lint-imports` green on all seven contracts.

### Phase 2 — One repair subgraph, two callers

The extraction is smaller than "the repair loop", because chat's repair region
and the draft's are not the same shape. The draft runs `generate ⇄ validate` and
nothing else. Chat's region also takes back-edges from `execute` and `inspect`,
and can leave `validate` *forward* to `present`. Extracting the union would drag
`execute`, `inspect` and `present` into a subgraph the draft never uses.

- Extract exactly what both callers share: a compiled subgraph over
  `generate → validate` with **three exits** — `ok`, `repair` (back to
  `generate`, budget permitting), and `give_up` (`FAILED`, or the restore
  signal). Put it in `app/pipeline/graph.py` beside the chat graph.
- The chat graph invokes it as a node and keeps the `execute → generate` and
  `inspect → generate` edges as its own, **re-entering** the subgraph. That works
  because the budget lives in state, not in the loop: `repair_count` is a derived
  property (`max(0, len(attempts) - 1)`), so a second entry counts the attempts
  that already happened without anything being threaded through.
- **`_restore_superseded` stays outside the subgraph.** It is chat-only — a draft
  has no `superseded_execution` because it never runs `inspect` — and it is the
  edge most likely to be lost in a merge, because it is the only one that jumps
  forward. Losing it means a failed structural retry costs the user a working
  answer, which is exactly what that function exists to prevent.
- **The chart ask stays outside too.** `propose_chart_intent(composed=True)` runs
  *after* the 50-row preview, and that preview is `execute_saved_sql` — a service
  call, not a node. Dragging it into the graph to make the picture tidy would put
  guarded execution inside the pipeline layer.
- `sql_draft_service.draft_sql` deletes its `for _ in range(DRAFT_MAX_REPAIRS +
  1)` loop and invokes the subgraph with a draft-shaped config. What must survive
  the merge, verbatim:

  - its **own** deadline (`DRAFT_DEADLINE_SECONDS`, checked before each
    `generate`) and its **own** ceiling (`DRAFT_MAX_REPAIRS = 1`) — these are
    deliberate differences with docstrings, not drift to be reconciled away;
  - `emit=_no_emit`, `history=[]`, and no step persistence;
  - `QuestionOutOfScopeError` and the exact `_OUT_OF_SCOPE` wording per intent —
    a block stores that string as its verdict and a user reads it;
  - `_sql_rules_for` **composing** `extra_rules` with `METRIC_SQL_RULES` rather
    than either overriding the other.
- `classify=True` (the `route` pre-step for report blocks) becomes a conditional
  entry edge rather than an `if` in the service — with the refusal still raised
  as `QuestionOutOfScopeError`, not returned as a graph state.
- Reconcile every remaining difference **deliberately and in writing** — the
  table in §1 is the checklist. Pick an answer per row and record it in the
  commit, rather than letting the merge decide silently.

**Exit criteria:** one repair implementation in the codebase, provable by grep;
`test_query_service.py`, `test_report_guard.py` and `test_sql_drafts.py` green;
a new test asserting chat and draft produce the same SQL for the same question,
connection and seed; a test that the tile draft still passes **no** policy to
`propose_chart_intent`; tile creation and report-block `/check` unchanged from
the UI's perspective.

### Phase 3 — Report generation as a graph

- New `app/workers/report_graph.py`. **Not** `app/reports/` — that package is
  self-contained by contract and may not import the pipeline or infra.
- Nodes: `check_disclosure → resolve_outline → execute_blocks → write_results →
  narrate_section (loop) → summarise → finish`.
- **`check_disclosure` is a node, and it is first.** `assert_wide_enough` runs at
  the start of every generation *and* every retry, and it fails the run with the
  policy error. It is not a precondition to hoist into the caller: a policy
  tightened between creation and generation has to stop the run from inside.
- **`_touch(progress_current, phase)` is this graph's `on_step`.** The poll
  response *is* the progressive render, so those writes belong to the adapter,
  exactly as `on_step` does in the chat graph — not scattered through the nodes.
- Narration stays **sequential**, with the loop expressed as a conditional edge
  back into `narrate_section` while sections remain. Each iteration still passes
  `established` prose forward, and `other_headings` still comes from the
  headings computed once before the loop. Resist the urge to `Send` these in
  parallel; the document quality depends on the order.
- **The executive summary is not just "last".** It is skipped inside the loop,
  written after it, and then placed at *its own* position — usually first. Keep
  that as an explicit edge, not as an ordering accident.
- **`retry_section` becomes a second entry point into the same graph**, not a
  direct call on one node. It runs the section's blocks *and* its paragraph:
  `check_disclosure → resolve_outline → clear_section → execute_blocks(mine) →
  write_results → (summarise | narrate_section) → rederive`. A summary section
  can itself be retried, which is why the branch exists. This is the report-side
  half of the duplication Phase 2 fixes on the dashboard side, and collapsing it
  is half the reason to do this phase.
- **Two things must keep differing between the two entries**, and a test should
  say so: a retry's `established` is the *whole* document minus the summary
  (including sections written after the one being retried), and a retry does
  **not** rewrite the executive summary.
- The `cancelled: asyncio.Event` check between phases becomes a graph-level check
  in the same places, so a cancel still keeps the results already paid for. The
  hard cancel behind it — `task.cancel()` after `llm_request_timeout_seconds + 5`
  — still lands mid-node; decide what that leaves behind before Phase 4 makes it
  a checkpoint.
- Status stays **derived** from section rows (`SUCCEEDED | PARTIAL | FAILED`).
  Do not let the graph become the source of truth for run status — progressive
  rendering and per-section retry depend on the current derivation.

**Exit criteria:** a report generated before and after produces the same
document (same sections, same block results, same `title_snapshot`, same status
derivation); the progressive poll response still updates as each result lands;
per-section retry still turns `PARTIAL` into `SUCCEEDED`; retrying a section
still leaves the summary alone; a cancelled run still keeps its written
sections.

### Phase 4 — Checkpointing, which is the actual payoff

Everything before this is a refactor. This is the first phase that gives a user
something they did not have.

- Add `langgraph-checkpoint-postgres`. **Decide the driver question first:**
  either accept a second `psycopg` pool alongside asyncpg, or write a
  `BaseCheckpointSaver` over the existing SQLAlchemy session. The second is more
  work and fewer moving parts in production. Pick one, write down why.
- Checkpoint **report runs** first — they are minutes long, so a crash costs
  real money and real time. Thread ID is the `report_run` UUID; a retry is a
  second invocation against that same thread, so decide whether it resumes the
  thread or opens a new one *before* writing the resume path.
- Chat runs: measure before deciding. At 5–60 seconds, the existing `runs` table
  plus heartbeat plus reconciler may already be enough, and a checkpoint write
  per node is not free — **especially now.** `RunState` carries the retrieved
  schema block (`context.tables`), a full `ExecutionResult`, a *second* full
  result set while a check-driven retry is in flight (`superseded_execution`), a
  compiled Vega-Lite spec (`chart`) and a `kpi`. A checkpoint per node writes
  most of that ten times per run. Measure the row size on a real question against
  the sales fixture before turning this on.
- `RunState` must round-trip through the checkpointer. Audit it for anything that
  does not serialise cleanly — and note `repair_count` and `last_attempt` are
  derived properties, so they must not be persisted as fields and then diverge.

**Exit criteria:** kill the API process mid-report, restart it, and the run
resumes from the last completed section instead of being swept to `FAILED` by
`sweep_report_runs`. A test proves it.

### Phase 5 — Durable clarification (optional, and a product change)

This one is **not a refactor** — it changes behaviour a user can see. Do it
deliberately or not at all.

Today `clarify` ends the run `NEEDS_CLARIFICATION`, and the user's reply arrives
as an ordinary new run with `_compose_question` rebuilding the exchange. With a
checkpointer, `interrupt()` makes it one paused run that resumes on reply.

Real gains: `_compose_question` and `_pending_clarification` disappear, and the
"at most once per exchange" rule becomes structural rather than enforced by
inspecting the previous run.

Real costs: `NEEDS_CLARIFICATION` stops being a run state and becomes a paused
thread, which the reconciler, the cancel path and the SPA all currently reason
about. An abandoned clarification becomes a checkpoint that lives forever unless
something reaps it.

> [pipeline.md §6](pipeline.md) calls this "the one thing worth migrating for".
> That line predates the discovery of the second executor on the draft path, and
> this record disagrees with it: #8 is the strongest case and Phase 4 is the
> payoff. Clarification is a real upgrade, but it is the one phase that can be
> skipped entirely without leaving the codebase worse.

**Exit criteria:** a clarify round-trip is one run; the reconciler leaves paused
threads alone; abandoned threads are reaped on a schedule; `clarify_enabled=false`
is still byte-identical to the pre-feature pipeline.

### Phase 6 — Cross-replica execution

Fold into the production-readiness work rather than doing it as a LangGraph
phase. Once runs are checkpointed they are resumable by *any* process, which is
what makes a shared queue worth having: a `SELECT … FOR UPDATE SKIP LOCKED`
claim over `runs`, a Redis-backed event bus so a browser on replica B sees a run
on replica A, and cancellation as a durable flag rather than a local task handle
(`ReportRunExecutor._flags` is a dict in one process).

LangGraph does not solve those three on its own — but Phase 4 is what makes
solving them possible.

---

## 5. How each phase is proved

In order of what each catches:

1. **The SSE snapshot tests** (Phase 0, three runs) — catch any change to the
   event contract, which is the failure the SPA would show a user.
2. **`make guard`** — catches a guard regression. Non-negotiable #3.
3. **`make test`** — the full suite, including the three hostile-corpus replays
   and `test_sql_drafts.py`, which is now where the draft path's own semantics
   (deadline, classify refusal, the composed chart ask, `tile_type` rules) are
   pinned.
4. **`lint-imports`** — catches a layering violation, including LangGraph leaking
   into a self-contained package.
5. **`sales_v1` on the eval harness** — catches a behavioural regression the unit
   tests cannot see. Same model, temperature 0, compared against the Phase 0 run.
   Prompts are unchanged, so **any** accuracy movement beyond noise means the
   orchestrator changed something it should not have.

A phase is done when all five pass, not when the code runs.

---

## 6. Checklist

### Phase 0 — Groundwork
- [x] `langgraph>=1.2,<2` added to `[project.dependencies]` (resolves to 1.2.11)
- [x] Seventh `import-linter` contract confining `langgraph` / `langchain_core`,
      **with `allow_indirect_imports = true`**, added and passing
- [x] CI grep: `import langgraph` outside `app/pipeline/` and `app/workers/` fails the build
- [ ] **Eval baseline captured** (`eval_run` UUID, accuracy, companion metrics, model, temperature 0) and recorded here
      → **outstanding.** The slot and the protocol are
      [`backend/app/eval/reports/langgraph_phase0_baseline.md`](../backend/app/eval/reports/langgraph_phase0_baseline.md);
      the harness calls a real provider and costs real money, so it needs an
      account. **Phase 2 must not start until this is filled in** — until it is,
      Phase 1 has four of its five gates, not five.
- [x] Baseline note records the prompt version as the **constant** (`v7`), not `runs.prompt_version`
- [x] SSE snapshot tests written and passing against the current pipeline, for
      **three** runs: analytical, METADATA (the `describe` halt), and a failed
      check-driven retry (the `_restore_superseded` forward jump)
      → [`tests/unit/test_pipeline_events.py`](../backend/tests/unit/test_pipeline_events.py).
      **Five runs, not three:** the other two walk `validate → generate` and
      `execute → generate` / `execute → present`, so that all five non-linear
      edges have a snapshot before Phase 1 rewires them.
- [x] Those tests assert artifact and text events too — including
      `ARTIFACT_CREATED {"kind": "KPI"}` on a single-row result, and the
      `CHART` branch on a multi-row one
- [x] `run_steps` rows asserted by the same tests — both the settled row per
      `seq` and the RUNNING-then-terminal pair `_record_step` upserts

### Phase 1 — Chat pipeline
- [x] `app/pipeline/graph.py` created; state carries `RunState` whole
      (`GraphState = TypedDict("run": RunState)`, no per-field reducers)
- [x] `NodeDeps` passed via `config["configurable"]`, never in state — with
      `on_step` and the per-run `seq` counter beside it, for the same reason:
      the graph is compiled once and shared, so nothing run-specific may be
      closed over by the adapter
- [x] Node adapter owns the deadline check, timing, `on_step` persistence, and both `emit` calls
- [x] All ten nodes wired; `ORDER` replaced by edges (it survives as the
      linear-successor table `_next` reads, and as what `test_clarify.py` asserts on)
- [x] **All five** non-linear edges wired: three repairs into `generate`, two
      restores forward to `present` — pinned structurally in
      [`tests/unit/test_pipeline_graph.py`](../backend/tests/unit/test_pipeline_graph.py)
      and behaviourally in the event snapshots
- [x] `HALT` and `FAILED` route to `END`
- [x] `_MAX_TRANSITIONS` is `recursion_limit` **and** `GraphRecursionError` is
      caught and written as `E_PIPELINE_LOOP` — never a 500. Same ceiling, and
      measured: 25 node executions under both executors
- [x] `RunTimeoutError` still raised *before* a node, never inside one
- [x] `describe` and `clarify` are still **nodes that report `SKIPPED`**, not
      conditional edges that route around them — a skipped node still writes a
      `run_steps` row and emits its event pair
- [x] Node crash still becomes an `E_NODE_FAILED` step, not a 500
- [x] `AnalyticsPipeline.run` signature unchanged; nothing above the pipeline touched
      — `run_service`, `app.eval.runner` and `app.pipeline.__init__` still
      import it from `app.pipeline.pipeline`, which is now a facade over `graph.py`
- [x] `present` and `describe` still stream through `deps.emit`, not through
      LangGraph streaming — the graph is driven with `ainvoke`, and no node
      was modified
- [x] All three SSE snapshot runs unchanged (all five, and the two non-node
      stop paths) — the same test file passes against both executors
- [x] `make test` (1196 passed), `make guard`, `lint-imports` (7 contracts) green
- [ ] Eval `sales_v1` within noise of the Phase 0 baseline → **blocked on the
      Phase 0 baseline above.** Nothing to compare against yet.

### Phase 2 — The shared repair subgraph
- [ ] `generate → validate` extracted as a compiled subgraph with three exits
- [ ] Compiled **once at module scope**, not per request
- [ ] Chat graph uses it, and owns the `execute`/`inspect` back-edges by re-entry
- [ ] `_restore_superseded`'s forward jump left outside the subgraph and still working
- [ ] `propose_chart_intent(composed=True)` left outside the graph, still after the preview
- [ ] `draft_sql`'s hand-rolled `for` loop deleted
- [ ] The draft keeps its own deadline (`DRAFT_DEADLINE_SECONDS`) and ceiling (`DRAFT_MAX_REPAIRS`)
- [ ] The draft still passes **no** policy to `propose_chart_intent` (a test says so)
- [ ] `_sql_rules_for` still **composes** `extra_rules` with `METRIC_SQL_RULES`
- [ ] `classify=True` is a conditional entry edge, not an `if` in the service, and
      still raises `QuestionOutOfScopeError` with the `_OUT_OF_SCOPE` wording
- [ ] Every remaining row of the §1 divergence table resolved and recorded in the commit message
- [ ] New test: chat and draft produce the same SQL for the same question
- [ ] `test_query_service.py`, `test_report_guard.py`, `test_sql_drafts.py` green
- [ ] Tile creation and report-block `/check` unchanged from the UI
- [ ] Only one repair implementation remains (provable by grep)

### Phase 3 — Report generation
- [ ] `app/workers/report_graph.py` created (**not** in `app/reports/`)
- [ ] Nodes: check disclosure → resolve outline → execute blocks → write results
      → narrate section (loop) → summarise → finish
- [ ] `assert_wide_enough` is the first node and still fails the run
- [ ] Progress writes (`_touch`) owned by the adapter, like `on_step`
- [ ] Narration still **sequential**; `established` and `other_headings` still threaded forward
- [ ] Executive summary still skipped in the loop, written last, placed at its own position
- [ ] `retry_section` is a **second entry point into the same graph** — queries then paragraph
- [ ] A retry still reads the whole document (minus the summary) as `established`
- [ ] A retry still does **not** rewrite the executive summary
- [ ] Cancel check preserved between phases; written results still kept
- [ ] Run status still **derived** from section rows
- [ ] Before/after documents identical, `title_snapshot` included
- [ ] Progressive poll still updates as each result lands
- [ ] Per-section retry still turns `PARTIAL` into `SUCCEEDED`

### Phase 4 — Checkpointing
- [ ] Driver decision made and written down (second psycopg pool vs. custom SQLAlchemy saver)
- [ ] `RunState` audited for checkpoint round-trip, derived properties included
- [ ] Checkpoint row size measured on a real chat run before enabling it there
- [ ] Report runs checkpointed, keyed by `report_run` UUID
- [ ] Retry-vs-resume thread semantics decided and written down
- [ ] Crash test: kill mid-report, restart, run resumes from the last completed section
- [ ] Chat-run checkpointing decided on measurement, not assumption
- [ ] `sweep_report_runs` updated so a resumable run is not swept to `FAILED`

### Phase 5 — Durable clarification (optional)
- [ ] Explicit go/no-go decision recorded — this changes user-visible behaviour
- [ ] `interrupt()` replaces the end-run-and-recompose design
- [ ] `_compose_question` and `_pending_clarification` removed
- [ ] Reconciler leaves paused threads alone
- [ ] Abandoned threads reaped on a schedule
- [ ] `clarify_enabled=false` still byte-identical to the pre-feature pipeline

### Phase 6 — Cross-replica
- [ ] Redis-backed `EventPublisher` adapter
- [ ] `SELECT … FOR UPDATE SKIP LOCKED` claim over `runs`
- [ ] Cancellation as a durable flag, not a local task handle (`ReportRunExecutor._flags`)
- [ ] `event_bus.forget()` actually called on run completion
- [ ] Reconciler holds an advisory lock
- [ ] Two replicas behind a load balancer: streaming and cancel both verified

### Explicitly not migrating
- [ ] Confirmed: follow-up suggestions (`run_service.suggest_followups`) stay a one-shot call
- [ ] Confirmed: `reports/outline.propose` stays a one-shot call
- [ ] Confirmed: `semantic/generator.py` stays `asyncio.gather` + semaphore
- [ ] Confirmed: the capability probe stays a plain probe
- [ ] `PROMPT_VERSION` never moved during the migration (still `v7`)
- [ ] The call-site inventory never grew: still twelve use cases across fourteen sites
