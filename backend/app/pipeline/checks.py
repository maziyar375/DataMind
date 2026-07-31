"""Deterministic result checks — the third failure mode.

The repair loop already handles two signals: the guard rejected the SQL, and
the database refused to run it. Both mean *the query did not produce a result*.
This module covers the third and most expensive case: the query ran, returned
something plausible, and is wrong.

Every check here is **structural** — it reads the SQL, the schema snapshot and
the result *shape*, never a result value. That is deliberate rather than
incidental:

* it costs no tokens and adds no latency, so it can run on every request;
* it cannot inherit the generator's own misreading of the question, the way a
  model critiquing its own SQL does;
* and it works identically under every disclosure policy, including NONE,
  where a model-based critic would be handed the string "(Result data was not
  shared with the model by policy.)" and could conclude nothing.

A finding is a *suspicion*, never a verdict, and the bar for acting on one is
deliberately high: **a check informs the answer, it does not rewrite the
query.** Only `retry=True` findings may spend a regeneration, and today that is
`C_EMPTY_RESULT` alone — and only the subset of it that carries *structural
evidence* the query is wrong: a literal the snapshot says the column cannot
hold, or a range wholly outside the column's recorded bounds. An empty result
with no such evidence is a legitimate answer ("no orders in that window"), so
it is reported and left alone.

Everything else is advisory: recorded on the attempt, emitted as an event, and
shown in the step trail for a human to judge. That is not caution for its own
sake. `C_NULLABLE_INNER_JOIN` shipped retry-eligible and cost four correct
answers on `sales_v1` (see `reports/sales_v1_deepseek_2026-07-27.md`), because
"should this nullable foreign key be outer-joined?" is a question about what
the user meant, and a structural check cannot see intent. Resolving that kind
of ambiguity by heuristic is the failure mode the eval already identified as
the binding constraint — automating it makes the product worse, not better.
"""
from __future__ import annotations

import re
from typing import Any, cast

import sqlglot
from pydantic import BaseModel
from sqlglot import expressions as exp

# Columns whose name marks a row as not-really-there. Filtering them is almost
# always required for a question about what currently exists, and forgetting is
# invisible: the query succeeds and quietly over-counts.
_SOFT_DELETE_PATTERNS = (
    r"^is_deleted$", r"^deleted$", r"^is_archived$", r"^archived$",
    r"^is_active$", r"^active$", r"^is_void(ed)?$", r"^is_test$",
)
_SOFT_DELETE_RE = re.compile("|".join(_SOFT_DELETE_PATTERNS), re.IGNORECASE)

# Questions whose natural answer is one number. Kept narrow: a false positive
# here is only advisory, but noise in the step trail still costs trust.
_SINGLE_FIGURE_RE = re.compile(
    r"\b(how many|how much|what (is|was) the (total|average|sum|number)|"
    r"total number of|count of)\b",
    re.IGNORECASE,
)

# Only values that order the same way as text are compared against a column's
# recorded min/max: numbers, and ISO dates/timestamps (which sort lexically).
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")


class Finding(BaseModel):
    """One structural suspicion about a result that ran successfully."""

    code: str
    message: str
    hint: str
    retry: bool = False

    def to_feedback(self) -> str:
        return f"- [{self.code}] {self.message} {self.hint}"


def _alias_map(statement: exp.Expression) -> dict[str, str]:
    """alias (or bare name) -> real table name, for resolving `o.employee_id`."""
    aliases: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        aliases[table.alias_or_name] = table.name
    return aliases


