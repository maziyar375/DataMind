"""Which signal chose each table, recorded per run — hybrid-retrieval Phase 3.

`0036` recorded *what* retrieval did: the strategy, the sections, how many
tables, how big the block was. It could not answer the question mvp2 **A5** and
**B2** both turn on — *is a curator's word, or a DBA's sentence, actually
deciding what the model sees?* — because after the ranking every table looks
alike.

`runs.retrieval_signals` is that answer, and the property this file exists to
hold is that **it is counted by the same function that ranked them**. A second
reading of the same lists would agree on the day it was written and describe a
ranking the code had stopped performing some months later.

Two more Phase 3 properties are here: the counts are written **only** on the
branch that chooses, and the tables the budget cut are **named** rather than
only counted.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.pipeline import nodes
from app.pipeline.nodes import SIGNALS, NodeDeps, rank_tiers, retrieve
from app.pipeline.relevance import PROSE_FLOOR, TableVector, VectorIndex
from app.pipeline.state import RunState
from tests.unit.test_retrieval_prose import (
    RELS,
    SNAPSHOT,
    _run,
    by_name,
    table,
)

# ── the vocabulary ───────────────────────────────────────────────────────


def test_the_signal_names_are_the_tier_order() -> None:
    """`SIGNALS[i]` is tier `i`. `runs.retrieval_signals` stores these strings,
    so a renamed or reordered one silently splits a distribution in two."""
    assert SIGNALS == ("name", "term", "carried", "column", "fk", "prose", "rest")


def test_every_tier_the_ranker_can_return_has_a_name() -> None:
    tiers, _ = rank_tiers(
        SNAPSHOT,
        named=[by_name("orders")],
        by_term=[by_name("customers")],
        carried=[by_name("products")],
        by_column=[by_name("shipments")],
        scores={"public.order_items": 1.0},
        relationships=[],
    )
    assert max(tiers.values()) < len(SIGNALS)
    assert {SIGNALS[t] for t in tiers.values()} <= set(SIGNALS)


# ── one rule, two readers ────────────────────────────────────────────────


def test_the_ranker_and_the_telemetry_are_the_same_function() -> None:
    """The whole point of extracting `rank_tiers` from `fit_to_budget`: the
    thing that decides the order is the thing that reports it."""
    import inspect

    source = inspect.getsource(nodes.fit_to_budget)
    assert "rank_tiers(" in source
    assert "def tier(" not in source


def test_each_signal_wins_in_its_own_order() -> None:
    """A table found by two signals is filed under the stronger one — the
    precedence `fit_to_budget` ranks by, not a set union."""
    both = by_name("orders")
    tiers, _ = rank_tiers(
        [both], named=[both], by_term=[both], carried=[both], by_column=[both],
        scores={"public.orders": 1.0}, relationships=[],
    )
    assert SIGNALS[tiers["public.orders"]] == "name"

    tiers, _ = rank_tiers(
        [both], named=[], by_term=[both], carried=[both], by_column=[both],
        scores={"public.orders": 1.0}, relationships=[],
    )
    assert SIGNALS[tiers["public.orders"]] == "term"


def test_a_bridge_is_fk_and_a_described_table_is_prose() -> None:
    tiers, touches = rank_tiers(
        SNAPSHOT,
        named=[by_name("orders")], carried=[], by_column=[],
        scores={"public.products": 1.0},
        relationships=RELS,
    )
    assert SIGNALS[tiers["public.shipments"]] == "fk"
    assert touches["public.shipments"] == {"public.orders"}
    assert SIGNALS[tiers["public.products"]] == "prose"


def test_a_table_under_the_floor_is_rest_not_prose() -> None:
    tiers, _ = rank_tiers(
        [by_name("products")], named=[], carried=[], by_column=[],
        scores={"public.products": PROSE_FLOOR - 0.01}, relationships=[],
    )
    assert SIGNALS[tiers["public.products"]] == "rest"


# ── the node ─────────────────────────────────────────────────────────────


def _wide(n: int = 60) -> dict[str, Any]:
    wide_cols = tuple(f"c{j}" for j in range(20))
    tables = [
        *(table(f"filler_{i:02d}", *wide_cols) for i in range(n)),
        *(
            table(
                t["name"], *(c["name"] for c in t["columns"]), *wide_cols,
                comment=t["comment"],
            )
            for t in SNAPSHOT
        ),
    ]
    assert sum(60 + 40 * len(t["columns"]) for t in tables) > nodes._RETRIEVE_BUDGET_CHARS
    return {"tables": tables, "relationships": RELS}


def _deps(snapshot: dict[str, Any], **kw: Any) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=snapshot, history=[], policy=None,  # type: ignore[arg-type]
        emit=emit, include_db_comments=True, **kw,
    )


@pytest.mark.asyncio
async def test_the_counts_add_up_to_what_was_sent() -> None:
    """Every table in the block is accounted for by exactly one signal —
    which is what makes the column a distribution rather than a tally."""
    state = _run("how many orders hold refunds?")
    await retrieve(state, _deps(_wide()))
    assert state.context is not None
    assert sum(state.retrieval_signals.values()) == len(state.context.tables)
    assert state.retrieval_signals["name"] >= 1


@pytest.mark.asyncio
async def test_a_strategy_that_sends_everything_records_no_choice() -> None:
    """NULL rather than zero, and this is where the NULL comes from:
    `FULL_SNAPSHOT` sent every table, so it chose none, and `{}` here would be
    read as "chose nothing"."""
    state = _run("how many orders hold refunds?")
    small = {"tables": [by_name("orders"), by_name("customers")], "relationships": RELS}
    await retrieve(state, _deps(small))
    assert state.context is not None
    assert state.context.strategy == "FULL_SNAPSHOT"
    assert state.retrieval_signals == {}


@pytest.mark.asyncio
async def test_a_schema_question_records_no_choice_either() -> None:
    state = _run("what is in this database?")
    state.intent = "METADATA"  # type: ignore[assignment]
    await retrieve(state, _deps(_wide()))
    assert state.context is not None
    assert state.context.strategy == "SCHEMA_QUESTION"
    assert state.retrieval_signals == {}


@pytest.mark.asyncio
async def test_a_vector_hit_is_filed_apart_from_a_word_hit() -> None:
    """Both are prose, and after the blend they are one number. Only one of
    them is the index, and *"is the embedding index doing anything?"* has no
    other answer.

    The question shares no word with any prose here, so the lexical half is
    0.0 everywhere and anything promoted was promoted by the vector.
    """
    from app.pipeline.relevance import prose_by_table, prose_fingerprint

    wide_cols = tuple(f"c{j}" for j in range(20))
    target = table(
        "warranty_claims", *wide_cols,
        comment="a cancellation the customer chose, not a failed payment",
    )
    snapshot = {
        "tables": [*(table(f"filler_{i:02d}", *wide_cols) for i in range(60)), target],
        "relationships": [],
    }
    key = "public.warranty_claims"
    texts = prose_by_table(snapshot["tables"], include_comments=True)
    assert texts[key]

    async def embed(_texts: Any) -> list[list[float]]:
        return [[1.0, 0.0]]

    index = VectorIndex(
        model="m", dimension=2,
        tables={
            key: TableVector(
                vector=(1.0, 0.0),
                stored_fingerprint=prose_fingerprint(texts[key], "m", 2),
            )
        },
        embed=embed,
    )

    asked = "how many people stopped paying us"
    state = _run(asked)
    await retrieve(state, _deps(snapshot, vectors=index))
    assert state.context is not None
    assert key.split(".")[1] in [t["name"] for t in state.context.tables]
    assert state.retrieval_signals.get("vector") == 1
    assert "prose" not in state.retrieval_signals
    assert sum(state.retrieval_signals.values()) == len(state.context.tables)

    # And with no index at all the same question promotes nothing: the lexical
    # half genuinely scores zero, so the vector is what moved it.
    bare = _run(asked)
    await retrieve(bare, _deps(snapshot))
    assert "vector" not in bare.retrieval_signals
    assert "prose" not in bare.retrieval_signals


@pytest.mark.asyncio
async def test_the_dropped_tables_are_named_in_the_trail() -> None:
    """§6.5 of the research: a thin answer on a wide schema looks identical
    whether the right table was dropped or was never there."""
    # A question that names nothing: no seed, so every table is a candidate
    # and the budget has to cut. A seeded question narrows to the seed and its
    # neighbours, which on this fixture all fit.
    state = _run("zzzqqq entirely unrelated wording")
    result = await retrieve(state, _deps(_wide()))
    detail = result.detail or ""
    assert "not shown (" in detail
    named = detail.split("not shown (")[1].split(")")[0]
    assert named.count(",") <= nodes._DROPPED_NAMED
    assert state.context is not None
    for name in named.split(", "):
        if name.startswith("+"):
            continue
        assert name in state.context.dropped_tables


def test_the_ranker_is_not_accidentally_async() -> None:
    assert not asyncio.iscoroutinefunction(rank_tiers)


def test_a_state_that_never_reached_retrieve_carries_no_signals() -> None:
    """A run answered from the knowledge store skips five nodes. NULL, not
    zero — the rule this schema keeps having to make."""
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="anything",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    assert state.retrieval_signals == {}
