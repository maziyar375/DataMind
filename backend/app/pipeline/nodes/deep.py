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
from app.core.logging import get_logger
from app.domain.ports.llm import ChatMessage
from app.domain.value_objects import DisclosurePolicy
from app.pipeline import evidence as ev
from app.pipeline.metadata import select_tables, table_chars
from app.pipeline.nodes import NodeDeps, _render_history, _Thinking, retrieve_budget_chars
from app.pipeline.prompts.deep import (
    DEEP_PLAN_SYSTEM,
    DEEP_PLAN_USER,
    DEEP_REVISE_SYSTEM,
    DEEP_REVISE_USER,
)
from app.pipeline.state import (
    AnalysisPlan,
    DeepState,
    NodeResult,
    PlanRevision,
    PlanStep,
    RetrievedContext,
    RunError,
    StepEvidence,
    StepRevision,
)
from app.reports import checks
from app.reports.language import detect
from app.reports.narrate import section_messages

log = get_logger(__name__)

#: The policies under which result values may reach a prompt at all — the same
#: two `disclose()` shares rows under. Below them there is nothing for a
#: reviser or a writer to read, so neither is called.
_WIDE = (DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL)

#: The labels the graph reads as "write the answer now" and "close this step
#: without running it".
SYNTHESIZE = "synthesize"
STEP = "step"


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
async def _revise(state: DeepState, deps: NodeDeps, current: PlanStep) -> None:
    """Keep the next step, or replace it now its dependencies have answered.

    Only for a step that names dependencies, only when every one of them
    produced a result, and only under a policy that lets their results reach
    a prompt — otherwise there is nothing to revise *from*, and the step runs
    as planned with no call made. A revision **replaces**; the plan's length
    never changes, so it can never carry the plan past its ceiling.

    Fails open, like `clarify`: a provider error keeps the step as written.
    The findings it reads are `disclosed` and `computed`, rendered through the
    same `narration` the writer uses — never `execution`.
    """
    if not current.depends_on or state.disclosure_policy not in _WIDE:
        return
    earlier = {e.index: e for e in state.evidence}
    blocks = [earlier.get(i) for i in current.depends_on]
    if any(b is None or b.status != "DONE" for b in blocks):
        return
    findings = "\n\n".join(
        ev.narration(b).render(i + 1)  # type: ignore[arg-type]
        for i, b in zip(current.depends_on, blocks, strict=True)
    )
    try:
        verdict = await deps.llm_gateway.structured(
            deps.llm,
            [
                ChatMessage(role="system", content=DEEP_REVISE_SYSTEM),
                ChatMessage(role="user", content=DEEP_REVISE_USER.format(
                    restatement=state.plan.restatement if state.plan else state.asked,
                    question=current.question, intent=current.intent,
                    tool=current.tool, why=current.why, findings=findings,
                )),
            ],
            StepRevision,
            on_usage=state.usage_sink("step"),
        )
    except LLMError as err:
        log.info("deep_revise_failed_open", error=err.message)
        return
    question = verdict.question.strip()
    if verdict.keep or not question or question == current.question:
        return
    replacement = PlanStep(
        question=question, intent=verdict.intent, why=verdict.why or current.why,
        tool=verdict.tool, depends_on=current.depends_on,
    )
    assert state.plan is not None
    state.plan.steps[state.cursor] = replacement
    state.revisions.append(
        PlanRevision(index=state.cursor, replaced=current, by=replacement)
    )


async def step(state: DeepState, deps: NodeDeps) -> NodeResult:
    """Point the chat road at the next sub-question.

    Only ever entered through a router that has already checked the budget,
    so what is left to decide is whether the plan has a step left, whether it
    should be sharpened first, and whether its tool is one `compute` knows. A
    tool it does not know closes the step as SKIPPED **without spending a
    query on it** — the planner selects from a closed set, and anything
    outside it is not run (plan D4).
    """
    current = state.current_step
    if current is None or state.plan is None:
        return NodeResult(goto=SYNTHESIZE, detail="Plan complete")

    await _revise(state, deps, current)
    current = state.begin_step()
    assert current is not None and state.plan is not None
    total = len(state.plan.steps)
    revised = any(r.index == state.cursor for r in state.revisions)

    if current.tool != "SQL" and current.tool not in ev.SHAPE_HINTS:
        state.evidence.append(StepEvidence(
            index=state.cursor, step=current,
            first_attempt=len(state.attempts), last_attempt=len(state.attempts),
            status="SKIPPED", note=f"No computation is called {current.tool!r}.",
        ))
        state.cursor += 1
        return NodeResult(
            status="SKIPPED", goto=STEP,
            detail=f"Step {state.cursor} of {total} skipped: unknown tool",
        )

    # What the generator is asked: the sub-question, plus the shape `compute`
    # needs the rows in. A plain SQL step is asked exactly its own question.
    state.question = ev.with_shape(current)
    return NodeResult(
        detail=f"Step {state.cursor + 1} of {total}"
        + (" (revised)" if revised else "")
        + f": {current.question[:160]}"
    )


