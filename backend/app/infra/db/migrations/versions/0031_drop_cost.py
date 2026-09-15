"""Drop `cost_usd` from every table that carried it.

DataMind no longer prices model calls. The figure was a best-effort lookup in
litellm's price map, and for every OpenAI-compatible or self-hosted provider —
which is most of what this product is pointed at — the lookup found nothing and
wrote NULL. A column that is null on nearly every row, and that a screen then
has to explain, costs more attention than it returns. Token counts are the
measurement; they stay.

`runs`, `report_runs` and `semantic_jobs` got the column in `0023`;
`eval_results` has had it since `0002`.

The downgrade puts the columns back **empty**. Whatever they held is gone, and
that is acceptable only because a price was always a recomputable estimate
rather than a record — the token counts it was derived from are untouched.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

TABLES = ("runs", "report_runs", "semantic_jobs", "eval_results")


def upgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "cost_usd")


def downgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("cost_usd", sa.Float))
