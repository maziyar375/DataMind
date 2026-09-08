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
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.core.errors import AuthenticationError, ForbiddenError, ValidationError
from app.domain.ports.authz import Authorizer, ResourceRef
from app.domain.value_objects.authz import Capability, Privilege, ResourceType
from app.infra.authz.factory import build_authorizer
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.session import get_sessionmaker
from app.infra.identity.local import LocalIdentityProvider
from app.infra.identity.service_key import ServiceKeyProvider, looks_like_service_key
from app.services.policy import require
from app.services.role_service import RoleService
from app.services.team_service import TeamService

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


def get_service_identity_provider(
    db: DbDep, settings: SettingsDep
) -> ServiceKeyProvider:
    """The **second** authenticator, wired exactly like the first.

    Two of them is the whole of requirement 9's seam, shipped rather than
    described: a human's session token and a machine's API key are verified by
    different objects that produce the same `AuthenticatedIdentity`, and
    `get_ctx` builds one `RequestContext` from either.
    """
    return ServiceKeyProvider(db, settings)


ServiceIdentityDep = Annotated[
    ServiceKeyProvider, Depends(get_service_identity_provider)
]


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
    service_identity: ServiceIdentityDep,
    db: DbDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> RequestContext:
    """Who is calling, and what they may do — resolved per request.

    **Two authenticators, one context.** The bearer token's *shape* decides
    which: a `dm_sk_` prefix goes to the service authenticator, anything else to
    the JWT path. The dispatch is on the shape and never a fallback — a token
    that looks like a key and is not one is refused rather than quietly retried
    against the other verifier, because a credential that can be verified two
    ways has two attack surfaces.

    What comes out is **one `RequestContext` either way**, built from the same
    two queries, and nothing downstream — no service, no repository, no
    authorizer — can tell which authenticated the request. That is the seam
    requirement 9 asks for, and this function is where it is either kept or
    lost.

    The capability set and the team set are read from the database here rather
    than carried in the token (plan decision 15). Two indexed reads on every
    authenticated call; what they buy is a revoked role — or a removal from a
    team, or a revoked key — taking effect on the **next request** instead of at
    the next token refresh, which is the difference between "we removed their
    access" and "we removed their access, up to fifteen minutes from now".
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Sign in to continue.")

    token = credentials.credentials
    service = looks_like_service_key(token)
    who = (
        await service_identity.verify_key(token)
        if service
        else await identity.verify_access_token(token)
    )
    # Teams first, because capability resolution takes them: a role reaches a
    # principal directly *or* through a team, and asking for the second answer
    # without the first would silently drop half of it. Both arms run for both
    # kinds of principal, which is what makes a service user's permissions the
    # same object as a human's rather than a parallel one.
    team_ids = await TeamService(db).team_ids(who.user_id)
    capabilities = await RoleService(db).resolve_capabilities(who.user_id, team_ids)
    actor_ip = _client_ip(request)
    if service:
        return RequestContext.for_service(
            who, capabilities, team_ids, actor_ip=actor_ip
        )
    return RequestContext.for_user(who, capabilities, team_ids, actor_ip=actor_ip)


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


def on(
    type_: ResourceType, privilege: Privilege, param: str = "id"
) -> Callable[..., Awaitable[RequestContext]]:
    """A declarative guard for a **resource**. The sibling of `needs`.

    ```python
    @router.get("/{connection_id}/grants")
    async def list_grants(
        connection_id: UUID,
        ctx: Annotated[RequestContext, Depends(
            on(ResourceType.CONNECTION, Privilege.MANAGE, "connection_id")
        )],
    ): ...
    ```

    `needs` guards an app-wide verb; this guards *this* thing, and both run in
    a dependency **before** the handler body for the same reason — a check that
    lives in a dependency cannot be forgotten by whoever adds the next route.
    That is §18.4's shape (a), and this codebase's answer to OWASP API1:2023.

    It reads the id from the path by name rather than positionally, because a
    route with two ids (`/connections/{connection_id}/grants/{grant_id}`) has
    to be able to say which one the question is about — and a guard that got
    that wrong would silently authorise against the wrong row.

    **The 404/403 rule is not implemented here.** It is implemented once, in
    `services/policy.require`, which this calls; a second copy in a dependency
    is exactly how the two answers drift apart and turn a list endpoint into an
    existence oracle. The session is passed along for the same reason — that is
    where a refusal becomes an `access.denied` row, and a guard that skipped it
    would be the one route whose denials nobody could find.

    The handler still receives the `RequestContext`, so the body can ask for a
    second, different privilege where it genuinely needs one — a `PATCH` that
    also touches a `manage`-gated field, say. What it must not do is re-ask the
    same question, which would be two round trips for one answer.
    """

    async def guard(
        request: Request, ctx: CtxDep, db: DbDep, authz: AuthzDep
    ) -> RequestContext:
        raw = request.path_params.get(param)
        if raw is None:  # pragma: no cover - a wiring mistake, not a request
            raise RuntimeError(
                f"on(...) was asked for path parameter {param!r}, which this "
                f"route does not declare: {request.url.path}"
            )
        try:
            resource_id = UUID(str(raw))
        except ValueError:
            # A malformed id is a bad request, not a missing resource: nothing
            # was looked up, so answering 404 would imply something was.
            raise ValidationError("That is not a valid identifier.") from None
        await require(
            ctx, authz, ResourceRef(type=type_, id=resource_id), privilege, db=db
        )
        return ctx

    return guard


# `require_admin` and `AdminDep` used to live here. Phase 3 replaced every
# route that carried them with `needs(capability)`, and Phase 10 deleted
# them once `tests/unit/test_capability_guards.py` proved the count of
# remaining callers was zero. Nothing in this product asks what role
# somebody has; it asks what they may do.

#: The four permission-shaped guards the administration screens use, named once
#: so a route reads as the sentence it enforces.
UserReadDep = Annotated[RequestContext, Depends(needs(Capability.USER_READ))]
UserManageDep = Annotated[RequestContext, Depends(needs(Capability.USER_MANAGE))]
RoleReadDep = Annotated[RequestContext, Depends(needs(Capability.ROLE_READ))]
RoleManageDep = Annotated[RequestContext, Depends(needs(Capability.ROLE_MANAGE))]
AuditReadDep = Annotated[RequestContext, Depends(needs(Capability.AUDIT_READ))]
TeamReadDep = Annotated[RequestContext, Depends(needs(Capability.TEAM_READ))]
TeamManageDep = Annotated[RequestContext, Depends(needs(Capability.TEAM_MANAGE))]
ServiceUserManageDep = Annotated[
    RequestContext, Depends(needs(Capability.SERVICE_USER_MANAGE))
]
#: The access review. A **read** capability an Auditor holds and a Connection
#: Owner does not: sharing your own dashboard is not a reason to be able to
#: enumerate everybody else's access.
AccessReviewDep = Annotated[
    RequestContext, Depends(needs(Capability.ACCESS_REVIEW))
]
