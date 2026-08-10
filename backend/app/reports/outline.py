"""The proposed outline: ask for one, read it, and keep what is usable.

Pure. A string and an `LLMGateway` *port* go in, a validated structure comes
out — no session, no HTTP client, no settings — which is what makes every case
below testable against a fake gateway and a literal.

The rule that shapes this file: **a malformed part costs that part, never the
proposal.** A model asked for six sections and returning five good ones plus
one with an invented field has done five sixths of a useful job, and throwing
all of it away costs the user a minute and the operator a call. So the JSON is
read section by section and block by block:

* An unknown field *inside a section* drops that section. `extra="forbid"` is
  deliberate here — an answer in a shape we did not ask for is one whose fields
  we cannot safely guess the meaning of, and a mis-bound heading is worse than
  a missing one.
* An unknown field, or a bad type, inside a *block* drops that block only. Its
  section keeps the questions that were fine.
* A truncated reply — the common failure, since an outline is the longest thing
  this feature asks a model to write in one call — keeps every section that
  arrived whole, because the sections are recovered individually rather than by
  parsing the document that never finished.

Nothing here writes to the database; the service persists what this returns.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.ports.llm import ChatMessage, LLMGateway, ResolvedLLM
from app.reports.prompts import (
    LANGUAGE_NAMES,
    REPORT_OUTLINE_SYSTEM,
    REPORT_OUTLINE_USER,
)

# Caps, not preferences: the prompt asks for the number of sections the user
# chose, of 1-3 blocks each, and these bound what a model that ignored it can
# cost. Every block is a query and a model call at generation time, so an
# outline of forty is not an outline.
MAX_SECTIONS = 8
MAX_BLOCKS_PER_SECTION = 4

# How many sections the model is asked for, and the range the user may ask
# within. The floor is 2 because one section is not a document — it is a chart
# with a paragraph — and the ceiling is `MAX_SECTIONS`, so the number a user
# picks and the number the parser will keep can never disagree.
MIN_SECTION_TARGET = 2
MAX_SECTION_TARGET = MAX_SECTIONS
DEFAULT_SECTION_TARGET = 5


def clamp_section_target(value: int | None) -> int:
    """The requested count, brought inside what this module can honour.

    Clamped rather than refused: this is the one number in the create dialog
    with no wrong answer, and 422-ing a report because someone typed 20 would
    trade a document for a lecture. The API validates the range at the edge —
    this is what makes a direct service call, a migrated row, or a future
    caller safe too.
    """
    if value is None:
        return DEFAULT_SECTION_TARGET
    return max(MIN_SECTION_TARGET, min(MAX_SECTION_TARGET, value))

# The column widths in `models.py`. Truncating here rather than letting the
# database do it means the text the user approves is the text that was stored.
MAX_HEADING_CHARS = 300
MAX_INTENT_CHARS = 2_000
MAX_QUESTION_CHARS = 2_000
MAX_TITLE_CHARS = 300

#: Output-token floor for the outline call, whatever the provider row says. An
#: outline of seven sections is long, and a truncated one is the failure this
#: module spends most of its lines recovering from.
#:
#: Raised with the r2 prompt, which asks for 4-7 sections rather than 3-6 and
#: for an `intent` that states a comparison rather than a topic — so the reply
#: is roughly half as long again, and the recovery path that used to be the
#: exception would have become the norm.
OUTLINE_MIN_MAX_TOKENS = 6_144

# The summary the user did not ask for and always wants. Added by the service
# at position 0 with `kind = EXECUTIVE_SUMMARY`, and removable like any other
# section — which is why its wording lives here rather than in a prompt.
EXECUTIVE_SUMMARY = {
    "fa": ("خلاصه مدیریتی", "مهم‌ترین یافته‌های این گزارش در چند جمله."),
    "en": (
        "Executive summary",
        "The findings of this report in a few sentences, for a reader who "
        "will not read the rest.",
    ),
}


class ProposedBlock(BaseModel):
    """One question the model proposes. `extra="forbid"` — see the module doc."""

    model_config = ConfigDict(extra="forbid")

    question: str
    #: What the figure is captioned with: a statement, where `question` is a
    #: question. **Optional, and empty is a real answer** — the reader is shown
    #: the question when a model omits it, which is what every outline proposed
    #: before r4 amounts to. A missing title must never cost a block: the
    #: caption is the one field of a block that can be fixed by typing over it.
    title: str = ""
    block_type: Literal["CHART", "TABLE", "METRIC"] = "CHART"
    time_window: Literal[
        "none", "last_7_days", "last_30_days", "last_month", "last_3_months",
        "last_12_months", "previous_quarter", "ytd", "custom",
    ] = "none"


class ProposedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    intent: str = ""
    blocks: list[ProposedBlock] = []


@dataclass(frozen=True, slots=True)
class OutlineProposal:
    """What survived, and what did not.

    The counts are not decoration: they are the only signal that a model is
    answering in a shape this parser has to keep salvaging, and they are what a
    log line needs to say so before anyone notices through the UI.
    """

    sections: list[ProposedSection] = field(default_factory=list)
    dropped_sections: int = 0
    dropped_blocks: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.sections


def build_messages(
    *,
    request: str,
    language: str,
    sections: int,
    dialect: str,
    schema_block: str,
) -> list[ChatMessage]:
    """The one call this module makes, as messages.

    Separate from `propose` so a test can read the prompt without a gateway,
    and so the language reaches the model as a name rather than a code.

    The section count rides in the *user* message, next to the request it came
    from, rather than in the system prompt: the system prompt is the house
    style and is identical for every report, and a number that changes per
    report does not belong in the part a provider can cache.
    """
    return [
        ChatMessage(role="system", content=REPORT_OUTLINE_SYSTEM),
        ChatMessage(
            role="user",
            content=REPORT_OUTLINE_USER.format(
                language=LANGUAGE_NAMES.get(language, language),
                sections=clamp_section_target(sections),
                dialect=dialect,
                request=request.strip(),
                schema=schema_block,
            ),
        ),
    ]


async def propose(
    gateway: LLMGateway,
    llm: ResolvedLLM,
    *,
    request: str,
    language: str,
    sections: int = DEFAULT_SECTION_TARGET,
    dialect: str,
    schema_block: str,
) -> OutlineProposal:
    """Ask for an outline and read the reply.

    `complete`, not `structured`: the gateway's structured path fails the whole
    reply when the JSON will not parse, and the reply this call gets is long
    enough that "will not parse" usually means "was cut off after four good
    sections". Recovering those four is the entire point of `parse`.

    A reply longer than `sections` is trimmed to it — the user asked for a
    number and the extra sections are the model's opinion, not theirs. A reply
    *shorter* is kept as it came: the module's rule is that a malformed part
    costs that part and never the proposal, and four good sections the user can
    add a fifth to beats a refusal.
    """
    target = clamp_section_target(sections)
    completion = await gateway.complete(
        llm,
        build_messages(
            request=request,
            language=language,
            sections=target,
            dialect=dialect,
            schema_block=schema_block,
        ),
    )
    return parse(completion.text, limit=target)


def parse(text: str, *, limit: int = MAX_SECTIONS) -> OutlineProposal:
    """Read a model's reply into an outline, keeping whatever is usable."""
    candidates = _candidates(text or "")

    sections: list[ProposedSection] = []
    dropped_sections = 0
    dropped_blocks = 0
    seen: set[str] = set()

    for candidate in candidates:
        section, lost = _section(candidate)
        dropped_blocks += lost
        if section is None:
            dropped_sections += 1
            continue
        key = section.heading.casefold()
        if key in seen:
            # Two sections with one heading render as one heading twice, and
            # the second paragraph reads as a repeat of the first.
            dropped_sections += 1
            continue
        seen.add(key)
        sections.append(section)

    kept = min(max(1, limit), MAX_SECTIONS)
    dropped_sections += max(0, len(sections) - kept)
    return OutlineProposal(
        sections=sections[:kept],
        dropped_sections=dropped_sections,
        dropped_blocks=dropped_blocks,
    )


