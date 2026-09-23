"""Keeping the schema vector index current, on the product's schedule.

`docs/plans/hybrid-retrieval.md` Phase 2. One bounded pass per connection that
has an embedding model pinned; the pass itself is
`services/retrieval_index.index_schema_vectors`, and this is only the loop that
calls it and the reasons for its cadence.

**Its own loop rather than a fourth step in `knowledge_maintenance`.** That one
is about templates — staleness, conflicts, their vectors — and it runs only
over connections that *have* templates, which is exactly the wrong population
here: a connection with a documented schema and no curated questions is the one
this index helps most. The two also want different cadences, for a reason
worth writing down: a template store moves when a person edits it, while this
index moves when a **schema sync** or a **layer publish** moves the prose
underneath it, and neither of those is something the customer does on our
schedule.

**Periodic rather than event-driven, for now.** Hooking the sync and the
publish would be tighter and is the obvious next move; a loop is what makes the
index *self-correcting* in the meantime, because every pass is idempotent
against the fingerprint and costs one query when nothing moved. An event hook
that is ever missed leaves an index wrong forever; a loop that is ever missed
leaves it one pass behind.

**Nothing here can fail a question.** A pass that cannot reach the provider
leaves the vectors it had, and `retrieve` scores lexically for whatever is not
current. That is the same posture the pass itself takes, stated twice because
the two are separately forgettable.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.infra.db.models import DatabaseConnection
from app.services.retrieval_index import SchemaIndexResult, index_schema_vectors

log = get_logger(__name__)


async def _connections_to_index(db: AsyncSession) -> list[UUID]:
    """Every connection with an embedding model pinned.

    Every one, not only the ones somebody opened: an index rots on the
    customer's schedule and not on ours, and the connection nobody is looking
    at is exactly the one whose comments moved three syncs ago.

    **Here rather than in `services/retrieval_index.py`, and that is not a
    filing preference.** A `select` over an owned table inside `app/services/`
    has to compose `visible(...)` or ask `require`, and
    `test_authz_conformance.py` enforces it — correctly, because a service
    reads on somebody's behalf. A sweep has no behalf: it is upkeep of a
    derived index, with no principal and no response, and the answer is not to
    invent a god context but to put the unscoped query where the codebase
    already puts this exact one (`knowledge_maintenance.maintenance_once`).
    The service keeps the per-connection work, and every one of its callers
    hands it a connection somebody was already allowed to reach.
    """
    result = await db.execute(
        select(DatabaseConnection.id).where(DatabaseConnection.embedding_model != "")
    )
    return list(result.scalars().all())


async def schema_index_once(settings: Settings) -> int:
    """One pass over every connection with an embedding model. Returns vectors written."""
    from app.infra.db.session import get_sessionmaker

    written = 0
    async with get_sessionmaker()() as session:
        for connection_id in await _connections_to_index(session):
            connection = await session.get(DatabaseConnection, connection_id)
            if connection is None:
                continue
            try:
                out: SchemaIndexResult = await index_schema_vectors(
                    session, settings, connection
                )
                written += out.embedded
                if not out.ok:
                    log.warning(
                        "schema_index_pass_error",
                        connection_id=str(connection_id),
                        error=out.error,
                    )
            except Exception:
                # One connection's unreachable provider must not stop the pass
                # for every other connection. The next one tries again.
                log.exception(
                    "schema_index_connection_failed",
                    connection_id=str(connection_id),
                )
                await session.rollback()
        await session.commit()
    return written


async def schema_index_loop(settings: Settings) -> None:
    while True:
        # Slept first, deliberately, and for `maintenance_loop`'s reason: a
        # fleet restarting together must not call one embedding endpoint once
        # per replica in the same second.
        await asyncio.sleep(settings.schema_index_interval_seconds)
        try:
            written = await schema_index_once(settings)
            if written:
                log.info("schema_index_swept", embedded=written)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("schema_index_loop_failed")
