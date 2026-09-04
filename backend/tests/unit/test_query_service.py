"""The second entry point into the guarded path.

A dashboard tile runs stored SQL with no model involved, which is a second
chance to bypass the SQL guard. These tests are the proof that it does not get
one: the corpus from `test_sqlguard_hostile.py` is replayed *through the tile
executor*, so a statement typed straight into `dashboard_tiles.sql` is stopped
by the same wall a generated statement is.

The rest of the file covers the other five rules of the service: re-validate on
every execution, re-check ownership on every execution, a tile may only tighten
the connection's containment, the connector is always closed, and a tile
failure is a value rather than an exception.
"""
from __future__ import annotations

import base64
import os
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.errors import ConnectorError
from app.domain.ports.database import QueryResult, ResultColumn
from app.services import query_service
from app.services.query_service import (
    TileRequest,
    TileResult,
    effective_max_rows,
    execute_many,
    execute_saved_sql,
)
from tests.unit.test_sqlguard_hostile import HOSTILE

OWNER = uuid4()

# The same five tables the hostile corpus is written against, in the shape a
# stored snapshot has — so the corpus can be replayed through a real tile.
SNAPSHOT: dict[str, Any] = {
    "dialect": "postgres",
    "relationships": [],
    "tables": [
        {
            "schema": "public",
            "name": "orders",
            "columns": [
                {"name": n}
                for n in ("id", "customer_id", "order_date", "status", "total_amount")
            ],
        },
        {
            "schema": "public",
            "name": "order_items",
            "columns": [
                {"name": n}
                for n in ("id", "order_id", "product_id", "quantity", "unit_price")
            ],
        },
        {
            "schema": "public",
            "name": "products",
            "columns": [{"name": n} for n in ("id", "name", "category", "price")],
        },
        {
            "schema": "public",
            "name": "customers",
            "columns": [{"name": n} for n in ("id", "name", "region_id", "signed_up_at")],
        },
        {
            "schema": "public",
            "name": "regions",
            "columns": [{"name": n} for n in ("id", "name")],
        },
    ],
}

VALID_SQL = "SELECT status, total_amount FROM public.orders"

COLUMNS = [
    ResultColumn(name="status", db_type="text", semantic_type="nominal"),
    ResultColumn(name="total_amount", db_type="numeric", semantic_type="quantitative"),
]
ROWS: list[list[Any]] = [["paid", 120.0], ["pending", 60.0], ["refunded", 20.0]]


class FakeConnection:
    """Only what the service reads off a `DatabaseConnection` row."""

    def __init__(
        self,
        *,
        owner_id: UUID = OWNER,
        max_rows: int = 1000,
        statement_timeout_ms: int = 30_000,
    ) -> None:
        self.id = uuid4()
        self.owner_id = owner_id
        self.name = "sales"
        self.database_type = "postgres"
        self.host = "db.internal"
        self.port = 5432
        self.database_name = "sales"
        self.username = "readonly"
        self.encrypted_password = "not-a-real-envelope"
        self.ssl_mode = None
        self.max_rows = max_rows
        self.statement_timeout_ms = statement_timeout_ms


