"""In-process executor for report generation, and the body of a run.

The same trade `SemanticJobExecutor` makes, for the same reason: a generation is
minutes of database and provider latency rather than seconds, so it gets a low
concurrency ceiling and no heartbeat. Durability comes from the `report_runs`
row, and `sweep_orphans` at startup turns a run stranded by a dead process into
a FAILED one — honest, because the results that *did* land are still there to
read and the user can simply generate again.

**It mirrors `workers/semantic.py`; it does not share it.** The two jobs have the
same shape and different bodies, and a shared executor would be one class with
two `if` branches inside every method.

Three things here are load-bearing:

* **Disclosure is re-checked at the start of every run.** CLAUDE.md invariant #4
  says the policy filters at render time, never only at write time. A report
  created against a `SAMPLE` connection whose policy is later tightened must
  fail its run out loud rather than quietly produce paragraphs written from
  nothing.
* **Every result row is written the moment it exists**, each in its own commit.
  The poll response is then a snapshot of what has landed so far, which is the
  entire progressive-rendering design — no special protocol, and a browser that
  reloads mid-run resumes exactly where it was.
* **The run's status is derived from its parts, never set.** That is what lets a
  per-section retry turn a `PARTIAL` run into a `SUCCEEDED` one with no state
  machine, and it is why `PARTIAL` can exist at all.

Cancellation is cooperative *and* hard, as it is for a semantic job: the flag is
checked between phases so an in-flight query is allowed to finish rather than
being abandoned, and the task is cancelled outright if it does not stop.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import DisclosureTooNarrowError
from app.core.logging import get_logger
from app.domain.ports.database import ResultColumn
from app.domain.ports.llm import LLMGateway, ResolvedLLM
from app.domain.value_objects import (
    ReportBlockResultStatus,
    ReportBlockType,
    ReportRunStatus,
    ReportSectionKind,
    ReportSectionResultStatus,
)
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    Report,
    ReportBlock,
    ReportBlockResult,
    ReportRun,
    ReportSection,
    ReportSectionResult,
)
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.pipeline.disclosure import disclose
from app.pipeline.state import ExecutionResult
from app.reports import checks, narrate
from app.reports.narrate import BlockNarration, WrittenSection
from app.reports.prompts import no_data_sentence
from app.services.query_service import (
    TileRequest,
    TileResult,
    execute_many,
    resolve_llm,
    secret_box,
)
from app.services.report_service import assert_wide_enough

log = get_logger(__name__)

# A generation already runs its blocks concurrently against the customer's
# database; two whole reports at once is a load test of it, not a speed-up.
MAX_CONCURRENT_JOBS = 2

#: Output-token floor for a prose call, whatever the provider row says. Four
#: sentences is nothing; the budget is for the scratchpad a reasoning model
#: spends before emitting them, and an overrun truncates the paragraph
#: mid-word.
NARRATE_MIN_MAX_TOKENS = 2_048

_NO_SQL = (
    "This block has not been checked, so it has no query to run. Check it in "
    "the outline, then generate again."
)

_NO_MODEL = (
    "This report's model configuration has been removed, so its prose could "
    "not be written. Choose a model for the report and generate again."
)

#: What the executor runs: a whole generation, or one section of one.
_Body = Callable[[AsyncSession, Settings, UUID, asyncio.Event], Awaitable[None]]


class ReportRunExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._flags: dict[UUID, asyncio.Event] = {}

    async def submit(self, run_id: UUID) -> None:
        self._schedule(run_id, generate_run, f"report:{run_id}")

    async def submit_retry(self, run_id: UUID, section_id: UUID) -> None:
        """Re-run one section of a finished run, through the same machinery.

        Not a route that blocks for half a minute: the viewer is already
        polling this run, so a retry that lands on the same row renders through
        the loop the page is already running.
        """

        async def retry(
            db: AsyncSession, settings: Settings, rid: UUID, flag: asyncio.Event
        ) -> None:
            await retry_section(db, settings, rid, section_id, flag)

        self._schedule(run_id, retry, f"report-retry:{run_id}")

    def _schedule(self, run_id: UUID, body: _Body, name: str) -> None:
        cancelled = asyncio.Event()
        self._flags[run_id] = cancelled
        task = asyncio.create_task(self._run(run_id, cancelled, body), name=name)
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._forget(run_id))

    async def cancel(self, run_id: UUID) -> bool:
        """Ask first, then insist.

        Setting the flag lets the run stop between phases and keep the results
        it has already paid for; the hard cancel a moment later covers a run
        stuck in a query or a provider call that will never return.
        """
        flag = self._flags.get(run_id)
        task = self._tasks.get(run_id)
        if flag is None or task is None or task.done():
            return False
        flag.set()

        async def insist() -> None:
            await asyncio.sleep(self._settings.llm_request_timeout_seconds + 5)
            if not task.done():
                task.cancel()

        asyncio.create_task(insist())  # noqa: RUF006 (fire-and-forget by design)
        return True

    def _forget(self, run_id: UUID) -> None:
        self._tasks.pop(run_id, None)
        self._flags.pop(run_id, None)

    async def _run(
        self, run_id: UUID, cancelled: asyncio.Event, body: _Body
    ) -> None:
        from app.infra.db.session import get_sessionmaker

        async with self._semaphore:
            try:
                async with get_sessionmaker()() as session:
                    await body(session, self._settings, run_id, cancelled)
            except asyncio.CancelledError:
                log.info("report_run_cancelled", run_id=str(run_id))
                raise
            except Exception:
                log.exception("report_executor_failed", run_id=str(run_id))


async def sweep_orphans() -> int:
    """Fail runs left QUEUED or RUNNING by a process that died. Returns how many.

    Unlike a semantic job, a stranded run may have written real results before
    the process went, and those rows stay: a document that lost half its
    sections to a restart is still worth reading, and the message says the rest
    is one more generation away.
    """
    from app.infra.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(ReportRun).where(
                ReportRun.status.in_(
                    (ReportRunStatus.QUEUED, ReportRunStatus.RUNNING)
                )
            )
        )
        rows = list(result.scalars())
        for row in rows:
            row.status = ReportRunStatus.FAILED
            row.finished_at = utcnow()
            row.error_message = (
                "The server restarted while this report was being generated. "
                "Whatever had already been computed was kept — generate again "
                "for the rest."
            )
        await session.commit()
        return len(rows)


def derive_status(succeeded: list[bool]) -> str:
    """The run's status, read off its parts.

    `PARTIAL` exists because nothing else in this codebase generates
    independently-failable pieces: a run of seven sections where one failed is
    neither a success nor a failure, and calling it either would be a lie the
    user has to open the document to catch.
    """
    if not succeeded:
        return ReportRunStatus.FAILED
    if all(succeeded):
        return ReportRunStatus.SUCCEEDED
    if any(succeeded):
        return ReportRunStatus.PARTIAL
    return ReportRunStatus.FAILED


async def generate_run(
    db: AsyncSession, settings: Settings, run_id: UUID, cancelled: asyncio.Event
) -> None:
    """Execute every block of a run, then write the document over the results.

    The order of work is §8's:

        1. re-check disclosure
        2. execute every block            -> one connector, results as they land
        3. per section, in order          -> narrate, check the figures, write
        4. the executive summary, last    -> from the finished sections' prose
        5. derive the run's status from its parts

    Step 3 is where the progressive render the reader watches comes from: a
    paragraph is committed the moment it is written, so the next poll has one
    more section of the document than the last one did.

    This owns no long transaction. It commits after each write, because a
    poller can only see committed state and progress that rode along on one
    transaction would appear all at once at the end, which is the opposite of
    the point.
    """
    run = await db.get(ReportRun, run_id)
    if run is None:
        return

    try:
        await _generate(db, settings, run, cancelled)
    except asyncio.CancelledError:
        await _finish(db, run, ReportRunStatus.CANCELLED)
        raise
    except Exception as err:  # a broken run is a failed run, never a bare 500
        log.exception("report_run_failed", run_id=str(run_id))
        await _finish(db, run, ReportRunStatus.FAILED, error=str(err)[:500])


async def _generate(
    db: AsyncSession, settings: Settings, run: ReportRun, cancelled: asyncio.Event
) -> None:
    report = await db.get(Report, run.report_id)
    if report is None:
        await _finish(db, run, ReportRunStatus.FAILED, error="The report was removed.")
        return

    connection = (
        await db.get(DatabaseConnection, report.connection_id)
        if report.connection_id is not None
        else None
    )
    if connection is None:
        await _finish(
            db,
            run,
            ReportRunStatus.FAILED,
            error=(
                "This report's database connection has been removed, so it "
                "cannot be generated. Past runs stay readable."
            ),
        )
        return

    try:
        # §7, and the half of it that is easy to forget: the gate at creation
        # says what was true then. A policy tightened since must stop this run
        # rather than let it write paragraphs from values the model never saw.
        assert_wide_enough(connection)
    except DisclosureTooNarrowError as err:
        await _finish(db, run, ReportRunStatus.FAILED, error=err.message)
        return

    sections, blocks = await _outline(db, report.id)
    if cancelled.is_set():
        await _finish(db, run, ReportRunStatus.CANCELLED)
        return

    steps = len(blocks) + len(sections)
    await _touch(
        db,
        run,
        status=ReportRunStatus.RUNNING,
        started_at=utcnow(),
        progress_total=steps,
        progress_current=0,
        phase=f"Running {len(blocks)} quer{'y' if len(blocks) == 1 else 'ies'}",
    )

    results = await _execute_blocks(db, settings, connection, blocks, run.owner_id)

    outcomes: list[bool] = []
    done = 0
    written: dict[UUID, list[ReportBlockResult]] = {}
    for position, (block, section) in enumerate(blocks):
        result = results.get(block.id) or _no_sql_result()
        row = _block_result(run, block, section.heading, result, position)
        db.add(row)
        written.setdefault(section.id, []).append(row)
        outcomes.append(result.status == "OK")
        done += 1
        await _touch(
            db,
            run,
            progress_current=done,
            phase=f"Wrote result {position + 1} of {len(blocks)}",
        )

    if cancelled.is_set():
        # The results that landed are kept: they were paid for, and a cancelled
        # run that threw them away would be a slower way of doing nothing.
        await _finish(db, run, ReportRunStatus.CANCELLED)
        return

    # ── the document ─────────────────────────────────────────────────────
    narrator = await _narrator(db, settings, run)
    prose: list[WrittenSection] = []
    summary: tuple[int, ReportSection] | None = None

    for position, section in enumerate(sections):
        if section.kind == ReportSectionKind.EXECUTIVE_SUMMARY:
            # Written last, from the sections it summarises. Its *position* is
            # wherever the user put it — usually first, which is the point.
            summary = (position, section)
            continue

        done += 1
        await _touch(
            db,
            run,
            progress_current=done,
            phase=f"Writing {section.heading}"[:200],
        )
        row = await _narrate(
            settings,
            run=run,
            report=report,
            section=section,
            position=position,
            results=written.get(section.id, []),
            policy=connection.disclosure_policy,
            narrator=narrator,
        )
        db.add(row)
        await db.commit()
        outcomes.append(row.status != ReportSectionResultStatus.FAILED)
        if row.prose:
            prose.append(WrittenSection(heading=row.heading_snapshot, prose=row.prose))

        if cancelled.is_set():
            await _finish(db, run, ReportRunStatus.CANCELLED)
            return

    if summary is not None:
        position, section = summary
        done += 1
        await _touch(db, run, progress_current=done, phase="Writing the summary")
        row = await _summarise(
            settings,
            run=run,
            report=report,
            section=section,
            position=position,
            written=prose,
            narrator=narrator,
        )
        db.add(row)
        await db.commit()
        outcomes.append(row.status != ReportSectionResultStatus.FAILED)

    await _finish(db, run, derive_status(outcomes))


async def retry_section(
    db: AsyncSession,
    settings: Settings,
    run_id: UUID,
    section_id: UUID,
    cancelled: asyncio.Event,
) -> None:
    """Re-run one section of a finished run: its queries, then its paragraph.

    The rest of the document stays on screen and untouched — including the
    executive summary, which is **not** rewritten. That is deliberate: the
    summary is a paragraph the user may have edited, and silently replacing it
    because a section below it was retried would destroy writing. A summary
    that should reflect the retry is one regeneration away, and the summary
    section can itself be retried on its own.

    Because the run's status is *derived*, nothing here transitions it: the
    rows are replaced and the status is read off whatever the run now holds, so
    a successful retry turns `PARTIAL` into `SUCCEEDED` with no state machine.
    """
    run = await db.get(ReportRun, run_id)
    if run is None:
        return

    try:
        await _retry(db, settings, run, section_id, cancelled)
    except asyncio.CancelledError:
        await _finish(db, run, ReportRunStatus.CANCELLED)
        raise
    except Exception as err:  # noqa: BLE001 — a retry may not crash the run
        log.exception(
            "report_retry_failed", run_id=str(run_id), section_id=str(section_id)
        )
        await _finish(db, run, ReportRunStatus.FAILED, error=str(err)[:500])


async def _retry(
    db: AsyncSession,
    settings: Settings,
    run: ReportRun,
    section_id: UUID,
    cancelled: asyncio.Event,
) -> None:
    report = await db.get(Report, run.report_id)
    connection = (
        await db.get(DatabaseConnection, report.connection_id)
        if report is not None and report.connection_id is not None
        else None
    )
    if report is None or connection is None:
        await _finish(
            db,
            run,
            ReportRunStatus.FAILED,
            error=(
                "This report's database connection has been removed, so its "
                "sections cannot be retried. Past runs stay readable."
            ),
        )
        return

    try:
        # Every entry into a generation re-checks it, not just the first.
        assert_wide_enough(connection)
    except DisclosureTooNarrowError as err:
        await _finish(db, run, ReportRunStatus.FAILED, error=err.message)
        return

    sections, blocks = await _outline(db, report.id)
    positions = {section.id: index for index, section in enumerate(sections)}
    section = next((s for s in sections if s.id == section_id), None)
    if section is None:
        # Deleted between the request and the worker picking it up. The run is
        # re-derived from what it still holds rather than left RUNNING.
        await _rederive(db, run)
        return

    start = await _clear_section(db, run.id, section_id)
    mine = [(block, section) for block, sec in blocks if sec.id == section_id]

    await _touch(db, run, phase=f"Retrying {section.heading}"[:200])
    results = (
        await _execute_blocks(db, settings, connection, mine, run.owner_id)
        if mine
        else {}
    )

    rewritten: list[ReportBlockResult] = []
    for offset, (block, _section) in enumerate(mine):
        result = results.get(block.id) or _no_sql_result()
        row = _block_result(run, block, section.heading, result, start + offset)
        db.add(row)
        rewritten.append(row)
        await db.commit()

    if cancelled.is_set():
        await _finish(db, run, ReportRunStatus.CANCELLED)
        return

    narrator = await _narrator(db, settings, run)
    if section.kind == ReportSectionKind.EXECUTIVE_SUMMARY:
        row = await _summarise(
            settings,
            run=run,
            report=report,
            section=section,
            position=positions[section.id],
            written=await _written_sections(db, run.id, section_id),
            narrator=narrator,
        )
    else:
        row = await _narrate(
            settings,
            run=run,
            report=report,
            section=section,
            position=positions[section.id],
            results=rewritten,
            policy=connection.disclosure_policy,
            narrator=narrator,
        )
    db.add(row)
    await db.commit()
    await _rederive(db, run)


async def _clear_section(db: AsyncSession, run_id: UUID, section_id: UUID) -> int:
    """Drop this section's rows, and say where they sat.

    The replacements reuse the position the old rows had, so a retried section
    stays where the reader left it instead of jumping to the end of the
    document.
    """
    rows = list(
        (
            await db.execute(
                select(ReportBlockResult).where(ReportBlockResult.run_id == run_id)
            )
        ).scalars()
    )
    mine = [row for row in rows if row.section_id == section_id]
    for row in mine:
        await db.delete(row)

    for prose in (await _section_rows(db, run_id)):
        if prose.section_id == section_id:
            await db.delete(prose)
    await db.flush()

    if mine:
        return min(row.position for row in mine)
    return max((row.position for row in rows), default=-1) + 1


async def _written_sections(
    db: AsyncSession, run_id: UUID, exclude: UUID
) -> list[WrittenSection]:
    """The run's finished paragraphs, as the summary reads them.

    `edited_prose` wins where the user wrote one: a summary written over the
    model's draft of a paragraph the user has since rewritten would summarise a
    document nobody is looking at.
    """
    return [
        WrittenSection(
            heading=row.heading_snapshot,
            prose=(row.edited_prose if row.edited_prose is not None else row.prose),
        )
        for row in sorted(await _section_rows(db, run_id), key=lambda r: r.position)
        if row.section_id != exclude
        and row.status != ReportSectionResultStatus.FAILED
        and (row.edited_prose or row.prose)
    ]


async def _rederive(db: AsyncSession, run: ReportRun) -> None:
    """Read the run's status off every row it now holds."""
    blocks = list(
        (
            await db.execute(
                select(ReportBlockResult).where(ReportBlockResult.run_id == run.id)
            )
        ).scalars()
    )
    outcomes = [row.status == ReportBlockResultStatus.OK for row in blocks]
    outcomes += [
        row.status != ReportSectionResultStatus.FAILED
        for row in await _section_rows(db, run.id)
    ]
    await _finish(db, run, derive_status(outcomes))


