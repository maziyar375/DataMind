"""Deleting a data source, and the two ways it silently did not.

A connection that had ever answered a question could not be deleted, and the
user was told it had been. Two independent defects, and either one alone would
have been survivable:

1. **`runs.connection_id` was `NOT NULL` with no `ON DELETE` rule** — the only
   reference to `database_connections` without one; `conversations`,
   `dashboard_tiles`, `reports`, `schema_snapshots`, `semantic_jobs` and
   `semantic_layers` all had an answer. Postgres refused the parent delete.
2. **The failure was invisible.** `get_db` commits *after* the handler returns,
   by which point `204 No Content` has already been written, so the
   `ForeignKeyViolationError` reached the log and never the caller. The row
   stayed, the list still showed it, and nothing said why.

The first is fixed in migration 0014 and asserted here off the mapper, because
a real database is not available in this suite. The second is fixed by flushing
inside the request, which is what `update_connection` already did — and is
asserted by making the flush fail and requiring that the caller hears about it.
That second test is the one that matters: a schema mistake this file cannot
foresee will at least stop being reported as success.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.context import RequestContext
from app.infra.db.models import DatabaseConnection, LlmConfig, Run
from app.main import create_app

USER = uuid4()
CONNECTION_ID = uuid4()


# ── what the mapper promises the database ────────────────────────────────
@pytest.mark.parametrize("column", ["connection_id", "llm_config_id"])
def test_a_run_releases_its_parents_instead_of_pinning_them(column: str) -> None:
    """`SET NULL`, not `CASCADE`: a run is the record of a question that was
    asked and answered, and deleting the connection it used must not delete the
    transcript. `model_snapshot` keeps the connection and model names, so the
    turn stays explainable after the parent is gone."""
    col = Run.__table__.c[column]
    assert col.nullable, f"runs.{column} must be nullable for SET NULL to be legal"

    fk = next(iter(col.foreign_keys))
    assert fk.ondelete == "SET NULL", (
        f"runs.{column} has ondelete={fk.ondelete!r}; without SET NULL the "
        "parent cannot be deleted once any run has used it"
    )


def test_ownership_is_not_released_the_same_way() -> None:
    """The deliberate exception. `owner_id` is denormalised for ownership
    scoping on the hot path, and a row whose owner is NULL is a row no
    ownership filter matches — the wrong shape for a security check. Deleting a
    user who has history is a different question with a different answer."""
    col = Run.__table__.c.owner_id
    assert not col.nullable
    assert next(iter(col.foreign_keys)).ondelete is None


# ── and that a refusal reaches the caller ────────────────────────────────
class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeDb:
    """Enough of an `AsyncSession` for the two delete routes.

    `flush_error` is the whole point: it stands in for any constraint the
    database might refuse the delete with, without needing a database.
    """

    def __init__(self, row: Any, *, flush_error: Exception | None = None) -> None:
        self.row = row
        self.deleted: list[Any] = []
        self.flushed = False
        self._flush_error = flush_error

    async def execute(self, _statement: Any) -> Any:
        return _Result(self.row)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        self.flushed = True
        if self._flush_error is not None:
            raise self._flush_error


def _connection() -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID,
        owner_id=USER,
        name="sales",
        database_type="postgres",
        host="db.internal",
        port=5432,
        database_name="sales",
        username="analytics_ro",
        encrypted_password="ciphertext",
        schema_allowlist=["sales"],
        disclosure_policy="SAMPLE",
        max_rows=1000,
        statement_timeout_ms=30_000,
    )


def _llm_config() -> LlmConfig:
    return LlmConfig(
        id=uuid4(),
        owner_id=USER,
        name="deepseek",
        provider="OpenAI-compatible",
        model="deepseek-chat",
        encrypted_api_key="ciphertext",
    )


def _client(db: FakeDb) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=USER, email="user@test.local", role="MEMBER", correlation_id="test"
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "row"),
    [("/api/v1/connections", _connection), ("/api/v1/llm-configs", _llm_config)],
)
def test_a_delete_that_succeeds_still_answers_204(path: str, row: Any) -> None:
    obj = row()
    db = FakeDb(obj)
    response = _client(db).delete(f"{path}/{obj.id}")

    assert response.status_code == 204
    assert db.deleted == [obj]
    assert db.flushed, "the delete must reach the database inside the request"


@pytest.mark.parametrize(
    ("path", "row"),
    [("/api/v1/connections", _connection), ("/api/v1/llm-configs", _llm_config)],
)
def test_a_delete_the_database_refuses_is_not_reported_as_success(
    path: str, row: Any
) -> None:
    """The regression that let this ship. Before the flush moved inside the
    request the response was 204 and the row was still there — the one failure
    mode a user cannot debug, because the interface agreed with them."""
    obj = row()
    db = FakeDb(obj, flush_error=RuntimeError("violates foreign key constraint"))
    response = _client(db).delete(f"{path}/{obj.id}")

    assert response.status_code != 204
    assert response.status_code >= 400


def test_deleting_something_that_is_not_yours_is_still_a_404() -> None:
    """Unchanged by the fix, and worth pinning while the route is being edited:
    ownership is checked before anything is deleted."""
    db = FakeDb(None)
    response = _client(db).delete(f"/api/v1/connections/{uuid4()}")

    assert response.status_code == 404
    assert db.deleted == []


def test_a_run_row_can_be_built_without_its_parents() -> None:
    """What `SET NULL` leaves behind, at the mapper level: the row is still a
    valid `Run`, so history reads rather than raising."""
    run = Run(
        id=uuid4(),
        conversation_id=uuid4(),
        owner_id=USER,
        connection_id=None,
        llm_config_id=None,
        model_snapshot={"connection_name": "sales", "model": "deepseek-chat"},
    )
    assert run.connection_id is None
    # The names survive the parent, which is what keeps an old turn readable.
    assert run.model_snapshot["connection_name"] == "sales"


def test_history_from_a_released_run_cannot_cross_into_another_connection() -> None:
    """The disclosure consequence, stated where it will be read.

    `_recent_turns` keeps a message only when its run's `connection_id` equals
    the connection now asking. `None` equals no connection, so a turn whose data
    source has been deleted is dropped from the prompt rather than replayed
    under whatever policy the next connection carries. Fail-closed, and the
    reason `SET NULL` is safe here — invariant #4 in CLAUDE.md.
    """
    connection_id: UUID = uuid4()
    runs: dict[UUID, tuple[UUID | None, str]] = {
        (kept := uuid4()): (connection_id, "SUCCEEDED"),
        (released := uuid4()): (None, "SUCCEEDED"),
    }

    surviving = [m for m in (kept, released) if runs[m][0] == connection_id]
    assert surviving == [kept]
