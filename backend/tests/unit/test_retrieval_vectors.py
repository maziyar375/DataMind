"""What a table's prose *means* reaches retrieval — mvp2 B2, Phase 2.

`docs/plans/hybrid-retrieval.md`. Phase 1 asks whether a table's prose uses the
question's words. This asks whether it means what the question means, which is
the one thing no lexical score can do: *"how many people stopped paying?"*
against a comment reading *"a cancellation the customer chose, not a failed
payment"* shares not one content word.

The property this file exists for is the **fail-open**. Six things can go
wrong on the way to a vector score and every one of them has to come back as
the Phase 1 score with the question unaffected — a retrieval feature that can
fail a question is worse than one that is absent. They are one test each.

No provider and no database: the embedder is a callable and the index is a
dataclass, which is the whole return on keeping the arithmetic in
`app/pipeline/relevance.py` and the I/O in `app/services/retrieval_index.py`.
"""
from __future__ import annotations

import asyncio
import importlib
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.clock import utcnow
from app.infra.db.models import Base
from app.pipeline import nodes
from app.pipeline.nodes import NodeDeps, retrieve
from app.pipeline.relevance import (
    VECTOR_FLOOR,
    TableVector,
    VectorIndex,
    blend,
    cosines,
    prose_by_table,
    prose_fingerprint,
    rescale,
)
from app.pipeline.state import RunState
from tests.unit.test_dashboard_models import OpRecorder
from tests.unit.test_retrieval_prose import RELS, SNAPSHOT, table

MIGRATION = importlib.import_module(
    "app.infra.db.migrations.versions.0039_schema_table_vectors"
)

MODEL = "text-embedding-3-small"
DIM = 4


# ── the store ────────────────────────────────────────────────────────────


def _replay(direction: str) -> OpRecorder:
    recorder = OpRecorder()
    original = MIGRATION.op
    try:
        MIGRATION.op = recorder
        getattr(MIGRATION, direction)()
    finally:
        MIGRATION.op = original
    return recorder


def test_the_revision_follows_0038() -> None:
    assert (MIGRATION.revision, MIGRATION.down_revision) == ("0039", "0038")


def test_the_migration_and_the_orm_agree_column_for_column() -> None:
    recorded = _replay("upgrade").tables["schema_table_vectors"]
    orm = Base.metadata.tables["schema_table_vectors"]
    assert {c.name for c in recorded.columns} == {c.name for c in orm.columns}
    for column in recorded.columns:
        mirror = orm.columns[column.name]
        assert column.nullable == mirror.nullable, column.name
        assert {fk.target_fullname for fk in column.foreign_keys} == {
            fk.target_fullname for fk in mirror.foreign_keys
        }, column.name


def test_a_vector_dies_with_its_connection() -> None:
    fk = next(
        iter(Base.metadata.tables["schema_table_vectors"].c.connection_id.foreign_keys)
    )
    assert fk.ondelete == "CASCADE"


def test_the_vector_is_an_array_not_a_pgvector_column() -> None:
    """mvp2 §B2 proposed pgvector; learning-loop D3 answered the same question
    for the same kind of index first, and `postgres:16-alpine` does not carry
    the extension. Two different answers to one question would be worse than
    either."""
    column = Base.metadata.tables["schema_table_vectors"].c.embedding
    assert isinstance(column.type, sa.ARRAY)
    assert column.nullable, "NULL is *not measured*, never the zero vector"


def test_one_row_per_table_per_connection() -> None:
    up = _replay("upgrade")
    constraints = up.tables["schema_table_vectors"].constraints
    assert any(
        getattr(c, "name", "") == "uq_schema_table_vectors_table" for c in constraints
    )


def test_the_downgrade_removes_what_the_upgrade_added() -> None:
    down = _replay("downgrade")
    assert down.dropped_tables == ["schema_table_vectors"]
    assert down.dropped_indexes == ["ix_schema_table_vectors_connection"]


# ── the fingerprint ──────────────────────────────────────────────────────


def test_the_fingerprint_moves_when_any_of_its_three_inputs_does() -> None:
    """Staleness is derived from this and nothing else, so each of the three
    has to be in it: an edited description, a re-pinned model, a changed
    width."""
    base = prose_fingerprint(("one row per order",), MODEL, DIM)
    assert base != prose_fingerprint(("one row per invoice",), MODEL, DIM)
    assert base != prose_fingerprint(("one row per order",), "other-model", DIM)
    assert base != prose_fingerprint(("one row per order",), MODEL, DIM + 1)
    assert base == prose_fingerprint(("one row per order",), MODEL, DIM)


def test_the_order_of_the_sentences_is_part_of_it() -> None:
    """`table_text` returns a fixed order — comments, then the layer — so two
    runs over one snapshot agree. A fingerprint that ignored order would call a
    reordered bag current when the text it embeds is a different string."""
    assert prose_fingerprint(("a", "b"), MODEL, DIM) != prose_fingerprint(
        ("b", "a"), MODEL, DIM
    )


