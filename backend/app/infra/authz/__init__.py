"""Implementations of the `Authorizer` port.

`owner_only` is what the product does today, written down as a policy object so
that every call site can be routed through the port **before** the policy
changes. `rbac` arrives in Phase 6 and is selected by one setting.
"""
from app.infra.authz.owner_only import OwnerOnlyAuthorizer

__all__ = ["OwnerOnlyAuthorizer"]
