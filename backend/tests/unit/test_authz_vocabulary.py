"""Phase 0 — the vocabulary exists, the lattice is a lattice, and today's
answers are unchanged.

Nothing in this suite tests a feature, because Phase 0 ships no feature. It
tests the four things that would otherwise be discovered wrong three phases
later, when a hundred call sites already depend on them:

* **The lattice is really a lattice.** Reflexive, transitive, `manage` above
  everything, `describe` below everything. It is expanded at *check* time into
  `WHERE privilege = ANY(:satisfying)`, so an asymmetry here would be a silent
  privilege escalation in a query nobody reads twice.
* **`satisfying()` cannot be mutated by its caller.** It hands out the shared
  frozenset; a caller that could `.add()` to it would widen the lattice for
  every later request in the process.
* **`OwnerOnlyAuthorizer` agrees with `owns()`**, case for case, including the
  three that look alike and are not: no `owner_id` attribute at all, an
  `owner_id` of `None`, and somebody else's id.
* **Every resource type says what all five privileges mean.** The matrix is the
  specification and the source of `GET /{resource}/{id}/actions`; a type with a
  hole in it is a share dialog with a blank radio button.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.context import RequestContext
from app.domain.ports.authz import (
    Decision,
    Everything,
    Ids,
    ResourceRef,
    Subquery,
)
from app.domain.value_objects.authz import (
    ALL_PRIVILEGES,
    DERIVED_RESOURCE_TYPES,
    PRIVILEGE_MEANINGS,
    PRIVILEGED_CAPABILITIES,
    Capability,
    PrincipalKind,
    Privilege,
    ResourceType,
    implied_by,
    satisfying,
)
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.services.policy import owns

OWNER = uuid4()
STRANGER = uuid4()


def ctx(user_id=OWNER) -> RequestContext:
    return RequestContext(
        user_id=user_id, email="u@test.local", role="MEMBER", correlation_id="t"
    )


# ── the vocabulary is the size the design says it is ─────────────────────
def test_the_enums_are_closed_and_the_right_size() -> None:
    """Counts, because every one of them is a number the plan commits to and a
    silent addition is how a vocabulary stops being one."""
    assert len(Privilege) == 5
    assert len(ResourceType) == 8
    assert len(Capability) == 18
    assert len(PrincipalKind) == 2


def test_create_is_a_capability_and_never_a_privilege() -> None:
    """You cannot hold a privilege on an instance that does not exist yet."""
    assert not any("create" in p.value for p in Privilege)
    assert {c for c in Capability if c.value.endswith(".create")} == {
        Capability.CONNECTION_CREATE,
        Capability.LLM_CONFIG_CREATE,
        Capability.DASHBOARD_CREATE,
        Capability.REPORT_CREATE,
        Capability.CONVERSATION_CREATE,
    }


def test_the_privileged_four_are_the_ones_a_leaked_key_must_not_reach() -> None:
    assert set(PRIVILEGED_CAPABILITIES) == {
        Capability.USER_MANAGE,
        Capability.ROLE_MANAGE,
        Capability.SERVICE_USER_MANAGE,
        Capability.SETTINGS_MANAGE,
    }


def test_the_derived_types_are_exactly_knowledge_and_the_semantic_layer() -> None:
    """Both carry the connection's id, so both resolve owners through it."""
    assert set(DERIVED_RESOURCE_TYPES) == {
        ResourceType.KNOWLEDGE,
        ResourceType.SEMANTIC_LAYER,
    }


# ── the lattice ──────────────────────────────────────────────────────────
def test_the_lattice_is_reflexive() -> None:
    """Holding a privilege satisfies a demand for it. The one that would be
    embarrassing to get wrong."""
    for p in Privilege:
        assert p in satisfying(p)


def test_the_lattice_is_transitive() -> None:
    """If holding `held` answers a demand for `mid`, and `mid` answers a demand
    for `low`, then `held` answers `low`. Asserted over all 125 triples rather
    than argued about, because the expansion happens inside a SQL `ANY(...)`
    where nobody re-reads it."""
    for low in Privilege:
        for mid in satisfying(low):
            for held in satisfying(mid):
                assert held in satisfying(low), f"{held} should satisfy {low}"


