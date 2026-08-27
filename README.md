<p align="center">
  <img src="frontend/public/brand.png" alt="DataMind" width="140" height="140">
</p>

<h1 align="center">DataMind</h1>

<p align="center">
  <strong>Business intelligence for the people who have the questions, not the SQL.</strong><br>
  Ask in plain language — get a written answer, a table, a chart, or a whole report.<br>
  Every query is checked before it runs, and you decide how much of your data ever reaches the model.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/LangGraph-1C3C3C" alt="LangGraph">
  <img src="https://img.shields.io/badge/LiteLLM-4B5563" alt="LiteLLM">
  <img src="https://img.shields.io/badge/SQLGlot-334155" alt="SQLGlot">
  <img src="https://img.shields.io/badge/SQLAlchemy%202.0-D71F00?logo=sqlalchemy&logoColor=white" alt="SQLAlchemy 2.0">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black" alt="React 18">
  <img src="https://img.shields.io/badge/TypeScript-5.6-3178C6?logo=typescript&logoColor=white" alt="TypeScript 5.6">
  <img src="https://img.shields.io/badge/Vite-5.4-646CFF?logo=vite&logoColor=white" alt="Vite 5.4">
  <img src="https://img.shields.io/badge/Vega--Lite-E8912D" alt="Vega-Lite">
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" alt="Docker">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue" alt="MIT License"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-works-today">Features</a> ·
  <a href="#two-things-are-never-left-to-the-model">Security</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="docs/README.md">Docs</a>
</p>

---

The people who most need an answer from the database are usually the ones who
cannot write the query for it — no joins to work out, no dialect to learn, no
analyst to wait three days for. DataMind takes the question as you'd say it out
loud and answers it. The generated SQL is shown every time, so whoever *can*
read it is able to check it.

Point it at **PostgreSQL, MySQL, SQL Server, or Oracle**; the question is the
same, the dialect is the connector's problem.

**Three ways to use the same guarded query path:**

- **Chat** is a conversation — ask as many questions as you want, and a
  follow-up carrying no subject of its own (*"and by month?"*) is understood
  from the turns before it. Each answer is about right now.
- **Dashboards** watch numbers that are always current — a grid of saved
  queries, each on its own connection and its own refresh rate.
- **Reports** are a document. Describe what you need, approve the outline a
  model proposes, and get prose, tables and charts written section by section
  over your own data — with every headline figure computed from the rows rather
  than by a model. Printable, and re-runnable months later against fresh data.


## Two things are never left to the model

A tool that writes SQL against your production database and posts your business
data to a model provider has exactly two ways to hurt you. Neither is handled by
asking the model nicely.

- **Every statement is checked before it runs.** The model only *proposes*
  SQL — it never executes anything. Each statement is parsed into an AST and
  walked against an allowlist, with every table and column resolved against the
  connection's real schema; an unrecognised construct is a **rejection, not a
  warning**, so the failure mode is a refusal rather than a bypass. Underneath
  that, execution is read-only in the database's own terms, with a row cap and a
  statement timeout. Every way into that guard — a chat question, a dashboard
  tile, a report block, an imported dashboard — is treated identically; none is
  privileged, and a hostile-query corpus is replayed through each on every build.
- **You decide what leaves your database.** Each connection declares how much of
  a query result may reach the model provider — `NONE`, `AGGREGATE`, `SAMPLE`,
  or `FULL` — and the policy in force is shown in the chat header **at the
  moment you ask**, not buried in a settings page. It governs all three channels
  a value could escape through: the results, the per-column hints in the schema
  block, and the conversation history. Credentials are separate: encrypted with
  AES-256-GCM and never returned by any endpoint, in any form.

The full argument for both — every point where data reaches a provider and
exactly what each one sends, the disclosure ladder, the guard's rejection codes,
and a pre-production checklist — is in
[**docs/security.md**](docs/security.md).

---

## Quick start

You need Docker and Docker Compose.

