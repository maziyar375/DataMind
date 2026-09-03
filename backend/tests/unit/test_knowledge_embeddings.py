"""The embedding matcher — a swap, not a rewrite.

Phase 7 of `docs/learning-loop-plan.md`. What is actually being claimed here,
in the order it would hurt if it broke:

* **the loop degrades to lexical, never to nothing** — every way the embedding
  half can fail (no model pinned, no fresh vector, a provider that is down, a
  provider that changed width) ends with the lexical matcher answering, and
  none of them raises;
* **masking is the whole idea** — a question about July and West retrieves a
  template written for March and East, and it does so using only information
  the schema or a *curator* supplied;
* **staleness is derived, never tracked** — a template edit, a schema re-sync
  and a model change each invalidate exactly what they should, because the
  fingerprint is a hash of the three inputs and not a flag somebody sets;
* **nothing is measured twice** — an embedding arm that quietly fell back to
  lexical must not be reportable as an embedding run.

There is no test here that asserts embeddings *improve* anything. That is not a
unit test's job and §3.8 says so: FK-neighbour expansion once lifted retrieval
recall 70% → 86% with flat execution accuracy, and the only honest instrument
for the question is the eval arm running against a real provider.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from uuid import uuid4

import pytest

from app.knowledge.embed import (
    COLUMN_MASK,
    MIN_TERM_CHARS,
    SIMILARITY_FLOOR,
    TABLE_MASK,
    VALUE_MASK,
    EmbeddingMatcher,
    VectorEntry,
    VectorIndex,
    Vocabulary,
    cosine,
    fingerprint,
    mask_question,
    needs_embedding,
    to_index,
)
from app.knowledge.matcher import (
    EMBEDDING,
    LEXICAL,
    SHORT_CIRCUIT_THRESHOLD,
    Candidate,
    FallbackMatcher,
)
from app.knowledge.models import (
    KnowledgeTemplate,
    TemplateParam,
    TemplateRole,
    TemplateStatus,
)

SNAPSHOT = {
    "tables": [
        {
            "schema": "public",
            "name": "orders",
            "columns": [
                {"name": "id"},
                {"name": "region"},
                {"name": "order_date"},
                {"name": "total_amount"},
            ],
        },
        {
            "schema": "public",
            "name": "order_items",
            "columns": [{"name": "quantity"}, {"name": "unit_price"}],
        },
    ]
}

CONNECTION = uuid4()


def template(
    question: str,
    *,
    values: str = "",
    status: TemplateStatus = TemplateStatus.ACTIVE,
    role: TemplateRole = TemplateRole.RETRIEVABLE,
    hits: int = 0,
) -> KnowledgeTemplate:
    params = (
        [TemplateParam(name="region", comment=f"one of: {values}")] if values else []
    )
    return KnowledgeTemplate(
        id=uuid4(),
        question=question,
        question_normalized=question.casefold(),
        sql="SELECT 1",
        params=params,
        status=status,
        role=role,
        hit_count=hits,
    )


def vector(*values: float) -> list[float]:
    return list(values)


def index_of(
    entries: list[VectorEntry], *, model: str = "m", dimension: int = 3
) -> VectorIndex:
    """An index whose fingerprints are all correct — the healthy case."""
    vocabulary = Vocabulary.from_snapshot(
        SNAPSHOT, [entry.template for entry in entries]
    )
    return VectorIndex(
        vocabulary=vocabulary,
        model=model,
        dimension=dimension,
        entries=[
            VectorEntry(
                template=entry.template,
                vector=entry.vector,
                stored_fingerprint=fingerprint(
                    mask_question(entry.template.question, vocabulary), model, dimension
                ),
            )
            for entry in entries
        ],
    )


def matcher_over(index: VectorIndex, asked: list[float]) -> EmbeddingMatcher:
    async def embed(_texts: object) -> list[list[float]]:
        return [asked]

    async def source(_connection_id: object) -> VectorIndex:
        return index

    return EmbeddingMatcher(embed, source)


# ── the vocabulary ───────────────────────────────────────────────────────
def test_the_vocabulary_reads_tables_and_columns_off_a_plain_dict() -> None:
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert "orders" in v.tables
    assert "order_items" in v.tables
    assert "region" in v.columns
    assert not v.is_empty


def test_an_identifier_is_known_by_both_of_its_spellings() -> None:
    """`order_items` is written "order items" in every question anybody asks."""
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert "order_items" in v.tables
    assert "order items" in v.tables


def test_a_short_name_is_never_masked() -> None:
    """`id` is a column in most databases and a word in most questions.

    Masking it would delete the sentence rather than generalise it, which is
    the failure mode the whole module is written against.
    """
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert "id" not in v.columns
    assert all(len(name) >= MIN_TERM_CHARS for name in v.columns | v.tables)


def test_a_name_that_is_both_a_table_and_a_column_is_a_table() -> None:
    snapshot = {
        "tables": [
            {"name": "region", "columns": [{"name": "region"}, {"name": "label"}]}
        ]
    }
    v = Vocabulary.from_snapshot(snapshot)
    assert "region" in v.tables
    assert "region" not in v.columns


def test_no_snapshot_is_an_empty_vocabulary_and_not_an_exception() -> None:
    assert Vocabulary.from_snapshot(None).is_empty
    assert Vocabulary.from_snapshot({}).is_empty
    assert Vocabulary.from_snapshot({"tables": None}).is_empty


def test_the_vocabulary_carries_the_values_a_curator_declared() -> None:
    """The piece without which the canonical example does not work.

    `EMEA` is a word, not a detectable literal — the same problem
    `mask_declared_values` solves for the lexical matcher, solved with the same
    information.
    """
    t = template("revenue for {region}", values="EMEA, APAC, LATAM")
    v = Vocabulary.from_snapshot(SNAPSHOT, [t])
    assert "emea" in v.values
    assert "apac" in v.values


def test_a_declared_value_too_short_to_be_a_word_is_not_in_the_vocabulary() -> None:
    """`NA` is two characters and appears inside ordinary English."""
    t = template("revenue for {region}", values="EMEA, NA")
    assert "na" not in Vocabulary.from_snapshot(SNAPSHOT, [t]).values


def test_a_declared_value_that_is_also_a_schema_name_stays_a_schema_name() -> None:
    t = template("revenue for {region}", values="orders, EMEA")
    v = Vocabulary.from_snapshot(SNAPSHOT, [t])
    assert "orders" in v.tables
    assert "orders" not in v.values


# ── masking ──────────────────────────────────────────────────────────────
def test_two_askings_collapse_to_one_string_when_both_slots_are_declared() -> None:
    """§3.8's own example, with the curator having said what the slots hold."""
    t = KnowledgeTemplate(
        id=uuid4(),
        question="revenue in {month} for {region}",
        params=[
            TemplateParam(name="region", comment="one of: West, East, North"),
            TemplateParam(name="month", comment="one of: July, March, August"),
        ],
    )
    v = Vocabulary.from_snapshot(SNAPSHOT, [t])
    assert mask_question("revenue in July for West", v) == mask_question(
        "revenue in March for East", v
    )


