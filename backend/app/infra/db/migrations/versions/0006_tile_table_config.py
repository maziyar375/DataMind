"""Tiles: how a TABLE tile is drawn.

One nullable JSONB column. **NULL means "as the query returned it"** — every
column, in query order, no sort applied — the same way a NULL `chart_config`
means "decide the chart afresh from each result". So no server default, and no
backfill: a tile written before this migration is already correct.

The column is deliberately *not* part of `result_fingerprint`. It changes how
the browser draws rows it already has, never what the query returns, so an edit
to a column label must not invalidate a cached result and re-run a query
against the customer's database.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboard_tiles",
        sa.Column("table_config", pg.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dashboard_tiles", "table_config")
