# Access control — build plan

> **Subject:** [mvp2-plan.md §1.5](mvp2-plan.md#15-single-player-by-construction)
> and [Theme D](mvp2-plan.md#theme-d--make-it-a-team-product) (D1, D2, D4;
> D3 and D5 deferred with triggers).
> **Source of the design:** [research/access-control.md](research/access-control.md)
> — Lakekeeper's `Authorizer` trait and `.fga` model, read against Metabase,
> Superset and Grafana. This document is the **decision and the schedule**; that
> one is the argument. Where they disagree, this one wins, and §0.3 says why.
> **Scope of this MVP:** users, groups, resource grants, a privilege lattice, an
> audit trail for every authorization event, and the seams that let an OIDC
> provider arrive later. **No OIDC adapter is built here** and **no identity
> server is deployed** — §6 is the recipe for adding one, written now so it stays
> true.
> **Status:** plan. Decisions in §0.3 are made, not open; §13 lists what remains
> genuinely open.
> **Siblings:** [security.md](security.md) · [architecture.md](architecture.md) ·
> [learning-loop-plan.md](learning-loop-plan.md) (Phase 8 built `audit.py`,
> which this plan finishes).

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

**One function answers "may this actor do this to this thing", every call site
asks it, the answer is computed from ownership plus a grants table in DataMind's
own Postgres, and swapping any part of that — the store, the identity provider,
the principal type — is a change behind a port rather than a change at 200 call
sites.**

### 0.2 What is being built, and what is not

| | |
|---|---|
| **Built** | `Privilege` lattice · `Authorizer` port · `groups` + `group_members` · `grants` · grants on connections, reports and dashboards · sharing UI · ownership transfer · audit rows for grant / revoke / **denial** · the 404-vs-403 rule |
| **Built as a seam, not a feature** | provider-namespaced identities · `groups.(provider_id, source_id)` · `ctx.group_ids` · `auth_provider` config switch with one implementation · an actor context that background work can construct |
| **Not built, with a written trigger** | OIDC adapter (D5) · row-level security (D3) · workspaces / folders · nested groups · `pass_grants` delegation · per-viewer tile cache · service-account principals |
| **Not built, and not wanted** | an external authorization service · a second source of truth for grants · `deny` rules · sharing `llm_configs` |

**The constraint that shapes everything:** no Keycloak, no OpenFGA, no second
container. The research doc's §1.5 is the reason — Lakekeeper needs OpenFGA
because Iceberg namespaces nest without bound, and it pays for it with a
`reconcile` subcommand whose own docstring admits eventual consistency. DataMind's
resource graph is **two levels deep with four resource types**. The model is worth
copying; the service is not. What is copied instead is the *port* — the thing that
makes the choice reversible.

### 0.3 The nine decisions, decided

[research/access-control.md §8](research/access-control.md) lists nine open
questions. This plan closes all nine, because the schema cannot be written until
four of them are answered. **⚠️ marks the ones that would change the schema if
reversed.**

| # | Question | **Decision** | Consequence |
|---|---|---|---|
| 1 ⚠️ | Whose credentials does a shared object execute under? | **The connection's grant.** A viewer executes a tile only if *they* hold `select` on that tile's connection, re-checked at every execution — never at share time. | Grants exist on connections **and** on artifacts. Both are checked. This is the whole reason D1 is blocking. |
| 2 ⚠️ | Does `dashboard_tile_cache` grow a viewer in its key? | **No — and the reason is written into the model's docstring as a trigger.** The trigger: *the first time a tile's result depends on who is looking.* | Cache stays keyed on `tile_id` ([`models.py:632`](../backend/app/infra/db/models.py#L632)). Phase 6 adds a test that fails if a viewer-dependent filter is introduced without the key changing. |
| 3 | Does an administrator read everything? | **No implicit read. An admin may _grant themselves_ access, and the grant is an audited row.** | The enforced behaviour today (admins see nothing they do not own) becomes the documented one. Lakekeeper's escalation path, adopted. |
| 4 ⚠️ | Is granting `select` on a connection a disclosure decision? | **Yes.** `describe` exposes `disclosure_policy`; only the owner or a `manage_grants` holder may change it; every ask records the policy in force. | `disclosure_policy` splits out of the ordinary `modify` surface. |
| 5 ⚠️ | Per-resource grants now, or a workspace now? | **Per-resource now, workspace shape reserved.** `grants.resource_type` is a string and the check is a join, so a `workspace` row type is additive. | E→F stays a migration of existing grants into a container, which is a real migration but a known one. §12 writes the trigger. |
| 6 | May local and OIDC accounts coexist? | **Yes** — a user with `external_subject` set may no longer use a password; the bootstrap admin stays local as break-glass. | Decided now because §6's step 6 depends on it, even though no OIDC code is written. |
| 7 | What happens to the refresh cookie under OIDC? | **Under `auth_provider="oidc"`, `/auth/login`, `/auth/refresh` and the cookie all disappear.** | Written into the config docstring in Phase 0 so the future adapter cannot quietly ship two session lifetimes. |
| 8 | Is a denial audited, and does it leak existence? | **404 unless the caller holds at least `describe`; 403 above that.** Every 403 writes `audit.record(outcome=DENIED, …)` carrying `Decision.because`. | The existing `DENIED` constant finally has a producer. |
| 9 | What happens to grants when a principal goes away? | **`DISABLED` keeps grants. Deleting a user requires ownership transfer first.** | Ships in the same phase as the first grant, not after the first support ticket. |

> **Why decision 1 is the one to re-read.** It is the difference between DataMind
> and Superset's `DASHBOARD_RBAC`, which — per Superset's own documentation —
> grants dashboard access *and thereby* read on every dataset behind it. That is a
> defensible product; it is not this one, because this product's README leads with
> *"you decide what leaves your database."* Sharing an artifact here shares the
> **picture**, never the **pipe**.

### 0.4 The eight phases

Each phase is sized to be finished, reviewed and merged on its own. The "sessions"
column is the honest estimate for doing it with Claude Code — a session being one
context window of focused work ending at a green `make check`.

| # | Phase | Size | Sessions | Ships behaviour? |
|:--:|---|:--:|:--:|:--:|
| **0** | The vocabulary and the port | S | 1 | no — pure addition |
| **1** | Step zero, part A — dashboards and reports take `ctx` | M | 2 | no — behaviour-preserving |
| **2** | Step zero, part B — everything else, and the grep gate | M | 2 | no — behaviour-preserving |
| **3** | Principals — groups, membership, `ctx.group_ids` | M | 2 | yes — admin can make groups |
| **4** | **Grants on connections (D1)** — the blocking item | L | 3 | yes — **sharing begins** |
| **5** | The audit half — grant, revoke, denial, escalation | S | 1 | yes |
| **6** | Reports, then dashboards, then the intersection rule | M | 2–3 | yes — **D2 read-only sharing** |
| **7** | The rulebook, the seams, and the conformance check | S–M | 1–2 | no — makes the rules enforceable |

**Phases 0–2 are worth doing even if Theme D is cancelled**, because they replace
a false docstring with a true one. [`policy.py`](../backend/app/services/policy.py)
opens with *"Row-level or column-level security later is a change in this module
only"*, and that sentence is not true of the code today: `can_read`, `can_write`
and `can_administer_users` have **zero call sites**, while **208 lines** across
`api/`, `services/` and `workers/` compare `owner_id` by hand.

---

# Part 1 — The design

## 1. The six concepts

Everything in this plan is one of six things. A new feature that cannot be
expressed in these six is a feature that needs a design change, not a workaround —
that rule is what [§14](#18-the-rulebook) makes checkable.

### 1.1 Principal — *who is asking*

A principal is a **user** or a **group**. Nothing else, in this MVP.

```
   Principal
     ├── User    — a person, a row in `users`, identified by `users.id`
     └── Group   — a named set of users, a row in `groups`
```

Three properties are load-bearing:

- **A grant points at `users.id` or `groups.id` — never at an email, never at an
  external subject.** This is what makes the OIDC migration free: when a local
  account becomes an OIDC account, `users.id` does not change, so no grant is
  rewritten. It is also why an identity is *namespaced* the moment it comes from
  outside (§6).
- **Groups are flat.** Not laziness: Keycloak, Entra and Okta all emit membership
  in a token as a **flat list of paths** (`["/analytics", "/analytics/finance"]`),
  so the hierarchy is already flattened by the issuer before DataMind sees it. A
  local nesting would be a second, contradicting hierarchy. §12 has the trigger for
  changing this, and the change is one `WITH RECURSIVE`.
- **A group may mirror an external group, or not.** `groups.(provider_id,
  source_id)` is `(NULL, NULL)` for a DataMind-managed group and `('oidc',
  '/analytics')` for a mirrored one. Binding an existing group to an external one
  is **two column updates and zero grant changes**. This is Lakekeeper's
  `RoleSourceSystem`, and it is the single highest-value column in the schema.

**Roles do not disappear.** `Role.ADMIN | MEMBER` stays exactly what it is: a
*system-wide* capability (manage users, read the audit log), orthogonal to
resource grants. This is Metabase's split — *application permissions* beside
*collection permissions* — and conflating them is how Superset's model became a
bag of strings.

### 1.2 Resource — *what is being asked about*

Four types in this MVP, and the enum is closed on purpose:

```python
class ResourceType(StrEnum):
    CONNECTION = "connection"    # the pipe — grants here are disclosure decisions
    DASHBOARD  = "dashboard"     # an artifact — may span several connections
    REPORT     = "report"        # an artifact — bound to exactly one connection
    CONVERSATION = "conversation"  # personal by default; grantable, rarely granted
```

**`llm_configs` is deliberately absent, and that absence is a rule.** Sharing an
LLM config shares the *use of an encrypted provider key*. It is named here so
nobody adds it by analogy later.

The graph is **two levels and fixed**:

```
   connection ──┬── dashboard ── tile        (tile carries its OWN connection_id)
                ├── report ── section ── block
                ├── conversation ── run
                ├── semantic_layer           (follows the connection — no grant of its own)
                └── knowledge_templates      (follows the connection, gated by `modify`)
```

Two consequences worth stating before the schema:

- **Leaves are not grantable.** A tile, a section, a block, a run has no grant row;
  it inherits from its parent artifact. Fewer rows, and no way to create an
  orphaned permission.
- **The semantic layer and the knowledge store already follow the connection**, so
  D1 alone brings a meaningful amount of team behaviour at zero extra modelling
  cost. That is the strongest argument for sequencing connections first.

### 1.3 Privilege, and the lattice

Four privileges. The lattice is declared **once, in the domain layer**, and
expanded **at check time** — so changing it never needs a backfill.

```
   manage_grants  ⊃  modify  ⊃  select  ⊃  describe
```

| Privilege | On a **connection** | On a **dashboard / report** |
|---|---|---|
| `describe` | it exists; its name, kind, and **disclosure policy**; *not* its schema | it exists; its name and description |
| `select` | ask questions through it; read its schema, semantic layer and knowledge store | view it, and view its results |
| `modify` | edit it, re-sync it, **curate its knowledge** | edit it; add and remove tiles or sections |
| `manage_grants` | grant and revoke on it; change its **disclosure policy**; transfer ownership | grant and revoke on it; transfer ownership |

```python
# app/domain/value_objects/authz.py
#: For each privilege, every privilege whose holder also holds it. The SQL
#: analogue of Lakekeeper's `define select: [...] or modify` — asked as
#: `WHERE privilege = ANY(:satisfying)` rather than remembered at 200 call
#: sites, which is the entire reason to write a lattice down once.
_SATISFIED_BY: dict[Privilege, frozenset[Privilege]] = {
    Privilege.DESCRIBE:      frozenset(Privilege),
    Privilege.SELECT:        frozenset({SELECT, MODIFY, MANAGE_GRANTS}),
    Privilege.MODIFY:        frozenset({MODIFY, MANAGE_GRANTS}),
    Privilege.MANAGE_GRANTS: frozenset({MANAGE_GRANTS}),
}
```

**Why `curate` is not a fifth privilege.** [`policy.can_curate`](../backend/app/services/policy.py)
already answers *administrator, or the owner of the connection*. Under grants that
becomes **`modify` on the connection**, which is the same rule in the new
vocabulary and preserves the property D4 wants: *a reader granted access to
somebody's connection may ask it questions and may not rewrite what it has been
taught.* Seven tests in `test_audit_and_permissions.py` already pin that behaviour
and must keep passing unchanged.

**Why `pass_grants` is not a fifth privilege.** Lakekeeper shipped delegated
granting and then took half of it back in v4.10 — revoking now requires
`manage_grants` at every level, because *taking a privilege back is
administration, never delegation*. The property that buys is **no grant is ever
more than one hop from an administrator or an owner**, which is what lets a grant
row omit its grantor and lets revocation avoid cascading. Start there rather than
arrive there.

**Why there is no `deny`.** One `but not` clause is the most any of these models
should have, and DataMind needs none. A permission model where the answer requires
reading rules in priority order is a model nobody can predict.

### 1.4 Grant — *the fact that authorises*

One privilege, on one resource, to one principal. Nothing else. No expiry (§13),
no condition, no scope string.

**A grant is additive and monotone.** Effective privilege is the **union** over
every grant reaching the actor — directly, or through any group they belong to —
plus ownership. There is no subtraction anywhere in the model, which is what makes
the check a single `EXISTS` and makes the answer explainable in one sentence.

### 1.5 Ownership — *a fact about the resource, not a grant*

`owner_id` stays exactly where it is, on every table that has it, meaning exactly
what it means. Lakekeeper models it the same way — `define ownership: [user,
role#assignee]` sits *alongside* the privileges rather than replacing them.

Three things follow, and all three are why this is the cheap design:

- **No backfill.** Turning grants on creates no rows; the authorizer reads
  ownership **and** grants from day one.
- **Rolling back is a config flip**, not a data migration — `authz_backend`
  returns to `owner_only` and the grants table is simply not read.
- **The owner is never locked out of their own resource**, so no bootstrap
  problem, and no "who granted the first grant" chicken-and-egg.

Ownership confers the full lattice, including `manage_grants`. Transferring it is
an explicit, audited action gated on `manage_grants` (§9).

### 1.6 Decision and Visible — *the two answers*

**These are two different operations and building the second out of N copies of
the first is the mistake this design exists to avoid.**

```python
@dataclass(frozen=True, slots=True)
class Decision:
    """Allowed, plus why — so a denial can be audited with a reason.

    `because` is grant ids, or the literal word `owner`, or `admin_self_grant`.
    Never free text: it goes in `audit_logs.detail`, and a log that says
    "no, because the only grant reaching you is describe" is a different
    artifact from one that says "no".
    """
    allowed: bool
    because: tuple[str, ...] = ()


Visible = Everything | Subquery | Ids
```

- `Everything` — authorization disabled, or a case where no clause is needed.
- `Subquery(select)` — what `GrantsAuthorizer` returns: a SQLAlchemy `Select` of
  ids that composes into the caller's existing query as
  `.where(Dashboard.id.in_(subq))`. **One round trip, one plan, one index scan.**
- `Ids(frozenset)` — what an out-of-process authorizer would have to return.
  Present in the union so the port is honest about the shape a future
  `OpenFgaAuthorizer` would need, rather than being quietly built to exclude one.

The subquery is the whole model in one statement:

```sql
SELECT resource_id FROM grants
 WHERE resource_type = :type
   AND privilege     = ANY(:satisfying)          -- the lattice, expanded
   AND (user_id = :actor OR group_id = ANY(:groups))
UNION
SELECT id FROM dashboards WHERE owner_id = :actor      -- ownership
```

---

## 2. The three invariants

Everything else in this document is detail around these. They are restated as
enforceable rules in the Phase 7 rulebook.

> **I1. No SQL in `api/` or `services/` filters on `owner_id` directly.**
> Visibility comes from `authz.visible(...)`; permission comes from
> `authz.allowed(...)`. Verified by a grep in CI, not by review.

> **I2. Sharing an artifact never shares the data behind it.**
> A tile renders only if the viewer holds `select` on *that tile's* connection,
> re-checked at execution. This is decision 1, and it is the one that makes
> read-only sharing safe rather than merely available.

> **I3. Every authorization event is a row.**
> Grant, revoke, ownership transfer, admin self-escalation, and every 403.
> `Decision.because` travels with it. Identifiers and counts, never content —
> the rule [`audit.py`](../backend/app/services/audit.py) already states.

---

## 3. The data model

Two migrations, deliberately separate so Phase 3 can ship without Phase 4.

### 3.1 Migration `0022_groups.py`

```sql
CREATE TABLE groups (
    id           uuid PRIMARY KEY,
    name         varchar(100) NOT NULL,
    description  text,
    -- The external identity this group mirrors, when it mirrors one. Both
    -- columns or neither: an external identity is (provider, id) together.
    -- Unused until an OIDC adapter exists. Present now because retrofitting a
    -- namespace onto identifiers that grants already point at is the migration
    -- nobody wants.
    provider_id  varchar(50),
    source_id    varchar(255),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_groups_name   UNIQUE (name),
    CONSTRAINT uq_groups_source UNIQUE (provider_id, source_id),
    CONSTRAINT ck_groups_source_pair
        CHECK ((provider_id IS NULL) = (source_id IS NULL))
);

-- Membership. Meaningful only for DataMind-managed groups: when a group is
-- provider-managed, membership comes from the token and this table stays empty.
CREATE TABLE group_members (
    group_id  uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id   uuid NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    added_at  timestamptz NOT NULL DEFAULT now(),
    added_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX ix_group_members_user ON group_members (user_id);
```

### 3.2 Migration `0023_grants.py`

```sql
CREATE TABLE grants (
    id            uuid PRIMARY KEY,
    resource_type varchar(30) NOT NULL,   -- ResourceType, validated in the domain
    resource_id   uuid        NOT NULL,
    -- Exactly one principal. A CHECK, not a convention.
    user_id       uuid REFERENCES users(id)  ON DELETE CASCADE,
    group_id      uuid REFERENCES groups(id) ON DELETE CASCADE,
    privilege     varchar(20) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_grants_one_principal
        CHECK ((user_id IS NULL) <> (group_id IS NULL)),
    -- NULLS NOT DISTINCT is the point: without it Postgres treats every
    -- (…, NULL, group, priv) row as unique and the table silently accepts
    -- duplicate grants. Requires PG15+; this deployment is on postgres:16.
    CONSTRAINT uq_grants UNIQUE NULLS NOT DISTINCT
        (resource_type, resource_id, user_id, group_id, privilege)
);

CREATE INDEX ix_grants_resource ON grants (resource_type, resource_id);
CREATE INDEX ix_grants_user     ON grants (user_id)  WHERE user_id  IS NOT NULL;
CREATE INDEX ix_grants_group    ON grants (group_id) WHERE group_id IS NOT NULL;
```

**No foreign key on `resource_id`**, because it is polymorphic. The cost is that a
deleted resource can leave an orphaned grant; the answer is a `ON DELETE` hook in
the service plus a reconciler sweep, and **not** four nullable FK columns. Phase 4
writes the sweep.

**No `grantor` column beyond `created_by`.** That is the `pass_grants` decision
(§1.3) cashed out: nothing walks a chain on revoke because no chain exists.

### 3.3 What the schema deliberately does not have

| Absent | Why | What would bring it |
|---|---|---|
| `grants.expires_at` | An expiring grant needs a sweeper, a notification, and a story for "expired mid-render". None of that is MVP. | A compliance requirement, or a "share for 7 days" feature. §13. |
| `grants.parent_id` / container | Option F. Grants are per-resource until grant *count* is a real complaint. | §12 trigger: any single user holding >50 grants, or a request to "share a folder". |
| `group_members.member_group_id` | Flat groups, §1.1. | An IdP that emits nested membership DataMind must resolve itself. |
| `deny` / priority | §1.3. | Nothing. This is a "no". |
| `resource_id` FK | Polymorphic by design. | Nothing; the sweep is cheaper than the alternative. |

---

## 4. The port

```python
# app/domain/ports/authz.py — a Protocol. No I/O, no SQLAlchemy, no FastAPI.
class Authorizer(Protocol):
    async def allowed(
        self, ctx: RequestContext, ref: ResourceRef, privilege: Privilege
    ) -> Decision: ...

    async def allowed_many(
        self, ctx: RequestContext, pairs: Sequence[tuple[ResourceRef, Privilege]]
    ) -> list[Decision]: ...

    async def visible(
        self, ctx: RequestContext, type_: ResourceType, privilege: Privilege
    ) -> Visible: ...
```

Two implementations ship in this plan; the third is named to prove the port is
real, not to be written.

| Implementation | Phase | What it does |
|---|:--:|---|
| `OwnerOnlyAuthorizer` | 0 | `allowed` is `owns(...)`; `visible` is `Subquery(owner_id == actor)`. **Today's behaviour, exactly.** |
| `GrantsAuthorizer` | 4 | ownership `UNION` the grants subquery, lattice expanded. |
| *`OpenFgaAuthorizer`* | — | never. The `Ids` arm of `Visible` exists so this stays possible rather than so it happens. |

Selected by `authz_backend: Literal["owner_only", "grants"] = "owner_only"` in
`core/config.py`, resolved in [`api/deps.py`](../backend/app/api/deps.py) exactly
as `get_identity_provider` already resolves the identity port. **The default flips
to `grants` at the end of Phase 4**, and the previous value remains a working
rollback for one release.

`app.domain` may not import `sqlalchemy` — an import-linter contract already
enforces it. So `Subquery` is declared in the domain as an opaque carrier and
constructed only in `infra/authz/`. That constraint is a feature: it keeps the
port describable without a database.

---

## 5. The two operations, and where each is used

| | `allowed(ctx, ref, privilege)` | `visible(ctx, type, privilege)` |
|---|---|---|
| Question | may this actor do this to **this** thing? | which of these things may this actor see? |
| Used by | `GET /x/{id}`, every mutation, every execution | every `GET /x` list endpoint |
| Returns | `Decision` | `Visible` |
| Cost | one indexed lookup | one subquery folded into the caller's `SELECT` |
| **Anti-pattern** | — | **N calls to `allowed` in a loop.** This is Lakekeeper's L7, and rebuilding it later is the expensive version. |

Detail endpoints call `allowed`. List endpoints call `visible` and compose the
result into the query they already run. A list endpoint that filters in Python has
a bug, not a style problem — pagination will be wrong.

---

## 6. The identity seam — how OIDC arrives later, exactly

**No OIDC code is written in this plan.** What is written is the four seams that
make writing it later a contained change. Each is verified by a test in Phase 7,
because a seam nothing exercises is a comment.

| # | Seam | Phase | Status today |
|---|---|:--:|---|
| S1 | `IdentityProvider` Protocol, five methods | — | ✅ exists, [`domain/ports/identity.py:31`](../backend/app/domain/ports/identity.py#L31) |
| S2 | `users.external_subject` column | — | ✅ exists since `0001` ([`models.py:61`](../backend/app/infra/db/models.py#L61)), **never read or written** |
| S3 | `ctx.group_ids` — resolved once per request, source unknown downstream | 3 | to build |
| S4 | `groups.(provider_id, source_id)` — a group may name an external group | 3 | to build |
| S5 | `auth_provider` config switch, one implementation | 0 | to build |

**The recipe, written now so it stays honest.** When SSO is asked for:

1. Add `oidc_issuer`, `oidc_audience`, `oidc_subject_claim` (default `"sub"`),
   `oidc_groups_claim` beside the existing `auth_provider`.
2. Write `OidcIdentityProvider.verify_access_token`: fetch and cache JWKS from
   `{issuer}/.well-known/openid-configuration`; verify signature, `iss`, `aud`,
   `exp` **locally** — no introspection call per request, so the IdP is on the
   startup and key-rotation paths, never the hot path.
3. `external_subject` becomes `"{provider_id}~{subject}"`. **The prefix is not
   decoration** — a bare `sub` collides the day a second issuer appears, and
   Lakekeeper's own docs warn that changing the subject source orphans every
   existing assignment.
4. Map the groups claim through `groups WHERE provider_id = :p AND source_id = :v`.
   **Unknown values are ignored, never auto-created.** An IdP that renames a group
   must not silently create an empty, ungranted one; binding a group is a
   deliberate, audited admin act.
5. `authenticate` and `rotate_session` raise `NotImplementedError`; `/auth/login`,
   `/auth/refresh` and the `raymand_refresh` cookie disappear (decision 7).
6. First OIDC login matches on lowercased email, sets `external_subject`, clears
   `password_hash`. **Every `owner_id` and every grant still points at the same
   `users.id`** — nothing is re-granted, nothing is re-shared.

**Testing it needs no Keycloak.** Generate an RS256 keypair in a fixture, serve the
public half as a static JWKS dict, mint tokens with `pyjwt` (already a dependency),
and assert on expiry, wrong audience, wrong issuer, unknown `kid`, and group
mapping. Roughly forty lines of `conftest.py`, running in milliseconds. A real
Keycloak belongs in a manual `docker-compose.oidc.yml` that CI never starts — the
treatment `docker-compose.replicas.yml` already gets.

---

## 7. The actor problem — requests, workers, and machines

**This is the part the research did not cover and the code will hit in Phase 4.**

`RequestContext` is built in [`get_ctx`](../backend/app/api/deps.py) from a bearer
token. Background work has no bearer token, and today it improvises: `app/workers/`
reconstructs identity from `connection.owner_id`
([`benchmark.py:287`](../backend/app/workers/benchmark.py#L287),
[`knowledge_maintenance.py:310`](../backend/app/workers/knowledge_maintenance.py#L310),
[`report.py:561`](../backend/app/workers/report.py#L561)). Once `owner_id` stops
being the authorization answer, that improvisation is a hole.

**The rule: background work runs as a named principal, never as "no principal".**

```python
# Two constructors, and no third.
RequestContext.for_user(user)          # a request, from a verified token
RequestContext.on_behalf_of(user_id)   # background work, for a named user,
                                       # carrying that user's group_ids
```

- A scheduled report run executes **as the report's owner** — the same privileges,
  the same denials, the same audit rows with a flag saying the actor was not
  present. If the owner loses `select` on the connection, the scheduled run
  **fails**, and that is correct.
- There is **no god context**. A worker that needs to act without a user is a
  worker doing something the permission model does not cover, and that is a design
  conversation, not a `ctx=None`.
- `correlation_id` and `actor_ip` are empty for background actors; `audit_logs`
  already tolerates both.

This is what lets F1 (metric alerts) and F2 (scheduled reports) land later without
reopening the model — §11 makes that explicit.

---

## 8. The disclosure interaction

Each connection declares a `disclosure_policy` — how much of a result may reach
the model provider ([security.md §3](security.md)). Today, the person who chose
that policy is the only person who can trigger a query under it. **The moment a
connection is shared, one person's disclosure choice governs another person's
questions**, and that person may not know what it is.

Three rules, and they are why `describe` is a real privilege rather than a
formality:

1. **`describe` exposes the policy.** A grantee must be able to see what leaves
   *before* they ask.
2. **`modify` may not change it; `manage_grants` may.** Widening `NONE` → `FULL`
   is not an edit, it is a disclosure decision. Phase 4 splits the field out of the
   ordinary update surface.
3. **Every ask records the policy in force**, in the audit row. This is the second
   half of D4 and the sentence the README's positioning depends on.

**The unsolved half, named rather than smoothed over:** a grant to a group of forty
is a disclosure decision made on behalf of forty people, and none of Metabase,
Superset or Grafana model consent to it at all. §13 keeps it open.

---

## 9. Ownership transfer, and deleting a user

`dashboards.owner_id` is already `ON DELETE CASCADE`
([`models.py:524`](../backend/app/infra/db/models.py#L524)). Today, deleting a user
destroys only their private work. **After sharing exists, it destroys work other
people depend on** — which is why transfer ships in the same phase as the first
grant.

- `POST /{resource}/{id}/transfer` — gated on `manage_grants`, audited, and the
  new owner must be `ACTIVE`.
- `DELETE /users/{id}` refuses while the user owns any grantable resource, naming
  what they own. `_guard_last_admin` ([`users.py:132`](../backend/app/api/v1/users.py#L132))
  is the precedent for refusing a destructive action with an explanation.
- `DISABLED` keeps every grant, because disabling is reversible and re-enabling
  must not be a re-grant.

---

## 10. Errors — 404, 403, and the existence oracle

Today's pattern returns **404 for a resource you do not own**, which leaks nothing.
Grants make the distinction meaningful, and getting it backwards turns every list
endpoint into an existence oracle.

> **404 unless the caller holds at least `describe`. 403 above that.**

| Situation | Status | Audited? |
|---|---|---|
| No grant reaches the actor at all | **404** | no — it is indistinguishable from a typo |
| Holds `describe`, needs `select` | **403**, naming the privilege needed | **yes**, `DENIED` + `because` |
| Holds `select`, needs `modify` | **403** | **yes** |
| Holds `select` on the artifact, not on a tile's connection | **200** with the tile rendered as *"no access to this data source"* | **yes**, once per render |

The last row is decision 1 made visible: a partly-visible dashboard is a better
product than a refused one **and** a better product than a leaking one.

---

# Part 2 — The phases

Each phase states its goal, its activities, its gate, and what it must not change.
**A phase is done when its gate is green**, not when the code is written.

---

## Phase 0 — The vocabulary and the port · **S** · ~1 session

**Goal.** Every name this design uses exists in the tree, with tests, before
anything depends on it. No behaviour changes; nothing is deleted.

**Activities**

- [ ] `app/domain/value_objects/authz.py` — `Privilege`, `ResourceType`,
      `_SATISFIED_BY`, and `satisfying(privilege) -> frozenset[Privilege]`.
- [ ] `app/domain/ports/authz.py` — `ResourceRef`, `Decision`, `Visible`
      (`Everything | Subquery | Ids`), `Authorizer` Protocol.
- [ ] `app/infra/authz/owner_only.py` — `OwnerOnlyAuthorizer`, returning exactly
      today's answers.
- [ ] `core/config.py` — `authz_backend: Literal["owner_only","grants"] =
      "owner_only"` and `auth_provider: Literal["local"] = "local"`, the latter
      with a docstring recording decision 7 (under `oidc`, login/refresh/cookie
      disappear).
- [ ] `api/deps.py` — `get_authorizer` + `AuthzDep`, resolved like
      `get_identity_provider`.
- [ ] `services/policy.py` — add `can(ctx, resource, privilege) -> bool`
      delegating to the authorizer; keep `owns`, `can_curate` and the seven tests
      that pin them **untouched**.
- [ ] Tests: the lattice is reflexive and transitive; `manage_grants` satisfies
      all four; `describe` satisfies only itself upward; `OwnerOnlyAuthorizer`
      agrees with `owns` on a table of cases.

**Gate.** `make check` green · import-linter green (`app.domain` still imports no
`sqlalchemy`) · zero call sites changed · zero test assertions changed.

---

## Phase 1 — Step zero, part A: dashboards and reports take `ctx` · **M** · ~2 sessions

**Goal.** The two files holding **132 of the 208** `owner_id` lines stop taking a
bare UUID and stop writing their own `WHERE owner_id`. **Behaviour is identical.**

**Activities**

- [ ] `DashboardService` — `list/get/create/update/delete/tile/add_tile/
      update_tile/delete_tile/duplicate_tile/set_layout/export/import_document/
      refresh` take `ctx: RequestContext` instead of `owner_id: UUID`.
- [ ] `_owned_connection` / `_owned_llm_config` become `_authorized_connection` /
      `_authorized_llm_config`, asking the authorizer.
- [ ] `ReportService` — same treatment across its ~30 methods; `create_run`,
      `runs_of`, `run`, `cancel_run` included.
- [ ] Every `WHERE owner_id = :owner` in these two files becomes composition with
      `authz.visible(...)`.
- [ ] Routers `dashboards.py` and `reports.py` pass `ctx`, not `ctx.user_id`.
- [ ] `dashboard_transfer.py` follows the same signature change.

**Gate.** `make check` green · **no test assertion changed** · `grep -n "owner_id"
app/services/dashboard_service.py app/services/report_service.py` returns only
model-construction sites (setting the owner on create) — no comparisons.

**Must not change.** The 404-on-not-yours behaviour, tile cache keying, export
format, or any response shape.

---

## Phase 2 — Step zero, part B: everything else, and the grep gate · **M** · ~2 sessions

**Goal.** The remaining ~76 lines. When this ends, **`owner_id` is a fact stored on
a row and nothing else in `api/` or `services/` reads it to make a decision.**

**Activities**

- [ ] `run_service.py` (17), `sql_draft_service.py` (12), `semantic_service.py` (7),
      `query_service.py` (7), `knowledge_service.py` (1).
- [ ] Routers: `conversations.py` (9), `llm_configs.py` (4), `connections.py` (4),
      `semantic.py` (2), `knowledge.py` (2), `drafts.py` (2).
- [ ] `app/workers/` — introduce `RequestContext.on_behalf_of(user_id)` (§7) and
      use it in `report.py`, `benchmark.py`, `knowledge_maintenance.py`,
      `report_graph.py`. **No `ctx=None` anywhere.**
- [ ] `scripts/` CI check: `grep -rn "owner_id ==" backend/app/api backend/app/services`
      must return zero, wired into `make check` beside the existing LiteLLM grep.
- [ ] A test asserting `RequestContext` cannot be constructed without a user id.

**Gate.** `make check` green including the new grep · full suite unchanged ·
`docs/architecture.md` updated where it describes ownership as the enforcement
mechanism.

---

## Phase 3 — Principals: groups, membership, `ctx.group_ids` · **M** · ~2 sessions

**Goal.** A group exists, has members, and arrives on every request context — with
**no grants yet**, so the phase is independently useful (a group is already a
useful admin object) and independently reviewable.

**Activities**

- [ ] Migration `0022_groups.py` (§3.1) and the two models.
- [ ] `services/group_service.py` — create, rename, delete, add/remove member, list
      members, list a user's groups.
- [ ] `api/v1/groups.py` — admin-only CRUD. Rebinding `(provider_id, source_id)`
      is a **separate, audited** endpoint, because it redirects which external
      group's members flow into a set of grants.
- [ ] `RequestContext.group_ids: frozenset[UUID]`, resolved **once per request** in
      `get_ctx` and in `on_behalf_of`. One query, cached for the request.
- [ ] Audit actions: `group.created`, `group.deleted`, `group.member.added`,
      `group.member.removed`, `group.source.bound`.
- [ ] Frontend: a **Groups** tab beside Users in `UsersPage.tsx` (or a sibling
      page) — list, create, membership picker.
- [ ] Tests: membership resolution, cascade on user delete, the `ck_groups_source_pair`
      constraint, `group_ids` present on a context built both ways.

**Gate.** `make check` green · an admin can create a group and add members through
the UI · `ctx.group_ids` populated and **unused by any decision** (grep proves it).

---

## Phase 4 — Grants on connections · **L** · ~3 sessions · ⚠️ **the blocking item (D1)**

**Goal.** *"Who may read through this connection"* has an explicit, auditable,
revocable answer. This is the phase that changes what the product is.

**Activities**

- [ ] Migration `0023_grants.py` (§3.2) and the model.
- [ ] `app/infra/authz/grants.py` — `GrantsAuthorizer`: `allowed`, `allowed_many`,
      `visible` returning the `UNION` subquery of §1.6.
- [ ] `services/grant_service.py` — grant, revoke, list-for-resource,
      list-for-principal; `manage_grants` required for all four; **self-revocation
      of the last `manage_grants` is refused**, mirroring `_guard_last_admin`.
- [ ] `api/v1/connections.py` — `GET/POST/DELETE /connections/{id}/grants`, and
      `GET /connections/{id}/actions` returning *what may I do here* so the UI
      renders affordances from the server's answer rather than from a role guess.
- [ ] **Disclosure split (§8):** `disclosure_policy` moves out of the ordinary
      update payload into a `manage_grants`-gated endpoint; `describe` exposes it.
- [ ] **Ownership transfer (§9):** `POST /connections/{id}/transfer`;
      `DELETE /users/{id}` refuses while grantable resources are owned.
- [ ] **Knowledge and semantic follow the connection:** `can_curate` becomes
      `modify` on the connection; the seven existing tests must pass unchanged.
- [ ] **The 404/403 rule (§10)** implemented once, in an exception helper, not per
      router.
- [ ] Orphaned-grant sweep in `workers/reconciler.py`.
- [ ] Frontend: a **Share** panel on a data source — principal picker (users and
      groups in one dropdown, Grafana's pattern), privilege radio, current-grants
      list with revoke.
- [ ] Flip `authz_backend` default to `"grants"`; keep `"owner_only"` working for
      one release.
- [ ] Tests: lattice implication end-to-end (a `modify` holder passes a `select`
      check); group grants; revoke; the disclosure gate; transfer; the sweep;
      **a viewer with `select` on a connection may ask but may not curate.**

**Gate.** `make check` green · two users, one connection, one grant, and user B can
ask a question through user A's connection and **cannot** edit its knowledge or its
disclosure policy · every grant and revoke appears in `GET /audit`.

---

## Phase 5 — The audit half: grant, revoke, denial, escalation · **S** · ~1 session

**Goal.** [`audit.py`](../backend/app/services/audit.py) has a `DENIED` constant
and **nothing produces one**. After this phase, every authorization event is a row.

**Activities**

- [ ] `audit.record(outcome=DENIED, …)` fires on every 403, carrying
      `Decision.because`. Never on a 404 (§10).
- [ ] New actions: `grant.created`, `grant.revoked`, `ownership.transferred`,
      `access.denied`, `admin.self_granted`.
- [ ] **Admin escalation (decision 3):** an admin may grant themselves any
      privilege on any resource; it is an ordinary grant row **plus** an
      `admin.self_granted` audit row. There is no silent read path.
- [ ] The ask path records the **disclosure policy in force** (§8 rule 3) — the
      remaining half of D4.
- [ ] `GET /audit` gains filters for outcome and resource type; the frontend audit
      view surfaces denials distinctly.
- [ ] Tests: a denial writes exactly one row with a non-empty `because`; a 404
      writes none; `detail` carries **no** SQL, question text or rows.

**Gate.** `make check` green · a denied request produces a row naming the privilege
that was missing · rule 3 of `audit.py` (identifiers and counts, never content)
verified by an assertion, not by reading.

---

## Phase 6 — Reports, then dashboards, then the intersection rule · **M** · ~2–3 sessions

**Goal.** D2 read-only sharing. **Reports first because they are easy; dashboards
last because they are the hard case** — which is the opposite of the intuitive
order.

**6a — Reports** (a report's `connection_id` is single and **immutable after
creation**, [`models.py:700`](../backend/app/infra/db/models.py#L700), so there is
no intersection to resolve):

- [ ] `grants` on `resource_type='report'`; share panel; `visible` in the list
      endpoint.
- [ ] A viewer with `select` on the report **and** `select` on its connection sees
      results; missing the second gives the tile-level message of §10, not a 500.

**6b — Dashboards** (a tile carries its **own** `connection_id`,
[`models.py:578`](../backend/app/infra/db/models.py#L578), so one dashboard may
span several connections):

- [ ] **The intersection rule, implemented and documented:** the dashboard renders;
      each tile renders **iff** the viewer holds `select` on that tile's
      connection; a tile they cannot see renders as a named placeholder. Not
      hidden — hiding it makes the dashboard silently wrong.
- [ ] Sharing a dashboard **warns** when its tiles span connections the grantee
      cannot read, naming them. The share is still allowed; the surprise is not.
- [ ] `refresh` re-checks per tile, per execution (decision 1 / invariant I2).
- [ ] **The tile-cache invariant (decision 2):** the trigger sentence goes into
      `DashboardTileCache`'s docstring, and a test asserts the cache key contains
      no viewer. That test is the tripwire for anyone adding a viewer-dependent
      filter.

**Gate.** `make check` green · a two-connection dashboard shared with a user
granted one of them renders one tile and one placeholder · the cache-key test
exists and fails if a viewer is added to the key.

---

## Phase 7 — The rulebook, the seams, and the conformance check · **S–M** · ~1–2 sessions

**Goal.** Make the rules **checkable by the next person**, who will be Claude Code
with none of this context.

**Activities**

- [ ] **Write [`docs/access-control-rules.md`](access-control-rules.md)** — the
      deliverable §14 specifies. Not a summary of this plan: a short, imperative
      rulebook a feature author reads *before* writing an endpoint.
- [ ] Add a pointer to it from `CLAUDE.md`, from `docs/README.md`, and from the
      module docstring of `services/policy.py`.
- [ ] **A conformance test module** — `tests/unit/test_authz_conformance.py`:
      - every route in the app carries `ctx` and is reachable only through a
        dependency that produces one;
      - every `ResourceType` has an entry in the privilege table;
      - no `WHERE owner_id ==` in `api/` or `services/` (the Phase 2 grep, promoted
        to a test so it fails locally, not only in CI);
      - every list endpoint whose model has an `owner_id` composes `visible(...)`.
- [ ] **Seam tests (§6)** — assert `external_subject` round-trips a namespaced
      `provider~subject`; assert an unknown external group is **ignored, not
      created**; assert `ctx.group_ids` has exactly one resolution site.
- [ ] Update `docs/security.md` with a §on authorization, and `docs/architecture.md`
      where it still says sharing is impossible.
- [ ] Fill in the ledger in §15 of this document.

**Gate.** `make check` green · `docs/access-control-rules.md` exists and the
conformance test enforces every rule it states that is mechanically checkable.

---

# Part 3 — Expandability

## 11. How each future feature lands on this model

The test of this design is not what it does today. It is what the **next ten
features** cost. Each row is a real item from [mvp2-plan.md](mvp2-plan.md).

| Feature | What it needs from access control | Cost on this model |
|---|---|:--:|
| **E1 · File upload (CSV/Excel)** | An uploaded file becomes an ordinary connection | **zero** — grants, disclosure, guard and knowledge all apply unchanged. This is the single strongest evidence the model is right. |
| **E3 · Result export** | `select` on the artifact **and** its connection | **zero** — it is a read, checked like any read |
| **A1/A5 · Knowledge and synonyms** | curate = `modify` on the connection | **zero** — decided in §1.3 |
| **C1 · Pin a chat answer to a dashboard** | `modify` on the dashboard, `select` on the connection | **zero** — two existing checks |
| **D4 · Full audit** | `Decision.because` on every event | **built** in Phase 5 |
| **F2 · Scheduled reports** | A background actor with real privileges | **small** — `on_behalf_of` (§7) exists; add the schedule |
| **F1 · Metric alerts** | Same, plus "who may receive an alert about data they cannot read" | **small** — the delivery check is `select` on the connection, at send time |
| **E2 · MCP server** | A **machine** principal | **medium** — add a `service_account` principal type: a third nullable column on `grants` and one `CHECK` change. The lattice, the port and every call site are untouched. |
| **C4 · Dashboard filters/parameters** | Nothing — *until a parameter is per-user* | **zero now**; per-user parameters trip the §12 cache trigger |
| **D3 · Row-level security** | Filters attached to **groups**, applied as predicates in the generated SQL | **medium, and the model is already shaped for it** — Superset's shape. Needs the tile cache key to grow a viewer, which is why decision 2 is written as a trigger |
| **D5 · SSO / OIDC** | The five seams of §6 | **medium, and contained** — no grant changes, no schema change |
| **Option F · Workspaces** | A container that owns resources | **large but additive** — `resource_type='workspace'`, a `workspace_id` on each resource, one `OR` in the subquery. Existing grants keep working |

**Two features the model deliberately makes expensive**, because they should be
expensive:

- **Public / anonymous share links.** There is no anonymous principal, and adding
  one means deciding what "the connection's grant" means with no viewer. That is a
  product decision, and the model refusing to guess is correct.
- **Per-column masking.** It is `deny` in disguise (§1.3). It belongs with D3 or not
  at all.

## 12. The triggers — what would force a change, and what the change is

A deferral without a trigger is an omission. Each of these is a sentence someone
will one day say, paired with what to do when they say it.

| Trigger — *someone says…* | Deferred thing | The change |
|---|---|---|
| *"a tile's rows should depend on who is looking"* | per-viewer tile cache | `dashboard_tile_cache` PK becomes `(tile_id, viewer_key)`; **this is decision 2's tripwire and Phase 6 tests it** |
| *"I need to share a folder"* or one user holds >50 grants | workspaces (Option F) | migrate grants into a container; `E→F` is a real migration, `F→E` is not a thing anyone does |
| *"our IdP nests groups and we need that"* | nested groups | `group_members.member_group_id` + one `WITH RECURSIVE` in the subquery |
| *"we need SSO"* | OIDC adapter | §6, six steps, no schema change |
| *"a contractor needs access for two weeks"* | `grants.expires_at` | one column, one sweeper, one decision about mid-render expiry |
| *"a team lead should be able to share what they were shared"* | `pass_grants` | **read Lakekeeper's v4.10 changelog first** — they shipped it and then took half of it back |
| *"an agent needs its own credentials"* | service-account principal | third principal column on `grants`, one `CHECK` change |
| *"row-level security"* | D3 | needs C4, the connector port growing a per-request identity, **and** the cache trigger above |

---

# Part 4 — The rest

## 13. Genuinely open questions

Not deferrals — things this plan cannot settle.

1. **Consent to a group grant.** Granting `select` to a group of forty makes a
   disclosure decision on behalf of forty people. None of Metabase, Superset or
   Grafana model this. Do the forty get told?
2. **Whether a workspace and a tenant can be kept apart.** Every peer eventually
   grew a second, coarser boundary — Metabase's tenancy, Grafana's organisations,
   Lakekeeper's projects. Whether Option F can ship without becoming a
   multi-tenancy project is not answerable from a document.
3. **"Revoked means revoked now."** A disabled user's access ends within one
   access-token lifetime (≤15 min), because `rotate_session` refuses them. Making
   revocation immediate is a per-request user lookup — a real cost, deliberately
   not paid. Is ≤15 minutes acceptable to the first customer who asks?
4. **Whether `describe` is a privilege anyone actually grants**, or only an
   internal step in the 404/403 rule. If the latter, the UI should not offer it.
5. **Grant count at which the subquery stops being free.** The index makes it cheap
   to five figures; nobody has measured it against a realistic graph.

## 14. The rulebook — what Phase 7 must produce

**`docs/access-control-rules.md`** is a deliverable, not documentation-as-an-
afterthought. Its audience is whoever writes the *next* feature, most likely with
no memory of this plan. It must be short enough to read before writing an endpoint.

Required contents:

1. **The six concepts in one page** — principal, resource, privilege, grant,
   ownership, decision (§1), with the lattice diagram.
2. **The three invariants** (§2), stated as rules with their consequence.
3. **A checklist for any new endpoint**, which is the part people will actually
   use:
   - Which `ResourceType` does this touch? If none: why is that right?
   - Which `Privilege` does it need? Use the lattice; never check two.
   - Detail or list? Detail calls `allowed`; list composes `visible`. **Never a
     loop over `allowed`.**
   - Does it execute against a connection? Then it re-checks `select` on **that**
     connection, at execution.
   - Does it change a disclosure policy, an owner, or a grant? Then it is
     `manage_grants` and it is audited.
   - What does a caller without permission see — 404 or 403? Apply §10.
   - Does its result depend on **who is looking**? If yes, stop: that trips the
     cache trigger (§12) and needs a design conversation.
4. **A checklist for any new resource type** — the enum, the privilege table row,
   the `visible` subquery arm, the sweep entry, the conformance test.
5. **What is not in the model and must not be improvised** — no `deny`, no priority
   order, no per-endpoint role string, no `ctx=None`, no god context, no grant
   pointing at an email or an external subject.
6. **How this is enforced** — a pointer to `tests/unit/test_authz_conformance.py`
   and the `make check` grep, so a reader knows which rules a machine checks and
   which rely on them.

---

## 15. Progress ledger — what is done, what is not

**Updated at the end of every phase.** The counts are activities from Part 2.

### 15.1 Already in the tree — the foundation this plan builds on

| | Item | Where |
|:--:|---|---|
| ✅ | Argon2id + HS256 access tokens + rotating refresh with reuse detection | [`infra/identity/local.py`](../backend/app/infra/identity/local.py) |
| ✅ | `IdentityProvider` port, five methods | [`domain/ports/identity.py:31`](../backend/app/domain/ports/identity.py#L31) |
| ✅ | `users.external_subject` column (**unread**) | [`models.py:61`](../backend/app/infra/db/models.py#L61), since `0001` |
| ✅ | `Role.ADMIN` / `MEMBER`, and `ACTIVE` / `INVITED` / `DISABLED` | `domain/value_objects/` |
| ✅ | `AdminDep`, and `_guard_last_admin` as the precedent for refusing destruction | [`deps.py:97`](../backend/app/api/deps.py#L97), [`users.py:132`](../backend/app/api/v1/users.py#L132) |
| ✅ | `audit.py` with `SUCCESS/DENIED/FAILED` and nine curation actions | [`services/audit.py`](../backend/app/services/audit.py) |
| ✅ | `GET /audit`, admin-only | [`api/v1/audit.py`](../backend/app/api/v1/audit.py) |
| ✅ | `can_curate` — the template every other permission function follows | [`services/policy.py`](../backend/app/services/policy.py) |
| ✅ | Import-linter contracts keeping `domain/` free of `sqlalchemy` | `pyproject.toml` |
| ❌ | Anything that produces a `DENIED` row | — |
| ❌ | `can_read` / `can_write` / `can_administer_users` call sites (**zero**) | — |
| ❌ | Groups, grants, sharing, ownership transfer | — |

### 15.2 Phase 0 — The vocabulary and the port · **0 / 7**

- [ ] `domain/value_objects/authz.py` — `Privilege`, `ResourceType`, lattice
- [ ] `domain/ports/authz.py` — `ResourceRef`, `Decision`, `Visible`, `Authorizer`
- [ ] `infra/authz/owner_only.py` — `OwnerOnlyAuthorizer`
- [ ] `config.py` — `authz_backend`, `auth_provider` (+ decision-7 docstring)
- [ ] `deps.py` — `get_authorizer`, `AuthzDep`
- [ ] `policy.can(...)` delegating; `can_curate` and its seven tests untouched
- [ ] Lattice + authorizer tests

### 15.3 Phase 1 — Step zero, part A · **0 / 6**

- [ ] `DashboardService` takes `ctx`
- [ ] `_authorized_connection` / `_authorized_llm_config`
- [ ] `ReportService` takes `ctx`
- [ ] `WHERE owner_id` → `visible(...)` in both services
- [ ] Routers `dashboards.py`, `reports.py` pass `ctx`
- [ ] `dashboard_transfer.py` follows

### 15.4 Phase 2 — Step zero, part B · **0 / 5**

- [ ] Remaining services (`run`, `sql_draft`, `semantic`, `query`, `knowledge`)
- [ ] Remaining routers (`conversations`, `llm_configs`, `connections`, `semantic`, `knowledge`, `drafts`)
- [ ] `RequestContext.on_behalf_of` + all four workers
- [ ] `make check` grep gate: zero `owner_id ==` in `api/`, `services/`
- [ ] `architecture.md` updated

### 15.5 Phase 3 — Principals · **0 / 7**

- [ ] Migration `0022_groups.py` + models
- [ ] `group_service.py`
- [ ] `api/v1/groups.py` (+ separate audited source-binding endpoint)
- [ ] `ctx.group_ids`, resolved once per request, both constructors
- [ ] Five group audit actions
- [ ] Frontend Groups surface
- [ ] Tests: membership, cascade, constraint, context

### 15.6 Phase 4 — Grants on connections (D1) · **0 / 12**

- [ ] Migration `0023_grants.py` + model
- [ ] `GrantsAuthorizer` — `allowed`, `allowed_many`, `visible`
- [ ] `grant_service.py` (+ last-`manage_grants` guard)
- [ ] Connection grants API + `GET /connections/{id}/actions`
- [ ] Disclosure policy split behind `manage_grants`
- [ ] Ownership transfer + `DELETE /users/{id}` refusal
- [ ] `can_curate` → `modify`, seven tests unchanged
- [ ] 404/403 rule in one helper
- [ ] Orphaned-grant sweep in the reconciler
- [ ] Frontend Share panel
- [ ] `authz_backend` default → `"grants"`
- [ ] Tests: lattice, groups, revoke, disclosure, transfer, sweep, curate-denial

### 15.7 Phase 5 — The audit half · **0 / 6**

- [ ] `DENIED` on every 403, with `because`
- [ ] Five new audit actions
- [ ] Admin self-grant escalation path
- [ ] Ask path records the disclosure policy in force
- [ ] `GET /audit` filters + frontend denial view
- [ ] Tests: one row per denial, none per 404, no content in `detail`

### 15.8 Phase 6 — Reports and dashboards · **0 / 6**

- [ ] Report grants + share panel + `visible` in list
- [ ] Report viewer needs `select` on report **and** connection
- [ ] Dashboard grants
- [ ] The intersection rule + tile placeholder
- [ ] Share-time warning naming unreadable connections
- [ ] Tile-cache invariant: docstring trigger + the key test

### 15.9 Phase 7 — The rulebook and the seams · **0 / 6**

- [ ] **`docs/access-control-rules.md`** (§14)
- [ ] Pointers from `CLAUDE.md`, `docs/README.md`, `policy.py`
- [ ] `tests/unit/test_authz_conformance.py` — four mechanical rules
- [ ] Seam tests (§6): namespaced subject, unknown group ignored, one `group_ids` site
- [ ] `security.md` + `architecture.md` updated
- [ ] This ledger filled in

### 15.10 Deliberately not built — with the trigger that would change that

| | Item | Trigger |
|:--:|---|---|
| ⏳ | OIDC adapter (D5) | *"we need SSO"* → §6 |
| ⏳ | Row-level security (D3) | needs C4 + connector identity + the cache key |
| ⏳ | Workspaces (Option F) | a folder request, or >50 grants on one user |
| ⏳ | Nested groups | an IdP that emits nesting DataMind must resolve |
| ⏳ | `pass_grants` | read Lakekeeper's v4.10 changelog first |
| ⏳ | Service-account principals | E2, the MCP server |
| ⏳ | `grants.expires_at` | a time-boxed contractor |
| ❌ | External authorization service (OpenFGA) | nothing in this product's shape |
| ❌ | `deny` rules, priority ordering | nothing — this is a "no" |
| ❌ | Sharing `llm_configs` | nothing — it shares the use of a provider key |

---

## 16. The one-line acceptance test

> **Two people, one database credential, one dashboard: the second can see the
> numbers, cannot see the password, cannot change what the connection has been
> taught, cannot widen what leaves for the model provider — and every one of those
> four facts is a row in `audit_logs`.**
