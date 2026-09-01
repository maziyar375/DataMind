"""Phase 8 — who may curate, whose queue a flag lands in, and the record of it.

Three claims, and each one is a thing that quietly rots if it is not asserted:

* **`curation_admin_only` is on, and it did not lock anybody out.** The flip is
  admin **or owner**. Without the second half it would mean the person who owns
  a connection cannot curate their own store — `_owned()` already scopes every
  knowledge endpoint to the owner, and an administrator cannot reach somebody
  else's connection either, so admin-only alone takes rights away and grants
  none. That is a lockout, not a posture.
* **A flag is routed to the connection's owner**, and the server says whose
  queue it went to rather than the SPA guessing.
* **Every curation write leaves a row**, and that row carries identifiers and
  counts — never SQL, never question text. An audit log that became a second
  copy of the store would be a second thing to secure and the one place
  somebody forgets to.

The failure this phase exists to prevent is Microsoft's, documented: a store of
business logic whose provenance nobody can establish.
"""
from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.context import RequestContext
from app.services import audit
from app.services.policy import can_curate

OWNER = uuid4()
STRANGER = uuid4()


def ctx(user_id=OWNER, role: str = "MEMBER") -> RequestContext:
    return RequestContext(
        user_id=user_id, email="u@test.local", role=role, correlation_id="t"
    )


class _Connection:
    def __init__(self, owner_id=OWNER) -> None:
        self.owner_id = owner_id


OPEN = Settings(curation_admin_only=False)
CLOSED = Settings(curation_admin_only=True)


# ── who may curate ───────────────────────────────────────────────────────
def test_the_flag_is_on_by_default() -> None:
    """Phase 8's first box. Curation writes business logic that answers
    questions on other people's behalf, and user management now exists to
    express that as a privilege."""
    assert Settings().curation_admin_only is True


def test_with_the_flag_off_anyone_signed_in_may_curate() -> None:
    """The single-player install's setting, unchanged and still supported."""
    assert can_curate(ctx(), OPEN) is True
    assert can_curate(ctx(STRANGER), OPEN, _Connection()) is True


def test_an_administrator_may_curate() -> None:
    assert can_curate(ctx(STRANGER, role="ADMIN"), CLOSED, _Connection()) is True


def test_the_owner_may_curate_their_own_connection() -> None:
    """The half that makes the flip correct rather than merely done."""
    assert can_curate(ctx(), CLOSED, _Connection(owner_id=OWNER)) is True


def test_a_stranger_may_not() -> None:
    """Inert today — `_owned()` answers 404 first — and the whole point of
    turning the flag on *before* sharing exists rather than after: when a
    connection can be shared, a reader may ask it questions and may not rewrite
    what it has been taught."""
    assert can_curate(ctx(STRANGER), CLOSED, _Connection(owner_id=OWNER)) is False


def test_no_resource_asks_the_strict_question() -> None:
    """Fail closed: the honest reading of "I cannot establish who owns this"
    is *no*, not *probably fine*."""
    assert can_curate(ctx(), CLOSED) is False
    assert can_curate(ctx(role="ADMIN"), CLOSED) is True


def test_a_resource_with_no_owner_is_not_owned_by_anybody() -> None:
    assert can_curate(ctx(), CLOSED, object()) is False


# ── the audit writer ─────────────────────────────────────────────────────
class _FakeDb:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)


@pytest.mark.asyncio
async def test_an_audit_row_carries_the_actor_the_action_and_the_resource() -> None:
    db = _FakeDb()
    target = uuid4()
    row = await audit.record(
        db, ctx(),
        action=audit.TEMPLATE_CREATED,
        resource_type=audit.TEMPLATE,
        resource_id=target,
        detail={"source": "MANUAL"},
    )
    assert row is not None
    assert row.actor_user_id == OWNER
    assert row.action == audit.TEMPLATE_CREATED
    assert row.resource_id == target
    assert row.outcome == audit.SUCCESS
    assert db.added == [row]


