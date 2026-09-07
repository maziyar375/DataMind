"""Every route that used to say "administrator" now names a capability.

Phase 3 replaced one two-value role string with eighteen verbs, and the whole
value of that is lost if a single route keeps deciding for itself. So this file
walks the **live route table** rather than the source: a decorator can be
copied, a dependency cannot be faked, and what actually runs is what the guard
does or does not do before the handler body.

Three claims:

* **Every route that carried `AdminDep` now carries a capability**, and the
  capability is the right one — `user.read` for reading people, `user.manage`
  for changing them, `audit.read` for the log.
* **An unprivileged caller gets 403 with a sentence.** Not 404: a capability is
  not about a resource, so refusing one reveals nothing about what exists, and
  "you need `role.manage`" is a support ticket somebody can act on.
* **The guard runs before the handler.** Asserted by pointing the route at a
  session that would raise if anything touched it — if the body ran, the test
  fails loudly rather than passing for the wrong reason.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.api import deps
from app.core.context import RequestContext
from app.core.errors import ForbiddenError
from app.domain.value_objects.authz import Capability
from app.main import create_app

#: The routes that were guarded by `AdminDep` before Phase 3, and the
#: capability each must now require. Written out rather than derived: the point
#: of the file is that somebody decided, per route, which of the eighteen words
#: it needs — and `user.read` versus `user.manage` is exactly the decision an
#: Auditor's existence depends on.
EXPECTED: dict[tuple[str, str], Capability] = {
    ("GET", "/api/v1/users"): Capability.USER_READ,
    ("POST", "/api/v1/users"): Capability.USER_MANAGE,
    ("PATCH", "/api/v1/users/{user_id}"): Capability.USER_MANAGE,
    ("PUT", "/api/v1/users/{user_id}/password"): Capability.USER_MANAGE,
    ("DELETE", "/api/v1/users/{user_id}"): Capability.USER_MANAGE,
    ("GET", "/api/v1/audit"): Capability.AUDIT_READ,
}

#: The routes Phase 3 adds, and what each needs. Reading somebody's roles is
#: part of reading their account; changing them is a role operation.
ADDED: dict[tuple[str, str], Capability] = {
    ("GET", "/api/v1/roles"): Capability.ROLE_READ,
    ("GET", "/api/v1/roles/capabilities"): Capability.ROLE_READ,
    ("GET", "/api/v1/roles/privileges"): Capability.ROLE_READ,
    ("GET", "/api/v1/roles/{role_id}"): Capability.ROLE_READ,
    ("POST", "/api/v1/roles"): Capability.ROLE_MANAGE,
    ("PATCH", "/api/v1/roles/{role_id}"): Capability.ROLE_MANAGE,
    ("DELETE", "/api/v1/roles/{role_id}"): Capability.ROLE_MANAGE,
    ("GET", "/api/v1/users/{user_id}/roles"): Capability.USER_READ,
    ("POST", "/api/v1/users/{user_id}/roles"): Capability.USER_MANAGE,
    ("DELETE", "/api/v1/users/{user_id}/roles/{role_id}"): Capability.USER_MANAGE,
}


def _guarded_by(route: Any) -> set[Capability]:
    """Which capabilities this route's dependency tree demands.

    Read off the closure of every `needs(...)` guard in the flattened
    dependant, which is what FastAPI will actually call. A route that got its
    guard by importing the wrong alias, or by declaring a `ctx: CtxDep` and
    checking inside the body, has an empty set here — which is the failure this
    walk exists to catch.
    """
    found: set[Capability] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        call = dependant.call
        closure = getattr(call, "__closure__", None) or ()
        if getattr(call, "__name__", "") == "guard":
            for cell in closure:
                if isinstance(cell.cell_contents, Capability):
                    found.add(cell.cell_contents)
        stack.extend(dependant.dependencies)
    return found


def _routes() -> dict[tuple[str, str], Any]:
    """Every mounted `APIRoute`, keyed by method and path.

    Flattened rather than read off `app.routes`, because this FastAPI version
    keeps an included router as one opaque route object with the real handlers
    (and the mount prefix) inside it. A walk that stopped at the top level
    would find nothing and **pass**, which is the worst possible way for a
    permissions test to succeed — so `test_the_walk_finds_the_real_routes`
    below asserts the walk itself is not empty.
    """
    out: dict[tuple[str, str], Any] = {}

    def descend(routes: list[Any], prefix: str) -> None:
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                descend(
                    included.routes, prefix + (route.include_context.prefix or "")
                )
                continue
            if not hasattr(route, "dependant"):
                continue
            for method in getattr(route, "methods", set()) or set():
                out[(method, prefix + route.path)] = route

    descend(list(create_app().routes), "")
    return out


def test_the_walk_finds_the_real_routes() -> None:
    """The guard on the guard.

    Every assertion in this file is of the form "this route requires that
    capability", and every one of them would vanish silently if `_routes()`
    returned nothing. It has done exactly that once already, on a FastAPI
    version that wraps an included router in a single opaque object.
    """
    found = _routes()
    assert ("GET", "/api/v1/roles") in found
    assert len(found) > 50


@pytest.mark.parametrize(("key", "capability"), sorted((EXPECTED | ADDED).items()))
def test_each_administration_route_names_its_capability(
    key: tuple[str, str], capability: Capability
) -> None:
    route = _routes().get(key)
    assert route is not None, f"{key[0]} {key[1]} is not mounted"
    assert capability in _guarded_by(route), (
        f"{key[0]} {key[1]} does not require {capability}"
    )


def test_no_route_in_the_tree_still_uses_the_deprecated_admin_guard() -> None:
    """`require_admin` survives one release for out-of-tree callers only.

    It is now literally `needs(Capability.USER_MANAGE)` — `is_admin` reads the
    capability set — so nothing can *disagree*; what this asserts is that
    nothing in the product still spells it the old way, which is the condition
    for deleting it in Phase 10.
    """
    offenders = [
        f"{method} {path}"
        for (method, path), route in _routes().items()
        if any(
            dependency.call is deps.require_admin
            for dependency in route.dependant.dependencies
        )
    ]
    assert offenders == []


# ── the refusal itself ───────────────────────────────────────────────────
class _Exploding:
    """A session that fails the test if the handler body is ever reached."""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - the point
        raise AssertionError(
            f"the handler ran and touched the database ({name}); the guard "
            "should have refused before the body"
        )


def _client(capabilities: frozenset[Capability]) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: _Exploding()
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=uuid4(),
        email="someone@test.local",
        role="MEMBER",
        capabilities=capabilities,
    )
    return TestClient(app)


def test_a_caller_without_the_capability_is_refused_before_the_handler() -> None:
    client = _client(frozenset())

    response = client.get("/api/v1/roles")

    assert response.status_code == 403
    client.app.dependency_overrides.clear()


def test_the_refusal_names_the_permission_that_was_missing() -> None:
    """A 403 saying "forbidden" produces a support ticket; one saying "you need
    role.manage" produces an action."""
    client = _client(frozenset({Capability.ROLE_READ}))

    response = client.post("/api/v1/roles", json={"name": "Analyst"})

    assert response.status_code == 403
    assert "role.manage" in response.json()["detail"]
    client.app.dependency_overrides.clear()


