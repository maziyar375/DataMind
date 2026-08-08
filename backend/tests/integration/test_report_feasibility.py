"""*Can this block be produced, and if not, why* — answered mechanically.

Three outcomes, and the whole point is that all three are **stored verdicts**
rather than exceptions. A question the guard refuses is not a failed request:
it is a block that says INFEASIBLE, in the guard's own words, with a Generate
button that stays disabled until the user rewords it.

The fourth claim here is the one that is easy to lose and expensive to lose:
the time rules that make a saved report re-runnable actually reach the model,
in the dialect the connection speaks, and reach the *repair* prompt too.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.errors import LLMError, NotFoundError, ValidationError
from app.domain.ports.database import ResultColumn
from app.domain.value_objects import ReportFeasibility, SqlOrigin
from app.infra.db.models import (
    DatabaseConnection,
    LlmConfig,
    Report,
    ReportBlock,
    ReportSection,
)
from app.reports.prompts import DIALECT_DATE_ARITHMETIC, report_time_rules
from app.services import report_service
from app.services.query_service import TileResult
from app.services.report_service import ReportService, sql_fingerprint
from app.services.sql_draft_service import SqlDraft
from app.sqlguard import GuardPolicy, guard
from app.sqlguard.validator import ValidationIssue, ValidationReport
from tests.unit.test_report_service import FakeDb, FakeSettings

OWNER = uuid4()
REPORT_ID = uuid4()
SECTION_ID = uuid4()
BLOCK_ID = uuid4()
CONNECTION_ID = uuid4()
LLM_ID = uuid4()

VALID_SQL = (
    "SELECT date_trunc('month', order_date) AS month, SUM(total_amount) AS revenue "
    "FROM public.orders "
    "WHERE order_date >= CURRENT_DATE - INTERVAL '3 months' GROUP BY 1"
)


def _rows(count: int) -> TileResult:
    return TileResult(
        status="OK",
        columns=[
            ResultColumn(name="month", db_type="date"),
            ResultColumn(name="revenue", db_type="numeric"),
        ],
        rows=[["2026-05-01", 120] for _ in range(count)],
        row_count=count,
        duration_ms=7,
    )


def _draft(
    *,
    status: str = "VALID",
    preview: TileResult | None = None,
    errors: list[dict] | None = None,
) -> SqlDraft:
    return SqlDraft(
        sql=VALID_SQL,
        validation_status=status,
        # Built from a real `ValidationReport` rather than hand-shaped, and
        # that is not fussiness. A hand-written `{"errors": [...]}` passed for
        # a long time while the service read a key the serialised report has
        # never had — `errors` is a *property* filtering `issues`, and
        # `model_dump` emits declared fields only — so every rejection reached
        # the user as the generic fallback while this file asserted the guard's
        # own words. A fake that cannot drift from the payload is the fix.
        validation_report=ValidationReport(
            status=status,  # type: ignore[arg-type]
            issues=[ValidationIssue(**issue) for issue in (errors or [])],
        ).model_dump(mode="json"),
        referenced_tables=["public.orders"],
        chart_suggestion={"chart_type": "line"},
        chart_options=[{"chart_type": "line", "allowed": True}],
        preview=preview,
    )


def _db(*, database_type: str = "postgres") -> FakeDb:
    report = Report(
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
    block = ReportBlock(
        id=BLOCK_ID,
        section_id=SECTION_ID,
        position=1,
        question="revenue by month",
        sql="",
        sql_hash="",
        sql_origin="GENERATED",
        block_type="CHART",
        time_window="last_3_months",
        feasibility_status=ReportFeasibility.UNCHECKED,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    section = ReportSection(
        id=SECTION_ID,
        report_id=REPORT_ID,
        position=1,
        heading="روند درآمد",
        intent="",
        kind="NORMAL",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    connection = DatabaseConnection(
        id=CONNECTION_ID,
        owner_id=OWNER,
        name="sales",
        database_type=database_type,
        host="db",
        port=5432,
        database_name="sales",
        username="ro",
        encrypted_password="x",
        max_rows=1000,
        disclosure_policy="SAMPLE",
    )
    return FakeDb(
        report=report,
        sections=[section],
        blocks=[block],
        connection=connection,
        llm_config=LlmConfig(
            id=LLM_ID, owner_id=OWNER, name="deepseek", provider="openai", model="m"
        ),
        snapshot_tables=[
            {
                "schema": "public",
                "name": "orders",
                "columns": [{"name": "order_date", "data_type": "date"}],
            }
        ],
    )


class _Draft:
    """Stands in for `sql_draft_service.draft_sql`, recording its arguments."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    async def __call__(self, _db: Any, _settings: Any, **kwargs: Any) -> SqlDraft:
        self.calls += 1
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def drafting(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Everything but the model call, which each test decides the outcome of."""

    def _install(result: Any) -> _Draft:
        fake = _Draft(result)
        monkeypatch.setattr(report_service, "draft_sql", fake)
        return fake

    monkeypatch.setattr(report_service, "AesGcmSecretBox", lambda *a, **k: object())
    return _install


def _service(db: FakeDb) -> ReportService:
    return ReportService(db, FakeSettings())  # type: ignore[arg-type]


# ── the three outcomes ───────────────────────────────────────────────────
async def test_valid_sql_with_rows_is_feasible(drafting: Any) -> None:
    drafting(_draft(preview=_rows(3)))
    db = _db()

    block, draft = await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert block.feasibility_status == ReportFeasibility.FEASIBLE
    assert block.feasibility_reason is None
    assert block.feasibility_checked_at is not None
    assert block.sql == VALID_SQL
    assert block.sql_hash == sql_fingerprint(VALID_SQL)
    assert block.sql_origin == SqlOrigin.GENERATED
    # The suggestion travels in the response and is never stored: NULL
    # chart_config means Auto, so a re-run on differently-shaped data is free
    # to re-decide.
    assert draft is not None and draft.chart_suggestion == {"chart_type": "line"}
    assert block.chart_config is None


async def test_valid_sql_with_no_rows_is_empty_not_infeasible(drafting: Any) -> None:
    """The query works and the answer is "nothing happened", which a report may
    legitimately want to say. Calling that infeasible would block a run over a
    quarter with no returns in it."""
    drafting(_draft(preview=_rows(0)))
    db = _db()

    block, _ = await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert block.feasibility_status == ReportFeasibility.EMPTY
    assert "no rows" in (block.feasibility_reason or "")
    # Still a usable statement: the run executes it and the section says so.
    assert block.sql == VALID_SQL
    assert block.sql_hash


async def test_a_rejected_statement_is_infeasible_in_the_guards_own_words(
    drafting: Any,
) -> None:
    """Verbatim, message then hint. A re-worded explanation is a second
    vocabulary for the user to learn, and it drifts from the rule that produced
    it."""
    drafting(
        _draft(
            status="REJECTED",
            errors=[
                {
                    "rule_id": "E_UNKNOWN_COLUMN",
                    "message": "Unknown column 'profit' on public.orders.",
                    "hint": "Sync the schema, or use a column that exists.",
                }
            ],
        )
    )
    db = _db()

    block, _ = await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert block.feasibility_status == ReportFeasibility.INFEASIBLE
    assert block.feasibility_reason == (
        "Unknown column 'profit' on public.orders. "
        "Sync the schema, or use a column that exists."
    )
    # Nothing stored: a rejected statement must never become a block's SQL.
    assert block.sql == ""
    assert block.sql_hash == ""


async def test_a_model_that_produces_no_sql_is_a_verdict_not_a_502(
    drafting: Any,
) -> None:
    """"The model could not produce a query" *is* the answer to the question
    this route asks, and the user's next move is to reword the block."""
    fake = drafting(LLMError("The model could not produce a query."))
    db = _db()

    block, draft = await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert fake.calls == 1
    assert block.feasibility_status == ReportFeasibility.INFEASIBLE
    assert "could not produce" in (block.feasibility_reason or "")
    assert draft is None


async def test_valid_sql_the_database_refuses_is_infeasible(drafting: Any) -> None:
    """A timeout or a dropped view: not the guard's doing, not fixable by
    rewording, but still "this cannot be produced"."""
    drafting(
        _draft(
            preview=TileResult(
                status="ERROR",
                error_code="E_QUERY_FAILED",
                error_message="canceling statement due to statement timeout",
            )
        )
    )
    db = _db()

    block, _ = await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert block.feasibility_status == ReportFeasibility.INFEASIBLE
    assert "timeout" in (block.feasibility_reason or "")


# ── the time rules ───────────────────────────────────────────────────────
async def test_the_check_sends_the_time_rules_for_this_window(
    drafting: Any,
) -> None:
    fake = drafting(_draft(preview=_rows(2)))
    db = _db()

    await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    rules = fake.kwargs["extra_rules"]
    assert "the last 3 months" in rules
    assert "Never write a literal date" in rules
    assert DIALECT_DATE_ARITHMETIC["postgres"] in rules
    # The block's own question, unchanged: the rules are an addendum, not a
    # rewrite of what the user asked.
    assert fake.kwargs["question"] == "revenue by month"


@pytest.mark.parametrize("database_type", sorted(DIALECT_DATE_ARITHMETIC))
async def test_the_rules_speak_the_connections_dialect(
    drafting: Any, database_type: str
) -> None:
    """Postgres INTERVAL, MySQL DATE_SUB, T-SQL DATEADD, Oracle TRUNC/INTERVAL.
    A block generated with the wrong dialect's example is a rejection and a
    wasted repair."""
    fake = drafting(_draft(preview=_rows(1)))
    db = _db(database_type=database_type)

    await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert DIALECT_DATE_ARITHMETIC[database_type] in fake.kwargs["extra_rules"]


@pytest.mark.parametrize("database_type", sorted(DIALECT_DATE_ARITHMETIC))
def test_every_dialects_example_survives_the_guard(database_type: str) -> None:
    """The example is what the model copies, so an example the guard rejects is
    a feature that never works on that engine. Oracle is why this test exists:
    `ADD_MONTHS` — the obvious spelling, and the one the design doc named —
    parses to a node the allowlist does not carry."""
    dialects = {"mssql": "tsql"}
    statement = (
        "SELECT SUM(total_amount) FROM public.orders WHERE "
        + DIALECT_DATE_ARITHMETIC[database_type]
    )
    policy = GuardPolicy(
        dialect=dialects.get(database_type, database_type),
        allowed_tables={"public.orders"},
        allowed_columns={"public.orders": {"total_amount", "order_date"}},
    )

    report, _ = guard(statement, policy)

    assert report.status == "VALID", (
        f"{database_type}: {report.errors[0].message if report.errors else ''}"
    )


def test_the_rules_carry_the_connections_own_time_conventions() -> None:
    """Fiscal year start and calendar-vs-rolling are facts about the database,
    recorded per connection in the semantic layer — the only correct place for
    a deployment whose fiscal year starts in Farvardin."""
    rules = report_time_rules(
        database_type="postgres",
        time_window="ytd",
        conventions="Time conventions: the fiscal year starts in March.",
    )

    assert "this year, to date" in rules
    assert "the fiscal year starts in March" in rules


def test_a_block_with_no_window_still_forbids_a_literal_date() -> None:
    """`none` means the block named no window, not that it may hard-code one:
    the question itself often carries the period."""
    rules = report_time_rules(database_type="postgres", time_window="none")

    assert "Never write a literal date" in rules
    assert "only when the question itself names a period" in rules


# ── what is refused before a token is spent ──────────────────────────────
async def test_an_unsynced_connection_is_refused_before_the_model_call(
    drafting: Any,
) -> None:
    """`draft_sql` refuses an empty snapshot itself — the model would otherwise
    be handed a schema block with no tables and asked to write SQL against it,
    spending a call to produce something the guard is guaranteed to reject."""
    fake = drafting(
        ValidationError("Sync this connection's schema before drafting SQL against it.")
    )
    db = _db()
    db.snapshot_tables = []

    with pytest.raises(ValidationError):
        await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    # It reached `draft_sql`, which refuses before spending the call — and the
    # block is left UNCHECKED rather than marked infeasible for a reason that
    # has nothing to do with the question.
    assert fake.calls == 1
    assert db.blocks[0].feasibility_status == ReportFeasibility.UNCHECKED


async def test_a_report_with_no_model_cannot_check_anything(drafting: Any) -> None:
    fake = drafting(_draft(preview=_rows(1)))
    db = _db()
    db.report.llm_config_id = None  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert fake.calls == 0


async def test_a_report_whose_connection_was_removed_cannot_check(
    drafting: Any,
) -> None:
    """SET NULL keeps the document readable; it cannot make it re-runnable."""
    fake = drafting(_draft(preview=_rows(1)))
    db = _db()
    db.report.connection_id = None  # type: ignore[union-attr]

    with pytest.raises(ValidationError):
        await _service(db).check_block(REPORT_ID, BLOCK_ID, OWNER)

    assert fake.calls == 0


async def test_a_block_from_another_users_report_is_a_404(drafting: Any) -> None:
    """The block is reached only through its report, so a block id borrowed
    from someone else's matches no row — and 404, not 403, keeps it
    indistinguishable from one that never existed."""
    fake = drafting(_draft(preview=_rows(1)))
    db = _db()
    db.report = None

    with pytest.raises(NotFoundError):
        await _service(db).check_block(REPORT_ID, BLOCK_ID, uuid4())

    assert fake.calls == 0


def test_the_fingerprint_ignores_only_whitespace() -> None:
    """It is what makes run-to-run comparison honest: two runs whose SQL
    differs must not compare equal, and a reformatted statement is not a
    different statement."""
    assert sql_fingerprint("SELECT  1\n  FROM t") == sql_fingerprint("SELECT 1 FROM t")
    assert sql_fingerprint("SELECT 1 FROM t") != sql_fingerprint("SELECT 2 FROM t")
