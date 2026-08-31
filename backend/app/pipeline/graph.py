"""The compiled graphs, and the one repair region they share.

The single most important decision in this migration is what is *not* here:
**the ten node functions are unmodified.** They still take `(RunState,
NodeDeps)`, still mutate the state in place, and still report a `NodeResult`.
This module is wiring — an adapter that turns each one into a LangGraph node,
and edge lists that replaced the index arithmetic `pipeline.py` used to do over
`ORDER`.

See [docs/langgraph-migration.md](../../../docs/langgraph-migration.md) §4.
Three things to read before changing anything here:

**The adapter owns the executor's job, not the node's.** The deadline check,
the `seq` counter, the timing, the `on_step` persistence call and both `emit`
calls all live in `_adapt` for the same reason they lived in the `while` loop:
they are what make the SSE event sequence identical run after run, and a node
that emitted its own step events would be a node that could forget to.
`tests/unit/test_pipeline_events.py` is the contract, and it predates this file.

**`result.goto or successor` reads a label, not a direction.** The chat graph
has five edges that are not the linear chain — three repairs *back* into
`generate` (from `validate`, `execute` and `inspect`) and two restores *forward*
into `present` (from `validate` and `execute`, via `_restore_superseded`,
skipping `execute` and `inspect`). That one expression carries all five, because
a node names where it wants to go and the adapter does not care which way that
is. No edge needs special-casing, and none can be left out.

**`match` is the only node that can skip four others.** It sits between
`route` and `retrieve` and its hit exit names `validate` — the repair region's
guard, which already feeds `execute`. A stored template therefore reuses every
guarantee the generated path has (re-validation against the current snapshot,
the rewriter, the row cap) and gets no exemption. Its miss exit is the ordinary
successor, and on a miss it writes nothing to the state, so the prompt the
generator receives is byte-identical to the one it received before this node
existed. `tests/unit/test_pipeline_graph.py` asserts exactly that, because it
is the promise `PROMPT_VERSION` stays at v8 on.

**`generate ⇄ validate` is written down once** — `_add_repair_region` — and
built into both graphs. That is Phase 2's whole point: there were two executors
over one node set (this one and a hand-rolled `for` loop in
`sql_draft_service`), and every change to repair semantics had to be made twice
or diverge silently. It did diverge, twice, before anyone noticed. What the two
callers legitimately differ on travels in the invoke config — the deadline, the
event and step sinks, where a validated statement goes next — so a difference
now has to be *written* rather than drifted into.

LangGraph is confined to this package and `app/workers/` by an import-linter
contract and a CI grep — see `pyproject.toml`.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.core.clock import utcnow
from app.core.errors import LLMError, QuestionOutOfScopeError, RunTimeoutError
from app.core.logging import get_logger
from app.domain.value_objects import StepName, StepStatus
from app.pipeline import nodes
from app.pipeline.nodes import NodeDeps
from app.pipeline.state import NodeResult, RunError, RunState

log = get_logger(__name__)

NodeFn = Callable[[RunState, NodeDeps], Awaitable[NodeResult]]
OnStep = Callable[[int, str, str, str | None, int], Awaitable[None]]
#: `(state, node_name) -> None`, raising when the caller is out of time. Takes
#: the node name because the two callers deliberately check in different
#: places: a chat run before *every* node, a draft before each `generate` only.
DeadlineCheck = Callable[[RunState, str], None]
#: `(label, state) -> node name in this graph`. A node names where it wants to
#: go; which graph it is running in decides what that label means.
Router = Callable[[str, RunState], str]

ROUTE = str(StepName.ROUTE)
MATCH = str(StepName.MATCH)
RETRIEVE = str(StepName.RETRIEVE)
DESCRIBE = str(StepName.DESCRIBE)
CLARIFY = str(StepName.CLARIFY)
GENERATE = str(StepName.GENERATE)
VALIDATE = str(StepName.VALIDATE)
EXECUTE = str(StepName.EXECUTE)
INSPECT = str(StepName.INSPECT)
PRESENT = str(StepName.PRESENT)
CHART = str(StepName.CHART)

#: The draft graph's refusal. Not a `StepName` — it is not a step, it writes no
#: row, and it exists only to raise.
REFUSE = "refuse"

# The chat pipeline's linear chain, in order. This is no longer walked by index
# — the graphs' edges are — but it is still the source of which node follows
# which, and `tests/unit/test_clarify.py` reads it to assert `clarify` sits
# between `retrieve` and `generate`.
ORDER: list[tuple[str, NodeFn]] = [
    (StepName.ROUTE, nodes.route),
    # Between route and retrieve, and it is the one node in this list that can
    # leave the chain: a hit jumps to `validate` and the four nodes below it
    # never run. A miss changes nothing — no state written, no prompt altered —
    # which is the promise `PROMPT_VERSION` stays at v8 on.
    (StepName.MATCH, nodes.match),
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
# here is one node, because nothing in these graphs fans out and neither embeds
# the other — before raising `GraphRecursionError`. Same ceiling, caught at the
# facade and turned back into the same error.
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


class GraphState(TypedDict):
    """The chat graph's state: the existing model carried whole.

    Not decomposed into per-field reducers, deliberately. `RunState` is already
    the typed state the nodes were written against, mutation in place keeps
    working, and the adapter returns the mutated object as the update — so
    there is exactly one representation of a run in the process, and no reducer
    that could disagree with a node about what a field means.
    """

    run: RunState


class DraftState(TypedDict):
    """The draft graph's state.

    `run` means the same thing it does above. `classify` is graph *input*, not
    run state: it selects the entry edge, and it has no business on a model
    that a chat run checkpoints.
    """

    run: RunState
    classify: bool


class _Seq:
    """The run's step counter.

    Per invocation, never per graph: the graphs are compiled once at module
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


