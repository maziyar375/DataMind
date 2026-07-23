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
from app.sqlguard.validator import ValidationReport


class RetrievedContext(BaseModel):
    """Everything the generator is allowed to see about the schema."""

    dialect: str
    tables: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, str]] = Field(default_factory=list)
    strategy: Literal["FULL_SNAPSHOT", "EXACT_MATCH", "TRIGRAM"] = "FULL_SNAPSHOT"

    def render(self) -> str:
        lines = [f"Dialect: {self.dialect}", "", "Tables:"]
        for table in self.tables:
            cols = ", ".join(
                f"{c['name']} {c['data_type']}"
                + ("" if not c.get("is_primary_key") else " PK")
                + ("" if not c.get("is_foreign_key") else f" FK->{c.get('references')}")
                for c in table.get("columns", [])
            )
            rows = table.get("approx_row_count")
            suffix = f"  (~{rows:,} rows)" if rows else ""
            lines.append(f"- {table['schema']}.{table['name']}({cols}){suffix}")
        if self.relationships:
            lines.append("")
            lines.append("Foreign keys:")
            for rel in self.relationships:
                lines.append(
                    f"- {rel['from_table']}.{rel['from_column']} -> "
                    f"{rel['to_table']}.{rel['to_column']}"
                )
        return "\n".join(lines)


class SqlAttempt(BaseModel):
    attempt_no: int
    raw_sql: str
    rewritten_sql: str | None = None
    report: ValidationReport
    db_error: str | None = None


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
    disclosed: DisclosedResult | None = None
    chart: dict[str, Any] | None = None
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
