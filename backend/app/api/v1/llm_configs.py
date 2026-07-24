from __future__ import annotations

import time
import uuid
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CtxDep, DbDep, SecretBoxDep, SettingsDep
from app.api.schemas import (
    LlmConfigCreate,
    LlmConfigRead,
    LlmConfigTestRequest,
    LlmConfigUpdate,
    TestResult,
)
from app.core.clock import utcnow
from app.core.errors import ConflictError, LLMError, NotFoundError
from app.domain.ports.llm import ProviderCapabilities, ResolvedLLM
from app.infra.db.models import LlmConfig
from app.infra.llm.litellm_gateway import LiteLLMGateway

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


@router.get("", response_model=list[LlmConfigRead])
async def list_configs(ctx: CtxDep, db: DbDep) -> list[LlmConfigRead]:
    result = await db.execute(
        select(LlmConfig)
        .where(LlmConfig.owner_id == ctx.user_id)
        .order_by(LlmConfig.created_at)
    )
    return [_to_read(r) for r in result.scalars()]


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

    config_id = uuid.uuid4()
    row = LlmConfig(
        id=config_id,
        owner_id=ctx.user_id,
        name=payload.name,
        provider=payload.provider,
        base_url=payload.base_url,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
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

    resolved = ResolvedLLM(
        config_id=payload.config_id or uuid.uuid4(),
        provider=payload.provider,
        model=payload.model,
        base_url=payload.base_url,
        api_key=api_key,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        capabilities=ProviderCapabilities(),
    )
    gateway = LiteLLMGateway(timeout_seconds=settings.llm_request_timeout_seconds)

    started = time.perf_counter()
    try:
        capabilities = await gateway.probe(resolved)
    except LLMError as err:
        return TestResult(
            ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            message=err.message,
        )

    return TestResult(
        ok=True,
        latency_ms=int((time.perf_counter() - started) * 1000),
        message=f"Reached {payload.model}",
        detected_capabilities={
            "supports_structured_output": capabilities.supports_structured_output,
            "supports_streaming": capabilities.supports_streaming,
            "supports_system_prompt": capabilities.supports_system_prompt,
        },
    )


@router.patch("/{config_id}", response_model=LlmConfigRead)
async def update_config(
    config_id: UUID, payload: LlmConfigUpdate,
    ctx: CtxDep, db: DbDep, box: SecretBoxDep,
) -> LlmConfigRead:
    row = await _owned(db, config_id, ctx)
    for field, value in payload.model_dump(
        exclude_unset=True, exclude={"api_key"}
    ).items():
        if value is not None:
            setattr(row, field, value)

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
        capabilities=ProviderCapabilities(),
    )
    gateway = LiteLLMGateway(timeout_seconds=settings.llm_request_timeout_seconds)

    started = time.perf_counter()
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

    return TestResult(
        ok=True, latency_ms=latency,
        message=f"Reached {row.model}",
        detected_capabilities=row.capabilities,
    )