def test_masking_removes_what_it_can_prove_and_leaves_the_rest_to_the_model() -> None:
    """The honest division of labour, and the reason this phase is embeddings.

    With only `{region}` declared, the two askings still differ by a month
    name — masking removes the difference a *curator* declared to be a value
    and nothing else. What is left is one word of ordinary English inside two
    otherwise identical sentences, which is precisely the residue an embedding
    model handles and a trigram matcher does not: the same pair scores under
    the lexical short-circuit threshold.
    """
    from app.knowledge.matcher import (
        SHORT_CIRCUIT_THRESHOLD as LEXICAL_GATE,
    )
    from app.knowledge.matcher import (
        trigram_similarity,
    )

    t = template("revenue in {month} for {region}", values="West, East, North")
    v = Vocabulary.from_snapshot(SNAPSHOT, [t])
    left = mask_question("revenue in July for West", v)
    right = mask_question("revenue in March for East", v)

    assert "west" not in left and "east" not in right
    assert left.replace("july", "") == right.replace("march", "")
    assert trigram_similarity(left, right) < LEXICAL_GATE


def test_a_template_and_the_question_it_was_written_for_agree() -> None:
    t = template("revenue by region for {region}", values="EMEA, APAC")
    v = Vocabulary.from_snapshot(SNAPSHOT, [t])
    assert mask_question(t.question, v) == mask_question(
        "revenue by region for EMEA", v
    )


