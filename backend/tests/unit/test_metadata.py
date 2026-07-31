"""Schema questions answered from the snapshot, at the granularity asked.

The behaviour under test is that "what tables do I have?" and "what columns
does orders have?" are different questions: the first must not answer with
every column of every table (on the sales fixture that is ~600 names, most of
them the same audit columns repeated), and the second must not answer with a
bare inventory.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.metadata import (
    MAX_DETAILED_TABLES,
    MAX_LISTED_TABLES,
    answer_metadata,
    match_tables,
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
