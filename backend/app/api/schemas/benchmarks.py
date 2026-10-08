"""Request/response DTOs: benchmarks and the score (Phase 6)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkSetWrite(BaseModel):
    """Create a set. The members' roles move off `RETRIEVABLE` on save."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    template_ids: list[UUID] = Field(default_factory=list)
    #: What share is held out. Bounded away from 0 and 1: a set with nothing
    #: held out has no honest number in it, and one that holds out everything
    #: has no taught number to compare against.
    held_out_fraction: float = Field(default=0.4, ge=0.1, le=0.9)


class BenchmarkRunRead(BaseModel):
    """One run, with **both** numbers — never one.

    Accuracy on questions answered *from* a template and accuracy on questions
    answered *without* one are different numbers, and only the second moves for
    a reason. `held_out_accuracy` is `null` rather than `0` when nothing scored,
    because a run with no held-out question has no held-out accuracy and
    printing 0% for it would be the loudest possible wrong answer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    set_id: UUID
    status: str
    prompt_version: str = ""
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    total: int = 0
    scored: int = 0
    matched: int = 0
    held_out_total: int = 0
    held_out_matched: int = 0
    taught_total: int = 0
    taught_matched: int = 0
    #: `PUBLISHED` or `DRAFT` — which semantic layer document was scored.
    semantic_source: str = "PUBLISHED"
    #: The head revision a `DRAFT` run was pinned to.
    semantic_revision: int | None = None
    #: The published version scored, or on a draft run the one it was edited
    #: over. `0`: no layer; `null`: before versions were recorded.
    semantic_layer_version: int | None = None
    #: How often the run's answers matched a metric definition:
    #: `{in_scope, used, ignored, unknown}`, or null when nothing was attributed.
    metric_use: dict[str, int] | None = None
    error_message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    @property
    def held_out_accuracy(self) -> float | None:
        return (
            self.held_out_matched / self.held_out_total
            if self.held_out_total else None
        )

    @property
    def taught_accuracy(self) -> float | None:
        return (
            self.taught_matched / self.taught_total if self.taught_total else None
        )


class BenchmarkResultRead(BaseModel):
    """One question's verdict, labelled by the comparator and by no model."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID | None = None
    question: str = ""
    gold_sql: str = ""
    candidate_sql: str = ""
    role: str = "HELD_OUT"
    outcome: str = "ERROR"
    from_template: bool = False
    gold_row_count: int | None = None
    candidate_row_count: int | None = None
    duration_ms: int = 0
    failure_reason: str = ""


class BenchmarkSetRead(BaseModel):
    """A set, its history, and the two numbers from its latest run."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connection_id: UUID
    name: str
    description: str = ""
    template_ids: list[UUID] = Field(default_factory=list)
    held_out_fraction: float = 0.4
    created_at: datetime
    updated_at: datetime
    #: Newest first. The score strip draws a sparkline from these, so it is
    #: capped at a handful — a sparkline of sixty points is a smudge.
    runs: list[BenchmarkRunRead] = Field(default_factory=list)
    #: The newest run that scored a semantic layer **draft**, kept out of `runs`
    #: so the strip stays the published product's number. The publish dialog
    #: reads it beside `runs`.
    draft_run: BenchmarkRunRead | None = None
    #: How the split fell at creation, so the strip can say "on 25 held-out
    #: questions" before a single run exists.
    held_out_count: int = 0


class BenchmarkCandidateRead(BaseModel):
    """A template a set may be built from."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    question: str
    hit_count: int = 0
    referenced_tables: list[str] = Field(default_factory=list)


class BenchmarkOverview(BaseModel):
    """What the Knowledge tab's score strip needs, in one round trip.

    Appears only once a set exists — §4.8: never an empty chart. `sets` is
    empty on a connection that has not built one, and the strip is simply
    absent rather than showing zeros.
    """

    sets: list[BenchmarkSetRead] = Field(default_factory=list)
    can_curate: bool = True
    #: How many live templates could go into a set today, so the empty state
    #: can say something specific instead of "create a benchmark".
    candidates: int = 0
    min_set_size: int = 4


class TemplateParamWrite(BaseModel):
    """A declared slot, as the editor sends it."""

    name: str = Field(min_length=1, max_length=60)
    type: Literal["string", "number", "date", "datetime", "boolean"] = "string"
    comment: str = ""


class KnowledgeTemplateWrite(BaseModel):
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    params: list[TemplateParamWrite] = Field(default_factory=list)
    note: str = ""
    source: Literal[
        "MANUAL", "CHAT_CONFIRMED", "CHAT_CORRECTED", "TILE", "REPORT_BLOCK"
    ] = "MANUAL"
    # The curator's one checkbox in the editor: "use this to measure accuracy,
    # not to answer questions". `HELD_OUT` is assigned by the system at
    # creation in Phase 6 and is deliberately not offerable here.
    role: Literal["RETRIEVABLE", "BENCHMARK_ONLY"] = "RETRIEVABLE"


