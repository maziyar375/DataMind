"""Build the demo's fixtures from a real run of every scripted statement.

    build.py --pg HOST:PORT --mysql HOST:PORT

Nothing in `mock/fixtures/*.generated.ts` is typed by hand. This script:

1. loads the demo warehouse (`warehouse.py`) into a scratch PostgreSQL, with the
   repo fixture's DDL and catalog comments, and connects to a scratch MySQL
   holding the repo's own Sakila fixture;
2. syncs both schemas through the backend's **real connectors**, under each
   connection's disclosure policy — the snapshots the Schema tab shows;
3. puts every scripted statement (`questions.py`) through the backend's **real
   guard** against those snapshots, so a rejected draft is rejected with the
   guard's own words and the SQL on screen is the guard's own rewrite;
4. runs it through the real connector against the scratch database, "today"
   frozen at `DEMO_TODAY`;
5. runs the backend's **result checks** and **chart planner/compiler** on the
   rows, including every alternative type for *Change chart*;
6. writes the narrative from those rows and checks its figures against them;
7. runs every step of the scripted deep analyses the same way, closes each one
   with the deep pipeline's own disclosure and computation, and checks every
   cited sentence of the answer against the step it cites (`run_deep`).

`build-fixtures.sh` starts and removes the scratch containers around this.
"""
from __future__ import annotations

import argparse
import warnings
import asyncio
import json
import re
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
REPO = DEMO.parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(HERE))

import asyncpg  # noqa: E402

from app.charts import (  # noqa: E402
    ChartIntent, candidate_intent, chart_options, compile_vega_lite, plan_chart,
    plan_kpi, profile_result, unchartable_reason,
)
from app.domain.ports.database import ResultColumn  # noqa: E402
from app.domain.value_objects import HintBudget  # noqa: E402
from app.infra.connectors.mysql import MySqlConnector  # noqa: E402
from app.infra.connectors.postgres import PostgresConnector  # noqa: E402
from app.pipeline import evidence as ev  # noqa: E402
from app.pipeline.checks import inspect_result  # noqa: E402
from app.pipeline.state import ExecutionResult, PlanRevision, PlanStep, StepEvidence  # noqa: E402
from app.reports import checks  # noqa: E402
from app.sqlguard import GuardPolicy, guard  # noqa: E402

import questions as Q  # noqa: E402
import warehouse  # noqa: E402

OUT = DEMO / "mock" / "fixtures"
warnings.filterwarnings("ignore", module="aiomysql")
MAX_ROWS = 1000
#: The snapshot version the demo reports: synced three times since it was added.
SNAPSHOT_VERSION = 3

CONNECTIONS = {
    "sales": {"dialect": "postgres", "policy": "SAMPLE", "schemas": ["public"]},
    "sakila": {"dialect": "mysql", "policy": "AGGREGATE", "schemas": []},
}


def demo_today() -> date:
    text = (OUT / "today.ts").read_text()
    match = re.search(r"DEMO_TODAY = '(\d{4}-\d{2}-\d{2})'", text)
    assert match, "DEMO_TODAY not found in mock/fixtures/today.ts"
    return date.fromisoformat(match.group(1))


def freeze(sql: str, today: date, dialect: str) -> str:
    """The statement as it would run *on* DEMO_TODAY.

    Only for execution: the SQL the demo displays keeps `CURRENT_DATE`, exactly
    as a model writes it. This is the one liberty taken with a statement, and
    it changes which day it is, not what the statement does.
    """
    literal = f"DATE '{today.isoformat()}'" if dialect == "postgres" else f"DATE('{today.isoformat()}')"
    out = re.sub(r"\bCURRENT_DATE\b(\(\))?", literal, sql, flags=re.I)
    return re.sub(r"\bCURDATE\(\)", literal, out, flags=re.I)


