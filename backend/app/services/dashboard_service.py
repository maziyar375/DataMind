"""Dashboards: CRUD, and the refresh that turns tiles into results.

Most of this file is ordinary CRUD with one rule threaded through every method —
**everything is scoped to the owner, and a resource belonging to someone else is
404, not 403**, so another user's dashboard is indistinguishable from one that
does not exist.

The two parts that are not CRUD:

* **Saving re-runs the guard.** A preview passing in the editor is not
  authorisation to save; `dashboard_tiles.sql` is user-typed text, so it is
  validated against the connection's current snapshot on the way in — and again
  on every refresh, by `query_service`, which trusts nothing this module did.
  **Importing a file is the same act**, and gets the same treatment: every tile
  in a document goes through `_validated_tile_fields` before a row exists.
* **The cache is what makes per-tile rates safe.** Five people with a 30-second
  tile open is a load generator pointed at the customer's database unless a
  result computed a moment ago is reused. Freshness is `now - computed_at <
  effective_refresh_interval(tile)` *and* an unchanged fingerprint of what
  produced it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, SqlRejectedError, ValidationError
from app.core.logging import get_logger
from app.domain.value_objects import TileType
from app.infra.db.models import (
    Dashboard,
    DashboardTile,
    DashboardTileCache,
    DatabaseConnection,
    LlmConfig,
)
from app.services.dashboard_transfer import (
    DashboardDocument,
    SkippedTile,
    build_document,
    label_of,
    parse_document,
    tile_fields,
)
from app.services.query_service import (
    TileRequest,
    TileResult,
    effective_max_rows,
    execute_many,
    latest_snapshot,
    policy_from_snapshot,
)
from app.sqlguard import guard

log = get_logger(__name__)

# Tile types that hold no SQL and are never executed or refreshed.
_NO_SQL_TYPES = frozenset({TileType.TEXT})


# ── the refresh policy, as pure functions ────────────────────────────────
def effective_refresh_interval(tile: DashboardTile, dashboard: Dashboard) -> int:
    """The rate this tile actually runs at, in seconds. `0` means manual only.

    `NULL` on the tile means "inherit", which is a different answer from `0`
    ("never, unless I press refresh") — collapsing the two is the one mistake
    this column exists to prevent.
    """
    if tile.refresh_interval_seconds is None:
        return max(0, dashboard.default_refresh_interval_seconds or 0)
    return max(0, tile.refresh_interval_seconds)


def result_fingerprint(tile: DashboardTile) -> str:
    """A hash of everything that decides what a refresh returns.

    Stored in `sql_hash` — the column is named for the case that matters, but
    the chart intent and the row cap shape the payload too, so an edit to
    either has to miss the cache. Without that, changing a tile from a pie to a
    line would keep serving the pie until its interval happened to elapse.

    `table_config` is **deliberately absent**. It decides how the browser draws
    rows it already has — column order, labels, a sort — and nothing about what
    the query returns, so renaming a column header must not re-run a query
    against the customer's database. If a table setting is ever added that
    changes the *payload*, it belongs here; today none does.
    """
    material = json.dumps(
        {
            "connection_id": str(tile.connection_id),
            "sql": (tile.sql or "").strip(),
            "max_rows": tile.max_rows,
            "chart_config": tile.chart_config,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def is_fresh(
    cache: DashboardTileCache | None,
    *,
    interval_seconds: int,
    fingerprint: str,
    now: datetime | None = None,
) -> bool:
    """Whether a cached result may be served instead of running the query.

    A `0` interval (manual) serves the cache on any hit until the user presses
    refresh, which arrives as `force` and never reaches this function.
    """
    if cache is None or cache.sql_hash != fingerprint:
        return False
    if interval_seconds <= 0:
        return True
    return (now or utcnow()) - cache.computed_at < timedelta(seconds=interval_seconds)


class DashboardService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    # ── dashboards ───────────────────────────────────────────────────────
    async def list(self, owner_id: UUID) -> list[Dashboard]:
        result = await self._db.execute(
            select(Dashboard)
            .where(Dashboard.owner_id == owner_id)
            .order_by(Dashboard.updated_at.desc())
        )
        return list(result.scalars())

    async def get(self, dashboard_id: UUID, owner_id: UUID) -> Dashboard:
        result = await self._db.execute(
            select(Dashboard).where(
                Dashboard.id == dashboard_id, Dashboard.owner_id == owner_id
            )
        )
        dashboard = result.scalar_one_or_none()
        if dashboard is None:
            # 404 rather than 403: see the module docstring.
            raise NotFoundError("Dashboard not found.")
        return dashboard

    async def tiles_of(self, dashboard_id: UUID) -> list[DashboardTile]:
        result = await self._db.execute(
            select(DashboardTile)
            .where(DashboardTile.dashboard_id == dashboard_id)
            .order_by(DashboardTile.position, DashboardTile.created_at)
        )
        return list(result.scalars())

    async def create(self, owner_id: UUID, **fields: Any) -> Dashboard:
        name = (fields.get("name") or "").strip()
        if not name:
            raise ValidationError("A dashboard needs a name.")
        await self._refuse_duplicate_name(owner_id, name)

        dashboard = Dashboard(id=uuid.uuid4(), owner_id=owner_id, **{**fields, "name": name})
        self._db.add(dashboard)
        await self._db.flush()
        return dashboard

    async def update(
        self, dashboard_id: UUID, owner_id: UUID, **changes: Any
    ) -> Dashboard:
        dashboard = await self.get(dashboard_id, owner_id)
        if (name := changes.get("name")) is not None:
            name = name.strip()
            if not name:
                raise ValidationError("A dashboard needs a name.")
            if name != dashboard.name:
                await self._refuse_duplicate_name(owner_id, name)
            changes["name"] = name

        for field, value in changes.items():
            setattr(dashboard, field, value)
        await self._db.flush()
        # `updated_at` has an onupdate, so the attribute is expired after the
        # flush; without the refresh, serialising it lazily loads mid-response
        # and trips MissingGreenlet.
        await self._db.refresh(dashboard)
        return dashboard

    async def delete(self, dashboard_id: UUID, owner_id: UUID) -> None:
        await self._db.delete(await self.get(dashboard_id, owner_id))

    async def _refuse_duplicate_name(self, owner_id: UUID, name: str) -> None:
        existing = await self._db.execute(
            select(Dashboard).where(
                Dashboard.owner_id == owner_id, Dashboard.name == name
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("You already have a dashboard with that name.")

    # ── tiles ────────────────────────────────────────────────────────────
    async def tile(
        self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID
    ) -> DashboardTile:
        await self.get(dashboard_id, owner_id)
        result = await self._db.execute(
            select(DashboardTile).where(
                DashboardTile.id == tile_id,
                DashboardTile.dashboard_id == dashboard_id,
            )
        )
        tile = result.scalar_one_or_none()
        if tile is None:
            raise NotFoundError("Tile not found.")
        return tile

    async def add_tile(
        self, dashboard_id: UUID, owner_id: UUID, **fields: Any
    ) -> DashboardTile:
        await self.get(dashboard_id, owner_id)
        return await self._store_tile(
            dashboard_id, await self._validated_tile_fields(owner_id, fields)
        )

    async def _store_tile(
        self, dashboard_id: UUID, fields: dict[str, Any]
    ) -> DashboardTile:
        """Write a tile whose fields have already been through the guard.

        Split out for `import_document`, which validates every tile in a file
        *before* creating anything — so a document with one bad statement is
        refused whole rather than half-written. The split is only over where the
        validated fields came from: nothing reaches this method that
        `_validated_tile_fields` has not returned.
        """
        tile = DashboardTile(id=uuid.uuid4(), dashboard_id=dashboard_id, **fields)
        if tile.position == 0:
            tile.position = len(await self.tiles_of(dashboard_id))
        self._db.add(tile)
        await self._db.flush()
        return tile

    async def update_tile(
        self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID, **changes: Any
    ) -> DashboardTile:
        tile = await self.tile(dashboard_id, tile_id, owner_id)
        merged = {
            "tile_type": tile.tile_type,
            "connection_id": tile.connection_id,
            "sql": tile.sql,
            "max_rows": tile.max_rows,
            **changes,
        }
        # Re-validated as a whole, not field by field: switching a TEXT tile to
        # a CHART is legal, and it is the *resulting* tile that has to make
        # sense, not the field that happened to change.
        validated = await self._validated_tile_fields(owner_id, merged)

        for field in changes:
            setattr(tile, field, validated.get(field, changes[field]))
        await self._db.flush()
        await self._db.refresh(tile)
        return tile

    async def delete_tile(
        self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID
    ) -> None:
        await self._db.delete(await self.tile(dashboard_id, tile_id, owner_id))

    async def duplicate_tile(
        self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID
    ) -> DashboardTile:
        source = await self.tile(dashboard_id, tile_id, owner_id)
        copy = DashboardTile(
            id=uuid.uuid4(),
            dashboard_id=dashboard_id,
            connection_id=source.connection_id,
            llm_config_id=source.llm_config_id,
            title=f"{source.title} (copy)"[:200],
            tile_type=source.tile_type,
            question=source.question,
            sql=source.sql,
            sql_origin=source.sql_origin,
            chart_config=source.chart_config,
            table_config=source.table_config,
            max_rows=source.max_rows,
            refresh_interval_seconds=source.refresh_interval_seconds,
            grid_x=source.grid_x,
            grid_y=source.grid_y + source.grid_h,
            grid_w=source.grid_w,
            grid_h=source.grid_h,
            position=len(await self.tiles_of(dashboard_id)),
        )
        # The cache is deliberately not copied: a duplicate has its own clock
        # and its own first refresh.
        self._db.add(copy)
        await self._db.flush()
        return copy

    async def set_layout(
        self, dashboard_id: UUID, owner_id: UUID, positions: list[dict[str, Any]]
    ) -> list[DashboardTile]:
        """One call per drag-end, one row per tile — never a dashboard-level blob.

        A tile named here that is not on this dashboard is ignored rather than
        an error: a drag that raced a delete in another tab should finish, not
        fail the whole layout save.
        """
        await self.get(dashboard_id, owner_id)
        tiles = {tile.id: tile for tile in await self.tiles_of(dashboard_id)}

        touched: list[DashboardTile] = []
        for entry in positions:
            tile = tiles.get(entry.get("tile_id"))
            if tile is None:
                continue
            for field in ("grid_x", "grid_y", "grid_w", "grid_h", "position"):
                if (value := entry.get(field)) is not None:
                    setattr(tile, field, value)
            touched.append(tile)

        await self._db.flush()
        # `updated_at` carries an `onupdate`, which an UPDATE does not fetch
        # back: the attribute is expired, and serialising it would lazily load
        # inside the response — MissingGreenlet, a 500, and only ever against a
        # real database.
        for tile in touched:
            await self._db.refresh(tile)
        return sorted(tiles.values(), key=lambda t: (t.position, t.created_at))

    async def _validated_tile_fields(
        self, owner_id: UUID, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Everything a tile must satisfy before it is stored.

        The guard runs here because the save path may not trust the preview:
        the editor's preview and the save are two requests, and the second one
        carries whatever the client chose to send.
        """
        fields = dict(fields)
        tile_type = fields.get("tile_type") or TileType.CHART
        sql = (fields.get("sql") or "").strip()

        if tile_type in _NO_SQL_TYPES:
            # A text tile has nothing to run, so it may not carry a statement
            # that a later type change would silently make executable.
            fields["sql"] = ""
            fields["connection_id"] = None
            return fields

        connection_id = fields.get("connection_id")
        if connection_id is None:
            raise ValidationError("This tile type needs a database connection.")
        connection = await self._owned_connection(connection_id, owner_id)

        if fields.get("llm_config_id") is not None:
            await self._owned_llm_config(fields["llm_config_id"], owner_id)

        if not sql:
            raise ValidationError("This tile type needs a SQL statement.")
        fields["sql"] = sql

        if fields.get("max_rows") is not None:
            # Stored already clamped, so the editor never shows a cap the
            # connection would not honour.
            fields["max_rows"] = effective_max_rows(connection, fields["max_rows"])

        snapshot = await latest_snapshot(self._db, connection.id)
        if not snapshot.get("tables"):
            raise ValidationError(
                "Sync this connection's schema before saving SQL against it."
            )
        report, _ = guard(sql, policy_from_snapshot(snapshot, connection))
        if report.status != "VALID":
            first = report.errors[0] if report.errors else None
            raise SqlRejectedError(
                first.message if first else "This SQL was rejected by the guard.",
                rule_id=(first.rule_id if first else "E_SQL_REJECTED"),
            )
        return fields

    async def _owned_connection(
        self, connection_id: UUID, owner_id: UUID
    ) -> DatabaseConnection:
        result = await self._db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.id == connection_id,
                DatabaseConnection.owner_id == owner_id,
            )
        )
        connection = result.scalar_one_or_none()
        if connection is None:
            raise NotFoundError("Connection not found.")
        return connection

    async def _owned_llm_config(self, llm_config_id: UUID, owner_id: UUID) -> LlmConfig:
        result = await self._db.execute(
            select(LlmConfig).where(
                LlmConfig.id == llm_config_id, LlmConfig.owner_id == owner_id
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise NotFoundError("Model configuration not found.")
        return config

    # ── import / export ──────────────────────────────────────────────────
    async def export(self, dashboard_id: UUID, owner_id: UUID) -> DashboardDocument:
        """The dashboard as a portable document. No ids, no results, no secrets.

        The connections are loaded in full because the document needs two things
        off them — the name and the engine — and `display_names` deliberately
        returns only the first. Both queries are owner-scoped; a tile pointing at
        a row this user does not own contributes no connection to the file, and
        its tile exports unmapped.
        """
        dashboard = await self.get(dashboard_id, owner_id)
        tiles = await self.tiles_of(dashboard_id)
        connections = await self._connections_of(tiles, owner_id)
        return build_document(dashboard, tiles, connections)

    async def import_document(
        self,
        owner_id: UUID,
        *,
        document: Any,
        name: str | None = None,
        connection_map: dict[str, UUID] | None = None,
        skip_invalid: bool = False,
    ) -> tuple[Dashboard, list[SkippedTile]]:
        """Create a dashboard from a document, one guard pass per tile.

        Order matters, and it is the reason this is not a loop around
        `add_tile`:

        1. **Every tile is validated before anything is created.** A file with
           one statement the guard refuses leaves no half-built dashboard behind
           and reports *all* the tiles it refused, not the first — the user is
           holding one file and deciding once what to do about it.
        2. **`skip_invalid` is the user's answer to that report**, not a
           default. Importing a board against a database whose schema has moved
           on genuinely does lose tiles; dropping them silently would be a
           dashboard that looks complete and is not.
        3. The dashboard row is created only once tiles are known to be
           storable, so the common failure costs no name.

        A connection is resolved from the caller's own rows, never from the
        file: the document names a database, and only the person importing it
        can say which of *their* connections that is.
        """
        parsed = parse_document(document)
        targets = await self._resolve_refs(parsed, connection_map or {}, owner_id)

        prepared: list[dict[str, Any]] = []
        skipped: list[SkippedTile] = []
        for index, tile in enumerate(parsed.tiles):
            try:
                prepared.append(
                    await self._validated_tile_fields(
                        owner_id, tile_fields(tile, targets)
                    )
                )
            except (SqlRejectedError, ValidationError, NotFoundError) as exc:
                skipped.append(
                    SkippedTile(
                        title=label_of(tile, index),
                        code=getattr(exc, "code", "E_VALIDATION"),
                        reason=exc.message,
                    )
                )

        if skipped and not skip_invalid:
            raise ValidationError(_refusal(skipped), tiles=[s.title for s in skipped])

        wanted = (name or parsed.dashboard.name).strip()
        if not wanted:
            raise ValidationError("A dashboard needs a name.")
        settings = parsed.dashboard.model_dump(exclude={"name"})
        dashboard = await self.create(
            owner_id, name=await self._free_name(owner_id, wanted), **settings
        )
        for fields in prepared:
            await self._store_tile(dashboard.id, fields)
        return dashboard, skipped

    async def _resolve_refs(
        self,
        document: DashboardDocument,
        connection_map: dict[str, UUID],
        owner_id: UUID,
    ) -> dict[str, UUID]:
        """Which connection each `ref` in the file means, for this user.

        The client's map wins, and every id in it is checked against the
        caller's own rows — this is a route that takes an id from a request
        body, so the ownership check is the wall. Refs the map leaves out fall
        back to an exact name match, which is what makes re-importing a file
        into the account it came from a single click. Names are unique per owner
        (`uq_conn_owner_name`), so that match is never ambiguous.
        """
        declared = {connection.ref: connection for connection in document.connections}
        resolved: dict[str, UUID] = {}
        for ref, connection_id in connection_map.items():
            if ref not in declared:
                continue  # a ref for a connection this document never mentions
            await self._owned_connection(connection_id, owner_id)
            resolved[ref] = connection_id

        unmapped = [
            connection
            for ref, connection in declared.items()
            if ref not in resolved and connection.name.strip()
        ]
        if unmapped:
            by_name = await self._connection_ids_by_name(owner_id)
            for connection in unmapped:
                match = by_name.get(connection.name.strip().casefold())
                if match is not None:
                    resolved[connection.ref] = match
        return resolved

    async def _connection_ids_by_name(self, owner_id: UUID) -> dict[str, UUID]:
        rows = await self._db.execute(
            select(DatabaseConnection.id, DatabaseConnection.name).where(
                DatabaseConnection.owner_id == owner_id
            )
        )
        return {str(row[1]).strip().casefold(): row[0] for row in rows}

    async def _free_name(self, owner_id: UUID, wanted: str) -> str:
        """`wanted`, or the first free number after it.

        Names are unique per owner, and the collision that matters here is the
        ordinary one: importing a file back into the account that wrote it.
        Refusing the whole document over its name — after every statement in it
        has passed the guard — would be a wall in front of the common case, so
        an import renames instead. Every other write path still refuses a
        duplicate, because renaming what a user typed would be a different thing
        entirely.
        """
        rows = await self._db.execute(
            select(Dashboard.name).where(Dashboard.owner_id == owner_id)
        )
        taken = {str(name).strip().casefold() for name in rows.scalars()}
        if wanted.casefold() not in taken:
            return wanted

        for number in range(2, 1000):
            suffix = f" ({number})"
            candidate = f"{wanted[: 100 - len(suffix)].rstrip()}{suffix}"
            if candidate.casefold() not in taken:
                return candidate
        raise ConflictError("You already have a dashboard with that name.")

    # ── display names for the tile chrome ────────────────────────────────
    async def display_names(
        self, tiles: list[DashboardTile]
    ) -> tuple[dict[UUID, str], dict[UUID, str]]:
        """Connection and model names for the tile chips — names only.

        Two queries for a whole dashboard rather than two per tile, and never
        the connection's internals: a tile carries an id and a label, never a
        host, a username or a key.
        """
        connection_ids = {t.connection_id for t in tiles if t.connection_id}
        llm_ids = {t.llm_config_id for t in tiles if t.llm_config_id}

        connections: dict[UUID, str] = {}
        if connection_ids:
            rows = await self._db.execute(
                select(DatabaseConnection.id, DatabaseConnection.name).where(
                    DatabaseConnection.id.in_(connection_ids)
                )
            )
            connections = {row[0]: row[1] for row in rows}

        models: dict[UUID, str] = {}
        if llm_ids:
            rows = await self._db.execute(
                select(LlmConfig.id, LlmConfig.name).where(LlmConfig.id.in_(llm_ids))
            )
            models = {row[0]: row[1] for row in rows}
        return connections, models

    # ── refresh ──────────────────────────────────────────────────────────
    async def refresh(
        self,
        dashboard_id: UUID,
        owner_id: UUID,
        tile_ids: list[UUID] | None = None,
        force: bool = False,
    ) -> dict[UUID, TileResult]:
        """Run the tiles that are due and return one result per tile asked for.

        `tile_ids` is not an optimisation. With per-tile rates the browser asks
        for the tiles that are *due*, which is the normal call shape; the whole
        dashboard is the exception, on first paint.
        """
        dashboard = await self.get(dashboard_id, owner_id)
        wanted = set(tile_ids or [])
        tiles = [
            tile
            for tile in await self.tiles_of(dashboard_id)
            if not wanted or tile.id in wanted
        ]
        if not tiles:
            return {}

        results: dict[UUID, TileResult] = {}
        runnable: list[DashboardTile] = []
        for tile in tiles:
            if tile.tile_type in _NO_SQL_TYPES or not (tile.sql or "").strip():
                continue  # a text tile has nothing to compute
            if tile.connection_id is None:
                # The connection was deleted out from under a tile that
                # survived it — SET NULL, by design (§4).
                results[tile.id] = TileResult(
                    status="ERROR",
                    error_code="E_CONNECTION_REMOVED",
                    error_message=(
                        "This tile's database connection has been removed. "
                        "Edit the tile to point it at another one."
                    ),
                )
                continue
            runnable.append(tile)

        cache = await self._cache_rows([t.id for t in runnable])
        stale: list[DashboardTile] = []
        for tile in runnable:
            row = cache.get(tile.id)
            if not force and is_fresh(
                row,
                interval_seconds=effective_refresh_interval(tile, dashboard),
                fingerprint=result_fingerprint(tile),
            ):
                assert row is not None
                results[tile.id] = TileResult.from_payload(row.result)
            else:
                stale.append(tile)

        if stale:
            results |= await self._execute(stale, owner_id, cache)
        return results

    async def _execute(
        self,
        tiles: list[DashboardTile],
        owner_id: UUID,
        cache: dict[UUID, DashboardTileCache],
    ) -> dict[UUID, TileResult]:
        connections = await self._connections_of(tiles, owner_id)

        requests: list[TileRequest] = []
        results: dict[UUID, TileResult] = {}
        for tile in tiles:
            connection = connections.get(tile.connection_id)
            if connection is None:
                results[tile.id] = TileResult(
                    status="ERROR",
                    error_code="E_CONNECTION_REMOVED",
                    error_message="This tile's database connection is unavailable.",
                )
                continue
            requests.append(
                TileRequest(
                    tile_id=tile.id,
                    sql=tile.sql,
                    connection=connection,
                    chart_intent=_chart_intent(tile),
                    want_kpi=tile.tile_type == TileType.METRIC,
                    max_rows=tile.max_rows,
                )
            )

        if requests:
            results |= await execute_many(
                self._db, self._settings, requests=requests, owner_id=owner_id
            )

        by_id = {tile.id: tile for tile in tiles}
        for tile_id, result in results.items():
            if (tile := by_id.get(tile_id)) is not None:
                self._store(tile, result, cache.get(tile_id))
        await self._db.flush()
        return results

    async def _connections_of(
        self, tiles: list[DashboardTile], owner_id: UUID
    ) -> dict[UUID, DatabaseConnection]:
        """Every connection the batch needs, in one owner-scoped query.

        Scoped here as well as in `query_service`: this is the query that
        decides which rows are even loaded, and that one is the wall.
        """
        ids = {t.connection_id for t in tiles if t.connection_id}
        if not ids:
            return {}
        rows = await self._db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.id.in_(ids),
                DatabaseConnection.owner_id == owner_id,
            )
        )
        return {connection.id: connection for connection in rows.scalars()}

    async def _cache_rows(
        self, tile_ids: list[UUID]
    ) -> dict[UUID, DashboardTileCache]:
        if not tile_ids:
            return {}
        rows = await self._db.execute(
            select(DashboardTileCache).where(DashboardTileCache.tile_id.in_(tile_ids))
        )
        return {row.tile_id: row for row in rows.scalars()}

    def _store(
        self,
        tile: DashboardTile,
        result: TileResult,
        row: DashboardTileCache | None,
    ) -> None:
        """Write a result to the cache — including a failure.

        A failed refresh is cached too. Without it, a tile whose query is
        broken re-runs that query on every tick of every open browser, which is
        the worst thing a dashboard can do to a database.
        """
        payload = result.to_payload()
        payload["computed_at"] = result.computed_at.isoformat()

        if row is None:
            row = DashboardTileCache(tile_id=tile.id)
            self._db.add(row)
        row.sql_hash = result_fingerprint(tile)
        row.result = payload
        row.row_count = result.row_count
        row.computed_at = result.computed_at
        row.duration_ms = result.duration_ms
        row.error_code = result.error_code
        row.error_message = result.error_message

    async def last_refreshed(self, dashboard_ids: list[UUID]) -> dict[UUID, datetime]:
        """The newest `computed_at` per dashboard, for the index cards."""
        if not dashboard_ids:
            return {}
        rows = await self._db.execute(
            select(DashboardTile.dashboard_id, DashboardTileCache.computed_at)
            .join(DashboardTileCache, DashboardTileCache.tile_id == DashboardTile.id)
            .where(DashboardTile.dashboard_id.in_(dashboard_ids))
        )
        newest: dict[UUID, datetime] = {}
        for dashboard_id, computed_at in rows:
            if computed_at is not None and (
                dashboard_id not in newest or computed_at > newest[dashboard_id]
            ):
                newest[dashboard_id] = computed_at
        return newest

    async def tile_counts(self, dashboard_ids: list[UUID]) -> dict[UUID, int]:
        if not dashboard_ids:
            return {}
        rows = await self._db.execute(
            select(DashboardTile.dashboard_id).where(
                DashboardTile.dashboard_id.in_(dashboard_ids)
            )
        )
        counts: dict[UUID, int] = {}
        for (dashboard_id,) in rows:
            counts[dashboard_id] = counts.get(dashboard_id, 0) + 1
        return counts


