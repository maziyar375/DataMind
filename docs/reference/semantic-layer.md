# The semantic layer

What the schema *means*, as one editable document per connection — and the
rules that keep it from lying to the generator.

Code: [`backend/app/semantic/`](../../backend/app/semantic/) — `generator.py`
(one model call per table), `render.py` (the tiered fit), `validate.py` (what
is refused), `merge.py` (what survives a regeneration). The editor is
[`frontend/src/components/semantic.tsx`](../../frontend/src/components/semantic.tsx).

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
- **Regeneration is safe.** Any field a user edits sets `provenance.edited`, and
  `merge_documents` keeps those entities; `REPLACE` is the explicit "start
  over" the UI makes you choose.
- **It is off-by-absence.** With no layer, or with
  `connections.semantic_layer_enabled` false, `RetrievedContext.render` emits
  **byte-identical** output to before the feature existed — verified by a test.
  That switch is how you A/B a layer against the bare schema on the eval suite
  without deleting it. `PROMPT_VERSION` moved when it shipped, because two runs
  either side of it are otherwise indistinguishable from the outside.
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
