# The learning loop — build plan

> **Status:** a plan, not a proposal. Written 2026-08-31 against `main`.
> The argument for *why* lives in
> [research/learning-loop.md](research/learning-loop.md) and
> [mvp2-plan.md §1.1](mvp2-plan.md#11-the-system-cannot-learn-and-that-is-the-whole-ballgame);
> this document is *what we build, in what order, and how we know it worked*.
> Read the status-banner convention in [architecture.md](architecture.md).
>
> **Four decisions were taken before writing this** (§0.2). They are recorded
> here rather than re-argued: everything below assumes them.
>
> **68 of 86 items.** **Phases 1–4 and 6 are complete** — the store is built,
> filled by hand *and* from traffic, read on the ask path, kept from rotting,
> measurable by its owner, and the loop closes back to the person who flagged an
> answer. **Phase 5 is built and shipped off**: `PROMPT_VERSION` is v9, the empty
> slot renders v8's bytes, and `knowledge_examples_enabled` defaults to false
> because its gate needs a provider key this environment does not have — which
> is also why Phase 0's three baselines are still unmade.
> [§13](#13-progress-ledger--what-is-done-what-is-not) is the
> ledger: what is already in the codebase and load-bearing (§13.1), then a
> checkbox per deliverable per phase, each with the check that proves its state.
> Tick a box in the commit that lands the work, never ahead of it.

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

*A connection owner can teach DataMind a question they care about, see the
system answer it with a **Verified** badge the next time anyone asks, and watch
a number that says whether teaching it helped.*

That sentence has four verbs and each is a phase group: **teach** (§3.2),
**answer** (§3.3), **see** (§3.4–3.5), **measure** (§3.7).

### 0.2 The four decisions

| # | Decision | What it rules out |
|---|---|---|
| **D1** | **The artifact is a parameterized question→SQL template.** A question pattern with typed slots, SQL with `:params`, parameters proposed automatically from the guard's AST at save time. | Literal pairs. Research §6.5 is explicit: retrofitting parameters onto pairs authored without them means re-curating everything, and a literal store's hit rate stays near zero. |
| **D2** | **Short-circuit first, few-shot second.** Phase 2 makes a close match skip generation entirely; Phase 5 injects templates into the prompt, and only if an eval arm says it did not regress. | Shipping both at once. The repo has twice measured prompt additions lowering execution accuracy (36% → 26%); moving two unknowns in one window makes the result unreadable. |
| **D3** | **The matcher is an interface with a lexical default.** `pg_trgm` (ships with `postgres:16-alpine`) always works; an embedding implementation is used only when the connection's LLM config exposes one. | pgvector as a hard dependency, and embeddings as a hard dependency. The loop degrades to lexical, never to nothing. |
| **D4** | **Anyone signed in may curate today; one function flips it to admin.** All write paths depend on `can_curate(ctx)` in [policy.py](../backend/app/services/policy.py), which returns `True` for any authenticated user until a settings flag says otherwise. | Scattered role checks. `Role.ADMIN` and `AdminDep` already exist in [deps.py](../backend/app/api/deps.py) — the flip is a function body and an env var, not a migration. |

### 0.3 The phases

| # | Phase | Size | Gate to start | Changes an answer? |
|---|---|:--:|---|:--:|
| **0** | [Fix the ruler](#31-phase-0--fix-the-ruler) | S | — | no |
| **1** | [The store and the curation surface](#32-phase-1--the-store-and-the-curation-surface) | M | Phase 0 done | **no** — the store is inert |
| **2** | [Match, short-circuit, badge](#33-phase-2--match-short-circuit-and-the-badge) | M | Phase 1 done | yes, without touching the prompt |
| **3** | [Capture: feedback, queue, backlog](#34-phase-3--capture-feedback-the-queue-and-the-backlog) | M | Phase 1 done (parallel with 2) | no |
| **4** | [Store health: staleness and conflicts](#35-phase-4--store-health) | S–M | Phase 2 + 3 | no |
| **5** | [Few-shot injection](#36-phase-5--few-shot-injection-behind-an-eval-gate) | M | Phase 0 numbers on paper | **yes, and it can regress** |
| **6** | [Benchmark and a score](#37-phase-6--benchmark-and-a-score-in-the-product) | M | Phase 1 (`role` column exists) | no |
| **7** | [Embedding matcher](#38-phase-7--the-embedding-matcher) | M | Phase 2 shipped and measured | yes, matching only |
| **8** | [Permissions hardening](#39-phase-8--permissions-hardening) | S | Phases 1–3 | no |

Phases 1–4 are the release worth shipping on its own: a store, a way to fill it,
a visible payoff, and a way to keep it from rotting. Phase 5 onward is the part
that has to earn its way in with a measurement.

### 0.4 The five stages, mapped to this repo

```
 ① CAPTURE            ② CURATE             ③ STORE              ④ RETRIEVE            ⑤ MEASURE
 feedback on an   →   a human writes   →   knowledge_       →   match + bind    →    benchmark on
 answer, plus the     or approves a        templates            → short-circuit       HELD_OUT rows,
 backlog from runs    template             (+ pg_trgm)          → few-shot            never on
                                                                                      RETRIEVABLE ones
 Phase 3              Phase 1, 3           Phase 1              Phase 2, 5, 7         Phase 6
```

---

## 1. The artifact — what a template is

### 1.1 Anatomy

One worked example, which is the thing the UI in §4.4 is designed to produce:

```yaml
question:     "revenue by month for {region} in {year}"
sql: |
  SELECT date_trunc('month', o.created_at) AS month,
         SUM(o.amount)                     AS revenue
  FROM   orders o
  WHERE  o.region      = :region
    AND  o.status     <> 'CANCELLED'
    AND  o.created_at >= :year
  GROUP  BY 1
  ORDER  BY 1
params:
  - name: region   type: string  comment: "one of EMEA, NA, APAC"
  - name: year     type: date    comment: "the first day of the year"
note:                "Cancelled orders are never revenue. Use `orders`, not
                      `sales_daily_rollup` — the rollup double-counts refunds."
literal_provenance:  HUMAN_AUTHORED
role:                RETRIEVABLE
status:              ACTIVE
schema_version:      7
```

Three fields carry more weight than they look like they do:

- **`note`** is the only free text in the design, and it is deliberately *not*
  rendered into any prompt in Phases 1–4. It is written for the **next curator**,
  not the model. Research Option E measured that more prose in the prompt lowers
  accuracy; a note that never reaches the prompt cannot.
- **`literal_provenance`** decides whether the template's literals may be shown
  under a restrictive disclosure policy. §5.2.
- **`role`** decides whether the template may be retrieved, benchmarked, or
  neither. It is the enforcement of research §6.4, and it is a column rather
  than a convention because a convention will not survive six months.

### 1.2 Parameters, and where they come from

**The curator does not type `:params`. The AST offers them.**

On save, `app/knowledge/params.py` walks the SQLGlot tree the guard already
produced and proposes a parameter for every literal it can classify:

| Literal in the statement | Proposed as | Why it is safe to propose |
|---|---|---|
| `o.created_at >= '2026-01-01'` | `:from_date` (date) | a comparison against a temporal column |
| `o.created_at BETWEEN a AND b` | `:from_date`, `:to_date` | same, paired |
| `o.region = 'EMEA'` | `:region` (string) | equality against a low-cardinality column |
| `o.amount > 10000` | `:threshold` (decimal) | comparison against a numeric measure |
| `o.status <> 'CANCELLED'` | **not proposed** | an exclusion inside a `<>`/`NOT IN` is almost always part of the *definition*, not the question |
| anything inside a `CASE`/`COALESCE` | **not proposed** | too likely to be business logic |

Each proposal is a **suggestion with a checkbox**, defaulted on for the first
two rows and off for the rest, and every proposal shows the exact literal it
would replace. No model call — this is a tree walk, and it is the one thing in
this design no competitor does, because none of them has a guard that already
parses the statement (research §6.5).

**Binding at match time is also deterministic** (§3.3). If a parameter cannot be
bound from the question with confidence, the template does **not** short-circuit
and the run proceeds normally. A half-bound template is a confident wrong
answer, which is the failure class this product exists to avoid.

### 1.3 The three roles

| `role` | Retrieved as a few-shot? | Scored by the benchmark? | Set when |
|---|:--:|:--:|---|
| `RETRIEVABLE` | ✅ | ❌ | the default |
| `BENCHMARK_ONLY` | ❌ | ✅ | curator ticks "use this to measure, not to answer" |
| `HELD_OUT` | ❌ | ✅ | assigned automatically to a fixed fraction at creation |

Enforced in the query that builds the candidate set, not in a comment. A
template may never be both at once — that is research §6.4's measurement trap,
and it is the single correction the research offers to
[mvp2-plan.md §A3](mvp2-plan.md#a3-benchmarks-and-a-score-in-the-product)'s
"the two features share a table". They share a *table*; they must not share a
*row's purpose*.

> **Note on short-circuiting vs. roles.** `BENCHMARK_ONLY` and `HELD_OUT`
> templates are excluded from few-shot retrieval **and** from the Phase 2
> short-circuit. A held-out question that gets answered from its own stored SQL
> is measuring nothing.

### 1.4 Status — what a template does when the world moves

```
                       ┌──────────┐
   author / approve →  │  ACTIVE  │  ← re-validation passes
                       └────┬─────┘
        schema moved and         two templates, same intent,
        the SQL no longer        different results
        resolves    │                        │
                    ▼                        ▼
              ┌──────────┐            ┌─────────────┐
              │  STALE   │            │ CONFLICTED  │
              └────┬─────┘            └──────┬──────┘
                   │  curator fixes it       │  curator archives one
                   └──────────┬──────────────┘
                              ▼
                        ┌──────────┐
                        │ ARCHIVED │   never deleted by the system
                        └──────────┘
```

`STALE` and `CONFLICTED` are **withdrawn from retrieval and from
short-circuiting**, kept visible, and surfaced in the curator's queue. This
mirrors the rule the semantic layer already follows: an invalid *generated*
entry is dropped, an invalid *human-written* one is flagged and kept, because
*"deleting a person's work to hide drift is worse than showing it."*

### 1.5 Template kinds beyond SQL — recorded, deferred

A "typed fact catalog" (measure / filter / field / glossary / join rule) was
considered and **deferred on purpose**: those shapes already exist as
`SemanticMetric`, `GlossaryTerm` and friends in
[app/semantic/models.py](../backend/app/semantic/models.py), validated on save
and rendered into the prompt. Building a second home for them would fork the
one artifact the product already curates well.

**The rule instead:** when a correction is *definition-shaped* it goes into the
semantic layer, and when it is *question-shaped* it becomes a template. The
Phase 3 review queue offers both buttons on the same correction, side by side,
so the curator decides rather than the router. This is research Option D folded
in as a complement, which is what it is best at.

---

## 2. The data model

### 2.1 `knowledge_templates`

```sql
CREATE TABLE knowledge_templates (
  id                   uuid PRIMARY KEY,
  connection_id        uuid NOT NULL REFERENCES database_connections(id) ON DELETE CASCADE,

  question             text NOT NULL,          -- "revenue by month for {region} in {year}"
  question_normalized  text NOT NULL,          -- lowercased, literals masked; the match key
  sql                  text NOT NULL,          -- guard-validated on write AND on every use
  params               jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{name,type,comment}]
  note                 text NOT NULL DEFAULT '',

  source               text NOT NULL,          -- MANUAL | CHAT_CONFIRMED | CHAT_CORRECTED | TILE | REPORT_BLOCK
  literal_provenance   text NOT NULL,          -- HUMAN_AUTHORED | MODEL_DERIVED          §5.2
  role                 text NOT NULL DEFAULT 'RETRIEVABLE',  -- | BENCHMARK_ONLY | HELD_OUT  §1.3
  status               text NOT NULL DEFAULT 'ACTIVE',       -- | STALE | CONFLICTED | ARCHIVED
  status_reason        text NOT NULL DEFAULT '',             -- shown verbatim in the UI

  schema_version       integer NOT NULL DEFAULT 0,           -- the snapshot it validated against
  referenced_tables    text[] NOT NULL DEFAULT '{}',         -- from the validation report
  conflicts_with       uuid[] NOT NULL DEFAULT '{}',         -- §3.5

  created_by           uuid REFERENCES users(id) ON DELETE SET NULL,
  verified_by          uuid REFERENCES users(id) ON DELETE SET NULL,
  verified_at          timestamptz,
  last_validated_at    timestamptz,
  hit_count            integer NOT NULL DEFAULT 0,
  last_hit_at          timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_knowledge_templates_question
    UNIQUE (connection_id, question_normalized)
);

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX ix_knowledge_templates_match
  ON knowledge_templates USING gin (question_normalized gin_trgm_ops);
CREATE INDEX ix_knowledge_templates_conn_status
  ON knowledge_templates (connection_id, status, role);
```

Design notes worth keeping:

- **`ON DELETE CASCADE` on the connection**, like `semantic_layers` — a template
  describes exactly one connection's schema and has no life without it.
- **`schema_version` mirrors `semantic_layers.schema_version`**, so the UI can
  say *"the schema has moved on underneath this"* using the drift language the
  semantic tab already has ([semantic-drift.ts](../frontend/src/components/semantic-drift.ts)).
- **`created_by` and `verified_by` are separate and both `SET NULL`.** A
  template mined from a tile was created by the system and verified by a person;
  deleting the person must not delete the knowledge.
- **`hit_count`** is what makes pruning possible: a template nobody has matched
  in ninety days is a maintenance cost with no return, and §4.7 surfaces it.

### 2.2 `knowledge_template_hits`

Every match attempt that produced an answer, so §6 can count without guessing.

```sql
CREATE TABLE knowledge_template_hits (
  id             uuid PRIMARY KEY,
  run_id         uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  template_id    uuid REFERENCES knowledge_templates(id) ON DELETE SET NULL,
  matcher        text NOT NULL,               -- LEXICAL | EMBEDDING
  score          double precision NOT NULL,
  outcome        text NOT NULL,               -- SHORT_CIRCUIT | FEW_SHOT | REJECTED_UNBOUND
                                              -- | REJECTED_STALE | OVERRIDDEN_BY_USER
  bound_params   jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_template_hits_template ON knowledge_template_hits (template_id, created_at);
```

`OVERRIDDEN_BY_USER` is written when someone clicks *Generate a fresh answer
instead* on a verified answer. **That single column is the honest measure of
whether the short-circuit is trusted**, and no vendor in the research publishes
its equivalent.

### 2.3 `answer_feedback`

```sql
CREATE TABLE answer_feedback (
  id              uuid PRIMARY KEY,
  run_id          uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  connection_id   uuid REFERENCES database_connections(id) ON DELETE CASCADE,
  user_id         uuid REFERENCES users(id) ON DELETE SET NULL,
  verdict         text NOT NULL,          -- CORRECT | WRONG | NEEDS_REVIEW
  comment         text NOT NULL DEFAULT '',
  state           text NOT NULL DEFAULT 'OPEN',   -- OPEN | RESOLVED | DISMISSED
  resolved_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  resolved_at     timestamptz,
  became_template uuid REFERENCES knowledge_templates(id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, user_id)
);
```

`became_template` is the loop closing, as one nullable FK. It is what lets the
UI tell the person who flagged an answer *what happened to their flag* — the
thing Genie does with a notification and nobody else does at all.

### 2.4 Migrations and the extension

One Alembic revision per phase, never a squashed one:

| Revision | Phase | Contents |
|---|:--:|---|
| `..._knowledge_templates` | 1 | `CREATE EXTENSION pg_trgm`, the table, both indexes |
| `..._knowledge_hits` | 2 | `knowledge_template_hits` |
| `..._answer_feedback` | 3 | `answer_feedback` |
| `..._benchmarks` | 6 | `benchmark_sets`, `benchmark_runs`, `benchmark_results` |

`CREATE EXTENSION pg_trgm` needs a role with sufficient privilege on the **app**
database. It is available in `postgres:16-alpine` with no image change — this is
the whole reason D3 chose it. The migration must be written so that a
pre-existing extension is not an error, and the matcher must degrade to a plain
`LIKE`-and-token comparison with a warning if the extension is genuinely absent,
rather than failing the connection.

---

## 3. The phases

Each phase carries: **the goal**, **backend**, **frontend**, **tests**,
**done when**, and **the risk it carries**. A phase is not done until its
"done when" is a fact somebody checked, not an intention.

### 3.1 Phase 0 — Fix the ruler

**Size S. Blocking. Nothing below this line means anything until it lands.**

Three measurements are broken right now, and every number Phases 2–7 produce
would inherit the breakage.

#### P0.1 — `runs.prompt_version` records a lie

[run_service.py:168](../backend/app/services/run_service.py#L168) writes
`self._settings.prompt_version`, and
[config.py:77](../backend/app/core/config.py#L77) defaults that setting to
**`"v2"`**. The constant that actually renders the prompt is
[`PROMPT_VERSION = "v8"`](../backend/app/pipeline/prompts/__init__.py#L14).
**Every run in the database claims v2 and none of them is v2.**

*Fix:* record the constant that produced the bytes. The setting stays as an
override for an experiment, but the run records what actually ran, resolved at
render time rather than read from config.

*Test:* a unit test that renders a prompt and asserts the run row's
`prompt_version` equals `prompts.PROMPT_VERSION`. It must fail on today's code.

#### P0.2 — Retrieval recall is 1.0 by construction

`_RETRIEVE_BUDGET_CHARS = 50_000`
([nodes/__init__.py:260](../backend/app/pipeline/nodes/__init__.py#L260))
against a `sales` fixture that estimates ~26,480 chars, so **every** eval
question takes `FULL_SNAPSHOT` and recall is 1.0 for free. Any Phase 5 or 7
claim that templates or embeddings improved retrieval is unfalsifiable today.

*Fix:* run the suite at a lowered budget (a runner flag, not a code edit), and
record the decision in `app/eval/suites/CHANGELOG.md` — post-change recall
numbers are not comparable to pre-change ones and someone will compare them.

#### P0.3 — The v7 → v8 semantic-layer baseline was never taken

The layer's render was fixed on 2026-08-30 and `PROMPT_VERSION` moved v7 → v8,
but the A/B has never been run against a prompt that *contains* the layer. The
runner still needs a way to pass `NodeDeps.semantic`.

*Fix:* a `--semantic on|off` arm on the runner, then one baseline run of each,
written into `docs/eval.md`.

**Done when:** three numbers are on paper in `docs/eval.md` — execution accuracy
with the layer off, with the layer on, and retrieval recall at a budget that can
actually miss. Until then Phase 5 is not allowed to start.

**Risk:** none technical. The risk is skipping it because it is unglamorous, and
then being unable to defend any number produced for the next three months.

---

### 3.2 Phase 1 — The store and the curation surface

**Size M. The store ships inert: no answer in the product changes.**

That is deliberate and it is the whole reason this is a separate phase. It puts
the guard's fifth entry point, the hostile-corpus replay, and the disclosure
decision in the tree *before* anything reads from the store — so if the read
path is wrong in Phase 2, the door it is coming through has already been proven.

#### Backend

A new self-contained package, in the shape of `app/semantic/`:

```
backend/app/knowledge/
  __init__.py       public surface: KnowledgeTemplate, normalize_question, propose_params
  models.py         Pydantic: KnowledgeTemplate, TemplateParam, ParamType
  normalize.py      question → question_normalized (lowercase, mask literals, strip punctuation)
  params.py         AST walk → proposed parameters (§1.2)
  bind.py           question + params → bound values, or None       (used in Phase 2)
  validate.py       template + snapshot → guard verdict + referenced_tables
  matcher.py        the Protocol + LexicalMatcher                    (Phase 2 fills this)
  compare.py        result-set equality, lifted from app/eval        (Phase 6 fills this)
```

**Import-linter:** add `app.knowledge` to the layered contract between
`app.semantic` and `app.domain`, plus an eighth contract *"knowledge is
self-contained"* forbidding `fastapi`, `sqlalchemy`, `litellm`, `app.infra`,
`app.api`, `app.services` — byte-for-byte the contract `app.semantic` already
carries. A package that parses SQL and normalises strings has no business
knowing what a session is.

Then:

- `app/services/knowledge_service.py` — CRUD, validation on save, the backfill
  reader. Owns every DB call; the package above owns none.
- `app/api/v1/knowledge.py` — mounted at
  `/connections/{connection_id}/knowledge`, mirroring
  [semantic.py](../backend/app/api/v1/semantic.py)'s `_owned()` scoping helper.
- `app/services/policy.py` gains:

```python
def can_curate(ctx: RequestContext, settings: Settings) -> bool:
    """Who may write connection knowledge.

    Today: anyone signed in. The product is single-player and the highest-value
    correction comes from the person who knew the answer. When user management
    lands, one env var makes this admin-only — every call site already asks
    this function, so nothing else moves.
    """
    if settings.curation_admin_only:
        return ctx.is_admin
    return True
```

with `curation_admin_only: bool = False` in `core/config.py`. **Every write
endpoint calls it. No endpoint checks `ctx.is_admin` directly.**

#### Frontend

- `frontend/src/components/knowledge.tsx` — the tab, built on the same
  primitives `semantic.tsx` uses (`Chip`, `Field`, `Modal`, `EmptyState`,
  `PrimaryButton`, `GhostButton`, `InlineEdit`, `relativeTime`, `dirOf`).
- `frontend/src/components/knowledge-template.ts` +
  `knowledge-template.test.ts` — the DOM-free logic: question normalisation
  preview, parameter rendering, status labels, drift explanation. This joins the
  nine existing logic suites that `npm test` actually runs, which is the only
  frontend test gate this repo has.
- `DataSourcesPage.tsx`: the tab tuple becomes
  `'settings' | 'schema' | 'semantic' | 'knowledge'`.
- `api/client.ts`: a `knowledge` namespace beside `semantic`.
- `api/types.ts`: `KnowledgeTemplate`, `TemplateParam`, `TemplateCheckResult`.

Design is specified in §4.3 and §4.4.

#### Tests

| File | What it proves |
|---|---|
| `tests/unit/test_knowledge_guard.py` | **the fifth entry point** — the full hostile corpus from `test_sqlguard_hostile.py`, replayed through template save *and* template execution. This is the file that must exist before Phase 2 reads anything. |
| `tests/unit/test_knowledge_params.py` | the AST walk proposes date bounds and categorical equalities, and does **not** propose literals inside `<>`, `NOT IN`, `CASE` or `COALESCE` |
| `tests/unit/test_knowledge_normalize.py` | two questions differing only in literals normalise to the same key; two differing in intent do not |
| `tests/unit/test_knowledge_api.py` | ownership scoping, `can_curate` gating in both settings, 404 on another user's connection |
| `tests/unit/test_knowledge_disclosure.py` | a `MODEL_DERIVED` template is not rendered when `HintBudget.value_lists` is false (§5.2) |

**Done when:** a curator can author a template against the `aurora` demo
connection, see the AST propose `:from_date`, save it, edit it, archive it — and
`make test` and `make guard` are green with the hostile corpus replayed through
the new door. **No chat answer behaves differently.**

**Risk:** the AST parameter proposal over-reaches and offers nonsense
parameters, which makes the feature feel unreliable on first contact. Mitigated
by the conservative table in §1.2 and by defaulting every proposal past the
first two to *off*.

---

### 3.3 Phase 2 — Match, short-circuit, and the badge

**Size M. The first phase that changes an answer — and it does it without
changing a single byte of the prompt.**

#### The graph branch

```
        START
          │
        ROUTE
          │
     ┌────┴──────────────┐
     │  (analytical)     │  (metadata / refusal — unchanged)
     ▼
   MATCH  ◄── new node
     │
     ├── hit, all params bound  ──►  VALIDATE ──► EXECUTE ──► INSPECT ──► PRESENT ──► CHART
     │        (state.candidate_sql = bound template SQL)
     │
     └── miss / unbound / stale ──►  RETRIEVE ──► DESCRIBE ──► CLARIFY ──► GENERATE ──► VALIDATE ──► …
```

**Why `MATCH` sits after `ROUTE` and jumps to `VALIDATE`:** the existing
`validate` node is already the guard's entry point for the pipeline, and it
already feeds the repair loop and `execute`. A hit that sets
`state.candidate_sql` and hands over to `VALIDATE` reuses every guarantee the
generated path has — re-validation against the *current* snapshot, the rewriter,
the row cap — with no new execution code. A stored template gets **no
exemption**, which is the invariant Part 5 of the MVP2 plan names first.

`RunState` gains three fields: `matched_template_id`, `match_score`,
`match_kind` (`LEXICAL | EMBEDDING`). They are the source of the badge, the hit
log, and the trace line.

#### Matching (D3)

```python
class TemplateMatcher(Protocol):
    async def match(
        self, question: str, connection_id: UUID, *, limit: int
    ) -> list[Candidate]: ...
```

`LexicalMatcher` normalises the question the same way the store did and scores
with `pg_trgm` similarity. **Two thresholds, not one:**

- `SHORT_CIRCUIT_THRESHOLD` — high, deliberately conservative (start at 0.85 and
  tune from the override rate, not from taste);
- `FEW_SHOT_THRESHOLD` — lower, unused until Phase 5.

A near-miss is not a hit. The cost of a miss is today's behaviour; the cost of a
false hit is a confident wrong answer.

#### Binding (deterministic, no model call)

`bind.py` fills each declared parameter from the question:

- **date/datetime** — a small grammar over the phrases people actually type
  ("last month", "in July", "2026", "Q3", "last 12 months", an ISO date),
  resolved against the run's clock;
- **string** — a literal from the question that matches the parameter's comment
  list or a value the schema snapshot already legitimately knows;
- **number/decimal** — a numeral in the question.

**Any parameter that cannot be bound cancels the short-circuit** and the run
falls through to generation, logged as `REJECTED_UNBOUND`. That log line is how
we learn which grammars to add.

#### The three-tier badge (design in §4.5)

| Tier | Condition | Shown |
|---|---|---|
| **Verified** | answered from a template, or from a semantic metric's exact SQL | green chip + **the matched question** + *Generate a fresh answer instead* |
| **Grounded** | generated, and every table it touched has a semantic-layer entry | quiet accent chip |
| **Generated** | generated against bare schema | one line of faint text — *say so plainly*, do not alarm |

**Showing the matched question is not optional.** It is the user's only defence
against a confident wrong match, it is the single best UI decision in the whole
research, and it costs one line.

#### Tests

- `tests/unit/test_knowledge_match.py` — threshold behaviour, normalisation
  round-trip, `BENCHMARK_ONLY`/`HELD_OUT`/`STALE`/`CONFLICTED` never match.
- `tests/unit/test_knowledge_bind.py` — the date grammar, and the
  cancel-on-unbound rule.
- `tests/unit/test_pipeline_graph.py` — extended: the `MATCH` node's two exits,
  and that a miss produces the **byte-identical** prompt it produced before this
  phase existed. That assertion is the promise `PROMPT_VERSION` stayed at v8
  through Phase 2; from Phase 5 it is the promise that v9's empty slot renders
  v8's bytes.
- A guard-failure-on-a-stored-template test: the run does **not** fail; the
  template is marked `STALE` with a reason and the run falls through to
  generation (research §6.2 — *fail as a value*).

**Done when:** asking a curated question on `aurora` returns in database time
with a Verified badge and no model call on the generate path, the override
button works and writes `OVERRIDDEN_BY_USER`, and hit rate / override rate are
queryable from `knowledge_template_hits`.

**Risk:** a false match. Mitigations, in order of strength: the conservative
threshold, the cancel-on-unbound rule, the visible matched question, the
one-click override, and the hit log that makes the false-match rate a number
rather than an anecdote.

---

### 3.4 Phase 3 — Capture: feedback, the queue, and the backlog

**Size M. Ships no accuracy. Ships the reason anyone curates.**

Research L6: *the hardest part of curation is not writing the template — it is
knowing which template to write.* The system already knows; it is sitting in
`runs`.

#### What gets built

1. **Feedback on an answer** — ✓ / ✗ / *Ask for review* in the chat answer
   footer, writing `answer_feedback`. Three verdicts, not two: Genie's *Yes /
   Fix it / Request review* split exists because "wrong" and "please look at
   this" are different asks.
2. **Save this answer as a template** — one click from a chat answer, prefilled
   with the question and the final SQL, opening the Phase 1 editor with the
   parameter proposals already computed. `source = CHAT_CONFIRMED`; if the user
   edited the SQL first, `CHAT_CORRECTED`.
3. **The review queue** — every `OPEN` feedback row for a connection, showing
   the question, the SQL, the comment, and the result the asker saw. Three
   actions: *Correct the SQL → save as template*, *Add to the semantic layer*
   (§1.5), *Dismiss with a reason*. Resolving writes back to the asker.
4. **The backfill** — every `dashboard_tiles` and `report_blocks` row with
   `sql_origin IN ('GENERATED_EDITED','HANDWRITTEN')` carries a `question` and a
   human-corrected `sql`. These are verified pairs that **exist right now and are
   read by nothing.** They arrive as *proposals* in the queue, never as approved
   templates, and `GENERATED_EDITED` proposals carry
   `literal_provenance = MODEL_DERIVED` because a human edited a statement whose
   literals the model chose (§5.2).
5. **The backlog** — the ranked work queue, derived from `runs`:

| Rank by | Signal | Where it comes from |
|---|---|---|
| 1 | asked often, never matched a template | `runs` grouped by normalised question, `LEFT JOIN` on hits |
| 2 | asked often, and flagged wrong | joined with `answer_feedback` |
| 3 | asked, and the run failed or was repaired | `runs.error_code`, `repair_count` |
| 4 | **words the retrieval did not recognise** | tokens in questions that match no table, column, comment, business name, synonym or glossary term |

Rank 4 is Power BI's *Review questions* feature, it is the one idea in the
research nobody else has copied, and it is nearly free here: the semantic layer
already holds the vocabulary to compare against.

**Done when:** a curator opens Knowledge → Backlog on a connection with real
traffic and sees a finite, ranked list of what to teach next, each row one click
from a prefilled editor.

**Risk:** feedback UI with no visible payoff is worse than none — people learn
their thumbs-down goes nowhere and stop. Mitigation: `answer_feedback.
became_template` plus a notification, so a flag that turned into knowledge says
so to the person who raised it. If Phase 3 ships without that link, it has
shipped a suggestion box.

---

### 3.5 Phase 4 — Store health

**Size S–M. The phase that stops the store decaying into noise.**

Research L5: a curated store degrades two ways, and both are handled.

#### Staleness

On every schema sync, re-validate every `ACTIVE` template against the new
snapshot. A template whose SQL no longer resolves becomes `STALE` with a
`status_reason` naming the object that moved (*"column `orders.region` no longer
exists"*), is withdrawn from matching and few-shot, and appears in the queue.
Never deleted, never silently dropped.

#### Conflicts — where DataMind beats the field

Fabric detects conflicts by *reasoning over SQL text* and reports a confidence
score of 1–5. **DataMind can run both statements and compare the result sets**,
because [app/eval/metrics.py](../backend/app/eval/metrics.py) already does
deterministic result-set comparison with a documented numeric tolerance.

Two templates whose normalised questions are near-duplicates and whose results
differ on the same connection is a **fact**, not an opinion. The check:

1. find pairs above a normalised-question similarity threshold;
2. bind both to the same parameter values;
3. execute both through the guard, read-only, row-capped;
4. compare with `app/knowledge/compare.py`;
5. differ → both marked `CONFLICTED`, `conflicts_with` populated, **the diverging
   rows shown to the curator** as the evidence.

Run it in `app/workers/`, on a schedule and on demand — never on the request
path, and never on a refresh path (invariant 3 of MVP2 Part 5 concerns model
calls; this makes none, but it still does not belong on a hot path).

#### Pruning

`hit_count = 0` after ninety days is surfaced, not enforced. Genie caps
instructions at 100 per agent for a reason; DataMind's version of that cap is
visibility plus a suggestion, because a template written for a question asked
once a year is not waste.

**Done when:** renaming a column on the `aurora` demo turns exactly the affected
templates amber with a readable reason, and seeding two contradictory revenue
templates flags both with the rows that prove it.

**Risk:** the conflict checker executes SQL on the customer's database on a
schedule. It must inherit the connection's read-only credentials, the row cap
and the disclosure policy without exception, and it must be switchable off per
connection.

---

### 3.6 Phase 5 — Few-shot injection, behind an eval gate

**Size M. The only phase in this plan that can make the product worse.**

`RetrievedContext` gains `examples: list[TemplateExample]`. `GENERATE_SYSTEM`
gains an `{examples}` slot that renders to the empty string when there are none
— so a connection with no templates produces a **byte-identical** prompt and
the existing baseline still holds. `PROMPT_VERSION` moves **v8 → v9**.

Budget: the examples block reuses the line-by-line fitting discipline the
semantic block already uses, and it is **last** in the priority order — schema
first, semantic layer second, examples third. Examples that crowd out the schema
are exactly the change that scored 36% → 26%.

**The gate, stated as a rule that can fail:**

> Ship few-shot injection only if execution accuracy on **held-out** questions
> is not worse than the Phase 0 baseline, at the same retrieval budget, on the
> same suite. Report both numbers — with templates and without — and report the
> split between questions that matched a template and questions that did not.

A `--templates on|off` arm on the eval runner, beside the existing `--comments`
arm. If the delta is negative on a small model, that is a result worth
publishing in `docs/eval.md`, not a reason to tune until it is positive.

**Rollback:** a per-connection `knowledge_examples_enabled` toggle, mirroring
`semantic_layer_enabled`. Off is byte-identical to v8 behaviour.

**Done when:** the arm has run, both numbers are in `docs/eval.md`, and the
decision to ship or not is written down with the evidence.

---

### 3.7 Phase 6 — Benchmark and a score, in the product

**Size M. This is where a customer gets a number of their own.**

#### The import-linter problem, solved cleanly

The contract *"eval is offline-only (never on the request path)"* forbids
`app.api`, `app.services`, `app.pipeline`, `app.domain`, `app.sqlguard` and
`app.infra` from importing `app.eval`. That contract stays.

**The comparator moves down, not up.** The pure functions — `values_equal`,
`_rows_equal`, `result_sets_match`, and the tolerance constants — move to
`app/knowledge/compare.py`, and `app/eval/metrics.py` imports them from there.
`app.eval → app.knowledge` is a permitted direction; nothing on the request path
gains an import of `app.eval`. One implementation, one set of tests, both
callers, contract intact.

#### Separate tables, deliberately

New `benchmark_sets` / `benchmark_runs` / `benchmark_results`, **not** the
existing `eval_runs` / `eval_results`. MVP2 Part 5's meta-rule is explicit: the
customer-facing instrument and the frozen developer suite must stay
architecturally separate *"or the two will contaminate each other within a
month."* Sharing a table is how that starts.

#### The three rules that keep the number honest

1. A template is retrievable **or** benchmarkable, never both — enforced by the
   `role` column in the query that builds each set (§1.3).
2. A fixed fraction is `HELD_OUT` at creation and never retrieved. **That is the
   only number worth putting in front of a customer.**
3. Report the split: accuracy on questions answered *from* a template and
   accuracy on questions answered *without* one are different numbers, and only
   the second moves for a reason. Genie's Evaluations tab shows one number; that
   is a weakness to improve on, not a design to copy.

Runs execute in `app/workers/`. Labels come from the deterministic comparator —
**no LLM judge.** Fabric fell back to one and gets *true / false / unclear*;
DataMind's comparator is the strongest starting position of the five products
compared in the research, and spending a model call per row to get a worse
answer would be a strange trade.

**Done when:** a connection owner opens Knowledge → Score, runs their set, and
sees two accuracy numbers with a history — without a developer.

---

### 3.8 Phase 7 — The embedding matcher

**Size M. A swap, not a rewrite — which is what D3 bought.**

`EmbeddingMatcher` implements the same Protocol. **Masked** question similarity
(DAIL-SQL): replace table names, column names and literal values with generic
tokens before embedding, so *"revenue in July for West"* retrieves the template
written for *"revenue in March for East"*.

Vectors go through the existing `LLMGateway` port — LiteLLM already supports
embedding endpoints, so no new Python dependency and no new deployment unit.
**Availability is a capability check on the connection's LLM config**, and when
it is absent the lexical matcher runs and the feature is simply quieter. The
learning loop degrades to lexical, never to nothing.

Two things that must be recorded or reproducibility breaks: the embedding model
id and its dimension, pinned per connection, and an index-staleness rule (a
template edit invalidates its vector; a model change invalidates all of them).

**Measure it, and remember the precedent:** FK-neighbour expansion once lifted
retrieval recall 70% → 86% with **flat** execution accuracy. Retrieval
improvements do not automatically become answer improvements, and this one
should be reported the same way.

---

### 3.9 Phase 8 — Permissions hardening

**Size S.**

- Flip `curation_admin_only` to `true` by default when user management lands.
  Every write path already asks `can_curate` — nothing else moves. (D4.)
- Route the review queue to a **connection owner** once ownership exists
  ([mvp2-plan §D1](mvp2-plan.md)). Until then it is scoped to the connection's
  creator, which is honest about the limitation rather than pretending.
- Every curation write emits an audit-log row — `audit_logs` already exists and
  MVP2 §D4 calls turning it on the best ratio in the document. A store of
  business logic that nobody can prove the provenance of is exactly the failure
  mode Microsoft shipped and documented.

---

## 4. UI and UX design

> This section is a **design brief a builder can implement from**, not a mood
> board. It is written against the incumbent visual world — the oklch tokens in
> [theme/tokens.ts](../frontend/src/theme/tokens.ts) and the primitives in
> [components/ui.tsx](../frontend/src/components/ui.tsx) — which is authority,
> not a starting suggestion. **No new colour, no component library, no new
> font.** Everything below is composed from what exists.

### 4.1 The brief

| | |
|---|---|
| **Mode** | **Operate.** Nobody arrives here to be persuaded. They arrive to fix something, and scanability, consistency and native expectations outrank expression. Brand lives in precise details, not in decoration. |
| **Who arrives** | The person who owns a connection — an analyst or a data-literate admin who knows the SQL and knows which answers are wrong. In the near term, any signed-in user (D4). They are irritated: they got a wrong answer, or they were told the system can be taught and want to see whether that is true. |
| **The one thing they must do** | Turn something they know into something the system knows — in under two minutes, without learning a new concept. |
| **What success looks like** | They ask the question again and it comes back with a **Verified** badge. That loop closing, visibly, on the first template, is the entire activation moment of this feature. |
| **What is uniquely true here** | The guard already parses every statement. That is why DataMind can *offer* parameters instead of demanding them, and why a conflict between two templates can be a **fact** (run both, compare rows) rather than a model's 1-to-5 confidence score. No competitor's UI can make either promise. |

**Anti-goals**, stated so they are not drifted into: a "knowledge base" that
looks like a wiki; a curation flow that requires reading documentation; a red
badge on ordinary generated answers; bulk "approve all suggestions"; a second
place to define metrics that competes with the semantic layer.

### 4.2 Information architecture — where this lives, and why

**Knowledge is a fourth tab on the connection detail**, beside
`Settings · Schema · Semantic`:

```
Data sources ▸ aurora (PostgreSQL)
┌──────────────────────────────────────────────────────────────────────┐
│  Settings   Schema   Semantic   Knowledge ⁴                           │
└──────────────────────────────────────────────────────────────────────┘
```

Three reasons, in order of weight: a template is scoped to exactly one
connection and dies with it, like the semantic layer; the curator is already on
this screen when they open the layer, and curation is the same job; and a
top-level nav item would promise a destination people visit, when this is a
place people are *sent* — from a wrong answer, from a flag, from the backlog.

The count on the tab is **only** the number of things needing a human. Zero
work, no number. A badge that always shows a total is decoration; a badge that
appears when there is work is a signal.

**Inside the tab: one work list, three sections, one detail pane.** Not
sub-tabs — tabs inside tabs bury the work and make "what should I do next" a
navigation problem:

```
┌── Knowledge ─────────────────────────────────────────────────────────────────┐
│ ┌ 🔍 Search   [All ▾] [Needs you ▾] ──────────┐ ┌──────────────────────────┐ │
│ │                                              │ │                          │ │
│ │  NEEDS YOU · 4                               │ │      detail pane         │ │
│ │  ⚠ 2 flagged answers                         │ │                          │ │
│ │  ⚠ 1 template went stale                     │ │  (the selected row,      │ │
│ │  ⚠ 1 conflict                                │ │   full width, editable)  │ │
│ │                                              │ │                          │ │
│ │  SUGGESTED · 12                              │ │                          │ │
│ │  Asked 9× this month, never matched          │ │                          │ │
│ │  From an edited dashboard tile               │ │                          │ │
│ │  …                                           │ │                          │ │
│ │                                              │ │                          │ │
│ │  TEMPLATES · 23                              │ │                          │ │
│ │  ✓ revenue by month for {region} in {year}   │ │                          │ │
│ │  ✓ top stores by {metric}, last {n} days     │ │                          │ │
│ │  …                                           │ │                          │ │
│ └──────────────────────────────────────────────┘ └──────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

Read the order top to bottom: **what is broken, what to teach next, what has
been taught.** The archive is last because it is the least urgent thing on the
screen, even though it is the thing the feature is named after.

The search and filter bar **sticks** — the same rule `semantic.tsx` already
follows, for the same reason: a store of forty templates scrolls past it in a
second.

### 4.3 The list row

Every row in every section is the same object with the same anatomy, which is
what makes three sections feel like one list rather than three widgets:

```
 ✓  revenue by month for {region} in {year}                     14 hits · 3d ago
    orders, stores                                        Verified by Maziyar A.
```

```
 ⚠  monthly revenue by area                              STALE · column moved
    orders, regions                          `orders.region` no longer exists
```

```
 ○  "which stores beat target last quarter"           asked 9× · never matched
    suggested from traffic                                        Teach this →
```

- The leading glyph is **status, not decoration**: `✓` active, `⚠` needs you,
  `○` not yet a template. Status is never carried by colour alone.
- Line two is always *what it touches* on the left and *why you are looking at
  it* on the right.
- The `{param}` braces render in `--text-dim` inside the question, so a
  parameterized template reads as a family at a glance. This is the one piece of
  syntax the curator has to learn, and showing it in the list is where they
  learn it.

### 4.4 The editor — the screen that matters most

This is where the AST proposal (§1.2) either feels like magic or like noise.
The whole design goal is: **the curator pastes SQL and gets offered a family.**

```
┌─ Teach a question ───────────────────────────────────────────────────  ✕ ─┐
│                                                                            │
│  QUESTION                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ revenue by month for {region} in {year}                              │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│  Matches questions like: "revenue by month for EMEA in 2026",              │
│  "monthly revenue, NA, 2025"                              ← live preview   │
│                                                                            │
│  SQL                                                        ✓ Valid · orders  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ SELECT date_trunc('month', o.created_at) AS month,                   │  │
│  │        SUM(o.amount) AS revenue                                      │  │
│  │ FROM orders o                                                        │  │
│  │ WHERE o.region = ⟦'EMEA'⟧                                            │  │
│  │   AND o.status <> 'CANCELLED'                                        │  │
│  │   AND o.created_at >= ⟦'2026-01-01'⟧                                 │  │
│  │ GROUP BY 1 ORDER BY 1                                                │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  PARAMETERS                     found by reading your SQL — nothing was sent│
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ ☑  'EMEA'        →  :region      string   one of: EMEA, NA, APAC     │  │
│  │ ☑  '2026-01-01'  →  :year        date     the first day of the year  │  │
│  │ ☐  'CANCELLED'   →  :status      string   inside a ≠ — usually part   │  │
│  │                                            of the definition          │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  NOTE FOR THE NEXT PERSON                                       optional   │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Cancelled orders are never revenue. Use orders, not                  │  │
│  │ sales_daily_rollup — the rollup double-counts refunds.               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│  Written for people, not the model. This never goes into a prompt.         │
│                                                                            │
│  ☐  Use this to measure accuracy, not to answer questions                  │
│                                                                            │
│                                        [ Cancel ]   [ Save template ]      │
└────────────────────────────────────────────────────────────────────────────┘
```

Five decisions in that layout, each with a reason:

1. **The literals are marked in the SQL itself** (⟦…⟧, rendered as a subtle
   `--accent-bg` highlight), and hovering a parameter row highlights its literal.
   The curator sees *what would change*, not an abstract list. This is the
   Teach-Q&A lesson: reinterpret in front of the person before they save.
2. **The question preview is live and shows two concrete examples.** Nobody
   understands `{region}` from the brace; everybody understands
   *"revenue by month for EMEA in 2026"*.
3. **The third proposal is offered and unticked**, with the reason written next
   to it. Showing the rejected candidate teaches the rule better than hiding it,
   and the curator occasionally knows better.
4. **"nothing was sent"** on the parameters header. This product's users are
   chosen partly because they care what leaves; an AST walk that makes no model
   call should say so, once, quietly.
5. **Validation is never guessed at locally** — the `✓ Valid · orders` chip
   comes from the same backend parser that will reject the statement on save,
   exactly as `semantic.tsx` does with `POST .../semantic/check`. A local
   "looks fine" that the server then rejects is the worst possible interaction.

**Entry from a chat answer** is the same modal, prefilled, with a fourth line
above the question: *"From your answer on 31 Aug"* — so the curator knows the
SQL is the one they just saw work.

### 4.5 The badge — the three tiers on a chat answer

The most consequential twenty pixels in the plan. It goes in the answer header,
above the existing step panel and *Generated SQL* disclosure in
[chat.tsx](../frontend/src/components/chat.tsx).

**Verified** — a green `Chip` with a check, and *two* things the research says
nobody should ship without:

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ ✓ Verified   answered from a saved question                          │
 │   “revenue by month for {region} in {year}”  ·  region=EMEA, year=2026│
 │   Not what you asked?  Generate a fresh answer instead                │
 └──────────────────────────────────────────────────────────────────────┘
```

The matched question **and the bound parameters** are shown. Power BI shows the
matched trigger phrase; showing the bindings as well is a small addition that
answers the next question a suspicious user has — *did it think July or June?*

**Grounded** — a quiet accent chip, nothing else:

```
 ◆ Grounded   every table it used is described in your semantic layer
```

**Generated** — no chip, no colour, one line of `--text-faint`:

```
 Generated against the bare schema.
```

**The most important design decision in this section is that "Generated" is not
a warning.** It is the default path, it is most answers, and dressing it in
amber would train every user to ignore amber within a week. Verified *earns* a
chip; Generated gets an honest sentence. Presence versus absence carries the
hierarchy — not a traffic light.

### 4.6 Feedback and the review queue

**On the answer**, in the footer beside the existing copy control — small,
unobtrusive, and never a modal:

```
   Was this right?   [ ✓ Yes ]  [ ✗ No ]  [ Ask for review ]
```

`✗ No` expands one textarea inline (*"What was wrong? — optional"*), `Ask for
review` expands the same plus a note that it goes to whoever owns the
connection. Both submit to a single line of acknowledgement in place:
*"Thanks — this is in the review queue."* No toast, no dialog, no confetti.

**In the queue** (a "Needs you" row's detail pane) the correction is a
**side-by-side**, because the curator's actual job is comparing two statements:

```
┌── Flagged 2 days ago by Sara · “this double-counts refunds” ──────────────┐
│                                                                            │
│  “total revenue last month”                                                │
│                                                                            │
│  WHAT IT ANSWERED                    │  YOUR CORRECTION                    │
│  SELECT SUM(amount)                  │  SELECT SUM(o.amount)               │
│  FROM sales_daily_rollup             │  FROM orders o                      │
│  WHERE month = '2026-07'             │  WHERE o.status <> 'CANCELLED'      │
│                                      │    AND o.created_at >= …            │
│  12 rows · 2.1s                      │  [ Run to compare ]                 │
│                                                                            │
│  This correction is:                                                       │
│   ◉ a question people ask     → save as a template                         │
│   ○ a definition             → add to the semantic layer                   │
│   ○ neither                  → dismiss with a reason                       │
│                                                                            │
│                                          [ Dismiss ]  [ Save and resolve ] │
└────────────────────────────────────────────────────────────────────────────┘
```

The radio group is §1.5's rule made into an interaction: the curator decides
whether a correction is question-shaped or definition-shaped, and the product
does not guess. *Run to compare* uses the Phase 4 comparator, so the curator can
see the two result sets differ before they commit — the same evidence the
conflict checker uses, offered to a human.

### 4.7 Stale, conflicted, and the health of the store

These are **rows in "Needs you"**, not a separate dashboard. A health screen
nobody opens is worse than a queue everybody already looks at.

A stale row's detail pane leads with the reason and the fix, in that order:

```
 ⚠  This template stopped working when the schema changed
    `orders.region` no longer exists.  Last worked 12 days ago, 31 hits.
    [ Edit the SQL ]   [ Archive ]
```

A conflict shows **the rows that disagree**, because that is the evidence and it
is the thing no competitor can show:

```
 ⚠  Two templates answer this differently
    “monthly revenue”            →  2026-07 │ 481,220
    “revenue by month”           →  2026-07 │ 512,940     ← differs
    The second includes cancelled orders.
    [ Keep the first ]  [ Keep the second ]  [ Edit both ]
```

Unused templates get the quietest possible treatment: a `--text-faint` line in
the Templates section — *"no matches in 90 days"* — and no action button. It is
information, not an accusation.

### 4.8 The score (Phase 6)

One strip at the top of the Knowledge tab, appearing only once a benchmark set
exists — never an empty chart:

```
 Accuracy    72%  on 25 held-out questions        ▁▂▃▄▅▆  last 6 runs
             91%  on questions you have taught     ← shown second, and smaller
             Last run 2 days ago · [ Run benchmark ]
```

**The held-out number is first, larger, and the one on the sparkline.** The
taught number is shown because hiding it would be dishonest, and shown second
and smaller because it is the number that goes up for the wrong reasons. Genie's
Evaluations tab shows one number; this shows two and says which one to believe.

### 4.9 States — every one of them, specified

| State | What the user sees |
|---|---|
| **First run, no templates** | Not an illustration. A three-item list of what to do *right now*, each a real link: **"Teach a question"** (opens the editor), **"12 questions people asked that nothing answered"** (the backlog, if traffic exists), **"3 corrected dashboard tiles you can turn into templates"** (the backfill, if any). If all three are empty, one sentence: *"Nothing to teach yet. Ask a few questions in chat first — this fills up from what people actually ask."* |
| **No schema snapshot** | The tab is reachable but authoring is blocked, with the existing sync affordance: *"Sync this connection's schema first — a template is checked against it."* |
| **Loading** | The existing `Spinner`, in the pane that is loading. Never a full-tab blocker: the list and the detail load independently. |
| **Saving** | The button becomes a spinner in place; the form stays interactive except for Save. The existing `UnsavedNote` pattern guards navigation away. |
| **Validation failed** | The `ErrorNote` primitive under the SQL box, carrying the guard's own message verbatim. The guard's language is precise and rewriting it into something friendlier loses the reason. |
| **`can_curate` is false** | Buttons are **absent, not disabled** — a disabled control the user can never enable is an insult. One line at the top of the list: *"Only administrators can add templates on this connection."* The list stays fully readable: seeing what the system knows is not a privilege. |
| **Empty search** | *"No templates match ‘churn’."* plus *"Teach this question"* as the action, because a failed search for a term is itself a curation signal. |
| **Overflow** | 40 tables' worth of templates is realistic; 400 is not, in this product's near term. The list virtualises above 200 rows and no sooner. |
| **Long question / long SQL** | The question clamps to two lines in a row, never in the detail. SQL scrolls inside its own container — the pane never scrolls sideways. |

### 4.10 Visual language

Everything maps to a token that already exists. **No new colour enters the
system for this feature**, which is the constraint that keeps it looking like
the product rather than like a bolted-on module.

| Meaning | Token | Where |
|---|---|---|
| Verified / active / healthy | `--green`, `--green-bg`, `--green-border` | the badge, the `✓` glyph, active rows |
| Needs a human — stale, conflicted, flagged | `--amber`, `--amber-bg` | the `⚠` glyph, "Needs you" |
| Guard rejection, destructive confirm | `--red`, `--red-bg` | validation errors, archive confirm |
| Grounded, parameters, selection | `--accent`, `--accent-bg`, `--accent-border` | the Grounded chip, the `⟦literal⟧` highlight |
| Not yet knowledge; quiet metadata | `--text-dim`, `--text-faint` | suggestions, "no matches in 90 days" |
| SQL | `--code-bg`, `--code-text` | every statement, everywhere |

Both themes come free, because every one of those has a dark and a light
definition in [tokens.ts](../frontend/src/theme/tokens.ts) and the light theme's
plum accent was chosen precisely so it does not collide with amber-warning or
red-error semantics.

**Type and space** follow the semantic tab exactly: content capped at a readable
measure rather than stretched across the pane, the same field rhythm from
`Field` / `FieldRow`, the same 28px gutter the `Tabs` strip already sets.

### 4.11 Copy deck

Curation copy is product copy and it is written once, here, so three developers
do not invent three voices.

| Where | String |
|---|---|
| Tab label | `Knowledge` |
| Empty title | `Teach this connection` |
| Primary action | `Teach a question` |
| Editor title (new / edit) | `Teach a question` / `Edit template` |
| Question field help | `Write it the way someone would ask it.` |
| Parameter section header | `Parameters` · sub: `found by reading your SQL — nothing was sent` |
| Note field help | `Written for people, not the model. This never goes into a prompt.` |
| Save | `Save template` |
| Verified badge | `Verified` · `answered from a saved question` |
| Override link | `Generate a fresh answer instead` |
| Grounded badge | `Grounded` · `every table it used is described in your semantic layer` |
| Generated line | `Generated against the bare schema.` |
| Feedback prompt | `Was this right?` → `Yes` / `No` / `Ask for review` |
| Feedback ack | `Thanks — this is in the review queue.` |
| Flag resolved (to the asker) | `Your flag on “{question}” became a saved template.` |
| Stale | `This template stopped working when the schema changed.` |
| Conflict | `Two templates answer this differently.` |
| Unused | `No matches in 90 days.` |
| Backlog row | `Asked {n}× this month, never matched` → `Teach this` |
| Backfill row | `From a dashboard tile you corrected` |
| Permission | `Only administrators can add templates on this connection.` |

Three rules behind those strings: **say what happened, not what the system
did** (*"this template stopped working"*, not *"validation failure"*); **never
call it AI** — it is a saved question; **never apologise** — a stale template is
schema drift, not a mistake anyone made.

### 4.12 Accessibility, localisation, responsiveness

- **Status is never colour alone.** Every state carries a glyph and a word.
  The `Chip` primitive already renders text; the `✓ / ⚠ / ○` leading glyphs make
  the list legible in greyscale.
- **Contrast** comes from the tokens, which were tuned for it — the light
  theme's `glyph-ink-l: 0.46` note in `tokens.ts` exists because 0.65 washed out
  on paper. Nothing here overrides a token with a literal.
- **The validation result is `aria-live="polite"`.** The curator's eyes are in
  the SQL box when the verdict lands.
- **Keyboard:** the parameter proposals are real checkboxes in a real list, tab
  order follows the visual order, and `Modal` already traps focus and restores
  it. The editor is reachable and completable without a mouse.
- **RTL, properly:** the product already ships `dirOf()` and a Persian edition
  of its planning docs. The question field and the note field get
  `dir={dirOf(value)}` — a Persian question must render right-to-left. **The SQL
  box is always `dir="ltr"`**, in both themes and both directions, because a
  bidi-reordered statement is unreadable and, worse, ambiguous. The list row
  applies the same rule per line.
- **Responsive:** list-plus-detail collapses to a single column under ~900px —
  the list becomes the screen and selecting a row pushes the detail with a back
  affordance, the pattern the settings surfaces already use. The editor modal
  becomes full-height on narrow screens. Below 600px this is a *reading* surface:
  authoring SQL on a phone is not a scenario worth designing for, and pretending
  otherwise produces a bad version of both.

### 4.13 Motion

Almost none, on purpose. This is an Operate surface and the incumbent app is
restrained — a `Spinner`, a `ProgressBar`, and little else.

Three uses, and no fourth: the detail pane cross-fades on selection
(120ms, opacity only); the inline feedback textarea expands on height (160ms,
`ease-out`); the parameter-literal highlight is an instant background change on
hover, not a transition. Everything respects `prefers-reduced-motion`. The
Verified badge **never animates** — a badge that draws attention to itself is a
badge nobody trusts.

### 4.14 Where the design will be tempted to go wrong

Written down now so it is a decision later rather than a drift:

1. **Making "Generated" scary.** The moment ordinary answers carry a warning,
   the badge system is noise. Verified earns colour; the default gets a sentence.
2. **A "suggested templates" bulk-approve.** Genie's knowledge mining *proposes*
   and a human *approves*, one at a time. A checkbox column with "Approve all"
   would fill the store with unreviewed statements in an afternoon and destroy
   the only property that makes it worth having.
3. **Turning the note into a prompt field.** It will be tempting — it is right
   there, and free text feels like it should help. Research Option E measured
   the opposite. If it ever renders into a prompt, it moves `PROMPT_VERSION` and
   goes through an eval arm like everything else.
4. **A wiki.** Rich text, folders, tags, ownership hierarchies. The list is a
   work queue; the moment it becomes a document tree, curation stops being a
   two-minute task.
5. **Hiding the SQL.** Every surface in this product shows the statement. A
   curation UI that abstracts it away would be the first place DataMind stopped
   showing its work.

---

## 5. Security, disclosure, and the guard

### 5.1 The guard: a fifth entry point that gets no exemption

DataMind has four guarded doors today, and each replays the hostile corpus in
its own test file — `test_sqlguard_hostile.py`, `test_query_service.py`,
`test_report_guard.py`, `test_dashboard_transfer.py`. None of the four is
privileged, and *"the moment one door is special, the guarantee is gone."*
**Templates are the fifth**, and
`test_knowledge_guard.py` lands in Phase 1 — before anything reads from the
store.

Two validations, answering different questions:

| When | Question it answers | On failure |
|---|---|---|
| **On save** | Is this SQL legal at all, against the current snapshot? | reject the save, show the guard's message verbatim |
| **On every use** | Is it *still* legal against the schema as it is **now**? | mark `STALE`, withdraw from matching, fall through to generation — **never fail the run, never silently vanish** |

The second row is a fifth-posture decision in the codebase's own taxonomy: a
stale template **fails as a value**. It mirrors the semantic layer's existing
rule — an invalid generated entry is dropped, an invalid human-written one is
flagged and kept.

### 5.2 A template's literals are a disclosure

**This is the part the research says nobody else got right, and it is not
covered by [mvp2-plan.md §A1](mvp2-plan.md).**

A connection declares `NONE | AGGREGATE | SAMPLE | FULL`, and `HintBudget` gates
what the schema block may say about a column's *contents*. Under `NONE` and
`AGGREGATE`, `value_lists` is false — no literal read from a row reaches the
model, ever.

A template's SQL contains literals. Rendered into a prompt or into a
"this is the saved answer" panel, `WHERE tier = 'ENTERPRISE' AND region = 'EMEA'`
puts two column values in front of the model on a connection whose policy says
none may go. The ladder is not bypassed by a bug — it is bypassed because the
template travels on a path the ladder does not cover.

**The rule, and it follows from precedent the codebase already contains.**
Catalog comments are exempt from the disclosure gate for a stated reason: *"A
comment is DDL a human wrote: it is not read from a row, it does not change when
the data changes, and it is exactly as much 'customer data' as a column name."*
A hand-authored template meets all three tests. So:

> **A template's literals travel with structure when a human wrote them, and are
> gated like sample values when a machine did.**

Enforced by `literal_provenance`:

| Value | Set when | Rendered when |
|---|---|---|
| `HUMAN_AUTHORED` | typed by a person in the editor; `source = MANUAL` or `CHAT_CORRECTED` where the human wrote the literals | always |
| `MODEL_DERIVED` | mined from a `GENERATED_EDITED` tile or block, or confirmed from a generated answer without editing the literals | only when `HintBudget.value_lists` is true |

The awkward case is real and is handled by that table: a human edited a
statement whose literals the *model* chose, possibly from sampled values
disclosed under a policy that has since been tightened. A tightening must take
effect on the next question — that is the existing rule, enforced at render
time, not write time — and a store that survived the tightening would quietly
undo it.

**Deliverables:** a section in [security.md](security.md) describing this rung
of the ladder, and `test_knowledge_disclosure.py` proving a `MODEL_DERIVED`
template is not rendered under `NONE`.

### 5.3 Everything else that must not slip

- **The conflict checker executes customer SQL on a schedule** (Phase 4). It
  inherits the connection's read-only credentials, the row cap, the guard and
  the disclosure policy without exception, and it is switchable off per
  connection.
- **Nothing calls a model at refresh time** stays true: matching, binding,
  parameter proposal, staleness and conflict detection are all deterministic.
  The only model call this plan adds anywhere is the optional embedding in
  Phase 7, and it is on the ask path, never a refresh path.
- **No model is asked to do arithmetic.** The comparator computes; nothing
  narrates a benchmark result.
- **Audit** every curation write once `audit_logs` is on (Phase 8). A store of
  business logic whose provenance cannot be proven is the exact failure Microsoft
  shipped and documented.

---

## 6. Measurement — what each phase must produce

| Phase | The question | The instrument | Needs the eval? |
|:--:|---|---|:--:|
| 0 | Does `runs.prompt_version` record the truth? Can retrieval recall miss? What does v8 score with the layer on and off? | unit test + two runner arms | yes |
| 2 | **Hit rate** — what fraction of analytical questions are answered from a template? | `knowledge_template_hits` | no |
| 2 | **Override rate** — how often does a user reject a verified answer? | `OVERRIDDEN_BY_USER` | no |
| 2 | **Unbound rate** — how often does a match fail only because a parameter would not bind? | `REJECTED_UNBOUND` | no |
| 3 | Is the backlog acted on? Do flags become templates? | `answer_feedback.became_template` | no |
| 4 | How fast does the store rot? | `STALE` and `CONFLICTED` counts over time | no |
| 5 | Does few-shot injection help on **held-out** questions? | `--templates on/off` eval arm | **yes — this is the gate** |
| 6 | What is this customer's accuracy, split two ways? | in-product benchmark, deterministic comparator | no |
| 7 | Does embedding retrieval raise recall, and does accuracy follow? | recall delta + execution accuracy | yes |

**Nobody publishes a hit rate for the short-circuit path** — not Databricks, not
Microsoft, not Wren. It is the number that decides whether this whole design was
worth building, and DataMind can measure its own within a week of Phase 2.

**The trap, restated because it is easy to fall into:** find a failure → add a
template for it → re-run the benchmark → watch the number rise. That loop is
correct for *fixing* and useless for *measuring*. §1.3's `role` column is the
enforcement; §4.8's two numbers are the honesty.

---

## 7. API surface

All under `/api/v1/connections/{connection_id}/knowledge`, scoped by the same
`_owned()` helper [semantic.py](../backend/app/api/v1/semantic.py) uses.

| Method | Path | Phase | Gated by |
|---|---|:--:|---|
| `GET` | `/templates` | 1 | read (connection owner) |
| `POST` | `/templates` | 1 | `can_curate` |
| `PATCH` | `/templates/{id}` | 1 | `can_curate` |
| `DELETE` | `/templates/{id}` | 1 | `can_curate` (archives; never hard-deletes) |
| `POST` | `/templates/check` | 1 | read — validate SQL + propose params, no write |
| `GET` | `/capabilities` | 1 | read — `{can_curate: bool}`, so the UI hides rather than disables |
| `GET` | `/suggestions` | 3 | read — backlog + backfill proposals, ranked |
| `POST` | `/feedback` *(on `/runs/{id}`)* | 3 | any signed-in user |
| `GET` | `/reviews` | 3 | read |
| `POST` | `/reviews/{id}/resolve` | 3 | `can_curate` |
| `POST` | `/templates/revalidate` | 4 | `can_curate` — kicks the worker |
| `GET` | `/health` | 4 | read — stale / conflicted / unused counts |
| `GET`/`POST` | `/benchmarks`, `/benchmarks/{id}/run` | 6 | `can_curate` |

`POST /templates/check` is the endpoint the editor calls on every pause in
typing. It returns the guard verdict, `referenced_tables`, and the parameter
proposals — one round trip for all three, because they all come from the same
parse.

---

## 8. File-by-file change map

**New — backend**

```
app/knowledge/{__init__,models,normalize,params,bind,validate,matcher,compare}.py
app/services/knowledge_service.py
app/api/v1/knowledge.py
app/workers/knowledge_maintenance.py            (Phase 4)
app/infra/db/migrations/versions/*_knowledge_*.py
tests/unit/test_knowledge_{guard,params,normalize,api,disclosure,match,bind}.py
tests/unit/test_knowledge_conflicts.py          (Phase 4)
```

**New — frontend**

```
frontend/src/components/knowledge.tsx
frontend/src/components/knowledge-template.ts
frontend/src/components/knowledge-template.test.ts     ← joins the npm test gate
```

**Modified**

| File | Change | Phase |
|---|---|:--:|
| `app/services/policy.py` | `can_curate()` | 1 |
| `app/core/config.py` | `curation_admin_only`; fix `prompt_version` | 0, 1 |
| `app/services/run_service.py:168` | record the real prompt version | 0 |
| `pyproject.toml` | `app.knowledge` in the layered contract + an eighth "self-contained" contract | 1 |
| `app/infra/db/models.py` | four new rows | 1–6 |
| `app/api/v1/__init__.py` | mount the router | 1 |
| `app/pipeline/state.py` | `RunState.matched_template_*`; `RetrievedContext.examples` | 2, 5 |
| `app/pipeline/nodes/__init__.py` | the `match` node | 2 |
| `app/pipeline/graph.py` | `MATCH` + its conditional edges | 2 |
| `app/pipeline/prompts/__init__.py` | `{examples}` slot, `PROMPT_VERSION` → v9 | **5 only** |
| `app/eval/metrics.py` | import the comparator from `app.knowledge.compare` | 6 |
| `app/eval/runner.py` | `--semantic`, `--templates` arms | 0, 5 |
| `frontend/src/pages/DataSourcesPage.tsx` | the fourth tab | 1 |
| `frontend/src/api/{client,types}.ts` | the `knowledge` namespace | 1 |
| `frontend/src/components/chat.tsx` | the badge, feedback, *Save as template* | 2, 3 |

**Docs to update when the code lands** — this repo's convention is that a
document is part of the change, not a follow-up:

- [CLAUDE.md](../CLAUDE.md) — a "Knowledge templates" section beside the
  semantic layer's, and the fifth guard entry point named in the guard section.
- [security.md](security.md) — §5.2's disclosure rung.
- [pipeline.md](pipeline.md) — the `MATCH` node in §0's pipeline map.
- [eval.md](eval.md) — the Phase 0 baselines and the Phase 5 arm.
- [docs/README.md](README.md) — index this document.
- [mvp2-plan.md](mvp2-plan.md) — §A1/§A3 gain a pointer here, and §A3's *"the two
  features share a table"* gets the §1.3 correction.
- A Persian edition (`learning-loop-plan.fa.md`) if this plan is adopted, matching
  [mvp2-plan.fa.md](mvp2-plan.fa.md).

---

## 9. Risks, and what we do about each

| Risk | Likelihood | What it costs | Mitigation |
|---|---|---|---|
| **A false match answers confidently and wrongly** | medium | the worst failure class this product has | conservative threshold; cancel-on-unbound; the matched question and bindings shown; one-click override; `OVERRIDDEN_BY_USER` makes it a measured number |
| **Nobody curates — the store stays empty** | **high** | the whole feature is worth zero | the backlog (§3.4) and the backfill turn an open-ended chore into a finite list; the badge makes the payoff visible on template #1 |
| **Few-shot injection lowers accuracy** | medium | a real regression, with precedent (36% → 26%) | Phase 5 is gated on a held-out measurement and has a per-connection off switch that restores byte-identical v8 behaviour |
| **The store rots into contradictions** | medium | wrong answers on questions *nobody curated* | Phase 4 conflict detection, using the comparator — a fact, not a confidence score |
| **A template leaks values under a restrictive policy** | low | a disclosure breach in the feature meant to build trust | `literal_provenance` + a dedicated test + a section in security.md |
| **Parameter proposals feel like noise** | medium | the editor's magic moment fails on first contact | the conservative rules in §1.2; proposals past the first two default to off; the rejected candidate is shown with its reason |
| **Curation permissions land wrong** | low | rework across every endpoint | one `can_curate` function from day one — the flip is a function body |
| **The benchmark measures memorisation** | **high if unguarded** | a number that rises while nothing improves | the `role` column, enforced in the query; held-out at creation; two numbers reported, held-out first |

**Rollback posture per phase:** Phase 1 is inert. Phase 2 is one settings flag
(matching off → today's pipeline). Phase 5 is a per-connection toggle. Phase 7
falls back to lexical automatically. No phase requires a data migration to undo,
and no phase deletes anything a person wrote.

---

## 10. Deliberately not building

Recorded with triggers, in the [architecture.md](architecture.md) "still
deferred" style, so each is a decision rather than an omission.

| Not building | Why | Trigger to revisit |
|---|---|---|
| **Free-text instructions** (global + question-matching) | the repo has measured that more prose in `GENERATE_SYSTEM` lowers accuracy; it is unvalidatable, no conflict detection is possible, and it overlaps the semantic layer's existing free-text fields | after Phase 5 has been measured, and only as question-matching scope, never global |
| **Fine-tuning or RL on collected templates** | incompatible with a provider-agnostic BYO-model product; needs orders of magnitude more data than one customer produces; and it is **unauditable** — a tuned weight cannot be reviewed, diffed, version-controlled or switched off per connection, which trades away exactly the property this product sells | none. This is a permanent no. |
| **A second typed-fact store** | the semantic layer already holds metrics, glossary terms and synonyms, validated and rendered | never — the queue routes definition-shaped corrections there instead (§1.5) |
| **Automatic approval of mined templates** | Genie proposes and a human approves; the approval step is the feature | none |
| **pgvector** | breaks "no new deployment unit" and is unavailable on some managed Postgres | a customer whose scale makes lexical matching demonstrably insufficient *and* who can run the image |
| **Cross-connection template sharing** | needs an ownership model that does not exist | [mvp2-plan §D1](mvp2-plan.md) sharing |

---

## 11. Open questions

Things this plan does not settle, listed so they are decided on purpose.

1. **The short-circuit threshold's starting value.** 0.85 is a guess. It must be
   tuned from the override rate after Phase 2 ships, not from taste, and the
   tuning should be recorded the way eval changes are.
2. **How many parameters is too many.** Power BI caps filters at 3 per verified
   answer and permutations at 10. A five-parameter template is probably a query
   builder wearing a costume, but there is no evidence for the exact number yet.
3. **Whether the backlog should rank by frequency or by cost.** Ten cheap
   repeated questions versus one expensive failing one — the second may matter
   more, and `runs` has the latency and token columns to find out.
4. **Wren AI's question-matching mechanism is undocumented** and the source is
   Apache-2.0. Reading `Canner/WrenAI` is the highest-value follow-up in the
   research and would inform Phase 7 directly.
5. **What a template means after a connection is re-pointed** at a different
   database with the same shape — staging to production. `schema_version` covers
   drift within one connection; it says nothing about this.

---

## 12. The one-line acceptance test

At the end of Phases 0–4, this sentence is either true or it is not:

> *Someone who is not a developer noticed a wrong answer, fixed it once, and the
> next person to ask that question got a Verified answer in database time — and
> both of them could see exactly why.*

If it is true, the loop is closed and Phases 5–7 are about how far it goes. If
it is not, no amount of Phase 5 will help.

---

## 13. Progress ledger — what is done, what is not

> **Verified against the tree on 2026-08-31.** Every ✅ below was checked by
> reading the code, not by memory; every ❌ was confirmed absent the same way.
> The verification note beside each item is what to re-run to check it again.
>
> **Status: 68 of 86 plan items complete.** Phase 0's instruments are built
> (its three measurements are not — see §13.2, and they gate Phase 5 only), and
> **Phases 1–3 have landed in full**: the store, the curation surface, the
> guard's fifth entry point, the short-circuit, the badge, feedback, the review
> queue and the ranked backlog. What is done besides that is the *foundation* the
> plan leans on (§13.1) — which is substantial, and is why the research put the
> loop at "60% built and not wired up".
>
> **Keep this section honest.** Tick a box in the same commit that lands the
> work, never in advance and never in a batch afterwards. A checklist that runs
> ahead of the tree is worse than no checklist, for the same reason the eval's
> charter says *"an eval you are allowed to edit measures your willingness to
> edit it."*

### 13.1 Already in the tree — the foundation (not built by this plan)

These pre-date this document. They are listed because the plan depends on each
one, and because the plan would be much larger if any were missing.

| ✅ | What | Verified at |
|:--:|---|---|
| ✅ | **`Role.ADMIN` / `MEMBER` and `require_admin` / `AdminDep`** — so D4's flip is a function body, not a migration | [deps.py:69](../backend/app/api/deps.py#L69), [value_objects/__init__.py:9](../backend/app/domain/value_objects/__init__.py#L9) |
| ✅ | **`app/services/policy.py` exists** as "authorization as functions, not scattered role checks" — `can_curate` has a home to go to | [policy.py](../backend/app/services/policy.py) |
| ✅ | **The guard, with four unprivileged entry points**, each replaying the hostile corpus | `test_sqlguard_hostile.py`, `test_query_service.py`, `test_report_guard.py`, `test_dashboard_transfer.py` |
| ✅ | **`guard(sql, policy) -> (report, executable)`** — one call the template path reuses whole | [sqlguard/__init__.py:12](../backend/app/sqlguard/__init__.py#L12) |
| ✅ | **`RetrievedContext` — the seam Phase 5 fills.** The generator never learns which strategy produced its context | [pipeline/state.py:126](../backend/app/pipeline/state.py#L126) |
| ✅ | **The semantic layer**, with an editor, live validation against the save-path parser, provenance-preserving merge, and `schema_version` drift | [semantic/](../backend/app/semantic/), [semantic.tsx](../frontend/src/components/semantic.tsx) |
| ✅ | **The semantic layer render fix (mvp2 §A6)**, done 2026-08-30 — the precondition for any of this having an effect | [mvp2-plan.md §A6](mvp2-plan.md) |
| ✅ | **A deterministic result-set comparator** with a reasoned tolerance and three equivalence modes — stage ⑤'s expensive part, already built and unit-tested | [eval/metrics.py](../backend/app/eval/metrics.py) |
| ✅ | **`GoldRecord` — already the template schema**, including `verification: dual_form` | [eval/dataset.py:26](../backend/app/eval/dataset.py#L26) |
| ✅ | **The `--comments` eval arm** — the exact precedent Phase 5's `--templates` arm copies | [eval/runner.py:807](../backend/app/eval/runner.py#L807) |
| ✅ | **Corrections already in the database, unread**: `dashboard_tiles` and `report_blocks` carry `question` + `sql` + `sql_origin ∈ {GENERATED_EDITED, HANDWRITTEN}` | [models.py:539](../backend/app/infra/db/models.py#L539), [models.py:730](../backend/app/infra/db/models.py#L730) |
| ✅ | **A design system with the tokens this feature needs** — green/amber/red/accent in both themes, `Chip`/`Modal`/`EmptyState`/`Field`, `dirOf()` for RTL | [tokens.ts](../frontend/src/theme/tokens.ts), [ui.tsx](../frontend/src/components/ui.tsx) |
| ✅ | **A connection-detail tab strip** with `settings / schema / semantic` — the fourth tab slots in | [DataSourcesPage.tsx:49](../frontend/src/pages/DataSourcesPage.tsx#L49) |
| ✅ | **Seven import-linter contracts** and the layered rule the eighth will join | [pyproject.toml](../backend/pyproject.toml) |
| ⚠️ | **`audit_logs` table exists — and nothing writes to it.** Phase 8 turns it on | `grep -rn AuditLog backend/app` returns only the model |
| ⚠️ | **`eval_runs` / `eval_results` exist — written only by the dev CLI.** Phase 6 deliberately does *not* reuse them | [eval/runner.py:494](../backend/app/eval/runner.py#L494) |

### 13.2 Phase 0 — Fix the ruler · **5 / 6** ⚠️ instruments built, the three runs not made

- [x] `runs.prompt_version` records the constant that rendered the prompt, not `settings.prompt_version` — `RunService._prompt_version()` returns `prompts.PROMPT_VERSION` unless the setting overrides it, and `execute_run` re-stamps the row in the process that renders the bytes; `core/config.py` now defaults the setting to `None`
- [x] A test that fails on today's code and asserts the run row equals `prompts.PROMPT_VERSION` — `tests/unit/test_prompt_version.py`, six tests; four of them fail against `HEAD~` (verified by stashing the two source files and re-running)
- [x] The eval runner can lower `_RETRIEVE_BUDGET_CHARS` from the command line — `--retrieve-budget CHARS`, recorded on the scorecard as `retrieve_budget_chars`; `tests/eval/test_runner.py` pins that recall is 1.0 at the shipped ceiling and misses beneath it
- [x] The budget decision recorded in `app/eval/suites/CHANGELOG.md` — with the reason the fixture was *not* widened instead, and the comparability trap stated once
- [x] A `--semantic on|off` arm — plus the thing it needs to switch on: `backend/fixtures/sales_semantic.json` (21 entities, 14 metrics), bound to the live snapshot by `runner.load_semantic`, which aborts the run rather than render a half-binding layer
- [ ] The three Phase 0 baselines written into [eval.md](eval.md): accuracy layer-off, accuracy layer-on, recall at a budget that can miss — **the table is in [eval.md §6](eval.md) with the three commands and empty cells.** Each run calls a real provider and needs an `llm_configs` row with a working key; there is none in this environment, so the numbers are not on paper and this box stays open

> **This is the gate on everything else.** Phase 5 is not allowed to start until
> the last box here is ticked — and it is the *numbers* that tick it, not the
> instruments that produce them.

### 13.3 Phase 1 — The store and the curation surface · **22 / 22** ✅ landed

**Backend**

- [x] `app/knowledge/models.py` — `KnowledgeTemplate`, `TemplateParam`, `ParamType`, plus `TemplateRole` / `TemplateStatus` / `TemplateSource` / `LiteralProvenance` and `may_render_literals` (§5.2's gate, which had nowhere else to live)
- [x] `app/knowledge/normalize.py` — question → `question_normalized`, with `slots()` and the editor's preview reading the braces the same way
- [x] `app/knowledge/params.py` — the AST walk (§1.2), `parameterize()` that substitutes **on the tree**, and `placeholder()` — one `:name` spelling in all four dialects, because Postgres' generator renders `exp.Placeholder` as `%(name)s`
- [x] `app/knowledge/validate.py` — the fifth door: guard verdict + `referenced_tables` + declared slots, with `E_PARAM_MISMATCH` for the two ways a template fails to hold together. Returns **no executable SQL**, deliberately
- [x] `app/knowledge/__init__.py` — the public surface, 26 names
- [x] `app.knowledge` in the layered contract, between `app.semantic` and `app.domain` — `lint-imports` green
- [x] An eighth contract, *"knowledge is self-contained"* — with `app.sqlguard` deliberately **not** in the forbidden list, because validating a template is calling the guard
- [x] `app/services/knowledge_service.py` — CRUD, the save gate, the live check, and read-time re-validation that reports drift without persisting it
- [x] `app/api/v1/knowledge.py`, mounted, with the same `_owned()` scoping `semantic.py` uses
- [x] `can_curate(ctx, settings)` in `policy.py`
- [x] `curation_admin_only: bool = False` in `core/config.py`
- [x] **Every** write endpoint calls `can_curate`; no endpoint checks `ctx.is_admin` — asserted on the **AST**, not by grep, so the module's own docstring saying so does not trip it
- [x] `knowledge_templates` model + migration `0015`, with `CREATE EXTENSION IF NOT EXISTS pg_trgm` inside a SAVEPOINT (a role that may not create it logs and continues; the GIN index is skipped and Phase 2's matcher degrades) and both other indexes

**Frontend**

- [x] `components/knowledge.tsx` — the tab, the list, the detail pane and the editor, composed entirely from existing primitives and tokens
- [x] `components/knowledge-template.ts` + `.test.ts` — 60 checks; `npm test` now runs **ten** suites
- [x] `DataSourcesPage.tsx` — the fourth tab
- [x] `api/client.ts` `knowledge` namespace + `api/types.ts` types

**Tests**

- [x] `test_knowledge_guard.py` — **the fifth entry point**: the corpus imported from `test_sqlguard_hostile.py` and replayed on save, on every use, and a third time with a `:slot` spliced in; plus a test that the template policy builder and `query_service`'s agree
- [x] `test_knowledge_params.py` — 39 tests: proposes date bounds, equalities and measure thresholds; refuses `<>`, `NOT IN`, `IN`, `LIKE`, `CASE`; never offers a `date_trunc` unit, a `LIMIT` or a `GROUP BY` ordinal
- [x] `test_knowledge_normalize.py` — 21 tests, both directions of the contract
- [x] `test_knowledge_api.py` — 30 tests: ownership scoping on every route, `can_curate` across the whole write surface in **both** settings, reading open in both
- [x] `test_knowledge_disclosure.py` — 13 tests; a `MODEL_DERIVED` template is withheld under `NONE` and `AGGREGATE` (§5.2), and every `TemplateSource` is assigned a provenance

**Done when:** a curator authors a template against `aurora`, sees `:from_date`
proposed, saves it — and `make test` + `make guard` are green with the corpus
replayed through the new door. **No chat answer behaves differently.**

> **Done.** The store ships inert: nothing in `app/pipeline/` imports
> `app.knowledge`, `PROMPT_VERSION` is untouched at `v8`, and no node reads the
> table. Two things landed slightly wider than the plan asked for, and both are
> recorded rather than quietly absorbed:
>
> * **`parameterize()` lives in the backend, not the browser.** The plan left
>   the substitution unplaced. Doing it on the tree in the server means the
>   statement that gets stored is the one the guard just read — and a
>   `str.replace` in the editor would have rewritten the `'EMEA'` inside a
>   `CASE` arm along with the filter. `POST /templates/check` takes the ticked
>   names and returns the parameterized SQL.
> * **Two agreement rules the guard cannot see** — a declared parameter the SQL
>   never uses, and a `:slot` the parameter list never declares — are rejected
>   at save as `E_PARAM_MISMATCH`. Both produce a template that is stored and
>   never matches, which would reach the curator as silence rather than as an
>   error.

### 13.4 Phase 2 — Match, short-circuit, badge · **12 / 12** ✅ landed

- [x] `app/knowledge/matcher.py` — the `TemplateMatcher` Protocol, `Candidate`, and `best()` (which returns nothing for a near-miss, because "close" is not a category an answer can be in)
- [x] `LexicalMatcher`, given a *row source* so the package stays free of sqlalchemy. **`pg_trgm` is an index, not the verdict**: the query narrows with the GIN index where it exists, and `trigram_similarity` — Postgres' own algorithm reimplemented — always scores. One path, so a deployment without the extension gets the same verdicts more slowly rather than a different feature
- [x] `SHORT_CIRCUIT_THRESHOLD` = 0.85 and `FEW_SHOT_THRESHOLD` = 0.45, with the tuning signal named in the docstring
- [x] `app/knowledge/bind.py` — the date grammar (`last month`, `in July`, `Q3`, `2026`, `last 12 months`, ISO dates and pairs, `yesterday`), string binding from the parameter's own declared values, single-numeral binding, and `bind_sql` substituting **on the tree**
- [x] The cancel-on-unbound rule, logged as `REJECTED_UNBOUND` — with the slots that *did* bind still reported, so the log names what to teach the binder next
- [x] `RunState.matched_template_id` / `match_score` / `match_kind`, plus `match_outcome`, `bound_params` and `matched_question` (the badge needs the last two, and they must be what *this run* matched)
- [x] The `match` node in `nodes/__init__.py`, and `StepName.MATCH`
- [x] `MATCH` wired into `graph.py` with both exits — the graph is now the chain plus exactly **six** jumps
- [x] `knowledge_template_hits` model + migration `0016`, plus `runs.skip_templates`
- [x] The three-tier badge in `chat.tsx`, with the matched question **and the bound parameters** shown, and *Generated* deliberately carrying no chip
- [x] *Generate a fresh answer instead* → `POST /runs/{id}/override` writes `OVERRIDDEN_BY_USER`, then the question is re-asked with `skip_templates`
- [x] Tests: `test_knowledge_match.py` (29), `test_knowledge_bind.py` (42), `test_knowledge_short_circuit.py` (15 — including the stale-template-falls-through case and a hostile-table template refused on the read path), and the **byte-identical prompt on a miss** assertion in `test_pipeline_graph.py`

> `PROMPT_VERSION` **must still read `v8`** when this phase ships. If it moved,
> something in this phase was built wrong.
>
> **It read `v8`,** and `test_pipeline_graph.py` asserted it. Phase 5 moved it
> to `v9` for the `{examples}` slot, and rewrote that assertion to pin the
> thing that still matters here: with no examples the rendered prompt is
> byte-identical to v8's, so nothing this phase built changed a prompt.
>
> Two things landed differently from the sketch, both recorded rather than
> quietly absorbed:
>
> * **The template's declared values are masked out of the question before
>   scoring.** Without it the plan's own worked example scores **0.83** against
>   its own pattern — under the threshold — because `EMEA` is not detectably a
>   literal from the outside, and lowering the threshold to 0.83 to compensate
>   would let genuinely different questions in. The masking uses only what the
>   *curator* declared (`one of: EMEA, NA, APAC`), so it can remove a
>   difference the template called a value and can never invent a match.
> * **The `LIKE` fallback is a faithful trigram instead.** The plan asked for a
>   degraded "LIKE-and-token comparison" when `pg_trgm` is absent. Reimplementing
>   `similarity()` exactly costs about thirty lines and means the thresholds mean
>   one thing everywhere, rather than one thing on a managed database and another
>   on a laptop.
>
> One consequence worth knowing: the pipeline's hard ceiling is 25 **node
> executions**, and `match` spends one of them on every run, so a runaway repair
> loop now stops one `generate` earlier. `test_pipeline_events.py` records it.

### 13.5 Phase 3 — Capture: feedback, queue, backlog · **9 / 9** ✅ landed

- [x] `answer_feedback` model + migration `0017`, with `UNIQUE (run_id, user_id)` — one verdict per person, and a second press is a change of mind
- [x] `POST /runs/{id}/feedback`, open to any signed-in user — it does **not** ask `can_curate`, and a test asserts that it still works with the flag on while resolving a flag does not
- [x] ✓ / ✗ / *Ask for review* in the chat answer footer, inline — one textarea expands in place, one line of acknowledgement, no modal and no toast
- [x] *Save as a template* on an answer → the **same** `TemplateEditor` the Knowledge tab uses, prefilled with the question and the statement the reader just watched succeed. Reused rather than reimplemented: two editors would be two chances to get the disclosure rule wrong
- [x] `GET /reviews` + `POST /reviews/{id}/resolve`, the second gated by `can_curate`
- [x] The review detail pane: the flagged statement, and the question-shaped / definition-shaped / dismiss radio (§1.5) — a dismissal cannot be sent without a reason
- [x] The backfill reader over `dashboard_tiles` **and** `report_blocks` — proposals only, `GENERATED_EDITED` ⇒ `MODEL_DERIVED`
- [x] `GET /suggestions` — the ranked backlog, **five** sources: flagged, backfill, traffic, failed, and words the retrieval did not recognise. Everything already taught is excluded, so the list shrinks as it is worked
- [x] `answer_feedback.became_template` surfaced back to the person who flagged it, on their own answer, in the footer where they pressed the button

> Ship without that last box and this phase has shipped a suggestion box.
>
> **It is shipped.** Saving a template from a flag resolves that flag in the
> same action, so the link cannot be forgotten by a curator in a hurry.
>
> Three things landed differently from the sketch:
>
> * **The backlog has five sources, not four.** A flag is rank 1 in the plan's
>   own table but was not listed as a *source*; making it one means the queue
>   and the backlog are one ranked list rather than two screens that disagree
>   about what matters.
> * **The vocabulary gap needed a stopword list.** Without one, `last`,
>   `month`, `total` and `average` are "words nothing here recognises" on
>   almost every question, and the real gaps are buried on the first day. Three
>   groups — grammar, time, aggregation — and nothing further, because an
>   aggressive list hides real misses.
> * **A `CORRECT` verdict arrives already `RESOLVED`**, by the person who gave
>   it. Treating a ✓ as open work would put a permanent number on the tab that
>   no curator could clear, which is how a badge stops being a signal.

### 13.6 Phase 4 — Store health · **7 / 7** ✅ landed

- [x] Re-validate every `ACTIVE` template on schema sync → `STALE` with a readable `status_reason` — `KnowledgeService.sweep_staleness`, called inline from `POST /connections/{id}/schema/sync` in the same transaction, because it is `guard()` per template and makes **no** call to the customer's database. The reverse transition is there too: a template that resolves again returns to `ACTIVE` on its own, without which the first bad sync is permanent
- [x] `STALE` / `CONFLICTED` withdrawn from matching and few-shot, never deleted — `KnowledgeTemplate.is_withdrawn` beside `is_matchable`, and the predicate is in the candidate query as well as in the code
- [x] `app/workers/knowledge_maintenance.py` — `run_maintenance` (staleness first, then conflicts, and the order matters), `maintenance_loop` on a six-hour timer wired into `lifespan`, and `KnowledgeMaintenanceExecutor` for the on-demand path
- [x] Conflict detection: similarity → bind both → execute both through the guard → compare with `app/knowledge/compare.py` — `app/knowledge/conflict.py` holds the pure half (`similar_pairs` at a **measured** 0.60 threshold, `probe_values`), the worker holds the I/O, and execution goes through `execute_saved_sql`, the same door a dashboard tile uses
- [x] `conflicts_with` populated, **and the diverging rows shown as evidence** — `knowledge_templates.conflict_evidence` (migration `0018`), written from *each* row's own point of view so whichever the curator opens sees its own answer on the left; `ConflictEvidencePane` renders the two tables with the cell that moved in amber
- [x] Per-connection off switch for the scheduled checker — `connections.conflict_checks_enabled`, checked **before** a connector is opened, and it stops only the half that executes SQL: the staleness sweep is a parse and keeps running
- [x] `test_knowledge_conflicts.py` — 45 tests, plus 14 new frontend checks

> **Done.** Two things landed beyond the sketch, both recorded rather than
> quietly absorbed:
>
> * **`app/knowledge/compare.py` landed here, not in Phase 6.** Phase 4's own
>   text says the conflict checker compares with it, so the move had to happen
>   now — the eval harness's pure comparator came *down* a layer and
>   `app/eval/metrics.py` re-exports it. That ticks the first two boxes of
>   §13.8 in this commit; `lint-imports` is green and nothing on the request
>   path gained an import of `app.eval`.
> * **Both halves of every state transition, not just the bad one.** A stale
>   template that resolves again returns to `ACTIVE`; a conflicted pair that
>   agrees on a later pass is cleared. The plan only asked for the transitions
>   *into* the withdrawn states, and a store that can enter one and never leave
>   it is one nobody trusts — healing it would mean a curator opening forty
>   rows and pressing Save on each.
>
> One thing deliberately **not** built: the checker does not pick a winner.
> Both rows are marked, both keep their evidence, and §4.7's *Keep the first /
> Keep the second / Edit both* stays a human decision.

### 13.7 Phase 5 — Few-shot, behind an eval gate · **6 / 7** ⚠️ built, and shipped **off** because the gate cannot be run here

- [x] `RetrievedContext.examples` — `list[TemplateExample]`, carrying `literal_provenance` so §5.2's gate applies at render time. `RunState.examples` is where `match` puts them and `retrieve` reads them
- [x] The `{examples}` slot in `GENERATE_SYSTEM`, rendering to empty when there are none — written `{schema}\n{examples}\n{history}`, so empty collapses to the newline that was already there. `test_knowledge_few_shot.py` asserts the **byte-identity**, not a paraphrase of it
- [x] Budget fitting — examples **last**, after schema and semantic layer; `_EXAMPLE_CHARS_BLOCK` is 1,600 against the comment block's 2,500, at most four examples, and a long one is skipped whole rather than truncated
- [x] `PROMPT_VERSION` v8 → v9 — moved on the rule as written, and the ledger's own Phase 2 assertion (`test_pipeline_graph.py`) was rewritten to say v9 **and** to pin the empty case against v8's bytes
- [x] A `--templates on|off` eval arm, beside `--comments` — builds the store from the suite's own questions, holds out `HELD_OUT_FRACTION` deterministically by sorted id, excludes **every** record from the store it is measured against, tags each record `held_out`/`taught` so the per-tag breakdown reports the split, and records `examples_offered` / `short_circuited` per record so an arm where nothing matched cannot be read as an arm that measured something
- [x] Per-connection `knowledge_examples_enabled` toggle — migration `0019`, **default `false`**, surfaced in the connection settings beside the disclosure policy
- [ ] **The gate:** both numbers in [eval.md](eval.md), and the ship/don't-ship decision written down with its evidence — **the table is in [eval.md §6.1](eval.md) with both commands, the four rules for reading them, and empty cells.** Each run calls a real provider and needs an `llm_configs` row with a working key; there is none in this environment, so the numbers are not on paper and this box stays open. **The decision that follows from an unmet gate is written down and is the code's actual default: the feature ships off.**

> A negative delta on a small model is a result to publish, not a reason to tune
> until it goes positive.
>
> **Built, and deliberately inert.** The one box that cannot be ticked here is
> the one that decides whether the feature is on, so the column default is
> `false` and off is byte-identical to v8. Everything else — the slot, the
> budget, the disclosure gate, the arm, the reporting, the switch — is in the
> tree and tested, so flipping the default is one migration once somebody with
> a provider key runs two commands.
>
> Two things landed differently from the sketch, both recorded:
>
> * **`match` grew the second job, not `retrieve`.** The plan put `examples` on
>   `RetrievedContext` and left the collection unplaced. Doing it in `match`
>   means one node owns every read of the store and one threshold pair governs
>   both uses of it — and a short-circuit, which has no generator to teach,
>   offers nothing by construction rather than by a check somewhere else.
> * **The eval arm holds out *and* self-excludes.** The plan asked for a
>   held-out fraction. That is not sufficient on its own: a *taught* record
>   whose own gold SQL is in the store is answering itself, and its accuracy
>   would be quoted beside the held-out number as if the two were comparable.
>   Every record is excluded from the store it is measured against, held out or
>   not, so the `taught` row means "questions whose neighbours were taught" and
>   nothing stronger.

### 13.8 Phase 6 — Benchmark and a score · **7 / 7** ✅ landed

- [x] The pure comparator moves to `app/knowledge/compare.py`; `app/eval/metrics.py` imports it — **landed with Phase 4**, which needed it for the conflict checker. `values_equal`, `rows_equal`, `result_sets_match` and both tolerance constants, plus `first_difference` for the evidence; `metrics.py` re-exports them so every existing caller and `tests/eval/test_metrics.py` keep working against one implementation
- [x] `import-linter` still green — `app.eval` reachable from nothing on the request path — 8 contracts kept, and both `test_knowledge_conflicts.py` and `test_benchmarks.py` assert on the **parse** that neither `compare.py` nor the benchmark worker imports `app.eval`
- [x] `benchmark_sets` / `benchmark_runs` / `benchmark_results` — **separate from `eval_runs` / `eval_results`**, migration `0020`, with a test that names all four table names so a well-meaning consolidation fails loudly
- [x] `role` enforced in the query that builds each set (§1.3) — twice: `BenchmarkService.candidates` will only take an `ACTIVE`, `RETRIEVABLE` template, and `workers/benchmark._members` filters on role **again** when it loads them, so a member a curator edited back to `RETRIEVABLE` is excluded rather than silently scored
- [x] A fixed fraction assigned `HELD_OUT` at creation — `held_out_split`, deterministic by sorted id at a fixed stride, so the split is reproducible from the set's own membership list years later. Creating a set **withdraws every member from answering**, which is the enforcement rather than a side effect
- [x] Runs execute in `app/workers/`, labelled by the comparator — **no LLM judge** — `workers/benchmark.py` runs the real `AnalyticsPipeline` per question, executes the stored answer through `execute_saved_sql`, and compares with `app.knowledge.compare`. A stranded run is failed, not resumed
- [x] The score strip (§4.8): held-out number first and larger, taught number second — with the sparkline on the **held-out** series against a fixed 0–100% scale, `—` rather than `0%` when nothing scored, and the count of questions that could not be scored shown beside it

> **Done.** Three things landed beyond the sketch, all three because they are
> the ways an accuracy quietly becomes a lie:
>
> * **`from_template` is observed, not assigned.** §3.7's rule 3 asks for the
>   split between questions answered *from* a template and questions answered
>   without one. A member's `role` says what it may be used for; only the run
>   knows what happened — a held-out question can still be answered from a
>   *neighbour's* template, which is a real thing that happens on the ask path.
>   So the taught number counts what the run did, and the held-out number counts
>   what the role guaranteed.
> * **Nothing that did not run is in a denominator.** A member whose parameters
>   could not be probed (`NOT_PROBED`) or whose stored answer no longer executes
>   is counted in `total`, in neither accuracy, and the difference is shown on
>   the strip. An accuracy over a shrinking denominator always flatters, and it
>   is invisible unless somebody prints the gap.
> * **A set below four questions is refused.** 100%-on-three is a number
>   somebody would quote.
>
> The plan's acceptance test — *"a connection owner opens Knowledge → Score,
> runs their set, and sees two accuracy numbers with a history — without a
> developer"* — is built end to end: `POST /benchmarks` from the tab, a `202`
> and a row, the worker, and the strip. **The numbers themselves need a
> provider key**, the same blocker as Phase 0's baselines and Phase 5's gate.

### 13.9 Phase 7 — The embedding matcher · **4 / 5** ⚠️ built, and shipped **off** because the arm cannot be run here

- [x] `EmbeddingMatcher` behind the same Protocol — `app/knowledge/embed.py`, and `build_matcher` returns a bare `LexicalMatcher` unless a model is pinned, so the lexical path gained no wrapper and no branch
- [x] Masked question similarity (table names, column names, literals → generic tokens) — plus the values a **curator** declared, without which the canonical example does not work; `mask_literals` factored out of `normalize_question` so there is one reading of what a literal is
- [x] Capability detection on the connection's LLM config; silent fallback to lexical — `probe_embedding` refuses Anthropic with no network call and *asks* everything else; `FallbackMatcher` turns every failure into today's behaviour
- [x] Embedding model id + dimension pinned per connection; staleness rule on edit and on model change — **derived from a fingerprint, not tracked**, so a schema re-sync invalidates the right vectors too and no invalidation call exists to forget
- [ ] Recall delta **and** execution accuracy reported — remembering FK expansion moved recall 70 → 86% with flat accuracy — *`--matcher lexical|embedding` is built and the report prints both numbers on one line; **the two runs need a provider key with an embedding endpoint**, the same blocker as §13.2 and §13.7. [eval.md §6.3](eval.md) carries both commands and an empty table.*

### 13.10 Phase 8 — Permissions hardening · **0 / 4** ❌ not started

- [ ] `curation_admin_only` default flipped to `true`
- [ ] The review queue routed to a connection owner (needs [mvp2 §D1](mvp2-plan.md))
- [ ] Every curation write emits an `audit_logs` row
- [ ] `audit_logs` actually being written at all (mvp2 §D4) — **confirmed absent today**

### 13.11 Documentation · **0 / 7** ❌ not started

Docs land in the same commit as the code, per this repo's convention.

- [ ] [CLAUDE.md](../CLAUDE.md) — a "Knowledge templates" section, and the fifth guard entry point named
- [ ] [security.md](security.md) — §5.2's disclosure rung
- [ ] [pipeline.md](pipeline.md) — the `MATCH` node in §0's map
- [ ] [eval.md](eval.md) — Phase 0 baselines, Phase 5 arm
- [ ] [docs/README.md](README.md) — index this document *(note: `mvp2-plan.md` and `research/` are not indexed there either; index all three together or none)*
- [ ] [mvp2-plan.md](mvp2-plan.md) — §A1/§A3 point here, and §A3's *"the two features share a table"* takes the §1.3 correction
- [ ] `learning-loop-plan.fa.md`, if this plan is adopted

### 13.12 Totals

| Phase | Done | Total | Status |
|---|:--:|:--:|---|
| Foundation (pre-existing) | 14 | 16 | ✅ two are `⚠️ present but unwired` |
| 0 · Fix the ruler | 5 | 6 | ⚠️ **still blocking Phase 5** — the three runs are unmade |
| 1 · Store + curation surface | 22 | 22 | ✅ |
| 2 · Match, short-circuit, badge | 12 | 12 | ✅ |
| 3 · Capture | 9 | 9 | ✅ |
| 4 · Store health | 7 | 7 | ✅ |
| 5 · Few-shot | 6 | 7 | ⚠️ **built and shipped off** — the gate needs a provider key |
| 6 · Benchmark | 7 | 7 | ✅ |
| 7 · Embeddings | 4 | 5 | ⚠️ **built and shipped off** — the arm needs a provider key |
| 8 · Permissions | 0 | 4 | ❌ |
| Docs | 0 | 7 | ❌ |
| **Plan total** | **72** | **86** | |

### 13.13 Change log

One line per landing, newest last. This is the record the next reader trusts
over anything else in the document.

| Date | What landed | Boxes ticked |
|---|---|---|
| 2026-08-31 | This plan written; the tree audited to establish the starting position | — (0 of 86) |
| 2026-09-01 | **Phase 7 — the embedding matcher, built and shipped off.** `app/knowledge/embed.py`: masked question similarity (DAIL-SQL) with **three** tokens rather than one — `revenue by <column>` and `revenue by <table>` are different questions — plus the values a *curator* declared, which is the piece without which the canonical example does not work and is the same information `mask_declared_values` already uses lexically. `mask_literals` factored out of `normalize_question`, so the match key and the embedding key cannot hold two opinions about what a literal is. `EmbeddingMatcher` behind the **same Protocol** and against the **same two thresholds**, so the phase is a constructor change: the `match` node, the binder, the short-circuit and the badge are untouched, and `build_matcher` returns a bare `LexicalMatcher` when no model is pinned — no wrapper, no extra query, no extra branch on the shipped path. `FallbackMatcher` makes *"degrades to lexical, never to nothing"* ten lines instead of a branch in every caller, and `Candidate.matcher` already travels to `knowledge_template_hits.matcher`, so "is this doing anything?" is a query rather than a log search. **Staleness is derived, never tracked**: a vector stores the SHA-256 of (masked text, model id, width), so a template edit, a model change *and* a schema re-sync each invalidate exactly what they should and there is no invalidation call to forget — a failed vector is ignored, never deleted. Migration `0021`: five columns, **no pgvector** — the base image does not carry it, the store is a curator's worth of rows, and cosine belongs where `trigram_similarity` already is. `embed` / `probe_embedding` on the `LLMGateway` port and in the one module allowed to import litellm; the width is **measured** from a real call, and Anthropic is refused without one. `index_embeddings` is the maintenance pass's third step, after staleness and conflicts so it never spends a call on a row those two just withdrew; `PUT /knowledge/embeddings` probes, pins and indexes inline so the feature works on the next question. Frontend: one quiet strip with four states, and *word matching* reads as a choice rather than a fault — the off state describes what the other mode **adds**. 56 new backend tests plus 23 frontend checks. Docs: security.md §4.7 (what leaves, and why it is less than the generate prompt already sends), llm-calls.md §13b (calls 18–20, the only three in the product that send no prompt), eval.md (the matcher arm and §6.3), CLAUDE.md. **The last box stays open: the recall/accuracy pair needs a provider key, and the default that follows from an unmeasured arm is `embedding_model` empty — every connection matches lexically until somebody turns it on.** | 72 of 86 (§13.9, 4 of 5) |
| 2026-09-01 | **Phase 6 — a benchmark and a score, in the product.** `benchmark_sets` / `benchmark_runs` / `benchmark_results` + migration `0020`, **deliberately not** `eval_runs` / `eval_results` — MVP2 Part 5's meta-rule, with a test that names all four table names. `benchmark_service.py`: `held_out_split` (deterministic by sorted id, so the split is re-derivable from the set's own membership), `create_set` — which **withdraws every member from answering**, because §1.3's rule is enforced in the ask path's own query — `release`, which gives them back, and `score`, the pure function behind both numbers. `workers/benchmark.py`: the real `AnalyticsPipeline` per question, one probe filling both the question and the gold statement, the gold executed through `execute_saved_sql`, and labels from `app.knowledge.compare` — **no LLM judge**, and a test asserts on the parse that the worker imports nothing from `app.eval`. A stranded run is failed, not resumed. `GET/POST /benchmarks`, `DELETE /benchmarks/{id}`, `POST /benchmarks/{id}/run` (202 + a row), `GET /benchmarks/runs/{id}/results`. Frontend: the score strip — held-out first, larger, and on the sparkline against a fixed 0–100% scale; the taught number second and smaller; `—` rather than `0%` when nothing scored; the unscored count shown rather than hidden — plus the offer to create one, which says up front that those questions stop answering chat. 23 new backend tests, 15 new frontend checks. Docs: eval.md §6.2 (two instruments, one comparator, and why they must not share a table), CLAUDE.md. | 68 of 86 (§13.8, all 7) |
| 2026-09-01 | **Phase 5 — few-shot injection, built and shipped off.** `RetrievedContext.examples` + `TemplateExample`, and `GENERATE_SYSTEM`'s `{examples}` slot written `{schema}\n{examples}\n{history}` so the empty case collapses to **v8's exact bytes** — asserted, not asserted-about. `PROMPT_VERSION` v8 → v9. `match` collects near misses on a *miss* only (a short-circuit has no generator to teach) and `retrieve` carries them; §5.2's disclosure gate is applied in `render_examples`, at render time, withholding a `MODEL_DERIVED` template's literals whole under `NONE`/`AGGREGATE`. Budget: last in the prompt, four examples, 1,600 chars against the comment block's 2,500, long ones skipped rather than cut. `--templates on|off` on the runner, building the store from the suite itself, holding out two in five deterministically and excluding every record from the store it is measured against; `examples_offered` / `short_circuited` on the scorecard so an arm that matched nothing cannot be read as a measurement. Migration `0019` + the settings toggle, **default false**. 26 new backend tests. Docs: eval.md (the templates arm and §6.1, the gate, with both commands and empty cells), pipeline.md §5 (the v9 table), CLAUDE.md, llm-calls.md, CODEBASE.md. **The gate box stays open: the runs need a provider key, and the decision that follows from an unmet gate is the default the code ships.** | 63 of 86 (§13.7, 6 of 7) |
| 2026-09-01 | **Phase 4 — store health.** `app/knowledge/compare.py` — the eval harness's result-set comparator moved **down** a layer (`app.eval -> app.knowledge` is permitted; nothing on the request path gained an import of `app.eval`), plus `first_difference`, which returns the *diverging rows* rather than a boolean. `app/knowledge/conflict.py` — `similar_pairs` at a measured 0.60 threshold and `probe_values`, which refuses to invent a string it was not given, because a check that reports the store healthy because it could not test it is worse than no check. `KnowledgeService.sweep_staleness`, run inline on every schema sync: `ACTIVE` → `STALE` with the guard's own sentence, **and `STALE` → `ACTIVE` when the schema heals**. `app/workers/knowledge_maintenance.py` — the conflict checker (similarity → bind both at the same values → execute both through `execute_saved_sql` → compare) on a six-hour loop and on demand, marking **both** rows and storing the diverging rows from each one's own point of view. Migration `0018`: `conflict_evidence`, `last_conflict_check_at`, and `connections.conflict_checks_enabled` — the off switch, checked before a connector opens. `GET /health`, `POST /templates/revalidate`. Frontend: the conflict pane with the two answers side by side and the cell that moved in amber, the *Check the store* action, and the faint unused line with no button beside it. 45 new backend tests plus 14 frontend checks. | 57 of 86 (§13.6 all 7, §13.8 first two) |
| 2026-08-31 | **Phase 3 — capture: feedback, the queue, the backlog.** `answer_feedback` + migration `0017`; `POST /runs/{id}/feedback` open to **any** signed-in user (the person who notices a wrong answer is rarely the person allowed to fix it), with three verdicts and a `CORRECT` arriving already resolved. `app/knowledge/backlog.py` — the five ranked sources and the vocabulary gap, pure and unit-tested; `FeedbackService` — the queue, the resolution, and the aggregation over `runs`, `dashboard_tiles` and `report_blocks`. `GET /reviews`, `POST /reviews/{id}/resolve` (a dismissal needs a reason), `GET /suggestions`. Frontend: the inline ✓/✗/*Ask for review* footer, *Save as a template* opening the **same** editor the Knowledge tab uses, and the queue and backlog as two more sections of the one list. **`became_template` reaches the flagger on their own answer**, and saving a template from a flag resolves it in the same action. 42 new backend tests plus 14 frontend checks. Also: `tests/conftest.py` now forces JSON logs — an unhandled exception in a route was taking **over a minute** to render through structlog's rich console renderer, which made a failing API test look like a hung suite. | 48 of 86 (§13.5, all 9) |
| 2026-08-31 | **Phase 2 — match, short-circuit, badge.** `app/knowledge/matcher.py` (the Protocol, `LexicalMatcher` over an injected row source, `trigram_similarity` as Postgres' own algorithm, and the declared-value masking without which the plan's worked example scores 0.83) and `bind.py` (the date grammar and the cancel-on-unbound rule, substituting on the tree). The `match` node between `route` and `retrieve`, wired with both exits — a hit lands on `validate`, so a stored template reuses the guard, the rewriter and the row cap and gets no exemption; a miss writes nothing. `knowledge_template_hits` + migration `0016` + `runs.skip_templates`; every verdict logged, `OVERRIDDEN_BY_USER` included. The three-tier badge in `chat.tsx` with the matched question *and* the bound parameters, and *Generate a fresh answer instead* wired through `POST /runs/{id}/override`. 86 new backend tests. Docs: pipeline.md §2/§3 (the node, the graph, the eleven-node table), CLAUDE.md. **`PROMPT_VERSION` is still `v8`, and a test asserts it.** | 39 of 86 (§13.4, all 12) |
| 2026-08-31 | **Phase 1 — the store and the curation surface.** `app/knowledge/` (models, normalize, params, validate) with an eighth import-linter contract; `knowledge_templates` + migration `0015` (`pg_trgm` inside a SAVEPOINT, so a role that may not create extensions still migrates); `knowledge_service.py`; `/connections/{id}/knowledge/*` with `can_curate` on every write and `is_admin` nowhere; the Knowledge tab, its DOM-free half and its 60-check suite. Five test files, 216 backend tests — the hostile corpus replayed through the fifth door on save, on use, and with a slot spliced in. Docs: CLAUDE.md (the guard's *five* entry points, a Knowledge templates section, the code map, the eighth contract), security.md §3.2/§3.3/§4.5 (the disclosure rung and the fifth door), docs/README.md (the three unindexed docs indexed together). **The store is inert: `PROMPT_VERSION` is still v8, nothing in `app/pipeline/` imports `app.knowledge`, and no chat answer behaves differently.** | 27 of 86 (§13.3, all 22) |
| 2026-08-31 | **Phase 0 instruments.** `runs.prompt_version` records the prompt module's constant (+ `tests/unit/test_prompt_version.py`); the eval runner gained `--retrieve-budget` and `--semantic on\|off`, both off by default and both recorded on the scorecard; `backend/fixtures/sales_semantic.json` added as the layer-on arm's input; both decisions logged in `suites/CHANGELOG.md`. Docs: eval.md §1/§4/§6, CLAUDE.md, pipeline.md §5. **The three baseline runs were not made — no provider key in this environment.** | 5 of 86 (§13.2 boxes 1–5) |
