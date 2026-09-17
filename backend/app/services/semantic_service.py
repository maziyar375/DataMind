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
* **An edit lands in the draft; only publishing makes a version** (Phase 2).
  A save, a generation, a restore and an import each write
  `semantic_layers.draft_document`, which **no loader reads** — `load_layer`
  and `load_document` read `document`, the published copy. So an edit, and a
  model's guess about the schema, reach no answer until a person publishes
  them. The one reader of a draft outside the editor is `load_draft`, and its
  one caller is a benchmark run somebody asked to score a draft with.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
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
    SemanticNoteRequiredError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domain.ports.authz import Authorizer, ResourceRef
from app.domain.value_objects import HintBudget
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import (
    BenchmarkRun,
    DatabaseConnection,
    GeneratedQuery,
    LlmConfig,
    Run,
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
    IGNORED,
    SEMANTIC_PROMPT_VERSION,
    USED,
    Change,
    Progress,
    SchemaIndex,
    SemanticDocument,
    attribute,
    bind_layer,
    build_index,
    diff_documents,
    generate_document,
    merge_documents,
)
from app.semantic import limits as semantic_limits
from app.services import audit
from app.services.query_service import resolve_llm
from app.services.semantic_transfer import ImportReport, LayerFile, build_file, read_file

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
SEMANTIC_DRAFT_SAVED = "semantic.draft.saved"
SEMANTIC_DRAFT_DISCARDED = "semantic.draft.discarded"
SEMANTIC_PUBLISHED = "semantic.published"
SEMANTIC_EXPORTED = "semantic.exported"
SEMANTIC_IMPORTED = "semantic.imported"

