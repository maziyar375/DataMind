# DataMind — UI/UX & Structure Remediation Plan

Derived from the information-architecture audit of the frontend. Sixteen
findings (**F1–F16**), sequenced into seven phases.

**Scope:** the four Low-priority findings from the audit (F17 audit-log UI,
F18 rail composition, F19 selector primitives, F20 `View` key naming) are
**deliberately excluded**. Where a later phase brushes against one, it is noted
inline so the decision stays visible rather than forgotten.

**Sequencing rule:** dependency first, importance second. A Critical item that
would have to be built twice is scheduled after the thing it should be built
on. That is why routing (F1) precedes the Chat→Dashboard bridge (F2) even
though both are Critical, and why responsive work (F7) is last.

---

## Dependency map

```
F15 ─┐                                    (independent cleanup)
F12 ─┴─► Phase 0

F1 (routing) ──┬──► F14  (nav guard needs routes)
               ├──► F2   (prefill travels as route state)
               ├──► F10  (editor becomes a routed surface)
               ├──► F3   (empty pickers navigate somewhere)
               └──► F5   (top-level destination w/ connection filter)

F9  ── same file as F1, same question: who owns shell state

F4 ─┬─ same component (ResultTable) — do in one visit
F11 ┘

F13 (notifications) ──► F5 (queue badges need a surface)

F7  ── last: it re-flows layouts that phases 2–5 are still changing
```

---

## Phase 0 — Groundwork

Zero feature risk, touches shared scaffolding, makes every later phase safer.
Do this first so the phases that follow inherit correct structure.

### F15 · Consolidate index page headers — *size: S*

**Problem.** `PageHeader` exists in `frontend/src/components/ui.tsx` and only
`UsersPage` uses it. `DashboardsPage` and `ReportsPage` hand-roll byte-identical
markup. `ReportsPage` also renders inside a class named `rm-dash-index`.

**Steps**
1. Move `DashboardsPage`'s header (the `<h1>` + subtitle + actions block) onto
   `PageHeader`, passing `IndexSummary` as the `subtitle` node.
2. Do the same for `ReportsPage`.
3. Rename `.rm-dash-index` in `styles.css` to a section-neutral name
   (e.g. `.rm-index`) and update all three call sites.

**Done when** three index pages render from one component, and `PageHeader` has
no remaining duplicate in the tree.

### F12 · Accessibility scaffolding — *size: M*

**Problem.** No `<main>` landmark, no skip link. Chat, Data sources and LLM
providers have no `<h1>`. The shared `Modal` handles Escape, backdrop click and
scroll lock, but does not trap focus, move focus on open, or associate its
title via `aria-labelledby`.

**Steps**
1. In `App.tsx`, wrap the view area (`.rm-app-view`) in `<main id="main">` and
   add a visually-hidden skip link before the sidebar.
2. Give ChatPage, DataSourcesPage and LlmProvidersPage one `<h1>` each — for
   the two master–detail pages the natural place is `MasterColumn`'s `title`
   prop in `components/settings.tsx`, which already receives the page name.
3. In `Modal` (`ui.tsx`): focus the dialog on mount, restore focus to the
   opener on unmount, contain Tab within the dialog, and wire
   `aria-labelledby` to the title node.

**Done when** every page has exactly one `h1`, Tab cannot escape an open modal,
and the skip link is reachable as the first Tab stop.

> Do this before Phase 2 — F10 moves one editor out of a modal, but a dozen
> other modals keep the fix.

---

## Phase 1 — Foundation: routing and shell state ownership

The phase everything else leans on. Nothing user-facing changes shape; what
changes is that the app becomes addressable.

### F1 · Adopt the router — *size: L* — **Critical**

**Problem.** Navigation is the `View` union plus one `useState` in `App.tsx`.
`react-router-dom` v6 is a declared dependency and is imported nowhere. Open
dashboards, open reports, the active conversation and the run being read are
all component state, so nothing survives a refresh or can be linked.

