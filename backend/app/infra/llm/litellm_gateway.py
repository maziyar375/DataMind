"""The ONLY module permitted to import litellm.

CI enforces this:
    grep -rn "import litellm" app/ | grep -v infra/llm/   →  must be empty

That one check is what decides whether the LLM abstraction is real or
decorative. If litellm becomes a liability, `HttpxOpenAIGateway` below the
same Protocol is roughly 200 lines.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, TypeVar

import litellm
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import LLMError
from app.core.logging import get_logger
from app.domain.ports.llm import (
    ChatMessage,
    Completion,
    ProviderCapabilities,
    ResolvedLLM,
)

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)

litellm.drop_params = True
litellm.suppress_debug_info = True

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# Transient failures worth retrying: a 429 or a 5xx is a "try again shortly",
# not a bad request. Auth / bad-request / context-length errors are permanent
# and must fail fast — retrying them only wastes the caller's deadline.
_RETRYABLE = (
    RateLimitError,
    InternalServerError,
    ServiceUnavailableError,
    APIConnectionError,
    Timeout,
)


def _retry_after_seconds(err: Exception) -> float | None:
    """A provider's own Retry-After hint, if it sent one, honoured over backoff."""
    for attr in ("response", "llm_provider_response"):
        resp = getattr(err, attr, None)
        headers = getattr(resp, "headers", None)
        if headers:
            raw = headers.get("retry-after") or headers.get("Retry-After")
            try:
                if raw is not None:
                    return float(raw)
            except (TypeError, ValueError):
                pass
    return None


class LiteLLMGateway:
    def __init__(
        self,
        *,
        timeout_seconds: int = 60,
        max_retries: int = 0,
        retry_base_delay_seconds: float = 2.0,
        retry_max_delay_seconds: float = 30.0,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._retry_base = retry_base_delay_seconds
        self._retry_max = retry_max_delay_seconds

    @classmethod
    def from_settings(cls, settings: Any) -> LiteLLMGateway:
        """Build a gateway wired to the app's timeout + transient-retry policy."""
        return cls(
            timeout_seconds=settings.llm_request_timeout_seconds,
            max_retries=settings.llm_max_retries,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
            retry_max_delay_seconds=settings.llm_retry_max_delay_seconds,
        )

    # ── transient-failure retry ──────────────────────────────────────────
    async def _acompletion(self, **payload: Any) -> Any:
        """`litellm.acompletion` with bounded exponential backoff on transient
        errors. A permanent error (auth, bad request) is re-raised immediately."""
        attempt = 0
        while True:
            try:
                return await litellm.acompletion(**payload)
            except _RETRYABLE as err:
                if attempt >= self._max_retries:
                    raise
                delay = _retry_after_seconds(err) or min(
                    self._retry_base * (2**attempt), self._retry_max
                )
                log.warning(
                    "llm_retry",
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                    delay_s=round(delay, 1),
                    error=type(err).__name__,
                )
                await asyncio.sleep(delay)
                attempt += 1

    # ── request shaping ──────────────────────────────────────────────────
    def _kwargs(self, llm: ResolvedLLM, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        model = llm.model
        needs_openai_prefix = (
            (llm.provider == "Ollama" and not model.startswith("ollama/"))
            or (llm.provider in {"OpenAI-compatible", "Custom"} and "/" not in model)
        )
        if needs_openai_prefix:
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
            response = await self._acompletion(**self._kwargs(llm, messages))
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
            response = await self._acompletion(
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
            response = await self._acompletion(**payload)
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


def estimate_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    """Best-effort USD cost for a completion, from litellm's price map.

    Lives here because litellm may only be imported under infra/llm. Returns
    None for a model litellm does not price (e.g. a local Ollama model), so the
    eval reports cost where it is known and stays silent where it is not.
    """
    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except Exception:
        return None
    total = (prompt_cost or 0.0) + (completion_cost or 0.0)
    return total or None


def _parse_into(schema: type[T], raw: str) -> T:  # noqa: UP047  (matches TypeVar used above)
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
