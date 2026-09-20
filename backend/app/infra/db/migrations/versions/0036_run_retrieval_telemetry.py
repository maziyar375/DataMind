"""What retrieval did, per run — and the section the asker chose.

Phase 3 of `docs/plans/retrieval-sections.md` §2.2. Four columns recording the
schema block a turn was answered from, and one recording the *Ask within…*
choice that may have decided it.

Nullable, with **no backfill**: a run that predates these columns genuinely
has no answer, and a zero would be a lie about a measurement nobody took.

`retrieval_sections` holds section *names*, not ids: deleting a section must
not rewrite the history of the runs it answered. `semantic_layer_version` on
the same table is the precedent — a run records which artifact answered it as
a value, never as a reference.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("retrieval_strategy", sa.String(30)))
    op.add_column(
        "runs", sa.Column("retrieval_sections", postgresql.ARRAY(sa.Text))
    )
    op.add_column("runs", sa.Column("retrieval_tables", sa.Integer))
    op.add_column("runs", sa.Column("retrieval_chars", sa.Integer))
    op.add_column("runs", sa.Column("scope_choice", sa.Text))


def downgrade() -> None:
    op.drop_column("runs", "scope_choice")
    op.drop_column("runs", "retrieval_chars")
    op.drop_column("runs", "retrieval_tables")
    op.drop_column("runs", "retrieval_sections")
    op.drop_column("runs", "retrieval_strategy")
