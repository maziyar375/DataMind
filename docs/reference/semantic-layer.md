# The semantic layer

What the schema *means*, as one editable document per connection — and the
rules that keep it from lying to the generator.

Code: [`backend/app/semantic/`](../../backend/app/semantic/) — `generator.py`
(one model call per table), `render.py` (the tiered fit), `validate.py` (what
is refused, and `merge_documents`: what survives a regeneration), `bind.py`
(the one binder every reader goes through), `terms.py` (the words the layer
speaks, for the knowledge backlog), `diff.py` (the one differ), `attribute.py`
(which definitions an answer's SQL matched), `limits.py` (how long a text may
be), `attention.py` (what in a layer needs a person, and why). The portable file is `backend/app/services/semantic_transfer.py`. The editor is
[`frontend/src/components/semantic.tsx`](../../frontend/src/components/semantic.tsx).
The plan that turned the document into a versioned model is
[plans/semantic-layer-model.md](../plans/semantic-layer-model.md); all six of
its phases have landed.

Companion to [pipeline-chat.md](pipeline-chat.md) (the `retrieve` node that
renders it), [catalog-metadata.md](catalog-metadata.md) (DDL comments, which
this layer wins against per entity), [eval.md](eval.md) (which is how it was
argued for) and [llm-calls.md](llm-calls.md) §9 (the three generation prompts).

---


