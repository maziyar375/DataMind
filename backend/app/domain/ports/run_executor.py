from __future__ import annotations

from typing import Protocol
from uuid import UUID


class RunExecutor(Protocol):
    """The swap point for Celery. In-process asyncio for the MVP."""

    async def submit(self, run_id: UUID) -> None: ...

    async def cancel(self, run_id: UUID) -> bool: ...

    @property
    def worker_id(self) -> str: ...
