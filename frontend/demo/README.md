# The DataMind demo build

The real SPA, running entirely in the browser against recorded fixtures: no
backend, no network calls, no API keys. It is built for a static host (GitHub
Pages at `/DataMind/`) so anyone can click through the product.

**Nothing under `src/` knows it exists.** The demo is a separate Vite config
plus this folder; `npm run build` produces exactly the app it always did.

```bash
cd frontend
npx vite build   --config vite.demo.config.ts   # → demo-dist/
npx vite preview --config vite.demo.config.ts   # → http://localhost:4173/DataMind/
npx tsc -p demo/tsconfig.json                   # type-check the demo against the app
```

`DEMO_BASE` overrides the base path (default `/DataMind/`; use `/` to serve it
at a root). `DEMO_SOURCE_URL` overrides the banner's *View source* link. The
deploy workflow runs the build command above from `frontend/` with
`DEMO_BASE=/DataMind/` and publishes `frontend/demo-dist`.

---

## How it works

`vite.demo.config.ts` is `vite.config.ts` merged with a base path, its own
`outDir` (`demo-dist`, never `dist`), `define: { __DEMO__: true }`, and two
plugins.

**`plugins/demo-substitute.ts`: the seam.** A `resolveId` hook
(`enforce: 'pre'`) that redirects three of the app's imports, compared by
*resolved path* so any spelling of the import is caught:

| The app imports | It gets | Why |
|---|---|---|
| `src/api/client.ts` | `mock/client.ts` | every request the SPA makes goes through this one module |
| `react-router-dom` | `shims/react-router-dom.ts` | `createHashRouter` for `createBrowserRouter`: Pages has no SPA fallback, so a deep link has to live after `#` |
| `src/pages/LoginPage.tsx` | `shims/LoginPage.tsx` | the real page, with the demo credentials typed in. The demo opens here; signing in is remembered for the tab, so a reload stays in and a new visit starts at the door |

The client is the narrowest seam that catches every call: nothing else in
`src/` touches `fetch`, `EventSource` or `XMLHttpRequest`. Replacing it rather
than `fetch` keeps the mock typed. Each namespace in `mock/client.ts` is
declared as `typeof Real.<namespace>`, and `mock/surface.ts` checks the export
list, so a method the real client gains is a compile error here, not a blank
panel in production. `ApiError`, the token helpers and `isRunInFlight` are the
real module's own, re-exported. Imports made *from* `demo/` resolve normally,
which is how the mock reaches the real types without looping into itself.

**`plugins/demo-banner.ts`: the notice.** Injected through
`transformIndexHtml`. It adds a strip saying this is sample data with no live
database, plus a source link. It is dismissible and returns on reload. It is
drawn in the app's own tokens, so it follows the theme toggle, and it shortens
the shell's inline `100vh` so nothing sits under it. The same hook adds a small
head script that prefixes the base path onto `<img>` sources naming a file in
`public/`. `Logo` asks for `/brand.png` and the Creators page for
`/team/<name>.png` as plain strings, which a build cannot rewrite.

**The mock.**

- `mock/client.ts` answers every API call. Reads come from fixtures. Writes
  that can be honoured without pretending are kept for the session: asking,
  stopping and retrying a question, renaming or deleting a thread, and *Was
  this right?* (a flag lands in the Knowledge queue). Every other write is
  refused with one sentence saying why (`E_DEMO_READ_ONLY`).
