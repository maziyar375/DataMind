# CLAUDE.md — orientation for developers and agents

Read this before touching the code. It is the map, not the territory: it tells
you where things live, what must not break, and how to run and test — so you
can make a change without first reading all ~13k lines.

For the "why", see [docs/architecture.md](docs/architecture.md) (the full
proposal) and [docs/CODEBASE.md](docs/CODEBASE.md) (a code-grounded tour of the
stack). For users, see [README.md](README.md).

---

## What this is

**DataMind** — conversational business intelligence. A user asks a question in
plain language; the system routes it, generates SQL, **validates that SQL
statically**, runs it read-only against the target database, and returns a
written answer, a table, and a chart — with the generated SQL shown and
auditable. Targets **PostgreSQL, MySQL, SQL Server, and Oracle** behind one
connector interface.

A single modular-monolith **FastAPI** backend on one PostgreSQL app database,
plus a **React + Vite** SPA. No microservices, no broker, no vector DB.

> **Naming gotcha:** the product is *DataMind*, but the Python package is still
> `raymand` (import `app.*`), the compose project is `raymand`, and the
> bootstrap admin is `admin@raymand.local`. Renaming the package is a separate,
> deliberate task — don't do it incidentally.

---

## Tech stack (one line each)

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic,
  Pydantic v2 / pydantic-settings, structlog.
- **SQL safety:** SQLGlot (parse + AST allowlist, dialect-aware).
- **LLM:** LiteLLM — *only* behind the `LLMGateway` port (`app/infra/llm/`).
- **Crypto/auth:** argon2-cffi (Argon2id), PyJWT, `cryptography` (AES-256-GCM).
- **Target DB drivers:** asyncpg (Postgres), aiomysql (MySQL), oracledb *thin*
  (Oracle), pymssql (SQL Server). All ship wheels — no system DB client needed.
- **Frontend:** React 18 + TypeScript 5.6, Vite 5.4, react-router-dom 6,
  Vega-Lite (`vega`/`vega-lite`/`vega-embed`) for charts. Custom design system
  on oklch CSS variables — **no component library**.
- **Dev/CI:** pytest + pytest-asyncio, ruff, mypy (strict), **import-linter**
  (enforces the layer rule), Docker Compose.

---

## Commands

```bash
make secrets   # write .env with a fresh AES key + JWT secret (run once)
make up        # build & start db, sales fixture, api, web
make down      # stop everything
make logs      # follow api logs

make test      # full backend suite (cd backend && pytest -q)
make guard     # the hostile SQL corpus alone — the hard CI gate
make lint      # ruff + import-linter contracts
make fmt       # ruff format
make migrate   # alembic upgrade head
```

Frontend, from `frontend/`: `npm run dev`, `npm run build` (`tsc -b && vite
build`), `npm run typecheck` (`tsc --noEmit`), `npm run lint`.

**Verification loop before you claim done:** `npm run typecheck` + `npm run
build` for frontend changes; `make test` (and `make guard` if you touched
`sqlguard/` or a connector) for backend. Several past bugs only surfaced
end-to-end via the API, not in the UI — actually exercise the path you changed.

**Ports:** web `5173`, api `8000` (`/docs` for OpenAPI), app db `5432`, demo
`sales` db `5433`. On a remote host, expose **only 5173**; the SPA calls the
same-origin `/api/v1` and Vite proxies it to `api:8000`.

---

## Code map

