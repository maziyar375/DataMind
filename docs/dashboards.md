# Dashboards — the build spec

**Status: §12 items 1–6 built — the feature works end to end. A user can
create a dashboard, add a tile by asking or by writing SQL, pick its chart and
its refresh rate, and watch it refresh. Item 7 (filters, sharing, export,
"add to dashboard" from a chat run, scheduled warm refresh) is not started.**
**This file is the instruction as well as the record — see "Progress so far".**

Multiple dashboards per user; a Superset/Power-BI-shaped grid of tiles; each
tile bound to *its own* connection **and its own refresh rate**. A tile's SQL is
written one of two ways, both first-class:

1. **Ask in plain language** — pick a connection, pick an LLM provider, type the
   question, get SQL back as an **editable draft**.
2. **Write the SQL yourself** — the textarea is the same one; nothing about path
   1 is privileged.

Either way the user picks the chart type (or leaves it on *Auto*). Neither path
is trusted: the guard runs at preview, at save, and at every single refresh.

## Progress so far

| Date | What happened |
|---|---|
| 2026-07-31 | First spec written and committed (`plan creating dashboard`). No code. |
| 2026-08-01 | Spec rewritten to this scope: NL-authoring is the primary path (was "promote from chat"), refresh rate is **per tile**, chart type is user-selectable. Still no code. |
| 2026-08-01 | **§12 item 1 built.** `services/query_service.py` with `execute_saved_sql` + `execute_many`, the four helpers lifted out of `run_service` (`latest_snapshot`, `policy_from_snapshot`, `resolve_llm`, `bind_connector`; `semantic_service` now shares `resolve_llm` too), and `tests/unit/test_query_service.py` (55 tests, incl. the hostile corpus replayed through a tile). `make test` / `make guard` / `make lint` green. |
| 2026-08-01 | **§12 item 2 built.** `Dashboard` / `DashboardTile` / `DashboardTileCache` in `infra/db/models.py`, `DashboardStatus` / `TileType` / `SqlOrigin` in `domain/value_objects`, migration `0005_dashboards.py` — applied to the dev database (`0004 → 0005`) and the DDL read back. `tests/unit/test_dashboard_models.py` (16 tests) replays the migration against a recorder and diffs it against the ORM column by column, so the two definitions cannot drift apart unnoticed. |
| 2026-08-01 | **§12 item 3 built.** `services/sql_draft_service.py` (`draft_sql` + `validate_sql`), `api/v1/drafts.py` with `POST /sql/drafts` and `POST /sql/drafts/validate`, `SqlDraftRead`/`TileResultRead` in `api/schemas.py`. `tests/unit/test_sql_drafts.py` (18) + `tests/unit/test_drafts_api.py` (9), and `test_openapi_has_no_secrets.py` now walks every schema the app can *return* rather than two named models. Exercised against the running stack: hand-written SQL and a plain-language question both come back VALID with a live preview off the `sales` fixture; `SELECT … ; DROP TABLE …` comes back REJECTED with `E_MULTI_STATEMENT` and no preview. |
| 2026-08-01 | **§12 item 4 built.** `services/dashboard_service.py` (CRUD, guard-on-save, the per-tile cache, `refresh`), `api/v1/dashboards.py` with all twelve routes, and the dashboard DTOs in `api/schemas.py`. `tests/unit/test_dashboards_api.py` (30), `test_dashboard_cache.py` (20), `test_dashboard_service.py` (12). **Steps 1–4 verified end to end with curl** against the running stack: create → draft from a question → save as a tile → refresh (ran, 5 rows) → refresh again (served from cache, same `computed_at`) → `force=true` (new stamp) → edit the SQL (missed the cache inside its TTL) → duplicate → layout → delete (tiles and cache rows went with it). Hostile SQL was refused at save with `E_SQL_REJECTED`. |
| 2026-08-01 | **§12 item 5 built.** `pages/DashboardsPage.tsx` (index → open, view/edit toggle, settings drawer), `components/dashboard.tsx` (grid, tile shell, the four tile bodies, the one-tick scheduler), `components/dashboard-schedule.ts` (the due-tile rule, DOM-free) with a runnable check (`npm run test:schedule`, 9 cases). `ResultTable` **moved** from `chat.tsx` to `ui.tsx` (§2). `react-grid-layout@1.5.4` added **with the owner's approval**, and the web image rebuilt so a fresh container has it. `npm run typecheck` + `npm run build` green. |
| 2026-08-01 | **§12 item 6 built.** `components/tile-editor.tsx` — one modal, two tabs, one textarea, one debounced guard check; the chart, refresh, row-cap and tile-type pickers; `DashboardsPage` wires it to "Add tile" and to a tile's *Edit*. `tests/unit/test_tile_charts.py` (19) pins the exact `chart_config` payload the editor builds against `ChartIntent`, which is `extra="forbid"` and silently degrades to Auto when it disagrees. Replayed against the running stack: check → save with a picked bar (`chart_source: model`) → back to Auto (`heuristic`) → a row cap of 99,999 stored as the connection's 1,000 → a hostile edit refused with `E_SQL_REJECTED` → a TEXT tile stored with no SQL and no connection → deleted. **A read-after-write race was found and designed around** — see §8. |
| 2026-08-01 | **`E_LLM` on the Ask tab traced and fixed** — `PROMPT_VERSION` **v6 → v7**. Not a dashboards bug: unbounded fields in `SqlProposal` let a model ramble past `max_tokens` and truncate its own JSON, losing correct SQL. `tables_used` deleted (read nowhere) and an `_OUTPUT_RULES` envelope added to the three SQL prompts. Measured 2/6 failures → 0/6, median reply 750 → 95 tokens; raising `max_tokens` was measured and makes it worse. 8/8 drafts and a full chat run green afterwards. See [pipeline.md](pipeline.md) §4. |
| 2026-08-01 | **The dashboard itself made editable** (§7): name and description edited in place in the header under edit mode, and `compact_mode` given the control it never had — the grid read it from the start and nothing could set it, so free tile placement was unreachable. PATCH round-trips verified against the running stack, including the two 422s (empty name, unknown compact mode) the UI now avoids sending. |

