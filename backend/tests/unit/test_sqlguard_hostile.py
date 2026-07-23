"""The hostile corpus.

This is the milestone's hard gate: zero bypasses, or the build fails. Every
entry is a statement that must never reach a database driver.

When adding a new SQL feature to the allowlist, add its abuse case here first
and watch it fail. A guard that has only ever been tested against SQL it was
designed to accept has not been tested.
"""
from __future__ import annotations

import pytest

from app.sqlguard import GuardPolicy, guard

POLICY = GuardPolicy(
    dialect="postgres",
    max_rows=1000,
    allowed_tables={
        "public.orders", "public.order_items", "public.products",
        "public.customers", "public.regions",
    },
    allowed_columns={
        "public.orders": {"id", "customer_id", "order_date", "status", "total_amount"},
        "public.order_items": {"id", "order_id", "product_id", "quantity", "unit_price"},
        "public.products": {"id", "name", "category", "price"},
        "public.customers": {"id", "name", "region_id", "signed_up_at"},
        "public.regions": {"id", "name"},
    },
)

HOSTILE: list[tuple[str, str]] = [
    # ── statement chaining ───────────────────────────────────────────────
    ("SELECT 1; DROP TABLE orders", "E_MULTI_STATEMENT"),
    ("SELECT * FROM orders; DELETE FROM orders", "E_MULTI_STATEMENT"),
    # ── writes and DDL ───────────────────────────────────────────────────
    ("DROP TABLE orders", "E_NOT_A_SELECT"),
    ("DELETE FROM orders WHERE 1=1", "E_NOT_A_SELECT"),
    ("UPDATE orders SET total_amount = 0", "E_NOT_A_SELECT"),
    ("INSERT INTO orders VALUES (1)", "E_NOT_A_SELECT"),
    ("TRUNCATE orders", "E_NOT_A_SELECT"),
    ("CREATE TABLE evil (id int)", "E_NOT_A_SELECT"),
    ("ALTER TABLE orders DROP COLUMN status", "E_NOT_A_SELECT"),
    ("GRANT ALL ON orders TO PUBLIC", "E_NOT_A_SELECT"),
    # ── system catalogs ──────────────────────────────────────────────────
    ("SELECT * FROM pg_shadow", None),
    ("SELECT * FROM pg_catalog.pg_user", None),
    ("SELECT * FROM information_schema.tables", None),
    # ── dangerous functions ──────────────────────────────────────────────
    ("SELECT pg_sleep(10)", "E_FORBIDDEN_CONSTRUCT"),
    ("SELECT pg_read_file('/etc/passwd')", "E_FORBIDDEN_CONSTRUCT"),
    ("SELECT lo_import('/etc/passwd')", "E_FORBIDDEN_CONSTRUCT"),
    ("SELECT current_setting('is_superuser')", "E_FORBIDDEN_CONSTRUCT"),
    ("SELECT xp_cmdshell('dir')", "E_FORBIDDEN_CONSTRUCT"),
    ("SELECT dblink('host=evil', 'SELECT 1')", "E_FORBIDDEN_CONSTRUCT"),
    # ── exfiltration ─────────────────────────────────────────────────────
    ("COPY orders TO '/tmp/out.csv'", "E_FORBIDDEN_CONSTRUCT"),
    ("SELECT * FROM orders INTO OUTFILE '/tmp/x'", "E_FORBIDDEN_CONSTRUCT"),
    # ── schema violations ────────────────────────────────────────────────
    ("SELECT * FROM users", "E_TABLE_NOT_ALLOWED"),
    ("SELECT * FROM public.secrets", "E_TABLE_NOT_ALLOWED"),
    ("SELECT nonexistent_col FROM orders", "E_UNKNOWN_COLUMN"),
    ("SELECT x.id FROM orders o", "E_UNKNOWN_ALIAS"),
    ("SELECT o.* FROM orders o JOIN badtable b ON b.id = o.id", "E_TABLE_NOT_ALLOWED"),
    # ── union smuggling ──────────────────────────────────────────────────
    ("SELECT id FROM orders UNION SELECT usename FROM pg_shadow", None),
    ("SELECT id FROM orders UNION ALL SELECT id FROM users", "E_TABLE_NOT_ALLOWED"),
    # ── comment-based evasion ────────────────────────────────────────────
    ("SELECT * FROM orders -- ; DROP TABLE orders", "E_COMMENT_NOT_ALLOWED"),
    ("SELECT * /* sneaky */ FROM orders", "E_COMMENT_NOT_ALLOWED"),
    # ── malformed input ──────────────────────────────────────────────────
    ("SELECT FROM WHERE", None),
    ("", None),
]

