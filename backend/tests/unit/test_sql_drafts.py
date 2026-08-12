"""Drafting SQL from a question — the path that must leave nothing behind.

A draft borrows three nodes from the run pipeline and none of its bookkeeping.
That is the thing worth testing: it writes no `runs` row, no `conversations`
row and no `messages` row, it renders the schema block under the connection's
own disclosure policy exactly as a run does, and a rejected draft comes back as
a report the editor can render rather than as an exception.

The fakes here stand in for the three things a draft touches — the session, the
model gateway and the connector — so the whole path runs without a database, a
provider, or a network.
"""
from __future__ import annotations

import base64
import os
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import (
    LLMError,
    NotFoundError,
    QuestionOutOfScopeError,
    ValidationError,
)
from app.domain.ports.database import QueryResult, ResultColumn
from app.domain.ports.llm import Completion
from app.pipeline.contracts import SqlProposal
from app.services import query_service, sql_draft_service
from app.services.sql_draft_service import PREVIEW_MAX_ROWS, draft_sql, validate_sql

OWNER = uuid4()

SNAPSHOT: dict[str, Any] = {
    "dialect": "postgres",
    "relationships": [],
    "tables": [
        {
            "schema": "public",
            "name": "orders",
            "approx_row_count": 4200,
            "columns": [
                {"name": "id", "data_type": "integer", "is_primary_key": True},
                {
                    "name": "status",
                    "data_type": "text",
                    # Customer data. Whether this reaches the prompt is the
                    # disclosure policy's decision, not the draft path's.
                    "sample_values": ["paid", "pending", "refunded"],
                    "distinct_count": 3,
                },
                {"name": "total_amount", "data_type": "numeric"},
            ],
        }
    ],
}

VALID_SQL = "SELECT status, total_amount FROM public.orders"

COLUMNS = [
    ResultColumn(name="status", db_type="text", semantic_type="nominal"),
    ResultColumn(name="total_amount", db_type="numeric", semantic_type="quantitative"),
]
ROWS: list[list[Any]] = [["paid", 120.0], ["pending", 60.0], ["refunded", 20.0]]


# ── fakes ────────────────────────────────────────────────────────────────
class FakeConnection:
    def __init__(
        self,
        *,
        owner_id: UUID = OWNER,
        disclosure_policy: str = "SAMPLE",
        semantic_layer_enabled: bool = True,
    ) -> None:
        self.id = uuid4()
        self.owner_id = owner_id
        self.name = "sales"
        self.database_type = "postgres"
        self.host, self.port = "db.internal", 5432
        self.database_name, self.username = "sales", "readonly"
        self.encrypted_password = "not-a-real-envelope"
        self.ssl_mode = None
        self.max_rows = 1000
        self.statement_timeout_ms = 30_000
        self.disclosure_policy = disclosure_policy
        self.semantic_layer_enabled = semantic_layer_enabled


class FakeLlmConfig:
    def __init__(self, *, owner_id: UUID = OWNER) -> None:
        self.id = uuid4()
        self.owner_id = owner_id
        self.provider = "openai"
        self.model = "gpt-4o-mini"
        self.base_url = None
        self.encrypted_api_key = None
        self.temperature = 0.2
        self.max_tokens = 2048
        self.capabilities: dict[str, Any] = {}


class FakeGateway:
    """Returns canned SQL and keeps every prompt it was sent."""

    def __init__(self, *sql: str) -> None:
        self.replies = list(sql)
        self.calls: list[list[Any]] = []

    async def structured(self, _llm: Any, messages: Any, _schema: Any) -> SqlProposal:
        self.calls.append(list(messages))
        if not self.replies:
            raise LLMError("The provider is unavailable.")
        return SqlProposal(sql=self.replies.pop(0))

    @property
    def system_prompt(self) -> str:
        return self.calls[-1][0].content


class FailingGateway:
    async def structured(self, _llm: Any, _messages: Any, _schema: Any) -> SqlProposal:
        raise LLMError("The provider is unavailable.")