```
backend/app/
  main.py         ASGI factory: lifespan (bootstrap admin, reconcile orphans,
                  start reconciler), CORS, correlation-id middleware, health.
  api/            HTTP shape ONLY — no business logic.
    v1/           auth, users, connections, llm_configs, conversations routers
    schemas.py    Pydantic request/response DTOs (no secrets ever in reads)
    errors.py     RFC 7807 problem+json mapping
  core/           config, logging (with redaction), errors, correlation context, clock
  domain/         entities, value_objects (enums/kinds), ports — ZERO I/O, no frameworks
    ports/        Protocols: database, llm, secrets, identity, events, run_executor
  services/       use cases + transaction boundaries: run_service, bootstrap,
                  disclosure_service, policy
  pipeline/       the AI run: state.py (typed RunState), pipeline.py (state machine),
                  nodes/ (route→retrieve→generate→validate→execute→present→chart), prompts/
  sqlguard/       policy, validator, rewriter — self-contained, dialect-aware
  charts/         ChartIntent → validation → Vega-Lite
  infra/          adapters implementing the ports:
    db/           SQLAlchemy models.py + Alembic migrations + session
    connectors/   factory + postgres/mysql/mssql/oracle (one DatabaseConnector each)
    llm/          LiteLLM behind LLMGateway
    crypto/       SecretBox (AES-256-GCM)
    identity/     local Argon2id + JWT provider
    events/       SSE event publisher
  workers/        inprocess run executor + stale-run reconciler
  tests/          unit (incl. test_sqlguard_hostile.py) + integration
  fixtures/       sales_seed.sql — demo DB with a read-only role

frontend/src/
  main.tsx, App.tsx        entry + router/layout
  theme/tokens.ts          design tokens (oklch), DATABASE_TYPES, dark+light palettes
  api/client.ts, types.ts  typed client, SSE streaming + polling fallback
  components/               ui.tsx (primitives, icons, Logo), chat.tsx, settings.tsx
  pages/                    Login, Chat, DataSources, LlmProviders, Users
```

---

## The dependency rule (enforced, not documented)

```
api → services → pipeline → domain ← infra
```

`import-linter` fails CI on violation (`make lint`). Concretely:

- **`app.domain` imports no framework and no infra** — no fastapi, sqlalchemy,
  litellm, `app.infra`, `app.api`, `app.services`. Keep it pure.
- **`app.sqlguard` is self-contained** — no fastapi/sqlalchemy/litellm/infra/api.
- Services may reach into infra (that carve-out is explicit in the config).

