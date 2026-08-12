"""In-process run executor.

A text-to-SQL run is 5-60 seconds, not 5 hours. Celery would add a second
deployment unit and a serialization boundary that makes SSE fan-out harder,
in exchange for durability the `runs` table plus a heartbeat already provides.

The trigger conditions for revisiting that, written down so the decision is
falsifiable rather than a preference:
  * p95 run duration exceeds ~5 minutes, or
  * runs must survive a rolling deploy mid-execution, or
  * more than one API replica needs to share a run queue.

**The third one happened** (Phase 6), and it did not need Celery. A shared
queue is `SELECT … FOR UPDATE SKIP LOCKED` over the `runs` table this process
already writes to — `RunService.claim` — which costs no broker and no second
deployment unit. What "in-process" now means precisely: a run is executed in
whichever API process claimed it, and *only* there, because the claim is
atomic. The two things that used to be reachable only from inside that
process — cancelling it, and watching it — are now a column and a Postgres
notification respectively.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from uuid import UUID

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class InProcessRunExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._claimer: asyncio.Task[None] | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def submit(self, run_id: UUID) -> None:
        task = asyncio.create_task(self._run(run_id), name=f"run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    async def cancel(self, run_id: UUID) -> bool:
        """Stop a run *this* process is executing.

        Still the fast path and still worth having: on one replica, and on the
        replica that happens to own the run, this is instant. It is no longer
        the whole story — `RunService.cancel` writes `cancel_requested` for the
        case where the run is somewhere else, and the owner picks that up on
        its next heartbeat. Returning `False` here now means "not mine", not
        "could not be cancelled".
        """
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # ── the queue half of the claim ──────────────────────────────────────
    def start_claiming(self) -> None:
        """Poll for runs nobody is executing.

        The direct hand-off in `post_message` covers the normal path at no
        latency cost; this covers the one it cannot. A process that accepts a
        request, commits the `runs` row and then dies before `submit` leaves a
        run that is queued and unowned — with one replica that run was simply
        lost until the reconciler failed it, and the user was told nothing had
        started. With several, another replica can pick it up.
        """
        self._claimer = asyncio.create_task(self._claim_loop(), name="run-claimer")

    async def stop_claiming(self) -> None:
        if self._claimer is None:
            return
        self._claimer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._claimer
        self._claimer = None

    async def _claim_loop(self) -> None:
        from app.infra.db.session import get_sessionmaker
        from app.services.run_service import RunService

        while True:
            await asyncio.sleep(self._settings.run_claim_interval_seconds)
            try:
                async with get_sessionmaker()() as session:
                    service = RunService(session, self._settings)
                    candidates = await service.claimable_runs()
                for run_id in candidates:
                    if run_id in self._tasks:
                        continue
                    # Not claimed here — `execute_run` claims, and it is the
                    # only place that does, so a run cannot be claimed by a
                    # path that then fails to execute it.
                    log.info("run_reclaimed", run_id=str(run_id))
                    await self.submit(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("run_claimer_failed")

    async def _run(self, run_id: UUID) -> None:
        from app.infra.db.session import get_sessionmaker
        from app.services.run_service import RunService

        async with self._semaphore:
            heartbeat: asyncio.Task[None] | None = None
            try:
                async with get_sessionmaker()() as session:
                    service = RunService(session, self._settings)
                    heartbeat = asyncio.create_task(self._heartbeat(run_id))
                    await service.execute_run(run_id, worker_id=self._worker_id)
            except asyncio.CancelledError:
                log.info("run_cancelled", run_id=str(run_id))
                raise
            except Exception:
                log.exception("run_executor_failed", run_id=str(run_id))
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()

    async def _heartbeat(self, run_id: UUID) -> None:
        """Say we are alive, and ask whether we have been told to stop.

        Both directions on one timer, because they are one round trip and the
        heartbeat was already paying for it. This is where a cancel issued at
        another replica arrives, which makes `run_heartbeat_seconds` the
        worst-case latency of a cross-replica cancel — 10s by default, against
        a run whose whole budget is 5-60s.
        """
        from app.infra.db.session import get_sessionmaker
        from app.services.run_service import RunService

        while True:
            await asyncio.sleep(self._settings.run_heartbeat_seconds)
            try:
                async with get_sessionmaker()() as session:
                    stop = await RunService(session, self._settings).heartbeat(run_id)
                if stop:
                    log.info("run_cancel_observed", run_id=str(run_id))
                    task = self._tasks.get(run_id)
                    if task is not None and not task.done():
                        task.cancel()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("heartbeat_failed", run_id=str(run_id))
