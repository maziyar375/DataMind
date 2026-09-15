"""Who a run's tokens belong to.

`actor_id` answers *who asked*, as against `owner_id`'s *who owns the thing
asked about*. They are the same person in every row that exists today —
`create_run` refuses a conversation the caller does not own — and that is
precisely why the column is added now rather than after connections can be
shared: the backfill is `owner_id`, which is correct, instead of a guess about
a past nobody recorded.

Phase 5 of [docs/plans/token-accounting.md](../../../docs/plans/token-accounting.md)
widens this to report runs and semantic jobs; this file covers the chat run,
which is the only one Phase 3 touches.
"""
from __future__ import annotations

import pytest

from tests.unit.test_prompt_version import OWNER, _create, _settings


@pytest.mark.asyncio
async def test_a_new_run_records_who_asked() -> None:
    """Set from the caller, not left null for a later backfill to invent."""
    run = await _create(_settings())
    assert run.actor_id == OWNER


@pytest.mark.asyncio
async def test_actor_and_owner_agree_while_nothing_can_be_shared() -> None:
    """The claim that makes the migration's backfill correct.

    If this ever fails, `UPDATE runs SET actor_id = owner_id` was the wrong
    backfill for rows written after the change that broke it — which is a
    finding, not a test to relax.
    """
    run = await _create(_settings())
    assert run.actor_id == run.owner_id
