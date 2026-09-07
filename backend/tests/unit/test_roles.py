"""The eight seed roles, the four refusals, and the one query per request.

Phase 3 of `docs/user-management-and-access-control-plan.md`. What this file
pins down, in the order it would hurt if it broke:

* **The seed *is* the specification.** §12.3 of the plan says exactly what each
  of the eight roles carries; the table below restates it from that document
  rather than from the migration, because a test that read its expectation out
  of `SEED` would agree with any mistake in `SEED`. If the two disagree, one of
  them is wrong and the diff says which line.
* **Four refusals.** A system role will not have its capabilities edited; a
  role somebody holds will not be deleted, and the refusal *names them*; the
  last administrator cannot be demoted through either route into it.
* **Capabilities are resolved per request, in one query.** That is decision 15
  — not in the token — and it is what makes a revoked role take effect on the
  next request rather than at the next refresh. The query count is asserted
  because this runs on every authenticated call in the product.
* **Scoped privileges are stored and read by nothing.** Phase 3 ships the
  storage; the decision that reads it is Phase 6, and a test that proves the
  gap is the difference between "not yet wired" and "quietly half-wired".
"""
from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session

from app.core.context import RequestContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.value_objects import Role as LegacyRole
from app.domain.value_objects.authz import (
    ADMINISTRATOR,
    Capability,
    Privilege,
    ResourceType,
)
from app.infra.db.models import (
    AuditLog,
    Base,
    Role,
    RoleAssignment,
    RoleCapability,
    RoleScopedPrivilege,
    User,
)
from app.services.role_service import RoleService, assign_by_name, known_capabilities

if "alembic" not in sys.modules:  # a revision module imports `alembic.op` only
    try:
        import alembic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("alembic")
        stub.op = None  # type: ignore[attr-defined]
        sys.modules["alembic"] = stub

MIGRATION = importlib.import_module("app.infra.db.migrations.versions.0024_roles")


# ── the specification, restated ──────────────────────────────────────────
#: §12.3 of the plan, transcribed. Deliberately **not** imported from the
#: migration: this is the independent second opinion, and its whole value is
#: that somebody had to read the table and type it out.
SPEC: dict[str, tuple[set[str], set[tuple[str, str]]]] = {
    "Administrator": (
        {
            "user.read", "user.manage", "service_user.manage",
            "team.read", "team.manage", "role.read", "role.manage",
            "audit.read", "access.review",
            "connection.create", "llm_config.create", "dashboard.create",
            "report.create", "conversation.create",
            "settings.manage", "benchmark.manage", "eval.run",
            "system.maintenance",
        },
        set(),
    ),
    "Normal User": (
        {"dashboard.create", "report.create", "conversation.create", "team.read"},
        set(),
    ),
    "Viewer": ({"team.read"}, set()),
    "Data Engineer": (
        {"connection.create", "conversation.create", "team.read"},
        {("semantic_layer", "manage"), ("connection", "describe")},
    ),
    "BI Engineer": (
        {"dashboard.create", "report.create", "conversation.create", "team.read"},
        {("dashboard", "describe"), ("report", "describe")},
    ),
    "Knowledge Manager": (
        {"conversation.create", "benchmark.manage", "team.read"},
        {("knowledge", "manage"), ("connection", "describe")},
    ),
    "DataMind Maintainer": (
        {
            "llm_config.create", "service_user.manage", "settings.manage",
            "eval.run", "system.maintenance", "benchmark.manage",
        },
        {("llm_config", "describe")},
    ),
    "Auditor": (
        {"user.read", "team.read", "role.read", "audit.read", "access.review"},
        {
            ("connection", "describe"), ("dashboard", "describe"),
            ("report", "describe"),
        },
    ),
}


def _seeded() -> dict[str, tuple[set[str], set[tuple[str, str]]]]:
    return {
        name: (set(capabilities), set(scoped))
        for name, _description, capabilities, scoped in MIGRATION.SEED
    }


def test_the_migration_seeds_exactly_the_eight_roles() -> None:
    assert set(_seeded()) == set(SPEC)


@pytest.mark.parametrize("name", sorted(SPEC))
def test_each_seed_role_carries_exactly_what_the_plan_says(name: str) -> None:
    """One case per role, so a failure names the role rather than the table."""
    capabilities, scoped = _seeded()[name]
    assert capabilities == SPEC[name][0]
    assert scoped == SPEC[name][1]


