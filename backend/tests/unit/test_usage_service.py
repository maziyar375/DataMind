"""How many tokens the models used, and the three ways a usage screen lies about it.

Every test here is one of the failure modes `usage_service`'s docstring names.
Two of them are worth stating up front because they are what the file is
really for:

* **A null summed as zero** makes an unmeasured run read as no work and an
  average read as low, and it is the single most likely mistake in an
  aggregation over columns that are nullable on purpose.
* **An outer join "fixing" the departed-actor gap** attributes a former
  employee's spend to whoever remains. So the gap is asserted as *present* —
  a test that only checked the totals added up would pass with the wrong join.

The fixtures write the real tables through the ORM and the service runs its
real SQL against them, for `conftest.py`'s reason: a fake that answered queries
by inspecting statements would prove nothing about the statements, which are
the thing under test.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.infra.db.models import (
    Conversation,
    DatabaseConnection,
    Message,
    Report,
    ReportRun,
    Run,
    RunStep,
    SemanticJobRow,
    User,
)
from app.services import usage_service as usage

# `db` and its engine come from `tests/unit/conftest.py`.
from tests.unit.conftest import AsyncSessionShim

#: Every row in this file is written relative to this instant, so a test that
#: asserts a bucket's date is asserting arithmetic rather than today's date.
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

#: The window every test reads through unless it is testing the clamp itself.
#:
#: `until` is an hour **past** `NOW` because the range is half-open: a row
#: written at exactly `until` is outside it, by design, and a fixture that put
#: its rows on that boundary would be testing the boundary in every test rather
#: than the thing each one is about. A week and an hour is six-hour buckets.
WINDOW = usage.clamp_window(
    since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1), now=NOW
)


async def _person(db: AsyncSessionShim, name: str) -> UUID:
    """A real row, flushed: the fixture enforces foreign keys."""
    person = uuid4()
    db.add(
        User(
            id=person, email=f"{name.lower()}@test.local", display_name=name,
            password_hash="x", status="ACTIVE", kind="HUMAN",
        )
    )
    await db.flush()
    return person


async def _chat(
    db: AsyncSessionShim,
    actor: UUID,
    *,
    prompt: int | None = 100,
    completion: int | None = 10,
    model: str | None = "gpt-4o-mini",
    day: datetime | None = None,
    owner: UUID | None = None,
) -> UUID:
    """One `runs` row, with the conversation and message it needs.

    The FK order is the one `CLAUDE.md` warns about: the message is flushed
    before the run that references it.
    """
    conversation = uuid4()
    db.add(Conversation(id=conversation, owner_id=owner or actor, title="t"))
    await db.flush()

    message = uuid4()
    db.add(
        Message(id=message, conversation_id=conversation, seq=0, role="user", content="q")
    )
    await db.flush()

    run = uuid4()
    db.add(
        Run(
            id=run, conversation_id=conversation,
            user_message_id=message,
            # `owner` defaults to the actor, which is every row today. They
            # differ once a connection is shared, and that is the case the
            # departed-actor test needs: `runs.owner_id` cascades through
            # `conversations`, while `actor_id` is SET NULL.
            owner_id=owner or actor, actor_id=actor, status="SUCCEEDED",
            prompt_tokens=prompt, completion_tokens=completion,
            model_snapshot={} if model is None else {"model": model},
            created_at=day or NOW,
        )
    )
    await db.flush()
    return run


async def _report_run(
    db: AsyncSessionShim, actor: UUID, *, prompt: int = 700, model: str = "gpt-4o-mini"
) -> None:
    report = uuid4()
    db.add(Report(id=report, owner_id=actor, name=f"r-{report.hex[:8]}"))
    await db.flush()
    db.add(
        ReportRun(
            id=uuid4(), report_id=report, owner_id=actor, actor_id=actor,
            status="SUCCEEDED", prompt_tokens=prompt, completion_tokens=70,
            model_snapshot={"model": model}, created_at=NOW,
        )
    )
    await db.flush()


async def _semantic_job(
    db: AsyncSessionShim, actor: UUID, *, prompt: int = 300, model: str = "gpt-4o-mini"
) -> None:
    connection = uuid4()
    db.add(
        DatabaseConnection(
            id=connection, owner_id=actor, name=f"c-{connection.hex[:8]}",
            database_type="postgres", host="h", port=5432, database_name="d",
            username="u", encrypted_password="x",
        )
    )
    await db.flush()
    db.add(
        SemanticJobRow(
            id=uuid4(), connection_id=connection, owner_id=actor, actor_id=actor,
            status="SUCCEEDED", prompt_tokens=prompt, completion_tokens=30,
            model_snapshot={"model": model}, created_at=NOW,
        )
    )
    await db.flush()


# ── the invariant the whole screen rests on ──────────────────────────────
@pytest.mark.asyncio
async def test_a_total_equals_the_sum_of_its_buckets(db: AsyncSessionShim) -> None:
    """The one most likely to rot, because it holds by construction today.

    Both figures come from the same rows in the same query rather than from two
    additions, and this is what would notice if somebody split them.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, day=NOW)
    await _chat(db, ali, prompt=250, completion=25, day=NOW - timedelta(days=2))
    await _chat(db, ali, prompt=40, completion=4, day=NOW - timedelta(days=2))

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == sum(b.prompt_tokens for b in series.buckets)
    assert series.completion_tokens == sum(b.completion_tokens for b in series.buckets)
    assert series.runs == sum(b.runs for b in series.buckets)
    assert series.prompt_tokens == 390
    # Two buckets, not three rows: the one two days back carries two of them.
    assert len(series.buckets) == 2


