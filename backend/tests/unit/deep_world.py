"""A scripted world for driving a deep run end to end, with nothing real in it.

The schema, the guard policy and the result shapes are `test_pipeline_events`'
own, so a deep sub-query is judged by exactly the fixture a chat question is.
The gateway answers from a script dispatched on the *schema* asked for — the
plan, a statement, the prose — because once a repair edge is taken the calls
are not reached in a fixed order. No provider, no database, no money.

Not a `conftest.py`, on `semantic_world.py`'s precedent: imported by name into
the deep tests and nowhere else.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.core.clock import utcnow
from app.core.errors import ConnectorError, LLMError
from app.domain.ports.database import QueryResult, ResultColumn
from app.domain.ports.llm import ChatMessage, StreamChunk, Usage
from app.domain.value_objects import DeepBudget, DisclosurePolicy
from app.pipeline.graph import DeepPipeline
from app.pipeline.nodes import NodeDeps
from app.pipeline.state import AnalysisPlan, DeepState, PlanStep
from tests.unit.test_pipeline_events import (
    BY_STATUS,
    POLICY,
    REVENUE,
    SNAPSHOT,
    SQL_BY_STATUS,
    SQL_FORBIDDEN,
    SQL_TOTAL,
)

__all__ = [
    "BY_STATUS", "POLICY", "REVENUE", "SNAPSHOT", "SQL_BY_STATUS",
    "SQL_FORBIDDEN", "SQL_TOTAL",
    "DeepGateway", "DeepConnector", "DeepRecorder", "plan_of", "result",
    "deep_state", "deep_deps", "drive",
]


def plan_of(*questions: str, tool: str = "SQL", **extra: Any) -> AnalysisPlan:
    """A plan of `questions`, one step each, all with one tool."""
    return AnalysisPlan(
        restatement=extra.get("restatement", "Why revenue moved."),
        steps=[
            PlanStep(question=q, intent="CONFIRM", why="because", tool=tool,
                     depends_on=[])  # type: ignore[arg-type]
            for q in questions
        ],
        stop_when=extra.get("stop_when", "the driver is named"),
    )


def result(columns: list[ResultColumn], rows: list[list[Any]]) -> QueryResult:
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows),
        truncated=False, duration_ms=5, rows_scanned_estimate=4200,
    )


class DeepGateway:
    """Answers `plan`, `generate` and the prose from a script.

    `sql` is a list popped in order, or a callable `(messages) -> str` for a
    test that wants every statement to be the same bad one forever — the
    budget test, which must not run out of script before the budget runs out.
    Every call reports `prompt_tokens` so the token bound can be exercised.
    `messages` keeps every prompt sent, so a test can read what reached one.
    """

    def __init__(
        self,
        *,
        plan: AnalysisPlan | Exception | None = None,
        sql: Sequence[str] | Callable[[Sequence[ChatMessage]], str] = (),
        prose: Sequence[str] = ("The answer.",),
        route: str = "ANALYTICAL",
        prompt_tokens: int = 100,
    ) -> None:
        self._plan = plan
        self._sql = sql if callable(sql) else list(sql)
        self._prose = list(prose)
        self._route = route
        self._tokens = prompt_tokens
        self.messages: list[tuple[str, list[ChatMessage]]] = []

    async def complete(self, _llm: Any, messages: Any) -> Any:
        self.messages.append(("complete", list(messages)))
        route, tokens = self._route, self._tokens

        class _Completion:
            text = route
            latency_ms = 1
            prompt_tokens = tokens
            completion_tokens = 1

        return _Completion()

    async def structured(
        self, _llm: Any, messages: Any, schema: Any, *, on_usage: Any = None,
        **_kwargs: Any,
    ) -> Any:
        name = schema.__name__
        self.messages.append((name, list(messages)))
        if on_usage is not None:
            on_usage(Usage(prompt_tokens=self._tokens, completion_tokens=5,
                           latency_ms=1, model="scripted"))
        if name == "AnalysisPlan":
            if isinstance(self._plan, Exception):
                raise self._plan
            assert self._plan is not None, "the plan was not scripted"
            return self._plan
        if name == "SqlProposal":
            if callable(self._sql):
                return schema(sql=self._sql(messages), reasoning="")
            assert self._sql, "the script ran out of SQL"
            return schema(sql=self._sql.pop(0), reasoning="")
        if name == "ChartIntent":
            raise LLMError("no chart in this world")
        raise AssertionError(f"unscripted structured call for {name}")

    def stream(
        self, _llm: Any, messages: Any, *, on_usage: Any = None, **_kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        self.messages.append(("stream", list(messages)))
        deltas = list(self._prose)
        tokens = self._tokens

        async def gen() -> AsyncIterator[StreamChunk]:
            for delta in deltas:
                yield StreamChunk(text=delta)
            if on_usage is not None:
                on_usage(Usage(prompt_tokens=tokens, completion_tokens=5,
                               latency_ms=1, model="scripted"))

        return gen()


class DeepConnector:
    """The next scripted result, or a callable answering every statement."""

    dialect = "postgres"

    def __init__(
        self,
        results: Sequence[QueryResult | ConnectorError]
        | Callable[[str], QueryResult] = (),
    ) -> None:
        self._results = results if callable(results) else list(results)
        self.executed: list[str] = []
        self.max_rows: list[int] = []

    async def explain(self, _sql: str) -> int | None:
        return 4200

    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        self.executed.append(sql)
        self.max_rows.append(max_rows)
        if callable(self._results):
            return self._results(sql)
        assert self._results, "the script ran out of results"
        item = self._results.pop(0)
        if isinstance(item, ConnectorError):
            raise item
        return item

    async def close(self) -> None:
        return None


class DeepRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.steps: list[tuple[int, str, str, str | None, int]] = []

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, data))

    async def on_step(
        self, seq: int, name: str, status: str, detail: str | None, ms: int
    ) -> None:
        self.steps.append((seq, name, status, detail, ms))

    def settled(self) -> list[tuple[str, str]]:
        """`(name, status)` per `run_steps` row, in `seq` order."""
        rows: dict[int, tuple[str, str]] = {}
        for seq, name, status, _d, _ms in self.steps:
            if status != "RUNNING":
                rows[seq] = (name, status)
        return [rows[k] for k in sorted(rows)]

    def names(self) -> list[str]:
        return [name for name, _ in self.settled()]

    def detail(self, name: str) -> list[str | None]:
        return [d for _s, n, st, d, _ms in self.steps if n == name and st != "RUNNING"]


def deep_state(
    *,
    question: str = "Why did revenue drop in March?",
    policy: str = DisclosurePolicy.SAMPLE,
    max_repairs: int = 1,
    max_rows: int = 1000,
    budget: DeepBudget | None = None,
    deadline: datetime | None = None,
) -> DeepState:
    return DeepState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(),
        question=question,
        asked=question,
        disclosure_policy=policy,
        max_repairs=max_repairs,
        max_rows=max_rows,
        base_max_rows=max_rows,
        deadline_at=deadline or utcnow() + timedelta(minutes=10),
        budget=budget or DeepBudget(deadline_at=utcnow() + timedelta(minutes=5)),
    )


def deep_deps(
    gateway: DeepGateway, connector: DeepConnector, recorder: DeepRecorder,
    **extra: Any,
) -> NodeDeps:
    return NodeDeps(
        llm_gateway=gateway,  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
        connector=connector,  # type: ignore[arg-type]
        snapshot=SNAPSHOT,
        history=[],
        policy=POLICY,
        emit=recorder.emit,
        **extra,
    )


async def drive(
    state: DeepState, gateway: DeepGateway, connector: DeepConnector,
    **extra: Any,
) -> tuple[DeepState, DeepRecorder]:
    recorder = DeepRecorder()
    deps = deep_deps(gateway, connector, recorder, **extra)
    out = await DeepPipeline(on_step=recorder.on_step).run(state, deps)
    return out, recorder
