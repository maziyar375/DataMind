"""Making, listing and revoking a share — and the four refusals around it.

Writing a `grants` row is one `db.add`. Everything else here is the reason this
is a service:

* **`manage` is required for all four operations.** Not `modify`: editing a
  connection's credentials and deciding who else may read through it are
  different acts, and a lattice that implied the second from the first would
  mean anybody who could fix a password could also hand the database out. §14
  of the plan: `manage` is *not* implied by `modify`, which is also why revoke
  never has to walk a chain — no chain can exist.
* **Self-revocation of the last `manage` is refused.** The exact sibling of
  `_guard_last_administrator`: a resource whose last manager removed their own
  access is a resource nobody can share, unshare, transfer or set a disclosure
  policy on, and the only recovery is a database client. The owner is counted
  as a manager, because ownership confers the whole lattice — so an owner
  revoking their own redundant `manage` grant is allowed, and a non-owner
  revoking the last one is not.
* **A wildcard additionally requires `role.manage`.** `resource_id IS NULL`
  reaches every resource of a type, now and every one created afterwards. That
  is a role-shaped decision wearing a grant's clothes, so it needs the
  role-shaped capability and gets its own audit action — `grant.wildcard.created`
  — so it cannot hide among ordinary shares in a log somebody is scanning.
* **Ownership transfer is `manage`, audited, and refuses an inactive owner.**
  Handing a resource to a `DISABLED` account is a resource with no reachable
  owner, which is the state the deletion guard exists to prevent.

**Rows carry the resource, never the reason.** There is no `note` column and no
`granted_because`: an audit row records who granted what to whom and when, and
that is the record. A free-text justification field is a field nobody fills in
truthfully and everybody then trusts.
"""
from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.ports.authz import Authorizer, ResourceRef
from app.domain.value_objects import UserStatus
from app.domain.value_objects.authz import (
    SHAREABLE_LLM_CONFIG_PRIVILEGES,
    Capability,
    Privilege,
    ResourceType,
)
from app.infra.authz.owner_only import _OWNED_TABLES
from app.infra.db.models import Grant, Team, User
from app.services import audit
from app.services.policy import require

log = get_logger(__name__)

#: The five grant actions, in one place — for the same reason `role_service`
#: and `team_service` keep theirs there: an administrator reading the log
#: should be able to enumerate what can appear in it without reading routers.
GRANT = "grant"
GRANT_CREATED = "grant.created"
GRANT_REVOKED = "grant.revoked"
GRANT_WILDCARD_CREATED = "grant.wildcard.created"
OWNERSHIP_TRANSFERRED = "ownership.transferred"
DISCLOSURE_CHANGED = "disclosure.changed"
#: Decision 14, and the reason there is no administrator arm in the authorizer.
#: An administrator reaching somebody else's resource writes **two** rows: the
#: ordinary grant, and this — so the log distinguishes "Sara shared this" from
#: "an administrator gave themselves access", which are different events that a
#: single `grant.created` would render identically.
ADMIN_SELF_GRANTED = "admin.self_granted"


class GrantView:
    """One row of *"who can reach this"*, with **the path**.

    The path is the whole value of this screen and the reason it is not just a
    list of names. *"Sara — select"* is a fact somebody can neither act on nor
    verify; *"Sara — select, via the Finance team"* tells them the revoke they
    want is on the team, not on Sara, and that revoking it here would do
    nothing.

    `grant_id` is `None` for a path that is not a row — ownership. Ownership is
    not a grant (§14.1) and cannot be revoked, only transferred, so the UI
    renders it without a revoke control and this class says why in one field.
    """

    __slots__ = (
        "grant_id", "path", "principal_id", "principal_kind",
        "principal_name", "privilege", "team_id",
    )

    def __init__(
        self,
        *,
        grant_id: UUID | None,
        principal_id: UUID,
        principal_name: str,
        principal_kind: str,
        privilege: Privilege,
        path: str,
        team_id: UUID | None = None,
    ) -> None:
        self.grant_id = grant_id
        self.principal_id = principal_id
        self.principal_name = principal_name
        self.principal_kind = principal_kind
        self.privilege = privilege
        self.path = path
        self.team_id = team_id


