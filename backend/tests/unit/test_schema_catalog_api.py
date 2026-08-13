"""Phase 2 of the catalog-metadata plan: the descriptions survive a round trip.

Phase 1 taught four connectors to read the comments a DBA already wrote. This
phase is the part that makes them *reachable*: stored on the snapshot row, and
carried out through the API in a shape the schema browser can render. Three
claims are worth more than the rest:

* **Table and column comments ride inside `tables`** — no migration, because
  that column is already JSONB — while the database and schema descriptions
  need `catalog_meta`, which is what 0012 adds. Both halves have to arrive.
* **`counts` is what the UI reports.** "Picked up 143 column descriptions" is
  the only confirmation a user gets that their documentation is being used, and
  it is denormalised so the browser reads a field instead of walking a
  document. It has to agree with the document it is derived from.
* **A database with no comments is indistinguishable from before the feature.**
  Not "mostly the same" — `catalog_meta` is `{}` and no key appears anywhere in
  the snapshot, which is what keeps every stored snapshot and the eval baseline
  comparable. `test_catalog_comments.py` asserts this for the serialisation;
  this file asserts it survives the API.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import connections as connections_api
from app.core.clock import utcnow
from app.core.context import RequestContext
from app.domain.ports.database import ColumnInfo, SchemaSnapshot, TableInfo
from app.infra.db.models import DatabaseConnection, SchemaSnapshotRow
from app.main import create_app

USER = uuid4()
CONNECTION_ID = uuid4()


# ── the snapshot a connector hands back ──────────────────────────────────
def _commented_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        dialect="postgres",
        tables=[
            TableInfo(
                schema="sales",
                name="orders",
                approx_row_count=24_000,
                comment="One row per checkout. Cancelled orders are kept.",
                columns=[
                    ColumnInfo(name="id", data_type="bigint", is_primary_key=True),
                    ColumnInfo(
                        name="status",
                        data_type="text",
                        comment="fulfilment state; 'cancelled' still bills",
                    ),
                ],
            ),
            TableInfo(
                schema="sales",
                name="customers",
                comment="Deduplicated by email, nightly.",
                columns=[ColumnInfo(name="id", data_type="bigint")],
            ),
        ],
        database_comment="Order-to-cash for the EU storefront.",
        schema_comments={"sales": "Curated marts, rebuilt nightly."},
    )


def _bare_snapshot() -> SchemaSnapshot:
    """The same shape with every comment removed — the pre-feature case."""
    return SchemaSnapshot(
        dialect="postgres",
        tables=[
            TableInfo(
                schema="sales",
                name="orders",
                approx_row_count=24_000,
                columns=[
                    ColumnInfo(name="id", data_type="bigint", is_primary_key=True),
                    ColumnInfo(name="status", data_type="text"),
                ],
            )
        ],
    )


# ── the document `catalog_meta` stores ───────────────────────────────────
def test_catalog_meta_carries_the_two_comments_that_have_nowhere_else_to_go() -> None:
    meta = _commented_snapshot().catalog_meta()
    assert meta["database_comment"] == "Order-to-cash for the EU storefront."
    assert meta["schema_comments"] == {"sales": "Curated marts, rebuilt nightly."}


def test_counts_agree_with_the_document_they_are_derived_from() -> None:
    snapshot = _commented_snapshot()
    meta = snapshot.catalog_meta()
    assert meta["counts"] == {"tables": 2, "columns": 1}

    # The whole point of denormalising is that the two cannot be checked
    # against each other at read time, so they are checked here instead.
    assert meta["counts"]["tables"] == sum(1 for t in snapshot.tables if t.comment)
    assert meta["counts"]["columns"] == sum(
        1 for t in snapshot.tables for c in t.columns if c.comment
    )


def test_a_snapshot_with_no_comments_has_an_empty_catalog_meta() -> None:
    # `{}` exactly — the column default every pre-0012 row already holds, so a
    # re-synced connection with no comments is indistinguishable from one that
    # was never re-synced at all.
    assert _bare_snapshot().catalog_meta() == {}


def test_counts_are_absent_rather_than_zero_when_nothing_was_documented() -> None:
    # A zero here would reach the UI as "0 descriptions", which reads as a
    # failure to find something rather than the normal case of there being
    # nothing to find.
    assert "counts" not in _bare_snapshot().catalog_meta()


def test_a_database_comment_alone_still_produces_a_document() -> None:
    # MySQL and Oracle have no database comment at all, and Postgres may have
    # one with no table comments underneath it. Each key stands on its own.
    meta = SchemaSnapshot(
        dialect="postgres", tables=[], database_comment="Just the database."
    ).catalog_meta()
    assert meta == {"database_comment": "Just the database."}


# ── the round trip through the API ───────────────────────────────────────
class FakeConnector:
    """Returns a prepared snapshot instead of talking to a server."""

    snapshot: SchemaSnapshot = _commented_snapshot()
    closed = False

    def __init__(self, **_: Any) -> None:
        pass

    async def introspect(self, **_: Any) -> SchemaSnapshot:
        return FakeConnector.snapshot

    async def close(self) -> None:
        FakeConnector.closed = True


class FakeSecretBox:
    def decrypt(self, _ciphertext: str, *, aad: str) -> str:
        return "password"


def _connection(owner_id: UUID = USER) -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID,
        owner_id=owner_id,
        name="sales",
        database_type="postgres",
        host="db.internal",
        port=5432,
        database_name="sales",
        username="analytics_ro",
        encrypted_password="ciphertext",
        schema_allowlist=["sales"],
        disclosure_policy="SAMPLE",
        max_rows=1000,
        statement_timeout_ms=30_000,
    )


class FakeDb:
    """Enough of an `AsyncSession` for the sync and read routes.

    The routes issue three kinds of `execute`: the owner-scoped connection
    lookup, "what is the latest version?", and "give me the newest snapshot".
    They are told apart by what the statement *selects* rather than by call
    order or by matching its SQL text — `select(SchemaSnapshotRow)` renders
    every column, `schema_snapshots.version` among them, so a substring test
    answers the version query with a whole row. A route that grows a fourth
    query fails loudly here instead of silently receiving a connection.
    """

    def __init__(self, connection: DatabaseConnection | None) -> None:
        self.connection = connection
        self.added: list[Any] = []
        self.stored: SchemaSnapshotRow | None = None

    async def execute(self, statement: Any) -> Any:
        selected = statement.column_descriptions[0]
        entity, name = selected.get("entity"), selected.get("name")
        if entity is DatabaseConnection:
            return _Result(self.connection)
        if entity is SchemaSnapshotRow and name == "SchemaSnapshotRow":
            return _Result(self.stored)
        if name == "version":
            return _Result(self.stored.version if self.stored else None)
        raise AssertionError(f"unexpected query: {statement}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, SchemaSnapshotRow):
            # The DB default the ORM would apply on flush. Set here so the
            # response path sees what a real row would carry.
            obj.created_at = utcnow()
            self.stored = obj

    async def flush(self) -> None:
        pass


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    FakeConnector.snapshot = _commented_snapshot()
    monkeypatch.setattr(
        connections_api, "build_connector", lambda **kwargs: FakeConnector(**kwargs)
    )
    db = FakeDb(_connection())

    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_secret_box] = lambda: FakeSecretBox()
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER, email="user@test.local", role="MEMBER", correlation_id="test"
    )
    client = TestClient(app)
    client.db = db  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_sync_stores_catalog_meta_on_the_row(client: Any) -> None:
    response = client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync")
    assert response.status_code == 200

    row = client.db.stored
    assert row.catalog_meta == {
        "database_comment": "Order-to-cash for the EU storefront.",
        "schema_comments": {"sales": "Curated marts, rebuilt nightly."},
        "counts": {"tables": 2, "columns": 1},
    }


def test_sync_stores_table_and_column_comments_inside_tables(client: Any) -> None:
    # These need no migration and never went through `catalog_meta` — the
    # assertion is that they are still in the snapshot document itself.
    client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync")

    orders = client.db.stored.tables[0]
    assert orders["comment"] == "One row per checkout. Cancelled orders are kept."
    status = next(c for c in orders["columns"] if c["name"] == "status")
    assert status["comment"] == "fulfilment state; 'cancelled' still bills"


def test_the_sync_response_carries_the_comments_out(client: Any) -> None:
    body = client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync").json()

    assert body["catalog_meta"]["counts"] == {"tables": 2, "columns": 1}
    assert body["catalog_meta"]["database_comment"] == (
        "Order-to-cash for the EU storefront."
    )
    orders = body["tables"][0]
    assert orders["comment"] == "One row per checkout. Cancelled orders are kept."
    status = next(c for c in orders["columns"] if c["name"] == "status")
    assert status["comment"] == "fulfilment state; 'cancelled' still bills"


def test_reading_the_schema_back_returns_what_the_sync_stored(client: Any) -> None:
    synced = client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync").json()
    read = client.get(f"/api/v1/connections/{CONNECTION_ID}/schema").json()

    assert read["catalog_meta"] == synced["catalog_meta"]
    assert read["tables"] == synced["tables"]


def test_an_undocumented_column_reports_no_comment_rather_than_an_empty_one(
    client: Any,
) -> None:
    # `None`, not `""`. The browser renders nothing for the first and an empty
    # quotation for the second.
    body = client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync").json()
    first = body["tables"][0]["columns"][0]
    assert first["name"] == "id"
    assert first["comment"] is None


def test_a_connection_with_no_comments_reads_back_as_empty(client: Any) -> None:
    FakeConnector.snapshot = _bare_snapshot()
    body = client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync").json()

    assert client.db.stored.catalog_meta == {}
    # The DTO fills the shape in so a client never has to guard, but nothing in
    # it claims a description exists.
    assert body["catalog_meta"] == {
        "database_comment": None,
        "schema_comments": {},
        "counts": {"tables": 0, "columns": 0},
    }
    assert all(c["comment"] is None for c in body["tables"][0]["columns"])


def test_a_pre_migration_row_reads_back_without_a_guard(client: Any) -> None:
    """A snapshot stored before 0012 has `{}` and must not 500 on read.

    The column is not-null with a `'{}'` default, so this is the shape every
    existing row already has rather than a hypothetical one.
    """
    client.db.stored = SchemaSnapshotRow(
        id=uuid.uuid4(),
        connection_id=CONNECTION_ID,
        version=1,
        dialect="postgres",
        tables=[{"schema": "sales", "name": "orders", "columns": []}],
        relationships=[],
        table_count=1,
        catalog_meta={},
        created_at=utcnow(),
    )
    body = client.get(f"/api/v1/connections/{CONNECTION_ID}/schema").json()
    assert body["catalog_meta"]["counts"] == {"tables": 0, "columns": 0}
    assert body["tables"][0]["comment"] is None


def test_the_connector_is_closed_even_though_it_returned_comments(client: Any) -> None:
    FakeConnector.closed = False
    client.post(f"/api/v1/connections/{CONNECTION_ID}/schema/sync")
    assert FakeConnector.closed is True