#: The window the *Metrics in use* table counts over.
METRIC_USE_DAYS = 30

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
        """What the editor edits, re-bound to the newest snapshot.

        That is the **draft** when one exists and the published document
        otherwise — a NULL draft *is* the published document. Returns the
        document, its row (None when nothing has been generated), and the facts
        the UI needs to frame it: the table list, whether the schema has moved
        since the layer was written, which version is published and by whom,
        and the draft's changes against it.
        """
        row = await self.layer_row(connection.id)
        snapshot = await self._snapshot(connection.id)
        published_doc = _bind(row.document if row and row.document else {}, snapshot)
        has_draft = row is not None and row.draft_document is not None
        doc = _bind(row.draft_document or {}, snapshot) if has_draft else published_doc
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
            "has_draft": has_draft,
            "published_exists": not published_doc.is_empty,
            # Both sides bound to the same snapshot, so the binder's own
            # rewrites are not reported as edits somebody made.
            "unpublished_changes": (
                diff_documents(published_doc, doc) if has_draft else []
            ),
            "draft_updated_by_name": (
                await _author_name(self._db, row.draft_updated_by) if has_draft else ""
            ),
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
        """Save `doc` and publish it as the next version, in one step.

        The `PUT /semantic` of API clients and scripts. The editor saves a
        draft and publishes it separately; this does both at once and discards
        any draft somebody had, because the document it is given replaces
        everything. The note stays optional here, as it was before drafts —
        a script that worked against Phase 1 keeps working.

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
    ) -> DraftWritten:
        """Put version `number` back into the draft.

        Into the draft, not the published document: a restore is somebody's
        decision about what the model should read, and it gets the same review
        before it answers anything that any other edit gets. Publishing it
        writes the next version, so history stays linear — restoring v9 over
        v13 and publishing writes v14, and v10–v13 are still there.

        Bound against the **current** snapshot, so flag-don't-drop applies: an
        entry whose table has since been dropped comes back red, not missing.
        `note` is accepted for the API's sake and not stored — a note belongs to
        the version the draft becomes, and is asked for when it is published.
        """
        del note
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)
        source = await self.version(connection.id, number)

        snapshot = await self._snapshot(connection.id)
        bound = _bind(source.document or {}, snapshot)
        written = await _write_draft(
            self._db, connection_id=connection.id, head=head, document=bound,
            author=ctx.user_id, origin={"restored_from": number},
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_RESTORED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={"revision": written.head.revision, "restored_from": number},
        )
        return written

    async def delete(
        self, connection: DatabaseConnection, *, ctx: RequestContext
    ) -> Published | None:
        """Publish an empty document as a tombstone version (D10), and drop the draft.

        The history is kept: a deleted layer can be restored, and the runs that
        point at its versions still say what they were answered with. Versions
        go only when their connection does. A layer with nothing published and
        only a draft loses the draft and writes no version; one that is empty
        in both writes nothing.
        """
        head = await _lock_head(self._db, connection.id)
        if head is None or (not head.document and head.draft_document is None):
            return None
        if not head.document:
            _clear_draft(head)
            head.revision = (head.revision or 0) + 1
            await self._db.flush()
            await audit.record(
                self._db, ctx, action=SEMANTIC_DRAFT_DISCARDED,
                resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
                detail={"revision": head.revision},
            )
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

    # ── the draft ────────────────────────────────────────────────────────
    async def save_draft(
        self,
        connection: DatabaseConnection,
        doc: SemanticDocument,
        *,
        base_revision: int,
        ctx: RequestContext,
    ) -> DraftWritten:
        """Write the editor's document to the draft. Nothing a run reads moves.

        Refused with `E_SEMANTIC_CONFLICT` on a stale `base_revision`, and with
        `E_SEMANTIC_NO_CHANGES` when the document is the draft already. A
        document that equals the published one leaves no draft behind — there
        is nothing unpublished to show.
        """
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)
        snapshot = await self._snapshot(connection.id)
        written = await _write_draft(
            self._db, connection_id=connection.id, head=head,
            document=_bind(doc, snapshot), author=ctx.user_id, origin=None,
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_DRAFT_SAVED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={"revision": written.head.revision, "changes": len(written.changes)},
        )
        return written

    async def discard_draft(
        self, connection: DatabaseConnection, *, base_revision: int, ctx: RequestContext
    ) -> SemanticLayerRow:
        """Throw the draft away. The published document is untouched.

        A revision like any other write: an editor that read the draft before
        somebody else changed it cannot discard what it never saw.
        """
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)
        if head is None or head.draft_document is None:
            raise SemanticNoChangesError("There is no draft to discard.")
        _clear_draft(head)
        head.revision = (head.revision or 0) + 1
        await self._db.flush()
        await audit.record(
            self._db, ctx, action=SEMANTIC_DRAFT_DISCARDED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={"revision": head.revision},
        )
        return head

    async def publish(
        self,
        connection: DatabaseConnection,
        *,
        base_revision: int,
        ctx: RequestContext,
        note: str = "",
    ) -> Published:
        """Make the draft what the model reads, as the next version.

        The draft is bound again against the **current** snapshot — it may have
        been written before a re-sync — and diffed against the published
        document. Refused with `E_SEMANTIC_NO_CHANGES` when that list is empty,
        and with `E_SEMANTIC_NOTE_REQUIRED` when it holds a change that alters
        numbers and `note` is blank: those change a figure on somebody's
        dashboard without changing its SQL, and *why* is the one thing the
        history cannot say for itself. The draft's origin — the generations and
        the restore that wrote into it — becomes the version's.
        """
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)
        if head is None or head.draft_document is None:
            raise SemanticNoChangesError("There are no unpublished changes.")

        snapshot = await self._snapshot(connection.id)
        bound = _bind(head.draft_document, snapshot)
        changes = diff_documents(SemanticDocument.model_validate(head.document or {}), bound)
        if not changes:
            raise SemanticNoChangesError("Nothing changed.")
        if any(c.affects_sql for c in changes) and not note.strip():
            raise SemanticNoteRequiredError(
                "These changes alter what numbers mean. Say why in a note — a "
                "dashboard's SQL will not show that anything changed."
            )

        scored = await self._scored_draft_run(connection.id, head.revision)
        published = await _publish(
            self._db, connection_id=connection.id, head=head, document=bound,
            schema_version=snapshot["version"], author=ctx.user_id, note=note,
            origin=dict(head.draft_origin or {}), changes=changes,
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_PUBLISHED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={
                "version": published.version.version,
                "changes": len(published.changes),
                "affects_sql": any(c.affects_sql for c in published.changes),
                "scored_run_id": str(scored) if scored else None,
            },
        )
        return published

    # ── the portable document ────────────────────────────────────────────
    async def export(
        self,
        connection: DatabaseConnection,
        *,
        version: int | None,
        value_meanings: bool,
        ctx: RequestContext,
    ) -> LayerFile:
        """A published version as a file — the published one unless `version` says.

        A **version**, never the draft: a file is something another person will
        read as this layer, and a draft is not this layer yet. Value meanings
        only when asked (D9), and the audit row says which was chosen.
        """
        number = version
        if number is None:
            head = await self.layer_row(connection.id)
            number = head.published_version if head is not None else None
        if number is None:
            raise NotFoundError("This semantic layer has no published version to export.")
        row = await self.version(connection.id, number)
        file = build_file(
            SemanticDocument.model_validate(row.document or {}),
            connection_name=connection.name,
            engine=connection.database_type,
            version=number,
            value_meanings=value_meanings,
        )
        await audit.record(
            self._db, ctx, action=SEMANTIC_EXPORTED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={"version": number, "value_meanings_included": value_meanings},
        )
        return file

    async def import_file(
        self,
        connection: DatabaseConnection,
        raw: Any,
        *,
        base_revision: int,
        ctx: RequestContext,
    ) -> tuple[DraftWritten, ImportReport]:
        """A file into the **draft**, bound to this connection's snapshot.

        Typing by another route (D9): the same revision rule, the same binder,
        the same limits, and the same review before it answers anything. Tables
        the file names that this schema lacks come in flagged, not dropped. The
        draft's origin becomes `{"imported": true}`, which the version it is
        published as carries.
        """
        file, doc = read_file(raw)
        head = await _lock_head(self._db, connection.id)
        await _require_revision(self._db, ctx, connection.id, head, base_revision)
        snapshot = await self._snapshot(connection.id)
        bound = _bind(doc, snapshot)
        written = await _write_draft(
            self._db, connection_id=connection.id, head=head, document=bound,
            author=ctx.user_id, origin={"imported": True},
        )
        report = ImportReport.of(file, bound)
        await audit.record(
            self._db, ctx, action=SEMANTIC_IMPORTED,
            resource_type=audit.SEMANTIC_LAYER, resource_id=connection.id,
            detail={
                "revision": written.head.revision,
                "entities": report.entities,
                "unresolved": report.unresolved,
                "invalid_metrics": report.invalid_metrics,
            },
        )
        return written, report

    async def _scored_draft_run(self, connection_id: UUID, revision: int) -> UUID | None:
        """The benchmark run that scored exactly this draft, if one finished.

        Looked up rather than taken from the client: the audit row says a
        publish was scored only when a run of *this* revision actually was.
        """
        result = await self._db.execute(
            select(BenchmarkRun.id)
            .where(
                BenchmarkRun.connection_id == connection_id,
                BenchmarkRun.semantic_source == DRAFT_SOURCE,
                BenchmarkRun.semantic_revision == revision,
                BenchmarkRun.status == "SUCCEEDED",
            )
            .order_by(BenchmarkRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

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

    async def metric_use(
        self, connection_id: UUID, *, days: int = METRIC_USE_DAYS
    ) -> list[MetricUse]:
        """How often each metric's definition was used by the answers that could.

        Over the last `days` of chat runs on this connection, from the verdicts
        stored at finalisation. **Counts only** — no question, no answer, no SQL:
        a reader of the layer sees how its definitions fare, not who asked what.
        A statement counts toward a metric when the metric was in scope, which
        means the statement touched its table.
        """
        since = utcnow() - timedelta(days=days)
        result = await self._db.execute(
            select(GeneratedQuery.metric_use)
            .join(Run, Run.id == GeneratedQuery.run_id)
            .where(
                Run.connection_id == connection_id,
                Run.created_at >= since,
                GeneratedQuery.metric_use.is_not(None),
            )
        )
        return summarise_metric_use(result.scalars().all())

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
        """Merge the generation into the draft **as it is now**, under the lock.

        Into the draft, never the published document: a generated layer is a
        model's guess about what a schema means, and until Phase 2 it reached
        every answer the moment the job ended, unreviewed. The merge is over the
        current draft, or the published document when there is none.

        The job read nothing when it started. A save made during the minutes it
        spent at the provider is part of the current draft by the time this
        runs, so the merge keeps it — before versions, the job merged into the
        document it had read at the start and silently overwrote that save.
        """
        async with get_sessionmaker()() as session:
            head = await _lock_head(session, connection_id)
            current = SemanticDocument.model_validate(_working(head))
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
            # A model's text is cut to the limits a person's write is held to,
            # so the next save of this layer is never refused for a sentence
            # nobody here wrote.
            bound = _bind(semantic_limits.clip(merged), snapshot)

            written: DraftWritten | None = None
            if diff_documents(current, bound):
                written = await _write_draft(
                    session, connection_id=connection_id, head=head, document=bound,
                    author=actor_id, origin={"generated_job_ids": [str(job_id)]},
                )
                head = written.head
            if head is None:
                # A generation that produced nothing on a connection that never
                # had a layer: no version to write, and no row to record it on.
                await session.commit()
                return
            head.generated_by_llm_config_id = llm_config_id
            head.model_snapshot = model_snapshot
            head.prompt_version = SEMANTIC_PROMPT_VERSION
            head.generated_at = utcnow()
            if written is not None and actor_id is not None:
                await audit.record(
                    session, RequestContext.on_behalf_of(actor_id),
                    action=SEMANTIC_GENERATION_SAVED,
                    resource_type=audit.SEMANTIC_LAYER, resource_id=connection_id,
                    detail={"job_id": str(job_id), "revision": head.revision},
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
class MetricUse:
    """One metric's row in *Metrics in use*."""

    metric: str
    entity: str
    #: Answers whose statement touched the metric's table and was attributed.
    questions: int = 0
    used: int = 0
    ignored: int = 0

    @property
    def gap(self) -> int:
        return self.ignored - self.used


