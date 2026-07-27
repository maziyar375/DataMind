"""Semantic layer: semantic_layers + semantic_jobs, and the connection switch.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSONB = pg.JSONB
TEXT_ARRAY = pg.ARRAY(sa.Text())


def upgrade() -> None:
    # Existing connections default to on: a connection with no layer renders
    # no block at all, so the switch only starts mattering once a user
    # generates one, and defaulting to off would hide the feature they just
    # built.
    op.add_column(
        "database_connections",
        sa.Column(
            "semantic_layer_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.create_table(
        "semantic_layers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "connection_id", UUID,
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("document", JSONB, nullable=False, server_default="{}"),
        sa.Column("entity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metric_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reviewed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("issue_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "generated_by_llm_config_id", UUID,
            sa.ForeignKey("llm_configs.id", ondelete="SET NULL"),
        ),
        sa.Column("model_snapshot", JSONB, server_default="{}"),
        sa.Column("prompt_version", sa.String(20), server_default="s1"),
        sa.Column("generated_at", TS),
        sa.Column("edited_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", name="uq_semantic_layers_connection"),
    )
    op.create_index(
        "ix_semantic_layers_connection_id", "semantic_layers", ["connection_id"]
    )

    op.create_table(
        "semantic_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "connection_id", UUID,
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "llm_config_id", UUID,
            sa.ForeignKey("llm_configs.id", ondelete="SET NULL"),
        ),
        sa.Column("model_snapshot", JSONB, server_default="{}"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="MERGE"),
        sa.Column("only_tables", TEXT_ARRAY, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("phase", sa.String(200), nullable=False, server_default=""),
        sa.Column("progress_current", sa.Integer, nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stats", JSONB, server_default="{}"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_semantic_jobs_connection_id", "semantic_jobs", ["connection_id"])
    op.create_index(
        "ix_semantic_jobs_connection_created",
        "semantic_jobs",
        ["connection_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_jobs_connection_created", table_name="semantic_jobs")
    op.drop_index("ix_semantic_jobs_connection_id", table_name="semantic_jobs")
    op.drop_table("semantic_jobs")
    op.drop_index("ix_semantic_layers_connection_id", table_name="semantic_layers")
    op.drop_table("semantic_layers")
    op.drop_column("database_connections", "semantic_layer_enabled")
