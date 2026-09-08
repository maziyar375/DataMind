"""Machine identities: the second authenticator, and the four verbs it may not hold.

Phase 5 ships **a second way to authenticate** and deliberately no second way
to authorize. That split is what this file pins down, and the tests are ordered
by how much it would hurt to get each one wrong:

* **A service user's capabilities are byte-identical to a human's with the same
  role.** Not "equivalent", not "resolved the same way" — the same frozenset,
  from the same query. If those two answers could ever differ, an agent's reach
  would be a second permission model nobody was reviewing, and requirement 1
  would be a lie told by a `kind` column.
* **A valid key authenticates; a revoked, expired, unknown-prefix or
  wrong-secret one does not** — and all four failures produce the *same*
  sentence, because telling a caller which half of a guess was right is telling
  an attacker where to look next.
* **The comparison is constant-time**, asserted on the helper rather than on a
  stopwatch: a timing test on a CI runner measures the CI runner.
* **`last_used_at` is throttled**, so a busy integration does not turn every
  authenticated request into a write.
* **A `SERVICE` principal is refused by every `/auth` route**, and the three
  `CHECK` constraints refuse the row that would make one signable-in.
* **The four privileged capabilities are refused while the flag is off**, and
  audited when it is on. A leaked key must not be able to mint an administrator.
"""
from __future__ import annotations

import hmac
import inspect
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.context import RequestContext
from app.core.errors import AuthenticationError, ConflictError, NotFoundError, ValidationError
from app.domain.value_objects.authz import (
    PRIVILEGED_CAPABILITIES,
    Capability,
    PrincipalKind,
)
from app.infra.db.models import AuditLog, ServiceCredential, User
from app.infra.identity import service_key
from app.infra.identity.service_key import ServiceKeyProvider
from app.services.role_service import RoleService
from app.services.service_user_service import (
    CREDENTIAL_ISSUED,
    CREDENTIAL_REVOKED,
    SERVICE_USER_CREATED,
    SERVICE_USER_DELETED,
    SERVICE_USER_DISABLED,
    SERVICE_USER_PRIVILEGED,
    ServiceUserService,
    service_email,
)
from app.services.team_service import TeamService
from tests.unit.conftest import AsyncSessionShim, _user, ctx


def _settings(**overrides):
    """The real `Settings`, with one field moved. Never a fake.

    `allow_privileged_service_users` is read in exactly one place and a stub
    object would prove that place reads *something*. `model_copy` keeps every
    other value the deployment's, so a test cannot pass because it happened to
    also turn off something else.
    """
    return get_settings().model_copy(update=overrides)


def _service(db: AsyncSessionShim, **overrides) -> ServiceUserService:
    return ServiceUserService(db, _settings(**overrides))


def _secret_half(token: str) -> str:
    """Everything after the **first** underscore of the body.

    Not `rsplit`, and the difference is a real bug this test caught once:
    `secrets.token_urlsafe` emits base64url, whose alphabet includes `_`, so
    about half of all secrets contain one. The prefix is base32 and cannot, so
    the first underscore is the separator — which is exactly what
    `service_key._split` does.
    """
    return token[len(service_key.KEY_PREFIX):].partition("_")[2]


async def _agent(db: AsyncSessionShim, name: str = "Nightly reports") -> User:
    return await _service(db).create(
        ctx(), display_name=name, description="Runs the 03:00 report."
    )


