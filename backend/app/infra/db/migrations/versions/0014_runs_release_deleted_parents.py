"""Let a connection or an LLM config be deleted once a run has used it.

`runs` was the one table whose `connection_id` and `llm_config_id` were
`NOT NULL` with no `ON DELETE` rule, so Postgres refused the parent delete
outright. Every sibling already had the answer: `conversations`,
`dashboard_tiles`, `reports`, `report_runs`, `semantic_jobs` and
`semantic_layers` all `SET NULL` on both, and `dashboard_tiles` says why in the
model — show "connection removed", do not silently delete the thing the user
built. A run is the record of a question that was asked and answered; deleting
it to make the parent deletable would destroy the transcript to satisfy a
constraint.

So both columns become nullable and `SET NULL`, and a past run keeps its SQL,
its results and its `model_snapshot` — which already carries the connection and
model *names*, so the answer stays explainable after the connection is gone.

`runs.owner_id` is deliberately **not** touched, here or elsewhere. It is
denormalised for ownership scoping on the hot path, and a row whose owner is
NULL is a row no ownership filter matches — the wrong shape for a security
check. Deleting a user who has history is a separate question with a different
answer.

Revision ID: 0014
Revises: 0013
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# (constraint, column, referenced table)
_FKS = [
    ("runs_connection_id_fkey", "connection_id", "database_connections"),
    ("runs_llm_config_id_fkey", "llm_config_id", "llm_configs"),
]


def upgrade() -> None:
    for name, column, target in _FKS:
        op.alter_column("runs", column, existing_type=sa.Uuid(), nullable=True)
        op.drop_constraint(name, "runs", type_="foreignkey")
        op.create_foreign_key(name, "runs", target, [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    # Reversible only where no parent has actually been deleted since: a NULL
    # cannot be restored to a row that no longer exists. Rows orphaned by this
    # migration's whole point are removed rather than blocking the downgrade
    # with a NOT NULL violation, which is the honest trade — going back to a
    # schema that cannot represent them means they cannot come along.
    op.execute("DELETE FROM runs WHERE connection_id IS NULL OR llm_config_id IS NULL")
    for name, column, target in _FKS:
        op.drop_constraint(name, "runs", type_="foreignkey")
        op.create_foreign_key(name, "runs", target, [column], ["id"])
        op.alter_column("runs", column, existing_type=sa.Uuid(), nullable=False)
