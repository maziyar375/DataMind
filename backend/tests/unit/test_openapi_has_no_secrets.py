"""Milestone item 9, as a test rather than a promise.

If any serialization path ever grows a password or api_key field, this fails.
"""
from __future__ import annotations

import json

from app.api.schemas import ConnectionRead, LlmConfigRead

FORBIDDEN = (
    "password", "api_key", "apikey", "secret", "encrypted",
    # Phase 5. `token_hash` is the SHA-256 of a service key's secret half; a
    # response model carrying it would hand out the one value the whole
    # two-part key format exists to keep off the wire. `prefix` is deliberately
    # **not** here — it is the clear half by construction, and showing it is
    # what makes a key found in a log traceable to its owner.
    "token_hash", "token_secret",
)

# The two response fields that match a forbidden word on purpose. Named one by
# one, with the reason, so adding a third is a decision someone has to make
# here rather than a grep that quietly stops matching.
ALLOWED = {
    # A boolean ("is one set?"), never the key itself.
    ("LlmConfigRead", "has_api_key"),
    # Shown exactly once, at creation: an invite the admin cannot read is an
    # invite they cannot deliver.
    ("UserInviteResponse", "temporary_password"),
}

#: The one response in the whole API that carries a live credential, and the
#: field it carries it in. Named rather than pattern-matched, so a *second*
#: endpoint returning a key is a decision somebody makes on this line.
#:
#: `IssuedKeyResponse.token` is `dm_sk_<prefix>_<secret>`, returned from
#: `POST /service-accounts/{id}/keys` and never recoverable afterwards — the
#: row keeps a SHA-256 of the secret half and nothing else.
ISSUED_ONCE = {("IssuedKeyResponse", "token")}


def test_connection_read_model_has_no_credential_fields() -> None:
    for field in ConnectionRead.model_fields:
        assert not any(word in field.lower() for word in FORBIDDEN), (
            f"ConnectionRead exposes {field!r}"
        )


def test_llm_config_read_model_has_no_credential_fields() -> None:
    for field in LlmConfigRead.model_fields:
        if field == "has_api_key":
            continue  # a boolean, not a credential
        assert not any(word in field.lower() for word in FORBIDDEN), (
            f"LlmConfigRead exposes {field!r}"
        )


def test_read_schemas_serialise_without_credentials() -> None:
    schema = json.dumps(
        {
            "connection": ConnectionRead.model_json_schema(),
            "llm": LlmConfigRead.model_json_schema(),
        }
    ).lower()
    assert '"password"' not in schema
    assert '"api_key"' not in schema
    assert '"encrypted_password"' not in schema


def _openapi() -> dict:
    from app.main import create_app

    return create_app().openapi()


def _openapi_components() -> dict[str, dict]:
    return _openapi()["components"]["schemas"]


def _refs(node: object) -> set[str]:
    """Every component name reachable from a node, however deeply nested."""
    if isinstance(node, dict):
        found = set()
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _refs(value)
        return found
    if isinstance(node, list):
        return {name for item in node for name in _refs(item)}
    return set()


def _response_models(spec: dict) -> set[str]:
    """The components that can be *returned*, closed over their own nesting.

    Requests are excluded deliberately: a login body carries a password because
    that is what logging in is. The invariant is about what comes back.
    """
    reachable = {
        name
        for path in spec["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
        for name in _refs(operation.get("responses", {}))
    }
    components = spec["components"]["schemas"]
    frontier = set(reachable)
    while frontier:
        nested = {
            name
            for current in frontier
            for name in _refs(components.get(current, {}))
        } - reachable
        reachable |= nested
        frontier = nested
    return reachable


def test_no_read_model_in_the_generated_openapi_exposes_a_credential() -> None:
    """The two models above are checked by name; this walks every schema the
    app can actually return, so a DTO added later is covered on the day it is
    added rather than on the day someone remembers this file."""
    spec = _openapi()
    components = spec["components"]["schemas"]

    for name in sorted(_response_models(spec)):
        for field in components.get(name, {}).get("properties", {}):
            if (name, field) in ALLOWED | ISSUED_ONCE:
                continue
            assert not any(word in field.lower() for word in FORBIDDEN), (
                f"{name} exposes {field!r}"
            )


def test_it_sees_the_dashboard_read_models() -> None:
    """A walk over the schema proves nothing if the schema does not contain the
    new DTOs — an unregistered router would make the test above pass by being
    blind. These are the shapes Phase 3 added."""
    components = _openapi_components()
    returnable = _response_models(_openapi())

    assert "SqlDraftRead" in returnable
    assert "TileResultRead" in returnable
    assert "columns" in components["TileResultRead"]["properties"]


# ── Phase 5: the service key ─────────────────────────────────────────────
def test_no_response_model_carries_a_service_key_hash() -> None:
    """`token_hash` never reaches the wire, on any endpoint.

    The stored hash is not directly usable as a credential, which is exactly
    why it would be tempting to serialise "just for the admin screen" — and why
    this is asserted rather than assumed. It is a SHA-256 over 256 bits from
    the OS: publishing it publishes the only value standing between an
    offline copy of the table and every key in it.
    """
    components = _openapi_components()
    for name in sorted(_response_models(_openapi())):
        assert "token_hash" not in components.get(name, {}).get("properties", {}), (
            f"{name} exposes token_hash"
        )


def test_the_key_itself_appears_in_exactly_one_response_model() -> None:
    """Issued once, from one endpoint, and nowhere else.

    A second model returning the token would mean the key was recoverable — and
    a secret that can be re-read is a secret with no revocation story, because
    nobody can say who has seen it.
    """
    spec = _openapi()
    components = spec["components"]["schemas"]
    carriers = {
        name
        for name in _response_models(spec)
        if "token" in components.get(name, {}).get("properties", {})
    }
    assert carriers == {"IssuedKeyResponse"}

    # And it comes back from one path, which is the one that mints it.
    minting = {
        path
        for path, operations in spec["paths"].items()
        for operation in operations.values()
        if isinstance(operation, dict)
        and "IssuedKeyResponse" in _refs(operation.get("responses", {}))
    }
    assert minting == {"/api/v1/service-accounts/{service_user_id}/keys"}


def test_the_credential_read_model_shows_the_prefix_and_nothing_secret() -> None:
    """The prefix is the *point*, not an oversight.

    A key found in a log traces to its owner through this field, without the
    secret half ever having been stored — which is the entire reason the key
    format has two parts. Asserting it is present is asserting the design
    survived.
    """
    from app.api.schemas import ServiceCredentialRead

    fields = set(ServiceCredentialRead.model_fields)
    assert "prefix" in fields
    assert "token_hash" not in fields
    assert "token" not in fields
