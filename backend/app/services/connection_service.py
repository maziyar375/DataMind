"""What a connection does to the database it points at: probe it, and sync it.

The router owns the HTTP shape and the access check; this module owns the two
operations that open a socket to the customer's database and write what came
back. Both go through `query_service.bind_connector`, the one place the stored
password is decrypted against its row identity.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.domain.ports.database import ConnectionProbe, DatabaseConnector
from app.domain.ports.secrets import SecretBox
from app.domain.value_objects import HintBudget
from app.infra.connectors.factory import build_connector
from app.infra.db.models import DatabaseConnection, SchemaSnapshotRow
from app.services.knowledge_service import KnowledgeService
from app.services.query_service import bind_connector, latest_snapshot_version


async def _probe(connector: DatabaseConnector) -> ConnectionProbe:
    try:
        return await connector.probe()
    finally:
        await connector.close()


async def probe_draft(**params: Any) -> ConnectionProbe:
    """Probe credentials straight from a form. Nothing is written: the form may
    hold unsaved edits, so only `probe_stored` records status against a row."""
    return await _probe(build_connector(**params))


async def probe_stored(
    db: AsyncSession, box: SecretBox, connection: DatabaseConnection
) -> ConnectionProbe:
    """Probe the stored credentials and record the outcome on the row."""
    probe = await _probe(bind_connector(connection, box))
    connection.status = "OK" if probe.ok else "ERROR"
    connection.readonly_confirmed = probe.readonly_confirmed
    connection.server_version = probe.server_version
    connection.last_tested_at = utcnow()
    await db.flush()
    return probe


async def sync_schema(
    db: AsyncSession,
    box: SecretBox,
    settings: Settings,
    connection: DatabaseConnection,
) -> SchemaSnapshotRow:
    """Introspect and store a new snapshot version.

    Foreign keys are recorded from day one even though the graph view is a
    later release; backfilling them would mean re-syncing every connection.
    """
    connector = bind_connector(connection, box)
    try:
        snapshot = await connector.introspect(
            schema_allowlist=connection.schema_allowlist,
            # Column content hints are customer data, so what may be *captured*
            # is capped by the same policy that caps what may be disclosed.
            # A NONE connection stores structure only — tightening the policy
            # takes effect at once, loosening it needs a re-sync.
            hints=HintBudget.from_policy(connection.disclosure_policy),
        )
    finally:
        await connector.close()

    row = SchemaSnapshotRow(
        id=uuid.uuid4(),
        connection_id=connection.id,
        version=await latest_snapshot_version(db, connection.id) + 1,
        dialect=snapshot.dialect,
        tables=[t.as_dict() for t in snapshot.tables],
        relationships=[
            {
                "from_table": r.from_table, "from_column": r.from_column,
                "to_table": r.to_table, "to_column": r.to_column,
            }
            for r in snapshot.relationships
        ],
        table_count=len(snapshot.tables),
        # Table and column comments already ride inside `tables`. This carries
        # the two the snapshot document has no room for — the database and
        # schema descriptions — plus the counts of what the sync picked up.
        catalog_meta=snapshot.catalog_meta(),
    )
    db.add(row)
    connection.last_synced_at = utcnow()
    await db.flush()

    # Phase 4: the knowledge store is re-validated against the snapshot that
    # just landed, in the same transaction. Inline rather than queued because
    # it is `guard()` over each template and makes no call to the customer's
    # database — so the curator sees the amber rows on the screen this sync
    # returns to, rather than the next time a worker happens to run. The
    # *conflict* half is the one that executes SQL, and it stays in the worker.
    await KnowledgeService(db, settings).sweep_staleness(connection)
    return row
