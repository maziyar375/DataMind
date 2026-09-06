"""Who a run's tokens belong to, and what they cost.

`actor_id` answers *who asked*, as against `owner_id`'s *who owns the thing
asked about*. They are the same person in every row that exists today —
`create_run` refuses a conversation the caller does not own — and that is
precisely why the column is added now rather than after connections can be
shared: the backfill is `owner_id`, which is correct, instead of a guess about
a past nobody recorded.

`cost_usd` is best-effort by construction. `estimate_cost_usd` returns None for
a model litellm cannot price, which is the *normal* state for a self-hosted
deployment — so the property worth pinning is not that a cost appears, but that
an unknown one stays null and is never quietly rounded down to free.

Phase 5 of [docs/token-accounting-plan.md](../../../docs/token-accounting-plan.md)
widens this to report runs and semantic jobs; this file covers the chat run,
which is the only one Phase 3 touches.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.domain.ports.llm import Usage
from app.infra.db.models import Run
from app.pipeline.state import RunState
from app.services.run_service import _priced_model
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


def _state(**buckets: Usage) -> RunState:
    from datetime import timedelta

    from app.core.clock import utcnow

    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="q", dialect="postgres",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    for node, usage in buckets.items():
        state.record_usage(node, usage)
    return state


def _run(model: str | None) -> Any:
    return Run(
        id=uuid4(), conversation_id=uuid4(), user_message_id=uuid4(),
        owner_id=uuid4(), model_snapshot=({"model": model} if model else {}),
    )


def test_a_run_is_priced_under_the_name_the_gateway_actually_sent() -> None:
    """The resolved name, not the one the row spells.

    `openai/qwen-2.5` is what reached litellm and therefore what its price map
    is keyed on; `qwen-2.5` is what the provider row says and what a reader
    should be shown. Costing the second would miss the entry.
    """
    state = _state(route=Usage(prompt_tokens=10, model="openai/qwen-2.5"))
    assert _priced_model(state, _run("qwen-2.5")) == "openai/qwen-2.5"


def test_pricing_falls_back_to_the_snapshot_when_no_call_reported_a_name() -> None:
    """Every call streamed and the provider sent no usage: the run is still named.

    The fallback costs nothing — `estimate_cost_usd` over zero tokens returns
    None either way — and it keeps a run costable rather than anonymous.
    """
    assert _priced_model(_state(), _run("qwen-2.5")) == "qwen-2.5"


def test_a_run_with_no_model_name_anywhere_prices_as_unknown_not_as_wrong() -> None:
    """An empty name looks up nothing, which is the honest answer."""
    assert _priced_model(_state(), _run(None)) == ""


def test_an_unpriceable_model_leaves_the_cost_null() -> None:
    """The self-hosted case, and the rule every reader of the column inherits.

    `estimate_cost_usd` returns None for a model litellm does not know. Nothing
    on the write path may turn that into `0.0`: a real spend reported as free
    is worse than one reported as unknown, because only the second is visibly
    missing.
    """
    from app.infra.llm.litellm_gateway import estimate_cost_usd

    assert estimate_cost_usd("a-local-model-nobody-prices", 1000, 500) is None


def test_zero_tokens_never_manufacture_a_cost() -> None:
    """A run whose provider reported nothing is unpriced, not free.

    `estimate_cost_usd` returns None on a total of zero, which is what keeps
    the streamed-usage gap (§1.3 of the plan) visible as a null instead of
    averaging into every figure that touches it.
    """
    from app.infra.llm.litellm_gateway import estimate_cost_usd

    assert estimate_cost_usd("gpt-4o-mini", 0, 0) is None
