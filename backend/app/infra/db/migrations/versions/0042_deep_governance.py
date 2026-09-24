"""Deep analysis governance: a budget per connection, its snapshot per run, and
who may start one.

Phase 8 of `docs/plans/deep-analysis-mode.md`.

`database_connections.deep_budget` is what an operator allows one deep run on
that connection — five numbers, or NULL for the installation's ceiling.
`runs.deep_budget` is the same five, copied when the run is created, so a run
claimed by another replica after an edit spends what it was started under.
Both nullable JSONB with no default: NULL on a connection *means* "the
ceiling", and NULL on a run means "a quick run", which is every row before
this one.

**`deep.run` goes to Administrator and to nobody else.** The twentieth
capability, seeded by enumeration for `0030`'s reason. A deep answer costs
roughly fifteen times a chat answer and takes minutes; Metabase shipped
per-group limits around single-shot AI for less. So the seed fails closed: an
installation that turns the mode on decides who gets it by putting the word on
a role of its own, rather than discovering every Normal User already had it.
The downgrade deletes the word from every role, for `0030`'s reason.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

ROLES = ("Administrator",)

CAPABILITY = "deep.run"


def upgrade() -> None:
    op.add_column(
        "database_connections",
        sa.Column("deep_budget", postgresql.JSONB, nullable=True),
    )
    op.add_column("runs", sa.Column("deep_budget", postgresql.JSONB, nullable=True))
    for name in ROLES:
        op.execute(
            sa.text(
                "INSERT INTO role_capabilities (role_id, capability) "
                "SELECT id, :capability FROM roles WHERE name = :name "
                "ON CONFLICT DO NOTHING"
            ).bindparams(capability=CAPABILITY, name=name)
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_capabilities WHERE capability = :capability"
        ).bindparams(capability=CAPABILITY)
    )
    op.drop_column("runs", "deep_budget")
    op.drop_column("database_connections", "deep_budget")
