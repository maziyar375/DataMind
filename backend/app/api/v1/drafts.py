"""HTTP shape for SQL drafting. No business logic — see
`services/sql_draft_service.py`.

Two routes, one response shape, because they are two ways of arriving at the
same thing: a statement, the guard's verdict on it, and a preview of what it
returns. The first asks a model; the second never does, which is what makes a
dashboard buildable with no LLM provider configured at all.

Not mounted under `/connections/{id}` like the semantic layer: a draft belongs
to whoever is writing it, not to the connection, and the connection is one of
its inputs — the editor lets the user change it without changing the URL.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    SqlDraftRead,
    SqlDraftRequest,
    SqlValidateRequest,
    TileResultRead,
)
from app.services.sql_draft_service import SqlDraft, draft_sql, validate_sql

router = APIRouter(prefix="/sql", tags=["sql"])


def _read(draft: SqlDraft) -> SqlDraftRead:
    return SqlDraftRead(
        sql=draft.sql,
        validation_status=draft.validation_status,
        validation_report=draft.validation_report,
        referenced_tables=draft.referenced_tables,
        chart_suggestion=draft.chart_suggestion,
        chart_options=draft.chart_options,
        preview=(
            None
            if draft.preview is None
            else TileResultRead.model_validate(draft.preview.to_payload())
        ),
        question=draft.question,
        llm_config_id=draft.llm_config_id,
    )


@router.post("/drafts", response_model=SqlDraftRead)
async def create_draft(
    payload: SqlDraftRequest, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> SqlDraftRead:
    """Turn a plain-language question into an editable, guarded draft.

    Writes nothing: no conversation, no message, no run. Closing the editor
    leaves no trace, which is the difference between drafting and asking.
    """
    return _read(
        await draft_sql(
            db,
            settings,
            connection_id=payload.connection_id,
            llm_config_id=payload.llm_config_id,
            question=payload.question,
            owner_id=ctx.user_id,
        )
    )


@router.post("/drafts/validate", response_model=SqlDraftRead)
async def validate_draft(
    payload: SqlValidateRequest, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> SqlDraftRead:
    """Guard and preview a statement the user wrote or edited. No model runs.

    Passing here authorises nothing: saving a tile guards again, and so does
    every refresh after it.
    """
    return _read(
        await validate_sql(
            db,
            settings,
            connection_id=payload.connection_id,
            sql=payload.sql,
            owner_id=ctx.user_id,
        )
    )
