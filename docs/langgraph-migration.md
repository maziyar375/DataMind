# Migrating to LangGraph

A decision record and a phased plan. The decision half comes first because the
plan's scope depends on it: **three of the six LLM surfaces move, three stay**,
and moving the other three would be churn.

Written against LangGraph **1.2.10** (`langgraph-checkpoint` 4.2.0,
`langgraph-checkpoint-postgres` 3.1.2).

Companion to [pipeline.md](pipeline.md) (what is being migrated),
[architecture.md](architecture.md) (which deferred LangGraph, and on what
triggers) and [eval.md](eval.md) (which is how each phase is proved safe).

---

## 1. Every LLM call site, and the verdict on each

| # | Surface | Call site | Orchestration today | Migrate? |
| --- | --- | --- | --- | :--: |
| 1 | Chat | `nodes.route` | pipeline node | **Yes** |
| 2 | Chat | `nodes.clarify` | pipeline node | **Yes** |
| 3 | Chat | `nodes.generate` | pipeline node, inside the repair loop | **Yes** |
| 4 | Chat | `nodes.present` | pipeline node, streamed | **Yes** |
| 5 | Chat | `nodes.chart` | pipeline node | **Yes** |
| 6 | Chat | `run_service._suggestions` | one-shot, fire-and-forget | No |
| 7 | Dashboard | `sql_draft_service.draft_sql` | **its own hand-rolled loop over the chat nodes** | **Yes** |
| 8 | Report | `reports/outline.propose` | one-shot | No |
| 9 | Report | per-block SQL | `draft_sql(classify=True, extra_rules=…)` | **Yes**, via #7 |
| 10 | Report | `workers/report._narrate` | sequential `for` loop in the worker | **Yes** |
| 11 | Report | `workers/report._summarise` | after that loop | **Yes**, via #10 |
| 12 | Semantic | `semantic/generator.py` | `asyncio.gather` + semaphore, 4 concurrent | No |
| 13 | Platform | `llm_configs.probe` | capability probe | No |

### Why the three that move, move

**The chat pipeline (1–5).** This is the only thing in the codebase already
shaped like a graph: a linear order with a bounded `goto` back to `generate`,
two nodes that can halt the run, and a hand-maintained `_MAX_TRANSITIONS` guard
against a cycle. `pipeline.py` is a `while` loop doing index arithmetic over
`ORDER`, with `next(i for i, (n, _) in enumerate(ORDER) if n == result.goto)` to
resolve a jump. That is an edge list written as a list search. The node
signature `(state, deps) -> NodeResult` was deliberately built LangGraph-shaped,
so this is the wiring change the architecture doc predicted.

**The dashboard draft path (7) — the strongest case, and an unplanned one.**
`sql_draft_service.draft_sql` imports `route`, `retrieve`, `generate` and
`validate` from `app.pipeline.nodes` and drives them with its own loop:

```python
await retrieve(state, deps)
for _ in range(DRAFT_MAX_REPAIRS + 1):
    result = await generate(state, deps)
    if result.status == "FAILED":
        raise LLMError(...)
    if (await validate(state, deps)).goto != "generate":
        break
```

There are **two executors over one node set**. They already disagree in ways
nobody chose: the draft loop has its own repair ceiling, no deadline, no
transition cap, no step persistence, and no event emission. Every future change
to repair semantics has to be made twice or it silently diverges. A compiled
subgraph invoked by both callers is the fix, and it is worth doing even if
nothing else on this list happened.

Because a report block's SQL is `draft_sql` with `classify=True` and
`extra_rules` set, migrating #7 migrates #9 for free.

**Report generation (10, 11).** This is the one place where LangGraph's
*durability* argument is real rather than theoretical. A report run is minutes
long, sections are narrated sequentially, each result is committed as it lands,
and a process death fails the whole run — the written sections survive, but the
run cannot continue. Cancellation is an in-process `asyncio.Event`, which does
not survive a restart and does not exist on another replica.

Note what the argument is *not*: sections are deliberately sequential, because
each one receives `established=list(prose)` so section five can contrast with
section two instead of restating it. **Do not parallelise them.** Fan-out is not
the reason to migrate this; resume-after-crash is.

### Why the three that stay, stay

**Follow-up suggestions (6)** — one prompt, one completion, no state, fails open
and returns `[]`. A graph adds a compile step and buys nothing.

**Report outline (8)** — one `complete` call, deliberately not even `structured`
(the docstring explains why). One node is not a graph.

