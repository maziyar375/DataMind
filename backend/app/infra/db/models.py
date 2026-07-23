"""SQLAlchemy 2.x ORM for the application database only.

Customer databases are never modelled here; they are reached through
connectors and described by `schema_snapshots`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )


# ── identity ─────────────────────────────────────────────────────────────
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="MEMBER")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip: Mapped[str | None] = mapped_column(String(64))


# ── configuration ────────────────────────────────────────────────────────
class LlmConfig(Base, TimestampMixin):
    __tablename__ = "llm_configs"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_llm_owner_name"),)

    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    temperature: Mapped[float] = mapped_column(nullable=False, default=0.2)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    # Encrypted envelope produced by SecretBox. Never serialised outward.
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="UNTESTED")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatabaseConnection(Base, TimestampMixin):
    __tablename__ = "database_connections"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_conn_owner_name"),)

    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    database_type: Mapped[str] = mapped_column(String(20), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    ssl_mode: Mapped[str | None] = mapped_column(String(30))
    schema_allowlist: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    max_rows: Mapped[int] = mapped_column(Integer, default=1000)
    statement_timeout_ms: Mapped[int] = mapped_column(Integer, default=30_000)
    disclosure_policy: Mapped[str] = mapped_column(String(20), default="SAMPLE")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="UNTESTED")
    readonly_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    server_version: Mapped[str | None] = mapped_column(String(100))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchemaSnapshotRow(Base):
    __tablename__ = "schema_snapshots"

    id: Mapped[uuid.UUID] = _pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dialect: Mapped[str] = mapped_column(String(20), nullable=False)
    tables: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    relationships: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ── conversation ─────────────────────────────────────────────────────────
class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="New chat")
    default_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("database_connections.id", ondelete="SET NULL")
    )
    default_llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_message_seq: Mapped[int | None] = mapped_column(Integer)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_message_seq"),
    )

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ── runs ─────────────────────────────────────────────────────────────────
class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_conversation_created", "conversation_id", "created_at"),
        Index("ix_runs_status_heartbeat", "status", "heartbeat_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    # Denormalised so ownership scoping is a single-index lookup on the hot path.
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id"), nullable=False
    )
    llm_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("llm_configs.id"), nullable=False
    )
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer)
    db_latency_ms: Mapped[int | None] = mapped_column(Integer)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    fencing_token: Mapped[int | None] = mapped_column(BigInteger)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunStep(Base):
    """Persisted, not SSE-only: the step chips must survive a page refresh."""

    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_step_seq"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class GeneratedQuery(Base):
    __tablename__ = "generated_queries"
    __table_args__ = (UniqueConstraint("run_id", "attempt_no", name="uq_gq_attempt"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_sql: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_sql: Mapped[str | None] = mapped_column(Text)
    dialect: Mapped[str] = mapped_column(String(20), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    referenced_tables: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    referenced_columns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QueryExecution(Base):
    __tablename__ = "query_executions"

    id: Mapped[uuid.UUID] = _pk()
    generated_query_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generated_queries.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    rows_scanned_estimate: Mapped[int | None] = mapped_column(BigInteger)
    db_error_code: Mapped[str | None] = mapped_column(String(60))
    db_error_message: Mapped[str | None] = mapped_column(Text)
    result_schema: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    result_ref: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_kind", "run_id", "kind"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    storage: Mapped[str] = mapped_column(String(20), default="INLINE")
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RunEventRow(Base):
    """Durable event log so SSE can replay from Last-Event-ID after a reconnect."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_actor_at", "actor_user_id", "at"),
        Index("ix_audit_action_at", "action", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    actor_ip: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
