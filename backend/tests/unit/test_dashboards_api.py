"""The dashboard routes.

Three claims are worth more than the rest, and §10 names all three:

* **Every route is owner-scoped.** Not "most" — the sweep at the bottom walks
  the whole route table and fails on any route that reaches the service without
  the caller's own id, so a route added later is covered the day it is added.
* **One failing tile still returns 200** with the other tiles' data. A broken
  tile is an entry in `results`, never a failed response.
* **`POST /data` with `tile_ids` touches only those tiles** — with per-tile
  rates that is the normal call, and computing the whole dashboard instead
  would quietly undo the entire point of the cache.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import dashboards
from app.core.clock import utcnow
from app.core.context import RequestContext
from app.core.errors import NotFoundError, SqlRejectedError
from app.domain.ports.database import ResultColumn
from app.infra.db.models import Dashboard, DashboardTile
from app.main import create_app
from app.services.query_service import TileResult

USER = uuid4()
OTHER = uuid4()
DASHBOARD_ID = uuid4()
TILE_ID = uuid4()
CONNECTION_ID = uuid4()


def _dashboard(owner_id: UUID = USER) -> Dashboard:
    return Dashboard(
        id=DASHBOARD_ID,
        owner_id=owner_id,
        name="Ops",
        description=None,
        status="ACTIVE",
        grid_columns=12,
        row_height_px=60,
        gap_px=12,
        compact_mode="VERTICAL",
        palette="default",
        theme_override="INHERIT",
        default_refresh_interval_seconds=300,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _tile(tile_id: UUID = TILE_ID, interval: int | None = None) -> DashboardTile:
    return DashboardTile(
        id=tile_id,
        dashboard_id=DASHBOARD_ID,
        connection_id=CONNECTION_ID,
        llm_config_id=None,
        title="Orders by status",
        tile_type="CHART",
        question="how many orders by status",
        sql="SELECT status FROM public.orders",
        sql_origin="GENERATED",
        chart_config=None,
        max_rows=None,
        refresh_interval_seconds=interval,
        grid_x=0,
        grid_y=0,
        grid_w=4,
        grid_h=4,
        position=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


OK_RESULT = TileResult(
    status="OK",
    columns=[ResultColumn(name="status", db_type="text")],
    rows=[["paid"]],
    row_count=1,
    duration_ms=9,
)
BROKEN_RESULT = TileResult(
    status="ERROR", error_code="E_QUERY_FAILED", error_message="relation is gone"
)


class NoLazyLoads:
    """A dashboard row whose relationship raises if a response reads it.

    `Dashboard.tiles` is lazy. Reading it while serialising a response is a
    lazy load in a context that cannot await — `MissingGreenlet`, a 500, and
    only ever in the running app: a detached ORM object in a test happily
    returns an empty list instead. So the fake makes the mistake loud.
    """

    def __init__(self, dashboard: Dashboard) -> None:
        self._dashboard = dashboard

    def __getattr__(self, name: str) -> Any:
        if name == "tiles":
            raise RuntimeError(
                "MissingGreenlet: the response read a lazy relationship"
            )
        return getattr(self._dashboard, name)


class FakeService:
    """Stands in for `DashboardService`, recording every owner_id it is given.

    The router builds the service itself, so it is the *class* that is
    replaced. Each instance shares one call log through the class attribute.
    """

    calls: list[tuple[str, dict[str, Any]]] = []
    results: dict[UUID, TileResult] = {}
    raises: Exception | None = None
    tiles: list[DashboardTile] = []

    def __init__(self, _db: Any, _settings: Any) -> None:
        pass

    def _record(self, method: str, **kwargs: Any) -> None:
        # `method`, not `name`: a dashboard has a `name` field, and the
        # collision is a TypeError raised inside a route — which this app
        # turns into a rich-rendered traceback that takes longer to print
        # than the test takes to run.
        FakeService.calls.append((method, kwargs))
        if FakeService.raises is not None:
            raise FakeService.raises

    async def list(self, owner_id: UUID) -> list[Dashboard]:
        self._record("list", owner_id=owner_id)
        return [_dashboard()]

    async def tile_counts(self, ids: list[UUID]) -> dict[UUID, int]:
        return {DASHBOARD_ID: 2}

    async def last_refreshed(self, ids: list[UUID]) -> dict[UUID, datetime]:
        return {DASHBOARD_ID: utcnow()}

    async def get(self, dashboard_id: UUID, owner_id: UUID) -> Any:
        self._record("get", dashboard_id=dashboard_id, owner_id=owner_id)
        return NoLazyLoads(_dashboard())

    async def tiles_of(self, dashboard_id: UUID) -> list[DashboardTile]:
        return list(FakeService.tiles)

    async def display_names(self, tiles: list[DashboardTile]) -> tuple[dict, dict]:
        return {CONNECTION_ID: "sales"}, {}

    async def create(self, owner_id: UUID, **fields: Any) -> Any:
        self._record("create", owner_id=owner_id, **fields)
        return NoLazyLoads(_dashboard())

    async def update(self, dashboard_id: UUID, owner_id: UUID, **changes: Any) -> Any:
        self._record("update", dashboard_id=dashboard_id, owner_id=owner_id, **changes)
        return NoLazyLoads(_dashboard())

    async def delete(self, dashboard_id: UUID, owner_id: UUID) -> None:
        self._record("delete", dashboard_id=dashboard_id, owner_id=owner_id)

    async def tile(self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID) -> DashboardTile:
        self._record("tile", dashboard_id=dashboard_id, tile_id=tile_id, owner_id=owner_id)
        return _tile()

    async def add_tile(self, dashboard_id: UUID, owner_id: UUID, **fields: Any) -> DashboardTile:
        self._record("add_tile", dashboard_id=dashboard_id, owner_id=owner_id, **fields)
        return _tile()

    async def update_tile(
        self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID, **changes: Any
    ) -> DashboardTile:
        self._record(
            "update_tile",
            dashboard_id=dashboard_id,
            tile_id=tile_id,
            owner_id=owner_id,
            **changes,
        )
        return _tile()

    async def delete_tile(self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID) -> None:
        self._record(
            "delete_tile", dashboard_id=dashboard_id, tile_id=tile_id, owner_id=owner_id
        )

    async def duplicate_tile(
        self, dashboard_id: UUID, tile_id: UUID, owner_id: UUID
    ) -> DashboardTile:
        self._record(
            "duplicate_tile",
            dashboard_id=dashboard_id,
            tile_id=tile_id,
            owner_id=owner_id,
        )
        return _tile(uuid4())

    async def set_layout(
        self, dashboard_id: UUID, owner_id: UUID, positions: list[dict]
    ) -> list[DashboardTile]:
        self._record(
            "set_layout",
            dashboard_id=dashboard_id,
            owner_id=owner_id,
            positions=positions,
        )
        return list(FakeService.tiles)

    async def refresh(
        self,
        dashboard_id: UUID,
        owner_id: UUID,
        tile_ids: list[UUID] | None = None,
        force: bool = False,
    ) -> dict[UUID, TileResult]:
        self._record(
            "refresh",
            dashboard_id=dashboard_id,
            owner_id=owner_id,
            tile_ids=tile_ids,
            force=force,
        )
        return dict(FakeService.results)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    FakeService.calls = []
    FakeService.results = {TILE_ID: OK_RESULT}
    FakeService.raises = None
    FakeService.tiles = [_tile()]
    monkeypatch.setattr(dashboards, "DashboardService", FakeService)

    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: None
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER, email="user@test.local", role="MEMBER", correlation_id="test"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── reading ──────────────────────────────────────────────────────────────
def test_the_index_carries_a_tile_count_and_a_freshness_stamp(client: Any) -> None:
    response = client.get("/api/v1/dashboards")

    assert response.status_code == 200
    card = response.json()[0]
    assert card["name"] == "Ops"
    assert card["tile_count"] == 2
    assert card["last_refreshed_at"] is not None


def test_reading_a_dashboard_returns_its_tiles_and_no_results(client: Any) -> None:
    """The layout and the numbers are two requests: every tile is on its own
    clock, so the browser asks for the ones that are due."""
    response = client.get(f"/api/v1/dashboards/{DASHBOARD_ID}")

    assert response.status_code == 200
    body = response.json()
    assert "results" not in body
    tile = body["tiles"][0]
    assert tile["title"] == "Orders by status"
    assert "rows" not in tile
    # The chip's label, never the connection's internals.
    assert tile["connection_name"] == "sales"
    assert "host" not in tile and "encrypted_password" not in tile


def test_a_tile_reports_the_rate_it_actually_runs_at(client: Any) -> None:
    """`refresh_interval_seconds` is null — inherit — so the resolved number is
    the dashboard's, and the scheduler needs no second rule to work it out."""
    response = client.get(f"/api/v1/dashboards/{DASHBOARD_ID}")

    tile = response.json()["tiles"][0]
    assert tile["refresh_interval_seconds"] is None
    assert tile["effective_refresh_interval_seconds"] == 300


