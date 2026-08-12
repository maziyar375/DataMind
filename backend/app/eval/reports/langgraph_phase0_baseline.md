# Eval baseline — LangGraph migration, Phase 0

**Status: NOT YET MEASURED.** This file is the slot and the protocol. It is
committed empty on purpose: [docs/langgraph-migration.md](../../../../docs/langgraph-migration.md)
§4 Phase 0 requires the baseline to exist *before* Phase 1 moves the
orchestrator, and §5 makes every later phase compare against **this run**, not
against `suites/sales_v1.baseline.json` — which was measured on a different
model at a different temperature and cannot arbitrate a refactor.

The harness calls a real provider and costs real money, so it is not in
`make test` and cannot be run from a sandbox with no credentials. Whoever has
the account runs the command below and fills in §2.

---

## 1. What to run

From `backend/`, against the fixture the suite is frozen on:

```bash
python -m app.eval.runner --suite sales_v1 --llm-config <UUID> --json \
  | tee app/eval/reports/langgraph_phase0_baseline.json
```

Fixed model, **temperature 0**. Temperature 0 is not a preference here: the
whole point of the comparison is that the prompts do not change, so any
accuracy movement beyond noise means the orchestrator changed something it
should not have. Sampling noise would make that signal unreadable, and the
gate is 2 points wide.

Then re-run the negative suite the same way (`--suite sales_v1_negative`), and
record both.

## 2. The numbers (fill in)

| | value |
|---|---|
| `eval_run` UUID | _unmeasured_ |
| Date | _unmeasured_ |
| Model (provider string) | _unmeasured_ |
| Temperature | must be `0` |
| `max_tokens` | read it off the run's persisted `model_snapshot`, don't assume |
| Execution accuracy | _unmeasured_ |
| Retrieval recall — mean / full-hit | _unmeasured_ |
| Parse rate | _unmeasured_ |
| Policy-violation rate | _unmeasured_ |
| Wall time | _unmeasured_ |
| Prompt + completion tokens | _unmeasured_ |
| Negative suite — correct route | _unmeasured_ (10 records) |

**Code under test:** commit `2bf5bab` plus the Phase 0 groundwork (the
`langgraph` dependency, the seventh import-linter contract, the CI grep and
`tests/unit/test_pipeline_events.py`). None of those touch a prompt or a node,
so a baseline captured before or after them is the same baseline.

## 3. Two things to write down by hand, because the row lies

**The prompt version is `v7`.** `runs.prompt_version` is written from
`settings.prompt_version`, whose default is still `"v2"` — it is not read from
the `PROMPT_VERSION` constant in `app/pipeline/prompts/__init__.py`, which is
`"v7"`. [docs/pipeline.md](../../../../docs/pipeline.md) §7 records the drift.
Every eval row this run writes will therefore claim `v2`. Write the constant
down here, or the comparison against a later phase is meaningless:

> This baseline was measured with **`PROMPT_VERSION = "v7"`** and
> **`REPORT_PROMPT_VERSION` at r4**, whatever `runs.prompt_version` says.

Non-negotiable #1 holds both constants still for the whole migration, so a
later phase that reads `v7` here and `v7` there has compared like with like.

**The negative suite is no longer cheap.** Since `describe` landed, each of the
3 METADATA records (`sales-neg-001` … the metadata-tagged ones) costs a second
schema-bearing model call: `route` classifies, then `describe` streams an
answer over the full rendered schema block. The other 7 records — 5
UNSUPPORTED, 2 CHITCHAT — still halt at `route` for one cheap call. Expect the
token line to be well above what "10 records" suggests. **That is not a
regression**, and a later phase must not "fix" it.

## 4. How a later phase uses this

Per §5 of the migration record, a phase is done when all five gates pass, and
this is the fifth:

```bash
python -m app.eval.runner --suite sales_v1 --llm-config <the same UUID> \
  --baseline-file app/eval/reports/langgraph_phase0_baseline.json \
  --max-regression 0.02
```

Same model, same temperature, same fixture. Prompts are unchanged by
construction, so movement beyond ±2 points is the orchestrator, and the phase
is wrong until it is explained.
