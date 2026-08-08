"""The arithmetic a section's prose needs, done here instead of by the model.

This is the module that decides whether a report reads like analysis or like a
caption. Handed fifty rows and told to write four sentences, a model does what
a person would: it eyeballs the table and *estimates*. "Revenue grew by around
a fifth" over a series whose ends are 84,120 and 101,660 is a sentence nobody
can act on, and "grew 24%" — worked out in a language model's head from two
numbers in a text table — is worse, because it is precise and wrong.

So the figures a paragraph is most likely to want are computed here, exactly,
from the same rows, and handed to the writer as facts it may quote. Three
things follow at once, and the third is the reason this file exists:

* **The prose gets quantitative.** A section can say what changed, by how much,
  what share the leader holds and how concentrated the tail is, because those
  numbers are in front of it rather than waiting to be derived.
* **The hallucination class shrinks.** The most common invented figure in a
  generated report is not a fabricated total — it is a *derived* one: a growth
  rate, a share, a difference. Computing them removes the reason to invent one.
* **`checks.py` stops crying wolf.** Its known false positive is exactly this
  derived figure, which no result cell holds. Every value stated here is
  returned by `values()` and joins the pool the check matches against, so a
  correctly-quoted growth rate now matches something instead of being flagged.

## Why a partial result gets no facts at all

The one rule in this file that is about safety rather than quality: facts are
computed **only when the rows given are the complete result**, and the caller
says so. Two situations make them a prefix, and both are disqualifying:

* the connection's disclosure policy is `SAMPLE`, so `disclose()` handed over
  the first fifty rows of more;
* the platform's row cap truncated the query.

A total over a prefix is not an approximate total, it is a wrong one, and a
"largest" over a prefix is a claim about rows the writer never saw. There is no
honest way to caption that in a sentence a business reader will read, and a
report whose figures need a footnote to be true is the thing this feature is
trying not to be. Under `SAMPLE` this costs facts only on blocks returning more
than fifty rows — which a report's blocks, being aggregates, mostly do not.

It also means **this module widens no disclosure**: every value it states is
computed from rows the model was already given in full, so a fact sheet can
never carry a number out of a row the policy withheld.

Pure, like the rest of `app/reports/` — lists and strings in, a frozen
dataclass out. No session, no settings, no model, no tokens.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

#: Facts per block. Past a dozen the model stops reading them as the figures for
#: this paragraph and starts treating them as another table to summarise.
MAX_FACTS = 14

#: Rows scanned. A block returning more than this is not a block a paragraph is
#: written from, and the arithmetic is O(rows) per column.
MAX_ROWS = 20_000

#: How far apart the two halves of a series must be before the direction is
#: called. Below it, "flat" is the honest word and a model given "rising" will
#: write a trend into noise.
TREND_THRESHOLD_PCT = 2.0

#: Shares reported for the leading categories, in order.
TOP_N = (1, 3, 5)

#: The share the Pareto fact counts up to.
PARETO_SHARE = 0.8

# Columns whose values do not add up. Summing a rate, an average or a unit price
# produces a number that is arithmetically fine and semantically nonsense, and a
# model handed "Total avg_order_value: 41,203" will put it in a sentence.
# Matched on the name because that is all a result column carries — the database
# type says `numeric` for every one of them.
_NON_ADDITIVE = re.compile(
    r"(?:^|_)(?:avg|average|mean|median|rate|ratio|pct|percent|percentage|share|"
    r"margin|score|index|price|cost|balance|level|per)(?:_|$)",
    re.IGNORECASE,
)

# ...unless the name says outright that it is already a sum or a count. These
# win, because `total_price` and `sum_cost` are exactly the columns the rule
# above would otherwise refuse.
_ADDITIVE = re.compile(
    r"(?:^|_)(?:total|sum|count|num|qty|quantity|revenue|sales|amount|orders|units)"
    r"(?:_|$)",
    re.IGNORECASE,
)

# An identifier is a number the way a phone number is a number.
_ID_LIKE = re.compile(r"(?:^|_)(?:id|key|code|no|number|pk|fk|uuid|guid)(?:_|$)|_id$|^id$",
                      re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FactColumn:
    """One result column, as this module needs to read it.

    `semantic_type` is the classification the connectors already attach to every
    result column (`quantitative` | `temporal` | `nominal` | `ordinal`), so
    nothing here has to guess a column's kind from its values.
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


