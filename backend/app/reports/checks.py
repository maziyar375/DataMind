"""Tier 2 of §9: verify the figures the prose actually used.

Tier 1 is that the headline number is never model-written — `plan_kpi` computes
it from the rows. This module covers what the sentences around it say: every
numeral in the paragraph is extracted and looked for in the results that
paragraph was written from. Unmatched figures are recorded on the run and
marked in the UI.

Three properties, each of them deliberate:

* **It flags, it never blocks.** This is the posture `pipeline/checks.py` argues
  for at length — *"a finding is a suspicion, never a verdict"*. A check that
  refused to save a section over one unmatched figure would be worse than the
  hallucination it was guarding against, because the figures it gets wrong are
  the derived ones a good writer is *supposed* to produce.
* **It costs nothing.** No tokens, no model, no I/O — so it runs on every
  section of every run, and behaves identically whatever the provider is doing.
* **It reads both numeral systems.** «۱٫۲ میلیون» and "1.2M" are the same claim
  about the same number, and a check that understood only one of them would
  flag every figure in every Persian report and be turned off within a week.

What it deliberately does *not* do is prove a figure right. A number that
appears in the results may still be attached to the wrong noun, and no
token-free check can tell. Tier 3 — the model emitting `{{b2.revenue}}` and the
renderer substituting the value — is what makes that class impossible rather
than detected, and the findings this module produces are the evidence for
whether the prompt is reliable enough to try it.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, Field

#: Persian (U+06Fx) and Arabic-Indic (U+066x) digits to ASCII, the Arabic
#: decimal separator to a point and the Arabic thousands separator to a comma.
#: Every mapping is one character to one character, so the normalised text has
#: the *same length* as the original and a match's offsets still index into the
#: prose the user will read.
_NORMALISE = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬",
    "01234567890123456789.,",
)

_SCALES: dict[str, float] = {
    "k": 1e3, "thousand": 1e3, "هزار": 1e3,
    "m": 1e6, "mn": 1e6, "million": 1e6, "میلیون": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9, "میلیارد": 1e9,
}

# A number, an optional scale, an optional percent sign. Single-letter scales
# must be attached ("1.2M") because a bare "3 M" is far more likely to be the
# start of a word than three million; spelled-out ones may be spaced.
_FIGURE_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)"
    r"(?P<scale>[kKmMbB](?![\w])|\s*(?:thousand|million|billion|mn|bn)\b"
    r"|\s*(?:هزار|میلیون|میلیارد))?"
    r"(?P<percent>\s*(?:%|٪|درصد))?",
    re.IGNORECASE,
)

# Numbers inside a text cell — a date ("2026-05-01") carries the year and month
# a paragraph will name, and no numeric column holds them. Grouped digits are
# read both ways ("370,536" as 370536 *and* as 370 and 536), because a comma is
# a thousands separator in one locale and a list in another, and being wrong
# about which can only ever cost a match.
_EMBEDDED_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# A range: "90 to 94 thousand", «۹۰ تا ۹۴ هزار». The scale is written once and
# governs both ends, so the first figure inherits it — without this, the most
# natural sentence in any report ("between 90 and 94 thousand") flags its own
# first number every time. Found by reading a real generated paragraph.
_RANGE_RE = re.compile(
    r"(?P<first>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?:تا|to|through|and|و|[-–—])\s*"
    r"\d[\d,]*(?:\.\d+)?"
    r"(?P<scale>[kKmMbB](?![\w])|\s*(?:thousand|million|billion|mn|bn)\b"
    r"|\s*(?:هزار|میلیون|میلیارد))",
    re.IGNORECASE,
)

#: Anything past this is a runaway result, not a fact a paragraph can use.
MAX_POOL = 5_000

#: Numbers mined out of one text value. A date carries three; a paragraph — the
#: "cell" the executive summary is checked against — carries as many as it has
#: sentences, and capping *that* at a handful is how a summary quoting section
#: four gets flagged for it.
MAX_CELL_NUMBERS = 40

#: Pairs are only derived from a pool this small. Above it, N² ratios cover the
#: 0-100 range densely enough that every percentage would "match" and the check
#: would quietly stop finding anything.
MAX_DERIVED_POOL = 32

#: Relative slack, on top of the rounding tolerance the written precision
#: implies. Catching an invented figure is the job; arguing about the third
#: significant digit is not.
REL_TOLERANCE = 0.005

#: A paragraph with more unmatched figures than this has a problem no marker
#: per figure will convey.
MAX_FINDINGS = 12


class NumericFinding(BaseModel):
    """One figure in the prose that no result supports."""

    # As written, in the numerals the reader will see — so the UI can highlight
    # the exact substring rather than a normalised rendering of it.
    text: str
    value: float
    # `percentage` findings are the expected false positives: a rate the model
    # derived correctly from two values it was given. Kept apart so the UI can
    # mark them softly instead of crying wolf.
    kind: str = "figure"


class NumericCheck(BaseModel):
    """What the check looked at, and what it could not account for.

    Stored on `report_section_results.numeric_check`. NULL there means the check
    did not run; a row with `findings: []` means it ran and found nothing, which
    is a different and more useful statement.
    """

    checked: int = 0
    findings: list[NumericFinding] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def numeric_values(rows: Iterable[Sequence[Any]]) -> list[float]:
    """Every number the results hold, as a flat pool to match against.

    Strings are mined too, and on purpose: a month column of `2026-05-01`
    carries the year a paragraph will name, and a check that only read numeric
    cells would flag it every time.
    """
    pool: list[float] = []
    for row in rows:
        for value in row:
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                pool.append(float(value))
            else:
                # A string, a Decimal, a date, a UUID — whatever the driver
                # handed back. Its digits are what a paragraph could quote.
                pool.extend(_embedded(str(value)))
            if len(pool) >= MAX_POOL:
                return pool[:MAX_POOL]
    return pool


def figures_in(text: str) -> list[float]:
    """Every figure a paragraph *states*, with its scale word applied.

    The executive summary is checked against the sections' prose rather than
    against rows, so both sides have to be read by the same reader: a summary
    quoting «۹۴ هزار» from a section that wrote «۹۴ هزار» must match, and it
    cannot if one side sees 94,000 and the other sees 94.
    """
    normalised = (text or "").translate(_NORMALISE)
    inherited = {
        m.start("first"): m.group("scale") for m in _RANGE_RE.finditer(normalised)
    }
    figures: list[float] = []
    for match in _FIGURE_RE.finditer(normalised):
        parsed = _parse(match, inherited.get(match.start()))
        if parsed is not None:
            figures.append(parsed[0])
        if len(figures) >= MAX_POOL:
            break
    return figures


def check_prose(prose: str, values: Sequence[float], *, context: str = "") -> NumericCheck:
    """Every figure in the paragraph, looked for in the results behind it.

    `context` is the text the figure may legitimately have come from without
    being in any result — the heading, the section's intent, and the questions
    themselves. "The top 10 products" is not a claim about the data; it is the
    question repeated back.

    Never raises. A check that could fail a section over its own bug would be
    the most expensive line in the feature.
    """
    try:
        return _check(prose, values, context)
    except Exception:  # noqa: BLE001 — see the docstring; this must not throw
        return NumericCheck()


def _check(prose: str, values: Sequence[float], context: str) -> NumericCheck:
    normalised = (prose or "").translate(_NORMALISE)
    haystack = (context or "").translate(_NORMALISE)
    pool = [v for v in values if v == v]  # NaN never matches anything, including itself

    checked = 0
    findings: list[NumericFinding] = []
    seen: set[tuple[float, str]] = set()
    inherited = {
        m.start("first"): m.group("scale") for m in _RANGE_RE.finditer(normalised)
    }

    for match in _FIGURE_RE.finditer(normalised):
        parsed = _parse(match, inherited.get(match.start()))
        if parsed is None:
            continue
        value, unit, kind = parsed

        written = prose[match.start() : match.end()]
        if _asked_for(match.group("number"), haystack):
            # The question asked for the top 10; saying "the top 10" back is
            # not a figure the results have to support.
            continue

        checked += 1
        key = (round(value, 6), kind)
        if key in seen:
            continue
        if _matches(value, unit, pool):
            continue
        if kind == "percentage" and _derived(value, unit, pool):
            # A rate or a change the model computed correctly from two values
            # it was given. Expected, and not worth a marker.
            continue

        seen.add(key)
        findings.append(NumericFinding(text=written.strip(), value=value, kind=kind))
        if len(findings) >= MAX_FINDINGS:
            break

    return NumericCheck(checked=checked, findings=findings)


def _parse(
    match: re.Match[str], inherited_scale: str | None = None
) -> tuple[float, float, str] | None:
    """A match as its value, the size of its last written digit, and its kind.

    The unit is what makes rounding tolerance honest: "1.2M" claims a number to
    the nearest hundred thousand, so 1,234,567 satisfies it, while "1,234,567"
    claims one to the nearest unit and 1.2M does not.
    """
    try:
        number = match.group("number").replace(",", "")
        value = float(number)
    except ValueError:
        return None

    decimals = len(number.split(".")[1]) if "." in number else 0
    scale = 1.0
    raw_scale = (match.group("scale") or inherited_scale or "").strip().lower()
    if raw_scale:
        scale = _SCALES.get(raw_scale, 1.0)

    kind = "percentage" if match.group("percent") else "figure"
    return value * scale, (10.0**-decimals) * scale, kind


def _embedded(text: str) -> list[float]:
    """The numbers inside one text value, read both ways where it is ambiguous."""
    found: list[float] = []
    for token in _EMBEDDED_RE.findall(text.translate(_NORMALISE))[:MAX_CELL_NUMBERS]:
        cleaned = token.strip(",")
        if not cleaned or cleaned.startswith("."):
            continue
        found.append(float(cleaned.replace(",", "")))
        if "," in cleaned:
            found.extend(float(part) for part in cleaned.split(",") if part)
    return found


def _asked_for(number: str, haystack: str) -> bool:
    """Whether the question already carried this number.

    "The top 10 products" is the question repeated back, not a claim about the
    data — and flagging it would put a marker on the most ordinary sentence in
    the report. Bounded on both sides so `10` is not found inside `2010`.
    """
    return bool(
        haystack
        and re.search(rf"(?<!\d){re.escape(number)}(?!\d)", haystack)
    )


def _matches(value: float, unit: float, pool: Sequence[float]) -> bool:
    return any(_close(value, candidate, unit) for candidate in pool)


def _derived(value: float, unit: float, pool: Sequence[float]) -> bool:
    """Whether the figure is a share or a change of two values in the pool."""
    small = pool[:MAX_DERIVED_POOL]
    for a in small:
        for b in small:
            if b == 0:
                continue
            if _close(value, a / b * 100.0, unit) or _close(
                value, (a - b) / b * 100.0, unit
            ):
                return True
    return False


def _close(value: float, candidate: float, unit: float) -> bool:
    """Within one of the last written digit, or within a whisker either way.

    One unit rather than half: a figure written to a given precision may have
    been *truncated* to it as easily as rounded — "around 90 thousand" for
    90,944 is how people write, and half a unit would flag it. The cost is a
    factor of two in sensitivity, against hallucinations that are wrong by
    orders of magnitude or absent from the data entirely.
    """
    return abs(value - candidate) <= max(unit, abs(candidate) * REL_TOLERANCE)
