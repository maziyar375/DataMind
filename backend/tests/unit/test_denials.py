"""The audit half: every refusal is a row, and every 404 is not.

`audit_logs` has had a `DENIED` constant and **no producer** since migration
`0001`. A product whose positioning is *"you decide what leaves your database"*
has to be able to answer *"who tried, and was refused"* — and until Phase 6
there was nothing to refuse, because a resource you could not reach was a
resource you could not see. Grants made the question real; this file is what
makes the answer trustworthy.

Five claims, in the order they would hurt:

* **A 403 writes exactly one row, with a non-empty `because`.** Not "a row" —
  *one*. A denial written twice inflates every count somebody draws from this
  table, and a denial that records no path says nothing more than the HTTP
  status already did.
* **A 404 writes none.** A 404 is indistinguishable from a typo, and a log that
  recorded every mistyped URL would bury the denials somebody is looking for
  under the ones nobody is. This is the rule that makes the table readable.
* **`detail` carries no SQL, no question, no rows, no key.** Rule 3 of
  `services/audit.py`, asserted rather than reviewed — an audit log that
  quietly became a second copy of the store would be a second thing to secure
  and the one nobody remembers.
* **An administrator self-grant writes two rows.** Decision 14: an
  administrator may reach anything and cannot do so silently. One row is the
  grant, which is real and stays; the other says who gave it to themselves.
* **A failing audit write does not fail the action.** Rule 2, and the opposite
  posture to the guard: this observes, it does not authorise.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.context import RequestContext
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import Capability, Privilege, ResourceType
from app.infra.authz.rbac import RbacAuthorizer
from app.infra.db.models import AuditLog, Grant
from app.services import audit
from app.services.grant_service import (
    ADMIN_SELF_GRANTED,
    GRANT_CREATED,
    GrantService,
)
from app.services.policy import ACCESS_DENIED, require
from tests.unit.conftest import AsyncSessionShim, _connection, _team_grant, _user


def ctx(user_id, *, admin: bool = False, teams=frozenset()) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        email="u@test.local" if admin else "MEMBER",
        capabilities=(
            frozenset({Capability.USER_MANAGE}) if admin else frozenset()
        ),
        team_ids=teams,
        correlation_id="t",
    )


def _rows(db: AsyncSessionShim, action: str | None = None) -> list[AuditLog]:
    db._session.flush()
    statement = sa.select(AuditLog)
    if action is not None:
        statement = statement.where(AuditLog.action == action)
    return list(db._session.execute(statement).scalars())


# ── the denial ───────────────────────────────────────────────────────────
async def test_a_403_writes_exactly_one_row_naming_the_privilege(
    db: AsyncSessionShim,
) -> None:
    """The row `DENIED` has been waiting for since migration `0001`.

    One row, not two: a denial counted twice inflates every number somebody
    draws from this table, and *"has this spiked?"* is the only question a
    denial log is really asked.
    """
    owner = _user(db._session, "owner@test.local")
    reader = _user(db._session, "reader@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    _team_grant(
        db._session,
        type_=str(ResourceType.CONNECTION),
        resource_id=connection.id,
        privilege=str(Privilege.DESCRIBE),
        user=reader.id,
    )

    authz = RbacAuthorizer(db)
    ref = ResourceRef.to(ResourceType.CONNECTION, connection)
    with pytest.raises(ForbiddenError):
        await require(ctx(reader.id), authz, ref, Privilege.SELECT, db=db)

    rows = _rows(db, ACCESS_DENIED)
    assert len(rows) == 1
    assert rows[0].outcome == audit.DENIED
    assert rows[0].actor_user_id == reader.id
    assert rows[0].resource_id == connection.id
    assert rows[0].resource_type == str(ResourceType.CONNECTION)
    # What was needed, and what they actually hold.
    assert rows[0].detail["privilege"] == "select"
    assert rows[0].detail["held"] == ["describe"]


async def test_the_denial_records_the_path_that_was_tried(
    db: AsyncSessionShim,
) -> None:
    """`Decision.because` — the whole reason a denial is worth recording.

    *"They hold `describe` on this connection **through the Finance team**, and
    asking needs `select`"* is a sentence somebody can act on: it says which
    grant to widen and where it lives. "Denied" says nothing the HTTP status
    did not.
    """
    from app.services.team_service import TeamService
    from tests.unit.conftest import ctx as admin_ctx

    owner = _user(db._session, "owner@test.local")
    member = _user(db._session, "member@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    teams = TeamService(db)
    team = await teams.create(admin_ctx(), name="Finance")
    await teams.add_member(admin_ctx(), team_id=team.id, user_id=member.id)
    _team_grant(
        db._session,
        type_=str(ResourceType.CONNECTION),
        resource_id=connection.id,
        privilege=str(Privilege.DESCRIBE),
        team=team.id,
    )

    who = ctx(member.id, teams=await teams.team_ids(member.id))
    with pytest.raises(ForbiddenError):
        await require(
            who,
            RbacAuthorizer(db),
            ResourceRef.to(ResourceType.CONNECTION, connection),
            Privilege.SELECT,
            db=db,
        )

    because = _rows(db, ACCESS_DENIED)[0].detail["because"]
    assert because, "a denial with no path says nothing the status code did not"
    assert "via_team" in because


async def test_a_404_writes_nothing_at_all(db: AsyncSessionShim) -> None:
    """**The rule that keeps this table readable.**

    A principal to whom no fact reaches gets 404, and a 404 is indistinguishable
    from a typo. Auditing them would fill the log with mistyped URLs and bury
    the denials somebody is actually looking for — so the not-found branch is
    silent, on purpose, and this asserts the silence rather than trusting it.
    """
    owner = _user(db._session, "owner@test.local")
    stranger = _user(db._session, "stranger@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    with pytest.raises(NotFoundError):
        await require(
            ctx(stranger.id),
            RbacAuthorizer(db),
            ResourceRef.to(ResourceType.CONNECTION, connection),
            Privilege.SELECT,
            db=db,
        )

    assert _rows(db) == []


async def test_an_allowed_request_writes_nothing(db: AsyncSessionShim) -> None:
    """The guard on the guard.

    A `require` that logged every *successful* check would put a row on the
    table for every authenticated request in the product, and the denials would
    be one in ten thousand. `audit.record` is for acts, not for checks.
    """
    owner = _user(db._session, "owner@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    await require(
        ctx(owner.id),
        RbacAuthorizer(db),
        ResourceRef.to(ResourceType.CONNECTION, connection),
        Privilege.MANAGE,
        db=db,
    )
    assert _rows(db) == []


async def test_a_denial_without_a_session_still_refuses(
    db: AsyncSessionShim,
) -> None:
    """`db` is optional, and the refusal is not.

    A caller with no session in hand — a check made before one exists, a unit
    test — gets the same 403 with the same sentence. Making the session
    required would have meant threading one through every call site to buy an
    audit row, and a required argument half the callers fill in with `None` is
    worse than an optional one.
    """
    owner = _user(db._session, "owner@test.local")
    reader = _user(db._session, "reader@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    _team_grant(
        db._session,
        type_=str(ResourceType.CONNECTION),
        resource_id=connection.id,
        privilege=str(Privilege.DESCRIBE),
        user=reader.id,
    )

    with pytest.raises(ForbiddenError):
        await require(
            ctx(reader.id),
            RbacAuthorizer(db),
            ResourceRef.to(ResourceType.CONNECTION, connection),
            Privilege.SELECT,
        )
    assert _rows(db) == []


# ── what a row may carry ─────────────────────────────────────────────────
async def test_a_denial_carries_no_sql_no_question_and_no_key(
    db: AsyncSessionShim,
) -> None:
    """Rule 3 of `services/audit.py`, asserted rather than reviewed.

    An audit log that quietly became a second copy of the store would be a
    second thing to secure and the one place somebody forgets to. The row
    already carries the resource id; whatever it points at is where the content
    lives, behind the same permission check.

    Asserted on the **shape** — every value is an identifier, a count, a
    boolean or a short word — rather than on a list of forbidden substrings,
    because a substring test passes the day somebody adds a field it does not
    know to look for.
    """
    owner = _user(db._session, "owner@test.local")
    reader = _user(db._session, "reader@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    _team_grant(
        db._session,
        type_=str(ResourceType.CONNECTION),
        resource_id=connection.id,
        privilege=str(Privilege.DESCRIBE),
        user=reader.id,
    )

    with pytest.raises(ForbiddenError):
        await require(
            ctx(reader.id),
            RbacAuthorizer(db),
            ResourceRef.to(ResourceType.CONNECTION, connection),
            Privilege.SELECT,
            db=db,
        )

    detail = _rows(db, ACCESS_DENIED)[0].detail
    assert set(detail) == {"privilege", "held", "because"}
    flat = [detail["privilege"], *detail["held"], *detail["because"]]
    for value in flat:
        assert isinstance(value, str)
        # Every word is a privilege or a path — short, closed vocabulary. A
        # statement, a question or a key could not fit and would not match.
        assert len(value) <= 20, f"{value!r} is not a vocabulary word"
        assert " " not in value


def test_the_writer_caps_and_stringifies_whatever_it_is_given() -> None:
    """The backstop, independent of any call site.

    `_clean` is what makes rule 3 an enforced property rather than a discipline
    ten call sites have to remember. A caller that passed a statement gets a
    truncated string rather than a statement in the table.
    """
    from app.services.audit import MAX_DETAIL_CHARS, _clean

    cleaned = _clean({"sql": "SELECT " + "x" * 5000, "n": 3, "ok": True})
    assert len(cleaned["sql"]) == MAX_DETAIL_CHARS
    assert cleaned["n"] == 3 and cleaned["ok"] is True


# ── the administrator self-grant ─────────────────────────────────────────
async def test_a_self_grant_writes_two_rows(db: AsyncSessionShim) -> None:
    """Decision 14, and the reason the authorizer has no administrator arm.

    Every product in this space lets an administrator read anything, and in
    most of them the only evidence is the absence of an error. Here it is two
    rows: the grant, which is what the authorizer then sees, and
    `admin.self_granted`, so *"who gave themselves access to what"* is one
    filter rather than a join somebody has to think of.
    """
    owner = _user(db._session, "owner@test.local")
    admin = _user(db._session, "admin@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    authz = RbacAuthorizer(db)
    ref = ResourceRef.to(ResourceType.CONNECTION, connection)

    who = ctx(admin.id, admin=True)
    assert not await authz.allowed(who, ref, Privilege.SELECT)

    await GrantService(db, authz).self_grant(who, ref, privilege=Privilege.SELECT)

    # One ordinary grant row, which is what makes the access real.
    assert await authz.allowed(who, ref, Privilege.SELECT)
    grants = list(db._session.execute(sa.select(Grant)).scalars())
    assert len(grants) == 1
    assert grants[0].user_id == admin.id

    # And two audit rows, one of which says what actually happened.
    assert len(_rows(db, ADMIN_SELF_GRANTED)) == 1
    created = _rows(db, GRANT_CREATED)
    assert len(created) == 1
    assert created[0].detail["self_granted"] is True


async def test_a_non_administrator_cannot_self_grant(
    db: AsyncSessionShim,
) -> None:
    """The door is for account recovery, not for curiosity.

    Refused with a sentence pointing at the ordinary way to get access, and —
    the part that matters — **no rows**. A refused escalation is not an
    escalation, and recording it as one would make the `admin.self_granted`
    filter useless for the thing it exists to surface.
    """
    owner = _user(db._session, "owner@test.local")
    nosy = _user(db._session, "nosy@test.local")
    connection = _connection(db._session, owner_id=owner.id)

    with pytest.raises(ValidationError, match="administrator"):
        await GrantService(db, RbacAuthorizer(db)).self_grant(
            ctx(nosy.id),
            ResourceRef.to(ResourceType.CONNECTION, connection),
            privilege=Privilege.SELECT,
        )
    assert _rows(db) == []


async def test_there_is_no_silent_path_to_somebody_elses_resource(
    db: AsyncSessionShim,
) -> None:
    """The claim decision 14 rests on, stated as an absence.

    An administrator holds `user.manage` and, through it, every administration
    screen in the product. What they do **not** hold is reach over a connection
    they were not granted — and the only way to get it leaves two rows.
    """
    owner = _user(db._session, "owner@test.local")
    admin = _user(db._session, "admin@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    authz = RbacAuthorizer(db)
    who = ctx(admin.id, admin=True)

    for privilege in Privilege:
        assert not await authz.allowed(
            who, ResourceRef.to(ResourceType.CONNECTION, connection), privilege
        ), f"an administrator silently held {privilege}"
    assert _rows(db) == []


# ── rule 2: observing must not break acting ──────────────────────────────
async def test_a_failing_audit_write_does_not_fail_the_action() -> None:
    """The opposite posture to the guard, and right for the opposite reason.

    A curator saving a template must not lose it to a full disk on the audit
    table: this module *observes*, it does not authorise. The guard fails
    closed; this fails open, and the two are not in tension because they are
    answering different questions.
    """

    class _Refuses:
        def add(self, _row: object) -> None:
            raise RuntimeError("audit table is full")

    row = await audit.record(
        _Refuses(),  # type: ignore[arg-type]
        ctx(uuid4()),
        action="knowledge.template.created",
    )
    assert row is None


async def test_a_denial_that_cannot_be_written_still_denies(
    db: AsyncSessionShim,
) -> None:
    """Rule 2 and the guard, in the one place they meet.

    `require` writes a row *and* raises. If the write fails the raise must
    still happen — a refusal that became an allow because the audit table was
    full would be the worst possible reading of "failing to log never fails the
    action".
    """
    owner = _user(db._session, "owner@test.local")
    reader = _user(db._session, "reader@test.local")
    connection = _connection(db._session, owner_id=owner.id)
    _team_grant(
        db._session,
        type_=str(ResourceType.CONNECTION),
        resource_id=connection.id,
        privilege=str(Privilege.DESCRIBE),
        user=reader.id,
    )

    class _Sabotaged:
        """The real session, with `add` broken — so the reads still work."""

        def __init__(self, inner: AsyncSessionShim) -> None:
            self._inner = inner

        def add(self, _row: object) -> None:
            raise RuntimeError("audit table is full")

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    with pytest.raises(ForbiddenError):
        await require(
            ctx(reader.id),
            RbacAuthorizer(db),
            ResourceRef.to(ResourceType.CONNECTION, connection),
            Privilege.SELECT,
            db=_Sabotaged(db),  # type: ignore[arg-type]
        )
    assert _rows(db) == []


# ── the ask ──────────────────────────────────────────────────────────────
def test_the_ask_action_is_named_where_the_vocabulary_lives() -> None:
    """The remaining half of mvp2 §D4 has a word, and it is in `audit.py`.

    A grep rather than a behavioural test — the ask path itself runs a whole
    pipeline and is covered by the run tests — because the claim here is that
    an administrator reading the log can enumerate what can appear in it
    without reading the routers.
    """
    assert audit.ASK_RECORDED == "ask.recorded"

    from pathlib import Path

    source = Path("app/services/run_service.py").read_text()
    assert "audit.ASK_RECORDED" in source
    assert "disclosure_policy" in source.split("audit.ASK_RECORDED")[1][:600], (
        "the ask row must record the policy in force — that is the whole point"
    )
