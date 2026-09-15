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
* **Every write is a version, and there is one writer** (`_publish`). A save, a
  generation, a restore and a delete each lock the head row, write an immutable
  `semantic_layer_versions` row with its typed changes, and copy the document
  to `semantic_layers.document` in the same transaction — so what the model
  reads is always exactly one numbered version. A person's write carries the
  revision it was made against and is refused, not merged, when somebody wrote
  in between (`docs/plans/semantic-layer-model.md` Phase 1).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.context import RequestContext
from app.core.errors import (
    ConflictError,
    NotFoundError,
    SemanticConflictError,
    SemanticNoChangesError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domain.ports.authz import Authorizer, ResourceRef
from app.domain.value_objects import HintBudget
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    SchemaSnapshotRow,
    SemanticJobRow,
    SemanticLayerChangeRow,
    SemanticLayerRow,
    SemanticLayerVersionRow,
    User,
)
from app.infra.db.session import get_sessionmaker
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.semantic import (
    SEMANTIC_PROMPT_VERSION,
    Change,
    Progress,
    SchemaIndex,
    SemanticDocument,
    bind_layer,
    build_index,
    diff_documents,
    generate_document,
    merge_documents,
)
from app.services import audit
from app.services.query_service import resolve_llm

log = get_logger(__name__)

ACTIVE_STATUSES = ("QUEUED", "RUNNING")

#: The layer's audit vocabulary, beside the service that writes it. Ids, versions
#: and counts only (audit rule 3): the names of the entries that changed live in
#: `semantic_layer_changes`, behind the layer's own `select`.
SEMANTIC_SAVED = "semantic.saved"
SEMANTIC_RESTORED = "semantic.restored"
SEMANTIC_DELETED = "semantic.deleted"
SEMANTIC_CONFLICT = "semantic.conflict"
SEMANTIC_GENERATION_QUEUED = "semantic.generation.queued"
SEMANTIC_GENERATION_SAVED = "semantic.generation.saved"

#: How long a version note may be. A sentence or a paragraph about *why*; the
#: what is already the change list.
MAX_NOTE_CHARS = 2_000

#: Output-token floor for generation, whatever the provider row says. Sized for
#: the widest table in the demo schema described in full, with room for a
#: reasoning model's scratchpad. A configured budget larger than this is kept.
SEMANTIC_MIN_MAX_TOKENS = 8192


