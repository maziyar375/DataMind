# Evaluation

How DataMind measures whether a change to a prompt, a retrieval rule, or the
semantic layer actually helped — and why the number it prints is trustworthy
enough to gate a build on.

Every other doc here describes something that either works or doesn't. This one
describes the only part of the system whose output is a *number*, and numbers
invite self-deception. Most of the design below exists to make the number harder
to fake.

Code: [`backend/app/eval/`](../backend/app/eval/) — `dataset.py` (the record
schema and the fixture registry), `runner.py` (the harness), `metrics.py` (pure
scoring), `suites/` (the frozen golden sets), `reports/` (write-ups of past
runs). Tests: [`backend/tests/eval/`](../backend/tests/eval/).

Companion to [pipeline.md](pipeline.md) (what is being measured) and
[architecture.md](architecture.md) (why the semantic layer exists — the eval is
what argued for it).

---

## 1. What it runs

**The real pipeline.** Not a reimplementation of it, not a prompt harness that
calls the model directly. `runner.py` builds the same `AnalyticsPipeline`, the
same `NodeDeps`, the same `GuardPolicy`, and the same connector the HTTP request
path builds, then walks every question through `route → match → retrieve →
generate → validate → execute → …`. A guard rule that would reject a query in
production rejects it here, and it costs the run a point.

> **`match` is always SKIPPED here.** The runner builds `NodeDeps` with no
> matcher, so the knowledge store is never consulted and every number on this
> suite measures the *generated* path — which is the only honest way to measure
> it. A run that answered a golden question from a template stored for that
> question would score 1.0 and mean nothing. Phase 5 of
> [learning-loop-plan.md](learning-loop-plan.md) adds a `--templates on|off`
> arm, and it is an arm precisely so both numbers exist side by side.

The target database is a **fresh container** spun from a fixture seed via
testcontainers — never a stored connection, never a database someone has been
poking at. `dataset.FIXTURES` maps a record's `connection_fixture` name onto the
seed to load:

| Fixture | Dialect | Seed |
| --- | --- | --- |
| `sales_pg` | postgres | `backend/fixtures/sales_seed.sql` (the 42-table commerce schema) |

The schema is deliberately wide — 42 tables, 599 columns — because it was built
to **exceed the retrieve node's budget**, so that retrieval was genuinely
exercised rather than trivially correct.

> **At the shipped ceiling that is no longer true, and recall reports a
> constant.** `_RETRIEVE_BUDGET_CHARS` was raised **24,000 → 50,000**
> (`pipeline/nodes`, for a good reason of its own: the exact-name fallback
> misses `order_items` for a user who typed "order items"). The fixture's
> estimate — `sum(60 + 40*ncols)`, printed by `make fixtures` — is **26,480**,
> which sits *under* the ceiling. So `retrieve` takes the `FULL_SNAPSHOT` branch
> on every question in this suite, all 42 tables reach the generator, and
> **retrieval recall is 1.0 by construction**. The baseline's
> `retrieval_recall: 0.864` was measured under the old ceiling.
>
> **The decision taken (2026-08-31, Phase 0 of
> [learning-loop-plan.md](learning-loop-plan.md)): the eval can be run at a lower
> ceiling, and the fixture was left alone.** `--retrieve-budget CHARS` lowers it
> for one run — a runner flag, never a code edit, because the ceiling that ships
> is the one the request path must run at. Widening the fixture instead would
> have moved every other number on the suite at the same time. Absent the flag a
> run is byte-identical to every run before it, and the effective value is
> recorded on the scorecard as `retrieve_budget_chars`, because **a recall
> figure cannot be read without it** — see
> [`suites/CHANGELOG.md`](../backend/app/eval/suites/CHANGELOG.md), and never
> put a lowered-budget recall in the same sentence as a full-snapshot one.
> Originally found on 2026-08-14 while building the commented arm below — see
> [catalog-metadata-plan.md](catalog-metadata-plan.md) §10.

### The semantic-layer arm

`--semantic on` renders the fixture's own semantic layer —
`backend/fixtures/sales_semantic.json`, 21 entities and 14 metrics — into every
SQL prompt, exactly as a connection with a layer would. `runner.load_semantic`
binds it to the snapshot the run introspected (`validate_document`, then
`derive_joins` off the catalog, the same two steps `semantic_service` performs)
and **aborts the run if any entry no longer resolves**: a half-binding document
would make the arm layer-on for some questions and layer-off for others.

