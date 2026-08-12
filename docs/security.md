# Security and data handling

What DataMind sends to a third-party model, what it refuses to send, and what
stops a generated query from doing harm.

This document is written against the code, not against intentions. Every claim
below names the module that enforces it, so a reviewer can check the claim
rather than trust it. Where a protection has a known limit, the limit is
stated — a security document that lists only its strengths is a marketing
document.

Related reading: [architecture.md](architecture.md) for why the system is
shaped this way, [pipeline.md](pipeline.md) for what each node does,
[CODEBASE.md](CODEBASE.md) for where things live.

---

## 1. The threat model

DataMind sits between two parties that should not fully trust each other: a
business user asking questions in plain language, and a large language model
operated by a third party. It holds credentials to a production database and
routes model-authored SQL at it.

Four things can go wrong, and each has its own answer:

| # | Risk | Answer | Enforced in |
|---|------|--------|-------------|
| 1 | The model writes destructive or exfiltrating SQL | AST allowlist, fail-closed | `app/sqlguard/` |
| 2 | Customer data leaks to the model provider | Per-connection disclosure policy | `app/pipeline/disclosure.py`, `HintBudget` |
| 3 | A valid query does damage by weight | Read-only transaction, row cap, statement timeout | `app/infra/connectors/` |
| 4 | Stored database credentials are stolen | AES-256-GCM bound to the owning row | `app/infra/crypto/` |

The model is treated as **untrusted input, not as an attacker with
capabilities**. It proposes text. It never executes anything, never holds a
credential, and never chooses what it is allowed to see.

### What is out of scope

Stated plainly so nobody assumes otherwise:

- **The model provider's retention.** Once a prompt is sent, what the provider
  logs or trains on is governed by your contract with them, not by this code.
  Section 3 exists so you can decide what to send.
- **Prompt injection via database content.** Under `SAMPLE` and `FULL` a result
  row reaches the model, and a row containing instructions is a row the model
  reads. The guard means an injected instruction cannot produce dangerous SQL,
  but it could influence the *prose* of an answer. The disclosure policy is the
  control here: `NONE` and `AGGREGATE` remove the vector entirely.
- **A malicious operator.** Anyone who can edit the code or read the
  environment can defeat all of this.
- **Network transport.** TLS is the deployment's concern. See §7.

---

## 2. Every place data leaves for a model provider

There are **twelve use cases**, across fourteen call sites, and no others. The
dependency rule forbids importing `litellm` outside `app/infra/llm/`, and CI
greps for violations, so this list cannot silently grow.

| # | Use case | Trigger | Call site |
|---|----------|---------|-----------|
| 1 | Route the question | every run | `pipeline/nodes/__init__.py:112` |
| 2 | Describe the schema | a run classified METADATA | `pipeline/nodes/__init__.py:425` |
| 3 | Ask a clarifying question | every run, if enabled | `pipeline/nodes/__init__.py:495` |
| 4 | Generate SQL | every run | `pipeline/nodes/__init__.py:630` |
| 5 | Write the answer | every run | `pipeline/nodes/__init__.py:863` |
| 6 | Choose a chart | every run, if chartable; **every tile draft**, if chartable | `pipeline/nodes/__init__.py:947` |
| 7 | Suggest follow-up questions | SPA opens a thread | `services/run_service.py:736` |
| 8 | Draft SQL for a tile or a report block | user asks for a tile, or checks a block | `services/sql_draft_service.py` |
| 9 | Generate a semantic layer | user clicks Generate | `semantic/generator.py:383,417,452` |
| 10 | Propose a report outline | user proposes an outline | `reports/outline.py:203` |
| 11 | Write a report section | once per section, per generation | `workers/report.py:880` |
| 12 | Write the executive summary | once per generation | `workers/report.py:961` |

One model interaction sends **no customer data at all**: the capability probe
in `api/v1/llm_configs.py`, a fixed test prompt.

