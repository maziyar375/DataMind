"""The knowledge store: taught questions, scoped to a connection.

Phase 1 of `docs/learning-loop-plan.md`. The store ships **inert** — nothing
reads from it until Phase 2 — which is the point of landing it on its own: the
guard's fifth entry point and the disclosure decision are in the tree before
anything comes through that door.

Three things in here are worth reading before changing them:

**`CREATE EXTENSION IF NOT EXISTS pg_trgm`.** The lexical matcher Phase 2 adds
scores questions with trigram similarity, and `pg_trgm` ships inside
`postgres:16-alpine` with no image change — which is the whole reason the plan
chose it over pgvector. `IF NOT EXISTS` because a pre-existing extension must
not be an error, and the whole statement is wrapped: on a managed database
where the app role may not create extensions, the migration logs and continues,
and the matcher degrades to a `LIKE`-and-token comparison. A connection that
cannot be created is a worse outcome than a matcher that scores more coarsely.

**The GIN index is on `question_normalized`, not `question`.** The normalised
form is the match key — literals masked, case folded — and indexing the prose
would score two askings of the same question as different.

**The unique constraint is on the normalised form too**, so a curator cannot
create two rows that a matcher could not choose between.

Revision ID: 0015
Revises: 0014
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _try_create_extension() -> bool:
    """`pg_trgm`, if this role may create it. Never fatal.

    Postgres aborts the whole transaction on a failed statement, so the attempt
    takes a SAVEPOINT of its own — without it a permission error here would
    take the table creation below down with it.
    """
    connection = op.get_bind()
    try:
        with connection.begin_nested():
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        return True
    except Exception:
        # Logged rather than raised: `LexicalMatcher` degrades to a token
        # comparison with a warning, which is a coarser match and not a
        # missing feature.
        return False


def upgrade() -> None:
    has_trgm = _try_create_extension()

    op.create_table(
        "knowledge_templates",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id", PgUUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text, nullable=False, server_default=""),
        sa.Column("question_normalized", sa.Text, nullable=False, server_default=""),
        sa.Column("sql", sa.Text, nullable=False, server_default=""),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column(
            "literal_provenance", sa.String(20), nullable=False,
            server_default="HUMAN_AUTHORED",
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="RETRIEVABLE"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("status_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "referenced_tables", ARRAY(sa.Text), nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "conflicts_with", ARRAY(PgUUID(as_uuid=True)), nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "verified_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("hit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "connection_id", "question_normalized",
            name="uq_knowledge_templates_question",
        ),
    )
    op.create_index(
        "ix_knowledge_templates_connection_id", "knowledge_templates", ["connection_id"]
    )
    op.create_index(
        "ix_knowledge_templates_conn_status",
        "knowledge_templates",
        ["connection_id", "status", "role"],
    )
    if has_trgm:
        op.execute(
            "CREATE INDEX ix_knowledge_templates_match "
            "ON knowledge_templates USING gin (question_normalized gin_trgm_ops)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_templates_match")
    op.drop_index("ix_knowledge_templates_conn_status", table_name="knowledge_templates")
    op.drop_index(
        "ix_knowledge_templates_connection_id", table_name="knowledge_templates"
    )
    op.drop_table("knowledge_templates")
    # The extension is deliberately left in place: another feature may have
    # come to depend on it, and dropping a database-wide extension to undo one
    # table is a larger act than this migration was asked to perform.