```bash
python -m app.eval.runner --suite sales_v1                    # layer off
python -m app.eval.runner --suite sales_v1 --semantic on      # layer on
```

It is a checked-in file rather than a generated document because an arm whose
input is regenerated per run measures the generator. **Every claim in it is the
structured form of a fact already in `sales_seed.sql` or `sales_comments.sql`,
and it was not written by reading the golden set** — a layer authored against
the gold answers measures its author. Two of those restated facts add a
predicate a question did not ask for (closed customer accounts are excluded by
default; `average_rating` excludes moderated-out reviews), so if a gold answer
disagrees with the fixture's own documentation, the layer-on arm loses that
point. That is a finding about the layer, not a licence to edit the gold set.

The arm is recorded on the scorecard as `semantic_layer`, so two `eval_runs`
rows that differ only in this can be told apart afterwards.

### The commented arm

`--comments` loads a second file on top of the seed —
`backend/fixtures/sales_comments.sql`, 21 table and 42 column descriptions plus
the database's own — and turns on `include_db_comments`, so the run prompt
carries them under the rules in
[catalog-metadata-plan.md](catalog-metadata-plan.md) §4. Without the flag the
fixture is byte-for-byte the one every earlier run measured, which is what makes
the two comparable at all:

```bash
python -m app.eval.runner --suite sales_v1                # uncommented arm
python -m app.eval.runner --suite sales_v1 --comments     # commented arm
```

It is an overlay rather than a second `FIXTURES` entry for one reason: a
record's `connection_fixture` is part of the frozen suite, so a second fixture
would mean editing the golden set to switch arms — the one thing §2 forbids.
The arm is recorded on the scorecard (`metrics.catalog_comments`), so two
`eval_runs` rows that differ only in this can be told apart afterwards.

**Two of the fixture's comments are deliberately untrue** — `customers.segment`
is stale (it lists a segment the seed no longer writes) and `orders.subtotal` is
flatly wrong (it claims to be the amount the customer paid, which is
`total_amount`). Real catalogs rot, and a feature measured only against perfect
documentation is not measured. `tests/eval/test_golden_set.py` asserts both are
still there, so a well-meaning cleanup cannot quietly remove the hard half of
the test.

### The templates arm

`--templates on` builds a **knowledge store out of the suite's own questions**
and lets `match` offer near misses to the generator as few-shot examples, which
is what a connection with `knowledge_examples_enabled` does in production. Off
is not merely "the previous behaviour": it renders the generate prompt
byte-identically to `PROMPT_VERSION` v8, so every number on this page still
holds for it.

```bash
python -m app.eval.runner --suite sales_v1 --templates off   # = the v8 bytes
python -m app.eval.runner --suite sales_v1 --templates on
```

The store is built from the suite rather than from a separate fixture so the two
arms differ in exactly one thing. Two exclusions make the number honest, and
both are enforced in the code that builds the store rather than in a convention:

- **A fixed fraction is held out** (`HELD_OUT_FRACTION`, two in five), by sorted
  id at a fixed stride, so the split is deterministic and reproducible from the
  suite file. Those questions are never in the store.
- **Every record is excluded from the store it is measured against**, held out
  or not. A question answered with help from its own gold SQL measures nothing.

Each record is tagged `held_out` or `taught`, so the per-tag breakdown reports
the split for free — and **`held_out` is the only row worth quoting.** The
scorecard also carries `templates`, `templates_in_store`, `held_out_ids` and
`prompt_bytes_equal_v8`, so two `eval_runs` rows that differ only in this arm
can be told apart afterwards. The gate itself is §6.1.

---

## 2. The golden set

`app/eval/suites/sales_v1.json` — 50 analytical questions, each with:

```jsonc
{
  "id": "sales-014",
  "question": "…",
  "connection_fixture": "sales_pg",
  "expected_tables": ["orders", "order_items"],  // what retrieval must surface
  "gold_sql": "SELECT …",                        // the reference answer
  "result_equivalence": "set_unordered_by_columns",
  "expected_chart_type": "bar",
  "tags": ["join", "aggregation"],
  "difficulty": "medium",
  "verification": "dual_form"
}
```

