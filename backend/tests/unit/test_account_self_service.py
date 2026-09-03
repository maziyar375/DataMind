"""F6 — the two routes a member has to their own account.

Everything under `/users` is admin-only, which left an invited member holding
a one-time password an administrator generated and can still read, with no way
to change it. `PATCH /auth/me` and `PUT /auth/me/password` are the way out.

Four claims are the reason this file exists:

* **Neither route takes a user id.** They act on `ctx.user_id`, so there is no
  parameter to point at somebody else — the sweep at the bottom asserts that,
  because a later route added beside them with an id in the path would quietly
  become an admin API without an admin dependency.
* **A wrong current password is a 422, not a 401.** The frontend's client
  treats a 401 as a dead session; answering one for a typo in a form field is
  how a mistyped password ends in a sign-out screen.
* **A rotation revokes every session and issues a new one.** Leaving old
  sessions alive would not take the old password out of use; leaving *none*
  alive would sign out the person who just changed it.
* **The admin path is untouched.** It stays the recovery route for a password
  nobody remembers.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.context import RequestContext
from app.infra.db.models import Session as SessionRow
from app.infra.db.models import User
from app.infra.identity.local import LocalIdentityProvider
from app.main import create_app

USER_ID = uuid.uuid4()
CURRENT = "old-password-9"
NEW = "a-much-better-one"


class FakeResult:
    """Just enough of a `Result` for `_revoke_all_for_user`'s one query."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> list[Any]:
        return self._rows


class FakeDb:
    """One user, and a record of what the routes did to the session table."""

    def __init__(self, user: User, sessions: list[SessionRow] | None = None) -> None:
        self.user = user
        self.sessions = sessions if sessions is not None else []
        self.added: list[Any] = []

    async def get(self, model: Any, pk: Any) -> Any:
        return self.user if pk == self.user.id else None

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        pass

    async def execute(self, _statement: Any) -> FakeResult:
        return FakeResult([s for s in self.sessions if s.revoked_at is None])


def _user(status: str = "ACTIVE", must_change: bool = False) -> User:
    provider = LocalIdentityProvider(None, get_settings())  # type: ignore[arg-type]
    return User(
        id=USER_ID,
        email="member@test.local",
        display_name="Ada",
        password_hash=provider.hash_password(CURRENT),
        role="MEMBER",
        status=status,
        must_change_password=must_change,
    )


def _live_session() -> SessionRow:
    return SessionRow(
        id=uuid.uuid4(),
        user_id=USER_ID,
        refresh_token_hash="whatever",
        expires_at=utcnow() + timedelta(days=1),
    )


def _client(db: FakeDb) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER_ID, email="member@test.local", role="MEMBER", correlation_id="test"
    )
    return TestClient(app)


@pytest.fixture
def db() -> FakeDb:
    return FakeDb(_user())


@pytest.fixture
def client(db: FakeDb) -> Any:
    api = _client(db)
    yield api
    api.app.dependency_overrides.clear()


# ── the display name ─────────────────────────────────────────────────────
def test_a_member_may_rename_themselves(client: Any, db: FakeDb) -> None:
    response = client.patch("/api/v1/auth/me", json={"display_name": "Ada Lovelace"})

    assert response.status_code == 200
    assert response.json()["display_name"] == "Ada Lovelace"
    assert db.user.display_name == "Ada Lovelace"


def test_the_name_is_trimmed(client: Any, db: FakeDb) -> None:
    client.patch("/api/v1/auth/me", json={"display_name": "  Ada  "})
    assert db.user.display_name == "Ada"


def test_an_empty_name_is_refused(client: Any, db: FakeDb) -> None:
    """`display_name` is what the rail shows; a blank one erases the account
    from its own sidebar."""
    assert client.patch("/api/v1/auth/me", json={"display_name": "   "}).status_code == 422
    assert client.patch("/api/v1/auth/me", json={"display_name": ""}).status_code == 422


