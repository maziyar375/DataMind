"""HTTP shape for the semantic layer. No business logic — see
`services/semantic_service.py`.

Mounted under `/connections/{connection_id}/semantic` because a semantic layer
has no life of its own: it describes exactly one connection's schema, is
scoped by that connection's ownership, and dies with it.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    SemanticExpressionCheck,
    SemanticExpressionResult,
    SemanticGenerateRequest,
    SemanticJobRead,
    SemanticLayerRead,
    SemanticSaveRequest,
    SemanticTableFact,
)
from app.core.errors import NotFoundError, ValidationError
from app.infra.db.models import DatabaseConnection, SemanticJobRow
from app.semantic import SemanticDocument, check_expression
from app.services.semantic_service import SemanticService

router = APIRouter(prefix="/connections/{connection_id}/semantic", tags=["semantic"])


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
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> SemanticLayerRead:
    """The stored document, re-bound to the newest schema snapshot.

    Returns a 200 with an empty document rather than a 404 when nothing has
    been generated: "you have no semantic layer yet" is a state the editor
    renders, not an error it handles.
    """
    connection = await _owned(db, connection_id, ctx)
    return await _read_payload(SemanticService(db, settings), connection)


@router.put("", response_model=SemanticLayerRead)
async def save_semantic_layer(
    connection_id: UUID,
    payload: SemanticSaveRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> SemanticLayerRead:
    """Replace the document wholesale.

    The whole document rather than a patch per entity: an edit routinely moves
    a definition between entities (a metric belongs on the fact table, not the
    dimension the user opened), and a partial update cannot express that
    atomically.
    """
    connection = await _owned(db, connection_id, ctx)
    try:
        doc = SemanticDocument.model_validate(payload.document)
    except Exception as err:
        raise ValidationError("This semantic layer document is malformed.") from err

    service = SemanticService(db, settings)
    await service.save(connection, doc)
    return await _read_payload(service, connection)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_semantic_layer(
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> None:
    connection = await _owned(db, connection_id, ctx)
    await SemanticService(db, settings).delete(connection.id)


@router.post("/check", response_model=SemanticExpressionResult)
async def check_metric_expression(
    connection_id: UUID,
    payload: SemanticExpressionCheck,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> SemanticExpressionResult:
    """Validate one expression against the live snapshot, saving nothing.

    This is what makes the metric editor honest: the same parser that will
    reject the expression at save time answers while the user is still typing.
    """
    connection = await _owned(db, connection_id, ctx)
    index = await SemanticService(db, settings).schema_index(connection.id)

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
) -> SemanticJobRead:
    """Queue a generation and return immediately.

    202, not 200: describing forty tables is minutes of model latency, so the
    answer to "did it work" lives on the job row the client then polls.
    """
    connection = await _owned(db, connection_id, ctx)
    service = SemanticService(db, settings)
    job = await service.create_job(
        connection=connection,
        owner_id=ctx.user_id,
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
    connection_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> Any:
    await _owned(db, connection_id, ctx)
    return _job_read(await SemanticService(db, settings).latest_job(connection_id))


@router.get("/jobs/{job_id}", response_model=SemanticJobRead)
async def get_job(
    connection_id: UUID, job_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> SemanticJobRead:
    await _owned(db, connection_id, ctx)
    job = await SemanticService(db, settings).get_job(job_id, ctx.user_id)
    return SemanticJobRead.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=SemanticJobRead)
async def cancel_job(
    connection_id: UUID,
    job_id: UUID,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> SemanticJobRead:
    await _owned(db, connection_id, ctx)
    service = SemanticService(db, settings)
    await service.cancel_job(job_id, ctx.user_id)
    await request.app.state.semantic_executor.cancel(job_id)
    job = await service.get_job(job_id, ctx.user_id)
    return SemanticJobRead.model_validate(job)
