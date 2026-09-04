# The frontend

What the UI is made of, which screen owns what, and the rules the tree already
follows. This is the orientation doc — it stops at the point where a feature
doc takes over, and says which one that is.

**Authority is code, not this page.** The tokens in
[`theme/tokens.ts`](../frontend/src/theme/tokens.ts) and the primitives in
[`components/ui.tsx`](../frontend/src/components/ui.tsx) are the design system;
where this document and they disagree, they are right. The dark tokens are
copied verbatim from `assets/ui-design-concept.html` — the original concept,
which is dark-only. The light palette was designed afterwards against it.

---

## 1. The shell

[`main.tsx`](../frontend/src/main.tsx) mounts a **data router**
(`createBrowserRouter`) whose one catch-all route renders
[`App.tsx`](../frontend/src/App.tsx), which is the whole shell: an auth gate, a
232px rail, and one page filling the rest.

**Every screen has a URL.** The rail is `NAV` in `App.tsx` — a path, a label
and a glyph each — and the route table beside it gives each section a `/*`
path: `/chat` · `/dashboards` · `/reports` · `/knowledge` · `/sources` ·
`/providers` · `/users` (admin only; anyone else falls through) ·
`/settings` (your own account) · `/about`, and anything unrecognised redirects to `/chat`. Adding a
section means a `NAV` entry and a `<Route>` — except `/settings` and `/about`,
which are reached from the rail's footer rather than its list.

**A section owns the routes under it, and reads them with `useMatch` rather
than nesting a second `<Routes>`.** `/chat` → `/chat/:conversationId`,
`/dashboards/:id`, `/reports/:id` and its `/history` and `/runs/:runId`,
`/sources/:id/:tab` (`connection|policy|schema|semantic` — the first is the
bare `/sources/:id`, and `/sources/new` is the create form), `/providers/:id`,
`/knowledge/:connectionId`. `/sources/:id/knowledge` is kept as a **redirect**
to that last one: the console used to be rendered in both places, and one
screen at two addresses is a question the reader has to answer on every visit
for no benefit. The switch is inside the page because
the page must **stay mounted** across it: remounting a section on every open
and close would drop a chat's live stream and re-read a list the reader is
looking at. A `:id` that is not in a list the page has already loaded is
fetched on its own — a document reached by its own link has no index behind it.

**A page that hydrates a form from a list must key that on the loaded row, not
on the id in the URL.** The two differ for exactly one arrival, and it is the
one routing made possible: typing, bookmarking or refreshing `/sources/:id`
sets the id at mount while the list it has to be found in is still in flight.
An effect keyed on the URL fires once against a `selected` of `null`, bails,
and never runs again — so the form stays on its blank defaults, reports unsaved
changes in every field, and arms the navigation guard against edits nobody
made. Key it on `selected?.id`: the *id* rather than the row, because the row
is a fresh object after every list refresh and depending on it would discard
what the user had typed each time a save reloaded the list.

`createBrowserRouter` rather than `<BrowserRouter>` for one reason:
`useBlocker` exists only on a data router, and it is what makes the
unsaved-work guard possible. Nothing here uses loaders or actions.

The guard takes a **scope**. `useUnsavedWork(key, reason, within?)` registers
what would be lost and, optionally, the address it would survive inside. A
connection's tabs are separate routes and its draft is keyed on the connection
rather than the tab, so moving between them loses nothing — without `within`
the guard interrupted that move anyway, and a dialog that fires when nothing
is at stake is one people learn to dismiss unread. `App` filters the
registrations against the *pending* path, so the dialog also names the form
you are actually leaving rather than the first dirty thing on the app.

> **Wherever this is served, unknown paths must return `index.html`.** The Vite
> dev server and `vite preview` both do; a static host has to be told.

The signed-out side is routed too: `/about` is the credits page from either
side of the sign-in wall, and a deep link survives signing in — the location
does not change, only the gate in front of it.

