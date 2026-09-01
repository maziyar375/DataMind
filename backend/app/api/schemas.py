"""Request/response DTOs.

The read models here deliberately have no password or api_key field. There is
no serialization path that produces one; a CI test greps the generated
OpenAPI schema to prove it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr

# The one place that knows what an outline can be asked for. Imported rather
# than restated so the range the API rejects and the range the prompt honours
# cannot drift apart.
from app.reports.outline import (
    DEFAULT_SECTION_TARGET,
    MAX_SECTION_TARGET,
    MIN_SECTION_TARGET,
)


# ── auth ─────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (OAuth token type, not a secret)
    expires_in: int


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str
    role: str


# ── users ────────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["ADMIN", "MEMBER"] = "MEMBER"


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    role: Literal["ADMIN", "MEMBER"] | None = None
    status: Literal["ACTIVE", "INVITED", "DISABLED"] | None = None


class AdminSetPasswordRequest(BaseModel):
    """An admin sets a known password for another user.

    A floor of 8 characters, no ceiling that would matter — the value is
    hashed, never stored — is the whole policy. The request carries the
    password only; who may send it is decided by the admin dependency.
    """

    password: SecretStr = Field(min_length=8, max_length=200)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str
    role: str
    status: str
    created_at: datetime


class UserInviteResponse(BaseModel):
    """The temp password is shown exactly once, at creation, and never again."""
    user: UserRead
    temporary_password: str


# ── llm configs ──────────────────────────────────────────────────────────
class LlmConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal["OpenAI-compatible", "Anthropic"]
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=200_000)


class LlmConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: Literal["OpenAI-compatible", "Anthropic"] | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)


class LlmConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    provider: str
    base_url: str | None
    model: str
    temperature: float
    max_tokens: int
    status: str
    has_api_key: bool = False
    last_tested_at: datetime | None = None


class TestResult(BaseModel):
    ok: bool
    latency_ms: int
    message: str | None = None
    detected_capabilities: dict[str, Any] = Field(default_factory=dict)


class LlmConfigTestRequest(BaseModel):
    """Probe a model configuration straight from the (possibly unsaved) form.

    `config_id` is set when an existing config is being edited: it lets the
    saved API key be reused when the key field was left blank, so every other
    value still comes from the form rather than from the stored row.
    """

    config_id: UUID | None = None
    provider: Literal["OpenAI-compatible", "Anthropic"]
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=200_000)


# ── connections ──────────────────────────────────────────────────────────
class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    database_type: Literal["postgres", "mysql", "mssql", "oracle"] = "postgres"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr
    ssl_mode: Literal["require", "verify-full", "disable"] | None = "require"
    schema_allowlist: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    statement_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    disclosure_policy: Literal["NONE", "AGGREGATE", "SAMPLE", "FULL"] = "SAMPLE"
    clarify_enabled: bool = True
    include_db_comments: bool = True


class ConnectionUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    ssl_mode: Literal["require", "verify-full", "disable"] | None = None
    schema_allowlist: list[str] | None = None
    max_rows: int | None = Field(default=None, ge=1, le=100_000)
    statement_timeout_ms: int | None = Field(default=None, ge=1_000, le=300_000)
    disclosure_policy: Literal["NONE", "AGGREGATE", "SAMPLE", "FULL"] | None = None
    semantic_layer_enabled: bool | None = None
    clarify_enabled: bool | None = None
    include_db_comments: bool | None = None
    #: The scheduled conflict checker's off switch. It is the one part of the
    #: learning loop that runs statements against the customer's database
    #: without anybody asking, so it gets a checkbox rather than an argument.
    conflict_checks_enabled: bool | None = None
    #: Whether taught questions reach the generate prompt as few-shot examples.
    #: Off is byte-identical to v8 and is the default, until the eval gate in
    #: `docs/eval.md` §6.1 says otherwise.
    knowledge_examples_enabled: bool | None = None


class ConnectionRead(BaseModel):
    """Note the absence of any password field. There is no read model with one."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    database_type: str
    host: str
    port: int
    database_name: str
    username: str
    ssl_mode: str | None
    schema_allowlist: list[str]
    max_rows: int
    statement_timeout_ms: int
    disclosure_policy: str
    semantic_layer_enabled: bool = True
    clarify_enabled: bool = True
    include_db_comments: bool = True
    conflict_checks_enabled: bool = True
    knowledge_examples_enabled: bool = False
    status: str
    readonly_confirmed: bool
    server_version: str | None = None
    last_tested_at: datetime | None = None
    last_synced_at: datetime | None = None


