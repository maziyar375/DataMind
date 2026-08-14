.PHONY: help secrets up down targets targets-down logs test guard lint fmt migrate fixtures db-repair

help:
	@echo "make secrets   Generate .env with fresh keys"
	@echo "make up        Start the full stack"
	@echo "make down      Stop everything"
	@echo "make targets   Start the Oracle + SQL Server demo databases (opt-in, ~2GB each)"
	@echo "make targets-down  Stop them again"
	@echo "make test      Run the backend test suite"
	@echo "make guard     Run the hostile SQL corpus only"
	@echo "make lint      Ruff + architecture contracts"
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

# The Oracle and SQL Server demo targets. Behind a profile rather than in `up`
# because they want ~2 GB of RAM each and most sessions never touch them. The
# SQL Server seeder is a one-shot that skips if `sales` already exists, so this
# is safe to re-run. Addresses are in README.md.
targets:
	docker compose --profile targets up -d oracle mssql mssql-seed

targets-down:
	docker compose --profile targets stop oracle mssql

logs:
	docker compose logs -f api

test:
	cd backend && pytest -q

guard:
	cd backend && pytest tests/unit/test_sqlguard_hostile.py -v

lint:
	cd backend && ruff check app tests && lint-imports

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