def test_the_knowledge_manager_costs_two_rows_and_edits_no_credential() -> None:
    """Requirement 2's own test case, stated as an assertion.

    *"Extensive permissions over Knowledge resources without Admin access to
    the entire application."* One capability row and one scoped-privilege row —
    and, crucially, **nothing on `connection` above `describe`**, so the holder
    can teach a database whose password they cannot read or change.
    """
    capabilities, scoped = _seeded()["Knowledge Manager"]

    assert ("knowledge", "manage") in scoped
    assert "user.manage" not in capabilities
    assert not any(
        resource == "connection" and privilege != "describe"
        for resource, privilege in scoped
    )


def test_administrator_enumerates_every_capability_rather_than_wildcarding() -> None:
    """A nineteenth capability must be a deliberate line in a reviewed diff,
    not something Administrator silently acquires."""
    capabilities, _scoped = _seeded()["Administrator"]
    assert capabilities == {str(c) for c in Capability}


def test_every_seeded_word_is_one_this_build_knows() -> None:
    """The column is open and the enum is closed — but the *seed* is written by
    this repository, so a typo in it is a role that silently carries nothing."""
    for name, (capabilities, scoped) in _seeded().items():
        assert known_capabilities(capabilities) == {
            Capability(c) for c in capabilities
        }, name
        for resource_type, privilege in scoped:
            assert ResourceType(resource_type)
            assert Privilege(privilege)


def test_no_seed_role_names_a_resource_id() -> None:
    """There is nowhere to put one, and this asserts the shape stays that way.

    A role that could name one resource would make an access review
    unanswerable: "because of the Knowledge Manager role" and "because Sara
    granted it on 3 March" have to stay different sentences.
    """
    for _name, _description, _capabilities, scoped in MIGRATION.SEED:
        assert all(len(pair) == 2 for pair in scoped)


# ── the migration agrees with `models.py` ────────────────────────────────
class OpRecorder:
    """Stands in for `alembic.op`: records the DDL instead of emitting it."""

    def __init__(self) -> None:
        self.tables: dict[str, list[Any]] = {}
        self.indexes: list[tuple[str, str, list[str]]] = []
        self.statements: list[str] = []

    def create_table(self, name: str, *columns: Any, **_kw: Any) -> None:
        self.tables[name] = list(columns)

    def create_index(self, name: str, table: str, columns: list[str], **_kw: Any) -> None:
        self.indexes.append((name, table, columns))

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))

    def drop_table(self, *_a: Any, **_kw: Any) -> None:  # pragma: no cover
        pass

    def drop_index(self, *_a: Any, **_kw: Any) -> None:  # pragma: no cover
        pass


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> OpRecorder:
    recorder = OpRecorder()
    monkeypatch.setattr(MIGRATION, "op", recorder)
    MIGRATION.upgrade()
    return recorder


def test_the_migration_and_the_orm_describe_the_same_four_tables(
    recorded: OpRecorder,
) -> None:
    """Two definitions of one schema, and only a running database usually
    notices when they drift."""
    expected = {
        "roles", "role_capabilities", "role_scoped_privileges", "role_assignments",
    }
    assert set(recorded.tables) == expected

    for name in expected:
        declared = {
            column.name
            for column in recorded.tables[name]
            if isinstance(column, sa.Column)
        }
        assert declared == set(Base.metadata.tables[name].columns.keys()), name


def test_the_seed_and_the_backfill_both_run(recorded: OpRecorder) -> None:
    """Eight roles, their rows, and one assignment per existing account.

    The backfill is in the same migration on purpose: an installation that
    landed with a `roles` table and nobody holding one would be an upgrade that
    signed every administrator out of their own administration screens.
    """
    inserts = [s for s in recorded.statements if "INSERT INTO roles" in s]
    assert len(inserts) == 8
    assert any("INSERT INTO role_assignments" in s for s in recorded.statements)


# ── a real database, behind the async surface the service uses ───────────
_TABLES = (
    "users", "roles", "role_capabilities", "role_scoped_privileges",
    "role_assignments", "audit_logs",
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
    """The six tables this file writes, from the real metadata.

    Four edits to a *copy* of the column definitions, all about Postgres
    syntax rather than anything under test: a `::jsonb` server default SQLite
    cannot parse, an `ARRAY` its driver refuses, `JSONB` itself, which has no
    SQLite compiler, and `audit_logs.id` — a `BigInteger` primary key, which
    SQLite will not auto-increment because only `INTEGER PRIMARY KEY` aliases
    the rowid. None appears in an assertion here; they are the price of running
    the real statements against a real engine rather than faking them.
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
    have. Reading `SEED` here is legitimate where the table test above would
    not be: this fixture is *arranging* a world, not asserting what is in it.
    """
    for name, description, capabilities, scoped in MIGRATION.SEED:
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
        password_hash="x", role=role, status="ACTIVE",
    )
    session.add(user)
    session.flush()
    return user


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