# ── compute ──────────────────────────────────────────────────────────────
async def compute(state: DeepState, deps: NodeDeps) -> NodeResult:
    """Close the step: disclose its result, compute over it, move the cursor.

    Reached from `inspect` on success and from anywhere in the step's road on
    failure — the graph sends a give-up here instead of to the end of the run,
    because **one failed sub-question is evidence, not a failed analysis.**
    The error is moved onto the evidence and cleared from the run.

    `disclose()` runs here, per step, under the run's policy — so every
    result is disclosed before anything downstream (a revision, the writer)
    can read it, and they read only what it returned.
    """
    current = state.current_step
    if current is None:  # pragma: no cover - the router never sends us here
        return NodeResult(status="SKIPPED", detail="No step in progress")

    last = state.last_attempt if len(state.attempts) > state.step_first_attempt else None
    ran = state.execution is not None and last is not None and bool(last.rewritten_sql)
    evidence = ev.close_step(
        StepEvidence(
            index=state.cursor,
            step=current,
            first_attempt=state.step_first_attempt,
            last_attempt=len(state.attempts),
            execution=state.execution if ran else None,
            status="DONE" if ran else "FAILED",
            note="" if ran else (
                state.error.message if state.error else "The step produced no result."
            ),
        ),
        state.disclosure_policy,
    )
    state.rows_spent += evidence.row_count
    state.evidence.append(evidence)
    state.error = None
    state.cursor += 1

    n = evidence.index + 1
    if evidence.status == "FAILED":
        return NodeResult(detail=f"Step {n} failed: {evidence.note[:200]}")
    detail = f"Step {n}: {evidence.row_count} rows"
    if evidence.computed is not None:
        tool = evidence.computed["tool"].lower().replace("_", " ")
        refusal = evidence.computed["refusal"]
        detail += f" · {tool} " + (
            "computed" if evidence.computed["ok"] else f"refused ({refusal})"
        )
    return NodeResult(detail=detail)


# ── synthesize ───────────────────────────────────────────────────────────
_STOPPED: dict[str, str] = {
    "steps": "it reached the step limit",
    "queries": "it reached the query limit",
    "rows": "it reached the row limit",
    "tokens": "it reached the token limit",
    "time": "it ran out of time",
    "answer_now": "you asked for an answer now",
}


def _preface(state: DeepState) -> str:
    """The sentence that says how much of the plan the answer stands on.

    Written by the product, never by the writer, and first — so an answer
    built from three steps of five says so before it says anything else.
    Empty when every planned step ran.
    """
    planned = len(state.plan.steps) if state.plan else 0
    if not state.stop_reason and len(state.evidence) >= planned:
        return ""
    return (
        f"This answer is built from {len(state.evidence)} of {planned} planned "
        f"steps: the analysis stopped early because "
        f"{_STOPPED.get(state.stop_reason, 'it could not continue')}."
    )


def _plain(state: DeepState) -> str:
    """An answer no model wrote: what each step found, in the product's words.

    The road for a policy under which no result may reach a writer, for a run
    with nothing to write from, and for a writer that failed.

    It shows the reader the computed summaries — their own data — **with one
    exception, and it is about the next turn, not this one.** This answer is
    stored as the assistant's message, and under `SAMPLE`/`FULL`
    `disclose_history` replays it to the next question's model verbatim. A
    summary computed from rows the model was *not* given (a `SAMPLE` over
    fifty rows) would reach it that way. So under a wide policy a summary is
    written here only when the model could have seen every row behind it;
    under a narrow one the history filter withholds the whole message, and
    the summary is safe to show.
    """
    wide = state.disclosure_policy in _WIDE
    lines: list[str] = []
    for e in state.evidence:
        head = f"{e.index + 1}. {e.step.question}"
        if e.status != "DONE":
            lines.append(f"{head} — not answered: {e.note or 'no result'}")
            continue
        found = f"{e.row_count} rows"
        if e.computed is not None:
            if not wide or e.computed.get("complete"):
                found += f". {e.computed['summary']}"
            else:
                found += ". Figures were computed from it and are not repeated here."
        lines.append(f"{head} — {found}")
    return "\n".join(lines) or "No step of the analysis produced a result."


