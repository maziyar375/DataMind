"""Feedback on an answer, and what became of it.

Phase 3 of `docs/learning-loop-plan.md`. The hardest part of curation is not
writing a template — it is knowing *which* template to write, and the system
already knows: it is sitting in `runs`. This table is the other half of that,
the part only a person can supply.

Three verdicts rather than two, because "this is wrong" and "please look at
this" are different asks and collapsing them loses the second.

**`became_template` is the loop closing.** One nullable FK, and it is what
lets the product tell the person who flagged an answer what happened to their
flag. A feedback control with no visible payoff is worse than none: people
learn their thumbs-down goes nowhere and stop pressing it.

`UNIQUE (run_id, user_id)` — one verdict per person per answer; a second press
is a change of mind, not a second vote.

Revision ID: 0017
Revises: 0016
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_feedback",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", PgUUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "connection_id", PgUUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "user_id", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column(
            "resolved_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "became_template", PgUUID(as_uuid=True),
            sa.ForeignKey("knowledge_templates.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("run_id", "user_id", name="uq_answer_feedback_run_user"),
    )
    op.create_index("ix_answer_feedback_run_id", "answer_feedback", ["run_id"])
    op.create_index(
        "ix_answer_feedback_connection_state",
        "answer_feedback",
        ["connection_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_answer_feedback_connection_state", table_name="answer_feedback")
    op.drop_index("ix_answer_feedback_run_id", table_name="answer_feedback")
    op.drop_table("answer_feedback")