def test_role_and_status_are_not_fields_a_member_can_send(
    client: Any, db: FakeDb
) -> None:
    """The privilege escalation this schema exists to make unexpressable."""
    client.patch(
        "/api/v1/auth/me",
        json={"display_name": "Ada", "role": "ADMIN", "status": "ACTIVE",
              "email": "someone@else.local"},
    )
    assert db.user.role == "MEMBER"
    assert db.user.email == "member@test.local"


# ── the password ─────────────────────────────────────────────────────────
def test_the_current_password_must_be_right(client: Any, db: FakeDb) -> None:
    before = db.user.password_hash
    response = client.put(
        "/api/v1/auth/me/password",
        json={"current_password": "not-it", "new_password": NEW},
    )

    # 422, not 401: the client reads a 401 as a dead session.
    assert response.status_code == 422
    assert response.json()["code"] == "E_VALIDATION"
    assert db.user.password_hash == before


def test_a_correct_current_password_rotates_it(client: Any, db: FakeDb) -> None:
    before = db.user.password_hash
    response = client.put(
        "/api/v1/auth/me/password",
        json={"current_password": CURRENT, "new_password": NEW},
    )

    assert response.status_code == 200
    assert db.user.password_hash != before
    provider = LocalIdentityProvider(None, get_settings())  # type: ignore[arg-type]
    assert provider.verify_password(db.user, NEW)
    assert not provider.verify_password(db.user, CURRENT)


def test_a_short_new_password_is_refused(client: Any, db: FakeDb) -> None:
    """The same floor `AdminSetPasswordRequest` sets. Two different minimums
    for one field is a policy nobody can state."""
    response = client.put(
        "/api/v1/auth/me/password",
        json={"current_password": CURRENT, "new_password": "short"},
    )
    assert response.status_code == 422


def test_every_existing_session_is_revoked_and_a_new_one_issued() -> None:
    live = [_live_session(), _live_session()]
    db = FakeDb(_user(), sessions=live)
    client = _client(db)

    response = client.put(
        "/api/v1/auth/me/password",
        json={"current_password": CURRENT, "new_password": NEW},
    )

    assert response.status_code == 200
    assert all(s.revoked_at is not None for s in live), "old sessions survived"
    # And the caller is not the one signed out: a fresh session row, a fresh
    # refresh cookie, and an access token to carry on with.
    assert [row for row in db.added if isinstance(row, SessionRow)], "no new session"
    assert response.json()["access_token"]
    assert get_settings().refresh_cookie_name in response.cookies
    client.app.dependency_overrides.clear()


def test_an_invited_account_becomes_active_and_stops_being_told_to_change() -> None:
    db = FakeDb(_user(status="INVITED", must_change=True))
    client = _client(db)

    client.put(
        "/api/v1/auth/me/password",
        json={"current_password": CURRENT, "new_password": NEW},
    )

    assert db.user.status == "ACTIVE"
    assert db.user.must_change_password is False
    client.app.dependency_overrides.clear()


# ── the shape of the routes ──────────────────────────────────────────────
def test_no_self_service_route_takes_a_user_id() -> None:
    """The structural half of the guarantee.

    These routes are safe because they cannot name a victim, not because they
    check. A route added under `/auth/me` with an id in its path would be an
    admin API wearing a member's dependency, and this fails the day it lands.
    """
    app = create_app()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/v1/auth/me"):
            assert "{" not in path, f"{path} takes a parameter"


def test_the_admin_password_route_still_requires_an_admin() -> None:
    """F6 adds a path; it does not soften the one that was there."""
    from app.api.v1 import users

    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: None
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER_ID, email="member@test.local", role="MEMBER", correlation_id="test"
    )
    client = TestClient(app)

    response = client.put(
        f"/api/v1/users/{uuid.uuid4()}/password", json={"password": NEW}
    )
    assert response.status_code == 403
    assert users.set_user_password  # the route this is about, still where it was
    app.dependency_overrides.clear()
