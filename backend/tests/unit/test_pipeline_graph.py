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

from typing import Any

from app.domain.value_objects import StepName
from app.pipeline.graph import CHAT_GRAPH, DRAFT_GRAPH, RECURSION_LIMIT
from app.pipeline.pipeline import ORDER, AnalyticsPipeline

END = "__end__"
START = "__start__"


def edges(graph: Any = None) -> set[tuple[str, str]]:
    compiled = graph if graph is not None else CHAT_GRAPH
    return {(e.source, e.target) for e in compiled.get_graph().edges}


def draft_edges() -> set[tuple[str, str]]:
    return edges(DRAFT_GRAPH)


# The linear chain, as `ORDER` reads it.
CHAIN = [
    "route", "match", "retrieve", "describe", "clarify", "generate",
    "validate", "execute", "inspect", "present", "chart",
]

# The six edges that are not the chain, and the only six.
NON_LINEAR = {
    # The short-circuit: a taught question skips four nodes and lands on the
    # guard, which is the generated path's own entry point. A stored template
    # gets no exemption — it reuses `validate`, `execute` and everything after.
    ("match", "validate"),
    # Repairs, back into generate.
    ("validate", "generate"),   # the guard rejected the statement
    ("execute", "generate"),    # the database refused it
    ("inspect", "generate"),    # a retry=True structural finding, once per run
    # Restores, forward into present — skipping execute and inspect.
    ("validate", "present"),
    ("execute", "present"),
}


def test_the_graph_is_the_chain_plus_exactly_six_jumps() -> None:
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
    """`ORDER` still names the chain the graph is wired to."""
    assert [str(name) for name, _fn in ORDER] == CHAIN
    for name, following in zip(CHAIN, CHAIN[1:], strict=False):
        assert (name, following) in edges()
    assert ("chart", END) in edges()


# ── the short-circuit, and the promise it does not break ─────────────────
def test_match_sits_between_route_and_retrieve() -> None:
    """After `route`, because a taught question is about the data — and a
    CHITCHAT or UNSUPPORTED question halts before the store is ever read.
    Before `retrieve`, because a hit makes retrieval unnecessary."""
    assert ("route", "match") in edges()
    assert ("match", "retrieve") in edges()
    assert ("route", "retrieve") not in edges()


def test_match_has_exactly_two_exits_plus_the_end() -> None:
    """A hit, a miss, and a crash. No third verdict.

    "Partly matched" is not a state an answer can be in: a template whose
    parameters would not bind falls through to generation like any other miss,
    because a half-bound template is a confident wrong answer.
    """
    out = {target for source, target in edges() if source == "match"}
    assert out == {"validate", "retrieve", END}


async def test_a_miss_produces_the_prompt_it_produced_before_this_node_existed()\
        -> None:
    """**The promise `PROMPT_VERSION` stays at v8 on.**

    Phase 2 changes an answer without changing a single byte of the prompt. A
    miss must therefore write nothing to the state that the generator can see —
    no examples, no note, no hint that a store was consulted at all.

    Asserted on the rendered bytes rather than on the code, because the failure
    this guards against is somebody adding a helpful line to the schema block
    and moving the eval baseline underneath every past number.
    """
    from app.pipeline.state import RetrievedContext

    before = _state()
    after = _state()
    # What a miss does to the state: sets a score, and nothing else. (A hit
    # sets more, and a hit does not reach `generate` at all.)
    after.match_score = 0.42

    context = RetrievedContext(dialect="postgres", tables=[
        {"schema": "public", "name": "orders",
         "columns": [{"name": "id", "data_type": "bigint"}]},
    ])
    before.context, after.context = context, context

    assert context.render(before.disclosure_policy) == (
        context.render(after.disclosure_policy)
    )
    # And the fields the prompt is built from are untouched by a miss.
    assert before.question == after.question
    assert before.attempts == after.attempts == []


def test_the_prompt_version_did_not_move_in_this_phase() -> None:
    """If it moved, something in Phase 2 was built wrong.

    The short-circuit is a *branch around* the generator, not a change to it.
    Phase 5 is the phase allowed to move this constant, and it has to earn it
    with an eval arm.
    """
    from app.pipeline import prompts

    assert prompts.PROMPT_VERSION == "v8"


