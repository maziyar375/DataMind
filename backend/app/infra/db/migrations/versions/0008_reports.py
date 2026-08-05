"""Reports: the template, its outline, and the runs that snapshot it.

Six tables in one DDL step, and none of them shared with dashboards — deleting
that feature would leave this one working.

Three column choices are load-bearing rather than incidental, and each is a
different answer to "what happens when the thing this points at is deleted":

* `reports.connection_id` is **SET NULL, not CASCADE** — a deleted connection
  must leave a readable report that cannot regenerate, never delete the work.
  It is also immutable after creation, but that is a 422 in the service, not a
  constraint: the database cannot tell an edit from a correction.
* The result tables' back-references — `block_id`, `section_id` — are **SET
  NULL with snapshot columns beside them** (`heading_snapshot`,
  `question_snapshot`, `sql_text`, `position`). A run must stay readable after
  the block it came from is deleted; a historical document that silently loses
  a section is not a historical document.
* `report_blocks.chart_config` and `report_section_results.numeric_check` are
  **nullable with no server default**, because NULL means something in both:
  "decide the chart afresh from each result" and "the check did not run".

`report_section_results` carries **two** prose columns on purpose. Editing must
not be destroyed by a regeneration and a regeneration must not overwrite an
edit, and two columns on the run — rather than one on the template — is what
makes both true at once.

Like `runs.status`, every enum-shaped column is a plain `String`: a new block
type, time window or run status must not need DDL.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSONB = pg.JSONB


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "owner_id", UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        # The user's request, verbatim: what the outline was proposed from.
        sa.Column("prompt", sa.Text, nullable=False, server_default=""),
        # SET NULL: see the module docstring.
        sa.Column(
            "connection_id", UUID,
            sa.ForeignKey("database_connections.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "llm_config_id", UUID,
            sa.ForeignKey("llm_configs.id", ondelete="SET NULL"),
        ),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "name", name="uq_report_owner_name"),
    )
    op.create_index("ix_reports_owner_id", "reports", ["owner_id"])
    op.create_index("ix_reports_owner_updated", "reports", ["owner_id", "updated_at"])

    op.create_table(
        "report_sections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "report_id", UUID,
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("heading", sa.String(300), nullable=False, server_default=""),
        # Prompt input, not display text.
        sa.Column("intent", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.String(30), nullable=False, server_default="NORMAL"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_report_sections_report_id", "report_sections", ["report_id"])
    op.create_index(
        "ix_report_sections_report_position",
        "report_sections",
        ["report_id", "position"],
    )

    op.create_table(
        "report_blocks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "section_id", UUID,
            sa.ForeignKey("report_sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("question", sa.Text, nullable=False, server_default=""),
        sa.Column("sql", sa.Text, nullable=False, server_default=""),
        sa.Column("sql_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "sql_origin", sa.String(20), nullable=False, server_default="GENERATED"
        ),
        sa.Column(
            "block_type", sa.String(20), nullable=False, server_default="CHART"
        ),
        # Nullable and no default: NULL is "Auto".
        sa.Column("chart_config", JSONB),
        # A label only; never substituted into a statement at runtime.
        sa.Column(
            "time_window", sa.String(30), nullable=False, server_default="none"
        ),
        sa.Column(
            "feasibility_status", sa.String(20),
            nullable=False, server_default="UNCHECKED",
        ),
        sa.Column("feasibility_reason", sa.Text),
        sa.Column("feasibility_checked_at", TS),
        # NULL = the connection's own cap applies; a value may only lower it.
        sa.Column("max_rows", sa.Integer),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_report_blocks_section_id", "report_blocks", ["section_id"])
    op.create_index(
        "ix_report_blocks_section_position",
        "report_blocks",
        ["section_id", "position"],
    )

    op.create_table(
        "report_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "report_id", UUID,
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="QUEUED"),
        sa.Column("phase", sa.String(200), nullable=False, server_default=""),
        sa.Column("progress_current", sa.Integer, nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "llm_config_id", UUID,
            sa.ForeignKey("llm_configs.id", ondelete="SET NULL"),
        ),
        sa.Column("model_snapshot", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "prompt_version", sa.String(20), nullable=False, server_default="r1"
        ),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_report_runs_report_id", "report_runs", ["report_id"])
    op.create_index(
        "ix_report_runs_report_created", "report_runs", ["report_id", "created_at"]
    )

    op.create_table(
        "report_block_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id", UUID,
            sa.ForeignKey("report_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, with the snapshot columns below standing in for what is
        # gone: a deleted block must not delete the runs that used it.
        sa.Column(
            "block_id", UUID,
            sa.ForeignKey("report_blocks.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "section_id", UUID,
            sa.ForeignKey("report_sections.id", ondelete="SET NULL"),
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "heading_snapshot", sa.String(300), nullable=False, server_default=""
        ),
        sa.Column("question_snapshot", sa.Text, nullable=False, server_default=""),
        sa.Column("sql_text", sa.Text, nullable=False, server_default=""),
        sa.Column("sql_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("columns", JSONB, nullable=False, server_default="[]"),
        sa.Column("rows", JSONB, nullable=False, server_default="[]"),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "truncated", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        # NULL = no chart, which is an ordinary outcome.
        sa.Column("vega_spec", JSONB),
        sa.Column("chart_source", sa.String(20)),
        sa.Column("chart_note", sa.Text),
        sa.Column("kpi", JSONB),
        sa.Column("computed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="OK"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text),
    )
    op.create_index("ix_report_block_results_run_id", "report_block_results", ["run_id"])
    op.create_index(
        "ix_report_block_results_run_position",
        "report_block_results",
        ["run_id", "position"],
    )

    op.create_table(
        "report_section_results",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id", UUID,
            sa.ForeignKey("report_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id", UUID,
            sa.ForeignKey("report_sections.id", ondelete="SET NULL"),
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "heading_snapshot", sa.String(300), nullable=False, server_default=""
        ),
        # What the model wrote.
        sa.Column("prose", sa.Text, nullable=False, server_default=""),
        # What the user wrote over it. NULL = not edited, which is a different
        # answer from "edited to empty".
        sa.Column("edited_prose", sa.Text),
        # NULL = the check did not run. It flags, never blocks.
        sa.Column("numeric_check", JSONB),
        sa.Column("status", sa.String(20), nullable=False, server_default="OK"),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_report_section_results_run_id", "report_section_results", ["run_id"]
    )
    op.create_index(
        "ix_report_section_results_run_position",
        "report_section_results",
        ["run_id", "position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_section_results_run_position", table_name="report_section_results"
    )
    op.drop_index(
        "ix_report_section_results_run_id", table_name="report_section_results"
    )
    op.drop_table("report_section_results")

    op.drop_index(
        "ix_report_block_results_run_position", table_name="report_block_results"
    )
    op.drop_index("ix_report_block_results_run_id", table_name="report_block_results")
    op.drop_table("report_block_results")

    op.drop_index("ix_report_runs_report_created", table_name="report_runs")
    op.drop_index("ix_report_runs_report_id", table_name="report_runs")
    op.drop_table("report_runs")

    op.drop_index("ix_report_blocks_section_position", table_name="report_blocks")
    op.drop_index("ix_report_blocks_section_id", table_name="report_blocks")
    op.drop_table("report_blocks")

    op.drop_index("ix_report_sections_report_position", table_name="report_sections")
    op.drop_index("ix_report_sections_report_id", table_name="report_sections")
    op.drop_table("report_sections")

    op.drop_index("ix_reports_owner_updated", table_name="reports")
    op.drop_index("ix_reports_owner_id", table_name="reports")
    op.drop_table("reports")
