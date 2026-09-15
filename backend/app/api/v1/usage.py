"""What the models cost, read back out.

Phase 3 of [the usage plan](../../../../docs/plans/llm-observability-v2-implementation.md).
The aggregation is `services/usage_service.py` and this is the HTTP shape over
it — no arithmetic happens here, which is why the query got a phase and a test
file of its own before anything could render it.

**Three routes rather than one with a `user_id` parameter.** The parameterised
version has to decide its gate *inside* the handler — *"needs `usage.read`
unless the id is your own"* — and the access-control rulebook's §4 checklist
has one right answer per route, not one per argument value. So the scope is in
the path: `/usage/me` is the caller and carries no capability because nothing
can widen it, and the two that read about *other people* carry `usage.read`.
`make authz-check` and the conformance walk can both see all three.

**Counts, never content.** Every figure here is an integer, a price or a day.
"Ali asked 40 questions costing 180k tokens" is a different disclosure from
"here is what Ali asked", and only the first one is reachable through this
module — which is also why `usage.read` is a capability an Auditor holds and
a connection's owner does not.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.api.deps import CtxDep, DbDep, UsageReadDep
from app.api.schemas import UsageBucket, UsageSeries, UsageTotal
from app.services import usage_service as usage

router = APIRouter(prefix="/usage", tags=["usage"])


def _series(series: usage.Series) -> UsageSeries:
    """The service's dataclass, as the DTO. Field for field, deliberately.

    Written out rather than `model_validate(asdict(...))` because the two
    shapes being the same is a thing to notice when it stops being true: a
    field added to the service and silently absent from the wire is the kind
    of gap a screen discovers as a missing number.
    """
    return UsageSeries(
        actor_id=series.actor_id,
        actor=series.actor,
        prompt_tokens=series.prompt_tokens,
        completion_tokens=series.completion_tokens,
        cost_usd=series.cost_usd,
        runs=series.runs,
        unmeasured=series.unmeasured,
        unpriced=series.unpriced,
        buckets=[
            UsageBucket(
                day=bucket.day,
                prompt_tokens=bucket.prompt_tokens,
                completion_tokens=bucket.completion_tokens,
                cost_usd=bucket.cost_usd,
                runs=bucket.runs,
            )
            for bucket in series.buckets
        ],
    )


@router.get("/me", response_model=UsageSeries)
async def my_usage(
    ctx: CtxDep,
    db: DbDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> UsageSeries:
    """What the caller's own questions, reports and layer generations cost.

    **No capability, and that is not an oversight.** The scope is `ctx.user_id`
    and there is no parameter that could change it — the route has two, both
    dates — so there is nothing here to gate: refusing somebody their own token
    count would only teach them to stop looking at it. The two routes that read
    about *other people* are next door and carry `usage.read`.

    The window is clamped server-side (`MAX_WINDOW_DAYS`); a caller that names
    neither end gets the last thirty days. A person with no runs in the window
    gets a zero series carrying their own name, rather than an empty body the
    screen would have to guess at — "you, nothing this month" is an answer.
    """
    window = usage.clamp_window(since, until)
    return _series(await usage.for_actor(db, ctx.user_id, window=window))


@router.get("/users", response_model=list[UsageSeries])
async def usage_by_person(
    ctx: UsageReadDep,
    db: DbDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[UsageSeries]:
    """Everybody's usage, one series each, in **one** response.

    Not a per-person endpoint the screen loops over: the screen renders a list
    of people with a chart each, and a loop of authorized reads is the
    pagination bug §4 of the rulebook names. One grouped query, one answer.

    **Inner joined to `users`**, so a deleted person is not in this list. Their
    spend is not lost — it stays in `/usage/total`, which joins nothing — and
    the gap between the two is reported there rather than closed here. An outer
    join would attribute a departed person's spend to whoever remains, which is
    the one answer that is wrong.

    Every row carries a display name and no address. A usage screen answers
    *"who spent this"* with something a person recognises, and an email is a
    personal identifier it has no need of.
    """
    window = usage.clamp_window(since, until)
    return [_series(row) for row in await usage.per_actor(db, window=window)]


@router.get("/total", response_model=UsageTotal)
async def installation_usage(
    ctx: UsageReadDep,
    db: DbDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> UsageTotal:
    """What the whole installation spent, and how much of it has no owner.

    The one structural difference from the route above: this joins `users` not
    at all, so a run whose actor has since been deleted is still counted. It is
    a true record of tokens somebody spent, and leaving it out of *both* views
    would quietly shrink the installation's own total.

    So this total is legitimately larger than the sum of `/usage/users`, and
    `unattributed` is the size of that difference — carried on the wire so the
    screen can state it in a sentence instead of leaving a reader to add the
    people up and wonder where the rest went.
    """
    window = usage.clamp_window(since, until)
    total = await usage.installation(db, window=window)
    return UsageTotal(
        **_series(total).model_dump(),
        unattributed=total.unattributed,
        unattributed_tokens=total.unattributed_tokens,
    )
