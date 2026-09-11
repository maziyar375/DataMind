# CLAUDE.md — orientation for developers and agents

Read this before touching the code. **It is the map, not the territory**: where
things live, what must not break, and where the rest is written down — so you
can make a change without reading the whole codebase first.

Everything here is deliberately short. Each section names the document that
owns its subject, and **that document is the authority** — where this file and
a reference doc disagree, the reference doc is right.

| Question | Answer lives in |
|---|---|
| What is built? What is next? | [docs/status.md](docs/status.md) |
| What has already been decided, and why? | [docs/decisions.md](docs/decisions.md) |
| How do I run, test, verify? | [docs/development.md](docs/development.md) |
| How does the whole stack fit together? | [docs/reference/codebase.md](docs/reference/codebase.md) |
| Everything else | [docs/README.md](docs/README.md) — the index, and the routing table |

**The four you will reach for most:**

| | |
|---|---|
| [docs/reference/security.md](docs/reference/security.md) | **read before** changing `sqlguard/`, `disclosure.py`, `HintBudget`, or adding an LLM call site |
| [docs/reference/access-control.md](docs/reference/access-control.md) | **read before** writing any endpoint — it is the rulebook, and it is short |
| [docs/reference/pipeline-chat.md](docs/reference/pipeline-chat.md) | **read before** changing a node; its §0 maps all three pipelines |
| [docs/reference/codebase.md](docs/reference/codebase.md) | the code-grounded tour, when the map below is not enough |

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

Three surfaces sit on one guarded path: **Chat** (one question), **Dashboards**
(numbers kept current), **Reports** (a document).

> **Naming gotcha:** the product is *DataMind*, but the Python package is still
> `raymand` (import `app.*`), and so are the compose project and the app
> database. Renaming is a separate, deliberate task — don't do it incidentally.
> Two config values are caught in the middle of it and disagree between
> `.env.example` and the code; both are tabulated in
> [docs/status.md §7](docs/status.md#7-known-inconsistencies-in-the-repo-itself).
> **Read your `.env` rather than assuming either.**

---

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic,
  Pydantic v2 / pydantic-settings, structlog.
- **SQL safety:** SQLGlot (parse + AST allowlist, dialect-aware).
- **LLM provider access:** LiteLLM — *only* behind the `LLMGateway` port
  (`app/infra/llm/litellm_gateway.py`, the one module allowed to import it).
- **Run orchestration:** LangGraph — *only* inside `app/pipeline/` and
  `app/workers/`. Same bargain as LiteLLM: an import-linter contract and a CI
  grep hold the boundary. **LangChain is not a dependency** — the one
  `langchain_core` import is `RunnableConfig`, a type LangGraph pulls in.
- **Crypto/auth:** argon2-cffi (Argon2id), PyJWT, `cryptography` (AES-256-GCM).
- **Target DB drivers:** asyncpg, aiomysql, oracledb *thin*, pymssql. All ship
  wheels — no system DB client needed.
- **Frontend:** React 18 + TypeScript 5.6, Vite 5.4, react-router-dom 6,
  Vega-Lite for charts, `react-grid-layout` for the dashboard grid. That is the
  whole dependency list — the design system is custom, on oklch CSS variables,
  with **no component library**.
- **Dev/CI:** pytest + pytest-asyncio, ruff, mypy, **import-linter** (eight
  contracts), Docker Compose.

> **LiteLLM and LangGraph are different layers and neither replaced the other.**
> LiteLLM is the *provider adapter*; LangGraph is the *orchestrator*. How a
> provider row is configured, what parameters it may carry, and why there is no
> `kind` column: **[docs/reference/llm-providers.md](docs/reference/llm-providers.md)**.

---

## Commands

```bash
make test         # full backend suite, ~1,790 tests, well under a minute
make guard        # the hostile SQL corpus alone — the hard CI gate
make lint         # ruff + the eight import-linter contracts
make authz-check  # prove no module decides access for itself
make up / down / logs / secrets / migrate / fixtures / db-repair
```

From `frontend/`: `npm run typecheck`, `npm run build`, `npm test` (fifteen
suites). **`npm run lint` is a dead script** — eslint is neither a devDependency
nor configured.

**Verification loop before you claim done:** `make test` for backend, plus
`make guard` if you touched `sqlguard/` or a connector, plus `make authz-check`
if you touched anything permission-shaped; `npm run typecheck && npm run build
&& npm test` for frontend. Several past bugs only surfaced end-to-end via the
API, not in the UI — **actually exercise the path you changed.**

> The eval harness is **not** in `make test` — it calls a real provider and
> costs money. The commands, the environment's sharp edges (node is not on
> `PATH`; `.data/db` is a real database), the ports, the three demo databases
> and what CI does and does not gate are all in
> **[docs/development.md](docs/development.md)**.

---

## Code map

