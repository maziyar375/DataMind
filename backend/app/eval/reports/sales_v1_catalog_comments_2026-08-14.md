# Eval report — catalog comments A/B on `sales_v1`, DeepSeek V4 Flash

- **Date:** 2026-08-14
- **Model:** `openai/deepseek/deepseek-v4-flash`, **temperature 0** — llm_config
  `5d9a559f-d90e-4f39-a5d4-a284a335ff8e` (confirmed against each run's persisted
  `model_snapshot`, not assumed)
- **Suite:** `sales_v1` — 50 questions, **golden set unchanged**
  (`git status --porcelain` on `app/eval/suites/` and
  `tests/eval/sales_v1_verify.json` is empty, before and after)
- **Code under test:** commit `45766d9`, `PROMPT_VERSION` **v7**
- **The two runs:**

| Arm | `--comments` | eval_run | Wall |
|---|:---:|---|---:|
| **A — uncommented** | off | `0e85751d-8979-496b-9d3c-1712363f03fb` | 19.9 min |
| **B — commented** | on | `09ac556c-415c-4146-af76-ab8d49e0756b` | 22.6 min |

Both arms are the **same fixture**: arm B loads `fixtures/sales_comments.sql`
(21 table + 42 column descriptions, plus the database's and the schema's) on top
of the identical seed, and renders them into the run prompt. This is Phase 6 of
[docs/catalog-metadata-plan.md](../../../../docs/catalog-metadata-plan.md).

---

## The headline, and why it is not the result

| | **A — no comments** | **B — comments** | Δ |
|---|:---:|:---:|:---:|
| **Execution accuracy** | **40.0%** (20/50) | **36.0%** (18/50) | **−4.0 pp** |
| Retrieval recall / full-hit | 100% / 100% | 100% / 100% | — |
| Parse rate | 98% | **100%** | +2 |
| Guard-pass rate | 98% | **100%** | +2 |
| Execution-success rate | 98% | **100%** | +2 |
| Policy-violation rate | 6.0% | **2.0%** | −4.0 pp |
| Solved on first attempt | 46 | **49** | +3 |
| Repairs that failed outright | 1 | **0** | −1 |
| `ERROR` outcomes | 1 | **0** | −1 |

**Two questions is not a result.** At n = 50 and p ≈ 0.4 the standard error on a
single arm is about 7 points, so a 4-point gap is comfortably inside the noise
of one run. The stronger evidence that this is noise is in the same data:
**12 of the 50 questions flipped** — 5 to `MATCH`, 7 away from it — so the
underlying run-to-run variance is far larger than the 2-question net. Anyone
quoting "catalog comments cost 4 points" from this run would be quoting the
noise.

The responsible summary is: **no measurable effect on execution accuracy on this
model at this sample size.** What the run *does* support is qualitative, and it
is below.

---

## What the flips actually were

The 12 flips split three ways: **five** have a cause traceable to a specific
comment, **two** are artefacts that scored `MATCH` without being better answers,
and **five** are the ordinary inner-vs-left-join / ties / projection-shape
variance this suite shows between any two runs (`sales-008`, `sales-010`,
`sales-022`, `sales-042`, `sales-047` — none of which touches a commented column
in a way the comment explains). The five that matter:

### The comment moved the model to a stricter definition of revenue — and the golden set uses the looser one

The `orders` table comment says, truthfully:

> *One row per checkout, at any stage of its life. Not every order is revenue:
> cancelled and returned orders stay here with their money on them, so anything
> that means genuine sales has to filter on status.*

Arm B believed it, and started writing `WHERE status IN ('completed','shipped')`
on revenue questions. That **wins the one question that asks for the rule and
loses three whose gold counts every order**:

| Record | Question | Arm B did | Verdict |
|---|---|---|---|
| `sales-049` | "…leaving out anything cancelled or sent back" | `WHERE NOT status IN ('cancelled','returned')` | **gained** — arm A invented an `order_status_history` join and missed |
| `sales-002` | "total value of everything we've sold across every order" | added the status filter | **lost** — 3,590,310 vs the gold's 5,744,496 |
| `sales-028` | "Which brands move the most units?" | added the status filter | lost |
| `sales-040` | "What portion of our revenue does each category account for?" | added the status filter | lost |

This is the most interesting thing in the run, and it is not really a fact about
the feature. `sales-002`'s English says *"across every order"*, so its gold is
right to be unfiltered — but `sales-028` and `sales-040` simply say "units" and
"revenue", and a business would probably call arm B's answer the better one.
**The comment did not make the model wrong; it made the model disagree with the
suite about what revenue means.** That is a fact about the golden set worth
recording, and per the freeze rule it is *not* a licence to edit those golds:
they are defensible readings, and editing a gold because a change scored badly
is exactly what the freeze exists to prevent.

