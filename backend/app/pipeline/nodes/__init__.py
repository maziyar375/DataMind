"""The nodes.

Each is `async def node(state, deps) -> NodeResult`. Nodes mutate the typed
state and report status; they never touch persistence and never decide what
happens next beyond an optional `goto`. Ordering lives in the executor.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import ConnectorError, LLMError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector
from app.domain.ports.llm import ChatMessage, LLMGateway, ResolvedLLM
from app.pipeline.contracts import SqlProposal
from app.pipeline.prompts import (
    ANSWER_SYSTEM,
    ANSWER_USER,
    CHART_SYSTEM,
    CHART_USER,
    GENERATE_SYSTEM,
    GENERATE_USER,
    REPAIR_SYSTEM,
    ROUTE_SYSTEM,
)
from app.pipeline.state import (
    ExecutionResult,
    NodeResult,
    RetrievedContext,
    RunError,
    RunState,
    SqlAttempt,
)
from app.sqlguard import GuardPolicy, guard
from app.sqlguard.validator import ValidationReport

log = get_logger(__name__)


@dataclass(slots=True)
class NodeDeps:
    llm_gateway: LLMGateway
    llm: ResolvedLLM
    connector: DatabaseConnector
    snapshot: dict[str, Any]
    history: list[dict[str, str]]
    policy: GuardPolicy
    emit: Any  # async callable(event_type: str, data: dict) -> None


# ── route ────────────────────────────────────────────────────────────────
async def route(state: RunState, deps: NodeDeps) -> NodeResult:
    """Classify before spending a schema-sized prompt on small talk."""
    started = time.perf_counter()
    try:
        completion = await deps.llm_gateway.complete(
            deps.llm,
            [
                ChatMessage(role="system", content=ROUTE_SYSTEM),
                ChatMessage(role="user", content=state.question),
            ],
        )
        state.llm_latency_ms += completion.latency_ms
        state.prompt_tokens += completion.prompt_tokens
        state.completion_tokens += completion.completion_tokens
        label = completion.text.strip().upper().split()[0] if completion.text else ""
    except LLMError:
        # A routing failure must not fail the run; assume the common case.
        label = "ANALYTICAL"

    state.intent = label if label in {
        "ANALYTICAL", "METADATA", "CHITCHAT", "UNSUPPORTED"
    } else "ANALYTICAL"

    elapsed = int((time.perf_counter() - started) * 1000)

    if state.intent == "CHITCHAT":
        state.answer = (
            "I answer questions about the data in your connected database. "
            'Ask me something like "What was total revenue last month?"'
        )
        return NodeResult(status="HALT", detail=f"Classified CHITCHAT in {elapsed}ms")

    if state.intent == "UNSUPPORTED":
        state.error = RunError(
            code="E_UNSUPPORTED",
            message="That request is outside what this connection allows.",
            hint="This connection is read-only, so I can read data but never change it.",
        )
        return NodeResult(status="FAILED", detail="Classified UNSUPPORTED")

    if state.intent == "METADATA":
        # Answered straight from the already-loaded snapshot. Routing this
        # through generate/validate would make the LLM write SQL against
        # information_schema, which the guard always rejects as a system
        # table — the run would fail before an answer ever existed.
        state.answer = _describe_schema(deps.snapshot.get("tables", []))
        return NodeResult(status="HALT", detail=f"Classified METADATA in {elapsed}ms")

    return NodeResult(detail=f"Classified {state.intent} in {elapsed}ms")


def _describe_schema(tables: list[dict[str, Any]]) -> str:
    if not tables:
        return "This connection has no tables in its current schema snapshot."
    lines = [f"You have {len(tables)} table{'' if len(tables) == 1 else 's'}:"]
    for table in tables:
        cols = ", ".join(c["name"] for c in table.get("columns", []))
        rows = table.get("approx_row_count")
        suffix = f" (~{rows:,} rows)" if rows else ""
        lines.append(f"- {table['schema']}.{table['name']}{suffix}: {cols}")
    return "\n".join(lines)


# ── retrieve ─────────────────────────────────────────────────────────────
async def retrieve(state: RunState, deps: NodeDeps) -> NodeResult:
    """Naive by design: send the whole snapshot when it fits the budget.

    Exact-name matching is the fallback. Trigram, FTS, and embeddings are
    later strategies behind the same `RetrievedContext` shape; the generator
    never learns which one produced its context.
    """
    tables = deps.snapshot.get("tables", [])
    relationships = deps.snapshot.get("relationships", [])

    approx_chars = sum(60 + 40 * len(t.get("columns", [])) for t in tables)
    budget = 24_000

    if approx_chars <= budget:
        selected, strategy = tables, "FULL_SNAPSHOT"
    else:
        needle = state.question.lower()
        selected = [
            t for t in tables
            if t["name"].lower() in needle
            or any(c["name"].lower() in needle for c in t.get("columns", []))
        ] or tables[:20]
        strategy = "EXACT_MATCH"

    names = {f"{t['schema']}.{t['name']}" for t in selected}
    state.context = RetrievedContext(
        dialect=state.dialect,
        tables=selected,
        relationships=[
            r for r in relationships
            if r["from_table"] in names or r["to_table"] in names
        ],
        history=deps.history,
        strategy=strategy,
    )
    return NodeResult(detail=f"{len(selected)} tables via {strategy}")


# ── generate ─────────────────────────────────────────────────────────────
async def generate(state: RunState, deps: NodeDeps) -> NodeResult:
    assert state.context is not None
    attempt_no = len(state.attempts) + 1
    schema_text = state.context.render()

    if attempt_no == 1:
        history_text = ""
        if state.context.history:
            turns = "\n".join(
                f"{h['role']}: {h['content'][:300]}" for h in state.context.history
            )
            history_text = f"Earlier in this conversation:\n{turns}"
        messages = [
            ChatMessage(
                role="system",
                content=GENERATE_SYSTEM.format(
                    dialect=state.dialect, schema=schema_text, history=history_text
                ),
            ),
            ChatMessage(
                role="user", content=GENERATE_USER.format(question=state.question)
            ),
        ]
    else:
        previous = state.attempts[-1]
        feedback = previous.report.to_feedback()
        if previous.db_error:
            feedback += f"\nThe database also reported: {previous.db_error}"
        messages = [
            ChatMessage(
                role="system",
                content=REPAIR_SYSTEM.format(feedback=feedback, schema=schema_text),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Question: {state.question}\n\n"
                    f"Your rejected SQL was:\n{previous.raw_sql}"
                ),
            ),
        ]

    started = time.perf_counter()
    try:
        proposal = await deps.llm_gateway.structured(deps.llm, messages, SqlProposal)
    except LLMError as err:
        state.error = RunError(
            code="E_LLM",
            message="The model could not produce a query.",
            hint=err.message,
        )
        return NodeResult(status="FAILED", detail=err.message)

    state.llm_latency_ms += int((time.perf_counter() - started) * 1000)

    state.attempts.append(
        SqlAttempt(
            attempt_no=attempt_no,
            raw_sql=proposal.sql.strip().rstrip(";"),
            report=ValidationReport(),
        )
    )
    await deps.emit(
        "SQL_GENERATED",
        {"attempt_no": attempt_no, "sql": state.attempts[-1].raw_sql},
    )
    return NodeResult(detail=f"Attempt {attempt_no} drafted")


# ── validate ─────────────────────────────────────────────────────────────
async def validate(state: RunState, deps: NodeDeps) -> NodeResult:
    attempt = state.attempts[-1]
    report, executable = guard(attempt.raw_sql, deps.policy)
    attempt.report = report
    attempt.rewritten_sql = executable

    if report.status != "VALID":
        codes = [i.rule_id for i in report.errors]
        await deps.emit(
            "SQL_REJECTED",
            {
                "attempt_no": attempt.attempt_no,
                "issues": [i.model_dump() for i in report.errors],
            },
        )
        if state.repair_count < state.max_repairs:
            return NodeResult(
                status="OK", goto="generate", detail=f"Rejected: {', '.join(codes)}"
            )
        first = report.errors[0]
        state.error = RunError(code=first.rule_id, message=first.message, hint=first.hint)
        return NodeResult(status="FAILED", detail=f"Rejected: {', '.join(codes)}")

    await deps.emit(
        "SQL_VALIDATED",
        {
            "attempt_no": attempt.attempt_no,
            "sql": executable,
            "referenced_tables": report.referenced_tables,
            "limit_applied": report.limit_applied,
        },
    )
    return NodeResult(detail=f"Valid · {len(report.referenced_tables)} tables")


# ── execute ──────────────────────────────────────────────────────────────
async def execute(state: RunState, deps: NodeDeps) -> NodeResult:
    attempt = state.attempts[-1]
    sql = attempt.rewritten_sql
    assert sql is not None

    scanned = await deps.connector.explain(sql)

    try:
        result = await deps.connector.execute(
            sql,
            max_rows=state.max_rows,
            statement_timeout_ms=state.statement_timeout_ms,
        )
    except ConnectorError as err:
        attempt.db_error = err.message
        if state.repair_count < state.max_repairs:
            return NodeResult(status="OK", goto="generate", detail=err.message)
        state.error = RunError(
            code="E_QUERY_FAILED",
            message="The query could not be run against the database.",
            hint=err.message,
        )
        return NodeResult(status="FAILED", detail=err.message)

    state.db_latency_ms += result.duration_ms
    state.execution = ExecutionResult(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        duration_ms=result.duration_ms,
        rows_scanned_estimate=scanned,
    )
    await deps.emit(
        "QUERY_COMPLETED",
        {
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
            "rows_scanned_estimate": scanned,
        },
    )
    return NodeResult(detail=f"{result.row_count} rows in {result.duration_ms}ms")


# ── present ──────────────────────────────────────────────────────────────
async def present(state: RunState, deps: NodeDeps) -> NodeResult:
    from app.pipeline.disclosure import disclose

    assert state.execution is not None
    state.disclosed = disclose(state.execution, state.disclosure_policy)

    messages = [
        ChatMessage(role="system", content=ANSWER_SYSTEM),
        ChatMessage(
            role="user",
            content=ANSWER_USER.format(
                question=state.question,
                sql=state.executable_sql or "",
                row_count=state.execution.row_count,
                result=state.disclosed.render(),
            ),
        ),
    ]

    buffer: list[str] = []
    try:
        async for delta in deps.llm_gateway.stream(deps.llm, messages):
            buffer.append(delta)
            await deps.emit("TEXT_DELTA", {"text": delta})
    except LLMError as err:
        # The data is already correct; a narration failure should not lose it.
        log.warning("answer_stream_failed", error=err.message)
        fallback = (
            f"The query returned {state.execution.row_count} rows. "
            "I could not generate a written summary for this result."
        )
        buffer = [fallback]
        await deps.emit("TEXT_DELTA", {"text": fallback})

    state.answer = "".join(buffer).strip()
    return NodeResult(detail="Answer written")


# ── chart ──────────────────────────────────────────────────────────────────
async def chart(state: RunState, deps: NodeDeps) -> NodeResult:
    """Let the model choose a chart for the result, then compile it.

    Best-effort and fail-closed for the chart alone: the answer and the table
    are already persisted, so any failure here just yields no chart. The model
    only sees the result *schema* (column names + types + row count), never the
    row data — charting never widens what the disclosure policy already allows.
    """
    from app.charts import (
        ChartIntent,
        compile_vega_lite,
        heuristic_intent,
        validate_intent,
    )

    execution = state.execution
    if execution is None or execution.row_count == 0 or len(execution.columns) < 2:
        return NodeResult(status="SKIPPED", detail="Nothing chartable")

    columns = "\n".join(
        f"- {c.name} ({c.semantic_type})" for c in execution.columns
    )

    # Let the model choose first. It is best-effort: a provider error, or a
    # model that cannot emit a valid nested ChartIntent (common with small
    # models), falls through to a deterministic choice from the data shape so a
    # chart still appears and still varies question to question.
    intent: ChartIntent | None = None
    source = "model"
    try:
        intent = await deps.llm_gateway.structured(
            deps.llm,
            [
                ChatMessage(role="system", content=CHART_SYSTEM),
                ChatMessage(
                    role="user",
                    content=CHART_USER.format(
                        question=state.question,
                        row_count=execution.row_count,
                        columns=columns,
                    ),
                ),
            ],
            ChartIntent,
        )
    except LLMError as err:
        log.warning("chart_intent_failed", run_id=str(state.run_id), error=err.message)

    if intent is not None and validate_intent(intent, execution.columns)[0]:
        pass  # the model's choice is usable
    else:
        intent = heuristic_intent(execution.columns, execution.row_count)
        source = "heuristic"

    if intent is None or not validate_intent(intent, execution.columns)[0]:
        return NodeResult(status="SKIPPED", detail="No chart fits this result")

    state.chart = compile_vega_lite(intent, execution.columns, execution.rows)
    await deps.emit(
        "ARTIFACT_CREATED",
        {"kind": "CHART", "chart_type": intent.chart_type, "source": source},
    )
    return NodeResult(detail=f"{intent.chart_type} chart ({source})")
