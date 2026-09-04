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
from types import SimpleNamespace
from typing import Any, TypeVar

import litellm
from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    ContextWindowExceededError,
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
    EmbeddingCapability,
    ProviderCapabilities,
    ReasoningSink,
    ResolvedLLM,
    StreamChunk,
)
from app.domain.value_objects.llm_params import RESERVED

T = TypeVar("T", bound=BaseModel)
log = get_logger(__name__)

litellm.drop_params = True
litellm.suppress_debug_info = True

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# A reasoning model that inlines its scratchpad rather than returning it in a
# separate field puts prose — with braces in it — ahead of the answer.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# One re-ask when a reply will not parse. A malformed answer is not a transient
# error (so `_acompletion`'s backoff never sees it) and it is not a model
# verdict either — it is usually a fence, a preamble, or a truncation, and all
# three survive being pointed out. Without this, one bad reply costs the caller
# a whole table.
STRUCTURED_REPAIRS = 1

# The embedding model tried when a connection has not pinned one. Only one
# entry, because the product creates exactly two provider kinds and Anthropic
# has no embedding endpoint at all — a fact worth failing on without a network
# call rather than discovering as a 404. Anything OpenAI-compatible (OpenAI
# itself, Ollama, vLLM, LM Studio, a local gateway) may or may not serve this
# model, which is what the probe is for; a deployment serving something else
# names it explicitly and the name is pinned on the connection.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

#: Texts per embedding request. Providers accept far more, but a batch is also
#: a failure unit: one oversized request that trips a token limit loses the
#: whole indexing pass, and sixty-four short questions is comfortably inside
#: every endpoint's input cap.
EMBEDDING_BATCH = 64

# Schema-shaped output is not a place for sampling. The caller's temperature is
# tuned for prose; above this a model starts inventing keys and trailing commas.
MAX_STRUCTURED_TEMPERATURE = 0.2

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


