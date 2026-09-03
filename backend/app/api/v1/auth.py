from __future__ import annotations

from fastapi import APIRouter, Cookie, Response, status

from app.api.deps import CtxDep, DbDep, IdentityDep, SettingsDep
from app.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    ProfileUpdate,
    TokenResponse,
)
from app.core.errors import AuthenticationError, ValidationError
from app.domain.ports.identity import AuthenticatedIdentity, Credentials
from app.domain.value_objects import UserStatus
from app.infra.db.models import User

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
    user = await db.get(User, ctx.user_id)
    if user is None:
        raise AuthenticationError("This account no longer exists.")
    return MeResponse.model_validate(user)


# ── your own account ─────────────────────────────────────────────────────
# Everything under `/users` is `AdminDep`, which left an invited member with
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
    return MeResponse.model_validate(user)


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
