"""The two draft routes, over HTTP.

The service tests drive `draft_sql`/`validate_sql` directly; these drive the
app, because the failures they catch are different ones — a DTO that cannot
serialise what the service returns, a route that never got registered, an
error that comes back as a bare 500 instead of problem+json. §5 says the draft
path is "testable over HTTP alone", so it is tested that way.

Auth, the session and the settings are overridden; the service itself is
replaced per test, since what it does is already covered next door.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1 import drafts
from app.core.context import RequestContext
from app.core.errors import LLMError, NotFoundError, ValidationError
from app.domain.ports.database import ResultColumn
from app.main import create_app
from app.services.query_service import TileResult
from app.services.sql_draft_service import SqlDraft

USER = uuid4()
CONNECTION = uuid4()
LLM_CONFIG = uuid4()

PREVIEW = TileResult(
    status="OK",
    columns=[
        ResultColumn(name="status", db_type="text", semantic_type="nominal"),
        ResultColumn(name="total", db_type="numeric", semantic_type="quantitative"),
    ],
    rows=[["paid", 120.0], ["pending", 60.0]],
    row_count=2,
    duration_ms=11,
    vega_spec={"mark": "bar"},
    chart_source="heuristic",
)

DRAFT = SqlDraft(
    sql="SELECT status, total FROM public.orders",
    validation_status="VALID",
    validation_report={"status": "VALID", "issues": []},
    referenced_tables=["public.orders"],
    chart_suggestion={"chart_type": "bar"},
    preview=PREVIEW,
    question="revenue by status",
    llm_config_id=LLM_CONFIG,
)


@pytest.fixture
def client() -> Any:
    """The app without its lifespan.

    `TestClient` only runs startup inside a `with` block, and startup here
    bootstraps an admin, sweeps orphaned runs and starts the reconciler — all
    of which want a database. The routes under test want none of it.
    """
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: None
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER, email="user@test.local", role="MEMBER", correlation_id="test"
    )
    yield TestClient(app)
    app.dependency_overrides.clear()


def _serve(monkeypatch: pytest.MonkeyPatch, name: str, result: Any) -> list[dict]:
    """Replace one service function, recording the arguments it was called with."""
    calls: list[dict] = []

    async def fake(_db: Any, _settings: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(drafts, name, fake)
    return calls


def test_a_draft_comes_back_as_json_the_editor_can_render(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _serve(monkeypatch, "draft_sql", DRAFT)

    response = client.post(
        "/api/v1/sql/drafts",
        json={
            "connection_id": str(CONNECTION),
            "llm_config_id": str(LLM_CONFIG),
            "question": "revenue by status",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sql"] == DRAFT.sql
    assert body["validation_status"] == "VALID"
    assert body["referenced_tables"] == ["public.orders"]
    assert body["chart_suggestion"] == {"chart_type": "bar"}
    assert body["preview"]["row_count"] == 2
    assert body["preview"]["columns"][0]["name"] == "status"
    assert body["preview"]["vega_spec"] == {"mark": "bar"}
    assert body["preview"]["computed_at"] is not None
    assert body["preview"]["error"] is None
    # The route passes the caller's identity, never the body's.
    assert calls[0]["owner_id"] == USER


def test_a_rejected_draft_is_a_200_with_the_report(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal by the guard is an answer, not a failed request: the editor
    shows the reasons inline the way the metric editor does."""
    _serve(
        monkeypatch,
        "draft_sql",
        SqlDraft(
            sql="DROP TABLE orders",
            validation_status="REJECTED",
            validation_report={
                "status": "REJECTED",
                "issues": [{"rule_id": "E_NOT_A_SELECT", "message": "Not a SELECT."}],
            },
            referenced_tables=[],
        ),
    )

    response = client.post(
        "/api/v1/sql/drafts",
        json={
            "connection_id": str(CONNECTION),
            "llm_config_id": str(LLM_CONFIG),
            "question": "drop everything",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["validation_status"] == "REJECTED"
    assert body["preview"] is None
    assert body["validation_report"]["issues"][0]["rule_id"] == "E_NOT_A_SELECT"


def test_an_error_from_a_tile_result_reaches_the_client(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview that ran and failed is still a 200 — the draft exists, its
    preview is what went wrong."""
    _serve(
        monkeypatch,
        "validate_sql",
        SqlDraft(
            sql="SELECT 1 FROM public.orders",
            validation_status="VALID",
            validation_report={"status": "VALID"},
            referenced_tables=["public.orders"],
            preview=TileResult(
                status="ERROR",
                error_code="E_QUERY_FAILED",
                error_message="relation does not exist",
            ),
        ),
    )

    response = client.post(
        "/api/v1/sql/drafts/validate",
        json={"connection_id": str(CONNECTION), "sql": "SELECT 1 FROM public.orders"},
    )

    assert response.status_code == 200
    assert response.json()["preview"]["error"] == {
        "code": "E_QUERY_FAILED",
        "message": "relation does not exist",
    }


def test_hand_written_sql_needs_no_model(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `llm_config_id` in the request body: the route is usable with no
    provider configured at all."""
    calls = _serve(monkeypatch, "validate_sql", DRAFT)

    response = client.post(
        "/api/v1/sql/drafts/validate",
        json={"connection_id": str(CONNECTION), "sql": "SELECT 1"},
    )

    assert response.status_code == 200
    assert "llm_config_id" not in calls[0]


@pytest.mark.parametrize(
    "error,expected",
    [
        (NotFoundError("Connection not found."), 404),
        (ValidationError("Sync this connection's schema first."), 422),
        (LLMError("The provider is unavailable."), 502),
    ],
)
def test_service_errors_come_back_as_problem_json(
    client: Any, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: int
) -> None:
    _serve(monkeypatch, "draft_sql", error)

    response = client.post(
        "/api/v1/sql/drafts",
        json={
            "connection_id": str(CONNECTION),
            "llm_config_id": str(LLM_CONFIG),
            "question": "revenue",
        },
    )

    assert response.status_code == expected
    assert response.headers["content-type"].startswith("application/problem+json")


def test_an_empty_question_is_refused_before_a_model_is_asked(client: Any) -> None:
    response = client.post(
        "/api/v1/sql/drafts",
        json={
            "connection_id": str(CONNECTION),
            "llm_config_id": str(LLM_CONFIG),
            "question": "",
        },
    )

    assert response.status_code == 422


def test_the_routes_require_a_session() -> None:
    """No override this time: an unauthenticated draft is a 401, not a draft."""
    anonymous = TestClient(create_app())
    response = anonymous.post(
        "/api/v1/sql/drafts/validate",
        json={"connection_id": str(CONNECTION), "sql": "SELECT 1"},
    )

    assert response.status_code == 401
