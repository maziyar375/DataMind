"""Few-shot injection's rollback switch.

Phase 5 of `docs/learning-loop-plan.md`. One column, and the only interesting
thing about it is its default.

`PROMPT_VERSION` moves v8 → v9 with this phase because `GENERATE_SYSTEM` gains
an `{examples}` slot. **Off renders the empty string into that slot and the
prompt comes back byte-identical to v8**, which is what makes every recorded
baseline still hold for a connection that has this off.

**And off is the default.** This is the one change in the learning loop that
can make the product worse: eval Round 2 measured an unconditional addition to
this exact prompt costing ten points of execution accuracy on a small model
(36% → 26%) by crowding out the schema, and few-shot examples are that shape of
change. The plan's gate says ship it only if execution accuracy on **held-out**
questions is not worse than the Phase 0 baseline, at the same retrieval budget,
on the same suite. Until `docs/eval.md` §6.1 carries both numbers, the honest
default is the one that changes nothing — the switch exists, the arm exists,
and a connection owner who wants to try it can turn it on.

A revision of its own rather than a column bolted onto `0018`, because the plan
asks for one revision per phase and a squashed one loses which change a
deployment is actually taking.

Revision ID: 0019
Revises: 0018
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "database_connections",
        sa.Column(
            "knowledge_examples_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("database_connections", "knowledge_examples_enabled")