**Steps**
1. Mount a `BrowserRouter` in `main.tsx`. Keep `App.tsx`'s shell, sidebar and
   auth gate exactly as they are — only the mechanism changes, not the flat
   rail (its design rationale still holds).
2. Replace the `View` union with routes:
   - `/chat` · `/chat/:conversationId`
   - `/dashboards` · `/dashboards/:id`
   - `/reports` · `/reports/:id` · `/reports/:id/runs/:runId` · `/reports/:id/history`
   - `/sources` · `/sources/:id/:tab` (`settings|schema|semantic|knowledge`)
   - `/providers` · `/providers/:id`
   - `/users` (admin-gated route, not just a hidden rail item)
3. Convert the local index/detail switches to nested routes:
   `DashboardsPage`'s `openId`, `ReportsPage`'s `openId`/`runId`/`history`,
   `DataSourcesPage`'s `selectedId`/`tab`, `ChatPage`'s `activeId`.
4. Keep unknown paths redirecting to `/chat`, preserving today's landing
   behaviour.

> The `View` key `'settings'` currently renders the LLM providers page (F20,
> out of scope). Use `/providers` for the new path anyway — it costs nothing
> here and leaves `/settings` free for F6's account screen.

**Done when** every screen in the product has a URL that survives a hard
refresh, browser Back moves one screen rather than leaving the app, and a
dashboard link opens that dashboard for a signed-in colleague.

### F9 · One owner for global theme state — *size: S*

**Problem.** Two components write the theme. `App` owns the toggle; an open
dashboard carrying a `theme_override` calls `applyTheme` directly
(`pages/DashboardsPage.tsx`, the `override` effect) and on unmount restores the
value it captured at mount — which is stale if the user toggled the rail in the
meantime.

**Steps**
1. Lift the override into shell state: expose something like
   `requestThemeOverride(name | null)` from `App` via context.
2. Have `DashboardView` call it on mount and clear it on unmount; `App` remains
   the only caller of `applyTheme`.
3. Resolve effective theme as `override ?? userChoice`, so a rail toggle during
   an override still wins once the override clears.

**Done when** `applyTheme` has exactly one call site outside `theme/tokens.ts`,
and toggling the theme while an overriding dashboard is open leaves the correct
theme in place after closing it.

### F14 · Unsaved-change navigation guard — *size: S*

**Problem.** `DataSourcesPage` computes `hasChanges` and renders an
`UnsavedNote` beside Save, then discards the edits without a prompt when the
user clicks another rail destination or another connection in the master
column. The app knows the work is unsaved and does not act on it.

**Steps**
1. With routes in place, add one blocker (`useBlocker` / a route-level guard)
   rather than a check per page.
2. Have dirty-form pages register their dirty state with the shell.
3. Show a confirm dialog using the shared `Modal`: stay, or discard and go.

**Done when** navigating away from a dirty connection form — by rail, by master
column row, or by browser Back — asks first.

---

## Phase 2 — The core loop: connect the three pillars

The Critical product finding. The backend already treats Chat, Dashboards and
Reports as one guarded query path; this phase makes the frontend agree.

### F10 · Tile editor out of the modal — *size: M*

Scheduled **before** F2 so the bridge lands in its final container and the
prefill contract is written once.

**Problem.** `components/tile-editor.tsx` is an 880px `Modal` holding a full
authoring workspace: connection picker, ask-or-write tabs, SQL editor, guard
verdict, result preview, tile type grid, chart picker, column config and
refresh rate. It hides the grid the tile is joining, cannot be linked, and is
cramped on laptop screens.

**Steps**
1. Convert the container to a right-side drawer over the live dashboard,
   reusing the existing `.rm-drawer` treatment that `DashboardSettings` uses.
2. Give it a route: `/dashboards/:id/tiles/new` and `/dashboards/:id/tiles/:tileId`.
3. Accept a prefill payload — `{ question, sql, connectionId, chartConfig }` —
   from route state. This is the contract F2 will use.
