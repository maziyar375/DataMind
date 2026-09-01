"""Run lifecycle: creation, execution, reconciliation.

Durability comes from the `runs` table plus a heartbeat rather than from a
broker. The swap point for Celery is `RunExecutor`; nothing here knows how a
run gets scheduled.

Three of the rules here exist because more than one replica may be running
(Phase 6 of [docs/langgraph-migration.md](../../../docs/langgraph-migration.md)):

* **`claim` is how a run starts, and it is atomic.** Exactly one process may
  execute a run, and "exactly one" has to be enforced by the database rather
  than by which handler happened to receive the POST.
* **Cancelling is a row, not a task handle.** `cancel` writes
  `cancel_requested`; the process that owns the run reads it on its next
  heartbeat and stops itself. The API's local `executor.cancel` is still
  called first, and is still what makes a same-replica cancel instant.
* **`_finalise` may not overwrite a terminal status it did not set.** A run
  cancelled from another replica is `CANCELLED` before its executor notices,
  and the executor must not write `SUCCEEDED` over it on the way out.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import NotFoundError, RunTimeoutError, ValidationError
from app.core.logging import get_logger
from app.domain.ports.llm import ChatMessage
from app.domain.value_objects import (
    TRANSIENT_RUN_EVENTS,
    ArtifactKind,
    MessageRole,
    RunStatus,
    StepStatus,
)
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import (
    Artifact,
    Conversation,
    DatabaseConnection,
    GeneratedQuery,
    LlmConfig,
    Message,
    QueryExecution,
    Run,
    RunEventRow,
    RunStep,
)
from app.infra.events.bus import event_bus
from app.infra.events.listener import notify_run_event
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.pipeline.nodes import NodeDeps, _describe_schema, _render_history
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.prompts import PROMPT_VERSION
from app.pipeline.state import RunState
from app.services.knowledge_service import build_matcher, record_hit
from app.services.query_service import (
    bind_connector,
    latest_snapshot,
    policy_from_snapshot,
    resolve_llm,
)
from app.services.semantic_service import load_document

log = get_logger(__name__)

# Per-part cap on a question composed from a clarification exchange. Three
# parts, so the worst case is bounded at ~900 characters of user text plus the
# frame — the same order as a turn in `_render_history`, and for the same
# reason: a trimmed real sentence beats a paraphrase nothing verified.
_QUESTION_CHARS = 300

# Said by both paths into a released conversation — the one that names a
# connection explicitly and the one that relies on the conversation's own — so a
# user gets the same sentence whichever the SPA sends. Dashboards and reports
# already answer this case in their own words ("This tile's database connection
# is unavailable", "This report's connection was removed"); chat was the one
# surface that quietly carried on instead.
_RELEASED = (
    "The database this conversation used has been deleted. Its history stays "
    "readable, but it cannot be continued — start a new conversation to ask "
    "against another database."
)


class RunService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._box = AesGcmSecretBox(
            settings.secret_box_key.get_secret_value(),
            settings.secret_box_key_version,
        )

    def _prompt_version(self) -> str:
        """The version a run records: the constant that renders its prompts.

        `runs.prompt_version` exists so a change in wording never silently
        invalidates a historical comparison, which it can only do if it names
        the prompt the run actually used. It used to be written from
        `settings.prompt_version` — a default that said "v2" long after the
        module had reached v8, so **every** run in the database claimed a
        version none of them had run, and any number sliced by it was fiction.

        `settings.prompt_version` survives as an override for an experiment
        that wants its runs filed under a label of its own; empty (the
        shipped default) means "record what rendered it".
        """
        return (self._settings.prompt_version or "").strip() or PROMPT_VERSION

    # ── creation ─────────────────────────────────────────────────────────
    async def create_run(
        self,
        *,
        owner_id: UUID,
        conversation_id: UUID,
        content: str,
        connection_id: UUID | None,
        llm_config_id: UUID | None,
        skip_templates: bool = False,
    ) -> Run:
        conversation = await self._db.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_id != owner_id:
            raise NotFoundError("Conversation not found.")

        # Read before the connection is resolved, because whether this thread
        # has said anything decides what a missing connection *means*: nothing
        # chosen yet, or the one it was using deleted underneath it.
        next_seq = await self._next_message_seq(conversation_id)
        transcript_empty = next_seq == 1

        conn_id = connection_id or conversation.default_connection_id
        llm_id = llm_config_id or conversation.default_llm_config_id
        if conn_id is None:
            raise NotFoundError(
                _RELEASED if not transcript_empty
                else "This conversation has no database connection."
            )
        if llm_id is None:
            raise NotFoundError("This conversation has no model configured.")

        connection = await self._owned(DatabaseConnection, conn_id, owner_id)
        llm_config = await self._owned(LlmConfig, llm_id, owner_id)

        _bind_connection(conversation, connection.id, transcript_empty=transcript_empty)

        user_message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            seq=next_seq,
            role=MessageRole.USER,
            content=content,
        )
        self._db.add(user_message)
        # Flush now so the message row exists before `runs` is inserted: the
        # FK on user_message_id is a plain column, not a relationship, so the
        # unit of work has no dependency info to order the two inserts itself.
        await self._db.flush()

        # Snapshot the effective model config onto the run. Reading it from the
        # conversation later would make every prior run unexplainable the
        # moment a user switches models mid-thread.
        run = Run(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            user_message_id=user_message.id,
            owner_id=owner_id,
            connection_id=connection.id,
            llm_config_id=llm_config.id,
            model_snapshot={
                "provider": llm_config.provider,
                "model": llm_config.model,
                "base_url": llm_config.base_url,
                "temperature": llm_config.temperature,
                "max_tokens": llm_config.max_tokens,
                "connection_name": connection.name,
                "llm_config_name": llm_config.name,
            },
            prompt_version=self._prompt_version(),
            # Set by *Generate a fresh answer instead*. Durable rather than
            # in-memory because the replica that executes this run is not
            # necessarily the one that created it.
            skip_templates=skip_templates,
            status=RunStatus.QUEUED,
        )
        self._db.add(run)

        if conversation.title in ("New chat", "", None):
            conversation.title = content[:80]
        conversation.updated_at = utcnow()

        await self._db.flush()
        return run

    # ── claiming ─────────────────────────────────────────────────────────
    def _claimable(self) -> Any:
        """Runs no live process is executing: queued, or abandoned mid-run.

        The second half is what makes a claim a *takeover*. A run whose worker
        died is `RUNNING` with a heartbeat that stopped, and it is claimable
        for the same reason the reconciler is allowed to fail it — nobody is
        driving it. The reconciler still wins the race often, and that is
        fine: a failed run is a claimable run's alternative, not its enemy.
        """
        cutoff = utcnow() - timedelta(seconds=self._settings.run_stale_after_seconds)
        return or_(
            Run.status == RunStatus.QUEUED,
            (Run.status == RunStatus.RUNNING) & (Run.heartbeat_at < cutoff),
        )

    async def claim(self, run_id: UUID, *, worker_id: str) -> bool:
        """Take ownership of one run, or report that someone else has it.

        `FOR UPDATE SKIP LOCKED` over the candidate row, then an update of the
        row that survived. Two replicas racing for the same run take the lock
        in some order; the loser skips rather than blocks and gets `False`.
        The `UPDATE … WHERE` is not redundant with the lock — the lock
        serialises the readers, the predicate is what makes the second one see
        a row that no longer qualifies.

        `fencing_token` moves on every claim so a taken-over run's original
        worker can be told apart from its new one, which is what
        `_still_ours` reads on the way out.
        """
        candidate = (
            select(Run.id)
            .where(Run.id == run_id, self._claimable())
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        now = utcnow()
        result = await self._db.execute(
            update(Run)
            .where(Run.id == candidate)
            .values(
                status=RunStatus.RUNNING,
                worker_id=worker_id,
                started_at=now,
                heartbeat_at=now,
                fencing_token=int(now.timestamp() * 1000),
            )
            .returning(Run.id)
        )
        claimed = result.scalar_one_or_none() is not None
        await self._db.commit()
        return claimed

    async def claimable_runs(self, *, limit: int = 8) -> list[UUID]:
        """Runs this replica could pick up — the queue half of the claim.

        The direct hand-off in `post_message` covers the normal path and costs
        no latency; this covers the one it cannot, where the process that
        accepted the request died between the commit and the submit. Without
        it such a run sits `QUEUED` until the reconciler fails it, and the user
        is told a question they asked was never started.
        """
        result = await self._db.execute(
            select(Run.id)
            .where(self._claimable())
            .order_by(Run.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    # ── execution ────────────────────────────────────────────────────────
    async def execute_run(self, run_id: UUID, *, worker_id: str) -> None:
        if not await self.claim(run_id, worker_id=worker_id):
            # Another replica has it, or it is already finished. Either way
            # this process must not touch it: two executors on one run would
            # write two answers into one thread.
            log.info("run_not_claimed", run_id=str(run_id))
            return
        run = await self._db.get(Run, run_id)
        if run is None:  # pragma: no cover - claimed rows exist by definition
            return
        fencing_token = run.fencing_token

        await self._emit(run_id, "RUN_STARTED", {
            "run_id": str(run_id),
            "model": run.model_snapshot.get("model"),
            "connection": run.model_snapshot.get("connection_name"),
        })

        # Both are `SET NULL`, so a connection or an LLM config deleted between
        # this run being queued and being claimed leaves the pointer empty. That
        # is a narrow race — the row is normally written and executed within the
        # same second — but an `assert` would surface it as a bare crash, and
        # the run has to end in a terminal state either way.
        connection = (
            await self._db.get(DatabaseConnection, run.connection_id)
            if run.connection_id
            else None
        )
        llm_config = (
            await self._db.get(LlmConfig, run.llm_config_id)
            if run.llm_config_id
            else None
        )
        if connection is None or llm_config is None:
            missing = "data source" if connection is None else "model"
            run.status = RunStatus.FAILED
            run.error_code = "E_NOT_FOUND"
            run.error_message = f"The {missing} this run was using has been deleted."
            run.finished_at = utcnow()
            await self._db.commit()
            # The same terminal event every other ending emits, so the SPA needs
            # no new case and the SSE stream still closes.
            await self._emit(run_id, "RUN_FINISHED", {
                "status": run.status,
                "error_code": run.error_code,
                "repair_count": run.repair_count,
                "total_latency_ms": run.total_latency_ms,
            })
            await event_bus.close_run(run_id)
            return

        snapshot = await latest_snapshot(self._db, connection.id)
        # Loaded once per run, not per attempt: a repair regenerates against
        # the same schema block, and the layer is part of that block.
        semantic = await load_document(self._db, connection)
        # One lookup, two consequences: this run may not ask again, and its
        # question is the reply *plus* the question that reply answers.
        pending = await self._pending_clarification(run)
        state = RunState(
            run_id=run.id,
            conversation_id=run.conversation_id,
            owner_id=run.owner_id,
            connection_id=connection.id,
            question=await self._compose_question(run, pending),
            dialect=connection.database_type,
            max_rows=connection.max_rows,
            statement_timeout_ms=connection.statement_timeout_ms,
            disclosure_policy=connection.disclosure_policy,
            deadline_at=utcnow() + timedelta(seconds=self._settings.run_deadline_seconds),
        )

        connector = bind_connector(connection, self._box)

        deps = NodeDeps(
            llm_gateway=LiteLLMGateway.from_settings(self._settings),
            llm=resolve_llm(llm_config, self._box),
            connector=connector,
            snapshot=snapshot,
            history=await self._recent_history(run.conversation_id, connection.id),
            policy=policy_from_snapshot(snapshot, connection),
            emit=lambda t, d: self._emit(run_id, t, d),
            semantic=(semantic.model_dump(mode="json") if semantic else None),
            clarify_enabled=connection.clarify_enabled and pending is None,
            include_db_comments=connection.include_db_comments,
            # The knowledge store. Always built — an empty store simply never
            # matches, and the `match` node's miss path writes nothing and
            # alters no prompt — except when the reader has asked for a fresh
            # answer, where consulting it again would ignore them.
            matcher=build_matcher(self._db),
            templates_enabled=not run.skip_templates,
        )

        pipeline = AnalyticsPipeline(
            on_step=lambda seq, name, status, detail, ms: self._record_step(
                run_id, seq, name, status, detail, ms
            )
        )

        # Re-stamped here rather than trusted from `create_run`, because this
        # is the process that renders the bytes. A run queued by one replica
        # and claimed by another after a deploy would otherwise be filed under
        # the version of the prompt module that never touched it.
        run.prompt_version = self._prompt_version()

        try:
            state = await pipeline.run(state, deps)
        except RunTimeoutError:
            run.status = RunStatus.TIMED_OUT
            run.error_code = "E_TIMEOUT"
            run.error_message = "The run exceeded its time budget."
        except Exception as err:
            log.exception("run_crashed", run_id=str(run_id))
            run.status = RunStatus.FAILED
            run.error_code = "E_INTERNAL"
            run.error_message = str(err)[:500]
        finally:
            await connector.close()

        await self._finalise(run, state, fencing_token=fencing_token)

    # ── persistence of run output ────────────────────────────────────────
    async def _finalise(
        self, run: Run, state: RunState, *, fencing_token: int | None = None
    ) -> None:
        # What the *database* thinks, which is not what this session thinks:
        # sessions are `expire_on_commit=False`, so `run.status` here is the
        # value read when the run was claimed. A cancel that arrived at another
        # replica in the meantime is invisible to it.
        status_now, token_now = await self._authority(run.id)

        if fencing_token is not None and token_now != fencing_token:
            # This run was taken over — its heartbeat lapsed and another
            # process claimed it. That process is writing the answer now, so
            # writing ours too would put two answers in one thread.
            log.warning(
                "run_superseded", run_id=str(run.id),
                held=fencing_token, current=token_now,
            )
            return

        if status_now is not None and RunStatus(status_now).is_terminal:
            # Cancelled (or otherwise finished) elsewhere while we worked.
            # Adopt that verdict rather than overwriting it — every
            # `run.status == RUNNING` guard below then declines on its own,
            # and the results already paid for are still written, because a
            # cancelled run is a stopped run and not an erased one.
            run.status = status_now

        # The match verdict, before anything else this run produced. Written
        # here rather than in the node because a node never touches
        # persistence — and written for a *rejection* as readily as for a hit,
        # since the refusals are the numbers that say which grammars to teach
        # the binder next and how fast the store is rotting.
        if state.match_outcome:
            await record_hit(
                self._db,
                run_id=run.id,
                template_id=state.matched_template_id,
                outcome=state.match_outcome,
                matcher=state.match_kind or "LEXICAL",
                score=state.match_score,
                bound_params=state.bound_params,
            )

        for attempt in state.attempts:
            gq = GeneratedQuery(
                id=uuid.uuid4(),
                run_id=run.id,
                attempt_no=attempt.attempt_no,
                raw_sql=attempt.raw_sql,
                rewritten_sql=attempt.rewritten_sql,
                dialect=state.dialect,
                validation_status=attempt.report.status,
                validation_report=attempt.report.model_dump(),
                referenced_tables=attempt.report.referenced_tables,
                referenced_columns=attempt.report.referenced_columns,
            )
            self._db.add(gq)
            await self._db.flush()

            if attempt.rewritten_sql and state.execution is not None:
                self._db.add(
                    QueryExecution(
                        id=uuid.uuid4(),
                        generated_query_id=gq.id,
                        status="SUCCEEDED",
                        duration_ms=state.execution.duration_ms,
                        row_count=state.execution.row_count,
                        truncated=state.execution.truncated,
                        rows_scanned_estimate=state.execution.rows_scanned_estimate,
                        # ResultColumn is a slots dataclass and so has no
                        # __dict__; asdict is what actually serialises it.
                        result_schema=[
                            asdict(c) for c in state.execution.columns
                        ],
                    )
                )

        if state.execution is not None:
            artifact = Artifact(
                id=uuid.uuid4(),
                run_id=run.id,
                kind=ArtifactKind.TABLE,
                spec={
                    "columns": [
                        {"name": c.name, "db_type": c.db_type,
                         "semantic_type": c.semantic_type}
                        for c in state.execution.columns
                    ],
                    "rows": state.execution.rows,
                    "row_count": state.execution.row_count,
                    "truncated": state.execution.truncated,
                },
            )
            self._db.add(artifact)
            await self._db.flush()
            await self._emit(run.id, "ARTIFACT_CREATED", {
                "artifact_id": str(artifact.id), "kind": ArtifactKind.TABLE,
            })

        if state.chart is not None:
            chart_artifact = Artifact(
                id=uuid.uuid4(),
                run_id=run.id,
                kind=ArtifactKind.CHART,
                spec=state.chart,
            )
            self._db.add(chart_artifact)
            await self._db.flush()
            await self._emit(run.id, "ARTIFACT_CREATED", {
                "artifact_id": str(chart_artifact.id), "kind": ArtifactKind.CHART,
            })

        if state.kpi is not None:
            kpi_artifact = Artifact(
                id=uuid.uuid4(),
                run_id=run.id,
                kind=ArtifactKind.KPI,
                spec=state.kpi,
            )
            self._db.add(kpi_artifact)
            await self._db.flush()
            await self._emit(run.id, "ARTIFACT_CREATED", {
                "artifact_id": str(kpi_artifact.id), "kind": ArtifactKind.KPI,
            })

        if state.clarification is not None:
            # Persisted as an artifact rather than a column: it rides to the
            # SPA on the run detail the chat already fetches, exactly as the
            # ERROR artifact does, and needs no new endpoint or migration.
            self._db.add(
                Artifact(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    kind=ArtifactKind.CLARIFICATION,
                    spec=state.clarification.model_dump(mode="json"),
                )
            )

        if state.error is not None and run.status == RunStatus.RUNNING:
            run.status = RunStatus.FAILED
            run.error_code = state.error.code
            run.error_message = state.error.message
            self._db.add(
                Artifact(
                    id=uuid.uuid4(), run_id=run.id, kind=ArtifactKind.ERROR,
                    spec=state.error.model_dump(),
                )
            )
            await self._emit(run.id, "ERROR", state.error.model_dump())

        if state.answer:
            seq = await self._next_message_seq(run.conversation_id)
            assistant = Message(
                id=uuid.uuid4(),
                conversation_id=run.conversation_id,
                seq=seq,
                role=MessageRole.ASSISTANT,
                content=state.answer,
            )
            self._db.add(assistant)
            await self._db.flush()
            run.assistant_message_id = assistant.id

        if run.status == RunStatus.RUNNING:
            # A run that asked did not succeed and did not fail: it produced a
            # question, and the thread is waiting on the user. The status is
            # non-terminal by design, so `cancel` still works on it and the
            # reconciler — which sweeps QUEUED and RUNNING only — leaves it be.
            run.status = (
                RunStatus.NEEDS_CLARIFICATION
                if state.clarification is not None
                else RunStatus.SUCCEEDED
            )

        run.finished_at = utcnow()
        run.attempt_count = len(state.attempts)
        run.repair_count = state.repair_count
        run.llm_latency_ms = state.llm_latency_ms
        run.db_latency_ms = state.db_latency_ms
        run.prompt_tokens = state.prompt_tokens
        run.completion_tokens = state.completion_tokens
        if run.started_at:
            run.total_latency_ms = int(
                (run.finished_at - run.started_at).total_seconds() * 1000
            )
        await self._db.commit()

        await self._emit(run.id, "RUN_FINISHED", {
            "status": run.status,
            "error_code": run.error_code,
            "repair_count": run.repair_count,
            "total_latency_ms": run.total_latency_ms,
        })
        await event_bus.close_run(run.id)
        # Before Phase 6 nothing ever called this, so every event of every run
        # since boot stayed in memory behind a durable copy of itself. Safe
        # here because the SSE endpoint backfills from `run_events` before it
        # attaches: a client reconnecting after this reads the log instead.
        event_bus.forget(run.id)

    # ── reconciliation ───────────────────────────────────────────────────
    async def reconcile_stale(self) -> int:
        """A killed process must leave FAILED runs, never RUNNING forever."""
        cutoff = utcnow() - timedelta(seconds=self._settings.run_stale_after_seconds)
        result = await self._db.execute(
            update(Run)
            .where(
                Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
                (Run.heartbeat_at.is_(None)) | (Run.heartbeat_at < cutoff),
                Run.created_at < cutoff,
            )
            .values(
                status=RunStatus.FAILED,
                error_code="E_ORPHANED",
                error_message="The worker handling this run stopped responding.",
                finished_at=utcnow(),
            )
        )
        await self._db.commit()
        return result.rowcount or 0

    async def heartbeat(self, run_id: UUID) -> bool:
        """Say we are alive; find out whether we have been asked to stop.

        One statement for both, because they are the same round trip and the
        heartbeat already happens on a timer. That timer is therefore also the
        worst-case latency of a cross-replica cancel — the replica that owns
        the run learns about it here, and nowhere else.
        """
        result = await self._db.execute(
            update(Run)
            .where(Run.id == run_id)
            .values(heartbeat_at=utcnow())
            .returning(Run.cancel_requested)
        )
        cancel_requested = result.scalar_one_or_none()
        await self._db.commit()
        return bool(cancel_requested)

    async def cancel(self, run_id: UUID, owner_id: UUID) -> bool:
        """Record the cancellation. Stopping the work is the owner's job.

        The status goes terminal here so the user sees the run close
        immediately, and `cancel_requested` is what reaches the process
        actually executing it — which may be this one, may be another, and on
        a single replica is both. `_finalise` reads the terminal status back
        and declines to overwrite it, so the run stays cancelled even though
        its executor keeps going for another heartbeat or two.
        """
        run = await self._db.get(Run, run_id)
        if run is None or run.owner_id != owner_id:
            raise NotFoundError("Run not found.")
        if RunStatus(run.status).is_terminal:
            return False
        run.cancel_requested = True
        run.status = RunStatus.CANCELLED
        run.finished_at = utcnow()
        await self._db.commit()
        await self._emit(run_id, "RUN_FINISHED", {"status": RunStatus.CANCELLED})
        await event_bus.close_run(run_id)
        event_bus.forget(run_id)
        return True

    # ── helpers ──────────────────────────────────────────────────────────
    async def _emit(self, run_id: UUID, event_type: str, data: dict[str, Any]) -> None:
        seq = await event_bus.publish(run_id, event_type, data)
        if event_type in TRANSIENT_RUN_EVENTS:
            # Live only, deliberately: no row, no NOTIFY, no commit. See
            # `TRANSIENT_RUN_EVENTS` for the trade and its two consequences.
            return
        # Durable copy so a reconnecting client can replay from Last-Event-ID —
        # and, since Phase 6, so a replica that is not executing this run can
        # read the event at all.
        self._db.add(RunEventRow(run_id=run_id, seq=seq, type=event_type, data=data))
        try:
            # On this transaction, before this commit, deliberately: Postgres
            # holds a notification until commit and drops it on rollback, so a
            # listener elsewhere either sees the announcement *and* the row or
            # neither of them. See `infra/events/listener.py`.
            await notify_run_event(self._db, run_id, seq)
            await self._db.commit()
        except Exception:
            await self._db.rollback()

    async def _record_step(
        self, run_id: UUID, seq: int, name: str, status: str,
        detail: str | None, duration_ms: int,
    ) -> None:
        existing = await self._db.execute(
            select(RunStep).where(RunStep.run_id == run_id, RunStep.seq == seq)
        )
        step = existing.scalar_one_or_none()
        if step is None:
            step = RunStep(
                id=uuid.uuid4(), run_id=run_id, seq=seq, name=name,
                status=status, started_at=utcnow(),
            )
            self._db.add(step)
        step.status = status
        step.detail = detail
        if status != StepStatus.RUNNING:
            step.finished_at = utcnow()
            step.duration_ms = duration_ms
        await self._db.commit()

    async def _authority(self, run_id: UUID) -> tuple[str | None, int | None]:
        """`(status, fencing_token)` as the row has them, not as we remember.

        A fresh `SELECT` rather than `db.refresh`: the point is to read past
        this session's identity map, which `expire_on_commit=False` keeps
        populated with values from whenever the object was loaded.
        """
        result = await self._db.execute(
            select(Run.status, Run.fencing_token).where(Run.id == run_id)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else (None, None)

    async def _owned(self, model: type, entity_id: UUID, owner_id: UUID) -> Any:
        entity = await self._db.get(model, entity_id)
        if entity is None or entity.owner_id != owner_id:
            raise NotFoundError(f"{model.__name__} not found.")
        return entity

    async def _next_message_seq(self, conversation_id: UUID) -> int:
        result = await self._db.execute(
            select(Message.seq)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq.desc())
            .limit(1)
        )
        return (result.scalar_one_or_none() or 0) + 1

    async def _pending_clarification(self, run: Run) -> Run | None:
        """The run immediately before this one, if it ended in a question.

        Two things hang off this, and they are the same fact:

        * It stops a clarification loop. The model cannot be trusted to notice
          from the transcript that it already asked, and a user who has just
          answered must get an answer — so the second run in an exchange never
          gets to ask again, whatever it thinks of the reply.
        * It is what `_compose_question` needs to put the reply back together
          with the question it answers.

        The whole row, not just the status, because the second use needs the
        messages behind it. One query either way.
        """
        result = await self._db.execute(
            select(Run)
            .where(Run.conversation_id == run.conversation_id, Run.id != run.id)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        previous = result.scalar_one_or_none()
        if previous is None or previous.status != RunStatus.NEEDS_CLARIFICATION:
            return None
        return previous

    async def _question_of(self, run: Run) -> str:
        message = await self._db.get(Message, run.user_message_id)
        return (message.content if message else "") or ""

    async def _compose_question(self, run: Run, pending: Run | None) -> str:
        """The question this run actually has to answer.

        A clarification is not resumed — the reply arrives as an ordinary new
        run — so without this the pipeline is handed the reply *alone*. "Total
        sales (order amount)" is a complete, answerable question on its own,
        and the generator answered it on its own: one figure, when the question
        it replied to was "who are the best sellers?". The transcript did carry
        both turns, but as passive context, against an `_SQL_RULES` line that
        explicitly says to answer exactly what is asked at the granularity
        asked — so the history lost to the rule every time.

        Composing here rather than in a prompt is deliberate. `GENERATE_SYSTEM`
        stays byte-identical (eval Round 2: additions to it cost accuracy), and
        every node downstream benefits from the same fix — `retrieve` matches
        tables against the subject again, and `present` narrates the question
        the user actually asked.

        The frame is English because the surrounding prompt is; the user's own
        words are never translated or paraphrased, only quoted and trimmed.
        """
        reply = await self._question_of(run)
        if pending is None:
            return reply

        original = await self._question_of(pending)
        if not original or not reply:
            return reply or original

        asked = ""
        if pending.assistant_message_id is not None:
            message = await self._db.get(Message, pending.assistant_message_id)
            asked = (message.content if message else "") or ""

        parts = [original[:_QUESTION_CHARS]]
        if asked:
            parts.append(f"(Clarification asked: {asked[:_QUESTION_CHARS]}")
        else:
            parts.append("(A clarification was asked")
        parts.append(
            f"The user answered: {reply[:_QUESTION_CHARS]}\n"
            "That answer resolves the ambiguity in the question above. It is "
            "not itself the question — answer the original question, read "
            "under that answer.)"
        )
        return "\n".join(parts)

    async def _recent_history(
        self,
        conversation_id: UUID,
        connection_id: UUID,
        *,
        limit: int = 6,
        drop_latest: bool = True,
    ) -> list[dict[str, str]]:
        """The recent turns of one thread, against one connection.

        The single place history is assembled — the run path and the follow-up
        suggestions both come here, because a transcript sent to a model is a
        disclosure whichever prompt it lands in, and two builders would mean
        two chances to forget that.

        `connection_id` is a filter, not decoration. `_bind_connection` stops
        new threads from mixing connections, but rows written before it, or by
        any future path that sets `Run.connection_id` directly, would still
        carry one database's answers into another's prompt. A turn that cannot
        be attributed to a run on *this* connection is dropped: the same
        fail-closed reading the rest of the disclosure code uses.

        `drop_latest` removes the message that started the current run — it is
        the question being asked, not context for it. Suggestions have no such
        message and pass False.
        """
        result = await self._db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq.desc())
            .limit(limit + (1 if drop_latest else 0))
        )
        rows = list(result.scalars())
        if drop_latest:
            rows = rows[1:]
        if not rows:
            return []

        runs = await self._runs_behind([r.id for r in rows])
        kept = [r for r in rows if r.id in runs and runs[r.id][0] == connection_id]
        sql_by_message = await self._sql_behind(
            [r.id for r in kept if r.role == MessageRole.ASSISTANT]
        )
        turns: list[dict[str, str]] = []
        for row in reversed(kept):
            turn = {"role": row.role.lower(), "content": row.content or ""}
            if row.role == MessageRole.ASSISTANT:
                # A clarifying question is the assistant's turn but not an
                # answer: it is asked before any SQL runs, so it holds no
                # result data and survives every disclosure policy. Without
                # the marker it would be withheld as prose, and the user's
                # next message — the reply to it — would read as a non
                # sequitur.
                if runs[row.id][1] == RunStatus.NEEDS_CLARIFICATION:
                    turn["kind"] = "clarification"
                sql = sql_by_message.get(row.id)
                if sql:
                    turn["sql"] = sql
            turns.append(turn)
        return turns

    async def _runs_behind(
        self, message_ids: list[UUID]
    ) -> dict[UUID, tuple[UUID, str]]:
        """`message_id -> (connection_id, run status)`, for both roles.

        A message belongs to the run that wrote it: the user's as
        `user_message_id`, the assistant's as `assistant_message_id`. One query
        covers both, and a message matching neither is simply absent from the
        map — which the caller reads as "cannot attribute" and drops.
        """
        if not message_ids:
            return {}
        result = await self._db.execute(
            select(
                Run.user_message_id,
                Run.assistant_message_id,
                Run.connection_id,
                Run.status,
            ).where(
                or_(
                    Run.user_message_id.in_(message_ids),
                    Run.assistant_message_id.in_(message_ids),
                )
            )
        )
        attribution: dict[UUID, tuple[UUID, str]] = {}
        for user_message_id, assistant_message_id, conn_id, status in result.all():
            for message_id in (user_message_id, assistant_message_id):
                if message_id is not None:
                    attribution[message_id] = (conn_id, status)
        return attribution

    async def _sql_behind(
        self, assistant_message_ids: list[UUID]
    ) -> dict[UUID, str]:
        """The SQL that produced each of those answers, where one is known.

        The assistant *message* only ever holds `state.answer` — the prose. The
        statement behind it lives on `generated_queries`, so a follow-up turn
        can only build on the previous query if it is joined back in here.

        `rewritten_sql` is non-null exactly when the guard validated that
        attempt, so filtering on it skips drafts that never ran. Ordering by
        `attempt_no` and letting later rows win takes the last attempt that
        passed the guard — which is the presented one except in the rare case
        where `_restore_superseded` put an earlier result back and a later
        attempt had validated but failed in the database. Wrong by one attempt
        there, and it is a hint for the next question rather than anything the
        run depends on.
        """
        if not assistant_message_ids:
            return {}
        result = await self._db.execute(
            select(Run.assistant_message_id, GeneratedQuery.rewritten_sql)
            .join(GeneratedQuery, GeneratedQuery.run_id == Run.id)
            .where(
                Run.assistant_message_id.in_(assistant_message_ids),
                GeneratedQuery.rewritten_sql.is_not(None),
            )
            .order_by(GeneratedQuery.attempt_no)
        )
        return {
            message_id: sql
            for message_id, sql in result.all()
            if message_id is not None and sql
        }

    # ── follow-up suggestions ────────────────────────────────────────────
    async def suggest_followups(
        self, *, conversation_id: UUID, owner_id: UUID, limit: int = 3
    ) -> list[str]:
        """Propose a few natural-language follow-up questions for a thread.

        Grounded in the connection's schema snapshot and the recent
        conversation, so every suggestion is answerable over the same tables.
        Deliberately best-effort: a missing schema, an unconfigured model, or a
        provider error yields an empty list rather than disturbing the chat.

        Everything it sends is gated by the connection's disclosure policy —
        the schema description by `HintBudget`, the transcript by
        `disclose_history`. This prompt reaches the same third-party model the
        run path does; being a convenience feature buys it no exemption.
        """
        conversation = await self._db.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_id != owner_id:
            raise NotFoundError("Conversation not found.")

        conn_id = conversation.default_connection_id
        llm_id = conversation.default_llm_config_id
        if conn_id is None or llm_id is None:
            return []

        connection = await self._db.get(DatabaseConnection, conn_id)
        llm_config = await self._db.get(LlmConfig, llm_id)
        if connection is None or llm_config is None:
            return []

        snapshot = await latest_snapshot(self._db, conn_id)
        tables = snapshot.get("tables", [])
        if not tables:
            return []

        history = await self._recent_history(
            conversation_id, conn_id, limit=8, drop_latest=False
        )
        # Only suggest once the thread has at least one answered turn.
        if not any(m["role"] == "assistant" for m in history):
            return []

        # Rendered by the same function the run path uses, so the transcript
        # reaches this prompt on the same disclosure terms — this call is not
        # a question the user asked, it fires on its own when the SPA refreshes
        # a thread, and it was the one path that sent the raw messages.
        transcript = _render_history(history, connection.disclosure_policy)
        system = (
            "You help a business user explore a SQL database in plain language. "
            "Given the database schema and the conversation so far, propose "
            f"{limit} follow-up questions the user is likely to ask next. Rules: "
            "each question must be answerable with SQL over the tables shown; "
            "keep each under 12 words; make them specific to this schema, not "
            "generic; never repeat a question already asked. Output exactly "
            f"{limit} questions, one per line, with no numbering, quotes, or any "
            "other text."
        )
        user = (
            f"Database schema:\n"
            f"{_describe_schema(tables, connection.disclosure_policy)}\n\n"
            f"{transcript}"
        )

        try:
            gateway = LiteLLMGateway.from_settings(self._settings)
            completion = await gateway.complete(
                resolve_llm(llm_config, self._box),
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
                ],
            )
        except Exception:
            log.warning(
                "suggestions_failed", conversation_id=str(conversation_id)
            )
            return []

        return _parse_suggestions(completion.text, limit, history)


def _bind_connection(
    conversation: Conversation, connection_id: UUID, *, transcript_empty: bool
) -> None:
    """Hold a conversation to one database once it has started.

    A per-message `connection_id` may differ from the conversation's — the API
    has always accepted the override, and the run snapshots what it used. But
    history is keyed on the conversation, so a thread whose turns ran against
    two connections carries one connection's answers into the other's prompt,
    under the other's disclosure policy. A connection tightened to NONE would
    then be told what a FULL connection returned, by a route that never
    consults either policy.

    The SPA already behaves this way — the pickers lock once the transcript is
    non-empty — so this only closes the API path that bypasses them. Switching
    is still free while nothing has been said, and the conversation adopts the
    connection it is switched to rather than leaving the default stale.
    """
    if conversation.default_connection_id is None:
        # `None` means one of two things, and the transcript says which.
        # Nothing asked yet: the thread has not chosen a database, so it adopts
        # this one. Something asked: the database it *had* was deleted, and
        # `ON DELETE SET NULL` released this column (migration 0014). Adopting
        # a replacement there would silently continue one database's
        # conversation against another — the exact thing the pin below exists
        # to prevent, arriving through the back door. The transcript stays
        # readable; it just cannot be added to.
        if not transcript_empty:
            raise ValidationError(_RELEASED, conversation_id=str(conversation.id))
        conversation.default_connection_id = connection_id
        return
    if conversation.default_connection_id == connection_id:
        return
    if not transcript_empty:
        raise ValidationError(
            "This conversation is already bound to a different database "
            "connection. Start a new conversation to ask against another one.",
            conversation_id=str(conversation.id),
        )
    conversation.default_connection_id = connection_id


_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")


def _parse_suggestions(
    text: str, limit: int, history: list[dict[str, str]]
) -> list[str]:
    """Turn a model's free-text reply into clean, de-duplicated questions.

    The model is asked for one question per line, but real replies also carry
    numbering, bullets, or stray quotes; those are stripped. Anything already
    asked in the thread is dropped so a suggestion never echoes the user.
    """
    asked = {m["content"].strip().lower() for m in history}
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = _LIST_MARKER.sub("", raw.strip()).strip().strip('"').strip()
        if not line:
            continue
        key = line.lower()
        if key in seen or key in asked:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return out

