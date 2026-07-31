# Dashboards — the build spec

**Status: not built. This file is the instruction, not a description.**

Multiple dashboards per user; a grid of tiles; each tile bound to *its own*
connection; per-dashboard grid/palette/refresh settings; tile SQL either
hand-written, generated from a plain-language question, or promoted from a chat
answer.

Companion to [pipeline.md](pipeline.md) (the AI run),
[architecture.md](architecture.md) (the why) and [CODEBASE.md](CODEBASE.md)
(the stack). Read [../CLAUDE.md](../CLAUDE.md) first — the four invariants
there apply here unchanged, and §7 below says exactly how.

---

## 1. The one thing that makes this hard

Everything else is CRUD and UI. This is the part to get right:

> **Today the only path from SQL to a driver runs through the LLM pipeline.**
> A dashboard needs *"execute this stored SQL against this connection, no model
> involved"* — a second entry point into the guarded execution path.

A second entry point is a second chance to bypass the guard. It doesn't get
one: stored SQL is **re-validated on every execution**, never trusted because
it was validated when it was saved. §3 is the whole feature; build it first.

---

## 2. What to reuse (do not rebuild)

| Need | Already exists | File |
|---|---|---|
| SQL validation + rewrite | `guard(sql, policy) -> (report, executable)` | [`sqlguard/__init__.py`](../backend/app/sqlguard/__init__.py) |
| Build a `GuardPolicy` from a snapshot | `_policy_from_snapshot` (private to `RunService` — **lift it**, §3) | [`services/run_service.py:683`](../backend/app/services/run_service.py#L683) |
| Latest schema snapshot for a connection | `_latest_snapshot` (same — lift it) | [`services/run_service.py:616`](../backend/app/services/run_service.py#L616) |
| Read-only execution, timeouts, row caps | `build_connector(...)` → `DatabaseConnector` | [`infra/connectors/factory.py`](../backend/app/infra/connectors/factory.py) |
| Decide/repair a chart for a result shape | `profile_result` → `plan_chart` → `compile_vega_lite` | [`charts/__init__.py:170,469,564`](../backend/app/charts/__init__.py#L469) |
| Chart intent schema | `ChartIntent` (serialise this into `tiles.chart_config`) | [`charts/__init__.py:59`](../backend/app/charts/__init__.py#L59) |
| Render a Vega-Lite spec, theme-aware | `<VegaChart spec={...}/>` | [`VegaChart.tsx`](../frontend/src/components/VegaChart.tsx) |
| Render a result table | `ResultTable` — **extract to `ui.tsx`**, don't copy | [`chat.tsx:345`](../frontend/src/components/chat.tsx#L345) |
| NL → SQL | `retrieve` → `generate` → `validate` nodes | [`pipeline/nodes/__init__.py:173,334,433`](../backend/app/pipeline/nodes/__init__.py#L334) |
| Auth/session/db deps | `CtxDep`, `DbDep`, `SettingsDep` | [`api/deps.py`](../backend/app/api/deps.py) |

Two copies of the policy builder is how one of them silently stops matching the
guard. Lift, don't copy — that applies to `ResultTable` too.

---

## 3. Phase 1 — the tile execution service

**New file: `backend/app/services/query_service.py`.** One entry point:

```python
async def execute_saved_sql(
    db, *, sql: str, connection: DatabaseConnection, owner_id: UUID
) -> TileResult: ...
```

Five rules, each of which is a test in §8:

1. **Re-validate every time.** Load the connection's *current* snapshot, rebuild
   the `GuardPolicy`, call `guard()`. Never store an "already approved" flag and
   skip. A re-sync that dropped a table must make the tile **fail closed** with
   `E_SCHEMA_CHANGED` and a readable message — not run a stale query, not return
   an empty result that looks like "no data".
2. **Re-check ownership every time**, at execution, not only at tile save:
   `connection.owner_id == owner_id`. Connections can be deleted or the tile
   row edited underneath you.
3. **Containment is the connection's, not the tile's.** `max_rows` and
   `statement_timeout_ms` come from `DatabaseConnection`; a tile override may
   only *lower* them. The connector's read-only transaction does the rest.
4. **Always close the connector** — `try/finally`, mirroring
   [`run_service.py:176-227`](../backend/app/services/run_service.py#L176).
5. **A tile failure is a value, not an exception.** Return
   `TileResult(status="ERROR", error_code=..., error_message=...)`. One broken
   tile must never fail the dashboard response.

**Then `backend/app/services/dashboard_service.py`:** CRUD plus

```python
async def refresh(dashboard_id, owner_id, tile_ids=None) -> dict[UUID, TileResult]
```

which **groups tiles by `connection_id` and builds one connector per
connection**, running tiles under an `asyncio.Semaphore` (cap 4). Twelve tiles
on one database must not open twelve connections.

Layering: `services` may reach `infra`; nothing here touches `app.domain`'s
purity or the self-containment of `sqlguard`/`semantic`. Confirm with
`make lint` — contracts live in [`pyproject.toml`](../backend/pyproject.toml#L76).

---

## 4. Phase 2 — data model

**Edit [`infra/db/models.py`](../backend/app/infra/db/models.py); new
`migrations/versions/0005_dashboards.py`** (`down_revision = "0004"`, following
the shape of [`0004_clarify_switch.py`](../backend/app/infra/db/migrations/versions/0004_clarify_switch.py)).

### `dashboards`

| column | type | note |
|---|---|---|
| `id` | uuid pk | |
| `owner_id` | fk users **CASCADE** | |
| `name`, `description` | str/text | unique `(owner_id, name)`, mirroring `uq_conn_owner_name` |
| `status` | str(20) | `ACTIVE \| ARCHIVED` |
| `grid_columns` | int, default 12 | |
| `row_height_px`, `gap_px` | int | defaults 60 / 12 |
| `compact_mode` | str(20) | `VERTICAL \| NONE` |
| `palette` | str(30) | key into the validated palette set — §6 |
| `theme_override` | str(20) | `INHERIT \| DARK \| LIGHT` |
| `refresh_interval_seconds` | int, default 0 | `0` = manual only |
| `+ TimestampMixin` | | |

### `dashboard_tiles`

| column | type | note |
|---|---|---|
| `id` | uuid pk | |
| `dashboard_id` | fk **CASCADE** | |
| `connection_id` | fk `database_connections` **SET NULL** | **not CASCADE** — a deleted connection must leave a tile that says "connection removed", not silently delete the user's layout |
| `title` | str(200) | |
| `tile_type` | str(20) | `CHART \| TABLE \| METRIC \| TEXT` |
| `sql` | text | empty for `TEXT` tiles |
| `sql_origin` | str(20) | `HANDWRITTEN \| GENERATED \| FROM_RUN` — provenance only, never a trust signal |
| `source_run_id` | uuid, **no FK** | run cleanup must not cascade into dashboards |
| `chart_config` | jsonb | a serialised `ChartIntent` + optional colour override |
| `max_rows` | int, nullable | may only lower the connection's |
| `refresh_interval_seconds` | int, nullable | overrides the dashboard's |
| `grid_x/y/w/h`, `position` | int | layout lives **per tile**, not in one dashboard-level JSONB: a drag then saves one row and two open tabs cannot lose each other's edits |
| `+ TimestampMixin` | | |

### `dashboard_tile_cache`

`tile_id` pk · `sql_hash` · `result` jsonb · `row_count` · `computed_at` ·
`duration_ms` · `error_code` · `error_message`. Written in Phase 3; put it in
the same migration so there is one DDL step.

Add `DashboardStatus`, `TileType`, `SqlOrigin` to
[`domain/value_objects`](../backend/app/domain/value_objects/__init__.py).
Keep the columns plain `String` — like `runs.status`, so a new member needs no
DDL.

---

## 5. Phase 3 — API

**New file `backend/app/api/v1/dashboards.py`**, registered in
[`api/v1/__init__.py`](../backend/app/api/v1/__init__.py); DTOs in
[`api/schemas.py`](../backend/app/api/schemas.py).

```
GET    /dashboards                        owner-scoped list
POST   /dashboards
GET    /dashboards/{id}                   dashboard + tiles, NO results
PATCH  /dashboards/{id}                   name, grid, palette, refresh rate
DELETE /dashboards/{id}
POST   /dashboards/{id}/tiles
PATCH  /dashboards/{id}/tiles/{tid}
DELETE /dashboards/{id}/tiles/{tid}
PATCH  /dashboards/{id}/layout            bulk positions, one call per drag-end
POST   /dashboards/{id}/tiles/preview     validate + run UNSAVED sql (the editor)
POST   /dashboards/{id}/tiles/{tid}/data  execute one tile
POST   /dashboards/{id}/data              execute all tiles
```

House rules that apply: **literal paths declared above `/{id}` paths**;
business logic in the service, the route is HTTP shape only; errors through
[`api/errors.py`](../backend/app/api/errors.py) as problem+json.

Per-tile payload: `{status, columns, rows, row_count, truncated, duration_ms,
computed_at, vega_spec, error}`. `computed_at` is not optional — the UI must be
able to say "as of 14:32" rather than implying live data.

**Cache** (`dashboard_tile_cache`): serve cached when
`now - computed_at < effective_refresh_interval` **and** `sql_hash` matches,
unless `?force=true`. Without this, five people with a 30-second dashboard open
is a load generator pointed at the customer's database.

---

## 6. Phase 4 — frontend

New `pages/DashboardsPage.tsx`, `components/dashboard.tsx`,
`components/tile-editor.tsx`. Edit [`App.tsx`](../frontend/src/App.tsx#L12)
(the `View` union, the nav item, the render branch),
[`api/client.ts`](../frontend/src/api/client.ts) (a `dashboards` export beside
`connections`), [`api/types.ts`](../frontend/src/api/types.ts).

- **Grid:** `react-grid-layout`, `cols`/`rowHeight` from the dashboard's own
  settings; `onDragStop`/`onResizeStop` → debounced `PATCH /layout`. View mode
  locks dragging, edit mode unlocks it. It is a layout engine, not a styled
  component library, so it does not breach the "no component library" rule — but
  it is the first frontend dependency added since the initial stack, so it needs
  the owner's yes before `npm install`.
- **Tile shell:** title, connection chip, `computed_at`, kebab (refresh / edit /
  duplicate / delete), three states — `Spinner`, `ErrorNote`, data. All from
  [`ui.tsx`](../frontend/src/components/ui.tsx).
- **Tile body:** `CHART` → `VegaChart`; `TABLE` → the extracted `ResultTable`;
  `METRIC` → new big-number tile (one row × one numeric column).
- **Refresh:** **one** `setInterval` per dashboard calling
  `POST /dashboards/{id}/data` and fanning results out — not one per tile. Pause
  on `document.hidden`, or a forgotten background tab polls forever.
- **Settings drawer:** grid columns, row height, gap, palette, theme override,
  refresh rate (`Off / 30s / 1m / 5m / 15m / 1h`).

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

## 7. Phase 5 — authoring ("write what you want")

**A. Promote from chat — build this first.** An "Add to dashboard" action on a
succeeded run in [`chat.tsx:614`](../frontend/src/components/chat.tsx#L614). The
run already has validated SQL, a connection, and a chart spec; copy them into a
tile (`sql_origin = FROM_RUN`). Nearly free, and it proves the whole loop
end-to-end against real data before any grid code exists.

**B. Plain-language authoring.** `POST /sql/drafts` with
`{connection_id, llm_config_id, question}` → `{sql, validation_report,
referenced_tables, preview}`. New `services/sql_draft_service.py` reusing
`retrieve` → `generate` → `validate` with a `RunState` built from `uuid4()`
placeholders and a no-op `emit` — no conversation, no message, no `runs` row.

> Known wart: `RunState` requires `run_id` and `conversation_id`. Synthetic
> UUIDs are the honest cheap answer for a draft that is not a run. Comment it
> where it happens.

Editor flow: connection → model → question → **editable** SQL textarea →
Preview (inline guard report, the same live-validation feel as the semantic
layer editor) → chart type + axes, defaulted from `plan_chart` → Save.

**The model's SQL is a draft and hand-edits are allowed; neither is trusted.**
The guard runs at preview, at save, and at every refresh.

---

## 8. Invariants — how CLAUDE.md's four apply here

1. **AST validation fails closed.** Unchanged and now load-bearing in a second
   place: `dashboard_tiles.sql` is user-controlled text. It passes `guard()`
   against the connection's live snapshot on *every* execution. There is no
   "trusted tile".
2. **Containment underneath correctness.** Tiles execute through the same
   connectors, same read-only transaction, same statement timeout and row cap.
   A tile override may only tighten.
3. **Credentials encrypted, never exposed.** No dashboard read model may carry a
   password or `api_key`. The tile carries a `connection_id` and a display name
   — never connection internals.
4. **Disclosure is explicit.** `DisclosurePolicy` gates what reaches *the
   model*; a tile result reaching the owner's own browser is the same exposure
   as the chat table and needs no new gate. **This stops being true the moment
   dashboards are shared** — see §10.

---

## 9. Tests (write alongside, not after)

- `tests/unit/test_query_service.py` — SQL referencing a since-dropped table
  fails closed with `E_SCHEMA_CHANGED`; a tile whose connection belongs to
  another user is rejected; **hostile SQL written directly into
  `dashboard_tiles.sql` is rejected at refresh** (this is the test that proves
  dashboards did not open a guard bypass — model it on
  `tests/unit/test_sqlguard_hostile.py`).
- `tests/unit/test_dashboards_api.py` — ownership scoping on every route; one
  failing tile still returns 200 with the others' data.
  (`tests/integration/` is empty today; every test lives under `tests/unit/`.)
- The new read models are covered automatically by
  [`test_openapi_has_no_secrets.py`](../backend/tests/unit/test_openapi_has_no_secrets.py),
  which walks the generated schema — **check it actually sees the new DTOs**
  rather than assuming it does.
- `test_dashboard_cache.py` — a changed `sql_hash` invalidates regardless of TTL.
- Frontend: `npm run typecheck && npm run build`.

Gate before claiming any phase done: `make test`, `make guard`, `make lint`.

---

## 10. Decisions already made (and what would reopen them)

| Decision | Why | Reopen when |
|---|---|---|
| **Owner-only, no sharing in v1** | A shared dashboard means user B reads data pulled with user A's stored credentials against a connection B does not own. That is an authorization model, not a UI feature. | There is a real answer for "who may read through this connection" — then add `dashboard_shares`. |
| **Per-tile connection** | The point of the feature; the guard policy is rebuilt per connection anyway. | never |
| **Layout per tile, not one JSONB blob** | One row per drag; no lost updates between tabs. | never |
| **Pre-validated palettes, no free hex** | §6. | Someone re-runs the validator for a new set, both modes. |
| **No dashboard filters in v1** | `QueryExecutor.execute` ([ports/database.py:133](../backend/app/domain/ports/database.py#L133)) takes no bind parameters. Filters need the port extended across all four connectors. **Never by string interpolation.** | Phase 6. |
| **Cache is in Postgres, not in-process** | The reconciler already assumes multiple workers may exist; an in-process cache would go stale per worker. | never |

---

## 11. Build order

```
[ ] 1  query_service.py + lift _policy_from_snapshot / _latest_snapshot   (§3)
[ ] 2  models + migration 0005                                            (§4)
[ ] 3  dashboard_service.py + /dashboards routes + cache                  (§5)
[ ] 4  "Add to dashboard" from a chat run                                 (§7A)
[ ] 5  DashboardsPage: list, grid, tile shell, refresh loop               (§6)
[ ] 6  Tile editor + POST /sql/drafts                                     (§7B)
[ ] 7  Later: filters · sharing · export · scheduled warm refresh
```

Steps 1–4 give a working, plain dashboard with no grid interactions. Land that
and use it before investing in step 5.
