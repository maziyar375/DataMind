# CLAUDE.md — orientation for developers and agents

Read this before touching the code. It is the map, not the territory: it tells
you where things live, what must not break, and how to run and test — so you
can make a change without reading the whole codebase first.

Every section below has a fuller document behind it; this file is the part you
must not skip, and the `>` note at the top of each section says where the rest
lives. [docs/README.md](docs/README.md) indexes all fifteen. For users, see
[README.md](README.md).

The four you will reach for most:

| | |
|---|---|
| [docs/CODEBASE.md](docs/CODEBASE.md) | a code-grounded tour of the whole stack — start here if the map below is not enough |
| [docs/security.md](docs/security.md) | **read before** changing `sqlguard/`, `disclosure.py`, `HintBudget`, or adding an LLM call site |
| [docs/pipeline.md](docs/pipeline.md) | **read before** changing a node; §0 maps all three pipelines |
| [docs/architecture.md](docs/architecture.md) | the "why" — a pre-build proposal, so read its status banner first |

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
- **LLM provider access:** LiteLLM — *only* behind the `LLMGateway` port
  (`app/infra/llm/litellm_gateway.py`, the one module allowed to import it).
- **Run orchestration:** LangGraph — *only* inside `app/pipeline/` and
  `app/workers/`. Same bargain as LiteLLM: an import-linter contract and a CI
  grep hold the boundary.

> **These two are different layers and neither replaced the other.** LiteLLM is
> the *provider adapter* — it turns `ChatMessage[]` into a call to OpenAI,
> Anthropic, Ollama or vLLM, and that is all it does. LangGraph is the
> *orchestrator* — it decides which node runs next. LangGraph was adopted in the
> migration ([docs/langgraph-migration.md](docs/langgraph-migration.md));
> LiteLLM was never touched by it and is still the only way a prompt leaves the
> process. **LangChain is not a dependency**: the one `langchain_core` import is
> `RunnableConfig`, a type LangGraph pulls in, used in `pipeline/graph.py` and
> `workers/report_graph.py` and nowhere else.
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
make targets   # opt-in Oracle + SQL Server demo databases (~2GB RAM each)
make targets-down
make logs      # follow api logs

make test      # full backend suite (cd backend && pytest -q)
make guard     # the hostile SQL corpus alone — the hard CI gate
make lint      # ruff + import-linter contracts
make fmt       # ruff format
make migrate   # alembic upgrade head
make fixtures  # rebuild + verify the sales fixtures (PG/MySQL/MSSQL) from clean
make db-repair # recreate the empty PGDATA runtime dirs the studio drive strips
```

Frontend, from `frontend/`: `npm run dev`, `npm run build` (`tsc -b && vite
build`), `npm run typecheck` (`tsc --noEmit`), `npm run lint`, `npm test` (all
nine DOM-free logic suites: schedule, format, dashboard document, palette,
chat format, report document, report readiness, print, semantic drift).

The **eval harness is not in `make test`** — it calls a real provider and costs
money. `python -m app.eval.runner --suite sales_v1` from `backend/`, or
`backend/scripts/eval_run.sh` behind a rate-limiting provider. See
[docs/eval.md](docs/eval.md).

**Verification loop before you claim done:** `npm run typecheck` + `npm run
build` + `npm test` for frontend changes; `make test` (and `make guard` if you
touched `sqlguard/` or a connector) for backend. Several past bugs only surfaced
end-to-end via the API, not in the UI — actually exercise the path you changed.

**Ports:** web `5173`, api `8000` (`/docs` for OpenAPI), app db `5432`, demo
`sales` db `5433`, Sakila `3307`; behind `make targets`, Oracle `1521` and SQL
Server `1433`. On a remote host, expose **only 5173**; the SPA calls the
same-origin `/api/v1` and Vite proxies it to `api:8000`.

**All four engines have a demo database**, so a connector change can be driven
against a real server without testcontainers: `sales`/`sakila` start with the
stack, `oracle`/`mssql` are behind the `targets` profile. SQL Server loads the
same 42-table `sales` mirror as Postgres; Oracle loads a small four-table schema
in `backend/fixtures/oracle/` whose **`COMMENT ON` metadata is the point** —
it is the fixture that exercises catalog comments end to end, and its
`analytics_ro` deliberately holds no roles at all, not even `CONNECT`.

---

## Code map

```
backend/app/
  main.py         ASGI factory: lifespan (bootstrap admin, reconcile orphans,
                  start reconciler), CORS, correlation-id middleware, health.
  api/            HTTP shape ONLY — no business logic.
    v1/           auth, users, connections, llm_configs, semantic, conversations,
                  dashboards, drafts (SQL), reports
    deps.py       FastAPI dependencies (current user, session, settings)
    schemas.py    Pydantic request/response DTOs (no secrets ever in reads)
    errors.py     RFC 7807 problem+json mapping
  core/           config, logging (with redaction), errors, correlation context, clock
  domain/         entities, value_objects (enums/kinds), ports — ZERO I/O, no frameworks
    ports/        Protocols: database, llm, secrets, identity, events, run_executor
  services/       use cases + transaction boundaries: run_service,
                  semantic_service, report_service, dashboard_service,
                  dashboard_transfer (a dashboard as a portable file: no ids,
                  no results, no connection internals — and an imported
                  statement is hostile input like any other),
                  query_service (execute_saved_sql — the tile/report entry point
                  into guarded execution), sql_draft_service, bootstrap, policy
  pipeline/       the AI run: state.py (typed RunState), graph.py (the compiled
                  LangGraph + the node adapter), pipeline.py (the
                  AnalyticsPipeline facade over it),
                  nodes/ (route→retrieve→describe→clarify→generate→validate→
                  execute→inspect→present→chart), contracts.py (the node signature),
                  metadata.py (which tables a schema question is about, and the
                  rendered fallback answer),
                  prompts/, disclosure.py (result gate), checks.py (free result checks)
  sqlguard/       policy, validator, rewriter — self-contained, dialect-aware
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
  charts/         ChartIntent → result profile → shape fit → Vega-Lite
  eval/           the offline harness — runs the REAL pipeline against a
                  testcontainers fixture: dataset.py (record schema + fixture
                  registry), runner.py (CLI + scorecard to eval_runs/
                  eval_results), metrics.py (pure scoring), suites/ (the FROZEN
                  golden sets + CHANGELOG), reports/ (past run write-ups).
                  Costs real money; not in `make test`. See docs/eval.md
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
                  and a per-section retry are two entries into it)

