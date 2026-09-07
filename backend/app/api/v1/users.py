"""HTTP shape for people. The People tab of `/admin` is drawn from this.

**Reading and managing are two different permissions**, and splitting them is
the point of Phase 3 rather than a detail of it: an Auditor holds `user.read`
and can answer *who is in this installation* while being unable to change a
single thing about any of them, and a DataMind Maintainer holds neither because
administering people and administering the system are different jobs.

Every route here reaches its guard through `deps.needs(...)`, so the check runs
before the handler body. `AdminDep` is gone from this module; a route-table walk
in the tests asserts that every route that used to carry it now carries a
capability instead.
"""
from __future__ import annotations

import secrets
import uuid
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DbDep, SettingsDep, UserManageDep, UserReadDep
from app.api.schemas import (
    AdminSetPasswordRequest,
    RoleAssignmentWrite,
    RoleRead,
    ScopedPrivilegeRead,
    TeamRead,
    UserCreate,
    UserInviteResponse,
    UserRead,
    UserUpdate,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.value_objects import Role, UserStatus
from app.domain.value_objects.authz import ADMINISTRATOR, NORMAL_USER, PrincipalKind
from app.infra.db.models import User
from app.infra.identity.local import LocalIdentityProvider
from app.services.grant_service import owned_resources
from app.services.role_service import RoleService, assign_by_name
from app.services.team_service import TeamService

router = APIRouter(prefix="/users", tags=["users"])

#: What somebody is told when a People route is pointed at a machine.
#:
#: `GET /users` returns **every** principal — the team picker and the audit
#: renderer both have to resolve any `users.id`, and a list that hid machines
#: would make a service account unaddable to a team. But the *write* routes
#: here speak human: `PUT /users/{id}/password` on a service row would violate
#: `ck_users_service_no_password` and answer 500, and `PATCH` could promote a
#: machine to administrator behind the flag that exists to stop exactly that.
#: So they refuse, and say where the right screen is.
_MANAGED_ELSEWHERE = (
    "That is a service account, not a person. Machine identities are managed "
    "under Administration → Service accounts, where their keys live too."
)


async def _person(db, user_id: UUID) -> User:
    """The `users` row, if it is a human. 404 if missing, 422 if a machine.

    The split is deliberate: a missing id is a 404 because nothing exists to
    talk about, and a machine is a 422 with a sentence because something does
    and the caller is on the wrong screen.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    if user.kind == PrincipalKind.SERVICE:
        raise ValidationError(_MANAGED_ELSEWHERE)
    return user


@router.get("", response_model=list[UserRead])
async def list_users(ctx: UserReadDep, db: DbDep) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars())


@router.post("", response_model=UserInviteResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, ctx: UserManageDep, db: DbDep, settings: SettingsDep
) -> UserInviteResponse:
    """Admin creates the account with a one-time password, shown exactly once.

    The mock's "Add user" form has name and email but no password field, which
    implies an invite flow; this is the cheapest correct version of it.
    """
    email = payload.email.lower().strip()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A user with that email already exists.")

    temp_password = secrets.token_urlsafe(12)
    provider = LocalIdentityProvider(db, settings)
    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name=payload.display_name,
        password_hash=provider.hash_password(temp_password),
        role=payload.role,
        status=UserStatus.INVITED,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    # The role the legacy enum on the invitation meant. Written here rather
    # than left for the administrator to add afterwards, because an account
    # that holds no role at all can do nothing — not even open Chat — and
    # "invited, then separately given permission" is not a flow the form
    # offers.
    await assign_by_name(
        db,
        user_id=user.id,
        role_name=ADMINISTRATOR if payload.role == Role.ADMIN else NORMAL_USER,
    )
    await db.flush()
    return UserInviteResponse(
        user=UserRead.model_validate(user), temporary_password=temp_password
    )


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID, payload: UserUpdate, ctx: UserManageDep, db: DbDep
) -> User:
    user = await _person(db, user_id)

    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.email is not None:
        new_email = payload.email.lower().strip()
        if new_email != user.email:
            clash = await db.execute(select(User).where(User.email == new_email))
            if clash.scalar_one_or_none() is not None:
                raise ConflictError("A user with that email already exists.")
            user.email = new_email
    if payload.role is not None:
        if user.id == ctx.user_id and payload.role != Role.ADMIN:
            raise ValidationError("You cannot remove your own admin access.")
        # The legacy two-value control, kept working and made truthful: it now
        # moves the **assignment**, and `users.role` follows as the cache it
        # has become. Without this the toggle would write a string nothing
        # reads and the person's actual permissions would not move — which is
        # the worst possible outcome for a control labelled "Admin".
        await _set_administrator(db, ctx, user, payload.role == Role.ADMIN)
    if payload.status is not None:
        if user.id == ctx.user_id and payload.status == UserStatus.DISABLED:
            raise ValidationError("You cannot disable your own account.")
        user.status = payload.status

    await db.flush()
    return user


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_password(
    user_id: UUID, payload: AdminSetPasswordRequest,
    ctx: UserManageDep, db: DbDep, settings: SettingsDep,
) -> None:
    """An admin sets a known password for a user.

    The new password is deliberate, not a temporary one, so must_change is
    cleared and an INVITED account becomes ACTIVE. Every existing session is
    revoked: a reset that left old sessions valid would not actually lock the
    account, and if the admin is resetting their own password they expect to
    sign in again with the new one.

    A service account is refused rather than 500-ing on the `CHECK` that says a
    machine has no password — and the refusal names the screen that rotates a
    machine's credential, which is a key rather than a password.
    """
    user = await _person(db, user_id)

    provider = LocalIdentityProvider(db, settings)
    user.password_hash = provider.hash_password(payload.password.get_secret_value())
    user.must_change_password = False
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    await provider.revoke_all_sessions(user.id)
    await db.flush()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, ctx: UserManageDep, db: DbDep) -> None:
    """Delete a person. Refused while they own anything, **naming what**.

    Three refusals, and the third is new in Phase 6.

    Deleting a principal `SET NULL`s the `owner_id` of everything they own, and
    a resource with no owner is one nobody can reach: no ownership arm in
    `visible` matches it, no `manage` holder exists to share it, and the only
    way back is a database client. So the delete is refused while they own a
    grantable resource, and the refusal **lists what they own** — because
    *"transfer these four things first"* is a support ticket somebody can act
    on and *"cannot delete user"* is not.

    `POST /{resource}/{id}/transfer` is the way through it, which is also why
    that endpoint exists on every owned type rather than only on connections.

    A machine is deleted from its own screen: routing a service account through
    here would remove it with no `service_user.deleted` row naming what went.
    """
    if user_id == ctx.user_id:
        raise ValidationError("You cannot remove your own account.")
    user = await _person(db, user_id)
    await RoleService(db).guard_last_administrator(user_id)

    owned = await owned_resources(db, user_id)
    if owned:
        shown = ", ".join(owned[:5])
        more = f" and {len(owned) - 5} more" if len(owned) > 5 else ""
        raise ConflictError(
            f"“{user.display_name or user.email}” still owns {shown}{more}. "
            "Transfer them to somebody else first — deleting the account now "
            "would leave them with no owner and nobody able to share them."
        )

    await db.delete(user)


# ── a person's roles ─────────────────────────────────────────────────────
@router.get("/{user_id}/roles", response_model=list[RoleRead])
async def list_user_roles(user_id: UUID, ctx: UserReadDep, db: DbDep) -> list[RoleRead]:
    """What this person holds. `user.read`, not `role.manage`.

    Reading somebody's roles is part of reading their account — it is what the
    People detail pane shows — while *changing* them is a role operation. An
    Auditor is exactly the person this split exists for.
    """
    # Reading is not narrowed to humans: a service account's roles are the
    # honest answer to "what does this principal hold", and the People screen
    # never asks — but the audit and access-review surfaces will.
    if await db.get(User, user_id) is None:
        raise NotFoundError("User not found.")
    return [_role_read(role) for role in await RoleService(db).roles_of(user_id)]


@router.post(
    "/{user_id}/roles", response_model=list[RoleRead],
    status_code=status.HTTP_201_CREATED,
)
async def assign_user_role(
    user_id: UUID, payload: RoleAssignmentWrite, ctx: UserManageDep, db: DbDep
) -> list[RoleRead]:
    """Give this person a role. Idempotent; returns what they hold afterwards.

    Returning the whole set rather than the one assignment is what lets the
    detail pane re-render from the response instead of re-fetching, and it is
    the honest answer to "what happened" when the role was already held.
    """
    # A machine's roles go through `service_user_service`, which is where the
    # privileged-capability refusal lives. Assigning one here would be the way
    # around it.
    await _person(db, user_id)
    service = RoleService(db)
    await service.assign(ctx, user_id=user_id, role_id=payload.role_id)
    return [_role_read(role) for role in await service.roles_of(user_id)]


@router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_user_role(
    user_id: UUID, role_id: UUID, ctx: UserManageDep, db: DbDep
) -> None:
    """Take a role away — refused if it would leave no administrator."""
    await _person(db, user_id)
    await RoleService(db).unassign(ctx, user_id=user_id, role_id=role_id)


@router.get("/{user_id}/teams", response_model=list[TeamRead])
async def list_user_teams(user_id: UUID, ctx: UserReadDep, db: DbDep) -> list[TeamRead]:
    """Which teams this person is in. `user.read`, for the same reason their
    roles are: it is part of reading their account, and changing it is a team
    operation that lives on the team."""
    if await db.get(User, user_id) is None:
        raise NotFoundError("User not found.")
    service = TeamService(db)
    counts = await service.member_counts()
    return [
        TeamRead(
            id=team.id,
            name=team.name,
            description=team.description,
            members=counts.get(team.id, 0),
            provider_id=team.provider_id,
            source_id=team.source_id,
            created_at=team.created_at,
        )
        for team in await service.teams_of(user_id)
    ]


def _role_read(role) -> RoleRead:
    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        capabilities=sorted(row.capability for row in role.capabilities),
        scoped_privileges=[
            ScopedPrivilegeRead(
                resource_type=row.resource_type, privilege=row.privilege
            )
            for row in role.scoped_privileges
        ],
        created_at=role.created_at,
    )


async def _set_administrator(db, ctx, user: User, administrator: bool) -> None:
    """Move the Administrator assignment, and let `users.role` follow.

    The legacy `PATCH /users/{id}` control speaks in the old two-value
    vocabulary, and this is the one place that vocabulary is translated. The
    last-administrator guard lives in `RoleService` and is reached through
    `unassign`, so **both** routes into "this person is no longer an
    administrator" — this one and `DELETE /users/{id}/roles/{role_id}` — are
    stopped by the same count over the same table.
    """
    service = RoleService(db)
    role = await service.by_name(ADMINISTRATOR)
    if role is None:  # pragma: no cover - the seed is a migration
        user.role = Role.ADMIN if administrator else Role.MEMBER
        return
    if administrator:
        await service.assign(ctx, user_id=user.id, role_id=role.id)
    else:
        await service.unassign(ctx, user_id=user.id, role_id=role.id)
