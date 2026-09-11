"""What `0023` adds, and the promises the nullability makes.

The migration and `models.py` are two definitions of the same columns, and
nothing but a running database usually notices when they drift. So the
revision is replayed here against a recorder rather than a connection — the
pattern `test_report_models.py` established — and compared to `Base.metadata`
column by column.

The rest of the file pins the decisions in §2 of `docs/plans/token-accounting.md`
that are easy to "tidy" into a bug, and every one of them is about a **null**:

* a token column defaulted to `0` cannot be asked whether a node made no call
  or made one the provider did not report on;
* a `cost_usd` of `0.0` where litellm cannot price the model reports a real
  spend as free — the normal state for a self-hosted deployment;
* an `actor_id` backfilled to `owner_id` is correct today and only today, which
  is precisely why it is written now rather than invented later;
* and no historical row is given a token count it never had, because that is
  the `prompt_version` mistake this tree already carries in five weeks of rows.
"""
from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import sqlalchemy as sa

from app.infra.db.models import Base

# A revision module imports `alembic.op` and nothing else from alembic, and
# this test replaces `op` regardless — so a stub is enough to read the DDL
# without alembic installed.
if "alembic" not in sys.modules:
    try:
        import alembic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("alembic")
        stub.op = None  # type: ignore[attr-defined]
        sys.modules["alembic"] = stub

MIGRATION = importlib.import_module(
    "app.infra.db.migrations.versions.0023_token_accounting"
)

#: What `0023` adds, per table. The literal expectation, written out rather
#: than derived from the migration — a test that computes its expectation from
#: the thing it is testing agrees with any bug the thing has.
ADDED = {
    "run_steps": {"prompt_tokens", "completion_tokens", "llm_latency_ms", "llm_calls"},
    "runs": {"cost_usd", "actor_id"},
    "report_runs": {
        "prompt_tokens", "completion_tokens", "llm_latency_ms", "cost_usd", "actor_id",
    },
    "semantic_jobs": {
        "prompt_tokens", "completion_tokens", "llm_latency_ms", "cost_usd", "actor_id",
    },
}

RUN_TABLES = ("runs", "report_runs", "semantic_jobs")