class KnowledgeTemplatePatch(BaseModel):
    """Every field optional: the editor saves what the curator touched."""

    question: str | None = None
    sql: str | None = None
    params: list[TemplateParamWrite] | None = None
    note: str | None = None
    role: Literal["RETRIEVABLE", "BENCHMARK_ONLY"] | None = None
    # ACTIVE reactivates a template the curator has fixed; ARCHIVED takes one
    # out of use. STALE and CONFLICTED are the system's verdicts and are
    # refused here.
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


class TemplateCheckRequest(BaseModel):
    """What the editor sends on every pause in typing."""

    sql: str = ""
    question: str = ""
    params: list[TemplateParamWrite] = Field(default_factory=list)
    # The names the curator has ticked. When present the server does the
    # substitution — on the tree, not by string replacement — and returns the
    # parameterized SQL it would store.
    accept: list[str] | None = None


class TemplateCheckResult(BaseModel):
    valid: bool = False
    #: The guard's own first message, verbatim. Rewriting it into something
    #: friendlier loses the reason.
    issue: str = ""
    issues: list[dict[str, Any]] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    #: Every literal the AST walk found — ticked, unticked, or refused with the
    #: reason next to it. The editor renders all three.
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    #: The SQL as it would be stored, with the accepted literals replaced.
    sql: str = ""
    params: list[dict[str, Any]] = Field(default_factory=list)
    #: `{names}` the question declares, so the editor can pair them with slots.
    question_slots: list[str] = Field(default_factory=list)


class AnswerFeedbackWrite(BaseModel):
    """What the answer footer sends. Three verdicts, not two.

    "This is wrong" and "please look at this" are different asks: one is a
    correction the flagger could make themselves, the other is a question they
    cannot answer. Collapsing them loses the second.
    """

    verdict: Literal["CORRECT", "WRONG", "NEEDS_REVIEW"]
    comment: str = ""


class AnswerFeedbackRead(BaseModel):
    """One verdict, and what became of it.

    `became_template` is the loop closing. It is what lets the product tell the
    person who flagged an answer that their flag became knowledge — and a
    feedback control with no visible payoff is worse than none.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    verdict: str
    comment: str = ""
    state: str
    resolution_note: str = ""
    became_template: UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    #: Whose queue this landed in — the connection's owner. §4.6 asks the
    #: *Ask for review* control to say "it goes to whoever owns the
    #: connection", and a name the server returns is a promise that stays true
    #: when ownership changes, where hardcoded prose in the SPA would not.
    #: Empty when the owner cannot be named, which reads as the generic
    #: sentence rather than as a blank.
    routed_to: str = ""


class ReviewRead(BaseModel):
    """One flag in the queue, with the evidence beside it.

    The question and the SQL travel with the row because the curator's actual
    job here is comparing two statements, and a queue that made them click
    through to find the first one would not get used.
    """

    id: UUID
    run_id: UUID
    verdict: str
    comment: str = ""
    state: str
    created_at: datetime
    #: The question as it was asked, and the statement that answered it.
    question: str = ""
    sql: str = ""
    #: Who raised it, for the header line. A name, never an address.
    flagged_by: str = ""


class ReviewResolve(BaseModel):
    """§1.5's rule, made into an interaction.

    The curator decides whether a correction is *question-shaped* (it becomes a
    template) or *definition-shaped* (it belongs in the semantic layer) — the
    product does not guess. `dismiss` is the third option and it takes a
    reason, because a dismissal with no note is indistinguishable from being
    ignored.
    """

    template_id: UUID | None = None
    note: str = ""
    dismiss: bool = False


class SuggestionRead(BaseModel):
    """One row in the backlog: what to teach, and why it is worth teaching."""

    kind: Literal["FLAGGED", "BACKFILL", "TRAFFIC", "FAILED", "UNKNOWN_WORDS"]
    question: str
    count: int = 1
    reason: str = ""
    #: A statement to prefill the editor with, where one exists.
    sql: str = ""
    source: str = ""
    #: Whether the literals in `sql` were a model's choice — which decides
    #: whether they may be disclosed. `docs/reference/security.md`.
    model_derived: bool = False
    origin_id: str = ""
    words: list[str] = Field(default_factory=list)


class KnowledgeCapabilities(BaseModel):
    """So the UI hides rather than disables.

    A disabled control the reader can never enable is an insult; the list stays
    fully readable either way, because seeing what the system knows is not a
    privilege.
    """

    can_curate: bool = True
