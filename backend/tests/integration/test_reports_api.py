"""The report routes, end to end through the app.

Four claims are worth more than the rest:

* **Every route is owner-scoped.** Not "most" — the sweep at the bottom walks
  the whole route table and fails on any route that reaches the service without
  the caller's own id, so a route added in Phase 3 or 5 is covered the day it is
  added.
* **The disclosure refusal is a code, not a sentence.** The connection picker
  greys out what will not work; it can only do that if the failure arrives as
  `E_DISCLOSURE_TOO_NARROW` rather than as English prose to match on.
* **A different `connection_id` in a PATCH is 422**, mirroring
  `_bind_connection`.
* **Every write returns the written row**, resolved — chips and all — so the
  page splices it into state instead of re-reading it.

Cascade behaviour is not faked here: it is a property of the DDL, and
`tests/unit/test_report_models.py` pins every `ondelete` column by column
against the migration.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import reports
from app.core.clock import utcnow
from app.core.context import RequestContext
from app.core.errors import (
    ConflictError,
    DisclosureTooNarrowError,
    LLMError,
    NotFoundError,
    ValidationError,
)
from app.domain.ports.database import ResultColumn
from app.infra.db.models import Report, ReportBlock, ReportSection
from app.main import create_app
from app.services.query_service import TileResult
from app.services.sql_draft_service import SqlDraft

USER = uuid4()
REPORT_ID = uuid4()
SECTION_ID = uuid4()
BLOCK_ID = uuid4()
CONNECTION_ID = uuid4()
LLM_ID = uuid4()


def _report(owner_id: UUID = USER) -> Report:
    return Report(
        id=REPORT_ID,
        owner_id=owner_id,
        name="Quarterly sales",
        description=None,
        prompt="یک گزارش تحلیلی از عملکرد فروش سه ماه گذشته می‌خواهم",
        connection_id=CONNECTION_ID,
        llm_config_id=LLM_ID,
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
        heading="روند درآمد",
        intent="how revenue moved over the window",
        kind="NORMAL",
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def _block() -> ReportBlock:
    return ReportBlock(
        id=BLOCK_ID,
        section_id=SECTION_ID,
        position=1,
        question="revenue by month",
        sql="SELECT month, revenue FROM public.sales",
        sql_hash="abc123",
        sql_origin="GENERATED",
        block_type="CHART",
        chart_config=None,
        time_window="last_3_months",
        feasibility_status="FEASIBLE",
        feasibility_reason=None,
        feasibility_checked_at=utcnow(),
        max_rows=None,
        created_at=utcnow(),
        updated_at=utcnow(),
    )


class NoLazyLoads:
    """A report row whose relationship raises if a response reads it.

    `Report.sections` is lazy. Reading it while serialising is a lazy load in a
    context that cannot await — `MissingGreenlet`, a 500, and only ever in the
    running app: a detached ORM object in a test happily returns an empty list
    instead. So the fake makes the mistake loud.
    """

    def __init__(self, report: Report) -> None:
        self._report = report

    def __getattr__(self, name: str) -> Any:
        if name == "sections":
            raise RuntimeError("MissingGreenlet: the response read a lazy relationship")
        return getattr(self._report, name)


class FakeService:
    """Stands in for `ReportService`, recording every owner_id it is given.

    The router builds the service itself, so it is the *class* that is replaced.
    Each instance shares one call log through the class attribute.
    """

    calls: list[tuple[str, dict[str, Any]]] = []
    raises: Exception | None = None
    sections: list[ReportSection] = []
    blocks: list[ReportBlock] = []
    draft: Any = None

    def __init__(self, _db: Any, _settings: Any) -> None:
        pass

    def _record(self, method: str, **kwargs: Any) -> None:
        FakeService.calls.append((method, kwargs))
        if FakeService.raises is not None:
            raise FakeService.raises

    async def list(self, owner_id: UUID) -> list[Report]:
        self._record("list", owner_id=owner_id)
        return [_report()]

    async def get(self, report_id: UUID, owner_id: UUID) -> Any:
        self._record("get", report_id=report_id, owner_id=owner_id)
        return NoLazyLoads(_report())

    async def sections_of(self, report_id: UUID) -> list[ReportSection]:
        return list(FakeService.sections)

    async def blocks_of(self, section_ids: list[UUID]) -> list[ReportBlock]:
        return list(FakeService.blocks)

    async def display_names(self, reports: list[Any]) -> tuple[dict, dict]:
        return {CONNECTION_ID: "sales"}, {LLM_ID: "deepseek"}

    async def create(self, owner_id: UUID, **fields: Any) -> Any:
        self._record("create", owner_id=owner_id, **fields)
        return NoLazyLoads(_report())

    async def update(self, report_id: UUID, owner_id: UUID, **changes: Any) -> Any:
        self._record("update", report_id=report_id, owner_id=owner_id, **changes)
        return NoLazyLoads(_report())

    async def delete(self, report_id: UUID, owner_id: UUID) -> None:
        self._record("delete", report_id=report_id, owner_id=owner_id)

    async def propose_outline(self, report_id: UUID, owner_id: UUID) -> Any:
        self._record("propose_outline", report_id=report_id, owner_id=owner_id)
        return NoLazyLoads(_report())

    async def add_section(
        self, report_id: UUID, owner_id: UUID, **fields: Any
    ) -> ReportSection:
        self._record("add_section", report_id=report_id, owner_id=owner_id, **fields)
        return _section()

    async def update_section(
        self, report_id: UUID, section_id: UUID, owner_id: UUID, **changes: Any
    ) -> ReportSection:
        self._record(
            "update_section",
            report_id=report_id,
            section_id=section_id,
            owner_id=owner_id,
            **changes,
        )
        return _section()

    async def delete_section(
        self, report_id: UUID, section_id: UUID, owner_id: UUID
    ) -> None:
        self._record(
            "delete_section",
            report_id=report_id,
            section_id=section_id,
            owner_id=owner_id,
        )

    async def add_block(
        self, report_id: UUID, section_id: UUID, owner_id: UUID, **fields: Any
    ) -> ReportBlock:
        self._record(
            "add_block",
            report_id=report_id,
            section_id=section_id,
            owner_id=owner_id,
            **fields,
        )
        return _block()

    async def update_block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID, **changes: Any
    ) -> ReportBlock:
        self._record(
            "update_block",
            report_id=report_id,
            block_id=block_id,
            owner_id=owner_id,
            **changes,
        )
        return _block()

    async def delete_block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID
    ) -> None:
        self._record(
            "delete_block", report_id=report_id, block_id=block_id, owner_id=owner_id
        )

    async def check_block(
        self, report_id: UUID, block_id: UUID, owner_id: UUID
    ) -> tuple[ReportBlock, Any]:
        self._record(
            "check_block", report_id=report_id, block_id=block_id, owner_id=owner_id
        )
        return _block(), FakeService.draft


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    FakeService.calls = []
    FakeService.raises = None
    FakeService.sections = [_section()]
    FakeService.blocks = [_block()]
    FakeService.draft = SqlDraft(
        sql="SELECT 1",
        validation_status="VALID",
        validation_report={"status": "VALID"},
        referenced_tables=["public.orders"],
        chart_suggestion={"chart_type": "line"},
        chart_options=[{"chart_type": "line", "allowed": True}],
        preview=TileResult(
            status="OK",
            columns=[ResultColumn(name="month", db_type="date")],
            rows=[["2026-05-01"]],
            row_count=1,
        ),
    )
    monkeypatch.setattr(reports, "ReportService", FakeService)

    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: None
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER, email="user@test.local", role="MEMBER", correlation_id="test"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── reading ──────────────────────────────────────────────────────────────
def test_the_index_carries_the_chips_and_the_outline_size(client: Any) -> None:
    response = client.get("/api/v1/reports")

    assert response.status_code == 200
    card = response.json()[0]
    assert card["name"] == "Quarterly sales"
    assert card["connection_name"] == "sales"
    assert card["llm_config_name"] == "deepseek"
    assert card["language"] == "fa"
    assert card["section_count"] == 1


def test_reading_a_report_returns_its_outline_nested(client: Any) -> None:
    """The outline is the document's structure, so it arrives with it — one
    request, not one per section."""
    response = client.get(f"/api/v1/reports/{REPORT_ID}")

    assert response.status_code == 200
    body = response.json()
    section = body["sections"][0]
    assert section["heading"] == "روند درآمد"
    block = section["blocks"][0]
    assert block["question"] == "revenue by month"
    assert block["feasibility_status"] == "FEASIBLE"
    # NULL chart_config means Auto, and must survive the round trip as null.
    assert block["chart_config"] is None


def test_a_report_carries_names_never_connection_internals(client: Any) -> None:
    body = client.get(f"/api/v1/reports/{REPORT_ID}").json()

    assert body["connection_name"] == "sales"
    for forbidden in ("host", "username", "encrypted_password", "port"):
        assert forbidden not in body


# ── writing ──────────────────────────────────────────────────────────────
def test_creating_a_report_returns_201_and_the_written_row(client: Any) -> None:
    response = client.post(
        "/api/v1/reports",
        json={
            "name": "Quarterly sales",
            "prompt": "سه ماه گذشته",
            "connection_id": str(CONNECTION_ID),
            "language": "fa",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Quarterly sales"
    # Resolved, so the page can splice it into state instead of re-reading.
    assert body["connection_name"] == "sales"


def test_a_report_needs_a_connection_to_be_created_at_all(client: Any) -> None:
    """Pinned forever means chosen up front: there is no report without one."""
    response = client.post("/api/v1/reports", json={"name": "Nameless"})

    assert response.status_code == 422
    assert response.json()["code"] == "E_VALIDATION"


def test_a_narrow_connection_is_refused_with_a_code_the_ui_can_branch_on(
    client: Any,
) -> None:
    """The picker greys out ineligible connections and shows the reason; it can
    only do that from a code, never from an English string."""
    FakeService.raises = DisclosureTooNarrowError(
        "This connection's disclosure policy is AGGREGATE. A report's analysis "
        "is written from result values, so it needs SAMPLE or FULL."
    )

    response = client.post(
        "/api/v1/reports",
        json={"name": "Q3", "connection_id": str(CONNECTION_ID)},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "E_DISCLOSURE_TOO_NARROW"
    assert "SAMPLE or FULL" in body["detail"]


def test_moving_a_report_to_another_connection_is_a_422(client: Any) -> None:
    """Mirrors `_bind_connection`: a report keyed to one connection cannot
    cross disclosure policies."""
    FakeService.raises = ValidationError(
        "A report is pinned to the connection it was created against."
    )

    response = client.patch(
        f"/api/v1/reports/{REPORT_ID}", json={"connection_id": str(uuid4())}
    )

    assert response.status_code == 422
    assert "pinned" in response.json()["detail"]


def test_the_connection_id_reaches_the_service_so_it_can_refuse_it(
    client: Any,
) -> None:
    """The field is accepted *in order to be refused*. Dropping it at the DTO
    would look like a save that worked."""
    other = uuid4()

    client.patch(f"/api/v1/reports/{REPORT_ID}", json={"connection_id": str(other)})

    _method, kwargs = next(c for c in FakeService.calls if c[0] == "update")
    assert kwargs["connection_id"] == other


def test_a_patch_sends_only_the_fields_the_client_set(client: Any) -> None:
    """`exclude_unset`: omitting a field must leave it alone, not overwrite it
    with a default — and must not look like an attempt to move the connection."""
    client.patch(f"/api/v1/reports/{REPORT_ID}", json={"name": "Renamed"})

    _method, kwargs = next(c for c in FakeService.calls if c[0] == "update")
    assert kwargs["name"] == "Renamed"
    assert "connection_id" not in kwargs and "language" not in kwargs


def test_the_language_is_pinned_and_cannot_be_patched(client: Any) -> None:
    """Pinned at creation: every run's prose, and every past run's, is in it."""
    response = client.patch(f"/api/v1/reports/{REPORT_ID}", json={"language": "en"})

    assert response.status_code == 200
    _method, kwargs = next(c for c in FakeService.calls if c[0] == "update")
    assert "language" not in kwargs


def test_a_duplicate_name_is_a_409(client: Any) -> None:
    FakeService.raises = ConflictError("You already have a report with that name.")

    response = client.post(
        "/api/v1/reports",
        json={"name": "Quarterly sales", "connection_id": str(CONNECTION_ID)},
    )

    assert response.status_code == 409


def test_adding_a_section_returns_201_and_the_section(client: Any) -> None:
    response = client.post(
        f"/api/v1/reports/{REPORT_ID}/sections",
        json={"heading": "روند درآمد", "intent": "how revenue moved"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["heading"] == "روند درآمد"
    assert body["blocks"] == []


def test_adding_a_block_carries_no_sql(client: Any) -> None:
    """v1 edits the question, not the statement. A client that could send SQL
    here would be writing a statement no guard check ever saw."""
    response = client.post(
        f"/api/v1/reports/{REPORT_ID}/sections/{SECTION_ID}/blocks",
        json={
            "question": "revenue by month",
            "block_type": "CHART",
            "time_window": "last_3_months",
            "sql": "DROP TABLE orders",
        },
    )

    assert response.status_code == 201
    _method, kwargs = next(c for c in FakeService.calls if c[0] == "add_block")
    assert "sql" not in kwargs
    assert kwargs["question"] == "revenue by month"


def test_an_unknown_time_window_is_refused_by_the_dto(client: Any) -> None:
    response = client.post(
        f"/api/v1/reports/{REPORT_ID}/sections/{SECTION_ID}/blocks",
        json={"question": "revenue", "time_window": "whenever"},
    )

    assert response.status_code == 422


def test_editing_a_block_returns_the_written_row(client: Any) -> None:
    response = client.patch(
        f"/api/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}",
        json={"question": "revenue by week"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(BLOCK_ID)


def test_proposing_an_outline_returns_the_whole_report(client: Any) -> None:
    """The proposal *is* the document's structure, so the editor renders it
    from this response rather than re-reading the report."""
    response = client.post(f"/api/v1/reports/{REPORT_ID}/outline")

    assert response.status_code == 200
    body = response.json()
    assert body["sections"][0]["heading"] == "روند درآمد"
    assert [c[0] for c in FakeService.calls] == ["propose_outline"]


def test_a_report_with_no_model_cannot_propose_an_outline(client: Any) -> None:
    """No silent default: chat refuses the same way rather than picking one."""
    FakeService.raises = ValidationError(
        "Choose a model for this report before proposing an outline."
    )

    response = client.post(f"/api/v1/reports/{REPORT_ID}/outline")

    assert response.status_code == 422
    assert "model" in response.json()["detail"]


def test_a_model_that_returns_nothing_usable_is_reported_as_such(
    client: Any,
) -> None:
    FakeService.raises = LLMError(
        "The model did not return an outline that could be read."
    )

    response = client.post(f"/api/v1/reports/{REPORT_ID}/outline")

    assert response.status_code == 502
    assert response.json()["code"] == "E_LLM"


def test_checking_a_block_answers_with_a_verdict_and_a_preview(
    client: Any,
) -> None:
    """The block as stored, plus what the verdict was reached from — so the
    editor can show the chip, the reason and the shape in one round trip."""
    response = client.post(f"/api/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}/check")

    assert response.status_code == 200
    body = response.json()
    assert body["block"]["feasibility_status"] == "FEASIBLE"
    assert body["preview"]["row_count"] == 1
    assert body["chart_suggestion"] == {"chart_type": "line"}
    assert body["chart_options"][0]["chart_type"] == "line"
    # NULL means Auto, and the check does not take that decision away.
    assert body["block"]["chart_config"] is None


def test_a_check_that_produced_no_draft_still_answers(client: Any) -> None:
    """The model failing to write anything is a verdict, not a 502: the block
    says INFEASIBLE and the reason travels with it."""
    FakeService.draft = None

    response = client.post(f"/api/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}/check")

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] is None
    assert body["chart_options"] == []


def test_deleting_returns_204(client: Any) -> None:
    assert client.delete(f"/api/v1/reports/{REPORT_ID}").status_code == 204
    assert (
        client.delete(f"/api/v1/reports/{REPORT_ID}/sections/{SECTION_ID}").status_code
        == 204
    )
    assert (
        client.delete(f"/api/v1/reports/{REPORT_ID}/blocks/{BLOCK_ID}").status_code
        == 204
    )


# ── scoping ──────────────────────────────────────────────────────────────
def test_another_users_report_is_a_404(client: Any) -> None:
    """404, not 403: someone else's report is indistinguishable from one that
    never existed."""
    FakeService.raises = NotFoundError("Report not found.")

    response = client.get(f"/api/v1/reports/{REPORT_ID}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


ROUTES: list[tuple[str, str, dict | None]] = [
    ("get", "", None),
    ("post", "", {"name": "New", "connection_id": str(CONNECTION_ID)}),
    ("get", f"/{REPORT_ID}", None),
    ("patch", f"/{REPORT_ID}", {"name": "Renamed"}),
    ("delete", f"/{REPORT_ID}", None),
    ("post", f"/{REPORT_ID}/outline", None),
    ("post", f"/{REPORT_ID}/sections", {"heading": "New section"}),
    ("patch", f"/{REPORT_ID}/sections/{SECTION_ID}", {"heading": "Renamed"}),
    ("delete", f"/{REPORT_ID}/sections/{SECTION_ID}", None),
    (
        "post",
        f"/{REPORT_ID}/sections/{SECTION_ID}/blocks",
        {"question": "revenue by month"},
    ),
    ("post", f"/{REPORT_ID}/blocks/{BLOCK_ID}/check", None),
    ("patch", f"/{REPORT_ID}/blocks/{BLOCK_ID}", {"question": "revenue by week"}),
    ("delete", f"/{REPORT_ID}/blocks/{BLOCK_ID}", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_route_scopes_to_the_caller(
    client: Any, method: str, path: str, body: dict | None
) -> None:
    """The sweep. Every route must reach the service with the *session's* user
    id — never one from the URL, the body, or a default."""
    response = getattr(client, method)(
        f"/api/v1/reports{path}", **({"json": body} if body is not None else {})
    )

    assert response.status_code < 400, response.text
    owners = [
        kwargs["owner_id"] for _m, kwargs in FakeService.calls if "owner_id" in kwargs
    ]
    assert owners, f"{method.upper()} {path} reached no owner-scoped service call"
    assert all(owner == USER for owner in owners)


def test_the_sweep_covers_every_route_the_app_publishes() -> None:
    """A route added in a later phase must be added to `ROUTES` too, or this
    fails — the scoping sweep above is only as good as its list."""
    app = create_app()
    published = {
        (method.lower(), path.replace("/api/v1/reports", ""))
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/v1/reports")
        for method in item
    }
    covered = {(method, path) for method, path, _body in ROUTES}

    def _template(path: str) -> str:
        return (
            path.replace(str(REPORT_ID), "{report_id}")
            .replace(str(SECTION_ID), "{section_id}")
            .replace(str(BLOCK_ID), "{block_id}")
        )

    assert {(m, _template(p)) for m, p in covered} == published


def test_a_block_path_is_never_swallowed_by_a_section_path() -> None:
    """Both live under `/reports/{report_id}/...`, and a route table that
    resolved `/blocks/{id}` as a section id would 404 on every block edit."""
    app = create_app()
    paths = set(app.openapi()["paths"])

    assert "/api/v1/reports/{report_id}/blocks/{block_id}" in paths
    assert "/api/v1/reports/{report_id}/sections/{section_id}" in paths


def test_the_routes_require_a_session() -> None:
    anonymous = TestClient(create_app())

    assert anonymous.get("/api/v1/reports").status_code == 401
    assert anonymous.get(f"/api/v1/reports/{REPORT_ID}").status_code == 401
