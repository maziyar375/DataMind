"""AST-based validation. The model proposes; this module disposes.

Design notes
------------
* Validation is a *whitelist walk*, never a blacklist of bad strings. String
  blacklists are a secondary defence only, applied to the raw text before the
  parse to catch things that never should have been sent.
* Every rejection carries a stable `rule_id` so the frontend and the repair
  prompt can both branch on it without parsing English.
* The report records `referenced_tables` and `referenced_columns` because the
  chat UI renders them as metadata chips.
"""
from __future__ import annotations

from typing import Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

from app.sqlguard.policy import (
    ALLOWED_FUNCTIONS,
    ALLOWED_NODES,
    FORBIDDEN_IDENTIFIER_PREFIXES,
    FORBIDDEN_SUBSTRINGS,
    GuardPolicy,
)


class ValidationIssue(BaseModel):
    rule_id: str
    severity: Literal["ERROR", "WARNING"] = "ERROR"
    message: str
    hint: str | None = None
    node_sql: str | None = None


class ValidationReport(BaseModel):
    status: Literal["VALID", "REJECTED"] = "REJECTED"
    issues: list[ValidationIssue] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)
    limit_applied: int | None = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    def to_feedback(self) -> str:
        """Compact, deterministic text handed back to the model on repair."""
        if not self.errors:
            return "The query was rejected but no specific issue was recorded."
        lines = ["The SQL you produced was rejected. Fix these problems:"]
        for issue in self.errors:
            line = f"- [{issue.rule_id}] {issue.message}"
            if issue.hint:
                line += f" Hint: {issue.hint}"
            lines.append(line)
        lines.append("Return a corrected single SELECT statement.")
        return "\n".join(lines)


