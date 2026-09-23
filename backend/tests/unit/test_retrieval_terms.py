"""Business vocabulary reaches retrieval — mvp2 A5.

The semantic layer's labels, synonyms, metric names and glossary have always
rendered into the *generate* prompt, where they explain a table the retriever
has already chosen. mvp2 §A5 asks for the other direction: **the word someone
wrote down is how the table gets chosen in the first place**, so "churn" finds
`subscription_events` because a curator said so once.

Two properties, and the second is what licenses the first:

* a question asked in the business's own words promotes the table that word
  names, ahead of the tables it merely joins to;
* **a connection with no semantic layer ranks exactly as it did at v10** — the
  new tier is empty, and the four tiers that existed keep their relative order.

The branch this changes is `RANKED_MATCH`, and only it. The other three either
send every table or spend the budget by `select_tables`' rule, so a business
word cannot change what they select — which is why the arm that can measure
this is a *lowered* retrieve budget with the layer on, and why a default-budget
A/B would correctly report nothing.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.metadata import match_by_terms, match_tables
from app.pipeline.nodes import _layer_index, fit_to_budget
from app.semantic import (
    TERM_MAX_TABLES,
    GlossaryTerm,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    table_terms,
)


def table(name: str, *columns: str, schema: str = "public", rows: int = 0) -> dict[str, Any]:
    return {
        "schema": schema,
        "name": name,
        "approx_row_count": rows,
        "columns": [{"name": c, "data_type": "text"} for c in columns],
    }


SNAPSHOT = [
    table("subscription_events", "id", "customer_id", "event_type", "occurred_at"),
    table("orders", "id", "customer_id", "total", "status"),
    table("order_items", "id", "order_id", "product_id", "qty", "price"),
    table("customers", "id", "name", "created_at"),
    table("products", "id", "name", "category"),
]


def entity(tbl: str, **kw: Any) -> SemanticEntity:
    return SemanticEntity(table=f"public.{tbl}", **kw)


LAYER = SemanticDocument(
    entities=[
        entity(
            "subscription_events",
            label="Subscription events",
            synonyms=["churn", "retention"],
            columns=[SemanticColumn(name="event_type", label="Event kind")],
        ),
        entity(
            "order_items",
            label="Order lines",
            metrics=[
                SemanticMetric(
                    name="net_revenue", label="Net revenue", expression="SUM(price)"
                )
            ],
        ),
        entity("customers", label="Customers", synonyms=["accounts"]),
    ],
    glossary=[
        GlossaryTerm(term="AOV", meaning="average order value", maps_to=["net_revenue"]),
        GlossaryTerm(term="logo", meaning="a customer", maps_to=["public.customers"]),
    ],
)


# ── the index ────────────────────────────────────────────────────────────


def test_a_synonym_names_its_table() -> None:
    index = table_terms(LAYER)
    assert "churn" in index["public.subscription_events"]
    assert "retention" in index["public.subscription_events"]


def test_a_metric_name_is_vocabulary_because_nothing_else_can_match_it() -> None:
    """`net_revenue` appears nowhere in the schema — no column is called that,
    so `match_tables(columns=True)` can never find it."""
    index = table_terms(LAYER)
    assert {"net_revenue", "net revenue"} <= index["public.order_items"]


def test_a_glossary_term_lands_on_the_table_its_metric_is_measured_over() -> None:
    """`AOV` maps to a metric, not a table. It has to resolve through the
    metric to the entity that owns it, or the commonest kind of glossary entry
    indexes nothing."""
    assert "aov" in table_terms(LAYER)["public.order_items"]


def test_a_glossary_term_may_also_name_a_table_directly() -> None:
    assert "logo" in table_terms(LAYER)["public.customers"]


def test_a_maps_to_that_resolves_to_nothing_is_dropped_not_guessed() -> None:
    doc = SemanticDocument(
        entities=[entity("orders", label="Orders")],
        glossary=[GlossaryTerm(term="widget", meaning="?", maps_to=["public.nowhere"])],
    )
    assert all("widget" not in terms for terms in table_terms(doc).values())


def test_physical_names_are_absent_on_purpose() -> None:
    """`match_tables` owns how the database spells things. Repeating them here
    would let the layer re-assert a physical hit on the business tier."""
    index = table_terms(LAYER)
    assert "subscription_events" not in index["public.subscription_events"]
    assert "event_type" not in index["public.subscription_events"]


def test_an_excluded_entity_contributes_nothing() -> None:
    doc = SemanticDocument(
        entities=[entity("orders", label="Orders", synonyms=["sales"], exclude=True)]
    )
    assert table_terms(doc) == {}


def test_an_invalid_entry_contributes_nothing() -> None:
    """A layer entry the renderer keeps out of the prompt must not steer
    retrieval either — the model would be handed a table chosen by a word it
    was never shown."""
    doc = SemanticDocument(
        entities=[
            entity(
                "orders",
                label="Orders",
                valid=False,
                synonyms=["sales"],
            ),
            entity(
                "order_items",
                label="Lines",
                columns=[SemanticColumn(name="gone", label="Ghost", valid=False)],
                metrics=[
                    SemanticMetric(name="broken", label="Broken", valid=False),
                ],
            ),
        ]
    )
    index = table_terms(doc)
    assert "public.orders" not in index
    assert index["public.order_items"] == frozenset({"lines"})


def test_a_word_on_too_many_tables_is_dropped_because_it_narrows_nothing() -> None:
    """"Name" is a column label every table in a warehouse carries. Keeping it
    would promote five tables into the business tier and push the ones the
    question actually named below them."""
    doc = SemanticDocument(
        entities=[
            entity(
                f"t{i}",
                label=f"Table {i}",
                columns=[SemanticColumn(name="n", label="Name")],
            )
            for i in range(TERM_MAX_TABLES + 1)
        ]
    )
    index = table_terms(doc)
    assert all("name" not in terms for terms in index.values())
    # The labels that name one table each are untouched.
    assert "table 0" in index["public.t0"]


def test_a_word_on_exactly_the_limit_survives() -> None:
    doc = SemanticDocument(
        entities=[
            entity(f"t{i}", columns=[SemanticColumn(name="n", label="Name")])
            for i in range(TERM_MAX_TABLES)
        ]
    )
    assert all("name" in terms for terms in table_terms(doc).values())


def test_a_two_character_phrase_is_not_a_word_anyone_typed() -> None:
    doc = SemanticDocument(entities=[entity("orders", label="Orders", synonyms=["so"])])
    assert table_terms(doc)["public.orders"] == frozenset({"orders"})


# ── the matching ─────────────────────────────────────────────────────────


def test_a_business_word_finds_the_table_no_physical_name_would() -> None:
    index = table_terms(LAYER)
    hits = match_by_terms("how bad is churn this quarter?", SNAPSHOT, index)
    assert [t["name"] for t in hits] == ["subscription_events"]
    # And nothing about the question names that table physically.
    assert match_tables("how bad is churn this quarter?", SNAPSHOT) == []


def test_a_multi_word_phrase_is_matched_as_a_phrase() -> None:
    index = table_terms(LAYER)
    assert [t["name"] for t in match_by_terms("net revenue by month", SNAPSHOT, index)] == [
        "order_items"
    ]
    # The same two words apart are not the phrase.
    assert match_by_terms("revenue, net of returns", SNAPSHOT, index) == []


def test_a_single_word_must_be_a_whole_token() -> None:
    """The rule `match_tables` already applies, applied by the same helper so
    the two tiers stay comparable."""
    index = table_terms(LAYER)
    assert match_by_terms("what does the churner cohort do?", SNAPSHOT, index) == []


def test_hits_come_back_in_snapshot_order() -> None:
    index = table_terms(LAYER)
    hits = match_by_terms("accounts and churn", SNAPSHOT, index)
    assert [t["name"] for t in hits] == ["subscription_events", "customers"]


def test_a_layer_naming_a_table_the_snapshot_lost_selects_nothing() -> None:
    doc = SemanticDocument(entities=[entity("gone_away", label="Ghosts")])
    assert match_by_terms("show me ghosts", SNAPSHOT, table_terms(doc)) == []


def test_no_layer_is_an_empty_index_and_no_work() -> None:
    assert match_by_terms("churn", SNAPSHOT, {}) == []
    assert _layer_index(None).terms == {}
    assert _layer_index({}).terms == {}


def test_a_malformed_layer_degrades_rather_than_raising() -> None:
    """A document that will not parse is not a reason to fail a question — the
    renderer takes the same fail-open on the same input."""
    assert _layer_index({"entities": "not a list"}).terms == {}


def test_the_index_is_built_from_a_plain_dict_as_deps_carries_it() -> None:
    index = _layer_index(LAYER.model_dump(mode="json")).terms
    assert "churn" in index["public.subscription_events"]


# ── the ranking ──────────────────────────────────────────────────────────

RELS = [
    {"from_table": "public.order_items", "to_table": "public.orders"},
    {"from_table": "public.order_items", "to_table": "public.products"},
    {"from_table": "public.orders", "to_table": "public.customers"},
]


def _fit(budget: int, **kw: Any) -> list[str]:
    selected, _ = fit_to_budget(
        SNAPSHOT,
        named=kw.get("named", []),
        carried=kw.get("carried", []),
        by_column=kw.get("by_column", []),
        by_term=kw.get("by_term", []),
        relationships=RELS,
        budget_chars=budget,
    )
    return [t["name"] for t in selected]


def test_a_term_hit_outranks_everything_but_a_name_the_user_typed() -> None:
    """One table's worth of budget. The business hit is what survives."""
    one = 60 + 40 * 4
    kept = _fit(
        one,
        by_term=[SNAPSHOT[0]],
        carried=[SNAPSHOT[1]],
        by_column=[SNAPSHOT[3]],
    )
    assert kept == ["subscription_events"]


