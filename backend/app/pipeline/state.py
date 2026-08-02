"""Typed run state.

Node signatures are deliberately LangGraph-shaped — `async def node(state) ->
NodeResult` over a single typed state object. If the graph ever needs durable
interrupts or parallel fan-out, adopting LangGraph is a wiring change here,
not a rewrite of the nodes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.ports.database import ResultColumn
from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.pipeline.checks import Finding
from app.sqlguard.validator import ValidationReport

# Types whose min/max is temporal rather than numeric. Kept here rather than
# imported from a connector so the pipeline stays engine-agnostic.
_TEMPORAL_HINT_TYPES = ("date", "time", "timestamp")


def _render_hints(column: dict[str, Any], budget: HintBudget) -> str:
    """The suffix describing a column's *contents*, clipped to the budget.

    Everything here is customer data, so every branch is gated. The values
    themselves were already filtered at capture time — this is the second
    gate, the one that responds to a policy change without a re-sync.
    """
    parts: list[str] = []
    data_type = str(column.get("data_type", "")).lower()
    is_temporal = any(t in data_type for t in _TEMPORAL_HINT_TYPES)

    values = column.get("sample_values") or []
    if budget.value_lists and values:
        shown = list(values)[: budget.max_values]
        listed = ", ".join(shown)
        more = "" if len(shown) == len(values) else ", …"
        parts.append(f"∈ {{{listed}{more}}}")
    elif budget.stats and column.get("distinct_count") is not None:
        # Naming the cardinality without naming a value is what AGGREGATE
        # buys: enough for the model to treat a column as categorical and
        # GROUP BY it instead of inventing a literal to filter on.
        parts.append(f"{column['distinct_count']} distinct")

    if budget.stats:
        null_fraction = column.get("null_fraction")
        # Only worth the tokens when it changes the join the model writes.
        if null_fraction is not None and null_fraction >= 0.05:
            parts.append(f"{round(null_fraction * 100)}% null")

    ranged = budget.temporal_range if is_temporal else budget.numeric_range
    if ranged and column.get("min_value") and column.get("max_value"):
        parts.append(f"{column['min_value']}…{column['max_value']}")

    return f" [{'; '.join(parts)}]" if parts else ""


class RetrievedContext(BaseModel):
    """Everything the generator is allowed to see about the schema."""

    dialect: str
    tables: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    strategy: Literal["FULL_SNAPSHOT", "EXACT_MATCH", "TRIGRAM"] = "FULL_SNAPSHOT"
    # The connection's semantic layer, serialised. None when the connection
    # has none or has switched it off — in which case `render` emits exactly
    # the bytes it emitted before this field existed, so the eval baseline
    # stays comparable.
    semantic: dict[str, Any] | None = None

    def render(self, policy: str = DisclosurePolicy.NONE) -> str:
        """The schema block as the model sees it, for the policy in force.

        `policy` defaults to NONE so a caller that forgets to pass one emits
        structure only — a missing argument can never widen a disclosure.
        """
        budget = HintBudget.from_policy(policy)
        hinted = False
        body: list[str] = []
        for table in self.tables:
            rendered: list[str] = []
            for c in table.get("columns", []):
                hint = _render_hints(c, budget)
                hinted = hinted or bool(hint)
                rendered.append(
                    f"{c['name']} {c['data_type']}"
                    + ("" if not c.get("is_primary_key") else " PK")
                    + ("" if not c.get("is_foreign_key") else f" FK->{c.get('references')}")
                    + hint
                )
            rows = table.get("approx_row_count")
            suffix = f"  (~{rows:,} rows)" if rows and budget.row_counts else ""
            body.append(
                f"- {table['schema']}.{table['name']}({', '.join(rendered)}){suffix}"
            )

        lines = [f"Dialect: {self.dialect}"]
        if hinted:
            # The legend lives in the schema block, not in GENERATE_SYSTEM, so
            # that a run with no hints produces a prompt byte-identical to the
            # one the current baseline was measured on. Eval Round 2 showed
            # this prompt is sensitive to unconditional additions.
            lines.append(
                "A [bracket] after a column describes its contents: ∈ {…} lists "
                "every value the column takes, so filter using exactly these; "
                "N distinct is the value count; N% null warns that an inner "
                "join on the column drops rows; a…b is the observed range."
            )
        lines += ["", "Tables:", *body]
        if self.relationships:
            lines.append("")
            lines.append("Foreign keys:")
            for rel in self.relationships:
                lines.append(
                    f"- {rel['from_table']}.{rel['from_column']} -> "
                    f"{rel['to_table']}.{rel['to_column']}"
                )

        meaning = self._render_semantic(budget)
        if meaning:
            lines += ["", meaning]
        return "\n".join(lines)

    def _render_semantic(self, budget: HintBudget) -> str:
        """The semantic layer, scoped to the tables that survived retrieval.

        Deliberately last in the block: the structure is what the model must
        not get wrong, and it stays where it has always been. Import is local
        so `app.pipeline` does not pay for `app.semantic` on a run whose
        connection has no layer.
        """
        if not self.semantic:
            return ""
        from app.semantic import SemanticDocument, render_semantic

        try:
            doc = SemanticDocument.model_validate(self.semantic)
        except ValueError:
            return ""
        return render_semantic(
            doc,
            tables=[f"{t['schema']}.{t['name']}" for t in self.tables],
            budget=budget,
        )


class SqlAttempt(BaseModel):
    attempt_no: int
    raw_sql: str
    rewritten_sql: str | None = None
    report: ValidationReport
    db_error: str | None = None
    # Structural suspicions raised after this attempt actually ran. The third
    # repair signal, alongside `report` (guard said no) and `db_error` (the
    # database said no).
    findings: list[Finding] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    rows_scanned_estimate: int | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class DisclosedResult(BaseModel):
    """The subset of result data that the disclosure policy permits to leave."""

    policy: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    note: str = ""

    def render(self) -> str:
        if self.policy == "NONE":
            return "(Result data was not shared with the model by policy.)"
        header = " | ".join(self.columns)
        body = "\n".join(" | ".join(str(v) for v in row) for row in self.rows)
        return f"{header}\n{body}\n{self.note}".strip()


class ClarificationRequest(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)


class RunError(BaseModel):
    code: str
    message: str
    hint: str | None = None


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    run_id: UUID
    conversation_id: UUID
    owner_id: UUID
    connection_id: UUID
    question: str
    dialect: str = "postgres"
    max_rows: int = 1000
    max_repairs: int = 1
    statement_timeout_ms: int = 30_000
    disclosure_policy: str = "SAMPLE"
    deadline_at: datetime

    intent: Literal["ANALYTICAL", "METADATA", "CHITCHAT", "UNSUPPORTED"] | None = None
    clarification: ClarificationRequest | None = None
    context: RetrievedContext | None = None
    attempts: list[SqlAttempt] = Field(default_factory=list)
    execution: ExecutionResult | None = None
    # A result that ran cleanly but looked suspect, kept while a check-driven
    # retry is in flight. It is what makes the retry safe: if the second
    # attempt fails the guard or the database, the run falls back to this
    # instead of failing outright, so a check can never cost a working answer.
    superseded_execution: ExecutionResult | None = None
    check_repair_used: bool = False
    disclosed: DisclosedResult | None = None
    chart: dict[str, Any] | None = None
    # A serialised `KpiSpec`. Mutually exclusive with `chart` by construction:
    # the `chart` node reaches for a big number only where a chart was vetoed,
    # so a turn never carries both.
    kpi: dict[str, Any] | None = None
    answer: str | None = None
    error: RunError | None = None

    llm_latency_ms: int = 0
    db_latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def repair_count(self) -> int:
        return max(0, len(self.attempts) - 1)

    @property
    def last_attempt(self) -> SqlAttempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def executable_sql(self) -> str | None:
        last = self.last_attempt
        return last.rewritten_sql if last else None


class NodeResult(BaseModel):
    """What a node reports back to the executor."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["OK", "SKIPPED", "HALT", "FAILED"] = "OK"
    detail: str | None = None
    goto: str | None = None