# ── what the two callers legitimately differ on ──────────────────────────
def _run_deadline(run: RunState, _node: str) -> None:
    """A chat run's deadline: before **every** node, and hard.

    `AnalyticsPipeline.run` has always checked here, and `run_service` turns
    the exception into `TIMED_OUT`.
    """
    if utcnow() >= run.deadline_at:
        run.error = RunError(
            code="E_TIMEOUT",
            message="The run exceeded its time budget.",
            hint="Try a narrower question, or raise the run deadline.",
        )
        raise RunTimeoutError(run.error.message)


async def _no_step(
    _seq: int, _name: str, _status: str, _detail: str | None, _ms: int
) -> None:
    """A caller with nowhere to put a step trail. The draft path has none."""
    return None


def _configurable(
    deps: NodeDeps,
    *,
    on_step: OnStep = _no_step,
    check_deadline: DeadlineCheck = _run_deadline,
    out_of_scope: Mapping[str, str] | None = None,
    classify: bool = False,
) -> dict[str, Any]:
    """Everything run-specific, in one place so the two callers cannot drift.

    `NodeDeps` holds a live connector and an `emit` callable, so it is not
    serializable and must never live in graph state. The rest is here for the
    same reason the deps are: the graphs are compiled once and shared, so
    nothing per-run may be closed over by an adapter.
    """
    return {
        "deps": deps,
        "on_step": on_step,
        "check_deadline": check_deadline,
        # The wording a refused question is stored under. It lives in the
        # service that reads it — this layer knows nothing about report blocks
        # or figures — and travels in as data.
        "out_of_scope": dict(out_of_scope or {}),
        "classify": classify,
        "seq": _Seq(),
    }


# ── routers ──────────────────────────────────────────────────────────────
def _straight(label: str, _run: RunState) -> str:
    """The label names a node in this graph. The chat graph's whole story."""
    return label


def _remap(mapping: Mapping[str, str]) -> Router:
    """…except for these labels, which this graph spells differently."""

    def router(label: str, _run: RunState) -> str:
        return mapping.get(label, label)

    return router


def _refuse_unless_analytical(_label: str, run: RunState) -> str:
    """The draft's classifier gate — an edge, not an `if` in the service.

    Deliberately ignores the label `route` produced. Chat reads CHITCHAT and
    UNSUPPORTED as a HALT with a canned reply, and lets METADATA through to
    `describe`; a draft has nowhere to put a reply and no `describe` to reach,
    so all three are the same answer here: this question has no figure in it.
    `route` fails open to ANALYTICAL on a provider error, so a flaky model
    refuses nothing.
    """
    if run.intent is not None and run.intent != "ANALYTICAL":
        return REFUSE
    return RETRIEVE


# ── the adapter ──────────────────────────────────────────────────────────
def _adapt(
    name: str,
    fn: NodeFn,
    *,
    successor: str,
    router: Router = _straight,
    deadline: bool = True,
) -> Callable[..., Awaitable[Command[str]]]:
    """Wrap a node function as a LangGraph node, executor duties included."""

    async def node(state: GraphState, config: RunnableConfig) -> Command[str]:
        run = state["run"]
        configurable = config["configurable"]
        deps: NodeDeps = configurable["deps"]
        on_step: OnStep = configurable["on_step"]

        # Before the node, never inside one. One `structured` call can take
        # minutes once the gateway's retries and backoff are counted, so a
        # check after the call would report a budget the caller had already
        # spent.
        if deadline:
            configurable["check_deadline"](run, name)

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

        label = (
            END
            if result.status in ("HALT", "FAILED")
            else (result.goto or successor)
        )
        return Command(goto=router(label, run), update={"run": run})

    node.__name__ = f"node_{name}"
    return node


