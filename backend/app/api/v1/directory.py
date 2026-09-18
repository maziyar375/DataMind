"""Who you can share with: the people and teams of this installation.

**Why this exists beside `GET /users` and `GET /teams`.** Those two are
administration: `/users` needs `user.read` and returns accounts — status, roles,
the address somebody signs in with. The share dialog needs something much
smaller and needs it for everybody, because deciding who else may see your own
dashboard is part of owning one. When the dialog read `/users`, a Normal User —
the role that builds dashboards and reports — could share with teams only, and
the transfer dialog offered nobody at all.

So this returns the least a picker needs: an id to grant to, a name to show,
and whether it is a person, a service account or a team. **No email**, the rule
`owner_names` already follows: *"who is this"* is answered by a name, and an
address is personal data the question does not need. Active accounts only — a
disabled person cannot be granted anything useful and should not be offered.

Any signed-in principal may read it. It says who exists, which every shared
dashboard's owner line already says, and nothing about what anybody can reach.
"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CtxDep, DbDep
from app.api.schemas import DirectoryEntry, DirectoryRead
from app.infra.db.models import Team, TeamMember, User

router = APIRouter(prefix="/directory", tags=["directory"])


@router.get("", response_model=DirectoryRead)
async def read_directory(ctx: CtxDep, db: DbDep) -> DirectoryRead:
    people = await db.execute(
        select(User.id, User.display_name, User.email, User.kind)
        .where(User.status == "ACTIVE")
        .order_by(func.lower(func.coalesce(User.display_name, User.email)))
    )
    members = (
        select(TeamMember.team_id, func.count().label("n"))
        .group_by(TeamMember.team_id)
        .subquery()
    )
    teams = await db.execute(
        select(Team.id, Team.name, func.coalesce(members.c.n, 0))
        .outerjoin(members, members.c.team_id == Team.id)
        .order_by(func.lower(Team.name))
    )
    return DirectoryRead(
        people=[
            DirectoryEntry(
                id=row[0],
                # The part before the @ when there is no display name — the
                # same fallback `owner_names` uses, so a person is called the
                # same thing in the picker and on the card afterwards.
                name=row[1] or row[2].split("@")[0],
                kind=row[3] or "HUMAN",
                is_you=row[0] == ctx.user_id,
            )
            for row in people.all()
        ],
        teams=[
            DirectoryEntry(id=row[0], name=row[1], kind="TEAM", members=int(row[2]))
            for row in teams.all()
        ],
    )
