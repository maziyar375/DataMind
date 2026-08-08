"""Writing a block's SQL by hand — the other road to the same verdict.

`check_block` asks a model what the question means. This asks nothing: the
statement is the user's, and all the service does is put it in front of the
guard, run a preview, and record what came back. It is `validate_sql` — the
tile editor's hand-written path — behind a report's own route, which is what
makes a report buildable by somebody who knows their warehouse better than they
know how to phrase a question about it.

Two claims here are not obvious, and both are decisions rather than mechanics:

* **A rejected statement is kept.** `check_block` throws a rejected *draft*
  away; this keeps a rejected *statement*, because the semantic layer already
  settled the distinction — an invalid generated metric is dropped and an
  invalid human-written one is flagged and kept, since deleting a person's work
  to hide drift is worse than showing it.
* **Provenance is derived, never asserted.** A block that never held a
  generated statement becomes `HANDWRITTEN`; one that did becomes
  `GENERATED_EDITED` and stays there. The client sends no origin and could not
  gain anything by sending one: the column is provenance, and the guard reads
  the statement again at execution whatever it says.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.domain.ports.database import ResultColumn
from app.domain.value_objects import ReportFeasibility, SqlOrigin
from app.services import report_service
from app.services.query_service import TileResult
from app.services.report_service import sql_fingerprint
from app.services.sql_draft_service import SqlDraft
from app.sqlguard.validator import ValidationIssue, ValidationReport

# The fixture, its ids and its service factory come from the feasibility tests:
# the two routes answer the same question about the same block, and building a
# second report out of the same rows is how the two quietly stop agreeing.
from tests.integration.test_report_feasibility import (  # noqa: E402
    BLOCK_ID,
    OWNER,
    REPORT_ID,
    _db,
    _service,
)

HAND_SQL = "SELECT region, SUM(total_amount) AS revenue FROM public.orders GROUP BY 1"


def _preview(rows: int = 2) -> TileResult:
    return TileResult(
        status="OK",
        columns=[
            ResultColumn(name="region", db_type="text"),
            ResultColumn(name="revenue", db_type="numeric"),
        ],
        rows=[["north", 120] for _ in range(rows)],
        row_count=rows,
        duration_ms=4,
    )


def _draft(
    *,
    sql: str = HAND_SQL,
    status: str = "VALID",
    preview: TileResult | None = None,
    errors: list[dict] | None = None,
) -> SqlDraft:
    return SqlDraft(
        sql=sql,
        validation_status=status,
        # A real report, for the reason `test_report_feasibility._draft` gives:
        # a hand-shaped payload is how the service came to read a key that was
        # never in one.
        validation_report=ValidationReport(
            status=status,  # type: ignore[arg-type]
            issues=[ValidationIssue(**issue) for issue in (errors or [])],
        ).model_dump(mode="json"),
        referenced_tables=["public.orders"],
        chart_suggestion={"chart_type": "bar"},
        chart_options=[{"chart_type": "bar", "supported": True}],
        preview=preview,
    )


class _Validate:
    """Stands in for `sql_draft_service.validate_sql`, recording its arguments."""

    def __init__(self, result: SqlDraft) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}
        self.calls = 0

    async def __call__(self, _db: Any, _settings: Any, **kwargs: Any) -> SqlDraft:
        self.calls += 1
        self.kwargs = kwargs
        return self.result


@pytest.fixture
def validating(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(result: SqlDraft) -> _Validate:
        fake = _Validate(result)
        monkeypatch.setattr(report_service, "validate_sql", fake)
        return fake

    monkeypatch.setattr(report_service, "AesGcmSecretBox", lambda *a, **k: object())
    # Loud rather than silent: this path must never reach a model, and a test
    # that quietly did would look like it passed.
    async def _no_model(*_args: Any, **_kwargs: Any) -> SqlDraft:
        raise AssertionError("writing SQL by hand must not call a model")

    monkeypatch.setattr(report_service, "draft_sql", _no_model)
    return _install


# ── the verdict ──────────────────────────────────────────────────────────
async def test_a_valid_statement_with_rows_is_feasible(validating: Any) -> None:
    fake = validating(_draft(preview=_preview()))
    db = _db()

    block, draft = await _service(db).edit_block_sql(
        REPORT_ID, BLOCK_ID, OWNER, sql=HAND_SQL
    )

    assert fake.calls == 1
    assert fake.kwargs["sql"] == HAND_SQL
    assert block.feasibility_status == ReportFeasibility.FEASIBLE
    assert block.sql == HAND_SQL
    assert block.sql_hash == sql_fingerprint(HAND_SQL)
    assert draft.chart_options == [{"chart_type": "bar", "supported": True}]


async def test_a_valid_statement_with_no_rows_is_empty_not_infeasible(
    validating: Any,
) -> None:
    validating(_draft(preview=_preview(rows=0)))

    block, _ = await _service(_db()).edit_block_sql(
        REPORT_ID, BLOCK_ID, OWNER, sql=HAND_SQL
    )

    assert block.feasibility_status == ReportFeasibility.EMPTY
    assert block.sql == HAND_SQL


async def test_a_rejected_statement_is_kept_with_the_guards_own_reason(
    validating: Any,
) -> None:
    """The semantic layer's rule, applied here: a generated draft the guard
    refuses is dropped, a person's is flagged and kept. Nothing runs on it —
    the verdict says why — but nobody's typing disappears to hide a mistake."""
    validating(
        _draft(
            status="REJECTED",
            errors=[
                {
                    "rule_id": "E_UNKNOWN_TABLE",
                    "message": "Unknown table 'public.profits'.",
                    "hint": "Sync the schema, or use a table that exists.",
                }
            ],
        )
    )

    block, _ = await _service(_db()).edit_block_sql(
        REPORT_ID, BLOCK_ID, OWNER, sql="SELECT * FROM public.profits"
    )

    assert block.feasibility_status == ReportFeasibility.INFEASIBLE
    assert block.feasibility_reason == (
        "Unknown table 'public.profits'. Sync the schema, or use a table that exists."
    )
    assert block.sql == "SELECT * FROM public.profits"


