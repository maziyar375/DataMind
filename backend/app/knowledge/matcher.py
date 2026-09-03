"""Finding the template that answers a question — the interface and the default.

**Decision D3: the matcher is an interface with a lexical default.** `pg_trgm`
ships inside `postgres:16-alpine` with no image change and always works; an
embedding implementation (Phase 7) is used only when the connection's LLM
config exposes one. The loop degrades to lexical, never to nothing.

**Two thresholds, not one.** A near-miss is not a hit: the cost of a miss is
today's behaviour, and the cost of a false hit is a confident wrong answer.
`SHORT_CIRCUIT_THRESHOLD` starts deliberately high and is tuned **from the
override rate**, not from taste — `knowledge_template_hits.OVERRIDDEN_BY_USER`
is the number that says whether the short-circuit is trusted, and no vendor in
the research publishes its equivalent.

**Where the database is.** This package may not import sqlalchemy, so
`LexicalMatcher` is given a *row source* — an async callable that returns
candidate templates. That keeps the query in `app/services/` and the matching
*policy* — the thresholds, the role and status exclusions, the tie-break —
here, where it is unit-testable without a database.

**`pg_trgm` is an index, not the verdict.** The row source uses the GIN index
to narrow tens of thousands of rows to a handful; the score that decides is
always computed here, by `trigram_similarity`, which is Postgres' own
algorithm reimplemented (pad each word with two leading spaces and one
trailing, take every 3-gram, divide the intersection by the union). One
scoring path, so a deployment whose role could not `CREATE EXTENSION` gets the
same verdicts at a higher cost per query rather than a different feature — and
so the thresholds mean one thing everywhere.

**The template's own vocabulary counts against the question.** This is the
non-obvious part, and without it the design does not work. A stored pattern
normalises to `revenue by month for * in *`; the question someone types
normalises to `revenue by month for emea in *`, because `EMEA` is not
detectably a literal from the outside. Those score **0.83** — under the
threshold, so the canonical worked example would never fire, and lowering the
threshold to 0.83 to compensate would let genuinely different questions in.

The fix is not a fudge: it uses information the **curator supplied**. A
parameter whose comment reads `one of: EMEA, NA, APAC` declares that `EMEA` is
a value of that slot, so the matcher masks it in the question before scoring —
the same masking `normalize_question` applies to a number. It can only ever
remove a difference the template itself said was a *value*, so it raises the
score of a question this template was written for and leaves every other
question exactly where it was.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.knowledge.models import KnowledgeTemplate
from app.knowledge.normalize import MASK, normalize_question

#: Answer from a stored template instead of generating. High on purpose: tune
#: it down only when the override rate says the matches are being trusted.
SHORT_CIRCUIT_THRESHOLD = 0.85

#: Offer a template to the generator as an example. Unused until Phase 5, and
#: declared here so the two numbers live together and cannot drift into being
#: the same number by accident.
FEW_SHOT_THRESHOLD = 0.45

#: How many rows a matcher asks for. Small: only the best is ever used to
#: short-circuit, and Phase 5 wants a handful of examples at most.
DEFAULT_LIMIT = 5

LEXICAL = "LEXICAL"
EMBEDDING = "EMBEDDING"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One template the matcher thinks might answer the question."""

    template: KnowledgeTemplate
    score: float
    matcher: str = LEXICAL

    @property
    def short_circuits(self) -> bool:
        return self.score >= SHORT_CIRCUIT_THRESHOLD

    @property
    def few_shot(self) -> bool:
        return self.score >= FEW_SHOT_THRESHOLD


#: `(connection_id, question_normalized, limit) -> [template]`. The source may
#: narrow with the trigram index; it never decides.
RowSource = Callable[[UUID, str, int], Awaitable[list[KnowledgeTemplate]]]

#: How wide the row source casts. `pg_trgm`'s own default `%` threshold, so a
#: query written against the index and one written against a `LIKE` fallback
#: shortlist the same rows.
SHORTLIST_FLOOR = 0.3


class TemplateMatcher(Protocol):
    """What a matcher is, so Phase 7 can add one without touching the node."""

    async def match(
        self, question: str, connection_id: UUID, *, limit: int = DEFAULT_LIMIT
    ) -> list[Candidate]:
        ...


class LexicalMatcher:
    """Trigram similarity over `question_normalized`. Always available."""

    __slots__ = ("_rows",)

    def __init__(self, rows: RowSource) -> None:
        self._rows = rows

    async def match(
        self, question: str, connection_id: UUID, *, limit: int = DEFAULT_LIMIT
    ) -> list[Candidate]:
        """The best candidates, highest first. Never raises on an empty store.

        The question is normalised **the same way the store normalised its
        own** — one function, called from both sides — because a match key
        computed two ways is a match key that stops matching after someone
        edits one of them.
        """
        normalized = normalize_question(question)
        if not normalized:
            return []

        rows = await self._rows(connection_id, normalized, max(limit, DEFAULT_LIMIT))
        candidates = [
            Candidate(
                template=template,
                score=score_against(normalized, template),
                matcher=LEXICAL,
            )
            # `is_matchable` again, in code, even though the query filters on
            # it too. Belt and braces on purpose: a held-out template answering
            # its own question measures nothing, and a stale one answers with
            # SQL the schema no longer supports. Neither failure is visible
            # from the outside, so the check is made where it is cheap and in
            # the query where it is fast.
            for template in rows
            if template.is_matchable
        ]
        return sorted(candidates, key=_rank, reverse=True)[:limit]


