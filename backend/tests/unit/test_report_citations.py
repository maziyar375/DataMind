"""A number in the prose resolves to the query that produced it.

`docs/plans/deep-analysis-mode.md` Phase 3. Prose that merely *mentions* a
number is not a citation, and until r5 that is all a report had: the numeric
check matched every figure in a section against **the union of every result in
that section**, because a paragraph with no internal structure gives it nothing
finer to match against.

The consequence is the test this file is named for. A section with four results
merges four pools into one, so a sentence about last quarter's revenue quoting
a figure that appears only in the headcount result **passes** — the number is
in the pool, and the pool cannot say which result it came from. That is not a
hypothetical failure; it is the ordinary shape of a multi-block section.

r5 numbers the results and asks the writer to mark each sentence with the one
it drew from. `parse_claims` lifts the markers out before a reader ever sees
them, and `check_claims` matches each sentence against **its own** result. The
pool shrinks from four results to one, which is the whole of the improvement
and the whole of the risk: **this check will now fail sections that pass
today**, and some of those failures are the check being right.

The other half of this file is the degradation path, because the citation comes
from a model and a model may ignore the instruction. An uncited sentence falls
back to the union — exactly what it did before — and is counted, so a section
whose writer cited nothing reads as *weakly checked* rather than as *clean*.

No provider, no database. Strings and lists in, dataclasses out.
"""

from __future__ import annotations

from app.reports import checks

#: Two results under one heading, which is the ordinary shape: block 1 is the
#: money, block 2 is the people. The figures are chosen so no value appears in
#: both — that separation is what makes a cross-reference detectable at all.
REVENUE_POOL = [1_200_000.0, 980_000.0, 13.0]
HEADCOUNT_POOL = [47.0, 52.0]
POOLS = [REVENUE_POOL, HEADCOUNT_POOL]


def _claims(prose: str, *, results: int = 2) -> tuple[str, list[checks.Claim]]:
    return checks.parse_claims(prose, results=results)


# ── the marker never reaches the reader ──────────────────────────────────
def test_the_citation_markers_are_stripped_out_of_the_stored_prose() -> None:
    """A reader is shown a paragraph, not a bibliography.

    The markers are removed here rather than in the renderer for a reason that
    outlives the renderer: the prose is editable, an edit will not preserve a
    marker, and a citation stored as an offset into text somebody may rewrite
    is wrong by next week. So the sentence travels inside its claim and the
    stored prose is clean.
    """
    clean, claims = _claims(
        "Revenue reached 1,200,000 in March [1]. Headcount stood at 47 [2]."
    )

    assert "[1]" not in clean and "[2]" not in clean
    assert clean == "Revenue reached 1,200,000 in March. Headcount stood at 47."
    assert [c.cites for c in claims] == [1, 2]
    assert [c.text for c in claims] == [
        "Revenue reached 1,200,000 in March.",
        "Headcount stood at 47.",
    ]


def test_a_sentence_with_no_figure_needs_no_citation() -> None:
    _clean, claims = _claims("Revenue reached 1,200,000 [1]. The outlook is mixed.")

    assert [c.cites for c in claims] == [1, None]
    assert claims[1].figures == []


# ── the case the phase exists for ────────────────────────────────────────
def test_a_figure_from_another_result_is_flagged_where_today_it_passes() -> None:
    """The headline. Both halves are asserted, because "stricter" is a claim
    about the difference and not about the new behaviour alone."""
    prose = "Revenue reached 47 in March [1]."
    _clean, claims = _claims(prose)

    strict = checks.check_claims(claims, POOLS)
    lenient = checks.check_prose(
        "Revenue reached 47 in March.",
        [value for pool in POOLS for value in pool],
    )

    # Today: 47 is in the union, so nothing is flagged.
    assert lenient.findings == []
    # Now: 47 is not in the result the sentence cites.
    assert [f.value for f in strict.findings] == [47.0]
    assert strict.claims[0].unsupported
    assert not strict.claims[0].supported


