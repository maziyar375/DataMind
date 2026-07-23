"""Deterministic SQL safety layer. Nothing reaches a driver without passing here."""
from app.sqlguard.policy import GuardPolicy
from app.sqlguard.rewriter import render
from app.sqlguard.validator import SqlValidator, ValidationIssue, ValidationReport

__all__ = [
    "GuardPolicy", "SqlValidator", "ValidationReport", "ValidationIssue",
    "render", "guard",
]


def guard(sql: str, policy: GuardPolicy) -> tuple[ValidationReport, str | None]:
    """Validate then rewrite. Returns (report, executable_sql_or_None)."""
    validator = SqlValidator(policy)
    report, tree = validator.validate(sql)
    if report.status != "VALID" or tree is None:
        return report, None
    rewritten, limit = render(tree, policy.dialect, policy.max_rows)
    report.limit_applied = limit
    return report, rewritten
