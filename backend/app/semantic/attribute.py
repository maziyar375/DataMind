"""Did an answer use a metric's definition? Observed after the run, never enforced.

Phase 3 of `docs/plans/semantic-layer-model.md`. A metric reaches the prompt as
one line of text, and until this module nothing read it afterwards: a run
recorded no difference between SQL that used `revenue = SUM(total_amount) WHERE
status <> 'cancelled'` and SQL that summed the column and forgot the filter.

`attribute` reads the statement the guard already validated, and the layer
version the run was written with, and gives each metric in scope one of three
verdicts. It parses; it never executes and never rewrites. **It observes and
never enforces** (D8): nothing here can fail, block or change a run, and the
layer stays *fail open*.

The metrics in scope are the valid metrics on valid, non-excluded entities
whose table the statement touched.

* **`used`** — an expression the same, after normalising both sides, as the
  metric's expression, in a `SELECT` where **every** filter of the definition is
  a conjunct of the `WHERE`, the `HAVING` or an inner join's `ON` of that
  `SELECT` or of a derived table or CTE feeding it through `FROM` or an inner
  join.
* **`ignored`** — that same expression, with **at least one** filter of the
  definition *demonstrably* absent: no conjunct, grouping or join condition in
  any scope feeding the aggregate, or enclosing it, mentions a column the
  filter names — and every one of those could be read. This is the only verdict
  that accuses an answer of something, so it is the narrowest.
* **`unknown`** — everything else, and the default: no matching expression; a
  window function, a `FILTER` clause or a set operation around it; the metric's
  table read twice; a condition over the filter's column written in a form that
  does not normalise to the definition's (`status IN (…)` is not `<>`); a
  subquery or an unresolved column anywhere a filter could hide.

**Normalising** qualifies every column to `schema.table.column` through
aliases, CTEs and derived tables (a `*` is followed; an expression projection
is not a column), lower-cases identifiers, drops parentheses, and simplifies a
conjunct, so `status != 'X'`, `NOT status = 'X'` and `(status <> 'X')` are one
condition. String literals keep their case: `'CANCELLED'` is not `'cancelled'`.

**What this does not see.** A filterless metric such as `SUM(amount)` matches
any statement that sums the column, which is coincidence as often as use. That
is not decided here; the eval's layer-off arm is the control that tells the two
apart (§4.4). A matching aggregate over a join that fans rows out is still
`used`: the chip says the statement matches the definition, and join fan-out is
the join cautions' subject.

Pure, like the rest of `app.semantic`: a statement, a dialect, a document and an
optional schema index in; verdicts out. A statement that will not parse raises
`AttributionError`, and the caller stores nothing.
"""
from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope, walk_in_scope
from sqlglot.optimizer.simplify import simplify

from app.semantic.models import SemanticDocument, SemanticEntity, SemanticMetric
from app.semantic.validate import SchemaIndex

USED = "used"
IGNORED = "ignored"
UNKNOWN = "unknown"
VERDICTS = (USED, IGNORED, UNKNOWN)

#: How deep a column is followed through derived tables and CTEs. A statement
#: nested deeper than this is unusual enough to call `unknown`.
_MAX_DEPTH = 16


class AttributionError(ValueError):
    """The statement could not be read. Nothing is attributed."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """One metric's verdict. Schema vocabulary only — no values, no SQL."""

    metric: str
    entity: str
    verdict: str

    def as_dict(self) -> dict[str, str]:
        return {"metric": self.metric, "entity": self.entity, "verdict": self.verdict}


def attribute(
    sql: str,
    dialect: str,
    doc: SemanticDocument,
    tables_touched: Iterable[str],
    *,
    schema: SchemaIndex | None = None,
) -> list[Verdict]:
    """A verdict for every metric in scope, in document order."""
    touched = {t.strip().lower() for t in tables_touched if t}
    in_scope = [
        (entity, metric)
        for entity in doc.entities
        if entity.valid and not entity.exclude and entity.table.strip().lower() in touched
        for metric in entity.metrics
        if metric.valid
    ]
    if not in_scope:
        return []

    try:
        tree = sqlglot.parse_one(sql, read=dialect or None)
    except Exception as err:  # sqlglot raises several types; all mean "unreadable"
        raise AttributionError("The statement could not be parsed.") from err
    root = build_scope(tree) if tree is not None else None
    if root is None:
        raise AttributionError("The statement has no query to read.")

    known = set(touched) | {e.table.strip().lower() for e in doc.entities}
    if schema is not None:
        known |= set(schema.tables)
    reader = _Reader(dialect=dialect, known=known, schema=schema)
    scopes = [s for s in root.traverse() if isinstance(s.expression, exp.Select)]

    out: list[Verdict] = []
    for entity, metric in in_scope:
        try:
            verdict = _judge(reader, scopes, entity, metric)
        except Exception:  # noqa: BLE001 — one unreadable metric is `unknown`, not a failure
            verdict = UNKNOWN
        out.append(Verdict(metric.name, entity.table.strip().lower(), verdict))
    return out