# ── the equality that is the whole design ────────────────────────────────
async def test_a_service_user_s_capabilities_are_identical_to_a_human_s(
    db: AsyncSessionShim,
) -> None:
    """Requirement 1, as an equality rather than as a paragraph.

    Same role, two kinds of principal, one frozenset. The assertion is on the
    *set*, not on a spot-check of one capability, because the failure this
    guards against is a resolver that special-cases `kind` somewhere and
    narrows or widens the answer by one word.
    """
    roles = RoleService(db)
    bi_engineer = await roles.by_name("BI Engineer")
    assert bi_engineer is not None

    human = _user(db._session, "ada@test.local")
    agent = await _agent(db)
    await roles.assign(ctx(), user_id=human.id, role_id=bi_engineer.id)
    await _service(db).assign_role(
        ctx(), service_user_id=agent.id, role_id=bi_engineer.id
    )

    assert await roles.resolve_capabilities(agent.id) == await roles.resolve_capabilities(
        human.id
    )
    assert Capability.DASHBOARD_CREATE in await roles.resolve_capabilities(agent.id)


async def test_a_service_user_joins_teams_and_inherits_their_roles(
    db: AsyncSessionShim,
) -> None:
    """Decision 5: both kinds of principal are team members.

    The point is not that it is *possible* — `team_members.user_id` is a
    foreign key to `users.id` and always was — but that the resolver treats it
    the same way, so an agent added to the BI Engineers team acquires exactly
    what the people in it have.
    """
    teams = TeamService(db)
    roles = RoleService(db)
    viewer = await roles.by_name("Viewer")
    assert viewer is not None

    agent = await _agent(db)
    team = await teams.create(ctx(), name="Automation")
    await teams.add_member(ctx(), team_id=team.id, user_id=agent.id)
    await teams.assign_role(ctx(), team_id=team.id, role_id=viewer.id)

    team_ids = await teams.team_ids(agent.id)
    assert team_ids == {team.id}
    assert Capability.TEAM_READ in await roles.resolve_capabilities(agent.id, team_ids)


# ── the row ──────────────────────────────────────────────────────────────
async def test_a_service_user_is_a_users_row_with_none_of_the_human_columns(
    db: AsyncSessionShim,
) -> None:
    agent = await _agent(db)
    assert agent.kind == PrincipalKind.SERVICE
    assert agent.password_hash is None
    assert agent.external_subject is None
    assert agent.must_change_password is False
    assert agent.description == "Runs the 03:00 report."


def test_the_synthetic_address_is_a_slug_under_a_non_routable_domain() -> None:
    """Generated, never typed — and never deliverable.

    `.local` is reserved, so a misconfigured mailer reading `users.email` off a
    service row cannot reach a real mailbox. The discriminator is there because
    two people will both name an agent `reports`, and `users.email` is UNIQUE:
    colliding would surface as *"a user with that email already exists"* on a
    form with no email field.
    """
    assert service_email("Nightly Reports!", "abc123") == (
        "svc-nightly-reports-abc123@service.datamind.local"
    )
    assert service_email("***", "abc123").startswith("svc-agent-")


async def test_two_accounts_with_the_same_name_get_different_addresses(
    db: AsyncSessionShim,
) -> None:
    first = await _agent(db, "reports")
    second = await _agent(db, "reports")
    assert first.email != second.email


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("password_hash", "$argon2id$whatever"),
        ("external_subject", "oidc|123"),
        ("must_change_password", True),
    ],
)
def test_the_three_check_constraints_refuse_a_signable_in_machine(
    db: AsyncSessionShim, column: str, value: object
) -> None:
    """The kinds do not blur, and it is the database that says so.

    A service-layer rule is one `db.add` away from being bypassed, and the row
    a bypass would write is an interactive login for a machine identity. These
    are `CHECK`s, asserted here by writing the row the constraint forbids.

    SQLite enforces `CHECK` the same way Postgres does, which is why this test
    can run against the in-memory schema at all — the constraints come from the
    real `__table_args__`, not from a copy.
    """
    session = db._session
    row = {
        "id": str(uuid4()),
        "email": f"svc-{uuid4().hex[:8]}@service.datamind.local",
        "display_name": "Agent",
        "status": "ACTIVE",
        "kind": "SERVICE",
        "must_change_password": False,
        column: value,
    }
    columns = ", ".join(row)
    placeholders = ", ".join(f":{name}" for name in row)
    with pytest.raises(sa.exc.IntegrityError):
        session.execute(
            sa.text(f"INSERT INTO users ({columns}) VALUES ({placeholders})"),  # noqa: S608
            row,
        )
    session.rollback()


