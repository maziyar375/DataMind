"""The proposal: a complete, deterministic division of a snapshot into sections.

Sections are proposed by the system and corrected by a person — never authored
from a blank form (`docs/plans/retrieval-sections.md` D4). So the proposal has
to be good enough to ship as it stands, and two properties are not negotiable:

* **deterministic** — the same snapshot in, the same sections out, whatever
  order the relationships arrive in;
* **total** — every table in the snapshot is in exactly one place, a section
  or `Unassigned`, or the screen is lying.
"""
from __future__ import annotations

import random
from typing import Any

import pytest

from app.pipeline import sections as algo
from app.pipeline.metadata import table_chars

BUDGET = 50_000


def _t(name: str, *, schema: str = "public", columns: int = 6, rows: int = 0,
       comment: str = "") -> dict[str, Any]:
    return {
        "schema": schema, "name": name, "approx_row_count": rows,
        "comment": comment,
        "columns": [{"name": f"c{i}", "data_type": "text"} for i in range(columns)],
    }


def _fk(source: str, target: str, schema: str = "public") -> dict[str, str]:
    return {"from_table": f"{schema}.{source}", "from_column": f"{target}_id",
            "to_table": f"{schema}.{target}", "to_column": "id"}


def _placed(proposal: algo.Proposal) -> list[str]:
    return [t for s in proposal.sections for t in s.tables] + list(proposal.unassigned)


def _commerce() -> dict[str, Any]:
    """Two joined domains in one schema, a second schema, and three loners."""
    tables = [
        _t("orders", rows=5000), _t("order_items", rows=20000), _t("customers", rows=900),
        _t("payments", rows=4800),
        _t("products", rows=300), _t("product_tags"), _t("tags"), _t("suppliers"),
        _t("audit_log"), _t("settings"), _t("feature_flags"),
        _t("employees", schema="hr"), _t("departments", schema="hr"),
        _t("salaries", schema="hr"),
    ]
    rels = [
        _fk("order_items", "orders"), _fk("orders", "customers"), _fk("payments", "orders"),
        _fk("product_tags", "products"), _fk("product_tags", "tags"),
        _fk("products", "suppliers"),
        _fk("employees", "departments", "hr"), _fk("salaries", "employees", "hr"),
    ]
    return {"tables": tables, "relationships": rels}


# ── determinism and totality ─────────────────────────────────────────────
def test_the_same_snapshot_gives_the_same_sections() -> None:
    snapshot = _commerce()
    first = algo.propose(snapshot, budget_chars=BUDGET)
    for seed in range(5):
        shuffled = dict(snapshot)
        rels = list(snapshot["relationships"])
        random.Random(seed).shuffle(rels)
        shuffled["relationships"] = rels
        assert algo.propose(shuffled, budget_chars=BUDGET) == first


def test_every_table_is_in_exactly_one_place() -> None:
    snapshot = _commerce()
    proposal = algo.propose(snapshot, budget_chars=BUDGET)
    placed = _placed(proposal)
    assert sorted(placed) == sorted(algo.key(t) for t in snapshot["tables"])
    assert len(placed) == len(set(placed))


def test_unassigned_holds_what_joins_nothing() -> None:
    proposal = algo.propose(_commerce(), budget_chars=BUDGET)
    assert proposal.unassigned == (
        "public.audit_log", "public.settings", "public.feature_flags",
    )


def test_fk_components_become_sections_and_schemas_never_mix() -> None:
    proposal = algo.propose(_commerce(), budget_chars=BUDGET)
    groups = {frozenset(s.tables) for s in proposal.sections}
    assert frozenset({
        "public.orders", "public.order_items", "public.customers", "public.payments",
    }) in groups
    assert frozenset({
        "public.products", "public.product_tags", "public.tags", "public.suppliers",
    }) in groups
    assert frozenset({"hr.employees", "hr.departments", "hr.salaries"}) in groups
    for section in proposal.sections:
        assert len({t.split(".")[0] for t in section.tables}) == 1


