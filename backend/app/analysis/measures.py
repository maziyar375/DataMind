"""What kind of number a result column holds, and why it decides the algorithm.

This is the file the rest of the package hangs off, and the reason it exists is
the single most useful finding in
[research/deep-analysis-mode.md](../../../docs/research/deep-analysis-mode.md)
§3.4: **root-cause attribution is not one algorithm, it is three, chosen by
measure type.** A single `contribution = Δ(segment) / Δ(total)` is not merely
imprecise for two of the three — it is meaningless.

* A **sum** decomposes. Revenue is the sum of revenue per region, so the
  regions' changes add up to the total's change and a share of it is a real
  quantity.
* An **average** does not. Segment averages do not add to the overall average,
  and a "contribution" computed as if they did is a number with no referent.
  What is computable is a *hypothetical*: what the overall change would have
  been had this segment not moved.
* A **distinct count** does not even have that. Distinct customers per region
  do not sum to distinct customers overall, because one customer can be in two
  regions, and no amount of arithmetic over the per-segment figures recovers
  the overlap. Only the *shape* of the change distribution is readable.

So the classification below is load-bearing rather than tidy: getting it wrong
routes a measure to an algorithm that will return a confident number that means
nothing, which is worse than returning nothing at all.

## How a column is classified, and why it is by name

By name, because a name is all a result column carries. Every one of these
comes back from the database typed `numeric`, and the connectors' semantic type
says `quantitative` for all of them — neither can tell `total_revenue` from
`avg_order_value`. `app/reports/facts.py` reached the same conclusion for the
same reason and its two regular expressions are the precedent this file
follows, widened where the distinction matters here and it did not there.

The order is deliberate: **COMPLEX wins, then RATIO, then SIMPLE.** Each step
down is a step towards more arithmetic being permitted, so the fallback is the
most permissive one — and that is the bet this file makes, stated plainly: an
unrecognised quantitative column is treated as a sum. It is the same bet
`facts.py` makes with `_additive`, it is right for the overwhelming majority of
columns an analytical query returns, and the alternative (refusing everything
unfamiliar) would make the module useless on any schema whose naming this file
has not anticipated.

Pure, like `app/reports/` and for its reasons: lists and strings in, frozen
dataclasses out. No session, no settings, no model, no tokens — and, by the
ninth import-linter contract, no way to acquire any of them.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MeasureClass(StrEnum):
    """Which of §3.4's three algorithms a measure may be attributed with."""

    #: `SUM`, `COUNT`. Decomposes: the segments' changes add to the total's.
    SIMPLE = "SIMPLE"
    #: `AVG`, `SUM/SUM`. Does not decompose; a hypothetical is computable when
    #: the weight behind the ratio is also in the result.
    RATIO = "RATIO"
    #: `COUNT(DISTINCT)`. Neither decomposes nor has a hypothetical, because
    #: the per-segment figures do not carry the overlap between segments.
    COMPLEX = "COMPLEX"


class RefusalCode(StrEnum):
    """Why this module declined to compute something.

    Every one of these is returned, never raised. A refusal is an ordinary
    outcome of asking a question the data cannot answer, and it is the outcome
    a *why* question most often has: the module's whole value is that it says
    "nothing moved" where a model would name a driver.

    The first three are ThoughtSpot SpotIQ's own documented limits, copied
    rather than rediscovered ([research §3.4]). The rest were found by writing
    the arithmetic down.
    """

    #: SpotIQ refuses "growth of" / "versus" phrasings, and so does this: a
    #: measure that is *already* a change cannot be decomposed into a change.
    COMPARATIVE_MEASURE = "COMPARATIVE_MEASURE"
    #: SpotIQ refuses complex `group_*` formulas. A measure already aggregated
    #: across a grouping this module cannot see is not one it can re-aggregate.
    GROUP_FORMULA = "GROUP_FORMULA"
    #: SpotIQ refuses mixed attribute types, and a dimension column holding
    #: both `"web"` and `42` is one column by position and two by meaning.
    MIXED_ATTRIBUTE_TYPES = "MIXED_ATTRIBUTE_TYPES"

    NO_MEASURE = "NO_MEASURE"
    NO_DIMENSION = "NO_DIMENSION"
    NOT_ENOUGH_ROWS = "NOT_ENOUGH_ROWS"
    TOO_MANY_ROWS = "TOO_MANY_ROWS"
    #: The overall figure did not move. There is no change to attribute, and
    #: naming a driver for one would be inventing it — the most important
    #: refusal in the package, and the commonest true answer to a *why*.
    NO_CHANGE = "NO_CHANGE"
    #: The measure was zero before, so a percentage change is undefined and
    #: every share of it is infinite.
    ZERO_BASELINE = "ZERO_BASELINE"
    #: A ratio's hypothetical needs the weight behind it — the count the
    #: average was taken over. Assuming equal weights produces exactly the
    #: confident nonsense this package exists to avoid.
    RATIO_NEEDS_WEIGHT = "RATIO_NEEDS_WEIGHT"
    #: Two periods of different lengths are not comparable without saying so.
    UNCOMPARABLE_PERIODS = "UNCOMPARABLE_PERIODS"
    #: Fewer than two distinct values to spread over: a z-score of one number
    #: against itself is not a finding.
    NOT_ENOUGH_VALUES = "NOT_ENOUGH_VALUES"