def test_a_physical_name_still_wins_over_a_curator_s_label() -> None:
    """A physical name is the user's own word; a label is somebody else's."""
    one = 60 + 40 * 4
    assert _fit(one, named=[SNAPSHOT[1]], by_term=[SNAPSHOT[0]]) == ["orders"]


def test_a_term_hit_outranks_a_carried_table() -> None:
    """A curator naming this exact table is a statement about *this* question;
    a carried table is inherited from the last one."""
    one = 60 + 40 * 4
    assert _fit(one, by_term=[SNAPSHOT[0]], carried=[SNAPSHOT[1]]) == [
        "subscription_events"
    ]


def test_without_a_layer_the_four_old_tiers_keep_their_order() -> None:
    """The v10 guarantee. The new tier is empty on a connection with no layer,
    so `named > carried > by_column > fk-hop > rest` is exactly what it was —
    asserted rather than assumed, because renumbering the tiers is what would
    break it silently."""
    two = 2 * (60 + 40 * 5)
    assert _fit(two, named=[SNAPSHOT[1]], carried=[SNAPSHOT[3]]) == [
        "orders",
        "customers",
    ]
    assert _fit(two, carried=[SNAPSHOT[3]], by_column=[SNAPSHOT[4]]) == [
        "customers",
        "products",
    ]
    # An FK neighbour of a seed still beats a table that is nothing to it.
    assert _fit(two, named=[SNAPSHOT[1]])[0] == "orders"


