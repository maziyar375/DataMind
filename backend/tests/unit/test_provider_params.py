"""Provider-specific request parameters: the catalog, the guard, and the wire.

Four claims, in the order it would hurt if one broke:

* **Nothing in the catalog is invented.** Every parameter it names reaches the
  provider's request body — asserted against litellm's own parameter mapping,
  not against a comment — and every parameter it names is under the provider's
  own name for it. This is the test that makes "strictly the provider's API" a
  property of the code rather than a promise in a docstring.
* **Nothing outside it is storable.** A parameter the selected provider does
  not document is refused on save, with a sentence naming what it does accept,
  and so is anything the gateway sets itself — including through `extra_body`,
  which would otherwise be the way back in.
* **An unconfigured row is byte-identical to before the feature.** The empty
  map is every existing row, and its request must not move by a key.
* **The stored value is the sent value.** What survives validation is what the
  gateway puts on the wire, so a configuration cannot describe a request that
  never happens.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import litellm
import pytest
from fastapi.testclient import TestClient
from litellm.utils import get_optional_params

from app.api import deps
from app.core.context import RequestContext
from app.domain.ports.llm import ChatMessage, ResolvedLLM
from app.domain.value_objects.llm_params import (
    ANTHROPIC,
    OPENAI_COMPATIBLE,
    RESERVED,
    ParamError,
    catalog,
    completion_specs,
    embedding_specs,
    full_catalog,
    validate_completion_params,
    validate_embedding_params,
)
from app.infra.db.models import LlmConfig
from app.infra.llm.litellm_gateway import LiteLLMGateway, _adapt
from app.main import create_app

USER = uuid4()
MESSAGES = [ChatMessage(role="user", content="hi")]

#: One valid value per catalogued parameter, used to drive it through litellm's
#: mapper. Written here rather than read off `spec.example` so the example
#: strings stay free to be prose for a placeholder.
SAMPLES: dict[str, Any] = {
    "top_p": 0.9,
    "frequency_penalty": 0.2,
    "presence_penalty": 0.2,
    "seed": 7,
    "stop": ["\n\n"],
    "logit_bias": {"50256": -100},
    "user": "team-analytics",
    "service_tier": "auto",
    "store": True,
    "reasoning_effort": "low",
    "max_completion_tokens": 128,
    "prompt_cache_key": "datamind-sql",
    "safety_identifier": "u_8f21",
    "extra_body": {"top_k": 40},
    "top_k": 5,
    "stop_sequences": ["\n\nHuman:"],
    "thinking": {"type": "enabled", "budget_tokens": 1024},
    "metadata": {"user_id": "u_8f21"},
    "dimensions": 512,
}

#: Models to try a parameter against. More than one per provider because
#: support is **per model**, not per provider: `reasoning_effort` is real on a
#: reasoning model and dropped on `gpt-4o-mini`, and `top_p` is the other way
#: round on `gpt-5`. A parameter is real if any of these accepts it.
_MODELS = {
    OPENAI_COMPATIBLE: ("gpt-4o-mini", "gpt-5"),
    ANTHROPIC: ("claude-sonnet-4-5-20250929",),
}
_LITELLM_PROVIDER = {OPENAI_COMPATIBLE: "openai", ANTHROPIC: "anthropic"}


def _reaches_wire(provider: str, name: str, value: Any) -> bool:
    """Whether litellm puts this parameter in the request body for any of the
    representative models — asked of litellm, so the answer moves when the
    provider's API does rather than when somebody edits a list."""
    sent = _adapt(provider, {name: value})
    return any(
        name
        in get_optional_params(
            model=model, custom_llm_provider=_LITELLM_PROVIDER[provider], **sent
        )
        for model in _MODELS[provider]
    )


# ── nothing in the catalog is invented ───────────────────────────────────
@pytest.mark.parametrize("provider", [OPENAI_COMPATIBLE, ANTHROPIC])
def test_every_catalogued_completion_parameter_reaches_the_provider(
    provider: str,
) -> None:
    """The load-bearing test of the whole feature.

    A catalog entry is a promise that setting the parameter changes the
    request. `litellm.drop_params` is on — which is what keeps one prompt
    working across four providers — so a parameter the adapter does not carry
    is dropped *in silence*, and a configuration would then describe a
    behaviour that never happens. Nothing may be listed that cannot be shown to
    arrive.
    """
    for spec in completion_specs(provider):
        assert spec.name in SAMPLES, f"{spec.name} has no sample value in this test"
        assert _reaches_wire(provider, spec.name, SAMPLES[spec.name]), (
            f"{provider} “{spec.name}” is in the catalog but litellm does not "
            "send it for any representative model — it is either misnamed or "
            "not actually supported"
        )


