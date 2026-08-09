"""SQLAlchemy 2.x ORM for the application database only.

Customer databases are never modelled here; they are reached through
connectors and described by `schema_snapshots`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
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
    # Whether the connection's semantic layer reaches the generate prompt. A
    # switch rather than "delete the layer": turning it off is how you A/B a
    # layer against the bare schema without throwing the work away.
    semantic_layer_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Whether an unanswerable question stops to ask instead of guessing. Off is
    # the pre-feature behaviour exactly: the `clarify` node is skipped, so the
    # prompt, the step trail and the eval baseline are unchanged.
    clarify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
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


class SemanticLayerRow(Base, TimestampMixin):
    """What the schema *means*, for one connection.

    One live row per connection, not a version chain like `schema_snapshots`:
    this document is edited by hand, and a user who fixes a grain statement
    expects to have fixed it, not to have forked it. `schema_version` records
    which snapshot it was written against, so the UI can say when the schema
    has moved on underneath it.
    """

    __tablename__ = "semantic_layers"
    __table_args__ = (
        UniqueConstraint("connection_id", name="uq_semantic_layers_connection"),
    )

    id: Mapped[uuid.UUID] = _pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The snapshot version this document was bound to when it was written.
    schema_version: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Denormalised for the list view, which must not deserialise a 100 kB doc
    # per connection just to draw a chip.
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    metric_count: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_count: Mapped[int] = mapped_column(Integer, default=0)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    # Which model wrote it, kept for the same reason `runs.model_snapshot` is:
    # a layer generated by a weak model is a different artefact from one
    # generated by a strong one, and six months later nobody remembers which.
    generated_by_llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(20), default="s1")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SemanticJobRow(Base):
    """One generation run. Describing 40 tables is minutes, not milliseconds,
    so the request that starts it returns immediately and the UI follows this
    row — the same trade `runs` makes, without the SSE fan-out it does not need.
    """

    __tablename__ = "semantic_jobs"
    __table_args__ = (
        Index("ix_semantic_jobs_connection_created", "connection_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    mode: Mapped[str] = mapped_column(String(20), default="MERGE")   # MERGE | REPLACE
    only_tables: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    phase: Mapped[str] = mapped_column(String(200), default="")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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

    messages: Mapped[list[Message]] = relationship(
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


# ── dashboards ───────────────────────────────────────────────────────────
class Dashboard(Base, TimestampMixin):
    """A grid of tiles, owned by one user.

    Owner-only in v1 and deliberately so: a shared dashboard would let user B
    read data pulled with user A's stored credentials, through a connection B
    does not own. That is an authorization model, not a UI feature — see §9 of
    `docs/dashboards.md`.
    """

    __tablename__ = "dashboards"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_dashboard_owner_name"),
        Index("ix_dashboards_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    # Grid geometry. Held on the dashboard rather than in the client so two
    # browsers draw the same layout from the same numbers.
    grid_columns: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    row_height_px: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    gap_px: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    compact_mode: Mapped[str] = mapped_column(String(20), default="VERTICAL")

    # A key into the pre-validated palette set, never a hex value: the chart
    # palette is measured (OKLab ΔE, CVD simulation, contrast against the
    # chart's own surface), and a free colour destroys that silently.
    palette: Mapped[str] = mapped_column(String(30), default="default")
    theme_override: Mapped[str] = mapped_column(String(20), default="INHERIT")

    # The *fallback* for tiles that set no rate of their own; 0 = manual only.
    # The rate that matters is the tile's — see `DashboardTile`.
    default_refresh_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    tiles: Mapped[list[DashboardTile]] = relationship(
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardTile.position",
    )


class DashboardTile(Base, TimestampMixin):
    """One tile: its own connection, its own SQL, its own refresh rate.

    `sql` is hostile input by definition — the tile editor hands the user a
    textarea — so it is re-validated against the connection's *current* snapshot
    on every execution. `sql_origin` records where the text came from and grants
    nothing.
    """

    __tablename__ = "dashboard_tiles"
    __table_args__ = (
        Index("ix_dashboard_tiles_dashboard_position", "dashboard_id", "position"),
    )

    id: Mapped[uuid.UUID] = _pk()
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, emphatically **not** CASCADE: deleting a connection must leave a
    # tile that says "connection removed", not silently delete the layout the
    # user built.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("database_connections.id", ondelete="SET NULL")
    )
    # Which provider drafted the SQL. Provenance and "re-ask" only — never
    # consulted at refresh, where no model is involved at all.
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    tile_type: Mapped[str] = mapped_column(String(20), nullable=False, default="CHART")
    # The plain-language question that produced the draft, kept so "edit →
    # re-ask" works and so the user can see what they meant six weeks later.
    question: Mapped[str | None] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sql_origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="GENERATED"
    )

    # A serialised `ChartIntent`. **NULL means Auto** — `plan_chart` decides
    # afresh on each result — which is why this column is nullable rather than
    # defaulting to an empty object.
    chart_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # How a TABLE tile is drawn: column order, which columns are hidden, their
    # labels, alignment, number format, and a default sort. **NULL means "as
    # the query returned it"**, the same way NULL chart_config means Auto.
    #
    # Unlike `chart_config` this never reaches the backend's own rendering: the
    # browser applies it to rows it already has, so it is **not** part of
    # `result_fingerprint` — re-running a query because someone renamed a
    # column header would be absurd. It follows that hiding a column is
    # presentation, not redaction: the value is still in the payload. Anything
    # that must not reach the owner's browser belongs to the disclosure policy
    # or the SQL, never here.
    table_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # May only *lower* the connection's cap; `query_service.effective_max_rows`
    # is where that is enforced.
    max_rows: Mapped[int | None] = mapped_column(Integer)
    # The per-tile rate, and the point of the feature: NULL = inherit the
    # dashboard's default, 0 = manual only. Do not collapse this into a
    # dashboard-level setting.
    refresh_interval_seconds: Mapped[int | None] = mapped_column(Integer)

    # Layout per tile, not one dashboard-level JSONB: a drag saves one row, so
    # two open tabs cannot lose each other's edits.
    grid_x: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grid_y: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grid_w: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    grid_h: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    dashboard: Mapped[Dashboard] = relationship(back_populates="tiles")


class DashboardTileCache(Base):
    """The last computed result for a tile, keyed by the tile itself.

    In Postgres rather than in-process because the reconciler already assumes
    several workers may exist, and an in-process cache would go stale per
    worker. Written in Phase 4; the table lands here so there is one DDL step.
    """

    __tablename__ = "dashboard_tile_cache"

    tile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dashboard_tiles.id", ondelete="CASCADE"), primary_key=True
    )
    # The hash of the SQL that produced `result`. A tile whose SQL was edited
    # must miss the cache however fresh the row is.
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A failed refresh is cached too: without it, a tile pointed at a broken
    # query re-runs it on every tick of every open browser.
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)


# ── reports ──────────────────────────────────────────────────────────────
class Report(Base, TimestampMixin):
    """A document the user described, approved the structure of, and keeps.

    The `reports` / `report_runs` split is the one `SemanticLayerRow` vs
    `SemanticJobRow` and `Dashboard` vs `DashboardTileCache` already reached,
    and `SemanticLayerRow`'s reason applies verbatim to a heading: *"this
    document is edited by hand, and a user who fixes a grain statement expects
    to have fixed it, not to have forked it"*. So this row is the template,
    edited in place, and a run is a moment kept beside it.

    Owner-only in v1, for the reason `Dashboard` gives: a shared report means
    user B reads data pulled with user A's stored credentials through a
    connection B does not own. That is an authorization model, not a UI feature.

    **Reports share no table and no code path with dashboards.** Deleting the
    dashboards feature would leave this working.
    """

    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_report_owner_name"),
        Index("ix_reports_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # The user's original request, kept verbatim: it is what the outline was
    # proposed from, and what the prose prompt narrates towards months later.
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # SET NULL, emphatically **not** CASCADE: a deleted connection must leave a
    # readable report that cannot regenerate, never delete the user's work.
    # **Immutable after creation** (422) — byte-for-byte the conversation rule
    # in `_bind_connection`, because a report keyed to one connection cannot
    # cross disclosure policies.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("database_connections.id", ondelete="SET NULL")
    )
    # Changeable at any time. The model decides who writes the prose, not what
    # is in it.
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    # `fa` | `en`. **Derived from `prompt`, never chosen** (`reports/language.py`)
    # and re-derived when the request is rewritten, so a report cannot end up
    # described in one language and written in another. Still stored rather
    # than computed on read: a run snapshots it, and the document's direction
    # and furniture are read off it long after the request has been edited.
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="en")
    # How many sections the *model* is asked to propose. The executive summary
    # is added on top of it by the service, and the user adds or removes
    # sections freely afterwards — this governs the proposal, not the outline.
    section_target: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    sections: Mapped[list[ReportSection]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportSection.position",
    )


class ReportSection(Base, TimestampMixin):
    """One heading, and the N blocks under it.

    N, not one: *«روند درآمد و محصولات پرفروش»* is two queries under one
    heading. Prose is per-**section** and data is per-**block**, and one
    paragraph narrating several results together is the entire payoff.
    """

    __tablename__ = "report_sections"
    __table_args__ = (
        Index("ix_report_sections_report_position", "report_id", "position"),
    )

    id: Mapped[uuid.UUID] = _pk()
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heading: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # One line on what this section's paragraph should cover, written by the
    # outline model and editable by the user. It is prompt input, not display
    # text — the heading is what the document shows.
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="NORMAL")

    report: Mapped[Report] = relationship(back_populates="sections")
    blocks: Mapped[list[ReportBlock]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="ReportBlock.position",
    )


class ReportBlock(Base, TimestampMixin):
    """One question, one query, one chart.

    `sql` is hostile input by definition and is re-validated against the
    connection's *current* snapshot on every execution, through the same
    `execute_saved_sql` dashboards use. `sql_origin` records where the text
    came from and grants nothing — same rule as `dashboard_tiles.sql_origin`.
    """

    __tablename__ = "report_blocks"
    __table_args__ = (
        Index("ix_report_blocks_section_position", "section_id", "position"),
    )

    id: Mapped[uuid.UUID] = _pk()
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("report_sections.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The plain-language question. **This is what the user edits in v1**, not
    # the SQL — the outline is the primary interaction, and the SQL editor is
    # deferred.
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # What makes run-to-run comparison honest: comparing two runs whose SQL
    # differs is a lie, so the hash is recorded on the block and copied onto
    # every result it produces.
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sql_origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="GENERATED"
    )
    block_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="CHART"
    )
    # A serialised `ChartIntent`. **NULL means Auto** — right for a report
    # re-run months later against differently-shaped data.
    chart_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # A label only. The window lives in the SQL as relative date arithmetic the
    # database resolves on every run; this is never substituted into a
    # statement. See §6 of `docs/reports-plan.md`.
    time_window: Mapped[str] = mapped_column(String(30), nullable=False, default="none")

    feasibility_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNCHECKED"
    )
    # The guard's own message, shown to the user verbatim.
    feasibility_reason: Mapped[str | None] = mapped_column(Text)
    feasibility_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # May only *lower* the connection's cap; `query_service.effective_max_rows`
    # is where that is enforced. NULL = the connection's own cap applies.
    max_rows: Mapped[int | None] = mapped_column(Integer)

    section: Mapped[ReportSection] = relationship(back_populates="blocks")


class ReportRun(Base):
    """One generation: minutes of model calls, polled rather than streamed.

    Its status is **derived** from its section results, never set — that is what
    makes progressive rendering and per-section retry fall out for free instead
    of needing resume machinery, and it is why `PARTIAL` can exist at all.

    `model_snapshot` is copied from `SemanticLayerRow`'s reasoning: *"a layer
    generated by a weak model is a different artefact from one generated by a
    strong one, and six months later nobody remembers which."*
    """

    __tablename__ = "report_runs"
    __table_args__ = (
        Index("ix_report_runs_report_created", "report_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    phase: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_configs.id", ondelete="SET NULL")
    )
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(20), default="r1")
    # Snapshotted from the report: the language a run was written in stays
    # readable even if the template is somehow migrated later.
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="en")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReportBlockResult(Base):
    """One block's numbers, snapshotted at the moment they were computed.

    Written **as it lands**, not at the end of the run: the poll response is
    then a snapshot of what exists so far, which is the whole progressive
    rendering design — no special protocol, and a browser that reloads mid-run
    resumes exactly where it was.

    `block_id` is SET NULL and the heading, question and SQL are copied beside
    it, because a run must stay readable after the block it came from is
    deleted. A historical document that silently loses a section is not a
    historical document. `section_id` and `position` are here for the same
    reason from the other side: once the block is gone, they are what still
    groups the result under its heading and orders it within the run.
    """

    __tablename__ = "report_block_results"
    __table_args__ = (
        Index("ix_report_block_results_run_position", "run_id", "position"),
    )

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("report_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_blocks.id", ondelete="SET NULL")
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_sections.id", ondelete="SET NULL")
    )
    # The block's ordinal within the run, assigned in execution order.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    heading_snapshot: Mapped[str] = mapped_column(
        String(300), nullable=False, default=""
    )
    question_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sql_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    rows: Mapped[list[list[Any]]] = mapped_column(JSONB, default=list)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # NULL means no chart, which is an ordinary outcome: `plan_chart` vetoes
    # what the data cannot support, and the table and numbers stand alone.
    vega_spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    chart_source: Mapped[str | None] = mapped_column(String(20))
    chart_note: Mapped[str | None] = mapped_column(Text)
    # Computed by `plan_kpi` from the result rows, never written by a model —
    # the headline figure is the one number a report must not get wrong.
    kpi: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OK")
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)


class ReportSectionResult(Base):
    """One section's prose, for one run.

    **Two prose columns, not one.** A regeneration writes `prose` and leaves
    `edited_prose` NULL on the *new* run; the previous run keeps both. Editing
    never destroys, regenerating never overwrites, and the user's writing is
    always recoverable from the run it belongs to.
    """

    __tablename__ = "report_section_results"
    __table_args__ = (
        Index("ix_report_section_results_run_position", "run_id", "position"),
    )

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("report_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("report_sections.id", ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heading_snapshot: Mapped[str] = mapped_column(
        String(300), nullable=False, default=""
    )
    # What the model wrote.
    prose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # What the user wrote over it. **NULL = not edited**, which is a different
    # answer from "edited to empty" and must stay distinguishable.
    edited_prose: Mapped[str | None] = mapped_column(Text)
    # Findings from the Tier 2 numeric check: figures in the prose that no
    # result row supports. NULL = the check did not run. It **flags, never
    # blocks** — a finding is a suspicion, never a verdict.
    numeric_check: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OK")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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


# ── evaluation (offline; written by app.eval.runner, never on the request path) ──
class EvalRun(Base):
    """One invocation of the eval runner over a suite: the aggregate scorecard."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _pk()
    suite: Mapped[str] = mapped_column(String(60), nullable=False)
    suite_version: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    connection_fixture: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    # No FK: an eval may run against a config that was later deleted, and we
    # still want the historical scorecard to survive.
    llm_config_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(20), default="v1")
    git_sha: Mapped[str | None] = mapped_column(String(64))
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The full SuiteReport (execution accuracy, retrieval recall, all the rest).
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvalResult(Base):
    """Per-question outcome. Candidate SQL and failure reason are kept verbatim
    so a bad run can be read afterwards without re-running it."""

    __tablename__ = "eval_results"
    __table_args__ = (
        Index("ix_eval_results_run", "eval_run_id"),
        UniqueConstraint("eval_run_id", "record_id", name="uq_eval_result_record"),
    )

    id: Mapped[uuid.UUID] = _pk()
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(60), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    difficulty: Mapped[str | None] = mapped_column(String(20))

    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(20))

    expected_tables: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    retrieved_tables: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    retrieval_recall: Mapped[float | None] = mapped_column()
    retrieval_hit: Mapped[bool | None] = mapped_column(Boolean)

    gold_sql: Mapped[str | None] = mapped_column(Text)
    candidate_sql: Mapped[str | None] = mapped_column(Text)
    gold_row_count: Mapped[int | None] = mapped_column(Integer)
    candidate_row_count: Mapped[int | None] = mapped_column(Integer)

    parse_ok: Mapped[bool | None] = mapped_column(Boolean)
    validated_ok: Mapped[bool | None] = mapped_column(Boolean)
    execution_ok: Mapped[bool | None] = mapped_column(Boolean)
    execution_match: Mapped[bool | None] = mapped_column(Boolean)
    exact_match: Mapped[bool | None] = mapped_column(Boolean)
    policy_violations: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)

    attempts: Mapped[int | None] = mapped_column(Integer)
    repair_count: Mapped[int | None] = mapped_column(Integer)
    succeeded_on_attempt: Mapped[int | None] = mapped_column(Integer)

    llm_ms: Mapped[int | None] = mapped_column(Integer)
    validate_ms: Mapped[int | None] = mapped_column(Integer)
    db_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column()

    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
