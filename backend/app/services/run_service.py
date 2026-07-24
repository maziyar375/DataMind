"""Run lifecycle: creation, execution, reconciliation.

Durability comes from the `runs` table plus a heartbeat rather than from a
broker. The swap point for Celery is `RunExecutor`; nothing here knows how a
run gets scheduled.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import NotFoundError, RunTimeoutError
from app.core.logging import get_logger
from app.domain.ports.llm import ChatMessage, ProviderCapabilities, ResolvedLLM
from app.domain.value_objects import (
    ArtifactKind,
    DatabaseKind,
    MessageRole,
    RunStatus,
    StepStatus,
)
from app.infra.connectors.factory import build_connector
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
    SchemaSnapshotRow,
)
from app.infra.events.bus import event_bus
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.pipeline.nodes import NodeDeps, _describe_schema
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.state import RunState
from app.sqlguard import GuardPolicy

log = get_logger(__name__)


class RunService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._box = AesGcmSecretBox(
            settings.secret_box_key.get_secret_value(),
            settings.secret_box_key_version,
        )

    # ── creation ─────────────────────────────────────────────────────────
    async def create_run(
        self,
        *,
        owner_id: UUID,
        conversation_id: UUID,
        content: str,
        connection_id: UUID | None,
        llm_config_id: UUID | None,
    ) -> Run:
        conversation = await self._db.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_id != owner_id:
            raise NotFoundError("Conversation not found.")

        conn_id = connection_id or conversation.default_connection_id
        llm_id = llm_config_id or conversation.default_llm_config_id
        if conn_id is None:
            raise NotFoundError("This conversation has no database connection.")
        if llm_id is None:
            raise NotFoundError("This conversation has no model configured.")

        connection = await self._owned(DatabaseConnection, conn_id, owner_id)
        llm_config = await self._owned(LlmConfig, llm_id, owner_id)

        next_seq = await self._next_message_seq(conversation_id)
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
            prompt_version=self._settings.prompt_version,
            status=RunStatus.QUEUED,
        )
        self._db.add(run)

        if conversation.title in ("New chat", "", None):
            conversation.title = content[:80]
        conversation.updated_at = utcnow()

        await self._db.flush()
        return run

    # ── execution ────────────────────────────────────────────────────────
    async def execute_run(self, run_id: UUID, *, worker_id: str) -> None:
        run = await self._db.get(Run, run_id)
        if run is None:
            return
        if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
            return

        run.status = RunStatus.RUNNING
        run.worker_id = worker_id
        run.started_at = utcnow()
        run.heartbeat_at = utcnow()
        run.fencing_token = int(utcnow().timestamp() * 1000)
        await self._db.commit()

        await self._emit(run_id, "RUN_STARTED", {
            "run_id": str(run_id),
            "model": run.model_snapshot.get("model"),
            "connection": run.model_snapshot.get("connection_name"),
        })

        connection = await self._db.get(DatabaseConnection, run.connection_id)
        llm_config = await self._db.get(LlmConfig, run.llm_config_id)
        assert connection is not None and llm_config is not None

        snapshot = await self._latest_snapshot(connection.id)
        state = RunState(
            run_id=run.id,
            conversation_id=run.conversation_id,
            owner_id=run.owner_id,
            connection_id=connection.id,
            question=await self._question_of(run),
            dialect=connection.database_type,
            max_rows=connection.max_rows,
            statement_timeout_ms=connection.statement_timeout_ms,
            disclosure_policy=connection.disclosure_policy,
            deadline_at=utcnow() + timedelta(seconds=self._settings.run_deadline_seconds),
        )

        connector = build_connector(
            kind=connection.database_type,
            host=connection.host,
            port=connection.port,
            database=connection.database_name,
            username=connection.username,
            password=self._box.decrypt(
                connection.encrypted_password, aad=f"connection:{connection.id}"
            ),
            ssl_mode=connection.ssl_mode,
        )

        deps = NodeDeps(
            llm_gateway=LiteLLMGateway(
                timeout_seconds=self._settings.llm_request_timeout_seconds
            ),
            llm=self._resolve_llm(llm_config),
            connector=connector,
            snapshot=snapshot,
            history=await self._recent_history(run.conversation_id),
            policy=_policy_from_snapshot(snapshot, connection),
            emit=lambda t, d: self._emit(run_id, t, d),
        )

        pipeline = AnalyticsPipeline(
            on_step=lambda seq, name, status, detail, ms: self._record_step(
                run_id, seq, name, status, detail, ms
            )
        )

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

        await self._finalise(run, state)

    # ── persistence of run output ────────────────────────────────────────
    async def _finalise(self, run: Run, state: RunState) -> None:
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
            run.status = RunStatus.SUCCEEDED

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

    async def heartbeat(self, run_id: UUID) -> None:
        await self._db.execute(
            update(Run).where(Run.id == run_id).values(heartbeat_at=utcnow())
        )
        await self._db.commit()

    async def cancel(self, run_id: UUID, owner_id: UUID) -> bool:
        run = await self._db.get(Run, run_id)
        if run is None or run.owner_id != owner_id:
            raise NotFoundError("Run not found.")
        if RunStatus(run.status).is_terminal:
            return False
        run.status = RunStatus.CANCELLED
        run.finished_at = utcnow()
        await self._db.commit()
        await self._emit(run_id, "RUN_FINISHED", {"status": RunStatus.CANCELLED})
        await event_bus.close_run(run_id)
        return True

    # ── helpers ──────────────────────────────────────────────────────────
    async def _emit(self, run_id: UUID, event_type: str, data: dict[str, Any]) -> None:
        seq = await event_bus.publish(run_id, event_type, data)
        # Durable copy so a reconnecting client can replay from Last-Event-ID.
        self._db.add(RunEventRow(run_id=run_id, seq=seq, type=event_type, data=data))
        try:
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

    async def _question_of(self, run: Run) -> str:
        message = await self._db.get(Message, run.user_message_id)
        return (message.content if message else "") or ""

    async def _recent_history(
        self, conversation_id: UUID, limit: int = 6
    ) -> list[dict[str, str]]:
        result = await self._db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq.desc())
            .limit(limit + 1)
        )
        rows = list(result.scalars())[1:]  # drop the message that started this run
        return [
            {"role": r.role.lower(), "content": r.content or ""}
            for r in reversed(rows)
        ]

    # ── follow-up suggestions ────────────────────────────────────────────
    async def suggest_followups(
        self, *, conversation_id: UUID, owner_id: UUID, limit: int = 3
    ) -> list[str]:
        """Propose a few natural-language follow-up questions for a thread.

        Grounded in the connection's schema snapshot and the recent
        conversation, so every suggestion is answerable over the same tables.
        Deliberately best-effort: a missing schema, an unconfigured model, or a
        provider error yields an empty list rather than disturbing the chat.
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

        snapshot = await self._latest_snapshot(conn_id)
        tables = snapshot.get("tables", [])
        if not tables:
            return []

        history = await self._history_for_suggestions(conversation_id)
        # Only suggest once the thread has at least one answered turn.
        if not any(m["role"] == "assistant" for m in history):
            return []

        transcript = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}" for m in history
        )
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
            f"Database schema:\n{_describe_schema(tables)}\n\n"
            f"Conversation so far:\n{transcript}"
        )

        try:
            gateway = LiteLLMGateway(
                timeout_seconds=self._settings.llm_request_timeout_seconds
            )
            completion = await gateway.complete(
                self._resolve_llm(llm_config),
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

    async def _history_for_suggestions(
        self, conversation_id: UUID, limit: int = 8
    ) -> list[dict[str, str]]:
        result = await self._db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq.desc())
            .limit(limit)
        )
        rows = list(reversed(list(result.scalars())))
        return [
            {"role": r.role.lower(), "content": content}
            for r in rows
            if (content := (r.content or "").strip())
        ]

    async def _latest_snapshot(self, connection_id: UUID) -> dict[str, Any]:
        result = await self._db.execute(
            select(SchemaSnapshotRow)
            .where(SchemaSnapshotRow.connection_id == connection_id)
            .order_by(SchemaSnapshotRow.version.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {"tables": [], "relationships": [], "dialect": "postgres"}
        return {
            "tables": row.tables,
            "relationships": row.relationships,
            "dialect": row.dialect,
        }

    def _resolve_llm(self, config: LlmConfig) -> ResolvedLLM:
        api_key = ""
        if config.encrypted_api_key:
            api_key = self._box.decrypt(
                config.encrypted_api_key, aad=f"llm_config:{config.id}"
            )
        caps = config.capabilities or {}
        return ResolvedLLM(
            config_id=config.id,
            provider=config.provider,
            model=config.model,
            base_url=config.base_url,
            api_key=api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            capabilities=ProviderCapabilities(
                supports_structured_output=caps.get("supports_structured_output", False),
                supports_streaming=caps.get("supports_streaming", True),
            ),
        )


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


def _policy_from_snapshot(
    snapshot: dict[str, Any], connection: DatabaseConnection
) -> GuardPolicy:
    allowed_tables: set[str] = set()
    allowed_columns: dict[str, set[str]] = {}
    for table in snapshot.get("tables", []):
        qualified = f"{table['schema']}.{table['name']}".lower()
        allowed_tables.add(qualified)
        allowed_columns[qualified] = {
            c["name"].lower() for c in table.get("columns", [])
        }
    return GuardPolicy(
        # sqlglot names the SQL Server dialect `tsql`, not `mssql`, so the
        # connection's own kind cannot be handed over unmapped.
        dialect=DatabaseKind(connection.database_type).sqlglot_dialect,
        max_rows=connection.max_rows,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
    )
