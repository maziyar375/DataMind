# DataMind — Codebase Documentation

A code-grounded tour of *what the code is* and *what technology it uses*. It
sits between the user-facing [README](../README.md), the concise developer map
[CLAUDE.md](../CLAUDE.md), and the full design rationale
[architecture.md](architecture.md).

DataMind is a **conversational business-intelligence** application. You ask a
question in plain English (or Persian); it decides what you mean, writes SQL,
proves that SQL is safe *before* running it, executes it read-only against your
database, and hands back a written answer, a result table, and a chart — with
the generated SQL on display. It talks to **PostgreSQL, MySQL, SQL Server, and
Oracle** through one connector interface.

Size: ~7.1k lines of Python (backend) and ~5.7k lines of TypeScript/TSX
(frontend).

---

## 1. Technology stack

### Backend (`backend/`, Python 3.12)

| Concern | Technology | Notes |
| --- | --- | --- |
| Web framework | **FastAPI** ≥0.115 | ASGI app, OpenAPI at `/docs`, RFC 7807 errors |
| ASGI server | **Uvicorn** (`[standard]`) | `--reload` in dev |
| ORM / DB toolkit | **SQLAlchemy 2.0** (async) | `async` engine + sessions |
| App DB driver | **asyncpg** | the app's own store is PostgreSQL |
| Migrations | **Alembic** | `alembic upgrade head` on startup |
| Validation / settings | **Pydantic v2** + **pydantic-settings** | DTOs and typed config |
| SQL parsing & safety | **SQLGlot** ≥25 | dialect-aware parse + AST allowlist |
| LLM access | **LiteLLM** ≥1.52 | isolated behind the `LLMGateway` port |
| Password hashing | **argon2-cffi** (Argon2id) | |
| Tokens | **PyJWT** | short-lived access + rotating refresh |
| Encryption | **cryptography** | AES-256-GCM (`SecretBox`) |
| Logging | **structlog** | JSON in prod, redaction of secrets |
| HTTP client | **httpx** | provider capability probes |
| Forms / email | python-multipart, email-validator | |

**Target-database drivers** (all ship wheels, no system client required):

| Engine | Driver | Mode |
| --- | --- | --- |
| PostgreSQL | **asyncpg** | native async |
| MySQL | **aiomysql** | native async |
| Oracle | **oracledb** | *thin* mode — no Instant Client |
| SQL Server | **pymssql** | sync, offloaded via `asyncio.to_thread`; bundles FreeTDS |

**Dev / CI:** pytest + pytest-asyncio + pytest-cov, **ruff** (lint + format),
**mypy** (strict), **import-linter** (architecture contracts).

### Frontend (`frontend/`, TypeScript 5.6)

| Concern | Technology |
| --- | --- |
| UI library | **React 18.3** |
| Build / dev server | **Vite 5.4** (`@vitejs/plugin-react`) |
| Routing | **react-router-dom 6** |
| Charts | **Vega-Lite** via `vega` / `vega-lite` / `vega-embed` |
| Styling | Custom design system on **oklch CSS variables** — *no* component library |
| Fonts | Inter, JetBrains Mono, Vazirmatn (Persian) |

The design tokens in `src/theme/tokens.ts` are copied verbatim from the design
concept (`docs/ui-design-concept.html`); both dark and light palettes ship.

### Infrastructure

**Docker Compose** with four services:

| Service | Image / build | Role |
| --- | --- | --- |
| `db` | postgres:16-alpine | DataMind's own application store (port 5432) |
| `sales` | postgres:16-alpine | the **demo target** DB, seeded read-only (port 5433) |
| `api` | `./backend` | runs migrations then Uvicorn (port 8000) |
| `web` | `./frontend` | Vite dev server, proxies `/api` → `api:8000` (port 5173) |

The separate `sales` instance exists on purpose: the whole point is that
DataMind reaches customer data *over a connector with a read-only role*, not by
sharing a database.

---

## 2. Architecture at a glance

A **modular monolith** in strict layers. The dependency rule is enforced by
`import-linter` in CI, not by convention:

```
api  →  HTTP shape only. Auth extraction, DTO validation. No business logic.
services  →  Use cases. Transaction boundaries. Authorization decisions.
pipeline  →  The AI run: typed state, nodes, executor.
domain  →  Entities, value objects, Protocols (ports). Zero I/O, no frameworks.
infra  →  Adapters that implement the Protocols.

            api → services → pipeline → domain ← infra
```

**Ports and adapters** exist at exactly **four** seams — the four things most
likely to be swapped:

