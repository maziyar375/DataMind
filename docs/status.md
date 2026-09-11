# Project status

**Where DataMind is right now.** One page, so that "what is built?" and "what
is next?" do not require reading five ledgers.

> **Verified against the tree on 2026-09-11**, at `main` / `00e034f`. Every
> "built" below was checked by reading the code or a migration, not by memory.
> Each row names the document that owns the detail — this page is the index,
> never the argument.

**Milestone: MVP2, in progress.** MVP1 shipped and is tagged `v0.0.5`. MVP2 was
proposed on 2026-08-27 in [plans/mvp2.md](plans/mvp2.md); five of its strands
have since been designed, built and merged.

---

## 1. What MVP1 built

The product as it stands: a question in plain language becomes validated SQL,
run read-only, answered in prose with a table and a chart — across three
surfaces on one guarded path.

| | Owns the detail |
| --- | --- |
| **Chat** — eleven nodes, streamed over SSE, one bounded repair loop | [reference/pipeline-chat.md](reference/pipeline-chat.md) |
| **Dashboards** — tiles, each its own connection and refresh rate; no model at refresh time | [reference/dashboards.md](reference/dashboards.md) |
| **Reports** — an approved outline generated into a printable document | [reference/reports.md](reference/reports.md) |
| **The SQL guard** — AST allowlist, fails closed, five unprivileged entry points | [reference/security.md](reference/security.md) §4 |
| **Disclosure** — `NONE`/`AGGREGATE`/`SAMPLE`/`FULL` over results, hints and history | [reference/security.md](reference/security.md) §3 |
| **The semantic layer** — what the schema *means*, one document per connection | [reference/semantic-layer.md](reference/semantic-layer.md) |
| **Charts** — profile, veto, repair, compile to Vega-Lite | [reference/charts.md](reference/charts.md) |
| **The eval harness** — the real pipeline against a frozen golden set | [reference/eval.md](reference/eval.md) |
| **Four connectors** — PostgreSQL, MySQL, SQL Server, Oracle | [reference/codebase.md](reference/codebase.md) §3 |

## 2. What MVP2 has landed

Newest first. Each row is complete unless the **Caveat** column says otherwise.

| Strand | Landed | Caveat | Record |
| --- | --- | --- | --- |
| **User management and access control** — users, service users, roles, teams, grants on eight resource types, access review, audit of every authorization event | 2026-09-06 → 09-08, all 11 phases, 254/254 | — | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md); the rulebook is [reference/access-control.md](reference/access-control.md) |
| **Token accounting** — usage travels by sink, per node, per operation, per user; one structured log line per provider call | 2026-09-05, all 6 phases, migration `0023` | — | [plans/token-accounting.md](plans/token-accounting.md) |
| **UI/UX remediation** — routing, the Chat→Dashboard/Report bridge, responsive reflow | 2026-09-03, all 7 phases, 55/55 | — | [history/ui-improvement-plan.md](history/ui-improvement-plan.md); built state is [reference/frontend.md](reference/frontend.md) |
| **The learning loop** — the knowledge store, the curation surface, match/short-circuit/badge, feedback and the backlog, store health, the in-product benchmark, provenance | 2026-08-31 → 09-01, all 9 phases, 82/85 items | **Three items open**, all one blocker — see §3 | [plans/learning-loop.md](plans/learning-loop.md); built state is [reference/knowledge-templates.md](reference/knowledge-templates.md) |
| **Catalog metadata** — DDL comments read on all four engines, into the semantic generator and the run prompt | all 6 phases | Measured, and **not** a win — see §6 | [reference/catalog-metadata.md](reference/catalog-metadata.md) |
| **LangGraph migration** — the chat pipeline and the report worker are compiled graphs; the repair region is one subgraph with two callers | Phases 0–3 and 6 | Phases 4 and 5 **declined on measurement** | [plans/langgraph-migration.md](plans/langgraph-migration.md) |
| **Cross-replica** — claim-before-execute, cancel as a row, SSE over `LISTEN`/`NOTIFY` | as LangGraph Phase 6 | — | [reference/cross-replica.md](reference/cross-replica.md) |
| **Semantic layer render fix** — the layer reached the model on no question at all before this | 2026-08-30 | — | [reference/semantic-layer.md](reference/semantic-layer.md) |