The rail is **one flat list in the order the product is used**, not grouped:
captions over seven items are furniture, and a split invites a "which half is
this in?" decision on every glance. The order does the grouping instead, which
only works if it never doubles back — Chat, Dashboards, Reports and Knowledge
are today's work; Data sources, LLM providers and Users keep it running, in
falling order of how often anyone opens them. Knowledge is the fourth rather
than the fifth row because Chat is where a wrong answer gets flagged and
teaching one changes what Chat says next: it closes the loop the three above
it open. It spent Phase 5 under Data sources, on the argument that a console
belongs beside what it curates — but the way in from a single connection is
that connection's Knowledge tab, so the rail was never carrying that, and the
cost was the product's only badge sitting below the line where configuration
starts, where a count reads as a setting needing attention. Under it sits a quieter footer group — the
theme pair, the account, sign out, and Creators, which marks itself with colour
alone rather than the accent rail a nav row gets, because a credit given the
same weight as Chat reads as another place to work. The account block is the
name and avatar themselves — a button to `/settings` — with sign out kept as a
separate target beside it: leaving and editing are two intentions, and one
target for both is how people sign out by accident.

**Knowledge is the one rail entry with a count**, and the count is the argument
for the entry: it exists because a curation queue nobody can see is not a
queue, so if it is permanently empty on a real install the row has not earned
its place. **Red is reserved for a flag somebody raised** on a wrong answer; a
backlog of questions nothing answers yet is amber, because that is an
opportunity rather than a defect — and a mark that cries wolf about a backlog
is one people stop looking at, which is the exact failure the badge was added
to fix. `queueTone` in `knowledge-queue.ts` is the rule, and the console's rows
and its detail chips both follow it.

The view area is the document's `<main id="main">`, and a `.rm-skip` link
before the rail is the first thing in the tab order — the rail is seven
destinations plus a footer group, and a keyboard user should not have to walk
them to reach the page they opened.

Theme is `dark | light` in `localStorage`, applied by `applyTheme` writing
every token onto `:root` as a CSS variable plus a `data-theme` attribute. Two
things read that attribute rather than the variables: Vega charts, whose
colours are chosen in JS, and the print stylesheet.

---

## 2. The sections

| Section | Page | What it is for |
| --- | --- | --- |
| **Chat** | [`ChatPage.tsx`](../frontend/src/pages/ChatPage.tsx) | Ask one question, watch it answered, keep the thread. The section the product opens on. |
| **Dashboards** | [`DashboardsPage.tsx`](../frontend/src/pages/DashboardsPage.tsx) | Numbers that are always current: a grid of tiles that refresh themselves. |
| **Reports** | [`ReportsPage.tsx`](../frontend/src/pages/ReportsPage.tsx) | A document whose structure a human approved, generated over real results and kept as a snapshot. |
| **Knowledge** | [`KnowledgePage.tsx`](../frontend/src/pages/KnowledgePage.tsx) | The curation console, across every connection: flags raised, questions nothing answers, the maintenance sweep. |
| **Data sources** | [`DataSourcesPage.tsx`](../frontend/src/pages/DataSourcesPage.tsx) | The connections DataMind may read, and everything known about each one. |
| **LLM providers** | [`LlmProvidersPage.tsx`](../frontend/src/pages/LlmProvidersPage.tsx) | The models it may call, and the keys it calls them with. Two groups: *Models* answer questions, the *Embedder* makes vectors — a row is one or the other, and the form shows that kind's fields only. |
| **Users** | [`UsersPage.tsx`](../frontend/src/pages/UsersPage.tsx) | Who can sign in and what they may do. Admin only — the rail entry is not rendered otherwise. |
| **Your account** | [`AccountPage.tsx`](../frontend/src/pages/AccountPage.tsx) | `/settings`: your display name and your password. Reached from the user block in the rail, not from `NAV`. |
| **Creators** | [`AboutPage.tsx`](../frontend/src/pages/AboutPage.tsx) | Who built it. A colophon, not a destination; the one page on both sides of the sign-in wall. |
| **Login** | [`LoginPage.tsx`](../frontend/src/pages/LoginPage.tsx) | The only public surface, and therefore the only signed-out route to Creators. |

