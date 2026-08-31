"""The AST walk that proposes parameters. No model call — this is a tree walk.

**The curator does not type `:params`. The AST offers them.** DataMind can do
this because the guard already parses every statement; nothing here asks a
provider anything, and the editor says so on the parameter header.

What is proposed, and what is refused (plan §1.2):

| in the statement                    | proposed as              |
|-------------------------------------|--------------------------|
| `o.created_at >= '2026-01-01'`      | `:from_date` (date)      |
| `o.created_at BETWEEN a AND b`      | `:from_date`, `:to_date` |
| `o.region = 'EMEA'`                 | `:region` (string)       |
| `o.amount > 10000`                  | `:threshold` (number)    |
| `o.status <> 'CANCELLED'`           | **refused** — an exclusion is part of the *definition* |
| anything inside `CASE` / `COALESCE` | **refused** — too likely to be business logic |

A refusal is still *returned*, unticked, with its reason next to it. Showing
the rejected candidate teaches the rule better than hiding it, and the curator
occasionally knows better — so the reason is data the UI renders, not a comment
in this file.

Two further conservatisms, chosen because a nonsense parameter on first contact
is what makes the whole feature feel unreliable:

* only the **first two** eligible proposals are ticked by default;
* a list (`IN (…)`) and a pattern (`LIKE`) are refused, because one slot cannot
  stand for a list and a pattern is nearly always part of the definition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

from app.knowledge.models import ParamType, TemplateParam

#: How many proposals arrive ticked. Past this the curator opts in per row.
DEFAULT_SUGGESTED = 2


def placeholder(name: str) -> str:
    """`region` → `:region`. The one spelling of a slot in stored SQL."""
    return f":{name}"

#: Comparison classes the walk understands. Anything else in a predicate is
#: left alone — an unrecognised shape proposes nothing, which is the fail-safe
#: direction for a feature whose failure mode is a wrong slot.
_ORDERED = (exp.GT, exp.GTE, exp.LT, exp.LTE)
_LOWER_BOUND = (exp.GT, exp.GTE)

#: Ancestors that make any literal beneath them business logic.
_LOGIC_ANCESTORS = (exp.Case, exp.Coalesce, exp.If, exp.Nullif)

_AGGREGATES = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)

#: The comparison shapes the walk reads.
_PREDICATES = (
    exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.Between, exp.In, exp.Like, exp.ILike,
)

_TEMPORAL_TYPES = ("timestamp", "datetime", "date", "time")
_NUMERIC_TYPES = (
    "int", "serial", "numeric", "decimal", "float", "double", "real",
    "money", "number",
)
_BOOLEAN_TYPES = ("bool", "bit")


def column_type(data_type: str) -> ParamType:
    """A database type name, as one of the five slot types.

    Substring matching on purpose: four engines spell the same type six ways
    (`timestamp with time zone`, `datetime2`, `TIMESTAMP(6)`, `NUMBER(10,2)`),
    and a table of exact names would be wrong the first time someone connected
    an engine nobody tested against.
    """
    text = (data_type or "").lower()
    if any(t in text for t in _TEMPORAL_TYPES):
        # A day, or an instant. The binder resolves the two differently —
        # "last month" is a range of days on one and a range of instants on
        # the other — so the distinction is worth getting right. `time`
        # appears in every spelling that carries a clock (`datetime2`,
        # `timestamp`, `TIMESTAMP(6)`) and in none that does not.
        return ParamType.DATETIME if "time" in text else ParamType.DATE
    if any(t in text for t in _BOOLEAN_TYPES):
        return ParamType.BOOLEAN
    if any(t in text for t in _NUMERIC_TYPES):
        return ParamType.NUMBER
    return ParamType.STRING


@dataclass(frozen=True, slots=True)
class ColumnFacts:
    qualified: str
    name: str
    data_type: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    distinct_count: int | None = None

    @property
    def param_type(self) -> ParamType:
        return column_type(self.data_type)

    @property
    def is_measure(self) -> bool:
        """A number that is a *quantity*, not an identity.

        A key compared with `>` is a paging cursor, not a threshold, and
        calling it `:threshold` would be a lie in the editor.
        """
        return (
            self.param_type is ParamType.NUMBER
            and not self.is_primary_key
            and not self.is_foreign_key
        )


class SchemaFacts:
    """Column lookup over one snapshot, by qualified name or by suffix.

    A local index rather than `app.semantic.build_index`: this package sits
    *below* the semantic layer in the dependency rule, and it needs one thing
    that index does not carry — the per-column facts that decide whether a
    number is a threshold or a key.
    """

    __slots__ = ("_by_qualified",)

    def __init__(self, tables: list[dict[str, Any]] | None = None) -> None:
        self._by_qualified: dict[str, dict[str, ColumnFacts]] = {}
        for table in tables or []:
            qualified = f"{table.get('schema', '')}.{table.get('name', '')}".lower()
            self._by_qualified[qualified] = {
                str(c["name"]).lower(): ColumnFacts(
                    qualified=qualified,
                    name=str(c["name"]),
                    data_type=str(c.get("data_type", "")),
                    is_primary_key=bool(c.get("is_primary_key")),
                    is_foreign_key=bool(c.get("is_foreign_key")),
                    distinct_count=c.get("distinct_count"),
                )
                for c in table.get("columns", [])
                if c.get("name")
            }

    def resolve_table(self, name: str) -> str | None:
        """`orders` → `public.orders`, when exactly one table matches."""
        key = (name or "").lower()
        if key in self._by_qualified:
            return key
        hits = [q for q in self._by_qualified if q.split(".")[-1] == key.split(".")[-1]]
        return hits[0] if len(hits) == 1 else None

    def column(self, table: str | None, column: str) -> ColumnFacts | None:
        if table is not None:
            qualified = self.resolve_table(table)
            if qualified is None:
                return None
            return self._by_qualified[qualified].get(column.lower())
        # Unqualified: accept it only when exactly one table in the snapshot
        # has a column by that name. Two candidates is a guess, and a guessed
        # type produces a slot that never binds.
        hits = [
            cols[column.lower()]
            for cols in self._by_qualified.values()
            if column.lower() in cols
        ]
        return hits[0] if len(hits) == 1 else None


class ParamProposal(BaseModel):
    """One offer, ticked or not, with the exact literal it would replace."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: ParamType
    #: The literal as the statement renders it — `'EMEA'`, `10000`. The editor
    #: highlights it in the SQL so the curator sees *what would change*.
    literal: str
    #: Which occurrence of that exact text this is, 0-based. Two `'EMEA'`s in
    #: one statement are two proposals and the editor must highlight the right
    #: one.
    occurrence: int = 0
    comment: str = ""
    #: Ticked by default. False for a refusal *and* for an eligible proposal
    #: past `DEFAULT_SUGGESTED`.
    suggested: bool = False
    #: True when this literal may be parameterized at all.
    eligible: bool = True
    #: Written for the curator, verbatim, in the row next to the checkbox.
    reason: str = ""

    def as_param(self) -> TemplateParam:
        return TemplateParam(name=self.name, type=self.type, comment=self.comment)