async def _section_rows(db: AsyncSession, run_id: UUID) -> list[ReportSectionResult]:
    return list(
        (
            await db.execute(
                select(ReportSectionResult).where(ReportSectionResult.run_id == run_id)
            )
        ).scalars()
    )


# ── execution ────────────────────────────────────────────────────────────
async def _outline(
    db: AsyncSession, report_id: UUID
) -> tuple[list[ReportSection], list[tuple[ReportBlock, ReportSection]]]:
    """The report's sections in reading order, and its blocks in document order.

    The section travels with the block because its heading is copied onto the
    result row: a run has to stay readable after the section it came from is
    renamed or deleted, and a historical document that silently loses a heading
    is not a historical document.
    """
    sections = list(
        (
            await db.execute(
                select(ReportSection)
                .where(ReportSection.report_id == report_id)
                .order_by(ReportSection.position, ReportSection.created_at)
            )
        ).scalars()
    )
    if not sections:
        return [], []

    order = {section.id: (index, section) for index, section in enumerate(sections)}
    blocks = list(
        (
            await db.execute(
                select(ReportBlock)
                .where(ReportBlock.section_id.in_(list(order)))
                .order_by(ReportBlock.position, ReportBlock.created_at)
            )
        ).scalars()
    )
    blocks.sort(key=lambda b: (order[b.section_id][0], b.position))
    return sections, [(block, order[block.section_id][1]) for block in blocks]


