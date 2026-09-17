# DataMind against five products — a capability matrix

> **Subject:** the same exercise as [mvp2.md §2.6](../plans/mvp2.md#26-the-matrix),
> re-run with a different field. mvp2 compared DataMind to Microsoft Data
> Formulator, Wren AI, Databricks AI/BI Genie and Power BI. This page drops
> Data Formulator (a research prototype, not something anyone buys instead of
> DataMind) and adds the **two open-source BI platforms a self-hosting buyer
> actually shortlists against us: Apache Superset and Metabase.**
>
> **Scope:** Power BI (+ Fabric Copilot) · Wren AI · Databricks AI/BI Genie ·
> Apache Superset · Metabase.
>
> **Desk research date:** 2026-09-17. Competitor claims are sourced in §1 and
> §4; where the only source is a vendor page rather than product documentation,
> this page says so. **Anything not verifiable from a public document is marked
> `?`, not guessed.**
>
> **The DataMind column is against the tree on 2026-09-17**, not against
> mvp2's August reading — nine of its rows have moved since, and §3 lists which.
> Where this page and [../status.md](../status.md) disagree, status.md is right.
>
> **Read as argument, never as a description of this codebase** — that is
> [`reference/`](../reference/)'s job.

---

## 0. The one-paragraph answer

Against the two open-source incumbents, DataMind is **narrower and deeper**.
Superset and Metabase are mature BI platforms — forty-plus chart types,
drill-down, subscriptions, row-level security, dozens of connectors — that have
bolted a language model on top of a product designed for people who already
know what a dashboard is. DataMind is a language-model product that has grown the
governance a BI platform needs. The consequence is visible in the matrix as two
almost disjoint `○` columns: **they lack everything in "accuracy & trust"; we
lack everything in "reach and delivery".** Against Genie and Wren AI — products
built from the same starting point as ours — the gap has narrowed sharply since
August, and what remains of it is mostly reach, not trust.

---

## 1. The two that are new, and one Power BI row

Power BI, Wren AI and Genie are profiled in [mvp2.md §2.1–2.4](../plans/mvp2.md#part-2--what-the-other-four-do)
and nothing in this section supersedes that. What follows is the two additions,
plus the one thing about Power BI that matters for a Superset/Metabase
comparison.

### 1.1 Apache Superset

**The important fact first: Superset has no native text-to-SQL.** The official
documentation offers AI only through a **Model Context Protocol companion
service** (Superset 5.0+, admin-deployed), which lets Claude or ChatGPT list
datasets, run SQL under the caller's RBAC, create virtual datasets, build charts
through a preview-first Explore link, and assemble dashboards. The Superset
maintainers' stated direction is explicitly to keep AI **out of core** and in
extensions; the text-to-SQL work that exists lives in Preset's commercial *AI
Assist* and in community forks, not in the Apache project. [SIP-166](https://github.com/apache/superset/issues/33215) proposes
an in-core assistant and has not landed.

What Superset *is* strong at is the half DataMind does not have:

- **Datasets with metrics and calculated columns** — a semantic layer of a sort,
  though it is per-dataset SQL expressions rather than a reviewed business
  model, with **certification badges** on datasets and charts.
- **Explore** — drag-and-drop encoding, ad-hoc metrics, drill-to-detail and
  drill-by, cross-filtering, and 40+ pre-installed visualization types.
- **Native row-level security**, applied as a WHERE clause per role.
- **Alerts and reports** — threshold alerts and scheduled email/Slack delivery,
  in core, free.
- **Forecasting** — Prophet-based predictive analytics in the Advanced
  Analytics panel, which is the one piece of "analysis depth" a free BI tool
  rarely has. Two caveats: the `prophet` package is an optional install, and
  forecasting works **only on the ECharts time-series chart**.
- **Reach** — any SQLAlchemy-supported database (dozens), CSV/Excel upload, a
  full REST API, OAuth/LDAP/OIDC through Flask-AppBuilder.

**The honest read for us:** Superset out-reaches DataMind everywhere and has
nothing at all in the accuracy-and-trust column — no verified pairs, no
benchmarks, no review loop, no trusted badge on an *answer*, because there are
no generated answers in core to badge.

### 1.2 Metabase

The most dangerous of the five for DataMind's positioning, because it is the one
that has moved most and is aimed at the same buyer: a team that wants
self-hosted, plain-language analytics without a data engineer.

- **Metabot shipped in Metabase 60, April 2026.** It answers in natural
  language, builds query-builder charts, generates and *fixes* SQL in the native
  editor, analyzes an existing visualization, creates transforms, and runs
  **inside Slack**. The same release added an **Agent API** and an **MCP
  server**. Core AI is on **all plans**; semantic search is Pro/Enterprise.
- **It is grounded in a real semantic layer** — models (curated tables), metrics
  (reusable definitions), transforms, plus a **glossary** of business terms —
  and Metabase's own framing is that Metabot "queries your defined logic"
  instead of guessing. That is our §1.3 argument, shipped.
- **Provider-agnostic and permissioned.** Bring your own Claude or GPT provider;
  admins control *which groups* may use AI at all, and may scope AI to selected
  collections.
- **The limits are documented, and they are real.** Metabot cannot generate SQL
  with parameters, cannot manage alerts or subscriptions, cannot change
  visualization formatting, and searches only the first 100 tables unless
  scoped. Feedback is a thumbs-up/down — **it does not become training data, a
  verified pair, or a score.**
- **Platform strengths:** best-in-class drill-through, sandboxing (row- and
  column-level, paid), verified/official content, subscriptions and alerts,
  embedding as a first-class paid product, 18 official connectors (11 more
  from the community), file upload.

**The honest read for us:** Metabase now matches DataMind on the *shape* of the
semantic layer and beats us comprehensively on reach, delivery and interaction.
It does not have — and as far as public documentation shows, has not started —
**verified question→SQL pairs, an in-product benchmark, a curation queue, or a
guard that proves the generated statement is read-only before it runs.**

### 1.3 Power BI, for this comparison

One row matters here that did not in mvp2: Power BI is the only one of the five
with **alerts that respect row-level security** and **subscriptions delivered
under the recipient's own data scope**. When a procurement conversation asks
"can you email this to forty people and have each see only their region?", that
is the feature being asked about, and Superset and Metabase answer it too.
DataMind currently answers "no" three times over — no schedule, no alert, no
RLS.

---

## 2. The matrix

`●` present · `◐` partial · `○` absent · `?` not verifiable from public docs

### Accuracy and trust

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Semantic layer / metrics | ● *typed, versioned* | ● model + AI instr. | ● MDL | ● measures/filters | ◐ *dataset metrics* | ● models + metrics |
| Semantic layer is reviewed & versioned | ● *draft→publish, §4.3* | ◐ | ● version-controlled | ◐ | ○ | ◐ |
| Verified/example Q→SQL pairs | ● *parameterized* | ◐ | ● `queries.yml` | ● | ○ | ○ |
| Slots proposed from the parsed AST | ● **unique** | ○ | ○ | ○ *(hand-written `:param`)* | ○ | ○ |
| "Trusted / verified" badge on an answer | ● *3 tiers* | ● approved-for-Copilot | ◐ | ● | ○ *(certified **datasets**)* | ◐ *(verified **content**)* |
| Free-text business instructions | ◐ *layer only* | ● | ● `instructions.md` | ● | ○ | ◐ *glossary* |
| Entity / value matching | ○ *deferred, B3* | ◐ | ● profiling | ● | ○ | ◐ *field remapping* |
| In-product benchmarks + a score | ● *per connection* | ○ | ● eval runner | ● Evaluations tab | ○ | ○ |
| User→curator review workflow | ● *3 verdicts, queue* | ◐ | ◐ | ● Ask for Review | ○ | ◐ *thumbs only* |
| A correction becomes stored knowledge | ● *`became_template`* | ◐ | ● | ● knowledge mining | ○ | ○ |
| Metric-use attribution on an answer | ● **unique** | ○ | ○ | ○ | ○ | ○ |

### SQL safety

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Static AST validation, fail-closed | ● **best in class** | n/a | ● dry-plan | ◐ | ○ | ○ |
| Read-only proven at the engine | ● | n/a | ◐ | ◐ | ◐ *per-DB DML toggle* | ◐ *DB privileges* |
| Explicit disclosure policy to the LLM | ● **unique** | ○ | ○ | ○ | ○ | ○ |
| Generated SQL shown to the reader | ● | ◐ | ● | ● | ● | ● |
| Execution gated on a human approving the SQL | ○ | ○ | ○ | ○ | ◐ *MCP preview-first* | ○ |

### Interaction

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Conversational chat over data | ● | ● | ● | ● | ◐ *MCP client only* | ● Metabot |
| Chat in Slack / Teams | ○ | ● | ◐ | ● | ○ | ● |
| Branching / non-linear threads | ○ | ○ | ◐ | ○ | ○ | ○ |
| Drag-and-drop encoding | ○ | ● | ○ | ○ | ● Explore | ● query builder |
| AI-derived fields not in the data | ○ | ● DAX | ◐ | ◐ | ○ | ◐ *expressions* |
| Direct manipulation of a result | ◐ *chart type* | ● | ◐ | ◐ | ● | ● |
| Drill-down / drill-through / cross-filter | ○ | ● | ◐ | ◐ | ● | ● **strong** |
| Chart → dashboard in one click | ● *chat bridge* | ● | ● | ● | ● | ● |
| Chart types | 8 | 30+ | ~10 | ~10 | 40+ | ~20 |

### Analysis depth

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Multi-step / iterative agent | ○ | ◐ | ● sandboxed | ● | ○ | ◐ *agentic workflows* |
| Compute beyond SQL | ○ | ● DAX/Python | ● | ◐ | ◐ *Jinja* | ◐ |
| Root-cause / key drivers | ○ | ● | ◐ | ◐ | ○ | ○ |
| Forecasting / anomaly detection | ○ | ● | ○ | ◐ | ◐ *Prophet, time-series only* | ◐ *trend lines* |

### Documents and delivery

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Narrative report generation | ● **strong** | ◐ | ◐ | ○ | ○ | ○ |
| Human-approved outline gate | ● **unique** | ○ | ○ | ○ | ○ | ○ |
| Figures computed, never generated | ● **unique** | n/a | ○ | ○ | n/a | n/a |
| Scheduled generation / delivery | ○ | ● | ◐ | ◐ | ● | ● subscriptions |
| Alerts on thresholds | ○ | ● | ○ | ◐ | ● | ● |
| Delivery respects the recipient's scope | ○ | ● | ◐ | ◐ | ◐ | ● |

### Collaboration and governance

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Share a dashboard / report | ● | ● | ● | ● | ● | ● |
| Teams / groups / roles | ● *8 resource types* | ● | ● | ● | ● | ● |
| Row-level security | ○ *needs filters first* | ● | ● *(paid)* | ● | ● *in core* | ● *sandboxing, paid* |
| Column masking | ○ | ● | ◐ | ● | ○ | ● *(paid)* |
| Audit log of every authz event | ● | ● | ● *(paid)* | ● | ◐ *action log* | ● *(paid)* |
| Access review surface | ● | ● | ◐ | ◐ | ○ | ◐ |

### Reach

| Capability | DataMind | Power BI | Wren AI | Genie | Superset | Metabase |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Data sources | 4 | many | 22+ | Databricks | dozens *(any SQLAlchemy)* | 18 + 11 community |
| File upload (CSV/Excel) | ○ | ● | ● | ○ | ● | ● |
| Result export | ◐ *CSV, no Excel* | ● | ● | ● | ● | ● |
| Public API | ○ | ● | ● | ● | ● | ● |
| MCP server | ○ | ◐ | ● | ● | ● *5.0+* | ● *v60* |
| Embedded analytics | ○ | ● | ● | ● | ◐ | ● *(paid)* |
| SSO / OIDC / SAML | ○ *seams built* | ● | ● | ● | ● | ● *(paid)* |
| Self-hostable | ● | ○ | ● | ○ | ● | ● |
| Licence | PolyForm **Noncommercial** | proprietary | Apache-2.0 core | proprietary | Apache-2.0 | AGPL core + paid editions |
| Provider-agnostic LLM | ● | ○ | ● | ○ | ● *(BYO client)* | ● |

---

## 3. What moved since mvp2 §2.6

Nine DataMind rows changed between the August matrix and this one, and every one
of them is a landed strand in [../status.md](../status.md) §2 — not a
re-reading of the same code.

| Row | August | Now | Landed by |
| --- | :--: | :--: | --- |
| Semantic layer / metrics | ◐ *a blob* | ● | [plans/semantic-layer-model.md](../plans/semantic-layer-model.md) Phases 0–5 |
| Verified Q→SQL pairs | ○ | ● | the learning loop, 2026-09-01 |
| Trusted badge on an answer | ○ | ● *3 tiers* | [reference/knowledge-templates.md](../reference/knowledge-templates.md) §2 |
| In-product benchmarks + score | ○ *dev CLI* | ● | knowledge-templates §6, and *Score this draft* |
| User→curator review workflow | ○ | ● | knowledge-templates §3 |
| Learns from a correction | ○ | ● | `answer_feedback.became_template` |
| Chart → dashboard in one click | ○ | ● | the Chat→Dashboard/Report bridge, 2026-09-03 |
| Share / teams / audit | ○ ○ ○ | ● ● ● | [reference/access-control.md](../reference/access-control.md) |
| Result export | ○ | ◐ | CSV on answers, tiles and report figures |

Two rows are new in this matrix and both are DataMind-only: **AST-proposed
parameter slots** and **metric-use attribution on an answer**. Neither is a
capability anyone else appears to have shipped, and both fall out of having a
guard that already parses the statement — which is why they are cheap here and
expensive everywhere else.

**Two caveats on our own `●`s, so nobody quotes them too hard.** Few-shot
injection and the embedding matcher are **built and shipped off**, both waiting
on a provider key rather than on code, and the layer-on/layer-off accuracy
baselines have not been run. Execution accuracy is still **0.36** on the messy
`sales` fixture at `PROMPT_VERSION` v2 — model- and version-specific, and read
[../status.md](../status.md) §6 before repeating it. **No competitor in this
table publishes a comparable number at all**, which cuts both ways: we cannot
claim to beat them, and they cannot claim to beat us.

---

## 4. What this implies

**1. The trust column is the position, and it has widened, not narrowed.**
In August, four DataMind rows were emphasised `●` and all four were "never trust
the model". There are now seven, and three of the new ones (verified pairs, the
benchmark, the review loop) are features Genie has and *Superset and Metabase do
not*. Against the two open-source incumbents this is the entire differentiation
and it should be the entire pitch.

**2. The gap against Genie and Wren AI is now mostly reach.** Every row where
they still lead us is a connector, an API, an MCP server, a file upload or a
sandboxed multi-step agent. None of it requires changing anything about how
DataMind treats the model. mvp2's Tier 2 already names four of these (E1 file
upload, E2 MCP + REST, C2 data threads, C4 dashboard filters).

**3. The gap against Superset and Metabase is table stakes, and it has a
critical path.** Three rows lose procurement conversations on their own —
**row-level security, scheduled delivery, and threshold alerts** — and all three
are downstream of one deferred item. RLS "needs dashboard filters first"
([status.md](../status.md) §5), dashboard filters need bound parameters in
`QueryExecutor.execute`, and scheduled delivery needs sharing (done) plus a
schedule. **Dashboard filters (C4) unblock more of this matrix than any other
single item**, which is what mvp2 already says about it and is now true twice
over.

**4. One row is worth taking seriously as a threat rather than a gap.**
Metabase grounding Metabot in models, metrics and a glossary is DataMind's §1.3
thesis arriving in a product with twenty-nine connectors, an MCP server, an
Agent API and an existing install base. What they have not built is the part
that is hard: proving the statement is safe before it runs, and turning a wrong
answer into stored, parameterized, benchmarked knowledge. That is the moat, and
it is only a moat while we keep measuring it — which is the argument for the
three unrun eval baselines in [status.md](../status.md) §3 being the most
valuable unstarted work in the repo.

**5. The licence is an asymmetry worth naming before someone else names it.**
Superset is Apache-2.0 and Metabase's core is AGPL: both are free to run
commercially, forever. DataMind is **PolyForm Noncommercial 1.0.0** — source
you can read and self-host, but **commercial use requires a paid licence**.
Against Wren AI (Apache-2.0 core, paid for RLS, access control and embedding)
that is a normal open-core position; against Superset and Metabase it means the
trust column has to be worth paying for, since the alternative is free. This
page does not argue the licence either way. It argues that the pitch cannot
rest on "open source and self-hosted", because two of the five match that and
cost nothing.

---

## 5. Sources

**Apache Superset**
- [Using AI with Superset (official docs)](https://superset.apache.org/user-docs/using-superset/using-ai-with-superset/) — the MCP companion service, its tools, and the absence of native text-to-SQL
- [SIP-166: AI Assistant](https://github.com/apache/superset/issues/33215) — the proposal, not landed
- [Enabling AI NLP feature in Superset (discussion #39274)](https://github.com/apache/superset/discussions/39274) — maintainers' stated "extensions, not core" direction
- [Building Preset AI Assist (Preset blog)](https://preset.io/blog/building-preset-ai-assist-how-we-brought-text-to-sql-into-apache-superset/) — **vendor blog**; text-to-SQL as a commercial layer above Apache Superset
- [Row Level Security API (official docs)](https://superset.apache.org/developer-docs/api/row-level-security/) — RLS as a core feature, a WHERE clause per role
- [Time series forecasting (Preset blog)](https://preset.io/blog/time-series-forecasting-a-complete-guide/) — **vendor blog**; the source for the two Prophet caveats: optional package, ECharts time-series charts only

**Metabase**
- [Metabot (official docs)](https://www.metabase.com/docs/latest/ai/metabot) — capabilities *and* the documented limitations quoted in §1.2
- [AI in Metabase (official docs)](https://www.metabase.com/docs/latest/ai/start) — provider choice, per-group permissions
- [Semantic layer / models (product page)](https://www.metabase.com/features/models) — **vendor page**
- [Metabase AI (product page)](https://www.metabase.com/features/metabase-ai) — **vendor page**
- [Metabase 60 release notes](https://www.metabase.com/releases/metabase-60) — the source for the April 2026 date, the Agent API, the MCP server, and which plans carry which AI feature
- [MCP server (official docs)](https://www.metabase.com/docs/latest/ai/mcp)
- [Data sources](https://www.metabase.com/data-sources) — **vendor page**; 18 official drivers plus 11 community

**Power BI, Wren AI, Genie** — sourced in [mvp2.md §2.2–2.4](../plans/mvp2.md#22-wren-ai);
nothing here re-researches them beyond §1.3.

**DataMind** — [../status.md](../status.md) (2026-09-17), [../../LICENSE](../../LICENSE),
[reference/knowledge-templates.md](../reference/knowledge-templates.md),
[reference/access-control.md](../reference/access-control.md),
[reference/charts.md](../reference/charts.md) §2,
[reference/security.md](../reference/security.md),
[plans/semantic-layer-model.md](../plans/semantic-layer-model.md).