class GrantService:
    def __init__(self, db: AsyncSession, authz: Authorizer) -> None:
        self._db = db
        self._authz = authz

    # ── reading ──────────────────────────────────────────────────────────
    async def for_resource(
        self, ctx: RequestContext, ref: ResourceRef
    ) -> list[GrantView]:
        """Everyone who can reach this resource, and how. Needs `manage`.

        Ownership is listed first and without a grant id, because it is a path
        rather than a row. Then the grants, people before teams, so the list
        reads as *"these individuals, then these groups"* rather than as
        creation order — which is meaningless to somebody auditing a share.
        """
        await self._require_manage(ctx, ref)

        views: list[GrantView] = []
        owner = await self._owner_of(ref)
        if owner is not None:
            views.append(
                GrantView(
                    grant_id=None,
                    principal_id=owner.id,
                    principal_name=owner.display_name or owner.email,
                    principal_kind=owner.kind or "HUMAN",
                    privilege=Privilege.MANAGE,
                    path="owner",
                )
            )

        rows = await self._db.execute(
            select(Grant).where(
                Grant.resource_type == str(ref.type),
                Grant.resource_id == ref.id,
            )
        )
        grants = list(rows.scalars())
        people = await self._people({g.user_id for g in grants if g.user_id})
        teams = await self._teams({g.team_id for g in grants if g.team_id})

        for grant in grants:
            privilege = _known(grant.privilege)
            if privilege is None:
                continue
            if grant.user_id is not None:
                person = people.get(grant.user_id)
                if person is None:  # pragma: no cover - CASCADE removes these
                    continue
                views.append(
                    GrantView(
                        grant_id=grant.id,
                        principal_id=person.id,
                        principal_name=person.display_name or person.email,
                        principal_kind=person.kind or "HUMAN",
                        privilege=privilege,
                        path="direct",
                    )
                )
            else:
                team = teams.get(grant.team_id)
                if team is None:  # pragma: no cover - CASCADE removes these
                    continue
                views.append(
                    GrantView(
                        grant_id=grant.id,
                        principal_id=team.id,
                        principal_name=team.name,
                        principal_kind="TEAM",
                        privilege=privilege,
                        path="team",
                        team_id=team.id,
                    )
                )

        views.sort(
            key=lambda v: (
                v.path != "owner", v.principal_kind == "TEAM", v.principal_name
            )
        )
        return views

    async def for_principal(self, principal_id: UUID) -> list[Grant]:
        """Every grant naming this principal directly. For the access review.

        Direct only, and deliberately: *"what has Ali been given"* and *"what
        can Ali reach"* are different questions, and the second is Phase 9's,
        answered by walking teams and roles as well. Conflating them here would
        make this method quietly the wrong tool for both.
        """
        rows = await self._db.execute(
            select(Grant).where(Grant.user_id == principal_id)
        )
        return list(rows.scalars())

    # ── writing ──────────────────────────────────────────────────────────
    async def grant(
        self,
        ctx: RequestContext,
        ref: ResourceRef,
        *,
        privilege: Privilege,
        user_id: UUID | None = None,
        team_id: UUID | None = None,
    ) -> Grant:
        """Share this resource. Idempotent; needs `manage` on the resource.

        Idempotent rather than a conflict, for the same reason `RoleService.assign`
        is: pressing a control that is already on is not an error, and the
        alternative makes every client implement "check, then set" against a
        race with another administrator.
        """
        await self._require_manage(ctx, ref)
        principal = await self._one_principal(user_id, team_id)
        self._guard_key_equivalent(ref, privilege)

        existing = await self._db.execute(
            select(Grant).where(
                Grant.resource_type == str(ref.type),
                Grant.resource_id == ref.id,
                Grant.user_id == user_id,
                Grant.team_id == team_id,
                Grant.privilege == str(privilege),
            )
        )
        held = existing.scalar_one_or_none()
        if held is not None:
            return held

        row = Grant(
            id=uuid.uuid4(),
            resource_type=str(ref.type),
            resource_id=ref.id,
            user_id=user_id,
            team_id=team_id,
            privilege=str(privilege),
            created_by=ctx.user_id,
        )
        self._db.add(row)
        await self._db.flush()

        await audit.record(
            self._db, ctx,
            action=GRANT_CREATED,
            resource_type=str(ref.type), resource_id=ref.id,
            detail={
                "privilege": str(privilege),
                "principal_id": str(user_id or team_id),
                "principal": principal,
                "grant_id": str(row.id),
            },
        )
        return row

    async def grant_wildcard(
        self,
        ctx: RequestContext,
        type_: ResourceType,
        *,
        privilege: Privilege,
        user_id: UUID | None = None,
        team_id: UUID | None = None,
    ) -> Grant:
        """Every resource of this type, now and in future. Needs `role.manage`.

        Not `manage` on anything, because there is no *anything* — a wildcard
        reaches resources that do not exist yet, so no resource-scoped
        permission could authorise it. It is a role-shaped decision wearing a
        grant's clothes, so it takes the role-shaped capability and its own
        audit action.
        """
        if not ctx.can(Capability.ROLE_MANAGE):
            raise ValidationError(
                "Granting access to every resource of a type reaches things "
                "that do not exist yet, so it needs the “role.manage” "
                "permission — the same one that defines what a role may do. "
                "Grant the specific resources instead, or use a role."
            )
        principal = await self._one_principal(user_id, team_id)

        existing = await self._db.execute(
            select(Grant).where(
                Grant.resource_type == str(type_),
                Grant.resource_id.is_(None),
                Grant.user_id == user_id,
                Grant.team_id == team_id,
                Grant.privilege == str(privilege),
            )
        )
        held = existing.scalar_one_or_none()
        if held is not None:
            return held

        row = Grant(
            id=uuid.uuid4(),
            resource_type=str(type_),
            resource_id=None,
            user_id=user_id,
            team_id=team_id,
            privilege=str(privilege),
            created_by=ctx.user_id,
        )
        self._db.add(row)
        await self._db.flush()

        await audit.record(
            self._db, ctx,
            action=GRANT_WILDCARD_CREATED,
            resource_type=str(type_), resource_id=None,
            detail={
                "privilege": str(privilege),
                "principal_id": str(user_id or team_id),
                "principal": principal,
                "grant_id": str(row.id),
            },
        )
        log.warning(
            "wildcard_grant_created",
            resource_type=str(type_), privilege=str(privilege),
        )
        return row

    async def self_grant(
        self, ctx: RequestContext, ref: ResourceRef, *, privilege: Privilege
    ) -> Grant:
        """An administrator giving **themselves** access. Two rows, never none.

        This is the whole of decision 14, and the reason `RbacAuthorizer` has no
        administrator arm. Every product in this space has one, and every one of
        them has the same hole: an administrator can read any customer's data
        and the only evidence is the absence of an error. The alternative is not
        "administrators cannot help" — it is that helping is an *act*, and the
        act leaves a record.

        So an administrator may reach anything, and reaching it writes:

        1. an ordinary `grants` row, which is what the authorizer then sees —
           there is no second code path, no flag, and nothing for a later reader
           to discover; and
        2. an `admin.self_granted` row, so *"who gave themselves access to
           what, and when"* is one filter on the audit screen rather than a
           join somebody has to think of.

        The grant is **not** revoked afterwards, deliberately. A self-grant that
        expired at the end of the request would be indistinguishable from a
        silent read path in every way that matters — the row would be gone by
        the time anybody looked. It stays until somebody revokes it, which is
        itself a row.

        `user.manage` is the gate because that is what "administrator" means in
        this codebase now: it is exactly the capability the old `ADMIN` enum
        gated, and `role.manage` would let somebody who defines roles reach data
        through a door meant for account recovery.
        """
        if not ctx.can(Capability.USER_MANAGE):
            raise ValidationError(
                "Only an administrator can grant themselves access to somebody "
                "else's resource, and doing so is recorded. Ask whoever manages "
                "this to share it with you instead."
            )

        # Written **before** the grant, so a failure between the two leaves the
        # louder row rather than the quieter one. An `admin.self_granted` with
        # no grant beside it is a puzzle somebody investigates; a grant with no
        # `admin.self_granted` is the thing this exists to prevent.
        await audit.record(
            self._db, ctx,
            action=ADMIN_SELF_GRANTED,
            resource_type=str(ref.type), resource_id=ref.id,
            detail={"privilege": str(privilege), "principal_id": str(ctx.user_id)},
        )
        log.warning(
            "admin_self_granted",
            resource_type=str(ref.type),
            privilege=str(privilege),
        )

        row = Grant(
            id=uuid.uuid4(),
            resource_type=str(ref.type),
            resource_id=ref.id,
            user_id=ctx.user_id,
            team_id=None,
            privilege=str(privilege),
            created_by=ctx.user_id,
        )
        self._db.add(row)
        await self._db.flush()
        await audit.record(
            self._db, ctx,
            action=GRANT_CREATED,
            resource_type=str(ref.type), resource_id=ref.id,
            detail={
                "privilege": str(privilege),
                "principal_id": str(ctx.user_id),
                "grant_id": str(row.id),
                "self_granted": True,
            },
        )
        return row

    async def revoke(
        self, ctx: RequestContext, ref: ResourceRef, grant_id: UUID
    ) -> None:
        """Take a share away. Needs `manage`; refuses the last one to yourself.

        The grant is re-read against the resource rather than trusted from the
        path: `/connections/{a}/grants/{b}` where `b` belongs to another
        resource would otherwise revoke a share from a screen that showed
        neither.
        """
        await self._require_manage(ctx, ref)
        row = await self._db.get(Grant, grant_id)
        if (
            row is None
            or row.resource_type != str(ref.type)
            or row.resource_id != ref.id
        ):
            raise NotFoundError("That share no longer exists.")

        await self._guard_last_manager(ctx, ref, row)

        await audit.record(
            self._db, ctx,
            action=GRANT_REVOKED,
            resource_type=str(ref.type), resource_id=ref.id,
            detail={
                "privilege": row.privilege,
                "principal_id": str(row.user_id or row.team_id),
                "grant_id": str(row.id),
            },
        )
        await self._db.delete(row)
        await self._db.flush()

    async def transfer(
        self, ctx: RequestContext, ref: ResourceRef, *, to: UUID
    ) -> None:
        """Hand this resource to another principal. `manage`, and audited.

        The new owner must be `ACTIVE`: a resource owned by a disabled account
        has an owner nobody can reach, which is the state `DELETE /users/{id}`
        refuses to create and this must not create by another route.

        The old owner keeps nothing. If they should retain access, grant it —
        which is one row, and a row somebody can see, rather than an implicit
        residue of a transfer that happened months ago.
        """
        await self._require_manage(ctx, ref)
        table = _OWNED_TABLES.get(ref.type)
        if table is None:
            raise ValidationError("This kind of resource has no owner to transfer.")

        new_owner = await self._db.get(User, to)
        if new_owner is None:
            raise NotFoundError("That principal does not exist.")
        if new_owner.status != UserStatus.ACTIVE:
            raise ValidationError(
                f"“{new_owner.display_name or new_owner.email}” is not active. "
                "Transferring to a disabled account would leave this with an "
                "owner nobody can sign in as."
            )

        row = await self._db.get(table, ref.id)
        if row is None:
            raise NotFoundError("That resource no longer exists.")
        previous = row.owner_id
        row.owner_id = to
        await self._db.flush()

        await audit.record(
            self._db, ctx,
            action=OWNERSHIP_TRANSFERRED,
            resource_type=str(ref.type), resource_id=ref.id,
            detail={"from": str(previous), "to": str(to)},
        )

    async def revoke_all_for(self, type_: ResourceType, resource_id: UUID) -> int:
        """Every grant naming this resource, gone. The delete hook.

        Called when the resource is deleted, so the sweep in
        `workers/reconciler.py` has nothing to find in the normal case — the
        sweep is for the abnormal one (a delete that bypassed the service, a
        crash between two statements), which is why both exist.
        """
        result = await self._db.execute(
            delete(Grant).where(
                Grant.resource_type == str(type_),
                Grant.resource_id == resource_id,
            )
        )
        return result.rowcount or 0

    # ── the guards ───────────────────────────────────────────────────────
    async def _require_manage(self, ctx: RequestContext, ref: ResourceRef) -> None:
        """`manage`, for all four operations. Never `modify`.

        Editing a connection's credentials and deciding who else may read
        through it are different acts. A lattice that implied the second from
        the first would mean anybody who could fix a password could also hand
        the database out — which is exactly the conflation §14 exists to
        prevent.
        """
        await require(ctx, self._authz, ref, Privilege.MANAGE, db=self._db)

    async def _guard_last_manager(
        self, ctx: RequestContext, ref: ResourceRef, row: Grant
    ) -> None:
        """Nobody may revoke their own last `manage` and strand the resource.

        The sibling of `_guard_last_administrator`, and the same failure it
        prevents: a thing whose last manager removed their own access can no
        longer be shared, unshared, transferred or re-policied by anybody
        except through a database client.

        **Two conditions narrow it, and the second is worth knowing.**

        Only the *self* case is guarded: revoking somebody **else's** last
        `manage` is allowed, because whoever is doing it still holds `manage`
        by definition — they got past the gate — so nothing is stranded.
        Guarding that too would stop an administrator cleaning up after a
        departing colleague.

        And it can only actually fire for a resource type with **no owner**.
        Every owned table in this schema has `owner_id NOT NULL`, and ownership
        confers the whole lattice, so a connection, dashboard, report,
        conversation or model configuration always has at least one manager and
        cannot be stranded by any revoke. `ResourceType.TEAM` is the type this
        guard exists for today: a team has no owner column, so its `manage` is
        entirely by grant.

        The branch is kept rather than special-cased to `TEAM`, because the
        condition it tests — *"is anybody left who can manage this?"* — is the
        real question, and a version that hardcoded the one type it currently
        answers for would silently stop protecting the next type added without
        an owner.
        """
        if row.user_id != ctx.user_id:
            return
        if _known(row.privilege) != Privilege.MANAGE:
            return

        owner = await self._owner_of(ref)
        if owner is not None and owner.id == ctx.user_id:
            # An owner holds the whole lattice regardless of any grant, so this
            # row is redundant and dropping it changes nothing.
            return

        others = await self._db.execute(
            select(Grant.id)
            .where(
                Grant.resource_type == str(ref.type),
                Grant.resource_id == ref.id,
                Grant.privilege == str(Privilege.MANAGE),
                Grant.id != row.id,
            )
            .limit(1)
        )
        if others.scalars().first() is None and owner is None:
            raise ValidationError(
                "This is the only way anybody can manage access to this "
                "resource. Give somebody else “manage” first, or transfer "
                "ownership."
            )

    def _guard_key_equivalent(self, ref: ResourceRef, privilege: Privilege) -> None:
        """`modify` on an `llm_config` is equivalent to disclosing the API key.

        A holder can repoint `base_url` at a host they control and read the key
        out of the next request's `Authorization` header. So the share surface
        offers `describe` and `select` on this type and the API refuses the
        other three — the owner reaches them by owning it, and an administrator
        through the explicit self-grant path, both of which leave a row.
        """
        if ref.type is not ResourceType.LLM_CONFIG:
            return
        if privilege not in SHAREABLE_LLM_CONFIG_PRIVILEGES:
            raise ValidationError(
                f"“{privilege}” cannot be granted on a model configuration: "
                "anyone who can edit its endpoint can read its API key by "
                "pointing it at a host they control. Share “select” so they "
                "can use it to answer questions."
            )

    async def _one_principal(
        self, user_id: UUID | None, team_id: UUID | None
    ) -> str:
        """Exactly one, and it exists. Returns its name, for the audit row.

        The `CHECK` would catch "both or neither" as an `IntegrityError`, which
        a caller can only report as *"something went wrong"*. The useful
        sentence is the one below, and the existence check is what turns a
        typo'd id into a 404 rather than a share nobody can see.
        """
        if (user_id is None) == (team_id is None):
            raise ValidationError(
                "A share goes to exactly one person or one team."
            )
        if user_id is not None:
            person = await self._db.get(User, user_id)
            if person is None:
                raise NotFoundError("That person does not exist.")
            return person.display_name or person.email
        team = await self._db.get(Team, team_id)
        if team is None:
            raise NotFoundError("That team does not exist.")
        return team.name

    # ── small reads ──────────────────────────────────────────────────────
    async def _owner_of(self, ref: ResourceRef) -> User | None:
        """The owning `User` row, or `None`.

        Two hops for the derived types, and that is the point of them: a
        knowledge store's owner *is* its connection's owner, and its resource
        id *is* the connection's id — so `_OWNED_TABLES` maps both to
        `database_connections` and this reads the same row either way.
        """
        table = _OWNED_TABLES.get(ref.type)
        if table is None:
            return None
        owner_id = (
            await self._db.execute(select(table.owner_id).where(table.id == ref.id))
        ).scalar_one_or_none()
        if owner_id is None:
            return None
        return await self._db.get(User, owner_id)

    async def _people(self, ids: set[UUID]) -> dict[UUID, User]:
        if not ids:
            return {}
        rows = await self._db.execute(select(User).where(User.id.in_(ids)))
        return {row.id: row for row in rows.scalars()}

    async def _teams(self, ids: set[UUID]) -> dict[UUID, Team]:
        if not ids:
            return {}
        rows = await self._db.execute(select(Team).where(Team.id.in_(ids)))
        return {row.id: row for row in rows.scalars()}


