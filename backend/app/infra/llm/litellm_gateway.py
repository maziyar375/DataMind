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
    ProviderCapabilities,
    ResolvedLLM,
    StreamChunk,
)

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

    async def structured(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage], schema: type[T]
    ) -> T:
        """A validated `schema` instance, however the provider gets us there.

        The schema instruction is sent **always**, native mode or not. It is
        cheap, it is the only thing that works on the instructed-only tier, and
        `json_object` mode on several providers requires the word "json" in the
        prompt or returns an empty string. A provider claiming schema support is
        not a reason to trust its output, so the reply is parsed and validated
        here either way.
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
            response = await self._structured_call(payload)
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
