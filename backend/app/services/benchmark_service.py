"""Benchmark sets and their history — the customer's own accuracy number.

The transaction boundary and every DB call for Phase 6. The *execution* lives
in `app/workers/benchmark.py`, because a run is one model call per question and
belongs nowhere near a request.

Three rules keep the number honest, and all three are enforced here rather than
described:

1. **A template is retrievable or benchmarkable, never both.** Creating a set
   moves each member's `role` off `RETRIEVABLE`, so a member can never be
   short-circuited on the ask path — a question answered from its own stored
   SQL measures the store's ability to hold a string. `release` puts them back.
2. **A fixed fraction is `HELD_OUT` at creation**, chosen deterministically by
   sorted id at a fixed stride, and never retrieved. **That is the only number
   worth putting in front of a customer.**
3. **The split is reported.** Accuracy on questions answered *from* a template
   and accuracy on questions answered *without* one are different numbers, and
   only the second moves for a reason. Genie's Evaluations tab shows one
   number; that is a weakness to improve on, not a design to copy.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.infra.db.models import (
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkSet,
    DatabaseConnection,
    KnowledgeTemplateRow,
)
from app.knowledge import TemplateRole, TemplateStatus

log = get_logger(__name__)

#: What share of a set is held out at creation. Two in five: large enough that
#: the held-out number is not a coin toss on a small store, small enough that
#: most of what a curator taught still answers questions.
DEFAULT_HELD_OUT_FRACTION = 0.4

#: Below this, a set's numbers are noise dressed as a percentage. The API
#: refuses rather than producing a 100%-on-three-questions score strip, which
#: is exactly the number somebody would quote.
MIN_SET_SIZE = 4

MAX_SET_SIZE = 200

QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"

#: The same vocabulary `app/eval/metrics.py` uses. The two instruments must not
#: share a *table*; sharing the word for "the answer was right" costs nothing
#: and means one reader can read both.
OUTCOME_MATCH = "MATCH"
OUTCOME_MISMATCH = "MISMATCH"
OUTCOME_EXEC_FAILED = "EXEC_FAILED"
OUTCOME_VALIDATION_FAILED = "VALIDATION_FAILED"
OUTCOME_NO_SQL = "NO_SQL"
#: A member whose parameters could not be filled with probe values. Counted in
#: `total` and **not** in `scored`, so an accuracy is never quietly computed
#: over a shrinking denominator.
OUTCOME_NOT_PROBED = "NOT_PROBED"
OUTCOME_ERROR = "ERROR"


def held_out_split(template_ids: list[UUID], fraction: float) -> set[UUID]:
    """Which members are held out. Deterministic, by sorted id at a stride.

    Deterministic and not random, for the reason the eval arm's split is: two
    sets built from the same templates that held out different questions are
    two different instruments, and a customer comparing their numbers would
    never find out. Sorted-id-at-a-stride is reproducible from the set's own
    membership list, so the split can be re-derived years later.
    """
    ordered = sorted(template_ids, key=str)
    if not ordered or fraction <= 0:
        return set()
    stride = max(1, round(1 / min(fraction, 1.0)))
    return {tid for i, tid in enumerate(ordered) if i % stride == 0}


@dataclass(slots=True)
class Score:
    """One run's two numbers, as the score strip reads them."""

    held_out_total: int = 0
    held_out_matched: int = 0
    taught_total: int = 0
    taught_matched: int = 0

    @property
    def held_out_accuracy(self) -> float | None:
        """The number that goes first and larger, or None when there is none.

        `None` rather than 0.0 on an empty denominator: a run that scored no
        held-out question has *no* held-out accuracy, and printing 0% for it
        would be the loudest possible wrong answer.
        """
        return (
            self.held_out_matched / self.held_out_total
            if self.held_out_total else None
        )

    @property
    def taught_accuracy(self) -> float | None:
        return (
            self.taught_matched / self.taught_total if self.taught_total else None
        )


