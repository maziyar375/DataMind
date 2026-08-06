"""The prose prompts: what the model is shown, and in which language.

Pure, like the module: dataclasses in, messages out, no gateway and no session.

Three claims carry the section:

* **`ANSWER_SYSTEM` is not reused.** It is tuned for a two-sentence chat bubble
  that leads with the number answering one question, and reusing it is exactly
  how a report ends up reading like a chat transcript — a heading, then
  "Revenue was $1.24M.", seven times.
* **The language is pinned per report and named, not inferred.** A section
  whose heading happens to be a metric name must not come back in English
  because of it, and a model handed the code `fa` guesses where one handed
  "Persian (فارسی)" does not.
* **What reaches the prompt is what `disclose()` permitted.** This module is
  handed the disclosed columns, rows and note and lays them out; it cannot
  reach past them, and the layer contract makes sure of it — `app.reports`
  cannot import `app.pipeline`, where `disclose` lives.
"""
from __future__ import annotations

import pytest

from app.pipeline.prompts import ANSWER_SYSTEM
from app.reports.narrate import (
    MAX_CELL_CHARS,
    MAX_PROMPT_ROWS,
    BlockNarration,
    WrittenSection,
    has_no_data,
    has_nothing_to_say,
    section_messages,
    summary_messages,
)
from app.reports.prompts import REPORT_SECTION_SYSTEM, REPORT_SUMMARY_SYSTEM

REVENUE = BlockNarration(
    question="revenue by month",
    columns=["month", "revenue"],
    rows=[["2026-05", 1_234_567], ["2026-04", 987_654]],
    row_count=2,
)
PRODUCTS = BlockNarration(
    question="top products",
    columns=["product", "revenue"],
    rows=[["Widget", 400_000]],
    row_count=1,
)


def _messages(**overrides: object) -> list:
    kwargs: dict = {
        "heading": "روند درآمد",
        "intent": "how revenue moved over the window",
        "blocks": [REVENUE],
        "language": "fa",
        "request": "یک گزارش تحلیلی از عملکرد فروش",
    }
    return section_messages(**{**kwargs, **overrides})  # type: ignore[arg-type]


# ── the prompt this is, and the prompt it is not ─────────────────────────
def test_the_section_prompt_is_its_own_and_not_the_chat_one() -> None:
    system, user = _messages()

    assert system.role == "system"
    assert system.content == REPORT_SECTION_SYSTEM
    assert ANSWER_SYSTEM not in system.content
    # The rule that separates a document from a transcript.
    assert "Two to four sentences" in system.content
    assert user.role == "user"


def test_the_language_is_named_not_coded() -> None:
    _system, user = _messages()

    assert "Persian (فارسی)" in user.content
    assert not user.content.startswith("Write this section in: fa\n")


def test_the_language_is_pinned_even_when_everything_else_is_english() -> None:
    """The failure this prevents: section three comes back in English because
    its heading happened to be a metric name."""
    _system, user = _messages(heading="MRR", intent="", blocks=[PRODUCTS])

    assert "Persian (فارسی)" in user.content


def test_the_section_is_given_its_heading_its_intent_and_the_report_request() -> None:
    _system, user = _messages()

    assert "روند درآمد" in user.content
    assert "how revenue moved over the window" in user.content
    # What the reader asked for, so the paragraph is written towards it.
    assert "یک گزارش تحلیلی از عملکرد فروش" in user.content


def test_a_missing_intent_is_said_rather_than_left_blank() -> None:
    _system, user = _messages(intent="")

    assert "(not given)" in user.content


# ── what the model is shown ──────────────────────────────────────────────
def test_the_disclosed_values_are_what_reaches_the_prompt() -> None:
    _system, user = _messages()

    assert "month | revenue" in user.content
    assert "2026-05 | 1234567" in user.content
    assert "Question: revenue by month" in user.content


def test_a_withheld_result_carries_its_note_and_no_values() -> None:
    """What `disclose()` returns under a narrow policy: columns and a sentence,
    no rows. Reports refuse those policies at creation — but this module must
    still be incapable of showing what it was not given."""
    withheld = BlockNarration(
        question="revenue by month",
        columns=["month", "revenue"],
        rows=[],
        note="1,240 rows across columns: month, revenue. Individual values "
        "were not shared with the model.",
        row_count=1_240,
    )

    _system, user = _messages(blocks=[withheld])

    assert "Individual values were not shared" in user.content
    assert "1234567" not in user.content


def test_several_blocks_arrive_in_one_message_so_they_can_be_related() -> None:
    """One paragraph narrating several results together is the entire payoff of
    a section owning N blocks."""
    _system, user = _messages(blocks=[REVENUE, PRODUCTS])

    assert "revenue by month" in user.content
    assert "top products" in user.content
    assert "relate them to each other" in _messages()[0].content