Composition: 10 easy / 32 medium / 8 hard. The heaviest tags are `join` (29),
`aggregation` (25), `count` (10), `bridge` (9), `time_window` (8) and `ratio`
(8), down to single instances of `self_join`, `yoy`, `ties` and `soft_delete`.
Answers are compared as `scalar_numeric` (13), `set_unordered_by_columns` (30),
or `ordered_rows` (7) — the record declares which, because "the same answer" is
a different question for a total than for a ranking.

`sales_v1_negative.json` — 10 inputs that must **not** produce SQL: 3 metadata
questions, 2 chitchat, 3 write requests, 2 unanswerable. The correct outcome is
a route, and executing anything at all is a failure.

The 3 metadata records are scored on `state.intent` and on nothing having run,
both unchanged — but they are no longer free. Since `describe` was added they
each cost a second, schema-bearing model call (the answer itself), where the
old METADATA branch halted inside `route` with a rendered snapshot. That call
cannot leak SQL — `describe` writes none and halts — so the containment gate is
untouched; the negative suite is simply a few thousand tokens more expensive
than the record count suggests.

### The golden set is frozen

This is the rule the whole exercise rests on, and it is written at the top of
[`suites/CHANGELOG.md`](../backend/app/eval/suites/CHANGELOG.md):

> Questions are never edited to make a score go up. Gold SQL is corrected
> **only when demonstrably wrong** — it does not answer the question the English
> asks, or it errors against the fixture — and every such correction is logged
> with the reason and the evidence.
>
> Retrieval context and prompts may be tuned freely; the gold answers may not.

An eval you are allowed to edit measures your willingness to edit it. The
changelog format demands evidence (the query and output proving the old gold was
wrong), not a justification.

### The golds are checked against something other than themselves

`gold_sql` that was written by staring at the schema is a hypothesis, not a
reference. So each record carries `verification: "dual_form"`, and
[`tests/eval/sales_v1_verify.json`](../backend/tests/eval/sales_v1_verify.json)
holds a **second, structurally different** query per record — a subtraction
where the gold used a filter, a subquery where the gold used a join.
`tests/eval/test_golden_set.py` asserts the two agree on the fixture.

That test also runs static checks with no database at all: the suites parse,
`expected_tables` names only real fixture tables *and* matches the tables the
gold SQL actually references, and the negative set never expects SQL. The live
checks **skip rather than fail** when no fixture is reachable, so `make test`
stays green on a laptop with nothing running.

---

## 3. Scoring

`metrics.py` is pure — every function is a plain transformation of
already-collected results, unit-tested without a database or a model. Metrics
are listed in the order of importance the design fixed:

1. **Execution accuracy** — the headline. Did the candidate's result set match
   the gold's, under the record's declared equivalence?
2. **Retrieval recall @ k** and **full-hit rate** — did `retrieve` surface every
   expected table? This is the diagnostic that decides *what to fix*: a wrong
   answer with full recall is an interpretation problem, not a retrieval one.
   **Read §1's note before quoting either**: on the current fixture and ceiling
   they are pinned at 1.0 and measure nothing.
3. **Rates** — parse, validation pass, execution success, and
   **policy violations by rule**, plus the subset raised on a *repair* attempt
   rather than a first draft (a repair prompt carries the feedback and the
   schema but none of the mandatory rules, so that is where a regression there
   would surface).
4. **Repair distribution** — succeeded on attempt 1 vs 2 vs 3 vs failed.
5. **Latency p50/p95** split by llm / validate / db, tokens per question, and
   cost per question and per model.

Plus a per-tag breakdown, so "joins got worse" is visible rather than averaged
away.

Every record lands in exactly one outcome, most-desirable first: `MATCH` (the
only success), `MISMATCH` (ran, differed), `EXEC_FAILED` (valid SQL the database
still rejected), `VALIDATION_FAILED` (the guard refused every attempt), `NO_SQL`
(routed away from SQL), `ERROR` (a crash).

**`exact_match` is computed and never gated.** Two correct queries can be
textually unrelated; string equality measures conformity to one author's style.
It stays in the report as a diagnostic only.

