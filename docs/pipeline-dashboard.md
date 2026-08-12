# The dashboard pipelines, node by node

What happens between "user asks for a tile" and "twelve tiles redraw themselves
at 03:00 with nobody watching". Companion to [pipeline.md](pipeline.md) (the
chat run, and the shared machinery), [pipeline-report.md](pipeline-report.md)
(documents), and [dashboards.md](dashboards.md) (the data model, the API, the
grid, the tile editor). **This file is only the pipelines**: the flows, the
nodes, the prompts, and what happens when each step fails.

Code:
[`services/sql_draft_service.py`](../backend/app/services/sql_draft_service.py)
(question → guarded draft),
[`services/query_service.py`](../backend/app/services/query_service.py)
(stored SQL → result; the batch path),
[`services/dashboard_service.py`](../backend/app/services/dashboard_service.py)
(CRUD, the cache, the refresh policy),
[`frontend/src/components/dashboard-schedule.ts`](../frontend/src/components/dashboard-schedule.ts)
(which tiles are due).

---

## 1. Two pipelines, and the line between them

A dashboard is the only surface where **the model runs once and the query runs
forever**. That split is the whole design:

```
   AUTHORING  (a person is watching, a model is involved, nothing is scheduled)
   ┌──────────────────────────────────────────────────────────────────┐
   │  question ─► route? ─► retrieve ─► generate ─► validate ─► preview│──► save
   │      or     SQL typed by hand ────► validate ────────────► preview│    (guard again)
   └──────────────────────────────────────────────────────────────────┘
                                     │  dashboard_tiles.sql
                                     ▼
   REFRESH  (nobody is watching, no model exists, this runs every 30 seconds)
   ┌──────────────────────────────────────────────────────────────────┐
   │  due? ─► cache fresh? ─► guard ─► execute ─► plan_chart ─► plan_kpi│──► cache
   └──────────────────────────────────────────────────────────────────┘
```

| | Authoring | Refresh |
|---|---|---|
| **Trigger** | `POST /sql/drafts`, `POST /sql/drafts/validate`, tile save | browser tick, first paint, or "Refresh now" |
| **Model calls** | 1 (`generate`), +1 per repair. **0** on the hand-written road | **zero, always** |
| **Persists** | nothing until the tile is saved | the tile cache |
| **Failure posture** | raise — the user is looking at the editor | **a value** — `TileResult(status="ERROR")`, never an exception |
| **Guard** | at draft, at save | **again**, on every single execution |
| **Concurrency** | one draft, one user | ≤4 statements per request, 1 connector per connection |

**No model is ever asked anything at refresh time.** `llm_config_id` is on the
tile row for provenance and for "edit → re-ask" only. This is why a dashboard
keeps working after the provider key is revoked, and why a 30-second tile costs
nothing but a query.

---

## 2. Flow A — authoring a tile

### A1 · The plain-language road: `POST /sql/drafts`

