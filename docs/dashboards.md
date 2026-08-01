# Dashboards — the build spec

**Status: not built. This file is the instruction, not a description.**

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

Still absent: `services/sql_draft_service.py`, `services/dashboard_service.py`,
`api/v1/dashboards.py`, `api/v1/drafts.py`, and every frontend dashboard
page/component (`DashboardsPage.tsx`, `dashboard.tsx`, `tile-editor.tsx`).
§3's `dashboard_service.refresh` is now unblocked — the tables exist, and the
part of it that is not CRUD (group by connection, one connector per connection,
`Semaphore(4)`) is already built and tested as `query_service.execute_many`, so
item 4 maps `dashboard_tiles` rows to `TileRequest`s and does the caching around
it. Next step is §12, item 3.

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
| Render a result table | `ResultTable` — **extract to `ui.tsx`**, don't copy | [`chat.tsx:345`](../frontend/src/components/chat.tsx#L345) |
| NL → SQL | `retrieve` → `generate` → `validate` nodes | [`pipeline/nodes/__init__.py:237,418,518`](../backend/app/pipeline/nodes/__init__.py#L418) |
| Resolve an `LlmConfig` into a callable model | `resolve_llm(config, box, min_max_tokens=0)` (lifted; `run_service` and `semantic_service` both use it) | [`services/query_service.py`](../backend/app/services/query_service.py) |
| Auth/session/db deps | `CtxDep`, `DbDep`, `SettingsDep` | [`api/deps.py`](../backend/app/api/deps.py) |

Two copies of the policy builder is how one of them silently stops matching the
guard. Lift, don't copy — that applies to `_resolve_llm` and `ResultTable` too.
The natural home for the lifted backend helpers is the new
`services/query_service.py`; `run_service` then imports them from there. (The
backend half of that is done — `run_service` and `semantic_service` now import
all four. `ResultTable` is still to extract, in §7.)

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
- `tests/unit/test_tile_charts.py` — a stored `ChartIntent` naming a column the
  result no longer has degrades to a table with a note, not a 500; a `NULL`
  `chart_config` re-plans per result.
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
[ ] 3  sql_draft_service.py + POST /sql/drafts + /sql/drafts/validate     (§5)
[ ] 4  dashboard_service.py + /dashboards routes + per-tile cache         (§6)
[ ] 5  DashboardsPage: list, grid, tile shell, one-tick refresh scheduler (§7)
[ ] 6  Tile editor: Ask tab + Write-SQL tab + chart picker + rate picker  (§8)
[ ] 7  Later: filters · sharing · export · "add to dashboard" from a chat
       run · scheduled server-side warm refresh
```

Steps 1–4 are a complete backend: `curl` can create a dashboard, draft SQL from
a question, save it as a tile and refresh it. **Verify it there before writing a
line of grid code** — the guard tests in §10 are the gate, not the UI.

Steps 5–6 together are the first thing a user can use; a grid with no way to add
a tile, or an editor with nowhere to put one, is not shippable in either
direction — plan to land them in the same stretch.
