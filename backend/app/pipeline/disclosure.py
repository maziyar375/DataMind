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


def disclose(execution: ExecutionResult, policy: str) -> DisclosedResult:
    columns = [c.name for c in execution.columns]

    if policy == DisclosurePolicy.NONE:
        return DisclosedResult(
            policy=policy, columns=[], rows=[],
            note=f"{execution.row_count} rows were returned but not shared with the model.",
        )

    if policy == DisclosurePolicy.AGGREGATE:
        return DisclosedResult(
            policy=policy, columns=columns, rows=[],
            note=(
                f"{execution.row_count} rows across columns: {', '.join(columns)}. "
                "Individual values were not shared with the model."
            ),
        )

    if policy == DisclosurePolicy.SAMPLE:
        rows = execution.rows[:SAMPLE_ROWS]
        note = ""
        if execution.row_count > len(rows):
            note = (
                f"Showing the first {len(rows)} of {execution.row_count} rows."
            )
        return DisclosedResult(policy=policy, columns=columns, rows=rows, note=note)

    return DisclosedResult(
        policy=DisclosurePolicy.FULL,
        columns=columns,
        rows=execution.rows,
        note=f"{execution.row_count} rows.",
    )
