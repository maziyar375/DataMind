"""The five facts, combined once. **This is where "may they?" is answered.**

`OwnerOnlyAuthorizer` knows one fact — you may act on a row if you own it. This
knows five, and the whole design is that they are combined *here* rather than
at two hundred call sites:

| fact | where it lives | reaches |
|---|---|---|
| ownership | `<table>.owner_id` | the whole lattice, always |
| a direct grant | `grants.user_id` | one resource, at one privilege |
| a team grant | `grants.team_id` + `ctx.team_ids` | the same, for everybody in the team |
| a role scoped privilege | `role_scoped_privileges` | **every** resource of a type |
| a wildcard grant | `grants.resource_id IS NULL` | the same, but as a row rather than a role |

Two properties do most of the work, and both are the reason the lattice was
written down in Phase 0:

**The lattice is expanded once, at read time, never at write.** A demand for
`select` becomes `privilege = ANY('{select,modify,delete,manage}')` — one array
comparison against an index — rather than four rows written when the grant was
made. That is why changing the lattice never needs a backfill, and why a
`modify` holder passes a `select` check without anybody having remembered to
say so.

**`visible` short-circuits before it builds anything.** Step 0 asks whether a
wildcard grant or a role scoped privilege already covers *(type, privilege)*.
If one does the answer is `Everything`, which `restrict` turns into **no clause
at all** — so the administrator case, and the Knowledge Manager case, cost one
small indexed read and then nothing. Only when no wildcard applies is the union
subquery built, and it is then composed into the caller's own `SELECT`: one
round trip, one plan, and `LIMIT`/`OFFSET` that count rows the caller can
actually see.

**What this deliberately does not have** is an administrator arm. An
administrator does not silently reach another person's connection; they reach
it through an explicit, audited self-grant (plan decision 14, Phase 7). A
`ctx.is_admin` here would be a back door with no row behind it, and the entire
point of putting the answer in one place is that the one place can be read.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.domain.ports.authz import (
    NOTHING,
    Decision,
    Everything,
    ResourceRef,
    Subquery,
    Visible,
)
from app.domain.value_objects.authz import (
    ALL_PRIVILEGES,
    Privilege,
    ResourceType,
    satisfying,
)
from app.infra.authz.owner_only import _OWNED_TABLES
from app.infra.db import models

#: The words `Decision.because` can carry from this authorizer. They go into
#: `audit_logs.detail` and into the permission explainer, so they are
#: identifiers rather than sentences — the UI writes the sentence, and a log
#: full of prose is a log nobody can filter.
OWNER = "owner"
DIRECT = "direct"
VIA_TEAM = "via_team"
VIA_ROLE = "via_role"
WILDCARD = "wildcard"


class RbacAuthorizer:
    """`Authorizer` over ownership, grants, teams, roles and wildcards.

    Holds a session and nothing else: no cache, no per-request memo. Two
    reasons, and the second is the one that matters. The cheap one is that a
    request is short and the reads are indexed. The real one is that a cache
    keyed on anything is a cache that can be stale, and the property this whole
    design is sold on — *"revoking access takes effect on the next request"* —
    is a property of reading the database every time.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── one thing ────────────────────────────────────────────────────────
    async def allowed(
        self, ctx: RequestContext, ref: ResourceRef, privilege: Privilege
    ) -> Decision:
        """May this principal do this to **this** thing?

        Ownership first, because it is the commonest answer and it is free when
        the caller passed the row — which every `GET /x/{id}` has by the time
        it decides whether to return it. Then the wildcard arms, which are one
        indexed read each and answer for a whole type. Then the grant lookup.
        """
        held = await self._privileges(ctx, ref, wanted=privilege)
        if privilege in held.privileges:
            return Decision(True, held.because)
        return Decision(False, held.because)

    async def allowed_many(
        self, ctx: RequestContext, pairs: Sequence[tuple[ResourceRef, Privilege]]
    ) -> list[Decision]:
        """The same question about several things.

        Sequential rather than one clever query, and that is a deliberate
        limit rather than an oversight: the callers are a dashboard's tiles
        against their own connections — a handful — and each answer needs its
        own `because` for the placeholder that renders when it is no. A list
        endpoint asking this in a loop is the anti-pattern `visible` exists to
        prevent, and `make authz-check` is what notices.
        """
        return [await self.allowed(ctx, ref, privilege) for ref, privilege in pairs]

    async def privileges_on(
        self, ctx: RequestContext, ref: ResourceRef
    ) -> frozenset[Privilege]:
        """Everything they hold here — what `GET …/actions` renders from.

        The UI draws every affordance from this answer, so it must be the same
        computation `allowed` performs rather than a second one that agrees
        today. It literally is: both call `_privileges`.
        """
        return (await self._privileges(ctx, ref)).privileges

    # ── many things ──────────────────────────────────────────────────────
    async def visible(
        self, ctx: RequestContext, type_: ResourceType, privilege: Privilege
    ) -> Visible:
        """Which resources of this type may they act on, as a composable shape.

        **Step 0 first, in Python, before any SQL is built.** If a wildcard
        grant or a role scoped privilege covers `(type_, privilege)`, the
        answer is `Everything` — no clause, no join, no subquery for the
        planner to unwrap. That is the administrator case and the Knowledge
        Manager case, and it is the common one on the screens that list a lot.

        Otherwise, the union of §18.3: the ids this principal owns, plus the
        ids any grant reaching them names. Returned as a `Subquery` the caller
        folds into its own `SELECT`, never as a materialised list — a list
        would be the pagination bug the port exists to prevent.
        """
        satisfied = satisfying(privilege)
        by_role, by_grant = await self._wildcards(ctx, type_)
        because = tuple(
            word
            for word, held in ((VIA_ROLE, by_role), (WILDCARD, by_grant))
            if held & satisfied
        )
        if because:
            return Everything(because)

        table = _OWNED_TABLES.get(type_)
        granted = self._granted_ids(ctx, type_, satisfied)
        if table is None:
            # A type with no owner column — `TEAM` today. Grants still reach
            # it; ownership does not exist for it, so there is no arm to union.
            return Subquery(granted)
        # `union`, not `union_all`: a resource somebody both owns and was
        # granted appears once, which matters because the caller composes this
        # into an `IN (...)` whose row count it may also be paginating.
        return Subquery(
            select(table.id).where(table.owner_id == ctx.user_id).union(granted)
        )

    # ── the one computation both public answers use ──────────────────────
    async def _privileges(
        self, ctx: RequestContext, ref: ResourceRef, *, wanted: Privilege | None = None
    ) -> _Held:
        """Every privilege this principal holds on this resource, with the path.

        Three reads at most, whatever is asked: the owner (free when the caller
        passed the row), the wildcards, and the grants. `wanted` narrows the
        wildcard question to one satisfying set so a yes/no can return before
        the grant read; it never narrows the *answer*, because one query
        returning every privilege on a row costs no more than one returning a
        subset.
        """
        # 1. Ownership — the whole lattice, and free when the row is in hand.
        owner_id = await self._owner_of(ref)
        if owner_id is not None and owner_id == ctx.user_id:
            return _Held(ALL_PRIVILEGES, (OWNER,))

        because: list[str] = []
        held: set[Privilege] = set()

        # 2. Wildcards: a role scoped privilege, or a grant with no
        #    `resource_id`, over this whole type. Two reads, not two per
        #    privilege — `_wildcards` returns what is held and the paths.
        by_role, by_grant = await self._wildcards(ctx, ref.type)
        if by_role:
            because.append(VIA_ROLE)
        if by_grant:
            because.append(WILDCARD)
        held |= _expand(by_role | by_grant)
        if wanted is not None and wanted in held:
            return _Held(frozenset(held), tuple(because))

        # 3. Grants naming this resource, direct or through a team. One query
        #    returning every privilege on the row, expanded through the lattice
        #    in Python — the set is at most five words.
        rows = await self._db.execute(
            select(models.Grant.privilege, models.Grant.team_id).where(
                models.Grant.resource_type == str(ref.type),
                models.Grant.resource_id == ref.id,
                self._reaches(ctx),
            )
        )
        for privilege, team_id in rows.all():
            granted = _known(privilege)
            if granted is None:
                continue
            held |= _expand({granted})
            word = VIA_TEAM if team_id is not None else DIRECT
            if word not in because:
                because.append(word)

        return _Held(frozenset(held), tuple(because))

    # ── the two reads ────────────────────────────────────────────────────
    async def _wildcards(
        self, ctx: RequestContext, type_: ResourceType
    ) -> tuple[frozenset[Privilege], frozenset[Privilege]]:
        """What reaches **every** resource of this type, by role and by grant.

        Two sources, deliberately kept apart in the answer: a **role** scoped
        privilege and a **wildcard grant**. They have identical effect and
        completely different provenance, and an access review that could not
        tell *"because of the Knowledge Manager role"* from *"because somebody
        made a wildcard grant on 3 March"* would be answering a question nobody
        asked.

        Two queries whatever is being asked — never one per privilege. Each is
        an index scan over a table that holds a handful of rows in a healthy
        installation (`ix_grants_wildcard` is partial for exactly this).
        """
        role_rows = await self._db.execute(
            select(models.RoleScopedPrivilege.privilege)
            .join(
                models.RoleAssignment,
                models.RoleAssignment.role_id == models.RoleScopedPrivilege.role_id,
            )
            .where(
                models.RoleScopedPrivilege.resource_type == str(type_),
                self._role_reaches(ctx),
            )
        )
        grant_rows = await self._db.execute(
            select(models.Grant.privilege).where(
                models.Grant.resource_type == str(type_),
                models.Grant.resource_id.is_(None),
                self._reaches(ctx),
            )
        )
        return (
            frozenset(filter(None, (_known(p) for p in role_rows.scalars()))),
            frozenset(filter(None, (_known(p) for p in grant_rows.scalars()))),
        )

    def _granted_ids(
        self,
        ctx: RequestContext,
        type_: ResourceType,
        satisfied: frozenset[Privilege],
    ) -> Select[tuple[UUID]]:
        """`SELECT resource_id FROM grants …` — arm (2)(3) of §18.3.

        Not awaited: it is a statement, unioned into `visible`'s answer and
        executed by the caller as part of its own query. Building it here and
        running it there is the entire reason `Visible` is a shape rather than
        a list.
        """
        return select(models.Grant.resource_id).where(
            models.Grant.resource_type == str(type_),
            models.Grant.privilege.in_(sorted(str(p) for p in satisfied)),
            models.Grant.resource_id.is_not(None),
            self._reaches(ctx),
        )

    @staticmethod
    def _reaches(ctx: RequestContext) -> Any:
        """*"this grant reaches this principal"* — the two arms, as one clause.

        Direct **or** through a team, `OR`ed inside one `WHERE` rather than
        unioned by two round trips, exactly as `RoleService.resolve_capabilities`
        does it. A principal in no teams degrades to the direct arm alone, which
        is what it should mean.
        """
        direct = models.Grant.user_id == ctx.user_id
        if not ctx.team_ids:
            return direct
        return or_(direct, models.Grant.team_id.in_(ctx.team_ids))

    @staticmethod
    def _role_reaches(ctx: RequestContext) -> Any:
        """The same two arms over `role_assignments`."""
        direct = models.RoleAssignment.user_id == ctx.user_id
        if not ctx.team_ids:
            return direct
        return or_(direct, models.RoleAssignment.team_id.in_(ctx.team_ids))

    async def _owner_of(self, ref: ResourceRef) -> UUID | None:
        """Who owns the referenced row, or `None` if nobody or no such row.

        The same read `OwnerOnlyAuthorizer` performs, and for the two derived
        types it reads `database_connections` — a knowledge store's owner *is*
        its connection's owner, and its resource id *is* the connection's id.

        `None` collapses three distinct situations (the row is gone, the type
        has no owner column, the owner is null) because all three mean the same
        thing to this method: ownership does not answer, so ask the other four
        facts. The 404-versus-403 distinction is the API edge's job.
        """
        if ref.entity is not None:
            owner = getattr(ref.entity, "owner_id", None)
            return owner if isinstance(owner, UUID) else None

        table = _OWNED_TABLES.get(ref.type)
        if table is None:
            return None
        stmt: Select[tuple[UUID]] = select(table.owner_id).where(table.id == ref.id)
        return (await self._db.execute(stmt)).scalar_one_or_none()