@dataclass(frozen=True, slots=True)
class Refusal:
    """A declined computation, as a value.

    **Falsey**, so `if result:` reads the way a caller means it, and the same
    shape `FactSheet` already uses. `reason` is written for a reader rather
    than a developer: it is what the prose says when a deep step cannot answer
    its sub-question, and "nothing moved between these periods" is a finding a
    business reader can act on.
    """

    code: RefusalCode
    reason: str
    #: The column or value the refusal is about, where naming one helps.
    subject: str = ""

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Column:
    """One result column, as this package reads it.

    `semantic_type` is the classification the connectors already attach to
    every result column (`quantitative` | `temporal` | `nominal` | `ordinal`),
    so nothing here guesses a column's kind from its values. Identical in shape
    to `reports.facts.FactColumn` and deliberately not shared with it: that
    package is self-contained by contract and so is this one, and a type
    imported across the boundary is the first step to the boundary going.
    """

    name: str
    semantic_type: str = "nominal"

    @property
    def is_numeric(self) -> bool:
        return self.semantic_type == "quantitative"

    @property
    def is_temporal(self) -> bool:
        return self.semantic_type == "temporal"

    @property
    def is_categorical(self) -> bool:
        return self.semantic_type in ("nominal", "ordinal")


# A distinct count, however it is spelled. First, because it is the class whose
# arithmetic is most wrong if it falls through to one of the others.
_COMPLEX = re.compile(
    r"(?:^|_)(?:distinct|unique|uniq|nunique|dedup|deduped)(?:_|$)|"
    r"count_distinct|distinct_count",
    re.IGNORECASE,
)

# A measure that is already a change. SpotIQ's "growth of" / "versus" refusal,
# and the reason is arithmetic rather than parsing: the second difference of a
# series is not a thing a business question is asking about.
_COMPARATIVE = re.compile(
    r"(?:^|_)(?:growth|change|delta|diff|difference|variance|versus|vs|"
    r"yoy|mom|qoq|wow|pop|uplift|lift|gap)(?:_|$)|"
    r"_pct_change|_change_pct|_vs_",
    re.IGNORECASE,
)

# SpotIQ's other documented refusal: a `group_*` formula is already aggregated
# across a grouping this module cannot see.
_GROUP_FORMULA = re.compile(r"^group_|(?:^|_)group_(?:sum|avg|average|count|max|min)",
                            re.IGNORECASE)

# Non-additive, and **nothing overrides these**: a name carrying one of them
# is a ratio however else it is spelled, because every one of them names an
# aggregation that has already divided by something. `avg_total_revenue` is an
# average, not a total, and it is the case that makes this list dominant
# rather than merely present.
#
# The asymmetry is deliberate and it is the file's one safety margin. Calling a
# true sum a ratio costs an answer: the hypothetical needs a weight, and
# without one the module refuses. Calling a true ratio a sum costs a *wrong*
# answer — segment shares of a change that does not decompose — which is the
# risk `docs/plans/deep-analysis-mode.md` §3.3 names for this phase. So where
# the name is ambiguous, the ratio reading wins.
_RATIO_STRONG = re.compile(
    r"(?:^|_)(?:avg|average|mean|median|rate|ratio|pct|percent|percentage|share|"
    r"margin|score|index|per|yield|conversion|density|utilisation|utilization)"
    r"(?:_|$)",
    re.IGNORECASE,
)

# A name that says outright that it is a sum or a count. These beat the weak
# ratio list below, because `total_price` and `sum_cost` are exactly the
# columns it would otherwise refuse — and they lose to `_RATIO_STRONG` above,
# because `avg_total` is an average of totals and not a total.
_ADDITIVE = re.compile(
    r"(?:^|_)(?:total|sum|count|num|qty|quantity|revenue|sales|amount|orders|units)"
    r"(?:_|$)",
    re.IGNORECASE,
)