Not verified visually: this sandbox has no browser, so the UI was checked by
`typecheck`, `build`, the dev server transforming every new module, the
scheduler rule run against a controlled clock, and every request the editor
makes replayed against the running stack. Someone should open it.

Companion to [pipeline.md](pipeline.md) (the AI run),
[architecture.md](architecture.md) (the why) and [CODEBASE.md](CODEBASE.md)
(the stack). Read [../CLAUDE.md](../CLAUDE.md) first — the four invariants
there apply here unchanged, and §9 below says exactly how.

---

## 1. The one thing that makes this hard

Everything else is CRUD and UI. This is the part to get right:

> **Today the only path from SQL to a driver runs through the LLM pipeline.**
> A dashboard needs *"execute this stored SQL against this connection, no model
> involved"* — a second entry point into the guarded execution path.

A second entry point is a second chance to bypass the guard. It doesn't get
one: stored SQL is **re-validated on every execution**, never trusted because
it was validated when it was saved. And now that a user may *type* SQL into a
tile directly, `dashboard_tiles.sql` is hostile input by definition — not model
output that happens to be user-visible. §3 is the whole feature; build it first.

---

## 2. What to reuse (do not rebuild)

| Need | Already exists | File |
|---|---|---|
| SQL validation + rewrite | `guard(sql, policy) -> (report, executable)` | [`sqlguard/__init__.py:12`](../backend/app/sqlguard/__init__.py#L12) |
| Build a `GuardPolicy` from a snapshot | `policy_from_snapshot` (**lifted**, §3 — takes an optional lower `max_rows`) | [`services/query_service.py`](../backend/app/services/query_service.py) |
| Latest schema snapshot for a connection | `latest_snapshot(db, connection_id)` (lifted) | [`services/query_service.py`](../backend/app/services/query_service.py) |
| Decrypt a connection's password / bind a connector | `bind_connector(connection, box)` (lifted; was inline in `execute_run`) | [`services/query_service.py`](../backend/app/services/query_service.py) |
| Read-only execution, timeouts, row caps | `build_connector(...)` → `DatabaseConnector` | [`infra/connectors/factory.py:24`](../backend/app/infra/connectors/factory.py#L24) |
| Decide/repair a chart for a result shape | `profile_result` → `plan_chart` → `compile_vega_lite` | [`charts/__init__.py:170,469,564`](../backend/app/charts/__init__.py#L469) |
| Chart intent schema | `ChartIntent` (serialise this into `tiles.chart_config`) | [`charts/__init__.py:59`](../backend/app/charts/__init__.py#L59) |
| Render a Vega-Lite spec, theme-aware | `<VegaChart spec={...}/>` | [`VegaChart.tsx`](../frontend/src/components/VegaChart.tsx) |
| Render a result table | `ResultTable` (**moved** to `ui.tsx`, §7 — `chat.tsx` imports it from there now) | [`ui.tsx`](../frontend/src/components/ui.tsx) |
| NL → SQL | `retrieve` → `generate` → `validate` nodes | [`pipeline/nodes/__init__.py:237,418,518`](../backend/app/pipeline/nodes/__init__.py#L418) |
| Resolve an `LlmConfig` into a callable model | `resolve_llm(config, box, min_max_tokens=0)` (lifted; `run_service` and `semantic_service` both use it) | [`services/query_service.py`](../backend/app/services/query_service.py) |
| Auth/session/db deps | `CtxDep`, `DbDep`, `SettingsDep` | [`api/deps.py`](../backend/app/api/deps.py) |

Two copies of the policy builder is how one of them silently stops matching the
guard. Lift, don't copy — that applies to `_resolve_llm` and `ResultTable` too.
The natural home for the lifted backend helpers is the new
`services/query_service.py`; `run_service` then imports them from there. (All
of this is now done: `run_service` and `semantic_service` import the four
helpers, and `ResultTable` was moved — not copied — into `ui.tsx`.)

---

## 3. Phase 1 — the tile execution service

**New file: `backend/app/services/query_service.py`.** One entry point:

```python
async def execute_saved_sql(
    db, settings, *, sql: str, connection: DatabaseConnection,
    owner_id: UUID, chart_intent: ChartIntent | None = None,
    max_rows: int | None = None,
    # As built: the batch path passes both, and then owns closing the
    # connector. Omitted, this function loads and opens (and closes) its own.
    connector: DatabaseConnector | None = None,
    snapshot: dict | None = None,
) -> TileResult: ...
```

Six rules, each of which is a test in §10:

1. **Re-validate every time.** Load the connection's *current* snapshot, rebuild
   the `GuardPolicy`, call `guard()`. Never store an "already approved" flag and
   skip. A re-sync that dropped a table must make the tile **fail closed** with
   `E_SCHEMA_CHANGED` and a readable message — not run a stale query, not return
   an empty result that looks like "no data".
2. **Re-check ownership every time**, at execution, not only at tile save:
   `connection.owner_id == owner_id`. Connections can be deleted or the tile
   row edited underneath you.
3. **Containment is the connection's, not the tile's.** `max_rows` and
   `statement_timeout_ms` come from `DatabaseConnection`
   ([models.py:122](../backend/app/infra/db/models.py#L122)); a tile override may
   only *lower* them. The connector's read-only transaction does the rest.
4. **Always close the connector** — `try/finally`, mirroring
   [`run_service.py:176-227`](../backend/app/services/run_service.py#L176).
5. **A tile failure is a value, not an exception.** Return
   `TileResult(status="ERROR", error_code=..., error_message=...)`. One broken
   tile must never fail the dashboard response.
6. **The chart is decided here, not in the browser.** Profile the result, then
   `plan_chart(profile, suggestion=chart_intent)` — the stored intent goes in as
   a *suggestion* so the user's explicit choice gets the same name-check and
   shape-repair every model suggestion gets. Ship `plan.source` in the payload:
   when the user asked for a pie of 400 categories and `_fit` demoted it to a
   bar, the UI says so. An unfittable intent degrades to the table plus a note —
   **never** an error.

**`TileResult`** (a dataclass in the service, mapped to a DTO in the API):
`status`, `columns`, `rows`, `row_count`, `truncated`, `duration_ms`,
`computed_at`, `vega_spec`, `chart_source`, `chart_note`, `error_code`,
`error_message`.

`error_code` as built, for the UI to branch on: `E_SCHEMA_CHANGED` (the guard
refused a name the snapshot no longer has — re-sync and edit the tile),
`E_NO_SNAPSHOT` (never synced; nothing is dialled), `E_FORBIDDEN` (the
connection is not the caller's; nothing is decrypted or dialled),
`E_QUERY_FAILED` (the driver refused or timed out), `E_INTERNAL`, and otherwise
the guard's own `rule_id` verbatim (`E_NOT_A_SELECT`, `E_FORBIDDEN_CONSTRUCT`,
…) so a hand-written statement gets the same sentence the semantic-layer editor
would have shown.

**Then `backend/app/services/dashboard_service.py`:** CRUD plus

```python
async def refresh(dashboard_id, owner_id, tile_ids=None, force=False) -> dict[UUID, TileResult]
```

which **groups tiles by `connection_id` and builds one connector per
connection**, running tiles under an `asyncio.Semaphore` (cap 4). Twelve tiles
on one database must not open twelve connections. `tile_ids` is not an
optimisation — with per-tile refresh rates (§7) it is the normal call shape: the
browser asks for the tiles that are *due*, not for the whole dashboard.

That grouping is already built, as
`query_service.execute_many(db, settings, *, requests: list[TileRequest],
owner_id) -> dict[UUID, TileResult]` — it needs no dashboard tables, so it
landed with item 1. `refresh` maps rows to `TileRequest`s and owns the cache;
it does not re-implement the fan-out. One rule it adds that is easy to lose:
**every database read happens before the tiles fan out** — one snapshot per
connection, awaited in sequence — because an `AsyncSession` is not safe for
concurrent use.

Layering: `services` may reach `infra`; nothing here touches `app.domain`'s
purity or the self-containment of `sqlguard`/`semantic`. Confirm with
`make lint` — contracts live in [`pyproject.toml`](../backend/pyproject.toml#L82).

---

## 4. Phase 2 — data model

**Edit [`infra/db/models.py`](../backend/app/infra/db/models.py); new
`migrations/versions/0005_dashboards.py`** (`down_revision = "0004"`, following
the shape of [`0004_clarify_switch.py`](../backend/app/infra/db/migrations/versions/0004_clarify_switch.py)).

### `dashboards`

| column | type | note |
|---|---|---|
| `id` | uuid pk | |
| `owner_id` | fk users **CASCADE** | multiple dashboards per user; scoped on every route |
| `name`, `description` | str/text | unique `(owner_id, name)`, mirroring `uq_conn_owner_name` |
| `status` | str(20) | `ACTIVE \| ARCHIVED` |
| `grid_columns` | int, default 12 | |
| `row_height_px`, `gap_px` | int | defaults 60 / 12 |
| `compact_mode` | str(20) | `VERTICAL \| NONE` |
| `palette` | str(30) | key into the validated palette set — §7 |
| `theme_override` | str(20) | `INHERIT \| DARK \| LIGHT` |
| `default_refresh_interval_seconds` | int, default 0 | the *fallback* for tiles that don't set their own; `0` = manual only |
| `+ TimestampMixin` | | |

### `dashboard_tiles`

| column | type | note |
|---|---|---|
| `id` | uuid pk | |
| `dashboard_id` | fk **CASCADE** | |
| `connection_id` | fk `database_connections` **SET NULL** | **not CASCADE** — a deleted connection must leave a tile that says "connection removed", not silently delete the user's layout |
| `llm_config_id` | fk `llm_configs` **SET NULL**, nullable | which provider drafted the SQL; used to re-ask, and shown in the editor. Provenance only — never consulted at refresh |
| `title` | str(200) | |
| `tile_type` | str(20) | `CHART \| TABLE \| METRIC \| TEXT` |
| `question` | text, nullable | the plain-language question that produced the draft, kept so "edit → re-ask" works and the user can see what they meant six weeks later |
| `sql` | text | empty for `TEXT` tiles |
| `sql_origin` | str(20) | `GENERATED \| GENERATED_EDITED \| HANDWRITTEN` — provenance only, **never a trust signal**. The guard cannot tell them apart and must not |
| `chart_config` | jsonb, **nullable** | a serialised `ChartIntent`, optionally with a single-colour override. **`NULL` means Auto**: `plan_chart` decides afresh on each result |
| `max_rows` | int, nullable | may only lower the connection's |
| `refresh_interval_seconds` | int, nullable | **the per-tile rate.** `NULL` = inherit the dashboard's default; `0` = manual only. This column is the feature — do not collapse it into a dashboard-level setting |
| `grid_x/y/w/h`, `position` | int | layout lives **per tile**, not in one dashboard-level JSONB: a drag then saves one row and two open tabs cannot lose each other's edits |
| `+ TimestampMixin` | | |

### `dashboard_tile_cache`

`tile_id` pk · `sql_hash` · `result` jsonb · `row_count` · `computed_at` ·
`duration_ms` · `error_code` · `error_message`. Written in Phase 4; put it in
the same migration so there is one DDL step.

Add `DashboardStatus`, `TileType`, `SqlOrigin` to
[`domain/value_objects`](../backend/app/domain/value_objects/__init__.py).
Keep the columns plain `String` — like `runs.status`, so a new member needs no
DDL.

**As built**, the few things the table above left open: `palette` defaults to
`"default"` (the measured palette already in `VegaChart.tsx` — §7's five named
sets do not exist yet, and a default naming one of them would be a dangling
key); a new tile is `4 × 4` at `0,0`; `dashboard_tile_cache` caches **failures
too**, which is what its `error_code`/`error_message` are for — without that, a
tile whose query is broken re-runs it on every tick of every open browser. The
ORM adds `Dashboard.tiles` (`cascade="all, delete-orphan"`, ordered by
`position`) so Phase 4 loads a dashboard in one go.

---

## 5. Phase 3 — plain-language authoring (`POST /sql/drafts`)

This is the primary way a tile gets its SQL, so it lands **before** the grid:
it is testable over HTTP alone, and it is the only genuinely new backend
behaviour left after §3.

**New file `backend/app/services/sql_draft_service.py`.**

```python
async def draft_sql(
    db, settings, *, connection_id: UUID, llm_config_id: UUID,
    question: str, owner_id: UUID,
) -> SqlDraft: ...
```

Reuse `retrieve` → `generate` → `validate` from
[`pipeline/nodes`](../backend/app/pipeline/nodes/__init__.py) with a `RunState`
built from `uuid4()` placeholders and a no-op `emit` — no conversation, no
message, no `runs` row, no SSE. One repair attempt on a rejected draft (the
`validate` node already loops back to `generate`; cap it at 1 here — a draft is
interactive and the user is watching).

Returns `{sql, validation_report, referenced_tables, chart_suggestion,
preview}`, where `preview` is a capped sample execution through
`execute_saved_sql` — so what the user sees in the editor is produced by exactly
the code that will run the tile at 03:00.

> **Known wart:** `RunState` requires `run_id` and `conversation_id`. Synthetic
> UUIDs are the honest cheap answer for a draft that is not a run. Comment it
> where it happens.

`retrieve` renders the schema against the connection's `DisclosurePolicy` and
its semantic layer, unchanged — a draft is not a loophole around disclosure. If
the connection has no schema snapshot, fail with a message telling the user to
sync it, not with an empty prompt to the model.

**Routes** — new `backend/app/api/v1/drafts.py`:

```
POST /sql/drafts            {connection_id, llm_config_id, question} -> draft
POST /sql/drafts/validate   {connection_id, sql} -> validation report + preview
```

The second is the hand-written path *and* the "I edited what the model gave me"
path — one endpoint, because they are the same thing. It runs the guard and a
capped preview; it never calls a model.

**As built.** Both routes answer with one DTO, `SqlDraftRead`, whose `preview`
is a `TileResultRead` — *the same shape a tile returns after a refresh* (§6),
because a preview that could differ from a refresh is a preview that lies. The
cap is `PREVIEW_MAX_ROWS = 50`, well under any connection's own, and it reaches
both the rewriter's `LIMIT` and the driver. `draft_sql` opens **one** connector
and lends it to the preview; `validate_sql` has none to lend, so
`execute_saved_sql` opens and closes its own.

A rejection is a **200 with `validation_status: "REJECTED"`**, not a 4xx: the
editor renders the guard's reasons inline the way the metric editor does, and a
4xx would make "the model wrote something I can show you" indistinguishable from
"your request was malformed". `chart_suggestion` is the *heuristic's* read of
the preview's shape (`plan_chart` with no suggestion) — deterministic, free, and
only ever a default for the editor's pickers.

> **Fixed in `PROMPT_VERSION` v7 — it was never a drafts bug.** Drafts made an
> inherited failure obvious: `generate` asks the provider for structured output
> with the config's `max_tokens`, and on the widest schema blocks the reply came
> back `finish_reason=length` with truncated JSON, surfacing as `E_LLM` ("did
> not return valid SqlProposal JSON"). The cause was **unbounded fields in the
> structured-output contract**, not the provider padding: given a 28,892-char
> schema block the model wrote correct SQL in ~90 tokens and then kept going —
> first filling `tables_used` with 1,350 entries (42 tables repeated 61 times),
> and once that field was removed, deliberating *inside the `sql` string* — until
> the cap cut the reply mid-string. Two changes in `pipeline/prompts` and
> `pipeline/contracts` fixed it for the run path and the draft path at once:
> `tables_used` deleted (nothing read it; the trusted table list is the guard's)
> and an `_OUTPUT_RULES` envelope instruction added to all three SQL prompts.
> Measured: 2/6 failures → 0/6, median reply 750 tokens → 95. **Raising
> `max_tokens` was measured and is not the fix** — at 4,096 the failure rate got
> *worse* and the median reply was exactly the cap at both sizes, because the
> rambling expands to fill whatever budget it is given.

---

## 6. Phase 4 — the dashboard API

**New file `backend/app/api/v1/dashboards.py`**, registered in
[`api/v1/__init__.py`](../backend/app/api/v1/__init__.py); DTOs in
[`api/schemas.py`](../backend/app/api/schemas.py).

```
GET    /dashboards                        owner-scoped list
POST   /dashboards
GET    /dashboards/{id}                   dashboard + tiles, NO results
PATCH  /dashboards/{id}                   name, grid, palette, default refresh
DELETE /dashboards/{id}
POST   /dashboards/{id}/tiles
PATCH  /dashboards/{id}/tiles/{tid}       incl. sql, chart_config, refresh rate
DELETE /dashboards/{id}/tiles/{tid}
POST   /dashboards/{id}/tiles/{tid}/duplicate
PATCH  /dashboards/{id}/layout            bulk positions, one call per drag-end
POST   /dashboards/{id}/tiles/{tid}/data  execute one tile   (?force=true)
POST   /dashboards/{id}/data              {tile_ids?: [...]}  (?force=true)
```

House rules that apply: **literal paths declared above `/{id}` paths**;
business logic in the service, the route is HTTP shape only; errors through
[`api/errors.py`](../backend/app/api/errors.py) as problem+json.

Per-tile payload: `{status, columns, rows, row_count, truncated, duration_ms,
computed_at, vega_spec, chart_source, chart_note, error}`. `computed_at` is not
optional — with every tile on its own clock, "as of 14:32" is the only way a
reader tells a 30-second tile from the hourly one sitting next to it.

**Cache** (`dashboard_tile_cache`): serve cached when
`now - computed_at < effective_refresh_interval(tile)` **and** `sql_hash`
matches, unless `?force=true`. `effective_refresh_interval` is
`tile.refresh_interval_seconds` when not null, else the dashboard's default;
when that resolves to `0` the cache is served on any hit until the user presses
refresh. Without this, five people with a 30-second tile open is a load
generator pointed at the customer's database.

**As built.** The stored hash is a fingerprint of `(connection_id, sql,
max_rows, chart_config)`, not of the SQL alone — the column keeps the name it
was given in §4, but a tile switched from a pie to a line may not keep serving
the pie until its interval happens to elapse. **Failures are cached too**,
which is what `error_code`/`error_message` on the cache row are for: a tile
whose query is broken would otherwise re-run it on every tick of every open
browser. A tile whose connection was deleted (`SET NULL`, §4) answers
`E_CONNECTION_REMOVED` without opening anything.

`GET /dashboards/{id}` returns each tile with `effective_refresh_interval_
seconds` resolved and its connection/model *names* — the chips §7 draws — so
the scheduler needs no second copy of the inheritance rule and the grid needs
no extra round trip. Names only: no host, no username, nothing else from inside
a connection. Every tile-returning route resolves them, including `POST
/tiles`, so the editor can render the tile it just saved.

> **Two bugs the unit tests could not see**, both the `onupdate` + async gotcha
> from CLAUDE.md, both found by the curl run and now covered by
> `test_dashboard_service.py`: `PATCH /layout` flushed and serialised rows
> whose `updated_at` was expired (`MissingGreenlet`, a 500), and
> `DashboardRead.model_validate(dashboard)` read the lazy `Dashboard.tiles`
> relationship during serialisation. A detached ORM object in a fake answers
> both happily; a real session does not. The response builder now copies every
> field *except* `tiles`, and every UPDATE is followed by a `refresh`.

---

## 7. Phase 5 — the dashboard UI

New `pages/DashboardsPage.tsx`, `components/dashboard.tsx`,
`components/tile-editor.tsx`. Edit [`App.tsx:12`](../frontend/src/App.tsx#L12)
(the `View` union — add `'dashboards'` — the nav item, the render branch),
[`api/client.ts`](../frontend/src/api/client.ts) (a `dashboards` export beside
`connections`), [`api/types.ts`](../frontend/src/api/types.ts).

Shape it like Superset/Power BI, because that is what users expect:

- **List → open.** A dashboards index (cards: name, tile count, last refreshed),
  then a single dashboard filling the page. Rename/duplicate/archive from the
  card's kebab.
- **View mode vs edit mode**, one toggle in the header. View locks dragging and
  hides tile chrome; edit unlocks the grid and shows "Add tile".
- **Grid:** `react-grid-layout`, `cols`/`rowHeight` from the dashboard's own
  settings; `onDragStop`/`onResizeStop` → debounced `PATCH /layout`. It is a
  layout engine, not a styled component library, so it does not breach the "no
  component library" rule — but it is the first frontend dependency added since
  the initial stack, so it needs **the owner's yes before `npm install`**.
- **Tile shell:** title, connection chip, model chip when generated,
  `computed_at`, a refresh-rate chip (`30s`, `5m`, `Manual`), kebab (refresh now
  / edit / duplicate / delete), three states — `Spinner`, `ErrorNote`, data. All
  from [`ui.tsx`](../frontend/src/components/ui.tsx).
- **Tile body:** `CHART` → `VegaChart`; `TABLE` → the extracted `ResultTable`;
  `METRIC` → new big-number tile (one row × one numeric column); `TEXT` → plain
  text, no SQL, no connection.
- **Settings drawer:** grid columns, row height, gap, palette, theme override,
  default refresh rate.

**The dashboard is editable, not only its tiles.** Edit mode turns the header's
name and description into fields, committed on blur or Enter and reverted on
Escape — one PATCH per edit, not one per keystroke, and an empty name reverts
rather than sending a 422. Renaming from a card on the index screen still
works; it is just no longer the only way. The settings drawer also carries
**tile placement** (`compact_mode`), which the grid had been reading since the
first tile was drawn with nothing able to set it — so every dashboard was stuck
compacting upward, and "leave a tile where it is put" was unreachable despite
being the reason the layout is stored per tile (§11).

### Per-tile refresh — one scheduler, not one timer per tile

Each tile has its own rate, and the naive reading of that is a `setInterval` per
tile. Don't: twelve timers produce twelve requests that arrive interleaved, each
opening its own connector.

**One `setInterval(1000)` per open dashboard.** Each tick, compute which tiles
are due (`now - computed_at >= effective_interval`, skipping manual tiles and
tiles already in flight), and if any are, fire **one**
`POST /dashboards/{id}/data {tile_ids:[…]}` and fan the results out. Tiles due
in the same second coalesce into one request for free; a 30s tile and a 1h tile
never wait on each other.

Pause on `document.hidden` and resume on `visibilitychange`, refreshing whatever
went overdue while hidden — exactly once, not once per missed interval. A
forgotten background tab that polls forever is how this feature becomes the
reason someone's production database is slow.

Rate options: `Manual / 15s / 30s / 1m / 5m / 15m / 1h / 6h / 24h`, plus
"Inherit dashboard default" as a tile's initial value.

**As built.** The due-tile rule is a pure function in
[`dashboard-schedule.ts`](../frontend/src/components/dashboard-schedule.ts) —
no React, no DOM — because its two failure modes are "a background tab hammers
the customer's database" and "a 30-second tile silently shows ten-minute-old
numbers". `npm run test:schedule` runs it against a frozen clock (Node strips
the types; no test framework, no new dependency). The backend already resolves
`NULL` → the dashboard's rate and ships it as
`effective_refresh_interval_seconds`, so the browser holds no second copy of
the inheritance rule. A tile that is *re*-loading keeps its numbers on screen
with "refreshing" in the header: blanking a good result for half a second reads
as a fault. A failed poll leaves the last results in place — a stale number
carrying its own timestamp beats an empty grid.

### Colour — read this before adding a picker

The palette in [`VegaChart.tsx`](../frontend/src/components/VegaChart.tsx) is
**measured, not chosen**: OKLab ΔE, Machado-2009 CVD simulation, contrast
against the chart's own surface, per-mode accent anchoring — the numbers are in
that file's header. A free hex picker destroys all of it silently.

Ship **~5 pre-validated palettes** (accent, categorical, sequential, warm,
cool), each run through the same validator in both themes, plus a single-colour
override for single-series tiles. If a palette is added or changed, re-run the
validator **for both modes** — a hue swapped by eye is how a palette quietly
stops being readable.

> **Not built, deliberately.** The settings drawer offers exactly one palette,
> "Default (measured)", and says why. Shipping four more chosen by eye is the
> precise failure the paragraph above forbids, and the validator those numbers
> came from is not in the repo — it produced the table in `VegaChart.tsx`'s
> header. Adding a palette means re-running it for **both** modes first; the
> column, the API field and the picker are all in place for when someone does.

---

## 8. Phase 6 — the tile editor

One modal, opened by "Add tile" or a tile's *Edit*. Two tabs over **one shared
SQL textarea** — the tab chooses how the text got there, not what happens to it
afterwards.

**Tab 1 — Ask.**

```
Connection ▾   Model ▾   [ "revenue by month for the last year"     ]  (Generate)
```

`POST /sql/drafts` → the SQL lands in the textarea, editable, with the guard
report and the preview table beneath it. Editing it flips `sql_origin` to
`GENERATED_EDITED`; the `question` is kept so *Generate* can be pressed again
after a reword. Connection and model are chosen **per tile** — two tiles on one
dashboard may legitimately use different databases and different providers.

**Tab 2 — Write SQL.** The same textarea, empty, `sql_origin = HANDWRITTEN`.
`POST /sql/drafts/validate` on a debounce gives the same inline guard report as
the semantic-layer editor. No model is involved and none is required — a user
with no LLM provider configured can still build a whole dashboard.

**Then, for both tabs:**

- **Chart** — type selector (`Auto · Bar · Horizontal bar · Line · Area ·
  Scatter · Pie · Table only`), then X / Y / series pickers populated from the
  preview's columns. Defaulted from the draft's `chart_suggestion`; *Auto*
  stores `chart_config = NULL` and re-decides on every refresh. Choosing a type
  the data cannot support is allowed — the backend demotes it and says so
  (§3.6) rather than blocking the save.
- **Refresh** — this tile's rate, defaulting to "Inherit dashboard default".
- **Title**, tile type, optional `max_rows` (may only lower the connection's).
- **Save** → `POST /dashboards/{id}/tiles`, which **re-runs the guard**. The
  preview passing is not authorisation to save; the save path validates again,
  and so does every refresh after it.

**The model's SQL is a draft and hand-edits are allowed; neither is trusted.**

**As built.** [`components/tile-editor.tsx`](../frontend/src/components/tile-editor.tsx),
one modal, opened from "Add tile" (edit mode) or a tile's kebab → *Edit*.

*One check, not one per tab.* The debounced `POST /sql/drafts/validate` watches
the textarea whatever put text in it, so editing what the model wrote is
checked exactly like typing it yourself. **Connection sits above the tabs**
rather than inside the Ask tab as the sketch has it: tab 2 needs it just as
much — the guard resolves every name against that connection's snapshot — and
one control that means one thing beats two that mean the same. Changing it
clears the report, which described a different database.

*Provenance follows whoever last wrote the text*, not the tab in front.
`GENERATED` → `GENERATED_EDITED` on the first keystroke; a tile that starts on
tab 2 is `HANDWRITTEN` and stays so; switching tabs changes nothing, because
switching to tab 2 to fix one join does not erase the fact that a model wrote
the other twenty lines. The model chip is only shown, and `llm_config_id` only
stored, when a model was actually involved.

> **"Table only" is a tile type, not a chart intent.** Storing
> `chart_type: "none"` reads like "draw nothing" and does the opposite:
> [`validate_intent`](../backend/app/charts/__init__.py#L264) refuses that
> intent, so `plan_chart` falls through to the heuristic and draws whatever the
> shape suggests. The picker's *Table only* therefore sets `tile_type = TABLE`.
> `test_tile_charts.py` pins this, because the next person to read §8's option
> list will reach for `"none"` exactly as I did.

*The axis pickers are populated from the preview's columns*, never from the SQL
text — a name the result does not have loses the chart. When a re-check returns
different columns, a pick the new result can still honour is kept and one it
cannot is replaced from the fresh suggestion. **The suggestion defaults the
axes, not the type**: the type stays *Auto* (`chart_config = NULL`, re-planned
per refresh) until the user says otherwise, which is the right default for a
tile whose data changes shape.

> **A read-after-write race, found by the end-to-end run and designed around.**
> `get_db` commits in FastAPI's dependency teardown, which is **not** ordered
> before the response reaches the client: a `GET` issued the moment a write
> returns can be served from before that write's commit. Reproduced on
> `DELETE /dashboards/{id}` → `GET` returning **200** (then 404 half a second
> later); not reproduced on a tile insert in 10 tries, but the window is the
> same one. So the page splices the **returned** row into its state instead of
> re-reading — which every tile-returning route already makes possible by
> resolving the display names (§6). A re-read that lost the tile the user just
> saved would look exactly like a save that failed. **The race itself is
> pre-existing and app-wide**, not a dashboards bug, and fixing it means
> changing the session dependency for every route — worth doing, out of scope
> here.

---

## 9. Invariants — how CLAUDE.md's four apply here

1. **AST validation fails closed.** Unchanged and now load-bearing in a second
   place: `dashboard_tiles.sql` is user-controlled text — literally, since §8's
   second tab hands the user a textarea. It passes `guard()` against the
   connection's live snapshot on *every* execution. There is no "trusted tile",
   and `sql_origin` grants nothing.
2. **Containment underneath correctness.** Tiles execute through the same
   connectors, same read-only transaction, same statement timeout and row cap.
   A tile override may only tighten.
3. **Credentials encrypted, never exposed.** No dashboard read model may carry a
   password or `api_key`. A tile carries `connection_id`/`llm_config_id` and
   display names — never connection internals, never provider keys.
4. **Disclosure is explicit.** `DisclosurePolicy` gates what reaches *the
   model* — including in `POST /sql/drafts`, which renders the schema through
   the same `retrieve` path and therefore the same budget. A tile *result*
   reaching the owner's own browser is the same exposure as the chat table and
   needs no new gate. **This stops being true the moment dashboards are
   shared** — see §11.

---

## 10. Tests (write alongside, not after)

- `tests/unit/test_query_service.py` — SQL referencing a since-dropped table
  fails closed with `E_SCHEMA_CHANGED`; a tile whose connection belongs to
  another user is rejected; a tile `max_rows` above the connection's is clamped
  down; **hostile SQL written directly into `dashboard_tiles.sql` is rejected at
  refresh** (this is the test that proves dashboards did not open a guard
  bypass — model it on
  [`test_sqlguard_hostile.py`](../backend/tests/unit/test_sqlguard_hostile.py)).
- `tests/unit/test_sql_drafts.py` — a draft creates no `runs`/`conversations`
  row; a rejected draft returns the report rather than raising; the schema block
  respects the connection's `DisclosurePolicy`.
- `tests/unit/test_dashboards_api.py` — ownership scoping on every route; one
  failing tile still returns 200 with the others' data; `POST /data` with
  `tile_ids` touches only those tiles.
- `tests/unit/test_tile_charts.py` — the two cases originally listed here (a
  stored `ChartIntent` naming a column the result no longer has degrades rather
  than raising; a `NULL` `chart_config` re-plans per result) were already
  covered where the behaviour lives, in `test_query_service.py` ("rule 6: the
  chart is decided here") and `test_dashboard_cache.py`. What this file covers
  instead is the seam item 6 opened: `chart_config` is a JSON column written by
  a TypeScript file with no compile-time knowledge of `ChartIntent`, which is
  `extra="forbid"` — and a config it refuses is **indistinguishable from Auto**,
  so the user's explicit pick vanishes without an error. Every payload the
  editor can build is pinned against the model, along with the fact that all
  four connectors emit only axis types the model accepts.
- `test_dashboard_cache.py` — a changed `sql_hash` invalidates regardless of
  TTL; two tiles with different rates on one dashboard expire independently.
- The new read models are covered automatically by
  [`test_openapi_has_no_secrets.py`](../backend/tests/unit/test_openapi_has_no_secrets.py),
  which walks the generated schema — **check it actually sees the new DTOs**
  rather than assuming it does.
- Frontend: `npm run typecheck && npm run build`.

(`tests/integration/` is empty today; every test lives under `tests/unit/`.)

Gate before claiming any phase done: `make test`, `make guard`, `make lint`.

---

## 11. Decisions already made (and what would reopen them)

| Decision | Why | Reopen when |
|---|---|---|
| **NL-authoring is the primary path; "add to dashboard" from chat is not in v1** | The dashboard must stand on its own — a user who never opens chat still builds one. Promotion is a shortcut on top of a feature that has to exist first. | Steps 1–6 land; then it is about a day's work (a succeeded run already has validated SQL, a connection and a chart spec — copy them into a tile). |
| **Hand-written SQL is first-class, not an escape hatch** | Analysts do not want to argue with a model about a join they can type. It also makes the whole feature usable with no LLM provider configured. | never |
| **Refresh rate per tile, dashboard default as fallback** | A KPI over today's orders and a year-over-year rollup do not belong on the same clock. | never |
| **One scheduler tick, not one timer per tile** | §7. Twelve timers = twelve interleaved requests = twelve connectors. | never |
| **Chart type user-chosen, `NULL` = Auto** | The user knows what they want to show; the platform still gets to repair an impossible pick rather than draw something illegible. | never |
| **Owner-only, no sharing in v1** | A shared dashboard means user B reads data pulled with user A's stored credentials against a connection B does not own. That is an authorization model, not a UI feature. | There is a real answer for "who may read through this connection" — then add `dashboard_shares`. |
| **Per-tile connection** | The point of the feature; the guard policy is rebuilt per connection anyway. | never |
| **Layout per tile, not one JSONB blob** | One row per drag; no lost updates between tabs. | never |
| **Pre-validated palettes, no free hex** | §7. | Someone re-runs the validator for a new set, both modes. |
| **No dashboard filters in v1** | `QueryExecutor.execute` ([ports/database.py:133](../backend/app/domain/ports/database.py#L133)) takes no bind parameters. Filters need the port extended across all four connectors. **Never by string interpolation.** | Step 7. |
| **Cache is in Postgres, not in-process** | The reconciler already assumes multiple workers may exist; an in-process cache would go stale per worker. | never |

---

## 12. Build order

```
[x] 1  query_service.py + lift _policy_from_snapshot / _latest_snapshot /
       _resolve_llm / _bind_connection                                    (§3)
[x] 2  models + migration 0005                                            (§4)
[x] 3  sql_draft_service.py + POST /sql/drafts + /sql/drafts/validate     (§5)
[x] 4  dashboard_service.py + /dashboards routes + per-tile cache         (§6)
[x] 5  DashboardsPage: list, grid, tile shell, one-tick refresh scheduler (§7)
[x] 6  Tile editor: Ask tab + Write-SQL tab + chart picker + rate picker  (§8)
[ ] 7  Later: filters · sharing · export · "add to dashboard" from a chat
       run · scheduled server-side warm refresh
```

Steps 1–4 are a complete backend: `curl` can create a dashboard, draft SQL from
a question, save it as a tile and refresh it. **Verify it there before writing a
line of grid code** — the guard tests in §10 are the gate, not the UI.

Steps 5–6 together are the first thing a user can use; a grid with no way to add
a tile, or an editor with nowhere to put one, is not shippable in either
direction — plan to land them in the same stretch.
