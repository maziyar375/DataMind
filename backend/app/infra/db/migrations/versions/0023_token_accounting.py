"""Tokens, cost and an actor on the three run tables.

`runs.prompt_tokens` and `runs.completion_tokens` have been in the schema since
`0001` and have been **wrong** since: `structured()` and `stream()` never
returned the `usage` object they were handed, so a chat run recorded the tokens
of its cheapest call — a one-word routing classification — and none of the
expensive ones. A report generation and a semantic build, the two most
expensive operations in the product, recorded nothing at all because neither
table had anywhere to put a number. `0023` is the storage half of the fix whose
transport half is the `on_usage` sink added in the revision before it.

**Every token column is nullable and none is defaulted to `0`.** "This node
never calls a model" (`validate`, `execute`) and "it called one and the
provider reported nothing" are different facts, and a column that renders both
as `0` cannot be asked which happened. A `NULL` here means *not measured* —
never *no tokens* — and no query built on these may coerce one to zero.

**`run_steps` is where per-node attribution lives**, which is the question
`runs` alone cannot answer: a run's total says a question cost 12k tokens, and
only the steps say the schema block was 9k of it. `llm_calls` is on the same
row because `generate` can repair, so "how many calls did this step make" is
not derivable from the step's existence — and `llm_latency_ms` is distinct from
the step's own `duration_ms` because a node that spends 200ms of its 4s in the
provider is a different problem from one that spends 3.9s there.

**`cost_usd` is `Float` and nullable, and null is the normal state for a
self-hosted deployment.** `estimate_cost_usd` returns `None` for a model
litellm cannot price — a local Ollama model prices as nothing knowable, not as
free — and summing a null as zero would report a real spend as no spend.

**`actor_id` is added now, while it is trivially correct.** Today every one of
these rows is created by the person who owns it: `create_run` takes `owner_id`
from `ctx.user_id` and refuses a conversation the caller does not own. That
stops being true the moment a connection can be shared (mvp2 §D1), when the
person who *owns* a dataset and the person who *asked the question* are
routinely different and billing the owner for a colleague's question is the
wrong answer. Retrofitted later, the backfill would have to invent who asked;
done now it is `owner_id`, exactly, for every row that exists. Same reasoning
the tree applied to `curation_admin_only`: flip it before sharing exists, so
the flip is not also a behaviour change.

It is `ON DELETE SET NULL`, matching every other reference to `users` and
`llm_configs` here — a usage row whose actor was deleted is still a true record
of tokens spent, and CLAUDE.md is explicit that deleting history to satisfy a
constraint is the wrong trade. It is deliberately **not** `owner_id`'s
exception: that one stays non-null because ownership filters match on it, and a
row with a null owner is a row no filter finds.

**The token columns are not backfilled.** A historical run's true token count
is unknowable, and inventing one would repeat the `prompt_version` mistake this
tree already records, where rows from a five-week window still claim a version
they never ran. Nulls stay null.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

#: The three tables that record one unit of model work each. `run_steps` is
#: handled apart: it is the granular level, not a run.
RUN_TABLES = ("runs", "report_runs", "semantic_jobs")


def upgrade() -> None:
    # ── per-node attribution ─────────────────────────────────────────────
    for column in (
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("llm_latency_ms", sa.Integer),
        sa.Column("llm_calls", sa.Integer),
    ):
        op.add_column("run_steps", column)

    # ── the run tables ───────────────────────────────────────────────────
    # `runs` already carries prompt/completion tokens and `llm_latency_ms`
    # from 0001; the other two carry none of the three.
    op.add_column("runs", sa.Column("cost_usd", sa.Float))
    for table in ("report_runs", "semantic_jobs"):
        op.add_column(table, sa.Column("prompt_tokens", sa.Integer))
        op.add_column(table, sa.Column("completion_tokens", sa.Integer))
        op.add_column(table, sa.Column("llm_latency_ms", sa.Integer))
        op.add_column(table, sa.Column("cost_usd", sa.Float))

    # ── who caused the work ──────────────────────────────────────────────
    for table in RUN_TABLES:
        op.add_column(
            table, sa.Column("actor_id", postgresql.UUID(as_uuid=True))
        )
        op.create_foreign_key(
            f"fk_{table}_actor",
            table,
            "users",
            ["actor_id"],
            ["id"],
            ondelete="SET NULL",
        )
        # Indexed because the only question these columns exist to answer —
        # what has this person spent — groups by exactly this, across three
        # tables, and an unindexed sequential scan of `runs` is the whole
        # transcript.
        op.create_index(f"ix_{table}_actor_id", table, ["actor_id"])

    # Correct by construction rather than by assumption: every row that exists
    # was created by its owner, because no path has ever written one any other
    # way. Written out rather than interpolated in the loop above — a table
    # name in an f-string reads as an injection whether or not the value is a
    # literal three lines up, and three statements are cheaper than the
    # suppression comment explaining why this one is fine.
    op.execute("UPDATE runs SET actor_id = owner_id WHERE actor_id IS NULL")
    op.execute("UPDATE report_runs SET actor_id = owner_id WHERE actor_id IS NULL")
    op.execute("UPDATE semantic_jobs SET actor_id = owner_id WHERE actor_id IS NULL")


def downgrade() -> None:
    for table in reversed(RUN_TABLES):
        op.drop_index(f"ix_{table}_actor_id", table_name=table)
        op.drop_constraint(f"fk_{table}_actor", table, type_="foreignkey")
        op.drop_column(table, "actor_id")

    for table in ("semantic_jobs", "report_runs"):
        op.drop_column(table, "cost_usd")
        op.drop_column(table, "llm_latency_ms")
        op.drop_column(table, "completion_tokens")
        op.drop_column(table, "prompt_tokens")
    op.drop_column("runs", "cost_usd")

    for name in ("llm_calls", "llm_latency_ms", "completion_tokens", "prompt_tokens"):
        op.drop_column("run_steps", name)
