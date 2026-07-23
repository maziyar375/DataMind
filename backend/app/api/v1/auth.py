from __future__ import annotations

from fastapi import APIRouter, Cookie, Response, status

from app.api.deps import CtxDep, DbDep, IdentityDep, SettingsDep
from app.api.schemas import LoginRequest, MeResponse, TokenResponse
from app.core.errors import AuthenticationError
from app.domain.ports.identity import Credentials
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
