"""`retrieve`, told which section the question is about.

`docs/plans/retrieval-sections.md` §3.4. The eval fixtures have no sections and
fit the budget whole, so none of this is visible to the suite; recall against a
known-correct section is asserted here instead, over the same synthetic
500-table warehouse `test_retrieve_budget.py` bounds — a hub 400 tables
reference, and 99 that reference nothing.

* A section that fits is sent **whole**: `SECTION_SNAPSHOT`, every column of
  every member — the block the demo fixtures get.
* One that does not is ranked and cut **within the section**: `RANKED_MATCH`,
  never reaching past it.
* Either way the tables that join two members travel with it (the join
  closure) — a link or a dimension nobody thought to put in the section.
"""
from __future__ import annotations

from typing import Any

from app.domain.value_objects import DisclosurePolicy
from app.pipeline import nodes
from app.pipeline.metadata import table_chars
from app.pipeline.nodes import retrieve
from tests.unit.test_retrieve_budget import _deps, _rel, _state, _table, warehouse

BUDGET = nodes._RETRIEVE_BUDGET_CHARS


def _keys(tables: list[dict[str, Any]]) -> list[str]:
    return [f"{t['schema']}.{t['name']}" for t in tables]


def _scoped(question: str, members: list[str], *, intent: str = "ANALYTICAL",
            snapshot: dict[str, Any] | None = None) -> Any:
    state = _state(question, intent)
    state.scope_sections = ["Section"]
    state.scope_tables = members
    return state, snapshot or warehouse()


SMALL = [f"public.spoke_{i:03d}" for i in range(10)]        # 10 × 460 chars
LARGE = [f"public.spoke_{i:03d}" for i in range(200)]       # 200 × 460 chars


async def test_a_section_that_fits_is_sent_whole() -> None:
    state, snapshot = _scoped("totals by week", SMALL)
    result = await retrieve(state, _deps(snapshot))

    assert state.context is not None
    assert state.context.strategy == "SECTION_SNAPSHOT"
    by_key = dict(zip(_keys(snapshot["tables"]), snapshot["tables"], strict=True))
    got = dict(zip(_keys(state.context.tables), state.context.tables, strict=True))
    # Every member, every column — the table dicts themselves, not a summary.
    for member in SMALL:
        assert got[member] == by_key[member]
    assert state.context.dropped_tables == []
    assert result.detail == f"{len(got)} tables via SECTION_SNAPSHOT"
    assert len(state.context.render(DisclosurePolicy.NONE)) <= BUDGET


async def test_the_join_closure_rides_along_and_nothing_else_does() -> None:
    """Every spoke references the hub, so the hub joins two members: it is part
    of how the section is queried. An island joins nothing; a spoke outside the
    section joins only the hub — neither is in the block."""
    state, snapshot = _scoped("totals by week", SMALL)
    await retrieve(state, _deps(snapshot))

    assert state.context is not None
    assert set(_keys(state.context.tables)) == {*SMALL, "public.hub"}
    rels = state.context.relationships
    assert rels and all(r["to_table"] == "public.hub" for r in rels)


async def test_a_link_table_outside_the_section_is_a_bridge() -> None:
    snapshot = warehouse()
    snapshot["tables"].append(_table("spoke_link", refs=("spoke_000", "spoke_001")))
    snapshot["relationships"] += [_rel("spoke_link", "spoke_000"), _rel("spoke_link", "spoke_001")]
    # And one that touches a single member only: the edge of the section.
    snapshot["tables"].append(_table("spoke_note", refs=("spoke_000",)))
    snapshot["relationships"].append(_rel("spoke_note", "spoke_000"))

    state, _ = _scoped("totals by week", SMALL, snapshot=snapshot)
    await retrieve(state, _deps(snapshot))

    assert state.context is not None
    keys = set(_keys(state.context.tables))
    assert "public.spoke_link" in keys
    assert "public.spoke_note" not in keys


async def test_a_section_too_large_is_ranked_within_the_section() -> None:
    state, snapshot = _scoped("compare spoke 150 with spoke 042", LARGE)
    result = await retrieve(state, _deps(snapshot))

    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    keys = set(_keys(state.context.tables))
    # Nothing from outside the section and its closure — not an island, not a
    # spoke the section does not hold — however the ranking went.
    assert keys <= {*LARGE, "public.hub"}
    # What the question named, and the one hop out from it — which here is
    # the hub the two spokes join through.
    assert keys == {"public.spoke_150", "public.spoke_042", "public.hub"}
    assert result.detail == "3 tables via RANKED_MATCH"


async def test_nothing_named_in_a_large_section_is_cut_inside_it() -> None:
    state, snapshot = _scoped("how are we doing?", LARGE)
    result = await retrieve(state, _deps(snapshot))

    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    assert set(_keys(state.context.tables)) <= {*LARGE, "public.hub"}
    assert sum(table_chars(t) for t in state.context.tables) <= BUDGET
    # The cut happened inside the section, and the trail says how much it took.
    dropped = state.context.dropped_tables
    assert dropped and set(dropped) <= set(LARGE)
    assert len(state.context.tables) + len(dropped) == len(LARGE) + 1
    assert result.detail is not None and f"{len(dropped)} not shown" in result.detail


async def test_a_schema_question_ignores_any_scope() -> None:
    """`census` states the whole database's total, so a METADATA question is
    described from the whole snapshot even if a scope somehow reached it."""
    state, snapshot = _scoped("what is in this database?", SMALL, intent="METADATA")
    await retrieve(state, _deps(snapshot))

    assert state.context is not None
    assert state.context.strategy == "SCHEMA_QUESTION"
    assert not set(_keys(state.context.tables)) <= {*SMALL, "public.hub"}


async def test_no_scope_is_the_whole_snapshot_as_before() -> None:
    state = _state("show the hub")
    await retrieve(state, _deps(warehouse()))

    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    assert any(k.startswith("public.spoke_2") for k in _keys(state.context.tables))