@dataclass(slots=True)
class _Candidate:
    node: exp.Expression          # the literal to replace
    name: str
    type: ParamType
    eligible: bool
    reason: str
    comment: str = ""


def _aliases(tree: exp.Expression) -> dict[str, str]:
    """`o` → `orders`, plus every table under its own name."""
    mapping: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        mapping[name.lower()] = name
        alias = table.alias
        if alias:
            mapping[alias.lower()] = name
    return mapping


def _under_logic(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, _LOGIC_ANCESTORS):
            return True
        parent = parent.parent
    return False


def _single_column(node: exp.Expression) -> exp.Column | None:
    """The one column a comparison's left side is about, or None.

    An aggregate over exactly one column counts — `HAVING SUM(o.amount) >
    10000` is the plan's "comparison against a numeric measure", and refusing
    it would refuse the clearest threshold there is.
    """
    if isinstance(node, exp.Column):
        return node
    if isinstance(node, _AGGREGATES):
        inner = node.this
        return inner if isinstance(inner, exp.Column) else None
    return None


def _is_value(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) or (
        isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal)
    )


def _facts_for(
    column: exp.Column, aliases: dict[str, str], facts: SchemaFacts
) -> ColumnFacts | None:
    table = column.table
    resolved = aliases.get(table.lower()) if table else None
    return facts.column(resolved or table or None, column.name)


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        taken.add(name)
        return name
    for suffix in range(2, 100):
        candidate = f"{name}_{suffix}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise ValueError("could not name a parameter")  # pragma: no cover


