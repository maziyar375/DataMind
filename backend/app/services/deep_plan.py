"""A deep run's plan, for a reader arriving late — or after it ended.

`GET /runs/{id}/plan` (docs/plans/deep-analysis-mode.md §7). Two sources, and
the choice between them is the run's state, never the reader's:

* **ended** — the `ANALYSIS` artifact `_finalise` wrote: the plan, its
  revisions, every step, the answer's claims and how traceable they are.
* **in flight** — the durable `PLAN_PROPOSED` / `PLAN_REVISED` /
  `STEP_EVIDENCE` events folded into the same shape, so a reader who opens
  the thread at step three sees steps one and two exactly as a reader who
  watched them did. `fold` is the same fold the SPA runs over the live stream
  (`frontend/src/components/deep-plan.ts`), which is what keeps the two
  views from disagreeing.

**A reader who may not see the run's data gets the plan and nothing it
found**: the restatement and the questions are the transcript — the reader's
own question, rephrased — while the statements, row counts and computed
summaries are the data, and go with the rows (`_hydrate_run`'s rule).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.value_objects import ArtifactKind, RunDepth, RunEventType, RunStatus
from app.infra.db.models import Artifact, Run, RunEventRow

_PLAN_EVENTS = (
    RunEventType.PLAN_PROPOSED,
    RunEventType.PLAN_REVISED,
    RunEventType.STEP_EVIDENCE,
)


def fold(events: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """The plan so far, from its events in `seq` order. Pure.

    A revision replaces the step at its index and is kept, so the panel can
    strike the old one through (§4.2). A step's evidence is keyed by its
    index, so a replayed event is idempotent rather than a duplicate row.
    """
    plan: dict[str, Any] | None = None
    revisions: list[dict[str, Any]] = []
    steps: dict[int, dict[str, Any]] = {}
    for kind, data in events:
        if kind == RunEventType.PLAN_PROPOSED:
            plan = {k: data.get(k) for k in ("restatement", "steps", "stop_when")}
            plan["steps"] = list(data.get("steps") or [])
        elif kind == RunEventType.PLAN_REVISED and plan is not None:
            index = int(data["index"])
            if 0 <= index < len(plan["steps"]):
                plan["steps"][index] = data["by"]
            revisions.append(data)
        elif kind == RunEventType.STEP_EVIDENCE:
            steps[int(data["index"])] = data
    return {
        "plan": plan,
        "revisions": revisions,
        "steps": [steps[i] for i in sorted(steps)],
    }


def _withhold(record: dict[str, Any]) -> dict[str, Any]:
    """The plan without what it found — for a reader who may not see data."""
    return {
        **record,
        "steps": [
            {"index": s["index"], "question": s.get("question"),
             "intent": s.get("intent"), "tool": s.get("tool"),
             "status": s.get("status")}
            for s in record.get("steps", [])
        ],
        "claims": [],
        "traceable": None,
    }


async def read_plan(db: AsyncSession, run: Run, *, may_read_data: bool) -> dict[str, Any]:
    """The plan record for one run, from whichever source its state implies."""
    base: dict[str, Any] = {
        "depth": run.depth or RunDepth.QUICK,
        "status": run.status,
        "finished": not RunStatus(run.status).is_in_flight,
        "restricted": not may_read_data,
        "stop_reason": "",
        "claims": [],
        "traceable": None,
        "budget": None,
    }
    if run.depth != RunDepth.DEEP:
        return {**base, "plan": None, "revisions": [], "steps": []}

    artifact = (
        await db.execute(
            select(Artifact).where(
                Artifact.run_id == run.id, Artifact.kind == ArtifactKind.ANALYSIS
            )
        )
    ).scalar_one_or_none()
    if artifact is not None:
        record = {**base, **{k: v for k, v in artifact.spec.items() if k != "prompt_version"}}
    else:
        rows = (
            await db.execute(
                select(RunEventRow)
                .where(RunEventRow.run_id == run.id, RunEventRow.type.in_(_PLAN_EVENTS))
                .order_by(RunEventRow.seq)
            )
        ).scalars()
        record = {**base, **fold((row.type, row.data) for row in rows)}
    return record if may_read_data else _withhold(record)


# ── the interruption rate (docs/plans/deep-analysis-mode.md §3.8) ────────────
async def interruption(
    db: AsyncSession, *, since: Any = None, connection_id: Any = None
) -> dict[str, Any]:
    """Of the deep runs readers started, how many they read to the end.

    **The product metric no eval can give**: a mode people interrupt is a mode
    whose latency contract is wrong, and that is a finding about the plan
    panel, not about the SQL. Counted over deep runs that have **ended**, in
    four exclusive buckets:

    * `read_to_end` — SUCCEEDED, and nobody pressed *Answer now*;
    * `answer_now` — SUCCEEDED after *Answer now*: the reader took the answer
      early, which is an interruption that kept its evidence;
    * `cancelled` — the reader stopped it and kept nothing;
    * `failed` — FAILED or TIMED_OUT: the product stopped, not the reader,
      so it is reported and **kept out of the rate's denominator**.

    The rate is `(answer_now + cancelled) / (read_to_end + answer_now +
    cancelled)`, and None when no reader has finished a deep run yet — "no
    interruptions" and "no data" are different statements. The same query in
    SQL, for somebody at a `psql` prompt, is in `docs/reference/eval.md`.
    """
    from sqlalchemy import case, func

    ended = (RunStatus.SUCCEEDED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.TIMED_OUT)
    succeeded = Run.status == RunStatus.SUCCEEDED

    def bucket(condition: Any) -> Any:
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    query = select(
        bucket(succeeded & Run.answer_now_requested.is_(False)),
        bucket(succeeded & Run.answer_now_requested.is_(True)),
        bucket(Run.status == RunStatus.CANCELLED),
        bucket(Run.status.in_((RunStatus.FAILED, RunStatus.TIMED_OUT))),
    ).where(Run.depth == RunDepth.DEEP, Run.status.in_(ended))
    if since is not None:
        query = query.where(Run.created_at >= since)
    if connection_id is not None:
        query = query.where(Run.connection_id == connection_id)

    read_to_end, answer_now, cancelled, failed = (
        int(v) for v in (await db.execute(query)).one()
    )
    started = read_to_end + answer_now + cancelled
    return {
        "read_to_end": read_to_end,
        "answer_now": answer_now,
        "cancelled": cancelled,
        "failed": failed,
        "interruption_rate": (answer_now + cancelled) / started if started else None,
    }
