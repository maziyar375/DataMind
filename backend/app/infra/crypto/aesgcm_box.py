"""AES-256-GCM SecretBox.

Envelope format:  v<key_version>.<b64(nonce)>.<b64(ciphertext||tag)>

The `aad` argument binds a ciphertext to the row that owns it. Copying an
encrypted blob from connection A onto connection B produces a decryption
failure rather than a working credential.
"""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.errors import AppError

_NONCE_BYTES = 12


class SecretDecryptionError(AppError):
    code = "E_SECRET_DECRYPT"
    http_status = 500
    title = "Stored credential could not be decrypted"


class AesGcmSecretBox:
    def __init__(self, key_b64: str, key_version: int = 1) -> None:
        if not key_b64:
            raise ValueError(
                "SECRET_BOX_KEY is empty. Generate one with: python -c "
                "\"import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
            )
        key = base64.urlsafe_b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("SECRET_BOX_KEY must decode to exactly 32 bytes")
        self._aesgcm = AESGCM(key)
        self._key_version = key_version

    @property
    def key_version(self) -> int:
        return self._key_version

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        blob = self._aesgcm.encrypt(nonce, plaintext.encode(), aad.encode())
        return ".".join([
            f"v{self._key_version}",
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(blob).decode(),
        ])

    def decrypt(self, envelope: str, *, aad: str) -> str:
        try:
            version, nonce_b64, blob_b64 = envelope.split(".")
        except ValueError as err:
            raise SecretDecryptionError("Malformed secret envelope") from err

        if version != f"v{self._key_version}":
            raise SecretDecryptionError(
                f"Secret was sealed with key {version}, current key is "
                f"v{self._key_version}. Re-encrypt before rotating."
            )
        try:
            nonce = base64.urlsafe_b64decode(nonce_b64)
            blob = base64.urlsafe_b64decode(blob_b64)
            return self._aesgcm.decrypt(nonce, blob, aad.encode()).decode()
        except (InvalidTag, ValueError) as err:
            raise SecretDecryptionError(
                "Secret failed authentication. The ciphertext, the key, or the "
                "binding context does not match."
            ) from err