# ── loading ────────────────────────────────────────────────────────────────
async def load_sales(host: str, port: int, today: date) -> None:
    conn = await asyncpg.connect(host=host, port=port, user="demo", password="demo", database="sales")
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for statement in warehouse.schema_statements():
            await conn.execute(statement)
        for statement in warehouse.comment_statements():
            await conn.execute(statement)
        tables = warehouse.generate(today)
        for name, table in tables.items():
            await conn.copy_records_to_table(name, records=table.rows, columns=table.columns)
        await conn.execute("ANALYZE")
        await conn.execute("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_ro') THEN
                CREATE ROLE analytics_ro LOGIN PASSWORD 'analytics_ro';
              END IF;
            END $$;
            GRANT CONNECT ON DATABASE sales TO analytics_ro;
            GRANT USAGE ON SCHEMA public TO analytics_ro;
            GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_ro;
        """)
        print(f"  loaded {len(tables)} tables, {sum(len(t.rows) for t in tables.values()):,} rows")
    finally:
        await conn.close()


def connector(name: str, host: str, port: int) -> Any:
    if name == "sales":
        return PostgresConnector(host=host, port=port, database="sales", username="analytics_ro",
                                 password="analytics_ro", ssl_mode="disable")
    return MySqlConnector(host=host, port=port, database="sakila", username="analytics_ro",
                          password="analytics_ro", ssl_mode="disable")


def snapshot_dict(snapshot: Any) -> dict[str, Any]:
    """The snapshot the way `GET /connections/{id}/schema` serves it."""
    return {
        "dialect": snapshot.dialect,
        "tables": [t.as_dict() for t in snapshot.tables],
        "relationships": [
            {"from_table": r.from_table, "from_column": r.from_column,
             "to_table": r.to_table, "to_column": r.to_column}
            for r in snapshot.relationships
        ],
        "catalog_meta": snapshot.catalog_meta(),
        "server_version": snapshot.server_version,
    }


def policy_for(snapshot: dict[str, Any], dialect: str) -> GuardPolicy:
    """`query_service.policy_from_snapshot`, without a database row to read."""
    allowed_tables: set[str] = set()
    allowed_columns: dict[str, set[str]] = {}
    for table in snapshot["tables"]:
        qualified = f"{table['schema']}.{table['name']}".lower()
        allowed_tables.add(qualified)
        allowed_columns[qualified] = {c["name"].lower() for c in table["columns"]}
    return GuardPolicy(dialect=dialect, max_rows=MAX_ROWS, allowed_tables=allowed_tables,
                       allowed_columns=allowed_columns)


def row_count_sql(snapshot: dict[str, Any], dialect: str) -> str:
    """"How many records are in each table?" — the UNION a model writes for it."""
    parts = []
    for t in sorted(snapshot["tables"], key=lambda t: t["name"]):
        parts.append(f"SELECT '{t['name']}' AS table_name, COUNT(*) AS row_count FROM {t['schema']}.{t['name']}")
    return "\nUNION ALL\n".join(parts) + "\nORDER BY row_count DESC"


# ── one question ───────────────────────────────────────────────────────────
NUMBER = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)(\s*(?:million|%))?")


def check_figures(text: str, rows: list[list[Any]], extra: list[float]) -> None:
    """Every figure in a sentence is one the result supports.

    A figure passes if it is a cell (at the precision written), a sum of a
    column, a count of rows, a year, or one of the derived values the narrative
    declares it computed (`extra` — shares and lifts). Anything else is a typo,
    and a typo in the one sentence a visitor will add up is the demo's seam.
    """
    fa = str.maketrans("۰۱۲۳۴۵۶۷۸۹٬٪", "0123456789,%")
    text = text.translate(fa)
    # Names quoted from the result carry their own digits ('Arcwave 27" …').
    for value in sorted({v for r in rows for v in r if isinstance(v, str)}, key=len, reverse=True):
        text = text.replace(value, " ")
    cells = [v for r in rows for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)]
    columns = list(zip(*rows)) if rows else []
    sums = [sum(c) for c in columns if c and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in c)]
    for match in NUMBER.finditer(text):
        raw, unit = match.group(1), (match.group(2) or "").strip()
        value = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if unit == "%":
            continue  # shares and lifts are computed in the narrative itself
        candidates = [*cells, *sums, *extra, float(len(rows))]
        if unit == "million":
            ok = any(round(c / 1e6, decimals) == value for c in candidates)
        else:
            ok = (
                any(round(c, decimals) == value for c in candidates)
                or (decimals == 0 and 1990 <= value <= 2100)
                or (decimals == 0 and value <= 12)
            )
        if not ok:
            raise AssertionError(f"figure {match.group(0)!r} in the narrative matches nothing in the result")


async def run_question(
    q: Q.Question, snapshot: dict[str, Any], dialect: str, conn: Any, today: date,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": q.id, "connection": q.connection, "question": q.text, "aliases": q.aliases,
        "intent": q.intent, "followups": q.followups, "shows": q.shows, "history": q.history,
    }
    if q.intent == "METADATA":
        schema_name = snapshot["tables"][0]["schema"]
        text = q.narrative(Q.Schema(snapshot["tables"], snapshot["relationships"], schema_name))
        check_figures(text, [], [len(snapshot["tables"]), len(snapshot["relationships"]),
                                 *[t["approx_row_count"] or 0 for t in snapshot["tables"]]])
        out.update(answer=text, attempts=[], result=None, chart=None, kpi=None, options=[], redraws={})
        return out

    policy = policy_for(snapshot, dialect)
    attempts = []
    for n, draft in enumerate([*q.rejected, q.sql], 1):
        sql = row_count_sql(snapshot, dialect) if draft == "__ROW_COUNTS__" else draft
        report, executable = guard(sql, policy)
        final = n == len(q.rejected) + 1
        if final and report.status != "VALID":
            raise AssertionError(f"{q.id}: the final statement was rejected: {report.to_feedback()}")
        if not final and report.status == "VALID":
            raise AssertionError(f"{q.id}: draft {n} was supposed to be rejected and passed")
        attempts.append({
            "attempt_no": n,
            "raw_sql": sql,
            "rewritten_sql": executable if report.status == "VALID" else None,
            "validation_status": report.status,
            "validation_report": report.model_dump(mode="json"),
            "referenced_tables": sorted(report.referenced_tables),
        })
    final = attempts[-1]
    exec_sql = freeze(final["rewritten_sql"], today, dialect)
    result = await conn.execute(exec_sql, max_rows=MAX_ROWS, statement_timeout_ms=30000)
    scanned = await conn.explain(exec_sql)

    findings = inspect_result(
        question=q.text, sql=final["rewritten_sql"], dialect=dialect, tables=snapshot["tables"],
        row_count=result.row_count, column_count=len(result.columns), truncated=result.truncated,
    )
    retryable = [f.code for f in findings if f.retry]
    if retryable:
        # A retry-eligible finding sends the real pipeline back to `generate`;
        # the demo scripts no such run, so the statement has to be fixed.
        raise AssertionError(f"{q.id}: the result checks would retry on {retryable}")
    codes = [f.code for f in findings]
    inspect_step = {"status": "DONE", "detail": f"Noted: {', '.join(codes)}" if codes else "No issues found"}

    names = [c.name for c in result.columns]
    text = q.narrative(Q.Result(names, result.rows, codes))
    # A figure the question itself states ("the last 90 days") is not a claim.
    stated = [float(n) for n in re.findall(r"\d+", q.text)]
    check_figures(text, result.rows, stated)

    charts = charts_for(result, q.chart)
    out.update(
        template_check=template_checks(final["raw_sql"], q.text, snapshot, dialect),
        answer=text,
        attempts=attempts,
        result=result_record(result, scanned),
        chart=charts["chart"],
        kpi=charts["kpi"],
        chart_step=charts["chart_step"],
        inspect_step=inspect_step,
        findings=[f.model_dump(mode="json") for f in findings],
        options=charts["options"],
        redraws=charts["redraws"],
    )
    return out


def result_record(result: Any, scanned: int | None) -> dict[str, Any]:
    """A result as the TABLE artifact and the live preview carry it."""
    return {
        "columns": [{"name": c.name, "db_type": c.db_type, "semantic_type": c.semantic_type} for c in result.columns],
        "rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "duration_ms": result.duration_ms,
        "rows_scanned_estimate": scanned,
    }


def charts_for(result: Any, proposed: dict[str, Any] | None) -> dict[str, Any]:
    """What the `chart` node makes of a result, and every *Change chart* answer.

    `proposed` is the chart the model suggested for it; None, it declined.
    """
    columns = [ResultColumn(name=c.name, db_type=c.db_type, semantic_type=c.semantic_type) for c in result.columns]
    profile = profile_result(columns, result.rows, truncated=result.truncated)
    blocked = unchartable_reason(profile)
    chart = kpi = None
    chart_detail: dict[str, Any]
    if blocked is not None:
        planned = plan_kpi(profile, columns, result.rows) if result.row_count == 1 else None
        if planned is not None:
            kpi = planned.model_dump(mode="json")
            chart_detail = {"status": "DONE", "detail": "big number"}
        else:
            chart_detail = {"status": "SKIPPED", "detail": blocked}
    else:
        suggestion = ChartIntent.model_validate(proposed) if proposed else ChartIntent(chart_type="none")
        plan = plan_chart(profile, suggestion)
        if plan.intent is None:
            chart_detail = {"status": "SKIPPED", "detail": plan.reason or "No chart fits"}
        else:
            chart = compile_vega_lite(plan.intent, profile, columns, result.rows)
            chart_detail = {"status": "DONE", "detail": f"{plan.intent.chart_type} chart ({plan.source})"}

    options = [asdict(o) for o in chart_options(profile)]
    redraws: dict[str, Any] = {}
    for option in options:
        intent = candidate_intent(profile, option["chart_type"])
        plan = plan_chart(profile, intent) if intent is not None else None
        if plan is None or plan.intent is None or plan.intent.chart_type != option["chart_type"]:
            redraws[option["chart_type"]] = {"spec": None, "chart_type": "none",
                                             "reason": option["reason"] or "This result cannot be drawn that way."}
        else:
            redraws[option["chart_type"]] = {
                "spec": compile_vega_lite(plan.intent, profile, columns, result.rows),
                "chart_type": plan.intent.chart_type, "reason": None,
            }
    return {"chart": chart, "kpi": kpi, "chart_step": chart_detail, "options": options, "redraws": redraws}


# ── one deep analysis ──────────────────────────────────────────────────────
async def run_deep(
    q: Q.DeepQuestion, snapshot: dict[str, Any], dialect: str, policy_name: str, conn: Any, today: date,
) -> dict[str, Any]:
    """A deep run, step by step, through the pieces the deep graph is made of.

    Each step's statement goes through the real guard and the real connector,
    and is closed by `evidence.close_step` — the same disclosure under the
    connection's policy and the same `app.analysis` computation the `compute`
    node runs — so the plan panel shows the backend's own summaries. The
    answer is checked by `reports.checks.check_claims` against each step's
    `evidence.pool`, which is exactly what `synthesize` checks it against: a
    sentence citing step 3 may only state figures step 3's writer was given.

    The answer is built for every prefix of the plan, because *Answer now*
    can stop it after any step, and each of those is checked the same way.
    """
    policy = policy_for(snapshot, dialect)
    planned: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    evidences: list[StepEvidence] = []
    results: list[Q.Result] = []
    steps: list[dict[str, Any]] = []
    for i, s in enumerate(q.steps):
        assert all(0 <= d < i for d in s.depends_on), f"{q.id}: step {i + 1} depends forward"
        step = PlanStep(question=s.question, intent=s.intent, why=s.why, tool=s.tool, depends_on=s.depends_on)
        first = step
        if s.planned:
            # `_revise` only reads a step's dependencies, so one with none has
            # nothing to be sharpened by.
            assert s.depends_on, f"{q.id}: step {i + 1} is revised but depends on nothing"
            first = step.model_copy(update={"question": s.planned, "why": s.planned_why or s.why})
            revisions.append(PlanRevision(index=i, replaced=first, by=step).model_dump(mode="json"))
        planned.append(first.model_dump(mode="json"))

        report, executable = guard(s.sql, policy)
        if report.status != "VALID":
            raise AssertionError(f"{q.id}: step {i + 1} was rejected: {report.to_feedback()}")
        exec_sql = freeze(executable, today, dialect)
        result = await conn.execute(exec_sql, max_rows=MAX_ROWS, statement_timeout_ms=30000)
        scanned = await conn.explain(exec_sql)
        findings = inspect_result(
            question=s.question, sql=executable, dialect=dialect, tables=snapshot["tables"],
            row_count=result.row_count, column_count=len(result.columns), truncated=result.truncated,
        )
        retryable = [f.code for f in findings if f.retry]
        if retryable:
            raise AssertionError(f"{q.id}: step {i + 1}'s result checks would retry on {retryable}")
        codes = [f.code for f in findings]

        # Attempts are numbered across the whole run, as `generated_queries` is.
        attempt_no = i + 1
        execution = ExecutionResult(
            columns=[ResultColumn(name=c.name, db_type=c.db_type, semantic_type=c.semantic_type) for c in result.columns],
            rows=result.rows, row_count=result.row_count, truncated=result.truncated,
            duration_ms=result.duration_ms, rows_scanned_estimate=scanned,
        )
        evidence = ev.close_step(
            StepEvidence(index=i, step=step, first_attempt=i, last_attempt=attempt_no,
                         execution=execution, status="DONE"),
            policy_name,
        )
        evidences.append(evidence)
        results.append(Q.Result([c.name for c in result.columns], result.rows, codes, evidence.computed))
        steps.append({
            "evidence": ev.step_payload(evidence, executable),
            "attempt": {
                "attempt_no": attempt_no,
                "raw_sql": s.sql,
                "rewritten_sql": executable,
                "validation_status": report.status,
                "validation_report": report.model_dump(mode="json"),
                "referenced_tables": sorted(report.referenced_tables),
            },
            "result": result_record(result, scanned),
            "inspect_step": {"status": "DONE", "detail": f"Noted: {', '.join(codes)}" if codes else "No issues found"},
            **charts_for(result, s.chart),
        })

    answers: list[dict[str, Any]] = []
    for ran in range(1, len(q.steps) + 1):
        prose = q.answer(results[:ran])
        clean, claims = checks.parse_claims(prose, results=ran)
        blocks = [ev.narration(e) for e in evidences[:ran]]
        context = " ".join([q.text, *(e.step.question for e in evidences[:ran])])
        check = checks.check_claims(claims, [ev.pool(b) for b in blocks], context=context)
        uncited = [c.text for c in check.claims if c.cites is None]
        unsupported = [(c.text, [f.model_dump(mode="json") for f in c.unsupported]) for c in check.claims if c.unsupported]
        if uncited or unsupported:
            raise AssertionError(f"{q.id} after {ran} steps: uncited {uncited}, unsupported {unsupported}")
        answers.append({
            "steps": ran,
            "streamed": prose,
            "answer": clean,
            "claims": [c.model_dump(mode="json") for c in check.claims],
            "traceable": check.traceable,
        })

    return {
        "id": q.id, "connection": q.connection, "question": q.text, "aliases": q.aliases,
        "followups": q.followups, "shows": q.shows,
        "plan": {"restatement": q.restatement, "steps": planned, "stop_when": q.stop_when},
        "revisions": revisions,
        "steps": steps,
        "answers": answers,
    }


# ── sections and the template editor ─────────────────────────────────────
def section_sets(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What the Sections tab reads: nothing saved, and the proposal it asks for.

    `SectionService._shape` and `sections.propose` are pure over a snapshot, so
    this is the backend's own division of the schema, not a guess at one.
    """
    from app.pipeline import sections as algo
    from app.pipeline.nodes import retrieve_budget_chars
    from app.services.section_service import SectionService

    doc = {**snapshot, "version": SNAPSHOT_VERSION, "synced_at": None}
    service = SectionService(None)  # type: ignore[arg-type]  # _shape reads no database
    proposal = algo.propose(doc, semantic=None, budget_chars=retrieve_budget_chars())

    def plain(result: Any) -> dict[str, Any]:
        out = asdict(result)
        out["catalog"] = [{"table": t, "chars": c} for t, c in result.catalog]
        for section in out["sections"]:
            section["fit"] = str(section["fit"])
        return out

    return {
        "read": plain(service._shape(doc, [], saved=False, since=set())),
        "proposal": plain(service._shape(
            doc,
            [(None, s.name, s.description, list(s.tables), "PROPOSED", None) for s in proposal.sections],
            saved=False,
        )),
    }


def template_checks(sql: str, question: str, snapshot: dict[str, Any], dialect: str) -> dict[str, Any]:
    """`POST …/knowledge/templates/check` for one recorded statement.

    The editor asks with no literals accepted, adopts the backend's suggested
    ones, and asks again on every tick. Every subset of the eligible literals is
    answered here, keyed by the sorted names, so every tick the editor can make
    gets the backend's own verdict.
    """
    from itertools import combinations

    from app.knowledge.models import KnowledgeTemplate
    from app.knowledge.normalize import slots
    from app.knowledge.params import parameterize, propose_params
    from app.knowledge.validate import policy_from_tables, validate_template

    policy = policy_from_tables(snapshot["tables"], dialect=dialect, max_rows=MAX_ROWS)
    proposals = propose_params(sql, dialect=dialect, tables=snapshot["tables"])
    eligible = sorted({p.name for p in proposals if p.eligible})[:5]
    answers: dict[str, Any] = {}
    for size in range(len(eligible) + 1):
        for accept in combinations(eligible, size):
            rewritten, declared = (sql, []) if not accept else parameterize(
                sql, set(accept), dialect=dialect, tables=snapshot["tables"])
            verdict = validate_template(KnowledgeTemplate(question=question, sql=rewritten, params=declared), policy)
            answers[",".join(accept)] = {
                "valid": verdict.valid,
                "issue": verdict.message,
                "issues": [i.model_dump(mode="json") for i in verdict.report.errors],
                "referenced_tables": verdict.referenced_tables,
                "proposals": [p.model_dump(mode="json") for p in proposals],
                "sql": rewritten,
                "params": [p.model_dump(mode="json") for p in declared],
                "question_slots": slots(question),
            }
    return {"sql": sql, "question": question, "answers": answers}


# ── catalogs the admin screens render ─────────────────────────────────────
def catalogs() -> dict[str, Any]:
    from app.api.v1.roles import _GROUPS, _LABELS
    from app.domain.value_objects.authz import (
        PRIVILEGE_LABELS, PRIVILEGE_MEANINGS, SHARE_LEVELS, Capability, Privilege,
    )
    from app.domain.value_objects.llm_params import full_catalog

    return {
        "capabilities": [{"name": str(c), "group": _GROUPS[c], "label": _LABELS[c]} for c in Capability],
        "privileges": {str(t): {str(p): m for p, m in ms.items()} for t, ms in PRIVILEGE_MEANINGS.items()},
        "labels": {str(t): {str(p): PRIVILEGE_LABELS[t][p] for p in Privilege} for t in PRIVILEGE_LABELS},
        "levels": {str(t): [str(p) for p in SHARE_LEVELS[t]] for t in SHARE_LEVELS},
        "parameters": full_catalog(),
    }


# ── output ─────────────────────────────────────────────────────────────────
def _json(value: Any) -> str:
    def default(v: Any) -> Any:
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        raise TypeError(type(v))
    return json.dumps(value, ensure_ascii=False, indent=2, default=default)


HEADER = """// GENERATED by demo/scripts/build.py — do not edit by hand.
// Re-run demo/scripts/build-fixtures.sh after changing questions.py,
// warehouse.py or DEMO_TODAY. See demo/README.md.
"""


def write(name: str, body: str) -> None:
    (OUT / name).write_text(HEADER + body)
    print(f"  wrote mock/fixtures/{name}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg", required=True, help="host:port of the scratch PostgreSQL")
    parser.add_argument("--mysql", required=True, help="host:port of the scratch MySQL with Sakila")
    args = parser.parse_args()
    today = demo_today()
    pg_host, pg_port = args.pg.split(":")
    my_host, my_port = args.mysql.split(":")
    endpoints = {"sales": (pg_host, int(pg_port)), "sakila": (my_host, int(my_port))}

    print(f"DEMO_TODAY {today}")
    print("loading the sales warehouse")
    await load_sales(pg_host, int(pg_port), today)

    snapshots: dict[str, dict[str, Any]] = {}
    sections: dict[str, dict[str, Any]] = {}
    answers: list[dict[str, Any]] = []
    deeps: list[dict[str, Any]] = []
    for name, spec in CONNECTIONS.items():
        conn = connector(name, *endpoints[name])
        try:
            print(f"syncing {name}")
            snap = await conn.introspect(schema_allowlist=spec["schemas"], hints=HintBudget.from_policy(spec["policy"]))
            snapshots[name] = snapshot_dict(snap)
            sections[name] = section_sets(snapshots[name])
            print(f"  {len(snap.tables)} tables, {len(snap.relationships)} foreign keys")
            for q in [q for q in Q.QUESTIONS if q.connection == name]:
                answers.append(await run_question(q, snapshots[name], spec["dialect"], conn, today))
                a = answers[-1]
                rows = a["result"]["row_count"] if a["result"] else "—"
                chart = (a.get("chart_step") or {}).get("detail", "")
                print(f"  ✓ {q.id:22} rows={rows!s:>4}  {chart}")
            for d in [d for d in Q.DEEP_QUESTIONS if d.connection == name]:
                deeps.append(await run_deep(d, snapshots[name], spec["dialect"], spec["policy"], conn, today))
                last = deeps[-1]
                traced = last["answers"][-1]["traceable"]
                print(f"  ✓ {d.id:22} steps={len(d.steps)}  revised={len(last['revisions'])}  "
                      f"traceable={'—' if traced is None else f'{traced:.0%}'}")
        finally:
            await conn.close()

    ids = {a["id"] for a in answers} | {d["id"] for d in deeps}
    for a in [*answers, *deeps]:
        missing = [f for f in a["followups"] if f not in ids]
        assert not missing, f"{a['id']} suggests unknown questions {missing}"
    assert all(g in ids for g in Q.GUIDE) and all(h in ids for h, *_ in Q.HISTORY)

    write("schema.generated.ts", (
        "import type { SchemaColumn, SchemaSnapshot, SchemaTable, SectionSet } from '../../../src/api/types'\n\n"
        f"export const BUILT_FOR = '{today.isoformat()}'\n\n"
        "/** A column as the connector stores it: the SPA's type, plus the policy-gated hints it does not read. */\n"
        "type WireTable = Omit<SchemaTable, 'columns'> & { columns: (SchemaColumn & Record<string, unknown>)[] }\n\n"
        "/** Each connection's snapshot, as the real connector synced it. */\n"
        "export const SNAPSHOTS: Record<'sales' | 'sakila', Omit<SchemaSnapshot, 'version' | 'synced_at' | 'tables'> & "
        "{ tables: WireTable[]; server_version: string | null }> = "
        + _json(snapshots) + "\n\n"
        f"export const SNAPSHOT_VERSION = {SNAPSHOT_VERSION}\n\n"
        "/** The Sections tab: nothing saved, and the backend's own proposal. */\n"
        "export const SECTIONS: Record<'sales' | 'sakila', { read: SectionSet; proposal: SectionSet }> = "
        + _json(sections) + "\n"
    ))
    write("answers.generated.ts", (
        "import type { ScriptedAnswer } from '../script-types'\n\n"
        f"export const BUILT_FOR = '{today.isoformat()}'\n\n"
        "/** Every scripted answer, from a real run of its statement. */\n"
        f"export const ANSWERS: ScriptedAnswer[] = {_json(answers)}\n\n"
        f"/** The guide's questions, in the brief's order. */\nexport const GUIDE = {_json(Q.GUIDE)}\n\n"
        "/** The sidebar's conversations: question id, days before DEMO_TODAY, hour of day. */\n"
        f"export const HISTORY: [string, number, number][] = {_json([list(h) for h in Q.HISTORY])}\n"
    ))
    write("deep.generated.ts", (
        "import type { ScriptedDeep } from '../script-types'\n\n"
        f"export const BUILT_FOR = '{today.isoformat()}'\n\n"
        "/** Every scripted deep analysis, from a real run of each step's statement. */\n"
        f"export const DEEP: ScriptedDeep[] = {_json(deeps)}\n"
    ))
    write("catalog.generated.ts", (
        "import type { CapabilityEntry, ParameterCatalog } from '../../../src/api/types'\n\n"
        "/** The backend's own vocabularies, dumped from its code. */\n"
        f"export const CATALOG: {{\n  capabilities: CapabilityEntry[]\n  privileges: Record<string, Record<string, string>>\n"
        f"  labels: Record<string, Record<string, string>>\n  levels: Record<string, string[]>\n"
        f"  parameters: ParameterCatalog[]\n}} = {_json(catalogs())}\n"
    ))


if __name__ == "__main__":
    asyncio.run(main())
