"""One place that turns a setting into an `Authorizer`.

Two callers need one and neither may hardcode a class: `api/deps.py`, once per
request, and `app/workers/`, once per unit of background work. A worker that
constructed `OwnerOnlyAuthorizer` directly would keep asking the old question
after Phase 6 flipped the setting — the scheduled report would answer *"not
shared"* while the same report opened in a browser answered *"shared"*, which
is the worst kind of authorization bug because both halves look right.

It lives in `infra` rather than in `deps` so `app.workers` can reach it without
importing FastAPI.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.ports.authz import Authorizer
from app.infra.authz.owner_only import OwnerOnlyAuthorizer


def build_authorizer(db: AsyncSession, settings: Settings) -> Authorizer:
    """The authorizer this installation is configured to use.

    `rbac` is Phase 6; naming it before it exists raises rather than silently
    falling back, because a deployment that asked for grants and got ownership
    would look like it was working.
    """
    if settings.authz_backend == "rbac":  # pragma: no cover - Phase 6
        raise NotImplementedError(
            "authz_backend='rbac' arrives in Phase 6; RbacAuthorizer does not "
            "exist yet. Leave AUTHZ_BACKEND unset or set it to 'owner_only'."
        )
    return OwnerOnlyAuthorizer(db)
