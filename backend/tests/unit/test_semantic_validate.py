"""Binding a semantic document to a schema snapshot.

The whole value of the layer rests on this: a definition that no longer
resolves must be visible in the UI and absent from the prompt. Silently
keeping it is the failure mode that would poison every query that follows.
"""
from __future__ import annotations

from app.semantic import (
    Provenance,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    build_index,
    check_expression,
    derive_joins,
    merge_documents,
    validate_document,
)

TABLES = [
    {
        "schema": "sales",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "int", "is_primary_key": True},
            {"name": "customer_id", "data_type": "int", "is_foreign_key": True},
            {"name": "status", "data_type": "varchar",
             "sample_values": ["PAID", "CANCELLED"]},
            {"name": "ordered_at", "data_type": "timestamp"},
        ],
    },
    {
        "schema": "sales",
        "name": "order_items",
        "columns": [
            {"name": "id", "data_type": "int", "is_primary_key": True},
            {"name": "order_id", "data_type": "int", "is_foreign_key": True},
            {"name": "quantity", "data_type": "int"},
            {"name": "unit_price", "data_type": "numeric"},
        ],
    },
    {
        "schema": "sales",
        "name": "customers",
        "columns": [{"name": "id", "data_type": "int", "is_primary_key": True}],
    },
]

RELATIONSHIPS = [
    {"from_table": "sales.orders", "from_column": "customer_id",
     "to_table": "sales.customers", "to_column": "id"},
    {"from_table": "sales.order_items", "from_column": "order_id",
     "to_table": "sales.orders", "to_column": "id"},
]

INDEX = build_index(TABLES)


# ── expressions ──────────────────────────────────────────────────────────
def test_valid_aggregate_over_own_columns() -> None:
    valid, issue = check_expression(
        "SUM(sales.order_items.quantity * sales.order_items.unit_price)",
        entity_table="sales.order_items",
        index=INDEX,
    )
    assert valid and issue == ""


def test_unknown_column_is_rejected() -> None:
    valid, issue = check_expression(
        "SUM(sales.order_items.total_amount)",
        entity_table="sales.order_items",
        index=INDEX,
    )
    assert not valid
    assert "total_amount" in issue


def test_column_of_an_unjoined_table_is_rejected() -> None:
    valid, _ = check_expression(
        "SUM(sales.orders.id)", entity_table="sales.order_items", index=INDEX
    )
    assert not valid


def test_column_of_a_declared_join_is_accepted() -> None:
    valid, _ = check_expression(
        "COUNT(DISTINCT sales.orders.id)",
        entity_table="sales.order_items",
        index=INDEX,
        extra_tables=["sales.orders"],
    )
    assert valid


def test_unparseable_expression_is_rejected() -> None:
    valid, issue = check_expression(
        "SUM(quantity", entity_table="sales.order_items", index=INDEX
    )
    assert not valid and issue


def test_non_aggregate_is_valid_but_flagged() -> None:
    """A user's expression is never deleted for being unwise, only annotated."""
    valid, issue = check_expression(
        "quantity", entity_table="sales.order_items", index=INDEX
    )
    assert valid
    assert "aggregate" in issue.lower()


def test_filter_is_checked_as_a_predicate() -> None:
    valid, _ = check_expression(
        "sales.orders.status <> 'CANCELLED'",
        entity_table="sales.orders",
        index=INDEX,
        boolean=True,
    )
    assert valid


# ── documents ────────────────────────────────────────────────────────────
def _doc() -> SemanticDocument:
    return SemanticDocument(
        entities=[
            SemanticEntity(
                table="sales.orders",
                grain="one row per order",
                default_time_column="ordered_at",
                columns=[SemanticColumn(name="status")],
                metrics=[
                    SemanticMetric(
                        name="order_count", expression="COUNT(sales.orders.id)"
                    )
                ],
            )
        ]
    )


def test_a_sound_document_survives_validation() -> None:
    bound = validate_document(_doc(), INDEX)
    assert bound.entities[0].valid
    assert bound.entities[0].columns[0].valid
    assert bound.entities[0].metrics[0].valid
    assert bound.issue_count == 0


def test_dropped_table_is_flagged_not_deleted() -> None:
    doc = _doc()
    doc.entities[0].table = "sales.gone"
    bound = validate_document(doc, INDEX)
    assert len(bound.entities) == 1          # kept, so a human can see it
    assert not bound.entities[0].valid
    assert "snapshot" in bound.entities[0].issue


