"""A second kind of principal, in the table that already holds the first.

**A service user is a row in `users` with `kind='SERVICE'`.** Not a second
table, and the reason is arithmetic rather than taste: `dashboards.owner_id`,
`reports.owner_id`, `conversations.owner_id`, `runs.owner_id`,
`audit_logs.actor_user_id`, `role_assignments.user_id`, `team_members.user_id`
and the `grants` table Phase 6 adds all point at `users.id`. A separate
`service_users` table makes every one of them polymorphic — two nullable
columns and a `CHECK` on eleven tables — to buy a distinction one `varchar(20)`
already draws.

Three things fall out of the one identifier space, and each is a requirement
rather than a convenience:

* **An agent can own what it builds.** Ownership *is* `owner_id`, so a nightly
  agent that creates a dashboard owns it, and the transfer path Phase 6 adds
  works on it unchanged.
* **"Who did this?" stays one join.** The audit log answers for humans and
  machines with the same query, which is the only version of that question
  anybody can actually run during an incident.
* **A service user joins teams and holds roles** through the tables that
  already exist. Phase 3's capability resolver and Phase 4's team resolver need
  not learn a second principal shape — and a test in this phase asserts a
  service user's capability set is byte-identical to a human's with the same
  role, because that equality is the whole design.

**The three `CHECK`s are database constraints, not conventions.** A service
user has no password, no external subject and nothing to change at next
sign-in; the alternative — a service-layer rule — is one `db.add` away from
being bypassed, and the row it would write is an interactive login for a
machine identity.

**`email` stays `NOT NULL UNIQUE`, and a service user gets a synthetic,
non-routable address** — `svc-<slug>@service.datamind.local`. Generated, never
typed. Every existing join, uniqueness check and display path keeps working
without learning that the column is now sometimes meaningless, and the `.local`
suffix means a misconfigured mailer cannot deliver anywhere real. The UI shows
the display name.

`users.description` is `NULL` for a person and required for a machine, and the
requirement is enforced in the service rather than here: *"what is this for"*
is the field that decides whether anybody ever dares delete an undocumented
integration, and a `CHECK` on it would refuse the humans who legitimately have
none.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "kind", sa.String(20), nullable=False, server_default="HUMAN"
        ),
    )
    op.add_column("users", sa.Column("description", sa.Text))

    # Closed in code (`PrincipalKind` is a `StrEnum`), closed in the row too —
    # unlike `role_capabilities.capability`, which is deliberately open. The
    # difference is that a capability this build does not know can be *ignored*
    # and the installation stays up with fewer permissions; a principal kind it
    # does not know has no safe interpretation at all.
    op.create_check_constraint(
        "ck_users_kind", "users", "kind IN ('HUMAN', 'SERVICE')"
    )
    op.create_check_constraint(
        "ck_users_service_no_password",
        "users",
        "kind <> 'SERVICE' OR password_hash IS NULL",
    )
    op.create_check_constraint(
        "ck_users_service_no_external_subject",
        "users",
        "kind <> 'SERVICE' OR external_subject IS NULL",
    )
    op.create_check_constraint(
        "ck_users_service_no_password_change",
        "users",
        "kind <> 'SERVICE' OR must_change_password = false",
    )

    # Every People screen filters humans out of the machines and back again,
    # and `kind` is two-valued — so this index earns its keep on the *smaller*
    # side: "list the service accounts" in an installation with four of them
    # and four hundred people.
    op.create_index("ix_users_kind", "users", ["kind"])


def downgrade() -> None:
    # Service users are deleted rather than demoted. A row that survived the
    # downgrade would be an account with no password and no way to sign in,
    # indistinguishable from an invited human whose invitation was lost — and
    # `ON DELETE CASCADE` on `service_credentials` takes their keys with them,
    # which is the correct direction for a credential whose principal is gone.
    op.execute("DELETE FROM users WHERE kind = 'SERVICE'")

    op.drop_index("ix_users_kind", table_name="users")
    op.drop_constraint("ck_users_service_no_password_change", "users", type_="check")
    op.drop_constraint(
        "ck_users_service_no_external_subject", "users", type_="check"
    )
    op.drop_constraint("ck_users_service_no_password", "users", type_="check")
    op.drop_constraint("ck_users_kind", "users", type_="check")
    op.drop_column("users", "description")
    op.drop_column("users", "kind")
