"""**The guard is untouched** — `docs/plans/retrieval-sections.md` D2.

A section decides what the model is *shown*, never what it may *query*.
`policy_from_snapshot` builds the allowlist from the **whole** snapshot and is
not passed a section. The alternative — narrowing the guard to the section —
would break every saved dashboard tile and report block over an out-of-section
table, days later, as a per-tile `ERROR` value, for somebody who did not change
the sections.

This is the test that protects every saved artifact in the product. It runs
over real rows: a connection with a section saved that **excludes
`public.orders`**, and then a chat answer and a saved tile, both over
`public.orders`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.clock import utcnow
from app.core.context import RequestContext
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.infra.db import models
from app.pipeline.nodes import NodeDeps
from app.pipeline.pipeline import AnalyticsPipeline
from app.pipeline.state import RunState
from app.services.query_service import (
    execute_saved_sql,
    latest_snapshot,
    policy_from_snapshot,
)
from app.services.section_service import load_sections
from tests.unit.conftest import ACTOR, AsyncSessionShim, _connection
from tests.unit.test_access_behaviour import _every_table  # noqa: F401 — fixture
from tests.unit.test_pipeline_events import (
    ONE_ROW,
    SQL_TOTAL,
    TABLES,
    ScriptedConnector,
)
from tests.unit.test_query_service import FakeConnector, FakeSettings
from tests.unit.test_scope_node import ScopingGateway


@pytest.fixture
def sectioned(db: AsyncSessionShim) -> tuple[AsyncSessionShim, models.DatabaseConnection]:
    s = db._session
    connection = _connection(s, owner_id=ACTOR, name="Sales warehouse")
    s.add(models.SchemaSnapshotRow(
        id=uuid4(), connection_id=connection.id, version=1, dialect="postgres",
        tables=TABLES,
        relationships=[{"from_table": "public.orders", "from_column": "customer_id",
                        "to_table": "public.customers", "to_column": "id"}],
        table_count=len(TABLES), catalog_meta={},
    ))
    # The one section there is leaves `public.orders` out.
    s.add(models.ConnectionSection(
        id=uuid4(), connection_id=connection.id, name="People",
        description="Customers and nothing else.", tables=["public.customers"],
        origin="CURATED", schema_version=1, position=0,
    ))
    s.flush()
    return db, connection


async def test_the_allowlist_is_the_whole_snapshot_whatever_the_sections(
    sectioned: tuple[AsyncSessionShim, models.DatabaseConnection],
) -> None:
    db, connection = sectioned
    snapshot = await latest_snapshot(db, connection.id)
    sections = await load_sections(db, connection.id)
    assert sections is not None
    assert all("public.orders" not in s.tables for s in sections)

    policy = policy_from_snapshot(snapshot, connection)
    assert policy.allowed_tables == {"public.orders", "public.customers"}


async def test_a_chat_answer_naming_an_out_of_section_table_still_validates(
    sectioned: tuple[AsyncSessionShim, models.DatabaseConnection],
) -> None:
    """Routed to *People*, shown only `public.customers` — and the statement
    the model writes over `public.orders` still passes the guard and runs."""
    db, connection = sectioned
    snapshot = await latest_snapshot(db, connection.id)

    async def emit(_t: str, _d: Any) -> None:
        return None

    async def on_step(*_args: Any) -> None:
        return None

    state = RunState(
        run_id=uuid4(), conversation_id=uuid4(), owner_id=ACTOR,
        connection_id=connection.id, question="What was total revenue?",
        deadline_at=utcnow() + timedelta(seconds=60),
    )
    deps = NodeDeps(
        llm_gateway=ScopingGateway(section="People", sql=[SQL_TOTAL]), llm=None,
        connector=ScriptedConnector([ONE_ROW]), snapshot=snapshot, history=[],
        policy=policy_from_snapshot(snapshot, connection), emit=emit,
        sections=await load_sections(db, connection.id),
    )
    await AnalyticsPipeline(on_step=on_step).run(state, deps)

    assert state.scope_sections == ["People"]
    assert state.context is not None
    assert [t["name"] for t in state.context.tables] == ["customers"]
    assert state.attempts[-1].report.status == "VALID"
    assert state.execution is not None and state.execution.row_count == 1
    assert state.error is None


async def test_a_saved_tile_over_an_out_of_section_table_still_executes(
    sectioned: tuple[AsyncSessionShim, models.DatabaseConnection],
) -> None:
    """The dashboard path never reads sections at all: stored SQL is guarded
    against the connection's current snapshot, whole, as it always was."""
    db, connection = sectioned
    connector = FakeConnector()
    result = await execute_saved_sql(
        db, FakeSettings(),  # type: ignore[arg-type]
        sql="SELECT o.status, o.total_amount FROM public.orders o",
        connection=connection,
        ctx=RequestContext(user_id=ACTOR, email="actor@test.local"),
        authz=OwnerOnlyAuthorizer(),
        connector=connector,  # type: ignore[arg-type]
    )
    assert result.status == "OK", result
    assert connector.calls and "public.orders" in connector.calls[0][0]
