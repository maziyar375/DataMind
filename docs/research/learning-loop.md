# The learning loop — competitor research and options for DataMind

> **Subject:** [mvp2-plan.md §1.1](../mvp2-plan.md) — *"No learning loop — a correction
> cannot become knowledge"*, rated **Critical**.
> **Scope:** how Microsoft Data Formulator, Wren AI, Databricks AI/BI Genie and
> Power BI / Fabric Copilot solve this, what is verifiable from public
> documentation, and what DataMind should build.
> **Desk research date:** 2026-08-30. Every claim about a competitor is sourced;
> where a vendor asserts something it does not evidence, this document says so.
> **Status:** research and options. Not a decision, and not a plan — the
> recommendation in Part 5 is an argument, and Part 6 lists the decisions that
> have to be made before any of it is built.

---

## 0. The finding, in one page

**Three of the four ship a real learning loop. All three converge on the same
five-stage architecture, and none of them learns from feedback automatically.**
Data Formulator is the exception in both directions: it has no learning loop at
all, and it is the only one of the four that is honest about the fact.

The shared architecture:

```
    ①  CAPTURE          ②  CURATE           ③  STORE            ④  RETRIEVE        ⑤  MEASURE
    a signal that   →   a human turns   →   as a durable,   →   the right       →   a benchmark
    an answer was       it into an          reviewable          few of them         that says
    right or wrong      artifact            artifact            reach the prompt    whether ③ helped
```

Every stage is load-bearing, and **the industry's own documentation says stage ②
cannot be skipped**. Databricks states it flatly:

> *"Your Genie Agent's behavior does not change based on user feedback alone."*
> — [Test and monitor a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/monitor)

A thumbs-down is not knowledge. It is a *ticket* that a human converts into
knowledge. Any DataMind design that captures feedback and expects accuracy to
improve on its own is reproducing a mistake the market has already made and
documented.

Four things worth knowing before reading further, because they change the design:

1. **The winning artifact is a question→SQL pair, everywhere.** Genie calls them
   *example SQL queries*, Wren calls them *Question-SQL Pairs*, Fabric calls them
   *example queries / few-shots*, Power BI calls them *verified answers* (a
   trigger-phrase→visual pair, the same idea over a semantic model rather than
   over SQL). Four independent teams, one shape.
2. **The pair is used two ways, not one** — as a *few-shot example* that steers
   generation, and as a *short-circuit* that replaces generation on a close
   match. The second is the one that carries a visible trust badge, and it is
   strictly cheaper and strictly safer than the first.
3. **Curation quality is itself a measured artifact.** Microsoft ships
   `evaluate_few_shots` — it scores each example on **Clarity, Relatedness and
   Mapping**, and separately runs **conflict detection** across the approved set.
   This is the most sophisticated idea in the research and the one nobody
   expects: *your verified examples can be wrong, can contradict each other, and
   the contradiction degrades accuracy on questions unrelated to either one*.
4. **Nobody has solved the measurement trap, and DataMind's plan walks into it.**
   [mvp2-plan.md §A3](../mvp2-plan.md) proposes that verified pairs and benchmark
   rows *share a table*. If a pair is both injected into the prompt and scored as
   a benchmark row, the benchmark is measuring memorisation. See §6.4 — this is
   the single most consequential correction this research offers.

---

## 1. The four, one by one

### 1.1 Databricks AI/BI Genie — the reference implementation

The most complete loop in the market, and the one with published numbers. Genie
recently rebranded spaces as *Genie Agents*; the mechanisms are the same.

#### The five stages, as Genie implements them

| Stage | Mechanism | Detail |
|---|---|---|
| ① Capture | *"Is this correct?"* after every response | Three answers: **Yes**, **Fix it** (flags an error, optional explanation), **Request review** (escalates). Plus implicit signals: thumbs-up, and **downloading the query results**. |
| ② Curate | **Monitor tab** | Anyone with `CAN MANAGE` sees every question and answer asked in the agent, filterable by time, rating, user and status. They *"review the specific exchange, comment on the request, and confirm or correct the response."* A corrected query is saved with **"Add as instruction"**. The original asker is notified. |
| ③ Store | **Instructions** and the **knowledge store**, two separate quotas | Instructions: **100 per agent** — one example SQL query, one SQL function, or the *entire* General instructions text block each count as one. Knowledge store: **200 per agent** — table descriptions, join relationships and SQL expressions count; text instructions, example queries, SQL functions, column descriptions and prompt-matching settings do **not**. |
| ④ Retrieve | Similarity against saved examples; **prompt matching** for values | *"Genie can either use the example query directly or learn from it to handle similar questions."* Entity matching supplies curated distinct-value lists for **up to 120 columns, 1,024 values each, 127 characters each, string columns only**. |
| ⑤ Measure | **Benchmarks**, up to **500 questions per agent** | Gold SQL optional but recommended, with a *"Generate SQL"* button to draft it. Chat mode compares result sets automatically over **up to 5,000 rows**, treating values that round to the same **4 significant digits** as equal; labels are **Good / Bad / Manual review needed**. Agent mode uses an **LLM judge** with optional evaluation guidance. Accuracy is tracked over time in an **Evaluations** view. |

#### Trusted assets — the visible distinction

An example query may be **parameterized** (`:param`, typed as String / Date /
Date and Time / Decimal / Integer, each with a comment describing acceptable
values). Parameterized queries and registered Unity Catalog SQL functions are
**trusted assets**: *"When Genie uses a trusted asset to answer a question, it
provides a verified answer, giving agent users an extra layer of confidence."*
The user can edit a parameter value and re-run.

This is the mechanism behind a fact that is easy to miss: **parameterisation is
what makes one curated example serve a family of questions.** Without it a store
of pairs is a lookup table that only ever answers the exact question it was
written for, and curation never pays back the effort.

#### Structured business logic, separate from prose

Three typed expression kinds, each with synonyms and usage instructions:

| Kind | Example |
|---|---|
| **Measure** | Win rate → `COUNT(CASE WHEN stage='Closed Won' THEN 1 END) / NULLIF(COUNT(*),0)` |
| **Filter** | High-value orders → `orders.amount > 10000` |
| **Field** | Deal size → `CASE WHEN amount < 10000 THEN 'Small' … END` |

#### Knowledge mining — the closest thing to automatic learning

*"When an author thumbs-up a response or downloads query results, Genie analyzes
the query"* and then *"suggests new SQL expressions (measures, filters, or fields)
and additional join relationships."* Note the shape carefully: the system
**proposes**, an author **approves**. It is stage ② accelerated, not stage ②
removed. Primary and foreign keys from the catalog are saved as join
relationships automatically, which is the one thing Genie does without asking.

#### Governance interacts with the loop

A **row filter** excludes the entire table from prompt matching; a **column mask**
excludes the masked columns. Curated value lists are treated as disclosure
surface, not as free performance.

