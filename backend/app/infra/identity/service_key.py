"""The second authenticator: an API key, verified in one indexed read.

`LocalIdentityProvider` verifies a human's session token. This verifies a
machine's key, and both produce an `AuthenticatedIdentity` that `get_ctx` turns
into one `RequestContext` — which is the seam requirement 9 asks for, written
as shipped code rather than as a comment about a future adapter.

**The key format is `dm_sk_<prefix>_<secret>`**, and each of the three parts is
load-bearing:

* **`dm_sk`** — one literal a secret scanner can be taught. GitHub push
  protection, `gitleaks` and every commercial scanner match on a prefix; 48
  characters of base64 with no marker is a secret nobody can grep for.
* **`prefix`** — 12 characters of base32, **stored in the clear and unique**.
  A key found in a log traces to its owner without the secret half having ever
  been stored. This is the entire reason the key has two parts.
* **`secret`** — 32 bytes from `secrets.token_urlsafe`; 256 bits from the OS.

**Verification is SHA-256 and a constant-time compare, deliberately not
Argon2id.** Key-stretching makes *guessing a human password* expensive; against
a uniformly random 256-bit secret there is nothing to guess, so it would buy
zero security and cost 50–100 ms of CPU on **every call this identity makes**.
The password path keeps Argon2id for exactly the opposite reason — the same
principle applied to inputs with different entropy. `hmac.compare_digest` is
still used for the comparison: the hash is not secret, but a `==` over it leaks
a prefix length under timing, and the correct spelling costs nothing.

**Every refusal is the same refusal.** An unknown prefix, a wrong secret, a
revoked key, an expired key and a disabled principal all raise the same
`AuthenticationError` with the same sentence. Distinguishing them tells an
attacker which half of a guess was right; the audit log records which it
actually was, because the log is read by the operator and the response is read
by whoever sent the key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AuthenticationError, NotFoundError
from app.domain.ports.identity import AuthenticatedIdentity, IssuedKey
from app.domain.value_objects import UserStatus
from app.domain.value_objects.authz import PrincipalKind
from app.infra.db.models import ServiceCredential, User

#: The literal a secret scanner is taught, and the shape `get_ctx` dispatches
#: on. A bearer token starting with this goes to this authenticator; anything
#: else goes to the JWT path.
KEY_PREFIX = "dm_sk_"  # noqa: S105  (a public marker, not a secret)

#: Characters of base32 in the lookup half. Twelve is 60 bits — far past any
#: plausible key count's birthday bound, so the unique constraint on the column
#: is a correctness statement rather than a capacity plan.
PREFIX_CHARS = 12

#: Bytes in the secret half, before urlsafe-base64. 256 bits from the OS.
SECRET_BYTES = 32

#: How stale `last_used_at` is allowed to get. Without a throttle every request
#: from a busy integration is also a write, and the column's only question —
#: *"has anybody used this key? can I delete it?"* — needs minute resolution at
#: the very best.
LAST_USED_THROTTLE = timedelta(minutes=1)


def hash_secret(secret: str) -> str:
    """SHA-256 of the secret half, hex. See the module docstring for why."""
    return hashlib.sha256(secret.encode()).hexdigest()


def secrets_match(stored_hash: str, candidate_secret: str) -> bool:
    """Constant-time comparison of a presented secret against a stored hash.

    Its own function so a test can assert **the comparison itself** rather than
    attempt to measure timing — a timing test on a CI runner measures the CI
    runner. What is asserted is that this is the helper the verify path calls
    and that it is `hmac.compare_digest` underneath.
    """
    return hmac.compare_digest(stored_hash, hash_secret(candidate_secret))


def generate_key() -> tuple[str, str, str]:
    """A fresh key: `(full_token, prefix, secret)`.

    The prefix is base32 of random bytes, lowercased and trimmed — base32
    rather than base64 because it survives being read aloud, copied out of a
    log line and typed into a search box, which is exactly what somebody does
    with the clear half of a leaked key.
    """
    prefix = (
        base64.b32encode(os.urandom(10)).decode().lower().rstrip("=")[:PREFIX_CHARS]
    )
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return f"{KEY_PREFIX}{prefix}_{secret}", prefix, secret


def looks_like_service_key(token: str) -> bool:
    """Is this bearer token addressed to this authenticator?

    The whole of the dispatch in `get_ctx`. It is a *shape* test, not a
    validity test: a token that looks like a key and is not one is refused by
    this authenticator, never quietly retried against the JWT path, because a
    credential that can be verified two ways is a credential with two attack
    surfaces.
    """
    return token.startswith(KEY_PREFIX)


def _split(token: str) -> tuple[str, str] | None:
    """`(prefix, secret)` from a well-formed key, or `None`.

    Split on the **first** underscore after the marker, and that is not
    interchangeable with splitting on the last: `secrets.token_urlsafe` emits
    base64url, whose alphabet **includes `_`**, so a secret can and regularly
    does contain one. The prefix cannot — it is base32, lowercased — so the
    first underscore is unambiguously the separator, and `rsplit` would
    silently truncate roughly half of all secrets and fail every comparison
    against them.
    """
    if not looks_like_service_key(token):
        return None
    body = token[len(KEY_PREFIX):]
    prefix, _, secret = body.partition("_")
    if not prefix or not secret:
        return None
    return prefix, secret


class ServiceKeyProvider:
    """`ServiceIdentityProvider` over `service_credentials`.

    No session, no refresh, no rotation, and each absence is a consequence of
    there being nobody at the other end to sign out. Revocation is immediate
    because the row is read on **every** request — which a fifteen-minute JWT
    could not promise, and is the reason the service path does not mint one.
    """

    def __init__(self, db: AsyncSession, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings

    # ── verify ───────────────────────────────────────────────────────────
    async def verify_key(self, key: str) -> AuthenticatedIdentity:
        """The principal this key names, or one indistinguishable refusal.

        One indexed read on `prefix`, one constant-time compare, then four
        state checks. The order matters: the compare happens **before** the
        state checks so that a revoked key and a wrong secret cost the same,
        and a caller cannot use the response to learn that a prefix it guessed
        is a real, revoked credential.
        """
        parts = _split(key)
        if parts is None:
            raise _refused()
        prefix, secret = parts

        row = (
            await self._db.execute(
                select(ServiceCredential).where(ServiceCredential.prefix == prefix)
            )
        ).scalar_one_or_none()
        if row is None or not secrets_match(row.token_hash, secret):
            raise _refused()

        now = utcnow()
        if row.revoked_at is not None:
            raise _refused()
        if row.expires_at is not None and row.expires_at <= now:
            raise _refused()

        user = await self._db.get(User, row.service_user_id)
        if user is None or user.status == UserStatus.DISABLED:
            raise _refused()

        await self._touch(row, now)
        return AuthenticatedIdentity(
            user_id=user.id,
            email=user.email,
            role=user.role,
            display_name=user.display_name,
            kind=PrincipalKind.SERVICE,
        )

    async def _touch(self, row: ServiceCredential, now: datetime) -> None:
        """`last_used_at`, at most once a minute per key.

        The comparison is against the *stored* value rather than a cache, so
        the throttle holds across replicas and across process restarts — a
        per-process cache would let four replicas write four times a minute and
        would reset to "always write" on every deploy.
        """
        previous = row.last_used_at
        if previous is not None and now - previous < LAST_USED_THROTTLE:
            return
        row.last_used_at = now
        await self._db.flush()

    # ── issue and revoke ─────────────────────────────────────────────────
    async def issue_key(
        self,
        service_user_id: UUID,
        *,
        name: str,
        expires_at: datetime | None = None,
        created_by: UUID | None = None,
    ) -> IssuedKey:
        """Mint one key. **The returned token is the only copy that leaves.**

        When the caller names no expiry the default is applied here rather than
        as a column default, because *"the caller named no expiry"* and
        *"the caller asked for none"* have to stay different answers — the
        second is an explicit `expires_at=None` from an administrator who meant
        it, and it stays possible. A year is the default because an unexpiring
        machine credential is the one thing every published key-leak incident
        has in common.
        """
        token, prefix, secret = generate_key()
        row = ServiceCredential(
            id=uuid4(),
            service_user_id=service_user_id,
            name=name.strip()[:100],
            prefix=prefix,
            token_hash=hash_secret(secret),
            scopes=[],
            expires_at=expires_at,
            created_by=created_by,
        )
        self._db.add(row)
        await self._db.flush()
        return IssuedKey(
            credential_id=row.id,
            token=token,
            prefix=prefix,
            expires_at=row.expires_at,
        )

    def default_expiry(self) -> datetime | None:
        """A year out, or `None` if this installation configured no default."""
        days = self._settings.service_key_default_ttl_days if self._settings else 365
        if days <= 0:
            return None
        return utcnow() + timedelta(days=days)

    async def revoke_key(self, credential_id: UUID) -> None:
        """Stamp `revoked_at`. The key fails the **next** request.

        The row is kept rather than deleted: *"this key existed, was used until
        March, and was revoked on the 4th"* is the sentence an incident needs,
        and a deleted row answers none of it.
        """
        row = await self._db.get(ServiceCredential, credential_id)
        if row is None:
            raise NotFoundError("Key not found.")
        if row.revoked_at is None:
            row.revoked_at = utcnow()
            await self._db.flush()

    async def keys_of(self, service_user_id: UUID) -> list[ServiceCredential]:
        """Every key ever issued to this principal, newest first. Never a secret."""
        result = await self._db.execute(
            select(ServiceCredential)
            .where(ServiceCredential.service_user_id == service_user_id)
            .order_by(ServiceCredential.created_at.desc())
        )
        return list(result.scalars())


def _refused() -> AuthenticationError:
    """One sentence for all five refusals — see the module docstring."""
    return AuthenticationError("This API key is not valid.")