- `mock/stream.ts` turns a recorded answer into the event stream the pipeline
  emits: `STEP_STARTED`/`STEP_FINISHED` with the nodes' own detail sentences,
  `SQL_GENERATED`/`SQL_REJECTED`/`SQL_VALIDATED`, `RESULT_PREVIEW`,
  `TEXT_DELTA`, `RUN_FINISHED`. It plays them on a schedule. A deep analysis
  (`scriptDeep`) is played the way the deep graph runs one — `route`, `plan`,
  then per step `step` → the chat road → `compute`, then `synthesize` and
  `chart` — with `PLAN_PROPOSED`, `PLAN_REVISED`, `STEP_EVIDENCE` and
  `BUDGET_SPENT` where those nodes emit them. *Answer now* is honoured: the step
  in flight finishes, no other starts, and the answer is the one recorded for
  that many steps, under the product's own "built from 3 of 5 planned steps"
  sentence. A timeline is a
  pure function of the answer and `mock/timing.ts`, so the live trail and the
  persisted one agree.
- `mock/content.ts` serves the dashboards, reports and knowledge store: the
  recorded fixtures shaped into the API's types, with ids, owners, privileges
  and timestamps. A tile's `computed_at` is the one stamp on the **visitor's**
  clock rather than the demo's, because the dashboard scheduler compares it
  with `Date.now()`; on the demo's day every tile would look hours overdue.
- `mock/store.ts` holds the session's conversations and runs in
  `sessionStorage`, so a reload on a deep link, or in the middle of a run,
  comes back where it was.
- `mock/clock.ts` is the demo's clock: `DEMO_TODAY` in the afternoon, running
  from the start of the session. Every timestamp lives on it.
- `mock/timing.ts` holds **every delay**. Tune the pacing there.

---

## How the fixtures are made

`mock/fixtures/*.generated.ts` are not written by hand. They are the output
of `scripts/build.py`, which runs every scripted statement for real:

1. `scripts/warehouse.py` loads a sales warehouse into a scratch PostgreSQL.
   The schema comes from the repo's own `backend/fixtures/sales_seed.sql` and
   `sales_comments.sql`. The data is generated. The repo's Sakila fixture
   (`backend/fixtures/mysql/`) loads into a scratch MySQL unchanged.
2. Both schemas are synced through the backend's **real connectors**, under
   each connection's disclosure policy.
3. Every statement goes through the backend's **real guard**. The SQL panel
   shows the guard's own rewrite, and the repair loop's rejection is the
   guard's own message.
4. It is executed through the real connector, with `CURRENT_DATE` frozen at
   `DEMO_TODAY` for execution only.
5. The backend's **result checks** and **chart planner/compiler** run on the
   rows. That includes every alternative type for *Change chart*, the
   template editor's verdicts, and the Sections tab's proposal.
6. The narrative is written from those rows (`scripts/questions.py`) and
   checked: every figure in it must be a cell, a column total, a row count or
   a year. Each narrative asserts the shape it describes, such as "June is the
   outlier", so new data cannot ship a sentence that stopped being true.
7. The deep analysis, the dashboards, the reports and the knowledge store are
   recorded the same way, each checked by the backend code that checks the
   real thing (below).

```bash
frontend/demo/scripts/build-fixtures.sh   # needs Docker and a Python that can import backend/
```

The script starts two throwaway containers and removes them on exit. It
touches neither the compose stack nor `.data/`.

### What is from the repo, and what is invented

| From the repo | Invented for the demo |
|---|---|
| The 41 table and column names, the 64 foreign keys and the DDL comments of the `sales` fixture | Every row of sales data: Lumen Supply Co., its products, customers and staff, and a revenue story (≈2%/month growth, a Nov–Dec peak, a January dip, and one outlier June: a single $246,517 fleet-refresh order from Meridian Health Systems on 16 June) |
| The Sakila schema **and data**, unchanged | The people, teams, service account, audit log and token usage |
| The eight seeded roles, the capability catalog, the privilege meanings and the provider parameter catalog, dumped from backend code | The two connections' hosts and the two model providers |
| The node names, detail sentences, event payloads, guard, checks, chart compiler and section proposal | The per-step durations (`timing.ts`) and token counts (sized from the schema) |
| The tile, report and knowledge checks: `_chart`/`_kpi`, the report worker's `_numeric_check`, `validate_template`, the matcher, the binder and the backlog's own reasons | Who owns and shares each board and report, who taught each template, its hit count, and how often each question was asked this month |