async def _execute_blocks(
    db: AsyncSession,
    settings: Settings,
    connection: DatabaseConnection,
    blocks: list[tuple[ReportBlock, Any]],
    owner_id: UUID,
) -> dict[UUID, TileResult]:
    """Run the blocks that have a statement, through the guarded path.

    `execute_saved_sql` re-validates every statement against the connection's
    *current* snapshot, so `report_blocks.sql` gets no privileged path — a third
    entry point to the guard, and no exemption for any of them. `sql_origin`
    grants nothing.
    """
    requests = [
        TileRequest(
            tile_id=block.id,
            sql=block.sql,
            connection=connection,
            chart_intent=_chart_intent(block),
            want_kpi=block.block_type == ReportBlockType.METRIC,
            max_rows=block.max_rows,
        )
        for block, _section in blocks
        if block.sql.strip()
    ]
    if not requests:
        return {}
    return await execute_many(db, settings, requests=requests, owner_id=owner_id)


def _chart_intent(block: ReportBlock) -> Any:
    """A stored `ChartIntent`, or None for Auto.

    NULL is the common case and the right default for a report: a run months
    from now may see a differently-shaped result, and Auto lets `plan_chart`
    re-decide instead of insisting on a picture the data no longer supports.
    A malformed stored intent is treated as Auto for the same reason a tile
    does — the numbers are correct whatever is wrong with the picture.
    """
    if not block.chart_config:
        return None

    from app.charts import ChartIntent

    try:
        return ChartIntent.model_validate(block.chart_config)
    except Exception:  # noqa: BLE001
        log.warning("report_block_chart_config_unreadable", block_id=str(block.id))
        return None


