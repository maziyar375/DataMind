"""The report tables, and the promises their DDL makes.

Two definitions of the same six tables now exist — the ORM in `models.py` and
the migration under `versions/` — and nothing but a running database usually
notices when they drift apart. So **every** report migration is replayed here
in order against a recorder instead of a connection, and the result is compared
to `Base.metadata` column by column. A new revision that touches these tables
belongs in `MIGRATIONS` below; leaving it out is how the check quietly stops
covering the newest column.

The rest of the file pins the choices in §4 of `docs/reports-plan.md` that are
easy to "tidy" into a bug: a report survives its connection, a *run* survives
the block it was generated from, NULL means Auto, NULL means "not edited", and
every enum-shaped column is a plain string.
"""
from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import sqlalchemy as sa

from app.domain.value_objects import (
    ReportBlockResultStatus,
    ReportBlockType,
    ReportFeasibility,
    ReportLanguage,
    ReportRunStatus,
    ReportSectionKind,
    ReportSectionResultStatus,
    ReportStatus,
    ReportTimeWindow,
    SqlOrigin,
)
from app.infra.db.models import Base

# A revision module imports `alembic.op` and nothing else from alembic, and
# this test replaces `op` regardless — so a stub is enough to read the DDL
# without alembic installed, which keeps the drift check running in the plain
# test environment as well as in the container that actually migrates.
if "alembic" not in sys.modules:
    try:
        import alembic  # noqa: F401
    except ImportError:
        stub = types.ModuleType("alembic")
        stub.op = None  # type: ignore[attr-defined]
        sys.modules["alembic"] = stub

# Not `import` statements: the module names start with a digit, as every
# revision in `versions/` does.
MIGRATIONS = [
    importlib.import_module("app.infra.db.migrations.versions.0008_reports"),
]

TABLES = (
    "reports",
    "report_sections",
    "report_blocks",
    "report_runs",
    "report_block_results",
    "report_section_results",
)


class OpRecorder:
    """Stands in for `alembic.op`: records the DDL instead of emitting it."""

    def __init__(self) -> None:
        self.tables: dict[str, sa.Table] = {}
        self.indexes: list[tuple[str, str, list[str]]] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self._metadata = sa.MetaData()

    def create_table(self, name: str, *columns: Any, **_kw: Any) -> None:
        self.tables[name] = sa.Table(name, self._metadata, *columns)

    def create_index(
        self, name: str, table: str, columns: list[str], **_kw: Any
    ) -> None:
        self.indexes.append((name, table, list(columns)))

    def drop_table(self, name: str, **_kw: Any) -> None:
        self.dropped_tables.append(name)

    def drop_index(self, name: str, **_kw: Any) -> None:
        self.dropped_indexes.append(name)

    def add_column(self, table: str, column: sa.Column[Any], **_kw: Any) -> None:
        self.tables[table].append_column(column)

    def drop_column(self, table: str, name: str, **_kw: Any) -> None:
        self.dropped_columns.append((table, name))


def _replay(direction: str = "upgrade") -> OpRecorder:
    """Every report migration, in order, against one recorder.

    Downgrades run in reverse, which is the only order in which dropping a
    column from a table the next revision drops entirely makes sense.
    """
    recorder = OpRecorder()
    modules = MIGRATIONS if direction == "upgrade" else list(reversed(MIGRATIONS))
    originals = [m.op for m in modules]
    try:
        for module in modules:
            module.op = recorder
            getattr(module, direction)()
    finally:
        for module, original in zip(modules, originals, strict=True):
            module.op = original
    return recorder


def _recorded() -> OpRecorder:
    return _replay()


def _ondelete(column: sa.Column[Any]) -> str | None:
    return next((fk.ondelete for fk in column.foreign_keys), None)


# ── the two definitions must agree ───────────────────────────────────────
def test_the_migration_creates_exactly_the_orm_tables() -> None:
    assert set(_recorded().tables) == set(TABLES)


def test_the_migration_and_the_orm_agree_column_for_column() -> None:
    recorded = _recorded().tables
    for name in TABLES:
        orm = Base.metadata.tables[name]
        assert {c.name for c in recorded[name].columns} == {
            c.name for c in orm.columns
        }, name


def test_they_agree_on_what_may_be_null() -> None:
    """The nullability *is* the feature in four places here; a column that is
    NOT NULL in the database and optional in the ORM fails at INSERT time, in
    production, on a path nobody exercised."""
    recorded = _recorded().tables
    for name in TABLES:
        orm = Base.metadata.tables[name]
        for column in recorded[name].columns:
            assert column.nullable == orm.columns[column.name].nullable, (
                f"{name}.{column.name}"
            )


