.PHONY: help secrets up down logs test guard lint authz-check fmt migrate fixtures db-repair

help:
	@echo "make secrets   Generate .env with fresh keys"
	@echo "make up        Start the full stack"
	@echo "make down      Stop everything"
	@echo "make test      Run the backend test suite"
	@echo "make guard     Run the hostile SQL corpus only"
	@echo "make lint      Ruff + architecture contracts"
	@echo "make authz-check  Prove no module decides access for itself"
	@echo "make fixtures  Rebuild + verify the sales fixtures (PG/MySQL/MSSQL) from clean"
	@echo "make db-repair Recreate empty PGDATA runtime dirs the studio drive strips, then start db"

secrets:
	@test -f .env || cp .env.example .env
	@python3 -c "import os,base64,re,pathlib; \
p=pathlib.Path('.env'); t=p.read_text(); \
t=re.sub(r'^SECRET_BOX_KEY=.*$$','SECRET_BOX_KEY='+base64.urlsafe_b64encode(os.urandom(32)).decode(),t,flags=re.M); \
p.write_text(t)"
	@python3 -c "import secrets,re,pathlib; \
p=pathlib.Path('.env'); t=p.read_text(); \
t=re.sub(r'^JWT_SECRET=.*$$','JWT_SECRET='+secrets.token_urlsafe(48),t,flags=re.M); \
p.write_text(t)"
	@echo "Wrote .env with fresh keys."

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f api

test:
	cd backend && pytest -q

guard:
	cd backend && pytest tests/unit/test_sqlguard_hostile.py -v

lint:
	cd backend && ruff check app tests && lint-imports

# The authorization gate. Four greps, and each one names a way of deciding
# access that this codebase has agreed not to use — see
# docs/user-management-and-access-control-plan.md §18.4, which says there are
# three enforcement shapes and no fourth.
#
#   1. A bare ownership comparison in a route or a service. Ownership is a
#      *fact on a row*; whether it grants anything is the authorizer's answer,
#      and a service that decides for itself cannot later be shared.
#   2. `ctx.is_admin`. A role string standing in for a permission is exactly
#      what requirement 7 asks to be removed, and it is what makes "extensive
#      permissions over Knowledge without admin over everything" impossible.
#   3. `role == "ADMIN"` anywhere, backend or frontend. The UI renders
#      affordances from `GET /me/permissions` and `GET /{resource}/{id}/actions`,
#      never from a role it guessed.
#   4. A worker constructing a context out of nothing. There is no god
#      context: background work runs *as* a principal, through
#      `RequestContext.on_behalf_of`, or it is doing something the model does
#      not cover.
#
# **It fails today, on purpose.** It is added to CI as non-blocking in Phase 0
# and flips to blocking at the end of Phase 2, when the last of those lines is
# gone. A gate that only ever passed would have told nobody anything.
authz-check:
	@fail=0; 	run() { 	  echo "── $$1"; 	  shift; 	  if grep -rnE "$$@" 2>/dev/null; then fail=1; else echo "   clean"; fi; 	}; 	run "a service or route deciding ownership for itself" 	    "owner_id[[:space:]]*[!=]=" backend/app/api backend/app/services; 	run "a role string standing in for a permission" 	    "\.is_admin" backend/app/api backend/app/services backend/app/workers; 	run "an ADMIN literal compared anywhere" 	    "role == ['\"]ADMIN" backend/app frontend/src; 	run "a worker acting as nobody" 	    "ctx=None" backend/app/workers; 	if [ $$fail -ne 0 ]; then 	  echo; 	  echo "authz-check: the lines above decide access outside the authorizer."; 	  echo "Non-blocking until Phase 2 of docs/user-management-and-access-control-plan.md."; 	  exit 1; 	fi; 	echo "authz-check: clean."

fmt:
	cd backend && ruff format app tests

migrate:
	cd backend && alembic upgrade head

# Rebuild the demo/eval fixtures from clean and prove each dialect loads, has
# the expected 42 tables, and clears the retrieve-node budget. Rebuilds the
# Compose Postgres demo unless SKIP_DEMO=1; ONLY=pg|mysql|mssql narrows it.
fixtures:
	bash backend/fixtures/rebuild_fixtures.sh

# Escape hatch if the app DB ever fails to start with "could not open directory
# 'pg_notify'": the studio drive drops empty dirs on restart. The db service
# self-heals on `up` via scripts/pg-ensure-runtime-dirs.sh; this forces it and
# recreates the dirs directly in case the container can't start at all. Data is
# never touched — only the empty runtime scaffolding is recreated.
db-repair:
	@docker run --rm -u 0 -v "$(CURDIR)/.data/db:/pgdata" postgres:16-alpine sh -c \
	'for d in pg_notify pg_stat_tmp pg_replslot pg_serial pg_snapshots pg_tblspc pg_twophase pg_commit_ts pg_dynshmem pg_logical/snapshots pg_logical/mappings pg_wal/archive_status; do [ -d "/pgdata/$$d" ] || { mkdir -p "/pgdata/$$d" && chown "$$(stat -c %u:%g /pgdata)" "/pgdata/$$d" && chmod 700 "/pgdata/$$d"; }; done'
	docker compose up -d db
