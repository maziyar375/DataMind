"""The match key. Two askings of one question must produce one key.

`question_normalized` carries the store's unique constraint, the lexical
matcher's GIN index and (from Phase 2) every short-circuit decision. It is one
pure function, so this file is where its whole contract lives.

The two claims that matter, and they pull in opposite directions:

* **questions differing only in literals normalise to the same key** — that is
  what makes a template a *family* rather than a string;
* **questions differing in intent do not** — because the cost of confusing
  "revenue by month" with "revenue by store" is a confident wrong answer, which
  is the failure class this product exists to avoid.
"""
from __future__ import annotations

import pytest

from app.knowledge import MASK, example_questions, normalize_question, slots

PATTERN = "revenue by month for {region} in {year}"


# ── same question, different literals ────────────────────────────────────
@pytest.mark.parametrize(
    "a,b",
    [
        ("top 10 stores by revenue", "top 25 stores by revenue"),
        ("orders in 2026", "orders in 2025"),
        ("revenue since 2026-01-01", "revenue since 2024-07-19"),
        ("sales over 1,000.50", "sales over 42"),
        ("revenue for 'EMEA'", 'revenue for "NA"'),
        # A trailing question mark, stray punctuation and case are not intent.
        ("Revenue by month", "revenue by month?"),
        ("revenue  by   month", "revenue by month"),
    ],
)
def test_literal_differences_collapse_to_one_key(a: str, b: str) -> None:
    assert normalize_question(a) == normalize_question(b)


def test_a_pattern_and_a_filled_in_question_share_their_shape() -> None:
    # Not identical — `EMEA` is not detectably a literal — but the year is
    # masked on both sides, which is what makes the trigram score high enough
    # for Phase 2 to consider them at all.
    assert normalize_question(PATTERN) == f"revenue by month for {MASK} in {MASK}"
    filled = normalize_question("Revenue by month for EMEA in 2026")
    assert filled == f"revenue by month for emea in {MASK}"


# ── different questions, different keys ──────────────────────────────────
@pytest.mark.parametrize(
    "a,b",
    [
        ("revenue by month", "revenue by store"),
        ("revenue by month", "orders by month"),
        ("top stores by revenue", "worst stores by revenue"),
        # A stopword carries the intent here, which is exactly why nothing is
        # stripped: "revenue by month" and "revenue per month" are the same
        # question and "revenue by month" and "revenue in month" are not — a
        # distinction no stopword list gets right.
        ("revenue before 2026", "revenue after 2026"),
    ],
)
def test_different_intents_keep_different_keys(a: str, b: str) -> None:
    assert normalize_question(a) != normalize_question(b)


def test_a_number_inside_a_word_is_part_of_the_question() -> None:
    # `q3` and `p90` are words in the question, not values in it. Masking them
    # would make "revenue in q3" and "revenue in q4" the same question.
    assert normalize_question("revenue in q3") != normalize_question("revenue in q4")
    assert "q3" in normalize_question("revenue in Q3")


def test_two_adjacent_slots_are_one_mask_not_two() -> None:
    # `{from} {to}` and `{range}` describe the same shape; two masks with only
    # space between them would score as a longer question than it is.
    assert normalize_question("orders {from} {to}") == f"orders {MASK}"


# ── totality ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", ["", "   ", "???", "{}"])
def test_an_empty_question_normalises_rather_than_raising(value: str) -> None:
    # The editor calls this on every keystroke, and half a question is the
    # normal case.
    assert normalize_question(value) in ("", MASK)


def test_a_persian_question_keeps_its_letters() -> None:
    # casefold is a no-op on a script without case; the digits still mask, in
    # both the Persian and the Latin forms.
    assert normalize_question("درآمد ماهانه ۱۴۰۴") == f"درآمد ماهانه {MASK}"
    assert normalize_question("درآمد ماهانه 1404") == normalize_question(
        "درآمد ماهانه ۱۴۰۴"
    )


# ── the braces the curator learns ────────────────────────────────────────
def test_slots_are_read_in_order_without_duplicates() -> None:
    assert slots(PATTERN) == ["region", "year"]
    assert slots("{a} and {a} and {b}") == ["a", "b"]
    assert slots("no braces here") == []


def test_the_editor_preview_substitutes_real_values() -> None:
    # Nobody understands `{region}` from the brace; everybody understands
    # "revenue by month for EMEA in 2026".
    assert example_questions(PATTERN, [("region", "EMEA"), ("year", "2026")]) == (
        "revenue by month for EMEA in 2026"
    )
