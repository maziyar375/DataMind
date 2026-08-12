"""The wiring itself, read off the compiled graph.

`tests/unit/test_pipeline_events.py` proves the graph *behaves* like the
`while` loop it replaced, one control flow per run. This file proves the
edges exist at all — which is a different failure. An edge nothing walks in a
test is an edge that can be quietly dropped, and the two restore edges are
exactly that kind: `_restore_superseded` fires only when a check-driven retry
fails, which is not a path anyone reaches by hand.

The port map from [docs/langgraph-migration.md](../../../docs/langgraph-migration.md)
§4 Phase 1, in one assertion.
"""
from __future__ import annotations

from app.domain.value_objects import StepName
from app.pipeline.graph import CHAT_GRAPH, RECURSION_LIMIT, _next
from app.pipeline.pipeline import ORDER, AnalyticsPipeline

END = "__end__"
START = "__start__"


def edges() -> set[tuple[str, str]]:
    graph = CHAT_GRAPH.get_graph()
    return {(e.source, e.target) for e in graph.edges}


# The linear chain, as `ORDER` reads it.
CHAIN = [
    "route", "retrieve", "describe", "clarify", "generate",
    "validate", "execute", "inspect", "present", "chart",
]

# The five edges that are not the chain, and the only five.
NON_LINEAR = {
    # Repairs, back into generate.
    ("validate", "generate"),   # the guard rejected the statement
    ("execute", "generate"),    # the database refused it
    ("inspect", "generate"),    # a retry=True structural finding, once per run
    # Restores, forward into present — skipping execute and inspect.
    ("validate", "present"),
    ("execute", "present"),
}


def test_the_graph_is_the_chain_plus_exactly_five_jumps() -> None:
    linear = {(a, b) for a, b in zip(CHAIN, CHAIN[1:], strict=False)}
    # Every node can end the run: HALT, FAILED, or a node crash.
    halts = {(name, END) for name in CHAIN}

    assert edges() == {(START, "route")} | linear | halts | NON_LINEAR


def test_every_non_linear_edge_is_present() -> None:
    """Spelled out separately, so a diff names the edge that went missing."""
    for source, target in sorted(NON_LINEAR):
        assert (source, target) in edges(), f"{source} -> {target} is not wired"


def test_describe_and_clarify_are_nodes_on_the_chain_not_edges_around_it() -> None:
    """The tempting Phase 1 "improvement", refused in the wiring.

    A conditional edge out of `retrieve` routing METADATA to `describe` and
    everything else straight to `generate` looks equivalent and is not: a
    skipped node still writes a `run_steps` row and still emits its event pair,
    and an edge that routes around it emits neither — which shifts every later
    `seq` on every analytical run. If the trail should stop showing skipped
    nodes, that is a product decision made on its own, with the event snapshots
    updated deliberately.
    """
    assert ("retrieve", "describe") in edges()
    assert ("describe", "clarify") in edges()
    assert ("clarify", "generate") in edges()
    assert ("retrieve", "generate") not in edges()
    assert ("retrieve", "clarify") not in edges()
    assert ("describe", "generate") not in edges()


def test_the_chain_matches_order() -> None:
    """`ORDER` is still the source of which node follows which."""
    assert [str(name) for name, _fn in ORDER] == CHAIN
    for name, following in zip(CHAIN, CHAIN[1:], strict=False):
        assert _next(name) == following
    assert _next("chart") == END


def test_the_ceiling_is_the_one_the_loop_had() -> None:
    """25 node executions, expressed as a recursion limit rather than a counter.

    Every superstep in this graph is exactly one node — nothing fans out — so
    the two ceilings count the same thing.
    """
    assert RECURSION_LIMIT == 25


def test_the_graph_is_compiled_once() -> None:
    """Never per call.

    `draft_sql` is reached from `POST /sql/drafts` and the report-block
    `/check` route, both request/response with a user waiting, so a
    per-request `.compile()` would turn a sub-second authoring step into a
    measurable one. `AnalyticsPipeline` is constructed per run and holds
    nothing but the `on_step` callback; everything run-specific travels in the
    invoke config.
    """
    first = AnalyticsPipeline(on_step=_unused)
    second = AnalyticsPipeline(on_step=_unused)
    assert first is not second

    from app.pipeline import graph

    assert graph.CHAT_GRAPH is CHAT_GRAPH
    assert not hasattr(first, "_graph")


def test_step_names_are_the_node_names() -> None:
    """The graph's labels are `StepName`, which is what `run_steps.name` holds
    and what the SPA matches on to draw the trail."""
    assert {str(s) for s in StepName} == set(CHAIN)


async def _unused(
    _seq: int, _name: str, _status: str, _detail: str | None, _ms: int
) -> None:  # pragma: no cover - a placeholder, never called
    return None
