"""Request/response DTOs: knowledge templates."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# The store the learning loop fills. `TemplateParam` and `ParamProposal` are
# `app.knowledge`'s own models, used directly rather than restated: the editor
# needs exactly the fields the AST walk and the guard already agree on, and a
# second declaration is the one that drifts.
class KnowledgeTemplateRead(BaseModel):
    """One template, as the list row and the editor see it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connection_id: UUID
    question: str
    question_normalized: str
    sql: str
    params: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""
    source: str
    literal_provenance: str
    role: str
    status: str
    status_reason: str = ""
    schema_version: int = 0
    referenced_tables: list[str] = Field(default_factory=list)
    #: The other templates this one disagrees with, and the rows that prove it
    #: — `{summary, left_columns, right_columns, left_rows, right_rows}`, all
    #: cells already strings. Empty on every healthy template. §4.7's pane
    #: renders this rather than a warning, because the rows *are* the evidence
    #: and a conflict nobody can see the evidence for is one nobody acts on.
    conflicts_with: list[UUID] = Field(default_factory=list)
    conflict_evidence: dict[str, Any] = Field(default_factory=dict)
    hit_count: int = 0
    last_hit_at: datetime | None = None
    verified_at: datetime | None = None
    last_validated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeTemplateList(BaseModel):
    """The tab's payload: the rows, plus what frames them.

    `schema_version` and `can_curate` travel with the list because both change
    what the screen may offer, and a second round trip to learn whether the
    Save button should exist would show it and then take it away.
    """

    templates: list[KnowledgeTemplateRead] = Field(default_factory=list)
    schema_version: int = 0
    schema_synced: bool = False
    can_curate: bool = True
    #: Templates whose SQL no longer resolves against the current snapshot but
    #: are not yet marked `STALE` — read-time drift, reported the moment a
    #: re-sync creates it. A template the sweep has already withdrawn carries
    #: `status: "STALE"` instead, and appears in `health.stale`.
    stale_ids: list[UUID] = Field(default_factory=list)
    #: The store's health, so the tab can show the queue's counts without a
    #: second round trip. Phase 4.
    health: KnowledgeHealth = Field(default_factory=lambda: KnowledgeHealth())


class KnowledgeHealth(BaseModel):
    """Stale, conflicted and unused — the three rows of §4.7's queue.

    Ids rather than counts, because the queue links to the templates and a
    count the UI cannot turn into a list is a number nobody can act on.
    `unused` is deliberately last and deliberately actionless: a template
    written for a question asked once a year is not waste, so this is
    information rather than an accusation.
    """

    total: int = 0
    stale: list[UUID] = Field(default_factory=list)
    conflicted: list[UUID] = Field(default_factory=list)
    unused: list[UUID] = Field(default_factory=list)
    #: Whether the scheduled conflict checker may run on this connection. False
    #: means "was not allowed to look", which the UI must never print as
    #: "found nothing".
    conflict_checks_enabled: bool = True
    #: How many days with no hits earns a mention.
    unused_after_days: int = 90


class MaintenanceRead(BaseModel):
    """What one on-demand sweep did, for the button that asked for it."""

    checked: int = 0
    staled: list[UUID] = Field(default_factory=list)
    revived: list[UUID] = Field(default_factory=list)
    conflicted: list[UUID] = Field(default_factory=list)
    cleared: list[UUID] = Field(default_factory=list)
    pairs_considered: int = 0
    pairs_executed: int = 0
    #: Pairs the checker declined to run, each naming the slot that had no
    #: probe value. Surfaced rather than swallowed: it is how a curator learns
    #: that a parameter needs a value list.
    skipped: list[str] = Field(default_factory=list)
    conflicts_checked: bool = False
    #: The embedding index, when the connection has one. Zeroes otherwise.
    indexed: int = 0
    index_current: int = 0
    index_truncated: bool = False
    index_error: str = ""
