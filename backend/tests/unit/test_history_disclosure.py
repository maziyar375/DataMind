"""The conversation is a disclosure too.

`disclose()` gates the result of a run and `HintBudget` gates the schema block.
Neither had anything to say about the transcript — and the assistant message
`present` persists is prose written *from* result rows, replayed into the next
turn's prompt. A connection tightened from FULL to NONE therefore kept sending
yesterday's figures to the model under a policy whose entire meaning is that no
result data reaches it.

Filtering happens at *read* time, against the policy in force now, which is the
rule the hint budget already followed and the one the chat header promises. The
tests below fix the three properties that make that safe:

* under SAMPLE and FULL the filter is the **identity function**, so a run on a
  wide policy builds the prompt it built before this existed;
* under NONE, AGGREGATE, and anything unrecognised, an earlier answer's prose
  is withheld while the **SQL behind it survives** — that SQL is what a
  follow-up builds on, and it never saw a result row;
* a **clarifying question** survives every policy, because the user's next
  message is the reply to it.
"""
from __future__ import annotations

import pytest

from app.domain.value_objects import DisclosurePolicy
from app.pipeline.disclosure import WITHHELD_ANSWER, disclose_history
from app.pipeline.nodes import _describe_schema, _render_history

ANSWER = "Revenue was $1.24M across 812 orders."
SQL = "SELECT SUM(total) FROM public.orders"

THREAD: list[dict[str, str]] = [
    {"role": "user", "content": "What was revenue in June?"},
    {"role": "assistant", "content": ANSWER, "sql": SQL},
]

WIDE = [DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL]
NARROW = [DisclosurePolicy.NONE, DisclosurePolicy.AGGREGATE, "NOT_A_POLICY", ""]


# ── wide policies are untouched ─────────────────────────────────────────────
@pytest.mark.parametrize("policy", WIDE)
def test_a_wide_policy_sends_the_turns_unchanged(policy: str) -> None:
    """Identity, not an equal-looking copy: a connection that already shares
    result data with the model must build a byte-identical prompt, so the eval
    baseline measured before this existed still describes it."""
    assert disclose_history(THREAD, policy) is THREAD


# ── narrow policies withhold the answer, and only the answer ────────────────
@pytest.mark.parametrize("policy", NARROW)
def test_a_narrow_policy_withholds_an_earlier_answer(policy: str) -> None:
    disclosed = disclose_history(THREAD, policy)
    assert disclosed[1]["content"] == WITHHELD_ANSWER
    assert ANSWER not in str(disclosed)
    assert "1.24M" not in str(disclosed)
    assert "812" not in str(disclosed)


@pytest.mark.parametrize("policy", NARROW)
def test_the_users_own_words_are_never_withheld(policy: str) -> None:
    """The user typed them. Nothing about them came out of the database, and a
    transcript with the questions removed is not a conversation."""
    assert disclose_history(THREAD, policy)[0] == THREAD[0]


@pytest.mark.parametrize("policy", NARROW)
def test_the_sql_behind_a_withheld_answer_survives(policy: str) -> None:
    """The statement is schema-derived, not result-derived — it is what
    produced the rows, not something read off them — and it is the one thing a
    follow-up ("now break that down by month") actually needs."""
    assert disclose_history(THREAD, policy)[1]["sql"] == SQL


@pytest.mark.parametrize("policy", NARROW)
def test_a_clarifying_question_survives(policy: str) -> None:
    """`clarify` runs before any SQL executes, so its question cannot contain
    result data — and withholding it would leave the user's reply, which is the
    very next turn, answering a question the model can no longer see."""
    thread = [
        {"role": "assistant", "content": "Paid or ordered?", "kind": "clarification"},
        {"role": "user", "content": "Paid."},
    ]
    assert disclose_history(thread, policy) == thread


def test_the_original_turns_are_not_mutated() -> None:
    """The caller's list is the run's state; redaction is for the copy that
    goes to the model."""
    thread = [{"role": "assistant", "content": ANSWER}]
    disclose_history(thread, DisclosurePolicy.NONE)
    assert thread[0]["content"] == ANSWER


# ── the renderer applies it, and fails closed ───────────────────────────────
def test_render_defaults_to_the_narrowest_policy() -> None:
    """Same fail-closed default as `RetrievedContext.render`: a caller that
    forgets the policy cannot widen a disclosure by omission."""
    rendered = _render_history(THREAD)
    assert ANSWER not in rendered
    assert WITHHELD_ANSWER in rendered


def test_render_keeps_the_sql_line_under_a_narrow_policy() -> None:
    rendered = _render_history(THREAD, DisclosurePolicy.NONE)
    assert f"  SQL: {SQL}" in rendered
    assert "user: What was revenue in June?" in rendered


def test_render_under_a_wide_policy_is_unchanged() -> None:
    rendered = _render_history(THREAD, DisclosurePolicy.FULL)
    assert f"assistant: {ANSWER}" in rendered


# ── the suggestions prompt's schema block ───────────────────────────────────
TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "approx_row_count": 812_345,
        "columns": [{"name": "id"}, {"name": "total"}],
    }
]


@pytest.mark.parametrize("policy", [DisclosurePolicy.NONE, "NOT_A_POLICY"])
def test_row_counts_are_withheld_under_a_narrow_policy(policy: str) -> None:
    """`_describe_schema` builds the follow-up-suggestions prompt, which used
    to print approximate cardinality unconditionally — a figure derived from
    the customer's data that `HintBudget` withholds from every other prompt
    under this policy."""
    described = _describe_schema(TABLES, policy)
    assert "812,345" not in described
    assert "public.orders" in described and "total" in described


@pytest.mark.parametrize(
    "policy",
    [DisclosurePolicy.AGGREGATE, DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL],
)
def test_row_counts_are_shared_where_the_budget_allows(policy: str) -> None:
    assert "812,345" in _describe_schema(TABLES, policy)


def test_describe_schema_defaults_to_the_narrowest_policy() -> None:
    assert "812,345" not in _describe_schema(TABLES)
