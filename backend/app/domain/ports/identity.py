"""Who is calling — and **two ways of answering**, behind one shape.

> **Requirement 9: authentication and authorization separated, so an OIDC
> provider can arrive without redesigning user management.**

This module declares how a caller proves who they are. It declares nothing
about what they may then do: that is `ports/authz.py`, and the authorizer never
sees a token, a password, a cookie or a key.

From Phase 5 there are **two** authenticators, and that is the point rather
than an accident of scope. `IdentityProvider` verifies a human's session token;
`ServiceIdentityProvider` verifies a machine's API key. Both produce an
`AuthenticatedIdentity` and `api/deps.get_ctx` builds **one** `RequestContext`
out of either — so nothing downstream can tell which authenticated the request.
That equivalence is the seam an OIDC adapter will arrive through, proved by
shipped code rather than described by a comment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.value_objects.authz import PrincipalKind


@dataclass(frozen=True, slots=True)
class Credentials:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    """A principal, verified. **The output of either authenticator.**

    `kind` has a default because every existing construction site is a human
    signing in, and a default that says so is more honest than one that makes
    two hundred call sites restate the obvious. The service path passes it
    explicitly, and `get_ctx` carries it onto the context, where it is what the
    audit log records and what the `/auth/*` refusals read.
    """

    user_id: UUID
    email: str
    display_name: str = ""
    external_subject: str | None = None
    kind: PrincipalKind = PrincipalKind.HUMAN


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """A freshly minted API key, and the row that will verify it later.

    `token` is the **only** time the secret exists outside the caller's hands:
    it is returned once, shown once, and never recoverable — the row keeps a
    SHA-256 of the secret half and nothing else. `repr=False` so a stray log
    line or an exception's frame dump cannot print it.
    """

    credential_id: UUID
    token: str = field(repr=False)
    prefix: str = ""
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int = 900
    session_id: UUID | None = None


class IdentityProvider(Protocol):
    """How a **human** proves who they are: a password, then a session."""

    async def authenticate(self, credentials: Credentials) -> AuthenticatedIdentity: ...

    async def verify_access_token(self, token: str) -> AuthenticatedIdentity: ...

    async def issue_session(self, identity: AuthenticatedIdentity) -> SessionTokens: ...

    async def rotate_session(
        self, refresh_token: str
    ) -> tuple[AuthenticatedIdentity, SessionTokens]: ...

    async def revoke_session(self, session_id: UUID) -> None: ...


class ServiceIdentityProvider(Protocol):
    """How a **machine** proves who it is: one long-lived key, and no session.

    The sibling of `IdentityProvider`, and the differences are all consequences
    of one fact — there is nobody at the other end to be signed out.

    * **No session, no refresh, no rotation.** A key is the credential; there
      is no shorter-lived thing to exchange it for, and issuing one would only
      add a second secret to leak.
    * **Revocation is immediate**, because verification reads the row on every
      request. A revoked key fails the *next* call — which a JWT with a
      fifteen-minute life could not promise, and is the reason the service path
      does not mint one.
    * **Issuing returns the secret exactly once.** `IssuedKey.token` is the
      only moment it exists outside the caller; the row keeps a hash.
    """

    async def verify_key(self, key: str) -> AuthenticatedIdentity:
        """The principal this key names, or `AuthenticationError`.

        Refuses an unknown prefix, a wrong secret, a revoked key, an expired
        key and a disabled principal — and writes the same denial for all five,
        because telling a caller *which* of those it was is telling an attacker
        which half of a guess was right.
        """
        ...

    async def issue_key(
        self,
        service_user_id: UUID,
        *,
        name: str,
        expires_at: datetime | None = None,
    ) -> IssuedKey: ...

    async def revoke_key(self, credential_id: UUID) -> None: ...
