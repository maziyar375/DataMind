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

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement, FunctionElement
from sqlalchemy.types import Date

from app.infra.db.models import ReportRun, Run, RunStep, SemanticJobRow, User

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


# ── the day a row belongs to ─────────────────────────────────────────────
class day_of(FunctionElement[date]):  # noqa: N801 — a SQL function, named like one
    """`date_trunc('day', …)` on Postgres, `date(…)` on SQLite.

    The bucketing is UTC and the grouping key is a **date**, not a timestamp:
    two rows an hour apart either side of midnight belong to different days and
    that is the whole of the arithmetic.

    Compiled per dialect rather than written as a literal because the unit
    tests run against SQLite, which has no `date_trunc`, and a query that only
    exists in production is a query nothing tests. The alternative — grouping
    in Python over every row in the window — is the unbounded read this module
    exists to avoid.
    """

    type = Date()
    inherit_cache = True


@compiles(day_of)
def _day_of_default(element: Any, compiler: Any, **kw: Any) -> str:
    """Postgres, and anything else that speaks `date_trunc`."""
    (column,) = element.clauses
    return f"date_trunc('day', {compiler.process(column, **kw)})"


@compiles(day_of, "sqlite")
def _day_of_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    (column,) = element.clauses
    return f"date({compiler.process(column, **kw)})"


def _as_date(value: Any) -> date:
    """One day key, however the driver handed it back.

    Postgres returns a `datetime` from `date_trunc`; SQLite returns a string
    from `date()`. Neither is the dataclass's `date`, and a screen grouping on
    two different types would silently draw two bars for one day.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# ── the union the three aggregations read ────────────────────────────────
#: The three tables that record what a model call cost, and nothing else reads
#: as usage. Each contributes the same five columns, so the union is one shape
#: and a fourth source is a row here rather than a rewrite.
#:
#: `runs` is a chat question, `report_runs` one generated document, and
#: `semantic_jobs` one layer generation. They are counted together because the
#: question is *"what did this person's use of the product cost?"*, and a
#: screen that answered it for chat alone would understate every person who
#: writes reports.
_SOURCES = (Run, ReportRun, SemanticJobRow)


def _arm(model: type) -> sa.Select[Any]:
    """One table's contribution to the union, as the five columns it shares."""
    return sa.select(
        model.actor_id.label("actor_id"),
        day_of(model.created_at).label("day"),
        model.prompt_tokens.label("prompt_tokens"),
        model.completion_tokens.label("completion_tokens"),
        model.cost_usd.label("cost_usd"),
    )


def _operations(window: Window) -> sa.Subquery:
    """Every priced-or-unpriced operation in the window, from all three tables.

    `UNION ALL`, never `UNION`: two runs that happened to cost the same on the
    same day are two operations, and deduplicating them would silently halve a
    busy day.
    """
    arms = [
        _arm(model).where(
            model.created_at >= window.since,
            model.created_at < window.until,
        )
        for model in _SOURCES
    ]
    return sa.union_all(*arms).subquery("operations")


#: The four aggregates every scope computes, over whichever subquery it reads.
#:
#: `SUM` ignores nulls in SQL, which is exactly what both carried-over rules
#: want — an unmeasured row contributes nothing rather than zero. What cannot
#: be left to `SUM` is *noticing*, so the two counts below count the rows that
#: contributed nothing, and the screen turns them into the sentence that says
#: how partial the total is.
def _aggregates(source: Any) -> list[ColumnElement[Any]]:
    return [
        sa.func.coalesce(sa.func.sum(source.c.prompt_tokens), 0).label(
            "prompt_tokens"
        ),
        sa.func.coalesce(sa.func.sum(source.c.completion_tokens), 0).label(
            "completion_tokens"
        ),
        # **Not** coalesced. A scope where nothing was priced reports `None`,
        # which is "no price is knowable", and `0.0`, which is "it was free",
        # is a different and false claim.
        sa.func.sum(source.c.cost_usd).label("cost_usd"),
        sa.func.count().label("runs"),
        # Measured by the absence of *both* counts: a row reporting prompt
        # tokens and no completion tokens is measured, just oddly.
        sa.func.count()
        .filter(
            source.c.prompt_tokens.is_(None), source.c.completion_tokens.is_(None)
        )
        .label("unmeasured"),
        # Contributed tokens but no price. A row that measured nothing is
        # already counted above and is not counted twice here.
        sa.func.count()
        .filter(
            source.c.cost_usd.is_(None),
            sa.or_(
                source.c.prompt_tokens.is_not(None),
                source.c.completion_tokens.is_not(None),
            ),
        )
        .label("unpriced"),
    ]


