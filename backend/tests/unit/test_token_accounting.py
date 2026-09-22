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

The last section is `0037`'s pair, and it is the same rule sharpened. A cache
count has **three** states where the two above have two — the provider cached
nothing, the provider does not report caching, and the provider served the
prompt from cache — so `None` and `0` have to survive the whole way from the
provider's response to the column. Every assertion there is about which of the
two a given response produces.
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


def _details(**fields: Any) -> Any:
    """A `prompt_tokens_details` block carrying exactly these fields.

    Exactly these, and no others — the absence of a field is the signal under
    test, so a stub that defaulted the rest to `0` or to `None` would answer
    the question for the code.
    """
    return type("_D", (), fields)()


def _usage_with(prompt: int, completion: int, **fields: Any) -> Any:
    return type(
        "_U", (), {"prompt_tokens": prompt, "completion_tokens": completion, **fields}
    )()


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
    """A call with no sink sends exactly the bytes it sent before sinks existed.

    Written as Phase 1's gate, when no caller passed one. Phase 3 wired the six
    pipeline call sites, so the property it pins is now the one that matters
    longer: the *request* is unchanged by the presence or absence of a sink —
    `on_usage` reads the reply and never shapes the ask. The draft graph, the
    eval harness and every `complete()` caller still take this path.
    """
    seen: list[dict[str, Any]] = []

    async def once(**payload: Any) -> Any:
        seen.append(payload)
        return _reply('{"label": "orders"}', usage=_usage(3, 1))

    with patch("litellm.acompletion", side_effect=once):
        out = await _gateway().structured(_llm(), _MSG, _Answer)

    assert out.label == "orders"
    assert "stream" not in seen[0]
    assert "stream_options" not in seen[0]


# ── 0037: a cache count has three states, not two ─────────────────────────
@pytest.mark.asyncio
async def test_an_openai_style_reply_reports_the_tokens_it_served_from_cache() -> None:
    """`prompt_tokens_details.cached_tokens` is where an OpenAI-compatible
    endpoint puts it, and it is a **subset** of `prompt_tokens` — 800 of the
    1,000 below were not read again, they were not 800 extra."""
    seen: list[Usage] = []

    async def once(**_: Any) -> Any:
        return _reply(
            '{"label": "orders"}',
            usage=_usage_with(1000, 8, prompt_tokens_details=_details(cached_tokens=800)),
        )

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    assert seen[0].cache_read_tokens == 800
    assert seen[0].prompt_tokens == 1000
    assert seen[0].cache_read_tokens <= seen[0].prompt_tokens
    assert seen[0].cache_write_tokens is None


@pytest.mark.asyncio
async def test_a_cache_write_is_read_from_either_place_a_provider_puts_it() -> None:
    """LiteLLM normalises Anthropic's `cache_creation_input_tokens` into the
    details block on some paths and passes it through on the usage object on
    others. Both are read, because a number found in only one of them is a
    number this repo cannot see on half its providers."""
    for usage in (
        _usage_with(900, 5, prompt_tokens_details=_details(cache_creation_tokens=400)),
        _usage_with(900, 5, cache_creation_input_tokens=400),
    ):
        seen: list[Usage] = []

        async def once(_u: Any = usage, **_: Any) -> Any:
            return _reply('{"label": "orders"}', usage=_u)

        with patch("litellm.acompletion", side_effect=once):
            await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

        assert seen[0].cache_write_tokens == 400


@pytest.mark.asyncio
async def test_a_provider_that_says_nothing_about_caching_reports_none() -> None:
    """The distinction the columns exist for, at the point it is created.

    `None` here and `0` in the test below drive opposite decisions about
    whether a workload that re-sends the same schema block once per step is
    affordable, and a defensive `or 0` anywhere on this path collapses them.
    """
    seen: list[Usage] = []

    async def once(**_: Any) -> Any:
        return _reply('{"label": "orders"}', usage=_usage(1000, 8))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    assert seen[0].prompt_tokens == 1000
    assert seen[0].cache_read_tokens is None
    assert seen[0].cache_write_tokens is None


@pytest.mark.asyncio
async def test_a_reported_zero_stays_a_zero() -> None:
    """The other half, and the one a truthy check would break: an endpoint
    that reports caching and served none is **measured**, and it is the
    measurement that says the cache is not paying for itself."""
    seen: list[Usage] = []

    async def once(**_: Any) -> Any:
        return _reply(
            '{"label": "orders"}',
            usage=_usage_with(1000, 8, prompt_tokens_details=_details(cached_tokens=0)),
        )

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().structured(_llm(), _MSG, _Answer, on_usage=seen.append)

    assert seen[0].cache_read_tokens == 0
    assert seen[0].cache_read_tokens is not None


@pytest.mark.asyncio
async def test_a_completion_carries_its_cache_counts_into_the_usage_it_hands_out() -> None:
    """`route` is a `complete()` caller, so a count that stopped at the
    gateway's own log line would leave that node's row null for ever."""

    async def once(**_: Any) -> Any:
        return _reply(
            "ANALYTICAL",
            usage=_usage_with(300, 2, prompt_tokens_details=_details(cached_tokens=250)),
        )

    with patch("litellm.acompletion", side_effect=once):
        completion = await _gateway().complete(_llm(), _MSG)

    assert completion.cache_read_tokens == 250
    assert completion.usage(model="m").cache_read_tokens == 250


@pytest.mark.asyncio
async def test_a_stream_reports_the_cache_counts_on_its_usage_chunk() -> None:
    """The reassembled reply is response-shaped precisely so one helper reads
    both transports; this is the assertion that keeps it that way."""
    seen: list[Usage] = []
    chunks = [
        _chunk(content="hello"),
        _chunk(usage=_usage_with(
            1200, 30, prompt_tokens_details=_details(cached_tokens=900)
        )),
    ]

    with patch("litellm.acompletion", return_value=_stream(chunks)):
        async for _ in _gateway().stream(_llm(), _MSG, on_usage=seen.append):
            pass

    assert len(seen) == 1
    assert (seen[0].cache_read_tokens, seen[0].prompt_tokens) == (900, 1200)


def test_the_call_log_omits_a_cache_count_the_provider_never_sent() -> None:
    """A null in the log reads as "cached nothing" to everyone who greps it.

    So an unreported count is **absent from the line**, not logged as `None` —
    the same decision the column made, at the other end of the same path.
    """
    from app.infra.llm.litellm_gateway import _log_call

    lines: list[dict[str, Any]] = []
    with patch("app.infra.llm.litellm_gateway.log") as logger:
        logger.info.side_effect = lambda _event, **kw: lines.append(kw)
        _log_call(
            Usage(prompt_tokens=10, completion_tokens=1, model="m"),
            provider="OpenAI-compatible",
            operation="complete",
        )
        _log_call(
            Usage(
                prompt_tokens=10, completion_tokens=1, model="m",
                cache_read_tokens=0, cache_write_tokens=7,
            ),
            provider="OpenAI-compatible",
            operation="complete",
        )

    assert "cache_read_tokens" not in lines[0]
    assert "cache_write_tokens" not in lines[0]
    assert lines[1]["cache_read_tokens"] == 0
    assert lines[1]["cache_write_tokens"] == 7
