"""The AST walk that offers parameters — and, more importantly, refuses them.

This is the one thing in the design no competitor does, because none of them
has a guard that already parses the statement. It is also where the feature
either feels like magic or like noise, so most of this file is about what the
walk must **not** propose: an exclusion inside a `<>`, anything under a `CASE`,
a list, a pattern. A nonsense parameter on first contact is what makes a
curator stop trusting the panel.

The second half proves `parameterize` moves the literal the curator ticked and
nothing else — on the tree, never by string replacement, because `'EMEA'`
appears twice in a statement that filters on it and also mentions it in a
`CASE`.
"""
from __future__ import annotations

import pytest

from app.knowledge import (
    DEFAULT_SUGGESTED,
    ParamType,
    column_type,
    parameterize,
    propose_params,
)

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "customer_id", "data_type": "bigint", "is_foreign_key": True},
            {"name": "created_at", "data_type": "timestamp with time zone"},
            {"name": "order_day", "data_type": "date"},
            {"name": "region", "data_type": "text", "distinct_count": 3},
            {"name": "status", "data_type": "text"},
            {"name": "amount", "data_type": "numeric(12,2)"},
            {"name": "is_gift", "data_type": "boolean"},
        ],
    }
]


def _propose(sql: str):
    return propose_params(sql, dialect="postgres", tables=TABLES)


def _named(sql: str) -> dict[str, object]:
    return {p.name: p for p in _propose(sql)}


# ── what it proposes ─────────────────────────────────────────────────────
def test_a_lower_bound_on_a_date_column_is_a_from_date() -> None:
    proposal = _named("SELECT 1 FROM orders o WHERE o.created_at >= '2026-01-01'")
    assert proposal["from_date"].eligible
    assert proposal["from_date"].type is ParamType.DATETIME
    assert proposal["from_date"].literal == "'2026-01-01'"


def test_an_upper_bound_on_a_date_column_is_a_to_date() -> None:
    assert "to_date" in _named("SELECT 1 FROM orders o WHERE o.created_at < '2026-01-01'")


def test_a_between_on_a_date_column_proposes_both_bounds_paired() -> None:
    names = list(_named(
        "SELECT 1 FROM orders o WHERE o.created_at BETWEEN '2026-01-01' AND '2026-06-01'"
    ))
    assert names == ["from_date", "to_date"]


def test_a_date_column_typed_date_is_a_day_not_a_timestamp() -> None:
    # The binder resolves the two differently: "last month" is a day range on
    # one and an instant range on the other.
    assert _named(
        "SELECT 1 FROM orders o WHERE o.order_day >= '2026-01-01'"
    )["from_date"].type is ParamType.DATE


def test_an_equality_against_a_categorical_column_is_named_for_the_column() -> None:
    proposal = _named("SELECT 1 FROM orders o WHERE o.region = 'EMEA'")["region"]
    assert proposal.eligible and proposal.type is ParamType.STRING
    # The comment is what the curator reads *and* what Phase 2 binds against.
    assert "3" in proposal.comment


def test_a_comparison_against_a_measure_is_a_threshold() -> None:
    assert _named(
        "SELECT 1 FROM orders o WHERE o.amount > 10000"
    )["threshold"].type is ParamType.NUMBER


def test_an_aggregate_in_having_is_a_threshold_too() -> None:
    # `HAVING SUM(amount) > 10000` is the clearest threshold there is;
    # refusing it because the left side is not a bare column would be perverse.
    assert "threshold" in _named(
        "SELECT o.region FROM orders o GROUP BY o.region HAVING SUM(o.amount) > 10000"
    )


def test_a_key_compared_with_gt_is_not_called_a_threshold() -> None:
    # It is a paging cursor, not a quantity. Calling it `:threshold` in the
    # editor would be a small lie, and a curator who catches one stops
    # believing the rest of the panel.
    proposals = _named("SELECT 1 FROM orders o WHERE o.id > 5")
    assert "threshold" not in proposals
    assert proposals["id"].type is ParamType.NUMBER