> **Changed:** #6 gained a second trigger. Choosing a chart used to fire only
> at the end of a chat run; a **dashboard tile draft** now asks it too, so that
> a tile written from a sentence is drawn as what the sentence meant rather
> than as whatever a shape heuristic defaults to. It is the *same* call site —
> `propose_chart_intent`, one function, sent from two places — which is what
> keeps the count above at twelve and fourteen. **Nothing new leaves the
> process, and less does than on the chat path:** the tile call passes no
> policy argument at all, so `ResultProfile.describe` renders at the narrowest
> budget under *every* policy including `FULL`, withholding the one row value
> that block can carry (a measure's `min`/`max`). Asserted by
> `test_the_chart_call_sends_shape_and_never_a_row_value`. A report block's
> check deliberately does **not** fire it: its editor has no chart-type control
> and ignores the suggestion, so the answer would be discarded unread. (An
> earlier wording said a block "persists no chart config" — that was wrong;
> `report_blocks.chart_config` exists. The reason is the missing reader.)

> **Changed:** #10–#12 are Reports, and were missing from this table until
> 2026-08-12 — the list had not been revisited since the feature landed.
> Nothing they send is new in kind (see §2.1 and §2.3), but the claim above is
> only true if this table is maintained. Adding an LLM call site means adding a
> row here.

> **Changed:** #2 is new. A metadata question — *"what tables do I have?"*,
> *"what does `order_items` count?"* — used to be answered inside `route` by
> rendering the stored snapshot, and **halted before any model call**. It now
> reaches a model, because the answer to most schema questions is in the
> semantic layer rather than in the catalog, and a table list with row counts
> is a non-answer to all but the simplest of them. Nothing new leaves the
> process: the block it sends is `RetrievedContext.render` under the
> connection's own policy — the same bytes `generate` (#4) has always been
> sent for the same question — plus a count of tables and the names of any the
> block left out. The fallback when the provider fails is the old rendering,
> which still costs nothing.

### 2.1 What each one sends

Common building blocks, both governed by the disclosure policy (§3):

- **The schema block** — `_describe_schema()`. Table and column names, types,
  keys, and per-column *content hints* metered by `HintBudget`.
- **The transcript** — `_render_history()`, filtered by `disclose_history()`.

| # | Use case | Question | Schema | Transcript | Result rows | Notes |
|---|----------|:--------:|:------:|:----------:|:-----------:|-------|
| 1 | Route | ✅ | ❌ | ✅ recent turns | ❌ | Classification only |
| 2 | Describe | ✅ | ✅ | ✅ | ❌ | Schema questions only; no SQL is ever written |
| 3 | Clarify | ✅ | ✅ | ✅ | ❌ | Runs before any SQL exists |
| 4 | Generate SQL | ✅ | ✅ | ✅ | ❌ | **Never sees results** |
| 5 | Present | ✅ | ❌ | ❌ | **✅ per policy** | Also sends the executed SQL |
| 6 | Chart | ✅ | ❌ | ❌ | shape only | Counts and types, not values. From a tile draft, the **narrowest** shape block at every policy |
| 7 | Suggestions | ❌ | ✅ | ✅ | ❌ | Fires without the user asking |
| 8 | Tile / block SQL draft | ✅ | ✅ | ❌ none | ❌ | History deliberately empty |
| 9 | Semantic layer | ❌ | ✅ | ❌ | ❌ | Per-table, one call each |
| 10 | Report outline | ✅ the request | ✅ | ❌ none | ❌ | The whole snapshot, not a retrieval |
| 11 | Report section | ✅ per block | ❌ | ❌ | **✅ per policy** | Plus figures computed from those same rows |
| 12 | Report summary | ✅ the request | ❌ | ❌ | ❌ | **Prose only** — the sections' own paragraphs |

The single most important row is **#4**. The node that writes SQL never
receives result data under any policy — it works from schema, question, and
transcript alone. That holds for every caller of it, including a tile draft and
a report block.

**Result values reach exactly two of the twelve**: `present` (#5) and a report
section (#11). Both go through the same `disclose()`, and neither is reachable
without it — a report additionally refuses to run at all under `NONE` or
`AGGREGATE` (§2.3). Everything else works from structure, shape, or prose.

**#2 sends the schema block and nothing else new.** A schema question is
answered from structure and meaning: the same `RetrievedContext.render` block
#4 receives, whose row counts and per-column content hints are metered by
`HintBudget` exactly as they are everywhere else, plus a table count and the
names of any tables too numerous to describe in full. Counts and names, never a
row-count total outside the gate. Under `NONE` a schema answer therefore
carries no row counts at all — names, types and keys still travel, because
structure is never gated and a question about the schema cannot be answered
without it.

**#6, the chart chooser, sends shape rather than data**: the question, the row
count, and per column its type, distinct count, and whether it is constant. A
count is not a disclosure — the decision needs to know a column holds 1,000
distinct names or one repeated total, not what those names are. The one
exception is a numeric column's min/max, which *is* one specific row's value,
so it rides the same `HintBudget` gate as the schema block and appears only
where result values already do.

Three details worth knowing because they surprise people:

- **#7 fires on its own.** Follow-up suggestions are requested when the SPA
  refreshes a thread, not when the user asks a question. It renders the
  transcript through the *same* `_render_history` the run path uses, so it
  carries no wider disclosure — but it does mean a thread left open produces
  provider traffic. Set the connection's model to none, or the policy to
  `NONE`, if that matters to you.
- **#8 sends no history at all.** A draft passes `history=[]` deliberately:
  neither a dashboard tile nor a report block has a conversation to inherit, and
  inventing one would put another connection's answers into this prompt. A
  report block adds two things a tile does not: `route` runs first
  (`classify=True`), so a question with no data answer is refused before the
  schema-sized prompt is spent, and the block's time rules are appended to the
  SQL prompt through `NodeDeps.extra_rules`.
- **#5 sends the executed SQL back to the model** along with the disclosed
  result, so the answer can be narrated against the query that produced it.
  That SQL is derived from the schema, not from result values, and it is
  already on the user's screen as an auditable artifact.

### 2.2 The semantic layer (#9) in detail

Generating a semantic layer is the largest single batch of provider traffic:
one call for a whole-schema overview, then **one call per table**, four
concurrently, then one for the glossary. On a 42-table schema that is 44 calls.

What each call carries:

- **Overview** — table names, approximate row counts, column counts, and the
  foreign-key graph. Capped at 200 tables and 200 relationships. No values.
- **Per table** — that table's columns and types, its FK neighbours, and the
  content hints the current `HintBudget` allows. Same budget as a run.
- **Glossary** — the business terms already drafted, not raw schema.

It **widens no disclosure**: generation reads the same schema block a run
reads, under the same budget. On the way back, `_known_values()` filters the
model's proposed `value_meanings` down to values already present in the
snapshot, so a model cannot invent a key and have it stored as fact.

### 2.3 Reports (#10–#12) in detail

A report is the only feature that **refuses to run under a narrow policy**
rather than degrading. `assert_wide_enough` requires `SAMPLE` or `FULL`, and is
checked at report creation, at run creation, at the start of every generation,
and at the start of every section retry — so a policy tightened between any two
of those stops the work with a message naming the policy in force. The reason
is honesty, not caution: prose written from no values, printed beside charts
drawn from real ones, is a document that disagrees with itself.

- **#10, the outline**, sends the user's request and `RetrievedContext.render`
  under the connection's own policy — the same block #4 receives, `HintBudget`
  and all. Two differences from a run: there is **no retrieval** (the whole
  snapshot goes, since an outline is about the whole database, and no
  `_RETRIEVE_BUDGET_CHARS` ceiling applies), and there is no transcript. No
  result values, because none exist yet.
- **#11, a section's prose**, is the second of the two call sites that sees
  result data, and it sees it through `disclose()` at *narration* time — under
  the policy in force **now**, not the one in force when the query ran. It is
  additionally narrowed twice on the way: at most `MAX_PROMPT_ROWS` (50) rows
  per block, and each cell clipped to `MAX_CELL_CHARS` (120). The computed
  figures beside them (`reports/facts.py`) are derived from **those same
  disclosed rows** and only when they are the complete result, so a fact can
  never carry a value out of a row the policy withheld.
  `app.reports` sits below `app.pipeline` in the layer order, so `narrate.py`
  *cannot* call `disclose()` — the worker must disclose and hand down, which is
  the stricter reading of invariant #4, enforced by import-linter.
- **#12, the executive summary**, is given **no data at all** — only the
  finished paragraphs of the sections below it. That is a safety property, not
  a saving: a summary that could reach the rows would be a second place for a
  figure to be invented, while one that can only quote the sections can be
  checked against them, which is exactly what `reports/checks.py` does.

The **saved statements** (`report_blocks.sql`) never reach a model at
generation time; they are executed through `execute_saved_sql`, which
re-validates them against the connection's current snapshot like any tile
(§4.5).

---

## 3. The disclosure policy

Every connection declares how much result data may reach the model:

```
NONE  <  AGGREGATE  <  SAMPLE  <  FULL
```

The policy in force is shown in the chat header **at ask time**, so the user
knows what they are agreeing to before they press send.

### 3.1 What each level shares

| | `NONE` | `AGGREGATE` | `SAMPLE` | `FULL` |
|---|:---:|:---:|:---:|:---:|
| Row count | ✅ | ✅ | ✅ | ✅ |
| Column **names** | ❌ | ✅ | ✅ | ✅ |
| Row **values** | ❌ | ❌ | first 50 | all |
| Table row counts (schema) | ❌ | ✅ | ✅ | ✅ |
| Distinct counts, null fractions | ❌ | ✅ | ✅ | ✅ |
| Column **value lists** | ❌ | ❌ | ≤ 25 | ≤ 50 |
| Date/time min–max | ❌ | ❌ | ✅ | ✅ |
| Numeric min–max | ❌ | ❌ | ❌ | ✅ |
| Earlier answers in transcript | withheld | withheld | ✅ | ✅ |

Under `NONE`, the model is told *"1,412 rows were returned but not shared with
the model"* and writes its answer from that alone. `SAMPLE` caps at
`SAMPLE_ROWS = 50`.

### 3.2 The policy governs three things, not one

This is the part most easily got wrong, so it is enforced in three places:

1. **`disclose()`** gates the result of the current run.
2. **`HintBudget`** gates per-column content hints in the schema block.
3. **`disclose_history()`** gates the **conversation**.

The third exists because an assistant message is prose the model wrote *from*
result rows — *"Revenue was $1.24M across 812 orders"* — and the next turn
sends it back as context. Without filtering, a connection tightened from `FULL`
to `NONE` would keep replaying yesterday's figures under a policy whose entire
meaning is that no result data reaches the model.

All three filter at **render time, never at write time**. Tightening a policy
takes effect on the very next question, with no re-sync and no leak from the
transcript.

Under `NONE` and `AGGREGATE`, an earlier answer's prose is replaced with a
placeholder while its **SQL survives** — because SQL is derived from the schema,
not from results, and it is what a follow-up such as *"now break that down by
month"* actually builds on. The user's own turns always survive: they are the
user's words, not the database's.

A conversation is **pinned to one connection** (`_bind_connection`, HTTP 422 on
a mid-thread switch) so history can never cross policies.

### 3.3 The sensitive-name floor

Below the ladder sits a floor that applies **at every policy, including
`FULL`**: a column whose *name* matches a sensitive token never has its values
captured into the snapshot at all.

```
name  email  mail  phone  tel  mobile  address  addr  street  city
postal  zip  ssn  tax  passport  iban  account  card  token  secret
password  hash  ref  tracking  url  website  note  comment  description
body  title  subject  lat  lon  ip
```

Matched on token boundaries with an optional plural, so `billing_city` and
`notes` are caught while `capacity` and `is_active` are not.

This is a floor, not another rung, because low cardinality is not the same as
non-sensitive — a 12-value `city` column is still a disclosure. Two further
caps apply at capture: a column with more than `HINT_MAX_CARDINALITY = 25`
distinct values is not an enum and is never listed, and values longer than
`HINT_MAX_VALUE_CHARS = 40` are prose, not categories.

The floor applies at **capture**, so sensitive values are never written to the
snapshot in the first place. It is stricter than the render-time gates for a
reason: the schema block is sent on *every* question, whereas a result is sent
only for the query the user actually asked.

### 3.4 Known residual

Recorded here rather than omitted. Under `NONE`/`AGGREGATE`, kept SQL may
contain a literal — `WHERE status = 'churned'` — that originally came from a
value list a wider policy once allowed. It is a single token, it is already on
the user's screen as an auditable artifact, and stripping it would take from a
follow-up the one thing it most needs. Also noted in
[pipeline.md](pipeline.md) §5.

---

## 4. The SQL guard

The model **proposes** SQL. It never executes anything. Every statement is
parsed with SQLGlot and walked against an allowlist before it can reach a
database.

The governing rule is **fail-closed**: an unknown AST node type is a
*rejection*, not a warning. A new SQLGlot release that introduces an expression
class therefore causes a false rejection — never a bypass. That trade is
deliberate and is the whole design.

### 4.1 What is checked

Fifteen distinct rejection codes, each a separate check in
`sqlguard/validator.py`:

| Code | Rejects |
|------|---------|
| `E_PARSE` | Anything SQLGlot cannot parse |
| `E_MULTI_STATEMENT` | Statement chaining — `; DROP TABLE …` |
| `E_NOT_A_SELECT` | Any statement that is not a read |
| `E_NODE_NOT_ALLOWED` | Any AST node outside the allowlist |
| `E_FUNCTION_NOT_ALLOWED` | Any function outside the allowlist |
| `E_FORBIDDEN_CONSTRUCT` | Denied substrings (below) |
| `E_SYSTEM_TABLE` | `pg_*`, `information_schema`, `sys.*`, `mysql.*`, … |
| `E_TABLE_NOT_ALLOWED` | A table not in this connection's snapshot |
| `E_UNKNOWN_COLUMN` | A column not in that table |
| `E_UNKNOWN_ALIAS` | An alias that resolves to nothing |
| `E_COLUMNS_UNVERIFIED` | Columns that cannot be resolved at all |
| `E_STAR_NOT_ALLOWED` | `SELECT *` where policy forbids it |
| `E_COMMENT_NOT_ALLOWED` | Comment-based evasion |
| `E_TOO_MANY_JOINS` | More than `max_joins` (default 10) |
| `E_SUBQUERY_TOO_DEEP` | Deeper than `max_subquery_depth` (default 4) |

**Allowlists, not denylists.** `ALLOWED_NODES` enumerates the **117**
expression types permitted anywhere in the tree; `ALLOWED_FUNCTIONS` enumerates
the **71** function names permitted inside `exp.Anonymous` — which is how
SQLGlot represents any function it does not model. Without that second list,
allowing `Anonymous` would allow `pg_read_file()`.

A denylist of forbidden substrings runs *as well*, covering the classics:

```
pg_sleep  pg_read_file  pg_ls_dir  lo_import  lo_export  dblink
copy   into outfile  load_file  xp_cmdshell  sp_executesql
openrowset  opendatasource  bulk insert  current_setting  set_config
pg_terminate  pg_cancel
```

### 4.2 Names are resolved against the snapshot

Tables and columns are checked against the connection's **stored schema
snapshot**, not against the live database. Two consequences:

- A connection that has never been synced can be queried for **nothing at
  all** — the allowlist is empty and every table fails `E_TABLE_NOT_ALLOWED`.
- An unqualified `orders` is accepted only if it maps to exactly **one**
  `schema.orders`. Ambiguity is a rejection, not a guess.

### 4.3 The row limit is ours, never the model's

`sqlguard/rewriter.py` injects the `LIMIT` after validation, into an
already-valid tree:

- No `LIMIT` in the model's SQL → one is added.
- `LIMIT 1000000` → capped down to the connection's `max_rows`.

The effective limit is always `min(requested, max_rows)`. The model cannot
raise it, and asking it politely not to is not part of the design.

### 4.4 The hard CI gate

`backend/tests/unit/test_sqlguard_hostile.py` is a hostile corpus covering
statement chaining, DDL, writes, system catalogs, file reads, `xp_cmdshell`,
`INTO OUTFILE`, union smuggling, and comment evasion. **Zero bypasses or CI
fails.**

```bash
make guard        # the hostile corpus alone
make test         # the full backend suite
```

The guard is dialect-aware: the same validator renders PostgreSQL, MySQL,
T-SQL, and Oracle, so a bypass that works in one dialect is not a bypass in
another by accident.

### 4.5 Stored SQL is guarded too, twice

A dashboard tile stores SQL and re-runs it on a schedule, so a one-time check
at authoring would be worthless after a schema change.
`services/dashboard_service.py` validates tile SQL **on the way in** and
**again on every execution**, against the connection's *current* snapshot. A
tile whose table was dropped fails closed rather than running.

**A report block is the same story with a longer gap**: `report_blocks.sql` is
written by `/check` (a model) or by `PUT .../sql` (a person), and is
re-validated by `execute_saved_sql` on every generation — which may be months
later, against a schema that has moved. `tests/unit/test_report_guard.py`
replays the hostile corpus through that path specifically.

Hand-written SQL goes through the identical guard — there is no trusted path
for SQL a human typed, and `sql_origin` on either table is provenance for the
editor, never a signal the guard consults.

---

## 5. Containment underneath correctness

The guard can be wrong. Containment assumes it will be.

| Engine | Read-only mechanism | Timeout |
|--------|--------------------|---------|
| PostgreSQL | `READ ONLY` transaction | `statement_timeout` |
| MySQL / MariaDB | `START TRANSACTION READ ONLY` | `max_execution_time` (err 3024) |
| Oracle | `SET TRANSACTION READ ONLY` | driver `call_timeout` |
| SQL Server | read-only role (no such transaction mode) | query timeout |

Every engine additionally enforces a **row cap** and a **statement timeout**.
Defaults: `default_max_rows = 1000`, `default_statement_timeout_ms = 30_000`,
both overridable per connection.

Each connector **proves** its role cannot write — by attempting a write inside
a transaction it always rolls back — and reports `readonly_confirmed` honestly
either way. The UI surfaces this as *"read-only role confirmed"* when you test
a connection. **Grant DataMind a read-only database role.** The transaction
mode is a second line of defence, not a substitute for least privilege.

Runs are bounded in time and count: a per-run deadline
(`RUN_DEADLINE_SECONDS`), a ceiling of 24 state transitions, at most one
repair regeneration, and `MAX_CONCURRENT_RUNS` in flight. A node crash is
recorded as a run failure, never a bare HTTP 500; a process that dies mid-run
is healed by the reconciler.

---

## 6. Credentials and identity

**Database and provider credentials** are encrypted with AES-256-GCM, using the
**owning row's identity as additional authenticated data**. A ciphertext copied
from one connection row to another fails to decrypt rather than silently
working.

**No read model ever exposes a password or `api_key`** — and a test asserts
this against the generated Pydantic schemas, so it cannot regress into an
endpoint by accident.

**Authentication** is email + password with Argon2id (tunable time, memory, and
parallelism cost), short-lived JWT access tokens (`access_token_ttl_seconds`,
default 15 minutes), and rotating refresh tokens in an HttpOnly cookie
(`refresh_token_ttl_seconds`, default 14 days) with **reuse detection** — a
replayed refresh token invalidates the family. Refresh tokens are stored
hashed, never in the clear. An admin password reset revokes the user's live
sessions.

> **Losing `SECRET_BOX_KEY` means every stored credential must be re-entered.**
> There is no recovery path, by design. Back it up somewhere your database
> backups are not.

---

## 7. Deploying this safely

The defaults are development defaults. Before real data:

- [ ] **Change `ADMIN_PASSWORD`.** The bootstrap admin is
      `admin@raymand.local` / `raymand`, and the API logs a loud warning while
      it stays that way.
- [ ] **Generate fresh secrets** with `make secrets` — `SECRET_BOX_KEY` and
      `JWT_SECRET` — and back up the box key separately.
- [ ] **Grant read-only database roles.** Confirm each connection reports
      *read-only role confirmed*.
- [ ] **Choose a disclosure policy per connection deliberately.** The default
      should be the narrowest policy that still answers your questions. Start
      at `NONE` and widen only where the answers are visibly worse.
- [ ] **Terminate TLS in front of the app.** Nothing here does it for you, and
      refresh cookies deserve `Secure`.
- [ ] **Expose only port 5173.** The SPA calls same-origin `/api/v1` and Vite
      proxies to the API. Never expose the API or either database directly.
- [ ] **Do not expose `server.allowedHosts: true` publicly** — it is a
      dev-server convenience for proxied hosts (Codespaces, Lightning), not a
      production setting.
- [ ] **Review your model provider's retention terms.** Everything in §2 and §3
      controls what leaves; nothing here controls what the provider then does
      with it. A self-hosted model (Ollama, vLLM) behind `LLMGateway` removes
      the question entirely.
- [ ] **Sync schemas from a least-privilege role.** Constraint introspection
      uses engine catalogs rather than `information_schema` precisely because
      the latter is privilege-filtered and silently drops keys.

---

## 8. Reporting a vulnerability

Please report security issues privately rather than in a public issue. Include
a reproduction and the affected version. If the finding is a guard bypass, a
failing case added to `test_sqlguard_hostile.py` is the most useful possible
report.
