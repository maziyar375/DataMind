"""Metric attribution: which definitions a statement matched.

Phase 3 of `docs/plans/semantic-layer-model.md`, which numbered this `0032`.

A metric reaches the prompt as one line of text, and nothing read it afterwards.
`app/semantic/attribute.py` now reads the statement the guard validated, after
the run, and gives each metric on a table it touched `used`, `ignored` or
`unknown`. Three columns hold the answer, all nullable and none backfilled —
NULL is *not attributed*, which is the truth about every row written before:

* **`generated_queries.metric_use`** — on the attempt that was attributed:
  `{"version": 12, "verdicts": [{"metric", "entity", "verdict"}]}`. The version
  is the layer the run was written with, so a verdict can be read against the
  definition it was judged by.
* **`benchmark_results.metric_use`** — the same verdicts for one benchmark
  question, and **`benchmark_runs.metric_use`** — their counts for the run
  (`in_scope`, `used`, `ignored`, `unknown`), the metric-use rate a score is read
  beside.

Schema vocabulary and verdicts only: no values and no SQL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_queries", sa.Column("metric_use", postgresql.JSONB))
    op.add_column("benchmark_results", sa.Column("metric_use", postgresql.JSONB))
    op.add_column("benchmark_runs", sa.Column("metric_use", postgresql.JSONB))


def downgrade() -> None:
    op.drop_column("benchmark_runs", "metric_use")
    op.drop_column("benchmark_results", "metric_use")
    op.drop_column("generated_queries", "metric_use")