Left out of the `sales` fixture, because they exist to make an eval model
fail and would only read as noise here: the deprecated `product` table, the
`flg_2`/`cust_ref` columns, and the eight padding audit columns. The fixture's
two deliberately false comments (`customers.segment`, `orders.subtotal`) are
replaced with true ones.

### `DEMO_TODAY`

`mock/fixtures/today.ts` is the one date everything is relative to. It is
fixed rather than "now" because the story is tied to the calendar: December
is the holiday peak. To move the demo forward, change it and run
`build-fixtures.sh`. The data, the SQL results and the sentences move
together. A mismatch between the fixtures and the date is reported in the
browser console.

---

## The scripted questions

The composer offers **Quick** and **Deep**. Each mode answers what it was
recorded with: a question recorded only in the other mode gets a short reply
saying which mode to switch to, rather than the other mode's answer under the
wrong label.

| Connection | Question | Shows |
|---|---|---|
| Sales warehouse | How has monthly revenue trended over the last 12 months? | A time series: narrative, table and a line chart that agree |
| Sales warehouse | What tables do I have? | A schema question, answered from the snapshot with no SQL |
| Sales warehouse | What are our top 10 products by revenue this year, with their category and preferred supplier? | Six tables through a bridge, with a filtered `LEFT JOIN` |
| Sakila DVD rental | Which film categories bring in the most rental revenue, and how long are they kept? | MySQL (`DATEDIFF`), and an AGGREGATE policy: the model never sees a value, so the prose names none |
| Sales warehouse | Break down revenue by customer region for the last 12 months | The first draft fails the guard (`E_UNKNOWN_COLUMN`) and the repair loop fixes it |
| Sales warehouse | فروش هر دسته‌بندی محصول در سه ماه گذشته چقدر بوده است؟ | Persian, answered right to left |

The product's four starter chips are answered on both connections, as are the
questions behind the four conversations already in the sidebar. After each
answer, the follow-up chips offer the next scripted questions. Anything else
gets the demo's own reply: no steps, no badge, a list of what it can answer,
and the chips.

### The deep analysis

| Connection | Question | Shows |
|---|---|---|
| Sales warehouse | Why was June revenue so much higher than the months around it? | A five-step plan (a query, *What drove it*, *What stands out*, two more queries), two steps sharpened once the steps they depend on have answered, and an answer whose every sentence cites its step. It traces June's jump to one order: Meridian Health Systems' $246,517 on 16 June |

It is recorded like everything else, by `run_deep` in `scripts/build.py`: each
step's statement goes through the real guard and connector and is closed by the
deep pipeline's own `evidence.close_step`, so the plan panel shows the backend's
own computed summaries. The answer is written for **every prefix** of the plan,
because *Answer now* can stop it after any step, and each version is checked by
`reports.checks.check_claims` against the steps it cites: the build fails on a
sentence without a citation or a figure its step does not hold. A finished run
of it is already in the sidebar.

Sales runs under **SAMPLE** (the model saw the rows, so the prose quotes
them). Sakila runs under **AGGREGATE**, so the header's badge differs between
the two, and so does what an answer is allowed to say.

### Adding a question

1. Add a `Question` to `scripts/questions.py` with its connection, the SQL a
   model would write, the chart the model would propose, and a narrative
   function of the result. Add it to another question's `followups` so a chip
   offers it.
2. Run `scripts/build-fixtures.sh`. The build fails if the guard rejects the
   SQL, a result check would retry it, or the narrative quotes a figure the
   rows do not support.
3. Rebuild the demo. The question now matches exactly as written, or as any
   of its `aliases`, compared case- and punctuation-insensitively.

---

## Dashboards, reports and the knowledge store

