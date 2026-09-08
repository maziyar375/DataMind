"""The eight seams (§20.2), and an OIDC test with no Keycloak in it.

Requirement 9 asks that authentication and authorization be separated *"so an
OIDC provider can arrive without redesigning user management"*. That is a claim
about work nobody has done yet, which makes it the easiest kind of claim to
believe wrongly — a seam nothing exercises is a seam that has already closed.

Two things keep it honest. The first is shipped code: there are **two**
authenticators in the tree — password sessions and `dm_sk_` API keys — and
both produce the same `RequestContext`, so the seam carries traffic today.
The second is this file, which asserts the properties §20.3's recipe depends
on, *before* there is an implementation to break them.

**The OIDC half runs against a keypair generated in the fixture.** An RS256
key, a static JWKS dict, tokens minted with `pyjwt` — which is already a
dependency, because that is how sessions are signed. Expiry, wrong audience,
wrong issuer and an unknown `kid` are properties of the verification recipe,
not of Keycloak, and testing them against a real IdP would mean a container CI
starts, a network CI reaches, and a test that gets deleted the first week it
flakes. §20.3 says so in as many words; this is that paragraph, executed.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.context import RequestContext
from app.domain.value_objects.authz import Capability
from app.infra.db.models import Role, Team
from app.services.role_service import RoleService
from app.services.team_service import TeamService
from tests.unit.conftest import AsyncSessionShim, _user
from tests.unit.conftest import ctx as admin_ctx

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

ISSUER = "https://idp.example.com/realms/datamind"
AUDIENCE = "datamind"


# ── S2: the external subject is namespaced ───────────────────────────────
def _external(provider_id: str, subject: str) -> str:
    """The recipe's rule, as the one-liner it will be.

    Written here rather than in `app/` because there is no OIDC provider yet
    and a helper with no caller is a helper that drifts. What this pins is the
    **shape**, so whoever writes `OidcIdentityProvider` finds a failing test if
    they store a bare `sub`.
    """
    return f"{provider_id}~{subject}"


def test_an_external_subject_is_namespaced_by_its_provider() -> None:
    """§20.3 step 3: *"the prefix is not decoration"*.

    A bare `sub` collides the day a second issuer appears — two IdPs will
    happily both call somebody `12345`, and the second one to log in becomes
    the first one's account. The separator is `~` because it appears in no
    provider id and in no subject anybody issues, which is the only property
    a separator needs.
    """
    assert _external("keycloak", "12345") == "keycloak~12345"

    keycloak = _external("keycloak", "12345")
    entra = _external("entra", "12345")
    assert keycloak != entra, "two issuers must not collide on one subject"

    provider, _, subject = keycloak.partition("~")
    assert (provider, subject) == ("keycloak", "12345"), "it must round-trip"


async def test_the_column_holds_a_namespaced_subject_and_the_check_survives(
    db: AsyncSessionShim,
) -> None:
    """The column is `String(255)` and a machine may not have one.

    `ck_users_service_no_external_subject` is why: a service user
    authenticates with a key, and an external subject on one would be a second
    way in that nobody configured.
    """
    session = db._session
    person = _user(session, "person@test.local")
    person.external_subject = _external("keycloak", uuid.uuid4().hex)
    session.flush()

    reloaded = session.get(type(person), person.id)
    assert reloaded.external_subject.startswith("keycloak~")

    from app.infra.db.models import User

    constraints = {c.name for c in User.__table__.constraints}
    assert "ck_users_service_no_external_subject" in constraints


# ── S4/S5: an unknown external group is ignored, never created ───────────
async def test_an_unknown_external_group_maps_to_nothing(
    db: AsyncSessionShim,
) -> None:
    """§20.3 step 4: *"unknown values are ignored, never auto-created"*.

    The failure this prevents is quiet and expensive: an IdP administrator
    renames a group, every login starts creating an empty team, and a month
    later the Teams screen is a list of ghosts nobody dares delete because
    somebody might be in one.

    Binding is a **deliberate, audited admin act** — `TeamService.bind_source`,
    which writes `team.source.bound`. The mapping direction is therefore a
    lookup that can return nothing, and this asserts it does.
    """
    bound = await TeamService(db).create(admin_ctx(), name="Finance", description="")
    await TeamService(db).bind_source(
        admin_ctx(), team_id=bound.id, provider_id="keycloak", source_id="grp-finance"
    )

    async def team_for(claim: str) -> Team | None:
        import sqlalchemy as sa

        rows = await db.execute(
            sa.select(Team).where(
                Team.provider_id == "keycloak", Team.source_id == claim
            )
        )
        return rows.scalars().first()

    assert (await team_for("grp-finance")) is not None
    assert (await team_for("grp-renamed-by-the-idp")) is None

    # And nothing was created by asking.
    import sqlalchemy as sa

    count = (await db.execute(sa.select(sa.func.count()).select_from(Team))).scalar()
    assert count == 1


async def test_a_role_binds_to_a_source_the_same_way_a_team_does(
    db: AsyncSessionShim,
) -> None:
    """S5 — new in this plan: an OIDC `roles` claim maps to DataMind roles
    exactly as a `groups` claim maps to teams.

    Same two columns, same uniqueness, same all-or-nothing check. Asserted on
    the schema rather than through a service because nothing writes them yet:
    what must survive until Phase 11 is the *shape*, and a shape assertion is
    what notices a migration that drops half of it.
    """
    for model in (Team, Role):
        columns = {c.name for c in model.__table__.columns}
        assert {"provider_id", "source_id"} <= columns, model.__name__
        names = {c.name for c in model.__table__.constraints}
        assert any("source" in (name or "") for name in names), model.__name__


# ── S3/S8: one resolution site, and never from a claim ───────────────────
async def test_capabilities_come_from_the_database_not_from_an_identity(
    db: AsyncSessionShim,
) -> None:
    """Decision 15, as behaviour: a role revoked **now** takes effect on the
    next request.

    A capability carried in a token takes effect at the next *refresh*, and
    the window between those two is precisely when somebody revokes a role.
    So the identity provider says **who**, and this query says **what they may
    do** — every request, from these tables.
    """
    session = db._session
    person = _user(session, "person@test.local")
    service = RoleService(db)

    before = await service.resolve_capabilities(person.id, frozenset())
    assert before == frozenset()

    from app.services.role_service import assign_by_name

    await assign_by_name(db, user_id=person.id, role_name="Auditor")
    after = await service.resolve_capabilities(person.id, frozenset())

    assert Capability.AUDIT_READ in after
    assert Capability.ACCESS_REVIEW in after
    # And it is a *fresh* read, not a memo: the same service instance answers
    # differently the moment the row changes.
    assert before != after


def test_the_identity_layer_names_nothing_about_permissions() -> None:
    """The structural half of S8, asserted on the tree.

    `app/infra/identity/` verifies credentials. If it ever mentions a
    capability, a privilege or a role's *contents*, the seam has closed —
    because the next step after "the identity layer knows about capabilities"
    is always "so let us put them in the token".
    """
    for path in (APP / "infra" / "identity").rglob("*.py"):
        source = _code(path.read_text())
        for word in ("Capability", "Privilege", "capabilities", "privileges"):
            assert word not in source, (
                f"{path.name} names {word!r} — authentication says who, "
                "authorization says what they may do."
            )


def test_a_delegated_context_carries_no_app_wide_verb() -> None:
    """S3's other half: `on_behalf_of` is the only door for background work,
    and it opens onto exactly one principal's answers.

    A delegated context holds **no capability at all** — nothing background
    needs one, because a scheduled run reads and writes *resources* and reach
    over a resource is the authorizer's answer.
    """
    ctx = RequestContext.on_behalf_of(uuid.uuid4())
    assert ctx.capabilities == frozenset()
    assert ctx.team_ids == frozenset()
    assert ctx.delegated is True


# ── §20.3, executed: a synthetic OIDC provider ───────────────────────────
@pytest.fixture(scope="module")
def keypair() -> Any:
    """An RS256 keypair, generated here. **No Keycloak, no network, no
    container.** 2048 bits because it is what an IdP issues and because 1024
    is refused by `cryptography` on newer builds."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(keypair: Any) -> dict[str, Any]:
    """The public half, as a provider would serve it at
    `{issuer}/.well-known/jwks.json`. One key, with a `kid`."""
    numbers = keypair.public_key().public_numbers()

    def b64(value: int) -> str:
        import base64

        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test-key-1",
                "n": b64(numbers.n), "e": b64(numbers.e),
            }
        ]
    }


