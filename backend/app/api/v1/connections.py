from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select, update

from app.api.deps import CtxDep, DbDep, SecretBoxDep
from app.api.schemas import (
    ConnectionCreate, ConnectionRead, ConnectionTestRequest,
    ConnectionTestResult, ConnectionUpdate, SchemaRead,
)
from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.infra.connectors.factory import build_connector
from app.infra.db.models import DatabaseConnection, SchemaSnapshotRow

router = APIRouter(prefix="/connections", tags=["connections"])


async def _owned(db, connection_id: UUID, ctx) -> DatabaseConnection:
    """Scoping happens here, not in the router body, so it cannot be forgotten."""
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.id == connection_id,
            DatabaseConnection.owner_id == ctx.user_id,
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        # 404, not 403: another user's resource should not be distinguishable
        # from one that does not exist.
        raise NotFoundError("Connection not found.")
    return connection


@router.get("", response_model=list[ConnectionRead])
async def list_connections(ctx: CtxDep, db: DbDep) -> list[DatabaseConnection]:
    result = await db.execute(
        select(DatabaseConnection)
        .where(DatabaseConnection.owner_id == ctx.user_id)
        .order_by(DatabaseConnection.created_at)
    )
    return list(result.scalars())


@router.post("", response_model=ConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: ConnectionCreate, ctx: CtxDep, db: DbDep, box: SecretBoxDep
) -> DatabaseConnection:
    existing = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.owner_id == ctx.user_id,
            DatabaseConnection.name == payload.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("You already have a connection with that name.")

    connection_id = uuid.uuid4()
    connection = DatabaseConnection(
        id=connection_id,
        owner_id=ctx.user_id,
        name=payload.name,
        database_type=payload.database_type,
        host=payload.host,
        port=payload.port,
        database_name=payload.database_name,
        username=payload.username,
        encrypted_password=box.encrypt(
            payload.password.get_secret_value(), aad=f"connection:{connection_id}"
        ),
        key_version=box.key_version,
        ssl_mode=payload.ssl_mode,
        schema_allowlist=payload.schema_allowlist,
        max_rows=payload.max_rows,
        statement_timeout_ms=payload.statement_timeout_ms,
        disclosure_policy=payload.disclosure_policy,
        is_default=payload.is_default,
    )
    db.add(connection)
    if payload.is_default:
        await _clear_other_defaults(db, ctx.user_id, connection_id)
    await db.flush()
    return connection


@router.post("/test", response_model=ConnectionTestResult)
async def test_draft_connection(
    payload: ConnectionTestRequest, ctx: CtxDep
) -> ConnectionTestResult:
    """Probe credentials before they are saved.

    Declared above `/{connection_id}` so the literal path wins the match.
    Nothing is written: there is no row yet to record a status against.
    """
    connector = build_connector(
        kind=payload.database_type,
        host=payload.host,
        port=payload.port,
        database=payload.database_name,
        username=payload.username,
        password=payload.password.get_secret_value(),
        ssl_mode=payload.ssl_mode,
    )
    try:
        probe = await connector.probe()
    finally:
        await connector.close()

    return ConnectionTestResult(
        ok=probe.ok,
        latency_ms=probe.latency_ms,
        server_version=probe.server_version,
        readonly_confirmed=probe.readonly_confirmed,
        message=probe.message,
    )


@router.get("/{connection_id}", response_model=ConnectionRead)
async def get_connection(connection_id: UUID, ctx: CtxDep, db: DbDep) -> DatabaseConnection:
    return await _owned(db, connection_id, ctx)


