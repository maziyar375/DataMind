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

**Migration 0014 gave `None` a second meaning.** Before it, a connection could
not be deleted once any run had used it, so a null default meant "nothing chosen
yet" and adopting whatever arrived was the kind reading. Now
`conversations.default_connection_id` is released by `ON DELETE SET NULL` when
the database is deleted, so a null default on a thread that has *already spoken*
means its database is gone — and adopting a replacement would continue one
database's conversation against another, which is precisely what the pin exists
to stop, arriving through the back door. The transcript stays readable; it
cannot be added to. `transcript_empty` is what tells the two apart.
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


def test_a_conversation_that_has_said_nothing_yet_adopts_rather_than_refuses() -> None:
    """A thread with no default and no transcript has nothing to contradict,
    and refusing would make it permanently unusable."""
    conversation = FakeConversation(None)
    connection = uuid4()

    _bind_connection(conversation, connection, transcript_empty=True)

    assert conversation.default_connection_id == connection


def test_a_conversation_whose_database_was_deleted_cannot_be_continued() -> None:
    """The case that replaced the old "unbound rows adopt" rule.

    Asked for directly: a chat whose database has been deleted was still
    answering, because the null left by `ON DELETE SET NULL` read as "never
    bound" and the next message re-bound the thread to whatever the picker
    offered. Nothing leaked — `_recent_turns` drops turns it cannot attribute to
    the connection now asking — but the thread carried on as though its database
    were still there, which is not something a user can be expected to notice.
    """
    conversation = FakeConversation(None)

    with pytest.raises(ValidationError) as raised:
        _bind_connection(conversation, uuid4(), transcript_empty=False)

    assert "deleted" in str(raised.value)
    assert conversation.default_connection_id is None  # not adopted on the way out
