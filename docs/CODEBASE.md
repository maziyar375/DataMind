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

Three surfaces sit on that one guarded path: **Chat** (one question),
**Dashboards** (numbers kept current), and **Reports** (a document).

Size, as of 2026-08-15: **31k lines of Python** under `backend/app/` plus 22k
lines of backend tests, and **27k lines of TypeScript/TSX** under
`frontend/src/`.

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
| Run orchestration | **LangGraph** ≥1.2,<2 | isolated to `app/pipeline/` + `app/workers/` |
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
**mypy** (strict), **import-linter** (architecture contracts), **testcontainers**
(the eval harness's throwaway fixture databases).

### Frontend (`frontend/`, TypeScript 5.6)

| Concern | Technology |
| --- | --- |
| UI library | **React 18.3** |
| Build / dev server | **Vite 5.4** (`@vitejs/plugin-react`) |
| Routing | **react-router-dom 6** |
| Charts | **Vega-Lite** via `vega` / `vega-lite` / `vega-embed` |
| Dashboard grid | **react-grid-layout** — a layout engine, not a component library |
| Styling | Custom design system on **oklch CSS variables** — *no* component library |
| Fonts | Inter and JetBrains Mono from Google Fonts; **Vazirmatn** (Persian) self-hosted from `public/fonts` — a printed Persian report is a deliverable and must not depend on a CDN a firewalled deployment cannot reach |

The design tokens in `src/theme/tokens.ts` are copied verbatim from the design
concept (`docs/assets/ui-design-concept.html`); both dark and light palettes ship.

There is **no test runner**: the nine logic suites are plain `node
--experimental-strip-types` scripts over DOM-free modules. That is a deliberate
consequence of where the risk is — see §7.

### Infrastructure

**Docker Compose.** Five services start with the stack, two more are opt-in:

| Service | Image / build | Host port | Role |
| --- | --- | --- | --- |
| `db` | postgres:16-alpine | 5432 | DataMind's own application store |
| `sales` | postgres:16-alpine | 5433 | the **demo target** DB — the 42-table commerce schema, seeded read-only |
| `sakila` | mysql:8.0 | 3307 | the second demo target — the classic Sakila sample |
| `api` | `./backend` | 8000 | runs migrations then Uvicorn |
| `web` | `./frontend` | 5173 | Vite dev server, proxies `/api` → `api:8000` |
| `oracle` | gvenzl/oracle-xe:18 | 1521 | **profile `targets`** — a four-table schema whose `COMMENT ON` metadata is the point |
| `mssql` (+ `mssql-seed`) | mssql/server:2022 | 1433 | **profile `targets`** — the same 42-table `sales` mirror as Postgres |

The separate target instances exist on purpose: the whole point is that
DataMind reaches customer data *over a connector with a read-only role*, not by
sharing a database. Oracle and SQL Server sit behind a profile because each
wants ~2 GB of RAM and most sessions never touch them (`make targets`, and
`make targets-down` to stop them again).

Adding the opt-in two as data sources inside the app — these are the addresses
**on the compose network**, which is what the API dials, not your browser:

| Field    | Oracle demo    | SQL Server demo |
| -------- | -------------- | --------------- |
| Engine   | `Oracle`       | `SQL Server`    |
| Host     | `oracle`       | `mssql`         |
| Port     | `1521`         | `1433`          |
| Database | `XEPDB1`       | `sales`         |
| Schemas  | `SALES`        | `dbo`           |
| User     | `analytics_ro` | `analytics_ro`  |
| Password | `analytics_ro` | `analytics_ro`  |

On Oracle, `Database` is a **service name**, not a catalogue — that is how
Oracle is addressed, and the schema is the owning user (`SALES`). SQL Server
gets the same 42-table `sales` model as the Postgres demo, so the two are
directly comparable. Oracle's smaller four-table schema is the **`COMMENT ON`
fixture**: ask *"how much revenue did we make from paid orders?"* and the
generated SQL will filter `STATUS = 'P'`, a code meaning that exists nowhere but
the column comment. Sync it, then look at Semantic layer to see the DBA's
sentences promoted into the document.

`docker-compose.replicas.yml` overlays a second `api` behind nginx — see
[cross-replica.md](cross-replica.md).

---

## 2. Architecture at a glance

A **modular monolith** in strict layers. The dependency rule is enforced by
`import-linter` in CI, not by convention:

```
api       →  HTTP shape only. Auth extraction, DTO validation. No business logic.
services  →  Use cases. Transaction boundaries. Authorization decisions.
pipeline  →  The AI run: typed state, nodes, the compiled graph.
reports   →  Outline, facts, narration, the numeric check. Pure.
semantic  →  The document, its generator, its render. Pure.
domain    →  Entities, value objects, Protocols (ports). Zero I/O, no frameworks.
infra     →  Adapters that implement the Protocols.

   api → services → pipeline → reports → semantic → domain ← infra
```

Seven contracts hold that shape (`[tool.importlinter]` in
`backend/pyproject.toml`), and the layer order is not decoration — it buys
something concrete. `reports/narrate.py` **cannot** call `disclose()`, because
that lives in `app.pipeline` above it, so the report worker has to disclose
results under the policy in force at narration time and hand them down. That is
the stricter reading of invariant #4, enforced for free.

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

Two packages are additionally **self-contained** by contract — `sqlguard` and
`semantic`, joined by `reports` — meaning no fastapi, no sqlalchemy, no litellm,
no `app.infra`, no `app.api`, no `app.services`. Each is therefore a pure
function of its inputs and runs in a test against a dict and a fake gateway.

---

## 3. Directory-by-directory

### `backend/app/api` — the edge
Nine routers under `v1/`: `auth`, `users`, `llm_configs`, `connections`,
`semantic`, `conversations`, `drafts`, `dashboards`, `reports`. Each router only
shapes HTTP: extracts the identity, validates the DTO (`schemas.py`, 1.2k
lines), and calls a service. Errors map to RFC 7807 `problem+json`
(`errors.py`). `main.py` is the ASGI factory — it wires CORS, a correlation-id
middleware (every response carries `X-Correlation-ID`), health probes, and a
lifespan that on boot bootstraps the admin user, reconciles orphaned runs, fails
stranded semantic jobs, resumes stranded report runs, then starts the
`LISTEN`/`NOTIFY` event listener and the claim poller.

Route ordering matters: literal paths (`/connections/test`, `/dashboards/import`)
are declared **above** their `/{id}` siblings.

### `backend/app/core` — cross-cutting
Config (pydantic-settings), structured logging with secret redaction, the error
hierarchy, correlation context, and a clock. No business logic.

### `backend/app/domain` — the pure core
`value_objects/` holds the enums the whole system speaks in: `Role`,
`UserStatus`, `DatabaseKind` (postgres/mysql/mssql/oracle, each with a
`sqlglot_dialect` and `default_port`), `RunStatus`, `StepName`, `StepStatus`,
`MessageRole`, `ArtifactKind`, `DashboardStatus`, `TileType`, `SqlOrigin`, the
seven `Report*` enums, `DisclosurePolicy` (NONE/AGGREGATE/SAMPLE/FULL),
`RunEventType`, and `HintBudget` with the `SENSITIVE_COLUMN_TOKENS` floor.
`ports/` holds the Protocols and their immutable dataclass value objects
(`SchemaSnapshot`, `TableInfo`, `ColumnInfo`, `RelationshipInfo`, `QueryResult`,
`ConnectionProbe`, `Completion`, …). `entities/` is intentionally empty —
persistent entities live as ORM rows in `infra/db/models.py`, and the domain
deliberately speaks in value objects, not ORM.

### `backend/app/services` — use cases
Where transactions and authorization live. `run_service.py` (1k lines)
orchestrates creating, claiming and driving a run; `report_service.py` (1.2k)
and `dashboard_service.py` do the same for their features.
`query_service.py` owns `execute_saved_sql` — the tile and report-block entry
point into guarded execution. `sql_draft_service.py` drafts a statement by
re-entering the pipeline's own nodes. `dashboard_transfer.py` is a dashboard as
a portable file (no ids, no results, no connection internals — and an imported
statement is hostile input like any other). `bootstrap.py` idempotently ensures
the admin account; `policy.py` builds the `GuardPolicy` for a connection.

> There is no `disclosure_service.py`. The disclosure gate is
> `pipeline/disclosure.py` (`disclose`, `disclose_history`, `SAMPLE_ROWS`), and
> the per-column hint gate is `HintBudget` in `domain/value_objects/`.

### `backend/app/pipeline` — the AI run
`state.py` defines the typed `RunState`, `NodeResult`, `RunError` and
`RetrievedContext.render` (the schema block every prompt embeds). `graph.py`
holds the **compiled LangGraph**, the `ORDER` list that is still the source of
truth for sequence, the shared repair region, and `_adapt` — the wrapper that
turns a node function into a graph node while keeping the old executor's duties
(the deadline check, the `seq` counter, the `run_steps` write, both `emit`
calls). `pipeline.py` is now a 21-line facade re-exporting `AnalyticsPipeline`
and `ORDER` so nothing above the pipeline had to change.

`nodes/__init__.py` holds all **ten** nodes:

```
route → retrieve → describe → clarify → generate → validate → execute →
inspect → present → chart
```

linear with **five non-chain edges**: three repairs *back* into `generate` (from
`validate`, `execute`, `inspect`) and two restores *forward* into `present`. A
hard ceiling of 24 transitions and a per-run deadline bound it. `describe`
answers a schema question from the schema block plus the semantic layer and
halts before any SQL is written. `prompts/` holds versioned prompt templates
(`PROMPT_VERSION`, currently **v7**); `disclosure.py` is the result gate;
`checks.py` is `inspect`'s token-free structural checks; `metadata.py` decides
which tables a schema question is about. A node crash is caught and recorded as
a *run failure*, never a bare 500.

Full node-by-node reference: [pipeline.md](pipeline.md).

### `backend/app/sqlguard` — the safety net
Self-contained (import-linter forbids it from importing infra/api/frameworks).
`validator.py` parses model-proposed SQL with SQLGlot and walks it against an
allowlist of expression types — an unknown node type is a **rejection**, one of
fifteen `E_*` rejection codes. `policy.py` carries the dialect-aware rules and
both allowlists; `rewriter.py` injects `LIMIT` and normalizes. Names are
resolved against the connection's stored schema snapshot, so an unsynced
connection can query nothing. The hostile corpus in
`tests/unit/test_sqlguard_hostile.py` is the build's hard gate — and it is
replayed through the other two entry points as well
(`test_query_service.py`, `test_report_guard.py`).

### `backend/app/semantic` — what the schema *means*
`models.py` (the document), `validate.py` (bind it to a snapshot, parse metric
SQL with SQLGlot), `generator.py` (build one with a model — a whole-schema
overview, then one call per table four concurrently, then a glossary),
`render.py` (the prompt block, scoped to the retrieved tables and capped at
`DEFAULT_MAX_CHARS = 8_000`), `prompts.py` (`SEMANTIC_PROMPT_VERSION`).
Self-contained like `sqlguard`.

### `backend/app/reports` — the written document
`outline.py` (the proposed structure and how many sections to ask for),
`language.py` (which language the request is in — derived from script counts,
never asked, no model call), `facts.py` (the arithmetic a paragraph needs,
computed exactly from the rows), `narrate.py` (the per-section prose prompt,
from *disclosed* results), `checks.py` (the numeric consistency check — pure,
token-free, Persian and Latin numerals), `prompts.py`
(`REPORT_PROMPT_VERSION`, currently **r4**). Self-contained, and below the
pipeline for the reason in §2.

### `backend/app/charts` — presentation
One 2.1k-line module. `profile_result` → `unchartable_reason` (the free veto,
run *before* the model call) → `plan_chart` (fit and repair) →
`compile_vega_lite`, plus `plan_kpi` for a big number. The model proposes a
constrained `ChartIntent`; the data gets the veto. See [charts.md](charts.md).

### `backend/app/eval` — the offline harness
`dataset.py` (record schema + fixture registry), `runner.py` (CLI + scorecard),
`metrics.py` (pure scoring), `suites/` (the **frozen** golden sets +
`CHANGELOG.md`), `reports/` (write-ups of past runs). It drives the *real*
pipeline against a testcontainers fixture, calls a real provider, and costs real
money — so it is not in `make test`, and an import-linter contract keeps it off
the request path entirely. See [eval.md](eval.md).

### `backend/app/infra` — the adapters
- `db/` — SQLAlchemy models (**27 tables**, §4), 14 Alembic migrations, async
  session factory.
- `repositories/` — query helpers over the ORM models.
- `connectors/` — `factory.py` maps each `DatabaseKind` to a connector; each of
  `postgres.py` / `mysql.py` / `mssql.py` / `oracle.py` implements
  `DatabaseConnector` (introspection + read-only execution + a genuine read-only
  probe). `hints.py` is the engine-neutral column-hint contract they share;
  `comments.py` is its sibling for catalog descriptions (`clean_comment`,
  `is_noise`, `SYSTEM_SCHEMAS`). Constraint introspection uses each engine's own
  catalog (`pg_catalog`, `sys.*`, `ALL_*`) rather than `information_schema`,
  which is privilege-filtered under a read-only role.
- `llm/` — LiteLLM behind `LLMGateway`, with the retry/backoff, the
  `response_format` fallback and the one structured repair. CI greps to prove
  `import litellm` appears nowhere else.
- `crypto/` — `SecretBox`: AES-256-GCM with row identity as additional
  authenticated data.
- `identity/` — the local Argon2id + JWT provider (access token + rotating
  refresh token with reuse detection; admin set-password revokes live sessions).
- `events/` — the SSE event bus plus the `LISTEN`/`NOTIFY` listener that carries
  events between replicas.

### `backend/app/workers` — running the work
`inprocess.py` runs the chat pipeline off the request thread with heartbeats,
claiming a run before executing it so two replicas cannot both run one;
`reconciler.py` sweeps runs whose process died, behind a transaction-scoped
advisory lock, so none is stuck `RUNNING`. `semantic.py` and `report.py` are the
minutes-long generation jobs — polled, not streamed, with cooperative-then-hard
cancel. `report_graph.py` is the compiled report graph; a full generation and a
per-section retry are two entries into it.

### `backend/tests`, `backend/fixtures`, `backend/scripts`
Siblings of `app/`, not inside it. `tests/` is unit + integration + eval (22k
lines). `fixtures/` holds the 42-table `sales` schema in three dialects, the
Oracle four-table comment fixture, the Sakila seed, `sales_comments.sql` (the
eval's commented arm) and `rebuild_fixtures.sh` (`make fixtures`).
`scripts/` holds `eval_run.sh` (rate-limit-tolerant eval wrapper),
`eval_seed_llm_config.py` (used by the nightly workflow) and `catalog_probe.py`
(what each engine will actually tell you about its own comments, from a
read-only role).

Repo-root `scripts/` is a different directory: `nginx-replicas.conf`,
`pg-ensure-runtime-dirs.sh` and `seed_demo_dashboard.py`.

### `frontend/src`
`api/client.ts` is the typed client, including SSE streaming with a polling
fallback and `Last-Event-ID` replay. `theme/tokens.ts` holds design tokens and
`DATABASE_TYPES`. `components/` has the primitive kit (`ui.tsx` — inputs, icons,
the puzzle-piece `Logo`, `ResultTable`, `Kpi`), chat (`chat.tsx` + the DOM-free
`chat-format.ts`), the Vega renderer and chart picker, dashboards
(`dashboard.tsx`, `tile-editor.tsx`, `dashboard-transfer.tsx`), the semantic
layer editor (`semantic.tsx`, 2.7k lines), reports (`report.tsx`, 4k lines, plus
`report-history.tsx`), and settings scaffolding. `pages/` are Login, Chat,
DataSources, LlmProviders, Users, Dashboards, Reports.

**Nine modules are deliberately DOM-free and carry their own tests**, because
every way they can be wrong is quiet: `dashboard-schedule.ts`, `table-format.ts`,
`dashboard-document.ts`, `palette.ts`, `chat-format.ts`, `report-document.ts`,
`report-readiness.ts`, `report-print.ts`, `semantic-drift.ts`.

---

## 4. Data model (ORM tables, `infra/db/models.py`)

**27 tables**, in seven groups.

| Group | Tables |
| --- | --- |
| Identity | `users`, `sessions`, `audit_logs` |
| Configuration | `llm_configs`, `database_connections`, `schema_snapshots` |
| Semantic layer | `semantic_layers`, `semantic_jobs` |
| Chat | `conversations`, `messages`, `runs`, `run_steps`, `generated_queries`, `query_executions`, `artifacts`, `run_events` |
| Dashboards | `dashboards`, `dashboard_tiles`, `dashboard_tile_cache` |
| Reports | `reports`, `report_sections`, `report_blocks`, `report_runs`, `report_block_results`, `report_section_results` |
| Eval | `eval_runs`, `eval_results` |

The ones worth knowing:

- `schema_snapshots` — the introspected schema per connection. **The guard's
  source of truth**: every table and column name in a generated statement is
  resolved against it, so an unsynced connection can query nothing. Since
  migration `0012` it also carries `catalog_meta`, the descriptions the target
  database's own catalog holds.
- `runs` — one pipeline execution per user question, with `model_snapshot`
  recording which connection and model it used. Since `0014` its
  `connection_id` and `llm_config_id` are nullable and `ON DELETE SET NULL`:
  deleting a data source must not destroy the transcript of questions it
  answered, and `model_snapshot` already carries the *names*, so a past answer
  stays explainable after the connection is gone.
- `run_events` — the durable, ordered event log with `UNIQUE(run_id, seq)`. It
  backs SSE replay by `Last-Event-ID`, the polling fallback, *and* cross-replica
  fan-out, which is why the `NOTIFY` payload can be just `run_id:seq`.
- `dashboard_tile_cache` — in Postgres rather than in-process, because an
  in-process cache goes stale per worker. Failures are cached too.
- `report_runs` + the two result tables — a run's status is **derived** from its
  sections, which is what makes progressive rendering and per-section retry need
  no resume machinery.

---

## 5. Request-to-answer flow

1. **Ask.** `POST /api/v1/conversations/{id}/messages`. `run_service.create_run`
   writes the user `message`, **flushes** (so the `runs` FK resolves), writes
   the `runs` row, and hands off to the `RunExecutor`, which claims the run
   before executing it.
2. **Route.** Classify intent, reading the recent turns once a thread has any.
   CHITCHAT and UNSUPPORTED halt here with a canned reply; METADATA continues.
3. **Retrieve.** Select tables (no model call), attach the semantic layer, build
   the block every downstream prompt embeds — gated by `HintBudget`.
4. **Describe.** A METADATA question is answered from that block — schema *and*
   semantic layer — streamed, and **halts before any SQL**. Every other intent
   is `SKIPPED` and costs nothing.
5. **Clarify.** May end the run by *asking*, at most once per exchange. Fails
   open: a provider error proceeds.
6. **Generate.** The LLM (via `LLMGateway`) *proposes* SQL. It never executes
   anything.
7. **Validate.** `sqlguard` parses and walks the SQL against the allowlist and
   resolves names against the snapshot. Reject → bounded `goto` back to Generate.
8. **Execute.** The dialect's `DatabaseConnector` runs it inside read-only
   containment (READ ONLY transaction where the engine supports it; role +
   timeout on SQL Server), with a statement timeout and a row cap.
9. **Inspect.** Structural checks over SQL + snapshot + result *shape* — never a
   result value, so they cost no tokens. At most one retry, and the superseded
   result is restored if it fails.
10. **Present.** `disclose()` gates the result, then the answer is streamed.
11. **Chart.** Fail-open. The data vetoes first, then the model proposes, then
    `plan_chart` decides. Terminal status `SUCCEEDED` (or `FAILED` /
    `TIMED_OUT` / `CANCELLED`; `NEEDS_CLARIFICATION` is deliberately not
    terminal).

Throughout, each step persists a `run_step` and emits an SSE `run_event`; the
SPA renders the **live step trail** so the user sees exactly what happened —
this is a deliberate product feature, not debug output.

---

## 6. Security & safety properties

- **SQL validation fails closed** — an unrecognized AST node is rejected, so a
  new SQLGlot release can only cause a false *rejection*, never a bypass.
- **Three entry points to the guard, none privileged** — the `validate` node, a
  dashboard tile, and a report block, with `execute_saved_sql` re-validating
  stored SQL against the *current* snapshot on every execution. (Import of a
  dashboard file is a fourth door into the same check.) `sql_origin` is
  provenance, never trust.
- **Read-only containment** per engine, verified by attempting a write inside a
  rolled-back transaction at connect time.
- **Credential encryption bound to row identity** — a ciphertext copied between
  rows fails to decrypt; no read DTO ever exposes a password or `api_key`.
- **Explicit disclosure policy** — each connection caps how much result data
  reaches the model, shown in the chat header at ask time, and it governs
  **three** things at render time: the result, the schema block's content hints,
  and the conversation transcript.
- **Auth** — Argon2id, short-lived JWT access tokens, rotating refresh tokens in
  an HttpOnly cookie with reuse detection.

Every claim above names the module that enforces it, and its limits, in
[security.md](security.md).

---

## 7. Build, run, test

```bash
make secrets && make up          # fresh keys, then the whole stack
# open http://localhost:5173 — sign in with ADMIN_EMAIL / ADMIN_PASSWORD

make targets   # the opt-in Oracle + SQL Server demo databases (~2GB RAM each)
make test      # backend suite        make guard    # hostile SQL corpus (hard gate)
make lint      # ruff + import-linter make migrate  # alembic upgrade head
make fmt       # ruff format          make fixtures # rebuild + verify the fixtures
make db-repair # recreate the empty PGDATA runtime dirs the studio drive strips
```

Frontend (from `frontend/`): `npm run dev`, `npm run build` (`tsc -b && vite
build`), `npm run typecheck` (`tsc --noEmit`), `npm run lint`, `npm test` (all
nine DOM-free logic suites, listed in §3).

**Without Docker**, if you would rather run the two processes yourself — you
still need a PostgreSQL for the app store, and `DATABASE_URL` pointed at it:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload

cd ../frontend
npm install && npm run dev
```

The eval harness is separate and calls a real provider, so it is not part of
`make test` — see [eval.md](eval.md).

**CI's non-negotiables** (`.github/workflows/ci.yml`): the hostile SQL corpus
passes with zero bypasses; the seven import-linter contracts hold; `import
litellm` appears only under `infra/llm/`; and `import langgraph` /
`langchain_core` appear only under `app/pipeline/` and `app/workers/`. The
frontend job runs `tsc --noEmit` and `vite build`.

> `npm test` is **not** in CI, and `mypy` runs with `|| true` — strict mode is
> being adopted module by module. Both are worth knowing before trusting a green
> tick.

---

## 8. Deliberately deferred

Several things once on this list have since been **built**: the semantic layer
(`app/semantic/`), clarification turns (the `clarify` node), model-authored
charts (`app/charts/`), FK-neighbour retrieval expansion, the eval harness
(`app/eval/`), Dashboards, Reports, and multi-replica execution
([cross-replica.md](cross-replica.md)).

**LangGraph is no longer deferred.** The chat pipeline is a compiled graph
(`app/pipeline/graph.py`), the repair region is one subgraph with two callers,
and the report worker is a graph too (`app/workers/report_graph.py`). The bet
that the node signatures were already the right shape paid: the ten node
functions were not modified. Two phases were argued and **declined** on
measurement rather than skipped — checkpointing (88 KB per node, 97% of it the
schema block, for a run of 5–60 seconds) and durable clarification. See
[langgraph-migration.md](langgraph-migration.md) for both arguments and the
gates.

Still deferred **on purpose**, with named triggers to revisit each in
[architecture.md](architecture.md): rolling conversation summaries, Celery +
Redis, dashboard filters (`QueryExecutor.execute` takes no bind parameters, and
never by string interpolation), scheduled report generation, and sharing a
dashboard or report with another user — which is an authorization model, not a
UI feature.

> Naming note: the product is **DataMind**; the Python package and compose
> project are still `raymand` (`import app.*`, `admin@raymand.local`).
