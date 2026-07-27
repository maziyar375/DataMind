# Eval report — `sales_v1` on DeepSeek V4 Pro (retry narrowed to `C_EMPTY_RESULT`)

- **Date:** 2026-07-27 (second run of the day)
- **Model:** `openai/lightning-ai/deepseek-v4-pro` (Lightning OpenAI-compatible
  endpoint), temperature 0.2, `max_tokens` 2048 — llm_config
  `be57faec-3c20-4316-a6e6-b5bb2773e13c`
- **Suite:** `sales_v1` — 50 questions, golden set unchanged
- **Code under test:** commit `da1524a` **plus the uncommitted working-tree
  change** to `pipeline/checks.py` + `pipeline/nodes/__init__.py`
  (+`tests/unit/test_checks.py`, 120 insertions) — see *Provenance* below
- **eval_run:** `4bebb5ed-1dce-4fd7-a8f4-2399b7605332` (14m 31s wall)
- **Compares against:** [`sales_v1_deepseek_2026-07-27.md`](sales_v1_deepseek_2026-07-27.md)
  (run `c84f08dd…`, 30.0%) and [`sales_v1_deepseek_2026-07-26.md`](sales_v1_deepseek_2026-07-26.md)
  (run `77786471…`, 36.0%)

Same model, same temperature, same frozen golden set. The only variable is the
retry change, so this is a controlled test of the fix the previous report
prescribed.

---

## Headline

| | 07-26 (v2) | 07-27a (v3) | **07-27b (v3 + retry fix)** |
|---|:---:|:---:|:---:|
| **Execution accuracy (measured)** | **36.0%** | **30.0%** | **32.0%** |
| MATCH / MISMATCH / ERROR / VALIDATION_FAILED | 18 / 30 / 0 / 2 | 15 / 30 / 3 / 2 | 16 / 28 / **4** / 2 |
| Rate-limit ERRORs on questions that had been MATCH | 0 | 1 | **2** |
| **Accuracy with those infra losses added back** | 36.0% | 32.0% | **36.0%** |
| Retrieval recall mean / full-hit | 86.4% / 74% | 86.4% / 74% | 86.4% / 74% |
| Policy-violation rate | 10% | 10% | 8% |
| **Succeeded on attempt 2** | 3 | **17** | **2** |

**The fix did what it was predicted to do.** All four questions the check-driven
retry had corrupted are correct again, each on attempt 1:

| id | 07-26 (v2) | 07-27a (v3) | 07-27b | attempt |
|---|---|---|---|:--:|
| sales-014 | MATCH | MISMATCH | **MATCH** | 1 |
| sales-028 | MATCH | MISMATCH | **MATCH** | 1 |
| sales-042 | MATCH | MISMATCH | **MATCH** | 1 |
| sales-045 | MATCH | MISMATCH | **MATCH** | 1 |

Attempt-2 completions fell 17 → 2, and the two that remain
(sales-025, sales-034) are **guard repairs**, not check retries — the `inspect`
node no longer spends a regeneration on a query that ran. That is the mechanism
change, visible directly in the attempt numbers rather than inferred.

## Why the measured number is 32% and not 36%

Four questions ERRORed, and **all four are `litellm.RateLimitError` after the
gateway exhausted its four backoff retries** — Lightning's free tier throttled
hard during this window (the log also shows non-scoring rate-limit failures in
the `chart` and `present` nodes, which fail open).

| id | this run | 07-27a | 07-26 | costs a point? |
|---|---|---|---|---|
| sales-003 | ERROR | MATCH | MATCH | **yes** |
| sales-037 | ERROR | MATCH | MATCH | **yes** |
| sales-015 | ERROR | MISMATCH | MISMATCH | no |
| sales-020 | ERROR | MISMATCH | MISMATCH | no |

Adding back only the two questions that were MATCH in **both** prior runs gives
18/50 = **36.0%** — level with the v2 baseline. That is an adjustment with a
stated basis, not a measurement; **the only measured number here is 32.0%.**

Composition at 18 matches is near-identical to the 07-26 baseline: the adjusted
set differs from it by exactly one question in each direction (gains sales-019,
loses sales-013).

## Like-for-like against the run this fix targets (07-27a, 30%)

