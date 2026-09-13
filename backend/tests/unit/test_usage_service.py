"""What the models cost, and the three ways a usage screen lies about it.

Every test here is one of the failure modes `usage_service`'s docstring names.
Two of them are worth stating up front because they are what the file is
really for:

* **A null summed as zero** makes a real spend read as free and an average read
  as low, and it is the single most likely mistake in an aggregation over
  columns that are nullable on purpose.
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
#: than the thing each one is about.
WINDOW = usage.clamp_window(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1))


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
    cost: float | None = 0.5,
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
            prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost,
            created_at=day or NOW,
        )
    )
    await db.flush()
    return run


async def _report_run(
    db: AsyncSessionShim, actor: UUID, *, prompt: int = 700, cost: float | None = 2.0
) -> None:
    report = uuid4()
    db.add(Report(id=report, owner_id=actor, name=f"r-{report.hex[:8]}"))
    await db.flush()
    db.add(
        ReportRun(
            id=uuid4(), report_id=report, owner_id=actor, actor_id=actor,
            status="SUCCEEDED", prompt_tokens=prompt, completion_tokens=70,
            cost_usd=cost, created_at=NOW,
        )
    )
    await db.flush()


async def _semantic_job(
    db: AsyncSessionShim, actor: UUID, *, prompt: int = 300, cost: float | None = 1.0
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
            cost_usd=cost, created_at=NOW,
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
    # Two days, not three rows: the middle day carries two of them.
    assert len(series.buckets) == 2


@pytest.mark.asyncio
async def test_a_days_rows_land_in_one_bucket(db: AsyncSessionShim) -> None:
    """Bucketing is by day, so two questions an hour apart are one bar."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=10, completion=1, day=NOW - timedelta(hours=1))
    await _chat(db, ali, prompt=20, completion=2, day=NOW - timedelta(hours=3))

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert len(series.buckets) == 1
    assert series.buckets[0].runs == 2
    assert series.buckets[0].prompt_tokens == 30


# ── a null is never a zero ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_unmeasured_run_adds_nothing_and_is_counted(
    db: AsyncSessionShim,
) -> None:
    """`prompt_tokens IS NULL` is *not measured*, never *no tokens*.

    Summed as zero it would make an average read low and a real spend read as
    free — and, worse, silently: the total would still look like a number.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, cost=0.5)
    await _chat(db, ali, prompt=None, completion=None, cost=None)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == 100
    assert series.completion_tokens == 10
    assert series.runs == 2
    assert series.unmeasured == 1


@pytest.mark.asyncio
async def test_an_unpriced_run_adds_nothing_to_cost_and_is_counted(
    db: AsyncSessionShim,
) -> None:
    """Tokens but no price: every self-hosted model, on every row."""
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, cost=0.25)
    await _chat(db, ali, prompt=400, completion=40, cost=None)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == 500
    assert series.cost_usd == pytest.approx(0.25)
    assert series.unpriced == 1
    assert series.unmeasured == 0


@pytest.mark.asyncio
async def test_a_wholly_unpriced_scope_reports_no_cost_rather_than_zero(
    db: AsyncSessionShim,
) -> None:
    """`None` and `0.0` are different claims.

    The first is *no price is knowable*; the second is *it was free*. A screen
    printing "$0.00" for a self-hosted installation would be stating the second.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=100, completion=10, cost=None)
    await _chat(db, ali, prompt=200, completion=20, cost=None)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.cost_usd is None
    assert series.unpriced == 2
    assert series.prompt_tokens == 300


@pytest.mark.asyncio
async def test_an_unmeasured_row_is_not_also_counted_as_unpriced(
    db: AsyncSessionShim,
) -> None:
    """The two counts name different rows, so a screen can add them up.

    A row that measured nothing has no price *because* it measured nothing;
    counting it twice would overstate how much of the cost is missing.
    """
    ali = await _person(db, "Ali")
    await _chat(db, ali, prompt=None, completion=None, cost=None)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.unmeasured == 1
    assert series.unpriced == 0


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
    await _chat(db, ali, prompt=100, completion=10, cost=0.5)
    # Asked through a thread **Ali** owns, which is what a shared connection
    # looks like and the reason `actor_id` exists as a separate column. A run
    # the leaver also owned would be deleted outright by the cascade through
    # `conversations`, and there would be no spend left to attribute.
    await _chat(db, leaving, prompt=700, completion=70, cost=3.5, owner=ali)

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
    await _chat(db, ali, prompt=100, completion=10, cost=0.5)
    await _report_run(db, ali, prompt=700, cost=2.0)
    await _semantic_job(db, ali, prompt=300, cost=1.0)

    series = await usage.for_actor(db, ali, window=WINDOW)

    assert series.prompt_tokens == 1100
    assert series.completion_tokens == 110
    assert series.cost_usd == pytest.approx(3.5)
    assert series.runs == 3


# ── the window ───────────────────────────────────────────────────────────
def test_a_window_wider_than_the_maximum_is_clamped_not_served() -> None:
    """The union has no LIMIT; the window is what bounds the read."""
    window = usage.clamp_window(since=NOW - timedelta(days=5000), until=NOW, now=NOW)

    assert window.days == usage.MAX_WINDOW_DAYS
    assert window.until == NOW


def test_the_default_window_is_thirty_days() -> None:
    window = usage.clamp_window(now=NOW)

    assert window.days == usage.DEFAULT_WINDOW_DAYS
    assert window.until == NOW


def test_a_reversed_window_collapses_rather_than_inverting() -> None:
    """A caller's mistake reads as "no usage", which is true of no days."""
    window = usage.clamp_window(since=NOW, until=NOW - timedelta(days=5), now=NOW)

    assert window.days == 0


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
    assert series.cost_usd is None
    assert series.buckets == []
    assert series.runs == 0


@pytest.mark.asyncio
async def test_an_empty_installation_is_a_zero_series(db: AsyncSessionShim) -> None:
    await db.flush()

    total = await usage.installation(db, window=WINDOW)

    assert total.prompt_tokens == 0
    assert total.cost_usd is None
    assert total.unattributed == 0
    assert total.buckets == []


# ── per-run attribution ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_by_node_attributes_a_runs_spend_to_the_nodes_that_caused_it(
    db: AsyncSessionShim,
) -> None:
    """And omits the nodes that called no model.

    A run costing 12k tokens is not the same fact as the schema block being 9k
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