# ── resolution ───────────────────────────────────────────────────────────
async def test_capabilities_resolve_from_the_roles_a_principal_holds(
    db: AsyncSessionShim,
) -> None:
    session = db._session
    person = _user(session, "km@test.local")
    await assign_by_name(db, user_id=person.id, role_name="Knowledge Manager")
    session.flush()

    held = await RoleService(db).resolve_capabilities(person.id)

    assert held == {
        Capability.CONVERSATION_CREATE,
        Capability.BENCHMARK_MANAGE,
        Capability.TEAM_READ,
    }


async def test_two_roles_union_rather_than_the_stronger_one_winning(
    db: AsyncSessionShim,
) -> None:
    """Roles are not a hierarchy and not mutually exclusive — §12.4."""
    session = db._session
    person = _user(session, "both@test.local")
    await assign_by_name(db, user_id=person.id, role_name="Knowledge Manager")
    await assign_by_name(db, user_id=person.id, role_name="Auditor")
    session.flush()

    held = await RoleService(db).resolve_capabilities(person.id)

    assert Capability.BENCHMARK_MANAGE in held  # from Knowledge Manager
    assert Capability.AUDIT_READ in held  # from Auditor
    assert Capability.USER_MANAGE not in held  # from neither


async def test_capability_resolution_is_one_query(db: AsyncSessionShim) -> None:
    """It runs on **every** authenticated request. An N+1 here is an N+1 for
    the whole product, for the rest of this plan's life."""
    session = db._session
    person = _user(session, "many@test.local")
    for name in ("Knowledge Manager", "Auditor", "BI Engineer"):
        await assign_by_name(db, user_id=person.id, role_name=name)
    session.flush()
    db.statements.clear()

    await RoleService(db).resolve_capabilities(person.id)

    assert len(db.statements) == 1


async def test_a_capability_change_takes_effect_on_the_next_resolution(
    db: AsyncSessionShim,
) -> None:
    """Decision 15, as an assertion: capabilities come from the database on
    every request, never from the token — so revoking a role takes effect on
    the next request rather than at the next refresh."""
    session = db._session
    person = _user(session, "moving@test.local")
    service = RoleService(db)
    auditor = await service.by_name("Auditor")
    assert auditor is not None

    await service.assign(ctx(), user_id=person.id, role_id=auditor.id)
    assert Capability.AUDIT_READ in await service.resolve_capabilities(person.id)

    await service.unassign(ctx(), user_id=person.id, role_id=auditor.id)
    assert Capability.AUDIT_READ not in await service.resolve_capabilities(person.id)


async def test_a_capability_this_build_does_not_know_is_ignored_not_raised(
    db: AsyncSessionShim,
) -> None:
    """The open/closed bargain: a downgrade degrades to *fewer* permissions
    rather than to a 500 on sign-in."""
    session = db._session
    person = _user(session, "future@test.local")
    role = Role(id=uuid4(), name="From the future", is_system=False, description="")
    session.add(role)
    session.flush()
    session.add(RoleCapability(role_id=role.id, capability="audit.export"))
    session.add(RoleCapability(role_id=role.id, capability="audit.read"))
    session.add(RoleAssignment(id=uuid4(), role_id=role.id, user_id=person.id))
    session.flush()

    assert await RoleService(db).resolve_capabilities(person.id) == {
        Capability.AUDIT_READ
    }


# ── the four refusals ────────────────────────────────────────────────────
async def test_a_system_role_refuses_a_capability_edit(db: AsyncSessionShim) -> None:
    service = RoleService(db)
    auditor = await service.by_name("Auditor")
    assert auditor is not None

    with pytest.raises(ValidationError) as raised:
        await service.update(ctx(), auditor.id, capabilities=["user.manage"])

    # The refusal names the way out, because the person hitting it is an
    # administrator who is allowed to change roles.
    assert "custom role" in str(raised.value)


