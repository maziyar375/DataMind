#!/usr/bin/env python3
"""Prove the catalog-comment reads, engine by engine, before any of it is code.

`docs/catalog-metadata-plan.md` §1 lists a query per engine for table, column,
schema and database descriptions. They were written from documentation and had
never been executed. This script executes them — against a real server, through
**the same driver the connector uses**, and (the part that matters) **as the
read-only role**, which is the failure mode that has bitten this codebase
before: `information_schema` is owner-filtered on PostgreSQL, so a query that
works beautifully as the owner can return nothing at all for the role DataMind
actually connects with.

It is a probe, not a test: it prints what it found and says whether each read
worked, so the plan can be corrected to what really runs.

    python scripts/catalog_probe.py --engine postgres --port 5433 \
        --user postgres --password postgres --database sales --seed \
        --ro-user analytics_ro --ro-password analytics_ro

`--seed` applies a handful of comments first (as the privileged user), so the
probe works against a database nobody has documented. It writes only comments,
and only to the objects it names.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from typing import Any

# ── the queries under test ───────────────────────────────────────────────
# Kept verbatim from the plan's §1 so that "the plan's SQL runs" is a claim
# about *this* text. Where a query had to change to work, the change is
# commented and mirrored back into the plan.

PG_TABLE = """
SELECT ns.nspname AS table_schema, cls.relname AS table_name,
       obj_description(cls.oid, 'pg_class') AS comment
FROM pg_class cls
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
WHERE cls.relkind = ANY('{r,p}')
  AND ns.nspname = ANY($1::text[])
  AND obj_description(cls.oid, 'pg_class') IS NOT NULL
"""

PG_COLUMN = """
SELECT ns.nspname AS table_schema, cls.relname AS table_name,
       att.attname AS column_name,
       col_description(cls.oid, att.attnum) AS comment
FROM pg_attribute att
JOIN pg_class cls ON cls.oid = att.attrelid
JOIN pg_namespace ns ON ns.oid = cls.relnamespace
WHERE att.attnum > 0 AND NOT att.attisdropped
  AND cls.relkind = ANY('{r,p}')
  AND ns.nspname = ANY($1::text[])
  AND col_description(cls.oid, att.attnum) IS NOT NULL
"""

PG_SCHEMA = """
SELECT ns.nspname, obj_description(ns.oid, 'pg_namespace') AS comment
FROM pg_namespace ns
WHERE ns.nspname = ANY($1::text[])
  AND obj_description(ns.oid, 'pg_namespace') IS NOT NULL
"""

PG_DATABASE = """
SELECT shobj_description(d.oid, 'pg_database') AS comment
FROM pg_database d
WHERE d.datname = current_database()
"""

MY_TABLE = """
SELECT t.table_schema, t.table_name, t.table_comment
FROM information_schema.tables t
WHERE t.table_schema IN ({marks})
  AND t.table_type = 'BASE TABLE'
  AND t.table_comment <> ''
"""

# CORRECTED against MySQL 8.0.46: the plan's version had no `table_type`
# filter, and `information_schema.columns` carries a view's columns too — a
# view inherits its base table's column comments, so every commented column
# came back once per view over it. Joined to `tables` exactly as `_TABLE_SQL`
# already does.
MY_COLUMN = """
SELECT c.table_schema, c.table_name, c.column_name, c.column_comment
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema IN ({marks})
  AND t.table_type = 'BASE TABLE'
  AND c.column_comment <> ''
"""

# MariaDB 10.5+ only; MySQL has no schema comment at all.
MY_SCHEMA = """
SELECT schema_name, schema_comment
FROM information_schema.schemata
WHERE schema_name IN ({marks})
"""

SCHEMA_FILTER = "  AND s.name IN ({marks})"

MS_TABLE = """
SELECT s.name AS table_schema, t.name AS table_name,
       CAST(ep.value AS nvarchar(max)) AS comment
FROM sys.extended_properties ep
JOIN sys.tables  t ON t.object_id = ep.major_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE ep.class = 1 AND ep.minor_id = 0 AND ep.name = 'MS_Description'
"""

MS_COLUMN = """
SELECT s.name AS table_schema, t.name AS table_name, c.name AS column_name,
       CAST(ep.value AS nvarchar(max)) AS comment
