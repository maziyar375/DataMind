"""Asking instead of guessing — and the guarantees that make it safe.

The check is one model call placed after `retrieve`, so it judges the question
against the same schema block and semantic layer the generator will see. Three
properties matter more than the judgement itself, and each has a test here:

* it fails **open** — every error, refusal or malformed answer proceeds to
  `generate`, because a guessed answer shown with its SQL beats no answer;
* it asks **at most once** per exchange, enforced structurally rather than by
  asking the model to remember;
* it is **off-by-absence** — with the switch off, the run is byte-identical to
  one from before the feature existed.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import LLMError
from app.pipeline.contracts import ClarificationProposal
from app.pipeline.nodes import NodeDeps, _clean_options, clarify
from app.pipeline.state import RetrievedContext, RunState

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "order_total", "data_type": "numeric"},
            {"name": "paid_amount", "data_type": "numeric"},
        ],
    }
]


def _state(question: str = "Show me our best customers") -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.context = RetrievedContext(dialect="postgres", tables=TABLES)
    return state


class FakeGateway:
    """Returns a canned proposal, or raises, and records what it was sent."""

    def __init__(self, proposal: Any = None, error: Exception | None = None) -> None:
        self._proposal = proposal
        self._error = error
        self.messages: list[Any] = []
        self.calls = 0

    async def structured(self, _llm: Any, messages: Any, _schema: Any) -> Any:
        self.calls += 1
        self.messages = list(messages)
        if self._error is not None:
            raise self._error
        return self._proposal


def _deps(
    gateway: Any, *, enabled: bool = True
) -> tuple[NodeDeps, list[tuple[str, dict[str, Any]]]]:
    """Deps plus the list every emitted event lands in. `NodeDeps` is a slots
    dataclass, so the sink cannot just be hung off it."""
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    return (
        NodeDeps(
            llm_gateway=gateway, llm=None, connector=None, snapshot={},
            history=[], policy=None, emit=emit, clarify_enabled=enabled,
        ),
        events,
    )


# ── the switch ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_disabled_costs_nothing_and_asks_nothing() -> None:
    """Off-by-absence, like the semantic layer: no call, no question, no step
    that changes what `generate` receives."""
    gateway = FakeGateway(ClarificationProposal(answerable=False, question="Which?"))
    state = _state()

    result = await clarify(state, _deps(gateway, enabled=False)[0])

    assert result.status == "SKIPPED"
    assert gateway.calls == 0
    assert state.clarification is None
    assert state.answer is None


# ── the judgement ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_answerable_question_proceeds_untouched() -> None:
    gateway = FakeGateway(ClarificationProposal(answerable=True))
    state = _state("How many orders were placed in 2025?")

    result = await clarify(state, _deps(gateway)[0])

    assert result.status == "OK"
    assert result.goto is None
    assert state.clarification is None
    assert state.answer is None


@pytest.mark.asyncio
async def test_ambiguous_question_halts_before_any_sql() -> None:
    gateway = FakeGateway(
        ClarificationProposal(
            answerable=False,
            question="Best by total spend, or by number of orders?",
            options=["By total spend", "By number of orders"],
        )
    )
    state = _state()
    deps, events = _deps(gateway)

    result = await clarify(state, deps)

    assert result.status == "HALT"          # nothing downstream runs
    assert state.attempts == []             # in particular, no SQL was written
    assert state.clarification is not None
    assert state.clarification.options == ["By total spend", "By number of orders"]
    # The question is the turn's answer, so the thread reads as a conversation.
    assert state.answer == "Best by total spend, or by number of orders?"
    assert [t for t, _ in events] == ["CLARIFICATION_REQUESTED"]


@pytest.mark.asyncio
async def test_the_model_sees_the_schema_and_the_history() -> None:
    """A metric definition that already settles the question must be visible,
    or the node invents doubt the generator would not have had."""
    gateway = FakeGateway(ClarificationProposal(answerable=True))
    state = _state()
    state.context = RetrievedContext(
        dialect="postgres", tables=TABLES,
        history=[{"role": "user", "content": "only EU orders please"}],
    )

    await clarify(state, _deps(gateway)[0])
    system = gateway.messages[0].content

    assert "public.orders" in system
    assert "paid_amount" in system
    assert "only EU orders please" in system


# ── failing open ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_provider_error_proceeds_rather_than_stalling() -> None:
    """An unanswered question is worse than a guessed one — and the guess is
    still shown with its SQL for the user to check."""
    state = _state()

    result = await clarify(state, _deps(FakeGateway(error=LLMError("down")))[0])

    assert result.status == "SKIPPED"
    assert state.clarification is None


@pytest.mark.asyncio
async def test_unanswerable_with_no_question_proceeds() -> None:
    """`answerable=False` with nothing to ask is a malformed answer, not a
    reason to halt the run with an empty assistant message."""
    gateway = FakeGateway(ClarificationProposal(answerable=False, question="   "))
    state = _state()

    result = await clarify(state, _deps(gateway)[0])

    assert result.status == "OK"
    assert state.clarification is None
    assert state.answer is None


# ── options hygiene ─────────────────────────────────────────────────────────
def test_options_are_deduplicated_trimmed_and_capped() -> None:
    cleaned = _clean_options(
        ["By revenue", "  by   revenue ", "By orders", "", "By margin", "By region",
         "By tenure"]
    )
    assert cleaned == ["By revenue", "By orders", "By margin", "By region"]


# ── the loop guard ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_run_answering_a_question_may_not_ask_again() -> None:
    """The structural half of the guarantee. The model cannot be trusted to
    notice from the transcript that it already asked, so the *second* run of an
    exchange never gets the chance, whatever it makes of the reply."""
    from app.domain.value_objects import RunStatus
    from app.services.run_service import RunService

    class FakeResult:
        def scalar_one_or_none(self) -> str:
            return RunStatus.NEEDS_CLARIFICATION

    class FakeSession:
        async def execute(self, _query: Any) -> FakeResult:
            return FakeResult()

    service = RunService.__new__(RunService)
    service._db = FakeSession()  # type: ignore[assignment]

    class FakeRun:
        id = uuid4()
        conversation_id = uuid4()

    assert await service._previous_run_asked(FakeRun()) is True  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_fresh_question_may_ask() -> None:
    from app.domain.value_objects import RunStatus
    from app.services.run_service import RunService

    class FakeResult:
        def scalar_one_or_none(self) -> str:
            return RunStatus.SUCCEEDED

    class FakeSession:
        async def execute(self, _query: Any) -> FakeResult:
            return FakeResult()

    service = RunService.__new__(RunService)
    service._db = FakeSession()  # type: ignore[assignment]

    class FakeRun:
        id = uuid4()
        conversation_id = uuid4()

    assert await service._previous_run_asked(FakeRun()) is False  # type: ignore[arg-type]


# ── wiring ──────────────────────────────────────────────────────────────────
def test_clarify_runs_after_retrieve_and_before_generate() -> None:
    """Position is the whole design: after retrieve it can see the schema,
    before generate it costs no SQL."""
    from app.domain.value_objects import StepName
    from app.pipeline.pipeline import ORDER

    names = [name for name, _ in ORDER]
    assert names.index(StepName.RETRIEVE) < names.index(StepName.CLARIFY)
    assert names.index(StepName.CLARIFY) < names.index(StepName.GENERATE)


def test_the_generate_prompt_is_untouched_by_this_feature() -> None:
    """The query path must stay byte-identical: the eval baseline for
    GENERATE_SYSTEM was measured without any clarification wording, and adding
    to that prompt has already cost 10 points of execution accuracy once."""
    from app.pipeline.prompts import GENERATE_SYSTEM

    lowered = GENERATE_SYSTEM.lower()
    assert "clarif" not in lowered
    assert "ambigu" not in lowered


# ── waiting on the user is not waiting on the server ─────────────────────────
def test_a_run_that_asked_is_not_terminal_but_is_not_in_flight_either() -> None:
    """The two questions a status gets asked are different questions.

    `is_terminal` asks whether the *exchange* is over — it is not, which is why
    cancel still applies to a run that asked and why the reconciler leaves it
    alone. `is_in_flight` asks whether the executor may still emit events — it
    may not, because the run wrote its question and closed its stream. Reading
    the second off `not is_terminal` is what made the SPA reattach to a
    finished run, replay its RUN_FINISHED, reload, and reattach again, looping
    the step trail and locking the composer while the user was being asked to
    reply.
    """
    from app.domain.value_objects import RunStatus

    assert RunStatus.NEEDS_CLARIFICATION.is_terminal is False
    assert RunStatus.NEEDS_CLARIFICATION.is_in_flight is False

    for status in (RunStatus.QUEUED, RunStatus.RUNNING):
        assert status.is_in_flight is True
        assert status.is_terminal is False

    for status in (
        RunStatus.SUCCEEDED, RunStatus.FAILED,
        RunStatus.CANCELLED, RunStatus.TIMED_OUT,
    ):
        assert status.is_terminal is True
        assert status.is_in_flight is False
