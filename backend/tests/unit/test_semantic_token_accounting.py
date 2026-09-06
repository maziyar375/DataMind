"""What a semantic layer build cost, across all of its calls.

A build is the second-most expensive operation in the product — one model call
per table, four at a time, plus an overview and a glossary — and before this
phase it recorded **nothing**. `GenerationStats` had `prompt_tokens` and
`completion_tokens` fields all along; they were never written to, so a 42-table
build reported zero and the columns `semantic_jobs` gained in migration `0023`
had no writer.

Three properties, and the first is the one that makes concurrency safe:

* **the per-table pass is concurrent, so usage cannot be accumulated inside
  it.** Each call collects into a list the caller owns, and the caller folds
  those in under the lock the progress counters already take. A sink writing
  shared counters from four coroutines is the race §1.1 of the plan rejects a
  gateway-instance counter for;
* **every pass is counted** — the overview and the glossary too, not only the
  per-table work that dominates the number;
* **a table whose call failed still costs what the failed calls cost.** A build
  that counted only its successes would understate itself by exactly the
  failures worth knowing about.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import LLMError
from app.domain.ports.llm import Usage
from tests.unit.test_semantic_generator import (
    GOOD_ORDER_ITEMS,
    TABLES,
    ScriptedGateway,
    _run,
)

#: Distinct per pass, so a miscounted call is visible in the number itself
#: rather than only in a total that happens to be wrong by the right amount.
OVERVIEW = (700, 60)
TABLE = (400, 50)
GLOSSARY = (300, 30)


class CountingGateway(ScriptedGateway):
    """The scripted gateway, reporting usage through the sink as a real one does.

    Fires `on_usage` from inside the call rather than letting the test record
    it: the wiring under test *is* that the generator passes a sink at all, and
    a test that counted for itself would pass against a generator that passes
    none.
    """

    def __init__(self, table_reply: Any, **kwargs: Any) -> None:
        super().__init__(table_reply, **kwargs)
        self.usage_calls = 0

    async def structured(self, llm: Any, messages: Any, schema: Any, **kwargs: Any) -> Any:
        name = schema.__name__
        prompt, completion = {
            "_Overview": OVERVIEW,
            "_GlossaryDraft": GLOSSARY,
        }.get(name, TABLE)
        sink = kwargs.get("on_usage")
        if sink is not None:
            self.usage_calls += 1
            sink(Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                latency_ms=5,
                model="gpt-4o-mini",
            ))
        return await super().structured(llm, messages, schema)


def _expected(tables: int) -> tuple[int, int]:
    """Overview + one call per table + glossary."""
    return (
        OVERVIEW[0] + TABLE[0] * tables + GLOSSARY[0],
        OVERVIEW[1] + TABLE[1] * tables + GLOSSARY[1],
    )


@pytest.mark.asyncio
async def test_a_build_records_tokens_across_all_of_its_calls() -> None:
    """The phase gate: this used to be zero, whatever the build did."""
    gateway = CountingGateway(lambda _t: GOOD_ORDER_ITEMS)
    _doc, stats = await _run(gateway)

    prompt, completion = _expected(len(TABLES))
    assert stats.prompt_tokens == prompt
    assert stats.completion_tokens == completion
    assert stats.llm_calls == len(TABLES) + 2  # overview and glossary too
    assert stats.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_every_call_the_gateway_made_was_offered_a_sink() -> None:
    """No pass counts itself out.

    `llm_calls` counting right would still be satisfied by a pass that made a
    call and reported nothing; this asserts the sink reached every one of them,
    which is what stops a fourth pass being added without accounting.
    """
    gateway = CountingGateway(lambda _t: GOOD_ORDER_ITEMS)
    _doc, stats = await _run(gateway)

    assert gateway.usage_calls == len(gateway.calls)
    assert stats.llm_calls == gateway.usage_calls


@pytest.mark.asyncio
async def test_the_concurrent_pass_loses_no_calls_to_the_race() -> None:
    """Four coroutines at a time, and the total still adds up exactly.

    This is the test that justifies collecting per call and merging under the
    caller's lock. A shared counter incremented from inside the sink would pass
    intermittently and fail under load, which is the worst way for an
    accounting bug to behave.
    """
    gateway = CountingGateway(lambda _t: GOOD_ORDER_ITEMS)
    _doc, stats = await _run(gateway, concurrency=4)

    prompt, _completion = _expected(len(TABLES))
    assert stats.prompt_tokens == prompt
    assert stats.llm_calls == len(TABLES) + 2


@pytest.mark.asyncio
async def test_a_reply_that_arrived_and_then_failed_is_still_counted() -> None:
    """The provider answered and was paid; the failure came after.

    `CountingGateway` reports usage and *then* raises, which is the shape of
    every failure that is not a refused connection: a reply that came back
    unparseable, or one whose entity could not be validated. Those cost exactly
    what a good reply costs, and a build that counted only the tables it
    managed to describe would understate itself by its most expensive
    outcomes.
    """
    failing = TABLES[0]
    name = f"{failing['schema']}.{failing['name']}"

    def reply(table: str) -> Any:
        if table == name:
            return LLMError("the reply could not be read")
        return GOOD_ORDER_ITEMS

    gateway = CountingGateway(reply)
    _doc, stats = await _run(gateway)

    assert stats.tables_failed == [name]
    # Every table, including the one that failed after replying.
    assert stats.prompt_tokens == _expected(len(TABLES))[0]
    assert stats.llm_calls == len(TABLES) + 2


@pytest.mark.asyncio
async def test_a_call_that_never_reached_the_provider_costs_nothing() -> None:
    """The other failure shape: refused before a token was spent.

    Nothing fires the sink, so the failed table contributes nothing — and the
    tables beside it keep every token they spent. Both halves matter: a build
    must not invent a cost for a call that did not happen, and must not lose
    the ones that did.
    """
    failing = TABLES[0]
    name = f"{failing['schema']}.{failing['name']}"

    class RefusingGateway(CountingGateway):
        async def structured(
            self, llm: Any, messages: Any, schema: Any, **kwargs: Any
        ) -> Any:
            user = next(m.content for m in messages if m.role == "user")
            if f"Describe this table: {name}" in user:
                raise LLMError("the provider is unavailable")
            return await super().structured(llm, messages, schema, **kwargs)

    gateway = RefusingGateway(lambda _t: GOOD_ORDER_ITEMS)
    _doc, stats = await _run(gateway)

    assert stats.tables_failed == [name]
    survivors = len(TABLES) - 1
    assert stats.prompt_tokens == (
        OVERVIEW[0] + TABLE[0] * survivors + GLOSSARY[0]
    )
    assert stats.llm_calls == survivors + 2


@pytest.mark.asyncio
async def test_the_stats_dict_carries_the_numbers_the_job_row_stores() -> None:
    """`as_dict` is what reaches `semantic_jobs`, so it is part of the contract.

    The service lifts these into the dedicated columns migration `0023` added;
    a key renamed here without the service following would leave those columns
    null while the blob still looked right.
    """
    gateway = CountingGateway(lambda _t: GOOD_ORDER_ITEMS)
    _doc, stats = await _run(gateway)
    blob = stats.as_dict()

    assert blob["prompt_tokens"] == stats.prompt_tokens
    assert blob["completion_tokens"] == stats.completion_tokens
    assert blob["llm_latency_ms"] == stats.llm_latency_ms
    assert blob["llm_calls"] == stats.llm_calls
    assert blob["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_a_provider_that_reports_no_usage_leaves_a_build_at_zero_calls() -> None:
    """No sink fired means nothing measured — and the service writes no columns.

    `llm_calls` of zero is what tells `_finish` to leave the token columns
    NULL rather than writing zeros, so a build against a gateway that reports
    nothing reads as *not measured* rather than as free.
    """
    gateway = ScriptedGateway(lambda _t: GOOD_ORDER_ITEMS)  # fires no sink
    _doc, stats = await _run(gateway)

    assert stats.llm_calls == 0
    assert stats.prompt_tokens == 0
    assert stats.tables_described == len(TABLES)