def test_every_catalogued_embedding_parameter_reaches_the_provider() -> None:
    from litellm.utils import get_optional_params_embeddings

    for spec in embedding_specs(OPENAI_COMPATIBLE):
        resolved = get_optional_params_embeddings(
            model="text-embedding-3-small",
            custom_llm_provider="openai",
            **{spec.name: SAMPLES[spec.name]},
        )
        assert spec.name in resolved, f"embedding “{spec.name}” never reaches the body"


def test_anthropic_offers_no_embedding_parameters_because_it_offers_no_endpoint() -> None:
    """Not an omission. `probe_embedding` refuses Anthropic without a network
    call for the same reason: it is a permanent fact about the provider, and an
    empty catalog is that fact where a form can read it."""
    assert embedding_specs(ANTHROPIC) == ()
    assert catalog(ANTHROPIC)["embedding_supported"] is False
    assert catalog(OPENAI_COMPATIBLE)["embedding_supported"] is True


def test_the_catalog_uses_each_providers_own_names() -> None:
    """Anthropic documents `stop_sequences` and `metadata.user_id`; OpenAI
    documents `stop` and `user`. The stored configuration speaks the provider's
    reference, and the gateway adapts — which is what a gateway is for."""
    anthropic = {spec.name for spec in completion_specs(ANTHROPIC)}
    openai = {spec.name for spec in completion_specs(OPENAI_COMPATIBLE)}
    assert "stop_sequences" in anthropic and "stop" not in anthropic
    assert "stop" in openai and "stop_sequences" not in openai
    assert "top_k" in anthropic and "top_k" not in openai
    assert "metadata" in anthropic


def test_the_only_renamed_parameter_is_anthropics_metadata() -> None:
    """One rename, and it is a rename litellm forces: it reserves the
    `metadata` kwarg for its own callbacks and builds Anthropic's
    `metadata.user_id` from `user`. Everything else is passed through under the
    name it was stored with, and this test is what keeps that true."""
    sent = _adapt(ANTHROPIC, {"metadata": {"user_id": "u_8f21"}, "top_k": 5})
    assert sent == {"user": "u_8f21", "top_k": 5}

    body = get_optional_params(
        model="claude-sonnet-4-5-20250929", custom_llm_provider="anthropic", **sent
    )
    assert body["metadata"] == {"user_id": "u_8f21"}

    for provider in (OPENAI_COMPATIBLE, ANTHROPIC):
        for spec in completion_specs(provider):
            if provider == ANTHROPIC and spec.name == "metadata":
                continue
            sent = _adapt(provider, {spec.name: SAMPLES[spec.name]})
            assert list(sent) == [spec.name], f"{spec.name} was renamed"


def test_the_catalog_serves_every_creatable_provider_and_no_legacy_one() -> None:
    """`Custom` is still handled everywhere a stored row can carry it, and is
    deliberately absent from a picker — offering it would create one."""
    assert [entry["provider"] for entry in full_catalog()] == [
        OPENAI_COMPATIBLE,
        ANTHROPIC,
    ]


# ── nothing outside it is storable ───────────────────────────────────────
def test_a_parameter_the_provider_does_not_document_is_refused_by_name() -> None:
    with pytest.raises(ParamError) as err:
        validate_completion_params(OPENAI_COMPATIBLE, {"top_k": 5})
    message = str(err.value)
    assert "top_k" in message
    # The refusal has to be actionable, so it names what *is* accepted rather
    # than only what is not.
    assert "top_p" in message and "seed" in message


def test_a_parameter_from_the_other_provider_is_refused() -> None:
    """The failure this guard exists for: `top_k` is real, documented and
    meaningless to OpenAI, and litellm would drop it without a word."""
    validate_completion_params(ANTHROPIC, {"top_k": 5})
    with pytest.raises(ParamError):
        validate_completion_params(OPENAI_COMPATIBLE, {"top_k": 5})
    with pytest.raises(ParamError):
        validate_completion_params(ANTHROPIC, {"seed": 7})


