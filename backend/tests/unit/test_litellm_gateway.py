"""Transient-failure retry and structured output in the LLM gateway.

A 429 or a 5xx is "try again shortly", not a model verdict — the gateway must
retry it with bounded backoff so a rate-limited request is not surfaced to the
pipeline (and scored by the eval harness) as a model failure. A permanent error
(auth, bad request) must still fail fast.

The structured-output tests below guard a regression that cost whole tables:
the capability probe proves `json_object` works, `structured()` used to send
`json_schema`, and with `drop_params` on litellm stripped it — leaving the
model with no JSON instruction at all.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import litellm
import pytest
from pydantic import BaseModel, Field

from app.core.errors import LLMError
from app.domain.ports.llm import ChatMessage, ProviderCapabilities, ResolvedLLM
from app.infra.llm.litellm_gateway import LiteLLMGateway, _wire_schema

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


# ── structured output ────────────────────────────────────────────────────
class _Nested(BaseModel):
    name: str = ""


class _Answer(BaseModel):
    label: str = ""
    items: list[_Nested] = Field(default_factory=list)


def _reply(content: str, finish_reason: str = "stop") -> Any:
    """One provider response with a chosen body and finish reason."""
    message = type("_M", (), {"content": content})()
    choice = type("_C", (), {"message": message, "finish_reason": finish_reason})()
    return type("_R", (), {"choices": [choice], "usage": None})()


def _structured_llm(*, supports: bool, temperature: float = 0.2) -> ResolvedLLM:
    return ResolvedLLM(
        config_id="x", provider="OpenAI-compatible", model="deepseek/deepseek-chat",
        base_url=None, api_key="k", temperature=temperature,
        capabilities=ProviderCapabilities(supports_structured_output=supports),
    )


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


# ── structured output: the instruction is unconditional ──────────────────
@pytest.mark.asyncio
async def test_schema_instruction_is_sent_even_with_native_support() -> None:
    """The regression itself. A model advertising structured output still gets
    the written instruction, because `response_format` may be a weaker mode
    than a schema — or dropped entirely by litellm."""
    seen: dict[str, Any] = {}

    async def capture(**payload: Any) -> Any:
        seen.update(payload)
        return _reply('{"label": "ok"}')

    with patch("litellm.acompletion", side_effect=capture):
        out = await _gateway(0).structured(
            _structured_llm(supports=True), _MSG, _Answer
        )

    assert out.label == "ok"
    system = [m for m in seen["messages"] if m["role"] == "system"]
    assert system, "no system message carried the schema"
    assert "json" in system[0]["content"].lower()
    assert '"label"' in system[0]["content"], "the schema itself must be in the prompt"


@pytest.mark.asyncio
async def test_json_object_tier_for_a_model_without_schema_support() -> None:
    seen: dict[str, Any] = {}

    async def capture(**payload: Any) -> Any:
        seen.update(payload)
        return _reply('{"label": "ok"}')

    with (
        patch("litellm.acompletion", side_effect=capture),
        patch("litellm.supports_response_schema", return_value=False),
    ):
        await _gateway(0).structured(_structured_llm(supports=True), _MSG, _Answer)

    assert seen["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_json_schema_tier_when_the_model_map_says_so() -> None:
    seen: dict[str, Any] = {}

    async def capture(**payload: Any) -> Any:
        seen.update(payload)
        return _reply('{"label": "ok"}')

    with (
        patch("litellm.acompletion", side_effect=capture),
        patch("litellm.supports_response_schema", return_value=True),
    ):
        await _gateway(0).structured(_structured_llm(supports=True), _MSG, _Answer)

    assert seen["response_format"]["type"] == "json_schema"
    # `strict` demands a closed-world schema pydantic does not emit; asking for
    # it is a 400 on the providers that honour it.
    assert "strict" not in seen["response_format"]["json_schema"]


@pytest.mark.asyncio
async def test_no_response_format_when_the_probe_found_none() -> None:
    seen: dict[str, Any] = {}

    async def capture(**payload: Any) -> Any:
        seen.update(payload)
        return _reply('{"label": "ok"}')

    with patch("litellm.acompletion", side_effect=capture):
        await _gateway(0).structured(_structured_llm(supports=False), _MSG, _Answer)

    assert "response_format" not in seen
    assert any(m["role"] == "system" for m in seen["messages"])


@pytest.mark.asyncio
async def test_structured_caps_temperature() -> None:
    seen: dict[str, Any] = {}

    async def capture(**payload: Any) -> Any:
        seen.update(payload)
        return _reply('{"label": "ok"}')

    with patch("litellm.acompletion", side_effect=capture):
        await _gateway(0).structured(
            _structured_llm(supports=False, temperature=0.9), _MSG, _Answer
        )

    assert seen["temperature"] == 0.2


def test_wire_schema_has_no_refs() -> None:
    """Gemini's validator rejects `$ref`, and nested drafts are exactly what
    pydantic factors into `$defs`."""
    text = json.dumps(_wire_schema(_Answer))
    assert "$ref" not in text
    assert "$defs" not in text
    assert '"name"' in text, "the nested model's fields must survive inlining"


# ── structured output: recovery ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_malformed_reply_gets_one_repair_round_trip() -> None:
    replies = ["I'd be happy to help! Here is the table:", '{"label": "orders"}']
    calls = {"n": 0}

    async def flaky(**_: Any) -> Any:
        calls["n"] += 1
        return _reply(replies[calls["n"] - 1])

    with patch("litellm.acompletion", side_effect=flaky):
        out = await _gateway(0).structured(
            _structured_llm(supports=False), _MSG, _Answer
        )

    assert out.label == "orders"
    assert calls["n"] == 2  # prose, then the repair succeeded


@pytest.mark.asyncio
async def test_repair_is_bounded() -> None:
    calls = {"n": 0}

    async def always_prose(**_: Any) -> Any:
        calls["n"] += 1
        return _reply("no JSON here, sorry")

    with (
        patch("litellm.acompletion", side_effect=always_prose),
        pytest.raises(LLMError),
    ):
        await _gateway(0).structured(_structured_llm(supports=False), _MSG, _Answer)

    assert calls["n"] == 2  # one attempt, one repair, then give up


@pytest.mark.asyncio
async def test_truncation_is_reported_as_truncation() -> None:
    """'Invalid JSON' for a cut-off reply sends whoever reads the job stats
    hunting the prompt instead of the token budget."""

    async def cut_off(**_: Any) -> Any:
        return _reply('{"label": "orders", "items": [{"na', finish_reason="length")

    with patch("litellm.acompletion", side_effect=cut_off), pytest.raises(LLMError) as err:
        await _gateway(0).structured(_structured_llm(supports=False), _MSG, _Answer)

    assert "max_tokens" in err.value.message


@pytest.mark.asyncio
async def test_recovers_json_after_a_preamble_containing_a_brace() -> None:
    """The old first-`{`-to-last-`}` slice swallowed the preamble and failed."""

    async def messy(**_: Any) -> Any:
        return _reply('Here is {the} answer:\n```json\n{"label": "orders"}\n```')

    with patch("litellm.acompletion", side_effect=messy):
        out = await _gateway(0).structured(
            _structured_llm(supports=False), _MSG, _Answer
        )

    assert out.label == "orders"


@pytest.mark.asyncio
async def test_rejected_response_format_falls_back_to_the_instruction() -> None:
    """litellm's map reports `json_schema` support for DeepSeek, whose API
    accepts only `json_object`. A wrong map must cost one retry, not a table."""
    seen: list[dict[str, Any]] = []

    async def picky(**payload: Any) -> Any:
        seen.append(payload)
        if "response_format" in payload:
            raise litellm.BadRequestError(
                "response_format json_schema is not supported",
                llm_provider="deepseek", model="deepseek/deepseek-chat",
            )
        return _reply('{"label": "orders"}')

    with (
        patch("litellm.acompletion", side_effect=picky),
        patch("litellm.supports_response_schema", return_value=True),
    ):
        out = await _gateway(0).structured(
            _structured_llm(supports=True), _MSG, _Answer
        )

    assert out.label == "orders"
    assert len(seen) == 2
    assert "response_format" not in seen[1]
    # The fallback works only because the instruction was never conditional.
    assert any(m["role"] == "system" for m in seen[1]["messages"])


@pytest.mark.asyncio
async def test_context_length_error_is_not_retried() -> None:
    """A 400 for an oversized prompt is permanent — re-asking spends the
    caller's deadline and changes nothing."""
    calls = {"n": 0}

    async def too_long(**_: Any) -> Any:
        calls["n"] += 1
        raise litellm.ContextWindowExceededError(
            "prompt is too long", llm_provider="deepseek", model="m",
        )

    with (
        patch("litellm.acompletion", side_effect=too_long),
        pytest.raises(LLMError),
    ):
        await _gateway(0).structured(_structured_llm(supports=True), _MSG, _Answer)

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_recovers_json_after_an_inlined_reasoning_block() -> None:
    async def thinking(**_: Any) -> Any:
        return _reply('<think>maybe {a, b}</think>\n{"label": "orders"}')

    with patch("litellm.acompletion", side_effect=thinking):
        out = await _gateway(0).structured(
            _structured_llm(supports=False), _MSG, _Answer
        )

    assert out.label == "orders"


