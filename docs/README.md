# Documentation

Thirty-eight documents, in five groups. **Which group a document is in tells
you how to read it**, and that is the whole point of the arrangement:

| Folder | What it is | How to read it |
| --- | --- | --- |
| this page, [status.md](status.md), [decisions.md](decisions.md), [development.md](development.md) | Orientation | Start here |
| [`reference/`](reference/) | **How the built system works** | As fact. Where a reference doc and any other document disagree, the reference doc is right |
| [`plans/`](plans/) | Work: what is being built, in what order, and its ledger | As intent, plus a dated record of what landed |
| [`research/`](research/) | Arguments with evidence — what other products do, and what that implies | As argument. **Never** as a description of this codebase |
| [`history/`](history/) | Superseded or pre-build documents | For the *reasoning*, which is not recoverable from the code. **Not** as a description of the present |

Two more entry points sit outside `docs/`: [../README.md](../README.md) for
people *using* DataMind, and [../CLAUDE.md](../CLAUDE.md) for anyone — human or
agent — about to *change* it.

---

## Start here

| If you are asking… | Read |
| --- | --- |
| What is this project? | [../README.md](../README.md) |
| Where is the project right now? What is built, what is next? | **[status.md](status.md)** |
| What has already been decided, and why? | **[decisions.md](decisions.md)** |
| I am about to change code | [../CLAUDE.md](../CLAUDE.md) — the map, the invariants, the gotchas |
| How do I run, test and verify it? | [development.md](development.md) |
| How does the whole stack fit together? | [reference/codebase.md](reference/codebase.md) — a code-grounded tour |
| Why is it shaped this way? | [decisions.md](decisions.md) first, then [history/architecture-proposal.md](history/architecture-proposal.md) |

## Before you touch…

| Touching | Read first | Why |
| --- | --- | --- |
| `sqlguard/`, `disclosure.py`, `HintBudget`, or adding an LLM call site | [reference/security.md](reference/security.md) | Every claim names the module that enforces it, and states its limits |
| **Anything permission-shaped — any endpoint at all** | [reference/access-control.md](reference/access-control.md) | **The rulebook.** Seven concepts, five invariants, the algorithm verbatim, three checklists. Short on purpose; `test_authz_conformance.py` and `make authz-check` enforce the mechanical half |
| A pipeline node, a prompt, the routing | [reference/pipeline-chat.md](reference/pipeline-chat.md) | The chat run node by node — and §0 maps all three pipelines |
| A tile's SQL or its refresh | [reference/pipeline-dashboard.md](reference/pipeline-dashboard.md) | Authoring (model, once) vs refresh (no model, forever), with every error code |
| Outline, feasibility, report generation | [reference/pipeline-report.md](reference/pipeline-report.md) | The four report flows node by node |
| A prompt, or anything a model is sent | [reference/llm-calls.md](reference/llm-calls.md) | Every unique call: trigger, gateway method, the verbatim prompts, what fills each placeholder, what happens when it fails |
| Provider rows, model parameters, embedder configuration | [reference/llm-providers.md](reference/llm-providers.md) | The two creatable kinds, the parameter catalog, and the three rules about what reaches the wire |
| Anything under `frontend/src/` | [reference/frontend.md](reference/frontend.md) | The shell, what each section owns, the breakpoints, and the design-system rules nothing in CI enforces |
| Chart selection or Vega-Lite output | [reference/charts.md](reference/charts.md) | What it draws, what it refuses to draw, and why |
| Tiles, saved-SQL execution, `query_service.py` | [reference/dashboards.md](reference/dashboards.md) | The second entry point to the guard, and the six rules it obeys |
| Report generation, prose, print | [reference/reports.md](reference/reports.md) | Data model, generation order, where the numbers come from |
| The semantic layer — generation, render, validation | [reference/semantic-layer.md](reference/semantic-layer.md) | The tiered fit, what is refused, and what survives a regeneration |
| Knowledge templates — the store, the matcher, the badge, feedback | [reference/knowledge-templates.md](reference/knowledge-templates.md) | What a taught question *is*, the guard's fifth entry point, and the two switches that ship off |
| A connector's catalog reads, or what a DDL comment does to a prompt | [reference/catalog-metadata.md](reference/catalog-metadata.md) | Each engine's comment SQL as executed, the layer-wins rule, the per-engine hazards |
| Retrieval, prompts, anything you want to prove helped | [reference/eval.md](reference/eval.md) | The golden set, the metrics, and the CI gate |
| Claiming, cancelling, SSE fan-out, the reconciler | [reference/cross-replica.md](reference/cross-replica.md) | What stops being true with more than one API process, and the seven fixes |
| Orchestration — moving anything else onto LangGraph | [plans/langgraph-migration.md](plans/langgraph-migration.md) | Which surfaces moved, which didn't, and the two phases declined on measurement |
| *"Why can this person not see that?"* | **Administration → Access review** (`/admin/access`), then [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md) §15.2 | The screen answers it from the same five facts the authorizer decides from, and every row names the **path** — owner, direct, team, role, wildcard. `GET /me/permissions` is the same lens pointed at yourself, and needs no capability |

