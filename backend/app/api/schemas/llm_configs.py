"""Request/response DTOs: llm configs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LlmConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal["OpenAI-compatible", "Anthropic"]
    base_url: str | None = None
    #: **Optional**, and the route requires a row to declare this *or*
    #: `embedding_model`. An endpoint that serves only vectors — a self-hosted
    #: TEI or Infinity server, an Ollama with one embedding model pulled — has
    #: no chat model to name, and inventing one gave it a Test button that
    #: could only fail.
    model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=200_000)
    # Typed loosely here and checked hard in the route, against the *selected*
    # provider's own API — see `_checked_params`. A `dict[str, Any]` in the DTO
    # is what lets `llm_params.py` stay the single catalog; declaring the
    # parameter set twice is how the two would drift.
    params: dict[str, Any] = Field(default_factory=dict)
    embedding_model: str = Field(default="", max_length=200)
    embedding_params: dict[str, Any] = Field(default_factory=dict)


class LlmConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: Literal["OpenAI-compatible", "Anthropic"] | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    # `{}` clears every configured parameter and is a legitimate edit, so these
    # are nullable-for-absent rather than falsy-for-absent: the route's patch
    # loop skips `None`, and `{}` reaches the row.
    params: dict[str, Any] | None = None
    embedding_model: str | None = Field(default=None, max_length=200)
    embedding_params: dict[str, Any] | None = None


class LlmConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    provider: str
    base_url: str | None
    #: Empty on an embeddings-only provider. Every list of models to *answer*
    #: with filters on it — `query_service.can_chat` is the one predicate, and
    #: `resolve_llm` refuses at the funnel rather than at eleven call sites.
    model: str
    temperature: float
    max_tokens: int
    #: Provider-documented request parameters. Safe to return: the catalog
    #: refuses every name the gateway owns, `api_key` among them, so there is
    #: no key under which a secret could be stored here — and the test in
    #: `test_openapi_has_no_secrets.py` reads this file, not this comment.
    params: dict[str, Any] = Field(default_factory=dict)
    embedding_model: str = ""
    embedding_params: dict[str, Any] = Field(default_factory=dict)
    status: str
    has_api_key: bool = False
    last_tested_at: datetime | None = None
    # What this reader may do with it. A model shared for *use* holds
    # `describe` and `select`, and the providers page renders it read-only
    # rather than as a form whose Save can only 403.
    privileges: list[str] = Field(default_factory=list)
    # Whose it is, when it is not yours. A display name, never an address.
    shared: bool = False
    owner_name: str | None = None


class EmbeddingProbe(BaseModel):
    """The result of asking an endpoint for one vector.

    The width is **measured**, never assumed, for the same reason the pin on a
    connection is: two gateways serving one model name at different widths is a
    thing that happens.
    """

    ok: bool = False
    model: str = ""
    dimension: int = 0
    message: str = ""


class TestResult(BaseModel):
    ok: bool
    latency_ms: int
    message: str | None = None
    detected_capabilities: dict[str, Any] = Field(default_factory=dict)
    #: Which configured parameters reach this model, and which the provider
    #: does not accept for it. `litellm.drop_params` makes an unsupported
    #: parameter silent at request time; a test that stayed silent too would
    #: let a configuration claim a behaviour it never has.
    applied_params: dict[str, Any] = Field(default_factory=dict)
    dropped_params: list[str] = Field(default_factory=list)
    #: What the embedding half of this configuration can do, when one is
    #: configured. Absent when `embedding_model` is empty — most rows.
    embedding: EmbeddingProbe | None = None


class LlmConfigTestRequest(BaseModel):
    """Probe a model configuration straight from the (possibly unsaved) form.

    `config_id` is set when an existing config is being edited: it lets the
    saved API key be reused when the key field was left blank, so every other
    value still comes from the form rather than from the stored row.
    """

    config_id: UUID | None = None
    provider: Literal["OpenAI-compatible", "Anthropic"]
    base_url: str | None = None
    model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=200_000)
    params: dict[str, Any] = Field(default_factory=dict)
    embedding_model: str = Field(default="", max_length=200)
    embedding_params: dict[str, Any] = Field(default_factory=dict)


class ParameterCatalog(BaseModel):
    """One provider's configurable surface, for a form to be generated from.

    Served rather than restated in TypeScript on purpose: the parameter set is
    the provider's, it is validated on save against this same catalog, and a
    second copy in the SPA is a second thing to keep true. Adding a parameter
    is a line in `app/domain/value_objects/llm_params.py` and reaches the
    screen with no frontend change.
    """

    provider: str
    #: Each entry: `name`, `kind`, `summary`, `example`, and whichever of
    #: `minimum` / `maximum` / `choices` / `object_keys` the parameter has.
    completion: list[dict[str, Any]] = Field(default_factory=list)
    embedding: list[dict[str, Any]] = Field(default_factory=list)
    #: Whether the provider has an embedding endpoint at all. Stated rather
    #: than inferred from an empty list, so the form can say *why* — Anthropic
    #: does not offer one, which is a fact and not a gap in this catalog.
    embedding_supported: bool = False