# ── one metric ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class _Definition:
    root_type: type[exp.Expression]
    expression: str
    #: Canonical filter conjunct → the base columns it names.
    filters: dict[str, frozenset[str]]
    #: The entity's own table.
    entity: str
    #: The entity's table and its required joins: each may be read only once.
    tables: frozenset[str]
    #: True for an expression that names no column (`COUNT(*)`).
    columnless: bool


def _judge(
    reader: _Reader, scopes: list[Scope], entity: SemanticEntity, metric: SemanticMetric
) -> str:
    definition = _definition(reader, entity, metric)
    if definition is None:
        return UNKNOWN
    verdicts: set[str] = set()
    for scope in scopes:
        for node in walk_in_scope(scope.expression):
            if type(node) is not definition.root_type:
                continue
            if reader.canonical(node, scope, simplify_it=False) != definition.expression:
                continue
            verdicts.add(_occurrence(reader, scope, node, definition))
    if len(verdicts) == 1:
        return verdicts.pop()
    return UNKNOWN


def _definition(
    reader: _Reader, entity: SemanticEntity, metric: SemanticMetric
) -> _Definition | None:
    table = entity.table.strip().lower()
    joins = [j.strip().lower() for j in metric.required_joins if j.strip()]
    sources = ", ".join([table, *joins])
    text = (metric.expression or "").strip().rstrip(";")
    if not text:
        return None
    try:
        probe = sqlglot.parse_one(
            f"SELECT {text} FROM {sources}", read=reader.dialect or None  # noqa: S608
        )
    except Exception:
        return None
    scope = build_scope(probe)
    if scope is None or not probe.selects:
        return None
    node = probe.selects[0].unalias()
    metric_reader = reader.for_definition(default_table=table)
    expression = metric_reader.canonical(node, scope, simplify_it=False)
    if expression is None:
        return None

    filters: dict[str, frozenset[str]] = {}
    for raw in metric.filters:
        clause = (raw or "").strip().rstrip(";")
        if not clause:
            continue
        try:
            where = sqlglot.parse_one(
                f"SELECT 1 FROM {sources} WHERE {clause}", read=reader.dialect or None  # noqa: S608
            )
        except Exception:
            return None
        where_scope = build_scope(where)
        condition = where.args.get("where")
        if where_scope is None or condition is None:
            return None
        for part in _and_parts(condition.this):
            canonical = metric_reader.canonical(part, where_scope, simplify_it=True)
            columns = metric_reader.base_columns(part, where_scope)
            if canonical is None or not columns:
                return None
            filters[canonical] = columns
    return _Definition(
        root_type=type(node),
        expression=expression,
        filters=filters,
        entity=table,
        tables=frozenset([table, *joins]),
        columnless=not any(True for _ in node.find_all(exp.Column)),
    )


def _occurrence(
    reader: _Reader, scope: Scope, node: exp.Expression, definition: _Definition
) -> str:
    """The verdict for one matching expression in one `SELECT`."""
    if isinstance(node.parent, (exp.Window, exp.Filter)) or _in_set_operation(scope):
        return UNKNOWN

    inner = [scope, *_feeders(scope, inner_only=True)]
    every = [scope, *_feeders(scope, inner_only=False)]

    # The metric's tables each read once, or which instance a filter scopes is
    # a guess — `orders o JOIN orders o2 … WHERE o2.status <> 'x'` would
    # otherwise look filtered.
    instances = Counter(
        key
        for s in every
        for key in (reader.table_key(src) for src in _table_sources(s))
        if key is not None
    )
    if any(instances.get(t, 0) > 1 for t in definition.tables):
        return UNKNOWN
    # `COUNT(*)` counts whatever the FROM clause produces; it is the metric only
    # when that is the metric's own table, read once, and nothing else.
    if definition.columnless and instances != Counter({definition.entity: 1}):
        return UNKNOWN

    present: set[str] = set()
    for s in inner:
        for conjunct in _conjuncts(s, inner_joins_only=True):
            canonical = reader.canonical(conjunct, s, simplify_it=True)
            if canonical is not None:
                present.add(canonical)
    missing = [f for f in definition.filters if f not in present]
    if not missing:
        return USED

    # `ignored` needs the absence to be *demonstrable*: everything that could
    # restrict or split the rows, feeding or enclosing the aggregate, was read,
    # and none of it names a column the missing filter names.
    mentioned: set[str] = set()
    readers: list[tuple[Scope, list[exp.Expression]]] = [
        (s, [*_conjuncts(s, inner_joins_only=False), *_grouping(s)]) for s in every
    ]
    parent = scope.parent
    depth = 0
    while parent is not None and depth < _MAX_DEPTH:
        if isinstance(parent.expression, exp.Select):
            readers.append(
                (parent, [*_conjuncts(parent, inner_joins_only=False), *_grouping(parent)])
            )
        parent = parent.parent
        depth += 1
    for owner, expressions in readers:
        for expression in expressions:
            columns = reader.base_columns(expression, owner)
            if columns is None:
                return UNKNOWN
            mentioned |= columns
    if any(not (definition.filters[f] & mentioned) for f in missing):
        return IGNORED
    return UNKNOWN