### The tolerance is deliberate, not slack

Numeric comparison uses a relative tolerance of `1e-6` and an **absolute
tolerance of half a cent** (`5e-3`). That second number is not a fudge factor:
the golds report figures with `round(x, 2)`, so anything within half a cent of a
gold *is the same answer at the precision the gold states*. Without it, a
correct `AVG(x)` returning `957.416` scores wrong against a gold
`round(sum/count, 2)` of `957.42` — a presentation gap recorded as an error.

---

## 4. Running it

The harness needs the **app database** (to read an `llm_configs` row), the
**`SECRET_BOX_KEY`** (to decrypt that row's API key), and Docker (for the
fixture container). It calls a real provider, so **it costs real money.** It is
not part of `make test`.

```bash
cd backend

# Full suite. --llm-config defaults to $EVAL_LLM_CONFIG_ID, or to the sole
# config if the app DB has exactly one.
python -m app.eval.runner --suite sales_v1 --llm-config <uuid>

python -m app.eval.runner --suite sales_v1 --limit 5     # smoke test
python -m app.eval.runner --suite sales_v1 --tag join    # one slice
python -m app.eval.runner --suite sales_v1_negative      # the routing set
python -m app.eval.runner --suite sales_v1 --json        # machine-readable
python -m app.eval.runner --suite sales_v1 --comments    # with catalog comments
python -m app.eval.runner --suite sales_v1 --semantic on # with the semantic layer
python -m app.eval.runner --suite sales_v1 --retrieve-budget 12000   # recall can miss
```

**Every arm is off by default**, which is what keeps a bare `--suite sales_v1`
the run every earlier number was measured on. Three of them are recorded on the
scorecard — `catalog_comments`, `semantic_layer`, `retrieve_budget_chars` — so a
report can be attributed afterwards rather than remembered.

**Behind a rate-limiting provider, use the wrapper instead:**

```bash
scripts/eval_run.sh --suite sales_v1 --llm-config <uuid>
```

It widens only the transient-failure knobs the gateway already honours — 9
retries, 3s base, 90s ceiling, 120s request timeout — and, critically, raises
`RUN_DEADLINE_SECONDS` to 600 to match. Widening the backoff without moving the
deadline achieves nothing: `pipeline.run` aborts at `deadline_at` regardless of
how patient the gateway is willing to be. Every value is overridable, so
`LLM_MAX_RETRIES=4` gets the stock policy back.

This matters for the integrity of the number: an exhausted retry is scored
`OUTCOME_ERROR`, which is **indistinguishable in the report from the model
getting the question wrong**. Left unhandled, a rate limit reads as a worse
model.

Each run persists a scorecard to `eval_runs` / `eval_results` and prints the
`eval_run` UUID, so any figure can be traced back to its rows.

### Driving it in a test

`run_suite` and `evaluate_record` take their gateway and connector as arguments.
`tests/eval/test_runner.py` drives them with a scripted fake gateway against a
real fixture — deterministic, free, and still the real pipeline.

---

## 5. The CI gate

[`.github/workflows/eval-nightly.yml`](../.github/workflows/eval-nightly.yml)
runs at 07:00 UTC and on demand. It boots a throwaway app DB, seeds an
`llm_configs` row from `EVAL_API_KEY` via `backend/scripts/eval_seed_llm_config.py`, and
runs:

```bash
python -m app.eval.runner --suite sales_v1 \
  --fail-under 0.65 \
  --require-zero-policy-violations \
  --baseline-file app/eval/suites/sales_v1.baseline.json \
  --max-regression 0.02
```

Three independent gates: an absolute accuracy floor, a **hard** zero-tolerance
gate on policy violations, and a relative regression gate against the committed
baseline. Then the negative suite, which must route correctly and execute
nothing.

Without an `EVAL_API_KEY` secret the job **no-ops** rather than failing, so forks
and fork PRs never break for lack of a key.

### The baseline file is model-specific — read its `_README`

