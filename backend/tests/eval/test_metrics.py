"""Metric functions are pure; they are tested without a database or a model."""
from __future__ import annotations

from app.eval import metrics
from app.eval.metrics import (
    OUTCOME_MATCH,
    OUTCOME_MISMATCH,
    OUTCOME_VALIDATION_FAILED,
    RecordOutcome,
    aggregate,
    exact_match,
    format_report,
    percentile,
    result_sets_match,
    retrieval_recall,
    values_equal,
)

# ── value & result comparison (execution accuracy) ──────────────────────────


def test_values_equal_numeric_tolerance() -> None:
    assert values_equal(1.0, 1.0 + 5e-7)
    assert not values_equal(1.0, 1.01)
    assert values_equal("1.0", 1)           # string/number cross-type
    assert values_equal(None, None)
    assert not values_equal(None, 0)
    assert values_equal("Europe", "Europe ")  # trimmed
    assert not values_equal(True, 1)          # bool is not a number here


def test_values_equal_absorbs_two_decimal_rounding() -> None:
    # Golds report figures with round(x, 2); a full-precision but numerically
    # identical candidate must still count as equal (real cases from DeepSeek).
    assert values_equal(957.42, 957.416)     # gold round(sum/count,2) vs AVG(x)
    assert values_equal(24.44, 24.43781443)  # gold round(avg(margin),2) vs raw
    # ...but a genuinely different figure (>half a cent) is still not equal.
    assert not values_equal(0.02, 0.03)
    assert not values_equal(957.42, 957.99)


def test_unordered_scalar_rounding_matches() -> None:
    assert result_sets_match([[957.42]], [[957.416]], "scalar_numeric")


def test_unordered_match_ignores_row_order() -> None:
    gold = [[1, "a"], [2, "b"]]
    cand = [[2, "b"], [1, "a"]]
    assert result_sets_match(gold, cand, "set_unordered_by_columns")
    assert not result_sets_match(gold, cand, "ordered_rows")


def test_ordered_match_respects_order() -> None:
    gold = [["x", 3], ["y", 2], ["z", 1]]
    assert result_sets_match(gold, list(gold), "ordered_rows")
    assert not result_sets_match(gold, list(reversed(gold)), "ordered_rows")


def test_match_applies_numeric_tolerance_positionally() -> None:
    assert result_sets_match([[100.0000001]], [[100.0]], "set_unordered_by_columns")
    assert not result_sets_match([[1], [2]], [[1]], "set_unordered_by_columns")  # size differs


def test_exact_match_is_normalised_but_diagnostic() -> None:
    assert exact_match("SELECT 1;", "select   1")
    assert not exact_match("SELECT a FROM t", "SELECT b FROM t")
    assert not exact_match("SELECT 1", None)


# ── retrieval recall ────────────────────────────────────────────────────────


def test_retrieval_recall_uses_bare_names() -> None:
    assert retrieval_recall(["orders", "order_items"], ["public.orders"]) == 0.5
    assert retrieval_recall(["orders"], ["public.orders", "public.customers"]) == 1.0
    assert retrieval_recall([], []) == 1.0


# ── percentiles ─────────────────────────────────────────────────────────────


def test_percentile_interpolates() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([42.0], 95) == 42.0
    vals = [10.0, 20.0, 30.0, 40.0]
    assert percentile(vals, 50) == 25.0
    assert percentile(vals, 0) == 10.0
    assert percentile(vals, 100) == 40.0


# ── aggregation ─────────────────────────────────────────────────────────────


def _outcome(**kw: object) -> RecordOutcome:
    data: dict[str, object] = {"record_id": "x", "tags": ["t"], "difficulty": "easy", **kw}
    return RecordOutcome(**data)  # type: ignore[arg-type]


