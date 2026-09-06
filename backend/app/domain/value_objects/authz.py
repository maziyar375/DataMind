"""The vocabulary of authorization: privileges, resource types, capabilities.

**Every name this product uses to answer "may they?" is declared here, once.**
Nothing in this module does I/O, imports a framework, or knows what a session
is — it is a list of words and a lattice over five of them, so it can be
imported by the domain ports, by the infrastructure that implements them, by
the API that renders them, and by a test that asserts the whole matrix is
filled in.

The reason it exists *before* anything reads it: this design has eight resource
types, five privileges and eighteen capabilities. Introducing them alongside
the first feature that needs each one is how a vocabulary ends up half-invented
in three places, with `"dashboard"` in one module, `ResourceKind.DASHBOARDS` in
another and a bare string in the third.

Two rules govern every enum below and they pull in opposite directions on
purpose:

* **Closed in code.** They are `StrEnum`s, so a typo is an `AttributeError` at
  import and a new member is a deliberate line in a reviewed diff.
* **Open in the database.** The columns that will store these (Phase 3's
  `role_capabilities`, Phase 6's `grants`) are `varchar`, and a row naming a
  word this module does not know is *ignored with a warning* rather than
  raising. A downgrade must not lock everyone out of the installation.

See `docs/user-management-and-access-control-plan.md` §12 and §13.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class PrincipalKind(StrEnum):
    """Who is acting. Both kinds are rows in `users` (plan §11.1).

    A service user is not a second table: it is a user with no password, no
    external subject and no interactive login. Keeping one identifier space is
    what lets an agent *own* a dashboard, keeps every existing foreign key
    pointing at `users.id`, and keeps "who did this?" a single join for humans
    and machines alike.
    """

    HUMAN = "HUMAN"
    SERVICE = "SERVICE"


class Privilege(StrEnum):
    """What may be done *to one thing*. Five, linear, monotone.

        manage ⊃ delete ⊃ modify ⊃ select ⊃ describe

    `describe` is the floor and it is load-bearing: it means *this exists and
    has a name*, which is what lets an error say 404 instead of 403 without
    turning every list endpoint into an existence oracle (plan §19.1).

    **Create is deliberately absent.** You cannot hold a privilege on an
    instance that does not exist yet, so "may I make one at all?" is a
    `Capability` — see `Capability.DASHBOARD_CREATE` and plan §13.2.
    """

    DESCRIBE = "describe"
    SELECT = "select"
    MODIFY = "modify"
    DELETE = "delete"
    MANAGE = "manage"


class ResourceType(StrEnum):
    """The eight things a privilege can be held on. The enum is closed.

    Two of them are **derived**: `KNOWLEDGE` and `SEMANTIC_LAYER` carry the
    *connection's* id as their resource id. They are separate types because
    they have separate audiences — a Knowledge Manager is not a credential
    editor — and separate privilege meanings; they are not separate tables
    because a knowledge store has no identity apart from the connection it
    describes.

    **Leaves are not resources.** A tile, a section, a block, a run, a message,
    a template and a snapshot have no grant of their own and inherit from their
    parent. Fewer rows, and no way to create an orphaned permission.
    """

    CONNECTION = "connection"          # the pipe; a grant here is a disclosure decision
    KNOWLEDGE = "knowledge"            # derived: the resource id IS the connection id
    SEMANTIC_LAYER = "semantic_layer"  # derived: the resource id IS the connection id
    LLM_CONFIG = "llm_config"          # `select` = may answer with it
    DASHBOARD = "dashboard"            # an artifact; may span several connections
    REPORT = "report"                  # an artifact; bound to exactly one connection
    CONVERSATION = "conversation"      # personal by default; grantable, rarely granted
    TEAM = "team"                      # a principal, and also a resource


#: The two types whose resource id is their connection's id. Anything resolving
#: an id to a row — the `visible` subquery's ownership arm, the audit
#: renderer — reads `database_connections` for these.
DERIVED_RESOURCE_TYPES = frozenset({
    ResourceType.KNOWLEDGE,
    ResourceType.SEMANTIC_LAYER,
})


class Capability(StrEnum):
    """An app-wide verb with **no resource instance**.

    The test is mechanical: *if the check needs an id, it is a privilege, not a
    capability.* "May Ali create dashboards?" is a capability; "may Ali open
    *this* dashboard?" is a privilege.

    Eighteen of them, in four groups (plan §12.1). This is one half of the two
    axes every product in this space converged on — Metabase calls it
    *application permissions*, Looker calls it a *permission set*.
    """

    # ── people ───────────────────────────────────────────────────────────
    USER_READ = "user.read"
    USER_MANAGE = "user.manage"
    SERVICE_USER_MANAGE = "service_user.manage"
    TEAM_READ = "team.read"
    TEAM_MANAGE = "team.manage"
    ROLE_READ = "role.read"
    ROLE_MANAGE = "role.manage"

    # ── oversight ────────────────────────────────────────────────────────
    AUDIT_READ = "audit.read"
    ACCESS_REVIEW = "access.review"

    # ── creation ─────────────────────────────────────────────────────────
    CONNECTION_CREATE = "connection.create"
    LLM_CONFIG_CREATE = "llm_config.create"
    DASHBOARD_CREATE = "dashboard.create"
    REPORT_CREATE = "report.create"
    #: "May I use Chat at all." Which connections may be asked is `select` on
    #: each connection; which models may answer is `select` on each llm_config.
    #: Three questions, three answers, none of them a role string.
    CONVERSATION_CREATE = "conversation.create"

    # ── system ───────────────────────────────────────────────────────────
    SETTINGS_MANAGE = "settings.manage"
    BENCHMARK_MANAGE = "benchmark.manage"
    EVAL_RUN = "eval.run"
    SYSTEM_MAINTENANCE = "system.maintenance"


#: The four capabilities a leaked API key must not be able to reach.
#:
#: A service user may not hold any of these unless
#: `settings.allow_privileged_service_users` is on, which is off by default and
#: writes an audit row when it is turned on. This is a *policy*, enforced in
#: the service layer, rather than an invariant enforced by the database: the
#: blast radius of a leaked key must not include minting an administrator.
PRIVILEGED_CAPABILITIES = frozenset({
    Capability.USER_MANAGE,
    Capability.ROLE_MANAGE,
    Capability.SERVICE_USER_MANAGE,
    Capability.SETTINGS_MANAGE,
})


# ── the lattice ──────────────────────────────────────────────────────────
#: For each privilege, every privilege whose holder also holds it.
#:
#: This is the SQL analogue of Lakekeeper's `define select: [...] or modify`:
#: the implication is asked **once**, as `WHERE privilege = ANY(:satisfying)`,
#: rather than remembered at two hundred call sites. That is the entire reason
#: to write a lattice down — and it is why changing the lattice never needs a
#: backfill, because nothing is ever expanded at write time.
_SATISFIED_BY: Mapping[Privilege, frozenset[Privilege]] = MappingProxyType({
    Privilege.DESCRIBE: frozenset(Privilege),
    Privilege.SELECT: frozenset({
        Privilege.SELECT, Privilege.MODIFY, Privilege.DELETE, Privilege.MANAGE,
    }),
    Privilege.MODIFY: frozenset({
        Privilege.MODIFY, Privilege.DELETE, Privilege.MANAGE,
    }),
    Privilege.DELETE: frozenset({Privilege.DELETE, Privilege.MANAGE}),
    Privilege.MANAGE: frozenset({Privilege.MANAGE}),
})


def satisfying(privilege: Privilege) -> frozenset[Privilege]:
    """Which held privileges answer *yes* to a demand for `privilege`.

    Returns a `frozenset` on purpose: the result is handed to a query builder
    and to the UI, and a caller that mutated it would silently widen the
    lattice for every later request in the process.
    """
    return _SATISFIED_BY[privilege]


def implied_by(privilege: Privilege) -> frozenset[Privilege]:
    """The other direction: everything a holder of `privilege` also holds.

    `implied_by(MANAGE)` is all five. Used to render "what can I do here" from
    a single held privilege, and by ownership, which confers the full lattice.
    """
    return frozenset(p for p in Privilege if privilege in _SATISFIED_BY[p])


#: Everything, for the case that needs no lattice walk: an owner.
ALL_PRIVILEGES = frozenset(Privilege)


# ── what each privilege means, per resource type ─────────────────────────
#: **This table is the specification** (plan §13.3). Every route resolves to
#: exactly one cell of it, `GET /{resource}/{id}/actions` renders from it, and
#: a conformance test asserts every type has a row for every privilege — so a
#: ninth resource type cannot be added without deciding what its five verbs
#: mean.
#:
#: The strings are user-facing and deliberately concrete. "Grant/revoke" is a
#: sentence somebody can be shown next to a radio button; "manage" is not.
PRIVILEGE_MEANINGS: Mapping[ResourceType, Mapping[Privilege, str]] = MappingProxyType({
    ResourceType.CONNECTION: MappingProxyType({
        Privilege.DESCRIBE: (
            "See that it exists — name, engine and disclosure policy, "
            "but not its schema."
        ),
        Privilege.SELECT: (
            "Ask questions through it; read its schema snapshot and its "
            "semantic layer."
        ),
        Privilege.MODIFY: "Edit its host and credentials, re-sync the schema, test it.",
        Privilege.DELETE: "Delete the connection.",
        Privilege.MANAGE: (
            "Grant and revoke access, change the disclosure policy, "
            "transfer ownership."
        ),
    }),
    ResourceType.KNOWLEDGE: MappingProxyType({
        Privilege.DESCRIBE: "See that the store exists, with template and review counts.",
        Privilege.SELECT: (
            "Read templates, reviews, suggestions and benchmark results."
        ),
        Privilege.MODIFY: (
            "Create, edit and archive templates; resolve reviews; run a sweep."
        ),
        Privilege.DELETE: "Delete a benchmark set.",
        Privilege.MANAGE: "Change embedding settings; grant and revoke curation.",
    }),
    ResourceType.SEMANTIC_LAYER: MappingProxyType({
        Privilege.DESCRIBE: "See that a layer exists and when it was last written.",
        Privilege.SELECT: "Read the layer document.",
        Privilege.MODIFY: "Edit it, check an expression, queue a generation job.",
        Privilege.DELETE: "Delete the layer.",
        Privilege.MANAGE: "Grant and revoke access to it.",
    }),
    ResourceType.LLM_CONFIG: MappingProxyType({
        Privilege.DESCRIBE: (
            "See that it exists — provider, model and capabilities. Never the key."
        ),
        Privilege.SELECT: (
            "Use it to answer: pick it in Chat, on a tile, or in a report."
        ),
        Privilege.MODIFY: (
            "Edit the provider, model or endpoint. Equivalent to disclosing "
            "the API key — see the warning in this module."
        ),
        Privilege.DELETE: "Delete it.",
        Privilege.MANAGE: "Grant and revoke use of it; transfer ownership.",
    }),
    ResourceType.DASHBOARD: MappingProxyType({
        Privilege.DESCRIBE: "See that it exists — name, description and tile count.",
        Privilege.SELECT: (
            "View it and its results. Each tile still needs access to its own "
            "connection."
        ),
        Privilege.MODIFY: "Edit it; add, remove and re-lay-out tiles; import.",
        Privilege.DELETE: "Delete the dashboard.",
        Privilege.MANAGE: "Grant and revoke access; transfer ownership.",
    }),
    ResourceType.REPORT: MappingProxyType({
        Privilege.DESCRIBE: "See that it exists — name, description and language.",
        Privilege.SELECT: (
            "View it and its runs' results. Still needs access to its connection."
        ),
        Privilege.MODIFY: "Edit the outline, sections, blocks and SQL; trigger a run.",
        Privilege.DELETE: "Delete the report or one of its runs.",
        Privilege.MANAGE: "Grant and revoke access; transfer ownership.",
    }),
    ResourceType.CONVERSATION: MappingProxyType({
        Privilege.DESCRIBE: "See that it exists — title, connection and when.",
        Privilege.SELECT: "Read the transcript and its artifacts.",
        Privilege.MODIFY: "Rename it, continue the thread, give feedback.",
        Privilege.DELETE: "Delete the thread.",
        Privilege.MANAGE: "Grant and revoke access; transfer ownership.",
    }),
    ResourceType.TEAM: MappingProxyType({
        Privilege.DESCRIBE: "See that it exists — name and member count.",
        Privilege.SELECT: "List its members.",
        Privilege.MODIFY: "Add and remove members.",
        Privilege.DELETE: "Delete the team.",
        Privilege.MANAGE: "Assign roles to it, grant to it, rename it.",
    }),
})


#: ⚠️ **`modify` on an `llm_config` is equivalent to disclosing the API key.**
#:
#: A holder can repoint `base_url` at a host they control, wait for the next
#: call, and read the key out of the `Authorization` header. Three consequences,
#: and they are enforced in code rather than written down here only:
#:
#: 1. `modify`, `delete` and `manage` on an `llm_config` are **not offered in
#:    the share UI at all** — the privilege picker for this type shows
#:    `describe` and `select`.
#: 2. They are reachable only by the owner, or by an administrator through the
#:    explicit self-grant path, and every one of those writes an audit row.
#: 3. Changing `base_url` or `provider` on a config that holds a stored key
#:    **clears the key**. Re-entering it is the friction that makes the attack
#:    loud instead of silent.
KEY_EQUIVALENT_PRIVILEGES = frozenset({
    Privilege.MODIFY, Privilege.DELETE, Privilege.MANAGE,
})

#: What the share UI may offer on an `llm_config`, and the API may accept for
#: one from anybody but its owner. The complement of the set above.
SHAREABLE_LLM_CONFIG_PRIVILEGES = frozenset({Privilege.DESCRIBE, Privilege.SELECT})
