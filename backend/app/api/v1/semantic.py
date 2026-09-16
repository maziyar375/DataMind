"""HTTP shape for the semantic layer. No business logic — see
`services/semantic_service.py`.

Mounted under `/connections/{connection_id}/semantic` because a semantic layer
has no life of its own: it describes exactly one connection's schema and dies
with it. It is nevertheless a **resource type of its own** — its resource id
*is* the connection's id — because its audience is not the connection's: a
Data Engineer curates meaning across every database and still may not read a
credential or a row. Every route here asks about `semantic_layer`, never about
`connection`, and that distinction is the whole reason the type exists.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from app.api.deps import AuthzDep, CtxDep, DbDep, SettingsDep
from app.api.errors import problem_response
from app.api.schemas import (
    SemanticChangeList,
    SemanticChangeRead,
    SemanticDiffRequest,
    SemanticDraftRequest,
    SemanticExpressionCheck,
    SemanticExpressionResult,
    SemanticGenerateRequest,
    SemanticHistoryEntry,
    SemanticJobRead,
    SemanticLayerRead,
    SemanticPublishRequest,
    SemanticRestoreRequest,
    SemanticSaveRequest,
    SemanticTableFact,
    SemanticVersionList,
    SemanticVersionRead,
    SemanticVersionSummary,
)
from app.api.v1.access import attach_access_routes
from app.core.errors import (
    NotFoundError,
    SemanticBaseRevisionRequiredError,
    SemanticConflictError,
    ValidationError,
)
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.db.models import DatabaseConnection, SemanticJobRow, SemanticLayerVersionRow
from app.semantic import AFFECTS_SQL, Change, SemanticDocument, check_expression
from app.services.policy import require
from app.services.semantic_service import SemanticService, VersionSummary

router = APIRouter(prefix="/connections/{connection_id}/semantic", tags=["semantic"])

# `…/semantic/grants` and `…/semantic/actions`. No transfer, for the same
# reason the knowledge store has none: a layer's owner is its connection's
# owner, so there is one transfer and it lives on the thing that has an owner.
attach_access_routes(
    router,
    ResourceType.SEMANTIC_LAYER,
    param="connection_id",
    in_prefix=True,
    transferable=False,
)


async def _authorized(
    db, authz, connection_id: UUID, ctx, privilege: Privilege
) -> DatabaseConnection:
    """The connection whose **layer** this principal may act on at `privilege`.

    The row loaded is the connection — a layer has no row of its own until it
    is generated — but the question asked names `semantic_layer`, so a role
    carrying `(semantic_layer, manage)` reaches the editor without thereby
    reaching the credential.
    """
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise NotFoundError("Connection not found.")
    await require(
        ctx,
        authz,
        ResourceRef(
            type=ResourceType.SEMANTIC_LAYER, id=connection.id, entity=connection
        ),
        privilege,
        db=db,
    )
    return connection


def _job_read(job: SemanticJobRow | None) -> SemanticJobRead | None:
    return SemanticJobRead.model_validate(job) if job is not None else None


async def _read_payload(
    service: SemanticService, connection: DatabaseConnection
) -> SemanticLayerRead:
    doc, row, facts = await service.read(connection)
    job = await service.latest_job(connection.id)
    published = facts["published"]
    return SemanticLayerRead(
        document=doc.model_dump(mode="json"),
        # A row holding the empty document is a deleted layer, and the editor
        # offers it exactly what it offers a connection that never had one. The
        # document meant is the one shown: a generated draft over nothing
        # published is a layer to review, not an empty state.
        exists=row is not None and bool(
            row.draft_document if facts["has_draft"] else row.document
        ),
        enabled=connection.semantic_layer_enabled,
        entity_count=len(doc.entities),
        metric_count=doc.metric_count,
        reviewed_count=doc.reviewed_count,
        issue_count=doc.issue_count,
        schema_version=facts["schema_version"],
        schema_dialect=facts["schema_dialect"],
        stale=facts["stale"],
        tables=[SemanticTableFact(**t) for t in facts["tables"]],
        model_snapshot=(row.model_snapshot if row else {}),
        prompt_version=(row.prompt_version if row else ""),
        generated_at=(row.generated_at if row else None),
        edited_at=(row.edited_at if row else None),
        job=_job_read(job),
        revision=(row.revision if row else 0),
        published_version=(row.published_version if row else None),
        published_by_name=facts["published_by_name"],
        published_at=(published.created_at if published else None),
        published_note=(published.note if published else ""),
        published_origin=(published.origin if published else {}),
        published_exists=facts["published_exists"],
        has_draft=facts["has_draft"],
        draft_updated_by_name=facts["draft_updated_by_name"],
        draft_updated_at=(
            row.draft_updated_at if row is not None and facts["has_draft"] else None
        ),
        unpublished_changes=[_change_read(c) for c in facts["unpublished_changes"]],
    )


def _change_read(change: Change) -> SemanticChangeRead:
    return SemanticChangeRead(**change.as_dict())


def _summary(
    row: SemanticLayerVersionRow, author: str, kinds: dict[str, int]
) -> dict[str, Any]:
    return {
        "version": row.version,
        "parent_version": row.parent_version,
        "published_by": row.published_by,
        "published_by_name": author,
        "note": row.note,
        "origin": row.origin or {},
        "schema_version": row.schema_version,
        "entity_count": row.entity_count,
        "metric_count": row.metric_count,
        "reviewed_count": row.reviewed_count,
        "issue_count": row.issue_count,
        "created_at": row.created_at,
        "changes": kinds,
        "affects_sql": any(kind in AFFECTS_SQL for kind in kinds),
    }


def _version_summary(item: VersionSummary) -> SemanticVersionSummary:
    return SemanticVersionSummary(**_summary(item.row, item.author, item.kinds))


@router.get("", response_model=SemanticLayerRead)
async def get_semantic_layer(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> SemanticLayerRead:
    """The document the editor edits — the draft when there is one — re-bound
    to the newest schema snapshot, with its unpublished changes.

    Returns a 200 with an empty document rather than a 404 when nothing has
    been generated: "you have no semantic layer yet" is a state the editor
    renders, not an error it handles.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    return await _read_payload(SemanticService(db, settings, authz), connection)