def test_the_row_joins_the_callers_transaction_and_is_never_flushed() -> None:
    """Rule 1. A log that can commit while the action it describes rolls back
    is a log that invents history — so `record` only ever calls `add`.

    Asserted on the parse, because a `flush` added later would be invisible to
    a fake that does not implement one.
    """
    tree = ast.parse(Path("app/services/audit.py").read_text())
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "add" in called
    assert "flush" not in called
    assert "commit" not in called


@pytest.mark.asyncio
async def test_failing_to_log_never_fails_the_action() -> None:
    """Rule 2, and the opposite posture to the guard's — deliberately. A
    curator must not lose a saved template to a full disk on the audit table.
    """
    class _Broken:
        def add(self, row):
            raise RuntimeError("the audit table is gone")

    assert await audit.record(_Broken(), ctx(), action="x") is None


@pytest.mark.asyncio
async def test_the_ip_is_recorded_when_there_is_one_and_null_when_there_is_not() -> None:
    db = _FakeDb()
    with_ip = await audit.record(
        db, RequestContext(user_id=OWNER, email="e", role="MEMBER", actor_ip="10.0.0.4"),
        action="x",
    )
    without = await audit.record(db, ctx(), action="x")
    assert with_ip is not None and with_ip.actor_ip == "10.0.0.4"
    # `None`, not `""` — the column is nullable for exactly this case, and an
    # empty string would sort and filter as though it were an address.
    assert without is not None and without.actor_ip is None


@pytest.mark.asyncio
async def test_detail_carries_identifiers_and_counts_never_content() -> None:
    """Rule 3, enforced in one place rather than trusted at ten call sites."""
    db = _FakeDb()
    row = await audit.record(
        db, ctx(), action="x",
        detail={
            "count": 4,
            "enabled": True,
            "nothing": None,
            "sql": "SELECT " + "x" * 5_000,
            "ids": [str(uuid4()) for _ in range(50)],
        },
    )
    assert row is not None
    assert row.detail["count"] == 4
    assert row.detail["enabled"] is True
    assert row.detail["nothing"] is None
    assert len(row.detail["sql"]) == audit.MAX_DETAIL_CHARS
    assert len(row.detail["ids"]) == 20


@pytest.mark.asyncio
async def test_an_over_long_action_is_capped_to_the_column() -> None:
    """`action` is `String(60)`. A value that would raise on insert would fail
    the *curation write* it is describing, which is rule 2 violated by a
    truncation nobody did."""
    db = _FakeDb()
    row = await audit.record(db, ctx(), action="k." + "x" * 200)
    assert row is not None and len(row.action) == 60


# ── the vocabulary, and the reason it is a vocabulary ────────────────────
def test_every_audited_action_is_namespaced() -> None:
    """An admin filtering the log should be able to ask for "everything the
    learning loop did" with a prefix rather than a list."""
    actions = [
        v for k, v in vars(audit).items()
        if k.isupper() and isinstance(v, str) and "." in v
    ]
    assert actions
    assert all(a.startswith("knowledge.") for a in actions)


def test_every_curation_write_path_records_one() -> None:
    """The box, asserted on the parse rather than by counting call sites by
    hand: each of these route functions must mention `audit.record`.

    A route added later that writes curation and forgets this is the failure
    mode — one unlogged write is enough to make the log untrustworthy, because
    a reader cannot tell a gap from an absence of activity.
    """
    tree = ast.parse(Path("app/api/v1/knowledge.py").read_text())
    audited = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "record"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "audit"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )
    }
    for name in (
        "create_template",
        "update_template",
        "archive_template",
        "revalidate_store",
        "set_embedding_search",
        "resolve_review",
        "create_benchmark",
        "delete_benchmark",
        "run_benchmark",
    ):
        assert name in audited, f"{name} writes curation and logs nothing"


def test_the_audit_reader_is_administrators_only() -> None:
    """An audit log is a record *about people*. A curator has an operational
    need to change their connection's knowledge and none to read who else did
    what, and from where."""
    tree = ast.parse(Path("app/api/v1/audit.py").read_text())
    routes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(d, ast.Call | ast.Attribute)
            for d in node.decorator_list
        )
    ]
    assert routes
    for route in routes:
        annotations = {
            ast.unparse(a.annotation) for a in route.args.args if a.annotation
        }
        assert "AdminDep" in annotations, route.name
