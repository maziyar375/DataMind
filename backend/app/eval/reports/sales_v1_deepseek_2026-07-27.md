# Eval report — `sales_v1` on DeepSeek V4 Pro (schema hints + result checks)

- **Date:** 2026-07-27
- **Model:** `openai/lightning-ai/deepseek-v4-pro` (Lightning OpenAI-compatible
  endpoint), temperature 0.2, `max_tokens` 2048 — llm_config
  `be57faec-3c20-4316-a6e6-b5bb2773e13c`, owner `maziyar.azami.b@gmail.com`
- **Suite:** `sales_v1` — 50 questions, golden set unchanged
- **Code under test:** commit `da1524a` (`PROMPT_VERSION` v3)
- **eval_run:** `c84f08dd-34e8-479c-95cc-0789029f2e6b`
- **Compares against:** [`sales_v1_deepseek_2026-07-26.md`](sales_v1_deepseek_2026-07-26.md),
  eval_run `77786471-15b2-4f28-87e5-8d0e8255327b` (v2, 36.0%)

Same model, same temperature, same frozen golden set. The only variable is the
code, so this is a controlled A/B of commit `da1524a`.

---

## Headline: this is a regression, and the cause is identified

| | 2026-07-26 (v2) | 2026-07-27 (v3) | Δ |
|---|:---:|:---:|:---:|
| **Execution accuracy** | **36.0%** | **30.0%** | **-6.0 pt** |
| MATCH / MISMATCH / ERROR / VALIDATION_FAILED | 18 / 28 / 2 / 2 | 15 / 30 / 3 / 2 | |
| Retrieval recall (mean) | 86.4% | 86.4% | 0 |
| Retrieval full-hit | 74% | 74% | 0 |
| Parse rate | 100% | 94% | **-6 pt** |
| Policy-violation rate | 10% | 10% | 0 |
| Validation pass rate | — | 90% | |
| Succeeded on attempt 2 | **3** | **17** | **+14** |

Recall is identical to the decimal, confirming retrieval was untouched: the
budget estimate (`60 + 40*ncols`) does not count hint characters, so table
selection is unchanged and only the rendered text got longer.

**The drop is not caused by the schema hints. It is caused by the `inspect`
node's check-driven retry replacing correct answers with wrong ones.**

## The evidence

Five questions regressed, two improved:

| id | 2026-07-26 | 2026-07-27 | attempt then → now |
|---|---|---|---|
| sales-010 | MATCH | **ERROR** (rate limit) | 1 → – |
| sales-014 | MATCH | **MISMATCH** | 1 → **2** |
| sales-028 | MATCH | **MISMATCH** | 1 → **2** |
| sales-042 | MATCH | **MISMATCH** | 1 → **2** |
| sales-045 | MATCH | **MISMATCH** | 1 → **2** |
| sales-038 | MISMATCH | **MATCH** | 1 → 1 |
| sales-049 | VALIDATION_FAILED | **MATCH** | – → 1 |

All four genuine regressions share one signature: **correct on the first
attempt, wrong on the second.** Attempt-2 completions rose from 3 to 17 across
the suite, which is the `inspect` node firing. A structural check saw something
it did not like about a *correct* result, spent a regeneration, and the second
query was worse.

Both improvements happened on **attempt 1** — that is, from the schema hints,
not from the retry. sales-049 went from `VALIDATION_FAILED` to `MATCH`, the
signature of a model that previously invented a literal and now had the column's
real values in front of it.

## What this means for each half of the change

**Schema hints: neutral to mildly positive.** Two questions improved on attempt
1; none regressed on attempt 1. That is +2 on a 50-question suite, well inside
the ±4 pt noise band at temperature 0.2 — suggestive, not proven. Capture was
verified against the live fixture: 387 of 599 columns got statistics and 29 got
exact value lists (`orders.status`, `orders.channel`, `customers.segment` among
them), with the sensitive-name floor holding on every email/phone/name/city/ref
column.

**The result-check retry: harmful as shipped, -4 questions.** The safety net I
built restores the earlier result only when the retry *fails the guard or the
database*. It does nothing when the retry returns a perfectly valid result that
happens to be wrong — which is precisely what happened four times.

## The design flaw, stated plainly

`nodes.inspect` treats "a check fired" as sufficient reason to regenerate, and
treats any *runnable* second answer as an improvement. Neither holds. A
structural check is a suspicion with no ground truth; when it fires on a correct
query, regeneration is a coin flip that this suite says lands badly.

The fix is to make the retry earn its replacement rather than assume it:

1. **Re-run the checks on the retry's result and keep whichever result has
   fewer findings**, restoring the original on a tie. Today the second pass
   unconditionally accepts the new result and discards the fallback.