def test_a_table_and_a_column_get_different_tokens() -> None:
    """`revenue by <column>` and `revenue by <table>` are different questions.

    Folding both to one token would teach the matcher to confuse a grouping
    with a source, which is a worse error than the one masking fixes.
    """
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert TABLE_MASK in mask_question("rows in orders", v)
    assert COLUMN_MASK in mask_question("group by region", v)
    assert mask_question("count of orders", v) != mask_question("count of region", v)


def test_a_literal_is_masked_by_the_same_function_the_match_key_uses() -> None:
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert mask_question("top 10 stores", v) == mask_question("top 25 stores", v)
    assert VALUE_MASK in mask_question("sales in 2026", v)


def test_a_partial_word_is_never_masked() -> None:
    """Masking the `order` inside `reorder` invents a difference."""
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert "reordered" in mask_question("how many reordered items", v)


def test_a_question_naming_nothing_in_the_schema_survives_intact() -> None:
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert mask_question("how many customers do we have?", v) == (
        "how many customers do we have?"
    )


def test_a_run_of_masks_collapses_to_one() -> None:
    """"top <value> <value> stores" and "top <value> stores" differ over
    nothing anybody typed."""
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert VALUE_MASK * 2 not in mask_question("sales between 2024 and 2025", v)


def test_masking_is_total() -> None:
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert mask_question("", v) == ""
    assert mask_question("   ", v) == ""


# ── the fingerprint, which is the whole staleness rule ───────────────────
def test_the_same_inputs_fingerprint_the_same() -> None:
    assert fingerprint("a question", "m", 3) == fingerprint("a question", "m", 3)


def test_editing_the_question_invalidates_its_vector() -> None:
    assert fingerprint("revenue by month", "m", 3) != fingerprint(
        "revenue by week", "m", 3
    )


def test_changing_the_model_invalidates_every_vector() -> None:
    assert fingerprint("q", "small", 3) != fingerprint("q", "large", 3)


def test_changing_the_width_invalidates_every_vector() -> None:
    assert fingerprint("q", "m", 768) != fingerprint("q", "m", 1536)


def test_a_schema_resync_invalidates_the_vectors_it_should() -> None:
    """The third invalidation, and the one nobody writes down.

    The mask reads the schema's own names, so a table that was renamed changes
    the masked text of every question that mentioned it — and only those.
    """
    before = Vocabulary.from_snapshot(SNAPSHOT)
    after = Vocabulary.from_snapshot(
        {"tables": [{"name": "purchases", "columns": [{"name": "region"}]}]}
    )
    moved = "how many orders last month"
    untouched = "group by region"

    assert fingerprint(mask_question(moved, before), "m", 3) != fingerprint(
        mask_question(moved, after), "m", 3
    )
    assert fingerprint(mask_question(untouched, before), "m", 3) == fingerprint(
        mask_question(untouched, after), "m", 3
    )


def test_needs_embedding_returns_the_text_it_decided_from() -> None:
    """Not a boolean: the caller needs the text either way, and computing the
    mask twice is how the decision and the payload drift apart."""
    t = template("revenue by region")
    v = Vocabulary.from_snapshot(SNAPSHOT)
    masked = needs_embedding(t, "", 0, v, "m", 3)
    assert masked == mask_question(t.question, v)