def test_names_come_from_the_schema_the_prefix_or_the_largest_table() -> None:
    names = {frozenset(s.tables): s.name for s in algo.propose(
        _commerce(), budget_chars=BUDGET
    ).sections}
    # A schema with one section, and a name worth reading: the schema.
    assert names[frozenset({"hr.employees", "hr.departments", "hr.salaries"})] == "Hr"
    # Half or more share a leading word: that word.
    assert names[frozenset({
        "public.orders", "public.order_items", "public.customers", "public.payments",
    })] == "Order"
    # No shared word: the largest table.
    assert names[frozenset({
        "public.products", "public.product_tags", "public.tags", "public.suppliers",
    })] == "Product"


def test_proposed_names_are_unique_case_insensitively() -> None:
    snapshot = {
        "tables": [
            *[_t(f"order_{i}", schema="sales") for i in range(3)],
            *[_t(f"order_{i}", schema="archive") for i in range(3)],
        ],
        "relationships": [],
    }
    names = [s.name for s in algo.propose(snapshot, budget_chars=BUDGET).sections]
    assert len({n.lower() for n in names}) == len(names) == 2
    assert all(algo.valid_name(n) is None for n in names)


# ── the oversized component ──────────────────────────────────────────────
def _hub_warehouse(per_prefix: int = 60) -> dict[str, Any]:
    """300 tables in one FK component — every one joins the hub — named in
    five families, as a real warehouse is."""
    tables = [_t("hub", columns=4, rows=10**6)]
    rels = []
    families = ["order", "billing", "inventory", "shipping"]
    for family in families:
        for i in range(per_prefix):
            name = f"{family}_{i:02d}"
            tables.append(_t(name, columns=12))
            rels.append(_fk(name, "hub"))
    # A family too small to stand alone, joined to billing.
    for i in range(2):
        tables.append(_t(f"refund_{i}", columns=3))
        rels.append(_fk(f"refund_{i}", "billing_00"))
    # A prefix whose own tables are over budget, which is not split further.
    for i in range(57):
        name = f"ledger_{i:02d}"
        tables.append(_t(name, columns=30))
        rels.append(_fk(name, "hub"))
    return {"tables": tables, "relationships": rels}


def test_a_hub_component_is_split_by_prefix() -> None:
    snapshot = _hub_warehouse()
    assert len(snapshot["tables"]) == 300
    proposal = algo.propose(snapshot, budget_chars=BUDGET)
    names = {s.name for s in proposal.sections}
    assert {"Order", "Billing", "Inventory", "Shipping", "Ledger"} <= names
    assert sorted(_placed(proposal)) == sorted(algo.key(t) for t in snapshot["tables"])


def test_no_proposed_section_is_over_budget_unless_its_prefix_is() -> None:
    snapshot = _hub_warehouse()
    by_key = {algo.key(t): t for t in snapshot["tables"]}
    for section in algo.propose(snapshot, budget_chars=BUDGET).sections:
        if algo.fit(section.tables, by_key, BUDGET) != "TOO_LARGE":
            continue
        # Over budget only as a whole family, never because something was
        # attached to it.
        families = {algo.prefix(t.split(".")[1]) for t in section.tables}
        assert families == {"ledger"}, section.name
        assert sum(table_chars(by_key[t]) for t in section.tables) > BUDGET


def test_a_rare_prefix_joins_the_family_it_has_keys_to() -> None:
    proposal = algo.propose(_hub_warehouse(), budget_chars=BUDGET)
    billing = next(s for s in proposal.sections if s.name == "Billing")
    assert {"public.refund_0", "public.refund_1"} <= set(billing.tables)


def test_split_this_section_divides_only_its_members() -> None:
    snapshot = _hub_warehouse()
    members = [algo.key(t) for t in snapshot["tables"]
               if t["name"].startswith(("order_", "billing_"))]
    proposal = algo.propose(snapshot, budget_chars=BUDGET, only=members)
    assert {s.name for s in proposal.sections} == {"Order", "Billing"}
    assert sorted(_placed(proposal)) == sorted(members)


