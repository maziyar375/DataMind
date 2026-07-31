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
make fixtures  # rebuild + verify the sales fixtures (PG/MySQL/MSSQL) from clean
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
    v1/           auth, users, connections, llm_configs, semantic, conversations
    schemas.py    Pydantic request/response DTOs (no secrets ever in reads)
    errors.py     RFC 7807 problem+json mapping
  core/           config, logging (with redaction), errors, correlation context, clock
  domain/         entities, value_objects (enums/kinds), ports — ZERO I/O, no frameworks
    ports/        Protocols: database, llm, secrets, identity, events, run_executor
  services/       use cases + transaction boundaries: run_service,
                  semantic_service, bootstrap, disclosure_service, policy
  pipeline/       the AI run: state.py (typed RunState), pipeline.py (state machine),
                  nodes/ (route→retrieve→clarify→generate→validate→execute→
                  inspect→present→chart),
                  prompts/, disclosure.py (result gate), checks.py (free result checks)
  sqlguard/       policy, validator, rewriter — self-contained, dialect-aware
  semantic/       what the schema *means*: models.py (the document), validate.py
                  (bind it to a snapshot, parse metric SQL), generator.py (build
                  one with a model, one call per table), render.py (the prompt
                  block), prompts.py — self-contained like sqlguard
  charts/         ChartIntent → result profile → shape fit → Vega-Lite
  infra/          adapters implementing the ports:
    db/           SQLAlchemy models.py + Alembic migrations + session
    connectors/   factory + postgres/mysql/mssql/oracle (one DatabaseConnector each)
                  + hints.py: the engine-neutral column-hint contract they share
    llm/          LiteLLM behind LLMGateway
    crypto/       SecretBox (AES-256-GCM)
    identity/     local Argon2id + JWT provider
    events/       SSE event publisher
  workers/        inprocess run executor + stale-run reconciler + semantic.py
                  (generation jobs; minutes long, so they are polled not streamed)
  tests/          unit (incl. test_sqlguard_hostile.py) + integration
  fixtures/       sales_seed.sql (Postgres demo/eval DB) + sales_seed_mysql.sql
                  and sales_seed_mssql.sql dialect mirrors + rebuild_fixtures.sh
                  (`make fixtures`); each a wide, deliberately-messy 42-table
                  commerce schema with a read-only role, sized so retrieval is
                  actually exercised (snapshot exceeds the retrieve budget)

frontend/src/
  main.tsx, App.tsx        entry + router/layout
  theme/tokens.ts          design tokens (oklch), DATABASE_TYPES, dark+light palettes
  api/client.ts, types.ts  typed client, SSE streaming + polling fallback
  components/               ui.tsx (primitives, icons, Logo), chat.tsx,
                            settings.tsx, semantic.tsx (the layer editor)
  pages/                    Login, Chat, DataSources, LlmProviders, Users
