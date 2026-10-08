"""Request/response DTOs: conversations & messages."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas.benchmarks import AnswerFeedbackRead


class ConversationCreate(BaseModel):
    title: str | None = None
    connection_id: UUID | None = None
    llm_config_id: UUID | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None
    default_connection_id: UUID | None = None
    default_llm_config_id: UUID | None = None


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    status: str
    default_connection_id: UUID | None
    default_llm_config_id: UUID | None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    preview: str | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    connection_id: UUID | None = None
    llm_config_id: UUID | None = None
    # "Answer this without consulting the knowledge store." What *Generate a
    # fresh answer instead* sends after recording the override — the one
    # control that makes a Verified badge safe to show.
    skip_templates: bool = False
    #: *Ask within…* — the name of the section to answer from, or `NONE` for
    #: the whole database. Either way the routing call is skipped: this is a
    #: person answering the question that call asks. Omitted is nobody having
    #: chosen, which routes as before. A name no section has falls open to
    #: the whole database, so a stale picker can never narrow wrongly.
    scope: str | None = Field(default=None, max_length=60)
    #: *Quick* or *Deep — a few minutes*: which graph answers. The reader's
    #: choice, per question, beside `scope` and `skip_templates`; nothing
    #: escalates a quick question on its own (plan D1). DEEP is refused while
    #: `deep_enabled` is off.
    depth: Literal["QUICK", "DEEP"] = "QUICK"


class RunPlanRead(BaseModel):
    """A deep run's plan and what each step found — `GET /runs/{id}/plan`.

    The same shape whether it was read from the `ANALYSIS` artifact of an
    ended run or folded from the events of one still running
    (`services/deep_plan.py`). A QUICK run answers with `plan: null`.
    """

    depth: str
    status: str
    finished: bool
    #: This reader may see the transcript and not the data: the plan's
    #: questions are here, and nothing any step found.
    restricted: bool = False
    plan: dict[str, Any] | None = None
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    #: Why the run stopped short of its plan; "" when it did not.
    stop_reason: str = ""
    claims: list[dict[str, Any]] = Field(default_factory=list)
    traceable: float | None = None
    budget: dict[str, Any] | None = None


class RunStepRead(BaseModel):
    """One node's row in the trail, and what that node cost.

    The three token fields are **`None` by default and never `0`**, which is
    the whole of the rule the columns were added under: `validate` and
    `execute` call no model, and a row of zeroes beside `generate` would read
    as a measurement of nothing rather than as the absence of one. The chip
    renders them only where `llm_calls` is set and above zero, so a node that
    called nothing is unchanged.

    `llm_calls` is not derivable from the step existing: `generate` repairs,
    and a repaired call is two calls that were both paid for.

    The two cache counts carry the same rule one step further: `None` there is
    *this provider reports no caching*, `0` is *it reports caching and served
    none*, and both are **a subset of `prompt_tokens`** rather than an
    addition to it, so a reader adding them to the input figure double-counts.
    """

    model_config = ConfigDict(from_attributes=True)
    seq: int
    name: str
    status: str
    detail: str | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    llm_calls: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    spec: dict[str, Any]
    # As on a run: a reader shared the thread and not its database gets the
    # artifact's identity and none of its content.
    restricted: bool = False
    restricted_reason: str | None = None


class GeneratedQueryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attempt_no: int
    raw_sql: str
    rewritten_sql: str | None
    validation_status: str
    validation_report: dict[str, Any]
    referenced_tables: list[str]


class MetricUsedRead(BaseModel):
    """One definition an answer matched, as the chip's hover shows it."""

    metric: str
    entity: str
    label: str = ""
    expression: str = ""
    filters: list[str] = Field(default_factory=list)