# ── provenance ───────────────────────────────────────────────────────────
async def test_a_block_that_never_held_generated_sql_becomes_handwritten(
    validating: Any,
) -> None:
    validating(_draft(preview=_preview()))
    db = _db()  # its block starts with no SQL at all

    block, _ = await _service(db).edit_block_sql(
        REPORT_ID, BLOCK_ID, OWNER, sql=HAND_SQL
    )

    assert block.sql_origin == SqlOrigin.HANDWRITTEN


async def test_editing_a_generated_statement_records_that_it_started_as_one(
    validating: Any,
) -> None:
    validating(_draft(preview=_preview()))
    db = _db()
    db.blocks[0].sql = "SELECT 1"
    db.blocks[0].sql_origin = SqlOrigin.GENERATED

    block, _ = await _service(db).edit_block_sql(
        REPORT_ID, BLOCK_ID, OWNER, sql=HAND_SQL
    )

    assert block.sql_origin == SqlOrigin.GENERATED_EDITED


@pytest.mark.parametrize(
    "origin", [SqlOrigin.HANDWRITTEN, SqlOrigin.GENERATED_EDITED]
)
async def test_provenance_does_not_drift_on_a_second_edit(
    validating: Any, origin: str
) -> None:
    """"I started from what the model wrote" is a fact about the past, and it
    stays true however little of the original survives."""
    validating(_draft(preview=_preview()))
    db = _db()
    db.blocks[0].sql = "SELECT 1"
    db.blocks[0].sql_origin = origin

    block, _ = await _service(db).edit_block_sql(
        REPORT_ID, BLOCK_ID, OWNER, sql=HAND_SQL
    )

    assert block.sql_origin == origin


# ── refusals ─────────────────────────────────────────────────────────────
async def test_whitespace_is_not_a_statement(validating: Any) -> None:
    fake = validating(_draft(preview=_preview()))

    with pytest.raises(ValidationError):
        await _service(_db()).edit_block_sql(REPORT_ID, BLOCK_ID, OWNER, sql="   \n ")

    assert fake.calls == 0


async def test_a_removed_connection_refuses_before_the_guard(validating: Any) -> None:
    """A report whose connection is gone stays readable and stops being
    editable. There is no schema left to validate against."""
    fake = validating(_draft(preview=_preview()))
    db = _db()
    db.report.connection_id = None

    with pytest.raises(ValidationError):
        await _service(db).edit_block_sql(REPORT_ID, BLOCK_ID, OWNER, sql=HAND_SQL)

    assert fake.calls == 0


async def test_a_block_from_another_users_report_is_a_404(validating: Any) -> None:
    fake = validating(_draft(preview=_preview()))
    db = _db()
    db.report = None

    with pytest.raises(NotFoundError):
        await _service(db).edit_block_sql(REPORT_ID, BLOCK_ID, uuid4(), sql=HAND_SQL)

    assert fake.calls == 0
