# Retrieval at scale — competitor research and options for DataMind

> **Subject:** [mvp2-plan.md §1.2](../mvp2-plan.md) — *"Retrieval is a placeholder
> and does not scale past the demo"*, rated **Critical**.
> **Question asked:** how do Microsoft Data Formulator, Wren AI, Databricks AI/BI
> Genie and Power BI / Fabric Copilot handle **very large databases**, and what
> should DataMind do.
> **Constraints honoured throughout:** no change to the eval suite, and **no
> reduction of the context budget**. Every option below works at
> `_RETRIEVE_BUDGET_CHARS = 50_000` exactly as it stands. Where a competitor's
> answer *is* "send less", that is reported as research, not proposed as work.
> **Desk research date:** 2026-08-30. Competitor claims are sourced; unevidenced
> vendor assertions are marked as such.
> **Companion:** [learning-loop.md](learning-loop.md) covers §1.1. The two
> overlap at exactly one point, noted in §4.

---

## 0. The finding, in one page

**Three of the four do not solve large-schema retrieval. They refuse the
problem.** Only one of the four — Wren AI — retrieves over a large schema at
question time, and it needs two stages and a second model call to do it.

| | Answer to "my warehouse has 2,000 tables" |
|---|---|
| **Databricks Genie** | *You may attach at most **30**. Aim for **five or fewer**. Beyond that, prejoin into views.* Then federate: many narrow agents behind a supervisor that routes. |
| **Power BI / Fabric** | *The semantic model **is** the retrieval layer.* Copilot answers over a curated star schema, and an **AI data schema** narrows it further — a **hard** scope: a question about a field outside it gets no answer. Fabric lifted its 1,000-table ceiling but still advises **≤25 tables per source**. |
| **Wren AI** | The only real retriever: three indexing pipelines over MDL, vector table retrieval, then **an LLM pass that prunes columns**. |
| **Data Formulator** | Not a warehouse query engine. A **data-loading agent** searches the catalog, picks tables, and loads a subset into the workspace; analysis happens over what was loaded. |

The literature agrees with the market. **Spider 2.0** — real enterprise databases
averaging **700–800 columns per schema, up to 3,000** — drops frontier models
from ~91% on Spider 1.0 to roughly **17–21%**. Large-schema text-to-SQL is not a
solved problem that DataMind is behind on; it is an open one that everybody
manages by **shrinking the haystack before searching it**.

**Two things this research changes about §1.2's framing.**

**First, §1.2 understates the defect.** The plan says the `EXACT_MATCH` fallback
is a weak matcher that misses business language. It is also the opposite: a
**spurious over-matcher with no budget enforcement**. `"id" in "how many orders
were paid last month?"` is `True` — `id` is inside *paid* — so on a schema where
most tables carry an `id` column, that question selects **every table**, then
expands by one foreign-key hop, then renders it all. Nothing downstream clamps
it. Demonstrated in §1.1 and §1.2 below. Today it is invisible because both demo
fixtures take `FULL_SNAPSHOT`; on a real customer's first question it is the
entire behaviour.

**Second, the cheapest fix is already written and sitting in the repo.**
`pipeline/metadata.py::match_tables` is a token-boundary matcher with plural
forms, phrase handling and specificity resolution, and its docstring names the
exact bug the analytical path has: *"A single-word form must be a whole token —
'id' must not match inside 'identity'."* The METADATA path uses it. The path that
writes SQL does not.

---

## 1. What §1.2 actually costs, measured in this codebase

Four findings, in descending order of how much they matter. Two of them are not
in the plan.

### 1.1 The matcher over-matches, and the failure is unbounded

`pipeline/nodes/__init__.py::retrieve`, `EXACT_MATCH` branch:

```python
needle = state.question.lower()
matched = [
    t for t in tables
    if t["name"].lower() in needle
    or any(c["name"].lower() in needle for c in t.get("columns", []))
]
```

`x in needle` is Python substring containment, with no token boundary. Run
against real questions:

```
question: "How many orders were paid last month?"
    'id' in question -> True      #   pa-id
    'on' in question -> True      #   m-on-th
question: "what was total revenue by region last quarter?"
    'on' in q -> True             #   regi-on
    'at' in q -> True             #   wh-at
    'as' in q -> True             #   w-as
    'to' in q -> True             #   to-tal
```

Every table carrying a column named `id`, `on`, `at`, `as`, `to`, `no` or `by`
matches those questions. On a normal warehouse that is **most of the schema**.
And `matched` is then handed to `_expand_by_fk`, which adds every table one
foreign-key hop away — so the over-match is amplified, not corrected.

The plan's diagnosis (*"the match set is empty… `selected = tables[:20]`"*) is the
**under-match** failure and it is real. The over-match failure is the more
damaging one, because `tables[:20]` at least stays inside the budget.

### 1.2 The only branch a real customer takes is the only branch with no budget check

| Branch | Budget enforced? |
|---|---|
| `FULL_SNAPSHOT` | **yes** — it *is* the budget test (`approx_chars <= _RETRIEVE_BUDGET_CHARS`) |
| `SCHEMA_QUESTION` | **yes** — `select_tables(..., budget_chars=_RETRIEVE_BUDGET_CHARS)` spends it explicitly |
| `EXACT_MATCH` | **no** — `_expand_by_fk(...)` result goes straight into `RetrievedContext` |

And nothing downstream clamps it either: `RetrievedContext.render` caps the
*comment* block (2,500 chars) and the *semantic* block (8,000), and gates column
hints by `HintBudget` — but the table list itself is rendered in full, one line
per table, however many there are.

So the size of the schema block on the customer path is
`f(question wording × schema shape)`, unbounded. A hub table — `users`,
`accounts`, `products` — with 400 inbound foreign keys turns one seed match into
401 tables. The failure mode is not a wrong answer; it is a prompt that exceeds
the model's context, i.e. `E_LLM`, on a question that names one table.

**This is fixable without touching the budget or the eval.** Enforcing the
existing 50k budget on the branch that ignores it is not "sending less" — it is
sending *no more than the other two branches already send*.

### 1.3 A better matcher already exists in the tree

`pipeline/metadata.py`:

- `_names_for` — every spelling a person might type: `customer_addresses`,
  `customer addresses`, singular/plural.
- `match_tables` — *"A single-word form must be a whole token — 'id' must not
  match inside 'identity' — while a multi-word form is matched as a phrase"*,
  plus specificity resolution so *"customer addresses"* selects
  `customer_addresses` and not the `customers` hit inside it.