@pytest.mark.parametrize("name", sorted(RESERVED))
def test_nothing_the_gateway_owns_can_be_configured(name: str) -> None:
    """A second source of truth for `model`, `messages` or `api_key` would be a
    setting that works on three call sites and is overwritten on the fourth."""
    with pytest.raises(ParamError):
        validate_completion_params(OPENAI_COMPATIBLE, {name: "anything"})


def test_extra_body_is_not_a_way_back_in() -> None:
    """`extra_body` is the OpenAI client's documented passthrough and is
    deliberately unchecked *inside* — except for this, because a reserved key
    smuggled through it reaches the same request body."""
    assert validate_completion_params(
        OPENAI_COMPATIBLE, {"extra_body": {"top_k": 40, "repetition_penalty": 1.1}}
    ) == {"extra_body": {"top_k": 40, "repetition_penalty": 1.1}}

    with pytest.raises(ParamError) as err:
        validate_completion_params(OPENAI_COMPATIBLE, {"extra_body": {"model": "x"}})
    assert "model" in str(err.value)


@pytest.mark.parametrize(
    ("params", "fragment"),
    [
        ({"top_p": 1.5}, "at most 1"),
        ({"top_p": "0.9"}, "must be a number"),
        ({"top_p": True}, "must be a number"),
        ({"seed": 1.5}, "whole number"),
        ({"store": "yes"}, "true or false"),
        ({"service_tier": "express"}, "must be one of"),
        ({"stop": []}, "cannot be empty"),
        ({"stop": ["a", "b", "c", "d", "e", "f", "g", "h", "i"]}, "at most 8"),
        ({"logit_bias": []}, "must be a JSON object"),
        ({"user": "   "}, "cannot be empty"),
    ],
)
def test_a_value_outside_what_the_provider_documents_is_refused(
    params: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ParamError) as err:
        validate_completion_params(OPENAI_COMPATIBLE, params)
    assert fragment in str(err.value)


def test_an_object_parameter_takes_only_the_documented_keys() -> None:
    """Anthropic's `thinking` is `{type, budget_tokens}` and its `metadata` is
    `{user_id}`. A typo'd key is a 400 from Anthropic, so it is a refusal
    here — where somebody can still see what they typed."""
    validate_completion_params(
        ANTHROPIC, {"thinking": {"type": "enabled", "budget_tokens": 2048}}
    )
    with pytest.raises(ParamError) as err:
        validate_completion_params(ANTHROPIC, {"thinking": {"budget": 2048}})
    assert "budget_tokens" in str(err.value)


def test_a_single_stop_sequence_may_be_written_as_one_string() -> None:
    """Both APIs accept a bare string; storing it as a list keeps one shape
    downstream instead of two."""
    assert validate_completion_params(OPENAI_COMPATIBLE, {"stop": "END"}) == {
        "stop": ["END"]
    }


def test_an_embedding_parameter_is_checked_against_the_embedding_api() -> None:
    """The two maps are validated against different halves of the provider's
    API: `seed` is a completion parameter and means nothing to /v1/embeddings."""
    assert validate_embedding_params(OPENAI_COMPATIBLE, {"dimensions": 512}) == {
        "dimensions": 512
    }
    with pytest.raises(ParamError):
        validate_embedding_params(OPENAI_COMPATIBLE, {"seed": 7})
    with pytest.raises(ParamError):
        validate_embedding_params(ANTHROPIC, {"dimensions": 512})


# ── an unconfigured row is byte-identical to before ──────────────────────
def _llm(**over: Any) -> ResolvedLLM:
    base: dict[str, Any] = {
        "config_id": "x",
        "provider": OPENAI_COMPATIBLE,
        "model": "gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "k",
    }
    return ResolvedLLM(**{**base, **over})


def test_a_configuration_with_no_parameters_sends_the_request_it_always_did() -> None:
    gateway = LiteLLMGateway(timeout_seconds=60)
    assert gateway._kwargs(_llm(), MESSAGES) == {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "max_tokens": 2048,
        "timeout": 60,
        "api_key": "k",
        "api_base": "https://openrouter.ai/api/v1",
    }


def test_a_run_snapshot_gains_a_key_only_when_something_is_configured() -> None:
    """`model_snapshot` is how a past answer stays explainable, so a parameter
    that changed the answer belongs in it — and a run made by a configuration
    that sets none must read back exactly as one recorded before the column
    existed."""
    assert "params" not in _llm().snapshot()
    assert _llm(params={"seed": 7}).snapshot()["params"] == {"seed": 7}


