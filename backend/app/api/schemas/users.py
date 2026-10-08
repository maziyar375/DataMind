"""Request/response DTOs: users."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr


class UserCreate(BaseModel):
    """An invitation. **No role**: an account is created, then assigned.

    Phase 10 dropped the two-value `role` field with the column behind it.
    A new account starts as a Normal User — `assign_by_name` in the route —
    and anything more is `POST /users/{id}/roles`, which is audited and goes
    through the last-administrator guard. A role passed at creation time would
    be the one assignment in the product with no audit row.
    """

    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    """Name, address, status. **Roles move through their own routes.**

    The legacy two-value toggle lived here and is gone with `users.role`:
    `POST /users/{id}/roles` and `DELETE /users/{id}/roles/{role_id}` are the
    two doors, they are audited, and both reach the last-administrator guard
    through the same count over the same table.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    status: Literal["ACTIVE", "INVITED", "DISABLED"] | None = None


class AdminSetPasswordRequest(BaseModel):
    """An admin sets a known password for another user.

    A floor of 8 characters, no ceiling that would matter — the value is
    hashed, never stored — is the whole policy. The request carries the
    password only; who may send it is decided by the admin dependency.
    """

    password: SecretStr = Field(min_length=8, max_length=200)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str
    status: str
    #: The roles reaching this principal, by name — directly or through a team.
    #:
    #: **What the People screen badges and filters on** as of Phase 10, in
    #: place of the two-value `role` cache it used to read. It is strictly
    #: more true: somebody can be a Knowledge Manager *and* an Auditor, and
    #: neither of those was ever `ADMIN` or `MEMBER`.
    #:
    #: Names to **show**, never to branch on: what the interface may *do* is
    #: `capabilities`, which this list deliberately does not carry — a user
    #: list shipping everybody's capability set would be an access review
    #: nobody asked for.
    roles: list[str] = []
    #: `HUMAN` or `SERVICE`. Present from Phase 5 because `GET /users` returns
    #: **every principal**, machines included — the team picker and the audit
    #: renderer both need to resolve any `users.id` — so every list that draws
    #: a principal has to be able to badge one. Filtering machines out here
    #: instead would make a service account unaddable to a team.
    kind: str = "HUMAN"
    created_at: datetime


class UserInviteResponse(BaseModel):
    """The temp password is shown exactly once, at creation, and never again."""
    user: UserRead
    temporary_password: str
