"""*"What can Ali reach?"* and *"who can reach this?"* — one query, two orderings.

Requirement 6 asks that the UI *"clearly communicate what a user can and cannot
access"*. Every product surveyed in §9 leaves this gap: Superset and Metabase
can both tell you what a *role* holds, and neither can answer the question an
administrator actually asks, which is about a **person** and a **thing**.

Both lenses read the same five facts §15.2 defines, and that is the property
worth protecting: a review that computed reach a second way would eventually
disagree with the authorizer, and the one that is wrong would be the one people
trust, because it is the one on the screen. So the SQL here is the same five
arms `RbacAuthorizer` uses — ownership, a direct grant, a team grant, a
wildcard grant, a role's scoped privilege — read as **rows** rather than as a
yes/no.

Two things this module is careful about, and both are the kind of thing a
review screen gets wrong:

* **It shows reach and never data.** A row names a resource and a privilege.
  It never carries a host, a username, a connection string, a stored statement
  or a row from a customer's database — nothing a `describe` holder could not
  already see. `test_access_review.py` asserts that against the response.
* **`path` is the answer, not the privilege.** *"Reza — modify"* is a fact
  nobody can act on. *"Reza — modify · role: BI Engineer"* tells them the
  revoke they want is on the role, and that removing his direct grant would
  change nothing. The five path kinds are the five facts, one for one.

The lattice is deliberately **not** expanded here. A review lists what was
*written down*: a `manage` row is one row, not five, because an administrator
reading this is looking for the grants they could revoke and a screen showing
five rows per grant would be a screen nobody could audit. The expansion is a
read-time question and belongs where the read-time question is asked.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.authz.owner_only import _OWNED_TABLES
from app.infra.db.models import (
    Grant,
    Role,
    RoleAssignment,
    RoleScopedPrivilege,
    Team,
    TeamMember,
    User,
)

#: The five path kinds, one per fact in §15.2. Closed, and identifiers rather
#: than sentences: the UI writes the sentence, a CSV column full of prose is a
#: column nobody can filter, and `Decision.because` already uses these words.
OWNER = "owner"
DIRECT = "direct"
TEAM = "team"
ROLE = "role"
WILDCARD = "wildcard"


@dataclass(frozen=True, slots=True)
class ReachRow:
    """One reason one principal reaches one resource.

    A principal can appear more than once for the same resource — owning it and
    also being granted `select` through a team is two rows, and collapsing them
    would hide the grant somebody is looking for. The screen groups; the query
    does not.

    `resource_id` is `None` for a wildcard: it reaches every resource of the
    type, including ones that do not exist yet, which is exactly why a wildcard
    is a role-shaped decision and gets its own audit action.
    """

    principal_id: UUID
    principal_name: str
    principal_kind: str
    resource_type: str
    resource_id: UUID | None
    resource_name: str
    privilege: str
    path: str
    #: The team or role the reach arrives through, for the two paths that have
    #: one. Empty otherwise — never `"—"` or `"n/a"`, which a CSV reader would
    #: have to know to strip.
    via: str = ""


class AccessReviewService:
    """The two lenses. Read-only, and it writes nothing at all.

    Gated by the route on `access.review`, never by this class: an auditor
    holds that capability and nothing else, and a service that also checked it
    would be the second place the rule lives.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── the two lenses ───────────────────────────────────────────────────
    async def by_principal(self, principal_id: UUID) -> list[ReachRow]:
        """Everything this person, machine or team can reach, and how.

        The principal may be a **team**, and that is not a special case bolted
        on: a team is a principal in this model, `grants.team_id` names one,
        and *"what does the Finance team have"* is the question somebody asks
        before adding a person to it.
        """
        rows = await self._all()
        teams = await self._teams_of(principal_id)
        return [
            row
            for row in rows
            if row.principal_id == principal_id
            # A team's grants reach its members, so a person's lens has to
            # include them — otherwise the answer to "what can Ali reach"
            # omits everything he reaches through his job, which is most of it.
            or (row.path == TEAM and row.principal_id in teams)
        ]

    async def by_resource(
        self, type_: ResourceType, resource_id: UUID
    ) -> list[ReachRow]:
        """Everyone who can reach this thing, and how.

        Includes the wildcard and role rows, whose `resource_id` is null: they
        reach this resource without naming it, and a screen that showed only
        the rows naming it would answer *"who can reach this"* with a list
        missing the administrator who can reach everything.
        """
        return [
            row
            for row in await self._all()
            if row.resource_type == str(type_)
            and row.resource_id in (resource_id, None)
        ]

    # ── the five facts, as rows ──────────────────────────────────────────
    async def _all(self) -> list[ReachRow]:
        """Every reach in the installation. Four queries, whatever is asked.

        Read whole and filtered in Python, which is the opposite of the rule
        every *request path* in this codebase follows — and it is right here
        for the reason that rule exists. `visible` is composed into a query
        because a list endpoint pages, and filtering after the fact is a
        pagination bug. This endpoint does not page: an access review is a
        whole answer or it is misleading, since "these are the twenty rows on
        page one" is not something an auditor can sign off. The installations
        this serves have hundreds of grants, not millions, and the day that
        stops being true the fix is a filtered query per lens rather than a
        page.
        """
        return [
            *await self._owners(),
            *await self._grants(),
            *await self._role_privileges(),
        ]

    async def _owners(self) -> list[ReachRow]:
        """Ownership — the whole lattice, one row per owned resource.

        `KNOWLEDGE` and `SEMANTIC_LAYER` are skipped: they are derived types
        carrying their connection's id, so the owner row would be the
        connection's own, printed three times.
        """
        out: list[ReachRow] = []
        for type_, table in _OWNED_TABLES.items():
            if type_ in (ResourceType.KNOWLEDGE, ResourceType.SEMANTIC_LAYER):
                continue
            label = _LABEL[type_]
            rows = await self._db.execute(
                select(
                    table.id,
                    getattr(table, label),
                    User.id,
                    User.display_name,
                    User.email,
                    User.kind,
                ).join(User, User.id == table.owner_id)
            )
            out.extend(
                ReachRow(
                    principal_id=row[2],
                    principal_name=_person(row[3], row[4]),
                    principal_kind=row[5] or "HUMAN",
                    resource_type=str(type_),
                    resource_id=row[0],
                    resource_name=row[1] or "",
                    privilege=str(Privilege.MANAGE),
                    path=OWNER,
                )
                for row in rows.all()
            )
        return out

    async def _grants(self) -> list[ReachRow]:
        """Direct, team and wildcard grants — one query, three path kinds.

        The path is decided by the row's own shape and nothing else: a
        `team_id` makes it a team grant, a null `resource_id` makes it a
        wildcard, and everything left is direct.
        """
        rows = await self._db.execute(
            select(
                Grant.resource_type,
                Grant.resource_id,
                Grant.privilege,
                Grant.user_id,
                Grant.team_id,
                User.display_name,
                User.email,
                User.kind,
                Team.name,
            )
            .outerjoin(User, User.id == Grant.user_id)
            .outerjoin(Team, Team.id == Grant.team_id)
        )
        names = await self._resource_names()
        out: list[ReachRow] = []
        for row in rows.all():
            is_team = row[4] is not None
            out.append(
                ReachRow(
                    principal_id=row[4] if is_team else row[3],
                    principal_name=(row[8] or "") if is_team else _person(row[5], row[6]),
                    principal_kind="TEAM" if is_team else (row[7] or "HUMAN"),
                    resource_type=row[0],
                    resource_id=row[1],
                    resource_name=(
                        names.get((row[0], row[1]), "")
                        if row[1] is not None
                        else _EVERY.get(row[0], "every one")
                    ),
                    privilege=row[2],
                    path=WILDCARD if row[1] is None else TEAM if is_team else DIRECT,
                    via=(row[8] or "") if is_team else "",
                )
            )
        return out

    async def _role_privileges(self) -> list[ReachRow]:
        """A role's scoped privilege over a whole type, per holder.

        Expanded to the people and teams that hold the role rather than listed
        as roles, because the lens is about principals: *"the BI Engineer role
        has modify on connections"* is a fact about the model, and *"Reza has
        modify on every connection, through the BI Engineer role"* is the
        answer to the question being asked.
        """
        rows = await self._db.execute(
            select(
                RoleScopedPrivilege.resource_type,
                RoleScopedPrivilege.privilege,
                Role.name,
                RoleAssignment.user_id,
                RoleAssignment.team_id,
                User.display_name,
                User.email,
                User.kind,
                Team.name,
            )
            .join(Role, Role.id == RoleScopedPrivilege.role_id)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .outerjoin(User, User.id == RoleAssignment.user_id)
            .outerjoin(Team, Team.id == RoleAssignment.team_id)
        )
        out: list[ReachRow] = []
        for row in rows.all():
            is_team = row[4] is not None
            out.append(
                ReachRow(
                    principal_id=row[4] if is_team else row[3],
                    principal_name=(row[8] or "") if is_team else _person(row[5], row[6]),
                    principal_kind="TEAM" if is_team else (row[7] or "HUMAN"),
                    resource_type=row[0],
                    resource_id=None,
                    resource_name=_EVERY.get(row[0], "every one"),
                    privilege=row[1],
                    path=ROLE,
                    via=row[2],
                )
            )
        return out

    # ── names, and nothing else ──────────────────────────────────────────
    async def _resource_names(self) -> dict[tuple[str, UUID], str]:
        """What each resource is called. **A name and an id, never a field.**

        This is the whole of what a review response knows about a resource. A
        host, a username, a stored statement or a row is data, and an access
        review that carried any of it would be a screen that leaked the thing
        it exists to control.
        """
        names: dict[tuple[str, UUID], str] = {}
        for type_, table in _OWNED_TABLES.items():
            if type_ in (ResourceType.KNOWLEDGE, ResourceType.SEMANTIC_LAYER):
                continue
            rows = await self._db.execute(
                select(table.id, getattr(table, _LABEL[type_]))
            )
            for row in rows.all():
                names[(str(type_), row[0])] = row[1] or ""
                # The derived types are the connection under another name, so
                # a grant on `knowledge` finds its label here too rather than
                # rendering as a bare id.
                if type_ is ResourceType.CONNECTION:
                    names[(str(ResourceType.KNOWLEDGE), row[0])] = row[1] or ""
                    names[(str(ResourceType.SEMANTIC_LAYER), row[0])] = row[1] or ""
        rows = await self._db.execute(select(Team.id, Team.name))
        for row in rows.all():
            names[(str(ResourceType.TEAM), row[0])] = row[1] or ""
        return names

    async def _teams_of(self, principal_id: UUID) -> set[UUID]:
        rows = await self._db.execute(
            select(TeamMember.team_id).where(TeamMember.user_id == principal_id)
        )
        return set(rows.scalars())


