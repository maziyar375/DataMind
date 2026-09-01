"""Reading the audit log. Administrators only.

Phase 8 of `docs/learning-loop-plan.md`. The writing half is
`services/audit.py`; this is the half that makes the table worth having, and
it is deliberately small.

**Why a read endpoint at all**, when §3.9 only asks for the rows to be written:
the failure the ledger names is not *"the table is empty"*, it is *"this
product cannot answer who did what"*. A log nobody can read answers that
exactly as badly as an empty one. Thirty lines is the difference between a
table and a feature.

**Why administrators only**, when curation itself is open to a connection's
owner: an audit log is a record *about people*. It names who did what and from
where, which is the one thing in this product a curator has no operational need
to read about their colleagues — and `AdminDep` is the existing answer to
"who may see across users".

This is **not** the whole of [mvp2 §D4](../../../../docs/mvp2-plan.md), which
also wants every question recorded with the policy in force, the SQL that ran,
the rows returned, and what reached the model provider. Those are writes on the
ask path and belong to that plan. This endpoint reads whatever
`services/audit.py` has been asked to record, so they arrive here for free when
they arrive.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AdminDep, DbDep
from app.api.schemas import AuditEntry
from app.infra.db.models import AuditLog, User

router = APIRouter(prefix="/audit", tags=["audit"])

#: One page. The log is append-only and grows without bound, so there is no
#: "everything" to return — a caller that wants a window asks for one.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@router.get("", response_model=list[AuditEntry])
async def list_audit(
    ctx: AdminDep,
    db: DbDep,
    action: str | None = None,
    resource_id: UUID | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[AuditEntry]:
    """Recent audited actions, newest first.

    Two filters and no more: by `action` (what happened) and by `resource_id`
    (what it happened to). Those are the two questions somebody actually
    arrives with — *"who has been changing templates?"* and *"what happened to
    this one?"* — and a filter nobody uses is a query plan nobody has checked.

    The actor is returned as a **display name**, never an address, the same
    rule the review queue follows: an audit screen has no need of a personal
    identifier to answer either question.
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

    rows = list((await db.execute(statement)).scalars().all())
    names: dict[UUID, str] = {}
    for row in rows:
        if row.actor_user_id is not None and row.actor_user_id not in names:
            user = await db.get(User, row.actor_user_id)
            names[row.actor_user_id] = (user.display_name or "") if user else ""

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
