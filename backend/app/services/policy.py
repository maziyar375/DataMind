"""Authorization as functions, not scattered role checks.

Row-level or column-level security later is a change in this module only.

**`can()` is the one that matters from here on.** It asks the `Authorizer`
port, which is where ownership, grants, teams and role scoped privileges are
combined into one answer with a reason attached.

Three functions that used to sit below it — `can_read`, `can_write` and
`can_administer_users` — are gone as of Phase 2. Not tidying: each had **zero
callers** anywhere in `app/` or `tests/`, so deleting them changed no
behaviour, and each carried an `is_admin` arm that would otherwise have to be
explained to the authorization gate for the rest of the plan. What survives is
`owns` — a fact, used by exactly one caller — and `can_curate`, which is a
policy about curation rather than a question about reach.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.context import RequestContext
from app.domain.ports.authz import Authorizer, Decision, ResourceRef
from app.domain.value_objects.authz import Privilege, ResourceType

if TYPE_CHECKING:  # `Settings` is a pydantic model; the import is free at runtime
    from app.core.config import Settings


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
    is legitimate only where the answer is not being used to decide reach.
    Today that is one caller: `can_curate`, below. Everything else asks
    `can()`.
    """
    return getattr(resource, "owner_id", None) == ctx.user_id


def can_curate(
    ctx: RequestContext, settings: Settings, resource: Any = None
) -> bool:
    """Who may write connection knowledge — templates, reviews, resolutions.

    **Phase 8 flipped `curation_admin_only` to `true`.** Curation writes
    business logic that answers questions on other people's behalf, so it is a
    privileged act by default now that user management exists to express the
    privilege. Every write call site already asked this function, which is the
    whole reason it is a function here rather than an `is_admin` check
    scattered across the endpoints — the flip moved one line in `config.py`.

    **The owner of the connection is the other legitimate curator, and adding
    that is what makes the flip correct rather than merely done.** Without it
    the flag takes rights away and grants none: the knowledge routes already
    ask the authorizer before they get here, so an admin cannot reach somebody
    else's connection either, and admin-only would have meant *the person who
    owns a connection cannot curate their own store*. That is not what D4
    describes and it is not a security posture — it is a lockout.

    So the rule is **administrator, or the owner of the thing being curated**.
    Today those two are the only people who can reach a connection at all, so
    the flag changes nothing that anyone can observe. It starts mattering the
    moment [mvp2 §D1](../../../docs/mvp2-plan.md) lands and a connection can be
    *shared*: a reader granted access to somebody's connection may then ask it
    questions and may not rewrite what it has been taught, which is precisely
    the protection D4 wants and the reason to have the flag on before sharing
    exists rather than after.

    `resource` is the connection (or anything carrying `owner_id`). Omitting it
    asks the strict question — administrator only — because a caller with no
    resource in hand cannot establish ownership, and the fail-closed reading of
    "I don't know who owns this" is *no*.
    """
    if not settings.curation_admin_only:
        return True
    return ctx.is_admin or owns(ctx, resource)  # authz-ok: retires in Phase 3
