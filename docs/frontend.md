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

[`main.tsx`](../frontend/src/main.tsx) mounts
[`App.tsx`](../frontend/src/App.tsx), which is the whole shell: an auth gate, a
232px rail, and one page filling the rest.

**There is no router.** `react-router-dom` is a dependency and is imported
nowhere. Navigation is the `View` union in `App.tsx` and one `useState`; the
rail sets it and the shell renders a branch. Adding a section means a member of
that union, a rail entry and a branch — not a `<Route>`. The signed-out side
has no router either, which is why About is reached there through a boolean.

The rail is **one flat list in the order the product is used**, not grouped:
captions over six items are furniture, and a split invites a "which half is
this in?" decision on every glance. Under it sits a quieter footer group — the
theme pair, the account, sign out, and Creators, which marks itself with colour
alone rather than the accent rail a nav row gets, because a credit given the
same weight as Chat reads as a sixth place to work.

The view area is the document's `<main id="main">`, and a `.rm-skip` link
before the rail is the first thing in the tab order — the rail is six
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
| **Data sources** | [`DataSourcesPage.tsx`](../frontend/src/pages/DataSourcesPage.tsx) | The connections DataMind may read, and everything known about each one. |
| **LLM providers** | [`LlmProvidersPage.tsx`](../frontend/src/pages/LlmProvidersPage.tsx) | The models it may call, and the keys it calls them with. |
| **Users** | [`UsersPage.tsx`](../frontend/src/pages/UsersPage.tsx) | Who can sign in and what they may do. Admin only — the rail entry is not rendered otherwise. |
| **Creators** | [`AboutPage.tsx`](../frontend/src/pages/AboutPage.tsx) | Who built it. A colophon, not a destination; the one page on both sides of the sign-in wall. |
| **Login** | [`LoginPage.tsx`](../frontend/src/pages/LoginPage.tsx) | The only public surface, and therefore the only signed-out route to Creators. |

Three of these are **index pages** — Dashboards, Reports, Users — and they
share their furniture deliberately: the same page header, the same toolbar
(search, a filter offered only when there is something to filter, a sort), the
same loading skeleton, the same empty states. Dashboards and Reports offer
cards *or* rows because their records have a face worth showing; Users is rows
only, because the facts about an account are a line, not a card.

Two are **master–detail** — Data sources and LLM providers — and share the
frame in [`components/settings.tsx`](../frontend/src/components/settings.tsx)
(`MasterColumn`, `MasterItem`, `DetailHeader`, `DetailBody`, `Section`,
`FieldRow`, `Tabs`, `StatusLine`, `UnsavedNote`). Two pages that configure a
credential and probe it should not each invent their own shape.

### Sub-sections

```
Chat            rail of threads (date-bucketed) │ header: database · model · disclosure
                transcript │ composer │ the template editor, borrowed from Knowledge

Dashboards      index (cards│rows) → one board
                  view mode ⇄ edit-grid mode · tile kebab in both
                  tile editor (modal, 2 tabs over one SQL box)
                  focus mode · presentation mode · import/export

Reports         index → outline editor → run viewer → run history
                  outline editor is a workflow: Describe → Structure → Check → Generate

Data sources    master → detail, 4 tabs:
                  Settings · Schema (tables│graph) · Semantic layer · Knowledge
                  semantic detail: Meaning │ Columns │ Metrics

LLM providers   master → detail, one form

Users           rows, inline detail, one-time password panel
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
| `TemplateEditor` ([`knowledge.tsx`](../frontend/src/components/knowledge.tsx)) | the Knowledge tab, and *Save as template* on a chat answer |

That last one is the rule stated generally: **when two screens must agree about
a guard verdict, a disclosure rule or a parameter proposal, they share the
component.** Two editors are two chances to get one of them wrong.

**Two screens never poll a hidden tab.** The dashboard scheduler and the report
generation poll both pause on `document.hidden`. A forgotten background tab is
how a feature becomes the reason someone's production database is slow.

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
`SearchField` / `PageHeader`, `ResultTable`, `Kpi`, `CopyButton`, and the
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
| ≤640px | Page padding tightens and two-column form rows stack rather than crush |

`@media print` is a report feature, not a general one: it forces the light
token set over `applyTheme`'s inline styles, hides the shell, keeps a figure
whole across a page break, opens collapsed disclosures, and gives the article a
definite width. The two halves a stylesheet cannot reach — fonts, and redrawing
each Vega plot at page width in the light palette — are
[`report-print.ts`](../frontend/src/components/report-print.ts).

---

## 7. What is tested, and what is not

Eleven modules are deliberately **DOM-free** and carry their own suites, because
every way they can be wrong is quiet: `dashboard-schedule.ts`,
`table-format.ts`, `dashboard-document.ts`, `palette.ts`, `chat-format.ts`,
`report-document.ts`, `report-readiness.ts`, `report-print.ts`,
`semantic-drift.ts`, `knowledge-template.ts`, `thinking.ts`.

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
| The Knowledge tab, the badge, the copy | [learning-loop-plan.md §4](learning-loop-plan.md) — the one full design brief in the repo |
| The schema browser, catalog descriptions | [catalog-metadata-plan.md](catalog-metadata-plan.md) |
| What a step chip means | [pipeline.md](pipeline.md) — the nodes behind `NODE_META` |
| What may be shown to whom | [security.md](security.md) — the disclosure policy the header badge names |

The file-header docblock of the component you are about to change is generally
more specific than any of these, and is where the reasoning for a layout
decision was recorded at the time it was made. Read it first.
