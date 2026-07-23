"""In-process run executor.

A text-to-SQL run is 5-60 seconds, not 5 hours. Celery would add a second
deployment unit and a serialization boundary that makes SSE fan-out harder,
in exchange for durability the `runs` table plus a heartbeat already provides.

The trigger conditions for revisiting that, written down so the decision is
falsifiable rather than a preference:
  * p95 run duration exceeds ~5 minutes, or
  * runs must survive a rolling deploy mid-execution, or
  * more than one API replica needs to share a run queue.
"""
from __future__ import annotations

import asyncio
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

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def submit(self, run_id: UUID) -> None:
        task = asyncio.create_task(self._run(run_id), name=f"run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    async def cancel(self, run_id: UUID) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

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
        from app.infra.db.session import get_sessionmaker
        from app.services.run_service import RunService

        while True:
            await asyncio.sleep(self._settings.run_heartbeat_seconds)
            try:
                async with get_sessionmaker()() as session:
                    await RunService(session, self._settings).heartbeat(run_id)
            except Exception:
                log.warning("heartbeat_failed", run_id=str(run_id))
