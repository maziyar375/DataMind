"""In-process executor for semantic-layer generation.

The same trade `InProcessRunExecutor` makes, one size up: a generation is
minutes rather than seconds, so it gets a lower concurrency ceiling and no
heartbeat. Durability comes from the `semantic_jobs` row — a process that
dies mid-generation leaves a RUNNING row, and `sweep_orphans` at startup
turns it into a FAILED one the user can retry, which is the honest outcome
since nothing was persisted.

Cancellation is cooperative *and* hard: the generator polls the event between
tables so an in-flight provider call is allowed to finish rather than being
abandoned mid-request, and the task is cancelled outright if it does not stop.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

# One generation already runs several model calls in parallel; two at once on
# a free-tier endpoint is a rate-limit generator, not a speed-up.
MAX_CONCURRENT_JOBS = 2


class SemanticJobExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._flags: dict[UUID, asyncio.Event] = {}

    async def submit(self, job_id: UUID) -> None:
        cancelled = asyncio.Event()
        self._flags[job_id] = cancelled
        task = asyncio.create_task(
            self._run(job_id, cancelled), name=f"semantic:{job_id}"
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._forget(job_id))

    async def cancel(self, job_id: UUID) -> bool:
        """Ask first, then insist.

        Setting the flag lets the generator stop between tables and record its
        partial statistics; the hard cancel a moment later covers a job stuck
        inside a provider call that will never return.
        """
        flag = self._flags.get(job_id)
        task = self._tasks.get(job_id)
        if flag is None or task is None or task.done():
            return False
        flag.set()

        async def insist() -> None:
            await asyncio.sleep(self._settings.llm_request_timeout_seconds + 5)
            if not task.done():
                task.cancel()

        asyncio.create_task(insist())  # noqa: RUF006 (fire-and-forget by design)
        return True

    def _forget(self, job_id: UUID) -> None:
        self._tasks.pop(job_id, None)
        self._flags.pop(job_id, None)

    async def _run(self, job_id: UUID, cancelled: asyncio.Event) -> None:
        from app.infra.db.session import get_sessionmaker
        from app.services.semantic_service import SemanticService

        async with self._semaphore:
            try:
                async with get_sessionmaker()() as session:
                    service = SemanticService(session, self._settings)
                    await service.execute_job(job_id, cancelled)
            except asyncio.CancelledError:
                log.info("semantic_job_cancelled", job_id=str(job_id))
                raise
            except Exception:
                log.exception("semantic_executor_failed", job_id=str(job_id))


async def sweep_orphans() -> int:
    """Fail jobs left RUNNING by a process that died. Returns how many."""
    from sqlalchemy import select

    from app.core.clock import utcnow
    from app.infra.db.models import SemanticJobRow
    from app.infra.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(SemanticJobRow).where(
                SemanticJobRow.status.in_(("QUEUED", "RUNNING"))
            )
        )
        rows = list(result.scalars())
        for row in rows:
            row.status = "FAILED"
            row.finished_at = utcnow()
            row.error_message = (
                "The server restarted while this generation was running. "
                "Nothing was saved — start it again."
            )
        await session.commit()
        return len(rows)
