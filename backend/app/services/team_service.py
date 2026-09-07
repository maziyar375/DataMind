"""Teams: who is in one, what one holds, and why deleting one can fail.

A team is a named set of principals and, separately, a **resource** — which is
what lets a team lead exist without `team.manage` over every team in the
installation: they hold `(team, modify)` on theirs.

Three things here are the whole reason this is a service rather than four
routes doing `db.add`:

* **Deleting a team that holds role assignments is refused, naming them.**
  Metabase's *"reassigned to All Users"* is the anti-pattern — a silent
  widening at the moment somebody was trying to narrow — and a bare
  `IntegrityError` is the version of that refusal nobody can act on.
* **Rebinding a team to an external group is its own audited operation**, not a
  field on the edit form. Changing `(provider_id, source_id)` redirects which
  external group's members flow into a set of permissions; that is a different
  act from renaming, and an audit log recording it as `team.renamed` would be
  lying by omission.
* **Membership resolution is one query per request** and lives beside
  capability resolution, because they are read together on every authenticated
  call.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.infra.db.models import Role, RoleAssignment, Team, TeamMember, User
from app.services import audit

log = get_logger(__name__)

#: The six team actions, in one place — an administrator reading the log should
#: be able to enumerate what can appear in it without reading the routers.
TEAM = "team"
TEAM_CREATED = "team.created"
TEAM_RENAMED = "team.renamed"
TEAM_DELETED = "team.deleted"
TEAM_MEMBER_ADDED = "team.member.added"
TEAM_MEMBER_REMOVED = "team.member.removed"
TEAM_SOURCE_BOUND = "team.source.bound"


class TeamService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── resolution: the hot path ─────────────────────────────────────────
    async def team_ids(self, principal_id: UUID) -> frozenset[UUID]:
        """Every team this principal belongs to, in one query.

        Resolved once per request in `get_ctx`, beside the capability set, and
        held on the context for the life of the request. Teams are flat, so
        this is a single indexed read rather than a recursive walk — see the
        migration for why the flatness is a finding rather than a shortcut.
        """
        result = await self._db.execute(
            select(TeamMember.team_id).where(TeamMember.user_id == principal_id)
        )
        return frozenset(result.scalars().all())

    async def teams_of(self, principal_id: UUID) -> list[Team]:
        """The teams reaching this principal, by name — for `/auth/me`."""
        result = await self._db.execute(
            select(Team)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .where(TeamMember.user_id == principal_id)
            .order_by(Team.name)
        )
        return list(result.scalars())

    # ── reading ──────────────────────────────────────────────────────────
    async def list(self) -> list[Team]:
        result = await self._db.execute(select(Team).order_by(Team.name))
        return list(result.scalars())

    async def get(self, team_id: UUID) -> Team:
        team = await self._db.get(Team, team_id)
        if team is None:
            raise NotFoundError("Team not found.")
        return team

    async def by_name(self, name: str) -> Team | None:
        result = await self._db.execute(select(Team).where(Team.name == name))
        return result.scalar_one_or_none()

    async def member_counts(self) -> dict[UUID, int]:
        """How many people are in each team, for the list screen — one query."""
        result = await self._db.execute(
            select(TeamMember.team_id, func.count()).group_by(TeamMember.team_id)
        )
        return dict(result.all())

    async def members(self, team_id: UUID) -> list[User]:
        result = await self._db.execute(
            select(User)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team_id)
            .order_by(User.display_name, User.email)
        )
        return list(result.scalars())

    async def roles_by_team(self) -> dict[UUID, list[str]]:
        """Every team's roles, for the list screen — **one** grouped query.

        The list endpoint needs these, not just the detail one: a team's roles
        are the reason it exists, and a list that showed only member counts
        would put "what does this grant?" one click away from every row. It is
        a grouped read rather than a call per team for the same reason the
        member counts are.
        """
        result = await self._db.execute(
            select(RoleAssignment.team_id, Role.name)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(RoleAssignment.team_id.is_not(None))
            .order_by(Role.name)
        )
        out: dict[UUID, list[str]] = {}
        for team_id, name in result.all():
            out.setdefault(team_id, []).append(name)
        return out

    async def roles_of_team(self, team_id: UUID) -> list[Role]:
        result = await self._db.execute(
            select(Role)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(RoleAssignment.team_id == team_id)
            .order_by(Role.name)
        )
        return list(result.scalars())

    # ── writing ──────────────────────────────────────────────────────────
    async def create(
        self, ctx: RequestContext, *, name: str, description: str = ""
    ) -> Team:
        clean = name.strip()
        if not clean:
            raise ValidationError("A team needs a name.")
        if await self.by_name(clean) is not None:
            raise ConflictError("A team with that name already exists.")

        team = Team(id=uuid.uuid4(), name=clean, description=description.strip())
        self._db.add(team)
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=TEAM_CREATED, resource_type=TEAM, resource_id=team.id,
            detail={"name": team.name},
        )
        return team

    async def rename(
        self,
        ctx: RequestContext,
        team_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Team:
        """Name and description only.

        `(provider_id, source_id)` is deliberately not reachable from here —
        see `bind_source`, which is a separate operation for a separate reason.
        """
        team = await self.get(team_id)
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValidationError("A team needs a name.")
            if clean != team.name:
                if await self.by_name(clean) is not None:
                    raise ConflictError("A team with that name already exists.")
                team.name = clean
        if description is not None:
            team.description = description.strip()

        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=TEAM_RENAMED, resource_type=TEAM, resource_id=team.id,
            detail={"name": team.name},
        )
        return team

    async def bind_source(
        self,
        ctx: RequestContext,
        team_id: UUID,
        *,
        provider_id: str | None,
        source_id: str | None,
    ) -> Team:
        """Point this team at an external group, or unbind it. Both or neither.

        Its **own** operation and its own audit action, because rebinding
        redirects which external group's members flow into a set of
        permissions. That is not a rename with extra fields; it is the one
        thing in this module that can change who holds what without touching a
        single membership or grant row.

        Nothing reads these columns yet — there is no OIDC adapter. They are
        writable now so that binding an existing team, later, is two column
        updates rather than a namespace retrofitted onto identifiers every
        grant already points at.
        """
        team = await self.get(team_id)
        provider = (provider_id or "").strip() or None
        source = (source_id or "").strip() or None
        if (provider is None) != (source is None):
            raise ValidationError(
                "An external binding needs both a provider and a group "
                "identifier, or neither."
            )
        if provider is not None:
            clash = await self._db.execute(
                select(Team).where(
                    Team.provider_id == provider,
                    Team.source_id == source,
                    Team.id != team.id,
                )
            )
            if clash.scalar_one_or_none() is not None:
                raise ConflictError(
                    "Another team is already bound to that external group."
                )

        team.provider_id = provider
        team.source_id = source
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=TEAM_SOURCE_BOUND, resource_type=TEAM, resource_id=team.id,
            detail={"provider_id": provider, "source_id": source},
        )
        return team

    async def delete(self, ctx: RequestContext, team_id: UUID) -> None:
        """Refused while the team holds a role, naming which.

        The alternatives — cascading, or silently reassigning the members — are
        the failure this refusal exists to prevent: a permission change nobody
        asked for, at the moment somebody was trying to remove a permission.
        """
        team = await self.get(team_id)
        held = await self.roles_of_team(team_id)
        if held:
            names = ", ".join(role.name for role in held[:5])
            raise ConflictError(
                f"“{team.name}” still holds the {names} "
                f"{'role' if len(held) == 1 else 'roles'}. Remove them first."
            )

        await audit.record(
            self._db, ctx,
            action=TEAM_DELETED, resource_type=TEAM, resource_id=team.id,
            detail={"name": team.name},
        )
        await self._db.delete(team)
        await self._db.flush()

    # ── membership ───────────────────────────────────────────────────────
    async def add_member(
        self, ctx: RequestContext, *, team_id: UUID, user_id: UUID
    ) -> None:
        """Idempotent: adding somebody who is already in is not an error."""
        team = await self.get(team_id)
        if await self._db.get(User, user_id) is None:
            raise NotFoundError("User not found.")

        existing = await self._db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        if existing.scalar_one_or_none() is not None:
            return

        self._db.add(
            TeamMember(team_id=team_id, user_id=user_id, added_by=ctx.user_id)
        )
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=TEAM_MEMBER_ADDED, resource_type=TEAM, resource_id=team_id,
            detail={"team": team.name, "user_id": str(user_id)},
        )

    async def remove_member(
        self, ctx: RequestContext, *, team_id: UUID, user_id: UUID
    ) -> None:
        team = await self.get(team_id)
        await self._db.execute(
            delete(TeamMember).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=TEAM_MEMBER_REMOVED, resource_type=TEAM, resource_id=team_id,
            detail={"team": team.name, "user_id": str(user_id)},
        )

    async def set_members(
        self, ctx: RequestContext, *, team_id: UUID, user_ids: Sequence[UUID]
    ) -> list[User]:
        """The membership picker's whole answer, applied as a difference.

        A picker's state *is* the intended membership, so it is sent whole
        rather than as adds and removes the client had to diff correctly. The
        difference is computed here, so the audit log still gets one row per
        person who actually moved — a "membership replaced" row would be a
        record nobody can read.
        """
        current = {member.id for member in await self.members(team_id)}
        wanted = set(user_ids)
        for user_id in sorted(wanted - current):
            await self.add_member(ctx, team_id=team_id, user_id=user_id)
        for user_id in sorted(current - wanted):
            await self.remove_member(ctx, team_id=team_id, user_id=user_id)
        return await self.members(team_id)

    # ── roles held by a team ─────────────────────────────────────────────
    async def assign_role(
        self, ctx: RequestContext, *, team_id: UUID, role_id: UUID
    ) -> None:
        """Give a whole team a role. Members gain it on their next request.

        No last-administrator guard here, and that is a stated position rather
        than an omission: the guard counts principals holding `Administrator`
        **directly**, and Phase 4 does not change which people those are.
        Whether a team should be allowed to hold `Administrator` at all is §26
        of the plan's open question; until it is answered this path grants
        exactly what it says, and nothing about it is load-bearing for
        recovering a workspace.
        """
        from app.services.role_service import ROLE, ROLE_ASSIGNED

        team = await self.get(team_id)
        role = await self._db.get(Role, role_id)
        if role is None:
            raise NotFoundError("Role not found.")

        existing = await self._db.execute(
            select(RoleAssignment).where(
                RoleAssignment.team_id == team_id,
                RoleAssignment.role_id == role_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return

        self._db.add(
            RoleAssignment(
                id=uuid.uuid4(),
                team_id=team_id,
                role_id=role_id,
                created_by=ctx.user_id,
            )
        )
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=ROLE_ASSIGNED, resource_type=ROLE, resource_id=role_id,
            detail={"role": role.name, "team": team.name, "team_id": str(team_id)},
        )

    async def unassign_role(
        self, ctx: RequestContext, *, team_id: UUID, role_id: UUID
    ) -> None:
        from app.services.role_service import ROLE, ROLE_UNASSIGNED

        team = await self.get(team_id)
        role = await self._db.get(Role, role_id)
        if role is None:
            raise NotFoundError("Role not found.")

        await self._db.execute(
            delete(RoleAssignment).where(
                RoleAssignment.team_id == team_id,
                RoleAssignment.role_id == role_id,
            )
        )
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=ROLE_UNASSIGNED, resource_type=ROLE, resource_id=role_id,
            detail={"role": role.name, "team": team.name, "team_id": str(team_id)},
        )
