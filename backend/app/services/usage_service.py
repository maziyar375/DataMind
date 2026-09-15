"""How many tokens the models used, read back out of the three tables that record it.

Migration `0023` put `prompt_tokens` and `completion_tokens` on `runs`,
`report_runs` and `semantic_jobs`, and `run_steps` carries the same per node.
This module is the read side, and it is the whole of it: three aggregations
over a union — each bucketed in time and split by model — and one rollup for a
single run.

Three rules govern every figure below, and each is a way a usage screen lies:

* **A null is never summed as zero.** `prompt_tokens IS NULL` means *nothing
  measured this*, which is every row written before `0023` and every streamed
  reply whose provider sent no usage block. SQL's `SUM` already ignores nulls,
  so the arithmetic is right by default — what this module adds is
  `unmeasured`, which counts the rows that contributed nothing so the screen
  can say *how* partial a total is. A partial total that does not announce
  itself is the failure this rule names, and a boolean would not say how
  partial.

* **The per-person view inner joins `users`; the installation total does not
  join at all.** A deleted actor leaves the first and stays in the second, and
  the gap between them is real and correct. An outer join "fixing" it would
  attribute a departed person's spend to whoever remains, which is the one
  answer that is wrong. The total reports the size of that gap rather than
  hiding it.

* **The window is clamped server-side, and so is its resolution.** The union
  carries no `LIMIT`, and an unbounded range over three growing tables is an
  outage waiting for its first busy installation. The bucket width is chosen
  here from the window's length rather than taken from the caller, so no
  request can ask for a year in five-minute buckets.

**Counts, never content.** No question, no prompt, no generated SQL and no
result value is read here — only integers, a model name and a timestamp. "Ali
asked 40 questions using 180k tokens" is a different disclosure from "here is
what Ali asked", and only the first one is available through this module.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement, FunctionElement
from sqlalchemy.types import BigInteger

from app.infra.db.models import ReportRun, Run, RunStep, SemanticJobRow, User

#: The widest window a caller may ask for. A year and a day — wide enough for
#: "last year" plus the leap day, narrow enough that the union stays bounded.
MAX_WINDOW_DAYS = 366

#: What `since` defaults to when a caller names neither end.
DEFAULT_WINDOW_DAYS = 30

#: The widest UTC offset any zone uses (Line Islands, +14:00). A caller's
#: offset is clamped to it rather than refused.
MAX_OFFSET_MINUTES = 14 * 60

DAY_SECONDS = 86_400

#: How wide a bucket is, by how long the window is — the longest window each
#: width serves, narrowest first. Past the last row a bucket is a day.
#:
#: Chosen so every preset the screen offers draws between a dozen and ninety
#: bars: an hour is twelve five-minute buckets, six hours twenty-four quarter
#: hours, a day twenty-four hours, a week twenty-eight six-hour blocks, and a
#: month or a quarter one bar a day. A year is 366 bars, which is the ceiling.
GRANULARITY: tuple[tuple[timedelta, int], ...] = (
    (timedelta(hours=2), 5 * 60),
    (timedelta(hours=12), 15 * 60),
    (timedelta(days=2), 60 * 60),
    (timedelta(days=14), 6 * 60 * 60),
)


def bucket_seconds_for(span: timedelta) -> int:
    """The bucket width for a window this long."""
    for longest, seconds in GRANULARITY:
        if span <= longest:
            return seconds
    return DAY_SECONDS


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open `[since, until)` range, already clamped and aligned.

    `since` is the start of the first bucket, not necessarily the instant the
    caller asked for: it is moved so the window holds a whole number of
    buckets **ending with the one that contains `until`**. A "last 24 hours"
    asked at 10:02 is therefore twenty-four hourly buckets from 11:00 yesterday
    to the hour in progress — every bar but the last one whole, and the last
    one the hour that is happening now.

    Half-open because the alternative is an off-by-one nobody catches: a closed
    range either double-counts the boundary or drops it, depending on which
    comparison somebody wrote.

    `offset_seconds` is the reader's UTC offset. Buckets are aligned to *their*
    clock, so a day bar in Tehran starts at local midnight rather than at
    03:30. A fixed offset, not a zone: a window that spans a daylight-saving
    change keeps the offset it was asked with, and its bars on the far side of
    the change are an hour off local midnight. That is the trade for not
    needing a zone database in two SQL dialects.
    """

    since: datetime
    until: datetime
    bucket_seconds: int = DAY_SECONDS
    offset_seconds: int = 0

    @property
    def span(self) -> timedelta:
        return self.until - self.since