def _bucket(row: Any) -> Bucket:
    return Bucket(
        day=_as_date(row.day),
        prompt_tokens=int(row.prompt_tokens or 0),
        completion_tokens=int(row.completion_tokens or 0),
        cost_usd=float(row.cost_usd) if row.cost_usd is not None else None,
        runs=int(row.runs or 0),
    )


def _fold(buckets: Sequence[Bucket], rows: Sequence[Any]) -> tuple[float | None, int]:
    """The scope's cost and how much of it is unpriced, from the day rows.

    Returned rather than recomputed from `buckets`, because a day whose cost is
    `None` and a day that cost nothing are the same `Bucket` and the total has
    to tell them apart: a scope is unpriced only when **every** day in it is.
    """
    priced = [b.cost_usd for b in buckets if b.cost_usd is not None]
    unpriced = sum(int(row.unpriced or 0) for row in rows)
    return (sum(priced) if priced else None), unpriced


async def for_actor(
    db: AsyncSession, actor_id: UUID, *, window: Window
) -> Series:
    """One person's usage, by day.

    **Inner joins `users`**, like `per_actor` and unlike `installation`: this is
    the per-person view, and a person who has been deleted is not in it. See the
    module docstring's second rule.
    """
    series = await per_actor(db, window=window, only=actor_id)
    if series:
        return series[0]
    # Nothing in the window. Still name the person, so a quiet month reads as
    # "you, zero" rather than as an empty response the screen has to guess at.
    name = await db.scalar(sa.select(User.display_name).where(User.id == actor_id))
    return Series(actor_id=actor_id, actor=name or "")


async def per_actor(
    db: AsyncSession, *, window: Window, only: UUID | None = None
) -> list[Series]:
    """Everybody's usage, by day, one `Series` each.

    One grouped query rather than a query per person: the screen renders a list
    of people with a chart each, and a loop of per-person reads is the
    pagination problem the access-control rulebook names.

    The join to `users` is **inner** — a deleted actor's rows leave this view
    and stay in `installation`. An outer join would attribute a departed
    person's spend to whoever remains, which is the one answer that is wrong.
    """
    operations = _operations(window)
    conditions = [operations.c.actor_id.is_not(None)]
    if only is not None:
        conditions.append(operations.c.actor_id == only)

    daily = (
        sa.select(
            operations.c.actor_id,
            User.display_name.label("actor"),
            operations.c.day,
            *_aggregates(operations),
        )
        .join(User, User.id == operations.c.actor_id)
        .where(*conditions)
        .group_by(operations.c.actor_id, User.display_name, operations.c.day)
        .order_by(User.display_name, operations.c.day)
    )

    grouped: dict[UUID, list[Any]] = {}
    names: dict[UUID, str] = {}
    for row in (await db.execute(daily)).all():
        grouped.setdefault(row.actor_id, []).append(row)
        names[row.actor_id] = row.actor or ""

    return [_series(actor_id, names[actor_id], rows) for actor_id, rows in grouped.items()]


def _series(actor_id: UUID | None, actor: str, rows: Sequence[Any]) -> Series:
    """Fold a scope's day rows into its total.

    The total is summed from the same rows the buckets are built from, so the
    invariant the screen rests on — **the total equals the sum of the buckets**
    — holds by construction rather than by two additions agreeing.
    """
    buckets = [_bucket(row) for row in rows]
    cost, unpriced = _fold(buckets, rows)
    return Series(
        actor_id=actor_id,
        actor=actor,
        prompt_tokens=sum(b.prompt_tokens for b in buckets),
        completion_tokens=sum(b.completion_tokens for b in buckets),
        cost_usd=cost,
        runs=sum(b.runs for b in buckets),
        unmeasured=sum(int(row.unmeasured or 0) for row in rows),
        unpriced=unpriced,
        buckets=buckets,
    )