def test_unqualified_table_name_is_rescued_when_unambiguous() -> None:
    doc = _doc()
    doc.entities[0].table = "orders"
    bound = validate_document(doc, INDEX)
    assert bound.entities[0].valid
    assert bound.entities[0].table == "sales.orders"


def test_dropped_column_is_flagged() -> None:
    doc = _doc()
    doc.entities[0].columns.append(SemanticColumn(name="legacy_flag"))
    bound = validate_document(doc, INDEX)
    assert [c.valid for c in bound.entities[0].columns] == [True, False]


def test_time_column_that_no_longer_exists_is_cleared() -> None:
    doc = _doc()
    doc.entities[0].default_time_column = "created_at"
    bound = validate_document(doc, INDEX)
    assert bound.entities[0].default_time_column == ""
    assert bound.entities[0].issue


def test_metric_with_a_broken_filter_is_invalid() -> None:
    doc = _doc()
    doc.entities[0].metrics[0].filters = ["sales.orders.state = 'X'"]
    bound = validate_document(doc, INDEX)
    assert not bound.entities[0].metrics[0].valid
    assert "Filter" in bound.entities[0].metrics[0].issue


def test_validation_does_not_mutate_its_input() -> None:
    doc = _doc()
    doc.entities[0].table = "sales.gone"
    validate_document(doc, INDEX)
    assert doc.entities[0].valid is True     # the copy was flagged, not this


# ── joins ────────────────────────────────────────────────────────────────
def test_joins_are_derived_with_fan_out_warnings() -> None:
    joins = derive_joins(RELATIONSHIPS, INDEX)
    by_pair = {(j.left, j.right): j for j in joins}

    child = by_pair[("sales.order_items", "sales.orders")]
    assert child.cardinality == "many_to_one"
    assert "orders" in child.fan_out_warning
    assert child.provenance.source == "derived"


def test_join_to_a_missing_table_is_dropped() -> None:
    joins = derive_joins(
        [*RELATIONSHIPS,
         {"from_table": "sales.orders", "from_column": "id",
          "to_table": "sales.ghost", "to_column": "id"}],
        INDEX,
    )
    assert all(j.right != "sales.ghost" for j in joins)


# ── merging ──────────────────────────────────────────────────────────────
def test_regeneration_keeps_a_human_edited_entity() -> None:
    existing = _doc()
    existing.entities[0].grain = "one row per order, hand written"
    existing.entities[0].provenance = Provenance(source="human", edited=True)

    generated = SemanticDocument(
        entities=[
            SemanticEntity(table="sales.orders", grain="regenerated"),
            SemanticEntity(table="sales.customers", grain="one row per customer"),
        ]
    )
    merged = merge_documents(existing, generated)
    by_table = {e.table: e for e in merged.entities}

    assert by_table["sales.orders"].grain == "one row per order, hand written"
    assert by_table["sales.customers"].grain == "one row per customer"


def test_regeneration_overwrites_an_untouched_entity() -> None:
    merged = merge_documents(
        _doc(),
        SemanticDocument(
            entities=[SemanticEntity(table="sales.orders", grain="regenerated")]
        ),
    )
    assert merged.entities[0].grain == "regenerated"


def test_an_edited_entity_survives_a_generation_that_skipped_its_table() -> None:
    existing = _doc()
    existing.entities[0].provenance = Provenance(source="human", edited=True)
    merged = merge_documents(
        existing,
        SemanticDocument(entities=[SemanticEntity(table="sales.customers")]),
    )
    assert {e.table for e in merged.entities} == {"sales.orders", "sales.customers"}


def test_a_hand_written_exclusion_rule_survives_regeneration() -> None:
    """Sharper than the business context this rides beside: replacing an
    exclusion rule with the model's guess changes every total on the
    dashboard without touching a single query."""
    existing = _doc()
    existing.default_exclusions = "Rows where is_archived is true."
    existing.entities[0].provenance = Provenance(source="human", edited=True)

    merged = merge_documents(
        existing,
        SemanticDocument(
            default_exclusions="",
            entities=[SemanticEntity(table="sales.orders", grain="regenerated")],
        ),
    )
    assert merged.default_exclusions == "Rows where is_archived is true."


def test_an_untouched_exclusion_rule_is_replaced_by_the_generation() -> None:
    merged = merge_documents(
        _doc(),
        SemanticDocument(
            default_exclusions="Rows where deleted_at is not null.",
            entities=[SemanticEntity(table="sales.orders")],
        ),
    )
    assert merged.default_exclusions == "Rows where deleted_at is not null."