def test_the_headline_figure_is_handed_over_already_computed() -> None:
    """Tier 1 of §9: `plan_kpi` computed it from the rows, so it is the one
    number the model is not being asked to write."""
    _system, user = _messages(
        blocks=[BlockNarration(**{**vars_of(REVENUE), "kpi": "Revenue: $1.23M"})]
    )

    assert "Headline figure (already computed and shown): Revenue: $1.23M" in user.content


def test_a_long_result_is_capped_and_says_so() -> None:
    """`FULL` shares every row; a section carrying three thousand-row results
    would spend the context window on data no paragraph can use."""
    rows = [[f"2026-{i:02d}", i] for i in range(MAX_PROMPT_ROWS + 20)]
    long = BlockNarration(
        question="daily revenue",
        columns=["day", "revenue"],
        rows=rows,
        row_count=len(rows),
    )

    _system, user = _messages(blocks=[long])

    assert f"the first {MAX_PROMPT_ROWS} rows of {len(rows)}" in user.content
    assert user.content.count("2026-") == MAX_PROMPT_ROWS


def test_one_wide_cell_cannot_crowd_out_the_rows_around_it() -> None:
    wide = BlockNarration(
        question="tickets",
        columns=["note"],
        rows=[["x" * 500]],
        row_count=1,
    )

    _system, user = _messages(blocks=[wide])

    assert "…" in user.content
    assert "x" * (MAX_CELL_CHARS + 1) not in user.content


def test_a_failed_block_is_named_rather_than_hidden() -> None:
    """A paragraph written as if the section had two results when one of them
    failed narrates half the picture as if it were the whole."""
    broken = BlockNarration(question="refunds", error="Table not allowed.")

    _system, user = _messages(blocks=[REVENUE, broken])

    assert "could not be run: Table not allowed." in user.content


def test_an_empty_block_says_it_is_empty() -> None:
    empty = BlockNarration(question="returns", columns=["month"], rows=[], row_count=0)

    _system, user = _messages(blocks=[REVENUE, empty])

    assert "No rows" in user.content


# ── the three states of a section ────────────────────────────────────────
def test_every_block_empty_is_data_free_not_broken() -> None:
    empty = BlockNarration(question="returns", row_count=0)

    assert has_no_data([empty, empty])
    assert has_nothing_to_say([empty, empty])


def test_a_failure_is_not_emptiness() -> None:
    """Different outcomes with different sentences: "nothing happened" is a
    finding a report may legitimately state; "the query broke" is not."""
    broken = BlockNarration(question="refunds", error="boom")

    assert not has_no_data([broken])
    assert has_nothing_to_say([broken])


def test_one_block_with_rows_is_enough_to_write_about() -> None:
    empty = BlockNarration(question="returns", row_count=0)

    assert not has_no_data([REVENUE, empty])
    assert not has_nothing_to_say([REVENUE, empty])


# ── the summary ──────────────────────────────────────────────────────────
def test_the_summary_is_written_from_prose_and_no_data_of_its_own() -> None:
    """A summary that could reach the rows would be a second place for a figure
    to be invented. One that can only quote the sections can be checked."""
    system, user = summary_messages(
        sections=[
            WrittenSection(heading="روند درآمد", prose="درآمد ۱٫۲ میلیون بود."),
            WrittenSection(heading="Top products", prose="Widget led the quarter."),
        ],
        language="fa",
        request="عملکرد فروش",
    )

    assert system.content == REPORT_SUMMARY_SYSTEM
    assert "درآمد ۱٫۲ میلیون بود." in user.content
    assert "Widget led the quarter." in user.content
    assert "Persian (فارسی)" in user.content
    # No result table reaches it, in any form.
    assert "|" not in user.content


def test_a_section_that_was_never_written_is_left_out_of_the_summary() -> None:
    _system, user = summary_messages(
        sections=[
            WrittenSection(heading="Empty", prose="  "),
            WrittenSection(heading="Real", prose="Revenue rose."),
        ],
        language="en",
        request="",
    )

    assert "Empty" not in user.content
    assert "Revenue rose." in user.content


def test_a_report_where_nothing_was_written_still_builds_a_prompt() -> None:
    _system, user = summary_messages(sections=[], language="en", request="")

    assert "(no section was written)" in user.content


@pytest.mark.parametrize("language,named", [("fa", "Persian"), ("en", "English")])
def test_both_languages_are_named(language: str, named: str) -> None:
    _system, user = _messages(language=language)

    assert named in user.content


def vars_of(block: BlockNarration) -> dict:
    """`BlockNarration` is a slots dataclass, so it has no `__dict__`."""
    return {field: getattr(block, field) for field in block.__slots__}
