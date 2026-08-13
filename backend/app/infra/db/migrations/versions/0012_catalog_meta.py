"""Catalog metadata that has nowhere else to live.

Phase 1 taught the four connectors to read the descriptions a DBA already
wrote — `COMMENT ON`, MySQL's `COLUMN_COMMENT`, an `MS_Description` extended
property, Oracle's `ALL_*_COMMENTS` — and then had nowhere to put half of them.
Table and column comments ride inside `schema_snapshots.tables`, which is
already JSONB and needed no migration, so those have been persisting since
Phase 1 landed. The **database** and **schema** comments had no home and were
dropped on the floor at the end of every sync. This is their home.

One nullable-free JSONB column defaulting to `'{}'`, so every snapshot written
before this migration reads back as "nothing captured" rather than as NULL that
each consumer has to remember to guard:

    {
      "database_comment": "Order-to-cash for the EU storefront.",
      "schema_comments": {"sales": "Curated marts, rebuilt nightly."},
      "counts": {"tables": 12, "columns": 143}
    }

`counts` is denormalised on purpose. It is what lets the sync response and the
schema browser say "picked up 143 column descriptions" without walking the
whole `tables` document, and that sentence is the single most useful
confirmation a user can get that their DBA's work is actually being used.

**Named `catalog_meta`, never `metadata`** — `metadata` is taken on a
SQLAlchemy declarative class and the model would not import.

No backfill. The comments live on the server, not in our rows, so the honest
way to populate this for an existing connection is to re-sync it; inventing
counts for a snapshot we never read comments for would be a lie in a column
whose whole purpose is to report what we found.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schema_snapshots",
        sa.Column(
            "catalog_meta", pg.JSONB(), nullable=False, server_default="{}"
        ),
    )


def downgrade() -> None:
    op.drop_column("schema_snapshots", "catalog_meta")