```bash
git clone https://github.com/maziyar375/DataMind.git
cd DataMind

make secrets           # copies .env.example to .env, then writes a fresh AES key and JWT secret
docker compose up -d   # starts db, the three demo databases, api, and web in the background
```

Then open <http://localhost:5173> and sign in with the bootstrap admin. **The
credentials are whatever `ADMIN_EMAIL` / `ADMIN_PASSWORD` say in your `.env`** —
`make secrets` seeds them from `.env.example` as `admin@raymand.com` /
`raymand`. (The API's own fallback, used only when `.env` sets nothing, is
`admin@raymand.local`; read your `.env` rather than trusting either address
here.) Change `ADMIN_PASSWORD` before doing anything real — the API logs a loud
warning if you don't.

### The first five minutes

A fresh install knows nothing about your data and has no model to think with, so
there are four things to set up before the first question. In order:

**1. Add a model provider.** *LLM providers* → add one, with its API key and
model name, and **Test** it before saving — the probe is a real capability
check, not a ping. Anything that reads a question in plain language needs this:
chat, the outline of a report, a tile you'd rather describe than write.

**2. Add a database.** *Data sources* → pick the engine, fill in the address and
a **read-only** account. **Test** it — before or after saving — and you should
see **read-only role confirmed**; the connector proves the account cannot write
by attempting a write inside a transaction it rolls back.

**Three demo databases ship with the stack**, so you can exercise the app
against two engines without pointing it at anything real. They are not
interchangeable — each was built for a different job:

| Field                | `aurora` — **start here** | `sales` — the eval fixture | `sakila` — the MySQL one |
| -------------------- | ------------------------- | -------------------------- | ------------------------ |
| Engine               | `PostgreSQL`              | `PostgreSQL`               | `MySQL`                  |
| Host                 | `aurora`                  | `sales`                    | `sakila`                 |
| Port                 | `5432`                    | `5432`                     | `3306`                   |
| Database             | `aurora`                  | `sales`                    | `sakila`                 |
| User                 | `analytics_ro`            | `analytics_ro`             | `analytics_ro`           |
| Password             | `analytics_ro`            | `analytics_ro`             | `analytics_ro`           |
| *(from your host)*   | `localhost:5434`          | `localhost:5433`           | `localhost:3307`         |

Fill the form with the first six rows. **The last row is not for the form** —
it is where the same databases sit on your own machine, if you want to open one
in a SQL client.

- **`aurora`** is a specialty coffee chain over 24 months of trading — 13 clean
  tables, one obvious join path per question, no legacy columns and no
  soft-delete trap to remember. Its row counts are **tuned to the chart budgets**
  in `app/charts/`, so the obvious question produces the chart you wanted rather
  than one the platform has to trim: six product categories against a six-slice
  pie ceiling, four channels and four loyalty tiers inside the eight-series
  colour limit, eighteen stores against a twenty-five-mark bar. Its tables and
  columns carry `COMMENT ON` descriptions, which reach the prompt. **This is the
  one to point a first connection at.**
- **`sales`** is the *eval* fixture and is messy **on purpose** — 42 tables with
  near-duplicate names, legacy cruft columns, soft-delete traps and a stale
  rollup that gives wrong answers, wide enough that table retrieval is genuinely
  exercised. That makes it the right thing to measure against and the wrong
  thing to demo against.
- **`sakila`** is the classic MySQL sample: 16 tables, ~46k rows of films,
  actors, rentals and payments — there so the app can be driven against a
  second engine.

Those are the addresses **on the compose network** — the API dials them, not
your browser, which is why `localhost` would be wrong in the form.

**SQL Server and Oracle are supported all the same**, and their connectors are
tested — the stack simply ships no demo server for them (they cost ~2 GB of RAM
each and were removed), so point a data source at one of your own. Their seeds
are still in `backend/fixtures/`. Running the two processes without Docker is in
[docs/CODEBASE.md](docs/CODEBASE.md) §7.