The schema snapshot says what *exists*. The semantic layer says what it
**means**: the business name and **grain** of each table ("one row per line
item"), the columns worth explaining, **metrics** bound to exact SQL including
the filters that belong to the definition rather than the question, **time
conventions** (fiscal year, week start, whether "last month" is calendar or
rolling), the rows that **should not count** unless asked for (soft deletes,
test accounts — free text, because the rule spans tables that do not share a
column), a glossary, and per-join **fan-out cautions**. One editable document
per connection, in `semantic_layers`.

It exists because the eval said so: FK-neighbour retrieval lifted recall 70→86%
with **flat** execution accuracy, and the residual DeepSeek failures were
interpretation, not retrieval — rolling-vs-calendar windows, long-vs-wide
shapes. That is the class this addresses.

**It is read in two directions, and the second is newer.** The block it renders
explains a table the retriever has already chosen; since 2026-09-22 (mvp2
**A5**) its vocabulary also *does* the choosing. `app.semantic.table_terms`
turns the document into `table → the phrases that name it` — entity labels and
synonyms, column labels and synonyms, metric names, and glossary terms resolved
through `maps_to` — and `retrieve` matches a question against it on the
`RANKED_MATCH` branch, so "churn" reaches `subscription_events` because someone
wrote that down once. Two properties are load-bearing there and are the same
ones binding already guaranteed: **an excluded or invalid entry contributes
nothing**, because a table chosen by a word the model was never shown is worse
than a table not chosen; and **a connection without a layer retrieves exactly as
it did before**, which is what let `PROMPT_VERSION` move to v11 without
invalidating a measurement. See [pipeline-chat.md](pipeline-chat.md) §4 step 4.

- **Generate** — `POST /connections/{id}/semantic/generate` with an
  `llm_config_id` queues a `semantic_jobs` row and returns **202**; the SPA
  polls it. `app/semantic/generator.py` runs **one model call per table**, four
  concurrently: a whole-schema call returns forty one-line descriptions and no
  metrics, per-table calls return grain and real expressions. **Joins are
  derived, never asked for** — cardinality is readable off the catalog.
- **Nothing unchecked is kept.** Generated names are resolved against the
  snapshot and metric expressions parsed with SQLGlot; an invalid *generated*
  metric is dropped (and counted in the job's stats), while an invalid
  *human-written* one is flagged and kept, because deleting a person's work to
  hide drift is worse than showing it. Flagged entries never reach the prompt.
- **Every reader binds the layer, every time** (`bind_layer`). Validity is
  derived at read time against the snapshot the reader already holds; the
  stored `valid`/`issue` flags stay in the JSON for the editor, and no consumer
  trusts them. `load_document(db, connection, snapshot=…)` is the loader for a
  chat run, a SQL draft, a report outline, a report's time conventions, the
  in-product benchmark, the knowledge backlog's vocabulary and the *Grounded*
  tier; the editor's `GET`, `save`, a generation and the eval's `load_semantic`
  call `bind_layer` directly. *Until 2026-09-15 the run path trusted the flags
  from the last save*, so a column a re-sync dropped still reached the model
  while the editor showed it red, and a metric name claimed twice before
  `_refuse_ambiguous_metrics` existed (the demo `aurora` layer's
  `total_revenue`) rendered both definitions. Binding costs about 6 ms on
  `sales` and 11 ms on `aurora`'s 34 metrics, so it is not cached.
  `PROMPT_VERSION` moved v9 → v10 for it; a layer that had not drifted renders
  the same bytes (`test_semantic_bind.py`).
- **Every save is a version** (migration `0032`, Phase 1 of the plan). A save,
  a generation, a restore and a delete each lock the head row and, in one
  transaction, write an immutable `semantic_layer_versions` row, its typed
  changes (`semantic_layer_changes` — keys only, in `app/semantic/diff.py`'s
  vocabulary), and a copy into `semantic_layers.document`, which keeps meaning
  *what the model reads*, so no reader changed. A test holds every write path to
  `sha256(document) == versions[published_version].document_sha256`.
  - **A write names the revision it read.** `PUT` requires `base_revision`
    (`E_SEMANTIC_BASE_REVISION_REQUIRED` without it) and a stale one is a 409
    `E_SEMANTIC_CONFLICT` naming who saved — returned rather than raised, so
    its `semantic.conflict` audit row commits. An empty change list is
    `E_SEMANTIC_NO_CHANGES`, not an identical version. Nothing is merged on the
    server; the editor lists the displaced edits so they can be made again.
  - **A generation merges into the layer as it is when it lands**, under the
    lock, not into the document it read minutes earlier — so a save made while
    it ran survives. Before this the job silently overwrote it.
  - **Restore** puts an old version back — into the draft since Phase 2 —
    bound to today's snapshot (an entry whose table is gone comes back flagged,
    not missing). **Delete** publishes an empty tombstone version and drops any
    draft; history is kept.
  - **Runs and benchmark runs record `semantic_layer_version`** (`0` for no
    layer, NULL before `0032`), and *Grounded* reads that version: an answer is
    no longer grounded after the fact by a table described next week.
  - History is `/sources/:id/semantic/history[/:version]`, and
    `?entity=&item=` filters it to one table or metric, reached from the card.
- **An edit lands in a draft; publishing makes the version** (migration `0033`,
  Phase 2). The editor's Save writes `semantic_layers.draft_document`
  (`PUT …/semantic/draft`), and so do a **generation** and a **restore**. **No
  loader reads the draft**: `load_layer` and `load_document` read `document`,
  the published copy, so a chat run, a SQL draft, a report and a published
  benchmark all answer with the published version while a different draft
  exists (`test_semantic_draft.py` checks the loaders' source, the modules that
  may name the column, and the behaviour). A NULL draft *is* the published
  document; a draft saved back to what is published leaves no draft.
  - `POST …/semantic/publish {base_revision, note}` binds the draft against the
    current snapshot, diffs it against the published document, and writes the
    version through the one writer. Refused when the list is empty
    (`E_SEMANTIC_NO_CHANGES`) and when a change **alters numbers** with a blank
    note (`E_SEMANTIC_NOTE_REQUIRED`, 422) — the SQL of a dashboard does not show
    that `revenue` now excludes refunds, and the note is the only record of
    why. The draft's `draft_origin` (`generated_job_ids`, `restored_from`)
    becomes the version's `origin`. `DELETE …/semantic/draft?base_revision=`
    discards the draft. Both need `modify`, the privilege editing already
    needs: there is no approval step (D7).
  - **A first generation does not publish itself.** A model's guess about a
    schema is exactly what a draft is for; the job's notice says *written to
    your draft* and links to the publish dialog. This is the one place the flow
    is slower than before.
  - The one-step `PUT /semantic` stays for API clients and scripts — it saves
    and publishes at once and replaces any draft, and its note stays optional
    as it was in Phase 1.
  - **Scoring a draft.** The publish dialog offers *Score this draft* when the
    connection has a benchmark set: a `benchmark_runs` row with
    `semantic_source = DRAFT`, pinned to the layer's `revision`. The worker reads
    the draft through `load_draft` — the one reader of a draft outside the
    editor — and fails the run if the draft moved after it was queued, rather
    than scoring a draft nobody asked about. It needs `(semantic_layer, modify)`
    on top of the route's own `(knowledge, modify)`, and it scores the draft
    whether or not the switch is on, because that is what was asked. Draft runs
    stay out of the score strip; the dialog shows a delta only when both runs
    used the same prompt version and model (`semantic-score.ts`), and otherwise
    says why there is none. `semantic.published` records the run that scored
    exactly the published revision, found on the server, not sent by the client.
  - **Regeneration keeps the two document texts on their own flags.**
    `business_context` and `default_exclusions` gained `context_provenance` and
    `exclusions_provenance`, set by the editor; before them a merge kept a
    hand-written exclusion rule only when *something else* had been edited, so
    a curator who wrote only that rule lost it to the first generation.
  - The editor shows `Published v12 · published by … · 2 days ago` and either
    `● No unpublished changes` or `◐ 3 unpublished changes`, which opens the
    publish dialog. Its floating bar has two states: `Unsaved edits [Discard]
    [Save draft]`, and `3 unpublished changes [Discard draft] [Review and
    publish]`. Audit: `semantic.draft.saved {revision, changes}`,
    `semantic.draft.discarded {revision}`, `semantic.published {version,
    changes, affects_sql, scored_run_id}`; `semantic.restored` and
    `semantic.generation.saved` now carry the draft's `revision`.
- **Whether an answer used a definition is observed after the run** (migration
  `0034`, Phase 3). `app/semantic/attribute.py` reads the last statement the
  guard accepted and gives each valid metric on a touched table one verdict:
  **`used`** — the metric's expression, after normalising both sides (columns
  qualified to `schema.table.column` through aliases, CTEs and derived tables;
  identifiers lower-cased; parentheses dropped; conditions simplified, so
  `!=`, `<>` and `NOT … =` agree), in a `SELECT` whose `WHERE`, `HAVING` or
  inner-join `ON` — its own or a feeding CTE's or derived table's — carries
  **every** filter of the definition; **`ignored`** — that expression with at
  least one filter *demonstrably* absent, meaning nothing feeding or enclosing
  the aggregate mentions the filter's column and all of it could be read;
  **`unknown`** — the default, including window functions, `FILTER`, set
  operations, a self-join of the metric's table, `IN (…)` where the definition
  says `<>`, and any subquery a filter could hide in. It observes and never
  enforces (D8): it runs in `run_service` at finalisation, not as a node, so
  the SSE sequence is untouched; it stores
  `generated_queries.metric_use = {version, verdicts}` on the attributed
  attempt, and `NULL` when no layer reached the prompt, nothing was accepted
  or the statement cannot be read — a run never fails for it. A Verified
  answer is attributed like any other.
  - **The chip.** `RunKnowledge.metrics_used` carries only `used` verdicts, with
    the expression and filters read from the version the run recorded; the chat
    answer shows `✓ Matches the revenue definition` beside its tier, and hovering
    or focusing it shows the definition and `semantic layer v12`. There is no
    fourth tier. **`ignored` is stored and counted and shown on no answer**: the
    SQL-panel line waits for its precision to be measured at 0.95 or above on
    real runs.
  - **Metrics in use** (`GET …/semantic/metric-use?days=30`, `select`) counts,
    per metric, the answers whose statement touched its table and how many used
    or left out its definition, most-left-out first — in the Metrics panel.
    Counts only: no question, answer, SQL or asker.
  - **The benchmark** stores each question's verdicts
    (`benchmark_results.metric_use`) and their counts on the run
    (`benchmark_runs.metric_use`); **the eval** measures a definition-use rate on
    both arms (`definition_use` on the scorecard), because a filterless metric
    is matched by coincidence and only the layer-off arm can say how often.
  - Measured so far: zero false `used` on a 20-statement adversarial set and a
    26-statement labelled corpus against `sales_semantic.json`
    (`test_semantic_attribute.py`). On the 17 statements the demo `aurora`
    connection's past chat runs had produced, 2 were `used` (both correct on
    reading), 131 metric-and-statement pairs were `unknown` — mostly metrics the
    statement never computed — and none `ignored`, so `ignored` precision is
    **not measured yet**. A matching aggregate over a join that fans rows out is
    still `used`: the chip speaks of the definition, and fan-out is the join
    cautions' subject.
