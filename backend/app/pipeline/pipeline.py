"""The run as an explicit state machine.

The MVP graph is linear with one bounded repair loop, which is precisely why
LangGraph is deferred: there are no durable interrupts, no parallel fan-out,
and no resume-after-crash mid-graph. Node signatures are already
LangGraph-shaped, so adopting it later is wiring, not a rewrite.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from app.core.clock import utcnow
from app.core.errors import RunTimeoutError
from app.core.logging import get_logger
from app.domain.value_objects import StepName, StepStatus
from app.pipeline import nodes
from app.pipeline.nodes import NodeDeps
from app.pipeline.state import NodeResult, RunError, RunState

log = get_logger(__name__)

NodeFn = Callable[[RunState, NodeDeps], Awaitable[NodeResult]]

ORDER: list[tuple[str, NodeFn]] = [
    (StepName.ROUTE, nodes.route),
    (StepName.RETRIEVE, nodes.retrieve),
    (StepName.GENERATE, nodes.generate),
    (StepName.VALIDATE, nodes.validate),
    (StepName.EXECUTE, nodes.execute),
    (StepName.PRESENT, nodes.present),
]

# Hard ceiling on loop iterations, independent of max_repairs. A goto cycle
# can never spin forever even if a node misbehaves.
_MAX_TRANSITIONS = 24


class AnalyticsPipeline:
    def __init__(
        self,
        *,
        on_step: Callable[[int, str, str, str | None, int], Awaitable[None]],
    ) -> None:
        """`on_step(seq, name, status, detail, duration_ms)` persists a run_step."""
        self._on_step = on_step

    async def run(self, state: RunState, deps: NodeDeps) -> RunState:
        index = 0
        seq = 0
        transitions = 0

        while index < len(ORDER):
            if transitions > _MAX_TRANSITIONS:
                state.error = RunError(
                    code="E_PIPELINE_LOOP",
                    message="The run did not converge and was stopped.",
                )
                break
            transitions += 1

            if utcnow() >= state.deadline_at:
                state.error = RunError(
                    code="E_TIMEOUT",
                    message="The run exceeded its time budget.",
                    hint="Try a narrower question, or raise the run deadline.",
                )
                raise RunTimeoutError(state.error.message)

            name, fn = ORDER[index]
            seq += 1
            started = time.perf_counter()

            await self._on_step(seq, name, StepStatus.RUNNING, None, 0)
            await deps.emit("STEP_STARTED", {"seq": seq, "name": name})

            try:
                result = await fn(state, deps)
            except Exception as err:  # a node crash is a run failure, not a 500
                log.exception("node_failed", node=name, run_id=str(state.run_id))
                state.error = state.error or RunError(
                    code="E_NODE_FAILED",
                    message=f"The {name} step failed.",
                    hint=str(err)[:300],
                )
                duration = int((time.perf_counter() - started) * 1000)
                await self._on_step(seq, name, StepStatus.FAILED, str(err)[:300], duration)
                await deps.emit(
                    "STEP_FINISHED",
                    {"seq": seq, "name": name, "status": StepStatus.FAILED},
                )
                return state

            duration = int((time.perf_counter() - started) * 1000)
            status = {
                "OK": StepStatus.DONE,
                "SKIPPED": StepStatus.SKIPPED,
                "HALT": StepStatus.DONE,
                "FAILED": StepStatus.FAILED,
            }[result.status]

            await self._on_step(seq, name, status, result.detail, duration)
            await deps.emit(
                "STEP_FINISHED",
                {
                    "seq": seq, "name": name, "status": status,
                    "detail": result.detail, "duration_ms": duration,
                },
            )

            if result.status == "FAILED":
                return state
            if result.status == "HALT":
                return state
            if result.goto:
                index = next(
                    i for i, (n, _) in enumerate(ORDER) if n == result.goto
                )
                continue
            index += 1

        return state
