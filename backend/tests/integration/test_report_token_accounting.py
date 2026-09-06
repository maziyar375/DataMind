"""What a generated document cost to write.

A report generation is the most expensive operation in the product — one model
call per section, plus an executive summary, `report_narration_concurrency` of
them at a time — and before this phase it recorded **nothing at all**. The
columns exist since migration `0023`; this is the phase that gives them a
writer.

The property that carries the weight is concurrency. Sections are narrated in
waves, so accumulation cannot happen inside a shared counter: §1.1 of
[docs/token-accounting-plan.md](../../../docs/token-accounting-plan.md) rejects
a gateway-instance counter precisely because one `LiteLLMGateway` is shared
across a wave and four sections' counts would interleave into one bucket with
no way to separate them. Each call collects into a list of its own; the wave's
existing commit-what-came-back loop is where they are merged.

The other three:

* **a failed section does not lose the tokens its siblings spent** — the same
  rule the commit loop already follows for paragraphs, applied to their cost;
* **a section that was paid for and then failed is still counted**, because the
  provider charged for the reply whatever happened to it afterwards;
* **a retry adds to the totals rather than replacing them** — a run's cost is
  everything spent on it, and a read-modify-write that forgot the earlier pass
  would make a retried document look cheaper than a first-attempt one.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import LLMError
from app.domain.ports.llm import Completion
from app.services import query_service
from app.workers import report as worker
from tests.integration.test_report_runs import (
    PROSE,
    SECTION_ID,
    FakeConnector,
    FakeGateway,
    _generate,
    _readable,
    _retry,
)


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> FakeConnector:
    """One connector for the whole run, and nothing that dials a network.

    Redefined here rather than imported: a fixture is resolved by name in the
    module that uses it, which is why `test_report_graph.py` declares its own
    copy too.
    """
    fake = FakeConnector()
    monkeypatch.setattr(query_service, "bind_connector", lambda *a, **k: fake)
    return fake

#: Per call, so a wave of four is trivially distinguishable from a wave of one
#: that was counted four times.
PROMPT, COMPLETION, LATENCY = 900, 120, 40


class CountingGateway(FakeGateway):
    """`FakeGateway`, but its completions carry the numbers a provider reports.

    The base fake returns `Completion(text=...)`, whose token fields default to
    zero — which is exactly what the whole product recorded before this phase,
    and would let every assertion here pass against code that counts nothing.
    """

    async def complete(self, _llm: Any, messages: Any) -> Completion:
        self.calls.append(list(messages))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return Completion(
            text=reply,
            prompt_tokens=PROMPT,
            completion_tokens=COMPLETION,
            latency_ms=LATENCY,
        )


@pytest.fixture
def counting(monkeypatch: pytest.MonkeyPatch) -> CountingGateway:
    """Replaces the autouse `gateway` fixture's fake with a counting one."""
    fake = CountingGateway()
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _cls, _s: fake)
    )
    return fake


async def test_a_generated_document_records_what_it_cost_to_write(
    connector: FakeConnector, counting: CountingGateway
) -> None:
    """The phase gate: this was zero for every report ever generated."""
    db = _readable()
    await _generate(db)

    assert db.run is not None
    calls = len(counting.calls)
    assert calls >= 3, "two sections and a summary at least"
    assert db.run.prompt_tokens == PROMPT * calls
    assert db.run.completion_tokens == COMPLETION * calls
    assert db.run.llm_latency_ms == LATENCY * calls


async def test_a_wave_attributes_every_call_to_the_run_exactly_once(
    connector: FakeConnector, counting: CountingGateway
) -> None:
    """The test §1.1 exists for: four at once, and the total is still exact.

    A shared counter written from inside four concurrent coroutines would pass
    this intermittently — which is the worst way for an accounting bug to
    behave — so the assertion is equality against the call count, not merely
    that something non-zero was recorded.
    """
    db = _readable()
    await _generate(db, narration_concurrency=4)

    assert db.run is not None
    assert db.run.prompt_tokens == PROMPT * len(counting.calls)


async def test_the_same_document_costs_the_same_at_every_wave_size(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrency is a latency dial, never an accounting one.

    `report_narration_concurrency` of 1 is strictly sequential and 4 writes a
    whole wave at once. The document differs — a section can only read what was
    established before its own wave — but the *number of calls* does not, and
    neither may the total.
    """
    totals: list[int] = []
    for concurrency in (1, 4):
        fake = CountingGateway()
        monkeypatch.setattr(
            worker.LiteLLMGateway,
            "from_settings",
            # Bound now, not read from the loop variable when the lambda runs:
            # both iterations would otherwise resolve to the second gateway.
            classmethod(lambda _cls, _s, _f=fake: _f),
        )
        db = _readable()
        await _generate(db, narration_concurrency=concurrency)
        assert db.run is not None
        totals.append(db.run.prompt_tokens or 0)

    assert totals[0] == totals[1]
    assert totals[0] > 0


async def test_a_failed_section_does_not_lose_what_its_siblings_spent(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One provider refusal costs its paragraph, never the run's accounting.

    The refused call reports nothing — it raised before a completion existed —
    and the sections beside it must keep every token they spent. This is the
    commit-what-came-back rule the wave already follows for prose, applied to
    the cost of writing it.
    """
    fake = CountingGateway(
        PROSE, LLMError("the provider is unavailable"), PROSE, PROSE
    )
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _cls, _s: fake)
    )
    db = _readable()
    await _generate(db, narration_concurrency=4)

    assert db.run is not None
    # Every call but the one that raised, which produced no completion to read.
    paid = len(fake.calls) - 1
    assert paid >= 1
    assert db.run.prompt_tokens == PROMPT * paid


async def test_a_retry_adds_to_the_runs_totals_rather_than_replacing_them(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run's cost is everything spent on it, first pass and retries alike.

    The totals are read back off the row before each write, so a second pass
    extends what the first recorded. A read-modify-write that trusted this
    process's memory would make a retried document look cheaper than one nobody
    had to fix.
    """
    fake = CountingGateway()
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _cls, _s: fake)
    )
    db = _readable()
    await _generate(db)
    assert db.run is not None
    after_generate = db.run.prompt_tokens or 0

    await _retry(db, SECTION_ID)

    assert db.run.prompt_tokens is not None
    assert db.run.prompt_tokens > after_generate
