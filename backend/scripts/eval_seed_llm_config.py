"""Seed (idempotently) the llm_config the nightly eval runs against.

CI has no app UI to click through, so this inserts one model config straight
into the app DB from environment variables, encrypting the API key exactly the
way the API does (AES-256-GCM, AAD = ``llm_config:<id>``). It reuses
``ensure_admin`` for the owner FK so the row is well-formed.

Prints the config id on stdout — the workflow captures it and passes it to the
runner (or exports EVAL_LLM_CONFIG_ID).

    EVAL_MODEL=... EVAL_PROVIDER=... EVAL_BASE_URL=... EVAL_API_KEY=... \
        python -m scripts.eval_seed_llm_config
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import LlmConfig, User
from app.infra.db.session import dispose_engine, get_sessionmaker
from app.services.bootstrap import ensure_admin

_CONFIG_NAME = "eval-ci"


async def _amain() -> int:
    model = os.environ.get("EVAL_MODEL")
    if not model:
        print("EVAL_MODEL is required", file=sys.stderr)
        return 2
    provider = os.environ.get("EVAL_PROVIDER", "OpenAI-compatible")
    base_url = os.environ.get("EVAL_BASE_URL") or None
    api_key = os.environ.get("EVAL_API_KEY", "")

    settings = get_settings()
    box = AesGcmSecretBox(
        settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
    )

    sm = get_sessionmaker()
    async with sm() as session:
        await ensure_admin(session, settings)
        admin = (
            await session.execute(
                select(User).where(User.email == settings.admin_email.lower().strip())
            )
        ).scalar_one()

        existing = (
            await session.execute(select(LlmConfig).where(LlmConfig.name == _CONFIG_NAME))
        ).scalar_one_or_none()

        config_id = existing.id if existing else uuid.uuid4()
        encrypted = (
            box.encrypt(api_key, aad=f"llm_config:{config_id}") if api_key else None
        )
        # A hosted OpenAI-compatible endpoint; structured output + streaming on.
        capabilities = {
            "supports_structured_output": os.environ.get("EVAL_STRUCTURED", "1") == "1",
            "supports_streaming": True,
            "supports_system_prompt": True,
        }
        if existing:
            existing.provider = provider
            existing.model = model
            existing.base_url = base_url
            existing.encrypted_api_key = encrypted
            existing.capabilities = capabilities
        else:
            session.add(
                LlmConfig(
                    id=config_id,
                    owner_id=admin.id,
                    name=_CONFIG_NAME,
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    temperature=float(os.environ.get("EVAL_TEMPERATURE", "0.0")),
                    max_tokens=int(os.environ.get("EVAL_MAX_TOKENS", "2048")),
                    encrypted_api_key=encrypted,
                    capabilities=capabilities,
                    status="UNTESTED",
                )
            )
        await session.commit()

    await dispose_engine()
    print(config_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
