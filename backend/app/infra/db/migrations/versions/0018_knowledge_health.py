"""Store health: the conflict's evidence, and the switch that turns it off.

Phase 4 of `docs/learning-loop-plan.md`. Two columns and no new table, because
staleness and conflict are *states a template is in* and the status column
already exists to hold them.

**`knowledge_templates.conflict_evidence`** — the rows that prove two templates
disagree. Fabric detects conflicts by reasoning over SQL text and reports a
confidence of 1–5; DataMind runs both statements and compares the result sets,
so a conflict here is a fact rather than an opinion. Storing the diverging rows
is what makes it *actionable*: §4.7's pane shows the two answers side by side,
and a conflict the curator cannot see the evidence for is one more warning
nobody acts on. It is written by the worker and read by the detail pane;
nothing on the ask path touches it.

**`database_connections.conflict_checks_enabled`** — the per-connection off
switch the plan's own risk register demands. The checker executes SQL on the
customer's database on a schedule, and a customer who does not want that must
be able to say so without giving up the staleness sweep, which makes no
database call at all. Default true: the checks inherit the connection's
read-only credentials, its row cap and its disclosure policy, and a store
quietly rotting is the failure this phase exists to prevent.

Revision ID: 0018
Revises: 0017
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_templates",
        sa.Column(
            "conflict_evidence",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "knowledge_templates",
        sa.Column("last_conflict_check_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "database_connections",
        sa.Column(
            "conflict_checks_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("database_connections", "conflict_checks_enabled")
    op.drop_column("knowledge_templates", "last_conflict_check_at")
    op.drop_column("knowledge_templates", "conflict_evidence")
