"""The three usage routes, and the one of them that is a permission decision.

`test_usage_service.py` proves the arithmetic. This file proves the **scope**:
who each route answers about, and who is allowed to ask. Those are different
failures — an aggregation that is wrong shows a wrong number, and a gate that
is wrong shows the right number to the wrong person — and only the second one
is silent.

Three claims are worth more than the rest:

* **`/usage/me` cannot be widened.** It carries no capability, which is only
  safe because the scope is `ctx.user_id` and no argument reaches it. That is
  asserted against the route's own signature rather than by trying a parameter
  and finding it ignored: a test that tried `?user_id=` would keep passing on
  the day somebody adds the parameter and forgets the gate.
* **The other two refuse, and the refusal names `usage.read`.** A 403 saying
  "forbidden" produces a support ticket; one naming the capability produces an
  action.
* **A row names a person, never an address.** The rule the audit log already
  follows, asserted here because this is the second screen about people and the
  first one to be built after the rule was written down.

The rows are written through the ORM against the real schema, for
`conftest.py`'s reason, and the handlers run their real SQL over them behind an
HTTP client — the route's `Depends` tree is the thing under test, and a direct
call to the handler function would skip exactly the part that refuses.

The client is an **ASGI transport rather than `TestClient`**, which is the one
mechanical thing in this file. `TestClient` drives the app from a worker thread
and the fixture's SQLite connection belongs to the thread that created it, so
every route that reached the database failed with a thread error and every
route that refused before touching it passed — which is the precise shape of a
permissions test that looks green and proves nothing.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import get_type_hints
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa

from app.api import deps
from app.api.v1 import usage as routes
from app.core.context import RequestContext
from app.domain.value_objects.authz import Capability
from app.infra.db.models import Run, User
from app.main import create_app
from app.services import usage_service as usage
from tests.unit.conftest import AsyncSessionShim

# The seeded roles, as the migrations grant them. Read through the same helper
# `test_roles.py` uses so "an Auditor" here means what it means there, and
# `0030`'s grant is not transcribed a second time.
from tests.unit.test_roles import _granted_later, _seeded

# The fixtures that write a run with the foreign keys in the order the schema
# demands. Imported rather than copied: two fixtures writing the same tables
# would be two things to keep in step, and the first one to drift would make
# this file test a world the service tests do not.
from tests.unit.test_usage_service import NOW, _chat, _person

#: A window around the fixtures' instant, passed explicitly by every test that
#: is not about the default. Written as query parameters so the arithmetic
#: under test is the route's, not today's date.
SINCE = (NOW - timedelta(days=7)).isoformat()
UNTIL = (NOW + timedelta(hours=1)).isoformat()


def _capabilities(role: str) -> frozenset[Capability]:
    """Every capability one seeded role carries, after every migration."""
    granted, _scoped = _seeded()[role]
    return frozenset(Capability(word) for word in granted | _granted_later(role))


@asynccontextmanager
async def _client(
    db: AsyncSessionShim,
    *,
    caller: UUID,
    capabilities: frozenset[Capability] = frozenset(),
) -> AsyncIterator[httpx.AsyncClient]:
    """The app, reading the fixture's database, as one principal.

    `get_ctx` is overridden rather than a token being minted: authentication is
    `test_service_authentication.py`'s subject, and what this file is about is
    what happens *after* a principal is known. `get_db` is overridden with the
    fixture's session, so the statements the handlers run are the real ones
    against the real schema.

    In-process over ASGI, in **this** thread, for the reason the module
    docstring gives.
    """
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=caller,
        email="caller@test.local",
        correlation_id="t",
        capabilities=capabilities,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://usage.test"
    ) as client:
        yield client


# ── /usage/me: the scope is the caller ───────────────────────────────────
async def test_my_usage_returns_only_the_callers_rows(db: AsyncSessionShim) -> None:
    """The whole reason the route needs no capability.

    Two people, both with spend in the window, and a caller holding **no
    capability at all** — which is what a Normal User is. The answer is theirs
    and the other person is not in it, not even as a total they could subtract
    their own from.

    **The names are chosen so the other person sorts first.** The grouped query
    orders by display name, so a handler that had quietly widened to everybody
    and taken the first row would return the caller's own numbers — and pass —
    for any pair of names in the other order. This is the case that fails.
    """
    other = await _person(db, "Aaron First")
    mine = await _person(db, "Zoe Last")
    await _chat(db, other, prompt=9000, completion=900, cost=40.0)
    await _chat(db, mine, prompt=100, completion=10, cost=0.5)

    async with _client(db, caller=mine) as client:
        response = await client.get(
            "/api/v1/usage/me", params={"since": SINCE, "until": UNTIL}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["actor_id"] == str(mine)
    assert body["prompt_tokens"] == 100
    assert body["completion_tokens"] == 10
    assert body["runs"] == 1


async def test_my_usage_needs_no_capability_and_is_never_403(
    db: AsyncSessionShim,
) -> None:
    """A person's own token count is not something to be refused.

    Stated as its own case because the gate being absent is a **decision**,
    and a future reader finding no `Dep` on the route should find a test
    saying that was on purpose.
    """
    person = await _person(db, "Nobody")

    async with _client(db, caller=person) as client:
        response = await client.get("/api/v1/usage/me")

    assert response.status_code == 200


def test_my_usage_cannot_be_widened_by_any_parameter() -> None:
    """The route's signature, not its behaviour.

    `/usage/me` is ungated **because** its scope is `ctx.user_id` and nothing
    can reach it. A behavioural test — passing `?user_id=` and asserting it is
    ignored — would go on passing on the day somebody adds the parameter and
    forgets that removing the gate's premise means adding a gate.

    So: the handler takes the caller, a session and two dates. Anything else
    is a widening, and a `UUID` parameter is one however it is spelled.
    """
    hints = get_type_hints(routes.my_usage, include_extras=True)
    hints.pop("return", None)

    assert set(hints) == {"ctx", "db", "since", "until"}
    widening = [
        name
        for name, annotation in hints.items()
        if name not in {"ctx", "db"} and UUID in getattr(annotation, "__args__", ())
    ]
    assert not widening, (
        f"{widening} could name somebody other than the caller, and "
        "/usage/me carries no capability precisely because nothing can."
    )


async def test_the_default_window_is_the_last_thirty_days(
    db: AsyncSessionShim,
) -> None:
    """No `since`, no `until` — and a row from six weeks ago is not in it.

    Written against the real clock rather than the fixtures' instant, because
    the default is resolved from `now` and a test that pinned the clock would
    be asserting `clamp_window`'s arithmetic a second time instead of the
    route's default.
    """
    right_now = datetime.now(UTC)
    person = await _person(db, "Recent")
    await _chat(db, person, prompt=50, completion=5, day=right_now - timedelta(days=3))
    await _chat(db, person, prompt=800, completion=80, day=right_now - timedelta(days=45))

    async with _client(db, caller=person) as client:
        body = (await client.get("/api/v1/usage/me")).json()

    assert body["prompt_tokens"] == 50
    assert body["runs"] == 1
    assert usage.DEFAULT_WINDOW_DAYS == 30


# ── the two that read about other people ─────────────────────────────────
@pytest.mark.parametrize("path", ["/api/v1/usage/users", "/api/v1/usage/total"])
async def test_reading_about_other_people_refuses_without_the_capability(
    db: AsyncSessionShim, path: str
) -> None:
    """403, not 404 and not an empty list.

    An empty list would be the tempting "safe" answer and is the worse one: it
    reads as *"nobody has spent anything"*, which is a false statement rather
    than a refusal. A capability is about the caller, not about a resource, so
    saying no reveals nothing about what exists.
    """
    person = await _person(db, "Curious")
    await _chat(db, person)

    async with _client(db, caller=person) as client:
        response = await client.get(path)

    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/v1/usage/users", "/api/v1/usage/total"])
async def test_the_refusal_names_the_capability_that_was_missing(
    db: AsyncSessionShim, path: str
) -> None:
    """"You need `usage.read`" is a support ticket somebody can act on."""
    person = await _person(db, "Curious")

    async with _client(db, caller=person) as client:
        response = await client.get(path)

    assert "usage.read" in response.text


@pytest.mark.parametrize("path", ["/api/v1/usage/users", "/api/v1/usage/total"])
async def test_an_auditor_reaches_both(db: AsyncSessionShim, path: str) -> None:
    """The role `0030` was written for, asked through the routes it opens.

    An Auditor may read this and change nothing anywhere, which is the whole
    reason usage is a capability rather than an administrator's privilege.
    """
    auditor = await _person(db, "Auditor Ann")
    await _chat(db, auditor)

    async with _client(
        db, caller=auditor, capabilities=_capabilities("Auditor")
    ) as client:
        response = await client.get(path, params={"since": SINCE, "until": UNTIL})

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/usage/users", "/api/v1/usage/total"])
async def test_a_normal_user_reaches_neither(
    db: AsyncSessionShim, path: str
) -> None:
    """The other half of the same sentence, and the one that would rot quietly:
    a capability added to the wrong seed role fails nothing else here."""
    person = await _person(db, "Normal Norah")

    async with _client(
        db, caller=person, capabilities=_capabilities("Normal User")
    ) as client:
        response = await client.get(path, params={"since": SINCE, "until": UNTIL})

    assert response.status_code == 403


async def test_every_row_names_a_person_and_carries_no_address(
    db: AsyncSessionShim,
) -> None:
    """The rule `AuditEntry` states, on the second screen about people.

    A usage screen answers *"who spent this"* with something a person
    recognises. An email is a personal identifier it has no need of, and the
    body is checked as **text** so a field added later under any name cannot
    smuggle one in.
    """
    admin = await _person(db, "Admin Ada")
    spender = await _person(db, "Spender Sam")
    await _chat(db, spender)

    async with _client(
        db, caller=admin, capabilities=_capabilities("Administrator")
    ) as client:
        response = await client.get(
            "/api/v1/usage/users", params={"since": SINCE, "until": UNTIL}
        )

    rows = response.json()
    assert response.status_code == 200
    assert [row["actor"] for row in rows] == ["Spender Sam"]

    # Every address in the fixture's world, checked against the **whole body**
    # rather than against a named field: a field added later under any name
    # cannot smuggle one past this.
    emails = set((await db.execute(sa.select(User.email))).scalars().all())
    assert emails and not any(email in response.text for email in emails)


async def test_the_installation_total_carries_the_size_of_the_gap(
    db: AsyncSessionShim,
) -> None:
    """`unattributed` reaches the wire, which is what the screen states.

    The gap itself is `test_usage_service.py`'s subject — a deleted actor
    leaves the per-person view and stays in the total. What is asserted here
    is that the DTO does not drop the number on the way out, which is the
    difference between a screen that can explain the shortfall and one that
    leaves a reader to notice it.
    """
    admin = await _person(db, "Admin Ada")
    departed = await _person(db, "Departed Dana")
    await _chat(db, departed, prompt=300, completion=30, cost=1.0)
    await db.execute(_orphan(departed))

    async with _client(
        db, caller=admin, capabilities=_capabilities("Administrator")
    ) as client:
        body = (
            await client.get(
                "/api/v1/usage/total", params={"since": SINCE, "until": UNTIL}
            )
        ).json()

    assert body["actor_id"] is None
    assert body["prompt_tokens"] == 300
    assert body["unattributed"] == 1
    assert body["unattributed_tokens"] == 330


def _orphan(actor: UUID) -> sa.Update:
    """What deleting a person does to their runs: `actor_id` is SET NULL.

    Written as the UPDATE the constraint performs rather than by deleting the
    `users` row, because the conversation cascade would take the run with it
    and this test is about the run that survives its actor.
    """
    return sa.update(Run).where(Run.actor_id == actor).values(actor_id=None)
