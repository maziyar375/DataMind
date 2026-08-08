"""The report service's contract: the gate, the pin, and what an edit destroys.

Most of this file is ordinary CRUD and is tested through the routes. What is
tested here is the three rules that are not CRUD, each of which is invisible to
a route test because the service is faked there:

* **The disclosure gate.** A report is entirely narration written from result
  values, so a connection that hands the model none cannot carry one. Refused
  before a row is written — and `assert_wide_enough` is a free function so the
  worker can call the same one at the start of every generation (Phase 5).
* **The connection is pinned.** Byte-for-byte the conversation rule; re-sending
  the same one is a no-op, a different one is 422.
* **Editing a question un-checks its SQL.** The stored statement answers the
  question that was checked. Keeping the *verdict* means a run producing the
  right numbers under the wrong heading — the failure this whole reset exists
  to prevent. Whether the statement itself survives depends on who wrote it:
  a model draft is dropped, a hand-written one is kept.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import (
    ConflictError,
    DisclosureTooNarrowError,
    LLMError,
    NotFoundError,
    ValidationError,
)
from app.domain.value_objects import (
    DisclosurePolicy,
    ReportFeasibility,
    ReportSectionKind,
)
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    Report,
    ReportBlock,
    ReportBlockResult,
    ReportRun,
    ReportSection,
)
from app.reports.outline import OutlineProposal, ProposedBlock, ProposedSection
from app.services import report_service
from app.services.report_service import ReportService, assert_wide_enough, is_wide_enough

OWNER = uuid4()
OTHER = uuid4()
REPORT_ID = uuid4()
SECTION_ID = uuid4()
BLOCK_ID = uuid4()
CONNECTION_ID = uuid4()
RUN_ID = uuid4()
RESULT_ID = uuid4()


def _connection(policy: str = DisclosurePolicy.SAMPLE, max_rows: int = 1000) -> Any:
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
        max_rows=max_rows,
        disclosure_policy=policy,
    )


def _report(owner_id: UUID = OWNER) -> Report:
    return Report(
        id=REPORT_ID,
        owner_id=owner_id,
        name="Quarterly sales",
        description=None,
        prompt="a report on the last three months",
        connection_id=CONNECTION_ID,
        llm_config_id=None,
        language="fa",
        status="ACTIVE",
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _section() -> ReportSection:
    return ReportSection(
        id=SECTION_ID,
        report_id=REPORT_ID,
        position=1,
        heading="Revenue",
        intent="how revenue moved",
        kind="NORMAL",
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _block(**overrides: Any) -> ReportBlock:
    fields: dict[str, Any] = {
        "id": BLOCK_ID,
        "section_id": SECTION_ID,
        "position": 1,
        "question": "revenue by month",
        "sql": "SELECT month, revenue FROM public.sales",
        "sql_hash": "abc123",
        "sql_origin": "GENERATED",
        "block_type": "CHART",
        "chart_config": None,
        "time_window": "last_3_months",
        "feasibility_status": ReportFeasibility.FEASIBLE,
        "feasibility_reason": None,
        "feasibility_checked_at": utcnow(),
        "max_rows": None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    return ReportBlock(**{**fields, **overrides})


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
    def __init__(self, tables: list[dict]) -> None:
        self.tables = tables
        self.relationships: list[dict] = []
        self.dialect = "postgres"
        self.version = 1


class FakeDb:
    """Answers by table name, the way `test_dashboard_service.FakeDb` does."""

    def __init__(
        self,
        *,
        report: Report | None = None,
        sections: list[ReportSection] | None = None,
        blocks: list[ReportBlock] | None = None,
        connection: Any = None,
        duplicate_name: bool = False,
        snapshot_tables: list[dict] | None = None,
        llm_config: Any = None,
        run: Any = None,
        block_results: list[Any] | None = None,
    ) -> None:
        self.report = report
        self.sections = sections or []
        self.blocks = blocks or []
        self.connection = connection
        self.duplicate_name = duplicate_name
        self.snapshot_tables = snapshot_tables or []
        self.llm_config = llm_config
        self.run = run
        self.block_results = block_results or []
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.refreshed: list[Any] = []
        self.flushes = 0
        self._name_lookups = 0

    async def execute(self, statement: Any) -> FakeResult:
        sql = str(statement).lower()
        if "schema_snapshots" in sql:
            return FakeResult(
                [FakeSnapshotRow(self.snapshot_tables)] if self.snapshot_tables else []
            )
        if "max(" in sql:
            rows = self.blocks if "report_blocks" in sql else self.sections
            return FakeResult([max((r.position for r in rows), default=0)])
        if "report_block_results" in sql:
            return FakeResult(list(self.block_results))
        if "report_runs" in sql:
            return FakeResult([self.run] if self.run else [])
        if "report_blocks" in sql:
            return FakeResult(list(self.blocks))
        if "report_sections" in sql:
            return FakeResult(list(self.sections))
        if "reports" in sql:
            # Matched on the *predicate*, not the table: every `select(Report)`
            # names `reports.name` in its column list, so only `... name = :x`
            # identifies the uniqueness probe. It must answer "nothing found"
            # unless the test asked for a collision, or every create would 409
            # against its own report.
            if "reports.name = " in sql:
                return FakeResult([self.report] if self.duplicate_name else [])
            return FakeResult([self.report] if self.report else [])
        if "database_connections" in sql:
            return FakeResult([self.connection] if self.connection else [])
        if "llm_configs" in sql:
            return FakeResult([self.llm_config] if self.llm_config else [])
        return FakeResult([])

    async def get(self, _model: Any, _pk: Any) -> Any:
        return self.connection

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)


class FakeSettings:
    """Enough of `Settings` for the one path that decrypts a key.

    The box itself is faked in the `proposal` fixture; this only has to keep
    the lazy property from tripping over a bare `object()`.
    """

    class _Key:
        def get_secret_value(self) -> str:
            return "not-a-real-key"

    secret_box_key = _Key()
    secret_box_key_version = 1


def _service(db: FakeDb) -> ReportService:
    return ReportService(db, FakeSettings())  # type: ignore[arg-type]


# ── the disclosure gate ──────────────────────────────────────────────────
@pytest.mark.parametrize("policy", [DisclosurePolicy.NONE, DisclosurePolicy.AGGREGATE])
async def test_a_narrow_connection_cannot_carry_a_report(policy: str) -> None:
    """Prose written from no values beside charts drawn from real ones is a
    document that disagrees with itself — worse than no document."""
    db = FakeDb(connection=_connection(policy))

    with pytest.raises(DisclosureTooNarrowError) as raised:
        await _service(db).create(
            OWNER, name="Q3", connection_id=CONNECTION_ID, prompt="p"
        )

    assert raised.value.code == "E_DISCLOSURE_TOO_NARROW"
    assert raised.value.http_status == 422
    # The policy in force and both ways out of it, because the reader can act
    # on either.
    assert policy in raised.value.message
    assert "SAMPLE or FULL" in raised.value.message
    assert "different connection" in raised.value.message
    assert db.added == [], "a refused report must not be written"


@pytest.mark.parametrize("policy", [DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL])
async def test_a_wide_enough_connection_is_accepted(policy: str) -> None:
    db = FakeDb(connection=_connection(policy))

    report = await _service(db).create(
        OWNER, name="Q3", connection_id=CONNECTION_ID, prompt="p"
    )

    assert report.owner_id == OWNER
    assert db.added == [report]


def test_the_gate_is_a_free_function_the_worker_can_call() -> None:
    """§7 enforces this twice — at creation *and* at the start of every
    generation — so the check may not live inside the create path."""
    assert is_wide_enough(DisclosurePolicy.SAMPLE)
    assert not is_wide_enough(DisclosurePolicy.AGGREGATE)
    # Anything unrecognised fails closed, like `HintBudget.from_policy`.
    assert not is_wide_enough(None)
    assert not is_wide_enough("ANYTHING_ELSE")

    with pytest.raises(DisclosureTooNarrowError):
        assert_wide_enough(_connection(DisclosurePolicy.NONE))


async def test_a_report_needs_a_connection_at_all() -> None:
    with pytest.raises(ValidationError):
        await _service(FakeDb()).create(OWNER, name="Q3", connection_id=None)


async def test_someone_elses_connection_is_a_404() -> None:
    """The connection lookup is owner-scoped, so borrowing an id gets nothing."""
    with pytest.raises(NotFoundError):
        await _service(FakeDb(connection=None)).create(
            OWNER, name="Q3", connection_id=CONNECTION_ID
        )


# ── the pin ──────────────────────────────────────────────────────────────
async def test_moving_a_report_to_another_connection_is_refused() -> None:
    db = FakeDb(report=_report(), connection=_connection())

    with pytest.raises(ValidationError) as raised:
        await _service(db).update(REPORT_ID, OWNER, connection_id=uuid4())

    assert raised.value.http_status == 422
    assert "pinned" in raised.value.message


async def test_resending_the_same_connection_is_a_no_op() -> None:
    """A client that PATCHes a whole object must not be punished for it —
    exactly what `_bind_connection` does when the id is unchanged."""
    db = FakeDb(report=_report(), connection=_connection())

    report = await _service(db).update(
        REPORT_ID, OWNER, connection_id=CONNECTION_ID, name="Renamed"
    )

    assert report.name == "Renamed"
    assert report.connection_id == CONNECTION_ID


async def test_the_model_stays_swappable() -> None:
    """It decides who writes the prose, not what is in it."""
    db = FakeDb(report=_report(), connection=_connection())

    report = await _service(db).update(REPORT_ID, OWNER, llm_config_id=None)

    assert report.llm_config_id is None
    # An UPDATE does not fetch `updated_at` back, so the row is refreshed
    # before anything serialises it — otherwise MissingGreenlet, in production.
    assert db.refreshed == [report]


async def test_a_duplicate_name_is_a_409() -> None:
    db = FakeDb(report=_report(), connection=_connection(), duplicate_name=True)

    with pytest.raises(ConflictError):
        await _service(db).create(OWNER, name="Quarterly sales", connection_id=CONNECTION_ID)


async def test_another_users_report_is_a_404() -> None:
    """404, not 403: someone else's report is indistinguishable from one that
    never existed."""
    db = FakeDb(report=None)

    with pytest.raises(NotFoundError):
        await _service(db).get(REPORT_ID, OTHER)


# ── editing a block ──────────────────────────────────────────────────────
async def test_editing_the_question_drops_the_sql_it_was_checked_against() -> None:
    """Otherwise the run produces last week's numbers under this week's
    heading, and nothing in the document says so."""
    block = _block()
    db = FakeDb(report=_report(), sections=[_section()], blocks=[block], connection=_connection())

    updated = await _service(db).update_block(
        REPORT_ID, BLOCK_ID, OWNER, question="revenue by week"
    )

    assert updated.question == "revenue by week"
    assert updated.sql == "" and updated.sql_hash == ""
    assert updated.feasibility_status == ReportFeasibility.UNCHECKED
    assert updated.feasibility_reason is None
    assert updated.feasibility_checked_at is None


async def test_changing_the_time_window_invalidates_the_sql_too() -> None:
    """The window lives *in* the statement as relative date arithmetic, so a
    new label and the old SQL describe different periods."""
    block = _block()
    db = FakeDb(report=_report(), sections=[_section()], blocks=[block], connection=_connection())

    updated = await _service(db).update_block(
        REPORT_ID, BLOCK_ID, OWNER, time_window="ytd"
    )

    assert updated.sql == ""
    assert updated.feasibility_status == ReportFeasibility.UNCHECKED


@pytest.mark.parametrize("origin", ["HANDWRITTEN", "GENERATED_EDITED"])
async def test_editing_the_question_keeps_a_statement_someone_typed(
    origin: str,
) -> None:
    """The verdict goes; the SQL stays.

    A generated draft costs one click to reproduce. An hour of hand-written
    SQL does not, and losing it to a typo fix in the heading above it is the
    kind of thing a person never forgives a tool for. It is `UNCHECKED` either
    way, so nothing runs on it until the user says the two still belong
    together.
    """
    block = _block(sql_origin=origin)
    db = FakeDb(report=_report(), sections=[_section()], blocks=[block], connection=_connection())

    updated = await _service(db).update_block(
        REPORT_ID, BLOCK_ID, OWNER, question="revenue by week"
    )

    assert updated.sql == "SELECT month, revenue FROM public.sales"
    assert updated.sql_hash == "abc123"
    assert updated.sql_origin == origin
    # But it is not trusted: the pairing of question and statement is exactly
    # what has to be looked at again.
    assert updated.feasibility_status == ReportFeasibility.UNCHECKED
    assert updated.feasibility_checked_at is None


async def test_changing_the_chart_type_keeps_the_sql() -> None:
    """A chart is drawn from a result that has already been computed; re-running
    the query to change a mark would be absurd."""
    block = _block()
    db = FakeDb(report=_report(), sections=[_section()], blocks=[block], connection=_connection())

    updated = await _service(db).update_block(
        REPORT_ID, BLOCK_ID, OWNER, chart_config={"chart_type": "line"}
    )

    assert updated.sql == "SELECT month, revenue FROM public.sales"
    assert updated.feasibility_status == ReportFeasibility.FEASIBLE


async def test_resending_the_same_question_changes_nothing() -> None:
    """A whole-object PATCH from the editor must not silently invalidate a
    block the user did not touch."""
    block = _block()
    db = FakeDb(report=_report(), sections=[_section()], blocks=[block], connection=_connection())

    updated = await _service(db).update_block(
        REPORT_ID, BLOCK_ID, OWNER, question="revenue by month", block_type="TABLE"
    )

    assert updated.block_type == "TABLE"
    assert updated.sql == "SELECT month, revenue FROM public.sales"
    assert updated.feasibility_status == ReportFeasibility.FEASIBLE


async def test_a_block_row_cap_may_only_tighten_the_connections() -> None:
    """Containment belongs to the connection. Stored already clamped, so the
    editor never shows a cap the connection would not honour."""
    db = FakeDb(
        report=_report(),
        sections=[_section()],
        blocks=[],
        connection=_connection(max_rows=500),
    )

    block = await _service(db).add_block(
        REPORT_ID, SECTION_ID, OWNER, question="everything", max_rows=100_000
    )

    assert block.max_rows == 500


async def test_a_new_section_is_appended_past_the_last_one() -> None:
    """`max(position) + 1`, not `count()`: a list with a deleted middle entry
    would otherwise hand the new section a position already in use."""
    existing = _section()
    existing.position = 7
    db = FakeDb(report=_report(), sections=[existing], connection=_connection())

    section = await _service(db).add_section(REPORT_ID, OWNER, heading="Returns")

    assert section.position == 8


async def test_a_section_can_be_created_first_and_not_only_appended() -> None:
    """Position 0 is a real position — it is where the executive summary goes.

    Found by generating a report through the API rather than by reading the
    code: `if not section.position` treated an explicit 0 as "not given" and
    silently appended the summary to the end of the document, where a summary
    is worth nothing.
    """
    existing = _section()
    existing.position = 7
    db = FakeDb(report=_report(), sections=[existing], connection=_connection())

    section = await _service(db).add_section(
        REPORT_ID, OWNER, heading="خلاصه مدیریتی", position=0
    )

    assert section.position == 0


async def test_a_block_can_be_created_first_too() -> None:
    db = FakeDb(report=_report(), sections=[_section()], connection=_connection())

    block = await _service(db).add_block(
        REPORT_ID, SECTION_ID, OWNER, question="revenue", position=0
    )

    assert block.position == 0


# ── proposing an outline ─────────────────────────────────────────────────
class _Proposal:
    """A stand-in for `app.reports.outline.propose`, which the service calls."""

    def __init__(self, proposal: OutlineProposal) -> None:
        self.proposal = proposal
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    async def __call__(self, _gateway: Any, _llm: Any, **kwargs: Any) -> OutlineProposal:
        self.calls += 1
        self.kwargs = kwargs
        return self.proposal


PROPOSED = OutlineProposal(
    sections=[
        ProposedSection(
            heading="روند درآمد",
            intent="how revenue moved",
            blocks=[
                ProposedBlock(question="revenue by month", time_window="last_3_months"),
                ProposedBlock(question="revenue by region", block_type="TABLE"),
            ],
        ),
        ProposedSection(
            heading="Returns",
            intent="what came back",
            blocks=[ProposedBlock(question="returns by reason", block_type="METRIC")],
        ),
    ]
)


def _outline_db(**overrides: Any) -> FakeDb:
    report = _report()
    report.llm_config_id = uuid4()
    config = LlmConfig(
        id=report.llm_config_id,
        owner_id=OWNER,
        name="deepseek",
        provider="openai",
        model="deepseek/deepseek-v4-flash",
    )
    return FakeDb(
        report=report,
        connection=_connection(),
        llm_config=config,
        snapshot_tables=[
            {
                "schema": "public",
                "name": "orders",
                "columns": [{"name": "id", "data_type": "integer"}],
            }
        ],
        **overrides,
    )


@pytest.fixture
def proposal(monkeypatch: pytest.MonkeyPatch) -> _Proposal:
    """Everything past the model call, with the model call itself faked.

    The parser has its own test against literals; what is under test here is
    what the service *does* with a proposal.
    """
    fake = _Proposal(PROPOSED)
    monkeypatch.setattr(report_service, "propose", fake)
    monkeypatch.setattr(report_service, "resolve_llm", lambda *a, **k: object())
    # The key is decrypted only on this path, so the box is built lazily — and
    # faked here rather than handing the service a real settings object.
    monkeypatch.setattr(report_service, "AesGcmSecretBox", lambda *a, **k: object())
    monkeypatch.setattr(
        report_service.LiteLLMGateway, "from_settings", staticmethod(lambda _s: object())
    )
    monkeypatch.setattr(report_service, "load_document", _none)
    return fake


async def _none(*_args: Any, **_kwargs: Any) -> None:
    return None


async def test_a_proposal_becomes_sections_and_blocks(proposal: _Proposal) -> None:
    db = _outline_db()

    await _service(db).propose_outline(REPORT_ID, OWNER)

    sections = [o for o in db.added if isinstance(o, ReportSection)]
    blocks = [o for o in db.added if isinstance(o, ReportBlock)]
    assert [s.heading for s in sections][1:] == ["روند درآمد", "Returns"]
    assert [b.question for b in blocks] == [
        "revenue by month",
        "revenue by region",
        "returns by reason",
    ]
    assert [b.time_window for b in blocks][0] == "last_3_months"


async def test_the_executive_summary_leads_and_carries_no_blocks(
    proposal: _Proposal,
) -> None:
    """Written last, read first. It is an ordinary section otherwise — the UI
    can remove it like any other."""
    db = _outline_db()

    await _service(db).propose_outline(REPORT_ID, OWNER)

    first = [o for o in db.added if isinstance(o, ReportSection)][0]
    assert first.position == 0
    assert first.kind == ReportSectionKind.EXECUTIVE_SUMMARY
    # The report is Persian, so its summary is too.
    assert "خلاصه" in first.heading
    assert not [b for b in db.added if getattr(b, "section_id", None) == first.id]


async def test_a_proposed_block_has_no_sql_and_is_unchecked(
    proposal: _Proposal,
) -> None:
    """A proposed question has never been near the guard. Phase 4 is what turns
    it into a statement."""
    db = _outline_db()

    await _service(db).propose_outline(REPORT_ID, OWNER)

    for block in [o for o in db.added if isinstance(o, ReportBlock)]:
        # Falsy rather than `== ""`: the column default lands at flush, and
        # this fake never reaches a database.
        assert not block.sql and not block.sql_hash
        assert block.feasibility_status == ReportFeasibility.UNCHECKED


async def test_proposing_again_replaces_the_outline(proposal: _Proposal) -> None:
    """Propose is the "start again" button; the section and block routes are
    how an outline is edited."""
    existing = _section()
    db = _outline_db(sections=[existing])

    await _service(db).propose_outline(REPORT_ID, OWNER)

    assert db.deleted == [existing]


async def test_the_model_gets_the_request_the_language_and_the_schema(
    proposal: _Proposal,
) -> None:
    db = _outline_db()

    await _service(db).propose_outline(REPORT_ID, OWNER)

    assert proposal.kwargs["request"] == "a report on the last three months"
    assert proposal.kwargs["language"] == "fa"
    assert proposal.kwargs["dialect"] == "postgres"
    # The same block the generator sees: schema, keys, and the semantic layer.
    assert "public.orders" in proposal.kwargs["schema_block"]


async def test_an_unreadable_reply_is_a_502_and_writes_nothing(
    proposal: _Proposal,
) -> None:
    proposal.proposal = OutlineProposal()
    db = _outline_db()

    with pytest.raises(LLMError):
        await _service(db).propose_outline(REPORT_ID, OWNER)

    assert not [o for o in db.added if isinstance(o, ReportSection)]


async def test_an_unsynced_connection_is_refused_before_a_token_is_spent(
    proposal: _Proposal,
) -> None:
    """An outline proposed against a schema nobody has read is invention."""
    db = _outline_db()
    db.snapshot_tables = []

    with pytest.raises(ValidationError):
        await _service(db).propose_outline(REPORT_ID, OWNER)

    assert proposal.calls == 0


async def test_a_report_with_no_request_has_nothing_to_propose_from(
    proposal: _Proposal,
) -> None:
    db = _outline_db()
    db.report.prompt = "   "  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        await _service(db).propose_outline(REPORT_ID, OWNER)

    assert proposal.calls == 0


async def test_a_report_with_no_model_is_refused_rather_than_defaulted(
    proposal: _Proposal,
) -> None:
    """Chat refuses the same way. A silent default spends someone's tokens on
    a provider they did not choose."""
    db = _outline_db()
    db.report.llm_config_id = None  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        await _service(db).propose_outline(REPORT_ID, OWNER)

    assert proposal.calls == 0


async def test_a_heading_and_a_question_may_not_be_blank() -> None:
    db = FakeDb(report=_report(), sections=[_section()], connection=_connection())

    with pytest.raises(ValidationError):
        await _service(db).add_section(REPORT_ID, OWNER, heading="   ")
    with pytest.raises(ValidationError):
        await _service(db).add_block(REPORT_ID, SECTION_ID, OWNER, question=" ")


# ── redrawing a saved chart ──────────────────────────────────────────────
# The one place a report deliberately parts company with the chat redraw, which
# persists nothing: a report is a document that is kept and printed from its
# *saved* run, so a chart living only in the browser would not survive the
# export. It is written onto the run and onto the run alone — the same argument
# that put `edited_prose` there rather than on the template.
def _saved_run() -> ReportRun:
    return ReportRun(id=RUN_ID, report_id=REPORT_ID, owner_id=OWNER, status="SUCCEEDED")


def _saved_result() -> ReportBlockResult:
    return ReportBlockResult(
        id=RESULT_ID,
        run_id=RUN_ID,
        block_id=BLOCK_ID,
        section_id=SECTION_ID,
        position=0,
        heading_snapshot="روند درآمد",
        question_snapshot="revenue by month",
        sql_text="SELECT 1",
        sql_hash="abc",
        columns=[
            {"name": "month", "db_type": "date", "semantic_type": "temporal"},
            {"name": "revenue", "db_type": "numeric", "semantic_type": "quantitative"},
        ],
        rows=[["2026-01-01", 120], ["2026-02-01", 180], ["2026-03-01", 150]],
        row_count=3,
        truncated=False,
        vega_spec={"mark": "line"},
        chart_source="heuristic",
        chart_note="A pie chart does not fit this result; showing a line chart.",
        kpi=None,
        computed_at=utcnow(),
        duration_ms=9,
        status="OK",
    )


def _chart_db() -> FakeDb:
    return FakeDb(
        report=_report(),
        run=_saved_run(),
        block_results=[_saved_result()],
        connection=_connection(),
    )


async def test_a_redrawn_chart_is_written_onto_the_run() -> None:
    """Phase 10 prints the saved run, so a redraw that lived only in the
    browser would be lost on the way to the PDF."""
    db = _chart_db()

    row, options, reason = await _service(db).redraw_block_chart(
        REPORT_ID, RUN_ID, RESULT_ID, OWNER, chart_type="bar"
    )

    assert reason is None
    assert row.vega_spec is not None
    assert row.vega_spec != {"mark": "line"}
    # Who chose the picture, kept beside it.
    assert row.chart_source == "user"
    # The note explained a demotion from what was asked for. Nothing was
    # demoted here, so it goes rather than sitting under the new chart.
    assert row.chart_note is None
    assert db.flushes > 0
    assert any(option["chart_type"] == "bar" for option in options)


async def test_auto_gives_the_planner_no_suggestion() -> None:
    """`auto` is the absence of a type — which is what a re-run months from now
    on differently-shaped data does anyway."""
    db = _chart_db()

    row, _options, reason = await _service(db).redraw_block_chart(
        REPORT_ID, RUN_ID, RESULT_ID, OWNER, chart_type="auto"
    )

    assert reason is None
    assert row.chart_source == "heuristic"


async def test_a_type_this_result_cannot_carry_is_refused_with_its_reason() -> None:
    """An answer, never a 500 — and the stored chart is left exactly as it was
    rather than replaced by nothing."""
    db = _chart_db()

    row, options, reason = await _service(db).redraw_block_chart(
        REPORT_ID, RUN_ID, RESULT_ID, OWNER, chart_type="heatmap"
    )

    assert reason
    assert row.vega_spec == {"mark": "line"}
    assert row.chart_source == "heuristic"
    refused = next(o for o in options if o["chart_type"] == "heatmap")
    assert refused["supported"] is False


async def test_a_result_the_run_does_not_have_is_a_404() -> None:
    db = FakeDb(report=_report(), run=_saved_run(), block_results=[])

    with pytest.raises(NotFoundError):
        await _service(db).redraw_block_chart(
            REPORT_ID, RUN_ID, uuid4(), OWNER, chart_type="bar"
        )


async def test_a_block_that_kept_no_rows_has_nothing_to_draw() -> None:
    """A failed block is a paragraph's caveat, not a chart with no data."""
    empty = _saved_result()
    empty.rows = []
    db = FakeDb(report=_report(), run=_saved_run(), block_results=[empty])

    with pytest.raises(ValidationError):
        await _service(db).redraw_block_chart(
            REPORT_ID, RUN_ID, RESULT_ID, OWNER, chart_type="bar"
        )
