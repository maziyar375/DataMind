"""What to teach next — the ranked work queue, as a pure function.

Research L6: *the hardest part of curation is not writing the template, it is
knowing which template to write.* The system already knows; the evidence is
sitting in `runs`, in `answer_feedback`, and in the dashboard tiles somebody
already corrected by hand. This module is the reasoning over that evidence; the
aggregation that produces it lives in `app/services/knowledge_service.py`,
because this package may not import sqlalchemy.

Five kinds of suggestion, ranked in this order and for these reasons:

1. **`FLAGGED`** — somebody said an answer was wrong. That is a person's time
   already spent, and ignoring it is exactly how a feedback control becomes a
   suggestion box.
2. **`BACKFILL`** — a verified question→SQL pair that **already exists** in
   the database and is read by nothing. The cheapest knowledge in the product.
3. **`TRAFFIC`** — asked often, never matched. The clearest signal of demand
   there is.
4. **`FAILED`** — asked, and the run failed or needed repairing. Demand *and* a
   known defect.
5. **`UNKNOWN_WORDS`** — the vocabulary gap: words people use that nothing in
   the schema, the comments or the semantic layer recognises.

The last one is Power BI's *Review questions*, it is the one idea in the
research nobody else has copied, and it is nearly free here because the
semantic layer already holds the vocabulary to compare against. It ranks last
not because it is least valuable but because it is the least *actionable*: it
names a word, not a question, and the fix is often a synonym rather than a
template.

**Nothing here proposes an approved template.** A suggestion is a row with a
button on it; a person still writes the thing. A bulk "approve all" would fill
the store with unreviewed statements in an afternoon and destroy the only
property that makes it worth having.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.knowledge.normalize import MASK, normalize_question

#: Words that carry no schema meaning, so their absence from the vocabulary is
#: not a gap. Three groups, and each earns its place:
#:
#: * **grammar** — nobody expects `the` to be a column;
#: * **time** — `last`, `month`, `quarter` are how every question says *when*,
#:   and flagging them would put the same four words at the top of every
#:   backlog forever;
#: * **aggregation** — `total`, `average`, `count` describe an operation, not a
#:   thing. (A *metric* named "total revenue" still matches on `revenue`.)
#:
#: Deliberately no further than that. An aggressive list hides real misses, and
#: the cost of a false "unrecognised word" is one ignorable row.
_STOPWORDS = """
a an and are as at be by can could did do does for from get give had has have
how i in into is it its me my of on or our show tell that the their them there
these this to us was were what when where which who why will with would you
your please list top all each per over under between about many much most

last next previous past this current recent today yesterday tomorrow now
day days week weeks month months quarter quarters year years date dates time
times since until before after during ago ytd mtd qtd