Six questions gained, five lost:

- **Gained by the fix (4):** sales-014, -028, -042, -045 — the retry casualties.
- **Gained by luck (2):** sales-010, sales-019 — rate-limit ERRORs last time.
- **Lost to rate limits (2):** sales-003, sales-037.
- **Lost genuinely (3):** sales-013 (MATCH in both prior runs → MISMATCH on
  attempt 1), sales-038 and sales-049 (each has now been MATCH once and not-MATCH
  twice across three runs).

Those three losses all happen on **attempt 1**, so no retry is involved; they
are the ±2-4 pt temperature-0.2 flip noise this suite has shown from the start.
Netting the mechanism from the noise: the retry fix is worth **+4 questions**,
and nothing in this run is evidence against it.

## Correction to the previous report

07-27a reported "parse rate fell 100% → 94%" and suggested the longer v3 schema
block might be to blame. **That reading was wrong.** `parse_ok` is `false` on
exactly the ERROR rows and nowhere else, in every run:

| run | parse rate | rows with `parse_ok = false` |
|---|---|---|
| 07-27a | 94% | sales-010, -019, -031 — all three `RateLimitError` |
| 07-27b | 92% | sales-003, -015, -020, -037 — all four `RateLimitError` |

So "parse rate" on these runs is measuring **whether the provider answered at
all**, not whether the model's JSON was well-formed. sales-031, which 07-27a
called "a genuine parse failure", shows four `llm_retry … RateLimitError`
warnings before it failed. No evidence exists that the v3 prompt hurt parsing.
The same accounting explains `validation_pass_rate` 88% and
`execution_success_rate` 88% here: 4 ERROR + 2 VALIDATION_FAILED.

Second, smaller correction: 07-27a's headline table lists the 07-26 outcomes as
`18 / 28 / 2 / 2`. The persisted run (`77786471…`) records
`MATCH 18 / MISMATCH 30 / ERROR 0 / VALIDATION_FAILED 2`. Every 07-26 column in
this report is read from the database rather than from the earlier report.

## Baseline file: still not updated

`suites/sales_v1.baseline.json` stays at **36.0%**. The previous report set the
rule in advance — *re-baseline only after the retry fix is measured, and if that
run lands within noise of 36%, keep 36%* — and this run lands there on the
adjusted figure and 4 points under on the measured one. Writing 32% in would
loosen the gate using a number two rate-limit failures produced.

Nightly CI consequence: at `--max-regression 0.02` the **measured** 32% would
still fail the gate against 36%. On this evidence that is an infrastructure
false-trip, not a code regression — which is an argument for running the gate at
temperature 0 on a provider that does not throttle, as the baseline file's own
`_README` already says.

## Provenance caveats (read before quoting the number)

- **The measured code is not any single commit.** `eval_runs.git_sha` records
  `da1524a`, but the working tree carried the uncommitted retry change; the sha
  alone does not identify what ran. The diff is `checks.py`
  (`C_NULLABLE_INNER_JOIN` → advisory, retry surface = `C_EMPTY_RESULT` only),
  `nodes/__init__.py` (`generate` quotes back only `retry=True` findings), and
  23 passing unit tests including one that pins the whole retry surface.
- **`prompt_version` in the DB says `v2` and is wrong.** It comes from
  `settings.prompt_version`, hardcoded `"v2"` in `app/core/config.py:70`, while
  the actual prompt module is `PROMPT_VERSION = "v3"`
  (`app/pipeline/prompts/__init__.py:6`). Every run since the v3 bump is
  mislabelled in `eval_runs`. Not fixed here — changing code mid-measurement
  would defeat the point of a controlled run.
- Temperature 0.2 gives ±2-4 pt run-to-run noise on this suite; three separate
  questions flipped state between runs with no code reason.

## Secondary metrics

```
parse 92%  guard-pass 88%  exec-success 88%  policy-violation 8%
  violations by rule: E_UNKNOWN_COLUMN=2  E_TABLE_NOT_ALLOWED=1
                      E_UNKNOWN_ALIAS=1   E_NODE_NOT_ALLOWED=1
repair distribution: attempt_1=42  attempt_2=2  failed=6
latency p50/p95 ms   llm 1712/20310   validate 2/5   db 3/11   total 3636/55921
tokens/q  prompt 89  completion 4        exact_match (diagnostic) 0.0%
```

