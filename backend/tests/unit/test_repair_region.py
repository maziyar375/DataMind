"""One repair loop, two callers.

Phase 2 of [docs/langgraph-migration.md](../../../docs/langgraph-migration.md).
Before it there were **two executors over one node set**: `AnalyticsPipeline`
and a hand-rolled `for` loop in `sql_draft_service.draft_sql`. The cost of that
is not hypothetical — it had already been paid twice:

* `RunState.deadline_at` was enforced on the chat path and **inert** on the
  draft path, until someone noticed and closed the gap by hand, in its own
  commit;
* the two disagreed about repair ceilings, event sinks and step persistence in
  ways nobody could see from either file alone.

So this file tests the thing that fix has to buy: for the same question, the
same connection and the same scripted model, **chat and a draft produce the
same SQL from a byte-identical prompt**, and the differences that remain are
the ones someone wrote down.

The other half — that a draft still refuses an out-of-scope question, still
repairs exactly once, and still comes back with a report rather than an
exception — is `tests/unit/test_sql_drafts.py`, which did not change.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import LLMError, QuestionOutOfScopeError
from app.domain.ports.database import QueryResult, ResultColumn
from app.domain.value_objects import DisclosurePolicy
from app.pipeline.graph import draft_statement
from app.pipeline.nodes import NodeDeps
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.state import RunState
from app.services.sql_draft_service import _OUT_OF_SCOPE, _deadline_gate
from app.sqlguard import GuardPolicy
from tests.unit.test_pipeline_events import (
    POLICY,
    SNAPSHOT,
    SQL_FORBIDDEN,
    SQL_TOTAL,
    Recorder,
    ScriptedConnector,
    ScriptedGateway,
    rows,
)

QUESTION = "What was total revenue?"

ONE_ROW = QueryResult(
    columns=[ResultColumn(name="revenue", db_type="numeric",
                          semantic_type="quantitative")],
    rows=[[1_240_000.0]], row_count=1, duration_ms=7,
)


class PromptGateway(ScriptedGateway):
    """A `ScriptedGateway` that keeps every prompt it was sent."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sql_prompts: list[str] = []

    async def structured(self, llm: Any, messages: Any, schema: Any) -> Any:
        if schema.__name__ == "SqlProposal":
            self.sql_prompts.append(list(messages)[0].content)
        return await super().structured(llm, messages, schema)


def _state(**overrides: Any) -> RunState:
    """One state shape for both callers, so the comparison isolates the loop."""
    fields: dict[str, Any] = {
        "run_id": uuid4(), "conversation_id": uuid4(), "owner_id": uuid4(),
        "connection_id": uuid4(), "question": QUESTION, "dialect": "postgres",
        "max_rows": 1000, "max_repairs": 1,
        "disclosure_policy": DisclosurePolicy.SAMPLE,
        "deadline_at": utcnow() + timedelta(seconds=120),
    }
    fields.update(overrides)
    return RunState(**fields)


def _deps(gateway: Any, connector: Any = None, **overrides: Any) -> NodeDeps:
    async def no_emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    fields: dict[str, Any] = {
        "llm_gateway": gateway, "llm": None, "connector": connector,
        "snapshot": SNAPSHOT, "history": [], "policy": POLICY, "emit": no_emit,
    }
    fields.update(overrides)
    return NodeDeps(**fields)


async def _chat(gateway: Any, *, results: list[Any] | None = None) -> RunState:
    state = _state()
    recorder = Recorder()
    deps = _deps(gateway, ScriptedConnector(results or [ONE_ROW]),
                 emit=recorder.emit)
    return await AnalyticsPipeline(on_step=recorder.on_step).run(state, deps)


async def _draft(gateway: Any, *, classify: bool = False, **state_kw: Any) -> RunState:
    state = _state(**state_kw)
    return await draft_statement(
        state, _deps(gateway), classify=classify,
        check_deadline=_deadline_gate, out_of_scope=_OUT_OF_SCOPE,
    )


# ── the property the merge exists to buy ─────────────────────────────────
@pytest.mark.asyncio
async def test_chat_and_a_draft_write_the_same_sql_from_the_same_prompt() -> None:
    """Same question, same connection, same scripted model — same statement.

    The prompt assertion is the stronger half. Two executors could produce the
    same SQL from a scripted gateway by accident; they can only produce the
    same *bytes* if they built the schema block, the history block and the
    extra-rules composition identically. That is non-negotiable #1 holding
    across both callers at once.
    """
    chat_gateway = PromptGateway(sql=[SQL_TOTAL])
    draft_gateway = PromptGateway(sql=[SQL_TOTAL])

    chat = await _chat(chat_gateway)
    draft = await _draft(draft_gateway)

    assert chat_gateway.sql_prompts == draft_gateway.sql_prompts
    assert chat.attempts[-1].raw_sql == draft.attempts[-1].raw_sql
    assert chat.attempts[-1].rewritten_sql == draft.attempts[-1].rewritten_sql
    assert draft.attempts[-1].report.status == "VALID"


