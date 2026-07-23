from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import RequestContext, get_correlation_id
from app.core.errors import AuthenticationError, ForbiddenError
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.session import get_sessionmaker
from app.infra.identity.local import LocalIdentityProvider

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_identity_provider(db: DbDep, settings: SettingsDep) -> LocalIdentityProvider:
    return LocalIdentityProvider(db, settings)


IdentityDep = Annotated[LocalIdentityProvider, Depends(get_identity_provider)]


def get_secret_box(settings: SettingsDep) -> AesGcmSecretBox:
    return AesGcmSecretBox(
        settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
    )


SecretBoxDep = Annotated[AesGcmSecretBox, Depends(get_secret_box)]


async def get_ctx(
    request: Request,
    identity: IdentityDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestContext:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Sign in to continue.")
    who = await identity.verify_access_token(credentials.credentials)
    return RequestContext(
        user_id=who.user_id,
        email=who.email,
        role=who.role,
        correlation_id=get_correlation_id(),
    )


CtxDep = Annotated[RequestContext, Depends(get_ctx)]


async def require_admin(ctx: CtxDep) -> RequestContext:
    if not ctx.is_admin:
        raise ForbiddenError("This action requires an administrator account.")
    return ctx


AdminDep = Annotated[RequestContext, Depends(require_admin)]
