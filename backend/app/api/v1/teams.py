"""HTTP shape for teams. No business logic — see `services/team_service.py`.

Reading is `team.read`, writing is `team.manage`, and both arrive through
`deps.needs(...)` so the check runs before the handler body.

**`team.read` is held by five of the eight seed roles**, and that is the point
rather than an oversight: a person needs to be able to see the teams they are
in and who else is in them, because from Phase 6 a team is how they will have
been given access to anything. Changing one is a different question, and
`team.manage` is held by Administrator alone.

`PUT /teams/{id}/source` is a **separate, audited endpoint**, and it is the one
design decision in this file worth defending. Rebinding a team to a different
external group redirects which people flow into a set of permissions — it can
change who holds what without touching a single membership or grant row. Making
it two more optional fields on `PATCH` would file that under `team.renamed` in
the audit log, which is the kind of omission an access review cannot recover
from.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbDep, TeamManageDep, TeamReadDep
from app.api.schemas import (
    RoleAssignmentWrite,
    TeamCreate,
    TeamMembersWrite,
    TeamRead,
    TeamSourceWrite,
    TeamWrite,
    UserRead,
)
from app.infra.db.models import Team
from app.services.team_service import TeamService

router = APIRouter(prefix="/teams", tags=["teams"])


def _read(team: Team, members: int = 0, roles: list[str] | None = None) -> TeamRead:
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description,
        members=members,
        roles=roles or [],
        provider_id=team.provider_id,
        source_id=team.source_id,
        created_at=team.created_at,
    )


@router.get("", response_model=list[TeamRead])
async def list_teams(ctx: TeamReadDep, db: DbDep) -> list[TeamRead]:
    """Every team, with its member count **and its roles**.

    Two grouped queries beside the list, not two per row. The roles are here
    rather than only on the detail endpoint because they are the reason a team
    exists: a list showing member counts alone puts "what does this actually
    grant?" one click away from every row, and that is the question somebody
    scanning this screen is asking.
    """
    service = TeamService(db)
    counts = await service.member_counts()
    roles = await service.roles_by_team()
    return [
        _read(team, counts.get(team.id, 0), roles.get(team.id, []))
        for team in await service.list()
    ]


@router.get("/{team_id}", response_model=TeamRead)
async def get_team(team_id: UUID, ctx: TeamReadDep, db: DbDep) -> TeamRead:
    service = TeamService(db)
    team = await service.get(team_id)
    return _read(
        team,
        len(await service.members(team_id)),
        [role.name for role in await service.roles_of_team(team_id)],
    )


@router.get("/{team_id}/members", response_model=list[UserRead])
async def list_members(team_id: UUID, ctx: TeamReadDep, db: DbDep) -> list:
    service = TeamService(db)
    await service.get(team_id)
    return await service.members(team_id)


@router.post("", response_model=TeamRead, status_code=status.HTTP_201_CREATED)
async def create_team(payload: TeamCreate, ctx: TeamManageDep, db: DbDep) -> TeamRead:
    team = await TeamService(db).create(
        ctx, name=payload.name, description=payload.description or ""
    )
    return _read(team)


@router.patch("/{team_id}", response_model=TeamRead)
async def update_team(
    team_id: UUID, payload: TeamWrite, ctx: TeamManageDep, db: DbDep
) -> TeamRead:
    service = TeamService(db)
    team = await service.rename(
        ctx, team_id, name=payload.name, description=payload.description
    )
    return _read(team, len(await service.members(team_id)))


@router.put("/{team_id}/source", response_model=TeamRead)
async def bind_team_source(
    team_id: UUID, payload: TeamSourceWrite, ctx: TeamManageDep, db: DbDep
) -> TeamRead:
    """Bind this team to an external group, or unbind it.

    Its own endpoint and its own audit action — see the module docstring. It is
    inert today: no adapter reads `provider_id`, so binding a team changes
    nothing about who is in it. What it buys is that when an adapter arrives,
    binding an existing team is two column updates instead of a migration over
    every identifier a grant points at.
    """
    service = TeamService(db)
    team = await service.bind_source(
        ctx, team_id, provider_id=payload.provider_id, source_id=payload.source_id
    )
    return _read(team, len(await service.members(team_id)))


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(team_id: UUID, ctx: TeamManageDep, db: DbDep) -> None:
    """Refused while the team holds a role, naming which."""
    await TeamService(db).delete(ctx, team_id)


@router.put("/{team_id}/members", response_model=list[UserRead])
async def set_team_members(
    team_id: UUID, payload: TeamMembersWrite, ctx: TeamManageDep, db: DbDep
) -> list:
    """The whole intended membership. Returns who is in the team afterwards."""
    service = TeamService(db)
    await service.get(team_id)
    return await service.set_members(ctx, team_id=team_id, user_ids=payload.user_ids)


@router.post(
    "/{team_id}/roles", response_model=TeamRead, status_code=status.HTTP_201_CREATED
)
async def assign_team_role(
    team_id: UUID, payload: RoleAssignmentWrite, ctx: TeamManageDep, db: DbDep
) -> TeamRead:
    """Give the whole team a role. Members hold it from their next request."""
    service = TeamService(db)
    await service.assign_role(ctx, team_id=team_id, role_id=payload.role_id)
    team = await service.get(team_id)
    return _read(
        team,
        len(await service.members(team_id)),
        [role.name for role in await service.roles_of_team(team_id)],
    )


@router.delete(
    "/{team_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def unassign_team_role(
    team_id: UUID, role_id: UUID, ctx: TeamManageDep, db: DbDep
) -> None:
    await TeamService(db).unassign_role(ctx, team_id=team_id, role_id=role_id)
