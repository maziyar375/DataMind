"""A claim, the figures it states, and the result it is drawn from.

Phase 3 of `docs/plans/deep-analysis-mode.md` §2.2. One JSONB column on
`report_section_results`, holding one row per sentence of the prose: the
sentence, the figures in it, the result it cites, that result's id, and any
figure its cited result does not support.

**§11 Q1 is answered here, and it is answered by the plan's own rule** — JSONB
if nothing ever queries across claims, a table the moment something does.
Nothing does. Claim traceability is computed per run and lives on the run's own
scorecard; a claim is only ever read together with the prose it belongs to, by
the one endpoint that returns that prose. A `report_claims` table would add a
join, a cascade and an ordering column to the single read path that already has
all three, and would buy an index no query asks for. The moment a question like
*"every unsupported claim across every report this quarter"* is asked, this
becomes a table, and that migration is a `jsonb_to_recordset` away.

Nullable with **no backfill and no default**, for `0023`'s reason: a section
written before r5 has no claims, and an empty list there would say the writer
cited nothing — which is a finding about a provider, not about a run that
predates the instruction. The two have to stay apart.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report_section_results", sa.Column("claims", postgresql.JSONB)
    )


def downgrade() -> None:
    op.drop_column("report_section_results", "claims")
