# DataMind

Conversational BI. Ask a question in plain language, get a written answer, a
table, and a chart — with the generated SQL visible and auditable. Point it at
**PostgreSQL, MySQL, SQL Server, or Oracle**; the question is the same, the
dialect is the connector's problem.

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
make up           # builds and starts db, sales fixture, api, and web
```

Then open <http://localhost:5173> and sign in with the bootstrap admin
(`admin@raymand.local` / `raymand` by default — change `ADMIN_PASSWORD` in
`.env` before doing anything real; the API logs a loud warning if you don't).

Two demo databases ship with the stack, so you can exercise the app against
two engines. Add a data source pointing at either fixture — a **PostgreSQL**
sales model (14 related tables: orders, order_items, payments, shipments,
returns, inventory, employees…) or the classic **MySQL** "Sakila" sample
(16 tables, ~46k rows of films, actors, rentals, payments):

| Field    | PostgreSQL demo | MySQL demo     |
| -------- | --------------- | -------------- |
| Engine   | `PostgreSQL`    | `MySQL`        |
| Host     | `sales`         | `sakila`       |
| Port     | `5432`          | `3306`         |
| Database | `sales`         | `sakila`       |
| User     | `analytics_ro`  | `analytics_ro` |
| Password | `analytics_ro`  | `analytics_ro` |

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

**Backend**

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
- The SQL guard: parse, single-statement enforcement, AST allowlist, name
  resolution against the snapshot, LIMIT injection — dialect-aware, so the
  same guard renders Postgres, MySQL, T-SQL, and Oracle
- The pipeline: route → retrieve → generate → validate → execute → present →
  chart, with a bounded repair loop, including a metadata route that answers
  schema questions ("what tables do I have?") without touching SQL. The chart
  step is fail-open: the model proposes a constrained `ChartIntent` (compiled to
  Vega-Lite) with a data-shape heuristic fallback, and any failure just yields
  no chart
- SSE streaming with replay from `Last-Event-ID`, plus a polling fallback
- In-process run executor with heartbeats and a stale-run reconciler

**Frontend**

Chat with the live step trail, the "Generated SQL" panel, result tables and
charts, and metadata chips. Each conversation is pinned to one database and
model, chosen in the header — which also shows the disclosure policy — and
locked once the first message is sent. Copy buttons, conversation rename and
delete, and right-to-left support for Persian and other RTL scripts. Data
sources with an engine picker, a table list, and an FK graph view; LLM
providers; a user-management panel with per-user editing. Dark and light
themes.

**Not built yet:** the semantic layer, clarification turns, retrieval beyond
exact matching, rolling conversation summaries, and the eval harness. Each is
deferred deliberately — see the architecture doc for the reasoning.

---

## The three things that are not simplified

The architecture doc argues that most of this system should be as boring as
possible, and names three places where that does not apply.

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
provider: nothing, totals only, a bounded sample, or everything. The chat
header shows which policy is in force **at the moment you ask**, not in
documentation you have to go find.

---

## Architecture

```
api        →  HTTP shape only. Auth extraction, DTO validation, no business logic.
services   →  Use cases. Transaction boundaries. Authorization decisions.
pipeline   →  The AI run: typed state, nodes, executor.
domain     →  Entities, value objects, Protocols (ports). Zero I/O.
infra      →  Adapters implementing the Protocols.
```

The dependency rule — `api → services → domain ← infra` — is enforced by
`import-linter` in CI rather than by convention.

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
    api/        routers, DTOs, RFC 7807 error mapping
    core/       config, logging with redaction, errors, context
    domain/     entities, value objects, ports — no framework imports
    services/   use cases, disclosure policy, bootstrap
    pipeline/   typed RunState, nodes, executor, versioned prompts
    sqlguard/   parser, policy, validator, rewriter
    charts/     ChartIntent → validation → Vega-Lite
    infra/      SQLAlchemy, crypto, connectors (postgres/mysql/mssql/oracle),
                LiteLLM, events, identity
    workers/    in-process executor, reconciler
  tests/
  fixtures/     seeded sales database with a read-only role
frontend/
  src/
    theme/      design tokens taken verbatim from the design concept
    api/        typed client, SSE streaming with polling fallback
    components/ UI primitives and chat rendering
    pages/      login, chat, data sources, LLM providers, users
docs/
  architecture.md          the full architecture proposal
  ui-design-concept.html   the original design
```

---

## Frontend notes

The architecture doc says "MUI SPA", but the design concept is not MUI — it is
a custom system built on oklch CSS variables with its own visual language.
Reproducing it through MUI would have meant fighting MUI's defaults to arrive
back at the same place, so the SPA uses the design tokens directly with a
small component kit.

Every colour reads from a CSS variable defined in `src/theme/tokens.ts`, and
those values are copied verbatim from the design concept rather than
re-derived. Both the dark and light palettes are included.

---

## Testing

```bash
make test     # full backend suite
make guard    # the hostile SQL corpus alone
make lint     # ruff + import-linter contracts
```

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

---

## License

MIT
