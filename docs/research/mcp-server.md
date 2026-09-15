# An MCP Server in Front of DataMind

## Market Research and Design Decisions

**Status: research only.** Nothing described here is built. This document
surveys what comparable products ship, states honestly where DataMind is and
is not different, and lists the decisions that have to be made before any code
is written. It does not propose a schedule.

*Researched 2026-09-07.*

---

# 1. Context

The Model Context Protocol (MCP) is an open standard for connecting AI
assistants to the systems where data lives. An MCP server exposes a set of
**tools** — named operations with typed inputs — that an AI client such as
Claude, ChatGPT, or GitHub Copilot can call on the user's behalf.

The question this document answers is: if DataMind put an MCP server in front
of itself, what would it expose, and what has to be decided first?

Two findings shape everything below.

**The first: this is now table stakes.** All five platforms we looked at ship
an official MCP server, and every one of them shipped it within roughly the
last year. An MCP endpoint is no longer a differentiator in BI. Not having one
is starting to look like an absence.

**The second: DataMind's differentiator is not the endpoint, it is what sits
behind it.** Every product in this survey exposes "an agent can ask your data
questions." Several check the SQL first — Superset scans for mutations, WrenAI
dry-plans against its semantic layer — but none walks an *allowlist*, and none
holds a store of answers a human already verified. Those are the two things
worth building the offering around, and Section 4 is careful about exactly how
much of each claim survives scrutiny once WrenAI is in the comparison.

---

# 2. What the Market Ships

## 2.1 Summary

| Product | Official MCP server | Shape | Notable |
|---|---|---|---|
| **Metabase** | Yes, built in (v60+) | one endpoint inside the app | OAuth, ~13 tools, read + write |
| **Apache Superset** | Yes, in-tree (SIP-187) | separate process, shared DB | ~20 tools, preview-first writes |
| **Power BI / Fabric** | Yes, two of them (preview) | remote hosted + local stdio | split by job: query vs. author |
| **Databricks Genie** | Yes, managed (beta) | hosted | grounded in a governed ontology |
| **WrenAI** | Yes, hosted (Wren AI Cloud) | remote endpoint, OAuth | ~7 tools; open-source core, MDL semantic layer |

## 2.2 Metabase — built in, and the closest model to ours

Metabase ships its MCP server **inside the product** from version 60. There is
no separate install and no sidecar: it is an endpoint on the application
itself, at `/api/metabase-mcp`, spoken over Streamable HTTP.

Authentication is OAuth 2.0 through an embedded OAuth server, and access
tokens are scoped to the permissions the person already has in Metabase. When
a client connects, the user is sent to an authentication page and then
approves or blocks individual tools.

Their tools fall into three groups, and the grouping itself is instructive:

- **Read** — search, read a resource (collections, dashboards, databases,
  metrics, models, questions, tables), construct a query, execute a query
- **Write** — create a question, dashboard, or collection; update a dashboard
  or question
- **Raw SQL** — `execute_sql`, gated *separately*: it requires native-query
  permission, and an administrator can disable it globally with a single
  setting

Two details worth stealing. They have **interactive tools** that return a
rendered visualization rather than text. And they keep an **authorization
audit log** covering client registrations and approve/deny decisions.

## 2.3 Apache Superset — in-tree, roughly twenty tools

Superset's MCP service was proposed as SIP-187 and lives in the main
repository. It runs as a **separate process** from the web server, with its
own application instance, but shares the same database and configuration.

Tools cover charts (7), dashboards (4), datasets (2), query and explore (4),
and system operations (3).

The design idea worth borrowing is **preview-first**. Chart creation and
update default to *not writing*: the caller must explicitly ask to persist.
There is a dedicated preview tool for iterating without committing. Their
stated reasoning is that LLM-driven exploration otherwise fills the database
with abandoned attempts — a good instinct, and one that generalises past
charts.

Their security posture is a single sentence worth quoting, because it is the
sentence we would want to be able to say too:

> The MCP service cannot grant permissions that don't exist in Superset.

The same data-access objects and the same security manager serve both the web
UI and MCP, so row-level security and role-based access apply identically.

## 2.4 Power BI and Fabric — two servers, split by job

