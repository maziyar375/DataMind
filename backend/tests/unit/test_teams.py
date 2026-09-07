"""Teams, and the one sentence requirement 3 asks for.

> *"A BI Engineer team could have access to the relevant BI resources, and
> members of that team would inherit those permissions."*

Phase 4 delivers the first half of that — a role assigned to a team reaches its
members — and deliberately not the second, which needs grants. What this file
pins down, in the order it would hurt if it broke:

* **A role reaching a principal through a team is the same answer as one
  reaching them directly**, resolved in the same query, and it **stops** the
  moment they leave. That is the feature.
* **`ctx.team_ids` is populated and read by no resource decision.** Phase 4
  ships the membership; Phase 6 ships the grant that reads it, and a test that
  proves the gap is the difference between "not yet wired" and "quietly
  half-wired".
* **Deleting a team that holds a role is refused, naming it** — Metabase's
  *"reassigned to All Users"* is the anti-pattern, and a silent widening at the
  moment somebody was trying to narrow is the failure being avoided.
* **The last-administrator guard still counts people.** A team could hold
  `Administrator`; counting one as an administrator would let the last named
  one be removed on the strength of a team somebody else can empty in a click.
"""
from __future__ import annotations

import pathlib
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.value_objects import Role as LegacyRole
from app.domain.value_objects.authz import ADMINISTRATOR, Capability
from app.infra.db.models import AuditLog, RoleAssignment, TeamMember
from app.services.role_service import RoleService
from app.services.team_service import TeamService

# `db`, `ctx` and `_user` come from `tests/unit/conftest.py` — one fixture for
# the whole permission model, because it is one model.
from tests.unit.conftest import AsyncSessionShim, _user, ctx


# ── membership resolution ────────────────────────────────────────────────
async def test_a_principal_in_three_teams_resolves_all_three(
    db: AsyncSessionShim,
) -> None:
    service = TeamService(db)
    person = _user(db._session, "member@test.local")
    ids = set()
    for name in ("Analytics", "Finance", "Platform"):
        team = await service.create(ctx(), name=name)
        await service.add_member(ctx(), team_id=team.id, user_id=person.id)
        ids.add(team.id)

    assert await service.team_ids(person.id) == ids
    assert [t.name for t in await service.teams_of(person.id)] == [
        "Analytics", "Finance", "Platform",
    ]


async def test_membership_resolution_is_one_query(db: AsyncSessionShim) -> None:
    """It runs on every authenticated request, beside capability resolution."""
    service = TeamService(db)
    person = _user(db._session, "hot@test.local")
    team = await service.create(ctx(), name="Analytics")
    await service.add_member(ctx(), team_id=team.id, user_id=person.id)
    db.statements.clear()

    await service.team_ids(person.id)

    assert len(db.statements) == 1


async def test_adding_somebody_twice_is_not_an_error(db: AsyncSessionShim) -> None:
    service = TeamService(db)
    person = _user(db._session, "twice@test.local")
    team = await service.create(ctx(), name="Analytics")

    await service.add_member(ctx(), team_id=team.id, user_id=person.id)
    await service.add_member(ctx(), team_id=team.id, user_id=person.id)

    assert len(await service.members(team.id)) == 1


async def test_adding_a_missing_person_is_a_404(db: AsyncSessionShim) -> None:
    service = TeamService(db)
    team = await service.create(ctx(), name="Analytics")

    with pytest.raises(NotFoundError):
        await service.add_member(ctx(), team_id=team.id, user_id=uuid4())


# ── the feature ──────────────────────────────────────────────────────────
async def test_a_team_role_reaches_its_members_and_stops_when_they_leave(
    db: AsyncSessionShim,
) -> None:
    """Requirement 3, as one assertion and then its converse.

    The converse is the half that matters operationally: a permission that
    arrives with membership and does not leave with it is a permission nobody
    can revoke by managing the team, which is the whole reason to have teams.
    """
    teams = TeamService(db)
    roles = RoleService(db)
    person = _user(db._session, "bi@test.local")
    bi_engineer = await roles.by_name("BI Engineer")
    assert bi_engineer is not None

    team = await teams.create(ctx(), name="BI Engineers")
    await teams.assign_role(ctx(), team_id=team.id, role_id=bi_engineer.id)
    await teams.add_member(ctx(), team_id=team.id, user_id=person.id)

    held = await roles.resolve_capabilities(
        person.id, await teams.team_ids(person.id)
    )
    assert Capability.DASHBOARD_CREATE in held

    await teams.remove_member(ctx(), team_id=team.id, user_id=person.id)

    after = await roles.resolve_capabilities(
        person.id, await teams.team_ids(person.id)
    )
    assert Capability.DASHBOARD_CREATE not in after


