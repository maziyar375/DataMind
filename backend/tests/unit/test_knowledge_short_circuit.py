"""The `match` node: the first thing in this product that changes an answer.

And it changes one **without changing a byte of the prompt**. That is the whole
shape of Phase 2, and most of this file is about the four ways the node
declines — because the declines are what make the one acceptance safe.

The hit path is deliberately unremarkable: it fills `state.attempts` with the
bound statement and hands over to `validate`, which is the guard's own entry
point for the pipeline and already feeds `execute`. So a stored template reuses
every guarantee the generated path has — re-validation against the *current*
snapshot, the rewriter, the row cap — and gets **no exemption**.

The declines:

* no matcher, or templates disabled → SKIPPED, nothing read, nothing logged;
* nothing close enough → a miss, and the run is indistinguishable from before;
* a parameter would not bind → `REJECTED_UNBOUND`, logged;
* the SQL no longer passes the guard → `REJECTED_STALE`, logged, **the run does
  not fail and the row is not deleted**.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.knowledge import (
    KnowledgeTemplate,
    ParamType,
    TemplateParam,
    normalize_question,
    policy_from_tables,
)
from app.knowledge.matcher import Candidate
from app.pipeline.nodes import NodeDeps, match
from app.pipeline.state import RunState

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "created_at", "data_type": "date"},
            {"name": "region", "data_type": "text"},
            {"name": "amount", "data_type": "numeric"},
        ],
    }
]
POLICY = policy_from_tables(TABLES, dialect="postgres", max_rows=1000)

QUESTION = "revenue for {region} since {from_date}"
SQL = (
    "SELECT SUM(amount) FROM orders "
    "WHERE region = :region AND created_at >= :from_date"
)


def template(*, sql: str = SQL, question: str = QUESTION) -> KnowledgeTemplate:
    return KnowledgeTemplate(
        id=uuid4(),
        question=question,
        question_normalized=normalize_question(question),
        sql=sql,
        params=[
            TemplateParam(
                name="region", type=ParamType.STRING, comment="one of: EMEA, NA, APAC"
            ),
            TemplateParam(name="from_date", type=ParamType.DATE),
        ],
    )


class FakeMatcher:
    def __init__(self, *candidates: Candidate, fail: bool = False) -> None:
        self.candidates = list(candidates)
        self.fail = fail
        self.asked: list[str] = []

    async def match(self, question: str, _connection_id, *, limit: int = 5) -> Any:
        self.asked.append(question)
        if self.fail:
            raise RuntimeError("the store is on fire")
        return self.candidates


def state(question: str = "revenue for EMEA since 2026-01-01") -> RunState:
    run = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    run.intent = "ANALYTICAL"
    return run


def deps(matcher: Any = None, *, enabled: bool = True) -> NodeDeps:
    async def emit(_type: str, _data: dict) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None, snapshot={"tables": TABLES},
        history=[], policy=POLICY, emit=emit,
        matcher=matcher, templates_enabled=enabled,
    )


# ── the hit ──────────────────────────────────────────────────────────────
async def test_a_hit_hands_the_bound_statement_to_the_guard() -> None:
    run, hit = state(), template()
    result = await match(run, deps(FakeMatcher(Candidate(hit, 0.95))))

    # `validate` by name: the guard's own entry point, which already feeds
    # `execute`. Nothing about executing a stored template is new code.
    assert result.goto == "validate"
    assert len(run.attempts) == 1
    assert ":region" not in run.attempts[0].raw_sql
    assert "'EMEA'" in run.attempts[0].raw_sql
    assert "'2026-01-01'" in run.attempts[0].raw_sql


async def test_a_hit_records_everything_the_badge_needs() -> None:
    # The matched question and the bindings are the reader's only defence
    # against a confident wrong match — *did it think July or June?*
    run = state()
    await match(run, deps(FakeMatcher(Candidate(template(), 0.95))))

    assert run.match_outcome == "SHORT_CIRCUIT"
    assert run.matched_question == QUESTION
    assert run.bound_params == {"region": "EMEA", "from_date": "2026-01-01"}
    assert run.match_score == 0.95
    assert run.match_kind == "LEXICAL"


async def test_a_hit_emits_the_same_event_a_generated_statement_emits() -> None:
    # The SPA draws the SQL disclosure from `SQL_GENERATED`, and an answer
    # whose statement never appeared would be the first place this product
    # stopped showing its work.
    seen: list[tuple[str, dict]] = []

    async def emit(event_type: str, data: dict) -> None:
        seen.append((event_type, data))

    node_deps = deps(FakeMatcher(Candidate(template(), 0.95)))
    object.__setattr__(node_deps, "emit", emit)
    await match(state(), node_deps)

    assert [t for t, _ in seen] == ["SQL_GENERATED"]
    assert "'EMEA'" in seen[0][1]["sql"]


# ── the miss ─────────────────────────────────────────────────────────────
async def test_a_near_miss_is_not_a_hit() -> None:
    run = state()
    result = await match(run, deps(FakeMatcher(Candidate(template(), 0.80))))

    assert result.goto is None                  # falls through to `retrieve`
    assert result.status == "OK"
    assert run.attempts == []
    assert run.match_outcome == ""              # nothing to log: no verdict


async def test_a_miss_leaves_the_state_the_generator_reads_untouched() -> None:
    """The promise `PROMPT_VERSION` stays at v8 on.

    A miss must write nothing the generator can see — no examples, no note, no
    hint that a store was consulted. The score is recorded because the trace
    line shows it, and the generator never reads it.
    """
    run, before = state(), state()
    await match(run, deps(FakeMatcher(Candidate(template(), 0.80))))

    assert run.question == before.question
    assert run.attempts == before.attempts == []
    assert run.context is before.context is None
    assert run.matched_template_id is None and run.matched_question == ""


async def test_an_empty_store_is_a_miss_and_costs_nothing() -> None:
    run = state()
    assert (await match(run, deps(FakeMatcher()))).goto is None
    assert run.match_outcome == ""


# ── the four declines ────────────────────────────────────────────────────
async def test_no_matcher_is_the_pre_feature_path_exactly() -> None:
    run = state()
    result = await match(run, deps(None))
    assert result.status == "SKIPPED"
    assert run.match_outcome == "" and run.attempts == []


async def test_a_reader_who_asked_for_a_fresh_answer_is_not_overruled() -> None:
    # *Generate a fresh answer instead* is the one control that makes a
    # Verified badge safe to show. Consulting the store again would ignore it.
    matcher = FakeMatcher(Candidate(template(), 0.99))
    result = await match(state(), deps(matcher, enabled=False))

    assert result.status == "SKIPPED"
    assert matcher.asked == []      # not even asked


@pytest.mark.parametrize("intent", ["METADATA", "CHITCHAT", "UNSUPPORTED"])
async def test_only_an_analytical_question_consults_the_store(intent: str) -> None:
    run = state()
    run.intent = intent
    matcher = FakeMatcher(Candidate(template(), 0.99))

    assert (await match(run, deps(matcher))).status == "SKIPPED"
    assert matcher.asked == []


async def test_an_unbound_parameter_cancels_the_hit_and_says_so() -> None:
    """`REJECTED_UNBOUND` — the log that tells us what to teach the binder.

    A half-bound template is a confident wrong answer. Falling through costs
    exactly today's behaviour, and the log line is how the next date phrasing
    gets added.
    """
    run = state("revenue for EMEA")       # no date anywhere in the question
    result = await match(run, deps(FakeMatcher(Candidate(template(), 0.95))))

    assert result.goto is None
    assert run.attempts == []
    assert run.match_outcome == "REJECTED_UNBOUND"
    assert run.matched_template_id is not None      # so the log names the row
    assert "from_date" in (result.detail or "")


async def test_a_stale_template_falls_through_rather_than_failing_the_run() -> None:
    """*Fail as a value* — the fifth posture, applied.

    The schema moved underneath a template that was legal when it was written.
    The run does not fail, the row is not deleted, and the question is answered
    the ordinary way. Phase 4's worker is what marks the row `STALE`; this node
    only refuses to use it.
    """
    gone = template(sql="SELECT SUM(amount) FROM orders WHERE gone = :region "
                        "AND created_at >= :from_date")
    run = state()
    result = await match(run, deps(FakeMatcher(Candidate(gone, 0.95))))

    assert result.status == "OK" and result.goto is None
    assert run.error is None
    assert run.attempts == []
    assert run.match_outcome == "REJECTED_STALE"
    assert run.matched_template_id == gone.id


async def test_a_template_naming_a_forbidden_table_never_executes() -> None:
    # The fifth entry point, on the read path this time. A template stored
    # before an allowlist tightened does not get to run because it once passed.
    hostile = template(sql="SELECT usename FROM pg_shadow WHERE a = :region "
                           "AND b >= :from_date")
    run = state()
    result = await match(run, deps(FakeMatcher(Candidate(hostile, 0.99))))

    assert result.goto is None and run.attempts == []
    assert run.match_outcome == "REJECTED_STALE"


async def test_a_matcher_that_raises_never_fails_a_run() -> None:
    """The store is an accelerator, not a dependency.

    Every question it can answer is answerable without it, so a broken store
    costs latency and nothing else.
    """
    run = state()
    result = await match(run, deps(FakeMatcher(fail=True)))

    assert result.status == "SKIPPED"
    assert run.error is None and run.attempts == []
