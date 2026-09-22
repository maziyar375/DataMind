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

## Claims — and why checking one against its own result is strictly stricter

`docs/plans/deep-analysis-mode.md` Phase 3. Everything above matches a figure
against **the union of every result in the section**, which is as much as a
paragraph with no internal structure allows. But a section carrying four
blocks has four pools merged into one, and a sentence about *last quarter's
revenue* quoting a number that only appears in the *headcount by team* result
passes that check — the figure is in the pool, and the pool cannot say which
result it came from.

So the prose is now emitted with a citation per sentence (`narrate.py` numbers
the results; the writer marks each sentence it draws from one), `parse_claims`
lifts those markers back out, and `check_claims` matches each claim's figures
against **its own cited result and nothing else**. The pool a claim is checked
against shrinks from four results to one, which is what makes a
cross-referenced figure visible.

Two failure modes are handled rather than assumed away, because the citation
comes from a model:

* **An uncited sentence** falls back to the union, exactly as before, and is
  recorded as `uncited`. A writer that ignores the instruction degrades this
  check to what it was; it never breaks it.
* **A citation to a result that does not exist** — `[7]` where there are three
  — is treated as uncited rather than dropped, because a fabricated citation
  is itself worth seeing and silently discarding it would hide it.
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


class Claim(BaseModel):
    """One sentence of a report, the figures it states, and the result it cites.

    The edge `docs/plans/deep-analysis-mode.md` §3.4 asks for — **claim → step
    → SQL → result** — with `cites` as the middle link. It is the ordinal of a
    result *within its section*, 1-based, as the prompt numbered it; `None`
    means the writer marked no result and this sentence is checked against the
    union, the way every sentence used to be.

    `supported` is the per-claim verdict: false when a figure in this sentence
    appears in no cited result. It is a **suspicion, never a verdict** — the
    same posture the module has always had, for the reason its docstring gives.
    """

    #: The sentence as the reader will see it, with the citation marker already
    #: removed. Stored rather than re-derived, because the prose is editable and
    #: a claim that pointed at an offset would be wrong the moment it was.
    text: str
    #: 1-based ordinal of the result this sentence was drawn from, as the
    #: prompt numbered them. `None` where the writer cited nothing.
    cites: int | None = None
    #: Every figure the sentence states, with scale words applied.
    figures: list[float] = Field(default_factory=list)
    #: Figures in this sentence that its cited result does not support.
    unsupported: list[NumericFinding] = Field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.unsupported

    @property
    def uncited(self) -> bool:
        return self.cites is None


class NumericCheck(BaseModel):
    """What the check looked at, and what it could not account for.

    Stored on `report_section_results.numeric_check`. NULL there means the check
    did not run; a row with `findings: []` means it ran and found nothing, which
    is a different and more useful statement.
    """

    checked: int = 0
    findings: list[NumericFinding] = Field(default_factory=list)
    #: The sentences this section was broken into, each with the result it
    #: cites. Empty on a run from before claims existed, and on a section whose
    #: writer emitted no citation at all — `uncited` below says which.
    claims: list[Claim] = Field(default_factory=list)
    #: How many claims carried no citation. Non-zero means the check fell back
    #: to the union for that many sentences, and is therefore weaker than it
    #: looks — a number the traceability metric needs as its denominator.
    uncited: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def traceable(self) -> float | None:
        """The share of claims stating a figure that resolve to one result.

        The metric `docs/plans/deep-analysis-mode.md` §12.9 scores, computed
        here so it means the same thing in a report and in the eval. `None`
        where no claim states a figure — a section of pure prose has nothing
        to trace, which is not the same as tracing nothing.
        """
        stating = [c for c in self.claims if c.figures]
        if not stating:
            return None
        resolved = [c for c in stating if c.cites is not None and c.supported]
        return len(resolved) / len(stating)


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


# ── claims: the sentence, its figures, and the result it cites ───────────
#: The citation a writer appends to a sentence: `[2]`, `[۲]`, or `[1,3]` where
#: one sentence genuinely draws on two results. Anchored to the end of the
#: sentence, before its full stop or after it, because that is where a writer
#: puts one and because a bracket mid-sentence is far more likely to be an
#: aside than a citation.
_CITE_RE = re.compile(r"\s*\[\s*(\d+(?:\s*[,،]\s*\d+)*)\s*\]\s*(?=[.!?؟]?\s*|$)")

