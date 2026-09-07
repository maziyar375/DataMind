"""Reading the audit log. Administrators only.

Phase 8 of `docs/learning-loop-plan.md`. The writing half is
`services/audit.py`; this is the half that makes the table worth having, and
it is deliberately small.

**Why a read endpoint at all**, when §3.9 only asks for the rows to be written:
the failure the ledger names is not *"the table is empty"*, it is *"this
product cannot answer who did what"*. A log nobody can read answers that
exactly as badly as an empty one. Thirty lines is the difference between a
table and a feature.

**Why `audit.read`**, when curation itself is open to a connection's owner: an
audit log is a record *about people*. It names who did what and from where,
which is the one thing in this product a curator has no operational need to
read about their colleagues. It is a capability rather than a role so that an
**Auditor** — who may read this and change nothing anywhere — is expressible
without also being an administrator, which is the whole of requirement 2.

**Phase 7 made this the screen it was always shaped to be.** The table now
holds the whole permission story — who granted what to whom, who transferred
what, who changed a disclosure policy, who gave themselves access, and
**what was refused** — so the filters below stopped being a nicety. Two filters
over a table of template edits is fine; two filters over a table that also
holds every denial in the installation is a screen nobody can use.

The `DENIED` outcome finally has a producer (`services/policy.require`), which
is why `outcome` is a filter: *"what has been refused, and to whom"* is the
question an audit log exists for, and it was unanswerable here until there were
denials to find.

One half of [mvp2 §D4](../../../../docs/mvp2-plan.md) also landed: every ask
now records the **disclosure policy in force** for it, under `ask.recorded`.
What is still not here is the SQL that ran, the rows returned, and what reached
the model provider — those live on the run, which this row points at.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AuditReadDep, DbDep
from app.api.schemas import AuditEntry
from app.infra.db.models import AuditLog, User

router = APIRouter(prefix="/audit", tags=["audit"])

#: One page. The log is append-only and grows without bound, so there is no
#: "everything" to return — a caller that wants a window asks for one.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@router.get("", response_model=list[AuditEntry])
async def list_audit(
    ctx: AuditReadDep,
    db: DbDep,
    action: str | None = None,
    resource_id: UUID | None = None,
    outcome: str | None = None,
    resource_type: str | None = None,
    actor: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
    before: datetime | None = None,
) -> list[AuditEntry]:
    """Recent audited actions, newest first.

    **Seven filters, and each is a question somebody arrives with.** The
    original two — `action` and `resource_id` — answered *"who has been
    changing templates?"* and *"what happened to this one?"*, which was the
    whole surface when this table held curation edits. Phase 7 put every
    permission change and **every denial** in here, and five more questions
    came with them:

    * `outcome=DENIED` — *"what has been refused?"* The one this table existed
      for and could not answer until denials had a producer.
    * `resource_type` — *"what has been happening to connections?"*
    * `actor` — *"what has this person been doing?"*, by id rather than by
      name: two people can share a display name, and an audit filter that
      matched both would be worse than one that matched neither.
    * `since` / `until` — the range somebody has in mind when they are looking
      into something that happened on a particular afternoon.

    **Pagination is keyset, not offset.** `before` takes the `at` of the last
    row on the previous page, so paging through a log that is being appended to
    while you read it never skips a row or shows one twice — which `OFFSET`
    does, on exactly the table where it would be least noticeable and most
    misleading. Both indexes (`actor_user_id, at` and `action, at`) end in `at`
    and already serve it.

    The actor is returned as a **display name**, never an address, the same
    rule the review queue follows: an audit screen has no need of a personal
    identifier to answer any of these questions.
    """
    statement = (
        select(AuditLog)
        .order_by(AuditLog.at.desc())
        .limit(max(1, min(limit, MAX_LIMIT)))
    )
    if action:
        statement = statement.where(AuditLog.action == action)
    if resource_id is not None:
        statement = statement.where(AuditLog.resource_id == resource_id)
    if outcome:
        statement = statement.where(AuditLog.outcome == outcome.upper()[:20])
    if resource_type:
        statement = statement.where(AuditLog.resource_type == resource_type)
    if actor is not None:
        statement = statement.where(AuditLog.actor_user_id == actor)
    if since is not None:
        statement = statement.where(AuditLog.at >= since)
    if until is not None:
        statement = statement.where(AuditLog.at <= until)
    if before is not None:
        # Strictly less than, so the row that ended the previous page is not
        # the row that starts this one.
        statement = statement.where(AuditLog.at < before)

    rows = list((await db.execute(statement)).scalars().all())
    names = await _actor_names(db, {r.actor_user_id for r in rows if r.actor_user_id})

    return [
        AuditEntry(
            at=row.at,
            actor=names.get(row.actor_user_id, "") if row.actor_user_id else "",
            actor_ip=row.actor_ip or "",
            action=row.action,
            resource_type=row.resource_type or "",
            resource_id=row.resource_id,
            outcome=row.outcome,
            detail=row.detail or {},
        )
        for row in rows
    ]


async def _actor_names(db, ids: set[UUID]) -> dict[UUID, str]:
    """Display names for a page of rows, in **one** query.

    It was one `db.get` per row, which on a full page was a hundred round trips
    to draw one table — invisible while the log held a handful of template
    edits and no longer invisible now it holds every denial in the
    installation.

    A name, never an address, and never the id: an audit screen answers "who
    did this" with something a person recognises, and an email is a personal
    identifier the screen has no need of.
    """
    if not ids:
        return {}
    rows = await db.execute(
        select(User.id, User.display_name).where(User.id.in_(ids))
    )
    return {row[0]: (row[1] or "") for row in rows.all()}


@router.get("/actions", response_model=list[str])
async def list_actions(ctx: AuditReadDep, db: DbDep) -> list[str]:
    """Every action word that actually appears in this installation's log.

    Served rather than hardcoded in the SPA, for the same reason the capability
    catalog is: the vocabulary is closed in the backend and grows a phase at a
    time, and a filter dropdown shipping its own copy would offer a word that
    matches nothing — or, worse, miss one that does.

    Read from the rows rather than from the constants, so the list is what is
    *there*: a filter offering `grant.revoked` in an installation where nobody
    has ever revoked anything is a filter that returns an empty screen and
    teaches somebody the log is broken.
    """
    rows = await db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))
    return list(rows.scalars())
