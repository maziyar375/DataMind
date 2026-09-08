"""The intersection rule: a shared artifact, and the data behind it.

Phase 8 makes reports, dashboards, model configurations and conversations
shareable, and every one of them raises the same question — *what happens when
you may see the thing but not the database it was built over?* §19.2 answers
it once:

> A dashboard renders. Each tile renders **iff** the viewer holds `select` on
> that tile's connection. A tile they cannot see renders as a **named
> placeholder** — not hidden, because hiding it makes the dashboard silently
> wrong, and a partly visible dashboard is a better product than a refused one
> **and** a better product than a leaking one.

Five claims here, ordered by what it would cost to get each one wrong:

* **The check runs before the cache.** `dashboard_tile_cache` holds rows read
  out of the customer's database and is keyed on the tile alone. Ask after the
  lookup and a revoked reader keeps being served the last numbers they were
  allowed to see, for as long as the interval lasts. This is the only test in
  the suite that would catch that, and it is the reason the trigger sentence
  is in `DashboardTileCache`'s docstring.
* **The cache key contains no viewer**, which is what makes the order above
  sufficient rather than merely current. A per-viewer fingerprint is the
  refactor this rule dies to.
* **A placeholder is named, and is not the deleted-connection code.** *"The
  data source was removed"* and *"you were not given this data source"* have
  different remedies, and a reader shown the wrong one goes to the wrong
  person.
* **`select` and `describe` are the only privileges grantable on a model
  configuration** — `modify` is equivalent to handing over the API key, and
  moving the endpoint clears it.
* **Sharing a thread does not share its database.** The grant reaches the
  transcript; the connection is a separate question with a separate answer.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.context import RequestContext
from app.core.errors import ForbiddenError, ValidationError
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.authz.rbac import RbacAuthorizer
from app.infra.db.models import (
    AuditLog,
    Conversation,
    Dashboard,
    DashboardTile,
    DashboardTileCache,
    Report,
)
from app.services import dashboard_service, restricted
from app.services.dashboard_service import DashboardService, result_fingerprint
from app.services.query_service import TileResult
from app.services.report_service import ReportService
from tests.unit.conftest import AsyncSessionShim, _connection, _team_grant, _user


def who(user_id: UUID) -> RequestContext:
    """A principal holding no app-wide verb — reach comes from grants only."""
    return RequestContext(
        user_id=user_id, email="u@test.local", correlation_id="t"
    )


@pytest.fixture
def world(db: AsyncSessionShim):
    """An owner, a reader, two connections and a board that spans both.

    The shape the acceptance test names: *two people, one database credential,
    one dashboard.* The reader is shared the board and one of its two
    warehouses, which is the only arrangement in which "one tile and one
    placeholder" is a meaningful sentence.
    """
    session = db._session

    class _World:
        owner = _user(session, "owner@test.local")
        reader = _user(session, "reader@test.local")

    world = _World()
    world.sales = _connection(session, owner_id=world.owner.id, name="Sales")
    world.payroll = _connection(session, owner_id=world.owner.id, name="Payroll")

    world.dashboard = Dashboard(
        id=uuid4(),
        owner_id=world.owner.id,
        name="Company overview",
        status="ACTIVE",
        grid_columns=12,
        row_height_px=60,
        gap_px=12,
        compact_mode="VERTICAL",
        palette="default",
        theme_override="INHERIT",
        default_refresh_interval_seconds=0,
    )
    session.add(world.dashboard)
    session.flush()

    world.sales_tile = _tile(session, world.dashboard, world.sales.id, "Revenue")
    world.payroll_tile = _tile(session, world.dashboard, world.payroll.id, "Wages")

    # The reader is shared the board and **one** of its two databases.
    _team_grant(
        session, type_="dashboard", resource_id=world.dashboard.id,
        privilege="select", user=world.reader.id,
    )
    _team_grant(
        session, type_="connection", resource_id=world.sales.id,
        privilege="select", user=world.reader.id,
    )
    world.db = db
    world.authz = RbacAuthorizer(db)
    return world


def _tile(session: Any, dashboard: Dashboard, connection_id: UUID, title: str):
    tile = DashboardTile(
        id=uuid4(),
        dashboard_id=dashboard.id,
        connection_id=connection_id,
        title=title,
        tile_type="TABLE",
        sql="SELECT 1 FROM public.orders",
        sql_origin="HANDWRITTEN",
        grid_x=0, grid_y=0, grid_w=4, grid_h=4, position=0,
    )
    session.add(tile)
    session.flush()
    return tile


def _executor(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace `execute_many`, recording which tiles actually reached a
    database. The list being empty is the assertion in half these tests."""
    ran: list[Any] = []

    async def fake(
        _db: Any,
        _settings: Any,
        *,
        requests: list[Any],
        ctx: RequestContext,
        authz: Any,
    ) -> dict:
        ran.extend(requests)
        return {
            request.tile_id: TileResult(status="OK", row_count=1, rows=[[1]])
            for request in requests
        }

    monkeypatch.setattr(dashboard_service, "execute_many", fake)
    return ran