4. Leave every guard/validation path untouched; only the container and entry
   point move.

**Done when** the tile editor opens beside the grid rather than over it, has its
own URL, and accepts a prefill.

### F2 · Chat → Dashboards / Reports bridge — *size: M* — **Critical**

**Problem.** A finished answer offers Copy, Copy SQL, Regenerate, feedback and
Save as template (`components/chat.tsx`, the answer action row). There is no
route from an answer to a tile or a report block. Building a tile means
reopening the same question in a different box and asking again — even though
the answer on screen already carries validated SQL and a fitted chart.

**Steps**
1. Add **Add to dashboard** and **Add to report** to the answer action row,
   next to the existing `Save as template` — same `QuietAction` treatment, same
   `canTeach`-style guard (`run.queries.length > 0`).
2. **Add to dashboard:** pick a target dashboard (or create one), then navigate
   to F10's tile route with `{ question, sql, connectionId, chartConfig }` as
   prefill. Do not re-ask the model and do not re-run the query — carry the
   run's own statement, exactly as the chart picker already reuses its rows.
3. **Add to report:** pick an existing report bound to the same connection, or
   create one; append a block carrying the question and the hand-written
   statement, marked as user-authored so `Check all` will not overwrite it.
4. Disable both when the thread's connection is gone, with the reason on the
   page rather than in a `title`.

**Done when** an answer a user is looking at can become a tile or a report block
without retyping the question, and the SQL that lands is the SQL that ran.

---

## Phase 3 — Getting started, and getting data out

Two ends of the same journey: a new user can reach a first answer, and any user
can take a result away with them.

### F3 · First-run onboarding — *size: M*

**Problem.** A fresh install opens on Chat. Both header pickers read
"None configured", the composer says "Choose a database and model above to
start", and neither is a link. The real four-step setup order — provider →
connection → schema sync → semantic layer — exists only in the README, and step
three is not optional: an unsynced connection can answer nothing at all.

**Steps**
1. In `HeaderSelect`, when `options.length === 0`, replace the inert
   "None configured" row with an action that navigates to `/providers` or
   `/sources` (routes now exist from F1).
2. Add a dismissible setup checklist on the Chat welcome screen, showing live
   state for the four steps: model provider added · connection added · schema
   synced · semantic layer generated (marked optional).
3. Derive each step's state from data already fetched at boot — connections
   carry `last_synced_at`, providers carry `status`. No new endpoint.
4. Keep `STARTERS` for the ready state; the checklist replaces them only while
   the product is unusable.

**Done when** a brand-new install offers a path to its own prerequisites from
the screen it lands on, and the checklist disappears once the four steps are
satisfied.

### F4 · Result export — *size: S*

**Problem.** No CSV or Excel export exists anywhere. The only download in the
app is a dashboard's JSON document (layout and SQL, no results). Chat tables,
tile tables and report exhibits can only be read on screen.

**Steps**
1. Add an opt-in download control to `ResultTable` in `ui.tsx` — a quiet action
   in the same row as the existing expand affordance.
2. Serialise from the spec already in the client: `spec.columns` +
   `spec.rows`, respecting the resolved column config (order, headings,
   visibility) so the file matches what is on screen.
3. Escape properly (quotes, embedded commas, newlines, `null`), and name the
   file from the tile/report/question title.
4. Enable it at all three call sites — chat answers, dashboard tiles, report
   exhibits.

**Done when** every result table in the product can be downloaded, and the file
matches the visible columns.

### F11 · Interactive result tables — *size: M*