## `reference/` — how the built system works

The authoritative description of what exists. Seventeen documents.

**The system:** [codebase.md](reference/codebase.md) ·
[frontend.md](reference/frontend.md) ·
[security.md](reference/security.md) ·
[access-control.md](reference/access-control.md) ·
[cross-replica.md](reference/cross-replica.md)

**The three pipelines** — one set, written to the same shape:
[pipeline-chat.md](reference/pipeline-chat.md) (which also maps all three in
its §0) · [pipeline-dashboard.md](reference/pipeline-dashboard.md) ·
[pipeline-report.md](reference/pipeline-report.md)

**The model layer:** [llm-calls.md](reference/llm-calls.md) (what is sent) ·
[llm-providers.md](reference/llm-providers.md) (how a provider is configured)

**The features:** [dashboards.md](reference/dashboards.md) ·
[reports.md](reference/reports.md) ·
[charts.md](reference/charts.md) ·
[semantic-layer.md](reference/semantic-layer.md) ·
[knowledge-templates.md](reference/knowledge-templates.md) ·
[catalog-metadata.md](reference/catalog-metadata.md) ·
[eval.md](reference/eval.md)

Two of these are **also** the record of how their subject was built, because
nothing superseded them: `catalog-metadata.md` carries its own phase ledger
(§10), and `eval.md` carries the baseline table. Both are still the reference
for their subject.

## `plans/` — work, and its ledger

A plan is a **narrative of work**, not a reference: written to be executed
against, and carrying a dated record of what changed while it was. Read
[status.md](status.md) for which are done.

| Plan | State |
| --- | --- |
| [mvp2.md](plans/mvp2.md) | **Live — the current milestone.** Where MVP1 is weak, what the other four products do about it, and a three-tier proposal. Five of its strands have since been built as the plans below; §4 of [status.md](status.md) is what remains |
| [learning-loop.md](plans/learning-loop.md) | **Built**, 83/85 items. The store, the matcher, the badge, feedback, store health, the benchmark, provenance. **Phase 0's three baselines were run 2026-09-22** and are in [reference/eval.md §6](reference/eval.md). **Two switches still ship off**, each waiting on a gate run of its own. §13 is the ledger. Built state: [reference/knowledge-templates.md](reference/knowledge-templates.md) |
| [user-management-and-access-control.md](plans/user-management-and-access-control.md) | **Built** — all eleven phases, 254/254. Users, service users, roles, teams, grants on eight resource types. Read §0.4 first: eighteen decisions, four of which reverse [history/access-control-plan.md](history/access-control-plan.md). Part 6 is the maintained checklist; Part 4's per-phase boxes stopped being kept and are **not** outstanding work. The day-to-day document is [reference/access-control.md](reference/access-control.md) |
| [access-control-fixes.md](plans/access-control-fixes.md) | **Built**, 2026-09-18. A behavioural test pass over access control — fifteen server findings (creation capabilities never checked, team-granted data refused at execution, two report crashes, …) and the interface that offered edit controls to viewers — and the fixes. `tests/unit/test_access_behaviour.py` holds them |
| [semantic-layer-model.md](plans/semantic-layer-model.md) | **Built**, 61/63 items. The semantic layer as a model rather than a blob: one binder for every reader, numbered versions with typed changes and a revision check, draft and publish, metric attribution, export and import, and upkeep — all six phases landed by 2026-09-17. Two Phase 3 measurements wait on a provider key and real runs. §14 is the ledger |
| [token-accounting.md](plans/token-accounting.md) | **Built** — all six phases, migration `0023`. Usage travels by sink; tokens and cost are counted rather than assumed, per node, per operation, per user |
| [langgraph-migration.md](plans/langgraph-migration.md) | **Live.** Phases 0–3 and 6 done; Phases 4 (checkpointing) and 5 (durable clarification) argued and **declined**, each with the measurement that decided it. Read it before moving anything else onto LangGraph |
| [hybrid-retrieval.md](plans/hybrid-retrieval.md) | **Live — Phase 1 of 3 done.** mvp2 **B2**, the last unbuilt Tier 1 row. Phase 1 (2026-09-23) made the schema's *prose* rank tables — DDL comments and the semantic layer's descriptions, IDF-weighted against the question — on the one branch that has to choose; `PROMPT_VERSION` → v12. Phases 2–3 are the vectors over the same text and what the index says about itself. Two divergences from mvp2 §B2 are argued in §0.2: **no `pgvector`**, and `include_db_comments` governing ranking as well as rendering. §11 is the ledger, and **the arm it owes is unrun** |
| [deep-analysis-mode.md](plans/deep-analysis-mode.md) | **Phases 0–3 done, 31/85 — and the gate did not open.** A second, opt-in answer mode in chat: a declared plan, several guarded queries, deterministic contribution arithmetic, and a report whose every claim resolves to its SQL. **Phase 0 was the gate** — it ran on 2026-09-22 and returned **0.42**, §0.3's middle band, so Phases 1–3 (cache tokens, `app/analysis/`, claim citations) shipped and **Phases 4–9 are blocked** until [mvp2](plans/mvp2.md) A1/A5/B2 move the number. §12 is the ledger. Argued in [research/deep-analysis-mode.md](research/deep-analysis-mode.md) |

