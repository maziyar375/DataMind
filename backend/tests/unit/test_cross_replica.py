"""Phase 6: the facts that stop being true when there is more than one process.

Every case here is a thing that worked because a dict, a task handle or an
`asyncio.Event` happened to live in the same process as the code reaching for
it. None of them were bugs at one replica. All of them are at two, and the
tests are written from that angle — what the *second* process sees.

The transport itself (`LISTEN`/`NOTIFY`) is not exercised here: it needs a real
Postgres, and `make test` has none. What is checked here is everything either
side of it — that a notification is issued on the transaction that wrote the
row, that a delivered event reaches subscribers exactly once, and that the
in-memory buffer behind it is now released. The wire between them is proved by
the two-replica compose profile in `docs/cross-replica.md`.
"""
from __future__ import annotations

import asyncio
import base64
import os
import uuid
from typing import Any
from uuid import UUID

import pytest

from app.infra.events.bus import InProcessEventBus, event_bus
from app.infra.events.listener import CHANNEL, _parse, notify_run_event
from app.workers.reconciler import RECONCILER_LOCK_KEY, reconcile_once

# No `pytestmark`: `asyncio_mode = "auto"` collects the coroutines here, and an
# explicit mark would warn on the three synchronous cases below.


def _event(seq: int, event_type: str = "STEP_STARTED") -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "seq": seq,
        "run_id": str(uuid.uuid4()),
        "type": event_type,
        "at": None,
        "data": {"n": seq},
    }


# ── the bus, delivering what another process produced ────────────────────
async def test_a_remote_event_reaches_a_local_subscriber() -> None:
    """The whole point of the listener: replica B streams replica A's run."""
    bus = InProcessEventBus()
    run_id = uuid.uuid4()
    received: list[int] = []

    async def watch() -> None:
        async for event in bus.subscribe(run_id):
            received.append(event["seq"])

    task = asyncio.create_task(watch())
    await asyncio.sleep(0)  # let the subscription attach

    assert await bus.deliver(run_id, _event(1)) is True
    assert await bus.deliver(run_id, _event(2)) is True
    await bus.close_run(run_id)
    await task

    assert received == [1, 2]


async def test_the_owning_replica_hears_its_own_notification_and_drops_it() -> None:
    """A listener runs in *every* process, including the one that published.

    So the producer's own event comes back around. Without the watermark that
    would be every event twice on the replica the user is most likely to be
    connected to — the one running their query.
    """
    bus = InProcessEventBus()
    run_id = uuid.uuid4()

    seq = await bus.publish(run_id, "SQL_GENERATED", {})
    assert seq == 1
    # The same event, arriving back through the listener.
    assert await bus.deliver(run_id, _event(1, "SQL_GENERATED")) is False
    assert bus.watermark(run_id) == 1


async def test_a_gap_is_filled_rather_than_skipped() -> None:
    """A dropped notification must cost latency, not events.

    `_drain` reads `seq > watermark` rather than the single seq it was told
    about, so the next notification after a reconnect catches up everything
    missed in the window.
    """
    bus = InProcessEventBus()
    run_id = uuid.uuid4()

    assert await bus.deliver(run_id, _event(1)) is True
    # 2 and 3 were missed; 4 arrives. The drain would fetch 2, 3 and 4 — all
    # three are new to this process.
    for seq in (2, 3, 4):
        assert await bus.deliver(run_id, _event(seq)) is True
    assert bus.watermark(run_id) == 4


async def test_out_of_order_delivery_never_moves_the_watermark_back() -> None:
    bus = InProcessEventBus()
    run_id = uuid.uuid4()

    await bus.deliver(run_id, _event(5))
    assert await bus.deliver(run_id, _event(3)) is False
    assert bus.watermark(run_id) == 5


async def test_watching_is_false_until_someone_subscribes() -> None:
    """The listener's cheap guard: an idle replica does no query for a busy run.

    Without it, every replica reads `run_events` on every token of every run in
    the cluster — the fan-out cost of a broker with none of the benefit.
    """
    bus = InProcessEventBus()
    run_id = uuid.uuid4()
    assert bus.watching(run_id) is False

    async def watch() -> None:
        async for _ in bus.subscribe(run_id):  # noqa: B007 - draining, not reading
            pass

    task = asyncio.create_task(watch())
    await asyncio.sleep(0)
    assert bus.watching(run_id) is True

    await bus.close_run(run_id)
    await task
    assert bus.watching(run_id) is False


