"""HTTP shape for roles. No business logic — see `services/role_service.py`.

Two guards and no third: reading is `role.read`, writing is `role.manage`, and
both arrive through `deps.needs(...)` so the check runs before the handler body
and cannot be forgotten by whoever adds the next route. That is the answer to
OWASP API1:2023, and it is the reason there is no `if ctx.…` anywhere below.

**The capability catalog is served, not hardcoded in the SPA.** The eighteen
words are a closed enum in the backend; a checklist that shipped its own copy
would, on the day a nineteenth arrived, be a permission nobody could grant and
nobody could see was missing.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbDep, RoleManageDep, RoleReadDep
from app.api.schemas import (
    CapabilityCatalogEntry,
    RoleCreate,
    RoleRead,
    RoleWrite,
    ScopedPrivilegeRead,
)
from app.domain.value_objects.authz import PRIVILEGE_MEANINGS, Capability
from app.infra.db.models import Role
from app.services.role_service import RoleService

router = APIRouter(prefix="/roles", tags=["roles"])

#: The four groups of §12.1, and which capability belongs to which. Written out
#: rather than derived from the enum's declaration order, because the grouping
#: is a *reading* of the list — "people", "oversight", "creation", "system" —
#: and an order that happened to match today is not the same as a statement.
_GROUPS: dict[Capability, str] = {
    Capability.USER_READ: "People",
    Capability.USER_MANAGE: "People",
    Capability.SERVICE_USER_MANAGE: "People",
    Capability.TEAM_READ: "People",
    Capability.TEAM_MANAGE: "People",
    Capability.ROLE_READ: "People",
    Capability.ROLE_MANAGE: "People",
    Capability.AUDIT_READ: "Oversight",
    Capability.ACCESS_REVIEW: "Oversight",
    Capability.CONNECTION_CREATE: "Creation",
    Capability.LLM_CONFIG_CREATE: "Creation",
    Capability.DASHBOARD_CREATE: "Creation",
    Capability.REPORT_CREATE: "Creation",
    Capability.CONVERSATION_CREATE: "Creation",
    Capability.SETTINGS_MANAGE: "System",
    Capability.BENCHMARK_MANAGE: "System",
    Capability.EVAL_RUN: "System",
    Capability.SYSTEM_MAINTENANCE: "System",
}

#: One sentence per capability, in the second person, because it is rendered
#: beside a checkbox somebody is about to tick on another person's behalf.
_LABELS: dict[Capability, str] = {
    Capability.USER_READ: "See the list of people and their accounts.",
    Capability.USER_MANAGE: "Invite, edit, disable and remove people.",
    Capability.SERVICE_USER_MANAGE: "Create service accounts and their API keys.",
    Capability.TEAM_READ: "See teams and who is in them.",
    Capability.TEAM_MANAGE: "Create teams and change their membership.",
    Capability.ROLE_READ: "See roles and what each one carries.",
    Capability.ROLE_MANAGE: "Create roles and assign them to people.",
    Capability.AUDIT_READ: "Read the audit log.",
    Capability.ACCESS_REVIEW: "Ask who can reach a resource, and why.",
    Capability.CONNECTION_CREATE: "Add database connections.",
    Capability.LLM_CONFIG_CREATE: "Add model providers.",
    Capability.DASHBOARD_CREATE: "Create dashboards.",
    Capability.REPORT_CREATE: "Create reports.",
    Capability.CONVERSATION_CREATE: "Use Chat.",
    Capability.SETTINGS_MANAGE: "Change installation settings.",
    Capability.BENCHMARK_MANAGE: "Create and run benchmark sets.",
    Capability.EVAL_RUN: "Run the evaluation harness.",
    Capability.SYSTEM_MAINTENANCE: "Run maintenance jobs and sweeps.",
}


def _read(role: Role, holders: int = 0) -> RoleRead:
    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        capabilities=sorted(row.capability for row in role.capabilities),
        scoped_privileges=sorted(
            (
                ScopedPrivilegeRead(
                    resource_type=row.resource_type, privilege=row.privilege
                )
                for row in role.scoped_privileges
            ),
            key=lambda p: (p.resource_type, p.privilege),
        ),
        holders=holders,
        created_at=role.created_at,
    )


@router.get("/capabilities", response_model=list[CapabilityCatalogEntry])
async def capability_catalog(ctx: RoleReadDep) -> list[CapabilityCatalogEntry]:
    """The eighteen words a role can carry, grouped and explained.

    Declared above `/{role_id}` so the literal path wins the match.
    """
    return [
        CapabilityCatalogEntry(
            name=str(capability),
            group=_GROUPS[capability],
            label=_LABELS[capability],
        )
        for capability in Capability
    ]


@router.get("/privileges", response_model=dict[str, dict[str, str]])
async def privilege_matrix(ctx: RoleReadDep) -> dict[str, dict[str, str]]:
    """What each privilege means on each resource type — §13.3, as data.

    The scoped-privilege matrix in the role editor renders from this, so the
    sentence beside a radio button is the same sentence the backend's own
    conformance test asserts. A UI that wrote its own wording would drift, and
    the drift would be invisible until somebody granted the wrong thing.
    """
    return {
        str(resource_type): {
            str(privilege): meaning for privilege, meaning in meanings.items()
        }
        for resource_type, meanings in PRIVILEGE_MEANINGS.items()
    }


@router.get("", response_model=list[RoleRead])
async def list_roles(ctx: RoleReadDep, db: DbDep) -> list[RoleRead]:
    service = RoleService(db)
    counts = await service.holder_counts()
    return [_read(role, counts.get(role.id, 0)) for role in await service.list()]


@router.get("/{role_id}", response_model=RoleRead)
async def get_role(role_id: UUID, ctx: RoleReadDep, db: DbDep) -> RoleRead:
    service = RoleService(db)
    role = await service.get(role_id)
    return _read(role, (await service.holder_counts()).get(role.id, 0))


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate, ctx: RoleManageDep, db: DbDep
) -> RoleRead:
    role = await RoleService(db).create(
        ctx,
        name=payload.name,
        description=payload.description or "",
        capabilities=payload.capabilities or [],
        scoped_privileges=[
            (p.resource_type, p.privilege) for p in (payload.scoped_privileges or [])
        ],
    )
    return _read(role)


@router.patch("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: UUID, payload: RoleWrite, ctx: RoleManageDep, db: DbDep
) -> RoleRead:
    """Rename any role; re-scope only a custom one.

    A system role refuses a capability edit **with an explanation**, not a bare
    403: the person doing it is an administrator who is allowed to change
    roles, and the only useful answer names the thing they should do instead —
    make a custom role.
    """
    service = RoleService(db)
    role = await service.update(
        ctx,
        role_id,
        name=payload.name,
        description=payload.description,
        capabilities=payload.capabilities,
        scoped_privileges=(
            None
            if payload.scoped_privileges is None
            else [(p.resource_type, p.privilege) for p in payload.scoped_privileges]
        ),
    )
    return _read(role, (await service.holder_counts()).get(role.id, 0))


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: UUID, ctx: RoleManageDep, db: DbDep) -> None:
    await RoleService(db).delete(ctx, role_id)