def _no_sql_result() -> TileResult:
    return TileResult(status="ERROR", error_code="E_SQL_MISSING", error_message=_NO_SQL)


def _block_result(
    run: ReportRun,
    block: ReportBlock,
    heading: str,
    result: TileResult,
    position: int,
) -> ReportBlockResult:
    """One block's numbers, snapshotted at the moment they were computed.

    The heading, the question and the statement are copied rather than
    referenced — `block_id` is SET NULL precisely so this row survives the
    block being deleted.
    """
    return ReportBlockResult(
        id=uuid.uuid4(),
        run_id=run.id,
        block_id=block.id,
        section_id=block.section_id,
        position=position,
        heading_snapshot=heading[:300],
        question_snapshot=block.question,
        sql_text=block.sql,
        sql_hash=block.sql_hash,
        # `ResultColumn` is a slots dataclass with no `__dict__`, so `asdict` is
        # what actually serialises it.
        columns=[asdict(column) for column in result.columns],
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        vega_spec=result.vega_spec,
        chart_source=result.chart_source,
        chart_note=result.chart_note,
        kpi=result.kpi,
        computed_at=result.computed_at,
        duration_ms=result.duration_ms,
        status=(
            ReportBlockResultStatus.OK
            if result.status == "OK"
            else ReportBlockResultStatus.FAILED
        ),
        error_code=result.error_code,
        error_message=result.error_message,
    )


