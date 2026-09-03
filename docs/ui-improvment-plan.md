# DataMind — UI/UX & Structure Remediation Plan

> **Status: executed.** All seven phases and all 55 checklist items landed on
> **2026-09-03**, one commit per phase, `ea26f66..e9d5301`, with two
> corrections after review. **§ The ledger** at the foot of this file is the
> record: what each phase actually shipped, the decisions taken while
> executing, and what had to be fixed afterwards.
>
> Read this file for *why* a surface is shaped the way it is.
> [frontend.md](frontend.md) describes what was built — where the two
> disagree, frontend.md is right.

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
- [x] **F3** Empty `HeaderSelect` menus navigate to `/providers` / `/sources`
- [x] **F3** Setup checklist on the Chat welcome screen
- [x] **F3** Checklist state derived from existing boot data (no new endpoint)
- [x] **F3** Checklist hides once the four steps are satisfied
- [x] **F4** CSV download control on `ResultTable`
- [x] **F4** Export respects resolved column config; escaping verified
- [x] **F4** Live at all three call sites (chat, tiles, reports)
- [x] **F11** Click-to-sort headers with `aria-sort`
- [x] **F11** Stored tile sort still the default; user sort layers over it
- [x] **F11** Windowed rendering past ~200 rows
- [x] **F11** Sorting never re-runs the query

## Phase 4 — Configuration clarity
- [x] **F8** Settings tab split into Connection / Policy
- [x] **F8** Independent dirty tracking + Save per tab
- [x] **F8** `Test connection` scoped to the Connection tab
- [x] **F16** Ownership line on Data sources + LLM providers master columns
- [x] **F16** Empty states rewritten for a second user's first visit
- [x] **F16** Dashboards + Reports index subtitles updated
- [x] **F6** Backend: `PATCH /auth/me`
- [x] **F6** Backend: `PUT /auth/me/password` (verifies current, rotates refresh)
- [x] **F6** Frontend: account screen at `/settings`, opened from sidebar user block
- [x] **F6** Admin set-password path unchanged

## Phase 5 — Background work made visible
- [x] **F13** Notification surface at the shell with `aria-live`
- [x] **F13** Scoped to background/cross-page events only
- [x] **F13** Inline `ErrorNote` usage left untouched
- [x] **F13** Wired: semantic generation, benchmark runs, conflict checks
- [x] **F5** Cross-connection review/suggestion count badge on the rail
- [x] **F5** Knowledge console promoted to a top-level route with a filter
- [x] **F5** Per-connection tab retained as the way in from a connection
- [x] **F5** Rail-entry decision made with real usage data