## 3. Built, and shipped **off**

Three switches are off by default, and **all three wait on the same thing: a
provider key this environment does not have.** None of them is blocked on code.

| Off | Switch | What unblocks it |
| --- | --- | --- |
| **Few-shot injection** — taught examples in the generator prompt | `knowledge_examples_enabled`, default false | Held-out accuracy measured not-worse. `PROMPT_VERSION` is v9 and the empty slot renders v8's bytes exactly, so off is not a half-state |
| **The embedding matcher** — searching the knowledge store by meaning | `embedding_model`, default empty | A measured recall delta against `pg_trgm`, which needs no provider and is the default |
| **Phase 0's three eval baselines** — accuracy layer-off, layer-on, recall at a budget that can miss | — | Three runs against a real provider. The table is in [reference/eval.md](reference/eval.md) §6 with the commands and empty cells |

The last one gates the first: the plan does not allow the few-shot flip to be
argued until the baselines exist, because it is the *numbers* that decide it.

## 4. What is next

From [plans/mvp2.md](plans/mvp2.md) Part 4. **Tier 1 is ten of twelve done.**

**Tier 1 — remaining:**

| # | Item | State |
| --- | --- | --- |
| 8 | **Hybrid retrieval** (B2) — embeddings over table and column names blended with exact match and FK expansion | **Not built.** The embedding work that landed is the *knowledge-template* matcher (A5); the `retrieve` node still selects tables on word overlap |
| 11 | **Excel export** (E3) | CSV is built on chat answers, tiles and report figures; Excel is not |
| 12 | **CI hygiene** — `npm test` into CI, mypy honest or dropped, eslint installed or the script deleted | **Not done.** All three are still as [plans/mvp2.md](plans/mvp2.md) §1.10 describes |

**Tier 2 — pick two or three.** Sharing (D1–D2) is done, delivered by the
access-control work above. Of the rest, none is started: file upload (E1),
an MCP server and REST API (E2), dashboard filters (C4 — `QueryExecutor.execute`
still takes no bind parameters), data threads (C2), metric alerts (F1), direct
manipulation of a result (C3), rolling conversation summaries (B4).

## 5. Deferred on purpose, with triggers

A deferral is a decision, not an omission. Each of these has a written trigger;
when the trigger fires, the deferral is reopened rather than re-argued.

| Deferred | Trigger to revisit | Argued in |
| --- | --- | --- |
| Celery + Redis for run execution | p95 run > ~5 min, or runs must survive rolling deploys | [history/architecture-proposal.md](history/architecture-proposal.md) §17 |
| Dashboard filters / bound parameters | Someone extends `QueryExecutor.execute` — **never** by string interpolation | [reference/dashboards.md](reference/dashboards.md) §10 |
| Rolling conversation summaries | A thread outgrows the last-six-messages window | [plans/mvp2.md](plans/mvp2.md) B4 |
| LangGraph checkpointing | 88 KB of state per node, 97% of it the schema block, for a 5–60s run. Declined on that measurement | [plans/langgraph-migration.md](plans/langgraph-migration.md) Phase 4 |
| Durable clarification | Declined on the same kind of measurement | [plans/langgraph-migration.md](plans/langgraph-migration.md) Phase 5 |
| OIDC / SSO | First enterprise deal. The seams are built: `auth_provider`, provider-namespaced identities, `(provider_id, source_id)` on teams and roles | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md) §20 |
| Row-level security | Named in a deal; **needs dashboard filters first** | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md) §0.2 |
| Workspaces / folders, nested teams, delegated granting, time-boxed grants | Each has a written trigger | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md) §0.2 |
| Warehouse connectors | A named customer, not a roadmap slot | [plans/mvp2.md](plans/mvp2.md) E4 |
| Multi-step "deep dive" analysis, root-cause / key drivers, proactive digests | Tier 1 accuracy is credible and users start asking *why* | [plans/mvp2.md](plans/mvp2.md) Tier 3 |
| Scheduled reports | Needs sharing first — a delivered report is a shared report | [plans/mvp2.md](plans/mvp2.md) F2 |
| Entity/value dictionaries | Needs its own disclosure-ladder decision in [reference/security.md](reference/security.md) | [plans/mvp2.md](plans/mvp2.md) B3 |
| Rename the `raymand` package → `datamind` | Before an open-source push; **never** incidentally | [plans/mvp2.md](plans/mvp2.md) §1.11 |

