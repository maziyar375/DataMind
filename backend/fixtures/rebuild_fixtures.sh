#!/usr/bin/env bash
#
# Rebuild the demo/eval fixtures from clean and prove they load.
#
# For each dialect (PostgreSQL, MySQL, SQL Server) this spins a THROWAWAY
# container, loads the seed from empty, and asserts:
#   * the load completes with no error,
#   * the schema has the expected 42 tables, and
#   * the retrieve-node budget estimate `sum(60 + 40*ncols)` exceeds 24000,
#     which is the whole reason the fixture is wide (otherwise the retrieve node
#     sends the entire snapshot and retrieval is never exercised).
# It prints the table count, column total, budget estimate and load time for each.
#
# PostgreSQL additionally loads `sales_comments.sql` — the COMMENT ON overlay
# that is the commented arm of the eval A/B — and reads the descriptions back
# as `analytics_ro`, so a typo in it is found here rather than in a paid run.
#
# Then, unless SKIP_DEMO=1, it rebuilds the running Compose Postgres `sales`
# demo from a clean volume so the app sees the new schema (that DB is designed
# to be re-seeded from its init script — see docker-compose.yml).
#
# The MySQL and SQL Server mirrors are validated but not wired as long-running
# Compose services: the eval harness spins them per-suite via testcontainers.
#
# Usage:  make fixtures            # validate all dialects + rebuild PG demo
#         SKIP_DEMO=1 make fixtures   # validate only, leave the demo alone
#         ONLY=pg make fixtures       # validate a single dialect (pg|mysql|mssql)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PG_SEED="$HERE/sales_seed.sql"
PG_COMMENTS="$HERE/sales_comments.sql"
MY_SEED="$HERE/sales_seed_mysql.sql"
MS_SEED="$HERE/sales_seed_mssql.sql"

PG_IMAGE="${PG_IMAGE:-postgres:16-alpine}"
MY_IMAGE="${MY_IMAGE:-mysql:8.0}"
MS_IMAGE="${MS_IMAGE:-mcr.microsoft.com/mssql/server:2022-latest}"
MS_SA_PASSWORD="${MS_SA_PASSWORD:-Str0ng_Passw0rd!}"
ONLY="${ONLY:-all}"

EXPECT_TABLES=42
# The width floor. It was the retrieve node's ceiling when the fixture was
# built; `_RETRIEVE_BUDGET_CHARS` has since moved to 50,000, so clearing 24,000
# no longer means retrieval is exercised — at 26,480 this fixture now takes the
# FULL_SNAPSHOT branch. Kept as a floor because a narrow fixture is still wrong;
# see docs/eval.md §1 for what that costs the recall metric.
MIN_BUDGET=24000
RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; NC=$'\033[0m'
fail() { echo "${RED}FAIL:${NC} $*" >&2; exit 1; }
ok()   { echo "${GRN}ok:${NC} $*"; }

command -v docker >/dev/null || fail "docker is required"

