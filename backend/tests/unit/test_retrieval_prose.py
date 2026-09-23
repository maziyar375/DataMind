"""The schema's own sentences reach retrieval — mvp2 B2, Phase 1.

`docs/plans/hybrid-retrieval.md`. Every signal `RANKED_MATCH` had before this
is a **word the question repeats**: a table's name, a column's name, a name the
conversation already queried, a label or synonym a curator wrote (A5). A
question phrased in none of them selects nothing and falls to *"take the
biggest tables"*.

The sentences that would answer it are in the same snapshot — the DBA's
`COMMENT ON`, the curator's `description` and `grain` — and nothing on the
retrieval path has ever read them. This file holds the three properties that
matter:

* a question whose words appear **only in a table's prose** reaches that table;
* **with no comments and no layer, nothing changes at all** — every score is
  0.0, the new tier is empty, and the ranking is the one v11 performed;
* `include_db_comments` governs **ranking as well as rendering** (plan §0.2
  D4), so a connection that refuses to send its comments does not have them
  choose what is sent either.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.pipeline import nodes
from app.pipeline.nodes import NodeDeps, _layer_index, fit_to_budget, retrieve
from app.pipeline.relevance import (
    PROSE_FLOOR,
    PROSE_MAX_SEEDS,
    prose_seeds,
    rank_tables,
    table_text,
)
from app.pipeline.state import RunState
from app.semantic import (
    GlossaryTerm,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    table_prose,
    table_terms,
)


def table(
    name: str,
    *columns: str,
    schema: str = "public",
    rows: int = 0,
    comment: str = "",
    column_comments: dict[str, str] | None = None,
) -> dict[str, Any]:
    notes = column_comments or {}
    return {
        "schema": schema,
        "name": name,
        "approx_row_count": rows,
        "comment": comment,
        "columns": [
            {"name": c, "data_type": "text", "comment": notes.get(c, "")}
            for c in columns
        ],
    }


REFUNDS = "one row per line of an order; negative quantities are refunds"

SNAPSHOT = [
    table("orders", "id", "customer_id", "total", "status",
          comment="one header per checkout"),
    table("order_items", "id", "order_id", "product_id", "qty", "price",
          comment=REFUNDS),
    table("customers", "id", "name", "created_at",
          comment="one header per person who bought",
          column_comments={"created_at": "when they first signed up"}),
    table("products", "id", "name", "category"),
    table("shipments", "id", "order_id", "carrier"),
]

def rel(src: str, col: str, dst: str) -> dict[str, str]:
    return {
        "from_table": f"public.{src}", "from_column": col,
        "to_table": f"public.{dst}", "to_column": "id",
    }


RELS = [
    rel("order_items", "order_id", "orders"),
    rel("order_items", "product_id", "products"),
    rel("orders", "customer_id", "customers"),
    rel("shipments", "order_id", "orders"),
]


def by_name(name: str) -> dict[str, Any]:
    return next(t for t in SNAPSHOT if t["name"] == name)


# ── the bag ──────────────────────────────────────────────────────────────


def test_a_tables_comment_and_its_columns_comments_are_one_bag() -> None:
    bag = table_text(by_name("customers"))
    assert "one header per person who bought" in bag
    assert "when they first signed up" in bag


def test_a_table_with_nothing_written_about_it_has_an_empty_bag() -> None:
    """A name is not prose. `match_tables` owns how the database spells
    things, and repeating it here would score a physical hit twice."""
    assert table_text(by_name("products")) == ()


def test_comments_off_means_comments_unread() -> None:
    """Plan §0.2 D4. The flag exists so a connection can refuse to send its DDL
    comments to a provider; a comment that decides *which* tables are sent has
    reached the provider's answer by another road."""
    assert table_text(by_name("order_items"), include_comments=False) == ()


def test_the_layers_sentences_join_the_same_bag() -> None:
    """A curator's description and a DBA's comment are the same kind of
    evidence, and nothing downstream needs to tell them apart."""
    layer = {"public.products": ("what we sell, one row per SKU",)}
    assert table_text(by_name("products"), layer=layer) == (
        "what we sell, one row per SKU",
    )


