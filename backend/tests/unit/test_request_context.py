"""A context always names a principal, and background work says whose.

Phase 2 of `docs/user-management-and-access-control-plan.md`. The claim the
whole plan rests on is that `owner_id` is a *fact stored on a row* and nothing
in `api/` or `services/` reads it to decide anything — which only holds if
every call carries a principal to decide *about*. Three properties keep that
true, and each has a way it used to fail:

* **There is no context with no principal.** `ctx=None` was the spelling, and a
  decision made on nobody's behalf is one no authorizer can check.
* **`on_behalf_of` is the only way a worker gets one**, so "the scheduler ran
  Sara's report" and "Sara pressed run" are different rows in `audit_logs`
  rather than the same row twice.
* **A delegated context is not privileged.** It is the principal, exactly — no
  more — so a scheduled run that its owner could not perform by hand fails.
"""
from __future__ import annotations

import ast
import pathlib
from uuid import uuid4

import pytest

from app.core.context import RequestContext, set_correlation_id
from app.domain.value_objects.authz import Capability
from app.services import audit

WORKERS = pathlib.Path(__file__).resolve().parents[2] / "app" / "workers"


def test_a_context_cannot_be_built_without_a_principal() -> None:
    """The dataclass, not a convention, is what refuses.

    `user_id` has no default and never gets one. A default here — even `None` —
    would make `ctx=None` expressible again through the front door.
    """
    with pytest.raises(TypeError):
        RequestContext()  # type: ignore[call-arg]


def test_on_behalf_of_names_the_principal_and_marks_the_delegation() -> None:
    owner = uuid4()

    ctx = RequestContext.on_behalf_of(owner)

    assert ctx.user_id == owner
    assert ctx.delegated is True


def test_a_delegated_context_holds_no_app_wide_verb_at_all() -> None:
    """A worker looked nothing up, and "we did not look it up" reads as no.

    This used to assert `is_admin is False`, which was the weaker half of the
    claim and went with the property in Phase 10. What matters is the whole of
    it: a delegated context holds **no capability**, so there is no app-wide
    verb a scheduled job can perform that the click which scheduled it could
    not. Reach over a *resource* is still the authorizer's answer, read from
    the database against this principal.
    """
    ctx = RequestContext.on_behalf_of(uuid4())

    assert ctx.email == ""
    assert ctx.capabilities == frozenset()
    assert not any(ctx.can(capability) for capability in Capability)


def test_the_delegation_keeps_the_correlation_id_so_the_chain_is_one_story() -> None:
    cid = set_correlation_id("abc123")

    assert RequestContext.on_behalf_of(uuid4()).correlation_id == cid
    assert RequestContext.on_behalf_of(uuid4(), correlation_id="x").correlation_id == "x"


def test_delegate_re_points_a_context_and_drops_the_identity() -> None:
    """For the worker that already holds one — the report graph inside a run."""
    someone = RequestContext(
        user_id=uuid4(), email="sara@example.com", session_id=uuid4(), correlation_id="cid-1",
    )
    other = uuid4()

    delegated = someone.delegate(other)

    assert delegated.user_id == other
    assert delegated.delegated is True
    assert delegated.correlation_id == "cid-1"
    # Not carried across: it is no longer that person acting.
    assert (delegated.email, delegated.session_id) == ("", None)


class _Recorder:
    def __init__(self) -> None:
        self.rows: list[object] = []

    def add(self, row: object) -> None:
        self.rows.append(row)


async def test_an_audited_action_from_a_worker_says_it_was_delegated() -> None:
    db = _Recorder()

    await audit.record(
        db,  # type: ignore[arg-type]
        RequestContext.on_behalf_of(uuid4()),
        action="knowledge.store.revalidated",
        detail={"templates": 3},
    )

    assert db.rows[0].detail == {"templates": 3, "delegated": True}  # type: ignore[attr-defined]


async def test_an_ordinary_request_carries_no_delegated_flag_at_all() -> None:
    """Absent rather than `false`, so the flag reads as an exception in the log
    rather than as noise on every row."""
    db = _Recorder()

    await audit.record(
        db,  # type: ignore[arg-type]
        RequestContext(user_id=uuid4(), email="s@x.io"),
        action="knowledge.template.created",
        detail={"templates": 1},
    )

    assert db.rows[0].detail == {"templates": 1}  # type: ignore[attr-defined]


# ── the walk ─────────────────────────────────────────────────────────────
def _worker_modules() -> list[pathlib.Path]:
    return sorted(p for p in WORKERS.glob("*.py") if p.name != "__init__.py")


def test_no_worker_builds_a_context_any_other_way() -> None:
    """`RequestContext(...)` called directly anywhere under `app/workers/`.

    A grep would miss `RequestContext (` and a keyword-only spelling; this
    walks the AST, so the only way to add one is to change this test — which
    is the conversation the rule wants to force. `on_behalf_of` and `delegate`
    are the two sanctioned constructors and both are attribute calls, so they
    do not match.
    """
    offenders: list[str] = []
    for path in _worker_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RequestContext"
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == [], (
        "background work names its principal through "
        "RequestContext.on_behalf_of(...), never by constructing one: "
        + ", ".join(offenders)
    )


def test_the_workers_that_act_for_somebody_use_on_behalf_of() -> None:
    """The other half of the rule, so it cannot be satisfied by deleting code.

    Three worker modules run somebody's SQL — the report graph, the conflict
    sweep, the benchmark — and each has to say whose. A module that stopped
    building a context at all would pass the walk above and fail here.
    """
    users = {
        path.name
        for path in _worker_modules()
        if "on_behalf_of" in path.read_text()
    }

    assert {"report_graph.py", "knowledge_maintenance.py", "benchmark.py"} <= users
