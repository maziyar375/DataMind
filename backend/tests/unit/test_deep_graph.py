"""The deep graph: its edges, a run through it, and a budget no path exceeds.

docs/plans/deep-analysis-mode.md Phase 4, "done when": *a test can drive a
deep run end to end with stubbed nodes, and the budget cannot be exceeded by
any path through the graph.* The nodes here are the real ones; what is
stubbed is everything they reach — the gateway and the connector
(`deep_world.py`).

Each bound gets a run that tries to cross it. Three are refused before they
are spent (steps, queries, rows) and two are checked before every call and so
may be overrun by the one call in flight (tokens, time) — `DeepBudget` says
which is which, and the tests below hold it to what it says.
"""
from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ConnectorError, RunTimeoutError
from app.domain.value_objects import DeepBudget
from app.pipeline.graph import CHAT_GRAPH, DEEP_GRAPH, DRAFT_GRAPH, deep_recursion_limit
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
    plan_of,
    result,
)

END = "__end__"
START = "__start__"


def _edges(graph: object) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.get_graph().edges}  # type: ignore[attr-defined]


def _budget(**bounds: int) -> DeepBudget:
    return DeepBudget(deadline_at=utcnow() + timedelta(minutes=5), **bounds)


# ── the wiring ───────────────────────────────────────────────────────────
def test_the_deep_graph_is_exactly_these_edges() -> None:
    """Read off the compiled graph, so a dropped edge fails by name.

    The step region's give-ups all lead to `compute`, not to `END`: that is
    the one structural difference from chat, and it is what makes a failed
    sub-question evidence rather than a failed analysis.
    """
    assert _edges(DEEP_GRAPH) == {
        (START, "route"),
        ("route", "plan"), ("route", END),
        ("plan", "step"), ("plan", "synthesize"), ("plan", END),
        ("step", "scope"), ("step", "synthesize"), ("step", END),
        # A tool outside the closed set: the step closes without a query.
        ("step", "step"),
        ("scope", "retrieve"), ("scope", "compute"),
        ("retrieve", "generate"), ("retrieve", "compute"),
        ("generate", "validate"), ("generate", "compute"),
        ("validate", "execute"), ("validate", "generate"), ("validate", "compute"),
        ("execute", "inspect"), ("execute", "generate"), ("execute", "compute"),
        ("inspect", "compute"), ("inspect", "generate"),
        ("compute", "step"), ("compute", "synthesize"), ("compute", END),
        ("synthesize", "chart"), ("synthesize", END),
        ("chart", END),
    }


def test_the_repair_region_is_reused_not_reimplemented() -> None:
    """`generate ⇄ validate` is the same pair of edges in all three graphs."""
    for graph in (CHAT_GRAPH, DRAFT_GRAPH, DEEP_GRAPH):
        assert ("generate", "validate") in _edges(graph)
        assert ("validate", "generate") in _edges(graph)


def test_no_deep_node_can_reach_present_describe_clarify_or_match() -> None:
    nodes = {n for edge in _edges(DEEP_GRAPH) for n in edge}
    assert nodes.isdisjoint({"present", "describe", "clarify", "match"})


# ── a run, end to end ────────────────────────────────────────────────────
async def test_a_two_step_plan_runs_both_steps_on_the_chat_road() -> None:
    gateway = DeepGateway(
        plan=plan_of("What was total revenue?", "Revenue by status?"),
        sql=[SQL_TOTAL, SQL_BY_STATUS],
    )
    connector = DeepConnector([
        result(REVENUE, [[1700.0]]),
        result(BY_STATUS, [["shipped", 1000.0], ["pending", 500.0],
                           ["cancelled", 200.0]]),
    ])
    state, recorder = await drive(deep_state(), gateway, connector)

    road = ["step", "scope", "retrieve", "generate", "validate", "execute",
            "inspect", "compute"]
    assert recorder.names() == ["route", "plan", *road, *road, "synthesize", "chart"]
    assert state.error is None
    assert [e.status for e in state.evidence] == ["DONE", "DONE"]
    # Run-global, never reset per step (§2.4): the constraint on
    # `generated_queries` is `(run_id, attempt_no)`, and it holds.
    assert [a.attempt_no for a in state.attempts] == [1, 2]
    assert [(e.first_attempt, e.last_attempt) for e in state.evidence] == [(0, 1), (1, 2)]
    # The reader's question comes back for the answer and the chart.
    assert state.question == "Why did revenue drop in March?"
    assert state.execution is not None and state.execution.row_count == 3
    assert state.stop_reason == ""
    # Every planned step ran, so the writer's prose is the whole answer — no
    # preface saying it stands on part of the plan.
    assert state.answer == "The answer."
    assert state.synthesis is not None