# ── the repair region: generate ⇄ validate, written down once ────────────
@dataclass(frozen=True, slots=True)
class _RepairExits:
    """Where the region's three exits lead, for one caller.

    `repair` is not here because it is not an exit: `validate` returns
    `goto="generate"` while the budget allows, and that edge is internal to the
    region. The budget itself lives in state — `repair_count` is a derived
    property, `max(0, len(attempts) - 1)` — which is what lets the chat graph
    *re-enter* this region from `execute` and `inspect` without threading a
    counter through anything.
    """

    #: The guard accepted the statement.
    ok: str
    #: `_restore_superseded` put an earlier result back and wants to skip
    #: ahead. Chat-only in practice — a draft never runs `inspect`, so it has
    #: no `superseded_execution` to restore — but it is wired rather than
    #: assumed, because "unreachable" is a claim that rots.
    restore: str


def _add_repair_region(
    graph: Any, exits: _RepairExits, *, deadline_before_validate: bool
) -> None:
    """Ask the model for SQL, guard it, and try again if there is budget.

    The one implementation. Both graphs build it, so a change to how a repair
    works cannot reach one caller and miss the other — which is precisely how
    `RunState.deadline_at` came to be enforced on the chat path and inert on
    the draft path for as long as it was.

    `give_up` needs no exit parameter: a `FAILED` status routes to `END` in
    every graph, and what that *means* is the caller's business. Chat ends a
    failed run; a draft returns the rejected statement with its report, which
    the editor renders inline — a rejection there is an answer, not an error.
    """
    graph.add_node(
        GENERATE,
        _adapt(GENERATE, nodes.generate, successor=VALIDATE),
        destinations=(VALIDATE, END),
    )
    graph.add_node(
        VALIDATE,
        _adapt(
            VALIDATE,
            nodes.validate,
            successor=exits.ok,
            router=_remap({PRESENT: exits.restore}),
            # The chat executor has always checked before every node. The
            # draft checks before each `generate` and nowhere else, on purpose:
            # `validate` is the guard, it costs microseconds, and stopping a
            # draft *after* the model produced a statement the guard would have
            # accepted throws away work the user already waited for.
            deadline=deadline_before_validate,
        ),
        destinations=tuple({exits.ok, GENERATE, exits.restore, END}),
    )


# ── the chat graph ───────────────────────────────────────────────────────
_CHAT_EXITS = _RepairExits(ok=EXECUTE, restore=PRESENT)


def _build_chat() -> Any:
    graph: Any = StateGraph(GraphState)

    graph.add_node(ROUTE, _adapt(ROUTE, nodes.route, successor=MATCH),
                   destinations=(MATCH, END))
    # The short-circuit's two exits. A hit names `validate` and lands in the
    # repair region's guard; a miss falls through to `retrieve` and the run is
    # indistinguishable from one taken before this node existed.
    graph.add_node(MATCH, _adapt(MATCH, nodes.match, successor=RETRIEVE),
                   destinations=(RETRIEVE, VALIDATE, END))
    graph.add_node(RETRIEVE, _adapt(RETRIEVE, nodes.retrieve, successor=DESCRIBE),
                   destinations=(DESCRIBE, END))
    graph.add_node(DESCRIBE, _adapt(DESCRIBE, nodes.describe, successor=CLARIFY),
                   destinations=(CLARIFY, END))
    graph.add_node(CLARIFY, _adapt(CLARIFY, nodes.clarify, successor=GENERATE),
                   destinations=(GENERATE, END))

    _add_repair_region(graph, _CHAT_EXITS, deadline_before_validate=True)

    # The two re-entry edges are the parent's own, and they are why the region
    # did not swallow `execute` and `inspect`: a repair driven by the database
    # or by a structural finding restarts the region, and the budget in state
    # counts the attempts that already happened.
    graph.add_node(EXECUTE, _adapt(EXECUTE, nodes.execute, successor=INSPECT),
                   destinations=(INSPECT, GENERATE, PRESENT, END))
    graph.add_node(INSPECT, _adapt(INSPECT, nodes.inspect, successor=PRESENT),
                   destinations=(PRESENT, GENERATE, END))
    graph.add_node(PRESENT, _adapt(PRESENT, nodes.present, successor=CHART),
                   destinations=(CHART, END))
    graph.add_node(CHART, _adapt(CHART, nodes.chart, successor=END),
                   destinations=(END,))

    graph.add_edge(START, ROUTE)
    return graph