# Non-additive *unless* the name also says it was summed. A price is one
# item's price and does not add up; a `total_price` does.
_RATIO_WEAK = re.compile(
    r"(?:^|_)(?:price|cost|balance|level|fee|salary|age|duration|latency)(?:_|$)",
    re.IGNORECASE,
)

# An identifier is a number the way a phone number is a number.
_ID_LIKE = re.compile(
    r"(?:^|_)(?:id|key|code|no|number|pk|fk|uuid|guid)(?:_|$)|_id$|^id$",
    re.IGNORECASE,
)

#: Names that mean "how many of these were behind the average" — the weight a
#: ratio's hypothetical needs. Looked for automatically so the common result
#: shape (`region, avg_order_value, orders`) works without being told.
_WEIGHT = re.compile(
    r"(?:^|_)(?:count|orders|n|rows|volume|units|qty|quantity|weight|denominator|"
    r"observations|samples|txns|transactions)(?:_|$)",
    re.IGNORECASE,
)


def classify(column: Column) -> MeasureClass:
    """Which of the three algorithms this measure may be attributed with.

    COMPLEX, then RATIO, then SIMPLE — see the module docstring for why the
    fallback is the permissive one and what that bet costs.
    """
    name = column.name
    if _COMPLEX.search(name):
        return MeasureClass.COMPLEX
    if _RATIO_STRONG.search(name):
        return MeasureClass.RATIO
    if _ADDITIVE.search(name):
        return MeasureClass.SIMPLE
    if _RATIO_WEAK.search(name):
        return MeasureClass.RATIO
    return MeasureClass.SIMPLE


def unattributable(column: Column) -> Refusal | None:
    """SpotIQ's two name-shaped refusals, before any arithmetic is attempted.

    Checked on the measure's name rather than on its values because both are
    facts about what the column *is*: a column named `revenue_growth` holds a
    change, and attributing a change in a change to a segment answers a
    question nobody asked.
    """
    if _GROUP_FORMULA.search(column.name):
        return Refusal(
            code=RefusalCode.GROUP_FORMULA,
            reason=(
                f"{column.name!r} is already aggregated across a grouping this "
                "analysis cannot see, so it cannot be broken down again."
            ),
            subject=column.name,
        )
    if _COMPARATIVE.search(column.name):
        return Refusal(
            code=RefusalCode.COMPARATIVE_MEASURE,
            reason=(
                f"{column.name!r} is already a change. Attribute the measure it "
                "was computed from, not the change itself."
            ),
            subject=column.name,
        )
    return None


def measures(columns: Sequence[Column]) -> list[Column]:
    """The numeric columns worth doing arithmetic on.

    Identifiers are dropped: `SELECT customer_id, orders …` has two numeric
    columns and one measure, and "the biggest customer_id" is not a finding.
    """
    return [c for c in columns if c.is_numeric and not _ID_LIKE.search(c.name)]


def dimensions(columns: Sequence[Column]) -> list[Column]:
    """The columns a measure can be broken down by, categorical ones first.

    Temporal columns come last rather than being dropped: a period is a
    dimension, but a question asking *what drove this* almost never means
    "which month", and a result carrying both should be attributed by the
    category unless the caller says otherwise.
    """
    return [c for c in columns if c.is_categorical] + [
        c for c in columns if c.is_temporal
    ]


def weight_for(measure: Column, columns: Sequence[Column]) -> Column | None:
    """The count a ratio was taken over, if the result happens to carry it.

    Returned rather than assumed: a ratio with no weight is refused, not
    averaged as if every segment were the same size. `avg_order_value` beside
    `orders` is the common shape and works without being told; anything else
    the caller names explicitly.
    """
    candidates = [
        c
        for c in columns
        if c.is_numeric and c.name != measure.name and _WEIGHT.search(c.name)
    ]
    return candidates[0] if candidates else None


def as_number(value: Any) -> float | None:
    """A cell as a finite float, or `None` where it is not one.

    `bool` is excluded on purpose: `True` is an `int` in Python and a flag in a
    result, and summing flags is how a count of something appears out of
    nowhere.

    **Infinity and NaN are excluded too**, and that is not defensiveness. A
    division by zero in SQL reaches Python as `inf` on some drivers, and one
    `inf` in a column makes the mean `inf`, every z-score `nan`, and every
    comparison against a threshold `False` — so the module would report a
    quiet, well-formed distribution in which nothing stands out. A dropped
    value costs a row; a propagated one costs the whole finding.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    try:
        parsed = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
