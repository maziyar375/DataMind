from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    ArtifactRead,
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    GeneratedQueryRead,
    MessageAccepted,
    MessageCreate,
    MessageRead,
    RunRead,
    RunStepRead,
)
from app.core.errors import NotFoundError
from app.domain.value_objects import RunStatus
from app.infra.db.models import (
    Artifact,
    Conversation,
    DatabaseConnection,
    GeneratedQuery,
    LlmConfig,
    Message,
    Run,
    RunEventRow,
    RunStep,
)
from app.infra.events.bus import event_bus
from app.services.run_service import RunService

router = APIRouter(tags=["conversations"])


# ── conversations ────────────────────────────────────────────────────────
async def _owned_conversation(db, conversation_id: UUID, ctx) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.owner_id == ctx.user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("Conversation not found.")
    return row


@router.get("/conversations", response_model=list[ConversationRead])
async def list_conversations(ctx: CtxDep, db: DbDep) -> list[ConversationRead]:
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.owner_id == ctx.user_id,
            Conversation.status == "ACTIVE",
        )
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    )
    conversations = list(result.scalars())
    if not conversations:
        return []

    ids = [c.id for c in conversations]
    counts = await db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(Message.conversation_id.in_(ids))
        .group_by(Message.conversation_id)
    )
    count_map = dict(counts.all())

    previews = await db.execute(
        select(Message.conversation_id, Message.content, Message.seq)
        .where(Message.conversation_id.in_(ids))
        .order_by(Message.conversation_id, Message.seq.desc())
    )
    preview_map: dict[UUID, str] = {}
    for conv_id, content, _seq in previews.all():
        preview_map.setdefault(conv_id, (content or "")[:120])

    out: list[ConversationRead] = []
    for conversation in conversations:
        data = ConversationRead.model_validate(conversation)
        data.message_count = count_map.get(conversation.id, 0)
        data.preview = preview_map.get(conversation.id)
        out.append(data)
    return out


@router.post(
    "/conversations", response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreate, ctx: CtxDep, db: DbDep
) -> ConversationRead:
    connection_id = payload.connection_id
    llm_config_id = payload.llm_config_id

    # Fall back to whatever the user marked default, so a new chat is usable
    # without making them pick twice.
    if connection_id is None:
        result = await db.execute(
            select(DatabaseConnection.id)
            .where(DatabaseConnection.owner_id == ctx.user_id)
            .order_by(DatabaseConnection.is_default.desc(), DatabaseConnection.created_at)
            .limit(1)
        )
        connection_id = result.scalar_one_or_none()
    if llm_config_id is None:
        result = await db.execute(
            select(LlmConfig.id)
            .where(LlmConfig.owner_id == ctx.user_id)
            .order_by(LlmConfig.is_default.desc(), LlmConfig.created_at)
            .limit(1)
        )
        llm_config_id = result.scalar_one_or_none()

    conversation = Conversation(
        id=uuid.uuid4(),
        owner_id=ctx.user_id,
        title=payload.title or "New chat",
        default_connection_id=connection_id,
        default_llm_config_id=llm_config_id,
    )
    db.add(conversation)
    await db.flush()
    return ConversationRead.model_validate(conversation)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: UUID, payload: ConversationUpdate, ctx: CtxDep, db: DbDep
) -> ConversationRead:
    conversation = await _owned_conversation(db, conversation_id, ctx)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(conversation, field, value)
    await db.flush()
    # `updated_at` has an onupdate, so the flush expires it. Refresh here, in
    # the async context, or pydantic's attribute read below triggers a lazy
    # load outside the greenlet and raises MissingGreenlet — a 500 on what is
    # just a rename.
    await db.refresh(conversation)
    return ConversationRead.model_validate(conversation)


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(conversation_id: UUID, ctx: CtxDep, db: DbDep) -> None:
    conversation = await _owned_conversation(db, conversation_id, ctx)
    await db.delete(conversation)


# ── messages ─────────────────────────────────────────────────────────────
@router.get(
    "/conversations/{conversation_id}/messages", response_model=list[MessageRead]
)
async def list_messages(
    conversation_id: UUID, ctx: CtxDep, db: DbDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MessageRead]:
    """Messages plus the run that produced each assistant turn.

    Steps come from the persisted `run_steps` table rather than from replayed
    events, which is what makes the step chips survive a page refresh.
    """
    await _owned_conversation(db, conversation_id, ctx)

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.seq)
        .limit(limit)
    )
    messages = list(result.scalars())
    if not messages:
        return []

    runs_result = await db.execute(
        select(Run).where(Run.conversation_id == conversation_id)
    )
    runs = list(runs_result.scalars())
    by_assistant = {r.assistant_message_id: r for r in runs if r.assistant_message_id}
    by_user = {r.user_message_id: r for r in runs}

    hydrated = {r.id: await _hydrate_run(db, r) for r in runs}

    out: list[MessageRead] = []
    for message in messages:
        data = MessageRead.model_validate(message)
        run = by_assistant.get(message.id)
        if run is None and message.role == "USER":
            candidate = by_user.get(message.id)
            # Attach an in-flight or failed run to the user turn so the UI has
            # somewhere to render progress before an answer exists.
            if candidate is not None and candidate.assistant_message_id is None:
                run = candidate
        if run is not None:
            data.run = hydrated.get(run.id)
        out.append(data)
    return out


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_message(
    conversation_id: UUID, payload: MessageCreate,
    ctx: CtxDep, db: DbDep, settings: SettingsDep, request: Request,
) -> MessageAccepted:
    service = RunService(db, settings)
    run = await service.create_run(
        owner_id=ctx.user_id,
        conversation_id=conversation_id,
        content=payload.content,
        connection_id=payload.connection_id,
        llm_config_id=payload.llm_config_id,
    )
    await db.commit()

    executor = request.app.state.run_executor
    await executor.submit(run.id)

    return MessageAccepted(run_id=run.id, message_id=run.user_message_id)


