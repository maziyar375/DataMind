"""Request/response DTOs: token usage."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# The read side of migration `0023`'s per-call accounting. One shape, three
# uses: your own usage, everybody's, and the installation's.
#
# **Counts, never content.** No question, no prompt, no generated SQL and no
# result value is carried here — only integers, a model name and a day. "Ali
# asked 40 questions using 180k tokens" is a different disclosure from "here
# is what Ali asked", and only the first one is available through these DTOs.
class UsageBucket(BaseModel):
    """One bucket's tokens, for one scope.

    `start` is the bucket's first instant, in UTC; the bucket runs for the
    series' `bucket_seconds`. Buckets are aligned to the reader's clock (the
    `tz_offset` they asked with), so a day bucket starts at *their* midnight.
    """

    start: datetime
    #: Measured tokens only. A run that reported no count contributes nothing
    #: here and is counted into `UsageSeries.unmeasured` instead — it is never
    #: summed as zero.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: How many operations are behind the figures above. An hour with 400 runs
    #: and one with 4 are different facts about the same token count.
    runs: int = 0
    #: What of `prompt_tokens` the provider served from, or wrote into, its
    #: cache. **A subset of `prompt_tokens`** — a screen subdivides the input
    #: figure rather than stacking on top of it. `None` where nothing in this
    #: scope reported a cache figure at all, which is a different fact from a
    #: reported `0`.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class UsageModel(BaseModel):
    """One model's share of a scope: its total over the window, and its buckets."""

    #: The model as the run recorded it — `""` where a row recorded none.
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    runs: int = 0
    #: Operations on this model that reported no token count at all.
    unmeasured: int = 0
    #: What of `prompt_tokens` the provider served from, or wrote into, its
    #: cache. **A subset of `prompt_tokens`** — a screen subdivides the input
    #: figure rather than stacking on top of it. `None` where nothing in this
    #: scope reported a cache figure at all, which is a different fact from a
    #: reported `0`.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    #: How many operations reported a cache figure at all — the denominator a
    #: screen needs before it says "62% of input was served from cache".
    cache_measured: int = 0
    #: Sparse, like `UsageSeries.buckets`: a bucket this model did not run in
    #: is absent.
    buckets: list[UsageBucket] = Field(default_factory=list)


class UsageSeries(BaseModel):
    """One scope's usage: a total, the buckets it is made of, and the models.

    The invariant the screen rests on: **the total equals the sum of the
    buckets**, and equally the sum of `models`. All three come from the same
    rows, so they cannot drift.

    `buckets` is **sparse** — a bucket nothing ran in is absent. `since`,
    `until` and `bucket_seconds` describe the whole window, so a chart can
    draw the empty buckets and run its axis to the window's real end rather
    than to the last bucket anything happened in.
    """

    #: `None` on the installation total, which is nobody's.
    actor_id: UUID | None = None
    #: A **display name, never an address** — the rule `AuditEntry` already
    #: states, for the same reason: a usage screen answers *"who spent this"*
    #: with something a person recognises, and an email is a personal
    #: identifier the screen has no need of.
    actor: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    runs: int = 0
    #: How many of those operations reported no token count at all. Non-zero
    #: means every figure above understates, and the screen says so. A count
    #: rather than a flag, because a reader needs to know *how* partial a total
    #: is before deciding whether to act on it.
    unmeasured: int = 0
    #: What of `prompt_tokens` the provider served from, or wrote into, its
    #: cache. **A subset of `prompt_tokens`** — a screen subdivides the input
    #: figure rather than stacking on top of it. `None` where nothing in this
    #: scope reported a cache figure at all, which is a different fact from a
    #: reported `0`.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    #: How many operations reported a cache figure at all.
    cache_measured: int = 0
    #: The start of the first bucket. Aligned, so it may be a little earlier
    #: than the `since` that was asked for — see `usage_service.clamp_window`.
    since: datetime | None = None
    #: The end of the window, exclusive: *now*, unless the caller named one.
    until: datetime | None = None
    #: How wide every bucket is, chosen by the server from the window's length.
    bucket_seconds: int = 86_400
    buckets: list[UsageBucket] = Field(default_factory=list)
    #: The same total split by model, busiest first.
    models: list[UsageModel] = Field(default_factory=list)


class UsageTotal(UsageSeries):
    """The installation's own usage, and the size of the departed-actor gap.

    The per-person views inner join `users`; this one does not join at all, so
    a deleted person's spend leaves the first and stays here. The gap between
    them is real and correct — an outer join "fixing" it would attribute a
    departed person's spend to whoever remains — and `unattributed` is its
    size, carried on the wire so the screen states the difference rather than
    letting a reader discover it by adding the people up and finding a
    shortfall.
    """

    unattributed: int = 0
    unattributed_tokens: int = 0
