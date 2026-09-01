"""The event contract, pinned run by run.

This is the safety net named in [docs/langgraph-migration.md] §4 Phase 0, and
it exists *before* the orchestrator moves so that the move can be proved. Its
job is non-negotiable #2: the SSE event sequence — same types, same `seq`
numbering, same order — and the `run_steps` rows written alongside it. The SPA
parses that contract to draw the live step trail, so a rewiring that shifts a
`seq` is a user-visible regression that no other test in this suite would see.

**One run is not enough, because the graph is not linear.** `ORDER` reads like
a straight line with one back-edge, and it is neither: there are three edges
back into `generate` and two that jump *forward* into `present`, skipping two
nodes. Each run below pins one of those control flows.

| run | what it pins |
|---|---|
| analytical, clean | the eleven-node order, `match`/`describe`/`clarify` writing SKIPPED rows, and the KPI branch that runs *before* any model call |
| METADATA | the only path that ends at `describe` — the halt an "obvious" conditional-edge improvement would silently delete |
| failed check-driven retry | `inspect → generate`, then `_restore_superseded` jumping forward to `present` and skipping `execute`/`inspect` |
| guard rejection | `validate → generate`, and the `ARTIFACT_CREATED {"kind": "CHART"}` end of the `chart` node |
| db error, then a failed retry | `execute → generate`, and `_restore_superseded` firing from `execute` rather than `validate` |

The last two are not in the migration record's table of three; they are here
because Phase 1's checklist requires **all five** non-linear edges wired, and
an edge with no snapshot is an edge that can be forgotten. Between them the
five runs execute every edge in the graph.

Everything the pipeline reaches is faked: the gateway is a script, the
connector is a script, and `on_step`/`emit` are recorders. No database, no
provider, no money — this runs in `make test`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import ConnectorError
from app.domain.ports.database import QueryResult, ResultColumn
from app.domain.ports.llm import StreamChunk
from app.domain.value_objects import DisclosurePolicy, StepStatus
from app.pipeline.nodes import NodeDeps
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.state import RunState
from app.sqlguard import GuardPolicy

# ── the fixture the runs are written against ─────────────────────────────
# `status` carries `sample_values`, which the connector contract emits only
# when it is provably the column's complete domain — that is what makes
# `status = 'delivered'` structural evidence of a wrong query rather than a
# guess, and therefore what makes a `retry=True` finding reachable at all.
TABLES: list[dict[str, Any]] = [
    {
        "schema": "public",
        "name": "orders",
        "approx_row_count": 4200,
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True,
             "nullable": False},
            {"name": "customer_id", "data_type": "bigint", "nullable": False,
             "is_foreign_key": True, "references": "public.customers.id"},
            {"name": "order_date", "data_type": "date",
             "min_value": "2024-01-01", "max_value": "2025-12-31"},
            {"name": "status", "data_type": "text",
             "sample_values": ["shipped", "pending", "cancelled"]},
            {"name": "total_amount", "data_type": "numeric"},
        ],
    },
    {
        "schema": "public",
        "name": "customers",
        "approx_row_count": 900,
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True,
             "nullable": False},
            {"name": "name", "data_type": "text"},
        ],
    },
]

SNAPSHOT: dict[str, Any] = {
    "dialect": "postgres",
    "tables": TABLES,
    "relationships": [
        {"from_table": "public.orders", "from_column": "customer_id",
         "to_table": "public.customers", "to_column": "id"},
    ],
}

POLICY = GuardPolicy(
    dialect="postgres",
    max_rows=1000,
    allowed_tables={"public.orders", "public.customers"},
    allowed_columns={
        "public.orders": {"id", "customer_id", "order_date", "status",
                          "total_amount"},
        "public.customers": {"id", "name"},
    },
)

REVENUE = [ResultColumn(name="revenue", db_type="numeric",
                        semantic_type="quantitative")]
BY_STATUS = [
    ResultColumn(name="status", db_type="text", semantic_type="nominal"),
    ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
]

SQL_TOTAL = "SELECT SUM(o.total_amount) AS revenue FROM public.orders o"
SQL_BY_STATUS = (
    "SELECT o.status, SUM(o.total_amount) AS revenue "
    "FROM public.orders o GROUP BY o.status"
)
SQL_IMPOSSIBLE = "SELECT o.id FROM public.orders o WHERE o.status = 'delivered'"
SQL_SHIPPED = "SELECT o.id FROM public.orders o WHERE o.status = 'shipped'"
SQL_FORBIDDEN = "SELECT * FROM public.secrets"


def _rows(columns: list[ResultColumn], rows: list[list[Any]]) -> QueryResult:
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows),
        truncated=False, duration_ms=7, rows_scanned_estimate=4200,
    )


EMPTY = _rows([ResultColumn(name="id", db_type="bigint")], [])
ONE_ROW = _rows(REVENUE, [[1_240_000.0]])
THREE_ROWS = _rows(BY_STATUS, [["shipped", 1000.0], ["pending", 500.0],
                               ["cancelled", 200.0]])


# ── the fakes ────────────────────────────────────────────────────────────
class ScriptedGateway:
    """A gateway that answers from a script, so a run is fully deterministic.

    Dispatches on the requested schema rather than on call order, because the
    three `structured` callers (`clarify`, `generate`, the chart ask) are not
    reached in a fixed sequence once a repair edge is taken.
    """

    def __init__(
        self,
        *,
        route: str = "ANALYTICAL",
        sql: Sequence[str] = (),
        prose: Sequence[str] = ("Revenue was $1.24M.",),
        chart: Any = None,
    ) -> None:
        self._route = route
        self._sql = list(sql)
        self._prose = list(prose)
        self._chart = chart
        self.chart_asks = 0
        self.streams = 0

    async def complete(self, _llm: Any, _messages: Any) -> Any:
        class _Completion:
            text = self._route
            latency_ms = 3
            prompt_tokens = 11
            completion_tokens = 1

        return _Completion()

    async def structured(self, _llm: Any, _messages: Any, schema: Any) -> Any:
        name = schema.__name__
        if name == "SqlProposal":
            assert self._sql, "the script ran out of SQL"
            return schema(sql=self._sql.pop(0), reasoning="")
        if name == "ChartIntent":
            self.chart_asks += 1
            assert self._chart is not None, "the chart ask was not scripted"
            return self._chart
        raise AssertionError(f"unscripted structured call for {name}")

    def stream(self, _llm: Any, _messages: Any) -> AsyncIterator[StreamChunk]:
        self.streams += 1
        deltas = list(self._prose)

        async def gen() -> AsyncIterator[StreamChunk]:
            for delta in deltas:
                yield StreamChunk(text=delta)

        return gen()


class ScriptedConnector:
    """Hands back the next scripted result, or raises the next scripted error."""

    dialect = "postgres"

    def __init__(self, results: Sequence[QueryResult | ConnectorError]) -> None:
        self._results = list(results)

    async def explain(self, _sql: str) -> int | None:
        return 4200

    async def execute(
        self, _sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        assert self._results, "the script ran out of results"
        item = self._results.pop(0)
        if isinstance(item, ConnectorError):
            raise item
        return item


class Recorder:
    """Everything the executor emits and everything it persists, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.steps: list[tuple[int, str, str, str | None, int]] = []

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, data))

    async def on_step(
        self, seq: int, name: str, status: str, detail: str | None, ms: int
    ) -> None:
        self.steps.append((seq, name, status, detail, ms))


