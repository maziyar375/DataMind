"""What a turn cost, on the wire the chat screen reads.

`test_run_token_accounting.py` asserts the same invariant one layer down — a
run's total equals the sum of its steps — against the pipeline, with every
provider faked. This file asserts it **through the serialiser**, because the
two numbers reach the screen by different routes: the run's own total is a
column on `runs`, the steps' are columns on `run_steps`, and the DTO that
carries both is the first place they are shown side by side. A serialiser that
dropped a field, or a `RunStepRead` that quietly defaulted one to `0`, would
leave the pipeline suite entirely green.

The rule the whole phase turns on is that **null is not zero**. Three nodes
make the point and the file has a case for each:

* `validate` and `execute` call no model. All three fields are null, the chip
  renders no number, and a `0` there would read as *this node was free* rather
  than *this is not a question about this node*.
* `generate` calls one and reports. The counts are what the row holds.
* a node can call a provider that sends no usage block — a streamed reply from
  an endpoint that omits it — and that is `llm_calls` with null counts, which
  is again not zero.

There is no dedicated run-detail suite to extend, so this is a file of its own
rather than three cases bolted onto `test_intersection.py`, whose subject is
what a *shared* turn withholds.
"""
from __future__ import annotations

from uuid import uuid4

from app.api.v1.conversations import _hydrate_run
from app.infra.db.models import Conversation, Message, Run, RunStep
from tests.unit.conftest import AsyncSessionShim, _user

#: One node per row: its name, how many calls it made, and what they cost.
#: `validate` and `execute` are the two that call nothing, and they are in the
#: trail of every run — which is why "a step with no numbers" is the ordinary
#: case rather than an edge one.
TRAIL: tuple[tuple[str, int | None, int | None, int | None], ...] = (
    ("route", 1, 11, 1),
    ("retrieve", None, None, None),
    ("generate", 2, 9_000, 300),
    ("validate", None, None, None),
    ("execute", None, None, None),
    ("present", 1, 500, 90),
)


async def _run_with_steps(
    db: AsyncSessionShim,
    trail: tuple[tuple[str, int | None, int | None, int | None], ...] = TRAIL,
    *,
    prompt: int | None = None,
    completion: int | None = None,
) -> Run:
    """A persisted turn, with the run's totals summed from its own steps.

    Summed here rather than written by hand so a case that changes the trail
    cannot forget to change the total it is about to assert against — which
    would make the invariant test pass by agreeing with itself.
    """
    session = db._session
    owner = _user(session, f"owner-{uuid4().hex[:8]}@test.local")
    thread = Conversation(id=uuid4(), owner_id=owner.id, title="Q3", status="ACTIVE")
    session.add(thread)
    session.flush()
    question = Message(
        id=uuid4(), conversation_id=thread.id, seq=1, role="USER", content="revenue?"
    )
    session.add(question)
    session.flush()

    run = Run(
        id=uuid4(), conversation_id=thread.id, user_message_id=question.id,
        owner_id=owner.id, actor_id=owner.id, status="SUCCEEDED",
        model_snapshot={"model": "gpt"}, prompt_version="v9",
        prompt_tokens=(
            prompt
            if prompt is not None
            else sum(p for _n, _c, p, _o in trail if p is not None) or None
        ),
        completion_tokens=(
            completion
            if completion is not None
            else sum(o for _n, _c, _p, o in trail if o is not None) or None
        ),
    )
    session.add(run)
    session.flush()

    for seq, (name, calls, prompt_tokens, completion_tokens) in enumerate(trail):
        session.add(
            RunStep(
                id=uuid4(), run_id=run.id, seq=seq, name=name, status="DONE",
                duration_ms=120, llm_calls=calls,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )
        )
    session.flush()
    return run


def _step(data, name: str):
    return next(s for s in data.steps if s.name == name)


async def test_a_step_that_called_no_model_reports_nothing_at_all(
    db: AsyncSessionShim,
) -> None:
    """`validate` and `execute`, and the whole of why the fields are nullable.

    All three null, not zero. The chip keys on `llm_calls`, so a zero here
    would not only be a false measurement — it would put a `0 tok` on two
    chips in every trail in the product.
    """
    data = await _hydrate_run(db, await _run_with_steps(db))

    for name in ("validate", "execute", "retrieve"):
        step = _step(data, name)
        assert step.llm_calls is None, name
        assert step.prompt_tokens is None, name
        assert step.completion_tokens is None, name


