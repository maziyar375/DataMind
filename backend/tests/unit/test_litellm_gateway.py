"""Transient-failure retry in the LLM gateway.

A 429 or a 5xx is "try again shortly", not a model verdict — the gateway must
retry it with bounded backoff so a rate-limited request is not surfaced to the
pipeline (and scored by the eval harness) as a model failure. A permanent error
(auth, bad request) must still fail fast.
"""
from __future__ import annotations

from unittest.mock import patch

import litellm
import pytest

from app.core.errors import LLMError
from app.domain.ports.llm import ChatMessage, ProviderCapabilities, ResolvedLLM
from app.infra.llm.litellm_gateway import LiteLLMGateway

_LLM = ResolvedLLM(
    config_id="x", provider="OpenAI-compatible", model="m", base_url="http://x",
    api_key="k", capabilities=ProviderCapabilities(),
)
_MSG = [ChatMessage(role="user", content="hi")]


class _Resp:
    class _Choice:
        class _M:
            content = "hello"

        message = _M()

    choices = [_Choice()]
    usage = None


def _gateway(max_retries: int) -> LiteLLMGateway:
    # Tiny delays keep the test instant while still exercising the backoff path.
    return LiteLLMGateway(
        max_retries=max_retries, retry_base_delay_seconds=0.0, retry_max_delay_seconds=0.0
    )


def _rate_limit() -> litellm.RateLimitError:
    return litellm.RateLimitError("Rate limit reached", llm_provider="openai", model="m")


@pytest.mark.asyncio
async def test_retries_transient_then_succeeds() -> None:
    calls = {"n": 0}

    async def flaky(**_: object) -> _Resp:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _rate_limit()
        return _Resp()

    with patch("litellm.acompletion", side_effect=flaky):
        out = await _gateway(4).complete(_LLM, _MSG)

    assert out.text == "hello"
    assert calls["n"] == 3  # two 429s survived, third call succeeded


@pytest.mark.asyncio
async def test_permanent_error_fails_fast_without_retry() -> None:
    calls = {"n": 0}

    async def auth_fail(**_: object) -> _Resp:
        calls["n"] += 1
        raise litellm.AuthenticationError("bad key", llm_provider="openai", model="m")

    with patch("litellm.acompletion", side_effect=auth_fail), pytest.raises(LLMError):
        await _gateway(4).complete(_LLM, _MSG)

    assert calls["n"] == 1  # auth errors are not retried


@pytest.mark.asyncio
async def test_exhausts_retries_then_raises_llm_error() -> None:
    calls = {"n": 0}

    async def always(**_: object) -> _Resp:
        calls["n"] += 1
        raise _rate_limit()

    with patch("litellm.acompletion", side_effect=always), pytest.raises(LLMError):
        await _gateway(4).complete(_LLM, _MSG)

    assert calls["n"] == 5  # 1 initial + max_retries(4)


@pytest.mark.asyncio
async def test_retries_disabled_by_default() -> None:
    calls = {"n": 0}

    async def always(**_: object) -> _Resp:
        calls["n"] += 1
        raise _rate_limit()

    with patch("litellm.acompletion", side_effect=always), pytest.raises(LLMError):
        await LiteLLMGateway().complete(_LLM, _MSG)  # max_retries defaults to 0

    assert calls["n"] == 1