# ── how a run is described ───────────────────────────────────────────────
# One identifying field per non-step event. Enough that a reordering, a
# dropped event or a changed payload key fails; not so much that an unrelated
# wording change does.
_TAGS: dict[str, Any] = {
    "SQL_GENERATED": lambda d: f"attempt {d['attempt_no']}",
    "SQL_REJECTED": lambda d: f"attempt {d['attempt_no']}",
    "SQL_VALIDATED": lambda d: f"attempt {d['attempt_no']}",
    "QUERY_COMPLETED": lambda d: f"{d['row_count']} rows",
    "RESULT_CHECKED": lambda d: ",".join(f["code"] for f in d["findings"]),
    "CLARIFICATION_REQUESTED": lambda d: d["question"],
    "ARTIFACT_CREATED": lambda d: d["kind"],
    "TEXT_DELTA": lambda d: d["text"],
    "TEXT_RESET": lambda d: d["reason"],
}


def trail(recorder: Recorder) -> list[tuple[Any, str, str, str | None]]:
    """`(seq, type, name-or-tag, status)` for every event, in order."""
    out: list[tuple[Any, str, str, str | None]] = []
    for event_type, data in recorder.events:
        if event_type == "STEP_STARTED":
            out.append((data["seq"], event_type, data["name"], None))
        elif event_type == "STEP_FINISHED":
            out.append((data["seq"], event_type, data["name"], data["status"]))
        else:
            tagger = _TAGS.get(event_type)
            assert tagger is not None, f"unknown event type {event_type}"
            out.append((None, event_type, tagger(data), None))
    return out


