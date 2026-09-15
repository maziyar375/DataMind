"""A real schema for the semantic layer's write paths, on SQLite.

The version tests assert things about statements — a lock, a join to the
author, change counts grouped per version — and a fake that answered queries by
inspecting them would prove nothing about the statements. So, like
`conftest.py`, these run the real SQL against an in-memory database behind the
async surface the service calls, with the same Postgres-syntax edits.

Not a `conftest.py`: the tables here are a different set, and the fixtures are
imported by name into the two files that use them.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.context import RequestContext
from app.infra.db.models import (
    AuditLog,
    Base,
    DatabaseConnection,
    LlmConfig,
    SchemaSnapshotRow,
    SemanticLayerChangeRow,
    SemanticLayerRow,
    SemanticLayerVersionRow,
    User,
)
from app.semantic import (
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
)
from app.services import semantic_service
from app.services.semantic_service import SemanticService, document_sha256
from tests.unit.conftest import AsyncSessionShim, _refuse_lazy_loads, _UtcDateTime

TABLES = (
    "users", "llm_configs", "database_connections", "schema_snapshots",
    "semantic_layers", "semantic_layer_versions", "semantic_layer_changes",
    "semantic_jobs", "audit_logs",
)

AUTHOR = uuid4()
OTHER = uuid4()


class SessionShim(AsyncSessionShim):
    """The conftest shim, plus the two calls a worker path makes."""

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()

    async def refresh(self, obj: Any) -> None:
        self._session.refresh(obj)


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    metadata = sa.MetaData()
    for name in TABLES:
        table = Base.metadata.tables[name].to_metadata(metadata)
        for column in table.columns:
            if "::" in str(getattr(column.server_default, "arg", "")):
                column.server_default = None
            if isinstance(column.type, ARRAY | JSONB):
                column.type = sa.JSON()
            if column.primary_key and isinstance(column.type, sa.BigInteger):
                column.type = sa.Integer()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    engine.dialect.colspecs = {
        **engine.dialect.colspecs, sa.DateTime: _UtcDateTime, sa.ARRAY: sa.JSON,
    }

    @sa.event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    return engine


@pytest.fixture
def db(engine: sa.Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[SessionShim]:
    """One session for the request and for every session a worker opens.

    `_persist_generated` and `_touch_job` open sessions of their own through
    `get_sessionmaker`; here they get this one, so what the job writes is
    visible to the test the moment it commits, as it would be to a poller.
    """
    with Session(engine, expire_on_commit=False) as session:
        _refuse_lazy_loads(session)
        shim = SessionShim(session)

        @contextlib.asynccontextmanager
        async def opened() -> AsyncIterator[SessionShim]:
            yield shim

        monkeypatch.setattr(semantic_service, "get_sessionmaker", lambda: (lambda: opened()))
        for user_id, name in ((AUTHOR, "Sara Karimi"), (OTHER, "Ali Rezaei")):
            session.add(User(
                id=user_id, email=f"{user_id.hex[:6]}@test.local", display_name=name,
                password_hash="x", status="ACTIVE", kind="HUMAN",
            ))
        session.flush()
        yield shim
        session.rollback()
        for name in reversed(TABLES):
            session.execute(sa.text(f"DELETE FROM {name}"))  # noqa: S608
        session.commit()


def ctx(user_id: UUID = AUTHOR) -> RequestContext:
    return RequestContext(user_id=user_id, email="curator@test.local")


def orders(*columns: str) -> dict[str, Any]:
    return {
        "schema": "public", "name": "orders",
        "columns": [
            {"name": c, "data_type": "numeric", "is_primary_key": c == "id"}
            for c in columns
        ],
    }


def customers() -> dict[str, Any]:
    return {
        "schema": "public", "name": "customers",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "region", "data_type": "text"},
        ],
    }


def connection(db: SessionShim, *, enabled: bool = True) -> DatabaseConnection:
    row = DatabaseConnection(
        id=uuid4(), owner_id=AUTHOR, name=f"sales-{uuid4().hex[:6]}",
        database_type="postgres", host="db", port=5432, database_name="sales",
        username="ro", encrypted_password="x", key_version=1,
        schema_allowlist=["public"], disclosure_policy="SAMPLE",
        semantic_layer_enabled=enabled,
    )
    db.add(row)
    db._session.flush()
    sync(db, row, orders("id", "amount", "status"), customers())
    return row


def sync(db: SessionShim, conn: DatabaseConnection, *tables: dict[str, Any]) -> None:
    """A re-sync: the next snapshot version, holding exactly `tables`."""
    latest = db._session.scalar(
        sa.select(sa.func.max(SchemaSnapshotRow.version)).where(
            SchemaSnapshotRow.connection_id == conn.id
        )
    ) or 0
    db.add(SchemaSnapshotRow(
        id=uuid4(), connection_id=conn.id, version=latest + 1, dialect="postgres",
        tables=list(tables), relationships=[], table_count=len(tables), catalog_meta={},
    ))
    db._session.flush()


def llm_config(db: SessionShim) -> LlmConfig:
    row = LlmConfig(
        id=uuid4(), owner_id=AUTHOR, name=f"m-{uuid4().hex[:6]}", provider="openai",
        model="gpt-4o-mini", temperature=0.0, max_tokens=1024, capabilities={},
    )
    db.add(row)
    db._session.flush()
    return row


def layer() -> SemanticDocument:
    return SemanticDocument(
        entities=[
            SemanticEntity(
                table="public.orders",
                label="Orders",
                grain="one row per order",
                columns=[SemanticColumn(name="amount", label="Order value")],
                metrics=[
                    SemanticMetric(
                        name="revenue", expression="SUM(amount)",
                        filters=["status <> 'CANCELLED'"],
                    )
                ],
            ),
            SemanticEntity(table="public.customers", label="Customers"),
        ]
    )


def service(db: SessionShim) -> SemanticService:
    return SemanticService(db, Settings())  # type: ignore[arg-type]


def head(db: SessionShim, conn: DatabaseConnection) -> SemanticLayerRow | None:
    return db._session.scalar(
        sa.select(SemanticLayerRow).where(SemanticLayerRow.connection_id == conn.id)
    )


def versions(db: SessionShim, conn: DatabaseConnection) -> list[SemanticLayerVersionRow]:
    return list(db._session.scalars(
        sa.select(SemanticLayerVersionRow)
        .where(SemanticLayerVersionRow.connection_id == conn.id)
        .order_by(SemanticLayerVersionRow.version)
    ))


def change_rows(db: SessionShim, version: SemanticLayerVersionRow) -> list[tuple[str, str, str, bool]]:
    return sorted(
        (row.kind, row.entity_key, row.item_key, row.affects_sql)
        for row in db._session.scalars(
            sa.select(SemanticLayerChangeRow).where(
                SemanticLayerChangeRow.version_id == version.id
            )
        )
    )


def audit_rows(db: SessionShim, action: str) -> list[AuditLog]:
    return list(db._session.scalars(sa.select(AuditLog).where(AuditLog.action == action)))


def assert_head_is_its_published_version(db: SessionShim, conn: DatabaseConnection) -> None:
    """D3: what the model reads is exactly one numbered version, byte for byte."""
    row = head(db, conn)
    assert row is not None and row.published_version is not None
    [published] = [v for v in versions(db, conn) if v.version == row.published_version]
    assert document_sha256(row.document or {}) == published.document_sha256
    assert row.document == published.document