async def synthesize(state: DeepState, deps: NodeDeps) -> NodeResult:
    """Write the answer from the evidence collected — however much there is.

    The one terminal node every road reaches: a finished plan, an exhausted
    budget, and *Answer now*. It restores the reader's question and puts the
    last step that ran into `execution`, so `chart` and the TABLE artifact
    describe a real result.

    **The writer reads `narration(e)` and nothing else** — `disclosed`,
    `computed` when the model was given every row behind it, and the shape.
    It is `reports/narrate.py`'s section prompt, with the steps as the
    section's numbered results, so every sentence cites the step it came
    from; `parse_claims` lifts the markers out and `check_claims` checks each
    claim against **its own** step's result (Phase 3's claims, reused).
    Under `NONE`/`AGGREGATE` no writer is called at all: the answer is
    `_plain`, which is what reports would do if they did not refuse outright.
    """
    state.question = state.asked
    done = [e for e in state.evidence if e.status == "DONE"]
    planned = len(state.plan.steps) if state.plan else 0
    state.execution = done[-1].execution if done else None
    preface = _preface(state)
    if preface:
        await deps.emit("TEXT_DELTA", {"text": preface + "\n\n"})

    if not done or state.disclosure_policy not in _WIDE:
        body = _plain(state)
        await deps.emit("TEXT_DELTA", {"text": body})
        state.answer = f"{preface}\n\n{body}".strip()
        why = "nothing to write from" if not done else "policy withholds results"
        return NodeResult(
            detail=f"{len(done)} of {planned} steps · written without a model ({why})"
        )

    blocks = [ev.narration(e) for e in state.evidence]
    intent = (state.plan.restatement if state.plan else "") or state.asked
    if state.plan and state.plan.stop_when:
        intent += f" It is answered when: {state.plan.stop_when}"
    if preface:
        intent += f" {preface} Say what the steps that ran establish, and no more."
    messages = section_messages(
        heading=state.asked, intent=intent, blocks=blocks,
        language=detect(state.asked), request=state.asked,
    )

    buffer: list[str] = []
    thinking = _Thinking(deps.emit)
    try:
        async for chunk in deps.llm_gateway.stream(
            deps.llm, messages, on_usage=state.usage_sink("synthesize")
        ):
            if chunk.reasoning:
                await thinking.add(chunk.reasoning)
                continue
            await thinking.flush()
            buffer.append(chunk.text)
            await deps.emit("TEXT_DELTA", {"text": chunk.text})
    except LLMError as err:
        log.warning("deep_synthesis_failed", error=err.message)
        buffer = []
    await thinking.flush()

    prose = "".join(buffer).strip()
    if not prose:
        # Fail backwards: the evidence is correct and a failed writer may not
        # lose it. `TEXT_RESET` clears whatever reached the bus.
        await deps.emit("TEXT_RESET", {"reason": "stream_failed"})
        body = _plain(state)
        await deps.emit("TEXT_DELTA", {"text": f"{preface}\n\n{body}".strip()})
        state.answer = f"{preface}\n\n{body}".strip()
        return NodeResult(
            detail=f"{len(done)} of {planned} steps · writer failed, plain answer"
        )

    clean, claims = checks.parse_claims(prose, results=len(blocks))
    context = " ".join([state.asked, *(e.step.question for e in state.evidence)])
    check = checks.check_claims(claims, [ev.pool(b) for b in blocks], context=context)
    state.synthesis = check.model_dump()
    state.answer = f"{preface}\n\n{clean}".strip()
    if clean != prose:
        # The markers streamed live; the record is the clean prose. Replaced
        # rather than left, because a reader replaying the run would otherwise
        # see `[2]` where the answer's footnotes are.
        await deps.emit("TEXT_RESET", {"reason": "citations"})
        await deps.emit("TEXT_DELTA", {"text": state.answer})
    traced = check.traceable
    return NodeResult(
        detail=f"{len(done)} of {planned} steps · {len(check.claims)} claims"
        + ("" if traced is None else f" · {traced:.0%} traceable")
        + thinking.note()
    )
