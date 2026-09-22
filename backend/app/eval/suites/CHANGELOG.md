# Golden-set changelog

The golden set is **frozen**. Questions are never edited to make a score go up.
Gold SQL is corrected **only when demonstrably wrong** (it does not answer the
question the English asks, or it errors against the fixture), and every such
correction is logged here with the reason and the evidence.

Retrieval context and prompts may be tuned freely; the gold answers may not.

Format per entry:

```
## <date> — <suite> <record-id>
- **What changed:** old → new (one line)
- **Why:** the demonstrable defect (not "to pass")
- **Evidence:** the query/output that proves the old gold was wrong
```

> **Two things are logged here, not one.** Corrections to the gold answers
> (the section below, still empty) and **decisions that change what a number
> means** without touching a question. The second kind is the reason this file
> is read before a comparison: nobody can tell from a report that it was run at
> a different retrieve budget, and everybody will compare it anyway.

---

## Arm and harness decisions

### 2026-08-31 — the retrieve budget is a runner flag, and lowering it breaks comparability

- **What changed:** `python -m app.eval.runner` gained `--retrieve-budget CHARS`
  (Phase 0 of [docs/plans/learning-loop.md](../../../../docs/plans/learning-loop.md)).
  It lowers `app.pipeline.nodes._RETRIEVE_BUDGET_CHARS` for that run only. No
  gold answer, no fixture, and no shipped default moved: absent the flag the run
  is byte-identical to every run before it, and the effective value is now
  recorded on the scorecard as `retrieve_budget_chars`.
- **Why:** the shipped ceiling is 50,000 chars and the `sales` fixture estimates
  ~26,480, so **every** question takes the `FULL_SNAPSHOT` branch and
  `retrieval_recall` is 1.0 by construction. Recall and full-hit have been
  reporting a constant since the ceiling was raised 24k → 50k, which makes any
  claim that a change improved retrieval unfalsifiable. The fixture was built
  wide precisely so retrieval would be exercised; the flag gives that back
  without widening the fixture (which would move every other number too).
- **The trap, stated once so nobody falls into it:** a recall figure measured
  **at a lowered budget is not comparable to one measured at the shipped
  ceiling** — including the 0.864 in `sales_v1.baseline.json`, which was
  measured at 24k. Read `retrieve_budget_chars` off both scorecards before
  putting two recall numbers in the same sentence. Execution accuracy moves too:
  a question whose tables retrieval no longer selects is a question the model
  cannot answer, which is the point.

### 2026-08-31 — the semantic layer is an arm, with a fixture layer to switch on

- **What changed:** `--semantic on|off` (default `off`), and a new fixture file,
  `backend/fixtures/sales_semantic.json`, bound to the live snapshot by
  `runner.load_semantic` and recorded on the scorecard as `semantic_layer`.
- **Why:** `PROMPT_VERSION` moved v7 → v8 when the layer's render was fixed
  (mvp2 §A6), and the A/B has never been run against a prompt that *contains*
  the layer — the runner had no way to pass `NodeDeps.semantic` at all.
- **How the layer was authored, because this is where an arm gets faked:** every
  claim in it is the structured form of a fact already stated in
  `sales_seed.sql` or `sales_comments.sql`. It was **not** written by reading
  `sales_v1.json` — a layer authored against the gold answers measures its
  author. The two deliberate plants in the comments overlay (`customers.segment`
  stale, `orders.subtotal` wrong) appear here in their true form, which is the
  honest difference between a reviewed layer and a rotted DDL comment.
- **What it may cost:** the layer restates the fixture's own business rules,
  including two that add a predicate a question did not ask for — closed
  customer accounts are excluded by default, and `average_rating` excludes
  moderated-out reviews. If a gold answer disagrees with the fixture's own
  documentation, the layer-on arm loses that point. That is a finding about the
  layer, not a licence to edit the gold set.

### 2026-09-22 — `deep_v1` is frozen, it has no gold answers, and it reuses the flat fixture on purpose

- **What changed:** `deep_v1.json` — twenty *why* / *what-drove-it* questions for
  the deep analysis mode ([docs/plans/deep-analysis-mode.md](../../../../docs/plans/deep-analysis-mode.md)
  Phase 0), frozen the day they were written, before a planner, a loop or a
  contribution module existed. No gold SQL, no `result_equivalence`, and
  `tests/eval/test_golden_set.py` now fails if either appears.
- **Why no gold:** every metric that plan's Phase 7 declares — guard pass rate
  and execution success rate across sub-queries, claim traceability, plan
  adherence, cost per answer — is computed from the run itself. A reference
  answer would buy nothing and would be written, when it came, by whoever had
  just built the loop it grades.
- **§11 Q3, answered, and not the way the plan expected.** The question was
  whether this set reuses `sales` or needs a messier fixture with a real drop in
  it. It reuses `sales`, because the fixture turns out to have **no planted
  movement at all** — and that is the more valuable property, not a shortfall.
- **Evidence,** measured against the live fixture on the freeze date:

  ```
  orders/month, 2024-10 .. 2026-08 : 248 .. 259 every full month
  revenue                          : tracks it at ~$957/order throughout
  2026-01 vs 2026-02               : 8.355 vs 8.321 orders per trading day
  returns/month on returned_at     : 82 .. 85, reasons split 137/136/136/136
  order_items.unit_price           : ONE distinct value (88.70) over 18,000 rows
  ```

  So the true answer to most of these questions is *"nothing drove it — here is
  why the number looked like it did"*, and a mode that names a driver has
  fabricated one. That is the same bet `sales_v1_negative.json` makes, and it is
  a sharper test than a planted drop: fifteen of the twenty records carry a
  `known_by_construction` string recording the fact that makes the honest answer
  checkable.
- **What this set therefore cannot do, written down before anyone claims it:**
  it cannot score driver accuracy, because there is no driver. That needs a
  second fixture with a movement planted in it and its decomposition known in
  advance (`sales_drop_v1`, not built). The trigger to build it is the first
  time a top-three-driver number is wanted against real data rather than the
  constructed unit fixtures of `test_contribution.py`.

---

## Gold corrections

<!-- No corrections. The set as authored in Task 2 stands. -->

Evaluated against gemma-4-31B and DeepSeek V4 Pro. Every mismatch investigated
traced to a model choice, a service/harness defect (guard CTE handling, an
over-strict rounding tolerance), or genuine question ambiguity (relative time
windows, long-vs-wide comparison shape) — never a demonstrably wrong gold. No
gold SQL was changed.