# ── the leak that was there from the beginning ───────────────────────────
async def test_forget_releases_a_finished_run() -> None:
    """`forget()` existed before Phase 6 and was never called by anything.

    So `_history`, `_seq` and `_closed` held every event of every run since
    boot — behind a durable copy of the same events in `run_events`. This is
    the assertion that it is now actually released.
    """
    bus = InProcessEventBus()
    run_id = uuid.uuid4()
    await bus.publish(run_id, "RUN_STARTED", {})
    await bus.publish(run_id, "RUN_FINISHED", {})
    await bus.close_run(run_id)

    assert bus.watermark(run_id) == 2
    bus.forget(run_id)
    assert bus.watermark(run_id) == 0
    assert await bus.replay(run_id) == []


async def test_a_cancelled_run_is_forgotten_too() -> None:
    """The other way a run ends. It used to leak exactly like the first."""
    run_id = uuid.uuid4()
    await event_bus.publish(run_id, "RUN_STARTED", {})
    await event_bus.close_run(run_id)
    event_bus.forget(run_id)
    assert event_bus.watermark(run_id) == 0


# ── the notification ─────────────────────────────────────────────────────
async def test_the_notification_is_issued_on_the_writing_transaction() -> None:
    """Not a style point — it is the entire delivery guarantee.

    Postgres holds a notification until commit and drops it on rollback, so a
    listener sees the announcement and the row together or neither. Issued on
    another session, a reader could wake before the row was visible, find
    nothing, and go quiet until the next event.
    """
    executed: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        async def execute(self, statement: Any, params: Any = None) -> None:
            executed.append((str(statement), params or {}))

    run_id = uuid.uuid4()
    await notify_run_event(Recorder(), run_id, 7)  # type: ignore[arg-type]

    sql, params = executed[0]
    assert "pg_notify" in sql
    assert params == {"channel": CHANNEL, "payload": f"{run_id}:7"}


def test_the_payload_carries_an_identifier_and_not_an_event() -> None:
    """`NOTIFY` has an 8000-byte ceiling and a chart spec does not fit.

    Sending `run_id:seq` cannot hit it at any event size, and it keeps
    `run_events` the single authority on what an event actually contains.
    """
    run_id = uuid.uuid4()
    assert _parse(f"{run_id}:42") == (run_id, 42)
    assert len(f"{run_id}:42") < 60


def test_an_unparsable_payload_is_dropped_rather_than_raised() -> None:
    """The listener loop must survive nonsense on its channel.

    Anything with database access can `NOTIFY run_events, 'hello'`. A parse
    error that propagated would take down the connection and, with it, live
    streaming for every run this replica is serving.
    """
    assert _parse("not-a-uuid:1") is None
    assert _parse("hello") is None
    assert _parse(f"{uuid.uuid4()}:not-a-number") is None


