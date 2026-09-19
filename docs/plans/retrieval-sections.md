# Retrieval by sections — build plan

> **Status:** a plan, not a proposal. Written 2026-09-19 against `main`.
> The argument for *why* lives in
> [research/retrieval-at-scale.md](../research/retrieval-at-scale.md) and
> [mvp2.md §1.2](mvp2.md#12-retrieval-is-a-placeholder-with-a-hard-ceiling);
> this document is *what we build, in what order, and how we know it worked*.
>
> **The strategy was chosen before writing this.** A large database is divided
> into named **sections**; a small model call picks which section a question
> belongs to; retrieval then happens inside that section and the section fits
> the context budget whole. It is research
> [§O4 + §O5](../research/retrieval-at-scale.md#group-ii--shrink-the-haystack-what-the-competitors-actually-do)
> — *"shrink the haystack"* — with the routing step research §O9 describes,
> shrunk to its simplest useful form. Everything below assumes it.
>
> **One divergence from the research, stated up front.** The research rates
> topics-plus-routing **L** and says *"not MVP2"*, on the grounds that a topic
> owns its own semantic layer, its own verified pairs, its own accuracy score
> and its own conversation binding. **This plan builds none of that.** A section
> here is a *name, a sentence, and a list of tables* — nothing else hangs off
> it. That is what takes it from **L** to **M**, and it is the whole design
> decision (§0.2 D1).
>
> §14 is the ledger: a checkbox per deliverable per phase, each with the check
> that proves its state. Tick a box in the commit that lands the work, never
> ahead of it.

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

*On a database too large to send to a model, a question is answered from the
part of the database it is actually about — and the person asking can see which
part that was, and say so themselves when we get it wrong.*

### 0.2 The four decisions

| # | Decision | What it rules out |
|---|---|---|
| **D1** | **A section is a name, a sentence and a list of tables. Nothing else.** No per-section semantic layer, no per-section knowledge store, no per-section conversation binding, no per-section score. | Research §O5 as written. Those are all good ideas and all of them can be added later against a section that already exists; building them now is what makes this **L** instead of **M**, and none of them is needed for the routing to work. |
| **D2** | **A section is a hint, never a boundary. The guard is untouched.** `policy_from_snapshot` keeps building its allowlist from the **whole** snapshot. A section decides what the model is *shown*, never what it may *query*. | Narrowing the guard — which would silently break every saved dashboard tile and report block over an out-of-section table, days later, as a per-tile `ERROR` value, for someone who did not change the sections. Research §6.1 calls this the load-bearing decision and this plan takes its recommendation without amendment. |
| **D3** | **Every failure falls open to today's behaviour.** No sections, a model error, an empty reply, an unknown section name, or `NONE` — all of them produce the retrieval that runs today, over the whole snapshot. | A new single point of failure on the ask path. `scope` joins `route`, `clarify`, `inspect` and `chart` in the **fail open** posture (CLAUDE.md, *The five failure postures*): the feature is dropped, the work continues. |
| **D4** | **Sections are proposed by the system and corrected by a person — never authored from a blank form.** First open of the tab shows a complete, deterministic proposal computed from schemas, the foreign-key graph and name prefixes. | A 2,000-checkbox curation screen. Research §5.2 names this as the reason O4 fails in practice, and Power BI's answer — seed the scope from signal the system already has — is the one this copies. |

### 0.3 The phases

| # | Phase | Size | Gate to start | Changes an answer? |
|---|---|:--:|---|:--:|
| **0** | [Repair the floor](#4-phase-0--repair-the-floor) | S | — | on a path no fixture takes |
| **1** | [Sections exist, and do nothing](#5-phase-1--sections-exist-and-do-nothing) | M | Phase 0 done | **no** — sections are inert |
| **2** | [The `scope` node](#6-phase-2--the-scope-node) | M | Phase 1 done | **yes**, on sectioned connections only |
| **3** | [Make it trustworthy](#7-phase-3--make-it-trustworthy) | S–M | Phase 2 done | no — visibility, override, drift |

**Phase 0 is not a detour and it is not optional.** Two of its three deliverables
are defect repairs on the branch every real customer takes and no test exercises
(research §1.1, §1.2), and the third — a ranked selection that always fits the
budget — is the thing Phase 2 falls back on when a section is still too big, when
the router says `NONE`, and when the connection has no sections at all. Building
sections on top of an unbounded matcher means the fallback is still the bug.

### 0.4 The flow, after all four phases

```
route ──ANALYTICAL──→ match ──miss──→ scope ──→ retrieve ──→ describe ──→ …
  │                     │               │           │
  │                     │               │           └ scoped set fits the budget?
  │                     │               │               yes → SECTION_SNAPSHOT  ← the point
  │                     │               │               no  → RANKED_MATCH within the section
  │                     │               │
  │                     │               └ sections? no  → SKIPPED, whole snapshot (today)
  │                     │                 model error   → SKIPPED, whole snapshot
  │                     │                 reply NONE    → SKIPPED, whole snapshot
  │                     │
  │                     └ hit → validate  (never reaches scope — a stored answer
  │                                        needs no schema block, so it pays for
  │                                        no scope call)
  │
  └─METADATA─→ … scope does not run: "what is in this database?" is a question
                 about the whole database, and `SCHEMA_QUESTION` already answers
                 it over the whole snapshot with `census` naming what did not fit.
```

---

## 1. What a section is

### 1.1 Anatomy

| Field | What it is | Who writes it |
|---|---|---|
| `name` | *"Sales"* — short, unique within the connection, and the token the model replies with. | proposed, renamed by a person |
| `description` | One or two sentences: *"Orders, order lines, payments and refunds. Answers questions about revenue, order volume, discounts and returns."* **This is the text the router reads, and it is the single highest-leverage field in the feature.** | proposed from the semantic layer / catalog comments; edited by a person |
| `tables` | Qualified names — `["public.orders", "public.order_items"]`. | proposed from the FK graph; edited by a person |
| `origin` | `PROPOSED` until a person saves it, then `CURATED`. | the system |
| `schema_version` | The snapshot version this section was last curated against, for drift (§1.4). | the system |
| `position` | Display order. | drag to reorder |

Nothing else. In particular a section carries **no** semantic layer, **no**
templates, **no** disclosure policy and **no** access rules of its own — those
all live on the connection and continue to.

### 1.2 Where the proposal comes from

Deterministic, no model call, and it runs over the snapshot the connection
already has. In order:

1. **Split by schema.** Tables in different database schemas are different
   domains far more often than not, and `schema_allowlist` already establishes
   that a schema is a meaningful unit here.
2. **Within a schema, take connected components of the foreign-key graph.** A
   component of 3–40 tables becomes one candidate section. This is the same
   instinct `_expand_by_fk` already encodes — tables that join are about the same
   thing — applied once, offline, instead of per question.
3. **Split an oversized component by leading name token.** A hub table joins
   everything to everything, so a real warehouse produces one component of 300
   tables. Split it on the first `snake_case` token — `order_*`, `billing_*`,
   `inventory_*` — which is how the tables were named by someone who was already
   thinking in sections.
4. **Everything left over goes to `Unassigned`.** Not a section: a bucket, always
   present, never routed to, shown at the bottom of the screen. **Every table in
   the snapshot is in exactly one place**, or the screen is lying.
5. **Name** from the schema, the common prefix, or the largest member table.
6. **Describe** from the semantic layer where there is one — the member entities'
   `label`, `description`, `grain` and `synonyms`, which `app/semantic/models.py`
   already holds and which today reach only the generate prompt — else from
   member table names plus their catalog comments.

Step 6 is research **L5** — *"names stop discriminating at scale; descriptions
are what you search"* — and it is the cheapest unclaimed win in the research
document, because the content already exists and nothing retrieval-shaped has
ever read it.

### 1.3 Sizing, and the warning nobody else in the product gives

Each section shows its table count and its estimated cost in
`metadata.table_chars` terms, against `_RETRIEVE_BUDGET_CHARS`, as one of three
states:

| State | Means | Shown as |
|---|---|---|
| **Fits whole** | `sum(table_chars) ≤ budget` | the good state — a question routed here gets `SECTION_SNAPSHOT`, which is the same retrieval quality the demo fixtures get today |
| **Too large** | over budget | amber, with the char estimate and one action: *Split this section* |
| **Empty** | no members | grey; it can never be routed to, and the router is not told about it |

This is the in-product warning the research matrix marks `○` for DataMind and
`●` for Fabric and Genie. It is also the only place the feature asks anything of
a person, and it asks for one specific thing with a stated payoff — which is the
condition §5.2 of the research sets for curation work being done at all.

### 1.4 Drift

A schema sync moves the ground under a section. The rule is the one the codebase
already applies to a semantic layer and a stale knowledge template: **flag it,
never delete it** — *"deleting a person's work to hide drift is worse than
showing it."*

- A member table that is no longer in the snapshot is shown struck through with
  *"not in the current schema — re-synced 12 Sept"*, and is skipped silently at
  retrieval time, because a table that is not in the snapshot cannot be rendered
  and cannot pass the guard anyway.
- A table new since `schema_version` and in no section appears in `Unassigned`
  with a *new* marker.
- A **Re-propose** button re-runs §1.2 over the current snapshot and shows the
  result as a diff against what is saved. It never applies itself.

---

## 2. The data model

### 2.1 `connection_sections`

```
connection_sections
  id              uuid        primary key
  connection_id   uuid        → database_connections ON DELETE CASCADE, indexed
  name            text        not null
  description     text        not null default ''
  tables          text[]      not null default '{}'   -- ["public.orders", …]
  origin          text        not null default 'PROPOSED'   -- PROPOSED | CURATED
  schema_version  int         null      -- snapshot version last curated against
  position        int         not null default 0
  created_at, updated_at      timestamptz

  UNIQUE (connection_id, lower(name))
```

**A table rather than a `text[][]` on `database_connections`**, for three
reasons that all bite: a section is edited one at a time and needs a stable id
for the editor to address; `name` needs a uniqueness constraint the database
enforces, because the name is the token a model replies with and two sections
called *Sales* is an unresolvable reply; and `position`, `origin` and
`schema_version` are per-section facts that a packed array would have to encode
by convention.

**No foreign key from a section to a table**, because there is nothing to point
at — the snapshot is one JSONB document (research §1.4). `tables` is text, and
a name that no longer resolves is drift, handled in §1.4, not a broken
reference.

### 2.2 Run telemetry (Phase 3)

```
runs
  + retrieval_strategy    text        -- FULL_SNAPSHOT | SECTION_SNAPSHOT | RANKED_MATCH | SCHEMA_QUESTION
  + retrieval_sections    text[]      -- section names the scope node chose, in order; empty when it did not run
  + retrieval_tables      int         -- how many tables reached the block
  + retrieval_chars       int         -- what the block cost, rendered
```

Names, not ids: deleting a section must not rewrite the history of the runs it
answered, and `semantic_layer_version` on the same table is the precedent for
recording *which artifact a run used* as a value rather than a reference.

These four columns are what turn every later argument about retrieval from taste
into arithmetic, and they are the instrument research §5.4(1) asks for. They are
in Phase 3 rather than Phase 2 only because Phase 2 can be read off the step
trail, which already persists `detail` per node.

### 2.3 Migration

`0035_connection_sections.py` — one `CREATE TABLE`, one index, one unique
constraint. `0036_run_retrieval_telemetry.py` in Phase 3 — four nullable
columns, no backfill, because a run that predates them genuinely has no answer
and a zero would be a lie.

---

## 3. The pipeline change

### 3.1 The `scope` node

New node, new `StepName.SCOPE`, between `match` and `retrieve` in **both**
graphs. Its whole job:

```python
async def scope(state: RunState, deps: NodeDeps) -> NodeResult:
    """Which part of the database is this question about?"""
```

**Why between `match` and `retrieve`**, and not anywhere else:

- **After `route`**, so small talk and an unsupported request halt before we
  spend a call on them.
- **After `match`**, so a question answered from the knowledge store — which
  needs no schema block at all — pays for no scope call. `match`'s hit exit
  names `validate` and jumps clean over this node, exactly as it jumps over
  `retrieve` today.
- **Before `retrieve`**, because it produces `retrieve`'s input.
- **Not folded into `route`.** Tempting — `route` already makes a call and
  already returns a label — and wrong: `route`'s prompt is deliberately tiny and
  frozen (two variants, one of which is the eval baseline's bytes), and hanging
  a per-connection list of section descriptions off it would make every
  classification prompt vary with curation. Two decisions, two prompts, two
  independent fail-opens.

**In both graphs, not just chat.** `_build_draft` is what a dashboard tile and a
report block are written through, and a tile author asking a question of a
2,000-table warehouse has exactly the problem this feature exists for. CLAUDE.md
is explicit that a difference between the two paths has to be *written* rather
than drifted into; there is no reason to write one here.

### 3.2 The prompt

```
SCOPE_SYSTEM:

You decide which part of a database a question is about.

Each section below is a named group of tables, with a description of what it
answers. Reply with the names of the sections needed, comma-separated, most
relevant first — at most three. Reply NONE if no section fits, or if the
question is about the database as a whole.

Prefer one section. Name a second only when the question plainly needs both —
when it compares, joins or reconciles two things that live in different
sections.

Sections:
- Sales — Orders, order lines, payments and refunds. Answers questions about
  revenue, order volume, discounts and returns.
- Inventory — …

Reply with section names only.
```

`SCOPE_SYSTEM_WITH_CURRENT` adds one line — `Currently answering from: Sales` —
and one instruction: a follow-up that names nothing new keeps the current
section. Two prompts rather than one with an empty slot, for the reason
`ROUTE_SYSTEM` / `ROUTE_SYSTEM_WITH_HISTORY` are two: the first question of a
conversation should send the bytes the first question of a conversation was
measured on.

**`PROMPT_VERSION` does not move.** It names the generate prompt an answer's SQL
was written against; the schema block's *format* is untouched by everything in
this document, and only *which tables are in it* changes. The scope prompt
produces no SQL and enters no stored statement's provenance. (If it ever needs
its own version, `SEMANTIC_PROMPT_VERSION = "s4"` is the precedent.)

### 3.3 Parsing the reply, and the four ways it falls open

Split on commas, strip, lower-case, match against the connection's section names.
Then, in order:

| Condition | Result | Posture |
|---|---|---|
| `deps.sections` is None or empty | node reports **SKIPPED**, no model call | the connection has not opted in; everything downstream is byte-identical to today |
| `state.intent == "METADATA"` | node reports **SKIPPED**, no model call | a schema question is about the whole database |
| `LLMError` | SKIPPED, detail *"provider error"* | **fail open** — same as `route` |
| reply is `NONE`, empty, or matches no section | SKIPPED, detail *"no section matched"* | **fail open** |
| reply names sections | `state.scope_sections`, `state.scope_tables` set | the feature working |

A fail-open step may never widen anything (CLAUDE.md). This one cannot: falling
open **widens what the model is shown** back to the whole snapshot, which is
precisely what it is shown today, and the guard's allowlist was never narrowed in
the first place (D2).

### 3.4 What `retrieve` becomes

```python
scoped = [t for t in tables if key(t) in state.scope_tables] or tables
approx = sum(table_chars(t) for t in scoped)

if approx <= _RETRIEVE_BUDGET_CHARS:
    selected  = scoped
    strategy  = "SECTION_SNAPSHOT" if state.scope_tables else "FULL_SNAPSHOT"
elif state.intent == "METADATA":
    selected  = select_tables(question, tables, budget_chars=_RETRIEVE_BUDGET_CHARS)
    strategy  = "SCHEMA_QUESTION"          # over `tables`, never `scoped`
else:
    seed      = match_tables(question, scoped) + carried_from_history   # ← Phase 0 · O1
    expanded  = _expand_by_fk(seed, scoped, relationships) if seed else scoped
    selected, dropped = fit_to_budget(expanded, seed, carried)          # ← Phase 0 · O2
    strategy  = "RANKED_MATCH"
```

Four things to notice.

**`or tables` is the whole fail-open, in two words.** An empty scope is not a
narrow scope; it is no scope.

**The FK hop happens inside the scoped set, and it happens before the cut.**
Research §6.2: *expand, then rank, then cut — never cut before expanding, or the
bridge is gone before it is scored.* A section's members plus one hop is the
join closure Power BI preserves across an AI data schema, and it is why a
question about *Sales* can still join through a bridge table nobody thought to
put in the section.

**`SCHEMA_QUESTION` reads `tables`, not `scoped`.** A METADATA question that
somehow reached here with a scope set — it cannot today, but a future edge could
— must still describe the whole database, or `census` starts stating a wrong
total, which is the exact failure `census` exists to prevent.

**`SECTION_SNAPSHOT` is the point of the entire feature.** It is `FULL_SNAPSHOT`
by another name: every column of every table in the section, the block shape the
demo fixtures get and the only path in the product that is actually good. The
customer path stops being the untuned one.

### 3.5 State

```python
# RunState
scope_sections: list[str] = []   # names the scope node chose, in order
scope_tables:   list[str] = []   # qualified keys: the union of their members

# RetrievedContext
strategy: Literal[
    "FULL_SNAPSHOT", "SECTION_SNAPSHOT", "RANKED_MATCH",
    "EXACT_MATCH", "TRIGRAM", "SCHEMA_QUESTION",      # kept: old runs read back
] = "FULL_SNAPSHOT"
dropped_tables: list[str] = []   # what the budget cut, for the step detail (§4.3)
```

`EXACT_MATCH` and `TRIGRAM` stay in the literal union although nothing writes
them after Phase 0. A `run_steps` row from before this ships is read back by the
same model.

### 3.6 `NodeDeps`

```python
# The connection's sections, or None when it has none. **None is the
# pre-feature behaviour exactly**: `scope` reports SKIPPED, makes no model
# call, writes nothing to the state, and the prompt the generator receives is
# byte-identical to the one it received before this node existed.
sections: list[SectionSpec] | None = None
```

The `matcher: TemplateMatcher | None = None` field two lines above it is the
precedent and the argument is identical: the eval runner and the draft harness
leave it None, so the suite's baseline stays comparable by construction rather
than by care.

---

## 4. Phase 0 — Repair the floor

**Size S. No new concepts, no dependency, no prompt change, no migration.**
Research options **O1** and **O2**, plus the drop reporting from research §6.5.

### 4.1 O1 — use the matcher that is already in the tree

`retrieve`'s analytical branch matches with Python substring containment and no
token boundary:

```python
'id' in "how many orders were paid last month?"   # True — pa-id
'on' in "…revenue by region last quarter?"        # True — regi-on
```

On a warehouse where most tables carry an `id` column, that question selects
**every table**, then `_expand_by_fk` amplifies it. `metadata.match_tables` —
written for `describe`, sitting twenty lines away, unit-tested, whose docstring
names this exact bug — does token-boundary matching with plural forms,
`snake_case → "snake case"` and specificity resolution.

Extend it to columns and call it. One call-site change, plus tests.

### 4.2 O2 — spend the budget on the branch that ignores it

`FULL_SNAPSHOT` *is* the budget test. `SCHEMA_QUESTION` spends the budget
explicitly. The analytical branch does neither, and nothing downstream clamps it:
`RetrievedContext.render` caps the comment block and the semantic block and
gates column hints, but renders the table list in full, one line per table,
however many there are.

`fit_to_budget(expanded, seed, carried)` ranks and cuts:

1. tables the question **named** (`match_tables` hits),
2. tables **carried** from the conversation's SQL,
3. tables reached by the **FK hop**, preferring one that completes a join path
   between two already-selected tables over one that connects to nothing,
4. ties broken by `approx_row_count`, descending,

and takes tables until `_RETRIEVE_BUDGET_CHARS` is spent, always at least one.
What was cut lands in `dropped_tables`.

**This is not "sending less".** It caps the customer path at exactly what the
other two branches already cap themselves at. `_RETRIEVE_BUDGET_CHARS` is not
touched, and the research's standing constraint — no reduction of the context
budget — holds.

### 4.3 Drop reporting

`census` already names the tables a schema question could not fit, and `describe`
tells the user. The analytical path returns a count and no names. It gains the
same honesty: `"42 tables via RANKED_MATCH · 9 not shown"`.

### 4.4 Deliverables and the gate

| Deliverable | Check |
|---|---|
| `match_tables` extended to columns; analytical branch calls it | `test_retrieve_matcher.py` — `"paid"` selects no table for an `id` column; `"customer addresses"` selects `customer_addresses` and not `customers` |
| `fit_to_budget`, ranked, with `dropped_tables` | `test_retrieve_budget.py` over a **synthetic 500-table snapshot** — rendered block `≤ _RETRIEVE_BUDGET_CHARS` on every branch, including a hub table with 400 inbound FKs |
| Keys and join paths survive the cut | same file — a bridge table between two selected tables outranks an unconnected one |
| Drop reporting in the step detail | `test_pipeline_events.py` extension |

**The eval suite is not touched and does not move.** Both fixtures estimate under
the budget (`aurora` ~6k, `sales` ~26.5k) and take `FULL_SNAPSHOT` on every
question, so this phase changes a path the suite has never executed. That is a
statement about the suite's blindness, not about the change's safety — which is
why the gate is a synthetic 500-table snapshot in a unit test, where
`_RETRIEVE_BUDGET_CHARS` is *a module constant so a test can lower it*, exactly
as its own comment says.

---

## 5. Phase 1 — Sections exist, and do nothing

**Size M. The store, the proposal, the screen. Nothing on the ask path changes.**

A person can open a connection, see a complete proposed division of their
database, rename it, move tables between sections, write descriptions, and save
— and every answer the product gives is byte-identical to the answer it gave
before, because nothing reads `connection_sections` yet.

This is the learning loop's Phase 1 pattern and it is the right one: the cost of
a bad proposal is a screen someone disagrees with, not a wrong answer.

### 5.1 Deliverables

| Deliverable | Check |
|---|---|
| `connection_sections` table + `0035` migration | `alembic upgrade head` on a clean database and on a populated one |
| `SectionService.propose(connection)` — §1.2, deterministic | `test_sections_propose.py` — same snapshot in, same sections out, every table in exactly one place, `Unassigned` holds the remainder |
| Proposal splits an oversized FK component by name prefix | same file — a 300-table hub component yields prefix groups, none over budget unless the prefix itself is |
| Descriptions drawn from the semantic layer when present | same file — an entity's `label` and `grain` reach the section description; no layer falls back to names + catalog comments |
| CRUD service + endpoints (§9) | `test_sections_api.py` — SELECT to read, MODIFY to write, 404-above-403 through `policy.require` |
| The Sections tab (§8) | Playwright: propose → rename → move a table → save → reload shows it |
| Sizing states, including the **too large** warning | unit test on the estimator; Playwright on the badge |
| Nothing on the ask path reads it | `test_pipeline_graph.py` — a connection with sections saved produces the same node sequence and the same rendered prompt as one without |

---

## 6. Phase 2 — The `scope` node

**Size M. This is where behaviour changes, and only on a connection that has
saved sections.**

### 6.1 Deliverables

| Deliverable | Check |
|---|---|
| `StepName.SCOPE`, `SCOPE_SYSTEM`, `SCOPE_SYSTEM_WITH_CURRENT` | — |
| The `scope` node | `test_scope_node.py` — a clean pick; two sections; `NONE`; an unknown name; an empty reply; `LLMError`; `deps.sections is None`; `intent == "METADATA"`. **Each of the last six produces the unscoped table set.** |
| Wired into `CHAT_GRAPH` **and** `DRAFT_GRAPH` | `test_pipeline_graph.py` — node order in both; `match`'s hit exit still jumps to `validate` without passing through `scope` |
| `retrieve` reads `state.scope_tables` (§3.4) | `test_retrieve_scope.py` over the synthetic 500-table snapshot — a scoped set that fits yields `SECTION_SNAPSHOT` with every column of every member; one that does not yields `RANKED_MATCH` **within the section** |
| FK hop inside the scope, before the cut | same file — a bridge table outside the section but one hop from two members is selected |
| `NodeDeps.sections` loaded by `run_service` and `sql_draft_service` | `test_run_service.py` |
| **The guard is untouched** | `test_section_guard_unaffected.py` — with a section that excludes `public.orders`: a chat answer naming `public.orders` still validates, and a saved tile over `public.orders` still executes. **This is D2 and it is the test that protects every saved artifact in the product.** |
| The step trail shows the pick | `"Sales · 14 tables"`, or `"no section matched"`, or `"skipped — provider error"` |
| A run's token spend for `scope` is recorded | the adapter already writes per-node usage; assert the row is non-null on a hit and absent on a SKIP |

### 6.2 What this costs

One extra model call per analytical question on a sectioned connection, on the
critical path, before `generate`. Honestly:

- **Tokens: likely negative.** The scope prompt is a list of names and one-line
  descriptions — order 1–3k characters for twenty sections. It replaces up to
  50k characters of schema in the *generate* prompt with the ~8k of one section.
  The run gets cheaper, not dearer, and §2.2's columns will say by how much.
- **Latency: positive, order 300–800ms**, for a short completion on a small
  prompt. Measurable per node from the step trail on day one.
- **A new failure mode:** covered by the six fail-open cases above, each with a
  test.

### 6.3 What the eval does

Nothing, and that is correct rather than convenient. The fixtures have no
sections, so `deps.sections` is None, so `scope` reports SKIPPED and writes
nothing — the generate prompt is byte-identical and the baseline stays
comparable.

Which also means **this phase ships unmeasured by the suite**, and the plan
should not pretend otherwise. What it ships measured by:

1. `test_retrieve_scope.py` over the synthetic 500-table snapshot — recall
   against a known-correct section is asserted, not estimated.
2. The step trail, on a real connection, from the first question.
3. §2.2's four columns, in Phase 3.

To claim *"sections improve retrieval recall"* with a number requires
[mvp2 §B1](mvp2.md) — un-blinding the eval by lowering the ceiling for a run or
widening the fixture — which is a prerequisite for the **claim**, not for the
**ship**. It is out of scope here and named so nobody is surprised.

---

## 7. Phase 3 — Make it trustworthy

**Size S–M.** Research §6.5: *without this, every retrieval improvement is
unfalsifiable from the outside.* Four things, all small, all about a person being
able to see and correct a decision a model made on their behalf.

| Deliverable | Check |
|---|---|
| **Stickiness.** The section chosen for the previous turn is passed as `Currently answering from`, and a follow-up that names nothing keeps it. | `test_scope_node.py` — `"and by month?"` after a Sales turn stays in Sales |
| **The section is shown on the answer.** A small chip — *Answered from **Sales*** — beside the existing strategy affordances in the chat trail. | Playwright |
| **A person can override.** A picker in the composer: *Ask within…* → any section, or *Whole database*. An explicit choice skips the model call entirely and is reported as such. | Playwright + `test_scope_node.py` (an explicit scope makes no call) |
| **Drift.** §1.4 — struck-through missing members, `new` markers in `Unassigned`, **Re-propose** showing a diff. | `test_sections_drift.py` — a sync that drops a table leaves the section saved and the member flagged |
| **Telemetry.** §2.2's four columns + `0036`. | `test_run_service.py`; then one query answering *"how often does a real connection take each strategy, and how big does the block get?"* — the two questions mvp2 §1.2 cannot currently answer |

The override is the row the research matrix marks `○` for DataMind and `●` for
Data Formulator and Wren — *"the user can correct the retrieval decision"* — and
once sections exist it is a dropdown.

---

## 8. UI

### 8.1 Where it lives

A seventh tab on the data source detail, between `schema` and `semantic`:

```
Connection · Policy · Access · Schema · Sections · Semantic layer · Knowledge
```

Between those two deliberately. Sections are read *from* the schema and
described *by* the semantic layer, and the tab order is the order someone sets
them up in. `TABS` in [DataSourcesPage.tsx](../../frontend/src/pages/DataSourcesPage.tsx)
is the one-line change; the URL is `/sources/:id/sections`, so *"where do I set
this up?"* is a link.

### 8.2 The screen

One column of cards, one per section, `Unassigned` pinned last:

```
┌──────────────────────────────────────────────────────────────┐
│ Sales                                        14 tables · 9.2k │
│ Orders, order lines, payments and refunds. Answers questions │
│ about revenue, order volume, discounts and returns.          │
│                                                    Fits whole │
│ public.orders  public.order_items  public.payments  …        │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ Operations                                  310 tables · 190k │
│ …                                                             │
│ ⚠ Too large to send whole — a question here falls back to     │
│   matching within the section.          [ Split this section ]│
└──────────────────────────────────────────────────────────────┘
```

- **First open with nothing saved** shows the proposal with a banner: *"We
  divided your 2,000 tables into 23 sections. Check them, then save."* — one
  primary action, `Use these sections`. Never a blank form (D4).
- **Editing** is a table chip you drag, or a search box that adds one. The
  description is the field the copy pushes hardest on, because it is what the
  model reads — the field label says so.
- **Empty state, no snapshot:** the same sentence the rest of the product uses —
  *"This connection has no schema snapshot. Sync it, then try again."*
- **The estimate is honest about what it is:** the same `table_chars` figure
  `retrieve` decides with, so the badge and the runtime never disagree.

### 8.3 In chat

The step chip gains a label — `NODE_META.scope = { label: 'Scope', detail:
'Choosing which part of the database…' }` — and the answer carries the section
chip from §7. A SKIPPED scope shows nothing at all, so a connection without
sections sees the interface it sees today.

---

## 9. API surface

All five under the connection, all through `services/policy.require` with the
privileges [semantic.py](../../backend/app/api/v1/semantic.py) already
establishes for connection-owned artifacts.

| Method | Path | Privilege |
|---|---|---|
| `GET` | `/api/v1/connections/{id}/sections` | `SELECT` |
| `POST` | `/api/v1/connections/{id}/sections/propose` | `SELECT` — computes and returns, **writes nothing** |
| `PUT` | `/api/v1/connections/{id}/sections` | `MODIFY` — the whole set, in one transaction, so a move between two sections cannot half-apply |
| `DELETE` | `/api/v1/connections/{id}/sections/{section_id}` | `MODIFY` |
| `DELETE` | `/api/v1/connections/{id}/sections` | `MODIFY` — turns the feature off for this connection; sections are gone, retrieval is today's |

`PUT` takes the whole set rather than patching one section because sections
partition a set of tables: moving `public.refunds` from *Operations* to *Sales*
is one edit to two rows, and two requests can interleave into a table that is in
both or neither.

---

## 10. Security, disclosure, and the guard

**The guard gets no new entry point and no exemption.** Sections are read by
`retrieve` and by nothing else. `policy_from_snapshot` is not touched, is not
passed a section, and does not know the feature exists. The test named in §6.1
is the one that keeps this true.

**Section text travels with structure.** A name and a description are prose a
person or the semantic layer wrote about the shape of the database — the same
rung as a catalog comment, which `RetrievedContext.render` already sends under
every policy including `NONE`, on the stated grounds that it is DDL a human
wrote and is exactly as much customer data as a column name. Two consequences,
both written into `docs/reference/security.md`:

- A description is **never** derived from row values. The proposal reads names,
  comments and the semantic layer, and nothing that came out of a `SELECT`.
- A person may type anything into a description, including a literal. That is
  the same bet the product already takes on a hand-authored knowledge template's
  literals, and it is theirs to take — but the field's help text says where the
  text goes, which is *to the model, on every question*.

**Access control follows the connection.** No new resource type, no new
privilege, no entry in the lattice. A section is part of a connection the way a
semantic layer is, and `require` is called the same way in the same place.

---

## 11. Risks, and what we do about each

| Risk | What we do |
|---|---|
| **The router picks the wrong section, invisibly.** The failure looks like a confidently wrong answer, which is the worst shape a failure can have. | Show the pick on the answer (§7), let a person override it (§7), and let the router name up to three sections rather than forcing a single choice. `NONE` falls back to the whole database rather than guessing. |
| **Nobody curates, so the proposal is what ships.** | Make the proposal good enough to ship: §1.2 is deterministic, complete and derived from the FK graph the database itself declares. The one thing curation buys — a better `description` — is the one thing the screen asks for, with the payoff stated. |
| **A section is too large and the fallback is the old bug.** | That is precisely why Phase 0 is first, and why the sizing badge names the problem in the screen where it can be fixed. |
| **An extra model call on the critical path.** | Six fail-open cases with a test each; the prompt is ~1–3k characters; the trail reports its cost per run from day one. |
| **Sections drift from the schema.** | Flag, never delete (§1.4). A member that no longer resolves cannot be retrieved and cannot pass the guard, so drift degrades recall — it never produces a wrong statement. |
| **Someone asks for the guard to be narrowed to the section.** | It is a reasonable request and it is a **separate, opt-in feature with a pre-flight check** listing which tiles and report blocks it would break (research §6.1). Not in this plan. Saying no to it here is most of what keeps this plan **M**. |
| **The claim outruns the measurement.** | §6.3. The suite does not move and cannot; the number that would justify "recall improved" needs mvp2 §B1 first, and this document says so rather than quoting an estimate. |

---

## 12. Where this leads

Recorded so the next person does not have to re-derive it, and **not built
here**:

- **Column pruning inside a section** (research O3). Matters much less once a
  section fits whole; the place to reach for it is a section that is legitimately
  wide and cannot be split.
- **A searchable derived table** — `(connection_id, schema, table,
  searchable_text, tsvector)` — written by the sync (research O7, §6.3). It would
  make `RANKED_MATCH` a ranked lexical query instead of a Python pass, and it is
  the precondition for embeddings. The knowledge store's `pg_trgm` work is the
  pattern.
- **Embeddings over section descriptions**, to pick a section without a
  completion call. `knowledge_templates` already stores vectors without pgvector
  (`0021_knowledge_embeddings`), so the route exists.
- **Per-section semantic layers, verified pairs and accuracy scores** — research
  O5 as written. Every one of them hangs off a section that will already exist.

---

## 13. File-by-file change map

**Backend**

| File | Change | Phase |
|---|---|:--:|
| `app/pipeline/nodes/__init__.py` | `scope` node; `retrieve` rewritten per §3.4; `fit_to_budget`; `NodeDeps.sections` | 0, 2 |
| `app/pipeline/metadata.py` | `match_tables` extended to columns | 0 |
| `app/pipeline/state.py` | `RunState.scope_*`; `RetrievedContext.strategy` + `dropped_tables` | 0, 2 |
| `app/pipeline/graph.py` | `SCOPE` wired into both graphs | 2 |
| `app/pipeline/prompts/__init__.py` | `SCOPE_SYSTEM`, `SCOPE_SYSTEM_WITH_CURRENT` | 2 |
| `app/domain/value_objects/__init__.py` | `StepName.SCOPE` | 2 |
| `app/infra/db/models.py` | `ConnectionSection`; `Run` telemetry columns | 1, 3 |
| `app/infra/db/migrations/versions/0035_connection_sections.py` | new | 1 |
| `app/infra/db/migrations/versions/0036_run_retrieval_telemetry.py` | new | 3 |
| `app/services/section_service.py` | new — propose, read, save, drift | 1 |
| `app/services/run_service.py`, `app/services/sql_draft_service.py` | load sections into `NodeDeps` | 2 |
| `app/api/v1/sections.py`, `app/api/schemas.py` | the five endpoints | 1 |

**Frontend**

| File | Change | Phase |
|---|---|:--:|
| `src/components/sections.tsx` | new — the curation screen | 1 |
| `src/pages/DataSourcesPage.tsx` | the sixth tab | 1 |
| `src/api/types.ts`, `src/api/client` | `Section` and the five calls | 1 |
| `src/theme/tokens.ts` | `NODE_META.scope` | 2 |
| `src/components/chat.tsx` | the section chip; the *Ask within…* picker | 2, 3 |

**Docs**

| File | Change |
|---|---|
| `docs/reference/pipeline-chat.md` | the new node, its fail-open table, the strategy values |
| `docs/reference/security.md` | §10 — section text on the disclosure ladder |
| `docs/reference/catalog-metadata.md` | proposals read comments and the semantic layer |
| `CLAUDE.md` | code map, the node list, the three-pipelines table |

---

## 14. Progress ledger

Tick a box in the commit that lands the work, never ahead of it.

### Phase 0 — Repair the floor
- [x] `match_tables` extended to columns · *`test_retrieve_matcher.py` green*
- [x] Analytical branch calls it · *`'paid'` selects no `id` table*
- [x] `fit_to_budget` ranked and bounded · *500-table synthetic block ≤ budget*
- [x] Join paths survive the cut · *bridge outranks unconnected*
- [x] Drop reporting in the step detail · *`test_pipeline_events.py`*

> Landed 2026-09-19. One refinement to §4.2's ranking: **carried** tables rank
> *above* tables the question named only **by a column**. "and by status?"
> names a column half a warehouse has, and the table the follow-up continues
> must not lose its place to every other table with a `status`. The order is
> named-by-table → carried → named-by-column → FK hop (bridges first) → rest.
> The rendered-block check is asserted under `NONE` on the synthetic snapshot;
> the estimate (`table_chars`) is what every branch is bounded by, as before.

### Phase 1 — Sections exist, and do nothing
- [x] `connection_sections` + `0035` · *`alembic upgrade head`*
- [x] `SectionService.propose` deterministic and total · *every table placed once*
- [x] Oversized components split by prefix · *no proposed section over budget unless the prefix is*
- [x] Descriptions from the semantic layer, else names + comments
- [x] Five endpoints, authorized · *`test_sections_api.py`*
- [x] The Sections tab, with sizing states · *Playwright*
- [x] **The ask path is unchanged** · *same nodes, same prompt bytes, sections saved*

> Landed 2026-09-19. How each box was proved:
> `0035` ran `alembic upgrade head` on a clean database and on a clone of a
> populated one (and `downgrade 0034` → `upgrade` again), with
> `test_sections_models.py` holding the migration and the ORM to one shape.
> The proposal algorithm is `app/pipeline/sections.py` (pure) under
> `SectionService` — `test_sections_propose.py`. Endpoints ask about the
> **connection** (`select` / `modify`), with no new resource type —
> `test_sections_api.py` on the access `World`. The tab was driven with
> Playwright against a scratch clone in both themes: propose → rename → drag a
> table between cards → add one by name → save → reload shows it, and the
> *Too large* badge and *Split this section* under a lowered budget. The ask
> path check is structural (`test_pipeline_graph.py`): nothing that builds a
> run's inputs imports the store, and `NodeDeps` has no field for it.
>
> Three things the plan left open, decided here: a component or prefix group
> needs **3 tables** to stand alone (smaller goes to a name-prefix pool, then
> `Unassigned`); a table whose prefix is too rare **joins the prefix group it
> has the most foreign keys to**, if that group still fits; and *Split this
> section* is `POST …/sections/propose` with `{"tables": [...]}` — the same
> algorithm over one section's members, applied as a draft the person saves.
> `NONE` and `Unassigned` are reserved names, and a name may not contain a
> comma — it is the token the router replies with.

### Phase 2 — The `scope` node
- [ ] `StepName.SCOPE` + both prompts
- [ ] The node, with all six fail-open cases · *`test_scope_node.py`*
- [ ] Wired into `CHAT_GRAPH` and `DRAFT_GRAPH` · *`test_pipeline_graph.py`*
- [ ] `retrieve` honours `scope_tables`; `SECTION_SNAPSHOT` · *`test_retrieve_scope.py`*
- [ ] FK hop inside the scope, before the cut
- [ ] `NodeDeps.sections` loaded on both paths
- [ ] **The guard is untouched** · *`test_section_guard_unaffected.py`*
- [ ] The pick is in the step trail; `scope` usage is recorded

### Phase 3 — Make it trustworthy
- [ ] Stickiness across a follow-up
- [ ] The section chip on an answer
- [ ] *Ask within…* override, skipping the call
- [ ] Drift: flagged members, `new` markers, Re-propose diff
- [ ] Telemetry columns + `0036`, and the first distribution read off them
