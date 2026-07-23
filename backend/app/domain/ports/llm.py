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
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class LLMGateway(Protocol):
    """The model is a text generator, never an actor."""

    async def complete(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> Completion: ...

    async def stream(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]: ...

    async def structured(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage], schema: type[T]
    ) -> T: ...

    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities: ...
