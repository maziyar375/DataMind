from __future__ import annotations

import uuid

import pytest

from app.infra.events.bus import InProcessEventBus


@pytest.mark.asyncio
async def test_sequence_numbers_are_monotonic() -> None:
    bus = InProcessEventBus()
    run_id = uuid.uuid4()
    seqs = [await bus.publish(run_id, "STEP_STARTED", {"n": i}) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_late_subscriber_replays_missed_events() -> None:
    """A browser that reconnects mid-run must not lose the earlier steps."""
    bus = InProcessEventBus()
    run_id = uuid.uuid4()
    for i in range(3):
        await bus.publish(run_id, "STEP_STARTED", {"n": i})
    await bus.close_run(run_id)

    received = [event async for event in bus.subscribe(run_id)]
    assert [e["data"]["n"] for e in received] == [0, 1, 2]


@pytest.mark.asyncio
async def test_replay_respects_after_seq() -> None:
    bus = InProcessEventBus()
    run_id = uuid.uuid4()
    for i in range(4):
        await bus.publish(run_id, "STEP_STARTED", {"n": i})
    await bus.close_run(run_id)

    received = [e async for e in bus.subscribe(run_id, after_seq=2)]
    assert [e["data"]["n"] for e in received] == [2, 3]


@pytest.mark.asyncio
async def test_runs_are_isolated_from_each_other() -> None:
    bus = InProcessEventBus()
    a, b = uuid.uuid4(), uuid.uuid4()
    await bus.publish(a, "X", {"which": "a"})
    await bus.publish(b, "X", {"which": "b"})
    await bus.close_run(a)

    received = [e async for e in bus.subscribe(a)]
    assert len(received) == 1
    assert received[0]["data"]["which"] == "a"