def test_the_layer_is_still_read_when_the_comments_are_not() -> None:
    """The flag is about the *DBA's* comments. The layer is the customer's own
    document and `include_db_comments` says nothing about it."""
    layer = {"public.order_items": ("lines of an order",)}
    assert table_text(by_name("order_items"), layer=layer, include_comments=False) == (
        "lines of an order",
    )


# ── the layer's half of the bag ──────────────────────────────────────────


def entity(tbl: str, **kw: Any) -> SemanticEntity:
    return SemanticEntity(table=f"public.{tbl}", **kw)


LAYER = SemanticDocument(
    business_context="a direct-to-consumer retailer",
    entities=[
        entity(
            "subscription_events",
            label="Subscription events",
            synonyms=["churn"],
            description="every change to a subscription's state",
            grain="one row per state change",
            columns=[
                SemanticColumn(
                    name="event_type",
                    label="Event kind",
                    description="what happened to the subscription",
                    value_meanings={"C": "the customer cancelled"},
                )
            ],
            metrics=[
                SemanticMetric(
                    name="churn_rate",
                    label="Churn rate",
                    description="cancellations over the opening count",
                    expression="1.0",
                )
            ],
        ),
        entity("customers", label="Customers", description="people who bought"),
    ],
    glossary=[
        GlossaryTerm(
            term="voluntary churn",
            meaning="a cancellation the customer chose, not a failed payment",
            maps_to=["churn_rate"],
        )
    ],
)


def test_descriptions_and_grains_are_prose() -> None:
    prose = table_prose(LAYER)["public.subscription_events"]
    assert "every change to a subscription's state" in prose
    assert "one row per state change" in prose


def test_a_columns_description_and_its_value_meanings_are_prose() -> None:
    """The *meanings*, never the codes. `C` is a stored value, and matching a
    question against it is matching it against data."""
    prose = table_prose(LAYER)["public.subscription_events"]
    assert "what happened to the subscription" in prose
    assert "the customer cancelled" in prose
    assert "C" not in prose


def test_a_metrics_description_is_prose_but_its_name_is_not() -> None:
    """`table_terms` owns the name; nothing is read by both."""
    prose = table_prose(LAYER)["public.subscription_events"]
    assert "cancellations over the opening count" in prose
    assert "churn_rate" not in prose
    assert "churn_rate" in table_terms(LAYER)["public.subscription_events"]


def test_a_glossary_meaning_lands_on_the_table_its_metric_is_measured_over() -> None:
    """`vocabulary_terms` drops these meanings because it is building
    vocabulary. This is built from them: a sentence explaining what churn is,
    is exactly what a question about churn should find."""
    prose = table_prose(LAYER)["public.subscription_events"]
    assert "a cancellation the customer chose, not a failed payment" in prose


def test_labels_and_synonyms_are_absent_because_a_tier_already_reads_them() -> None:
    prose = table_prose(LAYER)["public.subscription_events"]
    assert "Subscription events" not in prose
    assert "churn" not in prose


def test_business_context_is_absent_because_it_says_nothing_about_which_table() -> None:
    assert all(
        "a direct-to-consumer retailer" not in sentences
        for sentences in table_prose(LAYER).values()
    )


def test_an_excluded_or_invalid_entry_contributes_nothing() -> None:
    """A sentence the renderer keeps out of the prompt must not choose the
    table either."""
    doc = SemanticDocument(
        entities=[
            entity("orders", description="gone", exclude=True),
            entity("shipments", description="also gone", valid=False),
            entity(
                "products",
                description="kept",
                columns=[
                    SemanticColumn(name="x", description="dropped", valid=False)
                ],
                metrics=[
                    SemanticMetric(name="m", description="dropped", valid=False)
                ],
            ),
        ]
    )
    assert table_prose(doc) == {"public.products": ("kept",)}


def test_a_table_the_layer_says_nothing_about_is_absent_rather_than_empty() -> None:
    doc = SemanticDocument(entities=[entity("orders", label="Orders")])
    assert table_prose(doc) == {}


def test_both_registers_come_out_of_one_parse() -> None:
    """`_layer_index` reads the document once: the names steer
    `match_by_terms`, the sentences feed `rank_tables`."""
    index = _layer_index(LAYER.model_dump(mode="json"))
    assert "churn" in index.terms["public.subscription_events"]
    assert "one row per state change" in index.prose["public.subscription_events"]


