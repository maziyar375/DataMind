"""The deep graph's four nodes: `plan`, `step`, `compute`, `synthesize`.

Same shape as every node in `nodes/__init__.py` — `async (state, deps) ->
NodeResult`, mutate the state, report a status, never touch persistence — and
wired by the same adapter, so the `run_steps` rows and the SSE pairs a deep run
writes are written by the code that writes them for chat
(docs/plans/deep-analysis-mode.md §3.5, "Reuse `_adapt` or the contract is
gone").

Everything *between* `step` and `compute` is the chat road, unchanged:
`scope → retrieve → generate ⇄ validate → execute → inspect`. A sub-query is
an ordinary statement, through the ordinary guard, with no new entry point.

**What these nodes never decide is whether the budget allows another step.**
That is the graph's routers (`graph.py`), which read `DeepState.exhausted`
before every step and `DeepState.may_repair` before every repair. A node that
checked its own budget would be a node that could forget to.
"""
from __future__ import annotations

from app.core.errors import LLMError
from app.domain.ports.llm import ChatMessage
from app.pipeline.metadata import select_tables, table_chars
from app.pipeline.nodes import NodeDeps, _render_history, retrieve_budget_chars
from app.pipeline.prompts.deep import DEEP_PLAN_SYSTEM, DEEP_PLAN_USER
from app.pipeline.state import (
    AnalysisPlan,
    DeepState,
    NodeResult,
    RetrievedContext,
    RunError,
    StepEvidence,
)

#: The step label the graph reads as "stop stepping and write the answer".
SYNTHESIZE = "synthesize"


# ── plan ─────────────────────────────────────────────────────────────────
def _planner_schema(state: DeepState, deps: NodeDeps) -> str:
    """The schema block the planner plans against.

    The block `generate` will see, rendered under the same disclosure policy,
    so a plan is never written against a column the generator is not shown.
    Over the retrieve budget it is narrowed the way a schema question is — by
    what the question names, then by size — because a planner that cannot see
    a table cannot plan a step over it, and a planner handed two thousand
    tables plans nothing useful either.
    """
    tables = deps.snapshot.get("tables", [])
    budget = retrieve_budget_chars()
    if sum(table_chars(t) for t in tables) > budget:
        tables = select_tables(state.asked, tables, budget_chars=budget)
    context = RetrievedContext(
        dialect=state.dialect,
        tables=tables,
        relationships=deps.snapshot.get("relationships", []),
        semantic=deps.semantic,
        catalog_meta=deps.snapshot.get("catalog_meta") or {},
        include_db_comments=deps.include_db_comments,
    )
    return context.render(state.disclosure_policy)


def _tidy(plan: AnalysisPlan, max_steps: int) -> tuple[AnalysisPlan, int]:
    """The plan as it will run: blank steps gone, the ceiling applied.

    **Truncated, never honoured.** A plan longer than `max_steps` keeps its
    first `max_steps` steps, because the order is the planner's statement of
    what must come first. A `depends_on` pointing forward, at itself or past
    the end is dropped: a step cannot build on one that has not run.
    """
    steps = [s for s in plan.steps if s.question.strip()]
    kept = steps[:max_steps]
    tidied = [
        s.model_copy(update={
            "depends_on": sorted({d for d in s.depends_on if 0 <= d < i}),
        })
        for i, s in enumerate(kept)
    ]
    return plan.model_copy(update={"steps": tidied}), len(steps) - len(kept)


async def plan(state: DeepState, deps: NodeDeps) -> NodeResult:
    """One structured call: the restatement, the steps, the stop condition.

    A failure here fails the run — there is no evidence yet to answer from,
    and an answer written without a plan would be a chat answer in a deep
    run's clothes.
    """
    system = DEEP_PLAN_SYSTEM.format(
        dialect=state.dialect,
        max_steps=state.budget.max_steps,
        schema=_planner_schema(state, deps),
        history=_render_history(deps.history, state.disclosure_policy),
    )
    try:
        proposal = await deps.llm_gateway.structured(
            deps.llm,
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=DEEP_PLAN_USER.format(question=state.asked)),
            ],
            AnalysisPlan,
            on_usage=state.usage_sink("plan"),
        )
    except LLMError as err:
        state.error = RunError(
            code="E_PLAN",
            message="The analysis could not be planned.",
            hint=err.message,
        )
        return NodeResult(status="FAILED", detail=err.message)

    tidied, dropped = _tidy(proposal, state.budget.max_steps)
    if not tidied.steps:
        state.error = RunError(
            code="E_PLAN",
            message="The analysis could not be planned.",
            hint="The planner proposed no step this database can answer.",
        )
        return NodeResult(status="FAILED", detail="No steps planned")

    state.plan = tidied
    detail = f"{len(tidied.steps)} steps planned"
    if dropped:
        detail += f" · {dropped} past the ceiling dropped"
    return NodeResult(detail=detail)


