"""Drop `users.role`. The two-value role is gone; assignments replaced it.

`users.role` was a `varchar(20)` holding `ADMIN` or `MEMBER`, and it was the
whole of this product's authorization model until migration `0024`. Since then
it has been a **cache** — `_set_administrator` moved the real Administrator
assignment and wrote the string afterwards so the old toggle kept reading
true — and a cache of a fact nobody consults is a field that can only ever be
wrong.

It goes now, at the end of the plan, and not earlier, for a reason worth
recording: every phase between `0024` and here had a rollback that involved
turning the new model off, and a build that had dropped this column could not
have taken one. Phase 10 is where the rollbacks stop being reachable, so this
is where the column stops being kept.

**Nothing is lost.** Every account's real permissions are `role_assignments`
rows written by `0024`'s data migration, and the downgrade below reconstructs
the string from them — `ADMIN` for anybody holding Administrator, `MEMBER` for
everybody else — which is exactly what `0024` computed in the other direction.
A round trip is lossless because the string was always derivable.

Two things this migration deliberately does **not** do:

* **It does not touch `knowledge_templates.role` or any other `role` column.**
  Those are unrelated words that happen to be spelled the same — a template's
  role is `RETRIEVABLE` / `BENCHMARK_ONLY` / `HELD_OUT`, and a semantic
  entity's is a description of a table. Naming the table explicitly is the
  whole of the protection against a search-and-replace.
* **It does not drop `roles` or `role_assignments`.** This removes the legacy
  string, not the model that replaced it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "role")


def downgrade() -> None:
    """Re-add the column and reconstruct it from the assignments.

    Nullable first, then backfilled, then `NOT NULL` — the three-step every
    column with no server default needs on a table that already has rows.
    `MEMBER` is the default because it was: `0024` read `ADMIN` as the
    exception and everything else as the norm.
    """
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=20), nullable=True),
    )
    op.execute("UPDATE users SET role = 'MEMBER'")
    op.execute(
        """
        UPDATE users u SET role = 'ADMIN'
        FROM role_assignments ra
        JOIN roles r ON r.id = ra.role_id
        WHERE ra.user_id = u.id AND r.name = 'Administrator'
        """
    )
    op.alter_column("users", "role", nullable=False, server_default="MEMBER")
