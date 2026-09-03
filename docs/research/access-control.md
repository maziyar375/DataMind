# Access control — how Lakekeeper does it, and what DataMind should build

> **Subject:** [mvp2-plan.md §1.5](../mvp2-plan.md#15-single-player-by-construction) —
> *"Single-player by construction"*, and the whole of its
> [Theme D](../mvp2-plan.md#theme-d--make-it-a-team-product) (D1–D5).
> **Scope:** how **Lakekeeper** — an Apache Iceberg REST catalog in Rust — splits
> authentication (OIDC, typically Keycloak) from authorization (OpenFGA), read at
> the level of its actual `.fga` model files and its Rust `Authorizer` trait; what
> **Metabase, Superset and Grafana** do about the same problem in a BI-shaped
> product; and what DataMind should build for users, groups, roles and grants —
> **designed so that Keycloak can arrive later, or never.**
> **Desk research date:** 2026-09-02, against `main`. Lakekeeper claims are sourced
> from its documentation and from the model and source files themselves, named
> by path. Where a claim's only source is a third party rather than product
> documentation, this document says so.
> **Status:** research and options. Not a decision. §6 is an argument, §8 lists
> the decisions that have to be made before any of it is built.
> **Siblings:** [learning-loop.md](learning-loop.md) ·
> [retrieval-at-scale.md](retrieval-at-scale.md) ·
> [semantic-layer-as-a-model.md](semantic-layer-as-a-model.md) ·
> [data-surface.md](data-surface.md) (§8.1's file-ownership question is the same
> question this document answers generally).

---

## 0. The finding, in one page

**First, a correction that changes the plan rather than merely the wording.**
Keycloak is the **authentication** system — it establishes *who is calling*.
OpenFGA is the **authorization** system — it decides *what that caller may do*.
Lakekeeper's own documentation puts it in one line: *"authentication verifies
**who** you are, while authorization determines **what** you can do"*, and it
requires the first before it will enable the second.

That ordering is not pedantry. It is the whole reason the "add Keycloak later"
constraint is satisfiable:

```
   AUTHENTICATION                          AUTHORIZATION
   who is calling?                         what may they do?
   ──────────────────────────────          ──────────────────────────────
   Keycloak / any OIDC IdP                 OpenFGA / Cedar / your own table
   Lakekeeper: LAKEKEEPER__OPENID_*        Lakekeeper: LAKEKEEPER__AUTHZ_BACKEND
   DataMind:   LocalIdentityProvider       DataMind:   services/policy.py
               (Argon2id + JWT)                        (67 lines, mostly unused)

   ── these are two doors, and they are hinged separately ──
   You can build the whole right-hand side with the left-hand side still being
   email + password. Lakekeeper ships exactly that combination as its default.
```

**The most useful single fact in this research:** Lakekeeper's default is
`LAKEKEEPER__AUTHZ_BACKEND=allowall`, and its authorization lives behind a Rust
trait (`Authorizer`) with four implementations — `AllowAll`, `OpenFGA`, `Cedar`,
and "write your own". **The architecture the user is asking for — one where a
heavy external dependency can be added later without rewriting the product — is
the architecture Lakekeeper already shipped**, and it is worth copying far more
than the OpenFGA deployment is.

Four more things that re-order the work:

1. **DataMind's resource graph is two levels deep and closed. Lakekeeper's is
   unbounded.** Iceberg namespaces nest arbitrarily (`finance.emea.q3.raw`), and
   a permission granted at the top must reach a table twelve levels down. That
   recursive, unbounded inheritance is precisely what a Zanzibar-style graph
   engine exists for. DataMind has `connection → dashboard → tile` and
   `connection → report → section → block`, with **fixed depth and a fixed set of
   four resource types**. The reason OpenFGA earns its cost at Lakekeeper does
   not exist here. **Copy the model. Do not copy the service.**
2. **OpenFGA costs Lakekeeper a consistency bug it has to ship a repair tool
   for.** The catalog's Postgres is the source of truth for *which objects
   exist*; OpenFGA holds *the hierarchy and the grants*; and **the two cannot be
   written in one transaction.** Lakekeeper's answer is a `lakekeeper openfga
   reconcile` subcommand whose own module docstring says the additive-plus-delete
   mode is *"eventual consistency"* and that *"if strict consistency during the
   run is required, quiesce API writes externally for the duration"*. DataMind
   would inherit that bug the day it adopted OpenFGA, and avoids it entirely — for
   free, not by cleverness — by keeping grants in the database it already has.
3. **The seam mvp2 assumes exists does not exist yet, and that is the first
   piece of work.** `services/policy.py` opens with *"Row-level or column-level
   security later is a change in this module only."* That sentence is **not true
   of the code today**: `owns`, `can_read`, `can_write` and
   `can_administer_users` have **zero call sites**. Only `can_curate` is called,
   from four places in `knowledge.py`. Every other authorization decision in the
   product is an inlined `where owner_id == ctx.user_id` in a SQLAlchemy query —
   **208 lines** mention `owner_id` across `api/`, `services/` and `workers/`,
   concentrated in `report_service.py` (75) and `dashboard_service.py` (57). Making that docstring true is a mechanical,
   behaviour-preserving refactor, and **everything in Theme D is cheap after it
   and expensive before it.**
4. **What actually blocks sharing is not the permission model.** It is three
   decisions the permission model cannot make for you, and all three are already
   visible in the code: whose credentials a shared dashboard executes under; whether
   `dashboard_tile_cache` — keyed on `tile_id` with **no viewer in the key**
   ([`models.py:632`](../../backend/app/infra/db/models.py#L632)) — may serve one
   user's rows to another; and whether granting access to a connection also
   grants that connection's **disclosure policy**, which decides what leaves for
   the model provider. §8 has all three. A grants table that ships before those
   are answered is a feature that ships a leak.

And one correction to §1.5 itself: it says the role enum is `ADMIN | MEMBER`
"with no resource-level grants", which is right, but it implies admins can see
everything. **They cannot.** `policy.can_read` says `owns(...) or ctx.is_admin` —
and nothing calls it. Every list and get filters on `owner_id`, so an
administrator today cannot read another user's dashboard, report, conversation or
connection through any endpoint. `AdminDep` appears in exactly two routers:
`users.py` and `audit.py`. The *documented* policy and the *enforced* policy
disagree, and the enforced one is stricter. That is the good direction to
disagree in, but it should be a decision (§8.3) rather than an accident.

---

## 1. Lakekeeper, in depth

Everything in this section is read from Lakekeeper's published documentation and
from the files named. The model files are small, legible and heavily commented —
if you read one thing from this research, read
[`authz/openfga/v4.10/components/`](https://github.com/lakekeeper/lakekeeper/tree/main/authz/openfga)
in the Lakekeeper repository.

### 1.1 The split: who you are, and what you may do

**Lakekeeper issues no credentials of its own.** There is no password column, no
API-key table, no "create token" endpoint that mints a Lakekeeper secret. Every
caller arrives holding a token some external system issued, and Lakekeeper's job
is to *validate* it and *derive an identity* from it.

**Token validation is local.** Lakekeeper fetches the JWKS from the provider's
`.well-known/openid-configuration` and verifies signatures itself; it never calls
an introspection endpoint per request. **Opaque tokens are not supported** —
tokens must be JWTs. This matters more than it sounds: it means the identity
provider is on the *startup* path and the *key-rotation* path, not on the hot
path of every API call. An IdP outage does not immediately stop a running
catalog.

The configuration surface is small:

| Variable | Meaning |
|---|---|
| `LAKEKEEPER__OPENID_PROVIDER_URI` | the realm/issuer, e.g. `http://keycloak:8080/realms/iceberg` |
| `LAKEKEEPER__OPENID_AUDIENCE` | expected `aud`; docs call it *"strongly recommended"* |
| `LAKEKEEPER__OPENID_SUBJECT_CLAIM` | which claim is the user id; default tries `oid` then `sub` |
| `LAKEKEEPER__OPENID_ADDITIONAL_ISSUERS` | for providers that emit more than one `iss` |
| `LAKEKEEPER__OPENID_SCOPE` | a scope the token must carry |
| `LAKEKEEPER__OPENID_ROLES_CLAIM` | the claim carrying group/role membership |

**The user id is a namespaced string, and the namespace is the point.** The
derivation order is: `OPENID_SUBJECT_CLAIM` if set, else `oid` (Entra emits
both and `oid` is the stable one), else `sub`, else fail. The result is prefixed
with the provider id: `oidc~<subject>`. With several providers configured the
prefix is that provider's key — `okta~user@example.com`,
`eksclustera~system:serviceaccount:ns:app` — and Kubernetes service accounts
arrive as `kubernetes~<uid>`.

That prefix is doing real work. It means **two different identity systems can
name principals in the same store without colliding**, and it means an identity
is *self-describing about where it came from*. A grant tuple that says
`oidc~alice` is a grant to an identity Keycloak owns; one that says
`kubernetes~9f2c…` is a grant to a pod. §7.6 argues DataMind should adopt the
same convention **before** it has a second provider, because retrofitting a
namespace onto identifiers that grants already point at is the migration nobody
wants.

**Users are provisioned on first sight**, not pre-created: name is taken from
`name`, else `given_name`+`family_name`, else `app_displayname`, else
`preferred_username`; email from `email`, else `upn` or `preferred_username` if
either contains an `@`. A machine identity with none of those becomes
*"Nameless App with ID &lt;user-id&gt;"* unless
`LAKEKEEPER__OPENID_DISPLAY_NAME_TEMPLATE` is set.

**Humans and machines authenticate differently, and Lakekeeper does not pretend
otherwise.** Machines use OAuth2 client credentials — a Spark job carries
`credential="<client-id>:<client-secret>"` and an `oauth2-server-uri`, and talks
to Keycloak directly. Humans use authorization-code-with-PKCE or device code
through the UI. The docs concede the awkward part plainly: most Iceberg clients
cannot perform an interactive flow, so **the Lakekeeper UI has a "generate a
token" screen** for humans who need to paste one into a notebook, with a
recommended token lifetime of ≤12 hours.

**Kubernetes is a first-class second authenticator**, not an OIDC special case:
`LAKEKEEPER__ENABLE_KUBERNETES_AUTHENTICATION=true` makes Lakekeeper validate
service-account tokens through the `TokenReview` API (its own service account
needs `system:auth-delegator`). One warning in that section is a lesson in
itself: `KUBERNETES_AUTHENTICATION_SUBJECT_SOURCE` chooses between the account's
UID and its `system:serviceaccount:ns:name` name, and **it must be chosen at
initial setup, because changing it orphans every existing role assignment.**
Identity keys are load-bearing forever.

### 1.2 The OpenFGA model, read closely

Lakekeeper's authorization model is a set of `.fga` files assembled by a module
manifest. The v4.10 manifest, verbatim:

```yaml
schema: '1.2'
contents:
  - components/model_version.fga
  - components/user.fga
  - components/role.fga
  - components/server.fga
  - components/project.fga
  - components/warehouse.fga
  - components/namespace.fga
  - components/lakekeeper_table.fga
  - components/lakekeeper_view.fga
  - components/lakekeeper_generic_table.fga
  - components/lakekeeper_catalog_tag.fga
```

The hierarchy is:

```
   server
     └── project              ← the security boundary; roles live here
           ├── role           ← the group primitive
           ├── catalog_tag    ← governance vocabulary (v4.10)
           └── warehouse
                 └── namespace ─┐  nests arbitrarily deep
                       ↑        │
                       └────────┘
                       ├── lakekeeper_table
                       ├── lakekeeper_view
                       └── lakekeeper_generic_table
```

**The four assignable privileges are a lattice, and the lattice is written into
the relations themselves.** From `warehouse.fga`:

```
define describe: [user, role#assignee] or ownership or select or create or describe from project
define select:   [user, role#assignee] or ownership or modify or select from project
define create:   [user, role#assignee] or ownership or create from project
define modify:   [user, role#assignee] or ownership or modify from project or data_admin from project
```

Read the first two lines together and the implication chain falls out: `modify`
implies `select` implies `describe`. Nobody has to remember to grant "read" when
they grant "write", and no endpoint has to check two relations. **The lattice is
declared once, in the model, rather than enforced by convention at 200 call
sites** — which is exactly the property `services/policy.py` was *supposed* to
give DataMind and currently does not.

**`[user, role#assignee]` is how groups work, and it is the single most
transferable idea in the file.** The bracketed list is the set of principal types
that may hold this relation directly. `user` is a person. `role#assignee` is
*"anything that is an assignee of some role"* — a **userset**, not an id. Grant
`select` to `role#assignee` of role *analysts*, and every assignee of *analysts*
gets `select`, computed at check time. And because `role.fga` declares

```
define assignee: [user, role#assignee] or ownership
```

…a role may be an assignee of another role. **Groups nest, with no extra
machinery and no denormalised membership table.**

**Inheritance runs downward for privileges and upward for visibility.** Downward
is the `from parent` clauses above. Upward is this, from `namespace.fga`:

```
define can_get_metadata: describe or can_get_metadata from child
```

*You can see a namespace if you can see anything inside it.* Without that rule a
user granted `select` on one deep table could not navigate to it — every
ancestor would be invisible. The docs call this *"bottom-up"* inheritance and are
careful about its limit: *"only items in the direct path are presented to
users"*, so it reveals the path to what you may read and nothing beside it.

**This upward rule is the one part of the model that is genuinely awkward to
express in SQL**, and it is worth being honest that it is awkward: in a
relational schema it is a recursive `EXISTS` over descendants, and it is the
strongest single argument for a graph engine. It is also **an argument DataMind
does not get to use**, because DataMind's tree is two levels deep with a known
shape — the "upward" query is a join, not a recursion (§7.5).

**Ownership, grant authority, and the v4.10 delegation change.** Three separate
relations carry authority, and separating them is deliberate:

```
define ownership:     [user, role#assignee]
define pass_grants:   [user, role#assignee]
define manage_grants: [user, role#assignee] or ownership or security_admin from project
```

- `ownership` — you made it. Confers the privilege lattice and, normally, grant
  authority.
- `manage_grants` — full administration of who may do what here and below;
  inherits down the tree.
- `pass_grants` — *"you may hand out privileges you yourself hold"*. It is
  directly assignable at every level and **never inherits**.

The grant actions read as `can_grant_select: manage_grants or (select and
pass_grants)` — you can grant `select` if you administer this object, *or* if you
hold `select` and hold the right to pass it on.

**Then v4.10 took half of `pass_grants` away, and the reasoning is the best
paragraph in the repository.** Revoking now requires `manage_grants` at every
level:

```
# REVOKE Permissions
# Taking a privilege back is administration, never delegation
define can_revoke_select: manage_grants
```

The changelog explains why:

> *"The point is delegation depth: every grant is now one hop from someone
> holding `manage_grants`, so there is no chain of delegated grants to unwind
> when access is withdrawn — which is what makes storing no grantor on a grant
> safe."*

That is a design decision paid for by an operational property: because no grant
can be more than one delegation deep, **a grant row does not need to record who
issued it**, and revocation never has to cascade. §7.3 recommends DataMind adopt
the *endpoint* of this evolution (no `pass_grants` at all, initially) rather than
repeat the intermediate step and the behaviour-breaking correction.

**`managed_access` is the "ownership is not enough" switch**, and it is one
`but not` clause:

```
define managed_access: [user:*, role:*]
define managed_access_inheritance: managed_access or managed_access_inheritance from parent
define manage_grants: [user, role#assignee]
                      or (ownership but not managed_access_inheritance from parent)
                      or manage_grants from parent
```

Turn `managed_access` on for a subtree and **owners inside it stop being able to
grant** — grant authority centralises on whoever holds `manage_grants` above.
This is the model's answer to *"the finance namespace is not something a table
author gets to share"*, and it is a single flag rather than a second permission
system. v4.10 also closed the obvious hole: `can_move` on a namespace requires
`manage_grants and modify`, specifically so a namespace cannot be populated under
a permissive parent and then moved into a managed subtree, smuggling its grants
past the control.

**Reads of the permission state are themselves permissioned, and asymmetrically
so.** `can_read_assignments` on a warehouse is defined as the disjunction of
every `can_grant_*` on it — with a blunt comment: *"Only if we can GRANT a
privilege, we can LIST them for now."* Roles go the other way: `role.fga` has no
directly-assignable read relation at all, and both `can_read` and
`can_read_assignments` derive from the project's `can_list_roles`. Its comment
explains the trade openly — role reads are *"PROJECT-UNIFORM"*, so whoever may
list roles may read the membership of **every** role in the project, and that
uniformity is what lets a transitive membership listing be authorized with **one**
project-level check instead of one check per role. **A permission model's own
read surface is a design problem with a performance answer**, and Lakekeeper
picked performance and wrote down what it cost.

### 1.3 What the model deliberately does not do

Three absences, each load-bearing:

- **No row or column filtering.** OpenFGA answers *may this principal perform
  this action on this object* — it never rewrites a query. Lakekeeper's row-level
  story lives in the engines above it (or in Cedar's attribute conditions), not
  here. This is the same boundary [mvp2 §D3](../mvp2-plan.md#d3-row-level-security--l--scope-out-of-mvp2-deliberately)
  draws for DataMind, and it is drawn in the same place for the same reason.
- **Almost no negative permissions.** The model has exactly one `but not`, and it
  is the `managed_access` clause. Deny rules compose badly and make "why can this
  person do this?" unanswerable; the model prefers to make the positive path
  narrower.
- **No conditions on time, IP, or request attributes.** Relationship tuples are
  static facts. Anything contextual is the *other* backend's job — which is why
  the other backend exists (§1.6).

### 1.4 The implementation: how a check actually happens

**The whole of authorization is behind one trait**, in
`crates/lakekeeper/src/service/authz/mod.rs`:

```rust
pub trait Authorizer
where Self: Send + Sync + 'static + HealthExt + Clone + std::fmt::Debug,
{
    type ServerAction: ServerAction;
    type ProjectAction: ProjectAction;
    type WarehouseAction: WarehouseAction;
    type NamespaceAction: NamespaceAction;
    type TableAction: TableAction;
    /* … one associated type per resource kind … */

    fn implementation_name() -> &'static str;
    async fn can_bootstrap(&self, metadata: &RequestMetadata) -> Result<()>;
    async fn are_allowed_table_actions_impl<A>(…)
        -> Result<Vec<AuthorizationDecision>, IsAllowedActionError>;
    async fn delete_user(&self, metadata: &RequestMetadata, user_id: UserId) -> Result<()>;
    async fn create_role(&self, …) -> Result<()>;
    /* … */
}
```

Four properties of that trait are worth stealing wholesale:

1. **The action vocabulary is a typed enum per resource, not a string.**
   `CatalogTableAction::{Drop, Undrop, WriteData, ReadData, GetMetadata, Commit,
   Rename, …}`. A typo is a compile error, and `strum::EnumCount` means the
   introspection endpoints can enumerate the complete set.
2. **Single and batch are both in the trait, and batch has a working default.**
   The doc comment is explicit that `are_allowed_x_actions` exists because
   *"sending a separate request for each check is inefficient"*, and that the
   default implementation *"just call[s] `is_allowed_x_action` in parallel"* for
   backwards compatibility. A new authorizer is correct immediately and fast
   later.
3. **Lifecycle hooks are part of the interface.** `delete_user`, `create_role`,
   `delete_role` — the authorizer is *told* when the world changes, rather than
   discovering it. This is what keeps two stores in step; §1.5 is what happens
   when it is not enough.
4. **A decision is a value, not a bool.** `AuthorizationDecision { allowed,
   determined_by: Vec<DeterminingFactor> }`, and the module docstring says the
   type is *"authorizer-agnostic … so they can live in the audit-event payload
   while each authorizer maps its own diagnostics down to them"*. OpenFGA and
   AllowAll return empty diagnostics; a policy engine returns the policies that
   matched. **The audit log gets "why", not just "no"** — which is exactly the
   gap [audit.py](../../backend/app/services/audit.py)'s `DENIED` constant is
   currently able to record but has nothing to fill.

**How a list endpoint is filtered — and it is not by checking each row.** For
projects, `authorizer.rs` first asks one question, then falls back to a reverse
index:

```rust
let list_all = self.check(CheckRequestTupleKey {
    user: actor.to_openfga(),
    relation: ServerRelation::CanListAllProjects.to_string(),
    object: self.openfga_server().clone(),
}).await?;
if list_all { return Ok(ListProjectsResponse::All); }

let projects = self.list_objects(
        FgaType::Project.to_string(),
        CatalogProjectAction::IncludeInList.to_openfga().to_string(),
        actor.to_openfga(),
    ).await? /* … parse ids … */;
```

`ListObjects` is *"give me every object of type T on which this user has relation
R"* — the inverse of a check. For deeper resources it batches instead:
`batch_check` chunks items to `max_batch_check_size`, fans the chunks out with
`try_join_all`, correlates responses by index, and **fails the whole request if
any item is missing from the response** (`MissingItemInBatchCheck`). It never
quietly treats an absent answer as a deny — an unanswered check is an error, not
a "no". That is the right posture and it is worth naming: *fail closed on the
request, not fail closed on the row*, because a silent per-row deny is
indistinguishable from a correct empty list.

**AllowAll is a real implementation, not a stub.** `allow_all.rs` is 18 KB
because it still has to answer the introspection endpoints honestly — *which
privileges exist on this resource type* — even though every check returns
`allow`. That size is the honest price of the pattern, and it is the pattern that
lets `LAKEKEEPER__AUTHZ_BACKEND=allowall` be the default and still be the same
code path as production.

### 1.5 The price: two stores, one truth

This is the section to read if the only question is *"should DataMind run
OpenFGA?"*

**Postgres holds which objects exist. OpenFGA holds the hierarchy between them
and every grant.** Under normal operation Lakekeeper writes both on every
mutating API call. **They are not one transaction, and they cannot be** — one is
a SQL database, the other is a gRPC service with its own database.

The repair tool is `crates/authz-openfga/src/reconcile.rs`, and its docstring is
unusually candid:

> *"`rebuild_hierarchy_tuples_from_catalog` — additive only. … Never deletes. Safe
> under concurrent writes (no lock required). `reconcile_hierarchy_tuples_from_catalog`
> — additive **plus** drift deletion. The caller passes a lock guard to serialize
> concurrent reconciles…"*

and, on the deletion mode:

> *"This entry point does **not** stop API writes. The catalog snapshot is built
> before the OpenFGA walk; concurrent renames or creates between those two reads
> can cause **transient** inconsistencies… All of these self-heal on the **next**
> reconcile run… If strict consistency during the run is required, quiesce API
> writes externally for the duration."*

Note what the deletion mode is careful *not* to touch: *"Ownership tuples,
grants, role assignments, and bootstrap admin tuples are **never** touched."*
Only the structural skeleton is rebuildable from the catalog. **Grants have no
second source of truth — if OpenFGA loses them, they are gone.** Which means
OpenFGA's database is now a thing that must be backed up on the same schedule,
and restored to the same point in time, as the catalog's. Two stateful services,
one backup story, and a documented ~80k tuples/sec full-store scan when they
drift.

**The authorization model is itself versioned and migrated**, which is a cost
nobody budgets for. `authz/openfga/README.md` is a changelog with two flags per
version — `MODIFIES_TUPLES` and `ADDS_TUPLES` — and the v4.0 entry shows why they
exist: table identity changed from `table_id` to `warehouse_id/table_id`, so
*"for each tuple referencing a table or view, the migration adds a new tuple
according to the new object representation."* A schema migration in your
authorization store, with a data backfill, executed by migration functions passed
to a model manager. v4.9 never shipped and had to be superseded by v4.10 because
a `main` build had already provisioned stores with it and *"the model is selected
by version"*.

The operational fine print is short but real: **OpenFGA v1.11 or later** is
required (Lakekeeper depends on `on_duplicate: ignore` write semantics),
`OPENFGA_MAX_TUPLES_PER_WRITE` must be at least 100, and the production tuning
guidance turns on four separate caches
(`OPENFGA_CHECK_QUERY_CACHE_ENABLED`, `OPENFGA_CHECK_ITERATOR_CACHE_ENABLED`,
`OPENFGA_CACHE_CONTROLLER_ENABLED`) with connection pools sized at 200/100.
Caches on an authorization service trade correctness-in-time for latency; that is
a knob DataMind would rather not own.

### 1.6 The escape hatch they built: Cedar

**Lakekeeper's newer second authorizer is the one that fits a small deployment,
and its existence is the strongest evidence for this document's recommendation.**

Cedar is *built into* Lakekeeper — **no external service, no second database.**
Its stated difference is one line: *"Permissions are policies, not grants"*, and
the consequence is blunt: **under Cedar the Grants API rejects writes.** There is
no runtime "share this with Bob" — access comes from policy source you deploy.

```cedar
permit (
    principal is Lakekeeper::User,
    action in [Lakekeeper::Action::"TableSelectActions"],
    resource is Lakekeeper::Table
) when {
    resource.warehouse.name == "prod" &&
    principal.project_roles.contains({provider_id: "oidc", source_id: "analysts"})
};
```

Policies load from local files or Kubernetes ConfigMaps, reload every
`LAKEKEEPER__CEDAR__REFRESH_INTERVAL_SECS` (default 5), validate against a
published schema at startup, and **an invalid policy fails the boot** — with
atomic reload, so a bad edit keeps the previous configuration rather than
half-applying. Roles come from the token's `LAKEKEEPER__OPENID_ROLES_CLAIM`
rather than from a database, and Cedar adds the one thing OpenFGA's tuples cannot
express: **attribute conditions**, including a genuinely clever ABAC path where
Iceberg table properties prefixed `access-` / `access_` are parsed into policy-
visible tags.

The trade is stated plainly by the docs and is the right frame for DataMind's own
choice:

| | Cedar | OpenFGA |
|---|---|---|
| Model | policy-as-code | runtime grants |
| External service | **none** | required (+ its own DB) |
| Who changes access | whoever can deploy policy | admins and owners, at runtime, in the UI |
| Grants API | rejects writes | full lifecycle |
| Conditions on attributes | yes | no |

**And note what Cedar costs at bootstrap:** *"Cedar: bootstrap grants no
permissions; access derives from policy sources or Instance Admins
configuration."* An engine with no runtime grants has no "first admin" to create
— you configure `LAKEKEEPER__INSTANCE_ADMINS=["oidc~alice"]` instead. That is a
genuinely different failure mode, and §7.6 uses it.

### 1.7 Bootstrap, and the lesson in it

`POST /management/v1/bootstrap` runs **once, ever**. The caller's token decides
who the first administrator is; `{"is-operator": true}` additionally grants the
most powerful role in the system, meant for a Kubernetes operator provisioning
resources. With authentication disabled there is no caller identity, so **no
admin is established at all**.

That last clause is the trap, and it is the one DataMind must design around. The
docs do not cover the transition — my reading of the bootstrap page found the
"enable auth after bootstrapping without it" case **undocumented**, which is
itself the finding. A product that can run open and then be closed has to answer
*what happens to identities and grants created while it was open*, and Lakekeeper
answers it by refusing the combination in its commercial build
(`LAKEKEEPER__INSECURE_ALLOW_UNAUTHENTICATED=true` is required to run without an
authenticator at all) rather than by migrating it.

**DataMind's situation is better and it should stay better.** DataMind will never
run without authentication — it has real local accounts today. So the transition
it must survive is not *none → Keycloak* but *local → Keycloak*, which is a join
between two identifiers rather than the invention of one. §7.6 is that join, and
the `users.external_subject` column that already exists —
[`models.py:61`](../../backend/app/infra/db/models.py#L61) — is where it lands.

---

## 2. Worth knowing: where this shape came from

Three paragraphs of background, because the vocabulary is otherwise unreadable
and because the origin explains what the tools are *for* — which is the fastest
way to see whether you have that problem.

**Zanzibar.** Google published *"Zanzibar: Google's Consistent, Global
Authorization System"* in 2019, describing the service that answers "may this
user open this Doc / watch this video / see this Calendar event" for essentially
every Google product. Its two defining constraints are **cross-service** (Docs
and Drive and Gmail must agree) and **globally distributed at enormous scale**,
and its answers to those constraints — relationship tuples as the only data
model, a "zookie" consistency token so a caller can demand a snapshot no older
than its own last write, and aggressive caching — are the constraints that shaped
every descendant. **OpenFGA (CNCF, from Auth0), SpiceDB (AuthZed) and Permify are
all re-implementations of that paper.** They inherit its shape, and — this is the
part that matters here — **they inherit its shape whether or not you have its
problems.**

**ReBAC vs RBAC vs ABAC**, since all three appear above. *RBAC*: a principal
holds roles, a role holds permissions, and the answer is a set lookup — this is
Superset's model, and DataMind's `ADMIN | MEMBER` is a two-row version of it. It
does not express "on which object", so it goes wrong the moment two dashboards
need different audiences. *ReBAC*: permission is derived from a **path through a
graph** — Alice → member-of → analysts → viewer-of → folder → parent-of →
dashboard. This is Lakekeeper's OpenFGA model, and its advantage is precisely
**unbounded, recursive inheritance**. *ABAC*: permission is a **predicate over
attributes** of principal, resource and request — Cedar's `when { … }` clauses,
Superset's row-level filters, Metabase's sandboxing. Real systems are usually
ReBAC for structure and ABAC for the exceptions, which is exactly what Lakekeeper
became by shipping both.

**And the honest summary for a product this size.** The check-service
architecture solves three problems: many services needing one answer; a
relationship graph too deep or too wide to traverse in the request; and
consistency across data centres. **DataMind has one service, one Postgres, and a
graph two edges deep.** It has none of the three. What it should take from
Zanzibar's descendants is the *vocabulary* — principals, relations, a privilege
lattice, usersets for groups, grant authority as its own relation — because that
vocabulary is genuinely better than the ad-hoc alternative, and it is free. What
it should not take is the network hop.

---

## 3. The BI-shaped precedent — because Lakekeeper is not the closest analogue

Lakekeeper is a **catalog**: its resources are data, its consumers are engines,
and its permission model is about tables. DataMind is a **BI tool**: its
resources are *saved artifacts about* data — dashboards, reports,
conversations — and its consumers are people in browsers. The products that have
already solved DataMind's exact problem are Metabase, Superset and Grafana, and
all three converged on the same two-axis answer that neither Lakekeeper nor this
codebase currently has.

*The claims below are from each product's own documentation, summarised; where a
detail is version- or tier-dependent, that is noted rather than smoothed over.*

### 3.1 Metabase — two orthogonal axes, and the naming to go with it

Metabase splits permissions into **data permissions** (which databases, schemas
and tables a group may query) and **collection permissions** (which saved
questions, dashboards and models a group may see and curate), plus a third
**application permissions** axis for admin features. Groups are the only
principal permissions attach to — there is no per-user grant.

The interaction between the two axes is the interesting part, and Metabase
documents it explicitly: collection permissions govern *viewing and curating
existing* questions and dashboards, but **changing a question's query, or writing
a new one, requires data permissions on the underlying source.** So "you may look
at this dashboard" and "you may ask this database things" are separate grants
that compose, and the composition is what makes read-only sharing safe.

Metabase's row-level story — *"row and column security"*, formerly *"data
sandboxing"* — is a paid tier, as is granular per-schema/per-table view
permission. That tiering is itself informative: **the two-axis model is the free,
universal part; per-row filtering is the part everyone charges for**, because it
is the part that is genuinely hard.

### 3.2 Superset — the cautionary tale, and it is a specific one

Superset's model is Flask-AppBuilder RBAC: permissions are fine-grained strings
(`can_add`, `can_edit`, `can_show`, `all_datasource_access`, …) bundled into
roles, with built-ins (`Admin`, `Alpha`, `Gamma`, `sql_lab`, `Public`)
re-synchronised on `superset init`. A user's effective permission is the union of
their roles'. It is a permission *bag*, not a graph, and it works.

**The cautionary detail is `DASHBOARD_RBAC`.** With that feature flag on, a role
can be granted access to a dashboard — and per Superset's documentation, granting
that access **bypasses dataset-level checks and implicitly grants read access to
all the dashboard's charts and thereby all their datasets.** That is a perfectly
defensible product decision (a dashboard nobody can render is useless) and it is
also **exactly the leak §1.5 of mvp2 warns about**, shipped as a feature flag:
share the artifact, and the data comes with it.

Superset's row-level security is genuinely good and worth copying the *shape* of:
RLS filters attach to roles and are applied as predicates in the generated SQL,
so they hold even for a caller who reaches the data another way; a user in
several roles gets their filters combined. **Enforcement in the query, not in the
UI** — the same posture DataMind's guard already takes.

### 3.3 Grafana — the container is the unit that scales

Grafana's answer is **folders**. Permissions assigned to a folder apply to
everything in it — dashboards, alert rules, and more — and inheritance *"always
flows downward"*. Above that sit coarse organisation roles (Viewer / Editor /
Admin); fine-grained per-resource RBAC is an Enterprise/Cloud feature. Principals
are **users, teams, or service accounts**, chosen from one dropdown.

Two things to take. First, **teams exist as a first-class principal from the free
tier upward**, because per-user grants do not survive contact with staff turnover.
Second, **the folder is the unit that keeps the grant count small** — a hundred
dashboards in six folders is six grants per audience, not six hundred. DataMind
has no container today, and §6 Group II treats introducing one as a real option
rather than an afterthought.

### 3.4 What the three agree on

| | Metabase | Superset | Grafana | Lakekeeper |
|---|---|---|---|---|
| Principal | group only | role | user / team / service account | user / role (nestable) |
| Container for inheritance | collection | — (dataset↔dashboard links) | folder | namespace (unbounded) |
| Artifact vs data permissions | **two explicit axes** | one bag, and they interact badly | folder + datasource perms | one tree; data *is* the resource |
| Row-level filtering | paid tier | free, in-query, role-attached | datasource-dependent | out of scope (engine's job) |
| Per-resource grants | via collections | feature-flagged | Enterprise | core |

Three agreements, and each is a decision DataMind has not made yet:

1. **"May see the artifact" and "may query the data" are two grants, not one.**
   All three keep them separate; Superset's flag that merges them is documented
   as a bypass. For DataMind this is the difference between sharing a dashboard
   and sharing a connection — and because a DataMind tile carries its **own**
   `connection_id`, the product cannot even ask the question at dashboard level
   without deciding what to do when one dashboard's tiles span two connections
   (§5.2).
2. **A group/team/role is the principal that matters; per-user grants are the
   exception.** Nobody built per-user-only.
3. **A container is what keeps the model usable**, and the container is
   permissioned, not the leaf.

---

## 4. Eight lessons

**L1. Split the two doors, and ship the authorization one first.** Authentication
and authorization are separable, and Lakekeeper ships them separately: OIDC is
optional, `AUTHZ_BACKEND` is separately optional, and the default combination is
"authenticated by nobody, authorized as everybody". DataMind's asymmetry is the
mirror image — it has real authentication and no authorization — which means the
Keycloak question can be postponed **without postponing anything else**.

**L2. Put the decision behind a port before you need two implementations.** The
`Authorizer` trait is what makes `allowall` / `openfga` / `cedar` / your-own a
config value. DataMind already has this pattern for identity
([`domain/ports/identity.py`](../../backend/app/domain/ports/identity.py)),
secrets, LLMs and connectors. It does **not** have it for authorization, despite
`policy.py` claiming to be it.

**L3. Declare the privilege lattice once, in data, not at the call sites.**
`define select: … or modify` costs one line and removes an entire class of bug —
the endpoint that checks `read` but forgets that `write` implies it. Two hundred
inlined `owner_id ==` comparisons are two hundred chances to get it wrong.

**L4. Groups are a userset, and they should nest.** `[user, role#assignee]`
gives nesting for free. DataMind should model a group as a principal *type*, not
as a column on `users`, precisely so that "the analytics group" can later mean "a
Keycloak group" without any grant changing.

**L5. Revocation is administration; delegation depth is a liability.** v4.10's
change — grant may be delegated, revoke may not — buys the property that **no
grant is more than one hop from an administrator**, which is what lets a grant row
omit its grantor and lets revocation avoid cascading. Start there instead of
arriving there.

**L6. A denial should carry a reason, and the reason belongs in the audit log.**
`AuthorizationDecision.determined_by` is deliberately authorizer-agnostic *so it
can live in the audit payload*. DataMind's `audit.py` already has a `DENIED`
outcome and nothing that produces one.

**L7. Filtering a list is a different operation from checking a row, and if you
build it as N checks you will rebuild it later.** Lakekeeper checks "can list
all" first, then falls back to a **reverse index** (`ListObjects`), and batches
where it cannot. In Postgres the equivalent is one `EXISTS`/join against the
grants table — but only if grants are *in* Postgres. Choosing an external
authorizer converts every list endpoint into a two-phase query, and that is the
cost nobody prices in advance.

**L8. Two stores means a reconciler, a second backup, and a model-migration
story.** Not a criticism of Lakekeeper — it is the correct engineering for a
catalog that must serve many engines. It is a straightforward *disqualification*
for a single-service product on a constrained server, and Lakekeeper itself
provides the alternative (Cedar) rather than insisting.

---

## 5. What DataMind already has — precisely

### 5.1 The starting position, in numbers

| | |
|---|---|
| API surface | **106 endpoints** across 11 routers in [`backend/app/api/v1/`](../../backend/app/api/v1/) |
| Roles | **2** — `Role.ADMIN`, `Role.MEMBER` ([`value_objects/__init__.py`](../../backend/app/domain/value_objects/__init__.py)) |
| User states | `ACTIVE`, `INVITED`, `DISABLED` |
| Groups / teams / workspaces | **none** |
| Resource-level grants | **none** |
| Authorization module | [`services/policy.py`](../../backend/app/services/policy.py), **67 lines** |
| …of which are actually called | **one function** (`can_curate`), from 4 sites in `knowledge.py` |
| Inlined ownership checks | **208 lines** mentioning `owner_id` in `api/` + `services/` + `workers/` (254 occurrences) |
| …concentrated in | `report_service.py` (75), `dashboard_service.py` (57), `run_service.py` (17) |
| `AdminDep` call sites | **2 routers** — `users.py`, `audit.py` |

Authentication, by contrast, is in good shape and does not need replacing:
Argon2id with tunable cost, HS256 access tokens at 15 minutes, rotating refresh
tokens in an HttpOnly cookie at 14 days with **reuse detection that kills the
whole family** ([`local.py:159`](../../backend/app/infra/identity/local.py#L159)),
hashed refresh tokens, and session revocation on admin password reset.
[security.md §6](../security.md) describes it accurately.

**And the OIDC hooks are already in the tree, unused.** Three of them:

- `IdentityProvider` is a `Protocol` in
  [`domain/ports/identity.py:31`](../../backend/app/domain/ports/identity.py#L31)
  with five methods, and `LocalIdentityProvider` is one implementation of it.
- `AuthenticatedIdentity` carries `external_subject: str | None`
  ([line 20](../../backend/app/domain/ports/identity.py#L20)) and `users` carries
  the matching column ([`models.py:61`](../../backend/app/infra/db/models.py#L61),
  present since migration `0001`). **Nothing reads or writes either.**
- `local.py`'s own module docstring says it out loud:

  > *"Swapping this for Keycloak means writing an `OidcIdentityProvider` and
  > flipping a config value. `services/` never changes, because it only ever sees
  > `RequestContext.user_id` and `RequestContext.role`."*

That claim is **half true**, and the half that is false is the subject of this
document. `services/` does only see `user_id` and `role` — so the *authentication*
swap really is contained. But `role` is a two-valued string that everything
downstream compares by hand, so *authorization* is not contained anywhere, and
the second half of that sentence is what stops being true the moment a third role
or a per-resource grant exists.

### 5.2 Five things the code says that §1.5 does not

**(a) `policy.py` is a seam in name only, and this is the finding that sets the
order of work.** Its docstring promises *"Row-level or column-level security
later is a change in this module only."* The call graph:

```
   can_curate(ctx, settings, resource)   ←  knowledge.py:102, :152, :328, :489
   owns(ctx, resource)                   ←  policy.py itself, and nowhere else
   can_read(ctx, resource)               ←  NOTHING
   can_write(ctx, resource)              ←  NOTHING
   can_administer_users(ctx)             ←  NOTHING
```

Everything else authorizes by construction — `dashboard_service.get()` takes
`owner_id` as a *parameter* and puts it in the `WHERE` clause, so the wrong
answer is not "denied", it is "not found". That is a defensible pattern (it
leaks nothing, not even existence) but it is **not a policy layer**, and no
amount of editing `policy.py` will change it. **Making that docstring true is
step zero**, and it is behaviour-preserving: today's rule *is* `owns`, so routing
every call site through a function that returns `owns` changes nothing except
where the decision lives.

**(b) The services take `owner_id`, not `ctx` — and that signature is the actual
migration.** `DashboardService.get(dashboard_id, owner_id)`,
`.update(dashboard_id, owner_id, **changes)`, `.tile(dashboard_id, tile_id,
owner_id)`, `_owned_connection(connection_id, owner_id)` — the identity arrives
as a bare UUID with no room for "…or a group they belong to, with at least
`select`". Every one of those becomes either a `ctx`-shaped parameter or a
*resolved id set*. 132 of those 208 lines are in two files, which is good news
— it is a large mechanical change in a small number of places, not a diffuse one.

**(c) `dashboard_tile_cache` has no viewer in its key, and that is fine now and
fatal later.** The table is keyed on `tile_id` alone
([`models.py:632`](../../backend/app/infra/db/models.py#L632)), and freshness is
decided by `result_fingerprint(tile)` — a SHA-256 over `connection_id`, `sql`,
`max_rows` and `chart_config`
([`dashboard_service.py:83`](../../backend/app/services/dashboard_service.py#L83)).
No user id, by design: the rows a tile produces do not currently depend on who is
looking.

Under **grant-based sharing that stays correct** — everyone who may see the tile
sees the same rows, because execution happens under the *connection's* grant, not
the viewer's. Under **row-level security it becomes a cross-user data leak**, and
a silent one: two users with different row filters would share a cache entry.
**This is the single most concrete argument for writing D3's deferral down as a
trigger rather than an omission**, and the trigger is exactly "the first time a
tile's result depends on the viewer, this table grows a viewer in its key."

**(d) A dashboard's shareability is an intersection, not a property.** Tiles carry
their own `connection_id`
([`models.py:578`](../../backend/app/infra/db/models.py#L578)) and are validated
against `_owned_connection(connection_id, owner_id)` — so **one dashboard may span
several connections**. "Share this dashboard with Bob" therefore has no
well-defined meaning until you decide what happens to a tile whose connection Bob
may not read: hide the tile, refuse the share, or share it and leak. Reports are
easier — `Report.connection_id` is single and **immutable after creation**
([`models.py:700`](../../backend/app/infra/db/models.py#L700), *"a report keyed to
one connection cannot cross disclosure policies"*) — and conversations bind one
connection the same way. **Dashboards are the hard case and should be sequenced
last**, which is the opposite of the intuitive order.

**(e) `curation_admin_only` already anticipates all of this, and says so.**
[`policy.py:31`](../../backend/app/services/policy.py#L31) is the one place in the
codebase that has already thought about shared connections:

> *"It starts mattering the moment mvp2 §D1 lands and a connection can be
> *shared*: a reader granted access to somebody's connection may then ask it
> questions and may not rewrite what it has been taught."*

That is the correct rule and it is already implemented. **`can_curate` is
therefore the template for every other permission function** — including the
detail that omitting the resource asks the *strict* question, because *"the
fail-closed reading of 'I don't know who owns this' is no."* Seven tests in
[`test_audit_and_permissions.py`](../../backend/tests/unit/test_audit_and_permissions.py)
already pin that behaviour.

### 5.3 What a grant would have to reach

For completeness, the resources that would need one, and their current scoping
column:

| Resource | Table | Scoped by | Sharing is… |
|---|---|---|---|
| Connection | `database_connections` | `owner_id` | **D1 — blocking; everything else depends on it** |
| LLM config | `llm_configs` | `owner_id` | probably never; it holds a provider key |
| Dashboard | `dashboards` | `owner_id` | D2, and the hard case (§5.2d) |
| Report | `reports` | `owner_id` | D2, and the easy case |
| Conversation / run | `conversations`, `runs` | `owner_id` | arguably never — a transcript is personal |
| Semantic layer | `semantic_layers` | via `connection_id` | follows the connection |
| Knowledge templates | `knowledge_templates` | via `connection_id` | follows the connection, gated by `can_curate` |
| Audit log | `audit_logs` | admin-only reader | stays admin-only |

Two observations worth making before §6. First, **the semantic layer and the
knowledge store already follow the connection rather than a user**, which means
D1 alone — connection grants — brings a meaningful amount of team behaviour with
it at no extra modelling cost. Second, **`llm_configs` is the resource that should
never be shared**, because sharing it shares an encrypted provider key's *use*;
it is worth naming that in the model rather than leaving it to be noticed later.

---

## 6. Options

Three groups, because three genuinely independent decisions are hiding inside
"add user management": **where the decision is computed**, **what the unit of
sharing is**, and **who issues identity**. They can be answered in any order, but
they must be answered separately — conflating them is how a project ends up
deploying Keycloak in order to share a dashboard.

### Group I — Where the authorization decision is computed

#### Option A · A grants table in DataMind's own Postgres, behind an `Authorizer` port · **M**

Principals, groups, and grants become tables in the app database. Checks are SQL.
List filtering composes into the existing query as a subquery. The port is a
`Protocol` in `domain/ports/`, exactly like `IdentityProvider`, with two
implementations from day one: `OwnerOnlyAuthorizer` (today's behaviour, the
default, and what CI runs) and `GrantsAuthorizer`.

- **For:** one store, one transaction, no reconciler, no second backup, no
  network hop in the request path. List filtering stays one query. Runs on the
  server that exists. Ships incrementally — `OwnerOnlyAuthorizer` first means the
  refactor lands with **zero behaviour change** and can be reviewed as such.
- **Against:** you write the inheritance logic yourself; it is not magic, and if
  DataMind ever grows a deep resource tree you will have rebuilt a worse
  OpenFGA. You own the model's tests.
- **Cost:** one migration, ~250 lines of new code, and the mechanical
  `owner_id`→`ctx` change across two service files.

#### Option B · OpenFGA as a deployed service · **L**

What Lakekeeper does. `docker-compose.yml` grows an `openfga` service and a
database for it; DataMind grows a client, a `.fga` model, and a tuple-writing
path parallel to every resource mutation.

- **For:** the model is expressive and declarative; nesting, usersets and the
  privilege lattice come free and correct; there is a mature UI and a playground
  for debugging; the vocabulary is portable.
- **Against:** **everything in §1.5.** A second stateful service and its
  database; a non-transactional dual write and therefore a drift-repair story you
  must build or port; grants that exist in **only one** place and must be backed
  up separately; an authorization-model migration path (`MODIFIES_TUPLES` /
  `ADDS_TUPLES`) to own; a network round trip on the hot path, mitigated by
  caches that trade freshness for latency; and **every list endpoint becomes two
  phases** — ask the authorizer for ids, then query for rows.
- **Cost on this deployment:** OpenFGA + its Postgres is the third and fourth
  stateful container in a compose file that already runs an app database and
  three demo databases. Its production guidance recommends pools of 200/100
  connections and four caches enabled. This is not a fit for the stated
  constraint, and it is not close.

#### Option C · An embedded policy engine (Cedar, or OPA/Rego in-process) · **M–L**

Lakekeeper's own second answer. Policies are files; no service; conditions on
attributes are expressible; ABAC becomes cheap.

- **For:** no extra container. Genuinely more expressive than a grants table for
  *conditional* rules ("only during business hours", "only if the connection's
  disclosure policy is `NONE`"). Policy-as-code is auditable and diffable.
- **Against:** **it cannot express runtime sharing** — Lakekeeper's own docs say
  the Grants API *rejects writes* under Cedar. "Share this dashboard with Bob"
  becomes "edit a policy file and redeploy", which is the wrong product for a
  self-service BI tool. Also, the mature Cedar and OPA bindings are Rust and Go;
  a Python integration is a second-class path.
- **Verdict:** the right answer for infrastructure, the wrong answer for a
  share button. Worth keeping in view for §8.1-style *conditions* later, layered
  on top of A rather than instead of it.

#### Option D · Postgres row-level security on the app database · **M**

Set a session variable per request; let Postgres RLS policies filter every table.

- **For:** genuinely un-bypassable within the database; no application code can
  forget a filter.
- **Against:** it fights the connection pooling that `get_sessionmaker()`
  depends on; policies live in migrations rather than in reviewable Python;
  debugging a wrong result means reading `pg_policy`; and it protects the *app*
  database, which is not where the sensitive data is — **the customer's data
  lives behind `DatabaseConnection`, and RLS here would not touch it.**
- **Verdict:** solving the wrong half of the problem. Named so that it is
  declined deliberately.

### Group II — What the unit of sharing is

#### Option E · Per-resource grants, no container · **S given A**

`(resource_type, resource_id, principal, privilege)`. Share one dashboard with one
group.

- **For:** simplest thing that works; matches the mental model of a share button;
  no new navigational concept in the UI.
- **Against:** grant count grows with resources × audiences, which is the problem
  Grafana's folders and Metabase's collections exist to solve. It arrives around
  the point where somebody has thirty dashboards.

#### Option F · A workspace (or folder) that owns resources · **M**

Resources belong to a workspace; grants attach to the workspace and inherit down;
per-resource grants remain as the exception.

- **For:** the answer all three BI peers converged on (§3.4). Grant count stays
  proportional to *audiences*, not resources. It gives connections, dashboards
  and reports a common parent, which is also where a shared connection most
  naturally lives.
- **Against:** it is a **product** change, not just an authorization one — every
  create flow needs a workspace picker, and every existing row needs a home. It
  also invites the question of whether a workspace is a tenant, which is a much
  bigger question and should be refused explicitly.
- **Note:** the schema in §7.2 keeps `resource_type='workspace'` reachable without
  building it, so E does not have to be re-done to get F.

#### Option G · Connection grants only — the minimum honest D1 · **S given A**

Grant on `database_connections` and nothing else. A shared connection means
someone else may ask questions through it and build their **own** dashboards on
it; no artifact is shared.

- **For:** it is the precondition
  [architecture.md](../architecture.md) names, and it is *strictly* the smaller
  half. It brings the semantic layer and the knowledge store with it for free
  (§5.3), and `can_curate` is **already written for it** (§5.2e). It avoids the
  dashboard-intersection problem (§5.2d) entirely, because nothing is shared that
  spans connections.
- **Against:** users will read "sharing" in a changelog and expect to share a
  dashboard.
- **Verdict:** this is the first shippable increment, and it is smaller than it
  looks.

### Group III — Who issues identity

#### Option H · Stay local; add groups as DataMind rows · **S**

Keep Argon2id + JWT. Groups are a table, membership is a table, an admin manages
both in the existing Users page.

- **For:** zero new infrastructure; testable in CI with no containers; the
  invite flow already exists.
- **Against:** no SSO, no central deprovisioning, passwords are DataMind's
  problem.

#### Option I · Write the OIDC adapter now, run no IdP · **S–M**

`OidcIdentityProvider` implementing the existing port, selected by
`auth_provider: "local" | "oidc"`, defaulting to `local`. Tested against a
locally-minted RS256 token and a static JWKS fixture — **no container, no
Keycloak, ~40 lines of test setup.**

- **For:** the integration is *finished and proven* before any IdP is chosen, so
  adopting one later is a config change and a group-mapping exercise rather than
  a project. It also forces the group-identity design (§7.6) to be right while it
  is still free to change.
- **Against:** code that nothing in production exercises, until it does.

#### Option J · Deploy Keycloak · **M, plus permanent operational cost**

- **For:** the reference implementation; every enterprise conversation knows it;
  groups, federation, MFA, and machine clients all included.
- **Against — and these are the stated constraint:** Keycloak's own sizing
  documentation puts base pod memory at **1250 MB** for a realm with 10,000
  cached sessions, recommends **at least 750 MB** as a limit for a basic
  deployment and **2 GB** for a small production one, and notes that in a
  container it targets ~70% of the limit as heap **plus roughly 300 MB
  non-heap**. That is the largest single process in the deployment, larger than
  DataMind itself. And it makes local development worse in a specific way the
  user already named: every developer must run it, every test that touches auth
  must wait for a realm to import, and a broken realm import is a broken test
  suite.

#### Option K · A lighter OIDC provider · **M**

If SSO is wanted but Keycloak's footprint is not, the same `OidcIdentityProvider`
serves any compliant issuer. *Third-party comparison, not vendor documentation:*
Zitadel is commonly cited at roughly 100 MB (single Go binary + Postgres) and
Authentik around 300 MB (multi-container), against Keycloak's ~1.25 GB; Dex is
smaller still but is a **federating connector**, not a user store, so it needs an
upstream. **Treat those numbers as directional and measure before committing** —
the point is the order of magnitude, not the figure.

- **Verdict:** the decision that Option I makes *cheap and reversible*, which is
  the whole reason to take Option I first.

### 6.4 The options side by side

| | New containers | Runtime cost | Runtime sharing? | Reversible? | Fits the stated constraint |
|---|---|---|---|---|---|
| **A** grants in Postgres | 0 | one join | yes | yes — it *is* the port | ✅ |
| **B** OpenFGA | +2 (svc + db) | RPC per check, 2-phase lists | yes | expensive to unwind | ❌ |
| **C** Cedar / OPA embedded | 0 | in-process eval | **no** | yes | ⚠️ wrong shape |
| **D** Postgres RLS | 0 | free | no | painful | ❌ wrong database |
| **E** per-resource grants | — | — | — | — | ✅ start here |
| **F** workspaces | — | — | — | — | ⏳ design for, build later |
| **G** connection-only | — | — | — | — | ✅ **first increment** |
| **H** local + groups | 0 | — | — | — | ✅ |
| **I** OIDC adapter, no IdP | 0 | — | — | — | ✅ **the enabling move** |
| **J** Keycloak | +1 (~1.25 GB) | — | — | — | ❌ *for now* |
| **K** lighter IdP | +1 (~0.1–0.3 GB) | — | — | — | ⏳ when SSO is asked for |

### 6.5 Recommendation

**A + G→E→F, with H now and I next; J or K only when somebody asks for SSO.**

In one sentence: **build Lakekeeper's model inside DataMind's own database,
behind Lakekeeper's own port pattern, and let the identity provider stay a
detail.**

The argument, compressed:

- **Against B**, the decisive fact is not cost, it is §1.5. OpenFGA's price is a
  second source of truth for data that has no backup elsewhere, and Lakekeeper —
  which needs it and is glad to have it — still had to write a reconciler with a
  documented eventual-consistency window and a "quiesce your API" escape hatch.
  DataMind's resource graph gives back none of what that buys.
- **For A**, the tell is that DataMind's four resource types and two-level tree
  make the whole model a table and two indexes, and that the port makes the
  choice reversible. If DataMind is ever wrong about this, `GrantsAuthorizer` is
  replaced by `OpenFgaAuthorizer` behind the same protocol — which is exactly the
  claim Lakekeeper's four implementations prove is achievable.
- **For G first**, because it is the item mvp2 marks **blocking**, it is the one
  the code has already been shaped for, and it sidesteps the dashboard
  intersection problem that would otherwise dominate the first release.
- **For I over J**, because the constraint the user actually stated — *"I don't
  have a lot of resources, and it makes testing my app hard"* — is answered by
  making the IdP a **configuration** rather than a **dependency**, and that is
  achieved by writing the adapter, not by deploying the server.

**And one thing that should be built before any of it:** §5.2(a)'s refactor. It
is behaviour-preserving, it is testable against the existing suite, and every
option above is materially cheaper on the other side of it.

---

## 7. A sketch of the recommended path

Sketches, not specifications — enough to argue with. Names follow the repo's
existing conventions; the layer rule (`domain/` has no I/O, `infra/` has no
domain logic, import-linter enforces both) is respected throughout.

### 7.1 Step zero — make `policy.py`'s docstring true

No new tables, no new behaviour, no new endpoint. Route every ownership decision
through one module, keep the answer identical, and let the tests prove it.

```python
# app/services/policy.py  — after step zero, before grants exist
def can(ctx: RequestContext, resource: Any, privilege: Privilege) -> bool:
    """The one place that answers 'may this actor do this to this thing'.

    Today the answer is ownership, exactly as before. The point of the function
    is that tomorrow it is not, and tomorrow is a change here.
    """
    return owns(ctx, resource)
```

The mechanical half is the service signatures. `DashboardService.get(dashboard_id,
owner_id)` becomes `get(dashboard_id, ctx)`, and the `WHERE owner_id = :owner`
clause becomes a call to the visibility helper below. **132 of those 208 lines
are in `report_service.py` and `dashboard_service.py`**, so this is a large
diff in a small blast radius, and `make test` is the proof.

Two properties make this reviewable: the diff should not change a single test
assertion, and `grep -rn "owner_id ==" backend/app/api backend/app/services`
should return **zero** hits when it is finished.

### 7.2 The schema

One migration — `0022_principals_and_grants.py`, on top of `0021`.

```sql
-- A named principal that is not a person. Flat by design (see 7.4).
CREATE TABLE groups (
    id           uuid PRIMARY KEY,
    name         varchar(100) NOT NULL,
    description  text,
    -- The external identity this group mirrors, when it mirrors one.
    -- Both columns or neither: an external identity is (provider, id) together.
    -- This is Lakekeeper's `RoleSourceSystem`, and it is why Keycloak can
    -- arrive later without a single grant being rewritten.
    provider_id  varchar(50),
    source_id    varchar(255),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_groups_name   UNIQUE (name),
    CONSTRAINT uq_groups_source UNIQUE (provider_id, source_id),
    CONSTRAINT ck_groups_source_pair
        CHECK ((provider_id IS NULL) = (source_id IS NULL))
);

-- Membership. Only meaningful for DataMind-managed groups: when a group is
-- provider-managed, membership comes from the token and this table is empty.
CREATE TABLE group_members (
    group_id  uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id   uuid NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    added_at  timestamptz NOT NULL DEFAULT now(),
    added_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (group_id, user_id)
);

-- One privilege, on one resource, to one principal.
CREATE TABLE grants (
    id            uuid PRIMARY KEY,
    resource_type varchar(30) NOT NULL,   -- 'connection' | 'dashboard' | 'report'
    resource_id   uuid        NOT NULL,
    -- Exactly one principal. A CHECK, not a convention.
    user_id       uuid REFERENCES users(id)  ON DELETE CASCADE,
    group_id      uuid REFERENCES groups(id) ON DELETE CASCADE,
    privilege     varchar(20) NOT NULL,    -- see 7.3
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

CREATE INDEX ix_grants_resource  ON grants (resource_type, resource_id);
CREATE INDEX ix_grants_user      ON grants (user_id)  WHERE user_id  IS NOT NULL;
CREATE INDEX ix_grants_group     ON grants (group_id) WHERE group_id IS NOT NULL;
```

**No `owner_id` column is removed.** Ownership stays where it is and keeps
meaning what it means — it is a *fact about the resource*, not a grant, and
Lakekeeper models it the same way (`define ownership: [user, role#assignee]`
alongside the privileges). The authorizer reads ownership **and** grants; there is
no backfill, and rolling the feature back is a config flip rather than a data
migration.

**No `grantor` column beyond `created_by` for the audit trail.** That is L5: if
grant authority is never delegated more than one hop, nothing needs to walk a
chain on revoke. Should `pass_grants` ever be added, this decision must be
revisited — and Lakekeeper's v4.10 changelog is the argument for not adding it.

### 7.3 The privilege lattice

Four privileges, and they mean what they mean in `warehouse.fga`, flattened:

| Privilege | On a connection | On a dashboard / report |
|---|---|---|
| `describe` | it exists; its name, kind and **disclosure policy**; not its schema | it exists; its name and description |
| `select` | ask questions through it; read its schema, semantic layer and knowledge store | view it, and view its results |
| `modify` | edit it, re-sync it, curate its knowledge | edit it, add and remove tiles or sections |
| `manage_grants` | grant and revoke on it | grant and revoke on it |

The lattice — `manage_grants` ⊃ `modify` ⊃ `select` ⊃ `describe` — is declared
once, in the domain layer, and expanded at check time rather than at write time
(so a lattice change never needs a backfill):

```python
# app/domain/value_objects/__init__.py
class Privilege(StrEnum):
    DESCRIBE      = "describe"
    SELECT        = "select"
    MODIFY        = "modify"
    MANAGE_GRANTS = "manage_grants"


#: For each privilege, every privilege whose holder also holds it. The SQL
#: analogue of `define select: [...] or modify` — asked as
#: `WHERE privilege = ANY(:satisfying)` rather than checked in Python at 200
#: call sites, which is the whole point of writing a lattice down once.
_SATISFIED_BY: dict[Privilege, frozenset[Privilege]] = {
    Privilege.DESCRIBE:      frozenset(Privilege),
    Privilege.SELECT:        frozenset({SELECT, MODIFY, MANAGE_GRANTS}),
    Privilege.MODIFY:        frozenset({MODIFY, MANAGE_GRANTS}),
    Privilege.MANAGE_GRANTS: frozenset({MANAGE_GRANTS}),
}
```

**Deliberately absent, each for a reason worth stating:**

- **`pass_grants`.** L5. Every grant stays one hop from an administrator or an
  owner.
- **`create`.** Lakekeeper needs it because a namespace is a container you create
  *into*. DataMind has no container until Option F, at which point it arrives with
  the container.
- **`curate` as a separate privilege.** `can_curate` already answers this as
  *administrator or owner*, and under grants it becomes *`modify` on the
  connection* — which is the same rule stated in the new vocabulary, and preserves
  the D4 property that a reader may ask but not teach.
- **A `deny` privilege.** §1.3. One `but not` clause is the most any of these
  models should have, and DataMind does not need even that until `managed_access`
  becomes a want.

### 7.4 Groups, and why they are flat

A group is a principal. Membership is a table. **Nesting is not supported in v1**,
and the reason is not laziness: Keycloak (and Entra, and Okta) emit group
membership in a token as a **flat list of paths** — `["/analytics",
"/analytics/finance"]` — so the hierarchy has already been flattened by the
issuer before DataMind sees it. Supporting nesting locally would create a second,
different hierarchy that the token's flat list would then contradict. The schema
above admits a `group_members.member_group_id` column later if that judgement
turns out wrong; the check query would become one `WITH RECURSIVE`.

**The `(provider_id, source_id)` pair is the whole Keycloak migration.** A group
created in DataMind has both NULL. A group that mirrors a Keycloak group carries
`('oidc', '/analytics')`. **Grants point at `groups.id` either way**, so binding
an existing DataMind group to a Keycloak group is one `UPDATE` of two columns and
changes no grant, no dashboard, and no test. This is Lakekeeper's
`RoleSourceSystem` — and its `can_update_source_system` action exists precisely
because *"this redirects which external group's members the provider sync flows
into the role"*, which is a security-relevant act deserving its own permission.
DataMind should treat rebinding a group as an admin-only, audited action for the
same reason.

### 7.5 The check, the list, and the port

Two operations, and **they are not the same operation** (L7).

```python
# app/domain/ports/authz.py — a Protocol, no I/O, no SQLAlchemy
@dataclass(frozen=True, slots=True)
class ResourceRef:
    type: ResourceType
    id: UUID
    owner_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """Allowed, plus why — so a denial can be audited with a reason.

    `because` mirrors Lakekeeper's `AuthorizationDecision.determined_by`: the
    grant ids or the word `owner`, never free text. `audit.record()` already
    has a DENIED outcome and nothing that produces one.
    """
    allowed: bool
    because: tuple[str, ...] = ()


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

`Visible` is the piece that pays for itself, and it is copied straight from
Lakekeeper's `ListProjectsResponse::{All, Projects(HashSet)}`:

```python
Visible = Everything | Subquery | Ids
```

- `Everything` — an administrator, or authorization switched off. The list
  endpoint adds no clause at all.
- `Subquery(select)` — what `GrantsAuthorizer` returns: a SQLAlchemy `Select` of
  ids that composes into the caller's existing query as
  `.where(Dashboard.id.in_(subquery))`. **One round trip, one plan, one index
  scan** — this is the property Option B cannot have.
- `Ids(frozenset)` — what an out-of-process authorizer would have to return.
  Present in the union so the port is honest about the shape a future
  `OpenFgaAuthorizer` would need, rather than being quietly built to exclude one.

The subquery itself is the entire model, in one statement:

```sql
SELECT resource_id FROM grants
 WHERE resource_type = :type
   AND privilege     = ANY(:satisfying)          -- the lattice, expanded
   AND (user_id = :actor OR group_id = ANY(:groups))
UNION
SELECT id FROM dashboards WHERE owner_id = :actor      -- ownership
```

`:groups` is the point of contact with §7.6: **the actor's group ids arrive on
the `RequestContext`, resolved once per request**, and where they came from is the
identity provider's business — a `group_members` join in local mode, a token
claim in OIDC mode. Nothing downstream of `get_ctx` can tell the difference.

```python
# app/core/context.py
@dataclass(frozen=True, slots=True)
class RequestContext:
    user_id: UUID
    email: str
    role: str
    #: Every group this actor belongs to, resolved once per request by the
    #: identity provider. Local: a join. OIDC: the groups claim, mapped through
    #: groups.(provider_id, source_id). Everything downstream reads only this.
    group_ids: frozenset[UUID] = frozenset()
    session_id: UUID | None = None
    correlation_id: str = ""
    actor_ip: str = ""
```

**That one field is the "add Keycloak later" design.** Everything else in this
document is detail around it.

### 7.6 How Keycloak arrives later — exactly

A checklist, in the order the work would happen, with what is already in the tree
marked.

1. **✅ The port exists.** `IdentityProvider` in
   [`domain/ports/identity.py`](../../backend/app/domain/ports/identity.py), five
   methods, one implementation.
2. **✅ The column exists.** `users.external_subject`, since migration `0001`,
   unread. It becomes `"{provider_id}~{subject}"` — Lakekeeper's convention,
   adopted (§1.1) because a bare `sub` collides the day a second issuer appears.
3. **Add the config switch.** `auth_provider: Literal["local", "oidc"] = "local"`
   in `core/config.py`, alongside `oidc_issuer`, `oidc_audience`,
   `oidc_subject_claim` (default `"sub"`), `oidc_groups_claim`. `get_identity_provider`
   in [`deps.py:34`](../../backend/app/api/deps.py#L34) picks the implementation.
   **Default stays `local`, and CI never changes.**
4. **Write `OidcIdentityProvider`.** `verify_access_token` becomes: fetch and
   cache JWKS from `{issuer}/.well-known/openid-configuration`, verify signature
   / `iss` / `aud` / `exp` locally (Lakekeeper's posture — **no introspection call
   per request**, and therefore no IdP on the hot path), derive
   `external_subject`, upsert the user, map the groups claim, and return an
   `AuthenticatedIdentity`. `authenticate` and `rotate_session` raise
   `NotImplementedError` — **the IdP owns the login flow and the refresh, and
   DataMind stops setting `raymand_refresh` entirely** (§8.7).
5. **Map groups, don't sync them.** For each value in the groups claim, look up
   `groups WHERE provider_id = :p AND source_id = :v`. Unknown values are
   **ignored, not auto-created** — an IdP that renames a group must not silently
   create an empty ungranted one, and an admin binding a group deliberately is
   the audited act (§7.4). Membership is never written to `group_members` for a
   provider-managed group: the token is the source of truth, which is exactly
   Lakekeeper/Cedar's `project_roles` model and removes the entire class of sync-
   job bugs.
6. **Migrate the humans.** Local accounts already carry `email`. First OIDC login
   matches on lowercased email, sets `external_subject`, and clears
   `password_hash`. **Every `owner_id` and every grant still points at the same
   `users.id`**, so nothing is re-granted and nothing is re-shared. This is the
   step Lakekeeper cannot offer (§1.7) because it never had local accounts; DataMind
   has them, and that is an advantage worth protecting by never letting a grant
   reference an external subject directly.
7. **Decide the mixed-mode question before step 6, not during it** (§8.6).

**Testing it without Keycloak — the constraint, answered directly.** The adapter
is testable with no container: generate an RS256 keypair in a fixture, serve the
public half as a static JWKS dict, mint tokens with `pyjwt` (already a
dependency), and assert on expiry, wrong audience, wrong issuer, unknown `kid`,
and group mapping. That is roughly forty lines of `conftest.py` and it runs in
milliseconds. **A real Keycloak belongs in a manual `docker-compose.oidc.yml`
that CI never starts** — the same treatment `docker-compose.replicas.yml` already
gets for the cross-replica work.

### 7.7 Order of work

| # | Step | Size | Why here |
|---|---|:--:|---|
| 1 | **Step zero** — one `can()`, services take `ctx`, zero behaviour change | **M** | everything after is cheaper; reviewable as a no-op |
| 2 | `Authorizer` port + `OwnerOnlyAuthorizer`, wired, still no grants | **S** | the seam is real and load-bearing before it is used |
| 3 | Groups + `group_members` + `ctx.group_ids` + admin UI | **S–M** | no grants yet; groups alone are useful for the next step |
| 4 | **Grants on connections only** (D1, Option G) + `GrantsAuthorizer` | **M** | the blocking item; brings semantic layer and knowledge with it |
| 5 | Audit every grant, revoke, and **denial** — with `Decision.because` | **S** | D4's second half; the log already has `DENIED` and nothing writes it |
| 6 | Grants on reports (the single-connection case) | **S** | proves the model on an easy resource |
| 7 | Grants on dashboards + the multi-connection intersection rule | **M** | §5.2(d); needs §8.2 answered first |
| 8 | `OidcIdentityProvider` + JWKS fixture tests, `auth_provider` default `local` | **M** | independent of 1–7; can run in parallel |
| 9 | Workspaces (Option F) | **L** | only once grant count is a real complaint |
| — | *Deferred:* RLS (D3), `managed_access`, `pass_grants`, per-viewer cache | — | §8, with triggers |

Steps 1–2 are the ones that are worth doing **even if Theme D is never built**,
because they replace a false docstring with a true one.

---

## 8. Decisions to make before building

Nine. The four marked ⚠️ change the design rather than the schedule, and three of
those are not permission questions at all — they are the questions a permission
model exposes and cannot answer.

### 8.1 ⚠️ Whose credentials does a shared object execute under? — the load-bearing one

[architecture.md](../architecture.md) states the problem exactly: *"User B would
read data pulled with user A's credentials, against a connection B does not
own."* Three answers, and they are genuinely different products:

- **The connection's grant** (mvp2's own proposal, and the recommendation here).
  A tile executes if the **viewer** holds `select` on the tile's connection —
  re-checked at every execution, not at share time. The stored credential is used,
  but only for someone the connection has been opened to. This is the same
  posture `execute_saved_sql` already takes with the schema snapshot: **re-validate
  at execution, trust nothing recorded earlier.**
- **The owner's session** (Superset's `DASHBOARD_RBAC`). Sharing the artifact
  implicitly grants the data. Simple, and it is the leak §1.5 warns about.
- **The viewer's own connection.** Requires every viewer to hold a credential,
  which is the constraint the README's own positioning rejects — *"those people do
  not each own a database credential."*

**This decision must be made before the schema is written**, because it decides
whether `grants` needs a row for the connection *and* the dashboard (answer one)
or only the dashboard (answer two).

### 8.2 ⚠️ Does `dashboard_tile_cache` grow a viewer in its key?

§5.2(c). Under answer one of §8.1 it does **not**, and that is correct and cheap.
The trigger for revisiting is precise and should be written into the model's
docstring: **the first time a tile's result depends on who is looking**, whether
that is row-level security (D3), a per-user parameter (C4), or a per-user
disclosure policy. Until then the current key is right, and *saying why it is
right* is what stops someone adding a viewer-dependent filter without noticing.

### 8.3 Does an administrator get read on everything?

The documented answer and the enforced answer currently disagree (§0):
`policy.can_read` says yes, and no call site asks it, so the enforced answer is
no. Both are defensible — Lakekeeper's server `admin` deliberately **cannot** act
inside a project without first assigning themselves `project_admin`, *"an action
[that] is visible in the audit log"*, which is a third and better answer than
either.

The recommendation is to make the enforced behaviour the documented one and add
Lakekeeper's escalation path: an administrator may **grant themselves** access to
any resource, and the grant is an audited row. That preserves recoverability — the
reason `_guard_last_admin` exists — without a silent backdoor, and it means "the
admin read this dashboard" is a fact in `audit_logs` rather than an absence.

### 8.4 ⚠️ Granting `select` on a connection is a disclosure decision, not only an access one

Each connection declares `disclosure_policy` — how much of a result may reach the
model provider ([security.md §3](../security.md)). Today the person who chose that
policy is the only person who can trigger a query under it. **The moment a
connection is shared, the owner's disclosure choice governs somebody else's
questions**, and that person may not know what it is.

Three sub-decisions: does `describe` expose the policy (recommendation: **yes** —
§7.3 puts it there deliberately, because a grantee should be able to see what
leaves before they ask); may a grantee with `modify` *change* it (recommendation:
**no** — split it out as an owner/`manage_grants`-only field, since widening it
from `NONE` to `FULL` is not an edit, it is a disclosure decision); and does the
audit row for an ask record the policy in force (recommendation: **yes**, and D4
already wants it).

### 8.5 Per-resource grants now, or a workspace now?

Option E versus F. E is smaller and F is where every peer ended up. The
recommendation is E with F's shape reserved — but the decision is genuinely
open, and the cost of being wrong is asymmetric: **E→F is a migration of existing
grants into a container; F→E is not a thing anyone does.** If a workspace is
likely within a year, the honest move may be to pay for it once.

### 8.6 May local accounts and OIDC accounts coexist?

The mixed-mode question, and it must be answered **before** the migration in
§7.6(6), not during it. Options: (a) `auth_provider` is exclusive, and switching
converts every account — simple, and it locks out anyone the IdP does not know;
(b) both are accepted, and a user with `external_subject` set may no longer use a
password — supports gradual migration, and is a second login path to secure; (c)
both, permanently, with local accounts reserved for break-glass — most operable,
most surface.

Lakekeeper sidesteps this by never having local accounts. **DataMind cannot
sidestep it, and (b) is the recommendation** — with the break-glass case handled
by the bootstrap admin remaining local, which is also the answer to "the IdP is
down and nobody can sign in."

### 8.7 What happens to the refresh cookie under OIDC?

`raymand_refresh` is DataMind's own rotating refresh token with reuse detection,
scoped to `/api/v1/auth`
([`auth.py:14`](../../backend/app/api/v1/auth.py#L14)). Under OIDC the IdP issues
refresh tokens and DataMind should issue none — otherwise there are two session
lifetimes, two revocation paths, and a signed-out user who is still signed in.
**Recommendation: under `auth_provider="oidc"`, `/auth/login`, `/auth/refresh`
and the cookie all disappear**, and `sessions` stops being written. That is a
visible API change and belongs in the decision, not in the implementation.

Related, and worth deciding at the same time: **a disabled user's live session.**
`LocalIdentityProvider.rotate_session` refuses a `DISABLED` user, so the block
takes effect within one access-token lifetime (≤15 minutes) rather than
immediately. Under OIDC the equivalent window is the IdP's, and it is usually
longer. If "revoked means revoked now" is required, that is a per-request user
lookup — a real cost, deliberately not paid today.

### 8.8 Is a denial audited, and does it leak existence?

Today's pattern returns **404 for a resource you do not own**, which leaks
nothing. Grants make the distinction meaningful: someone with `describe` but not
`select` should get **403**, because they can already see the thing exists.
Getting this backwards — 403 where 404 is correct — converts every list endpoint
into an existence oracle.

The rule that falls out of the lattice: **404 unless the caller holds at least
`describe`; 403 above that.** And `audit.record(outcome=DENIED, …)` should fire
for the 403 case with `Decision.because` attached — that is what
`AuthorizationDecision.determined_by` is for (§1.4), and it is the difference
between a log that says *"no"* and one that says *"no, because the only grant
reaching you is `describe` on the parent."*

### 8.9 What happens to grants when a principal goes away?

`ON DELETE CASCADE` on `grants.user_id` and `grants.group_id` handles deletion.
It does **not** handle the two harder cases: a user set to `DISABLED` still holds
grants (correct — disabling is reversible, and dropping grants would make
re-enabling a re-grant); and **deleting a user cascades away every resource they
own**, because `dashboards.owner_id` is already `ON DELETE CASCADE`
([`models.py:524`](../../backend/app/infra/db/models.py#L524)).

That second one is a live bug the moment sharing exists: **today, deleting a user
destroys only their private work; after sharing, it destroys work other people
depend on.** The fix is ownership transfer before deletion — Lakekeeper models
it as an explicit `can_change_ownership` action gated on `manage_grants` — and it
should be built in the same release as the first grant, not after the first
support ticket.

---

## 9. Open questions this research could not close

1. **How does Lakekeeper handle OpenFGA being unreachable mid-request?** The
   error type `OpenFGABackendUnavailable` is threaded through every check and a
   batch response with a missing item is a hard error, so the posture is clearly
   fail-closed per request. Whether there is a degraded read-only mode, and what
   the health endpoint reports, was not established from the source read here.
2. **The real cost of the OpenFGA hop at DataMind's scale.** All published
   latency figures found are Auth0's managed environment (P99 <50 ms) or
   third-party micro-benchmarks. Nothing measures a single-node OpenFGA beside a
   Python app on one host, which is the only number that would matter. The
   argument in §6 does not rest on it — it rests on §1.5 — but it is the number
   that would settle any residual doubt.
3. **Whether the `role#assignee` userset has a SQL cost equivalent.** For flat
   groups, no — it is `group_id = ANY(:groups)`. For nested groups it is a
   `WITH RECURSIVE`, and no one measured that against a realistic group graph.
   §7.4 argues nesting is not needed, but the argument is about token shape, not
   about cost.
4. **What Lakekeeper's UI actually exposes for permission management**, and how
   much of the OpenFGA vocabulary a non-expert has to learn to use it. This is the
   usability half of "which model should DataMind copy", and it was not
   assessable from documentation alone. The `GET /{resource}/{id}/actions`
   introspection endpoints suggest the UI asks the server *"what may I do here"*
   and renders affordances from the answer — a pattern worth copying regardless —
   but that is inference from the trait, not observation.
5. **Whether a workspace and a tenant can be kept apart.** Every product in §3
   eventually grew a second, coarser boundary (Metabase's tenancy, Grafana's
   organisations, Lakekeeper's projects). Whether DataMind can ship Option F
   without that becoming a multi-tenancy project is not something desk research
   can answer.
6. **The disclosure interaction with a *group* grant.** §8.4 assumes a grantee is
   a person who can be told what the policy is. A grant to a group of forty is a
   disclosure decision made on behalf of forty people, and none of the peers in §3
   appear to model consent to it at all.

---

## 10. Sources

**Lakekeeper — documentation**

- [Authentication](https://docs.lakekeeper.io/docs/nightly/authentication/) — OIDC config, subject-claim derivation, machine vs human flows, Kubernetes `TokenReview`, multi-provider prefixes.
- [Authorization](https://docs.lakekeeper.io/docs/nightly/authorization/) — backends, privileges/grants/principals vocabulary, provider-managed roles.
- [Authorization: OpenFGA](https://docs.lakekeeper.io/docs/nightly/authorization-openfga/) — hierarchy, bidirectional inheritance, `reconcile` modes, OpenFGA version and tuning requirements.
- [Authorization: Cedar](https://docs.lakekeeper.io/docs/nightly/authorization-cedar/) — policy sources, entity model, ABAC via table properties, "the Grants API rejects writes".
- [Configuration](https://docs.lakekeeper.io/docs/nightly/configuration/) — `LAKEKEEPER__AUTHZ_BACKEND` default `allowall`, `INSTANCE_ADMINS`, every env var quoted above.
- [Bootstrap](https://docs.lakekeeper.io/docs/nightly/bootstrap/) — bootstrap-once, `is-operator`, per-backend admin establishment.
- [Open Policy Agent bridge](https://docs.lakekeeper.io/docs/latest/opa/) — the Trino translation layer, referenced but not examined here.

**Lakekeeper — source, read directly**

- [`authz/openfga/README.md`](https://github.com/lakekeeper/lakekeeper/blob/main/authz/openfga/README.md) — the model changelog; `MODIFIES_TUPLES`/`ADDS_TUPLES`; the v4.10 revoke-authority and delegation-depth argument quoted in §1.2.
- [`authz/openfga/v4.10/components/*.fga`](https://github.com/lakekeeper/lakekeeper/tree/main/authz/openfga) — `server`, `project`, `role`, `warehouse`, `namespace`, `lakekeeper_table`; every relation quoted in §1.2 is verbatim from these files.
- `crates/lakekeeper/src/service/authz/mod.rs` — the `Authorizer` trait, typed action enums, batch-check contract, `RoleSourceSystem`.
- `crates/lakekeeper/src/service/authz/decision.rs` — `AuthorizationDecision` / `DeterminingFactor`.
- `crates/lakekeeper/src/service/authz/implementations/allow_all.rs` — the no-op authorizer that is still 18 KB.
- `crates/authz-openfga/src/authorizer.rs` — `ListObjects` for project listing, `batch_check` chunking and correlation.
- `crates/authz-openfga/src/reconcile.rs` — deletion semantics, the eventual-consistency note, the ~80k tuples/sec figure, "quiesce API writes externally".

**The BI peers** *(product documentation; tier-dependent features noted in §3)*

- [Metabase — permissions overview](https://www.metabase.com/docs/latest/permissions/start), [data permissions](https://www.metabase.com/docs/latest/permissions/data), [collection permissions](https://www.metabase.com/docs/latest/permissions/collections), [row and column security](https://www.metabase.com/docs/latest/permissions/row-and-column-security).
- [Apache Superset — security configuration](https://superset.apache.org/admin-docs/security/); `DASHBOARD_RBAC` behaviour per that documentation and the project's own discussion threads.
- [Grafana — roles and permissions](https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/), [folder access control](https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/folder-access-control/).

**Background and sizing**

- [OpenFGA — running in production](https://openfga.dev/docs/best-practices/running-in-production) — server pooling, database co-location, cache trade-offs.
- [Keycloak — concepts for sizing CPU and memory](https://www.keycloak.org/high-availability/multi-cluster/concepts-memory-and-cpu-sizing) — the 1250 MB / 750 MB / 2 GB figures and the container heap split. **Vendor documentation.**
- Zitadel ≈100 MB and Authentik ≈300 MB are **third-party comparison estimates**, not vendor figures; treat as directional and measure before committing.
- Zanzibar (Pang et al., USENIX ATC 2019) is the origin of the tuple model that OpenFGA, SpiceDB and Permify all re-implement.

**DataMind — everything asserted about this codebase**

Read from `main` on 2026-09-02:
[`services/policy.py`](../../backend/app/services/policy.py) ·
[`api/deps.py`](../../backend/app/api/deps.py) ·
[`api/v1/auth.py`](../../backend/app/api/v1/auth.py) ·
[`api/v1/users.py`](../../backend/app/api/v1/users.py) ·
[`infra/identity/local.py`](../../backend/app/infra/identity/local.py) ·
[`domain/ports/identity.py`](../../backend/app/domain/ports/identity.py) ·
[`core/context.py`](../../backend/app/core/context.py) ·
[`infra/db/models.py`](../../backend/app/infra/db/models.py) ·
[`services/dashboard_service.py`](../../backend/app/services/dashboard_service.py) ·
[`services/query_service.py`](../../backend/app/services/query_service.py) ·
[`services/audit.py`](../../backend/app/services/audit.py) ·
[`tests/unit/test_audit_and_permissions.py`](../../backend/tests/unit/test_audit_and_permissions.py).
Counts (106 endpoints, 11 routers, 208 lines mentioning `owner_id`, the per-file
distribution, the `policy.py` call graph) are `grep` results against that tree
and will drift.