**Semantic generation (12)** — `asyncio.gather` with a semaphore over
independent per-table calls, then a merge. That is map/reduce, and LangGraph's
`Send` API would express the same thing with more machinery and a new failure
mode. It is already the cleanest concurrency in the codebase; leave it.

**The capability probe (13)** — a health check that happens to call a model.

> Migrating these three anyway would mean touching working code with no test
> that could tell you it got better. If a future feature turns one of them into
> a real graph — say, an outline that iterates with the user — revisit that one
> then.

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
- **Streaming needs care.** `present` streams tokens through `deps.emit` today.
  LangGraph has its own streaming model; the migration must not route prose
  through it, or the SSE contract changes underneath the SPA.
- **`NodeDeps` is not serializable.** It holds a live `DatabaseConnector` and an
  `emit` callable. It cannot live in checkpointed state and must travel through
  the graph's runtime config instead.

---

## 3. Non-negotiables

Every phase is judged against these. If a phase cannot hold all five, the phase
is wrong, not the rule.

1. **Not one prompt byte changes.** `PROMPT_VERSION` does not move during this
   migration. The whole point of the eval gate below is that it isolates the
   orchestrator as the only variable.
2. **The SSE event sequence is identical.** Same types, same `seq` numbering,
   same order, same `run_steps` rows. The live step trail is a valued feature
   and the SPA parses this contract.
3. **The guard is untouched.** Three entry points, no exemptions, and
   `test_sqlguard_hostile.py` / `test_query_service.py` / `test_report_guard.py`
   stay green throughout.
4. **The dependency rule holds.** `import-linter` passes at every phase, with a
   new contract confining `langgraph` (Phase 0).
5. **Disclosure behaviour is unchanged.** `disclose()`, `HintBudget` and
   `disclose_history()` all still filter at render time.

---

## 4. The phases

### Phase 0 — Groundwork and the safety net

No behaviour change. This phase exists so every later phase can be proved.

- Add `langgraph>=1.2,<2` to `[project.dependencies]`. Do **not** add the
  checkpointer yet — it arrives in Phase 4 with its driver.
- Add an `import-linter` contract confining it:

  ```toml
  [[tool.importlinter.contracts]]
  name = "langgraph stays in the orchestration layer"
  type = "forbidden"
  source_modules = ["app.domain", "app.sqlguard", "app.semantic", "app.reports",
                    "app.charts", "app.api"]
  forbidden_modules = ["langgraph", "langchain_core"]
  ```

  Note `app.reports` is in the source list. That is deliberate: the report graph
  built in Phase 3 goes in `app/workers/`, **not** in `app/reports/`, which is
  self-contained and sits below the pipeline.
- Add a CI grep in the same spirit as the LiteLLM one — `import langgraph`
  outside `app/pipeline/` and `app/workers/` fails the build.
- **Capture the eval baseline now.** Run `sales_v1` on a fixed model at
  temperature 0, record the `eval_run` UUID, the accuracy, and the companion
  metrics. Every later phase compares against this run, not against
  `sales_v1.baseline.json` (which was measured on a different model).
- **Write the SSE snapshot test.** Drive one run end to end with a scripted fake
  gateway and assert the full ordered list of `(seq, type, name, status)` events
  plus the `run_steps` rows. This test is the contract for non-negotiable #2 and
  it must exist before anything moves.

### Phase 1 — The chat pipeline as a compiled graph (wrap, don't rewrite)

The single most important decision in this migration: **the nine node functions
are not modified.** They keep mutating `RunState` and returning `NodeResult`. A
thin adapter turns each into a LangGraph node.

- New `app/pipeline/graph.py`. State schema is a `TypedDict` with one key,
  `run: RunState` — the existing model carried whole rather than decomposed into
  per-field reducers. Mutation-in-place keeps working; the adapter returns the
  mutated object as the update.
- `NodeDeps` travels in `config["configurable"]`, never in state.
- The adapter owns what `pipeline.py` owns today: timing, the `on_step`
  persistence call, and the two `emit` calls. That is how the event sequence
  stays byte-identical.
- Sketch:

  ```python
  def _adapt(name: str, fn: NodeFn):
      async def node(state: GraphState, config: RunnableConfig) -> Command:
          deps = config["configurable"]["deps"]
          # …emit STEP_STARTED, time it, call fn, persist the run_step,
          #    emit STEP_FINISHED — exactly as pipeline.py does now…
          result = await fn(state["run"], deps)
          goto = END if result.status in ("HALT", "FAILED") else (result.goto or _next(name))
          return Command(goto=goto, update={"run": state["run"]})
      return node
  ```