def calls(recorder: Recorder) -> list[tuple[int, str, str]]:
    """Every `on_step` call, in order — two per node, RUNNING then terminal.

    `run_service._record_step` upserts on `(run_id, seq)`, so the pair is one
    `run_steps` row written twice: it appears as RUNNING while the node is in
    flight, which is what makes the trail *live* rather than a summary printed
    at the end. Both calls are part of the contract.
    """
    return [(seq, name, status) for seq, name, status, _detail, _ms in recorder.steps]


def rows(recorder: Recorder) -> list[tuple[int, str, str]]:
    """The `run_steps` rows as they settle — one per `seq`, terminal status."""
    settled: dict[int, tuple[int, str, str]] = {}
    for seq, name, status, _detail, _ms in recorder.steps:
        if status != StepStatus.RUNNING:
            settled[seq] = (seq, name, status)
    return [settled[seq] for seq in sorted(settled)]


def started(seq: int, name: str) -> tuple[Any, str, str, str | None]:
    return (seq, "STEP_STARTED", name, None)


def finished(
    seq: int, name: str, status: str = StepStatus.DONE
) -> tuple[Any, str, str, str | None]:
    return (seq, "STEP_FINISHED", name, status)


def event(event_type: str, tag: str) -> tuple[Any, str, str, str | None]:
    return (None, event_type, tag, None)


# The five nodes every run walks before any SQL exists. `match`, `describe` and
# `clarify` are SKIPPED here and that is the point: a skipped node still writes
# a `run_steps` row and still emits its event pair, which is exactly what a
# conditional edge routing *around* it would stop doing.
#
# `match` is SKIPPED because these runs build `NodeDeps` with no matcher — the
# pre-feature path, and the one the draft graph and the eval harness take. A
# connection whose knowledge store is empty behaves identically, since an empty
# store simply never matches.
PREAMBLE = [
    started(1, "route"), finished(1, "route"),
    started(2, "match"), finished(2, "match", StepStatus.SKIPPED),
    started(3, "retrieve"), finished(3, "retrieve"),
    started(4, "describe"), finished(4, "describe", StepStatus.SKIPPED),
    started(5, "clarify"), finished(5, "clarify", StepStatus.SKIPPED),
]


async def drive(
    gateway: ScriptedGateway,
    connector: ScriptedConnector,
    *,
    question: str = "What was total revenue?",
    max_repairs: int = 1,
    policy: str = DisclosurePolicy.SAMPLE,
) -> tuple[Recorder, RunState]:
    recorder = Recorder()
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        dialect="postgres", max_repairs=max_repairs, disclosure_policy=policy,
        deadline_at=utcnow() + timedelta(seconds=120),
    )
    deps = NodeDeps(
        llm_gateway=gateway, llm=None, connector=connector,
        snapshot=SNAPSHOT, history=[], policy=POLICY, emit=recorder.emit,
    )
    state = await AnalyticsPipeline(on_step=recorder.on_step).run(state, deps)
    return recorder, state