# Always tear down any throwaway container this run created, even on failure.
cleanup() { docker rm -f dm_fx_pg_$$ dm_fx_my_$$ dm_fx_ms_$$ >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

# ── PostgreSQL ──────────────────────────────────────────────────────────────
verify_pg() {
  local c=dm_fx_pg_$$
  echo "${YEL}==> PostgreSQL${NC} ($PG_IMAGE)"
  docker rm -f "$c" >/dev/null 2>&1 || true
  docker run -d --name "$c" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=sales "$PG_IMAGE" >/dev/null
  trap 'docker rm -f '"$c"' >/dev/null 2>&1 || true' RETURN
  until docker exec "$c" pg_isready -U postgres -d sales >/dev/null 2>&1; do sleep 1; done
  docker cp "$PG_SEED" "$c:/seed.sql" >/dev/null
  local t0=$SECONDS
  docker exec "$c" psql -U postgres -d sales -q -v ON_ERROR_STOP=1 -f /seed.sql >/dev/null 2>/tmp/dmfx_pg.err \
    || { cat /tmp/dmfx_pg.err >&2; fail "postgres seed did not load cleanly"; }
  local secs=$((SECONDS - t0))
  read -r tables cols budget < <(docker exec "$c" psql -U postgres -d sales -t -A -F' ' -c \
    "SELECT count(*), sum(nc), sum(60+40*nc) FROM (SELECT c.oid, count(a.attname) nc FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped WHERE c.relkind='r' GROUP BY c.oid) z;")
  docker exec "$c" psql -U analytics_ro -d sales -c "SELECT 1 FROM orders LIMIT 1" >/dev/null 2>&1 \
    || fail "analytics_ro cannot read"
  report pg "$tables" "$cols" "$budget" "$secs"
  verify_pg_comments "$c"
}

# The COMMENT ON overlay: it loads on top of the seed, and the catalog gives
# back what it wrote. Checked here because the only other thing that loads it is
# a paid eval run, where a misspelt object name is discovered forty minutes and
# real money in. Read as `analytics_ro`, since that is the role a customer's
# connection uses and pg_description carries no privilege filter of its own.
verify_pg_comments() {
  local c=$1
  docker cp "$PG_COMMENTS" "$c:/comments.sql" >/dev/null
  docker exec "$c" psql -U postgres -d sales -q -v ON_ERROR_STOP=1 -f /comments.sql >/dev/null 2>/tmp/dmfx_pgc.err \
    || { cat /tmp/dmfx_pgc.err >&2; fail "the sales_comments.sql overlay did not load cleanly"; }
  read -r ctables ccols cdb < <(docker exec "$c" psql -U analytics_ro -d sales -t -A -F' ' -c \
    "SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' WHERE c.relkind='r' AND obj_description(c.oid,'pg_class') IS NOT NULL),
            (SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' WHERE a.attnum>0 AND NOT a.attisdropped AND c.relkind='r' AND col_description(c.oid,a.attnum) IS NOT NULL),
            (SELECT count(*) FROM pg_database d WHERE d.datname=current_database() AND shobj_description(d.oid,'pg_database') IS NOT NULL);")
  [ "${ctables:-0}" -ge 15 ] || fail "pg comments: only ${ctables:-0} tables described (want >= 15)"
  [ "${ccols:-0}"   -ge 40 ] || fail "pg comments: only ${ccols:-0} columns described (want >= 40)"
  [ "${cdb:-0}"     -eq 1  ] || fail "pg comments: the database description is missing"
  ok "pg comments: $ctables tables, $ccols columns + the database, read back as analytics_ro"
}

# ── MySQL ───────────────────────────────────────────────────────────────────
verify_mysql() {
  local c=dm_fx_my_$$
  echo "${YEL}==> MySQL${NC} ($MY_IMAGE)"
  docker rm -f "$c" >/dev/null 2>&1 || true
  docker run -d --name "$c" -e MYSQL_ROOT_PASSWORD=root "$MY_IMAGE" >/dev/null
  trap 'docker rm -f '"$c"' >/dev/null 2>&1 || true' RETURN
  local i; for i in $(seq 1 90); do docker exec "$c" mysql -uroot -proot -e "SELECT 1" >/dev/null 2>&1 && break; sleep 2; done
  docker cp "$MY_SEED" "$c:/seed.sql" >/dev/null
  local t0=$SECONDS
  docker exec "$c" sh -c "mysql -uroot -proot < /seed.sql" 2>/tmp/dmfx_my.err \
    || { grep -v 'Using a password' /tmp/dmfx_my.err >&2; fail "mysql seed did not load cleanly"; }
  local secs=$((SECONDS - t0))
  read -r tables cols budget < <(docker exec "$c" mysql -uroot -proot -N -e \
    "SELECT COUNT(*), SUM(nc), SUM(60+40*nc) FROM (SELECT table_name, COUNT(*) nc FROM information_schema.columns WHERE table_schema='sales' GROUP BY table_name) z;" 2>/dev/null)
  report mysql "$tables" "$cols" "$budget" "$secs"
}

# ── SQL Server ──────────────────────────────────────────────────────────────
verify_mssql() {
  local c=dm_fx_ms_$$
  echo "${YEL}==> SQL Server${NC} ($MS_IMAGE)"
  docker rm -f "$c" >/dev/null 2>&1 || true
  docker run -d --name "$c" -e ACCEPT_EULA=Y -e "MSSQL_SA_PASSWORD=$MS_SA_PASSWORD" "$MS_IMAGE" >/dev/null
  trap 'docker rm -f '"$c"' >/dev/null 2>&1 || true' RETURN
  local sqlcmd="" i
  for i in $(seq 1 90); do
    for cand in /opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools/bin/sqlcmd; do
      docker exec "$c" test -x "$cand" 2>/dev/null && sqlcmd="$cand"
    done
    [ -n "$sqlcmd" ] && docker exec "$c" "$sqlcmd" -S localhost -U sa -P "$MS_SA_PASSWORD" -C -N -Q "SELECT 1" >/dev/null 2>&1 && break
    sleep 2
  done
  [ -n "$sqlcmd" ] || fail "sqlcmd not found in the mssql image"
  docker cp "$MS_SEED" "$c:/seed.sql" >/dev/null
  local t0=$SECONDS
  docker exec "$c" "$sqlcmd" -S localhost -U sa -P "$MS_SA_PASSWORD" -C -N -b -i /seed.sql >/tmp/dmfx_ms.out 2>/tmp/dmfx_ms.err \
    || { cat /tmp/dmfx_ms.err /tmp/dmfx_ms.out >&2; fail "mssql seed did not load cleanly"; }
  local secs=$((SECONDS - t0))
  read -r tables cols budget < <(docker exec "$c" "$sqlcmd" -S localhost -U sa -P "$MS_SA_PASSWORD" -C -N -d sales -h -1 -W -Q \
    "SET NOCOUNT ON; SELECT CAST(COUNT(*) AS varchar)+' '+CAST(SUM(nc) AS varchar)+' '+CAST(SUM(60+40*nc) AS varchar) FROM (SELECT t.object_id, COUNT(*) nc FROM sys.tables t JOIN sys.columns c ON c.object_id=t.object_id GROUP BY t.object_id) z;" 2>/dev/null | tr -d '\r' | grep -E '^[0-9]+ [0-9]+ [0-9]+$' | head -1)
  report mssql "$tables" "$cols" "$budget" "$secs"
}

report() {
  local d=$1 tables=$2 cols=$3 budget=$4 secs=$5
  [ "$tables" = "$EXPECT_TABLES" ] || fail "$d: expected $EXPECT_TABLES tables, got '$tables'"
  [ "$budget" -gt "$MIN_BUDGET" ] || fail "$d: budget estimate $budget must exceed $MIN_BUDGET (retrieval would not be exercised)"
  [ "$secs" -lt 60 ] || fail "$d: seed took ${secs}s (must be under 60s)"
  ok "$d: $tables tables, $cols columns, budget=$budget (> $MIN_BUDGET), loaded in ${secs}s"
}

case "$ONLY" in
  all)   verify_pg; verify_mysql; verify_mssql ;;
  pg)    verify_pg ;;
  mysql) verify_mysql ;;
  mssql) verify_mssql ;;
  *)     fail "ONLY must be one of: all pg mysql mssql" ;;