def test_a_figure_from_its_own_result_still_passes() -> None:
    """The other side of the same edge: stricter must not mean noisier."""
    _clean, claims = _claims(
        "Revenue reached 1,200,000 in March [1]. Headcount stood at 47 [2]."
    )

    check = checks.check_claims(claims, POOLS)

    assert check.findings == []
    assert all(c.supported for c in check.claims)
    assert check.traceable == 1.0


# ── the degradation path, because a model writes the citations ───────────
def test_an_uncited_sentence_falls_back_to_the_union_and_is_counted() -> None:
    """A writer that ignores the instruction leaves this check exactly as
    strong as it was before r5 — never weaker, and never broken."""
    _clean, claims = _claims("Revenue reached 47 in March.")

    check = checks.check_claims(claims, POOLS)

    assert check.findings == [], "the union still supports 47"
    assert check.uncited == 1
    assert check.claims[0].uncited


def test_a_citation_to_a_result_that_does_not_exist_is_kept_as_uncited() -> None:
    """Dropping it would hide the interesting part. A writer inventing a source
    is behaviour worth being able to see, and the sentence still has to be
    checked against something."""
    _clean, claims = _claims("Revenue reached 47 in March [7].", results=2)

    assert claims[0].cites is None
    assert checks.check_claims(claims, POOLS).uncited == 1


def test_a_section_nobody_cited_is_weakly_checked_rather_than_clean() -> None:
    """The number a reader needs before trusting a clean check: how many of the
    sentences were checked against one result and how many against all of
    them."""
    _clean, claims = _claims("Revenue reached 1,200,000. Headcount stood at 47. Both rose.")

    check = checks.check_claims(claims, POOLS)

    assert check.uncited == 3
    assert check.traceable == 0.0, "no figure resolves to one result"


# ── traceability, the metric Phase 7 scores ──────────────────────────────
def test_traceability_counts_only_the_sentences_that_state_a_figure() -> None:
    """A section of pure prose has nothing to trace, which is not the same as
    tracing nothing — so the metric is `None` rather than 0.0."""
    _clean, claims = _claims("The picture is mixed. It bears watching.")

    assert checks.check_claims(claims, POOLS).traceable is None


def test_traceability_is_the_share_that_resolves_to_one_result() -> None:
    _clean, claims = _claims(
        "Revenue reached 1,200,000 [1]. Headcount stood at 47. Margins held."
    )

    check = checks.check_claims(claims, POOLS)

    # Two sentences state a figure; one of them cites its result and is
    # supported by it. The third states none and is not in the denominator.
    assert check.traceable == 0.5


# ── both numeral systems, because the check has always read both ─────────
def test_a_citation_survives_persian_prose_and_persian_numerals() -> None:
    """The figure extraction has read both scripts since it was written, and a
    citation that only worked in one of them would flag every figure in every
    Persian report."""
    clean, claims = _claims("درآمد به ۱٬۲۰۰٬۰۰۰ رسید [۱]. تعداد کارکنان ۴۷ بود [۲].")

    assert "[" not in clean
    assert [c.cites for c in claims] == [1, 2]
    assert checks.check_claims(claims, POOLS).findings == []


# ── the posture, unchanged ───────────────────────────────────────────────
def test_neither_parsing_nor_checking_ever_raises() -> None:
    """It runs on every section of every run. A parse failure must cost
    citations, never the paragraph — the same rule `check_prose` has always
    followed, for the reason its docstring gives."""
    assert checks.parse_claims("", results=0) == ("", [])
    assert checks.parse_claims("[1]", results=1)[1] == []
    assert checks.check_claims([], [], context="").findings == []
    assert checks.check_claims(
        [checks.Claim(text="x 5", cites=99, figures=[5.0])], POOLS
    ).claims


def test_a_claim_is_capped_so_a_runaway_split_cannot_grow_without_bound() -> None:
    prose = " ".join(f"Sentence {i} has 47 in it [2]." for i in range(200))

    _clean, claims = _claims(prose)

    assert len(claims) == checks.MAX_CLAIMS
