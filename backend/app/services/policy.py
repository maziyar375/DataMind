"""Authorization as functions, not scattered role checks.

Row-level or column-level security later is a change in this module only.
"""
from __future__ import annotations

from typing import Any

from app.core.context import RequestContext


def owns(ctx: RequestContext, resource: Any) -> bool:
    return getattr(resource, "owner_id", None) == ctx.user_id


def can_read(ctx: RequestContext, resource: Any) -> bool:
    return owns(ctx, resource) or ctx.is_admin


def can_write(ctx: RequestContext, resource: Any) -> bool:
    return owns(ctx, resource)


def can_administer_users(ctx: RequestContext) -> bool:
    return ctx.is_admin