**3. Sync the schema.** **Sync schema** on the connection reads its tables,
columns, and primary and foreign keys into a stored snapshot. This step is not
optional and not a convenience: every generated statement is resolved against
that snapshot, so **a connection that has never been synced can be queried for
nothing at all**. Re-sync whenever the database changes shape.

**4. Generate the semantic layer.** *Data sources → Semantic layer →* **Generate
with AI**. The snapshot says what *exists*; the layer says what it **means** —
the business name and grain of each table, metrics bound to exact SQL, what
"last month" means here, which rows shouldn't count unless asked for. It runs a
model call per table and takes minutes, so it is queued and polled rather than
streamed, and you can edit anything it writes.

This one is genuinely optional — with no layer the product behaves exactly as it
did before the feature existed — but it is the step that moves answers from
plausible to right, because the failures it addresses are interpretation, not
retrieval. Skip it on the demo if you're only looking around.

Then **Chat**, and ask something like *"Revenue by store, last 12 months"* or
*"What's the category mix?"* (aurora), *"What was total revenue last month?"*
(sales), or *"Which film category earns the most?"* (Sakila). Dashboards and
Reports draw on the same connection once it is synced.

There is also a script that builds a whole demo board for you — 29 tiles over
the `sales` fixture, covering every chart family the platform draws — by talking
to the running API, so every tile faces the guard exactly like one you typed:

```bash
python scripts/seed_demo_dashboard.py            # create it
python scripts/seed_demo_dashboard.py --check    # and run every tile
```

### Running on a remote host

Lightning.ai, Codespaces, Gitpod, or any VM behind a tunnel all work, but two
things differ from a laptop and both are already configured:

- **Vite host checking.** Vite 5.4.12+ rejects requests whose `Host` header it
  does not recognise, which is every proxied dev domain — you get
  *"Blocked request. This host is not allowed."* `server.allowedHosts` is set
  to `true` in `vite.config.ts` for this reason. It is a dev-server
  convenience; do not expose that config publicly.
- **API address.** The browser is not on the same machine as the API, so an
  absolute `http://localhost:8000` would resolve to *your own laptop*. The SPA
  therefore calls the same-origin path `/api/v1`, and Vite forwards it
  server-side to `api:8000` over the compose network.

Only port **5173** needs to be exposed through your platform's port viewer.
The API is reached through it.

If hot reload does not fire on your host's bind mounts, start with
`VITE_POLL=1 docker compose up`.

---

## What works today

### Chat

<p align="center">
  <img src="docs/screenshots/chat.png" alt="Chat: a question in plain language answered with prose, the generated SQL, a result table and a chart" width="900">
</p>

Ask a question, watch the run happen — five to sixty seconds, four model calls
in the usual case and a fifth if there is a chart to plan. The written answer is
two or three sentences, because the table and the chart beside it are the rest
of it. The thread is pinned to one database and one model, picked before the
first message. The pipeline is ten nodes with one bounded repair loop:

```
route → retrieve → describe → clarify → generate → validate → execute →
inspect → present → chart
```

- **`route`** reads the recent turns, so a follow-up with no subject of its own
  ("and by month?") is understood as one.
- **`describe`** answers questions about the database itself — "what tables do
  I have?", "what does `order_items` count?", "which of these holds revenue?" —
  from the schema **and the semantic layer**, and **halts before any SQL**: no
  query is written, none is run. Every other question skips it entirely.
- **`clarify`** is the one node that can end a run by *asking*, and it **fails
  open**: a provider error proceeds to `generate`, because a guessed answer
  shown with its SQL beats no answer. It asks at most once per exchange.
- **`inspect`** covers the third failure mode — the query ran and the answer is
  wrong. Its checks read SQL, snapshot and result *shape*, never a result
  value, so they cost no tokens and behave identically under every disclosure
  policy.
- **`chart`** is fail-open, the opposite of the SQL guard. The model proposes a
  constrained `ChartIntent` compiled to Vega-Lite, but the backend profiles the
  result first and owns the decision: it vetoes charts the data cannot support,
  repairs salvageable ones (pie → bar past six slices, swapped axes), and caps
  category counts. Any failure just yields no chart.