def summarise_metric_use(stored: Iterable[dict[str, Any] | None]) -> list[MetricUse]:
    """Count stored verdicts per metric, the metrics most often ignored first.

    Sorted by the gap — `ignored` minus `used` — because that is the curation
    signal: a definition the answers keep leaving out is the one to look at.
    Ties go to the metric more questions touched, then to the name.
    """
    rows: dict[tuple[str, str], MetricUse] = {}
    for record in stored:
        for verdict in (record or {}).get("verdicts") or []:
            metric, entity = str(verdict.get("metric", "")), str(verdict.get("entity", ""))
            if not metric:
                continue
            row = rows.setdefault((entity, metric), MetricUse(metric=metric, entity=entity))
            row.questions += 1
            if verdict.get("verdict") == USED:
                row.used += 1
            elif verdict.get("verdict") == IGNORED:
                row.ignored += 1
    return sorted(rows.values(), key=lambda r: (-r.gap, -r.questions, r.metric, r.entity))


@dataclass(slots=True)
class DraftWritten:
    """What one draft write produced: the head row, and the draft's changes
    against the published document (empty when the draft was cleared)."""

    head: SemanticLayerRow
    changes: list[Change]


#: `benchmark_runs.semantic_source`: which document a run scored.
PUBLISHED_SOURCE = "PUBLISHED"
DRAFT_SOURCE = "DRAFT"


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
    updated_by = published.published_by if published else None
    updated_at = published.created_at if published else None
    # A draft written after the last publish is the newer write, and its
    # author is the person who changed the layer under this editor.
    if head is not None and head.draft_document is not None and head.draft_updated_at and (
        updated_at is None or head.draft_updated_at >= updated_at
    ):
        updated_by, updated_at = head.draft_updated_by, head.draft_updated_at
        author = await _author_name(db, updated_by)
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
        updated_by=str(updated_by) if updated_by else None,
        updated_by_name=author,
        updated_at=updated_at.isoformat() if updated_at else None,
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
    changes: list[Change] | None = None,
) -> Published:
    """Write `document` as the next version and make it what the model reads.

    **The one writer.** In a single transaction: the version row, its change
    rows, the copy into `semantic_layers.document`, and `revision` and
    `published_version` moved forward. `sha256(head.document)` equals the new
    version's `document_sha256` when this returns (D3), and a test holds every
    write path to it.

    `document` must already be bound; the change list is computed against the
    published document (or passed in, already computed against it), and an
    empty one is refused rather than written. **Publishing clears the draft**:
    whatever was unpublished either is this version or was replaced by it.
    """
    if changes is None:
        previous = SemanticDocument.model_validate(
            head.document if head is not None and head.document else {}
        )
        changes = diff_documents(previous, document)
    if not changes:
        raise SemanticNoChangesError("Nothing changed.")
    if len(note) > MAX_NOTE_CHARS:
        raise ValidationError(f"A note can be at most {MAX_NOTE_CHARS} characters.")

    head = await _ensure_head(db, connection_id, head)

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
    _clear_draft(head)
    await db.flush()
    return Published(head=head, version=version, changes=changes)


