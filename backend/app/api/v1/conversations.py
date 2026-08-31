from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    AnswerFeedbackRead,
    AnswerFeedbackWrite,
    ArtifactRead,
    ChartOptionRead,
    ChartRedrawRead,
    ChartRedrawRequest,
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    GeneratedQueryRead,
    MessageAccepted,
    MessageCreate,
    MessageRead,
    RunKnowledge,
    RunRead,
    RunStepRead,
    SuggestionsRead,
)
from app.core.errors import NotFoundError, ValidationError
from app.domain.value_objects import RunStatus
from app.infra.db.models import (
    AnswerFeedback,
    Artifact,
    Conversation,
    GeneratedQuery,
    KnowledgeTemplateHit,
    KnowledgeTemplateRow,
    Message,
    Run,
    RunEventRow,
    RunStep,
    SemanticLayerRow,
)
from app.infra.events.bus import event_bus
from app.services.knowledge_service import FeedbackService, record_hit
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
    # No auto-default: a conversation stores exactly the database and model the
    # user chose (or nothing yet). The chooser in the chat header is where that
    # choice is made, and a message cannot be sent until both are set.
    conversation = Conversation(
        id=uuid.uuid4(),
        owner_id=ctx.user_id,
        title=payload.title or "New chat",
        default_connection_id=payload.connection_id,
        default_llm_config_id=payload.llm_config_id,
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
        skip_templates=payload.skip_templates,
    )
    await db.commit()

    executor = request.app.state.run_executor
    await executor.submit(run.id)

    return MessageAccepted(run_id=run.id, message_id=run.user_message_id)


@router.get(
    "/conversations/{conversation_id}/suggestions",
    response_model=SuggestionsRead,
)
async def suggest_followups(
    conversation_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep,
) -> SuggestionsRead:
    """Model-proposed follow-up questions, grounded in schema + this thread.

    Best-effort: returns an empty list (never an error) when there is no
    schema, no model, or the provider is unavailable.
    """
    service = RunService(db, settings)
    suggestions = await service.suggest_followups(
        conversation_id=conversation_id, owner_id=ctx.user_id
    )
    return SuggestionsRead(suggestions=suggestions)


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
    data.knowledge = await _knowledge(db, run, data.queries)
    return data


async def _knowledge(db, run: Run, queries: list[GeneratedQueryRead]) -> RunKnowledge:
    """Which of the three tiers this answer earned, and the evidence for it.

    Computed on read rather than stamped on the run row, for the reason every
    other derived thing in this codebase is: the semantic layer moves, and an
    answer's *Grounded* claim is a statement about what is described now.

    The order matters. **Verified** is a fact about this run — it was answered
    from a template — and outranks everything. **Grounded** is a fact about the
    tables it touched. **Generated** is the default and carries no chip at all:
    the moment ordinary answers wear a warning, the badge system is noise.
    """
    hits = await db.execute(
        select(KnowledgeTemplateHit)
        .where(KnowledgeTemplateHit.run_id == run.id)
        .order_by(KnowledgeTemplateHit.created_at)
    )
    mine = await db.execute(
        select(AnswerFeedback).where(
            AnswerFeedback.run_id == run.id, AnswerFeedback.user_id == run.owner_id
        )
    )
    feedback = mine.scalar_one_or_none()
    given = AnswerFeedbackRead.model_validate(feedback) if feedback else None
    rows = list(hits.scalars())
    short_circuit = next((h for h in rows if h.outcome == "SHORT_CIRCUIT"), None)
    overridden = any(h.outcome == "OVERRIDDEN_BY_USER" for h in rows)

    if short_circuit is not None:
        question = ""
        if short_circuit.template_id is not None:
            template = await db.get(KnowledgeTemplateRow, short_circuit.template_id)
            question = template.question if template else ""
        return RunKnowledge(
            tier="VERIFIED",
            template_id=short_circuit.template_id,
            question=question,
            bound_params={
                k: str(v) for k, v in (short_circuit.bound_params or {}).items()
            },
            score=short_circuit.score,
            matcher=short_circuit.matcher,
            overridden=overridden,
            feedback=given,
        )

    touched = {t.lower() for q in queries for t in (q.referenced_tables or [])}
    if touched and run.connection_id is not None and await _all_described(
        db, run.connection_id, touched
    ):
        return RunKnowledge(tier="GROUNDED", overridden=overridden, feedback=given)
    return RunKnowledge(tier="GENERATED", overridden=overridden, feedback=given)


