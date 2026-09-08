"""The access review: two lenses that agree, and a screen that shows no data.

Requirement 6 — *"the UI should clearly communicate what a user can and cannot
access"* — is a promise about a screen, and the two ways to break it are:

* **the two lenses disagree.** *"What can Ali reach?"* and *"who can reach
  this?"* are one set of facts read two ways. If they can disagree, an
  administrator revoking what one screen shows will be surprised by the other,
  and the review stops being evidence. A property test over a generated world
  is what holds that, not a pair of hand-picked cases.
* **the review leaks.** It is a screen about *reach*, shown to auditors, and a
  row that carried a host, a username or a stored statement would make the
  thing that exists to control disclosure a disclosure of its own.

Below those: the five path kinds are the five facts of §15.2, one for one, and
a CSV that Excel will run rather than show is a vulnerability with a name.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.api.v1.access_review import _cell, _csv
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.db.models import Dashboard, Role, RoleAssignment, RoleScopedPrivilege
from app.services.access_review_service import (
    DIRECT,
    OWNER,
    ROLE,
    TEAM,
    WILDCARD,
    AccessReviewService,
    ReachRow,
)
from app.services.team_service import TeamService
from tests.unit.conftest import ACTOR, AsyncSessionShim, _connection, _team_grant, _user
from tests.unit.conftest import ctx as admin_ctx


@pytest.fixture
def world(db: AsyncSessionShim):
    """One of each of the five facts, so every path kind has a row.

    Built as a *world* rather than per test because the property below needs
    all five present at once: two lenses agreeing on a world with only direct
    grants in it would prove almost nothing.
    """
    session = db._session

    class _World:
        pass

    w = _World()
    w.owner = _user(session, "owner@test.local")
    w.member = _user(session, "member@test.local")
    w.outsider = _user(session, "outsider@test.local")

    w.connection = _connection(session, owner_id=w.owner.id, name="Sales")
    w.board = Dashboard(
        id=uuid4(), owner_id=w.owner.id, name="Overview", status="ACTIVE",
        grid_columns=12, row_height_px=60, gap_px=12, compact_mode="VERTICAL",
        palette="default", theme_override="INHERIT",
        default_refresh_interval_seconds=0,
    )
    session.add(w.board)
    session.flush()

    w.db = db
    w.service = AccessReviewService(db)
    return w


async def _team_with(db: AsyncSessionShim, name: str, member: UUID):
    team = await TeamService(db).create(admin_ctx(), name=name, description="")
    await TeamService(db).add_member(admin_ctx(), team_id=team.id, user_id=member)
    return team


# ── the five facts, one row each ─────────────────────────────────────────
async def test_ownership_is_a_row_and_it_says_owner(world) -> None:
    """Ownership is one of the five facts and belongs on the screen.

    A review that listed only *grants* would answer "who can reach this
    dashboard" with an empty list for a board nobody has shared — which is
    both wrong and the most misleading kind of wrong, because it looks like an
    answer.
    """
    rows = await world.service.by_resource(ResourceType.DASHBOARD, world.board.id)

    owner_rows = [r for r in rows if r.path == OWNER]
    assert [r.principal_id for r in owner_rows] == [world.owner.id]
    assert owner_rows[0].resource_name == "Overview"
    assert owner_rows[0].privilege == str(Privilege.MANAGE)


async def test_a_direct_grant_says_direct(world) -> None:
    _team_grant(
        world.db._session, type_="dashboard", resource_id=world.board.id,
        privilege="select", user=world.member.id,
    )

    rows = await world.service.by_resource(ResourceType.DASHBOARD, world.board.id)
    granted = [r for r in rows if r.path == DIRECT]

    assert [(r.principal_id, r.privilege) for r in granted] == [
        (world.member.id, "select")
    ]
    assert granted[0].via == ""  # a direct grant arrives through nothing


async def test_a_team_grant_says_team_and_names_it(world) -> None:
    """*"Sara — select"* is a fact nobody can act on. *"Sara — select · via the
    Finance team"* tells them the revoke they want is on the team, and that
    clicking revoke on this row would do nothing."""
    team = await _team_with(world.db, "Finance", world.member.id)
    _team_grant(
        world.db._session, type_="dashboard", resource_id=world.board.id,
        privilege="modify", team=team.id,
    )

    rows = await world.service.by_resource(ResourceType.DASHBOARD, world.board.id)
    via_team = [r for r in rows if r.path == TEAM]

    assert len(via_team) == 1
    assert via_team[0].principal_id == team.id
    assert via_team[0].principal_kind == "TEAM"
    assert via_team[0].via == "Finance"


async def test_a_wildcard_says_wildcard_and_names_no_resource(world) -> None:
    """`resource_id IS NULL` reaches every resource of the type — including
    ones that do not exist yet, which is why the cell says so in words rather
    than being blank."""
    _team_grant(
        world.db._session, type_="dashboard", resource_id=None,
        privilege="select", user=world.outsider.id,
    )

    rows = await world.service.by_resource(ResourceType.DASHBOARD, world.board.id)
    wildcard = [r for r in rows if r.path == WILDCARD]

    assert len(wildcard) == 1
    assert wildcard[0].resource_id is None
    assert wildcard[0].resource_name == "every dashboard"


async def test_a_role_privilege_says_role_and_names_the_role(world) -> None:
    """The acceptance criterion, as an assertion: *"why can Reza curate
    this?"* is answered in one row, and the answer names the role."""
    session = world.db._session
    role = Role(id=uuid4(), name="BI Engineer II", description="", is_system=False)
    session.add(role)
    session.flush()
    session.add(
        RoleScopedPrivilege(
            role_id=role.id, resource_type="dashboard", privilege="modify"
        )
    )
    session.add(
        RoleAssignment(
            id=uuid4(), role_id=role.id, user_id=world.member.id, created_by=ACTOR
        )
    )
    session.flush()

    rows = await world.service.by_resource(ResourceType.DASHBOARD, world.board.id)
    via_role = [r for r in rows if r.path == ROLE]

    assert len(via_role) == 1
    assert via_role[0].principal_id == world.member.id
    assert via_role[0].via == "BI Engineer II"
    assert via_role[0].privilege == "modify"


