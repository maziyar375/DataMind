"""The language a report is written in, read off the request.

Pure and token-free, so every case here is a literal. The claim under test is
one sentence: **whichever script carries more of the letters carries the
document** — because a Persian request naming Latin tables is still a Persian
request, and an English one quoting a Persian product name is still English.
"""
from __future__ import annotations

import pytest

from app.reports.language import detect


@pytest.mark.parametrize(
    "request_text,expected",
    [
        ("تحلیل فروش سه ماه اخیر", "fa"),
        ("an analysis of the last three months of sales", "en"),
        # A Persian request over an English schema: the table names are not a
        # vote, and this is the common case for a Persian user of this product.
        ("درآمد ماهانه از جدول orders در ۱۲ ماه گذشته", "fa"),
        # And the mirror: an English request quoting one Persian name.
        ("revenue for the «تهران» region by month", "en"),
        # Nothing to go on falls back rather than guessing.
        ("", "en"),
        ("2024 — 12/31 (#3)", "en"),
    ],
)
def test_the_script_that_carries_the_letters_carries_the_document(
    request_text: str, expected: str
) -> None:
    assert detect(request_text) == expected


def test_the_name_answers_when_the_request_is_empty() -> None:
    """A report may be created before its request is written, and a document
    titled in Persian and written in English is a bug nobody asked for."""
    assert detect("", "گزارش فروش") == "fa"


def test_the_request_outranks_the_name_rather_than_being_averaged_with_it() -> None:
    """Concatenated, a Latin name would outvote a short Persian request — and
    the request is the thing the whole document is written towards."""
    assert detect("تحلیل فروش", "Q3 sales review for the leadership team") == "fa"


def test_a_language_with_no_prompt_of_its_own_is_written_in_english() -> None:
    """`LANGUAGE_NAMES` names two languages, and the prose prompts name one of
    them explicitly. Detecting a third would promise a document nothing knows
    how to write."""
    assert detect("Bericht über den Umsatz der letzten drei Monate") == "en"