def _known(word: str) -> Privilege | None:
    """The `Privilege` this word names, or `None`. Open row, closed code."""
    try:
        return Privilege(word)
    except ValueError:
        return None


async def owned_resources(db: AsyncSession, principal_id: UUID) -> list[str]:
    """What this principal owns, named — for `DELETE /users/{id}`'s refusal.

    **The refusal prevents silent data loss, not an orphaned row**, and the
    difference is worth stating because it makes this the most consequential
    guard in the phase. Every owned table — `database_connections`,
    `llm_configs`, `dashboards`, `reports`, `conversations` — declares
    `owner_id NOT NULL` with `ON DELETE CASCADE`. So deleting a principal today
    does not leave their work ownerless; it **deletes their work**, along with
    every dashboard other people had been granted and every report those
    dashboards fed. Nothing warns first, and nothing can be undone.

    So the delete is refused while they own anything, and the refusal **names
    what they own** — because *"transfer these four things first"* is a support
    ticket somebody can act on, and *"cannot delete user"* is not.
    `POST /{resource}/{id}/transfer` is the way through it, which is why that
    endpoint exists on every owned type rather than only on connections.

    Reads every owned table, because every one of them cascades. Five small
    indexed reads, on a path that runs once per deletion.
    """
    names: list[str] = []
    for type_, label in _LABEL_COLUMN.items():
        table = _OWNED_TABLES[type_]
        rows = await db.execute(
            select(getattr(table, label))
            # Naming what a principal owns, not deciding what anybody may
            # reach. The question is "would deleting this account orphan
            # rows?", which is a question about the `owner_id` column itself:
            # no authorizer answer could stand in for it, because the principal
            # being asked about is the one about to be deleted.
            .where(table.owner_id == principal_id)  # authz-ok: owns, not reaches
            .limit(20)
        )
        names.extend(
            f"{str(type_).replace('_', ' ')} “{value or 'untitled'}”"
            for value in rows.scalars()
        )
    return names


#: What to call each owned thing in the refusal, and **which column holds it**.
#:
#: Written out rather than assumed, because the column is not the same
#: everywhere: a conversation has a `title`, everything else has a `name`, and
#: a loop that guessed `name` would raise on the one table it could not read —
#: turning a refusal somebody can act on into a 500.
#:
#: `KNOWLEDGE` and `SEMANTIC_LAYER` are **absent on purpose**. They map to
#: `database_connections`, which `CONNECTION` already counts, and listing a
#: connection three times would make the refusal read as if there were three
#: things to transfer.
_LABEL_COLUMN: dict[ResourceType, str] = {
    ResourceType.CONNECTION: "name",
    ResourceType.LLM_CONFIG: "name",
    ResourceType.DASHBOARD: "name",
    ResourceType.REPORT: "name",
    ResourceType.CONVERSATION: "title",
}
