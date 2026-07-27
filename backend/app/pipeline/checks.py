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
`C_EMPTY_RESULT` alone — the one case where the model produced no answer at
all, so there is nothing to lose by asking again.

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

    if row_count == 0:
        findings.append(
            Finding(
                code="C_EMPTY_RESULT",
                message="The query ran but matched no rows.",
                hint=(
                    "Re-check literal values against the schema's listed column "
                    "values, widen the date range, or relax a filter. If the "
                    "question implies rows should exist, an inner join or a "
                    "mis-spelled literal is the usual cause."
                ),
                retry=True,
            )
        )

    try:
        parsed = sqlglot.parse(sql, read=dialect)
    except Exception:
        return findings  # unparseable here is the guard's problem, not ours
    if not parsed or parsed[0] is None:
        return findings
    # sqlglot's stubs widen `parse` to `Expr`; every walker below only needs
    # the `Expression` surface, which is what it returns at runtime.
    statement = cast(exp.Expression, parsed[0])

    columns = _column_index(tables)

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

    if row_count > 1 and _SINGLE_FIGURE_RE.search(question):
        findings.append(
            Finding(
                code="C_GRANULARITY",
                message=(
                    f"The question asks for a single figure but {row_count} "
                    "rows came back."
                ),
                hint="Aggregate to one row, or drop the GROUP BY.",
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
