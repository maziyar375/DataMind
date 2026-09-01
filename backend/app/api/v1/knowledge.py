"""HTTP shape for knowledge templates. No business logic — see
`services/knowledge_service.py`.

Mounted under `/connections/{connection_id}/knowledge` for the reason the
semantic layer is: a template describes exactly one connection's schema, is
scoped by that connection's ownership, and dies with it.

**Every write asks `can_curate`. No endpoint here checks `ctx.is_admin`.** That
is the whole of decision D4 in `docs/learning-loop-plan.md`: curation is open
to any signed-in user today because the highest-value correction comes from the
person who knew the answer, and one settings flag makes it admin-only later
without touching a single call site.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    AnswerFeedbackRead,
    KnowledgeCapabilities,
    KnowledgeHealth,
    KnowledgeTemplateList,
    KnowledgeTemplatePatch,
    KnowledgeTemplateRead,
    KnowledgeTemplateWrite,
    MaintenanceRead,
    ReviewRead,
    ReviewResolve,
    SuggestionRead,
    TemplateCheckRequest,
    TemplateCheckResult,
)
from app.core.errors import ForbiddenError, NotFoundError
from app.infra.db.models import DatabaseConnection, SchemaSnapshotRow, User
from app.knowledge import (
    LiteralProvenance,
    TemplateParam,
    TemplateRole,
    TemplateSource,
    TemplateStatus,
    slots,
)
from app.services.knowledge_service import (
    UNUSED_AFTER_DAYS,
    FeedbackService,
    KnowledgeService,
)
from app.services.policy import can_curate
from app.workers.knowledge_maintenance import run_maintenance

router = APIRouter(
    prefix="/connections/{connection_id}/knowledge", tags=["knowledge"]
)


async def _owned(db, connection_id: UUID, ctx) -> DatabaseConnection:
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.id == connection_id,
            DatabaseConnection.owner_id == ctx.user_id,
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise NotFoundError("Connection not found.")
    return connection


def _require_curator(ctx, settings) -> None:
    if not can_curate(ctx, settings):
        raise ForbiddenError(
            "Only administrators can add templates on this connection."
        )


def _params(payload) -> list[TemplateParam]:
    return [TemplateParam.model_validate(p.model_dump()) for p in payload]


@router.get("/templates", response_model=KnowledgeTemplateList)
async def list_templates(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    include_archived: bool = False,
) -> KnowledgeTemplateList:
    """Every template, with the drift the current snapshot creates.

    Drift is computed on read *as well as* swept for, and the two answer
    different questions. The sweep runs on a schema sync and on demand, and it
    writes `STALE`. This read re-validates whatever is still `ACTIVE` and
    reports it in `stale_ids` without writing anything — so a template that
    stopped working between the last sweep and this page load is amber on the
    screen rather than silently trusted, exactly as the semantic layer does it.
    A row the sweep already withdrew carries `status: "STALE"` and is counted
    in `health` instead.
    """
    connection = await _owned(db, connection_id, ctx)
    service = KnowledgeService(db, settings)
    rows = await service.list_templates(
        connection, include_archived=include_archived
    )

    stale: list[UUID] = []
    for row in rows:
        if row.status != str(TemplateStatus.ACTIVE):
            continue
        verdict = await service.revalidate(connection, row)
        if not verdict.valid:
            stale.append(row.id)

    snapshot_version = max((r.schema_version for r in rows), default=0)
    current = await _snapshot_version(db, connection_id)
    return KnowledgeTemplateList(
        templates=[KnowledgeTemplateRead.model_validate(r) for r in rows],
        schema_version=current or snapshot_version,
        schema_synced=bool(current),
        can_curate=can_curate(ctx, settings),
        stale_ids=stale,
        health=await _health(service, connection),
    )


@router.get("/health", response_model=KnowledgeHealth)
async def store_health(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> KnowledgeHealth:
    """Stale, conflicted and unused counts — §4.7's three rows.

    Read-only and open to any reader of the connection, like the queue itself:
    knowing that the answer you are about to trust came from a store with four
    conflicts in it is not a privilege.
    """
    connection = await _owned(db, connection_id, ctx)
    return await _health(KnowledgeService(db, settings), connection)


@router.post("/templates/revalidate", response_model=MaintenanceRead)
async def revalidate_store(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> MaintenanceRead:
    """Sweep this connection's store now, and say what changed.

    Synchronous on purpose, unlike the semantic layer's generation job. The
    staleness half is a parse per template; the conflict half is two read-only,
    row-capped queries per near-duplicate pair, bounded by
    `MAX_PAIRS_PER_PASS`. That is a request a curator can wait for, and waiting
    means the list they are looking at refreshes to what the sweep found rather
    than to a job id.

    `can_curate`, because it writes template statuses — and because the
    conflict half runs statements against the customer's database, which is not
    something every reader of a connection should be able to start.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings)

    result = await run_maintenance(db, settings, connection)
    return MaintenanceRead(
        checked=result.staleness.checked,
        staled=result.staleness.staled,
        revived=result.staleness.revived,
        conflicted=result.conflicts.conflicted,
        cleared=result.conflicts.cleared,
        pairs_considered=result.conflicts.pairs_considered,
        pairs_executed=result.conflicts.pairs_executed,
        skipped=result.conflicts.skipped,
        conflicts_checked=result.conflicts_checked,
    )