async def test_a_system_role_may_be_renamed(db: AsyncSessionShim) -> None:
    """"Normal User" is a bad name in some installations, and the seed is not
    a claim about anybody's vocabulary."""
    service = RoleService(db)
    role = await service.by_name("Normal User")
    assert role is not None

    await service.update(ctx(), role.id, name="Analyst", description="Ours.")

    assert (await service.get(role.id)).name == "Analyst"
    assert await service.by_name("Analyst") is not None


async def test_a_system_role_cannot_be_deleted(db: AsyncSessionShim) -> None:
    service = RoleService(db)
    viewer = await service.by_name("Viewer")
    assert viewer is not None

    with pytest.raises(ValidationError):
        await service.delete(ctx(), viewer.id)


async def test_deleting_an_assigned_role_is_refused_and_names_the_holders(
    db: AsyncSessionShim,
) -> None:
    """`ON DELETE RESTRICT` would raise on its own — as an `IntegrityError` the
    caller can only report as "something went wrong". The useful sentence names
    who still holds it."""
    session = db._session
    service = RoleService(db)
    role = await service.create(ctx(), name="Analyst", capabilities=["team.read"])
    person = _user(session, "holder@test.local")
    session.flush()
    await service.assign(ctx(), user_id=person.id, role_id=role.id)

    with pytest.raises(ConflictError) as raised:
        await service.delete(ctx(), role.id)

    assert "holder" in str(raised.value)


async def test_an_unheld_custom_role_deletes(db: AsyncSessionShim) -> None:
    service = RoleService(db)
    role = await service.create(ctx(), name="Temporary")

    await service.delete(ctx(), role.id)

    assert await service.by_name("Temporary") is None


# ── the last administrator ───────────────────────────────────────────────
async def test_the_last_administrator_cannot_be_unassigned(
    db: AsyncSessionShim,
) -> None:
    """Counted over **assignments**, not over `users.role`.

    That column is a cache now. An installation whose administrators were made
    through the roles screen would have a correct `role_assignments` table and
    a stale enum — and a guard reading the stale one would happily strand the
    workspace with nobody able to reach the administration screens.
    """
    session = db._session
    service = RoleService(db)
    admin_role = await service.by_name(ADMINISTRATOR)
    assert admin_role is not None
    only = _user(session, "only@test.local", LegacyRole.ADMIN)
    await service.assign(ctx(), user_id=only.id, role_id=admin_role.id)

    with pytest.raises(ValidationError) as raised:
        await service.unassign(ctx(), user_id=only.id, role_id=admin_role.id)

    assert "only administrator" in str(raised.value)


async def test_a_second_administrator_makes_the_first_removable(
    db: AsyncSessionShim,
) -> None:
    session = db._session
    service = RoleService(db)
    admin_role = await service.by_name(ADMINISTRATOR)
    assert admin_role is not None
    first = _user(session, "first@test.local", LegacyRole.ADMIN)
    second = _user(session, "second@test.local", LegacyRole.ADMIN)
    await service.assign(ctx(), user_id=first.id, role_id=admin_role.id)
    await service.assign(ctx(), user_id=second.id, role_id=admin_role.id)

    await service.unassign(ctx(), user_id=first.id, role_id=admin_role.id)

    assert await service.resolve_capabilities(first.id) == frozenset()


async def test_losing_a_different_role_is_never_the_last_administrator(
    db: AsyncSessionShim,
) -> None:
    """The guard asks about the assignment that is going, not about the person."""
    session = db._session
    service = RoleService(db)
    admin_role = await service.by_name(ADMINISTRATOR)
    auditor = await service.by_name("Auditor")
    assert admin_role is not None and auditor is not None
    only = _user(session, "solo@test.local", LegacyRole.ADMIN)
    await service.assign(ctx(), user_id=only.id, role_id=admin_role.id)
    await service.assign(ctx(), user_id=only.id, role_id=auditor.id)

    await service.unassign(ctx(), user_id=only.id, role_id=auditor.id)

    assert Capability.USER_MANAGE in await service.resolve_capabilities(only.id)


# ── the legacy column follows, and decides nothing ───────────────────────
async def test_users_role_is_kept_true_as_a_cache(db: AsyncSessionShim) -> None:
    """Nothing reads it to decide anything — `make authz-check` enforces that —
    but a deployment rolled back to a build without `roles` would otherwise
    find every account demoted to MEMBER."""
    session = db._session
    service = RoleService(db)
    admin_role = await service.by_name(ADMINISTRATOR)
    assert admin_role is not None
    person = _user(session, "promoted@test.local")

    await service.assign(ctx(), user_id=person.id, role_id=admin_role.id)
    assert person.role == LegacyRole.ADMIN

    other = _user(session, "keeps@test.local", LegacyRole.ADMIN)
    await service.assign(ctx(), user_id=other.id, role_id=admin_role.id)
    await service.unassign(ctx(), user_id=person.id, role_id=admin_role.id)
    assert person.role == LegacyRole.MEMBER


