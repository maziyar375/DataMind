"""Access control, **called** — not read.

`test_authz_conformance.py` is structural: it reads the route table and the
source. That caught a great deal and missed the thing that mattered most,
because a structural check can only see that a guard is *spelled*, not that it
*runs*: its mutating-route sweep counted a route as guarded if its source
contained `"Dep"`, which `ctx: CtxDep` always does. Five `*.create`
capabilities were seeded, shown in the Roles tab and checked by nothing, and a
Viewer — *"creates nothing"* — could create a data source.

So this file drives the real app over real SQL (the conftest's SQLite schema)
as real principals, and asserts what they get back. Every claim here was a bug
found by doing exactly that; `docs/plans/access-control-fixes.md` §1 is the
list, and each test names its row.
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.api import deps
from app.core.config import get_settings
from app.core.context import RequestContext
from app.domain.value_objects.authz import (
    PRIVILEGE_LABELS,
    SHARE_LEVELS,
    SHAREABLE_LLM_CONFIG_PRIVILEGES,
    Capability,
    Privilege,
    ResourceType,
)
from app.infra.db import models
from app.main import create_app
from tests.unit.conftest import AsyncSessionShim, _connection, _team_grant, _user


# ── the harness ──────────────────────────────────────────────────────────
@pytest.fixture(autouse=True, scope="module")
def _every_table(engine: sa.Engine) -> None:
    """The rest of the schema, with the conftest's Postgres-to-SQLite edits.

    The conftest creates the tables the permission model reads; the routes
    here also touch report sections, blocks and the rest, and a route that
    500s on a missing table would read as an authorization answer.
    """
    metadata = sa.MetaData()
    for table in models.Base.metadata.tables.values():
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if "::" in str(getattr(column.server_default, "arg", "")):
                column.server_default = None
            if isinstance(column.type, ARRAY | JSONB):
                column.type = sa.JSON()
            if column.primary_key and isinstance(column.type, sa.BigInteger):
                column.type = sa.Integer()
    metadata.create_all(engine, checkfirst=True)


class _Session(AsyncSessionShim):
    """The conftest shim plus the async methods routes call on a session."""

    async def commit(self) -> None:
        self._session.flush()

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj: Any, *args: Any, **kwargs: Any) -> None:
        self._session.refresh(obj, *args, **kwargs)

    async def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._session.scalars(statement, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class _Box:
    """A secret box that round-trips without a key, so a route that stores a
    password answers its authorization question instead of a config error."""

    key_version = 1

    def encrypt(self, value: str, aad: str = "") -> str:
        return f"enc:{aad}:{value}"

    def decrypt(self, value: str, aad: str = "") -> str:
        return value.removeprefix(f"enc:{aad}:")


class World:
    """Five people, one of everything, and the grants that make them differ."""

    def __init__(self, db: AsyncSessionShim) -> None:
        s = db._session
        self.db = db
        self.owner = _user(s, "owner@world.local").id
        self.editor = _user(s, "editor@world.local").id   # modify on the board and report
        self.viewer = _user(s, "viewer@world.local").id   # select on the board and report
        self.glancer = _user(s, "glancer@world.local").id  # describe on everything
        self.stranger = _user(s, "stranger@world.local").id

        conn = _connection(s, owner_id=self.owner, name="Sales warehouse")
        self.conn = conn.id
        llm = models.LlmConfig(
            id=uuid4(), owner_id=self.owner, name="House model",
            provider="openai", model="gpt-4o", status="ACTIVE",
        )
        s.add(llm)
        s.flush()
        self.llm = llm.id
        board = models.Dashboard(
            id=uuid4(), owner_id=self.owner, name="Ops", status="ACTIVE",
            grid_columns=12, row_height_px=60, gap_px=12, compact_mode="VERTICAL",
            palette="default", theme_override="INHERIT",
            default_refresh_interval_seconds=300,
        )
        s.add(board)
        s.flush()
        self.dash = board.id
        tile = models.DashboardTile(
            id=uuid4(), dashboard_id=board.id, connection_id=conn.id,
            title="Orders", tile_type="TABLE", sql="SELECT status FROM orders",
            sql_origin="HANDWRITTEN", grid_x=0, grid_y=0, grid_w=4, grid_h=4,
            position=0,
        )
        s.add(tile)
        s.flush()
        self.tile = tile.id
        report = models.Report(
            id=uuid4(), owner_id=self.owner, name="Q3", connection_id=conn.id,
            llm_config_id=llm.id, language="en", status="ACTIVE",
        )
        s.add(report)
        s.flush()
        self.report = report.id
        section = models.ReportSection(
            id=uuid4(), report_id=report.id, position=0, heading="Revenue",
        )
        s.add(section)
        s.flush()
        s.add(models.ReportBlock(
            id=uuid4(), section_id=section.id, position=0, title="Total",
            question="total revenue", block_type="METRIC",
            sql="SELECT 1", sql_origin="HANDWRITTEN",
        ))
        s.flush()

        for type_, rid, privilege, who in (
            ("dashboard", board.id, "modify", self.editor),
            ("report", report.id, "modify", self.editor),
            ("dashboard", board.id, "select", self.viewer),
            ("report", report.id, "select", self.viewer),
            ("llm_config", llm.id, "select", self.viewer),
            ("dashboard", board.id, "describe", self.glancer),
            ("report", report.id, "describe", self.glancer),
            ("llm_config", llm.id, "describe", self.glancer),
            ("connection", conn.id, "describe", self.glancer),
        ):
            _team_grant(s, type_=type_, resource_id=rid, privilege=privilege, user=who)

        self.acting: UUID = self.owner
        self.capabilities: frozenset[Capability] = frozenset(Capability)
        self.app = create_app()
        shim = _Session(s)

        async def _db() -> AsyncIterator[Any]:
            yield shim

        async def _ctx() -> RequestContext:
            return RequestContext(
                user_id=self.acting, email="w@world.local",
                capabilities=self.capabilities, team_ids=frozenset(),
                correlation_id="world",
            )

        self.app.dependency_overrides[deps.get_db] = _db
        self.app.dependency_overrides[deps.get_ctx] = _ctx
        self.app.dependency_overrides[deps.get_secret_box] = lambda: _Box()
        # A real key for the services that build their own box from settings,
        # so they answer the question asked rather than a config error.
        settings = get_settings().model_copy(update={
            "secret_box_key": SecretStr(base64.urlsafe_b64encode(bytes(32)).decode()),
        })
        self.app.dependency_overrides[deps.get_settings] = lambda: settings

    def as_(self, who: UUID, capabilities: frozenset[Capability] | None = None) -> World:
        self.acting = who
        self.capabilities = frozenset(Capability) if capabilities is None else capabilities
        return self

    async def call(self, method: str, url: str, body: Any = None) -> httpx.Response:
        """One request, rolled back afterwards so the next one sees the world."""
        nested = self.db._session.begin_nested()
        try:
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://w") as c:
                return await c.request(method, url, json=body)
        finally:
            nested.rollback()


@pytest.fixture
def world(db: AsyncSessionShim) -> World:
    return World(db)


def _message(response: httpx.Response) -> str:
    detail = response.json().get("detail")
    return detail.get("message", "") if isinstance(detail, dict) else str(detail)


NOTHING: frozenset[Capability] = frozenset()


# ── S1, S2: creation is a capability, and it is asked ────────────────────
CREATE_ROUTES = [
    ("/api/v1/dashboards", {"name": "Mine"}, Capability.DASHBOARD_CREATE),
    ("/api/v1/connections", {
        "name": "Mine", "database_type": "postgres", "host": "h", "port": 5432,
        "database_name": "d", "username": "u", "password": "p",
    }, Capability.CONNECTION_CREATE),
    ("/api/v1/llm-configs", {
        "name": "Mine", "provider": "OpenAI-compatible", "model": "m",
        "base_url": "http://127.0.0.1:9",
    }, Capability.LLM_CONFIG_CREATE),
    ("/api/v1/conversations", {}, Capability.CONVERSATION_CREATE),
    ("/api/v1/reports", {"name": "Mine", "connection_id": "{conn}"}, Capability.REPORT_CREATE),
]


@pytest.mark.parametrize(("url", "body", "capability"), CREATE_ROUTES)
async def test_creating_needs_the_capability(
    world: World, url: str, body: dict[str, Any], capability: Capability
) -> None:
    """S1. A Viewer holds `team.read` and nothing else, and used to get 201."""
    body = json.loads(json.dumps(body).replace("{conn}", str(world.conn)))
    refused = await world.as_(world.owner, frozenset({Capability.TEAM_READ})).call(
        "POST", url, body
    )
    assert refused.status_code == 403, refused.text
    assert str(capability) in _message(refused)

    allowed = await world.as_(world.owner, frozenset({capability})).call("POST", url, body)
    assert allowed.status_code != 403, allowed.text


async def test_an_unsaved_probe_needs_the_create_capability(world: World) -> None:
    """S2. *Test connection* on an unsaved form makes the server dial a host
    somebody typed; *Test model* sends a request to a URL somebody typed."""
    world.as_(world.owner, NOTHING)
    connection = await world.call("POST", "/api/v1/connections/test", {
        "database_type": "postgres", "host": "10.0.0.1", "port": 5432,
        "database_name": "d", "username": "u", "password": "p",
    })
    assert connection.status_code == 403
    assert "connection.create" in _message(connection)

    model = await world.call("POST", "/api/v1/llm-configs/test", {
        "provider": "OpenAI-compatible", "model": "m", "base_url": "http://10.0.0.1",
    })
    assert model.status_code == 403
    assert "llm_config.create" in _message(model)


async def test_probing_a_saved_connection_needs_modify_even_with_a_typed_password(
    world: World,
) -> None:
    """S2. A typed password used to skip the check on the row named."""
    response = await world.as_(world.glancer).call("POST", "/api/v1/connections/test", {
        "connection_id": str(world.conn), "database_type": "postgres",
        "host": "10.0.0.1", "port": 5432, "database_name": "d", "username": "u",
        "password": "typed",
    })
    assert response.status_code == 403
    assert "modify" in _message(response)


# ── S10: the sweep that can fail ─────────────────────────────────────────
#: Routes that legitimately mutate for a principal with nothing: signing in
#: and out, and your own account.
_OPEN = {
    "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout",
    "/api/v1/auth/me", "/api/v1/auth/me/password",
}


_PREFIX = "/api/v1"


def _routes() -> list[tuple[str, str, Any]]:
    """Every mutating route: method, full path, and its request body model."""
    app = create_app()
    out: list[tuple[str, str, Any]] = []
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        for attribute in ("routes", "original_router"):
            nested = getattr(route, attribute, None)
            if nested is not None:
                stack.extend(nested.routes if hasattr(nested, "routes") else nested)
        if getattr(route, "endpoint", None) is None or not hasattr(route, "path"):
            continue
        # An included router reports its path without the app's prefix, and
        # a sweep that called `/dashboards` would get 404 from every route and
        # pass having asked nothing — which it did, the first time.
        path = route.path if route.path.startswith(_PREFIX) else _PREFIX + route.path
        field = getattr(route, "body_field", None)
        body = field.field_info.annotation if field is not None else None
        for method in sorted(set(route.methods or ()) & {"POST", "PATCH", "PUT", "DELETE"}):
            if path not in _OPEN:
                out.append((method, path, body))
    return sorted(set(out), key=lambda r: (r[1], r[0]))


def _example(model: Any, ids: dict[str, UUID]) -> Any:
    """A minimal **valid** body for `model`, built from its own fields.

    Without one, a route validates the body before its handler asks anything,
    answers 422, and the sweep learns nothing about its guard — a third of the
    routes did exactly that the first time this ran.
    """
    if model is None or not hasattr(model, "model_fields"):
        return {}
    body: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        body[name] = _value(name, field.annotation, ids)
    return body


def _value(name: str, annotation: Any, ids: dict[str, UUID]) -> Any:
    origin = get_origin(annotation)
    args = [a for a in get_args(annotation) if a is not type(None)]
    if origin in (Union, UnionType):
        return _value(name, args[0], ids)
    if origin is Literal:
        return args[0]
    if origin in (list, tuple, set, frozenset):
        return []
    if origin is dict or annotation is dict:
        return {}
    if annotation is UUID:
        return str(ids.get(name, ids.get("user_id") if name == "to" else uuid4()))
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 0.5
    if hasattr(annotation, "model_fields"):
        return _example(annotation, ids)
    if annotation is SecretStr:
        return "secret"
    return "x"


async def test_nobody_with_nothing_changes_anything(world: World) -> None:
    """Every mutating route, called for real by a principal with **no grant and
    no capability**, on the real ids of rows somebody else owns. None of them
    may succeed.

    The bodies are the create routes' valid ones where a 422 would otherwise
    hide the answer, and `{}` elsewhere — every other route names a resource in
    its path, and the stranger holds nothing on any of them.
    """
    bodies = {url: body for url, body, _ in CREATE_ROUTES}
    ids = {
        "connection_id": world.conn, "config_id": world.llm, "dashboard_id": world.dash,
        "tile_id": world.tile, "report_id": world.report,
        "llm_config_id": world.llm, "user_id": world.stranger,
    }
    world.as_(world.stranger, NOTHING)
    succeeded: list[str] = []
    crashed: list[str] = []
    refused = 0
    for method, path, model in _routes():
        url = re.sub(
            r"\{(\w+)\}",
            lambda m: "1" if m.group(1) == "number" else str(ids.get(m.group(1), uuid4())),
            path,
        )
        body = json.loads(
            json.dumps(bodies.get(path) or _example(model, ids)).replace(
                "{conn}", str(world.conn)
            )
        )
        response = await world.call(method, url, body)
        refused += response.status_code == 403
        if response.status_code < 300:
            succeeded.append(f"{method} {path} -> {response.status_code}")
        elif response.status_code >= 500:
            crashed.append(f"{method} {path} -> {response.status_code}")
    assert refused >= 10, (
        f"only {refused} routes answered 403 — the sweep is probably calling "
        "paths that do not exist and learning nothing"
    )
    assert not crashed, (
        "these crashed before answering, so the sweep cannot tell whether they "
        "are guarded:\n  " + "\n  ".join(crashed)
    )
    assert not succeeded, (
        "a principal with no grant and no capability changed something:\n  "
        + "\n  ".join(succeeded)
    )


# ── S4, S5: refusals that name what they are about ───────────────────────
async def test_a_report_editor_without_the_model_is_told_which_model(
    world: World,
) -> None:
    """S4. Was a 404 "Model configuration not found." — about a model the
    editor never asked about, printed on the report they were looking at."""
    s = world.db._session
    _team_grant(s, type_="connection", resource_id=world.conn, privilege="select",
                user=world.editor)
    response = await world.as_(world.editor).call(
        "POST", f"/api/v1/reports/{world.report}/runs", {}
    )
    assert response.status_code == 403, response.text
    assert "House model" in _message(response)


async def test_a_report_editor_without_the_data_is_told_which_data(world: World) -> None:
    response = await world.as_(world.editor).call(
        "POST", f"/api/v1/reports/{world.report}/runs", {}
    )
    assert response.status_code == 403, response.text
    assert "Sales warehouse" in _message(response)


async def test_a_board_editor_can_rename_a_tile_whose_data_they_lack(
    world: World,
) -> None:
    """S5. Every tile edit re-checked the data source, so a title fix was a
    404 about a connection."""
    world.as_(world.editor)
    renamed = await world.call(
        "PATCH", f"/api/v1/dashboards/{world.dash}/tiles/{world.tile}",
        {"title": "Orders by status"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Orders by status"

    requery = await world.call(
        "PATCH", f"/api/v1/dashboards/{world.dash}/tiles/{world.tile}",
        {"sql": "SELECT * FROM payroll"},
    )
    assert requery.status_code == 403
    assert "Sales warehouse" in _message(requery)


# ── S6: a shared board does not carry its data source's schema ───────────
async def test_a_viewer_without_the_data_gets_no_tile_sql(world: World) -> None:
    world.as_(world.viewer)
    board = await world.call("GET", f"/api/v1/dashboards/{world.dash}")
    assert board.status_code == 200
    (tile,) = board.json()["tiles"]
    assert tile["sql"] == ""
    assert tile["restricted"] is True
    assert tile["title"] == "Orders"

    exported = await world.call("GET", f"/api/v1/dashboards/{world.dash}/export")
    assert exported.status_code == 200
    assert [t["sql"] for t in exported.json()["tiles"]] == [""]


async def test_the_owner_still_gets_the_sql(world: World) -> None:
    board = await world.as_(world.owner).call("GET", f"/api/v1/dashboards/{world.dash}")
    (tile,) = board.json()["tiles"]
    assert tile["sql"] == "SELECT status FROM orders"
    assert tile["restricted"] is False


# ── S7: lists say what you can do; pickers offer what you can use ────────
async def test_index_cards_carry_what_the_reader_may_do(world: World) -> None:
    for url in ("/api/v1/dashboards", "/api/v1/reports"):
        (glanced,) = (await world.as_(world.glancer).call("GET", url)).json()
        assert glanced["privileges"] == ["describe"]
        (viewed,) = (await world.as_(world.viewer).call("GET", url)).json()
        assert viewed["privileges"] == ["describe", "select"]
        (owned,) = (await world.as_(world.owner).call("GET", url)).json()
        assert "manage" in owned["privileges"]


async def test_a_model_picker_offers_only_models_you_can_answer_with(
    world: World,
) -> None:
    world.as_(world.glancer)
    everything = (await world.call("GET", "/api/v1/llm-configs")).json()
    assert [m["name"] for m in everything] == ["House model"]
    assert everything[0]["privileges"] == ["describe"]
    assert everything[0]["shared"] is True
    assert everything[0]["owner_name"] == "owner"

    picker = (await world.call("GET", "/api/v1/llm-configs?purpose=chat")).json()
    assert picker == []

    usable = (
        await world.as_(world.viewer).call("GET", "/api/v1/llm-configs?purpose=chat")
    ).json()
    assert [m["name"] for m in usable] == ["House model"]


# ── S8: anybody can find somebody to share with ──────────────────────────
async def test_the_directory_needs_no_capability_and_carries_no_address(
    world: World,
) -> None:
    response = await world.as_(world.viewer, NOTHING).call("GET", "/api/v1/directory")
    assert response.status_code == 200
    body = response.json()
    names = {p["name"] for p in body["people"]}
    assert {"owner", "editor", "stranger"} <= names
    assert "@" not in response.text
    (me,) = [p for p in body["people"] if p["is_you"]]
    assert me["name"] == "viewer"


# ── S9, X1: the share levels are the server's, and short ─────────────────
async def test_a_model_configuration_offers_one_share_level(world: World) -> None:
    actions = (
        await world.as_(world.owner).call("GET", f"/api/v1/llm-configs/{world.llm}/actions")
    ).json()
    assert actions["levels"] == ["select"]
    assert actions["labels"]["select"] == "Can use"


async def test_a_reader_is_told_whose_it_is(world: World) -> None:
    actions = (
        await world.as_(world.viewer).call("GET", f"/api/v1/dashboards/{world.dash}/actions")
    ).json()
    assert actions["owner_name"] == "owner"
    assert actions["is_owner"] is False
    assert actions["levels"] == ["select", "modify", "manage"]
    assert actions["can"] == {
        "view": True, "edit": False, "delete": False, "share": False, "transfer": False,
    }


def test_every_type_labels_every_privilege_and_offers_real_levels() -> None:
    for type_ in ResourceType:
        assert set(PRIVILEGE_LABELS[type_]) == set(Privilege), type_
        assert SHARE_LEVELS[type_], type_
        assert list(SHARE_LEVELS[type_]) == sorted(
            SHARE_LEVELS[type_], key=list(Privilege).index
        ), f"{type_} levels are not in lattice order"
    assert set(SHARE_LEVELS[ResourceType.LLM_CONFIG]) <= SHAREABLE_LLM_CONFIG_PRIVILEGES


# ── background work acts with the person's teams ─────────────────────────
async def test_a_delegated_context_carries_the_persons_teams(world: World) -> None:
    """Chat runs and report runs are authorized *as* the person, in the
    background — and used to be asked without their teams, so data shared with
    a team passed the click and was refused at execution."""
    from app.domain.ports.authz import ResourceRef
    from app.infra.authz.rbac import RbacAuthorizer
    from app.infra.db.models import Team, TeamMember
    from app.services.team_service import delegated_context

    s = world.db._session
    finance = Team(id=uuid4(), name="Finance")
    s.add(finance)
    s.flush()
    s.add(TeamMember(team_id=finance.id, user_id=world.viewer))
    _team_grant(s, type_="connection", resource_id=world.conn, privilege="select",
                team=finance.id)
    ref = ResourceRef(type=ResourceType.CONNECTION, id=world.conn)
    authz = RbacAuthorizer(world.db)  # type: ignore[arg-type]

    ctx = await delegated_context(world.db, world.viewer)  # type: ignore[arg-type]
    assert ctx.team_ids == frozenset({finance.id})
    assert ctx.delegated
    assert await authz.allowed(ctx, ref, Privilege.SELECT)

    # …and the dialog stops warning that a Finance member cannot see the data.
    check = await world.as_(world.owner).call(
        "GET", f"/api/v1/dashboards/{world.dash}/share-check",
    )
    assert check.status_code == 200
    viewer_check = await world.call(
        "GET",
        f"/api/v1/dashboards/{world.dash}/share-check?user_id={world.viewer}",
    )
    assert viewer_check.json()["unreadable"] == []


async def test_a_report_share_check_names_the_data_source(world: World) -> None:
    response = await world.as_(world.owner).call(
        "GET", f"/api/v1/reports/{world.report}/share-check?user_id={world.viewer}"
    )
    assert response.status_code == 200
    assert [c["name"] for c in response.json()["unreadable"]] == ["Sales warehouse"]

    refused = await world.as_(world.viewer).call(
        "GET", f"/api/v1/reports/{world.report}/share-check?user_id={world.stranger}"
    )
    assert refused.status_code == 403
