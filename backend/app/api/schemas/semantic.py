"""Request/response DTOs: semantic layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# The document itself is `app.semantic.SemanticDocument`, used directly as the
# request and response body. Re-declaring it here would give it two shapes
# that drift, and the editor in the UI needs exactly the fields the renderer
# and validator already agree on.
class SemanticTableFact(BaseModel):
    """One physical table, as the editor's table picker sees it."""

    table: str
    column_count: int
    approx_row_count: int | None = None
    described: bool = False


class SemanticJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    connection_id: UUID
    llm_config_id: UUID | None = None
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    mode: str
    only_tables: list[str] = Field(default_factory=list)
    status: str
    phase: str = ""
    progress_current: int = 0
    progress_total: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class SemanticLayerRead(BaseModel):
    """The document plus everything the UI needs to frame it.

    `document` is what the editor edits: the **draft** when one exists, and the
    published document otherwise. The counts describe that same document.
    """

    document: dict[str, Any] = Field(default_factory=dict)
    #: True when `document` has anything in it — draft or published.
    exists: bool = False
    enabled: bool = True
    entity_count: int = 0
    metric_count: int = 0
    reviewed_count: int = 0
    issue_count: int = 0
    schema_version: int = 0
    schema_dialect: str = "postgres"
    # True when the schema has been re-synced since this document was written,
    # which is the moment a definition can quietly stop being true.
    stale: bool = False
    tables: list[SemanticTableFact] = Field(default_factory=list)
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    prompt_version: str = ""
    generated_at: datetime | None = None
    edited_at: datetime | None = None
    job: SemanticJobRead | None = None
    #: The concurrency token. Send it back as `base_revision` on the next write.
    revision: int = 0
    #: Which version `document` is, and who published it when, with what note —
    #: the editor's status line. `None` when nothing has ever been written.
    published_version: int | None = None
    published_by_name: str = ""
    published_at: datetime | None = None
    published_note: str = ""
    published_origin: dict[str, Any] = Field(default_factory=dict)
    #: True when the published document has anything in it — what a question
    #: reads right now, whatever the draft holds.
    published_exists: bool = False
    #: Unpublished edits exist. `document` is then the draft, and no run reads it.
    has_draft: bool = False
    draft_updated_by_name: str = ""
    draft_updated_at: datetime | None = None
    #: The draft against the published document, in the server's one vocabulary
    #: — what the status chip counts and the publish dialog lists.
    unpublished_changes: list[SemanticChangeRead] = Field(default_factory=list)


class SemanticGenerateRequest(BaseModel):
    llm_config_id: UUID
    # MERGE keeps every entity a person edited and refreshes the rest.
    # FILL_GAPS does that and also fills an edited entity's blanks — its empty
    # fields, and columns and metrics it lacks — dropping nothing (the editor's
    # default). REPLACE is the explicit "rewrite" the UI makes the user choose.
    mode: Literal["MERGE", "FILL_GAPS", "REPLACE"] = "MERGE"
    # Empty means the whole schema. Chosen tables are changed and nothing else
    # is: the business context and glossary are only ever filled, never
    # replaced, by a run over some of the tables.
    only_tables: list[str] = Field(default_factory=list, max_length=5_000)


class SemanticSaveRequest(BaseModel):
    document: dict[str, Any]
    #: The `revision` the document was edited from. Optional in the schema only
    #: so that leaving it out is refused with its own code
    #: (`E_SEMANTIC_BASE_REVISION_REQUIRED`) rather than a generic 422 — a
    #: client that omits it has not been updated, and silently overwriting on
    #: its behalf is the lost update the revision exists to stop.
    base_revision: int | None = None
    #: Why. Optional; the change list is the what.
    note: str = Field(default="", max_length=2_000)


class SemanticRestoreRequest(BaseModel):
    base_revision: int | None = None
    note: str = Field(default="", max_length=2_000)


class SemanticDraftRequest(BaseModel):
    """The editor's document, saved to the draft. No note: a note belongs to the
    version the draft becomes, and is asked for when it is published."""

    document: dict[str, Any]
    base_revision: int | None = None


class SemanticPublishRequest(BaseModel):
    base_revision: int | None = None
    #: Required when the draft's changes alter numbers
    #: (`E_SEMANTIC_NOTE_REQUIRED`); optional otherwise.
    note: str = Field(default="", max_length=2_000)


class SemanticDiffRequest(BaseModel):
    """Two documents to compare. Nothing is saved, as with `/check`."""

    before: dict[str, Any]
    after: dict[str, Any]


