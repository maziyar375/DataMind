# Access control — the rules

**Read this before writing an endpoint.** It is short on purpose: everything
here is a rule you can break in twenty minutes and nobody will notice for a
release. The argument behind each one is in
[user-management-and-access-control.md](../plans/user-management-and-access-control.md);
this is the part you need in your head while you type.

Two machines check most of it — `make authz-check` and
`backend/tests/unit/test_authz_conformance.py` — and §8 says which rules they
cover and which are on you.

---

## 1. Seven concepts, one page

Everything in this model is one of seven things. A feature that cannot be
expressed in them needs a design change, not a workaround.

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

**Capability or privilege? If the check needs an id, it is a privilege.**

| | *"What may I do?"* | *"What may I do it to?"* |
|---|---|---|
| Carried by | **capabilities** on a role | **grants** and **scoped privileges** |
| Asked as | `deps.needs(Capability.X)` | `deps.on(type, privilege, param)` |
| Answers | may Ali create a dashboard **at all**? | may Ali open **this** dashboard? |

**The lattice is data, expanded at read time.** A `modify` holder passes a
`select` check with no row saying so, because `satisfying(privilege)` widens
the question — `privilege = ANY(:satisfying)` — rather than the write. Changing
the lattice therefore never needs a backfill. Never check two privileges where
one would do: `manage or modify` is the lattice written out by hand, and it is
wrong the day the lattice changes.

**`manage` is not implied by `modify`.** Editing a connection's credentials and
deciding who else may read through it are different acts. This is why the
disclosure policy has its own `manage`-gated endpoint instead of being a field
on `PATCH`.

---

## 2. The five invariants

> **I1 — One decision function.** No SQL in `api/` or `services/` filters on
> `owner_id` to decide access, and no handler compares a role string.
> Visibility is `authz.visible(...)`; permission is `authz.allowed(...)`;
> app-wide verbs are `ctx.can(...)`. *Break it and the second copy of the rule
> is the one that is wrong, and nobody finds out until it is a screenshot in a
> ticket.*

> **I2 — Sharing an artifact never shares the data behind it.** A tile, a
> report figure or a chat turn renders only if the viewer holds `select` on
> **that** resource's connection, re-checked at execution. *Break it and a
> shared dashboard becomes a way to read a database you were never given.*

> **I3 — Every authorization event is a row.** Grant, revoke, role assignment,
> team membership, ownership transfer, administrator self-escalation, service
> key issued or revoked, disclosure change — and **every 403**.
> `Decision.because` travels with it. Identifiers and counts, never content.
> *Break it and the log has a gap nobody can tell from a quiet week.*

> **I4 — Permission is a union and nothing subtracts.** No `deny` row, no
> priority column, no evaluation order. *Break it and "why can Ali see this?"
> stops having a one-sentence answer, which is the failure mode every product
> in the research fell into.*

> **I5 — The UI is an affordance, never a boundary.** Every restriction the
> frontend renders exists because the server answered a question, and the same
> request made with `curl` is refused. *Break it and the product is
> unauthenticated with extra steps.*

---

## 3. The algorithm, verbatim

This is the thing people get wrong. It is five arms and one intersection.

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

**There is no sixth arm, and there is no administrator arm.** An administrator
reaching somebody else's resource is an explicit self-grant that writes a
grant row *and* an `admin.self_granted` row. If you are adding `if
ctx.is_admin` to a decision, that is the thing this model exists to not have.

---

## 4. Checklist — a new endpoint

Answer these before you write the handler. Each one has exactly one right
answer for any given route; a route you cannot answer them for is a route
whose access rule has not been decided yet.

- [ ] **Which `ResourceType` does this touch?** If none, is it a
      **capability**? (If the check needs an id, it is a privilege.)
- [ ] **Which `Privilege`?** Use the lattice. **Never check two.**
- [ ] **Detail or list?** A detail calls `allowed` — or declares
      `deps.on(type, privilege, param)`, which runs before the handler body and
      so cannot be forgotten. A list composes `visible(...)` into the `SELECT`
      it was already going to run. **Never a loop over `allowed`** — that is a
      pagination bug wearing a style complaint's clothes.
- [ ] **Does it execute against a connection?** Then it re-checks `select` on
      **that** connection, at execution — not at authoring, not at queue time.
- [ ] **Does it change a disclosure policy, an owner, a grant, a role or a
      membership?** Then it is `manage` (or a capability) **and** it is
      audited.
