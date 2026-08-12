"""A generation, from `POST /runs` to a document's worth of rows.

Five claims here are the ones the feature stands on:

* **A run is a set of independently-failable parts.** One block whose query the
  guard refuses must not take its neighbours with it — the run comes back
  `PARTIAL`, and every block that worked has its numbers.
* **The status is derived, never set.** That is what makes per-section retry
  (Phase 6) fall out with no state machine, and it is why `PARTIAL` exists.
* **Disclosure is re-checked at the start of every run.** A report created
  against a `SAMPLE` connection whose policy was later tightened fails out loud
  rather than quietly producing a document written from nothing.
* **Every result is committed as it lands.** The poll response is a snapshot of
  what exists so far; a run that writes everything in one final transaction has
  no progressive render, whatever the frontend does.
* **Cancellation keeps what was paid for.** A cancelled run is a stopped run,
  not an erased one.

The guard itself is not re-proved here — `tests/unit/test_report_guard.py`
replays the hostile corpus through this same path.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import ConflictError, ValidationError
from app.domain.ports.llm import ChatMessage, Completion
from app.domain.value_objects import DisclosurePolicy, ReportRunStatus
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    Report,
    ReportBlock,
    ReportBlockResult,
    ReportRun,
    ReportSection,
    ReportSectionResult,
)
from app.services import query_service
from app.services.report_service import ReportService
from app.workers import report as worker
from app.workers.report import derive_status, generate_run, retry_section
from tests.unit.test_query_service import SNAPSHOT, FakeConnector, FakeSettings

OWNER = uuid4()
REPORT_ID = uuid4()
SECTION_ID = uuid4()
OTHER_SECTION_ID = uuid4()
SUMMARY_SECTION_ID = uuid4()
CONNECTION_ID = uuid4()
LLM_ID = uuid4()
RUN_ID = uuid4()

PROSE = "درآمد وضعیت پرداخت‌شده ۱۲۰ بود و در مجموع ۳ وضعیت ثبت شد."


def _llm_config() -> LlmConfig:
    """A provider row as the database holds one — every column set.

    `max_tokens` matters: `resolve_llm` raises the floor on it for prose, and a
    transient object that never saw a column default would carry None.
    """
    return LlmConfig(
        id=LLM_ID,
        owner_id=OWNER,
        name="deepseek",
        provider="openai",
        model="m",
        temperature=0.0,
        max_tokens=1024,
        encrypted_api_key=None,
        capabilities={},
    )

# Two tables the snapshot allows, and one it does not.
GOOD_SQL = "SELECT status, total_amount FROM public.orders"
OTHER_SQL = "SELECT name, price FROM public.products"
REFUSED_SQL = "SELECT id FROM public.salaries"


# ── the fixtures the worker reads ────────────────────────────────────────
def _connection(policy: str = DisclosurePolicy.SAMPLE) -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID,
        owner_id=OWNER,
        name="sales",
        database_type="postgres",
        host="db",
        port=5432,
        database_name="sales",
        username="ro",
        encrypted_password="x",
        max_rows=1000,
        statement_timeout_ms=30_000,
        disclosure_policy=policy,
    )


def _report() -> Report:
    return Report(
        id=REPORT_ID,
        owner_id=OWNER,
        name="Quarterly sales",
        prompt="سه ماه گذشته",
        connection_id=CONNECTION_ID,
        llm_config_id=LLM_ID,
        language="fa",
        status="ACTIVE",
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _section(
    section_id: UUID = SECTION_ID,
    position: int = 1,
    heading: str = "روند درآمد",
    kind: str = "NORMAL",
) -> ReportSection:
    return ReportSection(
        id=section_id,
        report_id=REPORT_ID,
        position=position,
        heading=heading,
        intent="how revenue moved",
        kind=kind,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _summary_section(position: int = 0) -> ReportSection:
    return _section(
        SUMMARY_SECTION_ID,
        position=position,
        heading="خلاصه مدیریتی",
        kind="EXECUTIVE_SUMMARY",
    )


def _block(
    *,
    sql: str = GOOD_SQL,
    section_id: UUID = SECTION_ID,
    position: int = 1,
    block_type: str = "CHART",
    question: str = "revenue by status",
    title: str = "",
) -> ReportBlock:
    return ReportBlock(
        id=uuid4(),
        section_id=section_id,
        position=position,
        question=question,
        title=title,
        sql=sql,
        sql_hash="hash-of-" + sql[:20],
        sql_origin="GENERATED",
        block_type=block_type,
        chart_config=None,
        time_window="last_3_months",
        feasibility_status="FEASIBLE" if sql else "UNCHECKED",
        max_rows=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _run(status: str = ReportRunStatus.QUEUED) -> ReportRun:
    return ReportRun(
        id=RUN_ID,
        report_id=REPORT_ID,
        owner_id=OWNER,
        status=status,
        phase="",
        progress_current=0,
        progress_total=0,
        llm_config_id=LLM_ID,
        model_snapshot={"provider": "openai", "model": "m"},
        prompt_version="r1",
        language="fa",
        created_at=utcnow(),
    )


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None

    def __iter__(self) -> Any:
        return iter(self._rows)


class FakeSnapshotRow:
    def __init__(self) -> None:
        self.tables = SNAPSHOT["tables"]
        self.relationships = SNAPSHOT["relationships"]
        self.dialect = SNAPSHOT["dialect"]
        self.version = 1


class FakeGateway:
    """Stands in for the provider. Records every prompt it was sent.

    `replies` is consumed in order so a test can make the third section fail
    while the two before it succeed — which is the case the whole
    section-by-section design exists for.
    """

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies) or [PROSE]
        self.calls: list[list[ChatMessage]] = []

    async def complete(self, _llm: Any, messages: Any) -> Completion:
        self.calls.append(list(messages))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return Completion(text=reply)

    @property
    def prompts(self) -> list[str]:
        return [m.content for call in self.calls for m in call if m.role == "user"]


class FakeDb:
    """Answers by table name, as the other report fakes do.

    It also records the *order* of writes and commits, because "every result is
    written the moment it lands" is a claim about ordering: a fake that only
    collected the rows could not tell that design from one final transaction.
    """

    def __init__(
        self,
        *,
        run: ReportRun | None = None,
        report: Report | None = None,
        connection: DatabaseConnection | None = None,
        sections: list[ReportSection] | None = None,
        blocks: list[ReportBlock] | None = None,
        active_runs: list[ReportRun] | None = None,
        llm_config: Any = None,
        snapshot: bool = True,
    ) -> None:
        self.run = run
        self.report = report
        self.connection = connection
        self.sections = sections or []
        self.blocks = blocks or []
        self.active_runs = active_runs or []
        self.llm_config = llm_config or _llm_config()
        self.snapshot = snapshot
        self.added: list[Any] = []
        self.journal: list[str] = []
        self.commits = 0
        self.flushes = 0

    async def execute(self, statement: Any) -> FakeResult:
        sql = str(statement).lower()
        if "schema_snapshots" in sql:
            return FakeResult([FakeSnapshotRow()] if self.snapshot else [])
        # Declared above `report_blocks`/`report_sections`: the result tables
        # are what a retry reads back, and "report_block_results" contains
        # neither of the other two as a substring only by luck.
        if "report_block_results" in sql:
            return FakeResult(list(self.results))
        if "report_section_results" in sql:
            return FakeResult(list(self.prose))
        if "report_runs" in sql:
            return FakeResult(
                list(self.active_runs)
                if "status in" in sql
                else ([self.run] if self.run else [])
            )
        if "report_blocks" in sql:
            return FakeResult(list(self.blocks))
        if "report_sections" in sql:
            return FakeResult(list(self.sections))
        if "reports" in sql:
            return FakeResult([self.report] if self.report else [])
        if "database_connections" in sql:
            return FakeResult([self.connection] if self.connection else [])
        if "llm_configs" in sql:
            return FakeResult([self.llm_config] if self.llm_config else [])
        return FakeResult([])

    async def get(self, model: Any, _pk: Any) -> Any:
        return {
            ReportRun: self.run,
            Report: self.report,
            DatabaseConnection: self.connection,
            LlmConfig: self.llm_config,
        }.get(model)

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.journal.append(f"add:{type(obj).__name__}")

    async def delete(self, obj: Any) -> None:
        if obj in self.added:
            self.added.remove(obj)
        self.journal.append(f"delete:{type(obj).__name__}")

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, _obj: Any) -> None: ...

    async def commit(self) -> None:
        self.commits += 1
        self.journal.append("commit")

    @property
    def results(self) -> list[ReportBlockResult]:
        return [o for o in self.added if isinstance(o, ReportBlockResult)]

    @property
    def prose(self) -> list[ReportSectionResult]:
        return [o for o in self.added if isinstance(o, ReportSectionResult)]


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> FakeConnector:
    """One connector for the whole run, and nothing that dials a network."""
    fake = FakeConnector()
    monkeypatch.setattr(query_service, "bind_connector", lambda *a, **k: fake)
    return fake


@pytest.fixture(autouse=True)
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    """No test in this file reaches a provider.

    Autouse because narration is now part of *every* run: a test about block
    execution that quietly spent a real model call would be a test that only
    passes with an API key.
    """
    fake = FakeGateway()
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _cls, _s: fake)
    )
    return fake


def _settings() -> Any:
    return FakeSettings()


async def _generate(db: FakeDb, cancelled: asyncio.Event | None = None) -> None:
    await generate_run(db, _settings(), RUN_ID, cancelled or asyncio.Event())


async def _retry(
    db: FakeDb, section_id: UUID, cancelled: asyncio.Event | None = None
) -> None:
    await retry_section(
        db, _settings(), RUN_ID, section_id, cancelled or asyncio.Event()
    )


# ── the happy path ───────────────────────────────────────────────────────
async def test_a_run_writes_one_result_per_block_in_document_order(
    connector: FakeConnector,
) -> None:
    """Two sections, three blocks, one document. The order is the reader's,
    not the database's: section position first, then block position."""
    first, second = _block(position=2, question="second"), _block(position=1, question="first")
    third = _block(sql=OTHER_SQL, section_id=OTHER_SECTION_ID, position=1, question="third")
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section(), _section(OTHER_SECTION_ID, position=2, heading="محصولات")],
        blocks=[first, second, third],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.SUCCEEDED
    assert [r.question_snapshot for r in db.results] == ["first", "second", "third"]
    assert [r.position for r in db.results] == [0, 1, 2]
    # The heading travels with the numbers so the run survives its section
    # being renamed or deleted.
    assert [r.heading_snapshot for r in db.results] == [
        "روند درآمد", "روند درآمد", "محصولات"
    ]