esac

# ── Rebuild the running Compose Postgres demo from a clean volume ────────────
if [ "${SKIP_DEMO:-0}" != "1" ] && [ "$ONLY" = "all" ]; then
  ROOT="$(cd "$HERE/../.." && pwd)"
  if command -v docker >/dev/null && [ -f "$ROOT/docker-compose.yml" ]; then
    echo "${YEL}==> Rebuilding the Compose 'sales' demo from clean${NC}"
    ( cd "$ROOT"
      # Ask Docker which volume the service actually owns, rather than deriving
      # it. `$(basename "$ROOT")_raymand_sales` was wrong for the same reason it
      # looked right: Compose LOWER-CASES the project name it takes from the
      # directory, so a checkout named `DataMind` owns `datamind_raymand_sales`
      # — and the `|| true` turned that into a silent no-op. The demo then kept
      # its old volume, skipped every init script (Postgres runs them only on an
      # empty data directory), and the line below announced a rebuild that had
      # not happened. Derived name kept only as the fallback for when no
      # container exists yet.
      cid="$(docker compose ps -aq sales 2>/dev/null | head -1)"
      vol=""
      [ -n "$cid" ] && vol="$(docker inspect -f \
        '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' \
        "$cid" 2>/dev/null || true)"
      [ -n "$vol" ] || vol="$(printf '%s' "${COMPOSE_PROJECT_NAME:-$(basename "$ROOT")}" \
        | tr '[:upper:]' '[:lower:]')_raymand_sales"
      docker compose rm -sf sales >/dev/null 2>&1 || true
      docker volume rm "$vol" >/dev/null 2>&1 || true
      if docker volume inspect "$vol" >/dev/null 2>&1; then
        fail "could not drop the demo volume '$vol'; the demo would keep its old schema"
      fi
      docker compose up -d sales >/dev/null
    )
    # And prove it: the init scripts only run on an empty volume, so counting
    # the descriptions back is what says the rebuild happened at all.
    printf '    waiting for the demo to re-seed '
    demo_ok=0
    for _ in $(seq 1 60); do
      if docker compose -f "$ROOT/docker-compose.yml" exec -T sales \
           psql -U postgres -d sales -t -A -c \
           "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' WHERE c.relkind='r' AND obj_description(c.oid,'pg_class') IS NOT NULL" \
           2>/dev/null | tr -d '[:space:]' | grep -qE '^([1-9][0-9]+)$'; then
        demo_ok=1; break
      fi
      printf '.'; sleep 2
    done
    printf '\n'
    [ "$demo_ok" = 1 ] || fail "the demo came back without its catalog descriptions"
    ok "Compose 'sales' demo re-seeded from the new schema, descriptions included"
  else
    echo "${YEL}(skipping demo rebuild: docker compose / compose file not found)${NC}"
  fi
fi

echo "${GRN}All fixtures rebuilt and verified.${NC}"
