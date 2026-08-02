"""Redrawing a finished run's chart as another type.

The picker in chat sends a type and nothing else. What that must *not* become
is a second renderer: compiling a spec in the browser would put a chart on
screen the backend never approved, and re-running the SQL would let the picture
drift away from the table printed underneath it. So the redraw recompiles from
the rows already stored in the run's TABLE artifact, and these tests hold that
line — the rows the redraw sees, the types it accepts, and the fact that it
leaves one CHART artifact behind rather than a pile of them.

The session is faked because none of that needs a database: the whole path is
two selects, a profile, and a compile.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.domain.value_objects import ArtifactKind
from app.infra.db.models import Artifact, Run
from app.services.run_service import redraw_chart

OWNER = uuid4()

COLUMNS = [
    {"name": "status", "db_type": "text", "semantic_type": "nominal"},
    {"name": "total", "db_type": "numeric", "semantic_type": "quantitative"},
]
ROWS: list[list[Any]] = [["paid", 120.0], ["pending", 60.0], ["refunded", 20.0]]


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeDb:
    """Enough session to answer two `select`s and record what changed."""

    def __init__(self, run: Run | None, artifacts: list[Artifact]) -> None:
        self.run = run
        self.artifacts = artifacts
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.flushes = 0

    async def get(self, _model: type, _entity_id: UUID) -> Any:
        return self.run

    async def execute(self, statement: Any) -> FakeResult:
        kind = ArtifactKind.TABLE if "'TABLE'" in str(
            statement.compile(compile_kwargs={"literal_binds": True})
        ) else ArtifactKind.CHART
        return FakeResult([a for a in self.artifacts if a.kind == kind])

    def add(self, entity: Any) -> None:
        self.added.append(entity)
        self.artifacts.append(entity)

    async def delete(self, entity: Any) -> None:
        self.deleted.append(entity)

    async def flush(self) -> None:
        self.flushes += 1


def _table(rows: list[list[Any]] | None = None) -> Artifact:
    return Artifact(
        id=uuid4(),
        run_id=uuid4(),
        kind=ArtifactKind.TABLE,
        spec={
            "columns": COLUMNS,
            "rows": ROWS if rows is None else rows,
            "row_count": len(ROWS if rows is None else rows),
            "truncated": False,
        },
    )


def _setup(*, artifacts: list[Artifact] | None = None) -> tuple[FakeDb, UUID]:
    run_id = uuid4()
    run = Run(id=run_id, owner_id=OWNER)
    return FakeDb(run, artifacts if artifacts is not None else [_table()]), run_id


async def test_a_redraw_compiles_the_type_that_was_asked_for() -> None:
    db, run_id = _setup()

    artifact = await redraw_chart(
        db, run_id=run_id, owner_id=OWNER, chart_type="pie"  # type: ignore[arg-type]
    )

    assert artifact.kind == ArtifactKind.CHART
    assert artifact.spec["usermeta"]["datamind"]["chart_type"] == "pie"


async def test_the_redraw_sees_the_rows_the_run_already_returned() -> None:
    """The property worth protecting: a picker that re-queried could show a
    different answer than the table sitting under it."""
    db, run_id = _setup(artifacts=[_table([["paid", 5.0], ["void", 1.0]])])

    artifact = await redraw_chart(
        db, run_id=run_id, owner_id=OWNER, chart_type="bar"  # type: ignore[arg-type]
    )

    values = [row["total"] for row in artifact.spec["data"]["values"]]
    assert values == [5.0, 1.0]


async def test_a_type_this_result_cannot_carry_is_refused() -> None:
    """The same list that greys the button out. A client that asks anyway gets
    a 400, never a silent substitution — a picker that quietly drew something
    else is the behaviour this phase removed."""
    db, run_id = _setup()

    with pytest.raises(ValidationError):
        await redraw_chart(
            db, run_id=run_id, owner_id=OWNER, chart_type="heatmap"  # type: ignore[arg-type]
        )


async def test_a_redraw_replaces_the_chart_rather_than_appending_one() -> None:
    """A CHART artifact is a presentation of the TABLE, not a record of what
    happened — and two of them would leave nothing saying which one the reader
    is looking at. What the pipeline decided stays legible in the step trail."""
    existing = Artifact(
        id=uuid4(), run_id=uuid4(), kind=ArtifactKind.CHART, spec={"mark": "bar"}
    )
    db, run_id = _setup(artifacts=[_table(), existing])

    artifact = await redraw_chart(
        db, run_id=run_id, owner_id=OWNER, chart_type="pie"  # type: ignore[arg-type]
    )

    assert artifact is existing
    assert db.added == [] and db.deleted == []
    assert artifact.spec["usermeta"]["datamind"]["chart_type"] == "pie"


async def test_a_run_that_is_not_yours_is_not_found() -> None:
    db, run_id = _setup()

    with pytest.raises(NotFoundError):
        await redraw_chart(
            db, run_id=run_id, owner_id=uuid4(), chart_type="bar"  # type: ignore[arg-type]
        )


async def test_a_run_with_no_result_has_nothing_to_draw() -> None:
    db, run_id = _setup(artifacts=[])

    with pytest.raises(NotFoundError):
        await redraw_chart(
            db, run_id=run_id, owner_id=OWNER, chart_type="bar"  # type: ignore[arg-type]
        )
