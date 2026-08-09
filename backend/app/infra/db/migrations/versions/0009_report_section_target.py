"""Reports: how many sections the model is asked for.

One column, and a change of meaning for one that already exists.

`section_target` is the number the outline prompt is written around. It
replaces the "between 4 and 7 sections" the prompt used to assert on the
user's behalf, and it is deliberately *not* the number of sections a report
has: the executive summary is added on top of it, and the user adds and
removes sections after the proposal like any other edit.

`reports.language` keeps its column and loses its control. It is now derived
from the request text at creation and re-derived when the request is rewritten
(`app/reports/language.py`), so nothing here changes shape — existing rows are
already `fa` or `en` and stay exactly as their documents were written.

The server default is what makes this migration safe on a live table: every
report that existed before this column answers 5, which is the middle of the
range the old prompt asked for.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "section_target",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "section_target")
