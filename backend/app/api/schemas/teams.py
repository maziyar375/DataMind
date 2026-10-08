"""Request/response DTOs: teams."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TeamRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str
    #: How many people are in it. Present so the list can say "4 people" beside
    #: a team rather than making somebody open it to find out.
    members: int = 0
    #: The roles this team carries. Its members hold them, from their next
    #: request — which is the whole of requirement 3.
    roles: list[str] = []
    #: The external group this team mirrors, when it mirrors one. Both or
    #: neither; nothing reads them until an OIDC adapter exists.
    provider_id: str | None = None
    source_id: str | None = None
    created_at: datetime | None = None


class TeamWrite(BaseModel):
    """Name and description. **Not** the external binding.

    Rebinding a team to a different group redirects which people flow into a
    set of permissions, so it is a separate endpoint with a separate audit
    action rather than two more optional fields here — a schema that could
    express it would make it look like a rename.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class TeamCreate(TeamWrite):
    name: str = Field(min_length=1, max_length=100)


class TeamMembersWrite(BaseModel):
    """The whole intended membership, not a list of changes.

    The picker's state *is* the answer, and a client that had to send adds and
    removes would have to diff two lists correctly to avoid re-adding somebody
    another administrator had just taken out. The server computes the
    difference and audits one row per person who actually moved.
    """

    user_ids: list[UUID]


class TeamSourceWrite(BaseModel):
    """Both, or neither. A half-bound team is refused rather than stored."""

    provider_id: str | None = Field(default=None, max_length=50)
    source_id: str | None = Field(default=None, max_length=255)