> Sources: [Tune Genie Agent quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality) ·
> [Test and monitor a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/monitor) ·
> [Use benchmarks in a Genie space](https://docs.databricks.com/aws/en/genie/benchmarks) ·
> [Trusted assets](https://docs.databricks.com/aws/en/genie/trusted-assets) ·
> [Building confidence in your Genie space: benchmarks and Ask for Review](https://www.databricks.com/blog/building-confidence-your-genie-space-benchmarks-and-ask-review)

---

### 1.2 Wren AI — the same loop, as version-controlled files

Architecturally the closest competitor: open source, self-hostable,
semantic-layer-first, provider-agnostic. Their loop is deliberately built out of
*artifacts a team can review in a pull request* rather than rows in a product
database.

**The knowledge layer is two things**, and the docs are explicit that both are
separate from the semantic model (MDL):

| Component | What it is |
|---|---|
| **Question-SQL Pairs** | *"Allow you to save verified answers with matching questions and SQL statements."* When a user asks something similar later, *"Wren AI will generate SQL based on the saved SQL pairs to improve accuracy and consistency."* |
| **Instructions** | Split into **Global Instructions** — *"Applied to every query that Wren AI generates"* — and **Question-Matching Instructions**, *"applied only when a user's question matches certain patterns or topics."* |

The Global / Question-Matching split is the interesting half and the one Genie
does not have. A global instruction is a tax on every prompt; a question-matching
instruction costs tokens only on the questions it is about. Wren's own examples
make the distinction concrete: *"Exclude orders with `order_status IN ('canceled',
'unavailable')` from any sales or revenue-related calculations"* is global,
while the rule for computing *late delivery rate* from estimated versus actual
delivery dates is question-matching. **The documentation does not say how
question matching is implemented** — embeddings, keywords or otherwise.

**Capture is a button on an answer.** *"When you ask questions and get an answer,
you can save the question and the SQL query to your knowledge base by clicking the
**Save to Knowledge** button."* Pairs can also be authored proactively, *"to
prepare your AI assistant to answer specific questions before they're asked."*

**Correction has two doors**, and this is worth copying. From *Adjust Answers*:

- **Adjust the reasoning steps** — the user edits the plan *and* the selected
  models, *"which are retrieved through similarity search"*, then regenerates.
  This is a user correcting **retrieval**, not SQL, and it is the only product of
  the four that exposes that.
- **Adjust SQL** — edit the statement directly in a popup, submit, regenerate.

Either way, the adjusted answer can then be saved, which *"helps Wren AI learn
from your adjustments and generate more accurate answers for similar questions in
the future."*

**Retrieval prefers the store.** On a similar question Wren *"will first search
through views you saved; if there is no related SQL based on stored views, it
will generate SQL queries based on the context it collects."* Saved knowledge
short-circuits generation — the same two-way use as Genie's trusted assets.

**Underneath**: MDL as a version-controlled semantic layer, `instructions.md`,
`queries.yml`, and a local LanceDB index with hybrid retrieval. Wren describes an
*Agent Learning Loop* — scaffold models, enrich with business logic, recall
successful natural-language→SQL pairs from memory — and positions *Active
Learning* (soliciting validation and correction) as a direction of travel rather
than a shipped guarantee.

**No published benchmark surface inside the product.** Wren ships an eval runner
for golden datasets as developer tooling, which is exactly where DataMind's
`app/eval/` sits today. Neither has promoted it to a customer-visible score.

> Sources: [Knowledge Overview](https://docs.getwren.ai/oss/guide/knowledge/overview) ·
> [Question-SQL Pairs](https://docs.getwren.ai/cp/guide/knowledge/question-sql-pairs) ·
> [Instructions](https://docs.getwren.ai/oss/guide/knowledge/instructions) ·
> [Adjust Answers](https://docs.getwren.ai/oss/guide/home/adjust_answer) ·
> [WrenAI on GitHub](https://github.com/Canner/WrenAI)

---

### 1.3 Power BI and Fabric — *two* loops, a decade apart

Microsoft has shipped the same idea twice, and both are instructive because they
were built for different eras and make opposite trade-offs.

#### 1.3a The old loop: Q&A tooling (2019-era, still shipping)

The purest capture→curate loop in the market, and nobody talks about it any more:

| Stage | Mechanism |
|---|---|
| ① Capture | **Review questions** — the modeller sees *the actual questions users asked* against their dataset over the **last 28 days**, together with **the words Q&A did not recognise**, plus dataset owner, workspace and last-refresh date. |
| ② Curate | **Teach Q&A** — type a question containing an unknown term; Q&A prompts for a definition (a filter, or a field name); Q&A reinterprets the original question live; if the result is right, save. |
| ③ Store | Synonyms attached to columns and measures, plus a **linguistic schema** in YAML — parts of speech, synonyms, phrasings — exportable and importable. **Manage terms** lists everything taught, for review or deletion. |

Two things here that no 2026 product has bettered. First, **"words Q&A did not
recognise" is a *ranked backlog of exactly what to curate next*** — a
zero-judgement work queue, derived from real traffic, that tells the owner what
their users say and their model does not. Second, the curation UI **reinterprets
the question in front of you before you save**, so the author sees the effect of
the definition rather than guessing at it. Both ideas are cheap and both are
absent from DataMind's plan.

#### 1.3b The new loop: Copilot **verified answers**

Human-approved, visual responses triggered by predefined phrases, **stored on the
semantic model** so they apply everywhere the model is used. Each verified answer
is a *visual* + one or more *trigger phrases* + optional *filters*.

**Published limits** (unusually specific, and useful as sizing evidence):

| | |
|---|---|
| Verified answers per model | **250** |
| Trigger prompts per verified answer | **15** (recommended: aim for **5–7**) |
| Trigger prompt length | **500 characters** |
| Filters per verified answer | **3** at creation and at consumption |
| Filter permutations per verified answer | **10** |

**Matching is exact *or* semantic.** *"Copilot first checks for an exact or
semantically similar match to any trigger phrase tied to a verified answer. If a
match is found, Copilot returns the verified answer instead of generating a new
response."* Documented as **supported**: synonyms, reordered words, filter
criteria stated in the prompt. Documented as **not supported**: adding, removing
or swapping fields or dimensions; modifying or replacing the measure. The docs
give worked examples of both — *"Snowboard sales by month"* matches *"Snowboard
sales over time"* but not *"Ski bib sales by month"*.

**The UI treatment is the best in the research.** Four indicators on a verified
answer:

- a **Verified checkmark** — *"Indicates the response is human-reviewed and approved"*;
- the **matched trigger phrase**, so the user can see *how their prompt was
  interpreted* and correct it if the match was wrong;
- a **textual answer** summarising the data;
- **"How Copilot arrived at this"** — underlying data, logic and applied filters.

Showing the matched phrase is the mitigation for the single biggest risk in the
short-circuit design (a confident answer to a question the user did not ask), and
it costs one line of UI.

**Microsoft claims implicit learning** — *"Copilot also learns from how users
interact with verified answers. It gains a better understanding of phrasing,
synonyms, and data relationships to improve its future responses"* — but publishes
**no mechanism and no numbers**. Treat as marketing until evidenced.

**A caution worth quoting**, because it is the only place a vendor admits the
learning store is a security surface:

> *"Row-level security (RLS) and object-level security (OLS) aren't fully
> supported as security features for verified answers… there are scenarios where
> data might still be exposed (for example, through the file format in Git).
> During preview, don't rely on this functionality as a security feature."*

A curated answer store is a **second copy of business logic outside the
governance model**, and Microsoft could not make it inherit the model's security
in time for preview. §6.1 applies this directly to DataMind.

Alongside verified answers, Power BI offers **AI instructions** set on the
semantic model — prose guidance, the direct analogue of Genie's instructions —
and *approved-for-Copilot* marking, which removes friction warnings from answers
sourced from a model an admin has blessed.

#### 1.3c Fabric data agents — the best curation *quality* tooling anywhere

Example queries per data source (Lakehouse ✅, Warehouse ✅, Eventhouse KQL ✅,
Semantic Models ❌, Ontology ❌), used as few-shots:

> *"When a user asks a question against a data source, the Data Agent
> automatically retrieves the most relevant examples — **typically the top four**
> — and feeds them into its generation process."*

Three mechanisms here are ahead of everyone else:

**1. Examples are schema-validated before they are ever used.** *"Every example
query is validated against the schema of the selected data source — queries that
don't pass validation aren't sent to the agent."* An example that no longer parses
against the schema is silently withdrawn rather than teaching the model a
hallucination.

**2. The retrieved examples are shown in the trace.** A **run steps** view lets a
creator *"debug which example queries were retrieved and applied to a user's
question."* Curation without this is guesswork: an author adds an example, accuracy
does not move, and they cannot tell whether the example was wrong or was never
retrieved.

**3. The example set is scored and conflict-checked.** `evaluate_few_shots` runs
each pair through validation and returns a success rate plus per-example
diagnostics on three axes:

| Score | Asks |
|---|---|
| **Clarity** | Is the natural-language question specific and single-intent? (*"Total revenue by region for 2024"* good; *"Show performance"* not) |
| **Relatedness** | Does the SQL return the metric the question asks for, at the right granularity, with the right filters? (question asks a **count**, SQL returns `SUM(revenue)` → fails) |
| **Mapping** | Does **every literal in the question** appear in the SQL? (*"Orders over 100 in March 2025 for 'West'"* → SQL must contain `> 100`, `2025-03`, `'West'`) |

*"An example is considered high quality only if all three scores… are positive."*

Then **conflict detection** across the approved set. A conflict is flagged when
two or more examples:

- represent **the same intent** (on a normalised form of the question) but
  reference **different tables or views**;
- compute **the same metric** with **different aggregation logic or granularity**;
- would return **materially different results** for the same business question.

Each conflict is reported with the examples involved, a description of how they
diverge, and a **confidence score (5 = High … 1 = Speculative)**. *"Resolving them
helps improve query determinism, accuracy, and overall agent behavior."*

**And a separate evaluation SDK** for the agent itself: a DataFrame of
`question` / `expected_answer`, judged by an **LLM judge** with a customisable
`critic_prompt` over `{query}`, `{expected_answer}`, `{actual_answer}`; results
land in a summary table and a `_steps` table, with per-row verdicts of
**true / false / unclear** and a `thread_url` back to the conversation.

> Sources: [Data Agent example queries](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries) ·
> [Evaluate a Fabric data agent](https://learn.microsoft.com/en-us/fabric/data-science/evaluate-data-agent) ·
> [Verified answers](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-verified-answers) ·
> [AI instructions](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-instructions) ·
> [Intro to Q&A tooling](https://learn.microsoft.com/en-us/power-bi/natural-language/q-and-a-tooling-intro) ·
> [Teach Q&A](https://learn.microsoft.com/en-us/power-bi/natural-language/q-and-a-tooling-teach-q-and-a) ·
> [Q&A linguistic schema](https://learn.microsoft.com/en-us/power-bi/natural-language/q-and-a-tooling-advanced)

---

### 1.4 Microsoft Data Formulator — the honest negative result

**Data Formulator has no learning loop, and this is the most useful data point in
the section.** It is a Microsoft Research artifact answering *"what should the
interface for AI-assisted analysis be?"* — not *"how does a text-to-SQL system get
more accurate over time?"*

What it does have:

- **Thread memory** — the conversational agent *"maintains thread memory,
  unifying explanation, exploration, visualization, and recommendation into fluid
  conversations that carry context across turns."* Context **within** a session.
- **Data threads** — *"preserve the history of long analysis sessions, making it
  possible to revisit, reuse, and build on earlier work."* Navigation and
  branching, not accumulation of verified knowledge.
- **Experimental knowledge distillation** — from the Foundry Labs page, quoted in
  full because the hedge is the point: *"Experimental knowledge-distillation work
  lets agents extract reusable skills from sessions into a shared library that
  informs future sessions."* Explicitly labelled experimental; no product surface,
  no limits, no evaluation published.

The lesson for DataMind is not "copy DF". It is that **the research frontier
(distil reusable skills automatically) and the shipping frontier (a human
approves a question→SQL pair) are years apart**, and the three vendors with
paying customers all ship the second. A DataMind design premised on the system
extracting its own knowledge is betting on the frontier.

> Sources: [Data Formulator (Foundry Labs)](https://labs.ai.azure.com/innovations/data-formulator/) ·
> [Data Formulator 0.7 (MSR blog)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/) ·
> [microsoft/data-formulator](https://github.com/microsoft/data-formulator)

---

## 2. The comparison, at learning-loop resolution

`●` shipped · `◐` partial or unevidenced · `○` absent

| | **DataMind (today)** | **Data Formulator** | **Wren AI** | **Genie** | **Power BI / Fabric** |
|---|:--:|:--:|:--:|:--:|:--:|
| **① Capture** | | | | | |
| Rating on an answer | ○ | ○ | ◐ | ● Yes / Fix it / Request review | ◐ generic Copilot feedback |
| Escalate to an owner | ○ | ○ | ○ | ● Request review + notify | ○ |
| Log of real user questions for the owner | ○ | ○ | ◐ | ● Monitor tab + weekly digest | ● Review questions (28 days) |
| Unrecognised-term backlog | ○ | ○ | ○ | ○ | ● **unique** |
| **② Curate** | | | | | |
| Owner corrects SQL in-product | ◐ *(tile editor only)* | ○ | ● Adjust SQL | ● Monitor tab | ◐ |
| User corrects **retrieval** | ○ | ○ | ● **unique** | ○ | ○ |
| One click: correction → stored knowledge | ○ | ○ | ● Save to Knowledge | ● Add as instruction | ● Set up a verified answer |
| System proposes its own knowledge | ○ | ◐ *(experimental)* | ◐ | ● knowledge mining | ◐ Copilot-suggested trigger phrases |
| **③ Store** | | | | | |
| Question→SQL pairs | ○ | ○ | ● | ● | ● |
| **Parameterized** pairs | ○ | ○ | ◐ | ● `:param`, typed | ● up to 3 NL-settable filters |
| Free-text instructions | ◐ *(inside the layer)* | ○ | ● global + question-matching | ● 100-instruction budget | ● AI instructions |
| Typed measures / filters / fields | ◐ *(metrics only)* | ○ | ● MDL | ● | ● DAX |
| Synonyms / business vocabulary | ◐ *(layer, generate-only)* | ○ | ● | ● | ● linguistic schema (YAML) |
| Curated value dictionaries | ○ | ○ | ◐ | ● 120 cols × 1,024 values | ◐ |
| Version-controlled / reviewable | ○ *(JSONB blob)* | ○ | ● **files in git** | ◐ | ◐ |
| **④ Retrieve** | | | | | |
| Pairs retrieved as few-shots | ○ | ○ | ● | ● | ● top ~4 |
| Near-exact match short-circuits generation | ○ | ○ | ● | ● | ● |
| **Visible** verified/trusted badge | ○ | ○ | ◐ | ● | ● checkmark + matched phrase |
| Trace shows which examples were used | ◐ *(step trail, no examples)* | ● code shown | ◐ | ◐ | ● run steps |
| **⑤ Measure** | | | | | |
| In-product benchmark set | ○ *(dev CLI only)* | ○ | ◐ *(dev runner)* | ● 500 questions | ● SDK, notebook |
| Automated result comparison | ● **`app/eval/metrics.py`** | ○ | ● | ● 5,000 rows / 4 s.f. | ◐ LLM judge |
| Accuracy tracked over time, in the UI | ○ | ○ | ○ | ● Evaluations | ◐ |
| **Quality control of the store itself** | ○ | ○ | ○ | ○ | ● **Clarity/Relatedness/Mapping + conflict detection** |

**How to read this.** DataMind's `○` column is stage ①–④ almost entirely. But
notice where the `●` already is: **the comparator**. `app/eval/metrics.py`
already does automated result-set comparison with a documented numeric tolerance
and a six-label outcome taxonomy — the piece Genie built and Microsoft chose to
replace with an LLM judge. DataMind is not starting stage ⑤ from zero; it is
starting it from the strongest position of the five products.

---

## 3. Seven lessons, extracted

**L1 — Feedback is a ticket, not a gradient.** Databricks says it outright.
Build the review queue *and* the human step, or build neither.

**L2 — A pair must be parameterizable or curation never pays back.** Genie's
`:param`, Power BI's three NL-settable filters. A store of literal pairs answers
only the questions already asked; a store of parameterized pairs answers
families. This is the difference between a feature people use twice and a feature
people invest in.

**L3 — The badge is half the value.** Genie's verified answer, Power BI's
checkmark plus **the matched trigger phrase**. The badge makes curation's payoff
legible to the person deciding whether to curate, and the matched phrase is the
user's defence against a confident wrong match. Both are cheap.

**L4 — Show which examples were retrieved.** Fabric's run steps view. Without it,
an author who adds an example and sees no improvement cannot distinguish *the
example was wrong* from *the example was never retrieved*, and stops curating.

**L5 — The curated store degrades on its own.** Two decay modes, and Microsoft
handles both: **staleness** (validate every example against the current schema;
withdraw the ones that fail) and **conflict** (two examples answering the same
intent differently, which poisons questions related to *neither*). Nobody else
checks for conflicts. DataMind should.

**L6 — Traffic is the curation backlog.** Power BI's Review questions with its
list of unrecognised words, Genie's Monitor tab and weekly digest. The hardest
part of curation is not writing the pair — it is knowing which pair to write. The
system already knows: it is sitting in `runs`.

**L7 — Governance does not automatically follow the knowledge into the store.**
Microsoft shipped verified answers with RLS *unsupported* and said so. A curated
store is a second copy of business logic, on a different governance path from the
first. For DataMind, whose entire differentiation is "never trust the model,
prove every boundary", inheriting this bug would be worse than not shipping the
feature.

---

## 4. What DataMind already has — the loop is 60% built and not wired up

Before choosing an option it is worth being precise about the starting position,
because the plan understates it. Every stage has a component already in the
codebase.

| Stage | Already exists | Where | What is missing |
|---|---|---|---|
| ① Capture | Every question, every generated statement, every guard verdict, every outcome | `runs`, `generated_queries` (`raw_sql`, `rewritten_sql`, `validation_report`, `referenced_tables`), `query_executions` | A judgement column. `runs` records *whether it ran*, never *whether it was right*. |
| ① Capture | **Corrections that already happened** | `dashboard_tiles.sql` / `report_blocks.sql` with `sql_origin` in `GENERATED_EDITED` \| `HANDWRITTEN`, each carrying the `question` that produced the draft | Nothing reads them. A human already fixed this SQL and the fix is inert. |
| ② Curate | An editor for connection-scoped meaning, with live validation against the same parser the save path uses | `frontend/src/components/semantic.tsx`, `POST .../semantic/check` | It edits the layer, not answers. There is no queue and no per-answer correction. |
| ③ Store | A per-connection document with human/generated provenance, merge-on-regenerate, and flagged-entry semantics | `semantic_layers`, `app/semantic/models.py` (`Provenance`, `SemanticMetric`, `GlossaryTerm`) | No question→SQL artifact. The layer says what a table *means*, never what a question *maps to*. |
| ④ Retrieve | **The seam is already cut.** `RetrievedContext` is the single object the generator sees, and *"the generator never learns which one produced its context"* | `pipeline/state.py`, `pipeline/nodes/__init__.py::retrieve` | Three lexical branches, no pair store, no embeddings anywhere in `backend/app`. |
| ⑤ Measure | **A real comparator.** Execution accuracy over result sets, with `NUMERIC_REL_TOLERANCE=1e-6` / `NUMERIC_ABS_TOLERANCE=5e-3`, three equivalence modes (`scalar_numeric`, `set_unordered_by_columns`, `ordered_rows`) and a six-label outcome taxonomy | `app/eval/metrics.py`, `app/eval/dataset.py` | It is a developer CLI, costs real money, is fenced off the request path by an import-linter contract, and has no UI. |
| ⑤ Measure | **The gold-record schema is already the pair schema.** | `GoldRecord`: `id, question, connection_fixture, expected_tables, gold_sql, result_equivalence, expected_chart_type, tags, difficulty, verification` | Nothing writes one from a real run. |
| Safety | Four guard entry points, all replaying the hostile corpus | `test_sqlguard_hostile.py`, `test_query_service.py`, `test_report_guard.py`, `test_dashboard_transfer.py` | A pair store is a **fifth**, and it needs its own replay. |

Two structural advantages worth naming, because they change which option is
right:

**DataMind's comparator is better than Genie's, and far better than Fabric's.**
Genie compares up to 5,000 rows at 4 significant figures. Fabric gave up and used
an LLM judge, which costs a model call per benchmark row and returns
*true / false / **unclear***. DataMind already has a deterministic, unit-tested,
zero-cost comparator with an explicitly reasoned tolerance. **Stage ⑤ is the
expensive stage everywhere else and the cheap stage here.**

**`GoldRecord.verification: dual_form` is a discipline nobody else has.** The
eval requires every gold to have a *structurally different twin* that agrees on
the fixture, so a gold is checked against something other than itself. Applied to
verified pairs, this is the answer to Fabric's Relatedness score — not a
model's opinion about whether SQL matches a question, but a second statement that
must produce the same rows.

---

## 5. Options

Seven options, ordered roughly by increasing ambition. They are not exclusive —
§5.8 recommends a composition. Each carries the same header so they can be
compared directly.

Sizes follow the plan's convention: **S** = days, **M** = 1–3 weeks, **L** = a
month or more.

---

### Option A — Verified answer cache: near-exact match short-circuits the model

**What it is.** A per-connection store of `(question, sql, note, verified_by,
verified_at)`. On a new question, before `generate` runs, look for a stored pair
whose question matches closely. On a hit, **skip generation entirely**: re-validate
the stored SQL through the guard against the current snapshot, execute it, and
present the answer with a **Verified** badge and the matched stored question
visible ("answered from a saved question: *'revenue by month, last 12 months'* —
generate a fresh answer instead"). On a miss, the run proceeds exactly as today.

*From: Genie trusted assets; Power BI verified answers; Wren's "first search
through views you saved".*

**How it lands here.** A new table plus a branch in the graph before `generate`.
Crucially it **does not touch any prompt** — `GENERATE_SYSTEM` is byte-identical,
`PROMPT_VERSION` does not move, and every recorded eval number stays comparable.
Matching can start purely lexical (normalised question text + `pg_trgm`
similarity, which ships with the official Postgres image) at a deliberately high
threshold.

| Pros | Cons |
|---|---|
| **Cannot regress generation.** The prompt is unchanged; the only new failure mode is a bad match, and that is visible and one click from being undone. | **Brittle without parameterisation.** "Revenue last month" is a different stored row from "revenue in July" unless L2 is addressed. |
| **Zero model calls, database-time latency, zero tokens** on a hit — the only feature in this document that makes the product *cheaper*. | A false match is a *confident wrong answer*, the worst failure class for a trust-first product. Mitigated only by the visible matched-question affordance. |
| Ships the **badge** (L3) on day one, which is what makes curation legible and therefore what makes anyone curate. | Value is zero until the store has content — the cold-start problem is at its worst here. |
| Bootstraps for free from `sql_origin IN (GENERATED_EDITED, HANDWRITTEN)` tiles and report blocks, which already carry `question` + corrected `sql`. | Staleness bites hardest: a stored statement runs months later against a moved schema. (The guard already fails closed on this — it is a *user-visible failure*, not a safety hole.) |
| Measurable without the eval suite: hit rate, override rate, and "same question asked again" frequency are all countable from `runs`. | Adds a pre-`generate` branch to a graph the codebase deliberately keeps small. |

**Size: S–M.** New table + migration, a match function, a graph branch, a badge,
one write endpoint, the tile/block backfill.

---

### Option B — Few-shot pairs injected into `GENERATE`, lexical retrieval

**What it is.** The same store, used the other way: retrieve the *k* most similar
pairs and render them into the generate prompt as worked examples, the way Fabric
retrieves *"typically the top four"*. `RetrievedContext` gains an `examples`
field; `GENERATE_SYSTEM` gains an `{examples}` slot that renders to the empty
string when there are none.

*From: Genie example queries; Wren Question-SQL Pairs; Fabric few-shots.*

**How it lands here.** This is the option the existing architecture was shaped
for — *"embeddings are later strategies behind the same `RetrievedContext` shape;
the generator never learns which one produced its context."* The seam exists;
this fills it.

| Pros | Cons |
|---|---|
| Generalises where Option A cannot: a pair about *revenue by month* improves *cost by quarter*, because it teaches the join and the date convention, not the answer. | **This is a prompt change, and this repo has measured prompt changes backfiring.** A "getting the answer right" block in `GENERATE_SYSTEM` moved execution accuracy **36% → 26%** and parse 98% → 88% on the small eval model. Examples crowd out the schema exactly as that block did. |
| It is the mechanism every commercial competitor actually relies on for accuracy. | Costs tokens on **every analytical question**, including the ones no pair helps. Competes for budget with the schema block *and* the 8k semantic-layer allocation. |
| No new dependency and no new deployment unit if retrieval is `pg_trgm` + exact-name matching. | Lexical similarity misses paraphrase, which is most of the value. "Churn" does not retrieve the pair written about "attrition". |
| Reuses the existing `_RETRIEVE_BUDGET_CHARS` discipline and the line-by-line fitting already written for the semantic block. | **`PROMPT_VERSION` must move (v8 → v9)**, so every number recorded before it becomes non-comparable — on top of the v7→v8 move that has not been baselined yet. |
| Failure is graceful: no pairs → byte-identical prompt → the current baseline still holds. | A wrong pair actively teaches the model to be wrong, on questions the curator never considered (this is exactly what Fabric's conflict detection exists to catch). |

**Size: M.** Store + retrieval + prompt slot + budget fitting + a full eval arm to
prove it did not hurt.

---

### Option C — Option B with embeddings (hybrid retrieval)

**What it is.** Replace lexical similarity with embedding similarity, blended
with the existing exact-match and FK expansion — plan §B2, extended to cover the
pair store as well as the schema.

**Two implementation routes, and they are not equivalent:**

1. **`pgvector` in the app database.** The plan says this *"adds no deployment
   unit"*. That is not quite right: `docker-compose.yml` runs `postgres:16-alpine`,
   which does **not** carry pgvector. It means a new base image (or an extension
   install) for every self-hosted deployment, and it is not available on every
   managed Postgres a customer might point at. `pg_trgm`, by contrast, ships with
   the official image and needs only `CREATE EXTENSION`.
2. **Embeddings through the existing `LLMGateway` port.** LiteLLM already backs
   the gateway and supports embedding endpoints, so this adds no Python
   dependency — but it adds a **provider capability requirement** to a product
   whose selling point is that you point it at whatever model you have. An admin
   with a chat-only endpoint (a self-hosted gemma, an internal gateway) gets
   nothing.

| Pros | Cons |
|---|---|
| The only option that reliably retrieves on paraphrase, which is where the value is. | **Both routes break a positioning promise**: pgvector breaks "no new deployment unit"; gateway embeddings break "provider-agnostic, run it against anything". |
| Same vectors serve plan §B2 (schema retrieval) and §A5 (synonym index) — one investment, three payoffs. | Vectors need re-computation when a pair or the schema changes; a stale index is a silent accuracy regression with no error to point at. |
| The literature is clear about *how* to do it well: **masked question similarity** — replace table names, column names and literal values with a generic token before embedding — so "revenue in July for West" retrieves the pair written for "revenue in March for East". | Adds a genuinely new subsystem (index lifecycle, backfill, dimension/model pinning) to a codebase that currently has zero embeddings. `grep -rn "embedding\|vector" backend/app` returns two prose comments and no code, and that is a feature. |
| DAIL-SQL-style selection (score on question **and** query similarity) is well-studied and cheap to implement once vectors exist. | An embedding model is a **second** model whose version becomes part of the reproducibility story — `model_snapshot` and `PROMPT_VERSION` do not currently cover it. |

**Size: M–L**, and it is the option most likely to be under-estimated.

**Recommended middle path:** make the matcher an interface with a lexical
implementation that always works and an embedding implementation used *when the
connection's LLM config exposes one*. The learning loop must degrade to lexical,
never to nothing — anything else makes the flagship feature conditional on the
customer's provider.

---

### Option D — Corrections become **semantic-layer** entries, not pairs

**What it is.** Keep one knowledge artifact instead of two. A correction is
routed into the existing `semantic_layers` document — a new `SemanticMetric` with
the corrected expression, a fixed grain statement, a `GlossaryTerm`, a join
caution — rather than into a new pair store.

*From: Wren's MDL-first philosophy; Genie's measures/filters/fields.*

| Pros | Cons |
|---|---|
| **No new store, no new retrieval path, no new disclosure question.** Everything already flows: generate → validate → merge → render, with `provenance.edited` protecting human edits through regeneration. | **A correction usually is not a definition.** "You joined through the wrong bridge table" and "last month means the calendar month here" do not compress into a metric. Forcing them there loses the correction. |
| Metric expressions are already SQLGlot-parsed on save and flagged-not-deleted when invalid — Fabric's schema validation, already built. | The layer renders into `GENERATE` only. A metric cannot short-circuit a question, so Option A's badge, latency and cost wins are all unavailable. |
| Fixes the same problem from the other end: a metric is inherently *parameterized* (it is an expression, not an answer), which is L2 solved by construction. | The layer is one document per connection with a **JSONB blob** shape and no version chain — it is already flagged as §1.3's weakness. Pouring per-question corrections into it makes that worse, not better. |
| Cheapest possible option. Genuinely small. | No path to stage ⑤: a metric is not a benchmark row, so accuracy still cannot be measured by a customer. |

**Size: S.** Real value, low ceiling. Best understood as a *complement*, not an
alternative: metric-shaped corrections belong in the layer, question-shaped
corrections do not.

---

### Option E — A question-matching **instructions** layer

**What it is.** Free-text rules, in two scopes: **global** (rendered on every
generate prompt) and **question-matching** (rendered only when the question
matches a trigger). Wren's exact split.

| Pros | Cons |
|---|---|
| Captures the corrections that have no SQL — "never count test accounts", "fiscal year starts in April", "always exclude cancelled orders from revenue". | **The repo has measured that more prose in `GENERATE_SYSTEM` lowers accuracy** on a small model. A global instruction is precisely the shape of the change that scored 36% → 26%. |
| Question-matching scope is the mitigation for exactly that: the tax is paid only on the questions the rule is about. | Free text is unvalidatable. There is no parser that can tell a good instruction from a self-contradictory one, and no conflict detection is possible. |
| Trivial to author. No schema, no SQL literacy required — the widest possible set of people can contribute. | Instructions accumulate and rot invisibly. Genie caps them at **100 per agent** for a reason. |
| Overlaps almost entirely with the semantic layer's existing free-text fields (`business_context`, exclusion rules, time conventions), so much of it is already there. | Highest ratio of "feels productive" to "measurably helps" of any option here. |

**Size: S**, and it should be gated behind a measurement, not shipped on faith.

---

### Option F — Feedback capture and mining only, no store

**What it is.** Stage ① and ② with no ③: thumbs up/down and a **Request review**
button on answers, an owner queue, a Monitor-style view of what people actually
asked, and a *"words retrieval did not recognise"* backlog in the spirit of Power
BI's Review questions. The owner acts on it by editing the semantic layer.

| Pros | Cons |
|---|---|
| **The cheapest thing that changes behaviour**, because the behaviour it changes is the owner's: it tells them what to curate. L6 says this is the hard part. | On its own it improves nothing. It is a to-do list. Databricks' warning applies with full force. |
| Turns a dead-end frustration into the highest-quality signal the system can get, from the people who know the answer. | Feedback UI with no visible payoff is worse than none: users learn their thumbs-down goes nowhere and stop. |
| Every other option is more valuable with it than without it, and it is a prerequisite for the review workflow the plan wants (§A4). | Needs notifications, permissions and a queue — all real work in a product that is still single-player (§1.5). |
| It is the only option that produces the **cold-start** answer: the first 20 pairs come from the 20 most-asked questions, which only this surfaces. | Requires deciding who "the owner" is before ownership exists as a concept. |

**Size: S–M.**

---

### Option G — Fine-tuning or reinforcement learning on collected pairs

**What it is.** Accumulate corrections, fine-tune a model (or train a reward model
and do RL on execution feedback), ship the improved weights.

**Recommendation: reject for MVP2, and probably permanently.** Recorded here so
the decision is on paper rather than re-argued.

| Pros | Cons |
|---|---|
| The only approach where knowledge is *in the model* rather than in the prompt, so it costs no context budget at inference. | **Incompatible with the product.** DataMind is provider-agnostic and BYO-model by design; fine-tuning presumes a model you own and serve. |
| Genuine literature support, and can outperform prompting on a narrow domain. | Per-customer weights in a self-hosted product is a support and cost model nobody wants to operate. |
| | Needs orders of magnitude more pairs than any single customer will ever produce. |
| | **Unauditable.** A tuned weight cannot be reviewed, diffed, version-controlled or switched off per connection. A curated pair can be all four. This trades away exactly the property the product sells. |
| | No competitor in this research does it. Data Formulator's distillation work is the nearest, and it is labelled experimental. |

**Size: L+, and wrong.**

---

### 5.8 The options side by side

| | A · verified cache | B · few-shot (lexical) | C · few-shot (embeddings) | D · into the layer | E · instructions | F · feedback only | G · fine-tune |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Size | S–M | M | M–L | S | S | S–M | L+ |
| Touches `GENERATE_SYSTEM` | **no** | yes | yes | no *(renders in the layer block)* | yes | no | n/a |
| `PROMPT_VERSION` moves | **no** | **yes** | **yes** | no | **yes** | no | n/a |
| New runtime dependency | no | no | **yes** (pgvector **or** an embedding-capable provider) | no | no | no | yes |
| Can regress current accuracy | no | **yes** | **yes** | marginal | **yes** | no | yes |
| Cuts cost / latency | **yes** | no | no | no | no | no | no |
| Ships a visible trust badge | **yes** | no | no | no | no | no | no |
| Answers "is it getting better?" | partially | no | no | no | no | **yes**, qualitatively | no |
| Generalises beyond the exact question | no | **yes** | **yes** | **yes** | **yes** | n/a | yes |
| Reversible per connection | yes | yes | yes | yes | yes | yes | **no** |
| Solves the cold start | no | no | no | no | no | **yes** | no |

**Read the `PROMPT_VERSION` row together with the "can regress" row.** Options B,
C and E all change what the generator sees and all can lower accuracy — and the
repo has *already measured this happening twice*. Options A, D and F cannot. That
is the strongest single argument about sequencing in this document.

---

### 5.9 Recommendation

**Build A + F first, as one release. Then B, gated on a measurement. Treat C as
an upgrade to B's matcher, not a separate feature. Fold D in wherever a
correction happens to be metric-shaped. Defer E until B has been measured.
Reject G.**

The reasoning, in order:

**1. Fix the ruler before measuring anything.** Two blockers from the plan are
genuinely blocking, and neither is optional:

- **`runs.prompt_version` drift** (§1.10) — the baseline file records `v2`, the
  constant is `v8`, and *"every row written since claims v2 anyway"*. Until a run
  records the prompt version it actually used, no before/after comparison in
  MVP2 means anything.
- **Retrieval recall is 1.0 by construction** (§B1) — `_RETRIEVE_BUDGET_CHARS` is
  50k against a fixture estimating 26,480, so every eval question takes
  `FULL_SNAPSHOT`. Any claim that pairs improved retrieval is unfalsifiable today.

Add a third, from §A6: **the v7 → v8 semantic-layer baseline has never been
taken.** The layer's A/B has not been run against a prompt that contains the
layer. Starting the learning loop on top of an unmeasured prompt change means two
unknowns moving at once.

**2. Ship the badge before the machinery.** Option A delivers the visible
*Verified* tier with no prompt risk. It is what makes a connection owner believe
curation pays, and curation effort is the input every later stage consumes. Power
BI and Genie both put the badge in the UI; neither treats it as a nice-to-have.

**3. Ship the queue with it.** Option F is what tells the owner *which* pair to
write. A pair store with no backlog fills up with whatever the first curator
happened to think of; a pair store fed by the twenty most-asked questions is a
different product. Bootstrap it from `sql_origin IN (GENERATED_EDITED,
HANDWRITTEN)` — those corrections already exist and are already inert.

**4. Only then inject into the prompt, and prove it.** Option B is where the
generalisation is, and it is also where the 36% → 26% precedent lives. Run it as
an eval arm — a `--pairs` flag alongside the existing `--comments` arm — and
report the delta honestly, including the possibility that it is negative on small
models. Note the precedent worth remembering: FK-neighbour expansion lifted recall
70% → 86% with **flat** execution accuracy. Retrieval improvements do not
automatically become answer improvements.

**5. Make the matcher pluggable from the start** so C is a swap and not a rewrite,
and so a customer whose provider has no embedding endpoint still gets a working
loop.

---

## 6. Six decisions that must be made before any of this is built

These are the parts the competitor research does *not* answer, either because the
vendors solved them differently or because they did not solve them at all. Each
is a decision for DataMind, and each has a wrong answer that would cost more than
the feature is worth.

### 6.1 Is a verified pair a **disclosure**? ⚠️ the one nobody else got right

**This is not covered by [mvp2-plan.md §A1](../mvp2-plan.md), and it should be.**
§A1 correctly identifies a pair as a *guard* question — hostile input, a fifth
entry point, replay the corpus. It does not identify it as a *disclosure*
question, and it is one.

The invariant, from CLAUDE.md: a connection declares `NONE | AGGREGATE | SAMPLE |
FULL`, and `HintBudget` gates what the **schema block** may say about a column's
contents. Under `NONE` and `AGGREGATE`, `value_lists` is **false** — no literal
value read from a row reaches the model, ever, on any question.

Now consider a verified pair:

```sql
-- question: "revenue from enterprise customers last quarter"
SELECT SUM(amount) FROM orders
WHERE tier = 'ENTERPRISE' AND region = 'EMEA' AND status <> 'CANCELLED'
```

Injected as a few-shot under Option B, or rendered in a "this is the saved answer"
panel under Option A, **that statement puts three distinct column values into the
prompt on a connection whose policy is `NONE`.** The `HintBudget` ladder is
bypassed — not by a bug, but because the pair travels on a path the ladder does
not cover.

**There is a defensible answer, and the codebase already contains the reasoning
for it.** Catalog comments are exempt from the disclosure gate, and the stated
reason is precise: *"A comment is DDL a human wrote: it is not read from a row, it
does not change when the data changes, and it is exactly as much 'customer data'
as a column name."* A pair **hand-written by a connection owner** meets all three
tests — it is authored, static, and no more revealing than the schema. So the
rule follows from existing precedent rather than needing a new principle:

> **A pair's literals travel with structure when a human wrote them, and are
> gated like sample values when a machine did.**

Which means the provenance of a pair is load-bearing, and the awkward cases are
real:

- A pair **mined from a `GENERATED_EDITED` tile** is a hybrid: the human edited a
  statement whose literals the *model* chose — and the model may have chosen them
  from sampled values disclosed under a policy that has since been tightened.
  Tightening a policy must take effect on the next question (that is the existing
  rule, enforced at render time, not write time); a pair store that survives the
  tightening quietly undoes it.
- A pair written under `FULL` and read under `NONE` after a downgrade is the same
  problem with a different origin.

**Recommended decision:** store `literal_provenance` on every pair
(`HUMAN_AUTHORED` | `MODEL_DERIVED`), render `MODEL_DERIVED` pairs only when
`HintBudget.value_lists` is true, and treat this exactly as §B3 treats value
dictionaries — *"a disclosure decision, not a performance feature"*, with its own
control, its own rung on the ladder, and its own section in
[security.md](../security.md). This is Lesson L7, and Microsoft shipping verified
answers with RLS unsupported is the evidence for taking it seriously.

### 6.2 The guard: a fifth entry point, and a re-validation policy

§A1 already says a pair goes through the guard on execution, with a
`test_verified_pairs_guard.py` mirroring `test_report_guard.py`. Two additions
from the research:

**Validate on save *and* on use.** Fabric validates every example against the
data source's schema and *"queries that don't pass validation aren't sent to the
agent"*. DataMind's tile path already re-validates against the connection's
**current** snapshot on every execution, and pairs must do the same. The two
checks answer different questions: *is this SQL legal at all* (save time) and *is
it still legal against the schema as it is now* (use time).

**Decide what a failing pair does.** This is a fifth-posture question in the
codebase's own taxonomy. A pair that no longer resolves should **fail as a
value** — marked stale, withdrawn from retrieval, surfaced in the owner's queue —
not fail the run and not silently vanish. It mirrors the semantic layer's existing
rule: an invalid *generated* metric is dropped, an invalid *human-written* one is
flagged and kept, because *"deleting a person's work to hide drift is worse than
showing it."*

### 6.3 Conflicts: two pairs that answer the same question differently

Fabric is the only product that checks for this, and its taxonomy is directly
portable: same intent → different tables; same metric → different aggregation or
granularity; materially different results for the same business question.

DataMind can do better than Fabric here, and cheaply, because **it has a
comparator and Fabric does not**. Fabric detects conflicts by reasoning over the
SQL text. DataMind can detect them by *running both statements and comparing
result sets* with `app/eval/metrics.py` — the same function that decides execution
accuracy. Two pairs whose questions are near-duplicates and whose results differ
is a **fact**, not a confidence score of 1–5.

This is a genuinely differentiating feature, it is small once pairs exist, and it
is the thing that stops a curated store from decaying into noise.

### 6.4 ⚠️ The measurement trap — and a correction to §A3

[mvp2-plan.md §A3](../mvp2-plan.md) says of benchmarks and verified pairs:
*"A1's verified pairs are already exactly this shape, so the two features share a
table."*

**They are the same shape and they must not be the same rows.** If a pair is
injected into the prompt (Option B) *and* scored as a benchmark row, then the
benchmark measures whether the model can copy an example it was just handed.
Accuracy goes up, the graph in the Evaluations tab goes up, and **nothing has been
learned about questions the customer has not already curated** — which is the only
thing accuracy is supposed to predict.

The trap is well disguised because the workflow that causes it is the one every
vendor recommends: *find a failure → add an example for it → re-run the
benchmark → watch the number move*. That loop is correct for **fixing** and
useless for **measuring**, and Genie's docs do not distinguish the two.

Three rules that keep the measurement honest, all consistent with the eval
harness's existing discipline (*"An eval you are allowed to edit measures your
willingness to edit it"*):

1. **A pair may be a benchmark row or a retrievable example, never both at once.**
   One flag, enforced in the query that builds the few-shot candidate set — not a
   convention.
2. **Hold out a fraction of pairs at creation**, and never retrieve them. That
   held-out set is the only number worth putting in front of a customer.
3. **Report the split.** Accuracy on questions answered *from* a pair and accuracy
   on questions answered *without* one are two different numbers, and only the
   second one moves for a reason. Genie's Evaluations tab shows one number; that
   is a weakness to improve on, not a design to copy.

### 6.5 Parameterisation — the difference between a demo and a feature

Lesson L2, restated as a decision. Genie uses typed `:param` placeholders with
descriptive comments. Power BI uses up to three natural-language-settable filters
and caps permutations at 10. Without something equivalent, a store of literal
pairs answers only the questions already asked, curation never compounds, and the
hit rate of Option A's cache stays near zero.

DataMind has an unusual advantage: **the guard's AST is already there.** SQLGlot
parses every statement and the validation report already carries
`referenced_tables` and `referenced_columns`. Detecting which literals in a stored
statement are *date bounds* versus *categorical filters* is an AST walk, not a
model call — which means parameter extraction can be **offered automatically at
save time** ("this looks like it should work for any month — make the date range a
parameter?") rather than demanded from the curator. No competitor does this,
because none of them has a guard that already understands the statement.

**Do not defer this to a later phase.** It is the difference between a store with
a 3% hit rate and one with a 30% hit rate, and retrofitting parameters onto pairs
authored without them means re-curating everything.

### 6.6 The cold start, and who owns it

Every one of these features is worth zero on day one, and the honest framing of
§1.1's second consequence — *"the value of a deployment is stuck at day one"* —
cuts both ways: a learning loop that needs 50 curated pairs before it helps has
simply moved day one later.

Three sources of day-one content, in order of cost:

1. **Free, already in the database.** Every `dashboard_tiles` and `report_blocks`
   row with `sql_origin IN (GENERATED_EDITED, HANDWRITTEN)` carries a `question`
   and human-corrected `sql`. These are verified pairs that exist right now and
   are read by nothing.
2. **Cheap, from the semantic layer.** Every `SemanticMetric` with an exact SQL
   expression is a pair whose question is its business name. The layer is
   *already* the highest-quality curated content in the product.
3. **The queue.** Option F's ranked backlog of most-asked questions, which is what
   turns curation from an open-ended chore into a finite list.

And the governance question underneath: **there is no "owner" concept yet.**
DataMind is single-player (§1.5); Genie's review workflow is gated on `CAN
MANAGE`, and Power BI's on write permission to the semantic model. A review queue
needs a person to route to. Either the loop ships scoped to the connection's
creator — defensible, and honest about the limitation — or it waits on §D1/§D2,
which is a much longer dependency than the feature deserves.

---

## 7. A concrete sketch of the recommended path

Not a plan; enough detail to argue with. Follows the codebase's existing shapes
deliberately.

### 7.1 The store

```
verified_queries
  id                    uuid pk
  connection_id         uuid  fk → database_connections  ON DELETE CASCADE
  question              text                  -- the natural-language form
  question_normalized   text                  -- lowercased, literals masked; the match key
  sql                   text                  -- guard-validated on write AND on read
  params                jsonb  default []     -- [{name, type, comment}] — §6.5
  note                  text                  -- "how and when to use this", per Genie
  source                text                  -- CHAT_CONFIRMED | CHAT_CORRECTED | TILE | REPORT_BLOCK | MANUAL
  literal_provenance    text                  -- HUMAN_AUTHORED | MODEL_DERIVED  — §6.1
  role                  text                  -- RETRIEVABLE | BENCHMARK_ONLY | HELD_OUT — §6.4
  status                text                  -- ACTIVE | STALE | CONFLICTED | ARCHIVED
  schema_version        int                   -- the snapshot it was validated against
  verified_by           uuid  fk → users
  verified_at           timestamptz
  last_validated_at     timestamptz
  hit_count             int   default 0       -- earns its place, or gets pruned
  UNIQUE (connection_id, question_normalized)
```

`schema_version` mirrors `semantic_layers.schema_version` so the UI can say the
schema has moved on underneath a pair, the same way it already does for the layer.
`role` and `literal_provenance` are the two columns that exist purely because of
§6.4 and §6.1 — they are the cheapest possible enforcement of two rules that are
otherwise conventions nobody will remember in six months.

### 7.2 Where it plugs in

```
WRITE ── chat: "this answer was right"        → CHAT_CONFIRMED
      ── chat: owner edits the SQL, saves     → CHAT_CORRECTED
      ── backfill: tiles/blocks where
         sql_origin ∈ {GENERATED_EDITED,
                       HANDWRITTEN}           → TILE | REPORT_BLOCK
      ── editor beside the semantic layer     → MANUAL
              │
              ├── guard.validate() against the current snapshot   ← fifth entry point
              ├── AST walk → propose params                        ← §6.5
              └── conflict check vs existing pairs (run both, compare results)  ← §6.3

READ  ── (Option A) before `generate`:
             match question_normalized, high threshold
             → hit: re-validate, execute, badge Verified, show the matched question
             → miss: fall through, prompt unchanged

      ── (Option B) inside `retrieve`:
             RetrievedContext.examples = top-k RETRIEVABLE pairs
             → GENERATE_SYSTEM gains {examples}; empty ⇒ byte-identical prompt
             → PROMPT_VERSION v8 → v9

MEASURE ─ pairs with role=BENCHMARK_ONLY | HELD_OUT feed the in-product
          benchmark, reusing app/eval/metrics.py's comparator, never the LLM
          judge Fabric had to fall back on
```

### 7.3 The three-tier badge

Plan §A2's tiers, with the research's addition — **show the match, not just the
badge** (Power BI's single best UI decision):

| Tier | Means | Shown |
|---|---|---|
| **Verified** | answered from a stored pair, or a metric's exact SQL | badge **+ the matched stored question + "generate a fresh answer instead"** |
| **Grounded** | generated, but every table it touched has a semantic-layer entry | badge |
| **Generated** | generated against bare schema | say so plainly |

### 7.4 What to measure, and in what order

| Step | Question it answers | Gate |
|---|---|---|
| 0 | Does `runs.prompt_version` record the truth? | §1.10 — **blocking** |
| 0 | Does retrieval recall measure anything? | §B1 — **blocking** |
| 0 | What does the v8 prompt score with the layer on vs off? | §A6 — the missing baseline |
| 1 | Option A: hit rate, override rate, "asked again" rate | no eval needed; countable from `runs` |
| 2 | Option B: execution accuracy on **held-out** questions, with and without pairs | a `--pairs` eval arm beside `--comments` |
| 3 | Option C: retrieval recall delta from embeddings, and whether accuracy follows | remember: FK expansion moved recall 70→86% with **flat** accuracy |

---

## 8. Open questions this research could not close

Stated so nobody re-runs the same searches.

1. **Wren's question-matching mechanism is undocumented.** The Global vs
   Question-Matching split is public; whether matching is embeddings, keywords or
   a classifier is not. Answerable by reading `Canner/WrenAI`, which is
   Apache-2.0 — the highest-value follow-up in this list.
2. **No vendor publishes an accuracy delta for their learning loop.** Not
   Databricks, not Microsoft, not Wren. Every claim is qualitative
   ("improves accuracy", "gets better over time"). Power BI's *"Copilot also
   learns from how users interact with verified answers"* is the least evidenced
   claim in the research and should not be repeated as fact.
3. **Nobody publishes a hit rate for the short-circuit path.** What fraction of
   real questions are answered from a stored pair rather than generated is the
   number that would decide Option A's value, and it is not in any public
   document. DataMind can measure its own within a week of shipping.
4. **Genie's similarity matching is a black box.** *"Genie can either use the
   example query directly or learn from it to handle similar questions"* — the
   threshold, the embedding and the k are all unpublished.
5. **Data Formulator's knowledge-distillation work has no paper yet** beyond the
   Foundry Labs line quoted in §1.4. Worth re-checking at MSR in a few months;
   this is the only genuinely novel direction of the four.

---

## 9. Sources

**Databricks AI/BI Genie**
- [Tune Genie Agent quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality)
- [Test and monitor a Genie Agent](https://docs.databricks.com/aws/en/genie-agents/monitor)
- [Use benchmarks in a Genie space](https://docs.databricks.com/aws/en/genie/benchmarks)
- [Use trusted assets in AI/BI Genie spaces](https://docs.databricks.com/aws/en/genie/trusted-assets)
- [Building confidence in your Genie space: benchmarks and Ask for Review](https://www.databricks.com/blog/building-confidence-your-genie-space-benchmarks-and-ask-review)

**Wren AI**
- [Knowledge Overview](https://docs.getwren.ai/oss/guide/knowledge/overview)
- [Question-SQL Pairs](https://docs.getwren.ai/cp/guide/knowledge/question-sql-pairs)
- [Instructions](https://docs.getwren.ai/oss/guide/knowledge/instructions)
- [Adjust Answers](https://docs.getwren.ai/oss/guide/home/adjust_answer)
- [Canner/WrenAI on GitHub](https://github.com/Canner/WrenAI)
- [Beyond Text-to-SQL: Why Feedback Loops and Memory Layers Are the Future of GenBI](https://medium.com/wrenai/beyond-text-to-sql-why-feedback-loops-and-memory-layers-are-the-future-of-genbi-28b06512a0a2) *(vendor blog; not directly retrievable — cited via search summary, treat with care)*

**Power BI and Microsoft Fabric**
- [Prepare your data for AI — Verified answers](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-verified-answers)
- [Prepare your data for AI — AI instructions](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-instructions)
- [Intro to Q&A tooling to train Power BI Q&A](https://learn.microsoft.com/en-us/power-bi/natural-language/q-and-a-tooling-intro)
- [Teach Q&A to understand questions and terms](https://learn.microsoft.com/en-us/power-bi/natural-language/q-and-a-tooling-teach-q-and-a)
- [Edit Q&A linguistic schema and add phrasings](https://learn.microsoft.com/en-us/power-bi/natural-language/q-and-a-tooling-advanced)
- [Data Agent example queries](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries)
- [Evaluate a Fabric data agent](https://learn.microsoft.com/en-us/fabric/data-science/evaluate-data-agent)

**Microsoft Data Formulator**
- [Data Formulator — Microsoft Foundry Labs](https://labs.ai.azure.com/innovations/data-formulator/)
- [Data Formulator 0.7 (MSR blog)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/)
- [microsoft/data-formulator](https://github.com/microsoft/data-formulator)

**Literature**
- [Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation (DAIL-SQL)](https://arxiv.org/abs/2308.15363) — 86.6% execution accuracy on Spider; the source for question-similarity, **masked** question-similarity, and joint question+query selection of few-shot examples.

**DataMind, internal**
- [docs/mvp2-plan.md](../mvp2-plan.md) §1.1, §1.3, §1.10, Theme A, Theme B
- [CLAUDE.md](../../CLAUDE.md) — the four non-negotiable invariants, the five failure postures, the semantic layer, the eval harness
- [docs/eval.md](../eval.md) · [docs/security.md](../security.md) · [docs/pipeline.md](../pipeline.md)