# ── what it refuses, and says why ────────────────────────────────────────
@pytest.mark.parametrize(
    "sql,fragment",
    [
        ("SELECT 1 FROM orders o WHERE o.status <> 'CANCELLED'", "≠"),
        ("SELECT 1 FROM orders o WHERE o.status NOT IN ('A','B')", "exclusion"),
        ("SELECT 1 FROM orders o WHERE o.status IN ('A','B')", "list"),
        ("SELECT 1 FROM orders o WHERE o.status LIKE 'CANC%'", "pattern"),
        ("SELECT 1 FROM orders o WHERE o.status ILIKE 'canc%'", "pattern"),
        (
            "SELECT CASE WHEN o.region = 'EMEA' THEN 1 ELSE 0 END FROM orders o",
            "CASE",
        ),
        (
            "SELECT 1 FROM orders o "
            "WHERE COALESCE(CASE WHEN o.region = 'EMEA' THEN 1 END, 0) = 1",
            "CASE",
        ),
    ],
)
def test_a_refusal_is_returned_with_its_reason_rather_than_hidden(
    sql: str, fragment: str
) -> None:
    # Showing the rejected candidate teaches the rule better than hiding it,
    # and the curator occasionally knows better — so the reason is data the UI
    # renders, not a comment in the walk.
    refused = [p for p in _propose(sql) if not p.eligible]
    assert refused, f"nothing was refused for {sql!r}"
    assert all(not p.suggested for p in refused)
    assert any(fragment in p.reason for p in refused)


def test_a_comparison_that_is_not_about_one_column_proposes_nothing() -> None:
    # `COALESCE(region, 'NA') = 'EMEA'` filters on an expression, not a column,
    # so the walk cannot say what type the slot would hold or what to call it.
    # Silence is the fail-safe direction: a guessed type produces a slot that
    # never binds, which reads to the curator as the feature being broken.
    assert _propose(
        "SELECT 1 FROM orders o WHERE COALESCE(o.region, 'NA') = 'EMEA'"
    ) == []


def test_a_date_trunc_unit_is_never_offered_as_a_parameter() -> None:
    # `'month'` is business logic in an argument list, not a filter. The walk
    # only reads comparisons, which is what keeps it out.
    sql = "SELECT date_trunc('month', o.created_at) FROM orders o"
    assert _propose(sql) == []


def test_a_limit_is_never_offered_as_a_parameter() -> None:
    assert _propose("SELECT o.id FROM orders o LIMIT 10") == []


def test_a_group_by_ordinal_is_never_offered() -> None:
    assert _propose(
        "SELECT o.region, SUM(o.amount) FROM orders o GROUP BY 1 ORDER BY 1"
    ) == []


# ── how many arrive ticked ───────────────────────────────────────────────
def test_only_the_first_two_eligible_proposals_are_ticked() -> None:
    sql = (
        "SELECT 1 FROM orders o WHERE o.region = 'EMEA' "
        "AND o.created_at >= '2026-01-01' AND o.amount > 100 AND o.is_gift = TRUE"
    )
    proposals = _propose(sql)
    ticked = [p for p in proposals if p.suggested]
    assert len(ticked) == DEFAULT_SUGGESTED
    assert [p.name for p in ticked] == ["region", "from_date"]
    # The rest are offered, not hidden: the curator opts in per row.
    assert all(p.eligible for p in proposals if p.name in ("threshold", "is_gift"))


def test_proposals_arrive_in_statement_order() -> None:
    sql = (
        "SELECT 1 FROM orders o WHERE o.region = 'EMEA' "
        "AND o.status <> 'CANCELLED' AND o.created_at >= '2026-01-01'"
    )
    assert [p.name for p in _propose(sql)] == ["region", "status", "from_date"]


def test_two_identical_literals_are_told_apart_by_occurrence() -> None:
    # The editor highlights the literal a row would replace. Two `'EMEA'`s are
    # two rows and it must highlight the right one.
    sql = (
        "SELECT 1 FROM orders o JOIN orders p ON p.id = o.id "
        "WHERE o.region = 'EMEA' AND p.region = 'EMEA'"
    )
    occurrences = [p.occurrence for p in _propose(sql) if p.literal == "'EMEA'"]
    assert occurrences == [0, 1]


