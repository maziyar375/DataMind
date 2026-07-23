from __future__ import annotations

import asyncio

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


async def reconcile_once(settings: Settings) -> int:
    from app.infra.db.session import get_sessionmaker
    from app.services.run_service import RunService

    async with get_sessionmaker()() as session:
        count = await RunService(session, settings).reconcile_stale()
    if count:
        log.warning("runs_reconciled", count=count)
    return count


async def reconciler_loop(settings: Settings) -> None:
    while True:
        try:
            await reconcile_once(settings)
        except Exception:
            log.exception("reconciler_failed")
        await asyncio.sleep(settings.reconciler_interval_seconds)
