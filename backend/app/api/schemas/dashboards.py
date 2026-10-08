"""Request/response DTOs: dashboards."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.drafts import TileResultRead


class TableColumnConfig(BaseModel):
    """One column of a TABLE tile, as the editor configured it.

    Position in the list *is* the display order. A column the result returns
    but this list does not mention is shown at the end rather than hidden: a
    query that gains a column must not silently drop it from the tile.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    hidden: bool = False
    # None keeps the column's own name. An empty string is a real choice — a
    # blank header — so this is nullable rather than defaulting to "".
    label: str | None = Field(default=None, max_length=200)
    align: Literal["auto", "left", "right", "center"] = "auto"
    # "auto" is what the table did before this existed: integers grouped,
    # decimals to two places, everything else as text.
    format: Literal["auto", "integer", "decimal", "percent", "text"] = "auto"


class TableConfig(BaseModel):
    """How a TABLE tile is drawn. Presentation only — see `models.py`.

    Validated here, on the way in, rather than trusted from the browser; but
    `DashboardTileRead.table_config` stays a plain dict, because a row that
    somehow holds a shape this model refuses must still be *readable* — the
    alternative is one bad tile turning its whole dashboard into a 500.
    """

    model_config = ConfigDict(extra="forbid")

    columns: list[TableColumnConfig] = Field(default_factory=list, max_length=500)
    sort_column: str | None = Field(default=None, max_length=200)
    sort_direction: Literal["asc", "desc"] = "asc"


class DashboardTileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    dashboard_id: UUID
    connection_id: UUID | None = None
    # Names only, for the tile's chips. A tile never carries a host, a
    # username, or anything else from inside a connection.
    connection_name: str | None = None
    llm_config_id: UUID | None = None
    llm_config_name: str | None = None
    title: str = ""
    tile_type: str = "CHART"
    question: str | None = None
    sql: str = ""
    sql_origin: str = "GENERATED"
    # null means Auto: the chart is re-planned from each result.
    chart_config: dict[str, Any] | None = None
    # null means "as the query returned it": every column, in query order.
    table_config: dict[str, Any] | None = None
    max_rows: int | None = None
    # null means "inherit the dashboard's default"; 0 means manual only. The
    # resolved number is sent alongside so the scheduler needs no second rule.
    refresh_interval_seconds: int | None = None
    effective_refresh_interval_seconds: int = 0
    grid_x: int = 0
    grid_y: int = 0
    grid_w: int = 4
    grid_h: int = 4
    position: int = 0
    created_at: datetime
    updated_at: datetime
    # True when this reader may not query the tile's data source. The tile
    # then arrives without its `sql` — that is the source's schema — and the
    # client says why instead of opening an empty editor.
    restricted: bool = False


class DashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None = None
    status: str = "ACTIVE"
    grid_columns: int = 12
    row_height_px: int = 60
    gap_px: int = 12
    compact_mode: str = "VERTICAL"
    palette: str = "default"
    theme_override: str = "INHERIT"
    default_refresh_interval_seconds: int = 0
    created_at: datetime
    updated_at: datetime
    # The dashboard and its tiles, never their results: a tile's data is asked
    # for separately, because each tile is on its own clock.
    tiles: list[DashboardTileRead] = Field(default_factory=list)
    # What this reader may do here, from the same table `GET …/actions`
    # renders from. Embedded so the header does not draw every control enabled
    # for one frame and then take half of them away.
    privileges: list[str] = Field(default_factory=list)
    # Whose board this is, when it is not the reader's — the header says it,
    # because it is who to ask. A display name, never an address.
    owner_name: str | None = None


