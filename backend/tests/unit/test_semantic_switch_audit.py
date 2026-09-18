"""Turning the semantic layer on or off leaves a trace.

The switch changes what the model reads on every question asked through a
connection — the same class of decision as the disclosure policy, which has been
audited as `disclosure.changed` since Phase 6 — and it was the one policy toggle
that wrote nothing. `docs/plans/semantic-layer-model.md` P0.5.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.api.schemas import ConnectionUpdate
from app.api.v1 import connections
from app.core.context import RequestContext
from app.infra.db.models import AuditLog, DatabaseConnection
from app.services import audit

USER = uuid4()
CTX = RequestContext(user_id=USER, email="owner@test.local")


class FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None

    @property
    def audit_rows(self) -> list[AuditLog]:
        return [row for row in self.added if isinstance(row, AuditLog)]


async def _patch(
    monkeypatch: pytest.MonkeyPatch, *, enabled: bool, payload: ConnectionUpdate
) -> tuple[DatabaseConnection, FakeDb]:
    connection = DatabaseConnection(
        id=uuid4(), owner_id=USER, name="sales", database_type="postgres",
        semantic_layer_enabled=enabled, max_rows=1000,
    )

    async def authorized(*_a: Any, **_k: Any) -> DatabaseConnection:
        return connection

    async def as_read(*_a: Any, **_k: Any) -> None:
        # The response is not what this file is about; the audit rows are.
        return None

    monkeypatch.setattr(connections, "_authorized", authorized)
    monkeypatch.setattr(connections, "_with_privileges", as_read)
    db = FakeDb()
    await connections.update_connection(
        connection.id, payload, ctx=CTX, db=db, box=None, authz=None  # type: ignore[arg-type]
    )
    return connection, db


@pytest.mark.asyncio
@pytest.mark.parametrize("before", [True, False])
async def test_flipping_the_switch_writes_one_row_with_both_values(
    monkeypatch: pytest.MonkeyPatch, before: bool
) -> None:
    connection, db = await _patch(
        monkeypatch, enabled=before,
        payload=ConnectionUpdate(semantic_layer_enabled=not before),
    )

    assert connection.semantic_layer_enabled is (not before)
    [row] = db.audit_rows
    assert row.action == connections.SEMANTIC_SWITCH_CHANGED == "semantic.switch.changed"
    assert row.resource_type == audit.CONNECTION
    assert row.resource_id == connection.id
    assert row.actor_user_id == USER
    assert row.detail == {"from": before, "to": not before}


@pytest.mark.asyncio
async def test_saving_the_same_value_is_not_a_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Policy tab sends every field of its half on Save. A row per save would
    # bury the one that mattered.
    _, db = await _patch(
        monkeypatch, enabled=True, payload=ConnectionUpdate(semantic_layer_enabled=True)
    )
    assert db.audit_rows == []


@pytest.mark.asyncio
async def test_an_unrelated_edit_writes_nothing_about_the_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, db = await _patch(monkeypatch, enabled=True, payload=ConnectionUpdate(max_rows=50))
    assert db.audit_rows == []