backend/           ← these are SIBLINGS of app/, not inside it
  tests/          unit (incl. test_sqlguard_hostile.py) + integration + eval
                  (test_golden_set.py and the dual-form verify artifact)
  fixtures/       sales_seed.sql (Postgres demo/eval DB) + sales_seed_mysql.sql
                  and sales_seed_mssql.sql dialect mirrors + rebuild_fixtures.sh
                  (`make fixtures`); each a wide, deliberately-messy 42-table
                  commerce schema with a read-only role, built to *exceed* the
                  retrieve budget — which it no longer does: the budget was
                  raised 24k → 50k and the fixture estimates 26,480, so the
                  eval now runs entirely on FULL_SNAPSHOT and recall is 1.0 by
                  construction (docs/eval.md §1 — read it before quoting a
                  recall number). sales_comments.sql is the eval's commented
                  arm; mysql/ holds the Sakila seed and oracle/ the four-table
                  COMMENT ON fixture, one per extra demo DB
  scripts/        eval_run.sh (rate-limit-tolerant eval wrapper) +
                  eval_seed_llm_config.py (used by the nightly workflow) +
                  catalog_probe.py (what an engine will actually tell you about
                  its own comments, from a read-only role)

scripts/           repo root, not backend/: nginx-replicas.conf (the two-replica
                  balancer), pg-ensure-runtime-dirs.sh (`make db-repair`'s
                  in-container half), seed_demo_dashboard.py

frontend/src/
  main.tsx, App.tsx        entry + router/layout
  theme/tokens.ts          design tokens (oklch), DATABASE_TYPES, dark+light palettes
  api/client.ts, types.ts  typed client, SSE streaming + polling fallback
  components/               ui.tsx (primitives, icons, Logo, ResultTable),
                            VegaChart.tsx (the renderer), chart-picker.tsx,
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
                            configured table resolves/sorts/formats, also
                            DOM-free — `npm run test:format`),
                            dashboard-document.ts (reading an exported file:
                            what it is, and which connection each of its
                            databases is here — `npm run test:document`) +
                            dashboard-transfer.tsx (the download and the import
                            dialog), tile-editor.tsx
                            (ask or write the SQL; one guard check for both),
                            report.tsx (the outline editor + the document
                            viewer), report-history.tsx, report-document.ts
                            (merging a run into a document — `npm run
                            test:report`), report-readiness.ts (what generating
                            an outline now would produce — what the Generate
                            preflight says, `npm run test:readiness`),
                            report-print.ts (the print handoff: fonts, and
                            redrawing charts at page width —
                            `npm run test:print`)
  pages/                    Login, Chat, DataSources, LlmProviders, Users,
                            Dashboards, Reports
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

