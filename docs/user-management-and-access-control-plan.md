# User management and access control — implementation plan

> **What this is.** The implementation-ready plan for turning DataMind from a
> single-player product into one with users, **service users**, roles, teams,
> and per-resource permissions — with the authorization decision computed in
> **one** place and every UI affordance rendered from that decision rather than
> from a role guess.
>
> **Its three inputs, as required:**
> 1. The requirements brief (users, service users, roles, teams, fine-grained
>    permissions, resource-level access, UI, backend authorization, data model,
>    OIDC readiness).
> 2. The existing repository documentation and research — chiefly
>    [research/access-control.md](research/access-control.md) (Lakekeeper's
>    `Authorizer` trait and `.fga` model read against Metabase, Superset and
>    Grafana) and [access-control-plan.md](access-control-plan.md), plus
>    [architecture.md](architecture.md), [security.md](security.md),
>    [frontend.md](frontend.md), [CODEBASE.md](CODEBASE.md) and
>    [mvp2-plan.md](mvp2-plan.md) Theme D.
> 3. **New external research** performed for this plan: Power BI / Fabric
>    workspace roles, Apache Superset's Flask-AppBuilder RBAC and its
>    `DASHBOARD_RBAC` flag, Metabase's two-axis permissions and its API-key
>    model, Grafana service accounts and RBAC, Looker's permission-set × model-set
>    roles, Tableau's Allow/Deny/Unspecified capabilities, machine-identity
>    practice, and OWASP API1:2023. Sources are cited in [§28](#28-sources).
>
> **Relationship to [access-control-plan.md](access-control-plan.md).** That
> document is the direct ancestor of this one and **most of it survives
> unchanged** — the port, the lattice idea, the `Visible` subquery, the
> intersection rule, the 404/403 rule, the audit posture, the OIDC seams. This
> plan is a **superset**: it adds the four things the requirements need and that
> plan deferred or refused — **service users**, **named roles carrying
> permissions**, **teams as permission carriers**, and **grantable LLM /
> Knowledge / Chat resources** — and it reverses four of that plan's decisions
> on the record in [§0.4](#04-the-eighteen-decisions-decided). Where the two
> disagree, **this document wins**, and §0.4 says why in each case.
>
> **Status:** plan, not implementation. Nothing in this document is built. The
> investigation in Part 1 is done and its findings are marked `[x]` in the
> master checklist; everything else is `[ ]`.
>
> **Written:** 2026-09-06, against `main` at `cedea10`.

---

## Table of contents

- [Part 0 — The shape of it](#part-0--the-shape-of-it)
- [Part 1 — What exists today](#part-1--what-exists-today-investigation-findings)
- [Part 2 — External research](#part-2--external-research)
- [Part 3 — The design](#part-3--the-design)
- [Part 4 — The eleven phases](#part-4--the-eleven-phases)
- [Part 5 — Extensibility, risks, open questions](#part-5--extensibility-risks-and-open-questions)
- [Part 6 — Master implementation checklist](#part-6--master-implementation-checklist)

---

# Part 0 — The shape of it

## 0.1 The one-sentence goal

**One function answers *"may this principal do this to this resource"*, every
call site asks it, the answer is the union of ownership plus role-carried
privileges plus team-carried grants plus direct grants — computed from
DataMind's own Postgres — and swapping the identity provider, the principal
type, or the decision store is a change behind a port rather than a change at
213 call sites.**

## 0.2 What is built, what is a seam, what is not

| | |
|---|---|
| **Built** | `PrincipalKind` (human / service) · `service_credentials` + API-key authentication · `roles` carrying **capabilities** and **wildcard-scoped privileges** · `teams` + membership · `role_assignments` to users **and** teams · `grants` on eight resource types · a five-rung privilege lattice · the `Authorizer` port and two implementations · declarative route guards · `GET /me/permissions` and per-resource `GET …/actions` · an `/admin` section with People / Service accounts / Teams / Roles / Access review / Audit · resource-level Access & Share surfaces · audit rows for every authorization event including denials · the 404-vs-403 rule · ownership transfer |
| **Built as a seam, not a feature** | provider-namespaced external identities · `teams.(provider_id, source_id)` **and** `roles.(provider_id, source_id)` · `ctx.team_ids` / `ctx.capabilities` resolved once per request · `auth_provider` config switch with one implementation · `authz_backend` config switch with two · `RequestContext.on_behalf_of` for background work · a `scopes` column on `service_credentials`, unread |
| **Not built, with a written trigger** | OIDC adapter · row-level security · workspaces / folders · nested teams · delegated granting (`pass_grants`) · per-viewer tile cache · time-boxed grants (`expires_at`) · a second tenant boundary |
| **Not built, and not wanted** | an external authorization service · a second source of truth for permissions · `deny` rules or priority ordering · anonymous / public share links · UI-only restrictions treated as security |

**The constraint that shapes everything, restated from the research:** no
Keycloak, no OpenFGA, no second container, no second store. DataMind's resource
graph is **two levels deep with eight resource types**. Lakekeeper's *model* is
worth copying; its *service* is not. What is copied instead is the **port** —
the thing that makes the choice reversible — and that is
[research/access-control.md](research/access-control.md) L2.

## 0.3 The eleven phases at a glance

Each phase is sized to be finished, reviewed and merged on its own. "Sessions"
is the honest estimate for one context window of focused work ending at a green
gate.

| # | Phase | Size | Sessions | Ships user-visible behaviour? | Depends on |
|:--:|---|:--:|:--:|:--:|---|
| **0** | Vocabulary, the port, the config switches | S | 1 | no — pure addition | — |
| **1** | `ctx` everywhere, part A: dashboards & reports | M | 2 | no — behaviour-preserving | 0 |
| **2** | `ctx` everywhere, part B: the rest, workers, the grep gate | M | 2 | no — behaviour-preserving | 1 |
| **3** | **Roles and capabilities** — the table, the eight seeds, `is_admin` retires | L | 3 | yes — roles are real | 2 |
| **4** | **Teams** — membership, role assignment to teams, `ctx.team_ids` | M | 2 | yes — teams are real | 3 |
| **5** | **Service users** — kind, API keys, the second authenticator | M | 2–3 | yes — machines get identities | 3 |
| **6** | **Grants on connections, knowledge, semantic** ⚠️ the blocking item | L | 3 | yes — **sharing begins** | 4, 5 |
| **7** | The audit half — grant, revoke, denial, escalation, disclosure | S–M | 1–2 | yes | 6 |
| **8** | Grants on reports, dashboards, LLM configs, conversations | L | 3 | yes — **read-only sharing** | 6, 7 |
| **9** | Access review, "why can't I see this", the permission explainer | M | 2 | yes | 8 |
| **10** | The rulebook, conformance tests, seams, docs | M | 2 | no — makes the rules enforceable | all |

**Phases 0–2 are worth doing even if the rest is cancelled**, because they
replace a false docstring with a true one:
[`services/policy.py`](../backend/app/services/policy.py) opens with *"Row-level
or column-level security later is a change in this module only"*, and that
sentence is not true of the code today — `can_read`, `can_write` and
`can_administer_users` have **zero call sites** while **213 lines** across
`api/`, `services/` and `workers/` compare `owner_id` by hand.

**Phases 3–5 are independently useful without any sharing at all.** Roles, teams
and service accounts are administration objects that pay for themselves the day
they exist; grants can land a month later.

## 0.4 The eighteen decisions, decided

⚠️ marks a decision that would change the schema if reversed. **↺ marks a
decision that reverses or corrects [access-control-plan.md](access-control-plan.md).**

| # | Question | **Decision** | Consequence |
|:--:|---|---|---|
| 1 ⚠️ ↺ | Are service users a separate identity space? | **No — one principal space.** A service user is a row in `users` with `kind='SERVICE'`, no password, no interactive login, and its own credentials table. | Every existing FK — `owner_id`, `audit_logs.actor_user_id`, `grants.user_id` — keeps working unchanged. A separate table would have made all of them polymorphic. The plan's deferral of "service-account principals" is closed. |
| 2 ⚠️ ↺ | Does `Role` stay a two-valued enum? | **No.** `roles` becomes a table carrying **capabilities** and **wildcard-scoped privileges**; eight are seeded as system roles; custom roles are creatable. `users.role` survives one release as a read-only cache and is dropped in Phase 10. | This is requirement 2. It is also Looker's split — a role is a *permission set* (capabilities) plus a *model set* (which resources), and DataMind's "model set" is the grant table. |
| 3 ⚠️ | May a role carry resource privileges, not only capabilities? | **Yes, at wildcard scope only.** A role may say *"`manage` on **all** `knowledge`"*; it may never name one resource id. Naming one resource is what `grants` is for. | This is the single mechanism that makes **Knowledge Manager** expressible without admin. Mixing per-resource rows into roles is what makes *"why can Ali see this?"* unanswerable — Superset's failure mode. |
| 4 ↺ | `groups` or `teams`? | **`teams`.** One table, one word, used in the product and in the schema. `groups` was the research's word; the requirement's word is teams, and an OIDC group maps onto a team through `(provider_id, source_id)`. | A rename of the plan's `groups` table before it exists, which costs nothing now and a migration later. |
| 5 | May a service user join a team? | **Yes.** | Metabase's API-key model — *"the key will have the same permissions granted to that group"* — is the pattern to copy. Grafana deliberately forbids it, because its teams also route notifications; DataMind has no such second meaning, so the simpler model wins. |
| 6 ⚠️ ↺ | Is `llm_config` grantable? | **Yes, for `select` (use) only.** `modify` on an LLM config is **key-equivalent** — a holder can repoint `base_url` at a host they control and harvest the key on the next call — so `modify`/`manage` stay with the owner and an explicit administrator escalation, and both are audited. | Requirement 4 asks for *"a user can use only specific LLMs"*. The plan's *"never share `llm_configs`"* was right about the key and wrong about the use; splitting the rung resolves both. **The `base_url` finding is new and is the reason the split is safe.** |
| 7 ⚠️ ↺ | Is Knowledge its own resource type? | **Yes.** `ResourceType.KNOWLEDGE`, whose `resource_id` **is the connection's id** — a derived resource sharing the parent's id space, needing no new table. | The plan folded curation into `modify` on the connection. That makes a Knowledge Manager also a credential editor, which requirement 2 explicitly forbids. Splitting them is one enum member and one privilege-table row. |
| 8 ⚠️ | Is the semantic layer its own resource type? | **Yes**, by the same derived-resource trick. | A Data Engineer curates meaning without holding the credential. |
| 9 ⚠️ | Is `delete` its own privilege? | **Yes** — a rung between `modify` and `manage`. | Requirement 4 lists Delete beside Edit. *"May edit, may not destroy"* is a real ask; *"may delete, may not edit"* is not, so the lattice stays **linear** and monotone. |
| 10 | Where did **Create** go? | **Create is a capability, never a resource privilege.** You cannot hold a privilege on an instance that does not exist. | `connection.create`, `dashboard.create`, `report.create`, … live on roles. This one sentence prevents a whole category of confused modelling. |
| 11 | How do permissions combine across roles, teams and direct grants? | **Union. Most-permissive wins. No `deny`, no priority order, no ordering-dependent evaluation.** | Metabase (*"the most permissive access granted to them across all of their groups"*) and Power BI (*"they get the highest level of permission"*) both do this. Tableau's Deny-wins model is the counter-example: it is documented as requiring a user-rules-then-group-rules precedence walk, and it is why Tableau permissions are a specialism. §15 states the algorithm formally. |
| 12 ⚠️ | Is there a wildcard scope on a grant? | **Yes — `grants.resource_id IS NULL` means every resource of that type.** Writable only by a role definition or by an explicit, audited administrator action. | Without it, "Knowledge Manager over everything" is either a hard-coded `if` (the thing this plan exists to remove) or N rows that go stale on every new connection. |
| 13 | Does sharing an artifact share the data behind it? | **No.** A tile renders only if the viewer holds `select` on **that tile's** connection, re-checked at execution. | Unchanged from the plan (invariant I2), and now with the external evidence: Superset's `DASHBOARD_RBAC` documents the opposite behaviour as a **bypass** of dataset checks. |
| 14 | Does an administrator implicitly read everything? | **No. An administrator may _grant themselves_ access; the grant is a row and the escalation is an audit row.** | Unchanged from the plan. |
| 15 | Are capabilities and team membership carried in the JWT? | **No — resolved from the database once per request.** The access token carries the subject and nothing about authorization. | Costs one indexed query per request; buys **immediate** effect for a role change or a team removal, instead of up to one access-token lifetime. Partially closes the plan's open question 3. |
| 16 ↺ | Migration numbering | **0024–0029.** `0022_llm_params_and_embeddings` and `0023_token_accounting` are taken. | The plan's `0022_groups` / `0023_grants` numbers are stale; using them would collide. |
| 17 ↺ | What is the gate? | **`make lint && make test`, plus `npm run typecheck && npm run build && npm test` in `frontend/`.** | **There is no `make check` target**; the plan's gates name one that does not exist. A new `make authz-check` target carries the greps this plan adds. |
| 18 | Where does administration live in the UI? | **One `/admin` section, master–detail, six tabs.** `/users` becomes a redirect to `/admin/people`. Resource-level access lives **beside the resource**. | The rail is deliberately seven flat rows and its ordering carries the grouping ([frontend.md §1](frontend.md)); four more rows would break that. §21 is the full argument. |

---

# Part 1 — What exists today (investigation findings)

*Everything in this part was read out of the tree on 2026-09-06. Line counts and
call-site counts are from `main` at `cedea10`.*

## 1. The backend, in numbers

| | |
|---|---|
| API surface | **109 route decorators** across 11 routers in [`backend/app/api/v1/`](../backend/app/api/v1/) |
| Principals | **users only.** No groups, no teams, no service accounts |
| Roles | **2** — `Role.ADMIN`, `Role.MEMBER` ([`domain/value_objects/__init__.py`](../backend/app/domain/value_objects/__init__.py)) |
| User states | `ACTIVE`, `INVITED`, `DISABLED` |
| Resource-level grants | **none** |
| Authorization module | [`services/policy.py`](../backend/app/services/policy.py), **67 lines**, of which four functions have **zero callers** |
| `owner_id` references in `api/` + `services/` + `workers/` | **213 lines** |
| Latest migration | `0023_token_accounting` |
| Audit | [`services/audit.py`](../backend/app/services/audit.py) writes 9 curation actions; `SUCCESS`/`DENIED`/`FAILED` exist and **nothing produces a `DENIED`** |

### 1.1 The five things the code says

**(a) `policy.py` is a seam in name only.** Its docstring promises *"Row-level or
column-level security later is a change in this module only."* The call graph:

```
   can_curate(ctx, settings, resource)   ←  knowledge.py, four call sites
   owns(ctx, resource)                   ←  policy.py itself, and nowhere else
   can_read(ctx, resource)               ←  NOTHING
   can_write(ctx, resource)              ←  NOTHING
   can_administer_users(ctx)             ←  NOTHING
```

Everything else authorizes **by construction**: services take `owner_id` as a
parameter and put it in a `WHERE` clause, so the answer to an unauthorized
request is not *denied*, it is *not found*. That leaks nothing — it is a
defensible pattern — but it is not a policy layer, and no amount of editing
`policy.py` changes it. **Making that docstring true is step zero**, and it is
behaviour-preserving.

**(b) The services take `owner_id`, not `ctx`.** `DashboardService.get(id,
owner_id)`, `.update(id, owner_id, **changes)`, `_owned_connection(connection_id,
owner_id)`. Identity arrives as a bare UUID with no room for *"…or a team they
belong to, with at least `select`"*. The distribution is favourable:

| File | `owner_id` lines |
|---|---:|
| `services/report_service.py` | 76 |
| `services/dashboard_service.py` | 57 |
| `services/run_service.py` | 20 |
| `services/sql_draft_service.py` | 12 |
| `api/v1/conversations.py` | 9 |
| `services/semantic_service.py` | 8 |
| `services/query_service.py` | 7 |
| `api/v1/connections.py`, `api/v1/llm_configs.py` | 4 each |
| `services/policy.py` | 3 |
| `api/v1/knowledge.py`, `api/v1/drafts.py`, `api/v1/semantic.py`, `workers/benchmark.py`, `workers/report.py` | 2 each |
| `services/knowledge_service.py`, `workers/knowledge_maintenance.py`, `workers/report_graph.py` | 1 each |
| **Total** | **213** |

**133 of 213 are in two files.** This is a large mechanical change in a small
number of places, not a diffuse one.

**(c) `dashboard_tile_cache` has no viewer in its key**
([`models.py:717`](../backend/app/infra/db/models.py#L717)), and freshness is a
SHA-256 over `connection_id`, `sql`, `max_rows` and `chart_config`. Correct under
grant-based sharing, because execution happens under the **connection's** grant
and everyone who may see the tile sees the same rows. It becomes a silent
cross-user leak the day a tile's rows depend on the viewer. That is the trigger
sentence, and Phase 8 writes a test that fails if the key changes without it.

**(d) A dashboard's shareability is an intersection, not a property.** Tiles
carry their **own** `connection_id`
([`models.py:642`](../backend/app/infra/db/models.py#L642)) so one dashboard may
span several connections. `Report.connection_id` is single and immutable after
creation ([`models.py:746`](../backend/app/infra/db/models.py#L746)), and a
conversation is pinned to one connection the same way. **Reports are the easy
case and dashboards the hard one** — which is the opposite of the intuitive
build order.

**(e) `can_curate` already anticipates sharing and says so.** It is the one
function in the codebase that has thought about a shared connection, and its
rule — *administrator, or the owner of the thing being curated*, with the
fail-closed reading of "I don't know who owns this" being **no** — is the
template every other permission function should follow. Seven tests in
[`test_audit_and_permissions.py`](../backend/tests/unit/test_audit_and_permissions.py)
pin it.

### 1.2 What is already right and must be preserved

| | Item | Where |
|:--:|---|---|
| ✅ | Argon2id + HS256 access tokens + rotating refresh with reuse detection | [`infra/identity/local.py`](../backend/app/infra/identity/local.py) |
| ✅ | `IdentityProvider` Protocol, five methods — **the authentication seam already exists** | [`domain/ports/identity.py`](../backend/app/domain/ports/identity.py) |
| ✅ | `users.external_subject` column, present since `0001` and **never read or written** | [`models.py:61`](../backend/app/infra/db/models.py#L61) |
| ✅ | `AdminDep` and `_guard_last_admin` — the precedent for refusing a destructive action with an explanation | [`deps.py`](../backend/app/api/deps.py), [`users.py:132`](../backend/app/api/v1/users.py#L132) |
| ✅ | `audit.record()` joining the caller's transaction, never failing the action, carrying identifiers and counts and never content | [`services/audit.py`](../backend/app/services/audit.py) |
| ✅ | `GET /audit`, administrators only | [`api/v1/audit.py`](../backend/app/api/v1/audit.py) |
| ✅ | Import-linter contracts keeping `app.domain` free of `sqlalchemy` and `fastapi` | `backend/pyproject.toml` |
| ✅ | `PUT /auth/me/password` and `PATCH /auth/me` taking **no user id**, with a test that walks the route table to keep it that way | [`api/v1/auth.py`](../backend/app/api/v1/auth.py) |
| ✅ | `test_openapi_has_no_secrets.py` — no read model exposes a password or `api_key` | `backend/tests/unit/` |

### 1.3 The resource surfaces, as they are scoped today

| Resource | Table | Scoped by | List endpoint filters on |
|---|---|---|---|
| Database connection | `database_connections` | `owner_id` | `owner_id == ctx.user_id` |
| LLM config | `llm_configs` | `owner_id` | `owner_id == ctx.user_id` |
| Dashboard | `dashboards` | `owner_id` | `owner_id` via service |
| Report | `reports` | `owner_id` | `owner_id` via service |
| Conversation / run | `conversations`, `runs` | `owner_id` | `owner_id` in the router |
| Semantic layer | `semantic_layers` | via `connection_id` | follows the connection |
| Knowledge templates | `knowledge_templates` | via `connection_id` | follows the connection, gated by `can_curate` |
| Benchmarks | `benchmark_sets` | via `connection_id` | follows the connection |
| Audit log | `audit_logs` | admin-only reader | — |

Two facts worth carrying into the design. **The semantic layer, the knowledge
store and the benchmarks already follow the connection**, so connection grants
alone bring a large amount of team behaviour at no extra modelling cost — the
strongest argument for sequencing connections first. And **`llm_configs` is
per-owner**, so a second user on a fresh install today sees *no* models and can
ask nothing; the requirement's *"a user can use only specific LLMs"* is not a
new feature so much as the fix for a first-run cliff.

## 2. The frontend, as it stands

Read from [`frontend/src/`](../frontend/src/) and
[docs/frontend.md](frontend.md).

**The shell** ([`App.tsx`](../frontend/src/App.tsx)) renders a **seven-row flat
rail**, in a deliberate order: the four surfaces you *work* in (Chat,
Dashboards, Reports, Knowledge), then the three you *keep* (Data sources, LLM
providers, Users). `frontend.md` §1 states the rule that governs any addition:
*"The list stays flat and uncaptioned; the ordering is what carries the
grouping, so it must not zig-zag across that boundary."* The rail is followed by
a user block (→ `/settings`, the account page) and a colophon (→ `/about`).

**The route table** is `/chat/*`, `/dashboards/*`, `/reports/*`, `/sources/*`,
`/knowledge/*`, `/providers/*`, `/users` (admin-only), `/settings`, `/about`.

**Three page shapes exist and are shared deliberately:**

| Shape | Pages | Frame |
|---|---|---|
| **Index** | Dashboards, Reports, **Users** | page header · toolbar (search / filter-when-useful / sort) · skeleton · empty states |
| **Master–detail** | Data sources, LLM providers, Knowledge | `components/settings.tsx` — `MasterColumn`, `MasterItem`, `DetailHeader`, `DetailBody`, `Section`, `FieldRow`, `Tabs`, `StatusLine`, `UnsavedNote` |
| **Workspace** | Chat, one dashboard, one report | their own furniture |

**Data sources already has the tab strip this plan extends**:
`Connection · Policy · Schema · Semantic layer · Knowledge →`, where the last is
a *door* that navigates to `/knowledge/:id` rather than a second copy of the
console. The tab is in the URL (`/sources/:id/:tab`) precisely so *"where do I
set the disclosure policy?"* can be answered with a link.

**`UsersPage.tsx` (1,091 lines)** is an index page with an inline detail, a
one-time-password panel shown exactly once, and a confirm on every destructive
act. It is a good page and it becomes the **People** tab of the new `/admin`
section with its furniture intact.

**Two frontend facts that matter for this plan:**

- **The rail's admin gate is a client-side role string.** `NAV` carries
  `adminOnly: true` on the Users row and the route is wrapped in a role check.
  That is correct as an *affordance* and must never be the security boundary —
  §18 makes this a rule with a conformance test behind it.
- **`/auth/me` returns `{id, email, display_name, role}`** and nothing else. The
  frontend has no vocabulary for a capability, a team, or a privilege. Phase 3
  gives it one.

## 3. What the existing documentation already decided

| Document | What it decided that this plan keeps | What this plan changes |
|---|---|---|
| [research/access-control.md](research/access-control.md) | L1 split authn from authz and ship authz first · L2 the port before the second implementation · L3 the lattice declared once in data · L5 revocation is administration · L6 a denial carries a reason into the audit log · L7 list-filtering is a different operation from row-checking · L8 two stores means a reconciler | L4 said groups should nest; this plan keeps them **flat** with a written trigger, for the reason the sibling plan gives — IdPs emit flat paths |
| [access-control-plan.md](access-control-plan.md) | The `Authorizer` port · `Decision` / `Visible` · the `Subquery` composition · ownership as a fact not a grant · the intersection rule (I2) · the 404/403 rule · `on_behalf_of` for background work · the OIDC recipe · no `deny` | Decisions 1, 2, 6, 7 of §0.4 above (service users, roles-as-a-table, LLM configs, knowledge as its own type), plus the migration numbers and the gate command |
| [architecture.md §9, §18](architecture.md) | JWT + refresh, RBAC named as the model, secrets encrypted with row-bound AAD | §18 still describes ownership as the enforcement mechanism; Phase 2 corrects it |
| [security.md §3, §6](security.md) | The disclosure policy governs three render paths · credentials bound by AAD · no read model exposes a secret · `X-Real-IP` only · audit holds identifiers and counts, never content | §6 gains a subsection on **service credentials**; §3 gains the sharing interaction — one person's disclosure choice now governs another person's questions |
| [frontend.md §1, §2](frontend.md) | The flat seven-row rail and why · the index / master–detail / workspace shapes · "when two screens must agree about a verdict, they share the component" | §2's table gains `/admin`; the Users row's meaning widens |
| [mvp2-plan.md](mvp2-plan.md) Theme D | D1 is blocking and nothing is shared before it · a shared object executes under the **connection's** grant, re-checked at every execution · D3 (RLS) is out of scope with a trigger · D4 the audit log | D2's *"read-only share"* widens to the five-rung lattice |
| [CLAUDE.md](../CLAUDE.md) | The dependency rule · the four non-negotiable invariants · "a new API route: router in `api/v1/`, DTO in `schemas.py`, logic in `services/*`" | Gains a fifth invariant (§16) and a pointer to the rulebook |

---

# Part 2 — External research

*Performed for this plan on 2026-09-06. The research note
[research/access-control.md](research/access-control.md) already covers
Lakekeeper in depth and summarises Metabase, Superset and Grafana; this part
does not repeat it. It goes further on the four things the requirements need and
that note did not cover: **named roles**, **service accounts**, **permission
inheritance across multiple memberships**, and **where sharing lives in the
UI**. Each claim below is from the product's own documentation unless marked
otherwise; sources are in [§28](#28-sources).*

## 4. Power BI / Microsoft Fabric — the workspace as the unit, and the role as a bundle

**The model.** A *workspace* is the container. Four roles attach to it — **Admin,
Member, Contributor, Viewer** — and each is a **fixed bundle of capabilities**, not
a composable set. Roles are assigned to individuals *or* to security groups,
Microsoft 365 groups and distribution lists.

**The capability matrix, in the parts that matter to DataMind:**

| Capability | Admin | Member | Contributor | Viewer |
|---|:--:|:--:|:--:|:--:|
| Update / delete the workspace | ✅ | | | |
| Add or remove any user in a workspace role | ✅ | | | |
| Add members **or others with lower permissions** | ✅ | ✅ | | |
| Allow others to reshare items | ✅ | ✅ | | |
| Create, edit, delete content | ✅ | ✅ | ✅ | |
| View and interact with an item | ✅ | ✅ | ✅ | ✅ |
| Manage subscriptions created by others | ✅ | | | |

**Five findings, and four of them land in this plan.**

1. **Multiple memberships resolve to the *highest* permission.** *"If someone is
   in several user groups, they get the highest level of permission that's
   provided by the roles that they're assigned."* Union, most-permissive. This is
   decision 11.
2. **Delegation is bounded by rank, not by a separate privilege.** A Member may
   add users *with lower permissions* but may not change an existing user's
   role — to promote a Viewer, an Admin must remove them first. That is a
   deliberately awkward rule and DataMind should **not** copy it (§9's *avoid*
   column); the useful half is the *principle*: **granting is delegable,
   re-grading and revoking are administration.** Lakekeeper reached the same
   place from the other direction in v4.10.
3. **"May see the report" and "may query the model" are separate grants** —
   *Build* permission on a semantic model is its own thing. But Power BI then
   **auto-grants Build to Contributor and above** through the workspace role.
   DataMind takes the separation and **refuses the auto-grant**: that shortcut is
   the same shape as Superset's `DASHBOARD_RBAC` bypass.
4. **Disabling an identity does not remove its access records.** *"This behavior
   is by design to prevent accidental data loss and to allow access to be
   restored."* Exactly DataMind's decision that `DISABLED` keeps grants and
   deletion requires ownership transfer first.
5. **Service principals hold workspace roles and inherit the same permissions as
   users** for API operations. A machine identity is a principal, not a special
   code path. This is decision 1.

## 5. Apache Superset — the cautionary tale, read precisely

**The model.** Flask-AppBuilder RBAC. Permissions are fine-grained strings
(`can_add`, `can_edit`, `can_show`, `can_list`, `all_datasource_access`, …)
bundled into roles; a user's effective permission is the **union** of their
roles'. Five built-ins — **Admin, Alpha, Gamma, sql_lab, Public** — are
re-synchronised on `superset init`, and the documentation's own advice is that
*"it's not recommended to alter the roles described here"*: compose custom roles
beside them instead. Gamma is deliberately incomplete — *"they can only consume
data coming from data sources they have been given access to through another
complementary role"* — so a real Gamma user always holds **two** roles: the
capability role and a data-access role.

**Three findings.**

1. **The two-role composition is the right idea with the wrong ergonomics.**
   Superset gets the *separation* right — capabilities in one role, data reach in
   another — but expresses both as opaque permission strings in the same bag, so
   nobody can look at a user and say what they can reach. DataMind takes the
   separation and gives each half its own **typed** home: capabilities on the
   role, resource reach in `grants`.
2. **`DASHBOARD_RBAC` is the leak to design against, and it is documented as
   such.** With the flag on, granting a role access to a dashboard *"will bypass
   dataset level checks and implicitly grant read access to all the featured
   charts in the dashboard, and thereby also all the associated datasets."* The
   community has filed issues that it does not match its own documentation, that
   native filters break because the implicit grant does not cover their datasets,
   and that a `DRAFT` dashboard with no role assigned is reachable by anyone.
   **This is precisely the failure DataMind's intersection rule (I2) exists to
   prevent**, and it is worth knowing that the leak is not a bug — it is the
   consequence of merging the two axes.
3. **Superset's row-level security is worth copying the shape of, later.** RLS
   filters attach to **roles** and are applied as predicates in the generated
   SQL, so they hold for a caller who reaches the data another way; a user in
   several roles gets their filters combined. Enforcement in the query, not in the
   UI — the posture DataMind's SQL guard already takes. This is the shape D3 should
   take when it lands.

## 6. Metabase — the two axes, the "Blocked" nuance, and the API-key model

**The model.** Two orthogonal axes, plus a third for administration:

- **Data permissions** — per database / schema / table, split since v50 into
  **View data** (`Can view` · `Row and column security` · `Impersonated` ·
  `Blocked`) and **Create queries** (`Query builder and native` · `Query builder
  only` · `No`).
- **Collection permissions** — which saved questions, dashboards, models and
  metrics a group may see and curate.
- **Application permissions** — settings, monitoring, subscriptions.

**Permissions attach to *groups only*.** There is no per-user grant anywhere in
Metabase. `All Users` is the default group everyone is in, and the documentation
warns to restrict it before granting anything to specialised groups.

**Four findings.**

1. **Splitting *view* from *create queries* is the right granularity for a
   conversational BI product**, and DataMind's equivalent split is already
   implicit: `describe` (it exists, and what its disclosure policy is) versus
   `select` (ask questions through it). This plan makes that split explicit and
   adds `modify`, `delete`, `manage` above it.
2. **"Blocked" is not a counter-example to no-`deny`.** Metabase's `Blocked` looks
   like a deny rule and is not one: *"If a person in a Blocked group belongs to
   another group that has its View data permission set to 'Can view,' that more
   permissive access will take precedence."* It exists because in Metabase a
   **collection** grant alone can otherwise reach data. **DataMind has no such
   path** — the intersection rule means artifact access never implies connection
   access — so DataMind needs no `Blocked`, and decision 11 stands.
3. **The most-permissive rule is stated as product doctrine**, not as an
   implementation detail: *"If a person is in multiple groups, they will have the
   most permissive access granted to them across all of their groups."*
4. **The API-key model is the one to copy for service users.** A key is created
   in admin settings, **assigned to a group**, and *"the key will have the same
   permissions granted to that group"*. It is **shown exactly once**, named,
   regenerable and deletable, and it does **not** appear as a user account. Two
   details DataMind should take and one it should improve on:
   - take: **permissions come from ordinary group membership**, so there is no
     second permission system for machines;
   - take: **shown once, named, revocable**;
   - improve: Metabase's fallback when a key's group is deleted is to *reassign
     the key to `All Users`* — a silent, quiet widening of a machine identity's
     reach. DataMind refuses the delete instead (`ON DELETE RESTRICT` semantics
     for a team that holds role assignments, §17.4).

## 7. Grafana, Looker and Tableau — three more angles

### 7.1 Grafana — service accounts as a first-class principal, and the folder

- **Principals are users, teams, or service accounts, chosen from one dropdown.**
  A service account holds organisation roles (`Viewer`/`Editor`/`Admin`) and, in
  Enterprise, granular RBAC; **tokens inherit the service account's permissions**,
  and multiple tokens per account exist so that *"multiple applications use the
  same permissions"* while keeping separate audit trails. Tokens can be given an
  expiry.
- **`GET /api/access-control/user/permissions`** exists specifically so a caller
  can find out what a token can actually do. DataMind copies this as
  `GET /me/permissions`.
- **A service account cannot join a team**, and *"assigning team management
  permissions to a service account does not make the account part of the team."*
  DataMind deliberately diverges (decision 5) because Grafana's teams carry a
  second meaning — notification routing — that DataMind's do not.
- **RBAC is (action, scope) pairs**, where a scope may be a wildcard
  (`folders:*`) or one object (`folders:uid:xyz`). **This is the direct ancestor
  of decision 12**: a role's wildcard privilege and a grant's specific privilege
  are the same shape at two scopes.
- **The folder keeps the grant count small** — inheritance *"always flows
  downward"*. DataMind has no container, and §24 writes the trigger that would
  introduce one.

### 7.2 Looker — a role is a permission set **times** a model set

*"A role is made up of two items: a permission set and a model set. The model set
defines the models that a user has access to, and the permission set defines what
the user can do with that model."* Content access (folders) is managed
**separately** from feature access, and both compose.

This is the cleanest statement of the design this plan adopts:

```
   DataMind role  =  capabilities            ×  wildcard-scoped privileges
                     (Looker: permission set)   (Looker: model set)
   DataMind grant =  the same privileges, at the scope of one resource
                     (Looker: content/folder access)
```

The lesson taken is the **naming discipline**: keeping "what you may do" and
"what you may do it to" as two named things is what makes an access review
answerable. The lesson *not* taken is Looker's requirement that a user hold
exactly one role — DataMind allows several and unions them, which every other
product in this survey also does.

### 7.3 Tableau — the counter-example, and it is a specific one

Tableau's capabilities are ternary: **Allow / Deny / Unspecified**, with
`Unspecified` resolving to denied. Effective permission requires a documented
precedence walk: user rules first (deny beats allow), then group rules (a deny in
**any** group beats an allow in another), and permissions can be *locked* at the
project level so only administrators and project leaders may set rules.

**DataMind takes exactly one thing from this and refuses the rest.** The thing it
takes is *locking*: a governance surface where the person who owns a sensitive
resource can stop others re-sharing it — which DataMind already expresses more
simply as *revocation is administration* and *`manage` is not implied by
`modify`*. What it refuses is `deny` and precedence, for the reason the model
demonstrates: an effective permission that requires reading rules in priority
order is a permission nobody can predict, and predicting it is the entire job of
an access review.

## 8. Service accounts and machine identity — the practice outside BI

Synthesised from Google Cloud IAM guidance, current machine-identity practice,
and the two BI implementations above.

| Practice | Why | What DataMind does |
|---|---|---|
| A service identity is **a principal, not a shared human account** | audit attribution; revoking one integration must not lock out a person | `users.kind = 'SERVICE'`, its own row, its own grants |
| **Least privilege by construction** — one identity per integration | a shared "automation" account accretes every permission anyone ever needed | The create form asks for the integration's name and shows the effective permission before saving |
| **Secrets are shown once, stored hashed** | a store that can echo a key is a store that can leak every key | `token_hash`, shown once, `prefix` kept for identification |
| **A searchable, non-secret prefix** | a key found in a log or a repo must be traceable to its owner **without** the secret | `dm_sk_<prefix>_<secret>`; `prefix` is indexed and unique |
| **Fast verification, not a slow KDF** | Argon2id on every API request is 50–100 ms of CPU per call; the secret is 256 bits of entropy, not a human password, so stretching buys nothing | SHA-256 over the secret half, constant-time compare. **This is a deliberate divergence from the password path and §17.2 states the reason inline** |
| **Expiry, rotation with overlap, revocation** | a key with no expiry is a key that outlives the integration | `expires_at` (default 365 days, configurable), several live keys per identity so rotation has an overlap window, `revoked_at` |
| **`last_used_at`** | the only way to answer *"is this key still needed?"* | a column, written at most once per minute per key |
| **No interactive login, no password, no self-service** | a machine that can sign in through the browser is a human account with extra steps | `CHECK (kind <> 'SERVICE' OR password_hash IS NULL)` |
| **A machine may not mint administrators** | the blast radius of a leaked key must not include the permission system | A service user may not hold `user.manage`, `role.manage` or `service_user.manage` unless `settings.allow_privileged_service_users` is on; **off by default**, and turning it on is audited |

**OWASP API1:2023 (Broken Object Level Authorization)** is the standard that
names DataMind's current risk directly: *"every API endpoint that receives an ID
of an object, and performs any action on the object, should implement
object-level authorization checks"*, with the recommended mitigation being a
**centralised** mechanism that verifies the caller's relationship to the target
before the handler runs. That is requirement 7, and it is what §18's declarative
route guards are for.

## 9. What DataMind adopts, adapts and avoids

| Pattern | Source | Verdict | Why, for **this** product |
|---|---|:--:|---|
| Two orthogonal axes: artifact access vs data access | Metabase, Looker, Power BI | **Adopt** | It is the only way read-only sharing is safe in a product whose README leads with *"you decide what leaves your database."* |
| Most-permissive union across memberships | Metabase, Power BI, Superset | **Adopt** | Predictable, explainable in one sentence, computable as one `EXISTS` |
| Roles as named bundles assigned to principals **and** to groups | Power BI, Superset, Looker | **Adopt** | Requirement 2 and 3 in one mechanism |
| (action, scope) permissions where scope may be a wildcard | Grafana RBAC | **Adopt** | Decision 12; makes Knowledge Manager expressible without an `if` |
| Service account as a principal that gets permissions the ordinary way | Metabase API keys, Grafana, Power BI service principals | **Adopt** | Requirement 1, with no second permission system |
| `GET …/permissions` and per-resource `…/actions` | Grafana | **Adopt** | Requirement 6's *"the UI should clearly communicate what a user can and cannot access"*, sourced from the server |
| Key shown once, hashed, prefixed, expiring, revocable | Metabase, GCP, general practice | **Adopt** | §8 |
| Disabling keeps access records | Power BI | **Adopt** | Re-enabling must not be a re-grant |
| RLS filters attached to roles, applied as SQL predicates | Superset | **Adapt, later** | The right shape for D3. Needs the tile-cache key to grow a viewer first |
| A container that permissions inherit through (folder / collection / workspace) | Grafana, Metabase, Power BI | **Adapt, later** | DataMind's graph is two levels with eight types; per-resource grants are correct until grant *count* is a real complaint. §24 has the trigger |
| Groups as the *only* principal | Metabase | **Adapt** | Teams are the recommended default in the UI (a per-user grant shows a hint), but per-user grants stay possible — a two-person install should not have to invent a team |
| Delegated granting bounded by rank | Power BI Member | **Avoid** | *"Remove them, then re-add them at the new level"* is a rule people work around. DataMind's version: `manage` may grant and revoke; there is no half-privilege |
| Dashboard access implicitly granting dataset access | Superset `DASHBOARD_RBAC` | **Avoid** | It is the leak this product exists to not have. Invariant I2 |
| Auto-granting Build to Contributor and above | Power BI | **Avoid** | Same shape as the above, arrived at more politely |
| `deny` and precedence ordering | Tableau | **Avoid** | An effective permission nobody can predict makes an access review impossible |
| Re-synchronising built-in roles on boot, overwriting edits | Superset `superset init` | **Avoid** | System roles are seeded once and marked `is_system`; a later upgrade **adds** capabilities to a system role only through an explicit, audited migration |
| Reassigning an orphaned API key to "All Users" | Metabase | **Avoid** | A machine identity must never widen silently. Refuse the delete instead |
| Nested groups resolved by the application | Lakekeeper, Tableau | **Avoid, for now** | IdPs emit flat paths; a local nesting would be a second, contradicting hierarchy. §24 has the trigger |

---

# Part 3 — The design

## 10. The model on one page

Everything in this plan is one of **seven** things. A feature that cannot be
expressed in these seven needs a design change, not a workaround — and §26's
rulebook makes that checkable.

```
  PRINCIPAL ──────────────────────────────────────────────────────────────┐
   ├── User (kind=HUMAN)     a person; signs in with a password or an IdP  │
   ├── User (kind=SERVICE)   a machine or agent; signs in with an API key  │
   └── Team                  a named set of principals                     │
                                                                           │
  ROLE ─── a named bundle, assigned to a User or a Team                    │
   ├── CAPABILITIES              app-wide verbs with no instance           │
   │                             (user.manage, dashboard.create, audit.read)
   └── SCOPED PRIVILEGES         a privilege over ALL resources of a type  │
                                 (manage on ALL knowledge)                 │
                                                                           │
  GRANT ─── one PRIVILEGE, on one RESOURCE (or all of a type), to one ─────┘
            PRINCIPAL. Additive. Never subtractive.

  RESOURCE ── connection · knowledge · semantic_layer · llm_config
              dashboard · report · conversation · team

  PRIVILEGE ── describe ⊂ select ⊂ modify ⊂ delete ⊂ manage

  OWNERSHIP ── a column on the resource, not a grant. Confers the full lattice.

  DECISION ── allowed, plus *why*, so a denial can be audited with a reason.
```

**Reading it as the two axes the research converged on:**

| | *"What may I do?"* | *"What may I do it to?"* |
|---|---|---|
| Carried by | **capabilities** on a role | **grants** (per resource) and **scoped privileges** (per role, all resources of a type) |
| Metabase's word | application permissions | data + collection permissions |
| Looker's word | permission set | model set + folder access |
| Answers | *may Ali create a dashboard at all?* | *may Ali open **this** dashboard, and ask **that** database?* |

## 11. Principals

### 11.1 One identifier space, two kinds of user

**A service user is a row in `users` with `kind='SERVICE'`.** It has an id, a
display name, a description, a status, roles, teams, grants, and it can own
resources. It has **no password**, **no external subject**, **no interactive
login**, and **no self-service endpoints** — `PATCH /auth/me` and
`PUT /auth/me/password` are unreachable for it because it never holds a session
token.

Why one table rather than two:

- **Every existing foreign key keeps working.** `dashboards.owner_id`,
  `runs.owner_id`, `audit_logs.actor_user_id`, and every grant this plan adds
  point at `users.id`. A separate `service_users` table makes all of them
  polymorphic — two nullable columns and a `CHECK` on **eleven** tables — to buy
  a distinction one `varchar(20)` already draws.
- **An agent that builds a dashboard should own it**, and ownership is
  `owner_id`. A separate table makes that impossible without the polymorphism
  above.
- **The audit log stays one join.** *"Who did this?"* must answer for humans and
  machines with the same query.

Three constraints keep the kinds from blurring, and they are database
constraints, not conventions:

```sql
CHECK (kind <> 'SERVICE' OR password_hash IS NULL)
CHECK (kind <> 'SERVICE' OR external_subject IS NULL)
CHECK (kind <> 'SERVICE' OR must_change_password = false)
```

A fourth rule is enforced in the service layer because it is a policy, not an
invariant: **a service user may not hold `user.manage`, `role.manage`,
`service_user.manage` or `settings.manage`** unless
`settings.allow_privileged_service_users` is `true`. It is `false` by default and
turning it on writes an audit row. The reason is blast radius: a leaked key must
not be able to mint an administrator.

### 11.2 Teams

A team is a named set of principals. Both kinds of user may be members
(decision 5).

- **Teams are flat.** Not laziness: Keycloak, Entra and Okta all emit membership
  in a token as a **flat list of paths** (`["/analytics", "/analytics/finance"]`),
  so the hierarchy is already flattened by the issuer before DataMind sees it. A
  local nesting would be a second, contradicting hierarchy. §24 has the trigger,
  and the change is one `WITH RECURSIVE`.
- **A team may mirror an external group, or not.** `teams.(provider_id,
  source_id)` is `(NULL, NULL)` for a DataMind-managed team and `('oidc',
  '/analytics')` for a mirrored one. Binding an existing team to an external
  group is **two column updates and zero permission changes**. This is
  Lakekeeper's `RoleSourceSystem`, and it is the highest-value pair of columns in
  the schema.
- **A team carries two kinds of permission**: role assignments (capabilities and
  wildcard privileges) and grants (specific resources). Requirement 3's example —
  *"a BI Engineer team could have access to the relevant BI resources, and
  members of that team would inherit those permissions"* — is the first through
  role assignment and the second through grants, and both flow to members by the
  same union.
- **A team may itself be a resource** (`ResourceType.TEAM`). `manage` on one team
  means *manage this team's membership*, which is how a team lead exists without
  `team.manage` over every team.

### 11.3 The principal in a request

`RequestContext` grows four fields and two constructors, and loses nothing:

```python
@dataclass(frozen=True, slots=True)
class RequestContext:
    """Passed to every service and repository call. Scoping is not optional."""

    user_id: UUID                       # the principal id; the name is kept
    kind: PrincipalKind                 # HUMAN | SERVICE
    email: str
    #: Every capability this principal holds, from every role reaching them —
    #: directly or through a team. Resolved once per request from the database,
    #: never from the token (decision 15), so a revoked role takes effect on the
    #: next request rather than on the next token.
    capabilities: frozenset[Capability]
    #: Every team this principal belongs to. One query, cached for the request.
    team_ids: frozenset[UUID]
    session_id: UUID | None = None
    correlation_id: str = ""
    actor_ip: str = ""
    #: True when this context was built by `on_behalf_of` rather than from a
    #: verified credential — the audit log records it and nothing else reads it.
    delegated: bool = False

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities
```

`is_admin` **survives as a deprecated property** through Phases 3–9, defined as
`Capability.USER_MANAGE in self.capabilities`, and is deleted in Phase 10 once
the grep gate proves nothing reads it.

**Three constructors, and no fourth:**

```python
RequestContext.for_user(identity, capabilities, team_ids)      # a verified session
RequestContext.for_service(identity, capabilities, team_ids)   # a verified API key
RequestContext.on_behalf_of(user_id)                           # background work
```

`on_behalf_of` is how workers act. A scheduled report run executes **as the
report's owner** — the same privileges, the same denials, the same audit rows
with `delegated=True`. If the owner loses `select` on the connection, the
scheduled run **fails**, and that is correct. **There is no god context**: a
worker that needs to act without a principal is a worker doing something the
model does not cover, and that is a design conversation, not a `ctx=None`.

## 12. Roles and capabilities

### 12.1 What a capability is

A **capability** is an app-wide verb with **no resource instance**. If a check
needs an id, it is a privilege, not a capability.

| Group | Capability | Held by (seed roles) |
|---|---|---|
| **People** | `user.read` | Administrator, Auditor |
| | `user.manage` | Administrator |
| | `service_user.manage` | Administrator, DataMind Maintainer |
| | `team.read` | Administrator, Auditor, BI Engineer, Data Engineer |
| | `team.manage` | Administrator |
| | `role.read` | Administrator, Auditor |
| | `role.manage` | Administrator |
| **Oversight** | `audit.read` | Administrator, Auditor |
| | `access.review` | Administrator, Auditor |
| **Creation** | `connection.create` | Administrator, Data Engineer |
| | `llm_config.create` | Administrator, DataMind Maintainer |
| | `dashboard.create` | Administrator, Normal User, BI Engineer |
| | `report.create` | Administrator, Normal User, BI Engineer |
| | `conversation.create` | Administrator, Normal User, BI Engineer, Data Engineer, Knowledge Manager |
| **System** | `settings.manage` | Administrator, DataMind Maintainer |
| | `benchmark.manage` | Administrator, Knowledge Manager, DataMind Maintainer |
| | `eval.run` | Administrator, DataMind Maintainer |
| | `system.maintenance` | Administrator, DataMind Maintainer |

Eighteen capabilities. The list is **closed in code** (a `StrEnum`) and open in
the database (a `varchar`), in that order: a role row naming an unknown
capability is ignored with a warning rather than crashing the app, because a
downgrade must not lock everyone out.

**`conversation.create` is the "may I use Chat at all" capability** requirement 4
asks for. It gates opening a thread; *which* connections may be asked is
`select` on each connection, and *which* models may answer is `select` on each
`llm_config`. Three separate questions, three separate answers, none of them a
role string.

### 12.2 What a scoped privilege is

A role may also carry `(resource_type, privilege)` pairs, **always at wildcard
scope**. `KNOWLEDGE_MANAGER` holds `(knowledge, manage)`, which means *manage on
every knowledge store in the installation, now and in the future*.

**A role may never name a specific resource id.** That is what `grants` is for,
and keeping the two apart is what makes an access review answerable: *"Ali can
reach this because of the Knowledge Manager role"* and *"Ali can reach this
because of a grant made by Sara on 3 March"* are different sentences and must
stay different rows.

### 12.3 The eight seed roles

Seeded once by migration `0026`, marked `is_system = true`, **not**
re-synchronised on boot (Superset's mistake). A system role's name and
description are editable; its capability set is changed only by a migration, and
custom roles can be created freely.

| Role | Capabilities | Scoped privileges | The one-sentence purpose |
|---|---|---|---|
| **Administrator** | *all eighteen* | — (**no implicit resource read**, decision 14) | Manages people, teams, roles and the system. Sees no data they have not been granted or granted themselves. |
| **Normal User** | `dashboard.create`, `report.create`, `conversation.create`, `team.read` | — | Asks questions, builds their own dashboards and reports, sees exactly what they own or have been given. |
| **Viewer** | `team.read` | — | Consumes. Creates nothing. Every surface they see is a grant. |
| **Data Engineer** | `connection.create`, `conversation.create`, `team.read` | `(semantic_layer, manage)`, `(connection, describe)` | Owns how DataMind understands the schema. Can see that every connection exists and can curate meaning on all of them — **and still needs `select` to read any data**. |
| **BI Engineer** | `dashboard.create`, `report.create`, `conversation.create`, `team.read` | `(dashboard, describe)`, `(report, describe)` | Builds the artifacts. Their **data** reach comes from the team they are in, not from the role — requirement 3's example, exactly. |
| **Knowledge Manager** | `conversation.create`, `benchmark.manage`, `team.read` | `(knowledge, manage)`, `(connection, describe)` | Owns what the system has been taught, across every connection, **without** being able to edit a credential, change a disclosure policy, or read data they were not granted. |
| **DataMind Maintainer** | `llm_config.create`, `service_user.manage`, `settings.manage`, `eval.run`, `system.maintenance`, `benchmark.manage` | `(llm_config, describe)` | Keeps the installation running. Deliberately **not** `user.manage`: administering *people* and administering *the system* are different jobs and should be separable. |
| **Auditor** | `user.read`, `team.read`, `role.read`, `audit.read`, `access.review` | `(connection, describe)`, `(dashboard, describe)`, `(report, describe)` | Can answer *"who can reach what, and who did what"* for every resource, and can read **none** of the data in any of them. |

**Why Knowledge Manager is the load-bearing example.** It is requirement 2's own
test case — *"extensive permissions over Knowledge resources without having Admin
access to the entire application"* — and in this model it costs **two rows**:
one capability row and one scoped-privilege row. In the previous plan, where
curation was `modify` on the connection, the same role would have been a
credential editor. That is the whole argument for decision 7.

### 12.4 What roles deliberately are not

- **Not a hierarchy.** No role contains another. Administrator holds every
  capability by enumeration, so a new capability is a deliberate addition to a
  named list rather than something Administrator silently acquires.
- **Not mutually exclusive.** A principal may hold several; the union applies.
- **Not a source of resource-specific access.** §12.2.
- **Not carried in the token.** Decision 15.
- **Not deletable while assigned.** `role_assignments.role_id` is
  `ON DELETE RESTRICT`; deleting a role names the principals still holding it.

## 13. Resources and the privilege lattice

### 13.1 Eight resource types, and why the enum is closed

```python
class ResourceType(StrEnum):
    CONNECTION     = "connection"      # the pipe — a grant here is a disclosure decision
    KNOWLEDGE      = "knowledge"       # derived: resource_id IS the connection id
    SEMANTIC_LAYER = "semantic_layer"  # derived: resource_id IS the connection id
    LLM_CONFIG     = "llm_config"      # `select` = may answer with it; `modify` = key-equivalent
    DASHBOARD      = "dashboard"       # an artifact; may span several connections
    REPORT         = "report"          # an artifact; bound to exactly one connection
    CONVERSATION   = "conversation"    # personal by default; grantable, rarely granted
    TEAM           = "team"            # `manage` = manage this team's membership
```

**Derived resources** — `KNOWLEDGE` and `SEMANTIC_LAYER` — carry the **connection's
id** as their `resource_id`. They are separate *types* because they have separate
audiences (a Knowledge Manager is not a credential editor) and separate
*privileges*; they are not separate *tables* because a knowledge store has no
identity apart from the connection it describes. Adding a derived resource later
costs one enum member and one row in the privilege table.

**Leaves are not grantable.** A tile, a section, a block, a run, a message, a
template has no grant row; it inherits from its parent. Fewer rows, and no way to
create an orphaned permission.

The graph, unchanged from what the code already is:

```
   connection ──┬── dashboard ── tile      (tile carries its OWN connection_id)
                ├── report ── section ── block
                ├── conversation ── run ── message / artifact
                ├── semantic_layer         [derived resource, same id]
                ├── knowledge              [derived resource, same id]
                │     └── templates · reviews · benchmarks
                └── schema snapshot        (follows the connection, no grant)

   llm_config    (independent of any connection)
   team          (a principal, and also a resource)
```

### 13.2 The lattice

Five privileges, **linear and monotone**, declared **once** in the domain layer
and expanded **at check time** — so changing the lattice never needs a backfill.

```
   manage  ⊃  delete  ⊃  modify  ⊃  select  ⊃  describe
```

```python
# app/domain/value_objects/authz.py
#: For each privilege, every privilege whose holder also holds it. The SQL
#: analogue of Lakekeeper's `define select: [...] or modify` — asked once as
#: `WHERE privilege = ANY(:satisfying)` rather than remembered at 213 call
#: sites, which is the entire reason to write a lattice down.
_SATISFIED_BY: dict[Privilege, frozenset[Privilege]] = {
    Privilege.DESCRIBE: frozenset(Privilege),
    Privilege.SELECT:   frozenset({SELECT, MODIFY, DELETE, MANAGE}),
    Privilege.MODIFY:   frozenset({MODIFY, DELETE, MANAGE}),
    Privilege.DELETE:   frozenset({DELETE, MANAGE}),
    Privilege.MANAGE:   frozenset({MANAGE}),
}

def satisfying(p: Privilege) -> frozenset[Privilege]: return _SATISFIED_BY[p]
```

**Mapping to the requirement's verbs**, so nothing is lost in translation:

| Requirement verb | DataMind | Note |
|---|---|---|
| View | `select` | `describe` sits *below* it: the resource exists and has a name, without its contents |
| **Create** | **a capability**, not a privilege | Decision 10 — you cannot hold a privilege on an instance that does not exist |
| Edit | `modify` | |
| Delete | `delete` | Above `modify`: *may edit, may not destroy* is a real ask; the converse is not |
| Manage | `manage` | Grant, revoke, transfer ownership, change the disclosure policy |

### 13.3 What each privilege means, per resource type

**This table is the specification.** Every route in §22 resolves to exactly one
cell of it.

| | `describe` | `select` | `modify` | `delete` | `manage` |
|---|---|---|---|---|---|
| **connection** | it exists; name, engine, and **disclosure policy**; *not* its schema | ask questions through it; read its schema snapshot and its semantic layer | edit host/credentials; re-sync the schema; test it | delete the connection *(cascades to its artifacts' tiles — the UI names them first)* | grant/revoke; **change the disclosure policy**; transfer ownership |
| **knowledge** *(derived)* | the store exists; template and review counts | read templates, reviews, suggestions, benchmark results | create, edit and archive templates; resolve reviews; run a sweep | delete a benchmark set | change embedding settings; grant/revoke curation |
| **semantic_layer** *(derived)* | a layer exists; when it was last written | read the layer document | edit it; check an expression; queue a generation job | delete the layer | grant/revoke |
| **llm_config** | it exists; provider, model, capabilities. **never the key** | **use it to answer** — pick it in Chat, on a tile, in a report | ⚠️ **key-equivalent** (see below) | delete it | grant/revoke `select`; transfer ownership |
| **dashboard** | it exists; name, description, tile count | view it and its results *(each tile still needs `select` on **its** connection)* | edit; add / remove / re-layout tiles; import | delete the dashboard | grant/revoke; transfer ownership |
| **report** | it exists; name, description, language | view it and its runs' results *(plus `select` on its connection)* | edit the outline, sections, blocks, SQL; trigger a run | delete the report or a run | grant/revoke; transfer ownership |
| **conversation** | it exists; title, connection, when | read the transcript and its artifacts | rename; continue the thread; give feedback | delete the thread | grant/revoke; transfer ownership |
| **team** | it exists; name, member count | list its members | add and remove members | delete the team | assign roles to it; grant to it; rename |

> ### ⚠️ `modify` on an `llm_config` is equivalent to disclosing the API key
>
> A holder of `modify` can repoint `base_url` at a host they control, wait for
> the next call, and read the key out of the `Authorization` header. **This is a
> new finding from this investigation and it is why decision 6 is safe.** Three
> consequences, and they are enforced in code:
>
> 1. `modify`, `delete` and `manage` on an `llm_config` are **not offered in the
>    share UI at all** — the privilege radio for this type shows `describe` and
>    `select` only.
> 2. They are reachable **only** by the owner, or by an administrator through the
>    explicit self-grant path, and **every one writes an audit row**.
> 3. A change to `base_url` or `provider` on a config that has a stored key
>    **clears the key** and sets `status='UNTESTED'`. Re-entering it is the
>    friction that makes the attack loud instead of silent. A test asserts it.

### 13.4 What is not a resource, and stays that way

| Not a resource | Why | Governed by |
|---|---|---|
| `audit_logs` | a record *about people*; a per-row grant on an audit trail is a way to hide entries | the `audit.read` capability |
| `sessions`, `service_credentials` | credentials, not content | ownership + `user.manage` / `service_user.manage` |
| Schema snapshots | a fact about a connection with no independent identity | follows the connection |
| Tiles, sections, blocks, runs, messages, artifacts, templates | leaves | follow their parent |
| Settings | one global object | the `settings.manage` capability |

## 14. Grants

**A grant is one privilege, on one resource — or on every resource of a type — to
one principal.** Nothing else. No expiry (§24), no condition, no scope string, no
grantor chain.

```
   grant := (resource_type, resource_id | ALL, principal, privilege)
```

Four properties are load-bearing:

- **A grant points at `users.id` or `teams.id` — never at an email, never at an
  external subject.** This is what makes the OIDC migration free: when a local
  account becomes an OIDC account, `users.id` does not change, so **no grant is
  rewritten**.
- **A grant is additive and monotone.** Effective privilege is the **union** over
  every grant reaching the principal — directly, or through any team — plus role
  scoped privileges, plus ownership. There is no subtraction anywhere in the
  model, which is what makes the check a single `EXISTS` and the answer
  explainable in one sentence.
- **The wildcard is a first-class row.** `resource_id IS NULL` means *every
  resource of this type*. Two things may write one: a role definition (through
  `role_scoped_privileges`, which is materialised into the same check) and an
  administrator making a deliberate, audited installation-wide grant. Nothing
  else, and the API refuses `resource_id: null` from any caller without
  `role.manage`.
- **`manage` is not implied by `modify`.** Someone who may edit a dashboard may
  not re-share it. This is the useful half of Tableau's *locking* without
  Tableau's ternary logic, and it is what keeps *"no grant is more than one hop
  from an owner or an administrator"* true — which in turn is what lets a grant
  row omit its grantor and lets revocation avoid cascading.

### 14.1 Ownership is not a grant

`owner_id` stays exactly where it is, on every table that has it, meaning exactly
what it means. Three things follow, and all three are why this design is cheap:

- **No backfill.** Turning grants on creates no rows; the authorizer reads
  ownership **and** grants from day one.
- **Rolling back is a config flip** — `authz_backend` returns to `owner_only`
  and the grants table is simply not read.
- **The owner is never locked out of their own resource**, so there is no
  bootstrap problem and no *"who granted the first grant"*.

Ownership confers the full lattice including `manage`. Transferring it is an
explicit, audited action gated on `manage` (§19.3).

## 15. The effective-permission algorithm

> **Requirement 3 asks for this to be stated clearly. This section is that
> statement, and §26's rulebook repeats it verbatim.**

### 15.1 In one sentence

**A principal may perform an action on a resource if *any* of five independent
facts says so; the facts are unioned, never compared, never ordered, and there is
no fact that takes permission away.**

### 15.2 Formally

```
capabilities(P) =  ⋃  role.capabilities
                   for role ∈ roles(P) ∪ roles(teams(P))

privileges(P, R) =
      { all }                    if owner(R) = P                     ── (1) ownership
    ∪ { g.privilege }            for g ∈ grants
                                 where g.resource_type = type(R)
                                   and g.resource_id  = id(R)
                                   and g.principal    = P            ── (2) direct grant
    ∪ { g.privilege }            … and g.principal ∈ teams(P)        ── (3) team grant
    ∪ { g.privilege }            … and g.resource_id IS NULL         ── (4) wildcard grant
    ∪ { sp.privilege }           for sp ∈ role_scoped_privileges
                                 where sp.resource_type = type(R)
                                   and sp.role ∈ roles(P) ∪ roles(teams(P))
                                                                     ── (5) role privilege

allowed(P, R, needed) ⟺ privileges(P, R) ∩ satisfying(needed) ≠ ∅
```

`satisfying(needed)` is the lattice expansion of §13.2 — so a holder of `manage`
passes a `select` check without anyone remembering that they should.

### 15.3 The rules that fall out, stated so they can be quoted at a user

1. **More memberships never mean less access.** Adding a team, a role or a grant
   can only widen. This is Metabase's and Power BI's doctrine and it is the
   property that makes the model predictable.
2. **There is no `deny`, no priority, no ordering.** Two roles never conflict,
   because there is nothing for them to conflict about. §9 records why Tableau's
   alternative is rejected.
3. **Capabilities and privileges never substitute for each other.**
   `dashboard.create` does not let you open somebody's dashboard;
   `manage` on a dashboard does not let you create a new one.
4. **A wildcard privilege is still a privilege on one type.** Knowledge Manager's
   `(knowledge, manage)` says nothing about connections, dashboards or LLM
   configs.
5. **The intersection rule is not a subtraction.** A viewer who may see a
   dashboard but not a tile's connection is not being *denied* the tile — they
   simply never held `select` on that connection. The union is over two
   *different* resources, and both must pass. §19.2.
6. **An administrator's power is `user.manage`, not data.** Reading somebody's
   data requires a grant, and an administrator making one to themselves is an
   ordinary grant row plus an `admin.self_granted` audit row.

### 15.4 Worked examples

| Situation | Result | Which fact |
|---|---|---|
| Ali owns `conn-A`; asks a question through it | allowed | (1) |
| Sara is in team *Finance*; *Finance* holds `select` on `conn-A` | allowed to ask | (3) |
| Sara also holds Viewer (no `conversation.create`) | **refused to open a thread** | capability missing — a different question |
| Reza holds Knowledge Manager; opens `conn-A`'s knowledge console | allowed to curate | (5), `(knowledge, manage)` |
| Reza asks a question through `conn-A` | **refused** — Knowledge Manager grants nothing on `connection` above `describe` | no fact says yes |
| Nina holds a direct `select` grant on `dash-1` and is in team *BI* which holds `modify` on `dash-1` | may edit | (2) ∪ (3), most-permissive |
| Nina opens `dash-1`; tile 2 runs on `conn-B` she cannot read | dashboard renders; **tile 2 renders as a named placeholder** | §19.2 |
| An agent service user holds team *BI*'s grants | identical to a human in team *BI* | (3) — no special case |
| Admin Sam has no grant on `conn-A` | **sees nothing**, and may self-grant, audited | decision 14 |

## 16. The invariants

Five rules. Everything else in this document is detail around them, and §26's
conformance test enforces each one that a machine can check.

> **I1 — One decision function.** No SQL in `api/` or `services/` filters on
> `owner_id` to decide access, and no handler compares a role string. Visibility
> comes from `authz.visible(...)`; permission comes from `authz.allowed(...)`;
> app-wide verbs come from `ctx.can(...)`. *Checked by grep in `make
> authz-check`, and by `test_authz_conformance.py`.*

> **I2 — Sharing an artifact never shares the data behind it.** A tile, block or
> turn renders only if the viewer holds `select` on **that** resource's
> connection, re-checked at execution. *Checked by an integration test with a
> two-connection dashboard.*

> **I3 — Every authorization event is a row.** Grant, revoke, role assignment,
> team membership change, ownership transfer, administrator self-escalation,
> service-key issue and revoke, disclosure-policy change, and every 403.
> `Decision.because` travels with it. Identifiers and counts, never content.
> *Checked by a test that asserts one row per denial and none per 404.*

> **I4 — Permission is a union and nothing subtracts.** No `deny` row, no
> priority column, no evaluation order. *Checked by a property test: adding any
> grant, role or team membership never turns an `allowed` into a denial.*

> **I5 — The UI is an affordance, never a boundary.** Every restriction the
> frontend renders exists because the server answered a question, and the same
> request made without the UI is refused by the server. *Checked by a test that
> calls every mutating route as an unprivileged principal and asserts 403/404.*

## 17. The data model

Six migrations, deliberately separate so each phase ships on its own. Numbering
continues from `0023_token_accounting` (decision 16).

| Migration | Phase | Adds |
|---|:--:|---|
| `0024_principal_kind.py` | 5 | `users.kind`, `users.description`, three `CHECK`s |
| `0025_service_credentials.py` | 5 | `service_credentials` |
| `0026_roles.py` | 3 | `roles`, `role_capabilities`, `role_scoped_privileges`, `role_assignments`, + seed & backfill |
| `0027_teams.py` | 4 | `teams`, `team_members` |
| `0028_grants.py` | 6 | `grants` |
| `0029_drop_users_role.py` | 10 | drops `users.role` once nothing reads it |

> **Order note, resolved.** Phase 3 shipped first, so roles landed as
> **`0024_roles.py`** and teams as **`0025_teams.py`**; the remaining numbers
> shift down by two. The shapes below are unchanged, with one honest
> consequence of the reordering: `role_assignments.team_id` cannot exist before
> `teams` does, so `0024` creates the table with a non-null `user_id` and a
> two-column uniqueness, and `0025` widens it — nullable `user_id`, a
> `team_id`, the one-principal `CHECK`, and the three-column unique constraint
> `0024` already named.

### 17.1 `users`, extended

```sql
ALTER TABLE users ADD COLUMN kind        varchar(20) NOT NULL DEFAULT 'HUMAN';
ALTER TABLE users ADD COLUMN description text;          -- what a service user is for

ALTER TABLE users ADD CONSTRAINT ck_users_kind
      CHECK (kind IN ('HUMAN', 'SERVICE'));
ALTER TABLE users ADD CONSTRAINT ck_users_service_no_password
      CHECK (kind <> 'SERVICE' OR password_hash IS NULL);
ALTER TABLE users ADD CONSTRAINT ck_users_service_no_external_subject
      CHECK (kind <> 'SERVICE' OR external_subject IS NULL);
ALTER TABLE users ADD CONSTRAINT ck_users_service_no_password_change
      CHECK (kind <> 'SERVICE' OR must_change_password = false);

CREATE INDEX ix_users_kind ON users (kind);
```

`users.email` stays `NOT NULL UNIQUE` and a service user gets a **synthetic,
non-routable** address — `svc-<slug>@service.datamind.local` — so every existing
join, uniqueness check and display path keeps working. It is generated, never
typed, and the UI shows the display name.

`users.role` is **left in place** through Phase 9 as a read-only cache written by
the role service, so a rollback is a config flip rather than a migration. It is
dropped in `0029`.

### 17.2 `service_credentials`

```sql
CREATE TABLE service_credentials (
    id               uuid PRIMARY KEY,
    service_user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             varchar(100) NOT NULL,       -- "nightly-report-agent"
    -- The non-secret half of the key. Indexed and unique so a key found in a
    -- log or a repository can be traced to its owner WITHOUT the secret half
    -- ever being stored. This is the whole reason the key has two parts.
    prefix           varchar(16)  NOT NULL,
    -- SHA-256 over the secret half, hex. **Deliberately not Argon2id**, and the
    -- reason is not laziness: the secret is 256 bits from `secrets.token_urlsafe`,
    -- not a human password, so key-stretching defends against nothing and would
    -- add 50-100 ms of CPU to *every* API call this identity makes. The password
    -- path keeps Argon2id for exactly the opposite reason.
    token_hash       varchar(64)  NOT NULL,
    -- Reserved, unread, and named now so a future narrowing does not need a
    -- migration: a list of capability/privilege strings this key may exercise,
    -- always a SUBSET of what the service user holds. Empty = the principal's
    -- full permission.
    scopes           jsonb        NOT NULL DEFAULT '[]'::jsonb,
    expires_at       timestamptz,
    last_used_at     timestamptz,
    revoked_at       timestamptz,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    created_by       uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT uq_service_credentials_prefix UNIQUE (prefix)
);
CREATE INDEX ix_service_credentials_user ON service_credentials (service_user_id);
```

**The key format is `dm_sk_<prefix>_<secret>`** — `dm_sk` so a secret scanner can
be taught one pattern, `prefix` = 12 chars of base32 for lookup, `secret` = 32
bytes urlsafe. Verification is: split, `SELECT … WHERE prefix = :p` (one index
hit), constant-time compare of `sha256(secret)`, then check `revoked_at IS NULL`
and `expires_at`. **`last_used_at` is written at most once per minute per key**,
so a busy integration does not turn every request into a write.

### 17.3 `roles`, and the two tables that fill them

```sql
CREATE TABLE roles (
    id          uuid PRIMARY KEY,
    name        varchar(100) NOT NULL,
    description text NOT NULL DEFAULT '',
    -- A seeded role. Its capability set changes only through a migration, never
    -- through the API and never re-synchronised on boot — the failure mode
    -- Superset's `superset init` demonstrates. Its name and description are
    -- editable and it cannot be deleted.
    is_system   boolean NOT NULL DEFAULT false,
    -- The external role this mirrors, when it mirrors one. Both columns or
    -- neither. Unused until an OIDC adapter exists; present now because
    -- retrofitting a namespace onto identifiers that assignments point at is
    -- the migration nobody wants. The sibling of teams.(provider_id, source_id).
    provider_id varchar(50),
    source_id   varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_roles_name   UNIQUE (name),
    CONSTRAINT uq_roles_source UNIQUE (provider_id, source_id),
    CONSTRAINT ck_roles_source_pair CHECK ((provider_id IS NULL) = (source_id IS NULL))
);

CREATE TABLE role_capabilities (
    role_id    uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    capability varchar(50) NOT NULL,
    PRIMARY KEY (role_id, capability)
);

-- A privilege this role carries over EVERY resource of a type. A role may never
-- name one resource id — that is what `grants` is for, and keeping them apart is
-- what makes "why can Ali see this?" answerable in one sentence.
CREATE TABLE role_scoped_privileges (
    role_id       uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    resource_type varchar(30) NOT NULL,
    privilege     varchar(20) NOT NULL,
    PRIMARY KEY (role_id, resource_type, privilege)
);

CREATE TABLE role_assignments (
    id          uuid PRIMARY KEY,
    -- RESTRICT, not CASCADE: deleting a role that people hold must fail loudly
    -- and name them, the way `_guard_last_admin` already refuses to strand a
    -- workspace with no administrator.
    role_id     uuid NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
    team_id     uuid REFERENCES teams(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_role_assignment_one_principal
        CHECK ((user_id IS NULL) <> (team_id IS NULL)),
    CONSTRAINT uq_role_assignment UNIQUE NULLS NOT DISTINCT (role_id, user_id, team_id)
);
CREATE INDEX ix_role_assignments_user ON role_assignments (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX ix_role_assignments_team ON role_assignments (team_id) WHERE team_id IS NOT NULL;
```

**Backfill in the same migration**, so no install lands without roles:

```sql
-- every existing ADMIN gets the Administrator role; everyone else Normal User
INSERT INTO role_assignments (id, role_id, user_id)
SELECT gen_random_uuid(),
       (SELECT id FROM roles WHERE name = CASE WHEN u.role = 'ADMIN'
                                               THEN 'Administrator' ELSE 'Normal User' END),
       u.id
FROM users u;
```

### 17.4 `teams`

```sql
CREATE TABLE teams (
    id          uuid PRIMARY KEY,
    name        varchar(100) NOT NULL,
    description text NOT NULL DEFAULT '',
    provider_id varchar(50),          -- see roles.provider_id; identical contract
    source_id   varchar(255),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_teams_name   UNIQUE (name),
    CONSTRAINT uq_teams_source UNIQUE (provider_id, source_id),
    CONSTRAINT ck_teams_source_pair CHECK ((provider_id IS NULL) = (source_id IS NULL))
);

-- Meaningful only for DataMind-managed teams: when a team is provider-managed,
-- membership comes from the token on each login and this table stays empty.
CREATE TABLE team_members (
    team_id  uuid NOT NULL REFERENCES teams(id)  ON DELETE CASCADE,
    user_id  uuid NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    added_at timestamptz NOT NULL DEFAULT now(),
    added_by uuid REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX ix_team_members_user ON team_members (user_id);
```

Deleting a team that holds role assignments or grants is **refused**, naming what
it holds — never silently reassigned (Metabase's *"reassigned to All Users"* is
the anti-pattern, §9).

### 17.5 `grants`

```sql
CREATE TABLE grants (
    id            uuid PRIMARY KEY,
    resource_type varchar(30) NOT NULL,   -- ResourceType, validated in the domain
    -- NULL means EVERY resource of this type. Writable only by a caller holding
    -- `role.manage`, and always audited. This one nullable column is what makes
    -- "Knowledge Manager over everything" a row instead of an `if`.
    resource_id   uuid,
    -- Exactly one principal. A CHECK, not a convention. A service user is a
    -- `users` row, so it needs no third column.
    user_id       uuid REFERENCES users(id) ON DELETE CASCADE,
    team_id       uuid REFERENCES teams(id) ON DELETE CASCADE,
    privilege     varchar(20) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    created_by    uuid REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT ck_grants_one_principal CHECK ((user_id IS NULL) <> (team_id IS NULL)),
    -- NULLS NOT DISTINCT is the point: without it Postgres treats every row with
    -- a NULL in the key as unique and the table silently accepts duplicates.
    -- Requires PG15+; this deployment is on postgres:16.
    CONSTRAINT uq_grants UNIQUE NULLS NOT DISTINCT
        (resource_type, resource_id, user_id, team_id, privilege)
);

CREATE INDEX ix_grants_resource  ON grants (resource_type, resource_id);
CREATE INDEX ix_grants_user      ON grants (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX ix_grants_team      ON grants (team_id) WHERE team_id IS NOT NULL;
CREATE INDEX ix_grants_wildcard  ON grants (resource_type, privilege)
       WHERE resource_id IS NULL;
```

**No foreign key on `resource_id`**, because it is polymorphic across eight
types, two of which are derived and share their parent's id. The cost is that a
deleted resource can leave an orphaned grant; the answer is a delete hook in the
service **plus** a sweep in [`workers/reconciler.py`](../backend/app/workers/reconciler.py),
and **not** eight nullable FK columns.

**No `grantor` column beyond `created_by`.** Nothing walks a chain on revoke,
because no chain exists (§14, `manage` is not implied by `modify`).

### 17.6 What the schema deliberately does not have

| Absent | Why | What would bring it |
|---|---|---|
| `grants.expires_at` | An expiring grant needs a sweeper, a notification, and a story for *"expired mid-render"*. None of that is MVP | *"a contractor needs access for two weeks"* → one column, one sweeper, one decision |
| `grants.parent_id` / a container | Grants are per-resource until grant **count** is a real complaint | *"I need to share a folder"*, or one principal holding >50 grants → §24 |
| `team_members.member_team_id` | Flat teams, §11.2 | an IdP that emits nesting DataMind must resolve itself |
| `deny` / a priority column | I4 | nothing. This is a "no" |
| `resource_id` FK | polymorphic by design | nothing; the sweep is cheaper |
| A `permissions` table of strings | Superset's bag. Capabilities are an enum in code and a `varchar` in the row | nothing |
| A separate `service_users` table | §11.1 | nothing |
| `identities` (one user, many external subjects) | one issuer is the realistic case; `external_subject` is namespaced so a second is a migration, not a redesign | a second IdP, or a user who must keep two logins → §24 |

## 18. Backend authorization — the port and the enforcement surface

> **Requirement 7: a centralised approach rather than `if user.is_admin`
> scattered through the codebase.**

### 18.1 The port

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

    async def privileges_on(
        self, ctx: RequestContext, ref: ResourceRef
    ) -> frozenset[Privilege]: ...     # powers GET …/actions and the UI
```

```python
@dataclass(frozen=True, slots=True)
class Decision:
    """Allowed, plus why — so a denial can be audited with a reason.

    `because` is grant ids, role names, the literal word `owner`, or
    `admin_self_grant`. Never free text: it goes into `audit_logs.detail`, and a
    log that says "no, because the only privilege reaching you is describe" is a
    different artifact from one that says "no".
    """
    allowed: bool
    because: tuple[str, ...] = ()

Visible = Everything | Subquery | Ids
```

- `Everything` — the principal holds a wildcard for this type, or authorization
  is disabled. **Checked first**, so the common administrator case costs no join.
- `Subquery(select)` — what `RbacAuthorizer` returns: a SQLAlchemy `Select` of
  ids that composes into the caller's existing query as
  `.where(Dashboard.id.in_(subq))`. **One round trip, one plan, one index scan.**
- `Ids(frozenset)` — the shape an out-of-process authorizer would have to
  return. Present so the port is honest, not so it gets used.

`app.domain` may not import `sqlalchemy` (an import-linter contract already
enforces it), so `Subquery` is an opaque carrier declared in the domain and
constructed only in `infra/authz/`.

### 18.2 The two operations, and where each is used

| | `allowed(ctx, ref, privilege)` | `visible(ctx, type, privilege)` |
|---|---|---|
| Question | may this principal do this to **this** thing? | which of these things may they see? |
| Used by | `GET /x/{id}`, every mutation, every execution | every `GET /x` list endpoint |
| Cost | one indexed lookup | one subquery folded into the caller's `SELECT` |
| **Anti-pattern** | — | **N calls to `allowed` in a loop.** This is the research's L7. A list endpoint that filters in Python has a bug, not a style problem — pagination will be wrong |

### 18.3 The `visible` subquery, in full

```sql
-- ids of <type> the principal may act on at <privilege>.
-- Step 0, in Python, before any SQL: if a wildcard grant or a role scoped
-- privilege covers (type, satisfying(privilege)), return Everything.
  SELECT id FROM <table> WHERE owner_id = :actor                    -- (1)
UNION
  SELECT g.resource_id
    FROM grants g
   WHERE g.resource_type = :type
     AND g.privilege     = ANY(:satisfying)      -- the lattice, expanded once
     AND g.resource_id IS NOT NULL
     AND (g.user_id = :actor OR g.team_id = ANY(:teams))            -- (2)(3)
```

For the two **derived** types the `owner_id` arm reads
`database_connections`, because a knowledge store's owner is its connection's
owner.

### 18.4 The enforcement surface — three shapes, and no fourth

**(a) A declarative route guard, for the common case.** This is the answer to
OWASP API1: the check runs in a dependency, *before* the handler, and cannot be
forgotten because the handler cannot get its `ctx` without it.

```python
# app/api/deps.py
def needs(capability: Capability) -> Callable[..., Awaitable[RequestContext]]:
    """App-wide verb. Replaces every `AdminDep` and every `ctx.is_admin`."""

def on(
    type_: ResourceType, privilege: Privilege, param: str = "id"
) -> Callable[..., Awaitable[RequestContext]]:
    """Resource-scoped. Reads the id from the path, asks the authorizer, and
    raises the §19.1 error before the handler body runs."""
```

```python
@router.get("/{dashboard_id}", response_model=DashboardRead)
async def get_dashboard(
    dashboard_id: UUID,
    ctx: Annotated[RequestContext, Depends(on(ResourceType.DASHBOARD, Privilege.SELECT, "dashboard_id"))],
    svc: DashboardServiceDep,
) -> Dashboard:
    return await svc.get(ctx, dashboard_id)

@router.get("", response_model=list[UserRead])
async def list_users(ctx: Annotated[RequestContext, Depends(needs(Capability.USER_READ))], db: DbDep):
    ...
```

**(b) An in-service check**, where the resource is not addressed by the path —
a tile's connection at execution time, a report block's connection, the
`llm_config` chosen for a run. These call `authz.allowed(...)` directly and are
the only places that may.

**(c) A composed `visible(...)`**, in every list endpoint.

**There is no fourth shape.** In particular there is no `if ctx.role == …`, no
`if ctx.is_admin`, and no bare `WHERE owner_id = :actor` in `api/` or
`services/`. `make authz-check` greps for all three.

### 18.5 Two implementations, and a third that is only named

| Implementation | Phase | What it does |
|---|:--:|---|
| `OwnerOnlyAuthorizer` | 0 | `allowed` is `owns(...)`; `visible` is `Subquery(owner_id == actor)`. **Today's behaviour, exactly** |
| `RbacAuthorizer` | 6 | The five facts of §15.2, wildcard short-circuit first |
| *`ExternalAuthorizer`* | — | never. The `Ids` arm exists so this stays *possible*, not so it happens |

Selected by `authz_backend: Literal["owner_only", "rbac"] = "owner_only"` in
`core/config.py`, resolved in `api/deps.py` exactly as `get_identity_provider`
already resolves the identity port. **The default flips to `rbac` at the end of
Phase 6**, and the previous value stays a working rollback for one release.

### 18.6 The two endpoints the UI reads

```
GET /me/permissions            → { capabilities: [...], roles: [...], teams: [...] }
GET /{resource}/{id}/actions   → { privileges: [...], can: { edit: true, share: false, ... } }
```

Grafana has the first (`/api/access-control/user/permissions`) for exactly this
reason. **The UI renders every affordance from these two answers**, never from a
role string — which is what makes I5 true rather than aspirational.

## 19. Errors, the existence oracle, and the audit half

### 19.1 404, 403, and what each leaks

Today's pattern returns **404 for a resource you do not own**, which leaks
nothing. Grants make the distinction meaningful, and getting it backwards turns
every list endpoint into an existence oracle.

> **404 unless the caller holds at least `describe`. 403 above that.**

| Situation | Status | Audited? |
|---|---|---|
| No fact reaches the principal at all | **404** | no — indistinguishable from a typo |
| Holds `describe`, needs `select` | **403**, naming the privilege needed | **yes**, `DENIED` + `because` |
| Holds `select`, needs `modify` / `delete` / `manage` | **403** | **yes** |
| Holds `select` on the artifact, not on a tile's connection | **200**, tile rendered as *"no access to this data source"* | **yes**, once per render |
| Lacks the capability for a create/admin route | **403** | **yes** |
| A service user hitting a route its key's `scopes` exclude | **403** | **yes** |

Implemented **once**, in an exception helper, not per router.

### 19.2 The intersection rule, made visible

A dashboard renders. Each tile renders **iff** the viewer holds `select` on that
tile's connection. A tile they cannot see renders as a **named placeholder** —
not hidden, because hiding it makes the dashboard silently wrong, and a partly
visible dashboard is a better product than a refused one **and** a better product
than a leaking one.

Sharing a dashboard **warns** when its tiles span connections the grantee cannot
read, and names them. The share is still allowed; the surprise is not.

### 19.3 Ownership transfer, and deleting a principal

- `POST /{resource}/{id}/transfer` — gated on `manage`, audited, and the new
  owner must be `ACTIVE`.
- `DELETE /users/{id}` **refuses** while the principal owns any grantable
  resource, naming what they own.
  [`_guard_last_admin`](../backend/app/api/v1/users.py#L132) is the precedent for
  refusing a destructive action with an explanation; it becomes
  `_guard_last_administrator`, counting `role_assignments` rather than
  `users.role`.
- `DISABLED` keeps every grant, role and team membership, because disabling is
  reversible and re-enabling must not be a re-grant. Power BI documents the same
  choice for the same reason.
- Deleting a **team** or a **role** that anything points at is refused, naming
  the holders.

### 19.4 The audit vocabulary this plan adds

Appended to [`services/audit.py`](../backend/app/services/audit.py), which
already has the machinery and the three rules. **`DENIED` finally gets a
producer.**

```
role.created · role.updated · role.deleted · role.assigned · role.unassigned
team.created · team.renamed · team.deleted · team.member.added
team.member.removed · team.source.bound
grant.created · grant.revoked · grant.wildcard.created
ownership.transferred · access.denied · admin.self_granted
service_user.created · service_user.disabled · service_user.deleted
service_credential.issued · service_credential.revoked · service_credential.expired
disclosure.changed · llm_config.endpoint.changed
```

Rule 3 of that module is unchanged and non-negotiable: **`detail` carries
identifiers and counts, never content.** A denial row carries the resource id,
the privilege needed, and `Decision.because` — never the SQL, the question, or a
row.

### 19.5 The disclosure interaction

Each connection declares a `disclosure_policy` — how much of a result may reach
the model provider ([security.md §3](security.md)). Today the person who chose
that policy is the only person who can trigger a query under it. **The moment a
connection is shared, one person's disclosure choice governs another person's
questions**, and that person may not know what it is.

Three rules, and they are why `describe` is a real privilege rather than a
formality:

1. **`describe` exposes the policy.** A grantee must be able to see what leaves
   *before* they ask.
2. **`modify` may not change it; `manage` may.** Widening `NONE` → `FULL` is not
   an edit, it is a disclosure decision. Phase 6 splits the field out of the
   ordinary update payload into its own `manage`-gated endpoint.
3. **Every ask records the policy in force**, in the audit row. This is the
   second half of mvp2 D4 and the sentence the README's positioning depends on.

The unsolved half is named rather than smoothed over in §26: a grant to a team of
forty is a disclosure decision made on behalf of forty people.

## 20. Authentication, and how OIDC / Keycloak arrives later

> **Requirement 9: authentication and authorization separated, so an OIDC
> provider can arrive without redesigning user management.**

### 20.1 The separation, stated

**Authentication** answers *who is calling* and produces a
`RequestContext`. **Authorization** answers *what they may do* and reads only
`ctx.user_id`, `ctx.kind`, `ctx.capabilities` and `ctx.team_ids`. **The
authorizer never sees a token, a password, a cookie or an issuer**, and the
import-linter contract that keeps `app.domain` free of `fastapi` is what makes
that structural rather than aspirational.

There are already **two** authenticators after Phase 5 — password sessions and
service keys — and that is the point: the seam is exercised by shipped code
before OIDC ever arrives, so it cannot rot.

### 20.2 The eight seams

| # | Seam | Phase | Status today |
|---|---|:--:|---|
| S1 | `IdentityProvider` Protocol, five methods | — | ✅ exists |
| S2 | `users.external_subject`, namespaced `provider~subject` | 3 | column exists since `0001`, **never read or written** |
| S3 | `ctx.team_ids` — resolved once per request, source unknown downstream | 4 | to build |
| S4 | `teams.(provider_id, source_id)` | 4 | to build |
| S5 | `roles.(provider_id, source_id)` — **new in this plan**: an OIDC `roles` claim maps to DataMind roles exactly as a `groups` claim maps to teams | 3 | to build |
| S6 | `auth_provider` config switch, one implementation | 0 | to build |
| S7 | A **second** authenticator in the tree (service keys) proving the seam | 5 | to build |
| S8 | `ctx.capabilities` resolved from the database, never from a claim | 3 | to build |

### 20.3 The recipe, written now so it stays honest

When SSO is asked for:

1. Add `oidc_issuer`, `oidc_audience`, `oidc_subject_claim` (default `"sub"`),
   `oidc_groups_claim`, `oidc_roles_claim` beside the existing `auth_provider`.
2. Write `OidcIdentityProvider.verify_access_token`: fetch and cache JWKS from
   `{issuer}/.well-known/openid-configuration`; verify signature, `iss`, `aud`
   and `exp` **locally** — no introspection call per request, so the IdP sits on
   the startup and key-rotation paths and never on the hot path.
3. `external_subject` becomes `"{provider_id}~{subject}"`. **The prefix is not
   decoration** — a bare `sub` collides the day a second issuer appears.
4. Map the groups claim through `teams WHERE provider_id = :p AND source_id = :v`,
   and the roles claim through `roles` the same way. **Unknown values are
   ignored, never auto-created.** An IdP that renames a group must not silently
   create an empty one; binding is a deliberate, audited admin act.
5. `authenticate` and `rotate_session` raise `NotImplementedError`;
   `/auth/login`, `/auth/refresh` and the `raymand_refresh` cookie disappear
   under `auth_provider="oidc"`. **Service keys are unaffected** — a machine
   identity does not do an authorization-code flow, and if the customer wants
   one, the client-credentials grant replaces `service_credentials` behind the
   same `ServiceIdentityProvider` seam.
6. First OIDC login matches on lowercased email, sets `external_subject`, clears
   `password_hash`. **Every `owner_id`, every grant, every role assignment and
   every team membership still points at the same `users.id`** — nothing is
   re-granted, nothing is re-shared, nothing is re-assigned.
7. The bootstrap administrator stays **local**, as break-glass. Local and OIDC
   accounts coexist; a user with `external_subject` set may no longer use a
   password.

**Testing it needs no Keycloak.** Generate an RS256 keypair in a fixture, serve
the public half as a static JWKS dict, mint tokens with `pyjwt` (already a
dependency), and assert on expiry, wrong audience, wrong issuer, unknown `kid`,
group mapping and role mapping. Roughly forty lines of `conftest.py`, running in
milliseconds. A real Keycloak belongs in a manual `docker-compose.oidc.yml` that
CI never starts — the treatment `docker-compose.replicas.yml` already gets.

### 20.4 What must not happen

- **No permission ever keys on an email or an external subject.** Only
  `users.id` and `teams.id`.
- **No claim auto-creates a privileged role or team.** JIT provisioning creates
  the *user* and assigns the *default* role (`Normal User`, configurable); every
  other assignment comes from a binding an administrator made.
- **No capability is read out of a token.** S8. A token that could carry
  `user.manage` is a token whose issuer is now part of DataMind's permission
  system.

## 21. UI and UX

> **Requirement 6: determine exactly where user management and access control
> should exist within the *current* DataMind UI, after analysing it.** §2 is that
> analysis; this section is the conclusion.

### 21.1 The constraint the existing UI imposes

[frontend.md §1](frontend.md) states the rule that decides this whole section:

> *"The list stays flat and uncaptioned; the ordering is what carries the
> grouping, so it must not zig-zag across that boundary."*

The rail is seven rows in a deliberate order — four you **work** in (Chat,
Dashboards, Reports, Knowledge), three you **keep** (Data sources, LLM providers,
Users). Adding **Teams**, **Roles**, **Service accounts** and **Access review**
as rail rows would take it to eleven, put four rarely-touched administration
rows below a line that already ends the product's daily-use ordering, and break
the one rule the rail has.

So: **one destination, six tabs.**

### 21.2 The change to the rail — one row, renamed

```
   BEFORE                          AFTER
   Chat                            Chat
   Dashboards                      Dashboards
   Reports                         Reports
   Knowledge                       Knowledge
   Data sources                    Data sources
   LLM providers                   LLM providers
   Users            (adminOnly)    Administration   (needs user.read OR team.read
                                                     OR role.read OR audit.read)
```

Seven rows before, seven after. The row's **gate changes from a role string to a
capability set** — which is the frontend half of retiring `is_admin`, and it is
what lets an **Auditor** reach the section without being an administrator, and a
**DataMind Maintainer** reach Service accounts without reaching People.

`/users` becomes a **permanent redirect** to `/admin/people`. Bookmarks and the
links in `docs/` keep working; the ui-improvement plan's own finding — that a
deep link nobody tested was broken for five phases — is the reason this is
written down rather than assumed.

### 21.3 `/admin` — master–detail, six tabs

It takes the **master–detail** frame from
[`components/settings.tsx`](../frontend/src/components/settings.tsx) that Data
sources, LLM providers and Knowledge already share, because that is what it is:
a configuration surface where you pick a record on the left and work on it on the
right. The tab strip is in the URL (`/admin/:tab`, `/admin/:tab/:id`) for exactly
the reason the Data sources strip is: *"where do I set X?"* should be answerable
with a link.

| Tab | Route | Gate | What it holds |
|---|---|---|---|
| **People** | `/admin/people` | `user.read` | Today's `UsersPage` list, **furniture intact** — search, role filter, status filter, sort, the one-time-password panel, the confirm on every destructive act. The detail drawer gains three sections: **Roles** (chips + picker), **Teams** (chips + picker), **Effective access** (read-only, §21.6) |
| **Service accounts** | `/admin/service-accounts` | `service_user.manage` | List of `kind='SERVICE'` principals: name, description, roles, teams, key count, last used. Detail = the same Roles/Teams/Effective-access sections **plus** a Keys panel (§21.5) |
| **Teams** | `/admin/teams` | `team.read` | List with member counts. Detail = members picker (humans **and** service accounts in one list, kind-badged), roles assigned to the team, and the resources the team holds grants on |
| **Roles** | `/admin/roles` | `role.read` | The eight system roles (badged, capability set read-only) and any custom roles. Detail = a **capability checklist** grouped as §12.1 groups them, and a **scoped-privilege matrix** (8 resource types × 5 privileges) |
| **Access review** | `/admin/access` | `access.review` | §21.6 |
| **Audit** | `/admin/audit` | `audit.read` | The first UI for `GET /audit`, which today has an endpoint and no screen. Filters for actor, action, resource type, outcome; **denials rendered distinctly** |

**Why Audit moves here rather than getting its own rail row.** It is a record
*about people*, it is read by the same two roles that read the other five tabs,
and `api/v1/__init__.py` already calls it *"a peer of `users`… both are about
people rather than about a connection's data."* The information architecture
should say what the routing already says.

### 21.4 Where resource-level access lives — beside the resource

> The requirement's own suggestion — *"access management may belong close to the
> individual resource"* — is right, and the current UI already has the two
> shapes needed.

| Resource | Surface | Shape | Why this shape |
|---|---|---|---|
| **Connection** | `/sources/:id/access` — a **fifth tab**, between Policy and Schema | Tab | A connection's access is a *governance* surface reviewed alongside its disclosure policy, not a moment-in-time act. Putting it next to Policy is the point: §19.5 rule 2 means the two are one decision |
| **Knowledge** | `/knowledge/:id`, an **Access** control in the console header | Popover | The Knowledge Manager's surface. It grants on the derived `knowledge` resource, and it says so — *"this does not grant access to the data"* is on the panel |
| **Semantic layer** | `/sources/:id/semantic`, an **Access** control in the tab header | Popover | Same reasoning, same sentence |
| **Dashboard** | **Share** — from the index card's kebab **and** the board header | Dialog | Sharing a dashboard is an act, taken at a moment, usually from the list. Metabase, Grafana and Power BI all put it here |
| **Report** | **Share** — index card kebab and the report header | Dialog | As dashboards |
| **LLM config** | `/providers/:id`, an **Access** section in the detail form | Section | It is a short list and the page is already one form. **The privilege radio offers `describe` and `select` only** — §13.3's ⚠️ box, rendered as a rule rather than a warning |
| **Conversation** | The thread kebab in Chat's rail | Dialog | Personal by default; the affordance is present and quiet |
| **Team** | `/admin/teams/:id` | — | A team is administered where teams are administered |

**One component, not eight.** `<AccessPanel resource={{type, id}} />` renders the
principal picker, the privilege radio, the current-access list and the revoke
action, and it takes its **allowed privilege set from the server** (`GET
…/actions`) rather than from a constant — which is how the `llm_config`
restriction and any future per-type restriction arrive with no second
implementation. This follows the rule `frontend.md` §3 already states: *"when two
screens must agree about a guard verdict, a disclosure rule or a parameter
proposal, they share the component. Two editors are two chances to get one of
them wrong."*

**The principal picker is one dropdown** containing users, service accounts and
teams, kind-badged — Grafana's pattern, and the one that stops *"where do I add a
team?"* being a question. It **hints toward teams**: picking three individuals in
a row surfaces *"these three are all in Finance — grant to the team instead?"*.
Metabase's group-only model is too strict for a two-person install; a nudge is
the middle.

### 21.5 The service-account surfaces

**Creating one** is a three-field form — name, description (*"what is this for"*,
required, because an undocumented machine identity is the one nobody dares
delete), and roles/teams — and the form shows the **effective permission it will
have** before Save. A key is issued from the detail page, never at creation, so
that "make an identity" and "hand out a secret" are two deliberate acts.

**Issuing a key** shows it **exactly once**, in the same panel shape the
one-time-password flow already uses on `UsersPage` — the furniture exists and is
already correct about this. The panel carries the expiry, a copy button, and the
sentence that it cannot be shown again.

**The key list** shows name, `prefix`, created, expiry, `last_used_at`, and
revoke. Never the secret. A key unused for 90 days is flagged, and one past
expiry is struck through — the only way anyone ever answers *"can I delete this?"*

**What the UI refuses.** The role picker on a service account **hides**
`user.manage`, `role.manage`, `service_user.manage` and `settings.manage` unless
`allow_privileged_service_users` is on, and says why. The server refuses them
regardless (I5).

### 21.6 Communicating what a user can and cannot access

This is the half of requirement 6 that the researched products are all weakest
at, and it is cheap once `Decision.because` exists.

**Four surfaces:**

1. **"Shared with me"** — a filter chip on the Dashboards and Reports index
   toolbars, and a section header when the filter is off. The toolbar already
   offers *"a filter only when there is something to filter"*, which is exactly
   the right rule: the chip appears the first time something is shared with you.
2. **A quiet badge on a row you do not fully hold.** `Limited` on a dashboard
   whose tiles span a connection you cannot read; `Read-only` where you hold
   `select` but not `modify`. Rendered from `GET …/actions`, never from a role.
3. **The tile placeholder** (§19.2), naming the connection rather than saying
   *"error"*.
4. **"Why can I not see this?"** — an explain popover on any 403 surface and on
   any placeholder, rendering `Decision.because` as a sentence: *"You hold
   `describe` on this connection through the team **Finance**. Asking questions
   needs `select`. Ask **Sara** (owner)."* It also runs in reverse for
   administrators, from Access review.

**Access review** (`/admin/access`) is two lenses over the same data:

- **By principal** — *"what can Ali reach?"* — resource, privilege, and **the
  path**: `owner` · `direct grant` · `via team Finance` · `via role Knowledge
  Manager` · `wildcard`.
- **By resource** — *"who can reach this connection?"* — the same rows the other
  way round, and it is the same query.

Both export CSV. **Neither shows data**; both show reach.

### 21.7 The frontend plumbing

- `GET /auth/me` grows `kind`, `capabilities: string[]`, `roles: [{id,name}]`,
  `teams: [{id,name}]`. `MeResponse` is the one schema change the whole frontend
  hangs off.
- `useCan(capability)` and `useActions(resourceType, id)` are the two hooks. Every
  `user.role === 'ADMIN'` in the tree is replaced by the first; every disabled
  button and hidden menu item on a resource by the second.
- A `<Restricted reason={…}>` wrapper renders the explain popover, so the
  "why not" copy is written once.
- **No new page component is written from scratch.** `/admin` reuses the
  master–detail frame; People reuses `UsersPage`'s list; Share reuses `Modal`;
  the key panel reuses the one-time-password panel; the picker reuses
  `SearchField` + `Chip`.

### 21.8 The rule that governs all of it

> **UI restrictions are affordances. The server is the boundary.** Every hidden
> button has a server-side check behind it, and Phase 10's conformance test
> calls every mutating route as an unprivileged principal to prove it. A screen
> that hides a control it cannot also refuse is a bug, not a design choice.

## 22. The model mapped onto every existing DataMind surface

**This is the table a coding agent works from.** Every current route resolves to
exactly one row. `ctx` means *any authenticated principal*; a capability means
`needs(...)`; a `(type, privilege)` pair means `on(...)`; *"visible"* means the
list composes `authz.visible(...)`.

### 22.1 Chat, conversations and runs — `api/v1/conversations.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /conversations` | `owner_id == ctx` | **visible**(conversation, `select`) |
| `POST /conversations` | `ctx` | `conversation.create` **+** (connection, `select`) on the bound connection **+** (llm_config, `select`) on the chosen model |
| `PATCH/DELETE /conversations/{id}` | owner | (conversation, `modify` / `delete`) |
| `GET /conversations/{id}/messages` | owner | (conversation, `select`) |
| `POST /conversations/{id}/runs` | owner | (conversation, `modify`) **+** (connection, `select`) **+** (llm_config, `select`), **re-checked at execution** |
| `GET /runs/{id}`, `/sql`, `/events`, `/events/poll` | owner | (conversation, `select`) via the run's parent |
| `POST /runs/{id}/feedback` | owner | (conversation, `modify`) |
| `POST /runs/{id}/override` | owner | (knowledge, `modify`) on the run's connection — *teaching is curation* |
| `POST /runs/{id}/cancel`, `/chart` | owner | (conversation, `modify`) |
| `GET /artifacts/{id}` | owner | (conversation, `select`) via the artifact's run |

**Requirement 4's *"specific permissions for Chat"* is these three facts
together**: the capability to open a thread, `select` on each connection that may
be asked, and `select` on each model that may answer. None of them is a role
string.

### 22.2 Data sources — `api/v1/connections.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /connections` | `owner_id == ctx` | **visible**(connection, `describe`) |
| `POST /connections` | `ctx` | `connection.create` |
| `POST /connections/test` (unsaved) | `ctx` | `connection.create` |
| `GET /connections/{id}` | owner | (connection, `describe`) — the read model is **narrowed** at `describe`: name, engine, policy; no host, no schema |
| `PATCH /connections/{id}` | owner | (connection, `modify`) — **`disclosure_policy` removed from this payload** |
| `PUT /connections/{id}/disclosure` | — **new** | (connection, `manage`) + audit |
| `DELETE /connections/{id}` | owner | (connection, `delete`) |
| `POST /connections/{id}/test` | owner | (connection, `modify`) |
| `POST /connections/{id}/schema/sync` | owner | (connection, `modify`) |
| `GET /connections/{id}/schema` | owner | (connection, `select`) |
| `GET/POST/DELETE /connections/{id}/grants` | — **new** | (connection, `manage`) |
| `GET /connections/{id}/actions` | — **new** | (connection, `describe`) |
| `POST /connections/{id}/transfer` | — **new** | (connection, `manage`) |

### 22.3 Semantic layer — `api/v1/semantic.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /connections/{id}/semantic` | owner | (semantic_layer, `select`) |
| `PUT`, `DELETE` | owner | (semantic_layer, `modify` / `delete`) |
| `POST /check` | owner | (semantic_layer, `modify`) |
| `POST /generate`, `GET /jobs/*`, `POST /jobs/{id}/cancel` | owner | (semantic_layer, `modify`) **+** (connection, `select`) — generation reads the schema |

### 22.4 Knowledge — `api/v1/knowledge.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /templates`, `/health`, `/capabilities`, `/reviews`, `/suggestions`, `/benchmarks*` | owner via connection | (knowledge, `select`) |
| `POST /templates`, `PATCH`, `DELETE /templates/{id}` | `can_curate` | (knowledge, `modify`) |
| `POST /templates/check`, `/templates/revalidate` | `can_curate` | (knowledge, `modify`) |
| `PUT /embeddings` | `can_curate` | (knowledge, `manage`) — it re-indexes the whole store and costs provider calls |
| `POST /reviews/{id}/resolve` | `can_curate` | (knowledge, `modify`) |
| `POST /benchmarks`, `DELETE /benchmarks/{id}` | `can_curate` | `benchmark.manage` **+** (knowledge, `modify`) |
| `POST /benchmarks/{id}/run` | `can_curate` | `benchmark.manage` **+** (knowledge, `modify`) **+** (connection, `select`) — it executes SQL |

**`can_curate` is retired**, and the seven tests in
`test_audit_and_permissions.py` that pin it are **rewritten, not deleted**: the
rule *"a reader granted access to somebody's connection may ask it questions and
may not rewrite what it has been taught"* is preserved exactly, expressed as
`select` on the connection without `modify` on the knowledge. The
`curation_admin_only` setting is removed — the role model expresses it better.

### 22.5 LLM providers — `api/v1/llm_configs.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /llm-configs` | `owner_id == ctx` | **visible**(llm_config, `describe`) |
| `POST /llm-configs` | `ctx` | `llm_config.create` |
| `GET /parameters` | `ctx` | `ctx` — a static catalog |
| `POST /llm-configs/test` (unsaved) | `ctx` | `llm_config.create` |
| `PATCH /llm-configs/{id}` | owner | (llm_config, `modify`) ⚠️ **key-equivalent**: a change to `base_url` or `provider` clears the stored key and writes `llm_config.endpoint.changed` |
| `DELETE /llm-configs/{id}` | owner | (llm_config, `delete`) |
| `POST /llm-configs/{id}/test` | owner | (llm_config, `select`) |
| `GET/POST/DELETE /llm-configs/{id}/grants` | — **new** | (llm_config, `manage`), and the API **refuses** a grant above `select` |

**This fixes a first-run cliff as a side effect**: today a second user on a fresh
install sees no models and can ask nothing. After Phase 8 an administrator grants
`select` on the house model to the `All staff` team once.

### 22.6 Dashboards — `api/v1/dashboards.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /dashboards` | owner | **visible**(dashboard, `describe`) |
| `POST /dashboards`, `/import` | owner | `dashboard.create`; import additionally needs (connection, `select`) for every mapped connection |
| `GET /dashboards/{id}`, `/export` | owner | (dashboard, `select`) |
| `PATCH /dashboards/{id}`, `/layout`, tile create/update/delete/duplicate | owner | (dashboard, `modify`) **+** (connection, `select`) for the tile's connection |
| `DELETE /dashboards/{id}` | owner | (dashboard, `delete`) |
| `POST /dashboards/{id}/data`, `/tiles/{id}/data` | owner | (dashboard, `select`) **+ per tile** (connection, `select`) — **the intersection rule, at execution** |
| grants / actions / transfer | — **new** | (dashboard, `manage`) / `describe` / `manage` |

### 22.7 Reports — `api/v1/reports.py`

| Route | Today | Becomes |
|---|---|---|
| `GET /reports` | owner | **visible**(report, `describe`) |
| `POST /reports` | owner | `report.create` **+** (connection, `select`) |
| `GET /reports/{id}`, `/runs`, `/runs/{id}` | owner | (report, `select`) **+** (connection, `select`) for results |
| `PATCH`, `/outline`, block & section writes, `/blocks/{id}/sql`, `/check` | owner | (report, `modify`) **+** (connection, `select`) |
| `DELETE /reports/{id}` | owner | (report, `delete`) |
| `POST /reports/{id}/runs` | owner | (report, `modify`) **+** (connection, `select`) at execution |
| `POST /runs/{id}/cancel` | owner | (report, `modify`) |
| grants / actions / transfer | — **new** | (report, `manage`) / `describe` / `manage` |

### 22.8 Drafts, users, auth, audit

| Route | Today | Becomes |
|---|---|---|
| `POST /drafts`, `/drafts/validate` | owner of the connection | (connection, `select`) |
| `GET /users` | `AdminDep` | `needs(user.read)` |
| `POST /users`, `PATCH`, `PUT /password`, `DELETE` | `AdminDep` | `needs(user.manage)`; delete additionally refuses while the principal owns resources |
| `GET/POST/DELETE /users/{id}/roles`, `/teams` | — **new** | `needs(user.manage)` |
| `GET /service-users` … `POST /{id}/keys`, `DELETE /keys/{kid}` | — **new** | `needs(service_user.manage)` |
| `GET/POST/PATCH/DELETE /teams`, `/{id}/members` | — **new** | `needs(team.read / team.manage)`, or (team, `modify`) for a team lead |
| `PUT /teams/{id}/source` | — **new** | `needs(team.manage)`, separately audited |
| `GET/POST/PATCH/DELETE /roles` | — **new** | `needs(role.read / role.manage)`; system roles refuse capability edits |
| `GET /audit` | `AdminDep` | `needs(audit.read)` |
| `GET /access-review?principal=` / `?resource=` | — **new** | `needs(access.review)` |
| `GET /me/permissions` | — **new** | `ctx` |
| `POST /auth/login`, `/refresh`, `/logout` | — | unchanged, and **unreachable for `kind='SERVICE'`** |
| `GET/PATCH /auth/me`, `PUT /auth/me/password` | `ctx` | unchanged; the test that walks the route table to prove they take no user id stays |

### 22.9 Workers and background paths

| Worker | Today | Becomes |
|---|---|---|
| `workers/report.py`, `report_graph.py` | reconstructs identity from `connection.owner_id` | `RequestContext.on_behalf_of(report.owner_id)`, full checks, **failing the run** if the owner lost `select` |
| `workers/benchmark.py` | same | `on_behalf_of(benchmark_set.created_by)` |
| `workers/knowledge_maintenance.py` | same | `on_behalf_of(connection.owner_id)` |
| `workers/semantic.py` | job row carries `owner_id` | `on_behalf_of(job.owner_id)` |
| `workers/reconciler.py` | — | gains the **orphaned-grant sweep** |
| `services/bootstrap.py` | creates the admin user | additionally assigns the `Administrator` role; **the only place a role is assigned without an actor**, and it writes an audit row with `actor_user_id = NULL` |

---

# Part 4 — The eleven phases

Each phase states **what · why · depends on · backend · schema · frontend · docs
· tests · acceptance · not included**. **A phase is done when its acceptance
criteria are met**, not when the code is written.

**The gate for every phase** (decision 17 — there is no `make check`):

```bash
make lint && make test                    # ruff + import-linter + ~1,790 pytest
make authz-check                          # NEW in Phase 0 — the greps below
cd frontend && npm run typecheck && npm run build && npm test
```

`make authz-check` is a new Makefile target — `scripts/authz-check.sh`, wired
into CI beside the existing LiteLLM grep — that fails when any of these return a
hit outside the allowed paths:

```bash
grep -rnE "owner_id[[:space:]]*[!=]=" backend/app/api backend/app/services
grep -rn  "\.is_admin"  backend/app/api backend/app/services backend/app/workers
grep -rn  "role == ['\"]ADMIN" backend/app frontend/src
grep -rn  "ctx=None"    backend/app/workers
```

The first grep catches `!=` as well as `==`, because most decision sites are
spelled `if row.owner_id != owner_id`. One escape hatch, and it is deliberately
noisy: a line carrying `# authz-ok: <reason>` is exempt, and **every exemption is
printed at the end of every run**. It exists for the `unique (owner, name)`
predicate behind `_refuse_duplicate_name` — a constraint check about the row
being written, not an access decision — and there are three of them.

---

## Phase 0 — The vocabulary, the port, the switches · **S** · ~1 session

**What.** Every name this design uses exists in the tree, with tests, before
anything depends on it.

**Why.** The design has eight resource types, five privileges, eighteen
capabilities and one port. Introducing them alongside the first feature that
needs them is how a vocabulary ends up half-invented in three places. Nothing
here changes behaviour, so it can merge on its own and be reviewed as pure
addition.

**Depends on.** Nothing.

**Backend.**
- `app/domain/value_objects/authz.py` — `Privilege`, `ResourceType`,
  `Capability`, `PrincipalKind`, `_SATISFIED_BY`, `satisfying()`, and the
  per-type privilege table of §13.3 as data (`PRIVILEGE_MEANINGS`, used by the
  API to render `…/actions` and by the conformance test to prove every type has
  a row).
- `app/domain/ports/authz.py` — `ResourceRef`, `Decision`, `Visible`
  (`Everything | Subquery | Ids`), the `Authorizer` Protocol's four methods.
- `app/infra/authz/owner_only.py` — `OwnerOnlyAuthorizer`, returning **exactly**
  today's answers.
- `app/core/config.py` — `authz_backend: Literal["owner_only","rbac"] =
  "owner_only"`; `auth_provider: Literal["local"] = "local"` with a docstring
  recording §20.3 step 5; `allow_privileged_service_users: bool = False`;
  `service_key_default_ttl_days: int = 365`.
- `app/api/deps.py` — `get_authorizer` + `AuthzDep`, resolved exactly as
  `get_identity_provider` already is.
- `app/services/policy.py` — add `can(ctx, resource, privilege)` delegating to
  the authorizer. **`owns`, `can_curate` and their seven tests are untouched.**
- `Makefile` — the `authz-check` target.

**Schema.** None.

**Frontend.** None.

**Docs.** A one-paragraph pointer in `CLAUDE.md`'s *Adding things* section: *a
new API route decides its `ResourceType` and `Privilege` before it is written.*

**Tests.**
- The lattice is reflexive and transitive; `manage` satisfies all five;
  `describe` satisfies only itself upward.
- `satisfying()` returns a frozenset that callers cannot mutate.
- `OwnerOnlyAuthorizer` agrees with `owns()` on a table of cases including
  `owner_id` absent, `None`, and a foreign UUID.
- Every `ResourceType` has a `PRIVILEGE_MEANINGS` row for every `Privilege`.
- Import-linter still green: `app.domain` imports no `sqlalchemy`, no `fastapi`.

**Acceptance criteria.**
- [x] Gate green.
- [x] **Zero call sites changed and zero test assertions changed** — the diff is
      additive.
- [x] `make authz-check` runs and currently **fails** on the 213 known lines;
      it is added to CI as `continue-on-error` and flips to blocking in Phase 2.

**Not included.** No table, no migration, no endpoint, no UI, no `RbacAuthorizer`,
no roles, no teams, no service users. Nothing in `api/` or `services/` calls the
new port yet.

---

## Phase 1 — `ctx` everywhere, part A: dashboards and reports · **M** · ~2 sessions

**What.** The two files holding **133 of the 213** `owner_id` lines stop taking a
bare UUID and stop writing their own `WHERE owner_id`.

**Why.** This is *step zero* and it is behaviour-preserving: today's rule **is**
`owns`, so routing every call site through a function that returns `owns` changes
nothing except where the decision lives. Doing it before grants exist means the
grant change is a one-line swap of authorizer rather than a rewrite of two 1,000-
line services. Doing it *with* grants would make a large behavioural change and a
large mechanical change land in one reviewable diff, which is how sharing bugs
get shipped.

**Depends on.** Phase 0.

**Backend.**
- `DashboardService` — `list / get / create / update / delete / tile / add_tile /
  update_tile / delete_tile / duplicate_tile / set_layout / export /
  import_document / refresh / data` take `ctx: RequestContext` instead of
  `owner_id: UUID`.
- `_owned_connection` / `_owned_llm_config` become `_authorized_connection` /
  `_authorized_llm_config`, asking the authorizer.
- `ReportService` — the same treatment across its ~30 methods, `create_run`,
  `runs_of`, `run` and `cancel_run` included.
- Every `WHERE owner_id = :owner` in these two files becomes composition with
  `authz.visible(...)`.
- Routers `dashboards.py` and `reports.py` pass `ctx`, not `ctx.user_id`.

**Schema.** None.

**Frontend.** None.

**Docs.** None yet — `architecture.md` is corrected in Phase 2, once the claim is
fully false rather than half.

**Tests.** **No test assertion changes.** The existing dashboard and report suites
(`test_dashboard_service.py`, `test_dashboards_api.py`, `test_dashboard_cache.py`,
`test_dashboard_transfer.py`, the six integration report suites) are the proof
that behaviour is preserved. One new test asserts a list endpoint composes a
subquery rather than filtering in Python (assert on the emitted SQL).

**Acceptance criteria.**
- [x] Gate green with **no test assertion changed**.
- [x] `grep -n "owner_id" backend/app/services/dashboard_service.py
      backend/app/services/report_service.py` returns **only** model-construction
      sites — setting the owner on create — and no comparisons.
- [x] 404-on-not-yours, tile-cache keying, export format and every response shape
      are byte-identical.

**Not included.** The other ~80 `owner_id` lines. Workers. Any grant. Any UI.

---

## Phase 2 — `ctx` everywhere, part B: the rest, the workers, the gate · **M** · ~2 sessions

**What.** The remaining ~80 lines, and the grep gate goes blocking.

**Why.** When this ends, **`owner_id` is a fact stored on a row and nothing in
`api/` or `services/` reads it to make a decision.** That is the sentence
`policy.py`'s docstring has been claiming since migration `0001`, and it is what
makes every later phase a change in one module.

**Depends on.** Phase 1.

**Backend.**
- Services: `run_service.py` (20), `sql_draft_service.py` (12),
  `semantic_service.py` (8), `query_service.py` (7), `knowledge_service.py` (1).
- Routers: `conversations.py` (9), `llm_configs.py` (4), `connections.py` (4),
  `semantic.py` (2), `knowledge.py` (2), `drafts.py` (2).
- `RequestContext.on_behalf_of(user_id)` (§11.3) and its use in `workers/report.py`,
  `report_graph.py`, `benchmark.py`, `knowledge_maintenance.py`, `semantic.py`.
  **No `ctx=None` anywhere.**
- `make authz-check` becomes **blocking** in CI.

**Schema.** None.

**Frontend.** None.

**Docs.**
- `docs/architecture.md` §18, where it describes ownership as the enforcement
  mechanism.
- `docs/CODEBASE.md` §6, same.
- `docs/dashboards.md` §9 and `docs/reports.md` §14, which both say sharing is
  impossible — they now say *not yet*, with a pointer here.

**Tests.**
- `RequestContext` cannot be constructed without a principal id (a test that
  asserts the `TypeError`).
- Each worker path builds a context through `on_behalf_of` and the resulting
  audit row carries `delegated=True`.
- A test that walks `app.workers` and asserts no module constructs a context any
  other way.

**Acceptance criteria.**
- [ ] Gate green **including a blocking `make authz-check`**.
- [ ] Full suite unchanged.
- [ ] Four documentation files no longer describe ownership as the mechanism.

**Not included.** Roles, teams, service users, grants, UI. Behaviour is still
owner-only and the authorizer is still `OwnerOnlyAuthorizer`.

---

## Phase 3 — Roles and capabilities · **L** · ~3 sessions

**What.** `roles` becomes a table with eight seeded system roles carrying
capabilities and wildcard privileges; `is_admin` and `AdminDep` retire in favour
of `needs(capability)`; `/auth/me` learns to speak about capabilities.

**Why.** Requirement 2. It is also the phase that makes every later one cheaper:
after it, *"who may do this app-wide thing"* has one answer shape, and the
Administrator / specialised-role distinction the requirement asks for exists
before there is anything to specialise over.

**Depends on.** Phase 2.

**Backend.**
- `services/role_service.py` — create, update, delete (refusing while assigned),
  assign, unassign, list-for-principal, `resolve_capabilities(principal_id)`.
- `api/v1/roles.py` — CRUD, gated `role.read` / `role.manage`. A system role
  refuses capability edits with an explanation; its name and description are
  editable.
- `api/v1/users.py` — `GET/POST/DELETE /users/{id}/roles`.
- `api/deps.py` — `needs(capability)`. **`AdminDep` becomes an alias for
  `needs(Capability.USER_MANAGE)` and is deprecated in the same commit.**
- `RequestContext.capabilities`, resolved **once per request** in `get_ctx` by a
  single joined query over `role_assignments → roles → role_capabilities`,
  unioned across the principal's direct assignments (teams arrive in Phase 4).
- `ctx.is_admin` becomes a deprecated property computed from capabilities.
- `services/bootstrap.py` assigns `Administrator` to the bootstrap admin.
- `_guard_last_admin` → `_guard_last_administrator`, counting role assignments.
- `MeResponse` gains `kind`, `capabilities`, `roles`.
- Audit: `role.created/updated/deleted/assigned/unassigned`.

**Schema.** `0026_roles.py` — `roles`, `role_capabilities`,
`role_scoped_privileges`, `role_assignments` (§17.3), the eight-role seed, and
the backfill mapping `users.role` to an assignment. `users.role` **stays** as a
read-only cache written by `role_service`.

**Frontend.**
- `/users` → `/admin/people` redirect; the rail row becomes **Administration**
  and its gate becomes a capability set.
- `/admin` master–detail shell with **People** and **Roles** tabs (Teams, Service
  accounts, Access review and Audit arrive in later phases as tabs that appear
  when their capability is held).
- **Roles** tab: list, detail, capability checklist grouped as §12.1, scoped-
  privilege matrix, system-role badge.
- People detail gains a **Roles** section.
- `useCan()` replaces every `user.role === 'ADMIN'` in the tree.

**Docs.**
- `docs/security.md` §6 gains a paragraph on the role model and on decision 15
  (capabilities resolved per request, not carried in the token).
- `docs/frontend.md` §2's table gains `/admin`.
- This plan's ledger.

**Tests.**
- The eight seeds exist with exactly the capability sets of §12.3 (a table test —
  it is the specification).
- A system role refuses a capability edit; its name may be changed.
- Deleting an assigned role is refused and names the holders.
- The last-administrator guard counts assignments, and refuses through both the
  role route and the legacy `PATCH /users/{id}` path.
- A capability change takes effect on **the next request**, without a new token.
- Capability resolution is **one** query (assert on the statement count).
- Every route previously guarded by `AdminDep` is now guarded by a capability,
  and an unprivileged caller gets 403 (a route-table walk).

**Acceptance criteria.**
- [ ] Gate green.
- [ ] `grep -rn "is_admin" backend/app/api backend/app/services` returns only the
      deprecated property's definition.
- [ ] An administrator can create a custom role, tick capabilities, assign it,
      and the holder's `/auth/me` reflects it **without signing out**.
- [ ] A **DataMind Maintainer** can reach `/admin` and *not* the People tab; an
      **Auditor** can reach People and Audit and change nothing.

**Not included.** Teams (Phase 4). Service users (Phase 5). Any grant — the
scoped privileges are stored and **not yet read by any decision**, and a test
proves it.

---

## Phase 4 — Teams · **M** · ~2 sessions

**What.** A team exists, has members, may hold role assignments, and arrives on
every request context.

**Why.** Requirement 3, and the research's unanimous finding: *per-user grants do
not survive contact with staff turnover*. It ships before grants so that the
first grant anyone makes can already be made to a team.

**Depends on.** Phase 3.

**Backend.**
- `services/team_service.py` — create, rename, delete (refusing while it holds
  role assignments or grants), add/remove member, list members, list a
  principal's teams.
- `api/v1/teams.py` — CRUD gated on `team.read` / `team.manage`, **or**
  `(team, modify)` for a team lead. `PUT /teams/{id}/source` is a **separate,
  audited** endpoint, because rebinding redirects which external group's members
  flow into a set of permissions.
- `RequestContext.team_ids`, resolved **once per request** in `get_ctx` and in
  `on_behalf_of`; capability resolution widens to include roles reaching the
  principal through a team.
- `MeResponse` gains `teams`.
- Audit: `team.created/renamed/deleted/member.added/member.removed/source.bound`.

**Schema.** `0027_teams.py` — `teams`, `team_members` (§17.4).

**Frontend.**
- `/admin/teams` tab: list with member counts; detail with a members picker
  (kind-badged), the roles assigned to the team, and — after Phase 6 — the
  resources it holds.
- People detail gains a **Teams** section.

**Docs.** `docs/security.md`; this plan's ledger; a note in `CLAUDE.md` that a
team is the recommended default principal for any grant.

**Tests.**
- Membership resolution, including a principal in three teams.
- Cascade on user delete and on team delete.
- The `ck_teams_source_pair` constraint.
- `team_ids` present on a context built **both** ways.
- A role assigned to a team reaches its members' capabilities, and **stops**
  reaching them the moment they leave — on the next request.
- Deleting a team holding a role assignment is refused and names it.

**Acceptance criteria.**
- [ ] Gate green.
- [ ] An administrator can create a team, add members, assign it the **BI
      Engineer** role, and every member gains `dashboard.create` without signing
      out.
- [ ] `ctx.team_ids` is populated and **used by no resource decision yet** — a
      grep proves it.

**Not included.** Grants (Phase 6). Nested teams. External binding is a column
and an endpoint; nothing reads `provider_id` yet.

---

## Phase 5 — Service users · **M** · ~2–3 sessions

**What.** A machine identity with its own row, its own keys, its own roles and
teams, and a second authenticator.

**Why.** Requirement 1. It ships **before** grants for two reasons: the second
authenticator proves the authentication seam (S7) with real code rather than a
comment, and an agent that will later be granted access should already exist as a
principal so its first grant is an ordinary grant.

**Depends on.** Phase 3 (roles). Phase 4 is a soft dependency — service users can
join teams the moment both exist.

**Backend.**
- `domain/ports/identity.py` — `ServiceIdentityProvider` Protocol
  (`verify_key`, `issue_key`, `revoke_key`), the sibling of `IdentityProvider`.
- `infra/identity/service_key.py` — key generation (`dm_sk_<prefix>_<secret>`),
  SHA-256 verification with a constant-time compare, expiry and revocation
  checks, throttled `last_used_at`.
- `api/deps.py` — `get_ctx` dispatches on the bearer token's shape: a `dm_sk_`
  prefix goes to the service authenticator, anything else to the JWT path.
  **One `RequestContext` comes out either way**, and nothing downstream can tell
  which — which is the seam.
- `services/service_user_service.py` — create, disable, delete, issue key, revoke
  key, list keys; refuses privileged capabilities unless
  `allow_privileged_service_users`.
- `api/v1/service_users.py` — gated `service_user.manage`.
- `/auth/login`, `/auth/refresh`, `PATCH /auth/me`, `PUT /auth/me/password`
  **refuse** a `SERVICE` principal.
- Audit: `service_user.created/disabled/deleted`,
  `service_credential.issued/revoked/expired`.

**Schema.** `0024_principal_kind.py` (§17.1) and `0025_service_credentials.py`
(§17.2).

**Frontend.**
- `/admin/service-accounts` tab: list, create form (name, description, roles,
  teams, with the effective permission shown before Save), detail with a Keys
  panel, one-time key display reusing the existing one-time-password panel,
  expiry and `last_used_at` columns, revoke with confirm.
- Kind badges wherever a principal is listed — People, Teams, the future
  principal picker.

**Docs.**
- **`docs/security.md` §6 gains a subsection**: what a service key is, why
  SHA-256 rather than Argon2id, the prefix's purpose, the expiry default, and
  the rule that a machine may not mint administrators.
- `docs/security.md` §7's deployment checklist gains *"review service accounts
  and their keys"*.
- `README.md` gains a short *Programmatic access* section.

**Tests.**
- A valid key authenticates; a revoked, expired, unknown-prefix or
  wrong-secret key does not, and each writes the right audit outcome.
- Constant-time comparison is used (assert the helper, not the timing).
- `last_used_at` is throttled — two calls in the same minute write once.
- A `SERVICE` principal is refused by `/auth/login` and by every `/auth/me`
  route.
- The three `CHECK` constraints reject a service user with a password, an
  external subject, or `must_change_password`.
- A service user with the **BI Engineer** role has byte-identical capabilities to
  a human with it.
- Privileged capabilities are refused while the flag is off, allowed and audited
  while it is on.
- `test_openapi_has_no_secrets.py` extends to `token_hash` and to the key itself.

**Acceptance criteria.**
- [ ] Gate green.
- [ ] `curl -H "Authorization: Bearer dm_sk_…" /api/v1/dashboards` returns the
      same body a human in the same team would get.
- [ ] The key is displayed exactly once and is not recoverable from any endpoint.
- [ ] Revoking a key fails the **next** request.

**Not included.** OAuth2 client credentials. Per-key `scopes` — the column exists
and is unread. IP allow-lists. mTLS. Rate limits per key (named in §24).

## Phase 6 — Grants on connections, knowledge and the semantic layer · **L** · ~3 sessions · ⚠️ **the blocking item**

**What.** *"Who may read through this connection, and who may curate what it has
been taught"* gets an explicit, auditable, revocable answer. The `RbacAuthorizer`
lands and becomes the default.

**Why.** This is mvp2 **D1**, the phase every other sharing phase depends on, and
the one that changes what the product is. It comes first among the grant phases
because **the semantic layer, the knowledge store and the benchmarks already
follow the connection**, so connection grants alone bring a large amount of team
behaviour at no extra modelling cost.

**Depends on.** Phases 2, 3, 4, 5.

**Backend.**
- `app/infra/authz/rbac.py` — `RbacAuthorizer`: `allowed`, `allowed_many`,
  `visible`, `privileges_on`. Wildcard short-circuit first (§18.3 step 0), then
  the union subquery.
- `services/grant_service.py` — grant, revoke, list-for-resource,
  list-for-principal. `manage` required for all four. **Self-revocation of the
  last `manage` is refused**, mirroring `_guard_last_administrator`. A wildcard
  grant additionally requires `role.manage`.
- `api/deps.py` — `on(type, privilege, param)` (§18.4a).
- `api/v1/connections.py` — `GET/POST/DELETE /connections/{id}/grants`,
  `GET /connections/{id}/actions`, `POST /connections/{id}/transfer`.
- Same three on the derived types: `/connections/{id}/knowledge/grants`,
  `/connections/{id}/semantic/grants`.
- **The disclosure split (§19.5):** `disclosure_policy` moves out of `PATCH
  /connections/{id}` into `PUT /connections/{id}/disclosure`, gated on `manage`,
  audited. `describe` exposes the policy; the `ConnectionRead` model is narrowed
  at `describe` so a `describe` holder sees no host and no credentials.
- **`can_curate` is retired** and every knowledge route moves to the
  `(knowledge, …)` privileges of §22.4. `curation_admin_only` is removed from
  config.
- **The 404/403 rule (§19.1)** implemented once, in an exception helper.
- `workers/reconciler.py` — the orphaned-grant sweep.
- `DELETE /users/{id}` refuses while the principal owns a grantable resource.
- Flip `authz_backend` default to `"rbac"`; `"owner_only"` keeps working for one
  release.
- Audit: `grant.created/revoked`, `grant.wildcard.created`,
  `ownership.transferred`, `disclosure.changed`.

**Schema.** `0028_grants.py` (§17.5), including the wildcard partial index.

**Frontend.**
- `<AccessPanel>` — the one shared component (§21.4): principal picker (users,
  service accounts, teams, kind-badged, with the team hint), privilege radio
  driven by `…/actions`, current-access list showing **the path** (`owner` ·
  `direct` · `via team X` · `via role Y`), revoke with confirm.
- **Data sources gains a fifth tab, `/sources/:id/access`**, between Policy and
  Schema.
- The Policy tab's disclosure control moves behind `manage` and says so when the
  viewer holds only `modify`.
- Knowledge console header and Semantic tab header gain their Access popovers.
- Data sources list renders connections reached by grant, with an owner column.

**Docs.**
- `docs/security.md` §3 gains **the sharing interaction**: one person's
  disclosure choice governing another person's questions, and the three rules.
- `docs/security.md` §6 gains ownership transfer and the deletion refusal.
- `docs/architecture.md` §18 rewritten around the authorizer.
- `CLAUDE.md` gains invariant 5 (§16) and a pointer to the future rulebook.
- This plan's ledger.

**Tests.**
- Lattice implication end to end: a `modify` holder passes a `select` check.
- A team grant reaches a member and stops when they leave.
- A wildcard grant is refused without `role.manage`.
- Revoke; last-`manage` self-revocation refused.
- The disclosure gate: a `modify` holder cannot widen `NONE` → `FULL`; a `manage`
  holder can, and it is audited.
- Ownership transfer; `DELETE /users/{id}` refusal naming the owned resources.
- The sweep removes a grant whose resource is gone and nothing else.
- **The seven `can_curate` tests are rewritten, not deleted**, and prove the same
  rule: a principal with `select` on a connection may ask and may not curate.
- **A Knowledge Manager may curate `conn-A`'s knowledge and may not read
  `conn-A`'s data, edit its credentials, or change its disclosure policy.** This
  is requirement 2's acceptance test and it must be a named test.
- 404 with no privilege; 403 with `describe`; the message names the privilege.

**Acceptance criteria.**
- [ ] Gate green.
- [ ] Two users, one connection, one grant: user B can ask a question through
      user A's connection, **cannot** edit its knowledge, **cannot** widen its
      disclosure policy, and **cannot** see its host or password.
- [ ] Every grant, revoke, transfer and disclosure change appears in
      `GET /audit`.
- [ ] Setting `authz_backend=owner_only` restores the previous behaviour exactly.

**Not included.** Dashboards, reports, LLM configs, conversations (Phase 8).
Denial auditing (Phase 7). Access review (Phase 9).

---

## Phase 7 — The audit half · **S–M** · ~1–2 sessions

**What.** Every authorization event becomes a row, including the denials.

**Why.** [`audit.py`](../backend/app/services/audit.py) has had a `DENIED`
constant and **no producer** since migration `0001`. A product whose positioning
is *"you decide what leaves your database"* has to be able to answer *"who tried,
and was refused"*. It lands immediately after the first grants so the record
starts with the first share rather than after the first incident.

**Depends on.** Phase 6.

**Backend.**
- `audit.record(outcome=DENIED, …)` fires on every 403, carrying
  `Decision.because`. **Never on a 404** (§19.1) — a 404 is indistinguishable
  from a typo and auditing it makes the log noise.
- The remaining actions of §19.4.
- **Administrator escalation (decision 14):** an administrator may grant
  themselves any privilege on any resource; it is an ordinary grant row **plus**
  an `admin.self_granted` row. There is no silent read path anywhere.
- The ask path records the **disclosure policy in force** — the remaining half of
  mvp2 D4.
- `GET /audit` gains filters for `outcome`, `resource_type`, `actor` and a date
  range, and pagination.

**Schema.** None. `audit_logs` already has every column needed, and its two
indexes (`actor_user_id, at` and `action, at`) already serve the filters.

**Frontend.** `/admin/audit` — the first UI for the audit log. Denials rendered
distinctly (tone, not a separate list — a denial beside the grant that preceded
it is the story). Actor shown as a display name and **never** an address, the
rule the review queue already follows.

**Docs.** `docs/security.md` §4.8 extends to authorization events; its sentence
*"this is not the whole of mvp2 §D4"* becomes true in a smaller way.

**Tests.**
- A denial writes exactly **one** row with a non-empty `because`.
- A 404 writes **none**.
- `detail` carries no SQL, no question text, no rows, no key — asserted, not
  reviewed.
- An administrator self-grant writes two rows.
- An ask records the policy in force.
- A failing audit write does not fail the action (rule 2 of that module).

**Acceptance criteria.**
- [ ] Gate green.
- [ ] A denied request produces a row naming the privilege that was missing and
      the path that was tried.
- [ ] An administrator reading `/admin/audit` can reconstruct *who granted what
      to whom, when, and what was refused* without opening a database client.

**Not included.** Alerting on denials. Retention or archival (§24). Exporting the
audit log.

---

## Phase 8 — Grants on artifacts: reports, dashboards, LLM configs, conversations · **L** · ~3 sessions

**What.** Read-only sharing, in the order **reports → LLM configs → conversations
→ dashboards**.

**Why.** mvp2 **D2**. The order is deliberate and is the opposite of the
intuitive one: a report's `connection_id` is single and immutable so there is no
intersection to resolve; a dashboard tile carries its **own** `connection_id`, so
one dashboard may span several connections and *"share this dashboard"* has no
well-defined meaning until the intersection rule is implemented. **Dashboards are
the hard case and go last.**

**Depends on.** Phases 6, 7.

**Backend.**

*8a — Reports.* Grants on `report`; `visible` in the list; the viewer needs
`select` on the report **and** on its connection; a missing second gives the
tile-level message of §19.1, not a 500.

*8b — LLM configs.* Grants on `llm_config`, **`select` and `describe` only** —
the API refuses anything higher (§13.3 ⚠️). `PATCH` clears the stored key when
`base_url` or `provider` changes and writes `llm_config.endpoint.changed`.
`resolve_llm` on the ask path re-checks `select` at execution.

*8c — Conversations.* Grants on `conversation`. Sharing a thread shares the
transcript and the artifacts; it does **not** share the connection, so a shared
thread whose connection the viewer cannot read renders its results as
placeholders exactly as a dashboard does.

*8d — Dashboards.* Grants on `dashboard`. **The intersection rule, implemented
and documented:** the dashboard renders; each tile renders **iff** the viewer
holds `select` on that tile's connection; a tile they cannot see renders as a
**named placeholder**, never hidden. `refresh` re-checks **per tile, per
execution**. Sharing **warns** when the tiles span connections the grantee cannot
read, naming them; the share is still allowed.

**Schema.** None — `0028` already carries every type.

**Frontend.**
- **Share** dialog on the Dashboards and Reports index cards' kebab and on both
  detail headers, rendering the same `<AccessPanel>`.
- The cross-connection warning in the dashboard Share dialog, naming the
  connections.
- The tile placeholder, and the equivalent for a report block and a chat turn.
- **Access** section on the LLM provider detail form, `select`-only, with the
  reason on the panel.
- Conversation share from the thread kebab.
- **"Shared with me"** filter chip on both index toolbars.
- `Limited` / `Read-only` badges driven by `…/actions`.

**Docs.** `docs/dashboards.md` §9 and `docs/reports.md` §14 rewritten — they
currently say sharing is impossible. `docs/frontend.md` §2's sub-section map
gains the Access tab and the Share dialogs.

**Tests.**
- A two-connection dashboard shared with a principal granted one of them renders
  **one tile and one placeholder**.
- The share-time warning names the unreadable connections.
- **The tile-cache invariant:** the trigger sentence goes into
  `DashboardTileCache`'s docstring and a test asserts the cache key contains no
  viewer. That test is the tripwire for anyone adding a viewer-dependent filter.
- A report viewer with `select` on the report and not on its connection sees a
  placeholder, not a 500.
- An `llm_config` grant above `select` is refused by the API.
- Changing `base_url` clears the key.
- A shared conversation does not grant its connection.

**Acceptance criteria.**
- [ ] Gate green.
- [ ] **The one-line acceptance test:** *two people, one database credential, one
      dashboard — the second can see the numbers, cannot see the password, cannot
      change what the connection has been taught, cannot widen what leaves for
      the model provider, and every one of those four facts is a row in
      `audit_logs`.*
- [ ] A second user on a fresh install can be given the house LLM config and a
      connection, and asks their first question without an administrator sharing
      a password.

**Not included.** Public or anonymous share links (§23 — there is no anonymous
principal and adding one is a product decision). Comments and annotations. A
*certified* badge. Row-level security.

---

## Phase 9 — Access review and the permission explainer · **M** · ~2 sessions

**What.** *"What can Ali reach?"* and *"who can reach this?"* become screens, and
every refusal can explain itself.

**Why.** Requirement 6's *"the UI should clearly communicate what a user can and
cannot access"*, and the gap every product in §9 leaves. It is cheap here and
expensive later: `Decision.because` already carries the path, and building the
explainer after the surfaces have each invented their own error copy means
rewriting eight of them.

**Depends on.** Phase 8.

**Backend.**
- `services/access_review_service.py` — one query with two orderings.
  `by_principal(id)` returns `(resource_type, resource_id, name, privilege,
  path)`; `by_resource(type, id)` returns `(principal, kind, privilege, path)`.
  `path` is `owner` · `direct` · `team:<name>` · `role:<name>` · `wildcard`.
- `api/v1/access_review.py` — gated `access.review`, with CSV.
- `GET /me/permissions` (§18.6).
- Every 403 response body carries a structured `reason` — the privilege needed,
  what the caller holds, and through what — built from `Decision.because`.

**Schema.** None.

**Frontend.**
- `/admin/access` — the two lenses, a principal/resource switcher, filters by
  type and privilege, CSV export.
- `<Restricted>` and the **"Why can I not see this?"** popover, used by the tile
  placeholder, the report block placeholder, the chat turn placeholder and every
  403 surface.
- **Effective access** section in People, Service accounts and Teams details,
  reusing the by-principal lens.

**Docs.** `docs/frontend.md` §2 and the sub-section map; a short *How do I find
out why someone cannot see something?* entry in `docs/README.md`'s
by-what-you-are-touching table.

**Tests.**
- The two lenses agree: every row in `by_principal(P)` for resource R appears in
  `by_resource(R)` for P, and vice versa. A property test over generated
  fixtures.
- `path` is correct for each of the five facts of §15.2.
- The review shows **reach and never data** — no name, row or value from a
  customer database appears in any response.
- CSV escaping is RFC 4180 with the leading-apostrophe rule the existing
  `ResultTable` download already applies.

**Acceptance criteria.**
- [ ] Gate green.
- [ ] An administrator can answer *"why can Reza curate this?"* in one click and
      the answer names the role.
- [ ] A user who cannot see a tile is told which connection and who owns it.

**Not included.** Approval workflows or access requests. Scheduled review
campaigns. Notifications.

---

## Phase 10 — The rulebook, the conformance check, the seams · **M** · ~2 sessions

**What.** Make the rules **checkable by the next person**, who will be a coding
agent with none of this context.

**Why.** A rule nothing enforces is a comment. This repository already learned
that twice — `policy.py`'s untrue docstring, and the `prompt_version` that lied
for five weeks — and both are why this phase exists rather than being assumed.

**Depends on.** Everything.

**Backend / repo.**
- **Write [`docs/access-control-rules.md`](access-control-rules.md)** — §26's
  deliverable. Not a summary of this plan: a short, imperative rulebook read
  *before* writing an endpoint.
- Pointers to it from `CLAUDE.md`, `docs/README.md`, and the module docstring of
  `services/policy.py`.
- `tests/unit/test_authz_conformance.py`:
  - every route carries a `ctx` and is reachable only through a dependency that
    produces one;
  - every `ResourceType` has a `PRIVILEGE_MEANINGS` row for every `Privilege`;
  - every list endpoint whose model has an `owner_id` composes `visible(...)`;
  - no `owner_id ==`, no `.is_admin`, no `role == "ADMIN"` in `api/`, `services/`
    or `frontend/src` (the Phase 2 greps, promoted to a test so they fail
    locally, not only in CI);
  - **I5:** every mutating route, called as an unprivileged principal, returns
    403 or 404 — a route-table walk, not a hand-written list;
  - **I4:** a property test that adding any grant, role assignment or team
    membership never turns an `allowed` into a denial.
- **Seam tests (§20.2):** `external_subject` round-trips a namespaced
  `provider~subject`; an unknown external group is **ignored, not created**;
  `ctx.team_ids` and `ctx.capabilities` each have exactly **one** resolution
  site; a JWT carrying a `capabilities` claim is ignored.
- **A synthetic OIDC test** (§20.3): RS256 keypair in a fixture, static JWKS,
  tokens minted with `pyjwt`, asserting expiry, wrong audience, wrong issuer,
  unknown `kid`, group mapping and role mapping — **without a Keycloak**.
- Delete `ctx.is_admin`, `AdminDep` and `users.role`.
- Remove `authz_backend="owner_only"` from the default path (keep the class, one
  release later).

**Schema.** `0029_drop_users_role.py`.

**Frontend.** A `frontend/src/api/permissions.ts` module documenting the two
hooks, and a lint-style test that no component imports `user.role`.

**Docs.**
- `docs/access-control-rules.md` (new).
- `docs/README.md`: this plan moves from *Proposed* to *Live*, and the row for
  `access-control-plan.md` records that it is superseded by this document.
- `docs/architecture.md`, `docs/security.md`, `docs/frontend.md`,
  `docs/CODEBASE.md`, `CLAUDE.md` — final pass.
- The ledger in §27 filled in.

**Tests.** The conformance module is itself the deliverable; plus a test that
`docs/access-control-rules.md` exists and that every `ResourceType` and
`Capability` is named in it (a cheap doc-drift guard, the same trick the prompt
version test uses).

**Acceptance criteria.**
- [ ] Gate green.
- [ ] `docs/access-control-rules.md` exists and the conformance test enforces
      every rule it states that is mechanically checkable.
- [ ] `grep -rn "is_admin\|AdminDep\|users.role" backend/app` returns nothing.
- [ ] The OIDC seam tests pass with **no Keycloak in CI**.

**Not included.** The OIDC adapter itself. Row-level security. Workspaces.

---

# Part 5 — Extensibility, risks and open questions

## 23. How the next ten features land on this model

The test of this design is not what it does today; it is what the next features
cost.

| Feature | What it needs | Cost |
|---|---|:--:|
| **File upload (CSV/Excel)** | an uploaded file becomes an ordinary connection | **zero** — grants, disclosure, guard and knowledge all apply unchanged. The strongest evidence the model is right |
| **Result export** | `select` on the artifact **and** its connection | **zero** — it is a read, checked like any read |
| **Pin a chat answer to a dashboard** | `modify` on the dashboard, `select` on the connection | **zero** — two existing checks |
| **Scheduled reports** | a background actor with real privileges | **small** — `on_behalf_of` exists; add the schedule |
| **Metric alerts** | the same, plus *"who may receive an alert about data they cannot read"* | **small** — the delivery check is `select` on the connection at send time |
| **An MCP server / agent API** | a machine principal | **zero** — that is Phase 5 |
| **A new resource type** (e.g. `notebook`) | one enum member, one `PRIVILEGE_MEANINGS` row, one `visible` arm, one sweep entry, one conformance row | **small, and the checklist is written** (§26) |
| **A new specialised role** | one `roles` row and its capability/scoped-privilege rows | **zero code** |
| **Row-level security** | filters attached to **roles**, applied as predicates in generated SQL — Superset's shape | **medium, and the model is already shaped for it.** Needs the tile-cache key to grow a viewer |
| **Workspaces / folders** | a container that owns resources | **large but additive** — `resource_type='workspace'`, a `workspace_id` on each resource, one `OR` in the subquery. Existing grants keep working |
| **OIDC / Keycloak** | the eight seams of §20 | **medium and contained** — no grant change, no schema change |

**Two things the model deliberately makes expensive**, because they should be:

- **Public / anonymous share links.** There is no anonymous principal, and adding
  one means deciding what *"the connection's grant"* means with no viewer. That
  is a product decision, and the model refusing to guess is correct.
- **Per-column masking.** It is `deny` in disguise (I4). It belongs with row-level
  security or not at all.

## 24. The triggers — what would force a change, and what the change is

A deferral without a trigger is an omission. Each row is a sentence someone will
one day say, paired with what to do when they say it.

| Trigger — *someone says…* | Deferred thing | The change |
|---|---|---|
| *"a tile's rows should depend on who is looking"* | per-viewer tile cache | `dashboard_tile_cache`'s PK becomes `(tile_id, viewer_key)`. **This is Phase 8's tripwire test** |
| *"I need to share a folder"*, or one principal holds >50 grants | workspaces | migrate grants into a container; additive, and one-way |
| *"our IdP nests groups and we need that"* | nested teams | `team_members.member_team_id` + one `WITH RECURSIVE` in the subquery |
| *"we need SSO"* | the OIDC adapter | §20.3, seven steps, **no schema change** |
| *"a contractor needs access for two weeks"* | `grants.expires_at` | one column, one sweeper, one decision about mid-render expiry |
| *"a team lead should be able to share what they were shared"* | delegated granting | **read Lakekeeper's v4.10 changelog first** — they shipped it and took half of it back |
| *"this key should only be able to read dashboards"* | per-key `scopes` | the column exists; intersect it with the principal's permissions at context construction |
| *"row-level security"* | RLS | needs dashboard parameters, the connector port carrying a per-request identity, **and** the cache trigger above |
| *"we need to keep audit rows for seven years"* | audit retention | a partition by month and an archival job; the table is already append-only |
| *"one key is hammering the API"* | per-key rate limits | `service_credentials` is the natural key; nothing else changes |
| *"we have two IdPs"* | an `identities` table | one user, many `(provider_id, subject)` rows; `external_subject` is already namespaced |

## 25. Risks, and what each one costs

| Risk | Likelihood | What it costs | Mitigation in this plan |
|---|:--:|---|---|
| **The Phase 1–2 refactor breaks something subtle** — 213 lines across 18 files | High | a regression in dashboards or reports, the two biggest services | Behaviour-preserving by construction; **no test assertion may change**; the existing ~1,790-test suite is the proof; two phases, not one |
| **One query per request for capabilities and teams** (decision 15) | Certain | latency on every authenticated call | One joined query, indexed. Measure in Phase 3 and record the number. If it exceeds ~2 ms, memoize per `(principal, updated_at)` — a cache, not a claim in a token |
| **The wildcard grant is a footgun** | Medium | an accidental installation-wide grant | It needs `role.manage`, it is a distinct audit action, and Access review shows `wildcard` as its own path |
| **A shared connection widens disclosure without consent** | Medium | one person's policy choice governs forty people's questions | `describe` exposes the policy; only `manage` may widen it; every ask records the policy in force. The consent question stays open (§26) |
| **`modify` on an `llm_config` leaks the key** | High, if unaddressed | provider key exfiltration | §13.3 ⚠️ — not grantable, key cleared on endpoint change, audited, tested |
| **A leaked service key** | Medium | whatever that identity holds | Least privilege by construction, expiry by default, `prefix` for tracing, `last_used_at` for pruning, no privileged capabilities |
| **Migration ordering across six migrations in five phases** | Medium | a broken upgrade | Each phase ships one or two migrations and its own rollback note; `authz_backend` is the behavioural rollback and needs no down-migration |
| **The UI hides a control the server would allow, or vice versa** | Medium | a confusing product, or a hole | Every affordance reads `…/actions`; I5's route-table walk is the check |
| **Roles proliferate** | Medium | an unreviewable model | Eight seeds cover the requirement's list; Access review makes drift visible; custom roles are a deliberate act |

## 26. Genuinely open questions

Not deferrals — things this plan cannot settle.

1. **Consent to a team grant.** Granting `select` to a team of forty makes a
   disclosure decision on behalf of forty people. None of Metabase, Superset,
   Grafana or Power BI models this. Do the forty get told?
2. **Whether a workspace and a tenant can be kept apart.** Every product in §9
   eventually grew a second, coarser boundary — Metabase's tenancy, Grafana's
   organisations, Power BI's capacities. Whether DataMind can ship a container
   without becoming a multi-tenancy project is not answerable from a document.
3. **"Revoked means revoked now."** Authorization is immediate (decision 15), but
   *authentication* is not: a disabled user's access token stays valid for up to
   15 minutes. Making that immediate is a per-request user lookup — a real cost,
   deliberately not paid. Is ≤15 minutes acceptable to the first customer who
   asks?
4. **Whether `describe` is a privilege anyone actually grants**, or only an
   internal step in the 404/403 rule. If the latter, the share UI should not
   offer it. Phase 9's access review will show the answer in real data.
5. **Grant count at which the subquery stops being free.** The index makes it
   cheap into five figures; nobody has measured it against a realistic graph.
6. **Whether a service user should be able to own resources at all**, or whether
   an agent's dashboards should be owned by the human who configured it. This
   plan says yes (it is the simpler model and it keeps `owner_id` honest), but
   the first team whose agent leaves an orphaned dashboard behind may disagree.
7. **How a role's capability set should change on upgrade.** Adding a capability
   to a seeded role in a migration is a silent widening for every install.
   Adding it to *nothing* means a new feature is unreachable until someone
   notices. This plan chooses the migration and audits it; a release note is not
   a mechanism.

## 27. The rulebook — what Phase 10 must produce

**`docs/access-control-rules.md`** is a deliverable, not documentation as an
afterthought. Its audience is whoever writes the *next* feature, most likely with
no memory of this plan. It must be short enough to read before writing an
endpoint.

Required contents:

1. **The seven concepts on one page** (§10) with the lattice diagram and the
   capability-versus-privilege distinction.
2. **The five invariants** (§16), stated as rules with their consequence.
3. **The effective-permission algorithm** (§15), verbatim, because it is the
   thing people get wrong.
4. **A checklist for any new endpoint** — the part people will actually use:
   - Which `ResourceType` does this touch? If none, is it a **capability**?
   - Which `Privilege`? Use the lattice; never check two.
   - Detail or list? Detail calls `allowed` (or `on(...)`); list composes
     `visible`. **Never a loop over `allowed`.**
   - Does it execute against a connection? Then it re-checks `select` on **that**
     connection, at execution.
   - Does it change a disclosure policy, an owner, a grant, a role or a
     membership? Then it is `manage` (or a capability) and it is audited.
   - Does a caller without permission see 404 or 403? Apply §19.1.
   - Does its result depend on **who is looking**? If yes, stop — that trips the
     cache trigger (§24) and needs a design conversation.
5. **A checklist for any new resource type** — the enum member, the
   `PRIVILEGE_MEANINGS` row, the `visible` subquery arm, the sweep entry, the
   `…/actions` route, the conformance-test row, the `<AccessPanel>` mount point.
6. **A checklist for any new capability** — the enum member, which seed roles get
   it and why, the migration that adds it to them, the audit action if it guards
   a mutation.
7. **What is not in the model and must not be improvised** — no `deny`, no
   priority order, no per-endpoint role string, no `ctx=None`, no god context, no
   permission keyed on an email or an external subject, no capability read out of
   a token, no role naming one resource id.
8. **How this is enforced** — a pointer to `tests/unit/test_authz_conformance.py`
   and `make authz-check`, so a reader knows which rules a machine checks and
   which rely on them.

---

## 28. Sources

**Repository documents read for this plan** — [research/access-control.md](research/access-control.md) ·
[access-control-plan.md](access-control-plan.md) · [architecture.md](architecture.md) ·
[security.md](security.md) · [frontend.md](frontend.md) · [CODEBASE.md](CODEBASE.md) ·
[mvp2-plan.md](mvp2-plan.md) · [learning-loop-plan.md](learning-loop-plan.md) ·
[dashboards.md](dashboards.md) · [reports.md](reports.md) · [README.md](README.md) ·
[../CLAUDE.md](../CLAUDE.md).

**External sources** — read on 2026-09-06.

- Power BI / Fabric — [Roles in workspaces](https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-roles-new-workspaces) (the capability matrix, the highest-permission rule, the Member delegation limit, the disabled-identity note, service principals in workspace roles); [Roles in workspaces in Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces).
- Apache Superset — [STANDARD_ROLES.md](https://github.com/apache/superset/blob/master/RESOURCES/STANDARD_ROLES.md) (Admin / Alpha / Gamma / sql_lab / Public, and composing custom roles); [Security configuration](https://superset.apache.org/admin-docs/security/) (`DASHBOARD_RBAC` bypassing dataset checks, RLS attached to roles); issues [#18959](https://github.com/apache/superset/issues/18959) and [#22640](https://github.com/apache/superset/issues/22640) (native filters, and a DRAFT dashboard reachable with no role assigned).
- Metabase — [Permissions introduction](https://www.metabase.com/docs/latest/permissions/introduction) (groups only, most-permissive resolution, the All Users group); [Data permissions](https://www.metabase.com/docs/latest/permissions/data) (View data / Create queries, and Blocked being overridden by a more permissive group); [Collection permissions](https://www.metabase.com/docs/latest/permissions/collections); [API keys](https://www.metabase.com/docs/latest/people-and-groups/api-keys) (assigned to a group, shown once, and the reassign-to-All-Users fallback).
- Grafana — [Service accounts](https://grafana.com/docs/grafana/latest/administration/service-accounts/) (tokens inherit the account's permissions; a service account cannot join a team; expiry); [RBAC](https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/) and [basic and fixed role definitions](https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/rbac-fixed-basic-role-definitions/) (action-and-scope permissions, wildcard scopes, `/api/access-control/user/permissions`).
- Looker — [Access control and permission management](https://docs.cloud.google.com/looker/docs/access-control-and-permission-management) (a role is a permission set **and** a model set; content access managed separately from feature access).
- Tableau — [Effective permissions](https://help.tableau.com/current/server/en-us/permission_effective.htm) and [Permission capabilities and templates](https://help.tableau.com/current/online/en-us/permissions_capabilities.htm) (Allow / Deny / Unspecified, user-then-group precedence, deny wins, locked projects).
- Machine identity — [Best practices for managing service account keys](https://docs.cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys) and [Best practices for managing API keys](https://docs.cloud.google.com/docs/authentication/api-keys-best-practices) (rotation, expiry, least privilege, storage).
- [OWASP API1:2023 — Broken Object Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/) (every endpoint receiving an object id must check object-level authorization; centralise the mechanism).
- Keycloak — identity-provider mappers and group-membership claims, for §20.3's group and role mapping.

---

# Part 6 — Master implementation checklist

**How to use this.** Work top to bottom. `[x]` marks work completed during this
planning and investigation pass; everything else is `[ ]`. Tick an item only when
its verification passes, not when the code is written. Each phase ends with its
**gate** and its **acceptance** block — do not start the next phase with either
unticked.

**The gate, for every phase:**

```bash
make lint && make test && make authz-check
cd frontend && npm run typecheck && npm run build && npm test
```

## Phase −1 — Planning and investigation *(complete)*

- [x] Read `docs/research/access-control.md` (1,725 lines) — Lakekeeper's
      `Authorizer` trait, the OpenFGA model, the eight lessons, the Metabase /
      Superset / Grafana calibration, and the nine open questions
- [x] Read `docs/access-control-plan.md` (1,087 lines) — the six concepts, the
      three invariants, the eight phases, the nine decisions, the ledger
- [x] Read `docs/architecture.md` §9 and §18, `docs/security.md` §3/§4.8/§6/§7,
      `docs/CODEBASE.md`, `docs/frontend.md` §1–§3, `docs/README.md`,
      `docs/mvp2-plan.md` Theme D, `CLAUDE.md`
- [x] Enumerate the API surface — 109 route decorators across 11 routers
- [x] Count and locate every `owner_id` decision site — **213 lines**, 18 files,
      133 of them in two services
- [x] Read `services/policy.py` and confirm four of its five functions have zero
      callers
- [x] Read `core/context.py`, `api/deps.py`, `domain/ports/identity.py`,
      `infra/db/models.py`, `services/audit.py`, `api/v1/users.py`
- [x] Confirm the latest migration is `0023_token_accounting` (so this plan's
      migrations start at `0024`)
- [x] Confirm **there is no `make check` target** — the gate is
      `make lint && make test` plus the frontend triple
- [x] Read the frontend shell, the `NAV` rail, the route table, `UsersPage.tsx`,
      the Data sources tab strip, and `components/settings.tsx`'s master–detail
      frame
- [x] Confirm `/auth/me` returns `{id, email, display_name, role}` and nothing
      else
- [x] Confirm `llm_configs` and `database_connections` list endpoints filter on
      `owner_id == ctx.user_id`
- [x] External research: Power BI workspace roles and service principals;
      Superset standard roles and `DASHBOARD_RBAC`; Metabase two-axis
      permissions, Blocked, and API keys; Grafana service accounts and RBAC
      scopes; Looker permission-set × model-set; Tableau Allow/Deny/Unspecified;
      machine-identity practice; OWASP API1:2023
- [x] Identify the `llm_config` `base_url` key-exfiltration finding
- [x] Decide the eighteen decisions of §0.4, including the four that reverse
      `access-control-plan.md`
- [x] Write this plan

## Phase 0 — Vocabulary, the port, the switches

- [x] `app/domain/value_objects/authz.py`: `Privilege` (5), `ResourceType` (8),
      `Capability` (18), `PrincipalKind` (2)
- [x] `_SATISFIED_BY` and `satisfying()` in the same module
- [x] `PRIVILEGE_MEANINGS` — the §13.3 matrix as data
- [x] `app/domain/ports/authz.py`: `ResourceRef`, `Decision`, `Everything`,
      `Subquery`, `Ids`, `Visible`, `Authorizer` Protocol (4 methods)
- [x] `app/infra/authz/__init__.py` and `owner_only.py` — `OwnerOnlyAuthorizer`
- [x] `core/config.py`: `authz_backend`, `auth_provider` (+ the §20.3 docstring),
      `allow_privileged_service_users`, `service_key_default_ttl_days`
- [x] `api/deps.py`: `get_authorizer`, `AuthzDep`
- [x] `services/policy.py`: `can(ctx, resource, privilege)` delegating; `owns`
      and `can_curate` untouched
- [x] `Makefile`: the `authz-check` target with the four greps
- [x] CI: `authz-check` added as non-blocking
- [x] Test: lattice reflexive, transitive, `manage` satisfies all five
- [x] Test: `satisfying()` returns an immutable frozenset
- [x] Test: `OwnerOnlyAuthorizer` agrees with `owns()` on a table of cases
- [x] Test: every `ResourceType` × `Privilege` has a `PRIVILEGE_MEANINGS` entry
- [x] **Gate green**
- [x] **Acceptance:** zero call sites and zero test assertions changed

## Phase 1 — `ctx` everywhere, part A

- [x] `DashboardService`: 15 methods take `ctx`, not `owner_id`
- [x] `_owned_connection` → `_authorized_connection`, asking the authorizer
- [x] `_owned_llm_config` → `_authorized_llm_config`
- [x] `ReportService`: ~30 methods take `ctx`
- [x] Every `WHERE owner_id` in both services composes `authz.visible(...)`
- [x] `api/v1/dashboards.py` passes `ctx`
- [x] `api/v1/reports.py` passes `ctx`
- [x] Test: a list endpoint emits a subquery, not a Python filter
- [x] **Gate green with no test assertion changed** — with one honest
      exception: the two route sweeps (`test_dashboards_api.py`,
      `test_reports_api.py`) asserted *"every route reaches the service with
      the session's owner id"*, and the service no longer takes an owner id.
      They now read `kwargs["ctx"].user_id` instead of `kwargs["owner_id"]` —
      the same claim about the same session, through the object that now
      carries it. Every other assertion in the suite is byte-identical; only
      call sites and fakes moved.
- [x] **Acceptance:** `grep owner_id` in both services returns only
      model-construction sites, the three `# authz-ok:` uniqueness predicates,
      and the keyword arguments still handed to `query_service` /
      `sql_draft_service`, which take a context of their own in Phase 2

## Phase 2 — `ctx` everywhere, part B

- [x] `run_service.py` (20 lines)
- [x] `sql_draft_service.py` (12)
- [x] `semantic_service.py` (8)
- [x] `query_service.py` (7)
- [x] `knowledge_service.py` (1) — the one line is an **exemption**, not a
      conversion: `_embedding_candidates` picks the *connection owner's*
      provider rows, which is whose budget pays rather than whose reach is
      being checked, and it runs from a worker as often as from a request
- [x] Routers: `conversations.py`, `llm_configs.py`, `connections.py`,
      `semantic.py`, `knowledge.py`, `drafts.py`
- [x] `RequestContext.on_behalf_of(user_id)`, plus `delegate(user_id)` for the
      worker that already holds one, and `delegated: bool` on the dataclass
- [x] `workers/report.py`, `report_graph.py`, `benchmark.py`,
      `knowledge_maintenance.py` use it. **`workers/semantic.py` does not, and
      that is the honest answer**: it resumes a job that was authorized when it
      was queued and asks nothing further, so it has nobody to act *for* —
      `SemanticService` takes its authorizer optionally for exactly that half
- [x] `infra/authz/factory.build_authorizer` — the one place a setting becomes
      an implementation, so a worker cannot keep asking the old question after
      Phase 6 flips it
- [x] `make authz-check` becomes blocking in CI
- [x] Test: `RequestContext` cannot be built without a principal id
- [x] Test: no worker constructs a context any other way (an **AST walk** over
      `app/workers`, plus its converse — the three modules that run somebody's
      SQL must still name whose)
- [x] Test: a delegated action's audit row carries `delegated: true`, and an
      ordinary one carries no such key at all
- [x] Doc: `architecture.md` §18
- [x] Doc: `CODEBASE.md` §6
- [x] Doc: `dashboards.md` §9 and `reports.md` §14
- [x] **Gate green including blocking `authz-check`**
- [x] **Acceptance:** `owner_id` is a stored fact and nothing in `api/` or
      `services/` reads it to decide. The gate prints **nine** exemptions and
      every one carries its reason: five `unique (owner, name)` predicates
      (they ask about the row about to be *written*), one embedding-candidate
      query, and three lines marked **retires in Phase 3** — `require_admin`,
      `RequestContext.is_admin` and `can_curate`'s administrator arm, which are
      the `AdminDep` surface `needs(capability)` replaces. `policy.can_read`,
      `can_write` and `can_administer_users` were **deleted**: zero callers, so
      no behaviour moved with them

## Phase 3 — Roles and capabilities

- [x] Migration **`0024_roles.py`** (renumbered: Phase 3 ships before Phase 5,
      and alembic revisions are linear): `roles`, `role_capabilities`,
      `role_scoped_privileges`, `role_assignments`. **`role_assignments.team_id`
      is deliberately absent** — a column with a foreign key to a table that
      does not exist yet is not a column; `0025` adds it with `teams`
- [x] Seed the eight system roles with exactly the §12.3 capability sets
- [x] Backfill `users.role` → a role assignment for every existing user
- [x] ORM models for the four tables
- [x] `services/role_service.py`: create, update, delete-with-guard, assign,
      unassign, `resolve_capabilities`
- [x] `api/v1/roles.py`: CRUD gated `role.read` / `role.manage`, plus
      `/roles/capabilities` and `/roles/privileges` — the two vocabularies the
      editor renders, **served rather than duplicated in the SPA**
- [x] `api/v1/users.py`: `GET/POST/DELETE /users/{id}/roles`
- [x] `api/deps.py`: `needs(capability)`; `AdminDep` aliased and deprecated
- [x] `RequestContext.capabilities`, one query, resolved in `get_ctx`
- [x] `ctx.is_admin` becomes a deprecated computed property over `user.manage`
- [x] Every `AdminDep` route moves to `needs(...)`
- [x] `services/bootstrap.py` assigns `Administrator`, in the same transaction
      as the account — an installation cannot come up with an administrator who
      is not one
- [x] `_guard_last_admin` → `RoleService.guard_last_administrator`, counted over
      assignments, reached by **both** routes into a demotion
- [x] `MeResponse` gains `kind`, `capabilities`, `roles`, `teams`; a second
      endpoint `GET /auth/me/permissions` answers the same without the account
- [x] Audit actions: `role.created/updated/deleted/assigned/unassigned`
- [x] Frontend: `/users` → `/admin/people` permanent redirect
- [x] Frontend: rail row becomes **Administration**, gated on a capability set
- [x] Frontend: `/admin` shell with tabs appearing per capability
- [x] Frontend: **People** tab, reusing `UsersPage`'s list furniture
- [x] Frontend: People detail gains a **Roles** section
- [x] Frontend: **Roles** tab — list, capability checklist, scoped-privilege
      matrix, system badge
- [x] Frontend: `useCan()` replaces every `user.role === 'ADMIN'` that decided
      something. The survivors are labels and counts, each carrying its own
      `authz-ok:` marker — and the gate's frontend arm was **widened to match
      `===`**, which it never did, so it had silently checked nothing for two
      phases
- [x] Test: the eight seeds match §12.3 exactly (a table test, from an
      independent transcription of the plan)
- [x] Test: a system role refuses a capability edit, allows a rename
- [x] Test: deleting an assigned role is refused and names holders
- [x] Test: the last-administrator guard, through both routes
- [x] Test: a capability change takes effect on the next request, no new token
- [x] Test: capability resolution is one query
- [x] Test: a route-table walk proves every ex-`AdminDep` route is capability
      gated — walking the **live** table, and asserting the walk itself finds
      routes, because a flattener that returned nothing would have passed
- [x] Test: the SQLite session used by the role tests **refuses a lazy load**,
      because `AsyncSession` cannot do one. Added after `POST /roles` shipped
      as a 500 that every test in the file was green for
- [x] Doc: `security.md` §6 · `frontend.md` §2 · this plan's ledger
- [x] **Gate green** — ruff, 8 import-linter contracts, 2294 backend tests,
      `make guard`, `make authz-check`, frontend typecheck + build + tests
- [x] **Acceptance**, verified end to end against the running stack: an Auditor
      reaches People, Roles and Audit and is refused (403) on every write; a
      custom role is created, assigned, and appears in the holder's `/auth/me`
      **on the same access token**; a system role refuses a capability edit with
      an explanation and accepts a rename; deleting an assigned role is refused
      and names the holder

## Phase 4 — Teams

- [x] Migration **`0025_teams.py`** (renumbered with `0024`): `teams`,
      `team_members` — **and the widening of `role_assignments`** that `0024`
      could not ship: `team_id`, a nullable `user_id`, the one-principal
      `CHECK`, and the `UNIQUE NULLS NOT DISTINCT` three-column constraint
      `0024` had already named it for
- [x] ORM models
- [x] `services/team_service.py`: CRUD, membership, delete-with-guard,
      `roles_by_team` for the list screen
- [x] `api/v1/teams.py` gated `team.read` / `team.manage`. **`(team, modify)`
      for a team lead is *not* wired**, and that is honest rather than
      forgotten: the privilege exists in the vocabulary and the authorizer that
      would answer it is `OwnerOnlyAuthorizer`, which knows nothing about
      teams. It becomes reachable in Phase 6 with the rest of the grant path
- [x] `PUT /teams/{id}/source` as a separate, audited endpoint
- [x] `RequestContext.team_ids`, one query, resolved in `get_ctx`; empty on a
      delegated context, for the same fail-closed reason `capabilities` is
- [x] Capability resolution widens to roles reaching through a team — **two
      arms of one `WHERE`**, so the query count on the request path does not
      move
- [x] `MeResponse` gains `teams`, and `roles` becomes `distinct` — a role held
      directly *and* through a team is one role
- [x] Audit actions: the six team actions
- [x] Frontend: **Teams** tab — list, detail, members picker, assigned roles,
      external binding
- [x] Frontend: People detail gains a **Teams** section (read-only: membership
      is a set edited on the team, and a second per-person control would be a
      way to race it)
- [x] Test: membership resolution across three teams
- [x] Test: cascade on user delete and on team delete
- [x] Test: `ck_teams_source_pair`, through the service's own refusal
- [x] Test: `team_ids` populated, and a grep proving **no authorizer reads it**
- [x] Test: a team role reaches members and stops on removal
- [x] Test: deleting a team holding a role assignment is refused, and deleting
      a role a *team* holds names the team rather than a person
- [x] Test: the last-administrator guard is not satisfied by a team holding
      `Administrator`
- [x] Test: the teams list carries each team's roles, in one query — written
      for a bug found by opening the screen, where a team plainly holding
      BI Engineer rendered "no roles yet"
- [x] Doc: `security.md`, `frontend.md`, `CLAUDE.md` invariant 5, the ledger
- [x] **Gate green** — ruff, 8 import-linter contracts, backend suite,
      `make guard`, `make authz-check`, frontend typecheck + build + tests
- [x] **Acceptance**, verified end to end against the running stack: a probe
      account with **no roles at all** is put in a team holding BI Engineer and
      gains `dashboard.create` on the **same access token**; removing them from
      the team removes it again on the next request; `team_ids` is read by no
      resource decision, and a grep asserts it

## Phase 5 — Service users

- [ ] Migration `0024_principal_kind.py`: `users.kind`, `users.description`, the
      three `CHECK`s, `ix_users_kind`
- [ ] Migration `0025_service_credentials.py`
- [ ] ORM models and the `PrincipalKind` plumbing
- [ ] `domain/ports/identity.py`: `ServiceIdentityProvider` Protocol
- [ ] `infra/identity/service_key.py`: generate, verify (SHA-256,
      constant-time), expiry, revocation, throttled `last_used_at`
- [ ] `api/deps.py`: `get_ctx` dispatches on the `dm_sk_` prefix
- [ ] `services/service_user_service.py`, incl. the privileged-capability refusal
- [ ] `api/v1/service_users.py` gated `service_user.manage`
- [ ] `/auth/login`, `/auth/refresh`, `/auth/me*` refuse a `SERVICE` principal
- [ ] Audit actions: the six service actions
- [ ] Frontend: **Service accounts** tab — list, create form with effective
      permission preview, detail, Keys panel
- [ ] Frontend: one-time key display reusing the one-time-password panel
- [ ] Frontend: kind badges wherever a principal is listed
- [ ] Frontend: the role picker hides privileged capabilities and says why
- [ ] Test: valid / revoked / expired / unknown-prefix / wrong-secret key
- [ ] Test: constant-time comparison helper
- [ ] Test: `last_used_at` throttling
- [ ] Test: a `SERVICE` principal is refused by every `/auth` route
- [ ] Test: the three `CHECK` constraints
- [ ] Test: a service user's capabilities are byte-identical to a human's with
      the same role
- [ ] Test: privileged capabilities refused off, allowed and audited on
- [ ] Test: `test_openapi_has_no_secrets.py` covers `token_hash` and the key
- [ ] Doc: `security.md` §6 subsection and §7 checklist item; `README.md`
      *Programmatic access*
- [ ] **Gate green**
- [ ] **Acceptance:** a key authenticates and returns the same body a human in
      the same team would get; the key is shown once; revoking fails the next
      request

## Phase 6 — Grants on connections, knowledge, semantic layer ⚠️

- [ ] Migration `0028_grants.py` including the wildcard partial index
- [ ] ORM model
- [ ] `app/infra/authz/rbac.py`: `RbacAuthorizer` — wildcard short-circuit, then
      the union subquery, for all four port methods
- [ ] `services/grant_service.py`: grant, revoke, list-by-resource,
      list-by-principal, last-`manage` guard, wildcard requiring `role.manage`
- [ ] `api/deps.py`: `on(type, privilege, param)`
- [ ] `connections.py`: `/grants`, `/actions`, `/transfer`
- [ ] `/connections/{id}/knowledge/grants` and `/semantic/grants`
- [ ] `disclosure_policy` out of `PATCH`, into `PUT /connections/{id}/disclosure`
      gated on `manage`
- [ ] `ConnectionRead` narrowed at `describe` — no host, no credentials
- [ ] Every knowledge route moves to `(knowledge, …)`; `can_curate` deleted;
      `curation_admin_only` removed from config
- [ ] Every semantic route moves to `(semantic_layer, …)`
- [ ] The 404/403 rule in one exception helper
- [ ] `workers/reconciler.py`: the orphaned-grant sweep
- [ ] `DELETE /users/{id}` refuses while resources are owned, naming them
- [ ] `authz_backend` default flips to `"rbac"`
- [ ] Audit: `grant.created/revoked`, `grant.wildcard.created`,
      `ownership.transferred`, `disclosure.changed`
- [ ] Frontend: `<AccessPanel>` — picker, privilege radio from `…/actions`,
      current access **with the path**, revoke
- [ ] Frontend: `/sources/:id/access` as the fifth tab
- [ ] Frontend: the Policy tab's disclosure control gated on `manage`
- [ ] Frontend: Access popovers on the Knowledge console and Semantic tab headers
- [ ] Frontend: Data sources list shows granted connections with an owner column
- [ ] Test: lattice implication end to end
- [ ] Test: a team grant reaches a member, and stops on removal
- [ ] Test: a wildcard grant is refused without `role.manage`
- [ ] Test: last-`manage` self-revocation refused
- [ ] Test: the disclosure gate, both directions, audited
- [ ] Test: ownership transfer; the deletion refusal
- [ ] Test: the sweep removes only orphans
- [ ] Test: the seven `can_curate` tests **rewritten** and passing on the same
      rule
- [ ] Test: **a Knowledge Manager curates and cannot read, edit or widen** —
      requirement 2's named acceptance test
- [ ] Test: 404 with nothing, 403 with `describe`, message names the privilege
- [ ] Doc: `security.md` §3 (sharing interaction) and §6; `architecture.md` §18;
      `CLAUDE.md` invariant 5; the ledger
- [ ] **Gate green**
- [ ] **Acceptance:** the two-user connection scenario, all four refusals, every
      event in `GET /audit`, and `owner_only` still a working rollback

## Phase 7 — The audit half

- [ ] `DENIED` written on every 403, with `because`; never on a 404
- [ ] The remaining §19.4 actions
- [ ] Administrator self-grant path: an ordinary grant row **plus**
      `admin.self_granted`
- [ ] The ask path records the disclosure policy in force
- [ ] `GET /audit` filters: outcome, resource type, actor, date range, pagination
- [ ] Frontend: `/admin/audit`, denials rendered distinctly, actor by name only
- [ ] Test: one row per denial with a non-empty `because`
- [ ] Test: zero rows per 404
- [ ] Test: `detail` holds no SQL, question, row or key
- [ ] Test: a self-grant writes two rows
- [ ] Test: an ask records the policy
- [ ] Test: a failing audit write does not fail the action
- [ ] Doc: `security.md` §4.8
- [ ] **Gate green**
- [ ] **Acceptance:** an administrator can reconstruct who granted what and what
      was refused, from the UI

## Phase 8 — Grants on artifacts

**8a — Reports**
- [ ] Grants on `report`; `visible` in the list; share panel
- [ ] A viewer needs `select` on the report **and** its connection
- [ ] A missing connection privilege renders a placeholder, not a 500

**8b — LLM configs**
- [ ] Grants on `llm_config`, `describe`/`select` only; the API refuses higher
- [ ] `PATCH` clears the stored key when `base_url` or `provider` changes
- [ ] `llm_config.endpoint.changed` audit action
- [ ] `resolve_llm` re-checks `select` at execution
- [ ] Frontend: Access section on the provider detail, with the reason

**8c — Conversations**
- [ ] Grants on `conversation`; share from the thread kebab
- [ ] A shared thread does not share its connection

**8d — Dashboards**
- [ ] Grants on `dashboard`; `visible` in the list
- [ ] **The intersection rule** — per-tile `select` on that tile's connection
- [ ] The named tile placeholder, never hidden
- [ ] `refresh` re-checks per tile, per execution
- [ ] The share-time cross-connection warning, naming the connections
- [ ] The tile-cache trigger sentence in `DashboardTileCache`'s docstring

**Across 8**
- [ ] Frontend: Share dialogs on both index cards and both detail headers
- [ ] Frontend: **Shared with me** filter chip on both index toolbars
- [ ] Frontend: `Limited` / `Read-only` badges from `…/actions`
- [ ] Test: a two-connection dashboard renders one tile and one placeholder
- [ ] Test: the cache key contains no viewer (the tripwire)
- [ ] Test: an `llm_config` grant above `select` is refused
- [ ] Test: changing `base_url` clears the key
- [ ] Doc: `dashboards.md` §9, `reports.md` §14, `frontend.md` §2
- [ ] **Gate green**
- [ ] **Acceptance: the one-line test** — two people, one credential, one
      dashboard, four refusals, four audit rows

## Phase 9 — Access review and the explainer

- [ ] `services/access_review_service.py` — one query, two lenses, five path
      kinds
- [ ] `api/v1/access_review.py` gated `access.review`, with CSV
- [ ] `GET /me/permissions`
- [ ] Every 403 body carries a structured `reason` from `Decision.because`
- [ ] Frontend: `/admin/access` — both lenses, filters, CSV export
- [ ] Frontend: `<Restricted>` and the **Why can I not see this?** popover
- [ ] Frontend: the popover used by the tile, block and chat-turn placeholders
      and every 403 surface
- [ ] Frontend: **Effective access** in People, Service accounts and Teams
      details
- [ ] Test: the two lenses agree (a property test)
- [ ] Test: `path` correct for each of the five facts
- [ ] Test: the review shows reach and **never** data
- [ ] Test: CSV escaping, RFC 4180 plus the leading-apostrophe rule
- [ ] Doc: `frontend.md` §2, `docs/README.md`
- [ ] **Gate green**
- [ ] **Acceptance:** *"why can Reza curate this?"* answered in one click, naming
      the role

## Phase 10 — The rulebook, the conformance check, the seams

- [ ] Write `docs/access-control-rules.md` with all eight required contents (§27)
- [ ] Pointers from `CLAUDE.md`, `docs/README.md`, `services/policy.py`
- [ ] `tests/unit/test_authz_conformance.py`: every route carries a `ctx`
- [ ] …: every `ResourceType` × `Privilege` has a meaning row
- [ ] …: every owner-scoped list endpoint composes `visible(...)`
- [ ] …: the four greps, promoted to tests
- [ ] …: **I5** — a route-table walk calling every mutating route unprivileged
- [ ] …: **I4** — a property test that no addition ever removes access
- [ ] Seam test: `external_subject` round-trips `provider~subject`
- [ ] Seam test: an unknown external group is ignored, not created
- [ ] Seam test: `team_ids` and `capabilities` each have one resolution site
- [ ] Seam test: a `capabilities` claim in a JWT is ignored
- [ ] Synthetic OIDC test: RS256 fixture, static JWKS, expiry / audience /
      issuer / `kid` / group / role mapping — **no Keycloak in CI**
- [ ] Delete `ctx.is_admin`, `AdminDep`
- [ ] Migration `0029_drop_users_role.py`
- [ ] Frontend: `api/permissions.ts` and a test that no component reads
      `user.role`
- [ ] Doc: `docs/README.md` — this plan becomes **Live**;
      `access-control-plan.md` marked superseded
- [ ] Doc: final pass over `architecture.md`, `security.md`, `frontend.md`,
      `CODEBASE.md`, `CLAUDE.md`
- [ ] Doc: fill in §29's ledger
- [ ] **Gate green**
- [ ] **Acceptance:** the rulebook exists, the conformance test enforces every
      mechanical rule in it, and `grep is_admin\|AdminDep\|users.role` returns
      nothing

## Cross-cutting, do not forget

- [ ] Every new endpoint appears in `docs/architecture.md` §26's endpoint list
- [ ] Every new audit action is named in `services/audit.py`'s vocabulary block
- [ ] Every new `Capability` and `ResourceType` is named in the rulebook
- [ ] `docs/README.md`'s *"Users, groups, roles, grants — anything about who
      may"* row points at this plan, then at the rulebook
- [ ] No new dependency is added to `backend/pyproject.toml` by any phase
- [ ] No phase adds a container to `docker-compose.yml`
- [ ] `.env.example` documents `AUTHZ_BACKEND`, `AUTH_PROVIDER`,
      `ALLOW_PRIVILEGED_SERVICE_USERS`, `SERVICE_KEY_DEFAULT_TTL_DAYS`
- [ ] The eval harness and `make guard` are untouched by every phase

---

## 29. Progress ledger

**Updated at the end of every phase.** Counts are checklist items from Part 6.

| Phase | Items | Done | Landed |
|---|:--:|:--:|---|
| −1 · Planning and investigation | 16 | **16** | 2026-09-06 |
| 0 · Vocabulary and the port | 16 | **16** | 2026-09-06 |
| 1 · `ctx` part A | 10 | **10** | 2026-09-06 |
| 2 · `ctx` part B | 16 | **16** | 2026-09-07 |
| 3 · Roles and capabilities | 32 | **32** | 2026-09-07 |
| 4 · Teams | 20 | **20** | 2026-09-07 |
| 5 · Service users | 25 | 0 | — |
| 6 · Grants on connections | 34 | 0 | — |
| 7 · The audit half | 15 | 0 | — |
| 8 · Grants on artifacts | 26 | 0 | — |
| 9 · Access review | 15 | 0 | — |
| 10 · Rulebook and seams | 21 | 0 | — |
| Cross-cutting | 8 | 0 | — |
| **Total** | **254** | **110** | |

## 30. The one-line acceptance test for the whole plan

> **Six people, two teams, one agent, one database credential and one dashboard:
> the Knowledge Manager can teach the connection and cannot read it; the BI
> Engineer team can see the numbers and cannot see the password; the agent does
> exactly what its team does and cannot mint an administrator; the Auditor can
> say who can reach what and can read none of it; the administrator can reach
> anything and cannot do so silently — and every one of those facts is a row in
> `audit_logs`, enforced by the API, and visible in the UI without anyone
> guessing at a role string.**