@pytest.mark.asyncio
async def test_both_callers_repair_on_the_same_signal_and_the_same_budget() -> None:
    """A rejected statement is regenerated once, from `max_repairs` in state.

    The draft's `for _ in range(DRAFT_MAX_REPAIRS + 1)` is gone, and nothing
    replaced it: the ceiling was always `RunState.max_repairs`, because
    `validate` asks for a repair only while `repair_count < max_repairs`. The
    loop counted to the same number a second time, which is exactly the kind of
    duplicate that drifts.
    """
    chat_gateway = PromptGateway(sql=[SQL_FORBIDDEN, SQL_TOTAL])
    draft_gateway = PromptGateway(sql=[SQL_FORBIDDEN, SQL_TOTAL])

    chat = await _chat(chat_gateway)
    draft = await _draft(draft_gateway)

    assert len(chat.attempts) == len(draft.attempts) == 2
    # The repair prompt too, not just the first ask.
    assert chat_gateway.sql_prompts == draft_gateway.sql_prompts
    assert draft.attempts[-1].report.status == "VALID"


@pytest.mark.asyncio
async def test_a_draft_out_of_budget_returns_the_rejection_and_does_not_raise() -> None:
    """`give_up` means different things to the two callers, deliberately.

    Chat ends a failed run. A draft hands back the statement and its report,
    because the editor renders the guard's reasons inline — raising would make
    "the model wrote something I can show you and explain" indistinguishable
    from "your request was malformed".
    """
    draft = await _draft(PromptGateway(sql=[SQL_FORBIDDEN, SQL_FORBIDDEN]))

    assert len(draft.attempts) == 2
    assert draft.attempts[-1].report.status == "REJECTED"
    assert draft.error is not None and draft.error.code != "E_LLM"


# ── what the two callers deliberately do not share ───────────────────────
@pytest.mark.asyncio
async def test_a_draft_writes_no_steps_and_emits_no_events() -> None:
    """No `runs` row to attach a trail to, and no client listening.

    The chat run's `on_step` and `emit` are sinks in the config, not behaviour
    in the adapter, so a draft supplies no-ops rather than a second executor
    that never learned to call them.
    """
    recorder = Recorder()
    state = _state()
    await draft_statement(
        state, _deps(PromptGateway(sql=[SQL_TOTAL]), emit=recorder.emit),
        check_deadline=_deadline_gate, out_of_scope=_OUT_OF_SCOPE,
    )

    assert recorder.steps == []
    # `emit` is the caller's; a draft passes `_no_emit`, and this recorder
    # stands in for it to prove the adapter still calls what it is given.
    assert [t for t, _d in recorder.events] == [
        "STEP_STARTED", "STEP_FINISHED",      # retrieve
        "STEP_STARTED", "SQL_GENERATED", "STEP_FINISHED",
        "STEP_STARTED", "SQL_VALIDATED", "STEP_FINISHED",
    ]


@pytest.mark.asyncio
async def test_the_draft_deadline_stops_a_repair_and_not_a_validated_statement() -> None:
    """The draft's rule: before each `generate`, and nowhere else.

    A chat run is stopped before *every* node. A draft is not, and the
    difference is load-bearing rather than cosmetic: `validate` is the guard —
    pure CPU, microseconds — so stopping there would throw away a statement the
    model has already been paid for and the guard would have accepted.
    """
    # Already out of time, and the first `generate` has not run.
    with pytest.raises(LLMError) as raised:
        await _draft(
            PromptGateway(sql=[SQL_TOTAL]),
            deadline_at=utcnow() - timedelta(seconds=1),
        )
    assert "narrow the question" in raised.value.message


@pytest.mark.asyncio
async def test_a_spent_deadline_does_not_lose_a_statement_already_written() -> None:
    """The other side of the same rule, stated as a run.

    `retrieve` runs, `generate` runs inside its budget, and the clock passes
    the deadline while the provider is answering. `validate` must still run:
    the statement is written and paid for, and the guard costs nothing.
    """
    class SlowGateway(PromptGateway):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.state: RunState | None = None

        async def structured(self, llm: Any, messages: Any, schema: Any) -> Any:
            result = await super().structured(llm, messages, schema)
            if self.state is not None:      # the provider took too long
                self.state.deadline_at = utcnow() - timedelta(seconds=1)
            return result

    gateway = SlowGateway(sql=[SQL_TOTAL])
    state = _state()
    gateway.state = state

    await draft_statement(
        state, _deps(gateway), check_deadline=_deadline_gate,
        out_of_scope=_OUT_OF_SCOPE,
    )

    assert state.attempts[-1].report.status == "VALID"
    assert state.attempts[-1].rewritten_sql is not None


