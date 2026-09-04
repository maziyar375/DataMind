from __future__ import annotations

import time
import uuid
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CtxDep, DbDep, SecretBoxDep, SettingsDep
from app.api.schemas import (
    EmbeddingProbe,
    LlmConfigCreate,
    LlmConfigRead,
    LlmConfigTestRequest,
    LlmConfigUpdate,
    ParameterCatalog,
    TestResult,
)
from app.core.clock import utcnow
from app.core.errors import ConflictError, LLMError, NotFoundError, ValidationError
from app.domain.ports.llm import ProviderCapabilities, ResolvedLLM
from app.domain.value_objects.llm_params import (
    ParamError,
    embedding_specs,
    full_catalog,
    validate_completion_params,
    validate_embedding_params,
)
from app.infra.db.models import LlmConfig
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.services.query_service import can_chat, can_embed

router = APIRouter(prefix="/llm-configs", tags=["llm-configs"])


def _to_read(row: LlmConfig) -> LlmConfigRead:
    data = LlmConfigRead.model_validate(row)
    data.has_api_key = bool(row.encrypted_api_key)
    return data


async def _owned(db, config_id: UUID, ctx) -> LlmConfig:
    result = await db.execute(
        select(LlmConfig).where(
            LlmConfig.id == config_id, LlmConfig.owner_id == ctx.user_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("Model configuration not found.")
    return row


def _checked(provider: str, params: Any, embedding_params: Any) -> tuple[dict, dict]:
    """Both parameter maps, against the provider that will actually be called.

    Validated **here** rather than in a Pydantic validator for one reason worth
    stating: a PATCH may change the provider, leave it alone, or change only
    the parameters, and only the route knows the provider that results. A
    validator on `LlmConfigUpdate` would be checking Anthropic parameters
    against a payload that never mentions Anthropic. The catalog is the same
    one the request shaper reads, so a stored row cannot hold a parameter the
    gateway would silently drop.
    """
    try:
        return (
            validate_completion_params(provider, params),
            validate_embedding_params(provider, embedding_params),
        )
    except ParamError as err:
        raise ValidationError(str(err)) from err


def _declares_something(model: str, embedding_model: str) -> None:
    """A row has to be *for* something.

    `model` became optional so an endpoint that serves only vectors can be
    configured without inventing a chat model whose Test button could only
    fail. The other end of that is this: a row with neither is a record that
    appears in no picker, answers nothing and embeds nothing, and the only
    honest moment to say so is when it is being saved.

    Checked here rather than as a database CHECK for the same reason the
    disclosure policy and the provider kind are: this file is where a row is
    written, and a constraint violation reaches the caller as a 500 rather than
    as a sentence naming the field to fill in.
    """
    if not model and not embedding_model:
        raise ValidationError(
            "Give this provider a model to answer with, an embedding model, or "
            "both — a configuration with neither cannot be used for anything."
        )


def _embedding_refused(provider: str, model: str) -> None:
    """Refuse an embedding model on a provider that has no such endpoint.

    Anthropic is the case, and it is refused on save rather than at first use
    for the same reason `probe_embedding` refuses it without a network call: it
    is a permanent fact about the provider, and a configuration that stores it
    is one that will disappoint somebody later, quietly, on a different screen.
    """
    if model and not embedding_specs(provider) and provider != "Custom":
        raise ValidationError(
            f"{provider} does not offer an embedding endpoint, so it cannot be "
            "given an embedding model. Point an OpenAI-compatible provider at "
            "one instead."
        )


@router.get("/parameters", response_model=list[ParameterCatalog])
async def parameter_catalog(ctx: CtxDep) -> list[ParameterCatalog]:
    """What each provider documents, so the form can be generated from it.

    Declared above `/{config_id}` so the literal path wins the match. Signed-in
    but otherwise open: this is a description of two public APIs and holds
    nothing about anybody's configuration.
    """
    return [ParameterCatalog.model_validate(entry) for entry in full_catalog()]


@router.get("", response_model=list[LlmConfigRead])
async def list_configs(
    ctx: CtxDep, db: DbDep, purpose: Literal["chat", "embedding"] | None = None
) -> list[LlmConfigRead]:
    """Every provider configuration, or only the ones good for one job.

    A row declares a chat model, an embedding model, or both, so "the list of
    models" is now a question with three answers. The **providers page** wants
    all of them; every picker that chooses a model to *answer* with wants
    `purpose=chat`, or an embeddings-only endpoint would be offered as
    something to ask a question of.

    Filtered with the same two predicates `resolve_llm` refuses on, so a picker
    and the funnel behind it cannot disagree about what a row is for.
    """
    result = await db.execute(
        select(LlmConfig)
        .where(LlmConfig.owner_id == ctx.user_id)
        .order_by(LlmConfig.created_at)
    )
    rows = list(result.scalars())
    if purpose == "chat":
        rows = [row for row in rows if can_chat(row)]
    elif purpose == "embedding":
        rows = [row for row in rows if can_embed(row)]
    return [_to_read(row) for row in rows]


@router.post("", response_model=LlmConfigRead, status_code=status.HTTP_201_CREATED)
async def create_config(
    payload: LlmConfigCreate, ctx: CtxDep, db: DbDep, box: SecretBoxDep
) -> LlmConfigRead:
    existing = await db.execute(
        select(LlmConfig).where(
            LlmConfig.owner_id == ctx.user_id, LlmConfig.name == payload.name
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("You already have a model configuration with that name.")

    embedding_model = payload.embedding_model.strip()
    model = payload.model.strip()
    _declares_something(model, embedding_model)
    _embedding_refused(payload.provider, embedding_model)
    params, embedding_params = _checked(
        payload.provider, payload.params, payload.embedding_params
    )

    config_id = uuid.uuid4()
    row = LlmConfig(
        id=config_id,
        owner_id=ctx.user_id,
        name=payload.name,
        provider=payload.provider,
        base_url=payload.base_url,
        model=model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        params=params,
        embedding_model=embedding_model,
        embedding_params=embedding_params,
        encrypted_api_key=(
            box.encrypt(payload.api_key.get_secret_value(), aad=f"llm_config:{config_id}")
            if payload.api_key else None
        ),
        key_version=box.key_version,
    )
    db.add(row)
    await db.flush()
    return _to_read(row)


@router.post("/test", response_model=TestResult)
async def test_draft_config(
    payload: LlmConfigTestRequest,
    ctx: CtxDep, db: DbDep, box: SecretBoxDep, settings: SettingsDep,
) -> TestResult:
    """Probe a model configuration straight from the form, saved or not.

    Declared above `/{config_id}` so the literal path wins the match. Nothing
    is written: the form may hold unsaved edits that differ from the row, so a
    probe here never records capabilities against a row — only `/{id}/test`,
    which tests the stored values, may do that.

    When `config_id` is given and no new key was typed, the stored key is
    reused so an edit can be tested without re-entering the secret; every other
    value comes from the form.
    """
    api_key = payload.api_key.get_secret_value() if payload.api_key else ""
    if payload.config_id is not None and not payload.api_key:
        row = await _owned(db, payload.config_id, ctx)
        if row.encrypted_api_key:
            api_key = box.decrypt(row.encrypted_api_key, aad=f"llm_config:{row.id}")

    embedding_model = payload.embedding_model.strip()
    _declares_something(payload.model.strip(), embedding_model)
    _embedding_refused(payload.provider, embedding_model)
    params, embedding_params = _checked(
        payload.provider, payload.params, payload.embedding_params
    )

    resolved = ResolvedLLM(
        config_id=payload.config_id or uuid.uuid4(),
        provider=payload.provider,
        model=payload.model,
        base_url=payload.base_url,
        api_key=api_key,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        params=params,
        embedding_model=embedding_model,
        embedding_params=embedding_params,
        capabilities=ProviderCapabilities(),
    )
    gateway = LiteLLMGateway(timeout_seconds=settings.llm_request_timeout_seconds)

    started = time.perf_counter()
    # An embeddings-only configuration has no chat model to reach, so there is
    # nothing to send one short prompt to. Probing the embedding half alone is
    # the whole test for such a row, and calling it a failure because a model
    # it never claimed to have could not be reached would be nonsense.
    if not resolved.model:
        embedding = await _probe_embedding(gateway, resolved)
        return TestResult(
            ok=bool(embedding and embedding.ok),
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=(
                f"Reached {embedding.model}"
                if embedding and embedding.ok
                else (embedding.message if embedding else "")
            ),
            embedding=embedding,
        )
    try:
        capabilities = await gateway.probe(resolved)
    except LLMError as err:
        return TestResult(
            ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=err.message,
        )

    applied, dropped = gateway.applied_params(resolved)
    return TestResult(
        ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
        message=f"Reached {payload.model}",
        detected_capabilities={
            "supports_structured_output": capabilities.supports_structured_output,
            "supports_streaming": capabilities.supports_streaming,
            "supports_system_prompt": capabilities.supports_system_prompt,
        },
        applied_params=applied,
        dropped_params=dropped,
        embedding=await _probe_embedding(gateway, resolved),
    )


async def _probe_embedding(
    gateway: LiteLLMGateway, resolved: ResolvedLLM
) -> EmbeddingProbe | None:
    """One vector, if this configuration claims to serve them.

    Skipped entirely when no embedding model is configured — which is every row
    until somebody says otherwise — so testing a completions-only provider
    costs exactly what it always did.
    """
    if not resolved.embedding_model:
        return None
    capability = await gateway.probe_embedding(resolved)
    return EmbeddingProbe(
        ok=capability.available,
        model=capability.model,
        dimension=capability.dimension,
        message=capability.reason,
    )


@router.patch("/{config_id}", response_model=LlmConfigRead)
async def update_config(
    config_id: UUID, payload: LlmConfigUpdate,
    ctx: CtxDep, db: DbDep, box: SecretBoxDep,
) -> LlmConfigRead:
    row = await _owned(db, config_id, ctx)

    # The provider the row will have *after* this patch, which is what the
    # parameters have to be legal for. Switching provider and clearing the
    # parameters in one PATCH is the honest way to move a configuration from
    # OpenAI to Anthropic; switching without clearing is refused, naming the
    # parameter that does not survive the move.
    provider = payload.provider or row.provider
    given = payload.model_dump(exclude_unset=True)
    embedding_model = (
        payload.embedding_model.strip()
        if payload.embedding_model is not None
        else row.embedding_model
    )
    model = payload.model.strip() if payload.model is not None else row.model
    _declares_something(model, embedding_model)
    _embedding_refused(provider, embedding_model)
    params, embedding_params = _checked(
        provider,
        row.params if "params" not in given else payload.params,
        row.embedding_params
        if "embedding_params" not in given
        else payload.embedding_params,
    )

    for field, value in payload.model_dump(
        exclude_unset=True,
        exclude={"api_key", "params", "embedding_params", "embedding_model", "model"},
    ).items():
        if value is not None:
            setattr(row, field, value)
    row.model = model
    # Written unconditionally, not only when they were sent: a provider change
    # re-validates the stored maps above, and a row whose parameters were
    # accepted for the *old* provider must not survive the switch untouched.
    row.params = params
    row.embedding_params = embedding_params
    row.embedding_model = embedding_model

    if payload.api_key is not None:
        row.encrypted_api_key = box.encrypt(
            payload.api_key.get_secret_value(), aad=f"llm_config:{row.id}"
        )
        row.key_version = box.key_version
        row.status = "UNTESTED"

    await db.flush()
    return _to_read(row)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_config(config_id: UUID, ctx: CtxDep, db: DbDep) -> None:
    row = await _owned(db, config_id, ctx)
    await db.delete(row)
    # Inside the request, so a constraint that refuses this is an error the
    # caller sees rather than a 204 followed by a stack trace in the log —
    # `get_db` commits after the response is already written. See the note in
    # `connections.delete_connection`; `runs` blocked both until 0014.
    await db.flush()


@router.post("/{config_id}/test", response_model=TestResult)
async def test_config(
    config_id: UUID, ctx: CtxDep, db: DbDep,
    box: SecretBoxDep, settings: SettingsDep,
) -> TestResult:
    """A real probe: it calls the provider and records what it can actually do."""
    row = await _owned(db, config_id, ctx)
    api_key = ""
    if row.encrypted_api_key:
        api_key = box.decrypt(row.encrypted_api_key, aad=f"llm_config:{row.id}")

    resolved = ResolvedLLM(
        config_id=row.id, provider=row.provider, model=row.model,
        base_url=row.base_url, api_key=api_key,
        temperature=row.temperature, max_tokens=row.max_tokens,
        params=dict(row.params or {}),
        embedding_model=row.embedding_model or "",
        embedding_params=dict(row.embedding_params or {}),
        capabilities=ProviderCapabilities(),
    )
    gateway = LiteLLMGateway(timeout_seconds=settings.llm_request_timeout_seconds)

    started = time.perf_counter()
    if not row.model:
        # Embeddings only: the vector call *is* the test, and it is what the
        # row's status should record.
        embedding = await _probe_embedding(gateway, resolved)
        ok = bool(embedding and embedding.ok)
        row.status = "OK" if ok else "ERROR"
        row.last_tested_at = utcnow()
        await db.flush()
        return TestResult(
            ok=ok,
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=(
                f"Reached {embedding.model}"
                if ok and embedding
                else (embedding.message if embedding else "")
            ),
            embedding=embedding,
        )
    try:
        capabilities = await gateway.probe(resolved)
    except LLMError as err:
        row.status = "ERROR"
        row.last_tested_at = utcnow()
        await db.flush()
        return TestResult(
            ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=err.message,
        )

    latency = int((time.perf_counter() - started) * 1000)
    row.status = "OK"
    row.last_tested_at = utcnow()
    row.capabilities = {
        "supports_structured_output": capabilities.supports_structured_output,
        "supports_streaming": capabilities.supports_streaming,
        "supports_system_prompt": capabilities.supports_system_prompt,
    }
    await db.flush()

    applied, dropped = gateway.applied_params(resolved)
    return TestResult(
        ok=True, latency_ms=latency,
        message=f"Reached {row.model}",
        detected_capabilities=row.capabilities,
        applied_params=applied,
        dropped_params=dropped,
        embedding=await _probe_embedding(gateway, resolved),
    )
