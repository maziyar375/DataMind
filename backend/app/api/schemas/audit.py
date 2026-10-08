"""Request/response DTOs: the audit log (Phase 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    """One audited action, as an administrator reads it.

    No `id` and no `actor_user_id`: this is a record *about people*, and the
    two questions it exists to answer — who has been changing templates, and
    what happened to this one — are answered by a display name and a resource
    id. A row identifier would only be useful for editing, and an audit log
    that can be edited is not one.
    """

    at: datetime
    #: A name, never an address. The same rule the review queue follows.
    actor: str = ""
    actor_ip: str = ""
    action: str
    resource_type: str = ""
    resource_id: UUID | None = None
    outcome: str
    #: Identifiers and counts. Never SQL, question text or result rows — see
    #: `services/audit.py`, which enforces that rather than trusting it.
    detail: dict[str, Any] = Field(default_factory=dict)