class DashboardSummaryRead(BaseModel):
    """One card on the index: what it is, how big, and how fresh."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None = None
    status: str = "ACTIVE"
    default_refresh_interval_seconds: int = 0
    tile_count: int = 0
    last_refreshed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Somebody else's board, shared with this reader. Drives the "Shared with
    # me" filter and the owner's name on the card.
    shared: bool = False
    owner_name: str | None = None
    # What this reader may do with it, so a card they can only *describe* is
    # not drawn as one they can open, and the kebab offers only what the
    # server would allow.
    privileges: list[str] = Field(default_factory=list)


class NamedRef(BaseModel):
    """An id and the name a person calls it. Nothing else travels."""

    id: UUID
    name: str


class ShareCheckRead(BaseModel):
    """What a grantee would **not** be able to see on this dashboard.

    §19.2's second half: *"sharing warns when its tiles span connections the
    grantee cannot read, and names them. The share is still allowed; the
    surprise is not."* So this is a warning endpoint, never a gate — the
    dialog renders it beside an enabled Share button.

    `unreadable` names connections, never hosts or credentials: the same thing
    the tile placeholder shows the grantee, shown to the person about to
    create that placeholder.
    """

    total_connections: int = 0
    unreadable: list[NamedRef] = Field(default_factory=list)


class DashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    grid_columns: int = Field(default=12, ge=1, le=48)
    row_height_px: int = Field(default=60, ge=10, le=400)
    gap_px: int = Field(default=12, ge=0, le=64)
    palette: str = Field(default="default", max_length=30)
    theme_override: Literal["INHERIT", "DARK", "LIGHT"] = "INHERIT"
    default_refresh_interval_seconds: int = Field(default=0, ge=0, le=86_400)


class DashboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None
    grid_columns: int | None = Field(default=None, ge=1, le=48)
    row_height_px: int | None = Field(default=None, ge=10, le=400)
    gap_px: int | None = Field(default=None, ge=0, le=64)
    compact_mode: Literal["VERTICAL", "NONE"] | None = None
    palette: str | None = Field(default=None, max_length=30)
    theme_override: Literal["INHERIT", "DARK", "LIGHT"] | None = None
    default_refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)


class TileCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    tile_type: Literal["CHART", "TABLE", "METRIC", "TEXT"] = "CHART"
    connection_id: UUID | None = None
    llm_config_id: UUID | None = None
    question: str | None = None
    sql: str = ""
    # Provenance, never trust: the guard cannot tell these apart and does not
    # look. It exists so the editor knows which tab it opened on.
    sql_origin: Literal["GENERATED", "GENERATED_EDITED", "HANDWRITTEN"] = "GENERATED"
    chart_config: dict[str, Any] | None = None
    table_config: TableConfig | None = None
    max_rows: int | None = Field(default=None, ge=1)
    refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)
    grid_x: int = Field(default=0, ge=0)
    grid_y: int = Field(default=0, ge=0)
    grid_w: int = Field(default=4, ge=1)
    grid_h: int = Field(default=4, ge=1)
    position: int = Field(default=0, ge=0)


class TileUpdate(BaseModel):
    """Every field optional; only what is sent is changed.

    `chart_config` and `refresh_interval_seconds` can be set back to null on
    purpose — "Auto" and "inherit" are values, not the absence of one — so a
    client clears them by sending an explicit null, and omitting a field leaves
    it alone.
    """

    title: str | None = Field(default=None, max_length=200)
    tile_type: Literal["CHART", "TABLE", "METRIC", "TEXT"] | None = None
    connection_id: UUID | None = None
    llm_config_id: UUID | None = None
    question: str | None = None
    sql: str | None = None
    sql_origin: Literal["GENERATED", "GENERATED_EDITED", "HANDWRITTEN"] | None = None
    chart_config: dict[str, Any] | None = None
    table_config: TableConfig | None = None
    max_rows: int | None = Field(default=None, ge=1)
    refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)
    grid_x: int | None = Field(default=None, ge=0)
    grid_y: int | None = Field(default=None, ge=0)
    grid_w: int | None = Field(default=None, ge=1)
    grid_h: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0)


class TilePosition(BaseModel):
    tile_id: UUID
    grid_x: int | None = Field(default=None, ge=0)
    grid_y: int | None = Field(default=None, ge=0)
    grid_w: int | None = Field(default=None, ge=1)
    grid_h: int | None = Field(default=None, ge=1)
    position: int | None = Field(default=None, ge=0)


class LayoutUpdate(BaseModel):
    """One call per drag-end, carrying every tile the drag moved."""

    positions: list[TilePosition] = Field(default_factory=list)


class DashboardDataRequest(BaseModel):
    """Which tiles to compute. Empty means the whole dashboard.

    The normal call is a list: with per-tile rates the browser asks for the
    tiles that are *due*, and the whole dashboard is the first-paint case.
    """

    tile_ids: list[UUID] = Field(default_factory=list)


class DashboardDataRead(BaseModel):
    results: dict[UUID, TileResultRead] = Field(default_factory=dict)
