"""One conversation, one database.

`create_run` has always accepted a per-message `connection_id` that overrides
the conversation's default, and the run snapshots what it used. History,
however, is keyed on the *conversation*: a thread whose turns ran against two
connections hands one connection's answers to the other's prompt, under the
other's disclosure policy — so a connection set to NONE could be told what a
FULL connection returned, by a path that consults neither policy.

The SPA already locks the connection picker once the transcript is non-empty.
This closes the API route around it, while leaving the switch free on a thread
where nothing has been said yet.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.errors import ValidationError
from app.services.run_service import _bind_connection


class FakeConversation:
    """Only the two attributes `_bind_connection` reads and writes."""

    def __init__(self, default_connection_id: object = None) -> None:
        self.id = uuid4()
        self.default_connection_id = default_connection_id


def test_a_switch_mid_thread_is_refused() -> None:
    first, second = uuid4(), uuid4()
    conversation = FakeConversation(first)

    with pytest.raises(ValidationError):
        _bind_connection(conversation, second, transcript_empty=False)

    assert conversation.default_connection_id == first


def test_a_switch_before_the_first_message_is_adopted() -> None:
    """Nothing has been said, so there is no history to carry across — and the
    conversation must follow the connection it was switched to, or the next
    turn's history filter would drop the turn this one is about to write."""
    first, second = uuid4(), uuid4()
    conversation = FakeConversation(first)

    _bind_connection(conversation, second, transcript_empty=True)

    assert conversation.default_connection_id == second


def test_the_same_connection_is_always_fine() -> None:
    connection = uuid4()
    conversation = FakeConversation(connection)

    _bind_connection(conversation, connection, transcript_empty=False)

    assert conversation.default_connection_id == connection


def test_an_unbound_conversation_adopts_rather_than_refuses() -> None:
    """A thread with no default recorded — an older row, or one created before
    a connection was picked — has nothing to contradict, and refusing would
    make it permanently unusable."""
    conversation = FakeConversation(None)
    connection = uuid4()

    _bind_connection(conversation, connection, transcript_empty=False)

    assert conversation.default_connection_id == connection
