from __future__ import annotations

import secrets
import uuid
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AdminDep, DbDep, SettingsDep
from app.api.schemas import (
    AdminSetPasswordRequest, UserCreate, UserInviteResponse, UserRead, UserUpdate,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.value_objects import Role, UserStatus
from app.infra.db.models import User
from app.infra.identity.local import LocalIdentityProvider

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
async def list_users(ctx: AdminDep, db: DbDep) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars())


@router.post("", response_model=UserInviteResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, ctx: AdminDep, db: DbDep, settings: SettingsDep
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
    return UserInviteResponse(
        user=UserRead.model_validate(user), temporary_password=temp_password
    )


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID, payload: UserUpdate, ctx: AdminDep, db: DbDep
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")

    if payload.role is not None:
        if user.id == ctx.user_id and payload.role != Role.ADMIN:
            raise ValidationError("You cannot remove your own admin access.")
        await _guard_last_admin(db, user, payload.role)
        user.role = payload.role
    if payload.status is not None:
        if user.id == ctx.user_id and payload.status == UserStatus.DISABLED:
            raise ValidationError("You cannot disable your own account.")
        user.status = payload.status

    await db.flush()
    return user


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_password(
    user_id: UUID, payload: AdminSetPasswordRequest,
    ctx: AdminDep, db: DbDep, settings: SettingsDep,
) -> None:
    """An admin sets a known password for a user.

    The new password is deliberate, not a temporary one, so must_change is
    cleared and an INVITED account becomes ACTIVE. Every existing session is
    revoked: a reset that left old sessions valid would not actually lock the
    account, and if the admin is resetting their own password they expect to
    sign in again with the new one.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")

    provider = LocalIdentityProvider(db, settings)
    user.password_hash = provider.hash_password(payload.password.get_secret_value())
    user.must_change_password = False
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    await provider.revoke_all_sessions(user.id)
    await db.flush()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, ctx: AdminDep, db: DbDep) -> None:
    if user_id == ctx.user_id:
        raise ValidationError("You cannot remove your own account.")
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    await _guard_last_admin(db, user, Role.MEMBER)
    await db.delete(user)


async def _guard_last_admin(db, user: User, new_role: str) -> None:
    """A workspace with no administrator cannot be recovered from the UI."""
    if user.role != Role.ADMIN or new_role == Role.ADMIN:
        return
    result = await db.execute(select(User).where(User.role == Role.ADMIN))
    if len([u for u in result.scalars() if u.id != user.id]) == 0:
        raise ValidationError("This is the only administrator; promote another first.")