async def test_direct_and_team_roles_union_in_one_query(
    db: AsyncSessionShim,
) -> None:
    """Two arms of one `WHERE`, not two round trips — this is on the hot path."""
    teams = TeamService(db)
    roles = RoleService(db)
    person = _user(db._session, "both@test.local")
    auditor = await roles.by_name("Auditor")
    knowledge = await roles.by_name("Knowledge Manager")
    assert auditor is not None and knowledge is not None

    await roles.assign(ctx(), user_id=person.id, role_id=auditor.id)
    team = await teams.create(ctx(), name="Curators")
    await teams.assign_role(ctx(), team_id=team.id, role_id=knowledge.id)
    await teams.add_member(ctx(), team_id=team.id, user_id=person.id)

    team_ids = await teams.team_ids(person.id)
    db.statements.clear()
    held = await roles.resolve_capabilities(person.id, team_ids)

    assert len(db.statements) == 1
    assert Capability.AUDIT_READ in held  # direct
    assert Capability.BENCHMARK_MANAGE in held  # through the team


async def test_a_role_held_twice_is_listed_once(db: AsyncSessionShim) -> None:
    """Directly *and* through a team is one role — two chips would say nothing."""
    teams = TeamService(db)
    roles = RoleService(db)
    person = _user(db._session, "double@test.local")
    auditor = await roles.by_name("Auditor")
    assert auditor is not None

    await roles.assign(ctx(), user_id=person.id, role_id=auditor.id)
    team = await teams.create(ctx(), name="Auditors")
    await teams.assign_role(ctx(), team_id=team.id, role_id=auditor.id)
    await teams.add_member(ctx(), team_id=team.id, user_id=person.id)

    named = await roles.roles_of(person.id, await teams.team_ids(person.id))

    assert [role.name for role in named] == ["Auditor"]


async def test_the_list_carries_each_team_s_roles(db: AsyncSessionShim) -> None:
    """The list screen's own question, and the bug this test was written for.

    Every team came back with an empty `roles` list, so the Teams tab showed
    "no roles yet" beside a team that plainly had one — the rail two inches
    away was rendering the capabilities it granted. A list that shows a member
    count and nothing else puts "what does this grant?" one click from every
    row, which is the question somebody scanning the screen came with.
    """
    teams = TeamService(db)
    roles = RoleService(db)
    bi = await roles.by_name("BI Engineer")
    auditor = await roles.by_name("Auditor")
    assert bi is not None and auditor is not None

    engineers = await teams.create(ctx(), name="BI Engineers")
    watchers = await teams.create(ctx(), name="Watchers")
    await teams.create(ctx(), name="Nobody")
    await teams.assign_role(ctx(), team_id=engineers.id, role_id=bi.id)
    await teams.assign_role(ctx(), team_id=watchers.id, role_id=auditor.id)
    await teams.assign_role(ctx(), team_id=watchers.id, role_id=bi.id)

    by_team = await teams.roles_by_team()

    assert by_team[engineers.id] == ["BI Engineer"]
    assert by_team[watchers.id] == ["Auditor", "BI Engineer"]
    assert by_team.get((await teams.by_name("Nobody")).id) is None


async def test_every_team_s_roles_are_one_query(db: AsyncSessionShim) -> None:
    """Not one per row: eight queries to draw one table is how a list page
    becomes slow for no reason."""
    teams = TeamService(db)
    roles = RoleService(db)
    viewer = await roles.by_name("Viewer")
    assert viewer is not None
    for name in ("A", "B", "C"):
        team = await teams.create(ctx(), name=name)
        await teams.assign_role(ctx(), team_id=team.id, role_id=viewer.id)
    db.statements.clear()

    await teams.roles_by_team()

    assert len(db.statements) == 1


# ── the refusals ─────────────────────────────────────────────────────────
async def test_deleting_a_team_that_holds_a_role_is_refused_and_names_it(
    db: AsyncSessionShim,
) -> None:
    teams = TeamService(db)
    roles = RoleService(db)
    viewer = await roles.by_name("Viewer")
    assert viewer is not None
    team = await teams.create(ctx(), name="Readers")
    await teams.assign_role(ctx(), team_id=team.id, role_id=viewer.id)

    with pytest.raises(ConflictError) as raised:
        await teams.delete(ctx(), team.id)

    assert "Viewer" in str(raised.value)


