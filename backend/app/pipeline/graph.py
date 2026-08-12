"""The chat run as a compiled graph.

The single most important decision in this migration is what is *not* here:
**the ten node functions are unmodified.** They still take `(RunState,
NodeDeps)`, still mutate the state in place, and still report a `NodeResult`.
This module is wiring — an adapter that turns each one into a LangGraph node,
and an edge list that replaces the index arithmetic `pipeline.py` used to do
over `ORDER`.

See [docs/langgraph-migration.md](../../../docs/langgraph-migration.md) §4
Phase 1. The two things worth reading before changing anything here:

**The adapter owns the executor's job, not the node's.** The deadline check,
the `seq` counter, the timing, the `on_step` persistence call and both `emit`
calls all live in `_adapt` for the same reason they lived in the `while` loop:
they are what make the SSE event sequence identical run after run, and a node
that emitted its own step events would be a node that could forget to.
`tests/unit/test_pipeline_events.py` is the contract, and it predates this file.

**`result.goto or _next(name)` reads a label, not a direction.** The graph has
five edges that are not the linear chain — three repairs *back* into `generate`
(from `validate`, `execute` and `inspect`) and two restores *forward* into
`present` (from `validate` and `execute`, via `_restore_superseded`, skipping
`execute` and `inspect` entirely). That one expression carries all five,
because a node names where it wants to go and the adapter does not care which
way that is. No edge needs special-casing, and none can be left out.

LangGraph is confined to this package and `app/workers/` by an import-linter
contract and a CI grep — see `pyproject.toml`.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.core.clock import utcnow
from app.core.errors import RunTimeoutError
from app.core.logging import get_logger
from app.domain.value_objects import StepName, StepStatus
from app.pipeline import nodes
from app.pipeline.nodes import NodeDeps
from app.pipeline.state import NodeResult, RunError, RunState

log = get_logger(__name__)

NodeFn = Callable[[RunState, NodeDeps], Awaitable[NodeResult]]
OnStep = Callable[[int, str, str, str | None, int], Awaitable[None]]

# The linear chain, in order. This is no longer walked by index — the graph's
# edges are — but it is still the source of *which* node follows which, and
# `tests/unit/test_clarify.py` reads it to assert `clarify` sits between
# `retrieve` and `generate`.
ORDER: list[tuple[str, NodeFn]] = [
    (StepName.ROUTE, nodes.route),
    (StepName.RETRIEVE, nodes.retrieve),
    # A schema question ends here, answered from the block retrieve just built
    # — schema plus semantic layer — and never reaching generate, where it
    # would become SQL against information_schema and be rejected by the
    # guard. SKIPPED for every other intent, which is the common case.
    (StepName.DESCRIBE, nodes.describe),
    # After retrieve, so the question is judged against the schema block the
    # generator will see; before generate, so an unanswerable question costs
    # no SQL. HALTs when it asks — the user's reply arrives as a new run.
    (StepName.CLARIFY, nodes.clarify),
    (StepName.GENERATE, nodes.generate),
    (StepName.VALIDATE, nodes.validate),
    (StepName.EXECUTE, nodes.execute),
    (StepName.INSPECT, nodes.inspect),
    (StepName.PRESENT, nodes.present),
    (StepName.CHART, nodes.chart),
]

# Hard ceiling on node executions, independent of max_repairs. A goto cycle can
# never spin forever even if a node misbehaves. The old `while` loop allowed 25
# node executions before writing `E_PIPELINE_LOOP`; LangGraph's
# `recursion_limit` allows exactly that many supersteps — and every superstep
# here is one node, because nothing in this graph fans out — before raising
# `GraphRecursionError`. Same ceiling, caught in `AnalyticsPipeline.run` and
# turned back into the same error.
_MAX_TRANSITIONS = 24
RECURSION_LIMIT = _MAX_TRANSITIONS + 1

# `NodeResult.status` -> the status the step trail and `run_steps` record.
# HALT is a *completed* step: a run that ended because the user was asked a
# question, or because a schema question was answered, did not fail.
_STATUS: dict[str, str] = {
    "OK": StepStatus.DONE,
    "SKIPPED": StepStatus.SKIPPED,
    "HALT": StepStatus.DONE,
    "FAILED": StepStatus.FAILED,
}

# Every label a node can hand to `Command(goto=…)`, declared so the compiled
# graph knows its own shape. The linear successor plus the jumps, plus END —
# which every node can reach, because a node crash ends the run from wherever
# it happened.
_JUMPS: dict[str, tuple[str, ...]] = {
    # `_restore_superseded` returns goto="present" from inside both of these,
    # jumping *forward* over the nodes between. That is the edge most likely to
    # be lost in a rewiring, and the one that keeps a failed check-driven retry
    # from costing the user a working answer.
    StepName.VALIDATE: (StepName.GENERATE, StepName.PRESENT),
    StepName.EXECUTE: (StepName.GENERATE, StepName.PRESENT),
    # One check-driven retry per run, from a clean repair budget.
    StepName.INSPECT: (StepName.GENERATE,),
}


class GraphState(TypedDict):
    """The state schema: the existing model carried whole.

    Not decomposed into per-field reducers, deliberately. `RunState` is already
    the typed state the nodes were written against, mutation in place keeps
    working, and the adapter returns the mutated object as the update — so
    there is exactly one representation of a run in the process, and no
    reducer that could disagree with a node about what a field means.
    """

    run: RunState


class _Seq:
    """The run's step counter.

    Per invocation, never per graph: the graph is compiled once at module
    scope and shared by every concurrent run, so a counter closed over by the
    adapter would number two users' steps into each other. It travels in the
    config alongside `NodeDeps` for the same reason `NodeDeps` does.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def _next(name: str) -> str:
    """The linear successor of a node, or END when it is the last one."""
    index = next(i for i, (n, _) in enumerate(ORDER) if n == name)
    return str(ORDER[index + 1][0]) if index + 1 < len(ORDER) else END


