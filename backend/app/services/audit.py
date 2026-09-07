"""Who did what to this connection's knowledge, and when.

Phase 8 of `docs/learning-loop-plan.md`, and the table it writes to has been
sitting in the schema since migration `0001` with **nothing writing to it** —
`mvp2-plan.md` §D4 calls turning it on the best ratio in that document, for a
reason worth restating:

> The product's whole positioning is *"you decide what leaves your database."*
> Right now it cannot prove what left.

A store of business logic whose provenance nobody can establish is exactly the
failure mode Microsoft shipped and documented. A template answers questions on
the customer's behalf; the questions of *who taught it*, *who changed it*, and
*who withdrew it* have to be answerable from a table rather than from a
recollection.

**What this module is, and what it deliberately is not.**

It is a small, generic writer over `audit_logs`, used by the curation write
paths. It is **not** the whole of mvp2 §D4 — that also wants every question
recorded with the disclosure policy in force, the SQL that ran, the rows
returned, and what reached the model provider. Those belong to the ask path and
are that plan's scope, not this one's. This module is shaped so they arrive as
more `record()` calls and no new machinery.

**Three rules, and each one is a way this kind of log usually rots.**

1. **An audit row joins the caller's transaction; it never opens its own.**
   A log that can commit while the action it describes rolls back is a log that
   invents history. The consequence is accepted deliberately: a rolled-back
   curation write leaves no audit row, because it did not happen.
2. **Failing to log never fails the action.** A curator saving a template must
   not lose it to a full disk on the audit table. `record` catches and warns —
   the *opposite* posture to the guard, and right here for the same reason the
   guard's is right there: this observes, it does not authorise.
3. **`detail` carries identifiers and counts, never content.** No SQL text, no
   question text, no result rows, no key. An audit log that quietly became a
   second copy of the store is a second thing to secure, and the one place
   somebody would forget to. The row already carries the resource id; whatever
   it points at is where the content lives.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.logging import get_logger
from app.infra.db.models import AuditLog

log = get_logger(__name__)

SUCCESS = "SUCCESS"
DENIED = "DENIED"
FAILED = "FAILED"

#: The resource types this module writes about. Named constants rather than
#: string literals at each call site, because the value is what an admin
#: filters on and a typo makes a row invisible rather than wrong.
TEMPLATE = "knowledge_template"
REVIEW = "answer_feedback"
BENCHMARK_SET = "benchmark_set"
BENCHMARK_RUN = "benchmark_run"
CONNECTION = "database_connection"

#: Every curation action, in one place. `action` is `String(60)` on the row and
#: these are the whole vocabulary — an admin reading the log should be able to
#: enumerate what can appear in it without reading the routers.
TEMPLATE_CREATED = "knowledge.template.created"
TEMPLATE_UPDATED = "knowledge.template.updated"
TEMPLATE_ARCHIVED = "knowledge.template.archived"
STORE_REVALIDATED = "knowledge.store.revalidated"
EMBEDDINGS_CHANGED = "knowledge.embeddings.changed"
FEEDBACK_RECORDED = "knowledge.feedback.recorded"
REVIEW_RESOLVED = "knowledge.review.resolved"
BENCHMARK_CREATED = "knowledge.benchmark.created"
BENCHMARK_DELETED = "knowledge.benchmark.deleted"
BENCHMARK_RUN_QUEUED = "knowledge.benchmark.run"

#: **The ask itself**, with the disclosure policy that was in force for it.
#:
#: The remaining half of mvp2 §D4, and the sentence this product's positioning
#: rests on: *"you decide what leaves your database"* is only defensible if the
#: system can say, per question, what the decision **was**. Written at ask time
#: rather than read off the connection afterwards, because the policy can
#: change between one question and the next.
#:
#: It matters more since connections can be shared: the person who chose the
#: policy and the person asking are no longer necessarily the same person, and
#: the second may never have seen it.
ASK_RECORDED = "ask.recorded"

#: **The rest of the vocabulary, and where each word is defined.**
#:
#: The permission actions live beside the services that write them — the same
#: rule this module already follows for curation — because an action defined
#: away from its writer drifts from it. What lives *here* is the index: an
#: administrator reading the log should be able to enumerate everything that
#: can appear in it without reading the routers, and that is only true if one
#: file lists them all.
#:
#: ```
#: services/role_service.py         role.created · role.updated · role.deleted
#:                                  role.assigned · role.unassigned
#: services/team_service.py         team.created · team.renamed · team.deleted
#:                                  team.member.added · team.member.removed
#:                                  team.source.bound
#: services/service_user_service.py service_user.created · service_user.disabled
#:                                  service_user.deleted · service_user.privileged
#:                                  service_credential.issued
#:                                  service_credential.revoked
#:                                  service_credential.expired
#: services/grant_service.py        grant.created · grant.revoked
#:                                  grant.wildcard.created
#:                                  ownership.transferred · disclosure.changed
#: services/policy.py               access.denied
#: api/v1/llm_configs.py            llm_config.endpoint.changed
#: ```
#:
#: Phase 7 added `access.denied` — the producer `DENIED` had been waiting for
#: since migration `0001` — and `admin.self_granted`. Phase 8 adds
#: `llm_config.endpoint.changed`: the one action defined in a **router** rather
#: than a service, because the rule it records lives in the PATCH handler that
#: compares the endpoint before and after, and moving the word away from that
#: comparison would be moving it away from the only code that can decide it.

#: How much of a `detail` value survives. Generous for an identifier or a
#: status reason, mean enough that nobody is tempted to pass a statement.
MAX_DETAIL_CHARS = 500


async def record(
    db: AsyncSession,
    ctx: RequestContext,
    *,
    action: str,
    resource_type: str = "",
    resource_id: UUID | None = None,
    outcome: str = SUCCESS,
    detail: dict[str, Any] | None = None,
) -> AuditLog | None:
    """Write one audit row. Returns it, or `None` if it could not be written.

    Not flushed here: the row joins whatever transaction the caller is in, so
    it lands exactly when the action does and disappears with it if the action
    is refused by the database. That is the property that makes the log worth
    reading — rule 1 in the module docstring.
    """
    try:
        row = AuditLog(
            actor_user_id=ctx.user_id,
            actor_ip=(ctx.actor_ip or None),
            correlation_id=(ctx.correlation_id or None),
            action=action[:60],
            resource_type=resource_type[:60] or None,
            resource_id=resource_id,
            outcome=outcome[:20],
            detail=_clean(_delegation(ctx, detail)),
        )
        db.add(row)
        return row
    except Exception:
        # Rule 2. A curator must not lose a saved template to the audit table.
        log.warning("audit_write_failed", action=action)
        return None


def _delegation(ctx: RequestContext, detail: dict[str, Any] | None) -> dict[str, Any]:
    """`detail`, plus `delegated: true` when a worker acted for somebody.

    Added here rather than at the call sites because it is a property of the
    *context*, not of the action: the row already says who the action is
    attributed to, and this is the difference between "Sara clicked run" and
    "the scheduler ran Sara's report at 03:00". A log that cannot tell those
    apart cannot answer the only question anybody asks it.

    Absent — rather than `false` — on an ordinary request, so the flag reads as
    an exception in the log rather than as noise on every row.
    """
    if not ctx.delegated:
        return dict(detail or {})
    return {**(detail or {}), "delegated": True}


def _clean(detail: dict[str, Any]) -> dict[str, Any]:
    """Identifiers and counts, capped. Rule 3, enforced rather than trusted.

    Applied here rather than at each call site because there are ten call sites
    and it only takes one to turn this table into a second copy of the store —
    which would be a second thing to secure and the one nobody remembers.
    """
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if value is None or isinstance(value, bool | int | float):
            out[str(key)[:60]] = value
        elif isinstance(value, list | tuple):
            # Lengths and short id lists survive; a list of statements does not.
            out[str(key)[:60]] = [str(v)[:MAX_DETAIL_CHARS] for v in value][:20]
        else:
            out[str(key)[:60]] = str(value)[:MAX_DETAIL_CHARS]
    return out