Each step persists and streams over SSE, so the UI shows a live step trail
rather than a spinner. Replay from `Last-Event-ID`, with a polling fallback.

Once the answer is there you can **redraw its chart as a different type** from a
picker beside it. That costs no model call and — importantly — **re-runs no
query**: it draws the rows the run already returned, because the rows a chart is
drawn from must be the rows the prose above it was written from. The picker only
offers types the data can actually carry, and a pick the planner refuses comes
back with its reason rather than being compiled into something misleading.

Answered turns also suggest follow-up questions, scoped to the tables the thread
has been asking about.

Node-by-node reference: **[docs/pipeline.md](docs/pipeline.md)**.
Chart decisions: **[docs/charts.md](docs/charts.md)**.

### Dashboards

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard: a grid of tiles — charts, tables and big numbers — each on its own connection and refresh rate" width="900">
</p>

A grid of tiles, each a saved query bound to **its own connection** and **its
own refresh rate**, drawn as a chart, a table, a big number, or plain text.
A tile's SQL is written either by asking in plain language (you get an editable
draft back) or by typing it yourself — neither path is privileged, and someone
with no LLM provider configured can still build a whole dashboard.

The interesting part is that this is a **second entry point into guarded
execution**, and it gets no exemption: the guard runs at preview, at save, and
at every single refresh, re-validating stored SQL against the connection's
*current* snapshot rather than trusting that it passed when it was saved — a
re-sync that dropped a table fails the tile closed instead of returning an empty
result that looks like "no data". Ownership is re-checked at execution, and a
tile may only *lower* the connection's row cap and timeout, never raise them. A
broken tile is a value, not an exception — it never fails the dashboard.

**Nothing calls a model at refresh time**, which is the most load-bearing "no"
in the product: two calls when a tile is drafted (its SQL, then its chart), zero
per refresh, forever. A dashboard keeps working after the provider key is
revoked.

A dashboard also **exports to a file and imports from one**, so a board built
against one database can be rebuilt against another. The file holds the layout
and the SQL and nothing else: no ids, no results, and nothing from inside a
connection — you pick which of *your* data sources each of its databases is on
the way in, and every statement in it faces the guard exactly like one you
typed. **[docs/dashboards.md](docs/dashboards.md)**.

### Reports

<p align="center">
  <img src="docs/screenshots/report.png" alt="Report: a generated document with headings, prose written over real results, tables and numbered figures" width="900">
</p>

Chat answers now and a dashboard is always current; **a report is a document** —
a structure a human approved, prose written over real results, and a snapshot of
a moment that stays readable after the data has moved on. It shares no table and
no code path with Dashboards; deleting one would leave the other working.

Six steps. **Create**: pick the connection (pinned for the life of the report),
a model (changeable whenever), and how many sections to ask for — 2 to 8, a
starting point rather than a contract. **Outline**: one model call proposes the
headings and the questions under them, and you edit, reorder, add, or remove.
**Check**: each question becomes SQL and is run against the real schema, coming
back feasible, feasible-but-empty, or infeasible with the guard's own reason —
or you write the statement yourself, which is not a lesser path. **Generate**:
sections appear as they finish, and a failed one is retried alone rather than
costing the run. **Refine**: any paragraph, any chart. **Keep**: print to PDF,
and re-run it months later against fresh data.

The approval gate is the thing nothing else in the product has — no model call
is spent writing a document until a person has approved its plan. The rest are
guarantees about the finished text. Time windows resolve in the SQL itself
(`CURRENT_DATE - INTERVAL '3 months'`), so a re-run in six months describes
*then* rather than *now*. No model is asked to do arithmetic: the headline
numbers and the figures a paragraph needs are computed exactly from the rows,
and a separate pure check flags any figure the rows do not support. Runs are
kept, and a regeneration never overwrites one — your edits and the model's prose
live in separate columns. Persian and English, with the language read off the
request rather than asked for, so a Persian request cannot produce an English
document. **[docs/reports.md](docs/reports.md)**.