| Port (`domain/ports/`) | Adapter (`infra/`) | Purpose |
| --- | --- | --- |
| `LLMGateway` (`llm.py`) | `infra/llm/` (LiteLLM) | model completion + capability probe |
| `DatabaseConnector` (`database.py`) | `infra/connectors/` | introspection + read-only execution |
| `SecretBox` (`secrets.py`) | `infra/crypto/` | credential encryption |
| `RunExecutor` (`run_executor.py`) | `workers/inprocess.py` | run the pipeline off the request |

Plus `IdentityProvider` (auth) and `EventPublisher` (SSE). The domain is pure:
it imports no framework and no infra, so it can be unit-tested in isolation and
reasoned about without a database.

---

## 3. Directory-by-directory

### `backend/app/api` — the edge
Routers under `v1/`: `auth`, `users`, `connections`, `llm_configs`,
`conversations`. Each router only shapes HTTP: extracts the identity, validates
the DTO (`schemas.py`), and calls a service. Errors map to RFC 7807
`problem+json` (`errors.py`). `main.py` is the ASGI factory — it wires CORS, a
correlation-id middleware (every response carries `X-Correlation-ID`), health
probes, and a lifespan that bootstraps the admin user, reconciles orphaned runs
on boot, and starts the background reconciler.

### `backend/app/core` — cross-cutting
Config (pydantic-settings), structured logging with secret redaction, the error
hierarchy, correlation context, and a clock. No business logic.

### `backend/app/domain` — the pure core
`value_objects/` holds the enums that the whole system speaks in: `Role`,
`UserStatus`, `DatabaseKind` (postgres/mysql/mssql/oracle, each with a
`sqlglot_dialect` and `default_port`), `RunStatus`, `StepName`, `StepStatus`,
`MessageRole`, `ArtifactKind`, `DisclosurePolicy` (NONE/AGGREGATE/SAMPLE/FULL),
and `RunEventType`. `ports/` holds the Protocols and their immutable dataclass
value objects (`SchemaSnapshot`, `TableInfo`, `ColumnInfo`, `RelationshipInfo`,
`QueryResult`, `ConnectionProbe`, …). `entities/` is intentionally empty —
persistent entities live as ORM rows in `infra/db/models.py`, and the domain
deliberately speaks in value objects, not ORM.

### `backend/app/services` — use cases
Where transactions and authorization live. `run_service.py` (the largest file)
orchestrates creating and driving a run. `bootstrap.py` idempotently ensures the
admin account. `disclosure_service.py` + `policy.py` decide how much result data
may reach the model.

### `backend/app/pipeline` — the AI run
`state.py` defines the typed `RunState`, `NodeResult`, and `RunError`.
`pipeline.py` is a small explicit **state machine**: a linear order of nodes
with one bounded repair loop (a node may `goto` back to `generate`), a hard
ceiling of 24 transitions, and a per-run deadline. `nodes/` implements
`route → retrieve → generate → validate → execute → present`, plus the METADATA
short-circuit that answers schema questions without SQL. `prompts/` holds
versioned prompt templates. A node crash is caught and recorded as a *run
failure*, never a bare 500.

### `backend/app/sqlguard` — the safety net
Self-contained (import-linter forbids it from importing infra/api/frameworks).
`validator.py` parses model-proposed SQL with SQLGlot and walks it against an
allowlist of expression types — an unknown node type is a **rejection**.
`policy.py` carries the dialect-aware rules; `rewriter.py` injects `LIMIT` and
normalizes. Names are resolved against the connection's stored schema snapshot,
so an unsynced connection can query nothing. The hostile corpus in
`tests/unit/test_sqlguard_hostile.py` is the build's hard gate.

### `backend/app/charts` — presentation
Turns a `ChartIntent` into a validated **Vega-Lite** spec (the model does not
author raw chart JSON).

### `backend/app/infra` — the adapters
- `db/` — SQLAlchemy models (13 tables, below), Alembic migrations, async
  session factory.
- `connectors/` — `factory.py` maps each `DatabaseKind` to a connector; each of
  `postgres/mysql/mssql/oracle.py` implements `DatabaseConnector`
  (introspection + read-only execution + a genuine read-only probe). Constraint
  introspection uses each engine's own catalog (`pg_catalog`, `sys.*`, `ALL_*`)
  rather than `information_schema`, which is privilege-filtered under a
  read-only role.
- `llm/` — LiteLLM behind `LLMGateway`. CI greps to prove `import litellm`
  appears nowhere else.
- `crypto/` — `SecretBox`: AES-256-GCM with row identity as additional
  authenticated data.
- `identity/` — the local Argon2id + JWT provider (access token + rotating
  refresh token with reuse detection; admin set-password revokes live sessions).
- `events/` — the SSE event publisher.