# ── step ─────────────────────────────────────────────────────────────────
async def step(state: DeepState, deps: NodeDeps) -> NodeResult:
    """Point the chat road at the next sub-question.

    Only ever entered through a router that has already checked the budget,
    so the one thing left to decide is whether the plan has a step left.
    """
    current = state.begin_step()
    if current is None or state.plan is None:
        return NodeResult(goto=SYNTHESIZE, detail="Plan complete")
    total = len(state.plan.steps)
    return NodeResult(
        detail=f"Step {state.cursor + 1} of {total}: {current.question[:160]}"
    )


# ── compute ──────────────────────────────────────────────────────────────
async def compute(state: DeepState, deps: NodeDeps) -> NodeResult:
    """Close the step: record what it produced, and move the cursor.

    Reached from `inspect` on success and from anywhere in the step's road on
    failure — the graph sends a give-up here instead of to the end of the run,
    because **one failed sub-question is evidence, not a failed analysis.**
    The error is moved onto the evidence and cleared from the run.
    """
    current = state.current_step
    if current is None:  # pragma: no cover - the router never sends us here
        return NodeResult(status="SKIPPED", detail="No step in progress")

    last = state.last_attempt if len(state.attempts) > state.step_first_attempt else None
    ran = state.execution is not None and last is not None and last.rewritten_sql
    evidence = StepEvidence(
        index=state.cursor,
        step=current,
        first_attempt=state.step_first_attempt,
        last_attempt=len(state.attempts),
        execution=state.execution if ran else None,
        status="DONE" if ran else "FAILED",
        note="" if ran else (
            state.error.message if state.error else "The step produced no result."
        ),
    )
    if evidence.execution is not None:
        state.rows_spent += evidence.execution.row_count
    state.evidence.append(evidence)
    state.error = None
    state.cursor += 1

    if evidence.status == "FAILED":
        return NodeResult(detail=f"Step {evidence.index + 1} failed: {evidence.note[:200]}")
    return NodeResult(
        detail=f"Step {evidence.index + 1}: {evidence.execution.row_count} rows"  # type: ignore[union-attr]
    )


# ── synthesize ───────────────────────────────────────────────────────────
_STOPPED: dict[str, str] = {
    "steps": "it reached the step limit",
    "queries": "it reached the query limit",
    "rows": "it reached the row limit",
    "tokens": "it reached the token limit",
    "time": "it ran out of time",
    "answer_now": "you asked for an answer now",
}


async def synthesize(state: DeepState, deps: NodeDeps) -> NodeResult:
    """Write the answer from the evidence collected — however much there is.

    The one terminal node every road reaches: a finished plan, an exhausted
    budget, and (Phase 6) *Answer now*. It restores the reader's question
    and puts the last step that ran into `execution`, so `chart` and the
    TABLE artifact describe a real result.
    """
    state.question = state.asked
    done = [e for e in state.evidence if e.status == "DONE"]
    planned = len(state.plan.steps) if state.plan else 0
    state.execution = done[-1].execution if done else None

    lines = [f"Worked through {len(state.evidence)} of {planned} planned steps"]
    if state.stop_reason:
        lines[0] += f", and stopped early because {_STOPPED[state.stop_reason]}"
    lines[0] += "."
    for e in state.evidence:
        mark = "✓" if e.status == "DONE" else "✗"
        lines.append(f"{mark} {e.step.question}")
    state.answer = "\n".join(lines)
    return NodeResult(detail=f"{len(done)} of {planned} steps answered")
