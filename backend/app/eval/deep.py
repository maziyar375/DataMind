"""One `deep_v1` question through the real deep pipeline, and what it did.

`--suite deep_v1 --mode deep` (docs/plans/deep-analysis-mode.md Phase 7). The
same posture as `runner.evaluate_record`: the real graph, the real guard, the
real fixture database and a real provider — nothing here is a fake — and what
comes back is a `DeepOutcome`, every figure of which is read off the run's own
state. `metrics.deep_scorecard` turns a list of them into the scorecard.

There is no gold to compare with, by design: see `DeepRecord`.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from time import perf_counter
from typing import Any

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import RunTimeoutError
from app.domain.ports.database import DatabaseConnector
from app.domain.ports.llm import LLMGateway, ResolvedLLM
from app.domain.value_objects import DeepBudget
from app.eval.dataset import DeepRecord
from app.eval.metrics import (
    DEEP_ANSWERED,
    DEEP_ERROR,
    DEEP_PLAN_FAILED,
    DeepOutcome,
)
from app.pipeline.graph import DeepPipeline
from app.pipeline.nodes import NodeDeps
from app.pipeline.state import DeepState
from app.sqlguard import GuardPolicy

#: The chat eval's repair allowance, per step — so a deep sub-question is
#: repaired exactly as often as a golden question is.
_MAX_REPAIRS = 1


async def evaluate_deep(
    record: DeepRecord,
    *,
    gateway: LLMGateway,
    llm: ResolvedLLM,
    connector: DatabaseConnector,
    snapshot: dict[str, Any],
    policy: GuardPolicy,
    settings: Settings,
    model_name: str,
    semantic: dict[str, Any] | None = None,
    include_db_comments: bool = False,
) -> DeepOutcome:
    """Run one question in Deep and measure it. Never raises.

    The disclosure policy is SAMPLE, the eval's own: the fixture is synthetic
    data the harness owns. The budget is `DeepBudget`'s shipped defaults and
    the deadlines are the product's — `deep_deadline_seconds` hard, a fifth
    earlier soft — so a figure here is a figure about the mode as it ships.
    """
    o = DeepOutcome(
        record_id=record.id, tags=list(record.tags), difficulty=record.difficulty,
        model=model_name, min_steps=record.min_steps,
        expected_tables=list(record.expected_tables),
    )

    async def on_step(*_: Any) -> None:
        return None

    async def emit(_type: str, _data: dict[str, Any]) -> None:
        return None

    now = utcnow()
    hard = settings.deep_deadline_seconds
    state = DeepState(
        run_id=uuid.uuid4(), conversation_id=uuid.uuid4(), owner_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), question=record.question, asked=record.question,
        dialect=connector.dialect, max_rows=settings.default_max_rows,
        base_max_rows=settings.default_max_rows, max_repairs=_MAX_REPAIRS,
        statement_timeout_ms=settings.default_statement_timeout_ms,
        disclosure_policy="SAMPLE",
        deadline_at=now + timedelta(seconds=hard),
        budget=DeepBudget(deadline_at=now + timedelta(seconds=hard * 0.8)),
    )
    deps = NodeDeps(
        llm_gateway=gateway, llm=llm, connector=connector, snapshot=snapshot,
        history=[], policy=policy, emit=emit,
        include_db_comments=include_db_comments, semantic=semantic,
    )

    t0 = perf_counter()
    try:
        state = await DeepPipeline(on_step=on_step).run(state, deps)
    except RunTimeoutError:
        o.failure_reason = "run exceeded its hard deadline"
    except Exception as err:  # noqa: BLE001 - a harness must survive one bad question
        o.failure_reason = f"pipeline crashed: {str(err)[:300]}"
    o.total_ms = int((perf_counter() - t0) * 1000)
    measure(o, state)
    return o


def measure(o: DeepOutcome, state: DeepState) -> None:
    """Every figure the scorecard reads, off the run's state. Pure.

    Separate from `evaluate_deep` so the arithmetic is tested against a
    constructed state rather than against a provider.
    """
    o.prompt_tokens = state.prompt_tokens
    o.completion_tokens = state.completion_tokens
    o.cache_read_tokens = state.cache_read_tokens
    o.llm_ms = state.llm_latency_ms
    o.db_ms = state.db_latency_ms

    o.statements = len(state.attempts)
    o.statements_valid = sum(1 for a in state.attempts if a.report.status == "VALID")
    o.statements_ran = sum(
        1 for a in state.attempts if a.report.status == "VALID" and a.db_error is None
    )
    o.policy_violations = sorted({
        i.rule_id
        for a in state.attempts if a.report.status == "REJECTED"
        for i in a.report.errors
    })
    o.tables_reached = sorted({
        t for a in state.attempts if a.report.status == "VALID"
        for t in a.report.referenced_tables
    })

    if state.plan is not None:
        o.restatement = state.plan.restatement
        o.steps_declared = len(state.plan.steps)
    o.steps_done = sum(1 for e in state.evidence if e.status == "DONE")
    o.steps_failed = sum(1 for e in state.evidence if e.status == "FAILED")
    o.steps_skipped = sum(1 for e in state.evidence if e.status == "SKIPPED")
    o.revisions = len(state.revisions)
    o.stop_reason = state.stop_reason
    o.answer = state.answer or ""

    synthesis = state.synthesis or {}
    claims = synthesis.get("claims", [])
    o.claims = len(claims)
    stating = [c for c in claims if c.get("figures")]
    o.claims_stating = len(stating)
    o.claims_traced = sum(
        1 for c in stating if c.get("cites") is not None and not c.get("unsupported")
    )
    o.claims_uncited = int(synthesis.get("uncited", 0))

    if o.failure_reason is not None:
        o.outcome = DEEP_ERROR
    elif state.plan is None:
        o.outcome = DEEP_PLAN_FAILED
        o.failure_reason = state.error.message if state.error else "no plan"
    elif state.error is not None:
        o.outcome = DEEP_ERROR
        o.failure_reason = state.error.hint or state.error.message
    else:
        o.outcome = DEEP_ANSWERED
