"""Reports: what an exhibit is called, as opposed to what was asked for.

Two columns, one on the template and one on the run, and both hold the same
kind of text: the **declarative** label a figure is captioned with — "Revenue
by month, last twelve months" — as against the question the block was written
from — "How did revenue move month by month over the last year?".

A document captioned with its questions reads as a transcript of the session
that produced it. Reports are captioned with statements; the question is
provenance, and it keeps its column and moves to where provenance belongs (the
appendix and the query panel) rather than being thrown away.

Empty is meaningful and is the safe default: **a block with no title is
captioned with its question**, which is exactly what every row written before
this migration gets. So no backfill is needed and nothing looks different until
a title is written — by the outline prompt, which now asks for one, or by the
user in the editor.

`title_snapshot` is copied onto the result for the reason every other snapshot
column exists on that table: `block_id` is SET NULL, and a run must stay
readable — and correctly captioned — after the block it came from is deleted.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_blocks",
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
    )
    op.add_column(
        "report_block_results",
        sa.Column(
            "title_snapshot", sa.String(300), nullable=False, server_default=""
        ),
    )


def downgrade() -> None:
    op.drop_column("report_block_results", "title_snapshot")
    op.drop_column("report_blocks", "title")
