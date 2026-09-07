from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AuthzDep, CtxDep, DbDep, SecretBoxDep, SettingsDep
from app.api.schemas import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionTestRequest,
    ConnectionTestResult,
    ConnectionUpdate,
    DisclosureWrite,
    SchemaRead,
    narrow_to_describe,
)
from app.api.v1.access import attach_access_routes
from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects import HintBudget
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.authz.compose import restrict
from app.infra.connectors.factory import build_connector
from app.infra.db.models import DatabaseConnection, SchemaSnapshotRow, User
from app.services import audit
from app.services.grant_service import DISCLOSURE_CHANGED, GrantService
from app.services.knowledge_service import KnowledgeService
from app.services.policy import require

router = APIRouter(prefix="/connections", tags=["connections"])

# `/grants`, `/actions` and `/transfer`, written once in `access.py`. Declared
# here rather than at the bottom so the literal-path routes below (`/test`)
# still win the match against `/{connection_id}`.
attach_access_routes(router, ResourceType.CONNECTION, param="connection_id")


async def _authorized(
    db, authz, connection_id: UUID, ctx, privilege: Privilege
) -> DatabaseConnection:
    """The connection, if this principal may act on it at `privilege`.

    Scoping happens here, not in the router body, so it cannot be forgotten —
    and it happens by *asking*, so the answer changes with the authorizer
    rather than with this file. `privilege` is what the caller is about to do:
    `describe` to know it exists, `select` to read the row or query through it,
    `modify` to edit its credentials or re-sync it, `delete` to destroy it,
    `manage` to share it or change its disclosure policy. Under
    `OwnerOnlyAuthorizer` all five answer the same; under `RbacAuthorizer` — the
    default from Phase 6 — they are five different questions, which is why they
    were named before they differed.
    """
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise NotFoundError("Connection not found.")
    # `require` is the one place the 404/403 rule lives: 404 when nothing at
    # all reaches this principal, 403 naming the privilege when something does.
    # A second copy here is exactly how the two answers drift and turn a list
    # endpoint into an existence oracle.
    await require(
        ctx, authz, ResourceRef.to(ResourceType.CONNECTION, connection), privilege,
        db=db,
    )
    return connection


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> list[ConnectionRead]:
    """Every connection this principal may see, in one query.

    `describe` is the floor on purpose: a Data Engineer holds it over every
    connection and still needs `select` to ask a question through one, so the
    list is *"what exists that I may know about"* rather than *"what I may
    read"*.

    From Phase 6 the list therefore contains connections this principal was
    **granted** as well as ones they own, and each row carries two things it
    did not before: the privileges the caller holds on it — so the card can
    render read-only rather than offering an Edit the server would refuse — and
    the owner's display name, which is the answer to *"whose is this?"* on a
    screen that now shows other people's.

    A row this principal holds only `describe` on comes back **without its
    host, port, database name or username**. That is `narrow_to_describe`, and
    the loop below is the one place a list applies it.
    """
    visible = await authz.visible(ctx, ResourceType.CONNECTION, Privilege.DESCRIBE)
    result = await db.execute(
        restrict(
            select(DatabaseConnection).order_by(DatabaseConnection.created_at),
            DatabaseConnection.id,
            visible,
        )
    )
    rows = list(result.scalars())
    owners = await _owner_names(db, {row.owner_id for row in rows if row.owner_id})

    out: list[ConnectionRead] = []
    for row in rows:
        # One `privileges_on` per row, and this is the one place in the product
        # where that is the right shape rather than the anti-pattern: the
        # *filtering* was done by `visible` in one query above, and this is
        # rendering, not access. It is bounded by the page the caller can
        # already see, and the alternative — a second endpoint per card — is
        # the same N requests with latency added.
        held = await authz.privileges_on(
            ctx, ResourceRef.to(ResourceType.CONNECTION, row)
        )
        read = ConnectionRead.model_validate(row).model_copy(
            update={
                "privileges": sorted(str(p) for p in held),
                "owner": owners.get(row.owner_id, ""),
            }
        )
        out.append(read if Privilege.SELECT in held else narrow_to_describe(read))
    return out