def _person(display_name: str | None, email: str | None) -> str:
    """A display name, or the local part of an address. **Never the address.**

    The rule the review queue and the audit screen already follow, and it
    matters most here: this screen is *about people*, and a full list of every
    address in the installation is a thing an access review has no need to be.
    """
    return display_name or (email or "").split("@")[0]


#: Which column holds a resource's human name, per type. The same map
#: `grant_service` keeps for the deletion refusal, and for the same reason: a
#: conversation has a `title` and everything else has a `name`, so a loop that
#: guessed would raise on the one table it could not read.
_LABEL: dict[ResourceType, str] = {
    ResourceType.CONNECTION: "name",
    ResourceType.LLM_CONFIG: "name",
    ResourceType.DASHBOARD: "name",
    ResourceType.REPORT: "name",
    ResourceType.CONVERSATION: "title",
}

#: What a wildcard's "resource" is called. A sentence rather than an empty
#: cell, because *"every data source"* is the fact, and a blank would read as
#: a row whose resource had been deleted.
_EVERY: dict[str, str] = {
    str(ResourceType.CONNECTION): "every data source",
    str(ResourceType.KNOWLEDGE): "every knowledge store",
    str(ResourceType.SEMANTIC_LAYER): "every semantic layer",
    str(ResourceType.LLM_CONFIG): "every model configuration",
    str(ResourceType.DASHBOARD): "every dashboard",
    str(ResourceType.REPORT): "every report",
    str(ResourceType.CONVERSATION): "every conversation",
    str(ResourceType.TEAM): "every team",
}
