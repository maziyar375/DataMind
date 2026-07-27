"""Column hints must mean the same thing on all four engines.

Each connector reads a different catalog, but the generator sees one format
and is told to filter using exactly the values in `∈ {…}`. So the property
under test everywhere below is the same: **a value list is emitted only when
it is provably the complete domain**, and dropped entirely otherwise.

The per-engine folds are pure functions over catalog rows, so they are tested
directly with the rows each engine really returns — no container needed.
"""
from __future__ import annotations

import json

import pytest

from app.domain.ports.database import ColumnInfo
from app.domain.value_objects import HintBudget
from app.infra.connectors.hints import (
    HINT_MAX_CARDINALITY,
    ColumnHints,
    ProbeTarget,
    apply_probe,
    clean_values,
    enforce_budget,
    normalise_distinct,
    null_fraction_from_counts,
    plan_probes,
    quote_ident,
)
from app.infra.connectors.mssql import _build_hints as mssql_hints
from app.infra.connectors.mysql import _build_hints as mysql_hints
from app.infra.connectors.oracle import _build_hints as oracle_hints


# ── the shared contract ─────────────────────────────────────────────────────
def test_value_list_is_all_or_nothing() -> None:
    """Over the cap is dropped, not trimmed: a trimmed list would make the
    prompt's "filter using exactly these" instruction a lie."""
    assert clean_values([f"v{i}" for i in range(HINT_MAX_CARDINALITY)]) != []
    assert clean_values([f"v{i}" for i in range(HINT_MAX_CARDINALITY + 1)]) == []


def test_prose_disqualifies_the_whole_column() -> None:
    assert clean_values(["ok", "x" * 200]) == []


def test_values_are_sorted_and_deduped_for_a_stable_prompt() -> None:
    assert clean_values(["web", "phone", "web"]) == ["phone", "web"]


@pytest.mark.parametrize(
    ("name", "style", "expected"),
    [
        ('weird"name', '"', '"weird""name"'),
        ("weird`name", "`", "`weird``name`"),
        ("weird]name", "[", "[weird]]name]"),
        ("orders", '"', '"orders"'),
    ],
)
def test_identifiers_are_escaped_not_just_wrapped(
    name: str, style: str, expected: str
) -> None:
    """Names come from the customer's catalog, so a quote character in one is
    a real possibility and must not end the quoted identifier early."""
    assert quote_ident(name, style=style) == expected


def test_negative_n_distinct_is_a_fraction_of_rows() -> None:
    assert normalise_distinct(-1.0, 500) == 500      # every value unique
    assert normalise_distinct(-0.5, 500) == 250
    assert normalise_distinct(5, 500) == 5
    assert normalise_distinct(None, 500) is None


def test_null_fraction_needs_a_row_count() -> None:
    assert null_fraction_from_counts(30, 100) == 0.3
    assert null_fraction_from_counts(30, 0) is None
    assert null_fraction_from_counts(None, 100) is None


def test_probe_rejects_an_over_long_result() -> None:
    """The probe asks for cap+1 so an incomplete domain is recognisable."""
    hints: dict[tuple[str, str, str], ColumnHints] = {}
    target = ProbeTarget(schema="public", table="orders", column="status")

    apply_probe(hints, target, [f"v{i}" for i in range(HINT_MAX_CARDINALITY + 1)])
    assert hints == {}

    apply_probe(hints, target, ["web", "phone"])
    assert hints[("public", "orders", "status")].sample_values == ["phone", "web"]


# ── which columns earn a probe ──────────────────────────────────────────────
TEXT = frozenset({"varchar", "text"})


def test_probe_skips_sensitive_wide_and_already_known_columns() -> None:
    columns = {
        ("s", "t", "status"): "varchar",       # wanted
        ("s", "t", "email"): "varchar",        # sensitive
        ("s", "t", "total"): "numeric",        # not text
        ("s", "t", "sku"): "varchar",          # catalog already answered
        ("s", "t", "code"): "varchar",         # known to be too wide
        ("s", "big", "kind"): "varchar",       # table too large to scan
    }
    known = {
        ("s", "t", "sku"): ColumnHints(sample_values=["a", "b"]),
        ("s", "t", "code"): ColumnHints(distinct_count=10_000),
    }
    targets = plan_probes(
        columns=columns, known=known,
        row_counts={("s", "t"): 1_000, ("s", "big"): 500_000_000},
        text_types=TEXT,
    )
    assert [t.column for t in targets] == ["status"]


def test_probe_plan_is_capped() -> None:
    columns = {("s", "t", f"c{i}"): "varchar" for i in range(500)}
    targets = plan_probes(
        columns=columns, known={}, row_counts={("s", "t"): 10},
        text_types=TEXT, limit=7,
    )
    assert len(targets) == 7


