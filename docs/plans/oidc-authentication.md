# OIDC sign-in — build plan

> **Status:** a plan, not a proposal. Written 2026-09-20 against `main`.
> The argument for *why* — and seven of the eight seams this needs — already
> lives in
> [user-management-and-access-control.md §20](user-management-and-access-control.md#20-authentication-and-how-oidc--keycloak-arrives-later).
> This document is *what we build, in what order, and how we know it worked*.
>
> **The goal, as asked for:** one environment variable chooses between the way
> sign-in works today (email + password against this database) and delegating
> it to an external issuer — a self-hosted **Keycloak**, or **Sign in with
> Google**, or anything else publishing a discovery document.
>
> **One divergence from §20.3, stated up front and argued in [§2](#2-the-one-divergence-from-203-and-why).**
> That section imagined the SPA running the code flow and sending the IdP's own
> access token as the bearer on every request. This plan runs the
> authorization-code exchange **on the server** and has DataMind mint its own
> session exactly as the password path does. Google's access tokens are opaque
> — not JWTs, nothing for a resource server to verify — so the §20.3 shape
> works against Keycloak and cannot work against Google at all. Everything else
> in §20.3 is followed to the letter, including the step-5 matching rule, which
> is the whole security story and is [§8](#8-account-matching--the-rule-the-rest-of-this-document-serves).
>
> [§15](#15-progress-ledger) is the ledger: a checkbox per deliverable per
> phase, each with the check that proves its state. Tick a box in the commit
> that lands the work, never ahead of it.

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

*An installation chooses, with one environment variable and no migration,
whether a person proves who they are with a password stored here or with an
account at an issuer it trusts — and nothing downstream of that choice can tell
which happened.*

### 0.2 The five decisions

| # | Decision | What it rules out |
|---|---|---|
| **D1** | **The authorization-code exchange happens on the server, and DataMind still mints its own session.** The browser is redirected to the IdP, comes back to a callback route here, and leaves with the same access token and `raymand_refresh` cookie a password sign-in produces. | The §20.3 shape — the SPA bearing the IdP's token per request. It cannot work against Google (opaque access tokens), it puts the client secret or a public-client flow in the browser, and it makes session revocation something an external issuer owns. See [§2](#2-the-one-divergence-from-203-and-why). |
| **D2** | **`api/deps.get_ctx` does not change. At all.** Both modes produce a DataMind JWT; the bearer path, the two capability queries and the `RequestContext` are byte-identical either way. | A third branch in the hot path. The seam requirement 9 asks for is already load-bearing — two authenticators produce one context — and a third that only *mostly* matches would be where the next authorization bug lives. |
| **D3** | **Email matches once. `external_subject` matches forever after.** An account is bound to an issuer at first sign-in by a *verified* email; from then on it is found by subject alone, namespaced `{provider_id}~{subject}`. | The takeover: matching on email at every sign-in makes an attacker-controlled address at the IdP — a Keycloak realm anybody may register in, a Workspace alias — a takeover of the DataMind account with the same address. This is §20.3 step 5 and it is not negotiable. |
| **D4** | **Under `oidc`, a password still works for an account that has no `external_subject`, and for no other.** The bootstrap administrator stays local, as break-glass. | Being locked out of your own deployment by a typo in a client secret. Also the reverse — an account moved to SSO keeping a second, weaker way in. The rule is one condition in one function, not a role check. |
| **D5** | **Provisioning is gated on an email-domain allowlist, and an empty allowlist refuses everyone.** A stranger the IdP authenticates becomes a user only if their verified address matches, and then gets the configured default role and nothing else. | "Sign in with Google" on a public URL meaning *every Gmail account on earth is a user of this deployment*. An empty-means-everyone default would ship exactly that, silently. |

### 0.3 The phases

| # | Phase | Size | Gate to start | Changes an existing sign-in? |
|---|---|:--:|---|:--:|
| **0** | [Configuration and the provider, inert](#4-phase-0--configuration-and-the-provider-inert) | M | — | **no** — nothing imports it yet |
| **1** | [The two routes, and the session hand-off](#5-phase-1--the-two-routes-and-the-session-hand-off) | M | Phase 0 done | no — unreachable while `AUTH_PROVIDER=local` |
| **2** | [The login screen](#6-phase-2--the-login-screen) | S–M | Phase 1 done | no — the form is unchanged under `local` |
| **3** | [Break-glass, and the operator's story](#7-phase-3--break-glass-and-the-operators-story) | S | Phase 2 done | **yes**, under `oidc` only |

Phases 0–2 are all invisible while `AUTH_PROVIDER=local`, which is the default
and stays the default. **The feature is off until an operator turns it on**, and
turning it off again is the same one line — no migration, in either direction.

### 0.4 The flow, after all four phases

```
  ┌─ AUTH_PROVIDER=local ─────────────────────────────────────────────┐
  │  POST /auth/login  (email + password)  → Argon2id → issue_session │
  └───────────────────────────────────────────────────────────────────┘
                                                    │
                                    the SAME issue_session, the SAME
                                    sessions row, the SAME cookie
                                                    │
  ┌─ AUTH_PROVIDER=oidc ──────────────────────────────┴───────────────┐
  │  GET /auth/oidc/login                                             │
  │     ├ mint state + nonce + PKCE verifier                          │
  │     ├ set  datamind_oidc_tx   (signed, HttpOnly, 10 min)          │
  │     └ 307 → {issuer}/authorize?…                                  │
  │                            ↓  person signs in at Keycloak/Google  │
  │  GET /auth/oidc/callback?code=…&state=…                           │
  │     ├ state == cookie.state           (CSRF)                      │
  │     ├ POST {token_endpoint}           (server-to-server, + PKCE)  │
  │     ├ verify id_token vs JWKS         (sig, iss, aud, exp, nonce) │
  │     ├ claims → users row              (§8 — the rule)             │
  │     ├ issue_session  ← the same one the password path calls       │
  │     └ 302 → the SPA                                               │
  │                            ↓                                      │
  │  the SPA mounts, auth.restore() calls POST /auth/refresh,         │
  │  the cookie is already there, and it is signed in.                │
  └───────────────────────────────────────────────────────────────────┘
                                                    │
                     ┌──────────────────────────────┴──────────────────┐
                     │  get_ctx — UNCHANGED, and cannot tell which     │
                     │  of the three authenticators ran                │
                     └─────────────────────────────────────────────────┘
```

**No token is ever put in a URL.** The callback's last act is a redirect to the
SPA's own origin with nothing in the query string but, on failure, an error
code. The access token arrives by the refresh cookie the SPA already knows how
to spend — [`auth.restore()`](../../frontend/src/api/client.ts) calls
`/auth/refresh` on every mount today, which is the entire reason this hand-off
needs no new frontend machinery.

---

## 1. What already exists (verified against the tree, 2026-09-20)

This feature is unusually cheap because the shape was built for it. Seven of
§20.2's eight seams are shipped:

| Seam | Where | State |
|---|---|---|
| `IdentityProvider` Protocol | [`domain/ports/identity.py`](../../backend/app/domain/ports/identity.py) | ✅ five methods, and the module's own docstring says an OIDC adapter is the point |
| `users.external_subject` | [`infra/db/models.py`](../../backend/app/infra/db/models.py) | ✅ `String(255)`, nullable, since `0001` — **never read or written by anything** |
| `AuthenticatedIdentity.external_subject` | `domain/ports/identity.py` | ✅ the field is already on the dataclass |
| `ctx.team_ids`, `ctx.capabilities` from the database | [`api/deps.py`](../../backend/app/api/deps.py) | ✅ resolved per request, never from a claim |
| A second authenticator proving the seam | [`infra/identity/service_key.py`](../../backend/app/infra/identity/service_key.py) | ✅ `get_ctx` builds one context from either |
| `auth_provider` config switch | [`core/config.py`](../../backend/app/core/config.py) | ⚠️ exists as `Literal["local"]` with the §20.3 recipe in its docstring — **one member, on purpose** |
| `PrincipalKind` CHECK constraints | `models.py` `__table_args__` | ✅ `kind <> 'SERVICE' OR external_subject IS NULL` — the database already refuses a federated machine identity |
| `teams.(provider_id, source_id)` / `roles.(…)` | — | ❌ **not built**, and this plan does not build them ([§9](#9-what-this-plan-deliberately-does-not-build)) |

Two more facts that shape the work, both confirmed by reading the code rather
than assumed:

- **`pyjwt` and `httpx` are already dependencies** ([`backend/pyproject.toml`](../../backend/pyproject.toml)),
  and `cryptography` is there for RS256. **No new dependency is needed** — which
  matters in a repo whose frontend dependency list is four packages on purpose.
- **The SPA restores its session from the refresh cookie on mount.**
  `auth.restore()` → `POST /auth/refresh` → `GET /auth/me`, already wired in
  [`App.tsx`](../../frontend/src/App.tsx). The OIDC callback lands in that
  existing path for free.

---

## 2. The one divergence from §20.3, and why

§20.3 step 2 says: *"Write `OidcIdentityProvider.verify_access_token`: fetch and
cache JWKS … verify signature, `iss`, `aud` and `exp` locally"*, and step 5 says
`/auth/login`, `/auth/refresh` and the refresh cookie disappear. That describes
the SPA holding an IdP token and bearing it to the API.

**Three reasons this plan does the exchange server-side instead, and the first
one is decisive.**

1. **Google's access tokens are opaque.** They are not JWTs and carry no claims
   a resource server can verify — only the ID token is a JWT. Following §20.3
   literally would mean bearing an *ID* token to the API, which the OIDC spec's
   own security considerations warn against, or calling Google's introspection
   endpoint on every single request, which puts the IdP on the hot path that
   §20.3 step 2 is explicitly written to keep it off. A design that works
   against Keycloak and not against Google is not the design to ship for a
   setting whose whole selling point is *any issuer*.
2. **The client secret never reaches the browser.** The exchange is
   server-to-server over TLS, with PKCE on top.
3. **Sessions stay ours.** Revoking a session, disabling an account, the
   fifteen-minute access token and the reuse-detecting refresh rotation in
   [`local.py`](../../backend/app/infra/identity/local.py) all keep working
   unchanged, because the thing being revoked is still a `sessions` row — not
   something an external issuer owns and we can only wait out.

**What does not change from §20.3:** the identifier space, the matching rule
(step 5), the namespaced subject (step 3), unknown claim values never
auto-creating anything (step 4), the local break-glass administrator (step 7),
and testing without a Keycloak (the synthetic RS256 fixture).

§20.3 should be amended to point here once Phase 1 lands. A plan that has been
overtaken by a build and does not say so is how the next person implements the
wrong thing.

---

## 3. Configuration — the whole surface

Every value below is inert unless `AUTH_PROVIDER=oidc`. Added to
[`core/config.py`](../../backend/app/core/config.py) beside the existing
`auth_provider`.

| Setting | Default | What it is |
|---|---|---|
| `AUTH_PROVIDER` | `local` | `local` \| `oidc`. **The switch this whole plan exists for.** |
| `OIDC_ISSUER` | `""` | As it appears in `iss`. Discovery is `{issuer}/.well-known/openid-configuration`. Google: `https://accounts.google.com`. Keycloak: `http://localhost:8080/realms/datamind` |
| `OIDC_CLIENT_ID` | `""` | From the IdP |
| `OIDC_CLIENT_SECRET` | `""` | `SecretStr`. Confidential client |
| `OIDC_REDIRECT_URL` | `""` | Must match a registered redirect URI **exactly**, scheme and path included |
| `OIDC_POST_LOGIN_PATH` | `/` | Where the browser lands once the session exists |
| `OIDC_SCOPES` | `openid email profile` | Non-sensitive at Google, so no app review |
| `OIDC_DISPLAY_NAME` | `SSO` | What the button says. *"Sign in with Acme SSO"* is a different instruction from *"Sign in with Google"* |
| `OIDC_PROVIDER_ID` | `oidc` | Namespace for `external_subject`, stored `{provider_id}~{subject}`. **Not decoration** — a bare `sub` collides the day a second issuer appears |
| `OIDC_SUBJECT_CLAIM` | `sub` | §20.3 step 1 |
| `OIDC_GROUPS_CLAIM` | `groups` | Read and **logged**, never applied — [§9](#9-what-this-plan-deliberately-does-not-build) |
| `OIDC_ROLES_CLAIM` | `roles` | Same |
| `OIDC_ALLOWED_EMAIL_DOMAINS` | *empty* | Who may be **provisioned**. A domain (`acme.com`, matching subdomains) or a full address (`ada@acme.com`). **Empty refuses every account that does not already exist** — D5 |
| `OIDC_JIT_PROVISIONING` | `true` | Off means invite-only: sign-in is refused unless the account exists |
| `OIDC_DEFAULT_ROLE` | `Normal User` | What a provisioned account gets, and the only thing it gets |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `true` | An unverified email is treated as worse than none — it is the one an attacker would choose |
| `OIDC_TRANSACTION_TTL_SECONDS` | `600` | How long the browser has to come back with a code |
| `OIDC_TRANSACTION_COOKIE_NAME` | `datamind_oidc_tx` | |
| `OIDC_LEEWAY_SECONDS` | `60` | Clock skew tolerated on `exp`/`iat` |
| `OIDC_JWKS_CACHE_SECONDS` | `3600` | An unannounced key rotation costs at most this long |

**Fail loudly at startup, not at sign-in.** `AUTH_PROVIDER=oidc` with any of
issuer, client id, secret or redirect URL missing must refuse to boot, in
[`main.py`](../../backend/app/main.py)'s lifespan beside `ensure_admin`. A
deployment that starts and then cannot sign anybody in is worse than one that
does not start — the second tells you immediately, in the place you are already
looking.

---

## 4. Phase 0 — Configuration and the provider, inert

**Nothing imports the new module. `make test` proves the tree still builds and
that the settings load.**

### 4.1 Deliverables

1. **`core/config.py`** — `auth_provider: Literal["local", "oidc"]` and the
   twenty settings in [§3](#3-configuration--the-whole-surface). The existing
   §20.3 docstring is rewritten to point at this plan and to state D1.
2. **`domain/ports/identity.py`** — a third Protocol, `FederatedIdentityProvider`,
   with `authorization_url` and `complete` and deliberately **no**
   `issue_session` / `rotate_session` / `verify_access_token`. What it does not
   declare is the point: a federated sign-in ends in an `AuthenticatedIdentity`
   and the session built from it is DataMind's own.
3. **`infra/identity/oidc.py`** — the provider. Discovery + JWKS caching
   (process-wide, lock-guarded, with a single refetch on an unknown `kid`),
   `authorization_url` (state, nonce, PKCE `S256`), `complete` (state check,
   code exchange, ID-token verification), and `_resolve_user` ([§8](#8-account-matching--the-rule-the-rest-of-this-document-serves)).
4. **The transaction cookie codec** — `encode_transaction` / `decode_transaction`,
   a short-lived HS256 JWT over `jwt_secret` carrying `state`, `nonce`,
   `code_verifier` and a `typ` that stops it being confused with a session
   token. A signed cookie rather than a table: this is per-browser, single-use,
   dead in ten minutes, and read once by the process that wrote it. A row would
   buy nothing and would need sweeping.
5. **Migration `0037`** — a **unique** index on `users.external_subject`. The
   column has existed since `0001` and is entirely NULL, so the index is
   instant and the migration is reversible. Uniqueness is what makes "one
   external identity, one account" a fact about the database rather than a
   property of the code in `_resolve_user`.

### 4.2 The verification rules, spelled out

These are the lines a review should stop on.

| Check | Rule |
|---|---|
| Algorithm | An explicit allowlist — `RS256/384/512`, `ES256/384/512`, `PS256`. Asymmetric only, checked **before** any key is fetched. `alg: none` and an HMAC token verified against a readable public key are the two classic JWT forgeries and both die here |
| Signature | The JWKS key whose `kid` matches. An unknown `kid` refetches **once** (that is what a key rotation looks like from here); a single-key JWKS with no `kid` is accepted, "the first of several" is not |
| `iss` | Exact match against `OIDC_ISSUER` — **plus** the scheme-less form, because Google is documented to mint both `https://accounts.google.com` and the bare host. One named alternative, never a substring test that would also accept `accounts.google.com.evil.tld` |
| `aud` | Exact match against `OIDC_CLIENT_ID` |
| `exp`, `iat` | Required, with `OIDC_LEEWAY_SECONDS` |
| `nonce` | Required, constant-time compare against the transaction. Missing or wrong means this token was minted for some other sign-in — which is what a replay looks like |
| `state` | Constant-time compare, before the code is spent |
| Discovery | The document's own `issuer` must equal `OIDC_ISSUER`. That equality is what makes the `iss` check mean anything |

**The IdP's own error text is logged and never returned.** A wrong secret or an
unregistered redirect URI is a client-configuration fact that belongs in an
operator's log, not on a sign-in screen an unauthenticated stranger is reading.

### 4.3 How we know it worked

- `make test` green, `make lint` green — in particular the import-linter
  contracts: `infra` may import `services` (for `assign_by_name`) but
  `app.domain` must stay free of everything, and the new Protocol must not drag
  anything in.
- `python -c "from app.core.config import Settings; Settings()"` under both
  values of `AUTH_PROVIDER`.
- `alembic upgrade head` then `downgrade 0036` then up again, on a populated
  clone.

---

## 5. Phase 1 — The two routes, and the session hand-off

### 5.1 Deliverables

1. **`GET /auth/oidc/login`** → 307 to the issuer, having set the transaction
   cookie. Takes an optional `?next=` (a **relative path only** — an open
   redirect here is a phishing primitive, so anything with a scheme or a host
   is dropped for `OIDC_POST_LOGIN_PATH`).
2. **`GET /auth/oidc/callback`** → exchange, verify, resolve, `issue_session`,
   set `raymand_refresh` via the existing `_set_refresh_cookie`, clear the
   transaction cookie, 302 to the SPA. On failure, 302 to the SPA with
   `?sso_error=<code>` — an unauthenticated visitor should land on the login
   screen with a sentence, not on a JSON problem document.
3. **`GET /auth/config`** — unauthenticated, and the only new *read* the SPA
   needs: `{provider, sso: {enabled, label, start_url}, password_login}`. It
   says nothing an attacker does not learn by looking at the login page.
4. **`_refuse_service` applies to the OIDC routes too.** A machine identity does
   not do an authorization-code flow; the database `CHECK` forbids the column
   anyway, and the refusal should say *rotate its key* rather than 500.
5. **Audit rows** — `oidc.login`, `oidc.provisioned`, `oidc.linked`,
   `oidc.denied`. The `denied` one matters most: a stranger being refused by the
   allowlist is exactly what an operator wants to be able to count.

### 5.2 Three details that are easy to get wrong

- **`SameSite=Lax` is correct and must stay.** The callback is a top-level GET
  navigation, so the cookie is sent and set normally. `Strict` would break the
  return trip; `None` would be a gratuitous CSRF surface.
- **The refresh cookie's `path=/api/v1/auth` is unchanged**, so the SPA's
  existing `/auth/refresh` call finds it. Do not widen the path "to be safe".
- **`REFRESH_COOKIE_SECURE=true` is required in any real deployment**, and on
  the Lightning URL ([§12](#12-testing-it-on-lightning)) it is required in
  development too, because that origin is HTTPS.

### 5.3 How we know it worked

`tests/unit/test_oidc_authentication.py`, built on the §20.3 recipe — **no
Keycloak, no network**. An RS256 keypair in a fixture, the public half served as
a static JWKS dict, tokens minted with `pyjwt`, and `httpx` intercepted by a
`MockTransport`. The cases:

| | Asserts |
|---|---|
| Happy path | code → session; `users.external_subject` is set; the response sets `raymand_refresh` |
| Expired `exp` | refused |
| Wrong `aud` | refused |
| Wrong `iss` | refused — including the `accounts.google.com.evil.tld` shape |
| Unknown `kid` | one refetch, then refused |
| `alg: none` and HS256-signed-with-the-public-key | refused before any fetch |
| Missing / wrong `nonce` | refused |
| Wrong / absent `state` | refused, **before the code is exchanged** |
| Expired transaction cookie | refused with *"took too long"* |
| `email_verified: false` | refused under the default |
| Unknown email, allowlist empty | refused, no user row written |
| Unknown email, allowlist matches | user created, `Normal User` assigned, **and nothing else** |
| Known email, first sign-in | bound: `external_subject` set, `password_hash` cleared, **same `users.id`** |
| Known email already bound to a *different* subject | refused, not rebound — **the D3 test** |
| A `SERVICE` principal's address | refused with the rotate-your-key sentence |
| `capabilities` / `roles` claims in the token | **ignored**; `ctx.capabilities` still comes from the database |

That last row belongs in
[`test_authz_seams.py`](../../backend/tests/unit/test_authz_seams.py) as well as
here, because it is that file's whole subject.

---

## 6. Phase 2 — The login screen

[`LoginPage.tsx`](../../frontend/src/pages/LoginPage.tsx) keeps its scene, its
rotating tagline and its About link. It gains one call and two states.

```
   AUTH_PROVIDER=local            AUTH_PROVIDER=oidc
   ┌───────────────────┐          ┌───────────────────┐
   │  Email            │          │ ┌───────────────┐ │
   │  [             ]  │          │ │ Sign in with  │ │   ← the whole screen
   │  Password         │          │ │ Google        │ │
   │  [             ]  │          │ └───────────────┘ │
   │  [   Sign in   ]  │          │                   │
   └───────────────────┘          │  Use a password   │   ← a disclosure, closed
        unchanged                 └───────────────────┘      by default
```

1. **`auth.config()`** on mount. While it is in flight the card shows nothing
   rather than the wrong thing — a password form that vanishes a beat later is
   worse than a spinner.
2. **Under `oidc`**, the SSO button is the screen, labelled from
   `OIDC_DISPLAY_NAME`. It is a plain link to `/api/v1/auth/oidc/login` — a
   `fetch` cannot follow a cross-origin redirect to an IdP, and trying is the
   classic first bug here.
3. **Break-glass is a closed disclosure**, *"Use a password"*, that reveals
   today's form. Present but not offered.
4. **`?sso_error=` is read once, rendered in the existing `ErrorNote`, and
   stripped from the URL** so a refresh does not replay it.
5. **Under `local`, the screen is byte-identical to today.** No SSO button, no
   disclosure, no layout shift.

Also: **the About link must keep working**, since it is the one page on both
sides of the sign-in wall.

**How we know it worked:** `npm run typecheck && npm run build && npm test`,
then Playwright against the real app in both modes and both themes — and a
narrow viewport, because the rule that no page has two fixed columns below 700px
applies to a card that just grew a second sign-in path.

---

## 7. Phase 3 — Break-glass, and the operator's story

### 7.1 The rule, in one place

D4 lives in `LocalIdentityProvider.authenticate`, as one condition:

> Under `auth_provider == "oidc"`, a password authenticates an account whose
> `external_subject` is NULL, and no other.

In `authenticate` rather than in the route, because it is a fact about the
account and there is more than one caller — `PUT /auth/me/password` must refuse
for the same reason, and a second copy of the rule is how the two drift apart.
It is **not** a role check: it does not say *administrator*, it says *not
federated*, which is why it needs no capability and cannot be widened by
assigning somebody a role.

The refusal says what to do: *"This account signs in with {OIDC_DISPLAY_NAME}."*

### 7.2 The rest of the phase

- **`POST /users` under `oidc`** creates an account with no password, and the
  one-time-password panel in Administration → People hides itself. Generating a
  password nobody can use is a support ticket waiting to happen.
- **A startup warning** when `AUTH_PROVIDER=oidc` and the bootstrap admin has
  been bound to the issuer — the break-glass door has been locked from the
  inside, and it should be said out loud rather than discovered.
- **`docs/reference/access-control.md`** gains a short *"Who verifies a human"*
  section; `.env.example` gains the block from [§3](#3-configuration--the-whole-surface);
  `docs/status.md` and §20 of the access-control plan are updated to point here.

---

## 8. Account matching — the rule the rest of this document serves

`_resolve_user`, in order, and **the order is the security property**:

```
1.  by external_subject  ({provider_id}~{sub})   → the bound account
2.  by verified email, EXACTLY ONCE              → bind it: set external_subject,
    (only if no row holds that subject yet)        clear password_hash
3.  by provisioning                              → only if JIT is on AND the
                                                   domain allowlist matches
```

- A row already bound to a **different** subject is **refused, never rebound**.
  Rebinding on email is the takeover this ordering exists to prevent, and the
  refusal is the same sentence an unknown account gets.
- **`users.id` never changes.** Every `owner_id`, grant, role assignment and
  team membership already points at it, so binding an account to an issuer
  re-grants nothing, re-shares nothing and re-assigns nothing. This is the
  sentence §20.3 step 6 exists to make true, and it is what makes the whole
  switch a config change rather than a migration.
- A `SERVICE` principal is refused at step 2 — and the database's
  `ck_users_service_no_external_subject` refuses it again underneath.
- An `INVITED` account that signs in through the IdP has accepted its
  invitation and becomes `ACTIVE`. A `DISABLED` account is refused, in both
  modes, by the same check.

---

## 9. What this plan deliberately does not build

Naming these is worth more than building them, because each is a thing somebody
will otherwise assume is there.

- **Group → team and role → claim mapping (S4, S5).** The claims are read and
  **logged** — so an administrator can see what actually arrives and bind it
  later — and applied to nothing. Applying them needs `teams.(provider_id,
  source_id)` and `roles.(provider_id, source_id)` and an admin screen to set
  the bindings. §20.4's hardest rule is that **no claim auto-creates a
  privileged role or team**, and the cheapest way to keep it is to map nothing
  yet.
- **Single logout / back-channel logout.** `/auth/logout` revokes the DataMind
  session and leaves the IdP session alone. Worth doing; not this.
- **Multiple issuers at once.** One `OIDC_PROVIDER_ID`, one issuer. The
  namespaced `external_subject` is what makes a second one possible later
  without a data migration, which is the whole reason it is namespaced now.
- **Client-credentials for machines.** Service keys are untouched and keep
  working under both modes, exactly as §20.3 step 5 says.
- **Any change to the authorizer.** Not one line. If this plan makes you open
  `infra/authz/`, something has gone wrong.

---

## 10. Failure modes, and what each one says

| What happened | Who sees what |
|---|---|
| IdP unreachable | *"Could not reach the identity provider."* Log has the URL and the transport error |
| Wrong client secret | Screen: *"The identity provider rejected this sign-in."* Log: the IdP's own `invalid_client` |
| Redirect URI not registered | Google shows **its own** error page and never reaches us. The log is silent — which is itself the diagnostic, and [§11](#11-setting-up-google) step 3 is the fix |
| Discovery `issuer` ≠ `OIDC_ISSUER` | Refused with both values named. A misconfiguration, caught before any token is trusted |
| Clock skew | `OIDC_LEEWAY_SECONDS`; beyond it, *"has expired. Try again."* |
| Email not verified | Refused, with the reason |
| Domain not allowed | *"This identity is not allowed to sign in to this deployment."* An `oidc.denied` audit row |
| Account bound elsewhere | *"Already linked to a different single sign-on identity. Ask an administrator."* |
| Everything is broken | `AUTH_PROVIDER=local`, restart. Every local account works again immediately; every bound account has no password and needs an administrator to set one |

---

## 11. Setting up Google

No billing, no domain ownership, no app verification — about five minutes.

1. **A Google Cloud project**, then **APIs & Services → OAuth consent screen**.
   Choose *External* and **leave it in Testing**, then add your own address
   under *Test users*. In Testing status only listed test users can sign in,
   which is both the sandbox you want and the reason no verification review is
   needed.
2. **Credentials → Create OAuth client ID → Web application.** This yields
   `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
3. **Authorised redirect URI** — the one value that must match character for
   character, including the trailing path. For Lightning, [§12](#12-testing-it-on-lightning).
4. Scopes stay `openid email profile`: all non-sensitive.

```bash
AUTH_PROVIDER=oidc
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=<…>.apps.googleusercontent.com
OIDC_CLIENT_SECRET=<…>
OIDC_DISPLAY_NAME=Google
OIDC_ALLOWED_EMAIL_DOMAINS=your-exact-address@gmail.com
```

**Two Google-specific facts this plan is shaped around**, both already argued
above: access tokens are opaque ([§2](#2-the-one-divergence-from-203-and-why)),
and `iss` arrives in two forms ([§4.2](#42-the-verification-rules-spelled-out)).

### Keycloak instead

A `docker-compose.oidc.yml` that **CI never starts** — the treatment
`docker-compose.replicas.yml` already gets. One `keycloak:latest` in dev mode, a
`datamind` realm, one confidential client, one test user.

```bash
AUTH_PROVIDER=oidc
OIDC_ISSUER=http://localhost:8080/realms/datamind
OIDC_CLIENT_ID=datamind
OIDC_CLIENT_SECRET=<from the realm>
OIDC_DISPLAY_NAME=Keycloak
OIDC_ALLOWED_EMAIL_DOMAINS=example.com
```

Nothing in the provider is Google-specific or Keycloak-specific: both are
reached through discovery.

---

## 12. Testing it on Lightning

**It works, and the specifics below were verified against this Studio on
2026-09-20 rather than assumed.**

| Checked | Result |
|---|---|
| Vite dev server on a public HTTPS origin | `https://5173-01kywvjp5xzhbm3tx04td2c9eg.cloudspaces.litng.ai` → 200 |
| The API through that same origin | `POST /api/v1/auth/login` → a real backend 401. The `/api` proxy in [`vite.config.ts`](../../frontend/vite.config.ts) carries the whole API |
| Port 8000 publicly | **not exposed** — Lightning's proxy 404s it. Irrelevant: everything goes through 5173 |
| Outbound from the `api` container to Google | discovery fetched; token endpoint and JWKS URI resolved |

So:

```bash
OIDC_REDIRECT_URL=https://5173-01kywvjp5xzhbm3tx04td2c9eg.cloudspaces.litng.ai/api/v1/auth/oidc/callback
REFRESH_COOKIE_SECURE=true          # that origin is HTTPS
```

HTTPS, a real public TLD, no wildcard — everything Google requires of a
non-localhost redirect URI.

**Three caveats.**

1. **The hostname is this cloudspace's.** It survives restarts; it does not
   survive duplicating the Studio, which would mean a new URL and a new
   registered redirect URI.
2. **Google never fetches that URL** — only the browser does, on the way back.
   So even if the shared port asks for a Lightning login first, the flow
   completes.
3. **That origin is public.** Which is precisely why D5 makes an empty
   allowlist refuse everyone: *"sign in with Google"* on a public URL, with JIT
   provisioning and no allowlist, means any Google account on earth.

---

## 13. Files touched

| File | Phase | Change |
|---|:--:|---|
| `backend/app/core/config.py` | 0 | `auth_provider` gains `"oidc"`; twenty `oidc_*` settings |
| `backend/app/domain/ports/identity.py` | 0 | `FederatedIdentityProvider` Protocol |
| `backend/app/infra/identity/oidc.py` | 0 | **new** — the provider |
| `backend/app/infra/db/migrations/versions/0037_*.py` | 0 | **new** — unique index on `users.external_subject` |
| `backend/app/main.py` | 0 | startup validation of the OIDC block |
| `backend/app/api/v1/auth.py` | 1 | three routes; `_refuse_service` extended |
| `backend/app/api/deps.py` | 1 | one provider factory. **`get_ctx` is not touched** |
| `backend/app/api/schemas.py` | 1 | `AuthConfigResponse` |
| `backend/app/infra/identity/local.py` | 3 | the D4 condition in `authenticate` |
| `backend/tests/unit/test_oidc_authentication.py` | 1 | **new** — §5.3 |
| `backend/tests/unit/test_authz_seams.py` | 1 | a claim never becomes a capability |
| `frontend/src/api/client.ts`, `types.ts` | 2 | `auth.config()` and its type |
| `frontend/src/pages/LoginPage.tsx` | 2 | the button, the disclosure, `sso_error` |
| `.env.example` | 3 | the documented block |
| `docker-compose.oidc.yml` | 3 | **new** — Keycloak, never started by CI |
| `docs/reference/access-control.md`, `docs/status.md`, `…/user-management-and-access-control.md` §20 | 3 | point here |

**Not touched, and worth stating:** `infra/authz/`, `services/policy.py`,
`domain/ports/authz.py`, every route guard, and `get_ctx`. If a diff in this
work reaches any of them, the seam has been missed.

---

## 14. The checks that gate each phase

| Phase | Gate |
|:--:|---|
| 0 | `make test` · `make lint` (the eight contracts) · `alembic upgrade head` and back · settings load under both modes |
| 1 | the §5.3 table green · `make authz-check` · `make test` |
| 2 | `npm run typecheck && npm run build && npm test` · Playwright in both modes, both themes, narrow viewport |
| 3 | a real Google sign-in end to end on the Lightning URL · a real Keycloak sign-in via `docker-compose.oidc.yml` · break-glass proven by breaking the secret on purpose and getting back in |

Phase 3's last check is the one to actually perform rather than reason about.
**Several past bugs in this repo only surfaced end-to-end** — and an auth
change that is wrong is wrong in the one way that locks everybody out.

---

## 15. Progress ledger

Tick a box in the commit that lands the work, never ahead of it.

### Phase 0 — Configuration and the provider, inert
- [ ] `auth_provider` accepts `oidc`; the twenty settings · *`Settings()` under both modes*
- [ ] `FederatedIdentityProvider` Protocol · *`make lint`, contracts green*
- [ ] `infra/identity/oidc.py`: discovery, JWKS cache, PKCE, verification · *unit tests*
- [ ] Transaction cookie codec, with a `typ` a session token cannot satisfy
- [ ] Migration `0037`, unique index on `external_subject` · *up, down, up on a populated clone*
- [ ] Startup refuses `oidc` with an incomplete block · *boot test*

### Phase 1 — The two routes, and the session hand-off
- [ ] `GET /auth/oidc/login` · *307, transaction cookie set, `next` sanitised*
- [ ] `GET /auth/oidc/callback` · *session issued, refresh cookie set, no token in the URL*
- [ ] `GET /auth/config` · *unauthenticated, truthful in both modes*
- [ ] Service principals refused on all three
- [ ] Four audit actions, `oidc.denied` among them
- [ ] The §5.3 table green · *no Keycloak, no network*
- [ ] **`get_ctx` unchanged** · *`git diff` on `deps.py` shows only the factory*

### Phase 2 — The login screen
- [ ] `auth.config()` and its type
- [ ] SSO button, a link and not a `fetch` · *Playwright reaches the IdP*
- [ ] Break-glass disclosure, closed by default
- [ ] `sso_error` rendered once and stripped from the URL
- [ ] **Byte-identical under `local`** · *screenshot diff*

### Phase 3 — Break-glass, and the operator's story
- [ ] The D4 condition in `authenticate`, and `PUT /auth/me/password` obeys it
- [ ] `POST /users` under `oidc` mints no password; the OTP panel hides
- [ ] Startup warning when the bootstrap admin is federated
- [ ] `.env.example`, `docker-compose.oidc.yml`
- [ ] §20 of the access-control plan points here · *the divergence recorded, not silent*
- [ ] A real Google sign-in, end to end, on the Lightning URL
- [ ] A real Keycloak sign-in, end to end
- [ ] Break-glass proven by breaking the client secret on purpose