async def installation(db: AsyncSession, *, window: Window) -> InstallationSeries:
    """What the whole installation spent, by day.

    **No join to `users` at all** — the one structural difference from
    `per_actor`, and the one most likely to be "fixed" by a well-meaning later
    change. A run whose actor has since been deleted carries `actor_id IS NULL`
    and is still a true record of tokens somebody spent, so it is counted here.

    The gap between this and the sum of the per-person series is therefore real,
    and `unattributed` is its size. The screen states it. An outer join in
    `per_actor` would close the gap by attributing a departed person's spend to
    whoever remains, which is the one answer that is wrong; leaving those rows
    out of *both* views would quietly shrink the installation's own total,
    which is the other.
    """
    operations = _operations(window)
    daily = (
        sa.select(operations.c.day, *_aggregates(operations))
        .group_by(operations.c.day)
        .order_by(operations.c.day)
    )
    rows = (await db.execute(daily)).all()
    base = _series(None, "", rows)

    # A second, deliberately separate read: the shape of the gap. Folded into
    # the grouped query above it would need a `FILTER` per aggregate and would
    # still not answer "how many tokens", which is the half a reader cares
    # about — "11 operations" and "11 operations worth 400k tokens" are
    # different sentences and only the second one is actionable.
    orphaned = (
        await db.execute(
            sa.select(
                sa.func.count().label("runs"),
                sa.func.coalesce(
                    sa.func.sum(
                        sa.func.coalesce(operations.c.prompt_tokens, 0)
                        + sa.func.coalesce(operations.c.completion_tokens, 0)
                    ),
                    0,
                ).label("tokens"),
            ).where(operations.c.actor_id.is_(None))
        )
    ).one()

    return InstallationSeries(
        actor_id=None,
        actor="",
        prompt_tokens=base.prompt_tokens,
        completion_tokens=base.completion_tokens,
        cost_usd=base.cost_usd,
        runs=base.runs,
        unmeasured=base.unmeasured,
        unpriced=base.unpriced,
        buckets=base.buckets,
        unattributed=int(orphaned.runs or 0),
        unattributed_tokens=int(orphaned.tokens or 0),
    )


async def by_node(db: AsyncSession, run_id: UUID) -> list[NodeUsage]:
    """One run's spend, attributed to the nodes that caused it.

    `run_steps` grouped by name, in the order the steps ran. Phase 4's step
    chips are the only caller; it lives here because it is the same kind of
    query as its three siblings and splitting it across two modules would be
    the second place somebody looks for "where is usage read?".

    **Only the nodes that called a model.** `validate` and `execute` call none,
    and a row of zeroes beside `generate` would read as a measurement of
    nothing rather than as the absence of one — which is the same rule the
    nullable columns on `run_steps` already state. A node whose `llm_calls` is
    NULL or zero is left out entirely.

    A repaired `generate` is one row here, not two: the repair loop re-enters
    the same node, and `llm_calls` is why the column is not derivable from the
    step existing.
    """
    grouped = (
        sa.select(
            RunStep.name,
            sa.func.coalesce(sa.func.sum(RunStep.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            sa.func.coalesce(sa.func.sum(RunStep.completion_tokens), 0).label(
                "completion_tokens"
            ),
            sa.func.coalesce(sa.func.sum(RunStep.llm_calls), 0).label("llm_calls"),
            sa.func.coalesce(sa.func.sum(RunStep.llm_latency_ms), 0).label(
                "llm_latency_ms"
            ),
            sa.func.min(RunStep.seq).label("first_seq"),
        )
        .where(RunStep.run_id == run_id)
        .group_by(RunStep.name)
        # The order the nodes ran in, not alphabetical: a breakdown is read
        # against the chain it came from.
        .order_by(sa.func.min(RunStep.seq))
    )

    return [
        NodeUsage(
            name=row.name,
            prompt_tokens=int(row.prompt_tokens or 0),
            completion_tokens=int(row.completion_tokens or 0),
            llm_calls=int(row.llm_calls or 0),
            llm_latency_ms=int(row.llm_latency_ms or 0),
        )
        for row in (await db.execute(grouped)).all()
        if row.llm_calls
    ]
