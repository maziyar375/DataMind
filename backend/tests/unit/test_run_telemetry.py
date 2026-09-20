"""What retrieval did, recorded on the run — and what the next question reads.

Phase 3 of `docs/plans/retrieval-sections.md` §2.2. Four columns on `runs`
answering the two questions nothing in this product could answer before:
*how often does a real connection take each strategy*, and *how big does the
schema block get*. They are written in one place, `_finalise`, from the
context the pipeline actually built.

Two rules carry the file:

* **Null is not zero.** A run answered from the knowledge store never reaches
  `retrieve`, and four zeroes beside it would read as a measurement of a block
  that was never built.
* **A follow-up keeps its section**, and it reads it from here — the last turn
  that retrieved anything, on this thread and this connection. A run that
  failed before `retrieve` recorded no strategy and is not evidence; a run
  answered from the whole database recorded one and is.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from uuid import UUID, uuid4

from app.api.v1.conversations import _hydrate_run
from app.core.clock import utcnow
from app.core.config import Settings
from app.domain.value_objects import RunStatus
from app.infra.db.models import Conversation, Message, Run
from app.pipeline.state import RetrievedContext, RunState
from app.services.run_service import RunService
from tests.unit.conftest import ACTOR, AsyncSessionShim, _connection

TABLES = [
    {"schema": "public", "name": "orders", "approx_row_count": 12, "columns": [
        {"name": "id", "data_type": "bigint", "is_primary_key": True},
        {"name": "total_amount", "data_type": "numeric"},
    ]},
    {"schema": "public", "name": "order_items", "approx_row_count": 40, "columns": [
        {"name": "order_id", "data_type": "bigint"},
    ]},
]


def _service(db: AsyncSessionShim) -> RunService:
    return RunService(db, Settings())  # type: ignore[arg-type]


def _thread(db: AsyncSessionShim, connection_id: UUID) -> Conversation:
    session = db._session
    thread = Conversation(
        id=uuid4(), owner_id=ACTOR, title="Q3", status="ACTIVE",
        default_connection_id=connection_id,
    )
    session.add(thread)
    session.flush()
    return thread


#: Turns are ordered by `created_at`, so each row written here gets its own
#: second. Postgres stamps microseconds and never ties; SQLite stamps whole
#: seconds, and a test about *which turn was last* must not depend on how a
#: tie happens to break.
_MINUTE = [0]


def _run(
    db: AsyncSessionShim,
    thread: Conversation,
    connection_id: UUID | None,
    *,
    status: str = RunStatus.RUNNING,
    strategy: str | None = None,
    sections: list[str] | None = None,
) -> Run:
    session = db._session
    _MINUTE[0] += 1
    question = Message(
        id=uuid4(), conversation_id=thread.id, seq=uuid4().int % 10_000,
        role="USER", content="revenue?",
    )
    session.add(question)
    session.flush()
    run = Run(
        id=uuid4(), conversation_id=thread.id, user_message_id=question.id,
        owner_id=ACTOR, actor_id=ACTOR, connection_id=connection_id,
        status=status, model_snapshot={"model": "gpt"}, prompt_version="v10",
        started_at=utcnow(),
        created_at=utcnow() + timedelta(minutes=_MINUTE[0]),
        retrieval_strategy=strategy, retrieval_sections=sections,
    )
    session.add(run)
    session.flush()
    return run


def _state(run: Run, *, context: RetrievedContext | None, sections: list[str]) -> RunState:
    state = RunState(
        run_id=run.id, conversation_id=run.conversation_id, owner_id=ACTOR,
        connection_id=run.connection_id or uuid4(), question="revenue?",
        disclosure_policy="NONE",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.context = context
    state.scope_sections = sections
    return state


# ── the four columns ─────────────────────────────────────────────────────
async def test_a_scoped_run_records_what_it_retrieved(db: AsyncSessionShim) -> None:
    connection = _connection(db._session, owner_id=ACTOR)
    run = _run(db, _thread(db, connection.id), connection.id)
    context = RetrievedContext(
        dialect="postgres", tables=TABLES, strategy="SECTION_SNAPSHOT"
    )

    await _service(db)._finalise(run, _state(run, context=context, sections=["Sales"]))

    assert run.retrieval_strategy == "SECTION_SNAPSHOT"
    assert run.retrieval_sections == ["Sales"]
    assert run.retrieval_tables == 2
    # The block as the model received it, under the policy in force — the same
    # call `generate` makes, so this is the cost and not an estimate of it.
    assert run.retrieval_chars == len(context.render("NONE"))
    assert run.retrieval_chars > 0


async def test_the_whole_database_is_a_measurement_too(db: AsyncSessionShim) -> None:
    """A strategy with no sections is not the same fact as no strategy: it
    says this question was answered from everything, which is what the
    distribution is about."""
    connection = _connection(db._session, owner_id=ACTOR)
    run = _run(db, _thread(db, connection.id), connection.id)

    await _service(db)._finalise(
        run,
        _state(
            run,
            context=RetrievedContext(dialect="postgres", tables=TABLES),
            sections=[],
        ),
    )

    assert run.retrieval_strategy == "FULL_SNAPSHOT"
    assert run.retrieval_sections == []
    assert run.retrieval_tables == 2


async def test_a_run_that_never_retrieved_records_nothing(db: AsyncSessionShim) -> None:
    """Answered from the knowledge store, or failed before `retrieve`. Null is
    "no measurement", and a zero here would claim an empty block was built."""
    connection = _connection(db._session, owner_id=ACTOR)
    run = _run(db, _thread(db, connection.id), connection.id)

    await _service(db)._finalise(run, _state(run, context=None, sections=[]))

    assert run.retrieval_strategy is None
    assert run.retrieval_sections is None
    assert run.retrieval_tables is None
    assert run.retrieval_chars is None


# ── what the next question is told ───────────────────────────────────────
async def test_the_previous_turns_sections_are_the_current_ones(
    db: AsyncSessionShim,
) -> None:
    connection = _connection(db._session, owner_id=ACTOR)
    thread = _thread(db, connection.id)
    _run(db, thread, connection.id, status=RunStatus.SUCCEEDED,
         strategy="SECTION_SNAPSHOT", sections=["Sales"])
    asking = _run(db, thread, connection.id)

    assert await _service(db)._current_sections(asking) == ["Sales"]


async def test_a_turn_answered_from_everything_clears_them(
    db: AsyncSessionShim,
) -> None:
    """The thread has left its section, and the next question must not be
    told it is still in one."""
    connection = _connection(db._session, owner_id=ACTOR)
    thread = _thread(db, connection.id)
    _run(db, thread, connection.id, status=RunStatus.SUCCEEDED,
         strategy="SECTION_SNAPSHOT", sections=["Sales"])
    _run(db, thread, connection.id, status=RunStatus.SUCCEEDED,
         strategy="FULL_SNAPSHOT", sections=[])
    asking = _run(db, thread, connection.id)

    assert await _service(db)._current_sections(asking) == []


async def test_a_run_that_recorded_no_retrieval_is_not_evidence(
    db: AsyncSessionShim,
) -> None:
    """A crash between `match` and `retrieve` wrote no strategy. It says
    nothing about where the thread is, so the turn before it still does."""
    connection = _connection(db._session, owner_id=ACTOR)
    thread = _thread(db, connection.id)
    _run(db, thread, connection.id, status=RunStatus.SUCCEEDED,
         strategy="SECTION_SNAPSHOT", sections=["Sales"])
    _run(db, thread, connection.id, status=RunStatus.FAILED)
    asking = _run(db, thread, connection.id)

    assert await _service(db)._current_sections(asking) == ["Sales"]


async def test_another_connections_turn_is_not_context_for_this_one(
    db: AsyncSessionShim,
) -> None:
    """The same fail-closed reading `_recent_history` uses: a section name
    belongs to the database it was defined on."""
    session = db._session
    connection = _connection(session, owner_id=ACTOR)
    other = _connection(session, owner_id=ACTOR)
    thread = _thread(db, connection.id)
    _run(db, thread, other.id, status=RunStatus.SUCCEEDED,
         strategy="SECTION_SNAPSHOT", sections=["Elsewhere"])
    asking = _run(db, thread, connection.id)

    assert await _service(db)._current_sections(asking) == []


async def test_a_first_question_has_no_current_section(db: AsyncSessionShim) -> None:
    connection = _connection(db._session, owner_id=ACTOR)
    asking = _run(db, _thread(db, connection.id), connection.id)

    assert await _service(db)._current_sections(asking) == []


# ── on the wire: the chip's one fact ─────────────────────────────────────
async def test_the_turn_carries_the_sections_it_was_answered_from(
    db: AsyncSessionShim,
) -> None:
    """What *Answered from Sales* is drawn from. A run from before `0036` has
    NULL here and reads as an empty list — the chip draws nothing either
    way, and no client should have to tell the two apart."""
    connection = _connection(db._session, owner_id=ACTOR)
    thread = _thread(db, connection.id)
    answered = _run(db, thread, connection.id, status=RunStatus.SUCCEEDED,
                    strategy="SECTION_SNAPSHOT", sections=["Sales", "People"])
    older = _run(db, thread, connection.id, status=RunStatus.SUCCEEDED)

    assert (await _hydrate_run(db, answered)).retrieval_sections == ["Sales", "People"]
    assert (await _hydrate_run(db, older)).retrieval_sections == []


async def test_a_withheld_turn_does_not_name_the_section(
    db: AsyncSessionShim,
) -> None:
    """The intersection rule: this reader may see the transcript and not the
    database it was asked against, and a section name is a name somebody gave
    part of that database."""
    connection = _connection(db._session, owner_id=ACTOR)
    thread = _thread(db, connection.id)
    run = _run(db, thread, connection.id, status=RunStatus.SUCCEEDED,
               strategy="SECTION_SNAPSHOT", sections=["Sales"])

    data = await _hydrate_run(db, run, may_read_data=False)

    assert data.restricted is True
    assert data.retrieval_sections == []


# ── the choice, from the composer to the row ─────────────────────────────
def test_the_ask_route_carries_the_choice_to_the_run() -> None:
    """*Ask within…* is a durable per-run request, like *Generate a fresh
    answer instead*: the replica that executes this run is not necessarily
    the one that accepted it."""
    from app.api.schemas import MessageCreate
    from app.api.v1 import conversations

    assert "scope" in MessageCreate.model_fields
    assert "scope_choice=payload.scope" in inspect.getsource(conversations.post_message)
    assert "scope_choice=run.scope_choice" in inspect.getsource(
        RunService.execute_run
    )