def executive_summary(language: str) -> ProposedSection:
    """The section that is written last and read first."""
    heading, intent = EXECUTIVE_SUMMARY.get(language, EXECUTIVE_SUMMARY["en"])
    return ProposedSection(heading=heading, intent=intent, blocks=[])


# ── reading the reply ────────────────────────────────────────────────────
def _section(data: Any) -> tuple[ProposedSection | None, int]:
    """One candidate object as a section, and how many blocks it cost."""
    if not isinstance(data, dict):
        return None, 0

    raw_blocks = data.get("blocks")
    raw_blocks = raw_blocks if isinstance(raw_blocks, list) else []

    blocks: list[ProposedBlock] = []
    dropped = 0
    for item in raw_blocks:
        try:
            block = ProposedBlock.model_validate(item)
        except ValidationError:
            dropped += 1
            continue
        question = _clean(block.question, MAX_QUESTION_CHARS)
        if not question:
            dropped += 1
            continue
        blocks.append(
            block.model_copy(
                update={
                    "question": question,
                    "title": _clean(block.title, MAX_TITLE_CHARS),
                }
            )
        )

    dropped += max(0, len(blocks) - MAX_BLOCKS_PER_SECTION)
    blocks = blocks[:MAX_BLOCKS_PER_SECTION]

    try:
        section = ProposedSection.model_validate(
            {**data, "blocks": [b.model_dump() for b in blocks]}
        )
    except ValidationError:
        # An unknown field, or a heading that is not a string. See the module
        # docstring: this costs the section, never the proposal.
        return None, dropped

    heading = _clean(section.heading, MAX_HEADING_CHARS)
    if not heading or not blocks:
        # A section with no questions has nothing to narrate, and a heading the
        # generator would render above an empty space.
        return None, dropped
    return (
        section.model_copy(
            update={
                "heading": heading,
                "intent": _clean(section.intent, MAX_INTENT_CHARS),
                "blocks": blocks,
            }
        ),
        dropped,
    )


