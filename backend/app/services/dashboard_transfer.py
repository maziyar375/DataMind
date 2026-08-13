"""The portable form of a dashboard: what leaves as a file, and what a file may
become.

A dashboard is a layout, a set of statements, and a rate for each of them. None
of that is portable as it stands, because two of the three things a tile points
at — a connection and a model — are rows in *this* installation's database. So
the document carries neither.

Three rules decide everything in this module:

* **No ids leave, and no ids come back in.** A `connection_id` means nothing in
  another account, let alone another installation. Each connection a tile needs
  becomes a `ref` (`c1`, `c2`…) with a display **name** and an engine, and the
  importing user says which of *their* connections each ref is. A file that
  named a connection by id would either be useless or, worse, resolve to a row
  the file's reader was never meant to reach.
* **A document carries no results.** An export is a definition — the SQL, not
  the rows it returned. Exporting the cache would turn "share this dashboard"
  into "send this person an extract of the customer's database", which is a
  disclosure decision that no file format gets to make.
* **A document carries nothing from inside a connection.** A name and an engine
  are what the importer needs to choose a target and to be warned about a
  dialect change. The host, the database, the username and the password stay
  where they are (invariant #3) — a file leaves the system, and everything in it
  should be readable by whoever ends up holding it.

The import side is in `dashboard_service.import_document`, and the one thing to
know about it is in `docs/dashboards.md` §2: **an imported statement is hostile
input like any other.** `sql` in this file arrives from a text editor as easily
as from an export, so every tile goes through `_validated_tile_fields` — the
same guard call the save path makes — before a row is written.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as SchemaError

from app.core.clock import utcnow
from app.core.errors import ValidationError
from app.infra.db.models import Dashboard, DashboardTile, DatabaseConnection

# The two fields a reader checks before it trusts anything else in the file.
DOCUMENT_FORMAT = "datamind.dashboard"
DOCUMENT_VERSION = 1

# A ceiling on a hostile file, not on a real dashboard: twelve tiles is a busy
# board, and every tile costs a guard pass over a snapshot at import.
MAX_TILES = 200


# ── the document ─────────────────────────────────────────────────────────
class DocumentConnection(BaseModel):
    """A database a tile needs, as the file is allowed to describe it.

    The engine is here because it decides whether the SQL can even parse on the
    other side: mapping a Postgres tile onto MySQL is legal and usually wrong,
    and the importer deserves to be told before the guard tells them twelve
    times. Everything else about a connection is a credential's neighbourhood
    and stays out.
    """

    model_config = ConfigDict(extra="ignore")

    ref: str = Field(min_length=1, max_length=40)
    name: str = Field(default="", max_length=100)
    database_type: str = Field(default="", max_length=20)


class DocumentSettings(BaseModel):
    """The dashboard itself, minus its identity: no id, no owner, no status.

    An imported dashboard is `ACTIVE` and new. Carrying `ARCHIVED` across would
    import something already hidden from the default filter.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    grid_columns: int = Field(default=12, ge=1, le=48)
    row_height_px: int = Field(default=60, ge=10, le=400)
    gap_px: int = Field(default=12, ge=0, le=64)
    compact_mode: Literal["VERTICAL", "NONE"] = "VERTICAL"
    palette: str = Field(default="default", max_length=30)
    theme_override: Literal["INHERIT", "DARK", "LIGHT"] = "INHERIT"
    default_refresh_interval_seconds: int = Field(default=0, ge=0, le=86_400)


