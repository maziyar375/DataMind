"""*Answer now*, as the running graph sees it.

The durable ask is `runs.answer_now_requested`. This is how it reaches the
loop in the process that holds it, and it arrives by the two roads a cancel
does (`docs/reference/cross-replica.md`): the API route that received the
click sets it directly when the run is here, and the owning process's
heartbeat sets it when the click arrived somewhere else. The graph reads it
on the edge into the next step and on every edge back into `generate` — so
the step in flight finishes, no further step starts, and `synthesize` writes
from what exists.

Process-local on purpose, like the event bus: the flag is only meaningful to
the process executing the run, and the column is what makes it reach that
process. A set of run ids, never cleared by a reader — `forget` is called
when the run is finalised.
"""
from __future__ import annotations

from uuid import UUID

_ANSWER_NOW: set[UUID] = set()


def request_answer_now(run_id: UUID) -> None:
    _ANSWER_NOW.add(run_id)


def answer_now_requested(run_id: UUID) -> bool:
    return run_id in _ANSWER_NOW


def forget(run_id: UUID) -> None:
    _ANSWER_NOW.discard(run_id)
