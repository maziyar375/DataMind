"""Authorization as functions, not scattered role checks.

Row-level or column-level security later is a change in this module only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.context import RequestContext

if TYPE_CHECKING:  # `Settings` is a pydantic model; the import is free at runtime
    from app.core.config import Settings


def owns(ctx: RequestContext, resource: Any) -> bool:
    return getattr(resource, "owner_id", None) == ctx.user_id


def can_read(ctx: RequestContext, resource: Any) -> bool:
    return owns(ctx, resource) or ctx.is_admin


def can_write(ctx: RequestContext, resource: Any) -> bool:
    return owns(ctx, resource)


def can_administer_users(ctx: RequestContext) -> bool:
    return ctx.is_admin


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
    the flag takes rights away and grants none: `_owned()` already scopes every
    knowledge endpoint to `owner_id == ctx.user_id`, so an admin cannot reach
    somebody else's connection either, and admin-only would have meant *the
    person who owns a connection cannot curate their own store*. That is not
    what D4 describes and it is not a security posture — it is a lockout.

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
    return ctx.is_admin or owns(ctx, resource)