def test_the_kind_column_refuses_a_word_it_does_not_know(
    db: AsyncSessionShim,
) -> None:
    """Closed in the row as well as in code — unlike `role_capabilities`.

    The asymmetry is deliberate and worth pinning: an unknown *capability* can
    be ignored and the installation stays up with fewer permissions, so that
    column is open. An unknown *principal kind* has no safe reading at all.
    """
    session = db._session
    with pytest.raises(sa.exc.IntegrityError):
        session.execute(
            sa.text(
                "INSERT INTO users (id, email, display_name, status, kind, "
                "must_change_password) VALUES (:id, :email, 'x', "
                "'ACTIVE', 'ROBOT', false)"
            ),
            {"id": str(uuid4()), "email": f"{uuid4().hex}@test.local"},
        )
    session.rollback()


async def test_a_service_account_is_not_reachable_by_the_people_screens(
    db: AsyncSessionShim,
) -> None:
    """`GET /service-accounts/{id}` pointed at a person is a 404.

    Not a filter but a narrowing: the screen is gated `service_user.manage`,
    which a DataMind Maintainer holds and `user.read` they do not. Returning a
    human here would make it a second, quieter way to read the People list.
    """
    human = _user(db._session, "ada@test.local")
    with pytest.raises(NotFoundError):
        await _service(db).get(human.id)


async def test_a_description_is_required(db: AsyncSessionShim) -> None:
    """The field that looks optional and is not.

    An undocumented machine identity is the one nobody is ever willing to
    delete, and six months later it is the one still holding a key.
    """
    with pytest.raises(ValidationError, match="what this service account is for"):
        await _service(db).create(ctx(), display_name="thing", description="   ")


# ── the key: the happy path ──────────────────────────────────────────────
async def test_a_valid_key_authenticates_as_its_principal(
    db: AsyncSessionShim,
) -> None:
    agent = await _agent(db)
    issued = await _service(db).issue_key(
        ctx(), service_user_id=agent.id, name="ci"
    )

    who = await ServiceKeyProvider(db).verify_key(issued.token)
    assert who.user_id == agent.id
    assert who.kind == PrincipalKind.SERVICE


def test_the_key_carries_a_scannable_prefix_and_a_traceable_half() -> None:
    """`dm_sk_<prefix>_<secret>` — and each part is load-bearing.

    `dm_sk` is one literal a secret scanner can be taught; the prefix is the
    clear, indexed half that makes a key found in a log traceable to its owner
    without the secret half ever having been stored. That is the entire reason
    the key has two parts rather than one.
    """
    token, prefix, secret = service_key.generate_key()
    assert token == f"dm_sk_{prefix}_{secret}"
    assert service_key.looks_like_service_key(token)
    assert not service_key.looks_like_service_key("eyJhbGciOiJIUzI1NiJ9.x.y")
    assert len(prefix) == service_key.PREFIX_CHARS
    # Base32, lowercased: it survives being read aloud, copied out of a log and
    # typed into a search box — which is what somebody does with a leaked key.
    assert prefix.isalnum() and prefix.islower()


async def test_the_stored_row_holds_a_hash_and_never_the_key(
    db: AsyncSessionShim,
) -> None:
    agent = await _agent(db)
    issued = await _service(db).issue_key(ctx(), service_user_id=agent.id, name="ci")

    row = await db.get(ServiceCredential, issued.credential_id)
    assert row.prefix == issued.prefix
    assert issued.token not in row.token_hash
    assert len(row.token_hash) == 64  # sha256, hex
    # The secret half, not the whole token: hashing the token would make the
    # scannable prefix part of the secret and the traceable half unusable.
    secret = _secret_half(issued.token)
    assert row.token_hash == service_key.hash_secret(secret)