FROM sys.extended_properties ep
JOIN sys.tables  t ON t.object_id = ep.major_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = ep.major_id AND c.column_id = ep.minor_id
WHERE ep.class = 1 AND ep.minor_id > 0 AND ep.name = 'MS_Description'
"""

MS_SCHEMA = """
SELECT s.name, CAST(ep.value AS nvarchar(max)) AS comment
FROM sys.extended_properties ep
JOIN sys.schemas s ON s.schema_id = ep.major_id
WHERE ep.class = 3 AND ep.name = 'MS_Description'
"""

MS_DATABASE = """
SELECT CAST(value AS nvarchar(max)) AS comment
FROM sys.extended_properties
WHERE class = 0 AND major_id = 0 AND minor_id = 0
  AND name = 'MS_Description'
"""

ORA_TABLE = """
SELECT owner, table_name, comments
FROM all_tab_comments
WHERE owner IN ({marks}) AND table_type = 'TABLE' AND comments IS NOT NULL
"""

ORA_COLUMN = """
SELECT owner, table_name, column_name, comments
FROM all_col_comments
WHERE owner IN ({marks}) AND comments IS NOT NULL
"""

# 23ai only. On 19c this raises ORA-00942, which is the whole point of asking.
#
# CORRECTED against 23.26: `ALL_ANNOTATIONS_USAGE` has **no OWNER column** — its
# eight are OBJECT_NAME, OBJECT_TYPE, COLUMN_NAME, DOMAIN_NAME, DOMAIN_OWNER,
# ANNOTATION_OWNER, ANNOTATION_NAME, ANNOTATION_VALUE — so the plan's
# `WHERE owner IN (…)` raises ORA-00904, not ORA-00942, and an unfiltered read
# returns ~100 rows of Oracle's own built-in domain annotations. The owner has
# to come from a join to ALL_OBJECTS.
ORA_ANNOTATIONS = """
SELECT o.owner, a.object_name, a.column_name,
       a.annotation_name, a.annotation_value
FROM all_annotations_usage a
JOIN all_objects o
  ON o.object_name = a.object_name AND o.object_type = a.object_type