# ── the stored value is the sent value ───────────────────────────────────
def test_configured_parameters_are_added_without_displacing_the_request() -> None:
    gateway = LiteLLMGateway(timeout_seconds=60)
    sent = gateway._kwargs(
        _llm(params={"top_p": 0.9, "seed": 7, "stop": ["\n\n"]}), MESSAGES
    )
    assert sent["top_p"] == 0.9 and sent["seed"] == 7 and sent["stop"] == ["\n\n"]
    assert sent["model"] == "openai/gpt-4o-mini"
    assert sent["api_key"] == "k"


def test_a_row_written_by_an_older_validator_cannot_replace_the_request() -> None:
    """Belt and braces at the last point before a request is built: the catalog
    refuses these on save, and the gateway refuses them again on send."""
    gateway = LiteLLMGateway(timeout_seconds=60)
    sent = gateway._kwargs(
        _llm(params={"model": "evil", "api_key": "leaked", "messages": []}), MESSAGES
    )
    assert sent["model"] == "openai/gpt-4o-mini"
    assert sent["api_key"] == "k"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


def test_the_embedding_model_falls_through_argument_then_config_then_default() -> None:
    """The order that makes configuring an embedding model on a provider mean
    anything, while leaving a configuration that sets none where it was."""
    gateway = LiteLLMGateway(timeout_seconds=60)
    configured = _llm(embedding_model="text-embedding-3-large")

    assert gateway._embedding_kwargs(configured, "")["model"] == (
        "openai/text-embedding-3-large"
    )
    assert gateway._embedding_kwargs(configured, "nomic-embed-text")["model"] == (
        "openai/nomic-embed-text"
    )
    assert gateway._embedding_kwargs(_llm(), "")["model"] == (
        "openai/text-embedding-3-small"
    )


def test_embedding_parameters_ride_the_embedding_request_and_not_the_chat_one() -> None:
    gateway = LiteLLMGateway(timeout_seconds=60)
    llm = _llm(params={"seed": 7}, embedding_params={"dimensions": 512})
    assert gateway._embedding_kwargs(llm, "")["dimensions"] == 512
    assert "seed" not in gateway._embedding_kwargs(llm, "")
    assert "dimensions" not in gateway._kwargs(llm, MESSAGES)


def test_a_test_reports_what_the_provider_will_actually_accept() -> None:
    """`drop_params` makes an unsupported parameter silent at request time.
    Silence is right for a request and wrong for a test: a configuration that
    stores `reasoning_effort` against a model that has no such thing should say
    so on the screen where it was typed."""
    gateway = LiteLLMGateway(timeout_seconds=60)
    applied, dropped = gateway.applied_params(
        _llm(params={"seed": 7, "reasoning_effort": "low"})
    )
    assert applied == {"seed": 7}
    assert dropped == ["reasoning_effort"]

    assert gateway.applied_params(_llm()) == ({}, [])


# ── and the API stores exactly what it validated ─────────────────────────
class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> list[Any]:
        return [self._value] if self._value is not None else []


class FakeDb:
    def __init__(self, row: Any = None) -> None:
        self.row = row
        self.added: list[Any] = []

    async def execute(self, _statement: Any) -> Any:
        return _Result(self.row)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # A real flush applies the mapper's Python-side column defaults, and
        # the create route reads the row back through `LlmConfigRead`
        # immediately afterwards. A fake that skipped them would fail on
        # `status`, which is a fact about this fake and not about the route.
        for row in self.added:
            for column, default in (
                ("status", "UNTESTED"),
                ("capabilities", {}),
                ("is_default", False),
                ("temperature", 0.2),
                ("max_tokens", 2048),
                ("params", {}),
                ("embedding_model", ""),
                ("embedding_params", {}),
            ):
                if getattr(row, column, None) is None:
                    setattr(row, column, default)


class FakeSecretBox:
    """Enough of `SecretBox` for a route that stores a key. No real key is
    generated here — none of these tests puts one in."""

    key_version = 1

    def encrypt(self, value: str, *, aad: str) -> str:
        return f"enc:{aad}:{value}"

    def decrypt(self, value: str, *, aad: str) -> str:
        return value.removeprefix(f"enc:{aad}:")


def _client(db: FakeDb) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_secret_box] = lambda: FakeSecretBox()
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER, email="user@test.local", role="MEMBER", correlation_id="test"
    )
    return TestClient(app, raise_server_exceptions=False)