> **The step-4 decision: it earns a rail entry.** Not because the queue is
> large (on this install it is 42 across two connections), but because the
> alternative cannot satisfy the finding. "Reachable in one click from
> wherever they are" means the rail; a badge on Data sources that then needs a
> second click to reach the console leaves the queue exactly as far away as it
> was. So `Knowledge` sits in the rail directly after Data sources — what it
> curates — and carries the product's only count badge. The badge is also the
> argument for the row: if it is permanently empty on a real install, this
> entry has not earned its place and should go back to being a tab.
>
> Consequently the badge lives on **that** entry rather than on Data sources
> as step 1 wrote it. Step 1 described the cheapest first move, taken *before*
> the promotion; once the console has its own destination, two badges for one
> number is noise. The per-connection count on the Knowledge tab is unchanged
> from step 1 and is now live — the objection that kept it off (the number was
> not known until the tab had loaded) is answered by the shell counting it.
>
> **Corrected after review.** The first cut of the console reused the settings
> column wholesale and inherited three things that do not belong on it:
>
> - *Add a connection* as the column's primary action, which made the loudest
>   control on the curation screen a way out of the section into a create form
>   for something else. `MasterColumn`'s new-verb is optional now, and the one
>   place that offer belongs is the empty state beside it.
> - The column's count pill, which showed the number of **connections** (`2`)
>   under the word "Knowledge" while the rail showed the size of the **queue**
>   (`42`) three inches away. Two different numbers under one word is worse
>   than no number; the pill is optional now and the console omits it.
> - A summary line restating the rows underneath it. The list is sorted
>   busiest-first and each row carries its own count, so the sentence answered
>   nothing the rows did not. It and its `queueSentence` helper are gone.
>
> And the badge's colour was wrong: red over a queue that is entirely
> *suggestions* says something is broken on a connection where nothing is.
> Red is now reserved for a flag somebody raised on a wrong answer, amber for a
> backlog — `queueTone`, followed by the rail, the rows and the detail chips
> alike. A mark that cries wolf about a backlog is one people stop looking at,
> which is the failure this badge exists to prevent.
>
> **Step 3 was read too literally, and the console shipped twice.** "Keep the
> per-connection tab as a filtered view of the same screen" was implemented as
> the tab rendering its own copy of `KnowledgeTab` — so `/knowledge/:id` and
> `/sources/:id/knowledge` were byte-identical screens at two addresses, with
> the *same* scope. A scoped view beside a global one is a sound pattern (a
> repo's issues, and all your issues); this was not that, because neither view
> was global — `/knowledge` is a connection picker in front of a
> per-connection console. It was one screen twice, and the reader paid for it
> with a "which one do I use?" on every visit.
>
> The tab is now a **door**: it stays in the strip with its count and an arrow,
> and it navigates to `/knowledge/:id` for the connection you are on.
> `/sources/:id/knowledge` redirects there, so anything already bookmarked
> still opens the console. Step 3's actual requirement — "a curator working on
> one database is not forced up a level" — is met by the tab preserving the
> connection, which the rail entry deliberately does not: the rail is the
> queue view and opens wherever the work is.
>
> The rejected alternative was to make `/knowledge` genuinely cross-connection
> — one merged queue of flags and suggestions from every connection, with the
> templates, benchmarks and sweep staying on the tab. It is the better-sounding
> design and it is wrong here, because it breaks the thing §4.2 of the learning
> loop's design brief built the console around: *one work list, three sections,
> one detail pane, so "what should I do next" is never a navigation problem.*
> Splitting the queue away from the templates reintroduces exactly that
> problem, and at one or two connections — which is most installs — the merged
> queue is identical to the per-connection one anyway.

## Phase 6 — Responsive
- [x] **F7** `.rm-chats` becomes an overlay drawer below ~700px
- [x] **F7** `.rm-master` becomes an overlay drawer below ~700px
- [x] **F7** Drawer toggle in each page header; closes on select
- [x] **F7** Phase 2–5 surfaces verified at phone width
- [x] **F7** Dashboard `STACK_BELOW_PX` stacking re-checked

> Three things the responsive pass had to fix that the finding did not name,
> because they were invisible until the second column stopped taking the
> width: the chat header's two pickers are 232px + 190px of deliberately
> fixed trigger, wider together than a phone, so below 700px they take a line
> of their own and share it; a five-tab strip is ~430px, so it scrolls rather
> than clipping Knowledge off the end; and the dashboard toolbar's Present /
> Edit grid / Settings keeps its shape and hides its labels *visually* —
> `display: none` would have left three unnamed glyphs, so it uses `.rm-sr`'s
> clip instead and the accessible names survive.
>
> The tile-editor drawer (F10) needed no change: it was already
> `min(420px, 92vw)` below 900px. The account screen (F6), the split settings
> tabs (F8) and the notification card (F13) were all checked at 375px and
> none of them overflows.

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

---

# The ledger

What actually landed, in the order it landed. The checklist above says
*whether*; this says *what*, *where*, and — where it matters more than
either — *what changed while it was being executed*.

Every phase was gated on `npm run typecheck`, `npm run build` and `npm test`
from `frontend/`, and each was driven in the running app with Playwright
before it was committed. Phase 4 touched the backend and added `make test`,
`make guard` and `make lint` to its gate.

| # | Commit | Size | What shipped |
| --- | --- | --- | --- |
| 0 | `ea26f66` | 10 files, +265/−147 | `PageHeader` adopted by all three index pages, `.rm-dash-index` → `.rm-index`, the `<main>` landmark and skip link, one `<h1>` per page, and `Modal` given real focus management |
| 1 | `1905538` | 11 files, +544/−175 | `createBrowserRouter`, the `View` union deleted, every section on a `/*` route reading its own sub-routes with `useMatch`, `shell.tsx` (theme override + unsaved work), and one `useBlocker` guard for the whole app |
| 2 | `0b90fac` | 11 files, +1005/−83 | The tile editor became a routed `Drawer` beside the live grid; *Add to dashboard* and *Add to report* on the answer action row, carrying the run's own SQL |
| 3 | `aacb229` | 11 files, +858/−93 | Empty header pickers navigate somewhere; the setup checklist on the welcome screen; CSV download, click-to-sort and windowed rendering on `ResultTable` |
| 4 | `087eafa` | 18 files, +1254/−208 | Connection / Policy split with independent Saves; per-user ownership stated where lists are read; `PATCH /auth/me`, `PUT /auth/me/password` and the account screen at `/settings` |
| 5 | `ca3bda8` | 14 files, +1173/−38 | The shell's `aria-live` notice surface and its background watcher; the knowledge console promoted to `/knowledge` with a rail badge and `knowledge-queue.ts` |
| 6 | `e9d5301` | 11 files, +360/−17 | The second column becomes an off-canvas drawer below 700px, with every surface the earlier phases added checked at 375px |
| — | `48a6c27` | 10 files, +186/−34 | The docs that still described the shell this work replaced: README, the docs index, CODEBASE, reports, dashboards, security, and the learning loop's superseded §4.2 |

## Decisions taken while executing

Recorded inline where they were made, and listed here so none is lost:

- **Phase 1 → Phase 4.** The single navigation guard learned a *scope*
  (`useUnsavedWork(key, reason, within?)`). Splitting the connection form into
  two routed tabs made the guard fire on a move that loses nothing, and a
  dialog that interrupts when nothing is at stake is one people learn to click
  through unread.
- **Phase 4.** A wrong *current* password answers **422, not 401** — the
  client reads a 401 as a dead session, which would end a typo in a sign-out
  screen. The rotation revokes every session and immediately issues a fresh
  one, so the person who changed their password is the only one still signed
  in rather than the only one signed out.
- **Phase 5.** The knowledge badge moved off Data sources onto the promoted
  entry. Step 1 described the cheapest move, taken *before* the promotion;
  once the console has its own destination, two badges for one number is
  noise. The full argument is the block quote in Phase 5's checklist above.
- **Phase 6.** Three things the audit did not name, invisible until the second
  column stopped taking the width: the chat header's two fixed-width pickers,
  a five-tab strip wider than a phone, and a dashboard toolbar whose labels
  had to go *visually* rather than out of the accessibility tree.

## Corrected after review

Review found five things this plan got wrong, or that the implementation got
wrong while following it. They are the honest part of the record.

1. **The knowledge column borrowed what it was not** — `7d4fc41`. Promoting
   the console reused the settings master column wholesale and inherited an
   *Add a connection* primary action (a way *out* of the section, as the
   loudest control on the page), a count pill showing the number of
   connections under a word the rail was labelling with the size of the queue,
   and a summary line restating the rows below it. All three are gone;
   `MasterColumn`'s new-verb and count pill are optional now.
2. **The badge cried wolf** — `7d4fc41`. Red over a queue that is entirely
   *suggestions* says something is broken where nothing is. Red is now
   reserved for a flag somebody raised on a wrong answer; a backlog is amber
   (`queueTone`), and the rail, the rows and the detail chips all follow it.
3. **The console shipped twice** — `91f3f8b`. Step 3's *"keep the
   per-connection tab as a filtered view of the same screen"* was implemented
   as the tab rendering its own copy, so two addresses with the **same scope**
   rendered byte-identical screens. A scoped view beside a global one is a
   sound pattern; two views of one scope is one screen twice. The tab is a
   **door** now — it navigates to `/knowledge/:id` — and the old address
   redirects there.
4. **A deep link showed a blank form** — `91f3f8b`, and the worst of the five,
   because it predates the review by five phases. Both master–detail pages
   hydrated their form in an effect keyed on the id *in the URL*. On the
   arrival Phase 1 exists to make possible — typing, bookmarking or refreshing
   `/sources/:id` — that id is set at mount while the list it must be found in
   is still in flight, so the effect ran once against a `null` row, bailed,
   and never ran again: blank values on screen, both halves reporting unsaved
   changes nobody had made, and the navigation guard then refusing to let
   anyone leave. Keyed on `selected?.id` it hydrates when the row arrives.

   No phase verification caught it, and the reason is worth keeping: every
   scripted check reached a connection through `/sources` and its landing
   redirect, which sets the id *after* the list has loaded. **A route a test
   only ever arrives at from inside the app is a route nobody has tested.**
5. **The first-run panel argued with the page under it** — the commit that
   added this ledger. On a
   connection with an empty store and a backlog, a hero panel read *"write a
   question the way someone would ask it, paste the SQL that answers it"*
   directly above twenty-two questions people really asked, each with a *Teach
   this* button beside it. It buried the better path under an invitation to
   ignore it, and spent 200px of the top of the page doing so. The hero is now
   the hero only when the page really is empty; with a queue below it, the
   framing is one line that points at the queue.

---

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