async def test_a_step_that_did_reports_what_the_row_holds(
    db: AsyncSessionShim,
) -> None:
    """The counts pass through unchanged, `llm_calls` included.

    Two calls on one `generate` is the repair case, and it is the reason
    `llm_calls` is on the wire at all: a repaired generation is two calls that
    were both paid for, and nothing about the step's existence says so.
    """
    data = await _hydrate_run(db, await _run_with_steps(db))

    generate = _step(data, "generate")
    assert generate.prompt_tokens == 9_000
    assert generate.completion_tokens == 300
    assert generate.llm_calls == 2


async def test_the_runs_total_equals_the_sum_of_its_steps(
    db: AsyncSessionShim,
) -> None:
    """The invariant the trail's header rests on, asserted over the wire.

    The two numbers are written by different code from the same calls and
    travel to the screen down different columns, so they can disagree — and
    the moment they do, a header saying `9.6k tokens` above chips adding to
    something else makes both figures unbelievable rather than one of them
    wrong.
    """
    data = await _hydrate_run(db, await _run_with_steps(db))

    assert data.prompt_tokens == sum(
        s.prompt_tokens or 0 for s in data.steps
    )
    assert data.completion_tokens == sum(
        s.completion_tokens or 0 for s in data.steps
    )
    # And it is a real sum, not two nulls agreeing.
    assert data.prompt_tokens == 9_511
    assert data.completion_tokens == 391


async def test_a_call_that_reported_no_usage_is_null_and_not_zero(
    db: AsyncSessionShim,
) -> None:
    """A provider that streams without a usage block.

    `llm_calls` is 1 — the call was made and paid for — and the counts are
    null, because nothing measured them. The screen shows the chip with no
    number, which is the honest rendering: `0 tok` on a call that happened is
    the one reading that is definitely false.
    """
    trail = (("present", 1, None, None),)
    run = await _run_with_steps(db, trail, prompt=None, completion=None)

    data = await _hydrate_run(db, run)

    step = _step(data, "present")
    assert step.llm_calls == 1
    assert step.prompt_tokens is None
    assert step.completion_tokens is None
    assert data.prompt_tokens is None
    assert data.completion_tokens is None


async def test_an_old_run_carries_no_totals_and_still_serialises(
    db: AsyncSessionShim,
) -> None:
    """Every turn written before `0023`, which is most of a running
    installation's transcript.

    Nulls throughout and a 200, rather than a validation error on a field the
    row was never able to have. The header prints no total for these and the
    trail is exactly what it was.
    """
    trail = (("route", None, None, None), ("present", None, None, None))
    run = await _run_with_steps(db, trail)

    data = await _hydrate_run(db, run)

    assert data.prompt_tokens is None
    assert data.completion_tokens is None
    assert [s.name for s in data.steps] == ["route", "present"]
    assert all(s.llm_calls is None for s in data.steps)


async def test_the_step_order_is_the_order_they_ran(db: AsyncSessionShim) -> None:
    """A breakdown is read against the chain it came from.

    Asserted here because the chip row is where somebody reads it: `generate`
    costing 9k means something next to `route` costing 12, and alphabetised it
    would mean looking up the pipeline to find out what came first.
    """
    data = await _hydrate_run(db, await _run_with_steps(db))

    assert [s.name for s in data.steps] == [name for name, *_rest in TRAIL]
    assert [s.seq for s in data.steps] == list(range(len(TRAIL)))


async def test_a_withheld_turn_keeps_its_costs(db: AsyncSessionShim) -> None:
    """A shared thread whose database the reader was not given.

    The trail survives the intersection rule — that is what keeps a withheld
    turn reading as a transcript — and the token counts are part of the trail.
    They are counts, not content: no question, no statement and no row is in
    them, which is the same line `usage_service` draws and the reason a usage
    figure is safe where a result is not.
    """
    run = await _run_with_steps(db)

    data = await _hydrate_run(db, run, may_read_data=False)

    assert data.restricted is True
    assert data.artifacts == [] and data.queries == []
    assert _step(data, "generate").prompt_tokens == 9_000
    assert data.prompt_tokens == 9_511