# ── the draft graph ──────────────────────────────────────────────────────
# `retrieve → generate ⇄ validate`, with `route` in front when the caller
# asked for it. No `describe` (a draft has nowhere to put a schema answer), no
# `clarify` (nobody to ask), and nothing after `validate` — the preview and the
# chart ask are service calls through `execute_saved_sql`, and dragging them in
# here to make the picture tidy would put guarded execution in the pipeline
# layer.
_DRAFT_EXITS = _RepairExits(ok=END, restore=END)


async def _refuse(state: DraftState, config: RunnableConfig) -> Command[str]:
    """A classified question with no data answer, refused before any SQL.

    A node rather than a service `if`, so the refusal is part of the graph and
    the entry edge is what turns it on. Deliberately not wrapped by `_adapt`:
    this raises on purpose, and the adapter's crash handler would turn a
    verdict the user is meant to read into `E_NODE_FAILED`.
    """
    run = state["run"]
    intent = run.intent or "UNSUPPORTED"
    wording: Mapping[str, str] = config["configurable"]["out_of_scope"]
    raise QuestionOutOfScopeError(wording[intent], intent=intent)


def _entry(state: DraftState) -> str:
    """The conditional entry edge `classify=True` selects.

    Off by default, so a tile draft sends exactly the calls it always sent —
    the classifier is a second model call, and the tile editor did not ask for
    one per draft.
    """
    return ROUTE if state["classify"] else RETRIEVE


def _build_draft() -> Any:
    graph: Any = StateGraph(DraftState)

    graph.add_node(
        ROUTE,
        _adapt(ROUTE, nodes.route, successor=RETRIEVE,
               router=_refuse_unless_analytical),
        destinations=(RETRIEVE, REFUSE, END),
    )
    graph.add_node(REFUSE, _refuse, destinations=(END,))
    graph.add_node(RETRIEVE, _adapt(RETRIEVE, nodes.retrieve, successor=GENERATE),
                   destinations=(GENERATE, END))

    _add_repair_region(graph, _DRAFT_EXITS, deadline_before_validate=False)

    graph.add_conditional_edges(START, _entry, {ROUTE: ROUTE, RETRIEVE: RETRIEVE})
    return graph


# Compiled **once, at import**. Both are reached from request handlers —
# `draft_sql` from `POST /sql/drafts` and the report-block `/check` — and a
# per-request `.compile()` would turn a sub-second authoring step into a
# measurable one. Neither holds anything run-specific: that all travels in the
# config.
CHAT_GRAPH = _build_chat().compile(name="chat")
DRAFT_GRAPH = _build_draft().compile(name="draft")


# ── the two facades ──────────────────────────────────────────────────────
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
                    "configurable": _configurable(deps, on_step=self._on_step),
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


async def draft_statement(
    state: RunState,
    deps: NodeDeps,
    *,
    classify: bool = False,
    check_deadline: DeadlineCheck,
    out_of_scope: Mapping[str, str],
) -> RunState:
    """The draft's walk: `[route →] retrieve → generate ⇄ validate`.

    Everything a draft is *not* travels in through the config rather than being
    reimplemented: no step trail (`on_step` is a no-op, and `deps.emit` is the
    caller's `_no_emit`), and the caller's own deadline rule, which raises the
    caller's own exception with the caller's own wording. What it shares with a
    chat run is the part that matters — the same schema block, the same
    prompts, the same guard, and the same repair loop.

    Raises `QuestionOutOfScopeError` when `classify` is on and the question has
    no data answer, `LLMError` when the model could not produce a statement or
    the deadline passed. A statement the guard *rejected* is not an exception:
    it comes back on `state.attempts[-1]` with its report, because the editor
    renders the guard's reasons inline.
    """
    try:
        await DRAFT_GRAPH.ainvoke(
            {"run": state, "classify": classify},
            config={
                "configurable": _configurable(
                    deps,
                    check_deadline=check_deadline,
                    out_of_scope=out_of_scope,
                    classify=classify,
                ),
                "recursion_limit": RECURSION_LIMIT,
            },
        )
    except GraphRecursionError as err:  # pragma: no cover - see below
        # Unreachable while `max_repairs` bounds the region: `validate` only
        # asks for a repair while `repair_count < max_repairs`, and a draft
        # sets that to `DRAFT_MAX_REPAIRS`. Caught anyway, because the
        # alternative to a wrong verdict here is a 500, and `check_block`
        # already stores an `LLMError` as the block's reason.
        log.warning("draft_loop", connection_id=str(state.connection_id))
        raise LLMError("Drafting SQL did not converge and was stopped.") from err

    return state
