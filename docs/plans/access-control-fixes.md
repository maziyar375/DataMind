# Access control — what testing found, and the fixes

> **What this is.** A test pass over the access control that
> [user-management-and-access-control.md](user-management-and-access-control.md)
> built, the problems it found, and the plan that fixes them. The model itself
> (owner + grants + teams + roles, one `Authorizer`, the intersection rule) held
> up; what did not were the places that were supposed to *use* it.
>
> **How it was tested.** A behavioural probe, not a code read: the real app,
> real SQL on the unit-test SQLite schema, and ~60 resource routes called as
> five principals — owner, editor (`modify`), viewer (`select`), describe-only,
> stranger — plus each seeded role doing the job its description promises. The
> probe is kept as `backend/tests/unit/test_access_behaviour.py` so the
> findings below stay fixed.
>
> **Out of scope, on request:** sharing conversations. Nothing here adds it.
>
> **Written:** 2026-09-18.

---

## 1. What was found

Severity is what it costs a user, not how hard it is to fix.

### Server — the rules

| # | Problem | Evidence | Sev |
|:--:|---|---|:--:|
| S1 | **The five `*.create` capabilities are never checked.** `dashboard.create`, `report.create`, `connection.create`, `llm_config.create`, `conversation.create` exist on roles and in the Roles tab, and the plan's route table requires them — but no route asks. | A principal holding only `team.read` (the Viewer role: *"Creates nothing"*) got **201** on `POST /dashboards`, `/connections` and `/conversations`. | High |
| S2 | **The unsaved "Test connection" / "Test model" probes need nothing at all.** Any signed-in user can make the server open a socket to any host, or send a request to any `base_url`. With an id *and* a typed password, the connection probe skips its `modify` check too. | Read in `connections.py:test_draft_connection`, `llm_configs.py:test_draft_config`. | High |
| S3 | **Report blocks' *Check* and *Edit SQL* always crash.** Both pass `owner_id=` to `draft_sql`/`validate_sql`, which take `ctx`/`authz` since Phase 2. | `TypeError … unexpected keyword argument 'owner_id'` → **500** for the owner. | High |
| S4 | **An editor of a shared report cannot run it, and is told the wrong thing.** The run needs `select` on the report's model; the refusal is **404 "Model configuration not found."** about a model they never asked about. | `modify` holder → `POST /reports/{id}/runs` → 404. | Med |
| S5 | **A dashboard editor cannot even rename a tile** whose data source they cannot read: every tile edit re-checks the connection, and says **404 "Connection not found."** | `modify` on the board only → `PATCH …/tiles/{id} {"title": …}` → 404. | Med |
| S6 | **Tile SQL reaches viewers who may not read its data source.** `GET /dashboards/{id}` and `/export` return every tile's statement; that is the table and column names the connection's own `describe` meaning withholds (*"…but not its schema"*). | Viewer with no access to the connection: `tile_sql=['SELECT 1']`. | Med |
| S7 | **Lists and pickers show things you cannot use.** Dashboard and report lists are `describe`-scoped, so the BI Engineer and Auditor roles see every board in the org and get a 403 on each; their summaries carry no privileges, so the UI cannot tell. Chat, the tile editor and the new-report form offer describe-only databases, and `?purpose=chat` offers describe-only models. | BI Engineer: `LIST dashboards → 1`, `open → 403`. | Med |
| S8 | **Only administrators can share with a person.** The share and transfer dialogs list people from `GET /users`, which needs `user.read`. A Normal User — the persona that owns dashboards — sees only teams, and an empty transfer list. | Normal User → `GET /users` → **403**. | High |
| S9 | **Model configurations offer three share levels the server always refuses.** `/actions` returns all five meanings; `modify`/`delete`/`manage` on an `llm_config` are 422. The code comments claim the picker shows two. | `grant modify on llm → 422`. | Low |
| S10 | **`test_every_mutating_route_is_guarded` cannot fail.** It counts a route as guarded if its source contains `"Dep"` — which `ctx: CtxDep` always does. It is why S1 and S2 shipped green. | Read. | Med |
| S11 | Small: `PATCH`/`POST /connections` return `privileges: []`; the read-only access panel never names the owner (it only loads grants for `manage` holders). | Read + probe. | Low |
| S12 | **Data shared with a *team* was refused at execution.** Chat runs and report runs are authorized in the background through `RequestContext.on_behalf_of`, which carried **no teams** — so access that passed every interactive check (the request context has the teams) was refused when the run executed. Sharing to a team is the model's recommended way to share. The dashboard share-check had the same gap and warned about data a team member could in fact read. | Read in `run_service.py`, `report_graph.py`; `on_behalf_of(user)` → `team_ids = ∅`. | High |
| S13 | **Anyone holding a run id could cancel somebody else's chat answer.** `POST /runs/{id}/cancel` stopped the run in the executor *before* asking whose it was, then answered 404. | Read in `conversations.py:cancel_run`. | Med |
| S14 | **Query access to a data source showed tabs that said it did not exist.** Its Semantic and Knowledge tabs answered 404 "Connection not found." — they are separate grants — while the connection's own `select` meaning promised *"…and its semantic layer"*. | `select` on the connection only → `GET …/semantic` → 404. | Med |
| S15 | Small: the Knowledge header printed `null:null/null` for a curator who holds only `describe` on the data source; the embeddings switch (needs *full access* on the store) was offered to curators (`modify`) and refused; the semantic layer's on/off switch (a *data source* field) was offered to layer curators who cannot edit the data source. | Probe + read. | Low |

