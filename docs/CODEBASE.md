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

Size, as of 2026-09-03: **41k lines of Python** under `backend/app/` plus 29k
lines of backend tests, and **33k lines of TypeScript/TSX** under
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
| Fonts | **All three self-hosted** from `public/fonts` — Inter, JetBrains Mono and **Vazirmatn** (Persian), declared as `@font-face` in `styles.css`. A BI tool pointed at a production database is routinely deployed behind a firewall that cannot reach `fonts.gstatic.com`, and there a printed Persian report — a deliverable — would come out in a fallback face with the wrong metrics. `report-print.test.ts` asserts no `googleapis`/`gstatic` reference survives anywhere |

Both dark and light palettes ship, but only one of them came from the design
concept: the **dark** tokens in `src/theme/tokens.ts` are copied verbatim from
`docs/assets/ui-design-concept.html`, which is dark-only. The **light** palette
was designed afterwards against it — warm "paper" neutrals at hue ~80, with a
plum accent (~315) drawn from the logo rather than the dark theme's blue.

There is **no test runner**: the eleven logic suites are plain `node
--experimental-strip-types` scripts over DOM-free modules. That is a deliberate
consequence of where the risk is — see §7.

### Infrastructure

**Docker Compose.** Six services, all of which start with the stack:

| Service | Image / build | Host port | Role |
| --- | --- | --- | --- |
| `db` | postgres:16-alpine | 5432 | DataMind's own application store |
| `sales` | postgres:16-alpine | 5433 | the **eval fixture** — the 42-table commerce schema, seeded read-only and messy on purpose |
| `sakila` | mysql:8.0 | 3307 | the second demo target — the classic Sakila sample |
| `aurora` | postgres:16-alpine | 5434 | the **demo** target — 13 clean tables whose cardinalities are tuned to the chart budgets in `app/charts/__init__.py`, so the obvious question yields an untrimmed chart |
| `api` | `./backend` | 8000 | runs migrations then Uvicorn |
| `web` | `./frontend` | 5173 | Vite dev server, proxies `/api` → `api:8000` |

The separate target instances exist on purpose: the whole point is that
DataMind reaches customer data *over a connector with a read-only role*, not by
sharing a database.

**`aurora` and `sales` are not interchangeable.** `aurora` is the *demo* — clean,
one obvious join path per question, `COMMENT ON` throughout. `sales` is the
*eval fixture* and its messiness is the point (near-duplicate names, soft-delete
traps, a stale rollup that gives wrong answers). Do not "clean up" `sales`: an
eval that never fails measures nothing.

**Oracle and SQL Server have no demo service.** Both were compose services
behind a `targets` profile and were removed — ~2 GB of RAM each for something
most sessions never started. Nothing about the *support* changed: both
connectors ship, both are covered by the guard corpus, and both remain
selectable engines. What changed is that verifying a connector change against a
live server now means supplying one — the seeds are still in
`backend/fixtures/` (`oracle/` and `sales_seed_mssql.sql`), and
`fixtures/rebuild_fixtures.sh` already starts its own throwaway containers, so
it is unaffected.

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

