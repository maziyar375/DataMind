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
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

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
)
from app.services.role_service import RoleService, assign_by_name, known_capabilities

# `db`, `ctx` and `_user` come from `tests/unit/conftest.py`.
from tests.unit.conftest import AsyncSessionShim, _user, ctx

if "alembic" not in sys.modules:  # a revision module imports `alembic.op` only
    try:
        import alembic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("alembic")
        stub.op = None  # type: ignore[attr-defined]
        sys.modules["alembic"] = stub

MIGRATION = importlib.import_module("app.infra.db.migrations.versions.0024_roles")
TEAMS_MIGRATION = importlib.import_module(
    "app.infra.db.migrations.versions.0025_teams"
)


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
    """Stands in for `alembic.op`: records the DDL instead of emitting it.

    It records enough of the two revisions to compare their *result* with
    `models.py` — including `0025`'s `add_column`, which is how
    `role_assignments` gains the column `0024` could not create.
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[Any]] = {}
        self.indexes: list[tuple[str, str, list[str]]] = []
        self.statements: list[str] = []
        self.checks: list[tuple[str, str, str]] = []
        self.nullable: dict[tuple[str, str], bool] = {}

    def create_table(self, name: str, *columns: Any, **_kw: Any) -> None:
        self.tables[name] = list(columns)

    def add_column(self, table: str, column: Any, **_kw: Any) -> None:
        self.tables.setdefault(table, []).append(column)

    def alter_column(self, table: str, column: str, **kw: Any) -> None:
        if "nullable" in kw:
            self.nullable[(table, column)] = kw["nullable"]

    def create_index(self, name: str, table: str, columns: list[str], **_kw: Any) -> None:
        self.indexes.append((name, table, columns))

    def create_foreign_key(self, *_a: Any, **_kw: Any) -> None:
        pass

    def create_check_constraint(
        self, name: str, table: str, condition: str, **_kw: Any
    ) -> None:
        self.checks.append((name, table, condition))

    def drop_constraint(self, *_a: Any, **_kw: Any) -> None:
        pass

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))

    def drop_table(self, *_a: Any, **_kw: Any) -> None:  # pragma: no cover
        pass

    def drop_index(self, *_a: Any, **_kw: Any) -> None:  # pragma: no cover
        pass


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> OpRecorder:
    """**Both** revisions, replayed onto one recorder.

    `0024` and `0025` are one schema arriving in two instalments — roles
    shipped a phase before teams existed, so `role_assignments.team_id` cannot
    be created until the second. Comparing either half alone against
    `models.py` would fail for a reason that is not a defect; comparing the
    pair is the claim worth making.
    """
    recorder = OpRecorder()
    monkeypatch.setattr(MIGRATION, "op", recorder)
    monkeypatch.setattr(TEAMS_MIGRATION, "op", recorder)
    MIGRATION.upgrade()
    TEAMS_MIGRATION.upgrade()
    return recorder


def test_the_migrations_and_the_orm_describe_the_same_six_tables(
    recorded: OpRecorder,
) -> None:
    """Two definitions of one schema, and only a running database usually
    notices when they drift."""
    expected = {
        "roles", "role_capabilities", "role_scoped_privileges", "role_assignments",
        "teams", "team_members",
    }
    assert set(recorded.tables) == expected

    for name in expected:
        declared = {
            column.name
            for column in recorded.tables[name]
            if isinstance(column, sa.Column)
        }
        assert declared == set(Base.metadata.tables[name].columns.keys()), name


def test_the_widening_makes_role_assignments_take_a_team(
    recorded: OpRecorder,
) -> None:
    """The half `0024` could not ship, asserted where both halves are in view.

    A column with a foreign key to a table that does not exist yet is not a
    column — so `role_assignments` arrives non-null on `user_id` and gains the
    rest here: the team column, the nullability, and the `CHECK` that makes
    "exactly one principal" a constraint rather than a convention.
    """
    assert recorded.nullable == {("role_assignments", "user_id"): True}
    assert ("ck_role_assignment_one_principal", "role_assignments",
            "(user_id IS NULL) <> (team_id IS NULL)") in recorded.checks
    # `NULLS NOT DISTINCT` is raw SQL because SQLAlchemy's `UniqueConstraint`
    # cannot express it in an `ALTER`. Without it Postgres treats every row
    # with a NULL in the key as unique and the table accepts a duplicate team
    # assignment — which is the whole reason the clause is there.
    assert any(
        "UNIQUE NULLS NOT DISTINCT" in statement for statement in recorded.statements
    )


def test_the_seed_and_the_backfill_both_run(recorded: OpRecorder) -> None:
    """Eight roles, their rows, and one assignment per existing account.

    The backfill is in the same migration on purpose: an installation that
    landed with a `roles` table and nobody holding one would be an upgrade that
    signed every administrator out of their own administration screens.
    """
    inserts = [s for s in recorded.statements if "INSERT INTO roles" in s]
    assert len(inserts) == 8
    assert any("INSERT INTO role_assignments" in s for s in recorded.statements)


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
def test_exactly_one_authorizer_reads_a_scoped_privilege() -> None:
    """Phase 3 shipped the *storage*; **Phase 6 shipped the decision** reading it.

    Rewritten rather than deleted, and it makes the same claim one phase on: a
    role's wildcard reach is consulted in exactly one place. A second module
    that started reading `role_scoped_privileges` to decide something would be
    a second permission model, and this grep is what notices.

    `OwnerOnlyAuthorizer` must still answer from ownership alone — it is the
    rollback, and a rollback that quietly kept honouring role wildcards would
    not be one.

    **Phase 9 adds a reader that does not decide.** `access_review_service`
    reads the same table to *report* — "Reza has modify on every connection,
    through the BI Engineer role" — and reporting is the opposite failure mode
    from deciding: a review that computed reach a second way would eventually
    disagree with the authorizer, and the one on the screen is the one people
    would trust. So it is listed here with its reason rather than exempted,
    and the claim narrows to the one that matters: exactly one module reads
    this table **to answer `allowed`**.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    readers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "RoleScopedPrivilege" in path.read_text()
    }

    assert readers == {
        "infra/db/models.py",                    # declares it
        "services/role_service.py",              # writes it
        "infra/authz/rbac.py",                   # Phase 6: the one that decides
        "services/access_review_service.py",     # Phase 9: reports, never decides
    }
    # And the reporter really does only report: it never asks or answers the
    # authorization question, it renders rows.
    review = (root / "services" / "access_review_service.py").read_text()
    for decider in ("allowed(", "def can", "satisfying("):
        assert decider not in review, (
            f"{decider!r} in the access review — reach is computed in the "
            "authorizer, and a second implementation is a second answer."
        )
    assert "RoleScopedPrivilege" not in (
        root / "infra" / "authz" / "owner_only.py"
    ).read_text()
