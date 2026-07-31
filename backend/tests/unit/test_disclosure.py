"""Disclosure policy: what may leave the customer's database."""
from __future__ import annotations

from app.domain.ports.database import ResultColumn
from app.pipeline.disclosure import disclose
from app.pipeline.state import ExecutionResult

RESULT = ExecutionResult(
    columns=[
        ResultColumn(name="region", db_type="text", semantic_type="nominal"),
        ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
    ],
    rows=[[f"Region {i}", i * 100] for i in range(200)],
    row_count=200,
)


def test_none_shares_no_rows_and_no_column_names() -> None:
    result = disclose(RESULT, "NONE")
    assert result.rows == []
    assert result.columns == []
    assert "200" in result.note


def test_aggregate_shares_shape_but_not_values() -> None:
    result = disclose(RESULT, "AGGREGATE")
    assert result.rows == []
    assert "region" in result.render()
    assert "Region 5" not in result.render()


def test_sample_is_bounded() -> None:
    result = disclose(RESULT, "SAMPLE")
    assert len(result.rows) == 50
    assert "200" in result.note


def test_full_shares_everything_returned() -> None:
    result = disclose(RESULT, "FULL")
    assert len(result.rows) == 200


def test_sample_of_small_result_has_no_truncation_note() -> None:
    small = ExecutionResult(
        columns=RESULT.columns, rows=[["North", 10]], row_count=1
    )
    assert disclose(small, "SAMPLE").note == ""


def test_a_capped_result_says_so_under_every_policy() -> None:
    # 1000 rows is the platform's cap, not the answer: a model told only the
    # row count narrates it as "the top 1000 customers".
    capped = ExecutionResult(
        columns=[ResultColumn(name="name", db_type="text")],
        rows=[[f"c{i}"] for i in range(1000)],
        row_count=1000,
        truncated=True,
    )
    for policy in ("NONE", "AGGREGATE", "SAMPLE", "FULL"):
        assert "partial result" in disclose(capped, policy).note
