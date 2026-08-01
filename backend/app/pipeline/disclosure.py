"""What may leave the customer's database and reach a third-party model.

This is one of the three things the architecture refuses to simplify. The
policy is enforced here, in one place, and the chat header shows the user
which policy is in force at the moment they ask.

It lives in the pipeline layer because it is a pure transformation over a
pipeline `ExecutionResult` — no I/O, no service dependencies — and the
`present` node applies it as the last step before the model sees a result.
"""
from __future__ import annotations

from app.domain.value_objects import DisclosurePolicy
from app.pipeline.state import DisclosedResult, ExecutionResult

SAMPLE_ROWS = 50

# The policies under which a result's *values* may reach the model at all.
# Anything else — NONE, AGGREGATE, and any unrecognised string — is narrow, and
# `disclose_history` fails closed against it.
_WIDE_POLICIES = frozenset({DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL})


def _cap_note(execution: ExecutionResult) -> str:
    """Say so when the row cap, not the query, decided how many rows there are.

    Without this the model narrates a capped result as if it were the whole
    answer — "the top 1000 customers" — when 1000 is the platform's limit and
    the real count is unknown. Empty (and byte-identical to before) whenever
    the result is complete.
    """
    if not execution.truncated:
        return ""
    return (
        f" This is a partial result: the platform capped it at "
        f"{execution.row_count} rows, so the true total is higher and any "
        "'all'/'top N' claim about the full set cannot be made from it."
    )


def disclose(execution: ExecutionResult, policy: str) -> DisclosedResult:
    columns = [c.name for c in execution.columns]
    cap = _cap_note(execution)

    if policy == DisclosurePolicy.NONE:
        return DisclosedResult(
            policy=policy, columns=[], rows=[],
            note=(
                f"{execution.row_count} rows were returned but not shared "
                f"with the model.{cap}"
            ),
        )

    if policy == DisclosurePolicy.AGGREGATE:
        return DisclosedResult(
            policy=policy, columns=columns, rows=[],
            note=(
                f"{execution.row_count} rows across columns: {', '.join(columns)}. "
                f"Individual values were not shared with the model.{cap}"
            ),
        )

    if policy == DisclosurePolicy.SAMPLE:
        rows = execution.rows[:SAMPLE_ROWS]
        note = ""
        if execution.row_count > len(rows):
            note = (
                f"Showing the first {len(rows)} of {execution.row_count} rows."
            )
        return DisclosedResult(
            policy=policy, columns=columns, rows=rows, note=f"{note}{cap}".strip()
        )

    return DisclosedResult(
        policy=DisclosurePolicy.FULL,
        columns=columns,
        rows=execution.rows,
        note=f"{execution.row_count} rows.{cap}",
    )


# ── the transcript: the third thing the policy governs ──────────────────────
# `disclose` above gates the result of *this* run and `HintBudget` gates the
# schema block. Neither touches the conversation history, which is where a
# result disclosed under a wider policy comes back: the assistant message
# `present` persists is prose the model wrote *from* result rows ("Revenue was
# $1.24M across 812 orders"), and the next turn sends the last few messages
# back as context for the follow-up.
#
# Those messages were rendered once, at write time, under the policy of that
# moment — so without this a connection tightened from FULL to NONE would keep
# replaying yesterday's figures under a policy whose entire meaning is that no
# result data reaches the model. History is therefore filtered here, at *read*
# time, against the policy in force now: the same rule the hint budget follows,
# and the same rule the chat header promises the user.
#
# Two kinds of text survive every policy, because neither has ever seen a
# result row:
#
#   * the user's own turns — their words, not the database's;
#   * text derived from the *schema* rather than from a result: the SQL behind
#     an earlier answer, and a clarifying question. Both are what a follow-up
#     ("now break that down by month") actually builds on, and the SQL is
#     already on screen as an auditable artifact.
#
# Deliberately accepted residual: a literal inside kept SQL (`WHERE status =
# 'churned'`) may have come from a column value list that a wider policy's
# HintBudget once allowed into the schema block. It is one token, the user is
# already looking at it, and stripping it would take from a follow-up the one
# thing it most needs. Recorded in docs/pipeline.md §5.
#
# Not a summary and not a rewrite: a turn is kept whole or replaced whole, in
# keeping with the rule that nothing in this pipeline paraphrases the user.

WITHHELD_ANSWER = (
    "(this answer was produced under a wider result-sharing policy "
    "and is withheld from the model)"
)


def disclose_history(
    history: list[dict[str, str]], policy: str
) -> list[dict[str, str]]:
    """The recent turns as the policy in force now permits them to be sent.

    Identity under SAMPLE and FULL, so a connection that shares result data
    with the model builds exactly the prompt it built before this existed.
    """
    if policy in _WIDE_POLICIES:
        return history

    disclosed: list[dict[str, str]] = []
    for turn in history:
        if turn.get("role") != "assistant" or turn.get("kind") == "clarification":
            disclosed.append(turn)
            continue
        # Everything else on the turn is kept: the role, the `sql`, the kind.
        disclosed.append({**turn, "content": WITHHELD_ANSWER})
    return disclosed
