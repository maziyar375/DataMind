"""Guard handling of common table expressions (WITH ... AS).

A strong model reaches for CTEs on the harder analytical questions. The guard
must accept a valid read-only CTE — including one whose outer query references
the CTE through an alias (`FROM current_period cp`) — while still validating the
real tables inside the CTE bodies and rejecting anything a CTE tries to smuggle.
Regression for the `E_UNKNOWN_ALIAS`-on-CTE-alias false positive.
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
