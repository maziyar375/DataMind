"""Request/response DTOs: moving a dashboard between accounts and installations."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.dashboards import DashboardRead


# The document itself is `services/dashboard_transfer.DashboardDocument`, not a
# DTO here: a file outlives the HTTP call that produced it, so its shape belongs
# with the code that reads it back, and both directions validate against one
# definition. The three models below are only the *request* around it.
class DashboardImportRequest(BaseModel):
    """A file, plus the two decisions only the importing user can make.

    `document` is a plain object rather than the parsed model on purpose: the
    format and version are checked before the shape is, so a file from a later
    release is answered with "written by a newer version" instead of a list of
    field errors about a schema it was never written against.
    """

    document: dict[str, Any]
    # What to call it here. Omitted means the name in the file; either way a
    # name already taken gets a number, because refusing a whole document over
    # its title would be a wall in front of re-importing your own export.
    name: str | None = Field(default=None, max_length=100)
    # `ref` in the document -> a connection **this caller owns**. Every id is
    # re-checked against their own rows; a ref left out is matched by name.
    connection_map: dict[str, UUID] = Field(default_factory=dict)
    # The user's answer to a refusal, never a default: tiles the guard rejects
    # are dropped and reported instead of failing the import.
    skip_invalid: bool = False


class ImportSkipRead(BaseModel):
    """One tile the import dropped, named the way the user will look for it."""

    model_config = ConfigDict(from_attributes=True)
    title: str
    code: str
    reason: str


class DashboardImportRead(BaseModel):
    dashboard: DashboardRead
    imported_tiles: int = 0
    skipped: list[ImportSkipRead] = Field(default_factory=list)