---

## Three pipelines, one set of nodes

There are **three** pipelines in this product, and only one of them is a state
machine. Know which you are in before you go looking for an executor that does
not exist. [docs/pipeline.md](docs/pipeline.md) §0 is the full map.

| | **Chat** | **Dashboard** | **Report** |
|---|---|---|---|
| Orchestrator | `AnalyticsPipeline` — a compiled LangGraph | a service function + `asyncio.gather` | `ReportRunExecutor` + `report_graph.py` |
| Shape | streamed (SSE), 5–60s | request/response, sub-second on a cache hit | queued (**202**) + polled, minutes |
| Model runs | **at ask time**, every time | **at authoring time only** | at authoring *and* generation time |
| SQL comes from | `generate`, fresh per question | `dashboard_tiles.sql`, stored | `report_blocks.sql`, stored |
| Guard entry | the `validate` node | `execute_saved_sql` | `execute_saved_sql` |
| Result values → model | `present`, per policy | **never** | `narrate`, per policy (`NONE`/`AGGREGATE` refused) |
| Failure posture | the run fails | a per-tile `ERROR` **value** | per section; run status is **derived** |

**The guard has four entry points and none is privileged:** the `validate` node,
`execute_saved_sql` (tiles *and* report blocks), tile save, and dashboard
import. The hostile corpus is replayed through each
(`test_sqlguard_hostile.py`, `test_query_service.py`, `test_report_guard.py`,
`test_dashboard_transfer.py`).

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

> Node-by-node reference — what each node does, its exact logic, the prompts it
> sends, the control-flow rules, and the LangGraph port map:
> **[docs/pipeline.md](docs/pipeline.md)**. Read that before changing a node.
> Its §0 maps all three pipelines (chat, dashboard, report), lists every LLM
> call site in the product, and names the five failure postures (§4.1); the
> other two have the same treatment in
> **[docs/pipeline-dashboard.md](docs/pipeline-dashboard.md)** and
> **[docs/pipeline-report.md](docs/pipeline-report.md)**.

`POST /conversations/{id}/messages` → `run_service.create_run` writes the user
`message`, **flushes**, then the `runs` row (FK order matters — see below),
hands off to the in-process executor. `AnalyticsPipeline.run` invokes a
**compiled LangGraph** (`pipeline/graph.py`) whose chain is linear with one
bounded repair loop — plus five edges that are not the chain, listed below:

```
route → retrieve → describe → clarify → generate → validate → execute →
inspect → present → chart
```

The five non-chain edges are the reason this is a graph and not a list: three
repairs **back** into `generate` (from `validate`, `execute` and `inspect`) and
two restores **forward** into `present` (from `validate` and `execute`, via
`_restore_superseded`, skipping `execute` and `inspect`). The node functions
know nothing about any of it — they name a label in `NodeResult.goto` and the
adapter in `graph.py` routes it. **The adapter, not the nodes, owns the
deadline check, the `seq` counter, the `run_steps` write and both `emit`
calls**, which is what keeps the SSE sequence identical;
`tests/unit/test_pipeline_events.py` is that contract and
`tests/unit/test_pipeline_graph.py` is the wiring's.

- `route` classifies intent, reading the recent turns once a thread has any: a
  follow-up carries no subject of its own ("and by month?"), and classified
  alone it could come back CHITCHAT or UNSUPPORTED and halt the run before any
  SQL. A first turn sends the old history-free prompt byte-identically.
  CHITCHAT and UNSUPPORTED halt here with a canned reply; **METADATA continues**
  to `describe`.
- `retrieve` selects tables — from the question, plus the tables the recent
  turns' SQL actually queried, so a follow-up inherits its subject instead of
  falling to an arbitrary `tables[:20]` — then attaches the **semantic layer**;
  `RetrievedContext.render` appends a block describing only the retrieved
  tables — business names, grain, defined metrics with their SQL, time
  conventions, fan-out cautions. See "The semantic layer" below. A METADATA
  question over a snapshot too wide to send whole selects differently
  (`SCHEMA_QUESTION`): the tables it named, then the largest of the rest, since
  "what is in this database?" shares no words with any table name.