### A true comment can still point at the wrong column

`sales-009` ("how quickly do our suppliers deliver, on average, in each part of
the world"). The comment on `suppliers.lead_time_days` says — accurately —
*"The same field on product_suppliers overrides it per product."* Arm B followed
the pointer and averaged `product_suppliers.lead_time_days`; the gold averages
`suppliers.lead_time_days`. Both are defensible readings of the English and they
give different numbers (Europe: 7.50 vs 10.75). A comment that documents an
override will send a model to the override. Worth knowing before writing one.

### Two of the five "gains" are artefacts, not wins

Checked rather than counted, which is the point of doing this by hand:

- **`sales-037` passed on the tolerance, not on the query.** Gold is
  `sum(total_amount)/count(*)` over all orders = 957.42; arm B wrote
  `AVG(total_amount) WHERE status IN ('completed','shipped')` = 957.416. Those
  are different computations that agree to 0.004 — inside the documented
  half-cent absolute tolerance. A `MATCH`, and not an improvement.
- **`sales-023` passed on a fixture artefact.** Arm B added a status filter that
  happens to change nothing for the EU region, because of a modular alignment in
  the seed's generated data. Identical rows, coincidentally.

Deducting both, the honest gain column is three, not five.

---

## Neither planted comment was taken

The fixture carries two deliberate lies (documented in its header and asserted by
`tests/eval/test_golden_set.py`, so a tidy-up cannot quietly remove them):

- **stale** — `customers.segment` lists a `Reseller` tier the seed no longer
  writes;
- **wrong** — `orders.subtotal` claims to be *"the amount the customer actually
  paid us, tax and shipping included"*, which is `orders.total_amount`.

**Across all 100 pipeline runs, not one candidate query referenced `subtotal` or
`Reseller`.** Grepped over every persisted `candidate_sql` in both arms. The
model read the column names over the prose in exactly the case where the prose
was wrong — which is what the §5.1 prompt rule asks of it (*"if it contradicts
the column names and types you can see, say what you can support"*), and it is
the single most reassuring line in this report. One model, one run: it is
evidence, not a guarantee.

---

## Where the commented arm was plainly better

Every metric about *writing valid SQL against this schema* moved the same way,
and none moved the other way:

- parse, guard-pass and execution-success all **98% → 100%**;
- policy violations **6% → 2%** — arm A raised `E_NODE_NOT_ALLOWED` three times
  (one of them on a repair attempt) and `E_UNKNOWN_ALIAS` once; arm B raised
  `E_UNKNOWN_COLUMN` once;
- first-attempt solves **46 → 49**, failed repairs **1 → 0**, `ERROR`s **1 → 0**.

Four metrics moving together is worth more than any one of them, but it is still
one run per arm: read it as *consistent with* comments helping the model produce
valid, resolvable SQL, not as proof.

**Retrieval was saturated in both arms** (100% recall, 100% full-hit), so this
run says nothing about whether comments help retrieval — and that was
**predicted, not discovered**. The commit that held this A/B (`af29c19`) had
already worked out why: `_RETRIEVE_BUDGET_CHARS` moved 24,000 → 50,000 while this
fixture estimates 26,480, so `retrieve` takes the `FULL_SNAPSHOT` branch and
recall is 1.0 by construction; and `retrieve` selects on names, never reading a
comment at all. The run confirms both — 100.0% in each arm, to the decimal.
Whether comments help retrieval is not a question this suite can currently ask,
and re-running it will not change that. It needs a fixture wide enough to clear
50k, which is a separate piece of work.

## Cost and latency

Wall clock **19.9 → 22.6 min** (+13%), consistent with a larger prompt: on a
three-table retrieval the block grows from 1,749 to 4,379 characters. Per-question
token and cost accounting is **`n/a`** — this provider returns no usage block, so
the run cannot price the difference. That is a gap in what this A/B can say:
comments are not free, and how much they cost per question was not measured.

## What would settle it

1. **Three runs per arm** on the same model, comparing means. One run per arm
   cannot see a 2-question difference, and this suite flips ~12 questions
   between any two runs.
2. **A model whose provider reports usage**, so the accuracy delta can be put
   beside the token delta.
3. **V4 Pro**, the model `sales_v1.baseline.json` was measured on, if the result
   is ever to be compared with the recorded history. This run cannot be:
   **the baseline is 0.36 on V4 Pro at temperature 0.2 under `PROMPT_VERSION` v2**,
   and both arms here are V4 Flash at temperature 0 under v7. The two 36% figures
   are a coincidence of formatting and mean nothing together.