Eight contracts hold that shape (`[tool.importlinter]` in
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

Four packages are additionally **self-contained** by contract — `sqlguard`,
`semantic`, `reports` and `knowledge` — meaning no fastapi, no sqlalchemy, no
litellm, no `app.infra`, no `app.api`, no `app.services`. Each is therefore a
pure function of its inputs and runs in a test against a dict and a fake
gateway. `knowledge` carries one deliberate exception: `app.sqlguard` is *not*
on its forbidden list, because validating a taught template **is** calling the
guard, and the fifth entry point exists precisely so it reuses the same
`guard()` the other four do.

---

## 3. Directory-by-directory

### `backend/app/api` — the edge
Eleven routers under `v1/`: `auth`, `users`, `llm_configs`, `connections`,
`semantic`, `knowledge`, `conversations`, `drafts`, `dashboards`, `reports`,
`audit`. Each router only shapes HTTP: extracts the identity, validates the DTO
(`schemas.py`, 1.7k lines), and calls a service. Errors map to RFC 7807 `problem+json`
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
`knowledge_service.py` (1.5k) owns taught questions — guarded on save and
re-guarded on every use.
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

`nodes/__init__.py` holds all **eleven** nodes:

```
route → match → retrieve → describe → clarify → generate → validate → execute →
inspect → present → chart
```

linear with **six non-chain edges**: three repairs *back* into `generate` (from
`validate`, `execute`, `inspect`), two restores *forward* into `present`, and
`match`'s short-circuit to `validate`. A hard ceiling of 24 transitions and a
per-run deadline bound it. `match` answers a question somebody already taught —
no model call, trigram similarity plus a deterministic binder, landing on the
guard's own entry point so a stored template gets no exemption. `describe`
answers a schema question from the schema block plus the semantic layer and
halts before any SQL is written. `prompts/` holds versioned prompt templates
(`PROMPT_VERSION`, currently **v9**); `disclosure.py` is the result gate;
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

### `backend/app/knowledge` — what somebody already answered
A taught question is a **parameterized question→SQL template**, scoped to a
connection. `models.py` (the template, its three roles, and
`may_render_literals` — the disclosure rule its literals ride), `normalize.py`
(the match key), `params.py` (the AST walk that *offers* parameters from the
tree the guard already produced, and refuses the ones that are part of a
definition), `validate.py` (**the guard's fifth entry point**), `matcher.py` +
`bind.py` (the short-circuit, and the rule that any slot which will not bind
cancels the hit), `backlog.py` (what to teach next), `compare.py` (the
result-set comparator, shared with the eval and the benchmark),
`conflict.py` (which near-duplicate pairs are worth running), `embed.py`
(masked-question similarity — the vocabulary, the cosine, and the fingerprint
that makes staleness derived rather than tracked). Self-contained like
`sqlguard`, and allowed to call it. See
[learning-loop-plan.md](learning-loop-plan.md).

### `backend/app/semantic` — what the schema *means*
`models.py` (the document), `validate.py` (bind it to a snapshot, parse metric
SQL with SQLGlot), `generator.py` (build one with a model — a whole-schema
overview, then one call per table four concurrently, then a glossary),
`render.py` (the prompt block, scoped to the retrieved tables and fitted line
by line to `DEFAULT_MAX_CHARS = 8_000` — grain for every table first, then
metrics, then column meanings, round-robin), `prompts.py`
(`SEMANTIC_PROMPT_VERSION`).
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
- `db/` — SQLAlchemy models (**33 tables**, §4), 21 Alembic migrations, async
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
per-section retry are two entries into it. `knowledge_maintenance.py` keeps the
taught store from rotting — the staleness sweep (a parse) and the conflict
checker (which runs two near-duplicate templates through `execute_saved_sql`
and compares the rows; the one thing here that runs SQL without a person having
asked a question, and switchable off per connection). `benchmark.py` is the
customer's own accuracy number, scored by the deterministic comparator and
never by a model.

### `backend/tests`, `backend/fixtures`, `backend/scripts`
Siblings of `app/`, not inside it. `tests/` is unit + integration + eval (29k
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
layer editor (`semantic.tsx`, 2.9k lines), the knowledge/curation surface
(`knowledge.tsx`, 1.9k lines, plus the DOM-free `knowledge-template.ts`),
reports (`report.tsx`, 4k lines, plus `report-history.tsx`), and settings
scaffolding. `pages/` are Login, Chat, DataSources, LlmProviders, Users,
Dashboards, Reports and About.

**Eleven modules are deliberately DOM-free and carry their own tests**, because
every way they can be wrong is quiet: `dashboard-schedule.ts`, `table-format.ts`,
`dashboard-document.ts`, `palette.ts`, `chat-format.ts`, `report-document.ts`,
`report-readiness.ts`, `report-print.ts`, `semantic-drift.ts`,
`knowledge-template.ts`, `thinking.ts`.

---

## 4. Data model (ORM tables, `infra/db/models.py`)

**33 tables**, in nine groups.

| Group | Tables |
| --- | --- |
| Identity | `users`, `sessions`, `audit_logs` |
| Configuration | `llm_configs`, `database_connections`, `schema_snapshots` |
| Semantic layer | `semantic_layers`, `semantic_jobs` |
| Chat | `conversations`, `messages`, `runs`, `run_steps`, `generated_queries`, `query_executions`, `artifacts`, `run_events` |
| Dashboards | `dashboards`, `dashboard_tiles`, `dashboard_tile_cache` |
| Reports | `reports`, `report_sections`, `report_blocks`, `report_runs`, `report_block_results`, `report_section_results` |
| Knowledge | `knowledge_templates`, `knowledge_template_hits`, `answer_feedback` |
| Benchmarks | `benchmark_sets`, `benchmark_runs`, `benchmark_results` |
| Eval | `eval_runs`, `eval_results` |

The last two groups are separate on purpose. `benchmark_*` is the *customer's*
accuracy number over their own taught questions; `eval_*` is the frozen
developer suite. They share a vocabulary and one comparator
(`app/knowledge/compare.py`) — they share no table and no import, and a test
asserts the second on the parse, because the two would otherwise contaminate
each other within a month.

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
- `knowledge_templates` — a taught question as a *parameterized* question→SQL
  template, scoped to a connection and dying with it. `literal_provenance`
  decides whether its literals are structure or a disclosure;
  `knowledge_template_hits` logs every verdict including `OVERRIDDEN_BY_USER`,
  which is the honest measure of whether the short-circuit is trusted.
- `audit_logs` — defined in migration `0001` and written to by nothing until
  Phase 8 of the learning loop. `detail` holds identifiers and counts, never
  SQL, question text or result rows.

---

## 5. Request-to-answer flow

1. **Ask.** `POST /api/v1/conversations/{id}/messages`. `run_service.create_run`
   writes the user `message`, **flushes** (so the `runs` FK resolves), writes
   the `runs` row, and hands off to the `RunExecutor`, which claims the run
   before executing it.
2. **Route.** Classify intent, reading the recent turns once a thread has any.
   CHITCHAT and UNSUPPORTED halt here with a canned reply; METADATA continues.
2b. **Match.** Has somebody already answered this? No model call. A taught
   template above `SHORT_CIRCUIT_THRESHOLD` whose every slot binds jumps
   straight to Validate — the guard's own entry point, so it is re-validated,
   rewritten and row-capped like generated SQL. A miss changes nothing and the
   prompt is byte-identical.
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
- **Five entry points to the guard, none privileged** — the `validate` node
  (chat, and a taught template's short-circuit), `execute_saved_sql` (a
  dashboard tile and a report block, re-validating stored SQL against the
  *current* snapshot on every execution), `sql_draft_service` (the draft and
  hand-written roads), dashboard import, and a **knowledge template** (on save,
  and again on every use). The hostile corpus is replayed through each.
  `sql_origin` is provenance, never trust.
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

make test      # backend suite        make guard    # hostile SQL corpus (hard gate)
make lint      # ruff + import-linter make migrate  # alembic upgrade head
make fmt       # ruff format          make fixtures # rebuild + verify the fixtures
make db-repair # recreate the empty PGDATA runtime dirs the studio drive strips
```

Frontend (from `frontend/`): `npm run dev`, `npm run build` (`tsc -b && vite
build`), `npm run typecheck` (`tsc --noEmit`), `npm test` (all eleven DOM-free logic
suites, listed in §3). **`npm run lint` does not work** — the script exists but
eslint is neither a devDependency nor configured.

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
passes with zero bypasses; the eight import-linter contracts hold; `import
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
that the node signatures were already the right shape paid: the node functions
were not modified — and `match`, added afterwards, cost the wiring nothing.
Two phases were argued and **declined** on measurement rather than skipped —
checkpointing (88 KB per node, 97% of it the schema block, for a run of 5–60
seconds) and durable clarification. See
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