class ConnectionTestResult(BaseModel):
    ok: bool
    latency_ms: int
    server_version: str | None = None
    readonly_confirmed: bool = False
    message: str | None = None


class ConnectionTestRequest(BaseModel):
    """Probe credentials straight from the (possibly unsaved) form.

    Only the fields needed to open a socket. Row limits and the disclosure
    policy do not affect whether a connection works, so they are not asked for.

    `connection_id` is set when an existing connection is being edited: it lets
    the saved password be reused when the password field was left blank, so
    every other value still comes from the form rather than the stored row.
    """

    connection_id: UUID | None = None
    database_type: Literal["postgres", "mysql", "mssql", "oracle"] = "postgres"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr | None = None
    ssl_mode: Literal["require", "verify-full", "disable"] | None = "require"


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None
    # The description the database's own catalog carries. Absent on a snapshot
    # taken before comments were captured, and absent on any object nobody
    # documented — so `None`, not `""`, and the UI shows nothing rather than an
    # empty quotation.
    comment: str | None = None


class SchemaTable(BaseModel):
    schema_name: str = Field(alias="schema")
    name: str
    columns: list[SchemaColumn] = Field(default_factory=list)
    approx_row_count: int | None = None
    comment: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class SchemaCatalogCounts(BaseModel):
    """How many descriptions the last sync actually found."""

    tables: int = 0
    columns: int = 0


class SchemaCatalogMeta(BaseModel):
    """Catalog description one level above a table, plus what was picked up.

    Every field is optional because only PostgreSQL and SQL Server carry a
    database or schema description at all — MySQL has none outside MariaDB and
    Oracle has neither — so a client must treat all of this as absent by
    default rather than as empty.
    """

    database_comment: str | None = None
    schema_comments: dict[str, str] = Field(default_factory=dict)
    counts: SchemaCatalogCounts = Field(default_factory=SchemaCatalogCounts)


