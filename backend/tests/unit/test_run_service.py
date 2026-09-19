"""`NodeDeps.sections`, loaded where a run's inputs are built.

`docs/plans/retrieval-sections.md` §6.1. Two callers build `NodeDeps` for a
question — `run_service.execute_run` (chat) and `sql_draft_service.draft_sql`
(a dashboard tile or a report block) — and both load the connection's sections
through one function, `section_service.load_sections`. None when there are
none, which is the pre-feature run exactly.
"""
from __future__ import annotations

import inspect
from typing import Any
from uuid import uuid4

from app.infra.db import models
from app.pipeline.sections import SectionSpec
from app.services import run_service, sql_draft_service
from app.services.section_service import load_sections
from app.services.sql_draft_service import draft_sql
from tests.unit.conftest import ACTOR, AsyncSessionShim, _connection
from tests.unit.test_access_behaviour import _every_table  # noqa: F401 — fixture
from tests.unit.test_sql_drafts import (
    AUTHZ,
    CTX,
    VALID_SQL,
    FakeGateway,
    FakeSettings,
    _world,
)


# ── the loader ───────────────────────────────────────────────────────────
async def test_a_connection_with_no_sections_loads_none(db: AsyncSessionShim) -> None:
    connection = _connection(db._session, owner_id=ACTOR)
    assert await load_sections(db, connection.id) is None


async def test_sections_load_in_position_order_as_specs(db: AsyncSessionShim) -> None:
    s = db._session
    connection = _connection(s, owner_id=ACTOR)
    other = _connection(s, owner_id=ACTOR)
    for position, name, tables in ((1, "People", ["public.customers"]),
                                   (0, "Sales", ["public.orders", "public.order_items"])):
        s.add(models.ConnectionSection(
            id=uuid4(), connection_id=connection.id, name=name,
            description=f"{name} things.", tables=tables, origin="CURATED",
            position=position,
        ))
    # Another connection's section is not this connection's.
    s.add(models.ConnectionSection(
        id=uuid4(), connection_id=other.id, name="Elsewhere", description="",
        tables=["public.x"], origin="CURATED", position=0,
    ))
    s.flush()

    assert await load_sections(db, connection.id) == [
        SectionSpec("Sales", "Sales things.", ("public.orders", "public.order_items")),
        SectionSpec("People", "People things.", ("public.customers",)),
    ]


def test_a_chat_run_passes_them_to_the_pipeline() -> None:
    """Asserted on the source, as `test_metric_use.py` does for this method:
    `execute_run` needs a connector, a gateway and a live snapshot to call."""
    source = inspect.getsource(run_service.RunService.execute_run)
    assert "sections=await load_sections(self._db, connection.id)" in source


# ── the draft path, called ───────────────────────────────────────────────
class _Row:
    def __init__(self, name: str, tables: list[str]) -> None:
        self.name, self.description, self.tables = name, f"{name}.", tables


class ScopedGateway(FakeGateway):
    """Answers `scope` with a section name and `generate` with SQL."""

    def __init__(self, section: str, *sql: str) -> None:
        super().__init__(*sql)
        self.section = section
        self.scoped: list[Any] = []

    async def complete(self, _llm: Any, messages: Any) -> Any:
        from app.domain.ports.llm import Completion

        self.scoped.append(list(messages))
        return Completion(text=self.section)


SNAPSHOT: dict[str, Any] = {
    "dialect": "postgres",
    "relationships": [],
    "tables": [
        {"schema": "public", "name": "orders", "approx_row_count": 10, "columns": [
            {"name": "status", "data_type": "text"},
            {"name": "total_amount", "data_type": "numeric"},
        ]},
        {"schema": "public", "name": "customers", "approx_row_count": 10, "columns": [
            {"name": "id", "data_type": "bigint"}, {"name": "name", "data_type": "text"},
        ]},
    ],
}


async def test_a_draft_is_scoped_by_the_connections_sections(monkeypatch: Any) -> None:
    """A tile author's question goes through `scope` like a chat question —
    and the statement it stores is still guarded against the whole snapshot."""
    gateway = ScopedGateway("People", VALID_SQL)
    db, connection, llm_config, _connector = _world(
        monkeypatch, gateway=gateway, snapshot=SNAPSHOT
    )
    db.sections = [_Row("People", ["public.customers"])]

    draft = await draft_sql(
        db, FakeSettings(), connection_id=connection.id, llm_config_id=llm_config.id,
        question="revenue by status", ctx=CTX, authz=AUTHZ,
    )

    assert len(gateway.scoped) == 1
    assert "- People — People." in gateway.scoped[0][0].content
    # The generate prompt was shown the section, not the whole schema…
    assert "public.customers(" in gateway.system_prompt
    assert "public.orders(" not in gateway.system_prompt
    # …and the statement over `public.orders` it came back with passed the guard.
    assert draft.validation_status == "VALID"
    assert draft.referenced_tables == ["public.orders"]


async def test_a_draft_on_a_connection_without_sections_asks_nothing_more(
    monkeypatch: Any,
) -> None:
    gateway = ScopedGateway("People", VALID_SQL)
    db, connection, llm_config, _connector = _world(
        monkeypatch, gateway=gateway, snapshot=SNAPSHOT
    )
    await draft_sql(
        db, FakeSettings(), connection_id=connection.id, llm_config_id=llm_config.id,
        question="revenue by status", ctx=CTX, authz=AUTHZ,
    )
    assert gateway.scoped == []
    assert "public.orders(" in gateway.system_prompt


def test_the_draft_builder_loads_them_too() -> None:
    assert "sections=await load_sections(db, connection.id)" in inspect.getsource(
        sql_draft_service.draft_sql
    )
