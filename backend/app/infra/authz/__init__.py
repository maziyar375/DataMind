"""Implementations of the `Authorizer` port, and the two helpers that use it.

`owner_only` is what the product does today, written down as a policy object so
that every call site can be routed through the port **before** the policy
changes. `rbac` arrives in Phase 6 and is selected by one setting, read by
`factory.build_authorizer` — the only place any caller learns which
implementation it got. `compose` is how a `Visible` answer reaches a query.
"""
from app.infra.authz.compose import restrict
from app.infra.authz.factory import build_authorizer
from app.infra.authz.owner_only import OwnerOnlyAuthorizer

__all__ = ["OwnerOnlyAuthorizer", "build_authorizer", "restrict"]
