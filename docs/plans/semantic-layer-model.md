# The semantic layer as a model — build plan

> **Status: Phases 0 and 1 landed 2026-09-15.** Written 2026-09-15 against
> `main` at `d7cba6f`; the §14 ledger is the record of what is in the tree
> since. **Migration numbers moved:** `0030` and `0031` went to the usage screen
> while this was being written, so the plan's `0030_semantic_versions` shipped
> as **`0032`**, and Phase 2's and Phase 3's migrations will be `0033` and
> `0034`.
>
> **Answers** [mvp2.md §1.3](mvp2.md#13-the-semantic-layer-is-a-blob-not-a-model)
> — *"The semantic layer is a blob, not a model"*, ranked **High**. The render
> bug under it was fixed on 2026-08-30 (A6). This plan covers the design limits
> that fix left in place.
>
> **The argument behind it** is
> [research/semantic-layer.md](../research/semantic-layer.md) (2026-08-31). This
> plan takes that document's options and decides them again, against the tree as
> it is **now**. Since the research was written, the learning loop, access
> control and token accounting have all merged, and §1.3 lists five places where
> the research no longer holds.
>
> Read [reference/semantic-layer.md](../reference/semantic-layer.md) for the
> built feature, and [reference/access-control.md](../reference/access-control.md)
> before adding any route below.

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

*A connection owner can see who changed the `revenue` metric, when and why;
change it in a draft that no answer sees; publish it; and see which answers used
the new definition. And a column that a re-sync dropped never reaches the model
again.*

### 0.2 What "a blob" means in this code, precisely

Three things. The third is not in mvp2 §1.3, because it only shows up once you
read every piece of code that consumes the layer.

1. **It has no history.** `semantic_layers` holds one row per connection, and
   `PUT /connections/{id}/semantic` overwrites `document` in place. There is no
   version, author, note, diff or restore. A `REPLACE` generation or a wrong
   save cannot be undone. Nothing about the layer is written to `audit_logs`,
   even though that table now records about forty other actions.
2. **Its definitions are advisory.** A metric reaches the prompt as one line of
   text ([render.py:442](../../backend/app/semantic/render.py#L442)), and no
   code reads it after that. The run records no difference between SQL that
   used the definition and SQL that ignored it.
3. **Most of the code that reads it treats it as untyped JSON, and those readers
   disagree about what it says.** The typed model (`SemanticDocument`) and its
   binder (`validate_document`, which checks the document against a schema
   snapshot) both exist. But four of the six consumers skip one or both, and
   each skip is a live defect (§1.2).

Defect 3 gets fixed first, because every later phase adds another reader.

### 0.3 The phases

| Phase | What | Size | Moves `PROMPT_VERSION` | Can change an answer | Migration |
|---|---|:--:|:--:|---|---|
| **0** | **One reader.** Bind on load; the benchmark reads the layer; the backlog reads the model; the badge respects the switch | S | v9 → v10 | only where the layer had drifted from the schema | — |
| **1** | **Versions.** Every save becomes a numbered version with author, note and typed changes; a revision check on writes; history and restore; runs record which version answered them | M | no | no | `0030` |
| **2** | **Draft and publish.** Edits, generation, restore and import all land in a draft; publishing is a separate act; a draft can be scored before it is published | M | no | no (no run ever reads a draft) | `0031` |
| **3** | **Metric attribution.** Each metric gets a verdict of used, ignored or unknown, computed after the run; a chip on the answer; metric use per connection | M | no | no | `0032` |
| **4** | **Portable document.** Export and import | S | no | no | — |
| **5** | **Upkeep.** Regenerate chosen tables with a fill-gaps mode; a needs-attention filter | M | no | not until published | — |

**Phases 2 and 3 are independent once Phase 1 has landed.** Draft/publish comes
first here, which reverses the research's order; §1.3 explains why. Deferred
work, each item with a trigger for revisiting it, is in §11.

---

## 1. What the code does today

### 1.1 Six readers of the layer, and what each one trusts

| Reader | Where | Typed? | Bound to the current snapshot? | Respects `semantic_layer_enabled`? |
|---|---|:--:|:--:|:--:|
| Editor `GET` | [`SemanticService.read`](../../backend/app/services/semantic_service.py#L105) | yes | **yes** | n/a |
| Chat run, SQL draft, report outline, report time conventions | [`load_document`](../../backend/app/services/semantic_service.py#L520), called from `run_service.py:513`, `sql_draft_service.py:618`, `report_service.py:553` and `:799` | yes | **no** | yes |
| In-product benchmark | [`workers/benchmark.py:359`](../../backend/app/workers/benchmark.py#L359) | — | — | — |
| Eval harness | [`eval/runner.py:471` `load_semantic`](../../backend/app/eval/runner.py#L471) | yes | **yes**, and it aborts on a broken entry | n/a |
| The *Grounded* tier on an answer | [`conversations.py:492` `_all_described`](../../backend/app/api/v1/conversations.py#L492) | **no: a dict** | **no**: trusts the stored `valid` flag | **no** |
| The knowledge backlog's vocabulary | [`knowledge/backlog.py:170`](../../backend/app/knowledge/backlog.py#L170), via `knowledge_service.py:1657` | **no: a dict** | no | no |

Only the eval and the editor read the layer the way the reference doc describes.
The product's answer path does not.

### 1.2 Five defects found while writing this plan

#### 1.2.1 A column dropped by a re-sync still reaches the model

`save()` binds the document and **stores** the verdicts: a `valid` flag and an
`issue` on every entity, column and metric. The renderer keeps an entry out of
the prompt by reading those flags (`_scoped`,
[render.py:344](../../backend/app/semantic/render.py#L344), and
`_entity_lines`). But `load_document` deserialises the stored JSON without
binding it again. `sync_schema`
([connections.py:382](../../backend/app/api/v1/connections.py#L382))
re-validates the knowledge store (`sweep_staleness`, `:456`) and not the layer.
So the flags are only as current as the last save.

I reproduced this against the tree, using pure functions and no database. A
layer was saved against `orders(id, amount, status)`, then the table was
re-synced as `orders(id, total_cents)`:

```
run path   load_document → render     metric revenue = SUM(amount) WHERE status <> 'CANCELLED'.
                                      amount: order value; in USD.
editor     read → validate → render   (both lines gone; the metric shows red:
                                      "`amount` is not a column of public.orders.")
```

The guard still refuses any SQL that names `amount`, so this is not a safety
hole. The cost is a repair loop spent on a definition the editor already marks
as broken. It also contradicts the reference doc's *"Flagged entries never reach
the prompt"*, which is only true of flags computed before the schema moved. The
eval cannot see the problem, because `load_semantic` binds.

#### 1.2.2 The customer's accuracy score is measured without the semantic layer

`_run_question` builds `NodeDeps` without passing `semantic=`, so the field
defaults to `None`. The docstring says *"Everything else is exactly the ask
path."* For any connection with a layer, that is not true: the benchmark scores a
prompt with no layer, while chat answers with one.

That has two consequences. First, the score on `/knowledge/:id` is not the
product's score. Second, the payoff mvp2 A3 promises (*"A/B your own layer and
watch the score move"*) cannot happen, because toggling
`semantic_layer_enabled` changes nothing the benchmark renders.

#### 1.2.3 *Grounded* can describe a layer the answer never saw

`_knowledge` ([conversations.py:438](../../backend/app/api/v1/conversations.py#L438))
awards GROUNDED when every table the SQL touched has an entity in the layer.
`_all_described` reads `semantic_layers.document` as a dict, at *read* time.
That means:

- it ignores `semantic_layer_enabled`, so an answer written with the layer off
  is still labelled Grounded;
- it reads the layer as it is *now*, so describing a table a week after a
  question was asked turns that old answer Grounded after the fact;
- it trusts the stored `valid` flags (§1.2.1).

The read-time computation was a deliberate choice: *"an answer's Grounded claim
is a statement about what is described now."* That was sound while the layer
had no versions. Once runs record which version they were written against
(Phase 1), "described in the layer this answer was written with" can be derived,
and it is the honest reading.

#### 1.2.4 The backlog's vocabulary ignores every label a curator writes

`build_vocabulary` reads `entity["business_name"]`, `column["business_name"]`,
`metric["business_name"]` and `term["synonyms"]`. None of those four keys has
ever existed. The model's fields are `label` and `synonyms` on entities, columns
and metrics, and `term` plus `maps_to` on glossary terms. Labels, column synonyms
and glossary targets are therefore treated as unknown words, and *"what to teach
next"* suggests words the layer already explains.

The dict read is not careless. `app.knowledge` sits **below** `app.semantic` in
the layers contract
([pyproject.toml:94](../../backend/pyproject.toml#L94)), so it cannot import the
model. The fix goes through the service layer (P0.3).

#### 1.2.5 A generation and a save can silently overwrite each other

`execute_job` reads the existing document when the job **starts**
([semantic_service.py:290](../../backend/app/services/semantic_service.py#L290)),
spends minutes at the provider, and then writes `merge_documents(existing,
generated)` over the row (`:355`, `_persist_generated` at `:386`). The editor
does not disable Save while a job runs
([semantic.tsx:459](../../frontend/src/components/semantic.tsx#L459)), and the
API does not refuse the save either. So:

- a save made **during** a generation is overwritten when the job commits;
- an editor holding unsaved changes when the job ends keeps its old document
  (`semantic.tsx:134` deliberately does not adopt the reload), and its next Save
  overwrites what the job wrote.

Neither direction leaves a trace. Since access control landed (2026-09-08), a
layer can be granted to a team, so two people editing the same layer is now
normal, and the whole-document `PUT` has no precondition at all.

### 1.3 Where the research no longer holds

| The research said | Now | What that changes here |
|---|---|---|
| `audit_logs` has no writer, so versions would be its first (§5.1) | `services/audit.py` records about forty actions across curation, access and the ask path. **None of them concerns the semantic layer**, and toggling `semantic_layer_enabled` is not audited either | Layer writes join an existing vocabulary. No new machinery |
| Approval is theatre in a single-player product (§8.7) | A layer is a grantable resource (`ResourceType.SEMANTIC_LAYER`, with teams and grants) | Concurrency is now a correctness issue (§1.2.5). Approval is still deferred, but for a different reason (§11) |
| Draft/publish has no visible payoff at publish time, so attribution should come first (§6.9) | The in-product benchmark exists (learning loop Phase 6) | A draft can be **scored** before it is published, once §1.2.2 is fixed. That gives publishing a payoff, so draft/publish moves ahead of attribution |
| Attribution plugs in as a step after `validate` (§7.2) | The graph's SSE event sequence is a tested contract (`test_pipeline_events.py`), and runs already store their final SQL | Attribution runs **after** the run, over the stored SQL and the recorded version. No new node and no event change |
| Import is "a fifth way into stored SQL" and needs a hostile-corpus replay (§8.6) | Nothing executes a metric expression. It is prompt text, and the SQL the model writes after reading it is guarded like any other | Import is **not** a new guard entry point. It is equivalent to typing into the editor and is gated that way (§7.1). The replay is still worth running, as evidence for expansion if that is ever built |
| Keep 50 versions and prune the rest (§8.4) | Runs will point at versions (Phase 1) | Keep every version, because pruning breaks provenance. Retention gets a trigger (§11) |

One more correction: [reference/semantic-layer.md](../reference/semantic-layer.md)
names a `merge.py`. No such file exists; `merge_documents` lives in
[validate.py:354](../../backend/app/semantic/validate.py#L354).

### 1.4 What is already right, and must survive

- **Flag, don't drop.** A person's invalid entry stays visible and stays out of
  the prompt.
- **Off by absence.** With no layer, the prompt is byte-identical to the prompt
  from before the feature existed.
- **Joins are derived** from the catalog and never accepted from a client.
- **A metric name means one thing** (`_refuse_ambiguous_metrics`).
- **The render cap is an allocation**: tiered and round-robin.
- **Regeneration never costs a person their work** (`merge_documents` together
  with `provenance.edited`).
- **The live expression check uses the same parser as save.**
- **Drift and re-key detection** in the editor (`semantic-drift.ts`), which the
  research found no competitor matching.

---

## 2. The target model

### 2.1 The pieces

```
semantic_layers                  the head: one row per connection (same identity as today)
  ├─ document                    PUBLISHED: the only bytes any run, draft, report or benchmark reads
  ├─ published_version           which version `document` is
  ├─ revision                    increases on every write to this row; the concurrency token
  └─ draft_document              (Phase 2) unpublished edits, or NULL when there are none

semantic_layer_versions          immutable, numbered, linear: every document that ever reached the model
semantic_layer_changes           each version's typed changes against its parent, one row per entry

runs.semantic_layer_version            which version answered (0 = no layer reached the prompt)
benchmark_runs.semantic_layer_version  which version was scored (Phase 2 adds: and whether it was a draft)
generated_queries.metric_use           (Phase 3) which definitions the statement matched
```

### 2.2 The decisions

**D1: The document stays the unit of storage, and the entry is the unit of
change.** The document is not normalised into `semantic_entities` and
`semantic_metrics` tables. The renderer, the binder, the ambiguous-metric check
and the editor all work on a whole document, and moving a metric from one entity
to another is one atomic document edit. That atomicity is the stated reason the
`PUT` takes a whole document ([semantic.py:133](../../backend/app/api/v1/semantic.py#L133)).
What TMDL's one-file-per-table gives Power BI (a unit a reviewer can read) comes
here from the typed differ (D5), not from the storage shape. *Reversing this
later is expensive:* it would rewrite every consumer.

**D2: A version is a document that reached the model.** Versions are immutable,
numbered per connection, linear, and never branch. In Phase 1 every save is a
version, because every save takes effect. From Phase 2 on only a publish creates
one; a draft is working state, not history. This keeps the promise the
`SemanticLayerRow` docstring protects (*"a user who fixes a grain statement
expects to have fixed it, not to have forked it"*) and restores what that
promise cost. History is a log, and nobody ever has to choose between versions
except by deliberately restoring one into the draft.

**D3: `semantic_layers.document` keeps its meaning, "what the model reads".** It
becomes a copy of the published version, written in the same transaction as the
version row, so none of its readers change. The alternative, reading
`semantic_layer_versions` on every run, would move the feature's most important
read for no gain. A test asserts
`sha256(document) == versions[published_version].document_sha256` after every
write path.

**D4: One binder, and every reader goes through it.**
`bind_layer(raw, snapshot)` deserialises the document, runs `validate_document`,
and runs `derive_joins`, all against the snapshot the caller already holds.
Validity is derived at read time, every time. The stored `valid` and `issue`
flags stay in the JSON (the editor displays them), but no consumer trusts them.
The knowledge store already follows this rule: guarded on save and guarded again
on read.

**D5: One differ, in the backend.** `app/semantic/diff.py` returns typed changes
keyed by entry. The API returns those changes. The frontend groups them and
words them (`semantic-changes.ts`) and never computes a diff itself. A second
differ in TypeScript would be two functions that must agree forever, which is the
failure `covered_keys` was rewritten to avoid.

**D6: Concurrency is one integer, and a conflict is a refusal.** Every write
carries a `base_revision`. If it does not match, the server returns 409 naming
who wrote in the meantime. This plan does not merge two people's edits on the
server (§11 has the trigger for that). The one exception is a generation job,
because it is not a person: it merges its output into the **current** row, under
a lock, rather than into the document it read minutes earlier.

**D7: Publishing requires `modify`, the same privilege editing already
requires.** No approval step, and no new privilege in the lattice. The value
available now is separating the edit from its effect. A two-person rule is a
separate decision with its own trigger (§11).

**D8: Attribution observes and never enforces.** There are three verdicts:
`used`, `ignored` and `unknown`. `unknown` is the default, and `ignored` requires
a filter from the definition to be demonstrably absent. Attribution is computed
after the run, over stored SQL, and stored as a value. It never fails, blocks or
rewrites anything. The semantic layer stays **fail open** in the posture table in
CLAUDE.md. Expansion, which would change that, is deferred (§11).

**D9: An export carries no values from the data unless asked, and an import
lands in the draft.** `value_meanings` are keyed by real column values.
`dashboard_transfer` sets this codebase's precedent for a file leaving the system
(*"a document carries no results"*), so the same rule applies here. Import is
typing by another route: same privilege, same binder, and never straight to
published.

**D10: Deleting a layer keeps its history.** Delete publishes an empty document
as a new version, with `origin: {"deleted": true}`. Versions are removed only
when their connection is. This is the same instinct as `SET NULL` on runs:
deleting history to tidy up a table is the wrong trade.

**D11: `PROMPT_VERSION` moves from v9 to v10 in Phase 0.** CLAUDE.md's rule is
that *"a change to how much of the schema block survives moves it too."* Binding
on load changes what survives on any drifted layer, and the benchmark fix puts a
layer into benchmark prompts that never had one. The bump also separates
benchmark runs taken without the layer from runs taken with it, because
`benchmark_runs.prompt_version` already records the constant. Phase 0's three
eval baselines have not been taken yet (status.md §3), so no existing measurement
is invalidated. No later phase moves it.

---

## 3. The data model

### 3.1 Migration `0030_semantic_versions` (Phase 1)

```sql
ALTER TABLE semantic_layers
  ADD COLUMN revision          integer NOT NULL DEFAULT 0,
  ADD COLUMN published_version integer;                -- NULL until something is published

CREATE TABLE semantic_layer_versions (
  id               uuid PRIMARY KEY,
  connection_id    uuid NOT NULL REFERENCES database_connections(id) ON DELETE CASCADE,
  version          integer NOT NULL,                   -- 1, 2, 3 … per connection
  parent_version   integer,                            -- NULL for the first
  document         jsonb    NOT NULL,                  -- as bound at publish time
  document_sha256  char(64) NOT NULL,                  -- of the canonical JSON
  schema_version   integer  NOT NULL,                  -- the snapshot it was bound to
  published_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  note             text  NOT NULL DEFAULT '',
  origin           jsonb NOT NULL DEFAULT '{}',        -- generated_job_ids, restored_from, imported, deleted, migrated
  entity_count     integer NOT NULL DEFAULT 0,
  metric_count     integer NOT NULL DEFAULT 0,
  reviewed_count   integer NOT NULL DEFAULT 0,
  issue_count      integer NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (connection_id, version)
);

CREATE TABLE semantic_layer_changes (
  id             uuid PRIMARY KEY,
  version_id     uuid NOT NULL REFERENCES semantic_layer_versions(id) ON DELETE CASCADE,
  connection_id  uuid NOT NULL,                        -- denormalised for the history query
  kind           varchar(40) NOT NULL,                 -- §4.2's vocabulary
  entity_key     text NOT NULL DEFAULT '',             -- "schema.table", or '' for document-level
  item_key       text NOT NULL DEFAULT '',             -- metric, column or glossary term
  affects_sql    boolean NOT NULL DEFAULT false
);
CREATE INDEX ix_semantic_changes_entry
  ON semantic_layer_changes (connection_id, entity_key, item_key);

ALTER TABLE runs           ADD COLUMN semantic_layer_version integer;  -- NULL = not recorded (before 0030)
ALTER TABLE benchmark_runs ADD COLUMN semantic_layer_version integer;  -- 0 = no layer reached the prompt
```

**Backfill.** Every `semantic_layers` row with a non-empty document becomes
version 1, with `published_by` NULL, `origin {"migrated": true}`, and the note
*"Recorded when versioning was introduced. Nothing before this point was
kept."* It also gets `published_version = 1` and `revision = 1`. No author is
invented; this is the same rule that left the `prompt_version` rows from before
2026-08-31 unrewritten. The canonical JSON is
`json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, the
same in the migration and in the service.

**Why change rows store keys and not content.** A change row records *which
entry* changed. The before and after are recomputed from two immutable versions
when someone asks. Storing the content would create a second copy of every
definition, and the history query only needs the index.

**Size.** The `sales` layer has 42 entities, and a bound document is roughly
100 kB. A version is written per human save (Phase 1) or per publish (Phase 2),
never per keystroke and never per run. Measure the table on the demo connections
after Phase 1, before deciding anything about retention (§11).

### 3.2 Migration `0031_semantic_drafts` (Phase 2)

```sql
ALTER TABLE semantic_layers
  ADD COLUMN draft_document    jsonb,                        -- NULL: nothing unpublished
  ADD COLUMN draft_updated_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN draft_updated_at  timestamptz,
  ADD COLUMN draft_origin      jsonb NOT NULL DEFAULT '{}';  -- folded into the version's origin on publish

ALTER TABLE benchmark_runs
  ADD COLUMN semantic_source   varchar(20) NOT NULL DEFAULT 'PUBLISHED',  -- PUBLISHED | DRAFT
  ADD COLUMN semantic_revision integer;                                  -- the draft revision scored
```

### 3.3 Migration `0032_metric_attribution` (Phase 3)

```sql
ALTER TABLE generated_queries ADD COLUMN metric_use jsonb;   -- NULL: not attributed
-- {"version": 12, "verdicts": [{"metric": "revenue", "entity": "public.orders", "verdict": "used"}]}
```

---

## 4. The phases

### 4.1 Phase 0: One reader · **S** · moves `PROMPT_VERSION`

Nothing new appears for users. This phase fixes every defect in §1.2 except the
concurrency one, which needs a revision column. It comes first because each later
phase adds a reader, and all of them should start from the same one.

**P0.1: `bind_layer`, used by every loader.**

- Add `app/semantic/bind.py` with `bind_layer(raw, *, tables, relationships,
  dialect) -> SemanticDocument`. It replaces the three copies of the same steps
  that `save` (`:153–156`), `_persist_generated` (`:396–398`) and
  `eval.runner.load_semantic` (`:495–500`) each write out today.
- Change the signature to `load_document(db, connection, *, snapshot)` and bind
  against the given snapshot. `run_service` loads the snapshot two lines earlier
  (`:511`), `sql_draft_service` at `:245`, and `report_service` at `:543`.
  `report_service._time_conventions` (`:799`) loads `latest_snapshot` first, the
  same way its sibling does.
- `save`, `_persist_generated` and `load_semantic` all call `bind_layer`.
  `load_semantic` keeps its abort-on-broken rule, which is an eval policy rather
  than a difference in binding.
- **Cost:** one `sqlglot.parse_one` per metric expression and per filter, per
  run. Measure it on `sales` before and after. Only if it exceeds about 20 ms,
  memoise in-process on `(layer revision, snapshot version)`. Do not add the
  cache speculatively.

**P0.2: The benchmark reads the layer.**

- `workers/benchmark.py` `_run_question` passes `semantic=` from
  `load_document(db, connection, snapshot=snapshot)`. Correct its docstring.
- The `PROMPT_VERSION` bump (D11) is what makes runs before and after this
  change distinguishable on `benchmark_runs`.

**P0.3: The backlog reads the typed model.**

- Add `app/semantic/terms.py` with `vocabulary_terms(doc) -> frozenset[str]`,
  collecting entity, column and metric names, labels and synonyms, plus glossary
  terms and `maps_to`. Because it is typed, renaming a field breaks a test
  instead of silently breaking a feature. Excluded entities contribute nothing,
  just as they render nothing.
- `build_vocabulary(tables, terms)` takes words instead of a document, so
  `app.knowledge` still never imports `app.semantic`. `knowledge_service._vocabulary`
  binds the layer and passes in `vocabulary_terms(...)`.

**P0.4: *Grounded* respects the switch and the binder.** `_all_described` loads
through `load_document`, so the switch and binding apply, instead of reading the
row as a dict. This is an interim fix; Phase 1 replaces "the layer as it is now"
with "the version the run was written against".

**P0.5: Toggling the switch is audited.** When the connection `PATCH`
([connections.py:251](../../backend/app/api/v1/connections.py#L251)) changes
`semantic_layer_enabled`, it writes `semantic.switch.changed` with
`{from, to}`. The switch changes what the model reads on every question, and
`disclosure.changed` is the precedent. *Left as it is, on purpose:* the switch is
set under `connection modify` rather than `semantic_layer modify`. It is part of
the connection's policy set in the UI, and moving it is a separate
access-control question.

**Tests.**

- `test_semantic_bind.py`: the §1.2.1 probe becomes a test; a metric over a
  dropped column is absent from a run-path render. A second test pins that **a
  document bound at save renders byte-identically after `bind_layer` when the
  snapshot has not moved**, which proves the only prompt change is on drifted
  layers.
- `test_benchmark_semantic.py`: a fake gateway captures the generate prompt of a
  benchmark question on a connection with a layer. The layer's header is in the
  prompt when the switch is on, and absent when it is off.
- Extend the backlog tests: a word that appears only as a metric `label`, a
  column synonym or a glossary `maps_to` is not reported as unknown.
- Tier tests: with the layer switched off, the answer is not Grounded; with an
  entity invalid against the current snapshot, it is not Grounded.
- `test_prompt_version.py`: v10.

**Gate:** `make test` and `make lint` pass. Relabel the rows in
[eval.md §6](../reference/eval.md) so the unmade baselines say v10.

### 4.2 Phase 1: Versions · **M**

This is the first half of the blob problem. Every document that reached the
model is kept, attributed, diffable and restorable, and nothing overwrites
anything silently.

**The differ: `app/semantic/diff.py`.** A pure function,
`diff_documents(before, after) -> list[Change]`. Entry keys: an entity is its
lower-cased `table`; a column is `table` plus `name`; a metric is its `name`
(unique per document, enforced by `_refuse_ambiguous_metrics`); a glossary term is
its lower-cased `term`. The change kinds are fixed before any code is written:

```
document   context_changed · exclusions_changed★ · time_changed
entity     entity_added · entity_removed · entity_excluded · entity_included
           entity_described        (label, description, grain, role, default_time_column, synonyms)
column     column_added · column_removed · column_described · value_meanings_changed
metric     metric_added · metric_removed · metric_expression_changed★ · metric_filters_changed★
           metric_joins_changed★ · metric_described (label, description, unit, format, synonyms, additive)
glossary   glossary_added · glossary_removed · glossary_changed
review     reviewed_changed
                                                                   ★ affects_sql
```

The rules:

- Validator-owned fields (`valid`, `issue`) and derived `joins` never produce a
  change, because neither is something a person did.
- Reordering entities produces no change.
- A renamed metric appears as `metric_removed` plus `metric_added`. There is no
  identity to follow a rename by, and guessing one would be worse.
- The ★ kinds change numbers rather than wording. `metric_added` and
  `metric_removed` are also `affects_sql`. The history list sorts by this flag,
  and the Phase 2 publish dialog requires a note when it is set.

**The service.**

- `save(connection, doc, *, base_revision, note, ctx)` locks the head row
  (`SELECT … FOR UPDATE`) and refuses with 409 `E_SEMANTIC_CONFLICT` when
  `base_revision != revision`. Otherwise it binds, diffs against the published
  version, and **refuses an empty diff** with *"Nothing changed."* rather than
  writing an identical version. Then, in one transaction, it inserts the version
  and its change rows, copies the document to `document`, and increments
  `revision` and `published_version`.
- `execute_job` → `_persist_generated` locks the row, reads the **current**
  document again, runs `merge_documents(current, generated)`, and writes a
  version with `origin.generated_job_ids`. A save made during the generation is
  now part of `current`, so the merge keeps it. This fixes the first direction of
  §1.2.5; `base_revision` fixes the second.
- `restore(n, base_revision)` binds version *n* against the current snapshot and
  saves it as a new version with `origin.restored_from = n`. Flag, don't drop,
  applies: an entry that no longer resolves comes back red, not missing (research
  §8.8).
- `delete` saves an empty document with `origin.deleted` (D10). The route keeps
  `Privilege.DELETE`.
- `history(entity_key, item_key)` returns that entry's change rows joined to their
  versions, newest first.
- `load_document` returns `(document | None, version)`. `run_service` records
  `runs.semantic_layer_version` (0 when no layer reached the prompt) and adds it
  to the `ASK_RECORDED` detail. The benchmark worker records it on
  `benchmark_runs` the same way.

***Grounded*, properly.** `_knowledge` computes GROUNDED against the document of
`runs.semantic_layer_version`:

- 0 → never Grounded;
- *n* → Grounded when every table touched is a valid, non-excluded entity of
  version *n*, as bound when that version was published;
- NULL (a run from before migration `0030`) → Phase 0's rule applies.

It is still computed on read, so the docstring's principle holds. Only the input
changes.

**API.** `PUT /semantic` gains a required `base_revision` and an optional `note`.
New routes: `versions`, `versions/{n}`, `versions/{n}/changes`,
`versions/{n}/restore`, `history`, and `diff`. §6 has the full surface. A `PUT`
without `base_revision` is refused with 422, not treated as "overwrite anyway":
a client that omits it has not been updated, and the lost update is exactly what
the column exists to stop.

**Audit.** New actions, each with ids and counts only (audit rule 3):

| Action | Detail |
|---|---|
| `semantic.saved` | `{version, entities, metrics, issues, changes, affects_sql}` |
| `semantic.restored` | `{version, restored_from}` |
| `semantic.deleted` | `{version}` |
| `semantic.conflict` (outcome `FAILED`) | `{base_revision, revision}` |
| `semantic.generation.queued` | `{job_id, mode, tables}` |
| `semantic.generation.saved` | `{job_id, version}` |

No expressions go into `detail`, and neither do entry names; those live in
`semantic_layer_changes`, behind the layer's own `select`. Save is **not**
disabled while a generation runs: the lock makes a concurrent save safe, and
blocking a person's save for four minutes would be worse than the problem it
avoids.

**UI.** §5 covers: History sub-routes with a version list, a change list and
Restore; a per-entity history affordance; the conflict note; and a note popover
on Save for `affects_sql` changes.

**Tests.**

- `test_semantic_diff.py`: every change kind; validator fields, joins and
  reordering produce no change; the `affects_sql` set.
- `test_semantic_versions.py` (integration): a save produces v2 with its changes;
  an empty save is refused; restoring a version whose table was dropped brings it
  back flagged; delete keeps history; the backfill yields a v1 with a NULL author.
- `test_semantic_concurrency.py`: a stale `base_revision` returns 409 and writes
  nothing. **A save made during a generation survives the job's commit**, tested
  with a fake gateway that blocks on an event. A conflict writes its audit row.
- After save, generation, restore and delete, `document_sha256` equals the
  published version's hash (D3).
- Every new route resolves to exactly one `(semantic_layer, privilege)` cell;
  `make authz-check` passes.

**Gate:** after editing one metric's filter on the `sales` layer, the History
tab shows a readable change list; `make test` passes.

### 4.3 Phase 2: Draft and publish · **M**

This is the second half of the blob problem: editing stops reaching the model
instantly.

**Behaviour.**

- The editor writes to the draft: `PUT …/semantic/draft {document,
  base_revision}` sets `draft_document` and increments `revision`. A NULL draft
  means "the draft is the published document".
- **Generation, restore and import all write to the draft**, never to the
  published document. A generated layer is a model's guess about what a schema
  means, and today it reaches every answer the moment the job ends, unreviewed.
- `POST …/semantic/publish {base_revision, note}` diffs the draft against the
  published version, refuses an empty diff, writes the version through Phase 1's
  path, and clears the draft.
- `DELETE …/semantic/draft?base_revision=` discards the draft.
- Phase 1's `PUT /semantic` stays, meaning "save to the draft and publish in one
  step", for API clients and scripts. The SPA stops calling it.
- **`load_document` does not change.** It reads `document`, which is the
  published copy (D3). "A draft never reaches a run" holds entirely because no
  loader reads `draft_document`. One test asserts that on the loader code; another
  runs a pipeline against a connection whose draft and published documents
  differ.

**The note.** It is optional, except when the change list contains an
`affects_sql` change. Those changes alter a number on somebody's dashboard
without altering the text of its SQL, and *why* is the one thing a later reader
of the history cannot reconstruct.

**Scoring the draft.** When the connection has a benchmark set, the publish
dialog offers *Score this draft*. That queues a benchmark run with
`semantic_source = DRAFT` and a pinned `semantic_revision`, and for that run
only, the worker reads `draft_document` through `bind_layer`. The dialog then
shows held-out accuracy for the draft beside the latest run on the published
version.

- **It is advisory, not a gate.** A benchmark set is only as representative as
  the curator made it, and a hard gate would block publishing a fix for a metric
  that is wrong today.
- The cost is stated before it is spent (N questions × the chosen model),
  because it is a real provider bill.
- A draft run and a published run are comparable only when `prompt_version`,
  model and set all match. The dialog shows a delta only then, and otherwise
  explains why there is none.
- Queuing a draft run needs `modify` on the layer as well as the benchmark
  endpoint's own privilege, because it reads unpublished content.

**Merging a generation into a draft.** The merge is
`merge_documents(current_draft_or_published, generated)`. It closes one gap along
the way. `business_context` and `default_exclusions` have no provenance of their
own, so `merge_documents` keeps them only if *something else* in the document was
edited (`_edited`, [validate.py:394](../../backend/app/semantic/validate.py#L394)).
Give each of the two fields its own `Provenance`, set by the editor, and keep
each one on its own flag. Without that, a curator who wrote only the exclusion
rule loses it to the first generation, the very case the comment at `:370` calls
the sharpest.

**Audit.** New actions `semantic.draft.saved` `{revision, changes}`,
`semantic.draft.discarded`, and `semantic.published`
`{version, changes, affects_sql, scored_run_id}`. `semantic.saved` stays for the
one-step `PUT`.

**Access.** Update `PRIVILEGE_MEANINGS[SEMANTIC_LAYER]`:

- `SELECT`: *"Read the published layer, its draft, and its history."*
- `MODIFY`: *"Edit the draft, publish it, restore a version, import, check an
  expression, queue a generation."*

Update the rulebook's matrix wherever it is quoted.

**Tests.**

- A chat run, a SQL draft, a report outline and a PUBLISHED benchmark all render
  the published document while a different draft exists.
- A generation lands in the draft; the published document is unchanged;
  `revision` moved.
- Publishing with a stale revision returns 409; an `affects_sql` change without a
  note returns 422; an empty publish is refused.
- A DRAFT benchmark run renders the draft and records `semantic_source` and
  `semantic_revision`.
- `merge_documents` keeps an edited `default_exclusions` through a generation
  over an otherwise untouched document.

**Gate:** a person can edit, leave, come back and publish with a note. A chat
question asked between the edit and the publish is answered with the old
definition, and its run row names the version.

### 4.4 Phase 3: Metric attribution · **M**

This addresses the advisory problem by observing it, not by closing it.

**Where it runs.** In `run_service`, after the pipeline returns and before the
run is finalised. It reads the last guarded statement
(`generated_queries.rewritten_sql`, or `raw_sql` when there is none) and the
document of `runs.semantic_layer_version`. It is not a graph node: it needs
nothing from the graph that the run row lacks, and a node would change the event
sequence `test_pipeline_events.py` pins. On failure it logs and stores `NULL`
(fail open). The benchmark worker calls the same function, so each benchmark run
reports a metric-use rate.

**The function: `app/semantic/attribute.py`.** A pure function,
`attribute(sql, dialect, doc, tables_touched) -> list[Verdict]`. It parses the
statement; it never executes it and never rewrites it. The metrics in scope are
the valid metrics on non-excluded entities whose table the statement touched.

1. **Normalise** both sides with SQLGlot: qualify columns against the touched
   tables, lower-case identifiers, strip aliases, and sort conjuncts.
2. **`used`**: an aggregate with the metric's function over the metric's
   normalised columns, where every filter in the definition is a conjunct of the
   `WHERE` or `HAVING` that scopes *that* aggregate, including a CTE or subquery
   feeding it.
3. **`ignored`**: the same function over a column the metric's expression names,
   with **at least one** filter from the definition demonstrably absent from
   every scope feeding the aggregate. This is the only verdict that accuses an
   answer of something, so it has the narrowest definition and the most tests.
4. **`unknown`**: everything else, including window functions, a `DISTINCT`
   mismatch, set operations, and filters written in a form normalisation does
   not equate (`status != 'X'` and `NOT status = 'X'` normalise to the same
   thing; `status IN (…)` and `<>` do not).

A filterless metric such as `SUM(amount)` can be matched by coincidence. That is
not handled in the matcher. It is handled by the control arm in the measurement
below, which is the only place a coincidence can actually be told apart.

**Storage and display.**

- `generated_queries.metric_use` (migration `0032`).
- `RunKnowledge` gains `metrics_used: [{metric, label, expression, filters}]`,
  containing only `used` verdicts. The chat answer shows *Matches the `revenue`
  definition* beside the tier chip; hovering shows the expression, its filters
  and the layer version. There is no fourth tier: the three tiers (A2) are a
  built and argued design, and this is evidence on an answer, not a new rank.
- **`ignored` ships muted.** It is stored and counted, and it appears only as a
  plain line in the SQL panel: *"The `revenue` definition also excludes
  `status = 'CANCELLED'`; this query does not."* Even that line appears only
  after the precision gate below.
- Each connection gets a *Metrics in use* table in the layer's Metrics panel:
  metric, questions that touched its table, `used`, `ignored`, over the last
  30 days, sorted by the gap. That is the curation signal, and Phase 5's
  needs-attention filter reads it.

**Measurement: the gates.**

- **A labelled corpus** in `tests/unit/test_semantic_attribute.py`: hand-written
  statements against the `sales_semantic.json` metrics, plus an **adversarial
  set** built to tempt a false `used`. Examples: the right function with the
  wrong filter; the filter in an outer query that does not scope the aggregate;
  a filter on a joined table that has a column of the same name.
- **Zero false `used` on the adversarial set** before the chip ships.
- **`ignored` appears in the SQL panel only once its precision is at least
  0.95**, measured on the corpus plus a hand-labelled sample of real runs from
  the eval's layer-on arm. Until then it is counted, not shown.
- **The control that makes the rate mean something:** also run `attribute` over
  the eval's layer-**off** results. A `used` verdict in the off arm is
  coincidence, so the layer's effect on definition use is the on-arm rate minus
  the off-arm rate. The research's open question 6 (*if use is already
  near-total, attribution matters less*) is answered by exactly this pair, and it
  needs the two unmade Phase 0 baselines.

**Tests.** The corpus. Fail open: a statement SQLGlot cannot parse stores `NULL`
and the run still succeeds. `metrics_used` is absent for version 0. A VERIFIED
(short-circuited) answer is attributed too.

### 4.5 Phase 4: The portable document · **S**

**Export.** `GET …/semantic/export?version=n&value_meanings=false` returns JSON,
not a file download, because the SPA sends a bearer token (the dashboards
precedent).

```json
{
  "format": "datamind.semantic_layer",
  "format_version": 1,
  "exported_at": "2026-10-01T09:00:00Z",
  "source": { "connection": "sales", "engine": "postgresql", "version": 12 },
  "value_meanings_included": false,
  "document": { "business_context": "…", "time": { }, "entities": [ ], "glossary": [ ] }
}
```

- No ids, hosts or credentials: `dashboard_transfer`'s three rules.
- `value_meanings` are stripped unless the person ticks *Include value meanings
  (N columns)*, and the dialog says those are drawn from the data (D9).
- `joins`, `valid` and `issue` are stripped, because they are derived and an
  importer must re-derive them against its own snapshot.
- Requires `select`, because it is the same bytes `GET /semantic` already returns
  to that person. Audited as `semantic.exported` `{version,
  value_meanings_included}`.

**Import.** `POST …/semantic/import {file, base_revision}` writes into the draft
(D9) through `bind_layer`, and returns a report: entities resolved and unresolved,
metrics valid and invalid, and whether the file carried value meanings. Requires
`modify`. Limits against a hostile file: at most 2,000 entities, and this phase
adds `max_length` to the model's text fields. That limit also applies to the
editor, since prompt text should not be unbounded through either route. Audited
as `semantic.imported` `{revision, entities, unresolved, invalid_metrics}`.

**Why import is not a guard entry point.** Nothing executes a metric expression.
It is prompt text, and the SQL the model writes after reading it is guarded like
any other SQL. Import therefore adds no entry point to invariant #1. Replay the
hostile corpus through `check_expression` anyway, asserting each hostile input is
either rejected or parsed as an inert expression. It costs nothing, and it is the
evidence expansion (§11) would have to start from.

**Tests.** An export → import round trip on `sales` yields an empty change list,
apart from value meanings when they are omitted. Stripped fields; the limits; the
hostile replay; importing a file whose tables do not exist lands them flagged,
not dropped.

### 4.6 Phase 5: Upkeep · **M**

**Regenerating chosen tables.** The API has accepted `only_tables` since
migration `0003`, but the UI only ever sends undescribed tables (`undescribed`,
[semantic.tsx:320](../../frontend/src/components/semantic.tsx#L320)).
`GenerateModal` gains a table picker, with two modes for tables that already have
an entity:

- **Fill gaps** (the default) adds `merge_documents(..., fill_gaps=True)`. For an
  entity a person edited, it takes a generated value only where the field is
  empty, and only adds columns and metrics that do not exist yet. It never
  overwrites a field a person wrote. This handles the case drift detection
  surfaces but cannot act on: a table that has grown six columns.
- **Rewrite** replaces the existing entity with the generated one.

Both modes write to the draft (Phase 2), so a rewrite is a diff someone reads
before it answers anything. No generator prompt changes, and
`SEMANTIC_PROMPT_VERSION` stays `s4`.

**Needs attention.** A filter in the editor, beside the existing *issues* and
*review* filters, built from what is already known and needing no new job:

- entries invalid against the current snapshot (from the binder);
- tables with no entity;
- entities whose table's columns changed since the version that last changed the
  entity (comparing the snapshot at that version's `schema_version` with the
  newest one; both are kept);
- metrics where `ignored` outnumbers `used` over 30 days (Phase 3);
- entities with `provenance.source = llm` and `reviewed = false` that a Grounded
  answer relied on;
- a draft older than 7 days.

Each row names its reason and opens the entity.

**Tests.** `fill_gaps` never overwrites a non-empty field a person wrote;
column-shape drift is detected across two snapshots; the filter's counts match a
fixture.

---

## 5. UI and UX

The editor is `frontend/src/components/semantic.tsx`, at 3,250 lines. New surfaces
go into their own files (`semantic-history.tsx`, `semantic-publish.tsx`) rather
than growing it.

### 5.1 Where things live

- `/sources/:id/semantic`: the editor, as today.
- `/sources/:id/semantic/history`: the version list.
- `/sources/:id/semantic/history/:version`: one version's changes.

These are sub-routes read with `useMatch`, not nested `<Routes>`, so the tab stays
mounted. This follows the "every screen has a URL" rule.

### 5.2 The status line, in the page header

- **Phase 1:** `v12 · saved by Sara Karimi · 2 days ago · History`
- **Phase 2:** `Published v12 · by Sara Karimi · 2 days ago`, followed by either
  `No unpublished changes` or an amber `◐ 3 unpublished changes` chip that opens
  the publish dialog. Every state has a glyph and a word, never colour alone.

### 5.3 The floating bar

- **Phase 1:** `Unsaved changes  [Discard] [Save]`. Save opens a one-field note
  popover only when the pending changes include an `affects_sql` change;
  otherwise it saves immediately.
- **Phase 2:** two states.
  - Local edits not saved yet: `Unsaved edits  [Discard] [Save draft]`
  - A saved draft that differs from published: `3 unpublished changes  [Discard draft] [Review and publish]`

### 5.4 The publish dialog

- **The change list**, grouped by entity. `affects_sql` changes come first,
  marked *changes numbers*. Each line is a sentence from `semantic-changes.ts`,
  for example *"`revenue` now also excludes `status = 'REFUNDED'`"*, or
  *"`orders` grain changed from 'one row per order' to 'one row per order
  line'"*.
- **The note**, required when anything is marked.
- **The score section**, when a benchmark set exists: *Score this draft: 40
  questions on DeepSeek V4 Flash*. After a run it shows *Held-out 62% (draft) ·
  58% (v12)*, or the reason the two cannot be compared.
- `[Cancel] [Publish v13]`

### 5.5 History

- **The list** shows version, author, time, the note's first line, counts by
  change kind, a *changes numbers* marker, and `origin` as words (*generated*,
  *restored from v9*, *imported*, *deleted*, *recorded at migration*).
- **A version** shows its change list against its parent, or against a chosen
  version. Each entry links to its entity card. The action is `[Restore into
  draft]` (in Phase 1, *Restore as v14*).
- **Per entry:** a *History* icon on `EntityCard` and `MetricCard` opens the
  history route filtered to that entry.

### 5.6 The conflict

An `ErrorNote` beside the button that failed, not a toast (frontend convention):

> *Sara Karimi published v13 while you were editing. Your edits are still in
> this tab. [See what changed] [Reload]*

*See what changed* shows v12 → v13. *Reload* adopts v13 and lists the local edits
under *Your unsaved edits*. That list is computed by the server
(`POST …/semantic/diff {before: baseline, after: local}`) so the person can make
the edits again by hand. There is no automatic re-apply (D6).

### 5.7 On a chat answer (Phase 3)

Beside the existing tier chip: `✓ Matches the revenue definition`. Hovering shows
the expression, its filters, and *layer v12*. Nothing is shown for `unknown`.
`ignored` appears only as a line in the SQL panel, and only after its gate.

### 5.8 Conventions this touches

- `components/semantic-changes.ts` plus `.test.ts` (`npm run test:changes`)
  groups, orders and words a typed change list. It never compares documents. It
  is the fifteenth DOM-free module and the sixteenth `npm test` suite; update the
  list in CLAUDE.md.
- Notes and labels a person wrote get `dir={dirOf(value)}`. Expressions are always
  `dir="ltr"`.
- Below 700px the History list becomes an off-canvas drawer (`useListDrawer`),
  like every other second column.

---

## 6. API surface

All paths are under `/connections/{connection_id}`. Every privilege is asked on
`semantic_layer`, with the connection id as the resource id, as today's routes do.

| Method and path | Phase | Privilege | Notes |
|---|:--:|---|---|
| `GET /semantic` | 1, 2 | select | Adds `revision` and `published_version`. From Phase 2, `document` is the draft when one exists, plus `has_draft`, `draft_updated_by`, `draft_updated_at` and `unpublished_changes` |
| `PUT /semantic` | 1 | modify | Adds a required `base_revision` and an optional `note`. From Phase 2 it saves the draft and publishes in one step |
| `POST /semantic/diff` | 1 | select | `{before, after}` → changes. Saves nothing, like `/check` |
| `GET /semantic/versions` | 1 | select | Paged, newest first |
| `GET /semantic/versions/{n}` | 1 | select | The document |
| `GET /semantic/versions/{n}/changes` | 1 | select | `?against=m` |
| `POST /semantic/versions/{n}/restore` | 1 | modify | Phase 1: a new version. Phase 2: into the draft |
| `GET /semantic/history` | 1 | select | `?entity=&item=` |
| `DELETE /semantic` | 1 | delete | A tombstone version (D10) |
| `PUT /semantic/draft` | 2 | modify | `{document, base_revision}` |
| `DELETE /semantic/draft` | 2 | modify | `?base_revision=` |
| `POST /semantic/publish` | 2 | modify | `{base_revision, note}` |
| `POST /knowledge/benchmarks/{set_id}/run` | 2 | existing, plus `modify` on the layer when `semantic_source = DRAFT` | Adds `semantic_source` |
| `GET /semantic/export` | 4 | select | `?version=&value_meanings=` |
| `POST /semantic/import` | 4 | modify | Into the draft |

The new literal paths (`/diff`, `/versions`, `/history`, `/draft`, `/publish`,
`/export`, `/import`) sit under `/semantic` beside `/check` and `/generate`, and
none collides with a parameterised route.

**Errors:**

- `E_SEMANTIC_CONFLICT` (409) carries `revision`, `published_version`,
  `updated_by` and `updated_at`.
- `E_SEMANTIC_NO_CHANGES` (422).
- `E_SEMANTIC_NOTE_REQUIRED` (422).
- `E_SEMANTIC_BASE_REVISION_REQUIRED` (422).

---

## 7. Security, disclosure and the guard

### 7.1 The guard

Nothing in this plan executes SQL from the layer, so **no phase adds a guard
entry point**, and invariant #1's five entry points stay five.

- **Attribution** parses the statement the guard already validated and stores a
  verdict. It cannot change what runs.
- **Import** is equivalent to typing into the editor: same privilege, same binder,
  and it lands in a draft nothing reads.
- **Expansion** (§11) would be a guard question, if it is ever built, and it
  would start from Phase 4's hostile replay.

### 7.2 Disclosure

- **Versions and drafts are the same content class as the document.**
  `value_meanings` are data values, and they render only when
  `HintBudget.value_lists` allows, at render time, whichever version is rendered.
  Nothing here changes a render-time gate.
- **History exposes old versions to anyone with `select` on the layer**,
  including `value_meanings` that have since been removed from the live document.
  That is the same person who could read them while they were live. Revoking a
  grant revokes history with it, because every history route asks for the same
  `select`.
- **An export strips value meanings unless asked** (D9), and the audit row
  records which choice was made.
- **Attribution output** (`metric_use`) holds metric names and verdicts: schema
  vocabulary, no values.

### 7.3 Audit

Rule 3 holds throughout: ids, versions, counts and kinds only. The names of the
entries that changed live in `semantic_layer_changes`, under the layer's own
authorization. The index at the top of `services/audit.py` gains a
`SEMANTIC_LAYER = "semantic_layer"` resource constant and a line naming
`services/semantic_service.py` and its actions.

### 7.4 Access

Every route resolves to exactly one cell of `PRIVILEGE_MEANINGS[SEMANTIC_LAYER]`,
asked through `services.policy.require`. No handler checks `is_admin` or compares
owner ids. `make authz-check` and `test_authz_conformance.py` cover the new routes.

---

## 8. Measurement: what each phase must produce

| Phase | Must produce | Needs a provider key |
|:--:|---|:--:|
| 0 | The drift test; byte-identical output for layers that have not drifted; a benchmark prompt that contains the layer; the binder's cost measured on `sales` | no |
| 1 | Nothing about accuracy. This phase cannot change an answer, and a test asserts render bytes are unchanged by versioning | no |
| 2 | A test proving a draft never reaches a run; the first *Score this draft* delta on a real connection | the delta: yes |
| 3 | Zero false `used` on the adversarial corpus; `ignored` precision of at least 0.95 before it is shown; **the on-arm minus off-arm definition-use rate** on `sales_v1` | the rate: yes |
| 4 | An export → import round trip with an empty diff | no |
| 5 | `fill_gaps` never overwrites a field a person wrote | no |

The layer's own A/B (accuracy with `--semantic on` against `off`, at v10) is
still the number mvp2 §1.3 says has never been taken. This plan does not take it,
because this environment has no provider key. What it does is make "the layer" mean
the same thing in the eval, the benchmark and the product for the first time, which
was not true before Phase 0.

---

## 9. File-by-file change map

### Backend

| File | Phase | Change |
|---|:--:|---|
| `app/semantic/bind.py` | 0 | **new**: `bind_layer` |
| `app/semantic/terms.py` | 0 | **new**: `vocabulary_terms` |
| `app/semantic/diff.py` | 1 | **new**: `Change`, `diff_documents`, entry keys, `affects_sql` |
| `app/semantic/attribute.py` | 3 | **new**: `attribute`, `Verdict` |
| `app/semantic/models.py` | 2, 4 | `Provenance` for `business_context` and `default_exclusions` (2); `max_length` on text fields (4) |
| `app/semantic/validate.py` | 2, 5 | `merge_documents` respects the two new provenances (2); `fill_gaps` (5) |
| `app/semantic/__init__.py` | 0–5 | exports |
| `app/services/semantic_service.py` | 0–5 | `load_document(…, snapshot)` then `(doc, version)`; save with lock, revision and version; generation persisted under lock; restore, delete, history; draft and publish; export and import |
| `app/services/semantic_transfer.py` | 4 | **new**: the portable format, beside `dashboard_transfer.py` |
| `app/services/audit.py` | 1 | `SEMANTIC_LAYER` constant; index entry |
| `app/services/run_service.py` | 0, 1, 3 | pass the snapshot; record the version; `ASK_RECORDED` detail; attribution at finalisation |
| `app/services/sql_draft_service.py`, `app/services/report_service.py` | 0 | the loader's new signature |
| `app/services/knowledge_service.py` | 0 | `_vocabulary` passes typed terms |
| `app/services/benchmark_service.py` | 2 | queue a DRAFT run; the extra privilege check |
| `app/knowledge/backlog.py` | 0 | `build_vocabulary(tables, terms)`; no more dict keys |
| `app/workers/benchmark.py` | 0, 1, 2, 3 | pass `semantic`; record the version; read the draft for DRAFT runs; metric-use rate |
| `app/eval/runner.py` | 0 | `load_semantic` uses `bind_layer` |
| `app/pipeline/prompts/__init__.py` | 0 | `PROMPT_VERSION = "v10"` |
| `app/api/v1/semantic.py` | 1, 2, 4 | the routes in §6 |
| `app/api/v1/connections.py` | 0 | `semantic.switch.changed` in the `PATCH` |
| `app/api/v1/conversations.py` | 0, 1, 3 | *Grounded* through the loader, then against the recorded version; `metrics_used` |
| `app/api/v1/knowledge.py` | 2 | `semantic_source` on the benchmark run route |
| `app/api/schemas.py` | 1–4 | DTOs |
| `app/domain/value_objects/authz.py` | 2 | `PRIVILEGE_MEANINGS` wording |
| `app/infra/db/models.py` + migrations `0030`, `0031`, `0032` | 1, 2, 3 | §3 |

### Tests

New test files: `test_semantic_bind.py`, `test_benchmark_semantic.py` (0),
`test_semantic_diff.py`, `test_semantic_versions.py`,
`test_semantic_concurrency.py` (1), `test_semantic_draft.py` (2),
`test_semantic_attribute.py` (3), `test_semantic_transfer.py` (4). Existing tests
to extend: the backlog vocabulary tests, the run-knowledge tier tests,
`test_prompt_version.py`, `test_authz_conformance.py`, and `test_semantic_validate.py`
(merge provenance, `fill_gaps`).

### Frontend

| File | Phase | Change |
|---|:--:|---|
| `src/api/types.ts`, `src/api/client.ts` | 1–4 | `SemanticVersion`, `SemanticChange`, revision fields; the new calls |
| `src/components/semantic-changes.ts` + `.test.ts` | 1 | **new**, DOM-free |
| `src/components/semantic-history.tsx` | 1 | **new**: the version list, a version's changes, restore |
| `src/components/semantic-publish.tsx` | 2 | **new**: the publish dialog and score section |
| `src/components/semantic.tsx` | 1, 2, 4, 5 | status line; bar states; conflict note; per-entry history icons; export and import; the regeneration picker; needs-attention filter |
| `src/components/chat.tsx` | 3 | the *Matches the definition* chip |
| `package.json` | 1 | `test:changes` and the `test` chain |

### Docs, updated in the commit that lands each phase

| Document | Change |
|---|---|
| [reference/semantic-layer.md](../reference/semantic-layer.md) | binder, versions, drafts, attribution; replace the `merge.py` name; make *"flagged entries never reach the prompt"* true as written |
| [CLAUDE.md](../../CLAUDE.md) | the curated-documents row; the DOM-free module list; `PROMPT_VERSION` = v10 |
| [status.md](../status.md) | a §2 row per phase as it lands |
| [decisions.md](../decisions.md) §5 | D1–D11 as they land |
| [reference/security.md](../reference/security.md) | §7.1 and §7.2 of this plan |
| [reference/eval.md](../reference/eval.md) | v10; the benchmark reads the layer; the attribution control arm |
| [reference/knowledge-templates.md](../reference/knowledge-templates.md) §6 | a benchmark score is a score *with* the connection's layer |
| [reference/access-control.md](../reference/access-control.md) | `PRIVILEGE_MEANINGS` wording, where quoted |
| [mvp2.md](mvp2.md) §1.3 and [research/semantic-layer.md](../research/semantic-layer.md) | a banner pointing here, and naming §1.3's corrections |
| [docs/README.md](../README.md) | an index row for this plan |

---

## 10. Risks

| Risk | Phase | What we do about it |
|---|:--:|---|
| Draft/publish feels like friction to a connection with a single owner | 2 | Publishing is one click from the bar; a note is required only for number-changing edits; no approval step |
| *"I generated a layer and nothing changed"* | 2 | The job's notification says *Written to your draft: review and publish*, and opens the dialog |
| A false *Matches the definition* chip | 3 | `unknown` is the default; zero false `used` on an adversarial corpus; hovering shows the definition so a reader can check it |
| 409s annoy two people editing together | 1 | `semantic.conflict` audit rows count them, and §11's trigger turns that count into entry-level merge |
| `document` and the published version drift apart | 1 | One writer, one transaction, and a hash test after every write path |
| Binding on every run adds latency | 0 | Measure on `sales`; memoise on `(revision, snapshot version)` only if it matters |
| The version table grows | 1 | Measure after Phase 1; §11 trigger |
| The v10 bump is misread as a change to prompt wording | 0 | eval.md states exactly what moved: binding on load, and the benchmark reading the layer |
| Old runs (no recorded version) use a different tier rule | 1 | Documented. NULL keeps Phase 0's rule, which is correct except that it reads the current layer |

---

## 11. Deliberately not building, with triggers

| Not building | Why not now | Trigger to reopen |
|---|---|---|
| **Metric expansion** (`metric('revenue')` compiled into the SQL) | It is the only option that changes what runs, and it would make the layer fail closed. It is also a prompt addition, in a repo that has measured two prompt additions lowering accuracy | Phase 3 shows `ignored` on more than about 20% of in-scope questions on a real connection, **and** an eval arm is available to gate it. Write the fail-open → fail-closed decision into CLAUDE.md before any code |
| **Two-person publish** | It needs a rule for who may approve: a privilege above `modify` that is not `manage`, which means changing the lattice | A customer asks for it, or `semantic.published` rows show a self-published, number-changing edit that later had to be restored |
| **Merging concurrent drafts entry by entry** | It needs a patch-apply over the differ, and the 409 is honest | `semantic.conflict` audit rows exceed about 5 a week on any installation |
| **Version retention and pruning** | Runs point at versions, and pruning breaks the *Grounded* tier and attribution on old answers | `semantic_layer_versions` exceeds about 1 GB, or a single connection exceeds about 1,000 versions |
| **Files or Git as the system of record** (research C2) | DataMind's curator owns a connection, not a repository; export (Phase 4) is the bridge | A customer wants CI over the layer. Build pull and push over the export format then, never a second store |
| **Normalising the document into tables** | D1 | Per-entry permissions become a requirement (a team may edit `orders` but not `payments`) |
| **Synonyms and the glossary in `retrieve`** | Retrieval owns this: mvp2 B2 and A5, and [research/retrieval-at-scale.md](../research/retrieval-at-scale.md), which found the matcher already over-matches | B2 starts. It consumes `vocabulary_terms` from P0.3 instead of re-reading the JSON |
| **Flagging tiles and report blocks written against an old definition** | Stored SQL does not change when a metric does, and finding the statements that used the old definition needs attribution over stored SQL | Phase 3 lands. Then run `attribute` over `dashboard_tiles.sql` and `report_blocks.sql` against the version each was drafted under |

---

## 12. Open questions

1. **Should the first generation on an empty layer publish itself?**
   *Recommendation:* no. A model's guess about a schema is exactly what Phase 2
   puts in front of a person. This is, however, the one place the new flow is
   strictly slower than today's.
2. **Is a note required for `affects_sql` changes, or only prompted?**
   *Recommendation:* required. The cost is one sentence; the benefit is the only
   record of *why* a number moved.
3. **Should publishing mark the touched entities as reviewed?**
   *Recommendation:* no. `reviewed` is per entry and deliberate; published is per
   document (research §8.3). Keep the two words apart.
4. **Does a DRAFT benchmark run belong in the score history strip?**
   *Recommendation:* no. Show it in the publish dialog only, so the strip stays
   the published product's number.

---

## 13. The acceptance test

> On `sales`, change the `revenue` metric's filter in a draft. Ask *"revenue last
> month"* and get the old definition, with the answer naming v12. Publish with a
> note. Ask again and see *Matches the revenue definition · v13*. Open History and
> find who changed it and why. Drop the column the metric uses with a re-sync, and
> confirm the next prompt no longer mentions it.

---

## 14. Progress ledger

Tick each box in the commit that lands the work.

### 14.1 Phase 0: One reader · **9 / 9**

- [x] `app/semantic/bind.py` `bind_layer`; `save`, `_persist_generated` and `eval.runner.load_semantic` use it (and the editor's `read`, so the joins it shows are derived too)
- [x] `load_document(db, connection, *, snapshot)` binds; `run_service`, `sql_draft_service` and `report_service` (both call sites) pass a snapshot
- [x] Binder cost measured on `sales`; the cache added only if it exceeds 20 ms — **median 6.5 ms on `sales` (21 entities, 14 metrics), 10.9 ms on `aurora` (34 metrics); no cache.** The *Grounded* tier memoises per request instead, since a transcript would otherwise bind once per turn
- [x] `workers/benchmark.py` passes `semantic=`; its docstring corrected
- [x] `app/semantic/terms.py` `vocabulary_terms`; `build_vocabulary` takes words; `_vocabulary` passes them. Flagged entries contribute nothing, as well as excluded ones: neither renders
- [x] `_all_described` loads through `load_document`
- [x] `semantic.switch.changed` audited in the connection `PATCH`
- [x] `PROMPT_VERSION` v10; `test_prompt_version.py`; the eval.md §6 rows relabelled
- [x] Tests: bind (drift, and byte-identity when undrifted), benchmark prompt, backlog vocabulary, tier

Found while measuring: binding also applies validator rules the stored flags
predate. The demo `aurora` layer stores `total_revenue` on both `orders` and
`order_items` with no issue, because it was saved before
`_refuse_ambiguous_metrics`; the run path rendered both definitions until this
phase, and now renders neither.

### 14.2 Phase 1: Versions · **13 / 13**

- [x] Migration **`0032`** (not `0030`, see the status note): `revision`, `published_version`, `semantic_layer_versions`, `semantic_layer_changes`, version columns on `runs` and `benchmark_runs`; v1 backfilled with a NULL author. Rehearsed on a Postgres 16 clone of the demo database (one layer → v1, revision 1, hash equal to the service's), including downgrade and re-upgrade
- [x] `app/semantic/diff.py`: the §4.2 vocabulary, entry keys, `affects_sql`. **One refinement:** a metric is keyed by its table *and* name, because a stored document can hold one name on two tables (both flagged by `_refuse_ambiguous_metrics`) and a keyed comparison needs a key unique in every stored document. Adding or removing an entity also adds or removes its columns and metrics, so each entry's history starts where it did
- [x] `save`: lock, `base_revision`, empty-diff refusal, and version + changes + copy + revision in one transaction (`_publish`, the one writer)
- [x] `_persist_generated`: lock, merge over the current document, version with `origin.generated_job_ids`. A generation that changes nothing writes no version and moves no revision
- [x] Restore (flagged, not dropped), delete as a tombstone, the history query. A tombstone stores `{}`, which every loader already reads as no layer
- [x] Runs and benchmark runs record their version; the `ASK_RECORDED` detail carries it; `RunRead` exposes it (`load_layer` returns the document and the version off the same row; `load_document` stays for readers that need no version)
- [x] *Grounded* computed against the recorded version; NULL keeps Phase 0's rule
- [x] Routes: `versions`, `versions/{n}`, `changes`, `restore`, `history`, `diff`; `PUT` requires `base_revision`. The 409 is *returned* from the route rather than raised, so the `semantic.conflict` audit row commits instead of rolling back with the request
- [x] Audit actions, and the index in `services/audit.py`
- [x] Frontend: types and client; History sub-routes; per-entry history; conflict note; note popover. History is a single column with a back link rather than a list beside a detail, so it needs no drawer below 700px (checked at 390px: no horizontal scroll)
- [x] `semantic-changes.ts` with its test, wired into `npm test`
- [x] Tests: diff, versions, concurrency (including save during generation), hash equality, authz conformance
- [x] Gate: a readable change list on `sales` after a filter edit — driven in the browser against a migrated clone: edit `revenue`'s filter, the note prompt lists the change, Save, and History → v3 reads *"`revenue` now also filters on `orders.status <> 'refunded'` — changes numbers"*. A conflict, reload with the displaced edits listed, per-entry history and a restore were driven the same way

### 14.3 Phase 2: Draft and publish · **0 / 11**

- [ ] Migration `0031`: draft columns; `semantic_source` and `semantic_revision` on `benchmark_runs`
- [ ] Service and routes: save draft, discard, publish
- [ ] Generation, restore and import write to the draft
- [ ] The one-step `PUT` means draft plus publish
- [ ] A note required on `affects_sql` changes
- [ ] `Provenance` on `business_context` and `default_exclusions`; the merge keeps each on its own flag
- [ ] *Score this draft*: the worker reads the draft for DRAFT runs; the dialog shows a comparable delta or says why not
- [ ] `PRIVILEGE_MEANINGS` wording
- [ ] Frontend: status line, two-state bar, publish dialog
- [ ] Tests: no loader reads the draft (four surfaces), generation → draft, publish refusals, DRAFT benchmark, exclusions survive a merge
- [ ] Gate: a question asked between edit and publish is answered with the old definition, and the run names its version

### 14.4 Phase 3: Metric attribution · **0 / 9**

- [ ] Migration `0032`: `generated_queries.metric_use`
- [ ] `app/semantic/attribute.py` with the four rules of §4.4
- [ ] Labelled and adversarial corpus; zero false `used`
- [ ] Attribution at run finalisation (fail open); the benchmark worker reports the rate
- [ ] `RunKnowledge.metrics_used` and the chip
- [ ] The *Metrics in use* table
- [ ] On-arm minus off-arm definition-use rate, **needs a provider key**
- [ ] `ignored` precision measured at 0.95 or above, then the SQL panel line, **needs real runs**
- [ ] Tests: fail open, version 0, VERIFIED answers attributed

### 14.5 Phase 4: The portable document · **0 / 6**

- [ ] Export route and format; derived fields stripped; value meanings opt-in; audit
- [ ] Import route into the draft; the bind report; the limits; audit
- [ ] `max_length` on the model's text fields
- [ ] Hostile corpus replayed through `check_expression`
- [ ] Frontend: export and import dialogs
- [ ] Tests: round trip, limits, a flagged import

### 14.6 Phase 5: Upkeep · **0 / 5**

- [ ] Table picker for described tables, with fill-gaps and rewrite
- [ ] `merge_documents(fill_gaps=True)`
- [ ] Column-shape drift between a version's snapshot and the newest
- [ ] The needs-attention filter with its six reasons
- [ ] Tests: `fill_gaps` never overwrites; drift detection; filter counts

### 14.7 Documentation · **1 / 10**

The other nine documents span phases, so each is ticked when the last phase it
describes lands. What Phases 0 and 1 changed is already in each of them:
reference/semantic-layer.md, CLAUDE.md, status.md, decisions.md §5,
reference/security.md §2.2, reference/eval.md §6 and
reference/knowledge-templates.md §6.

- [ ] reference/semantic-layer.md
- [ ] CLAUDE.md
- [ ] status.md
- [ ] decisions.md §5
- [ ] reference/security.md
- [ ] reference/eval.md
- [ ] reference/knowledge-templates.md §6
- [ ] reference/access-control.md
- [ ] mvp2.md §1.3 and research/semantic-layer.md banners
- [x] docs/README.md index row

### 14.8 Totals

| Phase | Done | Items |
|---|:--:|:--:|
| 0 · One reader | 9 | 9 |
| 1 · Versions | 13 | 13 |
| 2 · Draft and publish | 0 | 11 |
| 3 · Metric attribution | 0 | 9 |
| 4 · Portable document | 0 | 6 |
| 5 · Upkeep | 0 | 5 |
| Documentation | 1 | 10 |
| **Total** | **23** | **63** |