@pytest.mark.asyncio
async def test_a_buckets_rows_land_in_one_bar(db: AsyncSessionShim) -> None:
    """A week is six-hour buckets, so two questions two hours apart are one bar."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=10, completion=1, day=NOW - timedelta(hours=1))
    await _chat(db, ali, prompt=20, completion=2, day=NOW - timedelta(hours=3))

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.bucket_seconds == 6 * 3600
    assert len(series.buckets) == 1
    assert series.buckets[0].runs == 2
    assert series.buckets[0].prompt_tokens == 30
    assert series.buckets[0].start == datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_an_hour_is_twelve_five_minute_buckets_and_rows_land_in_theirs(
    db: AsyncSessionShim,
) -> None:
    ali = await _person(db, "Ali")
    until = NOW + timedelta(minutes=1)
    window = usage.clamp_window(since=until - timedelta(hours=1), until=until, now=until)
    await _chat(db, ali, prompt=10, completion=1, day=NOW - timedelta(minutes=2))
    await _chat(db, ali, prompt=20, completion=2, day=NOW - timedelta(minutes=31))
    await _chat(db, ali, prompt=40, completion=4, day=NOW - timedelta(minutes=90))

    series = await usage.for_actor(db, ali, window=window)

    assert window.bucket_seconds == 300
    assert series.prompt_tokens == 30, "the row from ninety minutes ago is outside"
    assert [(b.start.hour, b.start.minute) for b in series.buckets] == [(11, 25), (11, 55)]


@pytest.mark.asyncio
async def test_a_day_bucket_starts_at_the_readers_midnight_not_greenwichs(
    db: AsyncSessionShim,
) -> None:
    """Tehran is +03:30. A question asked at 22:00 UTC is the next day there."""
    ali = await _person(db, "Ali")
    late = datetime(2026, 9, 12, 22, 0, tzinfo=UTC)
    await _chat(db, ali, prompt=10, completion=1, day=late)
    window = usage.clamp_window(
        since=NOW - timedelta(days=30), until=NOW, now=NOW, tz_offset_minutes=210
    )

    (bucket,) = (await usage.for_actor(db, ali, window=window)).buckets

    assert window.bucket_seconds == usage.DAY_SECONDS
    # Local midnight of 13 September, which is 20:30 UTC on the 12th.
    assert bucket.start == datetime(2026, 9, 12, 20, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_each_model_carries_its_own_buckets_summing_to_its_total(
    db: AsyncSessionShim,
) -> None:
    """What lets a chart show one model on its own without another request."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, model="a", day=NOW)
    await _chat(db, ali, prompt=200, completion=20, model="a", day=NOW - timedelta(days=2))
    await _chat(db, ali, prompt=50, completion=5, model="b", day=NOW)

    series = await usage.for_actor(db, ali, window=WINDOW)
    by_name = {m.model: m for m in series.models}

    assert len(by_name["a"].buckets) == 2
    assert sum(b.prompt_tokens for b in by_name["a"].buckets) == by_name["a"].prompt_tokens
    assert [b.prompt_tokens for b in by_name["b"].buckets] == [50]
    # And the models' buckets add back up to the scope's.
    assert sum(b.prompt_tokens for m in series.models for b in m.buckets) == sum(
        b.prompt_tokens for b in series.buckets
    )