class OpRecorder:
    """Stands in for `alembic.op`: records the DDL instead of emitting it.

    `0023` creates no table, so unlike the report recorder this one tracks
    `add_column` against tables it has never seen — that *is* the revision.
    """

    def __init__(self) -> None:
        self.added: dict[str, list[sa.Column[Any]]] = {}
        self.dropped_columns: list[tuple[str, str]] = []
        self.indexes: list[tuple[str, str, list[str]]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.foreign_keys: list[dict[str, Any]] = []
        self.dropped_constraints: list[tuple[str, str]] = []
        self.statements: list[str] = []

    def add_column(self, table: str, column: sa.Column[Any], **_kw: Any) -> None:
        self.added.setdefault(table, []).append(column)

    def drop_column(self, table: str, name: str, **_kw: Any) -> None:
        self.dropped_columns.append((table, name))

    def create_index(
        self, name: str, table: str, columns: list[str], **_kw: Any
    ) -> None:
        self.indexes.append((name, table, list(columns)))

    def drop_index(self, name: str, *, table_name: str = "", **_kw: Any) -> None:
        self.dropped_indexes.append((name, table_name))

    def create_foreign_key(
        self,
        name: str,
        source: str,
        referent: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kw: Any,
    ) -> None:
        self.foreign_keys.append(
            {
                "name": name,
                "source": source,
                "referent": referent,
                "local": list(local_cols),
                "remote": list(remote_cols),
                "ondelete": kw.get("ondelete"),
            }
        )

    def drop_constraint(self, name: str, table: str, **_kw: Any) -> None:
        self.dropped_constraints.append((name, table))

    def execute(self, statement: Any, **_kw: Any) -> None:
        self.statements.append(str(statement))


def _replay(direction: str = "upgrade") -> OpRecorder:
    recorder = OpRecorder()
    original = MIGRATION.op
    try:
        MIGRATION.op = recorder
        getattr(MIGRATION, direction)()
    finally:
        MIGRATION.op = original
    return recorder


def _ondelete(column: sa.Column[Any]) -> str | None:
    return next((fk.ondelete for fk in column.foreign_keys), None)


# ── the two definitions must agree ───────────────────────────────────────
def test_the_revision_hangs_off_0022() -> None:
    assert MIGRATION.revision == "0023"
    assert MIGRATION.down_revision == "0022"


def test_the_migration_adds_exactly_these_columns() -> None:
    recorded = {
        table: {c.name for c in columns} for table, columns in _replay().added.items()
    }

    assert recorded == ADDED


def test_every_added_column_exists_in_the_orm_with_the_same_type() -> None:
    """Two definitions of one column is one definition too many unless they are
    checked; a `Float` in the ORM against an `Integer` in the database is a
    cost silently rounded to whole dollars."""
    for table, columns in _replay().added.items():
        orm = Base.metadata.tables[table]
        for column in columns:
            assert column.name in orm.c, f"{table}.{column.name}"
            assert type(orm.c[column.name].type) is type(column.type), (
                f"{table}.{column.name}"
            )


def test_the_downgrade_drops_exactly_what_the_upgrade_added() -> None:
    up, down = _replay("upgrade"), _replay("downgrade")

    added = {(t, c.name) for t, cols in up.added.items() for c in cols}
    assert set(down.dropped_columns) == added
    assert {(n, t) for n, t, _c in up.indexes} == set(down.dropped_indexes)
    assert {(fk["name"], fk["source"]) for fk in up.foreign_keys} == set(
        down.dropped_constraints
    )


def test_the_downgrade_releases_each_actor_column_before_it_drops_it() -> None:
    """Dropping a column still carrying an index and a foreign key fails
    against a real database and passes every set-comparison above, because a
    set has no order. So the order is what is asserted."""
    down = _replay("downgrade")
    steps: list[tuple[str, str, str]] = []
    for name, table in down.dropped_indexes:
        steps.append(("index", table, name))
    for name, table in down.dropped_constraints:
        steps.append(("constraint", table, name))
    for table, column in down.dropped_columns:
        steps.append(("column", table, column))

    # Re-derive the order from the recorder's own append order rather than
    # from three separate lists, which would only re-state the grouping above.
    order = _drop_order()
    for table in RUN_TABLES:
        index = order.index(("index", table))
        constraint = order.index(("constraint", table))
        column = order.index(("column", table, "actor_id"))
        assert index < column, table
        assert constraint < column, table
    assert steps  # the DDL is not empty


def _drop_order() -> list[tuple[str, ...]]:
    """The downgrade's operations in the order it issues them."""
    recorder = OpRecorder()
    order: list[tuple[str, ...]] = []

    class _Ordered(OpRecorder):
        def drop_index(self, name: str, *, table_name: str = "", **kw: Any) -> None:
            order.append(("index", table_name))
            super().drop_index(name, table_name=table_name, **kw)

        def drop_constraint(self, name: str, table: str, **kw: Any) -> None:
            order.append(("constraint", table))
            super().drop_constraint(name, table, **kw)

        def drop_column(self, table: str, name: str, **kw: Any) -> None:
            order.append(("column", table, name))
            super().drop_column(table, name, **kw)

    recorder = _Ordered()
    original = MIGRATION.op
    try:
        MIGRATION.op = recorder
        MIGRATION.downgrade()
    finally:
        MIGRATION.op = original
    return order


# ── §2: a null is "not measured", never "no tokens" ──────────────────────
def test_no_token_column_is_defaulted_to_zero() -> None:
    """The whole reason these are nullable. A node that calls no model
    (`validate`, `execute`) and one that called a provider which reported
    nothing are different facts, and `0` in both places cannot say which."""
    for table, names in ADDED.items():
        orm = Base.metadata.tables[table]
        for name in names - {"actor_id"}:
            column = orm.c[name]
            assert column.nullable, f"{table}.{name}"
            assert column.default is None, f"{table}.{name}"
            assert column.server_default is None, f"{table}.{name}"


def test_the_migration_adds_them_nullable_too() -> None:
    for table, columns in _replay().added.items():
        for column in columns:
            assert column.nullable, f"{table}.{column.name}"
            assert column.server_default is None, f"{table}.{column.name}"


def test_run_steps_keeps_provider_time_apart_from_step_time() -> None:
    """`llm_latency_ms` is not `duration_ms`. A node spending 200ms of its four
    seconds at the provider is a different problem from one spending 3.9s
    there, and one column cannot be asked which."""
    steps = Base.metadata.tables["run_steps"]

    assert "duration_ms" in steps.c
    assert "llm_latency_ms" in steps.c
    assert steps.c.llm_latency_ms is not steps.c.duration_ms


def test_a_step_counts_its_calls_because_generate_repairs() -> None:
    """Not derivable from the step existing: a repaired `generate` is two calls
    and both were paid for."""
    assert "llm_calls" in Base.metadata.tables["run_steps"].c


def test_cost_is_a_float_on_every_run_table() -> None:
    for table in RUN_TABLES:
        column = Base.metadata.tables[table].c.cost_usd
        assert isinstance(column.type, sa.Float), table
        assert column.nullable, table


# ── §1.5: the actor ──────────────────────────────────────────────────────
def test_actor_id_is_set_null_and_owner_id_is_not() -> None:
    """Two different jobs. A usage row whose actor was deleted is still a true
    record of tokens spent; a row whose *owner* is null is a row no ownership
    filter matches, which is why `owner_id` keeps its non-null exception."""
    for table in RUN_TABLES:
        orm = Base.metadata.tables[table]
        assert _ondelete(orm.c.actor_id) == "SET NULL", table
        assert orm.c.actor_id.nullable, table
        assert not orm.c.owner_id.nullable, table
        assert _ondelete(orm.c.owner_id) != "SET NULL", table


def test_the_actor_foreign_key_points_at_users_and_is_set_null() -> None:
    keys = {fk["source"]: fk for fk in _replay().foreign_keys}

    assert set(keys) == set(RUN_TABLES)
    for table, fk in keys.items():
        assert fk["referent"] == "users", table
        assert (fk["local"], fk["remote"]) == (["actor_id"], ["id"]), table
        assert fk["ondelete"] == "SET NULL", table


def test_actor_id_is_backfilled_from_owner_id_on_every_run_table() -> None:
    """Correct by construction rather than by assumption: no path has ever
    written one of these rows with an owner who was not the caller."""
    statements = [s for s in _replay().statements if "actor_id = owner_id" in s]

    assert len(statements) == len(RUN_TABLES)
    for table in RUN_TABLES:
        assert any(f"UPDATE {table} " in s for s in statements), table


def test_no_token_column_is_backfilled() -> None:
    """A historical run's true token count is unknowable, and inventing one
    repeats the `prompt_version` mistake this tree carries in five weeks of
    rows that claim a version they never ran."""
    for statement in _replay().statements:
        for name in ("prompt_tokens", "completion_tokens", "llm_latency_ms",
                     "llm_calls", "cost_usd"):
            assert name not in statement, statement


def test_the_per_user_question_is_indexed_on_all_three_tables() -> None:
    """The one query these columns exist to answer groups by `actor_id` across
    three tables, and an unindexed scan of `runs` is the whole transcript."""
    indexed = {(table, tuple(columns)) for _n, table, columns in _replay().indexes}

    assert indexed == {(table, ("actor_id",)) for table in RUN_TABLES}


def test_the_orm_and_the_migration_agree_on_the_index_name() -> None:
    """`index=True` names an index by convention and the migration names one
    literally; disagreeing means the downgrade drops nothing and the next
    upgrade collides."""
    recorded = {name for name, _t, _c in _replay().indexes}
    declared = {
        index.name
        for table in RUN_TABLES
        for index in Base.metadata.tables[table].indexes
        if [c.name for c in index.columns] == ["actor_id"]
    }

    assert recorded == declared