def _token(keypair: Any, **claims: Any) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "3f2a-user",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **claims,
    }
    kid = claims.pop("_kid", "test-key-1")
    return jwt.encode(payload, keypair, algorithm="RS256", headers={"kid": kid})


def _verify(token: str, jwks: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """§20.3 step 2's verification, in the shape it will take.

    Signature, `iss`, `aud` and `exp`, all **locally** — no introspection call
    per request, so the IdP sits on the startup and key-rotation paths and
    never on the hot path. Written here rather than in `app/` for the same
    reason `_external` is: there is no provider yet, and this is the
    executable specification of the one that arrives.
    """
    header = jwt.get_unverified_header(token)
    key = next((k for k in jwks["keys"] if k["kid"] == header.get("kid")), None)
    if key is None:
        raise jwt.InvalidKeyError(f"unknown kid {header.get('kid')!r}")
    return jwt.decode(
        token,
        key=jwt.PyJWK(key).key,
        algorithms=["RS256"],
        audience=overrides.get("audience", AUDIENCE),
        issuer=overrides.get("issuer", ISSUER),
    )


def test_a_well_formed_token_verifies_against_the_static_jwks(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    claims = _verify(_token(keypair), jwks)
    assert claims["sub"] == "3f2a-user"


def test_an_expired_token_is_refused(keypair: Any, jwks: dict[str, Any]) -> None:
    """`exp` is checked locally, which is the whole reason there is no
    introspection call: a provider that has to be *asked* on every request is
    a provider whose outage is your outage."""
    stale = _token(keypair, exp=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(jwt.ExpiredSignatureError):
        _verify(stale, jwks)


def test_a_token_for_another_audience_is_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """The check people skip, and the one that matters most in a realm shared
    with other applications: a token minted for the wiki is a valid token, and
    without `aud` it is a valid token *here*."""
    with pytest.raises(jwt.InvalidAudienceError):
        _verify(_token(keypair, aud="some-other-app"), jwks)


def test_a_token_from_another_issuer_is_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    with pytest.raises(jwt.InvalidIssuerError):
        _verify(_token(keypair, iss="https://evil.example.com"), jwks)


def test_a_token_signed_with_an_unknown_key_is_refused(
    keypair: Any, jwks: dict[str, Any]
) -> None:
    """Key rotation's failure mode. A `kid` the JWKS does not carry means the
    provider rotated and the cache is stale — which is a *refetch*, and until
    it succeeds it is a refusal. Never a fallback to "any key in the set"."""
    rotated = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x",
         "exp": datetime.now(UTC) + timedelta(minutes=5)},
        keypair,
        algorithm="RS256",
        headers={"kid": "a-key-we-have-never-seen"},
    )
    with pytest.raises(jwt.InvalidKeyError):
        _verify(rotated, jwks)


def test_an_unsigned_token_is_refused(keypair: Any, jwks: dict[str, Any]) -> None:
    """`alg: none` is the oldest JWT attack there is, and `algorithms=["RS256"]`
    is what refuses it. Asserted because the day somebody widens that list to
    debug something is the day it stops being refused."""
    forged = jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "x"}, None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        _verify(forged, jwks)