class SemanticService:
    def __init__(
        self, db: AsyncSession, settings: Settings, authz: Authorizer | None = None
    ) -> None:
        self._db = db
        self._settings = settings
        #: Optional because `execute_job` is the body of a generation that was
        #: already authorized when it was queued — the worker resumes a decided
        #: job and has nobody to ask about. Every method that takes a `ctx`
        #: requires one, and says so by raising rather than by deciding for
        #: itself.
        self._authz = authz
        self._box = AesGcmSecretBox(
            settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
        )

    def _authorizer(self) -> Authorizer:
        """The authorizer, or a loud failure.

        A method that takes a `ctx` was called on a service built without one
        to ask with. That is a wiring mistake, and answering *no* would hide it
        behind a denial nobody could explain.
        """
        if self._authz is None:  # pragma: no cover - a wiring error, not a path
            raise RuntimeError(
                "SemanticService was constructed without an Authorizer and asked "
                "a question that needs one. Pass build_authorizer(db, settings)."
            )
        return self._authz

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
        doc = _bind(row.document if row and row.document else {}, snapshot)
        published, author = (
            await _version_with_author(self._db, connection.id, row.published_version)
            if row is not None and row.published_version else (None, "")
        )

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
            "published": published,
            "published_by_name": author,
        }
        return doc, row, facts

    # ── editing ──────────────────────────────────────────────────────────
    async def save(
        self,
        connection: DatabaseConnection,
        doc: SemanticDocument,
        *,
        base_revision: int,
        ctx: RequestContext,
        note: str = "",
    ) -> Published:
        """Persist an edited document as the next version.

        Joins are re-derived rather than accepted from the client: they are a
        reading of the catalog, and letting a form overwrite them would let a
        UI bug invent a cardinality nothing in the database supports.

        Refused with `E_SEMANTIC_CONFLICT` when `base_revision` is not the head
        row's revision — somebody saved, restored, deleted or a generation
        landed since this editor read the layer — and with
        `E_SEMANTIC_NO_CHANGES` when the document is the published one, rather
        than writing an identical version.
        """
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)

        snapshot = await self._snapshot(connection.id)
        bound = _bind(doc, snapshot)
        published = await _publish(
            self._db, connection_id=connection.id, head=head, document=bound,
            schema_version=snapshot["version"], author=ctx.user_id, note=note,
            origin={},
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_SAVED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail=published.audit_detail(),
        )
        return published

    async def restore(
        self,
        connection: DatabaseConnection,
        number: int,
        *,
        base_revision: int,
        ctx: RequestContext,
        note: str = "",
    ) -> Published:
        """Publish version `number` again, as a new version.

        Bound against the **current** snapshot, so flag-don't-drop applies: an
        entry whose table has since been dropped comes back red, not missing.
        History stays linear — restoring v9 over v13 writes v14, and v10–v13
        are still there to be restored in turn.
        """
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)
        source = await self.version(connection.id, number)

        snapshot = await self._snapshot(connection.id)
        bound = _bind(source.document or {}, snapshot)
        published = await _publish(
            self._db, connection_id=connection.id, head=head, document=bound,
            schema_version=snapshot["version"], author=ctx.user_id, note=note,
            origin={"restored_from": number},
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_RESTORED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={"version": published.version.version, "restored_from": number},
        )
        return published

    async def delete(
        self, connection: DatabaseConnection, *, ctx: RequestContext
    ) -> Published | None:
        """Publish an empty document as a tombstone version (D10).

        The history is kept: a deleted layer can be restored, and the runs that
        point at its versions still say what they were answered with. Versions
        go only when their connection does. Deleting a layer that is already
        empty writes nothing.
        """
        head = await _lock_head(self._db, connection.id)
        if head is None or not head.document:
            return None
        snapshot = await self._snapshot(connection.id)
        published = await _publish(
            self._db, connection_id=connection.id, head=head,
            document=SemanticDocument(), schema_version=snapshot["version"],
            author=ctx.user_id, note="", origin={"deleted": True},
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_DELETED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={"version": published.version.version},
        )
        return published

    # ── history ──────────────────────────────────────────────────────────
    async def versions(
        self, connection_id: UUID, *, limit: int = 50, before: int | None = None
    ) -> list[VersionSummary]:
        """Versions, newest first, with their change counts and authors.

        `before` pages: pass the lowest version number of the previous page.
        """
        statement = (
            select(SemanticLayerVersionRow, User.display_name)
            .outerjoin(User, User.id == SemanticLayerVersionRow.published_by)
            .where(SemanticLayerVersionRow.connection_id == connection_id)
        )
        if before is not None:
            statement = statement.where(SemanticLayerVersionRow.version < before)
        result = await self._db.execute(
            statement.order_by(SemanticLayerVersionRow.version.desc()).limit(limit)
        )
        rows = list(result.all())
        counts: dict[UUID, dict[str, int]] = {row.id: {} for row, _ in rows}
        if rows:
            changes = await self._db.execute(
                select(SemanticLayerChangeRow.version_id, SemanticLayerChangeRow.kind)
                .where(SemanticLayerChangeRow.version_id.in_(list(counts)))
            )
            for version_id, kind in changes.all():
                counts[version_id][kind] = counts[version_id].get(kind, 0) + 1
        return [
            VersionSummary(row=row, author=name or "", kinds=counts[row.id])
            for row, name in rows
        ]

    async def version(self, connection_id: UUID, number: int) -> SemanticLayerVersionRow:
        result = await self._db.execute(
            select(SemanticLayerVersionRow).where(
                SemanticLayerVersionRow.connection_id == connection_id,
                SemanticLayerVersionRow.version == number,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"Version {number} of this semantic layer does not exist.")
        return row

    async def version_author(self, row: SemanticLayerVersionRow) -> str:
        return await _author_name(self._db, row.published_by)

    async def changes(
        self, connection_id: UUID, number: int, *, against: int | None = None
    ) -> tuple[SemanticLayerVersionRow, int | None, list[Change]]:
        """Version `number`'s changes against its parent, or against `against`.

        Recomputed from the two immutable documents rather than read from the
        change rows, which hold keys only: the before and after of an entry are
        what a sentence is written from.
        """
        target = await self.version(connection_id, number)
        base_number = target.parent_version if against is None else against
        base = (
            SemanticDocument.model_validate(
                (await self.version(connection_id, base_number)).document or {}
            )
            if base_number else SemanticDocument()
        )
        after = SemanticDocument.model_validate(target.document or {})
        return target, base_number, diff_documents(base, after)

    async def history(
        self,
        connection_id: UUID,
        *,
        entity_key: str | None = None,
        item_key: str | None = None,
        limit: int = 100,
    ) -> list[HistoryEntry]:
        """One entry's changes, newest first, each with the version it landed in.

        `entity_key` alone is everything about a table — the entity and every
        column and metric on it; with `item_key` it is one column or metric.
        A glossary term is `item_key` with an empty `entity_key`.
        """
        statement = (
            select(SemanticLayerChangeRow, SemanticLayerVersionRow, User.display_name)
            .join(
                SemanticLayerVersionRow,
                SemanticLayerVersionRow.id == SemanticLayerChangeRow.version_id,
            )
            .outerjoin(User, User.id == SemanticLayerVersionRow.published_by)
            .where(SemanticLayerChangeRow.connection_id == connection_id)
        )
        if entity_key is not None:
            statement = statement.where(
                SemanticLayerChangeRow.entity_key == entity_key.strip().lower()
            )
        if item_key is not None:
            statement = statement.where(
                SemanticLayerChangeRow.item_key == item_key.strip().lower()
            )
        result = await self._db.execute(
            statement.order_by(
                SemanticLayerVersionRow.version.desc(), SemanticLayerChangeRow.kind
            ).limit(limit)
        )
        return [
            HistoryEntry(change=change, version=version, author=name or "")
            for change, version, name in result.all()
        ]

    async def diff(
        self, connection: DatabaseConnection, before: dict[str, Any], after: dict[str, Any]
    ) -> list[Change]:
        """Two documents' changes, both bound to the current snapshot. Saves nothing.

        Bound first so the binder's own rewrites — a resolved table name, a
        cleared date column — are not reported as edits somebody made.
        """
        snapshot = await self._snapshot(connection.id)
        try:
            return diff_documents(_bind(before, snapshot), _bind(after, snapshot))
        except ValueError as err:
            raise ValidationError("This semantic layer document is malformed.") from err

    # ── generation ───────────────────────────────────────────────────────
    async def create_job(
        self,
        *,
        ctx: RequestContext,
        connection: DatabaseConnection,
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
        # `select`: generating a layer *answers with* the model. `modify` on an
        # LLM config is equivalent to disclosing its key, so it must never be
        # what queuing a job requires.
        if config is None or not await self._authorizer().allowed(
            ctx, ResourceRef.to(ResourceType.LLM_CONFIG, config), Privilege.SELECT
        ):
            raise NotFoundError("Model configuration not found.")

        job = SemanticJobRow(
            id=uuid.uuid4(),
            connection_id=connection.id,
            owner_id=connection.owner_id,
            # Who asked for this build, as against who owns the connection it
            # describes. No longer the same person the moment a connection can
            # be shared — which is why both are recorded rather than one being
            # inferred from the other.
            actor_id=ctx.user_id,
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
        await audit.record(
            self._db, ctx, action=SEMANTIC_GENERATION_QUEUED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={
                "job_id": str(job.id), "mode": mode,
                "tables": len(job.only_tables) or len(snapshot["tables"]),
            },
        )
        return job

    async def latest_job(self, connection_id: UUID) -> SemanticJobRow | None:
        result = await self._db.execute(
            select(SemanticJobRow)
            .where(SemanticJobRow.connection_id == connection_id)
            .order_by(SemanticJobRow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_job(
        self, ctx: RequestContext, job_id: UUID, privilege: Privilege = Privilege.SELECT
    ) -> SemanticJobRow:
        """A generation job, if this principal may reach the layer it builds.

        A job is a **leaf**: it has no grant of its own and inherits from the
        connection whose semantic layer it is writing. That is why the question
        asked here names `semantic_layer` and the connection's id rather than
        the job's — a job row is an event in a resource's life, not a resource.
        """
        job = await self._db.get(SemanticJobRow, job_id)
        if job is None or not await self._authorizer().allowed(
            ctx,
            ResourceRef(type=ResourceType.SEMANTIC_LAYER, id=job.connection_id),
            privilege,
        ):
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
        # A description is prose, not SQL, so the output budget gets a floor
        # the run path does not need. 2048 was that floor and it was too low by
        # a factor of three: one `_TableDraft` for a forty-column table carries
        # a label, description and synonym list per column plus several metrics
        # with their SQL, and a reasoning model spends this same budget on its
        # scratchpad before emitting a token of it. Every overrun truncated the
        # JSON mid-object and cost the whole table.
        llm = resolve_llm(config, self._box, min_max_tokens=SEMANTIC_MIN_MAX_TOKENS)
        # Generation reads the same schema block a run does, under the same
        # policy: a layer can never be built from data the model would not
        # have been shown anyway.
        budget = HintBudget.from_policy(connection.disclosure_policy)
        mode, only_tables, actor_id = job.mode, list(job.only_tables), job.actor_id
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
                catalog_meta=snapshot["catalog_meta"],
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

        await self._persist_generated(
            job_id=job_id,
            actor_id=actor_id,
            connection_id=connection.id,
            generated=generated,
            mode=mode,
            only_tables=only_tables,
            snapshot=snapshot,
            llm_config_id=config.id,
            model_snapshot=llm.snapshot(),
        )
        await self._finish(job_id, "SUCCEEDED", stats=stats.as_dict())

    async def cancel_job(self, ctx: RequestContext, job_id: UUID) -> bool:
        job = await self.get_job(ctx, job_id, Privilege.MODIFY)
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
        job_id: UUID,
        actor_id: UUID | None,
        connection_id: UUID,
        generated: SemanticDocument,
        mode: str,
        only_tables: list[str],
        snapshot: dict[str, Any],
        llm_config_id: UUID,
        model_snapshot: dict[str, Any],
    ) -> None:
        """Merge the generation into the layer **as it is now**, under the lock.

        The job read nothing when it started. A save made during the minutes it
        spent at the provider is part of the current document by the time this
        runs, so the merge keeps it — before versions, the job merged into the
        document it had read at the start and silently overwrote that save.
        """
        async with get_sessionmaker()() as session:
            head = await _lock_head(session, connection_id)
            current = SemanticDocument.model_validate(
                head.document if head is not None and head.document else {}
            )
            merged = (
                generated if mode == "REPLACE" else merge_documents(current, generated)
            )
            # A partial run describes a subset; everything it did not touch stays.
            if only_tables:
                touched = {e.table.lower() for e in merged.entities}
                merged.entities.extend(
                    e.model_copy(deep=True)
                    for e in current.entities
                    if e.table.lower() not in touched
                )
            bound = _bind(merged, snapshot)

            published: Published | None = None
            if diff_documents(current, bound):
                published = await _publish(
                    session, connection_id=connection_id, head=head, document=bound,
                    schema_version=snapshot["version"], author=actor_id, note="",
                    origin={"generated_job_ids": [str(job_id)]},
                )
                head = published.head
            if head is None:
                # A generation that produced nothing on a connection that never
                # had a layer: no version to write, and no row to record it on.
                await session.commit()
                return
            head.generated_by_llm_config_id = llm_config_id
            head.model_snapshot = model_snapshot
            head.prompt_version = SEMANTIC_PROMPT_VERSION
            head.generated_at = utcnow()
            if published is not None and actor_id is not None:
                await audit.record(
                    session, RequestContext.on_behalf_of(actor_id),
                    action=SEMANTIC_GENERATION_SAVED,
                    resource_type=audit.SEMANTIC_LAYER, resource_id=connection_id,
                    detail={"job_id": str(job_id), "version": published.version.version},
                )
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
            # The same numbers the `stats` blob already carries, lifted into
            # columns so a usage query does not have to reach into JSONB — and
            # written *here* because all three exits (cancelled, failed and
            # succeeded) come through this one funnel. A cancelled build spent
            # what it spent before the cancel landed, and that is a true record.
            #
            # Only when a call was actually made: a build that got no further
            # than resolving its provider leaves nulls, which read as *not
            # measured* rather than as a free build.
            if stats.get("llm_calls"):
                fields["prompt_tokens"] = stats.get("prompt_tokens", 0)
                fields["completion_tokens"] = stats.get("completion_tokens", 0)
                fields["llm_latency_ms"] = stats.get("llm_latency_ms", 0)
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
                "dialect": "postgres", "version": 0, "catalog_meta": {},
            }
        return {
            "tables": row.tables,
            "relationships": row.relationships,
            "dialect": row.dialect,
            "version": row.version,
            # Database and schema descriptions, for the overview pass. A
            # pre-0012 snapshot reads back as `{}`, which is the same absence a
            # database with no comments produces — nothing downstream may tell
            # the two apart, and nothing downstream needs to.
            "catalog_meta": row.catalog_meta or {},
        }



# ── versions: the one writer ─────────────────────────────────────────────
@dataclass(slots=True)
class Published:
    """What one write produced: the head row, the new version, its changes."""

    head: SemanticLayerRow
    version: SemanticLayerVersionRow
    changes: list[Change]

    def audit_detail(self) -> dict[str, Any]:
        return {
            "version": self.version.version,
            "entities": self.version.entity_count,
            "metrics": self.version.metric_count,
            "issues": self.version.issue_count,
            "changes": len(self.changes),
            "affects_sql": any(c.affects_sql for c in self.changes),
        }


@dataclass(slots=True)
class VersionSummary:
    row: SemanticLayerVersionRow
    author: str
    #: Change counts by kind.
    kinds: dict[str, int]


@dataclass(slots=True)
class HistoryEntry:
    change: SemanticLayerChangeRow
    version: SemanticLayerVersionRow
    author: str


def document_sha256(document: dict[str, Any]) -> str:
    """sha256 of the canonical JSON. Migration `0032` computes the same bytes.

    Keys sorted, no whitespace, non-ASCII kept as written — so a Persian label
    hashes to what the database stores rather than to its escaped form.
    """
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stored(doc: SemanticDocument) -> dict[str, Any]:
    """The JSON a version and the head row hold for `doc`.

    A document with nothing in it is stored as `{}`, which every loader already
    reads as *no layer*: a deleted layer and one that was never written reach
    the prompt the same way — not at all.
    """
    dumped = doc.model_dump(mode="json")
    return {} if dumped == _EMPTY else dumped


_EMPTY = SemanticDocument().model_dump(mode="json")


async def _lock_head(db: AsyncSession, connection_id: UUID) -> SemanticLayerRow | None:
    """The head row, locked for the rest of this transaction.

    `FOR UPDATE` is what makes a save and a generation landing at the same
    moment take turns rather than both reading revision 7. A connection with no
    row yet has nothing to lock; two first writes racing there collide on
    `uq_semantic_layers_connection` instead, and the loser's request fails.
    """
    result = await db.execute(
        select(SemanticLayerRow)
        .where(SemanticLayerRow.connection_id == connection_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _require_revision(
    db: AsyncSession,
    ctx: RequestContext,
    connection_id: UUID,
    head: SemanticLayerRow | None,
    base_revision: int,
) -> None:
    """Refuse a write made against a revision that is no longer current.

    The refusal is audited (`semantic.conflict`, outcome `FAILED`) before it is
    raised, because §11's trigger for merging concurrent edits is a count of
    these rows. The route commits that row rather than letting the exception
    roll it back — the refusal is the thing that happened.
    """
    current = head.revision if head is not None else 0
    if base_revision == current:
        return
    published, author = (
        await _version_with_author(db, connection_id, head.published_version)
        if head is not None and head.published_version else (None, "")
    )
    await audit.record(
        db, ctx, action=SEMANTIC_CONFLICT,
        resource_type=audit.SEMANTIC_LAYER, resource_id=connection_id,
        outcome=audit.FAILED,
        detail={"base_revision": base_revision, "revision": current},
    )
    version = head.published_version if head is not None else None
    raise SemanticConflictError(
        (
            f"This semantic layer was changed (now v{version}) after you opened it."
            if version else "This semantic layer was changed after you opened it."
        ),
        revision=current,
        published_version=version,
        updated_by=(
            str(published.published_by) if published and published.published_by else None
        ),
        updated_by_name=author,
        updated_at=(
            published.created_at.isoformat() if published and published.created_at else None
        ),
    )


async def _publish(
    db: AsyncSession,
    *,
    connection_id: UUID,
    head: SemanticLayerRow | None,
    document: SemanticDocument,
    schema_version: int,
    author: UUID | None,
    note: str,
    origin: dict[str, Any],
) -> Published:
    """Write `document` as the next version and make it what the model reads.

    **The one writer.** In a single transaction: the version row, its change
    rows, the copy into `semantic_layers.document`, and `revision` and
    `published_version` moved forward. `sha256(head.document)` equals the new
    version's `document_sha256` when this returns (D3), and a test holds every
    write path to it.

    `document` must already be bound; the change list is computed against the
    published document, and an empty one is refused rather than written.
    """
    previous = SemanticDocument.model_validate(
        head.document if head is not None and head.document else {}
    )
    changes = diff_documents(previous, document)
    if not changes:
        raise SemanticNoChangesError("Nothing changed.")
    if len(note) > MAX_NOTE_CHARS:
        raise ValidationError(f"A note can be at most {MAX_NOTE_CHARS} characters.")

    if head is None:
        head = SemanticLayerRow(
            id=uuid.uuid4(), connection_id=connection_id, revision=0, document={},
        )
        db.add(head)
        await db.flush()

    stored = _stored(document)
    number = (head.published_version or 0) + 1
    version = SemanticLayerVersionRow(
        id=uuid.uuid4(),
        connection_id=connection_id,
        version=number,
        parent_version=head.published_version,
        document=stored,
        document_sha256=document_sha256(stored),
        schema_version=schema_version,
        published_by=author,
        note=note.strip(),
        origin=origin,
        entity_count=len(document.entities),
        metric_count=document.metric_count,
        reviewed_count=document.reviewed_count,
        issue_count=document.issue_count,
        created_at=utcnow(),
    )
    db.add(version)
    await db.flush()
    for change in changes:
        db.add(SemanticLayerChangeRow(
            id=uuid.uuid4(),
            version_id=version.id,
            connection_id=connection_id,
            kind=change.kind,
            entity_key=change.entity_key,
            item_key=change.item_key,
            affects_sql=change.affects_sql,
        ))

    head.document = stored
    head.schema_version = schema_version
    head.entity_count = version.entity_count
    head.metric_count = version.metric_count
    head.reviewed_count = version.reviewed_count
    head.issue_count = version.issue_count
    head.edited_at = version.created_at
    head.revision = (head.revision or 0) + 1
    head.published_version = number
    await db.flush()
    return Published(head=head, version=version, changes=changes)


async def _author_name(db: AsyncSession, user_id: UUID | None) -> str:
    if user_id is None:
        return ""
    user = await db.get(User, user_id)
    return (user.display_name or user.email) if user is not None else ""


async def _version_with_author(
    db: AsyncSession, connection_id: UUID, number: int
) -> tuple[SemanticLayerVersionRow | None, str]:
    result = await db.execute(
        select(SemanticLayerVersionRow).where(
            SemanticLayerVersionRow.connection_id == connection_id,
            SemanticLayerVersionRow.version == number,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None, ""
    return row, await _author_name(db, row.published_by)


def _bind(raw: SemanticDocument | dict[str, Any], snapshot: dict[str, Any]) -> SemanticDocument:
    """`bind_layer` over a snapshot dict, in the shape both loaders return."""
    return bind_layer(
        raw,
        tables=snapshot.get("tables") or [],
        relationships=snapshot.get("relationships") or [],
        dialect=snapshot.get("dialect") or "postgres",
    )


@dataclass(frozen=True, slots=True)
class LoadedLayer:
    """The layer a reader got, and which version it was.

    `version` is `0` when no layer reached the reader — none written, the
    switch off, or a document that would not deserialise — which is the value
    `runs.semantic_layer_version` records for "answered without a layer".
    """

    document: SemanticDocument | None
    version: int = 0


async def load_layer(
    db: AsyncSession,
    connection: DatabaseConnection,
    *,
    snapshot: dict[str, Any],
) -> LoadedLayer:
    """`load_document`, plus the version it loaded — for the readers that record it.

    The version comes off the same row as the document, so the two cannot
    disagree: `semantic_layers.document` is a copy of `published_version`.
    """
    if not connection.semantic_layer_enabled:
        return LoadedLayer(None)
    result = await db.execute(
        select(SemanticLayerRow).where(
            SemanticLayerRow.connection_id == connection.id
        )
    )
    row = result.scalar_one_or_none()
    if row is None or not row.document:
        return LoadedLayer(None)
    try:
        document = _bind(row.document, snapshot)
    except Exception:
        # A document that will not deserialise is a bug worth logging, never a
        # reason to fail the user's question.
        log.warning("semantic_document_unreadable", connection_id=str(connection.id))
        return LoadedLayer(None)
    return LoadedLayer(document, row.published_version or 0)


async def load_document(
    db: AsyncSession,
    connection: DatabaseConnection,
    *,
    snapshot: dict[str, Any],
) -> SemanticDocument | None:
    """The layer a run should use, bound to `snapshot`, or None.

    Kept as a free function because the pipeline needs exactly this and
    nothing else, and because a disabled switch has to be honoured in one
    place rather than at every call site.

    **Bound on every load, against the snapshot the caller is about to render
    the schema block from.** The stored `valid` flags are only as current as
    the last save, and a re-sync does not touch the layer — so a reader that
    trusted them would send the model a metric over a column the editor
    already shows as dropped. The snapshot is a required argument rather than
    something loaded here, because every caller already holds the one its
    prompt is built on, and binding against a second read of it could bind
    against a different version.

    Measured on the `sales` fixture (21 entities, 14 metrics): about 6 ms per
    load, and about 11 ms on `aurora`'s 34 metrics. Not cached: a cache keyed on
    anything but the document and the snapshot would be a second source of
    truth, and the cost is below a single provider round trip by two orders of
    magnitude.
    """
    return (await load_layer(db, connection, snapshot=snapshot)).document