Retrieval is byte-identical to both prior runs (86.4% mean, 74% full-hit),
confirming nothing upstream of `generate` moved.

## Per-tag

| tag | 07-27b | 07-27a | 07-26 | recall | n |
|---|---:|---:|---:|---:|---:|
| self_join | 0% | 0% | 0% | 100% | 1 |
| time_window | 0% | 0% | 0% | 100% | 8 |
| yoy | 0% | 0% | 0% | 100% | 1 |
| ties | 0% | 0% | 0% | 100% | 1 |
| cancelled | 0% | 33% | 0% | 100% | 3 |
| share | 0% | 50% | 0% | 100% | 2 |
| ranking | 20% | 0% | 20% | 73% | 5 |
| bridge | 22% | 0% | 11% | 69% | 9 |
| ratio | 25% | 38% | 38% | 100% | 8 |
| top_n | 25% | 0% | 25% | 75% | 4 |
| aggregation | 28% | 28% | 28% | 84% | 25 |
| join | 31% | 21% | 31% | 77% | 29 |
| filter | 50% | 67% | 50% | 97% | 6 |
| count | 50% | 40% | 60% | 87% | 10 |
| per_unit | 50% | 50% | 50% | 100% | 2 |
| distinct | 100% | 100% | 100% | 100% | 1 |
| soft_delete | 100% | 100% | 100% | 100% | 1 |

`ranking`, `bridge`, `top_n` and `join` recover to their 07-26 levels — those
tags carried the retry casualties. At n=1-5 a single question is 20-100 points,
so read the per-question table instead.

## Per-question

🔁 = restored by the retry fix · 🌐 = rate-limit ERROR (infrastructure) ·
`att` = attempt that succeeded · `rows` = gold / candidate

