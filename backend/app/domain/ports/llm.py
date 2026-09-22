from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
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
    #: Extra request parameters in the *provider's* own vocabulary, already
    #: validated against that provider's API (`value_objects/llm_params.py`).
    #: The adapter is the only layer allowed to rename one, and it renames
    #: exactly where litellm spells a documented parameter differently.
    #: Empty — the default, and every configuration that sets none — leaves the
    #: request byte-identical to before the field existed.
    params: dict[str, Any] = field(default_factory=dict)
    #: The model this endpoint is asked for vectors, and that request's own
    #: extra parameters. Separate from `model` because one endpoint serves
    #: both, and an embedding request rejects most of what a chat request needs.
    embedding_model: str = ""
    embedding_params: dict[str, Any] = field(default_factory=dict)
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"ResolvedLLM(provider={self.provider!r}, model={self.model!r}, "
            f"api_key='[REDACTED]')"
        )

    def snapshot(self) -> dict[str, Any]:
        """The model configuration as it was at run time. No secrets.

        `params` is in here for the same reason `temperature` is: it changes
        the answer, so a past run is only explainable with it. It is a
        provider's own documented parameters and carries no credential —
        `logit_bias` and `stop` are as much a setting as `temperature` — and
        the catalog refuses `api_key` and everything else the gateway owns, so
        there is no name under which one could arrive here.
        """
        snapshot: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        # Absent rather than empty when nothing is configured, so a run
        # recorded before this field existed and one made by a configuration
        # that sets no parameters read back the same.
        if self.params:
            snapshot["params"] = dict(self.params)
        return snapshot


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


def add_reported(total: int | None, reported: int | None) -> int | None:
    """Add a count that may not have been reported, keeping *unreported* intact.

    The arithmetic `Usage`'s two cache counts need, written once here beside
    the rule it serves rather than in each of the four places that accumulate
    them. `None` is *never reported*, not zero: a total stays `None` until
    some call reports a figure, and thereafter sums only the calls that
    reported one — which is exactly what SQL's `SUM` does over a nullable
    column, and why the read side in `usage_service` can rely on both halves
    meaning the same thing.

    The two obvious inline forms are both wrong, which is why this exists:
    `(total or 0) + (reported or 0)` turns *never reported* into `0`, and
    `total + reported` raises on the first call of every run.
    """
    if reported is None:
        return total
    return reported if total is None else total + reported