def test_a_malformed_layer_degrades_rather_than_raising() -> None:
    assert _layer_index({"entities": "not a list"}).prose == {}
    assert _layer_index(None).prose == {}


# ── the score ────────────────────────────────────────────────────────────


def test_a_question_answered_only_by_a_comment_scores_that_table() -> None:
    scores = rank_tables("which tables hold refunds?", SNAPSHOT)
    assert scores["public.order_items"] == pytest.approx(1.0)
    assert "public.products" not in scores


def test_a_rare_word_outweighs_one_every_table_shares() -> None:
    """No stopword list can be right per language *and* per customer. `df` is
    counted over this connection's own prose, so the corpus says which words
    discriminate."""
    tables = [
        table("t0", "id", comment="a header row, and the refunds against it"),
        *(table(f"t{i}", "id", comment="a header row") for i in range(1, 12)),
    ]
    scores = rank_tables("header refunds", tables)
    assert scores["public.t0"] > PROSE_FLOOR
    assert scores["public.t1"] < PROSE_FLOOR
    assert scores["public.t0"] > scores["public.t1"] * 5


def test_a_word_nothing_wrote_about_does_not_sink_every_score() -> None:
    """The denominator counts only question tokens this corpus has ever seen.
    Otherwise a question full of "how many" and "last month" is normalised by a
    weight no table could carry, and every score collapses toward zero for a
    reason that says nothing about any table."""
    asked = rank_tables("how many refunds did we issue in Patagonia?", SNAPSHOT)
    assert asked["public.order_items"] == pytest.approx(1.0)


def test_a_word_every_table_shares_promotes_all_of_them_which_is_promoting_none() -> None:
    """The honest limit of a per-question normalisation: when the only word the
    corpus knows is universal, every table carries the whole of the question's
    weight and every table ties. They land in one tier, the tiebreak behind
    them is untouched, and nothing has been reordered."""
    tables = [table(f"t{i}", "id", comment="a header row") for i in range(6)]
    scores = rank_tables("header", tables)
    assert len(scores) == len(tables)
    assert all(v == pytest.approx(1.0) for v in scores.values())


def test_no_prose_is_no_score_and_no_work() -> None:
    bare = [table("orders", "id"), table("customers", "id")]
    assert rank_tables("anything at all", bare) == {}
    assert rank_tables("", SNAPSHOT) == {}
    assert rank_tables("refunds", []) == {}


def test_a_two_letter_word_is_not_scored() -> None:
    """The floor `semantic.terms` and `metadata._names_for` both apply."""
    tables = [table("t0", "id", comment="up to id")]
    assert rank_tables("id up", tables) == {}


def test_every_score_is_a_share_in_the_unit_interval() -> None:
    scores = rank_tables("refunds for a person who bought a header", SNAPSHOT)
    assert scores
    assert all(0.0 < v <= 1.0 for v in scores.values())


def test_comments_off_scores_the_layer_alone() -> None:
    layer = {"public.products": ("everything we sell, including refurbished",)}
    scores = rank_tables(
        "refunds and refurbished stock", SNAPSHOT, layer=layer, include_comments=False
    )
    assert "public.order_items" not in scores
    assert scores["public.products"] == pytest.approx(1.0)


# ── the seeds ────────────────────────────────────────────────────────────


def test_a_table_under_the_floor_does_not_seed() -> None:
    scores = {"public.orders": PROSE_FLOOR - 0.01, "public.order_items": PROSE_FLOOR}
    assert [t["name"] for t in prose_seeds(SNAPSHOT, scores)] == ["order_items"]


def test_seeds_come_back_in_snapshot_order() -> None:
    scores = {"public.shipments": 0.9, "public.orders": 0.5}
    assert [t["name"] for t in prose_seeds(SNAPSHOT, scores)] == ["orders", "shipments"]