- [ ] **404 or 403?** Never decide this in the handler — call
      `services.policy.require`, which is the one place §19.1 lives: 404 when
      nothing reaches the principal (and **not** audited, because a 404 is
      indistinguishable from a typo), 403 naming the privilege when something
      does.
- [ ] **Does its result depend on who is looking?** If yes, **stop.** That
      trips the cache trigger — `DashboardTileCache`'s docstring has the long
      version — and it is a design conversation, not a keyword argument.

---

## 5. Checklist — a new resource type

Eight places, and the conformance test fails on six of them.

- [ ] The `ResourceType` enum member (`domain/value_objects/authz.py`).
- [ ] A `PRIVILEGE_MEANINGS` row — **all five privileges**, in the words a
      person reads, because a 403 and a share dialog both quote it.
- [ ] `_OWNED_TABLES` in `infra/authz/owner_only.py`, so `visible` has an
      ownership arm to union.
- [ ] A `_NOUN` and a `_NOT_FOUND` entry in `services/policy.py`.
- [ ] The orphaned-grant sweep in `workers/reconciler.py`.
- [ ] `attach_access_routes(router, ResourceType.X, param="…")` — the five
      access routes, written once.
- [ ] `_LABEL` in `services/access_review_service.py` and `grant_service.py`,
      so a review row and a deletion refusal can name it.
- [ ] A `<AccessPanel base="…">` mount point on its detail screen.

---

## 6. Checklist — a new capability

- [ ] The `Capability` enum member.
- [ ] Which seed roles get it, **and why** — a capability nobody holds is a
      capability that will be granted to Administrator by whoever needs it next.
- [ ] The migration that adds it to those roles.
- [ ] `deps.needs(...)` at the route, never a check in the body.
- [ ] An audit action, if it guards a mutation.
- [ ] A row in `docs/reference/security.md`'s capability table.

---

## 7. Not in the model — do not improvise these

Every one of these has been tried by a product in the research, and every one
of them is why that product's permissions are hard to reason about:

- **No `deny` row, no priority order, no evaluation order.** Permission is a
  union (I4).
- **No per-endpoint role string.** `if user.role == "ADMIN"` is the thing this
  model replaced.
- **No `ctx=None`.** A call that cannot say who is acting is a decision made on
  nobody's behalf. `RequestContext.user_id` has no default, so it is a
  `TypeError` rather than a convention.
- **No god context.** Background work names its principal through
  `RequestContext.on_behalf_of(owner)` and gets exactly that person's answers.
  A scheduled run its owner could not perform by hand is *supposed* to fail.
- **No permission keyed on an email or an external subject.** Both are
  mutable, and a permission that survives a rename is the point of an id.
- **No capability read out of a token.** They are resolved per request from the
  database (decision 15), which is what makes a revoked role take effect on the
  next request rather than at the next refresh. A JWT carrying a
  `capabilities` claim is ignored.
- **No role naming one resource id.** A scoped privilege is over a whole type.
  A role that could name one resource would make *"why can Ali see this?"*
  unanswerable in one sentence — "because of the Knowledge Manager role" and
  "because Sara granted it on 3 March" have to stay different sentences.
- **No viewer in a cache key.** See the endpoint checklist's last line.

---

## 8. How this is enforced

| Rule | Checked by |
|---|---|
| I1 — no `owner_id ==`, no role string, no `ADMIN` literal, no `ctx=None` | `make authz-check` (four greps, each exemption carrying its reason on the line) **and** `test_authz_conformance.py` |
| Every route resolves a `ctx` | `test_authz_conformance.py` |
| Every `ResourceType` × `Privilege` has a meaning | `test_authz_conformance.py` |
| Every owner-scoped list composes `visible` | `test_authz_conformance.py` |
| I5 — every mutating route refuses an unprivileged caller | `test_authz_conformance.py`, walking the route table |
| I4 — nothing subtracts | `test_authz_conformance.py`, a property test |
| I2 — the intersection rule, and the cache order | `test_intersection.py` |
| I3 — one row per denial, none per 404, no content in `detail` | `test_denials.py`, `test_audit_and_permissions.py` |
| This document does not go stale | `test_authz_conformance.py` asserts every `ResourceType` and `Capability` is named here |

**What no machine checks**, and therefore what a reviewer has to: whether the
privilege you picked is the *right* one. `select` where `describe` would do is
a leak nothing here will catch.
