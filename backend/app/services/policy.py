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


def can_curate(ctx: RequestContext, settings: Settings) -> bool:
    """Who may write connection knowledge — templates, reviews, resolutions.

    Today: anyone signed in. The product is single-player and the highest-value
    correction comes from the person who knew the answer; making curation
    admin-only now would mean the one user who can fix a wrong answer is
    usually not the one who saw it.

    When user management lands, one env var makes this admin-only. Every write
    call site already asks this function, so nothing else moves — which is the
    whole reason it is a function here rather than an `is_admin` check scattered
    across the endpoints.
    """
    if settings.curation_admin_only:
        return ctx.is_admin
    return True