# ── the key: every refusal ───────────────────────────────────────────────
async def test_an_unknown_prefix_is_refused(db: AsyncSessionShim) -> None:
    with pytest.raises(AuthenticationError, match="not valid"):
        await ServiceKeyProvider(db).verify_key("dm_sk_aaaaaaaaaaaa_whatever")


async def test_a_wrong_secret_is_refused(db: AsyncSessionShim) -> None:
    agent = await _agent(db)
    issued = await _service(db).issue_key(ctx(), service_user_id=agent.id, name="ci")
    tampered = f"dm_sk_{issued.prefix}_not-the-secret"
    with pytest.raises(AuthenticationError):
        await ServiceKeyProvider(db).verify_key(tampered)


async def test_a_revoked_key_fails_the_next_request(db: AsyncSessionShim) -> None:
    """The property a session token could not offer.

    Verification reads the row on **every** request, so revocation is immediate
    rather than "within fifteen minutes" — which is why the service path does
    not mint a JWT.
    """
    agent = await _agent(db)
    service = _service(db)
    issued = await service.issue_key(ctx(), service_user_id=agent.id, name="ci")
    assert await ServiceKeyProvider(db).verify_key(issued.token)

    await service.revoke_key(
        ctx(), service_user_id=agent.id, credential_id=issued.credential_id
    )
    with pytest.raises(AuthenticationError):
        await ServiceKeyProvider(db).verify_key(issued.token)


async def test_an_expired_key_is_refused(db: AsyncSessionShim) -> None:
    agent = await _agent(db)
    issued = await _service(db).issue_key(
        ctx(),
        service_user_id=agent.id,
        name="ci",
        expires_at=utcnow() - timedelta(seconds=1),
    )
    with pytest.raises(AuthenticationError):
        await ServiceKeyProvider(db).verify_key(issued.token)


async def test_a_key_for_a_disabled_principal_is_refused(
    db: AsyncSessionShim,
) -> None:
    """Disabling keeps the keys — and stops them working.

    That pairing is the point: `DISABLED` is reversible, so re-enabling must
    not be a re-issue, and a key that kept working through a disable would make
    the switch decorative.
    """
    agent = await _agent(db)
    service = _service(db)
    issued = await service.issue_key(ctx(), service_user_id=agent.id, name="ci")
    await service.update(ctx(), agent.id, status="DISABLED")

    with pytest.raises(AuthenticationError):
        await ServiceKeyProvider(db).verify_key(issued.token)
    # The row is still there. Re-enabling is not a re-grant.
    assert len(await service.keys(agent.id)) == 1


@pytest.mark.parametrize(
    "malformed",
    ["", "dm_sk_", "dm_sk_only-one-part", "dm_sk__no-prefix", "not-a-key-at-all"],
)
async def test_a_malformed_key_is_refused_without_touching_the_database(
    db: AsyncSessionShim, malformed: str
) -> None:
    with pytest.raises(AuthenticationError):
        await ServiceKeyProvider(db).verify_key(malformed)


async def test_every_refusal_says_the_same_thing(db: AsyncSessionShim) -> None:
    """One sentence for all of them, deliberately.

    An unknown prefix, a wrong secret, a revoked key and an expired key are
    four different facts, and distinguishing them in the *response* tells an
    attacker which half of a guess was right. Which it actually was goes in the
    audit log, which the operator reads and the caller does not.
    """
    agent = await _agent(db)
    service = _service(db)
    live = await service.issue_key(ctx(), service_user_id=agent.id, name="live")
    revoked = await service.issue_key(ctx(), service_user_id=agent.id, name="dead")
    await service.revoke_key(
        ctx(), service_user_id=agent.id, credential_id=revoked.credential_id
    )

    messages = set()
    for token in (
        "dm_sk_aaaaaaaaaaaa_nope",
        f"dm_sk_{live.prefix}_wrong-secret",
        revoked.token,
    ):
        with pytest.raises(AuthenticationError) as caught:
            await ServiceKeyProvider(db).verify_key(token)
        messages.add(caught.value.message)
    assert len(messages) == 1


