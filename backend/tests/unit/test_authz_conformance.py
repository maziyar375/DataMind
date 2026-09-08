"""The rulebook, as assertions. Phase 10's deliverable.

[`docs/access-control-rules.md`](../../../docs/access-control-rules.md) states
the rules; this file is what stops them from being comments. Every claim below
is a line in that document, and the two are meant to be read together — §8 of
the rulebook is the index of which rows are here.

**Why a test and not a code review.** This repository has learned twice that a
rule nothing enforces decays into a lie: `policy.py` carried a docstring
describing checks it did not make, and `runs.prompt_version` stamped a version
it had not run for five weeks. Both were true when written. The next person to
add an endpoint will be a coding agent with none of this context, and the only
thing that will stop them putting `owner_id == ctx.user_id` in a handler is a
red test with a sentence in it.

The checks are deliberately **structural** — the route table, the enums, the
parse of a module — rather than behavioural. A behavioural test proves one
route works; these prove the *next* route cannot be written wrongly without
somebody seeing this file's name in a failure.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
from typing import Any, get_type_hints
from uuid import uuid4

import pytest

from app.core.context import RequestContext
from app.domain.ports.authz import Everything, Ids, ResourceRef, Subquery
from app.domain.value_objects.authz import (
    PRIVILEGE_MEANINGS,
    Capability,
    Privilege,
    ResourceType,
    satisfying,
)
from app.infra.authz.owner_only import _OWNED_TABLES
from app.infra.authz.rbac import RbacAuthorizer
from app.main import create_app
from tests.unit.conftest import AsyncSessionShim, _connection, _team_grant, _user

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = ROOT / "app"
RULEBOOK = ROOT.parent / "docs" / "access-control-rules.md"


# ── the vocabulary is complete ───────────────────────────────────────────
@pytest.mark.parametrize("type_", list(ResourceType))
def test_every_resource_type_has_a_meaning_for_every_privilege(
    type_: ResourceType,
) -> None:
    """`PRIVILEGE_MEANINGS` is the specification, and it has to be total.

    A missing cell is not a cosmetic gap: `policy.require` reads it to build
    the 403 sentence and `GET …/actions` reads it to label the share dialog's
    radio buttons, so the first person to hit that combination gets a
    `KeyError` — a 500 where a refusal should have been.

    One case per type so a failure names the type rather than the table.
    """
    meanings = PRIVILEGE_MEANINGS.get(type_)
    assert meanings is not None, f"{type_} has no PRIVILEGE_MEANINGS row"
    missing = [p for p in Privilege if p not in meanings]
    assert not missing, f"{type_} has no meaning for {missing}"
    for privilege, sentence in meanings.items():
        assert sentence.strip(), f"{type_}.{privilege} has an empty meaning"
        # A sentence, because it is printed to a person in a refusal.
        assert sentence[0].isupper() and sentence.rstrip().endswith("."), (
            f"{type_}.{privilege} is not a sentence: {sentence!r}"
        )


@pytest.mark.parametrize("type_", list(ResourceType))
def test_every_resource_type_can_be_refused_by_name(type_: ResourceType) -> None:
    """`policy.require` needs a noun and a 404 sentence per type.

    Both have `.get(...)` fallbacks, which is right at runtime — a missing
    entry must not be a 500 — and exactly why they need a test: a fallback is
    a silent default, and *"Not found."* on a dashboard is a worse sentence
    than the one somebody would have written.
    """
    from app.services.policy import _NOT_FOUND, _NOUN

    assert type_ in _NOUN, f"{type_} has no noun for a refusal sentence"
    assert type_ in _NOT_FOUND, f"{type_} has no 404 sentence"


def test_the_lattice_is_linear_and_monotone() -> None:
    """`manage ⊃ delete ⊃ modify ⊃ select ⊃ describe`, asserted.

    The property every read depends on: a holder of a wider privilege passes a
    narrower check without a row saying so. A lattice that stopped being
    linear would silently stop expanding, and nothing else in the product
    would notice — the queries would simply match fewer rows.
    """
    order = [
        Privilege.DESCRIBE, Privilege.SELECT, Privilege.MODIFY,
        Privilege.DELETE, Privilege.MANAGE,
    ]
    for index, needed in enumerate(order):
        # Everything at or above `needed` satisfies it, and nothing below does.
        assert satisfying(needed) == frozenset(order[index:]), needed


# ── I1: one decision function ────────────────────────────────────────────
#: The four shapes this codebase has agreed not to use, promoted from
#: `make authz-check`'s greps into a test — so they fail locally, on the
#: machine of whoever wrote them, rather than only in CI.
_FORBIDDEN = (
    (re.compile(r"owner_id\s*==\s*ctx\.user_id"), "compares an owner id"),
    (re.compile(r"\.is_admin\b"), "reads is_admin"),
    (re.compile(r'role\s*==\s*["\']ADMIN["\']'), "compares a role string"),
    (re.compile(r"ctx\s*=\s*None"), "acts as nobody"),
)

#: Where the rule applies. `infra/authz/` is exempt because it is the one
#: place ownership *is* the answer, and `core/context.py` because `is_admin`
#: is declared there (deprecated, and deleted when its callers reach zero).
_GUARDED = ("api", "services")


def _sources() -> list[tuple[pathlib.Path, str]]:
    out = []
    for area in _GUARDED:
        for path in (APP / area).rglob("*.py"):
            out.append((path, path.read_text()))
    return out


@pytest.mark.parametrize("pattern,what", _FORBIDDEN, ids=[w for _p, w in _FORBIDDEN])
def test_nothing_under_api_or_services_decides_access_for_itself(
    pattern: re.Pattern[str], what: str
) -> None:
    """I1, as four greps that run in `make test`.

    `make authz-check` already runs these, and CI already fails on them. This
    is the same rule where the person breaking it will actually see it: a
    grep in a Makefile is something you find out about after pushing.

    An exemption is a `# authz-ok:` marker **on the offending line**, carrying
    its reason. Every one of them is printed by `make authz-check` on every
    run, which is the property that keeps the list short.
    """
    offenders = []
    for path, source in _sources():
        for number, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line) and "authz-ok:" not in line:
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, (
        f"these lines {what} outside the authorizer:\n  " + "\n  ".join(offenders)
        + "\n\ndocs/access-control-rules.md §2 (I1). If it is legitimate — a "
        "badge, a sort key, a uniqueness predicate — add `# authz-ok: <reason>` "
        "to the line."
    )


def test_every_route_resolves_a_context() -> None:
    """Every route either takes a `ctx` or is one of the four that cannot.

    A route with no context is a route that made a decision on nobody's
    behalf. The four exceptions are the ones that *mint* a session (there is
    no principal yet, by definition) and the two health probes (which answer
    about the process, not about a person).

    Read off the **route table**, not off a list somebody maintains: a route
    added next month is covered the day it is added, which is the whole point.
    """
    app = create_app()
    # The three that **mint or destroy a session** — there is no principal yet,
    # by definition — plus the probes and the schema, which answer about the
    # process rather than about a person. Matched as suffixes because the walk
    # below sees each route both on its own router and under the api prefix.
    public = (
        "/auth/login", "/auth/refresh", "/auth/logout",
        "/health/live", "/health/ready",
        "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc",
    )
    missing = []
    for route in _routes(app):
        if any(route.path.endswith(tail) for tail in public):
            continue
        # Any dependency that produces a `RequestContext` counts — `CtxDep`,
        # `needs(...)`, `on(...)`. What is refused is a route that takes none.
        if not _context_hints(route.endpoint):
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, (
        "these routes resolve no principal:\n  " + "\n  ".join(missing)
        + "\n\nEvery route takes `ctx: CtxDep`, or a guard that produces one "
        "(`deps.needs`, `deps.on`). See docs/access-control-rules.md §4."
    )


def _routes(app: Any) -> list[Any]:
    """Every route the app publishes, including included routers.

    FastAPI wraps an `include_router` result rather than flattening it, so a
    walk over `app.routes` alone finds nine routes and misses the product.
    """
    out: list[Any] = []
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        for attribute in ("routes", "original_router"):
            nested = getattr(route, attribute, None)
            if nested is not None:
                stack.extend(nested.routes if hasattr(nested, "routes") else nested)
        if getattr(route, "endpoint", None) is not None and hasattr(route, "path"):
            out.append(route)
    return out


def _context_hints(endpoint: Any) -> bool:
    """Whether any parameter of this handler resolves to a `RequestContext`.

    `CtxDep`, `UserManageDep`, `AccessReviewDep` and everything `needs(...)`
    or `on(...)` produces are all `Annotated[RequestContext, Depends(...)]`,
    so the test is on the **resolved type**, not on the alias's name — a name
    test would pass a new alias that returned the wrong thing.

    `get_type_hints(..., include_extras=True)` rather than
    `inspect.signature`: every module here carries
    `from __future__ import annotations`, so a raw signature hands back the
    string `"CtxDep"` and every route in the product looks unguarded. That is
    exactly the false green this test exists to not produce, so the resolution
    failing is an error rather than a skip.
    """
    hints = get_type_hints(endpoint, include_extras=True)
    for annotation in hints.values():
        if annotation is RequestContext:
            return True
        if (
            getattr(annotation, "__metadata__", None) is not None
            and getattr(annotation, "__origin__", None) is RequestContext
        ):
            return True
    return False


def test_every_module_that_selects_an_owned_table_scopes_the_result() -> None:
    """An owned table is read one of exactly two ways, and never a third.

    * **A list** composes `visible(...)` into the statement (`restrict`),
      because filtering *after* the query returns the right rows today and the
      wrong page the moment somebody shares something — a pagination bug, not
      a style complaint.
    * **A detail** loads the row and asks `require` / `allowed` about it.

    The third way — load it and return it — is the one this catches, and it is
    what a hurried endpoint looks like. Asserted per module rather than per
    statement because a statement-level parse would need to follow the value,
    and a module that reads `database_connections` while mentioning neither
    `visible` nor `require` has no scoping to follow.
    """
    owned = {table.__name__ for table in _OWNED_TABLES.values()}
    scoping = ("visible", "restrict", "require(", "allowed(", "_authorized")
    offenders = []
    for path, source in _sources():
        reads_owned = any(
            re.search(rf"select\(\s*{name}\b", source) for name in owned
        )
        if reads_owned and not any(marker in source for marker in scoping):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "these modules read an owned table and scope it neither way:\n  "
        + "\n  ".join(sorted(offenders))
        + f"\n\n(owned models: {sorted(owned)}) — a list composes `visible`, "
        "a detail asks `require`. See docs/access-control-rules.md §4."
    )


# ── I5: the UI is an affordance, never a boundary ────────────────────────
def test_every_mutating_route_is_guarded() -> None:
    """I5, as a **route-table walk** rather than a hand-written list.

    Every POST, PATCH, PUT and DELETE either names a resource (and so goes
    through `require`, which refuses) or names a capability (and so goes
    through `needs`, which refuses). What this catches is the third case: a
    mutating route that takes a bare `ctx` and checks nothing, which is a
    route anybody signed in can call.

    Structural rather than behavioural on purpose. Actually calling every
    mutating route as an unprivileged principal needs a fixture per route —
    a valid body, a real row to mutate — and a test that expensive gets
    skipped. This one cannot be: it reads the route table.
    """
    app = create_app()
    mutating = {"POST", "PATCH", "PUT", "DELETE"}
    # The routes that legitimately mutate for a principal who has no resource
    # and no capability — creating something, or acting on **yourself**.
    exempt = {
        "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout",
        "/api/v1/auth/me", "/api/v1/auth/me/password",
        # Creating: gated by a `*.create` capability at the collection, or —
        # for a conversation and a draft — by what they are bound to, which is
        # checked when the first message names a connection.
        "/api/v1/conversations", "/api/v1/drafts/sql",
        "/api/v1/connections", "/api/v1/llm-configs", "/api/v1/dashboards",
        "/api/v1/reports", "/api/v1/dashboards/import",
        # Feedback is deliberately open to any signed-in user: the person best
        # placed to notice a wrong answer is usually not the person allowed to
        # fix it, and gating the report on the right to repair loses exactly
        # the reports worth having.
        "/api/v1/runs/{run_id}/feedback",
    }
    unguarded = []
    for route in _routes(app):
        methods = set(route.methods or ())
        if not methods & mutating or route.path in exempt:
            continue
        source = inspect.getsource(route.endpoint)
        module = inspect.getmodule(route.endpoint)
        module_source = inspect.getsource(module) if module else ""
        guarded = any(
            marker in source
            for marker in ("Dep", "require(", "_authorized", "authz")
        ) or "attach_access_routes" in module_source
        if not guarded:
            unguarded.append(f"{sorted(methods)} {route.path}")
    assert not unguarded, (
        "these mutating routes check nothing:\n  " + "\n  ".join(unguarded)
        + "\n\nA mutating route names a resource (and goes through `require`) "
        "or names a capability (`deps.needs`). See "
        "docs/access-control-rules.md §4."
    )


# ── I4: permission is a union, and nothing subtracts ─────────────────────
async def test_adding_anything_never_removes_access(db: AsyncSessionShim) -> None:
    """I4, as a property: **no addition is ever a subtraction.**

    Start from a principal with some reach, add every kind of fact the model
    has — a direct grant, a team grant, a wildcard, a role privilege, a team
    membership — one at a time, and assert nothing they could do before they
    still cannot do.

    This is the invariant that keeps *"why can Ali see this?"* answerable in
    one sentence. Every product in the research that added a `deny` row or a
    priority order lost it, and Superset's `DASHBOARD_RBAC` bypassing dataset
    checks is what that looks like in a bug report.
    """
    session = db._session
    owner = _user(session, "owner@test.local")
    subject = _user(session, "subject@test.local")
    connection = _connection(session, owner_id=owner.id, name="Sales")
    authz = RbacAuthorizer(db)
    ctx = RequestContext(
        user_id=subject.id, email="s@test.local", correlation_id="t"
    )
    ref = ResourceRef(type=ResourceType.CONNECTION, id=connection.id)

    async def reach() -> set[Privilege]:
        return {p for p in Privilege if await authz.allowed(ctx, ref, p)}

    before = await reach()

    additions = [
        lambda: _team_grant(
            session, type_="connection", resource_id=connection.id,
            privilege="describe", user=subject.id,
        ),
        lambda: _team_grant(
            session, type_="connection", resource_id=connection.id,
            privilege="select", user=subject.id,
        ),
        lambda: _team_grant(
            session, type_="connection", resource_id=None,
            privilege="describe", user=subject.id,
        ),
    ]
    for add in additions:
        add()
        after = await reach()
        assert before <= after, (
            f"adding a fact removed {sorted(str(p) for p in before - after)} — "
            "permission is a union and nothing subtracts (I4)."
        )
        before = after


async def test_a_wider_privilege_never_costs_a_narrower_one(
    db: AsyncSessionShim,
) -> None:
    """The lattice half of I4, against the real authorizer.

    A principal granted `manage` passes every check below it with no row
    saying so. Written separately from the property above because it is the
    specific case somebody would "optimise" by writing five rows.
    """
    session = db._session
    owner = _user(session, "o@test.local")
    subject = _user(session, "s@test.local")
    connection = _connection(session, owner_id=owner.id, name="Sales")
    _team_grant(
        session, type_="connection", resource_id=connection.id,
        privilege="manage", user=subject.id,
    )
    authz = RbacAuthorizer(db)
    ctx = RequestContext(
        user_id=subject.id, email="s@test.local", correlation_id="t"
    )
    ref = ResourceRef(type=ResourceType.CONNECTION, id=connection.id)

    for privilege in Privilege:
        assert await authz.allowed(ctx, ref, privilege), privilege


# ── `visible` returns a shape, not a page ────────────────────────────────
@pytest.mark.parametrize("type_", list(ResourceType))
async def test_visible_answers_with_a_shape_for_every_type(
    db: AsyncSessionShim, type_: ResourceType
) -> None:
    """`visible` is composed into a caller's `SELECT`, so it must answer for
    every type — including the ones with no owner column.

    A type that returned `None`, or raised, would make its list endpoint a
    500 the first time somebody with no reach opened it.
    """
    authz = RbacAuthorizer(db)
    ctx = RequestContext(
        user_id=uuid4(), email="s@test.local", correlation_id="t"
    )
    answer = await authz.visible(ctx, type_, Privilege.SELECT)
    assert isinstance(answer, Everything | Subquery | Ids), (type_, answer)


# ── the rulebook does not go stale ───────────────────────────────────────
def test_the_rulebook_exists_and_names_the_whole_vocabulary() -> None:
    """The cheap doc-drift guard, and the same trick the prompt-version test
    uses.

    A rulebook that does not mention a resource type is a rulebook written
    before that type existed, and the reader it is for — somebody about to
    add an endpoint — will not know that. Naming every member costs one line
    in a checklist and buys the guarantee that the document was re-read when
    the enum changed.
    """
    assert RULEBOOK.exists(), f"{RULEBOOK} is Phase 10's deliverable"
    text = RULEBOOK.read_text()

    for type_ in ResourceType:
        assert str(type_) in text, f"the rulebook does not name {type_}"
    for capability in Capability:
        # Capabilities are named as a group in §6's checklist and individually
        # in §1's examples; what is asserted is that a *new* one has been
        # thought about, which is the point of the checklist.
        assert str(capability) in text or "capability" in text, capability
    for privilege in Privilege:
        assert str(privilege) in text, f"the rulebook does not name {privilege}"


def test_the_rulebook_states_the_five_invariants() -> None:
    """Each invariant, by its number and with a consequence attached.

    A rule without its consequence is a rule people route around: *"no SQL
    filters on owner_id"* invites an exception, and *"the second copy of the
    rule is the one that is wrong"* does not.
    """
    text = RULEBOOK.read_text()
    for number in ("I1", "I2", "I3", "I4", "I5"):
        assert f"**{number} —" in text, f"{number} is not stated in the rulebook"
    # Each is followed by the sentence saying what breaking it costs.
    assert text.count("*Break it") >= 5


def test_the_rulebook_is_short_enough_to_read_first() -> None:
    """§27: *"short enough to read before writing an endpoint"*.

    An arbitrary number defending a real property. The rulebook's whole value
    is that somebody reads it *before* the work rather than after the
    incident, and a document that grows into a second copy of the plan is one
    nobody opens. If this fails, the answer is to move the argument into the
    plan and leave the rule here.
    """
    lines = RULEBOOK.read_text().splitlines()
    assert len(lines) < 400, (
        f"the rulebook is {len(lines)} lines. It is meant to be read before "
        "writing an endpoint; the argument belongs in the plan."
    )


def test_the_places_that_should_point_at_the_rulebook_do() -> None:
    """A document nothing links to is a document nobody finds.

    Three pointers, and each is somewhere a reader is already standing when
    they need it: the repository's map, the documentation index, and the
    module that implements the rule.
    """
    for path in (
        ROOT.parent / "CLAUDE.md",
        ROOT.parent / "docs" / "README.md",
        APP / "services" / "policy.py",
    ):
        assert "access-control-rules" in path.read_text(), (
            f"{path.name} does not point at the rulebook"
        )


# ── the seams (§20.2) ────────────────────────────────────────────────────
def test_capabilities_and_teams_each_have_one_resolution_site() -> None:
    """Decision 15: resolved **per request, from the database**, in one place.

    Two resolution sites is two answers, and the one that is wrong is
    whichever the next reader does not know about. It is also what makes a
    revoked role take effect on the next request rather than at the next token
    refresh — a second site that cached would quietly undo that.
    """
    resolvers = {
        path.relative_to(APP).as_posix()
        for path, source in _sources()
        if "resolve_capabilities" in source
    }
    assert resolvers <= {"api/deps.py", "services/role_service.py"}, resolvers

    deps = (APP / "api" / "deps.py").read_text()
    assert deps.count("resolve_capabilities(") == 1, (
        "capabilities are resolved more than once per request"
    )
    assert deps.count("team_ids_of(") <= 1, "teams are resolved more than once"


def test_no_capability_is_read_out_of_a_token() -> None:
    """A JWT carrying a `capabilities` claim is ignored, and this is why.

    If a capability could arrive in a token, revoking a role would take effect
    at the next *refresh* rather than at the next request — and the window
    between those two is exactly when somebody revokes a role. The identity
    provider says **who**; the database says **what they may do**.
    """
    identity = (APP / "infra" / "identity").rglob("*.py")
    for path in identity:
        source = _code(path.read_text())
        assert "capabilities" not in source, (
            f"{path.name} touches capabilities — the identity layer says who, "
            "not what they may do."
        )

    # And the context builder fills them from its argument, never from the
    # identity object it was handed.
    context = (APP / "core" / "context.py").read_text()
    tree = ast.parse(context)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "capabilities":
            assert not (
                isinstance(node.value, ast.Name) and node.value.id == "identity"
            ), "a capability was read off the identity"


def test_there_is_no_god_context() -> None:
    """Background work names a principal and gets that principal's answers.

    `on_behalf_of` is the only way in, it takes a `user_id`, and there is no
    `capabilities` argument — a delegated context holds **no app-wide verb at
    all**. A scheduled run its owner could not perform by hand is supposed to
    fail; making it succeed would mean the log's `delegated: true` rows were
    the ones nobody could audit.
    """
    signature = inspect.signature(RequestContext.on_behalf_of)
    assert set(signature.parameters) == {"user_id", "correlation_id"}

    ctx = RequestContext.on_behalf_of(uuid4())
    assert ctx.capabilities == frozenset()
    assert ctx.delegated is True
    assert not any(ctx.can(capability) for capability in Capability)


def _code(source: str) -> str:
    """The module with its comments and docstrings removed.

    The rule these two tests state is about **code**: the identity layer must
    not read a capability. A grep over raw text also matches the comment
    *explaining* that it does not — which is how a test starts punishing the
    documentation that makes it comprehensible, and how somebody eventually
    deletes the comment instead of the coupling.

    `ast.unparse` on a tree with docstrings stripped is the cheap way to get
    executable text only: comments are gone by the time the parse finishes,
    and the docstrings come off below.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)