class RunKnowledge(BaseModel):
    """What the answer's badge says, and the evidence behind it.

    Three tiers, and the most consequential decision here is that **Generated
    is not a warning**. It is the default path, it is most answers, and
    dressing it in amber would train every reader to ignore amber within a
    week. Verified *earns* a chip; Generated gets an honest sentence.

    `question` and `bound_params` are not optional decoration. The matched
    question is the reader's only defence against a confident wrong match, and
    the bindings answer the next thing a suspicious reader wants to know —
    *did it think July or June?*
    """

    tier: Literal["VERIFIED", "GROUNDED", "GENERATED"] = "GENERATED"
    template_id: UUID | None = None
    #: The matched template's question, shown verbatim.
    question: str = ""
    #: `{"region": "EMEA", "year": "2026-01-01"}`.
    bound_params: dict[str, str] = Field(default_factory=dict)
    score: float = 0.0
    matcher: str = ""
    #: True once somebody asked for a fresh answer instead of this one.
    overridden: bool = False
    #: This reader's own verdict on this answer, and what became of it. Theirs,
    #: not anyone else's: the footer shows what *you* said, and showing a
    #: colleague's verdict there would be an opinion presented as a fact.
    feedback: AnswerFeedbackRead | None = None
    #: The semantic layer metrics whose definitions this answer's SQL matched —
    #: `used` verdicts only (Phase 3 of the semantic layer plan). Not a fourth
    #: tier: evidence beside the chip. Empty when no layer reached the prompt.
    metrics_used: list[MetricUsedRead] = Field(default_factory=list)
    #: The layer version those definitions were read from.
    metrics_version: int | None = None


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    status: str
    #: QUICK | DEEP. A deep turn renders report-shaped, with the plan panel.
    #: An unset value is QUICK: the column's default is applied at insert, so
    #: a run read before its first flush carries None, and every run before
    #: `0041` was a quick one.
    depth: str = "QUICK"
    error_code: str | None = None
    error_message: str | None = None
    repair_count: int = 0
    total_latency_ms: int | None = None
    db_latency_ms: int | None = None
    #: What the whole turn used, as the run row recorded it — and it equals the
    #: sum of `steps` below, because both are written from the same calls. Null
    #: on a run written before the counting existed, and on one whose provider
    #: reported no usage; the trail's header prints a total only when there is
    #: one, never `0 tokens`, which reads as a measurement and is not one.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    #: The database this turn was asked against, so a withheld turn's
    #: explainer can name the resource the reader needs access to. An
    #: identifier and nothing else — the connection's *name* was already here,
    #: in `model_snapshot`, since a past answer has to stay explainable after
    #: its source is deleted. Null when that has happened.
    connection_id: UUID | None = None
    #: Which semantic layer version answered: `0` when none reached the prompt,
    #: `None` on a run from before versions were recorded (migration `0032`).
    semantic_layer_version: int | None = None
    #: The sections this turn was answered from, in the order the pick named
    #: them — what the *Answered from* chip shows. Empty when the connection
    #: has no sections, when the question was about the database as a whole,
    #: and on every run from before `0036`. Withheld on a restricted turn
    #: with the rest of what names the database.
    retrieval_sections: list[str] = Field(default_factory=list)
    steps: list[RunStepRead] = Field(default_factory=list)
    artifacts: list[ArtifactRead] = Field(default_factory=list)
    queries: list[GeneratedQueryRead] = Field(default_factory=list)
    knowledge: RunKnowledge = Field(default_factory=lambda: RunKnowledge())
    # The intersection rule, on a turn in a shared thread: this reader may see
    # the transcript and not the database it was asked against, so the prose
    # survives and the table, the chart and the statement do not. Set in
    # `conversations.py`; `artifacts` and `queries` arrive empty beside it.
    restricted: bool = False
    restricted_reason: str | None = None

    @field_validator("depth", mode="before")
    @classmethod
    def _quick_when_unset(cls, value: Any) -> Any:
        return value or "QUICK"

    @field_validator("retrieval_sections", mode="before")
    @classmethod
    def _sections_or_empty(cls, value: object) -> object:
        """NULL on the column is "nothing was recorded", and that is an empty
        list on the wire: the chip draws nothing either way, and no client
        should have to tell a run from before `0036` from one answered from
        the whole database."""
        return value or []


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    seq: int
    role: str
    content: str | None
    created_at: datetime
    run: RunRead | None = None


class MessageAccepted(BaseModel):
    run_id: UUID
    message_id: UUID


class SuggestionsRead(BaseModel):
    """Model-proposed follow-up questions for a live conversation.

    Best-effort and ephemeral: an empty list is a valid answer (no schema, no
    model, or the provider was unavailable) and must not be treated as an error.
    """

    suggestions: list[str] = Field(default_factory=list)
