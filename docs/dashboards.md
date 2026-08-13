# Dashboards

A Superset/Power-BI-shaped grid of tiles. Each tile is a saved query bound to
**its own connection** and **its own refresh rate**, drawn as a chart, a table,
a big number, or plain text.

Companion to [pipeline.md](pipeline.md) (the AI run), [charts.md](charts.md)
(what gets drawn), [security.md](security.md) (the guard and disclosure) and
[architecture.md](architecture.md) (the why).

---

## 1. The idea

A tile's SQL is written one of two ways, both first-class:

1. **Ask in plain language** — pick a connection and a model, type the
   question, get SQL back as an **editable draft**.
2. **Write the SQL yourself** — the same textarea. Nothing about path 1 is
   privileged, and a user with no LLM provider configured can still build a
   whole dashboard.

Either way the user picks the chart type or leaves it on *Auto*.

**Neither path is trusted.** The guard runs at preview, at save, and at every
single refresh.

## 2. The one thing that makes this hard

Everything else is CRUD and UI. This is the part that matters:

> Before dashboards, the only path from SQL to a driver ran through the LLM
> pipeline. A dashboard needs *"execute this stored SQL against this
> connection, no model involved"* — a second entry point into the guarded
> execution path.

A second entry point is a second chance to bypass the guard. It does not get
one:

- Stored SQL is **re-validated on every execution**, never trusted because it
  passed when it was saved. A re-sync that dropped a table makes the tile fail
  closed with `E_SCHEMA_CHANGED` — not run a stale query, and not return an
  empty result that looks like "no data".
- `dashboard_tiles.sql` is **hostile input by definition**, since the user can
  type into it directly. It is not model output that happens to be visible.
- `sql_origin` (`GENERATED | GENERATED_EDITED | HANDWRITTEN`) is **provenance
  only, never a trust signal.** The guard cannot tell them apart and must not
  try.

`tests/unit/test_query_service.py` replays the hostile SQL corpus through a
tile. That test is what proves dashboards did not open a bypass.

## 3. Executing a tile

`services/query_service.py` is the single entry point:

```python
async def execute_saved_sql(
    db, settings, *, sql, connection, owner_id,
    chart_intent=None, max_rows=None, connector=None, snapshot=None,
) -> TileResult
```

Six rules, each with a test:

1. **Re-validate every time.** Load the connection's *current* snapshot,
   rebuild the `GuardPolicy`, call `guard()`. There is no "already approved"
   flag.
2. **Re-check ownership at execution**, not only at save. Connections get
   deleted and rows get edited underneath you.
3. **Containment is the connection's, not the tile's.** `max_rows` and
   `statement_timeout_ms` come from the connection; a tile override may only
   *lower* them.
4. **Always close the connector** — `try/finally`.
5. **A tile failure is a value, not an exception.** One broken tile must never
   fail the dashboard response.
6. **The chart is decided on the backend.** The stored intent goes into
   `plan_chart` as a *suggestion*, so a user's explicit pick gets the same
   name-check and shape-repair a model's suggestion gets. An unfittable intent
   degrades to the table plus a note — never an error.

`TileResult` carries `status`, `columns`, `rows`, `row_count`, `truncated`,
`duration_ms`, `computed_at`, `vega_spec`, `chart_source`, `chart_note`,
`error_code`, `error_message`.