class DocumentTile(BaseModel):
    """One tile, pointing at a `ref` instead of a connection.

    Bounded exactly like `TileCreate`, because that is what it becomes. The
    fields it does *not* have are as deliberate as the ones it does: no `id`, no
    `dashboard_id`, no `llm_config_id` (which model drafted the SQL is
    provenance about a row in another installation, and is never consulted at
    refresh anyway), and no result.
    """

    model_config = ConfigDict(extra="ignore")

    # `None` for a TEXT tile, and for a tile whose connection was deleted before
    # the export — which is a real state (`SET NULL`, §4), not a corrupt file.
    connection_ref: str | None = Field(default=None, max_length=40)
    title: str = Field(default="", max_length=200)
    tile_type: Literal["CHART", "TABLE", "METRIC", "TEXT"] = "CHART"
    question: str | None = None
    sql: str = ""
    # Provenance, and provenance only. It survives the round trip because "a
    # model wrote this" is worth knowing six weeks later; it grants nothing at
    # either end, and the guard cannot tell the values apart.
    sql_origin: Literal["GENERATED", "GENERATED_EDITED", "HANDWRITTEN"] = "GENERATED"
    chart_config: dict[str, Any] | None = None
    # Left as a plain object on purpose, the same way `DashboardTileRead` leaves
    # it one: `table_config` is how the browser draws rows it already has, so a
    # shape this side cannot read costs a column header, never a number and
    # never a query. `plan_chart` and `table-format.ts` both fall back to their
    # defaults on anything they do not recognise.
    table_config: dict[str, Any] | None = None
    max_rows: int | None = Field(default=None, ge=1)
    refresh_interval_seconds: int | None = Field(default=None, ge=0, le=86_400)
    grid_x: int = Field(default=0, ge=0)
    grid_y: int = Field(default=0, ge=0)
    grid_w: int = Field(default=4, ge=1)
    grid_h: int = Field(default=4, ge=1)
    position: int = Field(default=0, ge=0)


class DashboardDocument(BaseModel):
    """What a `.json` export holds, and the only shape import accepts."""

    model_config = ConfigDict(extra="ignore")

    format: str = DOCUMENT_FORMAT
    version: int = DOCUMENT_VERSION
    # Stamped so a file found in a downloads folder can be dated without being
    # opened against a database.
    exported_at: datetime = Field(default_factory=utcnow)
    dashboard: DocumentSettings
    connections: list[DocumentConnection] = Field(default_factory=list, max_length=64)
    tiles: list[DocumentTile] = Field(default_factory=list, max_length=MAX_TILES)


@dataclass(frozen=True, slots=True)
class SkippedTile:
    """A tile the import refused, and why — the value behind `skip_invalid`."""

    title: str
    code: str
    reason: str


# ── export ───────────────────────────────────────────────────────────────
def build_document(
    dashboard: Dashboard,
    tiles: list[DashboardTile],
    connections: dict[UUID, DatabaseConnection],
    *,
    now: datetime | None = None,
) -> DashboardDocument:
    """Turn stored rows into the portable form. Pure: no I/O, no session.

    `connections` is keyed by id and may be missing one a tile still names — a
    connection deleted out from under a tile leaves `connection_id` NULL, and a
    row loaded under a different owner is not here either. Either way the tile
    exports with no `connection_ref` and the importer is asked for one, which is
    the same conversation the tile editor would have had.
    """
    refs: dict[UUID, str] = {}
    listed: list[DocumentConnection] = []
    for tile in tiles:
        connection = connections.get(tile.connection_id) if tile.connection_id else None
        if connection is None or connection.id in refs:
            continue
        ref = f"c{len(refs) + 1}"
        refs[connection.id] = ref
        listed.append(
            DocumentConnection(
                ref=ref,
                name=connection.name,
                database_type=connection.database_type,
            )
        )

    return DashboardDocument(
        format=DOCUMENT_FORMAT,
        version=DOCUMENT_VERSION,
        exported_at=now or utcnow(),
        dashboard=DocumentSettings(
            name=dashboard.name,
            description=dashboard.description,
            grid_columns=dashboard.grid_columns,
            row_height_px=dashboard.row_height_px,
            gap_px=dashboard.gap_px,
            compact_mode=_one_of(dashboard.compact_mode, ("VERTICAL", "NONE")),
            palette=dashboard.palette,
            theme_override=_one_of(
                dashboard.theme_override, ("INHERIT", "DARK", "LIGHT")
            ),
            default_refresh_interval_seconds=dashboard.default_refresh_interval_seconds,
        ),
        connections=listed,
        tiles=[
            DocumentTile(
                connection_ref=refs.get(tile.connection_id) if tile.connection_id else None,
                title=tile.title,
                tile_type=_one_of(
                    tile.tile_type, ("CHART", "TABLE", "METRIC", "TEXT"), "CHART"
                ),
                question=tile.question,
                sql=tile.sql,
                sql_origin=_one_of(
                    tile.sql_origin,
                    ("GENERATED", "GENERATED_EDITED", "HANDWRITTEN"),
                    "GENERATED",
                ),
                chart_config=tile.chart_config,
                table_config=tile.table_config,
                max_rows=tile.max_rows,
                refresh_interval_seconds=tile.refresh_interval_seconds,
                grid_x=tile.grid_x,
                grid_y=tile.grid_y,
                grid_w=tile.grid_w,
                grid_h=tile.grid_h,
                position=tile.position,
            )
            for tile in tiles
        ],
    )


