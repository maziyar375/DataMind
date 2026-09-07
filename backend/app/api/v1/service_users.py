"""HTTP shape for machine identities. No business logic — see the service.

Every route is gated `service_user.manage`, which **Administrator and DataMind
Maintainer hold and nobody else does**. That pairing is the point rather than
an accident of the seed: running the installation and administering people are
different jobs, and *"who may create an agent"* belongs to the first. A
Maintainer holds no `user.read` at all and cannot see the People list.

**Two acts, two endpoints.** `POST /service-accounts` makes an identity;
`POST /service-accounts/{id}/keys` hands out a secret. A create that returned a
key would collapse them, and the secret would then exist wherever the account
was created — for a provisioning script, in a file.

**The key appears in exactly one response body in the whole API**, from
`POST …/keys`. Nothing else can return it: the row keeps a SHA-256 of the
secret half, and `test_openapi_has_no_secrets` asserts `token_hash` never
reaches a schema.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbDep, ServiceUserManageDep, SettingsDep
from app.api.schemas import (
    IssuedKeyResponse,
    RoleAssignmentWrite,
    ServiceCredentialCreate,
    ServiceCredentialRead,
    ServiceUserCreate,
    ServiceUserRead,
    ServiceUserUpdate,
)
from app.core.clock import utcnow
from app.infra.db.models import User
from app.services.role_service import RoleService
from app.services.service_user_service import ServiceUserService
from app.services.team_service import TeamService

router = APIRouter(prefix="/service-accounts", tags=["service-accounts"])


async def _read(db, user: User, *, active_keys: int = 0) -> ServiceUserRead:
    """One machine identity, with the roles and teams that give it its reach.

    The roles are here rather than one click away because they are the only
    interesting thing about a service account: an agent's name says what
    somebody hoped it would do, and its roles say what it can actually reach.
    """
    roles = await RoleService(db).roles_of(user.id)
    teams = await TeamService(db).teams_of(user.id)
    return ServiceUserRead(
        id=user.id,
        display_name=user.display_name,
        description=user.description,
        status=user.status,
        kind=user.kind,
        email=user.email,
        roles=[role.name for role in roles],
        teams=[team.name for team in teams],
        active_keys=active_keys,
        created_at=user.created_at,
    )


@router.get("", response_model=list[ServiceUserRead])
async def list_service_users(
    ctx: ServiceUserManageDep, db: DbDep, settings: SettingsDep
) -> list[ServiceUserRead]:
    """Every machine identity, with its live key count.

    The count is *live* keys — not revoked, not expired — because the question
    a list answers is "is anything still authenticating as this?", and a
    revoked key answers no.
    """
    service = ServiceUserService(db, settings)
    out = []
    for user in await service.list():
        keys = await service.keys(user.id)
        out.append(await _read(db, user, active_keys=sum(1 for k in keys if _live(k))))
    return out


@router.post(
    "", response_model=ServiceUserRead, status_code=status.HTTP_201_CREATED
)
async def create_service_user(
    payload: ServiceUserCreate,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> ServiceUserRead:
    """Make an identity. **No key comes back** — see the module docstring."""
    user = await ServiceUserService(db, settings).create(
        ctx,
        display_name=payload.display_name,
        description=payload.description,
        role_ids=payload.role_ids,
    )
    return await _read(db, user)


@router.get("/{service_user_id}", response_model=ServiceUserRead)
async def get_service_user(
    service_user_id: UUID,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> ServiceUserRead:
    service = ServiceUserService(db, settings)
    user = await service.get(service_user_id)
    keys = await service.keys(service_user_id)
    return await _read(db, user, active_keys=sum(1 for k in keys if _live(k)))


@router.patch("/{service_user_id}", response_model=ServiceUserRead)
async def update_service_user(
    service_user_id: UUID,
    payload: ServiceUserUpdate,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> ServiceUserRead:
    """Rename, re-describe, disable or re-enable. Disabling keeps everything."""
    user = await ServiceUserService(db, settings).update(
        ctx,
        service_user_id,
        display_name=payload.display_name,
        description=payload.description,
        status=payload.status,
    )
    return await _read(db, user)


@router.delete("/{service_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_user(
    service_user_id: UUID,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    """Delete the identity; `ON DELETE CASCADE` takes its keys with it."""
    await ServiceUserService(db, settings).delete(ctx, service_user_id)


# ── roles ────────────────────────────────────────────────────────────────
@router.post(
    "/{service_user_id}/roles",
    response_model=ServiceUserRead,
    status_code=status.HTTP_201_CREATED,
)
async def assign_service_user_role(
    service_user_id: UUID,
    payload: RoleAssignmentWrite,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> ServiceUserRead:
    """Give a machine a role — refused if the role can mint an administrator."""
    service = ServiceUserService(db, settings)
    await service.assign_role(
        ctx, service_user_id=service_user_id, role_id=payload.role_id
    )
    return await _read(db, await service.get(service_user_id))


@router.delete(
    "/{service_user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def unassign_service_user_role(
    service_user_id: UUID,
    role_id: UUID,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    await ServiceUserService(db, settings).unassign_role(
        ctx, service_user_id=service_user_id, role_id=role_id
    )


# ── keys ─────────────────────────────────────────────────────────────────
@router.get("/{service_user_id}/keys", response_model=list[ServiceCredentialRead])
async def list_keys(
    service_user_id: UUID,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> list[ServiceCredentialRead]:
    """Every key ever issued to this identity, newest first.

    Revoked and expired ones are **kept and returned**, not filtered: *"this
    key existed, was last used in March, and was revoked on the 4th"* is the
    sentence an incident needs, and a list that hid them answers none of it.
    The SPA strikes them through.
    """
    keys = await ServiceUserService(db, settings).keys(service_user_id)
    return [ServiceCredentialRead.model_validate(row) for row in keys]


@router.post(
    "/{service_user_id}/keys",
    response_model=IssuedKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_key(
    service_user_id: UUID,
    payload: ServiceCredentialCreate,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> IssuedKeyResponse:
    """Mint a key. **This is the only response in the API that carries one.**

    `never_expires` is how an administrator asks for a key with no expiry, and
    it is a flag rather than `expires_at: null` because *"the caller said
    nothing"* must not be the same request as *"the caller meant none"*: the
    first gets the installation default, a year.
    """
    service = ServiceUserService(db, settings)
    issued = await service.issue_key(
        ctx,
        service_user_id=service_user_id,
        name=payload.name,
        expires_at=payload.expires_at,
        use_default_expiry=not payload.never_expires,
    )
    rows = await service.keys(service_user_id)
    row = next(r for r in rows if r.id == issued.credential_id)
    return IssuedKeyResponse(
        credential=ServiceCredentialRead.model_validate(row), token=issued.token
    )


@router.delete(
    "/{service_user_id}/keys/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_key(
    service_user_id: UUID,
    credential_id: UUID,
    ctx: ServiceUserManageDep,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    """Revoke one key. It fails the **next** request that presents it."""
    await ServiceUserService(db, settings).revoke_key(
        ctx, service_user_id=service_user_id, credential_id=credential_id
    )


def _live(row) -> bool:
    """Not revoked, not past its expiry. The count the list screen shows."""
    if row.revoked_at is not None:
        return False
    return row.expires_at is None or row.expires_at > utcnow()