# ── the scratchpad, in a plain completion ────────────────────────────────
# `structured()` has always stripped an inlined `<think>` block, because JSON
# that will not parse is loud. Prose is silent: a scratchpad merged into
# `content` reads as a paragraph, and a report published it as one. These three
# say what `complete()` now subtracts, and — the last one — what it must not.
def _prose_reply(content: str, **fields: Any) -> Any:
    """A provider response whose message may carry a reasoning channel too."""
    message = type("_M", (), {"content": content, **fields})()
    choice = type("_C", (), {"message": message, "finish_reason": "stop"})()
    return type("_R", (), {"choices": [choice], "usage": None})()


@pytest.mark.asyncio
async def test_a_completion_drops_an_inlined_think_block() -> None:
    async def thinking(**_: Any) -> Any:
        return _prose_reply(
            "<think>Four sentences? Let me redraft.</think>\nRevenue rose 39.8%."
        )

    with patch("litellm.acompletion", side_effect=thinking):
        out = await _gateway(0).complete(_LLM, _MSG)

    assert out.text == "Revenue rose 39.8%."


@pytest.mark.asyncio
async def test_a_completion_drops_a_reasoning_channel_the_provider_also_merged() -> None:
    """The scratchpad in both fields is a subtraction, not a judgement call.

    An upstream that does not implement the split hands the whole trace back in
    `content` while still reporting it in `reasoning_content`. Same text, two
    places — so the reply is what is left when one is taken out of the other.
    """
    scratch = "Wait — is that ten months or twelve? Ten. Let's craft."

    async def merged(**_: Any) -> Any:
        return _prose_reply(
            f"{scratch}\n\nRevenue rose 39.8%.", reasoning_content=scratch
        )

    with patch("litellm.acompletion", side_effect=merged):
        out = await _gateway(0).complete(_LLM, _MSG)

    assert out.text == "Revenue rose 39.8%."


