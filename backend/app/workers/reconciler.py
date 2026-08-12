"""The stale-run sweep, and the lock that stops every replica doing it at once.

The sweep itself is one `UPDATE` and is idempotent, so concurrent sweeps are
not a correctness problem in the "two writers corrupt a row" sense. The lock is
here for a narrower reason: the sweep's predicate is *"heartbeat older than
`run_stale_after_seconds`"*, and a run whose heartbeat is momentarily late is
indistinguishable from one whose worker died. One sweeper reaching that
conclusion is the design. N sweepers reaching it simultaneously, on N
schedules, multiplies the chance that a live run gets failed out from under a
user who is watching it stream — and each replica's `reconciler_loop` ticks on
its own timer, so with three replicas the effective sweep interval is a third
of the configured one.

Three details, each of which was wrong in an earlier draft of this file:

**`pg_try_advisory_lock` and not `pg_advisory_lock`.** A replica that cannot
get the lock has nothing to wait for: another replica is sweeping *now*, the
sweep is global rather than per-replica, and this one's next tick comes round
in `reconciler_interval_seconds` anyway. Waiting would queue a redundant sweep
behind a completed one.

**`_xact_` and not the session-scoped form.** A session-level advisory lock is
released by `pg_advisory_unlock` or by the connection going away — and neither
is something this code can promise. `reconcile_stale` commits, and SQLAlchemy
may return the connection to the pool at that point and take a different one
for the next statement, so an `unlock` paired with a `lock` in the same
`async with` can land on a *different backend* and quietly fail. The lock then
leaks on a pooled connection and the reconciler is silenced for as long as that
connection lives. That is the one failure of this design nothing else would
notice, because a reconciler that never runs looks exactly like one that keeps
finding nothing. The transaction-scoped form cannot leak: Postgres releases it
at commit or rollback, whichever happens, including on a crash.

**The lock and the sweep are one transaction.** That is what makes the release
automatic — `reconcile_stale`'s own commit is the end of the transaction that
holds the lock, so the lock is held for exactly the sweep and not one statement
longer.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: An arbitrary constant, fixed forever: `pg_try_advisory_lock` namespaces on
#: the integer alone, so this is the whole identity of "the run reconciler".
#: Any other advisory lock in this application must not reuse it.
RECONCILER_LOCK_KEY = 8_274_119_003_461_552


async def reconcile_once(settings: Settings) -> int:
    """Sweep, if no other replica is already sweeping. Returns rows failed."""
    from app.infra.db.session import get_sessionmaker
    from app.services.run_service import RunService

    async with get_sessionmaker()() as session:
        held = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": RECONCILER_LOCK_KEY},
        )
        if not held.scalar():
            log.debug("reconciler_skipped_locked")
            # Ends the transaction the `SELECT` opened, which is also what
            # drops the lock attempt. Not cosmetic: without it the session
            # holds an idle transaction open until it is garbage collected.
            await session.rollback()
            return 0
        # Deliberately inside the same transaction, and deliberately not
        # wrapped in `try/finally`: `reconcile_stale` commits, and that commit
        # is what releases the lock. A rollback on the way out of an exception
        # releases it just the same.
        count = await RunService(session, settings).reconcile_stale()

    if count:
        log.warning("runs_reconciled", count=count)
    return count


async def reconciler_loop(settings: Settings) -> None:
    while True:
        try:
            await reconcile_once(settings)
        except Exception:
            log.exception("reconciler_failed")
        await asyncio.sleep(settings.reconciler_interval_seconds)
