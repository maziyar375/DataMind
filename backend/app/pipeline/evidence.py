"""A deep step's rows, turned into figures and into what a writer may see.

Pure, like `disclosure.py` beside it: an `ExecutionResult` and a policy go in,
dictionaries and `BlockNarration`s come out. The deep nodes call it; nothing in
here calls a model, a database or a service.

Two jobs, kept apart because they answer to different rules:

**`compute` — the arithmetic, for the reader.** Dispatches a step's `tool` into
`app.analysis` over the rows the query returned. The planner *selected* the
tool; this never lets it write one (plan D4), and a tool it does not know is a
skipped computation, never a crash. What comes back is shown to the reader
whatever the policy — it is their own data, on their own screen.

**`narration` — what reaches a prompt.** The same rule `workers/report.py`
follows, and for the same reason: the writer gets `disclose()`d rows, and the
computed figures **only when it was handed every row they were computed
from**. A total over the first fifty rows is a wrong total, and a driver
computed from rows the policy withheld would carry a value out of them. So
under `NONE`, `AGGREGATE`, a `SAMPLE` over fifty rows, or a capped result, the
writer is told the figures exist and not what they are.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Any

from app.analysis import (
    Column,
    Contribution,
    Distribution,
    PeriodPair,
    Refusal,
    compare,
    contribution,
    distribution,
)
from app.domain.value_objects import DisclosurePolicy
from app.pipeline.disclosure import disclose
from app.pipeline.state import (
    DisclosedResult,
    ExecutionResult,
    PlanStep,
    StepEvidence,
)
from app.reports import checks, facts
from app.reports.facts import Fact, FactSheet
from app.reports.narrate import BlockNarration

Rows = Sequence[Sequence[Any]]

#: What each tool asks of the rows, appended to the sub-question the generator
#: is given. The planner was told the same shapes; saying it again where the
#: SQL is written is what makes the rows arrive in a shape `compute` can read.
#: SQL is absent on purpose: a plain step is asked exactly its own question.
SHAPE_HINTS: dict[str, str] = {
    "COMPARE_PERIODS": (
        "Return a dated series: one row per day or month, with the date and "
        "the measure, covering both periods being compared, ordered by date."
    ),
    "CONTRIBUTION": (
        "Return one row per period and segment — the period, the segment and "
        "the measure — for exactly the two periods being compared. If the "
        "measure is an average or a rate, also return the count it was taken over."
    ),
    "OUTLIERS": (
        "Return one row per value of the dimension, with the measure."
    ),
}

#: A column name that reads as a period, for a CONTRIBUTION result whose
#: period came back as text ("2026-03", "Q1") rather than a date.
_PERIODISH = re.compile(
    r"(?:^|_)(?:period|month|year|quarter|week|day|date|yr|mon|qtr)(?:_|$)",
    re.IGNORECASE,
)

#: Figures kept per computation. A driver list is ranked, so the tail is the
#: least explanatory part of it and the first thing a sentence would omit.
_MAX_DRIVERS = 5


def with_shape(step: PlanStep) -> str:
    """The sub-question as the generator reads it."""
    hint = SHAPE_HINTS.get(step.tool)
    return f"{step.question}\n{hint}" if hint else step.question


# ── compute ──────────────────────────────────────────────────────────────
def compute(step: PlanStep, execution: ExecutionResult) -> dict[str, Any] | None:
    """`app.analysis` over one step's rows. None for a plain SQL step.

    Returns `{"tool", "ok", "summary", "facts": [{"text", "values"}],
    "refusal"}`. A refusal is a result — `ok` false, `summary` the sentence
    the analysis package wrote for a reader — because "nothing moved" is the
    commonest true answer to a *why* question.
    """
    if step.tool == "SQL":
        return None
    run = _DISPATCH.get(step.tool)
    if run is None:
        return _refused(step.tool, "UNKNOWN_TOOL",
                        f"No computation is called {step.tool!r}; the rows stand on their own.")
    if execution.truncated:
        # A driver over a capped result is a driver over a prefix of it.
        return _refused(step.tool, "TRUNCATED",
                        "The result was capped by the row limit, so no figure "
                        "computed from it would be a figure about the whole.")
    columns = [Column(name=c.name, semantic_type=c.semantic_type) for c in execution.columns]
    try:
        return run(step.tool, columns, execution.rows)
    except Exception as err:  # noqa: BLE001 — the package promises values, not raises
        return _refused(step.tool, "ERROR", f"The computation could not run: {err}"[:300])


def _refused(tool: str, code: str, reason: str) -> dict[str, Any]:
    return {"tool": tool, "ok": False, "summary": reason, "facts": [], "refusal": code}


def _computed(tool: str, lines: list[tuple[str, tuple[float, ...]]]) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": True,
        "summary": " ".join(text for text, _ in lines),
        "facts": [{"text": text, "values": list(values)} for text, values in lines],
        "refusal": None,
    }


def _from_refusal(tool: str, refusal: Refusal) -> dict[str, Any]:
    return _refused(tool, str(refusal.code), refusal.reason)


def _compare_periods(tool: str, columns: list[Column], rows: Rows) -> dict[str, Any]:
    pair = compare(columns=columns, rows=rows)
    if isinstance(pair, Refusal):
        return _from_refusal(tool, pair)
    return _computed(tool, _periods_lines(pair))


def _contribution(tool: str, columns: list[Column], rows: Rows) -> dict[str, Any]:
    period = next((c for c in columns if c.is_temporal), None) or next(
        (c for c in columns if _PERIODISH.search(c.name)), None
    )
    if period is None:
        return _refused(tool, "NO_PERIOD",
                        "The result has no period column, so there are no two "
                        "periods to attribute a change between.")
    at = [c.name for c in columns].index(period.name)
    labels = sorted({str(row[at]) for row in rows if row[at] is not None})
    if len(labels) < 2:
        return _refused(tool, "NOT_ENOUGH_ROWS",
                        "The result covers one period, so there is no change to attribute.")
    first, last = labels[0], labels[-1]
    rest = [c for c in columns if c.name != period.name]

    def side(label: str) -> list[list[Any]]:
        return [
            [v for i, v in enumerate(row) if i != at]
            for row in rows if str(row[at]) == label
        ]

    result = contribution(columns=rest, before_rows=side(first), after_rows=side(last))
    if isinstance(result, Refusal):
        return _from_refusal(tool, result)
    lines = _contribution_lines(result, first, last)
    if len(labels) > 2:
        lines.insert(0, (f"The result has {len(labels)} periods; the first ({first}) "
                         f"and last ({last}) were compared.", (float(len(labels)),)))
    return _computed(tool, lines)


def _outliers(tool: str, columns: list[Column], rows: Rows) -> dict[str, Any]:
    label = next((c for c in columns if c.is_categorical), None)
    measure = next((c for c in columns if c.is_numeric), None)
    if label is None or measure is None:
        return _refused(tool, "NO_MEASURE",
                        "Outliers need one column of labels and one of numbers.")
    names = [c.name for c in columns]
    at_label, at_measure = names.index(label.name), names.index(measure.name)
    pairs = [(str(row[at_label]), _number(row[at_measure])) for row in rows]
    kept = [(k, v) for k, v in pairs if v is not None]
    result = distribution([k for k, _ in kept], [v for _, v in kept])
    if isinstance(result, Refusal):
        return _from_refusal(tool, result)
    return _computed(tool, _outlier_lines(result, measure.name))


_DISPATCH: dict[str, Callable[[str, list[Column], Rows], dict[str, Any]]] = {
    "COMPARE_PERIODS": _compare_periods,
    "CONTRIBUTION": _contribution,
    "OUTLIERS": _outliers,
}


# ── the sentences ────────────────────────────────────────────────────────
def _fmt(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _periods_lines(pair: PeriodPair) -> list[tuple[str, tuple[float, ...]]]:
    lines: list[tuple[str, tuple[float, ...]]] = [(
        f"From {pair.before.first} – {pair.before.last} to {pair.after.first} – "
        f"{pair.after.last} the total went from {_fmt(pair.before.total)} to "
        f"{_fmt(pair.after.total)} ({_pct(pair.change_pct)}).",
        tuple(
            v for v in (pair.before.total, pair.after.total, pair.change_pct)
            if v is not None
        ),
    )]
    if pair.per_day_change_pct is not None:
        lines.append((
            f"Per calendar day the change was {_pct(pair.per_day_change_pct)}.",
            (pair.per_day_change_pct,),
        ))
    if pair.note:
        lines.append((pair.note, ()))
    return lines


def _contribution_lines(
    result: Contribution, first: str, last: str
) -> list[tuple[str, tuple[float, ...]]]:
    lines: list[tuple[str, tuple[float, ...]]] = [(
        f"{result.measure} went from {_fmt(result.total_before)} ({first}) to "
        f"{_fmt(result.total_after)} ({last}), a change of {_fmt(result.total_change)} "
        f"({_pct(result.total_change_pct)}), attributed by {result.dimension} "
        f"using the {result.method} method.",
        tuple(v for v in (result.total_before, result.total_after, result.total_change,
                          result.total_change_pct) if v is not None),
    )]
    for driver in result.top(_MAX_DRIVERS):
        parts = [f"{result.dimension} = {driver.value}: {_fmt(driver.before)} → "
                 f"{_fmt(driver.after)} ({driver.change:+,.2f})"]
        values = [driver.before, driver.after, driver.change]
        if driver.contribution is not None:
            share = driver.contribution * 100
            parts.append(f"{share:.1f}% of the total change")
            values.append(share)
        if driver.hypothetical_change_pct is not None:
            parts.append(
                f"without it the overall change would have been "
                f"{_pct(driver.hypothetical_change_pct)}"
            )
            values.append(driver.hypothetical_change_pct)
        if driver.appeared:
            parts.append("new in the later period")
        if driver.disappeared:
            parts.append("absent from the later period")
        lines.append(("; ".join(parts) + ".", tuple(v for v in values if math.isfinite(v))))
    lines.extend((note, ()) for note in result.notes)
    return lines


def _outlier_lines(result: Distribution, measure: str) -> list[tuple[str, tuple[float, ...]]]:
    if not result.discriminating:
        return [(
            f"With only {result.count} values no {measure} can stand out at a "
            f"z-score of {result.threshold:.1f}, so an empty list here says "
            "nothing about the data.",
            (float(result.count),),
        )]
    if not result.flagged:
        return [(
            f"No {measure} stands out: every one of the {result.count} values is "
            f"within {result.threshold:.1f} standard deviations of the mean "
            f"({_fmt(result.mean)}).",
            (float(result.count), result.mean),
        )]
    lines: list[tuple[str, tuple[float, ...]]] = [(
        f"{len(result.flagged)} of {result.count} values stand out "
        f"(mean {_fmt(result.mean)}, threshold {result.threshold:.1f} standard deviations).",
        (float(len(result.flagged)), float(result.count), result.mean),
    )]
    for o in result.flagged[:_MAX_DRIVERS]:
        lines.append((
            f"{o.label}: {_fmt(o.value)} ({o.direction.lower()}, z = {o.z_score:+.1f}).",
            (o.value, o.z_score),
        ))
    return lines


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ── what the writer may see ──────────────────────────────────────────────
def disclosed_in_full(disclosed: DisclosedResult, execution: ExecutionResult) -> bool:
    """Whether the model was handed every row the computation read."""
    return (
        disclosed.policy in (DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL)
        and not execution.truncated
        and len(disclosed.rows) == execution.row_count
    )


def close_step(evidence: StepEvidence, policy: str) -> StepEvidence:
    """Disclose the step's result and compute over it — once, when it closes.

    The policy is the run's, fixed when the run started, so disclosing here is
    disclosing at render time: nothing a later prompt reads was disclosed
    under any other policy. `computed["complete"]` records whether the writer
    may be shown the figures (see the module docstring).
    """
    execution = evidence.execution
    if execution is None:
        return evidence
    disclosed = disclose(execution, policy)
    computed = compute(evidence.step, execution)
    if computed is not None:
        computed["complete"] = disclosed_in_full(disclosed, execution)
    return evidence.model_copy(update={
        "disclosed": disclosed,
        "computed": computed,
        "row_count": execution.row_count,
        "truncated": execution.truncated,
        "column_types": {c.name: c.semantic_type for c in execution.columns},
    })


def narration(evidence: StepEvidence) -> BlockNarration:
    """One step, as a prompt may read it: `disclosed`, `computed` and the
    result's shape — never `execution`, which this function does not touch."""
    question = evidence.step.question
    if evidence.status != "DONE" or evidence.disclosed is None:
        return BlockNarration(
            question=question, error=evidence.note or "This step produced no result."
        )
    disclosed = evidence.disclosed
    complete = (
        disclosed.policy in (DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL)
        and not evidence.truncated
        and len(disclosed.rows) == evidence.row_count
    )
    sheet: list[Fact] = []
    note = disclosed.note
    if evidence.computed is not None:
        if evidence.computed.get("complete"):
            sheet.extend(
                Fact(text=f["text"], values=tuple(f["values"]))
                for f in evidence.computed.get("facts", [])
            )
            if not evidence.computed.get("ok"):
                note = f"{note} {evidence.computed['summary']}".strip()
        else:
            note = (f"{note} Figures were computed from this result but are "
                    "not shared with you, because you were not given every row "
                    "they were computed from.").strip()
    table = facts.compute(
        columns=[
            facts.FactColumn(name=c, semantic_type=evidence.column_types.get(c, "nominal"))
            for c in disclosed.columns
        ],
        rows=disclosed.rows,
        complete=complete,
    )
    return BlockNarration(
        question=question,
        columns=list(disclosed.columns),
        rows=[list(r) for r in disclosed.rows],
        note=note,
        row_count=evidence.row_count,
        facts=FactSheet(facts=tuple([*sheet, *table.facts][: facts.MAX_FACTS])),
    )


def pool(block: BlockNarration) -> list[float]:
    """Every figure one step's block supports: what the writer was *given*."""
    values = checks.numeric_values(block.rows)
    values.append(float(block.row_count))
    values.extend(block.facts.values())
    return values