Ports & adapters exist at **exactly four** seams — the four things most likely
to be replaced: **LLM, target database, secrets, run execution.** Add adapters
behind these ports; don't route around them. In particular: **never `import
litellm` outside `app/infra/llm/`** — CI greps for it.

---

## Non-negotiable invariants (don't regress these)

1. **SQL validation is AST-based and fails closed.** The model only *proposes*
   SQL. Every statement is parsed with SQLGlot and walked against an allowlist;
   an **unknown node type is a rejection, not a warning**. Names are resolved
   against the connection's stored schema snapshot — an unsynced connection can
   query nothing. `tests/unit/test_sqlguard_hostile.py` is the hard gate: zero
   bypasses or CI fails.
2. **Containment underneath correctness.** `READ ONLY` transaction on Postgres
   / MySQL / Oracle; read-only role + query timeout on SQL Server (no such
   transaction mode). Every engine adds a statement timeout and a row cap, and
   each connector proves the role can't write by trying — inside a rolled-back
   transaction.
3. **Credentials are encrypted with a binding context.** `SecretBox` is
   AES-256-GCM with the **row identity as AAD** — a ciphertext moved between
   rows fails to decrypt. **No read model ever exposes a password or
   `api_key`**; a test asserts this against the generated schemas.
4. **Disclosure is explicit and visible.** Each connection declares how much
   result data may reach the model: `NONE | AGGREGATE | SAMPLE | FULL`. The
   chat header shows the policy in force *at ask time*.

---

## How a run works

`POST /conversations/{id}/messages` → `run_service.create_run` writes the user
`message`, **flushes**, then the `runs` row (FK order matters — see below),
hands off to the in-process executor. `AnalyticsPipeline.run` walks a linear
state machine with one bounded repair loop:

```
route → retrieve → generate → validate → execute → present → chart
```

- `route` classifies intent. **METADATA** questions ("what tables do I have?")
  are answered from the schema snapshot and **HALT before any SQL**.
- A validation/execution failure can `goto` back to `generate` (bounded repair);
  a hard ceiling of 24 transitions and a per-run deadline prevent runaway loops.
- Each step persists a `run_step` and emits an SSE event; the SPA renders the
  **live step trail**, which is a valued feature — keep it visible, don't
  collapse it behind a "Thought for Xs" summary by default.
- `chart` is **best-effort and fail-open** (the opposite of the SQL guard): the
  model proposes a constrained `ChartIntent` compiled to Vega-Lite, with a
  data-shape heuristic as the fallback; any failure just yields no chart, since
  the answer and table are already persisted.
- A conversation is **bound to one connection + model**, picked in the chat
  header before the first message; the pickers lock once the transcript is
  non-empty. The choice is stored as the conversation's `default_connection_id`
  / `default_llm_config_id`. `create_run` still accepts a per-message override
  and snapshots what it used onto the run, so earlier turns stay explainable.
- Terminal states: `SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`
  (`NEEDS_CLARIFICATION` reserved).

A node crash is caught and recorded as a **run failure**, never a bare HTTP
500. A process that dies mid-run is healed by the reconciler + a startup sweep,
so no row is stuck `RUNNING`.

---

## Gotchas learned the hard way

- **FK insert order:** `runs` references `messages`. Add the user message and
  **`await db.flush()` before** adding the run, or you get a FK violation.
- **`updated_at` onupdate + async:** after a PATCH, `await db.refresh(obj)`
  before `model_validate`, or the expired attribute triggers `MissingGreenlet`.
- **Frozen dataclasses have no `__dict__`:** the port value objects are
  `@dataclass(frozen=True, slots=True)`; serialize with `dataclasses.asdict`,
  not `c.__dict__`.
- **Constraint introspection:** use engine catalogs (`pg_catalog`, `sys.*`,
  `ALL_*`), **not** `information_schema` — under a read-only role the latter is
  privilege-filtered and silently drops PKs/FKs (this is why the FK graph view
  once looked empty).
- **MySQL vs MariaDB:** use `SET SESSION max_execution_time` and match timeouts
  on error code **3024**; `SET STATEMENT ... FOR` is MariaDB-only.
- **Remote host / Vite:** `server.allowedHosts: true` and the same-origin
  `/api/v1` proxy are deliberate — see README "Running on a remote host".
- **Data model note:** ORM entities live in `infra/db/models.py`;
  `domain/entities/` is intentionally empty (the domain speaks in value objects
  and ports, not ORM rows).

---

## Adding things

- **A new target database:** implement the `DatabaseConnector` Protocol
  (`domain/ports/database.py`) in `infra/connectors/<engine>.py`, register it in
  `factory.py`, add the `DatabaseKind` + its `sqlglot_dialect`/`default_port`,
  extend `sqlguard` if the dialect needs it, and add the engine to the frontend
  `DATABASE_TYPES`. Verify against a real container with a read-only role.
- **A new API route:** router in `api/v1/`, DTO in `schemas.py`, business logic
  in a `services/*` function that owns the transaction. Literal paths (e.g.
  `/test`) must be declared **above** `/{id}` routes.
- **Prompt changes:** versioned prompts live in `pipeline/prompts/`.

---

## Git / environment notes

- This sandbox has **no GitHub auth** — `git push` will fail; the user pushes
  from their own terminal. Commit locally; don't attempt to push.
- Commit or branch only when asked. Config keys: `SECRET_BOX_KEY`, `JWT_SECRET`,
  `ADMIN_EMAIL`/`ADMIN_PASSWORD`, `DATABASE_URL`, `MAX_CONCURRENT_RUNS`,
  `RUN_DEADLINE_SECONDS`. Losing `SECRET_BOX_KEY` means re-entering every stored
  credential.