class FakeConnector:
    """Records what it was asked to run; never touches a network."""

    def __init__(
        self,
        *,
        columns: list[ResultColumn] | None = None,
        rows: list[list[Any]] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.closed = 0
        self._columns = COLUMNS if columns is None else columns
        self._rows = ROWS if rows is None else rows
        self._raises = raises

    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        self.calls.append((sql, max_rows, statement_timeout_ms))
        if self._raises is not None:
            raise self._raises
        return QueryResult(
            columns=list(self._columns),
            rows=[list(r) for r in self._rows],
            row_count=len(self._rows),
            duration_ms=7,
        )

    async def close(self) -> None:
        self.closed += 1


class FakeSnapshotRow:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.tables = snapshot["tables"]
        self.relationships = snapshot["relationships"]
        self.dialect = snapshot["dialect"]
        self.catalog_meta = snapshot.get("catalog_meta") or {}


class FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class FakeDb:
    """Answers the one query the service makes, and counts it."""

    def __init__(self, snapshot: dict[str, Any] | None = SNAPSHOT) -> None:
        self.snapshot = snapshot
        self.queries = 0

    async def execute(self, _statement: Any) -> FakeResult:
        self.queries += 1
        return FakeResult(None if self.snapshot is None else FakeSnapshotRow(self.snapshot))


class FakeKey:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class FakeSettings:
    """The fields the service reads, with a key of its own.

    Built per call rather than read from `get_settings()`: the real settings
    are process-cached, and a test that has already exercised a missing key
    would otherwise decide whether this one passes.

    The report worker imports it too, which is why `report_narration_concurrency`
    is here — carrying the *production* default rather than a convenient one, so
    a generation test exercises the wave size real runs use.
    """

    def __init__(self) -> None:
        self.secret_box_key = FakeKey(
            base64.urlsafe_b64encode(os.urandom(32)).decode()
        )
        self.secret_box_key_version = 1
        self.report_narration_concurrency = 4


def settings() -> Any:
    return FakeSettings()


async def run_tile(**kwargs: Any) -> TileResult:
    """`execute_saved_sql` with the boring arguments filled in."""
    kwargs.setdefault("sql", VALID_SQL)
    kwargs.setdefault("connection", FakeConnection())
    kwargs.setdefault("owner_id", OWNER)
    kwargs.setdefault("snapshot", SNAPSHOT)
    return await execute_saved_sql(FakeDb(), settings(), **kwargs)


# ── rule 1: re-validate on every execution ───────────────────────────────
@pytest.mark.parametrize("sql,_code", HOSTILE)
async def test_hostile_sql_typed_into_a_tile_is_rejected_at_refresh(
    sql: str, _code: str | None
) -> None:
    """The whole point of the tile executor having no privileged path.

    `dashboard_tiles.sql` is a textarea's worth of user input. Every statement
    the guard refuses for a model must be refused for a tile, at refresh, with
    nothing reaching the connector.
    """
    connector = FakeConnector()

    result = await run_tile(sql=sql, connector=connector)

    assert result.status == "ERROR"
    assert connector.calls == []


async def test_a_dropped_table_fails_closed_with_schema_changed() -> None:
    """A re-sync that dropped a table must break the tile loudly.

    Not a stale result, and not an empty one that reads as "no data" — the tile
    says the schema moved and points at the fix.
    """
    without_orders = {
        **SNAPSHOT,
        "tables": [t for t in SNAPSHOT["tables"] if t["name"] != "orders"],
    }
    connector = FakeConnector()

    result = await run_tile(snapshot=without_orders, connector=connector)

    assert result.status == "ERROR"
    assert result.error_code == "E_SCHEMA_CHANGED"
    assert "sync" in (result.error_message or "").lower()
    assert connector.calls == []


async def test_an_unsynced_connection_is_told_to_sync_not_that_the_sql_is_bad() -> None:
    connector = FakeConnector()

    result = await run_tile(
        snapshot={"tables": [], "relationships": [], "dialect": "postgres"},
        connector=connector,
    )

    assert result.error_code == "E_NO_SNAPSHOT"
    assert connector.calls == []


async def test_the_snapshot_is_loaded_when_the_caller_does_not_supply_one() -> None:
    """The single-tile path re-reads the schema itself; nothing is cached."""
    db = FakeDb()
    connector = FakeConnector()

    result = await execute_saved_sql(
        db,
        settings(),
        sql=VALID_SQL,
        connection=FakeConnection(),
        owner_id=OWNER,
        connector=connector,
    )

    assert result.status == "OK"
    assert db.queries == 1


async def test_an_empty_tile_runs_nothing() -> None:
    connector = FakeConnector()

    result = await run_tile(sql="   ", connector=connector)

    assert result.status == "ERROR"
    assert connector.calls == []


# ── rule 2: re-check ownership on every execution ────────────────────────
async def test_a_connection_owned_by_someone_else_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked at execution, not only at save: the tile row can be edited, and
    a connection can be deleted, underneath a saved tile."""
    binds: list[Any] = []
    monkeypatch.setattr(
        query_service, "bind_connector", lambda conn, box: binds.append(conn)
    )

    result = await run_tile(connection=FakeConnection(owner_id=uuid4()))

    assert result.status == "ERROR"
    assert result.error_code == "E_FORBIDDEN"
    # Nothing was decrypted and nothing was dialled.
    assert binds == []


# ── rule 3: containment is the connection's ──────────────────────────────
def test_a_tile_may_only_lower_the_connections_row_cap() -> None:
    connection = FakeConnection(max_rows=1000)

    assert effective_max_rows(connection, None) == 1000
    assert effective_max_rows(connection, 5000) == 1000    # clamped down
    assert effective_max_rows(connection, 100) == 100      # tightening allowed
    assert effective_max_rows(connection, 0) == 1000       # 0 is "unset", not "none"


async def test_an_oversized_tile_cap_is_clamped_in_both_the_sql_and_the_call() -> None:
    """The rewriter's LIMIT and the executor's row budget must be one number.

    If the tile's cap only reached the connector, the guard would happily
    rewrite `LIMIT 5000` into SQL the row budget then contradicts.
    """
    connector = FakeConnector()

    await run_tile(
        connection=FakeConnection(max_rows=1000), max_rows=5000, connector=connector
    )

    sql, max_rows, timeout = connector.calls[0]
    assert max_rows == 1000
    assert "1000" in sql and "5000" not in sql
    assert timeout == 30_000


async def test_a_lower_tile_cap_reaches_the_driver() -> None:
    connector = FakeConnector()

    await run_tile(max_rows=50, connector=connector)

    sql, max_rows, _ = connector.calls[0]
    assert max_rows == 50
    assert "50" in sql


# ── rule 4: always close the connector ───────────────────────────────────
async def test_a_connector_this_call_opened_is_closed_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = FakeConnector()
    monkeypatch.setattr(query_service, "bind_connector", lambda conn, box: connector)

    result = await run_tile()

    assert result.status == "OK"
    assert connector.closed == 1


async def test_a_connector_this_call_opened_is_closed_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = FakeConnector(raises=ConnectorError("statement timeout"))
    monkeypatch.setattr(query_service, "bind_connector", lambda conn, box: connector)

    result = await run_tile()

    assert result.error_code == "E_QUERY_FAILED"
    assert connector.closed == 1


async def test_a_connector_the_caller_owns_is_left_open() -> None:
    """The batch path opens one connector for a dozen tiles; the first tile to
    finish must not close it underneath the other eleven."""
    connector = FakeConnector()

    await run_tile(connector=connector)

    assert connector.closed == 0


# ── rule 5: a failure is a value ─────────────────────────────────────────
async def test_a_driver_error_becomes_a_tile_error_not_an_exception() -> None:
    connector = FakeConnector(raises=ConnectorError("relation does not exist"))

    result = await run_tile(connector=connector)

    assert result.status == "ERROR"
    assert result.error_code == "E_QUERY_FAILED"
    assert result.rows == []


async def test_an_unexpected_crash_becomes_a_tile_error_too() -> None:
    """One tile may never fail the dashboard response, whatever it hit."""
    connector = FakeConnector(raises=RuntimeError("driver exploded"))

    result = await run_tile(connector=connector)

    assert result.status == "ERROR"
    assert result.error_code == "E_INTERNAL"


# ── rule 6: the chart is decided here ────────────────────────────────────
def _intent(chart_type: str) -> Any:
    from app.charts import AxisSpec, ChartIntent

    return ChartIntent(
        chart_type=chart_type,
        x_axis=AxisSpec(field="status", type="nominal"),
        y_axis=AxisSpec(field="total_amount", type="quantitative"),
    )


async def test_a_null_intent_re_plans_from_the_result() -> None:
    result = await run_tile(connector=FakeConnector())

    assert result.status == "OK"
    assert result.vega_spec is not None
    assert result.chart_source == "heuristic"
    assert result.chart_note is None


async def test_a_stored_intent_is_a_suggestion_not_a_verdict() -> None:
    """A pie of thirty categories is a well-formed intent and a useless
    picture: the plan demotes it, the tile still returns its data, and the note
    is what the UI says out loud."""
    rows = [[f"status-{i}", float(100 - i)] for i in range(30)]
    connector = FakeConnector(rows=rows)

    result = await run_tile(chart_intent=_intent("pie"), connector=connector)

    assert result.status == "OK"
    assert result.row_count == 30
    assert result.vega_spec is not None
    assert result.chart_note is not None
    assert "pie" in result.chart_note


async def test_an_intent_naming_a_column_the_result_lost_degrades_to_a_table() -> None:
    """Never a 500, never an empty tile — the numbers are right whatever
    picture was asked for."""
    from app.charts import AxisSpec, ChartIntent

    gone = ChartIntent(
        chart_type="bar",
        x_axis=AxisSpec(field="region", type="nominal"),
        y_axis=AxisSpec(field="revenue", type="quantitative"),
    )

    result = await run_tile(chart_intent=gone, connector=FakeConnector())

    assert result.status == "OK"
    assert result.row_count == 3
    # It fell through to the heuristic rather than failing.
    assert result.chart_source in ("heuristic", "none")


# ── the batch path ───────────────────────────────────────────────────────
def _request(connection: Any, sql: str = VALID_SQL, **kwargs: Any) -> TileRequest:
    return TileRequest(tile_id=uuid4(), sql=sql, connection=connection, **kwargs)


async def test_tiles_on_one_connection_share_one_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Twelve tiles on one database must not open twelve connections."""
    connector = FakeConnector()
    opened: list[Any] = []

    def bind(conn: Any, box: Any) -> Any:
        opened.append(conn)
        return connector

    monkeypatch.setattr(query_service, "bind_connector", bind)

    connection = FakeConnection()
    requests = [_request(connection) for _ in range(12)]

    results = await execute_many(
        FakeDb(), settings(), requests=requests, owner_id=OWNER
    )

    assert len(results) == 12
    assert all(r.status == "OK" for r in results.values())
    assert len(opened) == 1
    assert connector.closed == 1
    assert len(connector.calls) == 12


async def test_two_connections_get_one_connector_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connectors: list[FakeConnector] = []

    def bind(conn: Any, box: Any) -> Any:
        connectors.append(FakeConnector())
        return connectors[-1]

    monkeypatch.setattr(query_service, "bind_connector", bind)

    first, second = FakeConnection(), FakeConnection()
    requests = [_request(first), _request(first), _request(second)]

    results = await execute_many(
        FakeDb(), settings(), requests=requests, owner_id=OWNER
    )

    assert len(results) == 3
    assert [len(c.calls) for c in connectors] == [2, 1]
    assert all(c.closed == 1 for c in connectors)


async def test_one_broken_tile_does_not_take_the_others_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        query_service, "bind_connector", lambda conn, box: FakeConnector()
    )

    connection = FakeConnection()
    good, hostile = _request(connection), _request(connection, "DROP TABLE orders")

    results = await execute_many(
        FakeDb(), settings(), requests=[good, hostile], owner_id=OWNER
    )

    assert results[good.tile_id].status == "OK"
    assert results[hostile.tile_id].status == "ERROR"