async def _all_described(db, connection_id: UUID, tables: set[str]) -> bool:
    """Whether the semantic layer has an entry for every table the SQL touched.

    All of them, not most: *"every table it used is described in your semantic
    layer"* is what the chip says, and a chip that is true four times out of
    five is worse than no chip.
    """
    result = await db.execute(
        select(SemanticLayerRow).where(SemanticLayerRow.connection_id == connection_id)
    )
    layer = result.scalar_one_or_none()
    if layer is None or not layer.document:
        return False
    described = {
        str(entity.get("table", "")).lower()
        for entity in (layer.document.get("entities") or [])
        if entity.get("valid", True)
    }
    return bool(described) and tables <= described


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


@router.post("/runs/{run_id}/feedback", response_model=AnswerFeedbackRead)
async def leave_feedback(
    run_id: UUID,
    payload: AnswerFeedbackWrite,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> AnswerFeedbackRead:
    """Was this right? — open to **any** signed-in user.

    Not gated by `can_curate`, deliberately. The person best placed to notice a
    wrong answer is the person who asked the question, and they are usually not
    the person allowed to fix it. Gating the report on the right to repair
    would lose exactly the reports worth having.

    One verdict per person per answer; pressing again is a change of mind.
    """
    run = await _owned_run(db, run_id, ctx)
    row = await FeedbackService(db, settings).record(
        run, user_id=ctx.user_id, verdict=payload.verdict, comment=payload.comment
    )
    return AnswerFeedbackRead.model_validate(row)


@router.post("/runs/{run_id}/override", response_model=RunKnowledge)
async def override_run(run_id: UUID, ctx: CtxDep, db: DbDep) -> RunKnowledge:
    """*Generate a fresh answer instead.* Records the rejection; asks nothing.

    Two calls rather than one, deliberately: this records that a reader did not
    believe a verified answer, and the client then re-asks the question with
    `skip_templates`. Splitting them means the *measurement* survives even if
    the reader closes the tab instead of re-asking — and that measurement is
    the point. `OVERRIDDEN_BY_USER` is the honest number for whether the
    short-circuit is trusted, and it is what the threshold is tuned from.

    Idempotent: pressing it twice records one rejection, because a reader
    clicking again is impatience, not a second opinion.
    """
    run = await _owned_run(db, run_id, ctx)
    existing = await db.execute(
        select(KnowledgeTemplateHit).where(
            KnowledgeTemplateHit.run_id == run.id,
            KnowledgeTemplateHit.outcome == "OVERRIDDEN_BY_USER",
        )
    )
    if existing.scalars().first() is None:
        hit = await db.execute(
            select(KnowledgeTemplateHit).where(
                KnowledgeTemplateHit.run_id == run.id,
                KnowledgeTemplateHit.outcome == "SHORT_CIRCUIT",
            )
        )
        answered = hit.scalars().first()
        if answered is None:
            raise ValidationError("This answer did not come from a saved question.")
        await record_hit(
            db,
            run_id=run.id,
            template_id=answered.template_id,
            outcome="OVERRIDDEN_BY_USER",
            matcher=answered.matcher,
            score=answered.score,
            bound_params=answered.bound_params,
        )
        await db.flush()

    queries = await db.execute(
        select(GeneratedQuery)
        .where(GeneratedQuery.run_id == run.id)
        .order_by(GeneratedQuery.attempt_no)
    )
    return await _knowledge(
        db, run, [GeneratedQueryRead.model_validate(q) for q in queries.scalars()]
    )


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

    # In flight, not "not terminal": a run that stopped to ask a question is
    # non-terminal because the exchange is unfinished, but it has already
    # emitted its last event. Subscribing to it waits for a run nobody is
    # executing, which after a restart — when the bus has no history for it and
    # never saw it close — is a stream that hangs open until the client gives up.
    in_flight = RunStatus(run.status).is_in_flight

    async def replay_log(after: int):
        result = await db.execute(
            select(RunEventRow)
            .where(RunEventRow.run_id == run_id, RunEventRow.seq > after)
            .order_by(RunEventRow.seq)
        )
        return list(result.scalars())

    async def generate():
        if not in_flight:
            # The run already finished; replay from the durable log and close.
            for row in await replay_log(start_from):
                yield _sse(row.seq, row.type, row.data)
            return

        # Attach *before* backfilling, then skip what the backfill covered.
        #
        # The order is the point. This replica may not be the one executing the
        # run — since Phase 6 events arrive here through `LISTEN`/`NOTIFY`, and
        # the local bus holds nothing for a run it never published. Backfilling
        # first and subscribing second would drop anything emitted between the
        # two, which on a fast node is most of a step. Subscribing first makes
        # the overlap a duplicate instead of a gap, and a duplicate is one
        # comparison to throw away.
        stream = event_bus.subscribe(run_id, after_seq=start_from)
        try:
            last = start_from
            for row in await replay_log(start_from):
                yield _sse(row.seq, row.type, row.data)
                last = max(last, row.seq)
                if row.type == "RUN_FINISHED":
                    # It finished between the status read above and this query.
                    return

            async for event in stream:
                if event["seq"] <= last:
                    continue
                if await request.is_disconnected():
                    break
                yield _sse(event["seq"], event["type"], event["data"])
                if event["type"] == "RUN_FINISHED":
                    break
        except asyncio.CancelledError:
            return
        finally:
            await stream.aclose()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/chart", response_model=ChartRedrawRead)
async def redraw_chart(
    run_id: UUID, body: ChartRedrawRequest, ctx: CtxDep, db: DbDep
) -> ChartRedrawRead:
    """Draw this run's result as a different chart type.

    Reads the run's own TABLE artifact rather than re-running the query: the
    rows a chart is drawn from must be the rows the answer above it was written
    from, and a database that has moved on since would quietly make the picture
    disagree with the prose.

    The type goes through `plan_chart` like any other suggestion, so a pick the
    data cannot carry is refused here with its reason rather than compiled into
    something misleading. The picker will not offer such a type in the first
    place — this is the same rule stated twice, once where it is displayed and
    once where it would matter if the display were stale.
    """
    from app.charts import (
        candidate_intent,
        chart_options,
        compile_vega_lite,
        plan_chart,
        profile_result,
    )
    from app.domain.ports.database import ResultColumn

    await _owned_run(db, run_id, ctx)
    result = await db.execute(
        select(Artifact).where(Artifact.run_id == run_id, Artifact.kind == "TABLE")
    )
    table = result.scalars().first()
    if table is None:
        raise NotFoundError("This run kept no result to redraw.")

    columns = [
        ResultColumn(
            name=c["name"],
            db_type=c.get("db_type", ""),
            semantic_type=c.get("semantic_type", "nominal"),
        )
        for c in table.spec.get("columns", [])
    ]
    rows = table.spec.get("rows") or []
    profile = profile_result(
        columns, rows, truncated=bool(table.spec.get("truncated"))
    )
    options = [ChartOptionRead(**asdict(o)) for o in chart_options(profile)]

    intent = candidate_intent(profile, body.chart_type)
    plan = plan_chart(profile, intent) if intent is not None else None
    if plan is None or plan.intent is None or plan.intent.chart_type != body.chart_type:
        reason = next(
            (o.reason for o in options if o.chart_type == body.chart_type),
            "This result cannot be drawn that way.",
        )
        return ChartRedrawRead(chart_type="none", reason=reason, options=options)

    return ChartRedrawRead(
        spec=compile_vega_lite(plan.intent, profile, columns, rows),
        chart_type=plan.intent.chart_type,
        options=options,
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