class RoutingGateway(FakeGateway):
    """A `FakeGateway` that also answers the classifier.

    `route` is the only thing in this path that calls `complete`; everything
    else goes through `structured`. So `completions` counting zero is a
    positive assertion that no question was classified.
    """

    def __init__(self, *sql: str, label: str = "ANALYTICAL") -> None:
        super().__init__(*sql)
        self.label = label
        self.completions = 0

    async def complete(self, _llm: Any, messages: Any) -> Completion:
        self.completions += 1
        self.routed = list(messages)
        return Completion(text=self.label)


class FakeConnector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.closed = 0

    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult:
        self.calls.append((sql, max_rows, statement_timeout_ms))
        return QueryResult(
            columns=list(COLUMNS),
            rows=[list(r) for r in ROWS],
            row_count=len(ROWS),
            duration_ms=4,
        )

    async def close(self) -> None:
        self.closed += 1


class FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class FakeSnapshotRow:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.tables = snapshot["tables"]
        self.relationships = snapshot["relationships"]
        self.dialect = snapshot["dialect"]


class FakeDb:
    """The session, minus everything a draft must not use.

    `add`, `flush` and `commit` are recorded rather than implemented: nothing
    in this path may write, and the tests assert on exactly that.
    """

    def __init__(
        self,
        *,
        entities: dict[UUID, Any] | None = None,
        snapshot: dict[str, Any] | None = SNAPSHOT,
    ) -> None:
        self.entities = entities or {}
        self.snapshot = snapshot
        self.added: list[Any] = []
        self.flushes = 0
        self.commits = 0
        self.selects = 0

    async def get(self, _model: type, entity_id: UUID) -> Any:
        return self.entities.get(entity_id)

    async def execute(self, statement: Any) -> FakeResult:
        self.selects += 1
        text = str(statement).lower()
        if "semantic_layers" in text:
            return FakeResult(None)
        if self.snapshot is None:
            return FakeResult(None)
        return FakeResult(FakeSnapshotRow(self.snapshot))

    def add(self, entity: Any) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


class FakeKey:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class FakeSettings:
    def __init__(self) -> None:
        self.secret_box_key = FakeKey(
            base64.urlsafe_b64encode(os.urandom(32)).decode()
        )
        self.secret_box_key_version = 1
        self.llm_request_timeout_seconds = 30
        self.llm_max_retries = 0
        self.llm_retry_base_delay_seconds = 0
        self.llm_retry_max_delay_seconds = 0


def _world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connection: FakeConnection | None = None,
    llm_config: FakeLlmConfig | None = None,
    gateway: Any = None,
    snapshot: dict[str, Any] | None = SNAPSHOT,
) -> tuple[FakeDb, FakeConnection, FakeLlmConfig, FakeConnector]:
    """Wire the fakes in for one draft."""
    connection = connection or FakeConnection()
    llm_config = llm_config or FakeLlmConfig()
    connector = FakeConnector()

    db = FakeDb(
        entities={connection.id: connection, llm_config.id: llm_config},
        snapshot=snapshot,
    )
    # Both modules: `draft_sql` opens the connector itself and lends it to the
    # preview, while `validate_sql` has none to lend and `execute_saved_sql`
    # opens (and closes) its own.
    monkeypatch.setattr(
        sql_draft_service, "bind_connector", lambda conn, box: connector
    )
    monkeypatch.setattr(query_service, "bind_connector", lambda conn, box: connector)
    if gateway is not None:
        monkeypatch.setattr(
            sql_draft_service.LiteLLMGateway, "from_settings", classmethod(
                lambda cls, settings: gateway
            )
        )
    return db, connection, llm_config, connector


async def _draft(
    monkeypatch: pytest.MonkeyPatch, *, gateway: Any, **kwargs: Any
) -> Any:
    db, connection, llm_config, _connector = _world(
        monkeypatch, gateway=gateway, **kwargs
    )
    return await draft_sql(
        db,
        FakeSettings(),
        connection_id=connection.id,
        llm_config_id=llm_config.id,
        question="revenue by status",
        owner_id=OWNER,
    ), db


