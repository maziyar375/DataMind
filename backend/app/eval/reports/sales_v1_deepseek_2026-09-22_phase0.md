# Eval report — the three Phase 0 baselines, at `PROMPT_VERSION` v10

- **Date:** 2026-09-22
- **Model:** `openai/deepseek/deepseek-v4-pro` via OpenRouter
  (`https://openrouter.ai/api/v1`), **temperature 0.0**, **`max_tokens` 8192** —
  llm_config `2e896025-6077-40dd-87f5-672d52341ccd`. All four values read back
  from each run's persisted `model_snapshot`, not assumed; **identical on all
  three arms.**
- **Suite:** `sales_v1` — 50 questions. **Golden set unchanged**:
  `sha256(sales_v1.json)` is `3f4a9091…f692` at every commit involved and in the
  working tree, and `git log ddfb777..HEAD -- backend/app/eval/suites/sales_v1.json`
  is empty.
- **Code under test:** `PROMPT_VERSION` **v10** on all three arms, confirmed from
  the `eval_runs` rows. Arm 1 ran commit `ddfb777`; arms 2 and 3 ran `d4f6b72`.
  (`eval_runs.git_sha` says `72d3aa1` for arm 1 and is **wrong** — see
  *The instrument recorded the wrong commit*, below.)
- **Wall clock:** 17:19:11 → 19:32:47 UTC, 2h 13m for the three arms back to back.

