"""Request/response DTOs: reports."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.drafts import TileResultRead

# The one place that knows what an outline can be asked for. Imported rather
# than restated so the range the API rejects and the range the prompt honours
# cannot drift apart.
from app.reports.outline import DEFAULT_SECTION_TARGET, MAX_SECTION_TARGET, MIN_SECTION_TARGET


# Every read here carries ids and display *names* only. A report is a document
# about someone's data; nothing from inside a connection belongs in one.
class ReportBlockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    section_id: UUID
    position: int = 0
    question: str = ""
    # What the figure is captioned with in the document. **Empty means "use the
    # question"**, which is what the editor shows as the placeholder rather
    # than filling the field in — a stored copy of the question would then have
    # to be kept in step with it.
    title: str = ""
    sql: str = ""
    sql_hash: str = ""
    sql_origin: str = "GENERATED"
    block_type: str = "CHART"
    # null means Auto: the chart is re-planned from each result, which is what a
    # report re-run months later on differently-shaped data needs.
    chart_config: dict[str, Any] | None = None
    time_window: str = "none"
    feasibility_status: str = "UNCHECKED"
    # The guard's own message, shown verbatim — never re-worded here.
    feasibility_reason: str | None = None
    feasibility_checked_at: datetime | None = None
    max_rows: int | None = None
    created_at: datetime
    updated_at: datetime


class ReportBlockCheckRead(BaseModel):
    """What a feasibility check answers.

    The block as stored, plus the three things that are *about* this check and
    not about the block: the preview the verdict was reached from, and the
    chart types the result can actually support. None of the three is
    persisted — `chart_config` stays NULL, which means Auto, because a report
    re-run on differently-shaped data must be free to re-decide.
    """

    block: ReportBlockRead
    preview: TileResultRead | None = None
    # The heuristic's read of the preview's shape, for defaulting the picker.
    chart_suggestion: dict[str, Any] | None = None
    # Per-type verdicts, so the picker disables what cannot work rather than
    # offering it and apologising later.
    chart_options: list[dict[str, Any]] = Field(default_factory=list)


class ReportSectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    report_id: UUID
    position: int = 0
    heading: str = ""
    intent: str = ""
    kind: str = "NORMAL"
    created_at: datetime
    updated_at: datetime
    blocks: list[ReportBlockRead] = Field(default_factory=list)


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None = None
    prompt: str = ""
    connection_id: UUID | None = None
    connection_name: str | None = None
    llm_config_id: UUID | None = None
    llm_config_name: str | None = None
    # Derived from `prompt`, never chosen — read by the client for the
    # document's direction and its own furniture, not for a picker.
    language: str = "en"
    section_target: int = 5
    status: str = "ACTIVE"
    created_at: datetime
    updated_at: datetime
    sections: list[ReportSectionRead] = Field(default_factory=list)
    # Whether this reader may reach the database this report was built over.
    # A report is bound to exactly one connection, so unlike a dashboard the
    # answer is a single boolean rather than a set — but the rule is the same
    # one: a shared report renders, and the figures it may not show become
    # named placeholders. False disables Generate and Check in the editor,
    # which would otherwise offer buttons that can only refuse.
    data_access: bool = True
    # What the reader may do here, from the same table `GET …/actions` renders
    # from. Embedded so the header does not draw every control enabled for one
    # frame and then take half of them away.
    privileges: list[str] = Field(default_factory=list)
    # Whose report this is, when it is not the reader's. A display name.
    owner_name: str | None = None


class ReportSummaryRead(BaseModel):
    """One card on the index: what it is, how big, and when it last ran."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None = None
    connection_id: UUID | None = None
    connection_name: str | None = None
    llm_config_id: UUID | None = None
    llm_config_name: str | None = None
    language: str = "en"
    section_target: int = 5
    status: str = "ACTIVE"
    section_count: int = 0
    created_at: datetime
    updated_at: datetime
    # Somebody else's report, shared with this reader. Drives the "Shared with
    # me" filter and the owner's name on the card; a reader who owns it sees
    # neither, because "shared with you by you" is noise.
    shared: bool = False
    owner_name: str | None = None
    # What this reader may do with it — see `DashboardSummaryRead.privileges`.
    privileges: list[str] = Field(default_factory=list)


class ReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    # The user's request, kept verbatim: it is what the outline is proposed
    # from, what the prose is narrated towards months later, and — since the
    # language picker went away — what the document's language is read off.
    prompt: str = Field(default="", max_length=8000)
    # Required and pinned forever — a report keyed to one connection cannot
    # cross disclosure policies.
    connection_id: UUID
    llm_config_id: UUID | None = None
    # How many sections to ask the model for. Not the size of the outline:
    # the executive summary is added on top, and the user edits the structure
    # afterwards. There is deliberately no `language` here — it is derived
    # from `prompt`.
    section_target: int = Field(
        default=DEFAULT_SECTION_TARGET,
        ge=MIN_SECTION_TARGET,
        le=MAX_SECTION_TARGET,
    )


class ReportUpdate(BaseModel):
    """Everything a report may change after creation.

    `connection_id` is here **so it can be refused**, not so it can be set:
    accepting the field and 422-ing on a different value tells the client what
    the rule is, where silently ignoring it would look like a save that worked.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    prompt: str | None = Field(default=None, max_length=8000)
    llm_config_id: UUID | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None
    connection_id: UUID | None = None
    # Changeable, because it only governs the *next* proposal — the outline on
    # screen is unaffected until the user asks for a new one.
    section_target: int | None = Field(
        default=None, ge=MIN_SECTION_TARGET, le=MAX_SECTION_TARGET
    )


class ReportSectionCreate(BaseModel):
    heading: str = Field(min_length=1, max_length=300)
    # One line on what this section's paragraph should cover. Prompt input, not
    # display text.
    intent: str = Field(default="", max_length=2000)
    kind: Literal["NORMAL", "EXECUTIVE_SUMMARY"] = "NORMAL"
    # Omitted means "append". Explicit `0` means *first* — which is where the
    # executive summary goes, so the two cannot share a value.
    position: int | None = Field(default=None, ge=0)


class ReportSectionUpdate(BaseModel):
    heading: str | None = Field(default=None, min_length=1, max_length=300)
    intent: str | None = Field(default=None, max_length=2000)
    position: int | None = Field(default=None, ge=0)


class ReportBlockCreate(BaseModel):
    """One question, one query, one chart.

    No `sql` field: a block is created from its question and the statement is
    produced by the feasibility check. Writing one by hand is a separate route
    (`PUT .../blocks/{id}/sql`), which is also the only thing that can move
    `sql_origin` off `GENERATED` — nothing a client sends at creation can.
    """

    question: str = Field(min_length=1, max_length=2000)
    # Optional at every entry point: a block is created from its question, and
    # a caption nobody wrote is the question itself.
    title: str = Field(default="", max_length=300)
    block_type: Literal["CHART", "TABLE", "METRIC"] = "CHART"
    chart_config: dict[str, Any] | None = None
    time_window: Literal[
        "none", "last_7_days", "last_30_days", "last_month", "last_3_months",
        "last_12_months", "previous_quarter", "ytd", "custom",
    ] = "none"
    max_rows: int | None = Field(default=None, ge=1)
    # Omitted means "append"; `0` means first. See `ReportSectionCreate`.
    position: int | None = Field(default=None, ge=0)


class ReportBlockSqlUpdate(BaseModel):
    """A statement the user wrote or edited, on its way to the guard.

    No `sql_origin`: provenance is derived from what the block already held,
    not asserted by the client. A caller cannot label its own SQL as
    model-generated, and would gain nothing by it if it could — the column is
    provenance, never trust.
    """

    sql: str = Field(min_length=1, max_length=20_000)


class ReportBlockUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    # `""` is a deliberate value here and not a no-op: it clears a caption the
    # model wrote and puts the question back over the figure. Omitting the
    # field is what leaves the stored one alone.
    title: str | None = Field(default=None, max_length=300)
    block_type: Literal["CHART", "TABLE", "METRIC"] | None = None
    # Explicit null is how a client goes back to Auto; omitting the field leaves
    # whatever is stored alone.
    chart_config: dict[str, Any] | None = None
    time_window: Literal[
        "none", "last_7_days", "last_30_days", "last_month", "last_3_months",
        "last_12_months", "previous_quarter", "ytd", "custom",
    ] | None = None
    max_rows: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0)