async def test_each_step_is_asked_its_own_question() -> None:
    """`generate` sees the sub-question, never the one before it."""
    gateway = DeepGateway(
        plan=plan_of("What was total revenue?", "Revenue by status?"),
        sql=[SQL_TOTAL, SQL_BY_STATUS],
    )
    connector = DeepConnector([result(REVENUE, [[1.0]]), result(BY_STATUS, [["a", 1.0]])])
    await drive(deep_state(), gateway, connector)

    asked = [m[-1].content for name, m in gateway.messages if name == "SqlProposal"]
    assert "What was total revenue?" in asked[0]
    assert "Revenue by status?" in asked[1]
    assert "total revenue" not in asked[1]


async def test_a_failed_step_is_evidence_and_the_analysis_goes_on() -> None:
    """The guard refuses step one twice; step two still runs, the run succeeds."""
    gateway = DeepGateway(
        plan=plan_of("Something the guard refuses", "Revenue by status?"),
        sql=[SQL_FORBIDDEN, SQL_FORBIDDEN, SQL_BY_STATUS],
    )
    connector = DeepConnector([result(BY_STATUS, [["shipped", 1.0]])])
    state, recorder = await drive(deep_state(max_repairs=1), gateway, connector)

    assert state.error is None
    assert [e.status for e in state.evidence] == ["FAILED", "DONE"]
    assert state.evidence[0].note  # says why, in words
    assert state.evidence[0].execution is None
    # Step one's two statements are both on record, as rows would be.
    assert [a.report.status for a in state.attempts[:2]] == ["REJECTED", "REJECTED"]
    assert connector.executed == [state.attempts[2].rewritten_sql]


async def test_each_step_gets_its_own_repair_allowance() -> None:
    """`repair_count` is per step: step two's first draft is not a repair.

    With the chat definition (`len(attempts) - 1`), step one's repair would
    use up the run's single allowance and step two could never be repaired.
    """
    gateway = DeepGateway(
        plan=plan_of("Total revenue?", "Revenue by status?"),
        sql=[SQL_FORBIDDEN, SQL_TOTAL, SQL_FORBIDDEN, SQL_BY_STATUS],
    )
    connector = DeepConnector([result(REVENUE, [[1.0]]), result(BY_STATUS, [["a", 1.0]])])
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector)

    assert [e.status for e in state.evidence] == ["DONE", "DONE"]
    assert [e.repairs for e in state.evidence] == [1, 1]


async def test_a_plan_that_finishes_at_the_ceiling_was_not_cut() -> None:
    gateway = DeepGateway(plan=plan_of("A?", "B?"), sql=[SQL_TOTAL, SQL_TOTAL])
    connector = DeepConnector(lambda _sql: result(REVENUE, [[1.0]]))
    state, _ = await drive(deep_state(budget=_budget(max_steps=2)), gateway, connector)
    assert len(state.evidence) == 2
    assert state.stop_reason == ""


async def test_small_talk_halts_before_anything_is_planned() -> None:
    gateway = DeepGateway(route="CHITCHAT")
    state, recorder = await drive(deep_state(), gateway, DeepConnector())
    assert recorder.names() == ["route"]
    assert state.plan is None and state.answer


# ── every bound, crossed on purpose ──────────────────────────────────────
async def test_the_query_bound_holds_when_every_statement_is_refused() -> None:
    """A generator that only ever writes refused SQL, and a repair allowance
    large enough that only the budget can stop it."""
    gateway = DeepGateway(plan=plan_of(*[f"Q{i}?" for i in range(5)]),
                          sql=lambda _m: SQL_FORBIDDEN)
    state, recorder = await drive(
        deep_state(max_repairs=50, budget=_budget(max_queries=4)), gateway, DeepConnector()
    )
    assert len(state.attempts) == 4
    assert state.stop_reason == "queries"
    assert state.error is None
    assert recorder.names()[-2:] == ["synthesize", "chart"]


async def test_the_worst_case_path_fits_the_recursion_limit() -> None:
    """Every statement runs and is refused by the *database*, so each one
    walks generate → validate → execute; the most node executions the budget
    allows must end in `synthesize`, never in `E_PIPELINE_LOOP`."""
    budget = _budget(max_steps=5, max_queries=12)
    gateway = DeepGateway(plan=plan_of(*[f"Q{i}?" for i in range(5)]),
                          sql=lambda _m: SQL_TOTAL)

    def refuse(_sql: str) -> object:
        raise ConnectorError("relation is locked")

    state, recorder = await drive(
        deep_state(max_repairs=50, budget=budget), gateway, DeepConnector(refuse)  # type: ignore[arg-type]
    )
    assert state.error is None, state.error
    assert len(state.attempts) == 12
    assert len(recorder.settled()) < deep_recursion_limit(state)
    assert recorder.names()[-2:] == ["synthesize", "chart"]