# ── a draft is not a run ─────────────────────────────────────────────────
async def test_a_draft_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `runs` row, no `conversations` row, no `messages` row — a draft the
    user abandons must leave nothing behind."""
    draft, db = await _draft(monkeypatch, gateway=FakeGateway(VALID_SQL))

    assert draft.validation_status == "VALID"
    assert db.added == []
    assert db.flushes == 0
    assert db.commits == 0


async def test_a_valid_draft_comes_back_with_its_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft, _db = await _draft(monkeypatch, gateway=FakeGateway(VALID_SQL))

    assert draft.sql == VALID_SQL
    assert draft.preview is not None
    assert draft.preview.status == "OK"
    assert draft.preview.row_count == 3
    assert draft.referenced_tables == ["public.orders"]


async def test_the_preview_is_capped_below_the_connections_own_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fifty rows answer "did this do what I meant"; a thousand is a report."""
    db, connection, llm_config, connector = _world(
        monkeypatch, gateway=FakeGateway(VALID_SQL)
    )

    await draft_sql(
        db,
        FakeSettings(),
        connection_id=connection.id,
        llm_config_id=llm_config.id,
        question="revenue by status",
        owner_id=OWNER,
    )

    sql, max_rows, _timeout = connector.calls[0]
    assert max_rows == PREVIEW_MAX_ROWS
    assert str(PREVIEW_MAX_ROWS) in sql


async def test_one_connector_serves_the_whole_draft_and_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, connection, llm_config, connector = _world(
        monkeypatch, gateway=FakeGateway(VALID_SQL)
    )

    await draft_sql(
        db,
        FakeSettings(),
        connection_id=connection.id,
        llm_config_id=llm_config.id,
        question="revenue by status",
        owner_id=OWNER,
    )

    assert len(connector.calls) == 1
    assert connector.closed == 1


# ── a rejection is a report, not an exception ────────────────────────────
async def test_a_rejected_draft_returns_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The editor renders the guard's reasons inline. Raising here would make
    "the model wrote something I can show you and explain" indistinguishable
    from "your request was malformed"."""
    hostile = "DROP TABLE orders"
    draft, _db = await _draft(monkeypatch, gateway=FakeGateway(hostile, hostile))

    assert draft.validation_status == "REJECTED"
    assert draft.sql == hostile
    assert draft.preview is None
    assert draft.validation_report["issues"][0]["rule_id"] == "E_NOT_A_SELECT"


async def test_a_rejected_draft_is_repaired_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draft is interactive and the user is watching: one retry, not the run
    pipeline's budget."""
    gateway = FakeGateway("DROP TABLE orders", VALID_SQL)

    draft, _db = await _draft(monkeypatch, gateway=gateway)

    assert len(gateway.calls) == 2
    assert draft.validation_status == "VALID"
    assert draft.preview is not None


async def test_the_repair_budget_stops_at_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway("DROP TABLE orders", "DELETE FROM orders", VALID_SQL)

    draft, _db = await _draft(monkeypatch, gateway=gateway)

    assert len(gateway.calls) == 2
    assert draft.validation_status == "REJECTED"


async def test_a_provider_failure_is_an_error_not_an_empty_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """502 through problem+json. A draft with no SQL in it is not a draft."""
    with pytest.raises(LLMError):
        await _draft(monkeypatch, gateway=FailingGateway())


# ── the deadline ─────────────────────────────────────────────────────────
# `RunState.deadline_at` is enforced by the run executor, and a draft calls the
# nodes directly — so the field was inert here until the check was written into
# this path. It bounds the repair specifically: one `structured` call can take
# minutes once the gateway's transient-failure retries and their backoff are
# counted, and without this the second attempt starts however long the first
# one took.
class SlowGateway(FakeGateway):
    """Answers, but advances the clock past the draft's budget while doing it."""

    def __init__(self, *sql: str, elapsed_seconds: int) -> None:
        super().__init__(*sql)
        self._elapsed = elapsed_seconds
        self.now = utcnow()

    async def structured(self, llm: Any, messages: Any, schema: Any) -> SqlProposal:
        self.now += timedelta(seconds=self._elapsed)
        return await super().structured(llm, messages, schema)