It was written for `describe`, and `retrieve`'s ANALYTICAL branch does not call
it. Reusing it is a small, dependency-free, prompt-neutral change that fixes both
halves of §1.1.

### 1.4 Second-order: the snapshot is one JSONB document, loaded whole

`latest_snapshot` deserialises `schema_snapshots.tables` in full on every run,
and `policy_from_snapshot` then walks every table and every column to build the
guard's allowlist. At demo size this is free. At 2,000 tables × 40 columns it is
a multi-megabyte parse per question **and per tile refresh**, before any
retrieval happens.

This is not the accuracy problem and should not be confused with it, but it is
the reason retrieval cannot become "a query over tables" without a schema of its
own — noted in §6.4.

### 1.5 Where the ceiling actually sits

`table_chars = 60 + 40 × len(columns)`. A 15-column table ≈ 660 chars, so
50,000 chars ≈ **75–85 tables**. `aurora` is ~6k (13 tables), `sales` ~26.5k (42
tables). **Both fixtures always take `FULL_SNAPSHOT`.** Everything above §1.1–1.2
describes code that no demo, and no eval run, has ever exercised.

---

## 2. How the four handle very large databases

### 2.1 Databricks AI/BI Genie — refuse the problem, then federate

Genie does not retrieve over a warehouse. **It makes the human pick the tables,
and caps how many they may pick.**