[`suites/sales_v1.baseline.json`](../backend/app/eval/suites/sales_v1.baseline.json)
currently records `execution_accuracy: 0.36`, measured 2026-07-26 on DeepSeek V4
Pro at temperature 0.2 under `PROMPT_VERSION` v2, with companion metrics
(retrieval recall 0.864, full-hit 0.74, parse rate 1.0, policy violation rate
0.10).

**That number is meaningless against a different model or different settings.**
The workflow defaults `EVAL_MODEL` to `gpt-4o-mini`, which is not what the
baseline was measured on. If you point the nightly at a different model,
regenerate this file from the first green run and commit it — and prefer
temperature 0 in CI, so run-to-run noise does not false-trip a two-point gate.

---

## 6. Reading a result honestly

`app/eval/reports/` holds write-ups of past runs, and they are worth reading as
examples of the standard: each one records the commit, the `PROMPT_VERSION`, the
`eval_run` UUID, the exact model and temperature (confirmed against the run's
persisted `model_snapshot`, not assumed), and proof the golden set was unchanged
(`git diff` before and after).

The 2026-07-31 report is the one to read first, because it opens by throwing out
its own headline: the runner printed 20.0%, but the provider account ran out of
credits at question 37, so fourteen questions never reached the model and were
scored `ERROR` into the same denominator. The report says the printed figure
"should not be quoted anywhere" and recomputes over the 36 questions that
actually ran.

That is the habit this doc is asking for. The harness produces a number; whether
the number *means* anything is still a judgement, and it belongs in the write-up
rather than in the tool output.

### The Phase 0 baselines — the ruler, before anything is measured with it

