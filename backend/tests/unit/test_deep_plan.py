"""The planner: one structured call, and a plan that is tidied before it runs.

docs/plans/deep-analysis-mode.md Phase 4: *a plan longer than `max_steps`
truncated rather than honoured.* Plus what the planner is shown — the schema
block the generator will see, under the same disclosure policy — and the two
ways it fails, both of which end the run because there is no evidence yet to
answer from.
"""
from __future__ import annotations

from datetime import timedelta

from app.core.clock import utcnow
from app.core.errors import LLMError
from app.domain.value_objects import DeepBudget, DisclosurePolicy
from app.pipeline.nodes import deep as deep_nodes
from app.pipeline.prompts import PROMPT_VERSION
from app.pipeline.prompts.deep import DEEP_PROMPT_VERSION
from app.pipeline.state import AnalysisPlan, PlanStep, SqlAttempt
from app.sqlguard.validator import ValidationReport
from tests.unit.deep_world import (
    DeepConnector,
    DeepGateway,
    DeepRecorder,
    deep_deps,
    deep_state,
    drive,
    plan_of,
)


async def _plan(plan: AnalysisPlan | Exception, **state_kw: object) -> tuple[object, object, DeepGateway]:
    gateway = DeepGateway(plan=plan)
    state = deep_state(**state_kw)  # type: ignore[arg-type]
    deps = deep_deps(gateway, DeepConnector(), DeepRecorder())
    result = await deep_nodes.plan(state, deps)
    return state, result, gateway


async def test_a_plan_past_the_ceiling_is_truncated_not_honoured() -> None:
    budget = DeepBudget(deadline_at=utcnow() + timedelta(minutes=1), max_steps=5)
    state, result, _ = await _plan(plan_of(*[f"Q{i}?" for i in range(8)]), budget=budget)
    assert [s.question for s in state.plan.steps] == [f"Q{i}?" for i in range(5)]  # type: ignore[attr-defined]
    assert result.detail == "5 steps planned · 3 past the ceiling dropped"  # type: ignore[attr-defined]


async def test_a_dependency_on_a_step_that_has_not_run_is_dropped() -> None:
    plan = AnalysisPlan(restatement="r", stop_when="s", steps=[
        PlanStep(question="A?", intent="CONFIRM", why="", tool="SQL", depends_on=[0, 1]),
        PlanStep(question="B?", intent="DECOMPOSE", why="", tool="CONTRIBUTION",
                 depends_on=[0, 1, 7, -1]),
    ])
    state, _, _ = await _plan(plan)
    assert [s.depends_on for s in state.plan.steps] == [[], [0]]  # type: ignore[attr-defined]


async def test_blank_steps_are_removed_and_none_left_fails_the_run() -> None:
    plan = plan_of("  ", "")
    state, result, _ = await _plan(plan)
    assert result.status == "FAILED"  # type: ignore[attr-defined]
    assert state.error is not None and state.error.code == "E_PLAN"  # type: ignore[attr-defined]


async def test_a_provider_error_fails_the_run_before_any_step() -> None:
    gateway = DeepGateway(plan=LLMError("provider down"))
    state, recorder = await drive(deep_state(), gateway, DeepConnector())
    assert recorder.settled() == [("route", "DONE"), ("plan", "FAILED")]
    assert state.error is not None and state.error.code == "E_PLAN"
    assert state.evidence == []


def test_every_plan_field_is_required_in_the_schema_and_forgiven_in_the_parse() -> None:
    """`ClarificationProposal`'s lesson: a defaulted field drops out of
    `required`, and a model under a strict schema then omits it — which here
    would make every step plain SQL and `compute` would never run."""
    required = set(PlanStep.model_json_schema()["required"])
    assert required == {"question", "intent", "why", "tool", "depends_on"}
    assert set(AnalysisPlan.model_json_schema()["required"]) == {
        "restatement", "steps", "stop_when",
    }
    parsed = AnalysisPlan.model_validate({"steps": [{"question": "A?"}]})
    assert parsed.steps[0].tool == "SQL" and parsed.steps[0].depends_on == []


async def test_the_planner_sees_the_schema_under_the_policy_in_force() -> None:
    """The generator's own schema block — and its value hints are gated the
    same way, so NONE shows the planner no value from the data."""
    _, _, wide = await _plan(plan_of("A?"), policy=DisclosurePolicy.SAMPLE)
    _, _, narrow = await _plan(plan_of("A?"), policy=DisclosurePolicy.NONE)

    def system(gateway: DeepGateway) -> str:
        return next(m for name, m in gateway.messages if name == "AnalysisPlan")[0].content

    assert "public.orders(" in system(wide) and "public.orders(" in system(narrow)
    assert "shipped" in system(wide)
    assert "shipped" not in system(narrow)
    assert "at most 5 steps" in system(wide)


def test_the_planner_prompt_versions_on_its_own() -> None:
    assert DEEP_PROMPT_VERSION == "d1"
    assert DEEP_PROMPT_VERSION != PROMPT_VERSION


def test_repair_count_is_the_current_steps_not_the_runs() -> None:
    state = deep_state()
    state.attempts = [
        SqlAttempt(attempt_no=n, raw_sql="SELECT 1", report=ValidationReport())
        for n in (1, 2, 3)
    ]
    assert state.repair_count == 2
    state.step_first_attempt = 2
    assert state.repair_count == 0
    state.attempts.append(SqlAttempt(attempt_no=4, raw_sql="SELECT 1", report=ValidationReport()))
    assert state.repair_count == 1