def _name_for(
    predicate: exp.Expression, column: ColumnFacts | None, fallback: str, taken: set[str]
) -> tuple[str, ParamType, str]:
    """What to call the slot, what it holds, and why it is being offered.

    The reason is produced here rather than guessed at the call site because it
    has to agree with the name: a `bigint` primary key compared with `>` is a
    paging cursor, and calling that "a measure" in the editor would be the kind
    of small lie that makes a curator stop trusting the whole panel.
    """
    kind = column.param_type if column else ParamType.STRING
    base = (column.name if column else fallback).lower()
    reason = "a value the question could name"

    if isinstance(predicate, exp.EQ):
        reason = "an equality against a column the question names"
    if isinstance(predicate, _ORDERED) and kind.is_temporal:
        base = "from_date" if isinstance(predicate, _LOWER_BOUND) else "to_date"
        reason = "a comparison against a date column"
    elif isinstance(predicate, _ORDERED) and column is not None and column.is_measure:
        base = "threshold"
        reason = "a comparison against a measure"
    elif isinstance(predicate, _ORDERED) and kind.is_temporal:
        reason = "a comparison against a date column"
    return _unique(base, taken), kind, reason


def _comment_for(column: ColumnFacts | None, kind: ParamType) -> str:
    if kind.is_temporal:
        return "a date the asker names, resolved when the question is asked"
    if column is not None and column.distinct_count and column.distinct_count <= 50:
        return f"one of the {column.distinct_count} values in {column.name}"
    return ""


def _candidates(tree: exp.Expression, facts: SchemaFacts) -> list[_Candidate]:
    """Every literal in a predicate, in statement order, judged."""
    aliases = _aliases(tree)
    taken: set[str] = set()
    out: list[_Candidate] = []

    # Depth-first, pre-order — which is *statement* order for the left-deep
    # `AND` chain a WHERE clause parses into. The editor renders proposals in
    # this order next to the SQL, so "the third row is the third filter" has to
    # be true; `find_all` does not promise it.
    for node in tree.walk(bfs=False):
        if not isinstance(node, _PREDICATES):
            continue
        predicate = node
        column_node = _single_column(predicate.this)
        if column_node is None:
            continue
        column = _facts_for(column_node, aliases, facts)
        label = column_node.name

        refusal = _refusal(predicate)
        values = _values_of(predicate)
        if not values:
            continue

        if refusal is not None:
            out.extend(
                _Candidate(node=v, name=label, type=ParamType.STRING,
                           eligible=False, reason=refusal)
                for v in values
            )
            continue

        if isinstance(predicate, exp.Between):
            kind = column.param_type if column else ParamType.STRING
            low, high = (
                ("from_date", "to_date") if kind.is_temporal
                else (f"min_{label.lower()}", f"max_{label.lower()}")
            )
            comment = _comment_for(column, kind)
            for value, base in zip(values, (low, high), strict=False):
                out.append(_Candidate(
                    node=value, name=_unique(base, taken), type=kind,
                    eligible=True, reason="a bound the asker names", comment=comment,
                ))
            continue

        name, kind, reason = _name_for(predicate, column, label, taken)
        out.append(_Candidate(
            node=values[0], name=name, type=kind, eligible=True,
            reason=reason, comment=_comment_for(column, kind),
        ))
    return out


