"""Every branch of `retrieve` fits the budget — including the one that used not to.

`FULL_SNAPSHOT` *is* the budget test and `SCHEMA_QUESTION` spends it explicitly,
but the analytical branch emitted whatever the FK hop produced, and nothing
downstream clamped it: `RetrievedContext.render` caps comments and the semantic
block but renders the table list whole. On a warehouse with a hub table, one
question naming the hub selected its every neighbour.

The eval suite cannot see any of this — both fixtures fit under the ceiling and
take `FULL_SNAPSHOT` on every question — so the gate is a synthetic 500-table
snapshot here, with a hub that 400 tables reference.

`docs/plans/retrieval-sections.md` §4.2–4.4.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.domain.value_objects import DisclosurePolicy
from app.pipeline import nodes
from app.pipeline.metadata import table_chars
from app.pipeline.nodes import NodeDeps, fit_to_budget, retrieve
from app.pipeline.state import RunState

BUDGET = nodes._RETRIEVE_BUDGET_CHARS


def _table(name: str, *, refs: tuple[str, ...] = (), extra: int = 8,
           rows: int = 0) -> dict[str, Any]:
    columns: list[dict[str, Any]] = [
        {"name": "id", "data_type": "bigint", "is_primary_key": True},
    ]
    for ref in refs:
        columns.append({
            "name": f"{ref}_id", "data_type": "bigint",
            "is_foreign_key": True, "references": f"public.{ref}.id",
        })
    columns += [{"name": f"{name}_c{i}", "data_type": "text"} for i in range(extra)]
    return {"schema": "public", "name": name, "approx_row_count": rows,
            "columns": columns}


def _rel(source: str, target: str) -> dict[str, str]:
    return {"from_table": f"public.{source}", "from_column": f"{target}_id",
            "to_table": f"public.{target}", "to_column": "id"}


def warehouse() -> dict[str, Any]:
    """500 tables: a hub 400 of them reference, and 99 that reference nothing.

    Every table carries an `id` column, which is what made the old substring
    matcher select all of them for any question containing "paid".
    """
    tables = [_table("hub", rows=1_000_000)]
    rels: list[dict[str, str]] = []
    for i in range(400):
        name = f"spoke_{i:03d}"
        tables.append(_table(name, refs=("hub",), rows=i))
        rels.append(_rel(name, "hub"))
    for i in range(99):
        tables.append(_table(f"island_{i:03d}", rows=10_000 + i))
    assert len(tables) == 500
    assert sum(table_chars(t) for t in tables) > BUDGET
    return {"tables": tables, "relationships": rels}


def _state(question: str, intent: str = "ANALYTICAL") -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.intent = intent  # type: ignore[assignment]
    return state


def _deps(snapshot: dict[str, Any], history: list[dict[str, str]] | None = None) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=snapshot, history=history or [], policy=None,  # type: ignore[arg-type]
        emit=emit,
    )


# ── every branch, bounded ────────────────────────────────────────────────
BRANCHES = [
    # (question, intent, history, expected strategy)
    ("show the hub", "ANALYTICAL", [], "RANKED_MATCH"),          # 400-neighbour hop
    ("totals for spoke 007", "ANALYTICAL", [], "RANKED_MATCH"),  # a leaf; hop is the hub
    ("how many were paid last month?", "ANALYTICAL", [], "RANKED_MATCH"),  # nothing named
    ("and by month?", "ANALYTICAL", [                            # a follow-up
        {"role": "assistant", "content": "…",
         "sql": "SELECT count(*) FROM public.hub h"},
    ], "RANKED_MATCH"),
    ("what is in this database?", "METADATA", [], "SCHEMA_QUESTION"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "intent", "history", "strategy"), BRANCHES)
async def test_every_branch_fits_the_budget(
    question: str, intent: str, history: list[dict[str, str]], strategy: str
) -> None:
    state = _state(question, intent)
    result = await retrieve(state, _deps(warehouse(), history))

    assert state.context is not None
    assert state.context.strategy == strategy
    # The estimate `retrieve` decides with, and the bytes the model is sent.
    assert sum(table_chars(t) for t in state.context.tables) <= BUDGET
    assert len(state.context.render(DisclosurePolicy.NONE)) <= BUDGET
    # What did not fit is counted, and counted honestly.
    dropped = state.context.dropped_tables
    assert len(state.context.tables) + len(dropped) <= 500
    if dropped:
        assert f"{len(dropped)} not shown" in (result.detail or "")
    else:
        assert "not shown" not in (result.detail or "")


@pytest.mark.asyncio
async def test_the_hub_neighbourhood_is_cut_and_the_cut_is_reported() -> None:
    state = _state("show the hub")
    result = await retrieve(state, _deps(warehouse()))

    assert state.context is not None
    kept, dropped = len(state.context.tables), len(state.context.dropped_tables)
    # The hub and its 400 neighbours were the candidates; about a hundred fit.
    assert kept + dropped == 401
    assert 50 < kept < 200
    assert result.detail is not None
    assert result.detail.startswith(f"{kept} tables via RANKED_MATCH · {dropped} not shown")


@pytest.mark.asyncio
async def test_a_snapshot_that_fits_is_sent_whole_and_drops_nothing() -> None:
    snapshot = warehouse()
    small = {"tables": snapshot["tables"][:20], "relationships": snapshot["relationships"][:19]}
    state = _state("show the hub")
    result = await retrieve(state, _deps(small))

    assert state.context is not None
    assert state.context.strategy == "FULL_SNAPSHOT"
    assert len(state.context.tables) == 20
    assert state.context.dropped_tables == []
    assert "not shown" not in (result.detail or "")


@pytest.mark.asyncio
async def test_the_named_table_survives_the_hub_cut() -> None:
    """The hub has 400 neighbours and room for about a hundred. `spoke_000` is
    the smallest of them and would be cut first on size — but the question
    named it, and a named table is never what the cut takes."""
    state = _state("compare the hub with spoke 000")
    await retrieve(state, _deps(warehouse()))

    assert state.context is not None
    names = {t["name"] for t in state.context.tables}
    assert {"spoke_000", "hub"} <= names
    assert state.context.dropped_tables
    # And nothing unconnected crowded in ahead of the hop.
    assert not any(n.startswith("island_") for n in names)


@pytest.mark.asyncio
async def test_a_follow_up_keeps_the_table_it_continues() -> None:
    history = [{"role": "assistant", "content": "…",
                "sql": "SELECT count(*) FROM public.spoke_399 s"}]
    state = _state("and by month?")
    await retrieve(state, _deps(warehouse(), history))

    assert state.context is not None
    assert "spoke_399" in {t["name"] for t in state.context.tables}


@pytest.mark.asyncio
async def test_at_least_one_table_goes_in_however_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nodes, "_RETRIEVE_BUDGET_CHARS", 10)
    state = _state("show the hub")
    await retrieve(state, _deps(warehouse()))

    assert state.context is not None
    assert [t["name"] for t in state.context.tables] == ["hub"]


@pytest.mark.asyncio
async def test_the_selection_is_rendered_in_snapshot_order() -> None:
    """A stable prompt: ranking decides *which*, never *where*."""
    snapshot = warehouse()
    state = _state("show the hub")
    await retrieve(state, _deps(snapshot))

    assert state.context is not None
    position = {t["name"]: i for i, t in enumerate(snapshot["tables"])}
    got = [position[t["name"]] for t in state.context.tables]
    assert got == sorted(got)


# ── keys and join paths survive the cut ──────────────────────────────────
def _plain(name: str, rows: int = 0) -> dict[str, Any]:
    return _table(name, rows=rows)


def test_a_bridge_between_two_named_tables_outranks_a_bigger_neighbour() -> None:
    """Room for the two named tables and one more. The bridge that joins them
    is the join path the question implies; a neighbour of only one of them is
    not, however many rows it has."""
    orders, products = _plain("orders"), _plain("products")
    bridge = _table("order_items", refs=("orders", "products"))
    neighbour = _table("order_notes", refs=("orders",), rows=9_999_999)
    candidates = [orders, neighbour, bridge, products]
    rels = [_rel("order_items", "orders"), _rel("order_items", "products"),
            _rel("order_notes", "orders")]

    budget = table_chars(orders) + table_chars(products) + table_chars(bridge)
    selected, dropped = fit_to_budget(
        candidates, named=[orders, products], carried=[], by_column=[],
        relationships=rels, budget_chars=budget,
    )
    assert [t["name"] for t in selected] == ["orders", "order_items", "products"]
    assert dropped == ["public.order_notes"]


def test_a_connected_table_outranks_an_unconnected_one() -> None:
    orders = _plain("orders")
    neighbour = _table("order_items", refs=("orders",))
    island = _plain("audit_log", rows=50_000_000)
    rels = [_rel("order_items", "orders")]

    selected, dropped = fit_to_budget(
        [island, orders, neighbour], named=[orders], carried=[], by_column=[],
        relationships=rels,
        budget_chars=table_chars(orders) + table_chars(neighbour),
    )
    assert [t["name"] for t in selected] == ["orders", "order_items"]
    assert dropped == ["public.audit_log"]


def test_named_beats_carried_beats_column_hits() -> None:
    named, carried, by_column = _plain("a", 1), _plain("b", 2), _plain("c", 3)
    each = table_chars(named)

    def pick(room: int) -> list[str]:
        selected, _ = fit_to_budget(
            [by_column, carried, named], named=[named], carried=[carried],
            by_column=[by_column], relationships=[], budget_chars=each * room,
        )
        return sorted(t["name"] for t in selected)

    assert pick(1) == ["a"]
    assert pick(2) == ["a", "b"]
    assert pick(3) == ["a", "b", "c"]


def test_a_wide_table_does_not_shut_out_the_narrow_ones_behind_it() -> None:
    first = _plain("first", rows=3)
    wide = _table("wide", extra=500, rows=2)
    narrow = _plain("narrow", rows=1)

    selected, dropped = fit_to_budget(
        [first, wide, narrow], named=[], carried=[], by_column=[],
        relationships=[], budget_chars=table_chars(first) + table_chars(narrow),
    )
    assert [t["name"] for t in selected] == ["first", "narrow"]
    assert dropped == ["public.wide"]
