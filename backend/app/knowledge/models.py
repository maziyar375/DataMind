"""The artifact: a parameterized question→SQL template.

Pure Pydantic, no I/O. The ORM row in `app/infra/db/models.py` stores these
fields; this module is what every other layer passes around, and what the
guard, the parameter proposer and (from Phase 2) the matcher agree on.

Three fields carry more weight than they look like they do:

* **`note`** is the only free text in the design, and it is deliberately not
  rendered into any prompt. It is written for the *next curator*. Research
  measured that more prose in the prompt lowers execution accuracy; a note
  that never reaches a prompt cannot.
* **`literal_provenance`** decides whether the template's literals may be
  shown under a restrictive disclosure policy — see `docs/security.md`.
* **`role`** decides whether a template may be retrieved, benchmarked, or
  neither. A column rather than a convention, because a convention will not
  survive six months.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.value_objects import HintBudget


class ParamType(StrEnum):
    """What a slot holds, and therefore how Phase 2 binds it from a question."""

    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"

    @property
    def is_temporal(self) -> bool:
        return self in (ParamType.DATE, ParamType.DATETIME)


class TemplateRole(StrEnum):
    """Retrieved, measured, or neither — never both at once (§1.3).

    A held-out question answered from its own stored SQL measures nothing, so
    the exclusion is enforced in the query that builds the candidate set.
    """

    RETRIEVABLE = "RETRIEVABLE"
    BENCHMARK_ONLY = "BENCHMARK_ONLY"
    HELD_OUT = "HELD_OUT"

    @property
    def is_retrievable(self) -> bool:
        return self is TemplateRole.RETRIEVABLE


class TemplateStatus(StrEnum):
    """`STALE` and `CONFLICTED` are withdrawn from use and kept visible.

    Deleting a person's work to hide drift is worse than showing it — the rule
    the semantic layer already follows for a human-written entry.
    """

    ACTIVE = "ACTIVE"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    ARCHIVED = "ARCHIVED"

    @property
    def is_usable(self) -> bool:
        return self is TemplateStatus.ACTIVE


class TemplateSource(StrEnum):
    """Where the text came from. Records provenance; grants nothing."""

    MANUAL = "MANUAL"
    CHAT_CONFIRMED = "CHAT_CONFIRMED"
    CHAT_CORRECTED = "CHAT_CORRECTED"
    TILE = "TILE"
    REPORT_BLOCK = "REPORT_BLOCK"


class LiteralProvenance(StrEnum):
    """Who chose the literals in the statement — a disclosure question.

    A hand-authored literal travels with structure, like a catalog comment: a
    person typed it, it is not read from a row, and it does not change when the
    data changes. One a *model* chose may have come from sampled values
    disclosed under a policy that has since been tightened, so it is gated like
    a sample value. `docs/security.md`.
    """

    HUMAN_AUTHORED = "HUMAN_AUTHORED"
    MODEL_DERIVED = "MODEL_DERIVED"


class TemplateParam(BaseModel):
    """One declared slot. `comment` is for the curator *and* for Phase 2's
    binder — a string parameter's comment may list the values it accepts."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: ParamType = ParamType.STRING
    comment: str = ""

    @property
    def placeholder(self) -> str:
        return f":{self.name}"

    def values(self) -> list[str]:
        """The value list a comment like `one of: EMEA, NA, APAC` declares.

        Empty when the comment is prose. Parsed rather than stored as a
        separate field because the curator writes one thing, not two, and a
        second field would be the one that goes stale.
        """
        _, _, tail = self.comment.partition(":")
        if not tail or "," not in tail:
            return []
        return [v.strip() for v in tail.split(",") if v.strip()]


class KnowledgeTemplate(BaseModel):
    """One taught question, as every layer above the database sees it."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    connection_id: UUID | None = None

    question: str = ""
    question_normalized: str = ""
    sql: str = ""
    params: list[TemplateParam] = Field(default_factory=list)
    note: str = ""

    source: TemplateSource = TemplateSource.MANUAL
    literal_provenance: LiteralProvenance = LiteralProvenance.HUMAN_AUTHORED
    role: TemplateRole = TemplateRole.RETRIEVABLE
    status: TemplateStatus = TemplateStatus.ACTIVE
    status_reason: str = ""

    schema_version: int = 0
    referenced_tables: list[str] = Field(default_factory=list)
    conflicts_with: list[UUID] = Field(default_factory=list)

    created_by: UUID | None = None
    verified_by: UUID | None = None
    verified_at: datetime | None = None
    last_validated_at: datetime | None = None
    hit_count: int = 0
    last_hit_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_matchable(self) -> bool:
        """Whether Phase 2 may short-circuit on this row.

        Both halves matter and both are enforced here rather than at each call
        site: a benchmark row answering its own question measures nothing, and
        a stale row answers with SQL the schema no longer supports.
        """
        return self.status.is_usable and self.role.is_retrievable

    def param(self, name: str) -> TemplateParam | None:
        return next((p for p in self.params if p.name == name), None)


def may_render_literals(
    provenance: LiteralProvenance, budget: HintBudget
) -> bool:
    """Whether this template's literals may leave, under the policy in force.

    **This is a rung of the disclosure ladder, and it is the one nobody else
    got right.** A connection declares `NONE | AGGREGATE | SAMPLE | FULL`, and
    `HintBudget` gates what the schema block may say about a column's
    *contents*. Under `NONE` and `AGGREGATE`, `value_lists` is false: no
    literal read from a row reaches the model, ever.

    A template's SQL contains literals. Rendered into a prompt, or into a
    "this is the saved answer" panel, `WHERE tier = 'ENTERPRISE' AND region =
    'EMEA'` puts two column values in front of the model on a connection whose
    policy says none may go. The ladder is not bypassed by a bug — it is
    bypassed because the template travels on a path the ladder does not cover.

    The rule follows from precedent the codebase already contains. Catalog
    comments are exempt from the gate for a stated reason: *a comment is DDL a
    human wrote — it is not read from a row, it does not change when the data
    changes, and it is exactly as much "customer data" as a column name.* A
    hand-authored template meets all three tests. A `MODEL_DERIVED` one does
    not: its literals may have come from sampled values disclosed under a
    policy that has since been tightened, and a store that survived the
    tightening would quietly undo it.

    > **A template's literals travel with structure when a human wrote them,
    > and are gated like sample values when a machine did.**

    Called at **render** time, never at write time — the same discipline
    `disclose()`, `HintBudget` and `disclose_history()` follow, so tightening a
    policy takes effect on the next question without a re-sync.
    """
    if provenance is LiteralProvenance.HUMAN_AUTHORED:
        return True
    return budget.value_lists