### `backend/app/workers` — running the run
`inprocess.py` runs the pipeline off the request thread with heartbeats;
`reconciler.py` sweeps runs whose process died so none is stuck `RUNNING`.

### `frontend/src`
`api/client.ts` is the typed client, including SSE streaming with a polling
fallback and `Last-Event-ID` replay. `theme/tokens.ts` holds design tokens and
`DATABASE_TYPES`. `components/` has the primitive kit (`ui.tsx` — inputs,
icons, the puzzle-piece `Logo`), chat rendering (`chat.tsx` — the live step
trail, SQL panel, tables, charts, copy buttons, RTL support), and settings
scaffolding. `pages/` are Login, Chat, DataSources, LlmProviders, and Users.

---

## 4. Data model (ORM tables, `infra/db/models.py`)

| Table | Holds |
| --- | --- |
| `users` | accounts, roles, status, Argon2id hash |
| `sessions` | refresh-token sessions (rotation + reuse detection) |
| `llm_configs` | model provider configs (encrypted API keys) |
| `database_connections` | target DB connections (encrypted credentials, disclosure policy) |
| `schema_snapshots` | introspected schema per connection — the guard's source of truth |
| `conversations` | chat threads |
| `messages` | user / assistant / system turns |
| `runs` | one pipeline execution per user question |
| `run_steps` | per-step status + timing (the visible trail) |
| `generated_queries` | model-proposed SQL and its validation verdict |
| `query_executions` | execution results / metadata |
| `artifacts` | table / chart / error / SQL-summary outputs |
| `run_events` | the event stream (SSE replay) |
| `audit_logs` | audit trail |

---

## 5. Request-to-answer flow

1. **Ask.** `POST /api/v1/conversations/{id}/messages`. `run_service.create_run`
   writes the user `message`, **flushes** (so the `runs` FK resolves), writes
   the `runs` row, and hands off to the `RunExecutor`.
2. **Route.** Classify intent. A METADATA question is answered directly from the
   `schema_snapshots` snapshot and **halts before any SQL**.
3. **Retrieve → Generate.** Assemble schema context; the LLM (via `LLMGateway`)
   *proposes* SQL. It never executes anything.
4. **Validate.** `sqlguard` parses and walks the SQL against the allowlist and
   resolves names against the snapshot. Reject → bounded `goto` back to Generate.
5. **Execute.** The dialect's `DatabaseConnector` runs it inside read-only
   containment (READ ONLY transaction where the engine supports it; role +
   timeout on SQL Server), with a statement timeout and a row cap.
6. **Present.** Build the answer text, the table artifact, and a validated
   Vega-Lite chart. Terminal status `SUCCEEDED` (or `FAILED` / `TIMED_OUT` /
   `CANCELLED`).

Throughout, each step persists a `run_step` and emits an SSE `run_event`; the
SPA renders the **live step trail** so the user sees exactly what happened —
this is a deliberate product feature, not debug output.

---

## 6. Security & safety properties

- **SQL validation fails closed** — an unrecognized AST node is rejected, so a
  new SQLGlot release can only cause a false *rejection*, never a bypass.
- **Read-only containment** per engine, verified by attempting a write inside a
  rolled-back transaction at connect time.
- **Credential encryption bound to row identity** — a ciphertext copied between
  rows fails to decrypt; no read DTO ever exposes a password or `api_key`.
- **Explicit disclosure policy** — each connection caps how much result data
  reaches the model, shown in the chat header at ask time.
- **Auth** — Argon2id, short-lived JWT access tokens, rotating refresh tokens in
  an HttpOnly cookie with reuse detection.

---

## 7. Build, run, test

```bash
make secrets && make up          # fresh keys, then the whole stack
# open http://localhost:5173 — sign in with ADMIN_EMAIL / ADMIN_PASSWORD

make test    # backend suite       make guard  # hostile SQL corpus (hard gate)
make lint    # ruff + import-linter make migrate # alembic upgrade head
```

Frontend (from `frontend/`): `npm run dev`, `npm run build`, `npm run
typecheck`.

CI's non-negotiables: the hostile SQL corpus passes with zero bypasses; the
import-linter layer/forbidden contracts hold; `import litellm` appears only
under `infra/llm/`.

---

## 8. Deliberately deferred

The semantic layer, clarification turns, model-authored charts, retrieval
beyond exact matching, rolling conversation summaries, LangGraph, Celery+Redis,
and the eval harness are all deferred **on purpose** — with named triggers to
revisit each in [architecture.md](architecture.md). The node signatures are
already LangGraph-shaped, so adopting it later is wiring, not a rewrite.

> Naming note: the product is **DataMind**; the Python package and compose
> project are still `raymand` (`import app.*`, `admin@raymand.local`).
