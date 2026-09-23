"""The schema vector index: building it, and handing it to a run.

`docs/plans/hybrid-retrieval.md` Phase 2 — mvp2 **B2**. Phase 1 scored a
table's prose against the question by word overlap; this embeds the same prose
so a question that shares no word with it can still reach the table. *"How many
people stopped paying?"* against a comment that says *"a cancellation the
customer chose, not a failed payment"*.

Everything here is the I/O half. The arithmetic — the fingerprint, the cosine,
the rescale and the blend — is in `app/pipeline/relevance.py`, which has no
database, no provider and no timeout in it, for the reason
`app/knowledge/embed.py` has none either: a score you can only exercise through
a container is a score nobody exercises.

**Three properties, each inherited rather than re-argued** (Phase 7 of
`docs/plans/learning-loop.md` decided all three for the template store, and a
second answer to one question would be worse than either):

* **No new deployment unit.** Vectors go in a `double precision[]`, not a
  pgvector column. `postgres:16-alpine` does not carry the extension.
* **Staleness is derived, never tracked.** The fingerprint hashes the prose,
  the model and the dimension; nothing has to remember to invalidate anything.
* **A failure here is not a failure of anything.** No pinned model, no fresh
  vector, a provider error, a timeout, a width that changed underneath the pin
  — each falls back to the lexical score with the question unaffected. A
  retrieval feature that can fail a question is worse than one that is absent.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.logging import get_logger
from app.infra.db.models import DatabaseConnection, SchemaTableVector
from app.pipeline.relevance import (
    Embedder,
    TableVector,
    VectorIndex,
    prose_by_table,
    prose_fingerprint,
)

log = get_logger(__name__)

#: How many tables one pass will embed. A ceiling rather than a setting, for
#: `MAX_EMBEDDINGS_PER_PASS`'s reason: a schema that needs more than this
#: re-embedded at once has just been re-synced or re-pinned, and spreading that
#: over a few passes costs nothing — the lexical score answers meanwhile —
#: while a single unbounded pass is a single unbounded bill.
MAX_TABLES_PER_PASS = 400


@dataclass(slots=True)
class SchemaIndexResult:
    """What one pass did, in counts a person can read back."""

    #: Tables with prose worth embedding at all. A table nobody has written a
    #: word about is not a candidate: there is nothing to embed, and a vector
    #: of the empty string would match every question equally.
    considered: int = 0
    embedded: int = 0
    #: Candidates whose stored vector was already current — the number that
    #: should be nearly everything on a steady-state connection, and the one
    #: that says the fingerprint rule is working.
    current: int = 0
    truncated: bool = False
    #: The provider's own sentence when the pass could not run. Empty on
    #: success *and* on "nothing to do", which are different from "it failed".
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


async def prose_for_connection(
    db: AsyncSession, connection: DatabaseConnection
) -> dict[str, tuple[str, ...]]:
    """Every table's sentences, exactly as `retrieve` will rebuild them.

    Same snapshot, same **bound** layer (`load_layer`, which is the one binder
    every reader goes through), same `include_db_comments`, same
    `prose_by_table`. If this drifts from the node by so much as a field, no
    fingerprint ever matches, nothing is ever fresh, and the feature silently
    does nothing while reporting that it indexed everything — the worst failure
    available here, because it has no symptom. Hence one function on each side
    and no second assembly of the bag.
    """
    from app.services.query_service import latest_snapshot
    from app.services.semantic_service import load_layer

    snapshot = await latest_snapshot(db, connection.id)
    tables = snapshot.get("tables") or []
    if not tables:
        return {}
    layer = await load_layer(db, connection, snapshot=snapshot)
    prose: dict[str, tuple[str, ...]] = {}
    if layer.document is not None:
        from app.semantic import table_prose

        prose = table_prose(layer.document)
    return prose_by_table(
        tables, layer=prose, include_comments=connection.include_db_comments
    )


async def index_schema_vectors(
    db: AsyncSession, settings: Settings, connection: DatabaseConnection
) -> SchemaIndexResult:
    """Bring this connection's table vectors up to date with its prose.

    Runs in the worker, never on a request that answers a question: embedding
    is a provider call, and a question whose latency depended on indexing would
    make retrieval worse than the lexical score it is meant to improve on. The
    one embed a question does pay for is its own, in `retrieve`.

    Nothing is ever deleted. A vector whose prose moved is overwritten on the
    next successful pass and ignored until then; a row for a table a re-sync
    dropped is simply never read again, because the snapshot is the authority
    on what exists.
    """
    out = SchemaIndexResult()
    if not connection.embedding_model or connection.embedding_dimension <= 0:
        return out

    # A table nobody has written a word about is not a candidate: there is
    # nothing to embed, and a vector of the empty string would sit equally
    # close to every question ever asked.
    prose = {
        key: text
        for key, text in (await prose_for_connection(db, connection)).items()
        if text
    }
    out.considered = len(prose)
    if not prose:
        return out

    result = await db.execute(
        select(SchemaTableVector).where(
            SchemaTableVector.connection_id == connection.id
        )
    )
    rows = {row.qualified_name: row for row in result.scalars().all()}

    pending: list[tuple[str, str]] = []
    for key, sentences in sorted(prose.items()):
        wanted = prose_fingerprint(
            sentences, connection.embedding_model, connection.embedding_dimension
        )
        row = rows.get(key)
        fresh = (
            row is not None
            and row.embedding_fingerprint == wanted
            and len(row.embedding or []) == connection.embedding_dimension
        )
        if fresh:
            out.current += 1
        else:
            pending.append((key, wanted))

    if not pending:
        return out
    if len(pending) > MAX_TABLES_PER_PASS:
        pending = pending[:MAX_TABLES_PER_PASS]
        out.truncated = True

    from app.services.knowledge_service import _embedding_llm

    llm = await _embedding_llm(db, settings, connection)
    if llm is None:
        out.error = (
            "No model provider is set up to embed, so there is nothing to "
            "index with. Give an OpenAI-compatible provider an embedding "
            "model in LLM providers."
        )
        return out

    from app.infra.llm.litellm_gateway import LiteLLMGateway

    gateway = LiteLLMGateway.from_settings(settings)
    try:
        vectors = await gateway.embed(
            llm,
            ["\n".join(prose[key]) for key, _ in pending],
            model=connection.embedding_model,
        )
    except Exception as err:
        # Bounded and reported. The index keeps whatever it had, which is the
        # difference between "a pass behind" and "empty" — and only the first
        # is true here.
        out.error = str(err)[:500]
        log.warning(
            "schema_index_failed",
            connection_id=str(connection.id),
            pending=len(pending),
        )
        return out

    if len(vectors) != len(pending):
        out.error = "The embedding endpoint returned the wrong number of vectors."
        return out

    now = utcnow()
    for (key, wanted), vector in zip(pending, vectors, strict=True):
        if len(vector) != connection.embedding_dimension:
            # The endpoint changed width underneath the pin. Writing this
            # vector would put two widths in one index, where cosine means
            # nothing — `relevance.cosines` would return 0.0 for every one of
            # them and the feature would look broken rather than mispinned.
            out.error = (
                f"The endpoint answered at {len(vector)} dimensions, not the "
                f"{connection.embedding_dimension} this connection is pinned "
                f"to. Re-pin the embedding model on the connection."
            )
            return out
        row = rows.get(key)
        if row is None:
            row = SchemaTableVector(
                connection_id=connection.id, qualified_name=key
            )
            db.add(row)
            rows[key] = row
        row.embedding = [float(v) for v in vector]
        row.embedding_fingerprint = wanted
        row.embedded_at = now
        out.embedded += 1

    await db.flush()
    log.info(
        "schema_vectors_indexed",
        connection_id=str(connection.id),
        embedded=out.embedded,
        current=out.current,
        truncated=out.truncated,
    )
    return out


async def load_vector_index(
    db: AsyncSession, settings: Settings, connection: DatabaseConnection
) -> VectorIndex:
    """The index `retrieve` scores against, or an empty one.

    Empty is the shipped state on almost every connection and costs one
    attribute read: with no `embedding_model` pinned this returns before it
    touches the table, so a run on a lexical connection makes no query it did
    not already make.

    The embedder is built lazily inside the callable rather than resolved here,
    for `knowledge_service._embedder`'s reason: resolving the config decrypts a
    key, and on a connection whose vectors are all stale the node returns
    before the callable is ever invoked — a key that was never decrypted is a
    key that never sat in memory for the length of a request.
    """
    if not connection.embedding_model or connection.embedding_dimension <= 0:
        return VectorIndex()

    result = await db.execute(
        select(SchemaTableVector).where(
            SchemaTableVector.connection_id == connection.id,
            SchemaTableVector.embedding_fingerprint != "",
        )
    )
    tables = {
        row.qualified_name: TableVector(
            vector=tuple(row.embedding or ()),
            stored_fingerprint=row.embedding_fingerprint or "",
        )
        for row in result.scalars().all()
    }
    if not tables:
        # Nothing to compare a question against, so do not offer an embedder
        # that would spend a call to compare it with nothing.
        return VectorIndex()
    return VectorIndex(
        model=connection.embedding_model,
        dimension=connection.embedding_dimension,
        tables=tables,
        embed=question_embedder(db, settings, connection),
    )


def question_embedder(
    db: AsyncSession, settings: Settings, connection: DatabaseConnection
) -> Embedder:
    """`(texts) -> vectors`, bounded, and `[]` on every failure.

    ⚠️ **This one is on the critical path**, so it carries
    `embedding_match_timeout_seconds` — the budget the template matcher already
    runs under, and for the identical reason: the gateway's own timeout is
    sized for a completion, and a revoked key or an endpoint that stopped
    answering would otherwise make every question on this connection wait out a
    minute before the lexical score it would have used anyway answered.

    `[]` rather than a raise is the contract, and `retrieve` reads it as *"rank
    on words"*.
    """

    async def embed(texts: Any) -> list[list[float]]:
        from app.infra.llm.litellm_gateway import LiteLLMGateway
        from app.services.knowledge_service import _embedding_llm

        try:
            llm = await _embedding_llm(db, settings, connection)
            if llm is None:
                return []
            gateway = LiteLLMGateway.from_settings(settings)
            return await asyncio.wait_for(
                gateway.embed(llm, list(texts), model=connection.embedding_model),
                timeout=settings.embedding_match_timeout_seconds,
            )
        except (TimeoutError, asyncio.CancelledError):
            log.warning(
                "schema_vector_question_timeout",
                connection_id=str(connection.id),
                timeout=settings.embedding_match_timeout_seconds,
            )
            return []
        except Exception:
            # Every other provider failure reads the same way to a question:
            # there is no vector, so there is no vector score. Logged at info
            # because the run is unaffected and the indexing pass reports the
            # same endpoint's failures where somebody is looking for them.
            log.info(
                "schema_vector_question_failed", connection_id=str(connection.id)
            )
            return []

    return embed