@pytest.mark.asyncio
async def test_a_reply_that_merely_quotes_its_own_reasoning_is_left_alone() -> None:
    """Only a scratchpad *at the front* is subtracted.

    A provider that keeps the channels apart is the normal case, and its
    `content` is the answer whatever the reasoning happens to say. Nothing here
    may go looking for the scratchpad's words inside the prose.
    """

    async def separated(**_: Any) -> Any:
        return _prose_reply(
            "Revenue rose 39.8%. Let's craft.",
            reasoning_content="Let's craft.",
        )

    with patch("litellm.acompletion", side_effect=separated):
        out = await _gateway(0).complete(_LLM, _MSG)

    assert out.text == "Revenue rose 39.8%. Let's craft."


# ── structured output, streamed ───────────────────────────────────────────
# `on_reasoning` changes the transport and nothing else. Everything above this
# line has to keep holding on the streamed path, because the reason to stream a
# reply nobody reads token by token is the *other* channel: a reasoning model
# spends its whole latency in `reasoning_content` before the first byte of
# JSON, and that channel exists only on a streamed request.


def _chunk(*, content: str = "", reasoning: str = "", finish_reason: str = "") -> Any:
    """One streamed chunk, in the shape litellm hands back."""
    delta = type(
        "_D", (), {"content": content or None, "reasoning_content": reasoning or None}
    )()
    choice = type("_C", (), {"delta": delta, "finish_reason": finish_reason})()
    return type("_Chunk", (), {"choices": [choice]})()


def _stream(chunks: list[Any]) -> Any:
    """A provider response that is an async iterator, as `stream=True` returns."""

    class _Stream:
        def __aiter__(self) -> Any:
            async def gen() -> Any:
                for chunk in chunks:
                    yield chunk

            return gen()

    return _Stream()


async def _sink(_piece: str) -> None:
    return None


@pytest.mark.asyncio
async def test_a_streamed_reply_is_reassembled_and_parsed_like_any_other() -> None:
    """The JSON arrives in pieces and the caller cannot tell."""
    thoughts: list[str] = []

    async def collect(piece: str) -> None:
        thoughts.append(piece)

    async def streamed(**payload: Any) -> Any:
        assert payload["stream"] is True
        return _stream(
            [
                _chunk(reasoning="which "),
                _chunk(reasoning="column?"),
                _chunk(content='{"label"'),
                _chunk(content=': "orders"}'),
                _chunk(finish_reason="stop"),
            ]
        )

    with patch("litellm.acompletion", side_effect=streamed):
        out = await _gateway(0).structured(
            _structured_llm(supports=False), _MSG, _Answer, on_reasoning=collect
        )

    assert out.label == "orders"
    assert "".join(thoughts) == "which column?"