LEGITIMATE: list[str] = [
    "SELECT SUM(total_amount) FROM orders WHERE order_date >= '2024-01-01'",
    "SELECT status, COUNT(*) FROM orders GROUP BY status",
    """SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue
       FROM order_items oi
       JOIN products p ON p.id = oi.product_id
       GROUP BY p.name
       ORDER BY revenue DESC""",
    """SELECT date_trunc('month', order_date) AS month, SUM(total_amount) AS total
       FROM orders GROUP BY month ORDER BY month""",
    """SELECT r.name AS region, COUNT(DISTINCT c.id) AS customers
       FROM customers c JOIN regions r ON r.id = c.region_id
       GROUP BY r.name""",
    "SELECT * FROM products WHERE price BETWEEN 50 AND 200",
    """SELECT p.category, AVG(p.price) AS avg_price
       FROM products p WHERE p.category IS NOT NULL
       GROUP BY p.category HAVING AVG(p.price) > 100""",
]


@pytest.mark.parametrize("sql,expected_code", HOSTILE)
def test_hostile_statements_are_rejected(sql: str, expected_code: str | None) -> None:
    report, executable = guard(sql, POLICY)

    assert report.status == "REJECTED", f"BYPASS — the guard accepted: {sql!r}"
    assert executable is None, f"BYPASS — executable SQL produced for: {sql!r}"
    assert report.errors, f"Rejected without a reason: {sql!r}"

    if expected_code is not None:
        codes = {issue.rule_id for issue in report.errors}
        assert expected_code in codes, (
            f"Expected {expected_code} for {sql!r}, got {sorted(codes)}"
        )


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_legitimate_analytics_sql_passes(sql: str) -> None:
    report, executable = guard(sql, POLICY)
    assert report.status == "VALID", (
        f"False rejection of legitimate SQL {sql!r}: "
        f"{[(i.rule_id, i.message) for i in report.errors]}"
    )
    assert executable is not None
    assert report.referenced_tables


def test_limit_is_injected_when_absent() -> None:
    report, executable = guard("SELECT id FROM orders", POLICY)
    assert report.status == "VALID"
    assert report.limit_applied == 1000
    assert "LIMIT 1000" in (executable or "").upper()


def test_oversized_limit_is_capped_down() -> None:
    """A model asking for a million rows gets the policy's cap, not its wish."""
    report, executable = guard("SELECT id FROM orders LIMIT 999999", POLICY)
    assert report.status == "VALID"
    assert report.limit_applied == 1000
    assert "999999" not in (executable or "")


def test_smaller_limit_is_respected() -> None:
    report, _ = guard("SELECT id FROM orders LIMIT 10", POLICY)
    assert report.limit_applied == 10


def test_referenced_tables_are_reported_for_the_ui() -> None:
    """The chat UI renders these as metadata chips, so they must be accurate."""
    report, _ = guard(
        "SELECT p.name FROM order_items oi JOIN products p ON p.id = oi.product_id",
        POLICY,
    )
    assert set(report.referenced_tables) == {"public.order_items", "public.products"}


def test_empty_allowlist_rejects_everything() -> None:
    """A connection that has never been synced must not be queryable."""
    empty = GuardPolicy(dialect="postgres", max_rows=100)
    report, executable = guard("SELECT id FROM orders", empty)
    assert report.status == "REJECTED"
    assert executable is None
