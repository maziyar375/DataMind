"""Clarification: the per-connection switch for the `clarify` node.

Nothing else is needed. `runs.status` and `artifacts.kind` are plain string
columns, not native enums, so `NEEDS_CLARIFICATION` and `CLARIFICATION` — both
already declared in `domain/value_objects` — need no DDL to start being used.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On by default, mirroring `semantic_layer_enabled`: a question that is
    # unambiguous never reaches the model, so the cost of leaving it on for an
    # existing connection is nothing, while defaulting to off would hide the
    # feature behind a setting nobody knows to look for.
    op.add_column(
        "database_connections",
        sa.Column(
            "clarify_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("database_connections", "clarify_enabled")
