"""Connection sections: a large database divided into named parts.

Phase 1 of `docs/plans/retrieval-sections.md`. One table, one index, one unique
constraint, and nothing reads it on the ask path yet — the store, the proposal
and the screen land before the `scope` node that uses them, so the cost of a
bad proposal is a screen somebody disagrees with, not a wrong answer.

A section is a name, a description and a list of qualified table names. The
name is unique per connection **case-insensitively** (`lower(name)`), because
it is the token a model replies with and *Sales* and *sales* would be one
reply naming two sections. `tables` references nothing: the snapshot is one
JSONB document, and a name that stops resolving is drift, not a broken key.

No backfill: no connection had sections before this.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "tables", postgresql.ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("origin", sa.String(20), nullable=False, server_default="PROPOSED"),
        sa.Column("schema_version", sa.Integer),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_connection_sections_connection_id", "connection_sections", ["connection_id"]
    )
    op.create_index(
        "uq_connection_sections_name",
        "connection_sections",
        ["connection_id", sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_connection_sections_name", table_name="connection_sections")
    op.drop_index("ix_connection_sections_connection_id", table_name="connection_sections")
    op.drop_table("connection_sections")