# ── a null is never a zero ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_unmeasured_run_adds_nothing_and_is_counted(
    db: AsyncSessionShim,
) -> None:
    """`prompt_tokens IS NULL` is *not measured*, never *no tokens*.

    Summed as zero it would make an average read low and real work read as
    none — and, worse, silently: the total would still look like a number.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10)
    await _chat(db, ali, prompt=None, completion=None)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == 100
    assert series.completion_tokens == 10
    assert series.runs == 2
    assert series.unmeasured == 1


# ── by model ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_scope_is_split_by_model_and_the_split_sums_to_the_total(
    db: AsyncSessionShim,
) -> None:
    """A second grouping of the same rows, so it adds up to the same figure.

    Busiest first, which is the order a reader scans a breakdown in.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, model="small")
    await _chat(db, ali, prompt=900, completion=90, model="large")
    await _chat(db, ali, prompt=500, completion=50, model="large")

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert [m.model for m in series.models] == ["large", "small"]
    assert series.models[0].prompt_tokens == 1400
    assert series.models[0].runs == 2
    assert sum(m.total_tokens for m in series.models) == (
        series.prompt_tokens + series.completion_tokens
    )
    assert sum(m.runs for m in series.models) == series.runs


@pytest.mark.asyncio
async def test_the_same_model_is_one_row_across_all_three_tables(
    db: AsyncSessionShim,
) -> None:
    """Chat, reports and layer generations on one model are one line, not three."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, model="shared")
    await _report_run(db, ali, prompt=700, model="shared")
    await _semantic_job(db, ali, prompt=300, model="other")

    series = await usage.for_actor(db, ali, window=WINDOW)

    by_name = {m.model: m for m in series.models}
    assert set(by_name) == {"shared", "other"}
    assert by_name["shared"].prompt_tokens == 800
    assert by_name["shared"].runs == 2


@pytest.mark.asyncio
async def test_a_run_with_no_recorded_model_is_kept_under_an_empty_name(
    db: AsyncSessionShim,
) -> None:
    """Dropped, it would leave a breakdown that does not add up to its total."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, model=None)
    await _chat(db, ali, prompt=50, completion=5, model="named")

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert {m.model for m in series.models} == {"", "named"}
    assert sum(m.prompt_tokens for m in series.models) == 150


@pytest.mark.asyncio
async def test_an_unmeasured_run_is_counted_against_its_model(
    db: AsyncSessionShim,
) -> None:
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=None, completion=None, model="quiet")
    await _chat(db, ali, prompt=100, completion=10, model="quiet")

    (model,) = (await usage.for_actor(db, ali, window=WINDOW)).models

    assert model.runs == 2
    assert model.unmeasured == 1
    assert model.prompt_tokens == 100


@pytest.mark.asyncio
async def test_per_actor_splits_each_person_by_their_own_models(
    db: AsyncSessionShim,
) -> None:
    ali = await _person(db, "Ali")
    reza = await _person(db, "Reza")
    await _chat(db, ali, prompt=100, completion=10, model="small")
    await _chat(db, reza, prompt=300, completion=30, model="large")

    people = {s.actor: s for s in await usage.per_actor(db, window=WINDOW)}

    assert [m.model for m in people["Ali"].models] == ["small"]
    assert [m.model for m in people["Reza"].models] == ["large"]


