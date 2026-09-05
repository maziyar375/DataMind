# Token accounting — closing the `structured()` / `stream()` gap

**Status:** proposed, not built. Written 2026-09-05.

A plan to make the token numbers this product already records *true*, and to
make them attributable — per node, per operation, and per user.

Read [docs/pipeline.md](pipeline.md) §0 for the call-site map this plan touches
and [docs/security.md](security.md) before changing anything near a gateway
call site.

---

## 0. Why this exists

`runs.prompt_tokens` and `runs.completion_tokens` have been in the schema since
migration `0001`. They are populated. **They are also wrong**, and the reason is
one line of type signature:

```python
async def structured(self, llm, messages, schema, *, on_reasoning=None) -> T: ...
```

`structured()` returns the validated pydantic model and nothing else. The
`response.usage` object that came back beside it — prompt tokens, completion
tokens — is read by no one and falls out of scope. `stream()` has the same
shape: it yields `StreamChunk` text and reasoning, never a usage record.

`complete()` *does* return usage, in a `Completion` dataclass that has carried
`prompt_tokens`, `completion_tokens` and `latency_ms` all along. So the product
has one honest call site and ten that silently report zero.

**The eleven chat/prose call sites, and what each records today:**

| Call site | Method | Usage recorded? |
|---|---|---|
| [nodes/__init__.py:150](../backend/app/pipeline/nodes/__init__.py#L150) `route` | `complete` | **yes** — the only one |
| [nodes/__init__.py:690](../backend/app/pipeline/nodes/__init__.py#L690) `describe` | `stream` | no |
| [nodes/__init__.py:788](../backend/app/pipeline/nodes/__init__.py#L788) `clarify` | `structured` | no |
| [nodes/__init__.py:953](../backend/app/pipeline/nodes/__init__.py#L953) `generate` | `structured` | no |
| [nodes/__init__.py:1315](../backend/app/pipeline/nodes/__init__.py#L1315) `present` | `stream` | no |
| [nodes/__init__.py:1399](../backend/app/pipeline/nodes/__init__.py#L1399) `chart` | `structured` | no |
| [semantic/generator.py:400](../backend/app/semantic/generator.py#L400) | `structured` | no |
| [semantic/generator.py:435](../backend/app/semantic/generator.py#L435) | `structured` | no |
| [semantic/generator.py:474](../backend/app/semantic/generator.py#L474) | `structured` | no |
| [reports/outline.py:203](../backend/app/reports/outline.py#L203) | `complete` | no — returns a `Completion`, reads `.text` only |
| [workers/report.py:743](../backend/app/workers/report.py#L743) section narration | `complete` | no — same |
| [workers/report.py:824](../backend/app/workers/report.py#L824) executive summary | `complete` | no — same |
| [services/run_service.py:1113](../backend/app/services/run_service.py#L1113) follow-up suggestions | `complete` | no — same |

So the gap is wider than "two methods discard usage". Four `complete()` callers
receive accurate token counts in hand and drop them, because nothing downstream
of them has anywhere to put the number.

**What that costs, concretely.** A chat run records the tokens of its cheapest
call (a one-word routing classification) and none of the expensive ones — the
schema block, the SQL generation, the prose. A **report generation**, the most
expensive operation in the product (one call per section plus an outline plus a
summary, `report_narration_concurrency` of them at a time), records **zero
tokens anywhere**. `semantic_jobs` — one model call per table, four
concurrently, across a 42-table schema — likewise records zero.

The eval harness is the one place this works properly: it reads
`state.prompt_tokens` and calls `estimate_cost_usd` ([runner.py:237-240](../backend/app/eval/runner.py#L237-L240)).
It works there because the eval runs the chat pipeline, where `route` happens to
be instrumented. It is measuring one call out of five and calling it a run.

### What this plan does not do

No new dependency, no new service, no metrics/tracing stack. Langfuse, Prometheus
and LiteLLM's proxy mode were all considered and are all deferred: this is a
correctness fix to numbers the schema already claims to hold, and it should not
arrive bundled with new infrastructure. If cross-run dashboards are wanted later,
they are wanted *on top of* accurate rows, and this is what makes the rows
accurate.

---

## 1. Design decisions

### 1.1 Usage travels by sink, not by return type

`structured()` grows an optional `on_usage` callback, exactly mirroring the
`on_reasoning` sink already on it:

```python
UsageSink = Callable[[Usage], None]

async def structured(
    self, llm, messages, schema, *,
    on_reasoning: ReasoningSink | None = None,
    on_usage: UsageSink | None = None,      # NEW
) -> T: ...

def stream(
    self, llm, messages, *,
    on_usage: UsageSink | None = None,      # NEW
) -> AsyncIterator[StreamChunk]: ...
```

**Why a sink and not a `(T, Usage)` tuple.** The tuple changes the return type
for all six `structured()` callers, including the three in `semantic/generator.py`
that have no use for the number and would each have to unpack and discard it. The
sink leaves every uninterested call site byte-identical and puts the change only
where somebody wants the data. It also matches the precedent the module already
set: `on_reasoning` is the same shape, for the same reason.

**Why not accumulate inside the gateway instance.** A per-instance counter is
fewest edits and wrong: one `LiteLLMGateway` is shared across the report worker's
concurrent narration waves ([workers/report.py](../backend/app/workers/report.py),
`report_narration_concurrency` default 4), so four sections' counts would
interleave into one bucket with no way to separate them. The sink is called with
one call's usage, synchronously, by the coroutine that made it — concurrency-safe
by construction.

**`Usage` is a new frozen dataclass in `domain/ports/llm.py`**, beside
`Completion`:

```python
@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model: str = ""          # for costing; the resolved name, post-prefix
```

`model` rides along because `estimate_cost_usd` needs it and the sink's receiver
(a node, a worker) should not have to reach back into `ResolvedLLM` to cost a
call it did not make.

### 1.2 `complete()` keeps its return type

`Completion` already carries everything `Usage` does. It does not change. The four
`complete()` call sites that drop their numbers are fixed by *reading* what they
are already handed and passing it on — not by changing the method. `Completion`
grows one convenience:

```python
def usage(self) -> Usage: ...
```

so a caller feeds a `Usage` to the same recorder the sink feeds, and there is one
accumulation path rather than two.

### 1.3 Streaming usage is best-effort, and says so

A streamed response carries usage only if the provider sends a final usage chunk
(OpenAI does with `stream_options={"include_usage": True}`; others vary, and some
OpenAI-compatible gateways send nothing). The plan:

- request it where supported, read it if it arrives, record zero if it does not;
- **never estimate.** A tokenizer-based guess is a number that looks like a
  measurement and is not one, and it would sit in the same column as measured
  values with nothing to distinguish them.

`stream()` is therefore the one method where a zero can mean "the provider did not
say" rather than "no tokens were spent". §4 records how that is made visible
rather than silently averaged in.

### 1.4 Failing to record never fails the work

The posture is `services/audit.py`'s, and for the same stated reason: *this
observes, it does not authorise*. A sink that raises must not lose a report
section that was successfully written. Every sink invocation is wrapped; a failure
warns and continues. This is the deliberate opposite of the guard's fail-closed
rule, and the distinction is the one `audit.py` already articulates.

### 1.5 Attribution: `actor_id` alongside `owner_id`

Per-user totals need a user. Today `runs.owner_id`, `report_runs.owner_id` and
`semantic_jobs.owner_id` all exist and all hold the authenticated caller —
`create_run` takes `owner_id` from `ctx.user_id` and refuses a conversation the
caller does not own ([run_service.py:160-172](../backend/app/services/run_service.py#L160-L172)),
so **owner and actor are the same person in every row that exists today.**

They will not stay that way. The platform is heading for multiple users working
on shared datasets, where the person who *owns* a connection and the person who
*asked the question* are routinely different — and billing the owner for a
colleague's question is the wrong answer.

So `actor_id` is added now, while it is trivially correct (it equals `owner_id`
for every existing row and for every row written before sharing ships), rather
than retrofitted later when a backfill would have to invent who asked. This is
the same reasoning the codebase applied to `curation_admin_only`: turn it on
before sharing exists, so the flip is not also a behaviour change.

- `actor_id` is **not nullable** and is set from `ctx.user_id` at creation.
- `owner_id` keeps its current meaning (ownership scoping) and is untouched.
- Per-user usage groups by `actor_id`. Per-resource cost groups by `owner_id`.
  Both questions become answerable, and neither answer is a guess.

### 1.6 Per-user totals are a query, not a table

There is no `user_token_usage` table and no counter. A user's usage is the sum of
the rows describing work they caused, grouped by `actor_id`. A maintained counter
would be a second answer able to disagree with the first — the same reason
embedding staleness is derived from a fingerprint rather than tracked, and report
run status is derived from its sections rather than set.

Per the decision on scope: **no read endpoint in this plan.** The numbers land in
the tables and are read with SQL. `GET /usage` is a later, separate piece of work
if it is wanted; it would follow `GET /audit`'s admin-only posture, because usage
per named user is a record about people.

---

## 2. Schema — migration `0023_token_accounting.py`

Three tables gain columns. Every column is nullable except the two `actor_id`s
that can be backfilled deterministically.

**`run_steps`** — per-node attribution, the granular level:

| Column | Type | Meaning |
|---|---|---|
| `prompt_tokens` | `Integer` | null = this step made no model call |
| `completion_tokens` | `Integer` | |
| `llm_latency_ms` | `Integer` | provider time only, distinct from the step's `duration_ms` |
| `llm_calls` | `Integer` | how many calls this step made — usually 0 or 1, but `generate` repairs |

Nullable and not defaulted to `0`, because "this node never calls a model"
(`validate`, `execute`) and "this node called one and it reported nothing" are
different facts and the column should not conflate them.

**`runs`** — one new column; the token columns already exist:

| Column | Type | Meaning |
|---|---|---|
| `cost_usd` | `Float` | best-effort, null where litellm cannot price the model |
| `actor_id` | `UUID FK users.id` | who asked. Backfilled from `owner_id` |

**`report_runs`** and **`semantic_jobs`** — currently record nothing:

| Column | Type |
|---|---|
| `prompt_tokens` | `Integer` |
| `completion_tokens` | `Integer` |
| `llm_latency_ms` | `Integer` |
| `cost_usd` | `Float` |
| `actor_id` | `UUID FK users.id`, backfilled from `owner_id` |

**FK posture:** `actor_id` is `ON DELETE SET NULL`, matching every other
reference to `users` and `llm_configs` in this schema. CLAUDE.md's rule is
explicit — deleting history to satisfy a constraint is the wrong trade — and a
usage row whose actor was deleted is still a true record of tokens spent. It is
*not* `owner_id`'s deliberate exception (that one stays non-null because
ownership filters match on it).

**Costing** uses the existing `estimate_cost_usd` in
[litellm_gateway.py:713](../backend/app/infra/llm/litellm_gateway.py#L713),
which already returns `None` for a model litellm cannot price (a local Ollama
model, say). Null cost with non-null tokens is the correct and expected state for
self-hosted deployments, and no query should treat null as zero.

**Backfill:** `UPDATE runs SET actor_id = owner_id WHERE actor_id IS NULL`, same
for the other two. Correct by §1.5. Token columns are **not** backfilled — a
historical run's true token count is unknowable, and inventing one would repeat
the `prompt_version` mistake CLAUDE.md records, where rows from a five-week window
still claim a version they never ran. Nulls stay null and mean "not measured".

---

## 3. The six phases

Ordered so **the tree is green at the end of every phase** and each is
independently reviewable and revertable. A phase never leaves a half-wired sink:
either nothing calls it yet, or everything that calls it also persists it.

### How each phase closes

Every phase ends with the same two-commit rhythm:

1. **Commit the implementation.** The work of the phase, on its own, with the
   suite passing as it stood before the phase's new tests were written.
2. **Write the phase's tests, run them, commit again.** The verification loop
   below, then a second commit carrying the tests and any fix they forced.

Two commits rather than one because the split is the useful thing to read later:
the first says what changed, the second says how we know. If the tests force a
change to the implementation, that fix belongs in the *second* commit — it is
part of what the test found, and squashing it into the first hides the finding.

**Verification loop, run at both commits of every phase:**

```bash
cd backend && make test      # ~1,790 tests, well under a minute
make lint                    # ruff + the eight import-linter contracts
```

`make guard` is **not** required in any phase — nothing here touches `sqlguard/`
or a connector. `npm` is not involved at all: this plan changes no frontend file.

**Commit message convention** follows the tree (`type(scope): a declarative
sentence`, lowercase, no trailing period). Each phase names its two below.

> **Do not push.** Commits are local; the user pushes from their own terminal.

---

### Phase 1 — the `Usage` type and the sinks

*Adds an optional parameter that nothing passes yet. Zero behaviour change.*

1. Add `Usage` and `UsageSink` to [domain/ports/llm.py](../backend/app/domain/ports/llm.py);
   add `Completion.usage()`.
2. Add `on_usage` to the `LLMGateway` Protocol's `structured` and `stream`.
3. Implement in [litellm_gateway.py](../backend/app/infra/llm/litellm_gateway.py):
   - `structured()` — read `response.usage` after `_structured_call` /
     `_structured_stream_call`, fire the sink. It fires **once per attempt**, so a
     repaired call (`STRUCTURED_REPAIRS`) reports both; that is correct, both were
     paid for.
   - `_consume_structured_stream()` — capture usage off the final chunk.
   - `stream()` — set `stream_options={"include_usage": True}` for
     OpenAI-compatible providers, read the trailing usage chunk if it comes.
   - wrap every sink call so a raising sink cannot fail the request (§1.4).

**Tests — new `tests/unit/test_token_accounting.py`:**

- a fake gateway fires `on_usage` with the counts the provider reported
- a sink that raises does not fail the call (§1.4)
- a repaired `structured()` call reports both attempts
- `stream()` with no provider usage chunk reports zero and does not estimate

**Phase gate:** every pre-existing test passes *unchanged*. This phase adds an
optional keyword argument and no caller passes it; if an existing test needed
editing, something was not additive.

```
feat(llm): usage travels back by sink, the way reasoning already does
test(llm): the usage sink fires per attempt and never fails the call
```

---

### Phase 2 — the migration

*Schema only. Nothing writes the new columns yet.*

4. Write `0023_token_accounting.py` per §2: the four `run_steps` columns,
   `runs.cost_usd`, tokens + cost on `report_runs` and `semantic_jobs`, and
   `actor_id` on all three run-ish tables.
5. Backfill `actor_id` from `owner_id` in the same migration (§1.5). Token columns
   are **not** backfilled (§2).
6. Mirror all of it in [infra/db/models.py](../backend/app/infra/db/models.py).

**Tests:**

- `alembic upgrade head` then `downgrade` then `upgrade` again, clean
- `actor_id` is non-null on every pre-existing row after the backfill
- new token columns are null, not zero, on historical rows

**Phase gate:** `make migrate` runs clean against `.data/db`, which is a **real
local database with real dashboards and connections in it** — treat a failure
here as a data problem, not a test problem.

```
feat(db): tokens, cost and an actor get columns on the three run tables
test(db): 0023 round-trips and backfills actor_id from owner_id
```

---

### Phase 3 — the chat pipeline accumulates and persists

*The first phase where a number changes. Sink → state → `run_steps` → `runs`,
end to end, so nothing is half-wired.*

7. `RunState` grows `record_usage(usage: Usage) -> None` and a per-node bucket.
   The existing `llm_latency_ms` / `prompt_tokens` / `completion_tokens` fields
   stay and become *accurate* rather than partial.
8. Rewrite `route`'s hand-rolled accumulation
   ([nodes/__init__.py:158-160](../backend/app/pipeline/nodes/__init__.py#L158-L160))
   to call `record_usage(completion.usage())` — one path, not two.
9. Pass `on_usage=state.record_usage` at the five other pipeline call sites
   (`describe`, `clarify`, `generate`, `present`, `chart`).
10. Widen the adapter's `on_step` callback in
    [pipeline/graph.py](../backend/app/pipeline/graph.py) to carry the node's
    usage bucket, and write it to the new `run_steps` columns.
    **The adapter owns this, not the nodes** — CLAUDE.md is explicit that the
    adapter owns the `seq` counter, the `run_steps` write and both `emit` calls,
    and that separation is what keeps the SSE sequence identical run after run. A
    node writing its own token row would be the first exception to that rule.
11. `run_service._finalise` ([run_service.py:687-690](../backend/app/services/run_service.py#L687-L690))
    additionally writes `cost_usd` via `estimate_cost_usd`, and `create_run` sets
    `actor_id` from `ctx.user_id`.

**Tests — extend `test_token_accounting.py`, plus:**

- **a run's total equals the sum of its `run_steps` rows** — the invariant that
  makes per-node attribution trustworthy, and the one most likely to rot
- a run now records tokens from *every* node that called a model, not just `route`
- `estimate_cost_usd` returning `None` leaves `cost_usd` null, and no path coerces
  it to `0.0`
- `tests/unit/test_pipeline_events.py` passes **unchanged** — see the gate

**Phase gate:** `test_pipeline_events.py` is the SSE-sequence contract and this
phase touches the adapter that owns emission. It must pass **as written**. If it
needs updating, the change reached further than intended — revisit rather than
edit the test.

```
feat(pipeline): every node's tokens are counted, and each step says its own
test(pipeline): a run's tokens equal the sum of its steps, and SSE is unmoved
```

---

### Phase 4 — reports and semantic jobs

*The two operations that record zero today. Independent of Phase 3 except for
the `Usage` type, so it could be reordered if reports are the priority.*

12. `outline.py` and the two `report.py` call sites: read the `Completion` they
    already receive and accumulate per run. Because narration runs in **concurrent
    waves**, accumulation must be per-call and merged at the wave's commit point —
    never a shared mutable counter (§1.1). The existing `return_exceptions=True`
    commit-what-came-back structure is where the merge belongs.
13. `semantic/generator.py`: pass `on_usage` at its three call sites; the generator
    already returns per-table stats, so usage joins that shape and lands in
    `semantic_jobs`.
14. `run_service.py:1113` (follow-up suggestions): same, onto the run.
15. Both workers set `actor_id` at job creation.

**Tests — extend `tests/unit/test_report_*.py` and the semantic suites:**

- concurrent narration waves attribute tokens to the right run — the test that
  justifies §1.1's rejection of a gateway-instance counter
- a failed section does not lose the tokens its siblings spent
- a semantic job records tokens across all its per-table calls

**Phase gate:** a report generation and a semantic layer build both record
non-zero tokens. Until this phase, both record nothing at all.

```
feat(reports): a generated document records what it cost to write
test(reports): concurrent waves attribute their tokens to the right run
```

---

### Phase 5 — attribution tests and the usage query

*No new production code. Proves the per-user story works before anything is
built on it.*

16. New `tests/unit/test_usage_attribution.py`:
    - `actor_id` is set from `ctx.user_id` on run, report run and semantic job
      creation
    - deleting a user leaves usage rows intact with `actor_id` null (`SET NULL`)
    - a per-user total groups correctly across all three tables
17. Add the canonical per-user rollup query to this document (§6) — SQL only, no
    endpoint, per the scope decision in §1.6.

This phase is one commit, not two: it *is* the tests. Commit the tests and the
documented query together.

```
test(usage): per-user totals group across all three tables and survive deletion
```

---

### Phase 6 — one structured log event per call

*Per-call granularity for free, on the logging pipeline that already exists.*

18. Emit an `llm_call` event from the gateway: model, provider, prompt/completion
    tokens, latency, cost, and the correlation id
    [core/logging.py](../backend/app/core/logging.py) already attaches.
19. **No prompt text, no completion text, no question.** `audit.py`'s rule 3,
    applied here: identifiers and counts only. A log that became a second copy of
    what reached the provider is a second thing to secure and the one place
    somebody would forget to.

**Tests:**

- an `llm_call` event carries the correlation id and the token counts
- **no test-visible field carries prompt or completion text** — assert on the
  emitted event's keys, so rule 3 is enforced rather than trusted

**Phase gate:** the redaction processor in `core/logging.py` still runs over these
events. Do not add a bypass for them.

```
feat(llm): every provider call leaves one structured log line
test(llm): the llm_call event carries counts and no content
```

---

## 4. What is not required

**No prompt-version move.** `PROMPT_VERSION`, `SEMANTIC_PROMPT_VERSION` and
`REPORT_PROMPT_VERSION` all stay where they are. Nothing about what the model
reads changes, and the convention CLAUDE.md sets is that the constant moves for
what reaches the model, not for what we record about the reply.

**No frontend work.** No file under `frontend/` is touched, so `npm run typecheck`
/ `build` / `test` are not part of any phase gate.

**No import-linter contract change.** `Usage` lives in `domain/ports/` as a frozen
dataclass with no I/O, which every one of the eight contracts already permits.

---

## 5. What this leaves undone

Named so nobody assumes otherwise:

- **Streamed token counts depend on the provider.** A gateway that sends no usage
  chunk yields zeros for `present` and `describe`. Visible as a null, never
  estimated.
- **No budgets, quotas or alerts.** This measures; it does not enforce. A per-user
  cap is a different feature with a different failure posture — it would have to
  fail *closed*, and everything here fails open.
- **No read endpoint.** SQL only, by decision. `GET /usage` is separate work.
- **Embeddings are not counted.** `embed()` spends real tokens during indexing
  passes. It is deliberately out of scope here (a different endpoint, a different
  unit, and the worker's sweep is the only caller) and worth a follow-up.
- **Historical rows stay null.** Not backfilled, for the reason §2 gives.

---

## 6. Reading the numbers

No endpoint (§1.6). These are the queries the phases are built to make answerable,
recorded here so the shape is agreed before anything depends on it.

**Per user, across everything they caused:**

```sql
SELECT
    u.email,
    SUM(x.prompt_tokens)     AS prompt_tokens,
    SUM(x.completion_tokens) AS completion_tokens,
    SUM(x.cost_usd)          AS cost_usd   -- NULL-safe: unpriced models stay out
FROM (
    SELECT actor_id, prompt_tokens, completion_tokens, cost_usd FROM runs
    UNION ALL
    SELECT actor_id, prompt_tokens, completion_tokens, cost_usd FROM report_runs
    UNION ALL
    SELECT actor_id, prompt_tokens, completion_tokens, cost_usd FROM semantic_jobs
) AS x
JOIN users u ON u.id = x.actor_id
GROUP BY u.email
ORDER BY cost_usd DESC NULLS LAST;
```

**Which node costs the most, across all runs** — the question `run_steps` exists
to answer:

```sql
SELECT name,
       COUNT(*) FILTER (WHERE llm_calls > 0) AS calls,
       SUM(prompt_tokens + completion_tokens) AS tokens,
       ROUND(AVG(llm_latency_ms)) AS avg_llm_ms
FROM run_steps
WHERE llm_calls > 0
GROUP BY name
ORDER BY tokens DESC;
```

Two rules for anything built on these later:

- **Never coerce a null `cost_usd` to zero.** Null means litellm could not price
  the model — the normal state for a self-hosted deployment — and summing it as
  zero silently reports a real spend as free.
- **A null token count is "not measured", not "no tokens".** Historical rows and
  providers that send no streamed usage chunk both produce nulls, and averaging
  over them understates every figure they touch.
