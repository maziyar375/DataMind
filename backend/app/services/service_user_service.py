"""Machine identities: creating one, and the four capabilities it may not hold.

A service user is a `users` row with `kind='SERVICE'` — see migration `0026`
for why one table rather than two. Everything about roles, teams and (from
Phase 6) grants therefore works on it unchanged, and a test asserts the
equality that follows: **a service user with the BI Engineer role has
byte-identical capabilities to a human with it.** That equality is the design,
not a coincidence; if the two ever diverged, an agent's reach would be a second
permission model nobody was reviewing.

Four things live here rather than in the router, and each is a refusal:

* **`allow_privileged_service_users`.** A machine may not hold `user.manage`,
  `role.manage`, `service_user.manage` or `settings.manage` while that flag is
  off, which is the default. The reason is blast radius rather than tidiness:
  a leaked key must not be able to mint an administrator, and *"the agent
  creates the accounts"* is a real requirement in some installations, which is
  why it is a flag rather than an invariant. Turning it on is a deployment
  decision; **using** it writes an audit row naming the capability.
* **The synthetic address.** `svc-<slug>@service.datamind.local`, generated and
  never typed. `users.email` stays `NOT NULL UNIQUE`, so every existing join,
  uniqueness check and display path keeps working; `.local` means a
  misconfigured mailer cannot deliver anywhere real.
* **The description is required.** An undocumented machine identity is the one
  nobody dares delete, so *"what is this for"* is a field on the form and a
  refusal in the service. It is deliberately not a `CHECK`, because most humans
  legitimately have none.
* **Deleting one is not the same as disabling it.** `DISABLED` keeps every
  role, team and key — a disabled key fails verification through the principal
  check, and re-enabling must not be a re-issue.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import RequestContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.ports.identity import IssuedKey
from app.domain.value_objects import UserStatus
from app.domain.value_objects.authz import (
    PRIVILEGED_CAPABILITIES,
    Capability,
    PrincipalKind,
)
from app.infra.db.models import (
    Role,
    RoleAssignment,
    RoleCapability,
    ServiceCredential,
    User,
)
from app.infra.identity.service_key import ServiceKeyProvider
from app.services import audit
from app.services.role_service import RoleService

log = get_logger(__name__)

#: The six service actions, in one place — for the same reason `role_service`
#: and `team_service` keep theirs there: an administrator reading the log
#: should be able to enumerate what can appear in it without reading routers.
SERVICE_USER = "service_user"
SERVICE_CREDENTIAL = "service_credential"
SERVICE_USER_CREATED = "service_user.created"
SERVICE_USER_DISABLED = "service_user.disabled"
SERVICE_USER_DELETED = "service_user.deleted"
CREDENTIAL_ISSUED = "service_credential.issued"  # noqa: S105  (an action name)
CREDENTIAL_REVOKED = "service_credential.revoked"  # noqa: S105  (an action name)
CREDENTIAL_EXPIRED = "service_credential.expired"  # noqa: S105  (an action name)
#: A seventh, beyond the six §19.4 names, and it earns its own word: it records
#: the moment `allow_privileged_service_users` was actually *used* — a machine
#: acquiring one of the four capabilities a leaked key must not reach. Filing it
#: under `service_user.created` would hide the one event an operator would want
#: to search for by name.
SERVICE_USER_PRIVILEGED = "service_user.privileged"

#: The domain every machine identity's synthetic address sits under. Not
#: routable, by design: `.local` is reserved for exactly this, so nothing that
#: reads `users.email` and tries to send to it can reach anywhere real.
SERVICE_EMAIL_DOMAIN = "service.datamind.local"


def service_email(name: str, discriminator: str) -> str:
    """`svc-<slug>-<discriminator>@service.datamind.local`.

    The discriminator is there because display names collide — two people will
    name an agent `reports` — and `users.email` is `UNIQUE`. Colliding on it
    would surface as *"a user with that email already exists"* on a form with
    no email field, which is the worst refusal in the product.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "agent"
    return f"svc-{slug}-{discriminator}@{SERVICE_EMAIL_DOMAIN}"


