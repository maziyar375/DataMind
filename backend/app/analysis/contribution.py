"""What actually moved — three algorithms, chosen by what the measure is.

The whole of F4's value, and the single most directly usable finding in
[research §3.4](../../../docs/research/deep-analysis-mode.md): root-cause
attribution **is not one algorithm**. A model picks which dimensions to test;
this computes what happened, exactly, from rows that have already been
executed. `measures.py` says why the class decides the method and what each one
costs if it is wrong.

* **`SIMPLE`** (`SUM`, `COUNT`) — each segment's change against the total's,
  at thresholds derived from the spread of all the changes. A driver carries
  `contribution`: a real share of a real change.
* **`RATIO`** (`AVG`, `SUM/SUM`) — a *hypothetical*: what the overall change
  would have been had this segment not moved. A driver carries
  `hypothetical_change_pct`, and a **smaller** one explains more.
* **`COMPLEX`** (`COUNT(DISTINCT)`) — z-scores over the change distribution,
  and nothing else. A driver carries `z_score` alone; its `contribution` is
  `None`, because it has none.

## The three things this module will not do

**It will not compute a share for a ratio.** Segment averages do not add to the
overall average. A "contribution" over them is a number with no referent, and
it would be indistinguishable in the prose from one that has a referent. The
hypothetical is computable instead, and it needs the weight the average was
taken over — the count of orders behind an average order value. Without that
weight the module **refuses**, rather than assuming every segment is the same
size. That assumption is the confident nonsense
`docs/plans/deep-analysis-mode.md` §3.3 names as this phase's risk.

**It will not decompose a distinct count at all.** Distinct customers per
region do not sum to distinct customers overall, because a customer can be in
two regions and no arithmetic over the per-segment figures recovers the
overlap. Only the shape of the change distribution is readable, which is why
that class gets `outliers.py` and nothing else.

**It will not name a driver for a change that did not happen.** `NO_CHANGE` is
the commonest true answer to a *why* question and the one a language model will
never give: asked why revenue dropped, it will find a reason. The threshold is
relative, so it holds for revenue in rials as well as for a conversion rate.

Pure. See `measures.py` for the package's charter.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.analysis import outliers
from app.analysis.measures import (
    Column,
    MeasureClass,
    Refusal,
    RefusalCode,
    as_number,
    classify,
    unattributable,
    weight_for,
)

#: SpotIQ examines the top ten absolute changes. Past that a reader is being
#: handed a table rather than an explanation.
TOP_CHANGES = 10

#: One segment explaining more than this much of the whole change is the stop
#: condition SpotIQ names: past it, the answer is that segment and the rest is
#: detail.
DOMINANT_SHARE = 0.5

#: Below this, relative to the size of the numbers involved, the total did not
#: move. Relative rather than absolute, because 0.01 is noise in revenue and a
#: doubling in a conversion rate.
FLAT_RELATIVE_CHANGE = 1e-9

#: Rows read, per side. `reports/facts.py`'s ceiling, for its reason.
MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class Driver:
    """One dimension value, and what it did.

    Which of the three explanatory fields is populated depends on the measure
    class, and the `None`s are meaningful: a `contribution` of `None` on a
    ratio is not a missing number, it is the statement that no such number
    exists.
    """

    value: str
    before: float
    after: float
    change: float
    #: `SIMPLE` only: this segment's share of the total change, signed. `1.0`
    #: is "this segment is the whole of it"; a negative share means the segment
    #: moved *against* the total.
    contribution: float | None = None
    #: `RATIO` only: what the overall change would have been, as a percentage,
    #: had this segment not moved. **Smaller magnitude means a stronger
    #: explanation** — if removing this segment's movement flattens the
    #: overall change, this segment was the overall change.
    hypothetical_change_pct: float | None = None
    #: `COMPLEX`, and `SIMPLE`'s threshold check: how unusual this segment's
    #: change is against the others'.
    z_score: float | None = None
    #: Outside the thresholds derived from the spread of all the changes.
    flagged: bool = False
    #: The segment appears on one side only — a new value, or one that stopped
    #: appearing. Its "change" is its whole size, which reads differently.
    appeared: bool = False
    disappeared: bool = False


@dataclass(frozen=True, slots=True)
class Contribution:
    """What moved, ranked, with the method that ranked it named.

    `notes` is not decoration. Everything in it is a sentence a reader needs
    before reading the drivers — that one segment is the whole story, that the
    dimension is too narrow for the threshold to mean anything, that a segment
    is new rather than grown. A ranking without them is a ranking that reads as
    more certain than it is.
    """

    measure: str
    dimension: str
    measure_class: MeasureClass
    method: str
    total_before: float
    total_after: float
    total_change: float
    total_change_pct: float | None
    drivers: tuple[Driver, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return True

    def top(self, n: int = 3) -> tuple[Driver, ...]:
        """The strongest explanations, already ranked by the method used."""
        return self.drivers[:n]


def contribution(
    *,
    columns: Sequence[Column],
    before_rows: Sequence[Sequence[Any]],
    after_rows: Sequence[Sequence[Any]],
    measure: Column | None = None,
    dimension: Column | None = None,
    weight: Column | None = None,
) -> Contribution | Refusal:
    """What drove the change in `measure`, broken down by `dimension`.

    Both row sets come from **one query run over two periods**, so they share
    `columns`. That is the shape a deep run produces — two steps on the same
    sub-question — and it is also what makes the comparison honest: two
    differently-shaped results would need a join this module has no way to
    check.
    """
    if len(before_rows) > MAX_ROWS or len(after_rows) > MAX_ROWS:
        return Refusal(
            code=RefusalCode.TOO_MANY_ROWS,
            reason=f"Past the {MAX_ROWS:,} rows a side this analysis reads.",
        )

    chosen = _choose(columns, measure, dimension)
    if isinstance(chosen, Refusal):
        return chosen
    measure, dimension = chosen

    refusal = unattributable(measure)
    if refusal is not None:
        return refusal

    names = [c.name for c in columns]
    at_dim, at_measure = names.index(dimension.name), names.index(measure.name)

    before = _totals(before_rows, at_dim, at_measure)
    after = _totals(after_rows, at_dim, at_measure)
    if isinstance(before, Refusal):
        return before
    if isinstance(after, Refusal):
        return after
    if not before and not after:
        return Refusal(
            code=RefusalCode.NOT_ENOUGH_ROWS,
            reason="Neither period has a row to compare.",
        )

    kind = classify(measure)
    if kind is MeasureClass.RATIO:
        weight = weight or weight_for(measure, columns)
        if weight is None:
            return Refusal(
                code=RefusalCode.RATIO_NEEDS_WEIGHT,
                reason=(
                    f"{measure.name!r} is an average or a rate, and averages do "
                    "not add up across segments. Include the count each one was "
                    "taken over and this can be attributed; without it, any "
                    "share would be invented."
                ),
                subject=measure.name,
            )
        at_weight = names.index(weight.name)
        return _ratio(
            measure=measure,
            dimension=dimension,
            before=before,
            after=after,
            before_weights=_totals(before_rows, at_dim, at_weight),
            after_weights=_totals(after_rows, at_dim, at_weight),
        )

    if kind is MeasureClass.COMPLEX:
        return _complex(measure, dimension, before, after)
    return _simple(measure, dimension, before, after)


# ── shared setup ─────────────────────────────────────────────────────────
def _choose(
    columns: Sequence[Column], measure: Column | None, dimension: Column | None
) -> tuple[Column, Column] | Refusal:
    """The measure and dimension to work on, named or found."""
    from app.analysis.measures import dimensions as _dimensions
    from app.analysis.measures import measures as _measures

    if measure is None:
        found = _measures(columns)
        if not found:
            return Refusal(
                code=RefusalCode.NO_MEASURE,
                reason="This result has no numeric column to attribute.",
            )
        measure = found[0]
    if dimension is None:
        found_dims = [c for c in _dimensions(columns) if c.name != measure.name]
        if not found_dims:
            return Refusal(
                code=RefusalCode.NO_DIMENSION,
                reason=(
                    "This result has nothing to break the measure down by — "
                    "a total on its own cannot be attributed."
                ),
            )
        dimension = found_dims[0]

    names = [c.name for c in columns]
    for column in (measure, dimension):
        if column.name not in names:
            return Refusal(
                code=RefusalCode.NO_MEASURE,
                reason=f"{column.name!r} is not one of this result's columns.",
                subject=column.name,
            )
    return measure, dimension


def _totals(
    rows: Sequence[Sequence[Any]], at_dim: int, at_measure: int
) -> dict[str, float] | Refusal:
    """One period's measure per dimension value.

    Summed rather than assumed unique: a result may carry a second dimension
    the caller is not attributing by, and rolling it up here is the same
    arithmetic the query would have done with a narrower `GROUP BY`.

    Mixed attribute types are refused rather than coerced — SpotIQ's own
    documented limit, and the reason is that `"42"` and `42` in one column are
    two segments a reader will read as one.
    """
    totals: dict[str, float] = {}
    kinds: set[str] = set()
    for row in rows:
        if at_dim >= len(row) or at_measure >= len(row):
            continue
        key = row[at_dim]
        value = as_number(row[at_measure])
        if value is None:
            continue
        kinds.add("number" if isinstance(key, int | float) and not isinstance(key, bool)
                  else "null" if key is None else "text")
        totals[_label(key)] = totals.get(_label(key), 0.0) + value

    if len({k for k in kinds if k != "null"}) > 1:
        return Refusal(
            code=RefusalCode.MIXED_ATTRIBUTE_TYPES,
            reason=(
                "The column being broken down by holds both numbers and text, "
                "so its values are not one kind of thing."
            ),
        )
    return totals


def _label(value: Any) -> str:
    """A dimension value as the string a sentence will name it by."""
    if value is None:
        return "(not set)"
    return str(value)


def _change_pct(before: float, after: float) -> float | None:
    if before == 0:
        return None
    return ((after - before) / abs(before)) * 100.0


def _flat(change: float, scale: float) -> bool:
    """Whether a change is nothing, relative to the size of what changed.

    No floor under `scale`. A floor of 1.0 would make this an *absolute* test
    below that scale, and a conversion rate going from 0.002 to 0.004 has
    doubled — the one class of measure where an absolute epsilon is certainly
    wrong. Where the scale is zero both totals were zero, so the change is
    zero, and `0 <= 0` is the right answer without any floor at all.
    """
    return abs(change) <= abs(scale) * FLAT_RELATIVE_CHANGE


def _no_change(measure: Column, dimension: Column) -> Refusal:
    return Refusal(
        code=RefusalCode.NO_CHANGE,
        reason=(
            f"{measure.name} did not move between these two periods, so there "
            f"is nothing for {dimension.name} to have driven."
        ),
        subject=measure.name,
    )


def _pairs(
    before: dict[str, float], after: dict[str, float]
) -> list[tuple[str, float, float]]:
    """Every value in either period, as `(value, before, after)`.

    The union, not the intersection: a segment that appeared is often the whole
    answer, and an inner join would drop exactly that case.
    """
    keys = sorted(set(before) | set(after))
    return [(key, before.get(key, 0.0), after.get(key, 0.0)) for key in keys]


# ── SIMPLE ───────────────────────────────────────────────────────────────
def _simple(
    measure: Column,
    dimension: Column,
    before: dict[str, float],
    after: dict[str, float],
) -> Contribution | Refusal:
    """A sum decomposes, so the shares are real and they add to one."""
    total_before, total_after = sum(before.values()), sum(after.values())
    total_change = total_after - total_before
    if _flat(total_change, max(abs(total_before), abs(total_after))):
        return _no_change(measure, dimension)

    rows = _pairs(before, after)
    changes = [after_v - before_v for _k, before_v, after_v in rows]
    spread = outliers.distribution([k for k, _b, _a in rows], changes)

    scored: list[Driver] = []
    for (key, before_v, after_v), change in zip(rows, changes, strict=True):
        z = None
        flagged = False
        if not isinstance(spread, Refusal):
            at = next((o for o in spread.scored if o.label == key), None)
            z = at.z_score if at else None
            flagged = bool(at and any(f.label == key for f in spread.flagged))
        scored.append(
            Driver(
                value=key,
                before=before_v,
                after=after_v,
                change=change,
                contribution=change / total_change,
                z_score=z,
                flagged=flagged,
                appeared=key not in before,
                disappeared=key not in after,
            )
        )

    # SpotIQ examines the top ten absolute changes; the rest are detail.
    scored.sort(key=lambda d: -abs(d.change))
    drivers = tuple(scored[:TOP_CHANGES])

    notes: list[str] = []
    leader = drivers[0] if drivers else None
    if leader and leader.contribution is not None and abs(leader.contribution) > DOMINANT_SHARE:
        notes.append(
            f"{leader.value} accounts for "
            f"{abs(leader.contribution) * 100:.0f}% of the change on its own."
        )
    if isinstance(spread, Refusal):
        notes.append("Every segment moved by about the same amount.")
    elif not spread.discriminating:
        notes.append(
            f"With {spread.count} values, no segment can clear the "
            f"{spread.threshold:.1f} threshold whatever the data does, so "
            "these are ranked by size of change rather than flagged as unusual."
        )
    for driver in drivers:
        if driver.appeared:
            notes.append(f"{driver.value} is new in the second period.")
        elif driver.disappeared:
            notes.append(f"{driver.value} has no rows in the second period.")

    return Contribution(
        measure=measure.name,
        dimension=dimension.name,
        measure_class=MeasureClass.SIMPLE,
        method="share of the total change, with thresholds derived from the spread",
        total_before=total_before,
        total_after=total_after,
        total_change=total_change,
        total_change_pct=_change_pct(total_before, total_after),
        drivers=drivers,
        notes=tuple(notes),
    )


# ── RATIO ────────────────────────────────────────────────────────────────
def _ratio(
    *,
    measure: Column,
    dimension: Column,
    before: dict[str, float],
    after: dict[str, float],
    before_weights: dict[str, float] | Refusal,
    after_weights: dict[str, float] | Refusal,
) -> Contribution | Refusal:
    """A weighted average, and what it would have been without each segment.

    The overall ratio is `sum(r_v * w_v) / sum(w_v)`, so a segment changes it
    through **both** its own ratio and its share of the weight — which is why
    the hypothetical replaces the pair together rather than the ratio alone. A
    segment whose average held steady while it doubled in size moved the
    overall average, and a method that only tested ratios would miss it
    entirely.
    """
    if isinstance(before_weights, Refusal):
        return before_weights
    if isinstance(after_weights, Refusal):
        return after_weights

    rows = _pairs(before, after)
    overall_before = _weighted(before, before_weights)
    overall_after = _weighted(after, after_weights)
    if overall_before is None or overall_after is None:
        return Refusal(
            code=RefusalCode.RATIO_NEEDS_WEIGHT,
            reason=(
                "The weights behind this average are zero or missing, so there "
                "is no overall average to attribute."
            ),
            subject=measure.name,
        )
    if overall_before == 0:
        return Refusal(
            code=RefusalCode.ZERO_BASELINE,
            reason=(
                f"{measure.name} was zero in the first period, so a percentage "
                "change in it is undefined."
            ),
            subject=measure.name,
        )

    actual_pct = ((overall_after - overall_before) / abs(overall_before)) * 100.0
    if _flat(overall_after - overall_before, abs(overall_before)):
        return _no_change(measure, dimension)

    drivers: list[Driver] = []
    for key, before_v, after_v in rows:
        held = dict(after)
        held_w = dict(after_weights)
        # "Had this segment not changed": its ratio *and* its weight put back
        # to what they were, with every other segment left as it ended.
        if key in before:
            held[key] = before[key]
        else:
            held.pop(key, None)
        if key in before_weights:
            held_w[key] = before_weights[key]
        else:
            held_w.pop(key, None)

        hypothetical = _weighted(held, held_w)
        hypothetical_pct = (
            ((hypothetical - overall_before) / abs(overall_before)) * 100.0
            if hypothetical is not None
            else None
        )
        drivers.append(
            Driver(
                value=key,
                before=before_v,
                after=after_v,
                change=after_v - before_v,
                hypothetical_change_pct=hypothetical_pct,
                appeared=key not in before,
                disappeared=key not in after,
            )
        )

    # Smaller hypothetical ⇒ stronger explanation: if the overall change nearly
    # vanishes once this segment is held still, this segment *was* the change.
    # A segment whose hypothetical is unknown sorts last rather than first.
    drivers.sort(
        key=lambda d: (
            d.hypothetical_change_pct is None,
            abs(d.hypothetical_change_pct or 0.0),
        )
    )
    ranked = tuple(drivers[:TOP_CHANGES])

    notes = [
        "Averages do not add up across segments, so these are hypotheticals: "
        f"the overall change is {actual_pct:+.1f}%, and each figure is what it "
        "would have been had that segment not moved.",
    ]
    leader = ranked[0] if ranked else None
    if (
        leader is not None
        and leader.hypothetical_change_pct is not None
        and abs(actual_pct) > 0
        and abs(leader.hypothetical_change_pct) <= abs(actual_pct) * (1 - DOMINANT_SHARE)
    ):
        notes.append(
            f"Without {leader.value} the overall change would have been "
            f"{leader.hypothetical_change_pct:+.1f}% instead of {actual_pct:+.1f}%."
        )

    return Contribution(
        measure=measure.name,
        dimension=dimension.name,
        measure_class=MeasureClass.RATIO,
        method="hypothetical overall change with each segment held still",
        total_before=overall_before,
        total_after=overall_after,
        total_change=overall_after - overall_before,
        total_change_pct=actual_pct,
        drivers=ranked,
        notes=tuple(notes),
    )


def _weighted(
    ratios: dict[str, float], weights: dict[str, float]
) -> float | None:
    """`sum(r * w) / sum(w)` over the segments that have both."""
    total_weight = sum(weights.get(key, 0.0) for key in ratios)
    if total_weight <= 0:
        return None
    return sum(value * weights.get(key, 0.0) for key, value in ratios.items()) / total_weight


# ── COMPLEX ──────────────────────────────────────────────────────────────
def _complex(
    measure: Column,
    dimension: Column,
    before: dict[str, float],
    after: dict[str, float],
) -> Contribution | Refusal:
    """A distinct count: the shape of the change, and no share of it.

    `contribution` stays `None` on every driver here, and `total_before` is the
    sum of the per-segment counts rather than the true overall distinct count —
    which this module does not have and cannot derive. The note says so, every
    time, because a figure labelled "total" that is not one is the kind of
    number that gets quoted.
    """
    rows = _pairs(before, after)
    changes = [after_v - before_v for _k, before_v, after_v in rows]
    spread = outliers.distribution([k for k, _b, _a in rows], changes)
    if isinstance(spread, Refusal):
        if spread.code is RefusalCode.NO_CHANGE:
            return _no_change(measure, dimension)
        return spread

    drivers = [
        Driver(
            value=key,
            before=before_v,
            after=after_v,
            change=change,
            z_score=next((o.z_score for o in spread.scored if o.label == key), None),
            flagged=any(f.label == key for f in spread.flagged),
            appeared=key not in before,
            disappeared=key not in after,
        )
        for (key, before_v, after_v), change in zip(rows, changes, strict=True)
    ]
    drivers.sort(key=lambda d: -abs(d.z_score or 0.0))
    ranked = tuple(drivers[:TOP_CHANGES])

    notes = [
        f"{measure.name} is a distinct count: the per-segment figures do not "
        "add up to the overall one, because the same thing can be counted in "
        "two segments. These are how unusual each segment's change is, not a "
        "share of the total.",
    ]
    if not spread.discriminating:
        notes.append(
            f"With {spread.count} values, no segment can clear the "
            f"{spread.threshold:.1f} threshold whatever the data does, so these "
            "are ranked by how far from the middle they are rather than "
            "flagged as unusual."
        )
    elif not spread.flagged:
        notes.append("No segment's change stands out from the others'.")

    summed_before, summed_after = sum(before.values()), sum(after.values())
    return Contribution(
        measure=measure.name,
        dimension=dimension.name,
        measure_class=MeasureClass.COMPLEX,
        method=f"z-score over the change distribution at N={spread.threshold:.1f}",
        total_before=summed_before,
        total_after=summed_after,
        total_change=summed_after - summed_before,
        total_change_pct=_change_pct(summed_before, summed_after),
        drivers=ranked,
        notes=tuple(notes),
    )
