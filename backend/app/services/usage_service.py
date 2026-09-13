"""What the models cost, read back out of the three tables that record it.

Migration `0023` put `prompt_tokens`, `completion_tokens` and `cost_usd` on
`runs`, `report_runs` and `semantic_jobs`, and `run_steps` carries the same per
node. **Nothing has read them since.** This module is the read side, and it is
the whole of it: three aggregations over a union, and one rollup for a single
run. No route, no DTO, no screen — those are the phases above this one.

Three rules govern every figure below, and each is a way a usage screen lies:

* **A null is never summed as zero.** `cost_usd IS NULL` means *litellm could
  not price this model*, which is every self-hosted deployment; `prompt_tokens
  IS NULL` means *nothing measured this*, which is every row written before
  `0023` and every streamed reply whose provider sent no usage block. SQL's
  `SUM` already ignores nulls, so the arithmetic is right by default — what
  this module adds is `unmeasured` and `unpriced`, which count the rows that
  contributed nothing so the screen can say *how* partial a total is. A
  partial total that does not announce itself is the failure both rules name,
  and a boolean would not say how partial.

* **The per-person view inner joins `users`; the installation total does not
  join at all.** A deleted actor leaves the first and stays in the second, and
  the gap between them is real and correct. An outer join "fixing" it would
  attribute a departed person's spend to whoever remains, which is the one
  answer that is wrong. The total reports the size of that gap rather than
  hiding it.

* **The window is clamped server-side.** The union carries no `LIMIT`, and an
  unbounded range over three growing tables is an outage waiting for its first
  busy installation.

**Counts, never content.** No question, no prompt, no generated SQL and no
result value is read here — only integers, a price and a timestamp. "Ali asked
40 questions costing 180k tokens" is a different disclosure from "here is what
Ali asked", and only the first one is available through this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

#: The widest window a caller may ask for. A year and a day — wide enough for
#: "last year" plus the leap day, narrow enough that the union stays bounded.
MAX_WINDOW_DAYS = 366

#: What `since` defaults to when a caller names neither end.
DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open `[since, until)` range of days, already clamped.

    Half-open because the alternative is an off-by-one nobody catches: a
    closed range over `date_trunc('day', …)` either double-counts the boundary
    day or drops it, depending on which comparison somebody wrote.
    """

    since: datetime
    until: datetime

    @property
    def days(self) -> int:
        return (self.until - self.since).days


def clamp_window(
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    now: datetime | None = None,
) -> Window:
    """Resolve and bound what the caller asked for.

    Missing ends default rather than fail: `until` is now, `since` is
    `DEFAULT_WINDOW_DAYS` before it. A range wider than `MAX_WINDOW_DAYS` is
    **narrowed to the most recent** `MAX_WINDOW_DAYS` rather than refused —
    a 400 on a window a caller could not know was too wide teaches nothing,
    and the recent end is the half anybody asking a usage question wants.

    A reversed range collapses to an empty one. That is a caller's mistake and
    it reads as "no usage", which is true of a window with no days in it.
    """
    right_now = now or datetime.now(UTC)
    end = until or right_now
    start = since if since is not None else end - timedelta(days=DEFAULT_WINDOW_DAYS)

    if start > end:
        start = end

    widest = end - timedelta(days=MAX_WINDOW_DAYS)
    if start < widest:
        start = widest

    return Window(since=start, until=end)


@dataclass(frozen=True, slots=True)
class Bucket:
    """One day's spend, for one scope.

    `cost_usd` is `None` — not `0.0` — when nothing in the day was priced.
    Zero is a measurement and this is the absence of one.
    """

    day: date
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    #: How many operations are behind the figures above. A day with 400 runs
    #: and one with 4 are different facts about the same token count.
    runs: int = 0


@dataclass(frozen=True, slots=True)
class Series:
    """One scope's usage: a total, and the days it is made of.

    The invariant the whole screen rests on: **the total equals the sum of the
    buckets.** It is computed from the same rows in the same query rather than
    added up twice, so the two cannot drift.
    """

    actor_id: UUID | None = None
    #: A display name, never an address. The rule `AuditEntry` already states,
    #: for the same reason: a usage screen answers *"who spent this"* with
    #: something a person recognises, and an email is a personal identifier
    #: the screen has no need of.
    actor: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    runs: int = 0
    #: How many of those operations reported no token count at all. Non-zero
    #: means every figure above understates, and the screen says so.
    unmeasured: int = 0
    #: How many contributed tokens but no price. Non-zero means `cost_usd` is
    #: partial, and the screen says *that* — it never prints a bare total.
    unpriced: int = 0
    buckets: list[Bucket] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class InstallationSeries(Series):
    """The whole installation, with the size of the departed-actor gap.

    `unattributed` is how many operations have no living actor — rows whose
    `actor_id` is NULL because the person was deleted, or was never recorded.
    They are counted in every figure here and in **none** of the per-person
    ones, and the screen states the difference rather than letting a reader
    discover it by adding the people up and finding a shortfall.
    """

    unattributed: int = 0
    unattributed_tokens: int = 0


@dataclass(frozen=True, slots=True)
class NodeUsage:
    """One node's share of one run. `run_steps`, grouped by name.

    A run saying a question cost 12k tokens is not the same as knowing the
    schema block was 9k of it, which is the question this answers and the run's
    own totals cannot.
    """

    name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    llm_latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens
