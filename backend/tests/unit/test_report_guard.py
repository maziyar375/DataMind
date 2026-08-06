"""The third entry point into the guarded path, and it gets no exemption.

Until reports, SQL reached a driver two ways: the pipeline wrote it and
`validate` guarded it, or a dashboard tile stored it and `execute_saved_sql`
re-guarded it. `report_blocks.sql` is the third, and a third entry point is a
third chance to bypass the guard.

It does not get one. This file is the proof: the corpus from
`test_sqlguard_hostile.py` — the hard CI gate — is replayed *through a report
run*, so a statement sitting in `report_blocks.sql` is stopped by the same wall
a generated statement is. `sql_origin` grants nothing, exactly as it grants
nothing to a tile.

The fixtures come from `tests/integration/test_report_runs.py` rather than being
copied: two fakes of the same worker drift, and the one that drifts is always
the one holding the security test.
"""
from __future__ import annotations

import pytest

from app.domain.value_objects import ReportRunStatus, SqlOrigin
from app.services import query_service
from tests.integration.test_report_runs import (
    GOOD_SQL,
    FakeDb,
    _block,
    _connection,
    _generate,
    _report,
    _run,
    _section,
)
from tests.unit.test_query_service import SNAPSHOT, FakeConnector
from tests.unit.test_sqlguard_hostile import HOSTILE


def _db(**block_fields: object) -> FakeDb:
    return FakeDb(
        run=_run(),
        report=_report(),
        connection=_connection(),
        sections=[_section()],
        blocks=[_block(**block_fields)],  # type: ignore[arg-type]
    )


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> FakeConnector:
    fake = FakeConnector()
    monkeypatch.setattr(query_service, "bind_connector", lambda *a, **k: fake)
    return fake


@pytest.mark.parametrize("sql,_code", HOSTILE)
async def test_hostile_sql_stored_in_a_report_block_is_rejected_at_generation(
    sql: str, _code: str | None, connector: FakeConnector
) -> None:
    """Every statement the guard refuses for a model is refused for a block,
    at execution, with nothing reaching the connector."""
    db = _db(sql=sql)

    await _generate(db)

    assert connector.calls == []
    assert db.results[0].status == "FAILED"
    assert db.run is not None and db.run.status == ReportRunStatus.FAILED


@pytest.mark.parametrize("sql,_code", HOSTILE[:8])
async def test_provenance_grants_nothing(
    sql: str, _code: str | None, connector: FakeConnector
) -> None:
    """`sql_origin` records where the text came from and decides nothing.

    Phase 11 lets a user type SQL into a block directly; the day it lands, a
    `HANDWRITTEN` statement must be exactly as guarded as a generated one.
    """
    db = _db(sql=sql)
    db.blocks[0].sql_origin = SqlOrigin.HANDWRITTEN

    await _generate(db)

    assert connector.calls == []
    assert db.results[0].status == "FAILED"


async def test_a_statement_is_re_validated_against_the_current_snapshot(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector
) -> None:
    """A block validated months ago is not a block validated now.

    The snapshot the guard resolves names against is the connection's *current*
    one, so a table dropped since the block was checked breaks the block loudly
    rather than being run against a schema that no longer has it.
    """
    dropped = {
        **SNAPSHOT,
        "tables": [t for t in SNAPSHOT["tables"] if t["name"] != "orders"],
    }
    monkeypatch.setattr(
        query_service, "latest_snapshot", lambda *a, **k: _async(dropped)
    )
    db = _db(sql=GOOD_SQL)

    await _generate(db)

    assert connector.calls == []
    assert db.results[0].error_code == "E_SCHEMA_CHANGED"


async def test_an_unsynced_connection_can_produce_nothing(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector
) -> None:
    """Fail closed, and say why: "table not allowed" is the wrong sentence for
    someone whose connection has simply never been synced."""
    empty = {"tables": [], "relationships": [], "dialect": "postgres"}
    monkeypatch.setattr(
        query_service, "latest_snapshot", lambda *a, **k: _async(empty)
    )
    db = _db(sql=GOOD_SQL)

    await _generate(db)

    assert connector.calls == []
    assert db.results[0].error_code == "E_NO_SNAPSHOT"


async def test_a_block_may_only_tighten_the_connections_row_cap(
    connector: FakeConnector,
) -> None:
    """Containment belongs to the connection. A block asking for more than the
    connection allows gets the connection's number, not its own."""
    db = _db(sql=GOOD_SQL)
    db.blocks[0].max_rows = 50_000

    await _generate(db)

    _sql, max_rows, timeout_ms = connector.calls[0]
    assert max_rows == 1000
    assert timeout_ms == 30_000


async def test_a_block_that_lowers_the_cap_is_honoured(
    connector: FakeConnector,
) -> None:
    db = _db(sql=GOOD_SQL)
    db.blocks[0].max_rows = 10

    await _generate(db)

    _sql, max_rows, _timeout = connector.calls[0]
    assert max_rows == 10


async def _async(value: object) -> object:
    return value
