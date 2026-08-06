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
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import DisclosureTooNarrowError
from app.core.logging import get_logger
from app.domain.value_objects import (
    ReportBlockResultStatus,
    ReportBlockType,
    ReportRunStatus,
)
from app.infra.db.models import (
    DatabaseConnection,
    Report,
    ReportBlock,
    ReportBlockResult,
    ReportRun,
    ReportSection,
)
from app.services.query_service import TileRequest, TileResult, execute_many
from app.services.report_service import assert_wide_enough

log = get_logger(__name__)

# A generation already runs its blocks concurrently against the customer's
# database; two whole reports at once is a load test of it, not a speed-up.
MAX_CONCURRENT_JOBS = 2

_NO_SQL = (
    "This block has not been checked, so it has no query to run. Check it in "
    "the outline, then generate again."
)


class ReportRunExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._flags: dict[UUID, asyncio.Event] = {}

    async def submit(self, run_id: UUID) -> None:
        cancelled = asyncio.Event()
        self._flags[run_id] = cancelled
        task = asyncio.create_task(self._run(run_id, cancelled), name=f"report:{run_id}")
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

    async def _run(self, run_id: UUID, cancelled: asyncio.Event) -> None:
        from app.infra.db.session import get_sessionmaker

        async with self._semaphore:
            try:
                async with get_sessionmaker()() as session:
                    await generate_run(session, self._settings, run_id, cancelled)
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
    """Execute every block of a run and persist the results as they land.

    Phase 5 is data only: the prose, the executive summary and the numeric
    check are Phase 6, and they hang off the same loop — a section's paragraph
    is written once its blocks' results exist.

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

    blocks = await _ordered_blocks(db, report.id)
    if cancelled.is_set():
        await _finish(db, run, ReportRunStatus.CANCELLED)
        return

    await _touch(
        db,
        run,
        status=ReportRunStatus.RUNNING,
        started_at=utcnow(),
        progress_total=len(blocks),
        progress_current=0,
        phase=f"Running {len(blocks)} quer{'y' if len(blocks) == 1 else 'ies'}",
    )

    results = await _execute_blocks(db, settings, connection, blocks, run.owner_id)

    outcomes: list[bool] = []
    for position, (block, heading) in enumerate(blocks):
        result = results.get(block.id) or _no_sql_result()
        db.add(_block_result(run, block, heading, result, position))
        outcomes.append(result.status == "OK")
        await _touch(
            db,
            run,
            progress_current=position + 1,
            phase=f"Wrote result {position + 1} of {len(blocks)}",
        )

    if cancelled.is_set():
        # The results that landed are kept: they were paid for, and a cancelled
        # run that threw them away would be a slower way of doing nothing.
        await _finish(db, run, ReportRunStatus.CANCELLED)
        return

    await _finish(db, run, derive_status(outcomes))


# ── execution ────────────────────────────────────────────────────────────
async def _ordered_blocks(
    db: AsyncSession, report_id: UUID
) -> list[tuple[ReportBlock, str]]:
    """Every block of the report in document order, each with its heading.

    The heading travels with the block because it is copied onto the result
    row: a run has to stay readable after the section it came from is renamed
    or deleted, and a historical document that silently loses a heading is not
    a historical document.
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
        return []

    order = {section.id: (index, section.heading) for index, section in enumerate(sections)}
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
    return [(block, order[block.section_id][1]) for block in blocks]


async def _execute_blocks(
    db: AsyncSession,
    settings: Settings,
    connection: DatabaseConnection,
    blocks: list[tuple[ReportBlock, str]],
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
        for block, _heading in blocks
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
