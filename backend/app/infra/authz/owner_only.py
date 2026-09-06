"""Today's rule, behind today's port. **This changes no behaviour.**

DataMind's authorization rule right now is one line repeated two hundred and
thirteen times: *you may act on a row if you own it*. This class is that line,
and nothing else — so routing every call site through the port is provably
behaviour-preserving, and the interesting change (grants, roles, teams) is a
one-line swap of implementation rather than a rewrite of two thousand-line
services.

Two properties are deliberate and worth stating, because both look like bugs
until you know they are the point:

* **The privilege is ignored.** Ownership confers the *whole* lattice, so an
  owner holds `manage` and a non-owner holds nothing; there is no privilege for
  which the answer differs. `RbacAuthorizer` is the first implementation for
  which `privilege` matters.
* **`is_admin` is not consulted.** An administrator does not today reach
  another user's dashboard, connection or report — `_owned()` scopes every one
  of those endpoints to `owner_id`, and `policy.can_read`'s admin arm has no
  callers. Adding an admin arm here would be a behaviour change smuggled into a
  refactor, and the plan puts administrator reach behind an explicit, audited
  self-grant instead (§0.4 decision 14).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.domain.ports.authz import (
    NOTHING,
    Decision,
    ResourceRef,
    Subquery,
    Visible,
)
from app.domain.value_objects.authz import ALL_PRIVILEGES, Privilege, ResourceType
from app.infra.db import models

#: Which table carries the `owner_id` for each resource type.
#:
#: The two derived types map to `database_connections` on purpose: a knowledge
#: store's owner *is* its connection's owner, and its resource id is the
#: connection's id, so both arms of the question read the same row.
#:
#: `TEAM` has no entry because teams do not exist until Phase 4. Asking about
#: one before then is not an error — it is a resource nobody owns, so the
#: answer is no and the visible set is empty, which is exactly the truth.
_OWNED_TABLES: dict[ResourceType, type[Any]] = {
    ResourceType.CONNECTION: models.DatabaseConnection,
    ResourceType.KNOWLEDGE: models.DatabaseConnection,
    ResourceType.SEMANTIC_LAYER: models.DatabaseConnection,
    ResourceType.LLM_CONFIG: models.LlmConfig,
    ResourceType.DASHBOARD: models.Dashboard,
    ResourceType.REPORT: models.Report,
    ResourceType.CONVERSATION: models.Conversation,
}

#: The one word `Decision.because` can carry under this policy.
_OWNER = ("owner",)


class OwnerOnlyAuthorizer:
    """`Authorizer` whose only fact is ownership.

    The session is optional so the class can be constructed — and unit
    tested — with the row already in hand. Every method that needs a row it was
    not given will load one, and raises if it has no session to load it with;
    that is a programming error, not a denial, and failing loudly beats
    returning `False` for a question that was never actually asked.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self._db = db

    # ── one thing ────────────────────────────────────────────────────────
    async def allowed(
        self, ctx: RequestContext, ref: ResourceRef, privilege: Privilege
    ) -> Decision:
        owner_id = await self._owner_of(ref)
        if owner_id is not None and owner_id == ctx.user_id:
            return Decision(True, _OWNER)
        return Decision(False)

    async def allowed_many(
        self, ctx: RequestContext, pairs: Sequence[tuple[ResourceRef, Privilege]]
    ) -> list[Decision]:
        return [await self.allowed(ctx, ref, privilege) for ref, privilege in pairs]

    async def privileges_on(
        self, ctx: RequestContext, ref: ResourceRef
    ) -> frozenset[Privilege]:
        """The full lattice for an owner, nothing for anybody else.

        Ownership is not a grant and is not one privilege: it confers all five.
        """
        owner_id = await self._owner_of(ref)
        if owner_id is not None and owner_id == ctx.user_id:
            return ALL_PRIVILEGES
        return frozenset()

    # ── many things ──────────────────────────────────────────────────────
    async def visible(
        self, ctx: RequestContext, type_: ResourceType, privilege: Privilege
    ) -> Visible:
        """`SELECT id FROM <table> WHERE owner_id = :actor`, as a subquery.

        A `Subquery` rather than a set of ids because the caller folds it into
        its own `SELECT` — `.where(Dashboard.id.in_(subq))` — and gets one
        round trip and correct pagination. Materialising the ids here would be
        the exact anti-pattern the port exists to prevent.

        `satisfying(privilege)` is not consulted: under this policy an owner
        holds every privilege and a non-owner holds none, so the lattice has
        nothing to expand. It is named in the signature because the *port* is
        what the rest of the codebase is written against, and Phase 6 fills it
        in without touching a single caller.
        """
        table = _OWNED_TABLES.get(type_)
        if table is None:
            return NOTHING
        return Subquery(select(table.id).where(table.owner_id == ctx.user_id))

    # ── the one read ─────────────────────────────────────────────────────
    async def _owner_of(self, ref: ResourceRef) -> UUID | None:
        """Who owns the referenced row, or `None` if nobody or no such row.

        `None` is returned for three distinct situations that all mean the same
        thing to a caller — the row is gone, the type has no owner column, or
        the row's owner is null — and collapsing them here is deliberate: an
        authorizer answers *may they*, and every one of the three answers no.
        The 404-versus-403 distinction is the API edge's job (plan §19.1), and
        it has the row in hand when it makes it.
        """
        if ref.entity is not None:
            owner = getattr(ref.entity, "owner_id", None)
            return owner if isinstance(owner, UUID) else None

        table = _OWNED_TABLES.get(ref.type)
        if table is None:
            return None
        if self._db is None:
            raise RuntimeError(
                "OwnerOnlyAuthorizer was constructed without a session and asked "
                f"about {ref.type} {ref.id} without the row. Pass "
                "ResourceRef.to(type, entity) or give the authorizer a session."
            )
        stmt: Select[tuple[UUID]] = select(table.owner_id).where(table.id == ref.id)
        return (await self._db.execute(stmt)).scalar_one_or_none()