async def test_a_team_holding_nothing_deletes(db: AsyncSessionShim) -> None:
    teams = TeamService(db)
    person = _user(db._session, "leaving@test.local")
    team = await teams.create(ctx(), name="Temporary")
    await teams.add_member(ctx(), team_id=team.id, user_id=person.id)

    await teams.delete(ctx(), team.id)

    assert await teams.by_name("Temporary") is None
    # The person survives; only the membership went.
    assert db._session.get(type(person), person.id) is not None


async def test_deleting_a_role_a_team_holds_names_the_team_not_a_person(
    db: AsyncSessionShim,
) -> None:
    """"Still assigned to Sara" would send somebody to the wrong screen."""
    teams = TeamService(db)
    roles = RoleService(db)
    role = await roles.create(ctx(), name="Analyst", capabilities=["team.read"])
    team = await teams.create(ctx(), name="Analysts")
    await teams.assign_role(ctx(), team_id=team.id, role_id=role.id)

    with pytest.raises(ConflictError) as raised:
        await roles.delete(ctx(), role.id)

    assert "the Analysts team" in str(raised.value)


async def test_a_duplicate_team_name_is_refused(db: AsyncSessionShim) -> None:
    service = TeamService(db)
    await service.create(ctx(), name="Analytics")

    with pytest.raises(ConflictError):
        await service.create(ctx(), name="Analytics")


# ── the external binding ─────────────────────────────────────────────────
async def test_a_binding_needs_both_halves_or_neither(db: AsyncSessionShim) -> None:
    """`ck_teams_source_pair`, enforced in the service so the message is a
    sentence rather than a constraint name."""
    service = TeamService(db)
    team = await service.create(ctx(), name="Analytics")

    with pytest.raises(ValidationError):
        await service.bind_source(
            ctx(), team.id, provider_id="oidc", source_id=None
        )
    with pytest.raises(ValidationError):
        await service.bind_source(
            ctx(), team.id, provider_id=None, source_id="/analytics"
        )


async def test_a_bound_team_records_both_columns_and_can_be_unbound(
    db: AsyncSessionShim,
) -> None:
    service = TeamService(db)
    team = await service.create(ctx(), name="Analytics")

    bound = await service.bind_source(
        ctx(), team.id, provider_id="oidc", source_id="/analytics"
    )
    assert (bound.provider_id, bound.source_id) == ("oidc", "/analytics")

    unbound = await service.bind_source(
        ctx(), team.id, provider_id=None, source_id=None
    )
    assert (unbound.provider_id, unbound.source_id) == (None, None)


async def test_two_teams_cannot_mirror_the_same_external_group(
    db: AsyncSessionShim,
) -> None:
    service = TeamService(db)
    first = await service.create(ctx(), name="Analytics")
    second = await service.create(ctx(), name="Analytics (old)")
    await service.bind_source(
        ctx(), first.id, provider_id="oidc", source_id="/analytics"
    )

    with pytest.raises(ConflictError):
        await service.bind_source(
            ctx(), second.id, provider_id="oidc", source_id="/analytics"
        )


async def test_rebinding_is_its_own_audit_action(db: AsyncSessionShim) -> None:
    """Not `team.renamed`. Rebinding redirects which people flow into a set of
    permissions; an audit log that filed it under a rename would be lying by
    omission."""
    service = TeamService(db)
    team = await service.create(ctx(), name="Analytics")
    await service.rename(ctx(), team.id, name="Analytics EU")
    await service.bind_source(
        ctx(), team.id, provider_id="oidc", source_id="/analytics"
    )
    db._session.flush()

    actions = [
        row.action for row in db._session.execute(sa.select(AuditLog)).scalars()
    ]
    assert actions == ["team.created", "team.renamed", "team.source.bound"]


# ── membership as a set ──────────────────────────────────────────────────
async def test_setting_the_membership_audits_only_who_actually_moved(
    db: AsyncSessionShim,
) -> None:
    """The picker sends the whole set; the log still reads as a list of moves.

    A "membership replaced" row would be a record nobody can read — the
    question an access review asks is *when did Ali join this team*, and only
    per-person rows can answer it.
    """
    service = TeamService(db)
    ali = _user(db._session, "ali@test.local")
    sara = _user(db._session, "sara@test.local")
    team = await service.create(ctx(), name="Analytics")
    await service.add_member(ctx(), team_id=team.id, user_id=ali.id)
    db._session.flush()
    db._session.execute(sa.delete(AuditLog))

    # Ali stays, Sara joins. One row, not two.
    after = await service.set_members(
        ctx(), team_id=team.id, user_ids=[ali.id, sara.id]
    )
    db._session.flush()

    assert {member.id for member in after} == {ali.id, sara.id}
    actions = [
        row.action for row in db._session.execute(sa.select(AuditLog)).scalars()
    ]
    assert actions == ["team.member.added"]