def _floor(instant: datetime, seconds: int, offset: int) -> datetime:
    """The start of the bucket `instant` falls in, on the reader's clock."""
    epoch = int(instant.timestamp())
    start = (epoch + offset) // seconds * seconds - offset
    return datetime.fromtimestamp(start, UTC)


def clamp_window(
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    now: datetime | None = None,
    tz_offset_minutes: int = 0,
) -> Window:
    """Resolve, bound and align what the caller asked for.

    Missing ends default rather than fail: `until` is now, `since` is
    `DEFAULT_WINDOW_DAYS` before it. A range wider than `MAX_WINDOW_DAYS` is
    **narrowed to the most recent** `MAX_WINDOW_DAYS` rather than refused —
    a 400 on a window a caller could not know was too wide teaches nothing,
    and the recent end is the half anybody asking a usage question wants.

    A reversed or empty range collapses to an empty one at `until`. That is a
    caller's mistake and it reads as "no usage", which is true of a window with
    no time in it.
    """
    right_now = now or datetime.now(UTC)
    end = until or right_now
    start = since if since is not None else end - timedelta(days=DEFAULT_WINDOW_DAYS)

    widest = end - timedelta(days=MAX_WINDOW_DAYS)
    if start < widest:
        start = widest

    minutes = max(-MAX_OFFSET_MINUTES, min(MAX_OFFSET_MINUTES, tz_offset_minutes))
    offset = minutes * 60

    if start >= end:
        return Window(since=end, until=end, bucket_seconds=300, offset_seconds=offset)

    span = end - start
    seconds = bucket_seconds_for(span)
    count = -(-int(span.total_seconds()) // seconds)  # ceiling division
    # The bucket holding the last instant *inside* the window. `until` itself
    # is excluded, so a window ending exactly on a boundary does not grow an
    # empty bucket past its own end.
    last = _floor(end - timedelta(microseconds=1), seconds, offset)
    first = last - timedelta(seconds=seconds * (count - 1))
    return Window(since=first, until=end, bucket_seconds=seconds, offset_seconds=offset)


@dataclass(frozen=True, slots=True)
class Bucket:
    """One bucket's tokens, for one scope. `start` is the bucket's first instant."""

    start: datetime
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: How many operations are behind the figures above. An hour with 400 runs
    #: and one with 4 are different facts about the same token count.
    runs: int = 0


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """One model's share of a scope: its total, and its buckets.

    `model` is the name as the run recorded it in `model_snapshot` — what the
    provider was configured to call, not the display name of the config — so
    two configs pointing at the same model are one row. `""` where a row
    recorded no model at all, which the screen names rather than drops: those
    tokens are in the scope's total, and a breakdown that silently omitted
    them would not add up to it.
    """

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    runs: int = 0
    #: As on `Series`: operations on this model that reported no token count.
    unmeasured: int = 0
    #: This model's own buckets, so a screen can chart one model on its own
    #: without a second request. Sparse, like `Series.buckets`.
    buckets: list[Bucket] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class Series:
    """One scope's usage: a total, the buckets it is made of, and the models.

    The invariant the whole screen rests on: **the total equals the sum of the
    buckets, and the sum of the models.** All three are folded from the same
    grouped rows rather than queried separately, so they cannot drift.

    `buckets` is **sparse** — a bucket nothing ran in is absent, not a row of
    zeros. `since`, `until` and `bucket_seconds` are what a screen needs to
    draw the empty ones, and the axis to the window's real end.
    """

    actor_id: UUID | None = None
    #: A display name, never an address. The rule `AuditEntry` already states,
    #: for the same reason: a usage screen answers *"who spent this"* with
    #: something a person recognises, and an email is a personal identifier
    #: the screen has no need of.
    actor: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    runs: int = 0
    #: How many of those operations reported no token count at all. Non-zero
    #: means every figure above understates, and the screen says so.
    unmeasured: int = 0
    since: datetime | None = None
    until: datetime | None = None
    bucket_seconds: int = DAY_SECONDS
    buckets: list[Bucket] = field(default_factory=list)
    #: The same total split by model, busiest first.
    models: list[ModelUsage] = field(default_factory=list)

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

    A run saying a question used 12k tokens is not the same as knowing the
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


# ── the bucket a row belongs to ──────────────────────────────────────────
class bucket_of(FunctionElement[int]):  # noqa: N801 — a SQL function, named like one
    """The epoch second a row's bucket starts at, on the reader's clock.

    `floor((epoch + offset) / width) * width - offset`, compiled per dialect
    because the unit tests run against SQLite, which has no `EXTRACT(EPOCH …)`,
    and a query that only exists in production is a query nothing tests. The
    alternative — bucketing in Python over every row in the window — is the
    unbounded read this module exists to avoid.

    Width and offset arrive as `literal_column`s of integers this module
    computed itself, never caller text: they are part of the statement's cache
    key that way, so a five-minute query never reuses an hourly one's SQL.
    """

    type = BigInteger()
    inherit_cache = True


@compiles(bucket_of)
def _bucket_of_default(element: Any, compiler: Any, **kw: Any) -> str:
    """Postgres, and anything else that speaks `EXTRACT(EPOCH FROM …)`."""
    column, width, offset = (compiler.process(c, **kw) for c in element.clauses)
    return (
        f"CAST(FLOOR((EXTRACT(EPOCH FROM {column}) + {offset}) / {width})"
        f" * {width} - {offset} AS BIGINT)"
    )


@compiles(bucket_of, "sqlite")
def _bucket_of_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    column, width, offset = (compiler.process(c, **kw) for c in element.clauses)
    return (
        f"((CAST(strftime('%s', {column}) AS INTEGER) + {offset}) / {width})"
        f" * {width} - {offset}"
    )


def _as_instant(value: Any) -> datetime:
    """One bucket key, however the driver handed it back, as a UTC instant."""
    return datetime.fromtimestamp(int(value), UTC)


# ── the union the three aggregations read ────────────────────────────────
#: The three tables that record what a model call used, and nothing else reads
#: as usage. Each contributes the same five columns, so the union is one shape
#: and a fourth source is a row here rather than a rewrite.
#:
#: `runs` is a chat question, `report_runs` one generated document, and
#: `semantic_jobs` one layer generation. They are counted together because the
#: question is *"how much did this person's use of the product consume?"*, and a
#: screen that answered it for chat alone would understate every person who
#: writes reports.
_SOURCES = (Run, ReportRun, SemanticJobRow)


def _arm(model: Any, window: Window) -> sa.Select[Any]:
    """One table's contribution to the union, as the five columns it shares.

    The model is read out of `model_snapshot` — the frozen copy of the config
    each row was run with — rather than joined through `llm_config_id`, which
    is `SET NULL` when a config is deleted and would move a finished run's
    tokens under whatever the config is renamed or repointed to later.
    Coalesced to `""` so a row without one groups with the others that lack it
    instead of splitting into a NULL group and an empty-string group.
    """
    return sa.select(
        model.actor_id.label("actor_id"),
        bucket_of(
            model.created_at,
            sa.literal_column(str(int(window.bucket_seconds))),
            sa.literal_column(str(int(window.offset_seconds))),
        ).label("bucket"),
        sa.func.coalesce(model.model_snapshot["model"].as_string(), "").label("model"),
        model.prompt_tokens.label("prompt_tokens"),
        model.completion_tokens.label("completion_tokens"),
    ).where(
        model.created_at >= window.since,
        model.created_at < window.until,
    )


def _operations(window: Window) -> sa.Subquery:
    """Every operation in the window, from all three tables.

    `UNION ALL`, never `UNION`: two runs that happened to use the same tokens
    in the same bucket are two operations, and deduplicating them would
    silently halve a busy hour.
    """
    return sa.union_all(*(_arm(model, window) for model in _SOURCES)).subquery(
        "operations"
    )


#: The four aggregates every scope computes, over whichever subquery it reads.
#:
#: `SUM` ignores nulls in SQL, which is exactly what the carried-over rule
#: wants — an unmeasured row contributes nothing rather than zero. What cannot
#: be left to `SUM` is *noticing*, so the count below counts the rows that
#: contributed nothing, and the screen turns it into the sentence that says
#: how partial the total is.
def _aggregates(source: Any) -> list[ColumnElement[Any]]:
    return [
        sa.func.coalesce(sa.func.sum(source.c.prompt_tokens), 0).label(
            "prompt_tokens"
        ),
        sa.func.coalesce(sa.func.sum(source.c.completion_tokens), 0).label(
            "completion_tokens"
        ),
        sa.func.count().label("runs"),
        # Measured by the absence of *both* counts: a row reporting prompt
        # tokens and no completion tokens is measured, just oddly.
        sa.func.count()
        .filter(
            source.c.prompt_tokens.is_(None), source.c.completion_tokens.is_(None)
        )
        .label("unmeasured"),
    ]


class _Tally:
    """A running sum of one group's four aggregates."""

    __slots__ = ("completion", "prompt", "runs", "unmeasured")

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.runs = 0
        self.unmeasured = 0

    def add(self, row: Any) -> None:
        self.prompt += int(row.prompt_tokens or 0)
        self.completion += int(row.completion_tokens or 0)
        self.runs += int(row.runs or 0)
        self.unmeasured += int(row.unmeasured or 0)

    def bucket(self, start: datetime) -> Bucket:
        return Bucket(
            start=start,
            prompt_tokens=self.prompt,
            completion_tokens=self.completion,
            runs=self.runs,
        )


def _series(
    actor_id: UUID | None, actor: str, rows: Sequence[Any], window: Window
) -> Series:
    """Fold one scope's `(bucket, model)` rows into its total, buckets and models.

    Every figure is summed from the same rows, so the invariant the screen
    rests on — **the total equals the sum of the buckets, and of the models** —
    holds by construction rather than by three additions agreeing.
    """
    total = _Tally()
    by_bucket: dict[int, _Tally] = {}
    by_model: dict[str, _Tally] = {}
    model_buckets: dict[str, dict[int, _Tally]] = {}

    for row in rows:
        key = int(row.bucket)
        name = row.model or ""
        total.add(row)
        by_bucket.setdefault(key, _Tally()).add(row)
        by_model.setdefault(name, _Tally()).add(row)
        model_buckets.setdefault(name, {}).setdefault(key, _Tally()).add(row)

    def buckets(tallies: dict[int, _Tally]) -> list[Bucket]:
        return [tallies[key].bucket(_as_instant(key)) for key in sorted(tallies)]

    models = [
        ModelUsage(
            model=name,
            prompt_tokens=tally.prompt,
            completion_tokens=tally.completion,
            runs=tally.runs,
            unmeasured=tally.unmeasured,
            buckets=buckets(model_buckets[name]),
        )
        for name, tally in by_model.items()
    ]
    # Busiest first, ranked here rather than by the screen so every caller of
    # the API gets the same order. Ties fall back to operations and then to
    # the name, so the list is stable between reads of the same window.
    models.sort(key=lambda m: (-m.total_tokens, -m.runs, m.model))

    return Series(
        actor_id=actor_id,
        actor=actor,
        prompt_tokens=total.prompt,
        completion_tokens=total.completion,
        runs=total.runs,
        unmeasured=total.unmeasured,
        since=window.since,
        until=window.until,
        bucket_seconds=window.bucket_seconds,
        buckets=buckets(by_bucket),
        models=models,
    )


async def for_actor(
    db: AsyncSession, actor_id: UUID, *, window: Window
) -> Series:
    """One person's usage, by bucket and by model.

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
    return _series(actor_id, name or "", [], window)


async def per_actor(
    db: AsyncSession, *, window: Window, only: UUID | None = None
) -> list[Series]:
    """Everybody's usage, by bucket and by model, one `Series` each.

    One grouped query rather than a query per person: the screen renders a list
    of people with a chart each, and a loop of per-person reads is the
    pagination problem the access-control rulebook names.

    The join to `users` is **inner** — a deleted actor's rows leave this view
    and stay in `installation`. An outer join would attribute a departed
    person's spend to whoever remains, which is the one answer that is wrong.
    """
    operations = _operations(window)
    conditions: list[ColumnElement[bool]] = [operations.c.actor_id.is_not(None)]
    if only is not None:
        conditions.append(operations.c.actor_id == only)

    grouped = (
        sa.select(
            operations.c.actor_id,
            User.display_name.label("actor"),
            operations.c.bucket,
            operations.c.model,
            *_aggregates(operations),
        )
        .join(User, User.id == operations.c.actor_id)
        .where(*conditions)
        .group_by(
            operations.c.actor_id,
            User.display_name,
            operations.c.bucket,
            operations.c.model,
        )
        .order_by(User.display_name)
    )

    rows: dict[UUID, list[Any]] = {}
    names: dict[UUID, str] = {}
    for row in (await db.execute(grouped)).all():
        rows.setdefault(row.actor_id, []).append(row)
        names[row.actor_id] = row.actor or ""

    return [
        _series(actor_id, names[actor_id], actor_rows, window)
        for actor_id, actor_rows in rows.items()
    ]


async def installation(db: AsyncSession, *, window: Window) -> InstallationSeries:
    """What the whole installation used, by bucket and by model.

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
    grouped = sa.select(
        operations.c.bucket, operations.c.model, *_aggregates(operations)
    ).group_by(operations.c.bucket, operations.c.model)
    base = _series(None, "", (await db.execute(grouped)).all(), window)

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
        runs=base.runs,
        unmeasured=base.unmeasured,
        since=base.since,
        until=base.until,
        bucket_seconds=base.bucket_seconds,
        buckets=base.buckets,
        models=base.models,
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