def test_by_term_defaults_to_empty_so_every_existing_caller_is_unchanged() -> None:
    """The parameter is keyword-only with a default: `sql_draft_service` and
    the tests written before A5 call this without it."""
    selected, dropped = fit_to_budget(
        SNAPSHOT,
        named=[SNAPSHOT[1]],
        carried=[],
        by_column=[],
        relationships=RELS,
        budget_chars=10_000,
    )
    assert [t["name"] for t in selected] == [t["name"] for t in SNAPSHOT]
    assert dropped == []


# ── the node ─────────────────────────────────────────────────────────────
#
# The unit tests above cover the index, the matcher and the ranking in
# isolation. These drive `retrieve` itself, on a snapshot too wide to send
# whole, because the whole point of A5 is what happens on the branch that has
# to choose — and because a feature wired into the wrong branch would pass
# every test above.

import asyncio  # noqa: E402
from datetime import timedelta  # noqa: E402
from uuid import uuid4  # noqa: E402

import pytest  # noqa: E402

from app.core.clock import utcnow  # noqa: E402
from app.pipeline import nodes  # noqa: E402
from app.pipeline.nodes import NodeDeps, retrieve  # noqa: E402
from app.pipeline.state import RunState  # noqa: E402


def _wide(n: int = 60) -> dict[str, Any]:
    """A snapshot over the retrieve budget, with the five real tables **last**.

    Every table is the same width, every row count is zero, and the fillers
    come first. That makes snapshot order the only tie-break left, so with
    nothing seeding retrieval the budget is spent on fillers and the five real
    tables are cut — which is what lets this file assert that a table is in the
    block *because a business word chose it*, rather than because it happened
    to be small or early.

    `fit_to_budget` returns its selection in snapshot order, not rank order, so
    rank is not observable from `RetrievedContext`. The budget cut is: a table
    that survives it was ranked above the ones that did not.
    """
    wide_cols = tuple(f"c{j}" for j in range(20))
    tables = [
        *(table(f"filler_{i:02d}", *wide_cols) for i in range(n)),
        *(table(t["name"], *(c["name"] for c in t["columns"]), *wide_cols)
          for t in SNAPSHOT),
    ]
    assert sum(60 + 40 * len(t["columns"]) for t in tables) > nodes._RETRIEVE_BUDGET_CHARS
    return {"tables": tables, "relationships": RELS}