# ── run 1: analytical, clean, one row ────────────────────────────────────
@pytest.mark.asyncio
async def test_a_clean_analytical_run_emits_the_eleven_node_trail() -> None:
    """The happy path, and the KPI branch that costs no tokens.

    A single-row result is the shape a big number is *made* of, so the `chart`
    node reaches for `plan_kpi` instead of a chart — and it does so from the
    data-shape veto, which runs before the model is asked anything. The
    assertion on `chart_asks` is what pins "before", and it is a property a
    rewiring could quietly lose by moving the veto behind the node call.
    """
    gateway = ScriptedGateway(sql=[SQL_TOTAL])
    recorder, state = await drive(gateway, ScriptedConnector([ONE_ROW]))

    assert trail(recorder) == PREAMBLE + [
        started(6, "generate"),
        event("SQL_GENERATED", "attempt 1"),
        finished(6, "generate"),
        started(7, "validate"),
        event("SQL_VALIDATED", "attempt 1"),
        finished(7, "validate"),
        started(8, "execute"),
        event("QUERY_COMPLETED", "1 rows"),
        finished(8, "execute"),
        started(9, "inspect"), finished(9, "inspect"),
        started(10, "present"),
        event("TEXT_DELTA", "Revenue was $1.24M."),
        finished(10, "present"),
        started(11, "chart"),
        event("ARTIFACT_CREATED", "KPI"),
        finished(11, "chart"),
    ]
    assert rows(recorder) == [
        (1, "route", StepStatus.DONE),
        (2, "match", StepStatus.SKIPPED),
        (3, "retrieve", StepStatus.DONE),
        (4, "describe", StepStatus.SKIPPED),
        (5, "clarify", StepStatus.SKIPPED),
        (6, "generate", StepStatus.DONE),
        (7, "validate", StepStatus.DONE),
        (8, "execute", StepStatus.DONE),
        (9, "inspect", StepStatus.DONE),
        (10, "present", StepStatus.DONE),
        (11, "chart", StepStatus.DONE),
    ]
    # Each row is written twice — RUNNING as the node starts, then its
    # terminal status. That is what makes the trail live rather than a summary
    # printed at the end, so both calls are part of the contract.
    assert calls(recorder)[:6] == [
        (1, "route", StepStatus.RUNNING),
        (1, "route", StepStatus.DONE),
        # `match` with no matcher in the deps: SKIPPED, and it still writes
        # both rows. A node that skipped *silently* would be an edge routing
        # around it, which is the thing this file exists to refuse.
        (2, "match", StepStatus.RUNNING),
        (2, "match", StepStatus.SKIPPED),
        (3, "retrieve", StepStatus.RUNNING),
        (3, "retrieve", StepStatus.DONE),
    ]
    assert len(calls(recorder)) == 22

    assert gateway.chart_asks == 0
    assert state.kpi is not None and state.chart is None
    assert state.error is None


