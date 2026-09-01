"""Narrating a result — what reaches the sentence, and what happens when the
narration breaks.

Two properties are tested here:

* `inspect`'s findings reach the answer prompt, because a caveat the system
  already computed ("archived rows were not filtered") is worthless sitting in
  an event nobody reads. Only the *message* travels — the hint is repair
  guidance written for the generator, not for the reader.
* a narration failure never loses the result, and never leaves a client
  showing half a sentence with the fallback glued onto the end.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import LLMError
from app.domain.ports.database import ResultColumn
from app.domain.ports.llm import StreamChunk
from app.pipeline.checks import Finding
from app.pipeline.nodes import NodeDeps, present
from app.pipeline.state import ExecutionResult, RunState, SqlAttempt
from app.sqlguard.validator import ValidationReport

NULLABLE_JOIN = Finding(
    code="C_NULLABLE_INNER_JOIN",
    message="This inner-joins on nullable column(s): orders.employee_id.",
    hint="Rows where those columns are NULL were dropped. Use a LEFT JOIN.",
)
SOFT_DELETE = Finding(
    code="C_SOFT_DELETE_UNFILTERED",
    message=(
        "Table(s) with a soft-delete flag were queried without filtering it: "
        "orders.is_archived."
    ),
    hint="Deleted or archived rows are counted in this result.",
)


def _state(findings: list[Finding] | None = None) -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="How many orders did we ship?",
        disclosure_policy="SAMPLE",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.attempts = [
        SqlAttempt(
            attempt_no=1,
            raw_sql="SELECT count(*) AS n FROM public.orders",
            rewritten_sql="SELECT count(*) AS n FROM public.orders LIMIT 1000",
            report=ValidationReport(),
            findings=findings or [],
        )
    ]
    state.execution = ExecutionResult(
        columns=[ResultColumn(name="n", db_type="bigint", semantic_type="quantitative")],
        rows=[[412]],
        row_count=1,
    )
    return state


class FakeGateway:
    """Streams canned deltas, optionally failing after `fail_after` of them.

    `thoughts` are yielded first, on the reasoning channel — what a reasoning
    model sends before it writes anything.
    """

    def __init__(
        self,
        deltas: list[str],
        *,
        fail_after: int | None = None,
        thoughts: list[str] | None = None,
    ) -> None:
        self._deltas = deltas
        self._fail_after = fail_after
        self._thoughts = thoughts or []
        self.messages: list[Any] = []

    def stream(self, _llm: Any, messages: Any) -> AsyncIterator[StreamChunk]:
        self.messages = list(messages)

        async def gen() -> AsyncIterator[StreamChunk]:
            for thought in self._thoughts:
                yield StreamChunk(reasoning=thought)
            for i, delta in enumerate(self._deltas):
                if self._fail_after is not None and i == self._fail_after:
                    raise LLMError("provider dropped the connection")
                yield StreamChunk(text=delta)
            if self._fail_after == len(self._deltas):
                raise LLMError("provider dropped the connection")

        return gen()


def _deps(gateway: Any) -> tuple[NodeDeps, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    return (
        NodeDeps(
            llm_gateway=gateway, llm=None, connector=None, snapshot={},
            history=[], policy=None, emit=emit,
        ),
        events,
    )


# ── caveats reach the reader ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_findings_reach_the_answer_prompt() -> None:
    gateway = FakeGateway(["412 orders shipped."])
    state = _state([NULLABLE_JOIN, SOFT_DELETE])

    await present(state, _deps(gateway)[0])
    prompt = gateway.messages[1].content

    assert "orders.employee_id" in prompt
    assert "orders.is_archived" in prompt
    # Both findings, not just the first, and as separate lines.
    assert prompt.count("\n- ") >= 2


@pytest.mark.asyncio
async def test_the_repair_hint_is_not_shown_to_the_reader() -> None:
    """`hint` is written for the generator ("use a LEFT JOIN"). Putting it in
    front of a business user reads as advice they cannot act on."""
    gateway = FakeGateway(["412 orders shipped."])

    await present(_state([NULLABLE_JOIN]), _deps(gateway)[0])
    prompt = gateway.messages[1].content

    assert "LEFT JOIN" not in prompt
    assert NULLABLE_JOIN.hint not in prompt


@pytest.mark.asyncio
async def test_no_findings_leaves_the_prompt_exactly_as_it_was() -> None:
    """The no-caveat case is the common case and the eval baseline: no stray
    placeholder, no empty heading, not even a trailing blank line."""
    from app.pipeline.disclosure import disclose
    from app.pipeline.prompts import ANSWER_USER

    gateway = FakeGateway(["412 orders shipped."])
    state = _state([])

    await present(state, _deps(gateway)[0])
    prompt = gateway.messages[1].content

    expected = ANSWER_USER.replace("{caveats}", "").format(
        question=state.question,
        sql=state.executable_sql or "",
        row_count=state.execution.row_count,  # type: ignore[union-attr]
        result=disclose(
            state.execution,  # type: ignore[arg-type]
            state.disclosure_policy,
        ).render(),
    )
    assert prompt == expected
    assert "caveat" not in prompt.lower()
    assert "{caveats}" not in prompt


@pytest.mark.asyncio
async def test_an_attempt_with_no_findings_is_the_only_one_consulted() -> None:
    """A retry that cleared a finding must not have it resurface here: only the
    attempt whose result is being presented is read."""
    gateway = FakeGateway(["412 orders shipped."])
    state = _state([])
    state.attempts.insert(
        0,
        SqlAttempt(
            attempt_no=0, raw_sql="SELECT 1", report=ValidationReport(),
            findings=[SOFT_DELETE],
        ),
    )

    await present(state, _deps(gateway)[0])

    assert "is_archived" not in gateway.messages[1].content


@pytest.mark.asyncio
async def test_no_attempts_at_all_does_not_crash() -> None:
    """Defensive, like the other nodes: a result with no attempt behind it
    still gets narrated rather than raising."""
    gateway = FakeGateway(["412 orders shipped."])
    state = _state([])
    state.attempts = []

    result = await present(state, _deps(gateway)[0])

    assert result.status == "OK"
    assert state.answer == "412 orders shipped."


@pytest.mark.asyncio
async def test_the_system_prompt_keeps_its_existing_constraints() -> None:
    from app.pipeline.prompts import ANSWER_SYSTEM

    assert "Two or three sentences" in ANSWER_SYSTEM
    assert "no SQL jargon, no markdown headings" in ANSWER_SYSTEM
    assert "caveat" in ANSWER_SYSTEM.lower()


# ── a narration failure ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_mid_stream_failure_tells_the_client_to_discard_the_partial() -> None:
    """The deltas already sent are on the live bus and durably stored for
    replay, so the fallback cannot just be appended to them."""
    gateway = FakeGateway(["412 orders ", "were "], fail_after=2)
    state = _state([])
    deps, events = _deps(gateway)

    result = await present(state, deps)

    types = [t for t, _ in events]
    assert types == ["TEXT_DELTA", "TEXT_DELTA", "TEXT_RESET", "TEXT_DELTA"]
    assert events[2][1] == {"reason": "stream_failed"}
    # The reset comes before the fallback, never after it.
    assert types.index("TEXT_RESET") < len(types) - 1
    # And the result itself survives untouched.
    assert result.status == "OK"
    assert state.answer is not None and "412" not in state.answer
    assert state.execution is not None and state.execution.rows == [[412]]


@pytest.mark.asyncio
async def test_a_failure_before_any_delta_needs_no_reset() -> None:
    """Nothing was rendered, so there is nothing to clear — today's behaviour
    is already right and stays byte-identical."""
    gateway = FakeGateway([], fail_after=0)
    state = _state([])
    deps, events = _deps(gateway)

    await present(state, deps)

    assert [t for t, _ in events] == ["TEXT_DELTA"]
    assert state.answer == (
        "The query returned 1 rows. "
        "I could not generate a written summary for this result."
    )
