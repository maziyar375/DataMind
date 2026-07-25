#!/bin/sh
# Postgres entrypoint shim: recreate the empty runtime subdirectories a cluster
# needs but that some hosts strip from a bind-mounted data directory.
#
# Why this exists: the `db` service bind-mounts its data dir onto the Lightning
# Studio drive so the database survives a studio stop. But that drive does not
# preserve *empty* directories across a restart, and Postgres refuses to start
# when e.g. `pg_notify`, `pg_wal/archive_status`, or `pg_stat_tmp` are missing
# ("FATAL: could not open directory ..."). The real data (base/global/pg_xact/
# pg_wal contents) is intact — only the empty scaffolding is gone, so we just
# recreate it, matching the data dir's own owner and 0700 perms.
#
# Guarded by PG_VERSION so this only ever repairs an already-initialised
# cluster; a fresh `initdb` is never disturbed (creating these dirs early would
# make the official entrypoint think the dir is non-empty and skip init).
set -e

DIR="${PGDATA:-/var/lib/postgresql/data}"

if [ -s "$DIR/PG_VERSION" ]; then
  owner="$(stat -c '%u:%g' "$DIR")"
  for d in pg_notify pg_stat_tmp pg_replslot pg_serial pg_snapshots pg_tblspc \
           pg_twophase pg_commit_ts pg_dynshmem pg_logical/snapshots \
           pg_logical/mappings pg_wal/archive_status; do
    if [ ! -d "$DIR/$d" ]; then
      mkdir -p "$DIR/$d"
      chown "$owner" "$DIR/$d"
      chmod 700 "$DIR/$d"
      echo "pg-ensure-runtime-dirs: recreated $d"
    fi
  done
fi

exec docker-entrypoint.sh "$@"
