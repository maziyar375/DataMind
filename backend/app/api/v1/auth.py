from __future__ import annotations

from fastapi import APIRouter, Cookie, Response, status

from app.api.deps import CtxDep, DbDep, IdentityDep, SettingsDep
from app.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    PermissionsResponse,
    ProfileUpdate,
    TokenResponse,
)
from app.core.errors import AuthenticationError, ValidationError
from app.domain.ports.identity import AuthenticatedIdentity, Credentials
from app.domain.value_objects import UserStatus
from app.infra.db.models import User
from app.services.role_service import RoleService
from app.services.team_service import TeamService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, token: str, settings) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_ttl_seconds,
        path="/api/v1/auth",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    identity: IdentityDep,
    settings: SettingsDep,
) -> TokenResponse:
    who = await identity.authenticate(
        Credentials(email=payload.email, password=payload.password.get_secret_value())
    )
    tokens = await identity.issue_session(who)
    _set_refresh_cookie(response, tokens.refresh_token, settings)
    return TokenResponse(
        access_token=tokens.access_token, expires_in=tokens.expires_in
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    identity: IdentityDep,
    settings: SettingsDep,
    raymand_refresh: str | None = Cookie(default=None),
) -> TokenResponse:
    if not raymand_refresh:
        raise AuthenticationError("No refresh token was provided.")
    _, tokens = await identity.rotate_session(raymand_refresh)
    _set_refresh_cookie(response, tokens.refresh_token, settings)
    return TokenResponse(
        access_token=tokens.access_token, expires_in=tokens.expires_in
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    identity: IdentityDep,
    settings: SettingsDep,
    raymand_refresh: str | None = Cookie(default=None),
) -> None:
    if raymand_refresh:
        await identity.revoke_by_refresh_token(raymand_refresh)
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth")


@router.get("/me", response_model=MeResponse)
async def me(ctx: CtxDep, db: DbDep) -> MeResponse:
    """Who is signed in, and every affordance the SPA renders from.

    The capabilities come off the **context**, which resolved them from the
    database a moment ago as part of authenticating this very request — so no
    second query, and no chance of the answer here disagreeing with the answer
    a route guard would give. That equivalence is what makes "the UI shows
    exactly what the backend would allow" a property of the system rather than
    a thing somebody has to keep true by hand.

    It also means a role granted or revoked while the user is signed in shows
    up on their **next** `/auth/me`, with no new token and no sign-out.
    """
    user = await db.get(User, ctx.user_id)
    if user is None:
        raise AuthenticationError("This account no longer exists.")
    return await _me(db, user, ctx)


@router.get("/me/permissions", response_model=PermissionsResponse)
async def my_permissions(ctx: CtxDep, db: DbDep) -> PermissionsResponse:
    """The same answer, without the account.

    A separate endpoint because the two are re-read on different rhythms: the
    profile changes when somebody edits their name, and permissions change when
    an administrator moves a role — and a screen that wants the second should
    not have to re-fetch the first. Grafana has exactly this endpoint
    (`/api/access-control/user/permissions`) for exactly this reason.
    """
    roles = await RoleService(db).roles_of(ctx.user_id, ctx.team_ids)
    teams = await TeamService(db).teams_of(ctx.user_id)
    return PermissionsResponse(
        capabilities=sorted(str(c) for c in ctx.capabilities),
        roles=[role.name for role in roles],
        teams=[team.name for team in teams],
    )


async def _me(db, user: User, ctx) -> MeResponse:
    """The account, plus what the context already resolved for this request.

    The roles list takes `ctx.team_ids` because a role can reach somebody
    through a team, and a `/auth/me` that named only their direct assignments
    would show a person capabilities it could not account for — which is the
    one thing this endpoint exists to prevent.
    """
    roles = await RoleService(db).roles_of(ctx.user_id, ctx.team_ids)
    teams = await TeamService(db).teams_of(ctx.user_id)
    return MeResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        capabilities=sorted(str(c) for c in ctx.capabilities),
        roles=[role.name for role in roles],
        teams=[team.name for team in teams],
    )


# ── your own account ─────────────────────────────────────────────────────
# Everything under `/users` needs a People capability, which left an invited member with
# no way to change the one-time password an administrator generated — and can
# still read. These two routes are that way out. They are deliberately *not*
# the admin routes with a softer dependency: they act on `ctx.user_id` and
# take no user id at all, so there is no path parameter to point at somebody
# else, and no branch that decides whether pointing at somebody else is
# allowed. The admin routes are untouched, and stay the recovery path for a
# password nobody remembers.


@router.patch("/me", response_model=MeResponse)
async def update_me(payload: ProfileUpdate, ctx: CtxDep, db: DbDep) -> MeResponse:
    user = await db.get(User, ctx.user_id)
    if user is None:
        raise AuthenticationError("This account no longer exists.")
    # Already trimmed and proven non-empty by the schema.
    user.display_name = payload.display_name
    await db.flush()
    return await _me(db, user, ctx)


@router.put("/me/password", response_model=TokenResponse)
async def change_my_password(
    payload: ChangePasswordRequest,
    response: Response,
    ctx: CtxDep,
    db: DbDep,
    identity: IdentityDep,
    settings: SettingsDep,
) -> TokenResponse:
    """Rotate your own password, proving you know the current one.

    A wrong current password is a `ValidationError`, not an authentication
    failure: it is a field on a form that is wrong, and answering 401 would
    tell the client its *session* had died — which is how a typo ends in a
    sign-out screen.

    Every session is then revoked, including this one, for the same reason the
    admin path revokes them: a rotation that left old sessions alive would not
    actually take the old password out of use. A fresh session is issued
    immediately afterwards, so the person who just changed their password is
    the only one still signed in rather than the only one signed out.
    """
    user = await db.get(User, ctx.user_id)
    if user is None:
        raise AuthenticationError("This account no longer exists.")
    if not identity.verify_password(user, payload.current_password.get_secret_value()):
        raise ValidationError("Your current password is not correct.")

    user.password_hash = identity.hash_password(payload.new_password.get_secret_value())
    user.must_change_password = False
    # An invited account that sets its own password has done the thing the
    # invitation asked for, so it stops being an invitation.
    if user.status == UserStatus.INVITED:
        user.status = UserStatus.ACTIVE
    await identity.revoke_all_sessions(user.id)

    tokens = await identity.issue_session(
        AuthenticatedIdentity(
            user_id=user.id,
            email=user.email,
            role=user.role,
            display_name=user.display_name,
        )
    )
    _set_refresh_cookie(response, tokens.refresh_token, settings)
    return TokenResponse(
        access_token=tokens.access_token, expires_in=tokens.expires_in
    )
