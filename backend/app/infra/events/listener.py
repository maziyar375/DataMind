"""The cross-replica half of event fan-out, over Postgres `LISTEN`/`NOTIFY`.

A run is executed by one process. A browser watching it may be connected to
any of them. Something has to carry an event across, and the migration record
([docs/langgraph-migration.md](../../../../docs/langgraph-migration.md) §4,
Phase 6) originally said Redis. It is Postgres instead, for the reason Phase 4
declined a checkpointer: **the rows already exist.** `run_events` is a durable,
ordered log with `UNIQUE(run_id, seq)`, written on every emit, and the SPA
already has a polling fallback that reads it. A broker would be a second copy
of that, in a second deployment unit, with its own delivery guarantees to
reason about.

**The notification carries no payload worth losing.** It is `run_id:seq` and
nothing else; the listener reads the event body out of `run_events`. Three
things fall out of that:

* `NOTIFY` has an 8000-byte payload ceiling, and some events (a chart spec, a
  result check) do not fit. Sending an identifier cannot hit it.
* Postgres delivers a notification **at commit**, and the row is written in
  that same transaction — so a listener that wakes and reads can never find
  the row missing. Ordering and visibility are the database's problem, and it
  already solved it.
* A dropped notification is a delay, not a loss. The next one for the same run
  makes the reader fetch `seq > watermark`, which catches up everything it
  missed, and a client that reconnects reads the log directly anyway.

The connection is dedicated and outside the pool: `LISTEN` is
session-scoped, so a pooled connection would stop listening the moment it was
handed back.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.infra.db.models import RunEventRow
from app.infra.events.bus import event_bus

log = get_logger(__name__)

#: One channel for every run. Postgres channel names are identifiers, so a
#: channel per run would mean a `LISTEN` per run and an unbounded set of them;
#: the run id travels in the payload instead and `event_bus.watching` throws
#: away what this process does not care about, before any query.
CHANNEL = "run_events"

#: How long a reconnect waits after the listener's connection drops. Short,
#: because until it is back this replica's SSE clients see nothing live — they
#: are not broken (the endpoint backfills from the log, and the SPA falls back
#: to polling) but they are behind.
_RECONNECT_SECONDS = 2.0


async def notify_run_event(db: AsyncSession, run_id: UUID, seq: int) -> None:
    """Announce one event, on the transaction that wrote it.

    Must be called on the **same** session as the `RunEventRow` insert and
    before its commit. That is not a convention, it is the whole delivery
    guarantee: Postgres holds a notification until commit and drops it on
    rollback, so a listener either sees the notification *and* the row, or
    neither. Calling this on a different session would reintroduce exactly the
    window this design exists to avoid — a wake-up that reads and finds
    nothing, followed by silence until the next event.
    """
    await db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": CHANNEL, "payload": f"{run_id}:{seq}"},
    )


def _parse(payload: str) -> tuple[UUID, int] | None:
    run_id, _, seq = payload.rpartition(":")
    try:
        return UUID(run_id), int(seq)
    except ValueError:
        log.warning("run_event_notify_unparsable", payload=payload[:100])
        return None


async def _drain(run_id: UUID, through_seq: int) -> None:
    """Feed this process's subscribers everything they have not seen yet.

    Reads `seq > watermark` rather than the one seq the notification named, so
    a notification lost in a reconnect window costs nothing: the next one
    fetches the gap too. `deliver` drops anything already seen, which is what
    makes the owning replica — which hears its own notifications — idempotent.
    """
    from app.infra.db.session import get_sessionmaker

    after = event_bus.watermark(run_id)
    if after >= through_seq:
        return

    async with get_sessionmaker()() as session:
        rows = await session.execute(
            select(RunEventRow)
            .where(RunEventRow.run_id == run_id, RunEventRow.seq > after)
            .order_by(RunEventRow.seq)
        )
        events = [
            {
                "protocol_version": "1.0",
                "seq": row.seq,
                "run_id": str(run_id),
                "type": row.type,
                "at": row.at.isoformat() if row.at else None,
                "data": row.data,
            }
            for row in rows.scalars()
        ]

    for event in events:
        await event_bus.deliver(run_id, event)
        if event["type"] == "RUN_FINISHED":
            # The mirror of what the owning replica does in `_finalise`: close
            # the local subscribers, then drop the buffer. Without this a
            # replica that only ever *watched* runs would hold every event of
            # every run it mirrored until it restarted.
            await event_bus.close_run(run_id)
            event_bus.forget(run_id)


class RunEventListener:
    """One `LISTEN` connection per process, restarted if it drops."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="run-event-listener")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never fatal. A replica with no listener still serves correct
                # SSE — the endpoint backfills from `run_events` and the SPA
                # falls back to polling — it just stops being live.
                log.exception("run_event_listener_dropped")
            await asyncio.sleep(_RECONNECT_SECONDS)

    async def _listen(self) -> None:
        from app.infra.db.session import get_engine

        engine = get_engine()
        raw = await engine.raw_connection()
        try:
            # asyncpg's own connection, below SQLAlchemy's wrapper — the
            # listener API is not part of the DBAPI shim.
            connection = raw.driver_connection
            if connection is None:  # pragma: no cover - asyncpg always sets it
                raise RuntimeError("no driver connection to LISTEN on")
            queue: asyncio.Queue[str] = asyncio.Queue()

            def on_notify(
                _conn: Any, _pid: int, _channel: str, payload: str
            ) -> None:
                queue.put_nowait(payload)

            await connection.add_listener(CHANNEL, on_notify)
            log.info("run_event_listener_ready", channel=CHANNEL)
            try:
                while True:
                    parsed = _parse(await queue.get())
                    if parsed is None:
                        continue
                    run_id, seq = parsed
                    # The cheap check first: a replica streaming nothing does
                    # no work at all for a busy run elsewhere.
                    if not event_bus.watching(run_id):
                        continue
                    try:
                        await _drain(run_id, seq)
                    except Exception:
                        log.exception("run_event_drain_failed", run_id=str(run_id))
            finally:
                with contextlib.suppress(Exception):
                    await connection.remove_listener(CHANNEL, on_notify)
        finally:
            # `raw_connection` hands back a pooled wrapper; closing it returns
            # the underlying connection rather than dropping it.
            with contextlib.suppress(Exception):
                raw.close()
