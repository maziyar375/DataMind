# Documentation index

Fifteen documents, ~11k lines. This page exists so you don't have to open
`architecture.md` (1,500 lines) to answer a question about charts.

The three `pipeline*.md` files are one set: [pipeline.md](pipeline.md) holds the
chat run **and** the map of all three pipelines (§0), with
[pipeline-dashboard.md](pipeline-dashboard.md) and
[pipeline-report.md](pipeline-report.md) taking the other two in the same shape.

## Start here

| If you are… | Read |
| --- | --- |
| Using DataMind | [../README.md](../README.md) |
| About to change code | [../CLAUDE.md](../CLAUDE.md) — the map, the invariants, the gotchas |
| Learning the stack | [CODEBASE.md](CODEBASE.md) — a code-grounded tour |
| Asking *why* it is shaped this way | [architecture.md](architecture.md) |

## By what you are touching

| Touching | Read first | Why |
| --- | --- | --- |
| `sqlguard/`, `disclosure.py`, `HintBudget`, or adding an LLM call site | [security.md](security.md) | Every claim names the module that enforces it, and states its limits |
| A pipeline node, a prompt, the routing | [pipeline.md](pipeline.md) | The chat run node by node — plus §0, which maps all three pipelines and lists every LLM call site |
| A tile's SQL or its refresh | [pipeline-dashboard.md](pipeline-dashboard.md) | Authoring (model, once) vs refresh (no model, forever), step by step, with every error code |
| Outline, feasibility, report generation | [pipeline-report.md](pipeline-report.md) | The four report flows node by node: prompts, salvage parsing, prose, and what each failure costs |
| Chart selection, Vega-Lite output | [charts.md](charts.md) | What it draws, what it refuses to draw, and why |
| Tiles, saved-SQL execution, `query_service.py` | [dashboards.md](dashboards.md) | The second entry point to the guard, and the six rules it obeys |
| Report generation, prose, print | [reports.md](reports.md) | Data model, generation order, where the numbers come from |
| Retrieval, prompts, anything you want to prove helped | [eval.md](eval.md) | The golden set, the metrics, and the CI gate |
| Claiming, cancelling, SSE fan-out, the reconciler | [cross-replica.md](cross-replica.md) | What stops being true with more than one API process, and the seven fixes |
| A connector's catalog reads, or what a DDL comment does to a prompt | [catalog-metadata-plan.md](catalog-metadata-plan.md) | Each engine's comment SQL as actually executed, the layer-wins suppression rule, and the per-engine hazards |
| Orchestration — moving anything else onto LangGraph | [langgraph-migration.md](langgraph-migration.md) | Which surfaces moved and which didn't, the two phases declined on measurement, and the checklist |

Those last two are also **plans**, in the sense below — they are the reference
for their subject *and* the record of how it was built. Read the §-pointers in
the status table before starting anywhere else in them.

## Plans and records

These three are **narratives of work**, not references: each was written to be
executed against, and each carries a dated ledger of what changed while it was
being executed. They stay in `docs/` rather than in a `plans/` subfolder because
47 cross-links point at them from the reference docs — the classification below
is the organisation, not the directory.

| Doc | Status |
| --- | --- |
| [langgraph-migration.md](langgraph-migration.md) | **Live.** Phases 0–3 and 6 are done — the chat pipeline and the report worker are compiled graphs, the repair region is one subgraph with two callers, and the cross-replica work landed as [cross-replica.md](cross-replica.md). Phase 4 (checkpointing) and Phase 5 (durable clarification) are argued and *declined*, each with the measurement that decided it. Read it before moving anything else onto LangGraph. |
| [catalog-metadata-plan.md](catalog-metadata-plan.md) | **Live, and still the reference.** Unlike `reports-plan.md` this one was never superseded by a companion: it is both the plan and the only description of catalog comments, so §10's ledger and "decisions changed while executing" are the record of what actually shipped. Read §1 for the per-engine SQL and §4 for what reaches the model. |
| [reports-plan.md](reports-plan.md) | **Superseded.** The phase-by-phase plan for Reports, kept as the record of what was intended. [reports.md](reports.md) describes what was built — where they disagree, reports.md is right. |

## Not documentation

`assets/` holds the original UI design concept (`ui-design-concept.html`) and
its generated runtime bundle (`support.js`). The frontend's `theme/tokens.ts`
takes its colour values from that file verbatim. Nothing in `assets/` is prose —
skip it when searching.