## `research/` — arguments, not descriptions

Eight notes. Each reads what other products do and proposes what DataMind should
take from it. **Where a research note and a reference doc disagree, the
reference doc is what the code does.**

| Note | Becomes |
| --- | --- |
| [learning-loop.md](research/learning-loop.md) | [plans/learning-loop.md](plans/learning-loop.md) — read this for *why*, the plan for *what* |
| [access-control.md](research/access-control.md) | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md). Reads Lakekeeper's Keycloak-plus-OpenFGA design down to its `.fga` files, calibrated against Metabase, Superset and Grafana. Its §0 and §5.2 correct two things this repo believed about its own authorization |
| [llm-observability.md](research/llm-observability.md) | [plans/token-accounting.md](plans/token-accounting.md). Four options for LLM observability, and why fixing the usage-reporting gap comes first |
| [retrieval-at-scale.md](research/retrieval-at-scale.md) | [plans/retrieval-sections.md](plans/retrieval-sections.md) (its §O4+§O5, *shrink the haystack*) and [plans/hybrid-retrieval.md](plans/hybrid-retrieval.md) (its §O7+§O8, *search it better*). One row of its §4.10 matrix is **wrong** and the second plan's §6 says why: a ranking change does move `PROMPT_VERSION`, because what the model reads includes which tables survive the cut |
| [semantic-layer.md](research/semantic-layer.md) | [plans/semantic-layer-model.md](plans/semantic-layer-model.md), and mvp2 §1.3 and Theme B. Its §5 carries three corrections to that section |
| [data-surface.md](research/data-surface.md) | Not yet a plan — mvp2 Theme E |
| [competitive-matrix.md](research/competitive-matrix.md) | Not a plan — a scoreboard. mvp2 §2.6's matrix re-run on 2026-09-17 against Power BI, Wren AI, Genie, **Superset and Metabase**, with the DataMind column updated to the tree. Its §3 lists the nine rows that moved since August; its §4 argues that dashboard filters unblock more of the gap than anything else |
| [deep-analysis-mode.md](research/deep-analysis-mode.md) | [plans/deep-analysis-mode.md](plans/deep-analysis-mode.md) — read this for *why*, the plan for *what*. mvp2 Theme F (**F3** bounded multi-step analysis, **F4** root-cause/key drivers), both still Tier 3. Reads Genie Agent mode, Fabric data agents, Wren, Metabase and Superset for the *second answer mode*, and the decomposition / TAG / deep-research-agent literature for the technique. Its §4 is an inventory of what this product already has for it; its §6.1 is the one measurement to take before building any of it |

## `history/` — superseded, and kept

Nothing here describes the present. Each is kept because it carries reasoning
that the code does not, and each names the document that replaced it.

| Document | Superseded by | Why it is kept |
| --- | --- | --- |
| [architecture-proposal.md](history/architecture-proposal.md) | [reference/codebase.md](reference/codebase.md) for what exists | The pre-build proposal, and still the best answer to *"why is it like this, and what was deferred on what trigger?"*. Read its status banner: five things have moved decisively since |
| [reports-plan.md](history/reports-plan.md) | [reference/reports.md](reference/reports.md) | The phase-by-phase intent, and the arguments behind each decision, which are not recoverable from the code |
| [access-control-plan.md](history/access-control-plan.md) | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md) | Nothing shipped *as written*, but its port, its lattice, its intersection rule and its OIDC recipe all survive into the plan that replaced it |
| [ui-improvement-plan.md](history/ui-improvement-plan.md) | [reference/frontend.md](reference/frontend.md) | **Done** — all seven phases, 55 items. Read it for *why* each surface is shaped the way it is, and its ledger for the four decisions taken while executing and the five things review found wrong afterwards |

## Not documentation

- [`assets/`](assets/) — the original UI design concept (`ui-design-concept.html`)
  and its generated bundle. `theme/tokens.ts` takes its **dark** values from it
  verbatim; the concept is dark-only and the light palette was designed
  afterwards. Nothing in here is prose — skip it when searching.
- [`screenshots/`](screenshots/) — the three images the root README embeds, with
  a note on how to re-capture them.
- `backend/app/eval/reports/` and `backend/app/eval/suites/CHANGELOG.md` live
  beside the code that produces them. The reports are write-ups of past eval
  runs; the changelog is the frozen golden set's correction log. Both are
  referenced from [reference/eval.md](reference/eval.md).

---

## Conventions

- **One source of truth per subject.** If two documents could answer the same
  question, one of them says which is authoritative — in its own header, in the
  first paragraph. Add that line before adding the second document.
- **Status banners are load-bearing.** Every plan and every historical document
  opens with what state it is in. Update it in the commit that changes the
  state, never in a batch afterwards.
- **A ledger is ticked by the commit that lands the work** — never in advance,
  never in a batch. *A checklist that runs ahead of the tree is worse than no
  checklist.*
- **Cross-link rather than restate.** Duplicated prose drifts; a link does not.