async def test_setting_the_membership_removes_who_is_no_longer_in_it(
    db: AsyncSessionShim,
) -> None:
    service = TeamService(db)
    ali = _user(db._session, "ali2@test.local")
    sara = _user(db._session, "sara2@test.local")
    team = await service.create(ctx(), name="Analytics")
    await service.set_members(ctx(), team_id=team.id, user_ids=[ali.id, sara.id])

    after = await service.set_members(ctx(), team_id=team.id, user_ids=[sara.id])

    assert [member.id for member in after] == [sara.id]


# ── cascades ─────────────────────────────────────────────────────────────
async def test_deleting_a_person_takes_their_memberships(
    db: AsyncSessionShim,
) -> None:
    service = TeamService(db)
    person = _user(db._session, "gone@test.local")
    team = await service.create(ctx(), name="Analytics")
    await service.add_member(ctx(), team_id=team.id, user_id=person.id)
    db._session.flush()

    db._session.delete(person)
    db._session.flush()

    assert db._session.execute(sa.select(TeamMember)).first() is None


async def test_deleting_a_team_takes_its_memberships(db: AsyncSessionShim) -> None:
    service = TeamService(db)
    person = _user(db._session, "stays@test.local")
    team = await service.create(ctx(), name="Analytics")
    await service.add_member(ctx(), team_id=team.id, user_id=person.id)

    await service.delete(ctx(), team.id)
    db._session.flush()

    assert db._session.execute(sa.select(TeamMember)).first() is None


async def test_deleting_a_team_takes_the_role_assignments_it_had(
    db: AsyncSessionShim,
) -> None:
    """`CASCADE` on the principal — after the refusal above has been satisfied
    by unassigning, there is nothing left to strand."""
    teams = TeamService(db)
    roles = RoleService(db)
    viewer = await roles.by_name("Viewer")
    assert viewer is not None
    team = await teams.create(ctx(), name="Readers")
    await teams.assign_role(ctx(), team_id=team.id, role_id=viewer.id)
    await teams.unassign_role(ctx(), team_id=team.id, role_id=viewer.id)

    await teams.delete(ctx(), team.id)
    db._session.flush()

    assert (
        db._session.execute(
            sa.select(RoleAssignment).where(RoleAssignment.team_id.is_not(None))
        ).first()
        is None
    )


# ── the last administrator still counts people ───────────────────────────
async def test_a_team_holding_administrator_does_not_satisfy_the_guard(
    db: AsyncSessionShim,
) -> None:
    """The guard's job is to keep one **person** recoverable.

    A team could hold `Administrator`, and counting it would let the last named
    administrator be removed on the strength of a team whose membership
    somebody else can empty in one click — leaving a workspace that looks
    administered and is not.
    """
    teams = TeamService(db)
    roles = RoleService(db)
    admin_role = await roles.by_name(ADMINISTRATOR)
    assert admin_role is not None
    only = _user(db._session, "only-admin@test.local", LegacyRole.ADMIN)
    await roles.assign(ctx(), user_id=only.id, role_id=admin_role.id)

    team = await teams.create(ctx(), name="Admins")
    await teams.assign_role(ctx(), team_id=team.id, role_id=admin_role.id)

    with pytest.raises(ValidationError):
        await roles.unassign(ctx(), user_id=only.id, role_id=admin_role.id)


# ── what Phase 4 deliberately does not do ────────────────────────────────
def test_no_resource_decision_reads_team_ids_yet() -> None:
    """Phase 4 ships membership; Phase 6 ships the grant that reads it.

    `ctx.team_ids` is populated on every request and consulted by exactly one
    thing — capability resolution, which unions the roles a team carries. No
    authorizer reads it, so no *resource* decision has moved. A grep rather
    than a behavioural test, because the claim is about absence.
    """
    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    readers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "team_ids" in path.read_text()
    }

    assert readers == {
        "core/context.py",              # declares it
        "api/deps.py",                  # resolves it
        "services/team_service.py",     # answers it
        "services/role_service.py",     # unions roles through it
        "api/v1/auth.py",               # renders the names
    }
    assert not (root / "infra" / "authz" / "owner_only.py").read_text().count(
        "team_ids"
    ), "an authorizer reads team_ids — that is Phase 6, not Phase 4"
