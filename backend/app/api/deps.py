"""The dependencies every route is assembled from, and the one guard.

**`needs(capability)` is how an app-wide permission is checked, and there is no
second way.** It runs in a dependency, *before* the handler body, and the
handler cannot obtain its `ctx` without it — which is the answer to OWASP
API1:2023, because a check that lives in a dependency cannot be forgotten by
the next route somebody adds. See §18.4 of
`docs/user-management-and-access-control-plan.md` for the three enforcement
shapes and why there is no fourth.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import RequestContext, get_correlation_id
from app.core.errors import AuthenticationError, ForbiddenError
from app.domain.ports.authz import Authorizer
from app.domain.value_objects.authz import Capability
from app.infra.authz.factory import build_authorizer
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.session import get_sessionmaker
from app.infra.identity.local import LocalIdentityProvider
from app.services.role_service import RoleService

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


def get_authorizer(db: DbDep, settings: SettingsDep) -> Authorizer:
    """The one object that answers "may they?", resolved once per request.

    Wired exactly as `get_identity_provider` is, and for the same reason: the
    port is the seam, the setting picks the implementation, and no caller
    anywhere in `api/` or `services/` learns which one it got. The choice
    itself lives in `infra/authz/factory.py`, because `app/workers/` needs the
    same answer and must reach it without importing FastAPI.
    """
    return build_authorizer(db, settings)


AuthzDep = Annotated[Authorizer, Depends(get_authorizer)]


def get_secret_box(settings: SettingsDep) -> AesGcmSecretBox:
    return AesGcmSecretBox(
        settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
    )


SecretBoxDep = Annotated[AesGcmSecretBox, Depends(get_secret_box)]


async def get_ctx(
    request: Request,
    identity: IdentityDep,
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestContext:
    """Who is calling, and what they may do — resolved per request.

    The capability set is read from the database here rather than carried in
    the token (plan decision 15). It costs one indexed join on every
    authenticated call; it buys a revoked role taking effect on the **next
    request** instead of at the next token refresh, which is the difference
    between "we removed their access" and "we removed their access, up to
    fifteen minutes from now".
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Sign in to continue.")
    who = await identity.verify_access_token(credentials.credentials)
    return RequestContext(
        user_id=who.user_id,
        email=who.email,
        role=who.role,
        capabilities=await RoleService(db).resolve_capabilities(who.user_id),
        correlation_id=get_correlation_id(),
        actor_ip=_client_ip(request),
    )


def _client_ip(request: Request) -> str:
    """The caller's address, for the audit log.

    `X-Real-IP` first, because this repo's own balancer
    (`scripts/nginx-replicas.conf`) sets it and without it every audited action
    behind a proxy is attributed to the proxy. **`X-Forwarded-For` is
    deliberately not read**: it is a client-settable header, and an audit log
    holding an address the actor chose is worse than one holding no address —
    the second is silent, the first is wrong and looks authoritative.

    That means a deployment behind a proxy that does *not* set `X-Real-IP`
    records the proxy's address. Honest, and fixable in one line of that
    proxy's config.
    """
    header = (request.headers.get("x-real-ip") or "").strip()
    if header:
        return header[:64]
    client = request.client
    return (client.host if client else "")[:64]


CtxDep = Annotated[RequestContext, Depends(get_ctx)]


def needs(capability: Capability) -> Callable[..., Awaitable[RequestContext]]:
    """A declarative guard for an app-wide verb.

    ```python
    @router.get("")
    async def list_users(ctx: Annotated[RequestContext, Depends(needs(Capability.USER_READ))]):
        ...
    ```

    **403, not 404.** A capability is not about a resource, so refusing one
    reveals nothing about what exists — the existence oracle the plan's §19.1
    worries about is a question about *rows*, and this is a question about the
    caller. Saying "you need `role.manage`" is also the difference between a
    support ticket somebody can act on and one that says "it didn't work".
    """

    async def guard(ctx: CtxDep) -> RequestContext:
        if not ctx.can(capability):
            raise ForbiddenError(
                f"This action needs the “{capability}” permission, which none "
                "of your roles carries."
            )
        return ctx

    return guard


async def require_admin(ctx: CtxDep) -> RequestContext:
    """Deprecated. `needs(Capability.USER_MANAGE)`, kept for one release.

    It is now literally that — `is_admin` reads the capability set — so the two
    spellings cannot disagree. Every route in the tree has moved to `needs`; a
    route-table walk asserts it, and this survives only so an out-of-tree
    caller does not break on the upgrade. Deleted in Phase 10.
    """
    if not ctx.can(Capability.USER_MANAGE):
        raise ForbiddenError("This action requires an administrator account.")
    return ctx


AdminDep = Annotated[RequestContext, Depends(require_admin)]

#: The four permission-shaped guards the administration screens use, named once
#: so a route reads as the sentence it enforces.
UserReadDep = Annotated[RequestContext, Depends(needs(Capability.USER_READ))]
UserManageDep = Annotated[RequestContext, Depends(needs(Capability.USER_MANAGE))]
RoleReadDep = Annotated[RequestContext, Depends(needs(Capability.ROLE_READ))]
RoleManageDep = Annotated[RequestContext, Depends(needs(Capability.ROLE_MANAGE))]
AuditReadDep = Annotated[RequestContext, Depends(needs(Capability.AUDIT_READ))]
