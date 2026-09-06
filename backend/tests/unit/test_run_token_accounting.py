"""What a run spent, per node, and why the two numbers have to agree.

Phase 1 gave the gateway a sink; this is the phase where a number changes.
Before it, `runs.prompt_tokens` held the cost of `route` — a one-word
classification — and nothing else, because `route` is the only node calling
`complete()`, the only method that ever returned its usage. The schema block,
the SQL generation and the prose were all unmeasured, which is a run reporting
its cheapest call and calling that the total.

Four properties, each a way this rots quietly:

* **a run's total equals the sum of its steps.** Two numbers computed by
  different code from the same calls, so they can disagree — and per-node
  attribution is worth nothing the moment they do. This is the invariant most
  likely to break when a node is added;
* **every node that called a model reports it, and no node that did not.**
  `validate` and `execute` leave nulls, because *not measured* and *no tokens*
  are different facts and one column cannot say both;
* **a step row holds what that execution spent**, not the node's running
  total — `generate` repairs, so it is three rows, not one growing one;
* **the chart's tokens land on `chart`**, though `present` starts that call
  and `chart` awaits it. A bucket chosen when the reply arrives would file
  them under whatever was open at the time, which is a race rather than a
  decision.

The SSE-sequence contract lives next door in `test_pipeline_events.py` and is
deliberately not restated here: this file asserts only the numbers, so a
failure names which of the two moved. Everything is faked — no provider, no
database, no money.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.domain.ports.database import QueryResult, ResultColumn
from app.domain.ports.llm import StreamChunk, Usage
from app.domain.value_objects import DisclosurePolicy, StepStatus
from app.pipeline.nodes import NodeDeps
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.state import NodeUsage, RunState
from tests.unit.test_pipeline_events import POLICY, SNAPSHOT

# Distinct per method so a misattributed call is visible in the number itself
# rather than only in a total that happens to be wrong by the right amount.
COMPLETE_USAGE = (11, 1)      # route
STRUCTURED_USAGE = (300, 40)  # generate, chart
STREAM_USAGE = (500, 90)      # present, describe


class _Completion:
    """What `complete()` hands back. `route` is the only caller in a chat run."""

    def __init__(self) -> None:
        self.text = "ANALYTICAL"
        self.latency_ms = 3
        self.prompt_tokens, self.completion_tokens = COMPLETE_USAGE
        self.truncated = False

    def usage(self, model: str = "") -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            latency_ms=self.latency_ms,
            model=model,
        )


class CountingGateway:
    """A scripted gateway that reports usage through the sink, as a real one does.

    Deliberately fires `on_usage` from inside the call rather than letting the
    test poke the state directly: the wiring under test *is* that the node
    passes a sink at all, and a test that recorded usage itself would pass
    against a node that passes none.
    """

    def __init__(self, *, sql: Sequence[str], prose: Sequence[str], chart: Any = None) -> None:
        self._sql = list(sql)
        self._prose = list(prose)
        self._chart = chart
        self.structured_calls = 0
        self.stream_calls = 0

    async def complete(self, _llm: Any, _messages: Any) -> Any:
        return _Completion()

    async def structured(
        self, _llm: Any, _messages: Any, schema: Any, **kwargs: Any
    ) -> Any:
        self.structured_calls += 1
        sink = kwargs.get("on_usage")
        if sink is not None:
            sink(Usage(
                prompt_tokens=STRUCTURED_USAGE[0],
                completion_tokens=STRUCTURED_USAGE[1],
                latency_ms=7,
                model="gpt-4o-mini",
            ))
        name = schema.__name__
        if name == "SqlProposal":
            assert self._sql, "the script ran out of SQL"
            return schema(sql=self._sql.pop(0), reasoning="")
        if name == "ChartIntent":
            assert self._chart is not None, "the chart ask was not scripted"
            return self._chart
        raise AssertionError(f"unscripted structured call for {name}")

    def stream(
        self, _llm: Any, _messages: Any, **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls += 1
        sink = kwargs.get("on_usage")
        deltas = list(self._prose)

        async def gen() -> AsyncIterator[StreamChunk]:
            for delta in deltas:
                yield StreamChunk(text=delta)
            # A real provider's usage arrives on the final chunk, after the
            # text — so the sink fires at the end of the generator, which is
            # also the shape that catches a node reading usage too early.
            if sink is not None:
                sink(Usage(
                    prompt_tokens=STREAM_USAGE[0],
                    completion_tokens=STREAM_USAGE[1],
                    latency_ms=9,
                    model="gpt-4o-mini",
                ))

        return gen()


class ScriptedConnector:
    dialect = "postgres"

    def __init__(self, results: Sequence[Any]) -> None:
        self._results = list(results)

    async def explain(self, _sql: str) -> int | None:
        return 4200

    async def execute(
        self, _sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        assert self._results, "the script ran out of results"
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StepRecorder:
    """The six-argument `on_step`, keeping the usage the adapter measured."""

    def __init__(self) -> None:
        self.steps: list[tuple[int, str, str, NodeUsage | None]] = []

    async def emit(self, _event_type: str, _data: dict[str, Any]) -> None:
        return None

    async def on_step(
        self,
        seq: int,
        name: str,
        status: str,
        detail: str | None,
        ms: int,
        usage: NodeUsage | None = None,
    ) -> None:
        self.steps.append((seq, name, status, usage))

    def settled(self) -> dict[str, NodeUsage | None]:
        """The terminal row per node name, as `run_steps` would hold it.

        Keyed by name rather than `seq` because these assertions are about
        which node spent what; the `seq` numbering is the neighbouring file's
        contract.
        """
        out: dict[str, NodeUsage | None] = {}
        for _seq, name, status, usage in self.steps:
            if status != StepStatus.RUNNING:
                out[name] = usage
        return out


COLUMNS = [
    ResultColumn(name="month", db_type="date", semantic_type="temporal"),
    ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
]
ROWS = [["2026-01-01", 10.0], ["2026-02-01", 12.0], ["2026-03-01", 9.0]]


def _result() -> QueryResult:
    """Three rows over two columns — chartable, so the `chart` node calls a model."""
    return QueryResult(
        columns=COLUMNS, rows=ROWS, row_count=len(ROWS),
        truncated=False, duration_ms=5,
    )


async def drive(
    gateway: CountingGateway,
    connector: ScriptedConnector,
    *,
    max_repairs: int = 1,
) -> tuple[StepRecorder, RunState]:
    recorder = StepRecorder()
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="Revenue by month?",
        dialect="postgres", max_repairs=max_repairs,
        disclosure_policy=DisclosurePolicy.SAMPLE,
        deadline_at=utcnow() + timedelta(seconds=120),
    )
    deps = NodeDeps(
        llm_gateway=gateway, llm=None, connector=connector,
        snapshot=SNAPSHOT, history=[], policy=POLICY, emit=recorder.emit,
    )
    state = await AnalyticsPipeline(on_step=recorder.on_step).run(state, deps)
    return recorder, state


SQL = (
    "SELECT date_trunc('month', order_date) AS month, "
    "SUM(total_amount) AS revenue FROM public.orders GROUP BY 1"
)


def _chart_intent() -> Any:
    from app.charts import AxisSpec, ChartIntent

    return ChartIntent(
        chart_type="line",
        x_axis=AxisSpec(field="month", type="temporal"),
        y_axis=AxisSpec(field="revenue", type="quantitative"),
    )


# ── the invariant ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_runs_total_is_the_sum_of_its_steps() -> None:
    """The one that makes per-node attribution trustworthy.

    The run's totals and the step rows are accumulated by different code — the
    state's counters as each sink fires, the step rows as the adapter diffs a
    bucket around each execution — from the same calls. They are two answers to
    one question, and the moment they disagree, the granular one is unusable
    and nobody can tell which is wrong.
    """
    gateway = CountingGateway(sql=[SQL], prose=["Revenue rose."], chart=_chart_intent())
    recorder, state = await drive(gateway, ScriptedConnector([_result()]))

    measured = [u for u in recorder.settled().values() if u is not None]
    assert measured, "no step recorded any usage at all"

    assert state.prompt_tokens == sum(u.prompt_tokens for u in measured)
    assert state.completion_tokens == sum(u.completion_tokens for u in measured)
    assert state.llm_latency_ms == sum(u.latency_ms for u in measured)


@pytest.mark.asyncio
async def test_every_node_that_called_a_model_reports_it_and_no_other() -> None:
    """Before this phase only `route` did — the whole point of the plan.

    The negative half matters as much: `validate` and `execute` call no model,
    and a zero written there would read as a measurement. They must be null.
    """
    gateway = CountingGateway(sql=[SQL], prose=["Revenue rose."], chart=_chart_intent())
    recorder, _state = await drive(gateway, ScriptedConnector([_result()]))
    settled = recorder.settled()

    for name in ("route", "generate", "present", "chart"):
        usage = settled[name]
        assert usage is not None, f"{name} called a model and recorded nothing"
        assert usage.calls == 1
        assert usage.prompt_tokens > 0

    # Nodes that never reach a provider, and a node that was skipped entirely.
    for name in ("retrieve", "validate", "execute", "inspect", "clarify"):
        assert settled[name] is None, f"{name} reported usage it never spent"


@pytest.mark.asyncio
async def test_route_is_no_longer_the_only_node_that_counts() -> None:
    """The regression this phase exists to prevent, stated as a number.

    A run's total used to be exactly `route`'s eleven prompt tokens. If someone
    removes a sink, the total silently falls back towards that — so the
    assertion is that the total is *much* larger than the one node's, not
    merely non-zero.
    """
    gateway = CountingGateway(sql=[SQL], prose=["Revenue rose."], chart=_chart_intent())
    recorder, state = await drive(gateway, ScriptedConnector([_result()]))

    route_usage = recorder.settled()["route"]
    assert route_usage is not None
    assert route_usage.prompt_tokens == COMPLETE_USAGE[0]
    assert state.prompt_tokens > route_usage.prompt_tokens * 10


# ── attribution that the scheduling could get wrong ──────────────────────
@pytest.mark.asyncio
async def test_the_charts_tokens_land_on_chart_though_present_starts_the_call() -> None:
    """`present` starts the chart's model call and `chart` awaits it.

    So the call is in flight across a node boundary, and a bucket picked when
    the reply arrives would file its tokens under `present` — or under
    whichever node happened to be open, which is a property of the scheduler
    rather than of the code. Each call site names its own node instead, and
    this is what says so.
    """
    gateway = CountingGateway(sql=[SQL], prose=["Revenue rose."], chart=_chart_intent())
    recorder, _state = await drive(gateway, ScriptedConnector([_result()]))
    settled = recorder.settled()

    present, chart = settled["present"], settled["chart"]
    assert present is not None and chart is not None
    # `present` streamed prose; `chart` made one structured call. If the chart's
    # tokens had leaked into `present`, present's count would carry both.
    assert (present.prompt_tokens, present.completion_tokens) == STREAM_USAGE
    assert (chart.prompt_tokens, chart.completion_tokens) == STRUCTURED_USAGE


@pytest.mark.asyncio
async def test_a_step_row_holds_what_that_execution_spent_not_the_running_total() -> None:
    """`generate` runs twice on a repair, and that is two rows, not one doubling.

    The adapter diffs the node's bucket around each execution. Without the
    diff, the second `generate` row would report both attempts and the sum over
    the steps would double-count the first — which is exactly how the
    total-equals-sum invariant above would start failing.
    """
    rejected = "SELECT * FROM pg_catalog.pg_tables"  # the guard refuses this
    gateway = CountingGateway(
        sql=[rejected, SQL], prose=["Revenue rose."], chart=_chart_intent()
    )
    recorder, state = await drive(gateway, ScriptedConnector([_result()]), max_repairs=1)

    generates = [
        usage for _seq, name, status, usage in recorder.steps
        if name == "generate" and status != StepStatus.RUNNING
    ]
    assert len(generates) == 2, "the repair did not happen; the script is wrong"
    for usage in generates:
        assert usage is not None
        assert usage.calls == 1, "a step row reported more than its own execution"
        assert usage.prompt_tokens == STRUCTURED_USAGE[0]

    # And the run still adds up, which is the same invariant under a repair.
    measured = [
        u for _s, _n, status, u in recorder.steps
        if status != StepStatus.RUNNING and u is not None
    ]
    assert state.prompt_tokens == sum(u.prompt_tokens for u in measured)


@pytest.mark.asyncio
async def test_the_bucket_counts_both_attempts_of_a_repaired_call() -> None:
    """The node's own bucket accumulates; only the *rows* are per-execution.

    Both facts are wanted: "what did `generate` cost this run" is the bucket,
    "what did this attempt cost" is the row. This pins that the first has not
    been sacrificed to get the second.
    """
    rejected = "SELECT * FROM pg_catalog.pg_tables"
    gateway = CountingGateway(
        sql=[rejected, SQL], prose=["Revenue rose."], chart=_chart_intent()
    )
    _recorder, state = await drive(gateway, ScriptedConnector([_result()]), max_repairs=1)

    bucket = state.node_usage["generate"]
    assert bucket.calls == 2
    assert bucket.prompt_tokens == STRUCTURED_USAGE[0] * 2


# ── the shape of a null ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_provider_that_reports_nothing_is_recorded_as_a_call_not_as_silence() -> None:
    """Zero tokens from a real call is still a call, and the row must say so.

    This is the `stream()` case §1.3 of the plan names: a provider that sends
    no usage chunk yields zeros, and those zeros must be distinguishable from a
    node that never called a model. `llm_calls = 1` with zero tokens is the
    distinction — the same row with `llm_calls` null would be a lie.
    """
    class SilentStream(CountingGateway):
        def stream(
            self, _llm: Any, _messages: Any, **kwargs: Any
        ) -> AsyncIterator[StreamChunk]:
            sink = kwargs.get("on_usage")

            async def gen() -> AsyncIterator[StreamChunk]:
                yield StreamChunk(text="Revenue rose.")
                # What a gateway that sends no usage chunk produces: the sink
                # fires with zeros rather than not firing, because the call
                # did happen. Never an estimate.
                if sink is not None:
                    sink(Usage(model="gpt-4o-mini"))

            return gen()

    gateway = SilentStream(sql=[SQL], prose=["Revenue rose."], chart=_chart_intent())
    recorder, _state = await drive(gateway, ScriptedConnector([_result()]))

    present = recorder.settled()["present"]
    assert present is not None, "a call that reported nothing still happened"
    assert present.calls == 1
    assert present.prompt_tokens == 0
    assert present.completion_tokens == 0


@pytest.mark.asyncio
async def test_no_tokens_reach_the_run_total_without_a_step_row_claiming_them() -> None:
    """The completeness half of the invariant, stated from the other side.

    `test_a_runs_total_is_the_sum_of_its_steps` would still pass if a node's
    tokens were counted twice in one row and missing from another. This asserts
    the stronger property the ledger buys: every bucket is fully claimed, so
    per-node attribution accounts for the whole run rather than most of it.

    The case that made this necessary: the chart's call is started by `present`
    and awaited by `chart`, so its sink fires during a node that is not the one
    it belongs to. A diff taken around each node's own execution wrote a null
    for `chart` and lost 300 tokens the run total already held.
    """
    gateway = CountingGateway(sql=[SQL], prose=["Revenue rose."], chart=_chart_intent())
    recorder, state = await drive(gateway, ScriptedConnector([_result()]))

    claimed: dict[str, int] = {}
    for _seq, name, status, usage in recorder.steps:
        if status != StepStatus.RUNNING and usage is not None:
            claimed[name] = claimed.get(name, 0) + usage.prompt_tokens

    for node, bucket in state.node_usage.items():
        assert claimed.get(node) == bucket.prompt_tokens, (
            f"{node} spent {bucket.prompt_tokens} and its rows claim "
            f"{claimed.get(node)}"
        )
