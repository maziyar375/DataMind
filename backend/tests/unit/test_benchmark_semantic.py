"""The customer's accuracy score is measured *with* the semantic layer.

`docs/plans/semantic-layer-model.md` §1.2.2. `_ask` built `NodeDeps` without
`semantic=`, the field defaulted to None, and the docstring beside it said
*"Everything else is exactly the ask path."* For any connection with a layer it
was not: the benchmark scored a prompt with no layer while chat answered with
one. So the number on `/knowledge/:id` was not the product's number, and
toggling `semantic_layer_enabled` changed nothing a benchmark rendered — the A/B
mvp2 A3 promises could not happen.

These tests drive `execute_benchmark_run` itself, through the real pipeline, and
read the prompt the generator was sent. Only the edges are faked: the provider,
the customer's database and the session. Wiring the layer into `_ask` without
loading it in the worker, or loading it without honouring the switch, fails
here.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.infra.db.models import (
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkSet,
    DatabaseConnection,
    KnowledgeTemplateRow,
    LlmConfig,
    SemanticLayerRow,
)
from app.knowledge import TemplateRole
from app.pipeline.prompts import PROMPT_VERSION
from app.semantic import SemanticDocument, SemanticEntity, SemanticMetric
from app.services import knowledge_service
from app.services.query_service import TileResult
from app.workers import benchmark
from tests.unit.test_benchmarks import CONNECTION_ID, _connection, _template
from tests.unit.test_pipeline_events import (
    ONE_ROW,
    SNAPSHOT,
    SQL_TOTAL,
    ScriptedConnector,
    ScriptedGateway,
)

#: The first line of the layer's entity section. Present only when a layer
#: rendered, and never produced by anything else in the schema block.
LAYER_HEADER = "What these tables mean"


def _layer() -> dict[str, Any]:
    return SemanticDocument(
        entities=[
            SemanticEntity(
                table="public.orders",
                label="Customer orders",
                grain="one row per order",
                metrics=[
                    SemanticMetric(
                        name="revenue",
                        expression="SUM(total_amount)",
                        filters=["status <> 'cancelled'"],
                    )
                ],
            )
        ]
    ).model_dump(mode="json")


class PromptCapturingGateway(ScriptedGateway):
    """The scripted gateway, keeping every generate prompt it was sent."""

    def __init__(self) -> None:
        super().__init__(sql=[SQL_TOTAL])
        self.generate_prompts: list[str] = []

    async def structured(self, llm: Any, messages: Any, schema: Any, **kwargs: Any) -> Any:
        if schema.__name__ == "SqlProposal":
            self.generate_prompts.append("\n".join(m.content for m in messages))
        return await super().structured(llm, messages, schema, **kwargs)


class ClosingConnector(ScriptedConnector):
    async def close(self) -> None:
        return None


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class WorkerDb:
    """The reads `execute_benchmark_run` makes, and a place for what it writes."""

    def __init__(
        self,
        *,
        run: BenchmarkRun,
        set_row: BenchmarkSet,
        connection: DatabaseConnection,
        template: KnowledgeTemplateRow,
        layer: dict[str, Any] | None,
        draft: dict[str, Any] | None = None,
        revision: int = 0,
    ) -> None:
        self._rows = {
            BenchmarkRun: run, BenchmarkSet: set_row,
            DatabaseConnection: connection, LlmConfig: LlmConfig(id=run.llm_config_id),
        }
        self._template = template
        self._layer = (
            SemanticLayerRow(
                id=uuid4(), connection_id=CONNECTION_ID, document=layer,
                draft_document=draft, revision=revision, published_version=1,
            )
            if layer is not None else None
        )
        self.results: list[BenchmarkResult] = []

    async def get(self, model: Any, _key: Any) -> Any:
        return self._rows.get(model)

    async def execute(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0].get("entity")
        if entity is KnowledgeTemplateRow:
            return _Result([self._template])
        if entity is SemanticLayerRow:
            return _Result([self._layer] if self._layer else [])
        if entity is BenchmarkResult:
            return _Result(self.results)
        raise AssertionError(f"unexpected query: {entity}")

    def add(self, obj: Any) -> None:
        if isinstance(obj, BenchmarkResult):
            self.results.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


async def _benchmark(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
    layer: dict[str, Any] | None,
    draft: dict[str, Any] | None = None,
    source: str = "PUBLISHED",
    pinned: int | None = None,
    revision: int = 0,
) -> tuple[PromptCapturingGateway, BenchmarkRun]:
    """Run a one-question benchmark and hand back what the generator was sent."""
    gateway = PromptCapturingGateway()

    async def snapshot(*_a: Any) -> dict[str, Any]:
        return SNAPSHOT

    async def gold(*_a: Any, **_k: Any) -> TileResult:
        return TileResult(status="OK", rows=ONE_ROW.rows, row_count=ONE_ROW.row_count)

    class _Llm:
        def snapshot(self) -> dict[str, Any]:
            return {"provider": "openai", "model": "gpt-4o-mini"}

    class _Gateways:
        @staticmethod
        def from_settings(_settings: Any) -> PromptCapturingGateway:
            return gateway

    monkeypatch.setattr(benchmark, "latest_snapshot", snapshot)
    monkeypatch.setattr(benchmark, "secret_box", lambda _s: object())
    monkeypatch.setattr(benchmark, "resolve_llm", lambda *_a, **_k: _Llm())
    monkeypatch.setattr(benchmark, "bind_connector", lambda *_a: ClosingConnector([ONE_ROW]))
    monkeypatch.setattr(benchmark, "LiteLLMGateway", _Gateways)
    monkeypatch.setattr(benchmark, "execute_saved_sql", gold)
    monkeypatch.setattr(benchmark, "build_authorizer", lambda *_a: None)
    # No store to match against: the question goes to the generator, which is
    # the prompt under test.
    monkeypatch.setattr(knowledge_service, "build_matcher", lambda *_a, **_k: None)

    template = _template("What was total revenue?", role=TemplateRole.HELD_OUT)
    template.sql = SQL_TOTAL
    set_row = BenchmarkSet(id=uuid4(), connection_id=CONNECTION_ID, template_ids=[template.id])
    run = BenchmarkRun(
        id=uuid4(), set_id=set_row.id, connection_id=CONNECTION_ID,
        llm_config_id=uuid4(), status="QUEUED",
        semantic_source=source, semantic_revision=pinned,
    )
    connection = _connection()
    connection.semantic_layer_enabled = enabled
    connection.include_db_comments = False

    db = WorkerDb(
        run=run, set_row=set_row, connection=connection, template=template, layer=layer,
        draft=draft, revision=revision,
    )
    await benchmark.execute_benchmark_run(db, Settings(), run.id)  # type: ignore[arg-type]
    return gateway, run


@pytest.mark.asyncio
async def test_a_benchmark_question_is_asked_with_the_connections_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, run = await _benchmark(monkeypatch, enabled=True, layer=_layer())

    assert run.status == "SUCCEEDED", run.error_message
    assert len(gateway.generate_prompts) == 1
    prompt = gateway.generate_prompts[0]
    assert LAYER_HEADER in prompt
    assert "metric revenue = SUM(total_amount) WHERE status <> 'cancelled'" in prompt


@pytest.mark.asyncio
async def test_a_benchmark_run_reports_how_often_its_answers_used_a_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3: the same attribution a chat run gets, per question and per run.

    The scripted answer sums `total_amount` and leaves out the definition's
    `status <> 'cancelled'` — an `ignored` verdict, counted, never shown."""
    _, run = await _benchmark(monkeypatch, enabled=True, layer=_layer())
    assert run.status == "SUCCEEDED", run.error_message
    assert run.metric_use == {"in_scope": 1, "used": 0, "ignored": 1, "unknown": 0}

    _, off = await _benchmark(monkeypatch, enabled=False, layer=_layer())
    assert off.metric_use is None, "no layer reached the prompt: not measured, not zero"


