"""Request/response DTOs: grants."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class GrantWrite(BaseModel):
    """Share a resource with exactly one principal, at one privilege.

    `user_id` **or** `team_id`, never both — the same `CHECK` the table
    carries, expressed here so a malformed request is a 422 with a sentence
    rather than an `IntegrityError` the client can only report as "something
    went wrong".
    """

    privilege: Literal["describe", "select", "modify", "delete", "manage"]
    user_id: UUID | None = None
    team_id: UUID | None = None


class GrantRead(BaseModel):
    """One row of *"who can reach this"* — **with the path**.

    The path is what makes this screen worth having. *"Sara — select"* is a
    fact somebody can neither act on nor verify; *"Sara — select, via the
    Finance team"* tells them the revoke they want is on the team, and that
    clicking revoke here would do nothing.

    `id` is `null` for ownership, which is a path rather than a row: ownership
    is not a grant, cannot be revoked, and is moved by transferring instead —
    so the UI renders it without a revoke control.
    """

    id: UUID | None = None
    principal_id: UUID
    principal_name: str
    #: `HUMAN`, `SERVICE` or `TEAM`. What the kind badge is drawn from.
    principal_kind: str
    privilege: str
    #: `owner` · `direct` · `team`.
    path: str


class ActionsRead(BaseModel):
    """`GET /{resource}/{id}/actions` — what the UI renders every control from.

    Two shapes of the same answer, deliberately. `privileges` is the raw set,
    for a screen that wants to *show* it; `can` is the named questions a
    component actually asks, so a button is `can.share` rather than
    `privileges.includes('manage')` — a spelling that puts a copy of the
    lattice in the SPA.

    `meanings` carries the sentence for each privilege on this resource type,
    from the backend's own `PRIVILEGE_MEANINGS` table. That is what lets the
    share dialog label a radio button with the same words a 403 would use.
    """

    privileges: list[str]
    can: dict[str, bool]
    meanings: dict[str, str]
    #: A short name for every privilege on this type — *Can view*, *Can edit*,
    #: *Full access* — for the row of somebody who holds it.
    labels: dict[str, str] = Field(default_factory=dict)
    #: The levels the share dialog offers, in order. A subset of the five:
    #: a model configuration offers *Can use* alone.
    levels: list[str] = Field(default_factory=list)
    #: Who owns this, by display name — so a person who cannot change access
    #: can be told whom to ask. Absent for a type with no owner of its own.
    owner_name: str | None = None
    #: True when the caller is that owner.
    is_owner: bool = False


class DirectoryEntry(BaseModel):
    """One person, service account or team somebody could share with.

    A name and a kind, never an address — see `api/v1/directory.py`.
    """

    id: UUID
    name: str
    kind: Literal["HUMAN", "SERVICE", "TEAM"]
    #: Teams only.
    members: int | None = None
    #: The caller, so a picker can leave them out without a second request.
    is_you: bool = False


class DirectoryRead(BaseModel):
    people: list[DirectoryEntry]
    teams: list[DirectoryEntry]


class SelfGrantWrite(BaseModel):
    """An administrator giving themselves access. One field, and a loud one.

    Deliberately **not** a flag on `GrantWrite`: an administrator granting
    themselves reach over somebody else's resource is a different act from
    sharing, with a different gate and a second audit row, and a boolean on the
    ordinary payload would make the two look like one operation with an option.
    """

    privilege: Literal["describe", "select", "modify", "delete", "manage"]


class TransferWrite(BaseModel):
    """Hand a resource to another principal. The new owner must be active."""

    to: UUID