def _refusal(skipped: list[SkippedTile]) -> str:
    """The sentence a refused import is answered with.

    It names the first few tiles and the reason the first one gave, because the
    two questions a user has are "which tiles?" and "is this the wrong
    connection or the wrong database?" — and the guard's own message answers the
    second. The full list rides along in the problem body as `tiles`.
    """
    names = ", ".join(f"“{item.title}”" for item in skipped[:3])
    if len(skipped) > 3:
        names += f" and {len(skipped) - 3} more"
    count = "1 tile" if len(skipped) == 1 else f"{len(skipped)} tiles"
    return (
        f"{count} in this file could not be imported against the connections "
        f"chosen: {names}. The first was refused because: {skipped[0].reason} "
        "Choose a different connection, or import the rest without them."
    )


def _chart_intent(tile: DashboardTile) -> Any:
    """A stored `ChartIntent`, or None for Auto.

    A malformed stored intent is treated as Auto rather than as an error: the
    numbers are correct whatever is wrong with the picture, and `plan_chart`
    will decide from the result's shape.
    """
    if not tile.chart_config:
        return None

    from app.charts import ChartIntent

    try:
        return ChartIntent.model_validate(tile.chart_config)
    except Exception:  # noqa: BLE001
        log.warning("tile_chart_config_unreadable", tile_id=str(tile.id))
        return None


__all__ = [
    "DashboardService",
    "effective_refresh_interval",
    "is_fresh",
    "result_fingerprint",
]