def test_colliding_names_are_suffixed_rather_than_overwritten() -> None:
    sql = (
        "SELECT 1 FROM orders o JOIN orders p ON p.id = o.id "
        "WHERE o.region = 'EMEA' AND p.region = 'NA'"
    )
    assert [p.name for p in _propose(sql)] == ["region", "region_2"]


# ── the substitution ─────────────────────────────────────────────────────
def test_parameterize_replaces_only_the_ticked_literal() -> None:
    sql = (
        "SELECT SUM(o.amount) FROM orders o WHERE o.region = 'EMEA' "
        "AND o.status <> 'CANCELLED' AND o.created_at >= '2026-01-01'"
    )
    rewritten, params = parameterize(
        sql, {"region", "from_date"}, dialect="postgres", tables=TABLES
    )
    assert ":region" in rewritten and ":from_date" in rewritten
    # The exclusion was refused and must survive as a literal.
    assert "'CANCELLED'" in rewritten
    assert [p.name for p in params] == ["region", "from_date"]


def test_parameterize_moves_the_filter_and_not_the_case_arm() -> None:
    # The reason this walks the tree instead of calling `str.replace`.
    sql = (
        "SELECT CASE WHEN o.region = 'EMEA' THEN 1 ELSE 0 END AS eu, o.id "
        "FROM orders o WHERE o.region = 'EMEA'"
    )
    rewritten, params = parameterize(
        sql, {"region"}, dialect="postgres", tables=TABLES
    )
    assert rewritten.count("'EMEA'") == 1
    assert "CASE WHEN o.region = 'EMEA'" in rewritten
    assert [p.name for p in params] == ["region"]


def test_a_placeholder_renders_the_same_in_every_dialect() -> None:
    # Postgres' generator spells a placeholder `%(name)s`, which is a driver's
    # binding syntax and not what this store agrees on. One spelling, four
    # engines, or a template authored against MySQL would not read on Oracle.
    sql = "SELECT a FROM orders WHERE region = 'EMEA'"
    tables = [{"schema": "", "name": "orders",
               "columns": [{"name": "a", "data_type": "int"},
                           {"name": "region", "data_type": "text"}]}]
    for dialect in ("postgres", "mysql", "tsql", "oracle"):
        rewritten, _ = parameterize(sql, {"region"}, dialect=dialect, tables=tables)
        assert ":region" in rewritten, dialect
        assert "%(" not in rewritten, dialect


def test_unparseable_sql_proposes_nothing_rather_than_raising() -> None:
    # The editor calls this on every pause in typing; half a statement is the
    # normal case, not an error.
    assert _propose("SELECT FROM WHERE") == []
    assert parameterize("SELECT FROM WHERE", {"x"}, tables=TABLES)[0] == (
        "SELECT FROM WHERE"
    )


def test_an_accepted_name_the_walk_cannot_find_is_ignored() -> None:
    sql = "SELECT o.id FROM orders o WHERE o.region = 'EMEA'"
    rewritten, params = parameterize(sql, {"nonexistent"}, tables=TABLES)
    assert "'EMEA'" in rewritten and params == []


# ── the type table ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "data_type,expected",
    [
        ("timestamp with time zone", ParamType.DATETIME),
        ("datetime2", ParamType.DATETIME),
        ("TIMESTAMP(6)", ParamType.DATETIME),
        ("date", ParamType.DATE),
        ("bigint", ParamType.NUMBER),
        ("NUMBER(10,2)", ParamType.NUMBER),
        ("double precision", ParamType.NUMBER),
        ("boolean", ParamType.BOOLEAN),
        ("bit", ParamType.BOOLEAN),
        ("character varying(50)", ParamType.STRING),
        ("", ParamType.STRING),
    ],
)
def test_engine_type_names_map_to_slot_types(data_type: str, expected) -> None:
    # Substring matching on purpose: four engines spell the same type six ways,
    # and a table of exact names would be wrong the first time someone
    # connected an engine nobody tested against.
    assert column_type(data_type) is expected