async def test_the_step_bound_holds_whatever_the_plan_says() -> None:
    gateway = DeepGateway(plan=plan_of(*[f"Q{i}?" for i in range(9)]),
                          sql=lambda _m: SQL_TOTAL)
    connector = DeepConnector(lambda _sql: result(REVENUE, [[1.0]]))
    state, _ = await drive(deep_state(budget=_budget(max_steps=3)), gateway, connector)
    assert len(state.evidence) == 3


async def test_the_row_bound_narrows_each_query_to_what_is_left() -> None:
    """Refused *before* it is spent: step two may fetch only the rows left."""
    gateway = DeepGateway(plan=plan_of("A?", "B?", "C?"), sql=lambda _m: SQL_BY_STATUS)
    three = result(BY_STATUS, [["a", 1.0], ["b", 2.0], ["c", 3.0]])
    two = result(BY_STATUS, [["a", 1.0], ["b", 2.0]])
    connector = DeepConnector([three, two])
    state, _ = await drive(
        deep_state(max_rows=1000, budget=_budget(max_rows_total=5)), gateway, connector
    )
    assert connector.max_rows == [5, 2]
    assert state.rows_spent == 5
    assert state.stop_reason == "rows"
    assert len(state.evidence) == 2


async def test_the_token_bound_stops_before_the_next_step() -> None:
    """route + plan = 200, step one's statement = 300 ≥ 250: no step two."""
    gateway = DeepGateway(plan=plan_of("A?", "B?"), sql=lambda _m: SQL_TOTAL,
                          prompt_tokens=100)
    connector = DeepConnector(lambda _sql: result(REVENUE, [[1.0]]))
    state, _ = await drive(
        deep_state(budget=_budget(max_prompt_tokens=250)), gateway, connector
    )
    assert len(state.evidence) == 1
    assert state.stop_reason == "tokens"


async def test_the_soft_deadline_writes_an_answer_from_nothing() -> None:
    """Out of time after planning: no step, a normal ending, an answer."""
    budget = DeepBudget(deadline_at=utcnow() - timedelta(seconds=1))
    gateway = DeepGateway(plan=plan_of("A?"))
    state, recorder = await drive(deep_state(budget=budget), gateway, DeepConnector())
    assert recorder.names() == ["route", "plan", "synthesize", "chart"]
    assert state.stop_reason == "time"
    assert state.error is None
    assert state.answer and "ran out of time" in state.answer


async def test_the_hard_deadline_is_still_a_timeout() -> None:
    """The run's own deadline is not the budget's: past it, `_adapt` raises
    before the next node exactly as it does in chat."""
    state = deep_state(deadline=utcnow() - timedelta(seconds=1))
    with pytest.raises(RunTimeoutError):
        await drive(state, DeepGateway(plan=plan_of("A?")), DeepConnector())


async def test_a_node_crash_still_ends_the_run() -> None:
    """A crash is a bug, not a finding: it is not turned into evidence."""
    gateway = DeepGateway(plan=plan_of("A?", "B?"), sql=lambda _m: SQL_TOTAL)

    def crash(_sql: str) -> object:
        raise RuntimeError("driver bug")

    state, recorder = await drive(deep_state(), gateway, DeepConnector(crash))  # type: ignore[arg-type]
    assert state.error is not None and state.error.code == "E_NODE_FAILED"
    assert recorder.settled()[-1] == ("execute", "FAILED")


# ── inert: nothing in the product can start one ──────────────────────────
def test_deep_is_off_by_default() -> None:
    assert Settings.model_fields["deep_enabled"].default is False


def test_nothing_outside_the_pipeline_reaches_the_deep_graph() -> None:
    """Phase 4's second half: the graph exists and nothing can run it.

    Read from the import graph rather than trusted. When Phase 6 wires the
    surface, this test is what has to change, and that change is the moment
    the mode becomes reachable.
    """
    root = Path(__file__).resolve().parents[2] / "app"
    names = {"DeepPipeline", "DEEP_GRAPH", "DeepState"}
    reached: list[str] = []
    for path in root.rglob("*.py"):
        if path.is_relative_to(root / "pipeline"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith("app.pipeline")
                and names & {alias.name for alias in node.names}
            ):
                reached.append(str(path.relative_to(root)))
    assert reached == []