@dataclass(frozen=True, slots=True)
class Fact:
    """One computed statement, and the numbers it states.

    `values` is not decoration and not a duplicate of `text`: it is what
    `checks.py` matches a paragraph's figures against, so a growth rate this
    module computed and the model quoted correctly is recognised instead of
    flagged. A fact whose sentence names no figure carries an empty tuple.
    """

    text: str
    values: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class FactSheet:
    facts: tuple[Fact, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.facts)

    def render(self) -> str:
        """The facts as the model reads them. Empty string when there are none."""
        return "\n".join(f"- {fact.text}" for fact in self.facts)

    def values(self) -> list[float]:
        """Every figure stated, for the numeric check's pool."""
        return [value for fact in self.facts for value in fact.values]


def compute(
    *,
    columns: Sequence[FactColumn],
    rows: Sequence[Sequence[Any]],
    complete: bool = True,
) -> FactSheet:
    """The figures worth stating about one block's result.

    `complete` is the caller's assertion that these rows are the whole result —
    not a disclosure sample, not a truncated query. It is false that carries the
    weight: see the module docstring for why a prefix gets nothing rather than
    something approximate.
    """
    if not complete or not rows or not columns:
        return FactSheet()

    body = [list(row) for row in rows[:MAX_ROWS]]
    measures = _measures(columns)
    if not measures:
        return FactSheet()

    dimension = _dimension(columns)
    facts: list[Fact] = []

    if len(body) == 1:
        facts.extend(_single_row_facts(columns, body[0], measures))
    else:
        for measure in measures[:2]:
            if dimension is not None and dimension.is_temporal:
                facts.extend(_series_facts(columns, body, dimension, measure))
            elif dimension is not None:
                facts.extend(_category_facts(columns, body, dimension, measure))
            else:
                facts.extend(_spread_facts(columns, body, measure))

    return FactSheet(facts=tuple(facts[:MAX_FACTS]))


# ── which columns carry what ─────────────────────────────────────────────
def _measures(columns: Sequence[FactColumn]) -> list[FactColumn]:
    """The numeric columns worth doing arithmetic on.

    Identifiers are dropped: `SELECT customer_id, orders FROM …` has two numeric
    columns and one measure, and "the largest customer_id" is not a finding.
    """
    return [c for c in columns if c.is_numeric and not _ID_LIKE.search(c.name)]


def _dimension(columns: Sequence[FactColumn]) -> FactColumn | None:
    """What the measure is broken down by — a period first, then a category.

    Temporal wins because a result carrying both ("month, region, revenue") is a
    series the reader reads as a series, and the facts a series wants (start,
    end, change, peak) are not the facts a ranking wants.
    """
    temporal = next((c for c in columns if c.is_temporal), None)
    if temporal is not None:
        return temporal
    return next((c for c in columns if c.is_categorical), None)


def _additive(column: FactColumn) -> bool:
    """Whether summing this column produces a number worth saying out loud."""
    if _ADDITIVE.search(column.name):
        return True
    return not _NON_ADDITIVE.search(column.name)


# ── the three shapes a result comes in ───────────────────────────────────
def _series_facts(
    columns: Sequence[FactColumn],
    rows: list[list[Any]],
    dimension: FactColumn,
    measure: FactColumn,
) -> list[Fact]:
    """A measure over time: where it started, where it ended, and what happened.

    Ordered by the period column rather than trusted to arrive ordered. A query
    without `ORDER BY` returns whatever the engine found convenient, and "fell
    from 101,660 to 84,120" read off an unordered result is a fabricated trend
    with real numbers in it — the worst kind.
    """
    d, m = _index(columns, dimension), _index(columns, measure)
    if d is None or m is None:
        return []

    points = [
        (row[d], _number(row[m]))
        for row in rows
        if _number(row[m]) is not None and row[d] is not None
    ]
    if len(points) < 2:
        return []
    points = _ordered(points)

    label = _clean(measure.name)
    facts: list[Fact] = [
        Fact(
            f"{label} covers {len(points)} periods, from "
            f"{_label(points[0][0])} to {_label(points[-1][0])}."
        )
    ]

    first, last = points[0], points[-1]
    values = [value for _, value in points]

    facts.append(
        Fact(
            f"{label} was {_fmt(first[1])} at {_label(first[0])} and "
            f"{_fmt(last[1])} at {_label(last[0])}.",
            (first[1], last[1]),
        )
    )

    change = last[1] - first[1]
    pct = _pct_change(first[1], last[1])
    if pct is not None:
        facts.append(
            Fact(
                f"Change from first period to last: {_signed(change)} "
                f"({_signed_pct(pct)}).",
                (abs(change), abs(pct)),
            )
        )

    peak = max(points, key=lambda point: point[1])
    trough = min(points, key=lambda point: point[1])
    if peak[0] != trough[0]:
        facts.append(
            Fact(
                f"Highest {label}: {_fmt(peak[1])} at {_label(peak[0])}. "
                f"Lowest: {_fmt(trough[1])} at {_label(trough[0])}.",
                (peak[1], trough[1]),
            )
        )

    mean = sum(values) / len(values)
    facts.append(Fact(f"Mean {label} per period: {_fmt(mean)}.", (mean,)))

    if _additive(measure):
        total = sum(values)
        facts.append(
            Fact(f"{label} summed over every period: {_fmt(total)}.", (total,))
        )

    direction = _direction(values)
    if direction is not None:
        facts.append(direction)
    return facts