async def _with_clock(
    monkeypatch: pytest.MonkeyPatch, gateway: SlowGateway
) -> Any:
    """Run a draft whose clock is the gateway's, so no test ever sleeps."""
    monkeypatch.setattr(sql_draft_service, "utcnow", lambda: gateway.now)
    return await _draft(monkeypatch, gateway=gateway)


async def test_a_repair_is_not_started_after_the_deadline_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = SlowGateway(
        "DROP TABLE orders",
        VALID_SQL,
        elapsed_seconds=sql_draft_service.DRAFT_DEADLINE_SECONDS + 1,
    )

    with pytest.raises(LLMError) as raised:
        await _with_clock(monkeypatch, gateway)

    # The first attempt was made and rejected; the second was never asked for.
    assert len(gateway.calls) == 1
    assert "narrow the question" in raised.value.message


async def test_a_draft_inside_its_budget_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check may only stop a draft that is out of time — a slow-but-legal
    repair still runs, and a first attempt is never refused for the time it has
    not yet spent."""
    gateway = SlowGateway(
        "DROP TABLE orders",
        VALID_SQL,
        elapsed_seconds=sql_draft_service.DRAFT_DEADLINE_SECONDS // 4,
    )

    draft, _db = await _with_clock(monkeypatch, gateway)

    assert len(gateway.calls) == 2
    assert draft.validation_status == "VALID"


# ── classify: the question the guard cannot ask ──────────────────────────
# The guard reads a *statement* — is it a SELECT, do its names resolve, is it
# safe. "How is the weather" gets answered with SQL that satisfies all three,
# so a caller who stores the verdict rather than showing it needs something
# upstream that reads the question instead. Chat has always had it; `classify`
# is that same gate, opt-in.
async def _classified(
    monkeypatch: pytest.MonkeyPatch, *, gateway: Any, classify: bool
) -> Any:
    db, connection, llm_config, _connector = _world(monkeypatch, gateway=gateway)
    return await draft_sql(
        db,  # type: ignore[arg-type]
        FakeSettings(),  # type: ignore[arg-type]
        connection_id=connection.id,
        llm_config_id=llm_config.id,
        question="how is the weather",
        owner_id=OWNER,
        classify=classify,
    )


@pytest.mark.parametrize(
    ("label", "phrase"),
    [
        ("CHITCHAT", "not a question about your data"),
        ("UNSUPPORTED", "outside what the database can answer"),
        ("METADATA", "what the schema contains"),
    ],
)
async def test_a_question_with_no_data_answer_is_refused_before_any_sql(
    monkeypatch: pytest.MonkeyPatch, label: str, phrase: str
) -> None:
    """The reported bug: this used to come back as valid SQL and a green
    verdict, because every gate downstream of `generate` reads the statement
    and never the question."""
    gateway = RoutingGateway(VALID_SQL, label=label)

    with pytest.raises(QuestionOutOfScopeError) as raised:
        await _classified(monkeypatch, gateway=gateway, classify=True)

    assert phrase in raised.value.message
    assert raised.value.detail["intent"] == label
    # Refused *before* the expensive call, not after: the schema-sized prompt
    # is the thing classifying exists to avoid spending.
    assert gateway.calls == []


async def test_an_analytical_question_passes_the_classifier_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = RoutingGateway(VALID_SQL, label="ANALYTICAL")

    draft = await _classified(monkeypatch, gateway=gateway, classify=True)

    assert draft.validation_status == "VALID"
    assert gateway.completions == 1


async def test_classifying_is_off_by_default_so_a_tile_draft_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tile editor shares this function and did not ask for a second model
    call per draft. Off by default means it pays for nothing — and a gateway
    that would have refused this question proves the classifier never ran."""
    gateway = RoutingGateway(VALID_SQL, label="CHITCHAT")

    draft = await _classified(monkeypatch, gateway=gateway, classify=False)

    assert draft.validation_status == "VALID"
    assert gateway.completions == 0