Error codes the UI branches on: `E_SCHEMA_CHANGED` (the guard refused a name
the snapshot no longer has), `E_NO_SNAPSHOT` (never synced), `E_FORBIDDEN` (not
the caller's connection — nothing is decrypted or dialled),
`E_CONNECTION_REMOVED`, `E_QUERY_FAILED`, `E_INTERNAL`, and otherwise the
guard's own `rule_id` verbatim.

**Batching.** `execute_many` groups tiles by `connection_id` and builds **one
connector per connection**, running under a semaphore capped at 4. Twelve tiles
on one database must not open twelve connections. One rule that is easy to
lose: **every database read happens before the tiles fan out** — one snapshot
per connection, awaited in sequence — because an `AsyncSession` is not safe for
concurrent use.

## 4. Data model

Three tables, migrations `0005` and `0006`.

### `dashboards`

`id` · `owner_id` (FK users, CASCADE) · `name`, `description` (unique per
owner) · `status` (`ACTIVE | ARCHIVED`) · `grid_columns` (12) ·
`row_height_px` (60) · `gap_px` (12) · `compact_mode` (`VERTICAL | NONE`) ·
`palette` (`"default"`) · `theme_override` (`INHERIT | DARK | LIGHT`) ·
`default_refresh_interval_seconds` (0 = manual).

### `dashboard_tiles`

| column | note |
|---|---|
| `connection_id` | FK **SET NULL**, not CASCADE — a deleted connection must leave a tile saying "connection removed", not silently delete the layout |
| `llm_config_id` | FK SET NULL. Which model drafted the SQL. Provenance only, **never consulted at refresh** |
| `tile_type` | `CHART \| TABLE \| METRIC \| TEXT` |
| `question` | the plain-language question, kept so "edit → re-ask" works and the user can see what they meant six weeks later |
| `sql` | empty for `TEXT` tiles |
| `sql_origin` | provenance only — see §2 |
| `chart_config` | serialised `ChartIntent`. **`NULL` means Auto**: re-planned on every result |
| `table_config` | how a `TABLE` tile is drawn. **`NULL` means "as the query returned it"** |
| `max_rows` | may only lower the connection's |
| `refresh_interval_seconds` | **the per-tile rate.** `NULL` = inherit; `0` = manual. This column *is* the feature — do not collapse it into a dashboard-level setting |
| `grid_x/y/w/h`, `position` | layout **per tile**, not one dashboard-level JSONB: a drag saves one row, and two open tabs cannot lose each other's edits |

A new tile is `4 × 4` at `0,0`. Status columns are plain `String`, like
`runs.status`, so a new member needs no DDL.

### `dashboard_tile_cache`

`tile_id` pk · `sql_hash` · `result` jsonb · `row_count` · `computed_at` ·
`duration_ms` · `error_code` · `error_message`.

The stored hash fingerprints `(connection_id, sql, max_rows, chart_config)` —
not the SQL alone, so a tile switched from a pie to a line does not keep
serving the pie until its interval elapses. **`table_config` is deliberately
excluded**: it redraws rows the browser already has, and renaming a column
header must not send a query to the customer's database.

**Failures are cached too**, which is what the error columns are for. Without
that, a tile whose query is broken re-runs it on every tick of every open
browser.

The cache is in Postgres, not in-process: the reconciler already assumes
multiple workers may exist, and an in-process cache would go stale per worker.

## 5. The API

```
GET    /dashboards                        owner-scoped list
POST   /dashboards
GET    /dashboards/{id}                   dashboard + tiles, NO results
PATCH  /dashboards/{id}
DELETE /dashboards/{id}
POST   /dashboards/{id}/tiles
PATCH  /dashboards/{id}/tiles/{tid}
DELETE /dashboards/{id}/tiles/{tid}
POST   /dashboards/{id}/tiles/{tid}/duplicate
PATCH  /dashboards/{id}/layout            bulk positions, one call per drag-end
POST   /dashboards/{id}/tiles/{tid}/data  (?force=true)
POST   /dashboards/{id}/data              {tile_ids?: [...]}  (?force=true)
GET    /dashboards/{id}/export            the portable document — see §11
POST   /dashboards/import                 {document, name?, connection_map, skip_invalid?}
```

Cache rule: serve cached when `now - computed_at < effective_refresh_interval`
**and** `sql_hash` matches, unless `?force=true`. When the interval resolves to
`0`, the cache is served on any hit until the user presses refresh. Without
this, five people with a 30-second tile open is a load generator pointed at the
customer's database.

`GET /dashboards/{id}` returns each tile with `effective_refresh_interval_seconds`
already resolved and its connection/model **names** — so the browser holds no
second copy of the inheritance rule and needs no extra round trip. Names only:
no host, no username, nothing else from inside a connection. Every
tile-returning route resolves them, so the editor can render the tile it just
saved.

`computed_at` is not optional. With every tile on its own clock, "as of 14:32"
is the only way a reader tells a 30-second tile from the hourly one beside it.

### Authoring endpoints

```
POST /sql/drafts            {connection_id, llm_config_id, question, tile_type?} -> draft
POST /sql/drafts/validate   {connection_id, sql, tile_type?} -> report + preview
```

`draft_sql` reuses the pipeline's `retrieve` → `generate` → `validate` nodes
with a `RunState` built from placeholder UUIDs and a no-op `emit` — no
conversation, no `runs` row, no SSE. One repair attempt, because a draft is
interactive and the user is watching.

Both routes answer with one DTO whose `preview` is a `TileResultRead` — *the
same shape a tile returns after a refresh*, because a preview that could differ
from a refresh is a preview that lies. `PREVIEW_MAX_ROWS = 50` reaches both the
rewriter's `LIMIT` and the driver.

A rejection is a **200 with `validation_status: "REJECTED"`**, not a 4xx: the
editor renders the guard's reasons inline, and a 4xx would make "the model
wrote something I can show you" indistinguishable from "your request was
malformed".

A draft is **not a loophole around disclosure** — `retrieve` renders the schema
through the connection's `DisclosurePolicy` and semantic layer, unchanged.

## 6. The UI

[`DashboardsPage.tsx`](../frontend/src/pages/DashboardsPage.tsx) (index and one
dashboard), [`dashboard.tsx`](../frontend/src/components/dashboard.tsx) (grid,
tile shell, scheduler), [`tile-editor.tsx`](../frontend/src/components/tile-editor.tsx).

**Index → open.** A searchable, sortable index of cards or rows — filterable by
status, since archiving is otherwise a verb with nowhere for the result to go.
Then one dashboard filling the page.

**View mode vs edit mode.** The header's *Edit grid* toggle governs the
**layout** only — drag, resize, add, the inline name. A tile's own
edit/duplicate/delete live on its kebab in **both** modes, reachable without a
mode switch. Edit mode tints the canvas and draws an alignment guide at the
dashboard's own cell geometry, so a mode looks like a mode.

**Grid** is `react-grid-layout` — a layout engine, not a component library, so
the "no component library" rule holds. Its two cosmetic defaults are restated
in `styles.css`. All eight resize handles are enabled; under
`compact_mode = NONE` collision *pushing* is disabled too, or one nudge
cascades through the board.

**Tile shell:** title, connection chip, refresh-rate chip, `computed_at`, an
expand button, and a kebab. The connection chip is always visible — which
database a number came from changes what it means. The **model that wrote the
SQL is deliberately not shown**: it describes how the tile was authored, not
the figure on screen.

**Tile bodies:** `CHART` → `VegaChart`; `TABLE` → `ResultTable`; `METRIC` →
`Kpi`; `TEXT` → plain text, no SQL, no connection.

Beyond the grid: full-screen focus on one tile, a presentation mode that covers
the app shell, per-type loading skeletons, and `Ctrl+Z` / `Ctrl+Y` over tile
placement.

### One scheduler, not one timer per tile

Each tile has its own rate, and the naive reading is a `setInterval` per tile.
Twelve timers produce twelve interleaved requests, each opening its own
connector.

**One `setInterval(1000)` per open dashboard.** Each tick computes which tiles
are *due* and fires **one** `POST /data {tile_ids}`. Tiles due in the same
second coalesce for free; a 30s tile and an hourly one never wait on each
other.

The tick **pauses on `document.hidden`** and on return refreshes whatever went
overdue **once** — not once per missed interval. A forgotten background tab
that polls forever is how this feature becomes the reason someone's production
database is slow.

The due rule is a pure function in
[`dashboard-schedule.ts`](../frontend/src/components/dashboard-schedule.ts) —
no React, no DOM — because its two failure modes are "a background tab hammers
the customer's database" and "a 30-second tile silently shows ten-minute-old
numbers". `npm run test:schedule` runs it against a frozen clock.

A tile that is *re*-loading keeps its numbers on screen with "refreshing" in
the header: blanking a good result for half a second reads as a fault. A failed
poll leaves the last results in place — a stale number carrying its own
timestamp beats an empty grid.

Rates: `Manual / 15s / 30s / 1m / 5m / 15m / 1h / 6h / 24h`, plus "inherit".

### The tile editor

One modal, two tabs over **one shared SQL textarea** — the tab chooses how the
text got there, not what happens to it afterwards. The debounced guard check
watches the textarea whatever put text in it, so editing what the model wrote
is checked exactly like typing it yourself.

**Connection sits above the tabs**, not inside the Ask tab: the guard resolves
every name against that connection's snapshot, so tab 2 needs it just as much.
Changing it clears the report, which described a different database.

**Provenance follows whoever last wrote the text**, not the tab in front.
`GENERATED` → `GENERATED_EDITED` on the first keystroke; switching tabs changes
nothing, because switching to tab 2 to fix one join does not erase the fact
that a model wrote the other twenty lines.

**Axis pickers are populated from the preview's columns**, never from the SQL
text — a name the result does not have loses the chart.

**The type picker is an input, not just an output.** `tile_type` rides on both
draft requests: `METRIC` appends SQL rules asking for a time series rather than
a lone figure, and asks the preview for a KPI so the editor shows the big number
it will actually draw — delta and sparkline included — instead of the rows
underneath it. Switching *to* METRIC re-checks once for that reason, since the
previous check did not ask for a KPI. It is a hint about the destination and
never a promise: nothing is saved by a draft, and the tile save path validates
the real type on its own.

**Whether the suggestion also moves the *type* depends on who chose it.**
`chart_source` comes back on the draft saying which: a `heuristic` pick defaults
the axes only and leaves the type on *Auto*; a `model` / `model_adjusted` pick —
one made by reading the question, on the plain-language road — pre-selects the
type as well, once per draft and only from Auto.

That default used to be Auto for everything, on the reasoning that a tile whose
data changes shape should be free to re-plan. The reasoning was sound and the
protection was redundant: `plan_chart` already re-fits a stored intent on every
refresh and demotes it with a note rather than failing (a pie past six slices
becomes a bar, an intent naming a dropped column degrades to the table). So Auto
was guarding against a risk the planner absorbs, at the cost of every tile
written from a sentence being drawn as a bar chart — because almost any two
columns *can* be a bar, and a shape heuristic has no way to know the question
asked about a share. The user can still set it back to Auto, and the editor will
not overrule them: adoption is keyed on the draft object, so it happens once per
round trip and never twice.

> **"Table only" is a tile type, not a chart intent.** Storing
> `chart_type: "none"` reads like "draw nothing" and does the opposite —
> `validate_intent` refuses it, so `plan_chart` falls through to the heuristic
> and draws whatever the shape suggests. The picker's *Table only* sets
> `tile_type = TABLE`.

**Table configuration** — for a `TABLE` tile, each column can be hidden,
reordered, relabelled, aligned and number-formatted, with a default sort. Three
properties keep it safe:

- **It never re-runs a query** (see §4's fingerprint note).
- **`NULL` still means "as the query returned it"**, so a picker merely looked
  at stores nothing.
- **A column the query adds later appears**, at the end, visible — the opposite
  default from "hide anything not on the list", and the one that cannot lose
  data. A configured column the result loses is greyed rather than deleted.

Rules live in [`table-format.ts`](../frontend/src/components/table-format.ts),
DOM-free for the same reason as the scheduler: every way they can be wrong is
quiet. `npm run test:format`.

> **Hiding a column hides it; it does not withhold it.** The value is in the
> payload that reached the browser. Anything that must not be sent belongs to
> the connection's disclosure policy or to the SQL.

## 7. How the four invariants apply

1. **AST validation fails closed** — now load-bearing in a second place.
   `dashboard_tiles.sql` is user-typed text and passes `guard()` against the
   connection's live snapshot on *every* execution. There is no trusted tile.
2. **Containment underneath correctness** — tiles execute through the same
   connectors, read-only transaction, statement timeout and row cap. A tile
   override may only tighten.
3. **Credentials encrypted, never exposed** — a tile carries ids and display
   names, never connection internals or provider keys.
4. **Disclosure is explicit** — `DisclosurePolicy` gates what reaches *the
   model*, including in `POST /sql/drafts`. That route's second call, which asks
   what the tile should be drawn as, is narrower still: it passes no policy at
   all, so it sends the result's *shape* — counts, ratios, a grain — under every
   policy including `FULL`, and never a row value. A tile *result* reaching the
   owner's own browser is the same exposure as the chat table and needs no new
   gate. **This stops being true the moment dashboards are shared** — see §9.

## 8. Tests

```bash
make test     # test_query_service, test_dashboards_api, test_dashboard_cache,
              # test_dashboard_service, test_dashboard_transfer, test_sql_drafts,
              # test_drafts_api, test_tile_charts, test_dashboard_models
make guard    # the hostile corpus
npm run test:schedule && npm run test:format && npm run test:document
```

Worth knowing what a few of them pin:

- **`test_query_service.py`** — hostile SQL written directly into
  `dashboard_tiles.sql` is rejected at refresh. This is the guard-bypass test.
- **`test_dashboard_models.py`** — replays migration `0005` against a recorder
  and diffs it against the ORM column by column, so the two cannot drift.
- **`test_tile_charts.py`** — every `chart_config` the editor can build, pinned
  against `ChartIntent`. See [charts.md](charts.md) §9 for why.
- **`test_dashboard_cache.py`** — a changed `sql_hash` invalidates regardless
  of TTL; two tiles with different rates expire independently.
- **`test_dashboard_transfer.py`** — the hostile corpus through an imported
  *file*, and the two claims that make a file safe to hand someone: it carries
  no connection internals, and a refused import creates nothing at all.
- **`test_openapi_has_no_secrets.py`** — walks every schema the app can return.

## 9. Decisions, and what would reopen them

| Decision | Why | Reopen when |
|---|---|---|
| Hand-written SQL is first-class | Analysts do not want to argue with a model about a join they can type. Also makes the feature usable with no LLM provider. | never |
| Refresh rate per tile | A KPI over today's orders and a year-over-year rollup do not belong on the same clock. | never |
| One scheduler tick | Twelve timers = twelve interleaved requests = twelve connectors. | never |
| Chart type user-chosen, `NULL` = Auto | The user knows what they want to show; the platform still repairs an impossible pick. | never |
| Per-tile connection | The point of the feature; the guard policy is rebuilt per connection anyway. | never |
| Layout per tile, not one JSONB | One row per drag; no lost updates between tabs. | never |
| Cache in Postgres | An in-process cache goes stale per worker. | never |
| Pre-validated palettes, no free hex | [charts.md](charts.md) §8. | Someone re-runs the validator, both themes. |
| **Owner-only, no sharing** | A shared dashboard means user B reads data pulled with user A's credentials against a connection B does not own. That is an authorization model, not a UI feature. | There is a real answer for "who may read through this connection" — then add `dashboard_shares`. |
| **No dashboard filters** | `QueryExecutor.execute` takes no bind parameters. Filters need the port extended across all four connectors. **Never by string interpolation.** | Someone extends the port. |

## 10. Not built

Filters, sharing, "add to dashboard" from a chat run, and scheduled
server-side warm refresh. (Export and import are built — §11.)

"Add to dashboard" is the cheapest of these — a succeeded run already has
validated SQL, a connection and a chart spec to copy into a tile. It was left
out so the dashboard would stand on its own: a user who never opens chat still
builds one, and promotion is a shortcut on top of a feature that has to exist
first.

## 11. Moving a dashboard: export and import

A dashboard is a layout, a set of statements and a rate for each of them. Two of
the three things a tile points at — a connection and a model — are rows in *this*
installation's database, so a file carries neither.

`services/dashboard_transfer.py` is the format;
`dashboard_service.export` / `import_document` are the two ends;
`components/dashboard-document.ts` is the browser's half of the reading, DOM-free
and tested like the scheduler.

### What is in the file

```json
{ "format": "datamind.dashboard", "version": 1, "exported_at": "…",
  "dashboard": { "name": "Ops", "grid_columns": 12, … },
  "connections": [ { "ref": "c1", "name": "sales", "database_type": "postgres" } ],
  "tiles": [ { "connection_ref": "c1", "sql": "…", "grid_x": 0, … } ] }
```

Three absences define it:

- **No ids.** A `connection_id` means nothing in another account, and a file
  that named one would either be useless or resolve to a row its reader was
  never meant to reach. Each database becomes a `ref` with a display name, and
  the importer says which of *their* connections that is.
- **No results.** An export is the SQL, never the rows it returned. Exporting
  the cache would turn "share this dashboard" into "send this person an extract
  of the customer's database" — a disclosure decision no file format gets to
  make (invariant #4).
- **Nothing from inside a connection.** The name and the engine are what the
  importer needs — the engine because SQL written for one dialect usually will
  not parse on another, and saying so in the dialog costs one line where finding
  out costs twelve rejections. The host, the database, the user and the password
  stay where they are (invariant #3).

`llm_config_id` does not survive either: which model drafted the SQL is
provenance about a row somewhere else, and it is never consulted at refresh.

### Importing is a fourth entry point to the guard

`sql` in a `.json` file is typed as easily as `sql` in the editor's textarea. So
every tile in a document goes through `_validated_tile_fields` — the *same* call
the save path makes — against the importing user's own snapshot.
`test_dashboard_transfer.py` replays a sample of the hostile corpus through a
file, which is what proves import opened nothing.

Two rules follow from that, and they are the reason import is not a loop around
`add_tile`:

1. **Every tile is validated before anything is created.** A file with one bad
   statement leaves no half-built dashboard behind, and the refusal names *all*
   the tiles it refused (in the problem body's `tiles`), because the user is
   holding one file and deciding about it once.
2. **`skip_invalid` is the user's answer to that report, never a default.**
   Importing a board against a database whose schema has moved on genuinely
   loses tiles; dropping them silently would be a dashboard that looks complete
   and is not. The response lists what it dropped, with the guard's own reason.

Connections resolve from the caller's own rows: the explicit `connection_map`
first — every id re-checked for ownership, since it arrives in a request body —
then an exact **name** match for anything left, which is what makes re-importing
your own export one click. Nothing softer than an exact name is attempted: a
match on engine alone would point a revenue tile at whichever Postgres came
first, and a wrong number under the right title is worse than a picker the user
has to fill in.

A name already taken gets a number. Every other write path refuses a duplicate;
here it would be a wall in front of the common case, raised after every statement
in the file had already passed the guard.

### The UI

Export sits in two places, one per state the dashboard can be in: on the index
card's kebab (next to Duplicate — both answer "I want another one of these"),
and in the open dashboard's **header**, in the group with Present / Edit grid /
Settings. It is the one action in a group of modes, and it earns the place: it
started inside the settings drawer, on the reasoning that "what this is, as a
file" is a property of the dashboard — and that is exactly where nobody looked
for it. Wanting the file is something you feel *while looking at the board*.

It is *fetched*, not linked: `<a download>` carries no bearer token, so the
browser saves the body the API returns.

Import is a dialog, because of the one question the file cannot answer: which
database is this? The browser parses the file, shows what is in it, asks the
mapping question, and warns before sending about the two things that will
otherwise come back as a dozen rejections — a changed engine, and tiles whose
connection was already gone when the file was written.

---

## Known issue: a read-after-write race

`get_db` commits in FastAPI's dependency teardown, which is **not** ordered
before the response reaches the client. A `GET` issued the moment a write
returns can be served from before that write's commit. Reproduced on
`DELETE /dashboards/{id}` → `GET` returning 200, then 404 half a second later.

The page works around it by splicing the **returned** row into its state
instead of re-reading — which every tile-returning route makes possible by
resolving display names (§5). A re-read that lost the tile the user just saved
would look exactly like a save that failed.

**The race is pre-existing and app-wide**, not a dashboards bug. Fixing it
properly means changing the session dependency for every route.
