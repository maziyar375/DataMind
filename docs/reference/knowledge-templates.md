# Knowledge templates

A **taught question**: one somebody already answered correctly, stored so the
system answers it the same way next time. The semantic layer says what the
schema means; this is the next thing along.

One editable row per taught question in `knowledge_templates`, scoped to a
connection and dying with it, curated at `/knowledge/:id`.

Code: [`backend/app/knowledge/`](../../backend/app/knowledge/) — `params.py`
(the AST slot proposer), `matcher.py` (trigram), `embed.py` (vectors and
cosine), `bind.py` (slot filling and its veto), `compare.py` (the shared
comparator), `backlog.py`, `conflict.py`. Workers:
[`knowledge_maintenance.py`](../../backend/app/workers/knowledge_maintenance.py)
and [`benchmark.py`](../../backend/app/workers/benchmark.py).

> **This is the reference for the built feature.**
> [plans/learning-loop.md](../plans/learning-loop.md) is the plan it was built
> from and the record of how — read it for the phase ledger, the four decisions
> taken up front (§0.2), and the measurement each phase had to produce.
> [research/learning-loop.md](../research/learning-loop.md) is the argument for
> why it exists at all.
>
> **Two parts of this ship *off***, both waiting on the same thing — a provider
> key, not code: few-shot injection (`knowledge_examples_enabled`, default
> false) and the embedding matcher (`embedding_model`, default empty). See
> [../status.md](../status.md).

