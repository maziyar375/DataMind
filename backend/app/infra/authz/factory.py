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
from app.infra.authz.rbac import RbacAuthorizer


def build_authorizer(db: AsyncSession, settings: Settings) -> Authorizer:
    """The authorizer this installation is configured to use.

    **`rbac` is the default from Phase 6** and `owner_only` remains a working
    rollback for one release. The rollback is a config flip rather than a
    migration in both directions: no grant row is *read* under `owner_only`,
    and running under it creates none — so flipping back narrows everybody to
    what they own and flipping forward restores every share exactly, with
    nothing to replay.

    An unknown value falls to `owner_only`, which is the narrower of the two.
    A typo in an environment variable should cost people access to things they
    were shared, not hand out access nobody granted.
    """
    if settings.authz_backend == "rbac":
        return RbacAuthorizer(db)
    return OwnerOnlyAuthorizer(db)