Three of these are **index pages** — Dashboards, Reports, Users — and they
share their furniture deliberately: the same page header, the same toolbar
(search, a filter offered only when there is something to filter, a sort), the
same loading skeleton, the same empty states. Dashboards and Reports offer
cards *or* rows because their records have a face worth showing; Users is rows
only, because the facts about an account are a line, not a card.

Three are **master–detail** — Data sources, LLM providers and Knowledge — and
share the frame in
[`components/settings.tsx`](../frontend/src/components/settings.tsx)
(`MasterColumn`, `MasterItem`, `DetailHeader`, `DetailBody`, `Section`,
`FieldRow`, `Tabs`, `StatusLine`, `UnsavedNote`). Two pages that configure a
credential and probe it should not each invent their own shape, and the third
picks a connection for the same reason.

`MasterColumn`'s new-verb and its count pill are both **optional**, and
Knowledge is why. That column lists connections as a *filter*, not as the thing
being counted: a pill reading `2` under the word "Knowledge" contradicts the
rail's badge reading `42` three inches away, and the column's loudest control
pointing at *Add a connection* would make the curation console's most prominent
offer a way out of the section into a create form for something else. So the
console shows neither — the rows carry the per-connection counts, the rail
carries the total, and the one place *Add a connection* belongs is the empty
state beside it, where it is the single thing that unblocks the page.

**Knowledge is one screen with two doors.** `/knowledge/:id` renders
`KnowledgeTab` — the console that used to be a connection's fourth tab —
behind a connection picker ordered by how much work each one is waiting on.
The console was always there; what was missing was a way to find it, because a
work queue three clicks inside one connection's fourth tab cannot ask for
attention. So the promotion adds a column and a rail badge and changes nothing
about the console itself.

The tab **stays in the Data sources strip**, because that is where people look
for a connection's store — but it is a *door*, not a second room: it navigates
to `/knowledge/:id` for the connection you are on, and carries an arrow saying
so (`leaves` on `Tabs`). It rendered its own copy of the console at first, and
that was wrong. A scoped view beside a global one is a good pattern — a repo's
issues and all your issues — but both of these had the *same* scope and the
same content, which is not that pattern; it is one screen twice, and the reader
pays for it with a "which one do I use?" on every visit.

### Sub-sections

```
Chat            rail of threads (date-bucketed) │ header: database · model · disclosure
                transcript │ composer │ the template editor, borrowed from Knowledge
                welcome: the setup checklist while unusable, the starters once not

Dashboards      index (cards│rows) → one board
                  view mode ⇄ edit-grid mode · tile kebab in both
                  tile editor (drawer beside the grid, own URL, 2 tabs over one SQL box)
                  focus mode · presentation mode · import/export

Reports         index → outline editor → run viewer → run history
                  outline editor is a workflow: Describe → Structure → Check → Generate

Knowledge       master (connections, busiest first) → the same console the
                  Data sources tab renders, filtered to one connection

Data sources    master → detail, 4 tabs and a door:
                  Connection · Policy · Schema (tables│graph) · Semantic layer
                  · Knowledge → (leaves, to /knowledge/:id)
                  two forms, two Saves, Test on Connection only
                  semantic detail: Meaning │ Columns │ Metrics

LLM providers   master → detail, one form

Users           rows, inline detail, one-time password panel

Your account    /settings, from the rail's user block: display name, password
```

---

## 3. How the sections relate

**A connection is the spine.** Chat binds a thread to exactly one; every
dashboard tile carries one; a report is created against one; the semantic layer
and the knowledge templates hang off one. That is why Data sources is not a
settings page in spirit — three of the four working surfaces are unusable until
one exists, and each of them says so in its own empty state rather than failing
at the first request.