Microsoft shipped **two** MCP servers, and the split is the most interesting
structural decision in the survey. Both are in public preview.

| | **Remote** | **Local** |
|---|---|---|
| Purpose | query existing models | build and edit semantic models |
| Transport | Streamable HTTP, hosted by Fabric | stdio, runs on your machine |
| Authentication | Microsoft Entra ID (OAuth) | Entra ID or service principal |
| Tools | query only | metadata read/write, query, database operations |

The remote server generates and executes DAX using the same query-generation
engine behind Copilot, and the agent learns the model's tables, columns, and
relationships in order to form correct queries. The local server edits tables,
columns, measures, and relationships from natural language, does bulk
operations with transaction support, and works against project files so that
changes flow through normal source control.

Their documentation carries a warning we should read before designing ours:

> Autonomous or misconfigured clients may perform destructive actions.
> Certain safeguards, such as flags to prevent destructive operations, are not
> standardized in the MCP specification and may not be supported by all
> clients.

That is an honest statement of the problem, and it is the problem our guard
already solves for a different reason.

## 2.5 Databricks Genie — the semantic layer as the product

Genie One ships a managed MCP server, in beta. Any agent can ask
natural-language questions across Genie agents and governed data and receive
grounded answers, with business terms, metric definitions, and table
relationships resolved through **Genie Ontology** — their governed semantic
layer.

They also use an *MCP App*: an extension that lets the server return an
interactive view rather than plain text.

Genie is the closest thing in the market to our semantic layer, and it is
worth watching. It is a definitions layer that makes generation better
informed. It is not a store of verified question-and-answer pairs — see §4.2.

## 2.6 WrenAI — the closest competitor in the survey

WrenAI (Canner, Apache-2.0) is the one product here that is shaped like
DataMind rather than like a BI suite that grew a chat box. It is open-source
conversational BI over 20-plus data sources, self-hostable, built around a
semantic layer, and it ships an official MCP server. Of everything surveyed it
is the most direct comparison, and it is the one worth reading closely.

Its MCP server is **hosted** — a single organization-level endpoint at
`https://cloud.getwren.ai/api/mcp`, spoken over HTTP, authenticated by **OAuth
against the user's Wren AI account**, with no API key and no local proxy. An
earlier per-project URL that carried a token in the path is deprecated and
being removed. That migration is itself a data point for §5.1: they started
with a token in a URL and moved to OAuth, which is the order this document
recommends, with the retrofit cost paid publicly.

The tool set is close to the one §6.1 arrives at independently:

| Tool | What it does |
|---|---|
| `list_projects` | which projects the agent can reach |
| `get_project_metadata` | table schema for a project |
| `ask` | end-to-end natural-language question |
| `generate_sql` | question in, SQL out |
| `run_sql` | execute a statement |
| `generate_chart` | a Vega chart specification |
| `generate_summary` | summarise a result set |
| `respond_clarification` | answer a clarifying question in a follow-up call |

Two of those deserve attention. **`respond_clarification` is the clarification
round-trip §5.5 says has to be designed rather than discovered** — somebody
else has already concluded that a run which ends by asking a question needs a
first-class way back in over MCP. Our `clarify` node produces exactly that
shape and ends a run `NEEDS_CLARIFICATION` with the options as an artifact, so
the hard half is built; the tool is the easy half. And `generate_chart`
returning **Vega** is the same choice we made, which means a chart-returning
tool is a serialisation of something we already have rather than new work.

Their governance story is **MDL plus dry-plan validation**. Data teams define
models, relationships, metrics and business rules in MDL, a Git-friendly JSON
definition layer; generated SQL is planned against that layer and dry-plan
validated before execution, with row limits and structured errors as the
guardrails. Row- and column-level security is applied server-side per user —
though that is a commercial-tier feature, not part of the open-source core.

**How this changes the differentiation claim** — honestly, and it does change
it:

- **The semantic layer is no longer distinctive.** MDL is a mature, versioned,
  reviewable definitions layer, and it is arguably ahead of ours on being
  Git-friendly. §2.7's "the meaning layer is the moat" is right about the
  market and wrong as a claim we can make alone. Ours earns its place by being
  generated and merge-safe, not by existing.
- **Dry-plan validation is a real check, and it is a different one.** A dry
  plan asks *can this be planned against the model* — names resolve, joins are
  legal, the shape is coherent. That overlaps our name resolution against the
  snapshot. It is **not** an allowlist walk over the syntax tree: planning
  successfully is not the same as containing only permitted node types, and
  the §4.1 argument survives intact. But the honest sentence is now "Superset
  denylists, WrenAI dry-plans, neither walks an allowlist," which is narrower
  than the sentence this document opened with.
- **Verified answers still have no equivalent.** WrenAI's context layer holds
  `instructions.md` and `queries.yml` — company knowledge and example queries
  in version control, closest to our few-shot path (which ships **off**, per
  §4.2's caveat). What is absent is the rest of the loop: no parameters lifted
  off the syntax tree, no bind-or-cancel veto, no staleness re-check that
  withdraws an entry when the schema moves, no override rate. A curated
  example file is a static asset a human maintains; the knowledge store is an
  artifact that withdraws itself. §4.2's claim holds, and this is the product
  most likely to close the gap.
- **Disclosure is still ours alone.** Row/column security is access control —
  who may see a column at all. It is not a per-connection policy governing how
  much result *data* may reach a model, which remains without equivalent
  anywhere in this survey.

One structural difference worth noting for §5.3. WrenAI's core is a CLI and an
engine you self-host, but the MCP server is delivered from *their* cloud. We
would be putting an MCP endpoint in front of software the customer already
runs, which is the Metabase shape and a better fit for a customer whose
premise for choosing us is that their data does not leave.

## 2.7 The patterns that repeat

Four things every one of these products does. Together they define the
baseline expectation for anything we ship.

1. **The MCP server is the same application, not a new one.** Metabase mounts
   an endpoint; Superset runs a separate process but shares the database and
   security manager; WrenAI's endpoint speaks to the same projects and the same
   MDL layer the product already runs on. Nobody built a parallel service with
   its own permission logic — that is how a product ends up with two different
   answers to "may this user see this?"

2. **Authentication is OAuth against the product's own identity, and
   permissions are inherited rather than redefined.** Metabase and Superset
   state this explicitly as a non-goal: MCP grants nothing new. WrenAI is the
   cautionary version — it shipped a per-project URL with the token in the
   path, and is now deprecating it in favour of OAuth.

3. **The semantic layer is what is actually being sold.** Power BI's pitch is
   schema-aware query generation; Genie's is an ontology that resolves
   business terms; WrenAI's whole product is MDL. The MCP endpoint is the
   delivery mechanism. The meaning layer is the moat — and, per §2.6, it is a
   moat at least three competitors are already inside, so ours differentiates
   by how it is built and maintained rather than by existing.

4. **Raw SQL is usually a separately gated tool**, not folded into the general
   query tool, and often with its own kill switch. Metabase is the clearest
   case: `execute_sql` needs native-query permission and an administrator can
   disable it globally. WrenAI is the exception that shows why the pattern
   exists — `run_sql` sits beside `ask` and `generate_sql` as an ordinary
   tool, with row limits and dry-plan validation rather than a separate gate
   doing the containment.

---

# 3. Where DataMind Already Fits

Most of the work here is choosing, not building. The API surface is already
close to the right shape, and several existing design decisions turn out to
have been the right ones for a reason nobody had in mind at the time.

**The draft path writes nothing.** `POST /sql/drafts` takes a question and
returns a guarded statement with a fifty-row preview, and its own
documentation says it plainly: no conversation, no message, no run, no trace
if the editor is closed. That is an unusually clean fit for a stateless tool
call.

**The validate path calls no model.** `POST /sql/drafts/validate` guards and
previews a statement a human wrote. Exposed as a tool, it becomes "check this
before you run it" — an operation no other BI MCP server offers.

**Dashboards call no model at refresh time.** Reading a board's current
numbers works with the provider key revoked. It is the cheapest read in the
product.

**The guard has five entry points and none is privileged.** The chat
validation node, saved-SQL execution, tile save, dashboard import, and
knowledge templates. The hostile corpus is replayed through every one. An MCP
tool would be a sixth door, and the rule that no door is special settles the
design question before it is asked.

**Everything is scoped by owner.** Every service function filters on
`owner_id`, and there is no sharing model — dashboards and reports are
owner-only, deliberately, and connection sharing is unbuilt future work. This
is the single most consequential fact for the authentication decision in §5.1.

---

# 4. What Is Actually Different About Us

This section states the differentiation claim carefully, because the obvious
version of it is wrong and we would rather find that out here than in front of
a customer.

## 4.1 The guard: allowlist, not denylist

**The overstated claim would be** that no competitor validates the model's SQL
before running it. That is false, twice over. Superset parses statements with
SQLGlot and rejects mutating ones when a database has DML disallowed — the
same parser we use, and the same family of check. WrenAI dry-plans generated
SQL against its MDL layer before executing it, which is a real pre-execution
check and closer to our name resolution than to Superset's mutation scan.

**The real difference is allowlist versus denylist**, and it is a genuine one.

- A **denylist** parses the statement and looks for the bad things — drop,
  delete, insert, and so on. Anything not recognised as bad passes. This is
  Superset's approach, and it is what nearly every database MCP server on the
  market does.
- An **allowlist** parses the statement and walks every node, rejecting
  anything not explicitly permitted. This is our invariant: an unknown node
  type is a rejection, not a warning.

The gap between these is the difference between "I must have anticipated the
attack" and "the attacker must find something I anticipated."

A live example landed during this research. A widely used Postgres MCP server
had its safe mode bypassed by exactly this class of gap: its validator
inspected function calls appearing as one kind of syntax-tree node, but a
function placed in a `FROM` clause parses as a *different* node type — one the
validator permitted without checking. Agents could read arbitrary files from
the database server. Nobody wrote a bug; somebody wrote a denylist, and the
parser produced a shape they had not enumerated. Under an allowlist, an
unrecognised node is a rejection, and there is nothing to bypass.

Superset has a milder version of the same seam: when its parser fails, it
refuses the statement because it cannot confirm the query is read-only. That
is failing closed, which is right — but failing closed on a *parse error* is a
much smaller guarantee than failing closed on a construct that parsed
perfectly well and simply was not on the list.

Metabase is in a different category again: we found no statement-level parse
gate on native queries at all. Their published guidance is a read-only
warehouse role, plus the application's own native-query permission and the
global kill switch. That is containment without static validation.

**The honest one-paragraph version:**

> Superset parses with SQLGlot and rejects mutating statements. WrenAI
> dry-plans SQL against its semantic layer before running it. Metabase relies
> on a read-only role and role-based access control. None of them walks an
> allowlist, so each is secure against the attacks its authors enumerated.
> DataMind's guard is allowlist-shaped, which is secure against attacks nobody
> enumerated.

That is weaker than the overstated version, and it is still the strongest
security claim in the survey.

**Two layers, not one.** The layering matters as much as the parser choice.

| | validation | containment |
|---|---|---|
| Metabase | — | read-only role, access control, kill switch |
| Superset | SQLGlot mutation check | row-level security via shared security manager |
| WrenAI | dry-plan against the MDL layer | row limits, structured errors; row/column security (commercial tier) |
| Power BI | not applicable (generates DAX) | Fabric role-based access control |
| **DataMind** | **allowlist walk, names resolved against the snapshot** | **read-only transaction, statement timeout, row cap, role proven by attempting a write** |

Our principle of *containment underneath correctness* is that both layers
exist and neither is trusted to cover the other's failure. A parser gap meets
a read-only transaction. A misconfigured role meets an allowlist.

**Why this matters more for MCP than for chat.** An MCP server is a
prompt-injection amplifier. The agent driving our tools may be reading a web
page, a support ticket, or a document that contains an instruction to
exfiltrate data. Metabase's answer is that the role is read-only. Ours is that
the injected statement must *also* survive an allowlist walk, name resolution
against the connection's stored snapshot, the row cap, and the disclosure
policy on the way back out.

That last item has no equivalent anywhere in this survey. None of these
products has a per-connection policy governing how much result data may reach
a model.

## 4.2 Verified answers: no competitor has an equivalent

This claim is less exhaustively checked than the guard claim, so treat it as a
strong hypothesis rather than a proven negative. The shape of what the vendors
ship supports it.

Every BI MCP server surveyed answers a question by **generating** something at
ask time. Power BI generates DAX. Superset runs whatever SQL the agent wrote.
Metabase constructs a query. WrenAI generates SQL against MDL and dry-plans
it. Two products come close and neither arrives: Genie Ontology is a
*definitions* layer, the same category as our semantic layer, and WrenAI's
`queries.yml` is a version-controlled file of example queries. Both make
generation better informed. Neither is a store of question-and-answer pairs
that a human verified and that maintains itself — see the §2.6 comparison,
which is the closest thing to a counter-example and still falls short on all
three counts below.

Our knowledge store is a different artifact: a taught question with a
curator-approved statement behind it, matched above a threshold, with
parameters bound and a veto on any slot that will not bind. A tool over that
could return an answer whose SQL **a named person approved**, re-validated
against today's schema, with the matched question and bound parameters
returned as provenance.

Three things make this hard to copy, and all three already exist for other
reasons:

1. **The parameters come from the syntax tree.** We walk the tree the guard
   already produced and offer a slot for each literal we can classify,
   refusing the ones that are part of a definition rather than a question. No
   competitor does this, and only WrenAI is structurally positioned to — it
   plans statements against MDL, so it has a tree to walk, but it walks it to
   validate rather than to curate. Everyone else would have to build the
   parser first. The same asset pays twice.

2. **Staleness is a re-check, not a flag someone sets.** A template that no
   longer resolves withdraws itself. So a tool can promise "a human verified
   this, *and* it still parses against the current schema" — two separate
   claims, both mechanical.

3. **A hit lands on the guard's own entry point.** Stored SQL gets no
   exemption. So a verified-answer tool is trustworthy for the *same* reason
   the SQL tools are, rather than for a new reason we would have to argue
   separately.

The honest advertisement is: *answers this question the way a human already
approved, or tells you it cannot.* Against five products whose tools all mean
*an LLM will write something and we will check it is not a delete*, that is a
genuinely different surface.

One caveat. Few-shot injection currently ships **off** by default, gated on
held-out accuracy in the eval, because more prose in the prompt has been
measured to *lower* accuracy here. The short-circuit path and the few-shot
path are separate switches, but the discipline is the same: if we expose
verified answers over MCP, the override rate is the number that tells us
whether it is trusted, and it should be instrumented from day one.

---

# 5. The Decisions

Ordered by how early they bite. The first two are expensive to retrofit; the
rest can be revisited.

## 5.1 Authentication — the blocker

Every service function is scoped by owner, and there is **no sharing model**.
An MCP client therefore authenticates as *a user* and sees exactly that user's
connections, dashboards, and reports.

That is clean, and it matches what the market promises: permissions inherited,
never redefined. The open question is how the client proves who it is.

| Option | Effort | Trade-off |
|---|---|---|
| Existing bearer token, pasted into client config | none | tokens are short-lived; means refresh handling or repeated re-pasting |
| A long-lived personal access token | moderate | new table, new revocation story, new audit surface — but good ergonomics |
| Full OAuth 2.0, as Metabase does | high | the right end state, and what the protocol now expects of remote servers |

**Recommendation:** start with a personal access token, and design the tool
surface as though OAuth is coming. The cost of retrofitting authentication
into a tool registry is low; the cost of retrofitting it into a *deployed*
integration that customers have configured is not.

## 5.2 Does disclosure apply at the MCP boundary?

**This is the decision most worth making now**, because it is the one place
where an existing policy takes on a genuinely new meaning.

Our disclosure policy governs how much result data may reach **a model**. An
MCP client *is* a model — running somewhere we do not control, at a vendor the
customer did not necessarily choose.

Today a connection set to the narrowest policy still shows the user their own
result table in the browser, because the browser is the user. **An MCP client
is not the user.** That distinction does not exist anywhere in the code right
now.

The recommended answer is that disclosure applies, and applies at least as
strictly as it does to our own pipeline. The sensitive-column floor should
hold under every policy, as it does today. The rule governing when a
template's literals may be rendered already has the right shape for a
verified-answer tool.

Getting this wrong is the difference between "you decide what leaves your
database" being true and being marketing.

## 5.3 Local or remote?

Microsoft shipped both and split them by job. For us the split is cheaper than
it sounds, because it is mostly a transport decision over one tool registry.

- **Local (stdio, token in the environment)** is the fast path to something
  demonstrable, and sidesteps §5.1 for a first version.
- **Remote (HTTP, mounted inside the API the way Metabase mounts its
  endpoint)** is where the product eventually wants to be. We already have the
  multi-replica story, the cross-process event delivery, and the reconciler.
  It forces the authentication question immediately.

## 5.4 Read-only, or writes too?

The strongest opening position is **read, draft, and validate — no
persistence**. Almost everything in the core tool set already writes nothing.
It is a coherent and honest first version: *an agent can ask your data
anything and check any SQL, and cannot change your DataMind.*

If writes come later, Superset's preview-first default is the pattern to copy.

A specific caution on teaching: allowing an agent to create knowledge
templates is tempting and dangerous, because the store's entire value is that
a *human* verified each entry. If it is built, it must create a **proposal**,
never an active retrievable row — exactly what the existing backfill does.

## 5.5 How does asking a question work as a tool call?

This is the hard one, and it is specific to us. A chat run is streamed, takes
five to sixty seconds, and makes four or five sequential provider calls. MCP
tool calls are request and response.

Three ways out:

1. **Block until the run is terminal.** Simple; risks the client timing out on
   a slow hosted model.
2. **Return a run identifier immediately and add a polling tool.** Matches the
   existing report path, which already returns immediately and is polled. Two
   calls per question, but it never hangs.
3. **Leave asking out of the first version** and let drafting and validating
   cover the ground.

There is a product question underneath the mechanical one. An MCP `ask` means
**two models in series**: the client's agent, then our pipeline's. That is
fine, but it is a different offering from DataMind as a *guarded execution
surface* for an agent that already has its own model. The second is cheaper to
build and quite possibly more valuable.

One detail that must be designed rather than discovered: a run that ends by
asking a clarifying question is not in a terminal state. The tool has to
return that question and its options as data the agent can answer in a
follow-up call. This actually suits MCP better than it suits chat — but only
if it is handled deliberately. **WrenAI ships a `respond_clarification` tool
for exactly this**, which is both a confirmation that the problem is real and
a ready-made shape to copy: our `clarify` node already ends a run
`NEEDS_CLARIFICATION` with a `CLARIFICATION` artifact carrying the options, so
what is missing is the tool, not the mechanism.

## 5.6 Cost and rate limiting

An agent in a loop issues far more queries than a person. The per-connection
row cap and statement timeout are the containment, and the existing rule that
an override may only *lower* them should bind here too. Worth considering an
MCP-specific cap that is lower still, and a rate limit per token.

A forgotten agent polling a customer's production database is the MCP version
of the forgotten background tab — a problem the dashboard scheduler already
had to solve once.

## 5.7 The guard as a sixth door

Not really a decision. Any tool that executes SQL calls the guard, and the
hostile corpus gains a sixth replay. Our own rule — *the moment one door is
special, the guarantee is gone* — settles it.

Worth writing that test **first**, since it is the claim the entire offering
rests on.

## 5.8 Audit

Every curation write already records to the audit log. MCP actions should too,
with the client identified. Metabase specifically ships an authorization audit
log covering client registration and approve/deny decisions.

Given our positioning, an MCP surface with no audit trail would undercut the
thing we are selling. One detail: the actor address is read from a header a
proxy sets, and a local stdio server has no address at all — that field needs
a defined meaning rather than an empty string.

---

# 6. A Possible Shape

Not a plan, and not a commitment. This is what the research suggests a
sensible first version would look like, offered so the decisions above have
something concrete to attach to.

## 6.1 Candidate tools, by tier

**Tier 1 — the obvious core.** All read-only except the last.

| Tool | What it does | Writes? |
|---|---|---|
| `list_connections` | what the agent can point at | no |
| `describe_schema` | tables, meanings, grain, metrics, cautions | no |
| `draft_sql` | question in, guarded SQL plus preview out | no |
| `validate_sql` | guard a statement the agent wrote | no |
| `run_saved_query` | execute something already stored | no |
| `ask` | the full pipeline | yes — a conversation and a run |

**Tier 2 — the differentiated ones.**

| Tool | Why it is interesting |
|---|---|
| `verified_answer` | a human-approved answer with provenance; no competitor has this |
| `list_metrics` | how this business defines its terms, with the exact SQL |
| `get_dashboard` | current numbers; calls no model |
| `get_report` | a generated document and its figures |

**Tier 3 — think hard first.** Creating tiles and dashboards; teaching
templates.

**Excluded.** User management, provider configuration, and the audit log
itself. Metabase and Superset do not expose these either.

## 6.2 A candidate first version

Local transport, read-only, five tools: list connections, describe schema,
draft SQL, validate SQL, run a saved query. Authentication by pasted token. No
writes and no `ask`. Every SQL path through the guard, hostile corpus
replayed, disclosure applied at the boundary.

Small, honest, and it demonstrates the one thing nobody else has: an agent
that can propose SQL and get a **static verdict with a rule identifier**
before anything touches the database.

Verified answers and asking come second, once there is evidence of how agents
actually use it.

## 6.3 The two decisions to make now

Everything else can be revisited. These two are expensive to change later:

1. **Disclosure applies at the MCP boundary.** An agent is a model, not a
   user.
2. **Authentication is designed for OAuth**, even if the first version ships a
   token.

---

# 7. Sources

**Metabase**
- MCP server — https://www.metabase.com/docs/latest/ai/mcp
- Database users, roles, and privileges — https://www.metabase.com/docs/latest/databases/users-roles-privileges
- Writable connection — https://www.metabase.com/docs/latest/databases/writable-connection

**Apache Superset**
- MCP integration — https://superset.apache.org/developer-docs/extensions/mcp/
- MCP server deployment and authentication — https://superset.apache.org/admin-docs/configuration/mcp-server/
- SIP-187, the MCP service proposal — https://github.com/apache/superset/issues/35498
- Using SQLGlot for the DML check (PR #31024) — https://github.com/apache/superset/pull/31024
- Parser-dialect limitations — https://github.com/apache/superset/discussions/32141
- Behaviour when DML is disallowed — https://github.com/apache/superset/issues/20565
- Technical deep dive (Preset) — https://preset.io/blog/superset-mcp-service-deep-dive/

**Power BI and Fabric**
- What are the Power BI MCP servers — https://learn.microsoft.com/en-us/power-bi/developer/mcp/mcp-servers-overview
- Remote server, getting started — https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-get-started
- Fabric data agent as an MCP server — https://learn.microsoft.com/en-us/fabric/data-science/data-agent-mcp-server

**Databricks**
- Genie One MCP server — https://docs.databricks.com/aws/en/agents/mcp-tools/genie-mcp
- The next generation of Genie — https://www.databricks.com/blog/next-generation-databricks-genie

**WrenAI**
- Project and README (Canner/WrenAI) — https://github.com/Canner/WrenAI
- WrenAI MCP integration guide — https://docs.getwren.ai/cp/guide/integrations/wrenai-mcp
- MCP server repository — https://github.com/Canner/WrenAI-mcp
- Wren Engine API (dry-plan endpoints) — https://docs.getwren.ai/oss/wren_engine_api
- Reducing hallucinations in text-to-SQL — https://www.getwren.ai/post/reducing-hallucinations-in-text-to-sql-building-trust-and-accuracy-in-data-access

**Security background**
- Postgres MCP Pro safe-mode parser bypass — https://forkast.news/postgres-mcp-pros-safe-mode-was-supposed-to-block-dangerous-sql-a-parser-flaw-bypasses-it-entirely/
- Building safe MCP servers for PostgreSQL — https://blog.pamelafox.org/2026/08/building-safe-mcp-servers-for-your.html
- Model Context Protocol specification — https://modelcontextprotocol.io/specification/latest

---

# 8. Related Reading

- `docs/security.md` — the guard, disclosure, and why both layers exist
- `docs/learning-loop-plan.md` — the knowledge store behind verified answers
- `docs/mvp2-plan.md` §D1 — connection sharing, which §5.1 depends on
- `docs/architecture.md` — the ports-and-adapters seams