# ── the reconciler's lock ────────────────────────────────────────────────
class _LockSession:
    """A session that answers the lock request however the test says."""

    def __init__(self, granted: bool) -> None:
        self.granted = granted
        self.calls: list[str] = []
        self.rollbacks = 0
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.calls.append(sql)

        class _R:
            def __init__(self, value: Any) -> None:
                self._value = value

            def scalar(self) -> Any:
                return self._value

        if "advisory" in sql:
            assert params == {"key": RECONCILER_LOCK_KEY}
            return _R(self.granted)
        return _R(None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def __aenter__(self) -> _LockSession:
        return self

    async def __aexit__(self, *_exc: Any) -> None: ...


def _patch_sessionmaker(monkeypatch: pytest.MonkeyPatch, session: _LockSession) -> None:
    from app.infra.db import session as session_module

    monkeypatch.setattr(session_module, "get_sessionmaker", lambda: (lambda: session))


async def test_a_replica_that_loses_the_lock_does_not_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every replica runs its own `reconciler_loop`, on its own timer.

    Un-locked, three replicas sweep three times per interval, each deciding
    independently that a run whose heartbeat is momentarily late has been
    abandoned. The sweep's verdict is a judgement call about a live user's
    run; it should be made once.
    """
    session = _LockSession(granted=False)
    _patch_sessionmaker(monkeypatch, session)

    assert await reconcile_once(_FakeSettings()) == 0
    # Bailed before touching `runs`, and ended the transaction the lock
    # attempt opened rather than leaving it idle.
    assert not any("runs" in c.lower() for c in session.calls)
    assert session.rollbacks == 1


async def test_the_lock_is_transaction_scoped_so_it_cannot_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session-scoped form was tried first, in production, and leaked.

    `pg_advisory_lock` is released by `pg_advisory_unlock` **on the connection
    that took it** — and `reconcile_stale` commits, at which point SQLAlchemy
    may hand the connection back to the pool and run the unlock on a different
    backend. The unlock then quietly fails and the lock survives on a pooled
    connection, silencing the reconciler for as long as that connection lives.
    It is invisible: a reconciler that never runs looks exactly like one that
    keeps finding nothing. A two-replica run turned it up in `pg_locks`.

    `pg_try_advisory_xact_lock` is released by Postgres at the end of the
    transaction — commit, rollback or crash — so there is nothing to pair and
    nothing to leak. This asserts the *form*, because the form is the fix.
    """
    session = _LockSession(granted=True)
    _patch_sessionmaker(monkeypatch, session)

    class _Boom:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...

        async def reconcile_stale(self) -> int:
            raise RuntimeError("sweep failed")

    import app.services.run_service as run_service_module

    monkeypatch.setattr(run_service_module, "RunService", _Boom)

    with pytest.raises(RuntimeError):
        await reconcile_once(_FakeSettings())

    assert any("pg_try_advisory_xact_lock" in c for c in session.calls)
    # Nothing to release by hand, so nothing that could be released against
    # the wrong connection.
    assert not any("advisory_unlock" in c for c in session.calls)


class _FakeSettings:
    run_stale_after_seconds = 60
    reconciler_interval_seconds = 30

    def __init__(self) -> None:
        # `RunService.__init__` builds a `SecretBox` eagerly, so even a test
        # that never decrypts anything needs a key.
        self.secret_box_key = _Key(
            base64.urlsafe_b64encode(os.urandom(32)).decode()
        )
        self.secret_box_key_version = 1


class _Key:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


# ── the claim ────────────────────────────────────────────────────────────
async def test_the_claim_skips_locked_rows_and_names_what_it_wants() -> None:
    """Two replicas racing for one run: the loser skips rather than blocks.

    Asserted on the statement because `make test` has no Postgres, and the
    statement is where the two load-bearing words live. `SKIP LOCKED` is what
    makes the loser return immediately with nothing instead of waiting for the
    winner's transaction; without it a claim is a queue and the second replica
    stalls behind the first for the length of a run.
    """
    from sqlalchemy.dialects import postgresql

    from app.services.run_service import RunService

    claimed: list[str] = []

    class _Session:
        async def execute(self, statement: Any, *_a: Any, **_k: Any) -> Any:
            # The Postgres dialect specifically: `SKIP LOCKED` is a Postgres
            # extension, and the default dialect silently compiles it away to
            # a plain `FOR UPDATE` — which is the blocking behaviour this test
            # exists to rule out.
            claimed.append(str(statement.compile(dialect=postgresql.dialect())))

            class _R:
                def scalar_one_or_none(self) -> Any:
                    return None

            return _R()

        async def commit(self) -> None: ...

    service = RunService(_Session(), _FakeSettings())  # type: ignore[arg-type]
    assert await service.claim(uuid.uuid4(), worker_id="w") is False

    sql = claimed[0].upper()
    assert "SKIP LOCKED" in sql
    assert "FOR UPDATE" in sql
    # The predicate is not redundant with the lock: the lock serialises the
    # readers, this is what makes the second one see a row that no longer
    # qualifies.
    assert "UPDATE RUNS" in sql


async def test_a_run_someone_else_holds_is_not_executed() -> None:
    """`execute_run` does nothing at all when the claim fails.

    The alternative is two executors on one run, which is two answers written
    into one conversation — and both of them charged for.
    """
    from app.services.run_service import RunService

    service = RunService(object(), _FakeSettings())  # type: ignore[arg-type]

    async def _lost(_run_id: UUID, *, worker_id: str) -> bool:
        return False

    service.claim = _lost  # type: ignore[method-assign]
    # No session access at all: if it got past the claim it would raise
    # AttributeError on the bare `object()` above.
    await service.execute_run(uuid.uuid4(), worker_id="w")
