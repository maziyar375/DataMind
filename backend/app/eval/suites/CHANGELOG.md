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
  (Phase 0 of [docs/learning-loop-plan.md](../../../../docs/learning-loop-plan.md)).
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

---

## Gold corrections

<!-- No corrections. The set as authored in Task 2 stands. -->

Evaluated against gemma-4-31B and DeepSeek V4 Pro. Every mismatch investigated
traced to a model choice, a service/harness defect (guard CTE handling, an
over-strict rounding tolerance), or genuine question ambiguity (relative time
windows, long-vs-wide comparison shape) — never a demonstrably wrong gold. No
gold SQL was changed.