def test_aggregate_headline_and_breakdowns() -> None:
    outs = [
        _outcome(record_id="a", tags=["join"], outcome=OUTCOME_MATCH, execution_match=True,
                 retrieval_recall=1.0, retrieval_hit=True, parse_ok=True, validated_ok=True,
                 execution_ok=True, succeeded_on_attempt=1, llm_ms=100, validate_ms=2, db_ms=5,
                 total_ms=110, prompt_tokens=50, completion_tokens=10, exact_match=True),
        _outcome(record_id="b", tags=["join"], outcome=OUTCOME_MISMATCH,
                 retrieval_recall=0.5, retrieval_hit=False, parse_ok=True, validated_ok=True,
                 execution_ok=True, succeeded_on_attempt=2, llm_ms=200, validate_ms=4, db_ms=9,
                 total_ms=230, prompt_tokens=80, completion_tokens=20),
        _outcome(record_id="c", tags=["bridge"], outcome=OUTCOME_VALIDATION_FAILED,
                 retrieval_recall=0.33, retrieval_hit=False, parse_ok=True, validated_ok=False,
                 execution_ok=False, succeeded_on_attempt=None, policy_violations=["E_UNKNOWN_COLUMN"],
                 llm_ms=300, validate_ms=6, db_ms=0, total_ms=320, prompt_tokens=90, completion_tokens=30),
    ]
    r = aggregate(outs)
    assert r.n == 3
    assert r.execution_accuracy == round(1 / 3, 4)                 # only 'a' matched
    assert r.retrieval_full_hit_rate == round(1 / 3, 4)
    assert r.execution_success_rate == round(2 / 3, 4)
    assert r.policy_violation_rate == round(1 / 3, 4)
    assert r.policy_violations_by_rule == {"E_UNKNOWN_COLUMN": 1}
    assert r.repair_distribution == {"attempt_1": 1, "attempt_2": 1, "failed": 1}
    assert r.exact_match_rate == round(1 / 3, 4)
    # per-tag: join has a+b (one match), bridge has c (no match)
    tags = {tb.tag: tb for tb in r.per_tag}
    assert tags["join"].n == 2 and tags["join"].execution_accuracy == 0.5
    assert tags["bridge"].n == 1 and tags["bridge"].execution_accuracy == 0.0
    assert r.latency_ms["llm"]["p50"] > 0


def test_repair_violations_are_a_subset_attributed_to_the_retry() -> None:
    """A rejection on attempt 2 is a different diagnosis from one on attempt 1.

    Only the repair prompts drop `GENERATE_SYSTEM`'s mandatory rules, so a
    violation raised there points at that omission; the same rule id raised on
    a first draft does not. The flat counter cannot tell them apart, which is
    the whole reason this second counter exists.
    """
    outs = [
        # rejected twice: once as a first draft, once again after repair
        _outcome(record_id="a", policy_violations=["E_UNKNOWN_COLUMN"],
                 repair_violations=["E_UNKNOWN_COLUMN"]),
        # rejected on the first draft only, then repaired successfully
        _outcome(record_id="b", policy_violations=["E_TABLE_NOT_ALLOWED"],
                 succeeded_on_attempt=2),
    ]
    r = aggregate(outs)
    assert r.policy_violations_by_rule == {
        "E_UNKNOWN_COLUMN": 1, "E_TABLE_NOT_ALLOWED": 1
    }
    assert r.repair_violations_by_rule == {"E_UNKNOWN_COLUMN": 1}
    assert "on a repair attempt: E_UNKNOWN_COLUMN=1" in format_report(r)


def test_repair_violations_absent_when_only_first_drafts_were_rejected() -> None:
    r = aggregate([_outcome(policy_violations=["E_UNKNOWN_COLUMN"])])
    assert r.repair_violations_by_rule == {}
    # Nothing to say means nothing printed — an empty line here would read as
    # a measurement rather than the absence of one.
    assert "on a repair attempt" not in format_report(r)


def test_report_dict_is_json_serialisable() -> None:
    import json

    r = aggregate([_outcome(outcome=OUTCOME_MATCH, execution_match=True, execution_ok=True,
                            succeeded_on_attempt=1, cost_usd=0.0001, model="gpt-x")])
    json.dumps(metrics.report_to_dict(r))  # must not raise