def test_the_seed_list_is_capped_at_the_best_of_them() -> None:
    """Prose is the weakest of the five signals and ranks below every other
    one, so a table past the cap would have to survive a cut that every named,
    carried, column-named and bridge table precedes."""
    tables = [table(f"t{i:02d}", "id") for i in range(PROSE_MAX_SEEDS + 10)]
    scores = {f"public.t{i:02d}": 0.5 + i / 1000 for i in range(len(tables))}
    seeds = prose_seeds(tables, scores)
    assert len(seeds) == PROSE_MAX_SEEDS
    # The *best* of them, and still in snapshot order.
    assert seeds[0]["name"] == "t10"
    assert seeds[-1]["name"] == f"t{PROSE_MAX_SEEDS + 9:02d}"


# ── the tier ─────────────────────────────────────────────────────────────


def _fit(budget: int, **kw: Any) -> list[str]:
    selected, _ = fit_to_budget(
        SNAPSHOT,
        named=kw.get("named", []),
        carried=kw.get("carried", []),
        by_column=kw.get("by_column", []),
        by_term=kw.get("by_term", []),
        scores=kw.get("scores"),
        relationships=RELS,
        budget_chars=budget,
    )
    return [t["name"] for t in selected]


def test_prose_ranks_below_a_bridge_because_a_bridge_is_structural() -> None:
    """Dropping a bridge does not make the answer worse, it makes the query
    impossible. `customers` and `shipments` both join the named `orders` and
    tie, so snapshot order settles it; `products` is described in the question's
    own words and still comes after both."""
    two = (60 + 40 * 4) + (60 + 40 * 3)
    kept = _fit(two, named=[by_name("orders")], scores={"public.products": 1.0})
    assert kept == ["orders", "customers"]
    assert "products" not in kept


def test_prose_ranks_above_a_table_with_nothing_to_say() -> None:
    """"This table is described in the words you used" beats "this table is
    big"."""
    big = dict(by_name("customers"), approx_row_count=10**9)
    selected, _ = fit_to_budget(
        [big, by_name("products")],
        named=[], carried=[], by_column=[],
        scores={"public.products": 1.0},
        relationships=[],
        budget_chars=60 + 40 * 3,
    )
    assert [t["name"] for t in selected] == ["products"]


def test_the_score_breaks_a_tie_before_size_does() -> None:
    """Size demoted to what it always should have been: the last resort, for
    when nothing written down has anything to say."""
    big = dict(by_name("products"), approx_row_count=10**9)
    small = dict(by_name("shipments"), approx_row_count=1)
    selected, _ = fit_to_budget(
        [big, small],
        named=[], carried=[], by_column=[],
        scores={"public.shipments": 0.05},
        relationships=[],
        budget_chars=60 + 40 * 3,
    )
    # Both are under the floor, so neither is promoted — but the tiebreak
    # inside their shared tier is the score, and only then the row count.
    assert [t["name"] for t in selected] == ["shipments"]


def test_scores_default_to_none_so_every_existing_caller_is_unchanged() -> None:
    """Keyword-only with a default: `sql_draft_service` and every test written
    before B2 call this without it."""
    selected, dropped = fit_to_budget(
        SNAPSHOT,
        named=[by_name("orders")], carried=[], by_column=[],
        relationships=RELS, budget_chars=10_000,
    )
    assert [t["name"] for t in selected] == [t["name"] for t in SNAPSHOT]
    assert dropped == []


def test_with_no_prose_the_ranking_is_the_one_v11_performed() -> None:
    """The off-by-absence property, at the function. An empty score map and no
    score map at all must rank identically, or the constant this inserted into
    the sort key is not constant."""
    for budget in (60 + 40 * 4, 2 * (60 + 40 * 5), 10_000):
        for kw in (
            {"named": [by_name("orders")]},
            {"carried": [by_name("customers")], "by_column": [by_name("products")]},
            {},
        ):
            assert _fit(budget, **kw) == _fit(budget, scores={}, **kw)


# ── the node ─────────────────────────────────────────────────────────────
#
# The tests above cover the bag, the score and the ranking in isolation. These
# drive `retrieve` itself on a snapshot too wide to send whole, because the
# whole point of B2 is what happens on the branch that has to choose — and a
# feature wired into the wrong branch would pass every test above.


