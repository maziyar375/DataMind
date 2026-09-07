"""Request-scoped context and correlation id propagation."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import UUID

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(value: str | None = None) -> str:
    cid = value or uuid.uuid4().hex
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    return _correlation_id.get()


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Passed to every service and repository call. Scoping is not optional.

    **A context always names a principal.** `user_id` has no default and never
    gets one: a call that could not say who was acting used to be spelled
    `ctx=None`, and every such call is a decision made on nobody's behalf,
    which is a decision no authorizer can check. `make authz-check` greps for
    the spelling; the dataclass makes it a `TypeError`.
    """

    user_id: UUID
    email: str
    role: str
    session_id: UUID | None = None
    correlation_id: str = ""
    #: Where the request came from, for the audit log and nothing else.
    #: Empty when it cannot be established, which is a state rather than a
    #: failure — `AuditLog.actor_ip` is nullable for exactly that case, and a
    #: log that refused to record an action because it could not name an
    #: address would be worse than one that records the action without it.
    actor_ip: str = ""
    #: True when this context was built by `on_behalf_of` rather than from a
    #: verified credential. The audit log records it and **nothing else reads
    #: it** — a delegated context has exactly the principal's permissions, no
    #: more, so a scheduled run that the owner could not perform by hand fails,
    #: and that is the correct outcome rather than a bug to route around.
    delegated: bool = False

    @property
    def is_admin(self) -> bool:
        """Deprecated. Becomes `USER_MANAGE in self.capabilities` in Phase 3,
        and is deleted in Phase 10 once the gate proves nothing reads it."""
        return self.role == "ADMIN"  # authz-ok: retires in Phase 3

    @classmethod
    def on_behalf_of(
        cls, user_id: UUID, *, correlation_id: str | None = None
    ) -> RequestContext:
        """The context background work acts under: a principal, delegated.

        A scheduled report runs **as the report's owner** — the same
        privileges, the same denials, the same rows in `audit_logs` with
        `delegated: true` in their detail. If the owner loses access to the
        connection, the scheduled run fails; there is no god context, and a
        worker that needs to act without a principal is doing something this
        model does not cover, which is a design conversation rather than a
        default argument.

        `email` and `role` are empty because a worker has neither in hand and
        neither is an input to any decision. Empty `role` in particular means a
        delegated context is **never** an administrator, which is the safe
        reading of "we did not look it up".
        """
        return cls(
            user_id=user_id,
            email="",
            role="",
            correlation_id=(
                get_correlation_id() if correlation_id is None else correlation_id
            ),
            delegated=True,
        )

    def delegate(self, user_id: UUID) -> RequestContext:
        """This context, re-pointed at another principal and marked delegated.

        For the worker that already holds a context — the report graph inside a
        run — and needs the one belonging to the row it is about to touch. It
        keeps the correlation id, so the whole chain stays one story in the
        log, and drops the identity, because it is no longer that person's.
        """
        return replace(
            self, user_id=user_id, email="", role="", session_id=None, delegated=True
        )
