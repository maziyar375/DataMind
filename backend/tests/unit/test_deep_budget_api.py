"""Who may start a deep analysis, what one may spend, and that both are logged.

docs/plans/deep-analysis-mode.md Phase 8's test: *the capability, the audit
row, and that a budget of zero steps refuses rather than degrading.* Called
through the real app with `test_access_behaviour.py`'s `World` where the answer
is the route's; through the service where the question is what got written,
because `World.call` rolls every request back.

The cap is only worth having if it fails **closed**, so the last block is the
ways it could fail open under load: many refusals at once, a run executed
after its connection was narrowed, and a run whose snapshot is gone.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa

from app.api import deps
from app.core.clock import utcnow
from app.core.errors import DeepRefusedError
from app.domain.value_objects import DeepLimits, RunStatus
from app.domain.value_objects.authz import Capability
from app.infra.db import models
from app.services import deep_budget
from app.services.run_service import RunService
from tests.unit.conftest import AsyncSessionShim
from tests.unit.test_access_behaviour import (
    World,
    _every_table,  # noqa: F401 - fixture
    _Session,
)
from tests.unit.test_deep_api import _authz, _ctx, _deep, _run, _thread

WITHOUT_DEEP = frozenset(Capability) - {Capability.DEEP_RUN}
BUDGET = "/api/v1/connections/{}/deep-budget"
FIVE = {
    "max_steps": 3, "max_queries": 8, "max_rows_total": 5_000,
    "max_prompt_tokens": 100_000, "deadline_seconds": 300,
}


@pytest.fixture
def world(db: AsyncSessionShim) -> World:
    return World(db)


def _settings(world: World) -> Any:
    return world.app.dependency_overrides[deps.get_settings]().model_copy(
        update={"deep_enabled": True}
    )


def _service(world: World) -> RunService:
    return RunService(_Session(world.db._session), _settings(world), _authz(world))  # type: ignore[arg-type]


def _audit(world: World, action: str) -> list[models.AuditLog]:
    return list(world.db._session.scalars(
        sa.select(models.AuditLog).where(models.AuditLog.action == action)
    ))


def _runs(world: World) -> list[models.Run]:
    return list(world.db._session.scalars(sa.select(models.Run)))


def _set(world: World, **limits: int) -> None:
    connection = world.db._session.get(models.DatabaseConnection, world.conn)
    connection.deep_budget = {**FIVE, **limits}
    world.db._session.flush()


class _Outcome:
    """`get_db`'s own commit-or-rollback, recorded — so a test can tell a
    refusal the route *returned* (its audit row commits) from one it raised
    (the row is rolled back with the request)."""

    def __init__(self, world: World) -> None:
        self.seen: list[str] = []
        shim = _Session(world.db._session)

        async def _db() -> AsyncIterator[Any]:
            try:
                yield shim
            except Exception:
                self.seen.append("rolled back")
                raise
            else:
                self.seen.append("committed")

        world.app.dependency_overrides[deps.get_db] = _db


# ── the capability ───────────────────────────────────────────────────────
async def test_without_deep_run_a_deep_question_is_refused(world: World) -> None:
    executor = _deep(world, on=True)
    outcome = _Outcome(world)
    thread = _thread(world)
    response = await world.as_(world.owner, WITHOUT_DEEP).call(
        "POST", f"/api/v1/conversations/{thread}/messages",
        {"content": "Why did revenue drop?", "depth": "DEEP"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "E_DEEP_REFUSED"
    assert body["reason"] == "capability"
    assert "deep.run" in body["detail"]
    assert executor.submitted == []
    # Returned, not raised: `get_db` commits, so the audit row survives.
    assert outcome.seen == ["committed"]


async def test_the_same_person_may_still_ask_a_quick_question(world: World) -> None:
    executor = _deep(world, on=True)
    thread = _thread(world)
    response = await world.as_(world.owner, WITHOUT_DEEP).call(
        "POST", f"/api/v1/conversations/{thread}/messages",
        {"content": "Revenue in March?"},
    )
    assert response.status_code == 202, response.text
    assert len(executor.submitted) == 1


async def test_a_capability_refusal_is_an_audit_row_and_nothing_else(world: World) -> None:
    thread = _thread(world)
    ctx = _ctx(world).__class__(
        user_id=world.owner, email="w@world.local", capabilities=WITHOUT_DEEP,
        team_ids=frozenset(), correlation_id="world",
    )
    before = world.db._session.scalar(sa.select(sa.func.count(models.Message.id)))
    with pytest.raises(DeepRefusedError):
        await _service(world).create_run(
            ctx=ctx, conversation_id=thread, content="Why?", connection_id=None,
            llm_config_id=None, depth="DEEP",
        )
    [row] = _audit(world, deep_budget.DEEP_REFUSED)
    assert row.outcome == "DENIED"
    assert row.resource_id == world.conn
    assert row.detail["reason"] == "capability"
    assert row.detail["conversation_id"] == str(thread)
    assert "Why" not in str(row.detail)  # identifiers, never the question
    # No question in the thread, no run: a refusal is not a smaller run.
    assert world.db._session.scalar(sa.select(sa.func.count(models.Message.id))) == before
    assert _runs(world) == []


async def test_a_retry_asks_for_the_capability_again(world: World) -> None:
    """The retry's actor is whoever pressed it; the first asker's capability
    does not carry over to them."""
    _deep(world, on=True)
    thread = _thread(world)
    failed = _run(world, thread, status=RunStatus.FAILED)
    response = await world.as_(world.owner, WITHOUT_DEEP).call(
        "POST", f"/api/v1/runs/{failed}/retry",
    )
    assert response.status_code == 403, response.text
    assert response.json()["reason"] == "capability"


async def test_deep_run_is_seeded_to_administrator_alone() -> None:
    import importlib

    migration = importlib.import_module(
        "app.infra.db.migrations.versions.0042_deep_governance"
    )
    assert migration.ROLES == ("Administrator",)
    assert migration.CAPABILITY == Capability.DEEP_RUN


# ── the budget, set per connection ───────────────────────────────────────
async def test_the_budget_reads_as_the_ceiling_until_somebody_sets_it(world: World) -> None:
    response = await world.as_(world.owner).call("GET", BUDGET.format(world.conn))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_default"] is True
    assert body["effective"] == body["ceiling"]
    assert body["ceiling"]["max_steps"] == 5
    assert body["ceiling"]["deadline_seconds"] == 600
    assert body["refused"] is None


async def test_describe_may_read_the_budget_and_may_not_set_it(world: World) -> None:
    read = await world.as_(world.glancer).call("GET", BUDGET.format(world.conn))
    assert read.status_code == 200, read.text
    write = await world.as_(world.glancer).call("PUT", BUDGET.format(world.conn), FIVE)
    assert write.status_code == 403, write.text


async def test_a_stranger_cannot_tell_the_connection_exists(world: World) -> None:
    response = await world.as_(world.stranger).call("GET", BUDGET.format(world.conn))
    assert response.status_code == 404


async def test_manage_sets_it_and_it_is_what_a_run_gets(world: World) -> None:
    response = await world.as_(world.owner).call("PUT", BUDGET.format(world.conn), FIVE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_default"] is False
    assert body["effective"] == FIVE


async def test_a_number_above_the_ceiling_is_refused_not_clipped(world: World) -> None:
    for bound, value in (("max_steps", 6), ("max_prompt_tokens", 400_001),
                         ("deadline_seconds", 601)):
        response = await world.as_(world.owner).call(
            "PUT", BUDGET.format(world.conn), {**FIVE, bound: value},
        )
        assert response.status_code == 422, (bound, response.text)
        assert bound in response.json()["detail"]


async def test_a_deadline_too_short_to_finish_a_step_is_refused(world: World) -> None:
    response = await world.as_(world.owner).call(
        "PUT", BUDGET.format(world.conn), {**FIVE, "deadline_seconds": 30},
    )
    assert response.status_code == 422


async def test_a_negative_number_is_not_a_budget(world: World) -> None:
    response = await world.as_(world.owner).call(
        "PUT", BUDGET.format(world.conn), {**FIVE, "max_queries": -1},
    )
    assert response.status_code == 422


async def test_a_budget_change_is_an_audit_row_naming_what_moved(world: World) -> None:
    connection = world.db._session.get(models.DatabaseConnection, world.conn)
    settings = _settings(world)
    ctx = _ctx(world)
    await deep_budget.set_budget(
        world.db, ctx, connection, DeepLimits(**FIVE), settings  # type: ignore[arg-type]
    )
    await deep_budget.set_budget(
        world.db, ctx, connection, DeepLimits(**{**FIVE, "max_steps": 2}), settings  # type: ignore[arg-type]
    )
    # The same numbers again moves nothing and writes nothing.
    await deep_budget.set_budget(
        world.db, ctx, connection, DeepLimits(**{**FIVE, "max_steps": 2}), settings  # type: ignore[arg-type]
    )
    first, second = _audit(world, deep_budget.DEEP_BUDGET_CHANGED)
    assert first.resource_id == world.conn
    assert first.detail["from_default"] is True
    assert first.detail["max_steps"] == "5 -> 3"
    assert second.detail == {"from_default": False, "max_steps": "3 -> 2"}


# ── zero refuses; it does not degrade ────────────────────────────────────
async def test_a_zero_step_budget_refuses_a_deep_question(world: World) -> None:
    executor = _deep(world, on=True)
    _set(world, max_steps=0)
    thread = _thread(world)
    response = await world.as_(world.owner).call(
        "POST", f"/api/v1/conversations/{thread}/messages",
        {"content": "Why did revenue drop?", "depth": "DEEP"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "E_DEEP_REFUSED"
    assert body["reason"] == "max_steps"
    assert "steps" in body["detail"]
    assert executor.submitted == []


async def test_a_zero_step_budget_writes_a_refusal_and_no_run(world: World) -> None:
    _set(world, max_steps=0)
    thread = _thread(world)
    with pytest.raises(DeepRefusedError):
        await _service(world).create_run(
            ctx=_ctx(world), conversation_id=thread, content="Why?",
            connection_id=None, llm_config_id=None, depth="DEEP",
        )
    [row] = _audit(world, deep_budget.DEEP_REFUSED)
    assert row.detail["reason"] == "max_steps"
    assert _runs(world) == []


@pytest.mark.parametrize("bound", [
    "max_steps", "max_queries", "max_rows_total", "max_prompt_tokens",
    "deadline_seconds",
])
async def test_every_bound_at_zero_refuses(world: World, bound: str) -> None:
    _set(world, **{bound: 0})
    thread = _thread(world)
    with pytest.raises(DeepRefusedError) as refused:
        await _service(world).create_run(
            ctx=_ctx(world), conversation_id=thread, content="Why?",
            connection_id=None, llm_config_id=None, depth="DEEP",
        )
    assert refused.value.detail["reason"] == bound


async def test_a_zero_budget_leaves_quick_questions_alone(world: World) -> None:
    _set(world, max_steps=0)
    thread = _thread(world)
    run = await _service(world).create_run(
        ctx=_ctx(world), conversation_id=thread, content="Revenue?",
        connection_id=None, llm_config_id=None,
    )
    assert run.depth == "QUICK" and run.deep_budget is None


async def test_the_budget_reads_as_refused_where_it_would_refuse(world: World) -> None:
    _set(world, max_steps=0)
    response = await world.as_(world.owner).call("GET", BUDGET.format(world.conn))
    assert response.json()["refused"] == "max_steps"


# ── fails closed, including under load ───────────────────────────────────
async def test_the_run_carries_the_budget_it_was_asked_under(world: World) -> None:
    _set(world)
    thread = _thread(world)
    run = await _service(world).create_run(
        ctx=_ctx(world), conversation_id=thread, content="Why?",
        connection_id=None, llm_config_id=None, depth="DEEP",
    )
    assert run.deep_budget == FIVE


async def test_a_lowered_ceiling_narrows_a_stored_budget(world: World) -> None:
    """A connection that stored 300 s is held to 120 the moment the
    installation's ceiling falls to 120 — without anybody editing the row."""
    _set(world)
    settings = _settings(world).model_copy(update={"deep_deadline_seconds": 120})
    connection = world.db._session.get(models.DatabaseConnection, world.conn)
    assert deep_budget.limits_for(connection, settings).deadline_seconds == 120