async def test_a_result_carries_the_numbers_the_chart_and_the_statement(
    connector: FakeConnector,
) -> None:
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block()],
    )

    await _generate(db)

    result = db.results[0]
    assert result.status == "OK"
    assert result.row_count == 3
    assert [c["name"] for c in result.columns] == ["status", "total_amount"]
    # Planned on this side, from the real result's shape — the browser is handed
    # a spec, never asked to decide.
    assert result.vega_spec is not None
    # Snapshotted, and the hash with it: comparing two runs whose SQL differs
    # is a lie, which is the only reason the column exists.
    assert result.sql_text == GOOD_SQL
    assert result.sql_hash.startswith("hash-of-")


async def test_only_a_metric_block_asks_for_a_kpi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profiling a five-thousand-row table to build a big number nobody will
    look at is work with no reader — so it is asked for, never inferred."""
    seen: dict[str, Any] = {}

    async def _spy(_db: Any, _settings: Any, *, requests: list[Any], owner_id: UUID) -> dict:
        seen["requests"] = requests
        seen["owner_id"] = owner_id
        return {}

    monkeypatch.setattr(worker, "execute_many", _spy)
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[
            _block(position=1, block_type="METRIC"),
            _block(position=2, block_type="CHART"),
        ],
    )

    await _generate(db)

    assert [r.want_kpi for r in seen["requests"]] == [True, False]
    # Executed as the run's owner, never as anything wider.
    assert seen["owner_id"] == OWNER


# ── a run is a set of parts ──────────────────────────────────────────────
async def test_a_refused_block_does_not_fail_its_neighbours(
    connector: FakeConnector,
) -> None:
    """`PARTIAL` is the honest answer, and nothing else in this codebase has
    it, because nothing else generates independently-failable parts."""
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[
            _block(position=1, question="good"),
            _block(sql=REFUSED_SQL, position=2, question="refused"),
        ],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.PARTIAL
    good, refused = db.results
    assert good.status == "OK" and good.row_count == 3
    assert refused.status == "FAILED"
    # The guard's own reason, on the row, where the reader of the document is.
    assert refused.error_code is not None
    assert refused.error_message


async def test_every_block_failing_is_a_failed_run(connector: FakeConnector) -> None:
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block(sql=REFUSED_SQL, position=1)],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.FAILED


async def test_a_block_with_no_query_says_so_rather_than_crashing_the_run(
    connector: FakeConnector,
) -> None:
    """An unchecked block reaching a run is a user who edited a question and
    generated without re-checking. It gets a row that names the fix."""
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block(position=1), _block(sql="", position=2)],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.PARTIAL
    unchecked = db.results[1]
    assert unchecked.status == "FAILED"
    assert unchecked.error_code == "E_SQL_MISSING"
    assert "Check it" in (unchecked.error_message or "")


@pytest.mark.parametrize(
    "outcomes,expected",
    [
        ([True, True], ReportRunStatus.SUCCEEDED),
        ([True, False], ReportRunStatus.PARTIAL),
        ([False, False], ReportRunStatus.FAILED),
        ([], ReportRunStatus.FAILED),
    ],
)
def test_the_status_is_read_off_the_parts(outcomes: list[bool], expected: str) -> None:
    """Derived, never set — which is what lets a Phase 6 retry turn a PARTIAL
    run into a SUCCEEDED one with no state transition to write."""
    assert derive_status(outcomes) == expected


# ── disclosure, re-checked ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "policy", [DisclosurePolicy.NONE, DisclosurePolicy.AGGREGATE]
)
async def test_a_policy_tightened_after_creation_fails_the_run(
    policy: str, connector: FakeConnector
) -> None:
    """CLAUDE.md invariant #4: disclosure filters at render time, never only at
    write time. The gate at creation says what was true then."""
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(policy),
        sections=[_section()],
        blocks=[_block()],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.FAILED
    assert policy in (db.run.error_message or "")
    assert "SAMPLE or FULL" in (db.run.error_message or "")
    # Not one query is spent finding this out, and no hollow document is left
    # behind to read.
    assert connector.calls == []
    assert db.results == []


async def test_a_removed_connection_fails_the_run_readably(
    connector: FakeConnector,
) -> None:
    """SET NULL, not CASCADE: the report and its past runs survive, and the
    next generation says why it cannot happen."""
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=None,
        sections=[_section()],
        blocks=[_block()],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.FAILED
    assert "removed" in (db.run.error_message or "")
    assert "Past runs stay readable" in (db.run.error_message or "")


# ── progress, and the poll it feeds ──────────────────────────────────────
async def test_every_result_is_committed_as_it_lands(
    connector: FakeConnector,
) -> None:
    """The poll response is a snapshot of what exists so far. A run that wrote
    everything in one final transaction would render all at once, whatever the
    frontend did."""
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block(position=1), _block(sql=OTHER_SQL, position=2)],
    )

    await _generate(db)

    writes = [e for e in db.journal if e != "commit"]
    assert writes == [
        "add:ReportBlockResult",
        "add:ReportBlockResult",
        # The paragraph lands after the numbers it is written from, and it is
        # its own poll's worth of document.
        "add:ReportSectionResult",
    ]
    # Every row is followed by its own commit, never batched behind the last.
    for index, entry in enumerate(db.journal):
        if entry.startswith("add:"):
            assert db.journal[index + 1] == "commit", entry


async def test_the_header_has_something_to_render_while_it_runs(
    connector: FakeConnector,
) -> None:
    """`phase` and the two counters are what «در حال تولید بخش ۳ از ۷» is
    rendered from, and they are on the poll response already."""
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block(position=1), _block(sql=OTHER_SQL, position=2)],
    )

    await _generate(db)

    assert db.run is not None
    # Two queries and one paragraph: the header counts both kinds of work.
    assert db.run.progress_total == 3
    assert db.run.progress_current == 3
    assert db.run.started_at is not None and db.run.finished_at is not None


# ── cancellation ─────────────────────────────────────────────────────────
async def test_cancelling_before_the_queries_run_spends_nothing(
    connector: FakeConnector,
) -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block()],
    )

    await _generate(db, cancelled)

    assert db.run is not None and db.run.status == ReportRunStatus.CANCELLED
    assert connector.calls == []
    assert db.results == []


async def test_a_cancelled_run_keeps_what_it_already_computed(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector
) -> None:
    """Cancellation stops a run; it does not erase one. Throwing away results
    that were already paid for is a slower way of doing nothing."""
    cancelled = asyncio.Event()

    real = query_service.execute_many

    async def _then_cancel(*args: Any, **kwargs: Any) -> dict:
        results = await real(*args, **kwargs)
        cancelled.set()  # the user hit cancel while the queries were in flight
        return results

    monkeypatch.setattr(worker, "execute_many", _then_cancel)
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block()],
    )

    await _generate(db, cancelled)

    assert db.run is not None and db.run.status == ReportRunStatus.CANCELLED
    assert db.run.finished_at is not None
    assert len(db.results) == 1 and db.results[0].status == "OK"


async def test_a_crash_mid_run_is_a_failed_run_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that raised would leave the row RUNNING until a restart swept
    it, and the user staring at a progress bar that never moves."""

    async def _boom(*_args: Any, **_kwargs: Any) -> dict:
        raise RuntimeError("the driver went away")

    monkeypatch.setattr(worker, "execute_many", _boom)
    db = FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block()],
    )

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.FAILED
    assert "driver went away" in (db.run.error_message or "")