total totals sum average avg mean median count number amount value values
breakdown group grouped compare comparison trend change growth rate ratio
percent percentage share split versus vs highest lowest best worst
"""
STOPWORDS = frozenset(_STOPWORDS.split())  # noqa: SIM905 - one word per idea

#: A token shorter than this is noise — an initial, a stray letter.
MIN_TOKEN_CHARS = 3

_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


class SuggestionKind(StrEnum):
    FLAGGED = "FLAGGED"
    BACKFILL = "BACKFILL"
    TRAFFIC = "TRAFFIC"
    FAILED = "FAILED"
    UNKNOWN_WORDS = "UNKNOWN_WORDS"


#: Rank order, highest first. A tuple rather than the enum's own order, so
#: reordering is one edit in one place and cannot be done by accident.
RANK = (
    SuggestionKind.FLAGGED,
    SuggestionKind.BACKFILL,
    SuggestionKind.TRAFFIC,
    SuggestionKind.FAILED,
    SuggestionKind.UNKNOWN_WORDS,
)


@dataclass(slots=True)
class Suggestion:
    """One row in the backlog: what to teach, and why it is worth teaching."""

    kind: SuggestionKind
    #: The question as somebody actually asked it — never the normalised form.
    #: A curator reads this and has to recognise it.
    question: str
    #: How many times it was asked (or, for a backfill, 1).
    count: int = 1
    #: The line that goes on the right of the row: *"asked 9× this month, never
    #: matched"*. Written here so three developers do not invent three voices.
    reason: str = ""
    #: A statement to prefill the editor with, where one exists.
    sql: str = ""
    #: `TILE` / `REPORT_BLOCK` for a backfill, so the editor can record where
    #: the SQL came from — which decides whether its literals are disclosable.
    source: str = ""
    #: `GENERATED_EDITED` literals were chosen by a model and edited by a
    #: person, so they are gated like sample values. `docs/security.md`.
    model_derived: bool = False
    #: The run or row this came from, so the UI can link back to it.
    origin_id: str = ""
    #: For `UNKNOWN_WORDS`, the words nothing recognised.
    words: list[str] = field(default_factory=list)

    @property
    def rank(self) -> tuple[int, int]:
        """Kind first, then how much traffic is behind it.

        Negated counts so a plain ascending sort puts the busiest first, which
        keeps the comparison readable at the call site.
        """
        return RANK.index(self.kind), -self.count


def rank_suggestions(items: list[Suggestion], *, limit: int = 30) -> list[Suggestion]:
    """The backlog, in the order a curator should work it.

    Finite by construction. A backlog that scrolls is a backlog nobody
    finishes, and the point of this screen is that "what should I do next" has
    an answer rather than a search.
    """
    return sorted(items, key=lambda s: s.rank)[:limit]


# ── the vocabulary gap ───────────────────────────────────────────────────
def build_vocabulary(
    tables: list[dict[str, Any]] | None = None,
    semantic: dict[str, Any] | None = None,
) -> set[str]:
    """Every word this connection can be said to *know*.

    Four sources, and all four matter: the physical names, the catalog comments
    a DBA wrote, the business names and synonyms in the semantic layer, and the
    glossary. A word absent from all four is one the retrieval had no way to
    resolve — which is exactly the gap worth showing a curator.

    Names are split on the separators that appear in real schemas, so
    `order_items` contributes `order` and `items` and a question saying "order
    items" is recognised.
    """
    words: set[str] = set()

    for table in tables or []:
        words |= _words(str(table.get("name", "")))
        words |= _words(str(table.get("schema", "")))
        words |= _words(str(table.get("comment", "")))
        for column in table.get("columns", []) or []:
            words |= _words(str(column.get("name", "")))
            words |= _words(str(column.get("comment", "")))

    for entity in (semantic or {}).get("entities", []) or []:
        words |= _words(str(entity.get("business_name", "")))
        words |= _words(str(entity.get("table", "")))
        for synonym in entity.get("synonyms", []) or []:
            words |= _words(str(synonym))
        for column in entity.get("columns", []) or []:
            words |= _words(str(column.get("business_name", "")))
            words |= _words(str(column.get("name", "")))
        for metric in entity.get("metrics", []) or []:
            words |= _words(str(metric.get("name", "")))
            words |= _words(str(metric.get("business_name", "")))
            for synonym in metric.get("synonyms", []) or []:
                words |= _words(str(synonym))

    for term in (semantic or {}).get("glossary", []) or []:
        words |= _words(str(term.get("term", "")))
        for synonym in term.get("synonyms", []) or []:
            words |= _words(str(synonym))

    return words


def unknown_words(question: str, vocabulary: set[str]) -> list[str]:
    """Words in a question that nothing in this connection recognises.

    Returned **in the order they were used and as they were typed**, because
    the reason line quotes them back to the curator and quoting a stem would
    read like a typo.

    Stopwords and very short tokens are dropped, and so is the mask token — a
    masked literal is a *value*, and values are not vocabulary. What is left is
    a word somebody used to talk about their data that the schema, the
    comments and the semantic layer have never heard of.

    Matching is by **lookup over a few endings**, not by stemming both sides.
    A stemmer commits to one form and is wrong in both directions; trying
    `customers → customer` and `churned → churn` against the real vocabulary
    can only ever mark a word *known*, which is the fail-safe direction here: a
    false "known" costs one missing backlog row, while a false "unknown" puts
    noise on every question and buries the real gaps on the first day.
    """
    known = set(vocabulary) | {_depluralise(word) for word in vocabulary}
    out: list[str] = []
    for token in _tokens(normalize_question(question)):
        if token in STOPWORDS or len(token) < MIN_TOKEN_CHARS:
            continue
        if any(form in known for form in _forms(token)):
            continue
        if token not in out:
            out.append(token)
    return out


def _tokens(value: str) -> list[str]:
    """Words in the order they were written. The question side."""
    return [w for w in _TOKEN.findall((value or "").lower()) if w != MASK]


def _words(value: str) -> set[str]:
    """Words as a set. The vocabulary side, where order means nothing."""
    return set(_tokens(value))


def _forms(word: str) -> list[str]:
    """The forms of a word worth looking for in the vocabulary.

    Length guards on purpose: `speed` must not become `spe`, and `king` must
    not become `k`. Each ending is only folded when what is left is still long
    enough to be a word somebody named a column after.
    """
    forms = [word, _depluralise(word)]
    if word.endswith("ed") and len(word) > 5:
        forms += [word[:-2], word[:-1]]
    if word.endswith("ing") and len(word) > 6:
        forms.append(word[:-3])
    return forms


def _depluralise(word: str) -> str:
    """`customers` → `customer`, `policies` → `policy`. Crude on purpose.

    A real stemmer would be a dependency and a source of surprises; the
    variation that matters most here is the plural, because people ask about
    "customers" and the column is called `customer_id`.
    """
    if len(word) > 3 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 3 and word.endswith("es") and word[-3] in "sxzo":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


# ── the copy the rows carry ──────────────────────────────────────────────
def traffic_reason(count: int) -> str:
    times = "once" if count == 1 else f"{count}×"
    return f"Asked {times} this month, never matched"


def failed_reason(count: int, repaired: int) -> str:
    if repaired and not count:
        return f"Needed repairing {repaired}×"
    if repaired:
        return f"Failed {count}×, repaired {repaired}×"
    return f"Failed {count}×"


def flagged_reason(comment: str) -> str:
    return comment.strip() or "Flagged as wrong"


def backfill_reason(source: str) -> str:
    return (
        "From a dashboard tile you corrected"
        if source == "TILE"
        else "From a report block you corrected"
    )


def unknown_reason(words: list[str]) -> str:
    listed = ", ".join(f"“{w}”" for w in words[:3])
    more = "" if len(words) <= 3 else f" and {len(words) - 3} more"
    return f"Nothing here is called {listed}{more}"
