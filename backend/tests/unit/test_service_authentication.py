"""The second authenticator, at the edge: dispatch, and the human-only routes.

`test_service_users.py` proves the key mechanism. This file proves the two
things that are only true at the HTTP boundary, and that a service-level test
cannot see:

* **`get_ctx` dispatches on the token's shape**, and the two paths produce one
  `RequestContext`. A `dm_sk_` bearer goes to the service authenticator and
  nothing else does — not as a fallback, because a credential that can be
  verified two ways has two attack surfaces.
* **Every route under `/auth` refuses a `SERVICE` principal**, with a 403 and a
  sentence. `/login` and `/refresh` are unreachable for a machine anyway — no
  password, no cookie — but `PATCH /auth/me` and `PUT /auth/me/password` are
  reachable with a valid key, and both would be wrong: the first lets a leaked
  key rename the identity it leaked from, and the second tries to write a
  `password_hash` onto a row whose `CHECK` forbids it, turning a policy
  question into a 500.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.context import RequestContext
from app.core.errors import AuthenticationError
from app.domain.ports.identity import AuthenticatedIdentity
from app.domain.value_objects.authz import Capability, PrincipalKind
from app.infra.identity import service_key
from app.main import create_app

SERVICE_ID = uuid.uuid4()
HUMAN_ID = uuid.uuid4()
KEY = "dm_sk_abcdefghijkl_a-secret-that-is-long-enough"  # noqa: S105  (a fixture)


class _RecordingIdentity:
    """Both authenticators, wearing both surfaces, recording which was asked.

    One object rather than two so the test can assert **which one ran** from a
    single place. The dispatch is the thing under test; the verification
    mechanics belong to `test_service_users.py`, which runs them against a real
    schema.
    """

    def __init__(self) -> None:
        self.jwt_calls: list[str] = []
        self.key_calls: list[str] = []

    async def verify_access_token(self, token: str) -> AuthenticatedIdentity:
        self.jwt_calls.append(token)
        if token.startswith(service_key.KEY_PREFIX):  # pragma: no cover - the bug
            raise AssertionError(
                "an API key reached the JWT verifier; the dispatch fell through"
            )
        return AuthenticatedIdentity(
            user_id=HUMAN_ID, email="ada@test.local", role="MEMBER"
        )

    async def verify_key(self, token: str) -> AuthenticatedIdentity:
        self.key_calls.append(token)
        if token != KEY:
            raise AuthenticationError("This API key is not valid.")
        return AuthenticatedIdentity(
            user_id=SERVICE_ID,
            email="svc-agent-1@service.datamind.local",
            role="MEMBER",
            display_name="Nightly reports",
            kind=PrincipalKind.SERVICE,
        )


class _Empty:
    """`Result.scalars()` over no rows: iterable, and `.all()`-able."""

    def all(self) -> list[Any]:
        return []

    def __iter__(self) -> Any:
        return iter(())


class _EmptyResult:
    def scalars(self) -> _Empty:
        return _Empty()


class _NoPrincipals:
    """A session that answers "no roles, no teams" to every resolution read.

    `get_ctx` runs both on every request whichever authenticator produced the
    identity — which is the point being asserted — so the double has to answer
    them for both kinds of principal, identically.
    """

    async def execute(self, _statement: Any, *_a: Any, **_k: Any) -> _EmptyResult:
        return _EmptyResult()

    async def get(self, _model: type, _pk: Any) -> Any:
        return None

    async def flush(self) -> None:
        return None


@pytest.fixture
def identity() -> _RecordingIdentity:
    return _RecordingIdentity()


@pytest.fixture
def client(identity: _RecordingIdentity) -> Any:
    """The **real** `get_ctx`, with only its two providers replaced.

    Overriding `get_ctx` itself — which every other API test in this suite does
    — would skip the dispatch entirely, and the dispatch is what this file is
    about.
    """
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: _NoPrincipals()
    app.dependency_overrides[deps.get_identity_provider] = lambda: identity
    app.dependency_overrides[deps.get_service_identity_provider] = lambda: identity
    api = TestClient(app)
    yield api
    app.dependency_overrides.clear()


# ── the dispatch ─────────────────────────────────────────────────────────
def test_a_service_key_goes_to_the_service_authenticator(
    client: Any, identity: _RecordingIdentity
) -> None:
    client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {KEY}"})
    assert identity.key_calls == [KEY]
    assert identity.jwt_calls == []


def test_anything_else_goes_to_the_jwt_path(
    client: Any, identity: _RecordingIdentity
) -> None:
    client.get("/api/v1/auth/me", headers={"Authorization": "Bearer eyJhbGci.x.y"})
    assert identity.jwt_calls == ["eyJhbGci.x.y"]
    assert identity.key_calls == []


def test_a_key_shaped_token_that_is_not_a_key_is_refused_not_retried(
    client: Any, identity: _RecordingIdentity
) -> None:
    """No fallback between the two verifiers, deliberately.

    A token that looks like a key and is not one is a failed key, not a
    candidate JWT. Retrying it against the other authenticator would give a
    caller two chances at two different secrets from one request — and would
    make the timing of a refusal depend on which verifier gave up first.
    """
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer dm_sk_notreal00000_nope"},
    )
    assert response.status_code == 401
    assert identity.jwt_calls == []


def test_no_bearer_at_all_is_still_a_401(client: Any) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_the_shape_test_is_a_prefix_test_and_nothing_more() -> None:
    """The whole of the dispatch, and it has to stay cheap and total.

    A dispatch that parsed, or looked anything up, would put a database read in
    front of every unauthenticated request.
    """
    assert service_key.looks_like_service_key("dm_sk_x_y")
    assert not service_key.looks_like_service_key("DM_SK_x_y")
    assert not service_key.looks_like_service_key(" dm_sk_x_y")
    assert not service_key.looks_like_service_key("")


# ── the human-only routes ────────────────────────────────────────────────
def _as_service(app: Any) -> None:
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=SERVICE_ID,
        email="svc-agent-1@service.datamind.local",
        role="MEMBER",
        kind=PrincipalKind.SERVICE,
        capabilities=frozenset({Capability.CONVERSATION_CREATE}),
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/auth/me", None),
        ("GET", "/api/v1/auth/me/permissions", None),
        ("PATCH", "/api/v1/auth/me", {"display_name": "Renamed by a key"}),
        (
            "PUT",
            "/api/v1/auth/me/password",
            {"current_password": "x" * 10, "new_password": "y" * 10},
        ),
    ],
)
def test_every_auth_me_route_refuses_a_service_principal(
    method: str, path: str, body: dict | None
) -> None:
    app = create_app()
    _as_service(app)
    # A session that would fail loudly if the handler body ever ran: the
    # refusal has to happen before anything is read or written, not as a
    # constraint violation on the way out.
    app.dependency_overrides[deps.get_db] = lambda: _Exploding()
    # No `with`: entering the context runs the app's lifespan, which bootstraps
    # the administrator against a real database. Nothing here needs it.
    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == 403
    assert "service account" in response.json()["detail"].lower()
    app.dependency_overrides.clear()


def test_a_human_principal_is_not_refused() -> None:
    """The guard on the guard: a refusal that fired for everybody would pass
    every assertion above and break the product."""
    app = create_app()
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=HUMAN_ID, email="ada@test.local", role="MEMBER"
    )
    app.dependency_overrides[deps.get_db] = lambda: _NoPrincipals()
    # 200 with an empty answer: the human got past the kind check, which is
    # the whole assertion. The empty capability list is what a principal with
    # no roles legitimately has.
    assert TestClient(app).get("/api/v1/auth/me/permissions").status_code == 200
    app.dependency_overrides.clear()


class _Exploding:
    """A session that fails the test if a refused handler's body is reached."""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - the point
        raise AssertionError(
            f"the handler ran and touched the database ({name}); a service "
            "principal should have been refused before the body"
        )
