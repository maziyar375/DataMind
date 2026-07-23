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


# ── auth ─────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
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
    provider: Literal["OpenAI-compatible", "Anthropic", "Ollama", "Custom"]
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=200_000)
    is_default: bool = False


class LlmConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: Literal["OpenAI-compatible", "Anthropic", "Ollama", "Custom"] | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    is_default: bool | None = None


class LlmConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    provider: str
    base_url: str | None
    model: str
    temperature: float
    max_tokens: int
    is_default: bool
    status: str
    has_api_key: bool = False
    last_tested_at: datetime | None = None


class TestResult(BaseModel):
    ok: bool
    latency_ms: int
    message: str | None = None
    detected_capabilities: dict[str, Any] = Field(default_factory=dict)


class LlmConfigTestRequest(BaseModel):
    """Probe a model configuration that has not been saved yet."""

    provider: Literal["OpenAI-compatible", "Anthropic", "Ollama", "Custom"]
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
    is_default: bool = False


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
    is_default: bool | None = None


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
    is_default: bool
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
    """Probe credentials that have not been saved yet.

    Only the fields needed to open a socket. Row limits and the disclosure
    policy do not affect whether a connection works, so they are not asked for.
    """

    database_type: Literal["postgres", "mysql", "mssql", "oracle"] = "postgres"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr
    ssl_mode: Literal["require", "verify-full", "disable"] | None = "require"


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None


class SchemaTable(BaseModel):
    schema_name: str = Field(alias="schema")
    name: str
    columns: list[SchemaColumn] = Field(default_factory=list)
    approx_row_count: int | None = None

    model_config = ConfigDict(populate_by_name=True)


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
