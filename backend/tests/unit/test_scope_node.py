"""The `scope` node: which part of the database is this question about?

`docs/plans/retrieval-sections.md` §3.1–3.3. On a connection with saved
sections a small model call names up to three of them, and their tables become
what `retrieve` narrows to. Everything else **falls open to today's behaviour**
(D3): no sections, a schema question, a provider error, a reply that is `NONE`,
empty, or names no section — each leaves the state untouched, so `retrieve`
builds the unscoped block it always built.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import LLMError
from app.domain.value_objects import StepStatus
from app.pipeline import nodes
from app.pipeline.nodes import NodeDeps, parse_scope_reply, retrieve, scope
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.prompts import SCOPE_SYSTEM, SCOPE_SYSTEM_WITH_CURRENT
from app.pipeline.sections import SectionSpec
from app.pipeline.state import RunState
from tests.unit.test_pipeline_events import (
    ONE_ROW,
    POLICY,
    SQL_TOTAL,
    ScriptedConnector,
    ScriptedGateway,
)


def _t(name: str) -> dict[str, Any]:
    return {"schema": "public", "name": name, "approx_row_count": 10,
            "columns": [{"name": "id", "data_type": "bigint"},
                        {"name": f"{name}_label", "data_type": "text"}]}


TABLES = [_t(n) for n in (
    "orders", "customers", "order_items", "customer_addresses", "products", "tags",
)]
SNAPSHOT: dict[str, Any] = {"dialect": "postgres", "tables": TABLES, "relationships": []}
ALL = [f"public.{t['name']}" for t in TABLES]

SECTIONS = [
    SectionSpec("Sales", "Orders and their lines.", ("public.orders", "public.order_items")),
    SectionSpec("People", "Customers and where they live.",
                ("public.customers", "public.customer_addresses")),
    SectionSpec("Catalog", "Products and their tags.", ("public.products", "public.tags")),
    # Every table it held has left the schema: never offered to the router.
    SectionSpec("Archive", "Old orders.", ("public.orders_2019",)),
]


class Router:
    """A gateway whose `complete` replies with a fixed string, or raises."""

    def __init__(self, reply: str = "", *, raises: bool = False) -> None:
        self.reply = reply
        self.raises = raises
        self.messages: list[Any] = []

    async def complete(self, _llm: Any, messages: Any) -> Any:
        self.messages.append(list(messages))
        if self.raises:
            raise LLMError("provider went away")

        class _Completion:
            text = self.reply
            latency_ms = 5
            prompt_tokens = 120
            completion_tokens = 2

        return _Completion()


def _state(question: str = "revenue by customer", intent: str = "ANALYTICAL") -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.intent = intent  # type: ignore[assignment]
    return state


def _deps(gateway: Any, sections: list[SectionSpec] | None = SECTIONS) -> NodeDeps:
    async def emit(_t: str, _d: Any) -> None:
        return None

    return NodeDeps(
        llm_gateway=gateway, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=SNAPSHOT, history=[], policy=None,  # type: ignore[arg-type]
        emit=emit, sections=sections,
    )


# ── the feature working ──────────────────────────────────────────────────
async def test_a_clean_pick_narrows_to_that_sections_tables() -> None:
    gateway = Router("People")
    state = _state()
    result = await scope(state, _deps(gateway))

    assert result.status == "OK"
    assert result.detail == "People · 2 tables"
    assert state.scope_sections == ["People"]
    # Snapshot order, not the section's.
    assert state.scope_tables == ["public.customers", "public.customer_addresses"]

    await retrieve(state, _deps(gateway))
    assert state.context is not None
    assert state.context.strategy == "SECTION_SNAPSHOT"
    assert [f"public.{t['name']}" for t in state.context.tables] == state.scope_tables


async def test_two_sections_are_the_union_in_the_routers_order() -> None:
    state = _state("do customers who buy tagged products order more?")
    result = await scope(state, _deps(Router("Catalog, People")))

    assert state.scope_sections == ["Catalog", "People"]
    assert state.scope_tables == [
        "public.customers", "public.customer_addresses", "public.products", "public.tags",
    ]
    assert result.detail == "Catalog, People · 4 tables"


async def test_the_router_sees_every_routable_section_and_the_question() -> None:
    gateway = Router("Sales")
    await scope(_state("total revenue last month"), _deps(gateway))

    (system, user), = gateway.messages
    assert system.role == "system" and user.role == "user"
    assert user.content == "total revenue last month"
    assert system.content == SCOPE_SYSTEM.format(sections="\n".join([
        "- Sales — Orders and their lines.",
        "- People — Customers and where they live.",
        "- Catalog — Products and their tags.",
    ]))
    # A section with nothing left in the schema can never be picked, so the
    # router is not told about it.
    assert "Archive" not in system.content


async def test_a_long_description_is_clipped_in_the_prompt() -> None:
    gateway = Router("Sales")
    long = [SectionSpec("Sales", "word " * 400, ("public.orders",))]
    await scope(_state(), _deps(gateway, long))
    line = gateway.messages[0][0].content.split("Sections:\n")[1].split("\n")[0]
    assert len(line) < 420 and line.endswith("…")


def test_the_follow_up_prompt_names_the_current_section() -> None:
    """Stickiness is Phase 3; the prompt it will send exists now, as a second
    prompt rather than an empty slot in the first."""
    rendered = SCOPE_SYSTEM_WITH_CURRENT.format(sections="- Sales — x", current="Sales")
    assert "Currently answering from: Sales" in rendered
    assert "Currently answering" not in SCOPE_SYSTEM.format(sections="- Sales — x")


@pytest.mark.parametrize(("reply", "picked"), [
    ("Sales", ["Sales"]),
    ("sales", ["Sales"]),
    ("**Sales**.", ["Sales"]),
    ("- People\n- Sales", ["People", "Sales"]),
    ('"Catalog", "Sales"', ["Catalog", "Sales"]),
    ("Sales, Sales, People", ["Sales", "People"]),
    ("Sales, People, Catalog, Ops", ["Sales", "People", "Catalog"]),  # at most three
    ("Catalog, NONE", ["Catalog"]),
    ("NONE", []),
    ("", []),
    ("I think it is about marketing.", []),
])
def test_a_reply_is_read_leniently_and_never_invents_a_section(
    reply: str, picked: list[str]
) -> None:
    names = ["Sales", "People", "Catalog", "Ops"]
    assert parse_scope_reply(reply, names) == picked


def test_a_name_that_ends_in_a_full_stop_still_matches() -> None:
    assert parse_scope_reply("Acme Inc.", ["Acme Inc."]) == ["Acme Inc."]


# ── the six ways it falls open ───────────────────────────────────────────
FALL_OPEN = [
    # (label, gateway, sections, intent, detail, a call was made and paid for)
    ("reply NONE", Router("NONE"), SECTIONS, "ANALYTICAL", "No section matched", True),
    ("an unknown name", Router("Marketing"), SECTIONS, "ANALYTICAL",
     "No section matched", True),
    ("an empty reply", Router(""), SECTIONS, "ANALYTICAL", "No section matched", True),
    ("a provider error", Router(raises=True), SECTIONS, "ANALYTICAL",
     "Skipped — provider error", False),
    ("no sections", Router("Sales"), None, "ANALYTICAL", None, False),
    ("a schema question", Router("Sales"), SECTIONS, "METADATA",
     "Skipped — a schema question is about the whole database", False),
]


@pytest.mark.parametrize(
    ("label", "gateway", "sections", "intent", "detail", "paid"), FALL_OPEN,
    ids=[case[0] for case in FALL_OPEN],
)
async def test_every_failure_produces_the_unscoped_table_set(
    label: str, gateway: Router, sections: Any, intent: str, detail: str | None,
    paid: bool,
) -> None:
    state = _state(intent=intent)
    result = await scope(state, _deps(gateway, sections))

    assert result.status == "SKIPPED", label
    assert result.detail == detail
    assert state.scope_sections == [] and state.scope_tables == []
    # Tokens are recorded exactly when a reply came back — a NONE was still
    # paid for; a provider error and a call never made cost nothing.
    assert ("scope" in state.node_usage) is paid
    if not paid and not gateway.raises:
        assert gateway.messages == [], "no model call when there is nothing to ask"

    await retrieve(state, _deps(gateway, sections))
    assert state.context is not None
    assert [f"public.{t['name']}" for t in state.context.tables] == ALL
    assert state.context.strategy == "FULL_SNAPSHOT"


async def test_nothing_routable_is_no_call_at_all() -> None:
    gateway = Router("Archive")
    result = await scope(_state(), _deps(gateway, [SECTIONS[3]]))
    assert result.status == "SKIPPED"
    assert gateway.messages == []


# ── in a run: the trail shows the pick, and the tokens are the node's ────
class ScopingGateway(ScriptedGateway):
    """`route` gets its label, `scope` gets a section name."""

    def __init__(self, *, section: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.section = section

    async def complete(self, llm: Any, messages: Any) -> Any:
        if "which part of a database" in messages[0].content:
            return await Router(self.section).complete(llm, messages)
        return await super().complete(llm, messages)


async def _run(sections: list[SectionSpec] | None) -> list[tuple[str, str, str | None, Any]]:
    steps: list[tuple[str, str, str | None, Any]] = []

    async def on_step(
        _seq: int, name: str, status: str, detail: str | None, _ms: int, usage: Any = None
    ) -> None:
        if status != StepStatus.RUNNING:
            steps.append((name, status, detail, usage))

    async def emit(_t: str, _d: Any) -> None:
        return None

    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="What was total revenue?",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    snapshot = {"dialect": "postgres", "relationships": [], "tables": [
        {"schema": "public", "name": "orders", "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "total_amount", "data_type": "numeric"},
        ]},
        {"schema": "public", "name": "customers", "columns": [
            {"name": "id", "data_type": "bigint"}, {"name": "name", "data_type": "text"},
        ]},
    ]}
    deps = NodeDeps(
        llm_gateway=ScopingGateway(section="Sales", sql=[SQL_TOTAL]), llm=None,
        connector=ScriptedConnector([ONE_ROW]), snapshot=snapshot, history=[],
        policy=POLICY, emit=emit, sections=sections,
    )
    await AnalyticsPipeline(on_step=on_step).run(state, deps)
    assert state.error is None
    return steps


async def test_the_step_trail_names_the_pick_and_records_its_tokens() -> None:
    steps = await _run([SectionSpec("Sales", "Orders.", ("public.orders",))])
    (step,) = [s for s in steps if s[0] == "scope"]
    _name, status, detail, usage = step
    assert (status, detail) == (StepStatus.DONE, "Sales · 1 table")
    assert usage is not None and usage.calls == 1 and usage.prompt_tokens == 120
    (retrieved,) = [s for s in steps if s[0] == "retrieve"]
    assert retrieved[2] == "1 tables via SECTION_SNAPSHOT"


async def test_a_connection_without_sections_writes_a_silent_skip() -> None:
    """No detail and no usage: the trail hides a scope step with nothing to
    say, so a connection without sections looks exactly as it did."""
    steps = await _run(None)
    (step,) = [s for s in steps if s[0] == "scope"]
    assert step[1:] == (StepStatus.SKIPPED, None, None)


def test_the_node_is_exported_where_the_graph_reads_it() -> None:
    assert nodes.scope is scope
