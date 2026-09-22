"""What a cached prompt cost, per run and per node.

Phase 1 of `docs/plans/deep-analysis-mode.md` §2.1. Two columns on `runs` and
the same two on `run_steps`, recording what of `prompt_tokens` the provider
served from — or wrote into — its cache.

**A subset of `prompt_tokens`, never an addition to it.** OpenAI reports
`cached_tokens` as the part of the prompt it did not have to read again, and
LiteLLM folds Anthropic's separate figures into `prompt_tokens` for the same
reason, so a reader adding either to the input count double-counts.

**Nullable, and never defaulted to `0`** — the rule `0023` added the existing
token columns under, and here it is load-bearing rather than tidy. Three facts
have to stay apart and two `int` columns can only hold two of them:

* the provider cached nothing — `0`;
* the provider does not report caching at all — NULL;
* the provider served the prompt from cache — a number.

The first two drive **opposite** decisions about whether a workload that
re-sends the same schema block once per step is affordable, which is the whole
reason this phase precedes the loop that would generate that workload. A `0`
default would quietly turn *"this endpoint says nothing"* into *"this endpoint
cached nothing"*, and nothing downstream could tell.

No backfill, for `0023`'s reason: a run that predates these columns genuinely
has no answer, and inventing one repeats the `prompt_version` mistake this tree
already carries in five weeks of rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_TABLES = ("runs", "run_steps")
_COLUMNS = ("cache_read_tokens", "cache_write_tokens")


def upgrade() -> None:
    for table in _TABLES:
        for name in _COLUMNS:
            op.add_column(table, sa.Column(name, sa.Integer))


def downgrade() -> None:
    for table in _TABLES:
        for name in reversed(_COLUMNS):
            op.drop_column(table, name)