async def _write_draft(
    db: AsyncSession,
    *,
    connection_id: UUID,
    head: SemanticLayerRow | None,
    document: SemanticDocument,
    author: UUID | None,
    origin: dict[str, Any] | None,
) -> DraftWritten:
    """Write `document` as the draft. **The one draft writer.**

    `document` must already be bound. Refused with `E_SEMANTIC_NO_CHANGES` when
    it is the working document already (the draft, or the published document
    when there is no draft). Otherwise the revision moves, and:

    * a document equal to the published one **clears** the draft — nothing is
      unpublished, so nothing is shown as unpublished;
    * any other document becomes the draft, attributed to `author`.

    `origin` says how the draft came to be. `generated_job_ids` accumulate
    across generations into one draft; `restored_from` and `imported` replace
    what was there, because a restore or an import replaces the whole document;
    `None` — a person's own edit — keeps the draft's origin, since the edit was
    made on top of it.
    """
    working = SemanticDocument.model_validate(_working(head))
    if not diff_documents(working, document):
        raise SemanticNoChangesError("Nothing changed.")

    head = await _ensure_head(db, connection_id, head)
    published = SemanticDocument.model_validate(head.document or {})
    changes = diff_documents(published, document)
    if changes:
        next_origin = dict(head.draft_origin or {}) if head.draft_document is not None else {}
        if origin is not None and ("restored_from" in origin or origin.get("imported")):
            next_origin = dict(origin)
        elif origin is not None:
            jobs = [
                *next_origin.get("generated_job_ids", []),
                *origin.get("generated_job_ids", []),
            ]
            next_origin = {**next_origin, **origin, "generated_job_ids": jobs}
        head.draft_document = _stored(document)
        head.draft_updated_by = author
        head.draft_updated_at = utcnow()
        head.draft_origin = next_origin
    else:
        _clear_draft(head)
    head.revision = (head.revision or 0) + 1
    await db.flush()
    return DraftWritten(head=head, changes=changes)