# ── descriptions ─────────────────────────────────────────────────────────
def test_the_layers_label_and_grain_reach_the_description() -> None:
    snapshot = _commerce()
    semantic = {"entities": [
        {"table": "public.orders", "label": "Orders", "grain": "one row per order",
         "description": "Every purchase a customer placed.",
         "synonyms": ["sales", "purchases"]},
        {"table": "public.order_items", "label": "Order lines",
         "grain": "one row per product in an order"},
        # Excluded on purpose, so it says nothing.
        {"table": "public.payments", "label": "SECRET", "exclude": True},
    ]}
    proposal = algo.propose(snapshot, semantic=semantic, budget_chars=BUDGET)
    orders = next(s for s in proposal.sections if "public.orders" in s.tables)
    assert "Orders (one row per order): Every purchase a customer placed" in (
        orders.description
    )
    assert "Order lines (one row per product in an order)" in orders.description
    assert "Also called: sales, purchases." in orders.description
    assert "SECRET" not in orders.description


def test_no_layer_falls_back_to_names_and_catalog_comments() -> None:
    snapshot = _commerce()
    snapshot["tables"][0]["comment"] = "One row per checkout. Excludes test orders."
    proposal = algo.propose(snapshot, budget_chars=BUDGET)
    orders = next(s for s in proposal.sections if "public.orders" in s.tables)
    assert orders.description.startswith("Order items")  # largest first
    assert "Orders: One row per checkout. Excludes test orders" in orders.description
    assert "Customers" in orders.description


def test_a_description_is_whole_items_and_bounded() -> None:
    snapshot = {
        "tables": [_t(f"table_number_{i:03d}", comment="x " * 60) for i in range(40)],
        "relationships": [_fk(f"table_number_{i:03d}", "table_number_000")
                          for i in range(1, 40)],
    }
    (section,) = algo.propose(snapshot, budget_chars=BUDGET).sections
    assert len(section.description) <= 400
    assert section.description.endswith("more tables.")


def test_a_description_never_reads_row_values() -> None:
    """Section text travels to a model under every disclosure policy, so a
    proposal reads names, comments and the layer — never a sampled value."""
    snapshot = _commerce()
    snapshot["tables"][0]["columns"][0]["sample_values"] = ["TOP-SECRET-VALUE"]
    snapshot["tables"][0]["columns"][0]["min_value"] = "TOP-SECRET-MIN"
    for section in algo.propose(snapshot, budget_chars=BUDGET).sections:
        assert "TOP-SECRET" not in section.description


# ── sizing ───────────────────────────────────────────────────────────────
def test_the_estimator_is_retrieves() -> None:
    tables = {algo.key(t): t for t in [_t("a", columns=10), _t("b", columns=2)]}
    assert algo.section_chars(["public.a", "public.b"], tables) == (
        table_chars(tables["public.a"]) + table_chars(tables["public.b"])
    )
    # A member the snapshot no longer has weighs nothing.
    assert algo.section_chars(["public.a", "public.gone"], tables) == (
        table_chars(tables["public.a"])
    )


@pytest.mark.parametrize(("members", "budget", "state"), [
    (["public.a"], 10_000, "FITS"),
    (["public.a", "public.b"], table_chars(_t("a", columns=10)), "TOO_LARGE"),
    ([], 10_000, "EMPTY"),
    (["public.gone"], 10_000, "EMPTY"),
])
def test_the_three_states(members: list[str], budget: int, state: str) -> None:
    tables = {algo.key(t): t for t in [_t("a", columns=10), _t("b", columns=2)]}
    assert algo.fit(members, tables, budget) == state


@pytest.mark.parametrize(("name", "ok"), [
    ("Sales", True),
    ("Sales, marketing", False),
    ("Two\nlines", False),
    ("", False),
    ("Unassigned", False),
    ("none", False),
    ("x" * 61, False),
])
def test_what_a_section_may_be_called(name: str, ok: bool) -> None:
    assert (algo.valid_name(name) is None) is ok