def _column_index(tables: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(table name, column name) -> column dict, lowercased on both keys."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for table in tables:
        for column in table.get("columns", []):
            index[(table["name"].lower(), column["name"].lower())] = column
    return index


def _referenced_table_names(statement: exp.Expression) -> set[str]:
    return {t.name.lower() for t in statement.find_all(exp.Table)}


def _resolve_column(
    column: exp.Column,
    aliases: dict[str, str],
    referenced: set[str],
    columns: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    """The snapshot entry for a column reference, qualified or not."""
    name = column.name.lower()
    if column.table:
        return columns.get((aliases.get(column.table, column.table).lower(), name))
    # Unqualified: trust it only when exactly one queried table carries the
    # name, so an ambiguous reference resolves to no opinion rather than the
    # wrong column's statistics.
    matches = [
        info
        for (table_name, column_name), info in columns.items()
        if column_name == name and table_name in referenced
    ]
    return matches[0] if len(matches) == 1 else None


def _literal_text(node: exp.Expression | None) -> str | None:
    """The written form of a scalar literal, or None if it isn't one.

    `DATE '2026-01-01'` and `'x'::text` arrive as casts, and a negative number
    as a negation; all three are the same literal to a range comparison.
    """
    if isinstance(node, exp.Cast):
        return _literal_text(node.this)
    if isinstance(node, exp.Neg):
        inner = _literal_text(node.this)
        return None if inner is None else f"-{inner}"
    if isinstance(node, exp.Literal):
        return str(node.this)
    return None


def _orderable(text: str) -> float | str | None:
    """`text` as something comparable to a recorded bound, else None."""
    stripped = text.strip()
    try:
        return float(stripped)
    except ValueError:
        return stripped if _ISO_DATE_RE.match(stripped) else None


def _beyond(text: str | None, bound: Any, *, above: bool) -> bool:
    """True when `text` lies strictly past `bound`, comparing like with like."""
    if text is None or bound is None:
        return False
    value, limit = _orderable(text), _orderable(str(bound))
    if isinstance(value, float) and isinstance(limit, float):
        return value > limit if above else value < limit
    if isinstance(value, str) and isinstance(limit, str):
        return value > limit if above else value < limit
    return False


def _matches_any(text: str, samples: list[Any]) -> bool:
    """Case-insensitive membership, tolerating `1` vs `1.0` on numbers."""
    needle = text.strip().casefold()
    for sample in samples:
        candidate = str(sample).strip()
        if candidate.casefold() == needle:
            return True
        try:
            if float(candidate) == float(text):
                return True
        except ValueError:
            continue
    return False


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    """Predicates joined by AND, unwrapping parentheses.

    OR and NOT branches are deliberately left whole and therefore ignored by
    the caller: an unmatchable literal under an OR proves nothing, because a
    sibling branch can still match rows.
    """
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        # sqlglot's stubs widen operand access to `Expr`; at runtime both
        # sides are `Expression`, which is all this walks.
        left = cast(exp.Expression, node.left)
        right = cast(exp.Expression, node.right)
        return _conjuncts(left) + _conjuncts(right)
    return [node]


def _required_predicates(statement: exp.Expression) -> list[exp.Expression]:
    """Every predicate that must hold for a row to survive: WHERE and JOIN ON."""
    roots: list[exp.Expression] = []
    for where in statement.find_all(exp.Where):
        if where.this is not None:
            roots.append(where.this)
    for join in statement.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            roots.append(on)

    predicates: list[exp.Expression] = []
    for root in roots:
        predicates.extend(_conjuncts(root))
    return predicates


def _contradicts_snapshot(
    predicate: exp.Expression,
    aliases: dict[str, str],
    referenced: set[str],
    columns: dict[tuple[str, str], dict[str, Any]],
) -> bool:
    """True when the snapshot's own statistics say this filter matches nothing.

    Only the two statistics that are exact by construction are trusted:
    `sample_values`, which connectors emit *only* when it is provably the
    column's complete domain (`infra/connectors/hints.py`), and min/max.
    """

    def sided(left: Any, right: Any) -> tuple[exp.Column, str, bool] | None:
        """(column, literal text, literal was the right operand).

        Operands are `Any` because sqlglot's stubs widen them to `Expr`; the
        `isinstance` checks below are what actually narrows them.
        """
        text = _literal_text(right)
        if isinstance(left, exp.Column) and text is not None:
            return left, text, True
        text = _literal_text(left)
        if isinstance(right, exp.Column) and text is not None:
            return right, text, False
        return None

    def info_for(column: exp.Column) -> dict[str, Any] | None:
        return _resolve_column(column, aliases, referenced, columns)

    if isinstance(predicate, exp.EQ):
        pair = sided(predicate.left, predicate.right)
        if pair is None:
            return False
        info = info_for(pair[0])
        samples = (info or {}).get("sample_values") or []
        return bool(samples) and not _matches_any(pair[1], samples)

    if isinstance(predicate, exp.In):
        column = predicate.this
        values = predicate.expressions or []
        if not isinstance(column, exp.Column) or not values:
            return False  # `IN (subquery)` has no literals to check
        texts = [_literal_text(v) for v in values]
        if any(text is None for text in texts):
            return False
        info = info_for(column)
        samples = (info or {}).get("sample_values") or []
        if not samples:
            return False
        # One reachable value is enough for the list to be satisfiable.
        return not any(_matches_any(cast(str, t), samples) for t in texts)

    if isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
        pair = sided(predicate.left, predicate.right)
        if pair is None:
            return False
        column_node, text, literal_on_right = pair
        info = info_for(column_node)
        if info is None:
            return False
        # `col > lit` and `lit > col` bound the column from opposite ends.
        is_lower_bound = isinstance(predicate, (exp.GT, exp.GTE)) == literal_on_right
        if is_lower_bound:
            return _beyond(text, info.get("max_value"), above=True)
        return _beyond(text, info.get("min_value"), above=False)

    if isinstance(predicate, exp.Between):
        column = predicate.this
        if not isinstance(column, exp.Column):
            return False
        info = info_for(column)
        if info is None:
            return False
        low = _literal_text(predicate.args.get("low"))
        high = _literal_text(predicate.args.get("high"))
        return _beyond(high, info.get("min_value"), above=False) or _beyond(
            low, info.get("max_value"), above=True
        )

    return False


def _impossible_filters(
    statement: exp.Expression,
    columns: dict[tuple[str, str], dict[str, Any]],
    dialect: str,
) -> list[str]:
    """Filters the schema snapshot says cannot match a row — a mistyped
    literal, or a date range outside the data the column actually holds."""
    aliases = _alias_map(statement)
    referenced = _referenced_table_names(statement)
    flagged: list[str] = []

    for predicate in _required_predicates(statement):
        if not _contradicts_snapshot(predicate, aliases, referenced, columns):
            continue
        rendered = predicate.sql(dialect=dialect)
        if rendered not in flagged:
            flagged.append(rendered)

    return flagged


def _has_grouping_keys(statement: exp.Expression) -> bool:
    """A GROUP BY with at least one key — i.e. one row per group is the point.

    A bare aggregate over the whole table has no `exp.Group` at all, so this
    stays False for `SELECT COUNT(*) FROM t`.
    """
    for group in statement.find_all(exp.Group):
        if group.expressions or any(
            group.args.get(kind) for kind in ("grouping_sets", "cube", "rollup")
        ):
            return True
    return False


def _nullable_inner_joins(
    statement: exp.Expression, columns: dict[tuple[str, str], dict[str, Any]]
) -> list[str]:
    """Inner joins whose ON condition uses a column the snapshot says is NULLable.

    A nullable foreign key plus an INNER JOIN silently drops every row where
    the key is unset — the classic under-count that returns a clean number.
    """
    aliases = _alias_map(statement)
    flagged: list[str] = []

    for join in statement.find_all(exp.Join):
        side = (join.side or "").upper()
        kind = (join.kind or "").upper()
        if side in {"LEFT", "RIGHT", "FULL"} or kind == "CROSS":
            continue  # an outer join is the fix, not the problem

        on = join.args.get("on")
        if on is None:
            continue

        for column in on.find_all(exp.Column):
            table_name = aliases.get(column.table, column.table).lower()
            info = columns.get((table_name, column.name.lower()))
            if info is None or not info.get("nullable"):
                continue
            # A nullable PK is a contradiction; only FKs are worth reporting,
            # and only they have an obvious LEFT JOIN remedy.
            if not info.get("is_foreign_key"):
                continue
            ref = f"{table_name}.{column.name.lower()}"
            if ref not in flagged:
                flagged.append(ref)

    return flagged


def _unfiltered_soft_deletes(
    statement: exp.Expression, tables: list[dict[str, Any]]
) -> list[str]:
    """Soft-delete flags on referenced tables that the query never mentions.

    A flag with a single distinct value is skipped: filtering on a column that
    is the same for every row cannot change the answer, and reporting it would
    bury the real cases. Real schemas are full of these — the eval fixture
    alone carries an always-false `is_archived` on 35 of its 42 tables, which
    would otherwise make this check fire on very nearly every query.
    """
    used = _referenced_table_names(statement)
    mentioned = {c.name.lower() for c in statement.find_all(exp.Column)}
    flagged: list[str] = []

    for table in tables:
        if table["name"].lower() not in used:
            continue
        for column in table.get("columns", []):
            name = column["name"]
            if not _SOFT_DELETE_RE.match(name):
                continue
            if name.lower() in mentioned:
                continue
            if column.get("distinct_count") == 1:
                continue
            flagged.append(f"{table['name']}.{name}")

    return flagged


def inspect_result(
    *,
    question: str,
    sql: str,
    dialect: str,
    tables: list[dict[str, Any]],
    row_count: int,
    column_count: int,
    truncated: bool,
) -> list[Finding]:
    """Structural suspicions about a result, most actionable first.

    Never raises: a check that cannot parse the SQL simply has no opinion.
    """
    findings: list[Finding] = []
    columns = _column_index(tables)

    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except Exception:
        parsed = []  # unparseable here is the guard's problem, not ours
    # sqlglot's stubs widen `parse` to `Expr`; every walker below only needs
    # the `Expression` surface, which is what it returns at runtime.
    statement = (
        cast(exp.Expression, parsed[0]) if parsed and parsed[0] is not None else None
    )

    if row_count == 0:
        impossible = (
            _impossible_filters(statement, columns, dialect) if statement else []
        )
        if impossible:
            findings.append(
                Finding(
                    code="C_EMPTY_RESULT",
                    message=(
                        "The query ran but matched no rows, and the schema "
                        "snapshot says these filter(s) can never match: "
                        f"{'; '.join(impossible)}."
                    ),
                    hint=(
                        "Re-check those literal values against the schema's "
                        "listed column values and recorded ranges — a "
                        "mis-spelled literal or a date range outside the data "
                        "is the usual cause."
                    ),
                    retry=True,
                )
            )
        else:
            findings.append(
                Finding(
                    code="C_EMPTY_RESULT",
                    message="The query ran and matched no rows.",
                    hint=(
                        "This may be a correct answer — a filter combination "
                        "or date range with no matching data. Only re-check if "
                        "the question implies matching rows should exist."
                    ),
                    # Advisory unless a filter provably contradicts the
                    # snapshot: brought in line with `C_NULLABLE_INNER_JOIN`
                    # below, which cost four correct answers on `sales_v1` by
                    # spending a retry on a signal that is often not an error.
                    # "No rows" is frequently the true answer, and a
                    # regeneration asked to fix one reliably invents rows.
                )
            )

    if statement is None:
        return findings

    nullable = _nullable_inner_joins(statement, columns)
    if nullable:
        findings.append(
            Finding(
                code="C_NULLABLE_INNER_JOIN",
                message=(
                    "This inner-joins on nullable column(s): "
                    f"{', '.join(nullable)}."
                ),
                hint=(
                    "Rows where those columns are NULL were dropped from the "
                    "result. Use a LEFT JOIN unless the question genuinely "
                    "excludes them."
                ),
                # Advisory, on evidence. This was retry-eligible in the first
                # release and measured 0 wins / 4 losses on `sales_v1`
                # (2026-07-27, DeepSeek V4 Pro, 36% -> 30%): every time it
                # fired on a *correct* query, the regeneration obeyed it,
                # cleared the finding and returned a worse answer. Whether a
                # nullable FK should be outer-joined is a question about what
                # was asked, and this check cannot see that.
            )
        )

    soft_deleted = _unfiltered_soft_deletes(statement, tables)
    if soft_deleted:
        findings.append(
            Finding(
                code="C_SOFT_DELETE_UNFILTERED",
                message=(
                    "Table(s) with a soft-delete flag were queried without "
                    f"filtering it: {', '.join(soft_deleted)}."
                ),
                hint=(
                    "Deleted or archived rows are counted in this result. Add "
                    "the flag to the WHERE clause if the question is about "
                    "rows that currently exist."
                ),
                # Advisory on purpose. Whether a soft-delete flag *should* be
                # filtered depends on the question ("revenue by product" wants
                # discontinued products included; "how many products do we
                # sell" does not), and a check cannot tell which was meant.
                # Retries are reserved for signals that are wrong far more
                # often than they are right.
            )
        )

    # A GROUP BY with keys explains the extra rows: "how many orders per
    # region?" trips the regex and is still shaped exactly right.
    if (
        row_count > 1
        and _SINGLE_FIGURE_RE.search(question)
        and not _has_grouping_keys(statement)
    ):
        findings.append(
            Finding(
                code="C_GRANULARITY",
                message=(
                    f"The question asks for a single figure but {row_count} "
                    "rows came back."
                ),
                hint=(
                    "Nothing in the query groups the rows, so the multiplicity "
                    "is an unaggregated SELECT or a join fanning out. "
                    "Aggregate to one row."
                ),
            )
        )

    if truncated:
        findings.append(
            Finding(
                code="C_TRUNCATED",
                message=(
                    f"The result hit the row cap at {row_count} rows "
                    f"across {column_count} columns."
                ),
                hint=(
                    "Any total the reader computes from these rows is partial. "
                    "Aggregate in SQL instead of returning raw rows."
                ),
            )
        )

    return findings
