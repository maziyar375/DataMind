from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str          # system | user | assistant
    content: str


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_structured_output: bool = False
    supports_streaming: bool = True
    supports_system_prompt: bool = True
    max_context_tokens: int = 8192


@dataclass(slots=True)
class ResolvedLLM:
    """Carries a decrypted key. Never placed in pipeline state, never logged."""

    config_id: Any
    provider: str
    model: str
    base_url: str | None
    api_key: str = field(repr=False, default="")
    temperature: float = 0.2
    max_tokens: int = 2048
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"ResolvedLLM(provider={self.provider!r}, model={self.model!r}, "
            f"api_key='[REDACTED]')"
        )

    def snapshot(self) -> dict[str, Any]:
        """The model configuration as it was at run time. No secrets."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One piece of a streamed reply, on one of two channels.

    A reasoning model does not start writing its answer; it thinks first, in
    the open, on a channel providers keep separate from the answer itself
    (`reasoning_content` on the delta). Both arrive over the same stream and
    both are the model working, but only one of them is the reply: reasoning
    is a scratchpad, and concatenating it into `state.answer` would publish
    the model's deliberation as if it were the answer.

    So they travel as one type with two fields rather than as a bare `str`.
    A caller that only wants the reply reads `text` and ignores the rest; a
    caller that wants to show the reader that something is happening — the
    minutes a reasoning model can spend before its first word of prose — has
    `reasoning` to show. Exactly one field is non-empty on any chunk a
    provider actually sends.
    """

    text: str = ""
    reasoning: str = ""


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    #: The provider stopped because it hit `max_tokens`, not because the model
    #: had finished. Defaults false, so every caller that does not care is
    #: unchanged — but a caller writing prose *does* care, because the symptom
    #: is a paragraph that ends mid-word and nothing else in the response says
    #: so. `structured` already reads the same signal for its repair path; this
    #: exposes it to `complete` callers rather than leaving them to guess.
    truncated: bool = False


class LLMGateway(Protocol):
    """The model is a text generator, never an actor."""

    async def complete(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> Completion: ...

    # An async generator: its type is a function returning an AsyncIterator, not
    # a coroutine — so this is `def`, not `async def` (callers use `async for`).
    def stream(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[StreamChunk]: ...

    async def structured(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage], schema: type[T]
    ) -> T: ...

    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities: ...
