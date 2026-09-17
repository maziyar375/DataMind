"""Attribution wired into the product: the run, the answer, the table, the score.

Phase 3 of `docs/plans/semantic-layer-model.md`. `test_semantic_attribute.py`
owns the verdicts; this file owns where they go:

* a run attributes **the last statement the guard accepted** — a Verified
  answer's included — against the layer version it recorded, and stores
  nothing when there is nothing to say or the statement cannot be read (fail
  open: a run never fails for this);
* an answer's knowledge carries the definitions it **used**, read from that
  version, and never an `ignored` one;
* *Metrics in use* counts verdicts per metric over a window, most-ignored first;
* a benchmark run counts its questions' verdicts.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.api.v1 import conversations
from app.api.v1 import semantic as semantic_routes
from app.core.clock import utcnow
from app.domain.value_objects.authz import Privilege
from app.infra.db.models import (
    BenchmarkResult,
    Conversation,
    GeneratedQuery,
    Message,
    Run,
)
from app.pipeline.state import SqlAttempt
from app.semantic import SemanticDocument, SemanticEntity, SemanticMetric
from app.services import run_service, semantic_service
from app.services.semantic_service import (
    attribute_statement,
    metric_use_of,
    summarise_metric_use,
)
from app.sqlguard.validator import ValidationReport
from app.workers.benchmark import metric_use_summary
from tests.unit.semantic_world import (
    AUTHOR,
    connection,
    db,  # noqa: F401 - fixture
    engine,  # noqa: F401 - fixture
    service,
)
from tests.unit.test_pipeline_events import SNAPSHOT
from tests.unit.test_run_knowledge_tier import DESCRIBED, TierDb, _run, _touching

LAYER = SemanticDocument(entities=[
    SemanticEntity(
        table="public.orders",
        metrics=[
            SemanticMetric(
                name="revenue", label="Revenue", expression="SUM(orders.total_amount)",
                filters=["orders.status <> 'cancelled'"],
            ),
            SemanticMetric(name="order_count", expression="COUNT(DISTINCT orders.id)"),
        ],
    ),
])

USED_SQL = "SELECT SUM(o.total_amount) FROM public.orders o WHERE o.status <> 'cancelled'"
IGNORED_SQL = "SELECT SUM(o.total_amount) FROM public.orders o"


def attempt(sql: str, status: str = "VALID", n: int = 1) -> SqlAttempt:
    return SqlAttempt(
        attempt_no=n, raw_sql=sql, rewritten_sql=f"{sql} LIMIT 1000" if status == "VALID" else None,
        report=ValidationReport(status=status, referenced_tables=["public.orders"]),
    )


# ── which statement, and fail open ───────────────────────────────────────
def test_the_last_accepted_attempt_is_the_one_attributed() -> None:
    attempts = [attempt(IGNORED_SQL, n=1), attempt(USED_SQL, n=2), attempt("SELEC", "REJECTED", 3)]
    chosen, use = metric_use_of(attempts, document=LAYER, version=7, snapshot=SNAPSHOT)
    assert chosen is attempts[1]
    assert use == {"version": 7, "verdicts": [
        {"metric": "revenue", "entity": "public.orders", "verdict": "used"},
        {"metric": "order_count", "entity": "public.orders", "verdict": "unknown"},
    ]}


def test_a_verified_answer_is_attributed_like_any_other() -> None:
    """A short-circuit puts the template's bound statement in as attempt 1, and
    the guard validates it; nothing about attribution depends on who wrote it."""
    verified = SqlAttempt(
        attempt_no=1, raw_sql=USED_SQL, rewritten_sql=USED_SQL + " LIMIT 1000",
        report=ValidationReport(status="VALID", referenced_tables=["public.orders"]),
    )
    _, use = metric_use_of([verified], document=LAYER, version=2, snapshot=SNAPSHOT)
    assert use is not None and use["verdicts"][0]["verdict"] == "used"


def test_nothing_accepted_nothing_attributed() -> None:
    assert metric_use_of(
        [attempt(USED_SQL, "REJECTED")], document=LAYER, version=1, snapshot=SNAPSHOT
    ) == (None, None)


def test_no_layer_reached_the_prompt_so_nothing_is_stored() -> None:
    # Version 0: the run answered without a layer; there is no definition to match.
    chosen, use = metric_use_of([attempt(USED_SQL)], document=None, version=0, snapshot=SNAPSHOT)
    assert chosen is not None and use is None


def test_a_statement_that_will_not_parse_stores_null_and_raises_nothing() -> None:
    assert attribute_statement(
        "SELECT SUM(( FROM", document=LAYER, version=1, tables=["public.orders"], snapshot=SNAPSHOT,
    ) is None


def test_any_failure_inside_attribution_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("a bug in the matcher")

    monkeypatch.setattr(semantic_service, "attribute", boom)
    assert attribute_statement(
        USED_SQL, document=LAYER, version=1, tables=["public.orders"], snapshot=SNAPSHOT,
    ) is None


def test_a_statement_touching_no_metric_table_stores_nothing() -> None:
    assert attribute_statement(
        "SELECT name FROM public.customers", document=LAYER, version=1,
        tables=["public.customers"], snapshot=SNAPSHOT,
    ) is None


def test_the_run_stores_verdicts_on_the_attributed_attempt_only() -> None:
    source = inspect.getsource(run_service.RunService._finalise)
    assert "metric_use_of(" in source
    assert "metric_use=metric_use if attempt is attributed else None" in source
    execute = inspect.getsource(run_service.RunService.execute_run)
    assert "layer=layer, snapshot=snapshot" in execute


# ── the answer carries the definitions it used ───────────────────────────
def _with_versions(document: dict[str, Any]) -> TierDb:
    tier = TierDb(layer=DESCRIBED)
    tier.versions[5] = document
    return tier


async def _knowledge(tier: TierDb, metric_use: dict[str, Any] | None, **kwargs: Any) -> Any:
    return await conversations._knowledge(
        tier, _run(5), _touching("public.orders"), metric_use=metric_use, **kwargs
    )


@pytest.mark.asyncio
async def test_an_answer_names_the_definitions_it_used_from_its_version() -> None:
    tier = _with_versions(LAYER.model_dump(mode="json"))
    knowledge = await _knowledge(tier, {"version": 5, "verdicts": [
        {"metric": "revenue", "entity": "public.orders", "verdict": "used"},
        {"metric": "order_count", "entity": "public.orders", "verdict": "ignored"},
    ]})
    assert [m.model_dump() for m in knowledge.metrics_used] == [{
        "metric": "revenue", "entity": "public.orders", "label": "Revenue",
        "expression": "SUM(orders.total_amount)", "filters": ["orders.status <> 'cancelled'"],
    }]
    assert knowledge.metrics_version == 5


@pytest.mark.asyncio
async def test_ignored_and_unknown_never_reach_the_answer() -> None:
    tier = _with_versions(LAYER.model_dump(mode="json"))
    knowledge = await _knowledge(tier, {"version": 5, "verdicts": [
        {"metric": "revenue", "entity": "public.orders", "verdict": "ignored"},
        {"metric": "order_count", "entity": "public.orders", "verdict": "unknown"},
    ]})
    assert knowledge.metrics_used == [] and knowledge.metrics_version is None


@pytest.mark.asyncio
async def test_no_attribution_or_version_zero_means_no_metrics() -> None:
    tier = _with_versions(LAYER.model_dump(mode="json"))
    assert (await _knowledge(tier, None)).metrics_used == []
    zero = {"version": 0, "verdicts": [{"metric": "revenue", "entity": "public.orders", "verdict": "used"}]}
    assert (await _knowledge(tier, zero)).metrics_used == []


@pytest.mark.asyncio
async def test_a_definition_since_deleted_is_read_from_the_version_not_today() -> None:
    tier = _with_versions(LAYER.model_dump(mode="json"))
    tier.layer = None  # today there is no layer at all
    knowledge = await _knowledge(tier, {"version": 5, "verdicts": [
        {"metric": "revenue", "entity": "public.orders", "verdict": "used"},
    ]})
    assert [m.metric for m in knowledge.metrics_used] == ["revenue"]


@pytest.mark.asyncio
async def test_a_transcript_reads_each_version_document_once() -> None:
    tier = _with_versions(LAYER.model_dump(mode="json"))
    use = {"version": 5, "verdicts": [{"metric": "revenue", "entity": "public.orders", "verdict": "used"}]}
    versions: conversations._Versions = {}
    described: conversations._Described = {}
    for _ in range(4):
        await _knowledge(tier, use, versions=versions, described=described)
    # One read for the tier's described set, one for the definitions.
    assert tier.layer_reads == 2


# ── Metrics in use ───────────────────────────────────────────────────────
def test_metrics_in_use_counts_per_metric_most_ignored_first() -> None:
    def v(metric: str, verdict: str) -> dict[str, str]:
        return {"metric": metric, "entity": "public.orders", "verdict": verdict}

    rows = summarise_metric_use([
        {"version": 1, "verdicts": [v("revenue", "used"), v("order_count", "ignored")]},
        {"version": 1, "verdicts": [v("revenue", "used"), v("order_count", "ignored")]},
        {"version": 2, "verdicts": [v("revenue", "ignored"), v("order_count", "unknown")]},
        None,
    ])
    assert [(r.metric, r.questions, r.used, r.ignored) for r in rows] == [
        ("order_count", 3, 0, 2),
        ("revenue", 3, 2, 1),
    ]


async def test_metrics_in_use_reads_this_connections_recent_runs(db) -> None:  # noqa: F811
    conn = connection(db)
    other = connection(db)

    def run_with(conn_id: Any, use: dict[str, Any] | None, *, days_ago: int = 0) -> None:
        conversation = Conversation(id=uuid4(), owner_id=AUTHOR, title="t")
        db.add(conversation)
        db._session.flush()
        message = Message(
            id=uuid4(), conversation_id=conversation.id, seq=1, role="USER", content="q",
        )
        db.add(message)
        db._session.flush()
        run = Run(
            id=uuid4(), conversation_id=conversation.id, user_message_id=message.id,
            owner_id=AUTHOR, connection_id=conn_id, status="SUCCEEDED",
            created_at=utcnow() - timedelta(days=days_ago),
        )
        db.add(run)
        db._session.flush()
        db.add(GeneratedQuery(
            id=uuid4(), run_id=run.id, attempt_no=1, raw_sql="SELECT 1", dialect="postgres",
            validation_status="VALID", metric_use=use,
        ))
        db._session.flush()

    used = {"version": 1, "verdicts": [{"metric": "revenue", "entity": "public.orders", "verdict": "used"}]}
    ignored = {"version": 1, "verdicts": [{"metric": "revenue", "entity": "public.orders", "verdict": "ignored"}]}
    run_with(conn.id, used)
    run_with(conn.id, ignored)
    run_with(conn.id, None)
    run_with(conn.id, ignored, days_ago=45)   # outside the window
    run_with(other.id, ignored)               # another connection

    [row] = await service(db).metric_use(conn.id, days=30)
    assert (row.metric, row.questions, row.used, row.ignored) == ("revenue", 2, 1, 1)
    assert len(await service(db).metric_use(conn.id, days=60)) == 1
    assert (await service(db).metric_use(conn.id, days=60))[0].ignored == 2


def test_the_metric_use_route_asks_select_on_the_layer() -> None:
    source = inspect.getsource(semantic_routes.get_semantic_metric_use)
    asked = [p for p in Privilege if f"Privilege.{p.name}" in source]
    assert asked == [Privilege.SELECT]
    assert "_authorized(db, authz, connection_id, ctx," in source


def test_the_metric_use_route_is_published() -> None:
    from app.main import create_app
    from tests.unit.test_authz_conformance import _routes

    paths = {(m, r.path) for r in _routes(create_app()) for m in r.methods or set()}
    assert ("GET", "/connections/{connection_id}/semantic/metric-use") in paths


# ── a benchmark run's metric-use rate ────────────────────────────────────
def test_a_benchmark_run_counts_its_questions_verdicts() -> None:
    def result(use: dict[str, Any] | None) -> BenchmarkResult:
        return BenchmarkResult(id=uuid4(), metric_use=use)

    assert metric_use_summary([result(None), result(None)]) is None
    assert metric_use_summary([
        result({"version": 1, "verdicts": [
            {"metric": "revenue", "entity": "public.orders", "verdict": "used"},
            {"metric": "order_count", "entity": "public.orders", "verdict": "unknown"},
        ]}),
        result({"version": 1, "verdicts": [
            {"metric": "revenue", "entity": "public.orders", "verdict": "ignored"},
        ]}),
        result(None),
    ]) == {"in_scope": 3, "used": 1, "ignored": 1, "unknown": 1}


# ── the eval's control arm ───────────────────────────────────────────────
def test_the_eval_counts_definition_use_per_arm_and_says_when_it_did_not_measure() -> None:
    from app.eval import metrics

    def outcome(attributed: bool, *verdicts: str) -> metrics.RecordOutcome:
        o = metrics.RecordOutcome(record_id="r", tags=[], difficulty="easy")
        o.metric_attributed = attributed
        o.metric_verdicts = [
            {"metric": "revenue", "entity": "public.orders", "verdict": v} for v in verdicts
        ]
        return o

    assert metrics.definition_use([outcome(False), outcome(False)]) is None
    assert metrics.definition_use([
        outcome(True, "used", "unknown"), outcome(True, "ignored"), outcome(True), outcome(False),
    ]) == {
        "questions_attributed": 3, "in_scope": 3,
        "used": 1, "ignored": 1, "unknown": 1, "used_rate": 0.3333,
    }
    assert metrics.definition_use([outcome(True)])["used_rate"] is None  # type: ignore[index]


def test_the_eval_attributes_on_both_arms() -> None:
    """The off arm is the control, so the layer must reach attribution there too
    — while never reaching the prompt."""
    from app.eval import runner

    main = inspect.getsource(runner)
    assert "attribution_layer = semantic" in main
    assert "if attribution_layer is None and spec.semantic_path is not None:" in main
    assert 'report_dict["definition_use"] = metrics.definition_use(outcomes)' in main