@router.patch("/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: UUID, payload: ConnectionUpdate,
    ctx: CtxDep, db: DbDep, box: SecretBoxDep,
) -> DatabaseConnection:
    connection = await _owned(db, connection_id, ctx)
    data = payload.model_dump(exclude_unset=True, exclude={"password"})

    for field, value in data.items():
        if value is not None:
            setattr(connection, field, value)

    if payload.password is not None:
        connection.encrypted_password = box.encrypt(
            payload.password.get_secret_value(), aad=f"connection:{connection.id}"
        )
        connection.key_version = box.key_version
        connection.status = "UNTESTED"
        connection.readonly_confirmed = False

    if payload.is_default:
        await _clear_other_defaults(db, ctx.user_id, connection.id)

    await db.flush()
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: UUID, ctx: CtxDep, db: DbDep) -> None:
    connection = await _owned(db, connection_id, ctx)
    await db.delete(connection)


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: UUID, ctx: CtxDep, db: DbDep, box: SecretBoxDep
) -> ConnectionTestResult:
    connection = await _owned(db, connection_id, ctx)
    connector = build_connector(
        kind=connection.database_type,
        host=connection.host,
        port=connection.port,
        database=connection.database_name,
        username=connection.username,
        password=box.decrypt(
            connection.encrypted_password, aad=f"connection:{connection.id}"
        ),
        ssl_mode=connection.ssl_mode,
    )
    try:
        probe = await connector.probe()
    finally:
        await connector.close()

    connection.status = "OK" if probe.ok else "ERROR"
    connection.readonly_confirmed = probe.readonly_confirmed
    connection.server_version = probe.server_version
    connection.last_tested_at = utcnow()
    await db.flush()

    return ConnectionTestResult(
        ok=probe.ok,
        latency_ms=probe.latency_ms,
        server_version=probe.server_version,
        readonly_confirmed=probe.readonly_confirmed,
        message=probe.message,
    )


@router.post("/{connection_id}/schema/sync", response_model=SchemaRead)
async def sync_schema(
    connection_id: UUID, ctx: CtxDep, db: DbDep, box: SecretBoxDep
) -> SchemaRead:
    """Introspect and store a new snapshot version.

    Foreign keys are recorded from day one even though the graph view is a
    later release; backfilling them would mean re-syncing every connection.
    """
    connection = await _owned(db, connection_id, ctx)
    connector = build_connector(
        kind=connection.database_type,
        host=connection.host,
        port=connection.port,
        database=connection.database_name,
        username=connection.username,
        password=box.decrypt(
            connection.encrypted_password, aad=f"connection:{connection.id}"
        ),
        ssl_mode=connection.ssl_mode,
    )
    try:
        snapshot = await connector.introspect(
            schema_allowlist=connection.schema_allowlist
        )
    finally:
        await connector.close()

    latest = await db.execute(
        select(SchemaSnapshotRow.version)
        .where(SchemaSnapshotRow.connection_id == connection.id)
        .order_by(SchemaSnapshotRow.version.desc())
        .limit(1)
    )
    version = (latest.scalar_one_or_none() or 0) + 1

    row = SchemaSnapshotRow(
        id=uuid.uuid4(),
        connection_id=connection.id,
        version=version,
        dialect=snapshot.dialect,
        tables=[
            {
                "schema": t.schema,
                "name": t.name,
                "approx_row_count": t.approx_row_count,
                "columns": [
                    {
                        "name": c.name, "data_type": c.data_type,
                        "nullable": c.nullable,
                        "is_primary_key": c.is_primary_key,
                        "is_foreign_key": c.is_foreign_key,
                        "references": c.references,
                    }
                    for c in t.columns
                ],
            }
            for t in snapshot.tables
        ],
        relationships=[
            {
                "from_table": r.from_table, "from_column": r.from_column,
                "to_table": r.to_table, "to_column": r.to_column,
            }
            for r in snapshot.relationships
        ],
        table_count=len(snapshot.tables),
    )
    db.add(row)
    connection.last_synced_at = utcnow()
    await db.flush()

    return _to_schema_read(row)


@router.get("/{connection_id}/schema", response_model=SchemaRead)
async def get_schema(connection_id: UUID, ctx: CtxDep, db: DbDep) -> SchemaRead:
    await _owned(db, connection_id, ctx)
    result = await db.execute(
        select(SchemaSnapshotRow)
        .where(SchemaSnapshotRow.connection_id == connection_id)
        .order_by(SchemaSnapshotRow.version.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("This connection has not been synced yet.")
    return _to_schema_read(row)


def _to_schema_read(row: SchemaSnapshotRow) -> SchemaRead:
    return SchemaRead(
        dialect=row.dialect,
        version=row.version,
        synced_at=row.created_at,
        tables=row.tables,
        relationships=row.relationships,
    )


async def _clear_other_defaults(db, owner_id: UUID, keep_id: UUID) -> None:
    await db.execute(
        update(DatabaseConnection)
        .where(
            DatabaseConnection.owner_id == owner_id,
            DatabaseConnection.id != keep_id,
        )
        .values(is_default=False)
    )