def test_they_agree_on_every_foreign_key_and_its_delete_rule() -> None:
    recorded = _recorded().tables
    for name in TABLES:
        orm = Base.metadata.tables[name]
        for column in recorded[name].columns:
            mirror = orm.columns[column.name]
            assert {fk.target_fullname for fk in column.foreign_keys} == {
                fk.target_fullname for fk in mirror.foreign_keys
            }, f"{name}.{column.name}"
            assert _ondelete(column) == _ondelete(mirror), f"{name}.{column.name}"


def test_the_revision_chain_is_unbroken() -> None:
    assert [m.revision for m in MIGRATIONS] == ["0008"]
    assert MIGRATIONS[0].down_revision == "0007"
    # Each revision hangs off the one before it: a fork here is two heads and
    # an `alembic upgrade` that refuses to run.
    for earlier, later in zip(MIGRATIONS, MIGRATIONS[1:], strict=False):
        assert later.down_revision == earlier.revision


def test_the_downgrade_drops_exactly_what_the_upgrade_created() -> None:
    up, down = _replay("upgrade"), _replay("downgrade")

    assert set(down.dropped_tables) == set(up.tables)
    assert set(down.dropped_indexes) == {name for name, _t, _c in up.indexes}
    assert down.dropped_columns == []


def test_the_downgrade_drops_children_before_parents() -> None:
    """A drop order that ignores the FK graph passes every check above and
    fails against a real database."""
    dropped = _replay("downgrade").dropped_tables

    for child, parent in (
        ("report_section_results", "report_runs"),
        ("report_block_results", "report_runs"),
        ("report_block_results", "report_blocks"),
        ("report_blocks", "report_sections"),
        ("report_sections", "reports"),
        ("report_runs", "reports"),
    ):
        assert dropped.index(child) < dropped.index(parent), f"{child} < {parent}"


# ── the choices that must survive a tidy-up ──────────────────────────────
def test_a_deleted_connection_leaves_the_report_standing() -> None:
    """SET NULL, not CASCADE. A report whose connection is gone must still be
    readable — it simply cannot regenerate. Never delete the user's work."""
    reports = Base.metadata.tables["reports"]

    assert _ondelete(reports.c.connection_id) == "SET NULL"
    assert reports.c.connection_id.nullable
    assert _ondelete(reports.c.llm_config_id) == "SET NULL"
    assert reports.c.llm_config_id.nullable


def test_a_deleted_report_takes_its_outline_and_its_runs() -> None:
    assert _ondelete(Base.metadata.tables["reports"].c.owner_id) == "CASCADE"
    for table, column in (
        ("report_sections", "report_id"),
        ("report_blocks", "section_id"),
        ("report_runs", "report_id"),
        ("report_block_results", "run_id"),
        ("report_section_results", "run_id"),
    ):
        assert _ondelete(Base.metadata.tables[table].c[column]) == "CASCADE", table


def test_a_deleted_block_leaves_the_run_readable() -> None:
    """The back-references from a result to what produced it are SET NULL, and
    the snapshot columns beside them are NOT NULL. A historical document that
    silently loses a section is not a historical document."""
    results = Base.metadata.tables["report_block_results"]
    sections = Base.metadata.tables["report_section_results"]

    for column in (results.c.block_id, results.c.section_id, sections.c.section_id):
        assert _ondelete(column) == "SET NULL", column.name
        assert column.nullable, column.name

    for column in (
        results.c.heading_snapshot,
        results.c.question_snapshot,
        results.c.sql_text,
        results.c.sql_hash,
        results.c.position,
        sections.c.heading_snapshot,
        sections.c.position,
    ):
        assert not column.nullable, column.name


def test_a_run_carries_the_hash_of_the_sql_it_ran() -> None:
    """Comparing two runs whose block SQL differs is a lie, so the hash is
    snapshotted onto the result rather than read back off the block."""
    assert "sql_hash" in Base.metadata.tables["report_blocks"].c
    assert "sql_hash" in Base.metadata.tables["report_block_results"].c


def test_null_chart_config_means_auto() -> None:
    """Not an empty object: `plan_chart` re-decides on every result, which is
    exactly right for a report re-run on differently-shaped data."""
    chart_config = Base.metadata.tables["report_blocks"].c.chart_config

    assert chart_config.nullable
    assert chart_config.default is None
    assert chart_config.server_default is None


def test_prose_is_two_columns_and_null_means_not_edited() -> None:
    """A regeneration writes `prose` and leaves `edited_prose` NULL on the new
    run; the previous run keeps both. Collapsing these into one column is how a
    regeneration destroys the user's writing."""
    results = Base.metadata.tables["report_section_results"]

    assert not results.c.prose.nullable
    assert results.c.edited_prose.nullable
    assert results.c.edited_prose.default is None
    assert results.c.edited_prose.server_default is None


def test_null_numeric_check_means_the_check_did_not_run() -> None:
    """It flags, never blocks — so "no findings" and "never checked" are
    different answers and the column may not collapse them."""
    numeric_check = Base.metadata.tables["report_section_results"].c.numeric_check

    assert numeric_check.nullable
    assert numeric_check.default is None
    assert numeric_check.server_default is None


