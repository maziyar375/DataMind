"""Reports: CRUD over the template and its outline.

Ordinary CRUD with the same rule threaded through every method dashboards use —
**everything is scoped to the owner, and a resource belonging to someone else is
404, not 403**, so another user's report is indistinguishable from one that does
not exist. Sections and blocks are reached *through* their report, never by id
alone, so ownership is checked once at the top of every path.

Three rules here are not CRUD, and each is a decision the rest of the feature
rests on:

* **The disclosure gate.** A report's analysis is written from result values, so
  a connection that shares none cannot carry one. Refused at creation, and
  re-checked at the start of every generation (Phase 5) — `assert_wide_enough`
  is a module-level function precisely so the worker can call the same one.
* **The connection is pinned.** Byte-for-byte the conversation rule in
  `run_service._bind_connection`, for the same reason: a report keyed to one
  connection cannot cross disclosure policies. Re-sending the connection it
  already has is a no-op; sending a different one is 422.
* **Editing a question un-checks its SQL.** `report_blocks.sql` answers the
  question that was checked, not the one now stored beside it. Keeping the old
  verdict would mean a run silently answering the previous question under a
  heading describing the new one — so the question and the time window reset
  feasibility to `UNCHECKED`. Whether the *statement* survives depends on who
  wrote it: a generated draft is dropped, because reproducing it costs one
  click, while a hand-written or hand-edited one is kept, because it does not.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    DisclosureTooNarrowError,
    LLMError,
    NotFoundError,
    QuestionOutOfScopeError,
    ValidationError,
)
from app.core.logging import get_logger
from app.domain.value_objects import (
    DisclosurePolicy,
    ReportFeasibility,
    ReportRunStatus,
    ReportSectionKind,
    SqlOrigin,
)
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    Report,
    ReportBlock,
    ReportBlockResult,
    ReportRun,
    ReportSection,
    ReportSectionResult,
)
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.pipeline.state import RetrievedContext
from app.reports.outline import (
    OUTLINE_MIN_MAX_TOKENS,
    OutlineProposal,
    executive_summary,
    propose,
)
from app.reports.prompts import REPORT_PROMPT_VERSION, report_time_rules
from app.semantic.render import _render_time
from app.services.query_service import effective_max_rows, latest_snapshot, resolve_llm
from app.services.semantic_service import load_document
from app.services.sql_draft_service import SqlDraft, draft_sql, validate_sql

log = get_logger(__name__)

# The policies a report can be written from. `NONE` and `AGGREGATE` hand the
# model no values at all — only "1,240 rows across columns: month, revenue" —
# while the charts on the same page render the real numbers in the browser,
# because that is the owner's own data reaching the owner's own screen.
WIDE_ENOUGH = frozenset({DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL})

# Fields whose new value invalidates the SQL that was generated for the old one.
_SQL_INVALIDATING = ("question", "time_window")

# A run in one of these has not reached a terminal state, so a second one would
# race it. Same refusal `semantic_service.create_job` makes, for the same reason.
ACTIVE_RUN_STATUSES = (ReportRunStatus.QUEUED, ReportRunStatus.RUNNING)


def sql_fingerprint(sql: str) -> str:
    """A hash of the statement, and only the statement.

    On the block it says which SQL was last checked; copied onto every result
    it says which SQL produced those numbers. Two runs whose block hashes match
    can be compared honestly, and two whose hashes differ cannot — which is the
    whole reason the column exists.
    """
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()


def _verdict(draft: SqlDraft) -> tuple[str, str | None]:
    """A draft's outcome as a feasibility status and the reason for it.

    The guard's own words, verbatim: a rewritten explanation is a second
    vocabulary for the user to learn, and it drifts from the rule that produced
    it. Same posture as `semantic.tsx` rendering metric-expression errors.
    """
    if draft.validation_status != "VALID":
        # `ValidationReport.errors` is a *property* — a filtered view of
        # `issues` — and `model_dump()` emits declared fields only. Reading
        # "errors" off the serialised report therefore always found nothing,
        # and every rejection wore the fallback sentence below instead of the
        # guard's own. The filter has to be applied here, on the key that is
        # actually in the payload.
        errors = [
            issue
            for issue in (draft.validation_report.get("issues") or [])
            if issue.get("severity", "ERROR") == "ERROR"
        ]
        first = errors[0] if errors else {}
        reason = " ".join(
            part for part in (first.get("message"), first.get("hint")) if part
        )
        return ReportFeasibility.INFEASIBLE, (
            reason
            # Reached only when the guard refused without saying why, which it
            # is not supposed to do. Worded for both roads to this verdict: the
            # question the model was given, and the statement someone typed.
            or "This could not be turned into a query this connection allows."
        )

    preview = draft.preview
    if preview is None or preview.status != "OK":
        # Valid SQL the database refused: a timeout, a permission, a view that
        # no longer resolves. Not the guard's doing, and not something the user
        # can fix by rewording — but it is still "this cannot be produced".
        return ReportFeasibility.INFEASIBLE, (
            (preview.error_message if preview else None)
            or "The query is valid but the database did not return a result."
        )

    if preview.row_count == 0:
        # Not a failure. The query works and the answer is "nothing happened",
        # which a report may legitimately want to say.
        return ReportFeasibility.EMPTY, (
            "The query is valid, but no rows fall in this window. The section "
            "will say so rather than showing an empty chart."
        )
    return ReportFeasibility.FEASIBLE, None


def _hand_origin(block: ReportBlock) -> str:
    """Where the statement now on a hand-edited block came from.

    Provenance only, and **never a trust signal** — the same rule
    `dashboard_tiles.sql_origin` carries. A block that has never held a
    generated statement is the user's own work from the start; one that has is
    that work edited, and it stays `GENERATED_EDITED` however little of the
    original survives, because "I started from what the model wrote" is the
    fact this column records.
    """
    if block.sql_origin == SqlOrigin.HANDWRITTEN or not block.sql:
        return SqlOrigin.HANDWRITTEN
    return SqlOrigin.GENERATED_EDITED


def _record_check(block: ReportBlock, status: str, reason: str | None) -> None:
    block.feasibility_status = status
    block.feasibility_reason = reason
    block.feasibility_checked_at = utcnow()


def is_wide_enough(policy: str | None) -> bool:
    """Whether a connection's disclosure policy can carry a report at all."""
    return policy in WIDE_ENOUGH


