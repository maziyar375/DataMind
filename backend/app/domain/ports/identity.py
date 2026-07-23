from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Credentials:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    user_id: UUID
    email: str
    role: str
    display_name: str = ""
    external_subject: str | None = None


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int = 900
    session_id: UUID | None = None


class IdentityProvider(Protocol):
    async def authenticate(self, credentials: Credentials) -> AuthenticatedIdentity: ...

    async def verify_access_token(self, token: str) -> AuthenticatedIdentity: ...

    async def issue_session(self, identity: AuthenticatedIdentity) -> SessionTokens: ...

    async def rotate_session(
        self, refresh_token: str
    ) -> tuple[AuthenticatedIdentity, SessionTokens]: ...

    async def revoke_session(self, session_id: UUID) -> None: ...
