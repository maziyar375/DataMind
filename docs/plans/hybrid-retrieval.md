# Hybrid retrieval — build plan

> **Status: built, 2026-09-23 — all three phases, 20 of 20 boxes. The one
> thing still open is the measurement, and it is a provider run rather than
> code.** Written the same day against `main` (`cb148b3`). This is [mvp2.md](mvp2.md) **B2**, the last unbuilt row of
> [status.md §4](../status.md#4-what-is-next)'s Tier 1, and the third of the
> three items [deep-analysis-mode.md §0.3](deep-analysis-mode.md) names as the
> work that would move its gate.
>
> The argument for *why* lives in
> [research/retrieval-at-scale.md](../research/retrieval-at-scale.md) §O7 and
> §O8; this document is *what we build, in what order, and how we know it
> worked*.
>
> **Two divergences from the documents above, stated up front.**
>
> 1. **mvp2 B2 says `pgvector`. This plan does not use it.** That sentence was
>    written in August, before [learning-loop.md](learning-loop.md) Phase 7
>    answered the same question for the same kind of index and shipped
>    `double precision[]` with the cosine computed in Python — *"the index
>    narrows, the matcher decides"*. Building a second, different answer to one
>    question is worse than either answer. §0.2 D2.
> 2. **The research rates O7 before O8 and this plan agrees**, but O7 is
>    smaller here than it was when that was written, because
>    [mvp2 §A5](mvp2.md#a5-business-term-and-synonym-index-used-at-retrieval--s--built-2026-09-22)
>    landed the half of it that reads the layer's *names*. What is left is the
>    half that reads its **sentences** — and the DDL comments, which nothing on
>    the retrieval path has ever read.
>
> §11 is the ledger: a checkbox per deliverable per phase, each with the check
> that proves its state. Tick a box in the commit that lands the work, never
> ahead of it.

---

## 0. The shape of it, in one page

### 0.1 The one-sentence goal

*On a database too large to send whole, the tables that reach the model are
chosen using every sentence somebody has already written about the schema — the
DBA's `COMMENT ON`, the curator's descriptions — and, where the connection can
serve vectors, on what the question **means** rather than only on the words it
happens to repeat.*

### 0.2 The four decisions

**D1 · The score reads only prose the other tiers do not read.**
Physical names belong to `match_tables`; labels, synonyms, metric names and
glossary terms belong to `match_by_terms` (A5). This scorer reads **sentences**:
table and column comments, the layer's `description`, `grain`, `meaning`,
`value_meanings` and a metric's `description`. Nothing is scored twice, and the
consequence is the property that makes an A/B possible at all: **with no DDL
comments and no semantic layer, every score is 0.0 and the ranking is
byte-identical to v11's.** That is the *off-by-absence* rule
[CLAUDE.md](../../CLAUDE.md) states for the two curated documents, extended to
the thing that ranks them.

**D2 · No `pgvector`, and no new deployment unit.**
`docker-compose.yml` runs `postgres:16-alpine`, which does not carry the
extension, and a managed Postgres may refuse it. Vectors are a
`double precision[]` column and the cosine is computed in Python — the decision
[learning-loop.md](learning-loop.md) D3 already made and Phase 7 already
shipped, in `app/knowledge/embed.py`. A schema index is *smaller* than a
template store, not larger: one row per table, not one per question anybody ever
asked.

**D3 · Embeddings are a capability, not a preference.**
A connection with no embedding model pinned has no vector index and the lexical
score answers alone — `FallbackMatcher`'s posture, applied to the schema. There
is no switch to turn this on, because a switch implies it could be on where it
cannot work. `runs.retrieval_signals` records which signal actually chose each
table, so *"is this doing anything?"* is a query rather than an opinion.

**D4 · `include_db_comments` governs ranking as well as rendering.**
The flag exists so a connection can refuse to send its DDL comments to a
provider. Reading them only to rank sends nothing — but it lets them decide what
*is* sent, and a rule with an exception in it is a rule somebody will get wrong
in six months. **Comments off means comments unread**, and the layer's prose
answers alone. Stated once, in `relevance.table_prose`, and tested.

### 0.3 The phases

| | Phase | What it is | New dependency | Provider call |
|---|---|---|---|---|
| **1** | Rank on the sentences somebody already wrote | `app/pipeline/relevance.py`, a sixth tier in `fit_to_budget`, a seed in `retrieve` | none | none |
| **2** | Vectors over the same text, blended | `schema_table_vectors` + `0039`, an indexing pass, a blended rank | none | one `embed` per run, when pinned |
| **3** | The index says what it knows | freshness in the product, per-run signal telemetry, and the sentence §6.5 asks for | none | none |

Phase 1 ships on every install the day it lands. Phase 2 ships on the installs
that have an embedding endpoint. Phase 3 is what makes either falsifiable from
outside a unit test.

---

## 1. What retrieval cannot see, and it is already written down

`retrieve` has four strategies and only one of them **chooses**: `RANKED_MATCH`,
reached when the snapshot is over `_RETRIEVE_BUDGET_CHARS` (50,000 — about
eighty average tables). Everything below is about that branch, and about nothing
else. The other three send the whole snapshot, the whole section, or spend the
budget by `select_tables`' own rule.

As of v11 that branch reads exactly four things:

| Signal | Reads | Landed |
|---|---|---|
| `named` | the table's own name, spelled as a person would type it | 2026-09-19 |
| `by_term` | the layer's labels, synonyms, metric names, glossary terms | 2026-09-22 (A5) |
| `carried` | the tables the conversation's own SQL already queried | 2026-09-19 |
| `by_column` | a column's name, spelled the same ways | 2026-09-19 |

Every one of those is a **word the question repeats**. Which means a question
whose words appear nowhere in the schema and nowhere in the layer's vocabulary
selects nothing at all, and falls to `fit_to_budget`'s last rule — *"anything
else, largest first"*. On a wide warehouse that is a coin toss weighted by row
count.

And the content that would answer it is sitting in the same snapshot:

- **`tables[].comment` and `tables[].columns[].comment`** — what the DBA wrote
  in the DDL. `state.py` renders them into the prompt under a char budget, and
  `metadata._detail` prints them in the schema-question fallback. **Nothing
  ranks on them.** `catalog-metadata.md` documents how they are read per engine;
  they are the single largest body of schema prose in most real databases.
- **The semantic layer's sentences** — `entity.description`, `entity.grain`,
  `column.description`, `column.value_meanings`, `metric.description`,
  `glossary.meaning`. A5 indexed the layer's **names**; its prose is longer than
  its names by an order of magnitude and is where a curator actually explains
  what a table is *for*.

*"Which tables have anything to do with refunds?"* does not contain the word
`order_items`, and a curator who wrote *"one row per line of an order;
negative quantities are returns"* has answered it. Today that sentence reaches
the model only if the table was already chosen — which is exactly backwards.

---

## 2. The score

### 2.1 What goes in the bag

`relevance.table_text(table, *, layer, include_comments)` returns the sentences
for one table and `rank_tables` turns the bag into tokens; the layer's half of
it is `semantic.table_prose(doc)`, keyed by table exactly as `table_terms` is.
In order:

1. `table["comment"]`, and each `column["comment"]` — **only when
   `include_comments`** (D4).
2. The layer's entity for that table, when one exists and is neither `exclude`d
   nor invalid: `description`, `grain`, then each valid column's `description`
   and the values of its `value_meanings`, then each valid metric's
   `description`.
3. Every glossary term whose `maps_to` resolves to that table or to a metric
   the table owns: its `meaning`. The resolution is `table_terms`' own, and it
   is the one place the two halves touch the same data — A5 takes the term,
   this takes the meaning. `vocabulary_terms` drops these meanings deliberately
   because it is building vocabulary; this is built from them.

**Not in the bag, deliberately:** names, labels, synonyms, metric names,
glossary terms, `business_context` and `default_exclusions`. The first five
belong to a tier that already reads them (D1). The last two belong to *every*
table, so they carry no signal about which one — and a token in every document
scores zero under §2.2 anyway, so this is belt and braces.

### 2.2 IDF, and why the corpus is the candidate set

A raw overlap count scores `orders` highest on the word `the`. The weight of a
token is therefore

```
idf(t) = ln(1 + N / (1 + df(t)))
```

with `N` the number of tables being ranked and `df(t)` how many of their bags
contain `t`. A word in every table's comment weighs ~0; a word in one weighs
~ln(N). No stopword list can be maintained and no stopword list is
language-specific in the right way — `df` is measured from this customer's own
prose, so "order" is a stopword in an order-management database and a strong
signal in a payroll one, which is correct and which no hand-written list gets
right.

The corpus is **the tables being ranked**, computed once per run before the FK
expansion, so a table's weight does not change depending on which seeds happened
to pull their neighbours in.

The question contributes `_tokens(question)` — `metadata`'s own regex, so one
rule tokenises questions everywhere — minus tokens shorter than
`_MIN_TOKEN_CHARS` (3, matching `terms.py`). The score is the matched share of
the question's own weight:

```
score(table) = Σ idf(t) for t in question ∩ bag  /  Σ idf(t) for t in question
```

∈ [0, 1], 0 when the question shares nothing, 1 when a table's prose contains
every content word of the question. A question none of whose tokens appear in
any bag scores every table 0.0 and changes nothing — the denominator is the
question's weight *as measured against this corpus*, so an unanswerable question
does not get normalised into a false winner.

### 2.3 The floor, and the seed cap

`_PROSE_FLOOR = 0.2`. A table joins the prose tier when it carries at least a
fifth of the question's weight: a five-word question needs one of its words, a
twenty-word question needs four. Longer questions demand more, which is right —
one shared word out of twenty is noise.

`PROSE_MAX_SEEDS = 20`. Prose is the **weakest** of the five signals and ranks
below all of them (§3). A prose table past the twentieth would have to survive a
cut that every named, carried, column-named and bridge table precedes; seeding
the FK expansion from it spends CPU on a table that cannot be shown. Twenty is
the cap on what *seeds*, never on what scores — the score still orders the tail.

---

## 3. Phase 1 — Rank on the sentences somebody already wrote

**The tier.** `fit_to_budget` gains `scores: dict[str, float] | None = None`
and one tier, and the order becomes:

| | Tier | Evidence |
|--:|---|---|
| 0 | named | the user's own word for the table |
| 1 | by term | a curator's word for *this* table |
| 2 | carried | the subject the conversation is already on |
| 3 | by column | a word half the warehouse may share |
| **4** | FK hop | a join path the question implies — bridges first |
| **5** | **prose** | **a sentence about the table matched, ≥ `_PROSE_FLOOR`** |
| 6 | rest | everything else |

Prose ranks **below the FK hop** because the hop is structural: dropping a
bridge does not make the answer worse, it makes the query impossible. Prose
ranks **above the rest** because "this table's description is about what you
asked" beats "this table is big".

Within every tier the tiebreak becomes `(-touches, -score, -rows, order)` —
`-score` inserted **before** `-rows`, so size is demoted to what it always
should have been: the last resort, used when nothing has anything to say. With
no comments and no layer every score is 0.0, the new key is constant, and the
sort is the one v11 performed.

**The seed.** `retrieve` computes `scores` over `scoped` (before expansion) and
seeds the FK hop with the top `PROSE_MAX_SEEDS` above the floor, alongside
`named`, `by_term`, `carried` and `by_column`. A table found only in prose is
still a subject, and its bridges are still needed.

**`PROMPT_VERSION` → `v12`.** What the model reads changes on `RANKED_MATCH`
for any connection that has DDL comments or a semantic layer. §6.

**Deliverables** — §11.

---

## 4. Phase 2 — Vectors over the same text, blended

Everything Phase 1 builds is reused: the same `table_prose` text is what gets
embedded, so the lexical and vector halves are demonstrably scoring the same
document and a difference between them is a difference in *method*, not in
input.

**The store.** `schema_table_vectors` — `(connection_id, qualified_name)`
unique, plus `schema_version`, the text's `fingerprint`, `embedding
double precision[]`, `embedded_at`. `0039`. CASCADE on the connection, like
`semantic_layers`.

**Staleness is derived, never tracked**, exactly as `knowledge.embed` does it:
the fingerprint hashes *(prose, model id, dimension)*, so a schema re-sync, a
layer edit and a model change each invalidate precisely what they should and
there is no invalidation call anybody can forget. A row whose fingerprint does
not recompute is **ignored, not deleted** — the next indexing pass replaces it,
and until then the lexical score answers.

**The indexing pass** is a worker, beside `knowledge_maintenance`: for each
connection with an embedding model pinned, embed the tables whose fingerprint
has moved, in batches. Bounded per pass. Never on the request path.

**The blend.** `retrieve` embeds the question once — `asyncio.wait_for` on
`settings.embedding_match_timeout_seconds`, the budget the template matcher
already uses — and the tier-5 score becomes

```
blended = max(lexical, cosine_rescaled)
```

`max` rather than a weighted sum, and this is the one number in this plan worth
arguing about: a weighted sum needs a weight nobody can derive without the
measurement §8 owes, while `max` says *"either kind of evidence is enough"*,
which is what a **recall** step is for. Precision is the cut's job, not the
scorer's. The rescale maps `[SIMILARITY_FLOOR, 1]` onto `[0, 1]` for the same
reason `knowledge.embed` has a floor at all: two unrelated English sentences
cosine around 0.3, so the bottom of the range is not evidence of anything.

**Every failure is lexical.** No pinned model, no fresh vectors, a timeout, a
provider error, a dimension that disagrees with the index — each falls to the
Phase 1 score with the run unaffected. A retrieval feature that can fail a
question is worse than one that can be absent.

---

## 5. Phase 3 — The index says what it knows

Three things, none of which change an answer:

1. **`runs.retrieval_signals`** — which signal chose each selected table,
   `{name|term|carried|column|fk|prose|vector}`, as counts. `0036` already put
   `retrieval_strategy`, `retrieval_tables` and `retrieval_chars` on `runs` and
   this is the fourth column of the same instrument. Nullable, and a run that
   predates it reads as *not measured* — the NULL-vs-0 rule this repo has
   already written down twice.
2. **Freshness, in the product.** The Connections screen says when the vector
   index was last built and how many tables are stale, using the same derived
   staleness — no new state, and no *"Rebuild index"* button that lies about
   what it did.
3. **What was left out.** §6.5 of the research: `dropped_tables` already exists
   on `RetrievedContext` and the step detail already counts it. Phase 3 puts the
   **names** where a curator can read them, because *"the retrieval was wrong"*
   and *"the SQL was wrong"* are different bugs and today only one of them is
   visible.

---

## 6. What moves `PROMPT_VERSION`, and a correction to the research

[research/retrieval-at-scale.md §4.10](../research/retrieval-at-scale.md) has a
row reading *"`PROMPT_VERSION` moves: no"* across all nine options, with the
note *"Retrieval decides which tables go in the block; the block's shape is
unchanged."*

**That row is wrong, and A5 already proved it.** CLAUDE.md's rule is that
prompts means *"everything the model ends up reading, not only wording: a change
to how much of the schema block survives moves it too."* A ranking change moves
which tables survive the cut, so it moves what the model reads, so it moves the
constant — A5 took `v10 → v11` for exactly this and Phase 1 takes `v11 → v12`.

The correction is worth making precisely because the research's conclusion —
*retrieval work can proceed while the prompt baseline question is open* — is
still **true**, and for a better reason than the one given: a retrieval change
is inert on a connection with no comments and no layer (D1), so the arms that
measured the prompt remain comparable to each other. It is comparability across
*configurations*, not across versions, and that is what the ledger has to say.

Phase 2 moves nothing further: the vector blend changes the same ranking the
same way, and a second `PROMPT_VERSION` bump inside one feature would tell a
reader that two different things reached the model when only one did. What it
*does* need is the second model in the reproducibility story — the embedding
model id belongs beside `model_snapshot`, and Phase 2 owes that.

---

## 7. Security, disclosure and the guard

- **The guard is untouched.** `policy_from_snapshot` builds its allowlist from
  the **whole** snapshot and does not know retrieval exists. A table this
  ranking selects or drops is neither more nor less queryable than it was. The
  test that pins this for sections (`test_section_guard_unaffected.py`) gets a
  sibling.
- **Nothing new reaches a provider in Phase 1.** The score is computed here and
  discarded; the block that goes out is built by `RetrievedContext.render` from
  the same fields under the same `HintBudget` it always was.
- **Phase 2 sends schema prose to an embedding endpoint.** That is a new egress
  and it is the same content the chat prompt already sends under
  `include_db_comments`, to the same class of provider — but to a *different
  configured row*, so D4's rule is what keeps it honest: a connection with
  comments off embeds the layer's prose only. Written up as
  [security.md §4.9](../reference/security.md), and as a **fifteenth row** in
  its §2 inventory of every place data leaves for a provider — which also
  turned up that §2's own verification grep never covered `gateway.embed`, so
  #13 had been outside it since Phase 7. The grep now includes it.
- **No customer *data* is embedded** — no values, no samples, no rows.
  `value_meanings` is a curator's gloss on a code, written by hand, not a
  probed value; `B3`'s value dictionaries are a different feature with a
  different disclosure decision and this plan does not anticipate them.

---

## 8. How we know it worked

**The suite cannot see this at its default budget**, for the same reason it
could not see A5: the eval fixture is far under `_RETRIEVE_BUDGET_CHARS`, so
every question takes `FULL_SNAPSHOT` and `RANKED_MATCH` never runs. The arm is
`--retrieve-budget 8000`, the flag B1 established, against the same budget with
the feature absent.

**Every flag it needs already exists**, which is the one piece of luck in this
plan. `--comments` loads the `sales` fixture's 66-statement `COMMENT ON`
overlay *and* sets `include_db_comments` on the connection — which since v12 is
also what decides whether comments are read for ranking — and `--semantic on`
loads the hand-written layer. So A5's owed arm and B2's are **four cells of one
grid** rather than two pairs: neither, layer, comments, both, all at budget
8,000 on one model. Two separate A/Bs would spend twice to answer one question
badly, since the second would have to re-measure the first's arm to stay
comparable. Recorded in [eval.md §6](../reference/eval.md).

Three sources of evidence, in the order they become available:

1. **Unit tests over a synthetic wide snapshot** — `_RETRIEVE_BUDGET_CHARS` is
   a module constant so a test can lower it, and `test_retrieval_terms.py`'s
   `_wide()` helper already builds the snapshot. Proves the mechanism, not the
   accuracy.
2. **A recall arm at budget 8,000.** Layer-off recall at that budget is
   `dc2ea4fd` — mean **80.2 %**, full-hit **62.0 %**, DeepSeek V4 Pro. **A
   figure from any other model may not be put in a sentence with it**
   ([eval.md §5](../reference/eval.md)), so an arm run on Flash needs its own
   Flash control and reads as a within-run delta only.
3. **The distribution off `runs.retrieval_signals`** (Phase 3), which is the
   only one of the three that measures a real customer's questions rather than
   fifty frozen ones.

**Until (2) exists, no claim that this improved retrieval is falsifiable** —
the rule A5 and B1 were both written under, and the reason A5's own arm is still
recorded as owed in [eval.md §6](../reference/eval.md).

---

## 9. File-by-file change map

| File | Phase | Change |
|---|:--:|---|
| `app/pipeline/relevance.py` | 1 | **new** — `table_text`, `rank_tables`, `prose_seeds`, the constants. Pure, no I/O |
| `app/semantic/terms.py` | 1 | `table_prose(doc)` beside `table_terms` — the sentences beside the names |
| `app/pipeline/metadata.py` | 1 | `_tokens` → `question_tokens`, `_qualified` → `qualified`: one tokeniser, one key |
| `app/pipeline/nodes/__init__.py` | 1 | `_term_index` → `_layer_index` (both registers, one parse); `fit_to_budget(scores=…)` + the prose tier; `retrieve` scores, seeds and reports |
| `app/pipeline/prompts/__init__.py` | 1 | `PROMPT_VERSION` → `v12`, with the comment block the constant carries |
| `tests/unit/test_retrieval_prose.py` | 1 | **new** — the bag, the IDF, the floor, the tier, the node |
| `app/infra/db/models.py` | 2 | `SchemaTableVector` |
| `…/migrations/versions/0039_schema_table_vectors.py` | 2 | **new** |
| `app/pipeline/relevance.py` | 2 | `prose_fingerprint`, `VectorIndex`, `cosines`, `rescale`, `blend` — still no I/O |
| `app/services/retrieval_index.py` | 2 | **new** — build, read, staleness, the bounded embedder |
| `app/workers/schema_index.py` | 2 | **new** — its own loop, its own population, its own cadence |
| `app/core/config.py` | 2 | `schema_index_interval_seconds` |
| `app/main.py` | 2 | the loop, started and cancelled with the others |
| `app/services/run_service.py`, `sql_draft_service.py` | 2 | the index on both build paths |
| `app/pipeline/nodes/__init__.py` | 2 | `NodeDeps.vectors`, `_vector_scores`, the six fail-opens |
| `app/eval/runner.py` | 2 | `--schema-vectors` and `embed_schema` — **additive**, default off |
| `tests/unit/test_retrieval_vectors.py` | 2 | **new** — the store, the fingerprint, the blend, the six |
| `app/pipeline/nodes/__init__.py` | 3 | `rank_tiers` + `SIGNALS` extracted from `fit_to_budget`; the dropped names |
| `app/infra/db/models.py` + `0040` | 3 | `runs.retrieval_signals`, nullable |
| `app/api/v1/knowledge.py`, `schemas.py` | 3 | `schema_tables` / `schema_tables_indexed` on `EmbeddingStatus` |
| `frontend/…/knowledge-template.ts`, `knowledge.tsx` | 3 | `schemaIndexLine`, one sentence under the pin |
| `tests/unit/test_retrieval_signals.py` | 3 | **new** — the vocabulary, one rule two readers, the node |

Reference docs that own a sentence this changes:
[pipeline-chat.md](../reference/pipeline-chat.md) §4 step 4,
[eval.md](../reference/eval.md) §6,
[catalog-metadata.md](../reference/catalog-metadata.md) (comments now rank),
[semantic-layer.md](../reference/semantic-layer.md) (the prose is read in a
third direction), [security.md](../reference/security.md) §3 (Phase 2's egress),
and [CLAUDE.md](../../CLAUDE.md)'s constants line.

---

## 10. Risks

| Risk | What we do |
|---|---|
| A verbose comment on a junk table outranks a terse one on the right table | IDF measures `df` on this customer's prose, so a phrase repeated across a hundred tables weighs nothing. The floor is a *share of the question's* weight, not a raw count |
| The prose tier crowds out the FK bridges | It cannot: it ranks strictly below them, and the cut walks in rank order |
| A layer edit silently changes retrieval | It always could (A5). Phase 3's signal counts are what make it visible |
| Phase 2's index goes stale and nobody notices | Staleness is derived from a fingerprint, not tracked — the pass cannot forget, and Phase 3 shows the count |
| Embedding endpoint is slow | One call, `wait_for`, falls to lexical. The template matcher already runs under the same budget on the same path |
| We over-claim | §8. The arm is owed and the ledger says so until it is run |

---

## 11. Progress ledger

Tick a box in the commit that lands the work, never ahead of it.

### Phase 1 — Rank on the sentences somebody already wrote
- [x] `relevance.table_text` + `semantic.table_prose` — comments and layer prose, nothing a tier already reads
- [x] `include_db_comments` off means comments unread · *a test, not a convention*
- [x] `rank_tables` — IDF over the candidate set, normalised, deterministic
- [x] The prose tier in `fit_to_budget`, below the FK hop
- [x] `-score` before `-rows` in every tier's tiebreak
- [x] `retrieve` seeds the hop from prose, capped at `PROSE_MAX_SEEDS`
- [x] **Byte-identical with no comments and no layer** · *the off-by-absence test*
- [x] `PROMPT_VERSION` → `v12`, and every pin updated
- [x] The step detail says how many tables prose *added*

> Landed 2026-09-23. `tests/unit/test_retrieval_prose.py`, 37 tests. What was
> decided while building it, and the one thing the plan above had wrong:
>
> - **The two registers come out of one parse.** `_term_index` became
>   `_layer_index`, returning `(terms, prose)`: A5 already parsed the document
>   once per run and a second function would have parsed it twice.
> - **The step detail counts what prose *added*, not what it matched.** A
>   table's own comment usually contains its own name, so a table the question
>   named scores high on prose as well — reporting that as *"1 by description"*
>   would have the trail claim the feature did work that `match_tables` did.
>   `by_prose` is therefore the seeds minus everything the other four signals
>   already found.
> - **The IDF is `ln((N + 1) / df)`, and the normalisation has an honest
>   limit.** A word in every table's prose weighs about nothing *as a weight* —
>   but the score is a share of the question's own weight, so a question whose
>   only corpus word is universal scores every table 1.0. That promotes all of
>   them, which is the same as promoting none: they land in one tier and the
>   tiebreak behind them is untouched. Written into the module rather than left
>   for somebody to discover, and pinned by a test named after it.
> - **`match_tables` tokenises `order_items` as one token**, so a question that
>   names a table by its physical name does not also score its comment through
>   the word "order". That is why the two signals stay disjoint in practice and
>   not only by intent.
> - **The off-by-absence claim is asserted, not assumed** — at the function
>   (`scores={}` ranks identically to no `scores` at all, over three budgets and
>   three seed shapes) and at the node (comments off with no layer selects the
>   same tables it selected at v11).

### Phase 2 — Vectors over the same text, blended
- [x] `schema_table_vectors` + `0039` · *`alembic upgrade head` on a clean database and on a clone of the populated one, `downgrade 0038` → `upgrade` again, and the migration and ORM replayed against one recorder*
- [x] `fingerprint` derived from (prose, model, dimension) · *edit, re-pin and re-width each invalidate*
- [x] The indexing pass, bounded, off the request path · *`workers/schema_index.py`*
- [x] The question vector under `embedding_match_timeout_seconds`
- [x] `max(lexical, rescaled cosine)` · *and the argument for `max` in the code*
- [x] Every failure path is lexical · *six of them, one parametrised test each*
- [x] The embedding model id in the reproducibility record · *`--schema-vectors`, on the scorecard*

> Landed 2026-09-23. `tests/unit/test_retrieval_vectors.py`, 29 tests. What was
> decided while building it:
>
> - **The arm exists.** This was going to be the phase whose claim could not be
>   measured at all — the eval builds its own `NodeDeps` and pins no embedding
>   model — so `--schema-vectors` was added to the runner beside `--comments`
>   and `--matcher`: one call to embed the schema's prose before the run, one
>   per question during it, and `embed_schema` builds its bags with
>   `relevance.prose_by_table`, the same function the node calls. That last
>   detail is the whole arm: an index whose every fingerprint is stale reports
>   *"embedded 42"* and changes nothing, which is the one failure here with no
>   symptom. The runner **refuses** to report a lexical run under a vector
>   label, exactly as the template arm does.
> - **Its own worker, not a fourth step in `knowledge_maintenance`.** That loop
>   runs over connections that *have templates*, which is precisely the wrong
>   population: a connection with a documented schema and no curated questions
>   is the one this helps most.
> - **One function builds the prose on both sides.** `retrieval_index`
>   `prose_for_connection` and the node both go through `prose_by_table`, and
>   the service says why in its docstring: if they drift by one field, no
>   fingerprint ever matches, nothing is ever fresh, and the feature silently
>   does nothing while reporting success.
> - **`load_vector_index` returns empty without touching the store**, so a
>   connection with no embedding model — which is every one of them today —
>   makes no query it did not already make.
> - **The sweep query lives in the worker, not the service.** A `select` over
>   an owned table inside `app/services/` has to compose `visible(...)` or ask
>   `require`, and `test_authz_conformance.py` enforces it — correctly, because
>   a service reads on somebody's behalf. A sweep has no behalf. The answer was
>   not to invent a god context but to put the unscoped query where the
>   codebase already puts this exact one (`knowledge_maintenance`), leaving the
>   service with per-connection work whose callers all hand it a connection
>   somebody was already allowed to reach. The conformance test caught this,
>   which is the whole reason it greps per module rather than per statement.
> - **The step trail does not yet tell a word hit from a vector hit.** Both
>   read `by description`. That is Phase 3's `retrieval_signals`, and until it
>   lands the honest statement is that the trail says prose chose the table,
>   not which half of prose did.

### Phase 3 — The index says what it knows
- [x] `runs.retrieval_signals` + `0040`, nullable · *and counted by the ranker itself*
- [x] Freshness where the pin already lives · *not the Connections screen — see below*
- [x] Dropped table **names** where a curator can read them
- [x] The first distribution read off a real connection · *and what it says is that this install cannot exercise the instrument*

> Landed 2026-09-23. `tests/unit/test_retrieval_signals.py` (13) and eight
> cases in `knowledge-template.test.ts`. What was decided:
>
> - **The telemetry is the ranker.** `fit_to_budget`'s tier function came out
>   as `rank_tiers`, and `retrieve` counts the selected tables with it. A
>   second reading of the same five lists would agree on the day it was written
>   and describe a ranking the code had stopped performing some months later —
>   which is the *only* way a column like this goes wrong, because nothing
>   fails when it does. `SIGNALS` is a tuple rather than a comment for the
>   same reason: `runs.retrieval_signals` stores those strings, and a rename
>   splits a distribution in two silently.
> - **A vector hit is filed apart from a word hit.** After `blend` the two
>   halves are one number, so `_vector_scores` reports *which* tables it
>   raised. Without that, *"is the embedding index doing anything?"* has no
>   answer at all once Phase 2 is on.
> - **NULL on three of four strategies, and that is the point.**
>   `FULL_SNAPSHOT` sent every table, `SECTION_SNAPSHOT` sent the section,
>   `SCHEMA_QUESTION` spent the budget by `select_tables`' rule: none of them
>   *chose*, and `{}` would read as "chose nothing". Third time this schema has
>   had to make the NULL-vs-zero call (`0023`, `0036`, `0037`).
> - **Freshness went where the pin already lives, not onto the Connections
>   screen this plan named.** The knowledge panel already owns the embedder,
>   the model and the width — and its three pin faults (`NO_EMBEDDER`,
>   `PROVIDER_MOVED`, `MODEL_MOVED`) now break **two** features, so a panel
>   naming only the taught questions would report half an outage. One extra
>   sentence (`schemaIndexLine`), silent in every state where it has nothing
>   true to add, including the faults, where the sentence above already covers
>   both. A second panel would have duplicated the pin to hold one count.
> - **The distribution, read off the real database**, which is the box this
>   plan wrote for itself:
>
>   | strategy | runs | avg tables | avg chars |
>   |---|--:|--:|--:|
>   | (not recorded) | 68 | – | – |
>   | FULL_SNAPSHOT | 3 | 13 | 15,643 |
>
>   **Every recorded run took `FULL_SNAPSHOT`, so every one of them will write
>   NULL to `retrieval_signals`** — the instrument is correct and this install
>   cannot exercise it, for the same reason A5 and B2 are inert here: 13 tables
>   and 42 tables both fit the budget whole. That is a finding about the
>   install, not about the column, and it is why the eval arm is still what
>   owes the number.

### The measurement
- [ ] The four-cell grid at budget 8,000 — neither / `--semantic on` / `--comments` / both, one model. Retires A5's owed arm too
