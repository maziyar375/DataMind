"""Roles as rows: capabilities, wildcard privileges, and who holds them.

`users.role` is a `varchar(20)` holding `ADMIN` or `MEMBER`, and every
permission question in the product has been answered by comparing it to the
string `"ADMIN"`. That answers exactly one question — *is this person an
administrator* — and requirement 2 asks a different one:

> extensive permissions over Knowledge resources **without** Admin access to
> the entire application.

There is no value of a two-member enum that means that. This revision replaces
the enum with four tables and eight seeded roles, of which **Knowledge Manager
is the load-bearing example**: one `role_capabilities` row and one
`role_scoped_privileges` row, and the holder can curate every store in the
installation while being unable to edit a credential, change a disclosure
policy, or read a row of anybody's data.

**Four decisions worth defending, because each has an obvious wrong version.**

1. **Capabilities are `varchar`, not a Postgres enum.** They are a closed
   `StrEnum` in `app/domain/value_objects/authz.py` — a typo is an
   `AttributeError` at import — and an open `varchar` in the row. The asymmetry
   is deliberate: a downgrade must not lock everybody out of the installation,
   so a row naming a word this deployment's code does not know is *ignored with
   a warning* rather than raising. An enum type would make the same row a
   constraint violation on insert, which turns a rollback into an outage.
2. **A role may never name one resource id.** `role_scoped_privileges` has a
   `resource_type` and nowhere to put an id. *"Ali can reach this because of
   the Knowledge Manager role"* and *"Ali can reach this because Sara granted
   it on 3 March"* are different sentences, and an access review can only
   answer them separately if they are separate tables. `grants` (Phase 6) is
   where an id goes.
3. **`role_assignments.role_id` is `ON DELETE RESTRICT`.** Deleting a role
   people hold fails loudly and names them, the way `_guard_last_admin` already
   refuses to strand a workspace with no administrator. Metabase's *"reassigned
   to All Users"* is the anti-pattern: a silent widening at the moment somebody
   was trying to narrow.
4. **The seed is written once and never re-synchronised.** `is_system` marks
   the eight; their capability sets change only through a migration. Superset
   re-runs its role sync on every `superset init` and quietly reverts local
   edits, which is why its permission model is the cautionary tale in the plan
   rather than the model. A system role's **name and description are editable**
   — "Normal User" is a bad name in some installations — and it cannot be
   deleted.

**`users.role` stays**, written by `role_service` as a read-only cache, so a
rollback is a config flip rather than a migration. It is dropped once the gate
proves nothing reads it (Phase 10).

**`role_assignments.team_id` is absent here and arrives with `teams`.** A
column with a foreign key to a table that does not exist yet is not a column;
§17.3 of the plan shows the finished shape, and this revision ships the half
that can stand on its own. `0025` widens `user_id` to nullable, adds `team_id`,
and swaps the uniqueness for the three-column form — which is why the unique
constraint below is named for what it becomes rather than for what it is today.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


#: The eight system roles, exactly as
#: `docs/plans/user-management-and-access-control.md` §12.3 specifies them.
#: **This is the specification, written out.** A table test asserts the same
#: eight sets from an independent literal, because a test that derived its
#: expectation from this tuple would agree with any mistake in it.
#:
#: Administrator holds all eighteen **by enumeration** rather than by a
#: wildcard, so a nineteenth capability is a deliberate line in a reviewed diff
#: instead of something Administrator silently acquires.
SEED: tuple[tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...]], ...] = (
    (
        "Administrator",
        "Manages people, teams, roles and the system. Sees no data they have "
        "not been granted or granted themselves.",
        (
            "user.read", "user.manage", "service_user.manage",
            "team.read", "team.manage", "role.read", "role.manage",
            "audit.read", "access.review",
            "connection.create", "llm_config.create", "dashboard.create",
            "report.create", "conversation.create",
            "settings.manage", "benchmark.manage", "eval.run",
            "system.maintenance",
        ),
        (),
    ),
    (
        "Normal User",
        "Asks questions, builds their own dashboards and reports, and sees "
        "exactly what they own or have been given.",
        ("dashboard.create", "report.create", "conversation.create", "team.read"),
        (),
    ),
    (
        "Viewer",
        "Consumes. Creates nothing. Every surface they see is a grant.",
        ("team.read",),
        (),
    ),
    (
        "Data Engineer",
        "Owns how DataMind understands the schema. Can see that every "
        "connection exists and curate meaning on all of them — and still needs "
        "select to read any data.",
        ("connection.create", "conversation.create", "team.read"),
        (("semantic_layer", "manage"), ("connection", "describe")),
    ),
    (
        "BI Engineer",
        "Builds the artifacts. Their data reach comes from the team they are "
        "in, not from this role.",
        ("dashboard.create", "report.create", "conversation.create", "team.read"),
        (("dashboard", "describe"), ("report", "describe")),
    ),
    (
        "Knowledge Manager",
        "Owns what the system has been taught, across every connection, "
        "without being able to edit a credential, change a disclosure policy, "
        "or read data they were not granted.",
        ("conversation.create", "benchmark.manage", "team.read"),
        (("knowledge", "manage"), ("connection", "describe")),
    ),
    (
        "DataMind Maintainer",
        "Keeps the installation running. Deliberately not user.manage: "
        "administering people and administering the system are different jobs.",
        (
            "llm_config.create", "service_user.manage", "settings.manage",
            "eval.run", "system.maintenance", "benchmark.manage",
        ),
        (("llm_config", "describe"),),
    ),
    (
        "Auditor",
        "Can answer who can reach what, and who did what, for every resource — "
        "and can read none of the data in any of them.",
        ("user.read", "team.read", "role.read", "audit.read", "access.review"),
        (("connection", "describe"), ("dashboard", "describe"), ("report", "describe")),
    ),
)


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        # The external role this mirrors, when it mirrors one. Both columns or
        # neither. Unused until an OIDC adapter exists; present now because
        # retrofitting a namespace onto identifiers that assignments already
        # point at is the migration nobody wants.
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
        sa.UniqueConstraint("name", name="uq_roles_name"),
        sa.UniqueConstraint("provider_id", "source_id", name="uq_roles_source"),
        sa.CheckConstraint(
            "(provider_id IS NULL) = (source_id IS NULL)",
            name="ck_roles_source_pair",
        ),
    )

    op.create_table(
        "role_capabilities",
        sa.Column(
            "role_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("capability", sa.String(50), primary_key=True),
    )

    op.create_table(
        "role_scoped_privileges",
        sa.Column(
            "role_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("resource_type", sa.String(30), primary_key=True),
        sa.Column("privilege", sa.String(20), primary_key=True),
    )

    op.create_table(
        "role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # RESTRICT, not CASCADE — decision 3 in this module's docstring.
        sa.Column(
            "role_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        # Named for the three-column form `0025` widens it to, so that
        # migration replaces a constraint rather than inventing one.
        sa.UniqueConstraint("role_id", "user_id", name="uq_role_assignment"),
    )
    op.create_index("ix_role_assignments_user", "role_assignments", ["user_id"])

    _seed()
    _backfill()


def _seed() -> None:
    """The eight, with generated ids.

    Ids are generated rather than pinned because nothing outside the database
    points at a role by id — assignments are rows, and everything human refers
    to a role by name. Constants here would be a second identifier to keep in
    sync, with no reader.
    """
    for name, description, capabilities, scoped in SEED:
        op.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, is_system) "
                "VALUES (gen_random_uuid(), :name, :description, true)"
            ).bindparams(name=name, description=description)
        )
        for capability in capabilities:
            op.execute(
                sa.text(
                    "INSERT INTO role_capabilities (role_id, capability) "
                    "SELECT id, :capability FROM roles WHERE name = :name"
                ).bindparams(capability=capability, name=name)
            )
        for resource_type, privilege in scoped:
            op.execute(
                sa.text(
                    "INSERT INTO role_scoped_privileges "
                    "(role_id, resource_type, privilege) "
                    "SELECT id, :resource_type, :privilege "
                    "FROM roles WHERE name = :name"
                ).bindparams(
                    resource_type=resource_type, privilege=privilege, name=name
                )
            )


def _backfill() -> None:
    """Every existing account gets the role its `users.role` string meant.

    In the same migration, so no installation lands with a `roles` table and
    nobody holding one — which would be an upgrade that signed everybody out of
    their own administration screens.
    """
    op.execute(
        sa.text(
            "INSERT INTO role_assignments (id, role_id, user_id) "
            "SELECT gen_random_uuid(), r.id, u.id "
            "FROM users u JOIN roles r ON r.name = CASE WHEN u.role = 'ADMIN' "
            "THEN 'Administrator' ELSE 'Normal User' END"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_role_assignments_user", table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_table("role_scoped_privileges")
    op.drop_table("role_capabilities")
    op.drop_table("roles")