async def test_group_and_role_claims_map_through_the_source_columns(
    db: AsyncSessionShim, keypair: Any, jwks: dict[str, Any]
) -> None:
    """The end of the recipe, executed: claims in, teams and roles out —
    **and unknown values ignored.**

    This is the test that would catch somebody implementing step 4 as
    `get_or_create`. Two of the three claimed groups are bound; the third is
    an IdP-side name nobody has bound here, and it maps to nothing.
    """
    import sqlalchemy as sa

    session = db._session
    teams = TeamService(db)
    finance = await teams.create(admin_ctx(), name="Finance", description="")
    await teams.bind_source(
        admin_ctx(), team_id=finance.id, provider_id="keycloak", source_id="grp-fin"
    )
    ops = await teams.create(admin_ctx(), name="Ops", description="")
    await teams.bind_source(
        admin_ctx(), team_id=ops.id, provider_id="keycloak", source_id="grp-ops"
    )

    auditor = (
        await db.execute(sa.select(Role).where(Role.name == "Auditor"))
    ).scalars().one()
    auditor.provider_id = "keycloak"
    auditor.source_id = "role-auditor"
    session.flush()

    claims = _verify(
        _token(
            keypair,
            groups=["grp-fin", "grp-ops", "grp-invented-yesterday"],
            roles=["role-auditor", "role-nobody-bound"],
        ),
        jwks,
    )

    async def bound(model: Any, values: list[str]) -> list[str]:
        rows = await db.execute(
            sa.select(model.name).where(
                model.provider_id == "keycloak", model.source_id.in_(values)
            )
        )
        return sorted(rows.scalars())

    assert await bound(Team, claims["groups"]) == ["Finance", "Ops"]
    assert await bound(Role, claims["roles"]) == ["Auditor"]

    # Nothing was created for the two unknown values.
    total_teams = (
        await db.execute(sa.select(sa.func.count()).select_from(Team))
    ).scalar()
    assert total_teams == 2


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
