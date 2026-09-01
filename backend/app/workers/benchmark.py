"""Running a benchmark set — one model call per question, off the request path.

Phase 6 of `docs/learning-loop-plan.md`. This is where a customer gets a number
of their own, and the whole design is about that number being honest.

**It runs the real pipeline.** The same `AnalyticsPipeline`, the same guard, the
same connector the ask path uses — because a benchmark that measured a
simplified path would be measuring something the customer never experiences.
The only thing this does that a chat run does not is execute the *gold*
statement afterwards and compare.

**The labels come from the comparator, and there is no LLM judge.**
`app/knowledge/compare.py` decides `MATCH` or `MISMATCH`, with the documented
numeric tolerance. Fabric fell back to a judge for this and gets *true / false /
unclear*; spending a model call per row to get a worse answer would be a strange
trade, and it would make the instrument non-deterministic in the one place it
must not be.

**Questions and gold SQL are both derived from one probe.** A template is a
*pattern* — `revenue by month for {region}` with `:region` in the SQL — so
before it can be asked or executed, one set of values has to fill both sides.
`probe_values` (Phase 4's, reused) supplies them deterministically, and a
member whose slots cannot be filled is recorded as `NOT_PROBED` and left out of
**both** denominators. An accuracy computed over the questions that happened to
run is the classic silent lie, and it always flatters.

`app.eval` is not imported here and must not be: that package is offline-only
by contract, and this is the customer-facing instrument. They share a
vocabulary and a comparator; they share no table and no import.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.logging import get_logger
from app.infra.db.models import (
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkSet,
    DatabaseConnection,
    KnowledgeTemplateRow,
    LlmConfig,
)
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.knowledge import (
    TemplateParam,
    TemplateRole,
    example_questions,
    probe_values,
    result_sets_match,
)
from app.knowledge.bind import bind_sql
from app.pipeline.nodes import NodeDeps
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.prompts import PROMPT_VERSION
from app.pipeline.state import RunState
from app.services.benchmark_service import (
    FAILED,
    OUTCOME_ERROR,
    OUTCOME_EXEC_FAILED,
    OUTCOME_MATCH,
    OUTCOME_MISMATCH,
    OUTCOME_NO_SQL,
    OUTCOME_NOT_PROBED,
    OUTCOME_VALIDATION_FAILED,
    RUNNING,
    SUCCEEDED,
    BenchmarkService,
)
from app.services.query_service import (
    bind_connector,
    execute_saved_sql,
    latest_snapshot,
    policy_from_snapshot,
    resolve_llm,
    secret_box,
)

log = get_logger(__name__)

#: Questions run at a time. One: a benchmark is not latency-sensitive, and
#: fanning out N model calls at a free-tier endpoint produces rate limits rather
#: than a faster number.
MAX_CONCURRENT_QUESTIONS = 1

#: Rows the gold statement may return while being compared. The same reasoning
#: as the conflict checker's cap — a mismatch shows itself immediately, and
#: pulling ten thousand rows twice per question spends the customer's database
#: to reach a conclusion already reached.
GOLD_ROW_CAP = 1_000

#: Non-analytical intents. A benchmark question that routes to CHITCHAT
#: produced no SQL, which is a failure of the *product* on that question and is
#: scored as one rather than skipped.
_NON_ANALYTICAL = ("METADATA", "CHITCHAT", "UNSUPPORTED")


async def execute_benchmark_run(
    db: AsyncSession, settings: Settings, run_id: UUID
) -> BenchmarkRun | None:
    """Run every member of a set and write the two numbers down.

    Idempotent in the only sense that matters: a run left `RUNNING` by a dead
    process is failed by `sweep_stranded` rather than resumed, because a
    half-scored benchmark is a number nobody should read and re-running costs
    only model calls.
    """
    run = await db.get(BenchmarkRun, run_id)
    if run is None or run.status not in ("QUEUED", RUNNING):
        return run

    set_row = await db.get(BenchmarkSet, run.set_id)
    connection = (
        await db.get(DatabaseConnection, run.connection_id)
        if run.connection_id else None
    )
    llm_config = (
        await db.get(LlmConfig, run.llm_config_id) if run.llm_config_id else None
    )
    if set_row is None or connection is None or llm_config is None:
        missing = (
            "benchmark set" if set_row is None
            else "data source" if connection is None else "model"
        )
        return await _fail(db, run, f"The {missing} this run was using is gone.")

    run.status = RUNNING
    run.started_at = utcnow()
    # Stamped by the process that renders the bytes, not by the one that queued
    # the row — Phase 0's lesson, applied to the second instrument as well.
    run.prompt_version = PROMPT_VERSION
    await db.commit()

    members = await _members(db, set_row)
    run.total = len(members)

    box = secret_box(settings)
    llm = resolve_llm(llm_config, box)
    run.model_snapshot = llm.snapshot()

    snapshot = await latest_snapshot(db, connection.id)
    if not snapshot.get("tables"):
        return await _fail(
            db, run,
            "Sync this connection's schema first — a benchmark is checked "
            "against it.",
        )

    gateway = LiteLLMGateway.from_settings(settings)
    connector = bind_connector(connection, box)
    now = utcnow()
    try:
        for template in members:
            result = await _run_one(
                db, settings, run, connection, template,
                gateway=gateway, llm=llm, connector=connector,
                snapshot=snapshot, now=now,
            )
            db.add(result)
            await db.flush()
    except Exception as err:  # noqa: BLE001 — a run must end in a terminal state
        log.exception("benchmark_run_failed", run_id=str(run_id))
        return await _fail(db, run, str(err)[:500])
    finally:
        await connector.close()

    results = await BenchmarkService(db, settings).results(run)
    score = BenchmarkService.score(results)
    run.scored = sum(
        1 for r in results if r.outcome not in (OUTCOME_NOT_PROBED, OUTCOME_ERROR)
    )
    run.matched = sum(1 for r in results if r.outcome == OUTCOME_MATCH)
    run.held_out_total = score.held_out_total
    run.held_out_matched = score.held_out_matched
    run.taught_total = score.taught_total
    run.taught_matched = score.taught_matched
    run.status = SUCCEEDED
    run.finished_at = utcnow()
    await db.commit()

    log.info(
        "benchmark_run_finished",
        run_id=str(run_id),
        held_out=f"{score.held_out_matched}/{score.held_out_total}",
        taught=f"{score.taught_matched}/{score.taught_total}",
    )
    return run


async def _run_one(
    db: AsyncSession,
    settings: Settings,
    run: BenchmarkRun,
    connection: DatabaseConnection,
    template: KnowledgeTemplateRow,
    *,
    gateway: Any,
    llm: Any,
    connector: Any,
    snapshot: dict[str, Any],
    now: Any,
) -> BenchmarkResult:
    """One question, end to end: ask, execute the gold, compare.

    The order matters. The pipeline runs first and knows nothing about the gold
    statement; the gold is executed afterwards through `execute_saved_sql`, the
    guard's own door, so the reference answer is subject to the same row cap and
    the same read-only credentials as everything else.
    """
    row = BenchmarkResult(
        id=uuid.uuid4(),
        run_id=run.id,
        template_id=template.id,
        role=template.role,
        question=template.question,
    )

    params = [TemplateParam.model_validate(p) for p in (template.params or [])]
    probe = probe_values(params, now=now)
    if not probe.ok:
        # Counted in `total`, absent from both denominators, and *named*: a
        # curator whose string parameter has no declared value list is told
        # which one, in the same words `REJECTED_UNBOUND` uses on the ask path.
        row.outcome = OUTCOME_NOT_PROBED
        row.failure_reason = (
            "No values to try for " + ", ".join(probe.unfilled)
            + " — give the parameter a value list (for example "
            "“one of: EMEA, NA, APAC”)."
        )
        return row

    question = example_questions(
        template.question, [(k, str(v)) for k, v in probe.values.items()]
    )
    row.question = question
    gold_sql = bind_sql(
        template.sql, probe.values,
        dialect=policy_from_snapshot(snapshot, connection).dialect,
    )
    if not gold_sql:
        row.outcome = OUTCOME_NOT_PROBED
        row.failure_reason = "The stored statement would not bind to any values."
        return row
    row.gold_sql = gold_sql

    started = utcnow()
    state = await _ask(
        settings, connection, question,
        gateway=gateway, llm=llm, connector=connector, snapshot=snapshot,
        db=db,
    )
    row.duration_ms = int((utcnow() - started).total_seconds() * 1000)
    row.from_template = state.match_outcome == "SHORT_CIRCUIT"
    row.candidate_sql = state.attempts[-1].raw_sql if state.attempts else ""

    if state.intent in _NON_ANALYTICAL:
        row.outcome = OUTCOME_NO_SQL
        row.failure_reason = f"routed as {state.intent}; no SQL was produced"
        return row
    if state.execution is None:
        if state.attempts and state.attempts[-1].report.status == "VALID":
            row.outcome = OUTCOME_EXEC_FAILED
            row.failure_reason = (
                state.error.message if state.error else "the database rejected it"
            )
        elif state.attempts:
            row.outcome = OUTCOME_VALIDATION_FAILED
            row.failure_reason = "the guard rejected every attempt"
        else:
            row.outcome = OUTCOME_ERROR
            row.failure_reason = (
                state.error.message if state.error else "no SQL was produced"
            )
        return row

    gold = await execute_saved_sql(
        db, settings, sql=gold_sql, connection=connection,
        owner_id=connection.owner_id, max_rows=GOLD_ROW_CAP,
        connector=connector, snapshot=snapshot,
    )
    if gold.status != "OK":
        # The *reference* failed, not the answer. That is a fact about the
        # template — usually schema drift the sweep has not caught yet — and
        # scoring the question against a gold that did not run would invent a
        # verdict. Neither denominator.
        row.outcome = OUTCOME_ERROR
        row.failure_reason = f"the stored answer did not run: {gold.error_message}"
        return row

    row.gold_row_count = gold.row_count
    row.candidate_row_count = state.execution.row_count
    if result_sets_match(gold.rows, state.execution.rows):
        row.outcome = OUTCOME_MATCH
    else:
        row.outcome = OUTCOME_MISMATCH
        row.failure_reason = (
            f"result mismatch: stored answer {gold.row_count} rows vs "
            f"{state.execution.row_count} rows"
        )
    return row


async def _ask(
    settings: Settings,
    connection: DatabaseConnection,
    question: str,
    *,
    gateway: Any,
    llm: Any,
    connector: Any,
    snapshot: dict[str, Any],
    db: AsyncSession,
) -> RunState:
    """One question through the real pipeline, with no conversation behind it.

    No history, deliberately: a benchmark question is asked cold, and carrying
    a transcript would make the number depend on the order the set happens to
    be stored in.

    The matcher **is** wired, because whether a question gets answered from the
    store is exactly what the split reports. Every set member is
    `BENCHMARK_ONLY` or `HELD_OUT`, so no member can be answered from its own
    row; a member answered from a *neighbour's* row is a real thing that
    happens on the ask path, and pretending otherwise would measure a product
    nobody uses.
    """
    from app.services.knowledge_service import build_matcher

    async def emit(_type: str, _data: dict[str, Any]) -> None:
        return None

    async def on_step(*_: Any) -> None:
        return None

    state = RunState(
        run_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
        owner_id=connection.owner_id, connection_id=connection.id,
        question=question, dialect=connection.database_type,
        max_rows=connection.max_rows,
        statement_timeout_ms=connection.statement_timeout_ms,
        disclosure_policy=connection.disclosure_policy,
        deadline_at=utcnow() + timedelta(seconds=settings.run_deadline_seconds),
    )
    deps = NodeDeps(
        llm_gateway=gateway, llm=llm, connector=connector, snapshot=snapshot,
        history=[], policy=policy_from_snapshot(snapshot, connection), emit=emit,
        # `clarify` is off: a benchmark cannot answer a clarifying question, and
        # a run that stopped to ask would be scored as a failure it did not
        # commit. Everything else is exactly the ask path.
        clarify_enabled=False,
        include_db_comments=connection.include_db_comments,
        matcher=build_matcher(db),
        templates_enabled=True,
        examples_enabled=bool(connection.knowledge_examples_enabled),
    )
    return await AnalyticsPipeline(on_step=on_step).run(state, deps)


async def _members(
    db: AsyncSession, set_row: BenchmarkSet
) -> list[KnowledgeTemplateRow]:
    """The set's members, **filtered by role** — §1.3, in the query.

    A member whose role has drifted back to `RETRIEVABLE` (a curator editing it
    by hand, a set released half-way) is silently *excluded* rather than
    silently scored: it can be answered from its own stored SQL, so scoring it
    would put a number in front of a customer that measures nothing.
    """
    ids = list(set_row.template_ids or [])
    if not ids:
        return []
    result = await db.execute(
        select(KnowledgeTemplateRow).where(
            KnowledgeTemplateRow.id.in_(ids),
            KnowledgeTemplateRow.role.in_(
                (str(TemplateRole.HELD_OUT), str(TemplateRole.BENCHMARK_ONLY))
            ),
        )
    )
    rows = {row.id: row for row in result.scalars().all()}
    # Set order, not database order: a customer reading two runs side by side
    # should see the same questions in the same places.
    return [rows[tid] for tid in ids if tid in rows]


async def _fail(
    db: AsyncSession, run: BenchmarkRun, message: str
) -> BenchmarkRun:
    run.status = FAILED
    run.error_message = message
    run.finished_at = utcnow()
    await db.commit()
    return run


# ── the executor ─────────────────────────────────────────────────────────
class BenchmarkExecutor:
    """In-process, one run at a time, durable through the `benchmark_runs` row.

    The same trade `SemanticJobExecutor` makes: a run is minutes, it holds a
    visible row while it works, and a process that dies leaves a `RUNNING` row
    that `sweep_stranded` turns into a `FAILED` one at startup — which is the
    honest outcome, because a half-scored benchmark is a number nobody should
    read.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUESTIONS)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def submit(self, run_id: UUID) -> None:
        task = asyncio.create_task(self._run(run_id), name=f"benchmark:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))

    async def _run(self, run_id: UUID) -> None:
        from app.infra.db.session import get_sessionmaker

        async with self._semaphore:
            try:
                async with get_sessionmaker()() as session:
                    await execute_benchmark_run(session, self._settings, run_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("benchmark_executor_failed", run_id=str(run_id))


async def sweep_stranded() -> int:
    """Fail runs left QUEUED or RUNNING by a process that died. Returns how many.

    Failed rather than resumed. A resumed benchmark would score the questions
    it had not reached against a store, a schema and a model that may all have
    moved since the ones it already scored — and a number assembled from two
    different worlds is worse than no number.
    """
    from app.infra.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(BenchmarkRun).where(BenchmarkRun.status.in_(("QUEUED", RUNNING)))
        )
        rows = list(result.scalars())
        for row in rows:
            row.status = FAILED
            row.finished_at = utcnow()
            row.error_message = (
                "The server restarted while this benchmark was running. "
                "Nothing was scored — run it again."
            )
        await session.commit()
        return len(rows)