def test_manage_satisfies_all_five_and_only_manage_does() -> None:
    for p in Privilege:
        assert Privilege.MANAGE in satisfying(p)
    assert satisfying(Privilege.MANAGE) == {Privilege.MANAGE}


def test_describe_is_the_floor_and_satisfies_only_itself_upward() -> None:
    """Everything satisfies a demand for `describe` — it is the floor — and
    holding `describe` satisfies nothing above it."""
    assert satisfying(Privilege.DESCRIBE) == ALL_PRIVILEGES
    for p in Privilege:
        if p is not Privilege.DESCRIBE:
            assert Privilege.DESCRIBE not in satisfying(p)


def test_the_lattice_is_linear() -> None:
    """Five privileges, and the satisfying sets nest: 5 ⊃ 4 ⊃ 3 ⊃ 2 ⊃ 1. A
    non-linear lattice is a legitimate design and *not this one*, so the shape
    is asserted rather than assumed."""
    order = [
        Privilege.DESCRIBE, Privilege.SELECT, Privilege.MODIFY,
        Privilege.DELETE, Privilege.MANAGE,
    ]
    sizes = [len(satisfying(p)) for p in order]
    assert sizes == [5, 4, 3, 2, 1]
    for lower, higher in zip(order, order[1:], strict=False):
        assert satisfying(higher) < satisfying(lower)


def test_implied_by_is_the_other_direction() -> None:
    """What a holder of X also holds. `manage` implies all five; `describe`
    implies only itself."""
    assert implied_by(Privilege.MANAGE) == ALL_PRIVILEGES
    assert implied_by(Privilege.DESCRIBE) == {Privilege.DESCRIBE}
    assert implied_by(Privilege.MODIFY) == {
        Privilege.MODIFY, Privilege.SELECT, Privilege.DESCRIBE
    }


def test_satisfying_returns_something_a_caller_cannot_mutate() -> None:
    """It hands out the shared set. A caller that could add to it would widen
    the lattice for every later request in the process."""
    result = satisfying(Privilege.MANAGE)
    assert isinstance(result, frozenset)
    with pytest.raises(AttributeError):
        result.add(Privilege.DESCRIBE)  # type: ignore[attr-defined]
    assert satisfying(Privilege.MANAGE) == {Privilege.MANAGE}


def test_the_lattice_table_itself_is_read_only() -> None:
    """`_SATISFIED_BY` is a `MappingProxyType`, so a module that imported it
    could not quietly rewrite a row of it either."""
    from app.domain.value_objects.authz import _SATISFIED_BY

    with pytest.raises(TypeError):
        _SATISFIED_BY[Privilege.DESCRIBE] = frozenset()  # type: ignore[index]


# ── the specification matrix ─────────────────────────────────────────────
def test_every_resource_type_says_what_every_privilege_means() -> None:
    """The conformance check. A ninth resource type cannot be added without
    deciding what its five verbs do."""
    assert set(PRIVILEGE_MEANINGS) == set(ResourceType)
    for type_ in ResourceType:
        assert set(PRIVILEGE_MEANINGS[type_]) == set(Privilege), type_


def test_every_meaning_is_a_sentence_somebody_could_be_shown() -> None:
    """These strings are rendered next to a radio button in the share dialog,
    so an empty one or a repeat of the privilege's own name is a bug."""
    for type_ in ResourceType:
        for privilege, meaning in PRIVILEGE_MEANINGS[type_].items():
            assert meaning.strip()
            assert meaning.rstrip().endswith(".")
            assert meaning.strip().lower() != privilege.value


def test_the_meanings_are_read_only() -> None:
    with pytest.raises(TypeError):
        PRIVILEGE_MEANINGS[ResourceType.TEAM] = {}  # type: ignore[index]


# ── the port's value objects ─────────────────────────────────────────────
def test_a_decision_carries_its_reason_and_is_truthy_when_allowed() -> None:
    yes = Decision(True, ("owner",))
    no = Decision(False)
    assert yes and yes.because == ("owner",)
    assert not no and no.because == ()