def _wide(n: int = 60) -> dict[str, Any]:
    """A snapshot over the retrieve budget, with the five real tables **last**.

    Every table is the same width, every row count is zero, and the fillers
    come first, so snapshot order is the only tiebreak left: with nothing
    seeding retrieval the budget is spent on fillers and the real tables are
    cut. That is what lets this file assert a table is in the block *because a
    sentence chose it*.

    `fit_to_budget` returns its selection in snapshot order rather than rank
    order, so rank is not observable from `RetrievedContext`. The budget cut is:
    a table that survived it outranked the ones that did not.
    """
    wide_cols = tuple(f"c{j}" for j in range(20))
    tables = [
        *(table(f"filler_{i:02d}", *wide_cols) for i in range(n)),
        *(
            table(
                t["name"],
                *(c["name"] for c in t["columns"]),
                *wide_cols,
                comment=t["comment"],
                column_comments={
                    c["name"]: c["comment"] for c in t["columns"] if c["comment"]
                },
            )
            for t in SNAPSHOT
        ),
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


def _deps(
    snapshot: dict[str, Any],
    *,
    semantic: dict[str, Any] | None = None,
    comments: bool = True,
) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=None, llm=None, connector=None,  # type: ignore[arg-type]
        snapshot=snapshot, history=[], policy=None,  # type: ignore[arg-type]
        emit=emit, semantic=semantic, include_db_comments=comments,
    )


async def _selected(question: str, **kw: Any) -> tuple[list[str], str]:
    state = _run(question)
    result = await retrieve(state, _deps(_wide(), **kw))
    assert state.context is not None
    assert state.context.strategy == "RANKED_MATCH"
    return [t["name"] for t in state.context.tables], result.detail or ""


@pytest.mark.asyncio
async def test_a_comment_puts_its_table_in_the_block() -> None:
    """The headline, and the whole of B2's first phase in one assertion.

    Nothing in "which tables hold refunds?" names a table: no table is called
    that, no column is, and there is no semantic layer. Without the comments
    the budget goes to the fillers and `order_items` is cut. With them, the
    sentence a DBA wrote once promotes the table it was written about."""
    with_comments, detail = await _selected("which tables hold refunds?")
    assert "order_items" in with_comments
    assert "1 by description" in detail

    without, bare = await _selected("which tables hold refunds?", comments=False)
    assert "order_items" not in without
    assert "by description" not in bare


@pytest.mark.asyncio
async def test_comments_off_selects_exactly_what_it_selected_before_b2() -> None:
    """The v11 guarantee, at the node: a connection with no comments read and
    no layer gets the table list it always got — asserted against the ranking
    run rather than against a remembered list."""
    question = "revenue by customer last month"
    off, _ = await _selected(question, comments=False)
    empty, _ = await _selected(question, comments=False, semantic={})
    assert off == empty


@pytest.mark.asyncio
async def test_a_layers_description_chooses_a_table_the_comments_do_not() -> None:
    layer = SemanticDocument(
        entities=[
            entity("products", description="what we sell, refurbished stock included")
        ]
    ).model_dump(mode="json")
    chosen, detail = await _selected(
        "how much refurbished stock is there?", semantic=layer, comments=False
    )
    assert "products" in chosen
    assert "1 by description" in detail


@pytest.mark.asyncio
async def test_the_trail_counts_what_prose_added_not_what_it_repeated() -> None:
    """A table the question already named does not need a description to be
    chosen, and a trail that counted it twice would say the layer is doing more
    than it is."""
    chosen, detail = await _selected("how many order_items were refunds?")
    assert "order_items" in chosen
    assert "by description" not in detail


@pytest.mark.asyncio
async def test_the_sentence_reaches_the_prompt_it_selected_the_table_for() -> None:
    """Selecting the table is half of it: the schema block the generator reads
    has to contain it, or the promotion bought nothing."""
    state = _run("which tables hold refunds?")
    await retrieve(state, _deps(_wide()))
    assert state.context is not None
    assert "order_items" in state.context.render("SAMPLE")


def test_the_scorer_is_not_accidentally_async() -> None:
    """It runs inside the node's hot path; it must not become something that
    has to be awaited without anyone noticing."""
    assert not asyncio.iscoroutinefunction(rank_tables)
    assert not asyncio.iscoroutinefunction(prose_seeds)
