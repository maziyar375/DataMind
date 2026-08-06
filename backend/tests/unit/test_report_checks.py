"""Tier 2: does the paragraph's arithmetic come from the database?

Every case here is a literal and a list of floats — no model, no database, no
tokens. That is the point of the check: it runs on every section of every run
and behaves identically whatever the provider is doing.

The two claims worth stating out loud:

* **It reads both numeral systems.** «۱٫۲ میلیون» and "1.2M" are the same claim
  about the same number. A check that understood only the second would flag
  every figure in every Persian report and be switched off within a week — and
  a switched-off check is worse than none, because it looks like coverage.
* **It flags, it never blocks.** There is no path here that raises, and no
  return value a caller could read as "refuse to save". The findings are a
  suspicion for a human to judge, exactly as `pipeline/checks.py` argues.
"""
from __future__ import annotations

import pytest

from app.reports.checks import MAX_FINDINGS, check_prose, figures_in, numeric_values

# One month of a revenue trend, as a section would hold it.
REVENUE = [1_234_567.0, 987_654.0, 1_100_000.0]


# ── a figure that is in the data is not flagged ──────────────────────────
def test_a_figure_lifted_from_a_row_is_matched() -> None:
    check = check_prose("Revenue reached 1,234,567 in May.", REVENUE)

    assert check.ok
    assert check.checked == 1


def test_a_scaled_figure_is_the_same_claim() -> None:
    """"1.2M" claims a number to the nearest hundred thousand, and 1,234,567
    satisfies that claim. Reading it as 1.2 would flag every rounded figure a
    good writer produces."""
    check = check_prose("Revenue reached 1.2M in May.", REVENUE)

    assert check.ok


@pytest.mark.parametrize(
    "prose",
    [
        "درآمد به ۱٫۲ میلیون رسید.",
        "درآمد به ۱٬۲۳۴٬۵۶۷ رسید.",
        "درآمد به 1.2 میلیون رسید.",
    ],
)
def test_persian_numerals_and_scale_words_are_read(prose: str) -> None:
    """The Persian decimal separator, the Persian thousands separator, Persian
    digits, and «میلیون» — a Persian report is the *first* case here, not an
    afterthought."""
    check = check_prose(prose, REVENUE)

    assert check.ok, check.findings


def test_a_rounded_figure_is_matched_at_the_precision_it_was_written() -> None:
    check = check_prose("The average order was 1,235.", [1234.6])

    assert check.ok


def test_a_year_from_a_date_column_is_matched() -> None:
    """No numeric column holds 2026; the month column holds `2026-05-01`, and
    "in May 2026" is the most natural sentence to write from it."""
    pool = numeric_values([["2026-05-01", 120.0]])

    assert check_prose("Revenue peaked in May 2026 at 120.", pool).ok


def test_the_row_count_is_a_figure_no_cell_holds() -> None:
    """"across 13 months" comes from the count the model was told, not from a
    value — so the caller puts it in the pool, and this proves it works."""
    check = check_prose("Revenue rose across all 13 months.", [*REVENUE, 13.0])

    assert check.ok


# ── a figure that is not is flagged ──────────────────────────────────────
def test_a_hallucinated_figure_is_flagged() -> None:
    check = check_prose("Revenue reached 9,900,000 in May.", REVENUE)

    assert not check.ok
    assert check.checked == 1
    finding = check.findings[0]
    assert finding.value == 9_900_000
    # As written, in the numerals the reader sees — so the UI can highlight the
    # exact substring rather than a normalised rendering of it.
    assert finding.text == "9,900,000"
    assert finding.kind == "figure"


def test_a_hallucinated_persian_figure_is_flagged_too() -> None:
    check = check_prose("درآمد به ۹٫۹ میلیون رسید.", REVENUE)

    assert [f.value for f in check.findings] == [9_900_000]
    assert check.findings[0].text == "۹٫۹ میلیون"


def test_one_good_figure_does_not_excuse_a_bad_one() -> None:
    check = check_prose(
        "Revenue reached 1,234,567 in May, up from 500,000 in April.", REVENUE
    )

    assert [f.value for f in check.findings] == [500_000]


def test_findings_are_capped() -> None:
    """A paragraph with twenty unmatched figures has a problem no marker per
    figure will convey."""
    prose = " ".join(f"{n}00001" for n in range(1, 30))

    assert len(check_prose(prose, REVENUE).findings) == MAX_FINDINGS


# ── found by reading a real generated report ─────────────────────────────
# Both of these came out of the first Persian document this feature produced
# against the sales fixture, not out of imagining what a model might write.
PAID = [370_536.0, 93_845.76, 93_835.77, 91_909.94, 90_944.53]


def test_a_range_written_with_one_scale_word_applies_it_to_both_ends() -> None:
    """«۹۰ تا ۹۴ هزار» — "between 90 and 94 thousand".

    The scale is written once and governs both numbers. Reading the first as a
    bare 90 flagged the most natural sentence in the report.
    """
    check = check_prose("هرکدام حدود ۹۰ تا ۹۴ هزار واحد درآمد داشته‌اند.", PAID)

    assert check.ok, check.findings


@pytest.mark.parametrize(
    "prose",
    [
        "Each brought in between 90 and 94 thousand.",
        "Each brought in 90 to 94 thousand.",
        "Each brought in 90-94 thousand.",
    ],
)
def test_the_same_range_in_english(prose: str) -> None:
    assert check_prose(prose, PAID).ok