# ── the schema block is the run path's, disclosure and all ───────────────
async def test_the_schema_block_respects_the_connections_disclosure_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drafting is not a loophole around disclosure: the same `retrieve` render
    and the same `HintBudget` decide what the model is told about a column's
    contents."""
    gateway = FakeGateway(VALID_SQL)
    await _draft(
        monkeypatch,
        gateway=gateway,
        connection=FakeConnection(disclosure_policy="NONE"),
    )
    closed = gateway.system_prompt

    open_gateway = FakeGateway(VALID_SQL)
    await _draft(
        monkeypatch,
        gateway=open_gateway,
        connection=FakeConnection(disclosure_policy="SAMPLE"),
    )
    sampled = open_gateway.system_prompt

    # Structure reaches the model under every policy.
    assert "public.orders" in closed and "public.orders" in sampled
    # Values do not.
    assert "paid" not in closed
    assert "3 distinct" not in closed
    assert "~4,200 rows" not in closed
    assert "paid" in sampled


async def test_a_draft_carries_no_conversation_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no thread behind a draft, and inventing one would put another
    connection's answers into this prompt."""
    gateway = FakeGateway(VALID_SQL)
    await _draft(monkeypatch, gateway=gateway)

    assert "Recent conversation" not in gateway.system_prompt


# ── loading and refusing ─────────────────────────────────────────────────
async def test_another_users_connection_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(NotFoundError):
        await _draft(
            monkeypatch,
            gateway=FakeGateway(VALID_SQL),
            connection=FakeConnection(owner_id=uuid4()),
        )


async def test_another_users_model_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(NotFoundError):
        await _draft(
            monkeypatch,
            gateway=FakeGateway(VALID_SQL),
            llm_config=FakeLlmConfig(owner_id=uuid4()),
        )


async def test_an_unsynced_connection_is_refused_before_a_model_is_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not an empty prompt: without a snapshot the guard would reject whatever
    came back, so the call is not worth making."""
    gateway = FakeGateway(VALID_SQL)

    with pytest.raises(ValidationError):
        await _draft(monkeypatch, gateway=gateway, snapshot=None)

    assert gateway.calls == []


# ── the hand-written path ────────────────────────────────────────────────
async def test_hand_written_sql_is_guarded_and_previewed_without_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the second route: a user with no provider configured
    can still build a dashboard."""
    db, connection, _llm, connector = _world(monkeypatch)

    draft = await validate_sql(
        db, FakeSettings(), connection_id=connection.id, sql=VALID_SQL, owner_id=OWNER
    )

    assert draft.validation_status == "VALID"
    assert draft.preview is not None
    assert draft.preview.row_count == 3
    assert draft.llm_config_id is None
    assert draft.question is None
    assert connector.closed == 1


async def test_hand_written_sql_gets_the_same_guard_as_a_generated_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sql_origin` is provenance, never trust. The textarea is hostile input."""
    db, connection, _llm, connector = _world(monkeypatch)

    draft = await validate_sql(
        db,
        FakeSettings(),
        connection_id=connection.id,
        sql="SELECT * FROM pg_shadow",
        owner_id=OWNER,
    )

    assert draft.validation_status == "REJECTED"
    assert draft.preview is None
    assert connector.calls == []


async def test_validate_refuses_another_users_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, connection, _llm, _connector = _world(
        monkeypatch, connection=FakeConnection(owner_id=uuid4())
    )

    with pytest.raises(NotFoundError):
        await validate_sql(
            db,
            FakeSettings(),
            connection_id=connection.id,
            sql=VALID_SQL,
            owner_id=OWNER,
        )


# ── the chart suggestion ─────────────────────────────────────────────────
async def test_the_draft_suggests_a_chart_from_the_previews_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic and free — the heuristic reads the result, no model is
    asked what to draw, and the user overrides it in the editor anyway."""
    draft, _db = await _draft(monkeypatch, gateway=FakeGateway(VALID_SQL))

    assert draft.chart_suggestion is not None
    assert draft.chart_suggestion["x_axis"]["field"] == "status"
    assert draft.chart_suggestion["y_axis"]["field"] == "total_amount"


async def test_a_rejected_draft_suggests_no_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft, _db = await _draft(
        monkeypatch, gateway=FakeGateway("DROP TABLE orders", "DROP TABLE orders")
    )

    assert draft.chart_suggestion is None