```
backend/app/
  main.py         ASGI factory: lifespan (bootstrap admin, reconcile orphans,
                  start reconciler), CORS, correlation-id middleware, health.
  api/            HTTP shape ONLY — no business logic.
    v1/           auth, users, connections, llm_configs, semantic, knowledge,
                  conversations, dashboards, drafts (SQL), reports
    deps.py       FastAPI dependencies (current user, session, settings)
    schemas.py    Pydantic request/response DTOs (no secrets ever in reads)
    errors.py     RFC 7807 problem+json mapping
  core/           config, logging (with redaction), errors, correlation context, clock
  domain/         entities, value_objects (enums/kinds, plus llm_params.py —
                  the per-provider catalog of documented request parameters,
                  read by BOTH the API that validates one and the gateway that
                  sends it), ports — ZERO I/O, no frameworks
    ports/        Protocols: database, llm, secrets, identity, events, run_executor
  services/       use cases + transaction boundaries: run_service,
                  semantic_service, knowledge_service (taught questions:
                  guarded on save and re-guarded on read), report_service,
                  dashboard_service,
                  dashboard_transfer (a dashboard as a portable file: no ids,
                  no results, no connection internals — and an imported
                  statement is hostile input like any other),
                  query_service (execute_saved_sql — the tile/report entry point
                  into guarded execution), sql_draft_service, bootstrap, policy
  pipeline/       the AI run: state.py (typed RunState), graph.py (the compiled
                  LangGraph + the node adapter), pipeline.py (the
                  AnalyticsPipeline facade over it),
                  nodes/ (route→match→retrieve→describe→clarify→generate→
                  validate→execute→inspect→present→chart — all eleven are
                  functions in the single file nodes/__init__.py; there is no
                  module per node. `match` is the short-circuit: a taught
                  question skips four nodes and lands on the guard),
                  contracts.py (the node signature),
                  metadata.py (which tables a schema question is about, and the
                  rendered fallback answer),
                  prompts/, disclosure.py (result gate), checks.py (free result checks)
  sqlguard/       policy, validator, rewriter — self-contained, dialect-aware
  knowledge/      what somebody already answered: models.py (the template,
                  the three roles, the disclosure rule), normalize.py (the match
                  key), params.py (the AST walk that offers parameters and
                  refuses the rest), validate.py (the guard's fifth entry
                  point), matcher.py + bind.py (the short-circuit and the
                  cancel-on-unbound rule), backlog.py (what to teach next),
                  compare.py (the result-set comparator — moved down from
                  app/eval so the conflict checker and the benchmark share one
                  set of tolerances), conflict.py (which pairs are worth
                  running, and with what values), embed.py (masked question
                  similarity: the vocabulary, the cosine, and the fingerprint
                  that makes staleness derived rather than tracked) —
                  self-contained like sqlguard,
                  and allowed to call the guard because that is what it is for
  semantic/       what the schema *means*: models.py (the document), validate.py
                  (bind it to a snapshot, parse metric SQL), generator.py (build
                  one with a model, one call per table), render.py (the prompt
                  block), prompts.py — self-contained like sqlguard
  reports/        the written document: outline.py (the proposed structure,
                  and how many sections to ask for), language.py (which
                  language the request is in — derived, never asked),
                  facts.py (the arithmetic a paragraph needs, computed exactly
                  from the rows), narrate.py (the per-section prose prompt, from
                  *disclosed* results), checks.py (the numeric consistency check
                  — pure, token-free, Persian and Latin numerals), prompts.py
                  (REPORT_PROMPT_VERSION) — self-contained, below the pipeline
  charts/         ChartIntent → result profile → shape fit → Vega-Lite. One file
                  (__init__.py), like pipeline/prompts/ — the budget constants
                  live at its top and `aurora` is seeded against them
  eval/           the offline harness — runs the REAL pipeline against a
                  testcontainers fixture: dataset.py (record schema + fixture
                  registry), runner.py (CLI + scorecard to eval_runs/
                  eval_results), metrics.py (pure scoring), suites/ (the FROZEN
                  golden sets + CHANGELOG), reports/ (past run write-ups).
                  Costs real money; not in `make test`. See docs/reference/eval.md
  infra/          adapters implementing the ports:
    db/           SQLAlchemy models.py + Alembic migrations + session
    repositories/ query helpers over the ORM models
    connectors/   factory + postgres/mysql/mssql/oracle (one DatabaseConnector each)
                  + hints.py: the engine-neutral column-hint contract they share
    llm/          LiteLLM behind LLMGateway
    crypto/       SecretBox (AES-256-GCM)
    identity/     local Argon2id + JWT provider
    events/       SSE event publisher
  workers/        inprocess run executor (claims a run before executing it, so
                  two replicas cannot both run one) + stale-run reconciler
                  (behind a transaction-scoped advisory lock) + semantic.py
                  and report.py (generation jobs; minutes long, so they are
                  polled not streamed, with cooperative-then-hard cancel) +
                  report_graph.py (the compiled report graph; a full generation
                  and a per-section retry are two entries into it) +
                  knowledge_maintenance.py (store health: the staleness sweep,
                  and the conflict checker that runs two near-duplicate
                  templates and compares the rows — never on a request path,
                  switchable off per connection) + benchmark.py (the customer's
                  own accuracy number: the real pipeline per question, the gold
                  executed through the guard, labels from the comparator and
                  from no model)

backend/           ← these are SIBLINGS of app/, not inside it
  tests/          unit (incl. test_sqlguard_hostile.py) + integration + eval
                  (test_golden_set.py and the dual-form verify artifact)
  fixtures/       sales_seed.sql (the Postgres EVAL fixture) +
                  sales_seed_mysql.sql and sales_seed_mssql.sql dialect mirrors
                  + rebuild_fixtures.sh (`make fixtures`); each a wide,
                  deliberately-messy 42-table commerce schema with a read-only
                  role, built to *exceed* the retrieve budget — which at the
                  shipped ceiling it no longer does: the budget was raised
                  24k → 50k and the fixture estimates 26,480, so a default run
                  is entirely FULL_SNAPSHOT and recall is 1.0 by construction.
                  `--retrieve-budget CHARS` lowers the ceiling for one run,
                  which is how recall is measured now (docs/reference/eval.md §1 — read it
                  before quoting a recall number).
                  sales_comments.sql is the eval's commented arm and
                  sales_semantic.json its semantic-layer arm.
                  demo_seed.sql + demo_comments.sql are `aurora`, the DEMO
                  fixture — a different artifact for a different job, tuned to
                  the chart budgets (see "The three demo databases" above).
                  mysql/ holds the Sakila seed and oracle/ the four-table
                  COMMENT ON fixture
  scripts/        eval_run.sh (rate-limit-tolerant eval wrapper) +
                  eval_seed_llm_config.py (used by the nightly workflow) +
                  catalog_probe.py (what an engine will actually tell you about
                  its own comments, from a read-only role)

scripts/           repo root, not backend/: nginx-replicas.conf (the two-replica
                  balancer), pg-ensure-runtime-dirs.sh (`make db-repair`'s
                  in-container half), seed_demo_dashboard.py — builds the
                  29-tile demo board over `sales` by talking to the running
                  API rather than the database, so every tile it creates went
                  through sqlguard exactly as a typed one would (`--check` runs
                  them all; credentials from ADMIN_EMAIL/ADMIN_PASSWORD)

frontend/src/
  main.tsx, App.tsx        entry + router/layout
  theme/tokens.ts          design tokens (oklch), DATABASE_TYPES, PROVIDER_URLS
                           (the provider picker's options), dark+light palettes
  api/client.ts, types.ts  typed client, SSE streaming + polling fallback
  components/               ui.tsx (primitives, icons, Logo, ResultTable),
                            VegaChart.tsx (the renderer), chart-picker.tsx
                            (shared by chat, report and tile-editor — in chat it
                            drives POST /runs/{id}/chart, which redraws the
                            run's stored TABLE artifact and never re-queries),
                            palette.ts (+ .test.ts — `npm run test:palette`),
                            chat.tsx, chat-format.ts (the three markdown
                            constructs a model writes anyway — bold, `code`,
                            bullets — read at display time into spans, never
                            into markup; `npm run test:chat`),
                            settings.tsx, semantic.tsx (the layer
                            editor), semantic-drift.ts (an all-or-nothing
                            re-key told apart from ordinary drift —
                            engine-neutral detection, Oracle-specific
                            explanation; `npm run test:drift`),
                            dashboard.tsx (grid + tile shell + the
                            one-tick refresh scheduler), dashboard-schedule.ts
                            (the due-tile rule, DOM-free, + its .test.ts —
                            `npm run test:schedule`), table-format.ts (how a
                            configured table resolves/sorts/formats, the sort a
                            reader layers over that by clicking a heading, and
                            the CSV it downloads — DOM-free,
                            `npm run test:format`),
                            dashboard-document.ts (reading an exported file:
                            what it is, and which connection each of its
                            databases is here — `npm run test:document`) +
                            dashboard-transfer.tsx (the download and the import
                            dialog), tile-editor.tsx
                            (ask or write the SQL; one guard check for both),
                            knowledge-queue.ts (how much curation work is
                            waiting, per connection and in total — DOM-free,
                            `npm run test:queue`), provider-params.ts (the
                            translation between a generated form field and the
                            JSON value a provider's API takes — DOM-free,
                            `npm run test:params`), notifications.tsx (the
                            shell's one aria-live surface),
                            report.tsx (the outline editor + the document
                            viewer), report-history.tsx, report-document.ts
                            (merging a run into a document — `npm run
                            test:report`), report-readiness.ts (what generating
                            an outline now would produce — what the Generate
                            preflight says, `npm run test:readiness`),
                            report-print.ts (the print handoff: fonts, and
                            redrawing charts at page width —
                            `npm run test:print`)
  pages/                    Login, Chat, DataSources, LlmProviders,
                            Dashboards, Reports, Knowledge (`/knowledge` — the
                            curation console promoted out of a connection's
                            fourth tab; `KnowledgeTab` behind a connection
                            picker, reached from the rail or from the Data
                            sources tab that now points here rather than
                            rendering a second copy; the rail's only count
                            badge — red for a flag somebody raised, amber for
                            a backlog), Admin (`/admin` — master-detail over
                            six tabs: People, Service accounts, Teams, Roles,
                            Access review, Audit. `/users` redirects here, to
                            /admin/people), Account (`/settings` — your own
                            display name and password, the only two things a
                            member may change about themselves), About (who
                            built it — the one page reachable from both sides
                            of the sign-in wall: the rail's footer group when
                            signed in, a link on the login screen when not;
                            portraits come from public/team/ and fall back to
                            an initial)
```

