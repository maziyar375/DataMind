"""Grants: the phase that changes what the product is, and the guards on it.

Everything before Phase 6 was vocabulary and shape. This is the first table
that can say *"Sara may read the Finance warehouse and nothing else"*, and the
tests below are ordered by how much it would cost to get each one wrong:

* **The lattice works end to end.** A `modify` holder passes a `select` check
  without four rows having been written, because the lattice is expanded at
  **read** time. If that ever stopped being true, every grant in the
  installation would need a backfill to keep meaning what it meant.
* **A team grant reaches a member and stops when they leave.** The reason teams
  shipped before grants: per-user shares do not survive staff turnover.
* **`manage` is not implied by `modify`.** Anybody who can fix a password must
  not thereby be able to hand the database out.
* **The four refusals**: a wildcard without `role.manage`, self-revocation of
  the last `manage`, a disclosure widen by a `modify` holder, and deleting a
  principal who still owns something.
* **404 with nothing, 403 with `describe`** — and the 403 names the privilege.
  Getting this backwards turns every list endpoint into an existence oracle;
  getting it *inconsistent* is worse, because a caller who sees both has
  learned the resource exists.
* **`owner_only` is still a working rollback.** The same world, the same
  grants, and a principal who was shared something reaches nothing — which is
  what makes flipping `AUTHZ_BACKEND` back a rollback rather than a hope.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.context import RequestContext
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.ports.authz import Everything, ResourceRef, Subquery
from app.domain.value_objects.authz import (
    Capability,
    Privilege,
    ResourceType,
)
from app.infra.authz.compose import restrict
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.infra.authz.rbac import RbacAuthorizer
from app.infra.db.models import AuditLog, DatabaseConnection, Grant
from app.services.grant_service import (
    GRANT_CREATED,
    GRANT_REVOKED,
    GRANT_WILDCARD_CREATED,
    OWNERSHIP_TRANSFERRED,
    GrantService,
    owned_resources,
)
from app.services.policy import require
from app.services.role_service import RoleService
from app.services.team_service import TeamService
from tests.unit.conftest import AsyncSessionShim, _connection, _user
from tests.unit.conftest import ctx as admin_ctx


def who(user_id, *capabilities: Capability) -> RequestContext:
    """A principal with no capabilities unless the test names some.

    Fail-closed by default, which is what a context built by hand should look
    like: `role.manage` has to be *given* for the wildcard tests, so a wildcard
    that started working without it would fail here rather than pass quietly.
    """
    return RequestContext(
        user_id=user_id,
        email="u@test.local",
        role="MEMBER",
        capabilities=frozenset(capabilities),
    )


def in_teams(base: RequestContext, *team_ids) -> RequestContext:
    """The same principal, as `get_ctx` would build them after a team read."""
    from dataclasses import replace

    return replace(base, team_ids=frozenset(team_ids))


@pytest.fixture
def world(db: AsyncSessionShim):
    """An owner, a stranger and one connection. Every test starts here.

    A named tuple would be tidier and less readable at the call site; three
    attributes on a throwaway object is what these tests actually use.
    """

    class _World:
        def __init__(self) -> None:
            self.owner = _user(db._session, "owner@test.local")
            self.other = _user(db._session, "other@test.local")
            self.connection = _connection(db._session, owner_id=self.owner.id)
            self.authz = RbacAuthorizer(db)
            self.grants = GrantService(db, self.authz)

        @property
        def ref(self) -> ResourceRef:
            return ResourceRef.to(ResourceType.CONNECTION, self.connection)

        @property
        def owner_ctx(self) -> RequestContext:
            return who(self.owner.id)

        @property
        def other_ctx(self) -> RequestContext:
            return who(self.other.id)

    return _World()


# ── the lattice, end to end ──────────────────────────────────────────────
async def test_a_modify_grant_passes_a_select_check(world) -> None:
    """The lattice, expanded at **read** time and never at write.

    One row is written — `modify` — and four questions answer yes. That is why
    changing the lattice never needs a backfill, and why nobody has to remember
    to write `select` beside every `modify`.
    """
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MODIFY, user_id=world.other.id,
    )

    for privilege in (Privilege.DESCRIBE, Privilege.SELECT, Privilege.MODIFY):
        assert await world.authz.allowed(world.other_ctx, world.ref, privilege), (
            f"a modify grant should satisfy {privilege}"
        )


async def test_a_modify_grant_does_not_pass_delete_or_manage(world) -> None:
    """Upwards, the lattice does not travel — and `manage` is the one that
    matters. Somebody who can fix this connection's password must not thereby
    be able to hand the database out to anybody else."""
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MODIFY, user_id=world.other.id,
    )

    for privilege in (Privilege.DELETE, Privilege.MANAGE):
        assert not await world.authz.allowed(world.other_ctx, world.ref, privilege)


async def test_exactly_one_row_is_written(world, db: AsyncSessionShim) -> None:
    """A `manage` grant is one row, not five.

    Expanding at write time would mean a grant made today carries a lattice
    that may have changed by the time it is read — and revoking it would mean
    deleting rows nobody remembers were implied.
    """
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MANAGE, user_id=world.other.id,
    )
    rows = db._session.execute(sa.select(Grant)).scalars().all()
    assert len(rows) == 1
    assert rows[0].privilege == "manage"

    # And all five questions still answer yes.
    for privilege in Privilege:
        assert await world.authz.allowed(world.other_ctx, world.ref, privilege)


async def test_ownership_confers_the_whole_lattice_with_no_row(world) -> None:
    """Ownership is not a grant (§14.1) and never becomes one.

    An owner holds every privilege because they own it, so there is nothing to
    revoke and nothing to expire — which is why transfer exists as a separate
    operation rather than as "revoke and re-grant".
    """
    assert await world.authz.privileges_on(world.owner_ctx, world.ref) == frozenset(
        Privilege
    )
    assert await world.authz.allowed(world.owner_ctx, world.ref, Privilege.MANAGE)


# ── teams ────────────────────────────────────────────────────────────────
async def test_a_team_grant_reaches_a_member_and_stops_when_they_leave(
    world, db: AsyncSessionShim
) -> None:
    """**The reason teams shipped before grants.**

    Per-user shares do not survive staff turnover: a workspace where every
    share was made to a person accumulates permissions nobody can attribute and
    nobody dares revoke. A share made to a team stops reaching somebody the
    moment they leave it, with no second action by whoever made the share.
    """
    teams = TeamService(db)
    team = await teams.create(admin_ctx(), name="Finance")
    await teams.add_member(admin_ctx(), team_id=team.id, user_id=world.other.id)

    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, team_id=team.id,
    )

    member = in_teams(world.other_ctx, team.id)
    assert await world.authz.allowed(member, world.ref, Privilege.SELECT)

    # They leave. The grant row is untouched; what changed is who it reaches.
    await teams.remove_member(admin_ctx(), team_id=team.id, user_id=world.other.id)
    assert await teams.team_ids(world.other.id) == frozenset()
    assert not await world.authz.allowed(world.other_ctx, world.ref, Privilege.SELECT)
    assert db._session.execute(sa.select(sa.func.count()).select_from(Grant)).scalar() == 1


async def test_a_team_grant_and_a_direct_grant_union(world, db: AsyncSessionShim) -> None:
    """Both arms in one `WHERE`, exactly as capability resolution does it.

    A principal reached by a direct `select` and a team `manage` holds `manage`
    — the union of what reaches them, never the minimum.
    """
    teams = TeamService(db)
    team = await teams.create(admin_ctx(), name="Platform")
    await teams.add_member(admin_ctx(), team_id=team.id, user_id=world.other.id)

    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MANAGE, team_id=team.id,
    )

    member = in_teams(world.other_ctx, team.id)
    assert await world.authz.privileges_on(member, world.ref) == frozenset(Privilege)


# ── wildcards ────────────────────────────────────────────────────────────
async def test_a_wildcard_grant_is_refused_without_role_manage(world) -> None:
    """`resource_id IS NULL` reaches things that do not exist yet.

    No resource-scoped permission could authorise that, which is why it takes
    the role-shaped capability instead — and why it is refused with a sentence
    naming the alternative rather than a bare 403.
    """
    with pytest.raises(ValidationError, match="role.manage"):
        await world.grants.grant_wildcard(
            world.owner_ctx,
            ResourceType.CONNECTION,
            privilege=Privilege.SELECT,
            user_id=world.other.id,
        )


async def test_a_wildcard_grant_with_role_manage_reaches_everything(
    world, db: AsyncSessionShim
) -> None:
    """Including resources created **after** the grant was made.

    That is what makes it a wildcard rather than a bulk share, and why the
    second assertion below matters more than the first.
    """
    granter = who(world.owner.id, Capability.ROLE_MANAGE)
    await world.grants.grant_wildcard(
        granter,
        ResourceType.CONNECTION,
        privilege=Privilege.SELECT,
        user_id=world.other.id,
    )

    assert await world.authz.allowed(world.other_ctx, world.ref, Privilege.SELECT)

    later = _connection(db._session, owner_id=world.owner.id, name="built-later")
    assert await world.authz.allowed(
        world.other_ctx,
        ResourceRef.to(ResourceType.CONNECTION, later),
        Privilege.SELECT,
    )


async def test_a_wildcard_short_circuits_visible_to_everything(world) -> None:
    """Step 0: no clause at all, not an `IN (SELECT …)` over every id.

    `Everything` is what `restrict` turns into *nothing*, so the common
    administrator and Knowledge Manager cases cost one small indexed read and
    then no join. A `Subquery` here would be correct and slow, and the
    slowness would only show up on the screens that list the most.
    """
    granter = who(world.owner.id, Capability.ROLE_MANAGE)
    await world.grants.grant_wildcard(
        granter,
        ResourceType.CONNECTION,
        privilege=Privilege.SELECT,
        user_id=world.other.id,
    )

    visible = await world.authz.visible(
        world.other_ctx, ResourceType.CONNECTION, Privilege.SELECT
    )
    assert isinstance(visible, Everything)
    assert "wildcard" in visible.because

    statement = sa.select(DatabaseConnection.id)
    assert (
        str(restrict(statement, DatabaseConnection.id, visible))
        == str(statement)
    ), "Everything must add no clause at all"


async def test_a_wildcard_gets_its_own_audit_action(world, db: AsyncSessionShim) -> None:
    """It must not hide among ordinary shares in a log somebody is scanning."""
    granter = who(world.owner.id, Capability.ROLE_MANAGE)
    await world.grants.grant_wildcard(
        granter,
        ResourceType.CONNECTION,
        privilege=Privilege.SELECT,
        user_id=world.other.id,
    )
    assert _audit(db, GRANT_WILDCARD_CREATED)
    assert not _audit(db, GRANT_CREATED)


async def test_a_role_scoped_privilege_and_a_wildcard_grant_are_told_apart(
    world, db: AsyncSessionShim
) -> None:
    """Identical effect, completely different provenance.

    An access review that could not tell *"because of the Knowledge Manager
    role"* from *"because somebody made a wildcard grant on 3 March"* would be
    answering a question nobody asked — the first is a job description, the
    second is a decision somebody made on a Tuesday.
    """
    role = await RoleService(db).by_name("Knowledge Manager")
    assert role is not None
    await RoleService(db).assign(admin_ctx(), user_id=world.other.id, role_id=role.id)

    visible = await world.authz.visible(
        world.other_ctx, ResourceType.KNOWLEDGE, Privilege.MANAGE
    )
    assert isinstance(visible, Everything)
    assert visible.because == ("via_role",)


# ── visible ──────────────────────────────────────────────────────────────
async def test_visible_unions_what_is_owned_with_what_is_granted(
    world, db: AsyncSessionShim
) -> None:
    """One subquery folded into the caller's own `SELECT`, never a list of ids.

    Materialising here is the pagination bug the whole port exists to prevent:
    a list endpoint that filtered in Python would return a page short by
    however many rows it dropped.
    """
    mine = _connection(db._session, owner_id=world.other.id, name="mine")
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )

    visible = await world.authz.visible(
        world.other_ctx, ResourceType.CONNECTION, Privilege.SELECT
    )
    assert isinstance(visible, Subquery)

    rows = db._session.execute(
        restrict(sa.select(DatabaseConnection.id), DatabaseConnection.id, visible)
    ).scalars().all()
    assert set(rows) == {mine.id, world.connection.id}


async def test_visible_excludes_what_only_a_lower_privilege_reaches(
    world, db: AsyncSessionShim
) -> None:
    """`describe` does not answer a demand for `select`.

    The floor is load-bearing: a Data Engineer sees every connection exists and
    still cannot ask a question through one.
    """
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.DESCRIBE, user_id=world.other.id,
    )

    for privilege, expected in (
        (Privilege.DESCRIBE, {world.connection.id}),
        (Privilege.SELECT, set()),
    ):
        visible = await world.authz.visible(
            world.other_ctx, ResourceType.CONNECTION, privilege
        )
        rows = db._session.execute(
            restrict(sa.select(DatabaseConnection.id), DatabaseConnection.id, visible)
        ).scalars().all()
        assert set(rows) == expected, f"at {privilege}"


# ── revoke, and the last-manager guard ───────────────────────────────────
async def test_revoking_a_grant_takes_effect_immediately(world) -> None:
    """The next request, not in fifteen minutes.

    The property the whole design is sold on, and it is a property of reading
    the database on every call rather than caching anything.
    """
    grant = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    assert await world.authz.allowed(world.other_ctx, world.ref, Privilege.SELECT)

    await world.grants.revoke(world.owner_ctx, world.ref, grant.id)
    assert not await world.authz.allowed(world.other_ctx, world.ref, Privilege.SELECT)


async def test_revoking_your_own_last_manage_on_an_unowned_thing_is_refused(
    world, db: AsyncSessionShim
) -> None:
    """The sibling of the last-administrator guard — and it fires on a **team**.

    Every *owned* table in this schema declares `owner_id NOT NULL`, and
    ownership confers the whole lattice, so a connection can never be stranded
    by a revoke: its owner is always a manager. `ResourceType.TEAM` has no
    owner column, so a team's `manage` is entirely by grant, and it is the type
    this guard actually protects today.

    That is worth asserting on the type where it bites rather than contriving
    an ownerless connection the database would refuse to store.
    """
    team = await TeamService(db).create(admin_ctx(), name="Finance")
    ref = ResourceRef(type=ResourceType.TEAM, id=team.id)

    grant = Grant(
        id=uuid4(),
        resource_type=str(ResourceType.TEAM),
        resource_id=team.id,
        user_id=world.other.id,
        privilege=str(Privilege.MANAGE),
    )
    db._session.add(grant)
    db._session.flush()

    with pytest.raises(ValidationError, match="only way anybody can manage"):
        await world.grants.revoke(world.other_ctx, ref, grant.id)

    # And it is not a blanket refusal: a second manager makes it allowed.
    second = Grant(
        id=uuid4(),
        resource_type=str(ResourceType.TEAM),
        resource_id=team.id,
        user_id=world.owner.id,
        privilege=str(Privilege.MANAGE),
    )
    db._session.add(second)
    db._session.flush()
    await world.grants.revoke(world.other_ctx, ref, grant.id)


async def test_an_owned_resource_can_never_be_stranded_by_a_revoke(
    world, db: AsyncSessionShim
) -> None:
    """Why the guard above needs no equivalent on a connection.

    `owner_id` is `NOT NULL` on every owned table, so there is always a
    principal holding the whole lattice. A test rather than a comment, because
    the guard's shape depends on it and a migration making a column nullable
    would otherwise silently open the gap.
    """
    from app.infra.authz.owner_only import _OWNED_TABLES

    for table in {t.__tablename__: t for t in _OWNED_TABLES.values()}.values():
        column = table.__table__.c.owner_id
        assert not column.nullable, f"{table.__tablename__}.owner_id became nullable"


async def test_revoking_your_own_redundant_manage_is_allowed(world) -> None:
    """An **owner** holds the whole lattice regardless of any grant.

    So a `manage` row granted to the owner is redundant, and dropping it
    changes nothing — a guard that refused it would be protecting against a
    state that cannot occur.
    """
    grant = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MANAGE, user_id=world.owner.id,
    )
    await world.grants.revoke(world.owner_ctx, world.ref, grant.id)
    assert await world.authz.allowed(world.owner_ctx, world.ref, Privilege.MANAGE)


async def test_revoking_somebody_elses_last_manage_is_allowed(world) -> None:
    """Only the *self* case is guarded, deliberately.

    Whoever is doing the revoking still holds `manage` — they got past the gate
    — so the resource is never left unmanageable. Guarding this case too would
    make an administrator unable to clean up after a departing colleague.
    """
    grant = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MANAGE, user_id=world.other.id,
    )
    await world.grants.revoke(world.owner_ctx, world.ref, grant.id)
    assert not await world.authz.allowed(world.other_ctx, world.ref, Privilege.MANAGE)


async def test_revoking_a_grant_that_belongs_to_another_resource_is_a_404(
    world, db: AsyncSessionShim
) -> None:
    """The path is not trusted for the relationship it asserts."""
    second = _connection(db._session, owner_id=world.owner.id, name="second")
    grant = await world.grants.grant(
        world.owner_ctx,
        ResourceRef.to(ResourceType.CONNECTION, second),
        privilege=Privilege.SELECT,
        user_id=world.other.id,
    )
    with pytest.raises(NotFoundError):
        await world.grants.revoke(world.owner_ctx, world.ref, grant.id)


async def test_granting_needs_manage_not_modify(world) -> None:
    """§14, as the refusal it exists to produce.

    A `modify` holder can re-credential this connection and re-sync its schema.
    They cannot hand it to somebody else — and because they hold `describe`
    through the lattice, they get a **403 naming the privilege** rather than a
    404, which is the other half of the rule.
    """
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MODIFY, user_id=world.other.id,
    )
    with pytest.raises(ForbiddenError, match="manage"):
        await world.grants.grant(
            world.other_ctx, world.ref,
            privilege=Privilege.SELECT, user_id=world.owner.id,
        )


# ── the 404/403 rule ─────────────────────────────────────────────────────
async def test_nothing_at_all_is_a_404(world) -> None:
    """Indistinguishable from a typo, and deliberately not audited.

    A 404 that was audited would fill the log with noise nobody can act on,
    because most of them are somebody mistyping a URL.
    """
    with pytest.raises(NotFoundError):
        await require(world.other_ctx, world.authz, world.ref, Privilege.SELECT)


async def test_describe_and_not_enough_is_a_403_naming_the_privilege(
    world,
) -> None:
    """The other half, and the message is the point.

    "You need select on this data source: ask questions through it" is
    something a person can take to whoever owns it. "Forbidden" is not — and
    the sentence comes from the same `PRIVILEGE_MEANINGS` table the share
    dialog labels its radio buttons from, so the two cannot drift.
    """
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.DESCRIBE, user_id=world.other.id,
    )

    with pytest.raises(ForbiddenError) as caught:
        await require(world.other_ctx, world.authz, world.ref, Privilege.SELECT)

    message = caught.value.message
    assert "select" in message
    assert "Ask questions through it" in message or "ask questions" in message.lower()
    assert "describe" in message  # what they do hold

    # And the same facts in one structured object, which is what the "Why can
    # I not see this?" popover renders. Phase 9 replaced three flat keys with
    # `reason`, so the prose above and the structure below are the same
    # components and cannot drift.
    reason = caught.value.detail["reason"]
    assert reason["needed"] == "select"
    assert reason["held"] == ["describe"]
    assert reason["resource_type"] == "connection"
    assert reason["noun"] == "data source"
    # The sentence lowercases the meaning's first letter to read as one clause;
    # the structure keeps it as written. Same words either way.
    assert reason["meaning"].lower() in message.lower()


async def test_the_two_answers_are_the_same_sentence_for_a_missing_row(
    world, db: AsyncSessionShim
) -> None:
    """A resource out of reach and one that does not exist read identically.

    If they differed, the pair of responses would be an existence oracle: ask
    for an id, and the *shape* of the refusal tells you whether it is real.
    """
    missing = ResourceRef(type=ResourceType.CONNECTION, id=uuid4())
    with pytest.raises(NotFoundError) as absent:
        await require(world.other_ctx, world.authz, missing, Privilege.SELECT)
    with pytest.raises(NotFoundError) as unreachable:
        await require(world.other_ctx, world.authz, world.ref, Privilege.SELECT)

    assert absent.value.message == unreachable.value.message


# ── transfer, and deleting a principal ───────────────────────────────────
async def test_ownership_transfer_moves_the_row_and_audits_it(
    world, db: AsyncSessionShim
) -> None:
    await world.grants.transfer(world.owner_ctx, world.ref, to=world.other.id)

    db._session.refresh(world.connection)
    assert world.connection.owner_id == world.other.id
    rows = _audit(db, OWNERSHIP_TRANSFERRED)
    assert len(rows) == 1
    assert rows[0].detail["to"] == str(world.other.id)

    # The old owner keeps nothing. If they should retain access, that is a
    # grant — one row somebody can see — rather than a residue of a transfer.
    assert not await world.authz.allowed(world.owner_ctx, world.ref, Privilege.SELECT)


async def test_transferring_to_a_disabled_account_is_refused(
    world, db: AsyncSessionShim
) -> None:
    """A resource owned by an account nobody can sign in as has no owner.

    That is exactly the state the deletion guard exists to prevent, so it must
    not be reachable by this other route either.
    """
    world.other.status = "DISABLED"
    db._session.flush()
    with pytest.raises(ValidationError, match="not active"):
        await world.grants.transfer(world.owner_ctx, world.ref, to=world.other.id)


async def test_deleting_a_principal_who_owns_something_is_refused_naming_it(
    world, db: AsyncSessionShim
) -> None:
    """*"Transfer these four things first"* is a ticket somebody can act on.

    And the stake is higher than an orphaned row. Every owned table declares
    `owner_id NOT NULL ON DELETE CASCADE`, so deleting the principal does not
    leave their work ownerless — it **deletes their work**, including
    dashboards other people had been granted. Nothing warns and nothing can be
    undone, which is why this refusal exists rather than a cleanup job.
    """
    owned = await owned_resources(db, world.owner.id)
    assert len(owned) == 1
    assert owned[0].startswith("connection “")
    assert await owned_resources(db, world.other.id) == []


async def test_the_refusal_names_every_kind_of_thing_they_own(
    world, db: AsyncSessionShim
) -> None:
    """Not only connections — a conversation is named by its `title`.

    The column is not `name` everywhere, and a loop that guessed would raise on
    the one table it could not read: a 500 in place of a refusal somebody can
    act on.
    """
    from app.infra.db.models import Conversation

    db._session.add(
        Conversation(id=uuid4(), owner_id=world.owner.id, title="Q3 review")
    )
    db._session.flush()

    owned = await owned_resources(db, world.owner.id)
    assert any(name.startswith("conversation “Q3 review”") for name in owned)


async def test_a_connection_is_named_once_not_three_times(
    world, db: AsyncSessionShim
) -> None:
    """The derived types share the connection's row and must not double-count.

    `_OWNED_TABLES` maps `knowledge` and `semantic_layer` to
    `database_connections`, so a naive loop would make the refusal read as if
    there were three things to move rather than one.
    """
    names = await owned_resources(db, world.owner.id)
    assert len(names) == 1
    assert not any("knowledge" in name for name in names)


# ── the delete hook and the sweep ────────────────────────────────────────
async def test_deleting_a_resource_revokes_its_grants(world, db: AsyncSessionShim) -> None:
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    removed = await world.grants.revoke_all_for(
        ResourceType.CONNECTION, world.connection.id
    )
    assert removed == 1
    assert db._session.execute(sa.select(sa.func.count()).select_from(Grant)).scalar() == 0


async def test_the_sweep_removes_only_orphans(world, db: AsyncSessionShim) -> None:
    """Hygiene, not a security fix — and it must not take anything else.

    An orphaned grant is inert: it names an id no row has, so every join drops
    it. What it is not is invisible — it would appear in an access review as
    reach nobody can account for.
    """
    from app.workers.reconciler import sweep_orphaned_grants

    live = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    orphan = Grant(
        id=uuid4(),
        resource_type=str(ResourceType.CONNECTION),
        resource_id=uuid4(),  # names nothing
        user_id=world.other.id,
        privilege=str(Privilege.SELECT),
    )
    wildcard = Grant(
        id=uuid4(),
        resource_type=str(ResourceType.CONNECTION),
        resource_id=None,  # names no resource, so none of its can be missing
        user_id=world.other.id,
        privilege=str(Privilege.SELECT),
    )
    db._session.add_all([orphan, wildcard])
    db._session.flush()

    assert await sweep_orphaned_grants(db) == 1

    survivors = {
        row.id for row in db._session.execute(sa.select(Grant)).scalars()
    }
    assert survivors == {live.id, wildcard.id}


# ── the rollback ─────────────────────────────────────────────────────────
async def test_owner_only_ignores_every_grant(world, db: AsyncSessionShim) -> None:
    """`AUTHZ_BACKEND=owner_only` restores the previous behaviour **exactly**.

    Same world, same rows, and a principal who was granted `manage` reaches
    nothing. That is what makes the flip a rollback rather than a hope — and
    it works in both directions, because no grant row is read under
    `owner_only` and running under it creates none.
    """
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MANAGE, user_id=world.other.id,
    )
    assert await world.authz.allowed(world.other_ctx, world.ref, Privilege.MANAGE)

    rolled_back = OwnerOnlyAuthorizer(db)
    assert not await rolled_back.allowed(world.other_ctx, world.ref, Privilege.DESCRIBE)
    assert await rolled_back.allowed(world.owner_ctx, world.ref, Privilege.MANAGE)


def test_the_factory_picks_the_implementation_from_the_setting(
    db: AsyncSessionShim,
) -> None:
    """And an unrecognised value falls to the **narrower** of the two.

    A typo in an environment variable should cost people access to things they
    were shared, not hand out access nobody granted.
    """
    from app.core.config import get_settings
    from app.infra.authz.factory import build_authorizer

    settings = get_settings()
    assert settings.authz_backend == "rbac", "Phase 6 flips the default"
    assert isinstance(build_authorizer(db, settings), RbacAuthorizer)
    assert isinstance(
        build_authorizer(db, settings.model_copy(update={"authz_backend": "owner_only"})),
        OwnerOnlyAuthorizer,
    )


# ── the audit record ─────────────────────────────────────────────────────
async def test_every_grant_and_revoke_leaves_a_row(world, db: AsyncSessionShim) -> None:
    grant = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    created = _audit(db, GRANT_CREATED)
    assert len(created) == 1
    assert created[0].detail["privilege"] == "select"
    assert created[0].detail["principal_id"] == str(world.other.id)

    await world.grants.revoke(world.owner_ctx, world.ref, grant.id)
    assert len(_audit(db, GRANT_REVOKED)) == 1


async def test_granting_twice_is_idempotent_and_writes_one_row(
    world, db: AsyncSessionShim
) -> None:
    """Pressing a control that is already on is not an error.

    The alternative makes every client implement "check, then set" against a
    race with another administrator — and the honest answer to "what happened"
    is the state, not a 409.
    """
    first = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    second = await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    assert first.id == second.id
    assert len(_audit(db, GRANT_CREATED)) == 1


# ── the share surface's one type-specific refusal ────────────────────────
async def test_modify_on_an_llm_config_cannot_be_granted(world, db: AsyncSessionShim) -> None:
    """`modify` on a model configuration is equivalent to disclosing the key.

    A holder can repoint `base_url` at a host they control and read the key out
    of the next request's `Authorization` header. So the API refuses the three
    key-equivalent privileges outright: the owner reaches them by owning it,
    and an administrator through the explicit self-grant path — both of which
    leave a row.
    """
    from app.infra.db.models import LlmConfig

    config = LlmConfig(
        id=uuid4(), owner_id=world.owner.id, name="gpt",
        provider="openai", model="gpt-4o",
    )
    db._session.add(config)
    db._session.flush()
    ref = ResourceRef.to(ResourceType.LLM_CONFIG, config)

    for privilege in (Privilege.MODIFY, Privilege.DELETE, Privilege.MANAGE):
        with pytest.raises(ValidationError, match="API key"):
            await world.grants.grant(
                world.owner_ctx, ref, privilege=privilege, user_id=world.other.id
            )

    # And the two that are safe to share still are.
    for privilege in (Privilege.DESCRIBE, Privilege.SELECT):
        await world.grants.grant(
            world.owner_ctx, ref, privilege=privilege, user_id=world.other.id
        )


# ── who can reach this ───────────────────────────────────────────────────
async def test_the_access_list_shows_the_path_and_the_owner_first(
    world, db: AsyncSessionShim
) -> None:
    """The path is what makes this list worth having.

    *"Sara — select"* is a fact nobody can act on; *"Sara — select, via the
    Finance team"* tells them the revoke they want is on the team, and that
    clicking revoke on Sara's row would do nothing.
    """
    teams = TeamService(db)
    team = await teams.create(admin_ctx(), name="Finance")
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.MODIFY, team_id=team.id,
    )

    views = await world.grants.for_resource(world.owner_ctx, world.ref)
    assert [v.path for v in views] == ["owner", "direct", "team"]
    # Ownership is a path, not a row: there is nothing to revoke.
    assert views[0].grant_id is None
    assert views[2].principal_name == "Finance"
    assert views[2].principal_kind == "TEAM"


async def test_listing_who_can_reach_it_needs_manage(world) -> None:
    """Not `select`: who else has been given access is information about the
    people in the installation, and somebody merely shared a dashboard has no
    business enumerating who else was."""
    await world.grants.grant(
        world.owner_ctx, world.ref,
        privilege=Privilege.SELECT, user_id=world.other.id,
    )
    with pytest.raises(ForbiddenError, match="manage"):
        await world.grants.for_resource(world.other_ctx, world.ref)


def _audit(db: AsyncSessionShim, action: str) -> list[AuditLog]:
    db._session.flush()
    return list(
        db._session.execute(
            sa.select(AuditLog).where(AuditLog.action == action)
        ).scalars()
    )


# ── the describe narrowing ───────────────────────────────────────────────
def test_a_describe_holder_sees_the_policy_and_not_the_host() -> None:
    """Requirement 6's quiet half, and §19.5's rule 1.

    A principal who holds only `describe` on a connection is somebody who was
    shared a *dashboard* whose tiles read through it, or who holds a Data
    Engineer's `(connection, describe)` over the whole installation. They must
    be able to see that it exists — and **what leaves it**, because a grantee
    has to know the disclosure policy before they ask a question through it.

    They must not see the host, port, database name or username. Those four
    together are enough to attempt a connection from anywhere the database is
    reachable, so handing them to somebody who may only know the connection
    exists is handing them the target. The password was never on this model at
    all, which is why the line is drawn at these four rather than at
    "credentials".
    """
    from app.api.schemas import DESCRIBE_HIDDEN, ConnectionRead, narrow_to_describe

    full = ConnectionRead(
        id=uuid4(),
        name="Finance warehouse",
        database_type="postgres",
        host="warehouse.internal",
        port=5432,
        database_name="finance",
        username="analytics_ro",
        ssl_mode="require",
        schema_allowlist=["public"],
        max_rows=1000,
        statement_timeout_ms=30_000,
        disclosure_policy="AGGREGATE",
        status="OK",
    )
    narrowed = narrow_to_describe(full)

    assert narrowed.host == ""
    assert narrowed.port == 0
    assert narrowed.database_name == ""
    assert narrowed.username == ""

    # What a `describe` holder still gets, and each is deliberate.
    assert narrowed.name == "Finance warehouse"
    assert narrowed.database_type == "postgres"
    assert narrowed.disclosure_policy == "AGGREGATE"

    # The original is untouched: it is built from a live ORM row, and blanking
    # fields on that row would write the blanks back on the next flush.
    assert full.host == "warehouse.internal"
    # A typed empty value per field, not one blank for all four: `model_copy`
    # does not revalidate, so `port` blanked to `""` would put a string on a
    # field the schema declares as a number.
    assert dict(DESCRIBE_HIDDEN) == {
        "host": "", "port": 0, "database_name": "", "username": "",
    }


def test_no_read_model_could_leak_the_password_either_way() -> None:
    """The narrowing is a second line, not the only one.

    `ConnectionRead` has never had a password field and a separate test walks
    the whole generated OpenAPI to keep it that way. Asserting it here as well
    is deliberate: this file is where somebody adding a field to the connection
    payload will be looking.
    """
    from app.api.schemas import ConnectionRead

    fields = set(ConnectionRead.model_fields)
    assert not any(
        word in field.lower()
        for field in fields
        for word in ("password", "secret", "encrypted")
    )