def test_a_range_does_not_excuse_a_number_the_scale_cannot_reach() -> None:
    """The inheritance is a reading of the sentence, not an amnesty."""
    check = check_prose("Each brought in 40 to 94 thousand.", PAID)

    assert [f.value for f in check.findings] == [40_000]


def test_a_summary_quoting_a_section_verbatim_is_never_flagged() -> None:
    """The summary is checked against the sections' *prose*, so both sides have
    to be read by the same reader.

    Mining the prose for bare digits saw «۹۴ هزار» as 94 and «۳۷۰,۵۳۶» as 370
    and 536 — so a summary that copied the section word for word was flagged
    for every figure in it. Both sentences below are the real ones.
    """
    section = (
        "بخش عمده درآمد از سفارش‌های تکمیل‌شده با ۳۷۰,۵۳۶ واحد به دست آمده و "
        "بقیه وضعیت‌ها هر کدام بین ۹۰ تا ۹۴ هزار واحد قرار دارند."
    )
    summary = (
        "درآمد اصلی از سفارش‌های تکمیل‌شده با ۳۷۰,۵۳۶ واحد حاصل شده است، در "
        "حالی که سایر وضعیت‌ها ارقامی بین ۹۰ تا ۹۴ هزار واحد داشته‌اند."
    )

    check = check_prose(summary, figures_in(section))

    assert check.ok, check.findings
    assert check.checked == 3


def test_a_summary_figure_no_section_wrote_is_still_flagged() -> None:
    """The symmetry must not become an amnesty: the summary may only quote."""
    check = check_prose("درآمد ۸۸۸ بود.", figures_in("درآمد ۳۷۰,۵۳۶ بود."))

    assert [f.value for f in check.findings] == [888]


def test_a_grouped_number_in_a_text_cell_is_read_both_ways() -> None:
    """A comma is a thousands separator in one locale and a list in another;
    being wrong about which can only ever cost a match."""
    pool = numeric_values([["370,536"]])

    assert 370_536.0 in pool
    assert 370.0 in pool and 536.0 in pool


def test_a_summary_is_checked_against_every_figure_the_sections_wrote() -> None:
    """The executive summary's pool is the sections' prose — one long "cell".

    Capping numbers-per-cell at a handful (right for a date column) silently
    truncated that pool, so a summary quoting the *fifth* figure of a section
    was flagged for quoting it correctly.
    """
    body = " ".join(f"figure {n}: {n * 1000}" for n in range(1, 12))

    check = check_prose("The last one was 11000.", numeric_values([[body]]))

    assert check.ok, check.findings


# ── the expected false positives ─────────────────────────────────────────
def test_a_rate_derived_from_two_values_is_not_flagged() -> None:
    """The plan calls these out by name: a percentage the model computed
    correctly from two values it was given. Flagging them is how a marker
    becomes noise and stops being read."""
    check = check_prose("Paid orders were 75% of the total.", [75.0, 100.0])

    assert check.ok


def test_a_change_between_two_values_is_not_flagged() -> None:
    check = check_prose("Revenue grew 25%.", [125.0, 100.0])

    assert check.ok


def test_a_percentage_that_is_nowhere_is_still_flagged_but_marked_as_one() -> None:
    """Kept apart from an invented absolute figure: the UI marks a suspect rate
    softly and a suspect total loudly, because one of them is routinely a false
    positive and the other is not."""
    check = check_prose("Revenue grew 61%.", [125.0, 100.0])

    assert [f.kind for f in check.findings] == ["percentage"]


def test_a_number_the_question_asked_for_is_not_a_claim_about_the_data() -> None:
    check = check_prose(
        "The top 10 products carried the quarter.",
        REVENUE,
        context="top 10 products by revenue",
    )

    assert check.ok
    # Not merely unflagged — never examined, because it is not a figure.
    assert check.checked == 0


def test_a_number_inside_another_number_does_not_count_as_asked_for() -> None:
    check = check_prose("Revenue was 10 in May.", [], context="the 2010 cohort")

    assert [f.value for f in check.findings] == [10]


# ── it never blocks, and never raises ────────────────────────────────────
@pytest.mark.parametrize(
    "prose", ["", "   ", "No figures at all.", "١٢٣٤٥", "12.", "..", "%"]
)
def test_junk_is_never_an_exception(prose: str) -> None:
    check = check_prose(prose, REVENUE)

    assert isinstance(check.checked, int)


def test_an_empty_pool_flags_rather_than_crashes() -> None:
    """A section whose every block failed still gets a paragraph in some
    circumstances; the check reports on it instead of dying."""
    check = check_prose("Revenue reached 1.2M.", [])

    assert len(check.findings) == 1


def test_the_check_is_a_record_not_a_verdict() -> None:
    """Nothing here returns a refusal, and `checked` is recorded even when
    everything matched — "the check ran and found nothing" is a more useful
    statement than silence."""
    check = check_prose("Revenue reached 1,234,567 across 3 months.", [*REVENUE, 3.0])

    assert check.checked == 2
    assert check.findings == []
    assert check.model_dump(mode="json") == {"checked": 2, "findings": []}


# ── the pool ─────────────────────────────────────────────────────────────
def test_booleans_and_nulls_are_not_numbers() -> None:
    """`True` is 1 in Python and is not a figure a paragraph can quote."""
    assert numeric_values([[True, None, 5]]) == [5.0]


def test_numbers_are_mined_out_of_text_cells() -> None:
    assert 2026.0 in numeric_values([["2026-05-01"]])