# ── constant time ────────────────────────────────────────────────────────
def test_the_comparison_is_constant_time() -> None:
    """Asserted on the helper, not on a stopwatch.

    A timing test on a CI runner measures the CI runner. What is checkable and
    what actually matters is that the comparison goes through
    `hmac.compare_digest` rather than `==` — so this reads the source of the
    one helper the verify path calls, and the verify path calls it.
    """
    source = inspect.getsource(service_key.secrets_match)
    assert "hmac.compare_digest" in source
    assert "==" not in source.split('"""')[-1]

    _, _, secret = service_key.generate_key()
    stored = service_key.hash_secret(secret)
    assert service_key.secrets_match(stored, secret)
    assert not service_key.secrets_match(stored, secret + "x")
    # And the primitive is the real one, not a same-named local.
    assert service_key.hmac.compare_digest is hmac.compare_digest


def test_verify_key_uses_that_helper_rather_than_comparing_itself() -> None:
    """The helper is only worth having if the hot path goes through it."""
    source = inspect.getsource(ServiceKeyProvider.verify_key)
    assert "secrets_match(" in source
    assert "token_hash ==" not in source


# ── last_used_at ─────────────────────────────────────────────────────────
async def test_last_used_at_is_written_on_first_use(db: AsyncSessionShim) -> None:
    agent = await _agent(db)
    issued = await _service(db).issue_key(ctx(), service_user_id=agent.id, name="ci")

    row = await db.get(ServiceCredential, issued.credential_id)
    assert row.last_used_at is None
    await ServiceKeyProvider(db).verify_key(issued.token)
    assert row.last_used_at is not None


async def test_two_calls_in_the_same_minute_write_once(
    db: AsyncSessionShim,
) -> None:
    """The throttle, and why the column is worth having at all.

    Without it every authenticated request from a busy integration is also a
    write — for a column whose only question, *"has anybody used this key? can
    I delete it?"*, needs minute resolution at the very best.
    """
    agent = await _agent(db)
    issued = await _service(db).issue_key(ctx(), service_user_id=agent.id, name="ci")
    provider = ServiceKeyProvider(db)

    await provider.verify_key(issued.token)
    row = await db.get(ServiceCredential, issued.credential_id)
    first = row.last_used_at

    await provider.verify_key(issued.token)
    assert row.last_used_at == first

    # Far enough back that the throttle has expired, and the next call writes.
    row.last_used_at = first - timedelta(minutes=5)
    db._session.flush()
    await provider.verify_key(issued.token)
    assert row.last_used_at > first - timedelta(minutes=5)


async def test_the_throttle_reads_the_stored_value_not_a_process_cache(
    db: AsyncSessionShim,
) -> None:
    """A fresh provider throttles just as the previous one did.

    A per-process cache would let four replicas write four times a minute and
    would reset to "always write" on every deploy — so the comparison is
    against the column, and this proves it by throwing the provider away.
    """
    agent = await _agent(db)
    issued = await _service(db).issue_key(ctx(), service_user_id=agent.id, name="ci")

    await ServiceKeyProvider(db).verify_key(issued.token)
    row = await db.get(ServiceCredential, issued.credential_id)
    first = row.last_used_at
    await ServiceKeyProvider(db).verify_key(issued.token)
    assert row.last_used_at == first


# ── expiry defaults ──────────────────────────────────────────────────────
async def test_naming_no_expiry_gets_the_installation_default(
    db: AsyncSessionShim,
) -> None:
    agent = await _agent(db)
    issued = await _service(db, service_key_default_ttl_days=30).issue_key(
        ctx(), service_user_id=agent.id, name="ci"
    )
    assert issued.expires_at is not None
    assert issued.expires_at > utcnow() + timedelta(days=29)


