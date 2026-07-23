"""The ONLY module permitted to import litellm.

CI enforces this:
    grep -rn "import litellm" app/ | grep -v infra/llm/   →  must be empty

That one check is what decides whether the LLM abstraction is real or
decorative. If litellm becomes a liability, `HttpxOpenAIGateway` below the
same Protocol is roughly 200 lines.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, TypeVar

import litellm
from pydantic import BaseModel, ValidationError as PydanticValidationError

from app.core.errors import LLMError
from app.core.logging import get_logger
from app.domain.ports.llm import (
    ChatMessage, Completion, ProviderCapabilities, ResolvedLLM,
)

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)

litellm.drop_params = True
litellm.suppress_debug_info = True

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LiteLLMGateway:
    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self._timeout = timeout_seconds

    # ── request shaping ──────────────────────────────────────────────────
    def _kwargs(self, llm: ResolvedLLM, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        model = llm.model
        if llm.provider == "Ollama" and not model.startswith("ollama/"):
            model = f"openai/{model}"
        elif llm.provider in {"OpenAI-compatible", "Custom"} and "/" not in model:
            model = f"openai/{model}"

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "timeout": self._timeout,
        }
        if llm.api_key:
            kwargs["api_key"] = llm.api_key
        if llm.base_url:
            kwargs["api_base"] = llm.base_url
        return kwargs

    # ── completion ───────────────────────────────────────────────────────
    async def complete(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> Completion:
        started = time.perf_counter()
        try:
            response = await litellm.acompletion(**self._kwargs(llm, messages))
        except Exception as err:
            raise LLMError(_clean(err)) from err

        latency_ms = int((time.perf_counter() - started) * 1000)
        text = (response.choices[0].message.content or "").strip()
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
        )

    async def stream(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]:
        try:
            response = await litellm.acompletion(
                **self._kwargs(llm, messages), stream=True
            )
            async for chunk in response:
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    yield piece
        except Exception as err:
            raise LLMError(_clean(err)) from err

    # ── structured output ────────────────────────────────────────────────
    async def structured(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage], schema: type[T]
    ) -> T:
        """Native structured output where available; instructed JSON otherwise.

        Either way the result is parsed and validated on our side. A provider
        claiming schema support is not a reason to trust its output.
        """
        payload = self._kwargs(llm, messages)

        if llm.capabilities.supports_structured_output:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        else:
            instruction = ChatMessage(
                role="system",
                content=(
                    "Reply with a single JSON object only. No prose, no markdown "
                    "fences. It must match this JSON Schema:\n"
                    f"{json.dumps(schema.model_json_schema())}"
                ),
            )
            payload["messages"] = [
                {"role": instruction.role, "content": instruction.content},
                *payload["messages"],
            ]

        try:
            response = await litellm.acompletion(**payload)
        except Exception as err:
            raise LLMError(_clean(err)) from err

        raw = (response.choices[0].message.content or "").strip()
        return _parse_into(schema, raw)

    # ── capability probe ─────────────────────────────────────────────────
    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities:
        messages = [ChatMessage(role="user", content="Reply with the word: ok")]
        await self.complete(llm, messages)

        supports_structured = False
        try:
            payload = self._kwargs(llm, messages)
            payload["response_format"] = {"type": "json_object"}
            payload["messages"] = [
                {"role": "user", "content": 'Reply with {"ok": true}'}
            ]
            await litellm.acompletion(**payload)
            supports_structured = True
        except Exception:
            supports_structured = False

        return ProviderCapabilities(
            supports_structured_output=supports_structured,
            supports_streaming=True,
            supports_system_prompt=True,
        )


def _parse_into(schema: type[T], raw: str) -> T:
    candidate = raw
    fenced = _JSON_FENCE.search(raw)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]

    try:
        return schema.model_validate_json(candidate)
    except (PydanticValidationError, ValueError) as err:
        raise LLMError(
            f"The model did not return valid {schema.__name__} JSON."
        ) from err


def _clean(err: Exception) -> str:
    text = str(err)
    # Provider errors sometimes echo the request, including the key.
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", "[REDACTED]", text)
    text = re.sub(r"('api_key':\s*)'[^']*'", r"\1'[REDACTED]'", text)
    return text[:500]
