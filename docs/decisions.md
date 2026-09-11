# Decision register

**What has already been decided, and where each decision is argued.**

This page is an **index, not an argument**. Every row names the document that
owns the reasoning; where this page and that document disagree, that document
is right. Nothing is decided here — decisions are made in the plan or the
reference doc that owns the subject, and listed here so an agent can find out
what is settled without reading thirty files.

**Status column:** **Standing** — in force. **Reversed** — a later decision
replaced it; the row names the replacement. **Declined** — considered and
argued down, with the measurement that decided it. **Evolved** — still in
force, but its terms changed; the row says how.

---

## 1. Shape of the system

| Decision | Status | Argued in |
| --- | --- | --- |
| **A modular monolith, not microservices.** One FastAPI backend, one PostgreSQL app database, one SPA. No broker, no service mesh | Standing | [history/architecture-proposal.md](history/architecture-proposal.md) §3 |
| **Ports and adapters at exactly four seams** — LLM, target database, secrets, run execution — because those are the four things most likely to be replaced | Standing | [history/architecture-proposal.md](history/architecture-proposal.md) §6 |
| **The dependency rule is enforced, not documented.** `api → services → pipeline → reports → semantic → domain ← infra`, with eight import-linter contracts failing CI on violation | Standing | [reference/codebase.md](reference/codebase.md) §2 |
| **LiteLLM is the only provider adapter, and only `infra/llm/` may import it.** A CI grep decides whether the abstraction is real or decorative | Standing | [reference/llm-providers.md](reference/llm-providers.md) §1 |
| **LangGraph was deferred, then adopted** — nodes built LangGraph-shaped from the start made adoption a wiring change rather than a rewrite. Confined to `app/pipeline/` and `app/workers/`, held by a contract and a grep | Standing | [plans/langgraph-migration.md](plans/langgraph-migration.md) |
| **LangGraph checkpointing** | **Declined** — 88 KB of state per node, 97% of it the schema block, for a run of 5–60 seconds | [plans/langgraph-migration.md](plans/langgraph-migration.md) Phase 4 |
| **Durable clarification as a graph interrupt** | **Declined** — a reply arrives as an ordinary new run; no durable interrupt, no resume | [plans/langgraph-migration.md](plans/langgraph-migration.md) Phase 5 |
| **Celery + Redis for run execution** | **Declined for now**, with a trigger: p95 run > ~5 min, or runs must survive rolling deploys. Durability is the `runs` table plus a heartbeat | [history/architecture-proposal.md](history/architecture-proposal.md) §17 |
| **Cross-replica coordination uses Postgres, not Redis** — `LISTEN`/`NOTIFY` over the `run_events` log both replicas already write, and no second deployment unit | Standing | [reference/cross-replica.md](reference/cross-replica.md) §2 |
| **A run is claimed before it is executed**, and cancelling is a row rather than a task handle | Standing | [reference/cross-replica.md](reference/cross-replica.md) §1 |
| **`domain/entities/` stays empty.** ORM rows live in `infra/db/models.py`; the domain speaks in value objects and ports | Standing | [reference/codebase.md](reference/codebase.md) §4 |

## 2. Safety — the two things never left to the model