# ── the prose ────────────────────────────────────────────────────────────
async def _narrator(
    db: AsyncSession, settings: Settings, run: ReportRun
) -> tuple[LLMGateway, ResolvedLLM] | None:
    """The gateway and the model this run's prose is written by, or nothing.

    Nothing is a real outcome rather than a crash: the model configuration is
    `SET NULL` on delete, so a report whose provider row was removed between
    queueing and generating still produces its numbers, and every section says
    plainly why it has no paragraph.
    """
    config = (
        await db.get(LlmConfig, run.llm_config_id)
        if run.llm_config_id is not None
        else None
    )
    if config is None:
        return None
    return (
        LiteLLMGateway.from_settings(settings),
        resolve_llm(
            config, secret_box(settings), min_max_tokens=NARRATE_MIN_MAX_TOKENS
        ),
    )


async def _narrate(
    settings: Settings,
    *,
    run: ReportRun,
    report: Report,
    section: ReportSection,
    position: int,
    results: list[ReportBlockResult],
    policy: str,
    narrator: tuple[LLMGateway, ResolvedLLM] | None,
) -> ReportSectionResult:
    """One section's paragraph, written over its blocks' results.

    Three outcomes before a token is spent, and the first two are why:

    * **every block empty** — `SKIPPED_NO_DATA`, with the one sentence that
      says so. A report that says "no returns were recorded in this period" is
      correct; one that hallucinates returns is not, and the surest way to
      avoid the second is not to ask.
    * **nothing produced rows and something broke** — `FAILED`, carrying the
      first error. A paragraph written over three failures would be fiction.
    * otherwise, one model call, then the numeric check over what came back.
    """
    row = ReportSectionResult(
        id=uuid.uuid4(),
        run_id=run.id,
        section_id=section.id,
        position=position,
        heading_snapshot=section.heading[:300],
        prose="",
        status=ReportSectionResultStatus.OK,
    )

    blocks = [_narration(result, policy) for result in results]
    if narrate.has_no_data(blocks) or not blocks:
        row.prose = no_data_sentence(run.language)
        row.status = ReportSectionResultStatus.SKIPPED_NO_DATA
        return row

    if narrate.has_nothing_to_say(blocks):
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = next(
            (b.error for b in blocks if b.error), "No result could be produced."
        )
        return row

    if narrator is None:
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = _NO_MODEL
        return row

    gateway, llm = narrator
    try:
        completion = await gateway.complete(
            llm,
            narrate.section_messages(
                heading=section.heading,
                intent=section.intent,
                blocks=blocks,
                language=run.language,
                request=report.prompt,
            ),
        )
    except Exception as err:  # noqa: BLE001 — one section, not the run
        # The same rule the semantic generator follows per table: a provider
        # failure costs the paragraph it was writing, and the run reports
        # PARTIAL rather than losing the six sections that worked.
        log.warning(
            "report_section_narration_failed",
            run_id=str(run.id),
            section_id=str(section.id),
            error=str(err)[:200],
        )
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = str(err)[:500]
        return row

    row.prose = (completion.text or "").strip()
    if not row.prose:
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = "The model returned an empty paragraph."
        return row

    row.numeric_check = _numeric_check(row.prose, section, blocks).model_dump(
        mode="json"
    )
    return row


