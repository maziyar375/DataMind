"""The eval harness.

    python -m app.eval.runner --suite sales_v1 --llm-config <id>
                              [--limit N] [--tag join] [--json]

Runs the **real pipeline** (route -> retrieve -> generate -> validate -> execute
-> present -> chart) — the same `AnalyticsPipeline`, `sqlguard`, and connector
the request path uses — against a fixture the runner spins up with
testcontainers. It scores every question, persists the scorecard to
`eval_runs` / `eval_results`, and prints a per-tag breakdown.

The core loop (`run_suite` / `evaluate_record`) takes its gateway and connector
as arguments, so it is driven in tests by a scripted fake gateway against a real
fixture — deterministic, free, and still exercising the real pipeline. The CLI
wires the real `LiteLLMGateway` and a fresh container.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from time import perf_counter
from typing import Any
from uuid import UUID

from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.core.errors import RunTimeoutError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector, SchemaSnapshot
from app.domain.ports.llm import LLMGateway, ProviderCapabilities, ResolvedLLM
from app.domain.value_objects import DatabaseKind, StepName, StepStatus
from app.eval import dataset, metrics
from app.eval.dataset import FixtureSpec, GoldRecord, NegativeRecord
from app.eval.metrics import (
    OUTCOME_ERROR,
    OUTCOME_EXEC_FAILED,
    OUTCOME_MATCH,
    OUTCOME_MISMATCH,
    OUTCOME_NO_SQL,
    OUTCOME_VALIDATION_FAILED,
    RecordOutcome,
)
from app.infra.connectors.factory import build_connector
from app.infra.crypto.aesgcm_box import AesGcmSecretBox
from app.infra.db.models import EvalResult, EvalRun, LlmConfig
from app.infra.db.session import dispose_engine, get_sessionmaker
from app.infra.llm.litellm_gateway import LiteLLMGateway, estimate_cost_usd
from app.pipeline.nodes import NodeDeps
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.state import RunState
from app.sqlguard import GuardPolicy

log = get_logger(__name__)

_NON_ANALYTICAL = {"METADATA", "CHITCHAT", "UNSUPPORTED"}
# Allow up to three attempts so the repair distribution is observable.
_MAX_REPAIRS = 2


# ── snapshot / policy construction (mirrors the request path) ────────────────


def snapshot_to_dict(snapshot: SchemaSnapshot) -> dict[str, Any]:
    """Same shape `connections.sync` stores and `run_service` feeds the pipeline."""
    return {
        "dialect": snapshot.dialect,
        "tables": [
            {
                "schema": t.schema,
                "name": t.name,
                "approx_row_count": t.approx_row_count,
                "columns": [
                    {
                        "name": c.name, "data_type": c.data_type,
                        "nullable": c.nullable,
                        "is_primary_key": c.is_primary_key,
                        "is_foreign_key": c.is_foreign_key,
                        "references": c.references,
                    }
                    for c in t.columns
                ],
            }
            for t in snapshot.tables
        ],
        "relationships": [
            {
                "from_table": r.from_table, "from_column": r.from_column,
                "to_table": r.to_table, "to_column": r.to_column,
            }
            for r in snapshot.relationships
        ],
    }


def build_policy(snapshot: dict[str, Any], sqlglot_dialect: str, max_rows: int) -> GuardPolicy:
    allowed_tables: set[str] = set()
    allowed_columns: dict[str, set[str]] = {}
    for table in snapshot.get("tables", []):
        qualified = f"{table['schema']}.{table['name']}".lower()
        allowed_tables.add(qualified)
        allowed_columns[qualified] = {c["name"].lower() for c in table.get("columns", [])}
    return GuardPolicy(
        dialect=sqlglot_dialect,
        max_rows=max_rows,
        allowed_tables=allowed_tables,
        allowed_columns=allowed_columns,
    )


def resolve_llm(config: LlmConfig, box: AesGcmSecretBox) -> ResolvedLLM:
    api_key = ""
    if config.encrypted_api_key:
        api_key = box.decrypt(config.encrypted_api_key, aad=f"llm_config:{config.id}")
    caps = config.capabilities or {}
    return ResolvedLLM(
        config_id=config.id,
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        api_key=api_key,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        capabilities=ProviderCapabilities(
            supports_structured_output=caps.get("supports_structured_output", False),
            supports_streaming=caps.get("supports_streaming", True),
        ),
    )


# ── scoring one record ──────────────────────────────────────────────────────


async def _run_gold(
    connector: DatabaseConnector, sql: str, settings: Settings
) -> tuple[list[list[Any]], int]:
    result = await connector.execute(
        sql, max_rows=settings.hard_row_cap,
        statement_timeout_ms=settings.default_statement_timeout_ms,
    )
    return result.rows, result.row_count


async def evaluate_record(
    record: GoldRecord,
    *,
    gateway: LLMGateway,
    llm: ResolvedLLM,
    connector: DatabaseConnector,
    snapshot: dict[str, Any],
    policy: GuardPolicy,
    settings: Settings,
    model_name: str,
    with_cost: bool = True,
) -> RecordOutcome:
    o = RecordOutcome(
        record_id=record.id, tags=record.tags, difficulty=record.difficulty,
        model=model_name, gold_sql=record.gold_sql, expected_tables=record.expected_tables,
    )

    validate_ms = 0

    async def on_step(seq: int, name: str, status: str, detail: str | None, ms: int) -> None:
        nonlocal validate_ms
        if name == StepName.VALIDATE and status != StepStatus.RUNNING:
            validate_ms += ms

    async def emit(_type: str, _data: dict[str, Any]) -> None:
        return None

    state = RunState(
        run_id=uuid.uuid4(), conversation_id=uuid.uuid4(), owner_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), question=record.question, dialect=connector.dialect,
        max_rows=settings.default_max_rows, max_repairs=_MAX_REPAIRS,
        statement_timeout_ms=settings.default_statement_timeout_ms,
        disclosure_policy="SAMPLE",
        deadline_at=utcnow() + timedelta(seconds=settings.run_deadline_seconds),
    )
    deps = NodeDeps(
        llm_gateway=gateway, llm=llm, connector=connector, snapshot=snapshot,
        history=[], policy=policy, emit=emit,
    )
    pipeline = AnalyticsPipeline(on_step=on_step)

    t0 = perf_counter()
    try:
        state = await pipeline.run(state, deps)
    except RunTimeoutError:
        o.outcome = OUTCOME_ERROR
        o.failure_reason = "run exceeded its deadline"
        o.total_ms = int((perf_counter() - t0) * 1000)
        return o
    except Exception as err:  # noqa: BLE001 - a harness must survive one bad question
        o.outcome = OUTCOME_ERROR
        o.failure_reason = f"pipeline crashed: {str(err)[:300]}"
        o.total_ms = int((perf_counter() - t0) * 1000)
        return o
    o.total_ms = int((perf_counter() - t0) * 1000)

    o.intent = state.intent
    o.attempts = len(state.attempts)
    o.repair_count = state.repair_count
    o.llm_ms = state.llm_latency_ms
    o.db_ms = state.db_latency_ms
    o.validate_ms = validate_ms
    o.prompt_tokens = state.prompt_tokens
    o.completion_tokens = state.completion_tokens
    if with_cost and (state.prompt_tokens or state.completion_tokens):
        o.cost_usd = estimate_cost_usd(model_name, state.prompt_tokens, state.completion_tokens)

    ctx_tables = state.context.tables if state.context else []
    retrieved = [f"{t['schema']}.{t['name']}" for t in ctx_tables]
    o.retrieved_tables = retrieved
    o.retrieval_recall = metrics.retrieval_recall(record.expected_tables, retrieved)
    o.retrieval_hit = o.retrieval_recall >= 1.0 - 1e-9

    if state.attempts:
        last = state.attempts[-1]
        o.candidate_sql = last.rewritten_sql or last.raw_sql
        o.parse_ok = not any(i.rule_id == "E_PARSE" for i in last.report.issues)
        o.validated_ok = any(a.report.status == "VALID" for a in state.attempts)
        o.policy_violations = sorted(
            {
                i.rule_id
                for a in state.attempts if a.report.status == "REJECTED"
                for i in a.report.errors
            }
        )
    o.execution_ok = state.execution is not None
    o.exact_match = metrics.exact_match(record.gold_sql, o.candidate_sql)

    if state.intent in _NON_ANALYTICAL:
        o.outcome = OUTCOME_NO_SQL
        o.failure_reason = f"routed as {state.intent}; no SQL produced"
        return o

    if not o.execution_ok:
        if o.validated_ok:
            o.outcome = OUTCOME_EXEC_FAILED
            o.failure_reason = _err_text(state) or "valid SQL was rejected by the database"
        elif state.attempts:
            o.outcome = OUTCOME_VALIDATION_FAILED
            rules = ",".join(o.policy_violations) or "unknown"
            o.failure_reason = f"guard rejected all attempts [{rules}]"
        else:
            o.outcome = OUTCOME_ERROR
            o.failure_reason = _err_text(state) or "no SQL was produced"
        return o

    assert state.execution is not None
    o.candidate_row_count = state.execution.row_count
    o.succeeded_on_attempt = len(state.attempts)

    try:
        gold_rows, gold_count = await _run_gold(connector, record.gold_sql, settings)
    except Exception as err:  # noqa: BLE001
        o.outcome = OUTCOME_ERROR
        o.failure_reason = f"gold SQL failed to execute: {str(err)[:300]}"
        return o
    o.gold_row_count = gold_count

    if metrics.result_sets_match(gold_rows, state.execution.rows, record.result_equivalence):
        o.outcome = OUTCOME_MATCH
        o.execution_match = True
    else:
        o.outcome = OUTCOME_MISMATCH
        o.failure_reason = (
            f"result mismatch: gold {gold_count} rows "
            f"vs candidate {state.execution.row_count} rows"
        )
    return o


def _err_text(state: RunState) -> str | None:
    if state.error is None:
        return None
    return state.error.hint or state.error.message


async def run_suite(
    records: Sequence[GoldRecord],
    *,
    gateway: LLMGateway,
    llm: ResolvedLLM,
    connector: DatabaseConnector,
    snapshot: dict[str, Any],
    policy: GuardPolicy,
    settings: Settings,
    model_name: str,
    with_cost: bool = True,
    progress: bool = False,
) -> list[RecordOutcome]:
    outcomes: list[RecordOutcome] = []
    for i, record in enumerate(records, 1):
        outcome = await evaluate_record(
            record, gateway=gateway, llm=llm, connector=connector, snapshot=snapshot,
            policy=policy, settings=settings, model_name=model_name, with_cost=with_cost,
        )
        outcomes.append(outcome)
        if progress:
            mark = "✓" if outcome.is_success else "✗"
            print(
                f"  [{i:>2}/{len(records)}] {mark} {record.id:<12} {outcome.outcome}"
                + (f" — {outcome.failure_reason}" if outcome.failure_reason else ""),
                file=sys.stderr,
            )
    return outcomes


# ── negative suite (route accuracy; must never emit SQL) ─────────────────────


async def evaluate_negative(
    record: NegativeRecord,
    *,
    gateway: LLMGateway,
    llm: ResolvedLLM,
    connector: DatabaseConnector,
    snapshot: dict[str, Any],
    policy: GuardPolicy,
    settings: Settings,
) -> RecordOutcome:
    o = RecordOutcome(record_id=record.id, tags=[record.category], difficulty="n/a")

    async def emit(_t: str, _d: dict[str, Any]) -> None:
        return None

    async def on_step(*_: Any) -> None:
        return None

    state = RunState(
        run_id=uuid.uuid4(), conversation_id=uuid.uuid4(), owner_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), question=record.question, dialect=connector.dialect,
        max_rows=settings.default_max_rows, max_repairs=_MAX_REPAIRS,
        statement_timeout_ms=settings.default_statement_timeout_ms,
        disclosure_policy="NONE",
        deadline_at=utcnow() + timedelta(seconds=settings.run_deadline_seconds),
    )
    deps = NodeDeps(
        llm_gateway=gateway, llm=llm, connector=connector, snapshot=snapshot,
        history=[], policy=policy, emit=emit,
    )
    try:
        state = await AnalyticsPipeline(on_step=on_step).run(state, deps)
    except Exception as err:  # noqa: BLE001
        o.outcome = "ROUTE_WRONG"
        o.failure_reason = f"pipeline crashed: {str(err)[:200]}"
        return o

    o.intent = state.intent
    o.attempts = len(state.attempts)
    o.candidate_sql = state.attempts[-1].raw_sql if state.attempts else None
    produced_sql = state.execution is not None
    route_ok = state.intent == record.expected_route
    if produced_sql:
        o.outcome = "SQL_LEAK"
        o.failure_reason = f"expected no SQL but a query ran (routed {state.intent})"
    elif route_ok:
        o.outcome = "ROUTE_OK"
    else:
        o.outcome = "ROUTE_WRONG"
        o.failure_reason = f"expected route {record.expected_route}, got {state.intent}"
    return o


def aggregate_negatives(outcomes: list[RecordOutcome]) -> dict[str, Any]:
    n = len(outcomes)
    route_ok = sum(1 for o in outcomes if o.outcome == "ROUTE_OK")
    leaks = sum(1 for o in outcomes if o.outcome == "SQL_LEAK")
    from collections import Counter

    by_cat: dict[str, dict[str, int]] = {}
    for o in outcomes:
        cat = o.tags[0] if o.tags else "?"
        d = by_cat.setdefault(cat, {"n": 0, "route_ok": 0})
        d["n"] += 1
        d["route_ok"] += int(o.outcome == "ROUTE_OK")
    return {
        "n": n,
        "route_accuracy": round(route_ok / n, 4) if n else 0.0,
        "sql_leak_count": leaks,
        "by_category": by_cat,
        "outcome_counts": dict(Counter(o.outcome for o in outcomes)),
    }


# ── fixture lifecycle (testcontainers) ───────────────────────────────────────


@asynccontextmanager
async def spin_fixture(spec: FixtureSpec) -> AsyncIterator[dict[str, Any]]:
    """Boot a throwaway container, load the seed, yield read-only connection params.

    testcontainers is a dev-only dependency, imported lazily so importing this
    module never requires it.
    """
    if spec.dialect != "postgres":
        raise NotImplementedError(f"eval fixture for {spec.dialect} not wired yet")
    import asyncpg
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        spec.image, username="postgres", password="postgres", dbname=spec.database  # noqa: S106
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(5432))
        seed_sql = spec.seed_path.read_text()
        admin = await asyncpg.connect(
            host=host, port=port, user="postgres",
            password="postgres", database=spec.database,  # noqa: S106
        )
        try:
            await admin.execute(seed_sql)
        finally:
            await admin.close()
        # Hand back the read-only role the seed created — the same posture prod uses.
        yield {
            "host": host, "port": port, "database": spec.database,
            "username": "analytics_ro", "password": "analytics_ro",
        }
    finally:
        container.stop()


# ── persistence ──────────────────────────────────────────────────────────────


async def persist(
    *,
    suite_name: str,
    suite_version: str,
    connection_fixture: str,
    llm_config_id: UUID | None,
    model_snapshot: dict[str, Any],
    prompt_version: str,
    outcomes: list[RecordOutcome],
    report_dict: dict[str, Any],
    records_by_id: dict[str, GoldRecord],
    started_at: Any,
    finished_at: Any,
) -> UUID:
    sm = get_sessionmaker()
    async with sm() as session:
        run = EvalRun(
            id=uuid.uuid4(),
            suite=suite_name,
            suite_version=suite_version,
            connection_fixture=connection_fixture,
            llm_config_id=llm_config_id,
            model_snapshot=model_snapshot,
            prompt_version=prompt_version,
            git_sha=_git_sha(),
            total=len(outcomes),
            metrics=report_dict,
            started_at=started_at,
            finished_at=finished_at,
        )
        session.add(run)
        await session.flush()
        for o in outcomes:
            rec = records_by_id.get(o.record_id)
            session.add(
                EvalResult(
                    id=uuid.uuid4(),
                    eval_run_id=run.id,
                    record_id=o.record_id,
                    question=rec.question if rec else "",
                    tags=o.tags,
                    difficulty=o.difficulty,
                    outcome=o.outcome,
                    intent=o.intent,
                    expected_tables=o.expected_tables,
                    retrieved_tables=o.retrieved_tables,
                    retrieval_recall=o.retrieval_recall,
                    retrieval_hit=o.retrieval_hit,
                    gold_sql=o.gold_sql or None,
                    candidate_sql=o.candidate_sql,
                    gold_row_count=o.gold_row_count,
                    candidate_row_count=o.candidate_row_count,
                    parse_ok=o.parse_ok,
                    validated_ok=o.validated_ok,
                    execution_ok=o.execution_ok,
                    execution_match=o.execution_match,
                    exact_match=o.exact_match,
                    policy_violations=o.policy_violations,
                    attempts=o.attempts,
                    repair_count=o.repair_count,
                    succeeded_on_attempt=o.succeeded_on_attempt,
                    llm_ms=o.llm_ms,
                    validate_ms=o.validate_ms,
                    db_ms=o.db_ms,
                    total_ms=o.total_ms,
                    prompt_tokens=o.prompt_tokens,
                    completion_tokens=o.completion_tokens,
                    cost_usd=o.cost_usd,
                    failure_reason=o.failure_reason,
                )
            )
        await session.commit()
        return run.id


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git on PATH, dev tooling
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return None


# ── CLI ──────────────────────────────────────────────────────────────────────


def _select(records: list[Any], *, limit: int | None, tag: str | None) -> list[Any]:
    out = records
    if tag:
        out = [r for r in out if tag in getattr(r, "tags", [])]
    if limit is not None:
        out = out[:limit]
    return out


async def _amain(args: argparse.Namespace) -> int:
    settings = get_settings()
    negative = dataset.is_negative_suite(args.suite)

    # Resolve the model config from the app database and decrypt its key.
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            config = await session.get(LlmConfig, UUID(args.llm_config))
        except ValueError:
            print(f"--llm-config must be a UUID, got {args.llm_config!r}", file=sys.stderr)
            return 2
        if config is None:
            print(f"No llm_config with id {args.llm_config}", file=sys.stderr)
            return 2
        box = AesGcmSecretBox(
            settings.secret_box_key.get_secret_value(), settings.secret_box_key_version
        )
        llm = resolve_llm(config, box)
        model_snapshot = llm.snapshot()
        config_id = config.id
    model_name = llm.model

    if negative:
        suite: dataset.GoldSuite | dataset.NegativeSuite
        suite = dataset.load_negative_suite(args.suite)
        records: list[Any] = _select(list(suite.records), limit=args.limit, tag=args.tag)
    else:
        suite = dataset.load_gold_suite(args.suite)
        records = _select(list(suite.records), limit=args.limit, tag=args.tag)

    if not records:
        print("No records selected (check --tag / --limit).", file=sys.stderr)
        return 1

    spec = dataset.fixture_for(records[0].connection_fixture)
    gateway = LiteLLMGateway(timeout_seconds=settings.llm_request_timeout_seconds)

    started_at = utcnow()
    print(f"Spinning fixture {spec.name} ({spec.image}) …", file=sys.stderr)
    async with spin_fixture(spec) as params:
        connector = build_connector(kind=spec.dialect, **params)
        try:
            snap = snapshot_to_dict(
                await connector.introspect(schema_allowlist=list(spec.schema_allowlist))
            )
            policy = build_policy(
                snap, DatabaseKind(spec.dialect).sqlglot_dialect, settings.default_max_rows
            )
            print(
                f"Running {len(records)} question(s) through the real pipeline …",
                file=sys.stderr,
            )
            if negative:
                neg_outcomes = [
                    await evaluate_negative(
                        r, gateway=gateway, llm=llm, connector=connector,
                        snapshot=snap, policy=policy, settings=settings,
                    )
                    for r in records
                ]
                outcomes = neg_outcomes
                report_dict = aggregate_negatives(neg_outcomes)
                rendered = json.dumps(report_dict, indent=2)
            else:
                outcomes = await run_suite(
                    records, gateway=gateway, llm=llm, connector=connector, snapshot=snap,
                    policy=policy, settings=settings, model_name=model_name, progress=True,
                )
                report = metrics.aggregate(outcomes)
                report_dict = metrics.report_to_dict(report)
                rendered = (
                    json.dumps(report_dict, indent=2)
                    if args.json
                    else metrics.format_report(
                        report, title=f"{suite.suite} {suite.version} · {model_name}"
                    )
                )
        finally:
            await connector.close()
    finished_at = utcnow()

    eval_run_id = await persist(
        suite_name=suite.suite,
        suite_version=suite.version,
        connection_fixture=records[0].connection_fixture,
        llm_config_id=config_id,
        model_snapshot=model_snapshot,
        prompt_version=settings.prompt_version,
        outcomes=outcomes,
        report_dict=report_dict,
        records_by_id={r.id: r for r in records} if not negative else {},
        started_at=started_at,
        finished_at=finished_at,
    )
    await dispose_engine()

    print(rendered)
    print(f"\neval_run: {eval_run_id}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.runner", description=__doc__)
    parser.add_argument("--suite", required=True, help="suite name, e.g. sales_v1")
    parser.add_argument("--llm-config", required=True, help="llm_configs UUID from the app DB")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N records")
    parser.add_argument("--tag", default=None, help="run only records carrying this tag")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
