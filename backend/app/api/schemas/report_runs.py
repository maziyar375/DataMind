"""Request/response DTOs: report runs."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.drafts import ChartOptionRead


class ReportRunRead(BaseModel):
    """One generation, as the history list and the progress header see it.

    `status` is **derived** from the run's parts rather than set, which is why
    `PARTIAL` is in it: some sections succeeded and some did not, and calling
    that either a success or a failure is a lie the reader has to open the
    document to catch.
    """

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    report_id: UUID
    status: str = "QUEUED"
    # Free text the header renders as «در حال تولید بخش ۳ از ۷», together with
    # the two counters below.
    phase: str = ""
    progress_current: int = 0
    progress_total: int = 0
    llm_config_id: UUID | None = None
    # Provider and model only — which model wrote this document, kept beside it.
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    prompt_version: str = ""
    language: str = "en"
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ReportBlockResultRead(BaseModel):
    """One block's numbers, as they were at the moment they were computed.

    The heading, the question and the statement are snapshots, not lookups: a
    run stays readable after its block is edited or deleted.
    """

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    block_id: UUID | None = None
    section_id: UUID | None = None
    position: int = 0
    heading_snapshot: str = ""
    # The caption this figure was published with. Empty means the document
    # captions it with the question, which is every run written before blocks
    # had titles.
    title_snapshot: str = ""
    question_snapshot: str = ""
    # Shown and auditable, exactly as a chat run's SQL is.
    sql_text: str = ""
    sql_hash: str = ""
    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    vega_spec: dict[str, Any] | None = None
    chart_source: str | None = None
    chart_note: str | None = None
    kpi: dict[str, Any] | None = None
    computed_at: datetime
    duration_ms: int = 0
    # The intersection rule, on a figure: this reader holds `select` on the
    # report and not on the connection behind it, so the block keeps its
    # heading, its caption and its position and loses its numbers, its chart
    # and its statement. Blanked in `reports.py`, never written this way.
    restricted: bool = False
    status: str = "OK"
    error_code: str | None = None
    error_message: str | None = None
    # Whether the statement behind this figure differs from the one the
    # *previous* generation ran. **null means there is nothing to compare
    # with** — a first run, or a block that did not exist last time — which is
    # a different answer from "unchanged" and has to stay distinguishable.
    sql_changed: bool | None = None


class ReportSectionResultRead(BaseModel):
    """One section's prose, for one run.

    Two prose fields, not one: `edited_prose` is NULL until the user writes
    over it, and a regeneration starts a *new* run rather than overwriting
    this one — so editing never destroys and regenerating never overwrites.
    """

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    section_id: UUID | None = None
    position: int = 0
    heading_snapshot: str = ""
    prose: str = ""
    edited_prose: str | None = None
    # Figures in the prose that no result row supports. A finding is a
    # suspicion, never a verdict — it flags, it never blocks.
    numeric_check: dict[str, Any] | None = None
    # One entry per sentence: the sentence, the figures it states, the result
    # it cites (`block_result_id`), and any figure that result does not
    # support. **The edge claim → result → SQL**, and it carries no SQL of its
    # own: the reader resolves `block_result_id` against `blocks` in the same
    # response, which is what keeps the intersection rule intact for free — a
    # reader who may not see a block gets a `restricted` block with no
    # `sql_text`, and the claim pointing at it resolves to nothing rather than
    # to a statement naming columns of a database they were not given.
    #
    # NULL means the run predates claims; `[]` means the writer cited nothing.
    claims: list[dict[str, Any]] | None = None
    status: str = "OK"
    error_message: str | None = None
    created_at: datetime


class ReportSectionResultUpdate(BaseModel):
    """Edit a paragraph of a saved run.

    Explicit `null` reverts to what the model wrote — which is the whole reason
    the edit lives in a column of its own rather than overwriting `prose`.
    """

    edited_prose: str | None = Field(default=None, max_length=20_000)


class ReportChartRequest(BaseModel):
    """Draw a saved block a different way. `auto` means "let the planner decide"."""

    chart_type: str


class ReportChartRead(BaseModel):
    """What a redraw changed, and the verdicts the picker needs to stay honest.

    Not the whole `ReportBlockResultRead`, which is the house rule everywhere
    else: the row's bulk is its `rows`, the redraw does not touch them, and
    shipping a capped result back to a client that already has it — to change a
    picture drawn from it — is the one place "return the written row" costs more
    than it settles. These are exactly the fields that were written.

    `spec` is null when the pick was refused, and `reason` then says why. The
    picker greys such a type out before it can be clicked; this is the same rule
    where it would matter if the display were stale.
    """

    spec: dict[str, Any] | None = None
    chart_source: str = "none"
    chart_note: str | None = None
    reason: str | None = None
    options: list[ChartOptionRead] = Field(default_factory=list)


class ReportRunDetailRead(ReportRunRead):
    """The poll target: the run, and everything written so far.

    Not "the finished document" — a run half-way through returns the half it
    has, which is what makes the progressive render need no protocol of its own.
    """

    blocks: list[ReportBlockResultRead] = Field(default_factory=list)
    sections: list[ReportSectionResultRead] = Field(default_factory=list)
