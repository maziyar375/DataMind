"""HTTP shape for dashboards. No business logic — see
`services/dashboard_service.py`.

Two things about the route table are deliberate:

* **Literal segments are declared above `/{...}` ones** (`/data` and `/layout`
  before nothing, but `/tiles/{tile_id}/data` after `/tiles/{tile_id}`), the
  house rule that keeps a literal from being swallowed by a path parameter.
* **Reading a dashboard returns no results.** `GET /dashboards/{id}` is the
  layout; the numbers come from `POST .../data`, because every tile is on its
  own clock and the browser asks for the ones that are due.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    DashboardCreate,
    DashboardDataRead,
    DashboardDataRequest,
    DashboardImportRead,
    DashboardImportRequest,
    DashboardRead,
    DashboardSummaryRead,
    DashboardTileRead,
    DashboardUpdate,
    ImportSkipRead,
    LayoutUpdate,
    TileCreate,
    TileResultRead,
    TileUpdate,
)
from app.infra.db.models import Dashboard, DashboardTile
from app.services.dashboard_service import DashboardService, effective_refresh_interval
from app.services.dashboard_transfer import DashboardDocument
from app.services.query_service import TileResult

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _tile_read(
    tile: DashboardTile,
    dashboard: Dashboard,
    connections: dict[UUID, str] | None = None,
    models: dict[UUID, str] | None = None,
) -> DashboardTileRead:
    read = DashboardTileRead.model_validate(tile)
    read.effective_refresh_interval_seconds = effective_refresh_interval(tile, dashboard)
    if tile.connection_id is not None:
        read.connection_name = (connections or {}).get(tile.connection_id)
    if tile.llm_config_id is not None:
        read.llm_config_name = (models or {}).get(tile.llm_config_id)
    return read


async def _dashboard_read(
    service: DashboardService, dashboard: Dashboard
) -> DashboardRead:
    tiles = await service.tiles_of(dashboard.id)
    connections, models = await service.display_names(tiles)
    # Every field *except* `tiles` is copied off the row. `Dashboard.tiles` is
    # a lazy relationship, and `model_validate` would read it — a lazy load
    # inside a response, in a context that cannot await, which is
    # MissingGreenlet. The tiles come from the query above, already enriched
    # with their effective rate and their chips' labels.
    fields = {
        name: getattr(dashboard, name)
        for name in DashboardRead.model_fields
        if name != "tiles"
    }
    return DashboardRead(
        **fields,
        tiles=[_tile_read(t, dashboard, connections, models) for t in tiles],
    )


def _data_read(results: dict[UUID, TileResult]) -> DashboardDataRead:
    return DashboardDataRead(
        results={
            tile_id: TileResultRead.model_validate(result.to_payload())
            for tile_id, result in results.items()
        }
    )


# ── dashboards ───────────────────────────────────────────────────────────
@router.get("", response_model=list[DashboardSummaryRead])
async def list_dashboards(
    ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> list[DashboardSummaryRead]:
    service = DashboardService(db, settings)
    dashboards = await service.list(ctx.user_id)
    ids = [d.id for d in dashboards]
    counts = await service.tile_counts(ids)
    refreshed = await service.last_refreshed(ids)

    cards: list[DashboardSummaryRead] = []
    for dashboard in dashboards:
        card = DashboardSummaryRead.model_validate(dashboard)
        card.tile_count = counts.get(dashboard.id, 0)
        card.last_refreshed_at = refreshed.get(dashboard.id)
        cards.append(card)
    return cards


@router.post("", response_model=DashboardRead, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    payload: DashboardCreate, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> DashboardRead:
    service = DashboardService(db, settings)
    dashboard = await service.create(ctx.user_id, **payload.model_dump())
    return await _dashboard_read(service, dashboard)


@router.post(
    "/import", response_model=DashboardImportRead, status_code=status.HTTP_201_CREATED
)
async def import_dashboard(
    payload: DashboardImportRequest, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> DashboardImportRead:
    """Create a dashboard from an exported document.

    Declared above `/{dashboard_id}` — the house rule — and taking the file as
    JSON rather than as an upload, because the browser has to read it anyway:
    the import dialog shows what is in the document and asks which connection
    each of its databases is *here* before anything is sent.

    A document's SQL is hostile input like any other. Every tile in it goes
    through the same guard call the editor's save path makes, so this is a
    fourth way into a stored statement and not a way around anything.
    """
    service = DashboardService(db, settings)
    dashboard, skipped = await service.import_document(
        ctx.user_id,
        document=payload.document,
        name=payload.name,
        connection_map=payload.connection_map,
        skip_invalid=payload.skip_invalid,
    )
    read = await _dashboard_read(service, dashboard)
    return DashboardImportRead(
        dashboard=read,
        imported_tiles=len(read.tiles),
        skipped=[ImportSkipRead.model_validate(item) for item in skipped],
    )


@router.get("/{dashboard_id}", response_model=DashboardRead)
async def get_dashboard(
    dashboard_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> DashboardRead:
    service = DashboardService(db, settings)
    dashboard = await service.get(dashboard_id, ctx.user_id)
    return await _dashboard_read(service, dashboard)


@router.patch("/{dashboard_id}", response_model=DashboardRead)
async def update_dashboard(
    dashboard_id: UUID,
    payload: DashboardUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> DashboardRead:
    service = DashboardService(db, settings)
    dashboard = await service.update(
        dashboard_id, ctx.user_id, **payload.model_dump(exclude_unset=True)
    )
    return await _dashboard_read(service, dashboard)


@router.get("/{dashboard_id}/export", response_model=DashboardDocument)
async def export_dashboard(
    dashboard_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> DashboardDocument:
    """The dashboard as a portable document — the layout and the SQL, nothing else.

    Answered as JSON rather than as a download: the SPA sends a bearer token,
    which a plain `<a download>` cannot, so the browser saves the body itself.
    No results and nothing from inside a connection are in it — see
    `services/dashboard_transfer.py` for what that costs and why.
    """
    return await DashboardService(db, settings).export(dashboard_id, ctx.user_id)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> None:
    await DashboardService(db, settings).delete(dashboard_id, ctx.user_id)


# ── layout ───────────────────────────────────────────────────────────────
@router.patch("/{dashboard_id}/layout", response_model=list[DashboardTileRead])
async def update_layout(
    dashboard_id: UUID,
    payload: LayoutUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> list[DashboardTileRead]:
    """Bulk positions, one call per drag-end.

    One row per tile rather than a dashboard-level blob, so two open tabs
    cannot overwrite each other's layout wholesale.
    """
    service = DashboardService(db, settings)
    dashboard = await service.get(dashboard_id, ctx.user_id)
    tiles = await service.set_layout(
        dashboard_id, ctx.user_id, [p.model_dump() for p in payload.positions]
    )
    connections, models = await service.display_names(tiles)
    return [_tile_read(t, dashboard, connections, models) for t in tiles]


# ── tiles ────────────────────────────────────────────────────────────────
@router.post(
    "/{dashboard_id}/tiles",
    response_model=DashboardTileRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tile(
    dashboard_id: UUID,
    payload: TileCreate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> DashboardTileRead:
    """Save a tile. **Re-runs the guard** — a passing preview authorises nothing."""
    service = DashboardService(db, settings)
    dashboard = await service.get(dashboard_id, ctx.user_id)
    tile = await service.add_tile(dashboard_id, ctx.user_id, **payload.model_dump())
    return _tile_read(tile, dashboard, *await service.display_names([tile]))


@router.patch("/{dashboard_id}/tiles/{tile_id}", response_model=DashboardTileRead)
async def update_tile(
    dashboard_id: UUID,
    tile_id: UUID,
    payload: TileUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> DashboardTileRead:
    service = DashboardService(db, settings)
    dashboard = await service.get(dashboard_id, ctx.user_id)
    tile = await service.update_tile(
        dashboard_id, tile_id, ctx.user_id, **payload.model_dump(exclude_unset=True)
    )
    return _tile_read(tile, dashboard, *await service.display_names([tile]))


@router.delete(
    "/{dashboard_id}/tiles/{tile_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_tile(
    dashboard_id: UUID,
    tile_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> None:
    await DashboardService(db, settings).delete_tile(dashboard_id, tile_id, ctx.user_id)


@router.post(
    "/{dashboard_id}/tiles/{tile_id}/duplicate",
    response_model=DashboardTileRead,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_tile(
    dashboard_id: UUID,
    tile_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> DashboardTileRead:
    service = DashboardService(db, settings)
    dashboard = await service.get(dashboard_id, ctx.user_id)
    tile = await service.duplicate_tile(dashboard_id, tile_id, ctx.user_id)
    return _tile_read(tile, dashboard, *await service.display_names([tile]))


# ── data ─────────────────────────────────────────────────────────────────
@router.post("/{dashboard_id}/data", response_model=DashboardDataRead)
async def refresh_dashboard(
    dashboard_id: UUID,
    payload: DashboardDataRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    force: bool = False,
) -> DashboardDataRead:
    """Compute the tiles named, or all of them.

    One broken tile is an `ERROR` entry in `results`, never a failed response:
    the other eleven tiles on the dashboard have nothing to do with it.
    """
    results = await DashboardService(db, settings).refresh(
        dashboard_id, ctx.user_id, tile_ids=list(payload.tile_ids), force=force
    )
    return _data_read(results)


@router.post("/{dashboard_id}/tiles/{tile_id}/data", response_model=TileResultRead)
async def refresh_tile(
    dashboard_id: UUID,
    tile_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    force: bool = False,
) -> Any:
    """One tile — the kebab's "Refresh now", which sends `force=true`."""
    results = await DashboardService(db, settings).refresh(
        dashboard_id, ctx.user_id, tile_ids=[tile_id], force=force
    )
    result = results.get(tile_id)
    if result is None:
        # A tile that computes nothing (TEXT) still exists; asking it for data
        # is answered, not refused.
        await DashboardService(db, settings).tile(dashboard_id, tile_id, ctx.user_id)
        return TileResultRead.model_validate(TileResult().to_payload())
    return TileResultRead.model_validate(result.to_payload())