### The semantic layer

The schema snapshot says what *exists*; the semantic layer says what it
**means** — the business name and grain of each table ("one row per line
item"), metrics bound to exact SQL, time conventions (fiscal year, week start,
whether "last month" is calendar or rolling), the rows that shouldn't count
unless asked for, a glossary, and per-join fan-out cautions. One editable
document per connection.

It can be generated from the schema (one model call per table, joins derived
from the catalog rather than guessed), and **nothing unchecked is kept**:
generated names are resolved against the snapshot and metric SQL is parsed, so
an invalid generated metric is dropped. An invalid *hand-written* one is
flagged and kept — deleting someone's work to hide drift is worse than showing
it. Regeneration preserves every field a human edited. It widens no disclosure:
generation reads the same schema block a run reads, under the same budget.

### Platform

- Email + password auth: Argon2id, short-lived JWT access tokens, rotating
  refresh tokens in an HttpOnly cookie, with reuse detection
- User management: invite with a one-time password, edit a user's name, email,
  and role (including promote/demote admin), delete a user, and an admin
  **set-password** that revokes the user's live sessions
- AES-256-GCM credential encryption, bound to the owning row so a ciphertext
  copied between rows fails to decrypt
- Four target-database connectors — **PostgreSQL, MySQL, SQL Server, and
  Oracle** — behind one port, each with genuine read-only verification and
  schema introspection including primary and foreign keys
- Connection testing that works **before a connection is saved** as well as
  after, so credentials can be checked without persisting a broken row
- Model configuration CRUD with a real capability probe, likewise testable
  before saving. Two provider kinds are creatable — **OpenAI-compatible** (which
  covers OpenAI itself and anything speaking its API: OpenRouter, Ollama, vLLM,
  a local gateway) and **Anthropic**
- In-process run executor with heartbeats and a stale-run reconciler, so no run
  is left stuck when a process dies
- An offline eval harness with a nightly CI run — **[docs/eval.md](docs/eval.md)**

### Frontend

Chat with the live step trail, the "Generated SQL" panel, result tables and
charts, a chart-type picker that redraws an answer's chart without re-querying,
and metadata chips. Each conversation is pinned to one database and model,
chosen in the header — which also shows the disclosure policy in force — and
locked once the first message is sent. Copy buttons, conversation rename and
delete, suggested follow-up questions, and right-to-left support for Persian and
other RTL scripts.

Data sources with an engine picker, a table list, an FK graph view, and the
semantic-layer editor (metric expressions validated live by the *same parser*
the save path uses, so the editor never promises something the backend will
reject). LLM providers. A user-management panel with per-user editing.
Dashboards with drag-and-resize tiles, per-tile refresh, and import/export of a
whole board as a file. Reports get three
screens of their own: an outline editor that walks Describe → Structure → Check
→ Generate with the guard's verdict on every question, a viewer that watches
the document write itself and lets any paragraph or chart be refined
afterwards, and the run history a regeneration adds to. An About page, the one
screen reachable from both sides of the sign-in wall. Dark and light themes,
built on a custom design system of oklch CSS variables — there is **no component
library**.

**Not built yet:** rolling conversation summaries, sharing a dashboard or a
report with another user, and scheduled report generation. Each is deferred
deliberately — see [docs/architecture.md](docs/architecture.md) for the
reasoning.

---

## Architecture

```
api        →  HTTP shape only. Auth extraction, DTO validation, no business logic.
services   →  Use cases. Transaction boundaries. Authorization decisions.
pipeline   →  The AI run: typed state, nodes, executor.
reports    →  Outline, facts, narration, the numeric check. Pure.
semantic   →  The document, its generator, its render. Pure.
domain     →  Entities, value objects, Protocols (ports). Zero I/O.
infra      →  Adapters implementing the Protocols.
```