#: Sentence boundaries, in both scripts. Deliberately simple: the alternative
#: is a sentence tokeniser, and a wrong split costs a claim boundary rather
#: than a figure — every figure is still checked, against a slightly wider or
#: narrower sentence.
_SENTENCE_RE = re.compile(r"(?<=[.!?؟])\s+|\n+")

#: Claims parsed from one section. A paragraph is 4–7 sentences by the house
#: style; this is the ceiling past which something has gone wrong with the
#: split rather than with the writing.
MAX_CLAIMS = 60


def parse_claims(prose: str, *, results: int) -> tuple[str, list[Claim]]:
    """Lift the citation markers out of the prose, into claims beside it.

    Returns the prose **as the reader will see it** — markers removed — and one
    `Claim` per sentence. Removing them here rather than in the renderer is the
    decision worth defending: the prose is editable, an edit will not preserve
    a marker, and a citation stored as an offset into text somebody may rewrite
    is a citation that will be wrong by next week. So the sentence travels with
    its claim and the stored prose is clean.

    `results` is how many results the section had. A citation past it is a
    fabricated one: kept as uncited rather than dropped, because a writer
    inventing a source is exactly the behaviour worth being able to see.

    Never raises — it runs on every section of every run, and a parse failure
    must cost citations rather than the paragraph.
    """
    try:
        return _parse_claims(prose, results)
    except Exception:  # noqa: BLE001 — see the docstring
        return prose, []


def _parse_claims(prose: str, results: int) -> tuple[str, list[Claim]]:
    if not (prose or "").strip():
        return prose, []

    claims: list[Claim] = []
    clean_parts: list[str] = []
    for sentence in _SENTENCE_RE.split(prose):
        if not sentence.strip():
            continue
        cited: list[int] = []

        def take(match: re.Match[str], into: list[int] = cited) -> str:
            into.extend(
                int(n) for n in re.split(r"[,،]", match.group(1)) if n.strip().isdigit()
            )
            return ""

        clean = _CITE_RE.sub(take, sentence).strip()
        if not clean:
            continue
        clean_parts.append(clean)
        # The first citation wins where a sentence names several: the check
        # needs one pool, and widening it to the union of two results is the
        # weakening this whole change exists to undo.
        first = next((n for n in cited if 1 <= n <= results), None)
        claims.append(
            Claim(text=clean, cites=first, figures=figures_in(clean))
        )
        if len(claims) >= MAX_CLAIMS:
            break

    return " ".join(clean_parts), claims


def check_claims(
    claims: Sequence[Claim],
    pools: Sequence[Sequence[float]],
    *,
    context: str = "",
) -> NumericCheck:
    """Each claim against **its own cited result**, and nothing else.

    `pools` is one pool per result, in the order the prompt numbered them, so
    `claims[i].cites` indexes into it. A claim citing nothing falls back to the
    union of every pool — which is exactly what `check_prose` has always done,
    so a writer that ignores the citation instruction leaves this check no
    weaker than it was.

    The union is built once rather than per claim: a section with eight results
    and seven sentences would otherwise concatenate eight lists seven times,
    and these pools run to thousands of values.

    Never raises, for `check_prose`'s reason.
    """
    try:
        return _check_claims(claims, pools, context)
    except Exception:  # noqa: BLE001
        return NumericCheck()


def _check_claims(
    claims: Sequence[Claim], pools: Sequence[Sequence[float]], context: str
) -> NumericCheck:
    union = [value for pool in pools for value in pool][:MAX_POOL]

    checked = 0
    findings: list[NumericFinding] = []
    uncited = 0
    settled: list[Claim] = []

    for claim in claims:
        if claim.cites is None:
            uncited += 1
            pool: Sequence[float] = union
        else:
            at = claim.cites - 1
            pool = pools[at] if 0 <= at < len(pools) else union

        one = _check(claim.text, pool, context)
        checked += one.checked
        settled.append(
            claim.model_copy(update={"unsupported": list(one.findings)})
        )
        for finding in one.findings:
            # De-duplicated across claims, so a figure a writer repeated in two
            # sentences is one marker rather than two — the same rule `_check`
            # already applies within a sentence.
            if not any(
                f.value == finding.value and f.kind == finding.kind for f in findings
            ):
                findings.append(finding)
            if len(findings) >= MAX_FINDINGS:
                break
        if len(findings) >= MAX_FINDINGS:
            break

    return NumericCheck(
        checked=checked,
        findings=findings[:MAX_FINDINGS],
        claims=settled,
        uncited=uncited,
    )