- `describe` answers a **METADATA** question ("what tables do I have?", "what
  does `order_items` count?") from that block — schema *and* semantic layer —
  streamed like any other answer, and **HALTs before any SQL**. Every other
  intent gets `SKIPPED`, so the common path costs nothing. It sits after
  `retrieve` for one reason: the answer to most schema questions is the grain,
  the labels and the metrics in the layer, and `route` runs before the layer is
  loaded. It never generates SQL — a schema question sent to `generate` becomes
  a query against `information_schema`, which the guard always rejects as a
  system table. `metadata.py` is what it builds on: `select_tables` (which
  tables), `census` (how many there are in total, and the names of any left
  out — counts and names only, never a row-count total outside `HintBudget`),
  and `answer_metadata`, the plain rendering of the snapshot that this node
  used to be, kept as the **fallback** for a provider failure and for an empty
  snapshot (which costs no model call at all). The exhaustive `_describe_schema`
  render stays for the follow-up-suggestions prompt, which needs every column
  name.
- A validation/execution failure can `goto` back to `generate` (bounded repair);
  a hard ceiling of 24 transitions and a per-run deadline prevent runaway loops.
- `clarify` is the one node that can end a run by *asking*. It runs after
  `retrieve` so it judges the question against the same schema block and
  semantic layer the generator will see, and it **fails open** — any provider
  error, or a malformed answer, proceeds to `generate`, because a guessed
  answer shown with its SQL beats no answer. When it does ask, the question
  becomes the assistant message, the run ends `NEEDS_CLARIFICATION` with a
  `CLARIFICATION` artifact carrying the options, and the user's reply arrives
  as an ordinary new run — no durable interrupt, no resume. It asks **at most
  once per exchange**, enforced in `run_service` by checking whether the
  previous run in the thread asked, not by trusting the model to remember.
  That same check (`_pending_clarification`) also makes the reply carry its
  question: a reply is usually a complete question on its own ("total sales"),
  so `_compose_question` rebuilds the exchange into one question before the
  pipeline sees it — otherwise the generator answers the criterion and drops
  the subject. Composed in the service, never in a prompt, so
  `GENERATE_SYSTEM` stays byte-identical and every node downstream of
  `state.question` is fixed at once. Switched per connection with
  `connections.clarify_enabled`; off is
  byte-identical to the pre-feature pipeline. `GENERATE_SYSTEM` is untouched
  by it on purpose — see the note in `pipeline/prompts`.
- `inspect` covers the third failure mode: the query ran and the answer is
  wrong. Its checks are **structural** — SQL + snapshot + result *shape*, never
  a result value — so they cost no tokens and behave identically under every
  disclosure policy. Only `retry=True` findings spend a regeneration, at most
  once per run, and the superseded result is restored if that retry fails, so a
  check can never turn a working answer into a failed run. See
  `pipeline/checks.py`.
- Each step persists a `run_step` and emits an SSE event; the SPA renders the
  **live step trail**, which is a valued feature — keep it visible, don't
  collapse it behind a "Thought for Xs" summary by default.
- `chart` is **best-effort and fail-open** (the opposite of the SQL guard): the
  model proposes a constrained `ChartIntent` compiled to Vega-Lite, with a
  data-shape heuristic as the fallback; any failure just yields no chart, since
  the answer and table are already persisted. The model's pick is a
  *suggestion*, never the last word: `charts.plan_chart` profiles the result
  first (cardinality, numeric range, constant columns) and owns the decision —
  it vetoes charts the data cannot support (a single row, a measure identical
  in every row, an id column as the measure), repairs salvageable intents (pie
  → bar past 6 slices, line → bar over unordered text, swapped axes,
  mislabelled axis types), and caps category charts at `MAX_CATEGORY_MARKS`
  while labelling the chart with what was dropped. The veto runs *before* the
  model call, so an unchartable result costs no tokens.
- A conversation is **bound to one connection + model**, picked in the chat
  header before the first message; the pickers lock once the transcript is
  non-empty. The choice is stored as the conversation's `default_connection_id`
  / `default_llm_config_id`. `create_run` still accepts a per-message override
  and snapshots what it used onto the run, so earlier turns stay explainable —
  but the **connection** override is now refused once the transcript is
  non-empty (`_bind_connection`, 422). History is keyed on the conversation, so
  a thread spanning two connections would hand one connection's answers to the
  other's prompt under the other's disclosure policy. The model may still be
  switched mid-thread; that changes who reads the transcript, not what is in
  it.
- Terminal states: `SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`.
  `NEEDS_CLARIFICATION` is deliberately **not** terminal — a run that asked a
  question is mid-exchange, so `cancel` still applies to it while the
  reconciler (which sweeps `QUEUED`/`RUNNING`) leaves it alone.

A node crash is caught and recorded as a **run failure**, never a bare HTTP
500. A process that dies mid-run is healed by the reconciler + a startup sweep,
so no row is stuck `RUNNING`.

**More than one API replica is supported, and three rules make it work** — see
[docs/cross-replica.md](docs/cross-replica.md) before touching any of them:

1. **A run is claimed before it is executed.** `RunService.claim` is
   `SELECT … FOR UPDATE SKIP LOCKED` + a conditional `UPDATE`, so exactly one
   process runs a given run and the loser skips instead of blocking. The
   direct hand-off from the POST handler is still the normal path; a claim
   poller picks up runs left unowned by a process that died before submitting.
2. **Cancelling is a row, not a task handle.** `runs.cancel_requested` and
   `report_runs.cancel_requested` reach the process actually doing the work,
   which reads them on its heartbeat (so worst case is
   `run_heartbeat_seconds`). The local `executor.cancel` stays as the
   same-replica fast path. `_finalise` will not overwrite a terminal status it
   did not set, or a cancel from elsewhere would be undone on the way out.
3. **Events cross processes over `LISTEN`/`NOTIFY`, not a broker.** The
   notification carries `run_id:seq`; the body is read from `run_events`, which
   was always being written. It is issued on the transaction that writes the
   row — that is the delivery guarantee, not a style choice — and the SSE
   endpoint backfills from the log before attaching to the local bus.

---

## The semantic layer

The schema snapshot says what *exists*. The semantic layer says what it
**means**: the business name and **grain** of each table ("one row per line
item"), the columns worth explaining, **metrics** bound to exact SQL including
the filters that belong to the definition rather than the question, **time
conventions** (fiscal year, week start, whether "last month" is calendar or
rolling), the rows that **should not count** unless asked for (soft deletes,
test accounts — free text, because the rule spans tables that do not share a
column), a glossary, and per-join **fan-out cautions**. One editable document
per connection, in `semantic_layers`.

It exists because the eval said so: FK-neighbour retrieval lifted recall 70→86%
with **flat** execution accuracy, and the residual DeepSeek failures were
interpretation, not retrieval — rolling-vs-calendar windows, long-vs-wide
shapes. That is the class this addresses.

- **Generate** — `POST /connections/{id}/semantic/generate` with an
  `llm_config_id` queues a `semantic_jobs` row and returns **202**; the SPA
  polls it. `app/semantic/generator.py` runs **one model call per table**, four
  concurrently: a whole-schema call returns forty one-line descriptions and no
  metrics, per-table calls return grain and real expressions. **Joins are
  derived, never asked for** — cardinality is readable off the catalog.
- **Nothing unchecked is kept.** Generated names are resolved against the
  snapshot and metric expressions parsed with SQLGlot; an invalid *generated*
  metric is dropped (and counted in the job's stats), while an invalid
  *human-written* one is flagged and kept, because deleting a person's work to
  hide drift is worse than showing it. Flagged entries never reach the prompt.
- **Regeneration is safe.** Any field a user edits sets `provenance.edited`, and
  `merge_documents` keeps those entities; `REPLACE` is the explicit "start
  over" the UI makes you choose.
- **It is off-by-absence.** With no layer, or with
  `connections.semantic_layer_enabled` false, `RetrievedContext.render` emits
  **byte-identical** output to before the feature existed — verified by a test.
  That switch is how you A/B a layer against the bare schema on the eval suite
  without deleting it. `PROMPT_VERSION` moved to **v4** because a v3 and a v4
  run are otherwise indistinguishable from the outside.
- **It widens no disclosure.** Generation reads the same schema block a run
  reads, under the same `HintBudget`, and column `value_meanings` are filtered
  to values already in the snapshot — the model cannot invent a key to leak.
- **Editing** lives in Data sources → Semantic layer
  (`frontend/src/components/semantic.tsx`). Metric expressions are validated
  live by `POST .../semantic/check`, which is the *same parser* the save path
  uses — the editor never promises something the backend will reject.

---

## Dashboards

> Full reference — the six rules `execute_saved_sql` obeys, the data model, the
> scheduler, the tile editor, and export/import:
> **[docs/dashboards.md](docs/dashboards.md)**. Authoring vs refresh step by
> step, with every error code:
> **[docs/pipeline-dashboard.md](docs/pipeline-dashboard.md)**.

A grid of tiles, each a saved query bound to **its own connection** and **its
own refresh rate**, drawn as a chart, a table, a big number, or plain text.
Three tables (`0005`, `0006`).

The one thing that makes this hard, and everything else is CRUD:

- **A tile is the second entry point into guarded execution**, and it gets no
  exemption. `services/query_service.py::execute_saved_sql` re-validates stored
  SQL against the connection's **current** snapshot on *every* execution — not
  because it passed when it was saved. A re-sync that dropped a table fails the
  tile closed with `E_SCHEMA_CHANGED` rather than returning an empty result that
  looks like "no data". `tests/unit/test_query_service.py` replays the hostile
  corpus through a tile; that test is what proves dashboards opened no bypass.
- **`dashboard_tiles.sql` is hostile input by definition** — the user types into
  it directly. `sql_origin` (`GENERATED | GENERATED_EDITED | HANDWRITTEN`) is
  provenance only; the guard cannot tell them apart and must not try.
- **A tile failure is a *value*, not an exception.** One broken tile must never
  fail the dashboard response. Error codes the UI branches on: `E_SCHEMA_CHANGED`,
  `E_NO_SNAPSHOT`, `E_FORBIDDEN`, `E_CONNECTION_REMOVED`, `E_QUERY_FAILED`,
  `E_INTERNAL`, else the guard's own `rule_id` verbatim.
- **Nothing calls a model at refresh time.** That is the most load-bearing "no"
  in the product: a dashboard keeps working after the provider key is revoked.
  A model runs at *authoring* time only — two calls per drafted tile (SQL, then
  chart), **zero** per refresh.
- **Containment is the connection's, not the tile's.** A tile override may only
  *lower* `max_rows` and `statement_timeout_ms`, never raise them.
- **Batching reads before it fans out.** `execute_many` groups tiles by
  connection, builds one connector per connection under
  `MAX_CONCURRENT_TILES = 4`, and does **every database read in sequence before
  the tiles fan out** — an `AsyncSession` is not safe for concurrent use.
- **The cache is in Postgres** (`dashboard_tile_cache`), not in-process, because
  an in-process cache goes stale per worker. `result_fingerprint` hashes
  `(connection_id, sql, max_rows, chart_config)` — **not the SQL alone**, so a
  tile switched from pie to line does not keep serving the pie until its
  interval elapses. `table_config` is deliberately **excluded**: renaming a
  column header must not send a query to the customer's database. **Failures are
  cached too**, or a broken tile re-runs on every tick of every open browser.
- **One `setInterval(1000)` per open dashboard, not one timer per tile.** Each
  tick computes which tiles are due and fires **one** `POST /data {tile_ids}`.
  It pauses on `document.hidden` and on return refreshes what went overdue
  **once**, not once per missed interval. The due rule is DOM-free in
  `dashboard-schedule.ts` — a forgotten background tab that polls forever is how
  this feature becomes the reason someone's production database is slow.
- **Import is a fourth door to the guard.** `sql` in a `.json` file is typed as
  easily as `sql` in a textarea, so every tile in a document goes through
  `_validated_tile_fields` — the same call the save path makes — and **every
  tile is validated before anything is created**, so a refused import leaves no
  half-built dashboard.

Not built, on purpose: filters (`QueryExecutor.execute` takes no bind
parameters — **never** by string interpolation), sharing, and "add to dashboard"
from a chat run.

---

## Charts

> Full reference — the eight types, every veto and repair, the constants and
> their reasoning, the colour work: **[docs/charts.md](docs/charts.md)**.

`backend/app/charts/` is one module: `profile_result` → `unchartable_reason` →
[model proposes `ChartIntent`] → `plan_chart` → `compile_vega_lite`, plus
`plan_kpi` for a big number. Every surface — chat, tile, report — decides its
picture with the same planner.

Four rules, and breaking any of them is quiet:

1. **The model proposes; the platform decides.** A `ChartIntent` is a
   *suggestion*. `plan_chart` vetoes what the data cannot support, repairs what
   is salvageable, and falls back to a shape heuristic when the model errors or
   returns garbage.
2. **The veto runs *before* the model call.** `unchartable_reason` is pure
   arithmetic over the profile, so a hopeless result costs zero tokens — and the
   step trail shows a fact about the data instead of "the model declined".
3. **Prompt/type parity.** A chart type is not "added" when the compiler draws
   it. It is added when `CHART_SYSTEM` describes when to pick it *and*
   `ResultProfile.describe()` carries the facts that rule is stated in terms of.
   Any change to `ChartType`, `_fit`, or a threshold constant is unfinished
   until both are updated. **A bullet describing behaviour the code no longer
   has is a bug in the prompt.**
4. **`chart_type: "none"` does not mean "draw nothing".** `validate_intent`
   refuses it, so `plan_chart` falls through to the heuristic and draws whatever
   the shape suggests — the model's reading discarded without a word. The
   picker's *Table only* sets `tile_type = TABLE` instead.

`PROMPT_VERSION` does **not** move for chart-prompt changes — the eval scores
generated SQL, and nothing on the SQL-producing path changes. Same convention as
`CLARIFY_SYSTEM` and `DESCRIBE_SYSTEM`.

The palette in `VegaChart.tsx` is **measured, not chosen** (OKLab ΔE, Machado
CVD simulation, contrast per mode) and `palette.test.ts` re-checks it. There is
no free hex picker because one would destroy all of that silently; adding a
second palette means re-running the validator **in both themes** first.

---

## Reports

> Full reference — the data model, the two roads to a block's SQL, the
> generation order, where the numbers come from, and the print handoff:
> **[docs/reports.md](docs/reports.md)**. `docs/reports-plan.md` is the record
> of what was intended, phase by phase.

Chat answers one question. A dashboard watches numbers that are always current.
**A report is a document**: a structure a human approved, prose written over
real results, and a snapshot of a moment that stays readable after the data has
moved on. Six tables (`0008_reports.py`), no table and no code path shared with
Dashboards.

The things worth knowing before you touch it:

- **A run's status is derived, not set.** `SUCCEEDED | PARTIAL | FAILED` comes
  from its sections, which is why progressive rendering and per-section retry
  need no resume machinery: a successful retry turns `PARTIAL` into `SUCCEEDED`
  with no state machine. Every result row is written the moment it lands, so
  the poll response *is* the progressive render.
- **Two prose columns.** `prose` is the model's, `edited_prose` is the user's,
  and **NULL means not edited** (so `null` is the revert). Both live on the
  *run*, never on the template — editing never destroys, regenerating never
  overwrites. The same rule sends a chart redraw to the run.
- **`report_blocks.sql` is a third entry point to the guard and gets no
  exemption.** It is re-validated against the connection's current snapshot on
  every execution through `execute_saved_sql`, and `sql_origin` is provenance
  only — `tests/unit/test_report_guard.py` replays the hostile corpus through
  it. Two routes write that column: `/check` asks a model, `PUT .../sql` asks
  nobody. Neither is privileged.
- **Editing a question resets the verdict, and only sometimes the SQL.** A
  generated draft is dropped (one click to reproduce); a hand-written or
  hand-edited one is kept — the semantic layer's rule about not deleting a
  person's work, applied to the same question.
- **The language is derived and the length is the user's.** Nobody picks a
  language: `reports/language.py` reads it off the request (script count, no
  model call) at creation and again whenever the request is rewritten, so the
  document cannot disagree with the thing it was asked for. `section_target`
  (2–8, default 5) is what the outline prompt asks for — the executive summary
  is added on top of it, and the user adds and deletes sections afterwards
  like any other edit. Both live on `reports`; a run snapshots the language it
  was written in, so past documents stay readable in their own.
- **Reports refuse `NONE`/`AGGREGATE`.** Prose written from no values beside
  charts drawn from real ones is a document that disagrees with itself. Gated
  at creation *and* re-checked at the start of every generation, because a
  policy tightened in between has to stop the run.
- **Time windows resolve in the SQL** (`CURRENT_DATE - INTERVAL '3 months'`),
  not in stored parameters and not by regenerating. `NodeDeps.extra_rules`
  carries the dialect rules, and a **METRIC dashboard tile** is the only other
  caller that appends anything (`METRIC_SQL_RULES` — a big number needs a time
  series before it can carry a delta or a sparkline). Empty for everyone else,
  so a chat run's SQL prompt is byte-identical to pre-feature and a test says
  so. Two callers means they **compose rather than override**
  (`_sql_rules_for`): a rule that silently replaced another would be found only
  by reading a prompt nobody prints.
- **A figure is captioned with a statement, and one number is not a figure.**
  A block carries both a `question` (what is asked of the database) and a
  `title` (what the document calls the exhibit); the caption is the title, and
  **an empty title falls back to the question**, which is what every block
  written before prompt r4 has. The question is not lost — it moves to the
  query panel and the appendix, where provenance belongs. A block whose result
  is a single number is drawn as a callout in the flow of its section rather
  than a numbered exhibit, and `figureNumbers` skips it so "Figure 4" still
  means something a reader can turn to.
- **No model is asked to do arithmetic.** `plan_kpi` computes the headline,
  `reports/facts.py` computes what a paragraph needs (and yields *nothing* for
  a partial or capped result, because a total over a prefix is a wrong total),
  and `reports/checks.py` flags figures the rows do not support — it flags,
  it never blocks.

---

## Proving a change helped — the eval harness

> Full reference — the golden set, every metric, the CI gate, and how to read a
> result honestly: **[docs/eval.md](docs/eval.md)**.

`app/eval/` runs the **real** pipeline — same `AnalyticsPipeline`, same
`NodeDeps`, same `GuardPolicy`, same connector as the HTTP path — against a
fresh fixture database in a throwaway container. It calls a real provider, so it
**costs real money and is not in `make test`**. An import-linter contract keeps
`app.eval` off the request path entirely.

```bash
cd backend
python -m app.eval.runner --suite sales_v1 --llm-config <uuid>
python -m app.eval.runner --suite sales_v1 --comments   # the catalog-comment arm
python -m app.eval.runner --suite sales_v1_negative     # must route, execute nothing
scripts/eval_run.sh --suite sales_v1 ...                # behind a rate-limiting provider
```

Four rules that matter more than any number it prints:

1. **The golden set is frozen.** Questions are *never* edited to make a score go
   up. `gold_sql` is corrected **only when demonstrably wrong**, and every
   correction is logged in `suites/CHANGELOG.md` with the evidence. Prompts and
   retrieval may be tuned freely; the gold answers may not. **An eval you are
   allowed to edit measures your willingness to edit it.**
2. **Golds are checked against something other than themselves.** Each record
   has a structurally different twin in `tests/eval/sales_v1_verify.json`, and
   `test_golden_set.py` asserts the two agree on the fixture. Adding a question
   means adding its twin.
3. **The baseline file is model-specific.** `sales_v1.baseline.json` records
   0.36 measured on **DeepSeek V4 Pro at temperature 0.2 under `PROMPT_VERSION`
   v2**. Against a different model or different settings it is meaningless —
   read its `_README` before quoting it, and never put two numbers from
   different models in one sentence.
4. **Retrieval recall currently measures nothing.** `_RETRIEVE_BUDGET_CHARS` was
   raised 24k → 50k and the fixture estimates 26,480, so `retrieve` takes
   `FULL_SNAPSHOT` on every question and recall is **1.0 by construction**. Do
   not compare a post-50k recall figure to a pre-50k one. Fixing it means
   widening the fixture or running the eval at a lower ceiling — a real
   decision, not a chore.

An exhausted retry scores `OUTCOME_ERROR`, which is **indistinguishable in the
report from the model getting the question wrong** — which is why the wrapper
exists and why it raises `RUN_DEADLINE_SECONDS` alongside the backoff. Widening
the retries without moving the deadline achieves nothing.

Two prompt changes have been measured to *lower* accuracy, and both are recorded
where someone would otherwise repeat them: a "getting the answer right" block in
`GENERATE_SYSTEM` (36% → 26%), and making `C_NULLABLE_INNER_JOIN` retry-eligible
(0 wins / 4 losses). **More instruction is not better here.**

---

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
  [docs/dashboards.md](docs/dashboards.md) "Known issue".
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
  [docs/security.md](docs/security.md) §2.4), so it must be one line, capped, and
  cleaned. Verify on a read-only role: this is the read most likely to need a
  privilege you cannot ask a customer for.
- **A new API route:** router in `api/v1/`, DTO in `schemas.py`, business logic
  in a `services/*` function that owns the transaction. Literal paths (e.g.
  `/test`) must be declared **above** `/{id}` routes.
- **Prompt changes:** versioned prompts live in `pipeline/prompts/`. Two sets
  are the exception, for the same reason and both recorded on the row they
  produce: `app/semantic/prompts.py` (`SEMANTIC_PROMPT_VERSION`) and
  `app/reports/prompts.py` (`REPORT_PROMPT_VERSION`). Both modules sit *below*
  the pipeline — the pipeline reads a layer, a report reads a node, and
  neither a layer nor a node knows anything about the thing above it.

---

## Git / environment notes

- This sandbox has **no GitHub auth** — `git push` will fail; the user pushes
  from their own terminal. Commit locally; don't attempt to push.
- Commit or branch only when asked. Config keys: `SECRET_BOX_KEY`, `JWT_SECRET`,
  `ADMIN_EMAIL`/`ADMIN_PASSWORD`, `DATABASE_URL`, `MAX_CONCURRENT_RUNS`,
  `RUN_DEADLINE_SECONDS`. Losing `SECRET_BOX_KEY` means re-entering every stored
  credential.
