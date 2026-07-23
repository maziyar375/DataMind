"""Email + password identity with rotating, reuse-detecting refresh tokens.

Swapping this for Keycloak means writing an `OidcIdentityProvider` and
flipping a config value. `services/` never changes, because it only ever
sees `RequestContext.user_id` and `RequestContext.role`.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.domain.ports.identity import (
    AuthenticatedIdentity,
    Credentials,
    SessionTokens,
)
from app.domain.value_objects import UserStatus
from app.infra.db.models import Session as SessionRow
from app.infra.db.models import User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LocalIdentityProvider:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost,
            parallelism=settings.argon2_parallelism,
        )

    # ── password helpers ─────────────────────────────────────────────────
    def hash_password(self, password: str) -> str:
        return self._hasher.hash(password)

    # ── authenticate ─────────────────────────────────────────────────────
    async def authenticate(self, credentials: Credentials) -> AuthenticatedIdentity:
        result = await self._db.execute(
            select(User).where(User.email == credentials.email.lower().strip())
        )
        user = result.scalar_one_or_none()

        # Always spend the hashing cost, so a missing account and a wrong
        # password are indistinguishable by timing.
        stored = user.password_hash if user and user.password_hash else _DUMMY_HASH
        try:
            self._hasher.verify(stored, credentials.password)
        except (VerifyMismatchError, InvalidHashError):
            raise AuthenticationError("Email or password is incorrect.") from None

        if user is None or user.password_hash is None:
            raise AuthenticationError("Email or password is incorrect.")
        if user.status == UserStatus.DISABLED:
            raise AuthenticationError("This account is disabled.")

        if self._hasher.check_needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(credentials.password)

        user.last_login_at = utcnow()
        await self._db.flush()

        return AuthenticatedIdentity(
            user_id=user.id, email=user.email, role=user.role,
            display_name=user.display_name,
        )

    # ── tokens ───────────────────────────────────────────────────────────
    def _mint_access_token(
        self, identity: AuthenticatedIdentity, session_id: UUID
    ) -> str:
        now = utcnow()
        payload = {
            "sub": str(identity.user_id),
            "email": identity.email,
            "role": identity.role,
            "name": identity.display_name,
            "sid": str(session_id),
            "iat": int(now.timestamp()),
            "exp": int(
                (now + timedelta(seconds=self._settings.access_token_ttl_seconds)).timestamp()
            ),
        }
        return jwt.encode(
            payload,
            self._settings.jwt_secret.get_secret_value(),
            algorithm=self._settings.jwt_algorithm,
        )

    async def verify_access_token(self, token: str) -> AuthenticatedIdentity:
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("Your session expired. Sign in again.") from None
        except jwt.PyJWTError:
            raise AuthenticationError("Invalid access token.") from None

        return AuthenticatedIdentity(
            user_id=UUID(payload["sub"]),
            email=payload.get("email", ""),
            role=payload.get("role", "MEMBER"),
            display_name=payload.get("name", ""),
        )

    async def issue_session(self, identity: AuthenticatedIdentity) -> SessionTokens:
        session_id = uuid.uuid4()
        refresh = secrets.token_urlsafe(32)
        row = SessionRow(
            id=session_id,
            user_id=identity.user_id,
            refresh_token_hash=_hash_token(refresh),
            expires_at=utcnow() + timedelta(
                seconds=self._settings.refresh_token_ttl_seconds
            ),
        )
        self._db.add(row)
        await self._db.flush()
        return SessionTokens(
            access_token=self._mint_access_token(identity, session_id),
            refresh_token=refresh,
            expires_in=self._settings.access_token_ttl_seconds,
            session_id=session_id,
        )

    async def rotate_session(
        self, refresh_token: str
    ) -> tuple[AuthenticatedIdentity, SessionTokens]:
        token_hash = _hash_token(refresh_token)
        result = await self._db.execute(
            select(SessionRow, User)
            .join(User, User.id == SessionRow.user_id)
            .where(SessionRow.refresh_token_hash == token_hash)
        )
        pair = result.first()
        if pair is None:
            raise AuthenticationError("Refresh token is not recognised.")

        session, user = pair

        if session.revoked_at is not None:
            # Reuse of an already-rotated token means the token leaked. Kill
            # every live session for this user rather than just this one.
            await self._revoke_all_for_user(user.id)
            raise AuthenticationError(
                "This session was revoked. Sign in again."
            )
        if session.expires_at <= utcnow():
            raise AuthenticationError("Your session expired. Sign in again.")
        if user.status == UserStatus.DISABLED:
            raise AuthenticationError("This account is disabled.")

        session.revoked_at = utcnow()
        identity = AuthenticatedIdentity(
            user_id=user.id, email=user.email, role=user.role,
            display_name=user.display_name,
        )
        tokens = await self.issue_session(identity)
        return identity, tokens

    async def revoke_session(self, session_id: UUID) -> None:
        row = await self._db.get(SessionRow, session_id)
        if row is not None and row.revoked_at is None:
            row.revoked_at = utcnow()
            await self._db.flush()

    async def revoke_by_refresh_token(self, refresh_token: str) -> None:
        result = await self._db.execute(
            select(SessionRow).where(
                SessionRow.refresh_token_hash == _hash_token(refresh_token)
            )
        )
        row = result.scalar_one_or_none()
        if row is not None and row.revoked_at is None:
            row.revoked_at = utcnow()
            await self._db.flush()

    async def revoke_all_sessions(self, user_id: UUID) -> None:
        """Every active session for a user, ended at once.

        A password reset must not leave old sessions valid, or the reset
        would not lock anyone out.
        """
        await self._revoke_all_for_user(user_id)

    async def _revoke_all_for_user(self, user_id: UUID) -> None:
        result = await self._db.execute(
            select(SessionRow).where(
                SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None)
            )
        )
        for row in result.scalars():
            row.revoked_at = utcnow()
        await self._db.flush()


# A well-formed Argon2id hash of a random value, used to equalise timing.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$"
    "c29tZXNhbHR2YWx1ZXg$0000000000000000000000000000000000000000000"
)