def _refusal(predicate: exp.Expression) -> str | None:
    """Why this predicate's literals must not become a slot, or None."""
    if _under_logic(predicate):
        return "inside a CASE or COALESCE — usually business logic, not the question"
    if isinstance(predicate, exp.NEQ):
        return "inside a ≠ — an exclusion is usually part of the definition"
    if isinstance(predicate, exp.In):
        if predicate.parent is not None and isinstance(predicate.parent, exp.Not):
            return "inside a NOT IN — an exclusion is usually part of the definition"
        return "a list, which one slot cannot stand for"
    if isinstance(predicate, (exp.Like, exp.ILike)):
        return "a pattern match — usually part of the definition"
    if predicate.parent is not None and isinstance(predicate.parent, exp.Not):
        return "negated — an exclusion is usually part of the definition"
    return None


def _values_of(predicate: exp.Expression) -> list[exp.Expression]:
    if isinstance(predicate, exp.Between):
        low, high = predicate.args.get("low"), predicate.args.get("high")
        return [n for n in (low, high) if _is_value(n)]
    if isinstance(predicate, exp.In):
        return [n for n in predicate.expressions if _is_value(n)]
    right = predicate.expression
    return [right] if _is_value(right) else []


def _parse(sql: str, dialect: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read=dialect)
    except (ParseError, Exception):  # noqa: B014 - sqlglot raises several
        return None


def _occurrences(sql: str, candidates: list[_Candidate], dialect: str) -> list[int]:
    """For each candidate, which occurrence of its literal text it is."""
    seen: dict[str, int] = {}
    out: list[int] = []
    for candidate in candidates:
        text = candidate.node.sql(dialect=dialect)
        out.append(seen.get(text, 0))
        seen[text] = seen.get(text, 0) + 1
    return out


def propose_params(
    sql: str, *, dialect: str = "postgres", tables: list[dict[str, Any]] | None = None
) -> list[ParamProposal]:
    """Every literal the walk found, ticked or refused, in statement order.

    Unparseable SQL proposes nothing rather than raising: the editor calls this
    on every pause in typing, and half a statement is the normal case.
    """
    tree = _parse(sql, dialect)
    if tree is None:
        return []

    facts = SchemaFacts(tables)
    candidates = _candidates(tree, facts)
    occurrences = _occurrences(sql, candidates, dialect)

    proposals: list[ParamProposal] = []
    ticked = 0
    for candidate, occurrence in zip(candidates, occurrences, strict=True):
        suggested = candidate.eligible and ticked < DEFAULT_SUGGESTED
        ticked += int(suggested)
        proposals.append(ParamProposal(
            name=candidate.name,
            type=candidate.type,
            literal=candidate.node.sql(dialect=dialect),
            occurrence=occurrence,
            comment=candidate.comment,
            suggested=suggested,
            eligible=candidate.eligible,
            reason=candidate.reason,
        ))
    return proposals


def parameterize(
    sql: str,
    accept: set[str],
    *,
    dialect: str = "postgres",
    tables: list[dict[str, Any]] | None = None,
) -> tuple[str, list[TemplateParam]]:
    """Replace the accepted literals with `:placeholders`, on the tree.

    On the tree, never by string replacement: `'EMEA'` appears in the statement
    once as a filter and possibly again inside a `CASE`, and a `str.replace`
    would rewrite both. The walk is the same one `propose_params` ran, so the
    name the curator ticked is the literal that moves.

    Returns the parameterized SQL and the declared parameters, in the order the
    slots appear. An accepted name the walk cannot find is ignored rather than
    raising — the editor may be a keystroke behind the server.
    """
    tree = _parse(sql, dialect)
    if tree is None:
        return sql, []

    facts = SchemaFacts(tables)
    chosen = [
        c for c in _candidates(tree, facts) if c.eligible and c.name in accept
    ]
    for candidate in chosen:
        # `exp.Var(":name")`, not `exp.Placeholder`: the Postgres generator
        # renders a Placeholder as `%(name)s`, which is a driver's binding
        # syntax and not what a curator reads or what this store agrees on.
        # A `Var` holding the literal text renders `:name` in all four
        # dialects, and re-parses as a `Placeholder` — both classes are on the
        # guard's allowlist, so the statement is guardable in either form.
        candidate.node.replace(exp.Var(this=placeholder(candidate.name)))

    params = [
        TemplateParam(name=c.name, type=c.type, comment=c.comment) for c in chosen
    ]
    return tree.sql(dialect=dialect), params