async def test_a_damaged_stored_budget_refuses_rather_than_reading_as_the_ceiling(
    world: World,
) -> None:
    connection = world.db._session.get(models.DatabaseConnection, world.conn)
    connection.deep_budget = {"max_steps": "lots"}
    world.db._session.flush()
    thread = _thread(world)
    with pytest.raises(DeepRefusedError) as refused:
        await _service(world).create_run(
            ctx=_ctx(world), conversation_id=thread, content="Why?",
            connection_id=None, llm_config_id=None, depth="DEEP",
        )
    assert refused.value.detail["reason"] == "unreadable"


async def test_many_refusals_at_once_are_each_a_refusal(world: World) -> None:
    """Twelve deep questions against a zero budget at the same moment: twelve
    refusals, twelve rows, no run — none of them slips through as a default."""
    _set(world, max_steps=0)
    thread = _thread(world)
    service = _service(world)

    async def ask() -> BaseException | None:
        try:
            await service.create_run(
                ctx=_ctx(world), conversation_id=thread, content="Why?",
                connection_id=None, llm_config_id=None, depth="DEEP",
            )
        except DeepRefusedError as err:
            return err
        return None

    outcomes = await asyncio.gather(*(ask() for _ in range(12)))
    assert all(isinstance(o, DeepRefusedError) for o in outcomes)
    assert len(_audit(world, deep_budget.DEEP_REFUSED)) == 12
    assert _runs(world) == []