class SqlValidator:
    def __init__(self, policy: GuardPolicy) -> None:
        self._policy = policy

    # ── public API ───────────────────────────────────────────────────────
    def validate(self, sql: str) -> tuple[ValidationReport, exp.Expression | None]:
        report = ValidationReport()

        text_issue = self._scan_raw_text(sql)
        if text_issue is not None:
            report.issues.append(text_issue)
            return report, None

        try:
            statements = sqlglot.parse(sql, read=self._policy.dialect)
        except ParseError as err:
            report.issues.append(
                ValidationIssue(
                    rule_id="E_PARSE",
                    message=f"The statement could not be parsed: {err}",
                    hint="Emit one syntactically valid SELECT statement.",
                )
            )
            return report, None

        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            report.issues.append(
                ValidationIssue(
                    rule_id="E_MULTI_STATEMENT",
                    message=f"Expected exactly one statement, found {len(statements)}.",
                    hint="Do not use semicolons to chain statements.",
                )
            )
            return report, None

        tree = statements[0]
        if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            report.issues.append(
                ValidationIssue(
                    rule_id="E_NOT_A_SELECT",
                    message=f"Only SELECT is permitted; got {type(tree).__name__.upper()}.",
                    hint="This connection is read-only. Read data, never modify it.",
                    node_sql=tree.sql(dialect=self._policy.dialect)[:200],
                )
            )
            return report, None

        self._walk_nodes(tree, report)
        self._resolve_tables_and_columns(tree, report)
        self._check_shape(tree, report)

        report.status = "REJECTED" if report.errors else "VALID"
        return report, (tree if report.status == "VALID" else None)

    # ── stage 1: raw text screen ─────────────────────────────────────────
    def _scan_raw_text(self, sql: str) -> ValidationIssue | None:
        lowered = sql.lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle in lowered:
                return ValidationIssue(
                    rule_id="E_FORBIDDEN_CONSTRUCT",
                    message=f"The statement contains a forbidden construct: {needle.strip()!r}.",
                    hint="Query only the business tables in the provided schema.",
                )
        if "--" in sql or "/*" in sql:
            return ValidationIssue(
                rule_id="E_COMMENT_NOT_ALLOWED",
                message="Comments are not permitted in generated SQL.",
                hint="Return the statement without comments.",
            )
        return None

    # ── stage 2: node allowlist ──────────────────────────────────────────
    def _walk_nodes(self, tree: exp.Expression, report: ValidationReport) -> None:
        seen_unknown: set[str] = set()
        for node in tree.walk():
            node_type = type(node)
            if node_type not in ALLOWED_NODES:
                name = node_type.__name__
                if name not in seen_unknown:
                    seen_unknown.add(name)
                    report.issues.append(
                        ValidationIssue(
                            rule_id="E_NODE_NOT_ALLOWED",
                            message=f"SQL construct {name!r} is not permitted.",
                            hint="Use plain SELECT ... FROM ... JOIN ... WHERE ... GROUP BY.",
                            node_sql=_safe_sql(node, self._policy.dialect),
                        )
                    )
            if isinstance(node, exp.Anonymous):
                fn = (node.name or "").lower()
                if fn not in ALLOWED_FUNCTIONS:
                    report.issues.append(
                        ValidationIssue(
                            rule_id="E_FUNCTION_NOT_ALLOWED",
                            message=f"Function {fn or '<unnamed>'}() is not on the allowlist.",
                            hint="Use standard aggregate and date functions only.",
                            node_sql=_safe_sql(node, self._policy.dialect),
                        )
                    )

    # ── stage 3: name resolution against the schema snapshot ─────────────
    def _resolve_tables_and_columns(
        self, tree: exp.Expression, report: ValidationReport
    ) -> None:
        cte_names = {
            cte.alias_or_name.lower()
            for cte in tree.find_all(exp.CTE)
            if cte.alias_or_name
        }
        alias_to_table: dict[str, str] = {}
        resolved: set[str] = set()

        for table in tree.find_all(exp.Table):
            bare = (table.name or "").lower()
            if not bare or bare in cte_names:
                continue

            db = (table.db or "").lower()
            qualified = f"{db}.{bare}" if db else None

            if qualified is None:
                qualified = self._policy.resolve_unqualified(bare)
                if qualified is None:
                    report.issues.append(
                        ValidationIssue(
                            rule_id="E_TABLE_NOT_ALLOWED",
                            message=f"Table {bare!r} is not present in this connection's schema.",
                            hint="Use only tables listed in the schema you were given.",
                            node_sql=_safe_sql(table, self._policy.dialect),
                        )
                    )
                    continue

            for prefix in FORBIDDEN_IDENTIFIER_PREFIXES:
                if qualified.startswith(prefix) or bare.startswith(prefix):
                    report.issues.append(
                        ValidationIssue(
                            rule_id="E_SYSTEM_TABLE",
                            message=f"System catalog {qualified!r} may not be queried.",
                        )
                    )
                    break

            if not self._policy.table_known(qualified):
                report.issues.append(
                    ValidationIssue(
                        rule_id="E_TABLE_NOT_ALLOWED",
                        message=f"Table {qualified!r} is not allowed on this connection.",
                        hint="Use only tables listed in the schema you were given.",
                        node_sql=_safe_sql(table, self._policy.dialect),
                    )
                )
                continue

            resolved.add(qualified)
            alias_to_table[bare] = qualified
            if table.alias:
                alias_to_table[table.alias.lower()] = qualified

        report.referenced_tables = sorted(resolved)
        output_aliases = _collect_output_aliases(tree)
        self._resolve_columns(
            tree, alias_to_table, cte_names, resolved, output_aliases, report
        )

    def _resolve_columns(
        self,
        tree: exp.Expression,
        alias_to_table: dict[str, str],
        cte_names: set[str],
        resolved_tables: set[str],
        output_aliases: set[str],
        report: ValidationReport,
    ) -> None:
        if cte_names:
            # Column provenance through CTEs needs a full scope resolver; the
            # MVP records columns without asserting them rather than guessing.
            report.issues.append(
                ValidationIssue(
                    rule_id="W_CTE_COLUMNS_UNVERIFIED",
                    severity="WARNING",
                    message="Column names inside CTEs were not verified against the schema.",
                )
            )

        referenced: set[str] = set()
        for column in tree.find_all(exp.Column):
            col_name = (column.name or "").lower()
            if not col_name or col_name == "*":
                continue
            table_ref = (column.table or "").lower()

            # `SELECT SUM(x) AS revenue ... ORDER BY revenue` — an unqualified
            # reference to a select-list alias is not a table column.
            if not table_ref and col_name in output_aliases:
                continue

            if table_ref:
                if table_ref in cte_names:
                    continue
                owner = alias_to_table.get(table_ref)
                if owner is None:
                    report.issues.append(
                        ValidationIssue(
                            rule_id="E_UNKNOWN_ALIAS",
                            message=f"Alias {table_ref!r} is not bound to any table in the FROM clause.",
                            node_sql=_safe_sql(column, self._policy.dialect),
                        )
                    )
                    continue
                candidates = [owner]
            else:
                candidates = sorted(resolved_tables)

            if cte_names:
                continue

            if not any(self._policy.column_known(t, col_name) for t in candidates):
                where = candidates[0] if len(candidates) == 1 else "any referenced table"
                report.issues.append(
                    ValidationIssue(
                        rule_id="E_UNKNOWN_COLUMN",
                        message=f"Column {col_name!r} does not exist on {where}.",
                        hint="Check the column list in the schema you were given.",
                        node_sql=_safe_sql(column, self._policy.dialect),
                    )
                )
                continue

            for table in candidates:
                if self._policy.column_known(table, col_name):
                    referenced.add(f"{table}.{col_name}")
                    break

        report.referenced_columns = sorted(referenced)

    # ── stage 4: shape limits ────────────────────────────────────────────
    def _check_shape(self, tree: exp.Expression, report: ValidationReport) -> None:
        join_count = len(list(tree.find_all(exp.Join)))
        if join_count > self._policy.max_joins:
            report.issues.append(
                ValidationIssue(
                    rule_id="E_TOO_MANY_JOINS",
                    message=f"{join_count} joins exceeds the limit of {self._policy.max_joins}.",
                )
            )

        depth = _max_subquery_depth(tree)
        if depth > self._policy.max_subquery_depth:
            report.issues.append(
                ValidationIssue(
                    rule_id="E_SUBQUERY_TOO_DEEP",
                    message=f"Subquery nesting of {depth} exceeds the limit of "
                    f"{self._policy.max_subquery_depth}.",
                )
            )

        if not self._policy.allow_star:
            for star in tree.find_all(exp.Star):
                report.issues.append(
                    ValidationIssue(
                        rule_id="E_STAR_NOT_ALLOWED",
                        message="SELECT * is not permitted on this connection.",
                        hint="List the columns you need explicitly.",
                        node_sql=_safe_sql(star, self._policy.dialect),
                    )
                )
                break


def _collect_output_aliases(tree: exp.Expression) -> set[str]:
    """Names introduced by the SELECT list, which GROUP BY / ORDER BY may use."""
    aliases: set[str] = set()
    for select in tree.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Alias) and projection.alias:
                aliases.add(projection.alias.lower())
    return aliases


def _safe_sql(node: exp.Expression, dialect: str) -> str | None:
    try:
        return node.sql(dialect=dialect)[:200]
    except Exception:  # pragma: no cover - generation is best-effort only
        return None


def _max_subquery_depth(node: exp.Expression, current: int = 0) -> int:
    deepest = current
    for child in node.args.values():
        children = child if isinstance(child, list) else [child]
        for item in children:
            if not isinstance(item, exp.Expression):
                continue
            nxt = current + 1 if isinstance(item, (exp.Subquery, exp.Select)) else current
            deepest = max(deepest, _max_subquery_depth(item, nxt))
    return deepest