def _adapt(name: str, fn: NodeFn) -> Callable[..., Awaitable[Command[str]]]:
    """Wrap a node function as a LangGraph node, executor duties included."""

    async def node(state: GraphState, config: RunnableConfig) -> Command[str]:
        run = state["run"]
        configurable = config["configurable"]
        deps: NodeDeps = configurable["deps"]
        on_step: OnStep = configurable["on_step"]

        # Before the node, never inside one. `structured` can take minutes once
        # the gateway's retries and backoff are counted, so a check after the
        # call would report a timeout the run had already spent.
        if utcnow() >= run.deadline_at:
            run.error = RunError(
                code="E_TIMEOUT",
                message="The run exceeded its time budget.",
                hint="Try a narrower question, or raise the run deadline.",
            )
            raise RunTimeoutError(run.error.message)

        seq = configurable["seq"].next()
        started = time.perf_counter()

        await on_step(seq, name, StepStatus.RUNNING, None, 0)
        await deps.emit("STEP_STARTED", {"seq": seq, "name": name})

        try:
            result = await fn(run, deps)
        except Exception as err:  # a node crash is a run failure, not a 500
            log.exception("node_failed", node=name, run_id=str(run.run_id))
            run.error = run.error or RunError(
                code="E_NODE_FAILED",
                message=f"The {name} step failed.",
                hint=str(err)[:300],
            )
            duration = int((time.perf_counter() - started) * 1000)
            await on_step(seq, name, StepStatus.FAILED, str(err)[:300], duration)
            await deps.emit(
                "STEP_FINISHED",
                {"seq": seq, "name": name, "status": StepStatus.FAILED},
            )
            return Command(goto=END, update={"run": run})

        duration = int((time.perf_counter() - started) * 1000)
        status = _STATUS[result.status]

        await on_step(seq, name, status, result.detail, duration)
        await deps.emit(
            "STEP_FINISHED",
            {
                "seq": seq, "name": name, "status": status,
                "detail": result.detail, "duration_ms": duration,
            },
        )

        goto = (
            END
            if result.status in ("HALT", "FAILED")
            else (result.goto or _next(name))
        )
        return Command(goto=goto, update={"run": run})

    node.__name__ = f"node_{name}"
    return node


def _build() -> StateGraph[GraphState, None, GraphState, GraphState]:
    graph: StateGraph[GraphState, None, GraphState, GraphState] = StateGraph(
        GraphState
    )
    for step, fn in ORDER:
        name = str(step)
        graph.add_node(
            name,
            _adapt(name, fn),
            # END is in every set: a HALT, a FAILED, or a node crash ends the
            # run from wherever it happened.
            destinations=(_next(name), *(_JUMPS.get(step, ())), END),
        )
    graph.add_edge(START, str(ORDER[0][0]))
    return graph


# Compiled **once, at import**. `AnalyticsPipeline` is constructed per run and
# a per-run `.compile()` would put graph construction on the request path for
# no benefit — the compiled graph holds nothing run-specific, because
# everything run-specific travels in the config.
CHAT_GRAPH = _build().compile(name="chat")


class AnalyticsPipeline:
    """The run, unchanged from every caller's point of view.

    Same constructor, same `run(state, deps) -> RunState`, same exceptions.
    `run_service`, the workers, the eval harness and the API do not know this
    is a graph, which is the whole point of Phase 1.
    """

    def __init__(
        self,
        *,
        on_step: Callable[[int, str, str, str | None, int], Awaitable[None]],
    ) -> None:
        """`on_step(seq, name, status, detail, duration_ms)` persists a run_step."""
        self._on_step = on_step

    async def run(self, state: RunState, deps: NodeDeps) -> RunState:
        try:
            await CHAT_GRAPH.ainvoke(
                {"run": state},
                config={
                    # `NodeDeps` holds a live connector and an `emit` callable,
                    # so it is not serializable and must never live in state.
                    "configurable": {
                        "deps": deps,
                        "on_step": self._on_step,
                        "seq": _Seq(),
                    },
                    "recursion_limit": RECURSION_LIMIT,
                },
            )
        except GraphRecursionError:
            # The ceiling is the same; the failure mode is not. The old loop
            # wrote this error and returned the state like any other failed
            # run, and LangGraph raises instead — so a runaway graph would
            # reach `run_service` as an unhandled exception and become a 500,
            # which `pipeline.py` has never done. Caught here, it stays a
            # failed run the user can read.
            log.warning("pipeline_loop", run_id=str(state.run_id))
            state.error = RunError(
                code="E_PIPELINE_LOOP",
                message="The run did not converge and was stopped.",
            )

        # The nodes mutate `state` in place and every update carries that same
        # object back, so this *is* the graph's final state — returned rather
        # than read out of the invoke result so the recursion path above,
        # which has no result to read, returns the same thing.
        return state
