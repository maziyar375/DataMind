"""The loop: revision, the compute dispatch, the written answer, the rows it leaves.

docs/plans/deep-analysis-mode.md Phase 5, "done when": *a deep run answers a
why question against the sales fixture with a report, every sub-query is a
row in `generated_queries`, and `make guard` is green with the corpus replayed
through the new path.* The guard half is `test_deep_guard.py`; the disclosure
half is `test_deep_disclosure.py`; this is the rest.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ConnectorError, LLMError
from app.domain.ports.database import ResultColumn
from app.domain.value_objects import DeepBudget, DisclosurePolicy, RunStatus
from app.infra.db.models import Conversation, GeneratedQuery, Message, QueryExecution, Run
from app.pipeline.state import AnalysisPlan, PlanStep, StepRevision
from app.services.run_service import RunService
from tests.unit.conftest import ACTOR, AsyncSessionShim, _connection
from tests.unit.deep_world import (
    BY_STATUS,
    REVENUE,
    SQL_BY_STATUS,
    SQL_FORBIDDEN,
    SQL_TOTAL,
    DeepConnector,
    DeepGateway,
    deep_state,
    drive,
    result,
)

PERIOD_SEGMENT = [
    ResultColumn(name="month", db_type="text", semantic_type="nominal"),
    ResultColumn(name="region", db_type="text", semantic_type="nominal"),
    ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
]
SERIES = [
    ResultColumn(name="day", db_type="date", semantic_type="temporal"),
    ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
]


def _step(question: str, tool: str = "SQL", depends_on: list[int] | None = None,
          intent: str = "CONFIRM") -> PlanStep:
    return PlanStep(question=question, intent=intent, why="",  # type: ignore[arg-type]
                    tool=tool, depends_on=depends_on or [])  # type: ignore[arg-type]


def _plan(*steps: PlanStep) -> AnalysisPlan:
    return AnalysisPlan(restatement="why revenue fell", stop_when="named", steps=list(steps))


class Scripted(DeepGateway):
    """A gateway whose revision verdicts are scripted too."""

    def __init__(self, *, revisions: list[StepRevision | Exception] | None = None,
                 **kw: Any) -> None:
        super().__init__(**kw)
        self.revisions = list(revisions or [])

    async def structured(self, llm, messages, schema, **kw):  # type: ignore[no-untyped-def]
        if schema is StepRevision:
            self.messages.append(("StepRevision", list(messages)))
            verdict = self.revisions.pop(0)
            if isinstance(verdict, Exception):
                raise verdict
            return verdict
        return await super().structured(llm, messages, schema, **kw)


# ── revision ─────────────────────────────────────────────────────────────
async def test_a_dependent_step_is_replaced_and_the_plan_does_not_grow() -> None:
    gateway = Scripted(
        plan=_plan(_step("Revenue by region?"),
                   _step("Drill into the region that fell", depends_on=[0], intent="DRILL")),
        sql=[SQL_BY_STATUS, SQL_TOTAL],
        revisions=[StepRevision(keep=False, question="Revenue by product in EMEA?",
                                why="EMEA fell most", intent="DRILL", tool="SQL")],
    )
    connector = DeepConnector([result(BY_STATUS, [["EMEA", 1.0]]), result(REVENUE, [[1.0]])])
    state, recorder = await drive(deep_state(), gateway, connector)

    assert state.plan is not None and len(state.plan.steps) == 2
    assert state.plan.steps[1].question == "Revenue by product in EMEA?"
    [revision] = state.revisions
    assert (revision.index, revision.replaced.question) == (1, "Drill into the region that fell")
    # The replacement is what was asked of the generator, and it is shown as
    # a revision in the trail.
    asked = [m[-1].content for n, m in gateway.messages if n == "SqlProposal"]
    assert "Revenue by product in EMEA?" in asked[1]
    assert "(revised)" in (recorder.detail("step")[1] or "")


async def test_a_step_with_no_dependencies_is_never_sent_for_revision() -> None:
    gateway = Scripted(plan=_plan(_step("A?"), _step("B?")), sql=[SQL_TOTAL, SQL_TOTAL])
    connector = DeepConnector(lambda _s: result(REVENUE, [[1.0]]))
    await drive(deep_state(), gateway, connector)
    assert "StepRevision" not in {n for n, _ in gateway.messages}


async def test_a_revision_fails_open() -> None:
    gateway = Scripted(
        plan=_plan(_step("A?"), _step("B?", depends_on=[0])),
        sql=[SQL_TOTAL, SQL_TOTAL], revisions=[LLMError("provider down")],
    )
    connector = DeepConnector(lambda _s: result(REVENUE, [[1.0]]))
    state, _ = await drive(deep_state(), gateway, connector)
    assert state.revisions == []
    assert [e.status for e in state.evidence] == ["DONE", "DONE"]


async def test_no_revision_when_a_dependency_failed() -> None:
    gateway = Scripted(
        plan=_plan(_step("A?"), _step("B?", depends_on=[0])),
        sql=[SQL_FORBIDDEN, SQL_FORBIDDEN, SQL_TOTAL],
    )
    connector = DeepConnector(lambda _s: result(REVENUE, [[1.0]]))
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector)
    assert "StepRevision" not in {n for n, _ in gateway.messages}
    assert [e.status for e in state.evidence] == ["FAILED", "DONE"]


# ── the compute dispatch ─────────────────────────────────────────────────
async def test_contribution_names_the_driver_known_by_construction() -> None:
    """Revenue falls 100 between two months, and 90 of it is EMEA."""
    rows = [
        ["2026-02", "EMEA", 500.0], ["2026-02", "APAC", 300.0], ["2026-02", "AMER", 200.0],
        ["2026-03", "EMEA", 410.0], ["2026-03", "APAC", 295.0], ["2026-03", "AMER", 195.0],
    ]
    gateway = DeepGateway(plan=_plan(_step("Revenue by region, Feb vs Mar?", "CONTRIBUTION")),
                          sql=[SQL_BY_STATUS])
    state, _ = await drive(deep_state(), gateway,
                           DeepConnector([result(PERIOD_SEGMENT, rows)]))
    computed = state.evidence[0].computed
    assert computed is not None and computed["ok"], computed
    drivers = computed["facts"][1]["text"]
    assert drivers.startswith("region = EMEA")
    assert "90.0% of the total change" in drivers
    # The generator was told the shape the rows had to arrive in.
    asked = next(m[-1].content for n, m in gateway.messages if n == "SqlProposal")
    assert "one row per period and segment" in asked


async def test_compare_periods_reports_per_day_beside_the_total() -> None:
    rows = [[date(2026, 1, 1) + timedelta(days=i), 10.0] for i in range(59)]
    gateway = DeepGateway(plan=_plan(_step("Daily revenue, Jan and Feb?", "COMPARE_PERIODS")),
                          sql=[SQL_TOTAL])
    state, _ = await drive(deep_state(), gateway, DeepConnector([result(SERIES, rows)]))
    computed = state.evidence[0].computed
    assert computed is not None and computed["ok"]
    assert "Per calendar day the change was +0.0%" in computed["summary"]


async def test_a_refusal_is_a_result_not_a_failure() -> None:
    """Nothing moved: the step is DONE, and the refusal is its finding."""
    rows = [["2026-02", "EMEA", 5.0], ["2026-03", "EMEA", 5.0]]
    gateway = DeepGateway(plan=_plan(_step("By region?", "CONTRIBUTION")), sql=[SQL_BY_STATUS])
    state, recorder = await drive(deep_state(), gateway,
                                  DeepConnector([result(PERIOD_SEGMENT, rows)]))
    [e] = state.evidence
    assert e.status == "DONE"
    assert e.computed is not None and e.computed["refusal"] == "NO_CHANGE"
    assert "refused (NO_CHANGE)" in (recorder.detail("compute")[0] or "")


async def test_a_capped_result_is_not_computed_over() -> None:
    capped = replace(
        result(PERIOD_SEGMENT, [["2026-02", "EMEA", 5.0], ["2026-03", "EMEA", 1.0]]),
        truncated=True,
    )
    gateway = DeepGateway(plan=_plan(_step("By region?", "CONTRIBUTION")), sql=[SQL_BY_STATUS])
    state, _ = await drive(deep_state(), gateway, DeepConnector([capped]))
    assert state.evidence[0].computed["refusal"] == "TRUNCATED"  # type: ignore[index]


async def test_an_unknown_tool_is_skipped_without_spending_a_query() -> None:
    """The planner selects from a closed set. Something outside it — here
    forced past validation — closes its step as SKIPPED and runs nothing."""
    odd = PlanStep.model_construct(question="Forecast it", intent="CHECK", why="",
                                   tool="FORECAST", depends_on=[])
    gateway = DeepGateway(plan=_plan(odd, _step("B?")), sql=[SQL_TOTAL])
    connector = DeepConnector([result(REVENUE, [[1.0]])])
    state, recorder = await drive(deep_state(), gateway, connector)

    assert [e.status for e in state.evidence] == ["SKIPPED", "DONE"]
    assert len(connector.executed) == 1 and len(state.attempts) == 1
    assert recorder.settled()[2] == ("step", "SKIPPED")


# ── the written answer ───────────────────────────────────────────────────
async def test_the_answer_cites_its_steps_and_each_claim_is_checked_against_its_own() -> None:
    gateway = DeepGateway(
        plan=_plan(_step("Total?"), _step("By status?")),
        sql=[SQL_TOTAL, SQL_BY_STATUS],
        prose=("Revenue was 1,700 [1]. ", "Shipped was 9,999 [2]."),
    )
    connector = DeepConnector([
        result(REVENUE, [[1700.0]]),
        result(BY_STATUS, [["shipped", 1000.0], ["pending", 700.0]]),
    ])
    state, recorder = await drive(deep_state(), gateway, connector)

    assert state.answer == "Revenue was 1,700. Shipped was 9,999."
    assert state.synthesis is not None
    claims = state.synthesis["claims"]
    assert [c["cites"] for c in claims] == [1, 2]
    assert claims[0]["unsupported"] == []
    # 9,999 is in no result of step two: flagged against *its own* step.
    assert [f["value"] for f in claims[1]["unsupported"]] == [9999.0]
    # The markers streamed live; the record replaces them with clean prose.
    kinds = [t for t, _ in recorder.events]
    assert "TEXT_RESET" in kinds
    assert recorder.events[-1][0] != "TEXT_RESET"


async def test_an_answer_built_from_part_of_the_plan_says_so_first() -> None:
    gateway = DeepGateway(plan=_plan(_step("A?"), _step("B?"), _step("C?")),
                          sql=lambda _m: SQL_TOTAL, prose=("Partial [1].",))
    connector = DeepConnector(lambda _s: result(REVENUE, [[1.0]]))
    budget = DeepBudget(deadline_at=utcnow() + timedelta(minutes=5), max_queries=1)
    state, _ = await drive(deep_state(budget=budget), gateway, connector)
    assert state.answer is not None
    assert state.answer.startswith(
        "This answer is built from 1 of 3 planned steps: the analysis stopped "
        "early because it reached the query limit."
    )
    # And the writer was told the same, so it does not overclaim.
    writer = next(m for n, m in gateway.messages if n == "stream")
    assert "Say what the steps that ran establish" in writer[-1].content


async def test_a_failed_writer_falls_back_to_the_evidence() -> None:
    class Broken(DeepGateway):
        def stream(self, *_a: Any, **_kw: Any) -> Any:
            async def gen() -> Any:
                raise LLMError("provider down")
                yield  # pragma: no cover
            return gen()

    gateway = Broken(plan=_plan(_step("Total?")), sql=[SQL_TOTAL])
    state, recorder = await drive(deep_state(), gateway,
                                  DeepConnector([result(REVENUE, [[1700.0]])]))
    assert state.error is None
    assert state.answer == "1. Total? — 1 rows"
    assert ("TEXT_RESET", {"reason": "stream_failed"}) in recorder.events


async def test_under_none_the_answer_is_written_without_a_model() -> None:
    gateway = DeepGateway(plan=_plan(_step("Total?")), sql=[SQL_TOTAL])
    state, _ = await drive(deep_state(policy=DisclosurePolicy.NONE), gateway,
                           DeepConnector([result(REVENUE, [[1700.0]])]))
    assert "stream" not in {n for n, _ in gateway.messages}
    assert state.answer == "1. Total? — 1 rows"
    assert state.synthesis is None


# ── repairs, counted for the run ─────────────────────────────────────────
async def test_the_runs_repairs_are_the_sum_of_its_steps() -> None:
    gateway = DeepGateway(plan=_plan(_step("A?"), _step("B?"), _step("C?")),
                          sql=[SQL_FORBIDDEN, SQL_TOTAL, SQL_TOTAL, SQL_FORBIDDEN, SQL_TOTAL])
    connector = DeepConnector(lambda _s: result(REVENUE, [[1.0]]))
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector)
    assert [e.repairs for e in state.evidence] == [1, 0, 1]
    assert state.total_repairs == 2
    assert len(state.attempts) - 1 == 4  # what the chat definition would have said


# ── every sub-query is a row ─────────────────────────────────────────────
async def test_every_sub_query_is_a_generated_queries_row_filed_against_its_own_result(
    db: AsyncSessionShim, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real `_finalise`, on the conftest's SQLite schema.

    `pg_notify` is the one statement SQLite cannot run, and `_emit` rolls the
    session back when it fails — so it is the one thing replaced here.
    """

    async def no_notify(*_a: Any) -> None:
        return None

    monkeypatch.setattr("app.services.run_service.notify_run_event", no_notify)
    gateway = DeepGateway(plan=_plan(_step("A?"), _step("B?")),
                          sql=[SQL_TOTAL, SQL_BY_STATUS, SQL_BY_STATUS])
    calls = {"n": 0}

    def results(_sql: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectorError("relation is locked")
        return (result(REVENUE, [[1.0]]) if calls["n"] == 1
                else result(BY_STATUS, [["a", 1.0], ["b", 2.0], ["c", 3.0]]))

    state, _ = await drive(deep_state(max_repairs=1), gateway, DeepConnector(results))
    assert [e.row_count for e in state.evidence] == [1, 3]

    session = db._session
    connection = _connection(session, owner_id=ACTOR)
    thread = Conversation(id=uuid4(), owner_id=ACTOR, title="why", status="ACTIVE",
                          default_connection_id=connection.id)
    session.add(thread)
    session.flush()
    question = Message(id=uuid4(), conversation_id=thread.id, seq=1, role="USER",
                       content=state.asked)
    session.add(question)
    session.flush()
    run = Run(id=state.run_id, conversation_id=thread.id, user_message_id=question.id,
              owner_id=ACTOR, actor_id=ACTOR, connection_id=connection.id,
              status=RunStatus.RUNNING, model_snapshot={"model": "m"},
              prompt_version="v12", started_at=utcnow())
    session.add(run)
    session.flush()

    await RunService(db, Settings())._finalise(run, state)  # type: ignore[arg-type]

    queries = session.scalars(
        select(GeneratedQuery).where(GeneratedQuery.run_id == run.id)
        .order_by(GeneratedQuery.attempt_no)
    ).all()
    assert [q.attempt_no for q in queries] == [1, 2, 3]
    executions = {
        e.generated_query_id: e.row_count
        for e in session.scalars(select(QueryExecution)).all()
    }
    # Attempt 1 is step one's result; attempt 2 was refused by the database
    # and repaired; attempt 3 is step two's result. Each filed against its own.
    assert [executions.get(q.id) for q in queries] == [1, None, 3]
    assert run.repair_count == 1
    assert run.attempt_count == 3
    assert run.status == RunStatus.SUCCEEDED
