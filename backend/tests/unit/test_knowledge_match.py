"""Which template answers a question — and, mostly, which does not.

Two thresholds, not one, and this file is largely about the higher one. **A
near-miss is not a hit.** The cost of a miss is today's behaviour: the run
generates SQL as it always did. The cost of a false hit is a confident wrong
answer wearing a Verified badge, and that is the failure class the whole
product is built around.

The three exclusions — `BENCHMARK_ONLY`, `HELD_OUT`, and anything not `ACTIVE`
— are asserted here *and* enforced in the query that builds the candidate set.
Neither failure is visible from the outside: a held-out question answered from
its own stored SQL measures nothing and looks perfect, and a stale template
answers with SQL the schema no longer supports and looks confident.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.knowledge import (
    KnowledgeTemplate,
    ParamType,
    TemplateParam,
    TemplateRole,
    TemplateStatus,
    normalize_question,
)
from app.knowledge.matcher import (
    FEW_SHOT_THRESHOLD,
    SHORT_CIRCUIT_THRESHOLD,
    LexicalMatcher,
    best,
    mask_declared_values,
    score_against,
    trigram_similarity,
    trigrams,
)

CONNECTION = uuid4()

REGION = TemplateParam(
    name="region", type=ParamType.STRING, comment="one of: EMEA, NA, APAC"
)
YEAR = TemplateParam(
    name="year", type=ParamType.DATE, comment="the first day of the year"
)


def template(
    question: str = "revenue by month for {region} in {year}",
    *,
    params: list[TemplateParam] | None = None,
    role: TemplateRole = TemplateRole.RETRIEVABLE,
    status: TemplateStatus = TemplateStatus.ACTIVE,
    hits: int = 0,
) -> KnowledgeTemplate:
    return KnowledgeTemplate(
        id=uuid4(),
        question=question,
        question_normalized=normalize_question(question),
        sql="SELECT 1 FROM orders",
        params=[REGION, YEAR] if params is None else params,
        role=role,
        status=status,
        hit_count=hits,
    )


def matcher(*templates: KnowledgeTemplate) -> LexicalMatcher:
    async def rows(_connection, _normalized, _limit):
        return list(templates)

    return LexicalMatcher(rows)


# ── the trigram scorer is Postgres' own ──────────────────────────────────
def test_similarity_is_symmetric_and_bounded() -> None:
    assert trigram_similarity("revenue by month", "revenue by month") == 1.0
    assert trigram_similarity("revenue", "customers") < 0.2
    assert trigram_similarity("a", "b") == trigram_similarity("b", "a")


def test_words_are_padded_the_way_pg_trgm_pads_them() -> None:
    # Two leading spaces and one trailing, so a short word still produces
    # trigrams and a word boundary counts as a difference. This is the
    # property that makes the in-Python fallback give the same verdicts as the
    # extension rather than merely similar ones.
    assert trigrams("ab") == {"  a", " ab", "ab "}


def test_the_mask_token_contributes_no_trigrams() -> None:
    # Which is what makes a masked literal a *wildcard*: "top * stores" and
    # "top * shops" differ by exactly the words that differ.
    assert trigrams("top * stores") == trigrams("top stores")


def test_an_empty_string_scores_nothing_rather_than_raising() -> None:
    assert trigram_similarity("", "revenue") == 0.0
    assert trigram_similarity("", "") == 1.0


# ── the template's own vocabulary ────────────────────────────────────────
def test_a_declared_value_is_masked_out_of_the_question() -> None:
    """The step without which the canonical example does not fire.

    A pattern normalises to `revenue by month for * in *`; the question
    normalises to `revenue by month for emea in *`, because `EMEA` is not
    detectably a literal from the outside. Those score 0.83 — under the
    threshold. The curator already told us `EMEA` is a value of that slot, so
    the matcher uses it.
    """
    asked = normalize_question("revenue by month for EMEA in 2026")
    assert mask_declared_values(asked, template()) == "revenue by month for * in *"


def test_masking_only_ever_removes_a_difference_the_curator_declared() -> None:
    # It cannot invent a match: a question with none of the template's declared
    # values comes back untouched.
    asked = normalize_question("how many customers signed up")
    assert mask_declared_values(asked, template()) == asked


def test_a_value_is_masked_as_a_whole_word() -> None:
    # `NA` inside `national` is not the region NA.
    asked = normalize_question("national revenue")
    assert mask_declared_values(asked, template()) == "national revenue"


def test_the_longest_declared_value_wins() -> None:
    param = TemplateParam(name="region", comment="one of: NORTH AMERICA, NORTH")
    asked = normalize_question("revenue for north america")
    assert mask_declared_values(asked, template(params=[param])) == "revenue for *"


# ── the threshold ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "question",
    [
        "revenue by month for EMEA in 2026",
        "Revenue by month for emea in 2025?",
        "revenue by month for APAC in 2024",
    ],
)
def test_the_question_the_template_was_written_for_short_circuits(
    question: str,
) -> None:
    assert score_against(normalize_question(question), template()) >= (
        SHORT_CIRCUIT_THRESHOLD
    )


@pytest.mark.parametrize(
    "question",
    [
        "revenue by store for EMEA in 2026",
        "orders by month for EMEA in 2026",
        "how many customers do we have",
        "revenue",
    ],
)
def test_a_different_question_does_not(question: str) -> None:
    assert score_against(normalize_question(question), template()) < (
        SHORT_CIRCUIT_THRESHOLD
    )


def test_the_two_thresholds_are_different_numbers() -> None:
    # They exist for different jobs — answering versus exemplifying — and
    # collapsing them into one would make Phase 5 either useless or dangerous.
    assert FEW_SHOT_THRESHOLD < SHORT_CIRCUIT_THRESHOLD


async def test_best_returns_nothing_for_a_near_miss() -> None:
    """A near-miss is not a hit.

    Every caller of `best()` is about to answer a person, and "close" is not a
    category an answer can be in.
    """
    found = await matcher(template()).match("revenue by store in 2026", CONNECTION)
    assert found and found[0].score < SHORT_CIRCUIT_THRESHOLD
    assert best(found) is None


async def test_best_returns_nothing_for_an_empty_store() -> None:
    assert best(await matcher().match("revenue", CONNECTION)) is None


async def test_an_empty_question_matches_nothing() -> None:
    assert await matcher(template()).match("   ", CONNECTION) == []


# ── the three exclusions ─────────────────────────────────────────────────
@pytest.mark.parametrize("role", [TemplateRole.BENCHMARK_ONLY, TemplateRole.HELD_OUT])
async def test_a_measuring_template_never_answers(role: TemplateRole) -> None:
    """§1.3's measurement trap, enforced rather than commented.

    A held-out question answered from its own stored SQL measures nothing — and
    it looks perfect while doing it, which is why the exclusion is asserted
    here and in the query, not left to a convention.
    """
    found = await matcher(template(role=role)).match(
        "revenue by month for EMEA in 2026", CONNECTION
    )
    assert found == []


@pytest.mark.parametrize(
    "status",
    [TemplateStatus.STALE, TemplateStatus.CONFLICTED, TemplateStatus.ARCHIVED],
)
async def test_a_template_that_is_not_active_never_answers(
    status: TemplateStatus,
) -> None:
    found = await matcher(template(status=status)).match(
        "revenue by month for EMEA in 2026", CONNECTION
    )
    assert found == []


# ── ordering ─────────────────────────────────────────────────────────────
async def test_the_best_score_comes_first() -> None:
    exact = template("revenue by month for {region} in {year}")
    other = template("orders by month for {region} in {year}")
    found = await matcher(other, exact).match(
        "revenue by month for EMEA in 2026", CONNECTION
    )
    assert found[0].template.id == exact.id


async def test_a_tie_goes_to_the_template_people_have_actually_used() -> None:
    # A tie is a store that needs pruning, not a coin to flip — and until
    # somebody prunes it, the one that has answered questions before is the
    # better guess.
    cold = template("revenue by month for {region} in {year}", hits=0)
    warm = template("revenue by month for {region} in {year}", hits=31)
    found = await matcher(cold, warm).match(
        "revenue by month for EMEA in 2026", CONNECTION
    )
    assert found[0].template.id == warm.id


async def test_the_matcher_reports_which_matcher_it_was() -> None:
    # The badge, the hit log and the trace line all read this, and Phase 7 adds
    # a second value to it.
    found = await matcher(template()).match(
        "revenue by month for EMEA in 2026", CONNECTION
    )
    assert found[0].matcher == "LEXICAL"


async def test_the_limit_is_honoured() -> None:
    many = [template(f"revenue by month for {{region}} in {{year}} {i}") for i in range(9)]
    assert len(await matcher(*many).match("revenue by month", CONNECTION, limit=3)) == 3


# ── the question is normalised exactly once, by one function ─────────────
async def test_the_asker_and_the_store_normalise_the_same_way() -> None:
    """One function, called from both sides.

    A match key computed two ways is a match key that stops matching the day
    somebody edits one of them — and the symptom is silence, which nobody
    reports as a bug.
    """
    stored = template("revenue by month for {region} in {year}")
    assert stored.question_normalized == normalize_question(stored.question)

    found = await matcher(stored).match(
        "REVENUE BY MONTH FOR EMEA IN 2026???", CONNECTION
    )
    assert best(found) is not None
