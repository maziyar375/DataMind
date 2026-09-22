"""Which values in a distribution are far enough out to be worth naming.

Two callers, and they want the same arithmetic for different reasons:

* `contribution.py`'s **SIMPLE** case needs *derived thresholds* — the point
  past which a segment's change stops being ordinary variation. SpotIQ's own
  description is "flag values outside the derived upper/lower thresholds", and
  derived is the operative word: a fixed cut-off is a number somebody chose,
  and the whole claim of this package is that nobody chose the numbers.
* `contribution.py`'s **COMPLEX** case has nothing *but* this. A distinct count
  does not decompose, so the only readable thing about a set of per-segment
  changes is their shape.

## Why the threshold moves with cardinality

Research §3.4 records SpotIQ's rule as *"N between 2.0 and 5.0 chosen by
dimension cardinality — higher cardinality, stricter threshold"*, and the
reason it is right is the multiple-comparisons problem. Testing four regions
for an extreme change is four chances to see one; testing four thousand SKUs is
four thousand chances, and at a fixed N the second will always produce
"outliers" that are nothing but the tail of a normal distribution. A threshold
that does not move with the number of comparisons is a threshold that
manufactures findings on wide dimensions — and wide dimensions are exactly
where a reader cannot check the answer by eye.

The mapping is a table rather than a formula, because a table can be read and
argued with:

| values tested | N |
|---|---|
| ≤ 5 | 2.0 |
| ≤ 20 | 2.5 |
| ≤ 50 | 3.0 |
| ≤ 200 | 3.5 |
| ≤ 1,000 | 4.0 |
| more | 5.0 |

## The ceiling nobody mentions, and what this module does about it

A z-score computed over a population of `n` values has a **hard maximum** of
`(n - 1) / sqrt(n)`, because one value can only be so far from a mean it is
itself part of. For four regions that ceiling is 1.5; for six it is 2.04; for
eight, 2.47. Every one of those is *below* the threshold the table above
chooses, which means that on a narrow dimension — four channels, six
categories, four loyalty tiers, the dimensions most business questions are
actually about — **no value can ever be flagged, whatever the data does.**

That is not a reason to lower the threshold. A threshold clamped to the
attainable range would flag the largest of any three numbers, which is the
opposite failure and a worse one. It is a reason to *say so*: `discriminating`
is false when the chosen N is unreachable, and a caller reading an empty
`flagged` can tell "nothing stood out" from "this method cannot tell". The
`contribution.py` COMPLEX case uses exactly that to fall back to a plain
ranking by size of change, with a note, rather than reporting silence as a
finding.

It is written down here because it is invisible in the output and would
otherwise be discovered as "the outlier detection never fires", six months
later, by somebody who assumes the data is quiet.

## Why a population standard deviation, and why a tiny spread refuses

`stdev` here divides by `n`, not `n - 1`: these are not a sample of a larger
population of regions, they are all the regions, and the sample correction
would be a correction for an inference nobody is making.

And when the spread is effectively zero — every segment changed by the same
amount, or by nothing — every z-score is either 0 or infinite, and the honest
answer is that there is nothing to flag. That case is not hypothetical on flat
data; it is the commonest one, which is why it returns a `Refusal` rather than
a division by something close enough to zero to produce a number.

Pure. See `measures.py` for the package's charter.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.analysis.measures import Refusal, RefusalCode

#: SpotIQ's range, as a table of (at most this many values, this N).
_THRESHOLDS: tuple[tuple[int, float], ...] = (
    (5, 2.0),
    (20, 2.5),
    (50, 3.0),
    (200, 3.5),
    (1_000, 4.0),
)

#: The N past the table's last row.
_WIDEST_N = 5.0

#: Below this, relative to the mean magnitude, the spread is not a spread.
#: A z-score over it is arithmetic on floating-point noise.
_FLAT_RELATIVE_SPREAD = 1e-9

#: More than this and the caller is asking about a dimension nobody will read
#: the answer to, at O(n log n) for the ranking. `reports/facts.py`'s ceiling.
MAX_VALUES = 20_000


def threshold_for(cardinality: int) -> float:
    """The z-score past which a value is flagged, for a dimension this wide."""
    for ceiling, n in _THRESHOLDS:
        if cardinality <= ceiling:
            return n
    return _WIDEST_N


@dataclass(frozen=True, slots=True)
class Outlier:
    """One value far enough from the middle to be worth a sentence."""

    label: str
    value: float
    z_score: float

    @property
    def direction(self) -> str:
        return "HIGH" if self.z_score >= 0 else "LOW"


@dataclass(frozen=True, slots=True)
class Distribution:
    """The shape of a set of labelled numbers, and what stands out in it.

    `flagged` is ordered by distance from the middle, furthest first, so a
    caller that wants the top three takes the first three. It is empty when
    nothing cleared the threshold, and that is a finding rather than a failure:
    **"every segment moved about the same amount" is the answer to a great many
    *why* questions**, and the module says it rather than ranking noise.
    """

    mean: float
    stdev: float
    threshold: float
    #: How many values were tested — the number the threshold was chosen from.
    count: int
    flagged: tuple[Outlier, ...] = field(default_factory=tuple)
    #: Every value with its z-score, in the order given. For a caller that
    #: wants to say how ordinary an unflagged value was.
    scored: tuple[Outlier, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return True

    @property
    def max_attainable_z(self) -> float:
        """The largest |z| any value in a population of this size can have.

        `(n - 1) / sqrt(n)`, and it is a fact about arithmetic rather than
        about the data: the mean is computed from the same values being scored.
        """
        return (self.count - 1) / math.sqrt(self.count) if self.count > 1 else 0.0

    @property
    def discriminating(self) -> bool:
        """Whether a value *could* clear the threshold at this cardinality.

        False means an empty `flagged` says nothing about the data — see the
        module docstring. A caller that reports "no outliers" without checking
        this is reporting arithmetic as a finding.
        """
        return self.threshold <= self.max_attainable_z

    @property
    def upper(self) -> float:
        """The derived upper threshold, in the values' own units."""
        return self.mean + self.threshold * self.stdev

    @property
    def lower(self) -> float:
        return self.mean - self.threshold * self.stdev


