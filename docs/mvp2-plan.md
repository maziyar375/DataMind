# MVP2 — where MVP1 is weak, and what to build next

> **Status:** research and proposal, written 2026-08-27 against `main` at
> `354a87e` (tagged `v0.0.5`). Part 1 is grounded in the code; Part 2 is desk
> research on four competing products; Parts 3–5 are the argument. Read the
> status banner convention from [architecture.md](architecture.md) — this is
> that kind of document.
>
> **Update 2026-08-30 — one item is now built.** [A6](#a6-fix-the-semantic-layer-render--s--done-2026-08-30),
> the render bug that made the semantic layer inert, is fixed; §1.3 records what
> changed and what did not. Everything else here is still proposal.
>
> Persian edition: [mvp2-plan.fa.md](mvp2-plan.fa.md).

---

## 0. The thesis, in one page

MVP1 is built as if the danger is **the model doing something destructive**.
Against that danger it is genuinely better than most commercial tools: an
AST allowlist that fails closed, four guard entry points with none privileged, a
disclosure policy that gates results *and* schema hints *and* the conversation
transcript, read-only proven by attempting a write, credentials bound to their
row by AAD. That work is real and it is not the problem.

The danger that actually decides whether a BI tool is adopted is different:
**the model returns a plausible, well-formatted, confidently-worded wrong
answer, and nobody notices.** The guard cannot catch that — a wrong query is a
perfectly legal query. The only measurement the repo has of that failure mode is
the eval baseline, and it says **0.36 execution accuracy** (DeepSeek V4 Pro,
temperature 0.2, `PROMPT_VERSION` v2, 2026-07-26, on the deliberately-messy
`sales` fixture). Roughly two questions in three come back wrong on a hard
schema.

That number is the product. And the structural problem is not that it is low —
it is that **there is no mechanism in the running product that can raise it.**
Today the only way DataMind gets smarter is a developer editing a prompt and
shipping code. A user who spots a wrong answer, knows the right SQL, and types
it in has nowhere to put it. Their correction dies in the transcript.

Every one of the four platforms researched here solved exactly this, and all
four solved it the same way: **they let the users' corrections become the
system's durable knowledge.** Databricks calls them instructions, example SQL
queries and trusted assets, and scores them with benchmarks. Wren AI calls them
`instructions.md` and Question-SQL pairs, version-controlled beside the semantic
model. Power BI calls it marking a model "approved for Copilot" with AI
instructions attached. None of them bet on a better prompt.

DataMind already has the two hardest pieces of that machine: a **semantic
layer** with a real editor and validation, and an **eval harness that runs the
real pipeline**. Both are one step away from being the learning loop, and
neither is wired to it. The semantic layer is a per-connection document a person
edits by hand; until 2026-08-30 the render bug in §1.3 meant it **did not reach
the model at all** on either demo connection, and now that it does, it is still
a document a person edits by hand and nothing writes back into. The eval is a
developer CLI that costs money and is not in `make test`, so no user has ever
seen a score.

**MVP2's spine should be: close that loop.** Make correction cheap, make
correction durable, make correction measurable, and put the measurement in front
of the person who owns the connection. Everything else in this document —
branching threads, multi-step analysis, sharing, connectors, alerting — is worth
building, and none of it matters if the answers are wrong.

**Ranked, the ten things:**

| # | Weakness | Severity | §  |
|---|---|---|---|
| 1 | No learning loop — a correction cannot become knowledge | **Critical** | [1.1](#11-the-system-cannot-learn-and-that-is-the-whole-ballgame) |
| 2 | Retrieval is a placeholder and does not scale past the demo | **Critical** | [1.2](#12-retrieval-is-a-placeholder-with-a-hard-ceiling) |
| 3 | The semantic layer is a blob, not a model *(render bug fixed 2026-08-30)* | **High** | [1.3](#13-the-semantic-layer-is-a-blob-not-a-model) |
| 4 | One question = one SQL statement; there is no *analysis* | **High** | [1.4](#14-one-question-one-query-there-is-no-analysis) |
| 5 | Single-player: no sharing, no teams, no audit trail | **High** | [1.5](#15-single-player-by-construction) |
| 6 | Linear chat only; exploration and publishing are disconnected | **High** | [1.6](#16-the-interaction-model-is-a-transcript) |
| 7 | Narrow data surface — no files, no warehouses, no export | **High** | [1.7](#17-the-data-surface-is-narrow-in-both-directions) |
| 8 | No operator surface — cost, failures, usage all invisible | **Medium** | [1.8](#18-nobody-can-see-the-system-running) |
| 9 | Nothing is proactive — no alerts, schedules or digests | **Medium** | [1.9](#19-nothing-happens-unless-someone-is-looking) |
| 10 | Quality gates are softer than the documentation's posture | **Medium** | [1.10](#110-the-quality-gates-are-softer-than-the-posture) |

---

# Part 1 — Where MVP1 is weak

Ten things. Each is a structural limit, not a bug list — the bugs that matter
appear inside them as evidence.

## 1.1 The system cannot learn, and that is the whole ballgame

### What is missing

There is **no path from a user's judgement back into the system**. Concretely,
none of these exist:

- **No feedback capture.** No thumbs up/down on an answer. `runs` records
  `status`, `error_code`, latency and token counts — nothing about whether the
  answer was *right*.
- **No verified-query store.** When a user fixes generated SQL in the tile
  editor (`sql_origin` becomes `GENERATED_EDITED`), that edit is recorded as
  **provenance only** — the CLAUDE.md is explicit that "the guard cannot tell
  them apart and must not try". Correct. But nothing else looks at it either.
  A hand-corrected statement that answers "revenue last month" correctly is
  sitting in `dashboard_tiles.sql`, and the next person to ask the same question
  in chat gets a fresh generation with no knowledge of it.
- **No few-shot example retrieval.** `GENERATE_SYSTEM` is a static prompt. No
  question-SQL pairs are retrieved and injected. There is no embedding anywhere
  in the codebase (`grep -rn "embedding\|vector" backend/app` is empty).
- **No in-product evaluation.** `app/eval/` runs the real pipeline against a
  testcontainers fixture and produces a scorecard — but it is a developer CLI,
  costs real money, is deliberately kept off the request path by an
  import-linter contract, and has no UI. **A customer can never measure their
  own accuracy.**
- **No review workflow.** A user who distrusts an answer has no way to escalate
  it to whoever owns the connection, and that owner has no queue of
  "answers people flagged".

### Why this is the critical one

Two consequences, and the second is worse than the first.

**First:** accuracy is frozen at whatever the prompt achieves. The repo already
documents that pushing on prompts has negative returns — two changes were
*measured to lower* accuracy (a "getting the answer right" block in
`GENERATE_SYSTEM`: 36% → 26%; making `C_NULLABLE_INNER_JOIN` retry-eligible: 0
wins / 4 losses). CLAUDE.md's own conclusion is "**More instruction is not
better here**." That road is closed, and no other road is open.

**Second, and this is the real cost:** the value of a deployment is *stuck at
day one*. Every competitor's pitch is "it gets better as your team uses it."
DataMind's honest pitch today is "it is exactly as good in month twelve as it
was in hour one." An organisation that invests three weeks of an analyst's time
curating a semantic layer gets a one-time step change and then a flat line.
Until 2026-08-30, per §1.3, not even that.

### The shape of the fix

The pieces mostly exist and are not connected:

```
today:     user spots wrong answer  →  (nothing)
           user edits tile SQL      →  sql_origin = GENERATED_EDITED  →  (nothing)
           developer edits prompt   →  eval CLI  →  scorecard in a file

MVP2:      user spots wrong answer  →  flag  →  owner's review queue
           owner corrects the SQL   →  saved as a verified question-SQL pair
           pair                     →  retrieved into GENERATE's prompt on
                                       similar questions
           pair                     →  becomes a benchmark row
           benchmark set            →  score, in the UI, per connection, over time
```

Every arrow is a small piece of work. The loop is the feature.

---

## 1.2 Retrieval is a placeholder with a hard ceiling

### What the code actually does

`pipeline/nodes/__init__.py::retrieve` has exactly three branches, and its own
docstring calls it "naive by design":

| Branch | Condition | Behaviour |
|---|---|---|
| `FULL_SNAPSHOT` | snapshot ≤ 50,000 chars | send **every** table |
| `SCHEMA_QUESTION` | METADATA intent, over budget | tables the question named + the largest of the rest |
| `EXACT_MATCH` | anything else, over budget | **substring match** of table/column names against the lowercased question, plus tables carried from recent history, plus one FK hop — else `tables[:20]` |

The docstring says the quiet part out loud: *"Exact-name matching is the
fallback. Trigram, FTS, and embeddings are later strategies behind the same
`RetrievedContext` shape."* Those later strategies were never built.

### Why the ceiling is hard

**Substring matching is structurally the wrong tool for business language.**
A user asks "what was churn last quarter?". No table is called `churn`. No
column is called `churn`. The match set is empty, `carried` is empty on a first
turn, so `selected = tables[:20]` — an arbitrary twenty tables in snapshot
order, which need not contain a single relevant one. The generator then writes
SQL against a schema block that does not describe the answer. The guard
validates it fine. The query runs fine. The answer is wrong and looks right.

**The 50k budget is a demo-sized budget.** Both fixtures fit under it (`aurora`
~6k, `sales` ~26.5k), so *both demo connections always take `FULL_SNAPSHOT`* and
the fallback path is never exercised in normal use. A real warehouse does not
fit. Rough arithmetic on the fixtures puts a table at ~600 chars of rendered
schema; 50k is therefore **~80 tables**. Any customer with a normal
production database — 300 tables, 2,000 tables — lands permanently in
`EXACT_MATCH` on their very first question. **The path that has never been
tuned is the only path a real customer will ever take.**

**And the eval cannot see this.** CLAUDE.md §"Proving a change helped" rule 4
states it plainly: the budget was raised 24k → 50k, the fixture estimates
26,480, so `retrieve` takes `FULL_SNAPSHOT` on every question and **recall is
1.0 by construction**. The one instrument that could measure the problem was
accidentally blinded to it. This is the single most important eval repair.

**Value-level retrieval does not exist at all.** Ask "sales in Florida" against a
column that stores `'FL'` and there is no mechanism to bridge it. `HintBudget`
carries per-column content hints, but `connectors/hints.py` only emits a value
list "when it is provably the complete domain" — a deliberately conservative
rule that is correct for its purpose and useless as an entity index — and the
hints are gated by the disclosure policy, so under `NONE`/`AGGREGATE` they are
absent entirely. Databricks Genie has a whole dedicated feature for this
("entity matching", up to 120 columns × 1,024 values), because it is one of the
most common single causes of a wrong `WHERE` clause.

### The shape of the fix

Three separable pieces, in order of value:

1. **Fix the eval's blindness** — run the suite at a lowered ceiling, or widen
   the fixture, so recall is a measurement again. Cheap. Do it first: nothing
   else in this section can be evaluated until it is done.
2. **A real retriever behind the existing `RetrievedContext` shape** — the seam
   is already there and the generator "never learns which one produced its
   context". Embeddings over table/column names *plus their catalog comments
   plus their semantic-layer business names* would be a straight swap. Postgres
   is already the app database; `pgvector` adds no deployment unit. Hybrid with
   the existing exact-match is strictly better than either alone.
3. **An entity/value index** — opt-in per column, per connection, with an
   explicit disclosure decision attached, because a value dictionary is
   customer data by any reading of [security.md](security.md) §2.4.

---

## 1.3 The semantic layer is a blob, not a model

> **Fixed 2026-08-30.** The render bug below is repaired — this is [A6](#a6-fix-the-semantic-layer-render--s--done-2026-08-30),
> the one item of this plan that is built. The design ceilings under it are not,
> and they are why this section still ranks.

### The bug, and what it now does instead

`app/semantic/render.py` assembled the block in sections and, over its
8,000-char `DEFAULT_MAX_CHARS` cap, **popped whole sections off the back**.
Every table description was *one* section, so the trim was all-or-nothing.
Measured on the two demo connections with every entity `valid` and the switch
on:

| connection | entities | block reaching the model | tables described |
|---|---|---|---|
| `sales` | 42 | 545 chars | **0** |
| `aurora` | 13 | 606 chars | **0** |

There was a cliff at six retrieved tables — five rendered, six rendered nothing.
Both fixtures sit far under the 50k retrieve budget so they always take
`FULL_SNAPSHOT` and always pass every table, hence were always past the cliff.
**The business names, the grain, and the metrics reached the generator on no
question at all**, which from outside is indistinguishable from the feature
being switched off. It was partially masked by the layer-wins-per-entity rule:
coverage reported nothing covered, so the DDL `COMMENT ON` text rendered
instead — on `aurora`, which has good comments, answers still looked informed;
on a customer database without them, which is most of them, nothing was left.
Why it was never caught: the trim test used **one table** with `max_chars=250`
and only asserted the output was short.

**What replaced it.** The cap is now spent as an allocation rather than a
truncation. The block is fitted **line by line**, in three tiers, each filled
**round-robin** across the retrieved tables:

1. every table's head line — business name, grain, role, date column, synonyms;
2. metrics, one table at a time per pass — the lines that change the SQL rather
   than the reading of it;
3. column meanings, one table at a time per pass.

Round-robin because relevance is unknown at this point: under `FULL_SNAPSHOT`
the retrieved order *is* catalog order, so filling in document order would spend
the whole cap on tables 1–6 — the same failure wearing a smaller hat. A line
that does not fit is skipped rather than cut, because half a metric definition
is where the `WHERE` clause lived, and the sections behind the tables (join
cautions, then glossary) are fitted the same way instead of being deleted. On
the 42-entity shape: **42 of 42 tables described, every metric funded, column
detail the only thing cut** — against 0 tables before.

One consequence worth naming: entities now render *partially*, so coverage
could no longer be a substring test over the block. `render_with_coverage`
returns the block and what it covers from one fit, and a column whose line did
not fit keeps its DDL comment — the two halves compose instead of overlapping.

`PROMPT_VERSION` moved v7 → v8. It does **not** invalidate the recorded
`sales_v1` baselines, because those ran with `NodeDeps.semantic` unset and their
bytes are untouched — but it does mean the layer's A/B has never yet been run
against a prompt that actually contained the layer. That measurement is now
possible for the first time, and it is the day-zero number MVP2 should be judged
from.

### The design ceilings underneath the bug

Even repaired, the layer has limits that matter for MVP2:

- **It is a blob, not a model.** One JSON document per connection, edited in a
  form. No version history, no diff, no "who changed this metric and why", no
  review before it takes effect. Wren AI's equivalent is a *file* — MDL plus
  `instructions.md` plus `queries.yml` — deliberately version-controlled and
  reviewable, and that is the right instinct: a metric definition is code.
- **Metrics are defined but never *enforced*.** A metric bound to exact SQL
  reaches the prompt as a *suggestion*. Nothing checks that a generated
  statement computing "revenue" actually used the revenue metric's expression.
  Genie's trusted assets and Power BI's certified/verified answers both draw the
  distinction DataMind does not: an answer that *used the approved definition* is
  a categorically different thing from an answer that merely mentioned it, and it
  should be **labelled differently in the UI**.
- **Generation is per-connection and expensive** (one model call per table, four
  concurrent, minutes) with **no incremental mode**. Add three tables to a
  200-table database and there is no "generate just these".
- **The glossary and synonyms never reach retrieval.** They are rendered into
  the *generate* prompt (when they render at all), not used to *find* tables —
  which is where a synonym would do the most good, per §1.2.

### Why this still ranks

The semantic layer is the answer to "why is DataMind better than piping a schema
into an LLM". Until 2026-08-30 that answer did not survive contact with the
code; now it reaches the model, and what is left is the harder half. A layer
that renders correctly is a *starting* position, not a moat: it is still one
hand-edited blob per connection, its metrics are still advisory, and nothing in
the product writes into it. Treat it as the durable knowledge store the learning
loop in §1.1 writes into — that is what turns a one-time curation exercise into
something that compounds.

---

## 1.4 One question, one query — there is no *analysis*

### The structural limit

The chat pipeline is `route → retrieve → describe → clarify → generate →
validate → execute → inspect → present → chart`. It is linear with one bounded
repair loop, and it produces **exactly one `SELECT`**. Everything DataMind can
answer must be expressible as a single statement whose result a model then
describes in two or three sentences.

The SQL surface itself is not the constraint — `sqlguard/policy.py` allows
`WITH`/CTE, `Window`, `Union`/`Intersect`/`Except`, `Lateral` and subqueries, so
a single statement can be genuinely sophisticated. **The missing thing is
iteration and computation between queries.**

### What that rules out

Everything a business user actually asks after the first question:

| Question | Why it fails today |
|---|---|
| "Why did revenue drop in March?" | needs decomposition across dimensions, contribution scoring, several queries |
| "How does this quarter compare to the same quarter last year, by segment?" | expressible in one statement, but only just — and the *interesting* part is the commentary on which segments moved |
| "Forecast next quarter" | no compute step; SQL cannot do this and no model should be asked to |
| "Which customers look like they're about to churn?" | needs a model or at minimum a scoring pass over a result |
| "Is that difference significant?" | needs statistics over the result rows |
| "Find anything unusual in last week's numbers" | no notion of a scan; nothing to iterate over |

There is a deliberate and correct rule that **no model is asked to do
arithmetic** — `plan_kpi` computes headlines, `reports/facts.py` computes what a
paragraph needs, `reports/checks.py` flags unsupported figures. That rule is
right and must survive MVP2. But it currently means arithmetic *beyond what
those three modules hard-code* simply does not happen. There is no general
compute step.

`inspect` is worth noting as the closest thing to an answer-quality check, and
its design is good — checks are **structural** (SQL + snapshot + result shape,
never a result value), so they cost no tokens and behave identically under every
disclosure policy. But by construction they cannot catch "this query is valid,
well-shaped, and answers the wrong question."

### The shape of the fix

A **bounded analysis loop** — the model may issue up to *N* guarded queries for
one question, and a deterministic compute step (DuckDB or pandas, in-process,
over already-returned rows) sits between them. This is the largest architectural
change in this document and it must not be taken lightly:

- Every query still goes through the guard. No exemptions, no fifth entry point
  that is special.
- Disclosure applies to **every intermediate result**, not just the last one.
- The step trail — a valued feature, per CLAUDE.md — must show every query, or
  the feature becomes the black box the whole product is designed not to be.
- It needs a hard budget: max queries, max total rows, max wall-clock. The
  existing `RUN_DEADLINE_SECONDS` is a start, not enough.
- Latency goes from 5–60s to potentially minutes, which likely means this is
  a **second run mode** ("deep dive"), not a change to how chat answers a simple
  question. Genie and Wren both ship exactly this split.

---

## 1.5 Single-player by construction

### The scope

Every resource is `owner_id`-scoped, with no exceptions:
`dashboard_service.list/get/create/update/delete` all filter on `owner_id`;
so do connections, reports and conversations. The role enum is two values,
`ADMIN | MEMBER`. There are no teams, no workspaces, no groups, no
resource-level grants.

The consequences:

- **A dashboard cannot be shown to anyone.** Not read-only, not by link, not to
  a named colleague. The export/import feature is the only sharing mechanism and
  it is a file you email — the recipient must own a connection, re-point every
  tile, and gets a fork that never receives updates.
- **A report cannot be delivered.** Print to PDF from the browser is the whole
  distribution story.
- **The semantic layer cannot be curated by a team.** One person's connection,
  one person's document.
- **No comments, no annotations, no @mentions, no certification badge.**

### Why the deferral, and why it should end

The deferral is well-argued in [architecture.md](architecture.md): *"User B
would read data pulled with user A's credentials, against a connection B does
not own. That is an authorization model, not a UI feature."* That is exactly
right and it is why sharing should not be bolted on.

But the stated trigger — "there is a real answer for *who may read through this
connection*" — is not a thing that arrives on its own. **It is MVP2 work.** BI is
a team sport. A dashboard nobody else can see is a personal notebook, and the
product's own README leads with "business intelligence for the people who have
the questions, not the SQL" — those people do not each own a database
credential.

The minimum honest answer:

1. **A connection declares who may read through it** — a grant list, or a
   workspace the connection belongs to. Explicit, auditable, revocable.
2. **A shared dashboard or report executes under the connection's grant**, not
   under either user's session, and re-checks it at every execution — the same
   posture `execute_saved_sql` already takes with the schema snapshot.
3. **Row-level security is the harder half and should be scoped out of MVP2
   deliberately, not silently.** Power BI, Wren AI (commercial) and Databricks
   all have per-user row filtering. Doing it properly means the connector port
   grows a per-request identity, and `QueryExecutor.execute` taking no bind
   parameters (§1.9) makes it worse. Say "not yet" in the docs and mean it.

### The audit hole

`app/infra/db/models.py:940` defines an `audit_logs` table. **Nothing writes to
it** — `grep -rn "AuditLog" backend/app/` returns the class definition and
nothing else.

So a product whose README's second section is titled "Two things are never left
to the model", and which correctly makes the disclosure policy visible at ask
time, **cannot answer "who asked what, against which connection, under which
policy, and what data reached the provider?"** That question is asked in every
security review of every BI tool, and the table to answer it is already sitting
in the schema, empty. This is a small piece of work with disproportionate value
to the product's own positioning.

---

## 1.6 The interaction model is a transcript

### What chat is today

A linear thread pinned to one connection and one model, with pickers that lock
once the transcript is non-empty. History is the **last 6 messages**
(`run_service._recent_history`, `limit: int = 6`), filtered to the bound
connection. Answered turns suggest follow-ups. Each answer carries prose, the
generated SQL, a table, and a chart whose type can be re-picked without
re-querying.

That last detail is genuinely good and worth keeping visible: redrawing costs no
model call and re-runs no query, because "the rows a chart is drawn from must be
the rows the prose above it was written from."

### The four limits

**Context falls off a cliff at six messages.** No summarization
(`architecture.md` defers "rolling conversation summaries"). A long analytical
session forgets its own beginning, silently, with no indication in the UI. The
deferral's stated trigger — "a thread outgrows the last-six-messages window" —
happens on approximately the seventh message.

**There is no branching.** This is the exact problem the Data Formulator papers
name: *"Questions evolve as you explore. Each answer can lead to follow-up
questions… A long chat history makes it hard to see where you are and how you
got there."* Real analysis is a tree — try a cut, back up, try another, compare
them side by side. A transcript can only be a list. Today, backing up means
scrolling, and comparing two analyses means two browser tabs.

**There is no direct manipulation of a result.** You may re-pick a chart type.
You may not: drag a field to an axis, add a filter, change the grouping, swap a
measure, pivot, sort by a different column, or drill in. Every one of those
requires composing an English sentence and paying for a full pipeline run — four
to five sequential provider calls, 5–60 seconds — to change something the user
could have expressed in one drag. This is precisely the gap Data Formulator's
research identifies: *"natural language can be quite universal, but it can be
verbose for describing the visualization intent and it may not be very
precise."*

**Exploration and publishing are disconnected.** `architecture.md` lists
`"add to dashboard" from a chat run` under *not built, on purpose*. So the
natural workflow — explore in chat until you find the number that matters, then
keep watching it — requires the user to *re-create the tile from scratch* in the
tile editor, re-asking the same question and hoping for the same SQL. The
statement is already sitting in `generated_queries`, already guard-validated,
already bound to a connection. Wiring it to a tile is a small piece of work that
closes the product's biggest workflow hole.

---

## 1.7 The data surface is narrow, in both directions

### Getting data in

**Four engines**: PostgreSQL, MySQL, SQL Server, Oracle. All four require an
existing server, a network route, and a read-only account. For comparison, Wren
AI advertises 20+ sources including BigQuery, Snowflake, ClickHouse, Redshift,
Databricks and DuckDB.

What that excludes matters more than the count:

- **No cloud warehouses.** Snowflake, BigQuery, Databricks and Redshift are
  where analytics data actually lives in 2026. A BI tool that cannot read them
  is a tool for the operational database, which is not usually where the
  business questions are.
- **No file upload.** No CSV, no Excel, no Google Sheets. The single most common
  first-run experience in every competitor — *drag a spreadsheet in and ask it
  something* — is unavailable. This is also the cheapest possible acquisition
  funnel and the fastest demo. Data Formulator accepts CSV, TSV, Excel, JSON and
  even screenshots.
- **No cross-connection queries.** A conversation is pinned to one connection by
  `_bind_connection`, for a good disclosure reason. But "revenue from Postgres
  against the campaign list in this spreadsheet" is a real question with no
  answer here.
- **Schema sync is manual and total.** A button, on a connection, that reads
  everything. No scheduled re-sync, no incremental sync, no notification that a
  table changed shape. Semantic drift is detected in the UI
  (`semantic-drift.ts`) but only when someone opens the editor.

### Getting data out

**There is no result export.** `grep` for csv/xlsx/download across
`backend/app` and `frontend/src` finds exactly one thing: dashboard document
export/import (`dashboard-transfer.tsx`), which carries **layout and SQL and
nothing else — no results by design**.

So a user who has the table they wanted cannot get it into a spreadsheet. They
will screenshot it. A report can only leave as browser print-to-PDF. There is no
API for a third party to fetch an answer, no embed path, and no MCP server —
while Wren AI ships an MCP server and SDKs, and Databricks ships both a
Conversation API and a managed Genie MCP server precisely so other agents can
ask questions of the warehouse.

That last one deserves emphasis. **DataMind is an ideal MCP server and does not
expose one.** The whole product is "a natural-language question becomes a
validated, contained, disclosure-gated query." That is a *tool*, in the agent
sense, and it is exactly what every agent framework in 2026 is looking for. The
guard is the reason it would be safe to expose. Shipping `ask(question,
connection) → {answer, sql, rows}` over MCP is a small piece of work on top of
machinery that already exists, and it is the highest-leverage distribution move
available.

---

## 1.8 Nobody can see the system running

Seven frontend pages: Login, Chat, DataSources, LlmProviders, Users, Dashboards,
Reports, About. **None of them is an operations view.**

What is measured but never shown:

- `runs.prompt_tokens` / `runs.completion_tokens` are recorded per run.
  `litellm_gateway.estimate_cost_usd` exists. **`cost_usd` is a column on
  `eval_results` only** — not on `runs` — and nothing surfaces spend anywhere.
  Nobody can answer "what did last month cost?"
- `runs.llm_latency_ms` / `db_latency_ms` / `total_latency_ms` are recorded.
  No p50/p95 anywhere.
- `error_code` and the guard's `rule_id` are recorded. **Nobody can see which
  questions are failing, or which guard rules fire most** — which is the single
  most useful signal for improving the semantic layer, and it is being thrown
  away.
- No rate limiting, no per-user quota, no concurrency cap per user.
  `MAX_CONCURRENT_RUNS` is global. A user in a loop can exhaust an API key.

And **chat results are not cached at all**. Dashboards have a real
Postgres-backed cache with a fingerprint over `(connection_id, sql, max_rows,
chart_config)` — good design, correctly excluding `table_config` so renaming a
column header does not hit the customer's database. Chat has nothing. Two people
asking the same question one minute apart pay twice, in tokens and in database
load.

---

## 1.9 Nothing happens unless someone is looking

DataMind is entirely **pull**. Three surfaces, all of which require a human to
initiate:

- Chat: a person types.
- Dashboards: a person has the tab open. The scheduler is one
  `setInterval(1000)` per open dashboard that pauses on `document.hidden` —
  well-built, and it means a closed dashboard refreshes nothing.
- Reports: a person clicks Generate. Scheduled report generation is explicitly
  deferred.

There are no alerts, no thresholds, no anomaly detection, no subscriptions, no
digests, no email, no Slack. Meanwhile the market has moved decisively the other
way: Power BI has data alerts on KPI/gauge/card tiles plus subscriptions that
respect row-level security; Tableau Pulse ships daily metric digests as its
headline feature; the 2026 BI commentary is uniformly about "proactive" and
"agentic" analytics that surfaces things nobody asked for.

**And `TEXT` tiles plus `METRIC` tiles are already 80% of an alert.** A `METRIC`
tile is a query returning one row and one numeric column, drawn big, with rules
(`METRIC_SQL_RULES`) that already produce a time series so it can carry a delta
and a sparkline. An alert is that tile plus a threshold plus a delivery channel.
The hard part is built.

**Related and separately painful: dashboards have no filters.** The reason is
architectural and honest — `QueryExecutor.execute` takes no bind parameters, and
the deferral note is emphatic that filters must **never** be done by string
interpolation. Correct. But that means a dashboard is a fixed set of fixed
queries: no date-range picker, no region selector, no drill-through, no
cross-filtering between tiles. Extending the port across four connectors to
carry real bound parameters is the unlock, and it is also a prerequisite for
Genie-style parameterized trusted queries (§3.1) and for row-level security
(§1.5). **Three separate MVP2 features are blocked on the same piece of work**,
which makes it much higher priority than its own deferral note suggests.

---

## 1.10 The quality gates are softer than the posture

The documentation's posture is exacting. The enforcement is not, in three
places, and each one is a place where a regression can ship silently:

| Gate | State | Consequence |
|---|---|---|
| `mypy` | configured `strict`, runs `\|\| true` in CI | a green tick is not a type-clean tree; strict is being adopted module-by-module with no visible progress metric |
| `npm test` | **not in CI** | the nine DOM-free logic suites — schedule, format, dashboard document, palette, chat format, report document, report readiness, print, semantic drift — are exactly the pure logic most likely to regress unnoticed, and only a human running them locally catches it |
| `npm run lint` | dead script; eslint is not a devDependency and there is no config | fails everywhere; the frontend has no linter at all |

Two further consistency problems worth naming because both cost real time:

- **`runs.prompt_version` lies.** `run_service` stamps it from
  `settings.prompt_version` (default `"v2"`), a *separate* string nobody updates
  from the `PROMPT_VERSION` constant (now `"v8"`). They diverged on 2026-07-26.
  **Every run and every eval row written since then claims v2.** Any comparison
  across phases is meaningless without hand-annotation. Fixing it reclassifies
  historical rows, which is why it needs a deliberate decision — but shipping
  MVP2 on top of a lying version field guarantees the MVP2 evaluation is
  unreadable too. **Fix it before the first MVP2 measurement, not after.**
- **The bootstrap admin address is inconsistent** across `core/config.py`,
  `docker-compose.yml`, `.env.example` and `seed_demo_dashboard.py`
  (`admin@raymand.local` vs `admin@raymand.com`). Small, but it is the first
  thing every new user touches.

None of these is architectural. All are cheap. Together they are the difference
between a codebase that *claims* rigour and one that *has* it — and MVP1 has
earned the second description everywhere except here.

---

## 1.11 Appendix: smaller ceilings, listed not argued

Real, but not worth a section each.

**Charts** — eight types (`line, bar, area, scatter, pie, heatmap, histogram,
combo`). No maps/geo, funnel, waterfall, box plot, treemap, gantt, sankey, or
small multiples. No in-table sparklines or conditional formatting. No chart
interactivity beyond Vega tooltips: no brush-and-link, no click-to-filter, no
zoom. Data Formulator advertises 30+ types. Note that the palette is *measured*
(OKLab ΔE, CVD simulation, contrast per mode) and re-checked by
`palette.test.ts` — adding types is fine, adding a free hex picker would destroy
that silently.

**Reports** — no scheduling, no delivery, no server-side PDF (browser print
only), no template library, no diff between two runs of the same report, no
DOCX/PPTX export. Languages are Persian and English only (`ReportLanguage`),
derived from the request rather than asked — a good design that simply has two
values.

**Auth** — email/password only. No SSO, no OIDC, no SAML, no SCIM, no MFA. Two
roles. Fine for MVP1; a blocker for the first enterprise conversation.

**Oracle identifier case** — documented and deliberately unfixed: a table created
as `CREATE TABLE "Orders"` folds onto the same key as a plain `ORDERS` beside
it, the later one wins, and a metric over the loser validates then fails at
execution with ORA-00904.

**Naming** — the product is *DataMind*, the Python package is still `raymand`,
as are the compose project and the app database. Deliberately deferred; worth
doing before an open-source push, and never incidentally.

---

# Part 2 — What the other four do

Desk research, August 2026. Sources are listed at the end of each subsection.

## 2.1 Microsoft Data Formulator

Open-sourced by Microsoft Research; the most *interesting* of the four, and the
least like DataMind. It is not a BI platform — it is a research answer to the
question **"what should the interface for AI-assisted analysis actually be?"**
and its answer is: not a chat window.

**The problem it names.** From the DF2 paper and the 0.7 release notes:
questions evolve as you explore; each answer leads to follow-ups; *"a long chat
history makes it hard to see where you are and how you got there"*; and
critically, *"natural language can be quite universal, but it can be verbose for
describing the visualization intent and it may not be very precise."* Both
observations apply to DataMind's chat exactly as written.

**Data threads.** A structured record of every question, intermediate finding
and chart in a session, presented so long sessions stay navigable. Users
**revisit earlier steps, branch into alternative analyses, and compare them side
by side without losing context**. This is a tree, not a transcript, and it is
the single most transferable idea in this document.

**Blended UI + NL.** Chart encoding shelves (drag a field to X, to Y, to colour)
combined with natural language, *"so that users can specify their design both
precisely and concisely."* The killer move: **you may drag a field that does not
exist in the data.** Name a concept the data does not contain and the AI
generates the transformation code to produce it. NL is used for what NL is good
at (describing a derived concept) and direct manipulation for what it is good at
(saying precisely where things go).

**Transparency and control.** *"Agents can be difficult to control if they are
working in a black box."* DF generates **verifiable, reproducible code for every
result** and shows it. DataMind already shares this instinct — the SQL panel and
the live step trail are the same commitment.

**Reach and composition.** Accepts CSV, TSV, Excel, JSON, screenshots and text
as well as databases; connectors maintain a "data memory" of relationships
between sources; results compose into a shareable report; 30+ chart types with
an AI style-refinement pass over an interactive canvas (adjust labels,
annotations, layout, colour, emphasis by describing the change).

> Sources: [Data Formulator (GitHub)](https://github.com/microsoft/data-formulator) ·
> [Data Formulator 0.7 (MSR blog)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/) ·
> [Data Formulator 2 (paper)](https://www.microsoft.com/en-us/research/publication/data-formulator-2-iteratively-creating-rich-visualizations-with-ai/) ·
> [Foundry Labs](https://labs.ai.azure.com/innovations/data-formulator/)

## 2.2 Wren AI

The closest competitor by architecture and philosophy — open source (Apache-2.0
core), self-hostable, semantic-layer-first, provider-agnostic. If DataMind has a
direct rival, this is it, and studying what they made *explicit* is instructive.

**MDL — the semantic layer as a version-controlled artifact.** A JSON-based
Modeling Definition Language encoding models, columns, relationships, views, and
**cubes and metrics as approved reusable definitions**. Their framing is the
important part: business meaning and approved definitions are captured as *"a
reviewable, version-controlled semantic layer (MDL), not buried in prompts."*
DataMind's layer is the same idea stored as an editable blob — same content,
none of the reviewability.

**The context layer is three files, not one.** `MDL` (structure) +
`instructions.md` (unstructured company knowledge) + `queries.yml` (example
queries). Plus a local LanceDB memory index with hybrid retrieval. **DataMind
has the first and neither of the other two**, and the other two are where the
learning loop lives.

**Validation before execution.** Wren does not hand generated SQL straight to
the database: the engine validates against the semantic model, resolves
business-level references to physical schema, and transpiles to the correct
dialect — plus dry-plan validation, row limits, and **structured errors with
hints**. Architecturally very close to DataMind's guard + snapshot resolution.
The differences worth noting: they resolve *business* references (a query can
name a metric, not a column) and their errors carry repair hints.

**Interaction.** Text-to-SQL and text-to-chart; automatic selection of the most
relevant visualization; follow-up instructions that adjust an existing chart
(*"make the bar chart yellow"*); **"Recommend a few questions to ask next"**;
and — most relevant to DataMind's `clarify` node — when a question is vague,
Wren **suggests several more specific questions to choose from** rather than
either guessing or asking an open question. Pin a chart straight to a dashboard,
no re-query.

**Reach.** 22+ data sources via Apache DataFusion/DuckDB. AI-powered
spreadsheets and 100+ pre-built cross-platform metrics. Sandboxed multi-step
reasoning where agents query, chart, extract from PDFs, build dashboards and
**save skills**. Browser-side GenBI apps compiled to WASM (`wren-core-wasm`),
deployable to Vercel or Cloudflare Pages by CLI. An **MCP server**, agent skills,
and SDKs (`wren-langchain`, `wren-pydantic`). An **eval runner for golden
datasets** — same instinct as `app/eval/`. An "AI Advisor" surface for
improving a project.

**What they charge for**, which is a useful read on where the commercial line
sits: row- and column-level security, access control, the GenBI UI, embedded
APIs, advanced audit, support.

> Sources: [WrenAI (GitHub)](https://github.com/Canner/WrenAI) ·
> [Why the semantic layer is essential for reliable text-to-SQL](https://www.getwren.ai/post/why-the-semantic-layer-is-essential-for-reliable-text-to-sql-and-how-wren-ai-brings-it-to-life) ·
> [Wren AI OSS](https://www.getwren.ai/oss) ·
> [Asking questions (docs)](https://docs.getwren.ai/cp/guide/home/ask) ·
> [AI-powered spreadsheets](https://getwren.ai/post/introducing-wren-ais-new-ai-powered-spreadsheets-pre-built-100-cross-platform-metrics)

## 2.3 Databricks AI/BI Genie

The most mature **trust and governance** story of the four, and the richest
source of ideas for §1.1. Everything below is a mechanism for making a
text-to-SQL system reliable *without touching the prompt*, which is precisely
DataMind's missing capability.

**The Genie space / Agent** is a scoped knowledge store over selected tables:
semantics extracted from existing catalog assets, plus curation and feedback
from expert analysts. Scoped by *topic*, not by database — worth noting, since
DataMind scopes by connection.

**Instructions** — plain-text guidance for global business logic and formatting
rules, explicitly *only* for context that fits nowhere else.

**Example SQL queries** — a natural-phrasing question paired with the SQL that
answers it. On an exact match Genie uses the query directly; on a similar
question it uses the example's structure as a guide. Queries may be
**parameterized** (`:param` with declared type and a descriptive comment), which
is what makes one example serve a family of questions.

**Trusted assets** — example queries and Unity Catalog SQL functions that give
verified answers. When Genie answers using a parameterized trusted query, **the
answer is marked "Trusted" in the UI**. This is the single best idea in the
research: a *visible, earned distinction between an answer that used approved
logic and an answer the model improvised.*

**Metric views** carry `COMMENT` fields Genie reads at query time, so governed
metric definitions flow into the model's context the same way table descriptions
do.

**Structured business logic**, separate from free text — **Filters** (named
boolean conditions: "High-value orders" = `orders.amount > 10000`), **Measures**
(named aggregations: "Win rate" = `COUNT(CASE WHEN stage='Closed Won' THEN 1
END) / NULLIF(COUNT(*),0)`), **Fields** (row-level derived attributes: deal
tiers, fiscal periods). Each carries synonyms and instructions.

**Prompt matching** — two features DataMind has no equivalent of:
- *Format assistance* (default on): samples representative column values so the
  model recognises data patterns and formatting.
- *Entity matching*: curated distinct-value lists (up to 120 columns, 1,024
  values each) so **"Florida" resolves to `WHERE state = 'FL'`** instead of
  `WHERE state ILIKE '%Florida%'`. Those same lists become **value
  dictionaries** — drop-downs users filter with.

**Join relationships** — declared conditions with cardinality (many-to-one,
one-to-many, one-to-one); Genie auto-aliases for multi/self-joins. DataMind
derives joins from the catalog, which is better where the catalog is honest.

**Column hiding, row filters and column masks** remove things from the model's
context entirely, preserving governance by construction.

**Knowledge mining** — Genie analyses catalog metadata *and learns from author
feedback* (thumbs-up responses, query downloads) to **suggest new SQL
expressions and join relationships**, incrementally improving the knowledge
store. The system proposes its own improvements.

**Benchmarks** — authors write benchmark questions with the correct SQL. A run
labels each **Correct** (result matches exactly) or **Needs Review**. Accuracy is
tracked over time in an **Evaluations tab**. Recommended practice: include the
most frequent user questions plus 2–3 phrasings of each. The refinement cycle is
explicit: find failures → add an example SQL query → re-run benchmarks → watch
the number move.

**Ask for Review** — an end user clicks a request icon on an answer they want
verified and adds a comment. The admin sees prompt, generated SQL and comment in
a History page, and marks the SQL correct or corrects it. **The user is
notified.** A complete human-in-the-loop trust workflow.

**Limits**, useful as sizing guidance: 100 instructions total (queries +
functions + text blocks); 200 knowledge-store snippets.

**Distribution** — Conversation APIs (stateful, multi-turn, with history) for
embedding in Slack, Teams, SharePoint or any app; Management APIs for CI/CD;
and a managed **Genie MCP server** exposing Genie as a conversational tool with
deep links back to cited sources.

> Sources: [Genie docs](https://docs.databricks.com/aws/en/genie/) ·
> [Tune Genie quality](https://docs.databricks.com/aws/en/genie/tune-quality) ·
> [Trusted assets](https://learn.microsoft.com/da-dk/azure/databricks/genie/trusted-assets) ·
> [Benchmarks and Ask for Review](https://www.databricks.com/blog/building-confidence-your-genie-space-benchmarks-and-ask-review) ·
> [Genie GA announcement](https://www.databricks.com/blog/aibi-genie-now-generally-available) ·
> [Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api) ·
> [Genie One MCP server](https://docs.databricks.com/aws/en/agents/mcp-tools/genie-mcp)

## 2.4 Power BI (and Fabric Copilot)

The incumbent. Not a model to copy wholesale, but the source of the **enterprise
table stakes** list — the features whose absence ends a procurement
conversation.

**Copilot, standalone.** A full-screen chat that answers across *any* report,
semantic model or Fabric data agent the user can access — as opposed to the
in-report pane. Users **attach a report or semantic model as a grounded
reference** (`+` or `/` to search and select). Copilot generates whole reports
from natural language, writes DAX, and explains anomalies.

**"Approved for Copilot."** A semantic model is explicitly marked AI-ready —
defined relationships, business-friendly field names, and **AI instructions set
within the model**. Answers from an approved model **skip the friction/warning
treatment** shown for unapproved ones, and admins can restrict Copilot to
approved items only. Same "earned trust, visibly different in the UI" pattern as
Genie's Trusted badge.

**Fabric data agents.** Developers build and train agents that are experts in a
specific topic or dataset; standalone Copilot **routes** questions to the right
one. A federation-of-specialists model rather than one agent over everything.

**Enterprise table stakes**, the reason this section exists:
- **Row-level security** — access to rows controlled by group membership, and
  applied when content is embedded.
- **Data alerts** — on KPI, gauge and card tiles; **alerts respect RLS** and
  evaluate only data the user may see.
- **Subscriptions** — scheduled delivery that applies the *recipient's* data
  scope by identity under RLS.
- **Drill-through, drill up/down, bookmarks**, cross-filtering.
- **Paginated reports** for structured printable output (invoices, statements).
- **Embedded analytics** with RLS, as a first-class product.
- **Q&A** — natural-language questions rendered as visuals.
- **Metrics/goals** — tracked KPIs with targets and status.

> Sources: [Copilot for Power BI overview](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-introduction) ·
> [Standalone Copilot](https://learn.microsoft.com/en-us/power-bi/explore-reports/copilot-chat-with-data-standalone) ·
> [Row-level security](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security) ·
> [Power BI January 2026 feature summary](https://powerbi.microsoft.com/en-us/blog/power-bi-january-2026-feature-summary) ·
> [Data alerts & subscriptions](https://powerbiconsulting.com/blog/power-bi-data-alerts-subscriptions-notification-2026)

## 2.5 Worth knowing: ThoughtSpot and Tableau Pulse

Not on the brief, but they own two concepts the other four do not.

**ThoughtSpot SpotIQ** — automated insight discovery. Analyses large numbers of
data combinations to surface anomalies, trends and correlations *nobody asked
about*. Its **change analysis** automatically identifies which dimensions,
segments or filters contributed most to a KPI movement, iteratively drillable —
Prophet-based models plus change analysis across dimensions. This is the
"why did revenue drop?" capability from §1.4, productised.

**Tableau Pulse** — a proactive metric layer that monitors KPIs and delivers
**personalised daily digests**: is this metric up, down, or anomalous, in plain
language. The 2026 framing across the industry is consistent — the shift from
*passive answer engine* to *active analytical assistant*.

> Sources: [SpotIQ](https://www.thoughtspot.com/product/analytics/spotiq) ·
> [Root-cause analysis](https://www.thoughtspot.com/data-trends/analytics/root-cause-analysis) ·
> [BI tools for AI anomaly detection (2026)](https://www.basedash.com/blog/best-bi-tools-for-ai-anomaly-detection-and-smart-alerting-2026)

## 2.6 The matrix

`●` present · `◐` partial · `○` absent

| Capability | DataMind | Data Formulator | Wren AI | Genie | Power BI |
|---|:--:|:--:|:--:|:--:|:--:|
| **Accuracy & trust** | | | | | |
| Semantic layer / metrics | ◐ *(a blob, advisory, §1.3)* | ○ | ● MDL | ● measures/filters | ● model + AI instr. |
| Verified/example Q→SQL pairs | ○ | ○ | ● `queries.yml` | ● | ◐ |
| "Trusted / verified" badge on an answer | ○ | ○ | ◐ | ● | ● approved-for-Copilot |
| Free-text instructions | ◐ *(layer only)* | ○ | ● `instructions.md` | ● | ● |
| Entity / value matching | ○ | ○ | ● profiling | ● | ◐ |
| In-product benchmarks + score | ○ *(dev CLI)* | ○ | ● eval runner | ● Evaluations tab | ○ |
| User→author review workflow | ○ | ○ | ◐ | ● Ask for Review | ◐ |
| Learns from feedback | ○ | ○ | ● | ● knowledge mining | ◐ |
| **SQL safety** | | | | | |
| Static AST validation, fail-closed | ● **best in class** | n/a | ● dry-plan | ◐ | n/a |
| Read-only proven at the engine | ● | ○ | ◐ | ◐ | n/a |
| Explicit disclosure policy to the LLM | ● **unique** | ○ | ○ | ○ | ○ |
| **Interaction** | | | | | |
| Conversational chat | ● | ● | ● | ● | ● |
| Branching / non-linear threads | ○ | ● **data threads** | ◐ | ○ | ○ |
| Drag-and-drop encoding + NL | ○ | ● **concept shelf** | ○ | ○ | ● (manual) |
| AI-derived fields not in the data | ○ | ● | ◐ | ◐ | ● DAX |
| Direct manipulation of a result | ◐ *(chart type)* | ● | ◐ | ◐ | ● |
| Drill-down / cross-filter | ○ | ◐ | ◐ | ◐ | ● |
| Chart → dashboard in one click | ○ | ● | ● | ● | ● |
| Chart types | 8 | 30+ | ~10 | ~10 | 30+ |
| **Analysis depth** | | | | | |
| Multi-step / iterative agent | ○ | ● | ● sandboxed | ● | ◐ |
| Compute beyond SQL | ○ | ● code gen | ● | ◐ | ● DAX/Python |
| Root-cause / key drivers | ○ | ◐ | ◐ | ◐ | ● (+ SpotIQ) |
| Forecasting / anomaly detection | ○ | ◐ | ○ | ◐ | ● |
| **Documents & delivery** | | | | | |
| Narrative report generation | ● **strong** | ◐ | ◐ | ○ | ◐ |
| Human-approved outline gate | ● **unique** | ○ | ○ | ○ | ○ |
| Figures computed, never generated | ● **unique** | ◐ | ○ | ○ | n/a |
| Scheduled generation / delivery | ○ | ○ | ◐ | ◐ | ● |
| Alerts on thresholds | ○ | ○ | ○ | ◐ | ● |
| **Collaboration** | | | | | |
| Share a dashboard / report | ○ | ◐ | ● | ● | ● |
| Teams / workspaces | ○ | ○ | ● | ● | ● |
| Row-level security | ○ | ○ | ● (paid) | ● | ● |
| Audit log | ○ *(table exists, unused)* | ○ | ● (paid) | ● | ● |
| **Reach** | | | | | |
| Data sources | 4 | many + files | 22+ | Databricks | many |
| File upload (CSV/Excel) | ○ | ● | ● | ○ | ● |
| Result export (CSV/Excel) | ○ | ● | ● | ● | ● |
| Public API | ○ | ◐ | ● | ● | ● |
| MCP server | ○ | ○ | ● | ● | ◐ |
| Embedded analytics | ○ | ○ | ● | ● | ● |
| SSO / OIDC / SAML | ○ | n/a | ● | ● | ● |

**Reading the matrix.** DataMind's four `●`-with-emphasis rows are all in the
"never trust the model" family — AST validation, proven read-only, the
disclosure policy, and the report guarantees (approved outline, computed
figures). **Nobody else has those.** They are a real, defensible position and
MVP2 must not trade any of them away. The `○` column is almost entirely
elsewhere: learning, interaction, depth, collaboration, reach.

---

# Part 3 — Concepts to build

Grouped by theme. Each carries what it is, why it fits *this* codebase, roughly
how it would work here, and a rough size. Sizes are **S** (days), **M** (1–3
weeks), **L** (a month or more).

## Theme A — Close the learning loop
*The spine of MVP2. Everything here serves §1.1.*

### A1. Verified question→SQL pairs · **M** · ⭐ highest value in the document
*From: Genie example SQL queries / trusted assets; Wren `queries.yml`.*

A new per-connection store of `(question, sql, note, verified_by, verified_at)`.
Written from four places, all of which already exist:
- an answered chat run — "this was right, save it";
- a corrected chat run — the user or owner edits the SQL and saves;
- an existing dashboard tile or report block whose `sql_origin` is
  `HANDWRITTEN` or `GENERATED_EDITED` — **these are already verified statements
  sitting in the database, unused**;
- typed directly in a new editor beside the semantic layer.

Read in `retrieve`: pull the *k* most similar pairs and pass them to `generate`
as few-shot examples through `RetrievedContext`, which is already the seam the
generator sees. On a near-exact match, offer the stored SQL directly with a
visible "this is a saved verified answer" affordance — costing zero model calls
and returning in database time.

*Why it fits:* every guarantee is preserved. A pair is hostile input like any
other and goes through the guard on execution, exactly as a tile does — this is
a **fifth entry point that gets no exemption**, and the hostile corpus must be
replayed through it (`test_verified_pairs_guard.py`, mirroring
`test_report_guard.py`).

*Depends on:* nothing. Can start immediately.

### A2. "Verified" as a visible property of an answer · **S**
*From: Genie's Trusted badge; Power BI's approved-for-Copilot friction.*

Three tiers, shown in the chat header and on every tile and figure:

| Tier | Means |
|---|---|
| **Verified** | answered from a stored verified pair, or by a metric's exact SQL |
| **Grounded** | generated, but every table it touched has a semantic-layer entry |
| **Generated** | generated against bare schema — *say so* |

*Why it fits:* it is the same instinct as showing the disclosure policy at ask
time. DataMind already tells the user what leaves; this tells them what backs
the answer. It is also the cheapest possible trust upgrade and it makes A1's
value legible to the person deciding whether to invest in curation.

### A3. Benchmarks and a score, in the product · **M**
*From: Genie benchmarks + Evaluations tab; Wren's eval runner.*

Promote `app/eval/` from a developer CLI to a per-connection feature. A
benchmark set is a list of `(question, expected_sql)` — **A1's verified pairs are
already exactly this shape**, so the two features share a table. Run the set
against a connection; label each **Correct** (result set matches) or **Needs
review**; show accuracy over time.

*Why it fits:* it makes accuracy the customer's number instead of the
developer's, which is the only way curation gets done. It also gives the
semantic-layer switch (`semantic_layer_enabled`) a purpose a user can see: A/B
your own layer and watch the score move.

*Prerequisite:* fix `runs.prompt_version` (§1.10) first, or every measurement
taken during MVP2 will be unreadable.

*Caution:* keep the import-linter contract that keeps `app.eval` off the request
path. This is a *new* service that reuses eval's scoring, not eval moving up the
stack.

### A4. Ask-for-review workflow · **S–M**
*From: Genie's Ask for Review.*

A flag button on any answer, with a comment. It lands in a review queue for
whoever owns the connection, showing question, generated SQL, and the comment.
The owner marks it correct, or corrects the SQL — **and a correction becomes an
A1 pair automatically**. The asker is notified.

*Why it fits:* it turns a dead-end frustration into the highest-quality training
signal the system can get, sourced from exactly the people who know the answer.
It also gives the connection owner a *reason* to open the semantic-layer editor.

### A5. Business-term and synonym index, used at retrieval · **S**
*From: Genie column synonyms; Wren `instructions.md`.*

The semantic layer already holds a glossary and business names. Today they
render into the *generate* prompt and nowhere else. Also index them for
`retrieve`, so "churn" finds `subscription_events` because someone wrote that
down once.

*Why it fits:* it is the cheapest fix for §1.2's core failure, it makes the
semantic layer pay off in a second place, and it needs no new data model.

### A6. Fix the semantic layer render · **S** · done 2026-08-30
*Not a new feature — the precondition for Theme A having any effect.*

`render_semantic` now fits the block **line by line** to the remaining budget
instead of popping whole sections, in three priority tiers filled round-robin
across the retrieved tables (grain → metrics → column meanings). The old trim
test (one table, `max_chars=250`, asserted only "short") is replaced by tests
that assert *which* content survives at 42 tables under the real cap, and by one
that pins coverage to what the block actually said — entities render partially
now, so `render_with_coverage` returns the block and its coverage from one fit.
See §1.3 for the measurements and [CLAUDE.md](../CLAUDE.md#the-semantic-layer)
for the rule.

*Still outstanding:* `PROMPT_VERSION` moved v7 → v8, which is the point — **take
the fresh baseline**. The recorded `sales_v1` runs are not invalidated (they ran
with the layer off, so their bytes are untouched), but the layer's A/B has never
been run against a prompt containing the layer. That is MVP2's day-zero
measurement and it does not exist yet; the runner still needs a way to pass
`NodeDeps.semantic` before it can be taken.

## Theme B — Fix retrieval
*Serves §1.2. Theme A's pairs and synonyms are useless if the right tables never reach the prompt.*

### B1. Un-blind the eval · **S** · ⚠️ blocking
Run the suite at a lowered `_RETRIEVE_BUDGET_CHARS` (or widen the fixture) so
recall stops being 1.0 by construction. **Nothing else in this theme can be
evaluated until this is done.** Record the decision in
`suites/CHANGELOG.md` — post-change recall numbers are not comparable to
pre-change ones and someone will try.

### B2. Hybrid retrieval behind `RetrievedContext` · **M**
Embeddings over table names + column names + catalog comments + semantic-layer
business names and descriptions, blended with the existing exact-match and FK
expansion. `pgvector` in the app database adds no deployment unit; the
`RetrievedContext` seam already exists and the generator "never learns which one
produced its context."

*Measure it:* B1 first, then this, and report the recall delta. The last time
retrieval changed (FK-neighbour expansion) it lifted recall 70→86% with **flat**
execution accuracy — a result worth remembering before over-claiming.

### B3. Entity/value dictionaries · **M**
*From: Genie entity matching + value dictionaries.*

Per-column, opt-in, per connection: capture distinct values for low-cardinality
categorical columns so "Florida" becomes `WHERE state = 'FL'`. Reuse the
`connectors/hints.py` probe machinery.

⚠️ **This is a disclosure decision, not a performance feature.** A value list is
customer data — more so than a column hint. It needs its own explicit control,
its own place in the disclosure ladder, and a section in
[security.md](security.md). Do not fold it silently into `HintBudget`.

*Bonus:* the same dictionaries become dashboard filter drop-downs once C4 lands.

### B4. Rolling conversation summaries · **S**
*Closes the six-message cliff (§1.6).* Summarise older turns into a compact
carry-forward. **The summary is prose written from result rows, so it is subject
to `disclose_history` exactly as an assistant message is** — the existing rule
covers it, and the implementation must go through the same filter rather than
around it.

## Theme C — Make the interaction match how analysis actually works
*Serves §1.6. This is where the product stops feeling like a demo.*

### C1. Pin any chat answer to a dashboard or report · **S** · ⭐ best value-per-day
The statement is already in `generated_queries`, already guard-validated,
already bound to a connection. Wire it to a tile. Wren AI advertises this as a
headline feature ("pin that chart straight to your dashboard — no re-query").

*Why it fits:* it closes the product's biggest workflow hole (explore → keep
watching), it costs almost nothing, and it makes chat and dashboards feel like
one product instead of two.

### C2. Data threads — branching, not a transcript · **L** · ⭐ most differentiating
*From: Data Formulator's core contribution.*

Let a conversation branch. From any answer, fork a new line of enquiry; keep both;
show them side by side. The transcript becomes a tree the user can navigate.

*Why it fits:* real analysis is a tree, and DataMind already stores every run
with its full state, artifacts and event log. **The data model is closer to
supporting this than the UI suggests** — `runs` hang off `conversations` and
carry their own snapshot of connection and model. What is missing is a parent
pointer and a UI that renders a tree.

*Caution:* history assembly (`_recent_history`) walks messages by `seq` on one
conversation. Branching means history is a *path through the tree*, not a
window on a list. That is the real work, and it must preserve the connection
filter that keeps disclosure policies from mixing.

### C3. Direct manipulation of a result · **M**
*From: Data Formulator's encoding shelves; the precision half of the "blended UI"
argument.*

Beside every result: change the grouping, swap the measure, add a filter, sort,
pivot, switch aggregation — **without composing an English sentence and paying
for a full pipeline run.** Two implementations, and the choice matters:

- *(a)* **Re-run modified SQL.** The controls edit the AST of the statement that
  is already there and re-execute through the guard. Exact, cheap, always
  correct, limited to what the statement's tables can express.
- *(b)* **Transform the returned rows client-side.** Instant, no database load,
  but the prose above the chart was written from the *original* rows — which
  breaks the product's own rule that "the rows a chart is drawn from must be the
  rows the prose above it was written from."

**Recommendation: (a).** It preserves the invariant, keeps the guard in the
path, and re-uses `execute_saved_sql`. The tile editor already proves a person
can edit a statement and have it re-validated.

*Stretch, from Data Formulator:* let a user name a field that does not exist and
have the model derive it — but as a **generated, guard-validated SQL expression**
added to the statement, never as opaque transformation code. That framing keeps
the whole thing inside the existing safety model.

### C4. Dashboard filters and parameters · **M** · ⚠️ unblocks three features
Extend `QueryExecutor.execute` across all four connectors to carry **real bound
parameters** — never string interpolation, per the existing deferral note.

*Why the priority is higher than its own deferral suggests:* the same work
unblocks **Genie-style parameterized verified queries (A1)**, **row-level
security (D3)**, and dashboard filters/drill-through. Three features, one port
change.

### C5. More chart types and light interactivity · **S–M**
Maps, funnel, waterfall, box plot, small multiples; in-table sparklines and
conditional formatting; click-a-bar-to-filter. Everything goes through
`plan_chart` — the model proposes, the platform disposes — and **prompt/type
parity is a hard rule**: a type is not added until `CHART_SYSTEM` describes when
to pick it *and* `ResultProfile.describe()` carries the facts that rule is
stated in terms of. Re-run the palette validator **in both themes** before
touching colour.

## Theme D — Make it a team product
*Serves §1.5. Sequenced deliberately: the authorization model first, features on top.*

### D1. An answer to "who may read through this connection" · **M** · ⚠️ blocking
The precondition `architecture.md` names. A connection carries an explicit grant
list (or belongs to a workspace). A shared object executes under the
**connection's** grant, re-checked at every execution — the same posture
`execute_saved_sql` already takes with the snapshot. Nothing is shared until
this exists.

### D2. Sharing, then collaboration · **M**
Read-only share of a dashboard or report to a named user or workspace. Then
comments and annotations on tiles and figures. Then a **certified** badge an
owner can put on a dashboard, mirroring A2's tiering.

### D3. Row-level security · **L** · *scope out of MVP2, deliberately*
Every serious competitor has it. Doing it right means the connector port carries
a per-request identity and predicates are applied server-side. **Depends on C4.**
Say "not in MVP2" in the docs with the trigger written down, rather than leaving
it unmentioned.

### D4. Turn on the audit log · **S** · ⭐ best ratio in the document
The table exists at `models.py:940` and nothing writes to it. Write: who asked
what, against which connection, under which disclosure policy, what SQL ran, how
many rows returned, and — critically — **what reached the model provider**.
Add an admin view.

*Why it fits:* the product's whole positioning is "you decide what leaves your
database." Right now it cannot prove what left. This is a day of work that
retroactively strengthens the strongest claim in the README.

### D5. SSO / OIDC · **M**
`domain/ports/identity` already exists as a port with a local Argon2id+JWT
adapter. A second adapter is the designed-for change. Table stakes for any
enterprise conversation.

## Theme E — Reach
*Serves §1.7. Each item independently widens who can use the product.*

### E1. File upload — CSV / Excel · **M** · ⭐ best acquisition move
*From: Data Formulator, Wren AI, everyone.*

Drag a spreadsheet in, ask it a question. Land it in a per-user DuckDB or a
scratch Postgres schema and it becomes an ordinary connection — **the entire
guard, snapshot, semantic layer and disclosure machinery applies unchanged**,
which is why this is far cheaper than it sounds.

*Why it fits:* it is the fastest demo, the cheapest funnel, and it makes the
product usable by someone with no database credentials at all — which is
literally the persona the README leads with.

### E2. An MCP server · **S–M** · ⭐ highest leverage per line of code
*From: Wren AI and Genie both shipping one.*

Expose `ask(question, connection) → {answer, sql, rows}` over MCP. **DataMind is
an unusually good MCP server and does not expose one:** the guard, the row cap,
the timeout and the disclosure policy are exactly the containment an agent
integration needs, and no competitor's MCP server has an equivalent of the
disclosure policy.

*Also ship:* a plain REST endpoint for the same thing. Today there is no way for
anything outside the SPA to ask a question.

### E3. Result export · **S**
CSV and Excel from any result — chat, tile, report figure. **It is currently
impossible to get a number out of DataMind except by retyping it.** Gate it by
disclosure policy? No — export goes to the *user*, who already sees the rows on
screen; the policy governs what reaches the **model provider**. Worth stating
explicitly in [security.md](security.md) so nobody re-litigates it later.

### E4. Warehouse connectors · **M each**
Snowflake, BigQuery, ClickHouse, Databricks. The `DatabaseConnector` port makes
each one mechanical: implement the Protocol, register in `factory.py`, add the
`DatabaseKind` + dialect + default port, extend `sqlguard` if the dialect needs
it, add to frontend `DATABASE_TYPES`, verify against a real container with a
read-only role. **Prioritise by customer, not by ease.**

### E5. Scheduled and incremental schema sync · **S**
A nightly re-sync per connection, with a notification when a table changes shape
and a diff against the semantic layer. `semantic-drift.ts` already knows how to
tell an all-or-nothing re-key from ordinary drift — it just needs to run without
someone opening the editor.

## Theme F — Depth and proactivity
*Serves §1.4 and §1.9. The most ambitious theme; the most likely to be cut.*

### F1. Metric alerts · **M** · ⭐ best value in this theme
*From: Power BI data alerts; Tableau Pulse.*

A `METRIC` tile plus a threshold plus a delivery channel (email, webhook, Slack).
**The hard 80% is built**: `METRIC_SQL_RULES` already produces a time series so a
big number can carry a delta and a sparkline, and the dashboard scheduler already
knows what is due. What is missing is server-side evaluation (today the scheduler
lives in an open browser tab) and a channel.

*Caution:* server-side evaluation means the "nothing calls a model at refresh
time" guarantee needs restating rather than weakening — an alert evaluates SQL,
not a model, and that must stay true.

### F2. Scheduled reports and digests · **M**
Deferred in MVP1 with the trigger "neither is load-bearing yet." Reports are
DataMind's most distinctive feature and cannot currently be *delivered* to
anyone. A monthly report that generates itself and arrives by email is the whole
point of a report. **Depends on D1** (a delivered report is a shared report).

### F3. Bounded multi-step analysis — "deep dive" · **L**
*From: Wren's sandboxed multi-step reasoning; Genie Agents; Data Formulator's DataAgent.*

A second run mode where the model may issue up to *N* guarded queries and a
deterministic compute step (DuckDB in-process) sits between them. Every query
through the guard, every intermediate result through `disclose()`, every query
visible in the step trail, hard budgets on queries/rows/wall-clock.

*Why a separate mode:* latency goes from seconds to minutes. Chat's 5–60s answer
is a feature and must not regress. Genie, Wren and Power BI all ship this split.

### F4. Root-cause / key-driver analysis · **M**, *given F3*
*From: ThoughtSpot SpotIQ change analysis.*

"Why did revenue drop in March?" → decompose the change across available
dimensions, score contributions, report the top drivers **with the SQL for each**.

*Why it fits DataMind specifically:* contribution analysis is **arithmetic over
result rows**, which is precisely what `reports/facts.py` already does and
precisely what the "no model is asked to do arithmetic" rule demands. The model
picks *which dimensions to test*; the platform computes *what actually moved*.
That is the same "model proposes, platform disposes" split as `plan_chart` and
the SQL guard, applied to a third thing.

### F5. Proactive digests · **M**, *given F1 and F2*
A daily or weekly note per connection: what moved, what looks anomalous, in
plain language, with the SQL behind each claim. Tableau Pulse's headline
feature, and the natural composition of F1 + F2 + F4.

---

# Part 4 — A proposed MVP2

Three tiers. The recommendation is that **Tier 1 is not negotiable and Tier 3 is
mostly not MVP2**.

## Tier 1 — The spine

*Without these, nothing else compounds. Roughly two months.*

| # | Item | § | Size |
|---|---|---|:--:|
| 1 | ~~**Fix the semantic layer render**~~ — **done 2026-08-30** | A6 | S |
| 2 | **Fix `runs.prompt_version`** — before any measurement | §1.10 | S |
| 3 | **Un-blind the eval's recall** | B1 | S |
| 4 | **Verified question→SQL pairs**, retrieved as few-shot | A1 | M |
| 5 | **Verified / Grounded / Generated** tiering on every answer | A2 | S |
| 6 | **Ask-for-review** → correction → pair | A4 | S–M |
| 7 | **Benchmarks and a score**, per connection, in the UI | A3 | M |
| 8 | **Hybrid retrieval** + synonym index | B2, A5 | M |
| 9 | **Turn on the audit log** | D4 | S |
| 10 | **Pin a chat answer to a dashboard** | C1 | S |
| 11 | **Result export** (CSV/Excel) | E3 | S |
| 12 | **CI: `npm test` in, mypy honest or dropped, eslint or delete the script** | §1.10 | S |

Items 1–3 are **blocking** and should land in the first week. Items 4–8 are the
learning loop and are the reason to do MVP2 at all. Items 9–12 are small,
high-ratio, and each closes an embarrassing hole.

**The success criterion for Tier 1 is a single sentence:** *a connection owner
can see their accuracy score, correct a wrong answer, and watch the score go up
— without a developer.* If that sentence is true at the end, MVP2 succeeded.

## Tier 2 — The differentiators

*Pick two or three. Roughly two more months.*

| # | Item | § | Size | Why |
|---|---|---|:--:|---|
| 13 | **File upload (CSV/Excel)** | E1 | M | best acquisition move; reuses everything |
| 14 | **MCP server + REST API** | E2 | S–M | highest leverage per line; DataMind is an ideal MCP tool |
| 15 | **Dashboard filters (bound params)** | C4 | M | unblocks A1-parameterized, D3, drill-through |
| 16 | **Sharing** (D1 then D2) | D1–2 | M+M | ends single-player; D1 is the hard half |
| 17 | **Data threads / branching** | C2 | L | most differentiating interaction change |
| 18 | **Metric alerts** | F1 | M | the 80% is built |
| 19 | **Direct manipulation of a result** | C3 | M | removes the "re-ask in English" tax |
| 20 | **Rolling conversation summaries** | B4 | S | the six-message cliff is hit constantly |

**A recommended cut**, if forced to three: **13 (file upload)**, **14 (MCP)**,
**16 (sharing)**. Reach, distribution, and the end of single-player. #17 is the
most *interesting* and should be Tier 2's stretch goal rather than its centre —
it is an L in a tier of Ms.

## Tier 3 — Named, scoped, and deferred on purpose

Write these down with triggers, in the [architecture.md](architecture.md)
"Still deferred" style, so the deferral is a decision rather than an omission.

| Item | § | Trigger to revisit |
|---|---|---|
| Multi-step "deep dive" analysis | F3 | Tier 1 accuracy is credible and users start asking *why* questions |
| Root-cause / key drivers | F4 | after F3 |
| Proactive digests | F5 | after F1 + F2 |
| Row-level security | D3 | first enterprise deal that names it; **needs C4 first** |
| Warehouse connectors | E4 | a named customer, not a roadmap slot |
| SSO / OIDC | D5 | first enterprise deal |
| Scheduled reports | F2 | needs D1 first — a delivered report is a shared report |
| Entity/value dictionaries | B3 | needs its own disclosure-ladder decision in security.md |
| Rename `raymand` → `datamind` | §1.11 | before an open-source push; never incidentally |

---

# Part 5 — What not to break

Five things MVP1 got right that MVP2 will be tempted to trade away. Each is
already an invariant; each is now under pressure from something proposed above.

1. **The guard's fail-closed posture, and no privileged entry point.** MVP2 adds
   at least one new door (verified pairs) and possibly three (analysis-loop
   queries, direct-manipulation edits, MCP requests). Every one of them replays
   the hostile corpus in its own test file, the way `test_query_service.py`,
   `test_report_guard.py` and `test_dashboard_transfer.py` already do. **The
   moment one door is special, the guarantee is gone.**

2. **The disclosure policy governing all three channels.** Result, schema hints,
   and conversation history — filtered at *render* time, never only at write
   time. Three proposals stress this: value dictionaries (B3) are new customer
   data, conversation summaries (B4) are prose written from rows, and a
   multi-step loop (F3) creates intermediate results that must each pass
   `disclose()`. None of them may go around it.

3. **"Nothing calls a model at refresh time."** The most load-bearing "no" in the
   product — a dashboard keeps working after the provider key is revoked. Alerts
   (F1) and scheduled reports (F2) both want a scheduler; neither may put a model
   call on a refresh path.

4. **"No model is asked to do arithmetic."** `plan_kpi`, `reports/facts.py` and
   `reports/checks.py` compute; models narrate. Root-cause analysis (F4) is the
   direct test: the model must pick *which dimensions to test*, and the platform
   must compute *what actually moved*. Same split as `plan_chart` and the guard.

5. **The live step trail, and the SQL shown every time.** Data Formulator's own
   research names this: *"agents can be difficult to control if they are working
   in a black box."* A multi-step loop is exactly where a product stops showing
   its work. Every query in a deep dive appears in the trail, with its SQL, or
   the feature has cost more than it bought.

**And one meta-rule, from the eval's own charter:** *"An eval you are allowed to
edit measures your willingness to edit it."* MVP2 will produce a lot of numbers.
The golden set stays frozen; `gold_sql` changes only when demonstrably wrong,
with the evidence in `suites/CHANGELOG.md`. The new benchmark feature (A3) is a
*customer-facing* instrument and must be kept architecturally separate from the
frozen developer suite, or the two will contaminate each other within a month.

---

## Sources

**Data Formulator** — [GitHub](https://github.com/microsoft/data-formulator) ·
[0.7 release (MSR)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/) ·
[DF2 paper](https://www.microsoft.com/en-us/research/publication/data-formulator-2-iteratively-creating-rich-visualizations-with-ai/) ·
[original paper](https://www.microsoft.com/en-us/research/publication/data-formulator-ai-powered-concept-driven-visualization-authoring/) ·
[Foundry Labs](https://labs.ai.azure.com/innovations/data-formulator/)

**Wren AI** — [GitHub](https://github.com/Canner/WrenAI) ·
[OSS overview](https://www.getwren.ai/oss) ·
[semantic layer for text-to-SQL](https://www.getwren.ai/post/why-the-semantic-layer-is-essential-for-reliable-text-to-sql-and-how-wren-ai-brings-it-to-life) ·
[asking questions](https://docs.getwren.ai/cp/guide/home/ask) ·
[first-project best practices](https://docs.getwren.ai/cp/getting_started/best_practice) ·
[AI Advisor](https://docs.getwren.ai/cp/guide/evaluation/ai-advisor) ·
[spreadsheets + metrics](https://getwren.ai/post/introducing-wren-ais-new-ai-powered-spreadsheets-pre-built-100-cross-platform-metrics) ·
[how Wren AI works](https://docs.getwren.ai/oss/overview/how_wrenai_works)

**Databricks AI/BI Genie** — [docs](https://docs.databricks.com/aws/en/genie/) ·
[tune quality](https://docs.databricks.com/aws/en/genie/tune-quality) ·
[trusted assets](https://learn.microsoft.com/da-dk/azure/databricks/genie/trusted-assets) ·
[benchmarks & Ask for Review](https://www.databricks.com/blog/building-confidence-your-genie-space-benchmarks-and-ask-review) ·
[GA announcement](https://www.databricks.com/blog/aibi-genie-now-generally-available) ·
[Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api) ·
[Genie One MCP server](https://docs.databricks.com/aws/en/agents/mcp-tools/genie-mcp) ·
[AI/BI concepts](https://learn.microsoft.com/en-us/azure/databricks/ai-bi/concepts)

**Power BI / Fabric** — [Copilot overview](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-introduction) ·
[standalone Copilot](https://learn.microsoft.com/en-us/power-bi/explore-reports/copilot-chat-with-data-standalone) ·
[row-level security](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security) ·
[embedded RLS](https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-row-level-security) ·
[January 2026 features](https://powerbi.microsoft.com/en-us/blog/power-bi-january-2026-feature-summary) ·
[alerts & subscriptions](https://powerbiconsulting.com/blog/power-bi-data-alerts-subscriptions-notification-2026)

**Context** — [ThoughtSpot SpotIQ](https://www.thoughtspot.com/product/analytics/spotiq) ·
[root-cause analysis](https://www.thoughtspot.com/data-trends/analytics/root-cause-analysis) ·
[BI anomaly detection 2026](https://www.basedash.com/blog/best-bi-tools-for-ai-anomaly-detection-and-smart-alerting-2026) ·
[BI trends 2026](https://www.thoughtspot.com/data-trends/business-intelligence/business-intelligence-trends) ·
[enterprise text-to-SQL prompting ablation (VLDB 2026)](https://www.vldb.org/2026/Workshops/VLDB-Workshops-2026/NOVAS/NOVAS26_16.pdf)
