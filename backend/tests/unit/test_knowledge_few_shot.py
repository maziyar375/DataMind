"""Few-shot injection — the one phase of this plan that can make it worse.

`PROMPT_VERSION` moves v8 → v9 here, and the whole of that move is one slot in
`GENERATE_SYSTEM`. Almost every test in this file is about the empty case,
because the empty case is the promise:

> **Off renders the v8 bytes.** A connection with no store, a connection with
> `knowledge_examples_enabled` off (the default), the draft graph and the
> templates-off eval arm all take that path, so every measurement recorded
> before v9 still holds for them.

Eval Round 2 measured an unconditional addition to this exact prompt costing
ten points of execution accuracy on a small model (36% → 26%) by crowding out
the schema. So the claims here, in the order they would hurt if they broke:

* nothing is added unless the connection asked for it;
* what is added is **last**, after the schema and the semantic layer, and is
  capped well below what catalog comments get;
* a `MODEL_DERIVED` template's literals are withheld under a closed disclosure
  policy — §5.2's rung, applied at **render** time like every other one;
* a run *answered* from the store offers no examples, because there is no
  generator to teach;
* a repair prompt gets none either: adding four statements to the prompt that
  produced a rejected one is the opposite of narrowing.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.domain.value_objects import DisclosurePolicy
from app.knowledge import (
    KnowledgeTemplate,
    LiteralProvenance,
    ParamType,
    TemplateParam,
    TemplateRole,
    TemplateStatus,
    normalize_question,
)
from app.knowledge.matcher import (
    FEW_SHOT_THRESHOLD,
    SHORT_CIRCUIT_THRESHOLD,
    Candidate,
)
from app.pipeline.nodes import NodeDeps, match, retrieve
from app.pipeline.prompts import PROMPT_VERSION
from app.pipeline.state import RetrievedContext, RunState, TemplateExample

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "created_at", "data_type": "date"},
            {"name": "region", "data_type": "text"},
            {"name": "amount", "data_type": "numeric"},
        ],
    }
]


def _template(
    question: str = "revenue by month for {region}",
    sql: str = "SELECT SUM(amount) FROM public.orders WHERE region = :region",
    provenance: LiteralProvenance = LiteralProvenance.HUMAN_AUTHORED,
    **kwargs: Any,
) -> KnowledgeTemplate:
    return KnowledgeTemplate(
        id=uuid4(), question=question,
        question_normalized=normalize_question(question), sql=sql,
        literal_provenance=provenance,
        params=[TemplateParam(
            name="region", type=ParamType.STRING, comment="one of: EMEA, NA, APAC"
        )],
        **kwargs,
    )


class FakeMatcher:
    def __init__(self, *candidates: Candidate) -> None:
        self.candidates = list(candidates)

    async def match(self, _q: str, _c: Any, *, limit: int = 5) -> Any:
        return self.candidates


def _state(question: str = "total revenue by month for EMEA") -> RunState:
    run = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    run.intent = "ANALYTICAL"
    return run


def _deps(matcher: Any = None, *, examples: bool = True, **kwargs: Any) -> NodeDeps:
    from app.knowledge import policy_from_tables

    async def emit(_t: str, _d: dict) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,
        snapshot={"tables": TABLES, "relationships": []},
        history=[], policy=policy_from_tables(TABLES, dialect="postgres"),
        emit=emit, matcher=matcher, examples_enabled=examples, **kwargs,
    )


# ── the version, and what it bought ──────────────────────────────────────
def test_the_prompt_version_is_v9() -> None:
    assert PROMPT_VERSION == "v9"


def test_the_empty_slot_is_byte_identical_to_v8() -> None:
    """The promise the whole phase rests on.

    Not "nearly the same" and not "semantically equivalent": the same bytes.
    A stray newline here silently invalidates every baseline in `docs/eval.md`
    and nobody finds out until a number moves for no reason.
    """
    from app.pipeline.prompts import GENERATE_SYSTEM

    v9_off = GENERATE_SYSTEM.format(
        dialect="postgres", schema="S", examples="", history="H"
    )
    # v8's template, spelled out rather than derived — the slot sits on a bare
    # line *between* two that already existed, so deleting the placeholder is
    # not the same edit as rendering it empty, and only the second is what
    # ships.
    v8 = GENERATE_SYSTEM.replace("{schema}\n{examples}\n{history}",
                                 "{schema}\n\n{history}").format(
        dialect="postgres", schema="S", history="H"
    )
    assert v9_off == v8
    assert "Schema:\nS\n\nH\n\nReply with the JSON object only" in v9_off


# ── nothing is added unless it was asked for ─────────────────────────────
async def test_examples_are_off_by_default_on_node_deps() -> None:
    # A `NodeDeps` built without the connection in hand renders what it always
    # rendered — the rule `clarify_enabled` and `include_db_comments` follow.
    from app.knowledge import policy_from_tables

    async def emit(_t: str, _d: dict) -> None:
        return None

    deps = NodeDeps(
        llm_gateway=None, llm=None, connector=None, snapshot={"tables": TABLES},
        history=[], policy=policy_from_tables(TABLES, dialect="postgres"), emit=emit,
    )
    assert deps.examples_enabled is False


async def test_the_switch_off_collects_nothing(monkeypatch: Any) -> None:
    run = _state()
    matcher = FakeMatcher(Candidate(_template(), 0.60))
    await match(run, _deps(matcher, examples=False))

    assert run.examples == []
    await retrieve(run, _deps(matcher, examples=False))
    assert run.context is not None and run.context.examples == []
    assert run.context.render_examples(DisclosurePolicy.FULL) == ""


async def test_a_near_miss_becomes_an_example_when_the_switch_is_on() -> None:
    run = _state()
    result = await match(run, _deps(FakeMatcher(Candidate(_template(), 0.60))))

    # Still a miss — the run continues to `retrieve` exactly as it did.
    assert result.goto is None
    assert len(run.examples) == 1
    assert run.examples[0].question == "revenue by month for {region}"
    assert "offered as an example" in (result.detail or "")


async def test_below_the_few_shot_threshold_nothing_is_offered() -> None:
    """Two thresholds, and the lower one is still a threshold.

    A template sharing three words with the question is not an example of how
    to answer it, and paying schema tokens for it is the change that cost ten
    points last time.
    """
    run = _state()
    await match(run, _deps(FakeMatcher(Candidate(_template(), FEW_SHOT_THRESHOLD - 0.01))))
    assert run.examples == []


async def test_a_short_circuit_offers_no_examples() -> None:
    """A run answered from the store has no generator to teach.

    Offering the template it just used as an example of itself would be a
    prompt about a call that never happens.
    """
    run = _state("revenue by month for EMEA")
    result = await match(
        run, _deps(FakeMatcher(Candidate(_template(), SHORT_CIRCUIT_THRESHOLD + 0.05)))
    )
    assert result.goto == "validate"
    assert run.examples == []


async def test_retrieve_carries_whatever_match_left() -> None:
    run = _state()
    deps = _deps(FakeMatcher(Candidate(_template(), 0.60)))
    await match(run, deps)
    await retrieve(run, deps)

    assert run.context is not None
    assert [e.question for e in run.context.examples] == [
        "revenue by month for {region}"
    ]


# ── the disclosure gate (§5.2), at render time ───────────────────────────
@pytest.mark.parametrize(
    "policy", (DisclosurePolicy.NONE, DisclosurePolicy.AGGREGATE)
)
def test_a_model_derived_example_is_withheld_under_a_closed_policy(
    policy: str,
) -> None:
    """The rung nobody else got right, on the path that would bypass it.

    `WHERE region = 'EMEA'` in an example puts a column value in front of the
    model on a connection whose policy says none may go. The whole example is
    withheld rather than stripped: there is no way to remove a literal from a
    `WHERE` clause and leave a statement that still teaches anything.
    """
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(
            question="revenue for EMEA", sql="SELECT 1 WHERE region = 'EMEA'",
            literal_provenance="MODEL_DERIVED",
        )
    ])
    assert context.render_examples(policy) == ""


@pytest.mark.parametrize("policy", (DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL))
def test_a_model_derived_example_travels_once_values_may(policy: str) -> None:
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(
            question="revenue for EMEA", sql="SELECT 1 WHERE region = 'EMEA'",
            literal_provenance="MODEL_DERIVED",
        )
    ])
    assert "revenue for EMEA" in context.render_examples(policy)


def test_a_human_authored_example_travels_under_every_policy() -> None:
    # A person typed it, it was not read from a row, and it does not change
    # when the data changes — the three tests a catalog comment passes.
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(question="revenue for EMEA", sql="SELECT 1")
    ])
    for policy in DisclosurePolicy:
        assert "revenue for EMEA" in context.render_examples(policy)


def test_the_gate_is_applied_at_render_not_at_match() -> None:
    """Tightening a policy takes effect on the next question, not the next sync.

    The same discipline `disclose()`, `HintBudget` and `disclose_history()`
    follow — and the reason is that a store filtered at write time would
    survive the tightening and quietly undo it.
    """
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(question="q", sql="SELECT 1",
                        literal_provenance="MODEL_DERIVED")
    ])
    assert context.render_examples(DisclosurePolicy.FULL) != ""
    assert context.render_examples(DisclosurePolicy.NONE) == ""
    # And the example is still on the context: nothing was deleted.
    assert len(context.examples) == 1


# ── the budget: last, and small ──────────────────────────────────────────
def test_examples_come_after_the_schema_and_the_semantic_layer() -> None:
    """The priority order, asserted on the rendered prompt rather than assumed.

    Schema first, semantic layer second, examples third. Examples that crowd
    out the schema are exactly the change that scored 36% → 26%.
    """
    from app.pipeline.prompts import GENERATE_SYSTEM

    context = RetrievedContext(dialect="postgres", tables=TABLES, examples=[
        TemplateExample(question="taught question", sql="SELECT 1")
    ])
    rendered = GENERATE_SYSTEM.format(
        dialect="postgres",
        schema=context.render(DisclosurePolicy.FULL),
        examples=context.render_examples(DisclosurePolicy.FULL),
        history="",
    )
    assert rendered.index("public.orders") < rendered.index("taught question")


def test_a_long_example_is_skipped_whole_never_truncated() -> None:
    # Half a statement is a worse input than no statement, and a long one that
    # ended the walk would shut out the short ones behind it.
    long_sql = "SELECT " + ", ".join(f"col_{i}" for i in range(200))
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(question="the enormous one", sql=long_sql),
        TemplateExample(question="the short one", sql="SELECT 1"),
    ])
    rendered = context.render_examples(DisclosurePolicy.FULL)

    assert "the enormous one" not in rendered
    assert "the short one" in rendered


def test_at_most_four_examples_reach_the_prompt() -> None:
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(question=f"question {i}", sql="SELECT 1") for i in range(9)
    ])
    rendered = context.render_examples(DisclosurePolicy.FULL)
    assert rendered.count("- Q:") == 4


def test_the_block_has_a_ceiling_and_it_is_small() -> None:
    """A fifth of what catalog comments get, deliberately.

    This block is the last thing added to a prompt that has already proved it
    is sensitive to unconditional additions.
    """
    from app.pipeline.state import _COMMENT_CHARS_BLOCK, _EXAMPLE_CHARS_BLOCK

    assert _EXAMPLE_CHARS_BLOCK < _COMMENT_CHARS_BLOCK

    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(question=f"question {i}", sql="SELECT " + "x" * 400)
        for i in range(4)
    ])
    assert len(context.render_examples(DisclosurePolicy.FULL)) < _EXAMPLE_CHARS_BLOCK + 400


def test_an_empty_example_is_not_a_header_alone() -> None:
    context = RetrievedContext(dialect="postgres", tables=[], examples=[
        TemplateExample(question="", sql=""),
    ])
    assert context.render_examples(DisclosurePolicy.FULL) == ""


# ── the eval arm ─────────────────────────────────────────────────────────
def test_the_held_out_split_is_deterministic() -> None:
    """Two runs of one arm that held out different questions are two different
    measurements, and nobody would notice."""
    from app.eval.dataset import load_gold_suite
    from app.eval.runner import held_out_ids

    records = load_gold_suite("sales_v1").records
    assert held_out_ids(records) == held_out_ids(records)
    assert held_out_ids(records)


def test_the_store_never_contains_a_held_out_question() -> None:
    from app.eval.dataset import load_gold_suite
    from app.eval.runner import build_template_store, held_out_ids

    records = load_gold_suite("sales_v1").records
    held = held_out_ids(records)
    store = build_template_store(records, held)

    stored = {t.question for t in store}
    assert stored.isdisjoint({r.question for r in records if r.id in held})
    assert 0 < len(store) < len(records)


async def test_a_record_is_never_measured_against_its_own_template() -> None:
    """§1.3's measurement trap, as the arm's own rule.

    A question answered from its own stored SQL measures the store's ability to
    hold a string. Held out or not, every record's own row is excluded from the
    store it is evaluated against.
    """
    from app.eval.dataset import load_gold_suite
    from app.eval.runner import build_template_store, held_out_ids, matcher_over

    records = load_gold_suite("sales_v1").records
    taught = [r for r in records if r.id not in held_out_ids(records)]
    store = build_template_store(records, held_out_ids(records))

    subject = taught[0]
    found = await matcher_over(store, exclude=subject.id).match(
        subject.question, uuid4(), limit=50
    )
    assert subject.question not in {c.template.question for c in found}


def test_the_arm_is_off_by_default_on_the_cli() -> None:
    from app.eval.runner import build_parser

    assert build_parser().parse_args(["--suite", "sales_v1"]).templates == "off"


# ── withdrawal, again, from this path ────────────────────────────────────
@pytest.mark.parametrize(
    "status", (TemplateStatus.STALE, TemplateStatus.CONFLICTED)
)
async def test_a_withdrawn_template_is_never_offered_as_an_example(
    status: TemplateStatus,
) -> None:
    """Phase 4's states hold on Phase 5's path too.

    A stale template teaching the generator SQL the schema no longer supports
    is a worse failure than a stale template refusing to answer: the model is
    being shown a wrong pattern and will copy it.
    """
    from app.knowledge.matcher import LexicalMatcher

    withdrawn = _template(status=status)

    async def rows(_c: Any, _n: str, _limit: int) -> list[Any]:
        return [withdrawn]

    run = _state("revenue by month for EMEA")
    await match(run, _deps(LexicalMatcher(rows)))
    assert run.examples == []


async def test_a_benchmark_template_is_never_offered_as_an_example() -> None:
    # §1.3: a row that exists to measure may not also be the thing measured.
    from app.knowledge.matcher import LexicalMatcher

    held = _template(role=TemplateRole.HELD_OUT)

    async def rows(_c: Any, _n: str, _limit: int) -> list[Any]:
        return [held]

    run = _state("revenue by month for EMEA")
    await match(run, _deps(LexicalMatcher(rows)))
    assert run.examples == []
