# Token usage in the UI — the implementation plan

> **Status: proposed, not built.** This is the build order for
> [llm-observability-v2.md](llm-observability-v2.md), which is the
> specification and stays the authority on *what* and *why*. This document is
> only *in what order*, *how it is verified*, and *where the commits land*.
>
> **Written:** 2026-09-13. Phase one —
> [token-accounting.md](token-accounting.md) — is built and merged, and is why
> there are numbers to show at all.

---

## 0. What already exists, and what this adds

Migration `0023` landed per-call accounting. Confirmed against the tree:

| Table | Columns it already has |
|---|---|
| `runs` | `prompt_tokens`, `completion_tokens`, `cost_usd`, `actor_id` |
| `report_runs` | the same four |
| `semantic_jobs` | the same four |
| `run_steps` | `prompt_tokens`, `completion_tokens`, `llm_latency_ms`, `llm_calls` |

**No column is added by this plan.** The specification's §*Two things to
settle* defers cache read/write tokens precisely so this stays a read-and-render
phase, and that decision is taken: **input + output only.** The one migration
here (`0030`) adds a *capability to two roles* and touches no data table.

What this plan adds is four things and nothing else:

1. `Capability.USAGE_READ`, seeded to **Administrator** and **Auditor**.
2. `GET /usage/*` — a read endpoint over the union of the three tables.
3. A **Token usage** rail section with a stacked bar chart.
4. Tokens and cost folded into the **existing** step-chip expansion in chat.

---

## 1. Decisions taken before any code

Each of these is a thing the specification leaves open or a thing the tree
forces. Settled here so no phase has to stop and decide.

### 1.1 Three endpoints, not one

```
GET /usage/me                                  → everyone
GET /usage/users?from=&to=                     → usage.read
GET /usage/total?from=&to=                     → usage.read
```

`/usage/me` is ungated and needs no capability: **the scope is the caller**,
which is `ctx.user_id` in the query and cannot be widened by a parameter. The
other two carry `deps.needs(Capability.USAGE_READ)`.

**Why not one endpoint with a `user_id` parameter.** Because then the gate
becomes conditional inside the handler — *"needs `usage.read` unless the id is
your own"* — and access-control §4's checklist has one right answer per route,
not one per argument value. Three routes, three unambiguous gates, and
`make authz-check` can see all of them.

**Why `/usage/users` returns every person in one response** rather than a
per-user endpoint: the screen renders a list of people with a chart each, and a
loop of `allowed` calls is the pagination bug §4 names. One query, grouped.

### 1.2 The response shape is decided here

One shape, three uses. `UsageSeries` is what every endpoint returns:

```python
class UsageBucket(BaseModel):
    day: date
    prompt_tokens: int              # measured tokens only; nulls are not summed
    completion_tokens: int
    cost_usd: float | None          # None when nothing in the bucket was priced
    runs: int                       # how many operations are behind the figure

class UsageSeries(BaseModel):
    actor_id: UUID | None           # None on /usage/total
    actor: str = ""                 # a display name, never an address — audit's rule
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    runs: int
    #: How many of those operations reported no token count at all. Non-zero
    #: means every figure above understates, and the screen says so.
    unmeasured: int
    #: How many contributed tokens but no price. Non-zero means `cost_usd` is
    #: partial, and the screen says *that* — it never prints a bare total.
    unpriced: int
    buckets: list[UsageBucket]
```

`unmeasured` and `unpriced` are the whole of the specification's two carried-over
rules, made into data the screen cannot ignore. A partial total that does not
say it is partial is the failure both rules name, and a boolean would not tell a
reader *how* partial.

### 1.3 Bucketing is daily, and the window is server-clamped

`date_trunc('day', created_at)` at UTC. Default window: the last 30 days. A
caller may narrow it; `MAX_WINDOW_DAYS = 366` caps it, because the union has no
`LIMIT` and an unbounded range over three growing tables is an outage waiting
for its first busy installation.

**The hour bucketing the specification mentions as an option is not built.** A
day is the smallest honest bucket the specification argues for, and an hourly
arm would be a second query, a second aggregation and a second axis format for a
window nobody has asked for yet.

### 1.4 The join is inner, and `/usage/total` does not join at all

Straight from phase one's §6 rule, and it is the one thing here most likely to
be "fixed" by a well-meaning later change:

- `/usage/me` and `/usage/users` **inner join** `users` on `actor_id`. A
  deleted user's rows leave the per-person view.
- `/usage/total` reads the three tables **with no join**, so those rows are
  still counted in the installation total.

The gap between the two is real and correct, and the screen states it — the
total tab shows how much of its spend has no living actor. An outer join would
attribute a departed person's spend to whoever remains, which is the one answer
that is wrong.

### 1.5 The chart is drawn, not planned