The dependency rule —
`api → services → pipeline → reports → semantic → domain ← infra` — is enforced
by `import-linter` in CI rather than by convention. It buys concrete things:
`reports/narrate.py` *cannot* call `disclose()`, because that lives in the
pipeline above it, so the worker must disclose results under the policy in
force at narration time and hand them down.

Ports and adapters exist at exactly four places, because these are the four
things most likely to be replaced: **LLM**, **target database**, **secrets**,
and **run execution**.

Those seven names are directories. Four more sit outside the ladder because
they depend on nothing in it: `sqlguard/` (the parser and the allowlist),
`charts/` (profile → fit → Vega-Lite), `workers/` (the run executor, the
reconciler and the report graph) and `eval/`, which one of the seven
import-linter contracts keeps off the request path entirely. For the tree —
what is in each one, down to the module — see
[docs/CODEBASE.md](docs/CODEBASE.md) §3, or the code map in
[CLAUDE.md](CLAUDE.md) if you are about to change something.

**LangGraph was deferred, and has since been adopted.** The bet the
architecture doc made — that nodes built LangGraph-shaped from the start would
make adoption a wiring change rather than a rewrite — paid: the chat pipeline
and the report worker are compiled graphs, the repair region is one subgraph
with two callers, and the ten node functions were not modified. Two phases were
then argued and **declined on measurement**: checkpointing (88 KB of state per
node, 97% of it the schema block, for a run of 5–60 seconds) and durable
clarification. [docs/langgraph-migration.md](docs/langgraph-migration.md) has
both arguments and the gates.

### Still deferred on purpose

| Deferred | Why | Trigger to revisit |
| --- | --- | --- |
| Celery + Redis | A run is 5–60 seconds. Durability comes from the `runs` table plus a heartbeat; Celery would add a deployment unit and make SSE fan-out harder. | p95 run > ~5 min, or runs must survive rolling deploys |
| Sharing a dashboard or report | User B would read data pulled with user A's credentials, against a connection B does not own. That is an authorization model, not a UI feature. | There is a real answer for "who may read through this connection" |
| Dashboard filters | `QueryExecutor.execute` takes no bind parameters. Filters need the port extended across all four connectors — **never** by string interpolation. | Someone extends the port |
| Rolling conversation summaries, scheduled report generation | Neither is load-bearing yet; the history tail and a manual re-run cover the cases. | A thread outgrows the last-six-messages window |

More than one API replica *is* supported — see
[docs/cross-replica.md](docs/cross-replica.md) for the three rules that make it
work.

LiteLLM is kept, but strictly behind `LLMGateway`, and LangGraph is confined to
`app/pipeline/` and `app/workers/`. CI greps to prove both:

```bash
grep -rn "import litellm" app/ | grep -v infra/llm/                      # must be empty
grep -rnE "^\s*(import|from)\s+(langgraph|langchain_core)\b" app/ \
  | grep -vE "^app/(pipeline|workers)/"                                  # must be empty
```

Those two lines decide whether the abstractions are real or decorative.

---

## Testing

```bash
make test      # full backend suite — 1,426 tests, ~3 minutes
make guard     # the hostile SQL corpus alone — the hard gate
make lint      # ruff + import-linter contracts
make fmt       # ruff format
make fixtures  # rebuild and verify the sales fixtures from clean
```

From `frontend/`:

```bash
npm run typecheck   # tsc --noEmit
npm run build       # tsc -b && vite build
npm test            # the nine DOM-free logic modules: schedule, format,
                    # dashboard document, palette, chat format, report
                    # document, report readiness, print, semantic drift
```

Typecheck plus build is the real frontend gate. There is a `npm run lint`
script, but **eslint is not a declared devDependency and the repo carries no
eslint config**, so it fails wherever you run it; CI does not run it either.

CI (`.github/workflows/ci.yml`) runs ruff, the import-linter contracts, both
boundary greps, the hostile corpus and the full backend suite, plus `tsc
--noEmit` and `vite build` on the frontend. Two things it does **not** gate:
`npm test`, and mypy — which runs with `|| true` while strict mode is adopted
module by module. Worth knowing before trusting a green tick.

