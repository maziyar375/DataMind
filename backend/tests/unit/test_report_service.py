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
* **Editing a question throws its SQL away.** The stored statement answers the
  question that was checked. Keeping it means a run producing the right numbers
  under the wrong heading — the failure this whole reset exists to prevent.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import (
    ConflictError,
    DisclosureTooNarrowError,
    NotFoundError,
    ValidationError,
)
from app.domain.value_objects import DisclosurePolicy, ReportFeasibility
from app.infra.db.models import DatabaseConnection, Report, ReportBlock, ReportSection
from app.services.report_service import ReportService, assert_wide_enough, is_wide_enough

OWNER = uuid4()
OTHER = uuid4()
REPORT_ID = uuid4()
SECTION_ID = uuid4()
BLOCK_ID = uuid4()
CONNECTION_ID = uuid4()


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
    ) -> None:
        self.report = report
        self.sections = sections or []
        self.blocks = blocks or []
        self.connection = connection
        self.duplicate_name = duplicate_name
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.refreshed: list[Any] = []
        self.flushes = 0
        self._name_lookups = 0

    async def execute(self, statement: Any) -> FakeResult:
        sql = str(statement).lower()
        if "max(" in sql:
            rows = self.blocks if "report_blocks" in sql else self.sections
            return FakeResult([max((r.position for r in rows), default=0)])
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
            return FakeResult([])
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


def _service(db: FakeDb) -> ReportService:
    return ReportService(db, object())  # type: ignore[arg-type]


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


async def test_a_heading_and_a_question_may_not_be_blank() -> None:
    db = FakeDb(report=_report(), sections=[_section()], connection=_connection())

    with pytest.raises(ValidationError):
        await _service(db).add_section(REPORT_ID, OWNER, heading="   ")
    with pytest.raises(ValidationError):
        await _service(db).add_block(REPORT_ID, SECTION_ID, OWNER, question=" ")