```

---

## The dependency rule (enforced, not documented)

```
api → services → pipeline → semantic → domain ← infra
```

`import-linter` fails CI on violation (`make lint`). Concretely:

- **`app.domain` imports no framework and no infra** — no fastapi, sqlalchemy,
  litellm, `app.infra`, `app.api`, `app.services`. Keep it pure.
- **`app.sqlguard` is self-contained** — no fastapi/sqlalchemy/litellm/infra/api.
- **`app.semantic` is self-contained** for the same reason — it is a pure
  function of a snapshot, a document and the `LLMGateway` *port*, so the whole
  generator runs in a test against a dict and a fake gateway.
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
   chat header shows the policy in force *at ask time*. The policy has **two
   halves**: `pipeline/disclosure.py` gates the result, and `HintBudget`
   (`domain/value_objects`) gates the per-column content hints in the schema
   block. Hints are filtered at *render* time, never only at sync, so
   tightening a policy takes effect without a re-sync — and the sensitive-name
   floor (`is_sensitive_column`) applies at capture under every policy,
   including FULL, because the schema block is sent on every question while a
   result is only sent for the query the user asked for.

---

## How a run works

`POST /conversations/{id}/messages` → `run_service.create_run` writes the user
`message`, **flushes**, then the `runs` row (FK order matters — see below),
hands off to the in-process executor. `AnalyticsPipeline.run` walks a linear
state machine with one bounded repair loop:

```
route → retrieve → clarify → generate → validate → execute → inspect →
present → chart
```

- `route` classifies intent. **METADATA** questions ("what tables do I have?")
  are answered from the schema snapshot and **HALT before any SQL**, by
  `pipeline/metadata.py`, at the granularity the question asked: an inventory
  (name, rows, column count — one line each, largest first) unless the question
  names a table, in which case that table's columns with types. The match is on
  the snapshot's own names, so this still costs no model call. The exhaustive
  `_describe_schema` render stays for the *model*-facing follow-up-suggestions
  prompt, which does need every column name.
- `retrieve` selects tables, then attaches the connection's **semantic layer**;
  `RetrievedContext.render` appends a block describing only the retrieved
  tables — business names, grain, defined metrics with their SQL, time
  conventions, fan-out cautions. See "The semantic layer" below.
- A validation/execution failure can `goto` back to `generate` (bounded repair);
  a hard ceiling of 24 transitions and a per-run deadline prevent runaway loops.
- `clarify` is the one node that can end a run without SQL. It runs after
  `retrieve` so it judges the question against the same schema block and
  semantic layer the generator will see, and it **fails open** — any provider
  error, or a malformed answer, proceeds to `generate`, because a guessed
  answer shown with its SQL beats no answer. When it does ask, the question
  becomes the assistant message, the run ends `NEEDS_CLARIFICATION` with a
  `CLARIFICATION` artifact carrying the options, and the user's reply arrives
  as an ordinary new run — no durable interrupt, no resume. It asks **at most
  once per exchange**, enforced in `run_service` by checking whether the
  previous run in the thread asked, not by trusting the model to remember.
  Switched per connection with `connections.clarify_enabled`; off is
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
  and snapshots what it used onto the run, so earlier turns stay explainable.
- Terminal states: `SUCCEEDED | FAILED | TIMED_OUT | CANCELLED`.
  `NEEDS_CLARIFICATION` is deliberately **not** terminal — a run that asked a
  question is mid-exchange, so `cancel` still applies to it while the
  reconciler (which sweeps `QUEUED`/`RUNNING`) leaves it alone.

A node crash is caught and recorded as a **run failure**, never a bare HTTP
500. A process that dies mid-run is healed by the reconciler + a startup sweep,
so no row is stuck `RUNNING`.

---

## The semantic layer

The schema snapshot says what *exists*. The semantic layer says what it
**means**: the business name and **grain** of each table ("one row per line
item"), the columns worth explaining, **metrics** bound to exact SQL including
the filters that belong to the definition rather than the question, **time
conventions** (fiscal year, week start, whether "last month" is calendar or
rolling), a glossary, and per-join **fan-out cautions**. One editable document
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
  Column hints are optional — a connector that populates none still works — but
  if you add them, go through `connectors/hints.py` and honour its one rule:
  **emit a value list only when it is provably the complete domain.** Each
  engine proves that differently (Postgres: MCV count equals `n_distinct`;
  Oracle: a FREQUENCY histogram's endpoints equal `num_distinct`; MySQL: a
  declared `enum`/`set`, or a *singleton* histogram; SQL Server: a histogram
  whose `rows_sampled` equals `rows`), and where none of that holds, the
  bounded `SELECT DISTINCT … LIMIT n+1` probe is exact or silent.
- **A new API route:** router in `api/v1/`, DTO in `schemas.py`, business logic
  in a `services/*` function that owns the transaction. Literal paths (e.g.
  `/test`) must be declared **above** `/{id}` routes.
- **Prompt changes:** versioned prompts live in `pipeline/prompts/`. The
  semantic-layer generation prompts are the exception — they live in
  `app/semantic/prompts.py` (versioned as `SEMANTIC_PROMPT_VERSION`, recorded
  on every layer) because `app.semantic` sits *below* the pipeline: the
  pipeline reads a layer, a layer knows nothing about a run.

---

## Git / environment notes

- This sandbox has **no GitHub auth** — `git push` will fail; the user pushes
  from their own terminal. Commit locally; don't attempt to push.
- Commit or branch only when asked. Config keys: `SECRET_BOX_KEY`, `JWT_SECRET`,
  `ADMIN_EMAIL`/`ADMIN_PASSWORD`, `DATABASE_URL`, `MAX_CONCURRENT_RUNS`,
  `RUN_DEADLINE_SECONDS`. Losing `SECRET_BOX_KEY` means re-entering every stored
  credential.
