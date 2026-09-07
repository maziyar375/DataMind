"""The three access routes, written once and attached to every resource type.

`GET/POST/DELETE …/grants`, `GET …/actions` and `POST …/transfer` are the same
five endpoints on a connection, on its knowledge store, on its semantic layer,
and — from Phase 8 — on reports, dashboards, model configurations and
conversations. Eight copies of five routes is eight places for the `manage`
gate to be spelled slightly differently, and the only way anybody would find
out is a resource somebody could share without being allowed to.

So they are written here once and **attached**:

```python
attach_access_routes(router, ResourceType.CONNECTION, param="connection_id")
```

Three things this arrangement buys, in the order they matter:

* **One `manage` gate.** Every one of the five goes through `GrantService`,
  which asks `policy.require` — which is the one place the 404/403 rule lives.
  A router cannot get it wrong by not writing it.
* **Phase 8 is a line per type.** Sharing a report is this call with a
  different enum member, not a fourth copy of a share dialog's backend.
* **The derived types work unchanged.** `KNOWLEDGE` and `SEMANTIC_LAYER` carry
  their *connection's* id, so `/connections/{id}/knowledge/grants` reads the
  same path parameter and asks about a different type — which is exactly the
  distinction requirement 2 turns on: a Knowledge Manager may be granted
  curation over a connection whose data they cannot read.

**`/actions` is deliberately not gated on `manage`.** It answers *"what may I
do here"*, and the honest answer for somebody holding `select` is "view, not
edit" — refusing to say so would make every read-only screen guess. It is
gated on holding *something*: `describe` is the floor, and a principal with
nothing gets the same 404 every other route gives them.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.deps import AuthzDep, CtxDep, DbDep
from app.api.schemas import (
    ActionsRead,
    GrantRead,
    GrantWrite,
    SelfGrantWrite,
    TransferWrite,
)
from app.core.context import RequestContext
from app.domain.ports.authz import Authorizer, ResourceRef
from app.domain.value_objects.authz import (
    PRIVILEGE_MEANINGS,
    Privilege,
    ResourceType,
)
from app.services.grant_service import GrantService, GrantView
from app.services.policy import require

#: The named questions a component asks, and the privilege behind each.
#:
#: The map exists so a button in the SPA is `can.share` rather than
#: `privileges.includes('manage')` — which would be a copy of the lattice in
#: the frontend, kept in sync by hand, wrong on the day the lattice changes.
#: The words are the verbs a person recognises; the privileges are the model.
CAN: dict[str, Privilege] = {
    "view": Privilege.SELECT,
    "edit": Privilege.MODIFY,
    "delete": Privilege.DELETE,
    "share": Privilege.MANAGE,
    "transfer": Privilege.MANAGE,
}


def _view(grant: GrantView) -> GrantRead:
    return GrantRead(
        id=grant.grant_id,
        principal_id=grant.principal_id,
        principal_name=grant.principal_name,
        principal_kind=grant.principal_kind,
        privilege=str(grant.privilege),
        path=grant.path,
    )


async def actions_for(
    ctx: RequestContext, authz: Authorizer, ref: ResourceRef
) -> ActionsRead:
    """What this principal may do here, in the two shapes the UI needs.

    Exported rather than private because the resource's own `GET /{id}` may
    want to embed it — a detail screen that fetched the resource and then its
    actions would render every control disabled for one frame, which reads as a
    permissions bug to whoever is looking at it.
    """
    held = await authz.privileges_on(ctx, ref)
    meanings = PRIVILEGE_MEANINGS[ref.type]
    return ActionsRead(
        privileges=sorted(str(p) for p in held),
        can={name: privilege in held for name, privilege in CAN.items()},
        meanings={str(p): meanings[p] for p in Privilege},
    )


def attach_access_routes(
    router: APIRouter,
    type_: ResourceType,
    *,
    param: str = "id",
    in_prefix: bool = False,
    transferable: bool = True,
) -> None:
    """Add the five access routes for `type_` to `router`.

    **`in_prefix=True` for the derived types**, and it is a fact about the
    router rather than a style choice. `connections` is mounted at
    `/connections` and addresses a row as `/{connection_id}/…`; the knowledge
    router is mounted at `/connections/{connection_id}/knowledge`, so the id is
    *already* in its prefix and adding it again would produce
    `…/knowledge/{connection_id}/grants` — which Starlette refuses outright, as
    a duplicated parameter name. That refusal is the good outcome; the bad one
    would have been a route that worked and read the wrong id.

    **`transferable=False`** is also for the derived types, and also not a
    simplification. A knowledge store has no owner of its own — its owner *is*
    its connection's owner — so `POST /connections/{id}/knowledge/transfer`
    would be a second, quieter way to transfer the connection. There is one
    transfer per owned thing, and it lives on the thing that has an owner.
    """
    path = "" if in_prefix else f"/{{{param}}}"

    def _ref(request: Request) -> ResourceRef:
        return ResourceRef(type=type_, id=UUID(request.path_params[param]))

    @router.get(f"{path}/grants", response_model=list[GrantRead], name=f"{type_}_grants")
    async def list_grants(
        request: Request, ctx: CtxDep, db: DbDep, authz: AuthzDep
    ) -> list[GrantRead]:
        """Everyone who can reach this, and how. Needs `manage`.

        `manage`, not `select`: who else has been given access is itself
        information about the people in the installation, and somebody who was
        merely shared a dashboard has no business enumerating who else was.
        """
        grants = await GrantService(db, authz).for_resource(ctx, _ref(request))
        return [_view(grant) for grant in grants]

    @router.post(
        f"{path}/grants",
        response_model=list[GrantRead],
        status_code=status.HTTP_201_CREATED,
        name=f"{type_}_grant",
    )
    async def create_grant(
        request: Request,
        payload: GrantWrite,
        ctx: CtxDep,
        db: DbDep,
        authz: AuthzDep,
    ) -> list[GrantRead]:
        """Share it. Idempotent; returns the whole list afterwards.

        The whole list rather than the one row, so the panel re-renders from
        the response instead of re-fetching — and so the honest answer to "what
        happened" when the share already existed is the state, not a 409.
        """
        service = GrantService(db, authz)
        ref = _ref(request)
        await service.grant(
            ctx,
            ref,
            privilege=Privilege(payload.privilege),
            user_id=payload.user_id,
            team_id=payload.team_id,
        )
        return [_view(grant) for grant in await service.for_resource(ctx, ref)]

    @router.delete(
        f"{path}/grants/{{grant_id}}",
        status_code=status.HTTP_204_NO_CONTENT,
        name=f"{type_}_revoke",
    )
    async def revoke_grant(
        request: Request,
        grant_id: UUID,
        ctx: CtxDep,
        db: DbDep,
        authz: AuthzDep,
    ) -> None:
        """Unshare. Refused if it would be your own last way to manage this."""
        await GrantService(db, authz).revoke(ctx, _ref(request), grant_id)

    @router.get(f"{path}/actions", response_model=ActionsRead, name=f"{type_}_actions")
    async def read_actions(
        request: Request, ctx: CtxDep, db: DbDep, authz: AuthzDep
    ) -> ActionsRead:
        """What may I do here — the answer every control is rendered from.

        Gated on `describe`, the floor, rather than on `manage`: the point of
        this endpoint is that a `select` holder can be shown a read-only screen
        instead of one with buttons that 403. A principal holding nothing gets
        the same 404 every other route gives them, through the same helper.
        """
        ref = _ref(request)
        await require(ctx, authz, ref, Privilege.DESCRIBE, db=db)
        return await actions_for(ctx, authz, ref)

    @router.post(
        f"{path}/grants/self",
        response_model=GrantRead,
        status_code=status.HTTP_201_CREATED,
        name=f"{type_}_self_grant",
    )
    async def self_grant(
        request: Request,
        payload: SelfGrantWrite,
        ctx: CtxDep,
        db: DbDep,
        authz: AuthzDep,
    ) -> GrantRead:
        """An administrator giving themselves access. **Two audit rows.**

        Declared before `POST …/grants` would match it? No — `/grants/self` is
        a longer literal path and Starlette matches it first regardless of
        order, because `/grants` has no path parameter to be confused with
        `self`. It is here rather than beside the ordinary grant because it is
        a different act with a different gate.

        `user.manage`, checked in the service, and refused for everybody else
        with a sentence pointing at the ordinary way to get access. See
        `GrantService.self_grant` for why the grant is not revoked afterwards.
        """
        service = GrantService(db, authz)
        ref = _ref(request)
        row = await service.self_grant(
            ctx, ref, privilege=Privilege(payload.privilege)
        )
        return GrantRead(
            id=row.id,
            principal_id=ctx.user_id,
            principal_name="you",
            principal_kind="HUMAN",
            privilege=row.privilege,
            path="direct",
        )

    if transferable:

        @router.post(
            f"{path}/transfer",
            status_code=status.HTTP_204_NO_CONTENT,
            name=f"{type_}_transfer",
        )
        async def transfer_ownership(
            request: Request,
            payload: TransferWrite,
            ctx: CtxDep,
            db: DbDep,
            authz: AuthzDep,
        ) -> None:
            """Hand it to somebody else. `manage`, audited, active owners only.

            The previous owner keeps nothing. If they should retain access,
            grant it — one row, visible in the access panel, rather than an
            implicit residue of a transfer that happened months ago and that
            nobody reviewing the shares would ever see.
            """
            await GrantService(db, authz).transfer(ctx, _ref(request), to=payload.to)
