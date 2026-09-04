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

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    AnswerFeedbackRead,
    BenchmarkCandidateRead,
    BenchmarkOverview,
    BenchmarkResultRead,
    BenchmarkRunRead,
    BenchmarkSetRead,
    BenchmarkSetWrite,
    EmbeddingProvider,
    EmbeddingStatus,
    EmbeddingWrite,
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
from app.infra.db.models import (
    BenchmarkSet,
    DatabaseConnection,
    KnowledgeTemplateRow,
    LlmConfig,
    SchemaSnapshotRow,
    User,
)
from app.knowledge import (
    LiteralProvenance,
    TemplateParam,
    TemplateRole,
    TemplateSource,
    TemplateStatus,
    slots,
)
from app.services import audit
from app.services.benchmark_service import (
    MIN_SET_SIZE,
    BenchmarkService,
    held_out_split,
)
from app.services.knowledge_service import (
    UNUSED_AFTER_DAYS,
    FeedbackService,
    KnowledgeService,
    embedding_providers,
    set_embeddings,
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


def _require_curator(ctx, settings, connection) -> None:
    """Administrator, or the owner of this connection. Never `is_admin` alone.

    The connection is passed rather than looked up because every caller has
    just resolved it through `_owned()` — and because `can_curate` with no
    resource asks the strict question, which is the wrong one here.
    """
    if not can_curate(ctx, settings, connection):
        raise ForbiddenError(
            "Only administrators and the owner of this connection can change "
            "what it has been taught."
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
        can_curate=can_curate(ctx, settings, connection),
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
    _require_curator(ctx, settings, connection)

    result = await run_maintenance(db, settings, connection)
    # A sweep writes template statuses without anybody naming a template, and
    # the conflict half runs statements against the customer's database. Both
    # are things somebody should be able to trace back to a person afterwards.
    await audit.record(
        db, ctx,
        action=audit.STORE_REVALIDATED,
        resource_type=audit.CONNECTION,
        resource_id=connection.id,
        detail={
            "checked": result.staleness.checked,
            "staled": len(result.staleness.staled),
            "revived": len(result.staleness.revived),
            "conflicted": len(result.conflicts.conflicted),
            "cleared": len(result.conflicts.cleared),
            "pairs_executed": result.conflicts.pairs_executed,
            "conflicts_checked": result.conflicts_checked,
            "indexed": result.index.embedded,
        },
    )
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
        indexed=result.index.embedded,
        index_current=result.index.current,
        index_truncated=result.index.truncated,
        index_error=result.index.error,
    )


@router.get("/embeddings", response_model=EmbeddingStatus)
async def embedding_status(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> EmbeddingStatus:
    """Whether this store is searched by meaning, and how much of it is indexed.

    Read-only and open to any reader of the connection: it names a model and
    counts rows, and both are already visible to anyone who can open the
    Knowledge tab.
    """
    connection = await _owned(db, connection_id, ctx)
    return await _embedding_status(db, connection)


@router.put("/embeddings", response_model=EmbeddingStatus)
async def set_embedding_search(
    connection_id: UUID,
    payload: EmbeddingWrite,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> EmbeddingStatus:
    """Turn embedding search on or off, and index what is already there.

    Synchronous, like `revalidate`, and for the same reason: turning this on
    probes the provider once and then embeds the store in batches of
    sixty-four, so a connection with a curator's worth of templates is a
    handful of calls. Waiting means the answer on the screen is what the
    feature will actually do on the next question, rather than a job id.

    `can_curate`, because it spends the owner's provider budget and changes how
    every question on this connection is matched.

    **A refusal leaves the connection exactly as it was**, and returns the
    provider's own sentence in `message`: *"Anthropic does not offer an
    embedding endpoint"* is a fix somebody can act on, and *"unavailable"* is
    not.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings, connection)

    result, message = await set_embeddings(
        db, settings, connection,
        enabled=payload.enabled,
        model=payload.model,
        llm_config_id=payload.llm_config_id,
    )
    await audit.record(
        db, ctx,
        action=audit.EMBEDDINGS_CHANGED,
        resource_type=audit.CONNECTION,
        resource_id=connection.id,
        outcome=audit.SUCCESS if not (message or result.error) else audit.FAILED,
        detail={
            "enabled": payload.enabled,
            "model": connection.embedding_model,
            "dimension": connection.embedding_dimension,
            # Identifiers and counts, never content — `services/audit.py`'s
            # third rule. Which provider indexed a store is exactly the kind
            # of "who did what with what" this log exists to answer.
            "llm_config_id": str(connection.embedding_llm_config_id or ""),
            "indexed": result.embedded,
            "reason": message or result.error,
        },
    )
    status = await _embedding_status(db, connection)
    status.message = message or result.error
    return status


async def _embedding_status(db, connection) -> EmbeddingStatus:
    live = await db.execute(
        select(func.count())
        .select_from(KnowledgeTemplateRow)
        .where(
            KnowledgeTemplateRow.connection_id == connection.id,
            KnowledgeTemplateRow.status == str(TemplateStatus.ACTIVE),
            KnowledgeTemplateRow.role == str(TemplateRole.RETRIEVABLE),
        )
    )
    indexed = await db.execute(
        select(func.count())
        .select_from(KnowledgeTemplateRow)
        .where(
            KnowledgeTemplateRow.connection_id == connection.id,
            KnowledgeTemplateRow.status == str(TemplateStatus.ACTIVE),
            KnowledgeTemplateRow.role == str(TemplateRole.RETRIEVABLE),
            KnowledgeTemplateRow.embedding_fingerprint != "",
        )
    )
    return EmbeddingStatus(
        enabled=bool(connection.embedding_model),
        model=connection.embedding_model or "",
        dimension=connection.embedding_dimension or 0,
        templates=live.scalar_one() or 0,
        indexed=indexed.scalar_one() or 0,
        llm_config_id=connection.embedding_llm_config_id,
        # Sent on every read, including the read that finds the feature off:
        # the control's whole job in that state is to say what turning it on
        # would use, and an empty list is what makes *"configure one first"*
        # the honest thing to show rather than a button that fails.
        providers=[
            EmbeddingProvider(
                id=row.id,
                name=row.name,
                provider=row.provider,
                model=row.embedding_model,
            )
            for row in await embedding_providers(db, connection)
        ],
    )


@router.get("/capabilities", response_model=KnowledgeCapabilities)
async def capabilities(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> KnowledgeCapabilities:
    """What this reader may do here, so the UI hides rather than disables."""
    connection = await _owned(db, connection_id, ctx)
    return KnowledgeCapabilities(
        can_curate=can_curate(ctx, settings, connection)
    )


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
    _require_curator(ctx, settings, connection)

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
    await audit.record(
        db, ctx,
        action=audit.TEMPLATE_CREATED,
        resource_type=audit.TEMPLATE,
        resource_id=row.id,
        detail={"connection_id": str(connection.id), "source": str(source)},
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
    _require_curator(ctx, settings, connection)

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
    # Which *fields* were sent, not what they were set to — the row's own
    # columns carry the values, and rule 3 keeps question text and SQL out of
    # a table that would otherwise become a second copy of the store.
    await audit.record(
        db, ctx,
        action=audit.TEMPLATE_UPDATED,
        resource_type=audit.TEMPLATE,
        resource_id=row.id,
        detail={
            "connection_id": str(connection.id),
            "fields": sorted(payload.model_dump(exclude_none=True)),
        },
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
    _require_curator(ctx, settings, connection)
    row = await KnowledgeService(db, settings).archive(connection, template_id)
    await audit.record(
        db, ctx,
        action=audit.TEMPLATE_ARCHIVED,
        resource_type=audit.TEMPLATE,
        resource_id=row.id,
        detail={"connection_id": str(connection.id)},
    )
    return KnowledgeTemplateRead.model_validate(row)


# ── the score (Phase 6) ──────────────────────────────────────────────────
@router.get("/benchmarks", response_model=BenchmarkOverview)
async def list_benchmarks(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> BenchmarkOverview:
    """Every set, its recent runs, and how many templates could join one.

    Read-only and open to any reader of the connection. §4.8 shows this strip
    **only once a set exists** — never an empty chart — so an empty `sets` is
    the signal for the tab to show nothing rather than zeros.
    """
    connection = await _owned(db, connection_id, ctx)
    service = BenchmarkService(db, settings)

    sets: list[BenchmarkSetRead] = []
    for row in await service.list_sets(connection):
        sets.append(await _read_set(service, row))

    return BenchmarkOverview(
        sets=sets,
        can_curate=can_curate(ctx, settings, connection),
        candidates=len(await service.candidates(connection)),
        min_set_size=MIN_SET_SIZE,
    )


@router.get("/benchmarks/candidates", response_model=list[BenchmarkCandidateRead])
async def benchmark_candidates(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> list[BenchmarkCandidateRead]:
    """Templates a set may be built from — live, and still answering questions.

    §1.3's rule in the query that builds the candidate set: `ARCHIVED`, `STALE`
    and `CONFLICTED` are out (a benchmark whose stored answer no longer runs
    measures the schema, not the product), and so is anything already committed
    to a set, because a template cannot be held out of one instrument and
    answering questions for another.
    """
    connection = await _owned(db, connection_id, ctx)
    rows = await BenchmarkService(db, settings).candidates(connection)
    return [BenchmarkCandidateRead.model_validate(r) for r in rows]


@router.post(
    "/benchmarks",
    response_model=BenchmarkSetRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_benchmark(
    connection_id: UUID,
    payload: BenchmarkSetWrite,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> BenchmarkSetRead:
    """Build a set. **This withdraws its members from answering questions.**

    That is the point rather than a side effect: §1.3's rule is that a template
    is retrievable or benchmarkable and never both, and a held-out question
    answered from its own stored SQL measures nothing. `can_curate`, because it
    changes what the ask path may use.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings, connection)

    service = BenchmarkService(db, settings)
    row = await service.create_set(
        connection,
        actor_id=ctx.user_id,
        name=payload.name,
        template_ids=payload.template_ids,
        description=payload.description,
        held_out_fraction=payload.held_out_fraction,
    )
    # Creating a set takes questions *out* of the ask path. That is a change to
    # what the product will answer from, made by a person, and it belongs in
    # the log for the same reason archiving a template does.
    await audit.record(
        db, ctx,
        action=audit.BENCHMARK_CREATED,
        resource_type=audit.BENCHMARK_SET,
        resource_id=row.id,
        detail={
            "connection_id": str(connection.id),
            "members": len(payload.template_ids),
            "held_out_fraction": payload.held_out_fraction,
        },
    )
    return await _read_set(service, row)


@router.delete("/benchmarks/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_benchmark(
    connection_id: UUID,
    set_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    """Delete a set and give its questions back to the ask path.

    The one place in the learning loop where `DELETE` really deletes: a set is
    an instrument, not somebody's knowledge, and the knowledge it was built
    from is returned intact and `RETRIEVABLE`.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings, connection)
    released = await BenchmarkService(db, settings).release(connection, set_id)
    await audit.record(
        db, ctx,
        action=audit.BENCHMARK_DELETED,
        resource_type=audit.BENCHMARK_SET,
        resource_id=set_id,
        detail={
            "connection_id": str(connection.id),
            "returned_to_retrievable": released,
        },
    )


@router.post(
    "/benchmarks/{set_id}/run",
    response_model=BenchmarkRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_benchmark(
    connection_id: UUID,
    set_id: UUID,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    llm_config_id: UUID | None = None,
) -> BenchmarkRunRead:
    """Queue a run. **202, and a row** — this is minutes of model calls.

    The row first, then the worker, the order `semantic_jobs` uses: a process
    that dies mid-run leaves a `RUNNING` row somebody can see and retry, rather
    than a request that never came back.
    """
    connection = await _owned(db, connection_id, ctx)
    _require_curator(ctx, settings, connection)

    service = BenchmarkService(db, settings)
    set_row = await service.get_set(connection, set_id)
    run = await service.queue_run(
        connection, set_row,
        actor_id=ctx.user_id,
        llm_config_id=llm_config_id or await _default_llm_config(db, ctx),
    )
    await audit.record(
        db, ctx,
        action=audit.BENCHMARK_RUN_QUEUED,
        resource_type=audit.BENCHMARK_RUN,
        resource_id=run.id,
        detail={"connection_id": str(connection.id), "set_id": str(set_id)},
    )
    await db.commit()

    executor = getattr(request.app.state, "benchmark_executor", None)
    if executor is not None:
        await executor.submit(run.id)
    return BenchmarkRunRead.model_validate(run)


@router.get(
    "/benchmarks/runs/{run_id}/results",
    response_model=list[BenchmarkResultRead],
)
async def benchmark_results(
    connection_id: UUID,
    run_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> list[BenchmarkResultRead]:
    """Every question's verdict, so a number can be argued with.

    A score nobody can drill into is a score nobody should trust — and the
    `failure_reason` on a mismatch is usually the next template to fix.
    """
    connection = await _owned(db, connection_id, ctx)
    service = BenchmarkService(db, settings)
    run = await service.get_run(connection, run_id)
    return [
        BenchmarkResultRead.model_validate(r) for r in await service.results(run)
    ]


async def _read_set(
    service: BenchmarkService, row: BenchmarkSet
) -> BenchmarkSetRead:
    out = BenchmarkSetRead.model_validate(row)
    out.runs = [
        BenchmarkRunRead.model_validate(r) for r in await service.runs(row)
    ]
    out.held_out_count = len(
        held_out_split(list(row.template_ids or []), row.held_out_fraction)
    )
    return out


async def _default_llm_config(db, ctx) -> UUID | None:
    """The caller's model, when the request did not name one.

    A benchmark without a model is not a benchmark, and making the UI pick one
    before it can show a score would put a configuration question in front of
    somebody who asked for a number.
    """
    result = await db.execute(
        select(LlmConfig)
        # A provider row can now declare an embedding model and no chat model.
        # Falling back to one of those would queue a benchmark that cannot
        # answer a single question.
        .where(LlmConfig.owner_id == ctx.user_id, LlmConfig.model != "")
        .order_by(LlmConfig.created_at)
    )
    config = result.scalars().first()
    return config.id if config is not None else None


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
    _require_curator(ctx, settings, connection)

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
    await audit.record(
        db, ctx,
        action=audit.REVIEW_RESOLVED,
        resource_type=audit.REVIEW,
        resource_id=row.id,
        detail={
            "connection_id": str(connection.id),
            "state": row.state,
            "became_template": str(payload.template_id or ""),
        },
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