# ── scopes ───────────────────────────────────────────────────────────────
def _in_set_operation(scope: Scope) -> bool:
    node = scope.expression.parent
    while node is not None:
        if isinstance(node, exp.SetOperation):
            return True
        if isinstance(node, (exp.Subquery, exp.CTE)):
            return False
        node = node.parent
    return False


def _join_of(scope: Scope, name: str) -> exp.Join | None:
    for join in scope.expression.args.get("joins") or []:
        if join.this.alias_or_name.lower() == name.lower():
            return join
    return None


def _feeders(scope: Scope, *, inner_only: bool, depth: int = 0) -> list[Scope]:
    """Derived tables and CTEs this `SELECT` reads rows from, recursively."""
    if depth >= _MAX_DEPTH:
        return []
    out: list[Scope] = []
    for name, (_node, source) in scope.selected_sources.items():
        if not isinstance(source, Scope):
            continue
        join = _join_of(scope, name)
        if inner_only and join is not None and join.side:
            continue
        if not isinstance(source.expression, exp.Select):
            out.append(source)
            continue
        out.append(source)
        out.extend(_feeders(source, inner_only=inner_only, depth=depth + 1))
    return out


def _table_sources(scope: Scope) -> list[exp.Table]:
    if not isinstance(scope.expression, exp.Select):
        return []
    return [
        source
        for _name, (_node, source) in scope.selected_sources.items()
        if isinstance(source, exp.Table)
    ]


