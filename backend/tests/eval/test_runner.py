"""End-to-end harness test: the REAL pipeline (route→…→execute→present→chart),
real sqlguard, real fixture — driven by a scripted fake gateway.

The fake model lets us assert the harness scores a *perfect* model as MATCH, a
*wrong* model as MISMATCH, and *invalid* SQL as a guard rejection with a repair
loop — deterministically and without a paid model. Retrieval recall is measured
independently, so the bridge questions show a recall gap even when a perfect
model still MATCHes.

Live checks connect to `SALES_FIXTURE_DSN` (default: the Compose demo on :5433)
and skip when no wide-schema fixture is reachable, so `make test` stays green.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import pytest

from app.core.config import get_settings
from app.domain.ports.llm import (
    ChatMessage,
    Completion,
    ProviderCapabilities,
    ResolvedLLM,
    StreamChunk,
)
from app.domain.value_objects import DatabaseKind
from app.eval import dataset, metrics, runner
from app.infra.connectors.factory import build_connector
from app.pipeline import nodes
from app.pipeline.contracts import SqlProposal

DSN = os.environ.get(
    "SALES_FIXTURE_DSN", "postgresql://analytics_ro:analytics_ro@localhost:5433/sales"
)
SETTINGS = get_settings()
LLM = ResolvedLLM(
    config_id="fake", provider="Fake", model="fake-model", base_url=None,
    capabilities=ProviderCapabilities(supports_structured_output=False),
)


class FakeGateway:
    """A scripted LLMGateway. `plan` maps a question to the SQL(s) it 'writes',
    one per attempt (so a repair path can be scripted)."""

    def __init__(self, *, route: str = "ANALYTICAL", plan: dict[str, list[str]] | None = None) -> None:
        self.route = route
        self.plan = plan or {}
        self._calls: dict[str, int] = {}
        # Every SQL-producing prompt this gateway was sent, so a test can ask
        # what actually reached the model rather than what was configured.
        self.prompts: list[str] = []

    def _question(self, messages: Sequence[ChatMessage]) -> str | None:
        blob = "\n".join(m.content for m in messages)
        for q in self.plan:
            if q in blob:
                return q
        return None

    async def complete(self, llm: ResolvedLLM, messages: Sequence[ChatMessage]) -> Completion:
        return Completion(text=self.route, prompt_tokens=8, completion_tokens=1, latency_ms=1)

    async def structured(self, llm: ResolvedLLM, messages: Sequence[ChatMessage], schema: type) -> Any:
        if schema is SqlProposal:
            self.prompts.append("\n".join(m.content for m in messages))
            q = self._question(messages)
            sqls = self.plan.get(q or "", ["SELECT 1"])
            i = self._calls.get(q or "", 0)
            self._calls[q or ""] = i + 1
            return SqlProposal(sql=sqls[min(i, len(sqls) - 1)])
        from app.core.errors import LLMError

        raise LLMError("fake gateway declines chart intent")  # -> heuristic/skip

    async def stream(
        self, llm: ResolvedLLM, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(text="ok")

    async def probe(self, llm: ResolvedLLM) -> ProviderCapabilities:
        return LLM.capabilities


@asynccontextmanager
async def _env() -> AsyncIterator[dict[str, Any]]:
    """Build a fixture connector on the *current* event loop (asyncpg pools are
    loop-bound), skipping cleanly when no wide-schema fixture is reachable."""
    asyncpg = pytest.importorskip("asyncpg")
    u = urlparse(DSN)
    params: dict[str, Any] = {
        "kind": "postgres", "host": u.hostname or "localhost", "port": u.port or 5432,
        "database": (u.path or "/sales").lstrip("/"),
        "username": u.username or "analytics_ro", "password": u.password or "analytics_ro",
    }
    try:
        probe = await asyncpg.connect(
            host=params["host"], port=params["port"], user=params["username"],
            password=params["password"], database=params["database"], timeout=5,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no sales fixture at {DSN}: {exc}")
    has = await probe.fetchval("SELECT to_regclass('public.sales_daily_rollup')")
    await probe.close()
    if has is None:
        pytest.skip("fixture reachable but not the wide Task-1 schema; run `make fixtures`")

    connector = build_connector(**params)
    try:
        snap = runner.snapshot_to_dict(await connector.introspect(schema_allowlist=["public"]))
        policy = runner.build_policy(
            snap, DatabaseKind("postgres").sqlglot_dialect, SETTINGS.default_max_rows
        )
        yield {"connector": connector, "snapshot": snap, "policy": policy}
    finally:
        await connector.close()


def _record(rid: str) -> dataset.GoldRecord:
    return next(r for r in dataset.load_gold_suite("sales_v1").records if r.id == rid)


async def _eval(
    record: dataset.GoldRecord, gateway: Any, env: dict[str, Any],
    *, semantic: dict[str, Any] | None = None,
) -> metrics.RecordOutcome:
    return await runner.evaluate_record(
        record, gateway=gateway, llm=LLM, connector=env["connector"],
        snapshot=env["snapshot"], policy=env["policy"], settings=SETTINGS,
        model_name="fake-model", with_cost=False, semantic=semantic,
    )


@pytest.mark.asyncio
async def test_perfect_model_matches_across_slices() -> None:
    # A spread across slices, including sales-030 which uses a cast (now allowed).
    ids = ["sales-001", "sales-007", "sales-018", "sales-030", "sales-043", "sales-048"]
    records = [_record(i) for i in ids]
    gateway = FakeGateway(plan={r.question: [r.gold_sql] for r in records})
    async with _env() as env:
        outcomes = await runner.run_suite(
            records, gateway=gateway, llm=LLM, connector=env["connector"],
            snapshot=env["snapshot"], policy=env["policy"], settings=SETTINGS,
            model_name="fake-model", with_cost=False,
        )
    report = metrics.aggregate(outcomes)
    assert report.execution_accuracy == 1.0, [
        (o.record_id, o.outcome, o.failure_reason) for o in outcomes if not o.is_success
    ]
    assert all(o.execution_ok and o.parse_ok and o.validated_ok for o in outcomes)


@pytest.mark.asyncio
async def test_retrieval_recall_gap_shows_on_bridge_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # sales-018 needs product_suppliers + order_items, which the question never
    # names. Even a perfect model MATCHes, but retrieval recall must be < 1.
    #
    # The budget is forced below the fixture (~26.5k chars) so this exercises
    # EXACT_MATCH on purpose. It used to rely on the fixture happening to
    # exceed the ceiling, which made the test silently vacuous the moment the
    # ceiling moved — raising it 24k -> 50k put the fixture under it, and this
    # assertion started failing for a reason that had nothing to do with
    # retrieval quality.
    monkeypatch.setattr(nodes, "_RETRIEVE_BUDGET_CHARS", 1_000)
    rec = _record("sales-018")
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan={rec.question: [rec.gold_sql]}), env)
    assert o.outcome == metrics.OUTCOME_MATCH
    assert o.retrieval_recall < 1.0 and not o.retrieval_hit


@pytest.mark.asyncio
async def test_fixture_fits_the_retrieve_budget_and_recalls_everything() -> None:
    """The shipped ceiling, on the shipped fixture: no selection happens.

    Pins the consequence of the 50k budget rather than the number itself — if
    the fixture grows past it, or the ceiling drops, retrieval starts choosing
    tables on the eval suite again and every recall figure in a report changes
    meaning. That should fail here first.
    """
    rec = _record("sales-018")
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan={rec.question: [rec.gold_sql]}), env)
    assert o.retrieval_recall == 1.0 and o.retrieval_hit


@pytest.mark.asyncio
async def test_cast_candidate_validates_and_matches() -> None:
    # Regression guard for the sqlguard fix: an explicit cast
    # (`date_trunc(...)::date`) is now on the allowlist (exp.DataType), so a model
    # that writes one validates and is scored on its result, not rejected.
    rec = _record("sales-030")  # gold uses ::date
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan={rec.question: [rec.gold_sql]}), env)
    assert o.outcome == metrics.OUTCOME_MATCH, o.failure_reason
    assert o.validated_ok and "E_NODE_NOT_ALLOWED" not in o.policy_violations


@pytest.mark.asyncio
async def test_wrong_but_valid_sql_is_a_mismatch() -> None:
    rec = _record("sales-001")  # gold counts phone orders
    wrong = "SELECT count(*) FROM orders WHERE channel = 'web'"
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan={rec.question: [wrong]}), env)
    assert o.outcome == metrics.OUTCOME_MISMATCH
    assert o.execution_ok and o.failure_reason


@pytest.mark.asyncio
async def test_invalid_sql_exhausts_repairs_and_fails_validation() -> None:
    rec = _record("sales-001")
    junk = "SELECT nonexistent_col FROM no_such_table"
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan={rec.question: [junk]}), env)
    assert o.outcome == metrics.OUTCOME_VALIDATION_FAILED
    assert o.attempts == 3            # 1 + _MAX_REPAIRS
    assert o.policy_violations        # a rule_id was recorded
    assert not o.execution_ok


@pytest.mark.asyncio
async def test_repair_then_succeed_reports_attempt_two() -> None:
    rec = _record("sales-002")
    plan = {rec.question: ["SELECT bad FROM nope", rec.gold_sql]}
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan=plan), env)
    assert o.outcome == metrics.OUTCOME_MATCH
    assert o.succeeded_on_attempt == 2
    assert o.repair_count == 1


# A query the guard *accepts* (only allowed tables/columns, allowlisted nodes)
# that still fails at execution: constant division by zero. This is the DB-error
# repair path — distinct from a guard rejection, which never reaches the database.
_DB_FAILS_BUT_VALID = "SELECT count(*) / (count(*) - count(*)) AS n FROM orders"


@pytest.mark.asyncio
async def test_db_error_triggers_repair_then_succeeds() -> None:
    # The repair loop must regenerate on a *database* error, not only on a guard
    # rejection. First attempt validates but divides by zero at execution; the
    # second is correct. Success on attempt two, with NO policy violation —
    # proving the repair was driven by the DB error, not the validator.
    rec = _record("sales-001")
    plan = {rec.question: [_DB_FAILS_BUT_VALID, rec.gold_sql]}
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan=plan), env)
    assert o.outcome == metrics.OUTCOME_MATCH, o.failure_reason
    assert o.succeeded_on_attempt == 2
    assert o.repair_count == 1
    assert o.validated_ok and not o.policy_violations  # DB error, not a rejection


@pytest.mark.asyncio
async def test_db_error_exhausts_repairs_and_fails_execution() -> None:
    # Every attempt validates but fails at the database. Repairs are bounded by
    # MAX_REPAIRS just like guard rejections (1 + _MAX_REPAIRS = 3 attempts), and
    # the terminal outcome is EXEC_FAILED (valid SQL the DB refused), never
    # VALIDATION_FAILED — so policy_violations stays empty.
    rec = _record("sales-001")
    plan = {rec.question: [_DB_FAILS_BUT_VALID]}  # returned on every attempt
    async with _env() as env:
        o = await _eval(rec, FakeGateway(plan=plan), env)
    assert o.outcome == metrics.OUTCOME_EXEC_FAILED, o.outcome
    assert o.attempts == 3            # 1 + _MAX_REPAIRS, bounded on DB errors too
    assert o.validated_ok and not o.execution_ok
    assert not o.policy_violations    # nothing was rejected by the guard
    assert o.failure_reason and "division by zero" in o.failure_reason


@pytest.mark.asyncio
async def test_negative_routing_scored_without_sql() -> None:
    neg = dataset.load_negative_suite("sales_v1_negative")
    rec = next(r for r in neg.records if r.expected_route == "CHITCHAT")
    async with _env() as env:
        ok = await runner.evaluate_negative(
            rec, gateway=FakeGateway(route="CHITCHAT"), llm=LLM,
            connector=env["connector"], snapshot=env["snapshot"],
            policy=env["policy"], settings=SETTINGS,
        )
        assert ok.outcome == "ROUTE_OK" and ok.candidate_sql is None

        wrong = await runner.evaluate_negative(
            rec, gateway=FakeGateway(route="ANALYTICAL", plan={rec.question: ["SELECT count(*) FROM orders"]}),
            llm=LLM, connector=env["connector"], snapshot=env["snapshot"],
            policy=env["policy"], settings=SETTINGS,
        )
    # An analytical mis-route that runs SQL is the worst case: a leak.
    assert wrong.outcome == "SQL_LEAK"


# ── the arms (Phase 0 of docs/learning-loop-plan.md: "fix the ruler") ────────
#
# Both are OFF by default, and that is the load-bearing property: every number
# recorded before they existed was measured with the layer absent and the
# shipped retrieve ceiling, so a bare `--suite sales_v1` has to stay that run.


def test_every_arm_is_off_unless_asked_for() -> None:
    args = runner.build_parser().parse_args(["--suite", "sales_v1"])
    assert args.semantic == "off"
    assert args.retrieve_budget is None
    assert args.comments is False


def test_the_arms_parse() -> None:
    args = runner.build_parser().parse_args(
        ["--suite", "sales_v1", "--semantic", "on", "--retrieve-budget", "12000"]
    )
    assert args.semantic == "on" and args.retrieve_budget == 12_000


def test_the_fixture_ships_a_semantic_layer_to_switch_on() -> None:
    """P0.3 needs something to put in `NodeDeps.semantic`. A file, not a
    generated document: an arm whose input is regenerated per run measures the
    generator."""
    spec = dataset.fixture_for("sales_pg")
    assert spec.semantic_path is not None and spec.semantic_path.exists()


@pytest.mark.asyncio
async def test_the_layer_binds_to_the_fixture_schema_with_no_broken_entries() -> None:
    """`load_semantic` refuses a document that no longer resolves, so this
    passing *is* the assertion that the shipped layer is whole. Without it the
    layer-on arm would be layer-on for some questions and layer-off for others,
    and nobody reading the number could tell."""
    async with _env() as env:
        doc = runner.load_semantic(dataset.fixture_for("sales_pg"), env["snapshot"])
    assert doc["entities"] and any(e["metrics"] for e in doc["entities"])
    # Derived from the catalog at load time, exactly as the product does it.
    assert doc["joins"] and all(j["on"] for j in doc["joins"])


@pytest.mark.asyncio
async def test_the_layer_reaches_the_prompt_only_in_the_layer_on_arm() -> None:
    """The A/B is only an A/B if the off arm is the prompt that was measured
    before the layer existed."""
    rec = _record("sales-001")
    async with _env() as env:
        semantic = runner.load_semantic(dataset.fixture_for("sales_pg"), env["snapshot"])

        off = FakeGateway(plan={rec.question: [rec.gold_sql]})
        await _eval(rec, off, env)
        on = FakeGateway(plan={rec.question: [rec.gold_sql]})
        await _eval(rec, on, env, semantic=semantic)

    assert off.prompts and on.prompts
    assert "What these tables mean" not in off.prompts[0]
    assert "What these tables mean" in on.prompts[0]
    assert "metric revenue = SUM(orders.total_amount)" in on.prompts[0]


@pytest.mark.asyncio
async def test_a_lowered_budget_is_what_makes_recall_measurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two halves of P0.2 in one place: at the shipped ceiling the fixture
    fits whole and recall is 1.0 by construction; under it, retrieval selects
    and recall can miss. `--retrieve-budget` lowers exactly this constant."""
    rec = _record("sales-018")
    async with _env() as env:
        full = await _eval(rec, FakeGateway(plan={rec.question: [rec.gold_sql]}), env)
        monkeypatch.setattr(nodes, "_RETRIEVE_BUDGET_CHARS", 1_000)
        lowered = await _eval(rec, FakeGateway(plan={rec.question: [rec.gold_sql]}), env)
    assert full.retrieval_recall == 1.0
    assert lowered.retrieval_recall < 1.0