# ── writing ──────────────────────────────────────────────────────────────
def test_a_saved_tile_comes_back_with_its_chip_labels(client: Any) -> None:
    """The editor renders the tile it just saved, chips and all — without a
    second round trip to find out what the connection is called."""
    response = client.post(
        f"/api/v1/dashboards/{DASHBOARD_ID}/tiles",
        json={"tile_type": "TEXT", "title": "Note"},
    )

    assert response.status_code == 201
    assert response.json()["connection_name"] == "sales"


def test_creating_a_dashboard_returns_201(client: Any) -> None:
    response = client.post("/api/v1/dashboards", json={"name": "Ops"})

    assert response.status_code == 201
    assert response.json()["name"] == "Ops"


def test_a_patch_sends_only_the_fields_the_client_set(client: Any) -> None:
    """`exclude_unset`: omitting a field must leave it alone, not overwrite it
    with a default."""
    client.patch(f"/api/v1/dashboards/{DASHBOARD_ID}", json={"gap_px": 20})

    _name, kwargs = next(c for c in FakeService.calls if c[0] == "update")
    assert kwargs["gap_px"] == 20
    assert "palette" not in kwargs and "name" not in kwargs


def test_saving_a_tile_the_guard_rejects_is_refused(client: Any) -> None:
    """The preview passing is not authorisation to save."""
    FakeService.raises = SqlRejectedError("Not a SELECT.", rule_id="E_NOT_A_SELECT")

    response = client.post(
        f"/api/v1/dashboards/{DASHBOARD_ID}/tiles",
        json={"tile_type": "CHART", "connection_id": str(CONNECTION_ID), "sql": "DROP TABLE t"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "E_SQL_REJECTED"


def test_a_layout_save_is_one_call_carrying_every_moved_tile(client: Any) -> None:
    response = client.patch(
        f"/api/v1/dashboards/{DASHBOARD_ID}/layout",
        json={
            "positions": [
                {"tile_id": str(TILE_ID), "grid_x": 4, "grid_y": 2, "position": 1}
            ]
        },
    )

    assert response.status_code == 200
    _name, kwargs = next(c for c in FakeService.calls if c[0] == "set_layout")
    assert kwargs["positions"][0]["grid_x"] == 4


def test_deleting_returns_204(client: Any) -> None:
    assert client.delete(f"/api/v1/dashboards/{DASHBOARD_ID}").status_code == 204
    assert (
        client.delete(f"/api/v1/dashboards/{DASHBOARD_ID}/tiles/{TILE_ID}").status_code
        == 204
    )


# ── data ─────────────────────────────────────────────────────────────────
def test_one_failing_tile_still_returns_200_with_the_others_data(
    client: Any,
) -> None:
    """Twelve tiles refresh together; one broken query may not take the other
    eleven with it."""
    broken = uuid4()
    FakeService.results = {TILE_ID: OK_RESULT, broken: BROKEN_RESULT}

    response = client.post(f"/api/v1/dashboards/{DASHBOARD_ID}/data", json={})

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[str(TILE_ID)]["status"] == "OK"
    assert results[str(TILE_ID)]["row_count"] == 1
    assert results[str(broken)]["status"] == "ERROR"
    assert results[str(broken)]["error"]["code"] == "E_QUERY_FAILED"


def test_asking_for_named_tiles_asks_the_service_for_exactly_those(
    client: Any,
) -> None:
    wanted = [str(TILE_ID)]

    client.post(f"/api/v1/dashboards/{DASHBOARD_ID}/data", json={"tile_ids": wanted})

    _name, kwargs = next(c for c in FakeService.calls if c[0] == "refresh")
    assert [str(t) for t in kwargs["tile_ids"]] == wanted
    assert kwargs["force"] is False


def test_an_empty_body_means_the_whole_dashboard(client: Any) -> None:
    client.post(f"/api/v1/dashboards/{DASHBOARD_ID}/data", json={})

    _name, kwargs = next(c for c in FakeService.calls if c[0] == "refresh")
    assert kwargs["tile_ids"] == []


def test_refresh_now_forces_past_the_cache(client: Any) -> None:
    client.post(
        f"/api/v1/dashboards/{DASHBOARD_ID}/tiles/{TILE_ID}/data?force=true", json={}
    )

    _name, kwargs = next(c for c in FakeService.calls if c[0] == "refresh")
    assert kwargs["force"] is True
    assert kwargs["tile_ids"] == [TILE_ID]


def test_a_single_tile_comes_back_as_one_result_not_a_map(client: Any) -> None:
    response = client.post(f"/api/v1/dashboards/{DASHBOARD_ID}/tiles/{TILE_ID}/data")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["columns"][0]["name"] == "status"
    assert body["computed_at"] is not None


def test_asking_a_text_tile_for_data_is_answered_not_refused(client: Any) -> None:
    """A TEXT tile computes nothing, which is a state the UI renders — not an
    error it has to handle."""
    FakeService.results = {}

    response = client.post(f"/api/v1/dashboards/{DASHBOARD_ID}/tiles/{TILE_ID}/data")

    assert response.status_code == 200
    assert response.json()["row_count"] == 0


# ── scoping ──────────────────────────────────────────────────────────────
def test_another_users_dashboard_is_a_404(client: Any) -> None:
    """404, not 403: someone else's dashboard is indistinguishable from one
    that never existed."""
    FakeService.raises = NotFoundError("Dashboard not found.")

    response = client.get(f"/api/v1/dashboards/{DASHBOARD_ID}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


ROUTES: list[tuple[str, str, dict | None]] = [
    ("get", "", None),
    ("post", "", {"name": "New"}),
    ("get", f"/{DASHBOARD_ID}", None),
    ("patch", f"/{DASHBOARD_ID}", {"name": "Renamed"}),
    ("delete", f"/{DASHBOARD_ID}", None),
    ("patch", f"/{DASHBOARD_ID}/layout", {"positions": []}),
    (
        "post",
        f"/{DASHBOARD_ID}/tiles",
        {"tile_type": "TEXT", "title": "Note"},
    ),
    ("patch", f"/{DASHBOARD_ID}/tiles/{TILE_ID}", {"title": "Renamed"}),
    ("delete", f"/{DASHBOARD_ID}/tiles/{TILE_ID}", None),
    ("post", f"/{DASHBOARD_ID}/tiles/{TILE_ID}/duplicate", None),
    ("post", f"/{DASHBOARD_ID}/data", {}),
    ("post", f"/{DASHBOARD_ID}/tiles/{TILE_ID}/data", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_route_scopes_to_the_caller(
    client: Any, method: str, path: str, body: dict | None
) -> None:
    """The sweep. Every route must reach the service with the *session's* user
    id — never one from the URL, the body, or a default."""
    response = getattr(client, method)(
        f"/api/v1/dashboards{path}", **({"json": body} if body is not None else {})
    )

    assert response.status_code < 400, response.text
    owners = [kwargs["owner_id"] for _n, kwargs in FakeService.calls if "owner_id" in kwargs]
    assert owners, f"{method.upper()} {path} reached no owner-scoped service call"
    assert all(owner == USER for owner in owners)


def test_the_sweep_covers_every_route_the_app_publishes() -> None:
    """A route added later must be added to `ROUTES` too, or this fails — the
    scoping sweep above is only as good as its list."""
    app = create_app()
    published = {
        (method.lower(), path.replace("/api/v1/dashboards", ""))
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/v1/dashboards")
        for method in item
    }
    covered = {
        (method, path)
        for method, path, _body in ROUTES
    }

    def _template(path: str) -> str:
        return (
            path.replace(str(DASHBOARD_ID), "{dashboard_id}")
            .replace(str(TILE_ID), "{tile_id}")
        )

    assert {(m, _template(p)) for m, p in covered} == published


def test_the_routes_require_a_session() -> None:
    anonymous = TestClient(create_app())

    assert anonymous.get("/api/v1/dashboards").status_code == 401
    assert (
        anonymous.post(f"/api/v1/dashboards/{DASHBOARD_ID}/data", json={}).status_code
        == 401
    )