def test_parameterised_types_are_recognised() -> None:
    """`varchar(50)` and `NVARCHAR2(20 CHAR)` are still text."""
    columns = {("s", "t", "a"): "varchar(50)", ("s", "t", "b"): "NVARCHAR2(20 CHAR)"}
    targets = plan_probes(
        columns=columns, known={}, row_counts={("s", "t"): 10},
        text_types=frozenset({"varchar", "nvarchar2"}),
    )
    assert len(targets) == 2


# ── MySQL ───────────────────────────────────────────────────────────────────
def _mysql_col(name: str, data_type: str, column_type: str) -> tuple:
    return ("db", "orders", name, data_type, "YES", 1, column_type)


def test_mysql_enum_is_the_exact_domain() -> None:
    """The declared type *is* the domain — better than any statistic."""
    hints = mysql_hints(
        col_rows=[_mysql_col("channel", "enum", "enum('web','phone','partner')")],
        cardinality_rows=[], histogram_rows=[],
    )
    record = hints[("db", "orders", "channel")]
    assert record.sample_values == ["partner", "phone", "web"]
    assert record.distinct_count == 3


def test_mysql_enum_escapes_doubled_quotes() -> None:
    hints = mysql_hints(
        col_rows=[_mysql_col("label", "enum", "enum('it''s','ok')")],
        cardinality_rows=[], histogram_rows=[],
    )
    assert hints[("db", "orders", "label")].sample_values == ["it's", "ok"]


def test_mysql_enum_on_a_sensitive_column_yields_no_values() -> None:
    hints = mysql_hints(
        col_rows=[_mysql_col("city", "enum", "enum('Berlin','Paris')")],
        cardinality_rows=[], histogram_rows=[],
    )
    record = hints[("db", "orders", "city")]
    assert record.sample_values == []
    assert record.distinct_count == 2      # the count is not the disclosure


def test_mysql_singleton_histogram_gives_values_equi_height_does_not() -> None:
    singleton = json.dumps({
        "histogram-type": "singleton",
        "null-values": 0.25,
        "buckets": [["web", 0.5], ["phone", 1.0]],
    })
    equi = json.dumps({
        "histogram-type": "equi-height",
        "null-values": 0.0,
        "buckets": [["a", "m", 0.5, 10], ["n", "z", 1.0, 10]],
    })
    hints = mysql_hints(
        col_rows=[], cardinality_rows=[],
        histogram_rows=[
            ("db", "orders", "channel", singleton),
            ("db", "orders", "notes", equi),
        ],
    )
    assert hints[("db", "orders", "channel")].sample_values == ["phone", "web"]
    assert hints[("db", "orders", "channel")].null_fraction == 0.25
    assert hints[("db", "orders", "notes")].sample_values == []


def test_mysql_index_cardinality_becomes_distinct_count() -> None:
    hints = mysql_hints(
        col_rows=[], histogram_rows=[],
        cardinality_rows=[("db", "orders", "customer_id", 4200)],
    )
    assert hints[("db", "orders", "customer_id")].distinct_count == 4200


def test_mysql_malformed_histogram_is_ignored() -> None:
    hints = mysql_hints(
        col_rows=[], cardinality_rows=[],
        histogram_rows=[("db", "orders", "c", "not json")],
    )
    assert hints[("db", "orders", "c")].sample_values == []


# ── Oracle ──────────────────────────────────────────────────────────────────
def _oracle_col(name: str, data_type: str) -> tuple:
    return ("APP", "ORDERS", name, data_type, "Y", 1)


def test_oracle_frequency_histogram_is_the_complete_domain() -> None:
    hints = oracle_hints(
        col_rows=[_oracle_col("STATUS", "VARCHAR2")],
        stat_rows=[("APP", "ORDERS", "STATUS", 3, 120)],
        histogram_rows=[
            ("APP", "ORDERS", "STATUS", "completed"),
            ("APP", "ORDERS", "STATUS", "pending"),
            ("APP", "ORDERS", "STATUS", "shipped"),
        ],
        counts={("APP", "ORDERS"): 1200},
    )
    record = hints[("APP", "ORDERS", "STATUS")]
    assert record.sample_values == ["completed", "pending", "shipped"]
    assert record.null_fraction == 0.1          # 120 nulls of 1200 rows


def test_oracle_partial_histogram_is_dropped() -> None:
    """Fewer endpoints than distinct values means it is not the whole domain."""
    hints = oracle_hints(
        col_rows=[_oracle_col("STATUS", "VARCHAR2")],
        stat_rows=[("APP", "ORDERS", "STATUS", 5, 0)],
        histogram_rows=[
            ("APP", "ORDERS", "STATUS", "completed"),
            ("APP", "ORDERS", "STATUS", "pending"),
        ],
        counts={("APP", "ORDERS"): 1200},
    )
    record = hints[("APP", "ORDERS", "STATUS")]
    assert record.sample_values == []
    assert record.distinct_count == 5           # the count still stands