Companion to [security.md](security.md) (the guard's fifth entry point, and the
disclosure rung a template's literals need), [semantic-layer.md](semantic-layer.md)
(the other curated document), [pipeline-chat.md](pipeline-chat.md) (where the
`match` node sits) and [eval.md](eval.md).

---

## 1. What a template is

Curated at `/knowledge/:id`, which is both a rail entry and a connection tab.

- **The artifact is a parameterized question→SQL template, not a literal
  pair.** `revenue by month for {region} in {year}` with `:region` and `:year`
  in the SQL. A literal store's hit rate stays near zero, and retrofitting
  parameters onto pairs authored without them means re-curating everything.
- **The curator does not type `:params`. The AST offers them.**
  `app/knowledge/params.py` walks the tree the guard already produced and
  proposes a slot for every literal it can classify — a date bound, an equality
  against a categorical column, a threshold on a measure — and **refuses** the
  ones that are almost always part of the definition: anything inside a `<>`,
  a `NOT IN`, a `CASE` or a `COALESCE`, a list, a pattern. A refusal is
  *returned* with its reason and shown unticked, because showing the rejected
  candidate teaches the rule better than hiding it. No model call: this is a
  tree walk, and it is the one thing here no competitor can do, because none of
  them has a guard that already parses the statement.
- **The substitution happens on the tree, in the server.** `parameterize`
  replaces the ticked literal node; a `str.replace` would rewrite the `'EMEA'`
  in a `CASE` arm too. Placeholders are written `:name` in every dialect —
  Postgres' generator spells `exp.Placeholder` as `%(name)s`, which is a
  driver's binding syntax and not what this store agrees on.
- **A template is guarded twice, and gets no exemption.** On save: is this
  legal at all, against the current snapshot — a rejection shows the guard's
  own message verbatim. On every use: is it *still* legal against the schema as
  it is **now** — a failure marks the template `STALE`, withdraws it, and lets
  the run fall through to generation. **Never fails the run, never silently
  deletes the row** — a stale template *fails as a value*, and an invalid
  human-written entry is flagged and kept, exactly as in the semantic layer.
- **`note` is written for the next curator and never reaches a prompt.** The
  research measured more prose in the prompt lowering execution accuracy. If it
  ever renders, that moves `PROMPT_VERSION` and goes through an eval arm like
  everything else.
- **`role` decides what a template is for**: `RETRIEVABLE` (answers questions),
  `BENCHMARK_ONLY` and `HELD_OUT` (measure accuracy). A held-out question
  answered from its own stored SQL measures nothing, so the exclusion is a
  column and is enforced in the query that builds the candidate set.
- **A template's literals are a disclosure** — `may_render_literals` in
  `app/knowledge/models.py`, and [security.md](security.md) for why.
  Hand-authored literals travel with structure like a catalog comment; ones a
  model chose are gated like sample values, at *render* time.
- **Curation is gated by exactly one function**, `services.policy.can_curate`,
  and no endpoint checks `ctx.is_admin` directly — a test asserts that on the
  parse. Phase 8 turned `curation_admin_only` **on by default**, and the rule
  it means is **administrator *or* the owner of the connection**. The second
  half is what makes the flip correct rather than a lockout: `_owned()` already
  scopes every knowledge endpoint to `owner_id == ctx.user_id`, so admin-only
  alone would have meant *the person who owns a connection cannot curate their
  own store*. Nobody can observe a difference today; it starts mattering the
  moment a connection can be **shared** (mvp2 §D1), which is why it is on
  before sharing exists rather than after.
- **`app/knowledge/` is self-contained** on the same terms as `sqlguard`,
  `semantic` and `reports` — no fastapi, sqlalchemy, litellm, `app.infra` or
  `app.services`. It *may* import `app.sqlguard`: validating a template **is**
  calling the guard, and that is the point.

## 2. Answering from the store — the `match` node

Between `route` and `retrieve`, no model call, and on the short-circuit path
it changes an answer
**without changing a byte of the prompt**. (Phase 5 gave the same node a second
job on a *miss* — see below — and that is what moved `PROMPT_VERSION` to v9.)

- **Two thresholds, not one.** `SHORT_CIRCUIT_THRESHOLD` (0.85) answers;
  `FEW_SHOT_THRESHOLD` is Phase 5's. A near-miss is not a hit: a miss costs
  today's behaviour, a false hit costs a confident wrong answer. The threshold
  is tuned from the **override rate**, not from taste.
- **`pg_trgm` is an index, not the verdict.** The row source narrows with the
  GIN index where the extension exists; the score is always computed by
  `trigram_similarity`, Postgres' own algorithm reimplemented. One scoring
  path, so a deployment without the extension gets the same verdicts more
  slowly rather than a different feature.
- **The template's declared values are masked out of the question before
  scoring.** Without it the canonical example — a `{region}` pattern against a
  question naming EMEA — scores 0.83 and never fires, and lowering the
  threshold to compensate would let real differences in. Masking can only
  remove a difference the *curator* declared to be a value.
- **Binding has a veto.** `bind.py` fills each slot from the question — a small
  date grammar, a value the parameter's comment lists, a single numeral — and
  **any slot that will not bind cancels the hit**, logged as
  `REJECTED_UNBOUND`. A half-bound template is a confident wrong answer, and
  the log is how the next grammar gets chosen. Substitution is on the tree:
  there is no rendering in which a bound value becomes SQL.
- **A hit lands on `validate`**, the guard's own entry point, so it is
  re-validated against the current snapshot, rewritten and row-capped like
  generated SQL. There is no new execution code in this phase.
- **A stale template fails as a value**: `REJECTED_STALE`, the run falls
  through to generation, the row is not deleted and the run does not fail.
- **`NodeDeps.matcher = None` is the pre-feature path exactly** — SKIPPED,
  nothing read, byte-identical prompt. The draft graph and the eval runner both
  take it.
- **Every verdict is logged** to `knowledge_template_hits`, including
  `OVERRIDDEN_BY_USER` — written when a reader presses *Generate a fresh answer
  instead*. That is the honest measure of whether the short-circuit is trusted,
  and no vendor in the research publishes its equivalent.
- **The badge is three tiers and "Generated" is not a warning.** Verified earns
  a green chip **plus the matched question and the bound parameters** — the
  reader's only defence against a confident wrong match. Grounded is a quiet
  accent chip. Generated gets one honest sentence in faint text, because it is
  most answers and dressing it in amber would train everyone to ignore amber.

## 3. Capture — feedback, the queue and the backlog

Ships no accuracy. Ships the reason anyone curates.

- **`POST /runs/{id}/feedback` is open to any signed-in user**, deliberately —
  it does **not** ask `can_curate`, while resolving a flag does. The person
  best placed to notice a wrong answer is the person who asked the question,
  and they are usually not the person allowed to fix it; gating the *report* on
  the right to *repair* loses exactly the reports worth having.
- **Three verdicts, not two.** `CORRECT` / `WRONG` / `NEEDS_REVIEW`, because
  "this is wrong" and "please look at this" are different asks. A `CORRECT`
  arrives already `RESOLVED`, by the person who gave it — otherwise the tab
  would carry a number no curator could ever clear.
- **`answer_feedback.became_template` is the loop closing.** One nullable FK,
  surfaced back to the flagger on their own answer. Without it the phase has
  shipped a suggestion box, and people learn their thumbs-down goes nowhere.
- **A dismissal takes a reason**, shown back to the flagger: a dismissal with
  no note is indistinguishable from being ignored.
- **The backlog is five ranked sources** (`app/knowledge/backlog.py`, pure):
  flagged, backfill, traffic, failed, and **words the retrieval did not
  recognise** — Power BI's *Review questions*, nearly free here because the
  semantic layer already holds the vocabulary. Everything already taught is
  excluded, so the list shrinks as it is worked.
- **The backfill reads what is already there.** `dashboard_tiles` and
  `report_blocks` with `sql_origin IN ('GENERATED_EDITED','HANDWRITTEN')` are
  verified question→SQL pairs that exist right now and are read by nothing.
  They arrive as **proposals**, never approved templates, and a
  `GENERATED_EDITED` one is `MODEL_DERIVED`.
- **The curator decides the shape, not a router.** A correction is
  question-shaped (a template), definition-shaped (the semantic layer), or
  neither (dismiss with a reason) — three radios, §1.5's rule as an
  interaction.

## 4. Few-shot injection — the one change that can make the product worse

> **Ships off.** `knowledge_examples_enabled` defaults to false.

`PROMPT_VERSION` moves **v8 → v9** here, and the whole of that move is one slot.

- **Off renders the v8 bytes, exactly.** The slot is written
  `{schema}\n{examples}\n{history}` and `RetrievedContext.render_examples`
  returns the empty string when there is nothing to show, so it collapses to the
  newline that was already there. A connection with no store, one with
  `knowledge_examples_enabled` off (**the default**), the draft graph and the
  templates-off eval arm all take that path — which is what keeps every number
  in [eval.md](eval.md) meaningful.
- **The default is a measurement, not caution.** Eval Round 2 measured an
  unconditional addition to this exact prompt costing ten points of execution
  accuracy on a small model (36% → 26%) by crowding out the schema, and
  few-shot examples are that shape of change. The plan gates the flip on
  held-out accuracy not being worse; until [eval.md §6.1](eval.md) has both
  numbers, off is the honest default.
- **Last, and small.** Schema first, semantic layer second, examples third. At
  most four, each capped, the block capped at a fifth of what catalog comments
  get, and a long example skipped whole rather than truncated so it cannot shut
  out the short ones behind it.
- **`match` collects them on a miss, never on a hit.** A run answered from the
  store has no generator to teach. `STALE`, `CONFLICTED`, `BENCHMARK_ONLY` and
  `HELD_OUT` templates are excluded here exactly as they are from the
  short-circuit — a stale template teaching the generator a pattern the schema
  no longer supports is worse than one refusing to answer.
- **The disclosure gate is at render time**, like every other rung: a
  `MODEL_DERIVED` template's literals are withheld under `NONE`/`AGGREGATE`, and
  the *whole example* is withheld rather than stripped, because there is no way
  to remove a literal from a `WHERE` clause and leave a statement that still
  teaches anything.
- **`--templates on|off` is how it gets measured.** The arm builds a store out
  of the suite's own questions, holds out two in five deterministically, and
  excludes every record from the store it is measured against. Only the
  `held_out` row of the per-tag breakdown is worth quoting.

## 5. Store health — staleness and conflict

A curated store decays two ways, and the two have different costs, so they are
two different jobs.

- **Staleness is a parse, so it runs inline on the sync that caused it.**
  `KnowledgeService.sweep_staleness` re-validates every live template against
  the snapshot `POST /schema/sync` just wrote: `ACTIVE` → `STALE` with the
  guard's own sentence in `status_reason` (*"column `orders.region` no longer
  exists"*, plus the fix), withdrawn from matching and from few-shot, **never
  deleted**. The reverse transition is there too — a template that resolves
  again returns to `ACTIVE` on its own, without which the first bad sync is
  permanent and healing the store means editing forty rows by hand. An empty
  snapshot changes nothing: that is a broken sync, not a broken store.
- **Conflict is an execution, and it is what no competitor can do.** Fabric
  reasons over SQL *text* and reports a confidence of 1–5.
  `app/workers/knowledge_maintenance.py` finds near-duplicate normalised
  questions (0.60, measured against real pairs, not picked), binds **both** to
  the same probe values, runs both through `execute_saved_sql` — the guard's
  own door, read-only, row-capped at 500 — and compares with
  `app/knowledge/compare.py`. Differ → **both** rows `CONFLICTED`,
  `conflicts_with` populated, and the diverging rows stored in
  `conflict_evidence` from each row's own point of view. The system never picks
  a winner.
- **Probe values are derived, never invented.** A date slot gets a fixed past
  window; a string slot gets a value the *curator* declared; a string slot with
  no declared vocabulary **stops the pair**, logged with the slot's name. A
  guessed noun would compare two empty result sets and call that agreement —
  a check that reports the store healthy because it could not test it.
- **The comparator moved down a layer, and that is deliberate.** `values_equal`
  / `result_sets_match` / the tolerances now live in `app/knowledge/compare.py`
  and `app/eval/metrics.py` re-exports them. `app.eval` is offline-only by
  contract, and the conflict checker and Phase 6's in-product benchmark both
  need exactly these tolerances: one implementation, both callers, contract
  intact.
- **The customer's off switch is `connections.conflict_checks_enabled`**,
  checked *before* a connector is opened. It stops only the half that runs SQL
  on their database; the staleness sweep is a parse and keeps working.
- **The matching-mode strip is drawn for every connection, taught or not.**
  It was gated on `rows.length > 0`, which hid the control in the two states
  that need it most: a connection with nothing taught could not be switched to
  embedding search *before* teaching anything (`aurora` was in exactly that
  state, `sales` was not, and that is the whole of why the two screens
  differed), and a connection that had it on lost the only control that turns
  it off the moment its last template was archived — pin intact, invisible.
  `embeddingView` grew a `templates === 0` branch instead, because *"ready,
  and the first question is indexed as it is saved"* is a true sentence and
  *"all 0 questions indexed"* is not.
- **Pruning is surfaced, never enforced.** Ninety days with no hits earns one
  faint line and no action button. Genie caps instructions at 100 per agent;
  DataMind's version of that cap is visibility, because a template written for
  a question asked once a year is not waste.

## 6. The score — a benchmark of the customer's own

Where a connection owner gets a number about *their* data, without a developer.

- **Separate tables, deliberately.** `benchmark_sets` / `benchmark_runs` /
  `benchmark_results`, **not** `eval_runs` / `eval_results`. MVP2 Part 5's
  meta-rule: the customer-facing instrument and the frozen developer suite must
  stay architecturally separate *"or the two will contaminate each other within
  a month"*, and sharing a table is how that starts. They share a vocabulary
  and one comparator; they share no table and no import, and a test asserts the
  second on the parse.
- **Building a set withdraws its members from answering.** That is the point,
  not a side effect: §1.3's rule is that a template is retrievable **or**
  benchmarkable and never both, and it is enforced in the query the ask path
  uses. Deleting the set gives the questions back.
- **A fixed fraction is `HELD_OUT` at creation**, deterministically by sorted id
  so the split is reproducible from the set's own membership list. **That is
  the only number worth putting in front of a customer.**
- **Two numbers, and the strip says which to believe.** Held-out first and
  larger and on the sparkline; questions answered *from* a template second and
  smaller, because that one goes up for the wrong reasons. `from_template` is
  the **observed** fact of what the run did, not a label assigned before it ran.
  Genie's Evaluations tab shows one number.
- **Nothing that did not run is in a denominator.** A member whose parameters
  could not be probed, or whose stored answer no longer executes, is counted in
  `total` and in neither accuracy — and the difference is shown. An accuracy
  over a shrinking denominator always flatters.
- **No LLM judge.** Labels come from `app/knowledge/compare.py`, the same
  deterministic comparator the eval and the conflict checker use. Fabric fell
  back to a judge and gets *true / false / unclear*.
- **Runs execute in `app/workers/benchmark.py`**, through the real
  `AnalyticsPipeline` — a benchmark that measured a simplified path would
  measure something nobody experiences. A run stranded by a restart is **failed,
  not resumed**: half of it was scored against a store, a schema and a model
  that may all have moved.

## 7. Searching the store by meaning — the embedding matcher

> **Ships off.** `embedding_model` defaults to empty; `pg_trgm` is the default matcher.

`EmbeddingMatcher` sits behind the same Protocol
`LexicalMatcher` does, so this phase is a **constructor change** and the `match`
node, both thresholds, the binder, the short-circuit and the badge are untouched.

- **Masked question similarity (DAIL-SQL).** Table names, column names, the
  values a *curator* declared, and literals are all replaced with `<table>`,
  `<column>` and `<value>` before anything is embedded — so *"revenue in July
  for West"* retrieves the template written for *"revenue in March for East"*.
  Three tokens rather than one, because `revenue by <column>` and `revenue by
  <table>` are different questions.
- **The loop degrades to lexical, never to nothing.** `FallbackMatcher` reads an
  empty result from the embedding half as *"ask the trigram one"*, and every way
  it can fail produces one: no model pinned, no fresh vector, a revoked key, a
  provider that changed width. **Word matching is not a degraded state** —
  `pg_trgm` needs no provider, no key and no budget, and it is the default.
- **Staleness is derived, never tracked.** A stored vector carries the SHA-256 of
  the three things that made it (masked text, model id, width). Asking whether it
  is current is recomputing that and comparing, so a template edit, a schema
  re-sync (the mask reads the schema's own names) and a model change each
  invalidate exactly what they should — and there is no invalidation call
  anybody can forget. A vector that fails is *ignored*, never deleted.
- **No pgvector, no vector DB, no new deployment unit.** Vectors are a
  `double precision[]` beside the template and cosine is computed in
  `app/knowledge/embed.py`, for the same reason `trigram_similarity` is computed
  in the matcher: **the index narrows, the matcher decides.**
- **Availability is a capability check.** Anthropic is refused with no network
  call; anything OpenAI-compatible is *asked*, and the width that comes back is
  **measured** and pinned on the connection — two gateways serving one model
  name at different widths is a thing that happens.
- **Which provider embeds is a row, and it used to be nothing at all.**
  `_embedding_llm` resolved the owner's `llm_configs.is_default`, and
  **nothing in the product has ever written `is_default`** — no route, no
  service, no form — so the lookup returned `None` for every connection of
  every account and *"Add a default model provider first"* was the only answer
  `PUT /knowledge/embeddings` could give. Phase 7 was unreachable from the
  interface. A provider is now a candidate when it *declares* an
  `embedding_model` (`knowledge_service.can_embed`, which also refuses
  Anthropic), the connection records which one indexed it in
  `embedding_llm_config_id` — `SET NULL`, so deleting a provider releases a
  store rather than deleting it — and `is_default` survives only as a sort key.
  `tests/unit/test_embedding_provider.py` asserts on the parse that nothing
  writes it, so the sentence above cannot quietly go stale.
- **One embedder serves the deployment, and it is resolved rather than chosen.**
  `embedding_provider(db, connection)` is the only answer to "which endpoint
  makes vectors here": the connection's pin first — those vectors were made with
  it and must keep being made with it or every one of them is silently re-meant
  — then the head of `_embedding_candidates`. **Nothing names one**:
  `EmbeddingWrite` carries `enabled` and an optional `model` (the escape hatch
  for a self-hosted endpoint serving a name the row does not declare) and no
  provider field, `PUT /knowledge/embeddings` reports the resolved row back as
  `embedder`, and the knowledge panel is a switch rather than a form. The model
  a **curator** chooses is the one that *answers*, and it is offered where a
  question is asked — chat, a dashboard tile, a report — because those pick
  between behaviours a reader can judge, while two stores embedded by two
  endpoints is a fact to keep straight for no benefit: vectors are only ever
  compared inside one store. Setting up the embedder is one step in LLM
  providers, which is what the refusal sentence names.
- **Indexing is a worker's job.** `index_embeddings` is the third pass of the
  six-hourly sweep, after staleness and conflicts so it never spends a call on a
  row those two just withdrew. Turning the feature on indexes inline, so it
  works on the next question rather than in six hours.
- **`--matcher lexical|embedding` is how it gets measured**, and the report
  prints retrieval *and* execution accuracy on one line with the reason:
  FK-neighbour expansion once moved recall 70% → 86% with **flat** accuracy.

## 8. Provenance — who did what, and whose queue a flag lands in

`audit_logs` has been in the schema since migration `0001` with **nothing
writing to it**; mvp2 §D4 calls turning it on the best ratio in that document,
because a product whose positioning is *"you decide what leaves your database"*
could not prove what left.

- **Every curation write leaves a row** — template created / updated /
  archived, a store sweep, an embedding switch, a review resolved, a benchmark
  set built, deleted or run, and a flag recorded. A test asserts each of those
  route functions calls `audit.record`, on the parse: one unlogged write is
  enough to make the log untrustworthy, because a reader cannot tell a gap from
  a quiet week.
- **Three rules in `services/audit.py`, and each is how this kind of log
  rots.** (1) The row joins the caller's transaction and is never flushed on
  its own — a log that commits while the action rolls back invents history.
  (2) Failing to log never fails the action; the **opposite** posture to the
  guard's, and right for the same reason the guard's is right: this observes,
  it does not authorise. (3) `detail` carries identifiers and counts, **never**
  SQL, question text or result rows — enforced in one function rather than
  trusted at ten call sites, because a log that became a second copy of the
  store is a second thing to secure.
- **`GET /audit` is administrators only.** An audit log is a record *about
  people*; a curator needs to change their connection's knowledge and has no
  operational need to read who else did what, and from where. The actor is a
  display name, never an address — the review queue's rule.
- **`actor_ip` reads `X-Real-IP`, never `X-Forwarded-For`.** The second is
  client-settable, and a log holding an address the actor chose is worse than
  one holding none: the first is wrong and looks authoritative.
- **A flag is routed to the connection's owner, and the server says whose queue
  it went to.** `AnswerFeedbackRead.routed_to` is a display name resolved at
  write time, so the acknowledgement stays true when ownership moves — prose
  baked into the SPA would quietly start lying. Until mvp2 §D1 gives a
  connection an explicit grant list, "the owner" and "whoever can act on this"
  are the same person by construction, which is honest about the limitation
  rather than pretending.
