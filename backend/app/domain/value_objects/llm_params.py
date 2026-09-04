"""Which extra request parameters each provider actually accepts.

A provider configuration used to be five fields, and every model in the product
ran at whatever `temperature` and `max_tokens` the form offered. Anything
else — nucleus sampling, a seed, stop sequences, Anthropic's extended
thinking — was unreachable without editing `_kwargs`.

**This is a catalog, not an open dictionary, and that is the whole design.**
The brief for this module is two sentences that pull against each other: a
configuration must be *strictly* what the selected provider documents, and it
must not need a code change every time a provider ships a parameter. A free
`dict[str, Any]` satisfies the second and abandons the first — it lets somebody
save `top_k` against OpenAI, where litellm silently drops it and the row then
describes a behaviour that never happens. A hand-written form field per
parameter satisfies the first and abandons the second.

So the parameter set is **data**:

* every entry is a parameter the provider's own API reference documents, under
  the provider's own name for it — `stop_sequences` for Anthropic, `stop` for
  OpenAI-compatible; the gateway adapts where litellm spells one differently,
  because adapting is what a gateway is for;
* the entry carries enough for a form to be *generated* from it — type, range,
  documented values, one line of prose — so adding a parameter is a line here
  and **no UI change and no request-shaping change**;
* `validate` refuses a name the selected provider does not document, and says
  which names it does, because a config that silently drops half of what was
  typed into it is worse than one that refuses to save.

Two deliberate exclusions, both about honesty rather than caution:

* **Nothing the gateway itself owns.** `model`, `messages`, `stream`,
  `response_format`, `temperature`, `max_tokens`, `api_key`, `api_base` and
  `timeout` are set per call from the config's own columns and from the code
  path (structured output clamps temperature and picks a JSON mode; the
  streamed transport is chosen by the caller). A second source of truth for any
  of them would be a setting that works on three call sites and is overwritten
  on the fourth. They are `RESERVED` below and refused everywhere, including
  inside `extra_body`.
* **Nothing that changes the shape of the reply.** `n`, `tools`, `functions`,
  `logprobs`, `modalities`, `audio` and `prediction` are all real OpenAI
  parameters and all of them make `choices[0].message.content` mean something
  other than what the pipeline reads. They are supported by the provider and
  unsupported by this product, which is a different sentence and belongs here
  rather than in a runtime failure.

`extra_body` is the one open door, and it is open because the OpenAI *client*
documents it: it is the standard way to send body fields an OpenAI-compatible
endpoint defines for itself (vLLM's `top_k`, OpenRouter's `provider` routing).
Anything in it is the deployment's own contract with its own gateway, so the
only thing checked is that it does not smuggle a reserved key back in.

Pure data and pure functions: no framework, no litellm, no I/O — the layer rule
holds, and both the API (which validates) and the gateway (which shapes the
request) read the one catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The two creatable provider kinds, plus the legacy value still on rows made
#: before it was dropped. `litellm_gateway` treats `Custom` as
#: OpenAI-compatible; so does this, or an old row could not be edited at all.
OPENAI_COMPATIBLE = "OpenAI-compatible"
ANTHROPIC = "Anthropic"
LEGACY_CUSTOM = "Custom"

#: Keys the gateway sets itself on every request. Never configurable — see the
#: module docstring. Also refused inside `extra_body`, which would otherwise be
#: a way around the whole list.
RESERVED: frozenset[str] = frozenset(
    {
        "model", "messages", "input", "stream", "response_format",
        "temperature", "max_tokens", "timeout", "api_key", "api_base",
        "api_version", "base_url", "custom_llm_provider", "num_retries",
        "mock_response",
    }
)

#: How many parameters one configuration may carry. A ceiling rather than a
#: setting: a request body assembled from an unbounded stored dict is an
#: unbounded request body, and nobody needs thirty.
MAX_PARAMS = 24

#: The longest a single string value may be. Generous for a stop sequence or a
#: cache key, and short of "somebody pasted a prompt in here".
MAX_STRING = 2_000

#: `stop` / `stop_sequences` are lists, and both providers cap them low.
MAX_LIST_ITEMS = 8


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One documented request parameter, in enough detail to render a field.

    `summary` is written for the person configuring the model, not for a
    developer: it is what appears under the input, and it is the only place the
    provider's semantics are stated in the product.
    """

    name: str
    #: `number | integer | boolean | string | string_list | object`.
    kind: str
    summary: str
    minimum: float | None = None
    maximum: float | None = None
    #: The values the provider documents. Empty means any string.
    choices: tuple[str, ...] = ()
    #: For an object parameter, the keys the provider documents. Empty means
    #: the provider defines the body itself (`extra_body`, `logit_bias`).
    object_keys: tuple[str, ...] = ()
    #: A valid value, shown as the field's placeholder. JSON for the shapes a
    #: text input cannot express.
    example: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The wire form the parameter catalog endpoint serves."""
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "summary": self.summary,
            "example": self.example,
        }
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.choices:
            out["choices"] = list(self.choices)
        if self.object_keys:
            out["object_keys"] = list(self.object_keys)
        return out


# ── OpenAI-compatible: POST /v1/chat/completions ─────────────────────────
# Names and ranges are the OpenAI Chat Completions reference. Everything here
# is understood by any endpoint claiming that API; an endpoint that does not
# implement one either ignores it or 400s, and the config's Test button is
# where that is found out (`LiteLLMGateway.applied_params` reports what
# survives for the model actually named).
_OPENAI_COMPLETION: tuple[ParamSpec, ...] = (
    ParamSpec(
        "top_p", "number",
        "Nucleus sampling: consider only the tokens making up this much "
        "probability mass. OpenAI recommends changing this or temperature, "
        "not both.",
        minimum=0.0, maximum=1.0, example="0.9",
    ),
    ParamSpec(
        "frequency_penalty", "number",
        "Penalise tokens by how often they have already appeared. Positive "
        "values reduce verbatim repetition.",
        minimum=-2.0, maximum=2.0, example="0.2",
    ),
    ParamSpec(
        "presence_penalty", "number",
        "Penalise tokens that have appeared at all. Positive values push the "
        "model towards new subjects.",
        minimum=-2.0, maximum=2.0, example="0.2",
    ),
    ParamSpec(
        "seed", "integer",
        "Best-effort determinism: the same seed and the same request should "
        "return the same completion. Not a guarantee — the provider may "
        "change the backend underneath it.",
        example="42",
    ),
    ParamSpec(
        "stop", "string_list",
        "Up to four sequences that end the completion. The sequence itself is "
        "not returned.",
        example='["\\n\\n"]',
    ),
    ParamSpec(
        "logit_bias", "object",
        "Token id → bias, −100 to 100. A bias of −100 bans a token and 100 "
        "forces it. Token ids are the tokenizer's, not words.",
        example='{"50256": -100}',
    ),
    ParamSpec(
        "user", "string",
        "A stable identifier for the end user, which OpenAI uses to detect "
        "abuse. Send an opaque id, never an email address.",
        example="team-analytics",
    ),
    ParamSpec(
        "service_tier", "string",
        "Which processing tier to bill and queue this request under.",
        choices=("auto", "default", "flex", "scale", "priority"),
        example="auto",
    ),
    ParamSpec(
        "store", "boolean",
        "Ask the provider to retain the completion for its own dashboards and "
        "evaluations. Off unless your account wants the history.",
        example="false",
    ),
    ParamSpec(
        "reasoning_effort", "string",
        "How long a reasoning model may think before answering. Ignored by "
        "models that do not reason.",
        choices=("minimal", "low", "medium", "high"),
        example="low",
    ),
    ParamSpec(
        "max_completion_tokens", "integer",
        "The newer ceiling on generated tokens, counting a reasoning model's "
        "hidden thinking. Set this as well as Max tokens when you point this "
        "configuration at a reasoning model.",
        minimum=1, maximum=1_000_000, example="4096",
    ),
    ParamSpec(
        "prompt_cache_key", "string",
        "Groups requests that share a prefix so the provider can serve them "
        "from its prompt cache.",
        example="datamind-sql",
    ),
    ParamSpec(
        "safety_identifier", "string",
        "An opaque, stable id for the end user, used for policy enforcement. "
        "The replacement OpenAI recommends over `user`.",
        example="u_8f21",
    ),
    ParamSpec(
        "extra_body", "object",
        "Body fields this endpoint defines for itself — vLLM's top_k, "
        "OpenRouter's provider routing. Sent verbatim; the OpenAI client's "
        "own passthrough. DataMind checks nothing inside it beyond refusing "
        "to let it overwrite the request.",
        example='{"top_k": 40}',
    ),
)

# ── OpenAI-compatible: POST /v1/embeddings ───────────────────────────────
# `encoding_format` is deliberately absent. `float` is the only value whose
# reply this gateway can read — `base64` would come back as a string where
# `_vectors` expects numbers — so offering the parameter would be offering one
# legal value and one way to break the index.
_OPENAI_EMBEDDING: tuple[ParamSpec, ...] = (
    ParamSpec(
        "dimensions", "integer",
        "Ask for shorter vectors. Supported by text-embedding-3 and later; "
        "the width DataMind pins is measured from the reply, so this changes "
        "the pin rather than contradicting it.",
        minimum=1, maximum=16_384, example="512",
    ),
    ParamSpec(
        "user", "string",
        "A stable, opaque end-user identifier, as on completions.",
        example="team-analytics",
    ),
    ParamSpec(
        "extra_body", "object",
        "Body fields this embedding endpoint defines for itself. Sent "
        "verbatim.",
        example='{"truncate": "END"}',
    ),
)

# ── Anthropic: POST /v1/messages ─────────────────────────────────────────
# Anthropic's own names, from the Messages API reference — `stop_sequences`,
# not `stop`; `metadata.user_id`, not `user`. Where litellm spells one
# differently the gateway translates, which is the one place a name may be
# rewritten.
_ANTHROPIC_COMPLETION: tuple[ParamSpec, ...] = (
    ParamSpec(
        "top_p", "number",
        "Nucleus sampling. Anthropic recommends adjusting this or "
        "temperature, not both.",
        minimum=0.0, maximum=1.0, example="0.9",
    ),
    ParamSpec(
        "top_k", "integer",
        "Sample from only the K most likely tokens. Anthropic describes it as "
        "an advanced control to remove long-tail responses.",
        minimum=0, maximum=500, example="40",
    ),
    ParamSpec(
        "stop_sequences", "string_list",
        "Custom sequences that stop generation. The model's stop_reason "
        "becomes stop_sequence when one fires.",
        example='["\\n\\nHuman:"]',
    ),
    ParamSpec(
        "thinking", "object",
        "Extended thinking. `{\"type\": \"enabled\", \"budget_tokens\": N}` — "
        "the budget must be at least 1024 and below Max tokens. DataMind "
        "already forwards a model's thinking to the step trail.",
        object_keys=("type", "budget_tokens"),
        example='{"type": "enabled", "budget_tokens": 2048}',
    ),
    ParamSpec(
        "metadata", "object",
        "`{\"user_id\": \"…\"}` — an opaque identifier for the end user, used "
        "by Anthropic for abuse detection. Never a name, an email or a phone "
        "number.",
        object_keys=("user_id",),
        example='{"user_id": "u_8f21"}',
    ),
)

#: Anthropic serves no embedding endpoint at all, which is why
#: `probe_embedding` refuses it without spending a network call. An empty
#: catalog is the same fact stated where a form can read it.
_ANTHROPIC_EMBEDDING: tuple[ParamSpec, ...] = ()


COMPLETION_PARAMS: dict[str, tuple[ParamSpec, ...]] = {
    OPENAI_COMPATIBLE: _OPENAI_COMPLETION,
    LEGACY_CUSTOM: _OPENAI_COMPLETION,
    ANTHROPIC: _ANTHROPIC_COMPLETION,
}

EMBEDDING_PARAMS: dict[str, tuple[ParamSpec, ...]] = {
    OPENAI_COMPATIBLE: _OPENAI_EMBEDDING,
    LEGACY_CUSTOM: _OPENAI_EMBEDDING,
    ANTHROPIC: _ANTHROPIC_EMBEDDING,
}


def completion_specs(provider: str) -> tuple[ParamSpec, ...]:
    """What this provider documents for a chat request. Empty for one nobody
    has catalogued, which reads as "configure nothing here" rather than as an
    error — a row can outlive the provider kind it was created under."""
    return COMPLETION_PARAMS.get(provider, ())


def embedding_specs(provider: str) -> tuple[ParamSpec, ...]:
    return EMBEDDING_PARAMS.get(provider, ())


def catalog(provider: str) -> dict[str, Any]:
    """The whole configurable surface of one provider, for a form to render."""
    return {
        "provider": provider,
        "completion": [spec.as_dict() for spec in completion_specs(provider)],
        "embedding": [spec.as_dict() for spec in embedding_specs(provider)],
        #: Whether this provider has an embedding endpoint at all. Stated
        #: rather than inferred from an empty list, so the UI can say *why*.
        "embedding_supported": bool(embedding_specs(provider)),
    }


def full_catalog() -> list[dict[str, Any]]:
    """Every creatable provider's catalog. The legacy `Custom` kind is left
    out: nothing creates one, and offering it in a picker would."""
    return [catalog(OPENAI_COMPATIBLE), catalog(ANTHROPIC)]


class ParamError(ValueError):
    """A parameter the selected provider does not document, or a value outside
    what it documents. Carries the sentence the API returns verbatim."""


def validate(
    provider: str,
    params: Any,
    *,
    specs: tuple[ParamSpec, ...],
    what: str,
    noun: str,
) -> dict[str, Any]:
    """The stored parameter map, checked against one provider's own API.

    Returns a normalised copy — numbers coerced to `float`/`int`, nothing else
    rewritten — so the value that reaches the request is the value that was
    validated. Raises `ParamError` with a sentence naming what is wrong and,
    for an unknown name, what the provider does accept.
    """
    if params is None or params == {}:
        return {}
    if not isinstance(params, dict):
        raise ParamError(f"{what}s must be a JSON object of names to values.")
    if len(params) > MAX_PARAMS:
        raise ParamError(
            f"{what}: at most {MAX_PARAMS} parameters. "
            f"This configuration has {len(params)}."
        )

    by_name = {spec.name: spec for spec in specs}
    out: dict[str, Any] = {}
    for name, value in params.items():
        if not isinstance(name, str):
            raise ParamError(f"{what}: parameter names must be strings.")
        if name in RESERVED:
            raise ParamError(
                f"“{name}” is set by DataMind on every request and cannot be "
                "configured here."
            )
        spec = by_name.get(name)
        if spec is None:
            raise ParamError(_unknown(provider, name, by_name, noun))
        out[name] = _value(spec, value, what)
    return out


def _unknown(
    provider: str, name: str, by_name: dict[str, ParamSpec], noun: str
) -> str:
    """Named, and answerable.

    The provider leads rather than the article: "a OpenAI-compatible" and "an
    Anthropic" are both wrong half the time, and a sentence that has to guess
    an article for a provider name it has never seen will guess wrong. And it
    lists what *is* accepted, because a refusal somebody cannot act on is a
    refusal they will retype.
    """
    if not by_name:
        return (
            f"{provider} takes no {noun}s in DataMind, so “{name}” cannot be "
            "stored."
        )
    known = ", ".join(sorted(by_name))
    return f"{provider} has no {noun} “{name}”. It accepts: {known}."


def _value(spec: ParamSpec, value: Any, what: str) -> Any:
    """One value against one spec. Split out because every branch below is a
    sentence somebody reads on a failed save."""
    label = f"{what} “{spec.name}”"

    if spec.kind == "boolean":
        if not isinstance(value, bool):
            raise ParamError(f"{label} must be true or false.")
        return value

    if spec.kind in {"number", "integer"}:
        # `bool` is an `int` in Python, and letting `True` through as 1 would
        # store a value the provider reads as a number and the form shows as a
        # checkbox next time.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ParamError(f"{label} must be a number.")
        if spec.kind == "integer":
            if isinstance(value, float) and not value.is_integer():
                raise ParamError(f"{label} must be a whole number.")
            value = int(value)
        else:
            value = float(value)
        if spec.minimum is not None and value < spec.minimum:
            raise ParamError(f"{label} must be at least {_num(spec.minimum)}.")
        if spec.maximum is not None and value > spec.maximum:
            raise ParamError(f"{label} must be at most {_num(spec.maximum)}.")
        return value

    if spec.kind == "string":
        if not isinstance(value, str):
            raise ParamError(f"{label} must be text.")
        text = value.strip()
        if not text:
            raise ParamError(f"{label} cannot be empty — remove it instead.")
        if len(text) > MAX_STRING:
            raise ParamError(f"{label} is longer than {MAX_STRING} characters.")
        if spec.choices and text not in spec.choices:
            raise ParamError(
                f"{label} must be one of: {', '.join(spec.choices)}."
            )
        return text

    if spec.kind == "string_list":
        if isinstance(value, str):
            # One sequence is the common case and both APIs accept a bare
            # string; storing it as a list keeps one shape downstream.
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ParamError(f"{label} must be a list of text values.")
        items = [v for v in value if v != ""]
        if not items:
            raise ParamError(f"{label} cannot be empty — remove it instead.")
        if len(items) > MAX_LIST_ITEMS:
            raise ParamError(f"{label} takes at most {MAX_LIST_ITEMS} values.")
        if any(len(v) > MAX_STRING for v in items):
            raise ParamError(f"{label} has a value longer than {MAX_STRING} characters.")
        return items

    if spec.kind == "object":
        if not isinstance(value, dict) or not all(
            isinstance(k, str) for k in value
        ):
            raise ParamError(f"{label} must be a JSON object.")
        if not value:
            raise ParamError(f"{label} cannot be empty — remove it instead.")
        for key in value:
            if key in RESERVED:
                raise ParamError(
                    f"{label} may not contain “{key}” — DataMind sets it on "
                    "every request."
                )
        if spec.object_keys:
            unknown = sorted(set(value) - set(spec.object_keys))
            if unknown:
                raise ParamError(
                    f"{label} takes only {', '.join(spec.object_keys)}; "
                    f"got {', '.join(unknown)}."
                )
        return dict(value)

    raise ParamError(f"{label} has an unsupported type.")  # pragma: no cover


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def validate_completion_params(provider: str, params: Any) -> dict[str, Any]:
    return validate(
        provider,
        params,
        specs=completion_specs(provider),
        what="Parameter",
        noun="request parameter",
    )


def validate_embedding_params(provider: str, params: Any) -> dict[str, Any]:
    return validate(
        provider,
        params,
        specs=embedding_specs(provider),
        what="Embedding parameter",
        noun="embedding parameter",
    )