def _and_parts(node: exp.Expression) -> list[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return [*_and_parts(node.left), *_and_parts(node.right)]
    return [node]


def _conjuncts(scope: Scope, *, inner_joins_only: bool) -> list[exp.Expression]:
    select = scope.expression
    if not isinstance(select, exp.Select):
        return []
    out: list[exp.Expression] = []
    for key in ("where", "having"):
        clause = select.args.get(key)
        if clause is not None:
            out.extend(_and_parts(clause.this))
    for join in select.args.get("joins") or []:
        if inner_joins_only and join.side:
            continue
        on = join.args.get("on")
        if on is not None:
            out.extend(_and_parts(on))
    return out


def _grouping(scope: Scope) -> list[exp.Expression]:
    select = scope.expression
    group = select.args.get("group") if isinstance(select, exp.Select) else None
    return list(group.expressions) if group is not None else []


# ── resolving names ──────────────────────────────────────────────────────
class _Reader:
    """Resolves columns to `schema.table.column` inside one statement."""

    def __init__(
        self,
        *,
        dialect: str,
        known: set[str],
        schema: SchemaIndex | None,
        default_table: str | None = None,
    ) -> None:
        self.dialect = dialect
        self._known = known
        self._schema = schema
        #: For a metric's own definition: a bare column belongs to the entity
        #: when nothing else claims it, as `check_expression` reads it.
        self._default = default_table

    def for_definition(self, *, default_table: str) -> _Reader:
        return _Reader(
            dialect=self.dialect, known=self._known, schema=self._schema,
            default_table=default_table,
        )

    def table_key(self, table: exp.Table) -> str | None:
        name = (table.name or "").lower()
        if not name:
            return None
        db = (table.db or "").lower()
        if db:
            return f"{db}.{name}"
        hits = sorted(k for k in self._known if k.split(".")[-1] == name)
        return hits[0] if len(hits) == 1 else None

    def canonical(self, node: exp.Expression, scope: Scope, *, simplify_it: bool) -> str | None:
        """`node` with every column qualified to its base table, as text; or None."""
        if any(True for _ in node.find_all(exp.Subquery, exp.Select)) and not isinstance(
            node, exp.Select
        ):
            return None
        copy = node.copy()
        while isinstance(copy, exp.Paren):
            copy = copy.this
        for column in list(copy.find_all(exp.Column)):
            resolved = self.column(column, scope)
            if resolved is None:
                return None
            key, name = resolved
            db, _, table = key.rpartition(".")
            replacement = exp.Column(
                this=exp.to_identifier(name),
                table=exp.to_identifier(table),
                db=exp.to_identifier(db) if db else None,
            )
            if column is copy:
                copy = replacement
            else:
                column.replace(replacement)
        if simplify_it:
            # An unsimplified condition still compares; it just matches less.
            with contextlib.suppress(Exception):
                copy = simplify(copy)
            while isinstance(copy, exp.Paren):
                copy = copy.this
        return copy.sql()

    def base_columns(
        self, node: exp.Expression, scope: Scope, depth: int = 0
    ) -> frozenset[str] | None:
        """Every base column `node` reads, or None when any of them is unreadable."""
        if depth >= _MAX_DEPTH:
            return None
        if any(True for _ in node.find_all(exp.Subquery, exp.Select)):
            return None
        out: set[str] = set()
        for column in node.find_all(exp.Column):
            found = self._lineage(column, scope, depth)
            if found is None:
                return None
            out |= found
        return frozenset(out)

    def column(
        self, column: exp.Column, scope: Scope, depth: int = 0
    ) -> tuple[str, str] | None:
        """`(table key, column)` for the one base column a plain reference reads."""
        if depth >= _MAX_DEPTH:
            return None
        name = (column.name or "").lower()
        if not name:
            return None
        source = self._source(column, scope)
        if isinstance(source, exp.Table):
            key = self.table_key(source)
            return (key, name) if key else None
        if isinstance(source, Scope):
            projection = _projection(source, name)
            if isinstance(projection, exp.Column):
                return self.column(projection, source, depth + 1)
            return None
        if source is None and self._default and not column.table:
            return (self._default, name)
        return None

    def _lineage(self, column: exp.Column, scope: Scope, depth: int) -> frozenset[str] | None:
        name = (column.name or "").lower()
        if not name:
            return None
        source = self._source(column, scope)
        if isinstance(source, exp.Table):
            key = self.table_key(source)
            return frozenset({f"{key}.{name}"}) if key else None
        if isinstance(source, Scope):
            projection = _projection(source, name)
            if projection is None:
                return None
            return self.base_columns(projection, source, depth + 1)
        if source is None and self._default and not column.table:
            return frozenset({f"{self._default}.{name}"})
        # An output alias (`ORDER BY total`, `HAVING n > 1` in some dialects)
        # is not a column of any source.
        select = scope.expression
        if isinstance(select, exp.Select) and not column.table:
            for projection in select.selects:
                if isinstance(projection, exp.Alias) and projection.alias.lower() == name:
                    return self.base_columns(projection.this, scope, depth + 1)
        return None

    def _source(self, column: exp.Column, scope: Scope) -> exp.Table | Scope | None:
        selected = {
            name.lower(): source for name, (_node, source) in scope.selected_sources.items()
        }
        qualifier = (column.table or "").lower()
        if qualifier:
            return selected.get(qualifier)
        if len(selected) == 1:
            return next(iter(selected.values()))
        name = (column.name or "").lower()
        hits = [source for source in selected.values() if self._exposes(source, name)]
        return hits[0] if len(hits) == 1 else None

    def _exposes(self, source: exp.Table | Scope, name: str) -> bool:
        if isinstance(source, exp.Table):
            key = self.table_key(source)
            facts = self._schema.table(key) if (self._schema is not None and key) else None
            return facts is not None and name in facts.columns
        return _projection(source, name) is not None


def _projection(scope: Scope, name: str) -> exp.Expression | None:
    """What a derived table or CTE calls `name`: an expression, or a column
    reached through its `*`. None when it has no such output."""
    select = scope.expression
    if not isinstance(select, exp.Select):
        return None
    star: exp.Expression | None = None
    for projection in select.selects:
        if isinstance(projection, exp.Star):
            star = projection
            continue
        if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
            star = projection
            continue
        if projection.alias_or_name.lower() == name:
            return projection.unalias()
    if star is None:
        return None
    table = star.table if isinstance(star, exp.Column) else ""
    return exp.Column(
        this=exp.to_identifier(name), table=exp.to_identifier(table) if table else None
    )
