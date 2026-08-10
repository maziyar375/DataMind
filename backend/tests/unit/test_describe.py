"""A schema question is answered from the schema, and from what it means.

The node under test replaced a fixed rendering of the snapshot — table names
and row counts, whatever had been asked. Three things have to hold for that to
be an improvement rather than a new way to be wrong:

* the answer is written over the *same* block the generator would have seen,
  semantic layer included, because the grain of a table and the metrics defined
  over it are written down nowhere else;
* it widens no disclosure — the block is rendered under the run's policy, and
  the transcript through the same filter as every other prompt;
* it never costs the user an answer: a provider that breaks, one that says
  nothing, and a snapshot with no tables all end with something readable, and
  none of them ends in SQL.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import LLMError
from app.domain.value_objects import DisclosurePolicy
from app.pipeline.metadata import answer_metadata
from app.pipeline.nodes import NodeDeps, describe, retrieve, route
from app.pipeline.state import RetrievedContext, RunState

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "approx_row_count": 4200,
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "status", "data_type": "text"},
            {"name": "total", "data_type": "numeric"},
        ],
    },
    {
        "schema": "public",
        "name": "order_items",
        "approx_row_count": 18000,
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {
                "name": "order_id",
                "data_type": "bigint",
                "is_foreign_key": True,
                "references": "public.orders.id",
            },
        ],
    },
]

SEMANTIC = {
    "business_context": "A retail order book.",
    "entities": [
        {
            "table": "public.order_items",
            "label": "Order lines",
            "grain": "one row per line item on an order",
            "role": "fact",
            "metrics": [
                {"name": "revenue", "expression": "SUM(public.order_items.total)"}
            ],
        }
    ],
}

HISTORY = [
    {"role": "user", "content": "What was revenue in June?"},
    {
        "role": "assistant",
        "content": "Revenue was $1.24M.",
        "sql": "SELECT SUM(total) FROM public.orders",
    },
]


class FakeGateway:
    """Streams canned deltas, optionally failing after `fail_after` of them."""

    def __init__(
        self, deltas: list[str] | None = None, *, fail_after: int | None = None
    ) -> None:
        self._deltas = deltas if deltas is not None else ["You have two tables."]
        self._fail_after = fail_after
        self.messages: list[Any] = []
        self.calls = 0

    def stream(self, _llm: Any, messages: Any) -> AsyncIterator[str]:
        self.calls += 1
        self.messages = list(messages)

        async def gen() -> AsyncIterator[str]:
            for i, delta in enumerate(self._deltas):
                if self._fail_after is not None and i == self._fail_after:
                    raise LLMError("provider dropped the connection")
                yield delta
            if self._fail_after == len(self._deltas):
                raise LLMError("provider dropped the connection")

        return gen()

    async def complete(self, _llm: Any, messages: Any) -> Any:
        self.calls += 1
        self.messages = list(messages)

        class _Completion:
            text = "METADATA"
            latency_ms = 1
            prompt_tokens = 1
            completion_tokens = 1

        return _Completion()


def _state(
    question: str = "What tables do I have?",
    *,
    intent: str | None = "METADATA",
    policy: str = DisclosurePolicy.SAMPLE,
    semantic: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question, disclosure_policy=policy,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.intent = intent  # type: ignore[assignment]
    state.context = RetrievedContext(
        dialect="postgres",
        tables=TABLES if tables is None else tables,
        history=history or [],
        semantic=semantic,
    )
    return state


def _deps(
    gateway: Any, *, tables: list[dict[str, Any]] | None = None, **kwargs: Any
) -> tuple[NodeDeps, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    return (
        NodeDeps(
            llm_gateway=gateway, llm=None, connector=None,
            snapshot={"tables": TABLES if tables is None else tables},
            history=[], policy=None, emit=emit, **kwargs,
        ),
        events,
    )


# ── who this node is for ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_analytical_question_skips_it_without_a_model_call() -> None:
    """The common case must cost nothing: no tokens, no answer, no halt."""
    gateway = FakeGateway()
    state = _state(intent="ANALYTICAL")

    result = await describe(state, _deps(gateway)[0])

    assert result.status == "SKIPPED"
    assert gateway.calls == 0
    assert state.answer is None


@pytest.mark.asyncio
async def test_a_schema_question_halts_before_any_sql() -> None:
    state = _state()

    result = await describe(state, _deps(FakeGateway())[0])

    assert result.status == "HALT"
    assert state.attempts == []
    assert state.answer == "You have two tables."


@pytest.mark.asyncio
async def test_route_no_longer_answers_the_question_itself() -> None:
    """`route` classifies and continues; the answer is `describe`'s job now.

    The old branch rendered the snapshot inside `route` and halted there, which
    is why a schema question never saw the semantic layer: `retrieve` had not
    run yet.
    """
    state = _state(intent=None)
    result = await route(state, _deps(FakeGateway())[0])

    assert state.intent == "METADATA"
    assert result.status == "OK"
    assert state.answer is None


# ── what reaches the model ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_prompt_carries_the_schema_and_the_semantic_layer() -> None:
    """The whole point of moving the answer after `retrieve`: a question about
    what a table *means* is answerable only from the layer."""
    gateway = FakeGateway()
    state = _state("what is order_items?", semantic=SEMANTIC)

    await describe(state, _deps(gateway)[0])
    system = gateway.messages[0].content

    assert "public.order_items(" in system  # the schema block
    assert "one row per line item on an order" in system  # the grain
    assert "metric revenue = SUM(public.order_items.total)" in system
    assert "A retail order book." in system
    assert gateway.messages[1].content == "Question: what is order_items?"


@pytest.mark.asyncio
async def test_the_prompt_states_how_much_of_the_schema_it_is() -> None:
    gateway = FakeGateway()

    await describe(_state(), _deps(gateway)[0])

    assert "This connection has 2 tables in public." in gateway.messages[0].content


@pytest.mark.asyncio
async def test_tables_left_out_of_the_block_are_still_named() -> None:
    """A model handed half a schema will otherwise answer for half a schema."""
    gateway = FakeGateway()
    state = _state()
    state.context.tables = [TABLES[0]]  # type: ignore[union-attr]

    await describe(state, _deps(gateway)[0])
    system = gateway.messages[0].content

    assert "This connection has 2 tables in public." in system
    assert "1 of them is described above" in system
    assert "One more table exists but is not described" in system
    assert "order_items" in system


@pytest.mark.asyncio
async def test_the_conversation_reaches_it_on_the_policys_terms() -> None:
    """"what columns does it have?" is unreadable without the turn that named
    the table — and an earlier answer's prose is result data, gated as such."""
    shared = FakeGateway()
    await describe(_state(history=HISTORY), _deps(shared)[0])
    assert "What was revenue in June?" in shared.messages[0].content
    assert "Revenue was $1.24M." in shared.messages[0].content

    narrow = FakeGateway()
    await describe(
        _state(history=HISTORY, policy=DisclosurePolicy.NONE), _deps(narrow)[0]
    )
    assert "Revenue was $1.24M." not in narrow.messages[0].content
    assert "What was revenue in June?" in narrow.messages[0].content


