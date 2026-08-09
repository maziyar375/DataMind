"""Reading a model's proposed outline.

The whole module is a pure function of a string, so every case here is a
literal and a fake gateway — no database, no settings, no HTTP client. That is
what the self-contained contract on `app.reports` buys.

Four replies matter, and they are the four the plan names: a good one, a
truncated one, one carrying a field nobody asked for, and an empty one. Each is
a real failure of a real provider, and the claim under test is the same in all
four: **a malformed part costs that part, never the proposal.**
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.domain.ports.llm import ChatMessage, Completion, ResolvedLLM
from app.reports.outline import (
    DEFAULT_SECTION_TARGET,
    MAX_BLOCKS_PER_SECTION,
    MAX_SECTION_TARGET,
    MAX_SECTIONS,
    MIN_SECTION_TARGET,
    clamp_section_target,
    executive_summary,
    parse,
    propose,
)
from app.reports.prompts import (
    REPORT_OUTLINE_SYSTEM,
    REPORT_OUTLINE_USER,
    REPORT_PROMPT_VERSION,
)

LLM = ResolvedLLM(config_id="c", provider="openai", model="m", base_url=None)


def _section(heading: str, question: str = "revenue by month", **extra: Any) -> dict:
    return {
        "heading": heading,
        "intent": f"what {heading} says",
        "blocks": [{"question": question, "block_type": "CHART", "time_window": "none"}],
        **extra,
    }


GOOD = {
    "sections": [
        _section("روند درآمد", "درآمد ماهانه سه ماه گذشته"),
        {
            "heading": "Top products",
            "intent": "which products carried the quarter",
            "blocks": [
                {
                    "question": "top 10 products by revenue",
                    "block_type": "TABLE",
                    "time_window": "last_3_months",
                },
                {"question": "revenue per category", "block_type": "CHART"},
            ],
        },
    ]
}


class FakeGateway:
    """Returns one canned reply and records what it was asked."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[ChatMessage] = []

    async def complete(self, _llm: Any, messages: Any) -> Completion:
        self.messages = list(messages)
        return Completion(text=self.text)


# ── a good reply ─────────────────────────────────────────────────────────
def test_a_good_reply_becomes_sections_and_blocks() -> None:
    proposal = parse(json.dumps(GOOD, ensure_ascii=False))

    assert [s.heading for s in proposal.sections] == ["روند درآمد", "Top products"]
    assert proposal.dropped_sections == 0 and proposal.dropped_blocks == 0

    second = proposal.sections[1]
    assert [b.question for b in second.blocks] == [
        "top 10 products by revenue",
        "revenue per category",
    ]
    assert second.blocks[0].block_type == "TABLE"
    assert second.blocks[0].time_window == "last_3_months"
    # The default is the neutral window, never a period nobody asked for.
    assert second.blocks[1].time_window == "none"


def test_a_fenced_reply_is_read_anyway() -> None:
    """Models add a code fence however firmly the prompt says JSON only."""
    fenced = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"

    assert len(parse(fenced).sections) == 2


def test_a_bare_list_of_sections_is_read() -> None:
    assert len(parse(json.dumps(GOOD["sections"], ensure_ascii=False)).sections) == 2


async def test_propose_sends_the_language_the_schema_and_the_request() -> None:
    gateway = FakeGateway(json.dumps(GOOD, ensure_ascii=False))

    proposal = await propose(
        gateway,  # type: ignore[arg-type]
        LLM,
        request="یک گزارش تحلیلی از فروش",
        language="fa",
        dialect="postgres",
        schema_block="Dialect: postgres\n\nTables:\n- public.orders(id, total)",
    )

    assert len(proposal.sections) == 2
    system, user = gateway.messages
    assert system.content == REPORT_OUTLINE_SYSTEM
    # The language as a name, not a code: "fa" alone is guessed at by the
    # models that most need telling.
    assert "Persian" in user.content
    assert "یک گزارش تحلیلی از فروش" in user.content
    assert "public.orders" in user.content


def test_the_prompt_forbids_the_summary_the_service_adds() -> None:
    """Otherwise every report opens with two summaries, one of them empty."""
    assert "executive summary" in REPORT_OUTLINE_SYSTEM.lower()
    # r3 takes the number of sections from the request instead of asserting a
    # range. The version moves with the wording because a document generated
    # under r2 is a different artefact, and the run row is the only thing that
    # says which one a reader is holding.
    assert REPORT_PROMPT_VERSION == "r3"