@pytest.mark.asyncio
async def test_switching_the_layer_off_takes_it_out_of_the_benchmark_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The A/B a customer can now run on their own layer: same set, one switch."""
    gateway, run = await _benchmark(monkeypatch, enabled=False, layer=_layer())

    assert run.status == "SUCCEEDED", run.error_message
    assert LAYER_HEADER not in gateway.generate_prompts[0]
    assert "SUM(total_amount) WHERE" not in gateway.generate_prompts[0]


@pytest.mark.asyncio
async def test_the_layer_on_and_off_prompts_differ_only_by_the_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off by absence holds on this path too.

    A connection with no layer at all and a connection whose layer is switched
    off render the same bytes — so a benchmark taken before a layer existed
    and one taken with it switched off are the same measurement.
    """
    off, _ = await _benchmark(monkeypatch, enabled=False, layer=_layer())
    none, _ = await _benchmark(monkeypatch, enabled=True, layer=None)
    assert off.generate_prompts == none.generate_prompts


@pytest.mark.asyncio
async def test_the_benchmark_binds_the_layer_to_the_snapshot_it_scores_against(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A metric over a column this snapshot does not have is flagged by the
    # binder and never reaches the benchmark's prompt — the same rule chat
    # follows, so the two numbers stay about the same product.
    layer = _layer()
    layer["entities"][0]["metrics"][0]["expression"] = "SUM(amount_cents)"
    gateway, _ = await _benchmark(monkeypatch, enabled=True, layer=layer)

    prompt = gateway.generate_prompts[0]
    assert "Customer orders" in prompt
    assert "amount_cents" not in prompt


def _draft() -> dict[str, Any]:
    """The layer with a number-changing edit nobody has published."""
    draft = _layer()
    draft["entities"][0]["metrics"][0]["filters"].append("status <> 'refunded'")
    return draft


@pytest.mark.asyncio
async def test_a_published_benchmark_reads_the_published_layer_while_a_draft_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fourth surface of Phase 2's rule: a draft reaches no run."""
    gateway, run = await _benchmark(
        monkeypatch, enabled=True, layer=_layer(), draft=_draft(), revision=4,
    )
    assert run.status == "SUCCEEDED", run.error_message
    prompt = gateway.generate_prompts[0]
    assert "metric revenue = SUM(total_amount) WHERE status <> 'cancelled'" in prompt
    assert "refunded" not in prompt
    assert (run.semantic_source, run.semantic_layer_version) == ("PUBLISHED", 1)


@pytest.mark.asyncio
async def test_a_draft_benchmark_scores_the_draft_it_was_pinned_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, run = await _benchmark(
        monkeypatch, enabled=True, layer=_layer(), draft=_draft(), revision=4,
        source="DRAFT", pinned=4,
    )
    assert run.status == "SUCCEEDED", run.error_message
    prompt = gateway.generate_prompts[0]
    assert "status <> 'cancelled' AND status <> 'refunded'" in prompt
    # What was scored, and over which published version the draft was edited.
    assert (run.semantic_source, run.semantic_revision, run.semantic_layer_version) == (
        "DRAFT", 4, 1,
    )


@pytest.mark.asyncio
async def test_a_draft_that_moved_after_queuing_fails_the_run_and_asks_no_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, run = await _benchmark(
        monkeypatch, enabled=True, layer=_layer(), draft=_draft(), revision=5,
        source="DRAFT", pinned=4,
    )
    assert run.status == "FAILED"
    assert "draft changed after this run was queued" in run.error_message
    assert gateway.generate_prompts == []


def test_runs_either_side_of_the_fix_carry_different_prompt_versions() -> None:
    # `benchmark_runs.prompt_version` is what tells a layer-off score (v9 and
    # earlier) from a score taken with the layer (v10 on). D11. v11 moved it
    # again, for the layer indexing retrieval — still layer-on, so the split
    # this test names is unaffected.
    assert PROMPT_VERSION == "v11"