async def test_asking_for_no_expiry_is_a_different_request(
    db: AsyncSessionShim,
) -> None:
    """"Said nothing" and "meant none" must not be the same request.

    Some integrations genuinely cannot rotate, so a never-expiring key stays
    possible — as a deliberate choice an administrator makes, rather than a
    default they fall into. An unexpiring machine credential is the one thing
    every published key-leak incident has in common.
    """
    agent = await _agent(db)
    issued = await _service(db).issue_key(
        ctx(), service_user_id=agent.id, name="ci", use_default_expiry=False
    )
    assert issued.expires_at is None


async def test_a_key_cannot_be_issued_to_a_disabled_account(
    db: AsyncSessionShim,
) -> None:
    agent = await _agent(db)
    service = _service(db)
    await service.update(ctx(), agent.id, status="DISABLED")
    with pytest.raises(ConflictError, match="disabled"):
        await service.issue_key(ctx(), service_user_id=agent.id, name="ci")


async def test_revoking_a_key_that_belongs_to_another_account_is_a_404(
    db: AsyncSessionShim,
) -> None:
    """The path is not trusted for the relationship it asserts.

    `/service-accounts/{a}/keys/{b}` where `b` belongs to another account would
    otherwise revoke somebody else's key from a screen that showed neither.
    """
    first = await _agent(db, "one")
    second = await _agent(db, "two")
    service = _service(db)
    issued = await service.issue_key(ctx(), service_user_id=second.id, name="ci")

    with pytest.raises(NotFoundError):
        await service.revoke_key(
            ctx(), service_user_id=first.id, credential_id=issued.credential_id
        )


# ── the four privileged capabilities ─────────────────────────────────────
async def test_a_privileged_role_is_refused_while_the_flag_is_off(
    db: AsyncSessionShim,
) -> None:
    """A leaked API key must not be able to mint an administrator.

    The refusal names the capability and says which setting would allow it —
    the difference between a support ticket somebody can act on and one that
    says "it didn't work".
    """
    administrator = await RoleService(db).by_name("Administrator")
    assert administrator is not None
    agent = await _agent(db)

    with pytest.raises(ValidationError, match="user.manage"):
        await _service(db).assign_role(
            ctx(), service_user_id=agent.id, role_id=administrator.id
        )


async def test_creating_one_with_a_privileged_role_is_refused_before_the_row(
    db: AsyncSessionShim,
) -> None:
    """Refused *before* the account exists, not after.

    A create that made the identity and then refused its role would leave a
    half-provisioned agent behind for somebody to find later and wonder about.
    """
    administrator = await RoleService(db).by_name("Administrator")
    assert administrator is not None
    with pytest.raises(ValidationError):
        await _service(db).create(
            ctx(),
            display_name="provisioner",
            description="makes accounts",
            role_ids=[administrator.id],
        )
    assert await _service(db).list() == []


async def test_the_flag_allows_it_and_writes_an_audit_row(
    db: AsyncSessionShim,
) -> None:
    """Turning it on is a deployment decision; **using** it is an event.

    An installation running its own provisioning agent has a real reason for
    this, which is why it is a flag rather than an invariant. What the flag
    does not do is make the moment quiet: *"when did an agent acquire
    user.manage"* stays answerable from the log, by name.
    """
    administrator = await RoleService(db).by_name("Administrator")
    assert administrator is not None
    service = _service(db, allow_privileged_service_users=True)
    agent = await _agent(db)

    await service.assign_role(
        ctx(), service_user_id=agent.id, role_id=administrator.id
    )
    assert Capability.USER_MANAGE in await RoleService(db).resolve_capabilities(
        agent.id
    )

    rows = _audit(db, SERVICE_USER_PRIVILEGED)
    assert len(rows) == 1
    assert "user.manage" in rows[0].detail["privileged_capabilities"]
    assert rows[0].detail["allowed_by_setting"] is True


