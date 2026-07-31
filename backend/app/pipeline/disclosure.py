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
