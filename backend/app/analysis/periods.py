"""Two periods, made comparable — or declared not to be.

The step before every *why did this change* question, and the one nobody asks
for. A reader who says "revenue fell in February" has compared two months, and
two months are not the same length: February 2026 holds 28 trading days against
January's 31, which is 9.7% fewer days before anything about the business has
happened at all. Attributing that gap to a region or a channel apportions the
calendar, and it produces a ranked list of contributions to a shortfall that
does not exist.

So this module does two things, and the second is the one that matters:

1. It splits a series into a *before* and an *after*, at a date or down the
   middle.
2. **It reports both figures per day beside the raw ones, and says when the
   two spans differ.** `comparable` is false when they do, and `note` is the
   sentence a reader needs before reading anything else.

The same applies, harder, to the *current* period. A month that is three weeks
old is not a month that fell 26%; it is a month that is three weeks old. The
fixture this package was written against ends mid-September, and the question
*"sales have fallen off a cliff this month, what happened?"* has exactly one
honest answer — which is why `deep_v1` asks it.

## Why per-day and not per-trading-day

Because a trading calendar is a fact about a business that is not in the result
rows, and inventing one here would be the module estimating. Per calendar day
is computable from what the query returned and is right for the case that
actually bites — months and quarters of different lengths. A business with
genuine weekday seasonality needs its own calendar, and that is a semantic
layer concern rather than an arithmetic one.

Pure. See `measures.py` for the package's charter.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.analysis.measures import Column, Refusal, RefusalCode, as_number

#: How different two spans may be before the comparison stops being one. 5% is
#: under the 28-vs-31-day month gap (9.7%) and over the noise in a quarter that
#: happens to straddle a leap day.
COMPARABLE_TOLERANCE = 0.05

#: Rows read. `reports/facts.py`'s ceiling, for its reason.
MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class Period:
    """One side of a comparison: what it covered and what it totalled."""

    label: str
    first: Any
    last: Any
    #: How many rows — usually buckets of the series — fell in it.
    buckets: int
    #: Calendar days from the first bucket to the last, **inclusive**: a
    #: January of daily rows spans 31 days, not 30.
    span_days: float
    total: float

    @property
    def per_day(self) -> float | None:
        return self.total / self.span_days if self.span_days > 0 else None


@dataclass(frozen=True, slots=True)
class PeriodPair:
    """Two periods and the change between them, stated twice.

    Twice on purpose. `change` and `change_pct` are what the reader asked for;
    `per_day_change_pct` is usually what they meant, and where the two disagree
    the disagreement *is* the answer. A February that is 9.7% short of January
    in revenue and 0.4% short per trading day did not have a bad month.
    """

    before: Period
    after: Period
    change: float
    change_pct: float | None
    per_day_change: float | None
    per_day_change_pct: float | None
    #: Whether the two spans are close enough to put in one sentence.
    comparable: bool
    #: Why not, when not — and what the per-day figures say instead. Empty
    #: where the periods are the same length.
    note: str = ""

    def __bool__(self) -> bool:
        return True


def _as_datetime(value: Any) -> datetime | None:
    """A period cell as something subtractable, or `None`.

    Strings are parsed only in ISO form. A module that guessed at date formats
    would be guessing at which of `03/04` is the month on behalf of a reader it
    cannot ask.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.replace(tzinfo=None)
    return None


def _span_days(first: datetime, last: datetime) -> float:
    """Inclusive calendar days between two bucket starts.

    Inclusive, because these are the *starts* of the first and last bucket of
    the period: a month of daily rows runs 1st to 31st, and the exclusive
    difference would call that 30 days and understate every per-day figure by
    the same 3%.
    """
    return (last - first).days + 1.0


def _period(
    label: str, stamps: Sequence[datetime], values: Sequence[float]
) -> Period:
    return Period(
        label=label,
        first=stamps[0],
        last=stamps[-1],
        buckets=len(values),
        span_days=_span_days(stamps[0], stamps[-1]),
        total=sum(values),
    )