def test_a_current_vector_needs_nothing() -> None:
    t = template("revenue by region")
    v = Vocabulary.from_snapshot(SNAPSHOT)
    current = fingerprint(mask_question(t.question, v), "m", 3)
    assert needs_embedding(t, current, 3, v, "m", 3) == ""


def test_a_right_fingerprint_at_the_wrong_width_still_needs_embedding() -> None:
    """The stored vector is 768 wide against a 1536 pin — a store half indexed
    by each model is a store where cosine means nothing."""
    t = template("revenue by region")
    v = Vocabulary.from_snapshot(SNAPSHOT)
    current = fingerprint(mask_question(t.question, v), "m", 3)
    assert needs_embedding(t, current, 2, v, "m", 3) != ""


def test_nothing_needs_embedding_when_no_model_is_pinned() -> None:
    t = template("revenue by region")
    v = Vocabulary.from_snapshot(SNAPSHOT)
    assert needs_embedding(t, "", 0, v, "", 0) == ""
    assert needs_embedding(t, "", 0, v, "m", 0) == ""


# ── cosine ───────────────────────────────────────────────────────────────
def test_identical_vectors_score_one() -> None:
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_orthogonal_vectors_score_zero() -> None:
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_a_negative_cosine_is_clamped_rather_than_shifted() -> None:
    """Every threshold in `matcher.py` reads as a fraction of a match."""
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == 0.0


def test_mismatched_widths_score_zero_rather_than_raising() -> None:
    """A person asking about revenue must not get an exception because the
    store was half indexed by another model."""
    assert cosine([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


def test_a_zero_vector_scores_zero() -> None:
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0


# ── the index ────────────────────────────────────────────────────────────
def test_an_index_with_no_model_is_not_available() -> None:
    assert not VectorIndex().is_available
    assert VectorIndex(model="m", dimension=3).is_available
    assert not VectorIndex(model="m", dimension=0).is_available


def test_a_stale_vector_is_ignored_and_not_deleted() -> None:
    t = template("revenue by region")
    index = VectorIndex(
        vocabulary=Vocabulary.from_snapshot(SNAPSHOT, [t]),
        model="m",
        dimension=3,
        entries=[VectorEntry(template=t, vector=vector(1, 0, 0), stored_fingerprint="x")],
    )
    assert index.usable() == []
    # Still there. The next pass replaces it; nothing was thrown away.
    assert len(index.entries) == 1


def test_a_vector_with_no_fingerprint_is_never_usable() -> None:
    t = template("revenue by region")
    index = VectorIndex(
        vocabulary=Vocabulary.from_snapshot(SNAPSHOT, [t]),
        model="m",
        dimension=3,
        entries=[VectorEntry(template=t, vector=vector(1, 0, 0))],
    )
    assert index.usable() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": TemplateStatus.STALE},
        {"status": TemplateStatus.CONFLICTED},
        {"status": TemplateStatus.ARCHIVED},
        {"role": TemplateRole.HELD_OUT},
        {"role": TemplateRole.BENCHMARK_ONLY},
    ],
)
def test_an_unmatchable_template_is_dropped_even_with_a_fresh_vector(
    kwargs: dict,
) -> None:
    """`is_matchable` is asked here as well as in the query, for the reason
    `LexicalMatcher` asks it twice: neither failure is visible from outside."""
    t = template("revenue by region", **kwargs)
    index = index_of([VectorEntry(template=t, vector=vector(1, 0, 0))])
    assert index.usable() == []


def test_to_index_builds_the_vocabulary_from_the_entries_own_templates() -> None:
    """Handing that job to the caller would make it two readings that have to
    agree, which is one more than can be relied on."""
    t = template("revenue for {region}", values="EMEA, APAC")
    index = to_index(SNAPSHOT, "m", 3, [VectorEntry(template=t, vector=vector(1, 0, 0))])
    assert "emea" in index.vocabulary.values