@pytest.mark.asyncio
async def test_the_installation_is_split_by_model_including_departed_actors(
    db: AsyncSessionShim,
) -> None:
    """The total joins nothing, and neither does its model split."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, model="small")
    await _chat(db, ali, prompt=300, completion=30, model="large")
    await db.execute(sa.update(Run).where(Run.prompt_tokens == 300).values(actor_id=None))
    await db.flush()

    total = await usage.installation(db, window=WINDOW)

    assert [m.model for m in total.models] == ["large", "small"]
    assert sum(m.prompt_tokens for m in total.models) == total.prompt_tokens == 400


# ── the departed actor, asserted as a gap rather than trusted to a comment ─
@pytest.mark.asyncio
async def test_deleting_an_actor_drops_them_from_per_actor_and_keeps_the_total(
    db: AsyncSessionShim,
) -> None:
    """The inner-join rule, as behaviour rather than as a comment.

    `actor_id` is `SET NULL` on all three tables, so a deleted person leaves
    rows behind with no actor. Those rows must leave the per-person view —
    nobody can be shown spend attributed to a name that no longer exists — and
    stay in the installation total, which is a fact about the installation and
    not about them.

    **The gap only opens where actor and owner differ**, which is worth knowing
    before reading a production number: `runs.owner_id` cascades through
    `conversations`, and `report_runs.owner_id` cascades directly, so deleting
    somebody who owned what they asked deletes the rows outright and there is
    no spend left to orphan. What survives is exactly what `actor_id` was added
    for — somebody asking through a connection or a thread that belongs to
    someone else.
    """
    ali = await _person(db, "Ali")
    leaving = await _person(db, "Leaving")
    await _chat(db, ali, prompt=100, completion=10)
    # Asked through a thread **Ali** owns, which is what a shared connection
    # looks like and the reason `actor_id` exists as a separate column. A run
    # the leaver also owned would be deleted outright by the cascade through
    # `conversations`, and there would be no spend left to attribute.
    await _chat(db, leaving, prompt=700, completion=70, owner=ali)

    before = await usage.installation(db, window=WINDOW)
    assert before.prompt_tokens == 800
    assert before.unattributed == 0

    # What the foreign keys do when the row goes: `actor_id` is SET NULL.
    await db.execute(
        sa.update(Run).where(Run.actor_id == leaving).values(actor_id=None)
    )
    await db.execute(sa.delete(User).where(User.id == leaving))
    await db.flush()

    people = await usage.per_actor(db, window=WINDOW)
    after = await usage.installation(db, window=WINDOW)

    assert [s.actor for s in people] == ["Ali"]
    assert sum(s.prompt_tokens for s in people) == 100
    # The gap is real, and stated rather than hidden.
    assert after.prompt_tokens == 800
    assert after.unattributed == 1
    assert after.unattributed_tokens == 770


# ── all three tables ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_chat_reports_and_semantic_jobs_land_in_one_series(
    db: AsyncSessionShim,
) -> None:
    """A person who writes reports is not a person who spent nothing.

    The union's three arms, asserted by their sum: a query that silently
    dropped one would still return a plausible number, which is why the
    fixture writes all three rather than trusting the `select` reads right.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10)
    await _report_run(db, ali, prompt=700)
    await _semantic_job(db, ali, prompt=300)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == 1100
    assert series.completion_tokens == 110
    assert series.runs == 3


# ── the window ───────────────────────────────────────────────────────────
def test_a_window_wider_than_the_maximum_is_clamped_not_served() -> None:
    """The union has no LIMIT; the window is what bounds the read."""
    window = usage.clamp_window(since=NOW - timedelta(days=5000), until=NOW, now=NOW)

    assert window.span <= timedelta(days=usage.MAX_WINDOW_DAYS)
    assert window.span > timedelta(days=usage.MAX_WINDOW_DAYS - 1)
    assert window.bucket_seconds == usage.DAY_SECONDS
    assert window.until == NOW


def test_the_default_window_is_thirty_days_of_day_buckets() -> None:
    window = usage.clamp_window(now=NOW)

    assert window.bucket_seconds == usage.DAY_SECONDS
    assert window.until == NOW
    # Thirty buckets, the last of them today.
    assert window.since == datetime(2026, 8, 15, tzinfo=UTC)


def test_a_reversed_window_collapses_rather_than_inverting() -> None:
    """A caller's mistake reads as "no usage", which is true of no time."""
    window = usage.clamp_window(since=NOW, until=NOW - timedelta(days=5), now=NOW)

    assert window.span == timedelta(0)


@pytest.mark.parametrize(
    ("span", "seconds", "buckets"),
    [
        (timedelta(hours=1), 5 * 60, 12),
        (timedelta(hours=6), 15 * 60, 24),
        (timedelta(hours=24), 60 * 60, 24),
        (timedelta(days=7), 6 * 3600, 28),
        (timedelta(days=30), 86_400, 30),
        (timedelta(days=90), 86_400, 90),
    ],
)
def test_every_preset_is_a_whole_number_of_buckets_ending_now(
    span: timedelta, seconds: int, buckets: int
) -> None:
    """The resolution is the server's to choose, and it is chosen from the span.

    And the window ends with the bucket that holds `until`, so the last bar on
    the chart is the one in progress rather than one that ended a while ago.
    """
    until = datetime(2026, 9, 13, 10, 2, tzinfo=UTC)
    window = usage.clamp_window(since=until - span, until=until, now=until)

    assert window.bucket_seconds == seconds
    assert window.until == until
    last_start = window.since + timedelta(seconds=seconds * (buckets - 1))
    assert last_start <= until < last_start + timedelta(seconds=seconds)


def test_the_offset_is_clamped_to_what_a_zone_can_be() -> None:
    window = usage.clamp_window(now=NOW, tz_offset_minutes=10_000)

    assert window.offset_seconds == usage.MAX_OFFSET_MINUTES * 60