def _cache_row(session: Any, tile: DashboardTile, value: str) -> DashboardTileCache:
    """A fresh cached result for a tile, holding a recognisable value.

    The value is what a leak would show: if any assertion below finds it in a
    response, the cache was read on behalf of somebody who may not see it.
    """
    result = TileResult(status="OK", rows=[[value]], row_count=1)
    payload = result.to_payload()
    payload["computed_at"] = utcnow().isoformat()
    row = DashboardTileCache(
        tile_id=tile.id,
        sql_hash=result_fingerprint(tile),
        result=payload,
        row_count=1,
        computed_at=utcnow(),
        duration_ms=1,
    )
    session.add(row)
    session.flush()
    return row


# ── the rule itself ──────────────────────────────────────────────────────
async def test_a_two_connection_board_renders_one_tile_and_one_placeholder(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance test of the whole phase, in one assertion pair.

    The reader was given the board and the Sales warehouse. They see the
    revenue numbers and a placeholder where the wages are — not an error page,
    not a silently missing tile, and not the wages.
    """
    ran = _executor(monkeypatch)

    results = await DashboardService(world.db, object(), world.authz).refresh(
        who(world.reader.id), world.dashboard.id
    )

    assert results[world.sales_tile.id].status == "OK"
    assert results[world.payroll_tile.id].error_code == restricted.NO_DATA_ACCESS
    # And the tile they may not see never reached a database.
    assert [request.tile_id for request in ran] == [world.sales_tile.id]


async def test_the_placeholder_names_the_database_and_is_not_the_removed_code(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*"Payroll"*, and a code that is not `E_CONNECTION_REMOVED`.

    Both halves matter. The name is what lets the reader ask the right person
    for access — it is exactly what `describe` means, and the share already
    decided this reader may see the board's shape. The distinct code is what
    stops the SPA telling them to edit a tile they cannot edit, about a data
    source that was never deleted.
    """
    _executor(monkeypatch)

    results = await DashboardService(world.db, object(), world.authz).refresh(
        who(world.reader.id), world.dashboard.id
    )
    withheld = results[world.payroll_tile.id]

    assert "Payroll" in (withheld.error_message or "")
    assert withheld.error_code != "E_CONNECTION_REMOVED"
    # And nothing about the connection beyond its name.
    assert "db.internal" not in (withheld.error_message or "")


async def test_the_owner_still_sees_both_tiles(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the rule, and the one a wrong `visible` breaks
    silently: the person who owns both warehouses loses nothing."""
    ran = _executor(monkeypatch)

    results = await DashboardService(world.db, object(), world.authz).refresh(
        who(world.owner.id), world.dashboard.id
    )

    assert {r.status for r in results.values()} == {"OK"}
    assert len(ran) == 2


# ── the tripwire: the check runs before the cache ────────────────────────
async def test_a_cached_result_is_not_served_to_a_reader_who_may_not_see_it(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The one that matters.**

    The payroll tile has a fresh cached result sitting in Postgres, computed
    for somebody who was allowed to compute it. A reader who holds the board
    and not that warehouse must get the placeholder, not the cache — and this
    is the only test in the suite that fails if the intersection check is
    moved below the cache lookup, because every other assertion here is about
    a tile that was going to run a query anyway.
    """
    _cache_row(world.db._session, world.payroll_tile, "SECRET-WAGES")
    ran = _executor(monkeypatch)
    asked = _spy_on_the_cache(monkeypatch)

    results = await DashboardService(world.db, object(), world.authz).refresh(
        who(world.reader.id), world.dashboard.id
    )
    withheld = results[world.payroll_tile.id]

    assert withheld.error_code == restricted.NO_DATA_ACCESS
    assert withheld.rows == []
    assert "SECRET-WAGES" not in str(withheld.to_payload())
    # Nor did it re-run the query to find that out. The sales tile ran, as it
    # always does — it has no cache row and the reader may see it.
    assert world.payroll_tile.id not in {request.tile_id for request in ran}

    # **The ordering assertion, and the reason this test exists.** The two
    # above would still pass if the check merely *overwrote* a cached result
    # afterwards — which is a version of this code that reads the customer's
    # stored rows on behalf of somebody who may not see them and then throws
    # them away, one refactor from returning them. So: the cache is asked
    # about the tiles the reader may see, and about no others.
    assert asked == [[world.sales_tile.id]]


def _spy_on_the_cache(monkeypatch: pytest.MonkeyPatch) -> list[list[UUID]]:
    """Record which tile ids the cache is looked up for, in order."""
    asked: list[list[UUID]] = []
    original = DashboardService._cache_rows

    async def spy(self: DashboardService, tile_ids: list[UUID]) -> Any:
        asked.append(list(tile_ids))
        return await original(self, tile_ids)

    monkeypatch.setattr(DashboardService, "_cache_rows", spy)
    return asked


def test_the_cache_key_contains_no_viewer() -> None:
    """What makes the order above sufficient rather than merely current.

    `result_fingerprint` takes a **tile** and nothing else. The day somebody
    adds a viewer to it — to filter rows per reader, say — the cache becomes
    one stored result per reader of every shared board, and, worse, a
    fingerprint that decides what somebody *else* is served. That feature is
    row-level security; it belongs in the guard and the connection, and it
    cannot be built by keying this table differently.

    Asserted on the signature rather than on a value, because a value test
    would pass a version that accepted a viewer and ignored it.
    """
    import inspect

    parameters = list(inspect.signature(result_fingerprint).parameters)
    assert parameters == ["tile"], (
        "result_fingerprint grew an argument. If it is a viewer, read "
        "DashboardTileCache's docstring before going further."
    )

    material = inspect.getsource(result_fingerprint)
    for word in ("ctx", "user_id", "viewer", "owner_id"):
        assert word not in material, f"{word!r} reached the cache fingerprint"


async def test_the_check_is_made_again_on_every_refresh(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per tile, per execution — not once when the board was opened.

    A revoke has to take effect on the next refresh, which is the property the
    whole authorizer is sold on. The first call sees the tile; the grant is
    then deleted; the second call must not.
    """
    _executor(monkeypatch)
    service = DashboardService(world.db, object(), world.authz)
    reader = who(world.reader.id)

    first = await service.refresh(reader, world.dashboard.id, force=True)
    assert first[world.sales_tile.id].status == "OK"

    session = world.db._session
    from app.infra.db.models import Grant

    session.query(Grant).filter(
        Grant.resource_id == world.sales.id, Grant.user_id == world.reader.id
    ).delete()
    session.flush()

    second = await service.refresh(reader, world.dashboard.id, force=True)
    assert second[world.sales_tile.id].error_code == restricted.NO_DATA_ACCESS


# ── the audit half ───────────────────────────────────────────────────────
async def test_a_render_writes_one_denial_per_connection_not_one_per_tile(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§19.1's fourth row is a 200 that is audited, and the unit is the
    database rather than the tile.

    A board with eight tiles on one unreachable warehouse is one fact about
    one half-finished share. Eight rows would bury it under itself.
    """
    _tile(world.db._session, world.dashboard, world.payroll.id, "Headcount")
    _executor(monkeypatch)

    await DashboardService(world.db, object(), world.authz).refresh(
        who(world.reader.id), world.dashboard.id
    )

    session = world.db._session
    denials = session.query(AuditLog).filter(AuditLog.outcome == "DENIED").all()
    assert len(denials) == 1
    assert denials[0].resource_id == world.payroll.id
    assert denials[0].detail["rendered_in"] == "dashboard"
    # Identifiers and counts, never content — rule 3 of `services/audit.py`.
    assert "SELECT" not in str(denials[0].detail)


# ── sharing warns, and does not refuse ───────────────────────────────────
async def test_the_share_check_names_the_databases_the_grantee_cannot_read(
    world: Any
) -> None:
    """The warning §19.2 asks for: *the share is still allowed; the surprise
    is not.* So this endpoint reports, and refuses nothing."""
    total, withheld = await DashboardService(
        world.db, object(), world.authz
    ).share_check(who(world.owner.id), world.dashboard.id, user_id=world.reader.id)

    assert total == 2
    assert [name for _id, name in withheld] == ["Payroll"]


async def test_the_share_check_needs_manage(world: Any) -> None:
    """It answers a question about a third party's reach, so only somebody who
    may decide this board's access may ask it."""
    with pytest.raises((ForbiddenError, Exception)):
        await DashboardService(world.db, object(), world.authz).share_check(
            who(world.reader.id), world.dashboard.id, user_id=world.reader.id
        )


async def test_the_share_check_finds_nothing_to_warn_about_for_the_owner(
    world: Any
) -> None:
    """The negative case, which is what stops the dialog crying wolf on every
    share: somebody who can read both warehouses gets an empty list."""
    total, withheld = await DashboardService(
        world.db, object(), world.authz
    ).share_check(who(world.owner.id), world.dashboard.id, user_id=world.owner.id)

    assert (total, withheld) == (2, [])


# ── reports: one connection, the same rule ───────────────────────────────
async def test_a_report_reader_without_the_connection_may_not_read_its_data(
    db: AsyncSessionShim
) -> None:
    """A report is bound to exactly one connection, so the second half of its
    authorization is a single boolean — and it is a different answer from
    "may you open the report"."""
    session = db._session
    owner, reader = _user(session, "o@test.local"), _user(session, "r@test.local")
    connection = _connection(session, owner_id=owner.id, name="Sales")
    report = Report(
        id=uuid4(), owner_id=owner.id, name="Quarterly", prompt="q",
        connection_id=connection.id, language="en", section_target=5,
        status="ACTIVE",
    )
    session.add(report)
    session.flush()
    _team_grant(
        session, type_="report", resource_id=report.id,
        privilege="select", user=reader.id,
    )

    service = ReportService(db, object(), RbacAuthorizer(db))

    assert await service.may_read_data(who(owner.id), report) is True
    assert await service.may_read_data(who(reader.id), report) is False


async def test_a_report_whose_connection_was_deleted_stays_readable(
    db: AsyncSessionShim
) -> None:
    """The `SET NULL` state, and the rule that is easy to get backwards.

    The data source was deleted out from under the document. There is no row
    left to hold a grant, so there is nobody this could be withholding the
    numbers *from* — and blanking every figure in every past report whose
    warehouse was retired is a much worse reading of *"its history stays
    readable, but it cannot be continued"* than the one every other surface
    already follows. Generating is refused separately, for its own reason.
    """
    session = db._session
    owner = _user(session, "o@test.local")
    report = Report(
        id=uuid4(), owner_id=owner.id, name="Orphan", prompt="q",
        connection_id=None, language="en", section_target=5, status="ACTIVE",
    )
    session.add(report)
    session.flush()

    service = ReportService(db, object(), RbacAuthorizer(db))
    assert await service.may_read_data(who(owner.id), report) is True


# ── conversations: sharing a thread is not sharing a database ────────────
async def test_a_shared_thread_does_not_share_its_connection(
    db: AsyncSessionShim
) -> None:
    """The rule 8c exists to state.

    A transcript is prose somebody already read; the connection behind it is a
    live credential against a customer's database. Sharing the first must
    never confer the second, and the only reason it does not is that they are
    two grants on two resource types — so this is the test that would catch
    anybody "simplifying" that into one.
    """
    session = db._session
    owner, reader = _user(session, "o@test.local"), _user(session, "r@test.local")
    connection = _connection(session, owner_id=owner.id, name="Sales")
    thread = Conversation(
        id=uuid4(), owner_id=owner.id, title="Q3", status="ACTIVE",
        default_connection_id=connection.id,
    )
    session.add(thread)
    session.flush()
    _team_grant(
        session, type_="conversation", resource_id=thread.id,
        privilege="select", user=reader.id,
    )

    authz = RbacAuthorizer(db)
    reader_ctx = who(reader.id)

    assert await authz.allowed(
        reader_ctx,
        ResourceRef(type=ResourceType.CONVERSATION, id=thread.id),
        Privilege.SELECT,
    )
    assert not await authz.allowed(
        reader_ctx,
        ResourceRef(type=ResourceType.CONNECTION, id=connection.id),
        Privilege.SELECT,
    )


async def test_a_shared_turn_keeps_its_prose_and_loses_its_numbers(
    db: AsyncSessionShim
) -> None:
    """What "renders its results as placeholders" means, on the read path.

    The reader gets the run — its status, its step trail, the sentence the
    model wrote — and gets no artifacts, no generated statement and no
    knowledge evidence. All three are the database: a stored `SELECT` names
    columns and filter values out of a schema this reader was never given, and
    the rows are the rows.
    """
    from app.api.v1.conversations import _hydrate_run
    from app.infra.db.models import Message, Run

    session = db._session
    owner = _user(session, "o@test.local")
    connection = _connection(session, owner_id=owner.id, name="Sales")
    thread = Conversation(
        id=uuid4(), owner_id=owner.id, title="Q3", status="ACTIVE",
        default_connection_id=connection.id,
    )
    session.add(thread)
    session.flush()
    question = Message(
        id=uuid4(), conversation_id=thread.id, seq=1, role="USER",
        content="revenue last quarter",
    )
    session.add(question)
    session.flush()
    run = Run(
        id=uuid4(), conversation_id=thread.id, user_message_id=question.id,
        owner_id=owner.id,
        actor_id=owner.id, connection_id=connection.id, status="SUCCEEDED",
        model_snapshot={"connection_name": "Sales", "model": "gpt"},
        prompt_version="v9",
    )
    session.add(run)
    session.flush()

    withheld = await _hydrate_run(db, run, may_read_data=False)

    assert withheld.status == "SUCCEEDED"
    assert withheld.restricted is True
    assert "Sales" in (withheld.restricted_reason or "")
    assert withheld.artifacts == [] and withheld.queries == []


# ── model configurations: the ceiling ────────────────────────────────────
@pytest.mark.parametrize("privilege", ["modify", "delete", "manage"])
async def test_a_model_configuration_cannot_be_shared_above_select(
    db: AsyncSessionShim, privilege: str
) -> None:
    """⚠️ `modify` on an `llm_config` is equivalent to disclosing its API key.

    A holder can repoint `base_url` at a host they control and read the key
    out of the next request's `Authorization` header. So the API refuses the
    three privileges above `select` wherever they are asked for, and the
    refusal says what to grant instead.
    """
    from app.infra.db.models import LlmConfig
    from app.services.grant_service import GrantService

    session = db._session
    owner, reader = _user(session, "o@test.local"), _user(session, "r@test.local")
    config = LlmConfig(
        id=uuid4(), owner_id=owner.id, name="house", provider="OpenAI-compatible",
        model="gpt", base_url="https://api.example.com", temperature=0.2,
        max_tokens=2048, params={}, embedding_model="", embedding_params={},
        capabilities={}, status="UNTESTED",
    )
    session.add(config)
    session.flush()

    service = GrantService(db, RbacAuthorizer(db))
    with pytest.raises(ValidationError) as refusal:
        await service.grant(
            who(owner.id),
            ResourceRef(type=ResourceType.LLM_CONFIG, id=config.id),
            privilege=Privilege(privilege),
            user_id=reader.id,
        )

    assert "API key" in str(refusal.value)
    assert "select" in str(refusal.value)


@pytest.mark.parametrize("privilege", ["describe", "select"])
async def test_the_two_safe_privileges_on_a_model_configuration_are_grantable(
    db: AsyncSessionShim, privilege: str
) -> None:
    """The other half, and the one that makes the product work: a second user
    on a fresh install can be given the house model and ask a question with
    it, without anybody sharing a key."""
    from app.infra.db.models import LlmConfig
    from app.services.grant_service import GrantService

    session = db._session
    owner, reader = _user(session, "o@test.local"), _user(session, "r@test.local")
    config = LlmConfig(
        id=uuid4(), owner_id=owner.id, name="house", provider="OpenAI-compatible",
        model="gpt", base_url="https://api.example.com", temperature=0.2,
        max_tokens=2048, params={}, embedding_model="", embedding_params={},
        capabilities={}, status="UNTESTED",
    )
    session.add(config)
    session.flush()

    await GrantService(db, RbacAuthorizer(db)).grant(
        who(owner.id),
        ResourceRef(type=ResourceType.LLM_CONFIG, id=config.id),
        privilege=Privilege(privilege),
        user_id=reader.id,
    )

    assert await RbacAuthorizer(db).allowed(
        who(reader.id),
        ResourceRef(type=ResourceType.LLM_CONFIG, id=config.id),
        Privilege(privilege),
    )


# ── moving the endpoint clears the key ───────────────────────────────────
def _llm_client(db: Any) -> Any:
    """The provider routes, with a session and a secret box that record.

    A `TestClient` rather than a direct call because the rule under test lives
    in the PATCH handler — it compares the row's endpoint before and after the
    patch — and a service-level test would be testing a function that does not
    exist.
    """
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_secret_box] = lambda: _Box()
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=_OWNER, email="o@test.local", correlation_id="t"
    )
    return TestClient(app, raise_server_exceptions=False)


_OWNER = uuid4()


class _Box:
    key_version = 1

    def encrypt(self, value: str, *, aad: str) -> str:
        return f"enc:{aad}:{value}"

    def decrypt(self, value: str, *, aad: str) -> str:
        return value.removeprefix(f"enc:{aad}:")


class _OneRow:
    """A session holding one provider row, and collecting what is written."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.added: list[Any] = []

    async def execute(self, _statement: Any) -> Any:
        return _Scalar(self.row)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _Scalar:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row

    def scalars(self) -> list[Any]:
        return [self._row] if self._row is not None else []

    def all(self) -> list[Any]:
        return []


def _config() -> Any:
    from app.infra.db.models import LlmConfig

    return LlmConfig(
        id=uuid4(), owner_id=_OWNER, name="house", provider="OpenAI-compatible",
        model="gpt", base_url="https://api.openai.com/v1",
        encrypted_api_key="enc:secret", key_version=1, temperature=0.2,
        max_tokens=2048, params={}, embedding_model="", embedding_params={},
        capabilities={}, status="OK",
    )


def test_moving_the_base_url_clears_the_stored_key() -> None:
    """⚠️ The mitigation for `modify` being key-equivalent.

    Somebody who can edit this row can point it at a host they control. If the
    key survived that, the credential would leave the product without anybody
    deciding to disclose it — so it does not survive: the row keeps working as
    a configuration and stops working as a credential until somebody who has
    the new endpoint's key types it in.
    """
    row = _config()
    db = _OneRow(row)

    response = _llm_client(db).patch(
        f"/api/v1/llm-configs/{row.id}",
        json={"base_url": "https://gateway.attacker.example/v1"},
    )

    assert response.status_code == 200, response.text
    assert row.encrypted_api_key is None
    assert row.status == "UNTESTED"
    assert response.json()["has_api_key"] is False


def test_changing_the_provider_clears_it_too() -> None:
    """The same secret sent to a different company is the same disclosure. A
    rule that watched only the URL would let this through while blocking the
    cosmetically similar case."""
    row = _config()
    db = _OneRow(row)

    response = _llm_client(db).patch(
        f"/api/v1/llm-configs/{row.id}",
        json={"provider": "Anthropic", "model": "claude-sonnet-5"},
    )

    assert response.status_code == 200, response.text
    assert row.encrypted_api_key is None


def test_a_rename_keeps_the_key() -> None:
    """The negative case, and the one that makes the rule usable: editing
    anything that is not the endpoint must not cost the credential."""
    row = _config()
    db = _OneRow(row)

    response = _llm_client(db).patch(
        f"/api/v1/llm-configs/{row.id}", json={"name": "House model"}
    )

    assert response.status_code == 200, response.text
    assert row.encrypted_api_key == "enc:secret"


def test_moving_the_endpoint_and_supplying_the_new_key_keeps_the_new_key() -> None:
    """The honest way to move a configuration, in one call. Clearing here
    would make the rule mean "you may never change the endpoint"."""
    row = _config()
    db = _OneRow(row)

    response = _llm_client(db).patch(
        f"/api/v1/llm-configs/{row.id}",
        json={"base_url": "https://vllm.internal/v1", "api_key": "new-secret"},
    )

    assert response.status_code == 200, response.text
    assert row.encrypted_api_key is not None
    assert "new-secret" in row.encrypted_api_key


def test_the_move_is_audited_and_the_row_holds_no_key() -> None:
    """`llm_config.endpoint.changed`, with identifiers and a boolean — never
    the secret, and never the row values rule 3 of `services/audit.py`
    forbids."""
    from app.infra.db.models import AuditLog as Row

    row = _config()
    db = _OneRow(row)

    _llm_client(db).patch(
        f"/api/v1/llm-configs/{row.id}",
        json={"base_url": "https://gateway.attacker.example/v1"},
    )

    written = [entry for entry in db.added if isinstance(entry, Row)]
    assert len(written) == 1
    assert written[0].action == "llm_config.endpoint.changed"
    assert written[0].detail["key_cleared"] is True
    assert "secret" not in str(written[0].detail)
