"""Who may start a deep analysis, and what one may spend on a connection.

Phase 8 of `docs/plans/deep-analysis-mode.md` — the governance half. The usage
subsystem *"measures, it does not enforce"*; this is the first thing in the
product that enforces, because an unbounded loop is exactly what a cap that
fails closed is for.

Three rules, each one a way a cap fails **open**:

1. **A connection's budget narrows the installation's ceiling and never widens
   it** (`DeepLimits.within`). `manage` on a connection is held by whoever
   created it; that is not somebody who decides what a deep run may cost here.
   Lowering the ceiling narrows every connection at once.
2. **A refusal is a refusal, not a smaller run.** A budget with a zero in it
   refuses to start, with a 403 and an audit row, rather than starting a run
   that plans nothing and writes an answer anyway.
3. **The budget is resolved once, when the run is created, and snapshotted onto
   it.** The executor reads the snapshot and nothing else — not the connection,
   and never `DeepBudget`'s defaults. A run claimed by another replica, taken
   over after a lapsed heartbeat, or executed after an operator narrowed the
   connection, spends exactly what it was started under; a DEEP run with no
   readable snapshot fails rather than run on a guess. That is what *"fails
   closed under load"* means here: no path through a busy system reaches a
   default.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import RequestContext
from app.core.errors import DeepRefusedError
from app.domain.value_objects import DEEP_LIMIT_FIELDS, DeepLimits
from app.domain.value_objects.authz import Capability
from app.infra.db.models import DatabaseConnection
from app.services import audit

#: An administrator changed a connection's deep budget. `detail` names each
#: bound that moved, from and to — numbers, never content.
DEEP_BUDGET_CHANGED = "deep.budget.changed"
#: A deep analysis was refused before it started: `reason` is `capability`
#: (the asker lacks `deep.run`) or the name of the bound that was zero.
DEEP_REFUSED = "deep.refused"

#: The sentence each refusal is told in. Addressed to the person who asked,
#: and naming who can change it — a refusal nobody can act on is a dead end.
_WHY = {
    "capability": (
        "Deep analysis needs the “deep.run” permission, which none of your "
        "roles carries. Ask an administrator to add it to one of them."
    ),
    "budget": (
        "Deep analysis is switched off on this data source: its deep budget "
        "allows no {bound}. Somebody with full access to the data source can "
        "raise it on its Policy tab."
    ),
}

_BOUND_WORDS = {
    "max_steps": "steps",
    "max_queries": "queries",
    "max_rows_total": "rows",
    "max_prompt_tokens": "prompt tokens",
    "deadline_seconds": "time",
}


def ceiling(settings: Settings) -> DeepLimits:
    """The installation's: no connection may allow more than this."""
    return DeepLimits.ceiling(settings.deep_deadline_seconds)


def limits_for(connection: DatabaseConnection, settings: Settings) -> DeepLimits:
    """What a deep run through this connection may spend, starting now.

    The stored budget clipped to the ceiling, or the ceiling where nothing is
    stored. A stored value that cannot be read raises `ValueError` — the caller
    refuses rather than falling back to the ceiling, which would be the widest
    answer available.
    """
    top = ceiling(settings)
    if connection.deep_budget is None:
        return top
    return DeepLimits.from_json(connection.deep_budget).within(top)


async def admit(
    db: AsyncSession,
    ctx: RequestContext,
    connection: DatabaseConnection,
    settings: Settings,
    *,
    conversation_id: UUID,
) -> DeepLimits:
    """May this person start a deep run on this connection — and on what budget?

    Returns the limits to snapshot onto the run. Otherwise records a
    `deep.refused` row and raises `DeepRefusedError`, which the route returns
    rather than raises so the row survives (`DeepRefusedError`'s docstring).

    Asked **after** the conversation and the connection have been authorized,
    so a refusal row is never written about a thread or a data source the
    asker cannot see — the audit log is not an existence oracle either.
    """
    if not ctx.can(Capability.DEEP_RUN):
        await _refuse(db, ctx, connection, conversation_id, reason="capability")
        raise DeepRefusedError(_WHY["capability"], reason="capability")

    try:
        limits = limits_for(connection, settings)
    except ValueError:
        # A damaged row is a refusal, named as one — never the ceiling.
        await _refuse(db, ctx, connection, conversation_id, reason="unreadable")
        raise DeepRefusedError(
            "This data source's deep budget cannot be read. Somebody with full "
            "access to it can set it again on its Policy tab.",
            reason="unreadable",
        ) from None

    bound = limits.refusal()
    if bound:
        await _refuse(db, ctx, connection, conversation_id, reason=bound)
        raise DeepRefusedError(
            _WHY["budget"].format(bound=_BOUND_WORDS[bound]), reason=bound
        )
    return limits


async def _refuse(
    db: AsyncSession,
    ctx: RequestContext,
    connection: DatabaseConnection,
    conversation_id: UUID,
    *,
    reason: str,
) -> None:
    await audit.record(
        db, ctx,
        action=DEEP_REFUSED,
        resource_type=audit.CONNECTION,
        resource_id=connection.id,
        outcome=audit.DENIED,
        detail={"reason": reason, "conversation_id": str(conversation_id)},
    )


async def set_budget(
    db: AsyncSession,
    ctx: RequestContext,
    connection: DatabaseConnection,
    limits: DeepLimits,
    settings: Settings,
) -> None:
    """Store a connection's budget, audited when it moved.

    The caller has already checked `manage` and that `limits` is within the
    ceiling; this refuses nothing. Storing the ceiling's own values is stored
    as numbers rather than NULL, on purpose: an administrator who typed *5
    steps* meant 5, and raising the installation's ceiling later must not
    quietly raise this connection with it.
    """
    was_default = connection.deep_budget is None
    before = _readable(connection.deep_budget, settings)
    after = limits.to_json()
    connection.deep_budget = after
    await db.flush()
    moved = {
        name: [before[name], after[name]]
        for name in DEEP_LIMIT_FIELDS
        if before[name] != after[name]
    }
    # Pinning the ceiling's own numbers is a change too: from here on, raising
    # the ceiling no longer raises this connection.
    if moved or was_default:
        await audit.record(
            db, ctx,
            action=DEEP_BUDGET_CHANGED,
            resource_type=audit.CONNECTION,
            resource_id=connection.id,
            detail={
                "from_default": was_default,
                **{name: f"{a} -> {b}" for name, (a, b) in moved.items()},
            },
        )


def _readable(raw: Any, settings: Settings) -> dict[str, int]:
    """The budget as it stood, for the audit row's *from* — best effort."""
    try:
        if raw is None:
            return ceiling(settings).to_json()
        return DeepLimits.from_json(raw).to_json()
    except ValueError:
        return dict.fromkeys(DEEP_LIMIT_FIELDS, -1)