# ── assignment behaviour ─────────────────────────────────────────────────
async def test_assigning_a_role_twice_is_not_an_error(db: AsyncSessionShim) -> None:
    """Pressing a toggle that is already on is impatience, not a conflict — and
    the alternative makes every client implement check-then-set against a race.
    """
    session = db._session
    service = RoleService(db)
    role = await service.by_name("Viewer")
    assert role is not None
    person = _user(session, "twice@test.local")

    first = await service.assign(ctx(), user_id=person.id, role_id=role.id)
    again = await service.assign(ctx(), user_id=person.id, role_id=role.id)

    assert first.id == again.id


async def test_assigning_to_a_missing_person_is_a_404(db: AsyncSessionShim) -> None:
    service = RoleService(db)
    role = await service.by_name("Viewer")
    assert role is not None

    with pytest.raises(NotFoundError):
        await service.assign(ctx(), user_id=uuid4(), role_id=role.id)


async def test_a_created_role_comes_back_with_the_rows_it_was_given(
    db: AsyncSessionShim,
) -> None:
    """The write's own answer, not the empty object it started from.

    Child rows are added beside the parent rather than through its
    collections, so without the reload in `_with_rows` this returns a role
    that says it carries nothing — and the route renders that into the
    response somebody's editor is about to re-draw from.
    """
    role = await RoleService(db).create(
        ctx(),
        name="Analyst",
        capabilities=["team.read", "dashboard.create"],
        scoped_privileges=[("dashboard", "describe")],
    )

    assert {row.capability for row in role.capabilities} == {
        "team.read", "dashboard.create",
    }
    assert [
        (row.resource_type, row.privilege) for row in role.scoped_privileges
    ] == [("dashboard", "describe")]


async def test_an_updated_role_comes_back_as_it_now_is(db: AsyncSessionShim) -> None:
    """Not as it was before the edit — the other half of the same bug."""
    service = RoleService(db)
    role = await service.create(ctx(), name="Analyst", capabilities=["team.read"])

    updated = await service.update(
        ctx(), role.id, capabilities=["audit.read", "access.review"]
    )

    assert {row.capability for row in updated.capabilities} == {
        "audit.read", "access.review",
    }


async def test_a_duplicate_role_name_is_refused(db: AsyncSessionShim) -> None:
    service = RoleService(db)

    with pytest.raises(ConflictError):
        await service.create(ctx(), name="Auditor")


# ── the audit half ───────────────────────────────────────────────────────
async def test_every_role_change_leaves_a_row(db: AsyncSessionShim) -> None:
    """Who granted what, to whom, and when. A permission model whose changes
    are unrecorded cannot answer the question an access review exists for."""
    session = db._session
    service = RoleService(db)
    person = _user(session, "audited@test.local")
    role = await service.create(ctx(), name="Analyst", capabilities=["team.read"])
    await service.assign(ctx(), user_id=person.id, role_id=role.id)
    await service.unassign(ctx(), user_id=person.id, role_id=role.id)
    await service.update(ctx(), role.id, description="Changed.")
    await service.delete(ctx(), role.id)
    session.flush()

    actions = [row.action for row in session.execute(sa.select(AuditLog)).scalars()]

    assert actions == [
        "role.created", "role.assigned", "role.unassigned",
        "role.updated", "role.deleted",
    ]


# ── what Phase 3 deliberately does not do ────────────────────────────────
def test_no_decision_reads_a_scoped_privilege_yet() -> None:
    """Phase 3 ships the *storage*; Phase 6 ships the decision that reads it.

    A grep rather than a behavioural test, because the claim is about absence:
    the only modules allowed to mention `role_scoped_privileges` today are the
    ones that store and render it. `OwnerOnlyAuthorizer` in particular must
    still answer from ownership alone, or Phase 3 would have quietly changed
    who can reach what.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    readers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "RoleScopedPrivilege" in path.read_text()
    }

    assert readers == {
        "infra/db/models.py",          # declares it
        "services/role_service.py",    # writes it
    }