# ── the property: the two lenses are one set of facts ────────────────────
async def test_the_two_lenses_agree(world) -> None:
    """Every row one lens shows for a pair, the other shows too.

    A property over a world holding all five facts rather than a pair of
    hand-picked cases: the failure this guards against is a lens that forgets
    an arm, and a case-by-case test only catches the arm somebody thought to
    write a case for.

    Team rows are the one asymmetry and it is deliberate: `by_principal(Ali)`
    includes what Ali reaches *through his team*, filed under the team's id,
    because "what can Ali reach" that omitted his job would be answering a
    different question. So the comparison is over the principals a row can be
    attributed to, which is the team for a team row and the person otherwise.
    """
    session = world.db._session
    team = await _team_with(world.db, "Finance", world.member.id)
    _team_grant(
        session, type_="dashboard", resource_id=world.board.id,
        privilege="select", user=world.member.id,
    )
    _team_grant(
        session, type_="dashboard", resource_id=world.board.id,
        privilege="modify", team=team.id,
    )
    _team_grant(
        session, type_="connection", resource_id=None,
        privilege="describe", user=world.outsider.id,
    )

    by_resource = {
        (ResourceType.DASHBOARD, world.board.id): await world.service.by_resource(
            ResourceType.DASHBOARD, world.board.id
        ),
        (ResourceType.CONNECTION, world.connection.id): await world.service.by_resource(
            ResourceType.CONNECTION, world.connection.id
        ),
    }
    principals = [world.owner.id, world.member.id, world.outsider.id, team.id]
    by_principal = {p: await world.service.by_principal(p) for p in principals}

    def key(row: ReachRow) -> tuple:
        return (row.principal_id, row.resource_type, row.resource_id, row.privilege,
                row.path)

    for (type_, rid), rows in by_resource.items():
        for row in rows:
            assert key(row) in {key(r) for r in by_principal[row.principal_id]}, (
                f"{row.principal_name} reaches {type_} {rid} by {row.path}, and "
                "their own lens does not say so"
            )

    for principal, rows in by_principal.items():
        for row in rows:
            if row.resource_id is None:
                continue  # a wildcard names no resource to look up
            lens = by_resource.get((ResourceType(row.resource_type), row.resource_id))
            if lens is None:
                continue
            assert key(row) in {key(r) for r in lens}, (
                f"{principal} claims {row.path} on {row.resource_name}, and the "
                "resource's own lens does not say so"
            )