The offline eval harness is separate and costs real tokens — see
[docs/eval.md](docs/eval.md).

---

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `SECRET_BOX_KEY` | 32-byte urlsafe-base64 key for credential encryption | — (`make secrets`) |
| `JWT_SECRET` | Access-token signing secret | — (`make secrets`) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Bootstrap admin, applied idempotently at startup | `admin@raymand.com` / `raymand` from `.env.example` |
| `DATABASE_URL` | Application database | the compose `db` service |
| `CORS_ORIGINS` | JSON array of allowed browser origins | `["http://localhost:5173"]` |
| `MAX_CONCURRENT_RUNS` | Executor concurrency limit | `8` |
| `RUN_DEADLINE_SECONDS` | Hard per-run time budget | `120` in code, raised to `300` by compose |
| `LLM_REQUEST_TIMEOUT_SECONDS` | Per-provider-call timeout | `60` in code, raised to `120` by compose |
| `VITE_POLL` | Set to `1` if hot reload misses your bind mounts | `0` |

The two compose overrides are headroom for slow hosted models: a chat run makes
four or five sequential provider calls, and against a flash model over a hosted
gateway the whole run can land at 45–140s, overrunning the code default and
failing questions that were about to succeed.

`make secrets` generates the two cryptographic values for you. Losing
`SECRET_BOX_KEY` means every stored credential must be re-entered.

> **Naming note:** the product is *DataMind*, but the Python package is still
> `raymand` (import `app.*`), as are the compose project and the app database.
> Renaming is a deliberate separate task. The bootstrap admin address is caught
> in the middle of it and is **inconsistent in the repo today**: the API and
> `docker-compose.yml` both fall back to `admin@raymand.local`, while
> `.env.example` — which `make secrets` copies to `.env`, and which compose then
> reads in preference to its own fallback — sets `admin@raymand.com`. Follow the
> quick start and you get the `.com` address. Check your `.env`.

If the app database ever fails to start with *"could not open directory
'pg_notify'"* — the Lightning Studio drive drops empty directories across
restarts — run `make db-repair`. It recreates the empty runtime scaffolding and
never touches data.

---

## Documentation

Fifteen documents. [docs/README.md](docs/README.md) is the index and classifies
all of them; this is the short version.

| Doc | Read it when |
| --- | --- |
| [CLAUDE.md](CLAUDE.md) | You are about to change code — the map, the invariants, the gotchas |
| [docs/CODEBASE.md](docs/CODEBASE.md) | You want a code-grounded tour of the whole stack |
| [docs/architecture.md](docs/architecture.md) | You want the *why*, including what was deferred and on what trigger |
| [docs/security.md](docs/security.md) | You are touching `sqlguard/`, disclosure, or adding an LLM call site |
| [docs/pipeline.md](docs/pipeline.md) | You are changing a node — and §0 maps all three pipelines |
| [docs/pipeline-dashboard.md](docs/pipeline-dashboard.md) · [docs/pipeline-report.md](docs/pipeline-report.md) | You are changing how a tile or a report gets its SQL |
| [docs/charts.md](docs/charts.md) | You are changing what gets drawn |
| [docs/dashboards.md](docs/dashboards.md) | You are changing tiles or saved-SQL execution |
| [docs/reports.md](docs/reports.md) | You are changing the document |
| [docs/eval.md](docs/eval.md) | You want to measure whether a change helped |
| [docs/cross-replica.md](docs/cross-replica.md) | You are running more than one API process |
| [docs/langgraph-migration.md](docs/langgraph-migration.md) · [docs/catalog-metadata-plan.md](docs/catalog-metadata-plan.md) · [docs/reports-plan.md](docs/reports-plan.md) | Plans and records — the narrative of a piece of work, with a dated ledger of what changed while it was executed |

---

## License

MIT