`draft_sql(db, settings, connection_id, llm_config_id, question, owner_id)`
([sql_draft_service.py:115-205](../backend/app/services/sql_draft_service.py#L115-L205)).

**It is deliberately not a run.** No conversation, no `messages` row, no `runs`
row, no SSE, no step trail. A draft is a thing the user is looking at, and
closing the editor must leave nothing behind. What it *does* reuse is the part
that matters: three of the chat pipeline's nodes, called directly.

**Node 0 — the two gates.** `_owned(...)` on the connection and the model config
(404 if either belongs to someone else), then `_snapshot_or_refuse`: a
connection with no schema snapshot raises *"Sync this connection's schema before
drafting SQL against it."* Without that, the model would be handed a schema
block with no tables in it and asked to write SQL anyway — spending a call to
produce a statement the guard is then guaranteed to reject.

**Node 0.5 — one connector for the whole draft.** Opened here, closed in a
`finally`. `retrieve`/`generate`/`validate` never touch it; the preview does,
and opening a second one to run fifty rows would be a connection the user never
asked for.

**The state and deps** it builds (`_draft_state`) differ from a chat run in
exactly five places:

| field | value | why |
|---|---|---|
| `history` | **`[]`** | a tile has no conversation to inherit, and inventing one would put another connection's answers in this prompt |
| `max_rows` | `PREVIEW_MAX_ROWS = 50` | a preview answers "did this do what I meant"; it is not a result |
| `max_repairs` | `DRAFT_MAX_REPAIRS = 1` | the user is watching |
| `emit` | `_no_emit` | no run to attach events to, no client listening |
| `run_id` / `conversation_id` | synthetic UUIDs | a known wart: `RunState` requires both. They are never persisted, emitted or looked up — making the fields optional would weaken the type for every real run to serve a path that writes nothing |

**Node 1 — `route`, only if asked.** `classify=False` by default, so a **tile**
draft sends exactly the calls it always sent. The report block path passes
`classify=True`; the asymmetry is deliberate and argued in
[pipeline-report.md §3.1](pipeline-report.md) — a tile draft's output is on
screen in front of the person who asked for it, while a block's is stored and
read months later.

**Node 2 — `retrieve`.** The chat node verbatim: `FULL_SNAPSHOT` under
`_RETRIEVE_BUDGET_CHARS` (50,000), `EXACT_MATCH` + one FK hop above it. With
`history=[]`, `_tables_from_history` contributes nothing, so a wide-schema draft
leans entirely on substring matching against the question — see
[pipeline.md §7](pipeline.md) gap 4 for what that gets wrong.

The connection's **semantic layer** is loaded on exactly the run path's terms
(`load_document`, and `RetrievedContext.render` scopes and gates it). A draft is
not a loophole around the layer's switch, nor around the disclosure budget.

**Node 3+4 — `generate` → `validate`, up to twice.**

```python
for _ in range(DRAFT_MAX_REPAIRS + 1):
    result = await generate(state, deps)
    if result.status == "FAILED":
        raise LLMError(state.error.hint or "The model could not produce a query.")
    if (await validate(state, deps)).goto != "generate":
        break
```

Same prompts as chat — `GENERATE_SYSTEM` with `_SQL_RULES` and `_OUTPUT_RULES`
on attempt 1, `REPAIR_SYSTEM` with the guard's feedback on attempt 2 — and the
same `SqlProposal` contract. `extra_rules` is empty for a tile, which means the
prompt is **byte-identical** to what chat sends.

If the second attempt is also rejected, the loop simply ends: the draft is
returned with `validation_status != "VALID"`, no preview, and the guard's issues
in `validation_report` for the editor to render inline. **A rejection is an
answer here, not an exception** — the same posture the semantic-layer editor
takes with metric-expression errors.

**Node 5 — the preview.** A `VALID` statement is run through
`execute_saved_sql` — *the code that will run the tile at 03:00* — with the
**raw** statement, not the guard's rewrite, because `execute_saved_sql` re-guards
from scratch and previewing the rewrite would preview something the tile will
never be asked to run.

**Node 6 — the chart defaults.** `_chart_suggestion` runs `profile_result` →
`plan_chart` over the preview (the **heuristic**, not a model: the editor needs
sensible defaults the user is about to override, not an opinion worth a token),
and `_chart_options` returns a per-type verdict list so the picker **disables**
what will not work rather than offering it and letting the save path demote it
with an apology. Both are wrapped in `try/except`: a defaulted picker is never
worth a 500, and an empty options list means "no opinion", which leaves every
type enabled — exactly the behaviour before the feature existed.

### A2 · The hand-written road: `POST /sql/drafts/validate`

`validate_sql` — guard, preview, verdict, **no model**. Same `SqlDraft` response
shape, which is what lets one editor serve both roads, and what makes a whole
dashboard buildable by a user with **no LLM provider configured at all**.

It is also the "I edited what the model gave me" road, because those are the
same thing.

### A3 · Saving is a second guard, not a receipt

`_validated_tile_fields`
([dashboard_service.py:314-363](../backend/app/services/dashboard_service.py#L314-L363))
runs on add **and** on update, and validates the *resulting* tile rather than the
field that changed — switching a TEXT tile to a CHART is legal, and it is the
whole tile that has to make sense:

1. `TEXT` tile → `sql` forced to `""`, `connection_id` forced to NULL. A text
   tile may not carry a statement that a later type change would silently make
   executable.
2. no `connection_id` → 422; connection must be the caller's own.
3. `llm_config_id`, if given, must be the caller's own.
4. empty `sql` → 422.
5. `max_rows` clamped by `effective_max_rows` **on the way in**, so the editor
   never shows a cap the connection would not honour.
6. no snapshot → 422, *"Sync this connection's schema before saving SQL against
   it."*
7. `guard(sql, policy_from_snapshot(...))` → `SqlRejectedError` with the rule id.

**The preview passing in the editor is not authorisation to save.** The preview
and the save are two requests, and the second carries whatever the client chose
to send.

---

## 3. Flow B — refresh

### B1 · The browser decides what is due

`dueTileIds(tiles, results, now)`
([dashboard-schedule.ts](../frontend/src/components/dashboard-schedule.ts)) —
one pure, DOM-free function, tested on its own (`npm run test:schedule`),
because getting it wrong in the eager direction turns a forgotten browser tab
into a load generator pointed at the customer's database, and getting it wrong
the other way silently shows ten-minute-old numbers on a 30-second tile.

Three rules:

- a **TEXT** tile computes nothing, ever;
- an interval of **0 is manual** — never due on a tick, only on request. This is
  why `NULL` (inherit the dashboard's rate) and `0` (never, unless I press
  refresh) are different values in the database; the backend resolves the
  inheritance (`effective_refresh_interval`) and a resolved `0` means "only when
  asked";
- a tile with **no result yet** is due immediately, which is what makes first
  paint fetch everything with no second code path. An unparseable `computed_at`
  also counts as due — a tile that can never refresh again is the worse failure.

**One scheduler, not one timer per tile.** The page ticks once and asks for the
set that is due: `POST /dashboards/{id}/data {tile_ids:[…]}`. The whole
dashboard is the *exception* (first paint), not the normal call.

### B2 · `DashboardService.refresh` — the cache gate

([dashboard_service.py:421-479](../backend/app/services/dashboard_service.py#L421-L479))

1. Load the dashboard (404 if not the caller's) and the tiles asked for.
2. Skip tiles with nothing to compute (TEXT, or blank SQL).
3. A tile whose `connection_id` is **NULL** — the connection was deleted out
   from under it, `SET NULL` by design — becomes
   `E_CONNECTION_REMOVED`: *"Edit the tile to point it at another one."* The
   layout the user built is not deleted because a connection was.
4. For the rest, read the cache rows and ask `is_fresh`:

   ```
   fresh  ⇔  cache exists
             AND cache.sql_hash == result_fingerprint(tile)
             AND (interval <= 0  OR  now - computed_at < interval)
   ```

   `result_fingerprint` hashes **everything that decides what a refresh
   returns**: `connection_id`, `sql`, `max_rows`, `chart_config`. Editing a tile
   from a pie to a line therefore misses the cache immediately instead of
   serving the pie until its interval happened to elapse.

   **`table_config` is deliberately absent** from the fingerprint. It decides how
   the browser draws rows it already has — column order, labels, a sort — and
   nothing about what the query returns, so renaming a column header must not
   re-run a query against the customer's database.

5. `force=true` (the kebab's "Refresh now") skips this gate entirely.
6. Everything stale goes to `_execute`.

A cache hit is rebuilt with `TileResult.from_payload` and is **indistinguishable
from a fresh computation, including `computed_at`** — which is the point of
serving it: the reader is told how old the number is.

### B3 · `execute_many` — the batch shape

([query_service.py:497-590](../backend/app/services/query_service.py#L497-L590))

1. Group requests by `connection.id`.
2. A group whose connection is not the caller's fails as a group with
   `E_FORBIDDEN`, and **not one connector is opened** — nothing is decrypted or
   dialled for a connection the caller does not own.
3. **Every database read happens before the fan-out**: one `latest_snapshot` per
   connection, awaited in sequence, because an `AsyncSession` is not safe for
   concurrent use. After that point nothing touches `db`.
4. A group whose snapshot is empty short-circuits to `E_NO_SNAPSHOT` for all its
   tiles — nothing this connection holds could pass the guard, so it is not worth
   dialling the database to find that out twelve times.
5. One connector per group (`bind_connector`; a failure here fails that group
   with the connector's own code), closed in a `finally`.
6. Tiles run under `MAX_CONCURRENT_TILES = 4`. The cap is on the customer's
   database, not on ours.

### B4 · `execute_saved_sql` — the node that runs a stored statement

([query_service.py:265-371](../backend/app/services/query_service.py#L265-L371)).
The second entry point into the guarded path, and it gets **no shortcut**:

| # | Step | Fails as |
|---|---|---|
| 1 | `connection.owner_id != owner_id` — re-checked at execution, not only at save, because connections get deleted, ids get reused, and rows get edited underneath you | `E_FORBIDDEN` |
| 2 | empty statement | `E_SQL_REJECTED` |
| 3 | load the **current** snapshot (unless the batch passed one) | `E_NO_SNAPSHOT` — worded for someone whose connection was simply never synced, because "table not allowed" is the wrong sentence for that |
| 4 | `row_cap = effective_max_rows(connection, max_rows)` — a tile may only **tighten** the connection's cap | — |
| 5 | `guard(sql, policy_from_snapshot(snapshot, connection, max_rows=row_cap))` → parse with SQLGlot, walk the AST against the allowlist, resolve every name against the snapshot, rewrite with the row `LIMIT` | `_guard_failure` (below) |
| 6 | `connector.execute(executable, max_rows, statement_timeout_ms)` — read-only transaction on PG/MySQL/Oracle, read-only role + query timeout on SQL Server | `E_QUERY_FAILED` with the driver's message |
| 7 | `_chart(...)` — `profile_result` → `plan_chart(profile, stored_intent)` → `compile_vega_lite` | never fails the tile: `vega_spec = None` + a note |
| 8 | `_kpi(...)` when `want_kpi` (METRIC tiles only) — `plan_kpi` over the same profile | never fails the tile: `kpi = None` |
| 9 | anything else | `AppError` → its own code; otherwise `E_INTERNAL` with `str(err)[:500]`, logged `tile_execution_crashed` |

The connector is closed on every exit, **and only by whoever opened it** — a
caller that passed one owns it.

**`_guard_failure` tells drift apart from bad SQL.** A rejection whose first rule
is `E_TABLE_NOT_ALLOWED` or `E_UNKNOWN_COLUMN` — for SQL that was valid when it
was saved — means the schema moved underneath the tile, so it is reported as
`E_SCHEMA_CHANGED` with *"re-sync the connection, then check it again"*. Every
other rejection carries the guard's own `rule_id`, message and hint verbatim.
The fix is different, so the code is different.

**Why the chart is decided on the backend.** The stored `ChartIntent` goes into
`plan_chart` as a *suggestion*, so the user's explicit pick gets the same
name-check and shape-repair a model's suggestion gets. An intent the data cannot
support degrades to the table plus a note (*"A pie chart does not fit this
result; showing a bar chart"*) — **never an error**: the numbers are correct
whatever picture was asked for. A stored intent that will not even parse is
treated as Auto (logged `tile_chart_config_unreadable`).

**Why the KPI is computed here and not in the browser.** Which column is the
value, how it is written, and whether the extra rows are context or clutter were
decided in a React component that knew nothing about the one answering the same
question in chat. `plan_kpi` is one planner for both, which is what keeps a tile
and a chat turn showing "total revenue" from disagreeing.

### B5 · The cache write

`_store(tile, result, row)` writes `sql_hash` (the fingerprint), the whole
payload, `row_count`, `computed_at`, `duration_ms`, and the error code and
message.

**A failed refresh is cached too.** Without it, a tile whose query is broken
re-runs that query on every tick of every open browser — the worst thing a
dashboard can do to a database.

A duplicated tile deliberately does **not** inherit the cache: a copy has its
own clock and its own first refresh.

---

## 4. Every failure, and what the user sees

`TileResult.status = "ERROR"` is a **value**, not an exception, at every level:
one broken tile shows its own error while the other eleven render. The response
is a 200 with an `error` object in that tile's entry.

| Code | Raised when | What the user does about it |
|---|---|---|
| `E_FORBIDDEN` | the tile's connection is not the caller's (checked before anything is decrypted) | — (should not happen through the UI) |
| `E_CONNECTION_REMOVED` | `connection_id` is NULL, or the row is gone | point the tile at another connection |
| `E_NO_SNAPSHOT` | the connection has never been synced | sync the connection |
| `E_SCHEMA_CHANGED` | the guard rejected a name the snapshot no longer has | re-sync, then check the SQL again |
| `E_SQL_REJECTED` | empty statement, or a rejection with no stated rule | fix the SQL |
| *guard `rule_id`* | any other rejection — `E_NOT_A_SELECT`, `E_NODE_NOT_ALLOWED`, `E_SYSTEM_TABLE`, `E_MULTI_STATEMENT`, `E_STAR_NOT_ALLOWED`, `E_FUNCTION_NOT_ALLOWED`, `E_PARSE`, … | the guard's own message and hint are shown verbatim |
| `E_QUERY_FAILED` | the database refused or timed out | the driver's message |
| `E_CONNECTOR` | the connector could not be built (bad credentials, unreachable host) | fixes the whole group at once |
| `E_INTERNAL` | anything uncaught | logged with a stack trace; the other tiles are unaffected |

Failures that are **not** tile errors, because they happen while a person is
watching and there is no tile yet:

| Raised | Where | HTTP |
|---|---|---|
| `ValidationError` "sync this connection's schema" | draft, save | 422 |
| `LLMError` "the model could not produce a query" | draft (`generate` FAILED) | 502 via RFC 7807 |
| `SqlRejectedError` | tile save | 422 with the rule id |
| `NotFoundError` | any resource not owned by the caller | **404, never 403** |
| `ConflictError` | duplicate dashboard name | 409 |

And two failure modes that are silently absorbed by design: a chart that cannot
be planned or compiled (the table stands), and a KPI that cannot be computed
(the tile stands). Both are logged, neither is surfaced as an error, because
**presentation never outranks numbers**.

---

## 5. What the dashboard pipeline deliberately does not have

- **No model at refresh.** See §1. This is the single most important property of
  the feature.
- **No history.** A tile has no conversation. `history=[]` at draft time is a
  disclosure decision, not an omission.
- **No `inspect` node.** The structural checks (`C_EMPTY_RESULT`,
  `C_GRANULARITY`, …) exist to steer a *regeneration*, and there is nothing to
  regenerate: the statement is the user's, approved once, and re-running it is
  the whole contract.
- **No `present` node.** Nobody narrates a tile. That is the difference between
  a dashboard and a report — and the reason a dashboard works fine on a `NONE`
  disclosure policy while a report refuses to run on one: **no result value ever
  reaches a model on this path**, at any policy, so there is nothing for the
  policy to gate. The rows go from the customer's database to the customer's own
  browser.
- **No background scheduler.** Refresh is driven by an open browser. A dashboard
  nobody has open computes nothing — which is a deliberate cost decision, not an
  oversight ([dashboards.md §10](dashboards.md)).

---

## 6. Sharp edges, verified in code (2026-08-12)

1. **`refresh` is a `POST` that writes.** It writes cache rows (flushed by the
   service, committed by the request's session), so it is not
   idempotent-cheap: `force=true` from many open tabs at once is the one way to
   turn the cache from a shield into a stampede. The per-tile interval is what
   normally prevents that.

2. **The cache is per tile, not per statement.** Two tiles with identical SQL on
   one connection are two queries. Deduplicating them would need the fingerprint
   to be the cache key rather than the tile id — a real optimisation, and a
   change to what "Refresh now" means for the tile beside it.

3. **A tile draft never runs `route`.** `classify=False` is the default, so
   *"how is the weather"* can produce valid SQL that the guard accepts (it
   resolves, it is a SELECT, it is safe) and a preview with nonsense in it. The
   user sees the preview before saving, which is why this is acceptable here and
   not acceptable for a report block, whose answer is stored rather than shown.

4. **A read-after-write race is app-wide, not a dashboards bug.** `get_db`
   commits in FastAPI's dependency teardown, which is not ordered before the
   response reaches the client, so a `GET` issued the instant a write returns can
   be served from before that write's commit. The page works around it by
   splicing the **returned** row into its state instead of re-reading. See
   [dashboards.md](dashboards.md) — fixing it properly means changing the session
   dependency for every route.