# ── run 2: METADATA, the one path that ends at describe ──────────────────
@pytest.mark.asyncio
async def test_a_metadata_question_halts_at_describe() -> None:
    """The halt the tempting Phase 1 "improvement" would silently change.

    A conditional edge out of `retrieve` routing METADATA to `describe` and
    everything else past it looks like the same graph. It is not: it stops
    `describe` and `clarify` writing their SKIPPED rows on every analytical
    run, which shifts every later `seq`.
    """
    gateway = ScriptedGateway(route="METADATA", prose=["You have two tables."])
    recorder, state = await drive(
        gateway, ScriptedConnector([]), question="What tables do I have?"
    )

    assert trail(recorder) == [
        started(1, "route"), finished(1, "route"),
        started(2, "match"), finished(2, "match", StepStatus.SKIPPED),
        started(3, "retrieve"), finished(3, "retrieve"),
        started(4, "describe"),
        event("TEXT_DELTA", "You have two tables."),
        finished(4, "describe"),
    ]
    assert rows(recorder) == [
        (1, "route", StepStatus.DONE),
        (2, "match", StepStatus.SKIPPED),
        (3, "retrieve", StepStatus.DONE),
        # HALT is a completed step, not a failed one.
        (4, "describe", StepStatus.DONE),
    ]
    assert state.answer == "You have two tables."
    assert state.attempts == [] and state.error is None


# ── run 3: a check-driven retry that fails, restoring forward ────────────
@pytest.mark.asyncio
async def test_a_failed_check_retry_restores_forward_into_present() -> None:
    """Two `seq` sequences no linear reading of `ORDER` predicts.

    `inspect` spends the run's one repair on a structural finding, the retry is
    rejected by the guard, and `_restore_superseded` returns `goto="present"`
    from inside `validate` — jumping **forward** over `execute` and `inspect`.
    Seq 10 is a validate; seq 11 is a present. Nothing in between.
    """
    gateway = ScriptedGateway(sql=[SQL_IMPOSSIBLE, SQL_FORBIDDEN])
    recorder, state = await drive(
        gateway,
        ScriptedConnector([EMPTY]),
        question="Which orders were delivered?",
    )

    assert trail(recorder) == PREAMBLE + [
        started(6, "generate"),
        event("SQL_GENERATED", "attempt 1"),
        finished(6, "generate"),
        started(7, "validate"),
        event("SQL_VALIDATED", "attempt 1"),
        finished(7, "validate"),
        started(8, "execute"),
        event("QUERY_COMPLETED", "0 rows"),
        finished(8, "execute"),
        started(9, "inspect"),
        event("RESULT_CHECKED", "C_EMPTY_RESULT"),
        finished(9, "inspect"),
        # The repair edge back into generate.
        started(10, "generate"),
        event("SQL_GENERATED", "attempt 2"),
        finished(10, "generate"),
        started(11, "validate"),
        event("SQL_REJECTED", "attempt 2"),
        # DONE, not FAILED: the restore is a successful step that redirects.
        finished(11, "validate"),
        # …and straight into present. No execute, no inspect.
        started(12, "present"),
        event("TEXT_DELTA", "Revenue was $1.24M."),
        finished(12, "present"),
        started(13, "chart"),
        finished(13, "chart", StepStatus.SKIPPED),
    ]
    assert rows(recorder) == [
        (1, "route", StepStatus.DONE),
        (2, "match", StepStatus.SKIPPED),
        (3, "retrieve", StepStatus.DONE),
        (4, "describe", StepStatus.SKIPPED),
        (5, "clarify", StepStatus.SKIPPED),
        (6, "generate", StepStatus.DONE),
        (7, "validate", StepStatus.DONE),
        (8, "execute", StepStatus.DONE),
        (9, "inspect", StepStatus.DONE),
        (10, "generate", StepStatus.DONE),
        (11, "validate", StepStatus.DONE),
        (12, "present", StepStatus.DONE),
        (13, "chart", StepStatus.SKIPPED),
    ]
    # The whole point of the restore: a failed check retry costs the user
    # nothing. The earlier result is back, and the run did not fail.
    assert state.execution is not None
    assert state.superseded_execution is None
    assert state.error is None