| id | 07-27b | 07-27a | 07-26 | att | recall | rows | tags |
|---|---|---|---|:--:|---:|---|---|
| sales-001 | MATCH | MATCH | MATCH | 1 | 1.00 | 1/1 | filter,count |
| sales-002 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | aggregation |
| sales-003 | ERROR 🌐 | MATCH | MATCH | – | 1.00 | –/– | aggregation |
| sales-004 | MATCH | MATCH | MATCH | 1 | 1.00 | 6/6 | distinct |
| sales-005 | MATCH | MATCH | MATCH | 1 | 1.00 | 1/1 | filter,count |
| sales-006 | MATCH | MATCH | MATCH | 1 | 1.00 | 1/1 | aggregation |
| sales-007 | MATCH | MATCH | MATCH | 1 | 1.00 | 6/6 | join,aggregation |
| sales-008 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 6/6 | join,self_join,aggregation |
| sales-009 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 7/7 | join,aggregation |
| sales-010 | MATCH | ERROR | MATCH | 1 | 1.00 | 16/16 | join,count |
| sales-011 | MATCH | MATCH | MATCH | 1 | 1.00 | 8/8 | join,aggregation |
| sales-012 | MATCH | MATCH | MATCH | 1 | 1.00 | 6/6 | join,aggregation |
| sales-013 | MISMATCH | MATCH | MATCH | 1 | 1.00 | 24/24 | join,count |
| sales-014 | MATCH 🔁 | MISMATCH | MATCH | 1 | 1.00 | 4/4 | join,count |
| sales-015 | ERROR 🌐 | MISMATCH | MISMATCH | – | 0.00 | –/– | join,count |
| sales-016 | MATCH | MATCH | MATCH | 1 | 1.00 | 10/10 | join,count |
| sales-017 | MISMATCH | MISMATCH | MISMATCH | 1 | 0.67 | 8/6 | join,aggregation,bridge |
| sales-018 | VALIDATION_FAILED | VALIDATION_FAILED | VALIDATION_FAILED | – | 0.67 | –/– | join,bridge,aggregation |
| sales-019 | MATCH | ERROR | MISMATCH | 1 | 1.00 | 20/20 | join,bridge,aggregation |
| sales-020 | ERROR 🌐 | MISMATCH | MISMATCH | – | 0.00 | –/– | join,bridge,aggregation |
| sales-021 | MISMATCH | MISMATCH | MISMATCH | 1 | 0.75 | 8/8 | join,bridge,aggregation |
| sales-022 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 16/16 | join,aggregation |
| sales-023 | MISMATCH | MISMATCH | MISMATCH | 1 | 0.80 | 3/3 | join,bridge,filter,aggregation |
| sales-024 | MISMATCH | MISMATCH | MISMATCH | 1 | 0.67 | 7/8 | join,bridge,count |
| sales-025 | MISMATCH | MISMATCH | MISMATCH | 2 | 0.33 | 6/6 | join,aggregation |
| sales-026 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 2/2 | join,bridge,aggregation |
| sales-027 | VALIDATION_FAILED | VALIDATION_FAILED | MISMATCH | – | 0.00 | –/– | join,aggregation |
| sales-028 | MATCH 🔁 | MISMATCH | MATCH | 1 | 0.67 | 16/16 | join,bridge,aggregation |
| sales-029 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | time_window,aggregation |
| sales-030 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 7/7 | time_window,aggregation |
| sales-031 | MISMATCH | ERROR | MISMATCH | 1 | 1.00 | 2/1 | time_window,yoy,aggregation |
| sales-032 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 2/1 | time_window,aggregation |
| sales-033 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 13/13 | time_window,count |
| sales-034 | MISMATCH | MISMATCH | MISMATCH | 2 | 1.00 | 1/1 | time_window,ratio |
| sales-035 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 5/4 | time_window,count |
| sales-036 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | time_window,ratio,cancelled |
| sales-037 | ERROR 🌐 | MATCH | MATCH | – | 1.00 | –/– | ratio |
| sales-038 | MISMATCH | MATCH | MISMATCH | 1 | 1.00 | 1/1 | ratio,share |
| sales-039 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | per_unit,ratio |
| sales-040 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 6/6 | share,ratio,join |
| sales-041 | MATCH | MATCH | MATCH | 1 | 1.00 | 1/1 | ratio,per_unit |
| sales-042 | MATCH 🔁 | MISMATCH | MATCH | 1 | 1.00 | 1/1 | ratio |
| sales-043 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 5/5 | ranking,top_n,join |
| sales-044 | MISMATCH | MISMATCH | MISMATCH | 1 | 0.50 | 10/10 | ranking,top_n,join |
| sales-045 | MATCH 🔁 | MISMATCH | MATCH | 1 | 0.50 | 3/3 | ranking,top_n,join |
| sales-046 | MISMATCH | MISMATCH | MISMATCH | 1 | 0.67 | 8/6 | ranking,join |
| sales-047 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 120/5 | ranking,top_n,ties,join |
| sales-048 | MATCH | MATCH | MATCH | 1 | 1.00 | 3/3 | soft_delete,filter,aggregation |
| sales-049 | MISMATCH | MATCH | VALIDATION_FAILED | 1 | 1.00 | 8/8 | filter,cancelled,aggregation,join |
| sales-050 | MISMATCH | MISMATCH | MISMATCH | 1 | 1.00 | 2/1 | filter,cancelled |

## What is left

The residue is unchanged from 07-26 and is not a service bug: `time_window`
(0/8) and `yoy` turn on rolling-vs-calendar window interpretation, and
sales-047 (`ties`, 120 gold rows vs 5 returned) is the documented
`FETCH FIRST … WITH TIES` guard gap. Two questions still fail the guard outright
(sales-018 `E_UNKNOWN_COLUMN`, sales-027 `E_TABLE_NOT_ALLOWED` +
`E_UNKNOWN_ALIAS`) — those are worth a look, being the only remaining failures
the pipeline itself causes.

**≥65% remains out of reach on this model+suite**, as every run since 07-26 has
said. The value of this run is the mechanism confirmation, not the headline.

## Reproduce

```bash
cd backend && set -a && source ../.env && set +a
python -m app.eval.runner --suite sales_v1 \
  --llm-config be57faec-3c20-4316-a6e6-b5bb2773e13c
```

*Source: persisted `eval_runs` / `eval_results`, run
`4bebb5ed-1dce-4fd7-a8f4-2399b7605332`. Golden set unchanged; no gold SQL was
edited for this run.*