async def _summarise(
    settings: Settings,
    *,
    run: ReportRun,
    report: Report,
    section: ReportSection,
    position: int,
    written: list[WrittenSection],
    narrator: tuple[LLMGateway, ResolvedLLM] | None,
) -> ReportSectionResult:
    """The executive summary: written last, read first, from prose alone.

    It is given no data of its own on purpose. A summary that could reach the
    rows would be a second place for a figure to be invented; one that can only
    quote the sections can be checked against them — which is exactly what the
    numeric check does here, with the sections' own text as the haystack.
    """
    row = ReportSectionResult(
        id=uuid.uuid4(),
        run_id=run.id,
        section_id=section.id,
        position=position,
        heading_snapshot=section.heading[:300],
        prose="",
        status=ReportSectionResultStatus.OK,
    )

    if narrator is None:
        # Checked before emptiness: with no model, the sections below have no
        # prose *because there is no writer*, and a summary reporting "no data"
        # would blame the database for it.
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = _NO_MODEL
        return row
    if not written:
        row.prose = no_data_sentence(run.language)
        row.status = ReportSectionResultStatus.SKIPPED_NO_DATA
        return row

    gateway, llm = narrator
    try:
        completion = await gateway.complete(
            llm,
            narrate.summary_messages(
                sections=written, language=run.language, request=report.prompt
            ),
        )
    except Exception as err:  # noqa: BLE001 — a summary is not the report
        log.warning(
            "report_summary_failed", run_id=str(run.id), error=str(err)[:200]
        )
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = str(err)[:500]
        return row

    row.prose = (completion.text or "").strip()
    if not row.prose:
        row.status = ReportSectionResultStatus.FAILED
        row.error_message = "The model returned an empty summary."
        return row

    # The summary's figures must already be in the sections, so the sections'
    # own numerals are the pool it is checked against.
    body = " ".join(s.prose for s in written)
    row.numeric_check = checks.check_prose(
        row.prose, checks.figures_in(body), context=section.intent
    ).model_dump(mode="json")
    return row