class ServiceUserService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._keys = ServiceKeyProvider(db, settings)
        self._roles = RoleService(db)

    # ── reading ──────────────────────────────────────────────────────────
    async def list(self) -> list[User]:
        result = await self._db.execute(
            select(User)
            .where(User.kind == PrincipalKind.SERVICE)
            .order_by(User.display_name)
        )
        return list(result.scalars())

    async def get(self, service_user_id: UUID) -> User:
        """The machine identity, or 404 — **never a human by this id.**

        The narrowing is the point rather than a filter: `/service-accounts/{id}`
        pointed at a person's id must not return that person's account, or the
        screen gated on `service_user.manage` becomes a second, quieter way to
        read the People list that `user.read` is supposed to gate.
        """
        user = await self._db.get(User, service_user_id)
        if user is None or user.kind != PrincipalKind.SERVICE:
            raise NotFoundError("Service account not found.")
        return user

    async def keys(self, service_user_id: UUID) -> list[ServiceCredential]:
        await self.get(service_user_id)
        return await self._keys.keys_of(service_user_id)

    # ── writing ──────────────────────────────────────────────────────────
    async def create(
        self,
        ctx: RequestContext,
        *,
        display_name: str,
        description: str,
        role_ids: Sequence[UUID] = (),
    ) -> User:
        """A machine identity with its roles. **No key is issued here.**

        Making an identity and handing out a secret are two deliberate acts,
        and a create that returned a key would collapse them — the key would
        exist in whatever created the account, which for a provisioning script
        is a file. Keys come from the detail page, one at a time, each shown
        once.
        """
        name = display_name.strip()
        if not name:
            raise ValidationError("A service account needs a name.")
        purpose = description.strip()
        if not purpose:
            raise ValidationError(
                "Say what this service account is for. An undocumented machine "
                "identity is the one nobody is willing to delete later."
            )

        for role_id in role_ids:
            await self._guard_privileged(ctx, role_id, name=name)

        user = User(
            id=uuid.uuid4(),
            email=service_email(name, uuid.uuid4().hex[:8]),
            display_name=name,
            description=purpose,
            kind=PrincipalKind.SERVICE,
            # Explicitly none of the three things the `CHECK`s forbid, written
            # out rather than left to a column default, because the row is the
            # place somebody reads to learn what a service user is.
            password_hash=None,
            external_subject=None,
            must_change_password=False,
            status=UserStatus.ACTIVE,
        )
        self._db.add(user)
        await self._db.flush()

        for role_id in role_ids:
            await self._roles.assign(ctx, user_id=user.id, role_id=role_id)

        await audit.record(
            self._db, ctx,
            action=SERVICE_USER_CREATED,
            resource_type=SERVICE_USER, resource_id=user.id,
            detail={"name": name, "roles": len(role_ids)},
        )
        return user

    async def update(
        self,
        ctx: RequestContext,
        service_user_id: UUID,
        *,
        display_name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> User:
        """Rename, re-describe, disable, re-enable.

        Disabling is audited and deleting is a different call, because they are
        different acts: a disabled identity keeps every role, team and key, and
        re-enabling it must not be a re-grant. Power BI documents the same
        choice for the same reason.
        """
        user = await self.get(service_user_id)
        if display_name is not None:
            clean = display_name.strip()
            if not clean:
                raise ValidationError("A service account needs a name.")
            user.display_name = clean
        if description is not None:
            clean_description = description.strip()
            if not clean_description:
                raise ValidationError("Say what this service account is for.")
            user.description = clean_description
        if status is not None and status != user.status:
            user.status = status
            if status == UserStatus.DISABLED:
                await audit.record(
                    self._db, ctx,
                    action=SERVICE_USER_DISABLED,
                    resource_type=SERVICE_USER, resource_id=user.id,
                    detail={"name": user.display_name},
                )
        await self._db.flush()
        return user

    async def delete(self, ctx: RequestContext, service_user_id: UUID) -> None:
        """Delete the identity; its keys go with it by `ON DELETE CASCADE`.

        The audit row is written **before** the delete, so it names what was
        removed rather than pointing at an id nothing resolves. Phase 6 adds
        the second refusal this needs — a principal that owns a grantable
        resource cannot be deleted until ownership moves.
        """
        user = await self.get(service_user_id)
        await audit.record(
            self._db, ctx,
            action=SERVICE_USER_DELETED,
            resource_type=SERVICE_USER, resource_id=user.id,
            detail={"name": user.display_name},
        )
        await self._db.delete(user)
        await self._db.flush()

    # ── roles ────────────────────────────────────────────────────────────
    async def assign_role(
        self, ctx: RequestContext, *, service_user_id: UUID, role_id: UUID
    ) -> None:
        """Give a machine a role, unless the role carries a privileged verb."""
        user = await self.get(service_user_id)
        await self._guard_privileged(ctx, role_id, name=user.display_name)
        await self._roles.assign(ctx, user_id=service_user_id, role_id=role_id)

    async def unassign_role(
        self, ctx: RequestContext, *, service_user_id: UUID, role_id: UUID
    ) -> None:
        await self.get(service_user_id)
        await self._roles.unassign(ctx, user_id=service_user_id, role_id=role_id)

    # ── keys ─────────────────────────────────────────────────────────────
    async def issue_key(
        self,
        ctx: RequestContext,
        *,
        service_user_id: UUID,
        name: str,
        expires_at: datetime | None = None,
        use_default_expiry: bool = True,
    ) -> IssuedKey:
        """Mint a key. **The token in the result is the only copy that leaves.**

        `use_default_expiry` distinguishes *"the caller named no expiry"* from
        *"the caller asked for none"*. The first gets a year; the second gets a
        key that never expires, which stays possible because some integrations
        genuinely cannot rotate — and is a deliberate choice an administrator
        makes rather than a default they fall into.
        """
        user = await self.get(service_user_id)
        if user.status == UserStatus.DISABLED:
            raise ConflictError(
                "This service account is disabled. Re-enable it before issuing "
                "a key — a key issued to a disabled account cannot be used."
            )
        clean = name.strip()
        if not clean:
            raise ValidationError("A key needs a name, so it can be revoked by one.")

        if expires_at is None and use_default_expiry:
            expires_at = self._keys.default_expiry()

        issued = await self._keys.issue_key(
            service_user_id, name=clean, expires_at=expires_at,
            created_by=ctx.user_id,
        )
        await audit.record(
            self._db, ctx,
            action=CREDENTIAL_ISSUED,
            resource_type=SERVICE_CREDENTIAL, resource_id=issued.credential_id,
            # The prefix, never the key. It is the clear half by construction —
            # it is what makes a leaked key traceable — and putting it here is
            # what lets an administrator match a key found in a log to the row
            # that issued it.
            detail={
                "service_user_id": str(service_user_id),
                "name": clean,
                "prefix": issued.prefix,
                "expires_at": issued.expires_at.isoformat() if issued.expires_at else None,
            },
        )
        return issued

    async def revoke_key(
        self, ctx: RequestContext, *, service_user_id: UUID, credential_id: UUID
    ) -> None:
        """Revoke one key. It fails the **next** request, not in fifteen minutes.

        The credential is re-read against the principal rather than trusted
        from the path: `/service-accounts/{a}/keys/{b}` where `b` belongs to
        another account would otherwise revoke somebody else's key from a
        screen that showed neither.
        """
        await self.get(service_user_id)
        row = await self._db.get(ServiceCredential, credential_id)
        if row is None or row.service_user_id != service_user_id:
            raise NotFoundError("Key not found.")

        await self._keys.revoke_key(credential_id)
        await audit.record(
            self._db, ctx,
            action=CREDENTIAL_REVOKED,
            resource_type=SERVICE_CREDENTIAL, resource_id=credential_id,
            detail={
                "service_user_id": str(service_user_id),
                "name": row.name,
                "prefix": row.prefix,
            },
        )

    # ── the policy ───────────────────────────────────────────────────────
    async def _guard_privileged(
        self, ctx: RequestContext, role_id: UUID, *, name: str
    ) -> None:
        """A machine may not mint an administrator.

        Enforced here rather than in the database because it is a *policy*: an
        installation running its own provisioning agent has a real reason to
        turn it on, and an invariant would leave them forking the product. What
        the flag does not do is make the choice quiet — using it writes an
        audit row naming the capability and the role, so *"when did an agent
        acquire user.manage"* is answerable from the log.
        """
        privileged = await self._privileged_capabilities_of(role_id)
        if not privileged:
            return

        listed = ", ".join(sorted(str(c) for c in privileged))
        if not self._settings.allow_privileged_service_users:
            raise ValidationError(
                f"That role carries {listed}, which a service account may not "
                "hold: a leaked API key must not be able to change who can sign "
                "in. Set ALLOW_PRIVILEGED_SERVICE_USERS to allow it, or give "
                "this account a role without those permissions."
            )
        await audit.record(
            self._db, ctx,
            action=SERVICE_USER_PRIVILEGED,
            resource_type="role", resource_id=role_id,
            detail={
                "name": name,
                "privileged_capabilities": sorted(str(c) for c in privileged),
                "allowed_by_setting": True,
            },
        )
        log.warning(
            "privileged_service_capability_allowed",
            capabilities=sorted(str(c) for c in privileged),
        )

    async def _privileged_capabilities_of(
        self, role_id: UUID
    ) -> frozenset[Capability]:
        """Which of the four this role carries. One query, no role load.

        Read from `role_capabilities` directly rather than through
        `RoleService.get`, because the question is about four strings and
        loading the role's whole object graph to answer it would be an eager
        load per assignment.
        """
        result = await self._db.execute(
            select(RoleCapability.capability)
            .join(Role, Role.id == RoleCapability.role_id)
            .where(Role.id == role_id)
        )
        held = set()
        for name in result.scalars():
            try:
                held.add(Capability(name))
            except ValueError:
                # An unknown word cannot be one of the four, and this module is
                # not the place that warns about it — `role_service` already
                # does, once per word, on the hot path.
                continue
        return frozenset(held & PRIVILEGED_CAPABILITIES)


async def is_service(db: AsyncSession, user_id: UUID) -> bool:
    """Is this principal a machine? One column, for the `/auth/*` refusals."""
    result = await db.execute(select(User.kind).where(User.id == user_id))
    return result.scalar_one_or_none() == PrincipalKind.SERVICE


async def roles_carrying_privilege(db: AsyncSession) -> frozenset[UUID]:
    """Every role that carries at least one of the four privileged verbs.

    For the create form, which hides them from the role picker and says why
    (plan §21.5). The server refuses them regardless — this only stops
    somebody filling in a form whose Save is going to be refused.
    """
    result = await db.execute(
        select(RoleCapability.role_id).where(
            RoleCapability.capability.in_(sorted(str(c) for c in PRIVILEGED_CAPABILITIES))
        )
    )
    return frozenset(result.scalars())


async def role_ids_of(db: AsyncSession, principal_id: UUID) -> list[UUID]:
    """The role ids reaching this principal directly. For the detail pane."""
    result = await db.execute(
        select(RoleAssignment.role_id).where(RoleAssignment.user_id == principal_id)
    )
    return list(result.scalars())
