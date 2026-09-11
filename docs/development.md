# Development

Everything you need to run, test and verify a change — the commands, the
environment's sharp edges, the demo databases, and what CI actually gates.

For *what to change*, start at [../CLAUDE.md](../CLAUDE.md). For installing
from scratch, the [quick start](../README.md#quick-start) is the short version.

---

## 1. Commands

```bash
make secrets   # copy .env.example to .env, then write a fresh AES key + JWT secret (run once)
make up        # build & start everything, in the FOREGROUND (docker compose up --build)
make down      # stop everything
make logs      # follow api logs

make test         # full backend suite (cd backend && pytest -q)
make guard        # the hostile SQL corpus alone — the hard CI gate
make lint         # ruff + the eight import-linter contracts
make authz-check  # prove no module decides access for itself
make fmt          # ruff format
make migrate      # alembic upgrade head
make fixtures     # rebuild + verify the sales fixtures (PG/MySQL/MSSQL) from clean
make db-repair    # recreate the empty PGDATA runtime dirs the studio drive strips
```

`make up` starts the app db, **all three** demo databases, the api and the web
container, and it does not detach — `docker compose up -d` is the backgrounded
form the README's quick start uses.

From `frontend/`: `npm run dev`, `npm run build` (`tsc -b && vite build`),
`npm run typecheck` (`tsc --noEmit`), `npm test`. **`npm run lint` does not
work** — the script exists but eslint is neither a devDependency nor
configured.

`npm test` runs **fifteen** suites with `node --experimental-strip-types`:
fourteen DOM-free logic modules under `src/components/` — `dashboard-schedule`,
`table-format`, `dashboard-document`, `palette`, `chat-format`,
`report-document`, `report-readiness`, `report-print`, `semantic-drift`,
`semantic-metrics`, `knowledge-template`, `thinking`, `knowledge-queue`,
`provider-params` — plus `scripts/permissions.test.ts`. They hold the logic
whose failures are quiet, and they are the *only* tested code in the frontend.
**One React import turns a suite into a thing that cannot run.**

The **eval harness is not in `make test`** — it calls a real provider and costs
money. `python -m app.eval.runner --suite sales_v1` from `backend/`, or
`backend/scripts/eval_run.sh` behind a rate-limiting provider. See
[reference/eval.md](reference/eval.md).

## 2. The verification loop, before you claim done

| Changed | Run |
| --- | --- |
| Backend | `make test` |
| `sqlguard/` or a connector | `make test` **and** `make guard` |
| Anything permission-shaped | `make authz-check` **and** `make test` |
| Frontend | `npm run typecheck` + `npm run build` + `npm test` |

The backend suite is ~1,790 tests plus 14 skips and takes well under a minute.
(It used to take three, because an unhandled exception inside an API test was
logged through structlog's **rich** console renderer, which walks every frame's
locals — one of which is a SQLAlchemy `Select`. `tests/conftest.py` now forces
JSON logs; a single failing API test used to cost over a minute of rendering
and made the suite look hung.)

Several past bugs only surfaced end-to-end via the API, not in the UI —
**actually exercise the path you changed.**

## 3. Four environment facts that read like "the tooling is missing"

- **node is not on `PATH`** in this environment — it is under
  `~/.nvm/versions/node/*/bin`. Export it and `npm ci` in `frontend/` (fast, the
  cache is warm), and all three frontend commands work. `frontend/node_modules`
  is gitignored, so installing on the host costs nothing.
- **…unless the compose stack has been up**, in which case host `npm ci` fails
  `EACCES`: the web container leaves `frontend/node_modules` behind as an empty
  root-owned mount point. Don't chase the permissions — run
  `docker exec datamind-web-1 sh -lc 'cd /app && npm run typecheck && npm run
  build && npm test'` against the same bind-mounted source instead.
- **pytest works as-is**; `alembic` is not installed in that env, and
  `core/config.py` reads `.env` **relative to the cwd**, so running alembic or
  uvicorn from `backend/` needs a copy of the root `.env` there.
- **`.data/db` is a real local database** with real dashboards and connections
  in it. Clean up anything an end-to-end script creates.

**Ports:** web `5173`, api `8000` (`/docs` for OpenAPI), app db `5432`, demo
`sales` db `5433`, Sakila `3307`, demo `aurora` db `5434`. On a remote host,
expose **only 5173**; the SPA calls the same-origin `/api/v1` and Vite proxies
it to `api:8000`.

If the app database ever fails to start with *"could not open directory
`pg_notify`"* — the Lightning Studio drive drops empty directories across
restarts — run `make db-repair`. It recreates the empty runtime scaffolding and
never touches data.

## 4. The three demo databases

All three start with the stack, across **two** engines. They are not
interchangeable and picking the wrong one wastes an afternoon:

| | engine | host port | tables | what it is for |
|---|---|---|---|---|
| `aurora` | PostgreSQL | `5434` | 13 | **the demo.** Clean, one obvious join path per question, `COMMENT ON` throughout |
| `sales` | PostgreSQL | `5433` | 42 | **the eval fixture.** Messy on purpose |
| `sakila` | MySQL | `3307` | 16 | the second engine |

- **`aurora`** (`backend/fixtures/demo_seed.sql` + `demo_comments.sql`) is a
  coffee chain over 24 months. Its cardinalities are **tuned to the constants in
  `app/charts/__init__.py`** — `product_categories` = 6 = `MAX_PIE_SLICES`,
  `channels` and `loyalty_tiers` = 4 ≤ `MAX_SERIES`, `stores` = 18, above
  `HORIZONTAL_BAR_FROM` and below `MAX_CATEGORY_MARKS` — so the obvious question
  yields an untrimmed chart. **If you change a chart budget, that tuning is a
  thing you can break**, and the seed's header comment is where the reasoning
  lives. Its schema estimate is ~6k against the 50k retrieve budget, so the
  whole snapshot always reaches the generator. `orders` is the single source of
  truth and `daily_store_metrics` is derived from it by aggregation, so asking
  the same question two ways reconciles — deliberately the opposite of `sales`'s
  `sales_daily_rollup` trap. (The seed header says "12 tables"; there are 13.)
- **`sales`** is the eval fixture and its messiness is the point — near-duplicate
  names, legacy cruft columns, soft-delete traps, a stale rollup that gives wrong
  answers. Do not "clean it up": an eval that never fails measures nothing.
  `sales_comments.sql` is the commented arm of the catalog-comments A/B.
- Only `db` and `sales` are in the api service's `depends_on`; `sakila` and
  `aurora` start alongside but the api does not wait on them.

**No engine but those two has a demo server.** The Oracle and SQL Server compose
services were **removed** (~2 GB of RAM each, rarely started), so a change to
`infra/connectors/oracle.py` or `mssql.py` **cannot be driven against a live
server from `make up`** — bring your own, or start one by hand. Their seeds
survive in `backend/fixtures/`: `sales_seed_mssql.sql` is the same 42-table
mirror as Postgres, and `oracle/` is the small four-table schema whose
**`COMMENT ON` metadata is the point** — the fixture that exercises catalog
comments end to end, whose `analytics_ro` deliberately holds no roles at all,
not even `CONNECT`. `make fixtures` is unaffected: `rebuild_fixtures.sh` starts
its own throwaway containers and never used the compose services.

## 5. What CI gates, and what it does not

`.github/workflows/ci.yml` runs, and **fails on**: ruff, the import-linter
contracts, `make authz-check`, the LiteLLM and LangGraph boundary greps, the
hostile SQL corpus, the full backend suite, and on the frontend `tsc --noEmit`
plus `vite build`.

Two things it does **not** gate, and both are worth knowing before trusting a
green tick:

- **`npm test`** is not in CI. The fifteen suites above run only where somebody
  runs them.
- **mypy** runs with `|| true`. Strict mode is configured but is being adopted
  module by module, so a green tick is not a type-clean tree.

`npm run lint` is a dead script — eslint is not a declared devDependency and
the repo carries no eslint config, so it fails wherever it is run. CI does not
call it.

`.github/workflows/eval-nightly.yml` is separate: it runs the golden suite
through the real pipeline against a hosted model nightly and fails on a
regression of more than 2 points from the committed baseline. Without the
`EVAL_API_KEY` secret the job no-ops, so forks never fail for lack of a key.

## 6. Configuration

The full table is in the [README](../README.md#configuration). Three things
that catch people:

- **`make secrets` generates `SECRET_BOX_KEY` and `JWT_SECRET`.** Losing
  `SECRET_BOX_KEY` means every stored credential must be re-entered.
- **`.env` is gitignored; `.env.example` is the tracked template** `make
  secrets` copies from. Editing `.env.example` changes what every fresh clone
  gets — including two values where the file and the code disagree on purpose
  or by accident; see [status.md § Known inconsistencies](status.md#7-known-inconsistencies-in-the-repo-itself).
- **`RUN_DEADLINE_SECONDS` and `LLM_REQUEST_TIMEOUT_SECONDS` default to 120 and
  60 in `core/config.py` and are raised to 300 and 120 by `docker-compose.yml`.**
  That is headroom for slow hosted models: a chat run makes four or five
  sequential provider calls, and against a flash model over a hosted gateway the
  whole run can land at 45–140s.

## 7. Git

- This sandbox has **no GitHub auth** — `git push` will fail; the user pushes
  from their own terminal. Commit locally; don't attempt to push.
- Commit or branch only when asked.
- Commit messages follow `type(scope): a declarative sentence` — lowercase, no
  trailing period.
