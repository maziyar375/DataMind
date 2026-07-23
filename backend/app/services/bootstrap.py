"""Idempotent admin bootstrap. Nothing in the domain knows this exists."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.value_objects import Role, UserStatus
from app.infra.db.models import User
from app.infra.identity.local import LocalIdentityProvider

log = get_logger(__name__)


async def ensure_admin(db: AsyncSession, settings: Settings) -> None:
    email = settings.admin_email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        return

    provider = LocalIdentityProvider(db, settings)
    password = settings.admin_password.get_secret_value()
    db.add(
        User(
            id=uuid.uuid4(),
            email=email,
            display_name=settings.admin_display_name,
            password_hash=provider.hash_password(password),
            role=Role.ADMIN,
            status=UserStatus.ACTIVE,
        )
    )
    await db.commit()

    if password == "raymand":
        log.warning(
            "admin_bootstrap_default_password",
            message="The bootstrap admin is using the default password. "
                    "Change ADMIN_PASSWORD before exposing this deployment.",
            email=email,
        )
    else:
        log.info("admin_bootstrap_created", email=email)
