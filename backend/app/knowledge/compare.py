"""Do two result sets say the same thing? — the deterministic comparator.

**This is the strongest starting position DataMind has, and it moved here so
two callers can share one implementation.** It was written for the eval harness
(`app/eval/metrics.py`), which is offline-only by contract, and the request-path
packages may not import it. Phase 4's conflict checker and Phase 6's in-product
benchmark both need exactly these functions, so the pure half came down a layer
rather than the contract coming up one: `app.eval -> app.knowledge` is a
permitted direction, and nothing on the request path gained an import of
`app.eval`. One implementation, one set of tolerances, one set of tests.

Everything here is a pure function over rows already fetched. No database, no
model, no I/O — which is what makes it usable from a worker, from the eval
runner and from a unit test with three lists in it.

**Why this matters more than it looks.** Fabric detects conflicting
instructions by *reasoning over SQL text* and reports a confidence score of one
to five. DataMind can run both statements and compare the rows. Two templates
whose questions are near-duplicates and whose results differ on the same
connection is a **fact**, not an opinion — and `first_difference` returns the
rows that prove it, because a conflict a curator cannot see the evidence for is
just another warning nobody acts on.

No LLM judge anywhere near this. Fabric fell back to one and gets *true /
false / unclear*; spending a model call per row to get a worse answer would be
a strange trade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

Row = list[Any]

# Relative tolerance soaks up float noise on large magnitudes (a million-scale
# SUM summed in a different order). The absolute tolerance matches the golden
# set's own precision: golds report figures with round(x, 2), so any value
# within half a cent of the gold is the *same* answer at the precision the gold
# states. Without this, a correct `AVG(x)` (957.416) is scored wrong against a
# gold `round(sum/count, 2)` (957.42) — a presentation gap, not an error.
NUMERIC_REL_TOLERANCE = 1e-6
NUMERIC_ABS_TOLERANCE = 5e-3

#: Row order is part of the answer — a ranking, a time series. Anything else is
#: compared as an unordered multiset. The spelling is the eval dataset's, so a
#: `GoldRecord.equivalence` can be passed straight through.
ORDERED_ROWS = "ordered_rows"

#: How many diverging rows a conflict carries as evidence. Enough to see the
#: shape of the disagreement, few enough to render in a detail pane — and few
#: enough that the rows travel as *evidence*, not as an export of the table.
MAX_EVIDENCE_ROWS = 5


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    # Decimal, date, etc. — try str->float, else not numeric.
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def values_equal(
    a: Any,
    b: Any,
    rel_tol: float = NUMERIC_REL_TOLERANCE,
    abs_tol: float = NUMERIC_ABS_TOLERANCE,
) -> bool:
    if a is None or b is None:
        return a is None and b is None
    na, nb = _as_number(a), _as_number(b)
    if na is not None and nb is not None:
        return math.isclose(na, nb, rel_tol=rel_tol, abs_tol=abs_tol)
    return str(a).strip() == str(b).strip()


def rows_equal(a: Row, b: Row) -> bool:
    """Two rows, compared by position and within tolerance."""
    return len(a) == len(b) and all(
        values_equal(x, y) for x, y in zip(a, b, strict=False)
    )


def result_sets_match(
    gold: list[Row], candidate: list[Row], equivalence: str = ""
) -> bool:
    """Do two result sets agree? — compare by position within each row.

    * `ordered_rows` — row order is part of the answer (rankings, time series).
    * everything else — unordered multiset of rows.

    Column names are ignored; the match is positional and tolerance-aware (see
    the tolerance constants). The unordered case is a greedy multiset match
    rather than a hash on rounded keys, so two rows equal *within tolerance*
    match even when they would round to different keys at a bucket boundary.
    Result sets here are small, so the O(n^2) match is not a concern. Two
    correct queries are rarely string-identical, which is why string equality
    is never the gate.
    """
    if len(gold) != len(candidate):
        return False
    if equivalence == ORDERED_ROWS:
        return all(rows_equal(g, c) for g, c in zip(gold, candidate, strict=False))
    remaining = list(candidate)
    for g in gold:
        for i, c in enumerate(remaining):
            if rows_equal(g, c):
                remaining.pop(i)
                break
        else:
            return False
    return True


@dataclass(slots=True)
class Divergence:
    """Where two result sets stop agreeing, in the shape the UI renders.

    Deliberately *not* a boolean plus a message. §4.7's conflict pane shows the
    rows themselves side by side — *"monthly revenue → 481,220 / revenue by
    month → 512,940"* — because the rows are the evidence, and the thing no
    competitor can show. A conflict reported as prose is a warning; a conflict
    reported as two rows is a fact the curator can act on in one read.
    """

    #: False when the two agree — in which case every list below is empty.
    differs: bool = False
    #: A sentence naming the *kind* of disagreement, for the log and the
    #: template's `status_reason`.
    summary: str = ""
    #: Column headers, taken from the left statement. Both are shown against
    #: these: a conflict where even the shape differs is still a conflict, and
    #: the pane says so rather than pretending the columns line up.
    left_columns: list[str] = field(default_factory=list)
    right_columns: list[str] = field(default_factory=list)
    #: Up to `MAX_EVIDENCE_ROWS` rows from each side, chosen to *show the
    #: difference* rather than to show the top of the table.
    left_rows: list[Row] = field(default_factory=list)
    right_rows: list[Row] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe, for the `conflict_evidence` column.

        Every cell is stringified: a `Decimal`, a `date` and a `UUID` all come
        back from the connectors and none of them is JSON. Stringifying here
        rather than at render time means the stored evidence is exactly what
        the curator will be shown.
        """
        return {
            "summary": self.summary,
            "left_columns": list(self.left_columns),
            "right_columns": list(self.right_columns),
            "left_rows": [[_cell(v) for v in row] for row in self.left_rows],
            "right_rows": [[_cell(v) for v in row] for row in self.right_rows],
        }


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def first_difference(
    left: list[Row],
    right: list[Row],
    *,
    equivalence: str = "",
    left_columns: list[str] | None = None,
    right_columns: list[str] | None = None,
) -> Divergence:
    """The evidence that two result sets disagree, or `differs=False`.

    Three kinds of disagreement, distinguished because the curator's next move
    differs for each:

    * **different shapes** — one returns three columns and the other four. The
      two statements are not answering the same question at all.
    * **different row counts** — usually one filters something the other does
      not. That is the canonical conflict: *"the second includes cancelled
      orders."*
    * **same shape, different values** — the interesting one, and the one where
      showing the rows is the whole point.

    The rows returned are the *diverging* ones wherever that is meaningful, not
    the first five: showing the top of two tables that agree for two hundred
    rows and differ at row two hundred and one is showing nothing.
    """
    left_columns = list(left_columns or [])
    right_columns = list(right_columns or [])

    if len(left_columns) != len(right_columns) and left_columns and right_columns:
        return Divergence(
            differs=True,
            summary=(
                f"The two statements return different columns — "
                f"{len(left_columns)} against {len(right_columns)}."
            ),
            left_columns=left_columns,
            right_columns=right_columns,
            left_rows=left[:MAX_EVIDENCE_ROWS],
            right_rows=right[:MAX_EVIDENCE_ROWS],
        )

    if result_sets_match(left, right, equivalence):
        return Divergence()

    if len(left) != len(right):
        summary = (
            f"The two statements return different numbers of rows — "
            f"{len(left):,} against {len(right):,}."
        )
    else:
        summary = "The two statements return the same rows with different values."

    return Divergence(
        differs=True,
        summary=summary,
        left_columns=left_columns,
        right_columns=right_columns,
        left_rows=_evidence(left, right),
        right_rows=_evidence(right, left),
    )


def _evidence(rows: list[Row], other: list[Row]) -> list[Row]:
    """Rows from `rows` that `other` has no match for, then whatever is left.

    A disagreement buried at row two hundred is still a disagreement, and a
    pane showing the first five rows of two tables that agree for the first two
    hundred is a pane showing nothing. Falling back to the head is deliberate:
    when the multiset matches but the *order* does not, no row is unmatched,
    and the head is where an order difference is visible.
    """
    remaining = list(other)
    unmatched: list[Row] = []
    for row in rows:
        for i, candidate in enumerate(remaining):
            if rows_equal(row, candidate):
                remaining.pop(i)
                break
        else:
            unmatched.append(row)
            if len(unmatched) >= MAX_EVIDENCE_ROWS:
                return unmatched
    return unmatched or rows[:MAX_EVIDENCE_ROWS]