**Four components are shared across sections, and that sharing is the point:**

| Component | Serves |
| --- | --- |
| [`VegaChart.tsx`](../frontend/src/components/VegaChart.tsx) | a chat turn, a `CHART` tile, a report figure, and the print redraw |
| [`chart-picker.tsx`](../frontend/src/components/chart-picker.tsx) | chat (redraws the stored artifact — never re-queries), report, tile editor |
| `ResultTable` / `Kpi` ([`ui.tsx`](../frontend/src/components/ui.tsx)) | chat results, `TABLE` and `METRIC` tiles, report figures and `plan_kpi` bands |

`ResultTable` carries three behaviours worth naming, all of them client-side
over rows it already has — **none of them ever re-runs a query**, for the same
reason the chart picker redraws from rows already returned:

- **Click a heading to sort**: ascending, descending, then *away*, back to
  whatever the tile stored — a two-state toggle would leave no way back to a
  configured ordering. `aria-sort` says which, and the mark is drawn on every
  heading but only shown under the pointer or where the sort is.
- **Past 200 rows the expanded table renders a window**, with spacer rows of
  exactly the height of what is not drawn, so the scrollbar stays honest. A
  thousand rows — the default row cap — used to mount as six thousand cells.
- **Download as CSV**, opt-in per call site (`download` names the file). The
  file has the columns the reader can see, in their order, under their
  headings, and **raw values rather than the formatted text on screen**: a
  spreadsheet has to compute with them. Escaping is RFC 4180, plus a leading
  apostrophe on anything starting `=`, `+` or `@`, which Excel would otherwise
  treat as a formula.
| `TemplateEditor` ([`knowledge.tsx`](../frontend/src/components/knowledge.tsx)) | the Knowledge tab, and *Save as template* on a chat answer |

That last one is the rule stated generally: **when two screens must agree about
a guard verdict, a disclosure rule or a parameter proposal, they share the
component.** Two editors are two chances to get one of them wrong.

**An answer can leave Chat.** Its action row offers *Add to dashboard* and
*Add to report* beside *Save as template*, all three guarded on the same fact —
the run produced a statement (`run.queries.length > 0`). What travels is the
run's own work: the question, the statement that ran, the connection, and the
chart type read from the spec's `usermeta`. **No model call, no re-execution.**
The dashboard road ends in the tile editor, prefilled and one screen further
on; the report road writes the block itself — created from the question, then
`PUT .../sql` — which is what marks the SQL `HANDWRITTEN`, so *Check all* (a
sweep over blocks with **no** query) can never overwrite it. Both are refused,
with the reason on the page rather than in a `title`, when the thread's
connection is gone; the report alone is refused under a `NONE`/`AGGREGATE`
policy, because a report's prose is written from result values and a tile never
sends one to a model.

**A fresh install is told what to do next, from what it already knows.** The
chat welcome screen shows a four-step checklist — provider · connection ·
schema sync · semantic layer (optional) — derived from the two lists the page
already fetches for its own pickers, with **no new endpoint**. It replaces the
starter questions rather than joining them (a chip that cannot be answered is
worse than no chip) and disappears the moment the three gating steps are done;
it is dismissible, and that sticks. The third step is not the formality it
looks: an unsynced connection can answer nothing, because the guard resolves
every name against the stored snapshot. An empty picker in the header offers
the way in rather than saying *None configured* at a dead end.

**Two screens never poll a hidden tab.** The dashboard scheduler and the report
generation poll both pause on `document.hidden`. A forgotten background tab is
how a feature becomes the reason someone's production database is slow.

**What a page may ask of the shell is one module**
([`shell.tsx`](../frontend/src/shell.tsx)), and both entries in it exist
because a page was reaching past its own edge:

| Hook | For |
| --- | --- |
| `useThemeOverride(theme \| null)` | a dashboard pinned to DARK or LIGHT. `App` resolves `override ?? the user's own choice` and is the **only** caller of `applyTheme`, so a rail toggle made during an override is still there when it clears |
| `useUnsavedWork(key, reason \| null)` | a dirty form. One `useBlocker` in the shell stops every navigation out of one — the rail, a row in the master column, browser Back — and asks. The hook returns a release, because a form that has just saved itself and is navigating as part of that save must let go before it goes |

Both are *requests*: the shell stays the single owner, so two components can
never disagree about what the theme is or about whether it is safe to leave.

---

## 4. The design system

Three layers, and which one a change belongs in is not a preference.

**[`theme/tokens.ts`](../frontend/src/theme/tokens.ts)** — every colour, in
oklch, with a dark *and* a light definition. Also `NODE_META` (the pipeline
steps, in order), `DATABASE_TYPES` (removing an entry removes the engine from
the picker) and `PROVIDER_URLS` (likewise, the provider picker). Never hardcode
a colour: a literal in a component is a bug in both themes, one of which has
simply not been looked at yet.

| Meaning | Token |
| --- | --- |
| Verified · active · healthy | `--green`, `--green-bg`, `--green-border` |
| Needs a human — stale, conflicted, flagged | `--amber`, `--amber-bg` |
| Guard rejection, destructive confirm | `--red`, `--red-bg` |
| Grounded, parameters, selection, "you are here" | `--accent`, `--accent-bg`, `--accent-border` |
| Quiet metadata, not-yet-anything | `--text-dim`, `--text-faint` |
| SQL, ids, figures | `--code-bg`, `--code-text` |

The light theme's plum accent (hue ~315, from the logo) was chosen precisely so
it collides with neither warning-amber nor error-red. A per-record identity hue
(`identityHue` / `engineHue`) belongs to the *record* and is the same in both
themes — otherwise the colour stops being a legend — while the lightness it is
mixed at belongs to the ground, which is what `glyph-tint-l` / `glyph-ink-l`
are for.

**[`components/ui.tsx`](../frontend/src/components/ui.tsx)** — ~40 primitives,
every dimension lifted from the design concept rather than invented: `Logo`,
`Icon.*`, `Field` / `TextInput` / `Select` / `TextArea` / `Toggle` /
`NumberStepper` / `InlineEdit`, `PrimaryButton` / `GhostButton` / `QuietAction`
/ `DangerButton`, `Chip` / `Dot` / `GlyphBadge` / `DisclosureBadge`, `Modal` /
`EmptyState` / `ErrorNote` / `Spinner` / `ProgressBar` / `Segmented` /
`SearchField` / `PageHeader`, `Drawer`, `ResultTable`, `Kpi`, `CopyButton`, and the
helpers `relativeTime`, `initialOf`, `identityHue`, `glyphTint`, `dirOf`.
Compose from these before writing a new one. **There is no component library**
and adding one is not on the table; `react-grid-layout` is a layout engine, not
a counter-example.

**[`styles.css`](../frontend/src/styles.css)** — everything a style attribute
cannot express, in ~30 banner-commented regions: the three self-hosted
`@font-face` families (Inter, JetBrains Mono, **Vazirmatn** — never from Google,
because this ships behind firewalls and a printed Persian report is a
deliverable), the global reduced-motion opt-out, the grid motif, the auth and
welcome ambients, the dashboard grid and its resize handles, skeletons,
`@media print`, the rail, the chat list, the settings screens, the people
index, Creators, and the thinking panel.

**The split:** layout, spacing and one-off values are inline `style={}` in the
JSX. `:hover`, `:focus-visible`, selection, keyframes, print and the responsive
reflow are `.rm-*` classes. No CSS modules, no utility classes. Where a rule
must beat an inline declaration it uses `!important` and says why on the line
above.

---

## 5. Rules that are not style

- **Status is never colour alone.** Every state carries a glyph and a word, so
  the screen survives greyscale, a print stylesheet, and colour blindness.
