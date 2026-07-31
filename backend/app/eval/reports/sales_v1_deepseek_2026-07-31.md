# Eval report — `sales_v1` on DeepSeek V4 Pro, post-semantic-layer pipeline

- **Date:** 2026-07-31
- **Model:** `openai/lightning-ai/deepseek-v4-pro` (Lightning OpenAI-compatible
  endpoint), **temperature 0**, **`max_tokens` 50000** — llm_config
  `be57faec-3c20-4316-a6e6-b5bb2773e13c` (both values already set in the DB;
  confirmed against the run's persisted `model_snapshot`, not assumed)
- **Suite:** `sales_v1` — 50 questions, **golden set unchanged** (`suites/` and
  `tests/eval/` byte-identical; verified with `git diff` before and after)
- **Code under test:** commit `4a37369`, `PROMPT_VERSION` **v5**
- **eval_run:** `dabe889f-3ee8-4f73-84e5-f064c96b66be` (28.7 min wall)
- **Compares against:** `77786471-15b2-4f28-87e5-8d0e8255327b` — the 36% run of
  2026-07-26 that `sales_v1.baseline.json` still records

---

## The headline number in the tool output is not a result

The runner printed **20.0%**. That figure should not be quoted anywhere.

At question 37 the Lightning account ran out of credits, and every remaining
question failed with a hard `litellm.APIError` ("does not have enough credits").
Fourteen questions — `sales-037` … `sales-050` — therefore never reached the
model. The harness scores an unreachable provider as `OUTCOME_ERROR`, which
divides into the same denominator as a wrong answer, so the printed accuracy is
a billing artefact.

| | count |
|---|:--:|
| Questions that actually ran | **36** (`sales-001`…`sales-036`) |
| Failed on account credits (not measurable) | **14** |
| MATCH | 10 |
| MISMATCH | 24 |
| ERROR — run deadline | 2 (`sales-019`, `sales-032`) |

**The suite must be re-run once credits are topped up.** Everything below is
what the 36 measurable questions support, and nothing more.

## Controlled comparison, same 36 questions

Comparing a 36-question prefix against a 50-question baseline would be
meaningless — the suite is ordered by tag, so the prefix is not a random
sample. The comparison below re-scores the **07-26 baseline run on exactly the
same 36 record ids**, from its own persisted `eval_results` rows.

| | 07-26 baseline (v2, temp 0.2) | **this run (v5, temp 0)** |
|---|:---:|:---:|
| Execution accuracy, same 36 questions | **36.1%** (13/36) | **27.8%** (10/36) |
| Excluding the 2 deadline ERRORs (34 q) | 38.2% (13/34) | 29.4% (10/34) |
| Retrieval recall mean / full-hit | 86.4% / 74% | 94.4% / 94.4% (see below) |
| Policy-violation rate | 10% | 5.6% (2 q, `E_NODE_NOT_ALLOWED`×2) |

The recall and policy figures above are recomputed over the same 36 questions.
The runner *printed* 96.0% recall and a 4.0% violation rate because it averaged
over all 50 — and note **the 14 credit-failed questions each recorded recall
1.000**: in the `FULL_SNAPSHOT` branch retrieval takes no model call, so it
"succeeds" even when the provider is refusing every request. That is worth
remembering before trusting recall@k on any truncated run.

Both deadline ERRORs were already MISMATCH in the baseline, so **no correct
answer was lost to the deadline** — excluding them does not rescue the number.

### The eight questions that moved

| id | baseline | this run | tags |
|---|---|---|---|
| sales-005 | MATCH | **MISMATCH** | filter, count |
| sales-013 | MATCH | **MISMATCH** | join, count |
| sales-015 | MISMATCH | **MATCH** | join, count |
| sales-016 | MATCH | **MISMATCH** | join, count |
| sales-018 | VALIDATION_FAILED | MISMATCH | join, bridge, aggregation |
| sales-019 | MISMATCH | ERROR (deadline) | join, bridge, aggregation |
| sales-028 | MATCH | **MISMATCH** | join, bridge, aggregation |
| sales-032 | MISMATCH | ERROR (deadline) | time_window, aggregation |

Four regressions, one gain: **net −3 questions**. `sales-028` is notable — it is
one of the four the 07-27b retry-narrowing fix specifically restored to MATCH,
and it has regressed again.

## What actually changed, and what it means

Fifteen commits separate this run from the baseline (semantic layer, `clarify`,
`metadata`, inspect surfacing, repair-prompt change, retrieve budget). Three
points matter for reading the number:

- **Retrieval is no longer exercised by this suite.** `_RETRIEVE_BUDGET_CHARS`
  went 24k → 50k (`pipeline/nodes/__init__.py:170`) and the `sales` fixture sits
  at ~26.5k, so `retrieve` now always takes the `FULL_SNAPSHOT` branch. The jump
  to 96% recall is **by construction, not improvement** — recall@k has stopped
  being an informative metric here, and the fixture no longer does the job it
  was widened to do (the docstring notes the fixture "straddled the old value";
  it no longer straddles anything). The generator now receives all 42 tables on
  every question, a much larger prompt than in the baseline — the most likely
  mechanism behind both the regressions and the deadline ERRORs.
- **The semantic layer was OFF.** `NodeDeps.semantic` defaults to `None` and the
  runner never sets it, so this is the **control arm**, not a test of the layer.
  The layer's effect on the suite remains unmeasured.
- **`clarify` was OFF** (`clarify_enabled` defaults `False`), so no run ended
  without SQL for want of an answered question.

**Two variables moved at once** — temperature 0.2 → 0 *and* fifteen commits of
pipeline change — so this run cannot attribute the −3. Temperature 0 is the
right setting (it removes the ±4pt run-to-run noise the 07-25 report measured),
but it means the honest comparison against a temp-0.2 baseline is directional.
A temp-0 baseline needs to be established before any of this is a gate.

## Deliberately not done

- **`sales_v1.baseline.json` was NOT updated.** It still reads 36% from
  2026-07-26. A partial, credit-truncated run measured at a different
  temperature is not a baseline, and writing 20% (or 27.8%) into that file would
  silently lower the CI regression gate on the strength of a billing failure.
- **No gold was edited.** The four regressions are candidate-vs-gold result
  mismatches, not gold defects; `suites/CHANGELOG.md` gains no entry.

## Harness changes made during this run

- **`prompt_version` provenance fixed** (`app/eval/runner.py`). It recorded
  `settings.prompt_version` — hardcoded `"v2"` in `core/config.py:70` — so every
  run since v3 was filed under a version it never used. It now records
  `PROMPT_VERSION` from the prompt module; this run is the first correctly
  labelled **v5**. *Still outstanding:* `run_service.py:123` has the same bug on
  the request path.
- **`scripts/eval_run.sh` added** — runs the suite with a retry envelope tuned
  for a rate-limiting provider (9 retries, 3s → 90s, 120s request timeout) and,
  critically, `RUN_DEADLINE_SECONDS=600` to match. The stock 120s deadline
  aborts the run regardless of gateway patience, so widening retries alone
  achieves nothing. It changes no prompt, scoring, or pipeline behaviour.
  **It would not have saved this run** — credit exhaustion is a hard `APIError`,
  correctly not retried.

## Next step

Top up the Lightning account and re-run the full 50 at temperature 0 to
establish a valid v5 baseline:

```bash
cd backend && scripts/eval_run.sh --suite sales_v1 \
  --llm-config be57faec-3c20-4316-a6e6-b5bb2773e13c
```

Then the A/B the semantic layer was built for: the same 50 questions with
`semantic_layer_enabled` on, against that baseline. Note the runner has no flag
for this yet — it never passes `NodeDeps.semantic`, so the treatment arm needs a
harness change before it can be measured at all.
