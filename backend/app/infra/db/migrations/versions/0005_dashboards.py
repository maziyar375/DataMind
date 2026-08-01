"""Dashboards: dashboards + dashboard_tiles + dashboard_tile_cache.

Three tables in one DDL step. The cache is not written until Phase 4, but a
second migration to add it would be a second deployment for one table nobody
had a reason to leave out.

Two column choices are load-bearing rather than incidental:

* `dashboard_tiles.connection_id` is **SET NULL, not CASCADE** — deleting a
  connection must leave a tile that says "connection removed", not silently
  delete the layout the user built.
* `chart_config` and `refresh_interval_seconds` are **nullable with no server
  default**, because NULL means something in both: "decide the chart afresh
  from each result" and "inherit the dashboard's default rate".

Like `runs.status`, every enum-shaped column is a plain `String` — adding a
tile type or a status must not need DDL.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = pg.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)
JSONB = pg.JSONB


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "owner_id", UUID,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("grid_columns", sa.Integer, nullable=False, server_default="12"),
        sa.Column("row_height_px", sa.Integer, nullable=False, server_default="60"),
        sa.Column("gap_px", sa.Integer, nullable=False, server_default="12"),
        sa.Column(
            "compact_mode", sa.String(20), nullable=False, server_default="VERTICAL"
        ),
        sa.Column("palette", sa.String(30), nullable=False, server_default="default"),
        sa.Column(
            "theme_override", sa.String(20), nullable=False, server_default="INHERIT"
        ),
        # 0 = manual only, and the default: a dashboard nobody has configured
        # must not start polling the customer's database on its own.
        sa.Column(
            "default_refresh_interval_seconds",
            sa.Integer, nullable=False, server_default="0",
        ),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "name", name="uq_dashboard_owner_name"),
    )
    op.create_index("ix_dashboards_owner_id", "dashboards", ["owner_id"])
    op.create_index(
        "ix_dashboards_owner_updated", "dashboards", ["owner_id", "updated_at"]
    )

    op.create_table(
        "dashboard_tiles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "dashboard_id", UUID,
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL: see the module docstring. A tile outliving its connection
        # is the whole point of this line.
        sa.Column(
            "connection_id", UUID,
            sa.ForeignKey("database_connections.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "llm_config_id", UUID,
            sa.ForeignKey("llm_configs.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("tile_type", sa.String(20), nullable=False, server_default="CHART"),
        sa.Column("question", sa.Text),
        sa.Column("sql", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "sql_origin", sa.String(20), nullable=False, server_default="GENERATED"
        ),
        # Nullable and no default: NULL is "Auto".
        sa.Column("chart_config", JSONB),
        sa.Column("max_rows", sa.Integer),
        # Nullable and no default: NULL is "inherit the dashboard's rate".
        sa.Column("refresh_interval_seconds", sa.Integer),
        sa.Column("grid_x", sa.Integer, nullable=False, server_default="0"),
        sa.Column("grid_y", sa.Integer, nullable=False, server_default="0"),
        sa.Column("grid_w", sa.Integer, nullable=False, server_default="4"),
        sa.Column("grid_h", sa.Integer, nullable=False, server_default="4"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_dashboard_tiles_dashboard_id", "dashboard_tiles", ["dashboard_id"]
    )
    op.create_index(
        "ix_dashboard_tiles_dashboard_position",
        "dashboard_tiles",
        ["dashboard_id", "position"],
    )

    op.create_table(
        "dashboard_tile_cache",
        sa.Column(
            "tile_id", UUID,
            sa.ForeignKey("dashboard_tiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sql_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("result", JSONB, nullable=False, server_default="{}"),
        sa.Column("row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("computed_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_message", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("dashboard_tile_cache")
    op.drop_index(
        "ix_dashboard_tiles_dashboard_position", table_name="dashboard_tiles"
    )
    op.drop_index("ix_dashboard_tiles_dashboard_id", table_name="dashboard_tiles")
    op.drop_table("dashboard_tiles")
    op.drop_index("ix_dashboards_owner_updated", table_name="dashboards")
    op.drop_index("ix_dashboards_owner_id", table_name="dashboards")
    op.drop_table("dashboards")