def _stored(provider: str = OPENAI_COMPATIBLE) -> LlmConfig:
    return LlmConfig(
        id=uuid4(),
        owner_id=USER,
        name="openrouter",
        provider=provider,
        model="deepseek/deepseek-v4-flash",
        base_url="https://openrouter.ai/api/v1",
        encrypted_api_key=None,
        temperature=0.2,
        max_tokens=2048,
        params={},
        embedding_model="",
        embedding_params={},
        capabilities={},
        status="UNTESTED",
    )


def test_the_catalog_is_served_so_the_form_can_be_generated_from_it() -> None:
    """The half of the bargain that makes a new parameter a one-line change:
    the SPA renders fields from this, so nothing in the frontend names a
    parameter."""
    response = _client(FakeDb()).get("/api/v1/llm-configs/parameters")
    assert response.status_code == 200
    body = response.json()
    names = {entry["provider"]: {p["name"] for p in entry["completion"]} for entry in body}
    assert "seed" in names[OPENAI_COMPATIBLE]
    assert "thinking" in names[ANTHROPIC]
    top_p = next(p for p in body[0]["completion"] if p["name"] == "top_p")
    assert top_p["minimum"] == 0.0 and top_p["maximum"] == 1.0 and top_p["summary"]


def test_creating_a_configuration_stores_the_parameters_it_validated() -> None:
    db = FakeDb()
    response = _client(db).post(
        "/api/v1/llm-configs",
        json={
            "name": "openrouter",
            "provider": OPENAI_COMPATIBLE,
            "model": "deepseek/deepseek-v4-flash",
            "params": {"top_p": 0.9, "seed": 7},
            "embedding_model": "text-embedding-3-small",
            "embedding_params": {"dimensions": 512},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["params"] == {"top_p": 0.9, "seed": 7}
    assert response.json()["embedding_model"] == "text-embedding-3-small"
    assert db.added[0].params == {"top_p": 0.9, "seed": 7}
    assert db.added[0].embedding_params == {"dimensions": 512}


def test_creating_with_a_parameter_the_provider_does_not_take_is_refused() -> None:
    db = FakeDb()
    response = _client(db).post(
        "/api/v1/llm-configs",
        json={
            "name": "openrouter",
            "provider": OPENAI_COMPATIBLE,
            "model": "gpt-4o-mini",
            "params": {"top_k": 40},
        },
    )
    assert response.status_code == 422
    assert "top_k" in response.json()["detail"]
    assert db.added == [], "nothing may be written when the parameters are refused"


def test_an_embedding_model_on_anthropic_is_refused_where_it_is_typed() -> None:
    """Rather than at first use, on a different screen, weeks later — which is
    where `probe_embedding`'s refusal would otherwise surface."""
    response = _client(FakeDb()).post(
        "/api/v1/llm-configs",
        json={
            "name": "claude",
            "provider": ANTHROPIC,
            "model": "claude-sonnet-4-5",
            "embedding_model": "text-embedding-3-small",
        },
    )
    assert response.status_code == 422
    assert "embedding" in response.json()["detail"].lower()


def test_switching_provider_revalidates_parameters_that_were_legal_before() -> None:
    """An OpenAI configuration carrying `seed`, patched to Anthropic, must not
    keep a parameter Anthropic has never heard of."""
    row = _stored()
    row.params = {"seed": 7}
    response = _client(FakeDb(row)).patch(
        f"/api/v1/llm-configs/{row.id}", json={"provider": ANTHROPIC}
    )
    assert response.status_code == 422
    assert "seed" in response.json()["detail"]
    assert row.provider == OPENAI_COMPATIBLE, "a refused patch changes nothing"


def test_switching_provider_and_the_parameters_together_is_allowed() -> None:
    row = _stored()
    row.params = {"seed": 7}
    response = _client(FakeDb(row)).patch(
        f"/api/v1/llm-configs/{row.id}",
        json={"provider": ANTHROPIC, "params": {"top_k": 5}},
    )
    assert response.status_code == 200, response.text
    assert row.provider == ANTHROPIC
    assert row.params == {"top_k": 5}


def test_an_empty_map_clears_the_parameters() -> None:
    """`{}` is a legitimate edit and must not read as "field absent" — which is
    what a falsy check would have made it."""
    row = _stored()
    row.params = {"seed": 7}
    response = _client(FakeDb(row)).patch(
        f"/api/v1/llm-configs/{row.id}", json={"params": {}}
    )
    assert response.status_code == 200, response.text
    assert row.params == {}


def test_a_patch_that_mentions_neither_leaves_both_alone() -> None:
    row = _stored()
    row.params = {"seed": 7}
    row.embedding_model = "text-embedding-3-small"
    response = _client(FakeDb(row)).patch(
        f"/api/v1/llm-configs/{row.id}", json={"name": "renamed"}
    )
    assert response.status_code == 200, response.text
    assert row.params == {"seed": 7}
    assert row.embedding_model == "text-embedding-3-small"
    assert row.name == "renamed"


def test_a_row_must_be_for_something() -> None:
    """`model` is optional and `embedding_model` is optional; **both** empty is
    a row that appears in no picker, answers nothing and embeds nothing. The
    only honest moment to say so is the save."""
    response = _client(FakeDb()).post(
        "/api/v1/llm-configs",
        json={"name": "empty", "provider": OPENAI_COMPATIBLE},
    )
    assert response.status_code == 422
    assert "embedding model" in response.json()["detail"]


def test_a_provider_may_be_created_with_no_chat_model_at_all() -> None:
    """A self-hosted embedding server has no chat model to name. Before this it
    had to invent one, and that invented name is what its own Test button would
    then fail against."""
    db = FakeDb()
    response = _client(db).post(
        "/api/v1/llm-configs",
        json={
            "name": "local embeddings",
            "provider": OPENAI_COMPATIBLE,
            "base_url": "http://tei:8080/v1",
            "embedding_model": "BAAI/bge-m3",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["model"] == ""
    assert db.added[0].embedding_model == "BAAI/bge-m3"


def test_clearing_the_last_role_off_a_row_is_refused() -> None:
    """A patch is checked against the row that *results*, not the payload — so
    emptying `model` on a row with no embedding model is refused rather than
    leaving a record nothing can use."""
    row = _stored()
    response = _client(FakeDb(row)).patch(
        f"/api/v1/llm-configs/{row.id}", json={"model": ""}
    )
    assert response.status_code == 422
    assert row.model, "a refused patch changes nothing"


def test_no_read_model_can_carry_a_key_through_the_parameter_map() -> None:
    """`params` is returned to the browser, so the thing that makes it safe is
    that no name under which a secret could be stored is storable at all."""
    for reserved in ("api_key", "api_base", "base_url"):
        assert reserved in RESERVED
    assert not any(
        spec.name in RESERVED
        for provider in (OPENAI_COMPATIBLE, ANTHROPIC)
        for spec in completion_specs(provider) + embedding_specs(provider)
    )


def test_a_run_records_the_parameters_that_produced_it() -> None:
    """`model_snapshot` is how a past answer stays explainable after its
    provider is deleted, and a parameter that changed the answer belongs in it.

    This is asserted against `run_service`'s own builder rather than against
    `ResolvedLLM.snapshot()`, because they are **two** snapshots and used to be
    three: `create_run` and `retry` each held a copy of the same dict literal,
    and neither learned about `params` when the column was added. A run made
    with a stop sequence recorded everything except the setting that changed
    what it said.
    """
    from app.services.run_service import _model_snapshot

    connection = SimpleNamespace(name="aurora")
    plain = _model_snapshot(_stored(), connection)
    assert "params" not in plain, (
        "a configuration that sets nothing must record exactly what it "
        "recorded before the column existed"
    )
    assert plain["llm_config_name"] and plain["connection_name"]

    configured = _stored()
    configured.params = {"seed": 7, "stop": ["\n\n"]}
    assert _model_snapshot(configured, connection)["params"] == {
        "seed": 7, "stop": ["\n\n"],
    }


def test_the_two_snapshots_agree_about_the_parameters() -> None:
    """`ResolvedLLM.snapshot()` and `run_service._model_snapshot` describe the
    same configuration for different readers, and the fields they share must
    not drift — which is exactly what happened to `params` the first time."""
    from app.services.run_service import _model_snapshot

    row = _stored()
    row.params = {"seed": 7}
    from_run = _model_snapshot(row, SimpleNamespace(name="aurora"))
    from_port = _llm(params={"seed": 7}, model=row.model, provider=row.provider).snapshot()
    for field in ("provider", "model", "temperature", "max_tokens", "params"):
        assert from_run[field] == from_port[field], field


def test_litellm_still_drops_unknown_parameters_globally() -> None:
    """The setting the whole design is built around. If this ever flips, an
    unsupported parameter becomes a provider 400 instead of a silent drop, and
    `applied_params` stops being a report and starts being a warning."""
    assert litellm.drop_params is True