Decided in the specification and restated because it is the tempting shortcut:
`app/charts/` is for query results, where a model proposes and the platform
vetoes. A usage chart's shape is written here. The spec is built in a **DOM-free
module**, `usage-chart.ts`, and handed to the existing `VegaChart.tsx`.

Colours come from `palette.ts`. Input and output are the **first two categorical
series** of the current palette, so the chart is theme-correct for free and no
literal enters the tree.

### 1.6 The per-run breakdown is additive to `RunStep`

`RunStep` in `frontend/src/api/types.ts` grows three optional fields
(`prompt_tokens`, `completion_tokens`, `llm_calls`) and the step chip renders
them **only when non-null**. A node that made no model call (`validate`,
`execute`) shows exactly what it shows today. No new always-on chrome in chat.

### 1.7 What is not built

Named so no phase quietly grows: no cache read/write columns, no budgets or
quotas, no embeddings counting, no cross-model grouping, no backfill, no CSV
export, no hourly buckets, no per-run cost on the chat header.

---

## 2. How the commits work

**Every commit point below is numbered, named, and mandatory.** They are not a
suggestion to "commit as you go" — the list is the list. Stop at each one, run
the checks for that phase, commit exactly what that point describes, and move
on. A phase with four commit points produces four commits, in that order, even
when the work felt like one motion.

**Why this many.** Each commit point is a place the tree is green and a reviewer
could stop reading. A thirty-file commit saying "add usage screen" is one a
reviewer either takes on faith or re-derives from scratch, and `git bisect` over
it lands on a commit that does everything. Small commits also make the two
failure modes here cheap to undo: a wrong aggregation and a wrong access gate
are both single-commit reverts.

**The rhythm inside a phase** is implementation, then the tests for it. A fix
the tests force belongs in the **test** commit, not amended into the one before
it — the fix is part of what the test found, and squashing it hides the finding.

