"""A run's depth, and the durable ask to answer now.

Phase 6 of `docs/plans/deep-analysis-mode.md` (§2.3, which named this `0039`
before hybrid retrieval took `0039` and `0040`).

`runs.depth` is QUICK or DEEP, defaulted QUICK so every existing row is correct
without a backfill — every run before this one was a chat run.
`runs.answer_now_requested` is `cancel_requested`'s twin and read on the same
heartbeat, for the same reason: the replica holding the loop is not
necessarily the one the click arrived at. Both are inputs, not measurements,
so both are NOT NULL with a server default, unlike the nullable telemetry
columns beside them.

The plan itself is not a column. It is an artifact (`ANALYSIS`), written once
the run ends, and until then the durable `PLAN_*` / `STEP_EVIDENCE` events are
the record — which is what `GET /runs/{id}/plan` folds for a reader arriving
late.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("depth", sa.String(10), nullable=False, server_default="QUICK"),
    )
    op.add_column(
        "runs",
        sa.Column(
            "answer_now_requested", sa.Boolean, nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint("ck_runs_depth", "runs", "depth IN ('QUICK', 'DEEP')")


def downgrade() -> None:
    op.drop_constraint("ck_runs_depth", "runs", type_="check")
    op.drop_column("runs", "answer_now_requested")
    op.drop_column("runs", "depth")
