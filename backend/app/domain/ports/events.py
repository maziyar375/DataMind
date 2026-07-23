from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID


class EventPublisher(Protocol):
    async def publish(self, run_id: UUID, event_type: str, data: dict[str, Any]) -> int: ...

    def subscribe(self, run_id: UUID, *, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]: ...

    async def close_run(self, run_id: UUID) -> None: ...
