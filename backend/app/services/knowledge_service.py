"""Use cases for knowledge templates: read, author, edit, archive, check.

The transaction boundary lives here, as it does in `semantic_service`. Two
things are worth knowing before changing this file:

* **Every write is guarded against the current snapshot.** A template is
  stored as it was written, not as it was true; the schema moves underneath it.
  Validating on save answers "is this legal at all", and re-validating on read
  is what lets the UI show drift the moment a re-sync creates it — without a
  migration and without a background sweep. The re-validation *reports*; it
  does not persist a status change. Phase 4 is what writes `STALE`.
* **`app.knowledge` owns the reasoning; this module owns every DB call.** The
  package below cannot import sqlalchemy, and that is the contract that keeps
  the guard's fifth entry point from growing a query of its own.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.value_objects import DatabaseKind
from app.infra.db.models import (
    DatabaseConnection,
    KnowledgeTemplateHit,
    KnowledgeTemplateRow,
    SchemaSnapshotRow,
)
from app.knowledge import (
    KnowledgeTemplate,
    LiteralProvenance,
    ParamProposal,
    TemplateParam,
    TemplateRole,
    TemplateSource,
    TemplateStatus,
    TemplateVerdict,
    normalize_question,
    parameterize,
    policy_from_tables,
    propose_params,
    slots,
    validate_template,
)
from app.knowledge.matcher import (
    SHORTLIST_FLOOR,
    LexicalMatcher,
    TemplateMatcher,
    trigrams,
)

log = get_logger(__name__)

#: What a template's `question` may say. Long enough for a real question in any
#: script, short enough that the list row and the match key stay meaningful.
MAX_QUESTION_CHARS = 400
MAX_NOTE_CHARS = 4_000

_NO_SNAPSHOT = (
    "Sync this connection's schema first — a template is checked against it."
)


class KnowledgeService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    # ── reading ──────────────────────────────────────────────────────────
    async def list_templates(
        self, connection: DatabaseConnection, *, include_archived: bool = False
    ) -> list[KnowledgeTemplateRow]:
        """Every template for a connection, newest first.

        Archived rows are excluded by default and never deleted — the archive
        is the least urgent thing on the screen, but it is still the record of
        what somebody once knew.
        """
        statement = select(KnowledgeTemplateRow).where(
            KnowledgeTemplateRow.connection_id == connection.id
        )
        if not include_archived:
            statement = statement.where(
                KnowledgeTemplateRow.status != TemplateStatus.ARCHIVED
            )
        result = await self._db.execute(
            statement.order_by(KnowledgeTemplateRow.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(
        self, connection: DatabaseConnection, template_id: UUID
    ) -> KnowledgeTemplateRow:
        row = await self._db.get(KnowledgeTemplateRow, template_id)
        if row is None or row.connection_id != connection.id:
            raise NotFoundError("Template not found.")
        return row

    # ── the editor's live check ──────────────────────────────────────────
    async def check(
        self,
        connection: DatabaseConnection,
        *,
        sql: str,
        question: str = "",
        params: list[TemplateParam] | None = None,
        accept: set[str] | None = None,
    ) -> tuple[TemplateVerdict, list[ParamProposal], str, list[TemplateParam]]:
        """Validate SQL and propose parameters in one round trip.

        One call because both answers come from the same parse, and because a
        local "looks fine" that the server then rejects is the worst possible
        interaction — the same reason `semantic/check` exists.

        When `accept` is given, the ticked literals are also replaced with
        `:slots` **on the tree**, and the parameterized SQL comes back with the
        parameters it declares. The substitution happens here rather than in
        the browser so the statement that gets saved is the one the guard just
        read.
        """
        snapshot = await self._snapshot(connection.id)
        policy = policy_from_tables(
            snapshot["tables"],
            dialect=DatabaseKind(connection.database_type).sqlglot_dialect,
            max_rows=connection.max_rows,
        )
        proposals = propose_params(
            sql, dialect=policy.dialect, tables=snapshot["tables"]
        )

        rewritten, declared = sql, list(params or [])
        if accept:
            rewritten, declared = parameterize(
                sql, accept, dialect=policy.dialect, tables=snapshot["tables"]
            )

        template = KnowledgeTemplate(
            question=question, sql=rewritten, params=declared
        )
        return validate_template(template, policy), proposals, rewritten, declared

    # ── writing ──────────────────────────────────────────────────────────
    async def create(
        self,
        connection: DatabaseConnection,
        *,
        actor_id: UUID,
        question: str,
        sql: str,
        params: list[TemplateParam],
        note: str = "",
        source: TemplateSource = TemplateSource.MANUAL,
        literal_provenance: LiteralProvenance = LiteralProvenance.HUMAN_AUTHORED,
        role: TemplateRole = TemplateRole.RETRIEVABLE,
    ) -> KnowledgeTemplateRow:
        question, note = self._clean(question, note)
        snapshot = await self._snapshot(connection.id)
        if not snapshot["tables"]:
            raise ValidationError(_NO_SNAPSHOT)

        verdict = self._guard(connection, snapshot, question, sql, params)
        normalized = normalize_question(question)

        row = KnowledgeTemplateRow(
            id=uuid.uuid4(),
            connection_id=connection.id,
            question=question,
            question_normalized=normalized,
            sql=sql,
            params=[p.model_dump(mode="json") for p in params],
            note=note,
            source=str(source),
            literal_provenance=str(literal_provenance),
            role=str(role),
            status=str(TemplateStatus.ACTIVE),
            status_reason="",
            schema_version=snapshot["version"],
            referenced_tables=verdict.referenced_tables,
            conflicts_with=[],
            created_by=actor_id,
            # Authoring *is* verification: a person typed this and pressed
            # save. A proposal mined from a tile arrives unverified in Phase 3
            # and is a different path.
            verified_by=actor_id,
            verified_at=utcnow(),
            last_validated_at=utcnow(),
            # Set here rather than left to the column default: the row is
            # returned to the caller before the transaction commits, and a
            # `None` where the read model promises an integer is a 500 the
            # curator would see as "saving failed" after it had saved.
            hit_count=0,
        )
        self._db.add(row)
        await self._flush_unique(question)
        return row

    async def update(
        self,
        connection: DatabaseConnection,
        template_id: UUID,
        *,
        actor_id: UUID,
        question: str | None = None,
        sql: str | None = None,
        params: list[TemplateParam] | None = None,
        note: str | None = None,
        role: TemplateRole | None = None,
        status: TemplateStatus | None = None,
    ) -> KnowledgeTemplateRow:
        """A partial update, re-guarded whenever the statement can have moved.

        `status` accepts only a curator's own two verdicts — reactivating a
        template they have fixed, or archiving it. `STALE` and `CONFLICTED` are
        written by the system in Phase 4, never by a form.
        """
        row = await self.get(connection, template_id)
        snapshot = await self._snapshot(connection.id)

        next_question = row.question if question is None else question
        next_note = row.note if note is None else note
        next_question, next_note = self._clean(next_question, next_note)
        next_sql = row.sql if sql is None else sql
        next_params = (
            [TemplateParam.model_validate(p) for p in row.params]
            if params is None else params
        )

        verdict = self._guard(
            connection, snapshot, next_question, next_sql, next_params
        )

        row.question = next_question
        row.question_normalized = normalize_question(next_question)
        row.sql = next_sql
        row.params = [p.model_dump(mode="json") for p in next_params]
        row.note = next_note
        row.referenced_tables = verdict.referenced_tables
        row.schema_version = snapshot["version"]
        row.last_validated_at = utcnow()
        row.verified_by = actor_id
        row.verified_at = utcnow()
        if role is not None:
            row.role = str(role)
        if status is not None:
            if status in (TemplateStatus.STALE, TemplateStatus.CONFLICTED):
                raise ValidationError(
                    "Stale and conflicted are set by the system, not by hand."
                )
            row.status = str(status)
            # An edit that fixes a stale template clears the explanation with
            # it. Leaving the old reason on a healthy row is how a UI comes to
            # show a warning nobody can act on.
            row.status_reason = ""

        await self._flush_unique(next_question)
        return row

    async def archive(
        self, connection: DatabaseConnection, template_id: UUID
    ) -> KnowledgeTemplateRow:
        """Archive, never delete.

        The system does not destroy a person's work — the same rule the
        semantic layer follows for a human-written entry. `DELETE` on the
        endpoint means "take this out of use", and it says so in the UI.
        """
        row = await self.get(connection, template_id)
        row.status = str(TemplateStatus.ARCHIVED)
        row.status_reason = "Archived by a curator."
        await self._db.flush()
        return row

    # ── re-validation (reports; does not persist a verdict) ──────────────
    async def revalidate(
        self, connection: DatabaseConnection, row: KnowledgeTemplateRow
    ) -> TemplateVerdict:
        """Is this template still legal against the schema as it is **now**?

        Phase 1 uses this to *show* drift on read. It deliberately does not
        write `STALE`: withdrawing a template from use is a behaviour change,
        and this phase changes no behaviour. Phase 4 is where the worker
        persists the verdict.
        """
        snapshot = await self._snapshot(connection.id)
        return self._verdict(connection, snapshot, self.to_model(row))

    # ── conversions ──────────────────────────────────────────────────────
    @staticmethod
    def to_model(row: KnowledgeTemplateRow) -> KnowledgeTemplate:
        return KnowledgeTemplate(
            id=row.id,
            connection_id=row.connection_id,
            question=row.question,
            question_normalized=row.question_normalized,
            sql=row.sql,
            params=[TemplateParam.model_validate(p) for p in row.params],
            note=row.note,
            source=TemplateSource(row.source),
            literal_provenance=LiteralProvenance(row.literal_provenance),
            role=TemplateRole(row.role),
            status=TemplateStatus(row.status),
            status_reason=row.status_reason,
            schema_version=row.schema_version,
            referenced_tables=list(row.referenced_tables or []),
            conflicts_with=list(row.conflicts_with or []),
            created_by=row.created_by,
            verified_by=row.verified_by,
            verified_at=row.verified_at,
            last_validated_at=row.last_validated_at,
            hit_count=row.hit_count or 0,
            last_hit_at=row.last_hit_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # ── internals ────────────────────────────────────────────────────────
    def _guard(
        self,
        connection: DatabaseConnection,
        snapshot: dict[str, Any],
        question: str,
        sql: str,
        params: list[TemplateParam],
    ) -> TemplateVerdict:
        """The save-time gate. Rejects with the guard's own words."""
        if not question.strip():
            raise ValidationError("Write the question this template answers.")
        template = KnowledgeTemplate(question=question, sql=sql, params=params)
        verdict = self._verdict(connection, snapshot, template)
        if not verdict.valid:
            raise ValidationError(verdict.message or "This SQL was rejected.")

        # A slot the question never names can never bind, so the template
        # would be stored and never match. Cheaper to say so now than to let
        # the curator discover it as silence.
        named = set(slots(question))
        missing = sorted({p.name for p in params} - named)
        if missing:
            raise ValidationError(
                "The question does not mention "
                + ", ".join(f"{{{name}}}" for name in missing)
                + " — a parameter the question never names can never be filled in."
            )
        return verdict

    def _verdict(
        self,
        connection: DatabaseConnection,
        snapshot: dict[str, Any],
        template: KnowledgeTemplate,
    ) -> TemplateVerdict:
        policy = policy_from_tables(
            snapshot["tables"],
            dialect=DatabaseKind(connection.database_type).sqlglot_dialect,
            max_rows=connection.max_rows,
        )
        return validate_template(template, policy)

    @staticmethod
    def _clean(question: str, note: str) -> tuple[str, str]:
        question = " ".join((question or "").split())[:MAX_QUESTION_CHARS]
        return question, (note or "").strip()[:MAX_NOTE_CHARS]

    async def _flush_unique(self, question: str) -> None:
        """Turn the unique constraint into the sentence a curator can act on."""
        try:
            await self._db.flush()
        except IntegrityError as err:
            raise ConflictError(
                f"This connection already has a template for “{question}”."
            ) from err

    async def _snapshot(self, connection_id: UUID) -> dict[str, Any]:
        result = await self._db.execute(
            select(SchemaSnapshotRow)
            .where(SchemaSnapshotRow.connection_id == connection_id)
            .order_by(SchemaSnapshotRow.version.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {"tables": [], "dialect": "postgres", "version": 0}
        return {
            "tables": row.tables,
            "dialect": row.dialect,
            "version": row.version,
        }


# ── the read path (Phase 2) ──────────────────────────────────────────────
#: How many rows the shortlist may return before scoring. A connection with a
#: healthy store has tens of templates; this is the ceiling that keeps a
#: pathological one from turning every question into a table scan's worth of
#: Python.
SHORTLIST_LIMIT = 200

_TRGM_AVAILABLE: bool | None = None


async def has_trigram(db: AsyncSession) -> bool:
    """Whether `pg_trgm` is installed, asked once per process.

    Cached because the answer cannot change without a migration, and because
    this sits on the ask path: a catalog lookup per question would be a cost
    paid forever to learn something that was decided at deploy time.
    """
    global _TRGM_AVAILABLE
    if _TRGM_AVAILABLE is None:
        try:
            found = await db.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            )
            _TRGM_AVAILABLE = found.scalar_one_or_none() is not None
        except Exception:
            _TRGM_AVAILABLE = False
        if not _TRGM_AVAILABLE:
            # Said once, loudly enough to find in a log and quietly enough not
            # to be an error: the loop still works, it just scores in Python.
            log.warning("pg_trgm_absent_matching_in_python")
    return _TRGM_AVAILABLE


def build_matcher(db: AsyncSession) -> TemplateMatcher:
    """The connection's matcher: lexical today, embedding in Phase 7 (D3).

    The row source is what keeps `app.knowledge` free of sqlalchemy. It uses
    the trigram index to **narrow**; the score that decides is always computed
    in the matcher, so a deployment without `pg_trgm` gets the same verdicts at
    a higher cost rather than a different feature.
    """

    async def rows(
        connection_id: UUID, normalized: str, limit: int
    ) -> list[KnowledgeTemplate]:
        statement = select(KnowledgeTemplateRow).where(
            KnowledgeTemplateRow.connection_id == connection_id,
            # §1.3, in the query that builds the candidate set rather than in a
            # comment: a held-out question answered from its own stored SQL
            # measures nothing, and a stale one answers with SQL the schema no
            # longer supports.
            KnowledgeTemplateRow.status == str(TemplateStatus.ACTIVE),
            KnowledgeTemplateRow.role == str(TemplateRole.RETRIEVABLE),
        )
        if await has_trigram(db):
            similarity = func.similarity(
                KnowledgeTemplateRow.question_normalized, normalized
            )
            statement = statement.where(similarity >= SHORTLIST_FLOOR).order_by(
                similarity.desc()
            )
        statement = statement.limit(SHORTLIST_LIMIT)

        result = await db.execute(statement)
        found = [KnowledgeService.to_model(row) for row in result.scalars().all()]
        if not await has_trigram(db):
            # No index to narrow with, so narrow here: a template that shares
            # no trigram with the question cannot score above zero, and
            # dropping it before the sort keeps a large store cheap.
            asked = trigrams(normalized)
            found = [t for t in found if asked & trigrams(t.question_normalized)]
        return found[:SHORTLIST_LIMIT]

    return LexicalMatcher(rows)


async def record_hit(
    db: AsyncSession,
    *,
    run_id: UUID,
    template_id: UUID | None,
    outcome: str,
    matcher: str = "LEXICAL",
    score: float = 0.0,
    bound_params: dict[str, Any] | None = None,
) -> KnowledgeTemplateHit:
    """One row per verdict, and the counters that go with a real hit.

    `hit_count` and `last_hit_at` move only on `SHORT_CIRCUIT`: they are what
    §4.7 prunes on, and a template that matched but could not bind has not
    earned its keep — it has told us the binder needs work.
    """
    row = KnowledgeTemplateHit(
        id=uuid.uuid4(),
        run_id=run_id,
        template_id=template_id,
        matcher=matcher,
        score=score,
        outcome=outcome,
        bound_params=bound_params or {},
    )
    db.add(row)
    if outcome == "SHORT_CIRCUIT" and template_id is not None:
        template = await db.get(KnowledgeTemplateRow, template_id)
        if template is not None:
            template.hit_count = (template.hit_count or 0) + 1
            template.last_hit_at = utcnow()
    return row
