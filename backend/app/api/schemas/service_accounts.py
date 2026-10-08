"""Request/response DTOs: service accounts."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ServiceUserCreate(BaseModel):
    """Name, purpose, roles. **No email field, and no password field.**

    The address is generated (`svc-<slug>-<discriminator>@service.datamind.local`)
    because `users.email` is `NOT NULL UNIQUE` and every join in the schema
    reads it; a machine has no mailbox and typing one would invite somebody to
    put a real person's address on an agent.

    `description` is required, and it is the only required field here that
    looks optional. An undocumented machine identity is the one nobody is ever
    willing to delete, and six months later it is the one still holding a key.
    """

    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    role_ids: list[UUID] = []


class ServiceUserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    #: No `INVITED`: a machine is never invited. Disabling keeps every role,
    #: team and key, so re-enabling is not a re-grant.
    status: Literal["ACTIVE", "DISABLED"] | None = None


class ServiceUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
    description: str | None = None
    status: str
    #: Always `SERVICE` here. Present so one component can render a principal
    #: from either list and badge it correctly.
    kind: str = "SERVICE"
    #: The synthetic address. Shown small, never as the identity — the UI shows
    #: the display name — but present because it is what `audit_logs` joins to
    #: and somebody reading a log line needs to be able to match it.
    email: str = ""
    roles: list[str] = []
    teams: list[str] = []
    #: How many keys have been issued and not revoked. The number that answers
    #: "is anything still authenticating as this?" without opening the detail.
    active_keys: int = 0
    created_at: datetime | None = None


class ServiceCredentialRead(BaseModel):
    """One key, described. **Never the key.**

    `prefix` is the clear half by construction: it is what makes a key found in
    a log traceable to its owner, and showing it is the whole reason the format
    has two parts. `token_hash` is not on this model and a test asserts the
    string never appears in the OpenAPI document.
    """

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    prefix: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None


class ServiceCredentialCreate(BaseModel):
    """Issue a key. Name it, so it can be revoked by name.

    `expires_at` omitted means *the installation default* — a year. An explicit
    `never_expires` is how an administrator asks for a key with no expiry, and
    it is a separate flag rather than `expires_at: null` because "the caller
    said nothing" and "the caller meant none" must not be the same request.
    """

    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None
    never_expires: bool = False


class IssuedKeyResponse(BaseModel):
    """The one and only time the secret leaves the server.

    Shaped like `UserInviteResponse` on purpose — the SPA reuses the
    one-time-password panel, which is already correct about showing a secret
    once and saying so.
    """

    credential: ServiceCredentialRead
    #: `dm_sk_<prefix>_<secret>`. Not recoverable from any endpoint afterwards:
    #: the row keeps a SHA-256 of the secret half and nothing else.
    token: str


class CapabilityCatalogEntry(BaseModel):
    """One capability, with the group and sentence the checklist renders.

    Served rather than hardcoded in the SPA for the same reason the parameter
    catalog is: the enum is closed **in the backend**, and a checklist that
    listed a nineteenth capability the server had never heard of — or missed
    one it had — would be a permission nobody could grant.
    """

    name: str
    group: str
    label: str


class ReachRead(BaseModel):
    """One reason one principal reaches one resource.

    **Reach, and never data.** A row names a principal, a resource and a
    privilege; it carries no host, no username, no stored statement and no row
    from a customer's database. An access review that leaked any of those
    would be a screen that discloses the thing it exists to control, and
    `tests/unit/test_access_review.py` asserts it against this model.

    `resource_id` is null for a wildcard or a role's scoped privilege: both
    reach every resource of the type, including ones that do not exist yet,
    and `resource_name` says so in words rather than leaving a blank cell that
    reads as a deleted row.
    """

    principal_id: UUID
    principal_name: str
    principal_kind: str
    resource_type: str
    resource_id: UUID | None = None
    resource_name: str
    privilege: str
    #: owner · direct · team · role · wildcard — the five facts of §15.2, one
    #: for one. An identifier rather than a sentence: the UI writes the
    #: sentence, and a CSV column of prose is a column nobody can filter.
    path: str
    #: The team or role the reach arrives through. Empty for the other three.
    via: str = ""


class PermissionsResponse(BaseModel):
    """`GET /me/permissions` — what the UI renders every affordance from.

    Never a role string. The SPA asks "may I?" and gets a list of verbs; it
    does not ask "what am I?" and guess. That is what makes "the UI shows
    exactly what the backend would allow" a property rather than an aspiration.
    """

    capabilities: list[str]
    roles: list[str]
    #: Empty until Phase 4 gives a principal teams. Present now so the SPA's
    #: shape does not change when they arrive.
    teams: list[str] = []
    #: **Every resource this principal can reach, and how** — the by-principal
    #: lens of the access review, pointed at yourself (Phase 9).
    #:
    #: It is here rather than behind `access.review` because *"what can I
    #: reach"* is a question everybody may ask about themselves, and answering
    #: it is the difference between a user who can ask an owner for access and
    #: one who files a support ticket saying the product is broken. The
    #: capability gates asking about **somebody else**.
    reach: list[ReachRead] = []
