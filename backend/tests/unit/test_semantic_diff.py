"""The one differ: typed changes keyed by entry.

`docs/plans/semantic-layer-model.md` §4.2 fixes the vocabulary before any code,
and D5 puts the only differ in the backend — the frontend groups and words a
change list and never compares two documents. So this file is the contract the
History tab, the note prompt and the conflict note all rest on:

* every kind is produced by exactly the edit it names;
* what a person did not do — a validator flag, a derived join, a reorder —
  produces nothing;
* `affects_sql` is exactly the six kinds that change a number.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

from app.semantic import (
    AFFECTS_SQL,
    KINDS,
    GlossaryTerm,
    Provenance,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticJoin,
    SemanticMetric,
    diff_documents,
)
from app.semantic import diff as d


def _base() -> SemanticDocument:
    return SemanticDocument(
        business_context="An order book.",
        default_exclusions="test accounts",
        entities=[
            SemanticEntity(
                table="public.orders",
                label="Orders",
                grain="one row per order",
                columns=[
                    SemanticColumn(name="status", value_meanings={"C": "cancelled"}),
                    SemanticColumn(name="amount", label="Order value"),
                ],
                metrics=[
                    SemanticMetric(
                        name="revenue",
                        label="Revenue",
                        expression="SUM(amount)",
                        filters=["status <> 'C'", "amount > 0"],
                        required_joins=["public.customers"],
                    )
                ],
            ),
            SemanticEntity(table="public.customers", label="Customers"),
        ],
        glossary=[GlossaryTerm(term="AOV", meaning="average order value")],
    )


def _orders(doc: SemanticDocument) -> SemanticEntity:
    entity = doc.entity("public.orders")
    assert entity is not None
    return entity


def _kinds(edit: Callable[[SemanticDocument], None]) -> list[tuple[str, str, str]]:
    before = _base()
    after = _base()
    edit(after)
    return [(c.kind, c.entity_key, c.item_key) for c in diff_documents(before, after)]


# ── every kind, from exactly its edit ────────────────────────────────────
def _set(obj: object, field: str, value: object) -> None:
    setattr(obj, field, value)


def _add_metric(doc: SemanticDocument) -> None:
    _orders(doc).metrics.append(SemanticMetric(name="aov", expression="AVG(amount)"))


def _add_column(doc: SemanticDocument) -> None:
    _orders(doc).columns.append(SemanticColumn(name="placed_at"))


EDITS: list[tuple[str, Callable[[SemanticDocument], None], tuple[str, str, str]]] = [
    ("context", lambda x: _set(x, "business_context", "A ledger."),
     (d.CONTEXT_CHANGED, "", "")),
    ("exclusions", lambda x: _set(x, "default_exclusions", "internal orders"),
     (d.EXCLUSIONS_CHANGED, "", "")),
    ("time", lambda x: _set(x.time, "fiscal_year_start_month", 4),
     (d.TIME_CHANGED, "", "")),
    ("exclude", lambda x: _set(_orders(x), "exclude", True),
     (d.ENTITY_EXCLUDED, "public.orders", "")),
    ("grain", lambda x: _set(_orders(x), "grain", "one row per order line"),
     (d.ENTITY_DESCRIBED, "public.orders", "")),
    ("column label", lambda x: _set(_orders(x).columns[1], "label", "Total"),
     (d.COLUMN_DESCRIBED, "public.orders", "amount")),
    ("value meanings",
     lambda x: _orders(x).columns[0].value_meanings.update({"R": "refunded"}),
     (d.VALUE_MEANINGS_CHANGED, "public.orders", "status")),
    ("add column", _add_column, (d.COLUMN_ADDED, "public.orders", "placed_at")),
    ("remove column", lambda x: _orders(x).columns.pop(1),
     (d.COLUMN_REMOVED, "public.orders", "amount")),
    ("add metric", _add_metric, (d.METRIC_ADDED, "public.orders", "aov")),
    ("remove metric", lambda x: _orders(x).metrics.clear(),
     (d.METRIC_REMOVED, "public.orders", "revenue")),
    ("expression", lambda x: _set(_orders(x).metrics[0], "expression", "SUM(amount) - 1"),
     (d.METRIC_EXPRESSION_CHANGED, "public.orders", "revenue")),
    ("filters", lambda x: _orders(x).metrics[0].filters.append("status <> 'R'"),
     (d.METRIC_FILTERS_CHANGED, "public.orders", "revenue")),
    ("joins", lambda x: _set(_orders(x).metrics[0], "required_joins", []),
     (d.METRIC_JOINS_CHANGED, "public.orders", "revenue")),
    ("metric unit", lambda x: _set(_orders(x).metrics[0], "unit", "USD"),
     (d.METRIC_DESCRIBED, "public.orders", "revenue")),
    ("glossary add", lambda x: x.glossary.append(GlossaryTerm(term="GMV", meaning="gross")),
     (d.GLOSSARY_ADDED, "", "gmv")),
    ("glossary remove", lambda x: x.glossary.clear(), (d.GLOSSARY_REMOVED, "", "aov")),
    ("glossary meaning", lambda x: _set(x.glossary[0], "meaning", "mean basket"),
     (d.GLOSSARY_CHANGED, "", "aov")),
    ("reviewed", lambda x: _set(_orders(x).provenance, "reviewed", True),
     (d.REVIEWED_CHANGED, "public.orders", "")),
]


@pytest.mark.parametrize(
    ("edit", "expected"), [(e, x) for _, e, x in EDITS], ids=[n for n, _, _ in EDITS]
)
def test_each_edit_produces_exactly_its_kind(
    edit: Callable[[SemanticDocument], None], expected: tuple[str, str, str]
) -> None:
    assert _kinds(edit) == [expected]


def test_including_an_excluded_entity_is_its_own_kind() -> None:
    before = _base()
    _orders(before).exclude = True
    after = _base()
    assert [c.kind for c in diff_documents(before, after)] == [d.ENTITY_INCLUDED]


def test_the_vocabulary_is_covered_by_this_file() -> None:
    # A kind added to the differ without a case above fails here, which is
    # where somebody adding one will look.
    covered = {x[0] for _, _, x in EDITS} | {d.ENTITY_INCLUDED, d.ENTITY_ADDED, d.ENTITY_REMOVED}
    assert covered == set(KINDS)


# ── what a person did not do ─────────────────────────────────────────────
def test_validator_owned_fields_produce_no_change() -> None:
    def flag(doc: SemanticDocument) -> None:
        orders = _orders(doc)
        orders.valid, orders.issue = False, "gone"
        orders.columns[0].valid, orders.columns[0].issue = False, "dropped"
        orders.metrics[0].valid, orders.metrics[0].issue = False, "bad"

    assert _kinds(flag) == []


def test_derived_joins_and_provenance_bookkeeping_produce_no_change() -> None:
    def derive(doc: SemanticDocument) -> None:
        doc.joins = [SemanticJoin(left="public.orders", right="public.customers", on="x")]
        _orders(doc).provenance = Provenance(source="human", edited=True)

    assert _kinds(derive) == []


def test_reordering_produces_no_change() -> None:
    def shuffle(doc: SemanticDocument) -> None:
        doc.entities.reverse()
        _orders(doc).columns.reverse()
        _orders(doc).metrics[0].filters.reverse()

    assert _kinds(shuffle) == []


def test_case_and_whitespace_in_a_key_do_not_make_a_new_entry() -> None:
    def recase(doc: SemanticDocument) -> None:
        _orders(doc).table = "PUBLIC.Orders"
        _orders(doc).metrics[0].expression = "SUM(amount);"

    assert _kinds(recase) == []


def test_a_renamed_metric_is_a_removal_and_an_addition() -> None:
    kinds = _kinds(lambda x: _set(_orders(x).metrics[0], "name", "net_revenue"))
    assert kinds == [
        (d.METRIC_ADDED, "public.orders", "net_revenue"),
        (d.METRIC_REMOVED, "public.orders", "revenue"),
    ]


def test_adding_an_entity_adds_what_hangs_off_it() -> None:
    def add(doc: SemanticDocument) -> None:
        doc.entities.append(SemanticEntity(
            table="public.items",
            columns=[SemanticColumn(name="qty")],
            metrics=[SemanticMetric(name="units", expression="SUM(qty)")],
        ))

    assert _kinds(add) == [
        (d.ENTITY_ADDED, "public.items", ""),
        (d.COLUMN_ADDED, "public.items", "qty"),
        (d.METRIC_ADDED, "public.items", "units"),
    ]


def test_the_same_metric_name_on_two_tables_is_two_entries() -> None:
    # Stored documents can hold this (the binder flags both), so the key has to
    # tell them apart or one edit would read as the other.
    def add(doc: SemanticDocument) -> None:
        customers = doc.entity("public.customers")
        assert customers is not None
        customers.metrics.append(SemanticMetric(name="revenue", expression="COUNT(*)"))

    assert _kinds(add) == [(d.METRIC_ADDED, "public.customers", "revenue")]


# ── affects_sql, and the detail a sentence is written from ───────────────
def test_affects_sql_is_exactly_the_kinds_that_change_numbers() -> None:
    assert {
        d.EXCLUSIONS_CHANGED, d.METRIC_ADDED, d.METRIC_REMOVED,
        d.METRIC_EXPRESSION_CHANGED, d.METRIC_FILTERS_CHANGED, d.METRIC_JOINS_CHANGED,
    } == AFFECTS_SQL
    for _, edit, (kind, _, _) in EDITS:
        before, after = _base(), _base()
        edit(after)
        [change] = diff_documents(before, after)
        assert change.affects_sql is (kind in AFFECTS_SQL), kind


def test_a_change_carries_the_fields_that_moved_and_their_values() -> None:
    before, after = _base(), _base()
    _orders(after).grain = "one row per order line"
    _orders(after).label = "Sales orders"
    [change] = diff_documents(before, after)
    assert change.as_dict() == {
        "kind": d.ENTITY_DESCRIBED,
        "entity_key": "public.orders",
        "item_key": "",
        "affects_sql": False,
        "fields": ["label", "grain"],
        "before": {"label": "Orders", "grain": "one row per order"},
        "after": {"label": "Sales orders", "grain": "one row per order line"},
    }


def test_a_filter_change_carries_both_lists() -> None:
    before, after = _base(), _base()
    _orders(after).metrics[0].filters.append("status <> 'R'")
    [change] = diff_documents(before, after)
    assert change.before == {"filters": ["status <> 'C'", "amount > 0"]}
    assert change.after == {"filters": ["status <> 'C'", "amount > 0", "status <> 'R'"]}


def test_identical_documents_have_no_changes() -> None:
    assert diff_documents(_base(), _base()) == []
    assert diff_documents(SemanticDocument(), SemanticDocument()) == []
