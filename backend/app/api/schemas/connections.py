"""Request/response DTOs: connections."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


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
    """Everything a `modify` holder may change. **Not the disclosure policy.**

    Widening `NONE` → `FULL` is not an edit, it is a disclosure decision: it
    changes how much of somebody else's query result may leave the database for
    a model provider, on a connection other people are now asking questions
    through. As of Phase 6 it lives on `PUT /connections/{id}/disclosure`,
    gated on `manage` and audited — and its absence from this schema is what
    makes that a rule rather than a convention, because a payload that could
    express it would be a second way to change it.
    """

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
    semantic_layer_enabled: bool | None = None
    clarify_enabled: bool | None = None
    include_db_comments: bool | None = None
    #: The scheduled conflict checker's off switch. It is the one part of the
    #: learning loop that runs statements against the customer's database
    #: without anybody asking, so it gets a checkbox rather than an argument.
    conflict_checks_enabled: bool | None = None
    #: Whether taught questions reach the generate prompt as few-shot examples.
    #: Off is byte-identical to v8 and is the default, until the eval gate in
    #: `docs/reference/eval.md` §6.1 says otherwise.
    knowledge_examples_enabled: bool | None = None


class ConnectionRead(BaseModel):
    """Note the absence of any password field. There is no read model with one.

    **From Phase 6 this model is narrowed at `describe`.** A principal who
    holds only `describe` on a connection — a Data Engineer, an Auditor, or
    somebody a dashboard was shared with whose tiles read through it — sees
    that it exists, what engine it is, and *what its disclosure policy is*, and
    sees **no host, no port, no database name and no username**. See
    `narrow_to_describe` below for why those four and not others.

    The fields are typed as optional rather than removed, so one model serves
    both audiences and no caller has to branch on which shape came back.
    """

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    database_type: str
    #: Blank at `describe`. Together these four are enough to attempt a
    #: connection from anywhere the database is reachable, which is why they
    #: are the line rather than "credentials" alone.
    host: str = ""
    port: int = 0
    database_name: str = ""
    username: str = ""
    ssl_mode: str | None = None
    schema_allowlist: list[str] = []
    max_rows: int = 0
    statement_timeout_ms: int = 0
    #: **Visible at `describe`, deliberately.** A grantee has to be able to see
    #: what leaves *before* they ask a question through this connection — rule
    #: 1 of §19.5, and the reason `describe` is a real privilege rather than a
    #: formality.
    disclosure_policy: str
    semantic_layer_enabled: bool = True
    clarify_enabled: bool = True
    include_db_comments: bool = True
    conflict_checks_enabled: bool = True
    knowledge_examples_enabled: bool = False
    status: str
    readonly_confirmed: bool = False
    server_version: str | None = None
    last_tested_at: datetime | None = None
    last_synced_at: datetime | None = None
    #: What the caller may do here, from the authorizer — so the SPA can render
    #: a read-only card without asking a second endpoint per row. Empty on the
    #: paths that do not resolve it; `GET …/actions` is the full answer.
    privileges: list[str] = []
    #: Who owns it, for the list's owner column. A display name, never an
    #: address — the rule the review queue already follows.
    owner: str = ""


#: The four fields a `describe` holder does not see, and the empty value each
#: is blanked to.
#:
#: A mapping rather than a tuple because `port` is an `int`: `model_copy` does
#: **not** revalidate, so blanking it to `""` would put a string on a field the
#: schema says is a number, and the only place that surfaced was a client
#: parsing it. The empty value has to match the declared type per field.
#:
#: Not "the credentials" — the password is not on this model at all and never
#: was. These four are the ones that, together, are enough to *attempt* a
#: connection from anywhere the database is reachable, so handing them to
#: somebody who may only know the connection exists is handing them the target.
#: `database_type` and `disclosure_policy` stay: the first is how the UI draws
#: an engine badge, and the second is what §19.5 requires a grantee to be able
#: to see before they ask.
DESCRIBE_HIDDEN: Mapping[str, object] = MappingProxyType({
    "host": "",
    "port": 0,
    "database_name": "",
    "username": "",
})


def narrow_to_describe(read: ConnectionRead) -> ConnectionRead:
    """`read`, with the four connection details blanked. Applied at `describe`.

    A copy rather than a mutation, because the input is built from a live ORM
    row and blanking fields on that row would write the blanks back on the next
    flush — which is the kind of bug that shows up as a connection whose host
    quietly became empty for everybody.

    `model_copy` does not revalidate, which is why `DESCRIBE_HIDDEN` carries a
    typed empty value per field rather than one blank for all four.
    """
    return read.model_copy(update=dict(DESCRIBE_HIDDEN))


class DeepBudgetWrite(BaseModel):
    """A connection's deep budget — the five numbers an administrator sets.

    Each is a ceiling on **one** deep run through this connection, and each is
    at most the installation's own (checked in the route, which knows it). A
    zero is allowed and means *no deep analysis here*: a run is then refused,
    with a reason, rather than started small.
    """

    max_steps: int = Field(ge=0)
    max_queries: int = Field(ge=0)
    max_rows_total: int = Field(ge=0)
    max_prompt_tokens: int = Field(ge=0)
    #: The hard deadline in seconds. 0 refuses; otherwise at least a minute,
    #: because a shorter one could not finish a single step and would be a
    #: silently smaller run rather than a refusal.
    deadline_seconds: int = Field(ge=0)


class DeepLimitsRead(BaseModel):
    max_steps: int
    max_queries: int
    max_rows_total: int
    max_prompt_tokens: int
    deadline_seconds: int


class DeepBudgetRead(BaseModel):
    """What one deep run through this connection may spend, and the most it could.

    `effective` is what a run started now would get: the stored budget clipped
    to the ceiling, or the ceiling itself when `is_default`. `refused` names
    the bound that stops a run starting at all, or is null.
    """

    effective: DeepLimitsRead
    ceiling: DeepLimitsRead
    is_default: bool
    refused: str | None = None


class DisclosureWrite(BaseModel):
    """Its own endpoint, its own schema, its own audit action.

    One field, and the reason it is not two more lines on `ConnectionUpdate`:
    the moment a connection is shared, **one person's disclosure choice governs
    another person's questions** — and that person may not know what it is.
    Widening `NONE` → `FULL` is a decision about what leaves the database for a
    model provider, made on behalf of everybody who can now ask through this
    connection. Filing it under "connection updated" in the audit log would be
    lying by omission.
    """

    disclosure_policy: Literal["NONE", "AGGREGATE", "SAMPLE", "FULL"]


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