class _StreamedReply:
    """A reassembled stream, shaped like the one-shot response it stands in for.

    `structured` reads `choices[0].message.content` and `finish_reason`, and it
    reads them the same way whichever transport fetched the reply. Giving the
    streamed path this shape is what keeps the parse, the validation and the
    repair round-trip one piece of code with one set of tests, rather than two
    that have to be kept in agreement.
    """

    __slots__ = ("choices",)

    def __init__(self, text: str, finish_reason: str) -> None:
        message = SimpleNamespace(content=text)
        self.choices = [SimpleNamespace(message=message, finish_reason=finish_reason)]


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
        # "Custom" is no longer offered when creating a config, but a row stored
        # before it was removed still resolves through here and still needs the
        # prefix — the read model types `provider` as a plain `str` precisely so
        # such a row keeps working. Do not narrow this to the creatable set.
        needs_openai_prefix = (
            llm.provider in {"OpenAI-compatible", "Custom"} and "/" not in model
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
        # Last, and never over the keys above: the catalog refuses every name
        # in `RESERVED`, so this cannot overwrite the model, the messages or
        # the credentials — the filter is belt-and-braces against a row written
        # by an older, laxer validator.
        kwargs.update(_adapt(llm.provider, llm.params))
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
            truncated=_finish_reason(response) == "length",
        )

    async def stream(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        """Both channels of a streamed reply, in the order the provider sends them.

        Reasoning is forwarded rather than dropped. A reasoning model can spend
        minutes on `reasoning_content` before its first token of `content`, and
        a stream that yields nothing for that whole time is indistinguishable
        from a hung run — which is exactly what it looked like: the pipeline's
        deadline is checked *between* nodes, and a provider still sending
        chunks never trips the request timeout, so a silent node just sat
        there. Callers now have something true to show for the wait.

        The field name is not settled across providers: OpenAI-compatible
        endpoints and litellm normalise to `reasoning_content`, OpenRouter also
        sends `reasoning`, and a model that inlines its scratchpad in `<think>`
        tags sends neither (that one is `_THINK_BLOCK`'s problem, not this
        one). Read both, prefer the normalised name.
        """
        try:
            response = await self._acompletion(
                **self._kwargs(llm, messages), stream=True
            )
            async for chunk in response:
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    yield StreamChunk(text=piece)
                    continue
                thought = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if thought:
                    yield StreamChunk(reasoning=thought)
        except Exception as err:
            raise LLMError(_clean(err)) from err

    # ── structured output ────────────────────────────────────────────────
    def _response_format(self, llm: ResolvedLLM, schema: type[T]) -> dict[str, Any] | None:
        """The strongest JSON mode this model actually accepts, or None.

        Three tiers, because "supports structured output" is not a boolean and
        treating it as one is what broke DeepSeek: the capability probe proves
        `json_object` works, and the caller then sent `json_schema` — a
        *different* feature, which DeepSeek's API rejects outright. Every table
        failed on a 400 that the generator recorded as "the model could not
        describe this table".

        `strict` is deliberately not set. Strict mode demands a closed-world
        schema — `additionalProperties: false` everywhere and every property
        required — which pydantic does not emit and which a `dict[str, str]`
        field (`value_meanings`) cannot satisfy at all.

        The tier chosen here is still only a best guess (see `_structured_call`
        for what happens when the map is wrong).
        """
        if not llm.capabilities.supports_structured_output:
            return None
        if _supports_response_schema(llm.model):
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__.lstrip("_") or "Answer",
                    "schema": _wire_schema(schema),
                },
            }
        return {"type": "json_object"}

    async def _structured_call(self, payload: dict[str, Any]) -> Any:
        """One completion, surviving a `response_format` the provider rejects.

        litellm's model map is a claim about a provider, not a contract with
        it — it reports `json_schema` support for DeepSeek, whose API accepts
        only `json_object`. A wrong map must therefore be recoverable rather
        than fatal: on a 400 we drop the response format and re-ask, because
        the written schema instruction is already in the messages and is on its
        own sufficient. A context-length 400 is exempt — retrying it changes
        nothing and only spends the caller's deadline.
        """
        try:
            return await self._acompletion(**payload)
        except ContextWindowExceededError as err:
            raise LLMError(_clean(err)) from err
        except BadRequestError as err:
            if "response_format" not in payload:
                raise LLMError(_clean(err)) from err
            log.warning("llm_response_format_rejected", error=_clean(err))
            plain = {k: v for k, v in payload.items() if k != "response_format"}
            try:
                return await self._acompletion(**plain)
            except Exception as retry_err:
                raise LLMError(_clean(retry_err)) from retry_err
        except Exception as err:
            raise LLMError(_clean(err)) from err

    async def _consume_structured_stream(
        self, payload: dict[str, Any], on_reasoning: ReasoningSink
    ) -> _StreamedReply:
        """One structured completion, taken as a stream so the thinking shows.

        Streaming and structured output are orthogonal flags on the same
        request — `stream` says how the bytes arrive, `response_format` says
        what they have to contain — so this sends both and reassembles the
        reply before anyone parses it. Nothing downstream can tell the
        difference: the object returned is shaped like the one-shot response,
        and the caller's parse, validation and repair round-trip are unchanged.

        The reason to pay for the reassembly is the *other* channel. A
        reasoning model spends its whole latency in `reasoning_content` before
        it emits a byte of JSON, and that channel exists only on a streamed
        request. Non-streamed, a thirty-second `clarify` can show a spinner and
        nothing else, which is what a hung run looks like.

        **The stall guard replaces the request timeout, which stops applying
        the moment chunks flow** (see `stream`). It measures silence, not
        length: a model that thinks for two minutes and says so every second is
        working, and killing it would be wrong. One that says nothing at all
        for a whole timeout's worth of seconds is not coming back.
        """
        response = await self._acompletion(**payload, stream=True)
        parts: list[str] = []
        finish_reason = ""
        chunks = response.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(
                    chunks.__anext__(), timeout=self._timeout
                )
            except StopAsyncIteration:
                break
            except TimeoutError as err:
                raise LLMError(
                    f"The model sent nothing for {self._timeout}s and the "
                    "request was abandoned."
                ) from err
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = getattr(choice, "finish_reason", "") or finish_reason
            delta = choice.delta
            piece = getattr(delta, "content", None)
            if piece:
                parts.append(piece)
                continue
            thought = getattr(delta, "reasoning_content", None) or getattr(
                delta, "reasoning", None
            )
            if thought:
                await on_reasoning(thought)
        return _StreamedReply("".join(parts), finish_reason)

    async def _structured_stream_call(
        self, payload: dict[str, Any], on_reasoning: ReasoningSink
    ) -> Any:
        """`_structured_call`'s error handling, over the streamed transport.

        Deliberately the same shape, including the `response_format` retry: a
        provider that rejects the format does so on the request, before any
        chunk arrives, so nothing has been shown to anyone when the plain
        re-ask goes out.
        """
        try:
            return await self._consume_structured_stream(payload, on_reasoning)
        except ContextWindowExceededError as err:
            raise LLMError(_clean(err)) from err
        except BadRequestError as err:
            if "response_format" not in payload:
                raise LLMError(_clean(err)) from err
            log.warning("llm_response_format_rejected", error=_clean(err))
            plain = {k: v for k, v in payload.items() if k != "response_format"}
            try:
                return await self._consume_structured_stream(plain, on_reasoning)
            except Exception as retry_err:
                raise LLMError(_clean(retry_err)) from retry_err
        except LLMError:
            raise
        except Exception as err:
            raise LLMError(_clean(err)) from err

    async def structured(
        self,
        llm: ResolvedLLM,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        on_reasoning: ReasoningSink | None = None,
    ) -> T:
        """A validated `schema` instance, however the provider gets us there.

        The schema instruction is sent **always**, native mode or not. It is
        cheap, it is the only thing that works on the instructed-only tier, and
        `json_object` mode on several providers requires the word "json" in the
        prompt or returns an empty string. A provider claiming schema support is
        not a reason to trust its output, so the reply is parsed and validated
        here either way.

        `on_reasoning` switches the transport, and nothing else. Given one, the
        request is streamed so the model's reasoning channel can be forwarded
        while it thinks; the JSON is reassembled and put through exactly the
        same parse, validation and repair below. Given none, the request is the
        single call it has always been — the callers that show nothing while
        they wait should not pay for a stream nobody watches.
        """
        base = self._kwargs(llm, messages)
        base["messages"] = [
            {"role": "system", "content": _json_instruction(schema)},
            *base["messages"],
        ]
        base["temperature"] = min(llm.temperature, MAX_STRUCTURED_TEMPERATURE)
        response_format = self._response_format(llm, schema)
        if response_format is not None:
            base["response_format"] = response_format

        payload = base
        for attempt in range(STRUCTURED_REPAIRS + 1):
            response = await (
                self._structured_stream_call(payload, on_reasoning)
                if on_reasoning is not None
                else self._structured_call(payload)
            )
            raw = (response.choices[0].message.content or "").strip()
            truncated = _finish_reason(response) == "length"
            try:
                return _parse_into(schema, raw)
            except LLMError:
                if attempt >= STRUCTURED_REPAIRS:
                    log.warning(
                        "llm_structured_unparseable",
                        schema=schema.__name__,
                        truncated=truncated,
                        reply_head=raw[:200],
                    )
                    raise LLMError(_unparseable(schema, truncated)) from None
                log.info(
                    "llm_structured_repair",
                    schema=schema.__name__,
                    truncated=truncated,
                    reply_head=raw[:200],
                )
                payload = {
                    **base,
                    "messages": [
                        *base["messages"],
                        {"role": "assistant", "content": raw[:2000]},
                        {"role": "user", "content": _repair_prompt(truncated)},
                    ],
                }
        raise AssertionError("unreachable")  # pragma: no cover

    # ── capability probe ─────────────────────────────────────────────────
    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities:
        """What this endpoint can do, established by asking it.

        `supports_structured_output` means "a JSON response mode works here",
        which is the weakest tier — `_response_format` decides between
        `json_schema` and `json_object` per request from litellm's model map.
        The probe must not test a stronger feature than the caller uses, or a
        model gets a capability it cannot honour (which is how DeepSeek ended
        up being sent `json_schema`).
        """
        messages = [ChatMessage(role="user", content="Reply with the word: ok")]
        await self.complete(llm, messages)

        supports_structured = False
        try:
            payload = self._kwargs(llm, messages)
            payload["response_format"] = {"type": "json_object"}
            # The word "json" must appear in the prompt or several providers
            # (DeepSeek among them) reject the request or return an empty body.
            payload["messages"] = [
                {"role": "user", "content": 'Reply with the JSON object {"ok": true}'}
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


    # ── configured parameters, as the provider will actually see them ────
    def applied_params(self, llm: ResolvedLLM) -> tuple[dict[str, Any], list[str]]:
        """Which configured parameters survive to the wire for *this* model.

        `litellm.drop_params` is on, which is what keeps one prompt working
        across four providers — and it means a parameter a provider does not
        support is dropped in silence. Silence is fine for a request and wrong
        for a *test*: a configuration that stores `seed` against a model whose
        endpoint ignores it should say so on the screen where it was typed,
        not behave differently from what the form claims.

        So the test path asks litellm the same question the request will ask,
        and reports both halves. A failure to answer is not a test failure —
        this is a description of a request, not the request — so it degrades to
        "everything was sent" rather than raising.
        """
        if not llm.params:
            return {}, []
        payload = self._kwargs(llm, [ChatMessage(role="user", content="")])
        try:
            resolved = litellm.utils.get_optional_params(
                model=payload["model"],
                custom_llm_provider=litellm.get_llm_provider(payload["model"])[1],
                **_adapt(llm.provider, llm.params),
            )
        except Exception:  # pragma: no cover - defensive; a description, not a call
            return dict(llm.params), []

        # Read the result under the name the person **typed**, not the one
        # litellm was handed. The two differ for exactly one parameter — a
        # configured Anthropic `metadata` is sent as `user` and arrives in the
        # body as `metadata` again — so the documented name is the honest key
        # on both sides, and a message about a dropped parameter names one the
        # reader can find on the form.
        applied = {name: resolved[name] for name in llm.params if name in resolved}
        dropped = sorted(name for name in llm.params if name not in resolved)
        return applied, dropped

    # ── embeddings (Phase 7) ─────────────────────────────────────────────
    def _embedding_kwargs(self, llm: ResolvedLLM, model: str) -> dict[str, Any]:
        """The same credential shaping `_kwargs` does, minus everything a chat
        call needs and an embedding endpoint rejects (temperature, max_tokens,
        messages).

        The model falls back through the configuration before the constant: an
        explicit argument wins (the caller pinned one), then the provider's own
        `embedding_model`, then `DEFAULT_EMBEDDING_MODEL`. That order is what
        makes configuring an embedding model on the provider mean anything,
        and it leaves a config that sets none exactly where it was.
        """
        name = model or llm.embedding_model or DEFAULT_EMBEDDING_MODEL
        if llm.provider in {"OpenAI-compatible", "Custom"} and "/" not in name:
            name = f"openai/{name}"
        kwargs: dict[str, Any] = {"model": name, "timeout": self._timeout}
        if llm.api_key:
            kwargs["api_key"] = llm.api_key
        if llm.base_url:
            kwargs["api_base"] = llm.base_url
        kwargs.update(_adapt(llm.provider, llm.embedding_params))
        return kwargs

    async def embed(
        self, llm: ResolvedLLM, texts: Sequence[str], *, model: str = ""
    ) -> list[list[float]]:
        """Vectors for a batch of texts, in the order they were given.

        Order is the contract: the caller pairs the results back onto its own
        rows by index, so a provider that returned them out of order would
        silently give every template somebody else's vector. litellm normalises
        the OpenAI response shape, which carries an explicit `index`; it is
        sorted on rather than trusted.

        Batched at `EMBEDDING_BATCH`, and one failed batch fails the call —
        this is never on a request path (indexing runs in the worker, and the
        ask path embeds exactly one question), so a partial answer would be a
        half-indexed store nobody could tell from a fully indexed one.
        """
        wanted = list(texts)
        if not wanted:
            return []

        out: list[list[float]] = []
        for start in range(0, len(wanted), EMBEDDING_BATCH):
            batch = wanted[start : start + EMBEDDING_BATCH]
            payload = self._embedding_kwargs(llm, model)
            payload["input"] = batch
            try:
                response = await litellm.aembedding(**payload)
            except Exception as err:
                raise LLMError(_clean(err)) from err
            out.extend(_vectors(response, len(batch)))
        return out

    async def probe_embedding(
        self, llm: ResolvedLLM, *, model: str = ""
    ) -> EmbeddingCapability:
        """Whether this endpoint embeds, and at what width — asked, not assumed.

        Anthropic is refused without a call: it has no embedding endpoint, and
        a probe that spends a request to be told so is a probe that reports
        "unavailable" for a network blip and "unavailable" for a permanent
        fact in the same sentence.
        """
        if llm.provider == "Anthropic":
            return EmbeddingCapability(
                reason=(
                    "Anthropic does not offer an embedding endpoint. Point this "
                    "connection at an OpenAI-compatible provider to use "
                    "embedding search."
                ),
            )

        name = model or llm.embedding_model or DEFAULT_EMBEDDING_MODEL
        try:
            vectors = await self.embed(llm, ["ok"], model=name)
        except LLMError as err:
            return EmbeddingCapability(model=name, reason=str(err))

        if not vectors or not vectors[0]:
            return EmbeddingCapability(
                model=name,
                reason="The endpoint accepted the request but returned no vector.",
            )
        return EmbeddingCapability(
            available=True, model=name, dimension=len(vectors[0])
        )


#: The one place a parameter may be renamed, and it renames exactly two.
#:
#: The catalog stores every parameter under the **provider's own** name, which
#: is the point: a configuration for Anthropic should read like Anthropic's
#: reference. litellm speaks OpenAI's dialect and translates on the way out, so
#: two of Anthropic's names have to be spoken to it in OpenAI's:
#:
#: * `metadata` — Anthropic documents `metadata.user_id`; litellm reserves the
#:   `metadata` kwarg for its own logging callbacks and would swallow it, and
#:   it builds `{"metadata": {"user_id": …}}` from `user`.
#: * nothing else. `stop_sequences`, `top_k` and `thinking` all reach the
#:   Anthropic body under their own names, verified in
#:   `test_provider_params.py` against litellm's own parameter mapping.
#:
#: Kept as data with the translation beside it so "does DataMind rename any of
#: this?" is answered by reading eight lines rather than by trusting a comment.
_ANTHROPIC_TO_LITELLM: dict[str, str] = {"metadata": "user"}


def _adapt(provider: str, params: dict[str, Any]) -> dict[str, Any]:
    """Configured parameters in the kwargs litellm accepts for this provider.

    `RESERVED` is re-applied here rather than trusted from the write path: this
    is the last point before a request is built, and a row written by an older
    validator must not be able to replace the model or the credentials.
    """
    if not params:
        return {}
    out: dict[str, Any] = {}
    for name, value in params.items():
        if name in RESERVED:
            continue
        if provider == "Anthropic" and name == "metadata":
            # Anthropic's own shape is `{"user_id": "…"}`; litellm builds
            # exactly that from `user`. A value without the documented key is
            # dropped rather than guessed at — the catalog already refuses it.
            user_id = value.get("user_id") if isinstance(value, dict) else None
            if isinstance(user_id, str) and user_id:
                out["user"] = user_id
            continue
        out[name] = value
    return out


def _vectors(response: Any, expected: int) -> list[list[float]]:
    """The embeddings out of a provider response, in request order.

    Sorted on the response's own `index` rather than trusting arrival order,
    and length-checked: a batch that came back short would shift every
    subsequent pairing by one, which is the kind of bug that shows up as "the
    matcher got worse" six weeks later.
    """
    data = list(getattr(response, "data", None) or [])
    rows: list[tuple[int, list[float]]] = []
    for position, item in enumerate(data):
        if isinstance(item, dict):
            vector = item.get("embedding") or []
            index = item.get("index", position)
        else:
            vector = getattr(item, "embedding", None) or []
            index = getattr(item, "index", position)
        rows.append((int(index), [float(v) for v in vector]))

    rows.sort(key=lambda pair: pair[0])
    out = [vector for _, vector in rows]
    if len(out) != expected:
        raise LLMError(
            f"The embedding endpoint returned {len(out)} vectors for "
            f"{expected} inputs."
        )
    return out


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


def _supports_response_schema(model: str) -> bool:
    """Whether litellm's model map says this model accepts a `json_schema`.

    Wrapped because the answer is data, not code: an unknown model raises or
    returns nothing, and the honest reading of "I don't know" is the weaker
    tier, which every OpenAI-compatible endpoint accepts.
    """
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:
        return False


def _wire_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """`model_json_schema()` with `$ref`/`$defs` resolved away.

    Gemini's schema validator rejects `$ref` outright, and it is exactly the
    nested drafts — a table's columns and metrics — that pydantic factors into
    `$defs`. Inlining costs a few hundred prompt tokens and makes the schema
    portable across every provider.
    """
    raw = schema.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(node: Any, depth: int) -> Any:
        # Self-referential models would recurse forever; none of ours are, but
        # a depth stop is cheaper than trusting that to stay true.
        if depth > 12:
            return {}
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.rsplit("/", 1)[-1], {})
                merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
                return inline(merged, depth + 1)
            return {k: inline(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(item, depth + 1) for item in node]
        return node

    inlined = inline(raw, 0)
    return inlined if isinstance(inlined, dict) else raw


def _json_instruction(schema: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object and nothing else. No prose before or "
        "after it, no markdown fences, no explanation. Every key below must be "
        "present; use an empty string, an empty list, or an empty object where "
        "you have nothing to say. The JSON must match this schema:\n"
        f"{json.dumps(_wire_schema(schema))}"
    )


def _repair_prompt(truncated: bool) -> str:
    if truncated:
        return (
            "That reply was cut off before the JSON was complete. Send it again, "
            "shorter: keep every required key, but drop optional entries and "
            "keep each description to one short sentence. Output only the JSON "
            "object."
        )
    return (
        "That was not a single valid JSON object. Send the same answer again as "
        "raw JSON only — no prose, no markdown fences, no trailing commas."
    )


def _unparseable(schema: type[BaseModel], truncated: bool) -> str:
    """Say which failure it was. 'Invalid JSON' for a truncation sends whoever
    reads the job stats hunting the prompt instead of the token budget.

    The reply itself is logged, never returned: this string reaches the user.
    """
    name = schema.__name__.lstrip("_")
    if truncated:
        return (
            f"The model's {name} answer was cut off at the output token limit. "
            "Raise max_tokens for this provider."
        )
    return f"The model did not return valid {name} JSON."


def _finish_reason(response: Any) -> str:
    try:
        return str(getattr(response.choices[0], "finish_reason", "") or "")
    except (AttributeError, IndexError):  # pragma: no cover - defensive
        return ""


def _candidates(raw: str) -> list[str]:
    """Every substring of a reply that might be the JSON object, best first.

    The old approach — first `{` to last `}` — is one guess, and it is wrong
    whenever a model writes a brace in its preamble or emits two fenced blocks.
    Each candidate is tried in turn instead.
    """
    text = _THINK_BLOCK.sub("", raw).strip()
    out: list[str] = [text]
    out.extend(match.group(1).strip() for match in _JSON_FENCE.finditer(text))

    # Balanced-brace scan from each opening brace, ignoring braces inside
    # strings — a description containing "{" must not close the object early.
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth, in_string, escaped = 0, False, False
        for end in range(start, len(text)):
            current = text[end]
            if escaped:
                escaped = False
                continue
            if current == "\\":
                escaped = True
            elif current == '"':
                in_string = not in_string
            elif not in_string:
                if current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        out.append(text[start : end + 1])
                        break
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in out:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _parse_into(schema: type[T], raw: str) -> T:  # noqa: UP047  (matches TypeVar used above)
    for candidate in _candidates(raw):
        try:
            return schema.model_validate_json(candidate)
        except (PydanticValidationError, ValueError):
            continue
    raise LLMError(f"The model did not return valid {schema.__name__} JSON.")


def _clean(err: Exception) -> str:
    text = str(err)
    # Provider errors sometimes echo the request, including the key.
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", "[REDACTED]", text)
    text = re.sub(r"('api_key':\s*)'[^']*'", r"\1'[REDACTED]'", text)
    return text[:500]
