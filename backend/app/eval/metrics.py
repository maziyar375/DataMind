"""Metrics, in the order of importance the phase fixed.

Everything here is a pure function over already-collected results, so it is unit
tested without a database or a model:

1. execution accuracy   — gold vs candidate result sets (the headline)
2. retrieval recall @ k  — did retrieval surface every expected table
3. parse / policy / execution rates
4. repair distribution   — succeeded at attempt 1 vs 2 vs 3
5. latency p50/p95 (llm/validate/db), tokens, cost per question

`exact_match` is computed as a diagnostic only and is never a gate.

**The result-set comparator lives in `app/knowledge/compare.py`, not here.**
It was written here and moved down a layer in Phase 4, because the conflict
checker and the in-product benchmark need exactly these tolerances and this
package is offline-only by contract — `app.eval -> app.knowledge` is a
permitted direction, and nothing on the request path gained an import of
`app.eval`. It is re-exported below so every existing caller and test keeps
working against one implementation.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Re-exported, not re-implemented. Every existing caller — `runner.py`, and
# `tests/eval/test_metrics.py`, which is where the tolerances are pinned —
# keeps importing them from here.
from app.knowledge.compare import (  # noqa: F401
    NUMERIC_ABS_TOLERANCE,
    NUMERIC_REL_TOLERANCE,
    Row,
    result_sets_match,
    rows_equal,
    values_equal,
)

# Outcome labels, most-desirable first. `MATCH` is the only success.
OUTCOME_MATCH = "MATCH"
OUTCOME_MISMATCH = "MISMATCH"          # ran, but result set differs from gold
OUTCOME_EXEC_FAILED = "EXEC_FAILED"    # valid SQL the database still rejected
OUTCOME_VALIDATION_FAILED = "VALIDATION_FAILED"  # guard rejected every attempt
OUTCOME_NO_SQL = "NO_SQL"              # routed away from SQL (metadata/chitchat/…)
OUTCOME_ERROR = "ERROR"                # pipeline/gold crash


_WS = re.compile(r"\s+")


def exact_match(gold_sql: str, candidate_sql: str | None) -> bool:
    """String equality after whitespace/case/semicolon normalisation. Diagnostic only."""
    if candidate_sql is None:
        return False

    def norm(s: str) -> str:
        return _WS.sub(" ", s.strip().rstrip(";")).lower()

    return norm(gold_sql) == norm(candidate_sql)


# ── retrieval recall @ k ────────────────────────────────────────────────────


def _bare(name: str) -> str:
    return name.split(".")[-1].strip().lower()


def retrieval_recall(expected_tables: list[str], retrieved_tables: list[str]) -> float:
    """Fraction of expected tables present in the retrieved set (bare names)."""
    if not expected_tables:
        return 1.0
    have = {_bare(t) for t in retrieved_tables}
    hits = sum(1 for t in expected_tables if _bare(t) in have)
    return hits / len(expected_tables)


# ── per-record outcome ──────────────────────────────────────────────────────


@dataclass
class RecordOutcome:
    """Everything measured for one question — persisted verbatim per record."""

    record_id: str
    tags: list[str]
    difficulty: str
    model: str = ""

    outcome: str = OUTCOME_ERROR
    intent: str | None = None

    expected_tables: list[str] = field(default_factory=list)
    retrieved_tables: list[str] = field(default_factory=list)
    retrieval_recall: float = 0.0
    retrieval_hit: bool = False

    gold_sql: str = ""
    candidate_sql: str | None = None
    gold_row_count: int | None = None
    candidate_row_count: int | None = None

    parse_ok: bool = False
    validated_ok: bool = False
    execution_ok: bool = False
    execution_match: bool = False
    exact_match: bool = False
    policy_violations: list[str] = field(default_factory=list)
    # The same rule ids, restricted to *repair* attempts. The flat list above
    # cannot say whether a violation came from the first draft or from a
    # regeneration, and the two have different causes: `REPAIR_SYSTEM` and
    # `REVIEW_SYSTEM` replace `GENERATE_SYSTEM` wholesale rather than extending
    # it, so a repair attempt is never shown the mandatory rules ("SELECT
    # only", "never guess a name", "do not add a LIMIT"). Whether that omission
    # costs runs is exactly what this field answers: violations clustering here
    # say yes, an empty list says the repair prompts are fine as they are.
    repair_violations: list[str] = field(default_factory=list)

    attempts: int = 0
    repair_count: int = 0
    succeeded_on_attempt: int | None = None

    # ── the templates arm (Phase 5) ─────────────────────────────────────
    #: How many taught questions reached the generate prompt as examples. Zero
    #: on the templates-off arm and on every question the matcher found nothing
    #: for, which is what makes the split reportable: the questions that were
    #: shown examples and the questions that were not are different populations
    #: and only one of them can move for a reason.
    examples_offered: int = 0
    #: True when the run was *answered* from a stored template rather than
    #: generated. Recorded because it must be near zero for the arm's accuracy
    #: number to be about the prompt at all — an answer that came from the
    #: store is not a measurement of few-shot injection.
    short_circuited: bool = False
    #: Which matcher produced the candidates this run saw — `LEXICAL`,
    #: `EMBEDDING`, or empty when the store was not consulted. Phase 7's
    #: `FallbackMatcher` means an embedding arm still answers lexically
    #: whenever the embedding half found nothing, so the arm's *label* and what
    #: actually retrieved are different facts and the report prints the second.
    matcher: str = ""

    llm_ms: int = 0
    validate_ms: int = 0
    db_ms: int = 0
    total_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None

    failure_reason: str | None = None

    @property
    def is_success(self) -> bool:
        return self.outcome == OUTCOME_MATCH


# ── aggregation ─────────────────────────────────────────────────────────────


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (p / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _rate(hits: int, total: int) -> float:
    return (hits / total) if total else 0.0


@dataclass
class TagBreakdown:
    tag: str
    n: int
    execution_accuracy: float
    retrieval_recall: float


@dataclass
class SuiteReport:
    n: int
    # 1. headline
    execution_accuracy: float
    # 2. diagnostic that decides what to fix
    retrieval_recall_mean: float
    retrieval_full_hit_rate: float
    # 3. rates
    parse_rate: float
    validation_pass_rate: float
    execution_success_rate: float
    policy_violation_rate: float
    policy_violations_by_rule: dict[str, int]
    # The subset of the above raised on a repair attempt rather than the first
    # draft — a repair prompt carries the feedback and the schema but none of
    # the mandatory rules, so this is where that would show up.
    repair_violations_by_rule: dict[str, int]
    # 4. repair distribution
    repair_distribution: dict[str, int]
    # 5. latency / tokens / cost
    latency_ms: dict[str, dict[str, float]]     # {"llm": {"p50":..,"p95":..}, ...}
    tokens_per_question: dict[str, float]
    cost_per_question: float | None
    cost_by_model: dict[str, float]
    # diagnostic, never a gate
    exact_match_rate: float
    # 6. the templates arm (Phase 5). All zero on every other arm, so a
    #    scorecard from before this existed reads the same way.
    examples_offered_rate: float
    examples_per_question: float
    short_circuit_rate: float
    # 7. the embedding matcher (Phase 7). `embedding_share` is the fraction of
    #    questions the embedding half actually retrieved for — not the fraction
    #    the arm was launched with. On a lexical arm it is 0.0 and the whole
    #    line is suppressed.
    embedding_share: float
    # breakdowns
    per_tag: list[TagBreakdown]
    outcome_counts: dict[str, int]


def aggregate(outcomes: list[RecordOutcome]) -> SuiteReport:
    n = len(outcomes)
    matched = [o for o in outcomes if o.is_success]

    # policy violations by rule, and the repair-attempt subset of them
    rule_counter: Counter[str] = Counter()
    repair_rule_counter: Counter[str] = Counter()
    for o in outcomes:
        rule_counter.update(o.policy_violations)
        repair_rule_counter.update(o.repair_violations)

    # repair distribution: attempt number a run first succeeded on
    repair: Counter[str] = Counter()
    for o in outcomes:
        if o.succeeded_on_attempt is None:
            repair["failed"] += 1
        else:
            repair[f"attempt_{o.succeeded_on_attempt}"] += 1

    def _pcts(field_name: str) -> dict[str, float]:
        vals = [float(getattr(o, field_name)) for o in outcomes]
        return {"p50": round(percentile(vals, 50), 1), "p95": round(percentile(vals, 95), 1)}

    # cost
    costed = [o.cost_usd for o in outcomes if o.cost_usd is not None]
    cost_by_model: dict[str, float] = {}
    model_sums: dict[str, float] = {}
    model_counts: dict[str, int] = {}
    for o in outcomes:
        if o.cost_usd is not None and o.model:
            model_sums[o.model] = model_sums.get(o.model, 0.0) + o.cost_usd
            model_counts[o.model] = model_counts.get(o.model, 0) + 1
    for m, total in model_sums.items():
        cost_by_model[m] = round(total / model_counts[m], 6)

    # per-tag breakdown
    tags = sorted({t for o in outcomes for t in o.tags})
    per_tag: list[TagBreakdown] = []
    for tag in tags:
        group = [o for o in outcomes if tag in o.tags]
        per_tag.append(
            TagBreakdown(
                tag=tag,
                n=len(group),
                execution_accuracy=round(
                    _rate(sum(o.is_success for o in group), len(group)), 4
                ),
                retrieval_recall=round(
                    sum(o.retrieval_recall for o in group) / len(group) if group else 0.0, 4
                ),
            )
        )

    return SuiteReport(
        n=n,
        execution_accuracy=round(_rate(len(matched), n), 4),
        retrieval_recall_mean=round(
            sum(o.retrieval_recall for o in outcomes) / n if n else 0.0, 4
        ),
        retrieval_full_hit_rate=round(_rate(sum(o.retrieval_hit for o in outcomes), n), 4),
        parse_rate=round(_rate(sum(o.parse_ok for o in outcomes), n), 4),
        validation_pass_rate=round(_rate(sum(o.validated_ok for o in outcomes), n), 4),
        execution_success_rate=round(_rate(sum(o.execution_ok for o in outcomes), n), 4),
        policy_violation_rate=round(
            _rate(sum(1 for o in outcomes if o.policy_violations), n), 4
        ),
        policy_violations_by_rule=dict(rule_counter.most_common()),
        repair_violations_by_rule=dict(repair_rule_counter.most_common()),
        repair_distribution=dict(repair),
        latency_ms={
            "llm": _pcts("llm_ms"),
            "validate": _pcts("validate_ms"),
            "db": _pcts("db_ms"),
            "total": _pcts("total_ms"),
        },
        tokens_per_question={
            "prompt": round(sum(o.prompt_tokens for o in outcomes) / n if n else 0.0, 1),
            "completion": round(
                sum(o.completion_tokens for o in outcomes) / n if n else 0.0, 1
            ),
        },
        cost_per_question=round(sum(costed) / len(costed), 6) if costed else None,
        cost_by_model=cost_by_model,
        exact_match_rate=round(_rate(sum(o.exact_match for o in outcomes), n), 4),
        examples_offered_rate=round(
            _rate(sum(1 for o in outcomes if o.examples_offered), n), 4
        ),
        examples_per_question=round(
            sum(o.examples_offered for o in outcomes) / n if n else 0.0, 2
        ),
        short_circuit_rate=round(
            _rate(sum(1 for o in outcomes if o.short_circuited), n), 4
        ),
        embedding_share=round(
            _rate(sum(1 for o in outcomes if o.matcher == "EMBEDDING"), n), 4
        ),
        per_tag=per_tag,
        outcome_counts=dict(Counter(o.outcome for o in outcomes).most_common()),
    )


def report_to_dict(report: SuiteReport) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(report)


# ── human-readable rendering ────────────────────────────────────────────────


def format_report(report: SuiteReport, *, title: str = "") -> str:
    def pct(x: float) -> str:
        return f"{x * 100:5.1f}%"

    lines: list[str] = []
    if title:
        lines += [title, "=" * len(title)]
    lines.append(f"questions: {report.n}")
    lines.append("")
    lines.append(f"1. EXECUTION ACCURACY   {pct(report.execution_accuracy)}   <- headline")
    lines.append(
        f"2. retrieval recall@k   mean {pct(report.retrieval_recall_mean)}   "
        f"full-hit {pct(report.retrieval_full_hit_rate)}"
    )
    lines.append(
        f"3. parse {pct(report.parse_rate)}   guard-pass {pct(report.validation_pass_rate)}   "
        f"exec-success {pct(report.execution_success_rate)}   "
        f"policy-violation {pct(report.policy_violation_rate)}"
    )
    if report.policy_violations_by_rule:
        rules = "  ".join(f"{k}={v}" for k, v in report.policy_violations_by_rule.items())
        lines.append(f"     violations by rule: {rules}")
    if report.repair_violations_by_rule:
        repair_rules = "  ".join(
            f"{k}={v}" for k, v in report.repair_violations_by_rule.items()
        )
        lines.append(f"     of those, on a repair attempt: {repair_rules}")
    dist = "  ".join(f"{k}={v}" for k, v in sorted(report.repair_distribution.items()))
    lines.append(f"4. repair distribution: {dist}")
    lat = report.latency_ms
    lines.append(
        f"5. latency p50/p95 ms  llm {lat['llm']['p50']:.0f}/{lat['llm']['p95']:.0f}  "
        f"validate {lat['validate']['p50']:.0f}/{lat['validate']['p95']:.0f}  "
        f"db {lat['db']['p50']:.0f}/{lat['db']['p95']:.0f}  "
        f"total {lat['total']['p50']:.0f}/{lat['total']['p95']:.0f}"
    )
    tok = report.tokens_per_question
    cost = "n/a" if report.cost_per_question is None else f"${report.cost_per_question:.5f}"
    lines.append(
        f"   tokens/q prompt {tok['prompt']:.0f} completion {tok['completion']:.0f}"
        f"   cost/q {cost}"
    )
    if report.cost_by_model:
        by = "  ".join(f"{m}=${c:.5f}" for m, c in report.cost_by_model.items())
        lines.append(f"   cost/q by model: {by}")
    lines.append(f"   exact_match (diagnostic, not a gate): {pct(report.exact_match_rate)}")
    if report.examples_offered_rate or report.short_circuit_rate:
        # Only on the templates arm. Printed beside the headline because an
        # accuracy number from this arm is uninterpretable without knowing how
        # many questions were actually shown an example — and whether any were
        # *answered* from the store rather than generated.
        lines.append(
            f"6. templates: {pct(report.examples_offered_rate)} of questions were "
            f"shown an example ({report.examples_per_question:.2f} per question); "
            f"short-circuited {pct(report.short_circuit_rate)}"
        )
        # Phase 7's two numbers, printed on one line and in this order on
        # purpose. §3.8: FK-neighbour expansion once lifted retrieval recall
        # 70% -> 86% with **flat** execution accuracy, and the lesson is that a
        # retrieval improvement is not an answer improvement until the second
        # number moves too. Printing them apart is how somebody quotes the
        # first one alone.
        lines.append(
            f"7. matcher: {pct(report.embedding_share)} of questions were "
            f"retrieved by embedding "
            f"(the rest fell back to lexical) — against execution accuracy "
            f"{pct(report.execution_accuracy)}. Retrieval moving is not "
            f"accuracy moving; compare BOTH against the lexical arm."
        )
    lines.append("")
    lines.append("per-tag breakdown (exec-accuracy | retrieval-recall | n):")
    for tb in sorted(report.per_tag, key=lambda t: t.execution_accuracy):
        lines.append(
            f"  {tb.tag:<16} {pct(tb.execution_accuracy)} | {pct(tb.retrieval_recall)} | {tb.n}"
        )
    lines.append("")
    lines.append("outcomes: " + "  ".join(f"{k}={v}" for k, v in report.outcome_counts.items()))
    return "\n".join(lines)
