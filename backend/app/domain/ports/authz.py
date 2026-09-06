"""The authorization port — one question, asked in one place.

> **Requirement 7: a centralised approach rather than `if user.is_admin`
> scattered through the codebase.**

This module declares *how the question is asked*, never how it is answered. It
is a `Protocol` with four methods and four value objects, and it imports no
framework, no SQLAlchemy and no infrastructure — an import-linter contract
enforces that, which is what keeps the port swappable rather than merely
described as swappable.

Two implementations exist behind it (plan §18.5):

| | what it does |
|---|---|
| `OwnerOnlyAuthorizer` | ownership, and nothing else. **Today's behaviour, exactly** |
| `RbacAuthorizer` | ownership, grants, team grants, role privileges, wildcards |

and a third — an out-of-process authorizer — is *named* and not built. The
`Ids` arm of `Visible` exists so that stays possible, not so it happens.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from app.domain.value_objects.authz import Privilege, ResourceType

if TYPE_CHECKING:  # a dataclass in `app.core`; the import is free at runtime
    from app.core.context import RequestContext


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """*Which* thing is being asked about: a type and an id.

    For the two derived types the id is the **connection's** id — a knowledge
    store and a semantic layer have no identity of their own — which is why
    `type` is not inferable from `id` and both are always given.

    `entity` is an **optimisation and never a second identifier**. A caller
    that has already loaded the row (every `GET /x/{id}` has, by the time it
    decides whether to return it) passes it so the authorizer does not re-read
    what is already in memory; a caller that has not simply omits it and the
    authorizer loads what it needs. It is excluded from equality and from
    `repr` so two refs to the same thing compare equal whether or not one of
    them happens to be carrying a row — and so a `repr` in a log line never
    prints a decrypted credential.
    """

    type: ResourceType
    id: UUID
    entity: Any | None = field(default=None, compare=False, repr=False)

    @classmethod
    def to(cls, type_: ResourceType, entity: Any) -> ResourceRef:
        """A ref to a row already in hand. `entity.id` is the resource id."""
        return cls(type=type_, id=entity.id, entity=entity)


@dataclass(frozen=True, slots=True)
class Decision:
    """Allowed, plus *why* — so a denial can be audited with a reason.

    `because` holds grant ids, role names, the literal word `owner`, or
    `admin_self_grant`. **Never free text**: it goes into `audit_logs.detail`,
    and a log that says *"no, because the only privilege reaching you is
    describe"* is a different artifact from one that says *"no"*. It is also
    what `GET /{resource}/{id}/actions` renders into the permission explainer,
    so a user can be told the sentence rather than shown a greyed-out button.
    """

    allowed: bool
    because: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed


# ── what `visible` may return ────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Everything:
    """No filter at all: a wildcard grant, a role scoped privilege, or
    authorization disabled.

    **Checked first**, before any query is built, so the common administrator
    case costs no join.
    """

    because: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Subquery:
    """A `SELECT id` the caller composes into its own query.

    `select` is a SQLAlchemy `Select` and is typed `Any` here because
    `app.domain` may not import SQLAlchemy — the object is constructed only in
    `app/infra/authz/` and consumed only by a repository, so the domain is
    merely the carrier. One round trip, one plan, one index scan.

    **The anti-pattern this type exists to prevent** is N calls to `allowed` in
    a loop. A list endpoint that filters in Python has a bug rather than a style
    problem: its pagination counts rows the caller may not see.
    """

    select: Any


@dataclass(frozen=True, slots=True)
class Ids:
    """A literal set of ids — the shape an out-of-process authorizer would have
    to return, and the shape `visible` takes when the answer is *nothing*.

    Present so the port is honest about what a remote policy engine could do,
    not so it gets used by the two implementations that exist.
    """

    ids: frozenset[UUID]


Visible = Everything | Subquery | Ids

#: The answer to "which of these may they see?" when the answer is none of them.
NOTHING = Ids(frozenset())


class Authorizer(Protocol):
    """The one place that answers "may this principal do this?".

    Four methods, and the pairing of the first two is the whole design: ask
    `allowed` about **one** thing, ask `visible` about **many**. There is no
    third way to find out, and `make authz-check` greps for the three shortcuts
    people reach for instead: a bare ownership comparison, an admin flag, and a
    role string compared to a literal.
    """

    async def allowed(
        self, ctx: RequestContext, ref: ResourceRef, privilege: Privilege
    ) -> Decision:
        """May this principal do this to **this** thing?

        Used by `GET /x/{id}`, by every mutation and by every execution.
        """
        ...

    async def allowed_many(
        self, ctx: RequestContext, pairs: Sequence[tuple[ResourceRef, Privilege]]
    ) -> list[Decision]:
        """The same question about several things, in one round trip.

        For the places that genuinely need N answers rather than a filter: a
        dashboard's tiles, each against *its own* connection.
        """
        ...

    async def visible(
        self, ctx: RequestContext, type_: ResourceType, privilege: Privilege
    ) -> Visible:
        """Which resources of this type may they act on at this privilege?

        Composed into the caller's existing `SELECT`. Never iterated.
        """
        ...

    async def privileges_on(
        self, ctx: RequestContext, ref: ResourceRef
    ) -> frozenset[Privilege]:
        """Everything they hold here — what powers `GET …/actions` and the UI.

        The UI renders every affordance from this answer and from
        `GET /me/permissions`, never from a role string. That is what makes
        "the UI shows exactly what the backend would allow" a property of the
        system rather than an aspiration.
        """
        ...