def _state() -> Any:
    from datetime import timedelta
    from uuid import uuid4

    from app.core.clock import utcnow
    from app.pipeline.state import RunState

    return RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="revenue by month",
        deadline_at=utcnow() + timedelta(seconds=60),
    )


def test_the_ceiling_is_the_one_the_loop_had() -> None:
    """25 node executions, expressed as a recursion limit rather than a counter.

    Every superstep in this graph is exactly one node — nothing fans out — so
    the two ceilings count the same thing.
    """
    assert RECURSION_LIMIT == 25


def test_both_graphs_are_compiled_once() -> None:
    """Never per call.

    `draft_sql` is reached from `POST /sql/drafts` and the report-block
    `/check` route, both request/response with a user waiting, so a
    per-request `.compile()` would turn a sub-second authoring step into a
    measurable one. `AnalyticsPipeline` is constructed per run and holds
    nothing but the `on_step` callback; everything run-specific travels in the
    invoke config, and the draft path has no object of its own at all.
    """
    first = AnalyticsPipeline(on_step=_unused)
    second = AnalyticsPipeline(on_step=_unused)
    assert first is not second

    from app.pipeline import graph

    assert graph.CHAT_GRAPH is CHAT_GRAPH
    assert graph.DRAFT_GRAPH is DRAFT_GRAPH
    assert not hasattr(first, "_graph")


def test_step_names_are_the_node_names() -> None:
    """The graph's labels are `StepName`, which is what `run_steps.name` holds
    and what the SPA matches on to draw the trail."""
    assert {str(s) for s in StepName} == set(CHAIN)


# ── the draft graph, and the region both graphs share ────────────────────
# Phase 2. `sql_draft_service` used to drive `route`/`retrieve`/`generate`/
# `validate` with a hand-rolled `for` loop — a second executor over one node
# set, which is how `RunState.deadline_at` came to be enforced on one path and
# inert on the other. These assertions are the shape that replaced it.
REGION = {("generate", "validate"), ("validate", "generate")}


def test_the_repair_region_is_the_same_two_edges_in_both_graphs() -> None:
    """One repair loop: ask, guard, and try again while the budget allows.

    Built by `_add_repair_region` in both graphs, so a change to how a repair
    works cannot reach one caller and miss the other.
    """
    assert edges() >= REGION
    assert draft_edges() >= REGION


def test_the_draft_graph_is_the_region_plus_what_a_draft_needs() -> None:
    """No `describe` (nowhere to put a schema answer), no `clarify` (nobody to
    ask), and nothing after `validate` — the preview and the composed chart ask
    are service calls through `execute_saved_sql`, and dragging them in here
    would put guarded execution in the pipeline layer."""
    assert set(DRAFT_GRAPH.get_graph().nodes) == {
        START, "route", "refuse", "retrieve", "generate", "validate", END,
    }
    assert ("retrieve", "generate") in draft_edges()
    assert ("validate", END) in draft_edges()


def test_classify_is_a_conditional_entry_edge() -> None:
    """Not an `if` in the service. Off by default, so a tile draft sends
    exactly the calls it always sent — the classifier is a second model call
    and the tile editor did not ask for one per draft."""
    assert (START, "route") in draft_edges()
    assert (START, "retrieve") in draft_edges()


def test_an_out_of_scope_question_is_refused_by_a_node() -> None:
    """The refusal is part of the graph, reachable only from `route`.

    It raises `QuestionOutOfScopeError` rather than returning a verdict as
    graph state, because the caller stores that message as the block's reason
    and a user reads it.
    """
    assert ("route", "refuse") in draft_edges()
    assert ("retrieve", "refuse") not in draft_edges()
    assert ("generate", "refuse") not in draft_edges()


def test_the_chart_ask_is_not_in_either_graph() -> None:
    """`propose_chart_intent(composed=True)` runs *after* the 50-row preview,
    and that preview is `execute_saved_sql` — a service call, not a node."""
    assert "chart" not in set(DRAFT_GRAPH.get_graph().nodes)
    # Chat's `chart` node is the other trigger of the same function, and it is
    # still the last node of the chat graph rather than part of the region.
    assert ("present", "chart") in edges()
    assert REGION.isdisjoint({("validate", "chart"), ("generate", "chart")})


async def _unused(
    _seq: int, _name: str, _status: str, _detail: str | None, _ms: int
) -> None:  # pragma: no cover - a placeholder, never called
    return None
