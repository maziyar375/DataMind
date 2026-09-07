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

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import AuthzDep, CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    SemanticExpressionCheck,
    SemanticExpressionResult,
    SemanticGenerateRequest,
    SemanticJobRead,
    SemanticLayerRead,
    SemanticSaveRequest,
    SemanticTableFact,
)
from app.api.v1.access import attach_access_routes
from app.core.errors import NotFoundError, ValidationError
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.db.models import DatabaseConnection, SemanticJobRow
from app.semantic import SemanticDocument, check_expression
from app.services.policy import require
from app.services.semantic_service import SemanticService

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
    return SemanticLayerRead(
        document=doc.model_dump(mode="json"),
        exists=row is not None,
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
    )


@router.get("", response_model=SemanticLayerRead)
async def get_semantic_layer(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> SemanticLayerRead:
    """The stored document, re-bound to the newest schema snapshot.

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
    """Replace the document wholesale.

    The whole document rather than a patch per entity: an edit routinely moves
    a definition between entities (a metric belongs on the fact table, not the
    dimension the user opened), and a partial update cannot express that
    atomically.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    try:
        doc = SemanticDocument.model_validate(payload.document)
    except Exception as err:
        raise ValidationError("This semantic layer document is malformed.") from err

    service = SemanticService(db, settings, authz)
    await service.save(connection, doc)
    return await _read_payload(service, connection)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_semantic_layer(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> None:
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.DELETE)
    await SemanticService(db, settings, authz).delete(connection.id)


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