**Problem.** Sorting is config-driven only — headers are not clickable. There is
no pagination and no virtualisation; "show all" mounts every returned row (up
to the connection's `max_rows`, default 1000) into the DOM.

**Steps**
1. Make `<th>` clickable: sort ascending/descending/none, layered *over* the
   stored `config` sort rather than replacing it, so a tile's saved ordering is
   still the default.
2. Show sort state in the header (arrow + `aria-sort`).
3. Add windowed rendering past a threshold (~200 rows) so the expanded view
   stays responsive at the row cap.
4. Keep the sort client-side — it must not re-run the query, for the same
   reason the chart picker redraws from rows already returned.

**Done when** a user can reorder a result by any column without asking a new
question, and expanding a 1000-row result does not stall the page.

---

## Phase 4 — Configuration clarity

Three findings about surfaces that currently misrepresent who owns what and who
may change it.

### F8 · Split connection identity from policy — *size: M*

**Problem.** In `DataSourcesPage`, one form and one Save button hold two
unrelated things: connection identity (host, port, database, user, password,
SSL, schema allowlist) and governance (disclosure policy, clarify, send DB
comments to the model, taught examples, scheduled conflict checks, row cap,
statement timeout). They are edited by different people on different occasions.

**Steps**
1. Split the `Settings` tab into two tabs — **Connection** and **Policy** (or
   "What the model may see") — inside the existing `Tabs` strip, keeping the
   Schema / Semantic layer / Knowledge tabs as they are.
2. Give each its own dirty tracking and Save. `isDirty` (probe-affecting) stays
   with Connection; the broader `hasChanges` splits per tab.
3. `Test connection` belongs to the Connection tab only — it already probes
   only connectivity fields.
4. Keep the Danger zone at the foot of Connection.

**Done when** changing a disclosure policy does not require touching a form
containing a password field, and each tab saves independently.

### F16 · Say that resources are per-user — *size: S*

**Problem.** Connections, model providers, dashboards and reports are all
scoped to `owner_id` in the API. The rail presents Data sources and LLM
providers as workspace configuration, and nothing on screen says they are
yours alone. Two people see identical labels and different lists.

**Steps**
1. Add a quiet ownership line to the `MasterColumn` headers on Data sources and
   LLM providers ("Yours — not shared with your team").
2. Rewrite the empty states so a second user's first visit explains they need
   their own connection, rather than implying one is missing.
3. Same treatment on the Dashboards and Reports index subtitles.

**Done when** the per-user model is stated where it is read, and an empty list
never looks like a bug.

> Sharing itself remains deliberately deferred — this finding is about naming
> the model, not changing it.

### F6 · Self-service account — *size: M*

**Problem.** Every route in `backend/app/api/v1/users.py` is `AdminDep`. There is
no profile or account screen — the sidebar shows a name, a role and a sign-out
icon. A member invited with a one-time password has no way to change it or edit
their own display name, so every invited user stays on a password an
administrator generated and can read.

**Steps**
1. Backend: add self-scoped routes alongside the admin ones —
   `PATCH /auth/me` (display name) and `PUT /auth/me/password`, the latter
   requiring the current password and rotating refresh tokens the way the admin
   set-password path already does.
2. Frontend: add an account surface at `/settings` (free now that F1 put
   providers on `/providers`), opened from the sidebar user block.
3. Keep admin set-password as the separate recovery path it already is.

**Done when** a member can change their own password without asking an
administrator, and the admin path is unchanged.

---

## Phase 5 — Background work made visible

`F13` first: `F5`'s queue badges need somewhere to live.

### F13 · Notification layer — *size: M*

**Problem.** Every page keeps its own `error` string and renders an inline
`ErrorNote`; success is usually silent. Several operations are minutes long and
deliberately queued (semantic-layer generation, benchmark runs, scheduled
conflict checks), and their completion is only visible if you are still on the
tab that started them.

**Steps**
1. Add one lightweight notification surface at the shell, with an `aria-live`
   region.
2. Reserve it strictly for cross-page and background events. **Leave in-page
   errors exactly where they are** — inline `ErrorNote` next to the thing that
   failed is the better pattern and should not be replaced.
3. Wire the existing polled jobs to it: semantic generation finishing, a
   benchmark run completing, a conflict check finding a disagreement.

**Done when** a semantic-layer generation started on one screen announces itself
when it finishes on another, and no existing inline error has moved.

### F5 · Promote the learning loop — *size: L*

**Problem.** A full curation console — taught templates, benchmark scoring,
embedding match mode, a "Needs you" review queue, a suggestion backlog and
maintenance sweeps — is the fourth tab of one connection's detail pane, three
clicks deep and per-connection. Its work queue is invisible unless someone
opens a specific connection and clicks that tab.

**Steps**
1. Surface the actionable half first (cheapest, highest value): a count badge on
   the Data sources rail entry and on the Knowledge tab, fed by
   `reviews` + `suggestions` across all connections.
2. Promote the console to a top-level route — `/knowledge`, with a connection
   filter — reusing `KnowledgeTab`'s existing sections rather than rewriting
   them.
3. Keep the per-connection tab as a filtered view of the same screen, so a
   curator working on one database is not forced up a level.
4. Decide explicitly whether it earns a rail entry or lives under Data sources;
   badge-first (step 1) makes that judgement with real usage.

**Done when** a curator can see that work is waiting without opening a
connection, and the console is reachable in one click from wherever they are.

---

## Phase 6 — Responsive layout

Deliberately last: this reflows layouts that phases 2–5 are still changing.
Doing it earlier means doing it twice.

### F7 · Small-screen story — *size: M*

**Problem.** Below 860px the rail collapses to a 66px icon strip, but every
page's second column stays — 208px of chat list (`.rm-chats`), 212px of master
column (`.rm-master`). On a 375px phone that leaves roughly 100px for the
transcript. There is no off-canvas pattern for the list columns.

**Steps**
1. Below ~700px, turn `.rm-chats` and `.rm-master` into overlay drawers, reusing
   the `.rm-drawer` treatment already written for `DashboardSettings` at 900px.
2. Add a toggle in each page header to open its list drawer; close on select.
3. Verify the surfaces changed in earlier phases at phone width: the tile editor
   drawer (F10), the account screen (F6), the split settings tabs (F8), the
   notification surface (F13).
4. Check the dashboard grid's existing `STACK_BELOW_PX` stacking still reads
   correctly once the rail is the only chrome.

**Done when** a dashboard and a finished report are readable on a phone, and no
page has two fixed columns below 700px.

---

# Checklist

Tick as you go. Phases are ordered; items inside a phase can be done in any
order unless noted.

## Phase 0 — Groundwork
- [x] **F15** `PageHeader` adopted by DashboardsPage
- [x] **F15** `PageHeader` adopted by ReportsPage
- [x] **F15** `.rm-dash-index` renamed section-neutral, all call sites updated
- [x] **F12** `<main>` landmark + skip link in `App.tsx`
- [x] **F12** One `<h1>` on Chat, Data sources, LLM providers
- [x] **F12** `Modal`: initial focus, focus trap, focus restore, `aria-labelledby`

## Phase 1 — Foundation
- [x] **F1** `BrowserRouter` mounted; `View` union removed
- [x] **F1** Routes for chat, dashboards, reports, sources, providers, users
- [x] **F1** Index/detail state converted to nested routes (all 4 pages)
- [x] **F1** Unknown paths redirect to `/chat`; refresh preserves the screen
- [x] **F9** Theme override lifted into shell state
- [x] **F9** `applyTheme` has one call site outside `theme/tokens.ts`
- [x] **F14** Route-level guard blocks navigation away from a dirty form
- [x] **F14** Confirm dialog: stay / discard, wired to the connection form

## Phase 2 — The core loop
- [x] **F10** Tile editor converted from `Modal` to drawer over the grid
- [x] **F10** Tile editor routed (`/dashboards/:id/tiles/new` + `/:tileId`)
- [x] **F10** Prefill payload accepted from route state
- [x] **F2** "Add to dashboard" on the answer action row
- [x] **F2** "Add to report" on the answer action row
- [x] **F2** Prefill carries the run's own SQL — no re-ask, no re-run
- [x] **F2** Both disabled with an on-page reason when the connection is gone

## Phase 3 — Getting started & getting data out
- [ ] **F3** Empty `HeaderSelect` menus navigate to `/providers` / `/sources`
- [ ] **F3** Setup checklist on the Chat welcome screen
- [ ] **F3** Checklist state derived from existing boot data (no new endpoint)
- [ ] **F3** Checklist hides once the four steps are satisfied
- [ ] **F4** CSV download control on `ResultTable`
- [ ] **F4** Export respects resolved column config; escaping verified
- [ ] **F4** Live at all three call sites (chat, tiles, reports)
- [ ] **F11** Click-to-sort headers with `aria-sort`
- [ ] **F11** Stored tile sort still the default; user sort layers over it
- [ ] **F11** Windowed rendering past ~200 rows
- [ ] **F11** Sorting never re-runs the query

## Phase 4 — Configuration clarity
- [ ] **F8** Settings tab split into Connection / Policy
- [ ] **F8** Independent dirty tracking + Save per tab
- [ ] **F8** `Test connection` scoped to the Connection tab
- [ ] **F16** Ownership line on Data sources + LLM providers master columns
- [ ] **F16** Empty states rewritten for a second user's first visit
- [ ] **F16** Dashboards + Reports index subtitles updated
- [ ] **F6** Backend: `PATCH /auth/me`
- [ ] **F6** Backend: `PUT /auth/me/password` (verifies current, rotates refresh)
- [ ] **F6** Frontend: account screen at `/settings`, opened from sidebar user block
- [ ] **F6** Admin set-password path unchanged

## Phase 5 — Background work made visible
- [ ] **F13** Notification surface at the shell with `aria-live`
- [ ] **F13** Scoped to background/cross-page events only
- [ ] **F13** Inline `ErrorNote` usage left untouched
- [ ] **F13** Wired: semantic generation, benchmark runs, conflict checks
- [ ] **F5** Cross-connection review/suggestion count badge on the rail
- [ ] **F5** Knowledge console promoted to a top-level route with a filter
- [ ] **F5** Per-connection tab retained as a filtered view of the same screen
- [ ] **F5** Rail-entry decision made with real usage data

## Phase 6 — Responsive
- [ ] **F7** `.rm-chats` becomes an overlay drawer below ~700px
- [ ] **F7** `.rm-master` becomes an overlay drawer below ~700px
- [ ] **F7** Drawer toggle in each page header; closes on select
- [ ] **F7** Phase 2–5 surfaces verified at phone width
- [ ] **F7** Dashboard `STACK_BELOW_PX` stacking re-checked

---

## Verification for every phase

Run before considering a phase done:

```bash
cd frontend/          # from the repo root
npm run typecheck     # tsc --noEmit
npm run build         # tsc -b && vite build  — the real frontend gate
npm test              # the DOM-free logic modules
```

Backend touched (F6 only):

```bash
make test             # full suite
make guard            # the hostile SQL corpus
make lint             # ruff + import-linter contracts
```

> `npm run lint` is a dead script — eslint is not a declared devDependency and
> the repo carries no config. Do not add it to a phase's gate.

## Out of scope

Excluded by request (Low priority in the audit), listed so they are not lost:

- **F17** — `GET /audit` exists, admin-only, with no UI. Natural home: a tab on
  the Users page.
- **F18** — Setup destinations sit at equal rail weight with daily-use ones.
- **F19** — Three overlapping selector primitives; `PillTabs` stranded inside
  `semantic.tsx`.
- **F20** — `View` key `'settings'` renders the LLM providers page. *Partly
  resolved as a side effect of F1 and F6*, which put providers on `/providers`
  and the account screen on `/settings`.
