"""The per-process half of event fan-out: one asyncio queue per subscriber.

A run is executed by exactly one process, but the browser watching it may be
talking to any of them. So there are two halves. This is the local one — it
hands an event to the subscribers attached *here* — and `listener.py` is the
other, which carries an event from the process that produced it to the
processes that did not.

**The durable log is the authority, not this.** Every event is written to
`run_events` in the same transaction that publishes it (`RunService._emit`),
and the SSE endpoint backfills from those rows before it attaches here. This
buffer is a low-latency path in front of a Postgres table, which is why
`forget()` can drop a run's history the moment the run closes: nothing is lost
that a reconnecting client cannot read back from the log.

`seq` is assigned here, by the process executing the run, and that is safe for
the same reason: exactly one process owns a run at a time — see
`RunService.claim`, which is what makes "exactly one" true rather than hoped
for.
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
            self._remember(run_id, event)
            queues = list(self._subscribers.get(run_id, []))

        for queue in queues:
            queue.put_nowait(event)
        return seq

    async def deliver(self, run_id: UUID, event: dict[str, Any]) -> bool:
        """Hand a subscriber here an event produced somewhere else.

        The counterpart to `publish`, for events that arrived from another
        replica through `listener.py`. It does not assign a `seq` — the
        producing process already did, and that number is in the durable row
        this event was read from.

        Returns whether the event was new. The de-duplication is the same
        watermark `publish` advances, which is what makes it safe to run a
        listener in *every* process including the one that owns the run: that
        process publishes seq N locally, then hears its own notification and
        arrives back here with seq N already seen, and drops it.
        """
        seq = int(event["seq"])
        async with self._lock:
            if seq <= self._seq.get(run_id, 0):
                return False
            self._seq[run_id] = seq
            self._remember(run_id, event)
            queues = list(self._subscribers.get(run_id, []))

        for queue in queues:
            queue.put_nowait(event)
        return True

    def _remember(self, run_id: UUID, event: dict[str, Any]) -> None:
        """Caller holds the lock."""
        history = self._history.setdefault(run_id, [])
        history.append(event)
        if len(history) > self._buffer_size:
            del history[: len(history) - self._buffer_size]

    def watermark(self, run_id: UUID) -> int:
        """The highest `seq` this process has seen for a run. 0 if none."""
        return self._seq.get(run_id, 0)

    def watching(self, run_id: UUID) -> bool:
        """Whether anyone here is subscribed.

        The listener asks before it reads: a notification about a run nobody in
        this process is watching costs one dictionary lookup and no query,
        which is what keeps N idle replicas from all querying `run_events` on
        every token of a run none of them is streaming.
        """
        return bool(self._subscribers.get(run_id))

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
        """Drop a finished run's buffer.

        Called from `RunService._finalise` after `close_run`, and from the
        listener when it sees a `RUN_FINISHED` for a run it was mirroring.
        Before Phase 6 nothing called it at all, so `_history`, `_seq` and
        `_closed` grew for the life of the process — every event of every run
        since boot, held in memory behind a durable copy of itself.

        Safe to call the moment a run closes because the SSE endpoint
        backfills from `run_events` before attaching: a client that reconnects
        after this still gets every event, from the log rather than from here.
        """
        self._history.pop(run_id, None)
        self._seq.pop(run_id, None)
        self._closed.discard(run_id)


event_bus = InProcessEventBus()
