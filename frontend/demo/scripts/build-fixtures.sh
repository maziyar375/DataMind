#!/usr/bin/env bash
# Regenerate demo/mock/fixtures/*.generated.ts from a real run of every
# scripted statement. See demo/README.md, "How the fixtures are made".
#
#   frontend/demo/scripts/build-fixtures.sh
#
# Needs Docker (two throwaway databases, removed on exit) and a Python that can
# import the backend (`pip install -e backend` — the same environment
# `make test` uses). Nothing here touches the compose stack or `.data/`.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
pg="datamind-demo-fixtures-pg"
mysql="datamind-demo-fixtures-mysql"

cleanup() { docker rm -f "$pg" "$mysql" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "starting scratch PostgreSQL and MySQL (Sakila)…"
docker run -d --name "$pg" \
  -e POSTGRES_USER=demo -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=sales \
  -p 127.0.0.1::5432 postgres:16-alpine >/dev/null
# The repo's own Sakila fixture, loaded the way docker-compose.yml loads it.
docker run -d --name "$mysql" \
  -e MYSQL_ROOT_PASSWORD=demo -e MYSQL_DATABASE=sakila \
  -v "$repo/backend/fixtures/mysql:/docker-entrypoint-initdb.d:ro" \
  -p 127.0.0.1::3306 mysql:8.0 >/dev/null

port() { docker port "$1" "$2" | head -n1 | sed 's/.*://'; }

echo -n "waiting for PostgreSQL"
until docker exec "$pg" pg_isready -U demo -d sales >/dev/null 2>&1; do echo -n .; sleep 1; done
echo
# Sakila's data is a few MB of INSERTs; the read-only user is created last,
# so a successful count through it means the whole init has run.
echo -n "waiting for Sakila to load"
until docker exec "$mysql" mysql -uanalytics_ro -panalytics_ro -e "SELECT COUNT(*) FROM sakila.payment" >/dev/null 2>&1; do
  echo -n .; sleep 2
done
echo

python3 "$here/build.py" --pg "127.0.0.1:$(port "$pg" 5432)" --mysql "127.0.0.1:$(port "$mysql" 3306)"
