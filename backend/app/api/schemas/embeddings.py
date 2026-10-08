"""Request/response DTOs: the embedding matcher (Phase 7)."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class EmbeddingWrite(BaseModel):
    """Turn embedding search on or off for a connection.

    `model` is optional and almost always left empty: the probe tries the
    provider's small embedding model and pins whatever answers. A deployment
    running its own endpoint — Ollama, vLLM, a gateway — names its model here,
    and the *dimension* is never asked for, because it is measured from the
    endpoint's own reply rather than trusted from a form.
    """

    enabled: bool
    model: str = Field(default="", max_length=200)
    #: Rebuild every vector even though the fingerprints all still match.
    #: Ignored when `enabled` is false, where clearing the pin already clears
    #: them. This is the only way to recover a store whose endpoint moved
    #: underneath an unchanged model name and width — derived staleness cannot
    #: see that, because nothing it hashes has changed.
    force: bool = False


class EmbeddingProvider(BaseModel):
    """The provider configuration a store embeds with, as the panel names it.

    Reported, never posted. Which endpoint makes vectors is set up once in LLM
    providers and resolved by `embedding_provider` for every connection; there
    is no field on `EmbeddingWrite` that names one, because choosing an
    embedder per store is a fact to keep straight for no benefit — vectors are
    only ever compared inside one store.
    """

    id: UUID
    name: str
    provider: str
    model: str


class EmbeddingStatus(BaseModel):
    """What the store's embedding index looks like right now.

    `available` and `indexed` are separate on purpose: a connection can have a
    model pinned and no vectors yet (the first pass has not run) and that is a
    normal state, not a failure. The UI says "indexing" for it rather than
    "on", because "on" would promise a behaviour the next question will not
    show.
    """

    #: A model is pinned. False is the shipped default and means the lexical
    #: matcher answers — which is a state, not a degradation.
    enabled: bool = False
    model: str = ""
    dimension: int = 0
    #: Live templates that could carry a vector, and how many currently do.
    templates: int = 0
    indexed: int = 0
    #: The **other** index the same pin now feeds: tables with prose worth
    #: embedding, and how many carry a current vector
    #: (`schema_table_vectors`, mvp2 B2). Here rather than on a screen of its
    #: own because there is one pin, one provider and one dimension, and the
    #: three pin faults below break *both* features — a panel that named only
    #: the questions would report half an outage.
    schema_tables: int = 0
    schema_tables_indexed: int = 0
    #: Everything the probe or the last pass had to say. Empty on success.
    message: str = ""
    #: The provider that made this index — or, while the store is still matched
    #: on words, the one that would make it. `None` is the state where the
    #: control has nothing to switch to, and saying so is the difference
    #: between an offer and a button that fails.
    embedder: EmbeddingProvider | None = None
    #: Whether the pin still means what it says — `OK`, `NO_EMBEDDER`,
    #: `PROVIDER_MOVED` or `MODEL_MOVED`, from `knowledge_service.pin_health`.
    #:
    #: **Derived on every read, never stored**, the same rule vector staleness
    #: follows, and for the same reason: the three things that can break a pin
    #: — the provider deleted, replaced, or its model edited — all happen on a
    #: *different* screen from this one, and any of them would otherwise have
    #: to remember to come back and write a flag here. `enabled` answers
    #: whether a pin exists; this answers whether it can still be honoured, and
    #: the gap between those two questions is where a store went on reporting
    #: itself fully indexed while every question fell to word matching.
    pin: str = "OK"
    #: The embedding model the resolved provider would use *today*, when that
    #: is not the one this store was indexed with. Empty when they agree — and
    #: empty is the normal state.
    serves_model: str = ""