@router.get("/capabilities", response_model=KnowledgeCapabilities)
async def capabilities(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> KnowledgeCapabilities:
    """What this reader may do here, so the UI hides rather than disables."""
    await _owned(db, connection_id, ctx)
    return KnowledgeCapabilities(can_curate=can_curate(ctx, settings))


@router.post("/templates/check", response_model=TemplateCheckResult)
async def check_template(
    connection_id: UUID,
    payload: TemplateCheckRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> TemplateCheckResult:
    """Validate the SQL and propose parameters — one round trip, one parse.

    Read-only and open to any reader of the connection: it writes nothing, and
    a curator who cannot save still benefits from seeing why a statement was
    rejected. This is what makes the editor honest — the same parser that will
    reject the statement at save time answers while it is still being typed.
    """
    connection = await _owned(db, connection_id, ctx)
    verdict, proposals, sql, params = await KnowledgeService(db, settings).check(
        connection,
        sql=payload.sql,
        question=payload.question,
        params=_params(payload.params),
        accept=set(payload.accept) if payload.accept is not None else None,
    )
    return TemplateCheckResult(
        valid=verdict.valid,
        issue=verdict.message,
        issues=[i.model_dump() for i in verdict.report.errors],
        referenced_tables=verdict.referenced_tables,
        proposals=[p.model_dump(mode="json") for p in proposals],
        sql=sql,
        params=[p.model_dump(mode="json") for p in params],
        question_slots=slots(payload.question),
    )


@router.post(
    "/templates",
    response_model=KnowledgeTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    connection_id: UUID,
    payload: KnowledgeTemplateWrite,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> KnowledgeTemplateRead:
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings)

    source = TemplateSource(payload.source)
    row = await KnowledgeService(db, settings).create(
        connection,
        actor_id=ctx.user_id,
        question=payload.question,
        sql=payload.sql,
        params=_params(payload.params),
        note=payload.note,
        source=source,
        literal_provenance=_provenance(source),
        role=TemplateRole(payload.role),
    )
    return KnowledgeTemplateRead.model_validate(row)


@router.patch("/templates/{template_id}", response_model=KnowledgeTemplateRead)
async def update_template(
    connection_id: UUID,
    template_id: UUID,
    payload: KnowledgeTemplatePatch,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> KnowledgeTemplateRead:
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings)

    row = await KnowledgeService(db, settings).update(
        connection,
        template_id,
        actor_id=ctx.user_id,
        question=payload.question,
        sql=payload.sql,
        params=None if payload.params is None else _params(payload.params),
        note=payload.note,
        role=None if payload.role is None else TemplateRole(payload.role),
        status=None if payload.status is None else TemplateStatus(payload.status),
    )
    return KnowledgeTemplateRead.model_validate(row)


@router.delete("/templates/{template_id}", response_model=KnowledgeTemplateRead)
async def archive_template(
    connection_id: UUID,
    template_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> KnowledgeTemplateRead:
    """Archives. Never hard-deletes.

    A 200 with the archived row rather than a 204: the row still exists, the
    list still shows it under the archive, and returning it says so.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings)
    row = await KnowledgeService(db, settings).archive(connection, template_id)
    return KnowledgeTemplateRead.model_validate(row)


# ── capture: the queue and the backlog (Phase 3) ─────────────────────────
@router.get("/reviews", response_model=list[ReviewRead])
async def list_reviews(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    state: str = "OPEN",
) -> list[ReviewRead]:
    """Every flag on this connection, with the evidence beside it.

    Read-only and open to any reader of the connection: seeing what people
    reported is not a privilege, and it is often the fastest way to find out
    that the answer you are about to trust is already disputed.
    """
    connection = await _owned(db, connection_id, ctx)
    service = FeedbackService(db, settings)

    out: list[ReviewRead] = []
    for row, run, question, sql in await service.reviews(connection, state=state):
        out.append(ReviewRead(
            id=row.id,
            run_id=run.id,
            verdict=row.verdict,
            comment=row.comment,
            state=row.state,
            created_at=row.created_at,
            question=question,
            sql=sql,
            flagged_by=await _display_name(db, row.user_id),
        ))
    return out


@router.post("/reviews/{feedback_id}/resolve", response_model=AnswerFeedbackRead)
async def resolve_review(
    connection_id: UUID,
    feedback_id: UUID,
    payload: ReviewResolve,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> AnswerFeedbackRead:
    """Close a flag, and record what happened to it.

    `template_id` is the loop closing: the flag that became knowledge says so,
    and the person who raised it is told. Ship this without that link and the
    phase has shipped a suggestion box.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings)

    if payload.template_id is not None:
        # It must be a template on *this* connection: a resolution pointing at
        # somebody else's knowledge would tell the flagger a lie.
        await KnowledgeService(db, settings).get(connection, payload.template_id)

    row = await FeedbackService(db, settings).resolve(
        connection,
        feedback_id,
        actor_id=ctx.user_id,
        template_id=payload.template_id,
        note=payload.note,
        dismiss=payload.dismiss,
    )
    return AnswerFeedbackRead.model_validate(row)


@router.get("/suggestions", response_model=list[SuggestionRead])
async def list_suggestions(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    limit: int = 30,
) -> list[SuggestionRead]:
    """A finite, ranked list of what to teach next.

    The hardest part of curation is not writing a template — it is knowing
    which one to write. Five sources, ranked in `app.knowledge.backlog`:
    what somebody flagged, what already exists in a corrected tile, what people
    ask that nothing matches, what fails, and the words nothing here
    recognises.

    Everything already taught is excluded, so the list shrinks as it is worked.
    A backlog that does not shrink is a report, not a queue.
    """
    connection = await _owned(db, connection_id, ctx)
    items = await FeedbackService(db, settings).suggestions(
        connection, limit=max(1, min(limit, 100))
    )
    return [
        SuggestionRead(
            kind=str(item.kind),  # type: ignore[arg-type]
            question=item.question,
            count=item.count,
            reason=item.reason,
            sql=item.sql,
            source=item.source,
            model_derived=item.model_derived,
            origin_id=item.origin_id,
            words=item.words,
        )
        for item in items
    ]


async def _health(
    service: KnowledgeService, connection: DatabaseConnection
) -> KnowledgeHealth:
    health = await service.health(connection)
    return KnowledgeHealth(
        total=health.total,
        stale=health.stale,
        conflicted=health.conflicted,
        unused=health.unused,
        conflict_checks_enabled=connection.conflict_checks_enabled,
        unused_after_days=UNUSED_AFTER_DAYS,
    )


async def _display_name(db, user_id: UUID | None) -> str:
    """A name, never an address.

    The queue header says who raised a flag so the curator can go and ask them.
    An email there would put a personal identifier on a screen that has no need
    for one.
    """
    if user_id is None:
        return ""
    user = await db.get(User, user_id)
    return (user.display_name or "") if user is not None else ""


def _provenance(source: TemplateSource) -> LiteralProvenance:
    """Who chose the literals — the disclosure question (`docs/security.md`).

    A statement typed or corrected in the editor carries literals a person
    wrote. One confirmed from a generated answer without editing carries
    literals the *model* chose, possibly from sampled values disclosed under a
    policy that has since been tightened — so it is gated like a sample value.
    """
    machine = {TemplateSource.CHAT_CONFIRMED, TemplateSource.TILE,
               TemplateSource.REPORT_BLOCK}
    return (
        LiteralProvenance.MODEL_DERIVED
        if source in machine
        else LiteralProvenance.HUMAN_AUTHORED
    )


async def _snapshot_version(db, connection_id: UUID) -> int:
    result = await db.execute(
        select(SchemaSnapshotRow.version)
        .where(SchemaSnapshotRow.connection_id == connection_id)
        .order_by(SchemaSnapshotRow.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none() or 0
