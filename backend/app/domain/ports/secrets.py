from __future__ import annotations

from typing import Protocol


class SecretBox(Protocol):
    """Authenticated encryption for credentials at rest.

    `aad` binds a ciphertext to its owning row, so a blob copied from one
    connection row to another fails to decrypt rather than silently working.
    """

    def encrypt(self, plaintext: str, *, aad: str) -> str: ...

    def decrypt(self, envelope: str, *, aad: str) -> str: ...

    @property
    def key_version(self) -> int: ...
