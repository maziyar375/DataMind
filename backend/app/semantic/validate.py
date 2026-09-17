"""Bind a semantic document to a schema snapshot.

Everything in a semantic layer is a claim about the physical schema, and the
two drift: a table gets renamed, a column is dropped, a metric is typed into a
form with a fat finger. An unbound document is dangerous in a way an unbound
schema block is not — the guard catches SQL that names a column that does not
exist, but nothing catches a *definition* that quietly stops meaning anything.

So: every table, column and expression is resolved against the snapshot, and
what does not resolve is flagged (`valid=False` + a human-readable `issue`)
rather than dropped. Dropping would hide the drift; flagging puts it in the UI
where someone can fix it, and keeps it out of the prompt in the meantime.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from app.semantic.models import (
    Cardinality,
    Provenance,
    SemanticDocument,
    SemanticEntity,
    SemanticJoin,
    SemanticMetric,
)

# Aggregate nodes that make an expression a *measure* rather than a column
# reference with extra steps.
_AGGREGATES = (
    exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max,
    exp.ApproxDistinct, exp.Stddev, exp.Variance,
)


@dataclass(frozen=True, slots=True)
class TableFacts:
    qualified: str
    columns: dict[str, str]                 # lowercase name -> data type
    primary_key: frozenset[str] = frozenset()
    unique: frozenset[str] = frozenset()    # single-column unique constraints


@dataclass(slots=True)
class SchemaIndex:
    """Case-insensitive lookup over one snapshot. Built once per validation."""

    dialect: str = "postgres"
    tables: dict[str, TableFacts] = field(default_factory=dict)

    def table(self, qualified: str) -> TableFacts | None:
        return self.tables.get(qualified.lower())

    def resolve_suffix(self, name: str) -> str | None:
        """Match a bare table name to a qualified one, if unambiguous.

        A model asked for `public.orders` will sometimes answer `orders`. That
        is a naming slip, not a hallucination, and refusing it would throw away
        a correct description — but only when exactly one table matches.
        """
        needle = name.lower().split(".")[-1]
        hits = [q for q in self.tables if q.split(".")[-1] == needle]
        return hits[0] if len(hits) == 1 else None


def build_index(
    tables: list[dict[str, Any]], dialect: str = "postgres"
) -> SchemaIndex:
    index = SchemaIndex(dialect=dialect)
    for table in tables:
        qualified = f"{table.get('schema', '')}.{table.get('name', '')}".lower()
        columns = {
            str(c["name"]).lower(): str(c.get("data_type", ""))
            for c in table.get("columns", [])
            if c.get("name")
        }
        pk = frozenset(
            str(c["name"]).lower()
            for c in table.get("columns", [])
            if c.get("is_primary_key")
        )
        index.tables[qualified] = TableFacts(
            qualified=qualified, columns=columns, primary_key=pk, unique=pk
        )
    return index


# ── expressions ──────────────────────────────────────────────────────────
def check_expression(
    expression: str,
    *,
    entity_table: str,
    index: SchemaIndex,
    extra_tables: list[str] | None = None,
    boolean: bool = False,
) -> tuple[bool, str]:
    """Parse one metric expression or filter and resolve its column refs.

    Returns `(valid, issue)`; `issue` may be non-empty on a valid expression
    when something is worth saying but not worth suppressing (an expression
    that aggregates nothing still describes a column, it just will not roll
    up, and telling the user beats deleting their work).
    """
    text = (expression or "").strip().rstrip(";")
    if not text:
        return False, "Empty expression."

    scope = [entity_table.lower(), *[t.lower() for t in (extra_tables or [])]]
    known = index.table(entity_table)
    if known is None:
        return False, f"Unknown table {entity_table}."

    # Built to be *parsed*, never executed: there is no connection in this
    # module and no caller passes the result anywhere near one. Interpolation
    # is the whole point — the text under test is the untrusted part.
    probe = (
        f"SELECT 1 FROM {entity_table} WHERE {text}"  # noqa: S608
        if boolean
        else f"SELECT {text} AS m FROM {entity_table}"  # noqa: S608
    )
    try:
        tree = sqlglot.parse_one(probe, read=index.dialect)
    except Exception:
        return False, "This is not valid SQL."
    if tree is None:
        return False, "This is not valid SQL."
    # `1; DROP TABLE orders` as a filter parses — as a *block* of two
    # statements, whose columns all resolve. Nothing executes a metric, but a
    # chained statement is not an expression, and storing it as a valid one
    # would put it in every prompt. Found replaying the hostile SQL corpus
    # through this function (`test_semantic_transfer.py`, Phase 4).
    if not isinstance(tree, exp.Select):
        return False, "One expression, not a statement — remove the `;` and what follows it."

    # Aliases the expression itself introduces (a subquery, a lateral) are not
    # ours to resolve; only bare and table-qualified references are checked.
    for column in tree.find_all(exp.Column):
        name = (column.name or "").lower()
        qualifier = (column.table or "").lower()
        if not name:
            continue
        if qualifier:
            candidates = [
                t for t in scope
                if t == qualifier or t.split(".")[-1] == qualifier
            ]
            if not candidates:
                return False, f"`{qualifier}` is not this entity or a joined table."
            facts = index.table(candidates[0])
            if facts and name not in facts.columns:
                return False, f"`{qualifier}.{name}` is not a column of that table."
            continue
        if not any(
            (facts := index.table(t)) is not None and name in facts.columns
            for t in scope
        ):
            return False, f"`{name}` is not a column of {entity_table}."

    if boolean:
        return True, ""

    if not any(tree.find(node) for node in _AGGREGATES):
        return True, "No aggregate function — this will not roll up over rows."
    return True, ""


# ── documents ────────────────────────────────────────────────────────────
def validate_document(
    doc: SemanticDocument, index: SchemaIndex
) -> SemanticDocument:
    """Return a copy with every entry resolved against the snapshot.

    Pure: the input is not mutated, so a caller can diff before and after to
    show a user exactly what the last schema sync broke.
    """
    checked = doc.model_copy(deep=True)

    for entity in checked.entities:
        facts = index.table(entity.table)
        if facts is None:
            # One rescue attempt for an unqualified name, then it is drift.
            resolved = index.resolve_suffix(entity.table)
            if resolved is None:
                entity.valid = False
                entity.issue = (
                    f"`{entity.table}` is not in the current schema snapshot. "
                    "It may have been renamed or dropped."
                )
                continue
            entity.table = resolved
            facts = index.table(resolved)
        assert facts is not None

        entity.valid, entity.issue = True, ""

        if entity.default_time_column and (
            entity.default_time_column.lower() not in facts.columns
        ):
            entity.issue = (
                f"`{entity.default_time_column}` is not a column of this table."
            )
            entity.default_time_column = ""

        seen: set[str] = set()
        for column in entity.columns:
            key = column.name.lower()
            if key not in facts.columns:
                column.valid = False
                column.issue = "This column is not in the current schema snapshot."
            elif key in seen:
                column.valid = False
                column.issue = "Duplicate entry for this column."
            else:
                column.valid, column.issue = True, ""
                seen.add(key)

        for metric in entity.metrics:
            metric.valid, metric.issue = check_expression(
                metric.expression,
                entity_table=entity.table,
                index=index,
                extra_tables=metric.required_joins,
            )
            if not metric.valid:
                continue
            for predicate in metric.filters:
                ok, issue = check_expression(
                    predicate,
                    entity_table=entity.table,
                    index=index,
                    extra_tables=metric.required_joins,
                    boolean=True,
                )
                if not ok:
                    metric.valid = False
                    metric.issue = f"Filter `{predicate}`: {issue}"
                    break

    _refuse_ambiguous_metrics(checked)

    # A join to a table that is gone cannot be repaired by a human, so unlike
    # the rest of the document it is simply dropped.
    checked.joins = [
        j for j in checked.joins
        if index.table(j.left) is not None and index.table(j.right) is not None
    ]
    return checked


def _refuse_ambiguous_metrics(doc: SemanticDocument) -> None:
    """A metric name means one thing in a database, or it means nothing.

    Everything else in this file checks a definition against the *schema*. This
    checks the document against itself, and it is the one rule the per-table
    shape of the layer cannot express: `revenue` on `orders` and `revenue` on
    `invoices` are each perfectly valid, each render into the same prompt, and
    the model then picks one of them — silently, and not always the same one.
    That is the exact failure a metric layer exists to prevent, so a name that
    is claimed twice answers nothing until a human decides which is which.

    The same posture the knowledge store takes with two templates that
    disagree: both stop being used and both say why, because choosing for the
    curator is how a store becomes untrustworthy rather than merely incomplete.

    Two things it deliberately does not do. An **excluded** entity is not in
    the prompt at all, so a name it also uses is not a collision — it is a
    table somebody set aside. And a metric already invalid for a schema reason
    keeps that reason: it is out of the prompt either way, its problem is the
    more immediate one, and the collision is reported the moment it is fixed.
    """
    homes: dict[str, list[tuple[SemanticEntity, SemanticMetric]]] = {}
    for entity in doc.entities:
        if entity.exclude or not entity.valid:
            continue
        for metric in entity.metrics:
            name = metric.name.strip().lower()
            if name and metric.valid:
                homes.setdefault(name, []).append((entity, metric))

    for defined in homes.values():
        if len(defined) < 2:
            continue
        for entity, metric in defined:
            elsewhere = sorted({e.table for e, _ in defined if e.table != entity.table})
            metric.valid = False
            metric.issue = (
                "`" + metric.name + "` is also defined on "
                + ", ".join("`" + t + "`" for t in elsewhere)
                + ". A metric name means one thing here, so neither is used "
                "until one is renamed."
            ) if elsewhere else (
                "`" + metric.name + "` is defined twice on this table. "
                "Rename or remove one of them."
            )


# ── joins, derived rather than asked for ─────────────────────────────────
def derive_joins(
    relationships: list[dict[str, Any]], index: SchemaIndex
) -> list[SemanticJoin]:
    """Turn the FK edge list into join semantics without spending a token.

    Cardinality follows from the constraints: a foreign key pointing at a
    primary key is many-to-one unless the referencing column is itself unique.
    Asking a model to guess this would be slower, dearer, and wrong more often
    than reading it off the catalog.
    """
    joins: list[SemanticJoin] = []
    seen: set[tuple[str, str, str]] = set()

    for rel in relationships:
        left = str(rel.get("from_table", "")).lower()
        right = str(rel.get("to_table", "")).lower()
        left_column = str(rel.get("from_column", "")).lower()
        right_column = str(rel.get("to_column", "")).lower()
        if not (left and right and left_column and right_column):
            continue
        if index.table(left) is None or index.table(right) is None:
            continue

        key = (left, right, left_column)
        if key in seen:
            continue
        seen.add(key)

        left_facts = index.table(left)
        assert left_facts is not None
        cardinality: Cardinality = (
            "one_to_one"
            if left_facts.unique == frozenset({left_column})
            else "many_to_one"
        )

        warning = ""
        if cardinality == "many_to_one":
            child = left.split(".")[-1]
            parent = right.split(".")[-1]
            warning = (
                f"{parent} rows repeat once per matching {child} row — "
                f"aggregate {parent} columns only after de-duplicating, and "
                f"count DISTINCT {parent} keys."
            )

        joins.append(
            SemanticJoin(
                left=left,
                right=right,
                on=f"{left}.{left_column} = {right}.{right_column}",
                cardinality=cardinality,
                fan_out_warning=warning,
                provenance=Provenance(source="derived"),
            )
        )
    return joins


# ── merging a regeneration over an edited document ───────────────────────
def merge_documents(
    existing: SemanticDocument, generated: SemanticDocument, *, fill_gaps: bool = False
) -> SemanticDocument:
    """Lay a fresh generation under what a person already wrote.

    Regeneration must never cost a user their work, so a human-touched entity
    survives untouched and only *new* tables are taken from the generation.
    Everything else prefers the generated entry — that is what the user asked
    for by pressing the button.

    `fill_gaps` (Phase 5 of `docs/plans/semantic-layer-model.md`) is for the
    table that grew six columns after somebody described it. It changes two
    things and nothing else: an edited entity is still kept, but it also takes
    what the generation found that it lacks (`fill_entity`); and nothing is
    dropped — an entity or glossary term the generation did not return stays,
    edited or not, because a gap-filling run that loses a table it failed to
    describe has not filled a gap.
    """
    merged = generated.model_copy(deep=True)

    if existing.time.provenance.edited:
        merged.time = existing.time.model_copy(deep=True)
    # Each of the two document-level texts is kept on its **own** flag, which
    # the editor sets when a person types into it. `_edited(existing)` stays as
    # a second reason to keep one, for documents written before the flags
    # existed: it can only keep a text, never lose one.
    if existing.business_context and (
        existing.context_provenance.edited or _edited(existing)
    ):
        merged.business_context = existing.business_context
        merged.context_provenance = existing.context_provenance.model_copy()
    # Kept on the same terms as the context above, and for a sharper reason: a
    # regeneration that replaced a hand-written exclusion rule with the model's
    # guess would change every total on the dashboard without touching a query.
    if existing.default_exclusions and (
        existing.exclusions_provenance.edited or _edited(existing)
    ):
        merged.default_exclusions = existing.default_exclusions
        merged.exclusions_provenance = existing.exclusions_provenance.model_copy()

    kept = {e.table.lower(): e for e in existing.entities if e.edited}
    merged.entities = [
        kept.get(e.table.lower(), e).model_copy(deep=True) for e in merged.entities
    ]
    # An edited entity for a table the generation skipped is still the user's —
    # and under `fill_gaps`, so is every other one.
    present = {e.table.lower() for e in merged.entities}
    merged.entities.extend(
        e.model_copy(deep=True)
        for e in existing.entities
        if e.table.lower() not in present and (fill_gaps or e.edited)
    )
    if fill_gaps:
        found = {e.table.lower(): e for e in generated.entities}
        for index, entity in enumerate(merged.entities):
            source = found.get(entity.table.lower())
            if entity.table.lower() in kept and source is not None:
                merged.entities[index] = fill_entity(
                    entity, source, taken=_metric_names(merged, but=entity.table)
                )

    have = {g.term.lower() for g in merged.glossary}
    merged.glossary.extend(
        g.model_copy(deep=True)
        for g in existing.glossary
        if g.term.lower() not in have and (fill_gaps or g.provenance.edited)
    )
    return merged


def fill_entity(
    person: SemanticEntity, generated: SemanticEntity, *, taken: frozenset[str] = frozenset()
) -> SemanticEntity:
    """`person`'s entity, with only the gaps taken from `generated`.

    **A field somebody wrote is never overwritten.** A text is taken only where
    it is empty, a list only where it has nothing in it, a role only where it is
    still `unknown`; `exclude` and the provenance stay the person's, so the
    entity is still protected from the next regeneration. Columns and metrics
    are **added** when the entity has none by that name, and an existing column
    gains only its own empty texts.

    An existing **metric is never touched**. Its empty `filters` are not a gap
    — a metric with no filters is a definition, and filling them would change a
    number while claiming to fill a blank. A generated metric whose name is in
    `taken` (defined on another table of the document) is not added either:
    `_refuse_ambiguous_metrics` would then switch off both, including the one a
    person relies on.
    """
    filled = person.model_copy(deep=True)
    for name in ("label", "description", "grain", "default_time_column"):
        if not getattr(filled, name).strip() and getattr(generated, name).strip():
            setattr(filled, name, getattr(generated, name))
    if not filled.synonyms:
        filled.synonyms = list(generated.synonyms)
    if filled.role == "unknown":
        filled.role = generated.role

    columns = {c.name.lower(): c for c in filled.columns}
    for column in generated.columns:
        mine = columns.get(column.name.lower())
        if mine is None:
            filled.columns.append(column.model_copy(deep=True))
            continue
        for name in ("label", "description", "unit"):
            if not getattr(mine, name).strip() and getattr(column, name).strip():
                setattr(mine, name, getattr(column, name))
        if not mine.synonyms:
            mine.synonyms = list(column.synonyms)
        if not mine.value_meanings:
            mine.value_meanings = dict(column.value_meanings)

    names = {m.name.lower() for m in filled.metrics} | taken
    for metric in generated.metrics:
        if metric.name.lower() not in names:
            filled.metrics.append(metric.model_copy(deep=True))
            names.add(metric.name.lower())
    return filled


def confine_to_tables(
    current: SemanticDocument, merged: SemanticDocument, tables: Iterable[str]
) -> SemanticDocument:
    """A generation over chosen tables changes those tables and nothing else.

    The generator still writes a business context, an exclusion rule, time
    conventions and a glossary on a partial run — it needs them to describe
    the tables well — but the person chose *tables*. So the chosen entities
    come from `merged`, every other entity stays exactly as it is in `current`
    and where it was, and the document-level fields are only **filled**: a
    text that is empty takes the generated one, the time conventions are taken
    only when nothing was ever set, and generated glossary terms are added
    beside the existing ones, never over them.

    Before this a partial run replaced an untouched business context with one
    written from the chosen tables alone, and dropped every glossary term those
    tables did not produce.
    """
    chosen = {t.lower() for t in tables}
    produced = {e.table.lower(): e for e in merged.entities if e.table.lower() in chosen}

    out = current.model_copy(deep=True)
    out.entities = [
        produced.pop(e.table.lower()).model_copy(deep=True)
        if e.table.lower() in produced else e
        for e in out.entities
    ]
    out.entities.extend(e.model_copy(deep=True) for e in produced.values())

    if not out.business_context.strip() and merged.business_context.strip():
        out.business_context = merged.business_context
        out.context_provenance = merged.context_provenance.model_copy()
    if not out.default_exclusions.strip() and merged.default_exclusions.strip():
        out.default_exclusions = merged.default_exclusions
        out.exclusions_provenance = merged.exclusions_provenance.model_copy()
    if not current.entities and not current.time.provenance.edited:
        out.time = merged.time.model_copy(deep=True)

    have = {g.term.lower() for g in out.glossary}
    out.glossary.extend(
        g.model_copy(deep=True) for g in merged.glossary if g.term.lower() not in have
    )
    return out


def _metric_names(doc: SemanticDocument, *, but: str) -> frozenset[str]:
    """Every metric name defined on a table other than `but`."""
    return frozenset(
        m.name.lower()
        for e in doc.entities
        if e.table.lower() != but.lower()
        for m in e.metrics
    )


def _edited(doc: SemanticDocument) -> bool:
    return doc.time.provenance.edited or any(e.edited for e in doc.entities)


def entity_stub(table: dict[str, Any]) -> SemanticEntity:
    """An empty, valid entity for a table nothing has described yet."""
    return SemanticEntity(
        table=f"{table.get('schema', '')}.{table.get('name', '')}".lower(),
        provenance=Provenance(source="derived"),
    )
