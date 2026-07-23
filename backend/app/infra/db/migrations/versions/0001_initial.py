"""Initial schema.

Revision ID: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text),
        sa.Column("role", sa.String(20), nullable=False, server_default="MEMBER"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("must_change_password", sa.Boolean, server_default=sa.false()),
        sa.Column("external_subject", sa.String(255)),
        sa.Column("last_login_at", TS),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("issued_at", TS, server_default=sa.func.now()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("rotated_from", UUID),
        sa.Column("user_agent", sa.String(400)),
        sa.Column("ip", sa.String(64)),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_token", "sessions", ["refresh_token_hash"])

    op.create_table(
        "llm_configs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(500)),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("temperature", sa.Float, nullable=False, server_default="0.2"),
        sa.Column("max_tokens", sa.Integer, nullable=False, server_default="2048"),
        sa.Column("encrypted_api_key", sa.Text),
        sa.Column("key_version", sa.Integer, server_default="1"),
        sa.Column("is_default", sa.Boolean, server_default=sa.false()),
        sa.Column("status", sa.String(20), server_default="UNTESTED"),
        sa.Column("capabilities", pg.JSONB, server_default="{}"),
        sa.Column("last_tested_at", TS),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_llm_owner_name"),
    )
    op.create_index("ix_llm_configs_owner_id", "llm_configs", ["owner_id"])

    op.create_table(
        "database_connections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("database_type", sa.String(20), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("database_name", sa.String(200), nullable=False),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("encrypted_password", sa.Text, nullable=False),
        sa.Column("key_version", sa.Integer, server_default="1"),
        sa.Column("ssl_mode", sa.String(30)),
        sa.Column("schema_allowlist", pg.ARRAY(sa.Text), server_default="{}"),
        sa.Column("max_rows", sa.Integer, server_default="1000"),
        sa.Column("statement_timeout_ms", sa.Integer, server_default="30000"),
        sa.Column("disclosure_policy", sa.String(20), server_default="SAMPLE"),
        sa.Column("is_default", sa.Boolean, server_default=sa.false()),
        sa.Column("status", sa.String(20), server_default="UNTESTED"),
        sa.Column("readonly_confirmed", sa.Boolean, server_default=sa.false()),
        sa.Column("server_version", sa.String(100)),
        sa.Column("last_tested_at", TS),
        sa.Column("last_synced_at", TS),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="uq_conn_owner_name"),
    )
    op.create_index("ix_connections_owner_id", "database_connections", ["owner_id"])

    op.create_table(
        "schema_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("connection_id", UUID,
                  sa.ForeignKey("database_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("dialect", sa.String(20), nullable=False),
        sa.Column("tables", pg.JSONB, server_default="[]"),
        sa.Column("relationships", pg.JSONB, server_default="[]"),
        sa.Column("table_count", sa.Integer, server_default="0"),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_snapshots_connection", "schema_snapshots", ["connection_id"])

    op.create_table(
        "conversations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False, server_default="New chat"),
        sa.Column("default_connection_id", UUID,
                  sa.ForeignKey("database_connections.id", ondelete="SET NULL")),
        sa.Column("default_llm_config_id", UUID,
                  sa.ForeignKey("llm_configs.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), server_default="ACTIVE"),
        sa.Column("summary", sa.Text),
        sa.Column("summary_through_message_seq", sa.Integer),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversations_owner_updated", "conversations",
                    ["owner_id", "updated_at"])

    op.create_table(
        "messages",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("conversation_id", UUID,
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_message_seq"),
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_id"])

    op.create_table(
        "runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("conversation_id", UUID,
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_message_id", UUID,
                  sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assistant_message_id", UUID,
                  sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("connection_id", UUID,
                  sa.ForeignKey("database_connections.id"), nullable=False),
        sa.Column("llm_config_id", UUID, sa.ForeignKey("llm_configs.id"), nullable=False),
        sa.Column("model_snapshot", pg.JSONB, server_default="{}"),
        sa.Column("prompt_version", sa.String(20), server_default="v1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="QUEUED"),
        sa.Column("attempt_count", sa.Integer, server_default="0"),
        sa.Column("repair_count", sa.Integer, server_default="0"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text),
        sa.Column("llm_latency_ms", sa.Integer),
        sa.Column("db_latency_ms", sa.Integer),
        sa.Column("total_latency_ms", sa.Integer),
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("worker_id", sa.String(100)),
        sa.Column("fencing_token", sa.BigInteger),
        sa.Column("heartbeat_at", TS),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_runs_conversation_created", "runs", ["conversation_id", "created_at"])
    op.create_index("ix_runs_status_heartbeat", "runs", ["status", "heartbeat_at"])

    op.create_table(
        "run_steps",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("name", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("detail", sa.Text),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("duration_ms", sa.Integer),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_step_seq"),
    )
    op.create_index("ix_run_steps_run", "run_steps", ["run_id"])

    op.create_table(
        "generated_queries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("raw_sql", sa.Text, nullable=False),
        sa.Column("rewritten_sql", sa.Text),
        sa.Column("dialect", sa.String(20), nullable=False),
        sa.Column("validation_status", sa.String(20), nullable=False),
        sa.Column("validation_report", pg.JSONB, server_default="{}"),
        sa.Column("referenced_tables", pg.ARRAY(sa.Text), server_default="{}"),
        sa.Column("referenced_columns", pg.ARRAY(sa.Text), server_default="{}"),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_gq_attempt"),
    )
    op.create_index("ix_gq_run", "generated_queries", ["run_id"])

    op.create_table(
        "query_executions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("generated_query_id", UUID,
                  sa.ForeignKey("generated_queries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("row_count", sa.Integer),
        sa.Column("truncated", sa.Boolean, server_default=sa.false()),
        sa.Column("rows_scanned_estimate", sa.BigInteger),
        sa.Column("db_error_code", sa.String(60)),
        sa.Column("db_error_message", sa.Text),
        sa.Column("result_schema", pg.JSONB, server_default="[]"),
        sa.Column("result_ref", UUID),
    )
    op.create_index("ix_qe_gq", "query_executions", ["generated_query_id"])

    op.create_table(
        "artifacts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("spec", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("storage", sa.String(20), server_default="INLINE"),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_artifacts_run_kind", "artifacts", ["run_id", "kind"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("data", pg.JSONB, server_default="{}"),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
    )
    op.create_index("ix_run_events_run", "run_events", ["run_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("actor_user_id", UUID),
        sa.Column("actor_ip", sa.String(64)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("resource_type", sa.String(60)),
        sa.Column("resource_id", UUID),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("detail", pg.JSONB, server_default="{}"),
    )
    op.create_index("ix_audit_actor_at", "audit_logs", ["actor_user_id", "at"])
    op.create_index("ix_audit_action_at", "audit_logs", ["action", "at"])


def downgrade() -> None:
    for table in [
        "audit_logs", "run_events", "artifacts", "query_executions",
        "generated_queries", "run_steps", "runs", "messages", "conversations",
        "schema_snapshots", "database_connections", "llm_configs",
        "sessions", "users",
    ]:
        op.drop_table(table)