> *"Genie Agents support up to **30 tables or views**."*
> *"**Aim for five or fewer tables.**"*
> *"The more focused your selection, the better. Keeping your agent narrowly
> focused on a small amount of data is ideal."*
> *"If your data topic requires more than 30 tables, **prejoin related tables
> into views or metric views** before adding them to your agent."*
> — [Curate an effective Genie Agent](https://docs.databricks.com/aws/en/genie/best-practices)

Three consequences worth absorbing:

**A Genie space is scoped by *topic*, not by database.** DataMind scopes by
*connection*, which is a database. That is the single biggest structural
difference between the two products, and it is why Genie never needed a
retriever: with ≤30 curated tables the whole scope fits the prompt, so
"retrieval" is a *modelling* activity done once by a human, not a ranking
function run per question.

**The recommended fix for scale is to change the data, not the search.** Prejoin
into views; use **metric views**, which *"pre-define metrics, dimensions, and
aggregations"*. Reduce the number of objects the model can be wrong about.

**Beyond 30 tables, the answer is many agents plus a router.** The published
pattern is domain-specific spaces (Credit Decisioning, Fraud, Claims) wired
together by a **Supervisor Agent** — *"a managed orchestration layer… uses a
dynamic supervisor pattern to analyze the user's question and orchestrate between
Genie Spaces for structured data, Knowledge Assistant agents for unstructured
data, and MCP servers for tools."* A large enterprise does not get one big Genie;
it gets a **federation of small ones**.

Within a space, the mechanisms that shrink context further are curation, not
retrieval: **column hiding** (removes columns from the model's context entirely),
row filters and column masks, and curated table/column descriptions.

> Sources: [Curate an effective Genie Agent](https://docs.databricks.com/aws/en/genie/best-practices) ·
> [Create and manage a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/set-up) ·
> [Tune Genie Agent quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality) ·
> [Multi-agent Supervisor](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/multi-agent-supervisor)

---

### 2.2 Power BI and Fabric — the semantic model *is* the retrieval layer

Power BI's answer predates the AI era and is the reason Copilot works at all: you
never point it at a warehouse. You point it at a **semantic model** — a curated
star schema with named measures, defined relationships and business-friendly
field names. The modelling work that DataMind hopes to do at question time,
Power BI did at design time, years earlier, for other reasons.

On top of that, three AI-era narrowing mechanisms:

**AI data schema — a *hard* scope, not a preference.** *"An AI data schema lets
semantic model authors define a focused subset of the model's schema for Copilot
to prioritize… A streamlined schema reduces ambiguity."* The test procedure in
the docs is the proof that it is a boundary and not a hint:

> *"Ask a data question by using a field that **isn't** in the AI data schema.
> **Copilot shouldn't return an answer.**"*

Two details worth stealing outright:

- *"Fields that are hidden in the semantic model are **automatically excluded**
  in the initial AI data schema when you set it up for the first time."* — the
  scope is **seeded from existing signal**, so it is not a blank curation form.
- *"Copilot still respects relationships **regardless of** the AI data schema…
  if two fields are related and one of them is included, Copilot can still return
  answers that require that relationship."* — **the scope constrains what is
  described, not what may be joined.** This is exactly the tension DataMind's
  `_expand_by_fk` sits in, resolved in the direction of keeping joins working.

Also documented: *"When you use Copilot to create a report page, search for data,
or use a DAX query, Copilot requires the **entire** semantic model. It doesn't
consider the AI data schema."* Different tasks get different scopes — a narrow
scope for answering, a full scope for authoring.

**Fabric data agents — the one vendor that raised a ceiling instead of lowering
one.** Previously restricted to sources with fewer than **1,000 tables** or under
**100 columns plus measures**, *"these schema size restrictions have now been
lifted"*, with support for *"over 1,000 tables and more than 100 columns and
measures"*. But the guidance did not change with the ceiling:

> *"For optimal results, limit the number of tables to **25 or fewer** for a given
> data source."*
> *"**Select only** the tables, columns, views, and functions needed for the
> questions that the data agent should answer. Irrelevant objects increase
> ambiguity and give the query-generation tool more possible paths to consider."*
> — [Best practices for improving data agent query generation](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configuration-best-practices)

And a warning is shown in-product that *"the accuracy of results may vary with
larger schema sizes"*. Microsoft supports the big schema and tells you not to use
one.

**Schema object descriptions (Preview)** are explicitly positioned for this case
— *"For large or ambiguous SQL schemas"* — attaching per-object business meaning
where names are *"abbreviated, generic, or similar to one another"* or where
*"the schema is too large to explain every object clearly in data source
instructions."* Note what this is: **enriching the text that retrieval and
generation read**, table by table, because names alone stop discriminating once
there are thousands of them.

**Standalone Copilot routes.** Users attach a report, semantic model or Fabric
data agent as a grounded reference, and Copilot routes questions to the right
specialist. Same federation answer as Databricks, different packaging.

> Sources: [AI data schemas](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-data-schema) ·
> [Prepare your data for AI](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai) ·
> [Data agent configuration best practices](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configuration-best-practices) ·
> [Expanded Data Agent support for large data sources](https://blog.fabric.microsoft.com/en-us/blog/expanded-data-agent-support-for-large-data-sources/)

---

### 2.3 Wren AI — the only one that actually retrieves

Wren is the closest architectural comparison and the only one of the four whose
answer to a large schema is *a retriever* rather than *a smaller schema*.

**Three separate indexing pipelines**, all over MDL rather than raw DDL:

| Pipeline | Indexes |
|---|---|
| **DB Schema Indexing** | *"tables, columns, relationships, views, and metrics"* — emitted as distinct document types: `TABLE`, `TABLE_COLUMNS` (batched), `VIEW`, `METRIC` |
| **Historical Question Indexing** | *"previously asked questions and their corresponding SQL queries"* — the learning loop and the retriever share an index |
| **Table Description Indexing** | *"high-level table descriptions for **table discovery and selection**"* — a separate, coarser index built for the first hop |

**Retrieval is two-stage, and the second stage is a model call.** The DB Schema
Retrieval pipeline is described as *"the most critical retrieval pipeline"*, and
its flow is:

```
semantic analysis → table identification by vector similarity
  → schema context retrieval → LLM-driven column selection → filtered schema
```

with **smart column pruning**: *"Uses LLM to select only necessary columns"*, for
*"context window management"*. Columns are indexed in batches (**default 50 per
document**) rather than one document per table, because a 200-column table would
otherwise be one embedding that means nothing.

Reported configuration from the service: `table_retrieval_size: 10` and
`table_column_retrieval_size: 100` — ten tables, a hundred columns. *(Reported
via search of the WrenAI service configuration; not confirmed against the repo in
this pass — see §8.)*

**Three ideas here that DataMind does not have and could:**

1. **Separate coarse and fine indexes.** A table-description index for "which
   tables", a column index for "which columns of those". One index answering both
   questions answers neither well.
2. **Prune columns, not just tables.** A 200-column table costs ~8,000 chars in
   DataMind's estimator — one table can eat 16% of the whole budget. DataMind
   selects tables and then renders **every column of them**. Wren does not.
3. **A model call inside retrieval.** Cheap recall, then precision. This is the
   standard modern shape and it is worth naming as the cost it is: one extra LLM
   round trip on the critical path.

> Sources: [Wren AI architecture](https://docs.getwren.ai/oss/reference/architecture) ·
> [WrenAI architecture notes (gist)](https://gist.github.com/coderplay/9023fa0e251883b5586de4529be4857a) ·
> [Canner/WrenAI](https://github.com/Canner/WrenAI)

---

### 2.4 Microsoft Data Formulator — retrieval as an agentic *loading* step

Data Formulator does not query a warehouse in place, so it does not have a schema
block problem — it has a *catalog discovery* problem, and it solves that with a
tool-using agent rather than a ranker.

0.7 added persistent connectors (Superset, Kusto, Cosmos DB, MySQL, PostgreSQL,
MSSQL, S3, Azure Blob, BigQuery) with **SSO, lazy catalog loading and smart
filtering**, plus a **data-loading agent** that *"finds the right tables, plans
multi-table loads, and pulls data in straight from chat"*, letting users
*"search and apply smart filters when discovering tables across large catalogs."*

The architecture is the interesting part: **the large catalog is browsed and
searched, a subset is materialised into the workspace, and the analysis agent
only ever sees what was loaded.** Retrieval is a separate, visible, user-steerable
step with its own UI — not a hidden ranking function inside the prompt pipeline.

Whether this is transferable to DataMind is genuinely arguable. It suits an
exploratory tool where a person is willing to say "load orders and customers
first". It suits a chat product less, where the promise is that you just ask. But
it is the only design of the four in which **the user can see and correct the
retrieval decision** — and Wren offers a smaller version of the same thing by
letting a user edit *"the selected models (which are retrieved through similarity
search)"* when adjusting an answer.

> Sources: [Data Formulator (Foundry Labs)](https://labs.ai.azure.com/innovations/data-formulator/) ·
> [Data Formulator 0.7 (MSR blog)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/) ·
> [microsoft/data-formulator releases](https://github.com/microsoft/data-formulator/releases)

---

### 2.5 What the literature says

**Large schemas are where text-to-SQL is still broken.** Spider 2.0 uses real
enterprise databases — Google Analytics, Salesforce, on BigQuery / Snowflake /
DuckDB / Postgres — *averaging 700–800 columns per schema and up to 3,000 in
extreme cases*. Frontier models score roughly **17–21%** there, against **91%** on
Spider 1.0 and **73%** on BIRD. The gap is not model quality; it is schema scale.

**Long context reduces the need for schema linking — on frontier models only.**
*Is Long Context All You Need? Leveraging LLM's Extended Context for NL2SQL*
(VLDB) finds that *"the latest LLMs can retrieve relevant schema elements from
unfiltered database schema (i.e., passing all DB tables without selection)"*, and
reports **67.41% on BIRD dev** with a long-context Gemini 1.5 Pro pipeline with no
fine-tuning. The paper is careful not to declare schema linking obsolete, and the
caveat is decisive for DataMind: this is a property of a *frontier long-context
model*. DataMind is **provider-agnostic by design** and its eval model is a small
one on which prompt bloat has already been measured to hurt — *"more instruction
is not better here"*. **The smaller the model a customer configures, the more
retrieval quality matters.** "Send it all and let the model sort it out" is a
strategy DataMind structurally cannot adopt as its only strategy.

**Retrieval strategy is well-studied.** DAIL-SQL's example-selection work
(question similarity, **masked** question similarity — replacing table names,
column names and literals with a generic token before comparing — and joint
question+query similarity) applies as directly to *schema* retrieval as to
example retrieval, and is the same machinery [learning-loop.md](learning-loop.md)
recommends.

> Sources: [Spider 2.0 (ICLR 2025)](https://arxiv.org/abs/2411.07763) ·
> [xlang-ai/Spider2](https://github.com/xlang-ai/Spider2) ·
> [Is Long Context All You Need? (VLDB)](https://www.vldb.org/pvldb/vol18/p2735-ozcan.pdf) ·
> [DAIL-SQL](https://arxiv.org/abs/2308.15363)

---

### 2.6 The matrix

`●` shipped · `◐` partial · `○` absent

| Mechanism | **DataMind** | **Data Formulator** | **Wren AI** | **Genie** | **Power BI / Fabric** |
|---|:--:|:--:|:--:|:--:|:--:|
| **Shrink the haystack** | | | | | |
| Human-curated table scope | ◐ *(schema-level only)* | ● loaded tables | ◐ | ● **max 30, aim ≤5** | ● AI data schema |
| Scope is a hard boundary, not a hint | ○ | ● | ○ | ● | ● |
| Column-level hiding | ○ | n/a | ◐ | ● | ● |
| Scope seeded from existing signal | ○ | ○ | ○ | ○ | ● hidden fields |
| Prejoined views / metric views advised | ○ | n/a | ● cubes/metrics | ● | ● the whole model |
| Federation: many scopes + a router | ○ | ○ | ◐ projects | ● supervisor | ● Copilot routing |
| **Search the haystack** | | | | | |
| Retrieval at question time | ◐ substring | ◐ agentic search | ● vector | ○ *(not needed)* | ◐ |
| Vector index over schema | ○ | ○ | ● | ○ | ◐ |
| Separate table vs column index | ○ | ○ | ● | ○ | ○ |
| **Column** pruning within a table | ○ | n/a | ● LLM pass | ○ | ● |
| Two-stage recall → precision | ○ | ● | ● | ○ | ◐ |
| FK / relationship expansion | ● | ○ | ● | ● declared joins | ● preserved across scope |
| Value / entity index for `WHERE` | ○ | ○ | ◐ | ● 120 cols × 1,024 vals | ◐ |
| **Enrich what is searched** | | | | | |
| Catalog comments in the block | ● | ○ | ● | ● | ● |
| Business names / synonyms indexed | ○ *(render-only)* | ○ | ● | ● | ● linguistic schema |
| Per-object descriptions for big schemas | ◐ *(semantic layer)* | ○ | ● | ● | ● **explicitly for large schemas** |
| **Control and honesty** | | | | | |
| Budget enforced on every path | ○ **← §1.2** | n/a | ● | n/a | n/a |
| User can see what was retrieved | ◐ *(count only)* | ● | ● | ◐ | ● run steps |
| User can **correct** retrieval | ○ | ● | ● | ○ | ○ |
| Warns when the schema is too big | ○ | ○ | ○ | ● hard cap | ● in-product warning |

**How to read it.** DataMind's gaps are concentrated in the top block — *shrink
the haystack* — which is the block every competitor solves first and which needs
no embeddings, no new dependency and no prompt change. The bottom-left `○` —
budget enforced on every path — is a defect, not a missing feature.

---

## 3. Five lessons

**L1 — Nobody searches a warehouse at question time. They shrink it first.**
30 tables (Genie), 25 per source (Fabric), a curated star schema (Power BI), a
loaded subset (DF). The one product that retrieves at question time, Wren, does
so over an MDL model that a human already curated. **Curation is the primary
mechanism and retrieval is the secondary one, in every product examined.**

**L2 — Scope must be a boundary, or people will not trust it.** Power BI's test
script says a question about an out-of-scope field *should return no answer*.
A "preference" that the model can route around is not a scope; it is a hint that
will surprise someone.

**L3 — But relationships survive the scope.** Power BI keeps joins working across
the AI data schema. Genie declares join relationships explicitly. Scope tells the
model what to *talk about*; the join graph is what makes an answer possible at
all. DataMind's `_expand_by_fk` has the right instinct and the wrong bounds.

**L4 — Column pruning is a separate problem from table selection, and DataMind
does not do it at all.** One 200-column table is ~8,000 chars in DataMind's own
estimator — 16% of the budget for one table. Wren prunes columns with a model
pass; Power BI and Genie prune them by hiding. DataMind selects a table and
renders every column it has.

**L5 — Names stop discriminating at scale; descriptions are what you search.**
Fabric ships per-object descriptions *specifically* for large schemas. Genie
curates table and column descriptions. Wren indexes a dedicated table-description
index for discovery. DataMind already *has* this content — the semantic layer's
business names, grain statements and glossary, plus catalog comments — and
**indexes none of it.** It renders into the generate prompt and is invisible to
retrieval. That is the cheapest unclaimed win in this document.

---

## 4. Options

Nine options, grouped by what they do. All work at the current 50k budget and
none requires touching the eval suite. Sizes follow the plan: **S** = days,
**M** = 1–3 weeks, **L** = a month or more.

Two of them (**O1**, **O2**) are defect repairs rather than features, and are
listed first because everything else is built on top of them.

---

### Group I — Repair what is there (no new concepts)

#### O1 · Fix the matcher: reuse `metadata.match_tables` · **S**

Replace the substring predicate in `retrieve`'s `EXACT_MATCH` branch with the
token-boundary matcher already in `pipeline/metadata.py`, extended to columns.

| Pros | Cons |
|---|---|
| Fixes the over-match (§1.1) **and** improves the under-match: plural forms and `snake_case → "snake case"` mean "customer addresses" finds `customer_addresses`. | Still lexical. "Churn" still finds nothing, which is §1.2's headline complaint and this does not address it. |
| **Zero new dependencies, zero prompt change, `PROMPT_VERSION` does not move.** The generator sees the same block shape it always did. | Changes which tables reach the prompt on the `EXACT_MATCH` path, so it *is* a behaviour change — just not a prompt-format one. |
| The code is written, documented and unit-tested; this is a call-site change plus tests. | Neither fixture exercises the path, so the change ships unmeasured by the existing suite (see §5.4 for what to do instead). |
| Removes a class of bug that is embarrassing to explain to a customer. | |

**Recommendation: do this first.** It is the highest ratio in the document.

---

#### O2 · Enforce the existing budget on every branch · **S**

After `_expand_by_fk`, spend the same `_RETRIEVE_BUDGET_CHARS` the other two
branches already spend: rank the selected set (direct matches first, then
history-carried, then FK neighbours, then by row count) and take tables until the
budget is used. Record what was dropped, the way `census` already does for
`SCHEMA_QUESTION`.

| Pros | Cons |
|---|---|
| Closes the unbounded-prompt failure (§1.2). Turns an `E_LLM` on a large schema into a bounded answer. | Requires a **ranking decision** — and any ranking is a guess until there is a way to measure it. |
| **This is not "sending less".** It caps the customer path at what `FULL_SNAPSHOT` and `SCHEMA_QUESTION` already cap themselves at. The budget constant is untouched. | A dropped table is a silently missing join path. Mitigation: say so in the step detail, as `census` does. |
| No new dependency, no prompt-format change. | Makes retrieval quality *visible* for the first time, which will surface problems O1 does not fix. |
| Gives `retrieve` a single, testable contract: **the block never exceeds the budget, on any path.** | |

---

#### O3 · Prune columns, not just tables · **S–M**

Per L4. When a selected table is wide, render a subset of its columns: keys and
foreign keys always, then columns the question or the semantic layer points at,
then the rest until the table's share of the budget is spent — the same
round-robin allocation `app/semantic/render.py` already uses for the layer block,
which is the proven pattern in this codebase.

| Pros | Cons |
|---|---|
| Attacks the axis nobody else in DataMind attacks. One 200-column table = ~8,000 chars = 16% of the budget today. | **A dropped column is a wrong answer waiting to happen** — worse than a dropped table, because the model will confidently use a similar column instead. |
| Lets *more tables* fit in the same budget, which is the thing that actually helps a wide schema. | Choosing which columns matter, lexically, is the same hard problem as choosing tables — one level down. |
| The allocation machinery exists and is tested (`render_semantic`'s three-tier round-robin). | The guard's allowlist still contains the un-rendered columns, so the model can reference a column it was never shown and pass validation. That is safe but confusing. |
| Keys/FKs pinned means join paths always survive — L3 respected. | |

---

### Group II — Shrink the haystack (what the competitors actually do)

#### O4 · A curated table scope per connection · **M** · *the competitors' answer*

`database_connections.schema_allowlist` already exists and scopes by **schema**.
Extend to a **table-level** scope: the tables this connection's questions are
answered from. Seeded, per Power BI's pattern, from something the system already
knows — tables that appear in existing tiles, report blocks and answered runs, or
simply the largest N — so it is not a blank form over 2,000 checkboxes.

| Pros | Cons |
|---|---|
| It is what **all four** competitors do, in four different packagings. The market has voted. | Pushes work onto the customer, which is exactly the "three weeks of an analyst's time" complaint §1.1 makes about the semantic layer. |
| Turns retrieval from a hard problem into an easy one: 40 curated tables fit the budget whole, so `FULL_SNAPSHOT` — the *best* path — becomes the customer's path too. | A scope is wrong the moment the question is about something outside it, and the user cannot tell why the answer is bad. Needs the L2 treatment: say it plainly. |
| Cheap to build: one column, one UI list, one filter applied at snapshot load. | Reintroduces the §1.1 curation problem: nobody curates without a visible payoff. |
| **Can also narrow the guard** (§6.2) — turning a retrieval feature into a governance feature, which is Genie's column-hiding argument and fits DataMind's posture. | Deciding whether it narrows the guard is a real decision, not a detail. |
| Solves §1.4 for free: a scoped snapshot is a smaller load per run. | |

---

#### O5 · Topics: many scopes per connection, plus routing · **L** · *Genie's federation*

O4, plural. A connection carries several named topics ("Sales", "Inventory"),
each with its own table scope, semantic layer subset and verified pairs. A
question is routed to one — by the existing `route` node, extended.

| Pros | Cons |
|---|---|
| The published answer to "my warehouse has 2,000 tables" from **both** Databricks and Microsoft. It is what large deployments actually look like. | **Large.** Touches connections, the semantic layer, conversations (which are pinned to a connection today), dashboards and the router. |
| Each topic is small enough for `FULL_SNAPSHOT`, so accuracy per topic is as good as the demo's. | Adds a routing decision that can be wrong, on top of every existing failure mode — and a mis-route is invisible to the user. |
| Composes with everything: verified pairs, benchmarks and accuracy scores all become per-topic, which is how a customer would want to read them anyway. | Premature before O4 exists. A topic is a scope with a name; build the scope first. |
| A natural place for cross-topic questions to *refuse* honestly rather than answer badly. | Conversations are pinned to one connection for disclosure reasons (`_bind_connection`); topics inside a connection interact with that and need thought. |

**Recommendation: not MVP2. Record it as where O4 leads.**

---

#### O6 · Lean on the semantic layer as the scope · **S**

A connection with a semantic layer already has a human-curated statement of what
matters. Use *described* tables as a retrieval prior — rank them above
undescribed ones, or (opt-in) restrict to them.

| Pros | Cons |
|---|---|
| **Free curation.** The work is already done, by the person best placed to do it, and currently pays off in exactly one place (the generate prompt). | Only helps connections that have a layer, and layer coverage on a 2,000-table warehouse will be partial for a long time. |
| Directly implements L5 with content that already exists. | The layer is generated one table at a time and can be *wrong*; treating it as a scope promotes a soft signal to a hard one. |
| Gives the semantic layer a second, visible payoff — the same argument §A5 makes for the synonym index. | Interacts with `semantic_layer_enabled`: if the layer is a scope, switching it off changes retrieval, and the A/B switch stops being clean. |

---

### Group III — Search the haystack better

#### O7 · Lexical scoring over enriched text (`pg_trgm` / FTS) · **M**

Replace the boolean match with a **ranked** one, scoring each table against the
question over: table and column names, catalog comments, and the semantic layer's
business names, synonyms, grain statements and glossary. Postgres full-text
search and `pg_trgm` both ship with the official `postgres:16-alpine` image.

| Pros | Cons |
|---|---|
| **No new dependency and no new deployment unit** — genuinely, unlike pgvector (§O8). | Lexical similarity still misses paraphrase. "Churn" finds `subscription_events` only if someone wrote "churn" in the glossary — which is the point, but it needs the glossary. |
| Ranking is what O2 needs anyway. This supplies it with something better than heuristics. | Requires the schema to be queryable, and it is a JSONB blob today (§1.4, §6.4). This is where that bill comes due. |
| Indexes the semantic layer for retrieval — L5, §A5, and the largest unclaimed win. | Trigram similarity on business prose is noisy; FTS needs stemming config per language. |
| Degrades gracefully: with no comments and no layer it is no worse than O1. | |

---

#### O8 · Embeddings, hybrid with O7 · **M–L**

Vectors over the same enriched text, blended with the lexical score. Two routes,
neither free — this is covered in detail in
[learning-loop.md §5 Option C](learning-loop.md) and the analysis is identical:

- **pgvector** — `docker-compose.yml` runs `postgres:16-alpine`, which does
  **not** carry it. New base image, and not available on every managed Postgres.
- **Embeddings via the LiteLLM gateway** — no Python dependency, but it makes the
  flagship retrieval feature conditional on the customer's provider exposing an
  embedding endpoint, in a product whose pitch is "point it at whatever you have".

| Pros | Cons |
|---|---|
| The only option that reliably retrieves on paraphrase, which is §1.2's headline complaint. | Breaks one positioning promise whichever route is taken. |
| Same index serves schema retrieval, verified-pair retrieval (§A1) and the synonym index (§A5) — one investment, three payoffs. | Index lifecycle: re-embed on every schema sync and every layer edit; a stale index is a silent accuracy regression with no error to point at. |
| Well-trodden: two-stage vector recall is what Wren ships. | Adds a **second** model whose version belongs in the reproducibility story, which `model_snapshot` and `PROMPT_VERSION` do not currently cover. |

**If taken, take it as a swap behind an interface**, with lexical (O7) as the
always-available implementation.

---

#### O9 · Two-stage retrieval with a model pass · **M** · *Wren's shape*

Cheap recall (O1/O7) over-selects deliberately — say 40 tables — then one small
LLM call prunes to the ~10 tables and the columns that matter, and only those are
rendered.

| Pros | Cons |
|---|---|
| The strongest known technique, and it is what the one competitor that solves this actually does. | **A model call on the critical path**, before `generate`. Latency and cost on every analytical question, and a new failure mode. |
| A model is far better than any lexical score at "does `subscription_events` have anything to do with churn". | Must **fail open** — a pruning failure has to fall back to the unpruned set, or a provider hiccup takes retrieval down. |
| Solves table *and* column selection in one pass (O3 for free). | Under- or over-pruning is invisible unless the retrieved set is shown to the user (L-adjacent: Fabric's run steps). |
| Fits the existing architecture: `RetrievedContext` is the seam, and nodes already fail open (`route`, `clarify`, `inspect`, `chart`). | The pruning model sees table and column *names* — no new disclosure, but it is another prompt to reason about. |

---

### 4.10 Side by side

| | O1 matcher | O2 budget | O3 columns | O4 scope | O5 topics | O6 layer-as-prior | O7 lexical rank | O8 embeddings | O9 model pass |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Size | S | S | S–M | M | L | S | M | M–L | M |
| New dependency | no | no | no | no | no | no | no | **yes** | no |
| Prompt format changes | no | no | no | no | no | no | no | no | no |
| `PROMPT_VERSION` moves | no | no | no | no | no | no | no | no | no |
| Extra LLM call per question | no | no | no | no | no | no | no | no | **yes** |
| Needs the schema to be queryable | no | no | no | no | no | no | **yes** | **yes** | no |
| Needs customer curation | no | no | no | **yes** | **yes** | ◐ | ◐ | no | no |
| Fixes over-match (§1.1) | **yes** | ◐ | no | ◐ | ◐ | no | **yes** | **yes** | **yes** |
| Fixes unbounded prompt (§1.2) | no | **yes** | ◐ | **yes** | **yes** | ◐ | no | no | ◐ |
| Fixes business language ("churn") | no | no | no | no | no | ◐ | ◐ | **yes** | **yes** |
| Helps at 2,000 tables | ◐ | **yes** | ◐ | **yes** | **yes** | ◐ | ◐ | **yes** | **yes** |

**Note the two all-`no` rows.** Nothing in this document changes the prompt
*format* or moves `PROMPT_VERSION`. Retrieval decides *which tables* go in the
block; the block's shape is unchanged. That is a real advantage over Theme A's
work, where every option that generalises requires a prompt change and carries
the 36% → 26% risk — and it is why retrieval work can proceed while the prompt
baseline question is still open.

---

## 5. Recommendation

**Ship O1 + O2 now, as one small release. Then O4. Then O7, and only then decide
between O8 and O9.** O3 rides along with O2. O5 is where O4 leads, not MVP2 work. O6 is a
cheap ranking prior to fold into O7.

### 5.1 Why O1 + O2 first

They are **defect repairs on a path no test exercises and every customer takes**.
Neither needs a decision, a dependency, a curation workflow or a prompt change.
Together they change `retrieve`'s contract from *"a boolean match with unbounded
output"* to *"a ranked selection that always fits the budget"* — which is the
precondition for every other option, because none of the rest can be reasoned
about while the baseline is "sometimes everything".

The bug in §1.1 is also the kind that is cheap now and expensive later: the first
customer with a 500-table schema will hit it on their first question, and the
symptom (`E_LLM`, or a nonsense answer) points nowhere near the cause.

### 5.2 Why O4 next, even though it is the least clever option

Because it is what **all four competitors do**, because it converts the customer
path back to `FULL_SNAPSHOT` — the only path that is actually good — and because
it is the only option that makes a 2,000-table warehouse work *today*, with no
embeddings and no second model. Genie caps at 30 tables and tells you to aim for
five; that is not a limitation they apologise for, it is their accuracy strategy.

The honest counter is that it is curation work, and §1.1 is already asking the
customer for curation work. Two mitigations, both from the research: **seed the
scope** from what the system already knows (Power BI seeds from hidden fields;
DataMind can seed from tiles, report blocks and answered runs), and make the
scope's payoff visible — a scoped connection retrieves whole, and can say so.

### 5.3 Why O7 before O8/O9

O7 needs no dependency and no provider capability, and it delivers L5 — indexing
the semantic layer and catalog comments, content that exists today and that
retrieval cannot see. Once ranked lexical retrieval is in place with a measurable
hit rate, the choice between embeddings (paraphrase, at the cost of a promise)
and a model pass (precision, at the cost of latency) can be made on evidence
rather than on architecture taste.

### 5.4 Getting evidence without touching the eval

The suite stays as it is. Three sources of evidence that do not touch it:

1. **The step trail already reports retrieval.** `retrieve` returns
   `f"{len(selected)} tables via {strategy}"`, and `run_steps` persists it. Log
   the strategy, the selected count and the rendered char count per run and the
   distribution answers "how often does a real connection take `EXACT_MATCH`, and
   how big does the block get?" — the two questions §1.2 cannot currently answer.
2. **Unit tests over a synthetic wide snapshot.** `_RETRIEVE_BUDGET_CHARS` is
   already *"a module constant, not a local, so a test can lower it to exercise
   the fallback"* — that is a **test** lowering it, not the product. A 500-table
   synthetic snapshot in a unit test proves O1 and O2 without a container, a
   provider, or a single change to `app/eval/`.
3. **An additive fixture, if and when wanted.** A new wide suite alongside
   `sales_v1` changes nothing about the existing one — the frozen golden set
   stays frozen, and no recorded number becomes non-comparable. Out of scope for
   this phase; noted so the option is on the record.

---

## 6. Six decisions to make before building

### 6.1 Is a curated scope a *hint* or a *boundary*? ⚠️ the load-bearing one

Today retrieval and the guard are **decoupled, deliberately and correctly**.
`retrieve` decides what the model is *shown*; `policy_from_snapshot` builds the
guard's allowlist from the **entire snapshot**, independent of that. So a model
that names a table it was never shown still passes validation, as long as the
table exists in the snapshot. That is the right default: retrieval is a hint, and
**the guard is the only boundary**.

O4 forces the question. Power BI answered it one way — *"Ask a data question by
using a field that isn't in the AI data schema. Copilot shouldn't return an
answer."* Genie answered it the same way with column hiding, which *removes
things from the model's context entirely, preserving governance by construction*.

The two readings, and what each costs:

| | **Scope as a hint** | **Scope as a boundary** |
|---|---|---|
| Guard allowlist | whole snapshot, unchanged | narrowed to the scope |
| A question about an out-of-scope table | may be answered, if the model guesses the table | refused, plainly |
| Existing tiles / report blocks over out-of-scope tables | keep working | **break** — this is the trap |
| What it is | a retrieval optimisation | a governance feature |

**The trap is the third row.** Dashboard tiles and report blocks re-validate
against the connection's *current* snapshot on every execution. If a scope
narrows the guard, narrowing it silently breaks saved artifacts that were fine
yesterday — the failure surfaces as a per-tile `ERROR` value, days later, to
someone who did not change the scope.

**Recommended:** ship O4 as a **hint** (retrieval only, guard untouched), and
treat "restrict the guard to the scope" as a **separate, explicit, opt-in
setting** with a pre-flight check that lists which tiles and blocks it would
break. Two features, two decisions, one of them reversible.

### 6.2 Relationships must survive the scope, and FK expansion must be bounded

L3, made concrete. Power BI keeps joins working across the AI data schema;
`_expand_by_fk` has the same instinct and no bounds.

Under O2, ranking has to place FK-expanded tables *below* direct matches but
*above* nothing — a bridge table is worthless alone and essential alongside its
two entities. Two rules that fall out of the research:

- **Expand, then rank, then cut** — never cut before expanding, or the bridge is
  gone before it is scored.
- **Prefer completing a join path over adding an unrelated table.** A selection
  of three tables that join is a better prompt than five that do not, and this is
  the one place where "fewer tables" is *better*, not merely cheaper.

Under O4, the same question: does the scope include the bridge tables the scoped
entities join through? Power BI's answer — relationships are respected regardless
— says the FK closure of a scope should be retrievable even when the closure is
not itself in the scope.

### 6.3 O7 and O8 need the schema to be queryable, and it is a JSONB blob

`schema_snapshots.tables` is one JSONB document (§1.4). Ranking, indexing and
embedding all want **one row per table** (and for O3, per column) with an index
on the searchable text. This is the real cost hiding inside O7 and O8, and it is
not in either option's headline size.

Three ways out, in ascending order of work:

1. **Rank in Python** over the deserialised blob. Fine at hundreds of tables,
   pointless at thousands, and it does nothing for §1.4's load cost.
2. **A derived, denormalised search table** written by the sync — `(connection_id,
   schema, table, searchable_text, tsvector)` — leaving the snapshot as the
   source of truth. Additive, no migration of existing data, and it is what O7
   actually needs.
3. **Normalise the snapshot.** Correct, and much larger than this phase.

**Recommended: (2).** It also fixes §1.4 for the retrieval path specifically:
selecting candidate tables becomes an indexed query instead of a full-document
parse, and only the selected tables need their full JSON.

### 6.4 Staleness: a scope and an index both drift from the schema

`semantic_layers.schema_version` already records which snapshot a layer was
written against, so the UI can say the schema has moved on. A table scope needs
the same, and so does any derived search table or vector index: a schema sync
that drops a table must not leave it in a scope, and must not leave a stale row in
the index that retrieves a table the guard will then refuse.

The existing pattern in the codebase applies unchanged: an artifact that no
longer resolves is **flagged, not deleted** — *"deleting a person's work to hide
drift is worse than showing it."*

### 6.5 Say what was left out

`census` already names the tables `SCHEMA_QUESTION` could not fit, and `describe`
tells the user. The customer path has no equivalent: `EXACT_MATCH` returns a
count and no names.

Once O2 lands, every dropped table is a possible wrong answer, and the step
detail is the cheapest possible mitigation — *"42 tables via EXACT_MATCH, 9 not
shown"*. Fabric's run steps view is the fuller version of this idea, and it is
what lets a curator tell *"the retrieval was wrong"* from *"the SQL was wrong"*.
Without it, every retrieval improvement is unfalsifiable from the outside.

### 6.6 One overlap with the learning loop, and it is worth taking

[learning-loop.md](learning-loop.md) recommends a store of verified
question→SQL pairs. Every stored pair contains, in its `referenced_tables`, **a
human-confirmed answer to "which tables does this kind of question need"** — the
exact supervision signal retrieval lacks.

Two uses, both cheap once pairs exist:

- **Seed O4's scope** from the tables that verified pairs, tiles and report
  blocks actually reference. This is the "seed the scope from existing signal"
  pattern Power BI uses for hidden fields.
- **Carry tables from a matched pair.** If a question is similar to a stored
  pair, that pair's `referenced_tables` are a strong retrieval prior —
  `_tables_from_history` already does exactly this trick for conversation
  history, and the mechanism is identical.

Neither requires the pair store to be finished. Both work off `dashboard_tiles`
and `report_blocks` today.

---

## 7. A sketch of the recommended path

Not a plan; enough to argue with.

### 7.1 `retrieve`, after O1 + O2 + O3

```
                     ┌ approx_chars ≤ budget ─────────────→ FULL_SNAPSHOT   (unchanged)
snapshot ─ scope? ───┤
  (O4)               └ over budget ─┬ METADATA ───────────→ SCHEMA_QUESTION (unchanged)
                                    │
                                    └ ANALYTICAL ─────────→ RANKED_MATCH    (new)
                                        1. match_tables(question)        ← O1, token-boundary
                                        2. + tables carried from history   (unchanged)
                                        3. + one FK hop                    (unchanged)
                                        4. rank: direct > carried > FK-neighbour,
                                                 tie-broken by row count
                                        5. take tables until budget spent  ← O2
                                        6. per table, fit columns:         ← O3
                                              PK/FK always, then question-
                                              or layer-referenced, then the
                                              rest, round-robin
                                        7. report what was dropped         ← §6.5
```

`strategy` gains one value. `RetrievedContext` is otherwise unchanged, the
generator sees the same block shape, and **`PROMPT_VERSION` does not move**.

### 7.2 The scope (O4)

```
database_connections
  + retrieval_scope        text[]   -- ["public.orders", ...]; empty = whole schema
  + retrieval_scope_mode   text     -- 'HINT' (default) | 'ENFORCED'   ← §6.1, opt-in
  + retrieval_scope_version int     -- the snapshot it was curated against  ← §6.4
```

Empty is the current behaviour exactly, so every existing connection is
unaffected. Seeded on first open from tables referenced by existing tiles, report
blocks and successful runs (§6.6).

### 7.3 The search table (O7, §6.3)

```
schema_search                        -- derived, rebuilt by the sync
  connection_id   uuid
  schema, name    text
  kind            text               -- TABLE | COLUMN
  searchable_text text               -- names + catalog comment + semantic-layer
                                     --   business name, synonyms, grain, glossary
  ts              tsvector           -- GIN index; pg_trgm on searchable_text
  PRIMARY KEY (connection_id, schema, name, kind)
```

The snapshot stays the source of truth. This exists only to answer "which
candidates", after which the selected tables are read from the snapshot as they
are today.

### 7.4 Order of work

| # | Work | Size | Gate |
|---|---|:--:|---|
| 1 | O1 matcher + O2 budget + drop reporting | S | unit tests over a synthetic 500-table snapshot |
| 2 | O3 column fitting | S–M | same, plus "keys always survive" |
| 3 | Retrieval telemetry: strategy, table count, rendered chars per run | S | answers "how often is `EXACT_MATCH` real?" |
| 4 | O4 scope, seeded, `HINT` mode only | M | tiles and blocks unaffected by construction |
| 5 | O7 search table + ranked lexical retrieval, O6 folded in as a prior | M | hit-rate telemetry from step 3 |
| 6 | Decide O8 vs O9 | — | on the numbers from step 5, not on architecture taste |

---

## 8. Open questions this research could not close

1. **Wren's exact retrieval parameters are unconfirmed.** `table_retrieval_size:
   10` and `table_column_retrieval_size: 100` are reported via search of the
   WrenAI service configuration; direct fetches of the config file in the repo
   returned nothing in this pass. The repo is Apache-2.0 and the pipeline is in
   `wren-ai-service` — reading it directly is the highest-value follow-up here,
   the same as in [learning-loop.md](learning-loop.md) §8.
2. **No vendor publishes retrieval recall.** Not one of the four reports how often
   its retrieval surfaces the right tables. Every claim is architectural. This
   means DataMind has no external benchmark to compare against, and also that
   nobody else can claim a number either.
3. **Genie's behaviour inside a space is undocumented.** With ≤30 tables it is
   plausible that all of them are sent every time and there is no retrieval step
   at all — but Databricks does not say, and the 200-snippet knowledge-store cap
   suggests *something* is selected.
4. **Fabric's post-1,000-table mechanism is undescribed.** The restriction was
   lifted; the blog does not say what replaced it, and the best-practice guidance
   (≤25 tables) is unchanged, which suggests the mechanism is "it will work, but
   worse" rather than a new retriever.
5. **The long-context result has not been reproduced on small models.** The VLDB
   finding is on Gemini 1.5 Pro. Whether "send more schema" helps or hurts a
   30B-class self-hosted model is exactly the question DataMind's own measured
   36% → 26% prompt-bloat result gestures at, and nobody has published it.

---

## 9. Sources

**Databricks AI/BI Genie**
- [Curate an effective Genie Agent](https://docs.databricks.com/aws/en/genie/best-practices) — the 30-table cap, "aim for five or fewer", prejoin into views
- [Create and manage a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/set-up)
- [Tune Genie Agent quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality) — column hiding, knowledge-store limits
- [Use Supervisor Agent to create a coordinated multi-agent system](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/multi-agent-supervisor)
- [Beyond the single Genie space: multi-agent AI analytics](https://www.aimpointdigital.com/blog/beyond-the-single-genie-space-building-multi-agent-ai-analytics-on-databricks) *(third-party; pattern description)*

**Power BI and Microsoft Fabric**
- [Prepare your data for AI — AI data schemas](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-data-schema) — the hard-scope test, hidden-field seeding, relationships preserved
- [Prepare your data for AI](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai)
- [Best practices for improving data agent query generation](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-configuration-best-practices) — schema selection, ≤25 tables per source, schema object descriptions for large schemas
- [Fabric data agent creation](https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent)
- [Expanded Data Agent support for large data sources](https://blog.fabric.microsoft.com/en-us/blog/expanded-data-agent-support-for-large-data-sources/) — the lifted 1,000-table / 100-column restriction

**Wren AI**
- [Wren AI architecture](https://docs.getwren.ai/oss/reference/architecture)
- [WrenAI architecture notes (gist)](https://gist.github.com/coderplay/9023fa0e251883b5586de4529be4857a) — the three indexing pipelines, two-stage retrieval, LLM column pruning, 50-column batching
- [Canner/WrenAI](https://github.com/Canner/WrenAI)

**Microsoft Data Formulator**
- [Data Formulator (Foundry Labs)](https://labs.ai.azure.com/innovations/data-formulator/)
- [Data Formulator 0.7 (MSR blog)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/) — lazy catalog loading, smart filtering, the data-loading agent
- [microsoft/data-formulator releases](https://github.com/microsoft/data-formulator/releases)

**Literature**
- [Spider 2.0: Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows](https://arxiv.org/abs/2411.07763) — 700–800 columns per schema, up to 3,000; ~17–21% vs 91% on Spider 1.0
- [xlang-ai/Spider2](https://github.com/xlang-ai/Spider2)
- [Is Long Context All You Need? Leveraging LLM's Extended Context for NL2SQL (VLDB)](https://www.vldb.org/pvldb/vol18/p2735-ozcan.pdf) — 67.41% BIRD dev with long-context Gemini 1.5 Pro, unfiltered schema
- [Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation (DAIL-SQL)](https://arxiv.org/abs/2308.15363) — masked question similarity and selection strategies

**DataMind, internal**
- [docs/mvp2-plan.md](../mvp2-plan.md) §1.2, §1.3, Theme B
- [docs/research/learning-loop.md](learning-loop.md) — §1.1, and the shared retrieval infrastructure
- [CLAUDE.md](../../CLAUDE.md) — the invariants, the failure postures, the eval rules
- `backend/app/pipeline/nodes/__init__.py::retrieve` · `backend/app/pipeline/metadata.py` ·
  `backend/app/pipeline/state.py::RetrievedContext` · `backend/app/services/query_service.py::policy_from_snapshot`
