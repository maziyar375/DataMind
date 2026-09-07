"""Request-scoped context and correlation id propagation."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from app.domain.value_objects.authz import Capability, PrincipalKind

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
    #: HUMAN or SERVICE. **Nothing about authorization reads it** — a service
    #: user with the BI Engineer role has byte-identical capabilities to a
    #: human with it, and a test asserts that equality — so this exists for
    #: exactly three things: the `/auth/*` routes that refuse a machine, the
    #: audit log, and the badge the UI draws. A default, because every context
    #: built by hand is a person or a delegation, and the service path sets it
    #: explicitly.
    kind: PrincipalKind = PrincipalKind.HUMAN
    session_id: UUID | None = None
    correlation_id: str = ""
    #: Where the request came from, for the audit log and nothing else.
    #: Empty when it cannot be established, which is a state rather than a
    #: failure — `AuditLog.actor_ip` is nullable for exactly that case, and a
    #: log that refused to record an action because it could not name an
    #: address would be worse than one that records the action without it.
    actor_ip: str = ""
    #: Every capability this principal holds, from every role reaching them.
    #: Resolved **once per request** in `get_ctx`, from the database and never
    #: from the token (plan decision 15), so a role revoked now takes effect on
    #: the next request rather than at the next token refresh.
    #:
    #: It has a default, and the default is the safe one: an empty set can do
    #: nothing app-wide. A context built by hand — a worker, a test — is
    #: therefore *fail-closed* rather than unconstructible, which matters
    #: because the alternative is a required field that every construction site
    #: fills in with a guess.
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    #: Every team this principal belongs to. One query, resolved beside the
    #: capabilities in `get_ctx` and held for the life of the request.
    #:
    #: **Nothing decides resource access from it yet** — that is Phase 6, where
    #: it becomes the second arm of the grant lookup. What reads it today is
    #: capability resolution, which already unions the roles a team carries.
    #: Empty is the safe default for the same reason `capabilities` is: a
    #: principal in no teams is exactly what an unresolved context should look
    #: like.
    team_ids: frozenset[UUID] = field(default_factory=frozenset)
    #: True when this context was built by `on_behalf_of` rather than from a
    #: verified credential. The audit log records it and **nothing else reads
    #: it** — a delegated context has exactly the principal's permissions, no
    #: more, so a scheduled run that the owner could not perform by hand fails,
    #: and that is the correct outcome rather than a bug to route around.
    delegated: bool = False

    def can(self, capability: Capability) -> bool:
        """Does this principal hold this app-wide verb?

        The **only** way to ask. A route asks it through `deps.needs(...)`,
        which runs before the handler body and so cannot be forgotten; a
        service asks it directly where the answer is not what the route was
        guarding. Nothing anywhere asks what role somebody has.
        """
        return capability in self.capabilities

    @property
    def is_admin(self) -> bool:
        """Deprecated, and no longer a role string.

        It reads `user.manage` because that is what the old `ADMIN` enum
        actually gated — the People screens — and because the alternative,
        "holds every capability", would quietly demote an administrator the
        day a nineteenth capability was added. Its two remaining callers are
        `require_admin` and `can_curate`; it is deleted in Phase 10 once the
        gate proves that number is zero.
        """
        return Capability.USER_MANAGE in self.capabilities

    @classmethod
    def for_user(
        cls,
        identity: Any,
        capabilities: frozenset[Capability],
        team_ids: frozenset[UUID],
        *,
        actor_ip: str = "",
        session_id: UUID | None = None,
    ) -> RequestContext:
        """A verified human session, with what the database says they may do.

        `identity` is an `app.domain.ports.identity.AuthenticatedIdentity` and
        is typed loosely on purpose: `app.core` sits below `app.domain` in the
        import graph, and a real annotation here would be a cycle for the sake
        of a name. What it must have is `user_id`, `email` and `role`.
        """
        return cls(
            user_id=identity.user_id,
            email=identity.email,
            role=identity.role,
            kind=PrincipalKind.HUMAN,
            session_id=session_id,
            capabilities=capabilities,
            team_ids=team_ids,
            correlation_id=get_correlation_id(),
            actor_ip=actor_ip,
        )

    @classmethod
    def for_service(
        cls,
        identity: Any,
        capabilities: frozenset[Capability],
        team_ids: frozenset[UUID],
        *,
        actor_ip: str = "",
    ) -> RequestContext:
        """A verified API key. **The same object, from the other authenticator.**

        This is the seam requirement 9 asks for, made of code: every field but
        `kind` is filled from the same two queries the human path runs, and no
        service, repository or authorizer downstream can tell the two apart.
        There is no `session_id` because a key is not exchanged for a session —
        it *is* the credential, which is what lets revoking one fail the very
        next request.
        """
        return cls(
            user_id=identity.user_id,
            email=identity.email,
            role=identity.role,
            kind=PrincipalKind.SERVICE,
            capabilities=capabilities,
            team_ids=team_ids,
            correlation_id=get_correlation_id(),
            actor_ip=actor_ip,
        )

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
        neither is an input to any decision, and `capabilities` is empty for a
        stronger reason: a delegated context holds **no app-wide verb at all**.
        Nothing background needs one — a scheduled run reads and writes
        *resources*, and reach over a resource is the authorizer's answer, read
        from the database against this principal. A worker that genuinely
        needed `settings.manage` would be doing something this model does not
        cover, which is a design conversation rather than a wider default.
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
            self,
            user_id=user_id,
            email="",
            role="",
            # Reset rather than carried: the principal changed, so the old
            # principal's kind is not a fact about the new one. A run started
            # by an API key and delegated to the report's owner must not
            # describe that owner as a machine.
            kind=PrincipalKind.HUMAN,
            session_id=None,
            capabilities=frozenset(),
            team_ids=frozenset(),
            delegated=True,
        )