@router.put("", response_model=SemanticLayerRead)
async def save_semantic_layer(
    connection_id: UUID,
    payload: SemanticSaveRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticLayerRead:
    """Save a document and publish it as the next version, in one step.

    For API clients and scripts; the editor saves a draft (`PUT …/draft`) and
    publishes it (`POST …/publish`) as two acts. Any draft is replaced.

    The whole document rather than a patch per entity: an edit routinely moves
    a definition between entities (a metric belongs on the fact table, not the
    dimension the user opened), and a partial update cannot express that
    atomically.

    `base_revision` is required, and a stale one is a 409 naming who wrote in
    the meantime. That 409 is *returned* rather than raised so its audit row
    commits — the refusal is what happened.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    if payload.base_revision is None:
        raise SemanticBaseRevisionRequiredError(
            "Say which revision this document was edited from (`base_revision`)."
        )
    try:
        doc = SemanticDocument.model_validate(payload.document)
    except Exception as err:
        raise ValidationError("This semantic layer document is malformed.") from err

    service = SemanticService(db, settings, authz)
    try:
        await service.save(
            connection, doc, base_revision=payload.base_revision, note=payload.note, ctx=ctx
        )
    except SemanticConflictError as err:
        return problem_response(err)  # type: ignore[return-value]
    return await _read_payload(service, connection)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_semantic_layer(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> None:
    """Publish an empty document as a tombstone version. History is kept (D10)."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.DELETE)
    await SemanticService(db, settings, authz).delete(connection, ctx=ctx)
    await db.flush()


@router.put("/draft", response_model=SemanticLayerRead)
async def save_semantic_draft(
    connection_id: UUID,
    payload: SemanticDraftRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticLayerRead:
    """Save the editor's document to the draft. **No question reads a draft.**

    `modify`. The same revision rule as every write: a stale `base_revision` is
    a 409, returned so its audit row commits.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    if payload.base_revision is None:
        raise SemanticBaseRevisionRequiredError(
            "Say which revision this draft was edited from (`base_revision`)."
        )
    try:
        doc = SemanticDocument.model_validate(payload.document)
    except Exception as err:
        raise ValidationError("This semantic layer document is malformed.") from err

    service = SemanticService(db, settings, authz)
    try:
        await service.save_draft(connection, doc, base_revision=payload.base_revision, ctx=ctx)
    except SemanticConflictError as err:
        return problem_response(err)  # type: ignore[return-value]
    return await _read_payload(service, connection)


@router.delete("/draft", response_model=SemanticLayerRead)
async def discard_semantic_draft(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
    base_revision: int | None = Query(default=None, ge=0),
) -> SemanticLayerRead:
    """Throw the draft away; the published document is untouched. `modify`.

    Returns the layer rather than 204, because what the editor needs next is
    the published document it now shows and the revision to write against.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    if base_revision is None:
        raise SemanticBaseRevisionRequiredError(
            "Say which revision you are discarding (`base_revision`)."
        )
    service = SemanticService(db, settings, authz)
    try:
        await service.discard_draft(connection, base_revision=base_revision, ctx=ctx)
    except SemanticConflictError as err:
        return problem_response(err)  # type: ignore[return-value]
    return await _read_payload(service, connection)


@router.post("/publish", response_model=SemanticLayerRead)
async def publish_semantic_draft(
    connection_id: UUID,
    payload: SemanticPublishRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticLayerRead:
    """Publish the draft as the next version — `modify`, the privilege editing
    already needs (D7). No approval step.

    Refused when there is nothing to publish (`E_SEMANTIC_NO_CHANGES`), and when
    a change alters numbers and the note is blank (`E_SEMANTIC_NOTE_REQUIRED`).
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    if payload.base_revision is None:
        raise SemanticBaseRevisionRequiredError(
            "Say which revision of the draft you are publishing (`base_revision`)."
        )
    service = SemanticService(db, settings, authz)
    try:
        await service.publish(
            connection, base_revision=payload.base_revision, note=payload.note, ctx=ctx
        )
    except SemanticConflictError as err:
        return problem_response(err)  # type: ignore[return-value]
    return await _read_payload(service, connection)


@router.post("/diff", response_model=list[SemanticChangeRead])
async def diff_semantic_documents(
    connection_id: UUID,
    payload: SemanticDiffRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> list[SemanticChangeRead]:
    """What changed between two documents, in the server's one vocabulary.

    `select`, and it saves nothing: it is how the editor learns whether its
    pending edits change numbers (and so ask for a note), and how a conflict
    lists the edits a person has to make again. The frontend never compares two
    documents itself.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    changes = await SemanticService(db, settings, authz).diff(
        connection, payload.before, payload.after
    )
    return [_change_read(c) for c in changes]


@router.get("/versions", response_model=SemanticVersionList)
async def list_semantic_versions(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
    limit: int = Query(default=50, ge=1, le=200),
    before: int | None = Query(default=None, ge=1),
) -> SemanticVersionList:
    """Every version of this layer, newest first, paged by version number."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    service = SemanticService(db, settings, authz)
    items = await service.versions(connection.id, limit=limit, before=before)
    head = await service.layer_row(connection.id)
    return SemanticVersionList(
        versions=[_version_summary(item) for item in items],
        revision=head.revision if head else 0,
        published_version=head.published_version if head else None,
        next_before=(
            items[-1].row.version
            if len(items) == limit and items[-1].row.version > 1 else None
        ),
    )


@router.get("/versions/{number}", response_model=SemanticVersionRead)
async def get_semantic_version(
    connection_id: UUID,
    number: int,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticVersionRead:
    """One version's document, as it was bound when it was published."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    service = SemanticService(db, settings, authz)
    row = await service.version(connection.id, number)
    _, _, changes = await service.changes(connection.id, number)
    kinds: dict[str, int] = {}
    for change in changes:
        kinds[change.kind] = kinds.get(change.kind, 0) + 1
    return SemanticVersionRead(
        **_summary(row, await service.version_author(row), kinds),
        document=row.document or {},
    )


@router.get("/versions/{number}/changes", response_model=SemanticChangeList)
async def get_semantic_version_changes(
    connection_id: UUID,
    number: int,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
    against: int | None = Query(default=None, ge=1),
) -> SemanticChangeList:
    """A version's changes against its parent, or against `against`."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    _, base, changes = await SemanticService(db, settings, authz).changes(
        connection.id, number, against=against
    )
    return SemanticChangeList(
        version=number, against=base, changes=[_change_read(c) for c in changes]
    )


@router.post("/versions/{number}/restore", response_model=SemanticLayerRead)
async def restore_semantic_version(
    connection_id: UUID,
    number: int,
    payload: SemanticRestoreRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticLayerRead:
    """Put an old version back into the draft — `modify`, like a save.

    Into the draft, not the published document: publishing it is what writes
    the next version. Bound to the current snapshot: an entry whose table has
    gone since comes back flagged rather than missing.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    if payload.base_revision is None:
        raise SemanticBaseRevisionRequiredError(
            "Say which revision you are restoring over (`base_revision`)."
        )
    service = SemanticService(db, settings, authz)
    try:
        await service.restore(
            connection, number,
            base_revision=payload.base_revision, note=payload.note, ctx=ctx,
        )
    except SemanticConflictError as err:
        return problem_response(err)  # type: ignore[return-value]
    return await _read_payload(service, connection)


@router.get("/history", response_model=list[SemanticHistoryEntry])
async def get_semantic_history(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
    entity: str | None = Query(default=None, max_length=512),
    item: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SemanticHistoryEntry]:
    """Every change to one entry, newest first.

    `entity` alone is a table and everything on it; `entity` and `item` are one
    column or metric; `item` with an empty `entity` is a glossary term.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    entries = await SemanticService(db, settings, authz).history(
        connection.id, entity_key=entity, item_key=item, limit=limit
    )
    return [
        SemanticHistoryEntry(
            version=e.version.version,
            kind=e.change.kind,
            entity_key=e.change.entity_key,
            item_key=e.change.item_key,
            affects_sql=e.change.affects_sql,
            published_by_name=e.author,
            note=e.version.note,
            origin=e.version.origin or {},
            created_at=e.version.created_at,
        )
        for e in entries
    ]


@router.post("/check", response_model=SemanticExpressionResult)
async def check_metric_expression(
    connection_id: UUID,
    payload: SemanticExpressionCheck,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticExpressionResult:
    """Validate one expression against the live snapshot, saving nothing.

    This is what makes the metric editor honest: the same parser that will
    reject the expression at save time answers while the user is still typing.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    index = await SemanticService(db, settings, authz).schema_index(connection.id)

    valid, issue = check_expression(
        payload.expression,
        entity_table=payload.table,
        index=index,
        extra_tables=payload.required_joins,
        boolean=payload.is_filter,
    )
    return SemanticExpressionResult(valid=valid, issue=issue)


@router.post("/generate", response_model=SemanticJobRead, status_code=status.HTTP_202_ACCEPTED)
async def generate_semantic_layer(
    connection_id: UUID,
    payload: SemanticGenerateRequest,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticJobRead:
    """Queue a generation and return immediately.

    202, not 200: describing forty tables is minutes of model latency, so the
    answer to "did it work" lives on the job row the client then polls.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    service = SemanticService(db, settings, authz)
    job = await service.create_job(
        ctx=ctx,
        connection=connection,
        llm_config_id=payload.llm_config_id,
        mode=payload.mode,
        only_tables=payload.only_tables,
    )
    read = SemanticJobRead.model_validate(job)
    # Committed before the worker starts, or the worker races the transaction
    # that created the row it is about to load.
    await db.commit()
    await request.app.state.semantic_executor.submit(job.id)
    return read


@router.get("/jobs/latest", response_model=SemanticJobRead | None)
async def latest_job(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> Any:
    await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    return _job_read(
        await SemanticService(db, settings, authz).latest_job(connection_id)
    )


@router.get("/jobs/{job_id}", response_model=SemanticJobRead)
async def get_job(
    connection_id: UUID,
    job_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticJobRead:
    await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    job = await SemanticService(db, settings, authz).get_job(ctx, job_id)
    return SemanticJobRead.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=SemanticJobRead)
async def cancel_job(
    connection_id: UUID,
    job_id: UUID,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SemanticJobRead:
    await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    service = SemanticService(db, settings, authz)
    await service.cancel_job(ctx, job_id)
    await request.app.state.semantic_executor.cancel(job_id)
    job = await service.get_job(ctx, job_id)
    return SemanticJobRead.model_validate(job)