class SchemaRelationship(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class SchemaRead(BaseModel):
    dialect: str
    version: int
    synced_at: datetime | None = None
    tables: list[SchemaTable] = Field(default_factory=list)
    relationships: list[SchemaRelationship] = Field(default_factory=list)
    catalog_meta: SchemaCatalogMeta = Field(default_factory=SchemaCatalogMeta)


# ── semantic layer ───────────────────────────────────────────────────────
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
    """The document plus everything the UI needs to frame it."""

    document: dict[str, Any] = Field(default_factory=dict)
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


class SemanticGenerateRequest(BaseModel):
    llm_config_id: UUID
    # MERGE keeps every entity a person edited; REPLACE is the explicit
    # "start over" the UI has to make the user confirm.
    mode: Literal["MERGE", "REPLACE"] = "MERGE"
    # Empty means the whole schema.
    only_tables: list[str] = Field(default_factory=list)


class SemanticSaveRequest(BaseModel):
    document: dict[str, Any]


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


# ── knowledge templates ──────────────────────────────────────────────────
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


# ── the audit log (Phase 8) ──────────────────────────────────────────────
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


# ── the embedding matcher (Phase 7) ──────────────────────────────────────
class EmbeddingWrite(BaseModel):
    """Turn embedding search on or off for a connection.

    `model` is optional and almost always left empty: the probe tries the
    provider's small embedding model and pins whatever answers. A deployment
    running its own endpoint — Ollama, vLLM, a gateway — names its model here,
    and the *dimension* is never asked for, because it is measured from the
    endpoint's own reply rather than trusted from a form.
    """

    enabled: bool
    model: str = Field(default="", max_length=200)


class EmbeddingStatus(BaseModel):
    """What the store's embedding index looks like right now.

    `available` and `indexed` are separate on purpose: a connection can have a
    model pinned and no vectors yet (the first pass has not run) and that is a
    normal state, not a failure. The UI says "indexing" for it rather than
    "on", because "on" would promise a behaviour the next question will not
    show.
    """

    #: A model is pinned. False is the shipped default and means the lexical
    #: matcher answers — which is a state, not a degradation.
    enabled: bool = False
    model: str = ""
    dimension: int = 0
    #: Live templates that could carry a vector, and how many currently do.
    templates: int = 0
    indexed: int = 0
    #: Everything the probe or the last pass had to say. Empty on success.
    message: str = ""


# ── benchmarks and the score (Phase 6) ───────────────────────────────────
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
    #: whether they may be disclosed. `docs/security.md`.
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


# ── SQL drafts & tile results ────────────────────────────────────────────
# These are the dashboard's two shared shapes. `TileResultRead` is what a tile
# returns after a refresh *and* what the editor previews with — one shape,
# because a preview that could differ from a refresh is a preview that lies.
class TileErrorRead(BaseModel):
    code: str
    message: str = ""


class TileColumnRead(BaseModel):
    name: str
    db_type: str = ""
    semantic_type: str = "nominal"


class ChartRedrawRequest(BaseModel):
    """Redraw a finished run's result as a different chart type.

    Only the type: a reader picking "heatmap" from a grid has not picked
    columns, and the platform already knows which columns a heatmap of this
    result would use — it is the same choice it would have made itself.
    """

    chart_type: str


class ChartRedrawRead(BaseModel):
    """A recompiled spec, plus the verdicts the picker needs to stay honest.

    The options travel with every response so a reader who has just redrawn a
    chart is looking at a picker describing the same result, without a second
    round trip.

    Nothing here is persisted. A transcript records what a run produced, and
    quietly rewriting yesterday's chart artifact because someone flipped a
    picker today would make the step trail ("bar chart (model)") a lie about
    the row beside it. The new spec lives in the browser for as long as the
    reader is looking at it.
    """

    spec: dict[str, Any] | None = None
    chart_type: str
    reason: str | None = None
    options: list[ChartOptionRead] = Field(default_factory=list)


class ChartOptionRead(BaseModel):
    """Whether one chart type fits a given result, and if not, why not.

    `supported` is computed by asking the real planner for that type and seeing
    whether it comes back unchanged, so it cannot drift from what the compiler
    would actually do. `reason` is prose for a tooltip and decides nothing.

    `columns` is the channel → column map that made the verdict true, and it is
    what keeps "supported" from being a promise about columns the caller then
    does not use.
    """

    chart_type: str
    supported: bool
    reason: str | None = None
    columns: dict[str, str] | None = None


class TileResultRead(BaseModel):
    status: Literal["OK", "ERROR"] = "OK"
    columns: list[TileColumnRead] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    # Not optional: with every tile on its own clock, "as of 14:32" is the only
    # way a reader tells a 30-second tile from the hourly one beside it.
    computed_at: datetime
    vega_spec: dict[str, Any] | None = None
    # Who chose the chart — model | model_adjusted | heuristic | none — and, when
    # the pick was overruled, what happened. A demoted chart says so out loud
    # rather than quietly drawing something else.
    chart_source: str = "none"
    chart_note: str | None = None
    # A `KpiSpec` for a METRIC tile: the value already written out, its label,
    # and whatever comparison the result supported. Decided on this side so a
    # tile and a chat turn showing the same number agree about it.
    kpi: dict[str, Any] | None = None
    error: TileErrorRead | None = None


#: What the editor's type picker is set to while a draft is being made. It is a
#: *hint about the destination*, never a promise — nothing is saved here, and
#: the tile save path validates the real type independently. Optional so a
#: client that does not send it behaves exactly as one written before this
#: existed.
TileTypeHint = Literal["CHART", "TABLE", "METRIC", "TEXT"] | None


class SqlDraftRequest(BaseModel):
    connection_id: UUID
    llm_config_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    # METRIC earns two things: SQL rules that ask for a series rather than a
    # lone figure, and a KPI on the preview so the editor shows the big number
    # it will actually draw.
    tile_type: TileTypeHint = None


class SqlValidateRequest(BaseModel):
    """The hand-written path *and* the "I edited the model's draft" path."""

    connection_id: UUID
    sql: str = Field(min_length=1, max_length=100_000)
    # No prompt on this road, so this buys the preview's KPI alone — which is
    # what lets someone writing their own `SELECT month, SUM(...)` see the
    # delta and the sparkline before saving.
    tile_type: TileTypeHint = None


class SqlDraftRead(BaseModel):
    """A statement, why the guard accepted or refused it, and what it returns.

    `validation_status` is REJECTED for a refused draft and the response is
    still a 200: the editor renders the guard's reasons inline the way the
    metric editor does, and a 4xx would make "the model wrote SQL I can show
    you" indistinguishable from "your request was malformed".
    """

    sql: str
    validation_status: str
    validation_report: dict[str, Any] = Field(default_factory=dict)
    referenced_tables: list[str] = Field(default_factory=list)
    # A `ChartIntent` for the editor's pickers to default from; null when the
    # preview's shape suggests nothing.
    chart_suggestion: dict[str, Any] | None = None
    # Who chose it: `model` / `model_adjusted` when a model read the question,
    # `heuristic` when only the column shape was consulted, null when nothing
    # was decided. The editor pre-selects a chart type for the first two and
    # leaves *Auto* alone for the rest.
    chart_source: str | None = None
    # Per-type verdicts for the picker: `{chart_type, supported, reason}`.
    # Empty means "no opinion" — the editor leaves every type enabled.
    chart_options: list[ChartOptionRead] = Field(default_factory=list)
    preview: TileResultRead | None = None
    question: str | None = None
    llm_config_id: UUID | None = None


# ── dashboards ───────────────────────────────────────────────────────────
class TableColumnConfig(BaseModel):
    """One column of a TABLE tile, as the editor configured it.

    Position in the list *is* the display order. A column the result returns
    but this list does not mention is shown at the end rather than hidden: a
    query that gains a column must not silently drop it from the tile.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    hidden: bool = False
    # None keeps the column's own name. An empty string is a real choice — a
    # blank header — so this is nullable rather than defaulting to "".
    label: str | None = Field(default=None, max_length=200)
    align: Literal["auto", "left", "right", "center"] = "auto"
    # "auto" is what the table did before this existed: integers grouped,
    # decimals to two places, everything else as text.
    format: Literal["auto", "integer", "decimal", "percent", "text"] = "auto"


class TableConfig(BaseModel):
    """How a TABLE tile is drawn. Presentation only — see `models.py`.

    Validated here, on the way in, rather than trusted from the browser; but
    `DashboardTileRead.table_config` stays a plain dict, because a row that
    somehow holds a shape this model refuses must still be *readable* — the
    alternative is one bad tile turning its whole dashboard into a 500.
    """

    model_config = ConfigDict(extra="forbid")

    columns: list[TableColumnConfig] = Field(default_factory=list, max_length=500)
    sort_column: str | None = Field(default=None, max_length=200)
    sort_direction: Literal["asc", "desc"] = "asc"


class DashboardTileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    dashboard_id: UUID
    connection_id: UUID | None = None
    # Names only, for the tile's chips. A tile never carries a host, a
    # username, or anything else from inside a connection.
    connection_name: str | None = None
    llm_config_id: UUID | None = None
    llm_config_name: str | None = None
    title: str = ""
    tile_type: str = "CHART"
    question: str | None = None
    sql: str = ""
    sql_origin: str = "GENERATED"
    # null means Auto: the chart is re-planned from each result.
    chart_config: dict[str, Any] | None = None
    # null means "as the query returned it": every column, in query order.
    table_config: dict[str, Any] | None = None
    max_rows: int | None = None
    # null means "inherit the dashboard's default"; 0 means manual only. The
    # resolved number is sent alongside so the scheduler needs no second rule.
    refresh_interval_seconds: int | None = None
    effective_refresh_interval_seconds: int = 0
    grid_x: int = 0
    grid_y: int = 0
    grid_w: int = 4
    grid_h: int = 4
    position: int = 0
    created_at: datetime
    updated_at: datetime


class DashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None = None
    status: str = "ACTIVE"
    grid_columns: int = 12
    row_height_px: int = 60
    gap_px: int = 12
    compact_mode: str = "VERTICAL"
    palette: str = "default"
    theme_override: str = "INHERIT"
    default_refresh_interval_seconds: int = 0
    created_at: datetime
    updated_at: datetime
    # The dashboard and its tiles, never their results: a tile's data is asked
    # for separately, because each tile is on its own clock.
    tiles: list[DashboardTileRead] = Field(default_factory=list)


class DashboardSummaryRead(BaseModel):
    """One card on the index: what it is, how big, and how fresh."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None = None
    status: str = "ACTIVE"
    default_refresh_interval_seconds: int = 0
    tile_count: int = 0
    last_refreshed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    grid_columns: int = Field(default=12, ge=1, le=48)
    row_height_px: int = Field(default=60, ge=10, le=400)
    gap_px: int = Field(default=12, ge=0, le=64)
    palette: str = Field(default="default", max_length=30)
    theme_override: Literal["INHERIT", "DARK", "LIGHT"] = "INHERIT"
    default_refresh_interval_seconds: int = Field(default=0, ge=0, le=86_400)


class DashboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None
    grid_columns: int | None = Field(default=None, ge=1, le=48)
    row_height_px: int | None = Field(default=None, ge=10, le=400)
    gap_px: int | None = Field(default=None, ge=0, le=64)
    compact_mode: Literal["VERTICAL", "NONE"] | None = None
    palette: str | None = Field(default=None, max_length=30)
    theme_override: Literal["INHERIT", "DARK", "LIGHT"] | None = None
    default_refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)


class TileCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    tile_type: Literal["CHART", "TABLE", "METRIC", "TEXT"] = "CHART"
    connection_id: UUID | None = None
    llm_config_id: UUID | None = None
    question: str | None = None
    sql: str = ""
    # Provenance, never trust: the guard cannot tell these apart and does not
    # look. It exists so the editor knows which tab it opened on.
    sql_origin: Literal["GENERATED", "GENERATED_EDITED", "HANDWRITTEN"] = "GENERATED"
    chart_config: dict[str, Any] | None = None
    table_config: TableConfig | None = None
    max_rows: int | None = Field(default=None, ge=1)
    refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)
    grid_x: int = Field(default=0, ge=0)
    grid_y: int = Field(default=0, ge=0)
    grid_w: int = Field(default=4, ge=1)
    grid_h: int = Field(default=4, ge=1)
    position: int = Field(default=0, ge=0)


class TileUpdate(BaseModel):
    """Every field optional; only what is sent is changed.

    `chart_config` and `refresh_interval_seconds` can be set back to null on
    purpose — "Auto" and "inherit" are values, not the absence of one — so a
    client clears them by sending an explicit null, and omitting a field leaves
    it alone.
    """

    title: str | None = Field(default=None, max_length=200)
    tile_type: Literal["CHART", "TABLE", "METRIC", "TEXT"] | None = None
    connection_id: UUID | None = None
    llm_config_id: UUID | None = None
    question: str | None = None
    sql: str | None = None
    sql_origin: Literal["GENERATED", "GENERATED_EDITED", "HANDWRITTEN"] | None = None
    chart_config: dict[str, Any] | None = None
    table_config: TableConfig | None = None
    max_rows: int | None = Field(default=None, ge=1)
    refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)
    grid_x: int | None = Field(default=None, ge=0)
    grid_y: int | None = Field(default=None, ge=0)
    grid_w: int | None = Field(default=None, ge=1)
    grid_h: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0)