def compare(
    *,
    columns: Sequence[Column],
    rows: Sequence[Sequence[Any]],
    measure: Column | None = None,
    period: Column | None = None,
    split_at: Any = None,
) -> PeriodPair | Refusal:
    """Split a series in two and compare the halves, per day as well as in total.

    `split_at` is the first instant of the *after* period. Without it the
    series is cut down the middle by bucket count, which is what a question
    naming no date ("has this got worse?") means.

    The rows are sorted here rather than trusted to arrive sorted: a query
    without `ORDER BY` returns whatever the engine found convenient, and a
    before/after computed from that order is a comparison of two arbitrary
    halves.
    """
    if len(rows) > MAX_ROWS:
        return Refusal(
            code=RefusalCode.TOO_MANY_ROWS,
            reason=f"{len(rows):,} rows is past the {MAX_ROWS:,} this analysis reads.",
        )

    period = period or next((c for c in columns if c.is_temporal), None)
    if period is None:
        return Refusal(
            code=RefusalCode.NO_DIMENSION,
            reason="No period column, so there are no two periods to compare.",
        )
    if measure is None:
        measure = next(
            (c for c in columns if c.is_numeric and c.name != period.name), None
        )
    if measure is None:
        return Refusal(
            code=RefusalCode.NO_MEASURE,
            reason="No numeric column to compare between the two periods.",
        )

    names = [c.name for c in columns]
    try:
        at_period, at_measure = names.index(period.name), names.index(measure.name)
    except ValueError:
        return Refusal(
            code=RefusalCode.NO_MEASURE,
            reason=f"{measure.name!r} is not one of this result's columns.",
            subject=measure.name,
        )

    points: list[tuple[datetime, float]] = []
    for row in rows:
        stamp = _as_datetime(row[at_period]) if at_period < len(row) else None
        value = as_number(row[at_measure]) if at_measure < len(row) else None
        if stamp is not None and value is not None:
            points.append((stamp, value))

    if len(points) < 2:
        return Refusal(
            code=RefusalCode.NOT_ENOUGH_ROWS,
            reason=(
                "At least two dated rows are needed to compare two periods; "
                f"this result has {len(points)}."
            ),
        )

    points.sort(key=lambda p: p[0])
    cut = _as_datetime(split_at) if split_at is not None else None
    if cut is not None:
        first = [p for p in points if p[0] < cut]
        second = [p for p in points if p[0] >= cut]
    else:
        middle = len(points) // 2
        first, second = points[:middle], points[middle:]

    if not first or not second:
        return Refusal(
            code=RefusalCode.NOT_ENOUGH_ROWS,
            reason=(
                "The split leaves one side empty, so there is nothing to "
                "compare it against."
            ),
        )

    before = _period("before", [p[0] for p in first], [p[1] for p in first])
    after = _period("after", [p[0] for p in second], [p[1] for p in second])
    return _pair(before, after)


def _pair(before: Period, after: Period) -> PeriodPair:
    """The two periods' arithmetic, and the sentence about their lengths."""
    change = after.total - before.total
    change_pct = (
        (change / abs(before.total)) * 100.0 if before.total not in (0, 0.0) else None
    )

    per_day_change: float | None = None
    per_day_change_pct: float | None = None
    if before.per_day is not None and after.per_day is not None:
        per_day_change = after.per_day - before.per_day
        if before.per_day != 0:
            per_day_change_pct = (per_day_change / abs(before.per_day)) * 100.0

    longest = max(before.span_days, after.span_days)
    drift = (
        abs(after.span_days - before.span_days) / longest if longest > 0 else 0.0
    )
    comparable = drift <= COMPARABLE_TOLERANCE

    note = ""
    if not comparable:
        note = (
            f"These periods are not the same length — "
            f"{before.span_days:,.0f} days against {after.span_days:,.0f}. "
        )
        if per_day_change_pct is not None and change_pct is not None:
            note += (
                f"Per day the change is {per_day_change_pct:+.1f}%, against "
                f"{change_pct:+.1f}% in total; read the first one."
            )
        else:
            note += "Compare the per-day figures rather than the totals."

    return PeriodPair(
        before=before,
        after=after,
        change=change,
        change_pct=change_pct,
        per_day_change=per_day_change,
        per_day_change_pct=per_day_change_pct,
        comparable=comparable,
        note=note,
    )