2. **Narrow what may trigger a retry to `C_EMPTY_RESULT` only.** An empty result
   is the one case with nothing to lose. `C_NULLABLE_INNER_JOIN` fired on
   correct queries here — a nullable FK in an inner join is often exactly what
   the question meant.

Both are small changes to `pipeline/checks.py` and `nodes.inspect`. They are
**not** applied in this report's code; `da1524a` is the state measured.

## Measurement caveats (read before acting on the number)

- **Two of the three ERRORs are infrastructure, not model.** sales-010 and
  sales-019 hit `RateLimitError` after the gateway exhausted its four retries;
  Lightning's free tier throttled mid-run. sales-010 was a MATCH on
  2026-07-26. The third ERROR (sales-031) is a genuine parse failure.
- **Adjusted estimate, clearly labelled as such:** excluding the two rate-limit
  ERRORs, accuracy is 15/48 = **31.3%**. Adding back the four retry-damaged
  questions would put it near **38-40%**, i.e. slightly above baseline. That
  arithmetic is an *estimate to direct the fix*, not a measurement — the only
  measured number here is 30.0%.
- **Parse rate fell 100% → 94%.** Three completions did not yield valid
  `SqlProposal` JSON. The v3 schema block is longer (hint suffixes plus the
  notation legend), and `max_tokens` is still 2048. Worth watching: eval Round 2
  already recorded that a longer prompt hurt gemma's parse rate. One run is not
  enough to call this causal.
- Temperature 0.2 gives ±2-4 pt run-to-run noise. The -6 pt headline is just
  outside that band; the four-question retry mechanism, however, is not a noise
  artefact — it is visible in the attempt numbers.

## Baseline file: deliberately NOT updated

`suites/sales_v1.baseline.json` still reads **36.0% / v2**. Writing 30% into it
would enshrine a regression as the standard and let the nightly gate pass on a
worse pipeline. Re-baseline only after the retry fix is measured — and if that
run lands within noise of 36%, keep 36%.

Nightly CI consequence: `--max-regression 0.02` against the 36% baseline
**would fail** on this code. That is the gate working correctly.

## Per-tag

| tag | exec (new) | exec (2026-07-26) | Δ | recall | n |
|---|---:|---:|---:|---:|---:|
| bridge | 0% | 11% | -11 | 69% | 9 |
| ranking | 0% | 20% | -20 | 73% | 5 |
| self_join | 0% | 0% | +0 | 100% | 1 |
| ties | 0% | 0% | +0 | 100% | 1 |
| time_window | 0% | 0% | +0 | 100% | 8 |
| top_n | 0% | 25% | -25 | 75% | 4 |
| yoy | 0% | 0% | +0 | 100% | 1 |
| join | 21% | 31% | -10 | 77% | 29 |
| aggregation | 28% | 28% | +0 | 84% | 25 |
| cancelled | 33% | 0% | +33 | 100% | 3 |
| ratio | 38% | 38% | +0 | 100% | 8 |
| count | 40% | 60% | -20 | 87% | 10 |
| per_unit | 50% | 50% | +0 | 100% | 2 |
| share | 50% | 0% | +50 | 100% | 2 |
| filter | 67% | 50% | +17 | 97% | 6 |
| distinct | 100% | 100% | +0 | 100% | 1 |
| soft_delete | 100% | 100% | +0 | 100% | 1 |

Tag movements track the same four questions rather than any systematic effect
(`top_n` and `ranking` fall to 0% on n=4 and n=5; `share` and `cancelled` rise
on n=2 and n=3). At these counts a single question is 20-50 points, so read the
per-question table below, not this one.

## Per-question

⚠️ = regressed vs 2026-07-26 · ✅ = improved · `attempt` = which attempt
succeeded · `rows` = gold / candidate

