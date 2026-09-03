"""The embedding matcher's index, and the pin that makes it reproducible.

Phase 7 of `docs/learning-loop-plan.md`. Five columns and no new table.

**No pgvector, and that is a decision rather than an omission.** The base image
is `postgres:16-alpine`, which does not carry the extension, and §3.8 buys
explicitly "no new Python dependency and no new deployment unit". A knowledge
store is a curator's worth of rows — tens, occasionally hundreds — so cosine
over a `double precision[]` in Python is the same call `trigram_similarity`
already makes for the lexical matcher: **the index narrows, the matcher
decides.** A deployment that outgrows that has outgrown a human curator, which
is a different problem than a slow query. Nothing here forecloses adding
`vector` later; the column would change type and `embed.py` would keep its
tests.

**`embedding_fingerprint` is the whole staleness rule.** §3.8 asks for two
behaviours — a template edit invalidates its vector, a model change invalidates
all of them — and both fall out of storing a hash of the three inputs the
vector was computed from (the masked question, the model id, the width) and
recomputing it on read. There is no `is_stale` flag to set and therefore none
to forget, and a third invalidation nobody wrote down is covered too: a schema
re-sync changes which words the mask replaces, so it changes the masked text,
so it changes the fingerprint.

**The pin lives on the connection, not on the LLM config.** `embedding_model`
and `embedding_dimension` describe *this store's index*, and a store indexed at
1536 stays comparable only to questions embedded at 1536 by the same model. A
user switching their default provider must not silently re-mean every stored
vector; they change the pin, every fingerprint stops matching, and the next
pass re-indexes. Empty model means the lexical matcher, which is the shipped
behaviour and not a degraded one.

The dimension is nullable-by-default `0` rather than a guess: it is measured
from a real call to the endpoint, because two gateways serving the same model
name at different widths is a thing that happens.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "database_connections",
        sa.Column(
            "embedding_model", sa.String(200), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "database_connections",
        sa.Column(
            "embedding_dimension", sa.Integer, nullable=False, server_default="0"
        ),
    )

    op.add_column(
        "knowledge_templates",
        sa.Column("embedding", sa.ARRAY(sa.Float)),
    )
    op.add_column(
        "knowledge_templates",
        sa.Column(
            "embedding_fingerprint", sa.String(64), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "knowledge_templates",
        sa.Column("embedded_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("knowledge_templates", "embedded_at")
    op.drop_column("knowledge_templates", "embedding_fingerprint")
    op.drop_column("knowledge_templates", "embedding")
    op.drop_column("database_connections", "embedding_dimension")
    op.drop_column("database_connections", "embedding_model")