Recorded by `scripts/build.py` from `scripts/demo_dashboards.py`,
`demo_reports.py` and `demo_knowledge.py`.

**Two dashboards.** *Commercial overview* (Sales warehouse, the demo person's
own): three KPIs with their month-on-month move and sparkline, revenue over
two years, channel and region mix, category by segment, top products and
customers, returns, carrier volume against speed, and stock at its reorder
level, in sections with a line of prose each. *Rental operations* (Sakila,
shared read-only by Priya Nair). Each tile is run the way a refresh runs it
and planned by the dashboard service's own `_chart` / `_kpi`; the build fails
on a tile that is rejected, returns nothing, or cannot draw what it is. Several
layout choices exist because of what the planner does with a result, and are
commented where they are made: three wide KPIs rather than four narrow ones, a
month as a real date wherever a line should run along it, and no chart of a
measure that is flat across its categories.

**One report**, over the Sales warehouse because reports refuse Sakila's
AGGREGATE policy: *Monthly business review — August 2026*, the demo person's
own, so it opens on its outline and *Last run* opens the document. Every block
runs like a tile. Every paragraph goes through `parse_claims` and the report worker's own
`_narration` and `_numeric_check`, so a sentence citing figure 2 may only state
what figure 2's writer was given, and the build fails on an uncited sentence or
an unsupported figure. The executive summary follows the shape the summary
prompt asks for and is checked against the sections' prose, which is all its
writer sees. That check is strict: a month-on-month percentage that appears
only on a KPI's delta is flagged, so the prose does not quote one.

**The knowledge store.** Six saved questions on Sales and four on Sakila, each
passed through `validate_template` against the synced snapshot, with its
tables as the guard reports them. Two chat questions are **answered from the
store**: *Monthly revenue for Europe* and, on Sakila, *Films in the Comedy
category*. Each is the matcher's best candidate over the short-circuit
threshold, with its slot bound by the real binder, so the run skips five nodes,
goes straight to the guard and carries the Verified badge. Every other
recorded question's `match` step reports its real best score against the store,
and the build fails if one would in fact have matched. The *Suggested* tab is
the backlog's own ranking and wording: BACKFILL from the two board tiles
recorded as corrected by hand, FAILED for the question whose recorded run needed
repairing, TRAFFIC for the questions the sidebar asked, and a word nothing in
the schema knows, found by `unknown_words`.

## What the demo does and does not show

It shows what is built, and nothing that is not. Where it departs from a
default installation, or leaves something empty, it says so rather than
dressing it up:

- **The semantic layer is not part of the demo.** Its tab is hidden the way
  the product hides it from a reader without a grant on the layer
  (`SHOW_SEMANTIC_TAB` in `mock/client.ts`). Every answer says *Generated
  against the bare schema*, which is true.
- **Deep analysis is on here and shipped off in the product**
  (`docs/status.md` §3). The demo turns the installation switch on so the
  composer offers it; what it plays is a recorded run, not a claim that the
  mode ships.
- **Nothing is saved.** Boards, reports and templates can be opened, filtered
  and redrawn, and a figure's *Change chart* answers as it does in chat, but
  every edit, share, generation and save is refused with a sentence saying so.
  A forced dashboard refresh returns the same recorded rows, computed now.

No write reaches anything. Connections cannot be tested or re-synced, because
there is no database behind them. Providers are never called. Keys are never
in the browser, only `has_api_key`.

### Known rough edges (in the app, not the demo)

- After a reload on `/chat/<id>`, the header's database and model pickers
  show *Choose a database*, and a follow-up is refused until another thread
  is opened and this one reopened. `ChatPage` fills the pickers from the
  conversation list in an effect that runs before that list has loaded. The
  real app does the same; hash routing only makes the reload easier to reach.
- Editing the URL by hand logs a react-router warning: a blocker cannot
  intercept a hash change it did not make. Navigating inside the app does
  not.
