"""*Retry* is a second run against the same question, not a second question.

The distinction is the whole feature. A reader who stops an answer and asks for
it again has asked one question; posting the text a second time would leave the
thread holding two copies of a sentence they typed once, and every later turn's
history would carry the duplicate into the prompt. So a retry writes a new
`Run` against the **existing `user_message_id`** and nothing else moves.

The rules that keep that honest are what this file pins down. They are cheap to
state and expensive to rediscover: a run still in flight has not been given up
on, a run that answered is re-asked rather than retried (that path is
`override`, and the override rate is a number somebody reads), and a run whose
database has been deleted since cannot be reproduced at all.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.domain.value_objects import RunStatus
from app.services.run_service import RunService

OWNER = uuid.uuid4()


class FakeSession:
    """`get` out of a dict, `add`/`flush` recorded. Nothing else is reached."""

    def __init__(self, rows: dict[Any, Any]) -> None:
        self._rows = rows
        self.added: list[Any] = []

    async def get(self, _model: type, entity_id: Any) -> Any:
        return self._rows.get(entity_id)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


def _world(
    *, status: RunStatus = RunStatus.CANCELLED, answered: bool = False,
    released: str | None = None,
) -> tuple[FakeSession, Any]:
    conn_id = uuid.uuid4()
    llm_id = uuid.uuid4()

    # Stand-ins rather than ORM rows: `retry` reads attributes and builds one
    # real `Run`, and instantiating mapped classes here would be testing
    # SQLAlchemy's instrumentation instead of the rule.
    connection = SimpleNamespace(id=conn_id, owner_id=OWNER, name="Aurora")
    llm = SimpleNamespace(
        id=llm_id, owner_id=OWNER, name="flash", provider="OpenAI-compatible",
        model="m", base_url=None, temperature=0.2, max_tokens=1024,
    )
    run = SimpleNamespace(
        id=uuid.uuid4(), owner_id=OWNER,
        conversation_id=uuid.uuid4(), user_message_id=uuid.uuid4(),
        assistant_message_id=uuid.uuid4() if answered else None,
        # `released` names the FK that `ON DELETE SET NULL` has emptied.
        connection_id=None if released == "connection" else conn_id,
        llm_config_id=None if released == "llm" else llm_id,
        status=status, skip_templates=True,
    )

    rows: dict[Any, Any] = {run.id: run, conn_id: connection, llm_id: llm}
    return FakeSession(rows), run


def _service(session: FakeSession) -> RunService:
    from app.core.config import Settings

    return RunService(session, Settings())  # type: ignore[arg-type]


async def test_a_retry_reuses_the_question_it_is_retrying() -> None:
    session, run = _world()

    retried = await _service(session).retry(run.id, OWNER)

    assert retried.user_message_id == run.user_message_id
    assert retried.conversation_id == run.conversation_id
    assert retried.id != run.id
    assert retried.status == RunStatus.QUEUED
    assert session.added == [retried]


async def test_the_attempt_is_reproduced_not_re_resolved() -> None:
    """Connection and model come from the run being retried, so a retry runs
    against the conditions of the attempt it replaces rather than whatever the
    conversation's picker happens to hold now."""
    session, run = _world()

    retried = await _service(session).retry(run.id, OWNER)

    assert retried.connection_id == run.connection_id
    assert retried.llm_config_id == run.llm_config_id
    assert retried.skip_templates == run.skip_templates
    assert retried.model_snapshot["connection_name"] == "Aurora"


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
async def test_a_run_still_going_is_not_retried(status: RunStatus) -> None:
    session, run = _world(status=status)

    with pytest.raises(ConflictError):
        await _service(session).retry(run.id, OWNER)
    assert session.added == []


async def test_a_run_that_answered_is_re_asked_not_retried() -> None:
    session, run = _world(status=RunStatus.SUCCEEDED, answered=True)

    with pytest.raises(ConflictError):
        await _service(session).retry(run.id, OWNER)


async def test_a_released_database_cannot_be_reproduced() -> None:
    """`SET NULL` on delete, so a run whose connection is gone has no
    conditions left to reproduce — the same refusal a new message gets."""
    session, run = _world(released="connection")

    with pytest.raises(NotFoundError):
        await _service(session).retry(run.id, OWNER)


async def test_somebody_else_s_run_is_not_found() -> None:
    session, run = _world()

    with pytest.raises(NotFoundError):
        await _service(session).retry(run.id, uuid.uuid4())