def distribution(
    labels: Sequence[str], values: Sequence[float]
) -> Distribution | Refusal:
    """Z-scores over one set of labelled numbers, at a cardinality-chosen N.

    The values are usually *changes* rather than levels — what each segment did
    between two periods — but nothing here requires that, which is why the
    argument is a bare sequence rather than a period pair.
    """
    if len(labels) != len(values):
        return Refusal(
            code=RefusalCode.NOT_ENOUGH_VALUES,
            reason="Each value needs a label and each label a value.",
        )
    if len(values) > MAX_VALUES:
        return Refusal(
            code=RefusalCode.TOO_MANY_ROWS,
            reason=(
                f"{len(values):,} values is past the {MAX_VALUES:,} this "
                "analysis reads."
            ),
        )
    if len(values) < 2:
        return Refusal(
            code=RefusalCode.NOT_ENOUGH_VALUES,
            reason="A single value has nothing to be unusual against.",
        )

    if not all(math.isfinite(v) for v in values):
        return Refusal(
            code=RefusalCode.NOT_ENOUGH_VALUES,
            reason=(
                "Some of these values are infinite or not numbers, so the "
                "spread cannot be computed over them."
            ),
        )

    count = len(values)
    mean = sum(values) / count
    variance = sum((v - mean) ** 2 for v in values) / count
    stdev = math.sqrt(variance)

    # Flat, to within floating point. Scaled by the magnitude of the numbers
    # themselves and **with no floor of 1.0**, because a floor turns the test
    # absolute below that scale: a conversion rate moving from 0.002 to 0.004
    # has doubled, and a fixed epsilon would call it flat. Where every value is
    # zero the scale is zero too, and the test becomes `stdev <= 0` — which is
    # exactly right, and is the case the floor was there to guard.
    scale = max(abs(mean), max(abs(v) for v in values))
    if stdev <= scale * _FLAT_RELATIVE_SPREAD:
        return Refusal(
            code=RefusalCode.NO_CHANGE,
            reason=(
                "Every value here is the same to within rounding, so none of "
                "them stands out."
            ),
        )

    n = threshold_for(count)
    scored = tuple(
        Outlier(label=label, value=value, z_score=(value - mean) / stdev)
        for label, value in zip(labels, values, strict=True)
    )
    flagged = tuple(
        sorted(
            (o for o in scored if abs(o.z_score) >= n),
            key=lambda o: -abs(o.z_score),
        )
    )
    return Distribution(
        mean=mean,
        stdev=stdev,
        threshold=n,
        count=count,
        flagged=flagged,
        scored=scored,
    )
