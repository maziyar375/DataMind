"""What changed between two semantic documents, as typed changes keyed by entry.

One differ, in the backend (D5 of `docs/plans/semantic-layer-model.md`). The API
returns these changes and the frontend groups and words them; it never compares
two documents itself, because a second differ in TypeScript would be two
functions that must agree forever.

**The vocabulary is fixed**, and a change of meaning is a change here first:

    document   context_changed · exclusions_changed★ · time_changed
    entity     entity_added · entity_removed · entity_excluded · entity_included
               entity_described
    column     column_added · column_removed · column_described
               value_meanings_changed
    metric     metric_added★ · metric_removed★ · metric_expression_changed★
               metric_filters_changed★ · metric_joins_changed★ · metric_described
    glossary   glossary_added · glossary_removed · glossary_changed
    review     reviewed_changed
                                                             ★ affects_sql

**Entry keys.** An entity is its lower-cased `table`; a column is its table plus
its lower-cased `name`; a metric is its table plus its lower-cased `name`; a
glossary term is its lower-cased `term`. The plan keys a metric by name alone,
since `_refuse_ambiguous_metrics` makes a name unique among the metrics the
model reads — but a document can still *store* one name on two tables (both
flagged), and a keyed comparison needs a key that is unique in every stored
document, not only in the valid ones.

**Rules.**

* Validator-owned fields (`valid`, `issue`), the derived `joins` and
  `provenance.source`/`edited` never produce a change: none of them is
  something a person did to what the model reads.
* Reordering entities, columns, metrics or glossary terms produces no change;
  neither does reordering a metric's filters or required joins, which are
  conjuncts.
* A renamed metric is `metric_removed` plus `metric_added`. There is no identity
  to follow a rename by, and guessing one would be worse.
* Adding or removing an entity also adds or removes each of its columns and
  metrics, so every entry's own history starts where the entry did.

Pure: two documents in, a list out. It should be given two documents bound to
the same snapshot, or the binder's own rewrites (a resolved table suffix, a
cleared `default_time_column`) show up as changes nobody made.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.semantic.models import (
    GlossaryTerm,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
)

# ── the vocabulary ───────────────────────────────────────────────────────
CONTEXT_CHANGED = "context_changed"
EXCLUSIONS_CHANGED = "exclusions_changed"
TIME_CHANGED = "time_changed"
ENTITY_ADDED = "entity_added"
ENTITY_REMOVED = "entity_removed"
ENTITY_EXCLUDED = "entity_excluded"
ENTITY_INCLUDED = "entity_included"
ENTITY_DESCRIBED = "entity_described"
COLUMN_ADDED = "column_added"
COLUMN_REMOVED = "column_removed"
COLUMN_DESCRIBED = "column_described"
VALUE_MEANINGS_CHANGED = "value_meanings_changed"
METRIC_ADDED = "metric_added"
METRIC_REMOVED = "metric_removed"
METRIC_EXPRESSION_CHANGED = "metric_expression_changed"
METRIC_FILTERS_CHANGED = "metric_filters_changed"
METRIC_JOINS_CHANGED = "metric_joins_changed"
METRIC_DESCRIBED = "metric_described"
GLOSSARY_ADDED = "glossary_added"
GLOSSARY_REMOVED = "glossary_removed"
GLOSSARY_CHANGED = "glossary_changed"
REVIEWED_CHANGED = "reviewed_changed"

#: Every kind, in the order a change list is sorted within one entry.
KINDS: tuple[str, ...] = (
    CONTEXT_CHANGED, EXCLUSIONS_CHANGED, TIME_CHANGED,
    ENTITY_ADDED, ENTITY_REMOVED, ENTITY_EXCLUDED, ENTITY_INCLUDED, ENTITY_DESCRIBED,
    COLUMN_ADDED, COLUMN_REMOVED, COLUMN_DESCRIBED, VALUE_MEANINGS_CHANGED,
    METRIC_ADDED, METRIC_REMOVED, METRIC_EXPRESSION_CHANGED, METRIC_FILTERS_CHANGED,
    METRIC_JOINS_CHANGED, METRIC_DESCRIBED,
    GLOSSARY_ADDED, GLOSSARY_REMOVED, GLOSSARY_CHANGED,
    REVIEWED_CHANGED,
)

#: The kinds that change *numbers* rather than wording. A reader of a dashboard
#: cannot see these in its SQL, which is why they sort first and why a note is
#: asked for when one is saved.
AFFECTS_SQL: frozenset[str] = frozenset({
    EXCLUSIONS_CHANGED,
    METRIC_ADDED, METRIC_REMOVED,
    METRIC_EXPRESSION_CHANGED, METRIC_FILTERS_CHANGED, METRIC_JOINS_CHANGED,
})

_ENTITY_FIELDS = ("label", "description", "grain", "role", "default_time_column", "synonyms")
_COLUMN_FIELDS = ("label", "description", "synonyms", "role", "unit")
_METRIC_FIELDS = ("label", "description", "unit", "format", "synonyms", "additive")
_TIME_FIELDS = (
    "fiscal_year_start_month", "week_starts_on", "timezone", "relative_windows", "notes",
)
_GLOSSARY_FIELDS = ("meaning", "maps_to")


@dataclass(frozen=True, slots=True)
class Change:
    """One entry that changed, and how.

    `entity_key` is `""` for a document-level change and for a glossary term;
    `item_key` is `""` for a change to the entity itself. `fields`, `before`
    and `after` are the detail a sentence is written from — which fields moved
    and their values either side — and are recomputed on request rather than
    stored (`semantic_layer_changes` keeps the keys only).
    """

    kind: str
    entity_key: str = ""
    item_key: str = ""
    fields: tuple[str, ...] = ()
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)

    @property
    def affects_sql(self) -> bool:
        return self.kind in AFFECTS_SQL

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entity_key": self.entity_key,
            "item_key": self.item_key,
            "affects_sql": self.affects_sql,
            "fields": list(self.fields),
            "before": self.before,
            "after": self.after,
        }


def diff_documents(before: SemanticDocument, after: SemanticDocument) -> list[Change]:
    """Every entry that differs between `before` and `after`, in a stable order.

    Document-level changes first, then glossary terms, then entities by key —
    and within an entity, the entity's own changes before its columns' and its
    metrics'. The order is for determinism; how a list is *presented* is the
    frontend's decision (`semantic-changes.ts`).
    """
    changes: list[Change] = []
    changes += _document(before, after)
    changes += _glossary(before.glossary, after.glossary)

    old, new = _by_key(before.entities, _entity_key), _by_key(after.entities, _entity_key)
    for key in sorted(old.keys() | new.keys()):
        changes += _entity(key, old.get(key), new.get(key))
    return changes


# ── document level ───────────────────────────────────────────────────────
def _document(before: SemanticDocument, after: SemanticDocument) -> list[Change]:
    out: list[Change] = []
    if before.business_context != after.business_context:
        out.append(_changed(CONTEXT_CHANGED, ("business_context",), before, after))
    if before.default_exclusions != after.default_exclusions:
        out.append(_changed(EXCLUSIONS_CHANGED, ("default_exclusions",), before, after))
    moved = _moved(before.time, after.time, _TIME_FIELDS)
    if moved:
        out.append(_changed(TIME_CHANGED, moved, before.time, after.time))
    return out


def _glossary(before: list[GlossaryTerm], after: list[GlossaryTerm]) -> list[Change]:
    old, new = _by_key(before, _term_key), _by_key(after, _term_key)
    out: list[Change] = []
    for key in sorted(old.keys() | new.keys()):
        was, now = old.get(key), new.get(key)
        whole = ("term", *_GLOSSARY_FIELDS)
        if was is None:
            out.append(Change(GLOSSARY_ADDED, item_key=key, after=_values(now, whole)))
        elif now is None:
            out.append(Change(GLOSSARY_REMOVED, item_key=key, before=_values(was, whole)))
        else:
            moved = _moved(was, now, _GLOSSARY_FIELDS)
            if moved:
                out.append(_changed(GLOSSARY_CHANGED, moved, was, now, item_key=key))
    return out


# ── entities and what hangs off them ─────────────────────────────────────
def _entity(key: str, was: SemanticEntity | None, now: SemanticEntity | None) -> list[Change]:
    named = ("table", "label")
    if was is None or now is None:
        whole = (
            Change(ENTITY_ADDED, key, after=_values(now, named)) if was is None
            else Change(ENTITY_REMOVED, key, before=_values(was, named))
        )
        return [
            whole,
            *_columns(key, was.columns if was else [], now.columns if now else []),
            *_metrics(key, was.metrics if was else [], now.metrics if now else []),
        ]

    out: list[Change] = []
    if was.exclude != now.exclude:
        out.append(Change(
            ENTITY_EXCLUDED if now.exclude else ENTITY_INCLUDED, entity_key=key,
            before={"exclude": was.exclude}, after={"exclude": now.exclude},
        ))
    moved = _moved(was, now, _ENTITY_FIELDS)
    if moved:
        out.append(_changed(ENTITY_DESCRIBED, moved, was, now, entity_key=key))
    if was.provenance.reviewed != now.provenance.reviewed:
        out.append(_reviewed(was.provenance.reviewed, now.provenance.reviewed, key))
    out += _columns(key, was.columns, now.columns)
    out += _metrics(key, was.metrics, now.metrics)
    return out


def _columns(
    entity: str, before: list[SemanticColumn], after: list[SemanticColumn]
) -> list[Change]:
    old, new = _by_key(before, _name_key), _by_key(after, _name_key)
    out: list[Change] = []
    for key in sorted(old.keys() | new.keys()):
        was, now = old.get(key), new.get(key)
        named = ("name", "label")
        if was is None:
            out.append(Change(COLUMN_ADDED, entity, key, after=_values(now, named)))
            continue
        if now is None:
            out.append(Change(COLUMN_REMOVED, entity, key, before=_values(was, named)))
            continue
        moved = _moved(was, now, _COLUMN_FIELDS)
        if moved:
            out.append(_changed(
                COLUMN_DESCRIBED, moved, was, now, entity_key=entity, item_key=key
            ))
        if was.value_meanings != now.value_meanings:
            out.append(_changed(
                VALUE_MEANINGS_CHANGED, ("value_meanings",), was, now,
                entity_key=entity, item_key=key,
            ))
        if was.provenance.reviewed != now.provenance.reviewed:
            out.append(_reviewed(was.provenance.reviewed, now.provenance.reviewed, entity, key))
    return out


def _metrics(
    entity: str, before: list[SemanticMetric], after: list[SemanticMetric]
) -> list[Change]:
    old, new = _by_key(before, _name_key), _by_key(after, _name_key)
    out: list[Change] = []
    for key in sorted(old.keys() | new.keys()):
        was, now = old.get(key), new.get(key)
        definition = ("name", "label", "expression", "filters", "required_joins")
        if was is None:
            out.append(Change(METRIC_ADDED, entity, key, after=_values(now, definition)))
            continue
        if now is None:
            out.append(Change(METRIC_REMOVED, entity, key, before=_values(was, definition)))
            continue
        if _sql(was.expression) != _sql(now.expression):
            out.append(_changed(
                METRIC_EXPRESSION_CHANGED, ("expression",), was, now,
                entity_key=entity, item_key=key,
            ))
        if _conjuncts(was.filters) != _conjuncts(now.filters):
            out.append(_changed(
                METRIC_FILTERS_CHANGED, ("filters",), was, now,
                entity_key=entity, item_key=key,
            ))
        if _tables(was.required_joins) != _tables(now.required_joins):
            out.append(_changed(
                METRIC_JOINS_CHANGED, ("required_joins",), was, now,
                entity_key=entity, item_key=key,
            ))
        moved = _moved(was, now, _METRIC_FIELDS)
        if moved:
            out.append(_changed(
                METRIC_DESCRIBED, moved, was, now, entity_key=entity, item_key=key
            ))
        if was.provenance.reviewed != now.provenance.reviewed:
            out.append(_reviewed(was.provenance.reviewed, now.provenance.reviewed, entity, key))
    return out


# ── helpers ──────────────────────────────────────────────────────────────
def _entity_key(entity: SemanticEntity) -> str:
    return entity.table.strip().lower()


def _name_key(entry: SemanticColumn | SemanticMetric) -> str:
    return entry.name.strip().lower()


def _term_key(term: GlossaryTerm) -> str:
    return term.term.strip().lower()


def _by_key(items: list[Any], key: Any) -> dict[str, Any]:
    """First occurrence wins. A duplicate entry is already flagged by the
    binder, and a second copy is not a second entry to diff."""
    out: dict[str, Any] = {}
    for item in items:
        out.setdefault(key(item), item)
    return out


def _sql(text: str) -> str:
    """Trailing semicolons and surrounding whitespace are not a new definition."""
    return (text or "").strip().rstrip(";").strip()


def _conjuncts(filters: list[str]) -> list[str]:
    return sorted(s for s in (_sql(f) for f in filters) if s)


def _tables(joins: list[str]) -> list[str]:
    return sorted({j.strip().lower() for j in joins if j.strip()})


def _moved(was: Any, now: Any, fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f for f in fields if _plain(getattr(was, f)) != _plain(getattr(now, f)))


def _plain(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _values(entry: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: _json(getattr(entry, f)) for f in fields}


def _json(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _changed(
    kind: str,
    fields: tuple[str, ...],
    was: Any,
    now: Any,
    *,
    entity_key: str = "",
    item_key: str = "",
) -> Change:
    return Change(
        kind, entity_key, item_key, fields,
        before=_values(was, fields), after=_values(now, fields),
    )


def _reviewed(was: bool, now: bool, entity_key: str, item_key: str = "") -> Change:
    return Change(
        REVIEWED_CHANGED, entity_key, item_key, ("reviewed",),
        before={"reviewed": was}, after={"reviewed": now},
    )