**Not built, and not wanted** (from the access-control work): an external
authorization service, a second source of truth for permissions, `deny` rules
or priority ordering, anonymous public share links, and UI-only restrictions
treated as security.

## 6. The numbers, and how to read them

- **Execution accuracy 0.36** — DeepSeek V4 Pro, temperature 0.2,
  `PROMPT_VERSION` **v2**, 2026-07-26, on the deliberately-messy `sales`
  fixture. This is the number MVP2 exists to move. It is **model-specific and
  version-specific**: read `sales_v1.baseline.json`'s `_README` before quoting
  it, and never put two numbers from different models in one sentence.
- **Catalog comments: 40.0% uncommented vs 36.0% commented**, 50 questions,
  DeepSeek V4 Flash. Two questions inside a twelve-question variance.
  **Do not write "comments improved accuracy" anywhere** — no run says so. What
  one run does say: neither deliberately false comment was ever believed, and
  every metric about writing *valid* SQL improved.
- **Two prompt changes have been measured to *lower* accuracy** — a "getting
  the answer right" block in `GENERATE_SYSTEM` (36% → 26%) and making
  `C_NULLABLE_INNER_JOIN` retry-eligible (0 wins / 4 losses). More instruction
  is not better here.
- **Retrieval recall is 1.0 by construction at the shipped ceiling.** The
  budget is 50k and the fixture estimates 26,480, so a default run takes
  `FULL_SNAPSHOT` on every question. Lower it with `--retrieve-budget` to
  measure it, and never compare a lowered-budget figure to a full-snapshot one.
- **`runs.prompt_version` lied between 2026-07-26 and 2026-08-31.** It was
  stamped from a config default that had drifted. Fixed 2026-08-31; historical
  rows were **deliberately not rewritten**, so a version on a run from that
  window is unknown, not v2.

## 7. Known inconsistencies in the repo itself

Not bugs exactly — places where two files disagree and the disagreement costs
somebody twenty minutes. Listed so they are found before they are stumbled on.

| What | The two answers | Which wins in practice |
| --- | --- | --- |
| **The package name** | The product is *DataMind*; the Python package is `raymand` (import `app.*`), as are the compose project and the app database | Both. Renaming is a deliberate separate task — don't do it incidentally |
| **The bootstrap admin address** | `core/config.py` and `docker-compose.yml` fall back to `admin@raymand.local`; `.env.example` sets `admin@raymand.com` | **`.com`** — `make secrets` copies `.env.example` to `.env`, and compose reads `.env` in preference to its own fallback. Follow the quick start and you sign in as `admin@raymand.com`. Check your `.env` rather than assuming |
| **`AUTHZ_BACKEND`** | `core/config.py` defaults to `rbac`; `.env.example` sets `owner_only` | **`owner_only`** on a fresh clone, for the same reason — `.env` wins. Set it to `rbac` to exercise roles, teams and grants |

---

## Keeping this page honest

Update it **in the commit that changes what it says**, never in a batch
afterwards. A status page that runs ahead of the tree is worse than none, for
the same reason the eval's charter says *"an eval you are allowed to edit
measures your willingness to edit it."*

When a plan's ledger and this page disagree, **the ledger is right** — it sits
beside the work. This page is the index over all of them.
