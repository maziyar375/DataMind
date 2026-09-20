"""The analytical branch's matcher: token boundaries, not substrings.

`retrieve` used to seed on `name in question` for every table and column name,
with no token boundary. On a warehouse where most tables carry an `id` column,
"how many orders were paid last month?" matched every one of them — `id` sits
inside "pa-id" — and the FK hop then amplified that to the whole schema. The
branch now calls `metadata.match_tables`, the matcher `describe` already used,
extended to columns.

`docs/plans/retrieval-sections.md` §4.1.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.pipeline import nodes
from app.pipeline.metadata import match_tables
from app.pipeline.nodes import NodeDeps, retrieve
from app.pipeline.state import RunState


def _t(name: str, *columns: str, rows: int = 0) -> dict[str, Any]:
    return {
        "schema": "public",
        "name": name,
        "approx_row_count": rows,
        "columns": [{"name": c, "data_type": "text"} for c in columns],
    }


TABLES = [
    _t("orders", "id", "customer_id", "status", "paid_at"),
    _t("customers", "id", "name", "region"),
    _t("customer_addresses", "id", "customer_id", "city"),
    _t("products", "id", "title"),
    _t("shipments", "id", "order_id", "carrier"),
]


def _names(tables: list[dict[str, Any]]) -> set[str]:
    return {t["name"] for t in tables}


# ── the matcher ──────────────────────────────────────────────────────────
def test_paid_names_no_table_through_an_id_column() -> None:
    """The bug this replaced: `'id' in "…paid…"` is True."""
    assert match_tables("how many were paid last month?", TABLES, columns=True) == []


def test_a_two_letter_column_is_never_a_form() -> None:
    assert match_tables("show me every id", TABLES, columns=True) == []


def test_a_column_named_the_way_a_person_says_it_names_its_table() -> None:
    assert _names(match_tables("revenue by region", TABLES, columns=True)) == {
        "customers"
    }
    # snake_case spoken as two words, the same courtesy table names get.
    assert _names(match_tables("orders by paid at date", TABLES, columns=True)) >= {
        "orders"
    }


def test_columns_are_off_unless_asked_for() -> None:
    """`describe` and `select_tables` keep the table-only matcher: a schema
    question is about tables, and "which tables have a region?" is not a
    request to describe each of them."""
    assert match_tables("revenue by region", TABLES) == []


def test_customer_addresses_names_that_table_and_not_customers() -> None:
    """The specific name wins over the general one it contains."""
    found = _names(match_tables("list customer addresses", TABLES, columns=True))
    assert "customer_addresses" in found
    assert "customers" not in found


# ── through the node ─────────────────────────────────────────────────────
def _state(question: str) -> RunState:
    return RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )


def _deps(snapshot: dict[str, Any]) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=snapshot, history=[], policy=None, emit=emit,  # type: ignore[arg-type]
    )


def _wide() -> dict[str, Any]:
    """TABLES plus enough `id`-carrying filler that the snapshot is over budget
    at the lowered ceiling, so the analytical branch is the one that runs."""
    filler = [_t(f"ledger_{i:03d}", "id", "amount") for i in range(60)]
    return {"tables": [*TABLES, *filler], "relationships": []}


@pytest.mark.asyncio
async def test_paid_does_not_select_every_table_with_an_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nodes, "_RETRIEVE_BUDGET_CHARS", 1_000)
    snapshot = _wide()
    state = _state("how many orders were paid last month?")
    await retrieve(state, _deps(snapshot))

    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    selected = _names(state.context.tables)
    assert "orders" in selected
    # The old matcher selected all 65; the budget alone would not explain
    # `orders` coming first, the ranking does — it was the one table named.
    assert len(selected) < len(snapshot["tables"])


@pytest.mark.asyncio
async def test_customer_addresses_reaches_the_block_before_customers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Room for one table: it has to be the one the question named."""
    monkeypatch.setattr(nodes, "_RETRIEVE_BUDGET_CHARS", 200)
    state = _state("list customer addresses")
    await retrieve(state, _deps(_wide()))

    assert state.context is not None
    assert _names(state.context.tables) == {"customer_addresses"}
