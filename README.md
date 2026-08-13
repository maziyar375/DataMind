# DataMind

Conversational BI. Ask a question in plain language, get a written answer, a
table, and a chart — with the generated SQL visible and auditable. Point it at
**PostgreSQL, MySQL, SQL Server, or Oracle**; the question is the same, the
dialect is the connector's problem.

Three ways to use the same guarded query path:

- **Chat** answers one question and moves on.
- **Dashboards** watch numbers that are always current.
- **Reports** are a document — a structure you approved, prose written over
  real results, printable, and re-runnable months later against fresh data.

A single modular-monolith FastAPI application backed by one PostgreSQL
database, plus a React SPA. No microservices, no message broker, no vector
database in this release.

---

## Quick start

You need Docker and Docker Compose.

```bash
git clone https://github.com/<you>/datamind.git
cd datamind

make secrets      # writes .env with a fresh AES key and JWT secret
make up           # builds and starts db, both demo databases, api, and web
```

Then open <http://localhost:5173> and sign in with the bootstrap admin
(`admin@raymand.local` / `raymand` by default — change `ADMIN_PASSWORD` in
`.env` before doing anything real; the API logs a loud warning if you don't).

Two demo databases ship with the stack, so you can exercise the app against
two engines. Add a data source pointing at either fixture — a **PostgreSQL**
sales model (42 tables: orders, order_items, payments, shipments, returns,
inventory, employees…, deliberately messy and wide enough that table retrieval
is genuinely exercised) or the classic **MySQL** "Sakila" sample (16 tables,
~46k rows of films, actors, rentals, payments):

| Field    | PostgreSQL demo | MySQL demo     |
| -------- | --------------- | -------------- |
| Engine   | `PostgreSQL`    | `MySQL`        |
| Host     | `sales`         | `sakila`       |
| Port     | `5432`          | `3306`         |
| Database | `sales`         | `sakila`       |
| User     | `analytics_ro`  | `analytics_ro` |
| Password | `analytics_ro`  | `analytics_ro` |

Those are the addresses **on the compose network** — the API dials them, not
your browser. From the host the same databases are on ports `5433` and `3307`.

**Test** it — before or after saving — and you should see **read-only role
confirmed**. Then sync the schema and ask something like *"What was total
revenue last month?"* (sales) or *"Which film category earns the most?"*
(Sakila). SQL Server and Oracle connections are configured the same way, only
the engine and port differ.

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

### Without Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload

cd ../frontend
npm install && npm run dev
```

---

## What works today

### Chat

Ask a question, watch the run happen. The pipeline is ten nodes with one
bounded repair loop:

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

Node-by-node reference: **[docs/pipeline.md](docs/pipeline.md)**.
Chart decisions: **[docs/charts.md](docs/charts.md)**.

### Dashboards

A grid of tiles, each a saved query bound to **its own connection** and **its
own refresh rate**, drawn as a chart, a table, a big number, or plain text.
A tile's SQL is written either by asking in plain language (you get an editable
draft back) or by typing it yourself — neither path is privileged, and someone
with no LLM provider configured can still build a whole dashboard.

The interesting part is that this is a **second entry point into guarded
execution**, and it gets no exemption: stored SQL is re-validated against the
connection's current snapshot on every single refresh, ownership is re-checked
at execution, and a tile may only *lower* the connection's row cap and timeout,
never raise them. A broken tile is a value, not an exception — it never fails
the dashboard.

A dashboard also **exports to a file and imports from one**, so a board built
against one database can be rebuilt against another. The file holds the layout
and the SQL and nothing else: no ids, no results, and nothing from inside a
connection — you pick which of *your* data sources each of its databases is on
the way in, and every statement in it faces the guard exactly like one you
typed. **[docs/dashboards.md](docs/dashboards.md)**.

### Reports

Describe what you need, approve the outline a model proposes, and get prose,
tables and charts generated section by section from your own data. Each
question becomes one guarded query, written by the model or by hand. Time
windows resolve in the SQL itself (`CURRENT_DATE - INTERVAL '3 months'`), so
the same report re-run in six months describes *then* rather than *now*.

No model is asked to do arithmetic: the headline numbers and the figures a
paragraph needs are computed exactly from the rows, and a separate pure check
flags any figure the rows do not support. Runs are kept, so a document stays
readable after the data has moved on, and a regeneration never overwrites one —
your edits and the model's prose live in separate columns. Persian and English,
with the language derived from the request rather than asked for. Prints to PDF
from the browser. **[docs/reports.md](docs/reports.md)**.

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
  and role (including promote/demote admin), and an admin **set-password** that
  revokes the user's live sessions
- AES-256-GCM credential encryption, bound to the owning row so a ciphertext
  copied between rows fails to decrypt
- Four target-database connectors — **PostgreSQL, MySQL, SQL Server, and
  Oracle** — behind one port, each with genuine read-only verification and
  schema introspection including primary and foreign keys
- Connection testing that works **before a connection is saved** as well as
  after, so credentials can be checked without persisting a broken row
- Model configuration CRUD with a real capability probe, likewise testable
  before saving
- In-process run executor with heartbeats and a stale-run reconciler, so no run
  is left stuck when a process dies
- An offline eval harness with a nightly CI run — **[docs/eval.md](docs/eval.md)**

### Frontend

Chat with the live step trail, the "Generated SQL" panel, result tables and
charts, and metadata chips. Each conversation is pinned to one database and
model, chosen in the header — which also shows the disclosure policy in force —
and locked once the first message is sent. Copy buttons, conversation rename
and delete, and right-to-left support for Persian and other RTL scripts.

Data sources with an engine picker, a table list, an FK graph view, and the
semantic-layer editor (metric expressions validated live by the *same parser*
the save path uses, so the editor never promises something the backend will
reject). LLM providers. A user-management panel with per-user editing.
Dashboards with drag-and-resize tiles, per-tile refresh, and import/export of a
whole board as a file. Reports get three
screens of their own: an outline editor that walks Describe → Structure → Check
→ Generate with the guard's verdict on every question, a viewer that watches
the document write itself and lets any paragraph or chart be refined
afterwards, and the run history a regeneration adds to. Dark and light themes.

**Not built yet:** rolling conversation summaries, sharing a dashboard or a
report with another user, and scheduled report generation. Each is deferred
deliberately — see [docs/architecture.md](docs/architecture.md) for the
reasoning.

---

## The three things that are not simplified

The architecture doc argues that most of this system should be as boring as
possible, and names three places where that does not apply.

> For the full picture — every point where data reaches a model provider and
> exactly what each one sends, the disclosure ladder, the SQL guard's rejection
> codes, and a pre-production checklist — see
> **[docs/security.md](docs/security.md)**.

### 1. SQL validation is AST-based and fails closed

The model proposes SQL; it never executes anything. Every statement is parsed
with SQLGlot and walked against an allowlist of expression types. An unknown
node type is a **rejection**, not a warning — so a new SQLGlot release adding
an expression class causes a false rejection, never a bypass.

Table and column names are resolved against the connection's stored schema
snapshot. A connection that has never been synced can be queried for nothing
at all.

The hostile corpus in `backend/tests/unit/test_sqlguard_hostile.py` is the
build's hard gate: statement chaining, DDL, writes, system catalogs,
`pg_read_file`, `xp_cmdshell`, `INTO OUTFILE`, union smuggling, comment
evasion. Zero bypasses, or CI fails.

```bash
make guard
```

There are **three** entry points to the guard — the pipeline, a dashboard tile,
and a report block — and the corpus is replayed through all three
(`test_sqlguard_hostile.py`, `test_query_service.py`, `test_report_guard.py`).
Provenance (`GENERATED | GENERATED_EDITED | HANDWRITTEN`) is recorded but is
**never a trust signal**; the guard cannot tell them apart and must not try.

Containment sits underneath correctness, in each engine's own terms: a
`READ ONLY` transaction on PostgreSQL, MySQL, and Oracle; the read-only role
plus a query timeout on SQL Server, which has no such transaction mode. Every
engine adds a statement timeout and a row cap, and each connector verifies the
role genuinely cannot write by attempting one, inside a transaction it always
rolls back.

### 2. Credentials are encrypted with a binding context

`SecretBox` uses AES-256-GCM with the row identity as additional
authenticated data. Moving an encrypted blob from one connection to another
produces a decryption failure rather than a silently working credential.

No read model has a password or `api_key` field. A test asserts this against
the generated schemas so it cannot regress.

### 3. Disclosure is an explicit, visible policy

Each connection declares how much of a query result may reach the model
provider: `NONE`, `AGGREGATE`, `SAMPLE`, or `FULL`. The chat header shows which
policy is in force **at the moment you ask**, not in documentation you have to
go find.

The policy governs three things, and all three filter at *render* time rather
than at write time — so tightening a policy takes effect on the next question,
with no re-sync and no leak from the existing transcript:

1. the query **result** sent to the model,
2. the per-column **content hints** in the schema block,
3. the **conversation history** — an earlier answer is prose the model wrote
   *from* result rows, so replaying it is a disclosure. Under `NONE` and
   `AGGREGATE` that prose is withheld while its SQL survives, which is what a
   follow-up actually builds on.

A conversation is pinned to one connection so history can never cross policies,
and reports refuse `NONE`/`AGGREGATE` outright — prose written from no values
beside charts drawn from real ones is a document that disagrees with itself.

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

### Deferred on purpose

| Deferred | Why | Trigger to revisit |
| --- | --- | --- |
| LangGraph | The graph is linear with one bounded retry loop. Nodes are already LangGraph-shaped, so adopting it is wiring, not a rewrite. | Durable interrupts, parallel fan-out, or resume-after-crash mid-graph |
| Celery + Redis | A run is 5–60 seconds. Durability comes from the `runs` table plus a heartbeat; Celery would add a deployment unit and make SSE fan-out harder. | p95 run > ~5 min, runs must survive rolling deploys, or multiple API replicas share a queue |

LiteLLM is kept, but strictly behind `LLMGateway`. CI greps to prove it:

```bash
grep -rn "import litellm" app/ | grep -v infra/llm/   # must be empty
```

That one line decides whether the abstraction is real or decorative.

---

## Repository layout

```
backend/
  app/
    api/        routers, DTOs, deps, RFC 7807 error mapping
    core/       config, logging with redaction, errors, context, clock
    domain/     value objects and ports (Protocols) — no framework imports
    services/   use cases, saved-SQL execution, disclosure policy, bootstrap
    pipeline/   typed RunState, the ten nodes, executor, versioned prompts,
                the schema-question answer, the disclosure gate, free checks
    sqlguard/   policy, validator, rewriter — self-contained, dialect-aware
    semantic/   what the schema means: the document, its generator, its render
    reports/    outline, language, facts, narration, the numeric check — pure
    charts/     result profile → shape fit → Vega-Lite
    eval/       the offline harness: dataset, suites, runner, metrics, reports
    infra/      SQLAlchemy + Alembic, crypto, repositories,
                connectors (postgres/mysql/mssql/oracle), LiteLLM, events,
                identity
    workers/    in-process executor, reconciler, semantic and report jobs
  tests/        unit, integration, eval
  fixtures/     the 42-table sales schema in three dialects, the Sakila seed,
                and rebuild_fixtures.sh
  scripts/      eval_run.sh and its LLM-config seeder
frontend/
  src/
    theme/      design tokens taken verbatim from the design concept
    api/        typed client, SSE streaming with polling fallback
    components/ UI primitives, chat, dashboards, tile editor, the semantic
                layer editor, reports, the Vega renderer and chart picker,
                plus DOM-free logic modules with their own node tests
    pages/      login, chat, data sources, LLM providers, users, dashboards,
                reports
docs/           see docs/README.md for what to read when
```

`backend/tests/` and `backend/fixtures/` are siblings of `app/`, not inside it.

---

## Frontend notes

The architecture doc says "MUI SPA", but the design concept is not MUI — it is
a custom system built on oklch CSS variables with its own visual language.
Reproducing it through MUI would have meant fighting MUI's defaults to arrive
back at the same place, so the SPA uses the design tokens directly with a
small component kit.

Every colour reads from a CSS variable defined in `src/theme/tokens.ts`, and
those values are copied verbatim from the design concept
(`docs/assets/ui-design-concept.html`) rather than re-derived. Both the dark
and light palettes are included.

---

## Testing

```bash
make test      # full backend suite
make guard     # the hostile SQL corpus alone — the hard gate
make lint      # ruff + import-linter contracts
make fmt       # ruff format
make fixtures  # rebuild and verify the sales fixtures from clean
```

From `frontend/`:

```bash
npm run typecheck   # tsc --noEmit
npm run build       # tsc -b && vite build
npm run lint        # eslint
npm test            # the DOM-free logic modules (schedule, format, palette,
                    # report document, print)
```

The offline eval harness is separate and costs real tokens — see
[docs/eval.md](docs/eval.md).

---

## Configuration

| Variable | Purpose |
| --- | --- |
| `SECRET_BOX_KEY` | 32-byte urlsafe-base64 key for credential encryption |
| `JWT_SECRET` | Access-token signing secret |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Bootstrap admin, applied idempotently at startup |
| `DATABASE_URL` | Application database |
| `MAX_CONCURRENT_RUNS` | Executor concurrency limit |
| `RUN_DEADLINE_SECONDS` | Hard per-run time budget |

`make secrets` generates the two cryptographic values for you. Losing
`SECRET_BOX_KEY` means every stored credential must be re-entered.

> **Naming note:** the product is *DataMind*, but the Python package is still
> `raymand`, as are the compose project and the bootstrap admin
> (`admin@raymand.local`). Renaming is a deliberate separate task.

If the app database ever fails to start with *"could not open directory
'pg_notify'"* — the Lightning Studio drive drops empty directories across
restarts — run `make db-repair`. It recreates the empty runtime scaffolding and
never touches data.

---

## Documentation

| Doc | Read it when |
| --- | --- |
| [CLAUDE.md](CLAUDE.md) | You are about to change code — the map, the invariants, the gotchas |
| [docs/CODEBASE.md](docs/CODEBASE.md) | You want a code-grounded tour of the whole stack |
| [docs/architecture.md](docs/architecture.md) | You want the *why*, including what was deferred and on what trigger |
| [docs/security.md](docs/security.md) | You are touching `sqlguard/`, disclosure, or adding an LLM call site |
| [docs/pipeline.md](docs/pipeline.md) | You are changing a node |
| [docs/charts.md](docs/charts.md) | You are changing what gets drawn |
| [docs/dashboards.md](docs/dashboards.md) | You are changing tiles or saved-SQL execution |
| [docs/reports.md](docs/reports.md) | You are changing the document |
| [docs/eval.md](docs/eval.md) | You want to measure whether a change helped |

---

## License

MIT
