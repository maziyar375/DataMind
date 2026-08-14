"""The golden set is only worth trusting if it is mechanically checked.

Two kinds of check here:

* **Static** (always run, no database): the suite files parse, have the right
  shape and composition, `expected_tables` names only real fixture tables and
  matches the tables the gold SQL actually references, and the negative set
  never expects SQL.

* **Live** (needs the `sales` fixture): every gold query executes and returns a
  non-empty result (>= 2 rows, or a non-null scalar), and agrees with a second,
  structurally-different verify query. This is the independence guarantee — the
  gold SQL is graded against an independent formulation, not against itself.

The live checks connect to `SALES_FIXTURE_DSN` (default: the Compose demo on
:5433). They **skip** — never fail — when no fixture is reachable or the DB is
still the pre-Task-1 schema, so `make test` stays green without a database.
Point the DSN at a freshly-seeded fixture to actually run them:

    SALES_FIXTURE_DSN=postgresql://postgres:postgres@localhost:55433/sales \
        pytest tests/eval/test_golden_set.py -v
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

SUITES = Path(__file__).resolve().parents[2] / "app" / "eval" / "suites"
VERIFY_FILE = Path(__file__).resolve().parent / "sales_v1_verify.json"
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SEED_FILE = FIXTURES_DIR / "sales_seed.sql"
COMMENTS_FILE = FIXTURES_DIR / "sales_comments.sql"
DSN = os.environ.get(
    "SALES_FIXTURE_DSN", "postgresql://postgres:postgres@localhost:5433/sales"
)

FIXTURE_TABLES = {
    "countries", "regions", "categories", "subcategories", "brands", "products",
    "product", "product_variants", "suppliers", "warehouses", "teams", "employees",
    "loyalty_tiers", "customers", "customer_addresses", "currencies", "price_lists",
    "tax_rates", "promotions", "coupons", "tags", "carriers", "payment_methods",
    "product_suppliers", "price_list_items", "product_tags", "employee_teams",
    "inventory", "orders", "order_items", "order_promotions", "payments",
    "shipments", "shipment_items", "returns", "refunds", "reviews",
    "support_tickets", "wishlists", "order_status_history", "product_price_history",
    "sales_daily_rollup",
}

# id ranges -> slice -> required count (the composition contract)
SLICES = [
    ("single_table", range(1, 7), 6),
    ("two_table", range(7, 17), 10),
    ("multi_table", range(17, 29), 12),
    ("time_window", range(29, 37), 8),
    ("ratio_share", range(37, 43), 6),
    ("ranking", range(43, 48), 5),
    ("soft_delete_cancelled", range(48, 51), 3),
]
ALLOWED_EQUIV = {"scalar_numeric", "set_unordered_by_columns", "ordered_rows"}
ALLOWED_ROUTES = {"ANALYTICAL", "METADATA", "CHITCHAT", "UNSUPPORTED"}


def _load(name: str) -> dict[str, Any]:
    return json.loads((SUITES / name).read_text())


@pytest.fixture(scope="module")
def suite() -> dict[str, Any]:
    return _load("sales_v1.json")


@pytest.fixture(scope="module")
def negative() -> dict[str, Any]:
    return _load("sales_v1_negative.json")


@pytest.fixture(scope="module")
def verify_map() -> dict[str, str]:
    return json.loads(VERIFY_FILE.read_text())["verify_sql"]


def _tables_in(sql: str) -> set[str]:
    toks = {m.lower() for m in re.findall(r"(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", sql, re.I)}
    return toks & FIXTURE_TABLES


# ── static checks (no database) ─────────────────────────────────────────────


def test_suite_has_50_unique_records(suite: dict[str, Any]) -> None:
    ids = [r["id"] for r in suite["records"]]
    assert len(ids) == 50
    assert len(set(ids)) == 50


def test_composition_matches_the_contract(suite: dict[str, Any]) -> None:
    by_num = {int(r["id"].split("-")[1]): r for r in suite["records"]}
    for name, rng, count in SLICES:
        got = sum(1 for n in rng if n in by_num)
        assert got == count, f"slice {name}: expected {count}, got {got}"


def test_record_shape_and_vocabulary(suite: dict[str, Any]) -> None:
    required = {
        "id", "question", "connection_fixture", "expected_tables", "gold_sql",
        "result_equivalence", "expected_chart_type", "tags", "difficulty",
        "verification",
    }
    for r in suite["records"]:
        assert required <= set(r), f"{r['id']} missing {required - set(r)}"
        assert r["connection_fixture"] == "sales_pg"
        assert r["result_equivalence"] in ALLOWED_EQUIV
        assert r["difficulty"] in {"easy", "medium", "hard"}
        assert r["verification"] in {"dual_form", "single_form"}
        assert r["tags"], f"{r['id']} has no tags"


def test_expected_tables_are_real_and_match_gold(suite: dict[str, Any]) -> None:
    for r in suite["records"]:
        exp = set(r["expected_tables"])
        assert exp, f"{r['id']} lists no expected_tables"
        assert exp <= FIXTURE_TABLES, f"{r['id']} names unknown tables {exp - FIXTURE_TABLES}"
        used = _tables_in(r["gold_sql"])
        assert used == exp, f"{r['id']} gold touches {used} but expected_tables={exp}"


def test_dual_form_records_have_a_verify_query(suite: dict[str, Any], verify_map: dict[str, str]) -> None:
    for r in suite["records"]:
        if r["verification"] == "dual_form":
            assert r["id"] in verify_map, f"{r['id']} marked dual_form but has no verify query"


def test_negative_set_never_expects_sql(negative: dict[str, Any]) -> None:
    recs = negative["records"]
    assert len(recs) == 10
    for r in recs:
        assert r["expects_sql"] is False
        assert r["expected_route"] in ALLOWED_ROUTES
        assert "gold_sql" not in r
    # the required mix: metadata, chit-chat, write requests, 2 unanswerable
    cats = [r["category"] for r in recs]
    assert cats.count("unanswerable") == 2
    assert "metadata" in cats and "chitchat" in cats and "write_request" in cats


# ── the comments overlay (static; the commented arm of the A/B) ──────────────
#
# `sales_comments.sql` is loaded on top of the seed by `--comments`. A misspelt
# object name in it aborts the fixture load, which is a forty-minute paid run
# that dies at the first step — so the names are checked here, for free.


def _seed_columns() -> dict[str, set[str]]:
    """table -> its declared columns, parsed out of the seed's CREATE TABLEs."""
    sql = SEED_FILE.read_text()
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", sql, re.S):
        body = re.sub(r"--[^\n]*", "", m.group(2))
        cols = set()
        for line in body.split("\n"):
            line = line.strip()
            first = line.split(" ")[0].strip(",")
            if first and first.upper() not in {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"}:
                cols.add(first.lower())
        out[m.group(1).lower()] = cols
    return out


def _overlay() -> str:
    return COMMENTS_FILE.read_text()


def test_the_comments_overlay_names_only_real_objects() -> None:
    sql, cols = _overlay(), _seed_columns()
    for table in re.findall(r"COMMENT ON TABLE (\w+) IS", sql):
        assert table.lower() in FIXTURE_TABLES, f"comment on unknown table {table}"
    for table, col in re.findall(r"COMMENT ON COLUMN (\w+)\.(\w+) IS", sql):
        assert table.lower() in cols, f"comment on unknown table {table}"
        assert col.lower() in cols[table.lower()], f"comment on unknown column {table}.{col}"


def test_the_comments_overlay_covers_enough_of_the_schema() -> None:
    """The plan asks for ~15 tables and ~40 columns — enough that retrieval can
    hardly select a table the DBA never documented, which would measure the
    fixture rather than the feature."""
    sql = _overlay()
    assert len(re.findall(r"COMMENT ON TABLE ", sql)) >= 15
    assert len(re.findall(r"COMMENT ON COLUMN ", sql)) >= 40
    assert "COMMENT ON DATABASE sales IS" in sql      # seeds `business_context`
    assert "COMMENT ON SCHEMA public IS" in sql


def test_the_two_planted_comments_are_still_planted() -> None:
    """One stale and one wrong, on purpose (see the overlay's header and §10 of
    docs/catalog-metadata-plan.md). A future reader who "fixes" either of them
    quietly turns the A/B into a measurement against perfect documentation,
    which no real catalog is — so the plants are asserted, not just commented."""
    sql = _overlay()
    stale = re.search(r"COMMENT ON COLUMN customers\.segment IS\s*'([^']*)'", sql)
    assert stale and "Reseller" in stale.group(1), (
        "the stale plant is gone: customers.segment must still list the retired "
        "Reseller tier, which the seed no longer writes"
    )
    wrong = re.search(r"COMMENT ON COLUMN orders\.subtotal IS\s*'([^']*)'", sql)
    assert wrong and "actually paid" in wrong.group(1), (
        "the wrong plant is gone: orders.subtotal must still claim to be what "
        "the customer paid, which is orders.total_amount"
    )


def test_no_comment_carries_a_double_quote() -> None:
    """The renderer wraps a comment in "quotes" and does not escape what is
    inside, so an inner double quote makes the boundary ambiguous to a reader
    and to the model — the legend line's whole job is that the quotes mark where
    the DBA's prose starts and stops. Containment does not depend on this (the
    newline strip is what stops a forged section header); legibility does."""
    sql = _overlay()
    bodies = re.findall(r"COMMENT ON (?:TABLE|COLUMN|DATABASE|SCHEMA) \S+ IS\s*'((?:[^']|'')*)'", sql)
    assert bodies, "no comments parsed — the regex or the file changed shape"
    for body in bodies:
        assert '"' not in body, f"inner double quote in: {body[:80]}…"


def test_no_comment_exceeds_the_stored_caps() -> None:
    """`clean_comment` truncates past 400 (table) / 240 (column) chars. A fixture
    comment that needs truncating measures the truncator, not the feature."""
    sql = _overlay()
    for body in re.findall(r"COMMENT ON TABLE \w+ IS\s*'((?:[^']|'')*)'", sql):
        assert len(body.replace("''", "'")) <= 400
    for body in re.findall(r"COMMENT ON COLUMN \w+\.\w+ IS\s*'((?:[^']|'')*)'", sql):
        assert len(body.replace("''", "'")) <= 240


# ── live checks (need the fixture) ──────────────────────────────────────────


async def _connect() -> Any:
    asyncpg = pytest.importorskip("asyncpg")
    try:
        conn = await asyncpg.connect(dsn=DSN, timeout=5)
    except Exception as exc:  # noqa: BLE001 - any connect failure => skip
        pytest.skip(f"no sales fixture reachable at {DSN}: {exc}")
    # Guard against pointing at the pre-Task-1 schema.
    has = await conn.fetchval("SELECT to_regclass('public.sales_daily_rollup')")
    if has is None:
        await conn.close()
        pytest.skip("fixture reachable but not the wide Task-1 schema; run `make fixtures`")
    return conn


def _norm(rows: list[Any]) -> list[tuple[Any, ...]]:
    out = []
    for row in rows:
        cells = []
        for v in row.values():
            try:
                cells.append(round(float(v), 4))
            except (TypeError, ValueError):
                cells.append(v)
        out.append(tuple(cells))
    return out


def _nonempty(rows: list[Any]) -> bool:
    if len(rows) >= 2:
        return True
    if len(rows) == 1 and len(rows[0]) == 1:
        return next(iter(rows[0].values())) is not None
    return False


@pytest.mark.asyncio
async def test_every_gold_query_is_nonempty_and_agrees(suite: dict[str, Any], verify_map: dict[str, str]) -> None:
    conn = await _connect()
    try:
        problems: list[str] = []
        for r in suite["records"]:
            gold = await conn.fetch(r["gold_sql"])
            if not _nonempty(gold):
                problems.append(f"{r['id']}: gold returned {len(gold)} rows / null scalar")
                continue
            vsql = verify_map.get(r["id"])
            if vsql is None:
                continue
            verify = await conn.fetch(vsql)
            g, v = _norm(gold), _norm(verify)
            if r["result_equivalence"] == "ordered_rows":
                agree = g == v
            else:
                agree = sorted(g, key=repr) == sorted(v, key=repr)
            if not agree:
                problems.append(f"{r['id']}: gold disagrees with verify ({len(g)} vs {len(v)} rows)")
        assert not problems, "golden-set failures:\n" + "\n".join(problems)
    finally:
        await conn.close()
