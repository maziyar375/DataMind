"""The world the permission-model tests run in: a real schema, strictly.

`role_service` and `team_service` build statements — joins across four tables,
a union with two arms, a grouped count — and a hand-written fake that answered
queries by inspecting them would prove nothing about the statements themselves,
which are the thing under test. So these fixtures run the **real SQL** against
an in-memory SQLite database, behind the async surface the services call.

The session is also made **as strict as an asyncio one**: it refuses a lazy
load. That is not fussiness. A synchronous session lazy-loads silently and
correctly where an `AsyncSession` raises `MissingGreenlet`, and exactly that
difference let `POST /roles` ship as a 500 with every test in the suite green.
"""
from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.domain.value_objects import Role as LegacyRole
from app.domain.value_objects.authz import Capability
from app.infra.db.models import (
    Base,
    DatabaseConnection,
    Grant,
    Role,
    RoleCapability,
    RoleScopedPrivilege,
    User,
)

if "alembic" not in sys.modules:  # a revision module imports `alembic.op` only
    try:
        import alembic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("alembic")
        stub.op = None  # type: ignore[attr-defined]
        sys.modules["alembic"] = stub

ROLES_MIGRATION = importlib.import_module(
    "app.infra.db.migrations.versions.0024_roles"
)


class _UtcDateTime(sqlite.DATETIME):
    """`timestamptz`, for a database that has no such thing.

    Postgres stores an offset and gives it back; SQLite stores a string and
    returns a **naive** datetime, so a value written as `utcnow()` reads back
    as something `utcnow()` cannot be compared against at all — a `TypeError`,
    in a code path whose production behaviour is fine. That is a difference
    between the test database and the real one, which is exactly what the
    Postgres-syntax edits in `engine` already exist to erase.

    Installed on the **dialect** rather than on the copied metadata, and that
    is the whole subtlety: the ORM classes are mapped against
    `Base.metadata`, not against the copy `create_all` runs, so a type swapped
    on the copy would fix the `CREATE TABLE` and change nothing about what a
    query returns. `colspecs` maps a generic type to its dialect
    implementation, so this reaches every `DateTime` the mapping uses without
    mutating the shared metadata other test modules import.
    """

    def bind_processor(self, dialect: Any) -> Any:
        inner = super().bind_processor(dialect)

        def process(value: Any) -> Any:
            if value is not None and value.tzinfo is not None:
                value = value.astimezone(UTC).replace(tzinfo=None)
            return inner(value) if inner else value

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        inner = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            resolved = inner(value) if inner else value
            if resolved is not None and resolved.tzinfo is None:
                resolved = resolved.replace(tzinfo=UTC)
            return resolved

        return process


# ── a real database, behind the async surface the service uses ───────────
_TABLES = (
    "users", "teams", "team_members", "roles", "role_capabilities",
    "role_scoped_privileges", "role_assignments", "service_credentials",
    "grants", "audit_logs",
    # The owned tables. Present from Phase 6 because `RbacAuthorizer` reads
    # `owner_id` off them, `visible` unions against them, `owned_resources`
    # counts them for the deletion refusal, and the orphaned-grant sweep
    # deletes against every one — so a fixture holding only the identity
    # tables could not exercise any of it.
    "database_connections", "llm_configs", "dashboards", "reports",
    "conversations",
    # Phase 8: the intersection rule is a property of a **tile**, and the
    # tripwire on the cache is a property of the cache row. Both need the real
    # tables — a fake tile with a `connection_id` attribute would pass the
    # check being tested without any of the composition that makes it correct.
    "dashboard_tiles", "dashboard_tile_cache",
    # And the chat half of the same rule: a turn in a shared thread keeps its
    # prose and loses its results, which is a property of a `runs` row.
    # `messages` comes with it — `runs.user_message_id` is a foreign key, and
    # a metadata copy missing the target table cannot be created at all.
    "messages", "runs", "run_steps",
)


