"""Seed `usage.read` to Administrator and Auditor.

The nineteenth capability, and the first one added after `0024` wrote the
eight system roles. **No data table is touched** — this grants a word to two
roles and nothing else, which is the whole of what the usage screen needs
before it can be gated.

**Why these two and no others.** `usage.read` answers *"what did everybody's
questions cost?"*, which is a record **about people** — the same argument
`audit.read` makes, and the reason both sit in the oversight group. An Auditor
is precisely the role that reads such a record and changes nothing, so a role
described as *"can answer who did what, for every resource — and can read none
of the data in any of them"* is the one this belongs to. Reading your **own**
usage needs no capability at all: `/usage/me` scopes to the caller and no
parameter can widen it, so gating it would only stop people seeing a figure
that is already theirs.

**Why the seed is a migration rather than an edit to `0024`.** Administrator's
capabilities are seeded *by enumeration*, deliberately, so that a nineteenth
capability is a reviewed line in a diff instead of something Administrator
silently acquires through a wildcard. This file is that line. Editing `0024`
would grant it only to installations that had not migrated yet, which is the
one set of installations that does not need a migration.

**It is not a `PRIVILEGED_CAPABILITY`.** The four in that set are the ones a
leaked API key must not reach because they can mint an administrator. Reading
token counts mints nothing, and a service account that reports installation
spend to a finance system is a legitimate thing to want.

The insert is idempotent on the composite primary key `(role_id, capability)`
and tolerates a renamed or deleted role — the `SELECT` simply matches nothing.
A role somebody renamed is not a reason for an upgrade to fail.

The downgrade deletes the word from **every** role, not only these two. That is
the correct breadth: downgrading past this point is going back to a build whose
`Capability` enum does not know `usage.read`, and a row naming a word the build
does not know is ignored with a warning — so leaving a hand-granted one behind
would be a row that means nothing and reappears the day somebody upgrades again.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

#: The two roles that get it, by name. The same two names `0024` wrote; a
#: literal here rather than a reference to that module's `SEED`, because what
#: is being stated is *which roles this capability belongs to*, not *what
#: `0024` happened to seed*.
ROLES = ("Administrator", "Auditor")

CAPABILITY = "usage.read"


def upgrade() -> None:
    for name in ROLES:
        op.execute(
            sa.text(
                "INSERT INTO role_capabilities (role_id, capability) "
                "SELECT id, :capability FROM roles WHERE name = :name "
                "ON CONFLICT DO NOTHING"
            ).bindparams(capability=CAPABILITY, name=name)
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_capabilities WHERE capability = :capability"
        ).bindparams(capability=CAPABILITY)
    )