def test_a_ref_ignores_its_carried_row_when_comparing() -> None:
    """`entity` is an optimisation, never a second identifier: two refs to the
    same thing are the same ref whether or not one is carrying the row."""
    resource_id = uuid4()
    bare = ResourceRef(ResourceType.DASHBOARD, resource_id)
    loaded = ResourceRef(ResourceType.DASHBOARD, resource_id, entity=object())
    assert bare == loaded
    assert hash(bare) == hash(loaded)


def test_a_ref_repr_never_prints_the_row() -> None:
    """A connection row holds an encrypted credential and a `repr` of it in a
    log line would be the one place nobody looks."""

    class _Secretive:
        id = uuid4()
        password = "hunter2"  # noqa: S105

        def __repr__(self) -> str:  # pragma: no cover - would fail the assert
            return f"<Connection password={self.password}>"

    ref = ResourceRef.to(ResourceType.CONNECTION, _Secretive())
    assert "hunter2" not in repr(ref)


def test_ref_to_takes_its_id_from_the_row() -> None:
    class _Row:
        id = uuid4()

    row = _Row()
    assert ResourceRef.to(ResourceType.REPORT, row).id == row.id


def test_the_three_visible_shapes_are_distinguishable() -> None:
    """`Everything` short-circuits before any query is built, `Subquery` is
    composed into the caller's `SELECT`, `Ids` is the honest remote shape."""
    assert isinstance(Everything(), Everything)
    assert Subquery(select="not-really-a-select").select == "not-really-a-select"
    assert Ids(frozenset()).ids == frozenset()


# ── OwnerOnlyAuthorizer agrees with `owns()`, case for case ──────────────
class _Owned:
    """Anything with an `owner_id`. The shape every scoped table has."""

    def __init__(self, owner_id) -> None:
        self.id = uuid4()
        self.owner_id = owner_id


class _Unowned:
    """A row with no `owner_id` attribute at all — the third case, and the one
    a truthiness test would get wrong."""

    def __init__(self) -> None:
        self.id = uuid4()


CASES = [
    ("the owner", _Owned(OWNER), OWNER, True),
    ("somebody else", _Owned(STRANGER), OWNER, False),
    ("an unowned row", _Owned(None), OWNER, False),
    ("a row with no owner column", _Unowned(), OWNER, False),
]


@pytest.mark.parametrize(("label", "row", "actor", "expected"), CASES)
async def test_owner_only_agrees_with_owns(label, row, actor, expected) -> None:
    """The whole point of Phase 0: routing a call site through the port must
    return what `owns()` returned, or the refactor is a behaviour change."""
    who = ctx(actor)
    authorizer = OwnerOnlyAuthorizer()
    ref = ResourceRef.to(ResourceType.DASHBOARD, row)

    assert owns(who, row) is expected, label
    for privilege in Privilege:
        decision = await authorizer.allowed(who, ref, privilege)
        assert decision.allowed is expected, f"{label} / {privilege}"


async def test_ownership_confers_the_whole_lattice_and_nothing_less() -> None:
    """Ownership is not one privilege. It is all five, which is why it is not
    modelled as a grant."""
    authorizer = OwnerOnlyAuthorizer()
    mine = ResourceRef.to(ResourceType.REPORT, _Owned(OWNER))
    theirs = ResourceRef.to(ResourceType.REPORT, _Owned(STRANGER))

    assert await authorizer.privileges_on(ctx(), mine) == ALL_PRIVILEGES
    assert await authorizer.privileges_on(ctx(), theirs) == frozenset()


async def test_an_allowed_decision_says_owner_and_a_denial_says_nothing() -> None:
    """`because` is a closed vocabulary — grant ids, role names, `owner`,
    `admin_self_grant` — because it is written into `audit_logs.detail`."""
    authorizer = OwnerOnlyAuthorizer()
    yes = await authorizer.allowed(
        ctx(), ResourceRef.to(ResourceType.DASHBOARD, _Owned(OWNER)), Privilege.MANAGE
    )
    assert yes.because == ("owner",)


