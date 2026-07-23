"""Milestone item 9, as a test rather than a promise.

If any serialization path ever grows a password or api_key field, this fails.
"""
from __future__ import annotations

import json

from app.api.schemas import ConnectionRead, LlmConfigRead

FORBIDDEN = ("password", "api_key", "apikey", "secret", "encrypted")


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
