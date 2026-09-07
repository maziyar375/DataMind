"""API keys for machine identities: two halves, and neither is guessable.

**The key is `dm_sk_<prefix>_<secret>` and only the prefix is stored in the
clear.** Three properties come out of that shape, and each answers a failure
this table exists to prevent:

* **`dm_sk_` is one pattern a secret scanner can be taught.** GitHub's push
  protection, `gitleaks` and every commercial scanner match on a literal
  prefix; a key that was 48 characters of base64 with no marker is one nobody
  can grep for, in a log, in a repository, or in a support ticket.
* **`prefix` is indexed and unique, so a leaked key is traceable to its owner
  without the secret half ever being stored.** Somebody pastes the first
  twenty characters of a key they found in a log into the admin screen, and the
  answer is *"that is the nightly-report agent's key, issued in March"*. This
  is the entire reason the key has two parts rather than one.
* **Verification is one indexed read and one constant-time compare.**
  `SELECT … WHERE prefix = :p`, then `hmac.compare_digest` over the SHA-256 of
  the secret half. No scan, no per-row hashing.

**`token_hash` is SHA-256, deliberately not Argon2id, and that is not
laziness.** The secret half is 32 bytes from `secrets.token_urlsafe` — 256 bits
of entropy from the OS. Key-stretching exists to make *guessing a human
password* expensive; against a uniformly random 256-bit secret there is nothing
to guess, so Argon2id would buy zero security and cost 50–100 ms of CPU on
**every API call this identity makes**. The password path keeps Argon2id for
exactly the opposite reason. Both choices are the same principle applied to
inputs with different entropy.

**`scopes` is reserved, unread, and named now.** A list of capability or
privilege strings a key may exercise, always a *subset* of what the service
user holds; empty means the principal's full permission, which is what every
key has today. It is here because narrowing a key later should be a service
change, not a migration on a table with live credentials in it.

**`expires_at` defaults to a year out** (`service_key_default_ttl_days`), set
by the service rather than by a column default, because "the caller named no
expiry" and "the caller asked for none" have to stay different answers. An
unexpiring machine credential is the one thing every published key-leak
incident has in common.

**`last_used_at` is written at most once a minute per key**, in
`infra/identity/service_key.py`. Without the throttle every authenticated
request from a busy integration is also a write, and the column's only purpose
— answering *"can I delete this?"* — needs minute resolution at best.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # CASCADE: deleting a machine identity takes its keys with it. There is
        # no state in which a credential outliving its principal is useful, and
        # one that did would authenticate as a user id with no row behind it.
        sa.Column(
            "service_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "scopes", postgresql.JSONB, nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        # SET NULL rather than CASCADE: who issued a key is history, and the
        # key must not disappear because the administrator who minted it left.
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        # Unique because the prefix *is* the lookup key. A collision would make
        # verification ambiguous, and at 12 characters of base32 the birthday
        # bound is far past any plausible key count — so the constraint is a
        # correctness statement, not a capacity plan.
        sa.UniqueConstraint("prefix", name="uq_service_credentials_prefix"),
    )
    op.create_index(
        "ix_service_credentials_user", "service_credentials", ["service_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_service_credentials_user", table_name="service_credentials")
    op.drop_table("service_credentials")