def _candidates(text: str) -> Sequence[Any]:
    """Every object in the reply that might be a section.

    Three readings, in order of how much the model got right: the document as
    asked for, a bare list of sections, and — when neither parses — whatever
    complete objects can be recovered from the text.
    """
    body = _unfence(text)
    try:
        parsed = json.loads(body)
    except ValueError:
        return _salvage(body)

    if isinstance(parsed, dict):
        for key in ("sections", "outline", "report"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        # A single section returned bare, without its wrapper.
        return [parsed] if "heading" in parsed else []
    if isinstance(parsed, list):
        return parsed
    return []


def _salvage(body: str) -> list[Any]:
    """The sections of a reply that stopped mid-sentence.

    Scanned object by object with `raw_decode`, so a document truncated inside
    its fifth section still yields the four that closed. Objects that carry no
    heading are the *blocks* of the section that never closed, and are dropped:
    a block promoted to a section would arrive with no heading and no question
    under it.
    """
    decoder = json.JSONDecoder()
    found: list[Any] = []
    index = 0
    while (start := body.find("{", index)) != -1:
        try:
            obj, end = decoder.raw_decode(body, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            wrapped = obj.get("sections")
            if isinstance(wrapped, list):
                return wrapped
            found.append(obj)
        index = end
    return [o for o in found if isinstance(o, dict) and "heading" in o]


def _unfence(text: str) -> str:
    """Strip a markdown code fence. Models add one however firmly asked not to."""
    body = text.strip()
    if not body.startswith("```"):
        return body
    body = body[3:]
    if body[:4].lower().startswith("json"):
        body = body[4:]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


def _clean(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit].strip()
