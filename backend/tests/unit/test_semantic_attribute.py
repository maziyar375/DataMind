"""Did a statement use a metric's definition? The labelled corpus.

Phase 3 of `docs/plans/semantic-layer-model.md`. `attribute` gives each metric in
scope `used`, `ignored` or `unknown`, and two of those are claims about an
answer somebody will read: `used` puts *Matches the definition* beside it, and
`ignored` says it left out part of one. So the corpus is built around the ways
each could be wrong.

* **`LABELLED`** — hand-written statements against the metrics in
  `fixtures/sales_semantic.json`, each labelled for the metric it is about.
* **`ADVERSARIAL`** — statements built to *tempt* a false `used`: the right
  function with the wrong filter, the filter in an outer query that does not
  scope the aggregate, a filter on a joined table with a column of the same
  name, the metric's table joined to itself, the filter behind an outer join, in
  an `OR`, in a `CASE`, in a subquery nothing reads. **Zero false `used`** is the
  gate the chip ships behind.

The schema is the `sales` fixture's own columns for the tables involved, so a
bare column resolves the way the guard resolves it.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from app.semantic import SemanticDocument, bind_layer
from app.semantic.attribute import (
    IGNORED,
    UNKNOWN,
    USED,
    AttributionError,
    attribute,
)
from app.semantic.validate import build_index

FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "sales_semantic.json"

COLUMNS = {
    "orders": [
        "id", "customer_id", "employee_id", "coupon_id", "currency_id", "order_date",
        "status", "channel", "subtotal", "discount_total", "tax_total", "shipping_fee",
        "total_amount", "cust_ref", "notes", "placed_at", "created_at", "updated_at",
    ],
    "order_items": [
        "id", "order_id", "product_id", "variant_id", "quantity", "unit_price", "discount",
        "tax_rate_id", "line_total", "created_at",
    ],
    "returns": [
        "id", "order_item_id", "reason", "quantity", "refund_amount", "status",
        "returned_at", "created_at",
    ],
    "reviews": [
        "id", "product_id", "customer_id", "order_id", "rating", "title", "body",
        "is_verified", "is_hidden", "created_at",
    ],
    "customers": [
        "id", "name", "email", "phone", "region_id", "loyalty_tier_id", "referred_by_id",
        "segment", "credit_limit", "cust_ref", "signed_up_at", "last_order_at",
        "is_deleted", "deleted_at", "created_at", "updated_at",
    ],
    "products": ["id", "name", "category", "price", "cost", "active", "created_at"],
    "inventory": ["id", "product_id", "warehouse_id", "quantity", "reorder_level", "updated_at"],
    "refunds": ["id", "return_id", "payment_id", "amount", "method", "processed_at", "created_at"],
    "payments": ["id", "order_id", "payment_method_id", "amount", "currency_id", "status", "paid_at"],
    "shipments": ["id", "order_id", "warehouse_id", "carrier_id", "status", "shipped_at", "cost"],
}
TABLES = [
    {"schema": "public", "name": name, "columns": [{"name": c, "data_type": "text"} for c in cols]}
    for name, cols in COLUMNS.items()
]
SCHEMA = build_index(TABLES, "postgres")


@pytest.fixture(scope="module")
def layer() -> SemanticDocument:
    raw: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw.pop("_README", None)
    doc = bind_layer(raw, tables=TABLES, relationships=[], dialect="postgres")
    # The corpus is about the metrics these tables carry; they must all bind.
    for table in ("public.orders", "public.returns", "public.reviews", "public.customers"):
        entity = doc.entity(table)
        assert entity is not None and entity.valid, table
        assert all(m.valid for m in entity.metrics), (table, [m.issue for m in entity.metrics])
    return doc


def verdicts(
    layer: SemanticDocument, sql: str, *, dialect: str = "postgres"
) -> dict[str, str]:
    touched = [f"public.{name}" for name in COLUMNS]
    return {
        v.metric: v.verdict
        for v in attribute(sql, dialect, layer, touched, schema=SCHEMA)
    }


# ── the labelled corpus ──────────────────────────────────────────────────
LABELLED: list[tuple[str, str, str, str]] = [
    # (id, statement, metric, verdict)
    ("filterless metric, bare columns",
     "SELECT SUM(total_amount) AS revenue FROM orders", "revenue", USED),
    ("aliased and grouped",
     "SELECT date_trunc('month', o.order_date) AS m, SUM(o.total_amount) "
     "FROM public.orders AS o GROUP BY 1", "revenue", USED),
    ("the definition's filter, qualified by alias",
     "SELECT SUM(r.quantity) FROM returns r WHERE r.status = 'approved'",
     "returned_units", USED),
    ("the filter beside the question's own conditions",
     "SELECT SUM(quantity) FROM returns "
     "WHERE returned_at >= DATE '2026-01-01' AND status = 'approved' AND reason <> 'other'",
     "returned_units", USED),
    ("the filter in a CTE feeding the aggregate",
     "WITH approved AS (SELECT * FROM returns WHERE status = 'approved') "
     "SELECT SUM(approved.refund_amount) FROM approved", "credited_amount", USED),
    ("the filter in a derived table with explicit columns",
     "SELECT SUM(t.quantity) FROM (SELECT quantity, reason FROM returns "
     "WHERE status = 'approved') AS t", "returned_units", USED),
    ("the filter in HAVING",
     "SELECT status, SUM(quantity) FROM returns GROUP BY status HAVING status = 'approved'",
     "returned_units", USED),
    ("the filter in an inner join's ON",
     "SELECT SUM(r.quantity) FROM order_items i "
     "JOIN returns r ON r.order_item_id = i.id AND r.status = 'approved'",
     "returned_units", USED),
    ("a join for grouping, the filter kept",
     "SELECT c.segment, AVG(rv.rating) FROM reviews rv "
     "JOIN customers c ON c.id = rv.customer_id "
     "WHERE rv.is_hidden = false GROUP BY c.segment", "average_rating", USED),
    ("COUNT DISTINCT with its filter",
     "SELECT COUNT(DISTINCT id) FROM customers WHERE is_deleted = FALSE",
     "customer_count", USED),
    ("identifier case does not matter",
     "SELECT Sum(R.Quantity) FROM Returns R WHERE R.Status = 'approved'",
     "returned_units", USED),
    ("parentheses do not matter",
     "SELECT SUM(quantity) FROM returns WHERE (status = 'approved')",
     "returned_units", USED),

    ("the metric's only filter left out",
     "SELECT SUM(quantity) FROM returns", "returned_units", IGNORED),
    ("left out, with conditions on other columns",
     "SELECT AVG(rating) FROM reviews WHERE created_at >= DATE '2026-01-01'",
     "average_rating", IGNORED),
    ("left out, through a join that filters something else",
     "SELECT COUNT(DISTINCT c.id) FROM customers c JOIN orders o ON o.customer_id = c.id "
     "WHERE o.order_date >= DATE '2026-01-01'", "customer_count", IGNORED),
    ("left out, while a joined table's same-named column is filtered",
     "SELECT SUM(r.quantity) FROM returns r JOIN order_items i ON i.id = r.order_item_id "
     "JOIN orders o ON o.id = i.order_id WHERE o.status = 'approved'",
     "returned_units", IGNORED),
    ("left out, and a CTE nobody reads carries it",
     "WITH a AS (SELECT * FROM returns WHERE status = 'approved') "
     "SELECT SUM(quantity) FROM returns", "returned_units", IGNORED),

    ("the filter's column, in a form that does not normalise",
     "SELECT SUM(quantity) FROM returns WHERE status IN ('approved')",
     "returned_units", UNKNOWN),
    ("a window function",
     "SELECT SUM(quantity) OVER (PARTITION BY reason) FROM returns WHERE status = 'approved'",
     "returned_units", UNKNOWN),
    ("a DISTINCT mismatch",
     "SELECT COUNT(id) FROM customers WHERE is_deleted = false", "customer_count", UNKNOWN),
    ("a set operation",
     "SELECT SUM(quantity) FROM returns WHERE status = 'approved' "
     "UNION ALL SELECT SUM(quantity) FROM returns", "returned_units", UNKNOWN),
    ("a boolean written another way",
     "SELECT COUNT(DISTINCT id) FROM customers WHERE NOT is_deleted", "customer_count", UNKNOWN),
    ("split by the filter's column instead of filtered",
     "SELECT status, SUM(quantity) FROM returns GROUP BY status", "returned_units", UNKNOWN),
    ("a subquery where a filter could hide",
     "SELECT SUM(quantity) FROM returns WHERE order_item_id IN (SELECT id FROM order_items)",
     "returned_units", UNKNOWN),
    ("a FILTER clause",
     "SELECT SUM(quantity) FILTER (WHERE status = 'approved') FROM returns",
     "returned_units", UNKNOWN),
    ("no aggregate over the metric at all",
     "SELECT reason, returned_at FROM returns WHERE status = 'approved'",
     "returned_units", UNKNOWN),
]


@pytest.mark.parametrize(("case", "sql", "metric", "expected"), LABELLED, ids=[c[0] for c in LABELLED])
def test_the_labelled_corpus(
    layer: SemanticDocument, case: str, sql: str, metric: str, expected: str
) -> None:
    assert verdicts(layer, sql)[metric] == expected, case


def test_other_dialects_read_the_same_definition(layer: SemanticDocument) -> None:
    tsql = "SELECT TOP 10 SUM([r].[quantity]) FROM [public].[returns] AS [r] WHERE [r].[status] = 'approved'"
    assert verdicts(layer, tsql, dialect="tsql")["returned_units"] == USED
    mysql = "SELECT SUM(`r`.`quantity`) FROM `public`.`returns` AS `r` WHERE `r`.`status` != 'x'"
    assert verdicts(layer, mysql, dialect="mysql")["returned_units"] == UNKNOWN


# ── the adversarial set: built to tempt a false `used` ───────────────────
ADVERSARIAL: list[tuple[str, str, str]] = [
    ("the right function with the wrong filter",
     "SELECT SUM(quantity) FROM returns WHERE status = 'pending'", "returned_units"),
    ("the filter's value in another case",
     "SELECT SUM(quantity) FROM returns WHERE status = 'APPROVED'", "returned_units"),
    ("the filter negated",
     "SELECT SUM(quantity) FROM returns WHERE NOT (status = 'approved')", "returned_units"),
    ("the filter in an OR",
     "SELECT SUM(quantity) FROM returns WHERE status = 'approved' OR reason = 'damaged'",
     "returned_units"),
    ("the filter in an outer query that does not scope the aggregate",
     "SELECT * FROM (SELECT SUM(quantity) AS units, MAX(status) AS status FROM returns) t "
     "WHERE t.status = 'approved'", "returned_units"),
    ("the filter after grouping, outside the aggregate's scope",
     "SELECT t.units FROM (SELECT status, SUM(quantity) AS units FROM returns GROUP BY status) t "
     "WHERE t.status = 'approved'", "returned_units"),
    ("the filter on a joined table with a column of the same name",
     "SELECT SUM(r.quantity) FROM returns r JOIN order_items i ON i.id = r.order_item_id "
     "JOIN orders o ON o.id = i.order_id WHERE o.status = 'approved'", "returned_units"),
    ("the same-named filter on payments, joined",
     "SELECT COUNT(DISTINCT c.id) FROM customers c JOIN orders o ON o.customer_id = c.id "
     "JOIN payments p ON p.order_id = o.id WHERE p.status = 'approved'", "customer_count"),
    ("the metric's table joined to itself, the filter on the other copy",
     "SELECT SUM(r.quantity) FROM returns r JOIN returns r2 ON r2.id = r.id "
     "WHERE r2.status = 'approved'", "returned_units"),
    ("the filter inside a LEFT JOINed derived table",
     "SELECT SUM(r.quantity) FROM order_items i LEFT JOIN "
     "(SELECT * FROM returns WHERE status = 'approved') r ON r.order_item_id = i.id",
     "returned_units"),
    ("the filter in an outer join's ON",
     "SELECT SUM(r.quantity) FROM order_items i LEFT JOIN returns r "
     "ON r.order_item_id = i.id AND r.status = 'approved'", "returned_units"),
    ("the filter inside a CASE, not around the aggregate",
     "SELECT SUM(CASE WHEN status = 'approved' THEN quantity END) FROM returns",
     "returned_units"),
    ("the filter in a scalar subquery beside an unfiltered sum",
     "SELECT SUM(quantity), (SELECT COUNT(*) FROM returns WHERE status = 'approved') FROM returns",
     "returned_units"),
    ("the filter in a CTE the aggregate does not read",
     "WITH a AS (SELECT * FROM returns WHERE status = 'approved') SELECT SUM(quantity) FROM returns",
     "returned_units"),
    ("a same-named column summed on another table",
     "SELECT SUM(v.quantity) FROM inventory v JOIN order_items i ON i.product_id = v.product_id "
     "JOIN returns r ON r.order_item_id = i.id WHERE r.status = 'approved'", "returned_units"),
    ("an ambiguous bare column across a join",
     "SELECT SUM(quantity) FROM returns r JOIN order_items i ON i.id = r.order_item_id "
     "WHERE r.status = 'approved'", "returned_units"),
    ("HAVING on a different aggregate",
     "SELECT SUM(quantity) FROM returns HAVING MAX(status) = 'approved'", "returned_units"),
    ("DISTINCT inside the aggregate",
     "SELECT SUM(DISTINCT quantity) FROM returns WHERE status = 'approved'", "returned_units"),
    ("the hidden flag on the wrong table",
     "SELECT AVG(rv.rating) FROM reviews rv JOIN customers c ON c.id = rv.customer_id "
     "WHERE c.is_deleted = false", "average_rating"),
    ("the filter in a union branch",
     "SELECT SUM(quantity) FROM (SELECT quantity FROM returns WHERE status = 'approved' "
     "UNION ALL SELECT quantity FROM returns) u", "returned_units"),
]


@pytest.mark.parametrize(("case", "sql", "metric"), ADVERSARIAL, ids=[c[0] for c in ADVERSARIAL])
def test_no_adversarial_statement_is_called_used(
    layer: SemanticDocument, case: str, sql: str, metric: str
) -> None:
    assert verdicts(layer, sql)[metric] != USED, case


def test_zero_false_used_and_every_ignored_is_right_across_both_sets(
    layer: SemanticDocument,
) -> None:
    """The two numbers the chip and the SQL-panel line are gated on, on the corpus.

    `ignored` precision on real runs is a separate measurement (§4.4) and this
    does not stand in for it; it only proves the corpus holds none."""
    false_used = [c for c, sql, m in ADVERSARIAL if verdicts(layer, sql)[m] == USED]
    false_used += [
        c for c, sql, m, want in LABELLED if want != USED and verdicts(layer, sql)[m] == USED
    ]
    called_ignored = [
        (c, want) for c, sql, m, want in LABELLED if verdicts(layer, sql)[m] == IGNORED
    ]
    assert false_used == []
    assert called_ignored and all(want == IGNORED for _, want in called_ignored)


# ── scope and failure ────────────────────────────────────────────────────
def test_only_metrics_on_tables_the_statement_touched_are_in_scope(
    layer: SemanticDocument,
) -> None:
    found = attribute(
        "SELECT SUM(quantity) FROM returns WHERE status = 'approved'",
        "postgres", layer, ["public.returns"], schema=SCHEMA,
    )
    assert {v.entity for v in found} == {"public.returns"}
    assert {v.metric for v in found} == {"returned_units", "credited_amount"}
    assert {v.metric: v.verdict for v in found}["credited_amount"] == UNKNOWN


def test_excluded_and_flagged_entries_are_not_in_scope(layer: SemanticDocument) -> None:
    doc = layer.model_copy(deep=True)
    returns = doc.entity("public.returns")
    assert returns is not None
    returns.metrics[0].valid = False
    reviews = doc.entity("public.reviews")
    assert reviews is not None
    reviews.exclude = True
    found = attribute(
        "SELECT SUM(r.quantity), AVG(v.rating) FROM returns r JOIN reviews v ON v.id = r.id",
        "postgres", doc, ["public.returns", "public.reviews"], schema=SCHEMA,
    )
    assert [v.metric for v in found] == ["credited_amount"]


def test_a_statement_that_will_not_parse_raises_and_attributes_nothing(
    layer: SemanticDocument,
) -> None:
    with pytest.raises(AttributionError):
        attribute("SELEC SUM(quantity FROM", "postgres", layer, ["public.returns"])


def test_no_metric_in_scope_reads_nothing(layer: SemanticDocument) -> None:
    # Not even the parse: nothing to say about a statement with no metric table.
    assert attribute("SELEC nonsense", "postgres", layer, ["public.products"]) == []


def test_a_verdict_carries_names_and_no_sql(layer: SemanticDocument) -> None:
    [first, *_] = attribute(
        "SELECT SUM(quantity) FROM returns WHERE status = 'approved'",
        "postgres", layer, ["public.returns"], schema=SCHEMA,
    )
    assert first.as_dict() == {
        "metric": "returned_units", "entity": "public.returns", "verdict": USED,
    }