### Interface — the affordances

The server refuses correctly in every case above except S1/S2. The interface
does not ask it first, so a person with **view** access is offered **edit**.

| # | Surface | What a view-only person is offered | Sev |
|:--:|---|---|:--:|
| U1 | **Dashboards** | Inline rename and description, Edit grid, Settings, Add tile, every tile's Edit / Duplicate / Delete, drag and resize; on the index, Rename / Duplicate / Archive / Delete / Share. `DashboardRead.privileges` is fetched and used only for a badge. | High |
| U2 | **Reports** | The whole editor: outline, sections, blocks, SQL, Generate. `ReportRead.privileges` is used only for a badge. | High |
| U3 | **Data sources** | Save, Test, Re-sync, Delete and every field. Only the disclosure dropdown is gated. | Med |
| U4 | **Model providers** | A fully editable form and Delete on a model shared for *use*. `LlmConfigRead` carries no privileges at all. | Med |
| U5 | **Knowledge, Semantic** | Create / edit / archive templates, resolve reviews, embedding settings; edit / publish / restore the layer. | Med |
| U6 | **Create buttons** | New dashboard, New report, Add data source, Add a model — to roles without the capability. | Med |

### Interface — the access UI itself

| # | Problem |
|:--:|---|
| X1 | **Five technical levels.** The share dialog is five radio cards of dense sentences (`describe`, `select`, `modify`, `delete`, `manage`); row dropdowns show the raw words. On a dashboard, `describe` produces a card the person cannot open. People think *view / edit / full access*. |
| X2 | **Dashboard access and data access are different — and the UI only warns.** The share dialog says *"X cannot read Sales. Share the data source too"* and gives no way to do it; the viewer's placeholder says *ask for “select”*. |
| X3 | **Lecturing copy** in the places people act ("Prefer a team. … a row nobody can attribute a year from now."), and a header badge that says **Limited**. |

---

## 2. The plan

### Server

1. **Enforce creation** (S1, S2). `needs(...)` on `POST /dashboards`, `/dashboards/import`, `/reports`, `/connections`, `/llm-configs`, `/conversations`. The unsaved probes: with an id, `modify` on that row, always; without one, the create capability.
2. **Fix the two crashes** (S3): pass `ctx` and `authz`.
3. **Say the real reason** (S4, S5). A report's bound data source and model, and a tile's own data source, are already on screen for anyone who can open them, so a refusal names them — a **403 with a sentence**, audited like every 403. A tile edit that does not touch the query (title, chart, table, refresh, layout) no longer re-checks the data source at all.
4. **Withhold tile SQL** (S6) from a reader who cannot `select` the tile's connection, in the read and in the export. The tile says it is restricted; the question and title stay.
5. **Honest lists and pickers** (S7). Dashboard and report summaries carry `privileges`; `LlmConfigRead` gains `privileges` and `owner`; a model list asked for a *purpose* is `select`-scoped.
6. **A people directory** (S8): `GET /directory` — active people and service accounts (id, name, kind — **no email**, the rule `owner_names` already follows) and teams — for any signed-in user, because choosing who to share with is part of owning something.
7. **Share levels come from the server** (S9, X1): `/actions` returns `levels` — per type, which privileges a person is offered and a short label for each (*Can view / Can edit / Full access*), beside the existing meanings. `llm_config` offers *Can use* only. `/actions` also names the owner (S11).
8. **A conformance test that can fail** (S10): the probe's shape — every mutating route, called for real by a principal with no grants and no capabilities, with a valid body generated from the route's own model, never gets a 2xx (and never a 5xx that would hide the answer). The structural test stops accepting a bare `"Dep"`.
9. **Background work carries the person's teams** (S12): `team_service.delegated_context(db, user)` resolves them the same way `get_ctx` does; chat-run execution, report-run execution and the share-check use it.
10. **Authorize, then cancel** (S13).
11. **Say only what is true** (S14, S15): the connection's `select` meaning drops the semantic-layer claim; the UI hides a derived tab the reader cannot open, and offers each switch only to whoever may flip it.

### Interface

12. **One helper, `accessOf(privileges)`** → `{ view, edit, delete, share }`, mirroring the server's `CAN` map, and every surface in U1–U6 renders from it. View-only screens are *read-only screens*, not screens full of disabled controls.
13. **One header chip** for artifacts you do not own: *View only · Sara's* / *Can edit · Sara's*, replacing *Read-only* / *Limited*.
14. **Share dialog, simplified** (X1–X3): people and teams in one searchable list from the directory; a three-way level picker from `levels`; for a dashboard or report, when the person would see placeholders **and you can share those data sources**, one checkbox — *Also let them view the data in Sales warehouse* — that makes both grants. Rows show the friendly label.
15. **Placeholder copy**: *"This tile uses Sales warehouse, which hasn't been shared with you."* — no privilege jargon.

### Not in this plan

Conversation sharing (on request). Access requests with approval. Changing the
five-rung model on the server — the UI simplifies it; the audit log and the API
still speak it.

---

## 3. Ledger

- [x] 2026-09-18 — test pass, findings §1.
- [x] 2026-09-18 — server fixes 1–11. `test_access_behaviour.py` holds every one; backend suite green.
- [x] 2026-09-18 — interface 12–15, checked in the browser in both themes against fixture principals (viewer, editor, owner, Viewer role).
