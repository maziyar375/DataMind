"""Evaluation tables: eval_runs + eval_results.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSONB = pg.JSONB
TEXT_ARRAY = pg.ARRAY(sa.Text())


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("suite", sa.String(60), nullable=False),
        sa.Column("suite_version", sa.String(20), nullable=False, server_default=""),
        sa.Column("connection_fixture", sa.String(60), nullable=False, server_default=""),
        sa.Column("llm_config_id", UUID),
        sa.Column("model_snapshot", JSONB, server_default="{}"),
        sa.Column("prompt_version", sa.String(20), server_default="v1"),
        sa.Column("git_sha", sa.String(64)),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metrics", JSONB, server_default="{}"),
        sa.Column("notes", sa.Text),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "eval_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "eval_run_id", UUID,
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("record_id", sa.String(60), nullable=False),
        sa.Column("question", sa.Text, nullable=False, server_default=""),
        sa.Column("tags", TEXT_ARRAY, server_default="{}"),
        sa.Column("difficulty", sa.String(20)),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("intent", sa.String(20)),
        sa.Column("expected_tables", TEXT_ARRAY, server_default="{}"),
        sa.Column("retrieved_tables", TEXT_ARRAY, server_default="{}"),
        sa.Column("retrieval_recall", sa.Float),
        sa.Column("retrieval_hit", sa.Boolean),
        sa.Column("gold_sql", sa.Text),
        sa.Column("candidate_sql", sa.Text),
        sa.Column("gold_row_count", sa.Integer),
        sa.Column("candidate_row_count", sa.Integer),
        sa.Column("parse_ok", sa.Boolean),
        sa.Column("validated_ok", sa.Boolean),
        sa.Column("execution_ok", sa.Boolean),
        sa.Column("execution_match", sa.Boolean),
        sa.Column("exact_match", sa.Boolean),
        sa.Column("policy_violations", TEXT_ARRAY, server_default="{}"),
        sa.Column("attempts", sa.Integer),
        sa.Column("repair_count", sa.Integer),
        sa.Column("succeeded_on_attempt", sa.Integer),
        sa.Column("llm_ms", sa.Integer),
        sa.Column("validate_ms", sa.Integer),
        sa.Column("db_ms", sa.Integer),
        sa.Column("total_ms", sa.Integer),
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("cost_usd", sa.Float),
        sa.Column("failure_reason", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("eval_run_id", "record_id", name="uq_eval_result_record"),
    )
    op.create_index("ix_eval_results_run", "eval_results", ["eval_run_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_results_run", table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
