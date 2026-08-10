"""The deterministic half of a schema answer: which tables, and how many.

Two jobs, tested in that order.

`select_tables` and `census` are what the `describe` node builds its prompt
from — the tables a schema question is described from when the snapshot is too
wide to send whole, and the count and names that keep an answer written over
half a schema from being an answer *about* half a schema.

`answer_metadata` is the fallback underneath it, and the behaviour under test
there is granularity: "what tables do I have?" and "what columns does orders
have?" are different questions. The first must not answer with every column of
every table (on the sales fixture that is ~600 names, most of them the same
audit columns repeated), and the second must not answer with a bare inventory.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.metadata import (
    MAX_CENSUS_NAMES,
    MAX_DETAILED_TABLES,
    MAX_LISTED_TABLES,
    answer_metadata,
    census,
    match_tables,
    select_tables,
    table_chars,
)

AUDIT = ["created_by", "updated_by", "src_system", "src_batch_id", "audit_notes"]


def _table(
    name: str, *, rows: int | None = None, columns: list[str] | None = None,
    schema: str = "public",
) -> dict[str, Any]:
    names = columns or ["id", "name", *AUDIT]
    return {
        "schema": schema,
        "name": name,
        "approx_row_count": rows,
        "columns": [
            {
                "name": c,
                "data_type": "bigint" if c.endswith("id") else "text",
                "is_primary_key": c == "id",
                "references": "public.customers.id" if c == "customer_id" else None,
            }
            for c in names
        ],
    }


TABLES = [
    _table("brands", rows=18),
    _table("customers", rows=1500),
    _table("customer_addresses", rows=2250),
    _table("orders", rows=4200, columns=["id", "customer_id", "total", *AUDIT]),
]


# ── choosing what to describe ────────────────────────────────────────────
def test_a_snapshot_that_fits_is_described_whole() -> None:
    budget = sum(table_chars(t) for t in TABLES)
    assert select_tables("what tables do I have?", TABLES, budget_chars=budget) == (
        TABLES
    )


def _named(name: str) -> dict[str, Any]:
    return next(t for t in TABLES if t["name"] == name)


def test_the_budget_goes_to_the_largest_tables() -> None:
    """"What is in this database?" is answered by where the data is: the two
    biggest tables get described, the 18-row lookup does not.

    Snapshot order in the result, not size order — the block reads like the
    schema rather than like a leaderboard.
    """
    budget = table_chars(_named("orders")) + table_chars(_named("customer_addresses"))
    chosen = select_tables("what tables do I have?", TABLES, budget_chars=budget)

    assert [t["name"] for t in chosen] == ["customer_addresses", "orders"]


def test_a_named_table_is_described_however_small() -> None:
    chosen = select_tables("describe brands", TABLES, budget_chars=1)
    assert [t["name"] for t in chosen] == ["brands"]


def test_one_table_always_survives_the_budget() -> None:
    """A block describing nothing is worse than a block describing one thing."""
    assert len(select_tables("what tables are there", TABLES, budget_chars=1)) == 1


# ── the census ───────────────────────────────────────────────────────────
def test_a_complete_block_says_so() -> None:
    assert census(TABLES, TABLES) == (
        "This connection has 4 tables in public. Every one of them is "
        "described above."
    )


def test_an_incomplete_block_names_what_it_left_out() -> None:
    described = [t for t in TABLES if t["name"] == "orders"]
    said = census(TABLES, described)

    assert said.startswith("This connection has 4 tables in public.")
    assert "1 of them is described above" in said
    assert "The other 3 exist but are not described" in said
    for name in ("brands", "customers", "customer_addresses"):
        assert name in said


def test_the_census_qualifies_names_across_schemas() -> None:
    mixed = [_table("orders", rows=10), _table("audit_log", rows=5, schema="ops")]
    said = census(mixed, mixed[:1])

    assert said.startswith("This connection has 2 tables.")
    assert "ops.audit_log" in said


def test_the_census_carries_no_row_counts() -> None:
    """The counts in the schema block above it are gated by `HintBudget`; a
    total smuggled in here would be the one number that escaped the gate."""
    said = census(TABLES, TABLES[:1])
    for count in ("4,200", "4200", "2,250", "1500"):
        assert count not in said


def test_the_named_list_is_capped() -> None:
    many = [_table(f"t{i:04}", rows=i) for i in range(MAX_CENSUS_NAMES + 25)]
    said = census(many, many[:1])

    assert f"The other {len(many) - 1} exist" in said
    assert "and 24 more." in said


def test_an_empty_snapshot_has_no_census() -> None:
    assert census([], []) == ""


# ── the inventory ────────────────────────────────────────────────────────
def test_inventory_lists_every_table_once_and_no_columns() -> None:
    answer = answer_metadata("What tables do I have?", TABLES)

    assert "You have 4 tables in public." in answer
    for name in ("brands", "customers", "customer_addresses", "orders"):
        assert f"- {name} — " in answer
    # The whole point: not a single column name in an inventory answer.
    for column in ("src_batch_id", "audit_notes", "created_by"):
        assert column not in answer


def test_inventory_is_one_short_line_per_table() -> None:
    answer = answer_metadata("what tables do i have", TABLES)
    table_lines = [ln for ln in answer.splitlines() if ln.startswith("- ")]
    assert len(table_lines) == 4
    assert all(len(ln) < 60 for ln in table_lines)


def test_inventory_orders_by_size_and_shows_shape() -> None:
    answer = answer_metadata("what tables are there", TABLES)
    lines = [ln for ln in answer.splitlines() if ln.startswith("- ")]
    assert lines[0] == "- orders — ~4,200 rows, 8 columns"
    assert lines[-1] == "- brands — ~18 rows, 7 columns"


def test_inventory_caps_a_huge_schema() -> None:
    many = [_table(f"t{i:03}", rows=i) for i in range(MAX_LISTED_TABLES + 20)]
    answer = answer_metadata("what tables do I have?", many)
    assert len([ln for ln in answer.splitlines() if ln.startswith("- ")]) == (
        MAX_LISTED_TABLES
    )
    assert "…and 20 more." in answer


def test_inventory_qualifies_names_across_schemas() -> None:
    mixed = [_table("orders", rows=10), _table("audit_log", rows=5, schema="ops")]
    answer = answer_metadata("what tables do I have?", mixed)
    assert "You have 2 tables." in answer
    assert "- public.orders — " in answer and "- ops.audit_log — " in answer


def test_inventory_handles_an_unknown_row_count() -> None:
    answer = answer_metadata("what tables do I have?", [_table("orders")])
    assert "- orders — 7 columns" in answer


def test_empty_snapshot_says_so() -> None:
    assert "no tables" in answer_metadata("what tables do I have?", [])


# ── naming a table ───────────────────────────────────────────────────────
def test_a_named_table_gets_its_columns_with_types() -> None:
    answer = answer_metadata("what columns does orders have?", TABLES)
    assert answer.startswith("public.orders — ~4,200 rows, 8 columns:")
    assert "  - id (bigint) — primary key" in answer
    assert "  - customer_id (bigint) — → public.customers.id" in answer
    assert "  - audit_notes (text)" in answer
    # Only the table that was asked about.
    assert "brands" not in answer


def test_a_spoken_table_name_matches_the_snake_case_one() -> None:
    assert [t["name"] for t in match_tables("describe customer addresses", TABLES)] == [
        "customer_addresses"
    ]


def test_singular_and_plural_both_match() -> None:
    assert match_tables("what is in the order table", TABLES)[0]["name"] == "orders"
    assert match_tables("describe the brand table", TABLES)[0]["name"] == "brands"


def test_a_plain_inventory_question_names_no_table() -> None:
    for question in (
        "what tables do I have?",
        "how many tables are there?",
        "show me the schema",
    ):
        assert match_tables(question, TABLES) == []


def test_detail_is_bounded_and_says_what_it_left_out() -> None:
    many = [_table(f"table{i}", rows=i) for i in range(6)]
    question = " ".join(f"describe table{i}" for i in range(6))
    answer = answer_metadata(question, many)
    assert answer.count(" columns:") == MAX_DETAILED_TABLES
    assert "ask about those separately" in answer


def test_a_short_name_is_not_matched_inside_a_longer_word() -> None:
    # A table called `id` must not be dragged in by the word "identity".
    tables = [*TABLES, _table("id", rows=1)]
    assert all(t["name"] != "id" for t in match_tables("show me identity", tables))
