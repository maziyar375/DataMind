"""Store health: the sweep that stops a curated store decaying into noise.

Phase 4 of `docs/learning-loop-plan.md`. A curated store degrades two ways and
both are handled here.

**Staleness** is a parse. On every schema sync, every live template is
re-validated against the new snapshot; one whose SQL no longer resolves becomes
`STALE` with the guard's own sentence in `status_reason`, is withdrawn from
matching and from few-shot, and is never deleted. That half lives in
`KnowledgeService.sweep_staleness` — it makes no call to the customer's
database, so the sync that caused the drift can run it inline and the curator
sees the amber row on the screen the sync returns to.

**Conflict** is an execution, and it is the reason this module exists. Two
templates whose normalised questions are near-duplicates and whose *results*
differ on the same connection is a fact, not an opinion — Fabric reasons over
SQL text and reports a confidence of one to five; this runs both statements and
shows the rows that disagree. That costs two read-only queries per pair against
a customer's database, so it never runs on a request path, never on a refresh
path, and can be switched off per connection.

**The containment rules the checker inherits, without exception:**

* the connection's own read-only credentials, via `bind_connector`;
* the guard, via `execute_saved_sql` — the same entry point a dashboard tile
  uses, so the statement is re-validated against the current snapshot,
  rewritten and row-capped before anything runs it;
* `connections.max_rows` and `statement_timeout_ms`;
* `connections.conflict_checks_enabled`, which is the customer's off switch and
  is checked before a connector is opened, not after.

**And one it does not need.** The disclosure policy governs what reaches *the
model*; this makes no model call. The rows it reads are compared by
`app/knowledge/compare.py` and the diverging ones are stored as evidence for
the connection's own curator — the same person who can already run the
statement in the editor and read every row of it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.logging import get_logger
from app.infra.db.models import DatabaseConnection, KnowledgeTemplateRow
from app.knowledge import (
    Divergence,
    TemplateRole,
    TemplateStatus,
    first_difference,
    probe_values,
    similar_pairs,
)
from app.knowledge.bind import bind_sql
from app.knowledge.conflict import Pair
from app.services.knowledge_service import (
    KnowledgeService,
    StalenessResult,
)
from app.services.query_service import (
    bind_connector,
    execute_saved_sql,
    latest_snapshot,
    secret_box,
)

log = get_logger(__name__)

#: How many pairs one pass will execute. A ceiling rather than a setting: the
#: pair count is quadratic in the store's size, and a connection whose store
#: has grown to the point where this binds has a curation problem the checker
#: cannot fix by running for an hour against the customer's database.
MAX_PAIRS_PER_PASS = 25

#: Rows read per side while comparing. Well under `connections.max_rows`,
#: because a conflict shows itself in the first page of a result set and
#: pulling ten thousand rows twice to prove it would be spending the customer's
#: database to make a point already made.
COMPARE_ROW_CAP = 500


@dataclass(slots=True)
class ConflictResult:
    """What one conflict pass found, in ids rather than counts."""

    pairs_considered: int = 0
    pairs_executed: int = 0
    #: Pairs that could not be probed — a string slot with no declared values,
    #: most often. Named so the log says which slot, for the same reason
    #: `REJECTED_UNBOUND` names its slots on the ask path.
    skipped: list[str] = field(default_factory=list)
    conflicted: list[UUID] = field(default_factory=list)
    cleared: list[UUID] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.conflicted) + len(self.cleared)


@dataclass(slots=True)
class MaintenanceResult:
    """Both halves of one pass, so a caller reports one thing."""

    staleness: StalenessResult = field(default_factory=StalenessResult)
    conflicts: ConflictResult = field(default_factory=ConflictResult)
    #: False when `conflict_checks_enabled` is off. Reported rather than
    #: inferred from a zero, because "found nothing" and "was not allowed to
    #: look" are different sentences and the UI must not print the first for
    #: the second.
    conflicts_checked: bool = False


# ── the whole pass ───────────────────────────────────────────────────────
async def run_maintenance(
    db: AsyncSession,
    settings: Settings,
    connection: DatabaseConnection,
    *,
    check_conflicts: bool = True,
) -> MaintenanceResult:
    """Staleness first, then conflicts. Order matters.

    A template the schema broke is not a template whose *meaning* disagrees
    with another's — it is one that cannot run at all. Sweeping staleness first
    means the conflict pass never tries to execute a statement the guard is
    about to reject, and never marks a pair `CONFLICTED` for a disagreement
    that is really a missing column.
    """
    out = MaintenanceResult()
    service = KnowledgeService(db, settings)
    out.staleness = await service.sweep_staleness(connection)

    if check_conflicts and connection.conflict_checks_enabled:
        out.conflicts_checked = True
        out.conflicts = await detect_conflicts(db, settings, connection)
    return out


# ── conflicts ────────────────────────────────────────────────────────────
async def detect_conflicts(
    db: AsyncSession, settings: Settings, connection: DatabaseConnection
) -> ConflictResult:
    """Run near-duplicate templates against each other and compare the rows.

    The five steps from §3.5, in order:

    1. find pairs above a normalised-question similarity threshold;
    2. bind both to the same parameter values;
    3. execute both through the guard, read-only, row-capped;
    4. compare with `app/knowledge/compare.py`;
    5. differ → both `CONFLICTED`, `conflicts_with` populated, **the diverging
       rows stored** so the curator sees the evidence rather than a warning.

    A pair that now *agrees* clears the conflict on both rows — the same
    reasoning that makes `sweep_staleness` revive a healed template. A store
    that can enter a bad state and never leave it is one nobody trusts.
    """
    out = ConflictResult()
    rows = await _candidates(db, connection)
    if len(rows) < 2:
        return out

    by_id = {row.id: row for row in rows}
    templates = [KnowledgeService.to_model(row) for row in rows]
    pairs = similar_pairs(templates)
    out.pairs_considered = len(pairs)
    if not pairs:
        await _clear_all(rows, out)
        await db.flush()
        return out

    now = utcnow()
    # Loaded once and passed down, the way the dashboard refresh path does it:
    # `execute_saved_sql` would otherwise re-read the snapshot twice per pair,
    # and every read would return the same document.
    snapshot = await latest_snapshot(db, connection.id)
    connector = bind_connector(connection, secret_box(settings))
    conflicted_ids: set[UUID] = set()
    try:
        for pair in pairs[:MAX_PAIRS_PER_PASS]:
            probe = probe_values(
                [*pair.left.params, *pair.right.params], now=now
            )
            if not probe.ok:
                out.skipped.append(
                    f"{pair.left.id}/{pair.right.id}: "
                    f"no value for {', '.join(probe.unfilled)}"
                )
                continue

            left_sql = bind_sql(
                pair.left.sql, probe.values, dialect=connector.dialect
            )
            right_sql = bind_sql(
                pair.right.sql, probe.values, dialect=connector.dialect
            )
            if not left_sql or not right_sql:
                out.skipped.append(
                    f"{pair.left.id}/{pair.right.id}: a statement would not bind"
                )
                continue

            left = await _run(db, settings, connection, left_sql, connector, snapshot)
            right = await _run(db, settings, connection, right_sql, connector, snapshot)
            out.pairs_executed += 1
            if left is None or right is None:
                # One of them failed to execute. That is a staleness question,
                # not a conflict one, and the sweep above has already had its
                # say — inventing a conflict from an error would put two rows
                # in amber for a reason the evidence pane could not show.
                out.skipped.append(
                    f"{pair.left.id}/{pair.right.id}: a statement did not run"
                )
                continue

            divergence = first_difference(
                left.rows,
                right.rows,
                left_columns=left.columns,
                right_columns=right.columns,
            )
            if divergence.differs:
                _mark(by_id, pair, divergence, out)
                conflicted_ids.update({pair.left.id, pair.right.id})  # type: ignore[arg-type]
    finally:
        await connector.close()

    # Anything that was conflicted, was re-tested this pass, and no longer
    # disagrees. A row that was *not* re-tested keeps its conflict: silence is
    # not evidence of agreement.
    tested = {
        tid
        for pair in pairs[:MAX_PAIRS_PER_PASS]
        for tid in (pair.left.id, pair.right.id)
    }
    for row in rows:
        if row.id in tested and row.id not in conflicted_ids:
            _clear(row, out)

    await db.flush()
    if out.changed or out.skipped:
        log.info(
            "knowledge_conflicts_checked",
            connection_id=str(connection.id),
            considered=out.pairs_considered,
            executed=out.pairs_executed,
            conflicted=len(out.conflicted),
            cleared=len(out.cleared),
            skipped=len(out.skipped),
        )
    return out


async def _candidates(
    db: AsyncSession, connection: DatabaseConnection
) -> list[KnowledgeTemplateRow]:
    """Rows worth comparing: live, and meant to answer questions.

    `ARCHIVED` and `STALE` are excluded — one is out of use by a curator's
    decision and the other cannot run. `CONFLICTED` is deliberately **in**: it
    is how a resolved disagreement gets noticed. Role is filtered here for
    §1.3's reason: a `HELD_OUT` row exists to measure, and a conflict between
    two rows that never answer anything is not a fact about this product's
    answers.
    """
    result = await db.execute(
        select(KnowledgeTemplateRow).where(
            KnowledgeTemplateRow.connection_id == connection.id,
            KnowledgeTemplateRow.status.in_(
                (str(TemplateStatus.ACTIVE), str(TemplateStatus.CONFLICTED))
            ),
            KnowledgeTemplateRow.role == str(TemplateRole.RETRIEVABLE),
        )
    )
    return list(result.scalars().all())


async def _run(
    db: AsyncSession,
    settings: Settings,
    connection: DatabaseConnection,
    sql: str,
    connector: Any,
    snapshot: dict[str, Any],
) -> Any | None:
    """One statement, through the guard's own execution path. `None` on any
    failure — an error is never evidence of a conflict."""
    result = await execute_saved_sql(
        db,
        settings,
        sql=sql,
        connection=connection,
        owner_id=connection.owner_id,
        max_rows=COMPARE_ROW_CAP,
        connector=connector,
        snapshot=snapshot,
    )
    return result if result.status == "OK" else None


def _mark(
    by_id: dict[UUID, KnowledgeTemplateRow],
    pair: Pair,
    divergence: Divergence,
    out: ConflictResult,
) -> None:
    """Both rows, not one. Neither is presumed right.

    §4.7's pane offers *Keep the first / Keep the second / Edit both*, and it
    can only offer that if the system has not already picked a winner. The
    evidence is stored from each row's own point of view, so whichever the
    curator opens sees its own answer on the left.
    """
    left_row, right_row = by_id.get(pair.left.id), by_id.get(pair.right.id)  # type: ignore[arg-type]
    if left_row is None or right_row is None:
        return

    reason = (
        f"Two templates answer this differently — “{pair.right.question}” "
        f"disagrees. {divergence.summary}"
    )
    _apply(left_row, right_row.id, reason, divergence.as_dict(), out)

    mirrored = Divergence(
        differs=True,
        summary=divergence.summary,
        left_columns=divergence.right_columns,
        right_columns=divergence.left_columns,
        left_rows=divergence.right_rows,
        right_rows=divergence.left_rows,
    )
    _apply(
        right_row,
        left_row.id,
        f"Two templates answer this differently — “{pair.left.question}” "
        f"disagrees. {divergence.summary}",
        mirrored.as_dict(),
        out,
    )


def _apply(
    row: KnowledgeTemplateRow,
    other_id: UUID,
    reason: str,
    evidence: dict[str, Any],
    out: ConflictResult,
) -> None:
    row.status = str(TemplateStatus.CONFLICTED)
    row.status_reason = reason
    row.conflicts_with = sorted(
        {*(row.conflicts_with or []), other_id}, key=str
    )
    row.conflict_evidence = evidence
    row.last_conflict_check_at = utcnow()
    if row.id not in out.conflicted:
        out.conflicted.append(row.id)


def _clear(row: KnowledgeTemplateRow, out: ConflictResult) -> None:
    row.last_conflict_check_at = utcnow()
    if row.status != str(TemplateStatus.CONFLICTED):
        return
    row.status = str(TemplateStatus.ACTIVE)
    row.status_reason = ""
    row.conflicts_with = []
    row.conflict_evidence = {}
    out.cleared.append(row.id)


async def _clear_all(rows: list[KnowledgeTemplateRow], out: ConflictResult) -> None:
    for row in rows:
        _clear(row, out)


# ── the schedule ─────────────────────────────────────────────────────────
async def maintenance_once(settings: Settings) -> int:
    """One pass over every connection that has templates. Returns rows changed.

    Every connection, because a store rots on the customer's schedule and not
    on ours: nobody opens the Knowledge tab of the connection they stopped
    using, and that is exactly the one whose templates are answering questions
    with columns that moved.
    """
    from app.infra.db.session import get_sessionmaker

    changed = 0
    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(DatabaseConnection)
            .join(
                KnowledgeTemplateRow,
                KnowledgeTemplateRow.connection_id == DatabaseConnection.id,
            )
            .distinct()
        )
        connections = list(result.scalars().all())

        for connection in connections:
            try:
                out = await run_maintenance(session, settings, connection)
                changed += out.staleness.changed + out.conflicts.changed
            except Exception:
                # One connection's unreachable database must not stop the sweep
                # for every other connection. The next pass tries again.
                log.exception(
                    "knowledge_maintenance_failed",
                    connection_id=str(connection.id),
                )
                await session.rollback()
        await session.commit()
    return changed


async def maintenance_loop(settings: Settings) -> None:
    while True:
        # Slept first, deliberately: startup already re-validates nothing and
        # a fleet restarting together must not open a connector to every
        # customer database in the same second.
        await asyncio.sleep(settings.knowledge_maintenance_interval_seconds)
        try:
            changed = await maintenance_once(settings)
            if changed:
                log.info("knowledge_maintenance_swept", changed=changed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("knowledge_maintenance_loop_failed")


class KnowledgeMaintenanceExecutor:
    """On-demand maintenance, for the *Check for conflicts* button.

    The same trade `SemanticJobExecutor` makes and one size down: a pass is
    seconds to a minute, it holds no user-visible row while it runs, and the
    result is read from the templates themselves. So there is no job table —
    the button returns immediately, the pass runs, and the list refreshes to
    whatever it found. A process that dies mid-pass leaves the store exactly as
    it was, because every row this writes is idempotent against the next pass.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # One at a time per process. Two passes over one connection would open
        # two connectors to the customer's database to reach the same verdict.
        self._semaphore = asyncio.Semaphore(1)
        self._running: set[UUID] = set()

    def is_running(self, connection_id: UUID) -> bool:
        return connection_id in self._running

    async def submit(self, connection_id: UUID) -> bool:
        """Queue a pass. False when one is already queued for this connection."""
        if connection_id in self._running:
            return False
        self._running.add(connection_id)
        task = asyncio.create_task(
            self._run(connection_id), name=f"knowledge-maintenance:{connection_id}"
        )
        task.add_done_callback(lambda _: self._running.discard(connection_id))
        return True

    async def _run(self, connection_id: UUID) -> None:
        from app.infra.db.session import get_sessionmaker

        async with self._semaphore:
            try:
                async with get_sessionmaker()() as session:
                    connection = await session.get(DatabaseConnection, connection_id)
                    if connection is None:
                        return
                    await run_maintenance(session, self._settings, connection)
                    await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "knowledge_maintenance_job_failed",
                    connection_id=str(connection_id),
                )


__all__ = [
    "COMPARE_ROW_CAP",
    "MAX_PAIRS_PER_PASS",
    "ConflictResult",
    "KnowledgeMaintenanceExecutor",
    "MaintenanceResult",
    "detect_conflicts",
    "maintenance_loop",
    "maintenance_once",
    "run_maintenance",
]