def test_oracle_stats_without_histogram_still_give_counts() -> None:
    hints = oracle_hints(
        col_rows=[_oracle_col("EMPLOYEE_ID", "NUMBER")],
        stat_rows=[("APP", "ORDERS", "EMPLOYEE_ID", 40, 360)],
        histogram_rows=[], counts={("APP", "ORDERS"): 1200},
    )
    record = hints[("APP", "ORDERS", "EMPLOYEE_ID")]
    assert record.distinct_count == 40
    assert record.null_fraction == 0.3
    assert record.sample_values == []


# ── SQL Server ──────────────────────────────────────────────────────────────
def test_mssql_full_scan_histogram_is_usable() -> None:
    rows = [
        ("dbo", "orders", "status", "completed", 1000, 1000, 1000),
        ("dbo", "orders", "status", "pending", 1000, 1000, 1000),
    ]
    hints = mssql_hints(rows)
    assert hints[("dbo", "orders", "status")].sample_values == [
        "completed", "pending"
    ]


def test_mssql_sampled_histogram_is_rejected() -> None:
    """A histogram built from a sample cannot be a complete domain."""
    rows = [
        ("dbo", "orders", "status", "completed", 1000, 250, 1000),
        ("dbo", "orders", "status", "pending", 1000, 250, 1000),
    ]
    assert mssql_hints(rows) == {}


def test_mssql_sensitive_column_is_floored() -> None:
    rows = [("dbo", "customers", "city", "Berlin", 10, 10, 10)]
    assert mssql_hints(rows) == {}


# ── merging sources ─────────────────────────────────────────────────────────
def test_catalog_wins_over_probe() -> None:
    catalog = ColumnHints(distinct_count=5, null_fraction=0.1)
    probe = ColumnHints(distinct_count=99, sample_values=["a", "b"])
    merged = catalog.merged_with(probe)

    assert merged.distinct_count == 5           # authoritative value kept
    assert merged.null_fraction == 0.1
    assert merged.sample_values == ["a", "b"]   # probe fills the gap


def test_as_kwargs_omits_the_unknown() -> None:
    assert ColumnHints().as_kwargs() == {}
    assert ColumnHints(distinct_count=3).as_kwargs() == {"distinct_count": 3}


# ── the capture-side gate ───────────────────────────────────────────────────
FULL_HINTS = {
    ("s", "t", "c"): ColumnHints(
        distinct_count=3, null_fraction=0.2,
        sample_values=["a", "b"], min_value="1", max_value="9",
    )
}


def test_none_captures_nothing_derived_from_data() -> None:
    """Render already blocks these from the model. This is the stricter
    question: whether our own database should hold a copy at all."""
    assert enforce_budget(FULL_HINTS, HintBudget.from_policy("NONE")) == {}


def test_aggregate_captures_counts_but_no_values_or_ranges() -> None:
    kept = enforce_budget(FULL_HINTS, HintBudget.from_policy("AGGREGATE"))
    record = kept[("s", "t", "c")]

    assert record.distinct_count == 3
    assert record.null_fraction == 0.2
    assert record.sample_values == []
    assert record.min_value is None


def test_sample_keeps_values_and_temporal_ranges() -> None:
    kept = enforce_budget(FULL_HINTS, HintBudget.from_policy("SAMPLE"))
    assert kept[("s", "t", "c")].sample_values == ["a", "b"]
    assert kept[("s", "t", "c")].min_value == "1"


def test_unknown_policy_captures_nothing() -> None:
    assert enforce_budget(FULL_HINTS, HintBudget.from_policy("WHATEVER")) == {}


# ── the stored snapshot shape ───────────────────────────────────────────────
def test_hints_survive_serialisation_to_the_snapshot() -> None:
    """The bug this guards: two hand-written serialisers dropped every field
    added after they were written, so captured hints never reached storage."""
    column = ColumnInfo(
        name="status", data_type="text",
        distinct_count=3, null_fraction=0.2, sample_values=["a", "b"],
    )
    stored = column.as_dict()

    assert stored["sample_values"] == ["a", "b"]
    assert stored["distinct_count"] == 3
    assert stored["null_fraction"] == 0.2


def test_hintless_column_keeps_the_original_snapshot_shape() -> None:
    assert ColumnInfo(name="id", data_type="bigint").as_dict() == {
        "name": "id", "data_type": "bigint", "nullable": True,
        "is_primary_key": False, "is_foreign_key": False, "references": None,
    }