async def _owner_names(db, ids: set) -> dict:
    """Display names for the owner column. **Never an address.**

    The rule the review queue already follows, and it matters more here: this
    list is now visible to anybody a connection was shared with, and an email
    is a piece of personal data that "who owns this data source" does not need.
    """
    if not ids:
        return {}
    rows = await db.execute(
        select(User.id, User.display_name, User.email).where(User.id.in_(ids))
    )
    return {row[0]: (row[1] or row[2].split("@")[0]) for row in rows.all()}


@router.post("", response_model=ConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: ConnectionCreate, ctx: CtxDep, db: DbDep, box: SecretBoxDep
) -> DatabaseConnection:
    existing = await db.execute(
        select(DatabaseConnection).where(
            # Asks about the row that is about to be *written*, whose owner
            # is the caller by construction — not about anything they can
            # reach. That is why it is an exemption rather than a miss.
            DatabaseConnection.owner_id == ctx.user_id,  # authz-ok: unique (owner, name)
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
        clarify_enabled=payload.clarify_enabled,
        include_db_comments=payload.include_db_comments,
    )
    db.add(connection)
    await db.flush()
    return connection


@router.post("/test", response_model=ConnectionTestResult)
async def test_draft_connection(
    payload: ConnectionTestRequest,
    ctx: CtxDep,
    db: DbDep,
    box: SecretBoxDep,
    authz: AuthzDep,
) -> ConnectionTestResult:
    """Probe credentials straight from the form, saved or not.

    Declared above `/{connection_id}` so the literal path wins the match.
    Nothing is written: the form may hold unsaved edits that differ from the
    row, so a probe here never records status against a row — only
    `/{id}/test`, which tests the stored values, may do that.

    When `connection_id` is given and no new password was typed, the stored
    password is reused so an edit can be tested without re-entering the secret;
    every other value comes from the form.
    """
    if payload.password is not None:
        password = payload.password.get_secret_value()
    elif payload.connection_id is not None:
        connection = await _authorized(
            db, authz, payload.connection_id, ctx, Privilege.MODIFY
        )
        password = box.decrypt(
            connection.encrypted_password, aad=f"connection:{connection.id}"
        )
    else:
        raise ValidationError("A password is required to test a connection.")

    connector = build_connector(
        kind=payload.database_type,
        host=payload.host,
        port=payload.port,
        database=payload.database_name,
        username=payload.username,
        password=password,
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
async def get_connection(
    connection_id: UUID, ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> ConnectionRead:
    """The connection — narrowed to what this principal may know about it.

    `describe` gets in, and that is the change Phase 6 makes: a Data Engineer,
    an Auditor, or somebody a dashboard was shared with can now confirm the
    connection exists, see its engine and **see its disclosure policy** — which
    §19.5 rule 1 requires, because a grantee has to be able to see what leaves
    before they ask. They do not see the host, the port, the database name or
    the username; those four together are enough to attempt a connection from
    anywhere the database is reachable.
    """
    connection = await _authorized(
        db, authz, connection_id, ctx, Privilege.DESCRIBE
    )
    held = await authz.privileges_on(
        ctx, ResourceRef.to(ResourceType.CONNECTION, connection)
    )
    read = ConnectionRead.model_validate(connection).model_copy(
        update={"privileges": sorted(str(p) for p in held)}
    )
    return read if Privilege.SELECT in held else narrow_to_describe(read)


@router.patch("/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: UUID, payload: ConnectionUpdate,
    ctx: CtxDep, db: DbDep, box: SecretBoxDep, authz: AuthzDep,
) -> DatabaseConnection:
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
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

    await db.flush()
    return connection


@router.put("/{connection_id}/disclosure", response_model=ConnectionRead)
async def set_disclosure_policy(
    connection_id: UUID, payload: DisclosureWrite,
    ctx: CtxDep, db: DbDep, authz: AuthzDep,
) -> ConnectionRead:
    """Change how much of a result may leave for a model provider. **`manage`.**

    Its own endpoint, gated one privilege above every other edit, and audited
    under its own action — and the reason is the whole of §19.5. The moment a
    connection is shared, **one person's disclosure choice governs another
    person's questions**, and that person may not know what it is. Widening
    `NONE` → `FULL` is not an edit to a row; it is a decision about what leaves
    the customer's database, taken on behalf of everybody who can now ask
    through this connection.

    So `modify` — which can re-credential the connection and re-sync its schema
    — cannot touch it, and `manage` — which is also what lets somebody share it
    in the first place — can. The two decisions belong to the same person
    because they are the same decision seen twice.

    Narrowing is audited exactly as widening is. A policy that quietly tightened
    would break somebody's report, and *"who changed this and when"* is the
    question that gets asked either way.
    """
    connection = await _authorized(
        db, authz, connection_id, ctx, Privilege.MANAGE
    )
    previous = connection.disclosure_policy
    if payload.disclosure_policy != previous:
        connection.disclosure_policy = payload.disclosure_policy
        await db.flush()
        await audit.record(
            db, ctx,
            action=DISCLOSURE_CHANGED,
            resource_type=audit.CONNECTION, resource_id=connection.id,
            detail={"from": previous, "to": payload.disclosure_policy},
        )
    held = await authz.privileges_on(
        ctx, ResourceRef.to(ResourceType.CONNECTION, connection)
    )
    return ConnectionRead.model_validate(connection).model_copy(
        update={"privileges": sorted(str(p) for p in held)}
    )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID, ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> None:
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.DELETE)
    # The delete hook. `grants.resource_id` carries no foreign key — it is
    # polymorphic across eight types — so nothing removes these for us, and a
    # left-behind grant would name an id no row has. Inert, but it would show
    # up in an access review as reach nobody can explain. All three types go,
    # because the derived two share this connection's id.
    service = GrantService(db, authz)
    for type_ in (
        ResourceType.CONNECTION, ResourceType.KNOWLEDGE, ResourceType.SEMANTIC_LAYER,
    ):
        await service.revoke_all_for(type_, connection.id)
    await db.delete(connection)
    # Flush inside the request, exactly as `update_connection` does. `get_db`
    # commits *after* the handler returns, by which point the 204 has been
    # written — so a constraint that refuses the delete would otherwise be
    # reported to the user as success and to the log as a stack trace. This is
    # not hypothetical: `runs` blocked every connection that had ever answered a
    # question until migration 0014.
    await db.flush()


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: UUID, ctx: CtxDep, db: DbDep, box: SecretBoxDep, authz: AuthzDep
) -> ConnectionTestResult:
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
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
    connection_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    box: SecretBoxDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> SchemaRead:
    """Introspect and store a new snapshot version.

    Foreign keys are recorded from day one even though the graph view is a
    later release; backfilling them would mean re-syncing every connection.
    """
    connection = await _authorized(db, authz, connection_id, ctx, Privilege.MODIFY)
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
            schema_allowlist=connection.schema_allowlist,
            # Column content hints are customer data, so what may be *captured*
            # is capped by the same policy that caps what may be disclosed.
            # A NONE connection stores structure only — tightening the policy
            # takes effect at once, loosening it needs a re-sync.
            hints=HintBudget.from_policy(connection.disclosure_policy),
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

    return _to_schema_read(row)


@router.get("/{connection_id}/schema", response_model=SchemaRead)
async def get_schema(
    connection_id: UUID, ctx: CtxDep, db: DbDep, authz: AuthzDep
) -> SchemaRead:
    await _authorized(db, authz, connection_id, ctx, Privilege.SELECT)
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
        catalog_meta=row.catalog_meta or {},
    )