class FallbackMatcher:
    """Try one matcher, fall back to another when it cannot answer.

    Phase 7's *"the loop degrades to lexical, never to nothing"*, written as
    ten lines rather than as a branch inside every caller. The embedding
    matcher returns `[]` for every reason it might have — no model pinned on
    the connection, no vector fresh enough to trust, an embedding endpoint that
    is down — and each of those means *"ask the lexical one"*, which is exactly
    today's behaviour.

    **Empty rather than an exception is the contract, and the try/except here
    is the second door.** A matcher that raises would fail a person's question
    over a feature that is meant to be invisible when absent; the `match` node
    also catches, and both are cheap.

    Nothing is logged and nothing is counted here, because there is already a
    better record: `Candidate.matcher` travels to
    `knowledge_template_hits.matcher`, so *"is the embedding matcher doing
    anything?"* is a query against a table rather than a search through logs.
    """

    __slots__ = ("_primary", "_fallback")

    def __init__(self, primary: TemplateMatcher, fallback: TemplateMatcher) -> None:
        self._primary = primary
        self._fallback = fallback

    async def match(
        self, question: str, connection_id: UUID, *, limit: int = DEFAULT_LIMIT
    ) -> list[Candidate]:
        try:
            found = await self._primary.match(question, connection_id, limit=limit)
        except Exception:
            found = []
        if found:
            return found
        return await self._fallback.match(question, connection_id, limit=limit)


def score_against(normalized_question: str, template: KnowledgeTemplate) -> float:
    """How well a normalised question matches one template.

    The template's declared values are masked in the question first — see the
    module docstring. Masking can only remove a difference the curator said was
    a value, so it never invents a match: the score of a question the template
    was *not* written for is unchanged, because none of its words are in the
    template's vocabulary.
    """
    asked = mask_declared_values(normalized_question, template)
    return trigram_similarity(asked, template.question_normalized)


def mask_declared_values(normalized: str, template: KnowledgeTemplate) -> str:
    """Replace values the template's own parameters list with the mask token.

    Longest first, so `NORTH AMERICA` is one value and not `NORTH` plus a
    leftover. Whole words only — masking the `na` inside `national` would be
    the matcher inventing a difference rather than removing one.
    """
    declared = sorted(
        (value for param in template.params for value in param.values()),
        key=len,
        reverse=True,
    )
    out = normalized
    for value in declared:
        folded = value.strip().casefold()
        if not folded:
            continue
        out = re.sub(rf"(?<!\w){re.escape(folded)}(?!\w)", MASK, out)
    return re.sub(rf"{re.escape(MASK)}(?:\s*{re.escape(MASK)})+", MASK, out).strip()


def _rank(candidate: Candidate) -> tuple[float, int]:
    """Score first; a tie goes to the template people have actually used.

    A tie between two templates is a store that needs pruning, not a coin to
    flip — and until someone prunes it, the one that has answered questions
    before is the better guess.
    """
    return candidate.score, candidate.template.hit_count


def best(candidates: list[Candidate]) -> Candidate | None:
    """The one candidate worth short-circuiting on, or None.

    A near-miss returns None rather than the near-miss: every caller of this
    function is about to answer a person, and "close" is not a category an
    answer can be in.
    """
    if not candidates:
        return None
    top = candidates[0]
    return top if top.short_circuits else None


def trigram_similarity(left: str, right: str) -> float:
    """Postgres' `similarity()`, reimplemented.

    Identical algorithm, so the extension being absent changes the cost of a
    match and not its verdict: `|A ∩ B| / |A ∪ B|` over the 3-grams of each
    string, where each word is padded with two leading spaces and one trailing
    so short words still produce trigrams and word boundaries count.
    """
    a, b = trigrams(left), trigrams(right)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return len(a & b) / len(a | b)


def trigrams(value: str) -> set[str]:
    """The 3-gram set `pg_trgm` would build for this string."""
    out: set[str] = set()
    for word in _words(value):
        padded = f"  {word} "
        for i in range(len(padded) - 2):
            out.add(padded[i : i + 3])
    return out


def _words(value: str) -> list[str]:
    """Alphanumeric runs, lowercased — `pg_trgm`'s own word split.

    The mask character `normalize_question` leaves behind is not alphanumeric
    and drops out here, which is right: a masked literal contributes no
    trigrams to either side, so *"top * stores"* and *"top * shops"* differ by
    exactly the words that differ.
    """
    words: list[str] = []
    current: list[str] = []
    for character in (value or "").lower():
        if character.isalnum():
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words