def _narration(result: ReportBlockResult, policy: str) -> BlockNarration:
    """One stored result, as much of it as the policy lets the model see.

    This is the only place a report's results meet `disclose()`, and it runs at
    *narration* time rather than at execution time — so the policy that governs
    what the model reads is the one in force now, not the one in force when the
    query ran.
    """
    if result.status != ReportBlockResultStatus.OK:
        return BlockNarration(
            question=result.question_snapshot,
            error=result.error_message or "The query did not produce a result.",
        )

    disclosed = disclose(
        ExecutionResult(
            columns=[_column(c) for c in result.columns or []],
            rows=[list(row) for row in result.rows or []],
            row_count=result.row_count,
            truncated=result.truncated,
        ),
        policy,
    )
    return BlockNarration(
        question=result.question_snapshot,
        columns=list(disclosed.columns),
        rows=[list(row) for row in disclosed.rows],
        note=disclosed.note,
        kpi=_kpi_line(result.kpi),
        row_count=result.row_count,
    )


def _column(raw: dict[str, Any]) -> ResultColumn:
    """A stored column dict as the value object, tolerantly.

    `.get`, so a row written before a field existed rebuilds as a column
    without it rather than failing the whole section it belongs to.
    """
    return ResultColumn(
        name=str(raw.get("name", "")),
        db_type=str(raw.get("db_type", "")),
        semantic_type=str(raw.get("semantic_type", "nominal")),
    )


