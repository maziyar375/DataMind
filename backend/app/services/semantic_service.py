"""Use cases for the semantic layer: read, edit, generate.

The transaction boundary lives here, as it does in `run_service`. Two things
are worth knowing before changing this file:

* **Every read is re-validated against the current snapshot.** A document is
  stored as it was written, not as it was true; the schema moves underneath
  it. Binding on read means the UI shows drift the moment a re-sync creates
  it, without a migration or a background sweep.
* **Generation does not hold a transaction.** Describing forty tables is
  minutes of model latency. The job row is updated from short-lived sessions
  of its own so a poller sees progress, and so a long generation cannot pin a
  connection from the pool.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.value_objects import HintBudget
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    SchemaSnapshotRow,
    SemanticJobRow,
    SemanticLayerRow,
)
from app.infra.db.session import get_sessionmaker
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.semantic import (
    SEMANTIC_PROMPT_VERSION,
    Progress,
    SchemaIndex,
    SemanticDocument,
    build_index,
    derive_joins,
    generate_document,
    merge_documents,
    validate_document,
)
from app.services.query_service import resolve_llm

log = get_logger(__name__)

ACTIVE_STATUSES = ("QUEUED", "RUNNING")


class SemanticService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._box = AesGcmSecretBox(
            settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
        )

    # ── reading ──────────────────────────────────────────────────────────
    async def layer_row(self, connection_id: UUID) -> SemanticLayerRow | None:
        result = await self._db.execute(
            select(SemanticLayerRow).where(
                SemanticLayerRow.connection_id == connection_id
            )
        )
        return result.scalar_one_or_none()

    async def read(self, connection: DatabaseConnection) -> tuple[
        SemanticDocument, SemanticLayerRow | None, dict[str, Any]
    ]:
        """The stored document, re-bound to the newest snapshot.

        Returns the document, its row (None when nothing has been generated),
        and the snapshot facts the UI needs to offer a sensible editor: the
        table list, and whether the schema has moved since the layer was
        written.
        """
        row = await self.layer_row(connection.id)
        snapshot = await self._snapshot(connection.id)
        index = build_index(snapshot["tables"], snapshot["dialect"])

        doc = (
            SemanticDocument.model_validate(row.document)
            if row and row.document
            else SemanticDocument()
        )
        doc = validate_document(doc, index)

        described = {e.table.lower() for e in doc.entities}
        facts = {
            "schema_version": snapshot["version"],
            "schema_dialect": snapshot["dialect"],
            "tables": [
                {
                    "table": f"{t['schema']}.{t['name']}".lower(),
                    "column_count": len(t.get("columns", [])),
                    "approx_row_count": t.get("approx_row_count"),
                    "described": f"{t['schema']}.{t['name']}".lower() in described,
                }
                for t in snapshot["tables"]
            ],
            "stale": bool(row and row.schema_version != snapshot["version"]),
        }
        return doc, row, facts

    # ── editing ──────────────────────────────────────────────────────────
    async def save(
        self, connection: DatabaseConnection, doc: SemanticDocument
    ) -> SemanticLayerRow:
        """Persist an edited document, bound and counted.

        Joins are re-derived rather than accepted from the client: they are a
        reading of the catalog, and letting a form overwrite them would let a
        UI bug invent a cardinality nothing in the database supports.
        """
        snapshot = await self._snapshot(connection.id)
        index = build_index(snapshot["tables"], snapshot["dialect"])
        doc.joins = derive_joins(snapshot["relationships"], index)
        bound = validate_document(doc, index)

        row = await self.layer_row(connection.id)
        if row is None:
            row = SemanticLayerRow(id=uuid.uuid4(), connection_id=connection.id)
            self._db.add(row)

        row.document = bound.model_dump(mode="json")
        row.schema_version = snapshot["version"]
        row.entity_count = len(bound.entities)
        row.metric_count = bound.metric_count
        row.reviewed_count = bound.reviewed_count
        row.issue_count = bound.issue_count
        row.edited_at = utcnow()
        await self._db.flush()
        return row

    async def delete(self, connection_id: UUID) -> None:
        row = await self.layer_row(connection_id)
        if row is not None:
            await self._db.delete(row)

    # ── generation ───────────────────────────────────────────────────────
    async def create_job(
        self,
        *,
        connection: DatabaseConnection,
        owner_id: UUID,
        llm_config_id: UUID,
        mode: str,
        only_tables: list[str] | None,
    ) -> SemanticJobRow:
        """Queue a generation. Refuses when one is already in flight.

        The refusal is the point: two concurrent generations would race on one
        row and the loser's tokens would be spent for nothing.
        """
        running = await self._db.execute(
            select(SemanticJobRow).where(
                SemanticJobRow.connection_id == connection.id,
                SemanticJobRow.status.in_(ACTIVE_STATUSES),
            )
        )
        if running.scalars().first() is not None:
            raise ConflictError(
                "A semantic layer is already being generated for this connection."
            )

        snapshot = await self._snapshot(connection.id)
        if not snapshot["tables"]:
            raise ValidationError(
                "Sync this connection's schema before generating a semantic layer."
            )

        config = await self._db.get(LlmConfig, llm_config_id)
        if config is None or config.owner_id != owner_id:
            raise NotFoundError("Model configuration not found.")

        job = SemanticJobRow(
            id=uuid.uuid4(),
            connection_id=connection.id,
            owner_id=owner_id,
            llm_config_id=config.id,
            model_snapshot={"provider": config.provider, "model": config.model},
            mode=mode,
            only_tables=[t.lower() for t in (only_tables or [])],
            status="QUEUED",
            progress_total=(
                len(only_tables) if only_tables else len(snapshot["tables"])
            ) + 2,
        )
        self._db.add(job)
        await self._db.flush()
        return job

    async def latest_job(self, connection_id: UUID) -> SemanticJobRow | None:
        result = await self._db.execute(
            select(SemanticJobRow)
            .where(SemanticJobRow.connection_id == connection_id)
            .order_by(SemanticJobRow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_job(self, job_id: UUID, owner_id: UUID) -> SemanticJobRow:
        job = await self._db.get(SemanticJobRow, job_id)
        if job is None or job.owner_id != owner_id:
            raise NotFoundError("Generation job not found.")
        return job

    async def execute_job(self, job_id: UUID, cancelled: asyncio.Event) -> None:
        """The body of a generation. Runs outside the request lifecycle.

        This method owns no long transaction: it reads what it needs, closes,
        spends minutes talking to a provider, and writes the result at the
        end. Progress lands through `_progress`, which uses a session of its
        own for exactly the same reason.
        """
        job = await self._db.get(SemanticJobRow, job_id)
        if job is None:
            return
        connection = await self._db.get(DatabaseConnection, job.connection_id)
        config = (
            await self._db.get(LlmConfig, job.llm_config_id)
            if job.llm_config_id else None
        )
        if connection is None or config is None:
            await self._finish(job_id, "FAILED", error="The connection or model was removed.")
            return

        snapshot = await self._snapshot(connection.id)
        existing_row = await self.layer_row(connection.id)
        existing = (
            SemanticDocument.model_validate(existing_row.document)
            if existing_row and existing_row.document
            else SemanticDocument()
        )
        # A description is prose, not SQL, so the output budget gets a floor
        # the run path does not need.
        llm = resolve_llm(config, self._box, min_max_tokens=2048)
        # Generation reads the same schema block a run does, under the same
        # policy: a layer can never be built from data the model would not
        # have been shown anyway.
        budget = HintBudget.from_policy(connection.disclosure_policy)
        mode, only_tables = job.mode, list(job.only_tables)
        await self._db.commit()

        await self._start(job_id)
        gateway = LiteLLMGateway.from_settings(self._settings)

        try:
            generated, stats = await generate_document(
                tables=snapshot["tables"],
                relationships=snapshot["relationships"],
                dialect=snapshot["dialect"],
                gateway=gateway,
                llm=llm,
                budget=budget,
                only_tables=only_tables or None,
                on_progress=lambda p: self._progress(job_id, p),
                cancelled=cancelled.is_set,
            )
        except asyncio.CancelledError:
            await self._finish(job_id, "CANCELLED")
            raise
        except Exception as err:  # a provider failure is a job failure, not a 500
            log.exception("semantic_job_failed", job_id=str(job_id))
            await self._finish(job_id, "FAILED", error=str(err)[:500])
            return

        if cancelled.is_set():
            await self._finish(job_id, "CANCELLED", stats=stats.as_dict())
            return

        if stats.tables_described == 0 and stats.tables_failed:
            # Per-table failures are tolerated so one bad table cannot sink a
            # run — but *every* table failing is a provider or key problem, and
            # reporting it as success would leave the user staring at an empty
            # editor with no idea why.
            await self._finish(
                job_id,
                "FAILED",
                error=(
                    "The model could not describe any table. Check that this "
                    "model configuration works under Models, then try again."
                ),
                stats=stats.as_dict(),
            )
            return

        merged = (
            generated if mode == "REPLACE" else merge_documents(existing, generated)
        )
        # A partial run describes a subset; everything it did not touch stays.
        if only_tables:
            touched = {e.table.lower() for e in merged.entities}
            merged.entities.extend(
                e.model_copy(deep=True)
                for e in existing.entities
                if e.table.lower() not in touched
            )

        await self._persist_generated(
            connection_id=connection.id,
            doc=merged,
            snapshot=snapshot,
            llm_config_id=config.id,
            model_snapshot=llm.snapshot(),
        )
        await self._finish(job_id, "SUCCEEDED", stats=stats.as_dict())

    async def cancel_job(self, job_id: UUID, owner_id: UUID) -> bool:
        job = await self.get_job(job_id, owner_id)
        if job.status not in ACTIVE_STATUSES:
            return False
        job.status = "CANCELLED"
        job.finished_at = utcnow()
        await self._db.flush()
        return True

    # ── internals ────────────────────────────────────────────────────────
    async def _persist_generated(
        self,
        *,
        connection_id: UUID,
        doc: SemanticDocument,
        snapshot: dict[str, Any],
        llm_config_id: UUID,
        model_snapshot: dict[str, Any],
    ) -> None:
        async with get_sessionmaker()() as session:
            index = build_index(snapshot["tables"], snapshot["dialect"])
            doc.joins = derive_joins(snapshot["relationships"], index)
            bound = validate_document(doc, index)

            result = await session.execute(
                select(SemanticLayerRow).where(
                    SemanticLayerRow.connection_id == connection_id
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = SemanticLayerRow(id=uuid.uuid4(), connection_id=connection_id)
                session.add(row)

            row.document = bound.model_dump(mode="json")
            row.schema_version = snapshot["version"]
            row.entity_count = len(bound.entities)
            row.metric_count = bound.metric_count
            row.reviewed_count = bound.reviewed_count
            row.issue_count = bound.issue_count
            row.generated_by_llm_config_id = llm_config_id
            row.model_snapshot = model_snapshot
            row.prompt_version = SEMANTIC_PROMPT_VERSION
            row.generated_at = utcnow()
            await session.commit()

    async def _start(self, job_id: UUID) -> None:
        await self._touch_job(job_id, status="RUNNING", started_at=utcnow())

    async def _progress(self, job_id: UUID, progress: Progress) -> None:
        await self._touch_job(
            job_id,
            phase=progress.phase[:200],
            progress_current=progress.current,
            progress_total=progress.total,
        )

    async def _finish(
        self,
        job_id: UUID,
        status: str,
        *,
        error: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {"status": status, "finished_at": utcnow()}
        if error is not None:
            fields["error_message"] = error
        if stats is not None:
            fields["stats"] = stats
        await self._touch_job(job_id, **fields)

    async def _touch_job(self, job_id: UUID, **fields: Any) -> None:
        """Update the job row in a session of its own, and commit.

        A poller can only see committed state, and the generation itself holds
        no transaction — so progress cannot ride along on one.
        """
        async with get_sessionmaker()() as session:
            job = await session.get(SemanticJobRow, job_id)
            if job is None:
                return
            # A cancel that landed while a table was in flight must not be
            # overwritten by that table's progress update.
            if job.status == "CANCELLED" and fields.get("status") == "RUNNING":
                return
            for key, value in fields.items():
                setattr(job, key, value)
            await session.commit()

    async def schema_index(self, connection_id: UUID) -> SchemaIndex:
        """The name resolver for one connection's newest snapshot.

        Exposed because the metric editor validates against exactly this and
        should not have to reach past the service to get it.
        """
        snapshot = await self._snapshot(connection_id)
        return build_index(snapshot["tables"], snapshot["dialect"])

    async def _snapshot(self, connection_id: UUID) -> dict[str, Any]:
        result = await self._db.execute(
            select(SchemaSnapshotRow)
            .where(SchemaSnapshotRow.connection_id == connection_id)
            .order_by(SchemaSnapshotRow.version.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {
                "tables": [], "relationships": [],
                "dialect": "postgres", "version": 0,
            }
        return {
            "tables": row.tables,
            "relationships": row.relationships,
            "dialect": row.dialect,
            "version": row.version,
        }



async def load_document(
    db: AsyncSession, connection: DatabaseConnection
) -> SemanticDocument | None:
    """The layer a run should use, or None.

    Kept as a free function because the pipeline needs exactly this and
    nothing else, and because a disabled switch has to be honoured in one
    place rather than at every call site.
    """
    if not connection.semantic_layer_enabled:
        return None
    result = await db.execute(
        select(SemanticLayerRow).where(
            SemanticLayerRow.connection_id == connection.id
        )
    )
    row = result.scalar_one_or_none()
    if row is None or not row.document:
        return None
    try:
        return SemanticDocument.model_validate(row.document)
    except Exception:
        # A document that will not deserialise is a bug worth logging, never a
        # reason to fail the user's question.
        log.warning("semantic_document_unreadable", connection_id=str(connection.id))
        return None