**Checks before each commit.** Backend commits (1–16, and 26–29's backend half):

```bash
cd backend && make test          # ~1,790 tests, well under a minute
make lint                        # ruff + the eight import-linter contracts
make authz-check                 # phases 1 and 3 — it is the point of them
```

Frontend commits (17–25, and commit 3's SPA half):

```bash
cd frontend && npm run typecheck && npm run build && npm test
```

**Phase 4 straddles the two** — commit 16 is backend, commit 17 is frontend, and
commit 18's tests are both — so run whichever side the commit touches, and both
where it touches both. Phase 7 runs both throughout. `make guard` is **required in no phase** — nothing here
touches `sqlguard/` or a connector. Node is not on `PATH` in this environment;
see [docs/development.md](../development.md).

A commit point whose checks do not pass is not a commit point yet. Fix it there;
do not carry a red tree into the next one.

**Commit messages** follow the tree: `type(scope): a declarative sentence`,
lowercase, no trailing period, ending with the attribution line the repository
uses.

> **Never push.** Every commit is local. The user pushes from their own
> terminal, and nothing in this plan runs `git push` under any circumstances.

---

## Phase 1 — the capability

*Six lines of enum and seed, and the five places access-control §6 says a
capability has to appear. Nothing reads it yet.*

### 1A · The enum member

`Capability.USAGE_READ = "usage.read"` in
[domain/value_objects/authz.py](../../backend/app/domain/value_objects/authz.py),
in the **oversight** group beside `AUDIT_READ` — same group because it answers
the same kind of question about the same kind of subject.

That module's docstring says *"eighteen capabilities"*; make it **nineteen**
here. It is code, and it is now wrong.

> **Commit 1** — `feat(authz): usage.read joins the oversight capabilities`

### 1B · The migration

`0030_usage_capability.py`, `down_revision = "0029"`: insert a
`role_capabilities` row for **Administrator** and **Auditor**. Downgrade deletes
both.

- Administrator's seed in `0024` is **by enumeration**, deliberately — a
  nineteenth capability is a reviewed line, not something Administrator silently
  acquires. This migration is that line.
- It is **not** a `PRIVILEGED_CAPABILITY`: reading token counts cannot mint an
  administrator, and a service account that reports installation spend is a
  legitimate thing to want.

Check `make migrate` runs clean against `.data/db` — a **real local database**
with real dashboards in it. A failure there is a data problem, not a test
problem.

> **Commit 2** — `feat(db): 0030 seeds usage.read to Administrator and Auditor`

### 1C · The route dependency and the SPA's vocabulary

`UsageReadDep = Annotated[RequestContext, Depends(needs(Capability.USAGE_READ))]`
in [api/deps.py](../../backend/app/api/deps.py), beside `AuditReadDep`. Add
`usage.read` to the `Capability` union in
[frontend/src/permissions.tsx](../../frontend/src/permissions.tsx).

Both halves in one commit because they are the same fact written in two
languages, and a backend capability the SPA cannot name is one no screen can
branch on.

> **Commit 3** — `feat(authz): both sides of the wire can name usage.read`

### 1D · The rulebook

Name `usage.read` in [access-control.md](../reference/access-control.md) §1's
capability examples, and add its row to
[security.md](../reference/security.md)'s capability discussion. `security.md`
line 953 says *"one of **eighteen** app-wide verbs"* — it is **nineteen**.

This is not deferred to Phase 7: `test_authz_conformance.py` asserts every
`Capability` member is named in the rulebook, so the enum and the document move
together or the suite is red.

> **Built, with one correction to the sentence above.** That assertion is
> `str(capability) in text or "capability" in text`, and the rulebook contains
> the word "capability" — so it passes for a capability nobody has documented.
> The rulebook edit is required by this plan and was made, but it is **not**
> machine-enforced, and a later capability can be added without it. Worth
> tightening when somebody is in that file; out of scope here.

> **Commit 4** — `docs(authz): the rulebook names usage.read, and counts nineteen`

### 1E · The tests

Extend `backend/tests/unit/test_roles.py`:

- the independent literal in that file gains `usage.read` for **Administrator**
  and **Auditor** and for nobody else. That file asserts the seeded sets from a
  literal written apart from the migration, so both move together or the test
  fails — which is the point of it.
- a Normal User, a Viewer, a Knowledge Manager and a BI Engineer hold it not.

`test_authz_conformance.py` passes **unchanged**; commit 4 is what makes that
true.

**Phase gate:** `make test`, `make lint` and `make authz-check` all green.

> **Commit 5** — `test(authz): usage.read reaches two seeded roles and no others`

---

## Phase 2 — the usage query

*Pure aggregation in a service. No route, no DTO, nothing calls it. It is the
piece most likely to be subtly wrong, so it gets a phase and a test file of its
own before anything renders it.*

### 2A · The window, and the shape it returns

New `backend/app/services/usage_service.py`, with the scaffolding only:

- `MAX_WINDOW_DAYS = 366` and `DEFAULT_WINDOW_DAYS = 30`, plus the clamp that
  applies them. The union has no `LIMIT`, and an unbounded range over three
  growing tables is an outage waiting for its first busy installation.
- The internal result dataclasses the three functions will return, mirroring
  §1.2's shape — frozen, no I/O, so the aggregation below has somewhere to land
  before anything about SQL is decided.

The module imports only SQLAlchemy and the ORM models, so it sits where every
other service sits and no import-linter contract moves.

> **Commit 6** — `feat(usage): a clamped window and the shape a usage answer takes`

### 2B · The per-actor aggregation

`for_actor(db, actor_id, *, since, until)` and
`per_actor(db, *, since, until)`.

One `UNION ALL` over `runs`, `report_runs`, `semantic_jobs`, grouped by
`date_trunc('day', created_at)` and `actor_id`, **inner joined** to `users`.

- `SUM` ignores nulls in SQL, which is what both carried-over rules want: a null
  token count contributes nothing and is counted separately into `unmeasured`; a
  null `cost_usd` contributes nothing and is counted into `unpriced`. **Nothing
  coalesces either to zero.**
- `cost_usd` is `None` — not `0.0` — when no row in the scope was priced.

> **Commit 7** — `feat(usage): per-person daily spend, summed across all three tables`

### 2C · The installation total

`installation(db, *, since, until)` — the same union with **no join to
`users`**, so a deleted actor's rows are still counted here after they have left
the per-person view.

The function also returns how much of its spend has no living actor, because the
screen states that gap rather than hiding it (§1.4).

> **Commit 8** — `feat(usage): the installation total keeps what a departed actor spent`

### 2D · The per-run rollup

`by_node(db, run_id)` — `run_steps` grouped by `name` for one run. Phase 4 is
its only caller; it lands here because it is the same kind of query and belongs
beside its siblings.

> **Commit 9** — `feat(usage): one run's spend, broken down by the node that caused it`

### 2E · The tests

New `backend/tests/unit/test_usage_service.py`:

- **A user's total equals the sum of their buckets.** The invariant the whole
  screen rests on, and the one most likely to rot.
- A run with `prompt_tokens IS NULL` raises `unmeasured` and adds **nothing** to
  the token total — it is not summed as zero.
- A run with tokens and `cost_usd IS NULL` raises `unpriced` and adds nothing to
  the cost; the series' `cost_usd` stays `None` when *every* row is unpriced.
- **Deleting a user drops their rows from `per_actor` and keeps them in
  `installation`** — the inner-join rule, asserted as behaviour rather than
  trusted to a comment.
- All three tables contribute: a report run and a semantic job land in the same
  person's series as their chat runs.
- A window wider than `MAX_WINDOW_DAYS` is clamped, not served.
- An empty scope returns a zero series with an empty bucket list, never a 500.
- `by_node` attributes a run's steps to the right names and omits the nodes that
  made no model call.

**Phase gate:** `make test` green. No route exists yet, so nothing user-visible
changed.

> **Commit 10** — `test(usage): nulls are never zero, and a departed actor leaves the per-person view`

---

## Phase 3 — the read endpoints

*Wires the query to HTTP. This is where access control is decided, so it is
where `make authz-check` earns the phase.*

### 3A · The DTOs

`UsageBucket` and `UsageSeries` in
[api/schemas.py](../../backend/app/api/schemas.py), per §1.2. `actor` is a
**display name, never an email** — the rule `AuditEntry` already states, for the
same reason: a usage screen answers *"who spent this"* with something a person
recognises, and an address is a personal identifier the screen has no need of.

> **Commit 11** — `feat(api): the DTOs a usage answer is rendered through`

### 3B · `/usage/me` — the ungated route

New `backend/app/api/v1/usage.py` with one route, registered in
`api/v1/__init__.py`.

It takes `CtxDep` and scopes to `ctx.user_id`. **No capability**, because the
scope *is* the caller and no parameter can widen it. Literal path, so no
`/{id}` ordering question arises.

> **Commit 12** — `feat(api): everyone can read their own token usage`

### 3C · The two gated routes

`/usage/users` and `/usage/total`, both carrying `UsageReadDep`.

Separate from commit 12 on purpose: this is the commit where a gate could be
wrong, and it should be readable on its own. `/usage/users` returns every person
in one grouped response — a loop of `allowed` calls is the pagination bug
access-control §4 names.

> **Commit 13** — `feat(api): usage.read opens every person's usage and the installation total`

### 3D · The client

Client functions in
[frontend/src/api/client.ts](../../frontend/src/api/client.ts) and the two types
in `types.ts`. Typecheck only — no screen consumes them yet.

> **Commit 14** — `feat(api): the SPA can ask for usage, in all three scopes`

### 3E · The tests

New `backend/tests/unit/test_usage_api.py`:

- `/usage/me` returns **only the caller's** rows, for a caller holding no
  capability at all.
- `/usage/me` **cannot be widened**: no parameter changes its scope, asserted
  against the route signature rather than by trying one.
- `/usage/users` and `/usage/total` **403** for a caller without `usage.read`,
  and the refusal names the capability.
- An **Auditor** reaches both; a Normal User reaches neither.
- `/usage/users` returns a display name and **no email** for every row.
- The default window is 30 days when none is given.

`test_authz_conformance.py` passes **unchanged** — it walks the route table, and
three new routes that each resolve a `ctx` and gate correctly are what it checks
for. That is the real gate of this phase.

**Phase gate:** `make test`, `make lint`, `make authz-check` all green.

> **Commit 15** — `test(api): /usage/me cannot be widened and the other two refuse without usage.read`

---

## Phase 4 — tokens in the step-chip expansion

*The smallest user-visible change in the plan, and the one that makes
`run_steps` earn its columns.*

### 4A · The wire

`RunStep` in `api/schemas.py` grows `prompt_tokens`, `completion_tokens` and
`llm_calls` — all `int | None`, all defaulting to `None`. The run-detail
serialiser passes them through from the ORM rows.

No new query: the columns are on rows already being read.

> **Commit 16** — `feat(api): a run step reports what it cost, or says nothing`

### 4B · The chip

`RunStep` in `frontend/src/api/types.ts` grows the same three, optional.
`StepTrail` in [chat.tsx](../../frontend/src/components/chat.tsx) renders a
token chip **only where `llm_calls` is non-null and greater than zero** — a node
that called no model is unchanged, pixel for pixel.

The run's own total goes in the trail's header line beside the existing
step-count and duration, and **only when the run has a measured total**. Never
`0 tokens`, which reads as a measurement and is not one.

> **Commit 17** — `feat(chat): a step chip says what the step cost, where it cost anything`

### 4C · The tests

Backend, extending the run-detail suite:

- a step that made no model call serialises all three as `null`
- a step that did serialises the counts the row holds
- **the run's total equals the sum of its steps' counts** — the same invariant
  Phase 2 asserts over the aggregate, asserted here over the wire

Frontend: `npm run typecheck && npm run build && npm test`. No new suite — the
change is JSX, and the fourteen DOM-free modules stay fourteen until Phase 5.

**Phase gate:** `backend/tests/unit/test_pipeline_events.py` passes **as
written**. It is the SSE-sequence contract; this phase touches serialisation,
not emission, so if it needs editing the change reached further than intended.

> **Commit 18** — `test(chat): a run's tokens equal the sum of its steps, over the wire`

---

## Phase 5 — the chart spec, DOM-free

*A fifteenth tested module. Built and tested before any screen imports it,
because it is arithmetic and arithmetic is what those suites exist for.*

### 5A · The spec builder

New `frontend/src/components/usage-chart.ts`:

- `usageSpec(buckets, palette, opts) -> VisualizationSpec` — a stacked bar,
  x = day (temporal), y = tokens (quantitative), colour = series.
- Series are `['Input', 'Output']` **today**, built from a list so a third and
  fourth segment is a series and not a rewrite — the specification's stated
  reason for deferring cache tokens.
- **No React import, no DOM.** One React import turns the suite into a thing
  that cannot run.
- Colours arrive as an argument from `palette.ts`; the module holds no literal.

> **Commit 19** — `feat(usage): the usage chart's spec is arithmetic, not a planned chart`

### 5B · The totals, and the two sentences about partiality

`usageTotals(series)` in the same module — the textual summary's numbers, plus
the two sentences the carried-over rules require: *"n operations reported no
token count"* and *"cost is known for n of m"*.

Its own commit because it is the half that enforces honesty, and it is the half
a later change is most likely to simplify away into a bare number.

> **Commit 20** — `feat(usage): a partial total says how partial it is`

### 5C · The suite, and the count in the map

New `frontend/src/components/usage-chart.test.ts`:

- an empty bucket list produces a valid spec, not a throw
- a bucket with zero output tokens still renders its input segment
- the series order is stable, so the stack does not reorder between renders
- `usageTotals` **never reports a cost when `unpriced` covers the whole scope**,
  and says how partial the total is when it is partial
- `usageTotals` states the unmeasured count when non-zero, and nothing when zero

Wire it up: `"test:usage": "node --experimental-strip-types
src/components/usage-chart.test.ts"` in `package.json`, appended to the `test`
script. Update CLAUDE.md's *"fourteen DOM-free modules"* to **fifteen** in this
same commit — that sentence counts, and a stale count is worse than no count.

**Phase gate:** `npm test` runs fifteen suites and the new one is among them.

> **Commit 21** — `test(usage): the spec survives an empty window and never prints a partial total as whole`

---

## Phase 6 — the screen

*The page, the rail row, and the route. Everything it needs already exists and
is tested.*

### 6A · Your own usage

New `frontend/src/pages/UsagePage.tsx`, first pass: the caller's own series
only — textual summary plus the stacked bar, and the 7 / 30 / 90-day window
picker that every figure on the screen respects.

Partiality is **stated in prose beside the number**, from `usageTotals` — never
a footnote, never colour alone.

> **Commit 22** — `feat(usage): a page for what you have spent`

### 6B · The rail row and the route

A `NAV` entry in [App.tsx](../../frontend/src/App.tsx), **below LLM providers
and above Administration**, with **no `needsAny`** — everyone has their own
usage to see. Plus the `<Route path="/usage/*">` entry.

Separate commit: this is the point the feature becomes reachable, and it should
be a line in the log somebody can find.

> **Commit 23** — `feat(usage): token usage is a rail section, visible to everyone`

### 6C · The privileged view

Behind `can('usage.read')`: a per-person section and a **Total** tab. Tabs are
`useMatch` sub-routes of `/usage`, per the router convention — the section owns
`/usage/*` and stays mounted across open and close.

The total tab states how much of its spend has no living actor (§1.4).

> **Commit 24** — `feat(usage): a usage.read holder sees everyone, and the installation total`

### 6D · Verification

- `npm run typecheck && npm run build && npm test` — fifteen suites green.
- Below 700px the page has no second fixed column. It is a single column
  already, so this is a check, not work.
- **Manual, and required rather than optional**, because several past bugs in
  this tree only surfaced end-to-end: sign in as an **ordinary user**, confirm
  the rail row is present and the page shows one series, then run
  `curl /api/v1/usage/users` with that user's token and confirm **403**. That
  last one is invariant I5 — the UI is an affordance, never a boundary — and it
  is the only way to check it.

Any fix this pass forces lands in this commit.

**Phase gate:** an ordinary user sees their own usage and cannot reach anyone
else's, from the screen **and** from `curl`.

> **Commit 25** — `test(usage): the screen builds, and an ordinary user's curl is refused`

---

## Phase 7 — documentation

*The last phase, and the one that is easiest to skip and most expensive to have
skipped. Nothing here changes behaviour.*

The tree's own rule: **tick it in the commit that lands the work.** Phases 1 and
5 already carry their own doc edits for exactly that reason — a capability not in
the rulebook fails `test_authz_conformance.py`, and a DOM-free count that says
fourteen when there are fifteen is a lie in the map. This phase is everything
that could only be written once the whole thing exists.

### 7A · The two plans

- **[llm-observability-v2.md](llm-observability-v2.md)** — flip the status
  banner from *"proposed, not built"* to built, dated, the way
  [token-accounting.md](token-accounting.md)'s banner was flipped. Keep the body
  as the record of *why*; add a short *"what shipped differently"* note for the
  two places this plan settled something it left open (daily-only bucketing,
  three endpoints rather than one).
- **This document** — tick §4's ledger.

> **Commit 26** — `docs(plans): the usage screen is built, and the spec says so`

### 7B · The map

**[CLAUDE.md](../../CLAUDE.md)**:

- `api/v1/` route list gains `usage`
- `services/` gains `usage_service`
- `frontend/src/pages/` gains `Usage` with its one-line description
- `components/` gains `usage-chart.ts` (`npm run test:usage`)
- the DOM-free list — already fifteen from Phase 5; **verify, do not re-edit**
- CLAUDE.md itself does **not** state a capability count; check rather than
  assume, and the two places that do are handled in Phase 1

> **Commit 27** — `docs: CLAUDE.md maps the usage service, route, page and module`

### 7C · The reference docs

- **[security.md](../reference/security.md)** — the `usage.read` row and one
  paragraph on why reading token counts is a capability: it is a record *about
  people*, the same argument `audit.read` makes, and an Auditor who reads spend
  and changes nothing is the role that justifies it. Note also that a usage
  figure is **counts, never content** — no prompt text, no question, no SQL ever
  reaches this screen. (The *eighteen → nineteen* fix on line 953 landed in
  Phase 1; verify it is there.)
- **[access-control.md](../reference/access-control.md)** — `usage.read` named
  in §1's examples. Landed in Phase 1; verify.
- **[frontend.md](../reference/frontend.md)** — the Usage section in the rail
  tour, and `usage-chart.ts` in the DOM-free module list.
- **[llm-calls.md](../reference/llm-calls.md)** — if it describes where token
  counts land, it now also says where they are read. Check; edit only if it
  claims there is no read path.

> **Commit 28** — `docs(reference): where usage is read, and who may read it`

### 7D · The front door

- **[docs/status.md](../status.md)** — what is built, and the two things this
  phase deliberately did not do (cache tokens, embeddings) so the next reader
  does not assume they were forgotten.
- **[README.md](../../README.md)** — one line under *What works today →
  Platform* (`### Platform`), which is where the cross-cutting surfaces are
  listed. One line; do not invent a section.

**Phase gate:** `make test`, `make lint`, `make authz-check`, and `cd frontend
&& npm run typecheck && npm run build && npm test`.
`test_authz_conformance.py`'s rulebook-staleness assertions are the machine half
of this phase. Then a read-through: every file listed above, checked against the
tree it describes. A checklist that runs ahead of the code is worse than no
checklist.

> **Commit 29** — `docs: status and README name the usage screen`

---

## 3. Commit summary

**Twenty-nine commits, in this order.** Every one of them local; none is ever
pushed.

| # | Phase | Message |
|---|---|---|
| 1 | 1 | `feat(authz): usage.read joins the oversight capabilities` |
| 2 | 1 | `feat(db): 0030 seeds usage.read to Administrator and Auditor` |
| 3 | 1 | `feat(authz): both sides of the wire can name usage.read` |
| 4 | 1 | `docs(authz): the rulebook names usage.read, and counts nineteen` |
| 5 | 1 | `test(authz): usage.read reaches two seeded roles and no others` |
| 6 | 2 | `feat(usage): a clamped window and the shape a usage answer takes` |
| 7 | 2 | `feat(usage): per-person daily spend, summed across all three tables` |
| 8 | 2 | `feat(usage): the installation total keeps what a departed actor spent` |
| 9 | 2 | `feat(usage): one run's spend, broken down by the node that caused it` |
| 10 | 2 | `test(usage): nulls are never zero, and a departed actor leaves the per-person view` |
| 11 | 3 | `feat(api): the DTOs a usage answer is rendered through` |
| 12 | 3 | `feat(api): everyone can read their own token usage` |
| 13 | 3 | `feat(api): usage.read opens every person's usage and the installation total` |
| 14 | 3 | `feat(api): the SPA can ask for usage, in all three scopes` |
| 15 | 3 | `test(api): /usage/me cannot be widened and the other two refuse without usage.read` |
| 16 | 4 | `feat(api): a run step reports what it cost, or says nothing` |
| 17 | 4 | `feat(chat): a step chip says what the step cost, where it cost anything` |
| 18 | 4 | `test(chat): a run's tokens equal the sum of its steps, over the wire` |
| 19 | 5 | `feat(usage): the usage chart's spec is arithmetic, not a planned chart` |
| 20 | 5 | `feat(usage): a partial total says how partial it is` |
| 21 | 5 | `test(usage): the spec survives an empty window and never prints a partial total as whole` |
| 22 | 6 | `feat(usage): a page for what you have spent` |
| 23 | 6 | `feat(usage): token usage is a rail section, visible to everyone` |
| 24 | 6 | `feat(usage): a usage.read holder sees everyone, and the installation total` |
| 25 | 6 | `test(usage): the screen builds, and an ordinary user's curl is refused` |
| 26 | 7 | `docs(plans): the usage screen is built, and the spec says so` |
| 27 | 7 | `docs: CLAUDE.md maps the usage service, route, page and module` |
| 28 | 7 | `docs(reference): where usage is read, and who may read it` |
| 29 | 7 | `docs: status and README name the usage screen` |

Every message ends with the repository's attribution line.

**`git push` appears nowhere in this plan and must not be run.** The user pushes
from their own terminal, at whatever point they choose.

---

## 4. Ledger

Tick each box **in the commit that lands it**, not afterwards.

**Phase 1 — the capability** — built 2026-09-13

- [x] 1 · the enum member — **landed with 2**, see the note below
- [x] 2 · migration `0030`
- [x] 3 · route dependency + SPA vocabulary
- [x] 4 · the rulebook
- [x] 5 · the tests

> **Commits 1 and 2 are one commit**, and could not be two. `test_roles.py`
> asserts Administrator's seeded set equals every `Capability` member, so the
> enum without the seed is a red tree and the seed without the enum names a
> word the build does not know. §2's rule — *a commit point whose checks do not
> pass is not a commit point* — outranks §3's count. **Phase 1 is four
> commits.** That assertion now spans the whole migration chain rather than
> `0024` alone, so the next capability added this way reads as the reviewed
> line it is rather than as a regression in the seed.
>
> Two things the plan did not predict, both cheap: `api/v1/roles.py` serves the
> capability catalog from two exhaustive dicts (`_GROUPS`, `_LABELS`) that
> `KeyError` on a member they do not name, so the nineteenth word had to be
> added there too; and `test_authz_vocabulary.py` asserts `len(Capability) ==
> 18`, which is a deliberate count and moved to 19.

**Phase 2 — the usage query** — built 2026-09-13

- [x] 6 · the clamped window and the return shape
- [x] 7 · the per-actor aggregation
- [x] 8 · the installation total
- [x] 9 · the per-run rollup
- [x] 10 · the tests

> **The departed-actor gap is narrower than §1.4 implies, and the test says so.**
> `actor_id` is `SET NULL` on all three tables, but `runs.owner_id` cascades
> through `conversations` and `report_runs.owner_id` cascades directly — so
> deleting somebody who *owned* what they asked deletes the rows outright and
> there is no spend left to orphan. The gap opens only where actor and owner
> differ, which is exactly what `actor_id` was added for: somebody asking
> through a thread or connection belonging to someone else. The rule and the
> query are unchanged; what changed is that the test models the case that can
> actually happen, and a production `unattributed` figure should be read with
> this in mind.
>
> Two smaller things: `day_of` compiles to `date_trunc` on Postgres and
> `date()` on SQLite, because the unit suite runs against SQLite and a query
> that exists only in production is a query nothing tests; and `conftest.py`'s
> `_TABLES` gained `report_runs` and `semantic_jobs`, without which a union
> that silently dropped an arm would have passed.

**Phase 3 — the read endpoints** — built 2026-09-13

- [x] 11 · the DTOs
- [x] 12 · `/usage/me`
- [x] 13 · the two gated routes
- [x] 14 · the client
- [x] 15 · the tests

> **There is a third DTO, and §1.2 implies it without naming it.** The
> installation total has to carry `unattributed` — §1.4 says the screen states
> the departed-actor gap, and a gap the wire drops is one the screen cannot
> state. So `UsageTotal` extends `UsageSeries` with the two counts rather than
> putting nullable fields nobody else uses on the shared shape. The SPA has
> three types for the same reason, where 3D says two.
>
> **The API tests run over an ASGI transport, not `TestClient`, and that is
> load-bearing.** `TestClient` drives the app from a worker thread while the
> unit fixture's SQLite connection belongs to the test's own, so on the first
> run every route that reached the database failed with a thread error and
> every route that refused before touching it passed — a permissions suite
> that is green precisely on the cases it is not testing. Any later phase
> adding an HTTP test against the `db` fixture inherits this.
>
> **Two mutations were run against commit 15 before it was made**, because
> §3E's claims are the kind that pass by accident: removing `UsageReadDep`
> from `/usage/users`, and widening `/usage/me` to read everybody. The first
> failed three tests immediately. The second **passed** — the grouped query
> orders by display name and the fixture had named the caller first, so a
> handler returning "everybody, first row" returned the right person by luck.
> The fixture now names the other person so they sort first. A scope test
> whose fixture sorts the caller to the front is testing nothing.
>
> The three aggregations were also run against the **real Postgres** app
> database read-only, since `date_trunc` and the `FILTER` clauses only ever
> compile on SQLite in the suite. They work, and the installation's own data
> already has an unpriced run in it — which is the case `unpriced` exists for,
> arriving before the screen that reports it.

**Phase 4 — tokens in the step chip** — built 2026-09-13

- [x] 16 · the wire
- [x] 17 · the chip
- [x] 18 · the tests

> **`RunRead` grew two fields, which 4A does not mention and 4C requires.**
> The run's own total has to be on the wire for 4B's header line, and for
> 4C's *"the run's total equals the sum of its steps"* — with only the steps
> serialised there is nothing to compare them against. So `prompt_tokens` and
> `completion_tokens` join `RunStepRead`'s three. Still no `cost_usd`
> anywhere on this path, per §1.7.
>
> **There is no run-detail suite to extend.** The closest thing is one
> `_hydrate_run` case inside `test_intersection.py`, whose subject is what a
> *shared* turn withholds. So Phase 4's tests are a new file,
> `test_run_detail_tokens.py`, and the fifth test in it is the intersection
> case restated for this phase: a withheld turn keeps its trail, and the
> counts are part of the trail — they are counts, not content.
>
> **`conftest.py`'s `_TABLES` gained five tables**, the ones `_hydrate_run`
> reads beside the run. Without them only the *withheld* branch could be
> serialised — the one branch that touches none of them, and so the one that
> would hide a dropped field. Two mutations were run: defaulting the step
> counts to `0` fails all seven, dropping `llm_calls` fails four.
>
> **`tokenCount` decides its unit on the rounded figure.** The obvious
> version branches on the raw one and spells 9,999 as `10.0k` and 10,000 as
> `10k` — two spellings one token apart — and 999,999 as `1000k`.

**Phase 5 — the chart spec** — built 2026-09-13

- [x] 19 · the spec builder
- [x] 20 · the totals and the partiality sentences
- [x] 21 · the suite, and fifteen in the map

> **`npm test` now runs sixteen suites, not fifteen.** §5C's phase gate says
> fifteen, counting the *modules*: there were fourteen DOM-free modules and a
> fifteenth suite in `scripts/`. Fifteen modules, sixteen suites. Both counts
> are stated in CLAUDE.md and again in `docs/development.md`, and the second
> file is not in §5C's list — a count that is right in the map and wrong in
> the manual is the same stale checklist the rule is about, so both moved here.
>
> **`palette` is nullable, and a screen passes `null`.** §1.5 wants the chart
> theme-correct "for free", and pinning a range into the spec is the one way
> to lose that: `VegaChart` sets `config.range.category` from `palette.ts` for
> the theme in force and re-embeds on a flip, but an encoding-level range
> outranks a config one, so a spec built with a palette is frozen in the theme
> it was built in. Null inherits the renderer's — which is still
> `palette.ts`'s first two categorical slots, still no literal here. The
> argument stays for the callers whose theme is fixed rather than followed
> (print, a pinned board) and for the suite, which needs the range somewhere
> it can read it.
>
> **`usageTotals` refuses a cost rather than trusting a null.** §5B asks it
> never to report one when `unpriced` covers the whole scope; the obvious
> reading is that `cost_usd` is already null there and nothing is needed.
> `SUM` returns null only when *every* row is null, so a `0.0` over a fully
> unpriced window means somebody coalesced the column — and a deployment
> reported as free is exactly the failure §5's rules exist to prevent. The
> refusal is a line of its own and a test of its own.
>
> **A token total is null rather than `0` where nothing was measured**, on the
> step chip's rule one layer down. A scope of genuinely free work still reads
> `0`; one whose every operation went unmeasured reads nothing at all, and the
> two are told apart by `unmeasured`, not by the arithmetic.
>
> Four mutations were run against the suite: trusting `cost_usd` when the
> scope is wholly unpriced fails one test, printing the unmeasured zero fails
> one, dropping the segments that drew nothing fails four, and removing the
> `order` channel fails two.

**Phase 6 — the screen**

- [ ] 22 · your own usage
- [ ] 23 · the rail row and the route
- [ ] 24 · the privileged view
- [ ] 25 · verification, including the `curl`

**Phase 7 — documentation**

- [ ] 26 · the two plans
- [ ] 27 · the map
- [ ] 28 · the reference docs
- [ ] 29 · the front door

---

## 5. The four things most likely to go wrong

Not a summary — these are the specific regressions this plan is shaped to
prevent, and each has a test named above that catches it.

1. **A null summed as zero.** It makes a real spend read as free and an average
   read as low. Phase 2's tests.
2. **An outer join "fixing" the departed-actor gap.** It attributes a former
   employee's spend to whoever remains. Phase 2's test asserts the gap is
   *there*.
3. **`/usage/me` growing a `user_id` parameter.** It is the shortest path from
   three honest routes to one route with a conditional gate, and it is how a
   usage screen becomes an information leak. Phase 3's test asserts the route
   takes no such argument.
4. **A React import in `usage-chart.ts`.** It turns a suite that runs into one
   that cannot, silently, and nothing in CI would notice because `npm test` is
   not in CI.
