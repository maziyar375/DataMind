"""The per-connection switch for catalog comments reaching the model.

Nothing else is needed. The comments themselves have been captured since 0012 —
table and column comments inside `schema_snapshots.tables`, database and schema
comments in `schema_snapshots.catalog_meta` — so this column decides only
whether the schema block renders them.

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On by default, mirroring `semantic_layer_enabled` and `clarify_enabled`.
    # A comment is DDL a human wrote about the schema, sent alongside the column
    # names it describes, so the cost of leaving it on for an existing
    # connection is a few hundred characters of documentation the model did not
    # have yesterday — while defaulting to off would hide the whole feature
    # behind a setting nobody knows to look for. A shop that does keep secrets
    # or ticket numbers in its comments gets one checkbox instead of an
    # argument, and off is byte-identical to the pre-feature prompt.
    op.add_column(
        "database_connections",
        sa.Column(
            "include_db_comments",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("database_connections", "include_db_comments")