def _refuse_lazy_loads(session: Session) -> None:
    """Make this synchronous session as strict as an asyncio one.

    Added after a bug that shipped past every test in this file: `create` and
    `update` returned a role whose `capabilities` collection had been populated
    while it was empty, and the child rows were written beside it rather than
    through it. Reading the collection then **lazy-loads** — which a
    synchronous session does silently and correctly, and an `AsyncSession`
    cannot do at all. So `POST /roles` was a 500 in production and green here.

    A lazy load is legitimate in plenty of code; it is never legitimate in
    anything that runs under `AsyncSession`, which is everything in `app/`. So
    the session used by these tests refuses one, and the failure names the
    attribute rather than appearing later as a `MissingGreenlet` in a log.
    """

    @sa.event.listens_for(session, "do_orm_execute")
    def _guard(state: Any) -> None:
        # `lazy_loaded_from` is only meaningful for a SELECT; the teardown's
        # bulk DELETEs reach this hook too and raise if it is asked.
        if state.is_select and state.lazy_loaded_from is not None:
            raise AssertionError(
                "a lazy load happened here, which would be a MissingGreenlet "
                "under AsyncSession. Load the relationship eagerly — see "
                "RoleService.get."
            )


class AsyncSessionShim:
    """A synchronous `Session` wearing the async surface `RoleService` calls.

    The alternative was a hand-written fake that answers queries by inspecting
    statements, and it would prove nothing about the statements themselves —
    the joins in `resolve_capabilities` are the thing under test. This runs the
    real SQL against SQLite and counts it.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self.statements: list[Any] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(statement)
        return self._session.execute(statement, *args, **kwargs)

    async def get(self, model: type, primary_key: Any) -> Any:
        return self._session.get(model, primary_key)

    def add(self, obj: Any) -> None:
        self._session.add(obj)

    async def flush(self) -> None:
        self._session.flush()

    async def delete(self, obj: Any) -> None:
        self._session.delete(obj)


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    """The tables these files write, from the real metadata.

    Five edits, all about Postgres syntax rather than anything under test: a
    `::jsonb` server default SQLite cannot parse, an `ARRAY` its driver
    refuses, `JSONB` itself, which has no SQLite compiler, `audit_logs.id` — a
    `BigInteger` primary key, which SQLite will not auto-increment because only
    `INTEGER PRIMARY KEY` aliases the rowid — and every `timestamptz`, which
    SQLite hands back naive. The first four are made on a *copy* of the column
    definitions; the fifth is made on the dialect, for the reason
    `_UtcDateTime` gives. None appears in an assertion here; they are the price
    of running the real statements against a real engine rather than faking
    them.
    """
    metadata = sa.MetaData()
    for name in _TABLES:
        table = Base.metadata.tables[name].to_metadata(metadata)
        for column in table.columns:
            if "::" in str(getattr(column.server_default, "arg", "")):
                column.server_default = None
            if isinstance(column.type, ARRAY | JSONB):
                column.type = sa.JSON()
            if column.primary_key and isinstance(column.type, sa.BigInteger):
                column.type = sa.Integer()

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    # The two edits that have to happen on the **dialect** rather than on the
    # copy, for the reason `_UtcDateTime` gives: the ORM classes are mapped
    # against `Base.metadata`, not against the copy `create_all` runs, so a
    # type swapped on the copy fixes the `CREATE TABLE` and changes nothing
    # about what a query binds or returns.
    #
    # `ARRAY` is the second. `database_connections.schema_allowlist` is a
    # Postgres array, and SQLite's driver refuses to bind a list — so the
    # column reaches `CREATE TABLE` as JSON (below) and every insert through
    # the ORM still fails, with an error about parameter 12 that names nothing.
    engine.dialect.colspecs = {
        **engine.dialect.colspecs,
        sa.DateTime: _UtcDateTime,
        sa.ARRAY: sa.JSON,
    }

    @sa.event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    return engine


#: The administrator every test in this file acts as. A real row, because
#: `role_assignments.created_by` and `audit_logs.actor_user_id` are foreign
#: keys and SQLite is asked to enforce them — a fixture that acted as a made-up
#: id would be testing against a schema looser than the real one.
ACTOR = uuid4()


@pytest.fixture
def db(engine: sa.Engine) -> Iterator[AsyncSessionShim]:
    with Session(engine) as session:
        _refuse_lazy_loads(session)
        shim = AsyncSessionShim(session)
        session.add(
            User(
                id=ACTOR, email="actor@test.local", display_name="Actor",
                password_hash="x", role=LegacyRole.ADMIN, status="ACTIVE",
                kind="HUMAN",
            )
        )
        _seed_roles(session)
        yield shim
        session.rollback()
        for name in reversed(_TABLES):
            session.execute(sa.text(f"DELETE FROM {name}"))  # noqa: S608
        session.commit()


def _seed_roles(session: Session) -> None:
    """The migration's seed, applied through the ORM.

    The revision's own SQL uses `gen_random_uuid()`, which SQLite does not
    have. Reading `SEED` here is legitimate where a table test would not be:
    this fixture *arranges* a world, it does not assert what is in it.
    """
    for name, description, capabilities, scoped in ROLES_MIGRATION.SEED:
        role = Role(id=uuid4(), name=name, description=description, is_system=True)
        session.add(role)
        session.flush()
        for capability in capabilities:
            session.add(RoleCapability(role_id=role.id, capability=capability))
        for resource_type, privilege in scoped:
            session.add(
                RoleScopedPrivilege(
                    role_id=role.id, resource_type=resource_type, privilege=privilege
                )
            )
    session.flush()


def _user(session: Session, email: str, role: str = LegacyRole.MEMBER) -> User:
    user = User(
        id=uuid4(), email=email, display_name=email.split("@")[0],
        password_hash="x", role=role, status="ACTIVE", kind="HUMAN",
    )
    session.add(user)
    session.flush()
    return user


def _connection(
    session: Session, *, owner_id: UUID | None, name: str | None = None
) -> DatabaseConnection:
    """A real `database_connections` row, for the tests that need reach.

    A real row rather than a stub with an `owner_id` attribute, because from
    Phase 6 the authorizer does more than read that attribute: `visible`
    unions against this table, the orphaned-grant sweep deletes against it, and
    the deletion refusal counts it. A stub would pass the ownership arm and
    prove nothing about the other four facts.

    `owner_id=None` is a legitimate state and one of the tests: a resource
    nobody owns is reachable by nobody, which is the fail-closed reading.
    """
    row = DatabaseConnection(
        id=uuid4(),
        owner_id=owner_id,
        name=name or f"conn-{uuid4().hex[:8]}",
        database_type="postgres",
        host="db.internal",
        port=5432,
        database_name="warehouse",
        username="reader",
        encrypted_password="not-a-real-ciphertext",
        key_version=1,
        schema_allowlist=["public"],
    )
    session.add(row)
    session.flush()
    return row


def _team_grant(
    session: Session, *, type_: str, resource_id: UUID | None,
    privilege: str, user: UUID | None = None, team: UUID | None = None,
) -> Grant:
    """One `grants` row, written directly. For arranging, never for asserting.

    The service is the thing under test in the files that check *how* a grant
    is made; the files that check what a grant *does* need one to exist and
    should not have to satisfy `manage` first to get one.
    """
    row = Grant(
        id=uuid4(),
        resource_type=type_,
        resource_id=resource_id,
        user_id=user,
        team_id=team,
        privilege=privilege,
        created_by=ACTOR,
    )
    session.add(row)
    session.flush()
    return row


def ctx(user_id: UUID = ACTOR) -> RequestContext:
    """The administrator doing the administering, as `get_ctx` would build it:
    from a **capability set**, with the legacy role string along for the ride
    and deciding nothing."""
    return RequestContext(
        user_id=user_id,
        email="actor@test.local",
        role=LegacyRole.ADMIN,
        capabilities=frozenset({Capability.ROLE_MANAGE, Capability.USER_MANAGE}),
    )


