"""A follow-up is a question with its subject left out.

"and by month?" is nine characters carrying no table, no measure and no data
noun. Two nodes used to read it alone:

* `route` classified it against `ROUTE_SYSTEM` and the bare string, which put
  CHITCHAT and UNSUPPORTED within reach of a question that plainly needs the
  database — and either label HALTs the run before any SQL is written;
* `retrieve`, on a schema too large for the full snapshot, matched it against
  table and column names, matched nothing, and fell through to an arbitrary
  `tables[:20]` that need not contain the table the previous turn queried.

Both now read the turns before the question. The first turn of a conversation
has no history and must behave exactly as it did before — the eval suite is
single-turn, and its baseline is that prompt.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.domain.value_objects import DisclosurePolicy
from app.pipeline.nodes import NodeDeps, _tables_from_history, retrieve, route
from app.pipeline.prompts import ROUTE_SYSTEM
from app.pipeline.state import RunState

FOLLOW_UP = [
    {"role": "user", "content": "What was revenue in June?"},
    {
        "role": "assistant",
        "content": "Revenue was $1.24M.",
        "sql": "SELECT SUM(oi.total) FROM public.orders o "
               "JOIN public.order_items oi ON oi.order_id = o.id",
    },
]


def _state(question: str = "and by month?", policy: str = DisclosurePolicy.SAMPLE):
    return RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question, disclosure_policy=policy,
        deadline_at=utcnow() + timedelta(seconds=60),
    )


class FakeGateway:
    """Records the messages it was sent and answers with a fixed label."""

    def __init__(self, label: str = "ANALYTICAL") -> None:
        self._label = label
        self.messages: list[Any] = []

    async def complete(self, _llm: Any, messages: Any) -> Any:
        self.messages = list(messages)

        class _Completion:
            text = self._label
            latency_ms = 1
            prompt_tokens = 1
            completion_tokens = 1

        return _Completion()


def _deps(gateway: Any = None, *, history: list[dict[str, str]], snapshot: Any = None):
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=gateway, llm=None, connector=None,
        snapshot=snapshot or {}, history=history, policy=None, emit=emit,
    )


# ── route ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_first_turn_sends_the_prompt_it_always_sent() -> None:
    """Byte-identical, not merely equivalent: this is the prompt the eval
    baseline was measured on."""
    gateway = FakeGateway()
    await route(_state("What was revenue in June?"), _deps(gateway, history=[]))
    assert gateway.messages[0].content == ROUTE_SYSTEM


@pytest.mark.asyncio
async def test_a_follow_up_is_classified_with_the_conversation_in_view() -> None:
    gateway = FakeGateway()
    await route(_state(), _deps(gateway, history=FOLLOW_UP))

    system = gateway.messages[0].content
    assert system != ROUTE_SYSTEM
    assert "What was revenue in June?" in system
    # The question itself stays the user message: the earlier turns are context
    # for reading it, never the thing being classified.
    assert gateway.messages[1].content == "and by month?"


@pytest.mark.asyncio
async def test_route_reads_the_history_on_the_policys_terms() -> None:
    """`route` is a model call like any other, so the narrow-policy filter
    applies to it too — this was the node that never rendered history at all,
    and the easiest place to reintroduce a leak."""
    gateway = FakeGateway()
    await route(
        _state(policy=DisclosurePolicy.NONE), _deps(gateway, history=FOLLOW_UP)
    )

    system = gateway.messages[0].content
    assert "Revenue was $1.24M." not in system
    assert "What was revenue in June?" in system  # the user's own words stay


# ── retrieve ────────────────────────────────────────────────────────────────
def _t(name: str, columns: int = 40) -> dict[str, Any]:
    return {
        "schema": "public",
        "name": name,
        "columns": [{"name": f"{name}_c{i}", "data_type": "text"} for i in range(columns)],
    }


# Wide enough that `retrieve` must select rather than send the whole snapshot.
BIG_SNAPSHOT = {
    "tables": [
        _t("orders"), _t("order_items"), _t("customers"), _t("products"),
        *[_t(f"unrelated_{i}") for i in range(40)],
    ],
    "relationships": [
        {"from_table": "public.order_items", "from_column": "order_id",
         "to_table": "public.orders", "to_column": "id"},
    ],
}


def test_tables_are_read_from_the_previous_statement_not_its_prose() -> None:
    """Exact, because `_SQL_RULES` requires every table to be qualified: a
    statement the guard passed spells `public.orders` verbatim. Prose names no
    table at all, and substring-matching it would match `id` in "identify"."""
    carried = _tables_from_history(FOLLOW_UP, BIG_SNAPSHOT["tables"])
    assert {t["name"] for t in carried} == {"orders", "order_items"}


def test_no_sql_in_the_history_carries_nothing() -> None:
    assert _tables_from_history([{"role": "user", "content": "orders"}], BIG_SNAPSHOT["tables"]) == []


@pytest.mark.asyncio
async def test_a_follow_up_inherits_the_tables_the_previous_turn_queried() -> None:
    state = _state()
    await retrieve(state, _deps(history=FOLLOW_UP, snapshot=BIG_SNAPSHOT))

    assert state.context is not None
    assert state.context.strategy == "EXACT_MATCH"
    selected = {t["name"] for t in state.context.tables}
    assert {"orders", "order_items"} <= selected


@pytest.mark.asyncio
async def test_without_history_the_fallback_is_what_it_was() -> None:
    state = _state()
    await retrieve(state, _deps(history=[], snapshot=BIG_SNAPSHOT))

    assert state.context is not None
    assert [t["name"] for t in state.context.tables] == [
        t["name"] for t in BIG_SNAPSHOT["tables"][:20]
    ]


@pytest.mark.asyncio
async def test_the_question_still_leads_when_it_names_a_table() -> None:
    """History supplements the question; it does not displace it."""
    state = _state("how many products do we have?")
    await retrieve(state, _deps(history=FOLLOW_UP, snapshot=BIG_SNAPSHOT))

    assert state.context is not None
    assert "products" in {t["name"] for t in state.context.tables}


@pytest.mark.asyncio
async def test_retrieval_reads_the_raw_history_under_every_policy() -> None:
    """Choosing which of the customer's own tables to answer from is not a
    disclosure — the selection never leaves the process, and what is rendered
    from it is gated by `RetrievedContext.render` exactly as before."""
    state = _state(policy=DisclosurePolicy.NONE)
    await retrieve(state, _deps(history=FOLLOW_UP, snapshot=BIG_SNAPSHOT))

    assert state.context is not None
    assert {"orders", "order_items"} <= {t["name"] for t in state.context.tables}