def _category_facts(
    columns: Sequence[FactColumn],
    rows: list[list[Any]],
    dimension: FactColumn,
    measure: FactColumn,
) -> list[Fact]:
    """A measure across categories: the ranking, and how top-heavy it is.

    Concentration is the fact this shape is usually *for* and the one a model
    never volunteers. "The leading product is Widget A" is a caption; "Widget A
    is 31% of revenue and the top three are 58%" is the finding, and it is one
    division away from data already on the page.
    """
    d, m = _index(columns, dimension), _index(columns, measure)
    if d is None or m is None:
        return []

    pairs = [
        (row[d], _number(row[m]))
        for row in rows
        if _number(row[m]) is not None
    ]
    if len(pairs) < 2:
        return []

    ranked = sorted(pairs, key=lambda pair: pair[1], reverse=True)
    values = [value for _, value in ranked]
    label = _clean(measure.name)
    group = _clean(dimension.name)

    facts: list[Fact] = []
    total = sum(values)
    additive = _additive(measure)

    if additive:
        facts.append(
            Fact(
                f"{label} across all {len(ranked)} {group} values: "
                f"{_fmt(total)}.",
                (total, float(len(ranked))),
            )
        )

    top, bottom = ranked[0], ranked[-1]
    facts.append(
        Fact(
            f"Highest {label}: {_label(top[0])} at {_fmt(top[1])}. "
            f"Lowest: {_label(bottom[0])} at {_fmt(bottom[1])}.",
            (top[1], bottom[1]),
        )
    )

    # Shares only where they mean something: a percentage of a sum of averages
    # is a number with no referent, and negative values make a "share" that can
    # exceed 100% or flip sign.
    if additive and total > 0 and all(value >= 0 for value in values):
        for n in TOP_N:
            if len(ranked) <= n:
                break
            share = sum(values[:n]) / total * 100.0
            leader = _label(top[0]) if n == 1 else f"top {n}"
            facts.append(
                Fact(
                    f"{leader} accounts for {_pct(share)} of {label}."
                    if n == 1
                    else f"The {leader} together account for {_pct(share)} of {label}.",
                    (share,),
                )
            )

        needed = _pareto(values, total)
        if needed is not None and needed < len(ranked):
            facts.append(
                Fact(
                    f"{needed} of the {len(ranked)} {group} values make up "
                    f"{_pct(PARETO_SHARE * 100)} of {label}.",
                    (float(needed), float(len(ranked))),
                )
            )

    mean = total / len(values)
    facts.append(Fact(f"Mean {label} per {group}: {_fmt(mean)}.", (mean,)))
    return facts


def _spread_facts(
    columns: Sequence[FactColumn], rows: list[list[Any]], measure: FactColumn
) -> list[Fact]:
    """No dimension to break the measure down by — so describe the measure.

    These are individual observations rather than one row per group, and the
    only honest facts are about the distribution itself.
    """
    m = _index(columns, measure)
    if m is None:
        return []
    values = [value for value in (_number(row[m]) for row in rows) if value is not None]
    if len(values) < 2:
        return []

    label = _clean(measure.name)
    ordered = sorted(values)
    mean = sum(ordered) / len(ordered)
    median = _median(ordered)
    facts = [
        Fact(
            f"{label} across {len(ordered)} rows: lowest {_fmt(ordered[0])}, "
            f"highest {_fmt(ordered[-1])}.",
            (ordered[0], ordered[-1]),
        ),
        Fact(
            f"Mean {label}: {_fmt(mean)}. Median: {_fmt(median)}.",
            (mean, median),
        ),
    ]
    if _additive(measure):
        total = sum(ordered)
        facts.append(Fact(f"{label} summed over every row: {_fmt(total)}.", (total,)))
    return facts


