"""Grants: *"who may do what to **this** thing"*, as rows.

The table this whole plan was heading for, and the one that changes what the
product is. Everything before it was vocabulary and shape — roles said what a
principal may do *app-wide*, and `role_scoped_privileges` said what they may do
to *every* resource of a type. Neither could say *"Sara may read the Finance
warehouse and nothing else"*, and that sentence is the product.

**Six columns carry the whole model**, and each of the interesting ones is a
decision:

* **`resource_type` + `resource_id`, and no foreign key.** `resource_id` is
  polymorphic across eight types, two of which are *derived* and share their
  parent connection's id — so there is nothing single to reference. The cost is
  that deleting a resource can leave an orphaned grant; the answer is a delete
  hook in the service **plus** a sweep in `workers/reconciler.py`, and *not*
  eight nullable FK columns with a `CHECK` that exactly one is set. An orphaned
  grant is inert — it names an id no row has — so the sweep is hygiene rather
  than a correctness fix.
* **`resource_id IS NULL` means every resource of this type.** One nullable
  column is what makes *"Knowledge Manager over everything"* a row instead of
  an `if`. It is writable only by a caller holding `role.manage` — a wildcard
  is a role-shaped decision wearing a grant's clothes — and it is always
  audited under its own action, `grant.wildcard.created`, so it cannot hide
  among ordinary shares.
* **Exactly one principal, as a `CHECK`.** A grant goes to a person **or** a
  team, never both and never neither. A service user needs no third column: it
  is a `users` row (migration `0026`), which is the payoff for having kept one
  identifier space.
* **`UNIQUE NULLS NOT DISTINCT`.** Without it Postgres treats every row holding
  a NULL in the key as distinct, and this table would silently accept the same
  team grant, or the same wildcard, any number of times. Requires PG15+; this
  deployment runs postgres:16. The same reasoning `0025` applied to
  `role_assignments`.
* **No `grantor` beyond `created_by`.** Nothing walks a chain on revoke because
  no chain exists: `manage` is not implied by `modify`, so a grantee cannot
  re-grant unless somebody deliberately gave them `manage`, and revoking one
  grant never orphans another.
* **No `expires_at`, no `deny`, no priority.** An expiring grant needs a
  sweeper, a notification and a story for *"expired mid-render"*. A `deny` row
  turns *"why can Ali not see this?"* from one query into an ordering problem —
  every product that shipped one regrets it. §17.6 of the plan holds the
  triggers that would reopen either.

**Four indexes, and the fourth is the one that pays for the wildcard.** The
resource index answers *"who can reach this?"*; the two partial principal
indexes answer *"what can this principal reach?"* from either arm of the union;
and `ix_grants_wildcard` makes the short-circuit that runs **before** every
`visible` subquery a single index scan rather than a scan of the table.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Validated in the domain (`ResourceType`), stored as text — the same
        # open/closed bargain `role_capabilities.capability` makes, and for the
        # same reason: a downgrade must degrade to *fewer* permissions rather
        # than to a constraint violation on every read.
        sa.Column("resource_type", sa.String(30), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "team_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
        ),
        sa.Column("privilege", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        # SET NULL, not CASCADE: who granted access is history, and a share
        # must not disappear because the person who made it left the company.
        # That is exactly the moment somebody is reviewing the shares.
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (team_id IS NULL)",
            name="ck_grants_one_principal",
        ),
    )

    op.execute(
        "ALTER TABLE grants ADD CONSTRAINT uq_grants "
        "UNIQUE NULLS NOT DISTINCT "
        "(resource_type, resource_id, user_id, team_id, privilege)"
    )

    # "Who can reach this?" — the access panel, and the sweep's delete hook.
    op.create_index("ix_grants_resource", "grants", ["resource_type", "resource_id"])
    # The two arms of the union in `visible`, each partial so the index holds
    # only the rows that arm can match.
    op.create_index(
        "ix_grants_user", "grants", ["user_id"],
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_grants_team", "grants", ["team_id"],
        postgresql_where=sa.text("team_id IS NOT NULL"),
    )
    # Step 0 of `visible` (§18.3): before any subquery is built, ask whether a
    # wildcard already answers "everything". Partial, so it holds only wildcard
    # rows — which in a healthy installation is a handful.
    op.create_index(
        "ix_grants_wildcard", "grants", ["resource_type", "privilege"],
        postgresql_where=sa.text("resource_id IS NULL"),
    )


def downgrade() -> None:
    # Every share made through this table disappears, and that is the honest
    # rollback: a schema with no grants has no way to express them, and the
    # alternative — rewriting each into something the older code understands —
    # would invent permissions nobody granted. Ownership is untouched, so
    # everybody keeps exactly what they had before anything was shared.
    op.drop_index("ix_grants_wildcard", table_name="grants")
    op.drop_index("ix_grants_team", table_name="grants")
    op.drop_index("ix_grants_user", table_name="grants")
    op.drop_index("ix_grants_resource", table_name="grants")
    op.drop_table("grants")