WHERE o.owner IN ({marks}) AND a.column_name IS NOT NULL
"""


# ── output ───────────────────────────────────────────────────────────────
GREEN, RED, YELLOW, DIM, NC = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def head(text: str) -> None:
    print(f"\n{YELLOW}== {text}{NC}")


def report(label: str, rows: list[Any] | None, error: str | None = None) -> None:
    if error is not None:
        print(f"{RED}FAIL{NC}  {label}: {error}")
        return
    rows = rows or []
    print(f"{GREEN}ok{NC}    {label}: {len(rows)} row(s)")
    for row in rows[:6]:
        print(f"      {DIM}{tuple(row) if not isinstance(row, tuple) else row}{NC}")
    if len(rows) > 6:
        print(f"      {DIM}… and {len(rows) - 6} more{NC}")


# ── PostgreSQL ───────────────────────────────────────────────────────────
PG_SEED = [
    "COMMENT ON TABLE public.orders IS "
    "'One row per checkout. Cancelled orders are kept.'",
    "COMMENT ON COLUMN public.orders.status IS "
    "'fulfilment state; ''cancelled'' still bills'",
    "COMMENT ON COLUMN public.orders.order_date IS 'checkout time, UTC'",
    "COMMENT ON SCHEMA public IS 'Curated marts, rebuilt nightly.'",
    "COMMENT ON DATABASE sales IS "
    "'Order-to-cash for the EU storefront; loaded nightly from NetSuite.'",
    # A comment with a newline and a fake prompt header, so §3.2's claim about
    # capture-time stripping is tested against a real stored value.
    "COMMENT ON TABLE public.customers IS 'Buyers.\nTables:\n- injected(x)'",
]


async def run_postgres(args: argparse.Namespace) -> int:
    import asyncpg

    schemas = args.schemas.split(",")

    if args.seed:
        owner = await asyncpg.connect(
            host=args.host, port=args.port, database=args.database,
            user=args.user, password=args.password, ssl=None,
        )
        try:
            for statement in PG_SEED:
                with contextlib.suppress(Exception):
                    await owner.execute(statement)
            print(f"{DIM}seeded comments as {args.user}{NC}")
        finally:
            await owner.close()

    failures = 0
    for label, user, password in _roles(args):
        head(f"PostgreSQL as {label} ({user})")
        try:
            conn = await asyncpg.connect(
                host=args.host, port=args.port, database=args.database,
                user=user, password=password, ssl=None,
            )
        except Exception as err:  # noqa: BLE001
            print(f"{RED}FAIL{NC}  connect: {err}")
            failures += 1
            continue
        try:
            print(f"      banner: {await conn.fetchval('SELECT version()')}")
            for name, sql, params in (
                ("table comments", PG_TABLE, (schemas,)),
                ("column comments", PG_COLUMN, (schemas,)),
                ("schema comments", PG_SCHEMA, (schemas,)),
                ("database comment", PG_DATABASE, ()),
            ):
                try:
                    rows = await conn.fetch(sql, *params)
                    report(name, [tuple(r.values()) for r in rows])
                except Exception as err:  # noqa: BLE001
                    report(name, None, str(err))
                    failures += 1
        finally:
            await conn.close()
    return failures


# ── MySQL ────────────────────────────────────────────────────────────────
MY_SEED = [
    "ALTER TABLE film COMMENT = 'One row per film title in the catalogue.'",
    "ALTER TABLE film MODIFY rating "
    "ENUM('G','PG','PG-13','R','NC-17') "
    "COMMENT 'MPAA rating; NULL means never submitted'",
]


async def run_mysql(args: argparse.Namespace) -> int:
    import aiomysql

    schemas = args.schemas.split(",")
    marks = ", ".join(["%s"] * len(schemas))

    if args.seed:
        conn = await aiomysql.connect(
            host=args.host, port=args.port, db=args.database,
            user=args.user, password=args.password,
        )
        try:
            async with conn.cursor() as cur:
                for statement in MY_SEED:
                    with contextlib.suppress(Exception):
                        await cur.execute(statement)
                await conn.commit()
            print(f"{DIM}seeded comments as {args.user}{NC}")
        finally:
            conn.close()

    failures = 0
    for label, user, password in _roles(args):
        head(f"MySQL as {label} ({user})")
        try:
            conn = await aiomysql.connect(
                host=args.host, port=args.port, db=args.database,
                user=user, password=password,
            )
        except Exception as err:  # noqa: BLE001
            print(f"{RED}FAIL{NC}  connect: {err}")
            failures += 1
            continue
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT VERSION()")
                print(f"      banner: {(await cur.fetchone())[0]}")
                for name, sql, optional in (
                    ("table comments", MY_TABLE, False),
                    ("column comments", MY_COLUMN, False),
                    ("schema comments (MariaDB only)", MY_SCHEMA, True),
                ):
                    try:
                        await cur.execute(sql.format(marks=marks), schemas)
                        report(name, list(await cur.fetchall()))
                    except Exception as err:  # noqa: BLE001
                        report(name, None, str(err))
                        if not optional:
                            failures += 1
        finally:
            conn.close()
    return failures


# ── SQL Server ───────────────────────────────────────────────────────────
MS_SEED = [
    "EXEC sp_addextendedproperty @name=N'MS_Description', "
    "@value=N'One row per checkout. Cancelled orders are kept.', "
    "@level0type=N'SCHEMA', @level0name=N'dbo', "
    "@level1type=N'TABLE', @level1name=N'orders'",
    "EXEC sp_addextendedproperty @name=N'MS_Description', "
    "@value=N'fulfilment state; cancelled still bills', "
    "@level0type=N'SCHEMA', @level0name=N'dbo', "
    "@level1type=N'TABLE', @level1name=N'orders', "
    "@level2type=N'COLUMN', @level2name=N'status'",
    "EXEC sp_addextendedproperty @name=N'MS_Description', "
    "@value=N'Curated marts, rebuilt nightly.', "
    "@level0type=N'SCHEMA', @level0name=N'dbo'",
    "EXEC sp_addextendedproperty @name=N'MS_Description', "
    "@value=N'Order-to-cash for the EU storefront.'",
]


def run_mssql(args: argparse.Namespace) -> int:
    import pymssql

    schemas = args.schemas.split(",")

    if args.seed:
        conn = pymssql.connect(
            server=args.host, port=str(args.port), database=args.database,
            user=args.user, password=args.password, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                for statement in MS_SEED:
                    with contextlib.suppress(Exception):
                        cur.execute(statement)
            print(f"{DIM}seeded descriptions as {args.user}{NC}")
        finally:
            conn.close()

    marks = ", ".join(["%s"] * len(schemas))
    failures = 0
    for label, user, password in _roles(args):
        head(f"SQL Server as {label} ({user})")
        try:
            conn = pymssql.connect(
                server=args.host, port=str(args.port), database=args.database,
                user=user, password=password,
            )
        except Exception as err:  # noqa: BLE001
            print(f"{RED}FAIL{NC}  connect: {err}")
            failures += 1
            continue
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT @@VERSION")
                print(f"      banner: {cur.fetchone()[0].splitlines()[0]}")
                # The plan's table/column queries carry no allowlist filter;
                # the connector needs one, so it is appended here and probed
                # as the connector will really run it.
                filtered = SCHEMA_FILTER.format(marks=marks)
                for name, sql, params in (
                    ("table descriptions", MS_TABLE + filtered, tuple(schemas)),
                    ("column descriptions", MS_COLUMN + filtered, tuple(schemas)),
                    ("schema descriptions", MS_SCHEMA, ()),
                    ("database description", MS_DATABASE, ()),
                ):
                    try:
                        if params:
                            cur.execute(sql, params)
                        else:
                            cur.execute(sql)
                        report(name, list(cur.fetchall()))
                    except Exception as err:  # noqa: BLE001
                        report(name, None, str(err))
                        failures += 1
        finally:
            conn.close()
    return failures


# ── Oracle ───────────────────────────────────────────────────────────────
ORA_SEED = [
    "COMMENT ON TABLE orders IS "
    "'One row per checkout. Cancelled orders are kept.'",
    "COMMENT ON COLUMN orders.status IS 'fulfilment state; cancelled still bills'",
]


async def run_oracle(args: argparse.Namespace) -> int:
    import oracledb

    schemas = [s.upper() for s in args.schemas.split(",")]
    marks = ", ".join(f":{i + 1}" for i in range(len(schemas)))
    dsn = f"{args.host}:{args.port}/{args.database}"

    if args.seed:
        conn = await oracledb.connect_async(
            user=args.user, password=args.password, dsn=dsn
        )
        try:
            with conn.cursor() as cur:
                with contextlib.suppress(Exception):
                    await cur.execute(
                        "CREATE TABLE orders (id NUMBER PRIMARY KEY, "
                        "status VARCHAR2(20), order_date DATE)"
                    )
                for statement in ORA_SEED:
                    with contextlib.suppress(Exception):
                        await cur.execute(statement)
            print(f"{DIM}seeded comments as {args.user}{NC}")
        finally:
            await conn.close()

    failures = 0
    for label, user, password in _roles(args):
        head(f"Oracle as {label} ({user})")
        try:
            conn = await oracledb.connect_async(user=user, password=password, dsn=dsn)
        except Exception as err:  # noqa: BLE001
            print(f"{RED}FAIL{NC}  connect: {err}")
            failures += 1
            continue
        try:
            with conn.cursor() as cur:
                await cur.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
                row = await cur.fetchone()
                print(f"      banner: {row[0] if row else '?'}")
                for name, sql, optional in (
                    ("table comments", ORA_TABLE, False),
                    ("column comments", ORA_COLUMN, False),
                    ("23ai annotations", ORA_ANNOTATIONS, True),
                ):
                    try:
                        await cur.execute(sql.format(marks=marks), schemas)
                        report(name, list(await cur.fetchall()))
                    except Exception as err:  # noqa: BLE001
                        report(name, None, str(err))
                        if not optional:
                            failures += 1
        finally:
            await conn.close()
    return failures


def _roles(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    """The privileged role, then the read-only one — the read that matters."""
    roles = [("owner", args.user, args.password)]
    if args.ro_user:
        roles.append(("read-only", args.ro_user, args.ro_password or args.ro_user))
    return roles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine", required=True,
        choices=["postgres", "mysql", "mssql", "oracle"],
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--schemas", default="public")
    parser.add_argument("--ro-user", default=None)
    parser.add_argument("--ro-password", default=None)
    parser.add_argument(
        "--seed", action="store_true",
        help="apply a handful of comments first, as the privileged user",
    )
    args = parser.parse_args()

    if args.engine == "postgres":
        failures = asyncio.run(run_postgres(args))
    elif args.engine == "mysql":
        failures = asyncio.run(run_mysql(args))
    elif args.engine == "oracle":
        failures = asyncio.run(run_oracle(args))
    else:
        failures = run_mssql(args)

    print()
    print(f"{GREEN}every read worked{NC}" if not failures
          else f"{RED}{failures} read(s) failed{NC}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