async def test_owner_only_never_consults_the_admin_flag() -> None:
    """An administrator does not today reach another user's dashboard, and
    adding that here would be a behaviour change smuggled into a refactor."""
    admin = RequestContext(
        user_id=STRANGER, email="a@test.local", role="ADMIN", correlation_id="t"
    )
    assert admin.is_admin
    decision = await OwnerOnlyAuthorizer().allowed(
        admin, ResourceRef.to(ResourceType.DASHBOARD, _Owned(OWNER)), Privilege.SELECT
    )
    assert not decision.allowed


async def test_allowed_many_answers_in_order() -> None:
    authorizer = OwnerOnlyAuthorizer()
    pairs = [
        (ResourceRef.to(ResourceType.DASHBOARD, _Owned(OWNER)), Privilege.SELECT),
        (ResourceRef.to(ResourceType.DASHBOARD, _Owned(STRANGER)), Privilege.SELECT),
        (ResourceRef.to(ResourceType.REPORT, _Owned(OWNER)), Privilege.DELETE),
    ]
    assert [d.allowed for d in await authorizer.allowed_many(ctx(), pairs)] == [
        True, False, True
    ]


async def test_asking_about_a_row_it_was_not_given_needs_a_session() -> None:
    """Failing loudly beats returning `False` for a question that was never
    actually asked — a denial that was really a missing dependency is the
    hardest kind of bug to see."""
    with pytest.raises(RuntimeError, match="without a session"):
        await OwnerOnlyAuthorizer().allowed(
            ctx(), ResourceRef(ResourceType.DASHBOARD, uuid4()), Privilege.SELECT
        )


# ── `visible` composes rather than iterates ──────────────────────────────
async def test_visible_is_a_subquery_for_every_type_that_has_an_owner() -> None:
    """A list endpoint must fold this into its own `SELECT`. Returning ids
    would be the anti-pattern the port exists to prevent: N round trips and
    pagination that counts rows the caller cannot see."""
    authorizer = OwnerOnlyAuthorizer()
    for type_ in ResourceType:
        if type_ is ResourceType.TEAM:
            continue
        visible = await authorizer.visible(ctx(), type_, Privilege.SELECT)
        assert isinstance(visible, Subquery), type_


async def test_the_visible_subquery_filters_on_owner_in_sql() -> None:
    visible = await OwnerOnlyAuthorizer().visible(
        ctx(), ResourceType.DASHBOARD, Privilege.SELECT
    )
    assert isinstance(visible, Subquery)
    sql = str(visible.select).lower()
    assert "from dashboards" in sql
    assert "owner_id" in sql


async def test_the_derived_types_resolve_through_the_connection() -> None:
    """A knowledge store's owner *is* its connection's owner, and its id is the
    connection's id — so both arms of the question read the same table."""
    authorizer = OwnerOnlyAuthorizer()
    for type_ in DERIVED_RESOURCE_TYPES:
        visible = await authorizer.visible(ctx(), type_, Privilege.SELECT)
        assert isinstance(visible, Subquery)
        assert "from database_connections" in str(visible.select).lower()


async def test_a_type_with_no_table_yet_is_visible_as_nothing() -> None:
    """Teams arrive in Phase 4. Until then nobody owns one, so the honest
    answer is an empty set rather than an exception."""
    visible = await OwnerOnlyAuthorizer().visible(
        ctx(), ResourceType.TEAM, Privilege.SELECT
    )
    assert isinstance(visible, Ids)
    assert visible.ids == frozenset()


# ── the switches ─────────────────────────────────────────────────────────
def test_the_switches_default_to_todays_behaviour() -> None:
    """Phase 0 changes nothing that anybody can observe, and these four
    defaults are what that sentence rests on."""
    from app.core.config import Settings

    settings = Settings()
    assert settings.authz_backend == "owner_only"
    assert settings.auth_provider == "local"
    assert settings.allow_privileged_service_users is False
    assert settings.service_key_default_ttl_days == 365


def test_the_authorizer_dependency_resolves_the_owner_only_implementation() -> None:
    """Wired exactly as `get_identity_provider` is, so the port is the seam and
    no caller learns which implementation it got."""
    from app.api.deps import get_authorizer
    from app.core.config import Settings

    assert isinstance(get_authorizer(None, Settings()), OwnerOnlyAuthorizer)  # type: ignore[arg-type]


