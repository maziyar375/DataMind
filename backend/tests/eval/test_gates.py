"""The CI gate logic: what makes the nightly build pass or fail.

Pure functions over a report dict + parsed args — no model, no fixture. These
encode the phase's exit criteria, so they are worth pinning down exactly.
"""
from __future__ import annotations

import argparse
import json

from app.eval import runner


def _args(**over: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "fail_under": None,
        "require_zero_policy_violations": False,
        "baseline_file": None,
        "max_regression": 0.02,
    }
    base.update(over)
    return argparse.Namespace(**base)


def _report(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "execution_accuracy": 0.70,
        "policy_violation_rate": 0.0,
        "policy_violations_by_rule": {},
    }
    base.update(over)
    return base


def test_clean_report_passes_all_gates() -> None:
    assert runner._apply_gates(_report(), _args(fail_under=0.65)) == 0


def test_policy_violation_fails_hard() -> None:
    report = _report(policy_violation_rate=0.02, policy_violations_by_rule={"E_X": 1})
    assert runner._apply_gates(report, _args(require_zero_policy_violations=True)) == 1
    # ...but only when the gate is armed.
    assert runner._apply_gates(report, _args(require_zero_policy_violations=False)) == 0


def test_below_floor_fails() -> None:
    assert runner._apply_gates(_report(execution_accuracy=0.60), _args(fail_under=0.65)) == 1
    assert runner._apply_gates(_report(execution_accuracy=0.65), _args(fail_under=0.65)) == 0


def test_regression_beyond_threshold_fails(tmp_path) -> None:
    baseline = tmp_path / "base.json"
    baseline.write_text(json.dumps({"execution_accuracy": 0.72}))
    a = _args(baseline_file=str(baseline), max_regression=0.02)
    # 0.72 -> 0.69 is a 3-point drop: fails. 0.72 -> 0.70 is 2 points: allowed.
    assert runner._apply_gates(_report(execution_accuracy=0.69), a) == 1
    assert runner._apply_gates(_report(execution_accuracy=0.70), a) == 0
    # An improvement never fails.
    assert runner._apply_gates(_report(execution_accuracy=0.80), a) == 0


def test_missing_baseline_file_is_a_failure_not_a_silent_pass(tmp_path) -> None:
    a = _args(baseline_file=str(tmp_path / "nope.json"))
    assert runner._apply_gates(_report(), a) == 1


def test_negative_suite_containment_breach_fails() -> None:
    # A greeting/write/metadata question that executed SQL is a breach → fail.
    report = {"sql_leak_count": 1, "containment_leak_count": 1, "route_accuracy": 0.9}
    assert runner._apply_negative_gates(report) == 1


def test_negative_suite_unanswerable_readonly_leak_does_not_fail() -> None:
    # A leak from an unanswerable (analytical-form) question is read-only and
    # cannot be caught before retrieval — reported, not build-failing.
    report = {"sql_leak_count": 2, "containment_leak_count": 0, "route_accuracy": 0.8}
    assert runner._apply_negative_gates(report) == 0


def test_negative_suite_clean_passes() -> None:
    report = {"sql_leak_count": 0, "containment_leak_count": 0, "route_accuracy": 1.0}
    assert runner._apply_negative_gates(report) == 0