def test_the_prompt_states_no_section_count_of_its_own() -> None:
    """The count is the user's, and it arrives in the user message.

    A range left behind in the system prompt would be a second, contradictory
    instruction — and the model would have to choose which to believe.
    """
    assert "4 and 7" not in REPORT_OUTLINE_SYSTEM
    assert "exactly {sections}" in REPORT_OUTLINE_USER


# ── how many sections ────────────────────────────────────────────────────
async def test_the_requested_section_count_reaches_the_model() -> None:
    gateway = FakeGateway(json.dumps(GOOD, ensure_ascii=False))

    await propose(
        gateway,  # type: ignore[arg-type]
        LLM,
        request="an analysis of sales",
        language="en",
        sections=3,
        dialect="postgres",
        schema_block="Tables:\n- public.orders(id, total)",
    )

    _, user = gateway.messages
    assert "Sections: exactly 3" in user.content


async def test_a_longer_reply_is_trimmed_to_what_was_asked_for() -> None:
    """The extra sections are the model's opinion, not the user's."""
    reply = {"sections": [_section(f"S{i}") for i in range(6)]}
    gateway = FakeGateway(json.dumps(reply))

    proposal = await propose(
        gateway,  # type: ignore[arg-type]
        LLM,
        request="an analysis of sales",
        language="en",
        sections=3,
        dialect="postgres",
        schema_block="",
    )

    assert [s.heading for s in proposal.sections] == ["S0", "S1", "S2"]
    assert proposal.dropped_sections == 3


async def test_a_shorter_reply_is_kept_as_it_came() -> None:
    """A malformed part costs that part, never the proposal — and four good
    sections the user can add a fifth to beat a refusal."""
    reply = {"sections": [_section("S0"), _section("S1")]}
    gateway = FakeGateway(json.dumps(reply))

    proposal = await propose(
        gateway,  # type: ignore[arg-type]
        LLM,
        request="an analysis of sales",
        language="en",
        sections=6,
        dialect="postgres",
        schema_block="",
    )

    assert len(proposal.sections) == 2
    assert proposal.dropped_sections == 0


@pytest.mark.parametrize(
    "asked,expected",
    [
        (None, DEFAULT_SECTION_TARGET),
        (0, MIN_SECTION_TARGET),
        (1, MIN_SECTION_TARGET),
        (MIN_SECTION_TARGET, MIN_SECTION_TARGET),
        (4, 4),
        (MAX_SECTION_TARGET, MAX_SECTION_TARGET),
        (99, MAX_SECTION_TARGET),
    ],
)
def test_an_impossible_count_is_clamped_rather_than_refused(
    asked: int | None, expected: int
) -> None:
    assert clamp_section_target(asked) == expected


def test_the_ceiling_a_user_may_ask_for_is_the_one_the_parser_keeps() -> None:
    """Otherwise a user could ask for nine and silently be given eight."""
    assert MAX_SECTION_TARGET == MAX_SECTIONS


# ── a truncated reply ────────────────────────────────────────────────────
def test_a_truncated_reply_keeps_the_sections_that_arrived_whole() -> None:
    """The common failure: an outline is the longest thing this feature asks
    for in one call, and a cut-off document parses as nothing at all."""
    whole = json.dumps(
        {"sections": [_section("Revenue"), _section("Returns"), _section("Regions")]},
        ensure_ascii=False,
    )
    truncated = whole[: whole.index('{"heading": "Regions"') + 40]

    proposal = parse(truncated)

    assert [s.heading for s in proposal.sections] == ["Revenue", "Returns"]
    assert proposal.sections[0].blocks[0].question == "revenue by month"


def test_a_truncated_reply_does_not_promote_a_block_to_a_section() -> None:
    """The objects after the last complete section are the blocks of the one
    that never closed. Promoted, they would render as headings with no text."""
    whole = json.dumps({"sections": [_section("Revenue"), _section("Returns")]})
    cut = whole.index('"blocks"', whole.index("Returns"))
    truncated = whole[: cut + 60]

    proposal = parse(truncated)

    assert [s.heading for s in proposal.sections] == ["Revenue"]


# ── a reply with a field nobody asked for ────────────────────────────────
def test_an_unknown_field_in_a_section_costs_that_section_only() -> None:
    """`extra="forbid"`: a shape we did not ask for is one whose fields we
    cannot safely guess the meaning of. But five good sections out of six are
    still five sections."""
    reply = {
        "sections": [
            _section("Revenue"),
            _section("Returns", chart="pie"),
            _section("Regions"),
        ]
    }

    proposal = parse(json.dumps(reply))

    assert [s.heading for s in proposal.sections] == ["Revenue", "Regions"]
    assert proposal.dropped_sections == 1