# ── classify: an entry edge, not an `if` in the service ──────────────────
@pytest.mark.parametrize(
    ("label", "phrase"),
    [
        ("CHITCHAT", "not a question about your data"),
        ("UNSUPPORTED", "outside what the database can answer"),
        ("METADATA", "what the schema contains"),
    ],
)
@pytest.mark.asyncio
async def test_a_classified_question_with_no_data_answer_is_refused(
    label: str, phrase: str
) -> None:
    """Raised from inside the graph, with the *service's* wording.

    The copy is a product decision about report blocks ("a list of table names
    is not a figure"), so it stays in the service that stores it and travels in
    through the config. The pipeline layer refuses; it does not editorialise.
    """
    gateway = PromptGateway(route=label, sql=[SQL_TOTAL])

    with pytest.raises(QuestionOutOfScopeError) as raised:
        await _draft(gateway, classify=True)

    assert phrase in raised.value.message
    assert raised.value.detail["intent"] == label
    # Refused before the schema-sized prompt, which is the whole point.
    assert gateway.sql_prompts == []


@pytest.mark.asyncio
async def test_metadata_halts_a_chat_run_and_refuses_a_draft() -> None:
    """The same classification, read two ways, on purpose.

    Chat lets METADATA through to `describe`, which answers from the schema and
    halts. A draft has no `describe` to reach and nowhere to put a reply, so
    the same label is a refusal. That is why the draft's gate ignores the
    label `route` produced and reads `intent` instead.
    """
    chat = await _chat(
        PromptGateway(route="METADATA", prose=["You have two tables."]),
        results=[],
    )
    assert chat.intent == "METADATA"
    assert chat.answer == "You have two tables."
    assert chat.attempts == []

    with pytest.raises(QuestionOutOfScopeError):
        await _draft(PromptGateway(route="METADATA", sql=[SQL_TOTAL]), classify=True)


@pytest.mark.asyncio
async def test_classifying_is_off_by_default_so_a_tile_draft_pays_nothing() -> None:
    """A gateway that would have refused this question proves it never ran."""
    gateway = PromptGateway(route="CHITCHAT", sql=[SQL_TOTAL])

    draft = await _draft(gateway, classify=False)

    assert draft.intent is None
    assert draft.attempts[-1].report.status == "VALID"


@pytest.mark.asyncio
async def test_a_flaky_classifier_refuses_nothing() -> None:
    """`route` fails open to ANALYTICAL on a provider error, and the gate reads
    what `route` decided — so a provider having a bad minute cannot turn a
    perfectly good block INFEASIBLE."""
    class NoRouting(PromptGateway):
        async def complete(self, _llm: Any, _messages: Any) -> Any:
            raise LLMError("the provider is unavailable")

    draft = await _draft(NoRouting(sql=[SQL_TOTAL]), classify=True)

    assert draft.intent == "ANALYTICAL"
    assert draft.attempts[-1].report.status == "VALID"


# ── the guard is not renegotiated by any of this ─────────────────────────
@pytest.mark.asyncio
async def test_the_draft_path_guards_with_the_connections_own_policy() -> None:
    """Non-negotiable #3. The region calls `validate`, which calls `guard`, and
    the policy is the caller's — a draft against a narrower allowlist is
    refused exactly as a chat run would be."""
    narrow = GuardPolicy(
        dialect="postgres", max_rows=1000,
        allowed_tables={"public.customers"},
        allowed_columns={"public.customers": {"id", "name"}},
    )
    state = _state()
    await draft_statement(
        state, _deps(PromptGateway(sql=[SQL_TOTAL, SQL_TOTAL]), policy=narrow),
        check_deadline=_deadline_gate, out_of_scope=_OUT_OF_SCOPE,
    )

    assert state.attempts[-1].report.status == "REJECTED"
    assert state.attempts[-1].report.errors[0].rule_id == "E_TABLE_NOT_ALLOWED"


# ── the chat side is untouched by the extraction ─────────────────────────
@pytest.mark.asyncio
async def test_the_chat_run_still_walks_every_node() -> None:
    """The region did not swallow anything. `tests/unit/test_pipeline_events.py`
    asserts the full trail; this is the one-line version, here so a failure in
    this file says whether the extraction or the draft broke."""
    recorder = Recorder()
    state = _state()
    deps = _deps(PromptGateway(sql=[SQL_TOTAL]), ScriptedConnector([ONE_ROW]),
                 emit=recorder.emit)
    await AnalyticsPipeline(on_step=recorder.on_step).run(state, deps)

    assert [name for _seq, name, _status in rows(recorder)] == [
        # `match` sits between `route` and `retrieve` from Phase 2 of the
        # learning loop, and is SKIPPED here because these deps carry no
        # matcher — the pre-feature path, which has to stay free.
        "route", "match", "retrieve", "describe", "clarify", "generate",
        "validate", "execute", "inspect", "present", "chart",
    ]