@pytest.mark.asyncio
async def test_row_counts_ride_the_same_gate_as_every_other_prompt() -> None:
    """Structure is never gated, content always is — this node is no exception
    just because its subject *is* the schema."""
    shared = FakeGateway()
    await describe(_state(), _deps(shared)[0])
    assert "4,200 rows" in shared.messages[0].content

    narrow = FakeGateway()
    await describe(_state(policy=DisclosurePolicy.NONE), _deps(narrow)[0])
    system = narrow.messages[0].content
    assert "4,200 rows" not in system
    assert "public.orders(" in system  # the names still travel


# ── failing backwards, never forwards ──────────────────────────────────────
@pytest.mark.asyncio
async def test_a_broken_stream_falls_back_to_the_rendered_snapshot() -> None:
    gateway = FakeGateway(["You have ", "two "], fail_after=2)
    state = _state()
    deps, events = _deps(gateway)

    result = await describe(state, deps)

    assert result.status == "HALT"
    assert [t for t, _ in events] == [
        "TEXT_DELTA", "TEXT_DELTA", "TEXT_RESET", "TEXT_DELTA"
    ]
    assert events[2][1] == {"reason": "stream_failed"}
    assert state.answer == answer_metadata(state.question, TABLES)


@pytest.mark.asyncio
async def test_a_stream_that_says_nothing_is_a_failure_too() -> None:
    """A provider that yields nothing does not raise; an empty answer is still
    an unanswered question, and there is nothing rendered to reset."""
    state = _state()
    deps, events = _deps(FakeGateway([]))

    await describe(state, deps)

    assert [t for t, _ in events] == ["TEXT_DELTA"]
    assert state.answer == answer_metadata(state.question, TABLES)


@pytest.mark.asyncio
async def test_an_empty_snapshot_costs_no_model_call() -> None:
    gateway = FakeGateway()
    state = _state(tables=[])
    deps, events = _deps(gateway, tables=[])

    result = await describe(state, deps)

    assert gateway.calls == 0
    assert result.status == "HALT"
    assert state.answer is not None and "no tables" in state.answer
    assert [t for t, _ in events] == ["TEXT_DELTA"]


# ── retrieval for a schema question ────────────────────────────────────────
def _wide(name: str, columns: int = 40, rows: int = 0) -> dict[str, Any]:
    return {
        "schema": "public",
        "name": name,
        "approx_row_count": rows,
        "columns": [
            {"name": f"c{i}", "data_type": "text"} for i in range(columns)
        ],
    }


@pytest.mark.asyncio
async def test_a_wide_schema_is_selected_for_by_size_not_by_wording() -> None:
    """Over budget, the generator's selector seeds on words the question shares
    with a table name — and "what tables do I have?" shares none, which would
    leave a schema question answered from an arbitrary twenty."""
    tables = [_wide(f"t{i:03}", rows=i) for i in range(60)]
    state = _state(intent="METADATA")
    deps, _ = _deps(FakeGateway(), tables=tables)
    deps.snapshot["relationships"] = []

    await retrieve(state, deps)

    assert state.context is not None
    assert state.context.strategy == "SCHEMA_QUESTION"
    names = [t["name"] for t in state.context.tables]
    assert names  # something was chosen
    assert "t059" in names  # the largest
    assert "t000" not in names  # the smallest did not make the cut


@pytest.mark.asyncio
async def test_a_named_table_is_described_however_small_it_is() -> None:
    tables = [_wide(f"t{i:03}", rows=i) for i in range(60)]
    tables.append(_wide("tiny_lookup", columns=2, rows=1))
    state = _state("what columns does tiny_lookup have?", intent="METADATA")
    deps, _ = _deps(FakeGateway(), tables=tables)
    deps.snapshot["relationships"] = []

    await retrieve(state, deps)

    assert state.context is not None
    assert "tiny_lookup" in [t["name"] for t in state.context.tables]
