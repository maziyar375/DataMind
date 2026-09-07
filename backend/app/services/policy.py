"""Authorization as functions, not scattered role checks.

Row-level or column-level security later is a change in this module only.

**`can()` asks; `require()` asks and raises.** Both delegate to the
`Authorizer` port, which is where ownership, grants, teams, role scoped
privileges and wildcards are combined into one answer with a reason attached.

**`require()` is where the 404-versus-403 rule lives, and it lives here once.**
Getting that rule wrong turns every endpoint into an existence oracle, and
getting it inconsistent is worse than getting it uniformly wrong — a caller who
gets 404 from one route and 403 from another for the same resource has learned
that the resource exists. So it is one function, and §19.1's table is its
implementation:

| the caller holds | answer |
|---|---|
| nothing at all | **404** — indistinguishable from a typo, and audited as nothing |
| `describe` or more, but not enough | **403**, naming the privilege they need |

**`can_curate` is gone as of Phase 6, and its seven tests were rewritten rather
than deleted.** It approximated a reader/curator split with a settings flag
because there was no way to *grant* curation; now there is, and the
approximation is the privilege itself — `(knowledge, modify)`. `curation_admin_only`
went with it. The three functions before it (`can_read`, `can_write`,
`can_administer_users`) went in Phase 2, each with zero callers and an
`is_admin` arm.

What survives beside `can` and `require` is `owns` — a *fact*, legitimate only
where the answer is not being used to decide reach.
"""
from __future__ import annotations

from typing import Any

from app.core.context import RequestContext
from app.core.errors import ForbiddenError, NotFoundError
from app.domain.ports.authz import Authorizer, Decision, ResourceRef
from app.domain.value_objects.authz import (
    PRIVILEGE_MEANINGS,
    Privilege,
    ResourceType,
)


async def can(
    ctx: RequestContext,
    authz: Authorizer,
    ref: ResourceRef,
    privilege: Privilege,
) -> Decision:
    """May this principal do this to this thing? Delegates, and adds nothing.

    A thin function on purpose. It exists so that `services/` has one import to
    reach for and one name to grep, and so the answer arrives as a `Decision` —
    allowed *plus why* — rather than as a bare bool that an audit row cannot
    explain. Every scrap of logic lives in the authorizer; if this function ever
    grows an `if`, the rule it encodes belongs in `app/infra/authz/` where the
    other rules are and where the tests for them are.
    """
    return await authz.allowed(ctx, ref, privilege)


async def can_on(
    ctx: RequestContext,
    authz: Authorizer,
    type_: ResourceType,
    entity: Any,
    privilege: Privilege,
) -> Decision:
    """`can()` for a row already loaded — the common case in a service.

    Passing the row means the authorizer does not re-read what the caller has
    in memory. `ResourceRef.to` is the same thing spelled out; this is the
    version that reads well at a call site.
    """
    return await authz.allowed(ctx, ResourceRef.to(type_, entity), privilege)


def owns(ctx: RequestContext, resource: Any) -> bool:
    """Is this principal the row's owner? A **fact**, not a decision.

    Ownership is one of the five facts the authorizer combines; asking it here
    is legitimate only where the answer is not being used to decide reach —
    rendering an "owned by you" badge, say. Its last decision-making caller
    was `can_curate`, which Phase 6 retired. Everything else asks `can()` or
    `require()`.
    """
    return getattr(resource, "owner_id", None) == ctx.user_id


async def require(
    ctx: RequestContext,
    authz: Authorizer,
    ref: ResourceRef,
    privilege: Privilege,
) -> Decision:
    """`can()`, and raise the right error when the answer is no.

    **The one place the 404/403 rule is implemented.** See the module
    docstring for the table; the two branches below are it.

    The 403 message names the privilege *and what it means on this type*,
    read from `PRIVILEGE_MEANINGS` — the same table `GET …/actions` renders
    from, so the sentence in the refusal and the sentence beside the radio
    button in the share dialog cannot drift apart. "You need select on this
    connection: ask questions through it" is something a person can take to
    whoever owns it. "Forbidden" is not.

    Returns the `Decision` when it is yes, so a caller that wants `because` for
    an audit row has it without asking twice.
    """
    decision = await authz.allowed(ctx, ref, privilege)
    if decision:
        return decision

    # Nothing at all reaches them: the resource must be indistinguishable from
    # one that does not exist. Deliberately **not** audited — a 404 is
    # indistinguishable from a typo, and auditing it makes the log noise
    # (plan §19.1).
    held = await authz.privileges_on(ctx, ref)
    if not held:
        raise NotFoundError(_NOT_FOUND.get(ref.type, "Not found."))

    meaning = PRIVILEGE_MEANINGS[ref.type][privilege]
    raise ForbiddenError(
        f"This needs “{privilege}” on this {_NOUN[ref.type]}: {meaning[0].lower()}"
        f"{meaning[1:]} You hold "
        f"{', '.join(sorted(str(p) for p in held))}.",
        privilege=str(privilege),
        resource_type=str(ref.type),
        held=sorted(str(p) for p in held),
    )


#: What each type is called in a refusal. Written out rather than derived from
#: the enum, because `semantic_layer` reads badly in a sentence and "the
#: knowledge store" is what the UI calls it.
_NOUN: dict[ResourceType, str] = {
    ResourceType.CONNECTION: "data source",
    ResourceType.KNOWLEDGE: "knowledge store",
    ResourceType.SEMANTIC_LAYER: "semantic layer",
    ResourceType.LLM_CONFIG: "model configuration",
    ResourceType.DASHBOARD: "dashboard",
    ResourceType.REPORT: "report",
    ResourceType.CONVERSATION: "conversation",
    ResourceType.TEAM: "team",
}

#: The 404 sentence per type. Same wording the routes used before Phase 6, so
#: a client matching on it does not break — and deliberately the *same* whether
#: the row is missing or merely out of reach.
_NOT_FOUND: dict[ResourceType, str] = {
    ResourceType.CONNECTION: "Connection not found.",
    ResourceType.KNOWLEDGE: "Connection not found.",
    ResourceType.SEMANTIC_LAYER: "Connection not found.",
    ResourceType.LLM_CONFIG: "Model configuration not found.",
    ResourceType.DASHBOARD: "Dashboard not found.",
    ResourceType.REPORT: "Report not found.",
    ResourceType.CONVERSATION: "Conversation not found.",
    ResourceType.TEAM: "Team not found.",
}
