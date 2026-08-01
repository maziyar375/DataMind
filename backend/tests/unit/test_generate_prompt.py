"""What a SQL-producing call is told, on the first attempt and on a repair.

A repair is not a continuation: `REVIEW_SYSTEM` and `REPAIR_SYSTEM` open a
fresh two-message conversation, so the model has never seen `GENERATE_SYSTEM`
and anything those prompts omit is simply gone. Two things used to be omitted,
and both are tested here because neither is visible from reading one prompt:

* the **mandatory rules** ("SELECT only", "never guess a name", "do not add a
  LIMIT"), which a repair could break while correctly fixing what it was asked
  to fix — and a second rejection ends the run;
* the **conversation history**, which is also the only place a follow-up
  ("now break that down by month") can learn what the previous turn ran.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.domain.value_objects import DisclosurePolicy
from app.pipeline.checks import Finding
from app.pipeline.contracts import SqlProposal
from app.pipeline.nodes import NodeDeps, _render_history, generate
from app.pipeline.prompts import (
    GENERATE_SYSTEM,
    REPAIR_SYSTEM,
    REVIEW_SYSTEM,
)
from app.pipeline.state import RetrievedContext, RunState, SqlAttempt
from app.sqlguard.validator import ValidationIssue, ValidationReport

# One line from each mandatory rule, chosen so a paraphrase would not pass.
RULE_FRAGMENTS = (
    "Exactly one statement",
    "SELECT only",
    "Never guess a name",
    "Qualify tables with their schema",
    "Do not add a LIMIT",
    "Prefer explicit JOINs",
    "at that granularity",
)

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "total", "data_type": "numeric"},
        ],
    }
]


# ── the rules reach every prompt that produces SQL ───────────────────────────
@pytest.mark.parametrize("template", [GENERATE_SYSTEM, REVIEW_SYSTEM, REPAIR_SYSTEM])
def test_every_sql_producing_prompt_states_the_mandatory_rules(
    template: str,
) -> None:
    rendered = template.format(
        dialect="postgres", schema="Dialect: postgres", history="", feedback="x"
    )
    missing = [fragment for fragment in RULE_FRAGMENTS if fragment not in rendered]
    assert not missing, f"prompt dropped: {missing}"


@pytest.mark.parametrize("template", [GENERATE_SYSTEM, REVIEW_SYSTEM, REPAIR_SYSTEM])
def test_every_sql_producing_prompt_names_the_dialect(template: str) -> None:
    """Not incidentally, via the schema block's first line — as an instruction.

    A repair that emits `LIMIT` on Oracle or `TOP` on Postgres is rejected by a
    dialect-aware guard, and the repair budget is 1.
    """
    rendered = template.format(
        dialect="oracle", schema="(schema omitted)", history="", feedback="x"
    )
    assert "Dialect: oracle" in rendered


@pytest.mark.parametrize("template", [GENERATE_SYSTEM, REVIEW_SYSTEM, REPAIR_SYSTEM])
def test_every_sql_producing_prompt_carries_the_history(template: str) -> None:
    rendered = template.format(
        dialect="postgres", schema="s", history="Earlier: user: revenue?", feedback="x"
    )
    assert "Earlier: user: revenue?" in rendered


# ── rendering the history ────────────────────────────────────────────────────
# `_render_history` takes the disclosure policy and defaults to the narrowest,
# which withholds an earlier answer's prose. These tests are about the *shape*
# of a rendered turn, so they pass a wide policy explicitly; what the narrow
# ones withhold is tested in `test_history_disclosure.py`.
WIDE = DisclosurePolicy.FULL


def test_history_is_empty_when_there_are_no_turns() -> None:
    assert _render_history([], WIDE) == ""


def test_assistant_turn_carries_the_sql_behind_it() -> None:
    """The persisted assistant message is prose; the SQL is joined back in."""
    rendered = _render_history(
        [
            {"role": "user", "content": "What was revenue in June?"},
            {
                "role": "assistant",
                "content": "Revenue was $1.2M in June.",
                "sql": "SELECT SUM(total) FROM public.orders",
            },
        ],
        WIDE,
    )
    assert "user: What was revenue in June?" in rendered
    assert "assistant: Revenue was $1.2M in June." in rendered
    assert "  SQL: SELECT SUM(total) FROM public.orders" in rendered


def test_multi_line_sql_is_collapsed_onto_one_line() -> None:
    """Otherwise a statement's own newlines break the `role: content` structure
    the turns are read by, and a continuation line reads as a new turn."""
    rendered = _render_history(
        [{"role": "assistant", "content": "a", "sql": "SELECT 1\nFROM t\nWHERE x = 1"}],
        WIDE,
    )
    sql_lines = [ln for ln in rendered.splitlines() if "SELECT" in ln]
    assert sql_lines == ["  SQL: SELECT 1 FROM t WHERE x = 1"]


def test_a_turn_without_sql_renders_exactly_as_before() -> None:
    """A conversation with no query behind it — chitchat, a clarification, a
    failed run — must produce the bytes it produced before SQL was carried."""
    turns = [{"role": "user", "content": "hello"}]
    assert _render_history(turns, WIDE) == "Earlier in this conversation:\nuser: hello"


def test_long_content_and_sql_are_both_truncated() -> None:
    rendered = _render_history(
        [{"role": "assistant", "content": "x" * 900, "sql": "y" * 900}], WIDE
    )
    assert "x" * 300 in rendered and "x" * 301 not in rendered
    assert "y" * 400 in rendered and "y" * 401 not in rendered


# ── the repair path actually sends them ──────────────────────────────────────
class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def structured(self, _llm: Any, messages: Any, _schema: Any) -> Any:
        self.messages = list(messages)
        return SqlProposal(sql="SELECT 1", reasoning="")


def _state_with_history() -> RunState:
    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=uuid4(),
        connection_id=uuid4(), question="and by month?",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    state.context = RetrievedContext(
        dialect="postgres",
        tables=TABLES,
        history=[
            {
                "role": "assistant",
                "content": "Revenue was $1.2M.",
                "sql": "SELECT SUM(total) FROM public.orders",
            }
        ],
    )
    return state


def _deps(gateway: Any) -> NodeDeps:
    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    return NodeDeps(
        llm_gateway=gateway, llm=None, connector=None, snapshot={},
        history=[], policy=None, emit=emit,
    )


def _rejected(state: RunState) -> None:
    state.attempts.append(
        SqlAttempt(
            attempt_no=1,
            raw_sql="SELECT nope FROM public.orders",
            report=ValidationReport(
                status="REJECTED",
                issues=[ValidationIssue(rule_id="E_UNKNOWN_COLUMN", message="nope")],
            ),
        )
    )


def _reviewed(state: RunState) -> None:
    """An attempt that passed the guard, ran, and tripped a structural check."""
    state.attempts.append(
        SqlAttempt(
            attempt_no=1,
            raw_sql="SELECT SUM(total) FROM public.orders",
            rewritten_sql="SELECT SUM(total) FROM public.orders LIMIT 1000",
            report=ValidationReport(status="VALID"),
            findings=[
                Finding(code="C_EMPTY_RESULT", message="no rows", hint="check", retry=True)
            ],
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [_rejected, _reviewed])
async def test_a_repair_attempt_is_sent_the_rules_and_the_history(seed: Any) -> None:
    gateway = FakeGateway()
    state = _state_with_history()
    seed(state)

    await generate(state, _deps(gateway))

    system = gateway.messages[0].content
    assert [f for f in RULE_FRAGMENTS if f not in system] == []
    assert "SQL: SELECT SUM(total) FROM public.orders" in system
    assert "Dialect: postgres" in system


@pytest.mark.asyncio
async def test_the_two_repair_prompts_stay_distinguishable() -> None:
    """Sharing the rules must not blur the reason for the retry: a query that
    ran is not a query that was rejected, and telling the model otherwise sends
    it looking for an error that does not exist."""
    rejected_state, reviewed_state = _state_with_history(), _state_with_history()
    _rejected(rejected_state)
    _reviewed(reviewed_state)

    rejected_gateway, reviewed_gateway = FakeGateway(), FakeGateway()
    await generate(rejected_state, _deps(rejected_gateway))
    await generate(reviewed_state, _deps(reviewed_gateway))

    assert "rejected by a validator" in rejected_gateway.messages[0].content
    assert "ran successfully" in reviewed_gateway.messages[0].content
    assert "keep your original query" in reviewed_gateway.messages[0].content
