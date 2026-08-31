"""The guard's fifth entry point. A stored template gets no exemption.

DataMind has four guarded doors — the pipeline's `validate` node, a dashboard
tile, a report block, an imported dashboard — and each replays the hostile
corpus in a test of its own. None is privileged, because *the moment one door
is special, the guarantee is gone.* This is the fifth, and
`tests/unit/test_knowledge_guard.py` replays the same corpus through it.

**Two validations, answering different questions** (plan §5.1):

*On save* — is this legal at all, against the current snapshot? A failure
rejects the save and shows the guard's message verbatim.

*On every use* — is it **still** legal against the schema as it is **now**? A
failure marks the template `STALE`, withdraws it from matching, and lets the
run fall through to generation. It never fails the run and never silently
deletes the row.

The second row is the codebase's fifth failure posture: a stale template
**fails as a value**. It mirrors the semantic layer's rule — an invalid
*generated* entry is dropped, an invalid *human-written* one is flagged and
kept, because deleting a person's work to hide drift is worse than showing it.

**Nothing here returns executable SQL.** `guard()` hands back a rewritten
statement, and for a parameterized template that statement contains a driver's
binding syntax for the placeholders — something no caller should ever run. So
this module returns a verdict and the tables it touched, and Phase 2 binds the
slots to literals *before* asking the guard for something to execute.
"""
from __future__ import annotations

from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import expressions as exp

from app.knowledge.models import KnowledgeTemplate, TemplateParam
from app.knowledge.params import placeholder
from app.sqlguard import GuardPolicy, guard
from app.sqlguard.validator import ValidationIssue, ValidationReport

#: Guard rejections which, for SQL that was valid when it was saved, mean the
#: schema moved underneath the template rather than that it was ever wrong.
#: Same set `query_service` uses for a tile, and for the same reason: the fix
#: is "re-sync, then edit the SQL", not "this query is not allowed".
DRIFT_RULES = frozenset({"E_TABLE_NOT_ALLOWED", "E_UNKNOWN_COLUMN"})


class TemplateVerdict(BaseModel):
    """What the guard said about one template, in the shape the UI renders."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    valid: bool = False
    report: ValidationReport = Field(default_factory=ValidationReport)
    referenced_tables: list[str] = Field(default_factory=list)
    #: Slots the SQL uses. The editor compares these with what the question
    #: declares, because a `:region` the question never says is a slot that can
    #: never bind.
    placeholders: list[str] = Field(default_factory=list)
    #: True when the rejection is schema drift rather than an illegal query.
    drifted: bool = False

    @property
    def message(self) -> str:
        """The guard's own first message, verbatim.

        Rewriting it into something friendlier loses the reason, and the
        guard's language is already precise.
        """
        errors = self.report.errors
        return errors[0].message if errors else ""


def declared_placeholders(sql: str) -> list[str]:
    """Every `:name` the statement uses, in order, without duplicates.

    Read off the parse rather than off a regex: `':not_a_param'` inside a
    string literal is text, and a regex would declare a parameter that can
    never bind.
    """
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return []

    found: list[str] = []
    for node in tree.walk(bfs=False):
        name = ""
        if isinstance(node, exp.Placeholder) and node.this:
            name = str(node.this)
        elif isinstance(node, exp.Var) and str(node.this or "").startswith(":"):
            name = str(node.this)[1:]
        if name and name not in found:
            found.append(name)
    return found


def validate_sql(sql: str, policy: GuardPolicy) -> TemplateVerdict:
    """One statement, through the same `guard()` every other door uses.

    Parameterized SQL parses: `:region` is an `exp.Placeholder`, which has been
    on the allowlist since the guard was written. So the template needs no
    special parse, no pre-substitution and no exemption — which is the whole
    reason this door is cheap to make safe.
    """
    report, _executable = guard(sql or "", policy)
    valid = report.status == "VALID"
    codes = {issue.rule_id for issue in report.errors}
    return TemplateVerdict(
        valid=valid,
        report=report,
        referenced_tables=list(report.referenced_tables),
        placeholders=declared_placeholders(sql or ""),
        drifted=bool(codes & DRIFT_RULES),
    )


def validate_template(
    template: KnowledgeTemplate, policy: GuardPolicy
) -> TemplateVerdict:
    """The save-time check, plus the two consistency rules the guard cannot see.

    The guard answers "is this SQL legal". It cannot answer "does this template
    hold together", and two ways of not holding together produce a slot that
    silently never binds:

    * a declared parameter the SQL never uses;
    * a `:placeholder` in the SQL that is not declared.

    Both are reported as `E_PARAM_MISMATCH` so the editor can render them the
    same way it renders a guard rejection, with a stable code rather than
    parsed English.
    """
    verdict = validate_sql(template.sql, policy)
    declared = {p.name for p in template.params}
    used = set(verdict.placeholders)

    for issue in _param_issues(declared, used):
        verdict.report.issues.append(issue)

    if verdict.report.errors:
        verdict.report.status = "REJECTED"
        verdict.valid = False
    return verdict


def _param_issues(declared: set[str], used: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for name in sorted(declared - used):
        issues.append(ValidationIssue(
            rule_id="E_PARAM_MISMATCH",
            message=f"The parameter :{name} is declared but never used in the SQL.",
            hint="Use it in the statement, or remove it from the parameter list.",
        ))
    for name in sorted(used - declared):
        issues.append(ValidationIssue(
            rule_id="E_PARAM_MISMATCH",
            message=f"The SQL uses {placeholder(name)}, which is not declared.",
            hint="Declare it as a parameter, or replace it with a literal.",
        ))
    return issues


def policy_from_tables(
    tables: list[dict[str, Any]], *, dialect: str, max_rows: int = 1000
) -> GuardPolicy:
    """A guard policy from a raw snapshot document.

    A local builder rather than `query_service.policy_from_snapshot`: that one
    takes an ORM row, and this package may not import `app.services`. It reads
    the same two facts off the same document, and `test_knowledge_guard.py`
    pins that the two agree on the corpus.
    """
    allowed_tables: set[str] = set()
    allowed_columns: dict[str, set[str]] = {}
    for table in tables or []:
        qualified = f"{table.get('schema', '')}.{table.get('name', '')}".lower()
        allowed_tables.add(qualified)
        allowed_columns[qualified] = {
            str(c["name"]).lower() for c in table.get("columns", []) if c.get("name")
        }
    return GuardPolicy(
        dialect=dialect,
        max_rows=max_rows,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
    )


def describe_params(params: list[TemplateParam]) -> str:
    """`region=EMEA, year=2026`-style summary, for a log line or a badge."""
    return ", ".join(f"{p.name}:{p.type}" for p in params)
