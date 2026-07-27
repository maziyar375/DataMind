"""The schema block is customer data too — it must obey the disclosure policy.

`test_disclosure.py` covers the result side. This covers the other direction:
the per-column content hints that reach the model on *every* question, which
is why the floor here is stricter than the one applied to a single result.
"""
from __future__ import annotations

import pytest

from app.domain.value_objects import DisclosurePolicy, HintBudget, is_sensitive_column
from app.pipeline.state import RetrievedContext

CONTEXT = RetrievedContext(
    dialect="postgres",
    tables=[
        {
            "schema": "public",
            "name": "orders",
            "approx_row_count": 24000,
            "columns": [
                {"name": "id", "data_type": "bigint", "is_primary_key": True},
                {
                    "name": "status",
                    "data_type": "text",
                    "distinct_count": 5,
                    "sample_values": ["cancelled", "completed", "pending"],
                },
                {
                    "name": "employee_id",
                    "data_type": "bigint",
                    "nullable": True,
                    "is_foreign_key": True,
                    "references": "public.employees.id",
                    "distinct_count": 40,
                    "null_fraction": 0.3,
                },
                {
                    "name": "order_date",
                    "data_type": "date",
                    "min_value": "2023-01-04",
                    "max_value": "2026-07-19",
                },
                {
                    "name": "total_amount",
                    "data_type": "numeric",
                    "min_value": "1.50",
                    "max_value": "9912.00",
                },
            ],
        }
    ],
)


def test_none_emits_structure_only() -> None:
    rendered = CONTEXT.render(DisclosurePolicy.NONE)

    assert "status text" in rendered          # structure survives
    assert "completed" not in rendered        # values do not
    assert "distinct" not in rendered
    assert "% null" not in rendered
    assert "2023-01-04" not in rendered
    assert "24,000 rows" not in rendered      # volume is data too


def test_default_policy_is_closed() -> None:
    """A caller that forgets the argument must not widen a disclosure."""
    assert CONTEXT.render() == CONTEXT.render(DisclosurePolicy.NONE)


def test_aggregate_counts_without_values() -> None:
    rendered = CONTEXT.render(DisclosurePolicy.AGGREGATE)

    assert "5 distinct" in rendered           # it is categorical…
    assert "completed" not in rendered        # …without saying what it holds
    assert "30% null" in rendered
    assert "24,000 rows" in rendered
    assert "2023-01-04" not in rendered


def test_sample_lists_values_and_dates_but_not_numeric_ranges() -> None:
    rendered = CONTEXT.render(DisclosurePolicy.SAMPLE)

    assert "∈ {cancelled, completed, pending}" in rendered
    assert "2023-01-04…2026-07-19" in rendered
    assert "9912.00" not in rendered          # numeric range is FULL-only
    assert "A [bracket] after a column" in rendered   # legend appears with hints


def test_full_adds_numeric_ranges() -> None:
    rendered = CONTEXT.render(DisclosurePolicy.FULL)

    assert "1.50…9912.00" in rendered
    assert "∈ {cancelled, completed, pending}" in rendered


def test_no_legend_when_nothing_to_explain() -> None:
    """A hintless snapshot must render byte-identically to the old format, so
    the v2 baseline stays comparable for connections with no statistics."""
    bare = RetrievedContext(
        dialect="postgres",
        tables=[{
            "schema": "public", "name": "t", "approx_row_count": 5,
            "columns": [{"name": "id", "data_type": "bigint"}],
        }],
    )
    rendered = bare.render(DisclosurePolicy.FULL)

    assert "[bracket]" not in rendered
    assert rendered == "Dialect: postgres\n\nTables:\n- public.t(id bigint)  (~5 rows)"


def test_value_list_is_capped_and_marked() -> None:
    wide = RetrievedContext(
        dialect="postgres",
        tables=[{
            "schema": "public", "name": "t",
            "columns": [{
                "name": "code", "data_type": "text",
                "sample_values": [f"v{i}" for i in range(40)],
            }],
        }],
    )
    rendered = wide.render(DisclosurePolicy.SAMPLE)
    listed = rendered.rsplit("∈ {", 1)[1].split("}")[0]   # last: the legend uses ∈ too

    assert len(listed.split(", ")) == HintBudget.from_policy("SAMPLE").max_values + 1
    assert listed.endswith(", …")   # and the model is told the list is partial


@pytest.mark.parametrize(
    "column",
    ["name", "customer_name", "email", "contact_email", "phone", "city",
     "billing_city", "postal_code", "cust_ref", "tracking_number", "notes"],
)
def test_sensitive_columns_are_floored(column: str) -> None:
    assert is_sensitive_column(column)


@pytest.mark.parametrize(
    "column",
    ["status", "channel", "segment", "capacity", "is_active", "quantity",
     "promo_type", "kind", "priority", "rating"],
)
def test_categorical_columns_are_not_floored(column: str) -> None:
    assert not is_sensitive_column(column)


def test_unknown_policy_fails_closed() -> None:
    assert HintBudget.from_policy("SOMETHING_NEW") == HintBudget()
