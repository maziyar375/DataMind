"""The hit log, and the one column that makes a Verified badge safe to show.

Phase 2 of `docs/learning-loop-plan.md`. Two things:

**`knowledge_template_hits`** — one row per run that consulted the store,
whatever the verdict. The refusals are the point: `REJECTED_UNBOUND` is how we
learn which date phrasings to teach the binder next, and `OVERRIDDEN_BY_USER`
is the only honest measure of whether the short-circuit is *trusted*. The
short-circuit threshold is tuned from that number rather than from taste.

`template_id` is `SET NULL`, not `CASCADE`: a template that was archived and
later deleted must not erase the record that it once answered questions,
because that record is the evidence for whether teaching helped at all.

**`runs.skip_templates`** — what *Generate a fresh answer instead* sets. A
reader who does not believe a match needs a way out that costs one click, and
the flag has to be durable because the run may be claimed by a different
replica than the one that created it.

Revision ID: 0016
Revises: 0015
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_template_hits",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", PgUUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "template_id", PgUUID(as_uuid=True),
            sa.ForeignKey("knowledge_templates.id", ondelete="SET NULL"),
        ),
        sa.Column("matcher", sa.String(20), nullable=False, server_default="LEXICAL"),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column(
            "bound_params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_template_hits_run_id", "knowledge_template_hits", ["run_id"]
    )
    op.create_index(
        "ix_template_hits_template",
        "knowledge_template_hits",
        ["template_id", "created_at"],
    )

    op.add_column(
        "runs",
        sa.Column(
            "skip_templates", sa.Boolean, nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "skip_templates")
    op.drop_index("ix_template_hits_template", table_name="knowledge_template_hits")
    op.drop_index(
        "ix_knowledge_template_hits_run_id", table_name="knowledge_template_hits"
    )
    op.drop_table("knowledge_template_hits")