class SemanticChangeRead(BaseModel):
    """One entry that changed, in `app/semantic/diff.py`'s vocabulary.

    `before` and `after` hold only the fields named in `fields` (or, for an
    added or removed entry, what names it) — enough to write a sentence, and
    no more of the document than that.
    """

    kind: str
    entity_key: str = ""
    item_key: str = ""
    affects_sql: bool = False
    fields: list[str] = Field(default_factory=list)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class SemanticVersionSummary(BaseModel):
    """One row of the History list."""

    version: int
    parent_version: int | None = None
    published_by: UUID | None = None
    #: Empty for a version with no author: the one recorded at migration, or a
    #: generation whose requester has since been deleted.
    published_by_name: str = ""
    note: str = ""
    #: `generated_job_ids`, `restored_from`, `deleted`, `migrated`.
    origin: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 0
    entity_count: int = 0
    metric_count: int = 0
    reviewed_count: int = 0
    issue_count: int = 0
    created_at: datetime
    #: Change counts by kind.
    changes: dict[str, int] = Field(default_factory=dict)
    #: True when any change alters numbers rather than wording.
    affects_sql: bool = False


class SemanticVersionList(BaseModel):
    versions: list[SemanticVersionSummary] = Field(default_factory=list)
    revision: int = 0
    published_version: int | None = None
    #: Pass as `before` for the next page; `None` on the last one.
    next_before: int | None = None


class SemanticVersionRead(SemanticVersionSummary):
    document: dict[str, Any] = Field(default_factory=dict)


class SemanticChangeList(BaseModel):
    version: int
    #: What the changes are measured against: the parent by default. `None`
    #: means the empty document (version 1, or no parent).
    against: int | None = None
    changes: list[SemanticChangeRead] = Field(default_factory=list)


class SemanticHistoryEntry(BaseModel):
    """One change to one entry, with the version it landed in."""

    version: int
    kind: str
    entity_key: str = ""
    item_key: str = ""
    affects_sql: bool = False
    published_by_name: str = ""
    note: str = ""
    origin: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class SemanticImportRequest(BaseModel):
    """A semantic layer file, into the draft. The file is checked by the
    service — format, version, size, shape, limits — not by this schema, so a
    wrong file is refused with a sentence rather than a list of field errors."""

    file: Any
    base_revision: int | None = None


class SemanticImportReport(BaseModel):
    """What the file resolved to against *this* connection's snapshot."""

    entities: int = 0
    #: Tables the file names that this schema does not have — kept, flagged.
    unresolved: int = 0
    metrics: int = 0
    invalid_metrics: int = 0
    value_meanings_included: bool = False
    value_meaning_columns: int = 0
    source_connection: str = ""
    source_engine: str = ""


class SemanticImportResult(BaseModel):
    layer: SemanticLayerRead
    report: SemanticImportReport


class SemanticMetricUseRow(BaseModel):
    metric: str
    entity: str
    #: Answers whose statement touched the metric's table and was attributed.
    questions: int = 0
    used: int = 0
    ignored: int = 0


class SemanticMetricUse(BaseModel):
    """*Metrics in use*: counts over the last `days` of answers. No questions,
    no answers, no SQL — how a definition fares, not who asked what."""

    days: int
    rows: list[SemanticMetricUseRow] = Field(default_factory=list)


class SemanticAttentionItem(BaseModel):
    """One thing in a layer that needs a person (`app/semantic/attention.py`).

    `reason` is one of `DRAFT_OLD`, `INVALID`, `COLUMNS_CHANGED`,
    `METRIC_IGNORED`, `UNREVIEWED_RELIED_ON`, `UNDESCRIBED`. `table` is empty
    for the document; `item` names the metric for `METRIC_IGNORED`. `detail`
    holds counts and schema names — never a question, an answer or a value.
    """

    reason: str
    table: str = ""
    item: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class SemanticAttention(BaseModel):
    """*Needs attention*, most urgent first, over the document the editor shows."""

    #: The window answers are counted over.
    days: int
    #: How old a draft may get before it is listed.
    draft_days: int
    items: list[SemanticAttentionItem] = Field(default_factory=list)


class SemanticExpressionCheck(BaseModel):
    """Live validation for the metric editor, so a bad expression is caught
    while it is being typed rather than when a question depends on it."""

    table: str
    expression: str
    required_joins: list[str] = Field(default_factory=list)
    is_filter: bool = False


class SemanticExpressionResult(BaseModel):
    valid: bool
    issue: str = ""