# ── the index ────────────────────────────────────────────────────────────


def _index(**kw: Any) -> VectorIndex:
    async def embed(texts: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    defaults: dict[str, Any] = {"model": MODEL, "dimension": DIM, "embed": embed}
    return VectorIndex(**{**defaults, **kw})


def _entry(sentences: tuple[str, ...], vector: tuple[float, ...]) -> TableVector:
    return TableVector(
        vector=vector,
        stored_fingerprint=prose_fingerprint(sentences, MODEL, DIM),
    )


TEXTS = {"public.orders": ("one header per checkout",)}
FRESH = _entry(TEXTS["public.orders"], (1.0, 0.0, 0.0, 0.0))


def test_a_vector_that_still_stands_for_its_prose_is_usable() -> None:
    index = _index(tables={"public.orders": FRESH})
    assert index.usable(TEXTS) == {"public.orders": (1.0, 0.0, 0.0, 0.0)}


def test_a_vector_whose_prose_moved_is_ignored_not_deleted() -> None:
    """The next pass overwrites it; until then this table is scored on words
    like any other. So a schema re-sync degrades the index table by table, and
    never wrongly — a vector is either current or absent, never quietly
    describing last month's comment."""
    index = _index(tables={"public.orders": FRESH})
    assert index.usable({"public.orders": ("somebody edited the comment",)}) == {}
    # And the entry is still there to be overwritten.
    assert index.tables["public.orders"] is FRESH


def test_a_vector_of_the_wrong_width_is_not_used() -> None:
    """Two widths in one index is a mispinned model, and cosine across them
    means nothing. The fingerprint carries the dimension, so this is the second
    door rather than the first."""
    narrow = TableVector(
        vector=(1.0, 0.0),
        stored_fingerprint=prose_fingerprint(TEXTS["public.orders"], MODEL, DIM),
    )
    assert _index(tables={"public.orders": narrow}).usable(TEXTS) == {}


def test_a_table_with_no_prose_wants_no_vector() -> None:
    """There is nothing to embed, and a vector of the empty string would sit
    equally close to every question ever asked."""
    index = _index(tables={"public.orders": FRESH})
    assert index.usable({"public.orders": ()}) == {}


def test_an_index_with_no_model_is_a_state_not_an_error() -> None:
    assert not VectorIndex().is_available
    assert VectorIndex().usable(TEXTS) == {}
    assert not _index(model="").is_available
    assert not _index(dimension=0).is_available
    assert not _index(embed=None).is_available


# ── the arithmetic ───────────────────────────────────────────────────────


def test_a_cosine_under_the_floor_is_not_evidence() -> None:
    """Two unrelated English sentences score around 0.3 on most models, so the
    bottom of the range carries no information."""
    assert rescale(VECTOR_FLOOR) == 0.0
    assert rescale(VECTOR_FLOOR - 0.2) == 0.0
    assert rescale(1.0) == 1.0
    assert 0.0 < rescale((VECTOR_FLOOR + 1.0) / 2) < 1.0


def test_the_blend_takes_whichever_evidence_is_stronger() -> None:
    assert blend({"a": 0.9}, {"a": 1.0}) == {"a": 1.0}
    assert blend({}, {"b": 1.0}) == {"b": 1.0}


def test_a_vector_can_add_a_table_and_can_never_remove_one() -> None:
    """`max`, not a weighted sum — argued in `relevance.blend`. A weighted sum
    needs a weight, and the only honest way to choose one is a measurement this
    feature still owes. It also keeps the arm interpretable: turning embeddings
    on can only add."""
    lexical = {"public.orders": 0.8}
    out = blend(lexical, {"public.orders": VECTOR_FLOOR, "public.customers": 1.0})
    assert out["public.orders"] == 0.8
    assert out["public.customers"] == 1.0


def test_no_cosines_is_the_lexical_score_itself() -> None:
    lexical = {"public.orders": 0.4}
    assert blend(lexical, {}) == lexical


def test_two_vectors_of_different_width_are_not_similar_rather_than_an_error() -> None:
    """A bug upstream, and the honest answer to "how similar are they" is *not
    at all* — not an exception thrown at somebody asking about revenue."""
    assert cosines([1.0, 0.0], {"a": (1.0, 0.0, 0.0)}) == {"a": 0.0}


def test_no_question_vector_is_no_cosines() -> None:
    assert cosines([], {"a": (1.0, 0.0)}) == {}


# ── the node ─────────────────────────────────────────────────────────────


def _wide(n: int = 60) -> dict[str, Any]:
    """The Phase 1 helper's snapshot, with the real tables' comments intact."""
    wide_cols = tuple(f"c{j}" for j in range(20))
    tables = [
        *(table(f"filler_{i:02d}", *wide_cols) for i in range(n)),
        *(
            table(
                t["name"],
                *(c["name"] for c in t["columns"]),
                *wide_cols,
                comment=t["comment"],
            )
            for t in SNAPSHOT
        ),
    ]
    assert sum(60 + 40 * len(t["columns"]) for t in tables) > nodes._RETRIEVE_BUDGET_CHARS
    return {"tables": tables, "relationships": RELS}


def _run(question: str) -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.intent = "ANALYTICAL"  # type: ignore[assignment]
    return state


def _deps(snapshot: dict[str, Any], vectors: VectorIndex) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=snapshot, history=[], policy=None,  # type: ignore[arg-type]
        emit=emit, include_db_comments=True, vectors=vectors,
    )