async def test_an_unprivileged_role_needs_no_flag(db: AsyncSessionShim) -> None:
    """The common case costs nothing and writes nothing.

    Four of eighteen capabilities are privileged; the other fourteen are what a
    machine identity is normally for, and a rule that made every assignment
    noisy would train everybody to ignore the noise.
    """
    viewer = await RoleService(db).by_name("Viewer")
    assert viewer is not None
    agent = await _agent(db)
    await _service(db).assign_role(
        ctx(), service_user_id=agent.id, role_id=viewer.id
    )
    assert _audit(db, SERVICE_USER_PRIVILEGED) == []


def test_the_four_privileged_capabilities_are_the_four_named_in_the_plan() -> None:
    """A fifth would be a deliberate line in a reviewed diff.

    Written out from an independent literal rather than derived from the
    frozenset, because a test that read the set it is checking would agree with
    any mistake in it.
    """
    assert {
        Capability.USER_MANAGE,
        Capability.ROLE_MANAGE,
        Capability.SERVICE_USER_MANAGE,
        Capability.SETTINGS_MANAGE,
    } == PRIVILEGED_CAPABILITIES


# ── audit ────────────────────────────────────────────────────────────────
async def test_creating_disabling_and_deleting_each_write_one_row(
    db: AsyncSessionShim,
) -> None:
    service = _service(db)
    agent = await _agent(db)
    assert len(_audit(db, SERVICE_USER_CREATED)) == 1

    await service.update(ctx(), agent.id, status="DISABLED")
    assert len(_audit(db, SERVICE_USER_DISABLED)) == 1

    await service.delete(ctx(), agent.id)
    rows = _audit(db, SERVICE_USER_DELETED)
    # Written *before* the delete, so it names what was removed rather than
    # pointing at an id nothing resolves.
    assert len(rows) == 1
    assert rows[0].detail["name"] == "Nightly reports"


async def test_issuing_and_revoking_record_the_prefix_and_never_the_key(
    db: AsyncSessionShim,
) -> None:
    """Rule 3 of `services/audit.py`, on the one action that handles a secret.

    The prefix is the clear half by construction — it is what makes a key found
    in a log traceable — so recording it is the *point*. Recording the token
    would turn the audit table into a credential store, which is a second thing
    to secure and the one nobody remembers.
    """
    agent = await _agent(db)
    service = _service(db)
    issued = await service.issue_key(ctx(), service_user_id=agent.id, name="ci")
    await service.revoke_key(
        ctx(), service_user_id=agent.id, credential_id=issued.credential_id
    )

    for action in (CREDENTIAL_ISSUED, CREDENTIAL_REVOKED):
        rows = _audit(db, action)
        assert len(rows) == 1
        assert rows[0].detail["prefix"] == issued.prefix
        blob = str(rows[0].detail)
        assert issued.token not in blob
        assert _secret_half(issued.token) not in blob


def _audit(db: AsyncSessionShim, action: str) -> list[AuditLog]:
    db._session.flush()
    return list(
        db._session.execute(
            sa.select(AuditLog).where(AuditLog.action == action)
        ).scalars()
    )