# ── the document ─────────────────────────────────────────────────────────
def _readable(**overrides: Any) -> FakeDb:
    """A report that generates into a document: two sections and a summary."""
    fields: dict[str, Any] = {
        "run": _run(),
        "report": _report(),
        "connection": _connection(),
        "sections": [
            _summary_section(position=0),
            _section(position=1),
            _section(OTHER_SECTION_ID, position=2, heading="محصولات"),
        ],
        "blocks": [
            _block(position=1),
            _block(
                sql=OTHER_SQL,
                section_id=OTHER_SECTION_ID,
                position=1,
                question="top products",
            ),
        ],
    }
    return FakeDb(**{**fields, **overrides})


async def test_each_section_gets_a_paragraph_written_over_its_own_results(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    db = _readable()

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.SUCCEEDED
    assert [row.heading_snapshot for row in db.prose] == [
        "روند درآمد", "محصولات", "خلاصه مدیریتی",
    ]
    assert all(row.prose == PROSE for row in db.prose)
    # Written last, but positioned where the user put it — first.
    assert [row.position for row in db.prose] == [1, 2, 0]


async def test_a_section_is_shown_only_its_own_blocks(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Prose is per-section and data is per-block. A paragraph handed the whole
    report's results would narrate the section above it."""
    db = _readable()

    await _generate(db)

    revenue, products, _summary = gateway.prompts
    assert "revenue by status" in revenue and "top products" not in revenue
    assert "revenue by status" not in products


async def test_the_summary_is_written_last_and_from_the_prose(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """It is given no data of its own: a summary that could reach the rows
    would be a second place for a figure to be invented."""
    db = _readable()

    await _generate(db)

    summary = gateway.prompts[-1]
    assert PROSE in summary
    assert "SELECT" not in summary and "total_amount" not in summary
    # And it is the last row written, whatever position it renders at.
    assert [r for r in db.prose if r.section_id == SUMMARY_SECTION_ID] == db.prose[-1:]


async def test_the_prose_is_written_from_what_disclosure_permitted(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector, gateway: FakeGateway
) -> None:
    """The policy is applied at *narration* time, so the one in force now is
    the one that governs what the model reads — never the one that happened to
    be in force when the query ran."""
    monkeypatch.setattr(worker, "disclose", worker.disclose)
    db = _readable()

    await _generate(db)

    prompt = gateway.prompts[0]
    # SAMPLE shares values, and these are the fixture's own rows.
    assert "paid | 120.0" in prompt


async def test_the_language_is_named_in_every_prose_call(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Pinned per report, stated per call. A section whose heading is a metric
    name must not come back in the other language."""
    db = _readable()

    await _generate(db)

    assert all("Persian (فارسی)" in prompt for prompt in gateway.prompts)


async def test_the_figures_in_a_paragraph_are_checked_against_the_results(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Tier 2 of §9, and it costs nothing: the check runs on every section."""
    db = _readable()

    await _generate(db)

    check = db.prose[0].numeric_check
    assert check is not None
    # Both figures are accounted for, and from different places: ۱۲۰ is a
    # cell, ۳ is the row count — which no cell holds and every paragraph
    # reaches for.
    assert check["checked"] == 2
    assert check["findings"] == []


async def test_a_hallucinated_figure_is_flagged_and_the_section_is_still_saved(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It flags; it never blocks. A check that refused to save a section over
    one figure would be worse than the hallucination it was guarding against."""
    invented = FakeGateway("درآمد به ۹٬۹۰۰٬۰۰۰ تومان رسید.")
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _c, _s: invented)
    )
    db = _readable()

    await _generate(db)

    assert db.run is not None and db.run.status == ReportRunStatus.SUCCEEDED
    row = db.prose[0]
    assert row.status == "OK"
    assert row.prose  # saved, in full
    assert row.numeric_check is not None
    assert [f["value"] for f in row.numeric_check["findings"]] == [9_900_000]


async def test_a_section_with_no_data_is_not_a_failure(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """A report that says "nothing was recorded in this period" is correct; one
    that hallucinates the rows is not — so the sentence is canned and no model
    call is spent on it."""
    empty = FakeConnector(rows=[])
    db = _readable(sections=[_section(position=1)], blocks=[_block(position=1)])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(query_service, "bind_connector", lambda *a, **k: empty)
        await _generate(db)

    row = db.prose[0]
    assert row.status == "SKIPPED_NO_DATA"
    assert row.prose == "در این بازه داده‌ای برای این بخش ثبت نشده است."
    assert gateway.calls == [], "an empty section must cost no tokens"
    # Not a failure: the run succeeded, and the document says so plainly.
    assert db.run is not None and db.run.status == ReportRunStatus.SUCCEEDED


async def test_a_section_whose_every_query_broke_is_a_failure(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Different from emptiness, and it must stay different: a paragraph
    written over three rejections would be fiction."""
    db = _readable(
        sections=[_section(position=1)], blocks=[_block(sql=REFUSED_SQL, position=1)]
    )

    await _generate(db)

    row = db.prose[0]
    assert row.status == "FAILED"
    assert row.error_message
    assert gateway.calls == []
    assert db.run is not None and db.run.status == ReportRunStatus.FAILED


async def test_a_provider_failure_costs_its_section_and_not_the_run(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule the semantic generator follows per table, per section here: the
    six paragraphs that worked are worth keeping."""
    flaky = FakeGateway(PROSE, RuntimeError("provider exploded"), PROSE)
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _c, _s: flaky)
    )
    db = _readable()

    await _generate(db)

    assert [row.status for row in db.prose] == ["OK", "FAILED", "OK"]
    assert "provider exploded" in (db.prose[1].error_message or "")
    assert db.run is not None and db.run.status == ReportRunStatus.PARTIAL


async def test_a_run_whose_model_was_deleted_still_produces_its_numbers(
    connector: FakeConnector,
) -> None:
    """`llm_config_id` is SET NULL. The queries already ran; losing the prose
    must not lose them."""
    db = _readable(llm_config=None)
    db.llm_config = None

    await _generate(db)

    assert len(db.results) == 2
    assert all(row.status == "OK" for row in db.results)
    # Every paragraph says why it is missing — including the summary, which
    # must not report "no data" when the data is there and the writer is not.
    assert all(row.status == "FAILED" for row in db.prose)
    assert all("model configuration" in (r.error_message or "") for r in db.prose)


async def test_cancelling_between_sections_stops_before_the_next_call(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cooperative: the flag is read between sections so an in-flight provider
    call finishes rather than being abandoned mid-request."""
    cancelled = asyncio.Event()
    stopping = FakeGateway(PROSE, PROSE)

    async def complete(_llm: Any, messages: Any) -> Completion:
        cancelled.set()
        return await FakeGateway.complete(stopping, _llm, messages)

    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _c, _s: stopping)
    )
    monkeypatch.setattr(stopping, "complete", complete)
    db = _readable()

    await _generate(db, cancelled)

    assert db.run is not None and db.run.status == ReportRunStatus.CANCELLED
    # The first section was written and kept; the second was never asked for.
    assert len(db.prose) == 1
    assert len(stopping.calls) == 1


# ── retrying one section ─────────────────────────────────────────────────
async def test_a_retry_rewrites_only_its_own_rows(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    db = _readable()
    await _generate(db)
    before = {row.id for row in db.results}

    await _retry(db, SECTION_ID)

    survivors = {row.id for row in db.results} & before
    # One block result replaced, one left exactly as it was.
    assert len(survivors) == 1
    assert [row.heading_snapshot for row in db.prose].count("محصولات") == 1
    assert [row.heading_snapshot for row in db.prose].count("روند درآمد") == 1


async def test_a_retry_puts_the_section_back_where_it_was(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """A retried section must not jump to the end of the document."""
    db = _readable()
    await _generate(db)
    original = next(r.position for r in db.results if r.section_id == SECTION_ID)

    await _retry(db, SECTION_ID)

    assert [r.position for r in db.results if r.section_id == SECTION_ID] == [original]


async def test_a_successful_retry_turns_a_partial_run_into_a_succeeded_one(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector
) -> None:
    """No state machine anywhere: the status is read off the rows the run now
    holds, so this falls out of replacing them."""
    flaky = FakeGateway(RuntimeError("provider exploded"), PROSE, PROSE)
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _c, _s: flaky)
    )
    db = _readable()
    await _generate(db)
    assert db.run is not None and db.run.status == ReportRunStatus.PARTIAL

    await _retry(db, SECTION_ID)

    assert db.run.status == ReportRunStatus.SUCCEEDED
    assert db.run.finished_at is not None


async def test_retrying_the_summary_rewrites_it_from_the_sections_as_they_stand(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Including a paragraph the user has since rewritten: a summary of a draft
    nobody is looking at is not a summary."""
    db = _readable()
    await _generate(db)
    db.prose[0].edited_prose = "درآمد را خودم نوشتم."

    await _retry(db, SUMMARY_SECTION_ID)

    summary = gateway.prompts[-1]
    assert "درآمد را خودم نوشتم." in summary
    assert summary.count(PROSE) == 1  # the other section's, unedited


async def test_a_retry_of_a_deleted_section_re_derives_rather_than_hanging(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Deleted between the request and the worker picking it up. The run must
    not be left RUNNING."""
    db = _readable()
    await _generate(db)
    db.run.status = ReportRunStatus.RUNNING  # type: ignore[union-attr]

    await _retry(db, uuid4())

    assert db.run is not None and db.run.status == ReportRunStatus.SUCCEEDED


async def test_a_retry_re_checks_disclosure_like_every_other_entry(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    db = _readable()
    await _generate(db)
    db.connection = _connection(DisclosurePolicy.NONE)

    await _retry(db, SECTION_ID)

    assert db.run is not None and db.run.status == ReportRunStatus.FAILED
    assert "NONE" in (db.run.error_message or "")


# ── starting one ─────────────────────────────────────────────────────────
def _service(db: FakeDb) -> ReportService:
    return ReportService(db, _settings())  # type: ignore[arg-type]


def _creatable(**overrides: Any) -> FakeDb:
    fields: dict[str, Any] = {
        "report": _report(),
        "connection": _connection(),
        "sections": [_section()],
        "blocks": [_block()],
        "llm_config": _llm_config(),
    }
    return FakeDb(**{**fields, **overrides})


async def test_a_queued_run_snapshots_the_model_and_the_language() -> None:
    """Which model wrote this document, and in which language, kept beside it:
    six months later nobody remembers either."""
    db = _creatable()

    run = await _service(db).create_run(REPORT_ID, OWNER)

    assert run.status == ReportRunStatus.QUEUED
    assert run.model_snapshot == {"provider": "openai", "model": "m"}
    assert run.language == "fa"
    assert run.progress_total == 2  # one block, one section
    assert run.prompt_version


async def test_a_second_run_while_one_is_in_flight_is_refused() -> None:
    """Two generations of one report race on the same rows, and the loser's
    queries are spent for nothing."""
    db = _creatable(active_runs=[_run(ReportRunStatus.RUNNING)])

    with pytest.raises(ConflictError):
        await _service(db).create_run(REPORT_ID, OWNER)

    assert db.added == []


async def test_a_report_whose_blocks_were_never_checked_cannot_be_generated() -> None:
    db = _creatable(blocks=[_block(sql="")])

    with pytest.raises(ValidationError) as raised:
        await _service(db).create_run(REPORT_ID, OWNER)

    assert "Check them" in raised.value.message
    assert db.added == []


async def test_the_gate_is_at_creation_too_not_only_at_run_start() -> None:
    """Two places, §7: refusing here is what lets the picker grey a connection
    out instead of queueing a run that will fail a second later."""
    from app.core.errors import DisclosureTooNarrowError

    db = _creatable(connection=_connection(DisclosurePolicy.AGGREGATE))

    with pytest.raises(DisclosureTooNarrowError):
        await _service(db).create_run(REPORT_ID, OWNER)


async def test_cancelling_a_finished_run_changes_nothing() -> None:
    db = FakeDb(report=_report(), run=_run(ReportRunStatus.SUCCEEDED))

    assert await _service(db).cancel_run(REPORT_ID, RUN_ID, OWNER) is False
    assert db.run is not None and db.run.status == ReportRunStatus.SUCCEEDED


async def test_a_restart_reports_the_runs_it_interrupted_rather_than_failing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process that died mid-run leaves a row saying RUNNING forever.

    **Changed deliberately in Phase 4** of `docs/langgraph-migration.md`. This
    used to fail every such run on the reasoning that "nothing here knows how
    far it got" — which was never quite true: the result and section rows say
    exactly how far it got. A report run is minutes long, so failing it charged
    the user again for every section that had already finished.

    Now the sweep only *names* them, and startup hands each to the executor to
    be resumed. It writes nothing, which is the other half of the change: a run
    it touched and then failed to resume would be worse off than one it left
    alone.
    """
    from app.infra.db import session as session_module

    stranded = [_run(ReportRunStatus.RUNNING), _run(ReportRunStatus.QUEUED)]
    db = FakeDb(active_runs=stranded)

    class _Maker:
        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> Any:
            return db

        async def __aexit__(self, *_exc: Any) -> None: ...

    monkeypatch.setattr(session_module, "get_sessionmaker", lambda: _Maker())

    assert await worker.stranded_runs() == [run.id for run in stranded]
    # Untouched: not failed, not finished, and nothing committed.
    assert all(run.status != ReportRunStatus.FAILED for run in stranded)
    assert all(run.finished_at is None for run in stranded)
    assert db.commits == 0


async def test_cancelling_a_running_run_writes_the_row_immediately() -> None:
    """Written here rather than by the worker, so the next poll says CANCELLED
    even while an in-flight query is still finishing."""
    db = FakeDb(report=_report(), run=_run(ReportRunStatus.RUNNING))

    assert await _service(db).cancel_run(REPORT_ID, RUN_ID, OWNER) is True
    assert db.run is not None
    assert db.run.status == ReportRunStatus.CANCELLED
    assert db.run.finished_at is not None
