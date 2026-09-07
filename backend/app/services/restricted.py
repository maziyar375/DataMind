"""One name for *"you may see this thing, but not the data behind it"*.

Three surfaces need the same answer and none of them may invent its own: a
dashboard tile whose connection the reader cannot reach, a report block in the
same position, and a chat turn in a shared thread. §19.2's intersection rule is
what they are all implementing —

> A dashboard renders. Each tile renders **iff** the viewer holds `select` on
> that tile's connection. A tile they cannot see renders as a **named
> placeholder** — not hidden, because hiding it makes the dashboard silently
> wrong, and a partly visible dashboard is a better product than a refused one
> **and** a better product than a leaking one.

— and it applies unchanged to a report's figures and a thread's results.

**Why the placeholder is named.** Telling the reader *"Sales warehouse"* is a
disclosure, and it is a deliberate one: `describe` is exactly the privilege
that means "know this exists and what it is called", the person who shared the
board already decided this reader should see its shape, and a placeholder that
will not say what it is standing in for gives them nothing to ask for. What it
never carries is a host, a port, a username or a row.

**The check runs before the cache, and that is the whole of the security
property here.** `dashboard_tile_cache` holds rows from the customer's
database, keyed on the tile — deliberately, since a per-viewer cache would be a
cache that mostly misses and a *cache key carrying a viewer is how a viewer
ends up in a fingerprint that decides what somebody else is served*. The cache
is safe **because** nothing reaches it until this module has said the reader
may see that connection's data. Move the check after the cache lookup and a
revoked reader keeps being served the last numbers they were allowed to see.
`tests/unit/test_intersection.py` is the tripwire for both halves.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.domain.ports.authz import Authorizer
from app.domain.value_objects.authz import Privilege, ResourceType
from app.infra.authz.compose import restrict
from app.infra.db.models import DatabaseConnection
from app.services import audit
from app.services.policy import ACCESS_DENIED

#: The error code every restricted surface returns. Deliberately **not**
#: `E_CONNECTION_REMOVED`: "the data source was deleted" and "you were not
#: given this data source" are different facts with different remedies — the
#: first is fixed by editing the tile, the second by asking somebody for
#: access — and a reader who is shown the wrong one goes to the wrong person.
NO_DATA_ACCESS = "E_NO_DATA_ACCESS"


def no_access_message(connection_name: str | None) -> str:
    """The sentence, with the connection named when there is a name to give.

    One sentence rather than a code the frontend expands, because this string
    is also what a CSV export, a printed report and an API client see, and
    three copies of it would be three chances to describe the rule wrongly.
    """
    if connection_name:
        return (
            f"You do not have access to “{connection_name}”, the data source "
            "behind this. Ask whoever owns it for “select”."
        )
    return (
        "You do not have access to the data source behind this. Ask whoever "
        "owns it for “select”."
    )


async def readable_connection_ids(
    db: AsyncSession,
    ctx: RequestContext,
    authz: Authorizer,
    ids: set[UUID],
) -> set[UUID]:
    """Which of `ids` this principal may **ask questions through**.

    One query for a whole dashboard, composed rather than filtered: `visible`
    goes into the `SELECT`, so twelve tiles across four connections cost one
    round trip and not twelve `allowed` calls. A loop here would be the
    pagination bug the port exists to prevent, wearing a different hat.
    """
    if not ids:
        return set()
    visible = await authz.visible(ctx, ResourceType.CONNECTION, Privilege.SELECT)
    rows = await db.execute(
        restrict(
            select(DatabaseConnection.id).where(DatabaseConnection.id.in_(ids)),
            DatabaseConnection.id,
            visible,
        )
    )
    return set(rows.scalars())


async def connection_names(db: AsyncSession, ids: set[UUID]) -> dict[UUID, str]:
    """Names only, for the placeholder. Never a host, a user or a key.

    Read without an authorization filter **on purpose**: the caller has already
    established that the reader may see the artifact, and the artifact refers
    to this connection by construction. A name is what `describe` means, and
    withholding it would produce a placeholder that cannot say what it is.
    """
    if not ids:
        return {}
    rows = await db.execute(
        select(DatabaseConnection.id, DatabaseConnection.name).where(
            DatabaseConnection.id.in_(ids)  # authz-ok: names for a placeholder
        )
    )
    return {row[0]: row[1] for row in rows}


async def record_denials(
    db: AsyncSession,
    ctx: RequestContext,
    *,
    connection_ids: set[UUID],
    on_type: ResourceType,
    on_id: UUID,
) -> None:
    """One `access.denied` row per connection a render had to withhold.

    §19.1's fourth row: the intersection case is a **200 that is audited**,
    because "this reader was repeatedly shown placeholders on a board somebody
    shared with them" is the signal that a share is half-finished, and it is
    invisible unless it is written down.

    **Per connection, not per tile** — a board with eight tiles on one
    unreachable warehouse is one fact, and eight rows would bury it. The
    `detail` names the artifact the placeholder was rendered in, so the log
    answers *"where did they hit this"* without naming a tile that has no
    grant of its own to talk about.

    A polling dashboard writes one row per connection per poll, which is the
    literal reading of "once per render" and is a real volume: a board left
    open on a 30-second tick is 120 rows an hour. That is the cost of the
    signal today; throttling it belongs with the review screen that reads it,
    where "how often" is a question somebody is actually asking.
    """
    for connection_id in sorted(connection_ids, key=str):
        await audit.record(
            db,
            ctx,
            action=ACCESS_DENIED,
            resource_type=str(ResourceType.CONNECTION),
            resource_id=connection_id,
            outcome=audit.DENIED,
            detail={
                "privilege": str(Privilege.SELECT),
                "because": ["intersection"],
                "rendered_in": str(on_type),
                "rendered_in_id": str(on_id),
            },
        )
