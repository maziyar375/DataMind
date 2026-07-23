.PHONY: help secrets up down logs test guard lint fmt migrate

help:
	@echo "make secrets   Generate .env with fresh keys"
	@echo "make up        Start the full stack"
	@echo "make down      Stop everything"
	@echo "make test      Run the backend test suite"
	@echo "make guard     Run the hostile SQL corpus only"
	@echo "make lint      Ruff + architecture contracts"

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

fmt:
	cd backend && ruff format app tests

migrate:
	cd backend && alembic upgrade head
