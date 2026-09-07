"""Roles: what they carry, who holds them, and what that resolves to.

Two jobs, and the second is the one everything else in the product depends on:

* **CRUD over `roles`**, with three refusals that are the whole reason this is
  a service rather than four routes doing `db.add`: a system role will not have
  its capability set edited, a role that people hold will not be deleted, and
  the last administrator will not be left without the role.
* **`resolve_capabilities(principal_id)` — one query, on every request.** It is
  called from `get_ctx` before the handler runs, so it is on the hot path for
  literally every authenticated call in the product, and a version of it that
  loaded roles and then their capabilities per role would turn one round trip
  into N+1 for the rest of the plan's life.

**Capabilities are resolved from the database, never from the token** (plan
decision 15). The cost is one indexed join per request. What it buys is that
revoking a role takes effect on the **next request** rather than at the next
token refresh — the difference between "we removed their access" and "we
removed their access, up to fifteen minutes from now", which is not a sentence
anybody wants to say during an incident.

**An unknown capability string is ignored with a warning, never raised.** The
column is `varchar` and the enum is closed in code precisely so that a
downgrade — new rows, older code — degrades to *fewer* permissions rather than
to a 500 on sign-in. Fail-closed and stay up.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import RequestContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.value_objects import Role as LegacyRole
from app.domain.value_objects.authz import (
    ADMINISTRATOR,
    NORMAL_USER,
    Capability,
    Privilege,
    ResourceType,
)
from app.infra.db.models import (
    Role,
    RoleAssignment,
    RoleCapability,
    RoleScopedPrivilege,
    User,
)
from app.services import audit

log = get_logger(__name__)

#: Audit vocabulary for this module, in one place for the same reason
#: `services/audit.py` keeps the curation verbs in one place: an administrator
#: reading the log should be able to enumerate what can appear in it without
#: reading the routers.
ROLE = "role"
ROLE_CREATED = "role.created"
ROLE_UPDATED = "role.updated"
ROLE_DELETED = "role.deleted"
ROLE_ASSIGNED = "role.assigned"
ROLE_UNASSIGNED = "role.unassigned"


def known_capabilities(names: Iterable[str]) -> frozenset[Capability]:
    """The `Capability` members among `names`; anything else is dropped.

    Warned about rather than raised on — see the module docstring. The warning
    is per *word*, not per row, because the interesting event is "this
    deployment does not know `audit.export`", which is one fact however many
    roles carry it.
    """
    out: set[Capability] = set()
    for name in names:
        try:
            out.add(Capability(name))
        except ValueError:
            log.warning("unknown_capability_ignored", capability=name[:60])
    return frozenset(out)


def known_scoped(pairs: Iterable[tuple[str, str]]) -> frozenset[tuple[ResourceType, Privilege]]:
    """The `(ResourceType, Privilege)` pairs among `pairs`. Same posture."""
    out: set[tuple[ResourceType, Privilege]] = set()
    for resource_type, privilege in pairs:
        try:
            out.add((ResourceType(resource_type), Privilege(privilege)))
        except ValueError:
            log.warning(
                "unknown_scoped_privilege_ignored",
                resource_type=resource_type[:30],
                privilege=privilege[:20],
            )
    return frozenset(out)


class RoleService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── resolution: the hot path ─────────────────────────────────────────
    async def resolve_capabilities(self, principal_id: UUID) -> frozenset[Capability]:
        """Every capability this principal holds, in **one** query.

        `role_assignments → roles → role_capabilities`, joined and returned as
        a set of strings, then narrowed to the ones this build knows. A test
        asserts the statement count, because this runs on every authenticated
        request and an N+1 here is a per-request N+1 for the whole product.

        Teams widen this in Phase 4 — the union gains a second arm and the
        query count does not change.
        """
        result = await self._db.execute(
            select(RoleCapability.capability)
            .join(Role, Role.id == RoleCapability.role_id)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(RoleAssignment.user_id == principal_id)
        )
        return known_capabilities(result.scalars().all())

    async def roles_of(self, principal_id: UUID) -> list[Role]:
        """The roles reaching this principal, by name — for `/auth/me`."""
        result = await self._db.execute(
            select(Role)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(RoleAssignment.user_id == principal_id)
            .order_by(Role.name)
        )
        return list(result.scalars())

    # ── reading ──────────────────────────────────────────────────────────
    async def list(self) -> list[Role]:
        result = await self._db.execute(select(Role).order_by(Role.name))
        return list(result.scalars())

    async def get(self, role_id: UUID) -> Role:
        """The role, **with its capability and privilege rows attached**.

        Loaded through an explicit `selectinload` with `populate_existing`
        rather than `db.get`, and both halves matter. The eager load makes the
        two collections arrive as part of an awaited query — a lazy load under
        asyncio is a `MissingGreenlet`, not a query. `populate_existing`
        refreshes them on an instance the session is already holding, which is
        the case that bites right after a write: the role was put in the
        identity map with empty collections, and its child rows were added
        beside it rather than through them.
        """
        result = await self._db.execute(
            select(Role)
            .where(Role.id == role_id)
            .options(
                selectinload(Role.capabilities),
                selectinload(Role.scoped_privileges),
            )
            .execution_options(populate_existing=True)
        )
        role = result.scalar_one_or_none()
        if role is None:
            raise NotFoundError("Role not found.")
        return role

    async def by_name(self, name: str) -> Role | None:
        result = await self._db.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    async def holder_counts(self) -> dict[UUID, int]:
        """How many principals hold each role, for the list screen.

        One grouped query rather than a count per row: the roles list is eight
        rows on a fresh install and a dozen on a used one, and eight queries to
        draw one table is how a list page becomes slow for no reason.
        """
        result = await self._db.execute(
            select(RoleAssignment.role_id, func.count())
            .group_by(RoleAssignment.role_id)
        )
        return dict(result.all())

    # ── writing ──────────────────────────────────────────────────────────
    async def create(
        self,
        ctx: RequestContext,
        *,
        name: str,
        description: str = "",
        capabilities: Sequence[str] = (),
        scoped_privileges: Sequence[tuple[str, str]] = (),
    ) -> Role:
        """A custom role. Never `is_system` — that flag belongs to migrations."""
        clean = name.strip()
        if not clean:
            raise ValidationError("A role needs a name.")
        if await self.by_name(clean) is not None:
            raise ConflictError("A role with that name already exists.")

        role = Role(
            id=uuid.uuid4(),
            name=clean,
            description=description.strip(),
            is_system=False,
        )
        self._db.add(role)
        await self._db.flush()
        await self._write_capabilities(role, capabilities)
        await self._write_scoped(role, scoped_privileges)

        await audit.record(
            self._db, ctx,
            action=ROLE_CREATED, resource_type=ROLE, resource_id=role.id,
            detail={
                "name": role.name,
                "capabilities": len(known_capabilities(capabilities)),
            },
        )
        return await self._with_rows(role)

    async def update(
        self,
        ctx: RequestContext,
        role_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        capabilities: Sequence[str] | None = None,
        scoped_privileges: Sequence[tuple[str, str]] | None = None,
    ) -> Role:
        """Rename any role; re-scope only a custom one.

        The split is the whole of decision 4 in migration `0024`. A system
        role's **name** is a label an installation may legitimately dislike, so
        it is editable. Its **capability set** is the specification eight tests
        and one migration agree on, so changing it through the API would mean
        two installations running the same version disagreed about what
        "Auditor" means — and the refusal says so rather than 403-ing silently.
        """
        role = await self.get(role_id)
        changing_permissions = capabilities is not None or scoped_privileges is not None
        if role.is_system and changing_permissions:
            raise ValidationError(
                f"“{role.name}” is a built-in role: its name and description can "
                "be changed, but what it can do is fixed. Create a custom role "
                "to define a different set of permissions."
            )

        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValidationError("A role needs a name.")
            if clean != role.name:
                clash = await self.by_name(clean)
                if clash is not None:
                    raise ConflictError("A role with that name already exists.")
                role.name = clean
        if description is not None:
            role.description = description.strip()
        if capabilities is not None:
            await self._write_capabilities(role, capabilities, replace=True)
        if scoped_privileges is not None:
            await self._write_scoped(role, scoped_privileges, replace=True)

        await audit.record(
            self._db, ctx,
            action=ROLE_UPDATED, resource_type=ROLE, resource_id=role.id,
            detail={
                "name": role.name,
                "permissions_changed": changing_permissions,
            },
        )
        return await self._with_rows(role)

    async def delete(self, ctx: RequestContext, role_id: UUID) -> None:
        """Refused for a system role, and refused while anybody holds it.

        The second refusal **names the holders**. `ON DELETE RESTRICT` would
        raise on its own, but as an `IntegrityError` the caller can only report
        as "something went wrong" — and the useful sentence is *"three people
        hold this; remove it from them first"*.
        """
        role = await self.get(role_id)
        if role.is_system:
            raise ValidationError(
                f"“{role.name}” is a built-in role and cannot be deleted."
            )

        holders = await self._holders(role_id)
        if holders:
            shown = ", ".join(holders[:5])
            more = f" and {len(holders) - 5} more" if len(holders) > 5 else ""
            raise ConflictError(
                f"“{role.name}” is still assigned to {shown}{more}. "
                "Remove it from them first."
            )

        await audit.record(
            self._db, ctx,
            action=ROLE_DELETED, resource_type=ROLE, resource_id=role.id,
            detail={"name": role.name},
        )
        await self._db.delete(role)
        await self._db.flush()

    # ── assignment ───────────────────────────────────────────────────────
    async def assign(
        self, ctx: RequestContext, *, user_id: UUID, role_id: UUID
    ) -> RoleAssignment:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        role = await self.get(role_id)

        existing = await self._db.execute(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == role_id,
            )
        )
        held = existing.scalar_one_or_none()
        if held is not None:
            # Idempotent rather than a conflict: pressing a toggle that is
            # already on is not an error, and the alternative makes every
            # client implement "check, then set" against a race.
            return held

        assignment = RoleAssignment(
            id=uuid.uuid4(), user_id=user_id, role_id=role_id, created_by=ctx.user_id
        )
        self._db.add(assignment)
        await self._db.flush()
        await self._sync_legacy_role(user)
        await audit.record(
            self._db, ctx,
            action=ROLE_ASSIGNED, resource_type=ROLE, resource_id=role_id,
            detail={"role": role.name, "user_id": str(user_id)},
        )
        return assignment

    async def unassign(
        self, ctx: RequestContext, *, user_id: UUID, role_id: UUID
    ) -> None:
        user = await self._db.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        role = await self.get(role_id)
        await self.guard_last_administrator(user_id, losing=role_id)

        await self._db.execute(
            delete(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role_id == role_id,
            )
        )
        await self._db.flush()
        await self._sync_legacy_role(user)
        await audit.record(
            self._db, ctx,
            action=ROLE_UNASSIGNED, resource_type=ROLE, resource_id=role_id,
            detail={"role": role.name, "user_id": str(user_id)},
        )

    async def guard_last_administrator(
        self, user_id: UUID, *, losing: UUID | None = None
    ) -> None:
        """A workspace with no administrator cannot be recovered from the UI.

        Counted over **assignments**, not over `users.role`, because that
        column is now a cache: an installation whose administrators were all
        made through the roles screen would have a correct `role_assignments`
        table and a stale enum, and a guard reading the stale one would happily
        strand the workspace.

        `losing` is the assignment about to disappear, so the count answers
        *"who would still hold it afterwards"* rather than *"who holds it
        now"* — the same question `_guard_last_admin` asked, over the table
        that now knows.
        """
        administrator = await self.by_name(ADMINISTRATOR)
        if administrator is None:  # pragma: no cover - the seed is a migration
            return
        if losing is not None and losing != administrator.id:
            return

        result = await self._db.execute(
            select(RoleAssignment.user_id).where(
                RoleAssignment.role_id == administrator.id,
                RoleAssignment.user_id != user_id,
            )
        )
        if not result.scalars().first():
            raise ValidationError(
                "This is the only administrator; promote another first."
            )

    # ── internals ────────────────────────────────────────────────────────
    async def _with_rows(self, role: Role) -> Role:
        """The role as it now stands, after its child rows were written.

        Not a tidiness. Child rows are added beside the parent rather than
        through its collections, so the object a caller is holding still says
        "no capabilities" — and `POST /roles` answers 500 (a lazy load under
        asyncio) while `PATCH` answers with the role as it was *before* the
        edit. Both were real, and neither appears against a synchronous test
        session, which lazy-loads happily; only a real async one shows them.

        The id is read **before** the flush, because an expired instance
        cannot even be asked what its primary key is without doing IO.
        """
        role_id = role.id
        await self._db.flush()
        return await self.get(role_id)

    async def _holders(self, role_id: UUID) -> list[str]:
        """Display names of everyone holding this role, for the refusal."""
        result = await self._db.execute(
            select(User.display_name, User.email)
            .join(RoleAssignment, RoleAssignment.user_id == User.id)
            .where(RoleAssignment.role_id == role_id)
            .order_by(User.display_name)
        )
        return [(name or email) for name, email in result.all()]

    async def _write_capabilities(
        self, role: Role, capabilities: Sequence[str], *, replace: bool = False
    ) -> None:
        if replace:
            await self._db.execute(
                delete(RoleCapability).where(RoleCapability.role_id == role.id)
            )
        # Narrowed through the enum on the way *in* as well as on the way out:
        # the open column exists so an older build survives a newer row, not so
        # the API can invent vocabulary.
        for capability in sorted(known_capabilities(capabilities)):
            self._db.add(
                RoleCapability(role_id=role.id, capability=str(capability))
            )

    async def _write_scoped(
        self,
        role: Role,
        pairs: Sequence[tuple[str, str]],
        *,
        replace: bool = False,
    ) -> None:
        if replace:
            await self._db.execute(
                delete(RoleScopedPrivilege).where(
                    RoleScopedPrivilege.role_id == role.id
                )
            )
        for resource_type, privilege in sorted(known_scoped(pairs)):
            self._db.add(
                RoleScopedPrivilege(
                    role_id=role.id,
                    resource_type=str(resource_type),
                    privilege=str(privilege),
                )
            )

    async def _sync_legacy_role(self, user: User) -> None:
        """Keep `users.role` true as a **cache**, so a rollback is a flag flip.

        Nothing reads it to decide anything any more — `make authz-check`
        enforces that — but the column is still in the schema until Phase 10,
        the SPA still renders a chip from it, and a deployment that rolled back
        to a build without `roles` would otherwise find every account demoted
        to MEMBER. Written from the assignments, which are now the truth.
        """
        administrator = await self.by_name(ADMINISTRATOR)
        if administrator is None:  # pragma: no cover - the seed is a migration
            return
        result = await self._db.execute(
            select(RoleAssignment.id).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_id == administrator.id,
            )
        )
        user.role = (
            LegacyRole.ADMIN if result.scalars().first() else LegacyRole.MEMBER
        )


async def assign_by_name(
    db: AsyncSession, *, user_id: UUID, role_name: str = NORMAL_USER
) -> None:
    """Give a principal a role by name, with no context and no audit row.

    For the two paths that create an account rather than administer one:
    `bootstrap.ensure_admin`, which runs before anybody has signed in, and
    `POST /users`, whose audit story is the invitation rather than the
    assignment. Silent when the role is missing, which on a database that has
    not run `0024` is the truth rather than a failure worth crashing boot for.
    """
    result = await db.execute(select(Role.id).where(Role.name == role_name))
    role_id = result.scalar_one_or_none()
    if role_id is None:
        log.warning("role_seed_missing", role=role_name)
        return
    already = await db.execute(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == user_id, RoleAssignment.role_id == role_id
        )
    )
    if already.scalars().first() is not None:
        return
    db.add(RoleAssignment(id=uuid.uuid4(), user_id=user_id, role_id=role_id))
