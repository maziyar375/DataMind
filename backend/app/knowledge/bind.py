"""Filling a template's slots from the question. Deterministic, no model call.

**Any parameter that cannot be bound cancels the short-circuit.** That rule is
the single most important line in this module, and it is why the grammar below
is deliberately small: a half-bound template is a confident wrong answer, which
is the failure class this product exists to avoid. The cost of refusing to bind
is today's behaviour — the run generates SQL, as it always did. The cost of
binding badly is an answer that looks verified and is not.

Three kinds of slot, three readings:

* **date / datetime** — a small grammar over the phrases people actually type
  (*"last month"*, *"in July"*, *"2026"*, *"Q3"*, *"last 12 months"*, an ISO
  date), resolved against the run's clock, never against the machine's.
  `from_date` takes the range's start and `to_date` its end, so one phrase in
  the question fills a pair of slots — which is what the AST proposed them as.
* **string** — a value the parameter's own comment lists (`one of: EMEA, NA,
  APAC`), matched case-insensitively as a whole word. A parameter with no list
  binds only from an explicitly quoted value, because guessing which noun in a
  sentence is the region is exactly the guess that produces a wrong answer.
* **number** — a numeral in the question. Ambiguity is refused: two numerals
  and one numeric slot is a coin toss.

The unbound cases are *logged* (`REJECTED_UNBOUND` on `knowledge_template_hits`)
rather than swallowed, because that log is how we learn which grammars are
worth adding. Guessing now to avoid a log line later is the wrong trade.

**Substitution is on the tree.** `bind_sql` replaces each placeholder node with
a literal and re-renders; there is no string formatting anywhere in this file,
so there is no rendering in which a bound *value* can become SQL. The result
still goes through `guard()` before it reaches a driver, like everything else.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from app.knowledge.models import ParamType, TemplateParam

#: Slot names that mean "the start" and "the end" of one range. The AST walk
#: proposes exactly these for a date comparison or a `BETWEEN`, so the binder
#: and the proposer agree on the vocabulary by construction.
START_NAMES = ("from_date", "start_date", "since")
END_NAMES = ("to_date", "end_date", "until")

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_YEAR = re.compile(r"\b(19|20)(\d{2})\b")
_QUARTER = re.compile(r"\bq([1-4])\b", re.IGNORECASE)
_LAST_N = re.compile(
    r"\b(?:last|past|previous)\s+(\d+)\s+(day|week|month|quarter|year)s?\b",
    re.IGNORECASE,
)
_NUMERAL = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w.])")


@dataclass(frozen=True, slots=True)
class DateRange:
    """A half-open window: `start <= x < end`.

    Half-open on purpose. A closed range needs "the last instant of the month",
    which is a different value on a `date` column and a `timestamp` one, and
    getting it wrong silently drops or double-counts a day's rows.
    """

    start: date
    end: date


@dataclass(slots=True)
class Binding:
    """The result of trying to fill every slot."""

    values: dict[str, Any] = field(default_factory=dict)
    #: Slots the question did not supply. **Non-empty cancels the hit.**
    missing: list[str] = field(default_factory=list)
    #: The phrase the date grammar recognised, for the trace and the badge.
    window: str = ""

    @property
    def bound(self) -> bool:
        return not self.missing


def bind_params(
    question: str, params: list[TemplateParam], *, now: datetime
) -> Binding:
    """Fill every declared slot from the question, or say which ones failed.

    `now` is the run's clock, passed in rather than read, because "last month"
    asked at 23:59 on the 31st must not resolve differently depending on how
    long the queue was.
    """
    binding = Binding()
    if not params:
        return binding

    text = question or ""
    window, phrase = _date_range(text, now)
    binding.window = phrase

    for param in params:
        value = _bind_one(param, text, window, now)
        if value is None:
            binding.missing.append(param.name)
        else:
            binding.values[param.name] = value
    return binding


def _bind_one(
    param: TemplateParam, text: str, window: DateRange | None, now: datetime
) -> Any | None:
    if param.type.is_temporal:
        return _bind_temporal(param, window)
    if param.type is ParamType.NUMBER:
        return _bind_number(text)
    if param.type is ParamType.BOOLEAN:
        return _bind_boolean(text)
    return _bind_string(param, text)


def _bind_temporal(param: TemplateParam, window: DateRange | None) -> Any | None:
    if window is None:
        return None
    lowered = param.name.lower()
    if any(lowered.endswith(name) or lowered == name for name in END_NAMES):
        return window.end
    return window.start


def _bind_number(text: str) -> Any | None:
    """One numeral, or nothing.

    Two numerals and one numeric slot is a coin toss, and a coin toss inside a
    Verified badge is worse than no badge.
    """
    found = _NUMERAL.findall(_strip_dates(text))
    if len(found) != 1:
        return None
    raw = found[0].replace(",", "")
    return float(raw) if "." in raw else int(raw)


def _bind_boolean(text: str) -> Any | None:
    lowered = f" {text.lower()} "
    yes = any(w in lowered for w in (" yes ", " true ", " only "))
    no = any(w in lowered for w in (" no ", " false ", " not ", " without "))
    if yes == no:
        return None
    return yes


def _bind_string(param: TemplateParam, text: str) -> Any | None:
    """A value the parameter's own comment lists, or an explicitly quoted one.

    Nothing else. A parameter with no declared vocabulary cannot be filled by
    picking a noun out of a sentence — that guess is the one that produces a
    confident wrong answer, and the `REJECTED_UNBOUND` log is how we find out
    that this template needed a value list.
    """
    for value in param.values():
        if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text, re.IGNORECASE):
            return value

    quoted = re.findall(r"['‘“\"]([^'’”\"]{1,64})['’”\"]", text)
    return quoted[0] if len(quoted) == 1 else None


def _strip_dates(text: str) -> str:
    """Remove what the date grammar already claimed.

    Without this, *"revenue over 10000 in 2026"* offers the number binder two
    numerals and it refuses — when the question is perfectly unambiguous to a
    reader, because the year belongs to the date slot.
    """
    return _YEAR.sub(" ", _ISO_DATE.sub(" ", text))


# ── the date grammar ─────────────────────────────────────────────────────
def _date_range(text: str, now: datetime) -> tuple[DateRange | None, str]:
    """The one window a question describes, and the phrase that said so.

    Ordered from most specific to least, so *"last 3 months"* is not read as
    the bare numeral 3 and *"2026-01-01"* is not read as the year 2026.
    """
    today = now.date()
    lowered = (text or "").lower()

    if (found := _LAST_N.search(lowered)) is not None:
        count, unit = int(found.group(1)), found.group(2)
        return _back(today, count, unit), found.group(0)

    iso = _ISO_DATE.findall(text or "")
    if len(iso) >= 2:
        start, end = _as_date(iso[0]), _as_date(iso[1])
        return DateRange(start, end), f"{start} to {end}"
    if len(iso) == 1:
        start = _as_date(iso[0])
        return DateRange(start, today + timedelta(days=1)), str(start)

    for phrase, days in (("yesterday", 1), ("today", 0)):
        if phrase in lowered:
            start = today - timedelta(days=days)
            return DateRange(start, start + timedelta(days=1)), phrase

    if "last month" in lowered or "previous month" in lowered:
        first = today.replace(day=1)
        return DateRange(_add_months(first, -1), first), "last month"
    if "this month" in lowered:
        first = today.replace(day=1)
        return DateRange(first, _add_months(first, 1)), "this month"
    if "last week" in lowered:
        monday = today - timedelta(days=today.weekday())
        return DateRange(monday - timedelta(days=7), monday), "last week"
    if "this week" in lowered:
        monday = today - timedelta(days=today.weekday())
        return DateRange(monday, monday + timedelta(days=7)), "this week"
    if "last year" in lowered:
        return (
            DateRange(date(today.year - 1, 1, 1), date(today.year, 1, 1)),
            "last year",
        )
    if "this year" in lowered:
        return (
            DateRange(date(today.year, 1, 1), date(today.year + 1, 1, 1)),
            "this year",
        )

    # A quarter or a month name, optionally with a year beside it. "Q3" with no
    # year means this year's Q3, which is what a person asking in Q4 means.
    year = _year_in(lowered) or today.year
    if (quarter := _QUARTER.search(lowered)) is not None:
        q = int(quarter.group(1))
        start = date(year, 3 * (q - 1) + 1, 1)
        return DateRange(start, _add_months(start, 3)), quarter.group(0).upper()
    for name, number in _MONTHS.items():
        if re.search(rf"(?<!\w){name}(?!\w)", lowered):
            start = date(year, number, 1)
            return DateRange(start, _add_months(start, 1)), name

    if (found_year := _year_in(lowered)) is not None:
        return (
            DateRange(date(found_year, 1, 1), date(found_year + 1, 1, 1)),
            str(found_year),
        )
    return None, ""


def _year_in(lowered: str) -> int | None:
    found = _YEAR.search(lowered)
    return int(found.group(0)) if found else None


def _as_date(parts: tuple[str, str, str]) -> date:
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def _back(today: date, count: int, unit: str) -> DateRange:
    end = today + timedelta(days=1)
    if unit == "day":
        return DateRange(today - timedelta(days=count - 1), end)
    if unit == "week":
        return DateRange(today - timedelta(weeks=count), end)
    if unit == "quarter":
        return DateRange(_add_months(today, -3 * count), end)
    if unit == "year":
        return DateRange(_add_months(today, -12 * count), end)
    return DateRange(_add_months(today, -count), end)


def _add_months(value: date, months: int) -> date:
    total = value.month - 1 + months
    year = value.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


# ── substitution ─────────────────────────────────────────────────────────
def bind_sql(sql: str, values: dict[str, Any], *, dialect: str = "postgres") -> str | None:
    """The template's SQL with every slot replaced by a literal, on the tree.

    Returns None when the statement will not parse or a slot has no value —
    both of which cancel the short-circuit rather than producing a statement
    with a hole in it.

    There is no string formatting here, so a bound value can never become SQL:
    a date becomes an `exp.Literal` and renders quoted, whatever it contains.
    The result is still handed to `guard()` before anything runs it.
    """
    try:
        tree = sqlglot.parse_one(sql or "", read=dialect)
    except Exception:
        return None

    for node in list(tree.walk(bfs=False)):
        name = _slot_name(node)
        if name is None:
            continue
        if name not in values:
            return None
        node.replace(_literal(values[name]))
    return tree.sql(dialect=dialect)


def _slot_name(node: exp.Expression) -> str | None:
    """`:region`, however this statement happens to spell it.

    A stored template holds `exp.Var(":region")` as written and re-parses as
    `exp.Placeholder`; both are read, so a template saved before or after any
    round trip binds identically.
    """
    if isinstance(node, exp.Placeholder) and node.this:
        return str(node.this)
    if isinstance(node, exp.Var) and str(node.this or "").startswith(":"):
        return str(node.this)[1:]
    return None


def _literal(value: Any) -> exp.Expression:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))