def test_it_is_403_and_never_404() -> None:
    """A capability is about the caller, not about a resource, so refusing one
    reveals nothing about what exists — the existence-oracle rule in §19.1 is a
    rule about *rows*."""
    client = _client(frozenset())

    for path in ("/api/v1/roles", "/api/v1/users", "/api/v1/audit"):
        assert client.get(path).status_code == 403, path
    client.app.dependency_overrides.clear()


def test_holding_the_read_capability_is_not_holding_the_write_one() -> None:
    """The split that makes an Auditor possible: read everything about people,
    change nothing about any of them."""
    client = _client(frozenset({Capability.USER_READ, Capability.AUDIT_READ}))

    assert client.delete(f"/api/v1/users/{uuid4()}").status_code == 403
    assert client.patch(f"/api/v1/users/{uuid4()}", json={}).status_code == 403
    client.app.dependency_overrides.clear()


# ── the guard in isolation ───────────────────────────────────────────────
async def test_needs_returns_the_context_when_the_capability_is_held() -> None:
    guard = deps.needs(Capability.EVAL_RUN)
    ctx = RequestContext(
        user_id=uuid4(), email="e@test.local", role="MEMBER",
        capabilities=frozenset({Capability.EVAL_RUN}),
    )

    assert await guard(ctx) is ctx


async def test_needs_raises_forbidden_when_it_is_not() -> None:
    guard = deps.needs(Capability.EVAL_RUN)
    ctx = RequestContext(
        user_id=uuid4(), email="e@test.local", role="MEMBER",
        capabilities=frozenset({Capability.AUDIT_READ}),
    )

    with pytest.raises(ForbiddenError):
        await guard(ctx)


def test_a_guard_is_a_dependency_rather_than_a_call_inside_the_body() -> None:
    """The answer to OWASP API1:2023, asserted rather than described.

    A check inside a handler is one the next route can forget. This builds a
    route the way the product does and proves the refusal happens without the
    body being entered at all — the body here would raise a different error.
    """
    app = create_app()
    router = APIRouter()

    @router.get("/guarded")
    async def guarded(  # noqa: ANN202
        ctx: Any = Depends(deps.needs(Capability.SETTINGS_MANAGE)),
    ):
        raise AssertionError("the body ran")

    app.include_router(router)
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=uuid4(), email="x@test.local", role="MEMBER",
    )

    # Not entered as a context manager: the app's lifespan builds the run
    # executor, which wants a real secret-box key, and none of that is what
    # this test is about.
    assert TestClient(app).get("/guarded").status_code == 403
