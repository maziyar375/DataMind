"""Request/response DTOs: connection sections."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# `docs/plans/retrieval-sections.md`. A section is a name, a sentence and a
# list of tables; everything else here is what the screen needs to draw it —
# its weight in the units `retrieve` decides with, whether it fits, and what
# the snapshot no longer has.
class SectionRead(BaseModel):
    id: UUID | None = None
    name: str
    description: str
    tables: list[str]
    #: PROPOSED (computed, never saved) or CURATED (a person saved it).
    origin: Literal["PROPOSED", "CURATED"]
    schema_version: int | None = None
    position: int
    #: `table_chars` over the members still in the snapshot.
    chars: int
    fit: Literal["FITS", "TOO_LARGE", "EMPTY"]
    #: Members the current snapshot no longer has — kept, shown, never routed.
    missing: list[str] = Field(default_factory=list)


class SectionCatalogEntry(BaseModel):
    table: str
    chars: int


class SectionSetRead(BaseModel):
    """What is saved, or a proposal — one shape, so the screen draws both."""

    saved: bool
    sections: list[SectionRead]
    #: Every table in no section, in snapshot order. With the sections' members
    #: this is every table in the snapshot, each exactly once.
    unassigned: list[str]
    catalog: list[SectionCatalogEntry]
    #: `_RETRIEVE_BUDGET_CHARS`: a section at or under this is sent whole.
    budget_chars: int
    snapshot_version: int
    has_snapshot: bool
    #: Unassigned tables the snapshot gained since these sections were saved —
    #: drift, marked rather than silently piled up. Empty on a proposal and
    #: whenever the sections are current.
    new_tables: list[str] = Field(default_factory=list)
    #: When the snapshot being measured against landed, for the sentence a
    #: member the schema no longer has carries.
    synced_at: datetime | None = None


class SectionWriteItem(BaseModel):
    #: The section's id when it is one the connection already has; omitted
    #: for a new one.
    id: UUID | None = None
    name: str = Field(max_length=200)
    description: str = Field(default="", max_length=4_000)
    tables: list[str] = Field(default_factory=list, max_length=5_000)


class SectionSaveRequest(BaseModel):
    """The whole set. Sections partition tables, so a move between two of them
    is one edit and cannot half-apply."""

    sections: list[SectionWriteItem] = Field(max_length=500)


class SectionProposeRequest(BaseModel):
    #: Propose a division of just these tables — *Split this section*. Omitted,
    #: the whole snapshot.
    tables: list[str] | None = Field(default=None, max_length=5_000)
