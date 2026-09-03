"""Which templates are worth running against each other, and with what values.

The pure half of Phase 4's conflict checker. The worker
(`app/workers/knowledge_maintenance.py`) owns the database and the connector;
everything that decides *what to compare* lives here, where it runs against
literals in a test with no database anywhere near it.

**Why this check is the strongest thing in the phase.** Fabric detects
conflicting instructions by reasoning over SQL *text* and reports a confidence
score of one to five. DataMind can run both statements and compare the result
sets, because `app/knowledge/compare.py` already does deterministic result-set
comparison with a documented numeric tolerance. Two templates whose normalised
questions are near-duplicates and whose results differ on the same connection
is a **fact**, not an opinion — and the diverging rows are the evidence.

Two decisions worth stating, because both are places where guessing would be
easy and wrong:

**Probe values are derived, never invented.** To run two parameterized
statements "at the same parameter values" you need values. A date slot gets a
window computed from the run clock, because every date slot accepts one. A
string slot gets the first value the *curator* declared in its comment
(`one of: EMEA, NA, APAC`) — and a string slot with no declared vocabulary
yields nothing, so the pair is **skipped**. Filling it with a plausible-looking
noun would produce two statements that return nothing and a comparison that
says "these agree", which is worse than no check: it is a check that says the
store is healthy because it could not test it.

**A pair is skipped loudly, not silently.** The worker logs every skip with the
slot that had no value, for the same reason `REJECTED_UNBOUND` is logged on the
ask path: that log is how the next thing to teach the binder gets chosen.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.knowledge.bind import END_NAMES
from app.knowledge.matcher import trigram_similarity
from app.knowledge.models import KnowledgeTemplate, ParamType, TemplateParam

#: How alike two normalised questions must be before it is worth running both.
#:
#: Measured on the pairs this check exists for rather than picked: *"monthly
#: revenue"* / *"revenue by month"* scores 0.65, *"total revenue last month"* /
#: *"revenue for last month"* 0.66, *"revenue by month for {region}"* /
#: *"monthly revenue for {region}"* 0.71 — while *"total revenue"* / *"total
#: refunds"* scores 0.40 and *"revenue by region"* / *"orders by region"* 0.44.
#: 0.60 sits in the gap. Deliberately far below `SHORT_CIRCUIT_THRESHOLD`:
#: this is a question about whether two rows might mean the same thing, not
#: about whether one of them may answer a person, and the cost of testing a
#: pair that turns out to agree is one read-only query.
CONFLICT_SIMILARITY_THRESHOLD = 0.60

#: The window a date slot is probed with. A whole month ending yesterday: long
#: enough that a real filter selects rows on any sane fixture, and closed in
#: the past so two runs of the checker a minute apart compare the same data.
PROBE_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class Pair:
    """Two templates alike enough to be worth running against each other."""

    left: KnowledgeTemplate
    right: KnowledgeTemplate
    similarity: float

    @property
    def key(self) -> tuple[Any, Any]:
        """Order-independent identity, so a pair is considered once."""
        return tuple(sorted((str(self.left.id), str(self.right.id))))  # type: ignore[return-value]


def similar_pairs(
    templates: list[KnowledgeTemplate],
    *,
    threshold: float = CONFLICT_SIMILARITY_THRESHOLD,
) -> list[Pair]:
    """Every unordered pair of near-duplicate questions, most alike first.

    Compared on `question_normalized` — the match key, not the prose — so two
    askings that the store already treats as one question are what this finds.
    O(n²) on purpose: a connection with a healthy store has tens of templates,
    and a pre-filter would be an index to keep in step with the scorer for no
    measurable gain.
    """
    usable = [t for t in templates if t.question_normalized]
    pairs: list[Pair] = []
    for i, left in enumerate(usable):
        for right in usable[i + 1 :]:
            score = trigram_similarity(
                left.question_normalized, right.question_normalized
            )
            if score >= threshold:
                pairs.append(Pair(left=left, right=right, similarity=score))
    return sorted(pairs, key=lambda p: p.similarity, reverse=True)


@dataclass(frozen=True, slots=True)
class Probe:
    """Values to bind both statements with, or the slot that stopped us."""

    values: dict[str, Any]
    #: Non-empty means "do not run this pair". Named so the log can say which
    #: slot had no value, rather than "could not probe".
    unfilled: list[str]

    @property
    def ok(self) -> bool:
        return not self.unfilled


def probe_values(
    params: list[TemplateParam], *, now: datetime, window_days: int = PROBE_WINDOW_DAYS
) -> Probe:
    """One value per declared slot, deterministic for a given clock.

    Deterministic matters twice: the checker runs on a schedule, and a
    conflict that appears and disappears because the probe values moved is a
    conflict nobody will believe. Two templates sharing a slot *name* get the
    same value by construction, which is what "bind both to the same parameter
    values" means when the two statements were written by different people.
    """
    window_end = now.date() - timedelta(days=1)
    window_start = window_end - timedelta(days=window_days)

    values: dict[str, Any] = {}
    unfilled: list[str] = []
    for param in params:
        value = _probe_one(param, window_start, window_end)
        if value is None:
            unfilled.append(param.name)
        else:
            values[param.name] = value
    return Probe(values=values, unfilled=unfilled)


def _probe_one(param: TemplateParam, start: date, end: date) -> Any | None:
    if param.type.is_temporal:
        lowered = param.name.lower()
        if any(lowered.endswith(name) or lowered == name for name in END_NAMES):
            return end
        return start
    if param.type is ParamType.NUMBER:
        # Zero, not a made-up threshold. A `WHERE amount > :floor` probed at
        # zero returns the whole table for both statements, which is exactly
        # the comparison worth making: if two templates disagree with the
        # filter wide open, they disagree about the *definition*, which is what
        # a conflict is. A guessed 1,000 would compare two empty result sets on
        # a fixture whose amounts are small and call it agreement.
        return 0
    if param.type is ParamType.BOOLEAN:
        return True
    declared = param.values()
    return declared[0] if declared else None