- **Validation is never guessed at locally.** A metric expression, a SQL draft,
  a template — the verdict comes from the same backend parser that will reject
  it at save time, and the guard's own sentence is rendered verbatim. A
  re-worded rejection is one the user cannot act on; a local "looks fine" the
  server then rejects is the worst interaction in the product.
- **Text a person wrote gets `dir={dirOf(value)}`.** SQL is always `dir="ltr"`,
  in both themes and both directions — a bidi-reordered statement is unreadable
  and, worse, ambiguous.
- **A credential is testable before it is saved.** Both master–detail pages
  probe the *form* while it is dirty and persist nothing, so nobody has to
  leave a broken row behind to find out it is broken.
- **Identity and policy do not share a Save button.** A connection's
  credentials and its governance are edited by different people on different
  days, so they are two tabs with two dirty states, and each Save sends only
  its own fields. Nobody should have to open a form containing a password to
  change a disclosure policy, and two people editing two tabs must not
  overwrite each other with values neither of them looked at.
- **Work that outlives its screen says so; everything else stays put.** The
  shell has one `aria-live` corner (`components/notifications.tsx`), raised
  through `useNotify`, and one background watcher (`useBackgroundWatch`) that
  keeps polling a job after the page that started it has unmounted. It is
  reserved for exactly that: a four-minute semantic generation, a benchmark
  run, a sweep that found two templates disagreeing. In-page errors are **not**
  moved there — an `ErrorNote` beside the thing that failed explains it better
  than a corner of the screen can — and a notice never duplicates something
  already visible on the page the reader is on.
- **A count appears when there is work, never as decoration.** The rail's
  Knowledge badge and the Knowledge tab's count are the same number
  (`knowledge-queue.ts`), absent rather than `0`, and they mirror exactly what
  the console lists: a badge showing four over a list of two is how a reader
  learns to stop believing badges.
- **Say who owns a list, where the list is read.** Connections, providers,
  dashboards and reports are scoped to `owner_id`, so two colleagues see the
  same labels and different lists. The master columns carry one quiet line
  saying so, and every empty state is written for a second person's first
  visit — an empty list that reads as a missing one is a bug report waiting to
  be filed.
- **Destructive acts are confirmed, and a refusal is spoken.** A rejected
  delete that reaches nobody looks exactly like a delete that silently did
  nothing.
- **Every page has exactly one `<h1>`, and it is a thing already on screen.**
  The index pages get theirs from `PageHeader` (Dashboards, Reports and Users
  all render from it — a hand-rolled fourth copy is how three headers stop
  agreeing), the master–detail pages from `MasterColumn`'s title, and Chat from
  the conversation title, which goes visually hidden rather than away while it
  is being renamed.
- **A dialog is modal to the keyboard, not only to the mouse.** `Modal` moves
  focus in on open — leaving it where an `autoFocus` field put it — cycles Tab
  within itself, names itself with `aria-labelledby`, and hands focus back to
  the control that opened it. `aria-modal` alone is a claim, not a behaviour.
- **A stale number beats an empty one.** A reloading tile keeps its figures
  with "refreshing" in the header; a failed poll leaves the last result in
  place with its own timestamp.
- **Say what happened, not what the system did.** *"This template stopped
  working"*, not *"validation failure"*. Never call it AI — it is a saved
  question. Never apologise for schema drift; it is not a mistake anyone made.

---

## 6. Responsive, and print

Breakpoints are per-region rather than a global scale, because the things that
need to give way differ by screen. The set, and what each does:

| Width | What changes |
| --- | --- |
| ≤900px | The rail's chat list and the master column narrow (208 / 212px); the dashboard drawer floats over the grid instead of halving it |
| ≤880px | The people index drops its added-date column and legend |
| ≤860px | **The rail collapses to 66px** — labels hidden, glyphs stand in, the theme pair reduces to the other theme's icon; the outline steps wrap |
| ≤820px | The live-refresh label goes, leaving the dot |
| ≤760px | The dashboard's edit-mode hint is dropped |
| ≤720px | Report figures go single-column; dashboard row metadata is dropped |
| ≤700px | **The second column becomes an overlay** — the chat list and the master column go off-canvas beside the rail, with a toggle in each page header, a scrim, Escape, and a close on every navigation; the chat header wraps its two pickers onto their own line; tab strips scroll; the dashboard toolbar keeps its shape and hides its labels (visually — they are still the buttons' names) |
| ≤640px | Page padding tightens; two- and three-column form rows stack rather than crush |
| ≤620px | The dashboard grid stops being a grid: tiles stack full-width in order (`STACK_BELOW_PX`) |

**Nothing below 700px has two fixed columns.** That was the whole finding: the
rail already collapsed to 66px at 860px, but the second column did not give
ground with it, so a 375px screen had about a hundred pixels left for the
transcript, the form or the document. The mechanism is one module —
[`list-drawer.tsx`](../frontend/src/components/list-drawer.tsx) — shared by
Chat, Data sources, LLM providers and Knowledge, and it closes on the path
changing rather than on each page remembering to call back: every one of those
lists navigates when you pick something, and a drawer left open over the thing
you just chose is how this pattern is usually got wrong. The drawer sits
*beside* the collapsed rail rather than over it, because the rail is how you
leave the page.

`@media print` is a report feature, not a general one: it forces the light
token set over `applyTheme`'s inline styles, hides the shell, keeps a figure
whole across a page break, opens collapsed disclosures, and gives the article a
definite width. The two halves a stylesheet cannot reach — fonts, and redrawing
each Vega plot at page width in the light palette — are
[`report-print.ts`](../frontend/src/components/report-print.ts).

---

## 7. What is tested, and what is not

Twelve modules are deliberately **DOM-free** and carry their own suites, because
every way they can be wrong is quiet: `dashboard-schedule.ts`,
`table-format.ts`, `dashboard-document.ts`, `palette.ts`, `chat-format.ts`,
`report-document.ts`, `report-readiness.ts`, `report-print.ts`,
`semantic-drift.ts`, `knowledge-template.ts`, `thinking.ts`,
`knowledge-queue.ts`.

There is no test runner — each suite is a plain `node
--experimental-strip-types` script, run by `npm test`. Keeping these modules
free of React is what makes that possible, so **one React import turns a suite
into a thing that cannot run.**

Everything else is untested. **`npm test` is not in CI** (which runs
`tsc --noEmit` and `vite build` only) and `npm run lint` is a dead script —
eslint is neither a dependency nor configured. Run `npm test` yourself.

---

## 8. Where to read next

| Changing | Read |
| --- | --- |
| A tile, the grid, the refresh clock | [dashboards.md §6](dashboards.md) |
| The outline editor, the viewer, the PDF | [reports.md §11–§12](reports.md) |
| A chart, the picker, a colour in one | [charts.md](charts.md) |
| The knowledge console, the badge, the copy | [learning-loop-plan.md §4](learning-loop-plan.md) — the one full design brief in the repo; §4.2's information architecture is superseded and says so where it stands, since the console is now a rail entry as well as a tab |
| Why a surface is shaped the way it is | [ui-improvment-plan.md](ui-improvment-plan.md) — the audit this shell was rebuilt from, sixteen findings in seven phases, with the decisions taken while executing recorded inline |
| The schema browser, catalog descriptions | [catalog-metadata-plan.md](catalog-metadata-plan.md) |
| What a step chip means | [pipeline.md](pipeline.md) — the nodes behind `NODE_META` |
| What may be shown to whom | [security.md](security.md) — the disclosure policy the header badge names |

The file-header docblock of the component you are about to change is generally
more specific than any of these, and is where the reasoning for a layout
decision was recorded at the time it was made. Read it first.
