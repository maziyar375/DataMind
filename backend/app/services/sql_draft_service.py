"""Plain-language authoring: a question in, an editable SQL draft out.

This is how a tile normally gets its SQL, and it is deliberately *not* a run.
There is no conversation, no `messages` row, no `runs` row, no SSE, no step
trail — a draft is a thing the user is looking at, and if they close the editor
it should leave nothing behind.

What it does reuse is the part that matters: `retrieve` → `generate` →
`validate`, the same three nodes a question in chat goes through, so a drafted
statement is written against the same schema block, the same semantic layer and
the same disclosure budget, and refused by the same guard. The preview under
the editor is produced by `execute_saved_sql` — the code that will run the tile
at 03:00 — so what the user approves is what will actually run.

The second entry point, `validate_sql`, is the hand-written path *and* the "I
edited what the model gave me" path, because they are the same thing: guard,
preview, no model. A user with no LLM provider configured can build a whole
dashboard through it.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import LLMError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector
from app.infra.db.models import DatabaseConnection, LlmConfig
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.pipeline.nodes import NodeDeps, generate, retrieve, validate
from app.pipeline.state import RunState
from app.services.query_service import (
    TileResult,
    bind_connector,
    execute_saved_sql,
    latest_snapshot,
    policy_from_snapshot,
    resolve_llm,
    secret_box,
)
from app.services.semantic_service import load_document
from app.sqlguard import guard
from app.sqlguard.validator import ValidationReport

log = get_logger(__name__)

# A preview is a sanity check, not a result: fifty rows answer "did this do
# what I meant" and cost the customer's database almost nothing, however wide
# the connection's own cap is.
PREVIEW_MAX_ROWS = 50

# A draft is interactive and the user is watching, so it gets one repair, not
# the run pipeline's budget, and a short deadline of its own rather than
# `run_deadline_seconds` — nothing about a draft is a run.
DRAFT_MAX_REPAIRS = 1
DRAFT_DEADLINE_SECONDS = 120


@dataclass(slots=True)
class SqlDraft:
    """A statement the user is about to accept, edit, or throw away."""

    sql: str
    validation_status: str
    validation_report: dict[str, Any]
    referenced_tables: list[str]
    # The heuristic's read of the preview's shape, for defaulting the editor's
    # chart pickers. Deterministic and free: no model is asked what to draw,
    # and the user overrides it anyway.
    chart_suggestion: dict[str, Any] | None = None
    # Which types this preview can actually be drawn as, and why not for the
    # rest. The picker disables what will not work rather than offering it and
    # letting the save path demote it with an apology.
    chart_options: list[dict[str, Any]] = field(default_factory=list)
    preview: TileResult | None = None
    question: str | None = None
    llm_config_id: UUID | None = None


async def draft_sql(
    db: AsyncSession,
    settings: Settings,
    *,
    connection_id: UUID,
    llm_config_id: UUID,
    question: str,
    owner_id: UUID,
) -> SqlDraft:
    """Ask a model for SQL that answers `question`, then guard it and run it."""
    connection = await _owned(db, DatabaseConnection, connection_id, owner_id)
    llm_config = await _owned(db, LlmConfig, llm_config_id, owner_id)
    snapshot = await _snapshot_or_refuse(db, connection)

    box = secret_box(settings)
    # One connector for the whole draft: `retrieve`/`generate`/`validate` never
    # touch it, but the preview does, and opening a second one to run fifty
    # rows would be a connection the user never asked for.
    connector = bind_connector(connection, box)
    try:
        state = _draft_state(connection, question)
        deps = NodeDeps(
            llm_gateway=LiteLLMGateway.from_settings(settings),
            llm=resolve_llm(llm_config, box),
            connector=connector,
            snapshot=snapshot,
            # No conversation: a draft has no history to inherit, and inventing
            # one would put another connection's answers in this prompt.
            history=[],
            policy=policy_from_snapshot(snapshot, connection),
            emit=_no_emit,
            semantic=await _semantic(db, connection),
        )

        await retrieve(state, deps)
        for _ in range(DRAFT_MAX_REPAIRS + 1):
            result = await generate(state, deps)
            if result.status == "FAILED":
                raise LLMError(
                    (state.error.hint if state.error else None)
                    or "The model could not produce a query."
                )
            if (await validate(state, deps)).goto != "generate":
                break

        attempt = state.attempts[-1]
        return await _draft(
            db,
            settings,
            connection=connection,
            sql=attempt.raw_sql,
            report=attempt.report,
            snapshot=snapshot,
            connector=connector,
            owner_id=owner_id,
            question=question,
            llm_config_id=llm_config.id,
        )
    finally:
        await connector.close()


async def validate_sql(
    db: AsyncSession,
    settings: Settings,
    *,
    connection_id: UUID,
    sql: str,
    owner_id: UUID,
) -> SqlDraft:
    """Guard and preview a statement the user wrote or edited. No model.

    Passing here is not authorisation to save, and saving is not authorisation
    to run: the tile save path guards again, and so does every refresh.
    """
    connection = await _owned(db, DatabaseConnection, connection_id, owner_id)
    snapshot = await _snapshot_or_refuse(db, connection)
    report, _ = guard(sql, policy_from_snapshot(snapshot, connection))

    return await _draft(
        db,
        settings,
        connection=connection,
        sql=sql,
        report=report,
        snapshot=snapshot,
        connector=None,
        owner_id=owner_id,
    )


# ── the shared tail: preview, then chart ─────────────────────────────────
async def _draft(
    db: AsyncSession,
    settings: Settings,
    *,
    connection: DatabaseConnection,
    sql: str,
    report: ValidationReport,
    snapshot: dict[str, Any],
    connector: DatabaseConnector | None,
    owner_id: UUID,
    question: str | None = None,
    llm_config_id: UUID | None = None,
) -> SqlDraft:
    """Attach a preview and a chart suggestion to a statement and its report.

    A rejected statement gets no preview — there is nothing to run — and the
    report is the answer, not an exception: the editor renders the guard's
    reasons inline, exactly as the semantic-layer editor does.
    """
    preview: TileResult | None = None
    if report.status == "VALID":
        preview = await execute_saved_sql(
            db,
            settings,
            # The raw statement, not the guard's rewrite: `execute_saved_sql`
            # re-guards from scratch, and previewing the rewrite would preview
            # something the tile will never be asked to run.
            sql=sql,
            connection=connection,
            owner_id=owner_id,
            max_rows=PREVIEW_MAX_ROWS,
            connector=connector,
            snapshot=snapshot,
        )

    return SqlDraft(
        sql=sql,
        validation_status=report.status,
        validation_report=report.model_dump(mode="json"),
        referenced_tables=list(report.referenced_tables),
        chart_suggestion=_chart_suggestion(preview),
        chart_options=_chart_options(preview),
        preview=preview,
        question=question,
        llm_config_id=llm_config_id,
    )


def _chart_suggestion(preview: TileResult | None) -> dict[str, Any] | None:
    """What the data shape says it should be drawn as, if anything.

    The *heuristic*, not a model: the chart node's question ("what does this
    question want to see?") needs a run behind it, while the editor only needs
    sensible defaults in its pickers that the user is about to override.
    """
    if preview is None or preview.status != "OK" or len(preview.columns) < 2:
        return None

    from app.charts import plan_chart, profile_result

    try:
        profile = profile_result(
            preview.columns, preview.rows, truncated=preview.truncated
        )
        plan = plan_chart(profile)
    except Exception:  # noqa: BLE001 — a defaulted picker is never worth a 500
        log.exception("draft_chart_suggestion_failed")
        return None

    return plan.intent.model_dump(mode="json") if plan.intent is not None else None


def _chart_options(preview: TileResult | None) -> list[dict[str, Any]]:
    """Per-type verdicts for the editor's picker.

    Empty when there is nothing to judge — no preview, or a result too narrow
    to chart at all. The editor reads an empty list as "no opinion" and leaves
    every type enabled, which is the same thing it did before this existed: a
    picker that greys everything out because the preview failed would be worse
    than one that lets the save path answer.
    """
    if preview is None or preview.status != "OK" or len(preview.columns) < 2:
        return []

    from app.charts import chart_options, profile_result

    try:
        profile = profile_result(
            preview.columns, preview.rows, truncated=preview.truncated
        )
        return [asdict(option) for option in chart_options(profile)]
    except Exception:  # noqa: BLE001 — same posture as the suggestion above
        log.exception("draft_chart_options_failed")
        return []


# ── loading ──────────────────────────────────────────────────────────────
async def _owned(db: AsyncSession, model: type, entity_id: UUID, owner_id: UUID) -> Any:
    entity = await db.get(model, entity_id)
    if entity is None or entity.owner_id != owner_id:
        raise NotFoundError(f"{model.__name__} not found.")
    return entity


async def _snapshot_or_refuse(
    db: AsyncSession, connection: DatabaseConnection
) -> dict[str, Any]:
    """An unsynced connection is a message, not an empty prompt.

    Without this the model would be handed a schema block with no tables in it
    and asked to write SQL anyway — spending a call to produce a statement the
    guard is then guaranteed to reject.
    """
    snapshot = await latest_snapshot(db, connection.id)
    if not snapshot.get("tables"):
        raise ValidationError(
            "Sync this connection's schema before drafting SQL against it.",
            connection_id=str(connection.id),
        )
    return snapshot


async def _semantic(
    db: AsyncSession, connection: DatabaseConnection
) -> dict[str, Any] | None:
    """The connection's semantic layer, on exactly the run path's terms.

    A draft is not a loophole around the layer's switch, nor around the
    disclosure budget `retrieve` renders it under.
    """
    document = await load_document(db, connection)
    return document.model_dump(mode="json") if document else None


def _draft_state(connection: DatabaseConnection, question: str) -> RunState:
    """A `RunState` for something that is not a run.

    Known wart: `RunState` requires `run_id` and `conversation_id`, and a draft
    has neither. Synthetic UUIDs are the honest cheap answer — they are never
    persisted, never emitted, and never looked up. The alternative, making both
    fields optional, would weaken the type for every real run to serve a path
    that writes nothing.
    """
    return RunState(
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        owner_id=connection.owner_id,
        connection_id=connection.id,
        question=question,
        dialect=connection.database_type,
        max_rows=PREVIEW_MAX_ROWS,
        max_repairs=DRAFT_MAX_REPAIRS,
        statement_timeout_ms=connection.statement_timeout_ms,
        disclosure_policy=connection.disclosure_policy,
        deadline_at=utcnow() + timedelta(seconds=DRAFT_DEADLINE_SECONDS),
    )


async def _no_emit(_event_type: str, _data: dict[str, Any]) -> None:
    """A draft has no run to attach events to and no client listening."""
    return None
