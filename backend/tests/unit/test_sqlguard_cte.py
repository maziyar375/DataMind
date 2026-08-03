"""Guard handling of the two ways a query names something that is not a table:
a common table expression (`WITH ... AS`), and a derived table
(`JOIN (SELECT ...) AS t`).

A strong model reaches for both on the harder analytical questions — a CTE for
a staged calculation, a derived table for the denominator of a percentage. The
guard must accept them while still validating the real tables inside their
bodies and rejecting anything they try to smuggle.

Both were the same false positive: the alias is introduced by a node that is
not an `exp.Table`, so it never reached the alias map and every reference
through it came back `E_UNKNOWN_ALIAS` on perfectly valid SQL. Regression tests
for both live here.
"""
from __future__ import annotations

from app.sqlguard import GuardPolicy, guard

POLICY = GuardPolicy(
    dialect="postgres",
    max_rows=1000,
    allowed_tables={"public.orders", "public.order_items"},
    allowed_columns={
        "public.orders": {"id", "customer_id", "order_date", "status", "total_amount"},
        "public.order_items": {"id", "order_id", "product_id", "line_total"},
    },
)


def _codes(sql: str) -> list[str]:
    report, _ = guard(sql, POLICY)
    return [i.rule_id for i in report.errors]


def test_cte_referenced_by_alias_validates() -> None:
    # The exact shape DeepSeek produced that the guard used to reject.
    sql = (
        "WITH current_period AS ("
        "  SELECT SUM(oi.line_total) AS revenue"
        "  FROM public.orders o JOIN public.order_items oi ON o.id = oi.order_id"
        ") SELECT cp.revenue FROM current_period cp"
    )
    report, _ = guard(sql, POLICY)
    assert report.status == "VALID", _codes(sql)


def test_cte_referenced_by_bare_name_validates() -> None:
    sql = (
        "WITH t AS (SELECT id FROM public.orders) SELECT t.id FROM t"
    )
    assert guard(sql, POLICY)[0].status == "VALID"


def test_multiple_ctes_cross_join_validates() -> None:
    sql = (
        "WITH a AS (SELECT SUM(total_amount) AS r FROM public.orders), "
        "b AS (SELECT COUNT(*) AS n FROM public.orders) "
        "SELECT a.r, b.n FROM a CROSS JOIN b"
    )
    assert guard(sql, POLICY)[0].status == "VALID"


def test_cte_body_hitting_system_table_is_rejected() -> None:
    sql = "WITH x AS (SELECT * FROM pg_user) SELECT * FROM x"
    assert guard(sql, POLICY)[0].status == "REJECTED"


def test_cte_body_hitting_unknown_table_is_rejected() -> None:
    sql = "WITH x AS (SELECT * FROM public.salaries) SELECT * FROM x"
    codes = _codes(sql)
    assert "E_TABLE_NOT_ALLOWED" in codes


def test_cte_hiding_a_write_is_rejected() -> None:
    # A data-modifying CTE must never slip through as read-only.
    sql = "WITH x AS (DELETE FROM public.orders RETURNING id) SELECT id FROM x"
    assert guard(sql, POLICY)[0].status == "REJECTED"


def test_unknown_alias_still_caught_without_ctes() -> None:
    # The fix must not blunt the real E_UNKNOWN_ALIAS check on non-CTE queries.
    sql = "SELECT z.id FROM public.orders o"
    assert "E_UNKNOWN_ALIAS" in _codes(sql)


# ── derived tables: JOIN (SELECT ...) AS t ───────────────────────────────
# "What percentage of sales does each warehouse account for?" — a share needs a
# denominator over the whole set, and the natural way to write one without a
# window function is to cross-join a one-row subquery. The guard rejected it
# twice, so the run failed with `E_UNKNOWN_ALIAS` on SQL Postgres would have
# executed happily.
_SHARE = (
    "SELECT o.status, SUM(oi.line_total) * 100.0 / total.all_sales AS pct "
    "FROM public.orders o "
    "JOIN public.order_items oi ON o.id = oi.order_id "
    "CROSS JOIN (SELECT SUM(oi2.line_total) AS all_sales "
    "            FROM public.order_items oi2) AS total "
    "GROUP BY o.status, total.all_sales"
)


def test_derived_table_alias_validates() -> None:
    report, _ = guard(_SHARE, POLICY)
    assert report.status == "VALID", _codes(_SHARE)


def test_derived_table_alias_without_the_as_keyword_validates() -> None:
    # The model's second attempt differed from the first only by dropping `AS`,
    # which is the same tree — worth pinning, because a repair loop that keeps
    # producing equivalent SQL is what turns one guard bug into a failed run.
    assert guard(_SHARE.replace(") AS total", ") total"), POLICY)[0].status == "VALID"


def test_derived_table_body_hitting_an_unknown_table_is_rejected() -> None:
    sql = (
        "SELECT t.n FROM public.orders o "
        "CROSS JOIN (SELECT COUNT(*) AS n FROM public.salaries) AS t"
    )
    assert "E_TABLE_NOT_ALLOWED" in _codes(sql)


def test_derived_table_body_hitting_a_system_table_is_rejected() -> None:
    sql = (
        "SELECT t.n FROM public.orders o "
        "CROSS JOIN (SELECT COUNT(*) AS n FROM pg_user) AS t"
    )
    assert guard(sql, POLICY)[0].status == "REJECTED"


def test_a_derived_table_does_not_switch_off_column_checking() -> None:
    """The narrow half of the fix, and the reason it is not folded into `cte_names`.

    A CTE disables column verification for the whole statement — provenance
    through one needs a real scope resolver. A derived table must not: it is
    usually one small subquery beside four ordinary joins, and the columns on
    those joins are as checkable as they ever were.
    """
    sql = _SHARE.replace("o.status", "o.nonexistent_column")
    assert "E_UNKNOWN_COLUMN" in _codes(sql)


def test_unknown_alias_still_caught_beside_a_derived_table() -> None:
    sql = (
        "SELECT z.id, t.n FROM public.orders o "
        "CROSS JOIN (SELECT COUNT(*) AS n FROM public.orders o2) AS t"
    )
    assert "E_UNKNOWN_ALIAS" in _codes(sql)