@dataclass(frozen=True, slots=True)
class Usage:
    """What one provider call spent, as the provider reported it.

    It travels back from calls that have nowhere to put it: `structured`
    returns a validated model and `stream` yields text, so neither can return
    a number. `model` rides along so the log line names the *resolved* model
    (post-prefix), and a sink's receiver — a node, a worker — does not have to
    reach back into the `ResolvedLLM` for a call it only observed.

    A zero is not always "no tokens": a provider that sends no usage on a
    streamed reply reports nothing, and that is recorded as it stands rather
    than estimated, because an estimate in the same column as a measurement is
    indistinguishable from one.

    **The two cache counts take that rule one step further, and are `None`
    rather than `0`.** Three facts have to stay apart here, and two columns of
    `int` can only hold two of them: the provider cached nothing (`0`), the
    provider does not report caching at all (`None`), and the provider served
    the whole prompt from cache. The first two drive opposite decisions about
    whether a workload that re-sends the same schema block per step is
    affordable — see `docs/plans/deep-analysis-mode.md` §2.1 — so a default of
    `0` would quietly turn *"this endpoint says nothing"* into *"this endpoint
    cached nothing"*.

    **Both are a subset of `prompt_tokens`, never an addition to it.** OpenAI
    reports `cached_tokens` as the part of the prompt it did not have to read
    again; LiteLLM folds Anthropic's separate cache figures into
    `prompt_tokens` for the same reason. So a screen adding either to the input
    figure double-counts, and the only honest rendering subdivides the input
    rather than stacking on top of it.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    model: str = ""
    #: Prompt tokens the provider served from its cache — a subset of
    #: `prompt_tokens`. `None` where the provider said nothing about caching.
    cache_read_tokens: int | None = None
    #: Prompt tokens the provider charged to *write* into its cache — also a
    #: subset of `prompt_tokens`. `None` where nothing was reported.
    cache_write_tokens: int | None = None


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
    #: The provider's own cache counts, `None` where it reported none — the
    #: rule and the subset relationship are `Usage`'s, stated once there.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    def usage(self, model: str = "") -> Usage:
        """The same numbers, in the shape a sink hands out.

        So a `complete` caller feeds the same recorder a `structured` or
        `stream` caller feeds, and there is one accumulation path rather than
        two that can drift. The model name is the caller's to supply: the
        completion was built by the gateway, which knows the resolved name, but
        `Completion` predates usage reporting and widening it would touch every caller.
        """
        return Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            latency_ms=self.latency_ms,
            model=model,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingCapability:
    """Whether this endpoint will embed text, and with what.

    The dimension is **measured**, never assumed: it comes back from a real
    call, because a provider that serves `text-embedding-3-small` at 1536 and
    a local gateway serving something else at 768 are indistinguishable from
    the model name alone, and a store half-indexed at each width is a store
    where cosine means nothing. `reason` carries the provider's own sentence
    when it says no, so the UI can show why rather than "unavailable".
    """

    available: bool = False
    model: str = ""
    dimension: int = 0
    reason: str = ""


#: Where a streamed structured call sends the model's reasoning as it arrives.
#: Pieces, not a transcript: the caller decides what to do with each one, and
#: nothing here keeps them.
ReasoningSink = Callable[[str], Awaitable[None]]

#: Where a call reports what it spent. Fired once per provider *attempt*, by the
#: coroutine that made it — so concurrent callers never share a bucket, which a
#: counter on the gateway instance could not promise (one gateway serves the
#: report worker's whole narration wave). Synchronous on purpose: a sink is an
#: accumulation, not I/O, and awaiting one inside the gateway would let a slow
#: recorder hold a provider connection open.
UsageSink = Callable[[Usage], None]


class LLMGateway(Protocol):
    """The model is a text generator, never an actor."""

    async def complete(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> Completion: ...

    # An async generator: its type is a function returning an AsyncIterator, not
    # a coroutine — so this is `def`, not `async def` (callers use `async for`).
    def stream(
        self,
        llm: ResolvedLLM,
        messages: Sequence[ChatMessage],
        *,
        on_usage: UsageSink | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    # `on_reasoning` asks for the streamed transport and nothing else: same
    # schema, same validation, same repair. It exists because the reasoning
    # channel only arrives on a streamed request, so a node that wants to show
    # a reasoning model thinking has no other way to see it. Implementations
    # that cannot stream may ignore it — the JSON is what the caller branches
    # on, and it is unchanged either way.
    #
    # `on_usage` is the same shape for the same reason: an optional sink, so a
    # caller with no use for the number is byte-identical to before it existed,
    # and only the callers that want it change. A repaired call fires it twice,
    # once per attempt, because both attempts were paid for.
    async def structured(
        self,
        llm: ResolvedLLM,
        messages: Sequence[ChatMessage],
        schema: type[T],
        *,
        on_reasoning: ReasoningSink | None = None,
        on_usage: UsageSink | None = None,
    ) -> T: ...

    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities: ...

    # Embeddings are a *second* endpoint on the same credentials, not a second
    # gateway: Phase 7 of the learning loop needs vectors and explicitly buys
    # "no new Python dependency and no new deployment unit" by putting them
    # through here. `model` names the embedding model, which is never the chat
    # model — passing `llm` alone would send a chat model id to an embedding
    # endpoint.
    async def embed(
        self, llm: ResolvedLLM, texts: Sequence[str], *, model: str = ""
    ) -> list[list[float]]: ...

    async def probe_embedding(
        self, llm: ResolvedLLM, *, model: str = ""
    ) -> EmbeddingCapability: ...