- **A layer travels as a file** (Phase 4, no migration).
  `GET …/semantic/export?version=&value_meanings=false` (`select`) returns a
  **published version** — never the draft — as JSON with
  `format: "datamind.semantic_layer"`, `format_version: 1`, a `source` naming
  the connection and engine and nothing else about it, and the document with
  `joins`, `valid` and `issue` stripped, because those are readings of this
  snapshot. **`value_meanings` are stripped unless asked** — they are codes
  drawn from the data (D9) — and `semantic.exported {version,
  value_meanings_included}` records the choice. `POST …/semantic/import {file,
  base_revision}` (`modify`) checks the format, the version, at most 2,000
  tables and every text limit, then writes the file **into the draft** through
  the binder with origin `{"imported": true}`, and reports what resolved: a
  table this schema lacks comes in flagged, not dropped. Audited as
  `semantic.imported {revision, entities, unresolved, invalid_metrics}`. The
  editor offers both beside History; Import also sits beside *Generate with AI*
  on an empty layer. An export → import round trip is an empty change list,
  apart from value meanings when a plain export left them out.
  - **Import is not a guard entry point.** Nothing executes a metric
    expression. The hostile SQL corpus is replayed through `check_expression`
    anyway (`test_semantic_transfer.py`), and it found a real gap:
    `sqlglot.parse_one` reads `WHERE 1; DROP TABLE orders` as a *block* of two
    statements whose columns resolve, so a chained statement passed as a valid
    filter and would have been rendered into every prompt. `check_expression`
    now refuses anything that is not one `SELECT` probe. No stored metric in
    the fixture or the demo database contained an inner `;`, so no layer's
    prompt changed and `PROMPT_VERSION` did not move.
  - **Text limits hold both doors, on write** (`app/semantic/limits.py`): a
    business context of 10,000 characters, descriptions of 4,000, expressions
    of 4,000, filters of 2,000, and so on, plus item counts per list.
    `PUT …/semantic`, `PUT …/semantic/draft` and import refuse the first five
    problems in a sentence; a generation is clipped to the same limits instead.
    They are **not** `Field(max_length=…)` on the model: a parse-time limit
    would make an over-long stored layer fail to load — and the loaders fail
    open, so it would silently leave every prompt — and would let one verbose
    generated sentence sink a table. The real layers sit far inside them (the
    longest text in `sales` and `aurora` is a 411-character context), and a
    test fails when a text field exists without a limit.
