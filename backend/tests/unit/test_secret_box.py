"""SecretBox: the AAD binding is the property worth testing."""
from __future__ import annotations

import base64
import os

import pytest

from app.infra.crypto.aesgcm_box import AesGcmSecretBox, SecretDecryptionError


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_roundtrip() -> None:
    box = AesGcmSecretBox(_key())
    envelope = box.encrypt("hunter2", aad="connection:abc")
    assert box.decrypt(envelope, aad="connection:abc") == "hunter2"


def test_ciphertext_does_not_contain_plaintext() -> None:
    box = AesGcmSecretBox(_key())
    assert "hunter2" not in box.encrypt("hunter2", aad="connection:abc")


def test_nonce_is_fresh_each_time() -> None:
    box = AesGcmSecretBox(_key())
    a = box.encrypt("same", aad="connection:abc")
    b = box.encrypt("same", aad="connection:abc")
    assert a != b


def test_blob_copied_to_another_row_fails_to_decrypt() -> None:
    """This is the whole point of binding the ciphertext to its owning row."""
    box = AesGcmSecretBox(_key())
    envelope = box.encrypt("hunter2", aad="connection:abc")
    with pytest.raises(SecretDecryptionError):
        box.decrypt(envelope, aad="connection:xyz")


def test_wrong_key_fails() -> None:
    envelope = AesGcmSecretBox(_key()).encrypt("hunter2", aad="c:1")
    with pytest.raises(SecretDecryptionError):
        AesGcmSecretBox(_key()).decrypt(envelope, aad="c:1")


def test_tampered_ciphertext_fails() -> None:
    box = AesGcmSecretBox(_key())
    envelope = box.encrypt("hunter2", aad="c:1")
    version, nonce, blob = envelope.split(".")
    tampered = f"{version}.{nonce}.{blob[:-4]}AAAA"
    with pytest.raises(SecretDecryptionError):
        box.decrypt(tampered, aad="c:1")


def test_short_key_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        AesGcmSecretBox(base64.urlsafe_b64encode(os.urandom(16)).decode())


def test_empty_key_explains_how_to_generate_one() -> None:
    with pytest.raises(ValueError, match="SECRET_BOX_KEY"):
        AesGcmSecretBox("")
