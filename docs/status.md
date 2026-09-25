# Project status

**Where DataMind is right now.** One page, so that "what is built?" and "what
is next?" do not require reading five ledgers.

> **Verified against the tree on 2026-09-11**, at `main` / `00e034f`, with
> §2's newest row added 2026-09-13 as it landed. Every
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
| **Deep analysis** (mvp2 **F3**/**F4**) — a second, opt-in answer mode in chat: a declared plan of up to five sub-questions, each answered by one ordinary guarded query, the arithmetic computed by `app/analysis/` rather than narrated, and a short answer whose every figure cites its step; the reader watches the plan fill in and can press *Answer now*. Governed: `deep.run` (Administrator only by default) and a per-connection budget that narrows the installation's ceiling, is snapshotted onto each run, and refuses at zero rather than running smaller. The fourth pipeline — [reference/pipeline-deep.md](reference/pipeline-deep.md) | 2026-09-22 → 09-24, [plans/deep-analysis-mode.md](plans/deep-analysis-mode.md) **all ten phases**, migrations `0037`, `0038`, `0041`, `0042` | **Built over its own gate, and shipped off** (§3). §0.3 said Phases 4–9 wait until single-shot accuracy reaches 0.55; it is **0.42**, and they were built on the owner's instruction. **No deep number is evidence the mode works.** The one real run (Flash, one question) was cut by its soft deadline at step 3 of 5 and attributed a calendar effect to channels. The running stack needs `make migrate` | [plans/deep-analysis-mode.md](plans/deep-analysis-mode.md) §12 |
| **Retrieval says which signal chose each table** — `runs.retrieval_signals` (`0040`) records, per run, how many of the tables the model saw were chosen by their own name, a curator's word, the conversation, a column, a join, a description or its vector — counted by the same function that ranked them. The tables the budget cut are **named** in the step trail, not only counted; and the knowledge panel's one pin now reports both indexes it feeds | 2026-09-23, [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) **Phase 3** (mvp2 **B2**) | The running stack needs `make migrate`. **NULL on three of the four strategies** deliberately — they send everything, the section, or whatever spends the budget, so none of them chose. Read off this database the day it landed: 3 recorded runs, all `FULL_SNAPSHOT`, 13 tables — so **every run here will write NULL**, and the instrument is correct while this install cannot exercise it | [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) §11 |
| **Retrieval can find a table by what its prose *means*** — where a connection has an embedding model pinned, one vector per table over the same sentences Phase 1 scores (`schema_table_vectors`, `0039`), written by a worker on its own cadence and never on a request; `retrieve` embeds the question once and takes `max(lexical, rescaled cosine)`, so a question sharing no word with a table's description can still reach it. **Availability is a capability, not a switch**: no pinned model is no index | 2026-09-23, [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) **Phase 2** (mvp2 **B2**) | Migrations `0039`/`0040` were applied to the local database on 2026-09-23 and the API restarted. **Both connections here have `text-embedding-3-small` pinned at 256 dimensions**, so the hourly pass *will* build this index — 21 tables for `sales`, 13 for Aurora — the first time it runs. **Those vectors will then sit unused on this install**, because a vector can only change a ranking on `RANKED_MATCH` and both databases fit the 50,000-char budget whole. **Unmeasured**: the arm exists (`--schema-vectors`, added to the runner for it) and has not been run | [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) §11 |
| **The schema's own sentences choose tables** — a question phrased in none of the words the schema spells now reaches the table somebody *wrote about*: table and column DDL comments, plus the semantic layer's descriptions, grains, value meanings and glossary meanings, are scored against the question by IDF-weighted overlap (`app/pipeline/relevance.py`) and rank a table above the ones that are merely large. Nothing is read by both this and A5's name index. `include_db_comments` governs ranking as well as rendering. `PROMPT_VERSION` v11 → v12 | 2026-09-23, [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) **Phase 1** (mvp2 **B2**) | **Same branch limit as A5** — only `RANKED_MATCH`, above roughly 80 tables — so it changes nothing on either database in this install. **Unmeasured**: the arm is four cells of one grid with A5's (`--retrieve-budget 8000`, neither / `--semantic on` / `--comments` / both) and has not been run, so no claim that it improved retrieval is falsifiable yet. A connection with no comments and no layer ranks byte-identically to v11, asserted at the function *and* at the node. The embeddings half of B2 is unbuilt | [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) §11 |
| **The semantic layer chooses tables, not only explains them** — a question asked in the connection's own vocabulary reaches the table that word was written about: `app.semantic.table_terms` indexes entity labels and synonyms, column labels and synonyms, metric names and glossary terms by table, and `retrieve` ranks a hit **second**, above a table carried from the last turn and below one the user named outright. `PROMPT_VERSION` v10 → v11 | 2026-09-22, [plans/mvp2.md](plans/mvp2.md) **A5** | **Only the `RANKED_MATCH` branch can see it** — reached above roughly 80 tables — so it changes nothing on either database in this install (Aurora 13 tables, `sales` 42; both take `FULL_SNAPSHOT`). The arm that would measure it **has not been run** — and as of 2026-09-23 it is one cell of the four-cell grid B2 owes, because both features move the same branch and the same ranking function — so no claim that this improved retrieval is falsifiable yet. A connection with no layer retrieves byte-identically to v10, which is asserted rather than assumed | [plans/mvp2.md](plans/mvp2.md) §A5, [reference/pipeline-chat.md](reference/pipeline-chat.md) §4 |
| **Retrieval by sections** — a connection's schema can be divided into named sections (proposed from the FK graph and name prefixes, curated on a Sections tab that sizes each one in `retrieve`'s own units); a new `scope` node picks the section a question is about and `retrieve` sends it whole — `SECTION_SNAPSHOT` — plus any table joining two of its members; the pick is shown on the answer and can be overridden per question with *Ask within…*, which skips the routing call; a follow-up keeps its section; drift after a sync is flagged, never deleted; the analytical fallback now matches on word boundaries and ranks what it sends against the budget, reporting what it dropped; what retrieval did is recorded per run | 2026-09-20, Phases 0-3, migrations `0035` and `0036` | The running stack needs `make migrate`. **Unmeasured by the eval suite** — its fixtures have no sections and `NodeDeps.sections` stays None there by construction; checked live instead, on a clone of a real database. A connection with no sections is byte-identical to before: no model call, no prompt change | [plans/retrieval-sections.md](plans/retrieval-sections.md) §14 |
| **Semantic layer: upkeep** — the generate dialog picks tables, with *Fill the gaps* (never overwrites a field a person wrote, never touches an existing metric, drops nothing) and *Rewrite*; a run over chosen tables changes those tables and nothing else; *Needs attention* lists, from what is already stored, a draft untouched for a week, entries the schema broke, tables whose columns moved since their entity last changed, definitions answers keep leaving out, unreviewed model text Grounded answers stood on, and undescribed tables | 2026-09-17, Phase 5 | No generation was run against a real provider for it; the job path is tested with a fake gateway | [plans/semantic-layer-model.md](plans/semantic-layer-model.md) §4.6 |
| **Semantic layer: the portable document** — export a published version as a JSON file (no ids, hosts or derived fields; value meanings only when asked, and audited); import a file into the draft through the binder, with a report of what did not resolve; text limits on the editor and import (generations clipped); a chained statement is no longer accepted as a metric filter — found by replaying the hostile corpus through `check_expression` | 2026-09-17, Phase 4 | — | [plans/semantic-layer-model.md](plans/semantic-layer-model.md) §4.5 |
| **Semantic layer: metric attribution** — after a run, the last accepted statement is read against the layer version it was written with and each metric on a touched table is `used`, `ignored` or `unknown` (stored on `generated_queries`, fail open); an answer shows `✓ Matches the revenue definition` for `used` only, with the definition on hover; the Metrics panel gains *Metrics in use* (30 days, counts only); benchmark runs report a metric-use rate and the eval scores definition use on both arms | 2026-09-16, Phase 3, migration `0034` | The running stack needs `make migrate`. `ignored` precision is unmeasured (no `ignored` in the 17 real statements available), so it is shown on no answer; the on-arm minus off-arm rate needs the two unmade eval runs | [plans/semantic-layer-model.md](plans/semantic-layer-model.md) §4.4 |
| **Semantic layer: draft and publish** — the editor's Save, a generation and a restore write a draft that no question reads; *Review and publish* writes the version, with a note required when a change alters numbers; a draft can be discarded; *Score this draft* runs a benchmark set against the draft (pinned to its revision, kept out of the score strip) and shows a delta only when prompts and model match; the business context and exclusion rule survive a regeneration on their own provenance flags | 2026-09-16, Phase 2, migration `0033` | The running stack needs `make migrate`; no real *Score this draft* delta has been taken (needs a benchmark set and a provider key) | [plans/semantic-layer-model.md](plans/semantic-layer-model.md) §4.3 |
| **Semantic layer: versions** — every save, generation, restore and delete is a numbered, attributed version with typed changes; writes carry a revision and a stale one is a 409; a generation merges into the current layer under a lock; History, per-entry history and restore in the editor; runs and benchmark runs record the version that answered, and *Grounded* reads it | 2026-09-15, Phase 1, migration `0032` | The running stack needs `make migrate`. Version tables measured 2026-09-17 on the local database: one version (`aurora`'s migrated v1, a 47 kB document), 96 kB with indexes — far under the §11 trigger, and too little history to project growth from | [plans/semantic-layer-model.md](plans/semantic-layer-model.md) §4.2 |
| **Semantic layer: one reader** — every loader binds the layer to the snapshot it renders from (a column a re-sync dropped no longer reaches the model); the in-product benchmark reads the layer; the backlog's vocabulary reads the typed model; *Grounded* respects the switch; the switch is audited. `PROMPT_VERSION` v9 → v10 | 2026-09-15, Phase 0 | Benchmark scores at v9 and earlier were taken **without** the layer | [plans/semantic-layer-model.md](plans/semantic-layer-model.md) §4.1 |
| **Usage over any period, filtered by model** — `GET /usage/*` takes `tz_offset` and returns sub-day buckets (5 min → 1 day, chosen by the server from the window) aligned to the reader's clock, with `since`/`until`/`bucket_seconds` and per-model buckets; the page gains a period row (1h–90d and custom), a model filter, a timeline that runs to now, and ranked By model rows | 2026-09-15 | — | the screen is [reference/frontend.md](reference/frontend.md) |
| **Usage without cost, and by model** — `cost_usd` dropped from `runs`, `report_runs`, `semantic_jobs` and `eval_results`, and nothing prices a call any more (the gateway log line, the eval report and the usage screen included); every usage scope gains a per-model split; the **Installation** tab is renamed **All users** | 2026-09-15, migration `0031` | — | this row; the screen is [reference/frontend.md](reference/frontend.md) |
| **Token usage in the UI** — a **Token usage** rail section over three read routes, tokens on the chat step chips, `usage.read` seeded to Administrator and Auditor | 2026-09-13, all 7 phases, migration `0030` | Counts only, and **input + output only** — see below | [plans/llm-observability-v2-implementation.md](plans/llm-observability-v2-implementation.md); the spec is [plans/llm-observability-v2.md](plans/llm-observability-v2.md) |
| **User management and access control** — users, service users, roles, teams, grants on eight resource types, access review, audit of every authorization event | 2026-09-06 → 09-08, all 11 phases, 254/254 | — | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md); the rulebook is [reference/access-control.md](reference/access-control.md) |
| **Token accounting** — usage travels by sink, per node, per operation, per user; one structured log line per provider call | 2026-09-05, all 6 phases, migration `0023` | — | [plans/token-accounting.md](plans/token-accounting.md) |
| **UI/UX remediation** — routing, the Chat→Dashboard/Report bridge, responsive reflow | 2026-09-03, all 7 phases, 55/55 | — | [history/ui-improvement-plan.md](history/ui-improvement-plan.md); built state is [reference/frontend.md](reference/frontend.md) |
| **The learning loop** — the knowledge store, the curation surface, match/short-circuit/badge, feedback and the backlog, store health, the in-product benchmark, provenance | 2026-08-31 → 09-01, all 9 phases, 82/85 items | **Three items open**, all one blocker — see §3 | [plans/learning-loop.md](plans/learning-loop.md); built state is [reference/knowledge-templates.md](reference/knowledge-templates.md) |
| **Catalog metadata** — DDL comments read on all four engines, into the semantic generator and the run prompt | all 6 phases | Measured, and **not** a win — see §6 | [reference/catalog-metadata.md](reference/catalog-metadata.md) |
| **LangGraph migration** — the chat pipeline and the report worker are compiled graphs; the repair region is one subgraph with two callers | Phases 0–3 and 6 | Phases 4 and 5 **declined on measurement** | [plans/langgraph-migration.md](plans/langgraph-migration.md) |
| **Cross-replica** — claim-before-execute, cancel as a row, SSE over `LISTEN`/`NOTIFY` | as LangGraph Phase 6 | — | [reference/cross-replica.md](reference/cross-replica.md) |
| **Semantic layer render fix** — the layer reached the model on no question at all before this | 2026-08-30 | — | [reference/semantic-layer.md](reference/semantic-layer.md) |

**What the usage screen deliberately does not count, so the next reader does
not assume it was forgotten.** Both are named in the specification and both are
follow-ups with their own work, not oversights:

- **Cache read and cache write tokens.** The schema has `prompt_tokens` and
  `completion_tokens` and nothing else, and so does `Usage` in
  `domain/ports/llm.py` — a port type, so widening it moves every sink and
  every accumulation path with it. Including them would have turned a
  read-and-render phase into a schema phase. The chart's stack is a *list* of
  series for exactly this reason: a third and fourth segment costs a series,
  not a rewrite.
- **Embeddings.** `embed()` spends real tokens and is still uncounted — a
  different unit at a different call site.

Also absent on purpose, and each for a reason in the plan's §1.7: budgets or
quotas (this measures, it does not enforce — a cap would have to fail *closed*,
and everything here fails open), hourly buckets, CSV export, and any backfill
of historical rows. Grouping by model, absent at first, landed on 2026-09-15;
prices were removed the same day.

## 3. Built, and shipped **off**

Four switches are off by default. **Three wait on the same thing — a
provider key this environment does not have — and the fourth waits on a
number.** None of them is blocked on code.

| Off | Switch | What unblocks it |
| --- | --- | --- |
| **Few-shot injection** — taught examples in the generator prompt | `knowledge_examples_enabled`, default false | Held-out accuracy measured not-worse. `PROMPT_VERSION` is v10 (v9 added the slot; v10 changed no wording) and the empty slot renders v8's bytes exactly, so off is not a half-state |
| **The embedding matcher** — searching the knowledge store by meaning | `embedding_model`, default empty | A measured recall delta against `pg_trgm`, which needs no provider and is the default |
| **Phase 0's three eval baselines** — accuracy layer-off, layer-on, recall at a budget that can miss | — | Three runs against a real provider. The table is in [reference/eval.md](reference/eval.md) §6 with the commands and empty cells |
| **Deep analysis** — the fourth pipeline ([reference/pipeline-deep.md](reference/pipeline-deep.md)) | `deep_enabled`, default false; and `deep.run`, held by Administrator alone | **Single-shot execution accuracy ≥ 0.55** on `sales_v1`, layer on — [plans/deep-analysis-mode.md](plans/deep-analysis-mode.md) §0.3's threshold, written before the number existed. It is **0.42**. Until it moves, turning the switch on ships a longer, more confident, more expensive wrong answer |

The last one gates the first: the plan does not allow the few-shot flip to be
argued until the baselines exist, because it is the *numbers* that decide it.

## 4. What is next

From [plans/mvp2.md](plans/mvp2.md) Part 4. **Tier 1 is ten of twelve done**,
and item 8 — hybrid retrieval — is built as of 2026-09-23 but **unmeasured**,
which is why it stays in the table below rather than moving out of it.

**Tier 1 — remaining:**

| # | Item | State |
| --- | --- | --- |
| 8 | **Hybrid retrieval** (B2) — embeddings over table and column names blended with exact match and FK expansion | **Built but unmeasured**, 2026-09-23, all three phases — [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md). `retrieve` no longer selects on word overlap alone: the semantic layer's business vocabulary has named tables since 2026-09-22 (**A5**), and the schema's *prose* — DDL comments and the layer's descriptions — now ranks them (**B2 Phase 1**). The embeddings landed the same day, blended `max(lexical, cosine)` and gated on a pinned embedding model — and the plan diverges from this row on one point: **no `pgvector`**, because the tree already answered that for the same kind of index (`double precision[]`, cosine in Python — learning-loop Phase 7). Phase 3 landed the same day: per-run signal telemetry, index freshness where the pin lives, and the dropped-table names §6.5 of the research asks for. **What is left is not code** — it is the arm, and until it is run no claim that any of this improved retrieval is falsifiable. **B1 no longer blocks it**: recall is measurable at `--retrieve-budget 8000` (80.2 % / 62.0 %, `dc2ea4fd`). *(The earlier note here called the knowledge-template matcher "A5"; that was A1's Phase 7. A5 is the synonym index.)* |
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
| Proactive digests | Deep analysis was deferred beside it and is now built (§2, §3); digests remain Tier 3 | [plans/mvp2.md](plans/mvp2.md) Tier 3 |
| **Enabling** multi-step "deep dive" analysis, root-cause / key drivers | **The deferral to *build* it closed on 2026-09-24 — not by a number.** The trigger was tested on 2026-09-22 and did not fire (**0.42**, the middle band of [plans/deep-analysis-mode.md](plans/deep-analysis-mode.md) §0.3); Phases 4–9 were built over it on the owner's instruction, and the override is recorded in the plan rather than the gate re-read. What stays deferred is **turning it on**, and its trigger is the same number reaching **0.55** — moved by A1/A5/B2, not by this mode | [plans/deep-analysis-mode.md](plans/deep-analysis-mode.md) §0.3, §12 |
| Scheduled reports | Needs sharing first — a delivered report is a shared report | [plans/mvp2.md](plans/mvp2.md) F2 |
| Entity/value dictionaries | Needs its own disclosure-ladder decision in [reference/security.md](reference/security.md) | [plans/mvp2.md](plans/mvp2.md) B3 |
| Rename the `raymand` package → `datamind` | Before an open-source push; **never** incidentally | [plans/mvp2.md](plans/mvp2.md) §1.11 |

**Not built, and not wanted** (from the access-control work): an external
authorization service, a second source of truth for permissions, `deny` rules
or priority ordering, anonymous public share links, and UI-only restrictions
treated as security.

## 6. The numbers, and how to read them

- **Execution accuracy 0.42** — DeepSeek V4 Pro, temperature 0.0,
  `PROMPT_VERSION` **v10**, 2026-09-22, semantic layer **on**, on the
  deliberately-messy `sales` fixture; `eval_run`
  `5df63738-43a9-4b45-8e6d-b0b68e7ba9b6`. The layer-**off** arm scored the same
  42.0 % (`d8c1035d-…`) — **and not on the same questions: fourteen changed
  verdict, seven each way.** This is the number MVP2 exists to move. It is
  **model-specific and version-specific**: read
  [reference/eval.md §6](reference/eval.md) and the write-up
  [`sales_v1_deepseek_2026-09-22_phase0.md`](../backend/app/eval/reports/sales_v1_deepseek_2026-09-22_phase0.md)
  before quoting it, and never put two numbers from different models in one
  sentence.
- **The old 0.36 was v2 at temperature 0.2, 2026-07-26.** It stood as the
  product's only accuracy figure for two months. Superseded, not deleted:
  `sales_v1.baseline.json` still records it as a CI tripwire keyed to a model,
  which is a different job from being the number anyone quotes.
- **`sales_v1` cannot resolve a five-point difference.** 50 questions at
  p ≈ 0.42 carry a standard error near 7 points, so anything under roughly
  ±14 points is noise. The layer-on/layer-off pair above is the demonstration:
  28 % of the suite changed answer and the aggregate did not move.
- **The deep-analysis gate was applied on 2026-09-22, and it did not open.**
  [plans/deep-analysis-mode.md §0.3](plans/deep-analysis-mode.md) wrote three
  thresholds down before the number existed. The number it names — execution
  accuracy at v10 with the layer on — came back **0.42**, which is the middle
  band: **Phases 1–3 ship, Phases 4–9 wait.** Phases 1, 2 and 3 landed
  (cache-token accounting, `app/analysis/`, claim→SQL citations). **Phases
  4–9 were then built anyway, 2026-09-23 → 24, on the owner's instruction** —
  the mode exists and is off (§3). That does not re-read the gate: until
  [plans/mvp2.md](plans/mvp2.md) A1/A5/B2 move the number to 0.55, no deep
  number may be quoted as evidence the mode works, and the switch stays off.
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
  `FULL_SNAPSHOT` on every question. Lowered to 8,000 on 2026-09-22 it reads
  **mean 80.2 % / full-hit 62.0 %** (`dc2ea4fd-…`), which is the only setting
  under which a retrieval claim on this suite is falsifiable. Never compare a
  lowered-budget accuracy figure to a full-snapshot one. And note what that run
  also showed: **two questions scored recall 0.000 and answered correctly** —
  recall@k measures whether retrieval picked the tables the *annotator*
  expected, not whether the model could answer.
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
