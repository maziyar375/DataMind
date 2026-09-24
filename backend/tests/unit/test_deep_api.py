"""The deep surface over HTTP: depth, *Answer now*, the plan — and who may.

docs/plans/deep-analysis-mode.md Phase 6's test: *depth, answer-now, and that
both obey the same authorization as the run they belong to.* Called through the
real app with `test_access_behaviour.py`'s `World`, so every answer here is the
route's own — its dependency, its service call, its authorization — and not a
fake that agrees with the test.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.api import deps
from app.domain.value_objects import RunEventType, RunStatus
from app.infra.db import models
from app.pipeline import signals
from app.services.deep_plan import fold
from app.services.run_service import RunService
from tests.unit.conftest import AsyncSessionShim
from tests.unit.test_access_behaviour import (
    World,
    _every_table,  # noqa: F401 - fixture
    _Session,
    _team_grant,
)


@pytest.fixture
def world(db: AsyncSessionShim) -> World:
    return World(db)


def _ctx(world: World) -> Any:
    from app.core.context import RequestContext

    return RequestContext(
        user_id=world.owner, email="w@world.local", capabilities=world.capabilities,
        team_ids=frozenset(), correlation_id="world",
    )


def _authz(world: World) -> Any:
    from app.infra.authz.factory import build_authorizer

    settings = world.app.dependency_overrides[deps.get_settings]()
    return build_authorizer(_Session(world.db._session), settings)  # type: ignore[arg-type]


class _Executor:
    def __init__(self) -> None:
        self.submitted: list[UUID] = []

    async def submit(self, run_id: UUID) -> None:
        self.submitted.append(run_id)

    async def cancel(self, run_id: UUID) -> bool:
        return False


def _deep(world: World, *, on: bool) -> _Executor:
    settings = world.app.dependency_overrides[deps.get_settings]()
    enabled = settings.model_copy(update={"deep_enabled": on})
    world.app.dependency_overrides[deps.get_settings] = lambda: enabled
    executor = _Executor()
    world.app.state.run_executor = executor
    return executor


def _thread(world: World) -> UUID:
    s = world.db._session
    thread = models.Conversation(
        id=uuid4(), owner_id=world.owner, title="why", status="ACTIVE",
        default_connection_id=world.conn, default_llm_config_id=world.llm,
    )
    s.add(thread)
    s.flush()
    return thread.id


def _run(world: World, thread: UUID, *, depth: str = "DEEP",
         status: str = RunStatus.RUNNING) -> UUID:
    s = world.db._session
    question = models.Message(id=uuid4(), conversation_id=thread,
                              seq=uuid4().int % 10_000, role="USER", content="why?")
    s.add(question)
    s.flush()
    run = models.Run(
        id=uuid4(), conversation_id=thread, user_message_id=question.id,
        owner_id=world.owner, actor_id=world.owner, connection_id=world.conn,
        llm_config_id=world.llm, status=status, depth=depth,
        model_snapshot={"model": "m"}, prompt_version="v12",
    )
    s.add(run)
    s.flush()
    return run.id


def _event(world: World, run: UUID, seq: int, kind: str, data: dict[str, Any]) -> None:
    world.db._session.add(models.RunEventRow(run_id=run, seq=seq, type=kind, data=data))
    world.db._session.flush()


PLAN = {
    "restatement": "Why revenue fell in March.",
    "stop_when": "the driver is named",
    "steps": [
        {"question": "Revenue Feb vs Mar?", "intent": "CONFIRM", "why": "",
         "tool": "COMPARE_PERIODS", "depends_on": []},
        {"question": "By region?", "intent": "DECOMPOSE", "why": "",
         "tool": "CONTRIBUTION", "depends_on": [0]},
    ],
}
FOUND = {
    "index": 0, "question": "Revenue Feb vs Mar?", "intent": "CONFIRM",
    "tool": "COMPARE_PERIODS", "status": "DONE", "note": "", "row_count": 59,
    "sql": "SELECT day, SUM(total) FROM orders GROUP BY day", "attempts": 1,
    "computed": {"tool": "COMPARE_PERIODS", "ok": True,
                 "summary": "Per calendar day the change was +0.0%.", "refusal": None},
}


# ── depth ────────────────────────────────────────────────────────────────
async def test_deep_is_refused_while_the_mode_is_off(world: World) -> None:
    executor = _deep(world, on=False)
    thread = _thread(world)
    response = await world.as_(world.owner).call(
        "POST", f"/api/v1/conversations/{thread}/messages",
        {"content": "Why did revenue drop?", "depth": "DEEP"},
    )
    assert response.status_code == 422, response.text
    assert executor.submitted == []


async def test_a_deep_question_is_recorded_as_one_when_the_mode_is_on(world: World) -> None:
    executor = _deep(world, on=True)
    thread = _thread(world)
    response = await world.as_(world.owner).call(
        "POST", f"/api/v1/conversations/{thread}/messages",
        {"content": "Why did revenue drop?", "depth": "DEEP"},
    )
    assert response.status_code == 202, response.text
    assert executor.submitted == [UUID(response.json()["run_id"])]


async def test_create_run_writes_the_depth_it_was_asked_for(world: World) -> None:
    """Through the service, because `World.call` rolls each request back and
    the row would be gone by the time the test looked."""
    thread = _thread(world)
    settings = world.app.dependency_overrides[deps.get_settings]().model_copy(
        update={"deep_enabled": True}
    )
    ctx = _ctx(world)
    service = RunService(_Session(world.db._session), settings, _authz(world))  # type: ignore[arg-type]
    run = await service.create_run(
        ctx=ctx, conversation_id=thread, content="Why?", connection_id=None,
        llm_config_id=None, depth="DEEP",
    )
    assert run.depth == "DEEP" and run.answer_now_requested is False


async def test_depth_rejects_anything_but_quick_and_deep(world: World) -> None:
    _deep(world, on=True)
    thread = _thread(world)
    bad = await world.as_(world.owner).call(
        "POST", f"/api/v1/conversations/{thread}/messages",
        {"content": "Revenue?", "depth": "DEEPER"},
    )
    assert bad.status_code == 422


# ── answer now ───────────────────────────────────────────────────────────
async def test_answer_now_is_recorded_and_signalled(world: World) -> None:
    _deep(world, on=True)
    run = _run(world, _thread(world))
    response = await world.as_(world.owner).call("POST", f"/api/v1/runs/{run}/answer-now")
    assert response.status_code == 202, response.text
    # The local road: instant, on the replica that took the click.
    assert signals.answer_now_requested(run)
    signals.forget(run)

    # The durable road, read without the per-request rollback.
    settings = world.app.dependency_overrides[deps.get_settings]()
    service = RunService(_Session(world.db._session), settings, _authz(world))  # type: ignore[arg-type]
    await service.answer_now(_ctx(world), run)
    row = world.db._session.get(models.Run, run)
    assert row.answer_now_requested is True
    # And it is not cancel: the run is still running, nothing was requested
    # to stop.
    assert row.status == RunStatus.RUNNING and row.cancel_requested is False
    signals.forget(run)


async def test_answer_now_obeys_the_runs_authorization(world: World) -> None:
    """A stranger gets cancel's answer — the run does not exist for them —
    and a reader with `select` on the thread may watch but not steer."""
    _deep(world, on=True)
    thread = _thread(world)
    run = _run(world, thread)
    _team_grant(world.db._session, type_="conversation", resource_id=thread,
                privilege="select", user=world.viewer)

    stranger = await world.as_(world.stranger).call("POST", f"/api/v1/runs/{run}/answer-now")
    reader = await world.as_(world.viewer).call("POST", f"/api/v1/runs/{run}/answer-now")

    assert stranger.status_code == 404
    assert reader.status_code in (403, 404)
    assert world.db._session.get(models.Run, run).answer_now_requested is False
    assert not signals.answer_now_requested(run)


async def test_answer_now_needs_a_running_deep_run(world: World) -> None:
    _deep(world, on=True)
    thread = _thread(world)
    quick = _run(world, thread, depth="QUICK")
    done = _run(world, thread, status=RunStatus.SUCCEEDED)
    for run in (quick, done):
        response = await world.as_(world.owner).call("POST", f"/api/v1/runs/{run}/answer-now")
        assert response.status_code == 409, response.text


async def test_the_heartbeat_hands_answer_now_to_the_graph_without_stopping_it(
    world: World,
) -> None:
    """The cross-replica road: the click landed elsewhere, the column is set,
    and the owner learns of it on its heartbeat — which still says *don't
    stop*, because answer-now is not a stop."""
    run = _run(world, _thread(world))
    world.db._session.get(models.Run, run).answer_now_requested = True
    world.db._session.flush()

    settings = world.app.dependency_overrides[deps.get_settings]()
    stop = await RunService(_Session(world.db._session), settings).heartbeat(run)  # type: ignore[arg-type]
    assert stop is False
    assert signals.answer_now_requested(run)
    signals.forget(run)


# ── the plan ─────────────────────────────────────────────────────────────
async def test_a_late_reader_gets_the_plan_folded_from_its_events(world: World) -> None:
    _deep(world, on=True)
    run = _run(world, _thread(world))
    _event(world, run, 3, RunEventType.PLAN_PROPOSED, {**PLAN, "max_steps": 5})
    _event(world, run, 9, RunEventType.STEP_EVIDENCE, FOUND)
    _event(world, run, 11, RunEventType.PLAN_REVISED, {
        "index": 1, "replaced": PLAN["steps"][1],
        "by": {**PLAN["steps"][1], "question": "By region, EMEA first?"},
    })

    body = (await world.as_(world.owner).call("GET", f"/api/v1/runs/{run}/plan")).json()
    assert body["finished"] is False and body["restricted"] is False
    assert body["plan"]["restatement"] == "Why revenue fell in March."
    assert [s["question"] for s in body["plan"]["steps"]] == [
        "Revenue Feb vs Mar?", "By region, EMEA first?",
    ]
    assert body["revisions"][0]["replaced"]["question"] == "By region?"
    assert body["steps"][0]["sql"] == FOUND["sql"]


async def test_an_ended_run_is_read_from_its_analysis_artifact(world: World) -> None:
    run = _run(world, _thread(world), status=RunStatus.SUCCEEDED)
    world.db._session.add(models.Artifact(
        id=uuid4(), run_id=run, kind="ANALYSIS",
        spec={"plan": PLAN, "revisions": [], "steps": [FOUND], "stop_reason": "answer_now",
              "claims": [{"text": "Flat per day.", "cites": 1}], "traceable": 1.0,
              "budget": {"steps": 1}, "prompt_version": "d1"},
    ))
    world.db._session.flush()
    body = (await world.as_(world.owner).call("GET", f"/api/v1/runs/{run}/plan")).json()
    assert body["finished"] is True
    assert body["stop_reason"] == "answer_now"
    assert body["claims"][0]["cites"] == 1
    assert "prompt_version" not in body


async def test_a_reader_without_the_data_gets_the_questions_and_nothing_found(
    world: World,
) -> None:
    """`select` on the thread, nothing on the connection: the plan is the
    transcript, the statements and the figures are the data."""
    thread = _thread(world)
    run = _run(world, thread)
    _team_grant(world.db._session, type_="conversation", resource_id=thread,
                privilege="select", user=world.viewer)
    _event(world, run, 3, RunEventType.PLAN_PROPOSED, PLAN)
    _event(world, run, 9, RunEventType.STEP_EVIDENCE, FOUND)

    response = await world.as_(world.viewer).call("GET", f"/api/v1/runs/{run}/plan")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restricted"] is True
    assert body["plan"]["steps"][0]["question"] == "Revenue Feb vs Mar?"
    assert body["steps"] == [{"index": 0, "question": "Revenue Feb vs Mar?",
                              "intent": "CONFIRM", "tool": "COMPARE_PERIODS",
                              "status": "DONE"}]


async def test_a_stranger_cannot_read_the_plan(world: World) -> None:
    run = _run(world, _thread(world))
    response = await world.as_(world.stranger).call("GET", f"/api/v1/runs/{run}/plan")
    assert response.status_code == 404


def test_a_replayed_step_is_one_step() -> None:
    folded = fold([
        (RunEventType.PLAN_PROPOSED, PLAN),
        (RunEventType.STEP_EVIDENCE, FOUND),
        (RunEventType.STEP_EVIDENCE, FOUND),
    ])
    assert len(folded["steps"]) == 1


# ── what the interface is told exists ────────────────────────────────────
async def test_me_names_the_deep_feature_only_when_it_is_on(world: World) -> None:
    _deep(world, on=False)
    off = await world.as_(world.owner).call("GET", "/api/v1/auth/me")
    _deep(world, on=True)
    on = await world.call("GET", "/api/v1/auth/me")
    assert off.json()["features"] == []
    assert on.json()["features"] == ["deep"]
