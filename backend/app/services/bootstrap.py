"""Idempotent admin bootstrap. Nothing in the domain knows this exists.

It creates the account **and gives it the Administrator role**, because as of
Phase 3 those are two different facts and only the second one grants anything.
An installation whose first account had `users.role = 'ADMIN'` and no row in
`role_assignments` would boot with nobody able to reach the administration
screens — which is the specific failure this function exists to prevent.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.value_objects import Role, UserStatus
from app.domain.value_objects.authz import ADMINISTRATOR
from app.infra.db.models import User
from app.infra.identity.local import LocalIdentityProvider
from app.services.role_service import assign_by_name

log = get_logger(__name__)


async def ensure_admin(db: AsyncSession, settings: Settings) -> None:
    email = settings.admin_email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        return

    provider = LocalIdentityProvider(db, settings)
    password = settings.admin_password.get_secret_value()
    admin = User(
        id=uuid.uuid4(),
        email=email,
        display_name=settings.admin_display_name,
        password_hash=provider.hash_password(password),
        role=Role.ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(admin)
    await db.flush()
    # In the same transaction as the account, so the installation can never
    # come up with an administrator who is not one.
    await assign_by_name(db, user_id=admin.id, role_name=ADMINISTRATOR)
    await db.commit()

    if password == "raymand":  # noqa: S105  (comparing against the known default to warn)
        log.warning(
            "admin_bootstrap_default_password",
            message="The bootstrap admin is using the default password. "
                    "Change ADMIN_PASSWORD before exposing this deployment.",
            email=email,
        )
    else:
        log.info("admin_bootstrap_created", email=email)