# ── run 4: the guard rejects, the repair succeeds, a chart lands ─────────
@pytest.mark.asyncio
async def test_a_rejected_statement_repairs_and_charts() -> None:
    """`validate → generate`, and the other half of `ARTIFACT_CREATED`.

    Three rows with a dimension and a measure is the shape that survives the
    veto, so this is the run where the chart ask actually happens and the node
    emits a CHART rather than a KPI.
    """
    from app.charts import AxisSpec, ChartIntent

    intent = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="status", type="nominal"),
        y_axis=AxisSpec(field="revenue", type="quantitative"),
    )
    gateway = ScriptedGateway(
        sql=[SQL_FORBIDDEN, SQL_BY_STATUS], chart=intent
    )
    recorder, state = await drive(
        gateway, ScriptedConnector([THREE_ROWS]), question="Revenue by status?"
    )

    assert trail(recorder) == PREAMBLE + [
        started(6, "generate"),
        event("SQL_GENERATED", "attempt 1"),
        finished(6, "generate"),
        started(7, "validate"),
        event("SQL_REJECTED", "attempt 1"),
        finished(7, "validate"),
        started(8, "generate"),
        event("SQL_GENERATED", "attempt 2"),
        finished(8, "generate"),
        started(9, "validate"),
        event("SQL_VALIDATED", "attempt 2"),
        finished(9, "validate"),
        started(10, "execute"),
        event("QUERY_COMPLETED", "3 rows"),
        finished(10, "execute"),
        started(11, "inspect"), finished(11, "inspect"),
        started(12, "present"),
        event("TEXT_DELTA", "Revenue was $1.24M."),
        finished(12, "present"),
        started(13, "chart"),
        event("ARTIFACT_CREATED", "CHART"),
        finished(13, "chart"),
    ]
    assert [name for _seq, name, _status in rows(recorder)] == [
        "route", "match", "retrieve", "describe", "clarify",
        "generate", "validate", "generate", "validate",
        "execute", "inspect", "present", "chart",
    ]
    assert gateway.chart_asks == 1
    assert state.chart is not None and state.kpi is None


# ── run 5: the database refuses, twice, around a check retry ─────────────
@pytest.mark.asyncio
async def test_a_database_error_repairs_and_then_restores_from_execute() -> None:
    """The two edges the other runs do not reach.

    `execute → generate` on the first refusal, and — after `inspect` has spent
    the second repair on a structural finding — `_restore_superseded` firing
    from inside `execute` rather than `validate`. Two repairs, because that is
    what it takes to walk both in one run.
    """
    refused = ConnectorError("relation does not exist")
    gateway = ScriptedGateway(sql=[SQL_TOTAL, SQL_IMPOSSIBLE, SQL_SHIPPED])
    recorder, state = await drive(
        gateway,
        ScriptedConnector([refused, EMPTY, refused]),
        question="Which orders were delivered?",
        max_repairs=2,
    )

    assert trail(recorder) == PREAMBLE + [
        started(6, "generate"),
        event("SQL_GENERATED", "attempt 1"),
        finished(6, "generate"),
        started(7, "validate"),
        event("SQL_VALIDATED", "attempt 1"),
        finished(7, "validate"),
        # The database refuses; the repair edge out of execute.
        started(8, "execute"), finished(8, "execute"),
        started(9, "generate"),
        event("SQL_GENERATED", "attempt 2"),
        finished(9, "generate"),
        started(10, "validate"),
        event("SQL_VALIDATED", "attempt 2"),
        finished(10, "validate"),
        started(11, "execute"),
        event("QUERY_COMPLETED", "0 rows"),
        finished(11, "execute"),
        started(12, "inspect"),
        event("RESULT_CHECKED", "C_EMPTY_RESULT"),
        finished(12, "inspect"),
        started(13, "generate"),
        event("SQL_GENERATED", "attempt 3"),
        finished(13, "generate"),
        started(14, "validate"),
        event("SQL_VALIDATED", "attempt 3"),
        finished(14, "validate"),
        # Refused again, with no repair budget left — so the restore fires
        # here, one node later than it does in run 3.
        started(15, "execute"), finished(15, "execute"),
        started(16, "present"),
        event("TEXT_DELTA", "Revenue was $1.24M."),
        finished(16, "present"),
        started(17, "chart"),
        finished(17, "chart", StepStatus.SKIPPED),
    ]
    assert [name for _seq, name, _status in rows(recorder)] == [
        "route", "match", "retrieve", "describe", "clarify",
        "generate", "validate", "execute",
        "generate", "validate", "execute", "inspect",
        "generate", "validate", "execute",
        "present", "chart",
    ]
    assert state.execution is not None
    assert state.superseded_execution is None
    assert state.error is None