# ── runs ─────────────────────────────────────────────────────────────────
async def _owned_run(db, run_id: UUID, ctx) -> Run:
    result = await db.execute(
        select(Run).where(Run.id == run_id, Run.owner_id == ctx.user_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise NotFoundError("Run not found.")
    return run


async def _hydrate_run(db, run: Run) -> RunRead:
    steps = await db.execute(
        select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.seq)
    )
    artifacts = await db.execute(
        select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.created_at)
    )
    queries = await db.execute(
        select(GeneratedQuery)
        .where(GeneratedQuery.run_id == run.id)
        .order_by(GeneratedQuery.attempt_no)
    )
    data = RunRead.model_validate(run)
    data.steps = [RunStepRead.model_validate(s) for s in steps.scalars()]
    data.artifacts = [ArtifactRead.model_validate(a) for a in artifacts.scalars()]
    data.queries = [GeneratedQueryRead.model_validate(q) for q in queries.scalars()]
    return data


@router.get("/runs/{run_id}", response_model=RunRead)
async def get_run(run_id: UUID, ctx: CtxDep, db: DbDep) -> RunRead:
    run = await _owned_run(db, run_id, ctx)
    return await _hydrate_run(db, run)


@router.get("/runs/{run_id}/sql", response_model=list[GeneratedQueryRead])
async def get_run_sql(run_id: UUID, ctx: CtxDep, db: DbDep) -> list[GeneratedQueryRead]:
    await _owned_run(db, run_id, ctx)
    result = await db.execute(
        select(GeneratedQuery)
        .where(GeneratedQuery.run_id == run_id)
        .order_by(GeneratedQuery.attempt_no)
    )
    return [GeneratedQueryRead.model_validate(q) for q in result.scalars()]


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    run_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, request: Request
) -> dict[str, bool]:
    await request.app.state.run_executor.cancel(run_id)
    cancelled = await RunService(db, settings).cancel(run_id, ctx.user_id)
    return {"cancelled": cancelled}


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: UUID, ctx: CtxDep, db: DbDep, request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE with replay.

    `Last-Event-ID` takes precedence over `?after=`, so a browser reconnect
    resumes without the client having to track anything itself.
    """
    run = await _owned_run(db, run_id, ctx)

    start_from = after
    if last_event_id:
        try:
            start_from = int(last_event_id)
        except ValueError:
            start_from = after

    terminal = RunStatus(run.status).is_terminal

    async def generate():
        if terminal:
            # The run already finished; replay from the durable log and close.
            result = await db.execute(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id, RunEventRow.seq > start_from)
                .order_by(RunEventRow.seq)
            )
            for row in result.scalars():
                yield _sse(row.seq, row.type, row.data)
            return

        try:
            async for event in event_bus.subscribe(run_id, after_seq=start_from):
                if await request.is_disconnected():
                    break
                yield _sse(event["seq"], event["type"], event["data"])
                if event["type"] == "RUN_FINISHED":
                    break
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/events/poll")
async def poll_events(
    run_id: UUID, ctx: CtxDep, db: DbDep, after: int = Query(default=0, ge=0)
) -> list[dict[str, Any]]:
    """Polling fallback for environments where SSE is proxied away."""
    await _owned_run(db, run_id, ctx)
    result = await db.execute(
        select(RunEventRow)
        .where(RunEventRow.run_id == run_id, RunEventRow.seq > after)
        .order_by(RunEventRow.seq)
        .limit(500)
    )
    return [
        {
            "seq": row.seq, "type": row.type, "data": row.data,
            "at": row.at.isoformat() if row.at else None,
        }
        for row in result.scalars()
    ]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactRead)
async def get_artifact(
    artifact_id: UUID, ctx: CtxDep, db: DbDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
) -> ArtifactRead:
    result = await db.execute(
        select(Artifact, Run)
        .join(Run, Run.id == Artifact.run_id)
        .where(Artifact.id == artifact_id, Run.owner_id == ctx.user_id)
    )
    pair = result.first()
    if pair is None:
        raise NotFoundError("Artifact not found.")

    artifact, _run = pair
    data = ArtifactRead.model_validate(artifact)
    if artifact.kind == "TABLE" and isinstance(artifact.spec.get("rows"), list):
        rows = artifact.spec["rows"]
        data.spec = {
            **artifact.spec,
            "rows": rows[offset : offset + limit],
            "offset": offset,
            "total_rows": len(rows),
        }
    return data


def _sse(seq: int, event_type: str, data: dict[str, Any]) -> str:
    payload = json.dumps({"type": event_type, "data": data, "seq": seq})
    return f"id: {seq}\nevent: {event_type}\ndata: {payload}\n\n"
