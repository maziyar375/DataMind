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
path builds, then walks every question through `route → retrieve → generate →
validate → execute → …`. A guard rule that would reject a query in production
rejects it here, and it costs the run a point.

The target database is a **fresh container** spun from a fixture seed via
testcontainers — never a stored connection, never a database someone has been
poking at. `dataset.FIXTURES` maps a record's `connection_fixture` name onto the
seed to load:

| Fixture | Dialect | Seed |
| --- | --- | --- |
| `sales_pg` | postgres | `backend/fixtures/sales_seed.sql` (the 42-table commerce schema) |

The schema is deliberately wide enough that the snapshot **exceeds the retrieve
node's budget** — so retrieval is genuinely exercised rather than trivially
correct.

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
```

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

### What the eval has actually settled

The semantic layer exists because of a result here, not because it seemed like a
good idea: FK-neighbour retrieval expansion lifted recall from **70% to 86%**
with **flat** execution accuracy. Recall was no longer the bottleneck, and the
residual failures were interpretation — rolling-vs-calendar windows,
long-vs-wide result shapes. That is precisely the class the semantic layer
addresses, and `connections.semantic_layer_enabled` exists so it can be A/B'd on
this suite without deleting a layer someone spent time on.

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
  suite is a registry entry plus records, not new machinery.
- **Correcting a gold:** only on demonstrable defect, and the entry in
  `suites/CHANGELOG.md` is not optional.
- **Never** edit a question because a model keeps getting it wrong. That is the
  measurement working.
