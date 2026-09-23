"""Which signal chose each table a run was answered from.

Phase 3 of `docs/plans/hybrid-retrieval.md`. `0036` recorded *what* retrieval
did — the strategy, the sections, the table count, the rendered size. This
records **why each table was there**: `{"name": 3, "term": 1, "fk": 4, "prose":
2, "vector": 1}`, counted over the tables that survived the budget cut, by the
same function that ranked them.

It is the instrument mvp2 **A5** and **B2** owe. Both features change which
tables reach the model on one branch, both are inert on a connection without a
semantic layer or DDL comments, and until this column existed the only way to
ask *"is a curator's word, or a DBA's sentence, actually deciding what the
model sees?"* was an eval arm over fifty frozen questions. This answers it over
the questions a customer really asked.

**Written only on `RANKED_MATCH`**, and NULL everywhere else — which is the
point rather than a gap. `FULL_SNAPSHOT` sends every table, `SECTION_SNAPSHOT`
sends the section, `SCHEMA_QUESTION` spends the budget by `select_tables`' own
rule: none of them *chose* anything, and `{}` there would read as "chose
nothing" rather than "did not choose". The same NULL-vs-zero rule as `0023`,
`0036` and `0037`, for the third time, because it is the one this schema keeps
having to make.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("retrieval_signals", postgresql.JSONB))


def downgrade() -> None:
    op.drop_column("runs", "retrieval_signals")
