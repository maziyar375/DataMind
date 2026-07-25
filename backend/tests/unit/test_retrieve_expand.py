"""Foreign-key neighbour expansion in the retrieve node.

When the schema is too big for a full snapshot, exact-name matching finds the
entity tables a question names but never the junction/bridge tables that join
them. `_expand_by_fk` grows the matched set by one FK hop so those bridges reach
the generator.
"""
from __future__ import annotations

from app.pipeline.nodes import _expand_by_fk


def _t(name: str) -> dict:
    return {"schema": "public", "name": name, "columns": []}


_TABLES = [_t("orders"), _t("products"), _t("order_items"), _t("customers"), _t("tags")]
_RELS = [
    # order_items bridges orders<->products
    {"from_table": "public.order_items", "from_column": "order_id",
     "to_table": "public.orders", "to_column": "id"},
    {"from_table": "public.order_items", "from_column": "product_id",
     "to_table": "public.products", "to_column": "id"},
    # orders -> customers
    {"from_table": "public.orders", "from_column": "customer_id",
     "to_table": "public.customers", "to_column": "id"},
]


def _names(tables: list[dict]) -> set[str]:
    return {f"{t['schema']}.{t['name']}" for t in tables}


def test_bridge_table_pulled_in_from_named_entity() -> None:
    # Question named "orders"; order_items and customers are one hop away.
    out = _names(_expand_by_fk([_t("orders")], _TABLES, _RELS))
    assert out == {"public.orders", "public.order_items", "public.customers"}
    # products is two hops (orders -> order_items -> products) and must NOT appear.
    assert "public.products" not in out


def test_expansion_is_exactly_one_hop_regardless_of_rel_order() -> None:
    # Reversing relationship order must not leak a second hop in.
    a = _names(_expand_by_fk([_t("orders")], _TABLES, _RELS))
    b = _names(_expand_by_fk([_t("orders")], _TABLES, list(reversed(_RELS))))
    assert a == b


def test_unrelated_table_is_not_added() -> None:
    out = _names(_expand_by_fk([_t("orders")], _TABLES, _RELS))
    assert "public.tags" not in out  # no FK path from orders


def test_empty_seed_yields_empty() -> None:
    assert _expand_by_fk([], _TABLES, _RELS) == []


def test_result_preserves_snapshot_order() -> None:
    out = _expand_by_fk([_t("orders"), _t("order_items")], _TABLES, _RELS)
    names = [t["name"] for t in out]
    assert names == sorted(names, key=lambda n: [t["name"] for t in _TABLES].index(n))
