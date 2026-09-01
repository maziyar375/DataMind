# Documentation index

Eighteen documents plus four research notes. This page exists so you don't have
to open `architecture.md` (1,500 lines) to answer a question about charts.

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
| A prompt, or anything a model is sent | [llm-calls.md](llm-calls.md) | Every unique LLM call: trigger, gateway method, the verbatim system and user prompt, what fills each placeholder, and what happens when it fails |
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
| Knowledge templates — the store, the matcher, the badge, feedback | [learning-loop-plan.md](learning-loop-plan.md) | What a taught question *is*, the guard's fifth entry point, the disclosure rung its literals need, and the phase ledger |

Those last two are also **plans**, in the sense below — they are the reference
for their subject *and* the record of how it was built. Read the §-pointers in
the status table before starting anywhere else in them.

## Plans and records

These five are **narratives of work**, not references: each was written to be
executed against, and each carries a dated ledger of what changed while it was
being executed. They stay in `docs/` rather than in a `plans/` subfolder because
the cross-links point at them from the reference docs — the classification below
is the organisation, not the directory.

| Doc | Status |
| --- | --- |
| [langgraph-migration.md](langgraph-migration.md) | **Live.** Phases 0–3 and 6 are done — the chat pipeline and the report worker are compiled graphs, the repair region is one subgraph with two callers, and the cross-replica work landed as [cross-replica.md](cross-replica.md). Phase 4 (checkpointing) and Phase 5 (durable clarification) are argued and *declined*, each with the measurement that decided it. Read it before moving anything else onto LangGraph. |
| [catalog-metadata-plan.md](catalog-metadata-plan.md) | **Live, and still the reference.** Unlike `reports-plan.md` this one was never superseded by a companion: it is both the plan and the only description of catalog comments, so §10's ledger and "decisions changed while executing" are the record of what actually shipped. Read §1 for the per-engine SQL and §4 for what reaches the model. |
| [learning-loop-plan.md](learning-loop-plan.md) | **Live.** Teaching the system a question and measuring whether it helped. All nine phases are in the tree — the store and the curation surface, match/short-circuit/badge, feedback and the backlog, store health, few-shot, the in-product benchmark, the embedding matcher, permissions — but **three of them ship *off***: the few-shot block, the embedding matcher and Phase 0's own baselines all wait on the same thing, a provider key this environment does not have. §13 is the ledger, one checkbox per deliverable with the check that proves its state, and §13.13 is the dated record of each landing. Read §0.2 before arguing with any of it — four decisions are recorded there rather than re-argued. |
| [mvp2-plan.md](mvp2-plan.md) | **Live.** The wider second-milestone plan the learning loop is one strand of. Where it and `learning-loop-plan.md` disagree about the knowledge store, §1.3 of the latter is the correction. |
| [reports-plan.md](reports-plan.md) | **Superseded.** The phase-by-phase plan for Reports, kept as the record of what was intended. [reports.md](reports.md) describes what was built — where they disagree, reports.md is right. |

## Research

`research/` holds four notes, each answering *"what do the other four products
do about this, and what does that tell us"*: [the learning
loop](research/learning-loop.md) (the argument behind
`learning-loop-plan.md` — read it for *why*, and the plan for *what*),
[retrieval at scale](research/retrieval-at-scale.md), [the semantic layer as a
model](research/semantic-layer-as-a-model.md), and [the data
surface](research/data-surface.md). They are arguments with evidence, not
descriptions of this codebase; where a research note and a shipped doc
disagree, the shipped doc is what the code does.

## Not documentation

`assets/` holds the original UI design concept (`ui-design-concept.html`) and
its generated runtime bundle (`support.js`). The frontend's `theme/tokens.ts`
takes its colour values from that file verbatim. Nothing in `assets/` is prose —
skip it when searching.
