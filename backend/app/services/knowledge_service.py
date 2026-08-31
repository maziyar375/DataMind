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
from datetime import timedelta
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
    AnswerFeedback,
    DashboardTile,
    DatabaseConnection,
    GeneratedQuery,
    KnowledgeTemplateHit,
    KnowledgeTemplateRow,
    Message,
    Report,
    ReportBlock,
    ReportSection,
    Run,
    SchemaSnapshotRow,
    SemanticLayerRow,
)
from app.knowledge import (
    KnowledgeTemplate,
    LiteralProvenance,
    ParamProposal,
    Suggestion,
    SuggestionKind,
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
from app.knowledge.backlog import (
    backfill_reason,
    build_vocabulary,
    failed_reason,
    flagged_reason,
    rank_suggestions,
    traffic_reason,
    unknown_reason,
    unknown_words,
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


# ── capture (Phase 3) ────────────────────────────────────────────────────
#: How far back the backlog looks. A month, because "asked 9× this month" is a
#: sentence a curator can act on and "asked 9× ever" is not.
BACKLOG_WINDOW_DAYS = 30

#: How many normalised questions the aggregation considers. The backlog is
#: meant to be finite; a list that scrolls is one nobody finishes.
BACKLOG_CANDIDATES = 200

OPEN = "OPEN"
RESOLVED = "RESOLVED"
DISMISSED = "DISMISSED"

CORRECT = "CORRECT"
WRONG = "WRONG"
NEEDS_REVIEW = "NEEDS_REVIEW"

#: A tile or block whose SQL a person wrote or corrected. These are verified
#: question→SQL pairs that **exist right now and are read by nothing** — the
#: cheapest knowledge in the product.
CORRECTED_ORIGINS = ("GENERATED_EDITED", "HANDWRITTEN")


class FeedbackService:
    """Feedback on an answer, the review queue, and the backlog.

    Separate from `KnowledgeService` because it is a different job with a
    different audience: that one is a curator's editor, this one is what turns
    "somebody was unhappy" into "here is the next thing to teach". They share
    the store and nothing else.
    """

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    # ── feedback on an answer ────────────────────────────────────────────
    async def record(
        self,
        run: Any,
        *,
        user_id: UUID,
        verdict: str,
        comment: str = "",
    ) -> AnswerFeedback:
        """One verdict per person per answer; a second press is a change of mind.

        A `CORRECT` verdict arrives already `RESOLVED`, by the person who gave
        it. Confirming an answer *is* a resolution, and treating it as open
        work would put a permanent number on the tab that no curator could ever
        clear — which is how a badge stops being a signal.
        """
        if verdict not in (CORRECT, WRONG, NEEDS_REVIEW):
            raise ValidationError("That is not a verdict this system records.")

        existing = await self._db.execute(
            select(AnswerFeedback).where(
                AnswerFeedback.run_id == run.id,
                AnswerFeedback.user_id == user_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            row = AnswerFeedback(
                id=uuid.uuid4(),
                run_id=run.id,
                connection_id=run.connection_id,
                user_id=user_id,
                # Spelled out rather than left to the column defaults: the row
                # is serialised back to the caller before the transaction
                # commits, and a `None` where the read model promises a string
                # is a 500 the reader would see as "your feedback failed"
                # after it had been recorded.
                comment="",
                state=OPEN,
                resolution_note="",
            )
            self._db.add(row)

        row.verdict = verdict
        row.comment = (comment or "").strip()[:MAX_NOTE_CHARS]
        if verdict == CORRECT:
            row.state = RESOLVED
            row.resolved_by = user_id
            row.resolved_at = utcnow()
            row.resolution_note = ""
        else:
            # A change of mind reopens it: the curator's earlier "resolved"
            # answered a different flag.
            row.state = OPEN
            row.resolved_by = None
            row.resolved_at = None
            row.became_template = None
        await self._db.flush()
        return row

    async def for_run(self, run_id: UUID, user_id: UUID) -> AnswerFeedback | None:
        """This reader's own verdict on this answer, if they gave one.

        Their own, not anyone else's: the footer shows what *you* said, and
        showing a colleague's verdict there would be an opinion presented as a
        fact about the answer.
        """
        result = await self._db.execute(
            select(AnswerFeedback).where(
                AnswerFeedback.run_id == run_id, AnswerFeedback.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    # ── the review queue ─────────────────────────────────────────────────
    async def reviews(
        self, connection: DatabaseConnection, *, state: str = OPEN
    ) -> list[tuple[AnswerFeedback, Run, str, str]]:
        """Every flag on this connection, newest first, with its evidence.

        Each row carries the question that was asked and the SQL that answered
        it, because the curator's actual job here is *comparing two
        statements* — and a queue that made them click through to find the
        first one would not get used.
        """
        result = await self._db.execute(
            select(AnswerFeedback)
            .where(
                AnswerFeedback.connection_id == connection.id,
                AnswerFeedback.state == state,
                AnswerFeedback.verdict != CORRECT,
            )
            .order_by(AnswerFeedback.created_at.desc())
            .limit(BACKLOG_CANDIDATES)
        )
        rows = list(result.scalars().all())

        out: list[tuple[AnswerFeedback, Run, str, str]] = []
        for row in rows:
            run = await self._db.get(Run, row.run_id)
            if run is None:
                continue
            question, sql = await self._asked(run)
            out.append((row, run, question, sql))
        return out

    async def resolve(
        self,
        connection: DatabaseConnection,
        feedback_id: UUID,
        *,
        actor_id: UUID,
        template_id: UUID | None = None,
        note: str = "",
        dismiss: bool = False,
    ) -> AnswerFeedback:
        """Close a flag, and record what happened to it.

        `became_template` is the loop closing. It is what lets the product tell
        the person who flagged an answer that their flag became knowledge —
        and a feedback control with no visible payoff is worse than none,
        because people learn their thumbs-down goes nowhere and stop.

        A dismissal takes a reason for the same reason: a dismissal with no
        note is indistinguishable from being ignored.
        """
        row = await self._db.get(AnswerFeedback, feedback_id)
        if row is None or row.connection_id != connection.id:
            raise NotFoundError("That flag is not on this connection.")
        if dismiss and not note.strip():
            raise ValidationError(
                "Say why you are dismissing this — the person who flagged it will "
                "see the reason."
            )

        row.state = DISMISSED if dismiss else RESOLVED
        row.resolved_by = actor_id
        row.resolved_at = utcnow()
        row.resolution_note = note.strip()[:MAX_NOTE_CHARS]
        row.became_template = template_id
        await self._db.flush()
        return row

    # ── the backlog ──────────────────────────────────────────────────────
    async def suggestions(
        self, connection: DatabaseConnection, *, limit: int = 30
    ) -> list[Suggestion]:
        """A finite, ranked list of what to teach next.

        Five sources, and the ranking is `app.knowledge.backlog`'s — the
        reasoning lives there, and this method is only the aggregation the
        reasoning cannot do without a database.

        Everything already taught is excluded by normalised question, so the
        backlog shrinks as it is worked. A backlog that does not shrink is a
        report, not a queue.
        """
        taught = await self._taught(connection.id)
        items: list[Suggestion] = []
        items += await self._flagged(connection, taught)
        items += await self._backfill(connection, taught)
        traffic, unknown = await self._from_traffic(connection, taught)
        items += traffic
        items += unknown
        return rank_suggestions(items, limit=limit)

    async def _taught(self, connection_id: UUID) -> set[str]:
        result = await self._db.execute(
            select(KnowledgeTemplateRow.question_normalized).where(
                KnowledgeTemplateRow.connection_id == connection_id,
                KnowledgeTemplateRow.status != str(TemplateStatus.ARCHIVED),
            )
        )
        return {value for (value,) in result.all()}

    async def _flagged(
        self, connection: DatabaseConnection, taught: set[str]
    ) -> list[Suggestion]:
        out: list[Suggestion] = []
        for row, run, question, sql in await self.reviews(connection):
            if not question or normalize_question(question) in taught:
                continue
            out.append(Suggestion(
                kind=SuggestionKind.FLAGGED,
                question=question,
                count=1,
                reason=flagged_reason(row.comment),
                sql=sql,
                # A flagged answer's SQL was written by a model. If a curator
                # edits it in the editor before saving, the endpoint records
                # `CHAT_CORRECTED` and the literals become theirs.
                source=str(TemplateSource.CHAT_CONFIRMED),
                model_derived=True,
                origin_id=str(run.id),
            ))
        return out

    async def _backfill(
        self, connection: DatabaseConnection, taught: set[str]
    ) -> list[Suggestion]:
        """Verified pairs that already exist in the database and nothing reads.

        `dashboard_tiles` and `report_blocks` both carry a plain-language
        question, a statement, and `sql_origin`. A `GENERATED_EDITED` or
        `HANDWRITTEN` one is a question a person answered correctly, by hand,
        and then never told the system about.

        They arrive as **proposals**, never as approved templates, and a
        `GENERATED_EDITED` one is `MODEL_DERIVED`: a human edited a statement
        whose *literals* the model chose (`docs/security.md`).
        """
        out: list[Suggestion] = []

        tiles = await self._db.execute(
            select(DashboardTile).where(
                DashboardTile.connection_id == connection.id,
                DashboardTile.sql_origin.in_(CORRECTED_ORIGINS),
                DashboardTile.question.isnot(None),
                DashboardTile.sql != "",
            ).limit(BACKLOG_CANDIDATES)
        )
        for tile in tiles.scalars():
            question = (tile.question or "").strip()
            if not question or normalize_question(question) in taught:
                continue
            out.append(Suggestion(
                kind=SuggestionKind.BACKFILL,
                question=question,
                reason=backfill_reason("TILE"),
                sql=tile.sql,
                source=str(TemplateSource.TILE),
                model_derived=tile.sql_origin == "GENERATED_EDITED",
                origin_id=str(tile.id),
            ))

        blocks = await self._db.execute(
            select(ReportBlock)
            .join(ReportSection, ReportBlock.section_id == ReportSection.id)
            .join(Report, ReportSection.report_id == Report.id)
            .where(
                Report.connection_id == connection.id,
                ReportBlock.sql_origin.in_(CORRECTED_ORIGINS),
                ReportBlock.question != "",
                ReportBlock.sql != "",
            )
            .limit(BACKLOG_CANDIDATES)
        )
        for block in blocks.scalars():
            question = (block.question or "").strip()
            if not question or normalize_question(question) in taught:
                continue
            out.append(Suggestion(
                kind=SuggestionKind.BACKFILL,
                question=question,
                reason=backfill_reason("REPORT_BLOCK"),
                sql=block.sql,
                source=str(TemplateSource.REPORT_BLOCK),
                model_derived=block.sql_origin == "GENERATED_EDITED",
                origin_id=str(block.id),
            ))
        return out

    async def _from_traffic(
        self, connection: DatabaseConnection, taught: set[str]
    ) -> tuple[list[Suggestion], list[Suggestion]]:
        """Ranks 1, 3 and 4, from one pass over the month's questions.

        One pass because they are three readings of the same rows, and three
        queries would be three chances for them to disagree about which
        questions were asked.
        """
        since = utcnow() - timedelta(days=BACKLOG_WINDOW_DAYS)
        result = await self._db.execute(
            select(Run, Message.content)
            .join(Message, Run.user_message_id == Message.id)
            .where(
                Run.connection_id == connection.id,
                Run.created_at >= since,
                Message.content.isnot(None),
            )
            .order_by(Run.created_at.desc())
            .limit(BACKLOG_CANDIDATES * 5)
        )

        matched = await self._matched_run_ids(connection.id, since)
        grouped: dict[str, dict[str, Any]] = {}
        for run, content in result.all():
            question = (content or "").strip()
            if not question:
                continue
            key = normalize_question(question)
            if not key or key in taught:
                continue
            bucket = grouped.setdefault(
                key,
                {"question": question, "asked": 0, "matched": 0,
                 "failed": 0, "repaired": 0},
            )
            bucket["asked"] += 1
            bucket["matched"] += int(run.id in matched)
            bucket["failed"] += int(bool(run.error_code))
            bucket["repaired"] += int((run.repair_count or 0) > 0)

        vocabulary = await self._vocabulary(connection)
        traffic: list[Suggestion] = []
        unknown: list[Suggestion] = []
        for bucket in grouped.values():
            question, asked = bucket["question"], bucket["asked"]
            if bucket["failed"] or bucket["repaired"]:
                traffic.append(Suggestion(
                    kind=SuggestionKind.FAILED,
                    question=question,
                    count=asked,
                    reason=failed_reason(bucket["failed"], bucket["repaired"]),
                ))
            elif not bucket["matched"]:
                traffic.append(Suggestion(
                    kind=SuggestionKind.TRAFFIC,
                    question=question,
                    count=asked,
                    reason=traffic_reason(asked),
                ))

            words = unknown_words(question, vocabulary)
            if words:
                unknown.append(Suggestion(
                    kind=SuggestionKind.UNKNOWN_WORDS,
                    question=question,
                    count=asked,
                    reason=unknown_reason(words),
                    words=words,
                ))
        return traffic, unknown

    async def _matched_run_ids(self, connection_id: UUID, since: Any) -> set[UUID]:
        result = await self._db.execute(
            select(KnowledgeTemplateHit.run_id).where(
                KnowledgeTemplateHit.outcome == "SHORT_CIRCUIT",
                KnowledgeTemplateHit.created_at >= since,
            )
        )
        return {run_id for (run_id,) in result.all()}

    async def _vocabulary(self, connection: DatabaseConnection) -> set[str]:
        """What this connection can be said to know — schema plus the layer."""
        snapshot = await KnowledgeService(self._db, self._settings)._snapshot(
            connection.id
        )
        layer = await self._db.execute(
            select(SemanticLayerRow).where(
                SemanticLayerRow.connection_id == connection.id
            )
        )
        row = layer.scalar_one_or_none()
        return build_vocabulary(
            snapshot["tables"], row.document if row and row.document else None
        )

    async def _asked(self, run: Any) -> tuple[str, str]:
        """The question behind a run, and the statement that answered it."""
        message = await self._db.get(Message, run.user_message_id)
        query = await self._db.execute(
            select(GeneratedQuery)
            .where(GeneratedQuery.run_id == run.id)
            .order_by(GeneratedQuery.attempt_no.desc())
            .limit(1)
        )
        latest = query.scalar_one_or_none()
        return (
            (message.content or "").strip() if message else "",
            (latest.raw_sql if latest else "") or "",
        )