async def test_narrowing_the_connection_does_not_touch_a_queued_run(world: World) -> None:
    """The snapshot is the budget. An operator who narrows a connection
    governs the next question, and cannot reach into one already asked —
    which also means a run queued before a *widening* is not widened."""
    _set(world)
    thread = _thread(world)
    run = await _service(world).create_run(
        ctx=_ctx(world), conversation_id=thread, content="Why?",
        connection_id=None, llm_config_id=None, depth="DEEP",
    )
    _set(world, max_steps=0)
    assert run.deep_budget == FIVE
    state = _deep_state_for(world, run)
    assert state.budget.max_steps == 3
    assert state.budget.max_queries == 8


def _deep_state_for(world: World, run: models.Run) -> Any:
    from app.pipeline.state import RunState

    base = RunState(
        run_id=run.id, conversation_id=run.conversation_id, owner_id=run.owner_id,
        connection_id=run.connection_id, question="Why?", dialect="postgres",
        deadline_at=utcnow() + timedelta(seconds=60), max_rows=1000,
    )
    return _service(world)._deep_state(base, DeepLimits.from_json(run.deep_budget))


async def test_the_deadline_is_the_snapshot_s_not_the_installation_s(world: World) -> None:
    _set(world)
    thread = _thread(world)
    run = await _service(world).create_run(
        ctx=_ctx(world), conversation_id=thread, content="Why?",
        connection_id=None, llm_config_id=None, depth="DEEP",
    )
    before = utcnow()
    state = _deep_state_for(world, run)
    hard = (state.deadline_at - before).total_seconds()
    soft = (state.budget.deadline_at - before).total_seconds()
    assert 299 <= hard <= 301
    assert 239 <= soft <= 241