def assert_wide_enough(connection: DatabaseConnection) -> None:
    """The gate, in the two places §7 requires it: creation and generation.

    The message names the policy in force and both ways out of it, because the
    user reading it can act on either — and neither is discoverable from
    "disclosure policy too narrow" alone.
    """
    if is_wide_enough(connection.disclosure_policy):
        return
    raise DisclosureTooNarrowError(
        f"This connection's disclosure policy is {connection.disclosure_policy}. "
        "A report's analysis is written from result values, so it needs SAMPLE "
        "or FULL. Change the policy on the data source, or generate this report "
        "against a different connection.",
        connection_id=str(connection.id),
        disclosure_policy=str(connection.disclosure_policy),
    )


class ReportService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._box: AesGcmSecretBox | None = None

    @property
    def _secret_box(self) -> AesGcmSecretBox:
        """Built on first use, not in `__init__`.

        Only the outline call needs a decrypted key, and every other method on
        this service is CRUD that must not require a key to be configured to
        rename a report.
        """
        if self._box is None:
            self._box = AesGcmSecretBox(
                self._settings.secret_box_key.get_secret_value(),
                self._settings.secret_box_key_version,
            )
        return self._box

    # ── reports ──────────────────────────────────────────────────────────
    async def list(self, owner_id: UUID) -> list[Report]:
        result = await self._db.execute(
            select(Report)
            .where(Report.owner_id == owner_id)
            .order_by(Report.updated_at.desc())
        )
        return list(result.scalars())

    async def get(self, report_id: UUID, owner_id: UUID) -> Report:
        result = await self._db.execute(
            select(Report).where(Report.id == report_id, Report.owner_id == owner_id)
        )
        report = result.scalar_one_or_none()
        if report is None:
            # 404 rather than 403: see the module docstring.
            raise NotFoundError("Report not found.")
        return report

    async def create(self, owner_id: UUID, **fields: Any) -> Report:
        name = (fields.get("name") or "").strip()
        if not name:
            raise ValidationError("A report needs a name.")
        await self._refuse_duplicate_name(owner_id, name)

        connection_id = fields.get("connection_id")
        if connection_id is None:
            raise ValidationError(
                "A report needs a database connection, and it cannot be changed "
                "afterwards."
            )
        connection = await self._owned_connection(connection_id, owner_id)
        # Before anything is written: a report that cannot be generated should
        # never reach the list in the first place.
        assert_wide_enough(connection)

        if fields.get("llm_config_id") is not None:
            await self._owned_llm_config(fields["llm_config_id"], owner_id)

        report = Report(
            id=uuid.uuid4(),
            owner_id=owner_id,
            **{**fields, "name": name, "prompt": (fields.get("prompt") or "").strip()},
        )
        self._db.add(report)
        await self._db.flush()
        return report

    async def update(self, report_id: UUID, owner_id: UUID, **changes: Any) -> Report:
        report = await self.get(report_id, owner_id)

        # Mirrors `_bind_connection`: re-sending what it already has is a no-op,
        # so a client that PATCHes a whole object is not punished for it; naming
        # a different connection is refused.
        if changes.pop("connection_id", report.connection_id) != report.connection_id:
            raise ValidationError(
                "A report is pinned to the connection it was created against. "
                "Create a new report to run this analysis against another "
                "database.",
                report_id=str(report.id),
            )

        if (name := changes.get("name")) is not None:
            name = name.strip()
            if not name:
                raise ValidationError("A report needs a name.")
            if name != report.name:
                await self._refuse_duplicate_name(owner_id, name)
            changes["name"] = name

        # The model is swappable at any time — it decides who writes the prose,
        # not what is in it — but it still has to be the caller's own.
        if changes.get("llm_config_id") is not None:
            await self._owned_llm_config(changes["llm_config_id"], owner_id)

        for field, value in changes.items():
            setattr(report, field, value)
        await self._db.flush()
        # `updated_at` has an onupdate, so the attribute is expired after the
        # flush; without the refresh, serialising it lazily loads mid-response
        # and trips MissingGreenlet.
        await self._db.refresh(report)
        return report

    async def delete(self, report_id: UUID, owner_id: UUID) -> None:
        await self._db.delete(await self.get(report_id, owner_id))

    async def _refuse_duplicate_name(self, owner_id: UUID, name: str) -> None:
        existing = await self._db.execute(
            select(Report).where(Report.owner_id == owner_id, Report.name == name)
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("You already have a report with that name.")

    # ── the outline ──────────────────────────────────────────────────────
    async def sections_of(self, report_id: UUID) -> list[ReportSection]:
        result = await self._db.execute(
            select(ReportSection)
            .where(ReportSection.report_id == report_id)
            .order_by(ReportSection.position, ReportSection.created_at)
        )
        return list(result.scalars())

    async def blocks_of(self, section_ids: list[UUID]) -> list[ReportBlock]:
        """Every block under the given sections, in one query.

        One query for the whole outline rather than one per section: a report
        with ten sections is one page, and ten round trips to draw it is how a
        page gets slow before anyone notices.
        """
        if not section_ids:
            return []
        result = await self._db.execute(
            select(ReportBlock)
            .where(ReportBlock.section_id.in_(section_ids))
            .order_by(ReportBlock.position, ReportBlock.created_at)
        )
        return list(result.scalars())

    async def section(
        self, report_id: UUID, section_id: UUID, owner_id: UUID
    ) -> ReportSection:
        await self.get(report_id, owner_id)
        result = await self._db.execute(
            select(ReportSection).where(
                ReportSection.id == section_id, ReportSection.report_id == report_id
            )
        )
        section = result.scalar_one_or_none()
        if section is None:
            raise NotFoundError("Report section not found.")
        return section

    async def add_section(
        self, report_id: UUID, owner_id: UUID, **fields: Any
    ) -> ReportSection:
        await self.get(report_id, owner_id)
        heading = (fields.get("heading") or "").strip()
        if not heading:
            raise ValidationError("A section needs a heading.")

        fields = {**fields, "heading": heading}
        # `None`, not `0`: **position 0 is a real position** — it is where the
        # executive summary lives — so "append to the end" has to be a distinct
        # value from "put it first", not a falsy one.
        if fields.get("position") is None:
            fields["position"] = await self._next_position(
                ReportSection, ReportSection.report_id, report_id
            )
        section = ReportSection(id=uuid.uuid4(), report_id=report_id, **fields)
        self._db.add(section)
        await self._db.flush()
        return section

    async def update_section(
        self, report_id: UUID, section_id: UUID, owner_id: UUID, **changes: Any
    ) -> ReportSection:
        section = await self.section(report_id, section_id, owner_id)
        if (heading := changes.get("heading")) is not None:
            heading = heading.strip()
            if not heading:
                raise ValidationError("A section needs a heading.")
            changes["heading"] = heading

        for field, value in changes.items():
            setattr(section, field, value)
        await self._db.flush()
        await self._db.refresh(section)
        return section

    async def delete_section(
        self, report_id: UUID, section_id: UUID, owner_id: UUID
    ) -> None:
        await self._db.delete(await self.section(report_id, section_id, owner_id))

    # ── the proposed outline ─────────────────────────────────────────────
    async def propose_outline(self, report_id: UUID, owner_id: UUID) -> Report:
        """One model call: the user's request becomes a structure to approve.

        **It replaces whatever outline exists.** Proposing is the "start again"
        button; the granular section and block routes are how an outline is
        edited. Anything the user had written is gone, which is why the UI asks
        before calling this a second time.

        The call is synchronous — one completion, not the minutes a generation
        takes — so the request holds its session for the length of it. That is
        the trade `POST .../blocks/{id}/check` makes too, and the reason
        generation does not.
        """
        report = await self.get(report_id, owner_id)
        request = (report.prompt or "").strip()
        if not request:
            raise ValidationError(
                "Describe what this report should cover before proposing an "
                "outline."
            )
        if report.connection_id is None:
            raise ValidationError(
                "This report's connection was removed, so its outline cannot be "
                "proposed. Past runs stay readable."
            )
        connection = await self._owned_connection(report.connection_id, owner_id)
        if report.llm_config_id is None:
            raise ValidationError("Choose a model for this report before proposing an outline.")
        config = await self._owned_llm_config(report.llm_config_id, owner_id)

        snapshot = await latest_snapshot(self._db, connection.id)
        if not snapshot.get("tables"):
            # Before a token is spent: an unsynced connection can answer
            # nothing, so an outline proposed against it would be invention.
            raise ValidationError(
                "Sync this connection's schema before proposing an outline."
            )

        # The same block the generator sees, under the same disclosure budget:
        # the schema, the foreign keys, and the semantic layer scoped to them.
        semantic = await load_document(self._db, connection)
        context = RetrievedContext(
            dialect=snapshot["dialect"],
            tables=snapshot["tables"],
            relationships=snapshot.get("relationships") or [],
            semantic=(semantic.model_dump(mode="json") if semantic else None),
        )

        proposal = await propose(
            LiteLLMGateway.from_settings(self._settings),
            resolve_llm(config, self._secret_box, min_max_tokens=OUTLINE_MIN_MAX_TOKENS),
            request=request,
            language=report.language,
            dialect=connection.database_type,
            schema_block=context.render(connection.disclosure_policy),
        )
        if proposal.dropped_sections or proposal.dropped_blocks:
            log.info(
                "report_outline_salvaged",
                report_id=str(report.id),
                kept=len(proposal.sections),
                dropped_sections=proposal.dropped_sections,
                dropped_blocks=proposal.dropped_blocks,
            )
        if proposal.is_empty:
            raise LLMError(
                "The model did not return an outline that could be read. Try "
                "again, or say more about what the report should cover."
            )

        await self._replace_outline(report, proposal)
        return report

    async def _replace_outline(
        self, report: Report, proposal: OutlineProposal
    ) -> None:
        """Write the proposal over whatever was there.

        The executive summary goes in at position 0 and carries no blocks: it
        is written last, from the finished sections' prose, and it is an
        ordinary section otherwise — removable, editable, and reorderable.
        """
        for existing in await self.sections_of(report.id):
            await self._db.delete(existing)
        await self._db.flush()

        for position, proposed in enumerate(
            [executive_summary(report.language), *proposal.sections]
        ):
            section = ReportSection(
                id=uuid.uuid4(),
                report_id=report.id,
                position=position,
                heading=proposed.heading,
                intent=proposed.intent,
                kind=(
                    ReportSectionKind.EXECUTIVE_SUMMARY
                    if position == 0
                    else ReportSectionKind.NORMAL
                ),
            )
            self._db.add(section)
            for index, block in enumerate(proposed.blocks):
                self._db.add(
                    ReportBlock(
                        id=uuid.uuid4(),
                        section_id=section.id,
                        position=index,
                        question=block.question,
                        block_type=block.block_type,
                        time_window=block.time_window,
                        # No SQL and no feasibility: a proposed question has
                        # never been near the guard, and `UNCHECKED` is what
                        # the check in Phase 4 is for.
                        feasibility_status=ReportFeasibility.UNCHECKED,
                    )
                )
        await self._db.flush()

    # ── feasibility ──────────────────────────────────────────────────────
    async def check_block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID
    ) -> tuple[ReportBlock, SqlDraft | None]:
        """*Can this be produced, and if not, why* — answered by the guard.

        Synchronous and per block: one `draft_sql` call is five to ten seconds
        and the user is watching one heading, which is the same shape as the
        tile editor's guard check. Checking a whole outline is the frontend
        looping with visible per-block progress, which reads better than a job
        with a progress bar and needs no extra table.

        Every outcome is a *stored answer*, never an exception — including the
        model failing to produce anything. The question this route asks has a
        negative answer, and a 502 would leave the block saying `UNCHECKED`
        with the reason only in a toast.
        """
        report = await self.get(report_id, owner_id)
        block = await self.block(report_id, block_id, owner_id)
        if report.connection_id is None:
            raise ValidationError(
                "This report's connection was removed, so its blocks cannot be "
                "checked. Past runs stay readable."
            )
        connection = await self._owned_connection(report.connection_id, owner_id)
        if report.llm_config_id is None:
            raise ValidationError("Choose a model for this report before checking a block.")

        draft: SqlDraft | None = None
        try:
            draft = await draft_sql(
                self._db,
                self._settings,
                connection_id=connection.id,
                llm_config_id=report.llm_config_id,
                question=block.question,
                owner_id=owner_id,
                extra_rules=report_time_rules(
                    database_type=connection.database_type,
                    time_window=block.time_window,
                    conventions=await self._time_conventions(connection),
                ),
                # The guard reads a statement; this reads the question. Valid
                # SQL can be written for "how is the weather" — it resolves,
                # it is a SELECT, it is safe — so without this the block goes
                # green and reaches a run as a figure nobody asked for. Chat
                # has always halted here; a block is the one place the answer
                # is stored instead of shown, which is why it was missed.
                classify=True,
            )
        except QuestionOutOfScopeError as err:
            # A verdict, not a failure: the question is answerable by nothing,
            # and the user's next move is to rewrite it. Deliberately lands in
            # the same INFEASIBLE row a rejected statement does, so the block
            # reads the same way whichever gate stopped it.
            _record_check(block, ReportFeasibility.INFEASIBLE, err.message)
        except LLMError as err:
            # "The model could not produce a query" *is* the answer to this
            # question, and the user's next move is to reword the block.
            _record_check(block, ReportFeasibility.INFEASIBLE, err.message)
        else:
            _record_check(block, *_verdict(draft))
            if draft.validation_status == "VALID":
                block.sql = draft.sql
                block.sql_hash = sql_fingerprint(draft.sql)
                # Provenance only, and the one value *this* path can set:
                # whatever was there before, what is stored now came from the
                # model. `edit_block_sql` is where the other two come from.
                block.sql_origin = SqlOrigin.GENERATED

        await self._db.flush()
        await self._db.refresh(block)
        return block, draft

    async def edit_block_sql(
        self, report_id: UUID, block_id: UUID, owner_id: UUID, *, sql: str
    ) -> tuple[ReportBlock, SqlDraft]:
        """Store a statement the user wrote, after the guard has read it.

        The same shape as `check_block` and deliberately so — guard, preview,
        verdict, stored row — with the one difference that no model is asked.
        `validate_sql` is the tile editor's hand-written path, and it is the
        same function here, which is what makes a report buildable by someone
        who knows SQL better than they know their own question.

        **A rejected statement is kept**, where `check_block` throws a rejected
        *draft* away. That is not an inconsistency: the semantic layer already
        settled this distinction — an invalid generated metric is dropped and
        an invalid human-written one is flagged and kept, because *"deleting a
        person's work to hide drift is worse than showing it"*. A model draft
        costs one click to reproduce; what somebody typed does not. The verdict
        on the row carries the truth, and the guard reads the statement again
        at execution whatever is stored beside it.
        """
        report = await self.get(report_id, owner_id)
        block = await self.block(report_id, block_id, owner_id)
        if report.connection_id is None:
            raise ValidationError(
                "This report's connection was removed, so its queries cannot "
                "be validated. Past runs stay readable."
            )
        connection = await self._owned_connection(report.connection_id, owner_id)

        statement = sql.strip()
        if not statement:
            raise ValidationError(
                "A query cannot be empty. Check the question to have one "
                "written, or delete the block."
            )

        draft = await validate_sql(
            self._db,
            self._settings,
            connection_id=connection.id,
            sql=statement,
            owner_id=owner_id,
        )

        _record_check(block, *_verdict(draft))
        block.sql_origin = _hand_origin(block)
        block.sql = statement
        block.sql_hash = sql_fingerprint(statement)

        await self._db.flush()
        await self._db.refresh(block)
        return block, draft

    async def _time_conventions(self, connection: DatabaseConnection) -> str:
        """The connection's own time conventions, or nothing.

        Per connection, never per report: fiscal-year start and whether "last
        month" is calendar or rolling are facts about the database, and the
        semantic layer is where this codebase already records them.
        """
        document = await load_document(self._db, connection)
        return _render_time(document) if document else ""

    # ── runs ─────────────────────────────────────────────────────────────
    async def create_run(self, report_id: UUID, owner_id: UUID) -> ReportRun:
        """Queue a generation. Everything that can be refused is refused here.

        The worker inherits nothing it has to re-derive except the one thing it
        *must* re-derive: the disclosure policy, which is checked again at the
        start of the run because a policy tightened between this row being
        written and the run starting has to stop it (§7).

        A run with no statement to execute is refused rather than queued, and
        so is a second concurrent one: both would spend a worker slot to reach
        an answer the user could have been given synchronously.
        """
        report = await self.get(report_id, owner_id)

        active = await self._db.execute(
            select(ReportRun)
            .where(
                ReportRun.report_id == report_id,
                ReportRun.status.in_(tuple(ACTIVE_RUN_STATUSES)),
            )
            .limit(1)
        )
        if active.scalar_one_or_none() is not None:
            raise ConflictError("This report is already being generated.")

        if report.connection_id is None:
            raise ValidationError(
                "This report's connection was removed, so it cannot be "
                "generated. Past runs stay readable."
            )
        connection = await self._owned_connection(report.connection_id, owner_id)
        assert_wide_enough(connection)

        if report.llm_config_id is None:
            raise ValidationError("Choose a model for this report before generating it.")
        config = await self._owned_llm_config(report.llm_config_id, owner_id)

        sections = await self.sections_of(report_id)
        blocks = await self.blocks_of([s.id for s in sections])
        if not any(block.sql.strip() for block in blocks):
            # A block that has never been checked carries no statement, and a
            # run of nothing but those produces a document of error messages.
            raise ValidationError(
                "None of this report's blocks has a query yet. Check them "
                "first — an unchecked block has no SQL to run."
            )

        run = ReportRun(
            id=uuid.uuid4(),
            report_id=report_id,
            owner_id=owner_id,
            status=ReportRunStatus.QUEUED,
            llm_config_id=config.id,
            # Which model wrote this document, kept beside it: *"a layer
            # generated by a weak model is a different artefact from one
            # generated by a strong one, and six months later nobody remembers
            # which."*
            model_snapshot={"provider": config.provider, "model": config.model},
            prompt_version=REPORT_PROMPT_VERSION,
            # Snapshotted, not read through the report: a past run stays
            # readable in the language it was written in.
            language=report.language,
            # Every query, plus every paragraph — the two kinds of work the
            # header counts through. The executive summary is a section like
            # any other here, because writing it is a step too.
            progress_total=len(blocks) + len(sections),
        )
        self._db.add(run)
        await self._db.flush()
        return run

    async def runs_of(self, report_id: UUID, owner_id: UUID) -> list[ReportRun]:
        """The report's history, newest first. Rows only — a history list shows
        when and how it went, never every row of every result."""
        await self.get(report_id, owner_id)
        result = await self._db.execute(
            select(ReportRun)
            .where(ReportRun.report_id == report_id)
            .order_by(ReportRun.created_at.desc())
        )
        return list(result.scalars())

    async def run(self, report_id: UUID, run_id: UUID, owner_id: UUID) -> ReportRun:
        await self.get(report_id, owner_id)
        result = await self._db.execute(
            select(ReportRun).where(
                ReportRun.id == run_id, ReportRun.report_id == report_id
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise NotFoundError("Report run not found.")
        return run

    async def run_results(
        self, run_id: UUID
    ) -> tuple[list[ReportBlockResult], list[ReportSectionResult]]:
        """Everything written so far — which is the whole poll design.

        The response is a snapshot of what exists at this instant, so a run
        half-finished renders as a half-finished document with no special
        protocol, and a browser that reloads mid-run resumes where it was.
        """
        blocks = await self._db.execute(
            select(ReportBlockResult)
            .where(ReportBlockResult.run_id == run_id)
            .order_by(ReportBlockResult.position)
        )
        sections = await self._db.execute(
            select(ReportSectionResult)
            .where(ReportSectionResult.run_id == run_id)
            .order_by(ReportSectionResult.position)
        )
        return list(blocks.scalars()), list(sections.scalars())

    async def previous_block_hashes(self, run: ReportRun) -> dict[UUID, str]:
        """`block_id → sql_hash`, as of the generation before this one.

        This is what makes "the query behind this figure changed" answerable,
        and it is the reason `sql_hash` is copied onto every result rather than
        only onto the block: the block carries what its statement is *now*,
        while a document has to be comparable with the document before it.

        "Before" means the most recent earlier run that actually computed
        something. A cancelled run that wrote no rows is not a previous version
        of this report, and comparing against it would report every figure as
        unchanged — which is worse than saying nothing, because it is an answer.
        """
        previous = await self._db.execute(
            select(ReportBlockResult.run_id)
            .join(ReportRun, ReportRun.id == ReportBlockResult.run_id)
            .where(
                ReportRun.report_id == run.report_id,
                ReportRun.created_at < run.created_at,
            )
            .order_by(ReportRun.created_at.desc())
            .limit(1)
        )
        previous_run_id = previous.scalar_one_or_none()
        if previous_run_id is None:
            return {}

        rows = await self._db.execute(
            select(ReportBlockResult.block_id, ReportBlockResult.sql_hash).where(
                ReportBlockResult.run_id == previous_run_id
            )
        )
        # A result whose block has since been deleted keeps its snapshots but
        # loses its `block_id`, and there is nothing left to match it by.
        return {
            block_id: sql_hash for block_id, sql_hash in rows if block_id is not None
        }

    async def request_section_retry(
        self, report_id: UUID, run_id: UUID, section_id: UUID, owner_id: UUID
    ) -> ReportRun:
        """Put a finished run back to work on one of its sections.

        Everything a retry cannot recover from is refused here rather than
        discovered by the worker: a run still in flight, a section that is not
        part of this report, a connection that has since been removed or
        narrowed. What the worker inherits is a run it can actually retry.

        The run goes back to `RUNNING` and the viewer's existing poll shows the
        section rebuild itself. Nothing else about the run is touched — the
        other sections stay on screen, and the status is *re-derived* when the
        retry lands rather than transitioned.
        """
        run = await self.run(report_id, run_id, owner_id)
        if run.status in ACTIVE_RUN_STATUSES:
            raise ConflictError(
                "This report is already being generated. Wait for it to finish, "
                "then retry the section."
            )
        section = await self.section(report_id, section_id, owner_id)

        report = await self.get(report_id, owner_id)
        if report.connection_id is None:
            raise ValidationError(
                "This report's connection was removed, so its sections cannot "
                "be retried. Past runs stay readable."
            )
        assert_wide_enough(await self._owned_connection(report.connection_id, owner_id))

        run.status = ReportRunStatus.RUNNING
        run.phase = f"Retrying {section.heading}"[:200]
        run.error_message = None
        run.finished_at = None
        await self._db.flush()
        return run

    async def section_result(
        self, report_id: UUID, run_id: UUID, section_id: UUID, owner_id: UUID
    ) -> ReportSectionResult:
        await self.run(report_id, run_id, owner_id)
        result = await self._db.execute(
            select(ReportSectionResult).where(
                ReportSectionResult.run_id == run_id,
                ReportSectionResult.section_id == section_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("This run has no such section.")
        return row

    async def edit_prose(
        self,
        report_id: UUID,
        run_id: UUID,
        section_id: UUID,
        owner_id: UUID,
        *,
        edited_prose: str | None,
    ) -> ReportSectionResult:
        """Write over a paragraph — on the run, never on the template.

        `edited_prose` is a second column rather than an overwrite: the model's
        own words stay beside the user's, so a regeneration cannot destroy
        writing and reverting is free. Sending `null` is that revert, which is
        why the column is nullable and why NULL has to keep meaning *not
        edited* rather than *edited to nothing*.
        """
        row = await self.section_result(report_id, run_id, section_id, owner_id)
        row.edited_prose = edited_prose
        await self._db.flush()
        return row

    async def redraw_block_chart(
        self,
        report_id: UUID,
        run_id: UUID,
        result_id: UUID,
        owner_id: UUID,
        *,
        chart_type: str,
    ) -> tuple[ReportBlockResult, list[dict[str, Any]], str | None]:
        """Draw one block of a saved run as a different chart type.

        **Redrawn from the rows the run kept, never by re-running the query.**
        The picture under a paragraph has to be the picture that paragraph was
        written from, and a database that has moved on since would quietly make
        the two disagree.

        Unlike the chat redraw, this one is **persisted** — onto the run, and
        only onto the run. Two reasons, and they are the same two that put
        `edited_prose` on `report_section_results`: a report is a document that
        is kept and printed (Phase 10 prints the *saved* run, so a chart that
        lives only in the browser would not survive being exported), and a
        refinement made while reading one run must not rewrite the template
        every later run is generated from.

        The type goes through `plan_chart` like any other suggestion, so a pick
        the data cannot carry is refused with its reason rather than compiled
        into something misleading. The picker will not offer such a type in the
        first place; this is the same rule stated where it would matter if the
        display were stale.
        """
        from dataclasses import asdict

        from app.charts import (
            candidate_intent,
            chart_options,
            compile_vega_lite,
            plan_chart,
            profile_result,
        )
        from app.domain.ports.database import ResultColumn

        await self.run(report_id, run_id, owner_id)
        found = await self._db.execute(
            select(ReportBlockResult).where(
                ReportBlockResult.id == result_id, ReportBlockResult.run_id == run_id
            )
        )
        row = found.scalar_one_or_none()
        if row is None:
            raise NotFoundError("This run has no such result.")
        if row.status != "OK" or not row.rows:
            raise ValidationError("This block kept no result to draw.")

        columns = [
            ResultColumn(
                name=column["name"],
                db_type=column.get("db_type", ""),
                semantic_type=column.get("semantic_type", "nominal"),
            )
            for column in row.columns
        ]
        profile = profile_result(columns, row.rows, truncated=bool(row.truncated))
        options = [asdict(option) for option in chart_options(profile)]

        # "auto" is not a type, it is the absence of one: hand `plan_chart` no
        # suggestion and let the planner decide from the data, which is what a
        # re-run months from now on differently-shaped data would do anyway.
        if chart_type == "auto":
            plan = plan_chart(profile)
        else:
            intent = candidate_intent(profile, chart_type)
            plan = plan_chart(profile, intent) if intent is not None else None  # type: ignore[assignment]

        if plan is None or plan.intent is None or (
            chart_type != "auto" and plan.intent.chart_type != chart_type
        ):
            reason = next(
                (
                    option["reason"]
                    for option in options
                    if option["chart_type"] == chart_type and option["reason"]
                ),
                None,
            ) or (plan.reason if plan is not None else None)
            return row, options, reason or "This result cannot be drawn that way."

        row.vega_spec = compile_vega_lite(plan.intent, profile, columns, row.rows)
        # `user` is a fifth source beside model / model_adjusted / heuristic /
        # none, and it is worth recording: six months on, "who chose this
        # picture" is the same question `model_snapshot` exists to answer.
        row.chart_source = "heuristic" if chart_type == "auto" else "user"
        # The note explains a *demotion* from what was asked for. Nothing was
        # demoted here — the planner agreed with the pick — so it goes.
        row.chart_note = None
        await self._db.flush()
        await self._db.refresh(row)
        return row, options, None

    async def cancel_run(self, report_id: UUID, run_id: UUID, owner_id: UUID) -> bool:
        """Mark it cancelled. The worker stops between blocks and sees this.

        Writing the row here rather than waiting for the worker is what makes
        the answer immediate: the poll shows CANCELLED on its next tick even
        though an in-flight query is still finishing.
        """
        run = await self.run(report_id, run_id, owner_id)
        if run.status not in ACTIVE_RUN_STATUSES:
            return False
        run.status = ReportRunStatus.CANCELLED
        run.finished_at = utcnow()
        await self._db.flush()
        return True

    # ── blocks ───────────────────────────────────────────────────────────
    async def block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID
    ) -> ReportBlock:
        """A block, reached through its report so ownership is checked.

        The join is what makes `/reports/{id}/blocks/{bid}` safe as a flat path:
        a block id from someone else's report matches no row here, so it is a
        404 like everything else.
        """
        await self.get(report_id, owner_id)
        result = await self._db.execute(
            select(ReportBlock)
            .join(ReportSection, ReportSection.id == ReportBlock.section_id)
            .where(ReportBlock.id == block_id, ReportSection.report_id == report_id)
        )
        block = result.scalar_one_or_none()
        if block is None:
            raise NotFoundError("Report block not found.")
        return block

    async def add_block(
        self, report_id: UUID, section_id: UUID, owner_id: UUID, **fields: Any
    ) -> ReportBlock:
        report = await self.get(report_id, owner_id)
        await self.section(report_id, section_id, owner_id)

        question = (fields.get("question") or "").strip()
        if not question:
            raise ValidationError("A block needs a question.")
        fields = await self._clamped(report, {**fields, "question": question})

        if fields.get("position") is None:
            fields["position"] = await self._next_position(
                ReportBlock, ReportBlock.section_id, section_id
            )
        block = ReportBlock(id=uuid.uuid4(), section_id=section_id, **fields)
        self._db.add(block)
        await self._db.flush()
        return block

    async def update_block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID, **changes: Any
    ) -> ReportBlock:
        report = await self.get(report_id, owner_id)
        block = await self.block(report_id, block_id, owner_id)

        if (question := changes.get("question")) is not None:
            question = question.strip()
            if not question:
                raise ValidationError("A block needs a question.")
            changes["question"] = question
        changes = await self._clamped(report, changes)

        stale = any(
            field in changes and changes[field] != getattr(block, field)
            for field in _SQL_INVALIDATING
        )
        # Whose statement this is decides what happens to it, and the decision
        # has to be read *before* the change lands.
        generated = block.sql_origin == SqlOrigin.GENERATED

        for field, value in changes.items():
            setattr(block, field, value)
        if stale:
            # See the module docstring: the stored statement answers the old
            # question. The verdict always goes — a run must not produce the
            # right numbers under the wrong heading.
            block.feasibility_status = ReportFeasibility.UNCHECKED
            block.feasibility_reason = None
            block.feasibility_checked_at = None
            if generated:
                # A model draft costs one click to reproduce, so drop it.
                block.sql = ""
                block.sql_hash = ""
            # A hand-written or hand-edited statement is kept. Rewording a
            # question is how someone fixes a heading, and losing an hour of
            # SQL to a typo fix is the kind of thing a person never forgives a
            # tool for. It is back to `UNCHECKED` either way, so nothing runs
            # on it until the user says the two still belong together.

        await self._db.flush()
        await self._db.refresh(block)
        return block

    async def delete_block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID
    ) -> None:
        await self._db.delete(await self.block(report_id, block_id, owner_id))

    async def _clamped(self, report: Report, fields: dict[str, Any]) -> dict[str, Any]:
        """A block's row cap, stored already lowered to what will be honoured.

        `max_rows` may only *tighten* the connection's cap. Clamping on the way
        in rather than at execution means the editor never shows a number the
        connection would silently ignore.
        """
        fields = dict(fields)
        if fields.get("max_rows") is None or report.connection_id is None:
            return fields
        connection = await self._db.get(DatabaseConnection, report.connection_id)
        if connection is not None:
            fields["max_rows"] = effective_max_rows(connection, fields["max_rows"])
        return fields

    async def _next_position(self, model: Any, column: Any, parent_id: UUID) -> int:
        """Append at the end.

        `max(position) + 1`, not `count()`: a list that has had a middle entry
        deleted would otherwise hand the new one a position it already uses, and
        two rows sharing a position sort by insertion time — which reads as
        random to the person who arranged them.
        """
        result = await self._db.execute(
            select(func.max(model.position)).where(column == parent_id)
        )
        return int(result.scalar() or 0) + 1

    # ── display names ────────────────────────────────────────────────────
    async def display_names(
        self, reports: list[Report]
    ) -> tuple[dict[UUID, str], dict[UUID, str]]:
        """Labels for the chips: what the connection and the model are *called*.

        Names only — a report carries ids and display names, never a host, a
        username, or anything else from inside a connection.
        """
        connection_ids = {r.connection_id for r in reports if r.connection_id}
        model_ids = {r.llm_config_id for r in reports if r.llm_config_id}

        connections: dict[UUID, str] = {}
        if connection_ids:
            rows = await self._db.execute(
                select(DatabaseConnection.id, DatabaseConnection.name).where(
                    DatabaseConnection.id.in_(connection_ids)
                )
            )
            connections = {row[0]: row[1] for row in rows}

        models: dict[UUID, str] = {}
        if model_ids:
            rows = await self._db.execute(
                select(LlmConfig.id, LlmConfig.name).where(LlmConfig.id.in_(model_ids))
            )
            models = {row[0]: row[1] for row in rows}
        return connections, models

    # ── ownership ────────────────────────────────────────────────────────
    async def _owned_connection(
        self, connection_id: UUID, owner_id: UUID
    ) -> DatabaseConnection:
        result = await self._db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.id == connection_id,
                DatabaseConnection.owner_id == owner_id,
            )
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise NotFoundError("Connection not found.")
        return connection

    async def _owned_llm_config(self, llm_config_id: UUID, owner_id: UUID) -> LlmConfig:
        result = await self._db.execute(
            select(LlmConfig).where(
                LlmConfig.id == llm_config_id, LlmConfig.owner_id == owner_id
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise NotFoundError("Model configuration not found.")
        return config
