"""Report generation as a compiled graph, with two entry points.

Phase 3 of [docs/langgraph-migration.md](../../../docs/langgraph-migration.md).
The argument for moving this one is the same argument Phase 2 made about the
dashboard draft path, plus a second one the chat pipeline never had:

* **`_generate` and `_retry` were two drivers over one node set.** Both ran
  `assert_wide_enough → outline → execute blocks → write result rows →
  narrate | summarise`, each with its own sequencing and its own progress
  writes. The differences between them are real and documented — a retry's
  `established` is the whole document rather than the prefix, and a retry does
  not rewrite the executive summary — but the duplication around those
  differences was not.
* **A run is minutes long.** A process death loses it. Phase 4 makes this graph
  checkpointable, which is the payoff; this phase is what makes that possible.

**It lives in `app/workers/`, not `app/reports/`.** That package is
self-contained by contract — it may not import the pipeline or infra, which is
what forces the worker to disclose results *before* handing them to
`narrate.py` — and an import-linter contract names it as a source module that
may not see `langgraph`. See `pyproject.toml`.

Three things to know before changing anything here:

**The nodes are thin.** Every one of them delegates to a helper that already
existed in `report.py` — `_outline`, `_execute_blocks`, `_narrate`,
`_summarise`, `_block_result`, `_clear_section`, `_written_sections`. That is
deliberate for the same reason Phase 1 did not touch the ten chat nodes: the
prompts, the disclosure gating and the numeric checks are what the tests in
`tests/integration/test_report_runs.py` pin, and this phase must not move them.
It also keeps `monkeypatch.setattr(worker, "execute_many", …)` working, because
the lookup still happens in `report.py`'s globals.

**Narration is sequential and must stay so.** The loop is a conditional edge
back into `narrate_section`, not a `Send` fan-out. Each iteration passes the
prose written so far forward as `established`, which is what lets section five
contrast with section two instead of restating it. Parallelising it would be
faster and would produce a worse document.

**A node crash is handled at the facade, not in the adapter.** The opposite of
the chat graph, and on purpose: there a crashing node is one failed *step* in a
run that continues to a verdict, here it is the run. `generate_run` and
`retry_section` in `report.py` keep the `try/except` they always had.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.core.clock import utcnow
from app.core.errors import DisclosureTooNarrowError
from app.core.logging import get_logger
from app.domain.value_objects import (
    ReportRunStatus,
    ReportSectionKind,
    ReportSectionResultStatus,
)
from app.infra.db.models import (
    DatabaseConnection,
    Report,
    ReportBlock,
    ReportBlockResult,
    ReportRun,
    ReportSection,
)
from app.reports.narrate import WrittenSection
from app.services.report_service import assert_wide_enough
from app.workers import report

log = get_logger(__name__)

CHECK_DISCLOSURE = "check_disclosure"
RESOLVE_OUTLINE = "resolve_outline"
CLEAR_SECTION = "clear_section"
EXECUTE_BLOCKS = "execute_blocks"
WRITE_RESULTS = "write_results"
NARRATE_SECTION = "narrate_section"
SUMMARISE = "summarise"
FINISH = "finish"

Mode = Literal["generate", "retry", "resume"]

#: Writes `report_runs.progress_current` / `.phase` and commits, so the next
#: poll sees them. This graph's `on_step`: bound to the session and the run by
#: the facade, injected through the config, and the only way a node touches the
#: run row. The *content* stays at the call site because it is content — the
#: phase string names the section being written and the reader reads it.
Progress = Callable[..., Awaitable[None]]

#: Distinguishes "not resolved yet" from "resolved, and there is no model".
_UNSET = object()


@dataclass(slots=True)
class ReportWork:
    """The state, carried whole and mutated in place.

    Same treatment `RunState` gets in the chat graph and for the same reason:
    it is one representation of a generation in the process, with no per-field
    reducer that could disagree with a node about what a field means. What is
    *not* here is anything unserializable — the session, the settings, the
    cancel flag and the progress writer all travel in the config.
    """

    run: ReportRun
    mode: Mode
    #: Retry only: which section is being rewritten.
    section_id: UUID | None = None

    report: Report | None = None
    connection: DatabaseConnection | None = None
    sections: list[ReportSection] = field(default_factory=list)
    blocks: list[tuple[ReportBlock, ReportSection]] = field(default_factory=list)
    positions: dict[UUID, int] = field(default_factory=dict)

    #: What `execute_saved_sql` gave back, keyed by block id.
    results: dict[UUID, Any] = field(default_factory=dict)
    #: Block results by section, for the narrator.
    written: dict[UUID, list[ReportBlockResult]] = field(default_factory=dict)
    #: Retry only: the rows this pass replaced, in order.
    rewritten: list[ReportBlockResult] = field(default_factory=list)
    #: Retry only: the document position the cleared rows sat at.
    start: int = 0

    outcomes: list[bool] = field(default_factory=list)
    #: The paragraphs written so far, in order — the first pass's `established`.
    prose: list[WrittenSection] = field(default_factory=list)
    #: Every non-summary heading, computed once before the loop so three
    #: paragraphs do not each open on the same total.
    headings: list[str] = field(default_factory=list)
    #: The gateway and model this run's prose is written by, resolved once when
    #: the narration phase starts. `_UNSET` rather than `None` because **None
    #: is a real value here**: a report whose provider row was deleted between
    #: queueing and generating still produces its numbers, and every section
    #: says plainly why it has no paragraph.
    narrator: Any = _UNSET

    #: Resume only: sections that already have a paragraph, so the loop walks
    #: past them instead of writing a second one.
    narrated: set[UUID] = field(default_factory=set)

    #: The narration loop's cursor into `sections`.
    cursor: int = 0
    #: The executive summary and where the user put it — usually first, which
    #: is exactly why it cannot simply be appended.
    summary: tuple[int, ReportSection] | None = None
    #: Progress ticks written so far.
    done: int = 0

    #: Set by a node that has decided the run is over. `None` means "derive it",
    #: which is what almost every finished run does.
    status: str | None = None
    error: str | None = None


class GraphState(TypedDict):
    work: ReportWork


NodeFn = Callable[[ReportWork, RunnableConfig], Awaitable[str]]


def _adapt(
    name: str, fn: NodeFn, *, cancellable: bool = True
) -> Callable[..., Awaitable[Command[str]]]:
    """Wrap a node with what the executor owes it: the cancel check.

    Checked **before** the node, and `cancellable` is what puts it in exactly
    the places the two hand-rolled drivers checked it: before any query is
    spent, and between paragraphs.

    **The persistence phases are deliberately not cancellable**, and that is
    the whole substance of "a cancelled run keeps what it already computed".
    `execute_blocks` and `write_results` are one unit: the flag is routinely
    set *while the queries are in flight*, and a check between running them and
    writing them down would throw away results the customer's database has
    already been made to produce — a slower way of doing nothing. `clear_section`
    is excluded for the mirror-image reason: a retry that dropped a section's
    rows and then stopped would leave the document worse than it found it.

    `finish` is not cancellable either, for a duller reason: it is what *writes*
    the cancelled row, and a check in front of it would route it back to itself.
    """

    async def node(state: GraphState, config: RunnableConfig) -> Command[str]:
        work = state["work"]
        if cancellable and config["configurable"]["cancelled"].is_set():
            work.status = ReportRunStatus.CANCELLED
            return Command(goto=FINISH, update={"work": work})
        goto = await fn(work, config)
        return Command(goto=goto, update={"work": work})

    node.__name__ = f"node_{name}"
    return node


def _cfg(config: RunnableConfig) -> dict[str, Any]:
    return config["configurable"]


# ── nodes ────────────────────────────────────────────────────────────────
async def _check_disclosure(work: ReportWork, config: RunnableConfig) -> str:
    """The policy gate, and it is first on **both** entries.

    Not a precondition to hoist into the caller: the gate at creation says what
    was true then, and a policy tightened since has to stop the run from inside
    — including a retry, which is a second generation of one section and gets
    no exemption. CLAUDE.md invariant #4, the "filters at render time" half.
    """
    db = _cfg(config)["db"]
    work.report = await db.get(Report, work.run.report_id)
    work.connection = (
        await db.get(DatabaseConnection, work.report.connection_id)
        if work.report is not None and work.report.connection_id is not None
        else None
    )

    if work.mode == "generate" and work.report is None:
        return _fail(work, "The report was removed.")
    if work.report is None or work.connection is None:
        return _fail(
            work,
            "This report's database connection has been removed, so it "
            "cannot be generated. Past runs stay readable."
            if work.mode == "generate"
            else "This report's database connection has been removed, so its "
            "sections cannot be retried. Past runs stay readable.",
        )

    try:
        assert_wide_enough(work.connection)
    except DisclosureTooNarrowError as err:
        return _fail(work, err.message)
    return RESOLVE_OUTLINE


async def _resolve_outline(work: ReportWork, config: RunnableConfig) -> str:
    """The sections in reading order and the blocks in document order.

    Computed once per entry. `headings` comes off it here rather than inside
    the loop for the same reason: the outline does not change mid-run.
    """
    db, progress = _cfg(config)["db"], _cfg(config)["progress"]
    assert work.report is not None

    work.sections, work.blocks = await report._outline(db, work.report.id)
    work.positions = {s.id: i for i, s in enumerate(work.sections)}
    work.headings = [
        s.heading
        for s in work.sections
        if s.kind != ReportSectionKind.EXECUTIVE_SUMMARY
    ]

    if work.mode == "retry":
        if not any(s.id == work.section_id for s in work.sections):
            # Deleted between the request and the worker picking it up. The run
            # is re-derived from what it still holds rather than left RUNNING.
            return FINISH
        return CLEAR_SECTION

    # The whole outline, before a resume narrows it: the progress bar counts
    # the document, not what is left of it.
    steps = len(work.blocks) + len(work.sections)
    if work.mode == "resume":
        await _seed_from_written(work, config)

    await progress(
        status=ReportRunStatus.RUNNING,
        started_at=work.run.started_at or utcnow(),
        progress_total=steps,
        progress_current=work.done,
        phase=(
            f"Running {len(work.blocks)} "
            f"quer{'y' if len(work.blocks) == 1 else 'ies'}"
        ),
    )
    return EXECUTE_BLOCKS


async def _clear_section(work: ReportWork, config: RunnableConfig) -> str:
    """Retry only: drop this section's rows, and remember where they sat.

    The replacements reuse the position the old rows had, so a retried section
    stays where the reader left it instead of jumping to the end.
    """
    db, progress = _cfg(config)["db"], _cfg(config)["progress"]
    section = _section(work)
    assert work.section_id is not None  # `retry` mode, so it was given one

    work.start = await report._clear_section(db, work.run.id, work.section_id)
    work.blocks = [
        (block, section) for block, sec in work.blocks if sec.id == work.section_id
    ]
    await progress(phase=f"Retrying {section.heading}"[:200])
    return EXECUTE_BLOCKS


async def _execute_blocks(work: ReportWork, config: RunnableConfig) -> str:
    """Every block's statement, through `execute_saved_sql`'s guarded path.

    `report_blocks.sql` is a third entry point to the guard and gets no
    exemption: it is re-validated against the connection's *current* snapshot
    on every execution, and `sql_origin` is provenance only.
    """
    cfg = _cfg(config)
    assert work.connection is not None
    work.results = (
        await report._execute_blocks(
            cfg["db"], cfg["settings"], work.connection, work.blocks, work.run.owner_id
        )
        if work.blocks
        else {}
    )
    return WRITE_RESULTS


async def _write_results(work: ReportWork, config: RunnableConfig) -> str:
    """One row per block, committed as it lands.

    This is where the progressive render comes from: the poll response *is* a
    snapshot of what has been written, so a result held back until the end
    would be a result the reader never watches arrive.
    """
    db, progress = _cfg(config)["db"], _cfg(config)["progress"]
    total = len(work.blocks)

    for offset, (block, section) in enumerate(work.blocks):
        result = work.results.get(block.id) or report._no_sql_result()
        position = work.start + offset if work.mode == "retry" else offset
        row = report._block_result(
            work.run, block, section.heading, result, position
        )
        db.add(row)

        if work.mode == "retry":
            work.rewritten.append(row)
            await db.commit()
            continue

        work.written.setdefault(section.id, []).append(row)
        work.outcomes.append(result.status == "OK")
        work.done += 1
        await progress(
            progress_current=work.done,
            phase=f"Wrote result {offset + 1} of {total}",
        )

    if work.mode == "retry":
        section = _section(work)
        return (
            SUMMARISE
            if section.kind == ReportSectionKind.EXECUTIVE_SUMMARY
            else NARRATE_SECTION
        )
    return NARRATE_SECTION


async def _narrate_section(work: ReportWork, config: RunnableConfig) -> str:
    """One section's paragraph. The loop edge is the last line of this function.

    **Sequential on purpose.** `established` is the prose written so far, and it
    is what stops section five restating section two. A `Send` fan-out would
    hand every section an empty document and produce a worse one faster.
    """
    cfg = _cfg(config)
    db, progress = cfg["db"], cfg["progress"]
    assert work.report is not None and work.connection is not None
    await _ensure_narrator(work, config)

    if work.mode == "retry":
        section = _section(work)
        row = await report._narrate(
            cfg["settings"],
            run=work.run,
            report=work.report,
            section=section,
            position=work.positions[section.id],
            results=work.rewritten,
            policy=work.connection.disclosure_policy,
            narrator=work.narrator,
            other_headings=[
                s.heading
                for s in work.sections
                if s.id != work.section_id
                and s.kind != ReportSectionKind.EXECUTIVE_SUMMARY
            ],
            # A retried section is rewritten *into* a document that already
            # exists, so it reads the paragraphs around it — including the ones
            # written after it, which the first pass could not see. The summary
            # is left out, or the section would write around the very thing it
            # exists to say.
            established=await report._written_sections(
                db,
                work.run.id,
                work.section_id,
                *(
                    s.id
                    for s in work.sections
                    if s.kind == ReportSectionKind.EXECUTIVE_SUMMARY
                ),
            ),
        )
        db.add(row)
        await db.commit()
        return FINISH

    # ── the first pass: walk the sections in order ───────────────────────
    while work.cursor < len(work.sections):
        position = work.cursor
        section = work.sections[position]
        work.cursor += 1
        if section.kind == ReportSectionKind.EXECUTIVE_SUMMARY:
            # Written last, from the sections it summarises. Its *position* is
            # wherever the user put it — usually first, which is the point, and
            # why it is remembered rather than appended.
            work.summary = (position, section)
            continue

        if section.id in work.narrated:
            # Resume: this paragraph survived the crash that stopped the run.
            # Rewriting it would spend a model call to replace prose the reader
            # may already have seen — and would overwrite an edit.
            continue

        work.done += 1
        await progress(
            progress_current=work.done, phase=f"Writing {section.heading}"[:200]
        )
        row = await report._narrate(
            cfg["settings"],
            run=work.run,
            report=work.report,
            section=section,
            position=position,
            results=work.written.get(section.id, []),
            policy=work.connection.disclosure_policy,
            narrator=work.narrator,
            other_headings=[h for h in work.headings if h != section.heading],
            established=list(work.prose),
        )
        db.add(row)
        await db.commit()
        work.outcomes.append(row.status != ReportSectionResultStatus.FAILED)
        if row.prose:
            work.prose.append(
                WrittenSection(heading=row.heading_snapshot, prose=row.prose)
            )
        # Back into this node while sections remain — which is also where the
        # cancel check lands, between sections, exactly as before.
        return NARRATE_SECTION

    return SUMMARISE


async def _summarise(work: ReportWork, config: RunnableConfig) -> str:
    """The executive summary, written after everything it summarises."""
    cfg = _cfg(config)
    db, progress = cfg["db"], cfg["progress"]
    assert work.report is not None
    await _ensure_narrator(work, config)

    if work.mode == "retry":
        section = _section(work)
        row = await report._summarise(
            cfg["settings"],
            run=work.run,
            report=work.report,
            section=section,
            position=work.positions[section.id],
            written=await report._written_sections(db, work.run.id, work.section_id),
            narrator=work.narrator,
        )
        db.add(row)
        await db.commit()
        return FINISH

    if work.summary is None or work.summary[1].id in work.narrated:
        return FINISH

    position, section = work.summary
    work.done += 1
    await progress(progress_current=work.done, phase="Writing the summary")
    row = await report._summarise(
        cfg["settings"],
        run=work.run,
        report=work.report,
        section=section,
        position=position,
        written=work.prose,
        narrator=work.narrator,
    )
    db.add(row)
    await db.commit()
    work.outcomes.append(row.status != ReportSectionResultStatus.FAILED)
    return FINISH


async def _finish_run(work: ReportWork, config: RunnableConfig) -> str:
    """The one place a run's terminal row is written.

    **The status is derived, never set** — that is what lets a successful retry
    turn `PARTIAL` into `SUCCEEDED` with no state machine, and it is why
    `PARTIAL` can exist at all. The two exceptions are the two things a
    derivation cannot express: a run that failed before it had parts, and a run
    the user cancelled.
    """
    db = _cfg(config)["db"]
    if work.status is not None:
        await report._finish(db, work.run, work.status, error=work.error)
    elif work.mode in ("retry", "resume"):
        # Both write only part of the document, so neither can read its status
        # off what *this* pass produced. `_rederive` reads every row the run
        # now holds — which is the same reason a successful retry turns
        # `PARTIAL` into `SUCCEEDED` with no state machine.
        await report._rederive(db, work.run)
    else:
        await report._finish(db, work.run, report.derive_status(work.outcomes))
    return END


# ── helpers ──────────────────────────────────────────────────────────────
def _fail(work: ReportWork, message: str) -> str:
    work.status = ReportRunStatus.FAILED
    work.error = message
    return FINISH


def _section(work: ReportWork) -> ReportSection:
    """The section a retry is rewriting. Present by the time anything asks."""
    section = next(s for s in work.sections if s.id == work.section_id)
    return section


async def _seed_from_written(work: ReportWork, config: RunnableConfig) -> None:
    """Pick the run back up from the rows it already wrote.

    **This is the resume mechanism, and there is no checkpoint behind it.** The
    document *is* the progress: `report_block_results` and
    `report_section_results` say exactly which blocks ran and which sections
    were narrated, in order, durably, in the same transactions that produced
    them. A checkpointer would be a second and less reliable copy of that —
    less reliable because it would be written in a *different* transaction, so
    a crash in the window between committing a section and committing its
    checkpoint would resume onto a node that had already written its row and
    duplicate it. Reading the rows cannot have that bug: the row's existence is
    the fact being recorded.

    So this narrows the work to what is missing and seeds what the narrator
    needs to carry on: the block results it will read, and the prose already
    written, which the next section receives as `established`.
    """
    db = _cfg(config)["db"]
    block_rows = await report._block_rows(db, work.run.id)
    section_rows = await report._section_rows(db, work.run.id)

    # `section_id` is nullable on both result tables: deleting a section sets
    # it NULL rather than cascading, so a past document stays readable. Such a
    # row belongs to no section in this outline, so it can neither supply data
    # to a narrator nor mark anything as already written — but it still counts
    # as work that was done.
    done_blocks = {row.block_id for row in block_rows}
    for row in block_rows:
        if row.section_id is not None:
            work.written.setdefault(row.section_id, []).append(row)
    for rows in work.written.values():
        rows.sort(key=lambda r: r.position)

    work.narrated = {
        row.section_id for row in section_rows if row.section_id is not None
    }
    work.blocks = [
        (block, section)
        for block, section in work.blocks
        if block.id not in done_blocks
    ]
    work.done = len(block_rows) + len(section_rows)
    # The sections written before the crash, in reading order — what the next
    # one is told was already established. The summary is not among them: it is
    # written last, so on a resume it either does not exist yet or the run had
    # already finished.
    work.prose = await report._written_sections(
        db,
        work.run.id,
        *(
            s.id
            for s in work.sections
            if s.kind == ReportSectionKind.EXECUTIVE_SUMMARY
        ),
    )


async def _ensure_narrator(work: ReportWork, config: RunnableConfig) -> None:
    """Resolve the model at the start of the narration phase, once.

    Here rather than at the entry because that is where both hand-rolled
    drivers did it — after the block results are written and after the cancel
    check, so a run cancelled before any prose never resolves a provider at
    all.
    """
    if work.narrator is _UNSET:
        cfg = _cfg(config)
        work.narrator = await report._narrator(cfg["db"], cfg["settings"], work.run)


# ── the graph ────────────────────────────────────────────────────────────
def _build() -> Any:
    graph: Any = StateGraph(GraphState)
    graph.add_node(
        CHECK_DISCLOSURE,
        _adapt(CHECK_DISCLOSURE, _check_disclosure),
        destinations=(RESOLVE_OUTLINE, FINISH),
    )
    graph.add_node(
        RESOLVE_OUTLINE,
        _adapt(RESOLVE_OUTLINE, _resolve_outline),
        destinations=(EXECUTE_BLOCKS, CLEAR_SECTION, FINISH),
    )
    graph.add_node(
        CLEAR_SECTION,
        _adapt(CLEAR_SECTION, _clear_section, cancellable=False),
        destinations=(EXECUTE_BLOCKS, FINISH),
    )
    graph.add_node(
        EXECUTE_BLOCKS,
        _adapt(EXECUTE_BLOCKS, _execute_blocks, cancellable=False),
        destinations=(WRITE_RESULTS, FINISH),
    )
    graph.add_node(
        WRITE_RESULTS,
        _adapt(WRITE_RESULTS, _write_results, cancellable=False),
        destinations=(NARRATE_SECTION, SUMMARISE, FINISH),
    )
    graph.add_node(
        NARRATE_SECTION,
        _adapt(NARRATE_SECTION, _narrate_section),
        # Back into itself while sections remain. Sequential, not `Send`.
        destinations=(NARRATE_SECTION, SUMMARISE, FINISH),
    )
    graph.add_node(
        SUMMARISE,
        _adapt(SUMMARISE, _summarise),
        destinations=(FINISH,),
    )
    graph.add_node(
        FINISH,
        _adapt(FINISH, _finish_run, cancellable=False),
        destinations=(END,),
    )
    graph.add_edge(START, CHECK_DISCLOSURE)
    return graph


# Compiled once, at import. Both entries share it — that is the whole point of
# the phase, and `retry_section` being a second *entry* rather than a direct
# call on one node is what collapses the two drivers into one.
REPORT_GRAPH = _build().compile(name="report")

#: A report has as many supersteps as it has sections plus a fixed preamble, so
#: the ceiling has to scale with the outline rather than sit at the chat graph's
#: 25. `section_target` is capped at 8 and the summary rides on top; this is
#: that, with room for the preamble and a wide margin.
RECURSION_LIMIT = 256


# ── the two entries ──────────────────────────────────────────────────────
async def _invoke(work: ReportWork, config: dict[str, Any]) -> None:
    await REPORT_GRAPH.ainvoke(
        {"work": work},
        config={"configurable": config, "recursion_limit": RECURSION_LIMIT},
    )


def _configurable(
    db: Any, settings: Any, run: ReportRun, cancelled: Any
) -> dict[str, Any]:
    async def progress(**fields: Any) -> None:
        await report._touch(db, run, **fields)

    return {
        "db": db,
        "settings": settings,
        "cancelled": cancelled,
        "progress": progress,
    }


async def run_generation(
    db: Any, settings: Any, run: ReportRun, cancelled: Any
) -> None:
    """A whole report: every block, then the document over the results."""
    await _invoke(
        ReportWork(run=run, mode="generate"),
        _configurable(db, settings, run, cancelled),
    )


async def run_resume(
    db: Any, settings: Any, run: ReportRun, cancelled: Any
) -> None:
    """Pick up a run a dead process left half-written. **No checkpoint.**

    The same graph again, entered a third way. It re-checks disclosure (a
    policy may have been tightened while the process was down), re-reads the
    outline (the report may have been edited), and then runs only what is
    missing — because the rows already written say exactly what that is.

    Safe to call twice: everything it skips, it skips because the row exists,
    so a resume that itself crashes simply resumes again.
    """
    await _invoke(
        ReportWork(run=run, mode="resume"),
        _configurable(db, settings, run, cancelled),
    )


async def run_retry(
    db: Any, settings: Any, run: ReportRun, section_id: UUID, cancelled: Any
) -> None:
    """One section of a finished run: its queries, then its paragraph.

    The **same graph**, entered with a different mode. The rest of the document
    stays untouched — including the executive summary, which is not rewritten,
    because it is a paragraph the user may have edited and replacing it because
    a section below it was retried would destroy writing.
    """
    await _invoke(
        ReportWork(run=run, mode="retry", section_id=section_id),
        _configurable(db, settings, run, cancelled),
    )
