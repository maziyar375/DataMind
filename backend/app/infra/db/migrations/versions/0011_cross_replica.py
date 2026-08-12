"""Cross-replica execution: a durable cancel, and an owner for a report run.

Four columns, all of which exist because a fact that lived in one process's
memory has to survive being asked about from another process.

`runs.cancel_requested` and `report_runs.cancel_requested` replace the reach
into a local dict. Cancelling used to mean `task.cancel()` on an
`asyncio.Task` the API handler happened to be holding, which works exactly as
long as the handler and the run are in the same process. The flag is the same
request written where every replica can read it; the executing process
notices on its next heartbeat and stops itself. Both default to `false`, so
every row that already exists means "nobody has asked", which is true.

`report_runs.worker_id` and `report_runs.heartbeat_at` are what `runs` has
had since 0001, and for the reason `runs` has them: without a heartbeat there
is no way to tell a run that is being generated right now from one whose
process died mid-sentence. Startup resume (Phase 4) could not tell the
difference and would resume both — a second replica booting mid-generation
would start writing the same sections a live replica was already writing.
Nullable, because a run that finished before this migration has no owner and
needs none: `finished_at` already says it is over.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("runs", "report_runs"):
        op.add_column(
            table,
            sa.Column(
                "cancel_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    op.add_column("report_runs", sa.Column("worker_id", sa.String(100), nullable=True))
    op.add_column(
        "report_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The index `runs` already has, for the query that already exists there:
    # "which runs look abandoned?" reads status and heartbeat and nothing else.
    op.create_index(
        "ix_report_runs_status_heartbeat", "report_runs", ["status", "heartbeat_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_report_runs_status_heartbeat", table_name="report_runs")
    op.drop_column("report_runs", "heartbeat_at")
    op.drop_column("report_runs", "worker_id")
    for table in ("runs", "report_runs"):
        op.drop_column(table, "cancel_requested")