| id | outcome | 2026-07-26 | attempt | recall | rows | tags |
|---|---|---|:--:|---:|---|---|
| sales-001 | MATCH | MATCH | 1 | 1.00 | 1/1 | filter,count |
| sales-002 | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | aggregation |
| sales-003 | MATCH | MATCH | 1 | 1.00 | 1/1 | aggregation |
| sales-004 | MATCH | MATCH | 1 | 1.00 | 6/6 | distinct |
| sales-005 | MATCH | MATCH | 1 | 1.00 | 1/1 | filter,count |
| sales-006 | MATCH | MATCH | 1 | 1.00 | 1/1 | aggregation |
| sales-007 | MATCH | MATCH | 1 | 1.00 | 6/6 | join,aggregation |
| sales-008 | MISMATCH | MISMATCH | 1 | 1.00 | 6/6 | join,self_join,aggregation |
| sales-009 | MISMATCH | MISMATCH | 2 | 1.00 | 7/8 | join,aggregation |
| sales-010 | ERROR ⚠️ | MATCH | - | 1.00 | -/- | join,count |
| sales-011 | MATCH | MATCH | 1 | 1.00 | 8/8 | join,aggregation |
| sales-012 | MATCH | MATCH | 2 | 1.00 | 6/6 | join,aggregation |
| sales-013 | MATCH | MATCH | 1 | 1.00 | 24/24 | join,count |
| sales-014 | MISMATCH ⚠️ | MATCH | 2 | 1.00 | 4/5 | join,count |
| sales-015 | MISMATCH | MISMATCH | 1 | 0.00 | 25/1 | join,count |
| sales-016 | MATCH | MATCH | 1 | 1.00 | 10/10 | join,count |
| sales-017 | MISMATCH | MISMATCH | 1 | 0.67 | 8/6 | join,aggregation,bridge |
| sales-018 | VALIDATION_FAILED | VALIDATION_FAILED | - | 0.67 | -/- | join,bridge,aggregation |
| sales-019 | ERROR | MISMATCH | - | 1.00 | -/- | join,bridge,aggregation |
| sales-020 | MISMATCH | MISMATCH | 1 | 0.00 | 25/1 | join,bridge,aggregation |
| sales-021 | MISMATCH | MISMATCH | 2 | 0.75 | 8/8 | join,bridge,aggregation |
| sales-022 | MISMATCH | MISMATCH | 1 | 1.00 | 16/16 | join,aggregation |
| sales-023 | MISMATCH | MISMATCH | 2 | 0.80 | 3/3 | join,bridge,filter,aggregation |
| sales-024 | MISMATCH | MISMATCH | 2 | 0.67 | 7/8 | join,bridge,count |
| sales-025 | MISMATCH | MISMATCH | 2 | 0.33 | 6/6 | join,aggregation |
| sales-026 | MISMATCH | MISMATCH | 2 | 1.00 | 2/2 | join,bridge,aggregation |
| sales-027 | VALIDATION_FAILED | MISMATCH | - | 0.00 | -/- | join,aggregation |
| sales-028 | MISMATCH ⚠️ | MATCH | 2 | 0.67 | 16/16 | join,bridge,aggregation |
| sales-029 | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | time_window,aggregation |
| sales-030 | MISMATCH | MISMATCH | 1 | 1.00 | 7/7 | time_window,aggregation |
| sales-031 | ERROR | MISMATCH | - | 1.00 | -/- | time_window,yoy,aggregation |
| sales-032 | MISMATCH | MISMATCH | 1 | 1.00 | 2/1 | time_window,aggregation |
| sales-033 | MISMATCH | MISMATCH | 1 | 1.00 | 13/13 | time_window,count |
| sales-034 | MISMATCH | MISMATCH | 2 | 1.00 | 1/1 | time_window,ratio |
| sales-035 | MISMATCH | MISMATCH | 1 | 1.00 | 5/4 | time_window,count |
| sales-036 | MISMATCH | MISMATCH | 1 | 1.00 | 1/1 | time_window,ratio,cancelled |
| sales-037 | MATCH | MATCH | 1 | 1.00 | 1/1 | ratio |
| sales-038 | MATCH ✅ | MISMATCH | 1 | 1.00 | 1/1 | ratio,share |
| sales-039 | MISMATCH | MISMATCH | 2 | 1.00 | 1/1 | per_unit,ratio |
| sales-040 | MISMATCH | MISMATCH | 1 | 1.00 | 6/6 | share,ratio,join |
| sales-041 | MATCH | MATCH | 1 | 1.00 | 1/1 | ratio,per_unit |
| sales-042 | MISMATCH ⚠️ | MATCH | 2 | 1.00 | 1/1 | ratio |
| sales-043 | MISMATCH | MISMATCH | 2 | 1.00 | 5/5 | ranking,top_n,join |
| sales-044 | MISMATCH | MISMATCH | 2 | 0.50 | 10/10 | ranking,top_n,join |
| sales-045 | MISMATCH ⚠️ | MATCH | 2 | 0.50 | 3/3 | ranking,top_n,join |
| sales-046 | MISMATCH | MISMATCH | 2 | 0.67 | 8/6 | ranking,join |
| sales-047 | MISMATCH | MISMATCH | 2 | 1.00 | 120/5 | ranking,top_n,ties,join |
| sales-048 | MATCH | MATCH | 1 | 1.00 | 3/3 | soft_delete,filter,aggregation |
| sales-049 | MATCH ✅ | VALIDATION_FAILED | 1 | 1.00 | 8/8 | filter,cancelled,aggregation,join |
| sales-050 | MISMATCH | MISMATCH | 1 | 1.00 | 2/1 | filter,cancelled |

## Reproduce

```bash
python -m app.eval.runner --suite sales_v1 \
  --llm-config be57faec-3c20-4316-a6e6-b5bb2773e13c
```

*Source: persisted `eval_runs` / `eval_results`, run
`c84f08dd-34e8-479c-95cc-0789029f2e6b`. Golden set unchanged; no gold SQL was
edited for this run.*