class TilePosition(BaseModel):
    tile_id: UUID
    grid_x: int | None = Field(default=None, ge=0)
    grid_y: int | None = Field(default=None, ge=0)
    grid_w: int | None = Field(default=None, ge=1)
    grid_h: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0)


class LayoutUpdate(BaseModel):
    """One call per drag-end, carrying every tile the drag moved."""

    positions: list[TilePosition] = Field(default_factory=list)


class DashboardDataRequest(BaseModel):
    """Which tiles to compute. Empty means the whole dashboard.

    The normal call is a list: with per-tile rates the browser asks for the
    tiles that are *due*, and the whole dashboard is the first-paint case.
    """

    tile_ids: list[UUID] = Field(default_factory=list)


class DashboardDataRead(BaseModel):
    results: dict[UUID, TileResultRead] = Field(default_factory=dict)


# ── moving a dashboard between accounts and installations ────────────────
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


# ── reports ──────────────────────────────────────────────────────────────
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


# ── report runs ──────────────────────────────────────────────────────────
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


# ── conversations & messages ─────────────────────────────────────────────
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


class RunStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    name: str
    status: str
    detail: str | None = None
    duration_ms: int | None = None


class ArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    spec: dict[str, Any]


class GeneratedQueryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attempt_no: int
    raw_sql: str
    rewritten_sql: str | None
    validation_status: str
    validation_report: dict[str, Any]
    referenced_tables: list[str]


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


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    status: str
    error_code: str | None = None
    error_message: str | None = None
    repair_count: int = 0
    total_latency_ms: int | None = None
    db_latency_ms: int | None = None
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    steps: list[RunStepRead] = Field(default_factory=list)
    artifacts: list[ArtifactRead] = Field(default_factory=list)
    queries: list[GeneratedQueryRead] = Field(default_factory=list)
    knowledge: RunKnowledge = Field(default_factory=lambda: RunKnowledge())


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