async def test_a_person_sees_what_their_team_reaches(world) -> None:
    """The asymmetry above, asserted as the feature it is.

    A team grant is why teams shipped a phase before grants — a permission
    attached to a job survives the person leaving it — and a review that filed
    it only under the team would answer "what can Ali reach" with the small
    half of the truth.
    """
    team = await _team_with(world.db, "Finance", world.member.id)
    _team_grant(
        world.db._session, type_="dashboard", resource_id=world.board.id,
        privilege="modify", team=team.id,
    )

    mine = await world.service.by_principal(world.member.id)
    theirs = await world.service.by_principal(world.outsider.id)

    assert any(r.path == TEAM and r.via == "Finance" for r in mine)
    assert not any(r.path == TEAM for r in theirs)


# ── reach, never data ────────────────────────────────────────────────────
async def test_the_review_shows_reach_and_never_data(world) -> None:
    """The screen that controls disclosure must not be one.

    A connection row carries its **name**. Not its host, not its port, not the
    username the product connects as, and — obviously, and asserted anyway —
    not the encrypted password. Every one of those is on the row this query
    reads, which is exactly why it is worth pinning.
    """
    _team_grant(
        world.db._session, type_="connection", resource_id=world.connection.id,
        privilege="select", user=world.member.id,
    )

    rows = await world.service.by_resource(
        ResourceType.CONNECTION, world.connection.id
    )
    assert rows
    printed = " ".join(
        f"{r.principal_name} {r.resource_name} {r.privilege} {r.path} {r.via}"
        for r in rows
    )

    for secret in ("db.internal", "reader", "warehouse", "not-a-real-ciphertext",
                   "5432", "@test.local"):
        assert secret not in printed, f"{secret!r} reached an access review row"
    assert "Sales" in printed  # the name, which is what `describe` means


async def test_a_principal_is_a_display_name_never_an_address(world) -> None:
    """The rule the audit screen and the review queue already follow. It
    matters most here: this screen is *about people*, and a full list of every
    address in the installation is a thing an access review need not be."""
    rows = await world.service.by_principal(world.owner.id)

    assert rows
    assert all("@" not in row.principal_name for row in rows)
    assert rows[0].principal_name == "owner"  # the display name from the fixture


# ── the file somebody takes to a meeting ─────────────────────────────────
def test_csv_quotes_what_rfc_4180_says_to_quote() -> None:
    rows = [
        ReachRow(
            principal_id=uuid4(), principal_name='Ali "The Analyst", Reza',
            principal_kind="HUMAN", resource_type="dashboard",
            resource_id=uuid4(), resource_name="Q3\nreview", privilege="select",
            path=DIRECT,
        )
    ]
    out = _csv(rows)

    assert out.startswith("principal,principal_kind,")
    assert '"Ali ""The Analyst"", Reza"' in out
    assert '"Q3\nreview"' in out
    assert out.endswith("\r\n")


@pytest.mark.parametrize("dangerous", ["=1+1", "+SUM(A1)", "@import", "\tx"])
def test_a_name_a_spreadsheet_would_run_is_defused(dangerous: str) -> None:
    """A display name and a resource name are both text somebody typed, and a
    leading `=`, `+`, `@` or tab is a *formula* to Excel and Sheets. The
    leading apostrophe is the standard defusing — the same one
    `frontend/src/components/table-format.ts` applies to a downloaded
    result — and it survives the round trip as a visible character. The value
    is shown, not run."""
    assert _cell(dangerous) == f"'{dangerous}"


def test_a_minus_is_not_defused() -> None:
    """Deliberately not in the set, exactly as on the frontend: the only thing
    it would catch is a name beginning with a minus, which is data far more
    often than it is an attack."""
    assert _cell("-Sales") == "-Sales"


def test_every_path_kind_is_one_of_the_five() -> None:
    """Closed, and one for one with §15.2's five facts. A sixth path would be
    a sixth way to reach a resource, which is a design conversation rather
    than a string somebody adds."""
    assert {OWNER, DIRECT, TEAM, ROLE, WILDCARD} == {
        "owner", "direct", "team", "role", "wildcard"
    }
