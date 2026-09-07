"""Who may curate, whose queue a flag lands in, and the record of it.

Three claims, and each one is a thing that quietly rots if it is not asserted:

* **Curation is a privilege that can be granted, and it is `(knowledge, modify)`.**
  This section was seven tests about `can_curate` — a function and a settings
  flag that approximated a reader/curator split because, before Phase 6, there
  was no way to *grant* one. They are **rewritten rather than deleted**, and
  they assert the same rule they always did: somebody who may ask questions
  through a connection may not rewrite what it has been taught. What changed is
  that the rule is now a row somebody can see, revoke and review, rather than a
  boolean in `.env`.
* **A flag is routed to the connection's owner**, and the server says whose
  queue it went to rather than the SPA guessing.
* **Every curation write leaves a row**, and that row carries identifiers and
  counts — never SQL, never question text. An audit log that became a second
  copy of the store would be a second thing to secure and the one place
  somebody forgets to.

The failure this phase exists to prevent is Microsoft's, documented: a store of
business logic whose provenance nobody can establish.
"""
from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.context import RequestContext
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import Capability, Privilege, ResourceType
from app.infra.authz.rbac import RbacAuthorizer
from app.services import audit
from app.services.grant_service import GrantService
from tests.unit.conftest import AsyncSessionShim, _connection, _user
from tests.unit.conftest import ctx as admin_ctx

OWNER = uuid4()
STRANGER = uuid4()


def ctx(user_id=OWNER, role: str = "MEMBER") -> RequestContext:
    """A context whose capabilities match the role it names.

    `role` is the legacy enum and, as of Phase 3, decides nothing. The helper
    keeps the old argument so the tests below still read as sentences about
    administrators and members, and derives the capability set from it, which
    is exactly what `get_ctx` does against the database.
    """
    return RequestContext(
        user_id=user_id,
        email="u@test.local",
        role=role,
        capabilities=(
            frozenset({Capability.USER_MANAGE}) if role == "ADMIN" else frozenset()
        ),
        correlation_id="t",
    )


def _knowledge(connection) -> ResourceRef:
    """The store's ref: the **connection's** id, under the derived type.

    That pairing is the whole of requirement 2. A grant on `knowledge` and a
    grant on `connection` name the same uuid and mean entirely different
    things, which is what lets somebody curate a database they cannot read.
    """
    return ResourceRef(
        type=ResourceType.KNOWLEDGE, id=connection.id, entity=connection
    )


# ── who may curate: the same seven claims, through grants ────────────────
async def test_the_owner_may_curate_their_own_connection(
    db: AsyncSessionShim,
) -> None:
    """Ownership confers the whole lattice, so it confers curation.

    The half that made the old flag correct rather than merely done, and it
    survives unchanged: without it, a rule that named only administrators would
    have meant the person who owns a connection cannot curate their own store.
    """
    owner = _user(db._session, "owner@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    assert await RbacAuthorizer(db).allowed(
        ctx(owner.id), _knowledge(connection), Privilege.MODIFY
    )


async def test_a_stranger_may_not(db: AsyncSessionShim) -> None:
    """The claim the old flag could only approximate, now literal."""
    owner = _user(db._session, "owner@test.local")
    stranger = _user(db._session, "stranger@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    assert not await RbacAuthorizer(db).allowed(
        ctx(stranger.id), _knowledge(connection), Privilege.MODIFY
    )