# ── the two ways a run stops that are not a node's choice ────────────────
@pytest.mark.asyncio
async def test_a_node_crash_is_a_failed_step_and_not_an_exception() -> None:
    """`E_NODE_FAILED`, a FAILED step pair, and a state that comes back.

    Nothing above the pipeline ever sees the exception, which is why a node
    crash has never been a bare 500. Phase 1 has to keep this handler.
    """
    class Exploding(ScriptedConnector):
        async def explain(self, _sql: str) -> int | None:
            raise RuntimeError("connector went away")

    gateway = ScriptedGateway(sql=[SQL_TOTAL])
    recorder, state = await drive(gateway, Exploding([]))

    assert trail(recorder)[-2:] == [
        started(8, "execute"),
        finished(8, "execute", StepStatus.FAILED),
    ]
    assert rows(recorder)[-1] == (8, "execute", StepStatus.FAILED)
    assert state.error is not None
    assert state.error.code == "E_NODE_FAILED"
    assert state.answer is None


@pytest.mark.asyncio
async def test_a_run_that_will_not_converge_is_stopped_not_raised() -> None:
    """The hard ceiling, independent of `max_repairs`.

    A repair budget large enough to never run out turns `generate ⇄ validate`
    into a cycle, and something has to stop it. The ceiling is 25 node
    executions — and the *shape* of the stop matters as much as the count:
    `E_PIPELINE_LOOP` on the state, the run ending like any other failed run,
    and **no exception reaching the caller**. `run_service` has no handler for
    one, so a runaway graph that raised would be a bare 500, which is the one
    thing this executor has never done.
    """
    gateway = ScriptedGateway(sql=[SQL_FORBIDDEN] * 40)
    recorder, state = await drive(
        gateway, ScriptedConnector([]), max_repairs=100
    )

    assert len(rows(recorder)) == 25
    # Five preamble nodes now, so the cycle gets twenty executions rather than
    # twenty-one: the ceiling counts *node executions*, and `match` spends one
    # of them on every run. That is the honest consequence of adding a node to
    # the chain, and it is recorded here rather than absorbed by loosening the
    # assertion — a repair budget this large is a runaway either way.
    assert [name for _seq, name, _status in rows(recorder)][5:] == [
        "generate", "validate"
    ] * 10
    assert state.error is not None
    assert state.error.code == "E_PIPELINE_LOOP"


@pytest.mark.asyncio
async def test_an_expired_deadline_raises_before_the_node_runs() -> None:
    """`RunTimeoutError` is raised *before* a node, never inside one.

    So the trail stops cleanly at the last completed step: there is no
    STEP_STARTED for a node that never ran. `run_service` turns the exception
    into `TIMED_OUT`; the adapter that replaces this loop has to keep the check
    on the same side of the node call.
    """
    from app.core.errors import RunTimeoutError

    recorder = Recorder()
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="What was total revenue?",
        deadline_at=utcnow() - timedelta(seconds=1),
    )
    deps = NodeDeps(
        llm_gateway=ScriptedGateway(sql=[SQL_TOTAL]), llm=None,
        connector=ScriptedConnector([]), snapshot=SNAPSHOT, history=[],
        policy=POLICY, emit=recorder.emit,
    )

    with pytest.raises(RunTimeoutError):
        await AnalyticsPipeline(on_step=recorder.on_step).run(state, deps)

    assert recorder.events == []
    assert recorder.steps == []
    assert state.error is not None and state.error.code == "E_TIMEOUT"