These are the three baselines that
[learning-loop.md §13.2](../../../../docs/plans/learning-loop.md#132-phase-0--fix-the-ruler)
and
[deep-analysis-mode.md §3.1](../../../../docs/plans/deep-analysis-mode.md#31-phase-0--the-gate--size-s--blocking)
have both been blocked on since 2026-08-31. The instruments were built then; no
provider key existed in that environment, so the cells stayed empty. They are
filled now.

---

## The three numbers

| # | Arm | Execution accuracy | Retrieval recall (mean / full-hit) | `definition_use.used_rate` | `eval_run` |
|---|---|:---:|:---:|:---:|---|
| 1 | v10, layer **off** | **42.0 %** (21/50) | 100 % / 100 % | 0.1885 (122 in scope) | `d8c1035d-a03b-4526-8345-dfb82bc7fde9` |
| 2 | v10, layer **on** | **42.0 %** (21/50) | 100 % / 100 % | 0.2441 (127 in scope) | `5df63738-43a9-4b45-8e6d-b0b68e7ba9b6` |
| 3 | recall at `--retrieve-budget 8000` | 30.0 % (15/50) | **80.2 % / 62.0 %** | 0.1417 | `dc2ea4fd-9164-4524-9b51-bc74d992c8ff` |

Row 3's accuracy is **not** comparable to rows 1 and 2, by eval.md §6's own
rule: a question whose tables retrieval no longer selects is a question the
model cannot answer. It is context for the recall figure beside it, and nothing
else.

### The gate verdict, stated against the rule as written

[deep-analysis-mode.md §0.3](../../../../docs/plans/deep-analysis-mode.md#03-the-gate-stated-as-a-rule-before-it-can-be-argued-with)
wrote its thresholds down before this number existed. The number the rule names
is **arm 2 — execution accuracy at v10 with the semantic layer on — and it is
0.42**, which falls in the middle band:

> **0.36 – 0.55** — the gate opens for Phases 1–3 only. Phases 4–9 wait on
> [mvp2](../../../../docs/plans/mvp2.md) A1/A5/B2 moving the number, and this
> plan is re-read, not re-argued.

So: **Phases 1–3 are clear** (all three have landed). **Phases 4–9 do not
start.** The margin is not narrow enough to argue about — 0.42 is 13 points
under the opening threshold and 6 points over the closing one — and the honest
reading is the one the rule already wrote: the generator is right on fewer than
half of these questions, and a mode that asks it eight questions instead of one
inherits that error rate eight times over.

---

## The semantic layer moved fourteen questions and changed the score by nothing

This is the finding of the run, and it is invisible in the headline.

Arms 1 and 2 differ in exactly one thing — the layer — and both scored 42.0 %
with 21 MATCH. **They are not the same 21.** Fourteen questions changed verdict,
seven in each direction:

| id | layer off | layer on | tags |
|---|---|---|---|
| sales-005 | MISMATCH | **MATCH** | filter, count |
| sales-016 | MISMATCH | **MATCH** | join, count |
| sales-018 | MISMATCH | **MATCH** | join, bridge, aggregation |
| sales-021 | MISMATCH | **MATCH** | join, bridge, aggregation |
| sales-040 | MISMATCH | **MATCH** | share, ratio, join |
| sales-041 | MISMATCH | **MATCH** | ratio, per_unit |
| sales-047 | MISMATCH | **MATCH** | ranking, top_n, ties, join |
| sales-008 | MATCH | **MISMATCH** | join, self_join, aggregation |
| sales-013 | MATCH | **MISMATCH** | join, count |
| sales-014 | MATCH | **MISMATCH** | join, count |
| sales-028 | MATCH | **MISMATCH** | join, bridge, aggregation |
| sales-038 | MATCH | **MISMATCH** | ratio, share |
| sales-045 | MATCH | **MISMATCH** | ranking, top_n, join |
| sales-046 | MATCH | **MISMATCH** | ranking, join |

28 % of the suite changed answer and the aggregate did not move. Two things
follow, and both matter more than the 42 %.

**A 50-question suite cannot resolve a difference this size.** The binomial
standard error on 50 questions at p ≈ 0.42 is about 7 points, so anything under
roughly ±14 points is noise on this instrument. That is not an argument for
ignoring the layer; it is an argument that **`sales_v1` is too small to measure
a layer with**, and any future claim that some change moved accuracy by five
points on this suite is unfalsifiable. Written down here so it is not
rediscovered.

**The layer is doing something real, and it is not accuracy.** Two signals say
so where the headline cannot:

- **Definition use rose from 0.1885 to 0.2441** on the same suite and model — the
  difference of **+0.0556** that
  [semantic-layer-model.md §4.4](../../../../docs/plans/semantic-layer-model.md)
  asks for, and the reason one arm's rate alone is meaningless. Statements are
  measurably more likely to match a defined metric with the layer in the prompt.
- **Policy violations went from 4.0 % to 0.0 %, and repairs from three to none.**
  Arm 1 needed a second attempt on three questions and tripped
  `E_SUBQUERY_TOO_DEEP` and `E_NODE_NOT_ALLOWED` once each. Arm 2 produced
  fifty first-attempt statements that the guard accepted outright.

The layer buys better-formed, more conventional SQL that is wrong about
different things. On a suite this size that reads as zero.

---

## Row 3: what retrieval misses cost, and what recall@k does not measure

At `--retrieve-budget 8000` (against the shipped 50,000) the schema block stops
fitting and retrieval starts choosing. Recall fell to 80.2 % mean / 62.0 %
full-hit, and accuracy to 30.0 %. Nineteen questions took a retrieval miss;
`bridge` (62.2 % recall) and `join` (72.8 %) took the worst of it, which is the
expected shape — a bridge table is the thing a budget drops first.

**Two questions scored recall 0.000 and still answered correctly**, and they are
worth more than the aggregate:

| id | question | `expected_tables` | what the model actually wrote |
|---|---|---|---|
| sales-003 | *What does the average person on our staff earn?* | `employees` | `SELECT AVG(e.salary) FROM public.employees AS e` |
| sales-004 | *What kinds of products do we stock?* | `categories` | `SELECT DISTINCT category FROM public.products` |

Neither table was in the block the model was shown. sales-003 the model
**guessed** — `staff` → `employees`, and a `salary` column — and was right;
sales-004 it routed around the annotation entirely, answering from
`products.category` instead of the `categories` table the gold expects. Both
executed because **the guard's allowlist is the whole snapshot, never the
retrieved subset** (plan D2), which is working as designed.

So: **recall@k on this suite measures whether retrieval selected the tables the
annotator expected — not whether the model could answer.** It is a retrieval
diagnostic, not an upper bound on accuracy, and the 80.2 % should never be
quoted as "20 % of questions became unanswerable".

### One ERROR, and it is a ceiling not an outage

`sales-026` scored ERROR: the model's `SqlProposal` was **cut off at
`max_tokens` 8192** and came back unparseable after a 183-second call that
burned all 8192 completion tokens. Not a provider failure and not a wrong
answer — a config ceiling, dividing into the same denominator as a mistake.
Arm 3's 30.0 % is therefore 15/50; over the 49 questions that got an answer it
is 30.6 %. The difference does not change any conclusion, but the 2026-07-31
report is in this directory precisely because that distinction was once worth
fourteen questions.

---

## Comparability: the three arms ran on three trees, and it does not matter

Arm 1 ran while phases 1–3 of the deep-analysis plan were still landing. That is
a real threat to the comparison and it was checked rather than assumed:

```
$ git diff --stat ddfb777 d4f6b72 -- backend/app/pipeline backend/app/sqlguard \
      backend/app/knowledge backend/app/semantic backend/app/eval
 backend/app/pipeline/graph.py | 22 ++++++++++++++++++++++
 backend/app/pipeline/state.py | 26 +++++++++++++++++++++++++-
```

Those 47 lines are **entirely Phase 1's token accounting** — two nullable
`cache_*` fields on `NodeUsage` and `RunState`, `add_reported`, and
`_unreported_delta`. No prompt, no retrieval decision, no guard rule, no
generated statement and no execution path differs between the arms, and
`PROMPT_VERSION` is v10 on all three. Phase 2 (`app/analysis/`) ships inert and
is imported by nothing in the pipeline; Phase 3 touches `app/reports/` only.

**The three numbers are comparable with each other**, which is what §6 needs.

### The instrument recorded the wrong commit

`eval_runs.git_sha` for arm 1 says `72d3aa1`. Arm 1's process started at
17:19:23; `72d3aa1` was committed at **17:48:57**, thirty minutes into the run.
The cause is that `_git_sha()` is called inside `_persist`, i.e. *after* the run
finishes, so the column records the tree at write time rather than the code that
executed. On a two-hour suite run on a tree being worked on, that is a
measurement-integrity bug in the ruler itself, and it was caught only because
arm 1's cache-token logs disagreed with the sha it claimed.

**Fixed in this commit**: the sha is captured beside `started_at` and passed
through. Arm 1's stored row is left as it stands — rewriting a persisted
scorecard to look tidier is exactly what `suites/CHANGELOG.md` forbids — and
this report is the correction.

---

## Bonus: Phase 1's cache measurement, which these runs produced for free

[deep-analysis-mode.md §6](../../../../docs/plans/deep-analysis-mode.md#6-measurement--what-each-phase-must-produce)
owes Phase 1 "cache read/write tokens per node on a caching provider — **and the
ratio to uncached**, which is what decides whether the mode is affordable".
Arms 2 and 3 ran on the instrument and OpenRouter reports the figures:

| Arm | calls | calls reporting cache | prompt tokens on those calls | cache **read** | share of prompt served from cache |
|---|:--:|:--:|---:|---:|:--:|
| 2 — layer on, budget 50 k | 172 | 122 | 579,088 | 349,909 | **60.4 %** |
| 3 — layer off, budget 8 k | 173 | 124 | 230,261 | 73,735 | **32.0 %** |

Three things this says:

- **`cache_write_tokens` is 0 on every call.** That is the provider reporting
  reads and not writes, not an absence of caching — precisely the state the
  NULL-vs-0 rule exists to keep distinguishable, and the reason the columns are
  nullable. A `0` here is a reported zero; arm 1's blanks are *not measured*.
- **Arm 1 reported nothing at all** because the instrument did not exist in that
  process (see above), *not* because the provider was silent. Two facts, one
  empty cell — which is the whole argument for `NULL ≠ 0`.
- **The narrower prompt cached worse** (32 % vs 60 %). The cacheable prefix is
  the schema block; shrink it and the constant per-call overhead becomes a
  bigger share of a smaller prompt. A deep run re-sending a *large* schema block
  once per step is therefore the workload caching is best at, which is the
  affordability case §2.1 made in advance — now with a number under it.

What this does **not** establish is the cost ratio. That needs the provider's
cache-read price, which has not been confirmed here, and the honest statement is
the token one: **three fifths of the prompt tokens on a layer-on run were
served from cache.**

---

## What this run settles, and what it does not

**Settles.**

- The stale 0.36 at v2 is superseded: **0.42 at v10, layer on**, on the same
  suite and fixture. `sales_v1.baseline.json` still records the old figure and
  is left alone — it is a CI tripwire keyed to a model, not a claim.
- The gate verdict, on the rule as written: Phases 4–9 wait.
- learning-loop.md §13.2's last box, and eval.md §6's three empty cells.
- The semantic layer's effect on definition use: **+5.6 points**, measurable.

**Does not settle.**

- Whether the layer helps accuracy. 50 questions cannot see a difference this
  small; the answer is "not measurably, on this instrument".
- Anything about `deep_v1`. That set has no gold answers by design and nothing
  to run against it yet.
- Cost. Token shares are here; prices are not.