def test_an_unknown_field_in_a_block_costs_that_block_only() -> None:
    """One unusable question is not a reason to lose its heading, or the two
    questions beside it that were fine."""
    reply = {
        "sections": [
            {
                "heading": "Revenue",
                "intent": "how revenue moved",
                "blocks": [
                    {"question": "revenue by month"},
                    {"question": "revenue by week", "sql": "SELECT 1"},
                    {"question": "revenue by day"},
                ],
            }
        ]
    }

    proposal = parse(json.dumps(reply))

    section = proposal.sections[0]
    assert [b.question for b in section.blocks] == ["revenue by month", "revenue by day"]
    assert proposal.dropped_blocks == 1
    assert proposal.dropped_sections == 0


def test_an_invented_block_type_or_window_drops_the_block() -> None:
    """The enums are the contract with the database and the guard. A block
    typed `PIE` would be stored and never render."""
    reply = {
        "sections": [
            {
                "heading": "Revenue",
                "blocks": [
                    {"question": "a", "block_type": "PIE"},
                    {"question": "b", "time_window": "whenever"},
                    {"question": "c"},
                ],
            }
        ]
    }

    proposal = parse(json.dumps(reply))

    assert [b.question for b in proposal.sections[0].blocks] == ["c"]
    assert proposal.dropped_blocks == 2


# ── an empty reply ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "reply",
    ["", "   ", "I'm sorry, I can't help with that.", "{}", '{"sections": []}', "null"],
)
def test_an_unusable_reply_is_an_empty_proposal_never_an_exception(
    reply: str,
) -> None:
    """The caller decides what an empty outline means; this module never
    raises, so one bad reply cannot 500 the request."""
    proposal = parse(reply)

    assert proposal.sections == []
    assert proposal.is_empty


async def test_an_empty_reply_from_the_gateway_is_an_empty_proposal() -> None:
    proposal = await propose(
        FakeGateway(""),  # type: ignore[arg-type]
        LLM,
        request="anything",
        language="en",
        dialect="postgres",
        schema_block="",
    )

    assert proposal.is_empty


# ── what the parser normalises ───────────────────────────────────────────
def test_a_section_with_no_questions_is_not_a_section() -> None:
    """It would render as a heading above an empty space, and the prose model
    would be asked to narrate nothing."""
    reply = {"sections": [{"heading": "Introduction", "intent": "set the scene"}]}

    assert parse(json.dumps(reply)).is_empty


def test_a_repeated_heading_is_dropped() -> None:
    """Two sections under one heading read as one paragraph said twice."""
    reply = {"sections": [_section("Revenue"), _section("revenue "), _section("Returns")]}

    proposal = parse(json.dumps(reply))

    assert [s.heading for s in proposal.sections] == ["Revenue", "Returns"]
    assert proposal.dropped_sections == 1


def test_the_counts_are_capped() -> None:
    """Every block is a query and every section a model call at generation
    time, so an outline of forty is not an outline."""
    reply = {
        "sections": [
            {
                "heading": f"Section {i}",
                "blocks": [{"question": f"q{i}.{j}"} for j in range(6)],
            }
            for i in range(12)
        ]
    }

    proposal = parse(json.dumps(reply))

    assert len(proposal.sections) == MAX_SECTIONS
    assert all(len(s.blocks) == MAX_BLOCKS_PER_SECTION for s in proposal.sections)
    assert proposal.dropped_sections == 12 - MAX_SECTIONS


def test_whitespace_and_overlong_text_are_cleaned_to_the_column_widths() -> None:
    reply = {
        "sections": [
            {
                "heading": "  Revenue\n  over   time  ",
                "intent": "x" * 5_000,
                "blocks": [{"question": "y" * 5_000}],
            }
        ]
    }

    section = parse(json.dumps(reply)).sections[0]

    assert section.heading == "Revenue over time"
    assert len(section.intent) == 2_000
    assert len(section.blocks[0].question) == 2_000


# ── the summary the user did not ask for ─────────────────────────────────
@pytest.mark.parametrize("language,expected", [("fa", "خلاصه"), ("en", "Executive")])
def test_the_executive_summary_is_written_in_the_reports_language(
    language: str, expected: str
) -> None:
    section = executive_summary(language)

    assert expected in section.heading
    # No blocks: it is written last, from the other sections' prose, and runs
    # no query of its own.
    assert section.blocks == []


def test_an_unknown_language_falls_back_rather_than_failing() -> None:
    assert executive_summary("de").heading == executive_summary("en").heading