- `ORDER` becomes `add_edge` calls; the repair edge becomes the `Command(goto=…)`
  above; `_MAX_TRANSITIONS = 24` becomes `recursion_limit` in the invoke config.
- **`AnalyticsPipeline.run` keeps its exact signature** and delegates to the
  compiled graph. Nothing above the pipeline — `run_service`, the workers, the
  API — changes at all.
- The node crash handler stays: a node exception is still a run failure with an
  `E_NODE_FAILED` step, never a bare 500.

**Exit criteria:** SSE snapshot test unchanged; full backend suite green;
`make guard` green; eval `sales_v1` within noise of the Phase 0 baseline (same
model, temperature 0); `lint-imports` green.

### Phase 2 — One repair subgraph, two callers

- Extract `generate ⇄ validate` into a compiled subgraph in
  `app/pipeline/graph.py`.
- The chat graph uses it. `sql_draft_service.draft_sql` deletes its
  `for _ in range(DRAFT_MAX_REPAIRS + 1)` loop and invokes the same subgraph with
  a draft-shaped config (no emit, its own repair ceiling as a config value, still
  no conversation history).
- Reconcile the differences the two loops accumulated **deliberately and in
  writing**: does a draft get a deadline now? A transition cap? Pick an answer
  per difference and record it in the commit, rather than letting the merge
  decide silently.
- `classify=True` (the `route` pre-step for report blocks) becomes a conditional
  entry edge rather than an `if` in the service.

**Exit criteria:** one repair implementation in the codebase, provable by grep;
`test_query_service.py` and `test_report_guard.py` green; a new test asserting
chat and draft produce the same SQL for the same question, connection and seed;
tile creation and report-block `/check` unchanged from the UI's perspective.

### Phase 3 — Report generation as a graph

- New `app/workers/report_graph.py`. **Not** `app/reports/` — that package is
  self-contained by contract and may not import the pipeline or infra.
- Nodes: `resolve_outline → execute_blocks → narrate_section (loop) →
  summarise → finish`.
- Narration stays **sequential**, with the loop expressed as a conditional edge
  back into `narrate_section` while sections remain. Each iteration still passes
  `established` prose forward. Resist the urge to `Send` these in parallel; the
  document quality depends on the order.
- The `cancelled: asyncio.Event` check between sections becomes a graph-level
  check in the same place, so a cancel still keeps the results already paid for.
- Status stays **derived** from section rows (`SUCCEEDED | PARTIAL | FAILED`).
  Do not let the graph become the source of truth for run status — progressive
  rendering and per-section retry depend on the current derivation.
- `retry_section` invokes the `narrate_section` node directly, not the whole
  graph.

**Exit criteria:** a report generated before and after produces the same
document (same sections, same block results, same status derivation); the
progressive poll response still updates as each result lands; per-section retry
still turns `PARTIAL` into `SUCCEEDED`; a cancelled run still keeps its written
sections.

### Phase 4 — Checkpointing, which is the actual payoff

Everything before this is a refactor. This is the first phase that gives a user
something they did not have.

- Add `langgraph-checkpoint-postgres`. **Decide the driver question first:**
  either accept a second `psycopg` pool alongside asyncpg, or write a
  `BaseCheckpointSaver` over the existing SQLAlchemy session. The second is more
  work and fewer moving parts in production. Pick one, write down why.
- Checkpoint **report runs** first — they are minutes long, so a crash costs
  real money and real time. Thread ID is the `report_run` UUID.
- Chat runs: measure before deciding. At 5–60 seconds, the existing
  `runs` table plus heartbeat plus reconciler may already be enough, and a
  checkpoint write per node is not free.
- `RunState` must round-trip through the checkpointer. Audit it for anything
  that does not serialise cleanly before turning this on.

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

**Exit criteria:** a clarify round-trip is one run; the reconciler leaves paused
threads alone; abandoned threads are reaped on a schedule; `clarify_enabled=false`
is still byte-identical to the pre-feature pipeline.

### Phase 6 — Cross-replica execution

Fold into the production-readiness work rather than doing it as a LangGraph
phase. Once runs are checkpointed they are resumable by *any* process, which is
what makes a shared queue worth having: a `SELECT … FOR UPDATE SKIP LOCKED`
claim over `runs`, a Redis-backed event bus so a browser on replica B sees a run
on replica A, and cancellation as a durable flag rather than a local task handle.

LangGraph does not solve those three on its own — but Phase 4 is what makes
solving them possible.