| Decision | Status | Argued in |
| --- | --- | --- |
| **SQL validation is AST-based and fails closed.** An unknown node type is a rejection, not a warning; names resolve against the connection's stored snapshot | Standing | [reference/security.md](reference/security.md) §4 |
| **Every entry point to the guard is unprivileged.** Five doors — the `validate` node, `execute_saved_sql`, tile save, dashboard import, knowledge templates — and the hostile corpus is replayed through each. *The moment one door is special, the guarantee is gone* | Standing | [reference/security.md](reference/security.md) §4 |
| **Containment underneath correctness.** `READ ONLY` transaction where the engine has one, a read-only role plus timeout where it does not; every engine adds a statement timeout and a row cap, and each connector proves the role cannot write by trying | Standing | [reference/security.md](reference/security.md) §5 |
| **Disclosure governs three channels, filtered at *render* time** — the result, the per-column schema hints, and the conversation history — so tightening a policy takes effect on the next question with no re-sync and no leak from the transcript | Standing | [reference/security.md](reference/security.md) §3 |
| **A conversation is pinned to one connection**, so history can never cross disclosure policies. The model may still be swapped mid-thread | Standing | [reference/pipeline-chat.md](reference/pipeline-chat.md) |
| **Credentials are AES-256-GCM with the row identity as AAD**, so a ciphertext moved between rows fails to decrypt. No read model ever exposes a password or `api_key` | Standing | [reference/security.md](reference/security.md) §6 |
| **A DDL comment is not customer data.** It is text a person wrote about the schema, so it reaches the model under *every* disclosure policy — capped, cleaned at capture, one line | Standing | [reference/catalog-metadata.md](reference/catalog-metadata.md) §3 |
| **Export is not gated by disclosure.** The policy governs what reaches the **model provider**; an export goes to the user, who is already looking at the rows | Standing | [plans/mvp2.md](plans/mvp2.md) E3 |
| **No model is asked to do arithmetic.** `plan_kpi`, `reports/facts.py` and `reports/checks.py` compute; models narrate | Standing | [reference/reports.md](reference/reports.md) §8 |

## 3. Access control

The eighteen decisions are tabulated in full at
[plans/user-management-and-access-control.md](plans/user-management-and-access-control.md)
§0.4, with ⚠️ marking the ones that would change the schema if reversed. The
load-bearing ones:

| Decision | Status | Argued in |
| --- | --- | --- |
| **One principal space.** A service user is a row in `users` with `kind='SERVICE'` — not a separate identity table — so every existing FK keeps working | Standing (reverses the earlier plan) | §0.4 #1 |
| **`roles` is a table carrying capabilities and wildcard-scoped privileges**, not a two-valued enum. `users.role` was dropped in migration `0029` | Standing (reverses the earlier plan) | §0.4 #2 |
| **A role may carry resource privileges at wildcard scope only.** Naming one resource id is what `grants` is for — mixing per-resource rows into roles is Superset's failure mode | Standing | §0.4 #3 |
| **Teams, not groups** — one word in the product and in the schema; an OIDC group maps onto a team through `(provider_id, source_id)` | Standing (reverses the earlier plan) | §0.4 #4 |
| **Permissions combine by union; most-permissive wins.** No `deny`, no priority order, no ordering-dependent evaluation — Tableau's Deny-wins model is the counter-example | Standing | §0.4 #11, §15 |
| **`create` is a capability, never a resource privilege.** You cannot hold a privilege on an instance that does not exist | Standing | §0.4 #10 |
| **An LLM config is grantable for `select` only.** `modify` is key-equivalent — a holder could repoint `base_url` and harvest the key | Standing (reverses the earlier plan's "never share") | §0.4 #6 |
| **Sharing an artifact does not share the data behind it.** A tile renders only if the viewer holds `select` on that tile's connection, re-checked at execution | Standing | §0.4 #13 |
| **There is no administrator arm.** An administrator may *grant themselves* access; the grant is a row and the escalation is an audit row | Standing | §0.4 #14 |
| **Capabilities and team membership are resolved per request, not carried in the JWT** — one indexed query, in exchange for a role change taking effect immediately | Standing | §0.4 #15 |
| **404 above 403, in exactly one place** (`services/policy.require`). A second copy turns a list endpoint into an existence oracle | Standing | [reference/access-control.md](reference/access-control.md) §2 |
| **When you grant something, grant it to a team.** A permission attached to a job survives the person leaving it | Standing | [reference/access-control.md](reference/access-control.md) §1 |
| Users, groups, roles and grants as originally planned | **Reversed** — superseded by the plan above, which names the four decisions it reverses | [history/access-control-plan.md](history/access-control-plan.md) |

## 4. The learning loop

| Decision | Status | Argued in |
| --- | --- | --- |
| **The artifact is a parameterized question→SQL template**, not a literal pair — a literal store's hit rate stays near zero, and retrofitting parameters means re-curating everything | Standing (D1) | [plans/learning-loop.md](plans/learning-loop.md) §0.2 |
| **Short-circuit first, few-shot second.** Two unknowns in one window makes the result unreadable | Standing (D2) | [plans/learning-loop.md](plans/learning-loop.md) §0.2 |
| **The matcher is an interface with a lexical default.** `pg_trgm` always works; embeddings are used only where a provider exposes them. *Word matching is not a degraded state* | Standing (D3) | [plans/learning-loop.md](plans/learning-loop.md) §0.2 |
| **Curation is gated by exactly one function**, `policy.can_curate` — no endpoint checks admin-ness directly | Standing (D4), **Evolved**: Phase 8 turned `curation_admin_only` **on** by default, meaning *administrator **or** the connection's owner* | [reference/knowledge-templates.md](reference/knowledge-templates.md) §8 |
| **The curator does not type `:params` — the AST offers them.** A tree walk over the statement the guard already parsed, with refusals *shown* rather than hidden | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §1 |
| **`note` is written for the next curator and never reaches a prompt.** The research measured more prose in the prompt lowering accuracy | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §1 |
| **A stale template fails as a value** — withdrawn, never deleted, and the run falls through to generation | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §5 |
| **The customer benchmark and the developer eval share no table.** Separate `benchmark_*` tables, asserted on the parse, so the two cannot contaminate each other | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §6 |
| **No LLM judge.** Labels come from one deterministic comparator shared by the eval, the conflict checker and the benchmark | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §6 |
| **No pgvector, no vector DB.** Vectors are a `double precision[]` beside the template; the index narrows, the matcher decides | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §7 |
| **Embedding staleness is derived, never tracked** — a SHA-256 of masked text, model id and width, so there is no invalidation call anybody can forget | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §7 |
| **Feedback is open to any signed-in user; resolving it is not.** Gating the *report* on the right to *repair* loses exactly the reports worth having | Standing | [reference/knowledge-templates.md](reference/knowledge-templates.md) §3 |

## 5. The semantic layer

| Decision | Status | Argued in |
| --- | --- | --- |
| **The layer is off-by-absence.** With no layer, or with the switch off, the prompt is **byte-identical** to before the feature existed — which is what makes an A/B possible | Standing | [reference/semantic-layer.md](reference/semantic-layer.md) |
| **Nothing unchecked is kept.** A generated metric that does not parse is dropped; a *human-written* one is flagged and kept, because deleting a person's work to hide drift is worse | Standing | [reference/semantic-layer.md](reference/semantic-layer.md) |
| **The render cap is an allocation, not a truncation** — three tiers, round-robin across tables, a line skipped whole rather than cut in half | Standing (replaced a real bug, 2026-08-30) | [reference/semantic-layer.md](reference/semantic-layer.md) |
| **A metric name means one thing.** Two metrics with one name are both refused, naming each other | Standing | [reference/semantic-layer.md](reference/semantic-layer.md) |
| **A metric is defined on its entity and browsed in a list** — one editor, two ways in, no second copy | Standing | [reference/semantic-layer.md](reference/semantic-layer.md) |
| **Joins are derived from the catalog, never asked of the model** | Standing | [reference/semantic-layer.md](reference/semantic-layer.md) |

## 6. Pipelines, charts and the three surfaces

| Decision | Status | Argued in |
| --- | --- | --- |
| **Three pipelines, one set of nodes.** `retrieve → generate → validate` is written down once, as one compiled region with two callers — so a stored statement anywhere was written against the same schema block and the same guard as a chat answer | Standing | [reference/pipeline-chat.md](reference/pipeline-chat.md) §0 |
| **Every error path is one of five postures** — fail closed, fail open, fail backwards, fail as a value, fail the run. *"What should this do when it breaks?"* is answered by naming the posture | Standing | [reference/pipeline-chat.md](reference/pipeline-chat.md) §4 |
| **`describe` halts before any SQL.** A schema question sent to `generate` becomes a query against `information_schema`, which the guard always rejects | Standing | [reference/pipeline-chat.md](reference/pipeline-chat.md) §3 |
| **`clarify` fails open** — a guessed answer shown with its SQL beats no answer — and asks at most once per exchange, enforced in the service rather than trusted to the model | Standing | [reference/pipeline-chat.md](reference/pipeline-chat.md) §3 |
| **The model proposes a chart; the platform decides.** The veto runs *before* the model call, so an unchartable result costs no tokens | Standing | [reference/charts.md](reference/charts.md) §5 |
| **Prompt/type parity.** A chart type is added when the prompt describes when to pick it *and* the profile carries the facts that rule is stated in terms of — not when the compiler can draw it | Standing | [reference/charts.md](reference/charts.md) §5 |
| **The chart palette is measured, not chosen** (OKLab ΔE, CVD simulation, contrast per mode) and re-checked by a test. There is no free hex picker | Standing | [reference/charts.md](reference/charts.md) §8 |
| **Nothing calls a model at refresh time.** The most load-bearing "no" in the product: a dashboard keeps working after the provider key is revoked | Standing | [reference/dashboards.md](reference/dashboards.md) |
| **A tile failure is a value, not an exception.** One broken tile never fails the dashboard response | Standing | [reference/dashboards.md](reference/dashboards.md) |
| **The tile cache is in Postgres and fingerprints `(connection, sql, max_rows, chart_config)`** — not the SQL alone. `table_config` is deliberately excluded: renaming a column header must not query the customer's database | Standing | [reference/dashboards.md](reference/dashboards.md) |
| **One timer per open dashboard, not one per tile**, pausing on `document.hidden` | Standing | [reference/dashboards.md](reference/dashboards.md) |
| **A report's connection is pinned and its model is swappable**, exactly as a conversation's is | Standing | [reference/reports.md](reference/reports.md) §3 |
| **Reports refuse narrow disclosure.** A document whose charts carry real numbers and whose paragraphs carry none is worse than no document | Standing | [reference/reports.md](reference/reports.md) §3 |
| **A report run is not atomic** — its status is *derived* from its sections, which is what makes progressive rendering and per-section retry fall out for free | Standing | [reference/reports.md](reference/reports.md) §3 |
| **Time windows relativize in the SQL itself**, so a report re-run months later resolves them against the database's own clock | Standing | [reference/reports.md](reference/reports.md) §4 |
| **PDF is printed by the browser** — the charts are already SVG and the bidi is already right | Standing | [reference/reports.md](reference/reports.md) §12 |

## 7. Frontend

| Decision | Status | Argued in |
| --- | --- | --- |
| **No component library, and no new dependency to get one.** The design system is custom, on oklch CSS variables | Standing | [reference/frontend.md](reference/frontend.md) §4 |
| **A *data* router (`createBrowserRouter`), not `<BrowserRouter>`** — `useBlocker` exists only on that one, and it is what stops a navigation out of a dirty form | Standing | [reference/frontend.md](reference/frontend.md) §1 |
| **Every screen has a URL.** Before the UI remediation, `react-router-dom` was a declared dependency imported nowhere and nothing in the product had an address | Standing | [history/ui-improvement-plan.md](history/ui-improvement-plan.md) Phase 1 |
| **Never hardcode a colour.** Every value comes from `theme/tokens.ts`, which ships a dark and a light definition for each; chart colours are the one exception and live in `palette.ts` | Standing | [reference/frontend.md](reference/frontend.md) §4 |
| **Status is never colour alone** — every state carries a glyph and a word, so the screen survives greyscale and the print stylesheet | Standing | [reference/frontend.md](reference/frontend.md) §5 |
| **Administration is one `/admin` section with six tabs**, not four more rail rows — the rail is seven flat rows and its ordering carries the grouping | Standing | [plans/user-management-and-access-control.md](plans/user-management-and-access-control.md) §21 |
| **The knowledge console is a rail entry at `/knowledge` as well as a connection tab** | Standing — **supersedes** the learning loop plan's §4.2 information architecture | [reference/frontend.md](reference/frontend.md) §2 |

## 8. Evaluation

| Decision | Status | Argued in |
| --- | --- | --- |
| **The golden set is frozen.** Questions are never edited to make a score go up; `gold_sql` changes only when demonstrably wrong, with the evidence in `suites/CHANGELOG.md`. *An eval you are allowed to edit measures your willingness to edit it* | Standing | [reference/eval.md](reference/eval.md) §2 |
| **Golds are checked against something other than themselves** — each record has a structurally different twin, and adding a question means adding its twin | Standing | [reference/eval.md](reference/eval.md) §2 |
| **The eval runs the real pipeline**, not a reimplementation, and an import-linter contract keeps `app.eval` off the request path | Standing | [reference/eval.md](reference/eval.md) §1 |
| **The eval is not in `make test`** — it calls a real provider and costs money | Standing | [reference/eval.md](reference/eval.md) §4 |
| **The retrieve budget is lowered by a runner flag, never a code edit** — the shipped ceiling is the one the request path must use | Standing | [reference/eval.md](reference/eval.md) §6 |
| **Historical `prompt_version` rows were deliberately not rewritten.** A backfill would invent a version for a run nobody can re-render | Standing | [reference/eval.md](reference/eval.md) §6 |

## 9. Catalog metadata

| Decision | Status | Argued in |
| --- | --- | --- |
| **The semantic layer wins per entity**; DDL comments fill what it does not describe | Standing | [reference/catalog-metadata.md](reference/catalog-metadata.md) §4 |
| **Every catalog read is wrapped in `suppress`.** A comment is an accuracy aid, never a correctness dependency — a role that cannot read the catalog must still get a snapshot | Standing | [reference/catalog-metadata.md](reference/catalog-metadata.md) §1 |
| **Clean at capture, never at render**, so every consumer inherits one hygiene | Standing | [reference/catalog-metadata.md](reference/catalog-metadata.md) §1 |
| **Use engine catalogs (`pg_catalog`, `sys.*`, `ALL_*`), not `information_schema`** — under a read-only role the latter is privilege-filtered and silently drops constraints | Standing | [reference/catalog-metadata.md](reference/catalog-metadata.md) §1 |
| *"Comments improved accuracy"* | **Not decided, and must not be written.** The A/B measured 40.0% uncommented vs 36.0% commented — two questions inside a twelve-question variance | [reference/catalog-metadata.md](reference/catalog-metadata.md) §10 |

## 10. What MVP2 may not trade away

Five things MVP1 got right that MVP2 will be tempted to give up. Each is
already an invariant; each is under pressure from something proposed.

1. **The guard's fail-closed posture, and no privileged entry point.**
2. **Disclosure governing all three channels, filtered at render time.**
3. **Nothing calls a model at refresh time.**
4. **No model is asked to do arithmetic.**
5. **The live step trail, and the SQL shown every time.**

Argued in [plans/mvp2.md](plans/mvp2.md) Part 5.

---

## Adding to this page

Add a row when a decision is made that a future reader could otherwise
plausibly reverse by accident. Put the *argument* in the document that owns the
subject, and the *pointer* here. If a decision is reversed, do not delete its
row — change its status and name the replacement, because the fact that it was
once decided the other way is usually the most useful thing about it.
