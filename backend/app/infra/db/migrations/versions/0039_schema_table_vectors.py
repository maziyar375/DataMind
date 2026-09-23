"""One vector per table, so retrieval can find a table by what it means.

Phase 2 of `docs/plans/hybrid-retrieval.md` — mvp2 **B2**. Phase 1 scored a
table's prose against the question by word overlap; this stores an embedding of
that same prose so a question that shares **no word** with it can still reach
it. "How many people stopped paying?" against a comment that says *"a
cancellation the customer chose, not a failed payment"*.

**A `double precision[]`, not a `pgvector` column, and that is a decision not
an oversight.** mvp2 §B2 proposed pgvector; `docs/plans/learning-loop.md` D3
answered the same question for the same kind of index first and shipped the
array (`knowledge_templates.embedding`, `0022`). `docker-compose.yml` runs
`postgres:16-alpine`, which does not carry the extension, and a managed
Postgres may refuse it — while the cosine is a hundred floats multiplied in
Python over a schema's worth of rows, which is not where a question's latency
goes. Two different answers to one question would be worse than either.

**No `schema_version` column, deliberately.** Staleness is *derived* from
`embedding_fingerprint`, which hashes the prose, the model id and the
dimension: a re-synced comment, an edited description and a changed model each
make the recomputed fingerprint differ, so there is nothing to invalidate and
nothing anybody can forget to invalidate. A row for a table a re-sync dropped is
simply never read again — the snapshot is the authority on what exists, the
same rule `match_by_terms` follows for a semantic-layer entry that outlived its
table.

**Nothing here is customer data.** The text embedded is a DDL comment and a
curator's description — structure and documentation, the same content the
schema block already sends under `include_db_comments`. No values, no samples,
no rows. `docs/reference/security.md` §3.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schema_table_vectors",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "connection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("qualified_name", sa.Text(), nullable=False),
        # Nullable because a row may exist having failed to embed; a null is
        # "not measured", never "the zero vector".
        sa.Column("embedding", postgresql.ARRAY(sa.Float())),
        sa.Column(
            "embedding_fingerprint", sa.String(length=64),
            nullable=False, server_default="",
        ),
        sa.Column("embedded_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "connection_id", "qualified_name",
            name="uq_schema_table_vectors_table",
        ),
    )
    op.create_index(
        "ix_schema_table_vectors_connection",
        "schema_table_vectors",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schema_table_vectors_connection", table_name="schema_table_vectors"
    )
    op.drop_table("schema_table_vectors")