@pytest.mark.parametrize("snapshot", [None, {"max_steps": 3}, {**FIVE, "max_steps": 0}])
async def test_a_deep_run_without_a_usable_snapshot_fails_rather_than_runs(
    world: World, snapshot: dict[str, Any] | None,
) -> None:
    """A DEEP row whose budget is missing, damaged or zero — written by an
    older build, edited by hand, or corrupted — is failed by the executor with
    `E_DEEP_BUDGET`. It never reaches the defaults, and never opens the
    connector."""
    thread = _thread(world)
    run_id = _run(world, thread, status=RunStatus.QUEUED)
    row = world.db._session.get(models.Run, run_id)
    row.deep_budget = snapshot
    world.db._session.flush()

    service = _service(world)
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def _claimed(_run_id: UUID, *, worker_id: str) -> bool:
        return True

    async def _nothing(*_a: Any, **_k: Any) -> None:
        return None

    async def _emit(_run_id: UUID, kind: str, data: dict[str, Any]) -> None:
        emitted.append((kind, data))

    service.claim = _claimed  # type: ignore[method-assign]
    service._prime_events = _nothing  # type: ignore[method-assign]
    service._emit = _emit  # type: ignore[method-assign]
    from app.services import run_service as module

    original = module.event_bus.close_run
    module.event_bus.close_run = _nothing  # type: ignore[assignment]
    try:
        await service.execute_run(run_id, worker_id="w")
    finally:
        module.event_bus.close_run = original  # type: ignore[assignment]

    assert row.status == RunStatus.FAILED
    assert row.error_code == "E_DEEP_BUDGET"
    assert emitted[-1][0] == "RUN_FINISHED"
    assert emitted[-1][1]["error_code"] == "E_DEEP_BUDGET"
