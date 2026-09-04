"""Provider-specific request parameters, and an embedding model per provider.

Four columns, no new table, and every one of them backward-compatible by
construction: a row written before this migration reads back as `{}` and `''`,
which is exactly the request the gateway built yesterday.

**Why the parameters are one JSONB column and not fourteen.** The set is
provider-defined and moves when the provider moves —
`app/domain/value_objects/llm_params.py` is the catalog, and the whole point of
keeping it as data is that adding `prompt_cache_key` is a line there rather
than a migration, a DTO field, a form field and a request-shaping branch. The
column is the storage half of that bargain. Validation is *not* deferred to
read time: `api/schemas.py` checks the map against the selected provider's own
API before it is stored, so a row cannot hold a parameter the provider will
reject.

**Why the embedding model lives on `llm_configs` and the pin stays on
`database_connections`.** They answer different questions and only look alike.
`llm_configs.embedding_model` is *configuration* — which model this endpoint
should be asked for vectors — and it is typed by a person.
`database_connections.embedding_model` / `.embedding_dimension` (migration
`0021`) are a *record of an index*: what the vectors sitting in
`knowledge_templates` were actually made with, measured from a real reply, and
comparable to nothing else. Moving the configuration onto the connection would
lose the second; moving the pin onto the config would re-mean every stored
vector the moment somebody edited a form.

`database_connections.embedding_llm_config_id` closes the third gap, which was
a real dead end rather than a missing feature: `_embedding_llm` resolved the
owner's `llm_configs.is_default` row, and **nothing in the product has ever set
`is_default`** — no route, no service, no form. Embedding search could not be
turned on by anybody. The column names the provider the store was indexed with,
so the answer is a row rather than an invisible global, and it is `SET NULL`
like every other reference to `llm_configs`: deleting a provider must not
delete a knowledge store. A NULL falls back to the old `is_default` lookup, so
a deployment that had somehow set one keeps working.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_configs",
        sa.Column(
            "params",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "llm_configs",
        sa.Column(
            "embedding_model", sa.String(200), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "llm_configs",
        sa.Column(
            "embedding_params",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "database_connections",
        sa.Column("embedding_llm_config_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_connections_embedding_llm_config",
        "database_connections",
        "llm_configs",
        ["embedding_llm_config_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_connections_embedding_llm_config",
        "database_connections",
        type_="foreignkey",
    )
    op.drop_column("database_connections", "embedding_llm_config_id")
    op.drop_column("llm_configs", "embedding_params")
    op.drop_column("llm_configs", "embedding_model")
    op.drop_column("llm_configs", "params")
