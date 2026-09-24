"""The guard's sixth entry point: a deep run's sub-queries.

docs/plans/deep-analysis-mode.md §5 rule 1, and Phase 5's non-negotiable test.
Five doors already replay the hostile corpus — the `validate` node
(`test_sqlguard_hostile.py`), saved SQL (`test_query_service.py`), report
blocks (`test_report_guard.py`), dashboard import
(`test_dashboard_transfer.py`) and knowledge templates
(`test_knowledge_guard.py`). A deep run multiplies generated statements per
question by five to ten, and every one of them walks the same `validate` node
through the same repair region; this proves it, statement by statement,
rather than trusting the wiring.

**The moment one door is special, the guarantee is gone.** So the corpus goes
through each place a deep statement can come from: a step's first draft, a
repair after the guard refused, a repair after the database refused, and a
step the executor *revised* — the one statement in the product whose
question was written by a model reading earlier results.
"""
from __future__ import annotations

import pytest

from app.core.errors import ConnectorError
from app.pipeline.state import AnalysisPlan, PlanStep, StepRevision
from tests.unit.deep_world import (
    BY_STATUS,
    DeepConnector,
    DeepGateway,
    deep_state,
    drive,
    plan_of,
    result,
)
from tests.unit.test_sqlguard_hostile import HOSTILE, LEGITIMATE
from tests.unit.test_sqlguard_hostile import POLICY as CORPUS_POLICY

#: A statement the corpus policy accepts, so the step *after* a hostile one
#: has something legitimate to run and the test can see the run went on.
FINE = LEGITIMATE[1]  # SELECT status, COUNT(*) FROM orders GROUP BY status


def _connector() -> DeepConnector:
    return DeepConnector(lambda _sql: result(BY_STATUS, [["shipped", 3.0]]))


@pytest.mark.parametrize("sql,expected_code", HOSTILE)
async def test_a_hostile_first_draft_never_reaches_the_database(
    sql: str, expected_code: str | None
) -> None:
    """Refused twice (draft and repair), closed as FAILED evidence, and the
    analysis goes on to the next step, which runs."""
    gateway = DeepGateway(plan=plan_of("A?", "B?"), sql=[sql, sql, FINE])
    connector = _connector()
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector,
                           policy=CORPUS_POLICY)

    refused = state.attempts[:2]
    assert [a.report.status for a in refused] == ["REJECTED", "REJECTED"], (
        f"BYPASS — a deep sub-query was accepted: {sql!r}"
    )
    assert all(a.rewritten_sql is None for a in refused)
    if expected_code is not None:
        codes = {i.rule_id for a in refused for i in a.report.errors}
        assert expected_code in codes, f"Expected {expected_code} for {sql!r}, got {codes}"
    # Nothing but the legitimate statement ever reached the driver.
    assert len(connector.executed) == 1
    assert sql.strip() not in connector.executed
    assert [e.status for e in state.evidence] == ["FAILED", "DONE"]


@pytest.mark.parametrize("sql,_code", HOSTILE)
async def test_a_hostile_repair_after_a_database_error_is_refused(
    sql: str, _code: str | None
) -> None:
    """The repair region is re-entered from `execute`; the statement it
    produces there is guarded like a first draft."""
    gateway = DeepGateway(plan=plan_of("A?"), sql=[FINE, sql])
    calls = {"n": 0}

    def once_then_fine(_sql: str) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectorError("relation is locked")
        return result(BY_STATUS, [["shipped", 1.0]])

    connector = DeepConnector(once_then_fine)  # type: ignore[arg-type]
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector,
                           policy=CORPUS_POLICY)

    assert state.attempts[1].report.status == "REJECTED", f"BYPASS on repair: {sql!r}"
    assert len(connector.executed) == 1  # the first, legitimate, locked one
    assert state.evidence[0].status == "FAILED"


@pytest.mark.parametrize("sql,_code", HOSTILE)
async def test_a_revised_step_gets_no_exemption(sql: str, _code: str | None) -> None:
    """The question was rewritten by a model from earlier results; the SQL it
    leads to is still just a statement, and the guard still decides."""
    plan = AnalysisPlan(restatement="r", stop_when="s", steps=[
        PlanStep(question="A?", intent="CONFIRM", why="", tool="SQL", depends_on=[]),
        PlanStep(question="Drill into it", intent="DRILL", why="", tool="SQL",
                 depends_on=[0]),
    ])

    class Reviser(DeepGateway):
        async def structured(self, llm, messages, schema, **kw):  # type: ignore[no-untyped-def]
            if schema is StepRevision:
                self.messages.append(("StepRevision", list(messages)))
                return StepRevision(keep=False, question="Drill into shipped",
                                    why="it moved", intent="DRILL", tool="SQL")
            return await super().structured(llm, messages, schema, **kw)

    gateway = Reviser(plan=plan, sql=[FINE, sql, sql])
    connector = _connector()
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector,
                           policy=CORPUS_POLICY)

    assert state.revisions and state.revisions[0].by.question == "Drill into shipped"
    assert [a.report.status for a in state.attempts[1:]] == ["REJECTED", "REJECTED"]
    assert len(connector.executed) == 1