def _aligned_index(
    snapshot: dict[str, Any], *, target: str, vector: tuple[float, ...]
) -> VectorIndex:
    """An index whose every vector is current, and one of them points at the
    question's own direction while the rest point away."""
    texts = prose_by_table(snapshot["tables"])
    tables = {
        key: _entry(sentences, vector if key == target else (0.0, 0.0, 0.0, 1.0))
        for key, sentences in texts.items()
        if sentences
    }

    async def embed(asked: Any) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in asked]

    return VectorIndex(model=MODEL, dimension=DIM, tables=tables, embed=embed)


async def _selected(question: str, vectors: VectorIndex) -> list[str]:
    state = _run(question)
    await retrieve(state, _deps(_wide(), vectors))
    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    return [t["name"] for t in state.context.tables]


@pytest.mark.asyncio
async def test_a_vector_reaches_a_table_no_word_in_the_question_names() -> None:
    """The headline. Nothing in "how many people stopped paying?" appears in
    `order_items`' comment, in its name, or in any column of it — the lexical
    score for it is 0.0 and the budget goes to the fillers. With a vector that
    says the two mean the same thing, the table is in the block."""
    question = "how many people stopped paying?"
    snapshot = _wide()
    aligned = _aligned_index(
        snapshot, target="public.order_items", vector=(1.0, 0.0, 0.0, 0.0)
    )
    assert "order_items" in await _selected(question, aligned)
    assert "order_items" not in await _selected(question, VectorIndex())


@pytest.mark.asyncio
async def test_no_index_is_the_run_exactly_as_phase_1_ran_it() -> None:
    question = "how many people stopped paying?"
    assert await _selected(question, VectorIndex()) == await _selected(
        question, VectorIndex(model=MODEL, dimension=DIM)
    )


@pytest.mark.parametrize(
    "broken",
    ["no_model", "no_vectors", "stale", "empty_reply", "raises", "times_out"],
)
@pytest.mark.asyncio
async def test_every_way_this_can_fail_is_the_lexical_score(broken: str) -> None:
    """Six ways to get no vector, one test each, and all six answer the
    question. This is the property the feature is built around: it may be
    absent, it may not be a way for a question to fail."""
    question = "how many people stopped paying?"
    snapshot = _wide()
    good = _aligned_index(
        snapshot, target="public.order_items", vector=(1.0, 0.0, 0.0, 0.0)
    )

    async def blows_up(_asked: Any) -> list[list[float]]:
        raise RuntimeError("the endpoint is gone")

    async def times_out(_asked: Any) -> list[list[float]]:
        # What `question_embedder` returns after `wait_for` fires: its contract
        # is `[]`, never a raise, and the node reads that as "rank on words".
        return []

    async def nothing(_asked: Any) -> list[list[float]]:
        return [[]]

    index = {
        "no_model": VectorIndex(),
        "no_vectors": VectorIndex(model=MODEL, dimension=DIM, embed=good.embed),
        "stale": VectorIndex(
            model=MODEL, dimension=DIM, embed=good.embed,
            tables={
                key: TableVector(vector=(1.0, 0.0, 0.0, 0.0), stored_fingerprint="old")
                for key in good.tables
            },
        ),
        "empty_reply": VectorIndex(
            model=MODEL, dimension=DIM, tables=good.tables, embed=nothing
        ),
        "raises": VectorIndex(
            model=MODEL, dimension=DIM, tables=good.tables, embed=blows_up
        ),
        "times_out": VectorIndex(
            model=MODEL, dimension=DIM, tables=good.tables, embed=times_out
        ),
    }[broken]

    assert await _selected(question, index) == await _selected(question, VectorIndex())


@pytest.mark.asyncio
async def test_a_connection_that_cannot_embed_makes_no_call() -> None:
    """Not merely "the result is the same": the call is not made. An empty
    index must cost nothing, because it is the shipped state everywhere."""
    calls: list[Any] = []

    async def counted(asked: Any) -> list[list[float]]:
        calls.append(asked)
        return [[1.0, 0.0, 0.0, 0.0]]

    await _selected(
        "how many people stopped paying?",
        VectorIndex(model=MODEL, dimension=DIM, embed=counted),
    )
    assert calls == [], "no stored vector to compare against, so nothing to ask"


def test_the_blend_is_not_accidentally_async() -> None:
    assert not asyncio.iscoroutinefunction(blend)
    assert not asyncio.iscoroutinefunction(cosines)