def _working(head: SemanticLayerRow | None) -> dict[str, Any]:
    """The document an edit is made on: the draft, else the published one."""
    if head is None:
        return {}
    if head.draft_document is not None:
        return head.draft_document
    return head.document or {}


def _clear_draft(head: SemanticLayerRow) -> None:
    head.draft_document = None
    head.draft_updated_by = None
    head.draft_updated_at = None
    head.draft_origin = {}


async def _ensure_head(
    db: AsyncSession, connection_id: UUID, head: SemanticLayerRow | None
) -> SemanticLayerRow:
    """The head row, created empty (revision 0, nothing published) if absent."""
    if head is not None:
        return head
    head = SemanticLayerRow(
        id=uuid.uuid4(), connection_id=connection_id, revision=0, document={},
        draft_origin={},
    )
    db.add(head)
    await db.flush()
    return head


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


def metric_use_of(
    attempts: Sequence[Any],
    *,
    document: SemanticDocument | None,
    version: int,
    snapshot: dict[str, Any] | None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """The attempt to attribute, and its verdicts — for a run and a benchmark alike.

    The attempt is the **last one the guard accepted**: attribution reads a
    statement the guard already validated, never a refused one. A short-circuited
    (Verified) answer's statement is an attempt like any other, so it is
    attributed the same way. `(None, None)` when nothing was accepted.
    """
    accepted = next(
        (a for a in reversed(attempts) if getattr(a.report, "status", "") == "VALID"), None
    )
    if accepted is None or snapshot is None:
        return accepted, None
    return accepted, attribute_statement(
        accepted.rewritten_sql or accepted.raw_sql,
        document=document, version=version,
        tables=list(accepted.report.referenced_tables or []), snapshot=snapshot,
    )


def attribute_statement(
    sql: str,
    *,
    document: SemanticDocument | None,
    version: int,
    tables: list[str],
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Which metric definitions `sql` matched, as `generated_queries.metric_use`.

    **Fail open, and observing only** (D8 of the plan). `None` — store nothing —
    when no layer reached the prompt, when the statement touched no metric's
    table, and when the statement cannot be read; an exception here is logged
    and never reaches the run. The version is the one the run recorded, so a
    verdict is always read against the definition it was judged by.
    """
    if document is None or not sql:
        return None
    dialect = snapshot.get("dialect") or "postgres"
    try:
        verdicts = attribute(
            sql, dialect, document, tables,
            schema=build_index(snapshot.get("tables") or [], dialect),
        )
    except Exception as err:  # noqa: BLE001 — attribution may never fail a run
        log.warning("metric_attribution_failed", reason=type(err).__name__)
        return None
    if not verdicts:
        return None
    return {"version": version, "verdicts": [v.as_dict() for v in verdicts]}


class DraftMovedError(Exception):
    """The draft a benchmark run was pinned to is not the draft there now."""


async def load_draft(
    db: AsyncSession,
    connection: DatabaseConnection,
    *,
    snapshot: dict[str, Any],
    revision: int | None,
) -> LoadedLayer:
    """The **draft**, bound to `snapshot` — for a benchmark run scoring it.

    The one reader of `draft_document` outside the editor, and it is not a
    loader any question goes through: `load_layer` and `load_document` never
    read a draft, which is the whole of the rule that a draft reaches no run.

    Raises `DraftMovedError` when the head's revision is not `revision` — somebody
    saved, discarded or published after the run was queued, and a draft is not
    versioned, so the one that was asked about cannot be read any more. A score
    of a different draft under the first one's name would be a number about
    nothing anybody chose.

    Deliberately **not** gated on `semantic_layer_enabled`: scoring a draft is
    an explicit request to see what it would do, and answering it with no layer
    would score something else. `version` is the published version the draft
    was edited over (`0` when nothing is published).
    """
    result = await db.execute(
        select(SemanticLayerRow).where(SemanticLayerRow.connection_id == connection.id)
    )
    row = result.scalar_one_or_none()
    if row is None or row.revision != revision or row.draft_document is None:
        raise DraftMovedError(
            "The draft changed after this run was queued, so the draft it was "
            "asked to score is gone. Score the draft again."
        )
    document = _bind(row.draft_document, snapshot)
    return LoadedLayer(
        None if document.is_empty else document, row.published_version or 0
    )


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
