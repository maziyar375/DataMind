"""In-process event bus: one asyncio queue per subscriber per run.

Replacing this with Redis pub/sub is a single adapter swap; nothing above
`EventPublisher` knows how fan-out happens.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.core.clock import utcnow

_SENTINEL: dict[str, Any] = {"__closed__": True}


class InProcessEventBus:
    def __init__(self, *, buffer_size: int = 512) -> None:
        self._subscribers: dict[UUID, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._history: dict[UUID, list[dict[str, Any]]] = {}
        self._seq: dict[UUID, int] = {}
        self._closed: set[UUID] = set()
        self._buffer_size = buffer_size
        self._lock = asyncio.Lock()

    async def publish(self, run_id: UUID, event_type: str, data: dict[str, Any]) -> int:
        async with self._lock:
            seq = self._seq.get(run_id, 0) + 1
            self._seq[run_id] = seq
            event = {
                "protocol_version": "1.0",
                "seq": seq,
                "run_id": str(run_id),
                "type": event_type,
                "at": utcnow().isoformat(),
                "data": data,
            }
            history = self._history.setdefault(run_id, [])
            history.append(event)
            if len(history) > self._buffer_size:
                del history[: len(history) - self._buffer_size]
            queues = list(self._subscribers.get(run_id, []))

        for queue in queues:
            queue.put_nowait(event)
        return seq

    async def subscribe(
        self, run_id: UUID, *, after_seq: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            replay = [e for e in self._history.get(run_id, []) if e["seq"] > after_seq]
            already_closed = run_id in self._closed
            self._subscribers.setdefault(run_id, []).append(queue)

        try:
            for event in replay:
                yield event
            if already_closed:
                return
            while True:
                event = await queue.get()
                if event is _SENTINEL or event.get("__closed__"):
                    return
                yield event
        finally:
            async with self._lock:
                subs = self._subscribers.get(run_id, [])
                if queue in subs:
                    subs.remove(queue)
                if not subs:
                    self._subscribers.pop(run_id, None)

    async def close_run(self, run_id: UUID) -> None:
        async with self._lock:
            self._closed.add(run_id)
            queues = list(self._subscribers.get(run_id, []))
        for queue in queues:
            queue.put_nowait(_SENTINEL)

    async def replay(self, run_id: UUID, after_seq: int = 0) -> list[dict[str, Any]]:
        async with self._lock:
            return [e for e in self._history.get(run_id, []) if e["seq"] > after_seq]

    def forget(self, run_id: UUID) -> None:
        self._history.pop(run_id, None)
        self._seq.pop(run_id, None)
        self._closed.discard(run_id)


event_bus = InProcessEventBus()
