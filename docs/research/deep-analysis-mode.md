# A second answer mode — what "deep analysis" is, and how five products build it

> **Subject:** mvp2 [F3](../plans/mvp2.md#f3-bounded-multi-step-analysis--deep-dive--l)
> (bounded multi-step analysis) and [F4](../plans/mvp2.md) (root-cause / key
> drivers), both currently Tier 3 and deferred in
> [status.md §5](../status.md#5-deferred-on-purpose-with-triggers). This note is
> the evidence for reopening them: who ships a second mode, what it is made of,
> what the literature says the techniques cost, and what shape fits *this*
> codebase without breaking the five invariants in
> [mvp2 Part 5](../plans/mvp2.md#part-5--what-not-to-break).
>
> **Field:** Power BI (+ Microsoft Fabric data agents) · Wren AI · Databricks
> AI/BI Genie · Apache Superset · Metabase — the five named in the request —
> plus four products that have shipped more of this than any of the five:
> ThoughtSpot, Amazon Q in QuickSight, Snowflake Cortex Agents, Looker.
>
> **Desk research date:** 2026-09-21. Every competitor claim is sourced in §7.
> Vendor blog copy is marked as such and is **not** treated as documentation.
> Anything not verifiable from a public page is `?`, never guessed.
>
> **Read as argument, never as a description of this codebase** — that is
> [`reference/`](../reference/)'s job. Where this note and
> [status.md](../status.md) disagree, status.md is right.

---

## 0. The one-paragraph answer

**The split you are proposing is now the industry-standard shape, and every
product that ships it ships it as a *separate, opt-in mode* rather than as a
smarter default.** The reason is uniform and is the one you already wrote in
mvp2 F3: a single-shot answer is 5–60 seconds and a multi-step one is minutes,
and the fast path is a feature you cannot regress. What the mode does is also
convergent: it plans, it issues *several* guarded queries instead of one, it
reflects on each result before choosing the next, it stops on an explicit
budget, and it returns a **report with citations back to the individual
queries** rather than one table. The interesting variation is in three places
only — (a) whether a deterministic compute step sits between queries, (b) who
decides when the mode fires, and (c) whether the intermediate arithmetic is
done by the model or by the platform. DataMind is unusually well positioned on
(c), because `reports/facts.py`, `pipeline/checks.py` and `charts/` already
encode the "model proposes, platform disposes" split that root-cause analysis
demands, and it is unusually well positioned on trust generally, because the
guard and `disclose()` already govern every door. **The honest risk is not
architectural, it is economic and evaluative:** a deep mode costs roughly an
order of magnitude more tokens per answer, and this repo currently cannot count
cache tokens, cannot score a multi-query answer, and has a documented
single-shot execution accuracy of 0.36 on its own messy fixture. §6 says what
to do about that.

**One concern, stated once and then set aside.** F3's own written trigger is
*"Tier 1 accuracy is credible and users start asking why."* Tier 1 is ten of
twelve done and the 0.36 baseline is from `PROMPT_VERSION` v2 in July — it is
stale, not necessarily still true, and nobody has re-measured it. A deep mode
built on a generator that is wrong two times in three does not produce a better
answer; it produces a longer, more confident, more expensive wrong answer, with
eight queries' worth of surface area for the error to hide in. The design below
is complete and buildable. §6.1 is the one measurement to take before building
it.

---

## 1. Who ships a second mode

### 1.1 Databricks AI/BI Genie — **Agent mode**. The most complete implementation in the field.

This is the reference implementation of exactly what you described, and it is
worth reading its documentation directly rather than this summary.

| | |
|---|---|
| **Shape** | A mode toggle inside a Genie Agent, beside ordinary Chat. Standard mode "generates a single read-only SQL query"; Agent mode does not. |
| **Loop** | Creates and *refines a research plan* — "a structured approach and **hypotheses**" — then runs multiple SQL queries, "learns from each result", and iterates until confident. A published example shows **8 queries** for one analysis. |
| **Escalation** | It "dynamically scales its reasoning to the complexity of the task — moving quickly for simple prompts, and spending more time planning and evaluating for deeper investigations." (Vendor blog wording.) |
| **Clarification** | It "may ask follow-up questions to clarify your intent" *after* beginning research — not as a gate before it. |
| **Output** | A report: findings, visualizations, supporting tables, and **citations to the research steps**, with the underlying SQL available per claim. PDF export. |
| **Interruptibility** | An **Answer now** button stops the reasoning and answers from the context gathered so far. Cancellation is also exposed through the API (2026-08-26). |
| **Transparency** | Reasoning traces are a first-class, API-readable object (`GenieQueryAttachments`, Public Preview 2026-04-16). |
| **Human review** | A reader can **flag a report for manual review** with a comment; an author with `CAN MANAGE` reviews, responds, and *confirms or corrects* it. |
| **Scope limits** | Up to **50** tables/views/metric views per agent (raised from 30 on 2026-09-10); up to 200,000 conversations (raised from 10,000). |
| **Unstructured** | Agent mode — and *not* Chat mode — can read PDFs, slide decks and images from attached Unity Catalog volumes (≤10), under Unity Catalog permissions. |
| **Maturity** | Public Preview 2026-04-09, **GA 2026-07-02**. Agent mode APIs GA 2026-08-27. |
| **Cost posture** | Free through 2027-01-31; pay-as-you-go from 2026-07-08 with 150 free DBUs/month for LLM usage, and explicit **budget controls**. |

**What to take from it, in order of value:** the human-review loop on a
*report* (DataMind has the same instinct already, in the knowledge store's
feedback → template path); citations as a typed object rather than prose; and
**Answer now**, which is the cheapest possible answer to "this mode is
unbounded and I am waiting."

**What is missing from the public record:** no latency figures, no accuracy
benchmark against standard mode, no architecture beyond "plan, query, reflect."
Do not cite Genie as evidence that the approach *works* — only that it ships.

### 1.2 Power BI / Microsoft Fabric — the split is between two *products*, not two modes

Microsoft does not put a toggle in the chat box. It puts the depth in a
different artifact, which is a materially different answer to the same problem:

- **Copilot in Power BI** is the shallow tier — summarize a page, build a
  visual, write DAX, answer about one report or semantic model. There is now a
  full-screen **standalone Copilot** that searches across every report, semantic
  model and data agent you can see.
- **Fabric data agents** are the deep tier: a standalone Q&A service over **up
  to five data sources** with cross-source reasoning, custom instructions, a
  business glossary, **verified Q&A pairs**, conversation memory, audit trails,
  and a REST API. Copilot *invokes* a data agent when a question spans reports
  or needs the right source found first.
- The mechanism is named plainly in Microsoft's own documentation: **"Advanced
  DAX generation uses multiple reasoning steps to inspect model metadata,
  interpret the question, resolve ambiguity, and generate the DAX query, unlike
  standard DAX generation which generates a query in a single pass."** That is
  the single-shot-versus-multi-step split stated as a product setting.
- A **code interpreter tool (preview)** can be enabled for a data agent, giving
  it a sandboxed Python environment for computation and visualization over
  retrieved data.

**What to take from it:** "verified Q&A pairs" is DataMind's knowledge store
under another name, and Microsoft feeds it to the *deep* tier. That is a strong
signal for wiring `app/knowledge/` into a deep run's planner, not just into
`match`.

### 1.3 Wren AI — the reasoning is in the loop, but there is no branded deep mode

Wren AI is the closest architectural sibling to DataMind and the one whose
public writing is most useful, because it documents *technique* rather than
*feature*:

- **MDL (Metadata Definition Language)** is its context layer — business
  entities, relationships, centralized metric definitions — explicitly there so
  the model cannot "arbitrarily invent entities or metrics that don't exist."
  This is DataMind's `app/semantic/`.
- **CoT + ReAct**: chain-of-thought to "break down complex SQL queries step by
  step", ReAct to "interact dynamically with database schemas".
- **Dry-run validation loop**: generated SQL is executed against the source, and
  on error the failure is fed back for regeneration — "an immediate loop of
  trial and correction." This is DataMind's repair region, except DataMind's
  first gate is *static* (SQLGlot AST allowlist) rather than execution, which is
  strictly stronger and strictly cheaper.
- **Reasoning summary, optionally displayed** "for advanced users, allowing them
  to verify the query logic." Progressive disclosure of the trail.

**No separately-branded multi-step research mode is documented** as of this
note's date; the repo reorganised in May 2026 (Wren Engine merged into the main
repository; the v1 GenBI app preserved on `legacy/v1`). Treat Wren as evidence
for *technique*, not for the mode.

### 1.4 Metabase — documented as **not** having it

Metabot's own documentation lists, under limitations, that it cannot "create
multi-step analyses." Its three modes — chat sidebar, inline SQL generation in
the native editor, and a standalone *AI exploration* — are all discrete
request/response cycles within a conversation. Other documented limits: no
parameterized SQL, no chart formatting changes, no alert management.

What Metabase *has* built instead is the governance layer around single-shot
AI, and that is the more instructive part: v61 (May 2026) added **per-group
access controls, token and message limits, customizable system prompts, and AI
usage analytics**; v60 added an MCP server, Slack, and bring-your-own-model;
v63 added OpenAI, Bedrock and Azure as providers. Core AI is an add-on.

**What to take from it:** *token and message limits per group* is the control a
deep mode needs and that DataMind's usage screen deliberately does not have —
[status.md §2](../status.md#2-what-mvp2-has-landed) says the usage subsystem
"measures, it does not enforce." A deep mode is the trigger to revisit that,
because a cap that fails closed is exactly what an unbounded loop needs.

### 1.5 Apache Superset — no native text-to-SQL at all, therefore no second mode

Unchanged from [competitive-matrix.md §1.1](competitive-matrix.md): AI reaches
Superset through an admin-deployed **MCP companion service** (5.0+), through
Preset's commercial *AI Assist*, or through extensions such as the Vambery
sidebar in SQL Lab (public beta). **[SIP-166](https://github.com/apache/superset/issues/33215)**
proposes an in-core assistant and has not landed — and its stated design is
pointedly the opposite of this note's subject: *"intentionally simple — avoiding
the use of RAG, vector databases, or agentic LLM frameworks"* for maximum
compatibility.

Superset is the useful counter-example. A serious project looked at the same
problem and chose **no agent at all**, because portability across dozens of
engines mattered more. Its MCP posture also means the depth happens in *Claude
or ChatGPT*, not in Superset — the client is the agent and Superset is the tool.
That is a real fourth option and it costs almost nothing to support: DataMind's
own MCP server (mvp2 E2, [research/mcp-server.md](mcp-server.md)) would give
users a deep mode built by somebody else.

### 1.6 The four outside the named field that have shipped more

| Product | What it ships | The idea worth stealing |
|---|---|---|
| **ThoughtSpot SpotIQ — change analysis** | Compare two points in a visualization; identify **key change drivers** from underlying attribute columns. | **It is not one algorithm — it is three, chosen by measure type.** §3.4. This is the single most directly usable finding in this note for F4. |
| **Amazon Q in QuickSight — Scenarios** | Describe a problem; the agent finds data, **formulates a plan**, executes steps, summarizes with suggested actions — and "shows its analysis process so the user can follow along." | The mode is framed as *problem-solving*, not question-answering, and it is a **separate surface** from the Q&A box. Framing changes what users expect of the latency. |
| **Snowflake Cortex Agents** | An explicit three-step reasoning loop: **Plan** (parse, disambiguate, split into subtasks, choose a tool per subtask) → **Act** (call Cortex Analyst for structured, Cortex Search for unstructured, or a code-execution tool) → reflect. | The planner's output is a *tool assignment per subtask*, not free-form reasoning. That is a typed, inspectable, testable artifact — and it is what makes the loop bounded. |
| **Looker Conversational Analytics + Code Interpreter** | NL → **Python** → execute, for analysis beyond what the semantic model expresses, over the Looker semantic layer. | The escape hatch for questions SQL cannot express (§3.2), with the semantic layer still in front of it. |

### 1.7 The matrix

`●` shipped and documented · `◐` partial, or vendor-claim only · `○` absent ·
`—` not applicable

| | Genie | Power BI/Fabric | Wren AI | Metabase | Superset | **DataMind today** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| A second, slower, deliberate answer mode | ● | ◐ *(separate product)* | ○ | ○ | ○ | **○** |
| Explicit plan/hypotheses shown to the user | ● | ◐ | ◐ *(reasoning summary)* | ○ | ○ | **◐** *(step trail)* |
| Multiple queries per question | ● | ● | ○ | ○ | ○ | **○** |
| Reflection between queries | ● | ● | ◐ *(repair only)* | ○ | ○ | **◐** *(repair only)* |
| Deterministic compute between queries | ◐ | ● *(code interpreter, preview)* | ○ | ○ | ○ | **◐** *(`reports/facts.py`, report path only)* |
| Root-cause / key-driver analysis | ◐ | ◐ *(decomposition tree, not agentic)* | ○ | ○ | ○ | **○** |
| Report with per-claim citations | ● | ◐ | ○ | ○ | ○ | **◐** *(reports, not chat)* |
| Stop-and-answer-now | ● | ? | ? | ? | — | **◐** *(cancel, not answer-now)* |
| Per-run budget the operator sets | ● | ◐ | ? | ● *(token/message limits)* | — | **○** *(measures, does not enforce)* |
| Every generated statement statically validated before execution | ○ | ○ | ○ *(dry-run = execution)* | ○ | ○ | **●** |
| Every intermediate result through a disclosure policy | ○ | ○ | ○ | ○ | — | **●** *(by construction, if reused)* |
| Human review loop on the produced report | ● | ○ | ○ | ○ | ○ | **◐** *(feedback → template)* |

**The row that matters** is the third from the bottom. No competitor
statically proves a generated statement is read-only before running it. A deep
mode multiplies the number of generated statements per question by five to ten.
**That is the row where DataMind's lead widens rather than narrows as the mode
gets deeper** — and it is the argument for building this, rather than the
argument against.

---

## 2. The seven decisions, and how the field answered each

Every implementation above is a set of answers to the same seven questions.
This is the part to design against.

### 2.1 Separate mode, or auto-escalation?

**Field answer: separate mode, with escalation inside it.** Genie has a toggle
*and* "dynamically scales its reasoning." Microsoft has two products. Amazon Q
has a separate surface. Nobody silently turns a 5-second answer into a
4-minute one.

The reason is not technical, it is about the contract with the reader. A
person who typed a question and is watching a cursor has a latency budget in
their head. Breaking it is worse than answering shallowly. Anthropic's own
published principle for its research system is *"scale effort to query
complexity"* — but that scaling happens **within** a mode the user chose.

**Recommendation for DataMind:** an explicit control in the composer, next to
*Ask within…* — which already establishes that the composer carries per-question
routing decisions. Auto-escalation only as a *suggestion* on a finished shallow
answer ("this looks like a *why* question — run a deep analysis?"), never as a
silent upgrade.

### 2.2 Planning: plan-and-execute, ReAct, or iterative refinement?

The deep-research survey (arXiv 2512.02038) separates these cleanly:

| Pattern | What it is | Cost/behaviour |
|---|---|---|
| **Plan-and-execute** | Decompose the whole task up front into a structured action sequence, then run it. | Efficient resource allocation, budgets are trivially enforceable, the plan is inspectable and approvable — **but it struggles with dynamic discovery**, because the plan was written before any data was seen. |
| **ReAct** | Interleave reasoning and action in a tight loop. | Real-time adaptation to findings; **trades planning overhead for responsiveness**; hardest to budget, because there is no declared end. |
| **Iterative refinement** | Multiple passes; reformulate from intermediate results. | Balances the two; highest wall-clock. |

FDABench (arXiv 2509.02473), which benchmarks *data agents on analytical
queries* specifically, reports the cost of the choice: **planning workflows
have the lowest latency at every difficulty level; reflection workflows run
2–3× higher; the slowest analytical system is about 2.3× the fastest.** Its
design lessons are worth quoting because they contradict the naive instinct:
*iterative refinement and error recovery beat single-pass planning for
robustness*, while *minimizing unnecessary queries cuts both latency and cost
without hurting accuracy*.

**The synthesis the field has converged on, and the one to build:** a
**declared plan that is allowed to be revised**, with a hard step ceiling. Plan
first (cheap, inspectable, budgetable, approvable); permit the executor to
*replace a remaining step* based on what it found; never permit it to add
steps beyond the ceiling. Genie's "creates **and refines** a research plan" is
exactly this. Cortex Agents' Plan step, which emits a *tool assignment per
subtask*, is the typed version of it.

### 2.3 What is the unit of work?

Three candidates are in the field, and they are not equivalent:

1. **A query** (Genie's "runs multiple SQL queries") — simplest, and every
   query is independently guardable and citable.
2. **A sub-question** (Cortex's "split complex requests into subtasks") — maps
   to the semantic layer's vocabulary and to a report's sections.
3. **A hypothesis** (Genie's "develops … hypotheses"; the published example
   confirms the observation first, then generates candidate explanations) —
   the unit a human analyst actually uses for a *why* question.

**Recommendation:** the unit is a **sub-question that carries an intent**
(`CONFIRM` / `DECOMPOSE` / `COMPARE` / `DRILL` / `CHECK`), and a sub-question
produces one-to-many queries through the *existing* `generate → validate`
region. This keeps the plan readable by a human (it is a list of questions, not
a list of SQL), keeps every query on the existing guarded road, and makes the
intent a routing key for the deterministic tools in §3.4.

### 2.4 Where does computation happen?

This is the decision that separates products that can answer "why" from
products that can only answer "what."

- **SQL-only** (Genie, as documented): everything is expressed as another
  query. Simple; no new execution surface; but ratio decomposition, period-over-
  period contribution and cross-source arithmetic become gnarly SQL the model
  has to get right.
- **Sandboxed code interpreter** (Fabric data agents; Looker; Amazon Q; Cortex's
  code-execution tool): a Python sandbox with no network, no subprocess, no
  package installation, an isolated filesystem, and **intermediate results kept
  in the sandbox rather than in the model's context**. The documented pattern is
  that exact operations — counts, aggregates, date ordering, conditional rules —
  "run in the interpreter where they're reliable, rather than inferred through
  reasoning." DuckDB is the common engine: columnar, embedded, zero server, and
  one isolated database file per session.
- **Fixed deterministic tools** — a closed set of named, pure functions the
  planner may call (contribution analysis, period comparison, outlier scoring).

**Recommendation for DataMind: the third, and only the third, to begin with.**
mvp2 F3 proposes DuckDB and that is a reasonable eventual answer, but a code
interpreter is a **new execution surface**, and
[mvp2 Part 5 §1](../plans/mvp2.md#part-5--what-not-to-break) is explicit that
every new door replays the hostile corpus. A closed set of pure Python
functions over already-executed rows is *not a new door at all* — it is exactly
what `reports/facts.py` (591 lines of arithmetic over result rows) and
`reports/checks.py` already are, and it inherits their property that **no model
is asked to do arithmetic**. Defer the sandbox until a real question demands
something the closed set cannot express, and write that trigger down.

### 2.5 How does it stop?

The field's answers, all of which are needed together:

| Bound | Who has it | DataMind's existing hook |
|---|---|---|
| Step / query ceiling | Genie (implicit), all agent frameworks | none — `max_repairs` bounds repairs, not steps |
| Wall-clock deadline | — | **`RunState.deadline_at`, checked before every node** |
| Row ceiling per query | Genie | **`max_rows`, enforced by the guard's rewriter** |
| Token / message budget | Metabase (per group), Genie (DBU budget controls) | **measured per node/operation/user, not enforced** |
| Scope ceiling | Genie: 50 tables per agent | **`scope` node + sections + the retrieve budget** |
| User interrupt → *answer from what you have* | Genie's **Answer now** | **cancel exists; answer-now does not** |

**Answer now is the one to copy deliberately.** DataMind already has
cancel-as-a-row across replicas ([cross-replica.md](../reference/cross-replica.md));
the difference between *cancel* and *answer now* is that the latter is a
cooperative signal that skips remaining plan steps and jumps straight to
synthesis over the evidence already collected. The report worker's
cooperative-then-hard cancel is the nearest existing pattern.

### 2.6 What does it return?

Uniformly: **a report, not a table.** Findings, visualizations, supporting
tables, and citations back to the individual research steps, with the SQL
reachable per claim. Genie exports to PDF. Amazon Q adds "what the findings
might mean for the business with suggested actions."

DataMind has already built this artifact once — a report is an outline, a
statement per block, prose per section, and a summary, with numeric consistency
checked by `reports/checks.py` in Persian and Latin numerals. **A deep chat
answer is a report whose outline was proposed and approved by the system
instead of by a person.** That is the single largest piece of reuse available.

### 2.7 How is it trusted?

- **Show the trail.** Genie makes reasoning traces an API object. Wren shows an
  optional reasoning summary. Amazon Q "shows its analysis process." DataMind
  already streams `STEP_STARTED` / `STEP_FINISHED` / `SQL_GENERATED` /
  `SQL_VALIDATED` / `RESULT_CHECKED` / `REASONING_DELTA` over SSE, and mvp2 Part
  5 §5 already names Data Formulator's finding: *"agents can be difficult to
  control if they are working in a black box."*
- **Human review of the produced report.** Genie's flag → author reviews →
  confirms or corrects. DataMind's feedback → curation → template is the same
  loop aimed at a different artifact.
- **Citations as structure, not prose.** A claim links to a step; a step links
  to SQL; SQL links to a result. Prose that merely *mentions* a number is not a
  citation.

---

## 3. The techniques, from the literature

### 3.1 Decomposition is the oldest and best-measured idea here

Multi-agent text-to-SQL frameworks decompose the task into specialized stages —
schema linking, generation, refinement — handled by distinct modules:

- **MAC-SQL** (arXiv 2312.11242): a core *Decomposer* with few-shot CoT, plus a
  *Selector* that narrows the schema to a sub-database and a *Refiner* that
  fixes erroneous SQL. Built specifically for "large databases and complex user
  questions requiring multi-step reasoning."
- **CHESS**: four modules — Information Retriever, Schema Selector, Candidate
  Generator, Unit Tester — composable into workflows, *with the LLM deciding
  which modules to use*. The Unit Tester is the unusual one and the one worth
  noting: generated candidates are tested, not trusted.
- Reported on **BIRD** with GPT-4: vanilla 46.35% → MAC-SQL 57.56% → MAG-SQL
  61.08%. **These three numbers share a model and a benchmark, which is why they
  may sit in one sentence** — the repo's own rule. Do not carry them across to
  any other model.

**The load-bearing observation for DataMind:** two of MAC-SQL's three agents
already exist here. `scope` + `retrieve` *are* the Selector (and are better —
they are FK-aware and section-aware rather than model-guessed); `validate` +
the repair region *are* the Refiner (and are better — static AST validation
before execution, not error text after it). **What is missing is only the
Decomposer.** That is a genuinely small delta, and it is the strongest
structural argument in this note.

### 3.2 TAG — why some questions are not SQL questions

"Text2SQL is Not Enough: Unifying AI and Databases with TAG" (arXiv 2408.14717,
Berkeley/Stanford) makes the argument the mode exists to serve: text-to-SQL
addresses only questions expressible in **relational algebra**, and RAG only
questions answerable by point lookups. Neither covers questions needing world
knowledge or semantic reasoning over the rows. TAG's three steps — **query
synthesis → query execution → answer generation**, with iterative and recursive
generation patterns — are the abstract form of the mode. The companion line of
work, **LOTUS / semantic operators** (arXiv 2407.11418), adds `sem_filter`,
`sem_join`, `sem_agg` as first-class relational operators over LLM calls.

**What to take, and what to leave.** Take the diagnosis: the reason a second
mode exists is that a class of real questions has no single SQL answer, and
that class is larger than it looks. Leave the implementation — semantic
operators run a model *per row*, which is categorically incompatible with
`disclose()` at `SAMPLE` or below and would be a new and very wide door. If it
ever becomes interesting, it needs its own disclosure-ladder decision, exactly
as entity/value dictionaries (mvp2 B3) does.

### 3.3 Orchestrator-worker, and what it costs

Anthropic's published account of its own multi-agent research system is the
best-documented cost model available:

- A **lead agent plans and saves the plan to memory**, spawns 3–5 subagents in
  parallel with independent context windows, and reconciles their condensed
  findings with a **separate citation pass**.
- Result: **+90.2% over single-agent Claude Opus 4** on their internal research
  eval — while **token usage alone explains ~80% of performance variance**, and
  the system costs about **15× the tokens of a chat interaction**.
- Their stated condition: *"architecture follows task structure — multi-agent
  systems only win when the task decomposes into independent parallel
  threads."*

**Two things follow directly for DataMind.**

First, the 15× is the number to design the budget around, and it interacts
badly with two facts already recorded in this repo:
[status.md §2](../status.md#2-what-mvp2-has-landed) says **cache read and cache
write tokens are not counted** (the schema has only `prompt_tokens` and
`completion_tokens`, and `Usage` is a port type), and
[plans/langgraph-migration.md](../plans/langgraph-migration.md) Phase 4 measured
**88 KB of state per node, 97% of it the schema block**. A mode that re-sends
that block once per plan step is precisely the workload prompt caching exists
for, and precisely the workload this repo cannot currently measure. **Widening
`Usage` is a prerequisite, not a follow-up.**

Second, the parallelism condition is *not* met by most BI questions. "Why did
revenue drop in March?" decomposes into steps that are **causally ordered** —
you cannot pick which dimensions to test before you have confirmed the drop and
seen its shape. Run the plan mostly sequentially and parallelise only within a
step (the *N* dimension slices of one contribution analysis are genuinely
independent, and `asyncio.gather` over them is the same pattern
`DashboardService.refresh` already uses). **Do not build an orchestrator-worker
fan-out of sub-agents here.** It is the expensive pattern and the task shape
does not pay for it.

### 3.4 Root-cause arithmetic — the most directly usable finding in this note

For F4, do not invent the math. Two sources give it:

**ThoughtSpot SpotIQ change analysis** uses **three different algorithms
selected by measure type** — which is the insight, because a single
"contribution = Δ(segment) / Δ(total)" formula is wrong for two of the three:

| Measure class | Examples | Method |
|---|---|---|
| **Simply decomposable** | `SUM`, `COUNT` | Threshold-based outlier detection: examine the top ten absolute changes, stop when one timestamp's contribution exceeds 50%, flag values outside the derived upper/lower thresholds. |
| **Ratio-based** | `AVG`, `SUM/SUM` | Difference analysis is mathematically unstable, so compute a **hypothetical percentage change** per dimension value — *what the overall change would have been if this value had not changed*. Smaller hypothetical ⇒ stronger explanation. |
| **Complex** | `COUNT(DISTINCT)` | Z-score over the change distribution, with N between **2.0 and 5.0 chosen by dimension cardinality** — higher cardinality, stricter threshold. |

Its documented limits are also worth copying as refusals rather than
rediscovering: it refuses "growth of"/"versus" phrasings, complex `group_*`
formulas, and mixed attribute types.

**Adtributor** (Microsoft Research, revenue debugging in ad systems) is the
academic backbone: it scores candidate explanations on **explanatory power,
succinctness, and surprise**, reported >95% accuracy in its domain. Its known
limitation — it finds root causes in a **single attribute** — is why
**R-Adtributor** exists, applying it recursively for multi-attribute causes.
Later work (RiskLoc, CMMD, counterfactual-Shapley attribution) extends this;
none of it is needed for a first version.

**Why this fits DataMind exactly.** mvp2 Part 5 §4 — *"no model is asked to do
arithmetic"* — makes F4 the cleanest case in the product for the
model-proposes/platform-disposes split: **the model picks which dimensions to
test; a pure, token-free module computes what actually moved.** That module
belongs beside `reports/facts.py` and `pipeline/checks.py`, is unit-testable
with no provider, and costs zero tokens. It is also the piece that makes deep
mode *better than a human with a SQL editor* rather than merely faster.

### 3.5 Evaluation is where this will actually hurt

- **Spider 2.0** moved text-to-SQL to enterprise reality — real codebases, dbt
  models, auxiliary documentation, agents that "interactively reason, act and
  observe within real shell/DB environments." The headline finding is the
  collapse: **execution accuracy around 21% against >90% on the original
  Spider's single-turn setting.** Aggregator leaderboards report considerably
  higher figures for 2026 frontier models on *some* Spider 2.0 variants; the
  variants differ and the sources are secondary, so treat any single Spider 2.0
  number as unreliable and never compare two of them.
- **Scoring a deep answer is not execution accuracy.** There is no single
  `gold_sql` to compare against. The field's default is **LLM-as-judge**, whose
  documented failure modes are length/verbosity bias, position bias, and
  self-preference for the judge's own style — plus a full model call per
  judgment.
- The current consensus framing is to **score four axes together — accuracy,
  cost, latency, safety** — on a Pareto frontier, "so a high accuracy number
  cannot hide a 50× cost increase."

**What DataMind can score without a judge, and should score first:**

| Metric | Computed from | Provider needed |
|---|---|---|
| Guard pass rate across all sub-queries | `validate` verdicts | no |
| Execution success rate across all sub-queries | `execute` outcomes | no |
| **Claim traceability** — every number in the prose appears in some cited result | `reports/checks.py`'s existing numeric consistency check, applied per claim | **no** |
| Plan adherence — steps executed vs steps declared | plan artifact vs `run_steps` | no |
| Queries per answer, tokens per answer, wall-clock | usage accounting + `runs` | no |
| Final-answer correctness on a frozen deep set | comparator or judge | yes |

The first five are free and catch most of what will go wrong. **Claim
traceability is the important one** — it is the property that distinguishes a
deep answer from a long hallucination, this repo already has the machinery to
compute it, and no competitor documents having it.

And the eval charter's rule applies with full force here: *"an eval you are
allowed to edit measures your willingness to edit it."* A deep set must be
frozen the day it is written.

---

## 4. What DataMind already has that the field does not

Before designing, the honest inventory — because it determines how much of §5
is new code and how much is wiring.

| Needed by a deep mode | Exists? | Where |
|---|---|---|
| Static validation of every generated statement, fail-closed | **yes**, and uniquely | `app/sqlguard/`, five unprivileged entry points |
| A policy over what result values reach the model | **yes** | `disclose()`, `HintBudget`, `disclose_history()` |
| Bounded generate⇄validate repair, reusable outside the graph | **yes** | `_add_repair_region` in `pipeline/graph.py`, already two callers |
| Schema narrowing before generation | **yes** | `scope` (sections) + `retrieve`, FK-aware, budgeted |
| A business vocabulary the planner can plan in | **yes** | `app/semantic/`, versioned, published, bound to the snapshot |
| Known-good question→SQL pairs | **yes** | `app/knowledge/`, with match, params, staleness, conflicts |
| Arithmetic over result rows, token-free | **yes** | `reports/facts.py`, `reports/checks.py`, `pipeline/checks.py` |
| Multi-figure document assembly with a summary | **yes** | `app/reports/` + `workers/report_graph.py` |
| Chart planning with a data veto | **yes** | `app/charts/` — `profile_result` → `plan_chart` → Vega-Lite |
| A live, cross-replica step trail with resume | **yes** | SSE over `LISTEN`/`NOTIFY`, `subscribe(after_seq=…)` |
| Long-running work: claim, cancel, reconcile | **yes** | `workers/`, cooperative-then-hard cancel |
| Per-node / per-operation / per-user token accounting | **yes**, input+output only | `usage_service`, `runs.*_tokens` |
| **A planner / decomposer** | **no** | — |
| **A run-level step budget that fails closed** | **no** | `deadline_at` is per-node; usage measures, does not enforce |
| **Cache-token accounting** | **no** | named as a follow-up in status.md §2 |
| **A contribution / driver module** | **no** | — |
| **Citations as structure** | **no** | reports cite by section, not by claim |
| **Answer-now** | **no** | cancel exists, answer-now does not |
| **A scorer for a multi-query answer** | **no** | golden set is single-answer execution accuracy |

**Six missing pieces, of which three are pure and token-free** (budget,
contribution module, scorer). That is a smaller gap than the feature's
reputation suggests, and it is small *because* MVP1 and MVP2 built the parts
that are hard to retrofit.

---

## 5. The shape that fits

Not a plan — a plan is [`plans/`](../plans/)'s job. This is the argued shape,
so that whoever writes the plan starts from the right one.

### 5.1 A second graph, not a longer one

Build a **separate compiled LangGraph** — `DeepAnalysisPipeline` — beside
`AnalyticsPipeline`, reusing `_add_repair_region` the way `sql_draft_service`
already does. Reasons: the chat graph's twelve-node `ORDER` is a straight line
whose shape is load-bearing for the SSE contract; the deep loop is a cycle with
a budget; and `pipeline-chat.md §0.3`'s existing bargain — *reused nodes, with
differences expressed as invoke-config values rather than a second executor* —
applies to the **nodes**, not to the graph. Reuse `scope`, `retrieve`,
`generate`, `validate`, `execute`, `inspect` verbatim. Add `plan`, `step`,
`compute`, `synthesize`.

```
deep:  route → plan ⇄ [ step → (scope → retrieve → generate ⇄ validate) → execute → inspect → compute ] → synthesize → charts
                 ↑__________________ revise, while budget remains ___________________|
```

### 5.2 The plan is a typed artifact, shown and citable

A structured-output `AnalysisPlan` — the same `structured()` road `clarify`,
`generate` and `chart` already use:

```python
class PlanStep(BaseModel):
    question: str                     # in the user's language, human-readable
    intent: Literal["CONFIRM", "DECOMPOSE", "COMPARE", "DRILL", "CHECK"]
    why: str                          # why this step, given what is known
    tool: Literal["SQL", "CONTRIBUTION", "COMPARE_PERIODS"] = "SQL"
    depends_on: list[int] = []        # earlier step indices

class AnalysisPlan(BaseModel):
    restatement: str                  # what the system understood
    steps: list[PlanStep]             # <= DEEP_MAX_STEPS
    stop_when: str                    # the declared success condition
```

Emit it as its own SSE event (`PLAN_PROPOSED`) and persist it as an artifact.
It is what the user reads while waiting, what *Answer now* truncates, and what
the plan-adherence metric scores against. `tool` is Cortex's tool-assignment
idea, typed.

### 5.3 The budget is a value object, and it fails closed

Everything else in this product fails open; this must not. A `DeepBudget` with
`max_steps`, `max_queries`, `max_rows_total`, `deadline_at` and
`max_prompt_tokens`, checked before each step by the same `_check_deadline`
discipline the graph already applies before each node. Exhausting it is a
**normal termination that synthesizes from what exists** — the same path
*Answer now* takes — never an error. Admin-settable per connection, which is
the Metabase-governance lesson from §1.4.

### 5.4 Every sub-query is an ordinary query

No new guard entry point. Each step's SQL goes through `validate` exactly as a
chat answer's does, lands in `generated_queries` as a row, and is therefore
picked up for free by metric attribution, the knowledge store's backlog, and
`usage`. Every intermediate result goes through `disclose()` before any part of
it reaches a prompt — mvp2 Part 5 §2 names this as the invariant a multi-step
loop stresses most.

### 5.5 `compute` is a closed set of pure functions

A new `app/analysis/` package, sibling to `app/reports/` and with the same
charter: **pure, token-free, unit-tested without a provider.**
`contribution.py` implements §3.4's three measure classes;
`periods.py` does period-over-period alignment; `outliers.py` does the z-score
work. The planner may *select* one via `PlanStep.tool`; it may not write one.
No DuckDB, no Python sandbox, in version one — with the trigger written down:
*a real question in the deep eval set that the closed set demonstrably cannot
express.*

### 5.6 `synthesize` is the report pipeline, pointed at a chat thread

Reuse `reports/narrate.py`'s prose-from-disclosed-results discipline and
`reports/checks.py`'s numeric consistency check. Add one thing reports do not
have: **a claim→step→SQL citation edge**, so a number in the prose resolves to
the query that produced it. That edge is what §3.5's traceability metric reads,
and it is the row in §1.7 that nobody else has.

### 5.7 It streams; it does not poll

Reports are polled because they are minutes long with nothing to watch. A deep
run is minutes long with **the most interesting thing in the product** to watch,
and the SSE machinery already survives replicas, reconnects with `after_seq`,
and cancels as a row. Keep SSE. The only new events are `PLAN_PROPOSED`,
`PLAN_REVISED`, `STEP_EVIDENCE` and `BUDGET_SPENT`.

### 5.8 What it is *not* allowed to do

Restating mvp2 Part 5 against this specific feature, because these are the five
ways it will go wrong:

1. **No privileged door.** Every sub-query replays the hostile corpus in its own
   test file, as `test_query_service.py` and `test_report_guard.py` do.
2. **Every intermediate result through `disclose()`** — at render time, per
   step, not once at the start.
3. **No model call on any refresh path.** A deep run is a run; it must never
   become something a dashboard triggers.
4. **No model does arithmetic.** §3.4's module computes; the model narrates.
5. **Every query in the trail, with its SQL.** A deep mode is exactly where a
   product stops showing its work.

---

## 6. What to do, in order

### 6.1 Before building anything: one measurement

Re-run the eval at the current `PROMPT_VERSION` with the semantic layer on, and
get the three baselines [eval.md §6](../reference/eval.md) already has empty
cells for. F3's trigger is *"Tier 1 accuracy is credible"*, and 0.36 from a v2
prompt in July is not evidence either way. **If single-shot accuracy is still
near 0.36 on the messy fixture, build A1/A5/B2 instead** — a deep mode over an
unreliable generator compounds the unreliability across eight queries and
dresses it in a report.

### 6.2 Then, three things that are useful whether or not the mode ships

| # | Work | Why it stands alone |
|---|---|---|
| 1 | Widen `Usage` to carry **cache read/write tokens** | Already named as a follow-up; becomes load-bearing at 15× tokens; the usage chart's stack is already a list of series for this. |
| 2 | `app/analysis/contribution.py` — §3.4's three algorithms, pure | Token-free, provider-free, testable today; immediately useful as a **check** on ordinary answers; the whole of F4's value. |
| 3 | Claim→SQL citation structure in `app/reports/` | Improves reports now; is the deep mode's trust mechanism later; is the free half of the eval. |

### 6.3 Then the mode, smallest version that is still the feature

`DEEP_MAX_STEPS = 5`. Plan-and-revise, sequential, SQL plus the closed tool set.
No sandbox, no sub-agents, no unstructured files, no parallel fan-out. Composer
toggle, streamed trail, *Answer now*, a report with citations, a frozen
twenty-question deep eval set scored on §3.5's five free metrics.

**The measurement that decides whether it was worth it** is not accuracy. It is
this: of the deep runs a user starts, what fraction do they read to the end
rather than pressing *Answer now* or cancelling? A mode people interrupt is a
mode whose latency contract is wrong, and that is a product finding no eval will
give you.

### 6.4 What is genuinely deferred, with triggers

| Deferred | Trigger |
|---|---|
| DuckDB / Python sandbox as a compute step | a question in the frozen deep set the closed tool set demonstrably cannot express |
| Parallel sub-agents (orchestrator-worker) | a measured step-DAG where ≥3 steps are genuinely independent, *and* §3.3's 15× is affordable |
| Unstructured files in a deep run | a named customer, not a roadmap slot |
| Semantic operators / per-row model calls | its own disclosure-ladder decision in [security.md](../reference/security.md), like entity dictionaries |
| Auto-escalation from a shallow answer | after the mode ships and interruption rate is known |

---

## 7. Sources

**Databricks Genie** — [Agent mode in Genie Spaces](https://docs.databricks.com/aws/en/genie/agent-mode) ·
[Agent mode in Genie Agents](https://docs.databricks.com/aws/en/genie-agents/agent-mode) ·
[AI/BI and Genie One release notes 2026](https://docs.databricks.com/aws/en/ai-bi/release-notes/2026) (dates, limits, API GA) ·
[Introducing Genie Agent Mode](https://www.databricks.com/blog/introducing-genie-agent-mode) *(vendor blog)* ·
[Expanding Genie Agents: Deep analysis, file reasoning, and more](https://www.databricks.com/blog/expanding-genie-agents-deep-analysis-file-reasoning-and-more) *(vendor blog)*

**Microsoft** — [Standalone Copilot experience in Power BI (preview)](https://learn.microsoft.com/en-us/power-bi/explore-reports/copilot-chat-with-data-standalone) ·
[Use Copilot with semantic models](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-semantic-models) (advanced vs standard DAX generation) ·
[Semantic model best practices for data agent](https://learn.microsoft.com/en-us/fabric/data-science/semantic-model-best-practices) ·
[Enable the code interpreter tool (preview) for your data agent](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-code-interpreter) ·
[Overview of Copilot in Fabric](https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-overview)

**Wren AI** — [Reducing hallucinations in text-to-SQL](https://www.getwren.ai/post/reducing-hallucinations-in-text-to-sql-building-trust-and-accuracy-in-data-access) *(vendor blog; MDL, CoT+ReAct, dry-run loop, reasoning summary)*

**Metabase** — [Metabot documentation](https://github.com/metabase/metabase/blob/master/docs/ai/metabot.md) (modes, glossary, the "no multi-step analyses" limitation) ·
[AI releases](https://www.metabase.com/releases-ai) (v60 MCP/Slack/BYO model, v61 governance layer, v63 providers)

**Superset** — [Using AI with Superset](https://superset.apache.org/user-docs/using-superset/using-ai-with-superset/) (MCP) ·
[SIP-166 AI Assistant](https://github.com/apache/superset/issues/33215) ·
[Building Preset AI Assist](https://preset.io/blog/building-preset-ai-assist-how-we-brought-text-to-sql-into-apache-superset/) *(vendor blog)* ·
[Vambery AI Agent extension (public beta)](https://github.com/apache/superset/discussions/38356)

**The four outside the field** — [ThoughtSpot: SpotIQ change analysis](https://docs.thoughtspot.com/cloud/26.9.0.cl/spotiq-change) (the three measure classes) ·
[ThoughtSpot agents](https://www.thoughtspot.com/product/agents) *(vendor)* ·
[Amazon Q in QuickSight — Scenarios](https://docs.aws.amazon.com/quicksight/latest/user/scenarios.html) ·
[Snowflake Cortex Agents](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents) (Plan → Act → reflect) ·
[Looker Conversational Analytics overview](https://docs.cloud.google.com/looker/docs/conversational-analytics-overview) (Code Interpreter)

**Literature** — [Text2SQL is Not Enough: Unifying AI and Databases with TAG](https://arxiv.org/abs/2408.14717) ·
[TAG-Bench](https://github.com/tag-research/tag-bench) ·
[Semantic Operators / LOTUS](https://arxiv.org/pdf/2407.11418) ·
[MAC-SQL](https://arxiv.org/abs/2312.11242) ·
[Next-Generation Database Interfaces: a survey of LLM-based text-to-SQL](https://arxiv.org/pdf/2406.08426) ·
[Spider 2.0](https://proceedings.iclr.cc/paper_files/paper/2025/file/46c10f6c8ea5aa6f267bcdabcb123f97-Paper-Conference.pdf) ·
[Deep Research: A Systematic Survey](https://arxiv.org/pdf/2512.02038) (plan-and-execute vs ReAct vs iterative refinement) ·
[FDABench: a benchmark for data agents on analytical queries](https://arxiv.org/pdf/2509.02473) (planning vs reflection latency) ·
[Adtributor: Revenue Debugging in Advertising Systems](https://www.microsoft.com/en-us/research/publication/adtributor-revenue-debugging-in-advertising-systems/) ·
[RiskLoc](https://arxiv.org/pdf/2205.10004) and [CMMD](https://arxiv.org/pdf/2203.16280) (multi-dimensional RCA, later work) ·
[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system) (orchestrator-worker; 15× tokens; token use explains ~80% of variance) ·
[AI agent evaluation: metrics, frameworks, production failures](https://www.morphllm.com/ai-agent-evaluation) (LLM-as-judge failure modes; the four-axis rule)

**In this repo** — [plans/mvp2.md](../plans/mvp2.md) F3/F4/F5 and Part 5 ·
[status.md §4–§6](../status.md) ·
[reference/pipeline-chat.md](../reference/pipeline-chat.md) §0 ·
[reference/pipeline-report.md](../reference/pipeline-report.md) ·
[reference/security.md](../reference/security.md) §2–§4 ·
[reference/eval.md](../reference/eval.md) §6 ·
[plans/langgraph-migration.md](../plans/langgraph-migration.md) Phase 4 ·
[research/competitive-matrix.md](competitive-matrix.md)