def _run(question: str) -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question=question,
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.intent = "ANALYTICAL"  # type: ignore[assignment]
    return state


def _deps(snapshot: dict[str, Any], semantic: dict[str, Any] | None) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=snapshot, history=[], policy=None,  # type: ignore[arg-type]
        emit=emit, semantic=semantic,
    )


async def _selected(question: str, semantic: dict[str, Any] | None) -> tuple[list[str], str]:
    state = _run(question)
    result = await retrieve(state, _deps(_wide(), semantic))
    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    return [t["name"] for t in state.context.tables], result.detail or ""


@pytest.mark.asyncio
async def test_a_business_word_puts_its_table_in_the_block() -> None:
    """The headline, and the whole of mvp2 A5 in one assertion.

    Nothing in "how bad is churn?" names a table: no table is called that, no
    column is, and retrieval has no seed at all. Without the layer the budget
    goes to the fillers and `subscription_events` is cut. With it, the word a
    curator wrote down once promotes the table it was written about."""
    layer = LAYER.model_dump(mode="json")
    with_layer, detail = await _selected("how bad is churn this quarter?", layer)
    assert "subscription_events" in with_layer
    assert "1 by business term" in detail

    without, bare = await _selected("how bad is churn this quarter?", None)
    assert "subscription_events" not in without
    assert "business term" not in bare


@pytest.mark.asyncio
async def test_no_layer_selects_exactly_what_it_selected_before_a5() -> None:
    """The v10 guarantee, at the node. A connection without a semantic layer
    gets the same table list it always got — asserted against the ranking run
    with an explicitly empty index rather than against a remembered list."""
    question = "revenue by customer last month"
    without, _ = await _selected(question, None)
    empty, _ = await _selected(question, {})
    assert without == empty


@pytest.mark.asyncio
async def test_the_word_reaches_the_prompt_it_selected_the_table_for() -> None:
    """Selecting the table is only half of it: the schema block the generator
    reads has to contain it, or the promotion bought nothing."""
    state = _run("how bad is churn this quarter?")
    await retrieve(state, _deps(_wide(), LAYER.model_dump(mode="json")))
    assert state.context is not None
    assert "subscription_events" in state.context.render(DISCLOSURE)


DISCLOSURE = "SAMPLE"


def test_the_node_helper_is_not_accidentally_async() -> None:
    """`_layer_index` parses a document; it must not become something that has
    to be awaited inside the node's hot path without anyone noticing."""
    assert not asyncio.iscoroutinefunction(_layer_index)
