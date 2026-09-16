"""Semantic layer drafts: an edit stops reaching the model the moment it is saved.

Phase 2 of `docs/plans/semantic-layer-model.md`, which numbered this `0031`
(the usage screen took `0030` and `0031`, so Phase 1 shipped as `0032`).

Until now every save was a version, and every version was what the next
question read. A generation reached every answer the moment its job ended,
unreviewed. Two changes:

* **`semantic_layers` gains a draft.** `draft_document` holds unpublished
  edits, or NULL when there are none — a NULL draft *is* the published
  document. `draft_updated_by` and `draft_updated_at` say whose it is;
  `draft_origin` collects how it came to be (the generation jobs and the
  restore that wrote into it) and is folded into the version's `origin` when
  the draft is published. **No loader reads `draft_document`**: `document`
  keeps its meaning, *what the model reads*, so none of its readers change.
* **`benchmark_runs` records what it scored.** `semantic_source` is
  `PUBLISHED` or `DRAFT`, and `semantic_revision` is the head revision a draft
  run was pinned to. Every run before this migration scored the published
  document, which is what the default says.

No backfill: nothing before this migration was a draft.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("semantic_layers", sa.Column("draft_document", postgresql.JSONB))
    # SET NULL: a draft outlives the person who started it, as a version does.
    op.add_column(
        "semantic_layers",
        sa.Column(
            "draft_updated_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "semantic_layers",
        sa.Column("draft_updated_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "semantic_layers",
        sa.Column(
            "draft_origin", postgresql.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "benchmark_runs",
        sa.Column(
            "semantic_source", sa.String(20), nullable=False, server_default="PUBLISHED"
        ),
    )
    op.add_column("benchmark_runs", sa.Column("semantic_revision", sa.Integer))


def downgrade() -> None:
    op.drop_column("benchmark_runs", "semantic_revision")
    op.drop_column("benchmark_runs", "semantic_source")
    op.drop_column("semantic_layers", "draft_origin")
    op.drop_column("semantic_layers", "draft_updated_at")
    op.drop_column("semantic_layers", "draft_updated_by")
    op.drop_column("semantic_layers", "draft_document")