class BenchmarkService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    # ── reading ──────────────────────────────────────────────────────────
    async def list_sets(
        self, connection: DatabaseConnection
    ) -> list[BenchmarkSet]:
        result = await self._db.execute(
            select(BenchmarkSet)
            .where(BenchmarkSet.connection_id == connection.id)
            .order_by(BenchmarkSet.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_set(
        self, connection: DatabaseConnection, set_id: UUID
    ) -> BenchmarkSet:
        row = await self._db.get(BenchmarkSet, set_id)
        if row is None or row.connection_id != connection.id:
            raise NotFoundError("Benchmark set not found.")
        return row

    async def runs(self, set_row: BenchmarkSet, *, limit: int = 6) -> list[BenchmarkRun]:
        """The history, newest first. Six by default — §4.8's sparkline.

        A sparkline of two points is a line, and a sparkline of sixty is a
        smudge on a strip that is one line tall.
        """
        result = await self._db.execute(
            select(BenchmarkRun)
            .where(BenchmarkRun.set_id == set_row.id)
            .order_by(desc(BenchmarkRun.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def results(self, run: BenchmarkRun) -> list[BenchmarkResult]:
        result = await self._db.execute(
            select(BenchmarkResult)
            .where(BenchmarkResult.run_id == run.id)
            .order_by(BenchmarkResult.created_at)
        )
        return list(result.scalars().all())

    async def get_run(
        self, connection: DatabaseConnection, run_id: UUID
    ) -> BenchmarkRun:
        row = await self._db.get(BenchmarkRun, run_id)
        if row is None or row.connection_id != connection.id:
            raise NotFoundError("Benchmark run not found.")
        return row

    # ── candidates ───────────────────────────────────────────────────────
    async def candidates(
        self, connection: DatabaseConnection
    ) -> list[KnowledgeTemplateRow]:
        """Templates a set may be built from: live, and not already measuring.

        `ARCHIVED`, `STALE` and `CONFLICTED` are excluded — a benchmark whose
        gold SQL no longer runs measures the schema, not the product — and so
        are templates already committed to another set, because a template
        cannot be held out of one instrument and answering questions for
        another.
        """
        result = await self._db.execute(
            select(KnowledgeTemplateRow).where(
                KnowledgeTemplateRow.connection_id == connection.id,
                KnowledgeTemplateRow.status == str(TemplateStatus.ACTIVE),
                KnowledgeTemplateRow.role == str(TemplateRole.RETRIEVABLE),
            ).order_by(KnowledgeTemplateRow.created_at.desc())
        )
        return list(result.scalars().all())

    # ── writing ──────────────────────────────────────────────────────────
    async def create_set(
        self,
        connection: DatabaseConnection,
        *,
        actor_id: UUID,
        name: str,
        template_ids: list[UUID],
        description: str = "",
        held_out_fraction: float = DEFAULT_HELD_OUT_FRACTION,
    ) -> BenchmarkSet:
        """Build a set, and move every member's role off `RETRIEVABLE`.

        **The role change is the point, not a side effect.** §1.3's rule is
        that a template is retrievable or benchmarkable and never both, and it
        is enforced in the query that builds the ask path's candidate set — so
        the only way to keep a benchmark honest is for its members to stop
        being retrievable. A curator who wants a question back gets it back by
        deleting the set (`release`), which is a decision rather than an
        accident.
        """
        name = " ".join((name or "").split())[:120]
        if not name:
            raise ValidationError("Give this benchmark set a name.")

        wanted = list(dict.fromkeys(template_ids))
        if len(wanted) < MIN_SET_SIZE:
            raise ValidationError(
                f"A benchmark needs at least {MIN_SET_SIZE} questions — fewer "
                "than that is noise with a percentage sign on it."
            )
        if len(wanted) > MAX_SET_SIZE:
            raise ValidationError(
                f"A benchmark set holds at most {MAX_SET_SIZE} questions."
            )

        rows = await self._members(connection, wanted)
        held_out = held_out_split(wanted, held_out_fraction)

        set_row = BenchmarkSet(
            id=uuid.uuid4(),
            connection_id=connection.id,
            name=name,
            description=(description or "").strip()[:2_000],
            template_ids=wanted,
            held_out_fraction=held_out_fraction,
            created_by=actor_id,
        )
        self._db.add(set_row)

        for row in rows:
            row.role = str(
                TemplateRole.HELD_OUT if row.id in held_out
                else TemplateRole.BENCHMARK_ONLY
            )

        try:
            await self._db.flush()
        except IntegrityError as err:
            raise ConflictError(
                f"This connection already has a benchmark set called “{name}”."
            ) from err

        log.info(
            "benchmark_set_created",
            connection_id=str(connection.id),
            members=len(wanted),
            held_out=len(held_out),
        )
        return set_row

    async def release(
        self, connection: DatabaseConnection, set_id: UUID
    ) -> BenchmarkSet:
        """Delete a set and give its questions back to the ask path.

        Deletes the set row (its runs cascade) and returns every member to
        `RETRIEVABLE`. A template that was archived or went stale while it was
        in the set is left where it is — this restores a *role*, and it is not
        the place to overrule a curator or the staleness sweep.
        """
        set_row = await self.get_set(connection, set_id)
        for row in await self._members(connection, list(set_row.template_ids or [])):
            if row.role in (
                str(TemplateRole.HELD_OUT), str(TemplateRole.BENCHMARK_ONLY)
            ):
                row.role = str(TemplateRole.RETRIEVABLE)
        await self._db.delete(set_row)
        await self._db.flush()
        return set_row

    async def queue_run(
        self,
        connection: DatabaseConnection,
        set_row: BenchmarkSet,
        *,
        actor_id: UUID,
        llm_config_id: UUID | None,
    ) -> BenchmarkRun:
        """Queue an execution. The worker is what runs it.

        A row first, then the worker — the same order `semantic_jobs` uses, and
        for the same reason: a process that dies mid-run leaves a `RUNNING` row
        somebody can see and retry, rather than a request that never came back.
        """
        if await self._is_running(set_row):
            raise ConflictError("This benchmark is already running.")
        run = BenchmarkRun(
            id=uuid.uuid4(),
            set_id=set_row.id,
            connection_id=connection.id,
            llm_config_id=llm_config_id,
            status=QUEUED,
            total=len(set_row.template_ids or []),
            created_by=actor_id,
        )
        self._db.add(run)
        await self._db.flush()
        return run

    # ── scoring (called by the worker) ───────────────────────────────────
    @staticmethod
    def score(results: list[BenchmarkResult]) -> Score:
        """The two numbers, from the results. Pure, so it is tested directly.

        The **held-out** number counts members whose role is `HELD_OUT`: their
        own SQL was never retrievable, so the answer was generated. The
        **taught** number counts results the run actually answered *from* a
        template — the observed fact, not a label — which is §3.7's rule 3
        verbatim, and it is the number that goes up for the wrong reasons.

        A result that never scored (`NOT_PROBED`, `ERROR`) is in neither
        denominator. An accuracy computed over questions that did not run is
        the classic silent lie, and it always flatters.
        """
        out = Score()
        for row in results:
            if row.outcome in (OUTCOME_NOT_PROBED, OUTCOME_ERROR):
                continue
            matched = row.outcome == OUTCOME_MATCH
            if row.role == str(TemplateRole.HELD_OUT):
                out.held_out_total += 1
                out.held_out_matched += int(matched)
            if row.from_template:
                out.taught_total += 1
                out.taught_matched += int(matched)
        return out

    # ── internals ────────────────────────────────────────────────────────
    async def _members(
        self, connection: DatabaseConnection, template_ids: list[UUID]
    ) -> list[KnowledgeTemplateRow]:
        if not template_ids:
            return []
        result = await self._db.execute(
            select(KnowledgeTemplateRow).where(
                KnowledgeTemplateRow.connection_id == connection.id,
                KnowledgeTemplateRow.id.in_(template_ids),
            )
        )
        rows = list(result.scalars().all())
        missing = set(template_ids) - {r.id for r in rows}
        if missing:
            raise ValidationError(
                "Some of those questions are not on this connection."
            )
        return rows

    async def _is_running(self, set_row: BenchmarkSet) -> bool:
        result = await self._db.execute(
            select(BenchmarkRun).where(
                BenchmarkRun.set_id == set_row.id,
                BenchmarkRun.status.in_((QUEUED, RUNNING)),
            )
        )
        return result.scalars().first() is not None