def _single_row_facts(
    columns: Sequence[FactColumn], row: list[Any], measures: Sequence[FactColumn]
) -> list[Fact]:
    """One row: every figure it holds, named.

    A `METRIC` block already shows its headline through `plan_kpi`, but a single
    row often carries three or four numbers and the paragraph is written about
    all of them.
    """
    facts: list[Fact] = []
    for measure in measures[:MAX_FACTS]:
        index = _index(columns, measure)
        if index is None:
            continue
        value = _number(row[index])
        if value is None:
            continue
        facts.append(Fact(f"{_clean(measure.name)}: {_fmt(value)}.", (value,)))
    return facts


# ── the pieces ───────────────────────────────────────────────────────────
def _direction(values: Sequence[float]) -> Fact | None:
    """Rising, falling or flat — decided by halves, not by the two endpoints.

    Endpoints alone call a spike at the end "rising"; the halves compare the
    body of the series against itself, which is what a reader means by a trend.
    Needs six points before it will say anything: below that the two halves are
    two or three readings each and the word would be noise.
    """
    if len(values) < 6:
        return None
    half = len(values) // 2
    early = values[:half]
    late = values[len(values) - half :]
    first = sum(early) / len(early)
    second = sum(late) / len(late)
    pct = _pct_change(first, second)
    if pct is None:
        return None

    if abs(pct) < TREND_THRESHOLD_PCT:
        word = "broadly flat"
    elif pct > 0:
        word = "rising"
    else:
        word = "falling"
    return Fact(
        f"Direction over the whole series: {word} — the later half averages "
        f"{_fmt(second)} against {_fmt(first)} in the earlier half "
        f"({_signed_pct(pct)}).",
        (first, second, abs(pct)),
    )


def _pareto(ordered_desc: Sequence[float], total: float) -> int | None:
    """How many of the leading values it takes to reach `PARETO_SHARE`."""
    if total <= 0:
        return None
    running = 0.0
    for index, value in enumerate(ordered_desc, start=1):
        running += value
        if running / total >= PARETO_SHARE:
            return index
    return None


def _ordered(
    points: list[tuple[Any, float]],
) -> list[tuple[Any, float]]:
    """A series in period order, when the periods can be ordered at all.

    ISO dates and timestamps sort correctly as text, which is what the driver
    hands back through JSON, so this needs no date parsing. A column of mixed
    types is left in the order the query returned it rather than sorted by a
    comparison that would raise.
    """
    keys = [key for key, _ in points]
    if all(isinstance(key, str) for key in keys) or all(
        isinstance(key, (int, float)) and not isinstance(key, bool) for key in keys
    ):
        return sorted(points, key=lambda point: point[0])
    try:
        return sorted(points, key=lambda point: str(point[0]))
    except TypeError:  # pragma: no cover - defensive
        return points


def _index(columns: Sequence[FactColumn], wanted: FactColumn) -> int | None:
    return next(
        (i for i, column in enumerate(columns) if column.name == wanted.name), None
    )


def _number(value: Any) -> float | None:
    """A cell as a float, or nothing. Booleans are not measures."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _median(ordered: Sequence[float]) -> float:
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _pct_change(first: float, second: float) -> float | None:
    """Percent change, or nothing when the base makes it meaningless.

    A change from zero is infinite, and a change from a negative base has a sign
    that means the opposite of what a reader assumes.
    """
    if first == 0 or first < 0:
        return None
    change = (second - first) / first * 100.0
    return change if math.isfinite(change) else None


def _clean(name: str) -> str:
    """A column name as a phrase. `total_rev` is what a database calls it."""
    return name.replace("_", " ").strip() or "value"


def _label(value: Any) -> str:
    """A dimension value as it appears in the sentence, bounded."""
    if value is None:
        return "(not set)"
    text = str(value).strip()
    # A timestamp at midnight is a date, and every row of a monthly series
    # carrying " 00:00:00" makes the facts unreadable.
    if text.endswith(" 00:00:00") or text.endswith("T00:00:00"):
        text = text[:-9]
    return text[:60] if len(text) > 60 else text


def _fmt(value: float) -> str:
    """A number as a report writes it: grouped, and never to false precision."""
    if not math.isfinite(value):
        return "?"
    if float(value).is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _signed(value: float) -> str:
    return f"+{_fmt(value)}" if value > 0 else _fmt(value)


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _signed_pct(value: float) -> str:
    return f"+{value:.1f}%" if value > 0 else f"{value:.1f}%"
