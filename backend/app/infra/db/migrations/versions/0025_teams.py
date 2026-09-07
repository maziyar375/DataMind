"""Teams: a named set of principals, and the widening of `role_assignments`.

The research this plan rests on is unanimous on one point, and it is the reason
teams ship *before* the first grant exists rather than after: **per-user grants
do not survive contact with staff turnover.** A workspace where every share was
made to a person accumulates permissions nobody can attribute and nobody dares
revoke — the reviewer sees forty rows, forty names, and no way to tell which
five of them describe a job that still exists. Shipping teams first means the
first grant anybody makes can already be made to a team.

**`teams.(provider_id, source_id)` is the highest-value pair of columns in this
schema, and nothing reads it yet.** `(NULL, NULL)` is a DataMind-managed team;
`('oidc', '/analytics')` is one mirroring an external group. Binding an existing
team to an IdP group is then **two column updates and zero permission changes**
— rather than the migration where somebody discovers that every grant, every
role assignment and every audit row points at an identifier with no namespace
in it. Lakekeeper calls this a `RoleSourceSystem`; the cost of adding it now is
two nullable columns, and the cost of adding it later is a rewrite.

**Teams are flat, and that is a finding rather than a shortcut.** Keycloak,
Entra and Okta all emit membership as a *flat list of paths*
(`["/analytics", "/analytics/finance"]`), so the hierarchy is already flattened
by the issuer before DataMind sees it. A local nesting would be a second,
contradicting hierarchy over the same names. §24 of the plan holds the trigger
that would reopen it, and the change is one `WITH RECURSIVE`.

**Deleting a team that holds anything is refused, naming what it holds.**
Metabase's *"reassigned to All Users"* is the anti-pattern this avoids: a silent
widening at the exact moment somebody was trying to narrow.

**The second half of this revision is the widening `0024` could not do.**
Roles shipped a phase earlier, and a column with a foreign key to a table that
does not exist yet is not a column — so `role_assignments` arrived with a
non-null `user_id` and a two-column uniqueness. Here it gains `team_id`, loses
the non-null on `user_id`, gains the one-principal `CHECK`, and its unique
constraint becomes the three-column form `0024` already named it for. Existing
rows are untouched: every one of them is a user assignment, which is exactly
what the `CHECK` requires.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        # The external group this mirrors, when it mirrors one. Both columns or
        # neither — the same contract `roles` carries, deliberately identical so
        # an OIDC adapter learns one shape rather than two.
        sa.Column("provider_id", sa.String(50)),
        sa.Column("source_id", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_teams_name"),
        sa.UniqueConstraint("provider_id", "source_id", name="uq_teams_source"),
        sa.CheckConstraint(
            "(provider_id IS NULL) = (source_id IS NULL)",
            name="ck_teams_source_pair",
        ),
    )

    # Meaningful only for DataMind-managed teams: when a team is
    # provider-managed, membership comes from the token on each login and this
    # table stays empty.
    op.create_table(
        "team_members",
        sa.Column(
            "team_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "added_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    # Indexed because the question asked on **every request** is "which teams is
    # this principal in", which reads by `user_id` across the whole table.
    op.create_index("ix_team_members_user", "team_members", ["user_id"])

    _widen_role_assignments()


def _widen_role_assignments() -> None:
    """`role_assignments` learns to point at a team as well as a person.

    Order matters and is not arbitrary: the column and the constraint have to
    exist before the old uniqueness is dropped, or there is a window in which
    two identical assignments could be written. Inside one migration that
    window is theoretical — Alembic runs this in a transaction and this
    deployment's Postgres does transactional DDL — but the order costs nothing
    and the note is cheaper than rediscovering why it mattered somewhere it
    isn't.
    """
    op.add_column(
        "role_assignments",
        sa.Column("team_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_role_assignments_team", "role_assignments", "teams",
        ["team_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("role_assignments", "user_id", nullable=True)

    op.drop_constraint("uq_role_assignment", "role_assignments", type_="unique")
    # `NULLS NOT DISTINCT` is the point rather than a flourish: without it
    # Postgres treats every row with a NULL in the key as unique, and this table
    # would silently accept a team assignment twice over. Requires PG15+; this
    # deployment runs postgres:16.
    op.execute(
        "ALTER TABLE role_assignments ADD CONSTRAINT uq_role_assignment "
        "UNIQUE NULLS NOT DISTINCT (role_id, user_id, team_id)"
    )
    op.create_check_constraint(
        "ck_role_assignment_one_principal",
        "role_assignments",
        "(user_id IS NULL) <> (team_id IS NULL)",
    )
    op.create_index(
        "ix_role_assignments_team", "role_assignments", ["team_id"],
        postgresql_where=sa.text("team_id IS NOT NULL"),
    )


def downgrade() -> None:
    # A team assignment cannot survive a downgrade to a schema with no teams,
    # and dropping the rows is the honest answer: the alternative — rewriting
    # each one into an assignment for every member — invents permissions
    # nobody granted, which is the opposite of what a rollback is for.
    op.execute("DELETE FROM role_assignments WHERE team_id IS NOT NULL")

    op.drop_index("ix_role_assignments_team", table_name="role_assignments")
    op.drop_constraint(
        "ck_role_assignment_one_principal", "role_assignments", type_="check"
    )
    op.drop_constraint("uq_role_assignment", "role_assignments", type_="unique")
    op.create_unique_constraint(
        "uq_role_assignment", "role_assignments", ["role_id", "user_id"]
    )
    op.alter_column("role_assignments", "user_id", nullable=False)
    op.drop_constraint(
        "fk_role_assignments_team", "role_assignments", type_="foreignkey"
    )
    op.drop_column("role_assignments", "team_id")

    op.drop_index("ix_team_members_user", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
