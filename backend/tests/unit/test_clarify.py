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
class _FakeRun:
    """Enough of a `Run` for the two helpers that read one."""

    def __init__(self, status: str = "SUCCEEDED", *, asked: bool = False) -> None:
        self.id = uuid4()
        self.conversation_id = uuid4()
        self.status = status
        self.user_message_id = uuid4()
        self.assistant_message_id = uuid4() if asked else None


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


def _service(previous: Any, messages: dict[Any, str] | None = None) -> Any:
    """A `RunService` with just the session the helpers touch."""
    from app.services.run_service import RunService

    class FakeResult:
        def scalar_one_or_none(self) -> Any:
            return previous

    class FakeSession:
        async def execute(self, _query: Any) -> FakeResult:
            return FakeResult()

        async def get(self, _model: Any, key: Any) -> Any:
            content = (messages or {}).get(key)
            return _FakeMessage(content) if content is not None else None

    service = RunService.__new__(RunService)
    service._db = FakeSession()  # type: ignore[assignment]
    return service


@pytest.mark.asyncio
async def test_a_run_answering_a_question_may_not_ask_again() -> None:
    """The structural half of the guarantee. The model cannot be trusted to
    notice from the transcript that it already asked, so the *second* run of an
    exchange never gets the chance, whatever it makes of the reply."""
    from app.domain.value_objects import RunStatus

    previous = _FakeRun(RunStatus.NEEDS_CLARIFICATION)
    service = _service(previous)

    assert await service._pending_clarification(_FakeRun()) is previous


@pytest.mark.asyncio
async def test_a_fresh_question_may_ask() -> None:
    from app.domain.value_objects import RunStatus

    service = _service(_FakeRun(RunStatus.SUCCEEDED))

    assert await service._pending_clarification(_FakeRun()) is None


@pytest.mark.asyncio
async def test_the_first_run_of_a_thread_may_ask() -> None:
    """No previous run at all — `scalar_one_or_none` returns None."""
    service = _service(None)

    assert await service._pending_clarification(_FakeRun()) is None


# ── composing the reply back into the question ──────────────────────────────
@pytest.mark.asyncio
async def test_the_reply_to_a_clarification_carries_its_question() -> None:
    """The regression this exists for.

    Asked "who are our best sellers?", told "by total sales", the pipeline used
    to receive "by total sales" *alone* — a complete, answerable question that
    the generator duly answered with one figure across all orders. The subject
    has to travel with the reply, or `_SQL_RULES` ("answer exactly what is
    asked") reads the criterion as the whole question.
    """
    from app.domain.value_objects import RunStatus

    previous = _FakeRun(RunStatus.NEEDS_CLARIFICATION, asked=True)
    run = _FakeRun()
    service = _service(
        previous,
        {
            run.user_message_id: "Total sales (order amount)",
            previous.user_message_id: "Who are our best sellers?",
            previous.assistant_message_id: "By which measure?",
        },
    )

    composed = await service._compose_question(run, previous)

    assert composed.startswith("Who are our best sellers?")
    assert "Total sales (order amount)" in composed
    assert "By which measure?" in composed
    # The instruction matters as much as the text: without it the model reads
    # two questions and answers the nearer one.
    assert "not itself the question" in composed


@pytest.mark.asyncio
async def test_an_ordinary_question_is_passed_through_verbatim() -> None:
    """Off-by-absence, again: no pending clarification, no composition, and the
    pipeline sees exactly the bytes the user typed."""
    run = _FakeRun()
    service = _service(None, {run.user_message_id: "Revenue by month last year"})

    assert await service._compose_question(run, None) == "Revenue by month last year"


@pytest.mark.asyncio
async def test_composition_survives_a_missing_original() -> None:
    """Fails open like the node it serves: a run whose user message is gone
    yields the reply, never an empty question or a crash."""
    from app.domain.value_objects import RunStatus

    previous = _FakeRun(RunStatus.NEEDS_CLARIFICATION, asked=True)
    run = _FakeRun()
    service = _service(previous, {run.user_message_id: "By total sales"})

    assert await service._compose_question(run, previous) == "By total sales"


@pytest.mark.asyncio
async def test_each_quoted_part_is_capped() -> None:
    """A pasted essay in any of the three parts cannot crowd the schema out of
    the prompt."""
    from app.domain.value_objects import RunStatus
    from app.services.run_service import _QUESTION_CHARS

    previous = _FakeRun(RunStatus.NEEDS_CLARIFICATION, asked=True)
    run = _FakeRun()
    service = _service(
        previous,
        {
            run.user_message_id: "r" * 5_000,
            previous.user_message_id: "o" * 5_000,
            previous.assistant_message_id: "a" * 5_000,
        },
    )

    composed = await service._compose_question(run, previous)

    assert "o" * _QUESTION_CHARS in composed
    assert "o" * (_QUESTION_CHARS + 1) not in composed
    assert "r" * (_QUESTION_CHARS + 1) not in composed
    assert "a" * (_QUESTION_CHARS + 1) not in composed


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


# ── the schema the model actually sees ──────────────────────────────────────
def test_every_field_is_required_so_the_model_cannot_drop_the_options() -> None:
    """The bug this pins: `options` had a Python default, which kept it out of
    `required` in `model_json_schema()` — and that schema is what goes to the
    provider as a strict `json_schema` response format. Measured against the
    configured model on one ambiguous question: options came back on 1 of 3
    replies that asked something under the defaulted schema, and 4 of 4 with
    every field required. The user got a question with no chips to click.

    A default here is invisible to the model, so it is not a convenience — it
    is a silently weaker contract.
    """
    schema = ClarificationProposal.model_json_schema()
    assert set(schema["required"]) == {"answerable", "question", "options", "reasoning"}


def test_a_model_that_ignores_that_schema_still_does_not_break_clarify() -> None:
    """The other half. `clarify` fails open, so a `ValidationError` would reach
    it as "clarification unavailable" — the node whose job is noticing ambiguity,
    switched off by a missing key. Required in the schema, tolerated on the way
    in."""
    bare = ClarificationProposal.model_validate({"answerable": False, "question": "A or B?"})
    assert bare.options == [] and bare.reasoning == ""
    assert ClarificationProposal.model_validate({"answerable": True}).question == ""


def test_the_prompt_asks_for_options_on_every_question() -> None:
    """The schema makes the key present; only the prompt makes it non-empty."""
    from app.pipeline.prompts import CLARIFY_SYSTEM

    assert "Always give 2-4 options" in CLARIFY_SYSTEM
