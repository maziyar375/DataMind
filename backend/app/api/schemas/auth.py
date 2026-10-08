"""Request/response DTOs: auth."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (OAuth token type, not a secret)
    expires_in: int


class MeResponse(BaseModel):
    """Who is signed in, and — as of Phase 3 — what they may do.

    **`role` is gone as of Phase 10**, with the column behind it. It carried
    the legacy `ADMIN`/`MEMBER` string and was kept through the migration so
    an older SPA build would keep working; the model that replaced it has
    shipped, and a two-value cache of a fact nobody consults is a field that
    can only be wrong.

    `capabilities` is the answer to every *"may I?"* the interface asks, and
    `roles` is the list of names to **show**, never to branch on.
    """

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str
    #: HUMAN or SERVICE. One value today; the column arrives in Phase 5, and
    #: the field is here now so the SPA's badge does not need a new release
    #: when it does.
    kind: str = "HUMAN"
    capabilities: list[str] = []
    roles: list[str] = []
    teams: list[str] = []
    #: Installation switches the interface renders from — today only
    #: `"deep"`, present when `deep_enabled` is on. A switch, not a
    #: permission: it says what exists here, never what this person may do.
    features: list[str] = []


class ProfileUpdate(BaseModel):
    """What a signed-in person may change about themselves.

    One field, and the omissions are the design. Email is the login
    identifier and stays with the administrator who issued it; role and
    status are the two things a member must never be able to grant
    themselves, and a schema that cannot express them cannot be tricked
    into applying them.
    """

    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        """`min_length` counts spaces; the sidebar does not.

        Trimming in the route instead would accept `"   "`, store `""`, and
        erase the account from the rail that shows it — a 200 that deletes
        your own name. So the trim happens here, where the empty result is
        still a rejected request.
        """
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("A display name cannot be blank.")
        return trimmed


class ChangePasswordRequest(BaseModel):
    """A member rotating their own password.

    `current_password` is what separates this from the admin path: proof of
    possession, so a borrowed session cannot lock the owner out of their own
    account. The floor on the new one is `AdminSetPasswordRequest`'s, because
    two different minimum lengths for one password field is a policy nobody
    can state.
    """

    current_password: SecretStr
    new_password: SecretStr = Field(min_length=8, max_length=200)
