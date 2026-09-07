"""Whose tokens these are, and whether the question survives a deletion.

Phases 3 and 4 made the numbers true. This phase asks the question they were
made true *for*: **what has this person spent** — which is a sum over three
tables that record three different kinds of work, joined on a column added in
`0023` and written by three different services.

Nothing here is new production code. It is the phase that proves the per-user
story holds end to end before anything is built on top of it, per Phase 5 of
[docs/token-accounting-plan.md](../../../docs/token-accounting-plan.md), and it
pins three things that are each quietly easy to break:

* **`actor_id` is set at creation, by all three writers.** A service that
  forgets it writes a row belonging to nobody, and nothing fails — the column
  is nullable, because it has to survive its actor being deleted. So the only
  thing standing between a forgotten assignment and a silently unattributable
  row is a test that asserts each writer sets it.
* **Deleting a user does not delete their usage.** `ON DELETE SET NULL`, the
  posture every other reference to `users` here takes: a row recording tokens
  that were really spent stays true after the person who spent them is gone,
  and CLAUDE.md is explicit that deleting history to satisfy a constraint is
  the wrong trade. `CASCADE` would make a departing employee's spend vanish
  from every total that ever included it.
* **The rollup in §6 of the plan actually runs and actually adds up.** It is
  written down in a document as the canonical way to read these columns, and a
  documented query nothing executes is a query that drifts from the schema it
  claims to read.

The last two need a database rather than a fake, because they are assertions
about what the *engine* does with a foreign key and a `UNION ALL` — a stub
session would answer whatever this file told it to. There is no live database
in this suite, so `_engine` builds the four tables that matter from
`Base.metadata` itself, on in-memory SQLite. Building them from the real
metadata rather than from a hand-written `CREATE TABLE` is the point: a column
renamed in `models.py` and not here would fail, where a transcribed schema
would happily keep agreeing with itself.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.domain.value_objects import DisclosurePolicy
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.infra.db.models import Base, DatabaseConnection, LlmConfig
from app.services.report_service import ReportService
from app.services.semantic_service import SemanticService
from tests.unit.test_prompt_version import CTX, OWNER, _create, _settings

#: The rollup from §6 of the plan, verbatim in shape: three tables of work
#: unioned into one column set, joined to the person who caused it. Written
#: portably (no `NULLS LAST`, no `FILTER`) because it runs here on SQLite while
#: it runs on Postgres in production — the property under test is that the
#: columns line up and the sums are right, not the ordering clause.
ROLLUP = sa.text(
    """
    SELECT u.email,
           SUM(x.prompt_tokens)     AS prompt_tokens,
           SUM(x.completion_tokens) AS completion_tokens,
           SUM(x.cost_usd)          AS cost_usd
    FROM (
        SELECT actor_id, prompt_tokens, completion_tokens, cost_usd FROM runs
        UNION ALL
        SELECT actor_id, prompt_tokens, completion_tokens, cost_usd FROM report_runs
        UNION ALL
        SELECT actor_id, prompt_tokens, completion_tokens, cost_usd FROM semantic_jobs
    ) AS x
    JOIN users u ON u.id = x.actor_id
    GROUP BY u.email
    """
)

#: Everything the three run tables point at, transitively. Narrower than
#: `create_all` because the rest of the schema carries types SQLite has no
#: rendering for and this phase has no use for.
_TABLES = (
    "users", "conversations", "messages", "runs", "reports", "report_runs",
    "semantic_jobs", "database_connections", "llm_configs",
)


@compiles(JSONB, "sqlite")
def _jsonb_is_json(_element: Any, _compiler: Any, **_kw: Any) -> str:
    """`JSONB` is Postgres' name for what SQLite spells `JSON`.

    Registered at import, which is global to the process — harmless because it
    only teaches a dialect that renders nothing today to render something, and
    no other suite builds a SQLite schema.
    """
    return "JSON"


@pytest.fixture(scope="module")
def engine() -> tuple[sa.Engine, sa.MetaData]:
    """The four tables that matter, from the real metadata.

    Two edits are made to a copy of the column definitions, and both are about
    Postgres syntax rather than about anything under test: a `'{}'::jsonb`
    server default (SQLite cannot parse the cast, and every insert here is
    explicit anyway) and an `ARRAY` column (bound as a Python list, which the
    SQLite driver refuses). Neither appears on any column this file reads.
    """
    metadata = sa.MetaData()
    for name in _TABLES:
        table = Base.metadata.tables[name].to_metadata(metadata)
        for column in table.columns:
            default = getattr(column.server_default, "arg", "")
            if "::" in str(default):
                column.server_default = None
            if isinstance(column.type, ARRAY):
                column.type = sa.JSON()

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")

    @sa.event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        """SQLite checks foreign keys only when asked, and the whole point of
        two of these tests is what a foreign key does on delete."""
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    return engine, metadata


@pytest.fixture
def session(engine: tuple[sa.Engine, sa.MetaData]) -> Any:
    """A clean set of tables per test, so one test's rows never sum into
    another's total.

    Rows are written with Core inserts against the copied metadata rather than
    with ORM objects, because the mapped classes carry the Postgres `ARRAY`
    types the copy replaced — and this file has no interest in the two columns
    that use them.
    """
    connection, metadata = engine
    with Session(connection) as session:
        session.info["metadata"] = metadata
        yield session
        session.rollback()
        # Child-first, because the foreign keys this module exists to test are
        # being enforced.
        for name in _TABLES[::-1]:
            session.execute(sa.text(f"DELETE FROM {name}"))  # noqa: S608
        session.commit()


# ── actor_id is written, by each of the three services ───────────────────
@pytest.mark.asyncio
async def test_a_chat_run_records_who_asked() -> None:
    """The one the eval already leans on, restated here beside its siblings."""
    run = await _create(_settings())
    assert run.actor_id == OWNER


@pytest.mark.asyncio
async def test_a_report_run_records_who_asked() -> None:
    """A generation is the most expensive thing in the product; an unattributed
    one is the most expensive row nobody can bill."""
    from tests.unit import test_report_service as reports

    db = reports._outline_db(
        sections=[reports._section()], blocks=[reports._block()]
    )
    service = ReportService(
        db, reports.FakeSettings(), OwnerOnlyAuthorizer()
    )  # type: ignore[arg-type]
    run = await service.create_run(reports.CTX, reports.REPORT_ID)
    assert run.actor_id == reports.OWNER
    assert run.actor_id == run.owner_id


@pytest.mark.asyncio
async def test_a_semantic_job_records_who_asked() -> None:
    """The other operation that recorded nothing before Phase 4."""
    connection = DatabaseConnection(
        id=uuid4(), owner_id=OWNER, name="sales", database_type="postgres",
        host="db", port=5432, database_name="sales", username="ro",
        encrypted_password="x", disclosure_policy=DisclosurePolicy.SAMPLE,
    )
    llm = LlmConfig(
        id=uuid4(), owner_id=OWNER, name="deepseek", provider="openai",
        model="m", temperature=0.0, max_tokens=1024, encrypted_api_key=None,
        capabilities={},
    )
    db = _SemanticDb(llm=llm)
    service = SemanticService(
        db, _settings(), OwnerOnlyAuthorizer()
    )  # type: ignore[arg-type]
    job = await service.create_job(
        ctx=CTX, connection=connection, llm_config_id=llm.id,
        mode="MERGE", only_tables=None,
    )
    assert job.actor_id == OWNER
    assert job.actor_id == job.owner_id


# ── the actor can be deleted; the tokens they spent cannot ───────────────
def test_deleting_a_user_leaves_their_usage_rows_intact(session: Any) -> None:
    """`SET NULL`, not `CASCADE`.

    A row saying 12,000 tokens were spent is a true record of a real cost, and
    it stays true after the person who caused it leaves. Under `CASCADE` a
    departing employee's spend would disappear from every historical total that
    ever included it, silently and retroactively — the same trade CLAUDE.md
    refuses for `runs.connection_id` and `runs.llm_config_id`.
    """
    world = _world(session)
    _spend(session, world, run=(100, 10, 0.5), report=(1000, 200, 2.0),
           semantic=(50, 5, None))

    _delete_user(session, world["asker"])

    rows = session.execute(
        sa.text(
            "SELECT actor_id, prompt_tokens FROM runs "
            "UNION ALL SELECT actor_id, prompt_tokens FROM report_runs "
            "UNION ALL SELECT actor_id, prompt_tokens FROM semantic_jobs"
        )
    ).all()
    assert len(rows) == 3, "no usage row may be deleted with its actor"
    assert all(actor is None for actor, _ in rows), "the actor is released"
    assert sorted(tokens for _, tokens in rows) == [50, 100, 1000]


def test_an_orphaned_row_leaves_the_per_user_rollup_rather_than_skewing_it(
    session: Any,
) -> None:
    """The join is inner, and that is the right shape.

    Once `actor_id` is null the row belongs to no user, so it cannot appear
    under one — and it must not be attributed to whoever is left. The tokens
    are still readable; they are simply no longer *per user*, which is an
    honest answer to a question the row can no longer support.
    """
    world = _world(session)
    _spend(session, world, run=(100, 10, 0.5))
    assert session.execute(ROLLUP).all() == [("asker@example.test", 100, 10, 0.5)]

    _delete_user(session, world["asker"])

    assert session.execute(ROLLUP).all() == []
    total = session.execute(sa.text("SELECT SUM(prompt_tokens) FROM runs")).scalar()
    assert total == 100, "the tokens survive the attribution"


# ── the documented rollup ────────────────────────────────────────────────
def test_a_users_total_sums_all_three_kinds_of_work(session: Any) -> None:
    """§6's query, run against §2's schema.

    The number is the point: a chat run, a report generation and a semantic
    build are three different tables written by three different services, and
    the only reason "what has this person spent" is answerable at all is that
    all three carry the same four columns under the same names.
    """
    world = _world(session)
    _spend(session, world, run=(100, 10, 0.5), report=(1000, 200, 2.0),
           semantic=(50, 5, 0.25))

    assert session.execute(ROLLUP).all() == [
        ("asker@example.test", 1150, 215, 2.75)
    ]


def test_two_people_are_not_summed_together(session: Any) -> None:
    """Grouping by `actor_id` and not by `owner_id` is the whole feature.

    Both rows here are owned by the same person and asked by two different
    ones — the shape that arrives the day a connection can be shared, and the
    reason the column was added before that day rather than after it. A rollup
    that grouped by owner would bill one person for both.
    """
    world = _world(session)
    _spend(session, world, run=(100, 10, 0.5))
    _spend(session, world, actor=world["other"], run=(7, 3, 0.01))

    assert {
        email: prompt for email, prompt, _c, _cost in session.execute(ROLLUP)
    } == {"asker@example.test": 100, "other@example.test": 7}


def test_an_unpriced_model_does_not_report_a_real_spend_as_free(
    session: Any,
) -> None:
    """`SUM` skips nulls; it must never be helped to treat one as zero.

    A self-hosted deployment prices as null on every row, and the honest read
    of that total is "unknown", not "$0.00". The tokens are still summed, which
    is what makes the null visible rather than absolute: non-zero tokens beside
    a null cost is the shape that says *measured, but unpriceable*.
    """
    world = _world(session)
    _spend(session, world, run=(100, 10, None), semantic=(50, 5, None))

    [(_email, prompt, completion, cost)] = session.execute(ROLLUP).all()
    assert (prompt, completion) == (150, 15)
    assert cost is None, "an unpriced model stays unpriced, never zero"


def test_a_row_that_measured_nothing_does_not_drag_a_total_down(
    session: Any,
) -> None:
    """A null token count is *not measured*, and averaging over it lies.

    Streamed calls against a provider that sends no usage chunk produce exactly
    this row (§1.3), as does every run written before `0023`. It contributes
    nothing to a sum, which is correct — and it must not contribute a zero to a
    mean, which is why the plan's second rule says a null is never "no tokens".
    """
    world = _world(session)
    _spend(session, world, run=(100, 10, 0.5), report=(None, None, None))

    [(_email, prompt, completion, _cost)] = session.execute(ROLLUP).all()
    assert (prompt, completion) == (100, 10)

    measured = session.execute(
        sa.text(
            "SELECT COUNT(prompt_tokens) FROM ("
            "  SELECT prompt_tokens FROM runs"
            "  UNION ALL SELECT prompt_tokens FROM report_runs) AS x"
        )
    ).scalar()
    assert measured == 1, "COUNT over the column counts what was measured"


# ── fixtures ─────────────────────────────────────────────────────────────
def _world(session: Any) -> dict[str, Any]:
    """Two askers, one owner, and the rows the three run tables point at.

    `owner` owns everything; `asker` and `other` ask. That separation is the
    state `actor_id` exists for and is reachable today only by writing it
    directly — which is exactly what makes the three service-level tests above
    the other half of this file: they prove the writers set the column, and
    these prove the column answers the question once they have.
    """
    tables = session.info["metadata"].tables
    ids = {name: uuid.uuid4() for name in
           ("owner", "asker", "other", "conversation", "message", "report",
            "connection")}

    _insert(session, tables["users"], [
        {"id": ids["owner"], "email": "owner@example.test"},
        {"id": ids["asker"], "email": "asker@example.test"},
        {"id": ids["other"], "email": "other@example.test"},
    ])
    _insert(session, tables["conversations"],
            [{"id": ids["conversation"], "owner_id": ids["owner"]}])
    _insert(session, tables["messages"], [{
        "id": ids["message"], "conversation_id": ids["conversation"],
        "seq": 1, "role": "USER",
    }])
    _insert(session, tables["reports"], [{
        "id": ids["report"], "owner_id": ids["owner"], "name": "Quarterly",
    }])
    _insert(session, tables["database_connections"], [{
        "id": ids["connection"], "owner_id": ids["owner"], "name": "sales",
        "database_type": "postgres", "host": "db", "port": 5432,
        "database_name": "sales", "username": "ro", "encrypted_password": "x",
        "schema_allowlist": [],
    }])
    session.commit()
    return ids


def _insert(session: Any, table: sa.Table, rows: list[dict[str, Any]]) -> None:
    """One insert, with every column the row does not name left to its default.

    Core rather than the ORM: the mapped classes carry Postgres column types
    the SQLite copy of the schema had to replace, and nothing here reads the
    two columns that differ.
    """
    defaults = {
        column.name: (
            column.default.arg(None)
            if column.default is not None and column.default.is_callable
            else column.default.arg
            if column.default is not None
            else None
        )
        for column in table.columns
        if column.default is not None
    }
    session.execute(table.insert(), [{**defaults, **row} for row in rows])


def _spend(
    session: Any,
    world: dict[str, Any],
    *,
    actor: uuid.UUID | None = None,
    run: tuple[int | None, int | None, float | None] | None = None,
    report: tuple[int | None, int | None, float | None] | None = None,
    semantic: tuple[int | None, int | None, float | None] | None = None,
) -> None:
    """Record one unit of each kind of work: owned by `owner`, caused by
    `actor`, and each carrying the four columns the rollup reads."""
    tables = session.info["metadata"].tables
    actor_id = actor or world["asker"]

    def _usage(spend: tuple[int | None, int | None, float | None]) -> dict[str, Any]:
        prompt, completion, cost = spend
        return {
            "owner_id": world["owner"], "actor_id": actor_id,
            "prompt_tokens": prompt, "completion_tokens": completion,
            "cost_usd": cost,
        }

    if run is not None:
        _insert(session, tables["runs"], [{
            "id": uuid.uuid4(), "conversation_id": world["conversation"],
            "user_message_id": world["message"], **_usage(run),
        }])
    if report is not None:
        _insert(session, tables["report_runs"], [{
            "id": uuid.uuid4(), "report_id": world["report"], **_usage(report),
        }])
    if semantic is not None:
        _insert(session, tables["semantic_jobs"], [{
            "id": uuid.uuid4(), "connection_id": world["connection"],
            "only_tables": [], **_usage(semantic),
        }])
    session.commit()


def _delete_user(session: Any, user_id: uuid.UUID) -> None:
    """A Core delete, so the *database* is what releases the rows.

    An ORM `session.delete` would emit its own `UPDATE ... SET actor_id = NULL`
    from the relationship configuration and the test would pass against a
    schema with no `ON DELETE` clause at all — proving the ORM's behaviour
    rather than the constraint's, which is the half that ships.
    """
    users = session.info["metadata"].tables["users"]
    session.execute(users.delete().where(users.c.id == user_id))
    session.commit()


class _SemanticDb:
    """The three reads `create_job` makes: the in-flight probe, the snapshot,
    and the model config it is told to use."""

    def __init__(self, llm: LlmConfig) -> None:
        self._llm = llm
        self.added: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        sql = str(statement).lower()
        if "schema_snapshots" in sql:
            return _Result([_Snapshot()])
        return _Result([])

    async def get(self, _model: Any, _pk: Any) -> Any:
        return self._llm

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _Snapshot:
    tables = [{"name": "orders", "columns": []}]
    relationships: list[Any] = []
    dialect = "postgres"
    version = 1
    catalog_meta: dict[str, Any] = {}
