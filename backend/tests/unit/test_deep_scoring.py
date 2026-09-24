"""Scoring a deep answer, without an LLM judge.

docs/plans/deep-analysis-mode.md Phase 7: *`--suite deep_v1 --mode deep`
produces a scorecard with five numbers on it, and the interruption rate is a
query somebody can run.* The five are read off a run's own state
(`app.eval.deep.measure`) and pooled (`metrics.deep_scorecard`); answer
correctness is reported as not scored, and a test holds it there. The deep run
measured here is a scripted one (`deep_world.py`) — the real one is the
eval runner's, against the fixture, with a provider.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.domain.value_objects import RunStatus
from app.eval import dataset, metrics
from app.eval.deep import measure
from app.eval.metrics import DeepOutcome, deep_scorecard, format_deep_scorecard
from app.eval.runner import build_parser, deep_arm_refusal
from app.infra.db.models import Conversation, Message, Run
from app.services.deep_plan import interruption
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
    plan_of,
    result,
)


# ── one run, measured ────────────────────────────────────────────────────
async def test_a_run_is_measured_off_its_own_state() -> None:
    """Three steps: one refused twice, two answered — one of which cites a
    figure its result does not hold."""
    gateway = DeepGateway(
        plan=plan_of("Refused?", "Total?", "By status?"),
        sql=[SQL_FORBIDDEN, SQL_FORBIDDEN, SQL_TOTAL, SQL_BY_STATUS],
        prose=("Revenue was 1,700 [2]. ", "Shipped was 9,999 [3]. ", "Nothing else moved."),
    )
    connector = DeepConnector([
        result(REVENUE, [[1700.0]]),
        result(BY_STATUS, [["shipped", 1000.0], ["pending", 700.0]]),
    ])
    state, _ = await drive(deep_state(max_repairs=1), gateway, connector)

    o = DeepOutcome(record_id="deep-x", tags=[], difficulty="medium", min_steps=2)
    measure(o, state)

    assert o.outcome == metrics.DEEP_ANSWERED
    assert (o.statements, o.statements_valid, o.statements_ran) == (4, 2, 2)
    assert o.policy_violations == ["E_TABLE_NOT_ALLOWED"]
    assert (o.steps_declared, o.steps_done, o.steps_failed) == (3, 2, 1)
    # Two sentences state a figure; one resolves to its cited step, one does
    # not; the third states nothing and cites nothing.
    assert (o.claims, o.claims_stating, o.claims_traced, o.claims_uncited) == (3, 2, 1, 1)
    assert o.tables_reached == ["public.orders"]
    assert o.prompt_tokens > 0


async def test_a_run_with_no_plan_is_a_plan_failure_not_an_answer() -> None:
    from app.core.errors import LLMError

    state, _ = await drive(deep_state(), DeepGateway(plan=LLMError("down")), DeepConnector())
    o = DeepOutcome(record_id="deep-y", tags=[], difficulty="easy")
    measure(o, state)
    assert o.outcome == metrics.DEEP_PLAN_FAILED
    assert o.failure_reason


# ── the scorecard ────────────────────────────────────────────────────────
def _outcome(**kw: object) -> DeepOutcome:
    base = {"record_id": f"r{uuid4().hex[:4]}", "tags": [], "difficulty": "easy",
            "outcome": metrics.DEEP_ANSWERED}
    return DeepOutcome(**{**base, **kw})  # type: ignore[arg-type]


def test_rates_are_pooled_over_statements_and_claims_not_averaged_per_answer() -> None:
    """One answer with ten statements, one with one: a per-answer mean would
    weigh the one-statement answer as much as the ten."""
    card = deep_scorecard([
        _outcome(statements=10, statements_valid=9, statements_ran=9,
                 claims_stating=8, claims_traced=8, steps_declared=5, steps_done=5),
        _outcome(statements=1, statements_valid=0, statements_ran=0,
                 claims_stating=2, claims_traced=0, steps_declared=3, steps_done=0,
                 steps_failed=1, stop_reason="queries"),
    ])
    assert card["guard_pass_rate"] == round(9 / 11, 4)
    assert card["execution_success_rate"] == 1.0
    assert card["claim_traceability"] == round(8 / 10, 4)
    assert card["plan_adherence"] == round(5 / 8, 4)
    assert card["plan_reached"] == round(6 / 8, 4)
    assert card["stopped_early"] == {"queries": 1}


def test_answer_correctness_is_reported_as_not_scored() -> None:
    """The sixth metric needs a provider, and this scorecard uses none. It is
    stated as missing — never estimated, never defaulted to a number."""
    card = deep_scorecard([_outcome()])
    assert card["answer_correctness"] is None
    assert "not scored" in card["answer_correctness_note"]
    assert "answer correctness" in format_deep_scorecard(card)


def test_nothing_measured_is_none_not_zero() -> None:
    card = deep_scorecard([_outcome(outcome=metrics.DEEP_PLAN_FAILED)])
    assert card["answered"] == 0
    assert card["claim_traceability"] is None
    assert card["plan_adherence"] is None


def test_the_printed_card_carries_all_five_numbers() -> None:
    card = deep_scorecard([_outcome(statements=2, statements_valid=2, statements_ran=2,
                                    claims_stating=1, claims_traced=1,
                                    steps_declared=2, steps_done=2, total_ms=4000)])
    text = format_deep_scorecard(card)
    for label in ("guard pass rate", "execution success", "claim traceability",
                  "plan adherence", "queries / answer", "prompt tokens", "wall clock"):
        assert label in text


# ── the runner ───────────────────────────────────────────────────────────
def test_the_deep_suite_loads_frozen() -> None:
    suite = dataset.load_deep_suite("deep_v1")
    assert len(suite.records) == 20 and suite.frozen_on == "2026-09-22"
    assert dataset.is_deep_suite("deep_v1") and not dataset.is_deep_suite("sales_v1")


@pytest.mark.parametrize("argv,refused", [
    (["--suite", "deep_v1", "--mode", "deep"], False),
    (["--suite", "deep_v1", "--mode", "deep", "--semantic", "on", "--comments"], False),
    (["--suite", "deep_v1"], True),
    (["--suite", "sales_v1", "--mode", "deep"], True),
    (["--suite", "deep_v1", "--mode", "deep", "--templates", "on"], True),
    (["--suite", "deep_v1", "--mode", "deep", "--retrieve-budget", "8000"], True),
])
def test_the_runner_refuses_a_suite_and_mode_that_disagree(
    argv: list[str], refused: bool
) -> None:
    args: argparse.Namespace = build_parser().parse_args(argv)
    assert (deep_arm_refusal(args) is not None) is refused


def test_quick_is_the_default_mode() -> None:
    assert build_parser().parse_args(["--suite", "sales_v1"]).mode == "quick"


# ── the interruption rate ────────────────────────────────────────────────
def _deep_run(db: AsyncSessionShim, thread: Conversation, connection_id: object, *,
              status: str, answer_now: bool = False, depth: str = "DEEP") -> None:
    s = db._session
    question = Message(id=uuid4(), conversation_id=thread.id, seq=uuid4().int % 10_000,
                       role="USER", content="why?")
    s.add(question)
    s.flush()
    s.add(Run(id=uuid4(), conversation_id=thread.id, user_message_id=question.id,
              owner_id=ACTOR, actor_id=ACTOR, connection_id=connection_id,
              status=status, depth=depth, answer_now_requested=answer_now,
              model_snapshot={}, prompt_version="v12", created_at=utcnow()))
    s.flush()


async def test_the_interruption_rate_counts_what_readers_did(db: AsyncSessionShim) -> None:
    connection = _connection(db._session, owner_id=ACTOR)
    thread = Conversation(id=uuid4(), owner_id=ACTOR, title="t", status="ACTIVE",
                          default_connection_id=connection.id)
    db._session.add(thread)
    db._session.flush()
    for status, answer_now in [
        (RunStatus.SUCCEEDED, False), (RunStatus.SUCCEEDED, False),
        (RunStatus.SUCCEEDED, False), (RunStatus.SUCCEEDED, True),
        (RunStatus.CANCELLED, False), (RunStatus.FAILED, False),
        (RunStatus.RUNNING, False),
    ]:
        _deep_run(db, thread, connection.id, status=status, answer_now=answer_now)
    # A quick run is never a deep interruption, whatever happened to it.
    _deep_run(db, thread, connection.id, status=RunStatus.CANCELLED, depth="QUICK")

    counted = await interruption(db)  # type: ignore[arg-type]
    assert counted == {
        "read_to_end": 3, "answer_now": 1, "cancelled": 1, "failed": 1,
        # Failures are the product's, not the reader's: out of the denominator.
        "interruption_rate": 2 / 5,
    }
    later = await interruption(db, since=utcnow() + timedelta(days=1))  # type: ignore[arg-type]
    assert later["interruption_rate"] is None
