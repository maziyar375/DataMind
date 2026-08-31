# The semantic layer as a model — competitor research and options for DataMind

> **Subject:** [mvp2-plan.md §1.3](../mvp2-plan.md#13-the-semantic-layer-is-a-blob-not-a-model) —
> *"The semantic layer is a blob, not a model"*, rated **High**. The render bug
> under it was fixed 2026-08-30 ([A6](../mvp2-plan.md#a6-fix-the-semantic-layer-render--s--done-2026-08-30));
> the design ceilings it names are what this document is about.
> **Scope:** how Microsoft Data Formulator, Wren AI, Databricks AI/BI Genie and
> Power BI / Fabric Copilot store, version, review and *enforce* a semantic
> layer, what is verifiable from public documentation, and what DataMind should
> build.
> **Desk research date:** 2026-08-31, against `main`. Every competitor claim is
> sourced; where the only source is a vendor blog or a third party rather than
> product documentation, this document says so.
> **Status:** research and options. Not a decision. §6 is an argument, §8 lists
> the decisions that have to be made before any of it is built.
> **Siblings:** [learning-loop.md](learning-loop.md) (§1.1) ·
> [retrieval-at-scale.md](retrieval-at-scale.md) (§1.2). The three overlap
> deliberately and the overlaps are marked.

---

## 0. The finding, in one page

**"Blob, not a model" is two separate defects, and the industry treats them as
two separate products.**

```
   defect ①  THE BLOB GAP                    defect ②  THE ADVISORY GAP
   one JSON document, overwritten in         a metric definition reaches the model
   place. no history, no diff, no author,    as a sentence. nothing checks that the
   no review, no export, no per-entry        generated SQL used it. an answer that
   addressing.                               obeyed the definition and one that
                                             ignored it are indistinguishable.

   fixed by: versioning, publish gates,      fixed by: compiling the definition
   files, per-entity addressing              into the query path, or attributing
                                             the answer back to it
```

DataMind has both. **Three of the four competitors have closed ② and only two
have properly closed ①** — which is the opposite of the intuition, and it changes
the build order.

The single most important observation in this research: **nobody closed the
advisory gap by writing a better prompt.** Every one of them made the definition
structurally unavoidable instead.

| Product | How a metric definition becomes binding |
|---|---|
| **Power BI** | Copilot writes DAX against a tabular model where a *measure is the only way to aggregate*. Enforcement by construction — there is no path to a raw column sum. |
| **Databricks** | A **Unity Catalog metric view** is a YAML object whose measures are read with the `MEASURE()` function; the query engine composes the stored expression. Genie can be pointed at metric views as data sources. |
| **Wren AI** | The engine *rewrites* submitted SQL against MDL: models, relationships, calculated fields and views are **expanded into executable SQL** for the target dialect before it runs. |
| **Genie (free-SQL path)** | Cannot enforce, so it **short-circuits** instead: a parameterized *trusted asset* is executed verbatim and the answer is labelled verified. |
| **DataMind today** | `metric revenue = SUM(o_totalprice) WHERE status <> 'CANCELLED'` is rendered as a line of prose in the prompt (`app/semantic/render.py:442`) and never referred to again. |

Four things worth knowing before reading further, because each changes an option:

1. **The version-control story is weaker than the marketing implies.** Only
   Power BI (TMDL, one file per table) and Wren (one `metadata.yml` per model)
   have real per-entry diffs. Genie's entire answer is *export the whole space as
   one JSON string* (`serialized_space`) and commit that — which is blob
   versioning, one rung above DataMind, not ten. And Power BI's own trust
   artifact, verified answers, is documented as **"Git integration isn't
   supported."** The incumbent has not unified the two halves either.
2. **Three of §1.3's claims do not survive contact with the code**, one of them
   materially: incremental generation already exists. §5 has the corrections.
3. **`audit_logs` already exists in the schema and nothing has ever written to
   it.** "Who changed this metric and why" has a table waiting for it.
4. **Attributing an answer back to the metric it should have used appears to be
   unsolved in the market.** Genie's badge is by construction (a trusted asset
   *was executed*), not by inspection of free SQL. That makes Option D below
   genuinely novel — which is a reason for care, not a reason for confidence.

---

## 1. The four, one by one

### 1.1 Power BI and Fabric — the model *is* the artifact, and the query surface

The one product where the semantic layer is not a sidecar to the query engine but
the only thing the query engine can see. That is the whole lesson.

**Storage and version control — the best in the field.** A semantic model is
serialised as **TMDL** (Tabular Model Definition Language), a text format that
replaced the legacy single JSON blob. It has *"a folder structure with separate
files for each table, perspective, role, and culture"*, so a pull request touches
the file for the table that changed. Combined with Fabric **Git integration**
(workspace ⇄ Azure DevOps/GitHub) and **deployment pipelines** (Dev → Test →
Prod), the loop is: branch, edit a measure, open a PR, review a readable diff,
merge, promote. Third-party practice adds a lint gate — Tabular Editor's *Best
Practice Analyzer* run in CI.

**Enforcement is free, because DAX has no back door.** A measure is a named
calculation stored on the model. Copilot generates DAX; DAX aggregates through
measures. There is no equivalent of "the model wrote its own `SUM()` and ignored
the definition", which is precisely DataMind's failure mode. This is the
structural advantage of a model-first BI tool over a text-to-SQL tool, and it is
worth being honest that DataMind cannot simply copy it.

**AI-readiness as first-class model metadata.** *"Prep data for AI"* carries
three artifacts, and they are worth separating:

| Artifact | What it is | Where it lives |
|---|---|---|
| **AI instructions** | Free-text guidance for the agent — terminology, defaults for ambiguous questions, output style. **10,000 characters.** | Saved as a **markdown `.md` file in the `/Copilot` folder of a PBIP project** since the February 2026 Desktop release — i.e. version-controlled like source.¹ |
| **AI data schema** | Which fields the agent may see at all | The model |
| **Verified answers** | A human-approved *visual* bound to trigger phrases | The **semantic model**, not the report |

**Verified answers, in detail**, because it is the closest thing in the market to
a trust badge and its limits are instructive. Each has one or more **trigger
phrases**; Copilot *"first checks for an exact or semantically similar match to
any trigger phrase"* and *"returns the verified answer instead of generating a
new response"*. The response carries a **"Verified checkmark"** — *"indicates the
response is human-reviewed and approved"* — plus the **matched trigger phrase**
and a *"How Copilot arrived at this"* disclosure. Limits: **250 per model, 15
trigger prompts each, 500 characters per prompt, 3 filters (10 permutations)**,
and the documented advice is *"aim for five to seven trigger phrases per verified
answer"*. Semantic matching tolerates synonyms, reordering and filter criteria in
the prompt; it explicitly does **not** tolerate *"adding, removing, or swapping
out fields"* or *"modifying or replacing the original measure"*.

**Two honest limits that matter to DataMind's design:**

- **"Git integration isn't supported."** The trust artifact is the one part of
  the model that does not version. Even the incumbent ships the badge and the
  version history as separate, unjoined features.
- **RLS/OLS are *"not fully supported as security features for verified
  answers"***, with an explicit warning that data *"might still be exposed (for
  example, through the file format in Git)"*. A curated artifact is a disclosure
  surface. §8.5 takes this seriously.

> ¹ The `/Copilot` folder detail comes from Tabular Editor's blog, not Microsoft
> documentation. Treated as secondary.

### 1.2 Wren AI — the closest analogue, and the design DataMind would recognise

Same architecture, same philosophy, one decision made differently: **Wren's layer
is a project of files that compiles, DataMind's is a row that renders.**

**The project layout** (from the CLI quickstart):

```
~/jaffle-wren/
├── wren_project.yml          catalog + schema defaults
├── models/
│   ├── customers/metadata.yml    ← one file per model
│   └── orders/metadata.yml
├── views/
├── cubes/revenue/metadata.yml
├── relationships.yml
├── knowledge/
│   ├── rules/                 business + operational guidance
│   └── sql/                   reviewed NL→SQL examples
├── .wren/memory/              derived LanceDB index (rebuilt, not authored)
└── target/mdl.json            ← compiled manifest the engine reads
```

Commands: `wren context init`, `wren context set-profile`, **`wren context
validate`**, **`wren context build`** (compiles YAML → manifest),
`wren memory index`.

Four design decisions worth stealing outright:

1. **Authored source and compiled artifact are different files.** YAML is what a
   human reviews; `target/mdl.json` is what the engine consumes. DataMind has
   exactly this split conceptually — the document and the rendered block — and
   currently gives neither of them an address.
2. **One file per model.** The unit of review is the entity, not the database.
3. **Credentials are deliberately outside the versioned artifact** —
   *"connection profiles live separately in `~/.wren/profiles.yml` so credentials
   stay environment-specific."*
4. **The retrieval index is derived, never authored** — `.wren/memory/` is
   *"rebuilt from `knowledge/`"*. It can be deleted without losing anything.

**Enforcement.** MDL is *"the semantic contract"*. At query time *"Wren AI
expands those models, relationships, calculated fields, and views into executable
SQL for the target data source"* — the Rust engine (`wren-core`, over Apache
DataFusion) *"will transform Wren SQL based on MDL definition in
dialect-specific SQL"*. A question can name a business concept; the engine, not
the LLM, resolves it to physical columns. **The metric is not advice; it is a
macro the engine expands.**

**Deployment is an explicit act.** In the server product you *"press the 'Deploy'
button on the Navbar to synchronize any modifications in the Modeling page with
the Wren Engine"*, and until you do, the navbar shows **"Undeployed changes"**.
Edit and publish are separate — the exact gap DataMind's `PUT` does not have.

> **A discrepancy this research could not close.** Wren's own material describes
> two project shapes — `instructions.md` + `queries.yml` at the project root
> (used in [learning-loop.md](learning-loop.md) §1.2 and still cited on the
> marketing site), and the `knowledge/rules/` + `knowledge/sql/` directories the
> CLI quickstart scaffolds. Same concepts; probably an older and a newer layout.
> Do not treat either path as canonical.

### 1.3 Databricks — governed metrics underneath, an un-versioned blob on top

Databricks split the problem in two, and the two halves have very different
maturity. Reading them together is the most useful thing in this section.

**Underneath: Unity Catalog metric views — a real, governed semantic layer.** A
metric view is *"a Unity Catalog view, written in YAML"* that *"separat[es]
measure definitions from the fields (also called dimensions) used to group,
filter, and aggregate them"*. Shape:

```yaml
version: 1.1
source: samples.tpch.orders
joins:   [...]
filter:  [...]
dimensions:
  - name: Order Year
    expr: YEAR(o_orderdate)
measures:
  - name: Total Revenue
    expr: SUM(o_totalprice)
```

Queried with the **`MEASURE()`** function; *"the query engine generates the
correct computation"*. Because it is a UC object it inherits catalog governance,
lineage and permissions, and the definition is *"portable across every surface:
AI/BI Dashboards, Genie, Notebooks, SQL applications"*. **Genie agents can take
metric views as data sources**, which is Databricks' actual answer to the
advisory gap: don't let the model define the metric, give it a surface where the
metric is already defined.

One caveat found in the docs and worth carrying into §8: `at_most_one_match:
true` on a join is *"not validated at runtime"*. Even the governed layer contains
assertions the engine trusts rather than checks — exactly the status of
DataMind's `fan_out_warning`.

**On top: the Genie space — no version history at all.** The Space API
(`/api/2.0/genie/spaces`) offers create/get/list/update/delete. `GetSpace`
accepts **`include_serialized_space`** (*"requires at least CAN EDIT permission
on the space"*), returning the whole space as one JSON string containing config
and sample questions, `data_sources` (tables **and metric views**), `instructions`
(text instructions, example SQL questions, SQL functions, join specifications and
SQL snippets — filters, expressions, measures) and `benchmarks`. The only
versioning primitive in the API reference is an **`etag`**, for concurrent-write
conflicts.

So the CI/CD story is: pull the blob, commit the blob, push the blob to the next
workspace. Community and vendor-blog sources report Genie spaces became
first-class **Databricks Asset Bundle** resources in CLI v1.3.0, which automates
that round trip.² Nothing in the documentation describes per-instruction history,
authorship, or a diff.

**The governance line is drawn at the object, not the entry.** Deleting a metric
from a Genie space leaves no trace in the space; deleting a metric view is a
Unity Catalog operation with lineage and permissions attached. The lesson for
DataMind is that **it is legitimate to put history on the artifact and skip
history on the entry** — but only if there is an artifact.

> ² DAB support for Genie spaces: community/vendor-blog sources (Advancing
> Analytics, Databricks Community, `databricks/cli` PR #4191). Treated as
> secondary; the `serialized_space` field itself is in the API reference.

### 1.4 Microsoft Data Formulator — no semantic layer, and that is a position

DF has no semantic layer, no metric store and no governance surface, and it is
not an oversight: DF is a *visualization* research prototype where the analyst is
present at every step and verifies each result by reading the generated code.
Its nearest relative is a **"data memory"** that remembers *relationships between
sources* across its connectors — closer to DataMind's derived joins than to a
metric definition.

The transferable point is negative and worth keeping: **a semantic layer is the
price of the analyst not being in the room.** DF does not pay it because the
analyst is always in the room. DataMind's chat is asynchronous and its dashboards
run on a schedule, so DataMind must.

---

## 2. Worth knowing: where "semantics as code" came from

Not on the brief, but §6's options are all variations on a fifteen-year-old
argument and it is cheaper to inherit its conclusions than to rediscover them.

- **LookML (Looker, 2012)** — credited as the first mainstream *"semantic layer
  as code"*, roughly a decade before the phrase attached to dbt and Cube. It
  moved model authoring to analysts and put it under Git.
- **dbt Semantic Layer / MetricFlow** — *"metrics as code in YAML files within
  their existing dbt projects… version-controlled, reviewed via pull requests,
  and deployed alongside dbt transformations."*
- **Cube** — headless: one model, served to any front end over REST/GraphQL/SQL.

Databricks' own architecture write-up states the target state plainly:
**"Semantics as code — CI/CD, Git-versioned, dev → staging → prod"**, with
governance *"enforced by design"* so that *"row/column policies travel with every
metric"*. It also names what the layer must contain — dimensions, measures, joins,
filters (*"encode business rules directly into the metric definition"*), and
**metadata**: *"ownership, descriptions, certification status, tags, and
synonyms."*

Measured against that list, DataMind's document holds dimensions, measures,
joins, filters, descriptions and synonyms — and is missing exactly **ownership
and certification status**, which are the two fields that only mean something
once history and review exist.

---

## 3. The comparison, at §1.3 resolution

`●` present · `◐` partial · `○` absent

| | DataMind | Data Formulator | Wren AI | Databricks | Power BI |
|---|:--:|:--:|:--:|:--:|:--:|
| **Form** | | | | | |
| Semantic layer exists | ● | ○ | ● MDL | ● metric views | ● tabular model |
| Stored as reviewable text/files | ○ *(JSONB row)* | n/a | ● YAML + compiled JSON | ◐ YAML in UC | ● TMDL |
| One file/record **per entity** | ○ | n/a | ● `models/x/metadata.yml` | ● one view per metric | ● one file per table |
| Compiled artifact separate from source | ◐ *(rendered, not stored)* | n/a | ● `target/mdl.json` | ○ | ◐ |
| **History** | | | | | |
| Version history of the layer | ○ | n/a | ● via Git | ○ *(space)* · ◐ *(UC object)* | ● via Git |
| Diff two versions | ○ | n/a | ● | ○ | ● readable diffs |
| Author / "who changed this" | ○ | n/a | ● commit | ○ | ● commit |
| Export / import as a document | ○ | n/a | ● files | ● `serialized_space` | ● PBIP |
| Concurrent-edit protection | ○ | n/a | ● Git | ● `etag` | ● |
| **Review** | | | | | |
| Draft vs published state | ○ | n/a | ● **Deploy** / "Undeployed changes" | ◐ *(deploy via DAB)* | ● deployment pipelines |
| Approval before it takes effect | ○ | n/a | ● PR | ○ | ● PR |
| Lint / quality gate over the layer | ◐ *(validator, not a gate)* | n/a | ● `context validate` | ○ | ● BPA in CI |
| **Bindingness** | | | | | |
| Metric definitions **enforced** | ○ *(advisory)* | n/a | ● engine expansion | ● `MEASURE()` | ● DAX-only |
| Answer attributed to the definition | ○ | n/a | ◐ | ◐ *(trusted assets)* | ● verified answers |
| Visible trust badge | ○ | ○ | ◐ | ● Trusted | ● Verified checkmark |
| **Upkeep** | | | | | |
| Incremental / targeted regeneration | ◐ *(undescribed only)* | n/a | ● per file | ● per view | ● per table |
| Drift detection vs the schema | ● *(flag-not-drop + re-key)* | n/a | ◐ | ◐ | ◐ |
| Synonyms reach **retrieval** | ○ *(generate only)* | n/a | ● memory index | ● entity matching | ● linguistic schema |

Two readings of this table matter.

**DataMind's drift handling is best in class and nobody talks about it.** Flag,
don't drop; keep a human's invalid entry visible; detect a whole-layer re-key and
say one sentence instead of forty (`frontend/src/components/semantic-drift.ts`).
No competitor documentation describes anything equivalent. That is a real asset
and §6 should not trade it away.

**The gap is not "no Git".** It is *no history, no author, no draft state, no
export, and no bindingness*. Git is one way to get four of those; it is not the
only way, and it is the most expensive one.

---

## 4. Seven lessons

**L1 — Separate the source from the compiled artifact.** Wren authors YAML and
compiles `target/mdl.json`. DataMind already has this split and does not
materialise it: `render_with_coverage` produces the compiled form on every run
and throws it away. Storing what was rendered *for a given run* costs almost
nothing and turns "why did the model answer that?" from a re-derivation into a
lookup.

**L2 — The unit of review is the entity.** TMDL's one-file-per-table and Wren's
one-`metadata.yml`-per-model exist for the same reason: a diff over a whole
database is not reviewable. Any DataMind history that only diffs whole documents
inherits the blob problem in a new table.

**L3 — Publishing is a separate act from editing.** "Undeployed changes" is a
better idea than it looks. Today a half-finished metric edit reaches the very
next question asked on that connection.

**L4 — A metric only stops being advice when something other than the model
composes it.** Every product that closed the advisory gap did so by taking
composition away from the LLM. None did it by asking harder in the prompt.

**L5 — Curation must be visible in the answer or nobody curates.** Genie's
Trusted, Power BI's Verified checkmark plus its matched-trigger-phrase
disclosure. The badge is not a reward for the curator; it is the *evidence* that
curating changed anything.

**L6 — A curated artifact is a disclosure surface.** Microsoft says the quiet
part out loud: verified answers can leak RLS-protected data *"through the file
format in Git"*. DataMind's `value_meanings` are literally values from the
customer's data (bounded to what the snapshot already holds — `app/semantic/
generator.py`), and an export moves them outside the system.

**L7 — Version control is a spectrum, and the cheap end is most of the value.**
Genie ships *whole-artifact export* and calls it CI/CD. Whole-document history +
diff + restore is an S-sized feature that answers most of §1.3's complaint; a
git-backed file tree is an L-sized feature that answers the rest.

---

## 5. What DataMind already has — and three corrections to §1.3

### 5.1 The starting position, precisely

| Capability | Where | State |
|---|---|---|
| Typed document with provenance per entry | `app/semantic/models.py` — `Provenance(source, edited, reviewed)` on entity, column, metric, glossary term, time block | **Better than Genie's.** Genie's instructions carry no provenance at all. |
| Validation bound to a schema snapshot | `app/semantic/validate.py` — `validate_document`, `check_expression` (SQLGlot), `derive_joins` | Runs on save *and* on read; flags rather than drops human entries. |
| Live expression checking in the editor | `POST .../semantic/check` → the same parser the save path uses | Genuinely rare. Wren has `context validate` as a CLI step; DataMind has it per keystroke. |
| Safe regeneration | `merge_documents` + `provenance.edited`; `REPLACE` is an explicit choice in the UI | Solves the problem TMDL solves with Git, without Git. |
| Drift + re-key detection | `validate.py`, `frontend/src/components/semantic-drift.ts`, `npm run test:drift` | No competitor equivalent found. |
| Per-connection on/off switch | `database_connections.semantic_layer_enabled` | The A/B mechanism §6 leans on repeatedly. |
| Denormalised counts for the list view | `entity_count`, `metric_count`, `reviewed_count`, `issue_count` | Already the skeleton of a curation dashboard. |
| An **unused** audit table | `infra/db/models.py:939`, `0001_initial` | `grep -rn "audit_logs\|AuditLog" backend/app` returns the model and the migration and **nothing else**. No code path writes a row. |
| A portable-document precedent | `GET /dashboards/{id}/export`, `POST /dashboards/import`, `services/dashboard_transfer.py` | Export/import over JSON (not a download, because the SPA holds a bearer token) with the guard re-run on every imported statement. Option C is this, again. |

### 5.2 Three claims in §1.3 the code does not support

**① "No incremental mode" — incorrect, and this is the material one.**
`semantic_jobs` has carried `mode` (`MERGE`/`REPLACE`) and `only_tables TEXT[]`
since `0003_semantic_layer`; `app/semantic/generator.py:259` filters the table
list by it; and the UI already offers it —
`frontend/src/components/semantic.tsx:2219` sends `only_tables: scope ===
'missing' ? undescribed : []` behind a **"Only what is missing"** choice that is
the *default* when a layer exists. §1.3's own example — *"add three tables to a
200-table database and there is no 'generate just these'"* — is supported today,
because three new tables are undescribed tables.

What is genuinely absent is narrower: **you cannot regenerate a table that
already has a description.** `undescribed` is `tables.filter(t => !t.described)`
(`semantic.tsx:263`), so a table whose *shape changed* — the case drift detection
exists to surface — has no targeted path; the only options are "every table" or
nothing. That is a UI gap over an API that already accepts the request.

**② "No version history, no diff, no who-changed-this" — correct, and it is a
deliberate decision, not an oversight.** `SemanticLayerRow`'s docstring argues
it: *"One live row per connection, not a version chain like `schema_snapshots`:
this document is edited by hand, and a user who fixes a grain statement expects
to have fixed it, not to have forked it."* That argument is right about
*branching* and wrong about *history* — history is undo and accountability, not a
fork. Any option in §6 that adds versions must keep the promise the docstring is
protecting: **one live document, always; versions are a log, never a branch, and
never a thing the user has to choose between.**

**③ "The glossary and synonyms never reach retrieval" — correct**, and it is
`retrieval-at-scale.md`'s to fix, not this document's. Carried here as Option G
only so the composition is visible.

### 5.3 What a metric is worth today, exactly

`app/semantic/render.py:442` renders:

```
metric revenue = SUM(o_totalprice) WHERE o_orderstatus <> 'CANCELLED' — needs public.orders; USD; asked for as sales, turnover.
```

That line is placed in tier 2 of the allocation, funded before column detail, and
then **nothing in `app/pipeline` or `app/sqlguard` refers to metrics again**
(`grep -n "metric" backend/app/pipeline/nodes/*.py backend/app/sqlguard/*.py`
returns three comments and zero logic). The generator may use it, ignore it,
half-use it, or invent a fourth definition, and the run records no difference
between those outcomes. **§1.3's "advisory" is not an understatement — it is the
literal implementation.**

---

## 6. Options

Seven options in three groups. They are not exclusive; §6.8 recommends a
composition. Sizes follow the plan's convention: **S** = days, **M** = 1–3 weeks,
**L** = a month or more.

Group I closes the blob gap. Group II closes the advisory gap. Group III is
upkeep — the reason a layer that exists stays true.

---

### Group I — Make it a model

#### Option A — A version chain: `semantic_layer_versions`, with diff and restore

**What it is.** Every write to `semantic_layers.document` — the `PUT`, and the
commit at the end of a generate job — first appends the *prior* document to a new
table: `(id, connection_id, version, document JSONB, schema_version,
author_user_id, source ENUM(SAVE|GENERATE|IMPORT|RESTORE), note, entity_count,
metric_count, issue_count, created_at)`. The live row is untouched, so nothing on
the read path changes. The UI gains a **History** tab: a list, a two-version
structural diff, and **Restore** (which is itself a `SAVE` producing a new
version, never a rewind). Retention: keep N (say 50) plus every version marked
`kept`, prune the rest.

*From: the whole field. This is the floor everyone else is already standing on.*

**How it lands here.** `schema_snapshots` is the in-repo precedent for a
versioned JSONB document keyed by connection, down to the `version` integer.
Diffing belongs in the frontend as DOM-free logic — `semantic-diff.ts` alongside
`semantic-drift.ts`, in the `npm test` suite that already holds nine such files.
Structural diff, not text diff: entity added / removed / re-graind, metric added
/ expression changed / filter changed, glossary term changed — because a
whole-document text diff of reordered JSON is the blob problem again (L2).

| Pros | Cons |
|---|---|
| **Cannot regress anything.** Render path, prompt bytes, `PROMPT_VERSION` and every recorded baseline are untouched. | History is not review. Nobody is prevented from publishing a wrong metric; they can only be shown who did it afterwards. |
| Makes `REPLACE`-mode generation and mass drift-invalidation *recoverable*. Today a mis-clicked "Start over" is unrecoverable. | Diff quality is entirely down to the structural differ. A lazy implementation produces "the document changed", which is worthless. |
| Unblocks B, C and F — every one of them needs "the previous state" to exist. | Storage grows per save (~100 kB per version at 42 entities). Needs a retention policy on day one, not later. |
| Fills the `audit_logs` table with its first real writer (`semantic.save`, `semantic.generate`, `semantic.restore`). | A `PUT` that changes nothing still writes a version unless the writer dedupes on document equality. |
| Answers *"who changed this metric and why"* — the exact §1.3 sentence — with a `note` field and an author FK. | The `SemanticLayerRow` docstring has to be rewritten, and its argument answered, not ignored (§5.2②). |

**Size: S.** One table + migration, a write hook in `SemanticService.save`, a
list/get/restore endpoint trio, one frontend tab, one logic test suite.

---

#### Option B — Draft → publish: editing stops reaching the model instantly

**What it is.** Split the document in two: `semantic_layers.document` becomes the
**published** one — the only one `NodeDeps.semantic` ever loads — and
`draft_document` is what the editor writes. The editor shows **"Unpublished
changes"** and a **Publish** button that validates, copies draft → published,
writes a version (Option A) and an audit row. Optionally: publishing requires the
connection owner, and — once §1.5 lands teams — a second approver.

*From: Wren's Deploy button and "Undeployed changes"; Fabric deployment
pipelines; Genie's DAB promotion.*

| Pros | Cons |
|---|---|
| Removes a live footgun: today an interrupted edit — a metric saved with its `WHERE` clause half-typed — is in the next answer on that connection. | Two states to hold correctly in a 2,700-line editor, including "what does drift mean when draft and published bind to different snapshots?". |
| Makes the eval A/B *meaningful*: run the suite against the draft, publish only if the score did not drop. This is the first mechanism that could make `semantic_layer_enabled` a user-facing quality gate rather than a developer switch. | **Approval is theatre in a single-player product.** §1.5 says one person owns a connection; a gate you approve yourself is a speed bump. Publish is useful now; *approve* is not, until sharing exists. |
| Matches what every competitor does, and it is the thing users coming from Power BI or Wren will look for. | Adds a step to a workflow whose payoff (better answers) is invisible at the moment of publishing, so it will feel like friction unless paired with D. |
| Gives "certification status" (§2) somewhere to live at document level, distinct from `provenance.reviewed` per entry. | Collides with `provenance.reviewed`, which already exists and means something adjacent (§8.3). |

**Size: M.** Column + migration, service split, pipeline read-path change (one
line, but a *load-bearing* one), editor state, publish dialog, and a test that a
draft never reaches a run.

---

#### Option C — A portable document: export, import, then (optionally) files

**What it is.** Two phases.

*C1 (S):* `GET /connections/{id}/semantic/export` → a `SemanticLayerDocument`
with a `kind`/`version` header and the document; `POST
/connections/{id}/semantic/import` → validate, bind to the current snapshot, flag
what does not resolve, save (and version it, per A). The dashboards
export/import pair is the template, including the decision to answer JSON rather
than a download because the SPA sends a bearer token.

*C2 (L):* a directory serialisation — `semantic/entities/<schema>.<table>.yml`,
`joins.yml`, `glossary.yml`, `time.yml` — plus a CLI that pulls and pushes
against a checkout. This is where "a metric definition is code" becomes literally
true, and where per-entity diffs come for free in whatever tool the customer
already reviews code in.

*From: Wren's project files; TMDL; `serialized_space`.*

| Pros | Cons |
|---|---|
| C1 is the cheapest possible answer to *"a metric definition is code"*: put the exported file in Git and you have Git. | **Export is a disclosure event.** `value_meanings` are customer data values; an export moves them past the trust boundary. Needs an explicit decision, an audit row, and probably a policy check (§8.5). |
| Enables promotion between DataMind instances (staging → production), which is the only way a customer can safely test a layer change against real questions. | **Import is a fifth way into stored SQL.** Metric expressions and filters are SQL; every one must go through `check_expression`, exactly as `dashboard_transfer` re-runs the guard per tile. The hostile corpus needs a new replay. |
| Makes cold start solvable: ship starter layers for common schemas (Shopify, Stripe, a warehouse's `dim_`/`fact_` conventions) as importable documents. | C1's single-file diff is unreadable at 42 entities — it is blob versioning in a text editor. The value only arrives at C2. |
| Lets a reviewer who has no DataMind login review the layer. | C2 needs a conflict story (someone edited in the UI while the file moved) and that is a genuinely hard product question — Fabric spends a whole feature on it. |

**Size: S (C1) then L (C2).**

---

### Group II — Make the definitions bind

#### Option D — Metric attribution: did the answer actually use the definition?

**What it is.** After `validate` and before `present`, parse the generated
statement with SQLGlot (already a dependency, already parsing this SQL) and
compare its aggregate expressions against the metrics defined on the entities
that were retrieved. Three verdicts per metric in scope:

- **used** — an aggregate whose function, column set and definitional filters
  match the metric after normalisation;
- **ignored** — an aggregate over a column a metric covers, computed differently
  (a `SUM(o_totalprice)` with no cancellation filter where the metric has one);
- **unknown** — anything the matcher is not sure about. **The default.**

Store it on `generated_queries` (`metric_use JSONB`) and show it: a **"used the
`revenue` definition"** chip on the answer, and — only where the verdict is
confidently `ignored` — a quiet note that the approved definition differs.

*From: Genie's Trusted badge and Power BI's Verified checkmark, adapted. Neither
vendor does attribution over free-form SQL; they badge the case where an approved
artifact was executed. This is the harder version of the same idea, and no
public implementation of it was found.*

| Pros | Cons |
|---|---|
| **It is the only option that makes §1.3's thesis measurable.** "The semantic layer is the answer to why DataMind is better than piping a schema into an LLM" becomes a number: metric-use rate per connection, per metric, over time. | **A wrong badge is a §0-class failure.** The plan's whole thesis is "plausible, confident, wrong". A false *used* is exactly that, wearing DataMind's own seal. The matcher must be conservative to the point of frequently answering `unknown`. |
| Changes nothing about what runs. Pure observation, fail-open preserved, no prompt change, `PROMPT_VERSION` does not move, all baselines stay comparable. | Expression matching is genuinely hard: `SUM(o_totalprice)` vs `SUM(orders.o_totalprice)` vs a filtered CTE vs a window function. Normalisation coverage will be partial for a long time. |
| Produces the **curation signal** nothing else produces: *"'revenue' was asked 41 times; the metric was used 6 times"* means the definition, its synonyms or its placement is wrong. That is a to-do list generated from evidence. | Only meaningful where metrics exist. On a connection with a generated layer and no hand-written metrics it reports almost nothing — so it pays back only after curation, which is the wrong way round for cold start. |
| Feeds §1.1 directly: an `ignored` verdict on a question later marked wrong is the highest-value row a review queue could hold. | Adds a parse pass per run. Cheap (the AST is already built once by the guard) but it is one more thing on the request path. |
| Prepares E: attribution is the read-only half of expansion, and building it first tells you whether expansion would even fire. | Three-state vocabulary must be fixed *before* the matcher is written, or it will drift into two states and start lying (§8.2). |

**Size: M.** Normalisation + matcher + tests over a corpus of real generated SQL,
a column, a chip, and a per-connection metric-use view.

---

#### Option E — Metric expansion: let the model *name* a metric and compile it

**What it is.** The generator is told it may write a metric reference —
`{{metric:revenue}}` or a `metric('revenue')` pseudo-function — instead of
re-deriving the expression. A pre-guard expansion pass replaces each reference
with the stored expression, its definitional filters and its `required_joins`,
and then **the expanded SQL goes through the guard exactly as today**. Behind a
per-connection switch, like `semantic_layer_enabled`, and off by default until an
eval arm says otherwise.

*From: Wren's engine expansion; UC metric views' `MEASURE()`.*

| Pros | Cons |
|---|---|
| The only option that makes a definition genuinely **unignorable** where it is used, rather than merely observable. | **It puts a text substitution in front of the AST guard**, and MVP1's entire security posture is "the guard decides what runs, and it fails closed". The order must be *expand → guard*, never *guard → expand*, and that has to be provable, not asserted. |
| Change the metric once, and every future answer changes with it — the payoff a semantic layer is supposed to have and currently does not. | **It is a prompt change**, and this repo has *measured* prompt additions lowering accuracy twice (36% → 26%; 0 wins / 4 losses). Teaching a new syntax competes for exactly that budget. |
| Pairs with D for free: an expanded reference is `used` by construction, so the badge stops depending on a fuzzy matcher for the cases that matter most. | Models will half-use the syntax — a reference inside a string literal, a reference where a column belongs, a reference plus a redundant hand-written filter that double-counts the exclusion. Every one needs a defined behaviour. |
| Makes `required_joins` do real work; today it is a hint inside a rendered sentence. | `PROMPT_VERSION` moves again, on top of a v7 → v8 move that has never been baselined (§1.3). Two unknowns in flight. |
| Small implementation surface — one expansion function, one call site. | Failure modes are silent unless expansion is logged per run, which means it needs D's storage anyway. |

**Size: L**, almost entirely in proving it is safe and measuring that it helps.

---

### Group III — Make it cheaper to own

#### Option F — Per-entity addressing and a curation queue

**What it is.** Three related pieces:

1. `PATCH /connections/{id}/semantic/entities/{table}` beside the existing
   wholesale `PUT` — keeping both, because the `PUT`'s stated reason (moving a
   metric between entities atomically) is still true — guarded by an
   `updated_at`/etag precondition so two editors cannot silently overwrite each
   other (Genie's `etag`, imported).
2. **Targeted regeneration in the UI**: pick tables, regenerate those. The API
   already takes `only_tables`; this is a picker and a call (§5.2①).
3. A **"Needs attention"** queue on the layer page, assembled from things the
   system already knows: invalid entries (`issue_count`), undescribed tables,
   entities whose snapshot shape changed since `schema_version`, and — once D
   ships — metrics that exist and are never used.

| Pros | Cons |
|---|---|
| Turns curation from a 42-table chore into a work queue, which is the difference between a feature that is used once and one that is maintained. | Two write paths to keep consistent forever. The precondition check is the entire safety of it and is easy to get subtly wrong. |
| Makes the audit row *addressable*: `resource_type='semantic_entity'` beats "someone saved the layer". | A queue without evidence behind it is a to-do list. Items 1–3 of the queue are cheap; the item that actually matters (never-used metrics) depends on D. |
| Fixes the real incremental gap — regenerating a table whose shape changed — which drift detection already surfaces and currently cannot act on. | Regenerating one described table needs a merge decision per field ("the model rewrote a description a human edited") that MERGE handles at entity granularity and would now need at field granularity. |
| Every piece is independently shippable. | Least *architecturally* interesting option here, and the easiest to defer forever. |

**Size: M** (S if only the targeted-regeneration picker is taken).

---

#### Option G — Glossary and synonyms into retrieval

**What it is.** Expand the question's match terms with entity synonyms, column
synonyms and glossary `maps_to` targets before the retrieval matcher runs, so
"turnover" finds the table whose metric is called revenue.

**This belongs to [retrieval-at-scale.md](retrieval-at-scale.md), not here.** It
is listed so the composition is visible and so it is not built twice. Its pro is
that it makes one curation act pay in two places; its con is that the sibling
research found the matcher already over-matches, and synonym expansion makes an
over-matching matcher worse before it makes it better.

**Size: S**, in someone else's file.

---

### 6.8 The options side by side

| | A · versions | B · draft/publish | C1 · export | C2 · files | D · attribution | E · expansion | F · per-entity |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Size | S | M | S | L | M | L | M |
| Touches the read path | **no** | yes *(one line)* | no | no | no | **yes** | no |
| `PROMPT_VERSION` moves | no | no | no | no | **no** | **yes** | no |
| Can lower accuracy | no | no | no | no | no | **yes** | no |
| Closes the **blob** gap | ●● | ●● | ● | ●●● | ○ | ○ | ●● |
| Closes the **advisory** gap | ○ | ○ | ○ | ○ | ●● | ●●● | ○ |
| Ships something users see | ◐ history tab | ● publish state | ◐ a button | ○ | ● **a badge** | ◐ | ● a queue |
| Produces a measurement | no | ◐ *(enables A/B)* | no | no | **yes** | ◐ | no |
| New disclosure surface | no | no | **yes** | **yes** | no | no | no |
| New guard entry point | no | no | **yes** *(import)* | **yes** | no | **yes** *(pre-guard)* | no |
| Blocked by §1.5 (teams) | no | ◐ *(approval half)* | no | no | no | no | no |
| Prerequisite for others | A→B,C,F | — | — | C1 | E | — | — |

Read the middle three rows together. **A, C1, D and F cannot make an answer
worse.** B changes one line on the read path. Only **E** can lower accuracy, and
this repo has measured prompt changes doing exactly that twice. That is the
sequencing argument in one table.

---

### 6.9 Recommendation

**Build A + D first, as one release. Then C1 and F. Then B. Hold E behind an
eval arm and a switch. Give G to §1.2. Treat C2 as a customer-driven upgrade, not
a roadmap item.**

**1. A, because it is small and everything else needs it.** A version chain is
days of work, touches nothing on the read path, makes `REPLACE` and drift
survivable, and gives the unused `audit_logs` table its first writer. It also
retires the single sentence in §1.3 that reads worst: *"no version history, no
diff, no who changed this metric and why."*

**2. D, because the plan's own thesis demands a measurement, and this is the only
one on offer at §1.3's altitude.** §1.3 argues the layer "is the answer to why
DataMind is better than piping a schema into an LLM" — and today there is no
evidence for or against that on any real question, only an eval baseline
(0.36) that predates the layer reaching the model at all. Metric-use rate is a
number a connection owner can watch move, produced without touching the prompt,
on the same day the layer's first honest A/B becomes possible.

**If only one of the two can be built, build D.** History makes the layer
respectable; attribution makes it *accountable*, and the plan is explicit that
what does not compound does not matter.

**3. C1 and F next, as the upkeep release.** Export unblocks promotion between
instances and starter templates; targeted regeneration closes the incremental gap
that drift detection already surfaces and cannot act on. Both are small, and
neither can regress an answer.

**4. B once there is a reason to publish.** Draft/publish is right, and it is
most valuable the day an eval A/B can gate it — "publishing this layer changed
the score from X to Y" is a far better dialog than "are you sure?". Ship *publish*
without *approve* until §1.5 gives approval someone to mean something to.

**5. E last, behind `semantic_expansion_enabled`, with an eval arm and a written
safety argument.** It is the most valuable idea in Group II and the only one that
can make things worse. The order that makes it safe is: D first (so you can see
whether the model is already ignoring definitions and how often), then expansion
(so you can measure whether it moved).

**One thing not to build.** Do not put the semantic layer in Git as the primary
store. Wren and Power BI can, because their users are analysts with a repo and a
CI pipeline. DataMind's user is the person who owns a connection, and the plan's
own §1.5 says they are working alone. A file tree is the right *export*; it is
the wrong *system of record* for this product today.

---

## 7. A sketch of the recommended path

### 7.1 A + the audit writer

```sql
CREATE TABLE semantic_layer_versions (
  id             uuid PRIMARY KEY,
  connection_id  uuid NOT NULL REFERENCES database_connections(id) ON DELETE CASCADE,
  version        integer NOT NULL,              -- monotonic per connection
  document       jsonb   NOT NULL,              -- the document as it was
  schema_version integer NOT NULL,              -- the snapshot it was bound to
  source         varchar(20) NOT NULL,          -- SAVE | GENERATE | IMPORT | RESTORE
  author_user_id uuid REFERENCES users(id),
  note           text NOT NULL DEFAULT '',
  entity_count   integer NOT NULL DEFAULT 0,
  metric_count   integer NOT NULL DEFAULT 0,
  issue_count    integer NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (connection_id, version)
);
```

`SemanticService.save` appends the *outgoing* document, skipping the write when
the document is byte-identical, and writes one `audit_logs` row
(`action='semantic.save'`, `resource_type='semantic_layer'`,
`resource_id=connection_id`, `detail={version, entities, metrics, issues}`).
Endpoints: `GET .../semantic/versions`, `GET .../semantic/versions/{n}`,
`POST .../semantic/versions/{n}/restore`.

`frontend/src/components/semantic-diff.ts` — DOM-free, in `npm test` beside
`semantic-drift.ts` — compares two `SemanticDocument`s and returns typed changes:
`entity_added | entity_removed | grain_changed | metric_added | metric_removed |
metric_expression_changed | metric_filters_changed | column_meaning_changed |
glossary_changed | time_changed | exclusions_changed`. **The typed change list is
the feature**; the rendering is a list of sentences over it.

### 7.2 D, where it plugs in

`inspect` is the wrong node (it reads results); the attribution runs right after
`validate`, where the AST already exists:

```
generate → validate → [attribute] → execute → inspect → present → chart
                          │
                          └─ compare aggregates in the validated AST against
                             metrics on entities in RetrievedContext.covered_tables
                             → {metric: used|ignored|unknown}
                             → generated_queries.metric_use
```

Matching rules, deliberately conservative, in this order:

1. Normalise both sides with SQLGlot: qualify columns against the snapshot,
   lower-case identifiers, sort predicate conjuncts.
2. **used** — same aggregate function, same normalised column set, and every one
   of the metric's definitional `filters` present as a conjunct in an enclosing
   `WHERE`/`HAVING`/`QUALIFY` (or the CTE that feeds it).
3. **ignored** — same aggregate function over a column named in a metric's
   expression, with at least one definitional filter *absent*. This is the only
   verdict that accuses the answer of anything, so it needs the narrowest
   definition and the most tests.
4. **unknown** — everything else, including anything involving window functions,
   `DISTINCT` mismatches, or a metric whose entity was retrieved but whose
   columns do not appear.

UI: a small chip on the answer where at least one metric is `used`
("`revenue` definition applied"), hoverable to the expression. Nothing at all on
`unknown`. On `ignored`, a line in the existing SQL panel rather than a warning
banner — the first version of this must be able to be wrong without costing the
user trust.

Per-connection view: `metric`, `asked` (questions whose retrieval covered its
entity), `used`, `ignored` — sorted by the gap. That table is the curation queue
F wants and the review-queue input §1.1 wants.

### 7.3 Order of work

| # | Item | Size | Gate to the next |
|---|---|:--:|---|
| 1 | A — versions, diff, restore, first audit writer | S | History tab renders a real diff on the `sales` fixture |
| 2 | D — attribution + `metric_use` + chip | M | Metric-use rate reported for both demo connections |
| 3 | **The layer's first honest A/B** — eval with `semantic_layer_enabled` on vs off, at `PROMPT_VERSION` v8 | S | A number for §1.3 that has never existed |
| 4 | C1 — export/import, guard replay, disclosure decision | S | Hostile corpus passes on the import path |
| 5 | F — targeted regeneration + needs-attention queue | M | A described table can be regenerated alone |
| 6 | B — draft/publish, publish gated on an eval arm | M | No draft byte ever reaches a run (test) |
| 7 | E — expansion, switch off by default, eval arm | L | Only if 2 shows definitions are being ignored *and* 6 can gate it |

Step 3 is not optional and is not decoration. §1.3 says it plainly: the layer's
A/B *"has never yet been run against a prompt that actually contained the
layer"*. Every claim this document makes about how much any of this is worth
rests on a number nobody has taken.

---

## 8. Decisions to make before building

### 8.1 ⚠️ Does the layer stay advisory? — the load-bearing one

CLAUDE.md lists the semantic layer under **fail open**: *"the feature is dropped,
the work continues."* Options A, C, D and F preserve that exactly. **E breaks
it** — an expanded metric reference is a hard dependency of the statement, and a
metric that fails to expand must either fail the run or fall back to a
re-derivation the model did not write.

Decide deliberately, and write the decision into CLAUDE.md's fail-open table
before writing the code. The failure mode to avoid is drifting from D into E by
accident: a badge that starts warning, then blocking, then rewriting, with nobody
having decided that the layer became authoritative.

### 8.2 ⚠️ A wrong badge is worse than no badge

Fix the vocabulary before the matcher exists: **used / ignored / unknown**, with
`unknown` as the default and `ignored` requiring positive evidence (a
definitional filter demonstrably absent). Two-state thinking — "used or not
used" — will produce confident false accusations against correct SQL, and the
first one a user notices costs more trust than the feature earns in a month.

Corollary: the `ignored` verdict should ship *muted* (visible in the SQL panel,
absent from the answer) until its precision has been measured on real runs.

### 8.3 "Reviewed" and "published" are two different words

`Provenance.reviewed` already exists, per entry, documented as *"the only honest
basis for telling the model 'this definition is authoritative'"*. Option B adds a
document-level published state. If both ship with UI, the layer page will show a
reviewed metric inside an unpublished draft and users will reasonably ask which
one is live.

Recommendation: keep `reviewed` as *a human stands behind this entry* and name
the document state **published / unpublished changes** (Wren's words), never
"approved". Do not overload either.

### 8.4 A version is a snapshot, not a delta

Snapshot: ~100 kB per version, trivially correct, restore is a copy. Delta: small,
and it requires a merge implementation the restore path would then depend on for
correctness. Take snapshots, dedupe identical documents, cap retention, and make
the cap a setting rather than a constant nobody can find.

The open sub-question is what a `GENERATE` job writes: one version for the job,
or one per table it describes. One per job — the job is the user's action, and
`semantic_jobs.stats` already records what it touched.

### 8.5 ⚠️ Export is a disclosure decision, not a convenience

`SemanticColumn.value_meanings` maps real values from the customer's data to
meanings, and the generator is careful about it — values are filtered to those
already in the snapshot so *"the model cannot invent a key to leak"*. An export
file has no such boundary: it leaves the system, gets committed, gets emailed.

Microsoft's warning is the precedent — verified answers may expose RLS-protected
data *"through the file format in Git"*. Before C1 ships, decide: does an export
include `value_meanings` unconditionally, never, or subject to the connection's
`disclosure_policy`? Whichever it is, the export writes an `audit_logs` row, and
the UI says what is in the file before it is downloaded — the dashboards import
dialog already sets that precedent from the other direction.

### 8.6 Import is a fifth way into stored SQL

The four guard entry points each replay the hostile corpus
(`test_sqlguard_hostile.py`, `test_query_service.py`, `test_report_guard.py`,
`test_dashboard_transfer.py`). A metric `expression`, a metric `filter` and a
join `on` clause are all SQL, and an imported document supplies all three from
outside. `check_expression` must run on every one at import, and the import path
needs its own replay before it ships. This is the same lesson `dashboard_transfer`
already learned — *"a document's SQL is hostile input like any other."*

### 8.7 Teams: half of B is blocked and should be said so

§1.5 says the product is single-player by construction: one owner per connection.
*Publish* is useful immediately (it separates editing from taking effect).
*Approve* is not — a gate you clear yourself measures nothing. Ship the first,
design the second, and do not let the second's absence delay the first.

### 8.8 Restoring a version that no longer validates

Schemas move. A version restored six months later will contain entries that no
longer bind. The house rule already answers this and the restore path should
follow it exactly: **flag, keep, do not render.** A restore that silently dropped
the invalid half would hide the drift, which the codebase has already decided is
the worse failure. Restore then shows the same red rows the editor already knows
how to explain — including the whole-layer re-key sentence.

---

## 9. Open questions this research could not close

1. **Does Genie have any in-product history for a space's knowledge?** The API
   reference exposes only an `etag`. Absence in documentation is not proof of
   absence in product.
2. **Is Git support for Power BI verified answers planned?** The docs state it is
   unsupported and give no timeline. If it lands, the "badge and history are
   separate features" observation in §0 weakens.
3. **Which Wren project layout is current** — `instructions.md` + `queries.yml`,
   or `knowledge/rules/` + `knowledge/sql/`? Both appear in current material
   (§1.2).
4. **Does anyone attribute a free-SQL answer back to an approved metric?** No
   published implementation was found. Genie and Power BI badge *execution of an
   approved artifact*, which is a different and easier problem. If Option D is
   novel, it is novel in a field where four well-resourced teams chose the easier
   problem instead — that is worth a moment's thought before committing to M.
5. **How much accuracy does a semantic layer buy?** No vendor publishes a
   controlled number. Databricks publishes benchmark *machinery*, not deltas
   attributable to the layer. DataMind can produce this number for itself on the
   `sales` fixture with the switch it already has — and until step 3 of §7.3 runs,
   nobody in this market has a defensible figure, including DataMind.
6. **What does metric-use rate look like on a real connection?** The whole case
   for D assumes generated SQL sometimes ignores available definitions. That is
   near-certain — nothing enforces it — but the *rate* is unknown, and if it
   turned out to be 5%, D's priority drops sharply.

---

## 10. Sources

**Power BI / Fabric**
- [Prepare your data for AI — Verified answers](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-verified-answers) — trigger phrases, verified checkmark, limits (250/model, 15 prompts, 500 chars, 10 filter permutations), *"Git integration isn't supported"*, RLS/OLS caveat
- [Use Copilot with semantic models](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-semantic-models)
- [Copilot for Power BI overview](https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-introduction)
- [TMDL in Power BI Desktop developer mode](https://powerbi.microsoft.com/ru-ru/blog/tmdl-in-power-bi-desktop-developer-mode-preview/)
- [Why Power BI developers should care about TMDL](https://endjin.com/blog/why-power-bi-developers-should-care-about-the-tabular-model-definition-language-tmdl) — one file per table, diff/merge behaviour
- [Tabular Editor and Fabric Git integration](https://tabulareditor.com/blog/tabular-editor-and-fabric-git-integration)
- [How to write good AI instructions for a semantic model](https://tabulareditor.com/blog/how-to-write-good-ai-instructions-for-a-semantic-model) — 10,000 chars, `/Copilot` markdown file *(secondary source)*
- [Git integration in Microsoft Fabric](https://powerbiconsulting.com/blog/fabric-git-integration) · [Deployment pipelines and CI/CD in 2026](https://powerbiconsulting.com/blog/power-bi-deployment-pipelines-ci-cd-2026) *(secondary)*

**Wren AI**
- [What is Modeling Definition Language (MDL)?](https://docs.getwren.ai/oss/concepts/what_is_mdl) — YAML source → `target/mdl.json`, *"easy to inspect, commit, review, fork, and deploy"*
- [Quick start: Wren CLI with jaffle_shop](https://docs.getwren.ai/oss/get_started/quickstart) — project layout, `context validate` / `context build`, `memory index`
- [Architecture](https://docs.getwren.ai/oss/reference/architecture) — project files vs derived memory, profiles held outside the project
- [How we design our semantic engine for LLMs](https://www.getwren.ai/post/how-we-design-our-semantic-engine-for-llms-the-backbone-of-the-semantic-layer-for-llm-architecture) — SQL rewriting against MDL
- [Powering semantic SQL for AI agents with Apache DataFusion](https://www.getwren.ai/post/powering-semantic-sql-for-ai-agents-with-apache-datafusion)
- [Why the semantic layer is essential for reliable text-to-SQL](https://www.getwren.ai/post/why-the-semantic-layer-is-essential-for-reliable-text-to-sql-and-how-wren-ai-brings-it-to-life)
- [Modeling — Model](https://docs.getwren.ai/oss/engine/guide/modeling/model) — the Deploy button and "Undeployed changes"
- [WrenAI (GitHub)](https://github.com/Canner/WrenAI)

**Databricks**
- [Unity Catalog metric views](https://docs.databricks.com/aws/en/uc-semantics/metric-views/) · [Model metric views](https://docs.databricks.com/aws/en/uc-semantics/metric-views/basic-modeling) — YAML shape, `MEASURE()`, the un-validated `at_most_one_match`
- [Genie Space API reference](https://docs.databricks.com/api/genie/v1/space) — `include_serialized_space`, what `serialized_space` contains, `etag`
- [Tune Genie quality](https://docs.databricks.com/aws/en/genie/tune-quality) · [Trusted assets](https://learn.microsoft.com/da-dk/azure/databricks/genie/trusted-assets)
- [Semantic layer architecture: components, design patterns and AI integration](https://www.databricks.com/blog/semantic-layer-architecture-components-design-patterns-and-ai-integration) — *"Semantics as code — CI/CD, Git-versioned, dev → staging → prod"*
- [Redefining semantics for the future of BI and AI](https://www.databricks.com/blog/redefining-semantics-data-layer-future-bi-and-ai)
- [Genie in a bundle: deploying Genie spaces with DABs](https://www.advancinganalytics.co.uk/blog/genie-in-a-bundle) · [databricks/cli #4191](https://github.com/databricks/cli/pull/4191) *(secondary — DAB support)*
- [Metric views vs. knowledge store (element61)](https://www.element61.be/en/resource/metric-views-vs-knowledge-store-foundation-reliable-conversational-bi-databricks-genie) *(secondary)*

**Data Formulator**
- [Data Formulator (GitHub)](https://github.com/microsoft/data-formulator) · [Foundry Labs](https://labs.ai.azure.com/innovations/data-formulator/) — connectors and "data memory"

**Semantics-as-code lineage**
- [What is a semantic layer? (Cube)](https://cube.dev/articles/what-is-a-semantic-layer)
- [LookML vs dbt Semantic Layer vs a compiled semantic layer](https://colrows.com/blogs/lookml-vs-dbt-semantic-layer/) — LookML as the first "semantics as code"
- [Best semantic layer tools in 2026](https://getbruin.com/blog/semantic-layer-tools/)

**In-repo**
- `backend/app/semantic/{models,validate,render,generator,prompts}.py` ·
  `backend/app/infra/db/models.py` (`SemanticLayerRow:178`, `AuditLog:939`) ·
  `backend/app/infra/db/migrations/versions/0003_semantic_layer.py` ·
  `backend/app/api/v1/semantic.py` · `backend/app/api/v1/dashboards.py:176` ·
  `backend/app/pipeline/state.py:234` ·
  `frontend/src/components/semantic.tsx` · `semantic-drift.ts` ·
  [CLAUDE.md § The semantic layer](../../CLAUDE.md) ·
  [mvp2-plan.md §1.3](../mvp2-plan.md#13-the-semantic-layer-is-a-blob-not-a-model)
