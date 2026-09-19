"""HTTP shape for connection sections. No business logic — see
`services/section_service.py`.

Mounted under `/connections/{connection_id}/sections`, and — unlike the
semantic layer — asked about as the **connection** itself: a section is a leaf
of its connection with no audience of its own, so it takes no resource type,
no privilege and no grant (`docs/plans/retrieval-sections.md` §10). Reading or
proposing is `select` on the connection; changing the division is `modify`,
the same privilege that re-syncs the schema it divides.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AuthzDep, CtxDep, DbDep
from app.api.schemas import (
    SectionCatalogEntry,
    SectionProposeRequest,
    SectionRead,
    SectionSaveRequest,
    SectionSetRead,
)
from app.core.errors import NotFoundError
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.db.models import DatabaseConnection
from app.services.policy import require
from app.services.section_service import SectionService, SectionSet, SectionWrite

router = APIRouter(prefix="/connections/{connection_id}/sections", tags=["sections"])


async def _authorized(
    db, authz, connection_id: UUID, ctx, privilege: Privilege
) -> DatabaseConnection:
    """The connection, if this principal holds `privilege` on it — 404 above
    403, through `require`."""
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise NotFoundError("Connection not found.")
    await require(
        ctx,
        authz,
        ResourceRef(type=ResourceType.CONNECTION, id=connection.id, entity=connection),
        privilege,
        db=db,
    )
    return connection


def _read(result: SectionSet) -> SectionSetRead:
    return SectionSetRead(
        saved=result.saved,
        sections=[
            SectionRead(
                id=s.id, name=s.name, description=s.description, tables=s.tables,
                origin=s.origin, schema_version=s.schema_version,  # type: ignore[arg-type]
                position=s.position, chars=s.chars, fit=s.fit, missing=s.missing,
            )
            for s in result.sections
        ],
        unassigned=result.unassigned,
        catalog=[SectionCatalogEntry(table=t, chars=c) for t, c in result.catalog],
        budget_chars=result.budget_chars,
        snapshot_version=result.snapshot_version,
        has_snapshot=result.has_snapshot,
    )


@router.get("", response_model=SectionSetRead)
async def list_sections(
    connection_id: UUID, ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> SectionSetRead:
    """What is saved. Nothing saved is `saved: false` and no sections — the
    screen asks for a proposal itself, so a read never computes one."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    return _read(await SectionService(db).read(connection))


# A literal path, so above anything `/{section_id}`-shaped.
@router.post("/propose", response_model=SectionSetRead)
async def propose_sections(
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    authz: AuthzDep,
    payload: SectionProposeRequest | None = None,
) -> SectionSetRead:
    """A deterministic division of the snapshot. **Writes nothing** — which is
    why it is `select`: anybody who may read the schema may see how it would
    be divided."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
    only = payload.tables if payload is not None else None
    return _read(await SectionService(db).propose(connection, only=only))


@router.put("", response_model=SectionSetRead)
async def save_sections(
    connection_id: UUID,
    payload: SectionSaveRequest,
    ctx: CtxDep,
    db: DbDep,
    authz: AuthzDep,
) -> SectionSetRead:
    """Replace the whole set, in one transaction."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    saved = await SectionService(db).save(
        connection,
        [
            SectionWrite(
                id=item.id, name=item.name, description=item.description,
                tables=item.tables,
            )
            for item in payload.sections
        ],
    )
    return _read(saved)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_sections(
    connection_id: UUID, ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> None:
    """Every section — the feature off for this connection."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    await SectionService(db).clear(connection)


@router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(
    connection_id: UUID, section_id: UUID, ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> None:
    """One section. Its tables go to Unassigned."""
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
    await SectionService(db).delete(connection, section_id)
