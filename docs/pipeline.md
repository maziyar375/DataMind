# The run pipeline, node by node

What happens between "user hits enter" and "answer + table + chart appear".
Companion to [architecture.md](architecture.md) (the why) and
[CODEBASE.md](CODEBASE.md) (the whole stack). This file is only the pipeline.

Code: [`backend/app/pipeline/`](../backend/app/pipeline/) —
`pipeline.py` (the executor), `nodes/__init__.py` (all nine nodes),
`state.py` (typed state), `prompts/` (versioned prompts),
`checks.py`, `disclosure.py`, `metadata.py`.

---

## 1. What orchestrates this

**Not LangGraph. Not LiteLLM.** Both names come up, both are the wrong layer:

- **LiteLLM** is a *provider adapter*, not an orchestrator. It lives at
  `app/infra/llm/` behind the `LLMGateway` port and does one thing: turn
  `ChatMessage[]` into a provider call. **`import litellm` outside
  `app/infra/llm/` fails CI.** Nodes only ever see three methods:

  | method | returns | used by |
  |---|---|---|
  | `complete(llm, messages)` | `Completion` (text + token counts + latency) | `route` |
  | `structured(llm, messages, schema)` | a validated Pydantic model | `clarify`, `generate`, `chart` |
  | `stream(llm, messages)` | `AsyncIterator[str]` | `present` |

- **The orchestrator is our own state machine**, `AnalyticsPipeline.run` in
  [pipeline.py](../backend/app/pipeline/pipeline.py) — ~70 lines, a `while`
  loop over an ordered node list with an index. That is the whole engine.

- **LangGraph is deliberately deferred.** The graph is linear with one bounded
  loop: no parallel fan-out, no durable interrupts, no resume-mid-graph. Node
  signatures are already LangGraph-shaped (`async (state, deps) -> result`), so
  adopting it is wiring, not a rewrite. See §6 for the port map.

---

## 2. The graph

```
                                    ┌─── CHITCHAT / UNSUPPORTED / METADATA ──► HALT (answer, no SQL)
                                    │
  route ──────────────────────────► ┤   (history, once there is one)
                                    │ ANALYTICAL
                                    ▼
  retrieve  (no LLM — schema block + semantic layer + history)
      │
      ▼
  clarify ──── asks? ─────────────► HALT (question becomes the answer,
      │                                    run ends NEEDS_CLARIFICATION)
      │ answerable / skipped / failed-open
      ▼
  generate ◄──────────────────────────────────┐
      │                                       │
      ▼                                       │ goto generate
  validate ── guard rejected ─────────────────┤  (budget permitting)
      │ VALID                                 │
      ▼                                       │
  execute  ── db error ───────────────────────┤
      │ rows                                  │
      ▼                                       │
  inspect  ── retryable finding (once) ───────┘
      │
      ▼
  present  (disclosure gate → streamed narration)
      │
      ▼
  chart    (data veto → model → plan_chart → Vega-Lite)
```