def _one_of(value: str, allowed: tuple[str, ...], fallback: str | None = None) -> Any:
    """A stored status column, narrowed to what the document may say.

    The status columns are plain `String` so a new member needs no DDL
    (`docs/dashboards.md` §4). That freedom stops at the file: a row holding
    something this version does not know exports as the default rather than
    writing a document its own importer would reject.
    """
    return value if value in allowed else (fallback or allowed[0])


# ── import ───────────────────────────────────────────────────────────────
def parse_document(raw: Any) -> DashboardDocument:
    """Read a file into a document, or say plainly why it is not one.

    The format and version are checked *before* the shape, so a file from a
    later release is "written by a newer version" rather than a list of field
    errors about a schema it was never written against.
    """
    if not isinstance(raw, dict):
        raise ValidationError("That file is not a dashboard export.")

    if raw.get("format") != DOCUMENT_FORMAT:
        raise ValidationError(
            "That file is not a dashboard export — it has no "
            f'"{DOCUMENT_FORMAT}" marker.'
        )

    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise ValidationError("That export does not say which format version it is.")
    if version > DOCUMENT_VERSION:
        raise ValidationError(
            f"That export is in format version {version}; this installation reads "
            f"version {DOCUMENT_VERSION}. Update DataMind, then import it again."
        )

    try:
        return DashboardDocument.model_validate(raw)
    except SchemaError as exc:
        raise ValidationError(f"That export is malformed: {_first_problem(exc)}") from exc


def _first_problem(exc: SchemaError) -> str:
    """One readable sentence out of a pydantic error, not the whole report.

    The user is holding a file they did not write; "tiles.3.grid_w: Input should
    be greater than or equal to 1" tells them where to look, and the other
    nineteen errors underneath it do not.
    """
    errors = exc.errors()
    if not errors:
        return "it does not match the dashboard format."
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ())) or "document"
    return f"{where}: {first.get('msg', 'invalid value')}."


def tile_fields(tile: DocumentTile, targets: dict[str, UUID]) -> dict[str, Any]:
    """A document tile as the keyword arguments a tile row is built from.

    `llm_config_id` is absent rather than null-by-omission: the column exists,
    the document deliberately does not carry it, and an import has no model to
    attribute the SQL to.
    """
    return {
        "connection_id": (
            targets.get(tile.connection_ref) if tile.connection_ref else None
        ),
        "llm_config_id": None,
        "title": tile.title,
        "tile_type": tile.tile_type,
        "question": tile.question,
        "sql": tile.sql,
        "sql_origin": tile.sql_origin,
        "chart_config": tile.chart_config,
        "table_config": tile.table_config,
        "max_rows": tile.max_rows,
        "refresh_interval_seconds": tile.refresh_interval_seconds,
        "grid_x": tile.grid_x,
        "grid_y": tile.grid_y,
        "grid_w": tile.grid_w,
        "grid_h": tile.grid_h,
        "position": tile.position,
    }


def label_of(tile: DocumentTile, index: int) -> str:
    """What to call a tile in an error the user has to act on.

    Titles are optional and often empty, and "a tile was rejected" is unusable
    on a board of twelve. Falling back to the question, then to the position,
    means every rejection names something the user can find on screen.
    """
    if tile.title.strip():
        return tile.title.strip()
    question = (tile.question or "").strip()
    if question:
        return question[:60]
    return f"tile {index + 1}"


__all__ = [
    "DOCUMENT_FORMAT",
    "DOCUMENT_VERSION",
    "MAX_TILES",
    "DashboardDocument",
    "DocumentConnection",
    "DocumentSettings",
    "DocumentTile",
    "SkippedTile",
    "build_document",
    "label_of",
    "parse_document",
    "tile_fields",
]