[learning-loop-plan.md §3.1](learning-loop-plan.md#31-phase-0--fix-the-ruler)
blocks its Phase 5 on three numbers being on paper here. The instruments exist
as of 2026-08-31; **the three runs have not been made** — each calls a real
provider and needs an `llm_configs` row with a working key. Fill this table from
the runs, not from memory, and record the `eval_run` UUID beside each.

| # | Arm | Command | Execution accuracy | Retrieval recall | `eval_run` |
|---|---|---|---|---|---|
| 1 | v8, layer **off** | `--suite sales_v1` | *not yet run* | 1.0 by construction | — |
| 2 | v8, layer **on** | `--suite sales_v1 --semantic on` | *not yet run* | 1.0 by construction | — |
| 3 | recall at a budget that can miss | `--suite sales_v1 --retrieve-budget 12000` | *not yet run* | *not yet run* | — |

Three rules for whoever runs them:

- **Same model, same temperature, all three**, and name it in the row. Two
  numbers from different models do not belong in one sentence (§5).
- **Rows 1 and 2 differ in one thing.** No `--comments`, no budget flag: the
  layer is the only variable, which is the whole point of the pair.
- **Row 3's accuracy is not comparable to rows 1 and 2.** A question whose
  tables retrieval no longer selects is a question the model cannot answer; the
  number that matters in that row is recall, and the accuracy beside it is
  context for it.

Until row 3 exists, no claim that a change improved retrieval is falsifiable on
this suite, and the plan's Phase 5 (few-shot injection) is not allowed to start.

### 6.1 The few-shot gate — the one arm that can fail

[learning-loop-plan.md §3.6](learning-loop-plan.md#36-phase-5--few-shot-injection-behind-an-eval-gate)
ships few-shot injection **only if it earns it**, and it is the only phase of
that plan with a rule written so it can fail:

> Ship few-shot injection only if execution accuracy on **held-out** questions
> is not worse than the Phase 0 baseline, at the same retrieval budget, on the
> same suite. Report both numbers — with templates and without — and report the
> split between questions that matched a template and questions that did not.

The arm exists as of 2026-09-01 (`--templates on|off`, beside `--comments` and
`--semantic`). **The runs have not been made**, for the same reason the three
Phase 0 baselines have not: each calls a real provider and needs an
`llm_configs` row with a working key, and there is none in this environment.

**So the feature ships off.** `connections.knowledge_examples_enabled` defaults
to `false`, and off renders the generate prompt byte-identically to
`PROMPT_VERSION` v8 — the prompt every number on this page was measured on. The
switch, the arm and the reporting are built; the default flips when this table
has numbers in it and they say it should.

| # | Arm | Command | Execution accuracy (all) | …on `held_out` | …on `taught` | `eval_run` |
|---|---|---|---|---|---|---|
| 4 | v9, templates **off** (= v8 bytes) | `--suite sales_v1 --templates off` | *not yet run* | n/a | n/a | — |
| 5 | v9, templates **on** | `--suite sales_v1 --templates on` | *not yet run* | *not yet run* | *not yet run* | — |

Four rules for whoever runs them, and the first two are the ones that decide
whether the pair means anything:

- **Only the `held_out` column is the gate.** The arm builds its store out of
  the suite's own questions, holds out a fixed fraction
  (`runner.HELD_OUT_FRACTION`, two in five, split deterministically by sorted
  id so it is reproducible from the suite file), and additionally excludes
  every record's *own* row from the store it is measured against. A question
  answered with help from its own stored SQL measures the store's ability to
  hold a string — [§1.3](learning-loop-plan.md#13-the-three-roles)'s
  measurement trap, which is why `role` is a column in the product.
- **Read `6. templates:` on the scorecard before reading the accuracy.** It
  prints what fraction of questions were actually shown an example, how many
  each got, and the short-circuit rate. An arm where nothing matched is
  measuring the same prompt as the off arm, and a run where questions were
  *answered* from the store is not measuring the prompt at all.
- **Row 4 against row 1, not against row 5 alone.** Row 4 renders the v8 bytes,
  so it is also a check that the v9 slot really collapses: a row 4 that differs
  from the Phase 0 layer-off baseline on the same model means the slot left
  something behind, and nothing below it can be trusted until that is fixed.
- **A negative delta is a result, not a bug to tune away.** Publish it here and
  leave the default off. This prompt has surprised us before — a "getting the
  answer right" block of general SQL guidance took execution accuracy from 36%
  to 26% on a small model by crowding out the schema, which is exactly the
  shape of change few-shot examples are. That is why the examples block is
  **last** in the prompt, capped at a fifth of what catalog comments get, and
  limited to four examples.

### What the eval has actually settled

The semantic layer exists because of a result here, not because it seemed like a
good idea: FK-neighbour retrieval expansion lifted recall from **70% to 86%**
with **flat** execution accuracy. Recall was no longer the bottleneck, and the
residual failures were interpretation — rolling-vs-calendar windows,
long-vs-wide result shapes. That is precisely the class the semantic layer
addresses, and `connections.semantic_layer_enabled` exists so it can be A/B'd on
this suite without deleting a layer someone spent time on.

**Catalog comments did not move execution accuracy** (2026-08-14, DeepSeek V4
Flash): **40.0% without, 36.0% with**, over 50 questions each — two questions in
a run that flipped twelve, so the honest reading is *no measurable effect at this
sample size*, and the result is not comparable to the V4 Pro baseline. Recall was
**1.000 in both arms**, which was predicted rather than discovered: `retrieve`
selects on names and never reads a comment, and this fixture no longer clears the
budget (§1). Two things the run did establish: neither of the fixture's
deliberately false comments was ever believed, and parse, guard-pass,
execution-success and policy-violation rates all improved. Both are in
[`reports/sales_v1_catalog_comments_2026-08-14.md`](../backend/app/eval/reports/sales_v1_catalog_comments_2026-08-14.md),
which also throws out two of its own apparent gains after checking them — worth
reading as an example of the standard: an A/B whose author wanted a win, did not
get one, and said so.

---

## 7. Adding to the suite

- **A new question:** append a record with a `gold_sql` you have run against the
  fixture, add its structurally-different twin to
  `tests/eval/sales_v1_verify.json`, and let `test_golden_set.py` prove they
  agree. `expected_tables` must match the tables the gold SQL actually
  references — the static test checks this.
- **A new fixture / dialect:** add a `FixtureSpec` to `dataset.FIXTURES` with its
  image and seed path. The dialect mirrors already exist
  (`sales_seed_mysql.sql`, `sales_seed_mssql.sql`), so a MySQL or SQL Server
  suite is a registry entry plus records, not new machinery. A `comments_path`
  is optional and is what `--comments` loads.
- **Correcting a gold:** only on demonstrable defect, and the entry in
  `suites/CHANGELOG.md` is not optional.
- **Never** edit a question because a model keeps getting it wrong. That is the
  measurement working.
