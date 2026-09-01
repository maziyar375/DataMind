"""The customer's own accuracy number.

Phase 6 of `docs/learning-loop-plan.md`. Three tables, and the first thing to
say about them is what they are **not**: `eval_runs` / `eval_results`.

MVP2 Part 5's meta-rule is explicit — the customer-facing instrument and the
frozen developer suite must stay architecturally separate *"or the two will
contaminate each other within a month"*, and sharing a table is how that
starts. The developer suite is frozen, versioned, checked into the repo and
measured against a testcontainers fixture; a benchmark set is a customer's own
questions against their own database, editable by them, and its whole purpose
is to move. One table would mean one schema serving two lifecycles, and the
first migration that suited one would break the other.

**`benchmark_sets.template_ids`** is the membership, as an array rather than a
join table: a set is a list a person curates, it is read whole every time, and
it never needs to be queried from the other side. The *roles* stay on the
templates themselves (`knowledge_templates.role`), because §1.3's rule — a
template is retrievable **or** benchmarkable, never both — has to be enforced
in the query that builds the candidate set on the ask path, and a second copy
of that fact in a join table is a second copy that can disagree.

**`benchmark_runs` carries both numbers, not one.** Genie's Evaluations tab
shows a single figure; this stores held-out and taught separately, because
accuracy on questions answered *from* a template and accuracy on questions
answered *without* one are different numbers and only the second moves for a
reason. `held_out_*` is the one worth putting in front of anyone.

**`benchmark_results.from_template`** is the observed fact behind that split —
whether *this* run was answered from the store — rather than a label assigned
before the run. A question's role says what it is allowed to be used for; only
the run itself knows what actually happened.

No LLM judge anywhere in here. `outcome` comes from
`app/knowledge/compare.py`'s deterministic comparator, which is the strongest
starting position of the five products compared in the research; Fabric fell
back to a judge and gets *true / false / unclear*.

Revision ID: 0020
Revises: 0019
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_sets",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id", PgUUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "template_ids", ARRAY(PgUUID(as_uuid=True)),
            nullable=False, server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "held_out_fraction", sa.Float, nullable=False, server_default="0.4"
        ),
        sa.Column(
            "created_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "connection_id", "name", name="uq_benchmark_sets_name"
        ),
    )
    op.create_index(
        "ix_benchmark_sets_connection_id", "benchmark_sets", ["connection_id"]
    )

    op.create_table(
        "benchmark_runs",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "set_id", PgUUID(as_uuid=True),
            sa.ForeignKey("benchmark_sets.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "connection_id", PgUUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "llm_config_id", PgUUID(as_uuid=True),
            sa.ForeignKey("llm_configs.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("prompt_version", sa.String(20), nullable=False, server_default=""),
        sa.Column(
            "model_snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # Both numbers, stored rather than derived. A history is read far more
        # often than it is written, and recomputing a sparkline by scanning
        # every result row of every past run is how a score strip becomes slow
        # enough that nobody opens it.
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scored", sa.Integer, nullable=False, server_default="0"),
        sa.Column("matched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("held_out_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("held_out_matched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("taught_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("taught_matched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_benchmark_runs_set_created", "benchmark_runs", ["set_id", "created_at"]
    )

    op.create_table(
        "benchmark_results",
        sa.Column("id", PgUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", PgUUID(as_uuid=True),
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        # SET NULL, like `knowledge_template_hits.template_id`: a template
        # archived and later deleted must not erase the record of how the store
        # scored while it existed, because that record is the evidence for
        # whether teaching helped at all.
        sa.Column(
            "template_id", PgUUID(as_uuid=True),
            sa.ForeignKey("knowledge_templates.id", ondelete="SET NULL"),
        ),
        sa.Column("question", sa.Text, nullable=False, server_default=""),
        sa.Column("gold_sql", sa.Text, nullable=False, server_default=""),
        sa.Column("candidate_sql", sa.Text, nullable=False, server_default=""),
        sa.Column("role", sa.String(20), nullable=False, server_default="HELD_OUT"),
        sa.Column("outcome", sa.String(30), nullable=False, server_default="ERROR"),
        sa.Column(
            "from_template", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("gold_row_count", sa.Integer),
        sa.Column("candidate_row_count", sa.Integer),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_benchmark_results_run_id", "benchmark_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_results_run_id", table_name="benchmark_results")
    op.drop_table("benchmark_results")
    op.drop_index("ix_benchmark_runs_set_created", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
    op.drop_index("ix_benchmark_sets_connection_id", table_name="benchmark_sets")
    op.drop_table("benchmark_sets")