def _kpi_line(kpi: dict[str, Any] | None) -> str | None:
    """The headline figure as one line — Tier 1 of §9.

    `plan_kpi` computed it from the rows, so it is the one number in the
    section the model is not being asked to write. Handing it over lets the
    paragraph be qualitative around it instead of restating it to a precision
    it does not have.
    """
    if not kpi or not kpi.get("value"):
        return None
    line = f"{kpi.get('label') or 'value'}: {kpi['value']}"
    delta = kpi.get("delta") or {}
    if delta.get("text"):
        line += f" ({delta['text']} {delta.get('caption', '')})".rstrip(" )") + ")"
    return line


def _numeric_check(
    prose: str, section: ReportSection, blocks: list[BlockNarration]
) -> checks.NumericCheck:
    """Tier 2: every figure in the paragraph, looked for in what was disclosed.

    The pool is what the model was **given**, not what the query returned. A
    figure matched against a row the model never saw would be excused for a
    coincidence, which is the opposite of what this check is for.
    """
    pool = checks.numeric_values(
        [row for block in blocks for row in block.rows]
    )
    # A row count is a fact about the result that no cell holds, and "across 13
    # months" is the most natural sentence in the world to write from it.
    pool.extend(float(block.row_count) for block in blocks)
    context = " ".join(
        [section.heading, section.intent, *(block.question for block in blocks)]
    )
    return checks.check_prose(prose, pool, context=context)


# ── the run row ──────────────────────────────────────────────────────────
async def _touch(db: AsyncSession, run: ReportRun, **fields: Any) -> None:
    """Write progress and commit, so the next poll sees it.

    A cancel landed by the API while a query was in flight must not be undone
    by that query's progress update, so a run that is already CANCELLED is left
    alone — the same rule `semantic_service._touch_job` enforces.
    """
    await db.refresh(run)
    if run.status == ReportRunStatus.CANCELLED:
        await db.commit()
        return
    for key, value in fields.items():
        setattr(run, key, value)
    await db.commit()


async def _finish(
    db: AsyncSession, run: ReportRun, status: str, *, error: str | None = None
) -> None:
    fields: dict[str, Any] = {"status": status, "finished_at": utcnow(), "phase": ""}
    if error is not None:
        fields["error_message"] = error
    if status == ReportRunStatus.CANCELLED:
        # Cancellation is the one terminal state the API may also write, and
        # `_touch` would refuse to write it over itself.
        await db.refresh(run)
        for key, value in fields.items():
            setattr(run, key, value)
        await db.commit()
        return
    await _touch(db, run, **fields)
