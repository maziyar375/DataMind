"""Building the prose prompts — the section's paragraph, and the summary.

Pure, on the same terms as `outline.py`: dataclasses and strings go in, messages
come out. No session, no settings, no HTTP client, and — the part that matters —
**no disclosure decision**. The results arriving here are already disclosed.

That is not a shortcut around CLAUDE.md invariant #4; it is what the invariant
requires. `disclosure.disclose()` lives in `app.pipeline`, which sits *above*
`app.reports` in the layer order, so this module could not call it even if it
wanted to. The caller — `workers/report.py` — runs every result through
`disclose()` under the connection's policy in force *now*, and hands the
permitted columns, rows and note down here. A `BlockNarration` therefore
contains exactly what the model is allowed to see, and this module's only job
is to lay it out.

Two things this module does decide, both of which only ever narrow what the
model sees:

* **A row budget.** `FULL` shares every row, and a section carrying three
  thousand-row results would spend the context window on data no paragraph can
  use. So each block is rendered with at most `MAX_PROMPT_ROWS` rows and says
  how many it left out.
* **A value budget per cell.** A `notes` column can hold a paragraph of its
  own; truncating it keeps one wide cell from crowding out the fifty rows
  around it.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domain.ports.llm import ChatMessage
from app.reports.facts import FactSheet
from app.reports.prompts import (
    FACTS_HEADER,
    LANGUAGE_NAMES,
    REPORT_SECTION_ESTABLISHED,
    REPORT_SECTION_NEIGHBOURS,
    REPORT_SECTION_SYSTEM,
    REPORT_SECTION_USER,
    REPORT_SUMMARY_SYSTEM,
    REPORT_SUMMARY_USER,
)

#: Rows of one block's result that reach the prompt. Fifty is what `SAMPLE`
#: already shares, so under that policy this changes nothing; under `FULL` it is
#: the line between a paragraph and a token bill.
MAX_PROMPT_ROWS = 50

#: One cell's characters. Long enough for a product name or a status, short
#: enough that a `notes` column cannot eat the section.
MAX_CELL_CHARS = 120

#: Sections whose prose reaches the summary call. A report is capped at eight
#: sections by `outline.MAX_SECTIONS`, so this is a ceiling, not a filter.
MAX_SUMMARY_SECTIONS = 12

#: Earlier sections whose findings are carried into the next section's prompt.
#: The point is that section five does not restate section two; the point is not
#: to hand section five the whole document, which would grow every prompt in the
#: run and, past a few hundred words, stop being read at all.
MAX_ESTABLISHED = 5

#: Characters of an earlier section kept as "what it established". One or two
#: sentences: the finding, not the paragraph.
MAX_ESTABLISHED_CHARS = 240


@dataclass(frozen=True, slots=True)
class BlockNarration:
    """One block's result, as the prose prompt is allowed to see it.

    `columns`, `rows` and `note` are the output of `disclose()` — never the raw
    result. `kpi` is the headline figure `plan_kpi` computed from the rows
    (Tier 1 of §9): it is given to the model so the paragraph can be
    qualitative *around* a number it does not have to write, because the one
    figure a report must not get wrong is the one drawn largest.
    """

    question: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    note: str = ""
    kpi: str | None = None
    error: str | None = None
    row_count: int = 0
    #: What `facts.py` computed from these rows — the totals, changes, shares
    #: and extremes the paragraph would otherwise have to derive by eye. Empty
    #: when the model does not hold the complete result (see that module), and
    #: an empty sheet renders nothing at all rather than an empty heading.
    facts: FactSheet = field(default_factory=FactSheet)

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def is_empty(self) -> bool:
        """No rows to narrate. A failure is not empty — it is a failure."""
        return not self.failed and self.row_count == 0

    def render(self) -> str:
        """This block, as the model reads it."""
        head = f"Question: {self.question.strip()}"
        if self.failed:
            # Named rather than hidden: a paragraph written as if the section
            # had four results when one of them failed is a paragraph that
            # quietly narrates three quarters of the picture.
            return f"{head}\nThis query could not be run: {self.error}"

        if self.is_empty:
            return f"{head}\nNo rows. {self.note}".strip()

        lines = [head]
        if self.kpi:
            lines.append(f"Headline figure (already computed and shown): {self.kpi}")
        lines.append(f"Result ({self.row_count} rows):")
        lines.append(" | ".join(self.columns))
        for row in self.rows[:MAX_PROMPT_ROWS]:
            lines.append(" | ".join(_cell(value) for value in row))
        if len(self.rows) > MAX_PROMPT_ROWS:
            lines.append(
                f"(the first {MAX_PROMPT_ROWS} rows of {len(self.rows)} are shown)"
            )
        if self.note:
            lines.append(self.note)
        # Last, and deliberately: the table is what the figures were computed
        # from, so the model reads the source before the summary of it, and the
        # instruction to prefer these sits closest to the sentence it writes.
        if self.facts:
            lines.append("")
            lines.append(FACTS_HEADER)
            lines.append(self.facts.render())
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class WrittenSection:
    """A finished section, as the summary call reads it."""

    heading: str
    prose: str


def has_no_data(blocks: list[BlockNarration]) -> bool:
    """Every block came back empty — which is an answer, not a failure.

    A section under this rule is written without a model call at all: there is
    nothing to narrate, and the one sentence that says so must never vary.
    Distinct from "every block failed", which the caller reports as a failure
    because something went wrong rather than nothing happened.
    """
    return bool(blocks) and all(block.is_empty for block in blocks)


def has_nothing_to_say(blocks: list[BlockNarration]) -> bool:
    """No block produced rows, whether it was empty or broken."""
    return not blocks or all(block.failed or block.is_empty for block in blocks)


def section_messages(
    *,
    heading: str,
    intent: str,
    blocks: list[BlockNarration],
    language: str,
    request: str,
    other_headings: Sequence[str] = (),
    established: Sequence[WrittenSection] = (),
) -> list[ChatMessage]:
    """The one call that writes a section's paragraph.

    The language is named as a *name* rather than a code, and it is named per
    report rather than inferred per section — otherwise section three comes
    back in English because its heading happened to be a metric name.

    `other_headings` and `established` are what make seven independently
    written paragraphs read as one document. Without them every section is
    written by a writer who has never seen the rest of the report, and the
    result is the failure users describe as "it repeats itself": three
    paragraphs each opening on total revenue because total revenue is the
    largest number each of them was given. The headings say what is covered
    elsewhere; the established findings say what has already been claimed, so a
    later section can contrast with it instead of restating it.

    Both are bounded — `MAX_ESTABLISHED` sections at `MAX_ESTABLISHED_CHARS`
    each — because this text grows with every section written and an unbounded
    running context turns the last section of a long report into the most
    expensive call in the run.
    """
    results = "\n\n".join(block.render() for block in blocks) or "No results."
    return [
        ChatMessage(role="system", content=REPORT_SECTION_SYSTEM),
        ChatMessage(
            role="user",
            content=REPORT_SECTION_USER.format(
                language=LANGUAGE_NAMES.get(language, language),
                request=request.strip() or "(not given)",
                heading=heading.strip(),
                intent=intent.strip() or "(not given)",
                neighbours=_neighbours(other_headings, established),
                results=results,
            ),
        ),
    ]


def _neighbours(
    other_headings: Sequence[str], established: Sequence[WrittenSection]
) -> str:
    """The two context blocks, rendered only when they have something in them.

    A one-section report sends neither, so its prompt carries no empty
    scaffolding for the model to wonder about.
    """
    parts: list[str] = []

    headings = [h.strip() for h in other_headings if h and h.strip()]
    if headings:
        parts.append(
            REPORT_SECTION_NEIGHBOURS.format(
                headings="\n".join(f"- {heading}" for heading in headings)
            )
        )

    recent = [s for s in established if s.prose.strip()][-MAX_ESTABLISHED:]
    if recent:
        parts.append(
            REPORT_SECTION_ESTABLISHED.format(
                established="\n".join(
                    f"- {section.heading.strip()}: "
                    f"{_gist(section.prose, MAX_ESTABLISHED_CHARS)}"
                    for section in recent
                )
            )
        )
    return "".join(parts)


def _gist(prose: str, limit: int) -> str:
    """An earlier section's finding: its opening sentences, up to a limit.

    The lead is where the finding is, because that is what the section prompt
    asks for — so the first sentences are the part a later section must not
    repeat, and the rest is detail it was never going to duplicate anyway.
    """
    text = " ".join(prose.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a sentence boundary, in either script's punctuation, so the gist
    # ends as a statement rather than mid-clause.
    stop = max(cut.rfind(mark) for mark in (". ", "۔ ", "! ", "? ", "؟ ", "।"))
    return (cut[: stop + 1] if stop > limit // 2 else cut).strip() + "…"


def summary_messages(
    *, sections: list[WrittenSection], language: str, request: str
) -> list[ChatMessage]:
    """The executive summary, written last and read first.

    It is given the finished **prose** and no data of its own. That is the
    whole design: a summary that could reach the rows would be a second place
    for a figure to be invented, and one that can only quote the sections can
    be checked against them.
    """
    written = "\n\n".join(
        f"## {section.heading.strip()}\n{section.prose.strip()}"
        for section in sections[:MAX_SUMMARY_SECTIONS]
        if section.prose.strip()
    )
    return [
        ChatMessage(role="system", content=REPORT_SUMMARY_SYSTEM),
        ChatMessage(
            role="user",
            content=REPORT_SUMMARY_USER.format(
                language=LANGUAGE_NAMES.get(language, language),
                request=request.strip() or "(not given)",
                sections=written or "(no section was written)",
            ),
        ),
    ]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else f"{text[:MAX_CELL_CHARS]}…"