def test_a_block_max_rows_override_is_optional() -> None:
    """Unset, not "unlimited": the connection's cap applies when this is NULL,
    and a value may only lower it."""
    assert Base.metadata.tables["report_blocks"].c.max_rows.nullable


def test_the_time_window_is_a_label_with_a_neutral_default() -> None:
    """It drives the prompt and the UI. It is never substituted into a
    statement — the window lives in the SQL as relative date arithmetic the
    database resolves on every run — so a block that named no window must
    default to "none", not to a window nobody asked for."""
    time_window = Base.metadata.tables["report_blocks"].c.time_window

    assert isinstance(time_window.type, sa.String)
    assert not time_window.nullable
    assert time_window.default.arg == ReportTimeWindow.NONE  # type: ignore[union-attr]
    recorded = _recorded().tables["report_blocks"]
    assert recorded.c.time_window.server_default.arg == "none"  # type: ignore[union-attr]


def test_an_unchecked_block_says_so() -> None:
    """Feasibility is not assumed. A block nobody checked is `UNCHECKED`, never
    `FEASIBLE` — the generate button reads this column."""
    status = Base.metadata.tables["report_blocks"].c.feasibility_status

    assert status.default.arg == ReportFeasibility.UNCHECKED  # type: ignore[union-attr]
    recorded = _recorded().tables["report_blocks"]
    assert recorded.c.feasibility_status.server_default.arg == "UNCHECKED"  # type: ignore[union-attr]


def test_one_report_name_per_owner() -> None:
    constraints = {c.name for c in Base.metadata.tables["reports"].constraints}
    assert "uq_report_owner_name" in constraints


def test_the_language_is_pinned_on_the_report_and_snapshotted_on_the_run() -> None:
    """Pinned per report and never inferred per section, so a heading that
    happens to be a metric name cannot flip one paragraph into English."""
    for table in ("reports", "report_runs"):
        column = Base.metadata.tables[table].c.language
        assert not column.nullable, table
        assert column.default.arg in set(ReportLanguage), table  # type: ignore[union-attr]


def test_enum_shaped_columns_are_plain_strings() -> None:
    """Like `runs.status`: a new block type, time window or status needs no
    DDL."""
    for name in TABLES:
        for column in Base.metadata.tables[name].columns:
            assert not isinstance(column.type, sa.Enum), f"{name}.{column.name}"

    blocks = Base.metadata.tables["report_blocks"]
    assert isinstance(blocks.c.block_type.type, sa.String)
    assert isinstance(blocks.c.sql_origin.type, sa.String)
    assert isinstance(Base.metadata.tables["reports"].c.status.type, sa.String)
    assert isinstance(Base.metadata.tables["report_runs"].c.status.type, sa.String)


def test_the_defaults_are_members_of_the_enums_that_describe_them() -> None:
    reports = Base.metadata.tables["reports"]
    sections = Base.metadata.tables["report_sections"]
    blocks = Base.metadata.tables["report_blocks"]
    runs = Base.metadata.tables["report_runs"]

    assert reports.c.status.default.arg == ReportStatus.ACTIVE  # type: ignore[union-attr]
    assert sections.c.kind.default.arg == ReportSectionKind.NORMAL  # type: ignore[union-attr]
    assert blocks.c.block_type.default.arg == ReportBlockType.CHART  # type: ignore[union-attr]
    assert blocks.c.sql_origin.default.arg == SqlOrigin.GENERATED  # type: ignore[union-attr]
    assert runs.c.status.default.arg == ReportRunStatus.QUEUED  # type: ignore[union-attr]

    for table in ("report_block_results", "report_section_results"):
        assert Base.metadata.tables[table].c.status.default.arg == "OK", table  # type: ignore[union-attr]
    assert ReportBlockResultStatus.OK == "OK"
    assert ReportSectionResultStatus.OK == "OK"


def test_a_run_can_be_partly_right() -> None:
    """`PARTIAL` is the honest terminal state for a run that is a set of
    independently-failable parts. Nothing else in this codebase has it, because
    nothing else generates parts that can fail alone."""
    assert ReportRunStatus.PARTIAL.is_terminal
    assert not ReportRunStatus.RUNNING.is_terminal
    assert ReportSectionResultStatus.SKIPPED_NO_DATA in set(ReportSectionResultStatus)


def test_reports_share_no_table_with_dashboards() -> None:
    """The features are peers, not layers: deleting dashboards must leave
    reports working. A foreign key between them is how that stops being true."""
    dashboard_tables = {"dashboards", "dashboard_tiles", "dashboard_tile_cache"}

    assert dashboard_tables.isdisjoint(TABLES)
    for name in TABLES:
        for column in Base.metadata.tables[name].columns:
            for fk in column.foreign_keys:
                assert fk.column.table.name not in dashboard_tables, (
                    f"{name}.{column.name}"
                )