def _known(word: str) -> Privilege | None:
    """The `Privilege` this word names, or `None`.

    Open in the row, closed in code — the same bargain `role_capabilities`
    makes. A word this build does not know grants **nothing**, which is the
    fail-closed reading, and the warning belongs to `role_service`, which
    already emits one per unknown word rather than one per row.
    """
    try:
        return Privilege(word)
    except ValueError:
        return None


def _expand(granted: frozenset[Privilege] | set[Privilege]) -> set[Privilege]:
    """Everything these held privileges answer for — the lattice, applied once.

    A `manage` grant confers `delete`, `modify`, `select` and `describe`
    without four rows having been written, and that is the whole reason the
    lattice exists as data rather than as four `if`s per call site. Applied at
    **read** time, so changing the lattice never needs a backfill.
    """
    return {
        implied
        for implied in ALL_PRIVILEGES
        if any(word in satisfying(implied) for word in granted)
    }


class _Held:
    """A privilege set and the paths that produced it. Internal to this module.

    A tiny class rather than a tuple because both fields are read by name at
    three call sites, and `held[0]` at any of them would be the kind of line
    that gets the two the wrong way round exactly once.
    """

    __slots__ = ("because", "privileges")

    def __init__(self, privileges: frozenset[Privilege], because: tuple[str, ...]):
        self.privileges = privileges
        self.because = because


#: Re-exported so a caller that wants "nothing" does not have to import the
#: port to say so. Same object, so an identity check still works.
__all__ = ["NOTHING", "RbacAuthorizer"]