---

## 5. How each phase is proved

In order of what each catches:

1. **The SSE snapshot test** (Phase 0) — catches any change to the event
   contract, which is the failure the SPA would show a user.
2. **`make guard`** — catches a guard regression. Non-negotiable #3.
3. **`make test`** — the full suite, including the three hostile-corpus replays.
4. **`lint-imports`** — catches a layering violation, including LangGraph
   leaking into a self-contained package.
5. **`sales_v1` on the eval harness** — catches a behavioural regression the
   unit tests cannot see. Same model, temperature 0, compared against the Phase 0
   run. Prompts are unchanged, so **any** accuracy movement beyond noise means
   the orchestrator changed something it should not have.

A phase is done when all five pass, not when the code runs.

---

## 6. Checklist

### Phase 0 — Groundwork
- [ ] `langgraph>=1.2,<2` added to `[project.dependencies]`
- [ ] `import-linter` contract confining `langgraph` / `langchain_core` added and passing
- [ ] CI grep: `import langgraph` outside `app/pipeline/` and `app/workers/` fails the build
- [ ] Eval baseline captured (`eval_run` UUID, accuracy, companion metrics, model, temperature 0) and recorded here
- [ ] SSE snapshot test written and passing against the current pipeline
- [ ] `run_steps` rows asserted by that same test

### Phase 1 — Chat pipeline
- [ ] `app/pipeline/graph.py` created; state carries `RunState` whole
- [ ] `NodeDeps` passed via `config["configurable"]`, never in state
- [ ] Node adapter owns timing, `on_step` persistence, and both `emit` calls
- [ ] All nine nodes wired; `ORDER` replaced by edges
- [ ] Repair edge is `Command(goto="generate")`; `_MAX_TRANSITIONS` is `recursion_limit`
- [ ] `HALT` and `FAILED` route to `END`
- [ ] Node crash still becomes an `E_NODE_FAILED` step, not a 500
- [ ] `AnalyticsPipeline.run` signature unchanged; nothing above the pipeline touched
- [ ] `present` still streams through `deps.emit`, not through LangGraph streaming
- [ ] SSE snapshot test unchanged
- [ ] `make test`, `make guard`, `lint-imports` green
- [ ] Eval `sales_v1` within noise of the Phase 0 baseline

### Phase 2 — The shared repair subgraph
- [ ] `generate ⇄ validate` extracted as a compiled subgraph
- [ ] Chat graph uses it
- [ ] `draft_sql`'s hand-rolled `for` loop deleted
- [ ] Every deliberate difference between the two loops (deadline, repair ceiling, transition cap, step persistence) resolved and recorded in the commit message
- [ ] `classify=True` is a conditional entry edge, not an `if` in the service
- [ ] New test: chat and draft produce the same SQL for the same question
- [ ] `test_query_service.py` and `test_report_guard.py` green
- [ ] Tile creation and report-block `/check` unchanged from the UI
- [ ] Only one repair implementation remains (provable by grep)

### Phase 3 — Report generation
- [ ] `app/workers/report_graph.py` created (**not** in `app/reports/`)
- [ ] Nodes: resolve outline → execute blocks → narrate section (loop) → summarise → finish
- [ ] Narration still **sequential**; `established` prose still threaded forward
- [ ] Cancel check preserved between sections; written results still kept
- [ ] Run status still **derived** from section rows
- [ ] `retry_section` invokes the narrate node directly
- [ ] Before/after documents identical
- [ ] Progressive poll still updates as each result lands
- [ ] Per-section retry still turns `PARTIAL` into `SUCCEEDED`

### Phase 4 — Checkpointing
- [ ] Driver decision made and written down (second psycopg pool vs. custom SQLAlchemy saver)
- [ ] `RunState` audited for checkpoint round-trip
- [ ] Report runs checkpointed, keyed by `report_run` UUID
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
- [ ] Cancellation as a durable flag, not a local task handle
- [ ] `event_bus.forget()` actually called on run completion
- [ ] Reconciler holds an advisory lock
- [ ] Two replicas behind a load balancer: streaming and cancel both verified

### Explicitly not migrating
- [ ] Confirmed: follow-up suggestions (`run_service`) stay a one-shot call
- [ ] Confirmed: `reports/outline.propose` stays a one-shot call
- [ ] Confirmed: `semantic/generator.py` stays `asyncio.gather` + semaphore
- [ ] Confirmed: `llm_configs.probe` stays a plain probe
- [ ] `PROMPT_VERSION` never moved during the migration
