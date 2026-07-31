"""The free result checks, and the guarantee that they cannot make a run worse.

Each check exists because the fixture plants the trap it catches: nullable
foreign keys, a soft-delete flag on ~8% of customers, and questions phrased
for a single figure. All of them return a *successful* query with a wrong
answer, which is precisely what the guard and the database cannot see.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.pipeline.checks import inspect_result
from app.pipeline.state import ExecutionResult, RunState, SqlAttempt
from app.sqlguard.validator import ValidationReport

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "customer_id", "data_type": "bigint", "nullable": False,
             "is_foreign_key": True},
            {"name": "employee_id", "data_type": "bigint", "nullable": True,
             "is_foreign_key": True},
            {"name": "total_amount", "data_type": "numeric"},
            # `sample_values` is a *complete* domain by the connector contract
            # (infra/connectors/hints.py), which is what makes "not in this
            # list" evidence rather than a guess.
            {"name": "status", "data_type": "text",
             "sample_values": ["shipped", "Pending", "cancelled"]},
            {"name": "ordered_at", "data_type": "date",
             "min_value": "2024-01-01", "max_value": "2025-12-31"},
        ],
    },
    {
        "schema": "public",
        "name": "customers",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "is_deleted", "data_type": "boolean"},
        ],
    },
    {
        "schema": "public",
        "name": "employees",
        "columns": [{"name": "id", "data_type": "bigint", "is_primary_key": True}],
    },
]


def check(sql: str, *, question: str = "Revenue by employee", rows: int = 5,
          truncated: bool = False) -> set[str]:
    findings = inspect_result(
        question=question, sql=sql, dialect="postgres", tables=TABLES,
        row_count=rows, column_count=2, truncated=truncated,
    )
    return {f.code for f in findings}


def test_clean_query_raises_nothing() -> None:
    sql = (
        "SELECT e.id, SUM(o.total_amount) FROM public.orders o "
        "LEFT JOIN public.employees e ON e.id = o.employee_id GROUP BY e.id"
    )
    assert check(sql) == set()


def test_empty_result_is_flagged() -> None:
    assert "C_EMPTY_RESULT" in check("SELECT id FROM public.orders", rows=0)


def _empty(sql: str) -> Any:
    findings = inspect_result(
        question="How many orders?", sql=sql, dialect="postgres", tables=TABLES,
        row_count=0, column_count=1, truncated=False,
    )
    return next(f for f in findings if f.code == "C_EMPTY_RESULT")


def test_empty_result_retries_on_a_literal_the_column_cannot_hold() -> None:
    """`sample_values` is the column's complete domain, so a literal outside
    it is a mis-spelling, not an answer — the one case worth a regeneration."""
    flag = _empty("SELECT id FROM public.orders WHERE status = 'delivered'")
    assert flag.retry is True
    assert "delivered" in flag.message


@pytest.mark.parametrize(
    "sql",
    [
        # Literal present in the domain, only cased differently.
        "SELECT id FROM public.orders WHERE status = 'PENDING'",
        # One reachable value is enough for an IN list to be satisfiable.
        "SELECT id FROM public.orders WHERE status IN ('delivered', 'shipped')",
        # A date range squarely inside the recorded bounds.
        "SELECT id FROM public.orders WHERE ordered_at >= '2024-06-01'",
        # Under an OR, an unmatchable branch proves nothing.
        "SELECT id FROM public.orders "
        "WHERE status = 'delivered' OR status = 'shipped'",
        # No statistics on the column at all.
        "SELECT id FROM public.orders WHERE total_amount = 42",
        # No filter to be wrong about.
        "SELECT id FROM public.orders",
    ],
)
def test_empty_result_without_evidence_is_advisory_only(sql: str) -> None:
    """Zero rows is frequently the true answer. Retrying on it alone repeats
    the `C_NULLABLE_INNER_JOIN` regression: the model obeys the finding and
    invents rows that were correctly absent."""
    flag = _empty(sql)
    assert flag.retry is False
    assert "may be a correct answer" in flag.hint


def test_empty_result_retries_on_a_range_outside_the_recorded_bounds() -> None:
    sql = "SELECT id FROM public.orders WHERE ordered_at > DATE '2026-05-01'"
    assert _empty(sql).retry is True


def test_empty_result_retries_on_a_between_outside_the_recorded_bounds() -> None:
    sql = (
        "SELECT id FROM public.orders "
        "WHERE ordered_at BETWEEN '2019-01-01' AND '2019-12-31'"
    )
    assert _empty(sql).retry is True


def test_empty_result_evidence_is_found_through_a_join_condition() -> None:
    """ON is as much a filter as WHERE, and the check reads both."""
    sql = (
        "SELECT c.id FROM public.customers c JOIN public.orders o "
        "ON o.customer_id = c.id AND o.status = 'delivered'"
    )
    assert _empty(sql).retry is True


def test_unparseable_empty_result_is_advisory_not_retryable() -> None:
    """No AST means no evidence, and no evidence means no retry."""
    assert _empty("SELECT FROM WHERE ((").retry is False


def test_inner_join_on_nullable_fk() -> None:
    sql = (
        "SELECT e.id, SUM(o.total_amount) FROM public.orders o "
        "JOIN public.employees e ON e.id = o.employee_id GROUP BY e.id"
    )
    assert "C_NULLABLE_INNER_JOIN" in check(sql)


def test_nullable_inner_join_is_advisory_not_retryable() -> None:
    """It shipped retry-eligible and measured 0 wins / 4 losses on sales_v1:
    the regeneration obeyed it, cleared the finding, and answered a different
    question. Whether a nullable FK wants an outer join is about intent."""
    sql = (
        "SELECT e.id, SUM(o.total_amount) FROM public.orders o "
        "JOIN public.employees e ON e.id = o.employee_id GROUP BY e.id"
    )
    findings = inspect_result(
        question="Revenue by employee", sql=sql, dialect="postgres",
        tables=TABLES, row_count=5, column_count=2, truncated=False,
    )
    flag = next(f for f in findings if f.code == "C_NULLABLE_INNER_JOIN")
    assert flag.retry is False


def test_only_empty_result_may_spend_a_retry() -> None:
    """The whole retry surface, asserted in one place so widening it is a
    deliberate act with a test to change, not an accident."""
    from app.pipeline import checks

    sql = (
        "SELECT c.id FROM public.customers c "
        "JOIN public.orders o ON o.customer_id = c.id "
        "WHERE o.status = 'delivered'"
    )
    every_code = set()
    for rows in (0, 5):
        every_code |= {
            f.code for f in inspect_result(
                question="How many customers?", sql=sql, dialect="postgres",
                tables=TABLES, row_count=rows, column_count=2, truncated=True,
            ) if f.retry
        }
    assert every_code == {"C_EMPTY_RESULT"}
    assert checks.Finding(code="x", message="m", hint="h").retry is False


def test_inner_join_on_non_nullable_fk_is_fine() -> None:
    sql = (
        "SELECT c.id FROM public.orders o "
        "JOIN public.customers c ON c.id = o.customer_id WHERE c.is_deleted = false"
    )
    assert "C_NULLABLE_INNER_JOIN" not in check(sql)


def test_left_join_on_nullable_fk_is_the_fix_not_the_problem() -> None:
    sql = (
        "SELECT e.id FROM public.orders o "
        "LEFT JOIN public.employees e ON e.id = o.employee_id"
    )
    assert "C_NULLABLE_INNER_JOIN" not in check(sql)


def test_soft_delete_flag_ignored() -> None:
    sql = "SELECT COUNT(*) FROM public.customers"
    assert "C_SOFT_DELETE_UNFILTERED" in check(sql, question="How many customers?")


def test_soft_delete_flag_respected() -> None:
    sql = "SELECT COUNT(*) FROM public.customers WHERE is_deleted = false"
    assert "C_SOFT_DELETE_UNFILTERED" not in check(sql, question="How many customers?")


def test_soft_delete_only_for_tables_actually_queried() -> None:
    sql = "SELECT COUNT(*) FROM public.orders"
    assert "C_SOFT_DELETE_UNFILTERED" not in check(sql)


def test_soft_delete_is_advisory_not_retryable() -> None:
    """It cannot know whether the question wanted the flag filtered, so it
    must not be able to spend a model call guessing."""
    findings = inspect_result(
        question="How many customers?", sql="SELECT COUNT(*) FROM public.customers",
        dialect="postgres", tables=TABLES, row_count=1, column_count=1,
        truncated=False,
    )
    flag = next(f for f in findings if f.code == "C_SOFT_DELETE_UNFILTERED")
    assert flag.retry is False


def test_constant_soft_delete_flag_is_ignored() -> None:
    """An always-false `is_archived` cannot change any answer. Without this,
    the check fires on nearly every query against a real ERP schema."""
    tables = [{
        "schema": "public", "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "is_archived", "data_type": "boolean", "distinct_count": 1},
        ],
    }]
    findings = inspect_result(
        question="How many orders?", sql="SELECT COUNT(*) FROM public.orders",
        dialect="postgres", tables=tables, row_count=1, column_count=1,
        truncated=False,
    )
    assert [f.code for f in findings] == []


@pytest.mark.parametrize(
    "question",
    ["How many active customers do we have?", "What was the total revenue?",
     "How much did we refund?"],
)
def test_single_figure_question_with_many_rows(question: str) -> None:
    codes = check("SELECT id FROM public.orders", question=question, rows=12)
    assert "C_GRANULARITY" in codes


def test_single_figure_question_with_one_row_is_clean() -> None:
    codes = check("SELECT COUNT(*) FROM public.orders",
                  question="How many orders?", rows=1)
    assert "C_GRANULARITY" not in codes


def test_group_by_explains_the_extra_rows() -> None:
    """"How many orders per X?" trips the single-figure regex and is still
    shaped exactly right — one row per group is what was asked for."""
    codes = check(
        "SELECT employee_id, COUNT(*) FROM public.orders GROUP BY employee_id",
        question="How many orders per employee?", rows=12,
    )
    assert "C_GRANULARITY" not in codes


def test_grouping_sets_also_explain_the_extra_rows() -> None:
    codes = check(
        "SELECT status, COUNT(*) FROM public.orders GROUP BY ROLLUP(status)",
        question="How many orders?", rows=4,
    )
    assert "C_GRANULARITY" not in codes


def test_many_rows_without_a_group_by_still_flags() -> None:
    """The case the check is actually for: nothing in the SQL groups the
    rows, so the multiplicity is an unaggregated SELECT or a join fan-out."""
    codes = check(
        "SELECT o.id FROM public.orders o "
        "JOIN public.customers c ON c.id = o.customer_id",
        question="How many orders do we have?", rows=12,
    )
    assert "C_GRANULARITY" in codes


def test_truncation_is_advisory_only() -> None:
    findings = inspect_result(
        question="List orders", sql="SELECT id FROM public.orders",
        dialect="postgres", tables=TABLES, row_count=1000, column_count=1,
        truncated=True,
    )
    truncated = next(f for f in findings if f.code == "C_TRUNCATED")
    assert truncated.retry is False


def test_unparseable_sql_has_no_opinion() -> None:
    """The guard owns parse failures; a check must not double-report them."""
    assert check("SELECT FROM WHERE ((", rows=3) == set()


def test_checks_never_read_result_values() -> None:
    """The signature is the contract: no rows in, so nothing to leak out.

    This is what lets `inspect` run identically under every disclosure policy,
    including NONE, where a model critic would be shown nothing at all.
    """
    import inspect as _inspect

    params = set(_inspect.signature(inspect_result).parameters)
    assert "rows" not in params
    assert params == {
        "question", "sql", "dialect", "tables", "row_count", "column_count",
        "truncated",
    }


# ── the retry safety net ────────────────────────────────────────────────────
def _state(**kwargs: Any) -> RunState:
    from datetime import timedelta
    from uuid import uuid4

    from app.core.clock import utcnow

    return RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="q",
        deadline_at=utcnow() + timedelta(seconds=60), **kwargs,
    )


def test_failed_retry_restores_the_earlier_result() -> None:
    from app.pipeline.nodes import _restore_superseded

    original = ExecutionResult(row_count=7)
    state = _state()
    state.superseded_execution = original
    state.error = None

    result = _restore_superseded(state)

    assert result is not None
    assert result.goto == "present"
    assert state.execution is original      # the good result is back
    assert state.superseded_execution is None
    assert state.error is None


def test_no_fallback_when_no_retry_was_attempted() -> None:
    """Without a check-driven retry in flight, failures must fail as before."""
    from app.pipeline.nodes import _restore_superseded

    assert _restore_superseded(_state()) is None


@pytest.mark.asyncio
async def test_only_the_triggering_finding_reaches_the_repair_prompt() -> None:
    """Advisory means advisory in both directions.

    The bug this pins: `generate` quoted back *every* finding, so an advisory
    soft-delete note added a `WHERE is_deleted = false` the question never
    asked for — on a retry some other check had started. That flipped correct
    answers to wrong ones on sales_v1 (2026-07-27).
    """
    from app.pipeline.checks import Finding
    from app.pipeline.contracts import SqlProposal
    from app.pipeline.nodes import NodeDeps, generate
    from app.pipeline.state import RetrievedContext

    seen: list[str] = []

    class CapturingGateway:
        async def structured(self, _llm, messages, _schema):  # type: ignore[no-untyped-def]
            seen.extend(m.content for m in messages)
            return SqlProposal(sql="SELECT 1", tables_used=[], reasoning="")

    async def emit(_type: str, _data: dict) -> None:
        return None

    deps = NodeDeps(
        llm_gateway=CapturingGateway(), llm=None, connector=None, snapshot={},
        history=[], policy=None, emit=emit,
    )
    state = _state()
    state.context = RetrievedContext(dialect="postgres", tables=TABLES)
    state.attempts = [
        SqlAttempt(
            attempt_no=1, raw_sql="SELECT 1", rewritten_sql="SELECT 1",
            report=ValidationReport(status="VALID"),
            findings=[
                Finding(code="C_EMPTY_RESULT", message="empty", hint="widen",
                        retry=True),
                Finding(code="C_SOFT_DELETE_UNFILTERED", message="soft",
                        hint="filter is_deleted"),
            ],
        )
    ]

    await generate(state, deps)
    prompt = "\n".join(seen)

    assert "C_EMPTY_RESULT" in prompt
    assert "C_SOFT_DELETE_UNFILTERED" not in prompt
    # `is_deleted` itself is legitimately in the schema block; what must not
    # appear is the advisory finding's instruction to filter on it.
    assert "filter is_deleted" not in prompt


@pytest.mark.asyncio
async def test_inspect_retries_once_then_accepts() -> None:
    from app.pipeline.nodes import NodeDeps, inspect
    from app.pipeline.state import RetrievedContext

    async def emit(_type: str, _data: dict) -> None:
        return None

    deps = NodeDeps(
        llm_gateway=None, llm=None, connector=None, snapshot={},
        history=[], policy=None, emit=emit,
    )
    # A literal the snapshot says `status` cannot hold: the only empty result
    # that is evidence of a mistake rather than an answer.
    sql = "SELECT id FROM public.orders WHERE status = 'delivered'"
    state = _state(max_repairs=1)
    state.context = RetrievedContext(dialect="postgres", tables=TABLES)
    state.execution = ExecutionResult(row_count=0)
    state.attempts = [
        SqlAttempt(attempt_no=1, raw_sql=sql, rewritten_sql=sql,
                   report=ValidationReport(status="VALID"))
    ]

    first = await inspect(state, deps)
    assert first.goto == "generate"
    assert state.check_repair_used is True
    assert state.superseded_execution is not None

    # The retry produced another empty result: accept it rather than loop.
    state.attempts.append(
        SqlAttempt(attempt_no=2, raw_sql=sql, rewritten_sql=sql,
                   report=ValidationReport(status="VALID"))
    )
    second = await inspect(state, deps)
    assert second.goto is None
    assert state.superseded_execution is None