@pytest.mark.asyncio
async def test_without_a_sink_the_request_is_not_streamed() -> None:
    """The callers that show nothing while they wait must not pay for a stream
    nobody watches — and this is what keeps every non-streamed test above
    describing the path those callers still take."""
    seen: list[dict[str, Any]] = []

    async def plain(**payload: Any) -> Any:
        seen.append(payload)
        return _reply('{"label": "orders"}')

    with patch("litellm.acompletion", side_effect=plain):
        await _gateway(0).structured(_structured_llm(supports=False), _MSG, _Answer)

    assert "stream" not in seen[0]


@pytest.mark.asyncio
async def test_a_streamed_reply_gets_the_same_repair_round_trip() -> None:
    calls = {"n": 0}

    async def flaky(**_: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return _stream([_chunk(content="Happy to help!")])
        return _stream([_chunk(content='{"label": "orders"}')])

    with patch("litellm.acompletion", side_effect=flaky):
        out = await _gateway(0).structured(
            _structured_llm(supports=False), _MSG, _Answer, on_reasoning=_sink
        )

    assert out.label == "orders" and calls["n"] == 2


@pytest.mark.asyncio
async def test_a_streamed_truncation_is_still_reported_as_truncation() -> None:
    """`finish_reason` rides the last chunk rather than the response object, and
    the message a truncated reply produces is the one that tells somebody to
    raise `max_tokens` — losing it turns a fixable setting into a mystery."""

    async def cut_off(**_: Any) -> Any:
        return _stream(
            [
                _chunk(content='{"label": "ord'),
                _chunk(finish_reason="length"),
            ]
        )

    with patch("litellm.acompletion", side_effect=cut_off), pytest.raises(LLMError) as err:
        await _gateway(0).structured(
            _structured_llm(supports=False), _MSG, _Answer, on_reasoning=_sink
        )

    assert (
        "max_tokens" in str(err.value.message).lower()
        or "cut off" in str(err.value.message).lower()
    )


@pytest.mark.asyncio
async def test_a_rejected_response_format_still_falls_back_when_streamed() -> None:
    """The 400 arrives on the request, before any chunk, so nothing has been
    shown to anybody when the plain re-ask goes out."""
    seen: list[dict[str, Any]] = []

    async def picky(**payload: Any) -> Any:
        seen.append(payload)
        if "response_format" in payload:
            raise litellm.BadRequestError(
                "response_format json_schema is not supported",
                llm_provider="deepseek",
                model="deepseek/deepseek-chat",
            )
        return _stream([_chunk(content='{"label": "orders"}')])

    with (
        patch("litellm.acompletion", side_effect=picky),
        patch("litellm.supports_response_schema", return_value=True),
    ):
        out = await _gateway(0).structured(
            _structured_llm(supports=True), _MSG, _Answer, on_reasoning=_sink
        )

    assert out.label == "orders"
    assert len(seen) == 2 and "response_format" not in seen[1]


@pytest.mark.asyncio
async def test_a_provider_that_goes_silent_is_abandoned() -> None:
    """The request timeout stops applying the moment chunks flow, so the
    streamed path carries its own guard. It measures *silence*: a model that
    thinks for two minutes and says so every second is working."""

    thoughts: list[str] = []

    async def collect(piece: str) -> None:
        thoughts.append(piece)

    async def hangs(**_: Any) -> Any:
        class _Stalled:
            def __aiter__(self) -> Any:
                async def gen() -> Any:
                    yield _chunk(reasoning="starting to think")
                    await asyncio.sleep(3)  # far longer than the gateway's timeout
                    yield _chunk(content='{"label": "too late"}')

                return gen()

        return _Stalled()

    gateway = LiteLLMGateway(timeout_seconds=1, max_retries=0)
    with patch("litellm.acompletion", side_effect=hangs), pytest.raises(LLMError) as err:
        await gateway.structured(
            _structured_llm(supports=False), _MSG, _Answer, on_reasoning=collect
        )

    # The first chunk was read and forwarded; the guard fired on the silence
    # after it, not on the call as a whole.
    assert thoughts == ["starting to think"]
    assert "sent nothing" in str(err.value.message)