async def test_a_foreign_connection_is_never_dialled_in_a_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Any] = []
    monkeypatch.setattr(
        query_service, "bind_connector", lambda conn, box: opened.append(conn)
    )

    request = _request(FakeConnection(owner_id=uuid4()))

    results = await execute_many(
        FakeDb(), settings(), requests=[request], owner_id=OWNER
    )

    assert results[request.tile_id].error_code == "E_FORBIDDEN"
    assert opened == []


async def test_an_unsynced_connection_costs_no_connection_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Any] = []
    monkeypatch.setattr(
        query_service, "bind_connector", lambda conn, box: opened.append(conn)
    )

    request = _request(FakeConnection())
    results = await execute_many(
        FakeDb(snapshot=None), settings(), requests=[request], owner_id=OWNER
    )

    assert results[request.tile_id].error_code == "E_NO_SNAPSHOT"
    assert opened == []


async def test_the_snapshot_is_read_once_per_connection_not_once_per_tile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every database read happens before the tiles fan out — an AsyncSession
    is not safe for concurrent use."""
    monkeypatch.setattr(
        query_service, "bind_connector", lambda conn, box: FakeConnector()
    )

    db = FakeDb()
    connection = FakeConnection()
    await execute_many(
        db,
        settings(),
        requests=[_request(connection) for _ in range(5)],
        owner_id=OWNER,
    )

    assert db.queries == 1


async def test_an_empty_batch_touches_nothing() -> None:
    assert await execute_many(FakeDb(), settings(), requests=[], owner_id=OWNER) == {}
