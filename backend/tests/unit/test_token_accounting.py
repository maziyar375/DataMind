"""Usage travels back from a call that cannot return it.

`structured()` returns a validated model and `stream()` yields text, so both
have had the provider's own token counts in hand and dropped them — which is
why a chat run has been recording the tokens of its cheapest call and a report
generation has been recording none at all. `on_usage` is the sink that closes
that, shaped exactly like the `on_reasoning` sink already beside it: optional,
so every caller that does not want the number is unchanged.

Four properties are worth pinning, and each is a way this goes quietly wrong:

* the counts reported are the provider's, not a guess;
* a sink that raises loses no work, because this observes and does not
  authorise (`services/audit.py`'s posture, and its stated reason);
* a repaired call reports **both** attempts, because both were paid for and
  reporting only the successful one hides the expensive failure mode;
* a stream whose provider sends no usage chunk reports zero and **never
  estimates** — a tokenizer guess in the same column as a measurement is
  indistinguishable from one.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from app.core.errors import LLMError
from app.domain.ports.llm import (
    ChatMessage,
    Completion,
    ProviderCapabilities,
    ResolvedLLM,
    Usage,
)
from app.infra.llm.litellm_gateway import LiteLLMGateway

_MSG = [ChatMessage(role="user", content="hi")]


class _Answer(BaseModel):
    label: str = ""


def _llm(*, provider: str = "OpenAI-compatible", model: str = "gpt-4o-mini") -> ResolvedLLM:
    return ResolvedLLM(
        config_id="x", provider=provider, model=model, base_url=None, api_key="k",
        capabilities=ProviderCapabilities(supports_structured_output=False),
    )


def _gateway() -> LiteLLMGateway:
    return LiteLLMGateway(max_retries=0, retry_base_delay_seconds=0.0)


def _usage(prompt: int, completion: int) -> Any:
    return type("_U", (), {"prompt_tokens": prompt, "completion_tokens": completion})()


def _reply(content: str, *, usage: Any = None, finish_reason: str = "stop") -> Any:
    message = type("_M", (), {"content": content})()
    choice = type("_C", (), {"message": message, "finish_reason": finish_reason})()
    return type("_R", (), {"choices": [choice], "usage": usage})()


def _chunk(*, content: str = "", finish_reason: str = "", usage: Any = None) -> Any:
    """One streamed chunk. A usage chunk carries usage and **no choices**."""
    if usage is not None and not content:
        return type("_Chunk", (), {"choices": [], "usage": usage})()
    delta = type("_D", (), {"content": content or None, "reasoning_content": None})()
    choice = type("_C", (), {"delta": delta, "finish_reason": finish_reason})()
    return type("_Chunk", (), {"choices": [choice], "usage": None})()


def _stream(chunks: list[Any]) -> Any:
    class _Stream:
        def __aiter__(self) -> Any:
            async def gen() -> Any:
                for chunk in chunks:
                    yield chunk

            return gen()

    return _Stream()


# ── the shape of the thing ────────────────────────────────────────────────
def test_a_completion_hands_out_the_same_numbers_as_a_sink() -> None:
    """`complete()` already returns usage; `usage()` is how its callers feed the
    same recorder a sink feeds, so there is one accumulation path and not two."""
    got = Completion(
        text="hi", prompt_tokens=11, completion_tokens=3, latency_ms=42
    ).usage(model="openai/gpt-4o-mini")

    assert got == Usage(
        prompt_tokens=11, completion_tokens=3, latency_ms=42, model="openai/gpt-4o-mini"
    )


# ── structured() ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_structured_reports_the_counts_the_provider_sent() -> None:
    seen: list[Usage] = []

    async def once(**_: Any) -> Any:
        return _reply('{"label": "orders"}', usage=_usage(120, 8))

    with patch("litellm.acompletion", side_effect=once):
        out = await _gateway().structured(
            _llm(), _MSG, _Answer, on_usage=seen.append
        )

    assert out.label == "orders"
    assert len(seen) == 1
    assert (seen[0].prompt_tokens, seen[0].completion_tokens) == (120, 8)


@pytest.mark.asyncio
async def test_the_model_reported_is_the_one_litellm_was_asked_for() -> None:
    """Costing keys on the resolved name. A cost looked up under the *un*prefixed
    name is a null that reads as "this model is unpriced" — a wrong answer where
    a missing one was wanted."""
    seen: list[Usage] = []

    async def once(**_: Any) -> Any:
        return _reply('{"label": "x"}', usage=_usage(1, 1))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    assert seen[0].model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_a_repaired_call_reports_both_attempts() -> None:
    """Both were paid for. Reporting only the attempt that parsed would make the
    expensive failure mode the invisible one."""
    seen: list[Usage] = []
    calls = {"n": 0}

    async def flaky(**_: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply("Happy to help!", usage=_usage(100, 40))
        return _reply('{"label": "orders"}', usage=_usage(160, 6))

    with patch("litellm.acompletion", side_effect=flaky):
        out = await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    assert out.label == "orders"
    assert [(u.prompt_tokens, u.completion_tokens) for u in seen] == [(100, 40), (160, 6)]


@pytest.mark.asyncio
async def test_a_call_that_never_returns_reports_nothing() -> None:
    """There is no reply to read a count off, and inventing one would be the
    estimate this plan refuses everywhere else."""
    seen: list[Usage] = []

    async def unparseable(**_: Any) -> Any:
        return _reply("not json at all", usage=_usage(50, 5))

    with patch("litellm.acompletion", side_effect=unparseable), pytest.raises(LLMError):
        await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    # Both attempts happened and both are reported; the *raise* costs nothing
    # extra, it just means no seventh value arrives out of thin air.
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_a_provider_that_sends_no_usage_reports_zero_not_a_guess() -> None:
    seen: list[Usage] = []

    async def once(**_: Any) -> Any:
        return _reply('{"label": "orders"}')  # usage=None

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    assert (seen[0].prompt_tokens, seen[0].completion_tokens) == (0, 0)


# ── §1.4: failing to record never fails the work ──────────────────────────
@pytest.mark.asyncio
async def test_a_raising_sink_does_not_fail_a_structured_call() -> None:
    def explode(_usage: Usage) -> None:
        raise RuntimeError("the recorder is having a day")

    async def once(**_: Any) -> Any:
        return _reply('{"label": "orders"}', usage=_usage(9, 1))

    with patch("litellm.acompletion", side_effect=once):
        out = await _gateway().structured(_llm(), _MSG, _Answer, on_usage=explode)

    assert out.label == "orders"


@pytest.mark.asyncio
async def test_a_raising_sink_does_not_fail_a_stream() -> None:
    def explode(_usage: Usage) -> None:
        raise RuntimeError("the recorder is having a day")

    async def streamed(**_: Any) -> Any:
        return _stream([_chunk(content="hello"), _chunk(usage=_usage(4, 2))])

    with patch("litellm.acompletion", side_effect=streamed):
        text = "".join(
            [c.text async for c in _gateway().stream(_llm(), _MSG, on_usage=explode)]
        )

    assert text == "hello"


# ── stream() ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_stream_reports_the_trailing_usage_chunk() -> None:
    seen: list[Usage] = []

    async def streamed(**payload: Any) -> Any:
        # An OpenAI-compatible endpoint reports tokens on a stream only when
        # asked, so the request must ask.
        assert payload["stream_options"] == {"include_usage": True}
        return _stream(
            [
                _chunk(content="the "),
                _chunk(content="answer"),
                _chunk(usage=_usage(900, 12)),
            ]
        )

    with patch("litellm.acompletion", side_effect=streamed):
        text = "".join(
            [c.text async for c in _gateway().stream(_llm(), _MSG, on_usage=seen.append)]
        )

    assert text == "the answer"
    assert (seen[0].prompt_tokens, seen[0].completion_tokens) == (900, 12)


@pytest.mark.asyncio
async def test_a_stream_with_no_usage_chunk_reports_zero() -> None:
    """The one method where a zero can mean "the provider did not say". It is
    recorded as it stands: a tokenizer-based guess would sit in the same column
    as measured values with nothing to tell them apart."""
    seen: list[Usage] = []

    async def streamed(**_: Any) -> Any:
        return _stream([_chunk(content="hi")])

    with patch("litellm.acompletion", side_effect=streamed):
        async for _ in _gateway().stream(_llm(), _MSG, on_usage=seen.append):
            pass

    assert len(seen) == 1
    assert (seen[0].prompt_tokens, seen[0].completion_tokens) == (0, 0)


@pytest.mark.asyncio
async def test_a_streamed_structured_call_reports_its_usage_chunk() -> None:
    """`on_reasoning` switches the transport; it must not switch off the count."""
    seen: list[Usage] = []

    async def collect(_piece: str) -> None:
        return None

    async def streamed(**payload: Any) -> Any:
        assert payload["stream_options"] == {"include_usage": True}
        return _stream(
            [
                _chunk(content='{"label": '),
                _chunk(content='"orders"}'),
                _chunk(finish_reason="stop"),
                _chunk(usage=_usage(77, 5)),
            ]
        )

    with patch("litellm.acompletion", side_effect=streamed):
        out = await _gateway().structured(
            _llm(), _MSG, _Answer, on_reasoning=collect, on_usage=seen.append
        )

    assert out.label == "orders"
    assert (seen[0].prompt_tokens, seen[0].completion_tokens) == (77, 5)


@pytest.mark.asyncio
async def test_anthropic_is_not_sent_stream_options() -> None:
    """`stream_options` is the OpenAI streaming API's own shape. Anthropic
    reports usage on a streamed message unasked, and this only ever widens what
    may arrive — it must never be the reason a request is refused."""
    seen: list[dict[str, Any]] = []

    async def streamed(**payload: Any) -> Any:
        seen.append(payload)
        return _stream([_chunk(content="hi")])

    llm = _llm(provider="Anthropic", model="claude-sonnet-5")
    with patch("litellm.acompletion", side_effect=streamed):
        async for _ in _gateway().stream(llm, _MSG):
            pass

    assert "stream_options" not in seen[0]


@pytest.mark.asyncio
async def test_nothing_changes_for_a_caller_that_passes_no_sink() -> None:
    """The phase gate: this is an optional keyword argument and no caller passes
    it yet, so every path without one must be exactly what it was."""
    seen: list[dict[str, Any]] = []

    async def once(**payload: Any) -> Any:
        seen.append(payload)
        return _reply('{"label": "orders"}', usage=_usage(3, 1))

    with patch("litellm.acompletion", side_effect=once):
        out = await _gateway().structured(_llm(), _MSG, _Answer)

    assert out.label == "orders"
    assert "stream" not in seen[0]
    assert "stream_options" not in seen[0]