---

## The dependency rule (enforced, not documented)

```
api → services → pipeline → reports → semantic → domain ← infra
```

`import-linter` fails CI on violation (`make lint`). Concretely:

- **`app.domain` imports no framework and no infra** — no fastapi, sqlalchemy,
  litellm, `app.infra`, `app.api`, `app.services`. Keep it pure.
- **`app.sqlguard` is self-contained** — no fastapi/sqlalchemy/litellm/infra/api.
- **`app.semantic` is self-contained** for the same reason — it is a pure
  function of a snapshot, a document and the `LLMGateway` *port*, so the whole
  generator runs in a test against a dict and a fake gateway.
- **`app.knowledge` is self-contained** on the same terms, with one deliberate
  exception in the forbidden list: `app.sqlguard` is *not* forbidden, because
  validating a template is calling the guard and the fifth entry point exists
  precisely so it reuses the same `guard()` the other four do.
- **`app.reports` is self-contained** on the same terms, and the contract buys
  something concrete: `narrate.py` *cannot* call `disclose()`, because that
  lives in `app.pipeline` above it. So the worker has to disclose results
  under the policy in force at narration time and hand them down — which is
  the stricter reading of invariant #4, enforced for free.
- **`langgraph` stays in the orchestration layer** — `app.domain`,
  `app.sqlguard`, `app.semantic`, `app.reports`, `app.charts` and `app.api` may
  not import `langgraph` or `langchain_core`. The contract sets
  `allow_indirect_imports = true` **on purpose**: `app.api → app.services →
  app.pipeline.graph → langgraph` is a real chain and is not a violation, since
  the rule is that those packages do not *know* about langgraph. `app.reports`
  is in that list deliberately — the report graph belongs in `app/workers/`.
- Services may reach into infra (that carve-out is explicit in the config).