@pytest.mark.asyncio
async def test_a_row_outside_the_window_is_not_read(db: AsyncSessionShim) -> None:
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, day=NOW)
    await _chat(db, ali, prompt=999, completion=99, day=NOW - timedelta(days=40))

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == 100
    assert series.runs == 1


# ── nothing there ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_empty_scope_is_a_zero_series_and_never_a_failure(
    db: AsyncSessionShim,
) -> None:
    """A quiet month is an answer, not an error.

    The person is still named, so the screen reads "you, zero" rather than
    having to guess what an empty response meant.
    """
    ali = await _person(db, "Ali")
    await db.flush()

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.actor == "Ali"
    assert series.actor_id == ali
    assert series.prompt_tokens == 0
    assert series.buckets == []
    assert series.models == []
    assert series.runs == 0


@pytest.mark.asyncio
async def test_an_empty_installation_is_a_zero_series(db: AsyncSessionShim) -> None:
    await db.flush()

    total = await usage.installation(db, window=WINDOW)

    assert total.prompt_tokens == 0
    assert total.models == []
    assert total.unattributed == 0
    assert total.buckets == []


# ── per-run attribution ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_by_node_attributes_a_runs_spend_to_the_nodes_that_caused_it(
    db: AsyncSessionShim,
) -> None:
    """And omits the nodes that called no model.

    A run using 12k tokens is not the same fact as the schema block being 9k
    of it, and a zero beside `validate` would read as a measurement rather than
    the absence of one.
    """
    ali = await _person(db, "Ali")
    run = await _chat(db, ali, prompt=9200, completion=110)
    for seq, (name, prompt, completion, calls) in enumerate([
        ("route", 200, 10, 1),
        ("retrieve", None, None, None),
        ("generate", 9000, 100, 1),
        ("validate", None, None, 0),
        ("execute", None, None, None),
    ]):
        db.add(
            RunStep(
                id=uuid4(), run_id=run, seq=seq, name=name, status="SUCCEEDED",
                prompt_tokens=prompt, completion_tokens=completion, llm_calls=calls,
            )
        )
    await db.flush()

    nodes = await usage.by_node(db, run)

    assert [n.name for n in nodes] == ["route", "generate"]
    assert nodes[1].prompt_tokens == 9000
    assert sum(n.total_tokens for n in nodes) == 9310


@pytest.mark.asyncio
async def test_by_node_folds_a_repair_into_one_row(db: AsyncSessionShim) -> None:
    """`generate` twice is one node that was paid for twice.

    Which is exactly why `llm_calls` is a column rather than being derived from
    the step existing.
    """
    ali = await _person(db, "Ali")
    run = await _chat(db, ali)
    for seq, prompt, calls in ((2, 9000, 1), (4, 3000, 1)):
        db.add(
            RunStep(
                id=uuid4(), run_id=run, seq=seq, name="generate", status="SUCCEEDED",
                prompt_tokens=prompt, completion_tokens=50, llm_calls=calls,
            )
        )
    await db.flush()

    nodes = await usage.by_node(db, run)

    assert len(nodes) == 1
    assert nodes[0].llm_calls == 2
    assert nodes[0].prompt_tokens == 12000


@pytest.mark.asyncio
async def test_by_node_on_a_run_with_no_steps_is_empty(db: AsyncSessionShim) -> None:
    ali = await _person(db, "Ali")
    run = await _chat(db, ali)

    assert await usage.by_node(db, run) == []


# ── the per-person list ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_per_actor_returns_one_series_per_person(db: AsyncSessionShim) -> None:
    """One grouped query, not a read per person.

    The screen renders a list of people with a chart each, and a loop of
    per-person reads is the pagination problem the rulebook names.
    """
    ali = await _person(db, "Ali")
    reza = await _person(db, "Reza")
    await _chat(db, ali, prompt=100, completion=10)
    await _chat(db, reza, prompt=300, completion=30)
    await _chat(db, reza, prompt=200, completion=20)

    people = {s.actor: s for s in await usage.per_actor(db, window=WINDOW)}

    assert set(people) == {"Ali", "Reza"}
    assert people["Ali"].prompt_tokens == 100
    assert people["Reza"].prompt_tokens == 500
    assert people["Reza"].runs == 2


@pytest.mark.asyncio
async def test_per_actor_names_people_and_never_addresses(
    db: AsyncSessionShim,
) -> None:
    """A display name, never an email — `AuditEntry`'s rule, same reason.

    A usage screen answers *"who spent this"* with something a person
    recognises; an address is a personal identifier the screen has no need of.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali)

    (series,) = await usage.per_actor(db, window=WINDOW)

    assert series.actor == "Ali"
    assert "@" not in series.actor
