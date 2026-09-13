# Token usage in the UI — LLM observability, phase two

> **Status: proposed, not built.** This is the specification agreed before any
> code; nothing here exists yet.
>
> **Written:** 2026-09-13. Phase one is
> [token-accounting.md](token-accounting.md) — built and merged, and the reason
> there are numbers to show at all. The argument behind both is
> [research/llm-observability.md](../research/llm-observability.md).

---

## Intent

The backend already counts tokens honestly. Migration `0023` landed per-call
accounting across `runs`, `report_runs`, `semantic_jobs`, and per-node on
`run_steps`. **None of it is visible anywhere.** Phase one's §5 named the gap
itself: *"No read endpoint. SQL only, by decision. `GET /usage` is separate
work."*

This phase is that work — a read endpoint, and a screen.

---

## Who sees what

There is **no user hierarchy** in this product. No manager-of relation, no
`parent_id`, and teams are deliberately flat — the `Team` docstring says so:
*"every IdP that matters emits membership as an already-flattened list of
paths, and a local hierarchy would be a second one contradicting it."* Nobody
is *under* anybody.

So visibility is a **capability**, not a privilege — the check needs no
resource id. A new `usage.read`, following the `audit.read` precedent exactly:

| Who | Sees |
|---|---|
| **Everyone** | their own usage — the rows where they are the `actor_id` |
| **A `usage.read` holder** | every person's usage, plus an installation-wide total |

Seeded to **Administrator** and **Auditor**. Auditor is the reason this is a
capability rather than an administrator flag: somebody who reads what the
installation spends and changes nothing anywhere is a role this model can
express, and that is the whole of the same requirement `audit.read` answers.

One screen serves both audiences; the scope widens with the capability. **An
ordinary user never sees another person's number.**

> Adding a capability means the [access-control §6
> checklist](../reference/access-control.md#6--checklist--a-new-capability):
> the enum member, which seed roles get it and why, the migration adding it to
> those roles, `deps.needs(...)` at the route, and a row in
> [security.md](../reference/security.md)'s capability table. Note
> `0024_roles.py` gives Administrator its capabilities **by enumeration**, and
> `test_roles.py` asserts the same sets from an independent literal — both move
> together, or the test fails, which is the point of it.

---

## How it is bucketed

**Daily.** The default window is the **last 30 days**, and every figure on the
screen respects the selected window.

There is no session concept in this product — nothing tracks a login-to-logout
window, and a JWT carries no session id — so a day is the smallest honest
bucket. Every row across the three tables carries a `created_at` and an
`actor_id`, so bucketing is a `date_trunc` away.

**Two rules carry over from phase one's §6 and must not be broken:**

- **A null `cost_usd` is never summed as zero.** Null means litellm could not
  price that model — the normal state for a self-hosted deployment. Show tokens
  always; show cost only where it is known, and **say when a total is partial**
  rather than printing a number that reads as complete.
- **A null token count means "not measured", never "no tokens".** Historical
  rows and providers that send no streamed usage chunk both produce nulls, and
  averaging over them understates every figure they touch.

A third follows from phase one's inner-join rule: deleting a user sets
`actor_id` null and keeps every token they spent. The per-person view stops
counting those rows; the installation total still finds them. That split is
honest and must not be "fixed" with an outer join, which would attribute the
spend to whoever remains.

---

## UI

### Placement

A dedicated rail section, **"Token usage"**, sitting **below LLM providers and
above Administration**.

```
Chat
Dashboards
Reports
Knowledge
────────────────
Data sources
LLM providers
Token usage      ← new
Administration
```

It is visible to everyone, because everyone has their own usage to see. It sits
where it does because it is neither a surface you work in nor one you
configure — it is one you arrive at with a question, which is the same reason
the audit log sits where it does.

### What an ordinary user sees

Their own usage over the last 30 days.

**Textual summary** — total tokens, the input/output split, cost where known,
and the number of runs behind the figure.

**A stacked bar chart:**

- **X-axis:** time — day, or hour where the range is short enough to warrant it
- **Y-axis:** tokens
- **Stack:** input tokens + output tokens (optionally cache read/write — see
  *Open questions* below)

The stacked bar is chosen deliberately: it makes **total consumption and the
composition of that consumption** visible at once, which is the pair of
questions somebody opens a usage screen with.

### What a `usage.read` holder sees

The same screen, with more in it:

- **Their own usage** — textual and chart, exactly as above
- **Each other user's usage** — textual and chart, the same treatment
- **A separate tab for total usage** — every user's consumption combined

### Per-run detail

Tokens and cost fold into the **existing step-chip expansion** on a chat run.
No new always-on chrome in the chat: a reader who wants the breakdown opens it,
and everyone else sees the answer exactly as they do today.

This is where `run_steps` earns its columns — a run saying a question cost 12k
tokens is not the same as knowing the schema block was 9k of it.

---

## Two things to settle before building

### 1. Cache read/write tokens are not stored

The schema has `prompt_tokens` and `completion_tokens` and **nothing else**.
The `Usage` dataclass in `domain/ports/llm.py` carries the same two, plus
latency and model.

So the optional cache read/write segments of the stack **cannot be drawn from
what exists**. Including them means:

- a new migration adding the columns to all four token-bearing tables
- gateway work in `infra/llm/litellm_gateway.py` to read them off the provider
  response, where the provider reports them at all
- widening `Usage`, which is a port type — so every sink and every accumulation
  path moves with it

**Recommendation: treat the cache segments as deferred** and ship the stack as
input + output. They are a genuine follow-up with their own migration, and
bundling them turns a UI phase into a schema phase. The chart's stack is built
to take a third and fourth segment without redrawing, so adding them later
costs a series, not a rewrite.

### 2. The usage chart is chrome, not a query result

This product renders charts through Vega-Lite and has a planner in
`app/charts/` — `profile_result → unchartable_reason → plan_chart →
compile_vega_lite`. **That path is for query results**, where a model proposes
a shape and the platform vetoes it.

A usage chart is neither: its shape is decided here, in this document, and no
model is involved. Routing it through the planner would ask a veto to run
against a result nobody queried, and would make the picture depend on a
heuristic when it is already specified.

**Draw it directly.** Reuse `VegaChart.tsx` as the renderer if that is the
shortest path to a consistent look, but the spec is written by the screen, not
planned. Colours come from `components/palette.ts` like every other chart, and
never from a literal.

---

## What this phase does not do

Named so nobody assumes otherwise:

- **No budgets, quotas or alerts.** This measures; it does not enforce. Phase
  one said the same, and the reason still holds: a cap would have to fail
  *closed*, and everything here fails open.
- **No embeddings.** `embed()` spends real tokens and is still uncounted — a
  different unit, a different call site, and a follow-up of its own.
- **No cross-model comparison.** *"Which model costs us most per answer"* is a
  good question and a different screen; it needs the model name on the
  aggregation, which `model_snapshot` carries but this phase does not group by.
- **No backfill.** Historical rows stay null, for the reason phase one's §2
  gives — a backfill would invent a version for a run nobody can re-render.