- **Upkeep: regenerate chosen tables, and see what needs attention** (Phase 5,
  no migration).
  - **The generate dialog picks tables.** *What is missing*, *Tables I choose*
    (a searchable list marking which are already described) or *Every table*,
    and two modes for a table that already has an entity: **Fill the gaps**,
    the default, and **Rewrite**. `POST …/semantic/generate` takes `mode`
    `FILL_GAPS`, `REPLACE` or `MERGE` (the API's default, kept for scripts) and
    refuses tables the schema does not have. Either way the result is a draft.
  - **Fill the gaps never overwrites a field a person wrote**
    (`merge_documents(…, fill_gaps=True)` and `fill_entity`). An edited entity
    is kept and takes a generated value only where its own is empty — a text,
    an empty list, a role still `unknown` — and gains the columns and metrics it
    does not have; an existing column gains only its own empty texts. **An
    existing metric is never touched**: its empty `filters` are a definition,
    not a blank. A generated metric whose name another table already defines is
    not added, because `_refuse_ambiguous_metrics` would then switch off both.
    Nothing is dropped — an entity or glossary term the generation did not
    return stays, edited or not. Entities nobody edited are described afresh,
    as under `MERGE`.
  - **A run over chosen tables changes those tables and nothing else**, in any
    mode (`confine_to_tables`): every other entity stays as it was and where it
    was, and the business context, exclusion rule and glossary are only filled
    where empty. *Before this* a partial run replaced an unedited business
    context with one written from the chosen tables alone and dropped every
    glossary term those tables did not produce.
  - ***Needs attention*** (`GET …/semantic/attention?days=30`, `select`) is a
    filter beside *Has issues* and *Needs review*, and the hero's *need
    attention* count. Six reasons, decided in `app/semantic/attention.py` from
    what is already stored, most urgent first: **a draft untouched for 7
    days**; **an entity, column or metric the current snapshot breaks** (the
    binder's verdicts, not stored flags); **a table whose columns were added,
    removed or retyped since the version that last changed its entity** — the
    snapshot at that version's `schema_version` against the newest (a migrated
    v1 wrote no change rows, so an entity no row names dates from the first
    version; an entity a draft saved after the newest sync changed is not
    compared, and neither is one the draft adds); **a metric answers left part
    of more often than they used it** over 30 days; **an entity a model wrote
    that nobody reviewed, which Grounded answers stood on** over 30 days (the
    tier's own rule, `is_grounded`, against each run's version; Verified
    answers are not counted); and **a table with no entity**. Excluded entities
    are never a reason. Counts and schema names only. The editor groups it per
    table (`semantic-attention.ts`): *Open* opens the card on the tab the reason
    is about, a table that gained columns offers *Fill the gaps…*, and the
    undescribed tables share one row with *Describe…* — both open the generate
    dialog with those tables chosen.
  - On the demo `aurora` layer it found what nothing had shown before: four
    columns on `orders` and `order_items` retyped `smallint` → `integer` since
    v1 was bound, and nine model-written, unreviewed entities that Grounded
    answers stood on — `orders` alone by ten answers in 30 days.
- **The benchmark is scored with the layer.** Before v10 `workers/benchmark.py`
  never passed it, so every `benchmark_runs` row at v9 or earlier was taken
  layer-off whatever the switch said.
- ***Grounded* respects the switch.** An answer's tier reads the layer through
  the same loader, so a switched-off layer describes nothing and an entity
  counts only while it binds and is not excluded.
- **Regeneration is safe.** Any field a user edits sets `provenance.edited`, and
  `merge_documents` keeps those entities — and the business context and
  exclusion rule, each on its own flag. *Fill the gaps* keeps them and adds only
  what they lack; *Rewrite* (`REPLACE`) is the explicit choice the UI makes you
  make. Either way the result is a draft.
- **It is off-by-absence.** With no layer, or with
  `connections.semantic_layer_enabled` false, `RetrievedContext.render` emits
  **byte-identical** output to before the feature existed — verified by a test.
  That switch is how you A/B a layer against the bare schema on the eval suite
  without deleting it. `PROMPT_VERSION` moved when it shipped, because two runs
  either side of it are otherwise indistinguishable from the outside.
  Flipping the switch is audited as `semantic.switch.changed` `{from, to}`; it
  is set under `connection modify`, as part of the connection's policy.
- **It widens no disclosure.** Generation reads the same schema block a run
  reads, under the same `HintBudget`, and column `value_meanings` are filtered
  to values already in the snapshot — the model cannot invent a key to leak.
- **The cap is an allocation, not a truncation** (`app/semantic/render.py`).
  Over the 8k `DEFAULT_MAX_CHARS` cap the block is fitted **line by line**, in
  three tiers, each filled **round-robin** across the retrieved tables:
  1. every table's head line — business name, grain, role, date column,
     synonyms;
  2. metrics, one per table per pass — the lines that change the SQL;
  3. column meanings, one per table per pass.

  Round-robin because relevance is unknown here: under `FULL_SNAPSHOT` the
  retrieved order is catalog order, so a table with sixty described columns must
  not spend the budget forty others needed. A line that does not fit is skipped,
  never cut in half — half a metric is where the `WHERE` clause lived. The
  section behind the tables (join cautions, then glossary) is fitted the same
  way rather than dropped whole.

  *This replaced a real bug, fixed 2026-08-30.* The block used to be assembled
  in sections and pop whole sections off the back, and every table description
  was **one** section: past a cliff at six retrieved tables the layer arrived as
  `business_context` plus the time conventions and nothing else — `sales` (42
  entities) rendered 545 chars describing **0** tables, `aurora` (13) rendered
  606 describing **0**. Both fixtures sit far under the 50k retrieve budget, so
  they always take `FULL_SNAPSHOT`, pass every table, and were always past the
  cliff: the business names, grain and metrics reached the generator on no
  question at all. It was masked by the layer-wins-per-entity rule — coverage
  reported nothing covered, so the DDL `COMMENT ON` text rendered instead and a
  well-commented database still looked informed. The old trim test used one
  table and `max_chars=250` and only asserted the output was short, which is why
  it was never caught; `tests/unit/test_semantic_render.py` now fits 42 tables
  under the real cap. `PROMPT_VERSION` moved v7 → v8: this changes what the
  generator sees on every question asked against a connection with a layer.
- **Coverage is a projection of the render, not a second opinion.**
  `render_with_coverage` returns the block and the tables/columns it speaks
  about from one fit; `render_semantic` and `covered_keys` are its two halves.
  It has to be one call now that entities render *partially* — a table
  described with three of its six columns is normal, and the other three still
  need their DDL comments.
- **Editing** lives in Data sources → Semantic layer
  (`frontend/src/components/semantic.tsx`). Metric expressions are validated
  live by `POST .../semantic/check`, which is the *same parser* the save path
  uses — the editor never promises something the backend will reject.
- **A metric is *defined* on its entity and *browsed* in a list, and those are
  two different questions.** The definition stays on the table it measures — an
  aggregate needs a grain, columns and a validator that can resolve them, which
  is why every product with this feature anchors it somewhere (a dataset in
  Superset, a home table in Power BI, a source in a Databricks metric view).
  The **Metrics** panel above the table list is the other reading of the same
  document: `semantic-metrics.ts` flattens every entity's metrics, sorts by name
  then table, and a click routes back to that table's own card opened on its
  metrics section — one editor, two ways in, no second copy and no migration.
  It exists for the two questions the tree cannot answer: *what does this
  database measure*, and *does a name mean one thing*. `required_joins` is the
  tell that the tree was never the whole truth, since a metric already reaches
  through joins into tables it does not hang off.
- **A metric name means one thing, and `_refuse_ambiguous_metrics` enforces
  it.** Every other check in `semantic/validate.py` binds a definition to the
  *schema*; this one checks the document against itself. `revenue` on `orders`
  and `revenue` on `invoices` are each valid, each render into the same prompt,
  and the model then picks one — silently, and not always the same one. Both are
  refused with a sentence naming the other, the same posture the knowledge store
  takes with two templates that disagree. Two deliberate exemptions: an
  **excluded** entity claims nothing, because it is not in the prompt at all;
  and a metric already invalid for a schema reason keeps that reason, since it
  is out of the prompt either way and the collision surfaces the moment it is
  fixed. The panel computes the same collision client-side, so it is visible
  while it is being typed rather than only after a save.