async def test_a_reader_may_ask_and_may_not_curate(db: AsyncSessionShim) -> None:
    """**The sentence the whole flag existed to approximate**, now a row.

    A grant of `(connection, select)` lets somebody ask questions through this
    database. It does not let them rewrite what it has been taught, because
    that is a different resource type — and *that* is the split the old
    `curation_admin_only` was reaching for with a boolean that could only say
    "administrators and owners".
    """
    owner = _user(db._session, "owner@test.local")
    reader = _user(db._session, "reader@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    authz = RbacAuthorizer(db)

    await GrantService(db, authz).grant(
        ctx(owner.id),
        ResourceRef.to(ResourceType.CONNECTION, connection),
        privilege=Privilege.SELECT,
        user_id=reader.id,
    )

    reader_ctx = ctx(reader.id)
    assert await authz.allowed(
        reader_ctx,
        ResourceRef.to(ResourceType.CONNECTION, connection),
        Privilege.SELECT,
    )
    assert not await authz.allowed(
        reader_ctx, _knowledge(connection), Privilege.MODIFY
    )


async def test_curation_can_now_be_granted_on_its_own(
    db: AsyncSessionShim,
) -> None:
    """The other direction, which the flag could not express **at all**.

    `(knowledge, modify)` on one connection: this person may teach it, and
    holds nothing on the connection itself. No boolean in a config file could
    say that, which is why the flag went rather than being kept beside the
    grant.
    """
    owner = _user(db._session, "owner@test.local")
    curator = _user(db._session, "curator@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    authz = RbacAuthorizer(db)

    await GrantService(db, authz).grant(
        ctx(owner.id),
        _knowledge(connection),
        privilege=Privilege.MODIFY,
        user_id=curator.id,
    )

    curator_ctx = ctx(curator.id)
    assert await authz.allowed(
        curator_ctx, _knowledge(connection), Privilege.MODIFY
    )
    assert not await authz.allowed(
        curator_ctx,
        ResourceRef.to(ResourceType.CONNECTION, connection),
        Privilege.SELECT,
    )


async def test_an_administrator_does_not_silently_curate(
    db: AsyncSessionShim,
) -> None:
    """**The one answer that changed, and it changed on purpose.**

    The old rule let an administrator curate anybody's store by virtue of
    holding `user.manage`. That is a silent read path — no row, nothing in the
    log, nothing an access review would show — and decision 14 of the plan
    replaces every one of them with an explicit, audited self-grant.

    Administering *people* and reaching somebody's *data* are different powers,
    and the whole reason the answer is computed in one place is that the one
    place can be read.
    """
    owner = _user(db._session, "owner@test.local")
    admin = _user(db._session, "admin@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    assert not await RbacAuthorizer(db).allowed(
        ctx(admin.id, role="ADMIN"), _knowledge(connection), Privilege.MODIFY
    )


async def test_a_knowledge_manager_curates_every_store_and_reads_none(
    db: AsyncSessionShim,
) -> None:
    """Requirement 2's acceptance test, and it is a **named** test.

    > *"extensive permissions over Knowledge resources without Admin access to
    > the whole application"*

    The Knowledge Manager role carries `(knowledge, manage)` and
    `(connection, describe)`. So this principal may teach `conn-A`, and may
    **not** read its data, edit its credentials, delete it, or widen its
    disclosure policy — on a connection they have never been granted anything
    on individually.
    """
    from app.services.role_service import RoleService

    owner = _user(db._session, "owner@test.local")
    manager = _user(db._session, "manager@test.local")
    connection = _connection(db._session, owner_id=owner.id, name="conn-A")

    role = await RoleService(db).by_name("Knowledge Manager")
    assert role is not None
    await RoleService(db).assign(admin_ctx(), user_id=manager.id, role_id=role.id)

    authz = RbacAuthorizer(db)
    who = ctx(manager.id)
    connection_ref = ResourceRef.to(ResourceType.CONNECTION, connection)

    # May curate — every store, by a role scoped privilege rather than a grant.
    assert await authz.allowed(who, _knowledge(connection), Privilege.MANAGE)
    assert await authz.allowed(who, _knowledge(connection), Privilege.MODIFY)

    # And may not read the data, edit the credentials, delete it, or widen what
    # leaves it. Four separate assertions because they are four separate powers
    # and a single "cannot do anything" would pass if `describe` were missing.
    assert await authz.allowed(who, connection_ref, Privilege.DESCRIBE)
    assert not await authz.allowed(who, connection_ref, Privilege.SELECT)
    assert not await authz.allowed(who, connection_ref, Privilege.MODIFY)
    assert not await authz.allowed(who, connection_ref, Privilege.DELETE)
    assert not await authz.allowed(who, connection_ref, Privilege.MANAGE)


def test_the_flag_and_the_function_are_gone() -> None:
    """Neither survives as a second, quieter answer to the same question.

    A grep rather than a behavioural test, because the claim is about absence —
    and because a `can_curate` left behind with two callers would be a rule
    nobody was reviewing sitting beside the one everybody was.
    """
    app = Path("app")
    for path in app.rglob("*.py"):
        source = path.read_text()
        if "migrations/versions" in path.as_posix():
            continue  # a revision's prose may name what it replaced
        assert "def can_curate" not in source, f"{path} still defines can_curate"
        assert "settings.curation_admin_only" not in source, (
            f"{path} still reads curation_admin_only"
        )

    from app.core.config import Settings

    assert not hasattr(Settings(), "curation_admin_only")


# ── the audit writer ─────────────────────────────────────────────────────
class _FakeDb:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)


@pytest.mark.asyncio
async def test_an_audit_row_carries_the_actor_the_action_and_the_resource() -> None:
    db = _FakeDb()
    target = uuid4()
    row = await audit.record(
        db, ctx(),
        action=audit.TEMPLATE_CREATED,
        resource_type=audit.TEMPLATE,
        resource_id=target,
        detail={"source": "MANUAL"},
    )
    assert row is not None
    assert row.actor_user_id == OWNER
    assert row.action == audit.TEMPLATE_CREATED
    assert row.resource_id == target
    assert row.outcome == audit.SUCCESS
    assert db.added == [row]


def test_the_row_joins_the_callers_transaction_and_is_never_flushed() -> None:
    """Rule 1. A log that can commit while the action it describes rolls back
    is a log that invents history — so `record` only ever calls `add`.

    Asserted on the parse, because a `flush` added later would be invisible to
    a fake that does not implement one.
    """
    tree = ast.parse(Path("app/services/audit.py").read_text())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "add" in called
    assert "flush" not in called
    assert "commit" not in called


@pytest.mark.asyncio
async def test_failing_to_log_never_fails_the_action() -> None:
    """Rule 2, and the opposite posture to the guard's — deliberately. A
    curator must not lose a saved template to a full disk on the audit table.
    """
    class _Broken:
        def add(self, row):
            raise RuntimeError("the audit table is gone")

    assert await audit.record(_Broken(), ctx(), action="x") is None


@pytest.mark.asyncio
async def test_the_ip_is_recorded_when_there_is_one_and_null_when_there_is_not() -> None:
    db = _FakeDb()
    with_ip = await audit.record(
        db, RequestContext(user_id=OWNER, email="e", role="MEMBER", actor_ip="10.0.0.4"),
        action="x",
    )
    without = await audit.record(db, ctx(), action="x")
    assert with_ip is not None and with_ip.actor_ip == "10.0.0.4"
    # `None`, not `""` — the column is nullable for exactly this case, and an
    # empty string would sort and filter as though it were an address.
    assert without is not None and without.actor_ip is None


@pytest.mark.asyncio
async def test_detail_carries_identifiers_and_counts_never_content() -> None:
    """Rule 3, enforced in one place rather than trusted at ten call sites."""
    db = _FakeDb()
    row = await audit.record(
        db, ctx(), action="x",
        detail={
            "count": 4,
            "enabled": True,
            "nothing": None,
            "sql": "SELECT " + "x" * 5_000,
            "ids": [str(uuid4()) for _ in range(50)],
        },
    )
    assert row is not None
    assert row.detail["count"] == 4
    assert row.detail["enabled"] is True
    assert row.detail["nothing"] is None
    assert len(row.detail["sql"]) == audit.MAX_DETAIL_CHARS
    assert len(row.detail["ids"]) == 20


@pytest.mark.asyncio
async def test_an_over_long_action_is_capped_to_the_column() -> None:
    """`action` is `String(60)`. A value that would raise on insert would fail
    the *curation write* it is describing, which is rule 2 violated by a
    truncation nobody did."""
    db = _FakeDb()
    row = await audit.record(db, ctx(), action="k." + "x" * 200)
    assert row is not None and len(row.action) == 60


# ── the vocabulary, and the reason it is a vocabulary ────────────────────
def test_every_audited_action_is_namespaced() -> None:
    """An admin filtering the log should be able to ask for "everything the
    learning loop did" with a prefix rather than a list."""
    actions = [
        v for k, v in vars(audit).items()
        if k.isupper() and isinstance(v, str) and "." in v
    ]
    assert actions
    assert all(a.startswith("knowledge.") for a in actions)


def test_every_curation_write_path_records_one() -> None:
    """The box, asserted on the parse rather than by counting call sites by
    hand: each of these route functions must mention `audit.record`.

    A route added later that writes curation and forgets this is the failure
    mode — one unlogged write is enough to make the log untrustworthy, because
    a reader cannot tell a gap from an absence of activity.
    """
    tree = ast.parse(Path("app/api/v1/knowledge.py").read_text())
    audited = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "record"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "audit"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )
    }
    for name in (
        "create_template",
        "update_template",
        "archive_template",
        "revalidate_store",
        "set_embedding_search",
        "resolve_review",
        "create_benchmark",
        "delete_benchmark",
        "run_benchmark",
    ):
        assert name in audited, f"{name} writes curation and logs nothing"


def test_the_audit_reader_needs_the_audit_capability() -> None:
    """An audit log is a record *about people*. A curator has an operational
    need to change their connection's knowledge and none to read who else did
    what, and from where.

    It is a **capability** rather than an admin flag as of Phase 3, which is
    what makes an Auditor possible: somebody who can read this and change
    nothing anywhere else."""
    tree = ast.parse(Path("app/api/v1/audit.py").read_text())
    routes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(d, ast.Call | ast.Attribute)
            for d in node.decorator_list
        )
    ]
    assert routes
    for route in routes:
        annotations = {
            ast.unparse(a.annotation) for a in route.args.args if a.annotation
        }
        assert "AuditReadDep" in annotations, route.name
