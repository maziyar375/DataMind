"""Request/response DTOs: roles."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScopedPrivilegeRead(BaseModel):
    """A privilege a role holds over **every** resource of a type.

    No `resource_id`, here or in the table behind it. A role that could name
    one resource would make "why can Ali see this?" unanswerable in one
    sentence, which is the whole reason `grants` is a separate thing.
    """

    resource_type: str
    privilege: str


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str
    is_system: bool
    capabilities: list[str] = []
    scoped_privileges: list[ScopedPrivilegeRead] = []
    #: How many principals hold it. Present so the list screen can say "3
    #: people" beside a role rather than making somebody open it to find out —
    #: and so the delete button can be disabled with a reason.
    holders: int = 0
    created_at: datetime | None = None


class RoleWrite(BaseModel):
    """Create or replace a role's definition.

    `capabilities` and `scoped_privileges` are **whole sets**, not deltas: the
    editor is a checklist and a matrix, so what the user is looking at *is* the
    intended state, and a PATCH of additions and removals would need the client
    to diff two lists correctly to avoid re-granting something another
    administrator had just removed.

    Both default to `None` on the update path, which means *leave alone* — a
    rename must not silently clear a role's permissions.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    capabilities: list[str] | None = None
    scoped_privileges: list[ScopedPrivilegeRead] | None = None


class RoleCreate(RoleWrite):
    """Same shape, with the name required — a role without one has no handle."""

    name: str = Field(min_length=1, max_length=100)


class RoleAssignmentWrite(BaseModel):
    role_id: UUID