# ── the matcher ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_nearest_template_wins_and_is_labelled_embedding() -> None:
    near = template("revenue by region")
    far = template("how many staff joined")
    index = index_of(
        [
            VectorEntry(template=near, vector=vector(1, 0, 0)),
            VectorEntry(template=far, vector=vector(0, 1, 0)),
        ]
    )
    found = await matcher_over(index, vector(1, 0, 0)).match("anything", CONNECTION)

    assert [c.template.id for c in found] == [near.id]
    assert found[0].matcher == EMBEDDING
    assert found[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_a_score_below_the_floor_is_not_a_candidate_at_all() -> None:
    t = template("revenue by region")
    index = index_of([VectorEntry(template=t, vector=vector(1, 0, 0))])
    # Cosine ≈ 0.196, well under the floor: two unrelated English sentences
    # score around 0.3 on a real model, so a floor near zero filters nothing.
    assert await matcher_over(index, vector(1, 5, 0)).match("q", CONNECTION) == []
    assert SIMILARITY_FLOOR > 0.3


@pytest.mark.asyncio
async def test_a_tie_goes_to_the_template_people_have_actually_used() -> None:
    """The same tie-break `LexicalMatcher` uses, and deliberately the same:
    two matchers ranking ties differently would make the Phase 7 recall delta
    partly a measurement of the tie-break."""
    unused = template("revenue by region", hits=0)
    used = template("revenue per region", hits=9)
    index = index_of(
        [
            VectorEntry(template=unused, vector=vector(1, 0, 0)),
            VectorEntry(template=used, vector=vector(1, 0, 0)),
        ]
    )
    found = await matcher_over(index, vector(1, 0, 0)).match("q", CONNECTION)
    assert found[0].template.id == used.id


@pytest.mark.asyncio
async def test_a_high_enough_cosine_short_circuits_on_the_same_threshold() -> None:
    """One threshold for both matchers. Two would mean the short-circuit was
    trusted differently depending on how it was retrieved."""
    t = template("revenue by region")
    index = index_of([VectorEntry(template=t, vector=vector(1, 0, 0))])
    found = await matcher_over(index, vector(1, 0, 0)).match("q", CONNECTION)
    assert found[0].short_circuits
    assert found[0].score >= SHORT_CIRCUIT_THRESHOLD


@pytest.mark.asyncio
async def test_an_index_with_no_model_returns_nothing_rather_than_raising() -> None:
    assert await matcher_over(VectorIndex(), vector(1, 0, 0)).match(
        "q", CONNECTION
    ) == []


@pytest.mark.asyncio
async def test_a_provider_that_changed_width_says_nothing() -> None:
    """Every stored vector is now incomparable. The honest move is silence and
    a lexical answer until the next pass re-pins the model."""
    t = template("revenue by region")
    index = index_of([VectorEntry(template=t, vector=vector(1, 0, 0))])
    assert await matcher_over(index, vector(1, 0)).match("q", CONNECTION) == []


@pytest.mark.asyncio
async def test_a_question_that_masks_to_nothing_is_not_embedded() -> None:
    """No provider call for a question there is nothing to ask about."""
    calls: list[object] = []

    async def embed(texts: object) -> list[list[float]]:
        calls.append(texts)
        return [vector(1, 0, 0)]

    async def source(_c: object) -> VectorIndex:
        return index_of([VectorEntry(template=template("revenue"), vector=vector(1, 0, 0))])

    assert await EmbeddingMatcher(embed, source).match("   ", CONNECTION) == []
    assert calls == []


@pytest.mark.asyncio
async def test_an_empty_index_never_reaches_the_provider() -> None:
    """The state every connection is in until the first pass runs — and the one
    where an embedding call would be spent to learn nothing."""
    calls: list[object] = []

    async def embed(texts: object) -> list[list[float]]:
        calls.append(texts)
        return [vector(1, 0, 0)]

    async def source(_c: object) -> VectorIndex:
        return VectorIndex(model="m", dimension=3)

    assert await EmbeddingMatcher(embed, source).match("q", CONNECTION) == []
    assert calls == []


# ── the fallback: degrade to lexical, never to nothing ───────────────────
class _Fake:
    def __init__(self, found: list[Candidate] | None = None, raises: bool = False):
        self.found = found or []
        self.raises = raises
        self.called = 0

    async def match(self, _q: str, _c: object, *, limit: int = 5) -> list[Candidate]:
        self.called += 1
        if self.raises:
            raise RuntimeError("the embedding endpoint is down")
        return self.found


def _candidate(matcher: str) -> Candidate:
    return Candidate(template=template("revenue"), score=0.9, matcher=matcher)


@pytest.mark.asyncio
async def test_the_primary_answers_when_it_can() -> None:
    primary, fallback = _Fake([_candidate(EMBEDDING)]), _Fake([_candidate(LEXICAL)])
    found = await FallbackMatcher(primary, fallback).match("q", CONNECTION)

    assert found[0].matcher == EMBEDDING
    assert fallback.called == 0


@pytest.mark.asyncio
async def test_an_empty_primary_falls_back() -> None:
    primary, fallback = _Fake([]), _Fake([_candidate(LEXICAL)])
    found = await FallbackMatcher(primary, fallback).match("q", CONNECTION)
    assert found[0].matcher == LEXICAL


@pytest.mark.asyncio
async def test_a_primary_that_raises_falls_back_rather_than_failing_a_question() -> None:
    """A person asking about revenue must not lose their answer to a feature
    that is meant to be invisible when absent."""
    primary, fallback = _Fake(raises=True), _Fake([_candidate(LEXICAL)])
    found = await FallbackMatcher(primary, fallback).match("q", CONNECTION)

    assert found[0].matcher == LEXICAL
    assert fallback.called == 1


@pytest.mark.asyncio
async def test_both_empty_is_a_miss_and_not_an_error() -> None:
    assert await FallbackMatcher(_Fake(), _Fake()).match("q", CONNECTION) == []


@pytest.mark.asyncio
async def test_the_limit_reaches_both_halves() -> None:
    class _Recording(_Fake):
        limits: list[int] = []

        async def match(self, _q: str, _c: object, *, limit: int = 5) -> list[Candidate]:
            type(self).limits.append(limit)
            return await super().match(_q, _c, limit=limit)

    _Recording.limits = []
    await FallbackMatcher(_Recording(), _Recording()).match("q", CONNECTION, limit=2)
    assert _Recording.limits == [2, 2]


# ── the contracts that keep the phase honest ─────────────────────────────
def test_embed_holds_no_second_opinion_about_what_a_literal_is() -> None:
    """`mask_question` calls `mask_literals`; it does not re-implement it.

    Two readings of "what is a literal" is how the match key and the embedding
    key drift apart after somebody edits one regex.
    """
    source = inspect.getsource(mask_question)
    assert "mask_literals" in source
    assert "re.compile" not in source


def test_the_pure_module_imports_no_infrastructure() -> None:
    """`app.knowledge` is self-contained, and Phase 7 did not spend that.

    Asserted on the parse rather than on the text, because the docstring
    mentions LiteLLM by name and a substring check would read that as an
    import.
    """
    tree = ast.parse(Path("app/knowledge/embed.py").read_text())
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    for banned in ("litellm", "sqlalchemy", "fastapi"):
        assert not any(name.startswith(banned) for name in imported), banned
    for banned in ("app.infra", "app.services", "app.api", "app.pipeline"):
        assert not any(name.startswith(banned) for name in imported), banned


def test_the_two_matchers_agree_on_the_thresholds() -> None:
    """Phase 7 added a matcher, not a second policy. If these ever diverge, a
    template is trusted differently depending on how it was found — and the
    reader has no way of telling which happened."""
    from app.knowledge import matcher as m

    assert m.SHORT_CIRCUIT_THRESHOLD == 0.85
    assert m.FEW_SHOT_THRESHOLD == 0.45
    # The embedding floor is a shortlist, never a decision, so it sits below
    # both — a candidate that reaches the matcher is still judged by them.
    assert SIMILARITY_FLOOR < m.SHORT_CIRCUIT_THRESHOLD