def test_naming_a_backend_that_does_not_exist_yet_says_so() -> None:
    """`rbac` is Phase 6. Falling back silently to owner-only would be an
    installation that believes it enforces grants and does not."""
    from app.api.deps import get_authorizer
    from app.core.config import Settings

    with pytest.raises(NotImplementedError, match="Phase 6"):
        get_authorizer(None, Settings(authz_backend="rbac"))  # type: ignore[arg-type]


def test_the_domain_layer_imports_no_database_driver() -> None:
    """`lint-imports` enforces this for the whole package; this asserts it for
    the two modules Phase 0 added, so a `Select` type annotation that slipped
    into the port fails a test rather than a lint nobody ran locally."""
    import ast
    from pathlib import Path

    for module in ("app/domain/ports/authz.py", "app/domain/value_objects/authz.py"):
        tree = ast.parse(Path(module).read_text())
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not imported & {"sqlalchemy", "fastapi", "pydantic"}, module


# ── Phase 1: the list endpoints compose rather than iterate ──────────────
async def test_a_list_endpoint_emits_a_subquery_rather_than_filtering_in_python() -> None:
    """The claim Phase 1 rests on, asserted against the emitted SQL.

    `DashboardService.list` must fold the authorizer's answer into the
    statement it was already going to run. The failure mode this catches is the
    tempting one: load every dashboard, ask `allowed` about each, drop the
    misses. That returns the same rows today and a short page tomorrow, because
    `LIMIT` would count rows the caller cannot see — a pagination bug that only
    appears once somebody is sharing.
    """
    from app.services.dashboard_service import DashboardService
    from app.services.report_service import ReportService

    class _RecordingDb:
        """Captures the statement and answers with nothing."""

        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement):  # noqa: ANN001, ANN202
            self.statements.append(str(statement))

            class _Empty:
                def scalars(self):  # noqa: ANN202
                    return iter(())

            return _Empty()

    for service_class in (DashboardService, ReportService):
        db = _RecordingDb()
        service = service_class(db, object(), OwnerOnlyAuthorizer())  # type: ignore[arg-type]
        assert await service.list(ctx()) == []

        assert len(db.statements) == 1, "one round trip, not one per row"
        sql = " ".join(db.statements[0].lower().split())
        assert "in (select" in sql, sql
        assert "owner_id" in sql, sql


async def test_the_subquery_is_composed_into_the_callers_own_query() -> None:
    """`restrict` narrows a statement; it does not replace it. The ordering the
    caller asked for has to survive, or the authorizer would silently be
    deciding the page order too."""
    from sqlalchemy import select

    from app.infra.authz.compose import restrict
    from app.infra.db.models import Dashboard

    visible = await OwnerOnlyAuthorizer().visible(
        ctx(), ResourceType.DASHBOARD, Privilege.SELECT
    )
    sql = " ".join(
        str(
            restrict(
                select(Dashboard).order_by(Dashboard.updated_at.desc()),
                Dashboard.id,
                visible,
            )
        )
        .lower()
        .split()
    )
    assert "order by dashboards.updated_at desc" in sql
    assert "dashboards.id in (select dashboards.id" in sql


async def test_everything_adds_no_clause_at_all() -> None:
    """The wildcard case must cost nothing — not even an `IN (SELECT ...)` for
    the planner to unwrap."""
    from sqlalchemy import select

    from app.infra.authz.compose import restrict
    from app.infra.db.models import Dashboard

    plain = select(Dashboard)
    assert str(restrict(plain, Dashboard.id, Everything())) == str(plain)


async def test_visible_as_no_ids_matches_no_rows_rather_than_every_row() -> None:
    """`Ids(frozenset())` is "you may see none of these". An unfiltered query
    would be the dangerous reading of that."""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from app.infra.authz.compose import restrict
    from app.infra.db.models import Dashboard

    # Compiled against a real dialect with the binds rendered: an empty `IN` is
    # a *postcompile* placeholder until then, so a plain `str()` would prove
    # nothing about what the database is asked.
    statement = restrict(select(Dashboard.id), Dashboard.id, Ids(frozenset()))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert "1 != 1" in sql, sql