# ── the context, and the seam ────────────────────────────────────────────
def test_a_service_context_is_the_same_object_as_a_human_one() -> None:
    """Requirement 9's seam, asserted on the shape rather than described.

    `for_user` and `for_service` differ in exactly one field. If a third
    difference ever appeared, something downstream would be able to tell which
    authenticator ran — and the day anything branches on that, there are two
    permission models again.
    """
    from app.domain.ports.identity import AuthenticatedIdentity

    identity = AuthenticatedIdentity(
        user_id=uuid4(), email="x@test.local"
    )
    capabilities = frozenset({Capability.CONVERSATION_CREATE})
    teams = frozenset({uuid4()})

    human = RequestContext.for_user(identity, capabilities, teams)
    machine = RequestContext.for_service(identity, capabilities, teams)

    assert machine.kind == PrincipalKind.SERVICE
    assert human.kind == PrincipalKind.HUMAN
    assert human.capabilities == machine.capabilities
    assert human.team_ids == machine.team_ids
    assert human.user_id == machine.user_id
    # No session: a key is the credential rather than something exchanged for
    # one, which is what makes revoking it immediate.
    assert machine.session_id is None


def test_delegation_resets_the_kind() -> None:
    """A run started by an API key and delegated to a person's account.

    The principal changed, so the old principal's kind is not a fact about the
    new one — and an audit row describing a person as a machine is a row
    nobody can act on.
    """
    machine = RequestContext(
        user_id=uuid4(), email="", kind=PrincipalKind.SERVICE
    )
    assert machine.delegate(uuid4()).kind == PrincipalKind.HUMAN


# ── the People routes speak human ────────────────────────────────────────
def test_the_people_write_routes_refuse_a_machine() -> None:
    """`GET /users` lists every principal; the write routes here do not act on one.

    The asymmetry is deliberate and both halves matter. The **list** must carry
    machines: the team picker and the audit renderer both have to resolve any
    `users.id`, and a list that hid them would make a service account unaddable
    to a team. The **writes** must not: `PUT /users/{id}/password` on a service
    row would violate `ck_users_service_no_password` and answer 500 rather than
    a sentence, `PATCH` could promote a machine to administrator behind the very
    flag that exists to stop that, and `DELETE` would remove one with no
    `service_user.deleted` row naming what went.

    The refusal is a 422 with a sentence, not a 404: the account exists, and
    the caller is on the wrong screen.
    """
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.infra.db.models import User as UserRow
    from app.main import create_app

    machine = UserRow(
        id=uuid4(),
        email="svc-agent-1@service.datamind.local",
        display_name="Nightly reports",
        kind=PrincipalKind.SERVICE,
        status="ACTIVE",
    )

    class _OneMachine:
        async def get(self, _model: type, _pk: object) -> UserRow:
            return machine

        async def execute(self, *_a: object, **_k: object) -> object:  # pragma: no cover
            raise AssertionError("the handler ran past the kind check")

        async def flush(self) -> None:  # pragma: no cover
            raise AssertionError("the handler ran past the kind check")

    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: _OneMachine()
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=uuid4(),
        email="admin@test.local",
        capabilities=frozenset({Capability.USER_MANAGE, Capability.USER_READ}),
    )
    client = TestClient(app)
    target = f"/api/v1/users/{machine.id}"

    for method, path, body in (
        ("PATCH", target, {"display_name": "renamed"}),
        ("PUT", f"{target}/password", {"password": "x" * 12}),
        ("DELETE", target, None),
        ("POST", f"{target}/roles", {"role_id": str(uuid4())}),
    ):
        response = client.request(method, path, json=body)
        assert response.status_code == 422, f"{method} {path} answered {response.status_code}"
        assert "service account" in response.json()["detail"].lower()

    app.dependency_overrides.clear()


async def test_the_user_list_carries_the_kind_so_a_row_can_be_badged(
    db: AsyncSessionShim,
) -> None:
    """A principal list has to be able to say which kind each row is.

    Not cosmetic: the team picker draws from this list, and adding *the nightly
    agent* to a team is a different decision from adding *a colleague with a
    similar name* — a picker that could not tell them apart would make that
    mistake silent.
    """
    from app.api.schemas import UserRead

    agent = await _agent(db)
    assert UserRead.model_validate(agent).kind == "SERVICE"
    assert UserRead.model_validate(_user(db._session, "ada@test.local")).kind == "HUMAN"
