# The data surface — competitor research and options for DataMind

> **Subject:** [mvp2-plan.md §1.7](../mvp2-plan.md#17-the-data-surface-is-narrow-in-both-directions) —
> *"The data surface is narrow, in both directions"*, rated **High** (#7 of ten).
> It is the plan's Theme E ([E1–E5](../mvp2-plan.md#theme-e--reach)).
> **Scope:** how Microsoft Data Formulator, Wren AI, Databricks AI/BI Genie and
> Power BI / Fabric Copilot get data **in** (files, warehouses, federation,
> re-sync) and get answers **out** (export, API, MCP, embed), what is verifiable
> from public documentation, and what DataMind should build.
> **Desk research date:** 2026-08-31, against `main`. Every competitor claim is
> sourced; where the only source is a vendor blog or a third party rather than
> product documentation, this document says so.
> **Status:** research and options. Not a decision. §6 is an argument, §8 lists
> the decisions that have to be made before any of it is built.
> **Siblings:** [learning-loop.md](learning-loop.md) (§1.1) ·
> [retrieval-at-scale.md](retrieval-at-scale.md) (§1.2) ·
> [semantic-layer-as-a-model.md](semantic-layer-as-a-model.md) (§1.3). The
> overlaps with §1.2 are load-bearing here and are marked.

---

## 0. The finding, in one page

**"Narrow in both directions" is three separate defects, and they have wildly
different prices.** §1.7 argues them as one paragraph each, which hides the fact
that one of them is nearly free, one is a week per engine, and one is blocked on
a primitive the product does not have.

```
   defect ①  NO FILE PATH            defect ②  NO WAREHOUSES        defect ③  NOTHING GETS OUT
   every connection needs a          four operational engines.      no CSV, no Excel, no API
   host, a port and a read-only      the 2026 analytics estate      credential, no MCP. the
   account. "drag a spreadsheet      lives in Snowflake,            rows are already stored
   in" is unavailable, and it        BigQuery, Databricks.          and already served over
   is the cheapest funnel there is.                                 HTTP — nothing formats them.

   priced at: a new storage          priced at: ~1 connector        priced at: ① a formatter (S)
   decision + an untrusted-input     per engine, PLUS a             ② an API credential that does
   class the threat model does       containment story per          not exist (M, and it is
   not have yet                      engine, PLUS §1.2              really a §1.5 problem)
```

**The single most important observation in this research:** on the way *out*,
DataMind is much closer than §1.7 says. Every chat result is already persisted
whole — `run_service.py:421` writes a `TABLE` artifact whose `spec.rows` is the
full result set — and `GET /api/v1/artifacts/{id}`
([`conversations.py:470`](../../backend/app/api/v1/conversations.py#L470))
already serves those rows, paginated, to the owning user. **CSV export is a
content-type, not a feature.** What is genuinely missing on the way out is not
data access; it is a *credential a non-browser client can hold*
([`deps.py:50`](../../backend/app/api/deps.py#L50) accepts a bearer access token
and nothing else). That single fact re-orders Theme E: E3 is hours, and E2 —
the plan's "highest leverage per line of code" — is gated on work the plan files
under §1.5.

Four things worth knowing before reading further, because each changes an
option:

1. **Every competitor that ships file upload backs it with the same engine.**
   Data Formulator runs a per-session **DuckDB** with Parquet persistence; Wren
   AI's file path *is* a DuckDB connection (`read_csv` / `read_parquet` /
   `read_json`). Two independent teams, same answer. That is a strong prior for
   Option **F2** and an argument against inventing anything.
2. **The incumbent is retiring the naïve version of the feature DataMind wants
   to add.** Power BI's legacy local-Excel import "stop[s] refreshing after July
   31, 2026" and stops loading a month later, because *"when you upload a local
   file, Power BI adds a copy of the file to the workspace"* — a copy with no
   refresh path. Shipping a one-shot CSV import means shipping the thing
   Microsoft is switching off. §3 lesson 2.
3. **Genie's file upload is the governance model to copy, and it is stricter
   than anything in DataMind today.** An uploaded file lands in *"a user- and
   agent-specific Unity Catalog managed volume"* that *"is not listable and does
   not appear in the schema browser"*, and *"Only the user who uploaded the file
   can access it."* DataMind's connections are owner-scoped already, so this is
   reachable — but it is a decision, not a default (§8.1).
4. **Three of §1.7's claims do not survive contact with the code**, one of them
   materially. §4.8 has all three; the material one is the export finding two
   paragraphs above.

And one thing that is *worse* than §1.7 says: a warehouse connector is not just
a connector. Invariant #2 ("containment underneath correctness") is written in
terms of `READ ONLY` transactions and row caps. Snowflake and BigQuery have
neither in that form, and they add a containment axis DataMind has never had —
**money**. A query capped at 1,000 rows can still scan 40 TB. §8.4.

---

## 1. The four, one by one

Each section is in two halves, because the products are asymmetric: the ones
with the widest intake have the most locked-down egress, and vice versa.

### 1.1 Microsoft Data Formulator — files first, and a local engine underneath

The only one of the four whose *first-run* experience is a file, and the one
whose architecture DataMind could most nearly copy.

**Getting data in.** The README's list is the widest of the four at the small
end: *"CSV, TSV, Excel, JSON, screenshots, or text"*. A screenshot is not a
joke — the model reads the table out of the image and materialises it. Beyond
files, 0.7 shipped *"Persistent connectors for Superset, Kusto, Cosmos DB,
MySQL, PostgreSQL, MSSQL, BigQuery, S3, Azure Blob — with SSO, search, and smart
filters"*, and 0.8b1 added ClickHouse.

**The engine underneath is the transferable part.** Since v0.2 DF *"integrates
DuckDB as the backend local database to support data exploration with large
datasets (million rows)"*, and each user session gets its own workspace
directory and DuckDB instance, with Parquet for persistence.¹ So a CSV, an
Excel sheet, a screenshot and a Postgres table all become the same thing — a
table in a local DuckDB — and every downstream feature sees one uniform surface.
**This is exactly the shape of Option F2 below.**

**Loading is an agent step, not a form.** The loaders *"discover sources,
clarify your request, propose a loading plan, and let you review the data before
adding it to the workspace."* Note the review gate: the human approves the
loading plan before the data lands. DataMind has the same instinct in the report
outline gate.

**A plugin framework, deliberately.** *"New plugin framework makes adding more a
drop-in folder."* DF and DataMind arrived at the same conclusion from opposite
directions: DataMind's `DatabaseConnector` Protocol
([`ports/database.py`](../../backend/app/domain/ports/database.py)) is the same
bet, and it is already better specified.

**Getting data out — the weak half.** 0.7 documents *"Build and export reports
as image or PDF to tell the story"*. **A CSV/Excel export of a derived table is
not documented**, and no public API or MCP server is either. ⚠️ [mvp2-plan.md
§2.6](../mvp2-plan.md#26-the-matrix) scores Data Formulator `●` on "Result
export (CSV/Excel)"; this research could not verify that from the repository or
the release notes and would score it `◐`. §9.1 records it as unverified.

> ¹ The per-session DuckDB + Parquet detail is from DeepWiki's generated
> architecture summary, not Microsoft documentation. Treated as secondary; the
> DuckDB-as-backend claim itself is in the v0.2 release notes.

### 1.2 Wren AI — the widest intake, and the closest competitor

Same architecture, same philosophy as DataMind, one decision made very
differently: **Wren pushed dialect handling down into an engine so that adding a
source is configuration; DataMind pushed it into a hand-written connector so
that adding a source is code.**

**Getting data in.** The Cloud "Connect Data Sources" page names 17 across four
families:

| Family | Sources named |
|---|---|
| **Files** | CSV Upload |
| **Cloud warehouses** | BigQuery, Snowflake, Databricks, Amazon Redshift |
| **Databases** | PostgreSQL, managed PostgreSQL (Supabase, Neon), MySQL / MariaDB, SQL Server, Oracle, ClickHouse |
| **Query engines & lakes** | Trino & Starburst, Amazon Athena, Spark, Amazon EMR (Spark) |

The README's headline is *"20+ data sources"*. **All four of DataMind's engines
are in the "Databases" row** — i.e. Wren's entire advantage on intake is the
other three rows.

**The file path is a DuckDB path.** Cloud takes a direct CSV upload with one
documented limit — *"CSV file size must be less than 100MB"* — and creates data
models you review before modelling. OSS routes files through DuckDB explicitly:
*"CSV Files, JSON Files, Parquet Files"*, read from *"either a local path or a
cloud storage URL (such as AWS S3)"*, with the literal SQL in the docs —
`CREATE TABLE new_tbl AS SELECT * FROM read_csv('input.csv', header = true);`.

**Why adding a source is cheap for them.** `wren-core` is a Rust semantic engine
on **Apache DataFusion**; `ibis-server` uses **Ibis** as the unified data-source
API and **SQLGlot** for dialect translation.² A new source is largely an Ibis
backend plus MDL plumbing. DataMind uses SQLGlot too — but only in the guard,
for parsing and rendering, not for execution. That is the whole difference in
cost per engine, and it is a real architectural fork (§5.7, Option **E2**).

**The limit nobody advertises:** a Wren project is scoped to a single data
source, and cross-source joins are said to require a Trino integration.³ So
**Wren has not solved cross-connection questions either** — it has moved the
problem to a federation engine you must run yourself. §1.7's third bullet
("revenue from Postgres against the campaign list in this spreadsheet") is
unsolved in the market at this tier.

**Getting data out — the strongest of the four for a self-hosted product.**
Results and generated SQL export to CSV; charts export as SVG or PNG and pin to
a dashboard without re-querying.⁴ And the distribution surface is the real
lesson: a single organisation-level MCP endpoint at
`https://cloud.getwren.ai/api/mcp`, where *"Auth is handled via OAuth — no API
key needed"*, exposing eight tools:

| Tool | Purpose (docs' own words) |
|---|---|
| `list_projects` | "Projects you can query" |
| `get_project_metadata` | "Table schema for a project" |
| `ask` | "End-to-end natural-language Q&A" |
| `generate_sql` | "Question → SQL" |
| `run_sql` | "Execute SQL" |
| `generate_chart` | "Vega chart spec" |
| `generate_summary` | "Summarize results" |
| `respond_clarification` | "Answer a follow-up question from Wren AI" |

Read that list next to DataMind's pipeline nodes. `generate_sql` is `generate`,
`run_sql` is `execute_saved_sql`, `generate_chart` is `plan_chart` +
`compose_chart` (and Wren emits Vega, as DataMind does), `generate_summary` is
`present`, `respond_clarification` is the `clarify` node. **DataMind already has
seven of the eight as service functions**; what it lacks is the transport and
the credential. That is the single strongest piece of evidence for E2 in this
document — and also the strongest warning, because the eighth thing Wren has and
DataMind does not is OAuth.

> ² From a Wren engineering post and third-party architecture write-ups, not
> reference documentation. Secondary.
> ³ Third-party comparison page. **Secondary and uncorroborated** — treat the
> Trino detail as unverified; the "one source per project" shape is consistent
> with the docs' project model.
> ⁴ Vendor marketing pages, not reference documentation. Secondary.

### 1.3 Databricks AI/BI Genie — the narrowest intake, governed hardest

Genie is the inverse of Wren: it reads **one** thing — Unity Catalog — and gets
its breadth by making other systems *look* like Unity Catalog.

**Getting data in, part one: federation, not connectors.** Lakehouse Federation
mirrors an external database as a **foreign catalog** in Unity Catalog, covering
MySQL, PostgreSQL, Redshift, Snowflake, SQL Server, Synapse, BigQuery and other
Databricks workspaces; queries are pushed down over JDBC. Genie then points at
UC tables and never learns that some of them are somewhere else. **One intake
surface, N sources, governance written once.** This is the cleanest design in
the research and it is the argument for Option **E2**.

**Getting data in, part two: file upload, and it is the model to copy.** You can
upload CSV, Excel and PDF into a Genie Agent and query them alongside UC tables.
The limits are worth having on hand as sizing guidance:

| | Limit |
|---|---|
| Files per conversation | "up to 25 files per conversation, regardless of file type" |
| CSV / Excel size | "Each file must be smaller than 200 MB" |
| CSV / Excel width | "Each file must contain fewer than 100 columns" |
| PDF (beta) | "smaller than 20 MB", "no more than 20 pages", "limited to 15,000 characters" |

And the governance, which matters more than the limits:

- Files land in *"a user- and agent-specific Unity Catalog managed volume"*, and
  *"The volume is not listable and does not appear in the schema browser."*
- *"Only the user who uploaded the file can access it."* The uploader is granted
  `USE CATALOG, USE SCHEMA, READ VOLUME, and WRITE VOLUME` on their own files.
- Retention is by lifecycle, not by clock: files *"remain available until"* the
  user removes them, the conversation is deleted, or the agent is deleted.
- Modal split: *"CSV and Excel uploads are not available in Agent mode"*; *"PDF
  uploads require Agent mode"*; PDFs are UI-only — *"API-based file uploads are
  not supported."*

**Getting data out.** A CSV download button on any response — *"You can download
up to approximately 1GB of data"* — plus PNG for a visualisation, copy-to-
clipboard, and *"Download PDF"* for an Agent-mode report *"including findings,
visualizations, and citations"*. One number to steal: *"Query results persist
for seven days."* Genie does **not** keep result sets forever; it keeps the SQL
and offers a re-run. (§8.6.)

**Distribution.** The Conversation API for embedding, plus **managed MCP**:
`https://<workspace-hostname>/api/2.0/mcp/genie` for workspace-wide "Genie One",
and `.../api/2.0/mcp/genie/{genie_space_id}` scoped to one agent, both on the
`genie` OAuth scope, with the pitch that *"Unity Catalog enforces permissions,
so agents and users access only the tools and data you grant them."*

**The lesson in one line:** Databricks did not put an authorisation model behind
its MCP server; it put its MCP server behind an authorisation model it already
had. DataMind does not have one yet (§8.5).

> Genie Ontology, announced at DAIS 2026 as a learned context layer with an
> "ontorank" authority score, is adjacent to this document and belongs to
> [semantic-layer-as-a-model.md](semantic-layer-as-a-model.md). Sources for it
> are secondary (Atlan, Dawiso, datapao) and it is not relied on here.

### 1.4 Power BI and Fabric — the widest surface, and the honest warning label

The incumbent's intake is not comparable and is not the interesting part. The
interesting part is that **Power BI has already made every mistake available in
this problem space and documented the cleanup.**

**Getting data in.** 150+ Power Query connectors via Dataflow Gen2; OneLake
shortcuts to ADLS Gen2, S3 or another Lakehouse; Import, DirectQuery, Live
Connection, Direct Lake and composite models. Files: workspace → *New item* →
*Semantic model* → *CSV*, from OneDrive/SharePoint or *Upload file* from the
local machine.

**⚠️ And the warning label, which is the reason this section exists.** The
distinction Microsoft draws is exactly the one DataMind must draw:

> When you upload Excel files from OneDrive or SharePoint, Power BI creates a
> **connection** to the file. When you upload a **local** file, Power BI adds a
> **copy** of the file to the workspace.

A copy has no refresh path — and so: *"Semantic models created using the legacy
Excel import experience in the Power BI service stop refreshing after July 31,
2026, and stop loading after August 31, 2026"*, and *"Power BI no longer
supports uploading local Excel workbooks to workspaces and configuring refresh
for them."* **The incumbent is deprecating the exact feature §1.7 asks DataMind
to add**, because the one-shot copy became a support liability. §3 lesson 2 and
§8.6 are about not repeating this.

**Getting data out — by far the most thought-through, and the source of the
numbers.** Export from a visual offers *Summarized data*, *Underlying data* and
*Data with current layout*, with these documented caps:

| Format | Cap |
|---|---|
| `.csv` | "up to 30,000 rows max" |
| `.xlsx` | "up to 150,000 rows max" |
| `.xlsx` with live connections | "up to 500,000 rows max" |
| Matrix, *Data with current layout* | 150,000 **data intersections** (cells), not rows |
| DirectQuery | "16-MB uncompressed data", so possibly far fewer rows |

**Export is governed, in five separate ways**, and this is the part DataMind
should read closely:

1. **Sensitivity labels** ride the file out: *"If the sensitivity label has
   protection settings, Power BI applies these protection settings when
   exporting report data to Excel, PowerPoint, or PDF files."*
2. **A designer-level switch** with three settings: summarized only, summarized
   + underlying, or none.
3. **A tenant-level override**: *"If the Power BI admin portal settings conflict
   with the report settings for export data, the admin settings override."*
4. **RLS applies**: *"If RLS is applied, you can only export data you're
   authorized to see."* And underlying data needs build permission.
5. **Monitoring**, via Defender for Cloud Apps, *"to configure a policy that
   prevents users from downloading sensitive data from Power BI to unmanaged
   devices."*

**And one implementation detail worth stealing verbatim.** Power BI defends
against CSV injection on the way out:

> When you're exporting to *.csv*, certain characters are escaped with a leading
> **'** to prevent script execution when opened in Excel. This condition happens
> when: the column is defined as type "text" in the data model, **and** the
> first character of the text is one of the following: **=, @, +, -**

DataMind's threat model treats database content as untrusted
([security.md §2.4](../security.md)). A CSV writer that does not do this hands
that untrusted content to Excel as a formula. §8.3.

**The programmatic path.** `exportToFile` renders a report to PDF/PPTX/PNG
asynchronously; *"The URL is available for 24 hours"*; 500 concurrent requests
per capacity; on Premium Per User it is *"just one request in a five-minute
window"* and elsewhere documented as not supported for PPU at all. Plus
*Analyze in Excel* (a live OLE DB connection producing a real PivotTable, not a
flat file), paginated reports for uncapped formatted output, the XMLA endpoint,
and embedded analytics as a first-class product.

---

## 2. The comparison, at §1.7 resolution

`●` present · `◐` partial · `○` absent · `n/a` not applicable

### 2.1 Getting data in

| | DataMind | Data Formulator | Wren AI | Genie | Power BI |
|---|:--:|:--:|:--:|:--:|:--:|
| Operational databases (PG/MySQL/MSSQL/Oracle) | ● 4 | ● 3 (no Oracle) | ● 5+ | ◐ via federation | ● |
| Cloud warehouses (Snowflake/BigQuery/Redshift/Databricks) | ○ | ◐ BigQuery, Databricks | ● all four | ● native + federation | ● |
| Query engines / lakes (Trino, Athena, Spark) | ○ | ○ | ● | ◐ | ● |
| Object storage (S3, Azure Blob, OneLake) | ○ | ● | ◐ via DuckDB | ● volumes | ● shortcuts |
| **CSV / Excel upload** | ○ | ● | ● | ● | ● |
| Other file formats (JSON, Parquet, TSV) | ○ | ● | ● | ○ | ● |
| Unstructured (PDF, screenshot) | ○ | ● screenshot | ○ | ● PDF (beta) | ○ |
| Cross-source join in one question | ○ | ● (all in one DuckDB) | ○ (Trino only³) | ● (foreign catalogs) | ● (composite models) |
| Scheduled / incremental re-sync | ○ | ◐ auto-refresh | ◐ | ● (catalog is live) | ● |
| Local query engine for loaded data | ○ | ● DuckDB | ● DuckDB / DataFusion | n/a | ● VertiPaq |

### 2.2 Getting data out

| | DataMind | Data Formulator | Wren AI | Genie | Power BI |
|---|:--:|:--:|:--:|:--:|:--:|
| Result → CSV | ○ | ◐ *unverified* | ● | ● ~1 GB | ● 30k rows |
| Result → Excel | ○ | ○ | ○ | ○ | ● 150k / 500k rows |
| Chart → PNG / SVG | ○ | ● | ● | ● PNG | ● |
| Document → PDF | ◐ browser print | ● | ○ | ● Agent-mode report | ● + paginated |
| Copy result to clipboard | ○ | ◐ | ◐ | ● | ● |
| Public REST API for an answer | ◐ *exists, no credential* | ○ | ● | ● Conversation API | ● |
| MCP server | ○ | ○ | ● 8 tools, OAuth | ● managed, OAuth | ◐ |
| Embed in another app | ○ | ○ | ● (paid) | ● | ● first-class |
| Export governed by policy/labels | n/a | ○ | ◐ (paid RLS) | ◐ (UC) | ● five ways |
| Export written to an audit log | ○ *(table exists, unused)* | ○ | ◐ (paid) | ● | ● |

### 2.3 The one row that matters

| | DataMind | DF | Wren | Genie | Power BI |
|---|:--:|:--:|:--:|:--:|:--:|
| **Static AST validation of model-written SQL, fail-closed** | ● | n/a | ◐ dry-plan | ◐ | n/a |
| **Read-only proven at the engine, per connection** | ● | ○ | ◐ | ◐ | n/a |
| **Explicit per-connection disclosure policy to the LLM** | ● unique | ○ | ○ | ○ | ○ |

Widening the data surface is the one theme in MVP2 that **puts these three at
risk**, because all three are stated in terms of a connection to a server with a
role on it. A file has no role. A warehouse has no `READ ONLY` transaction. An
MCP client is not a signed-in human. Every option in §5 is scored on whether it
preserves this row, and the ones that do not are marked.

---

## 3. Six lessons

**1. Everyone who ships file upload runs an embedded analytical engine, and it
is DuckDB.** Data Formulator (per-session DuckDB + Parquet) and Wren AI
(`read_csv`/`read_parquet`/`read_json`) reached the same answer independently.
Nobody wrote a CSV type-inferrer. Nobody stored the rows in their application
database and queried them with the ORM. **If DataMind builds file upload, the
build-vs-adopt question has a strong published prior** — and DuckDB is a
first-class SQLGlot dialect, so the guard follows for free.

**2. A copy of a file is a dead end, and the incumbent proved it.** Power BI is
switching off local-Excel import in 2026 because the copy could not refresh. The
design instruction is: **an uploaded file must have a name, a version, and a
re-upload path from the first commit**, or it becomes a stale answer nobody can
account for. This is the single most actionable lesson in the document, and it
costs almost nothing to obey on day one and a migration to retrofit.

**3. An uploaded file is a per-user object, not a shared data source.** Genie's
volume *"is not listable and does not appear in the schema browser"* and *"Only
the user who uploaded the file can access it."* Sharing is a later, explicit
act. DataMind's connections are already owner-scoped
([`models.py:106`](../../backend/app/infra/db/models.py#L106),
`uq_conn_owner_name`), so the default is right — but it will be tempting to make
uploads "just another connection" and inherit the sharing story that §1.5 says
does not exist yet. That temptation is correct here, and only here.

**4. Breadth comes from an engine or from a governance layer — not from
connectors.** Wren gets 20+ sources from Ibis + DataFusion + SQLGlot. Genie gets
its breadth from Lakehouse Federation, so its own intake stays one surface.
Power BI gets it from Power Query. **Only DataMind is contemplating writing them
one at a time.** That is a legitimate choice — a hand-written connector is how
you prove a read-only role and a `READ ONLY` transaction, which is the whole
product — but it should be a decision, not a default. §5.7.

**5. Export is a governance surface everywhere it is mature.** Power BI gates it
five ways, escapes formula characters, and monitors it. Genie caps it at ~1 GB
and expires results at seven days. [mvp2-plan.md
E3](../mvp2-plan.md#e3-result-export--s) says *"Gate it by disclosure policy?
No"* — that is **right**, and it is not the whole question. Export is not a
disclosure decision; it is an **audit** decision and an **injection** decision.
§8.3.

**6. Nobody exposes an MCP server without an identity model underneath it.**
Wren: OAuth, org-level endpoint. Genie: OAuth scope, Unity Catalog enforcing
permissions per-tool. Both sentences are about authorisation, not about tools.
DataMind's tools are almost written; its authorisation model is one bearer token
per signed-in human. §8.5.

---

## 4. What DataMind already has

The starting position, precisely, because three of the five items are further
along than §1.7 implies.

### 4.1 The port is the good news

[`app/domain/ports/database.py`](../../backend/app/domain/ports/database.py)
defines the whole contract for a data source in four methods —
`introspect(schema_allowlist, hints) -> SchemaSnapshot`, `execute(sql, max_rows,
statement_timeout_ms) -> QueryResult`, `explain(sql) -> int | None`, `probe() ->
ConnectionProbe` — over three dataclasses (`TableInfo`, `ColumnInfo`,
`RelationshipInfo`) that already carry catalog comments and content hints.
`build_connector` ([`factory.py:15`](../../backend/app/infra/connectors/factory.py#L15))
maps a `DatabaseKind` to a class and nothing else. **Adding an engine is a
Protocol implementation, a `DatabaseKind` member with a SQLGlot dialect and a
default port, a `factory.py` line, and a `DATABASE_TYPES` entry
([`tokens.ts:138`](../../frontend/src/theme/tokens.ts#L138)).** §1.7 and E4 are
right about this.

### 4.2 The async objection is already answered

A recurring reason to fear warehouse connectors is that Snowflake's and
BigQuery's Python clients are synchronous while `DatabaseConnector` is async.
**That pattern already exists in the tree.**
[`mssql.py`](../../backend/app/infra/connectors/mssql.py) runs a fully
synchronous `pymssql` behind `asyncio.to_thread` for `probe`, `introspect`,
`execute` and `explain`, with a comment explaining why. A Snowflake connector is
the same shape. This is a genuine reduction in the risk of E4.

### 4.3 The result rows are already durable and already served

For every chat run that executed SQL,
[`run_service.py:421`](../../backend/app/services/run_service.py#L421) writes an
`Artifact(kind=TABLE)` whose `spec` holds `columns`, `rows`, `row_count` and
`truncated` — the complete result set, inline JSONB.
[`conversations.py:470`](../../backend/app/api/v1/conversations.py#L470) serves
it with `offset`/`limit` (default 500, max 5,000) under the owner check
`Run.owner_id == ctx.user_id`.

Dashboard tiles have the same property through
`DashboardTileCache`, and report blocks through `ReportBlockResult`. **Every
result in the product is already stored, already authorised, and already
paginated. Export needs a writer and a `Content-Disposition` header.**

### 4.4 The frontend already downloads a file, and already takes one

`exportDashboard` in
[`dashboard-transfer.tsx:33`](../../frontend/src/components/dashboard-transfer.tsx#L33)
fetches with the bearer token, wraps the body in a `Blob`, creates an object
URL, clicks a synthetic anchor and revokes on the next tick — with the comment
explaining why a plain `<a download>` cannot work here. The import dialog at
[`:242`](../../frontend/src/components/dashboard-transfer.tsx#L242) is a real
`<input type="file">` with a mapping step. **Both halves of the UI pattern
exist**; a CSV button reuses the first verbatim, and an upload wizard reuses the
second's shape.

### 4.5 `python-multipart` is already a dependency

[`pyproject.toml:38`](../../backend/pyproject.toml#L38) declares
`python-multipart>=0.0.12`. Nothing in `app/` uses `UploadFile` or multipart
today — it is declared and unused. The transport for file upload needs no new
dependency; the *parsing* is the work.

### 4.6 Snapshots are already versioned — the diff §1.7 wants is computable now

[`connections.py:219`](../../backend/app/api/v1/connections.py#L219) does not
overwrite. It reads `max(version)`, adds one, and inserts a new
`SchemaSnapshotRow`. Every historical shape of every connection's schema is
still in the database. And `app/semantic/validate.py:163` (`validate_document`)
already binds a semantic document to a snapshot and flags what no longer
resolves, in the backend, as a pure function.

So E5 ("scheduled and incremental schema sync") decomposes into three pieces of
very different sizes: **a scheduler** (new, small — the reconciler in
`app/workers/` is the precedent), **a diff** (essentially free — two JSONB
documents that already exist), and **incremental introspection** (real work, per
engine, and the least valuable of the three).

### 4.7 What is genuinely absent

- **A file has nowhere to live.** `database_connections`
  ([`models.py:106`](../../backend/app/infra/db/models.py#L106)) makes `host`,
  `port`, `database_name`, `username` and `encrypted_password` all `NOT NULL`.
  There is no object store, no volume, no blob column, no local database file.
- **No credential a machine can hold.**
  [`deps.py:50`](../../backend/app/api/deps.py#L50) accepts one thing: a bearer
  access token from `POST /auth/login`. No API keys, no service accounts, no
  OAuth client credentials, no scopes below "this user".
- **No cost dimension.** Containment is `max_rows` and `statement_timeout_ms`
  ([`query_service.py:166`](../../backend/app/services/query_service.py#L166)).
  Neither bounds bytes scanned, which is what a warehouse bills for.
- **No writer.** No CSV, no XLSX. `openpyxl`/`xlsxwriter` are not dependencies;
  stdlib `csv` is.

### 4.8 Three corrections to §1.7

**① "There is no result export … no API for a third party to fetch an answer."**
The first clause is true of the *user-facing* surface and misleading about the
system. The rows are stored (§4.3) and served over HTTP today. The second clause
is wrong as written: the REST API exists and is fairly complete —
`GET /runs/{id}`, `/runs/{id}/sql`, `/artifacts/{id}`, the whole dashboard and
report surface. **What does not exist is a credential a third party could
hold.** This matters because it moves E2's cost from "build an API" to "build an
authentication primitive", which is a different, larger, and more interesting
piece of work that belongs next to
[§1.5](../mvp2-plan.md#15-single-player-by-construction), not next to E3.

**② "Schema sync is manual and total … no notification that a table changed
shape."** True, and incomplete: snapshots are versioned and never overwritten
(§4.6), so the diff is computable from data already in the database. §1.7 also
attributes drift detection to `semantic-drift.ts`, which is the *frontend*
explainer for an all-or-nothing re-key; the backend already has
`semantic/validate.py`, which is the part a scheduled job would call.

**③ The size of E1 is understated in one direction and overstated in another.**
[E1](../mvp2-plan.md#e1-file-upload--csv--excel--m--best-acquisition-move) says
landing a file "in a per-user DuckDB or a scratch Postgres schema … becomes an
ordinary connection — the entire guard, snapshot, semantic layer and disclosure
machinery applies unchanged." For the **scratch Postgres schema** that is
exactly true and cheaper than "M" suggests (§5.1). For **per-user DuckDB** it is
not true without a schema migration: a DuckDB connection has no host, port,
username or password, and all four columns are `NOT NULL` (§4.7). The two
options are not interchangeable and the plan treats them as one.

---

## 5. Options

Eleven, in three groups: getting files in, getting engines in, getting answers
out. Each carries **Pros**, **Cons**, a size, and — because §2.3 is the whole
product — an explicit line on what it does to the guard, the containment
invariant and the disclosure policy.

Sizes are the plan's: **S** ≈ days, **M** ≈ a week or two, **L** ≈ longer.

### Group I — Getting files in

The four are mutually exclusive *as the primary path*. Pick one.

---

#### Option F1 — Materialise an upload into a scratch schema on the app database

**What.** `POST /connections/{id}/upload` — or a dedicated "Files" connection
created on first upload — parses the CSV/XLSX, infers types, `CREATE TABLE`s
into a per-owner schema (`upload_<owner_id>`) inside the existing application
PostgreSQL, and points an ordinary `DatabaseConnection` row at it. The row is a
real Postgres connection with a real host, port, database, username and
password, so **nothing in the schema, the connector factory, the guard, the
snapshot, the semantic layer or the disclosure policy changes at all.**

**Pros**
- **The cheapest thing that can possibly work.** No new engine, no new dialect,
  no new `DatabaseKind`, no migration to `database_connections`. `make up`
  already runs the Postgres it needs.
- The `PostgresConnector` is the most complete of the four — it is the only one
  that populates content hints (`distinct_count`, `sample_values`, min/max), so
  an uploaded table gets the *best* prompt quality of any connection in the
  product, immediately.
- Sync, semantic-layer generation, dashboards, reports, tiles, drift detection
  and the eval harness all work on day one with zero awareness of files.
- Containment is unchanged and provable: a dedicated read-only role on the
  scratch schema, `BEGIN READ ONLY`, statement timeout, row cap.
- Cross-connection joins are *impossible*, which keeps `_bind_connection` and
  the disclosure invariant intact by construction.

**Cons**
- ⚠️ **It puts customer row data in the application database**, which today
  holds only metadata, credentials and results. That is a real change to the
  blast radius of an app-DB compromise and to backup/retention policy, and
  `docs/security.md` would need a new section, not a new sentence.
- Requires a *write* path to a Postgres server from application code, in a
  codebase whose entire posture is "we never write to a database we did not
  create". The writer must be strictly separated from the guarded read path or
  it becomes a fifth guard entry point by accident.
- Type inference in Python is genuinely fiddly (dates, decimals, nulls,
  encodings, BOMs, Excel's serial dates) and stdlib `csv` gives you none of it.
  A 200 MB file is a slow, memory-hungry insert.
- No Parquet, no JSON, no S3 path — and no cheap route to them later.
- The app database becomes a capacity-planning problem shaped by user uploads.

**Guard / containment / disclosure:** unchanged, all three. **Size: S–M.**

---

#### Option F2 — A DuckDB `DatabaseKind`: files become a real engine

**What.** Add `DatabaseKind.DUCKDB` (SQLGlot dialect `duckdb`), a
`DuckDbConnector` implementing the port, and a per-owner `.duckdb` file (or a
directory of Parquet) as the storage. Upload writes with DuckDB's own readers —
`CREATE TABLE t AS SELECT * FROM read_csv(...)` / `read_parquet` /
`read_json` — which is precisely what Wren AI's documentation shows and what
Data Formulator does internally. `database_connections` gains a nullable
`file_path` (or the row stores the path in `database_name` and the other columns
become nullable — a migration either way).

**Pros**
- **This is what the field actually does.** Two independent products converged
  on it; the type inference, the Excel/Parquet/JSON readers, the memory
  behaviour on large files and the columnar scan speed are somebody else's
  solved problem.
- Formats for free: CSV, TSV, JSON, Parquet, and Excel via an extension —
  DataMind would leapfrog Wren Cloud's file support in one connector.
- **DuckDB is a first-class SQLGlot dialect**, so the guard's parse/render path
  needs no new work; the allowlist in `sqlguard/policy.py` may need a handful of
  DuckDB-specific function names and nothing structural.
- Read-only is *provable and total*: `duckdb.connect(path, read_only=True)` on
  the query path is a stronger guarantee than a role grant, because it is a
  property of the handle, not of a catalog somewhere.
- Customer row data stays out of the application database (F1's main cost).
- It is the only file option that leaves a door open to object storage (S3,
  Azure Blob) and to Option **W2** later, since the same engine attaches
  Postgres and MySQL.

**Cons**
- ⚠️ **A migration to `database_connections`**, the most load-bearing table in
  the product, to make five `NOT NULL` columns optional or to introduce a second
  connection *kind*. Every read model, the secret box AAD binding, `_owned`, the
  connection form and `DATABASE_TYPES` are touched.
- A new dependency on the request path (`duckdb`), and a **new file-lifecycle
  problem the product has never had**: where do these files live in Docker, what
  happens on a two-replica deployment (`docker-compose.replicas.yml`), who
  deletes them, what does backup mean. A local file is not shared state, and
  DataMind's replica story assumes the database is the only shared state. **This
  is the real cost of F2 and it is easy to under-count.**
- DuckDB's own SQL surface is enormous (`read_csv` in a query, `COPY`, macros,
  `INSTALL`/`LOAD`, `ATTACH`). The guard already rejects unknown nodes and
  unlisted functions, so the posture holds — but the hostile corpus
  (`test_sqlguard_hostile.py`) needs a DuckDB arm, and `make guard` is the hard
  CI gate. That is not optional work.
- `probe()`'s "prove the role cannot write by trying" has no natural meaning for
  a local file; the honest implementation is "opened read-only", which is
  different from what the other four report.

**Guard:** new dialect arm required in the hostile corpus. **Containment:**
stronger (read-only handle), but `explain()`/row-scan estimates differ.
**Disclosure:** unchanged. **Size: M.**

---

#### Option F3 — Datasets as a first-class concept, separate from connections

**What.** Do not pretend a file is a connection. Add a `datasets` table, an
upload API, its own storage, its own retrieval, and let a conversation be bound
to a connection *or* a dataset. The guard runs against a dataset's own schema
snapshot.

**Pros**
- Honest modelling. A file genuinely is not a server: it has no credentials, no
  read-only role, no drift, no incremental sync, and it *does* have things a
  connection does not — an original filename, an uploader, a size, a checksum, a
  version history, a retention policy.
- The natural home for the things §1.7 wants that a connection cannot express:
  re-upload as a new version, "this file is stale", per-file retention.
- Closest to Genie's model, which is the best-governed one in the research.

**Cons**
- ⚠️ **It forks the product's central abstraction.** Everything that takes a
  `connection_id` — conversations, runs, dashboards, tiles, reports, blocks, the
  semantic layer, drafts, the eval fixtures — grows a second case. That is a
  large, invasive, low-glamour refactor with a high regression surface across a
  1,426-test suite, and it buys nothing a user can see on day one.
- Duplicates the connection machinery (snapshot, semantic layer, policy) or
  generalises it, and generalising it is the same refactor by another name.
- Almost certainly the right end state and almost certainly the wrong first
  step.

**Guard / containment / disclosure:** all three need a second, parallel
statement. **Size: L.**

---

#### Option F4 — Load the file into the customer's own database *(listed to be rejected)*

**What.** Create a table in the user's Postgres/MySQL from the uploaded file, so
joins against their real data just work.

**Pros**
- Solves the cross-source problem completely and for free.
- No new storage anywhere.

**Cons**
- ⚠️ **It breaks the product's founding promise.** Invariant #2 and every
  connector's `probe()` exist to prove that DataMind's credential *cannot
  write*. This option requires a credential that can. There is no version of it
  that keeps the guarantee.
- Turns a demo feature into a production-database mutation, in a tool whose
  users are explicitly not DBAs.

**Verdict: do not build.** Recorded here because it is the obvious idea and
someone will propose it.

### Group II — Getting engines in, and keeping them fresh

---

#### Option W1 — Warehouse connectors, one at a time, behind the existing port

**What.** [E4](../mvp2-plan.md#e4-warehouse-connectors--m-each). Snowflake,
BigQuery, Databricks SQL, ClickHouse, Redshift — each a `DatabaseConnector`
implementation, a `DatabaseKind`, a `factory.py` line, a `DATABASE_TYPES` entry,
a hostile-corpus arm and a real read-only role verified against a live server.

**Pros**
- **Nothing architectural changes.** The port was designed for exactly this and
  §4.1/§4.2 show both the shape and the sync-driver workaround already exist.
- Each connector is independently valuable and independently shippable; you can
  do Snowflake and stop.
- Keeps the per-engine containment story that is DataMind's differentiator:
  a hand-written connector is where you *prove* the role cannot write, set the
  statement timeout, and cap rows in the engine's own idiom.
- Prioritise by customer, not by ease — one real customer's warehouse is worth
  more than three speculative ones.

**Cons**
- ⚠️ **Containment does not port.** There is no `BEGIN READ ONLY` on Snowflake
  or BigQuery. Snowflake's answer is a role with no write grants plus
  `STATEMENT_TIMEOUT_IN_SECONDS`; BigQuery's is IAM plus `maximumBytesBilled`
  plus a dry run. Both are *different guarantees*, and invariant #2 as written
  in CLAUDE.md would become false unless it is restated per engine. This is a
  documentation-and-honesty problem as much as a code problem.
- ⚠️ **A new containment axis: money.** `max_rows` and `statement_timeout_ms`
  do not bound bytes scanned. BigQuery's `maximum_bytes_billed` "estimated
  before the query execution … the query fails without incurring a charge" is
  the right primitive and DataMind has nowhere to put it. `explain()` returning
  a row estimate is the seam, but the policy, the UI and the error code do not
  exist.
- ⚠️ **It collides with §1.2.** `_RETRIEVE_BUDGET_CHARS = 50_000`
  ([`nodes/__init__.py:260`](../../backend/app/pipeline/nodes/__init__.py#L260))
  and the naïve matcher are tuned to a 42-table fixture. A real Snowflake
  account is thousands of tables across dozens of schemas. **Shipping a
  warehouse connector before the retrieval work in
  [retrieval-at-scale.md](retrieval-at-scale.md) ships a product that connects
  successfully and then answers badly** — which is worse than not connecting.
- No demo server. The Oracle and SQL Server compose services were already
  removed for RAM; a Snowflake or BigQuery arm cannot be exercised from
  `make up` at all, so the connector is only as good as somebody's real account.
- Cost per engine is real: driver, auth (key-pair, OAuth, service-account JSON —
  none of which is a password in a `SecretBox`), dialect quirks, information-
  schema differences, hint capture.

**Guard:** dialect arms needed. **Containment:** must be restated per engine.
**Disclosure:** unchanged. **Size: M each**, and the first one is more than the
second.

---

#### Option W2 — One federating engine behind a single connector

**What.** Ship one `DatabaseConnector` backed by DuckDB (`ATTACH … (TYPE
POSTGRES, READ_ONLY)`, `TYPE MYSQL`, plus `httpfs`/S3) or by Ibis, and get
several sources from one implementation — Genie's Lakehouse Federation shape,
Wren's Ibis shape.

**Pros**
- **Breadth per unit of code is unbeatable**, and it is what both comparable
  competitors actually did (§3 lesson 4).
- `READ_ONLY` is available on DuckDB's `ATTACH`, so the containment story has a
  real answer for the attached sources.
- Composes with F2 — same engine, same connector, one new dependency total.
- It is the only credible route to cross-source questions (Option X1).

**Cons**
- ⚠️ **It moves the query out of the engine that owns the data.** Predicate
  pushdown is the vendor's business, not yours; a join DataMind thinks is cheap
  can become a full table scan over the network. The row cap is applied *after*
  the federation layer has already pulled the data.
- ⚠️ **It dissolves the per-engine containment proof.** Today each connector
  proves its own role is read-only against the real server. Through a federation
  layer, DataMind proves DuckDB's handle is read-only — which says nothing about
  the credential DuckDB is holding for Snowflake.
- The snapshot, hints and comments all arrive through the federation layer's
  view of the catalog, which is lossier than a native `information_schema` read.
  Catalog comments — a shipped feature — would likely be lost.
- One dependency becomes load-bearing for every source, and its bugs are yours.

**Guard:** one dialect (DuckDB) for everything, which is *simpler* than W1.
**Containment:** ⚠️ materially weakened. **Disclosure:** unchanged.
**Size: M** for the first two attached kinds, then near-free.

---

#### Option W3 — Stay at four engines, deliberately, and say so

**What.** Decline the warehouse work for MVP2. Put the four supported engines
and the reason on the marketing surface: DataMind is the BI tool for the
operational database, where the guard's guarantees are strongest.

**Pros**
- Every engineer-week goes to §1.1/§1.2, which is where the plan's own thesis
  says the product is won or lost.
- The three `●`-with-emphasis rows in §2.3 stay unqualified and true.
- Honest positioning beats a checkbox: "four engines, each with a proven
  read-only role" is a better sentence than "22 sources" for the buyer who cares
  about the guard.

**Cons**
- ⚠️ **§1.7's central factual claim stands unanswered**: analytics data in 2026
  is in the warehouse. This is the option that loses deals in the first meeting.
- Every competitor's comparison table will show a `4` next to a `20+`.
- The gap widens over time and the eventual catch-up is not cheaper for waiting.

**Size: zero.** Listed because "not now" is a real option and the plan should
state it as one rather than let it happen by omission.

---

#### Option S1 — Scheduled re-sync and a snapshot diff

**What.** [E5](../mvp2-plan.md#e5-scheduled-and-incremental-schema-sync). A
periodic job per connection that re-introspects, writes the next
`SchemaSnapshotRow` version, **diffs it against the previous version**, and
surfaces "three tables changed shape, one column your semantic layer references
is gone" — routed through `semantic/validate.py`, which already computes exactly
that second half.

**Pros**
- **The diff is nearly free** (§4.6): both documents are already stored and the
  validator already exists. Most of the perceived cost is imaginary.
- `app/workers/` already has a scheduled job under a transaction-scoped advisory
  lock (the stale-run reconciler) — the multi-replica correctness pattern is
  written down and tested.
- It is what makes the semantic layer and any future verified-query store
  trustworthy over time; [learning-loop.md](learning-loop.md) needs it.
- It converts a silent wrong answer into a visible notification, which is the
  cheapest accuracy win in Theme E.

**Cons**
- A background job that opens a customer database on a timer is a new
  operational behaviour: it consumes connections, it can hammer a busy server,
  it fails when a credential is rotated, and *someone has to see the failure*
  ([§1.8](../mvp2-plan.md#18-nobody-can-see-the-system-running) says nobody can).
- Hint capture under `HintBudget` runs `probe_values` queries; doing that nightly
  on a large table is not free for the customer.
- **Incremental** introspection (the other half of E5) is per-engine work with a
  poor ratio — do the scheduler and the diff, and treat "total but scheduled" as
  the answer until a real connection is too big for it.
- Notifications need somewhere to land, and there is no notification surface.

**Guard / containment / disclosure:** unchanged. **Size: S** for scheduler +
diff; **M** if incremental introspection is included.

---

#### Option X1 — Cross-connection questions *(analysed, and recommended against for MVP2)*

**What.** Let one question join two connections — the §1.7 example, "revenue
from Postgres against the campaign list in this spreadsheet" — via W2's
federation engine.

**Pros**
- It is a real question users really ask, and F1/F2 make it *more* common by
  putting a spreadsheet next to a database.
- Technically reachable once W2 or F2 exists: DuckDB attaches both sides.

**Cons**
- ⚠️ **It breaks the disclosure invariant at its root.** `_bind_connection`
  pins a conversation to one connection *"so history can never cross
  policies"*. Two connections can hold two different `disclosure_policy` values,
  two different `HintBudget`s, two different `include_db_comments` settings and
  two different owners. There is no defensible answer to "what may the model see
  about this joined result" short of "the strictest of the two, computed per
  column, at render time" — which is a substantial piece of design in the most
  safety-critical module in the product.
- The guard resolves names against *the* snapshot; two snapshots means a merged
  namespace and a collision policy (`orders` in both).
- **No competitor at this tier has solved it either** (§1.2, note ³): Wren needs
  Trino; Genie needs everything to already be in Unity Catalog. Being second to
  a hard problem is not a market position.

**Verdict: name it, scope it out, and say why** — the way
[§1.5](../mvp2-plan.md#15-single-player-by-construction) scopes out RLS. Revisit
after the disclosure model has a per-column story.

### Group III — Getting answers out

---

#### Option O1 — Result export: CSV first, Excel if asked for

**What.** [E3](../mvp2-plan.md#e3-result-export--s). One endpoint per result
kind — chat artifact, dashboard tile, report block — returning `text/csv` with a
`Content-Disposition` filename, built on the read paths that already exist and
already authorise (§4.3). Frontend reuses `exportDashboard`'s blob-download
helper verbatim (§4.4).

**Pros**
- **The highest ratio in this document.** Days of work; removes the sentence
  *"a user who has the table they wanted cannot get it into a spreadsheet. They
  will screenshot it."*
- No new read path, no new authorisation, no re-query, no cost to the customer's
  database — the rows are already in the app database.
- stdlib `csv` only. Zero new dependencies for the CSV half.
- Unblocks the most common real workflow in BI (get it into Excel and keep
  working) and closes a `○` that every one of the four has as `●`.

**Cons**
- Excel (`.xlsx`) is a genuinely separate piece of work — a new dependency
  (`openpyxl`/`xlsxwriter`), type/format mapping, and a streaming story for
  large sheets. **Ship CSV first; treat Excel as a separate decision**, and note
  that only Power BI of the four actually ships `.xlsx`.
- ⚠️ **CSV injection.** Result values are untrusted content
  ([security.md §2.4](../security.md)); a cell beginning `=`, `@`, `+` or `-`
  becomes a formula in Excel. Power BI escapes these with a leading `'` and
  DataMind must too. This is small, but it is not optional and it needs a test.
- ⚠️ **The export ceiling is not the result ceiling.** A chat result is capped
  at `connection.max_rows` (default 1,000) and a tile may tighten it further, so
  "export" means "export what was already capped", not "export the query". The
  UI must not imply otherwise — Power BI's caps and Genie's ~1 GB exist because
  users assume export means everything.
- Nothing records that it happened (§8.3).

**Guard / containment:** untouched. **Disclosure:** untouched — and §8.2 argues
why that is correct rather than an oversight. **Size: S.**

---

#### Option O2 — An API credential a machine can hold, plus a public `ask`

**What.** API keys (or service accounts) with scopes, stored hashed, revocable,
listed in the UI; and `POST /api/v1/ask {question, connection_id}` returning
`{answer, sql, rows, chart}` — the chat pipeline without a conversation.

**Pros**
- **It is the actual prerequisite for E2, for embedding, and for any
  integration at all** (§4.8 ①). Everything else in "reach outward" is blocked
  on it, and nothing else is blocked on those.
- The pipeline is already callable without a browser: `AnalyticsPipeline.run`
  and `sql_draft_service.draft_sql` both take a connection and a question. The
  `DRAFT_GRAPH` path already runs with no history, no events and no persistence
  — **a stateless `ask` is close to a route over machinery that exists.**
- A scoped key is also the honest way to answer "who may read through this
  connection" for a *machine*, which is a strictly easier question than the
  human one D1 is stuck on.

**Cons**
- ⚠️ **It is a §1.5 problem wearing a Theme E hat.** A key today can only mean
  "act as this user", because there is no authorisation model finer than owner.
  Hand an agent a key and you have handed it every connection that user owns,
  with no per-connection scope, no rate limit and no audit
  (`audit_logs` exists and nothing writes to it). Shipping the credential before
  the scope is shipping the thing D1 was deferred to avoid.
- Real security surface: hashing, rotation, expiry, revocation, prefix display,
  leak response, and a rate limiter the product does not have.
- A stateless `ask` bypasses `disclose_history()` by having no history — fine,
  and it must be *deliberately* fine, with a test, not incidentally fine.

**Guard / containment:** untouched (the same nodes, the same guard). ⚠️
**Authorisation:** new, and the weakest link. **Size: M.**

---

#### Option O3 — An MCP server over the same service functions

**What.** [E2](../mvp2-plan.md#e2-an-mcp-server--sm--highest-leverage-per-line-of-code).
Expose `list_connections`, `get_schema`, `ask`, `generate_sql`, `run_sql`,
`export_result` over MCP — the Wren tool list almost verbatim (§1.2), because it
maps almost one-to-one onto DataMind's existing nodes and services.

**Pros**
- **DataMind is an unusually good MCP tool and the plan is right about why**:
  the guard, the row cap, the statement timeout and the disclosure policy are
  exactly the containment an agent integration needs, and **no competitor's MCP
  server has an equivalent of the disclosure policy**. That is a genuinely
  differentiated sentence in a crowded category.
- Almost all the work is already done (§1.2's table): seven of Wren's eight
  tools already exist as service functions.
- Distribution: it is how a product becomes reachable from inside somebody
  else's agent, which is the cheapest acquisition channel available in 2026.

**Cons**
- ⚠️ **Strictly blocked on O2.** Both competitors' MCP servers are sentences
  about OAuth and permission enforcement, not sentences about tools (§3 lesson
  6). Shipping tools first means shipping an unauthenticated or
  user-token-shaped surface, and a user access token is short-lived by design —
  wrong shape for an agent.
- An MCP client is a *model*, so every tool argument is model-authored input.
  That is fine for `ask` (the guard is the whole point) and much less fine for
  anything that takes a connection id, a row limit or a file path.
- Transport, protocol version drift, and a second public surface to keep
  compatible with the SPA's.
- The plan's **S–M** is right for the tools and wrong for the total, because the
  total includes O2.

**Guard / containment:** untouched. ⚠️ **Authorisation:** inherits O2's.
**Size: S–M on top of O2; not shippable without it.**

---

### 5.12 The options side by side

| | Option | Size | Value | Risk to §2.3 | Blocked on |
|---|---|:--:|:--:|:--:|---|
| **F1** | Upload → scratch schema on app DB | S–M | high | none | — |
| **F2** | DuckDB `DatabaseKind` | M | high | low (new guard arm) | a `database_connections` migration |
| **F3** | Datasets as a first-class concept | L | medium | medium | a large refactor |
| **F4** | Load into the customer's database | — | — | ⚠️ fatal | *do not build* |
| **W1** | Warehouse connectors, one at a time | M each | high | ⚠️ containment restated | §1.2 retrieval |
| **W2** | One federating engine | M | high | ⚠️ containment weakened | — |
| **W3** | Stay at four, deliberately | 0 | *positioning* | none | — |
| **S1** | Scheduled re-sync + diff | S | medium-high | none | a notification surface |
| **X1** | Cross-connection questions | L | medium | ⚠️ disclosure at its root | W2 + a per-column policy |
| **O1** | Result export (CSV) | S | **highest ratio** | none | — |
| **O2** | API keys + public `ask` | M | high | ⚠️ authorisation | §1.5 / D1 |
| **O3** | MCP server | S–M | high | inherits O2 | **O2** |

### 5.13 Recommendation

**Build O1, then F2, then S1. Defer W1 behind the §1.2 work. Ship O2+O3 only if
the team is prepared to treat it as an identity project.**

**① O1 (result export, CSV) — first, and it should be first in the whole
theme.** It is days, it touches nothing dangerous, the rows are already stored
and already authorised, the frontend helper already exists, and it deletes the
most embarrassing sentence in §1.7. Do the formula escaping and write the test.
Nothing else in Theme E has this ratio.

**② F2 (DuckDB) over F1 (scratch schema) — but only just, and for one reason.**
F1 is cheaper this quarter and F2 is cheaper every quarter after. The deciding
argument is not the format list; it is that **F1 puts customer rows in the
application database**, which changes the blast radius of the one database that
holds every credential in the product, and that is a decision you cannot walk
back once the first customer has uploaded. F2's cost — a migration and a
file-lifecycle story — is paid once, is visible, and is testable. F2 also
composes with W2 later; F1 composes with nothing.

> If the file-lifecycle story in a two-replica deployment cannot be answered in
> an afternoon (§8.7), **take F1**, put the scratch schema in its *own* database
> rather than the app database, and revisit. That variant keeps most of F1's
> cheapness and most of F2's isolation, at the cost of an extra service in
> compose.

**③ S1 (scheduled sync + diff) — third, because it is small and it protects
everything else.** The diff is nearly free (§4.6). Ship the scheduler and the
notification; leave incremental introspection alone until a real connection
needs it.

**④ W1 (warehouses) — after the §1.2 retrieval work, not before, and one
engine, chosen by a customer.** Connecting to a 4,000-table Snowflake account
with a 50,000-character retrieval budget and a substring matcher produces
confident wrong answers at scale. That is a worse outcome than the current `○`.
When it does happen: Snowflake or BigQuery first (whichever a real customer
has), and restate invariant #2 per engine in `docs/security.md` *in the same
pull request* — with `maximum_bytes_billed` / `STATEMENT_TIMEOUT_IN_SECONDS` as
first-class connection settings, not constants.

**⑤ O2+O3 (API keys, then MCP) — the highest-leverage item in the plan and the
one most likely to be mis-scoped.** E2 is "S–M" only if you already have a
machine credential, and DataMind does not. Either budget for an identity project
(keys, scopes, revocation, rate limits, audit) or do not start; a shortcut here
produces a public, agent-callable surface over production databases with an
authorisation model of "whatever this user owns". The good news is that the
identity work is the *same* work
[§1.5/D1](../mvp2-plan.md#15-single-player-by-construction) needs, so scheduling
them together makes both cheaper.

**Not recommended for MVP2:** F3 (right end state, wrong first step), F4 (never),
W2 (attractive, but it trades away the containment proof that is the product's
differentiator — revisit if W1 proves too slow), X1 (name it and scope it out).

---

## 6. Why this order, and not the plan's

[Part 4 of the plan](../mvp2-plan.md#tier-2--the-differentiators) puts **13 (file
upload)** and **14 (MCP + REST API)** in its recommended cut of three. This
research agrees with the instinct and disagrees with the ordering, for three
reasons.

**First: the cheapest item in the theme is not in the cut at all.** Result
export is scored **S** and ranked below both. But §4.3 shows it is smaller than
"S" implies — the rows are stored, the endpoint exists, the frontend helper
exists — and it removes a daily, visible, universally-noticed absence. A feature
that takes days and that every user hits every session should not be ranked
below a feature that takes weeks and that a subset of users hits once.

**Second: "MCP + REST API" is two items, and the invisible one is the big one.**
The plan's own words are *"highest leverage per line of code"*, and per line of
code that is true. But the lines of code are not the cost: the cost is an
authentication and authorisation primitive that does not exist and that
[D1](../mvp2-plan.md#d1-an-answer-to-who-may-read-through-this-connection--m--blocking)
already marks **⚠️ blocking**. The plan files the credential under Theme D and
the surface under Theme E and never joins them. Joined, E2 is an **M–L**, not an
**S–M** — and it becomes considerably cheaper if it is built *as* D1's machine
half rather than beside it.

**Third: warehouse connectors and retrieval are the same project.** The plan
ranks **B2 (hybrid retrieval)** in Tier 1 and **E4 (warehouse connectors)** in
Tier 3, which is the right order — but §1.7 does not say *why* they are coupled,
and someone reading Theme E alone will not find out. A warehouse is not just a
bigger database; it is the first data source in the product that will
**structurally exceed** the retrieval budget on day one. Connecting to it
without B2 converts an honest `○` into a confident wrong answer.

The one place this research is more optimistic than the plan is **E5**: §4.6
shows the diff is essentially free, which moves scheduled sync from
"nice-to-have" to "small, and it protects the semantic layer and every future
verified query".

---

## 7. A sketch of the recommended path

Not a design. Enough shape to argue about, and enough to size.

### 7.1 O1 — export, end to end

```
GET /api/v1/artifacts/{id}/export?format=csv          # chat result
GET /api/v1/dashboards/{d}/tiles/{t}/export?format=csv # tile (from cache)
GET /api/v1/reports/{r}/runs/{run}/blocks/{b}/export   # report figure
    → 200 text/csv; charset=utf-8
      Content-Disposition: attachment; filename="revenue-by-month-2026-08-31.csv"
```

Three routes, one writer:

```python
# app/api/v1/exports.py  (HTTP shape only, per the layer rule)
# app/services/export_service.py
def to_csv(columns: list[ResultColumn], rows: list[list[Any]]) -> Iterator[str]:
    """Stream a result as CSV.

    Values are escaped for spreadsheet formula injection: a text cell whose
    first character is one of = @ + - is prefixed with an apostrophe, the same
    defence Power BI applies on its own CSV path. Result values are untrusted
    content (security.md §2.4) and a spreadsheet is an execution context.
    """
```

Every route reuses the existing authorisation check verbatim — the artifact
route already filters on `Run.owner_id == ctx.user_id`, and the tile route
already carries `ctx.user_id` into `DashboardService.refresh`. **No new read
path, no new permission.** The frontend adds a button that calls the existing
blob-download helper.

The `truncated` flag on the result travels into the file as a final comment row
or into the filename, because a silently-capped export is the failure mode every
competitor's documentation is apologising for.

### 7.2 F2 — files as a DuckDB connection

```
POST /api/v1/uploads            multipart: file
  → parse header, sniff types with DuckDB's own reader
  → CREATE TABLE "<sanitised>" AS SELECT * FROM read_csv(?, header = true)
     into  <storage>/<owner_id>/<connection_id>.duckdb
  → 201 { connection_id, tables: [{name, columns, row_count}] }
  → then the ordinary sync route runs, unchanged
```

What changes, precisely:

| Where | Change |
|---|---|
| `domain/value_objects` | `DatabaseKind.DUCKDB`, dialect `duckdb`, no default port |
| `infra/db/models.py` | `host`/`port`/`username`/`encrypted_password` nullable; `file_path` added |
| Alembic | one migration, and a backfill that is a no-op |
| `infra/connectors/duckdb.py` | new: `probe`, `introspect`, `execute`, `explain` — opened `read_only=True` on every query path |
| `infra/connectors/factory.py` | one line |
| `sqlguard/policy.py` | a handful of DuckDB function names; **no structural change** |
| `tests/unit/test_sqlguard_hostile.py` | a DuckDB arm — `make guard` is the gate |
| `api/v1/uploads.py` | new: the only place `UploadFile` appears |
| `frontend` | an upload card on Data Sources, reusing the import dialog's shape |

The upload writer is the one component that must be *structurally* unable to
become a query path: it opens its own read-write handle, in its own module, and
the connector never opens anything but `read_only=True`. That separation is
worth an import-linter contract, in the spirit of the seven that already exist.

### 7.3 S1 — the scheduler and the diff

```
app/workers/schema_sync.py
  every N hours, per connection, under a transaction-scoped advisory lock
  (the stale-run reconciler is the pattern):
     snapshot_new = connector.introspect(...)        # as today
     insert SchemaSnapshotRow(version = prev + 1)    # as today
     diff = diff_snapshots(prev, new)                # new, pure, testable
     issues = validate_document(layer, new)          # exists today
     if diff or issues:  record + surface
```

`diff_snapshots` is a pure function over two JSONB documents: tables added,
removed, renamed; columns added, removed, retyped; relationships changed. It
belongs beside `semantic/validate.py`, is DOM-free and engine-neutral, and is
the kind of thing the frontend's `.test.ts` suites already do well.

### 7.4 Order of work

1. **O1 — CSV export**, all three result kinds, with the escaping test. *(days)*
2. **F2 — the DuckDB connector and the upload path**, CSV first, then Parquet
   and Excel for free. *(a week or two)*
3. **S1 — the scheduler and the diff**, with the notification landing wherever
   §1.8's operator surface lands. *(days, after the surface exists)*
4. **Then stop, and re-read §1.1 and §1.2.** Everything after this point in
   Theme E is either blocked on retrieval (W1) or on identity (O2/O3), and both
   of those are better projects than a fifth connector.

---

## 8. Decisions to make before building

### 8.1 ⚠️ Is an uploaded file a connection, a document, or a person's private scratch space? — the load-bearing one

Everything else in Group I follows from this. The three answers in the market:

- **Genie:** private by default and invisible to everyone else — *"Only the user
  who uploaded the file can access it"*, in a volume that *"is not listable and
  does not appear in the schema browser."*
- **Power BI:** a workspace asset, shared with the workspace, governed by
  sensitivity labels.
- **Wren / Data Formulator:** a project/session artifact — shared with whoever
  can see the project.

DataMind's connections are already owner-scoped and there is no sharing at all
(§1.5), so **the Genie answer is the free one today**. But three sub-questions
have no default:

1. **What `disclosure_policy` does an uploaded file get?** A connection defaults
   to `SAMPLE`. A spreadsheet a user dragged in is *more* likely to be a
   personal export of sensitive data than a governed warehouse table, and the
   user who dragged it in has not thought about the model provider at all.
   Defaulting an upload to `SAMPLE` is a decision to send its values to an LLM.
   Consider defaulting uploads to `AGGREGATE` and making the widening explicit.
2. **Does the sensitive-name floor still make sense?** `is_sensitive_column`
   matches on names a DBA chose. A spreadsheet's headers are whatever someone
   typed — `Col1`, `email address (personal)`, `ssn_last4`. The floor will be
   less effective exactly where it matters most.
3. **When sharing arrives (D2), do uploads share?** Say now, or the first
   sharing feature decides it by accident.

### 8.2 ⚠️ Export is not a disclosure decision — settle it in writing

[E3](../mvp2-plan.md#e3-result-export--s) already gets this right and the
reasoning deserves to be in `docs/security.md` rather than in a plan:

> The disclosure policy governs **what reaches the model provider**. It has
> never governed what reaches the user. A user looking at a result table on
> screen has already seen every value; a CSV of the same rows discloses nothing
> new to anyone. Gating export by `disclosure_policy` would be a category error
> — it would restrict the trusted party to protect against the untrusted one.

Two riders, so this cannot be misread later:

- It is true **because DataMind has no row-level security** (§1.5, D3). If RLS
  ever ships, "what the user may see" becomes a real question and export becomes
  a real enforcement point — Power BI's *"If RLS is applied, you can only export
  data you're authorized to see"* is the shape of that answer. Write the
  dependency down now.
- It is true of *values*. It is **not** automatically true of the generated SQL,
  which encodes schema. That is already visible in the UI, so it is consistent
  — but an export bundle that carries SQL to somebody who was never shown the
  UI is a different artifact, and that is the dashboard-transfer decision all
  over again.

### 8.3 Escaping, and whether an export is an event worth recording

Two small decisions that are cheap now and awkward later.

**Escaping is not optional.** Power BI escapes a leading `=`, `@`, `+` or `-`
in text cells *"to prevent script execution when opened in Excel"*. DataMind
already treats database content as untrusted (`security.md` §2.4 was written for
catalog comments); a CSV writer without this defence takes untrusted content and
hands it to a spreadsheet as code. One function, one test in the hostile suite's
spirit.

**Should an export be audited?** `audit_logs` exists in the schema and has never
been written to
([D4](../mvp2-plan.md#d4-turn-on-the-audit-log--s--best-ratio-in-the-document)
is *"best ratio in the document"*). Every mature product in §1 logs data egress;
Power BI goes as far as Defender policies on *"downloading sensitive data … to
unmanaged devices"*. **Export is the single most natural first writer for
`audit_logs`** — it is a discrete, user-initiated, data-leaving-the-building
event with an obvious actor, object and time. If D4 is going to happen anyway,
having O1 write the first row costs one line and makes D4 concrete.

### 8.4 ⚠️ Containment for engines that have no `READ ONLY`, and money as a fourth axis

Invariant #2 currently reads: *"`READ ONLY` transaction on Postgres / MySQL /
Oracle; read-only role + query timeout on SQL Server … each connector proves the
role can't write by trying."* Neither clause survives a warehouse unchanged:

| Engine | Read-only proof | Timeout | Cost bound |
|---|---|---|---|
| Snowflake | role grants only — no `READ ONLY` transaction | `STATEMENT_TIMEOUT_IN_SECONDS` (account/user/session/warehouse) | warehouse size + auto-suspend, none of it per-query |
| BigQuery | IAM only | job timeout | **`maximum_bytes_billed`** — estimated before execution; over the limit *"the query fails without incurring a charge"*; plus dry run |
| DuckDB (F2) | `read_only=True` on the handle — *stronger* than a role | none native | n/a |

Three things follow. **(a)** Invariant #2 must be restated per engine, in
`docs/security.md`, in the same pull request as the first warehouse connector —
not afterwards, or CLAUDE.md silently becomes false. **(b)** `probe()`'s
"prove it by trying" needs a per-engine definition and an honest
`readonly_confirmed=False` where it cannot be proven, rather than a comfortable
`True`. **(c)** **Cost is a containment axis DataMind does not have.**
`max_rows` and `statement_timeout_ms` are the whole vocabulary
([`query_service.py:166`](../../backend/app/services/query_service.py#L166)); a
1,000-row cap does not stop a 40 TB scan. A `max_bytes_scanned` connection
setting, enforced through `explain()`/dry-run *before* execution, is the minimum
— and it is a fail-closed refusal, not a warning.

### 8.5 ⚠️ An MCP server is an authorisation decision, not a protocol decision

Both shipped competitors describe their MCP servers in terms of identity first:
Wren's is *"a single, organization-level endpoint secured with OAuth"*; Genie's
is *"Unity Catalog enforces permissions, so agents and users access only the
tools and data you grant them."* DataMind has one credential type — a
short-lived bearer token for a signed-in human
([`deps.py:50`](../../backend/app/api/deps.py#L50)) — and one authorisation
rule: `owner_id == ctx.user_id`.

The decision to make **before** any tool is written:

- **What is the principal?** A key that acts as a user, or a service account
  that is its own principal? The second is more work and is the only one that
  can be revoked without locking a person out.
- **What is the scope?** Per-connection is the minimum useful unit, and it is
  exactly D1's question ("who may read through this connection") asked about a
  machine instead of a person. **Answer it once, for both.**
- **What is the rate limit?** There is none anywhere in the product. An agent in
  a loop against a customer's production database is a denial-of-service with a
  friendly name.
- **What gets audited?** See §8.3. A machine-initiated question is the strongest
  possible argument for turning `audit_logs` on.

If those four have no answers, E2 is not "S–M"; it is a security project with a
protocol on top.

### 8.6 Staleness, retention and quota for an uploaded file

Power BI is retiring local-file import because a copy cannot refresh, and Genie
ties file lifetime to conversation and agent lifetime rather than to a clock.
Decide, on day one, because each of these is a migration later:

- **Re-upload is a version, not a new connection.** A file must be replaceable
  in place, keeping its connection id, its semantic layer, its dashboards and
  its tiles. Without this, every refresh orphans everything built on it — the
  exact failure Microsoft is deprecating.
- **The UI must show the file's age**, next to `last_synced_at`, because a
  spreadsheet is stale from the moment it lands and nothing will tell the user
  otherwise.
- **Retention and quota.** Genie's answer is lifecycle-based (removed when the
  user, conversation or agent goes away); DataMind's natural equivalent is
  "deleted with the connection", which the `ondelete="CASCADE"` on
  `database_connections` already implies for the rows — but **not for the file
  on disk**, which nothing will clean up. Per-owner size and count quotas are
  cheap to add now and unpleasant to retrofit.
- **Result retention is a separate question with a competitor answer.** Genie:
  *"Query results persist for seven days"*, keeping the SQL and offering a
  re-run. DataMind stores every result forever, inline in `artifacts.spec.rows`
  ([`run_service.py:421`](../../backend/app/services/run_service.py#L421)). That
  is what makes O1 free — and it is also an unbounded, un-pruned store of
  customer data in the application database. Worth a policy before it is worth a
  problem.

### 8.7 Where does a DuckDB file live when there are two replicas?

`docker-compose.replicas.yml` exists and the whole design assumes **the database
is the only shared state** — the run executor claims rows, the reconciler takes
an advisory lock, and nothing on a replica's local disk matters. A per-owner
`.duckdb` file breaks that assumption: replica A holds the file, replica B gets
the request.

Three answers, pick one before writing the connector: a shared volume (simplest;
constrains deployment), object storage with a local cache (correct; a project of
its own), or **route requests for a file connection to the replica that holds
it** (clever; a new kind of coupling, and probably a trap). If none of these can
be decided quickly, §5.13's fallback to a variant of F1 in its own database is
the pragmatic move.

### 8.8 An uploaded file's headers are untrusted input, and the guard resolves against them

`security.md` §2.4 already establishes catalog descriptions as *"a new class of
content, and it is untrusted"* — a DBA wrote them, and they reach the model. An
uploaded spreadsheet is a strictly larger version of the same problem, and it is
worth naming because the intuition ("it's just a CSV") is wrong in three ways:

1. **Column headers become identifiers** that land in the schema snapshot, in
   `GuardPolicy.allowed_columns`, and in the generated SQL. They are attacker-
   controlled in a way `COMMENT ON` never was — anyone who can upload a file can
   choose them. Sanitise on the way in and treat quoting in the guard's renderer
   as load-bearing.
2. **Header text reaches the prompt** in the schema block, on every question, on
   every policy including `NONE`. A header reading *"Ignore previous
   instructions and…"* is a prompt-injection vector that does not depend on the
   disclosure policy at all — unlike a row value, which `NONE`/`AGGREGATE`
   removes.
3. **The file itself is a parser surface.** Zip bombs in `.xlsx`, absurd column
   counts, 2 GB of one line, mixed encodings. Genie's published limits (200 MB,
   <100 columns, 25 files per conversation) are a reasonable starting point and
   exist for exactly this reason.

---

## 9. Open questions this research could not close

Listed so the next person does not re-do the searching.

1. **Does Data Formulator export a derived table as CSV/Excel?** Not documented
   in the README or the release notes; only *"Build and export reports as image
   or PDF"*. [mvp2-plan.md §2.6](../mvp2-plan.md#26-the-matrix) scores it `●`.
   Answering it means running the app.
2. **How does Wren AI's Cloud CSV upload store the file?** The 100 MB limit is
   documented; the storage and query engine are not. The OSS DuckDB path is
   documented and is the obvious inference, but it is an inference.
3. **Is the "one data source per Wren project, cross-source needs Trino" claim
   accurate?** Only a third-party comparison page says it. It is consistent with
   the docs' project model but not corroborated by Wren's own documentation.
4. **What does Genie's Conversation API return for result rows?** The overview
   page defers to a REST reference this research could not retrieve; the
   `get_download_full_query_result` method on the Python SDK suggests a
   two-phase fetch (statement id → results), which would be the natural shape
   for a stateless `ask` in DataMind too.
5. **Row-count and cell limits for Genie's CSV download.** Documented as
   *"approximately 1GB"* of data, which is a size and not a shape. No row cap is
   published.
6. **What actually happens to accuracy when a warehouse-scale schema meets a
   text-to-SQL system?** Everyone publishes source counts; nobody publishes
   accuracy against 4,000 tables. The one number found — Databricks' *"84.5
   percent, up from 50 percent"* once Genie Ontology context is applied — is a
   vendor claim about *context*, not about scale, and comes from secondary
   coverage of DAIS 2026 rather than documentation.
7. **Does any competitor gate export by an LLM-disclosure-style policy?** None
   found. Power BI gates by sensitivity label, permission and tenant setting;
   Genie by Unity Catalog. This supports §8.2's conclusion but does not prove
   nobody has considered it.
8. **What is the real per-engine cost of a warehouse connector in this
   codebase?** Unanswerable from desk research and from the tree, because there
   is no demo server for any of them. The honest estimate needs one spike
   against a real Snowflake or BigQuery account with a read-only role — and that
   spike would also settle §8.4's `readonly_confirmed` question, which is the
   part that actually matters.

---

## 10. Sources

**Microsoft Data Formulator**
[Data Formulator (GitHub)](https://github.com/microsoft/data-formulator) ·
[README](https://github.com/microsoft/data-formulator/blob/main/README.md) ·
[Releases](https://github.com/microsoft/data-formulator/releases) ·
[Data Formulator 0.7 (MSR blog)](https://www.microsoft.com/en-us/research/blog/data-formulator-0-7-ai-powered-data-analytics-for-enterprise-data/) ·
[DeepWiki architecture summary](https://deepwiki.com/microsoft/data-formulator) *(secondary — generated documentation)*

**Wren AI**
[Connect Data Sources Overview](https://docs.getwren.ai/cp/guide/connect/overview) ·
[CSV Upload](https://docs.getwren.ai/cp/guide/connect/csv) ·
[DuckDB (files)](https://docs.getwren.ai/oss/guide/connect/duckdb) ·
[Quickstart with your own data](https://docs.getwren.ai/oss/getting_started/own_data) ·
[WrenAI MCP](https://docs.getwren.ai/cp/guide/integrations/wrenai-mcp) ·
[Wren SQL](https://docs.getwren.ai/cp/guide/home/wren_sql) ·
[WrenAI (GitHub)](https://github.com/Canner/WrenAI) ·
[Powering Semantic SQL for AI Agents with Apache DataFusion](https://www.getwren.ai/post/powering-semantic-sql-for-ai-agents-with-apache-datafusion) *(vendor blog — secondary)*

**Databricks AI/BI Genie**
[Upload a file to a Genie Agent](https://docs.databricks.com/aws/en/genie/file-upload) ·
[Use a Genie Agent to explore business data](https://learn.microsoft.com/en-us/azure/databricks/genie-agents/talk-to-genie) ·
[Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api) ·
[Managed MCP servers](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp) ·
[What is query federation? (Lakehouse Federation)](https://docs.databricks.com/aws/en/query-federation/database-federation) ·
[Run federated queries on Snowflake](https://docs.databricks.com/aws/en/query-federation/snowflake) ·
[Genie Ontology explained](https://atlan.com/know/ai-agent/databricks/genie-ontology/) *(third-party — secondary)*

**Power BI / Microsoft Fabric**
[Export data from a Power BI visualization](https://learn.microsoft.com/en-us/power-bi/visuals/power-bi-visualization-export-data) ·
[Get data from CSV files](https://learn.microsoft.com/en-us/power-bi/connect-data/service-comma-separated-value-files) ·
[Get data from Excel workbook files](https://learn.microsoft.com/en-us/power-bi/connect-data/service-excel-workbook-files) ·
[Export Power BI report to file (embedded)](https://learn.microsoft.com/en-us/power-bi/developer/embedded/export-to) ·
[Reports – Export To File (REST API)](https://learn.microsoft.com/en-us/rest/api/power-bi/reports/export-to-file) ·
[Row-level security](https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security)

**Engines and cost controls**
[DuckDB PostgreSQL extension](https://duckdb.org/docs/current/core_extensions/postgres/overview) ·
[Multi-database support in DuckDB](https://duckdb.org/2024/01/26/multi-database-support-in-duckdb) ·
[BigQuery: estimate and control costs](https://docs.cloud.google.com/bigquery/docs/best-practices-costs) ·
[Snowflake: cost controls for warehouses](https://docs.snowflake.com/en/user-guide/cost-controlling-controls)

**DataMind, in this repository**
[mvp2-plan.md §1.7](../mvp2-plan.md#17-the-data-surface-is-narrow-in-both-directions) ·
[Theme E](../mvp2-plan.md#theme-e--reach) ·
[security.md](../security.md) ·
[CODEBASE.md](../CODEBASE.md) ·
[pipeline.md](../pipeline.md) ·
[architecture.md](../architecture.md)