Ports & adapters exist at **exactly four** seams — the four things most likely
to be replaced: **LLM, target database, secrets, run execution.** Add adapters
behind these ports; don't route around them. In particular: **never `import
litellm` outside `app/infra/llm/`**, and **never `import langgraph` outside
`app/pipeline/` and `app/workers/`** — CI greps for both.

There are **eight** import-linter contracts now; `knowledge is self-contained`
joined them with Phase 1 of the learning loop.

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
   chat header shows the policy in force *at ask time*. The policy governs
   **three** things, all in `pipeline/disclosure.py` except the second:
   `disclose()` gates the result, `HintBudget` (`domain/value_objects`) gates
   the per-column content hints in the schema block, and `disclose_history()`
   gates the **conversation** — the assistant message is prose the model wrote
   *from* result rows, and the next turn sends it back. All three filter at
   *render* time, never only at write time, so tightening a policy takes effect
   on the next question without a re-sync and without a leak from the
   transcript. Under `SAMPLE`/`FULL` the history filter is the identity
   function; under `NONE`/`AGGREGATE` an earlier answer's prose is withheld
   while its **SQL survives**, which is what a follow-up actually builds on.
   A conversation is pinned to one connection (`_bind_connection`) so history
   can never cross policies. The sensitive-name floor (`is_sensitive_column`)
   applies at capture under every policy, including FULL, because the schema
   block is sent on every question while a result is only sent for the query
   the user asked for.
5. **One place answers "may they?", and it is the `Authorizer` port.**
   Nothing under `api/` or `services/` compares an owner id or reads a role
   string to decide anything: a single row asks `authz.allowed(ctx, ref,
   privilege)`, a list composes `authz.visible(...)` into the `SELECT` it was
   already going to run, and an app-wide verb is a route dependency,
   `deps.needs(capability)`, which runs before the handler body and so cannot
   be forgotten by the next route. Background work names its principal through
   `RequestContext.on_behalf_of(owner)` — **there is no god context.**

   The answer is computed from **five facts and there is no sixth**: ownership,
   a direct grant, a team grant, a role's scoped privilege, a wildcard grant.
   The lattice (`manage ⊃ delete ⊃ modify ⊃ select ⊃ describe`) is **data,
   expanded at read time**, so changing it never needs a backfill.

   Three things that are easy to regress: **`manage` is not implied by
   `modify`** (editing a connection's credentials and deciding who else may
   read through it are different acts); **there is no administrator arm** — if
   you are adding `if ctx.is_admin` to a decision, that is the thing this model
   exists to not have; and **404 above 403, in one place** (`services/policy.require`)
   — a second copy turns a list endpoint into an existence oracle.

   > **[docs/reference/access-control.md](docs/reference/access-control.md) is
   > the rulebook — read it before writing an endpoint.** Seven concepts, five
   > invariants, the algorithm verbatim, and a checklist each for a new
   > endpoint, a new resource type and a new capability. It is short on
   > purpose. `make authz-check` and
   > `backend/tests/unit/test_authz_conformance.py` enforce every rule in it a
   > machine can check;
   > [docs/plans/user-management-and-access-control.md](docs/plans/user-management-and-access-control.md)
   > is the argument behind all of it.

---
## Three pipelines, one set of nodes

There are **three** pipelines in this product, and only one of them is a state
machine. Know which you are in before you go looking for an executor that does
not exist. [docs/reference/pipeline-chat.md](docs/reference/pipeline-chat.md) §0 is the full map.

| | **Chat** | **Dashboard** | **Report** |
|---|---|---|---|
| Orchestrator | `AnalyticsPipeline` — a compiled LangGraph | a service function + `asyncio.gather` | `ReportRunExecutor` + `report_graph.py` |
| Shape | streamed (SSE), 5–60s | request/response, sub-second on a cache hit | queued (**202**) + polled, minutes |
| Model runs | **at ask time**, every time | **at authoring time only** | at authoring *and* generation time |
| SQL comes from | `generate`, fresh per question | `dashboard_tiles.sql`, stored | `report_blocks.sql`, stored |
| Guard entry | the `validate` node | `execute_saved_sql` | `execute_saved_sql` |
| Result values → model | `present`, per policy | **never** | `narrate`, per policy (`NONE`/`AGGREGATE` refused) |
| Failure posture | the run fails | a per-tile `ERROR` **value** | per section; run status is **derived** |

**The guard has five entry points and none is privileged:** the `validate` node,
`execute_saved_sql` (tiles *and* report blocks), tile save, dashboard
import, and **knowledge templates** (save *and* every use). The hostile corpus
is replayed through each (`test_sqlguard_hostile.py`, `test_query_service.py`,
`test_report_guard.py`, `test_dashboard_transfer.py`,
`test_knowledge_guard.py`). The moment one door is special, the guarantee is
gone.

**`retrieve` → `generate` → `validate` is written down once.** It is one
compiled region — `_add_repair_region` in `pipeline/graph.py` — built by both
`CHAT_GRAPH` and `DRAFT_GRAPH`, so a stored statement anywhere in the product
was written against the same schema block, the same semantic layer, the same
`_SQL_RULES` and the same guard as a chat answer. `sql_draft_service.draft_sql`
is the caller for both a dashboard tile and a report block. **Do not grow a
second executor over these nodes** — one existed, its `deadline_at` was enforced
on the chat path and inert on the draft path, and nobody noticed until Phase 2
deleted it.

Its three opt-ins are deliberately **not** uniform, each decided by where its
answer lands:

| Flag | On for | Off for | Because |
|---|---|---|---|
| `classify` | report blocks | tiles | a block's answer is stored and read months later; a tile's preview is in front of the person who asked |
| `compose_chart` | tiles | report blocks | the tile editor has a chart-type picker a suggestion can pre-select; the block editor has none and would discard the answer unread |
| `tile_type` | tiles **and** report blocks | chat | both store a statement that will be drawn as a big number; chat declares no destination |

A draft also inherits **no** history (`[]`), **no** events (`_no_emit`), **no**
persistence, and a shorter deadline of its own (`DRAFT_DEADLINE_SECONDS`).

### The five failure postures

Every error path in every pipeline is one of five. Naming them is worth more
than any individual handler, because *"what should this do when it breaks?"* is
answered by asking which posture the step belongs to.

| Posture | Means | Where |
|---|---|---|
| **Fail closed** | the refusal *is* the answer | the guard, name resolution, an unsynced connection, `disclose*` defaulting to the narrowest policy, reports refusing `NONE`/`AGGREGATE` |
| **Fail open** | the feature is dropped, the work continues | `route`, `clarify`, `inspect`, `chart`, the semantic layer, follow-up suggestions |
| **Fail backwards** | something computed replaces something generated | `describe` → `answer_metadata`, `present` → the fallback sentence, `plan_chart` → the shape heuristic |
| **Fail as a value** | the failure is data, stored or returned, not raised | `TileResult(status="ERROR")`, `ReportBlockResult(FAILED)`, `feasibility_status = INFEASIBLE` |
| **Fail the run** | stop, record, tell the user | `E_LLM`, a guard rejection out of budget, `E_TIMEOUT`, `E_NODE_FAILED`, `E_PIPELINE_LOOP`, `E_ORPHANED` |

Two rules keep them honest:

- **A step that has already produced correct data may not lose it to a
  presentation failure.** `TEXT_RESET` exists for this (deltas are already on
  the live bus *and* durably stored for replay, so discarding a buffer is not
  enough); `_restore_superseded` exists for this; caching a failed tile result
  exists for this.
- **A fail-open step may never widen anything.** `route` failing open to
  ANALYTICAL cannot skip the guard; `clarify` failing open cannot bypass
  disclosure; a missing policy argument always renders the *narrowest* block.

---

## How a run works

> Node by node — what each does, its exact logic, the prompts it sends, the
> control-flow rules and the LangGraph port map:
> **[docs/reference/pipeline-chat.md](docs/reference/pipeline-chat.md)**. Read
> that before changing a node. The other two pipelines get the same treatment
> in [pipeline-dashboard.md](docs/reference/pipeline-dashboard.md) and
> [pipeline-report.md](docs/reference/pipeline-report.md).

`POST /conversations/{id}/messages` → `run_service.create_run` writes the user
`message`, **flushes**, then the `runs` row (FK order matters), and hands off to
the in-process executor. `AnalyticsPipeline.run` invokes a **compiled
LangGraph** (`pipeline/graph.py`) whose chain is linear with one bounded repair
loop:

```
route → match → retrieve → describe → clarify → generate → validate →
execute → inspect → present → chart
```

**Five edges are not the chain**, and they are why this is a graph and not a
list: three repairs **back** into `generate` (from `validate`, `execute` and
`inspect`) and two restores **forward** into `present` (via
`_restore_superseded`, skipping `execute` and `inspect`).

Six facts that decide how a change lands:

- **The adapter owns the plumbing, not the nodes.** A node names a label in
  `NodeResult.goto`; `graph.py` routes it, and the adapter — not the node —
  owns the deadline check, the `seq` counter, the `run_steps` write and both
  `emit` calls. That is what keeps the SSE sequence identical.
  `test_pipeline_events.py` is that contract; `test_pipeline_graph.py` is the
  wiring's.
- **`match` can end the run without a model call.** A taught question skips
  four nodes and lands on the guard. See
  [knowledge-templates.md](docs/reference/knowledge-templates.md).
- **`describe` halts before any SQL**, answering METADATA questions from the
  schema block and the semantic layer. Every other intent gets `SKIPPED`.
- **`clarify` is the one node that can end a run by asking**, and it **fails
  open**. At most once per exchange, enforced in `run_service` rather than
  trusted to the model.
- **`inspect` reads SQL, snapshot and result *shape* — never a result value**,
  so it costs no tokens and behaves identically under every disclosure policy.
- **`chart` is fail-open, the opposite of the guard.** The veto runs *before*
  the model call, so an unchartable result costs nothing.

Terminal states: `SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`.
`NEEDS_CLARIFICATION` is deliberately **not** terminal. A node crash is recorded
as a run failure, never a bare 500; a process that dies mid-run is healed by the
reconciler plus a startup sweep.

**More than one API replica is supported**, and three rules make it work — a run
is claimed before it is executed, cancelling is a row rather than a task handle,
and events cross processes over `LISTEN`/`NOTIFY` on the transaction that writes
them. Read
[docs/reference/cross-replica.md](docs/reference/cross-replica.md) before
touching any of the three.

---

## The curated documents

Two things a person writes that change what the model sees. Neither is
described here — each has its own reference:

| | What it is | Reference |
|---|---|---|
| **The semantic layer** | What the schema *means* — business names, grain, metrics bound to exact SQL, time conventions, fan-out cautions. One document per connection | [docs/reference/semantic-layer.md](docs/reference/semantic-layer.md) |
| **Knowledge templates** | A question somebody already answered correctly, stored as a parameterized question→SQL template so the system answers it the same way next time | [docs/reference/knowledge-templates.md](docs/reference/knowledge-templates.md) |

Both are **off-by-absence**: with neither present, the prompt is byte-identical
to what it was before the features existed. That is what makes an A/B possible,
and it is a property to preserve rather than an accident.

---

## The three surfaces

Each has a reference doc; what follows is only what you can break from outside
it.

**Dashboards** ([docs/reference/dashboards.md](docs/reference/dashboards.md)) —
a grid of tiles, each a saved query on its own connection and refresh rate.

- **Nothing calls a model at refresh time.** The most load-bearing "no" in the
  product: a dashboard keeps working after the provider key is revoked. A model
  runs at *authoring* time only.
- **A tile failure is a *value*, not an exception.** One broken tile must never
  fail the dashboard response.
- **A tile is the second entry point into guarded execution and gets no
  exemption** — stored SQL is re-validated against the connection's *current*
  snapshot on every execution.
- **Containment is the connection's, not the tile's.** An override may only
  *lower* `max_rows` and `statement_timeout_ms`.

**Charts** ([docs/reference/charts.md](docs/reference/charts.md)) —
`profile_result → unchartable_reason → [model proposes] → plan_chart →
compile_vega_lite`. Every surface decides its picture with the same planner.

- **The model proposes; the platform decides.**
- **The veto runs before the model call**, so a hopeless result costs zero
  tokens.
- **Prompt/type parity.** A chart type is added when `CHART_SYSTEM` describes
  when to pick it *and* `ResultProfile.describe()` carries the facts that rule
  is stated in terms of. **A bullet describing behaviour the code no longer has
  is a bug in the prompt.**
- `PROMPT_VERSION` does **not** move for chart-prompt changes — nothing on the
  SQL-producing path changed. Same convention for `CLARIFY_SYSTEM` and
  `DESCRIBE_SYSTEM`.

**Reports** ([docs/reference/reports.md](docs/reference/reports.md)) — a
structure a human approved, prose written over real results, re-runnable months
later. Six tables (`0008`), no table and no code path shared with Dashboards.

- **Numbers come from the rows, not the model.** `plan_kpi` computes the
  headline; `reports/facts.py` computes what a paragraph needs; `checks.py`
  verifies what the prose says against the rows.
- **Reports refuse `NONE`/`AGGREGATE` disclosure**, at creation *and* at every
  generation.
- **A run's status is derived from its sections**, which is what makes
  progressive rendering and per-section retry fall out for free.

---

## Proving a change helped

> The golden set, every metric, the CI gate, and how to read a result honestly:
> **[docs/reference/eval.md](docs/reference/eval.md)**. The numbers as they
> stand, and how to quote them without lying:
> **[docs/status.md §6](docs/status.md#6-the-numbers-and-how-to-read-them)**.

`app/eval/` runs the **real** pipeline against a fresh fixture database in a
throwaway container. It calls a real provider, so it costs money and is not in
`make test`; an import-linter contract keeps `app.eval` off the request path.

Four rules that matter more than any number it prints:

1. **The golden set is frozen.** Questions are *never* edited to make a score go
   up. `gold_sql` is corrected only when demonstrably wrong, logged in
   `suites/CHANGELOG.md` with the evidence. **An eval you are allowed to edit
   measures your willingness to edit it.**
2. **Golds are checked against something other than themselves** — each record
   has a structurally different twin. Adding a question means adding its twin.
3. **The baseline file is model-specific.** Read its `_README` before quoting
   it, and never put two numbers from different models in one sentence.
4. **Retrieval recall is 1.0 by construction at the shipped ceiling.** Lower it
   with `--retrieve-budget` to measure it — a runner flag, never a code edit —
   and never compare a lowered-budget figure to a full-snapshot one.

**Two prompt changes have been measured to *lower* accuracy**, and both are
recorded where someone would otherwise repeat them: a "getting the answer right"
block in `GENERATE_SYSTEM` (36% → 26%), and making `C_NULLABLE_INNER_JOIN`
retry-eligible (0 wins / 4 losses). **More instruction is not better here.**

---

## Frontend conventions (nothing enforces these — the tree is just consistent)

`npm run lint` is a dead script and `npm test` is not in CI, so every rule
below is held up by the code agreeing with itself. Breaking one costs nothing
at commit time and shows up as drift a release later. Full tour:
[docs/reference/frontend.md](docs/reference/frontend.md).

- **Every screen has a URL, and the router is a *data* router.** `main.tsx`
  mounts `createBrowserRouter` — not `<BrowserRouter>` — because `useBlocker`
  exists only on that one, and it is what stops a navigation out of a dirty
  form. `App.tsx` holds the rail (`NAV`) and the route table; each section owns
  a `/*` path and reads its own sub-routes with `useMatch` rather than nesting
  a second `<Routes>`, so the section stays mounted across open and close (a
  remount would drop a chat's live stream). A new section is a `NAV` entry and
  a `<Route>`. Unknown paths redirect to `/chat`, and **whatever serves the
  build must return `index.html` for unknown paths.**
- **Hydrate a form from the row, not from the URL.** A detail page whose form
  is filled from a list must key that effect on `selected?.id`, never on the id
  in the path: on a deep link the path has an id before the list has arrived,
  so a URL-keyed effect fires once against `null`, bails, and leaves a blank
  form claiming unsaved changes — with the navigation guard then refusing to
  let anyone leave it. The *id*, not the row: the row is a new object after
  every list refresh and would re-hydrate over what was typed.
- **A page asks the shell for what is not its own.** `shell.tsx`:
  `useThemeOverride` (a dashboard pinned to a theme — `App` resolves
  `override ?? the user's choice` and is the only caller of `applyTheme`) and
  `useUnsavedWork(key, reason, within?)` (a dirty form, which the shell's one
  blocker asks about before letting a navigation through). `within` is the
  address the work survives inside — pass a record's own path when its tabs
  are routes, or the guard stops a form from reaching the tab beside it to
  protect edits a tab switch does not touch.
- **Long work announces itself; in-page errors stay put.** `shell.tsx`'s
  `useNotify` raises a `Notice` in the shell's `aria-live` corner, and
  `useBackgroundWatch` hands the shell a `{key, poll}` so a job outlives the
  page that started it (semantic generation, benchmark runs). It is for
  events that outlive their screen — **not** an error channel: a failed save
  still says so in an `ErrorNote` beside the button that failed, and moving
  those would make every failure less legible.
- **Connections are two things, and the screen says so.** `/sources/:id`
  opens **Connection** (host, port, database, user, password, SSL, schema
  allowlist, and the Danger zone) and `/sources/:id/policy` opens **Policy**
  (disclosure, DB comments, taught examples, clarify, conflict checks, row cap,
  timeout — the set is `POLICY_KEYS` in `DataSourcesPage.tsx`). The strip's
  fifth entry, Knowledge, is a **door**: it navigates to `/knowledge/:id`,
  where the console actually lives, and `/sources/:id/knowledge` redirects
  there. Each of the two forms has its
  own dirty state and its own Save, and each Save sends **only its own half**,
  so two people editing two tabs cannot overwrite each other. `Test connection`
  belongs to Connection, which is the only half it probes.
- **Styling is two places, and which one is not a preference.** Layout,
  spacing and one-off values are inline `style={}` in the JSX. Anything a
  style attribute cannot express — `:hover`, `:focus-visible`, selection,
  keyframes, `@media print`, the responsive reflow — is an `.rm-*` class in
  `styles.css`. There are no CSS modules and no utility classes.
- **No component library, and no new dependency to get one.** The whole list is
  React, `react-grid-layout` (a layout engine, not components), Vega, and
  `react-router-dom`. Primitives live in `components/ui.tsx`; the
  master–detail frame that Data sources and LLM providers share is
  `components/settings.tsx`. Compose from those before writing a new one.
- **Below 700px no page has two fixed columns.** The chat list and the
  settings master column become off-canvas drawers beside the collapsed rail
  (`components/list-drawer.tsx`: `useListDrawer`, `ListToggle`, `ListScrim`),
  closing on Escape, on the scrim, and on any navigation — which is what
  "close on select" means when every list here navigates. A page with a
  second column gets a `ListToggle` in its header and passes `open` to the
  column; that is the whole contract.
- **Never hardcode a colour.** Every value comes from a CSS variable defined in
  `theme/tokens.ts`, which ships a **dark and a light** definition for each.
  A literal hex or `oklch()` in a component is a bug in both themes — one of
  them just has not been looked at yet. Chart colours are the one exception and
  they live in `components/palette.ts`, tested apart from React.
- **The fourteen DOM-free modules must stay DOM-free.** `dashboard-schedule.ts`,
  `table-format.ts`, `dashboard-document.ts`, `palette.ts`, `chat-format.ts`,
  `report-document.ts`, `report-readiness.ts`, `report-print.ts`,
  `semantic-drift.ts`, `semantic-metrics.ts`, `knowledge-template.ts`,
  `thinking.ts`, `knowledge-queue.ts`, `provider-params.ts` — they hold the
  logic whose failures are quiet, they are (with `scripts/permissions.test.ts`,
  the fifteenth suite) the *only* tested code in the frontend, and their suites
  are plain `node --experimental-strip-types` scripts. **One React import turns
  a suite into a thing that cannot run.**
- **Text a person wrote gets `dir={dirOf(value)}`.** The product ships Persian.
  SQL is always `dir="ltr"`, in both themes and both directions — a
  bidi-reordered statement is unreadable and, worse, ambiguous.
- **Status is never colour alone**: every state carries a glyph and a word, so
  the screen survives greyscale and the print stylesheet.

## Gotchas learned the hard way

- **FK insert order:** `runs` references `messages`. Add the user message and
  **`await db.flush()` before** adding the run, or you get a FK violation.
- **A DELETE that cannot commit still returns 204.** `get_db` commits in
  FastAPI's dependency teardown, *after* the handler returned — so a
  `ForeignKeyViolationError` lands in the log while the success lands in the
  browser. This is how "deleting a data source does nothing" shipped. **Any
  route whose write can be refused by the database must `await db.flush()`
  inside the handler**, so the refusal becomes an error the caller sees. Same
  root cause as the read-after-write race in
  [docs/reference/dashboards.md](docs/reference/dashboards.md) "Known issue".
- **Every reference to `database_connections` and `llm_configs` is `SET NULL`,
  and `runs` was the last to get there** (migration `0014`). A run is the record
  of a question that was asked and answered; `model_snapshot` already carries
  the connection and model *names*, so a past answer stays explainable after its
  source is gone. Never CASCADE these two — deleting history to satisfy a
  constraint is the wrong trade. **`runs.owner_id` is deliberately untouched**:
  it is denormalised for ownership scoping, and a row whose owner is NULL is a
  row no ownership filter matches.
- **`SET NULL` gave `default_connection_id` a second meaning.** Null used to mean
  "nothing chosen yet"; it now also means "the connection was deleted". A thread
  in the second state must **refuse** a new message rather than silently re-bind
  to whatever the picker offers — `test_conversation_binding.py` was rewritten
  rather than deleted when that changed. Every surface downstream of a released
  connection has to say so: chat refuses, Reports disable Generate/Check with a
  sentence in the page, Dashboards already answered it with
  `E_CONNECTION_REMOVED` and a preserved layout.
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
  on error code **3024**; `SET STATEMENT ... FOR` is MariaDB-only. Same split
  for schema comments: `information_schema.SCHEMATA.SCHEMA_COMMENT` is MariaDB
  10.5+ and is error **1054** on every MySQL, so the read is attempted and
  suppressed, never required.
- **Oracle identifier case:** the catalog stores unquoted names upper-cased
  (`HR.EMPLOYEES`) and `build_index`/`_qualified` lower-case every key, which is
  correct because unquoted Oracle SQL is case-insensitive. It breaks on one
  input: a table created as `CREATE TABLE "Orders"` is stored `Orders`, can only
  be referenced `"Orders"`, and folds onto the same key as a plain `ORDERS`
  beside it — the later one wins and the other's columns stop resolving. A
  metric written over it validates and then fails at execution with ORA-00904.
  Known and deliberately unfixed; `test_semantic_validate.py`'s "Oracle
  identifier case" block asserts the behaviour as it stands.
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
  Column hints are optional — a connector that populates none still works — but
  if you add them, go through `connectors/hints.py` and honour its one rule:
  **emit a value list only when it is provably the complete domain.** Each
  engine proves that differently (Postgres: MCV count equals `n_distinct`;
  Oracle: a FREQUENCY histogram's endpoints equal `num_distinct`; MySQL: a
  declared `enum`/`set`, or a *singleton* histogram; SQL Server: a histogram
  whose `rows_sampled` equals `rows`), and where none of that holds, the
  bounded `SELECT DISTINCT … LIMIT n+1` probe is exact or silent.
  **Catalog comments are optional in exactly the same way, and go through
  `connectors/comments.py`** — `clean_comment` / `is_noise` / `SYSTEM_SCHEMAS`,
  the sibling of `hints.py`. Read whatever the engine calls a description
  (`COMMENT ON` on PG/MySQL/Oracle, `MS_Description` extended properties on SQL
  Server), for tables, columns, and the database or schema if it has either, and
  fold it into `ColumnInfo.comment` / `TableInfo.comment` /
  `SchemaSnapshot.database_comment` / `.schema_comments`. Three rules, and they
  are not style: **wrap every read in `contextlib.suppress`** like the stats
  reads — a comment is an accuracy aid, never a correctness dependency, and a
  role that cannot read the catalog must still get a snapshot; **clean at
  capture, never at render**, so every consumer inherits one hygiene; and
  **filter the allowlist through `business_schemas`**, which drops the engine's
  own dictionary schemas but never empties the list. A comment reaches a prompt
  under *every* disclosure policy (it is DDL a person wrote, not data — see
  [docs/reference/security.md](docs/reference/security.md) §2.4), so it must be one line, capped, and
  cleaned. Verify on a read-only role: this is the read most likely to need a
  privilege you cannot ask a customer for.
- **A new API route:** router in `api/v1/`, DTO in `schemas.py`, business logic
  in a `services/*` function that owns the transaction. Literal paths (e.g.
  `/test`) must be declared **above** `/{id}` routes.

  **Decide its `ResourceType` and `Privilege` before you write it.** Both enums
  live in `domain/value_objects/authz.py` and the matrix of what each verb means
  per type — `PRIVILEGE_MEANINGS` — is the specification every route resolves to
  exactly one cell of. The check itself is asked through the `Authorizer` port
  (`domain/ports/authz.py`), never by comparing `owner_id` or a role string in
  the handler: `make authz-check` greps for those three shortcuts, and
  [docs/plans/user-management-and-access-control.md](docs/plans/user-management-and-access-control.md)
  §18.4 gives the three enforcement shapes and says there is no fourth. A route
  that cannot name its type and privilege is a route whose access rule has not
  been decided yet.
- **Prompt changes:** versioned prompts live in `pipeline/prompts/`. Two sets
  are the exception, for the same reason and both recorded on the row they
  produce: `app/semantic/prompts.py` (`SEMANTIC_PROMPT_VERSION`) and
  `app/reports/prompts.py` (`REPORT_PROMPT_VERSION`). Both modules sit *below*
  the pipeline — the pipeline reads a layer, a report reads a node, and
  neither a layer nor a node knows anything about the thing above it.

  **The three constants as they stand: `PROMPT_VERSION` = `"v9"`,
  `SEMANTIC_PROMPT_VERSION` = `"s4"`, `REPORT_PROMPT_VERSION` = `"r4"`.** Move
  the one whose prompts you changed — and note that "prompts" means everything
  the model ends up reading, not only wording: a change to how much of the
  schema block survives moves it too. Chart, clarify and describe prompt changes
  move **none** of them, by convention — the eval scores generated SQL, and
  nothing on the SQL-producing path changed.

  **`runs.prompt_version` used to lie, and rows from that window still do.**
  `run_service` stamped it from `settings.prompt_version` (`core/config.py`), a
  *separate* string whose default said `"v2"` while the constant moved to v8, so
  every run written between 2026-07-26 and 2026-08-31 claims a version it never
  ran. **Fixed 2026-08-31** (Phase 0 of `docs/plans/learning-loop.md`): a run now
  records `prompts.PROMPT_VERSION` — resolved by `RunService._prompt_version`,
  stamped at creation and again by the process that renders the prompt, so a run
  queued by one replica and claimed by another after a deploy is filed under the
  module that actually rendered it. `settings.prompt_version` survives as an
  **override** for an experiment and is empty by default;
  `tests/unit/test_prompt_version.py` is the guard.

  **Historical rows were deliberately not rewritten** — a backfill would invent
  a version for a run nobody can re-render. Treat any `prompt_version` on a run
  from that window as unknown, not as v2; `docs/reference/pipeline-chat.md` §7 and the eval
  reports under `app/eval/reports/` record the drift where it happened.

---

## Git / environment notes

- This sandbox has **no GitHub auth** — `git push` will fail; the user pushes
  from their own terminal. Commit locally; don't attempt to push.
- Commit or branch only when asked. Commit messages follow
  `type(scope): a declarative sentence` — lowercase, no trailing period.
- `.env` is gitignored; `.env.example` is the tracked template `make secrets`
  copies from. Editing `.env.example` changes what every fresh clone gets.
- Losing `SECRET_BOX_KEY` means every stored credential must be re-entered.

The rest — ports, the three demo databases, the environment's four sharp edges,
what CI gates — is in [docs/development.md](docs/development.md).

---

## Keeping the documentation true

This file, [docs/status.md](docs/status.md) and every plan's ledger go stale in
the same way: somebody lands the work and ticks the box next week. **Tick it in
the commit that lands the work.** A checklist that runs ahead of the tree is
worse than no checklist.

When you change something this file describes, change it here *and* in the
reference doc that owns it — or, better, change it only in the reference doc and
make sure this file merely points there. **The goal for this file is to stay
small enough that it is always read.**