`ORDER` in [pipeline.py:25-38](../backend/app/pipeline/pipeline.py#L25-L38) is
the single source of truth for sequence. Nodes never decide what runs next
beyond an optional `goto`.

### Node summary

| # | node | LLM? | can HALT | can `goto` | writes |
|---|---|:--:|:--:|:--:|---|
| 1 | `route` | ✅ `complete` | ✅ | – | `intent`, `answer`, **tokens** |
| 2 | `retrieve` | ❌ | – | – | `context` |
| 3 | `clarify` | ✅ `structured` | ✅ | – | `clarification`, `answer` |
| 4 | `generate` | ✅ `structured` | – | – | `attempts[]` |
| 5 | `validate` | ❌ | – | ✅ `generate`/`present` | `attempt.report`, `.rewritten_sql` |
| 6 | `execute` | ❌ | – | ✅ `generate`/`present` | `execution` |
| 7 | `inspect` | ❌ | – | ✅ `generate` | `attempt.findings` |
| 8 | `present` | ✅ `stream` | – | – | `disclosed`, `answer` |
| 9 | `chart` | ✅ `structured` | – | – | `chart` |

**Typical successful run = 4 model calls** (route, clarify, generate, present)
**+ 1 if a chart survives the veto.** Four of the nine nodes cost nothing.

---

## 3. Each node in detail

### 1. `route` — classify before spending a schema-sized prompt

**Prompt:** the question, plus **`ROUTE_SYSTEM` on the first turn of a
conversation** and **`ROUTE_SYSTEM_WITH_HISTORY` once there is one**. Still no
schema and no semantic layer — the cheapest call in the run.

The history is there because a follow-up leaves its subject out. "and by
month?" is nine characters with no data noun in them, and classified alone it
put CHITCHAT and UNSUPPORTED within reach of a question that plainly needs the
database — either of which HALTs the run before a line of SQL is written. The
earlier turns are context for reading the question; the question itself stays
the user message, so what is being classified never changes.

A first turn has no history and sends `ROUTE_SYSTEM` **byte-identically** to
the way it always did. That matters: the eval suite is single-turn, so its
baseline is exactly this prompt.

The turns are rendered by `_render_history`, so they arrive filtered by the
connection's disclosure policy like every other prompt — see §3.8.

**Logic:**
1. `complete()`, take `text.strip().upper().split()[0]`.
2. Not one of the four labels → `ANALYTICAL`. `LLMError` → `ANALYTICAL`.
   Fail-open in both directions: a routing failure must not fail the run.
3. Branch:
   - **CHITCHAT** → canned greeting, `HALT`.
   - **UNSUPPORTED** (writes, out of scope) → canned refusal, `HALT`. Answered
     gracefully, not as an `E_*` error — a write request isn't a bug to debug.
   - **METADATA** → `metadata.answer_metadata(question, snapshot tables)`,
     `HALT`. **No model call, no SQL.** This branch exists because routing
     "what tables do I have?" into `generate` makes the model write SQL against
     `information_schema`, which the guard *always* rejects as a system table —
     the run would fail before an answer could exist. `metadata.py` picks
     granularity from the question: an inventory (name, rows, column count)
     unless the question names a table, then that table's columns with types.
   - **ANALYTICAL** → continue.

**Only node that records tokens** — see §7.

### 2. `retrieve` — build everything the generator is allowed to see

**No LLM call.** Cost: zero tokens, sub-millisecond.

**Logic** ([nodes/__init__.py:159-211](../backend/app/pipeline/nodes/__init__.py#L159-L211)):
1. `approx_chars = sum(60 + 40 * len(columns))` over all snapshot tables,
   against `_RETRIEVE_BUDGET_CHARS = 50_000`. (The `sales` fixture sits at
   26,480 — under the ceiling, so it takes step 2.)
2. **Under budget → `FULL_SNAPSHOT`**: send every table. This is the common
   path for small and medium schemas.
3. **Over budget → `EXACT_MATCH`**:
   - seed = tables whose *name*, or any of whose *column names*, appears as a
     lowercase **substring of the question**;
   - plus the tables the recent turns actually **queried** —
     `_tables_from_history` reads the qualified names out of the SQL behind an
     earlier answer, which is exact rather than approximate because
     `_SQL_RULES` requires every table to be schema-qualified. This is the
     follow-up case: "and by month?" matches no table on its own and would
     otherwise fall to the `tables[:20]` fallback, an arbitrary twenty that
     need not contain the table the question it continues was answered from.
     It reads the SQL and not the prose deliberately — "revenue rose in June"
     names no table, and substring-searching narration matches `id` inside
     "identify";
   - `_expand_by_fk(seed, …)` grows it by **exactly one FK hop in either
     direction** — a question names `orders` and `products` but never the
     `order_items` bridge that joins them, and substring matching structurally
     cannot find bridges;
   - no seed at all → `tables[:20]` in snapshot order.

   Retrieval reads the **raw** history, before the disclosure filter of §3.8:
   the selection never leaves the process, and what is rendered from it is
   gated by `RetrievedContext.render` exactly as before. No policy governs
   which of the customer's own tables the customer's own question may be
   answered from.
4. Keep only relationships touching a selected table.
5. Attach `deps.history` (last ≤6 messages) and `deps.semantic` (the layer, or
   `None`), build `RetrievedContext`.

**What `RetrievedContext.render(policy)` emits** — the block every downstream
prompt embeds:

```
Dialect: postgres
<bracket legend, only if some column actually rendered a hint>

Tables:
- public.orders(id integer PK, customer_id integer FK->public.customers.id,
                status text [∈ {paid, cancelled}], ...)  (~12,000 rows)

Foreign keys:
- public.order_items.order_id -> public.orders.id

<semantic-layer block, only if a layer is present and enabled>
```

Two independent gates apply here:
- **`HintBudget.from_policy(policy)`** decides whether row counts, value lists,
  distinct counts, null fractions and ranges render at all. Structure is never
  gated; *content* always is.
- **`render_semantic`** ([semantic/render.py](../backend/app/semantic/render.py))
  scopes the layer to the retrieved tables and drops anything invalid or
  excluded. **No layer → no block, not even a blank line** — byte-identical to
  the pre-feature prompt, which is what keeps the eval baseline comparable and
  gives you the A/B switch (`connections.semantic_layer_enabled`).

### 3. `clarify` — ask once, instead of answering a question nobody asked

**Prompt:** `CLARIFY_SYSTEM` (schema block + history) + `CLARIFY_USER`
(question) → `structured(ClarificationProposal)`.

**Placement is the design.** After `retrieve` so it judges against the *same*
schema block and semantic layer the generator will see — "which revenue
column?" is only answerable with the columns in hand, and a metric definition
that already settles the question must be visible or this node invents doubt.
Before `generate` so an unanswerable question costs no SQL.

**Logic:**
1. `deps.clarify_enabled` false → `SKIPPED`. It is false both when the
   connection switch is off **and** when this run *is* the answer to a question
   we already asked — enforced in `run_service` by checking the previous run's
   status, not by trusting the model to remember.
2. **Fails open in every direction**: `LLMError`, `ValueError`, or an empty
   question all mean *proceed*. A guessed answer shown with its SQL beats no
   answer.
3. `answerable=True` → proceed.
4. Otherwise: options cleaned (deduped, trimmed to 120 chars, capped at 4),
   **`state.answer` = the question itself** so the thread reads as a
   conversation rather than a dead run, emit `CLARIFICATION_REQUESTED`, `HALT`.
   The run ends `NEEDS_CLARIFICATION`, which is **not terminal** — `cancel`
   still applies, and the stale-run reconciler leaves it alone. The user's
   reply arrives as an ordinary new run; no durable interrupt, no resume.

**The reply carries its question with it.** Because there is no resume, the
next run's message is the reply *alone* — and a reply is usually a complete
question in its own right. Asked "who are our best sellers?", answered "total
sales (order amount)", the generator answered the reply: one figure across all
orders. The transcript held both turns, but as passive context, against an
`_SQL_RULES` line that says to answer exactly what is asked at the granularity
asked — history lost to the rule every time. `run_service._compose_question`
therefore rebuilds the question from the exchange (original question, the
clarifying question, the reply, and one line saying the reply is a criterion
and not the question) whenever `_pending_clarification` finds one. It is the
same single lookup that disables `clarify` for this run — one query, two
consequences.

Composed in the service rather than in a prompt for two reasons: it keeps
`GENERATE_SYSTEM` byte-identical (see below), and every node downstream of
`state.question` gets the fix at once — `retrieve` matches tables against the
subject again, and `present` narrates the question the user actually asked.
Each of the three quoted parts is capped at `_QUESTION_CHARS` (300), so a
pasted essay cannot crowd the schema out of the prompt. With no pending
clarification the question passes through verbatim.

`GENERATE_SYSTEM` is untouched by this feature on purpose: eval Round 2 showed
that prompt losing 10 points of execution accuracy to an unrelated addition, so
when a question is answerable the generator sees exactly what it saw before
clarify existed.

### 4. `generate` — the only node that writes SQL

`structured(SqlProposal)` → `{sql, reasoning}`. **Three different
prompts depending on why we're here:**

> **`PROMPT_VERSION` v7 — the runaway-reply fix. Read this before changing
> `SqlProposal` or the three SQL prompts.**
>
> Handed a wide schema, a model writes correct SQL in ~90 tokens and then keeps
> going, filling whatever **unbounded field** the contract offers until
> `max_tokens` cuts the reply mid-string. The JSON never closes, `_parse_into`
> throws, and a correct query is discarded as `E_LLM` ("did not return valid
> SqlProposal JSON"). Measured on the 42-table `sales` schema (28,892-char
> prompt):
>
> * `tables_used` was the first sink — 1,350 entries, the same 42 tables
>   repeated 61 times. **Removed.** Nothing read it: the referenced-table list
>   the platform trusts is the one `sqlguard` parses out of the SQL, because a
>   model's claim about which tables it used is not evidence.
> * With that gone the deliberation moved *into the `sql` string* — a query,
>   then `-- but the question might mean…`, then another query. **`_OUTPUT_RULES`**
>   ("put the statement in `sql` and nothing else") now ends all three SQL
>   prompts. 2/6 failures → 0/6; median reply 750 tokens → 95.
>
> Two non-fixes, both measured, both tempting: **raising `max_tokens` makes it
> worse** (6/8 failures at 4,096 vs 5/8 at 2,048 — the median reply is exactly
> the cap at either size, because the rambling expands to fill the budget), and
> **`maxItems` is ignored** by the provider's constrained decoder, so schema
> bounds do not restrain it. **Keep every field in a structured-output contract
> bounded, and say what the envelope is for.**

| when | system prompt | user turn |
|---|---|---|
| attempt 1 | `GENERATE_SYSTEM(dialect, schema, history)` | `Question: …` |
| retry after **`inspect`** (previous SQL was `VALID` **and** has `retry=True` findings) | `REVIEW_SYSTEM(feedback, rules, schema, history)` — *"ran successfully, but the result looks wrong"* | question + `Your previous SQL was:` |
| retry after **guard rejection or DB error** | `REPAIR_SYSTEM(feedback, rules, schema, history)` — *"rejected by a validator"* | question + `Your rejected SQL was:` |

The REVIEW/REPAIR split is not cosmetic: telling the model its SQL was
"rejected by a validator" when it actually ran fine is a lie that invites it to
fix the wrong thing.

**All three share one `_SQL_RULES` constant, and all three get the history.** A
repair opens a *fresh two-message conversation* — the model has never seen
`GENERATE_SYSTEM` — so anything a repair prompt omits is gone, not remembered.
Until v5 both repair prompts omitted every mandatory rule, which meant a repair
could fix the flagged issue, break a rule it had never been shown, and be
rejected a second time with a budget of 1 already spent.
[test_generate_prompt.py](../backend/tests/unit/test_generate_prompt.py) asserts
each prompt carries the rules, the dialect and the history, so the three cannot
drift apart again.

**Only the finding that *earned* the retry is quoted back.** An advisory finding
is advisory in both directions — it may not start a regeneration, and it may
not steer one some other check started. Passing the whole list is what let an
advisory soft-delete note add a `WHERE is_deleted = false` the question never
asked for, turning a correct answer into a wrong one.

`LLMError` → `E_LLM`, `FAILED`. Otherwise strip trailing `;`, append a
`SqlAttempt`, emit `SQL_GENERATED`.

> ⚠️ Do not add general SQL guidance to `GENERATE_SYSTEM`. Eval Round 2
> measured a "getting the answer right" block **lowering** execution accuracy
> 36% → 26% and parse 98% → 88% on the small model — the extra instructions
> crowded out the schema. More is not better here.

### 5. `validate` — the hard gate, fails closed

**No LLM call.** `guard(raw_sql, policy)` =
`SqlValidator.validate` (SQLGlot parse → AST walked against an **allowlist**,
names resolved against the connection's stored snapshot) → `render()` (rewrite,
inject the row `LIMIT`). An **unknown node type is a rejection, not a
warning**. An unsynced connection can query nothing.

**On `status != VALID`**, a three-rung ladder — the same one `execute` uses:
1. repair budget left (`repair_count < max_repairs`) → `goto generate`;
2. else `_restore_superseded()` — if a check-driven retry is in flight, put the
   earlier working result back and `goto present`;
3. else `FAILED` with the first error's `rule_id`.

### 6. `execute` — read-only, capped, timed out

**No LLM call.**
1. `connector.explain(sql)` → `rows_scanned_estimate` (for the step trail).
2. `connector.execute(sql, max_rows, statement_timeout_ms)` — `READ ONLY`
   transaction on PG/MySQL/Oracle, read-only role + query timeout on SQL Server.
3. `ConnectorError` → the same three-rung ladder as `validate`, ending in
   `E_QUERY_FAILED`.
4. Success → `state.execution`, emit `QUERY_COMPLETED`.

### 7. `inspect` — "it ran, and it's wrong"

The repair loop already covers two failure modes: *the guard said no* and *the
database said no*. Both mean no result exists. This covers the third and most
expensive: **the query ran, returned something plausible, and is wrong.**

**No LLM call, and it never reads a result value** — only the SQL, the
snapshot, and the result *shape*. That is deliberate: it costs no tokens, it
cannot inherit the generator's own misreading of the question the way a model
critiquing its own SQL does, and it behaves identically under every disclosure
policy (a model critic under `NONE` is handed *"Result data was not shared"* and
can conclude nothing).

| code | fires when | retry? |
|---|---|:--:|
| `C_EMPTY_RESULT` | 0 rows **and** a predicate provably contradicts the snapshot — a literal outside `sample_values`, or a range outside recorded min/max | ✅ |
| `C_EMPTY_RESULT` | 0 rows, no such evidence | ❌ advisory |
| `C_NULLABLE_INNER_JOIN` | inner join `ON` a nullable **FK** column | ❌ advisory |
| `C_SOFT_DELETE_UNFILTERED` | `is_deleted`/`archived`/`is_active`… on a queried table, never mentioned, `distinct_count != 1` | ❌ advisory |
| `C_GRANULARITY` | question matches the single-figure regex, `row_count > 1`, no `GROUP BY` keys | ❌ advisory |
| `C_TRUNCATED` | result hit the row cap | ❌ advisory |

**A finding is a suspicion, never a verdict.** The bar for spending a
regeneration is deliberately high, and it was set by measurement:
`C_NULLABLE_INNER_JOIN` shipped retry-eligible and cost **four correct answers**
on `sales_v1` (0 wins / 4 losses, 36% → 30%) — every time it fired on a
*correct* query, the regeneration obeyed it and came back worse. Whether a
nullable FK should be outer-joined is a question about what the user *meant*,
and a structural check cannot see intent.

**Retry mechanics:** at most one, only if `not check_repair_used` and the guard
and database haven't already spent the budget. It stashes
`superseded_execution` first — that stash is what makes the retry safe, because
`_restore_superseded` puts the working result back if the retry can't validate
or run. **A check can never turn a working answer into a failed run.**
`distinct_count == 1` is skipped on soft-delete flags because the eval fixture
carries an always-false `is_archived` on 35 of its 42 tables, which would
otherwise fire on nearly every query.

### 8. `present` — the disclosure gate, then narration

1. `disclose(execution, policy)` — the **one place** result data is filtered:

   | policy | what the model gets |
   |---|---|
   | `NONE` | row count only, zero values |
   | `AGGREGATE` | column *names* + row count, no values |
   | `SAMPLE` | first **50** rows |
   | `FULL` | everything |

   Plus a **cap note** when truncated, so the model can't narrate a capped
   result as "the top 1000 customers" when 1000 is the platform's limit.

2. Caveats = `inspect`'s findings **for the attempt being presented only**
   (never accumulated across retries, so a suspicion a retry cleared cannot
   resurface). Only `.message` is used — `.hint` is repair guidance addressed to
   the generator and would read as an instruction the answer failed to follow.

3. `stream()`, emitting `TEXT_DELTA` per chunk. On mid-stream `LLMError` with
   partial text already emitted: emit **`TEXT_RESET`** then a fallback
   sentence. Discarding the buffer isn't enough — deltas are already on the live
   bus *and* durably stored for `Last-Event-ID` replay, so a client would show
   half a sentence with the fallback stitched on. The data is already correct; a
   narration failure must not lose it.

**The sentence this node writes is result data, and it is persisted.** That is
the third thing the policy governs, alongside the result and the schema block:

- `disclose_history(history, policy)`
  ([disclosure.py](../backend/app/pipeline/disclosure.py)) filters the
  transcript at **read** time, against the policy in force *now* — the same
  rule `HintBudget` follows, and the same rule the chat header promises. Under
  `SAMPLE` and `FULL` it is the **identity function**, so a wide connection
  builds the prompt it built before this existed.
- Under `NONE`, `AGGREGATE` and anything unrecognised, an earlier answer's
  prose is replaced by a placeholder. Without that, a connection tightened from
  FULL to NONE went on replaying yesterday's figures to the model under a
  policy whose entire meaning is that no result data reaches it — the messages
  were rendered once, at write time, and nothing re-read them.
- **What survives every policy:** the user's own turns, the **SQL** behind an
  earlier answer, and a **clarifying question** (asked before any SQL runs, so
  it has seen no result — and withholding it would leave the user's reply,
  which is the very next turn, answering a question the model can no longer
  see). The SQL is also the part a follow-up actually needs: under `NONE` it is
  the only thing carrying "and by region?" back to the 2024 window the previous
  turn established.
- **Accepted residual:** a literal inside kept SQL (`WHERE status = 'churned'`)
  may have come from a column value list a wider policy's `HintBudget` once
  allowed. One token, already on screen in the SQL artifact the user is invited
  to audit, and stripping it would cost the follow-up its subject.
- **Not per-turn-precise.** The policy a message was *written* under is not
  recorded, so the filter reads the current one and fails closed. Under a
  narrow policy that withholds prose which may have been harmless — an answer
  written under `AGGREGATE` holds counts `AGGREGATE` permits. Making it exact
  needs a policy snapshot on `runs` and a migration; the loss is small because
  the SQL, which is what the next turn reads, is kept either way.

Both other paths that send a transcript to a model — `route` (§3.1) and the
follow-up suggestions (`run_service.suggest_followups`) — go through the same
filter. Suggestions in particular fire on their own when the SPA refreshes a
thread rather than because a user asked something, and their schema block goes
through `HintBudget` too: `_describe_schema` used to print approximate row
counts unconditionally, which is a figure derived from customer data that every
other prompt withholds under `NONE`.

**History is also scoped to one connection.** It is keyed on the conversation,
so a thread whose turns ran against two connections would hand one connection's
answers to the other's prompt under the other's policy. `_bind_connection`
refuses a per-message `connection_id` that differs from the conversation's once
the transcript is non-empty (the SPA already locks the picker there; this
closes the API route around it), and `_recent_history` additionally drops any
turn it cannot attribute to a run on *this* connection.

### 9. `chart` — the data gets a veto before the model gets a vote

Best-effort and fail-open (the opposite of the guard): the answer and table are
already persisted, so any failure here just means no chart.

1. Skip outright if no execution, 0 rows, or `< 2` columns.
2. `profile_result(...)` → cardinality, numeric range, constant columns.
3. **`unchartable_reason(profile)` runs *before* the model call** — a single
   row, a constant measure, or an id-as-measure is unchartable whatever the
   model says, so it costs **zero tokens** and the step trail shows a fact about
   the data instead of "the model declined".
4. `structured(ChartIntent)`. The model sees column names, types, cardinality
   and numeric range — **never a row value**, so charting never widens
   disclosure. A cardinality is a count, not data.
5. **`plan_chart(profile, suggestion)` owns the verdict.** It vetoes what the
   data can't support, repairs salvageable intents (pie → bar past 6 slices,
   line → bar over unordered text, swapped axes, mislabelled axis types), caps
   category charts at `MAX_CATEGORY_MARKS` with a label saying what was dropped,
   and falls back to a pure shape heuristic when the model errored or emitted
   garbage (common with small models).
6. `compile_vega_lite(...)` → `state.chart`, emit `ARTIFACT_CREATED`.

---

## 4. Control flow rules

Every rule lives in [pipeline.py](../backend/app/pipeline/pipeline.py).

**Node return values** — `NodeResult(status, detail, goto)`:

| status | executor does |
|---|---|
| `OK` | next node (or jump, if `goto` is set) |
| `SKIPPED` | next node — identical to `OK`, only the persisted step status differs |
| `HALT` | stop, run is finished successfully |
| `FAILED` | stop, `state.error` is already set |

**Guard rails, all three independent:**
- **Deadline** — `utcnow() >= state.deadline_at` is checked *before each node*,
  raising `RunTimeoutError` → `E_TIMEOUT`. It cannot interrupt a node in
  flight; the statement timeout does that job inside `execute`.
- **`_MAX_TRANSITIONS = 24`** — a `goto` cycle can never spin forever even if a
  node misbehaves → `E_PIPELINE_LOOP`.
- **Node crash** — caught, logged, recorded as `E_NODE_FAILED` and a `FAILED`
  step. **Never a bare HTTP 500.** A process that dies mid-run is healed by the
  reconciler and a startup sweep, so no row is stuck `RUNNING`.

**The repair budget is shared.** `max_repairs` defaults to **1** and
`repair_count = len(attempts) - 1`, so a run makes **at most 2 generate
attempts total** — and guard rejection, DB error and `inspect`'s retry all draw
from that same allowance. A check can never eat the budget the guard and the
database have first claim on.

**Every node** persists a `run_step` and emits `STEP_STARTED` /
`STEP_FINISHED` over SSE. The SPA renders this as the live step trail — a
valued feature; keep it visible, don't collapse it behind "Thought for Xs".

**Terminal states:** `SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`.
`NEEDS_CLARIFICATION` is deliberately **not** terminal.

---

## 5. Prompt versioning

`PROMPT_VERSION` (currently **v6**) is recorded on every run.
[prompts/__init__.py](../backend/app/pipeline/prompts/__init__.py) is the only
place run prompts live — except the semantic-layer *generation* prompts, which
live in `app/semantic/prompts.py` under `SEMANTIC_PROMPT_VERSION`, because
`app.semantic` sits *below* the pipeline: the pipeline reads a layer, a layer
knows nothing about a run.

**Move `PROMPT_VERSION` when the bytes the SQL-producing path sends change.**
That's why v3 → v4 for the semantic block, v4 → v5 for the shared rules and
history on repairs, and v5 → v6 for `ROUTE_SYSTEM_WITH_HISTORY` plus the
disclosure filter over the history — and why clarify, caveats and chart prompt
changes *don't* move it, since the eval scores generated SQL and none of those
touch it.

v6 is worth reading closely before you compare numbers across it, because it
moves for two changes that are each conditional:

| connection | v6 vs v5 |
|---|---|
| first turn of a thread, any policy | byte-identical (`ROUTE_SYSTEM`, no history to filter) |
| follow-up, `SAMPLE` or `FULL` | route prompt gains the history; every SQL prompt identical |
| follow-up, `NONE` or `AGGREGATE` | route prompt gains the history; SQL prompts lose earlier answers' prose, keep their SQL |

The eval suite is single-turn against a `SAMPLE` fixture, so it sits in the
first row: **v6 does not move the baseline**, and a v5 score is still
comparable. That is a property of the eval, not a general guarantee — a
multi-turn suite would need re-measuring.

> **Recorded version drift, pre-existing:** `runs.prompt_version` comes from
> `settings.prompt_version` ([config.py](../backend/app/core/config.py), default
> `"v2"`), not from the `PROMPT_VERSION` constant the prompts actually carry.
> Unless `PROMPT_VERSION` is also set in the environment, every run is recorded
> under the wrong version. Worth fixing before the next eval round; not part of
> the v6 change.

---

## 6. When you port to LangGraph

The shapes already line up. Nothing here needs redesigning:

| today | LangGraph |
|---|---|
| `async def node(state, deps) -> NodeResult` | node function (bind `deps` via `functools.partial` or config) |
| `RunState` (Pydantic, `extra="forbid"`) | graph state schema — already the right shape |
| `ORDER` list + `index += 1` | `add_edge` chain |
| `NodeResult.goto` | `add_conditional_edges` |
| `status="HALT"` | edge to `END` |
| `_MAX_TRANSITIONS = 24` | `recursion_limit` |
| `deps.emit(...)` + `on_step(...)` | `astream_events` / callbacks |
| `clarify` HALT + new run | `interrupt()` + checkpointer — **the actual upgrade** |

**The one thing worth migrating for** is clarification. Today a clarifying
question ends the run and the user's reply arrives as a brand-new run, with
continuity carried only by the 6-message history tail. A checkpointer plus
`interrupt()` would make it a real durable pause. Everything else on this list
is a lateral move — don't take the dependency for cosmetics.

---

## 7. Known gaps (verified in code, 2026-07-31)

1. **Token accounting only counts `route`.** `state.prompt_tokens` is written
   in exactly one place — [nodes/__init__.py:78-79](../backend/app/pipeline/nodes/__init__.py#L78-L79)
   — because `complete()` returns a `Completion` with usage while
   `structured()` and `stream()` return the parsed model / a delta iterator and
   drop it. So `runs.prompt_tokens` and the eval's `estimate_cost_usd` count
   only the tiny classifier call and **miss the schema-bearing `generate`
   prompt entirely**. Every cost figure is a large undercount. Fix: return usage
   from `structured`/`stream` in the gateway.

2. **The eval suite no longer exercises retrieval.** Measured against the live
   fixture: 42 tables, **599 columns**, `approx_chars = 26,480`. That
   straddled the old `24_000` ceiling, which is why the reports show 86.4%
   recall / 74% full-hit — `EXACT_MATCH` was firing, and the residual misses
   were real. Raising the ceiling to `50_000` puts the fixture **under** it, so
   `sales_v1` now runs entirely on `FULL_SNAPSHOT` and every recall figure in a
   future report will read 100% — not because retrieval improved but because it
   stopped choosing. Gaps 3-5 below are unfixed and now untested; they still
   fire on any schema past ~50k chars.

   `test_fixture_fits_the_retrieve_budget_and_recalls_everything` pins this so
   the change of meaning is visible, and
   `test_retrieval_recall_gap_shows_on_bridge_question` forces the budget down
   to keep covering `EXACT_MATCH`. **Do not compare a post-50k recall number
   against a pre-50k one.**

   (Do not read the DDL to estimate this: parsing `sales_seed.sql` for
   `CREATE TABLE` columns gives 313 and misses the ~286 added by later
   `ALTER TABLE`, which understates `approx_chars` by more than half and
   inverts the branch. Introspect the live database.)

3. **`retrieve` ignores the semantic layer's vocabulary.** `deps.semantic`
   holds per-table `label` and `synonyms` and per-column `synonyms` — exactly
   the "revenue" → `order_items.line_total` bridge that substring matching on
   raw catalog names cannot cross. It's passed straight through to
   `RetrievedContext` and never consulted for *selection*. Free recall, already
   in scope at that line.

4. **`EXACT_MATCH` matches backwards and on the wrong granularity.** The test is
   `table_name in question`, so **17 of the 42 fixture tables are snake_case
   and structurally unmatchable** (a user types "order items", not
   "order_items"). Meanwhile `id` is a column in **36 of 42 tables**, so any
   question containing *paid*, *did*, *provide* matches nearly everything and
   FK-expands to the whole schema. Too narrow on tables, far too loose on
   columns. Tokenizing both sides with a 3-4 char minimum fixes both.

   `_tables_from_history` narrows one case of this — a follow-up now inherits
   the previous statement's tables instead of matching nothing — but it is not
   a fix for the matcher. A *first* question is still matched exactly this way,
   and it is the first question that decides what the follow-up inherits.

5. **No budget re-check after `_expand_by_fk`.** The branch that exists to
   respect `_RETRIEVE_BUDGET_CHARS` can emit well past it, unbounded.

6. **v5 is shipped but unmeasured.** Two gaps were closed in one change:
   the repair prompts now carry the shared `_SQL_RULES` block and the
   conversation history, and that history now includes the SQL behind each
   earlier answer (`run_service._sql_behind` joins `generated_queries` back to
   the assistant message, since the message itself only holds the prose).

   **This is an unconditional addition to the SQL-producing path — the exact
   shape of change that cost 10 points in eval Round 2.** It has not been run
   against `sales_v1`. Until it has, treat v5 as unvalidated and keep v4
   reachable for comparison. Two numbers decide it:

   - **`repair_violations_by_rule`** — printed under the existing violations
     line as `of those, on a repair attempt: …`. This is the metric the change
     was made to move; it should shrink or empty out.

     ```
     violations by rule: E_UNKNOWN_COLUMN=2  E_TABLE_NOT_ALLOWED=1
          of those, on a repair attempt: E_UNKNOWN_COLUMN=1
     ```

   - **execution accuracy on attempt 1** — the regression risk. The rules
     block is unchanged there, but the history block is longer now (it carries
     SQL), and the small model is sensitive to a crowded prompt. If attempt-1
     accuracy drops, the history change is the suspect, not the rules.

   If v5 loses accuracy, split it: the rules-on-repair half only affects
   attempt 2 and can ship alone.

7. **`_sql_behind` can be one attempt off in a rare case.** It takes the
   highest `attempt_no` whose `rewritten_sql` is non-null (i.e. passed the
   guard). When `_restore_superseded` put an earlier result back *and* a later
   attempt had validated but failed in the database, the history shows the
   later statement rather than the one that produced the answer. It is a hint
   for the next question, not something a run depends on — fixing it means
   joining `query_executions` as well.

8. **v6's history filter is fail-closed, not per-turn-exact** (§3.8). The
   policy a message was written under is not recorded, so a narrow policy
   withholds prose that may have been permissible when written. Exactness needs
   a `disclosure_policy` snapshot on `runs` and a migration. Two smaller
   residuals sit with it: a value literal inside kept SQL, and a clarifying
   question, both of which can carry a column value that a wider policy's
   `HintBudget` once put in the schema block. All three are deliberate — see
   the reasoning in §3.8 before "fixing" one.

9. **v6 is shipped and unmeasured, on top of an unmeasured v5** (gap 6). Its
   route change is a *conditional* addition — a follow-up's classifier prompt
   grows, a first question's does not — so the single-turn eval cannot see it
   at all. What it cannot see it cannot catch: if history in `route` starts
   dragging classifications toward the previous turn's label ("what tables do I
   have?" after a revenue question coming back ANALYTICAL), the suite will read
   clean. A multi-turn eval case is the only thing that would measure this.
