"""Store health: the sweep that stops the store decaying into noise.

Two kinds of decay, two halves of this file.

**Staleness is a parse.** A schema sync re-validates every live template; one
whose SQL no longer resolves becomes `STALE` with the guard's own sentence,
is withdrawn from matching and from few-shot, and is **never deleted**. The
transition that gets forgotten is the other one — a template that resolves
again comes back to `ACTIVE`, because a store that can enter a bad state and
never leave it is one nobody trusts.

**Conflict is an execution, and it is the thing no competitor can do.** Fabric
detects conflicting instructions by reasoning over SQL *text* and reports a
confidence score of one to five. DataMind runs both statements through the
guard and compares the result sets with `app/knowledge/compare.py`, so a
conflict here is a fact — and the diverging rows are stored as the evidence,
because a conflict a curator cannot see the proof of is one more warning nobody
acts on.

The claims, in the order they would hurt if they broke:

* a template the schema broke is withdrawn, kept, and says which object moved;
* a template the schema healed comes back on its own;
* two templates that disagree are **both** marked, with the rows that prove it;
* two templates that agree are not marked, and a conflict that no longer holds
  is cleared;
* a pair that cannot be probed is skipped **loudly** rather than compared with
  invented values — a check that says the store is healthy because it could not
  test it is worse than no check;
* the checker obeys the connection's off switch, its read-only credentials and
  the guard, and never runs on a request path.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.config import Settings
from app.infra.db.models import (
    DatabaseConnection,
    KnowledgeTemplateRow,
    SchemaSnapshotRow,
)
from app.knowledge import (
    KnowledgeTemplate,
    ParamType,
    TemplateParam,
    TemplateRole,
    TemplateStatus,
    normalize_question,
)
from app.knowledge.compare import (
    MAX_EVIDENCE_ROWS,
    Divergence,
    first_difference,
    result_sets_match,
    rows_equal,
    values_equal,
)
from app.knowledge.conflict import (
    CONFLICT_SIMILARITY_THRESHOLD,
    probe_values,
    similar_pairs,
)
from app.services.knowledge_service import UNUSED_AFTER_DAYS, KnowledgeService
from app.workers import knowledge_maintenance as maintenance

CONNECTION_ID = uuid4()
OWNER = uuid4()

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "created_at", "data_type": "date"},
            {"name": "region", "data_type": "text"},
            {"name": "status", "data_type": "text"},
            {"name": "amount", "data_type": "numeric"},
        ],
    }
]

# The schema after somebody renamed a column — §3.5's own acceptance test.
TABLES_AFTER_RENAME = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "created_at", "data_type": "date"},
            {"name": "sales_region", "data_type": "text"},
            {"name": "status", "data_type": "text"},
            {"name": "amount", "data_type": "numeric"},
        ],
    }
]

REGION_SQL = "SELECT SUM(amount) FROM public.orders WHERE region = :region"
PLAIN_SQL = "SELECT SUM(amount) FROM public.orders"
CANCELLED_SQL = (
    "SELECT SUM(amount) FROM public.orders WHERE status <> 'CANCELLED'"
)


def _connection(**kwargs: Any) -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID, owner_id=OWNER, name="aurora",
        database_type="postgres", host="h", port=5432,
        database_name="aurora", username="ro", encrypted_password="x",
        max_rows=1000, statement_timeout_ms=30_000,
        disclosure_policy="SAMPLE",
        conflict_checks_enabled=kwargs.pop("conflict_checks_enabled", True),
        **kwargs,
    )


def _row(
    *,
    question: str,
    sql: str,
    status: TemplateStatus = TemplateStatus.ACTIVE,
    role: TemplateRole = TemplateRole.RETRIEVABLE,
    params: list[dict[str, Any]] | None = None,
    hit_count: int = 0,
    created_at: datetime | None = None,
    conflicts_with: list[UUID] | None = None,
    status_reason: str = "",
) -> KnowledgeTemplateRow:
    now = utcnow()
    return KnowledgeTemplateRow(
        id=uuid4(), connection_id=CONNECTION_ID,
        question=question, question_normalized=normalize_question(question),
        sql=sql, params=params or [], note="",
        source="MANUAL", literal_provenance="HUMAN_AUTHORED",
        role=str(role), status=str(status), status_reason=status_reason,
        schema_version=1, referenced_tables=["public.orders"],
        conflicts_with=conflicts_with or [], conflict_evidence={},
        hit_count=hit_count, created_at=created_at or now, updated_at=now,
    )


class FakeDb:
    """Enough of an `AsyncSession` for the sweep and the conflict checker."""

    def __init__(
        self,
        rows: list[KnowledgeTemplateRow],
        tables: list[dict[str, Any]] | None = None,
        version: int = 2,
    ) -> None:
        self.rows = rows
        self.snapshot = SchemaSnapshotRow(
            id=uuid4(), connection_id=CONNECTION_ID, version=version,
            dialect="postgres", tables=tables if tables is not None else TABLES,
            relationships=[], table_count=1,
        )
        self.flushes = 0

    async def execute(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0].get("entity")
        if entity is KnowledgeTemplateRow:
            return _Result(self._filtered(statement))
        if entity is SchemaSnapshotRow:
            return _Result([self.snapshot])
        raise AssertionError(f"unexpected query: {entity}")

    def _filtered(self, statement: Any) -> list[KnowledgeTemplateRow]:
        """Honour the status/role predicates rather than answering everything.

        §1.3's exclusion lives *in the query*, so a fake that ignored the
        predicate would prove nothing about the one place it is enforced.
        """
        params = statement.compile().params
        rows = list(self.rows)
        for key, value in params.items():
            if key.startswith("status") and isinstance(value, (list, tuple)):
                rows = [r for r in rows if r.status in value]
            elif key.startswith("status") and "!=" in str(statement.whereclause):
                rows = [r for r in rows if r.status != value]
            elif key.startswith("role"):
                rows = [r for r in rows if r.role == value]
        return rows

    async def flush(self) -> None:
        self.flushes += 1

    async def get(self, model: Any, key: UUID) -> Any:
        if model is KnowledgeTemplateRow:
            return next((r for r in self.rows if r.id == key), None)
        return None


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> Any:
        return _Scalars(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ── the comparator, moved down a layer ───────────────────────────────────
def test_the_comparator_is_one_implementation_shared_by_both_callers() -> None:
    """`app.eval.metrics` re-exports it rather than keeping a second copy.

    Two implementations of "do these result sets agree" would drift, and the
    one that drifted would be the one the customer's benchmark uses — the
    number in front of them, computed by the tolerance nobody re-measured.
    """
    from app.eval import metrics

    assert metrics.values_equal is values_equal
    assert metrics.result_sets_match is result_sets_match
    assert metrics.NUMERIC_ABS_TOLERANCE == 5e-3


def test_the_request_path_still_cannot_reach_the_eval_harness() -> None:
    """The contract the move was designed to keep, asserted rather than assumed.

    `app.knowledge` is below `app.eval`, so the comparator came *down*; if
    somebody later moves it back up, this fails before `lint-imports` runs.
    """
    import ast
    import pathlib

    import app.knowledge.compare as compare

    tree = ast.parse(pathlib.Path(compare.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith("app.eval") for name in imported)
    assert not any(name.startswith("app.") for name in imported)


# ── the evidence ─────────────────────────────────────────────────────────
def test_identical_result_sets_do_not_differ() -> None:
    rows = [["2026-07", 481_220], ["2026-08", 502_010]]
    assert not first_difference(rows, list(rows)).differs


def test_a_value_difference_carries_the_rows_that_prove_it() -> None:
    # §4.7's pane, as a data structure: two answers to one question, and the
    # number that differs visible in both.
    left = [["2026-07", 481_220]]
    right = [["2026-07", 512_940]]
    found = first_difference(
        left, right, left_columns=["month", "revenue"],
        right_columns=["month", "revenue"],
    )

    assert found.differs
    assert found.left_rows == left and found.right_rows == right
    assert "different values" in found.summary


def test_a_row_count_difference_says_so_in_the_summary() -> None:
    # The canonical conflict: one statement filters cancelled orders and the
    # other does not, so one returns more rows.
    found = first_difference([[1], [2], [3]], [[1], [2]])
    assert found.differs and "different numbers of rows" in found.summary


def test_a_shape_difference_is_reported_before_the_rows_are_compared() -> None:
    found = first_difference(
        [[1, 2]], [[1, 2, 3]],
        left_columns=["a", "b"], right_columns=["a", "b", "c"],
    )
    assert found.differs and "different columns" in found.summary


def test_the_evidence_is_the_diverging_rows_not_the_first_five() -> None:
    """A disagreement at row two hundred is still a disagreement.

    Showing the head of two tables that agree for the first two hundred rows is
    showing nothing, which is how a conflict pane becomes decoration.
    """
    left = [[i, 100] for i in range(200)] + [[200, 111]]
    right = [[i, 100] for i in range(200)] + [[200, 999]]
    found = first_difference(left, right)

    assert found.differs
    assert found.left_rows == [[200, 111]]
    assert found.right_rows == [[200, 999]]


def test_the_evidence_is_capped() -> None:
    left = [[i] for i in range(50)]
    right = [[i + 1000] for i in range(50)]
    found = first_difference(left, right)
    assert len(found.left_rows) == MAX_EVIDENCE_ROWS


def test_evidence_stringifies_the_headers_the_connector_actually_returns() -> None:
    """`QueryResult.columns` is a list of `ResultColumn`, not of strings.

    Caught end to end against a real database rather than here: the fake in
    this file handed over strings, so `as_dict` looked JSON-safe and the first
    real conflict failed with *"Object of type ResultColumn is not JSON
    serializable"* while writing `conflict_evidence`. The name is read off
    whatever the connector returned, and both forms are pinned.
    """
    from app.domain.ports.database import ResultColumn

    found = first_difference(
        [[1]], [[2]],
        left_columns=[ResultColumn(name="revenue", db_type="numeric")],
        right_columns=["revenue"],
    )
    stored = found.as_dict()
    assert stored["left_columns"] == ["revenue"]
    assert stored["right_columns"] == ["revenue"]
    # JSON-safe means JSON-safe: this is what goes into a JSONB column.
    import json

    json.dumps(stored)


def test_evidence_stringifies_every_cell_for_the_column() -> None:
    """`conflict_evidence` is JSONB, and a Decimal is not JSON.

    Stringified here rather than at render time, so the stored evidence is
    exactly what the curator will be shown.
    """
    from decimal import Decimal

    found = first_difference([[Decimal("1.50"), None]], [[Decimal("2.50"), None]])
    stored = found.as_dict()
    assert stored["left_rows"] == [["1.50", ""]]
    assert stored["right_rows"] == [["2.50", ""]]


def test_tolerance_is_the_evals_tolerance_not_a_new_one() -> None:
    # Half a cent apart is the *same* answer at the precision a gold states,
    # and a conflict checker with a stricter rule than the eval's would flag
    # every pair of correct templates that round differently.
    assert rows_equal([957.42], [957.416])
    assert not first_difference([[957.42]], [[957.416]]).differs


# ── which pairs are worth running ────────────────────────────────────────
def _template(question: str, sql: str = PLAIN_SQL, **kwargs: Any) -> KnowledgeTemplate:
    return KnowledgeTemplate(
        id=uuid4(), question=question,
        question_normalized=normalize_question(question), sql=sql, **kwargs
    )


def test_near_duplicate_questions_are_paired() -> None:
    pairs = similar_pairs([_template("monthly revenue"), _template("revenue by month")])
    assert len(pairs) == 1
    assert pairs[0].similarity >= CONFLICT_SIMILARITY_THRESHOLD


def test_genuinely_different_questions_are_not_paired() -> None:
    """The threshold's whole job.

    *"total revenue"* against *"total refunds"* scores 0.40 and *"revenue by
    region"* against *"orders by region"* 0.44 — both far below the 0.60 gate,
    and both would be a false conflict that costs two queries and one wrong
    amber row.
    """
    assert similar_pairs([
        _template("total revenue"),
        _template("total refunds"),
        _template("how many orders were cancelled"),
    ]) == []


def test_pairs_come_back_most_alike_first() -> None:
    pairs = similar_pairs([
        _template("monthly revenue"),
        _template("revenue by month"),
        _template("revenue by month for last year"),
    ])
    scores = [p.similarity for p in pairs]
    assert scores == sorted(scores, reverse=True)


def test_one_template_produces_no_pairs() -> None:
    # The store's most common state, and the one where an off-by-one in the
    # inner loop would compare a template with itself and mark it conflicted
    # with the rows it agrees with perfectly.
    assert similar_pairs([_template("monthly revenue")]) == []
    assert similar_pairs([]) == []


# ── probe values ─────────────────────────────────────────────────────────
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_a_date_pair_is_probed_with_one_window() -> None:
    probe = probe_values(
        [
            TemplateParam(name="from_date", type=ParamType.DATE),
            TemplateParam(name="to_date", type=ParamType.DATE),
        ],
        now=NOW,
    )
    assert probe.ok
    assert probe.values["from_date"] < probe.values["to_date"]
    # Closed in the past, so two runs of the checker a minute apart compare the
    # same data and a conflict cannot appear and vanish on the clock.
    assert probe.values["to_date"] < NOW.date()


def test_a_string_slot_is_probed_with_a_value_the_curator_declared() -> None:
    probe = probe_values(
        [TemplateParam(name="region", comment="one of: EMEA, NA, APAC")], now=NOW
    )
    assert probe.values == {"region": "EMEA"}


def test_a_string_slot_with_no_vocabulary_stops_the_pair() -> None:
    """Refusing to guess, for the reason the binder refuses on the ask path.

    A plausible-looking noun would produce two statements that return nothing
    and a comparison that says "these agree" — a check that reports the store
    healthy because it could not test it, which is worse than no check.
    """
    probe = probe_values(
        [TemplateParam(name="customer", comment="the customer name")], now=NOW
    )
    assert not probe.ok and probe.unfilled == ["customer"]


def test_two_templates_sharing_a_slot_name_get_the_same_value() -> None:
    param = TemplateParam(name="region", comment="one of: EMEA, NA")
    left = probe_values([param], now=NOW)
    right = probe_values([param], now=NOW)
    assert left.values == right.values


def test_a_numeric_threshold_is_probed_wide_open() -> None:
    # `WHERE amount > :floor` at zero compares two statements over the whole
    # table. A guessed 1,000 would compare two empty result sets on a small
    # fixture and call that agreement.
    assert probe_values(
        [TemplateParam(name="floor", type=ParamType.NUMBER)], now=NOW
    ).values == {"floor": 0}


# ── staleness ────────────────────────────────────────────────────────────
async def test_a_renamed_column_turns_exactly_the_affected_templates_amber(
    settings: Settings,
) -> None:
    """§3.5's own acceptance test, as a unit test.

    *"Renaming a column on the `aurora` demo turns exactly the affected
    templates amber with a readable reason."* Exactly — the template that never
    named the column is untouched.
    """
    affected = _row(question="revenue for {region}", sql=REGION_SQL,
                    params=[{"name": "region", "type": "string", "comment": ""}])
    untouched = _row(question="total revenue", sql=PLAIN_SQL)
    db = FakeDb([affected, untouched], tables=TABLES_AFTER_RENAME)

    result = await KnowledgeService(db, settings).sweep_staleness(_connection())

    assert result.staled == [affected.id]
    assert affected.status == str(TemplateStatus.STALE)
    assert untouched.status == str(TemplateStatus.ACTIVE)


async def test_the_reason_names_the_object_that_moved(settings: Settings) -> None:
    # "Validation failed" is useless; the guard's own sentence names the column,
    # which is the only part a curator can act on.
    row = _row(question="revenue for {region}", sql=REGION_SQL,
               params=[{"name": "region", "type": "string", "comment": ""}])
    await KnowledgeService(
        FakeDb([row], tables=TABLES_AFTER_RENAME), settings
    ).sweep_staleness(_connection())

    assert "region" in row.status_reason
    assert "re-sync" in row.status_reason.lower()


async def test_a_stale_template_is_kept_never_deleted(settings: Settings) -> None:
    row = _row(question="revenue for {region}", sql=REGION_SQL,
               params=[{"name": "region", "type": "string", "comment": ""}])
    db = FakeDb([row], tables=TABLES_AFTER_RENAME)
    await KnowledgeService(db, settings).sweep_staleness(_connection())

    assert row in db.rows
    assert row.sql == REGION_SQL          # the person's work is intact
    assert row.question == "revenue for {region}"


async def test_a_healed_template_comes_back_on_its_own(settings: Settings) -> None:
    """The transition everybody forgets.

    Without it the first bad sync is permanent, and healing the store means a
    curator opening forty rows and pressing Save on each.
    """
    row = _row(
        question="revenue for {region}", sql=REGION_SQL,
        status=TemplateStatus.STALE, status_reason="column region no longer exists",
        params=[{"name": "region", "type": "string", "comment": ""}],
    )
    result = await KnowledgeService(
        FakeDb([row], tables=TABLES), settings
    ).sweep_staleness(_connection())

    assert result.revived == [row.id]
    assert row.status == str(TemplateStatus.ACTIVE)
    assert row.status_reason == ""


async def test_a_conflicted_template_is_not_touched_by_the_staleness_sweep(
    settings: Settings,
) -> None:
    # A conflict is a disagreement about *meaning* and is not resolved by the
    # schema moving. Overwriting it here would drop the evidence a curator was
    # about to read.
    row = _row(question="total revenue", sql=PLAIN_SQL,
               status=TemplateStatus.CONFLICTED, status_reason="two answers")
    row.conflict_evidence = {"summary": "differs"}
    await KnowledgeService(FakeDb([row]), settings).sweep_staleness(_connection())

    assert row.status == str(TemplateStatus.CONFLICTED)
    assert row.conflict_evidence == {"summary": "differs"}


async def test_an_empty_snapshot_does_not_condemn_the_whole_store(
    settings: Settings,
) -> None:
    """A sync that produced no tables is a broken sync, not a broken store.

    Marking every template stale on it would be the loudest possible wrong
    answer, and the fix — re-sync — would then need a second sweep to undo.
    """
    row = _row(question="total revenue", sql=PLAIN_SQL)
    result = await KnowledgeService(
        FakeDb([row], tables=[]), settings
    ).sweep_staleness(_connection())

    assert result.checked == 0
    assert row.status == str(TemplateStatus.ACTIVE)


async def test_a_stale_templates_reason_is_refreshed_not_left(
    settings: Settings,
) -> None:
    row = _row(
        question="revenue for {region}", sql=REGION_SQL,
        status=TemplateStatus.STALE, status_reason="something from two syncs ago",
        params=[{"name": "region", "type": "string", "comment": ""}],
    )
    await KnowledgeService(
        FakeDb([row], tables=TABLES_AFTER_RENAME), settings
    ).sweep_staleness(_connection())

    assert "two syncs ago" not in row.status_reason
    assert "region" in row.status_reason


# ── withdrawal from the read path ────────────────────────────────────────
def test_stale_and_conflicted_are_both_withdrawn() -> None:
    """`is_matchable` is what the matcher and few-shot both ask.

    Belt and braces with the query's own predicate: a held-out template
    answering its own question measures nothing, and a stale one answers with
    SQL the schema no longer supports. Neither failure is visible from outside.
    """
    for status in (TemplateStatus.STALE, TemplateStatus.CONFLICTED):
        template = _template("total revenue", status=status)
        assert not template.is_matchable
        assert template.is_withdrawn

    assert _template("total revenue").is_matchable
    assert not _template("total revenue").is_withdrawn


def test_the_candidate_query_excludes_stale_conflicted_and_held_out() -> None:
    """The exclusion lives in the query, not in a comment (§1.3).

    Read off the compiled statement rather than off a fake's behaviour, so a
    predicate quietly dropped from the query fails here.
    """
    from sqlalchemy import select

    from app.services.knowledge_service import build_matcher

    statement = select(KnowledgeTemplateRow).where(
        KnowledgeTemplateRow.connection_id == CONNECTION_ID,
        KnowledgeTemplateRow.status == str(TemplateStatus.ACTIVE),
        KnowledgeTemplateRow.role == str(TemplateRole.RETRIEVABLE),
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "status" in compiled and "role" in compiled
    assert build_matcher is not None


# ── the conflict checker ─────────────────────────────────────────────────
class FakeConnector:
    """Answers each statement from a script, keyed by a substring of the SQL."""

    dialect = "postgres"

    def __init__(self, answers: dict[str, tuple[list[str], list[list[Any]]]]) -> None:
        self.answers = answers
        self.ran: list[str] = []
        self.closed = False

    async def execute(self, sql: str, **kwargs: Any) -> Any:
        self.ran.append(sql)
        for needle, (columns, rows) in self.answers.items():
            if needle in sql:
                return _ExecResult(columns, rows)
        raise AssertionError(f"unscripted statement: {sql}")

    async def close(self) -> None:
        self.closed = True


class _ExecResult:
    def __init__(self, columns: list[str], rows: list[list[Any]]) -> None:
        self.columns = columns
        self.rows = rows
        self.row_count = len(rows)
        self.truncated = False


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Swap the connector factory and the secret box; keep everything else.

    `execute_saved_sql` is *not* stubbed: the point of this phase is that the
    checker goes through the guard's own execution path, so the guard, the
    rewriter and the row cap all run for real here.
    """
    def install(connector: FakeConnector) -> None:
        monkeypatch.setattr(
            maintenance, "bind_connector", lambda *_a, **_k: connector
        )
        monkeypatch.setattr(maintenance, "secret_box", lambda *_a, **_k: object())

    return install


async def test_two_templates_that_disagree_are_both_marked(
    settings: Settings, patched: Any
) -> None:
    """Both, not one. Neither is presumed right.

    §4.7 offers *Keep the first / Keep the second / Edit both*, and it can only
    offer that if the system has not already picked a winner.
    """
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    db = FakeDb([left, right])
    patched(FakeConnector({
        "CANCELLED": (["revenue"], [[481_220]]),
        "SELECT": (["revenue"], [[512_940]]),
    }))

    result = await maintenance.detect_conflicts(db, settings, _connection())

    assert set(result.conflicted) == {left.id, right.id}
    assert left.status == right.status == str(TemplateStatus.CONFLICTED)
    assert left.conflicts_with == [right.id]
    assert right.conflicts_with == [left.id]


async def test_the_conflict_carries_the_rows_that_prove_it(
    settings: Settings, patched: Any
) -> None:
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    patched(FakeConnector({
        "CANCELLED": (["revenue"], [[481_220]]),
        "SELECT": (["revenue"], [[512_940]]),
    }))

    await maintenance.detect_conflicts(FakeDb([left, right]), settings, _connection())

    assert left.conflict_evidence["left_rows"] == [["512940"]]
    assert left.conflict_evidence["right_rows"] == [["481220"]]
    # Mirrored, so whichever row the curator opens sees its own answer first.
    assert right.conflict_evidence["left_rows"] == [["481220"]]
    assert right.conflict_evidence["right_rows"] == [["512940"]]


async def test_the_reason_names_the_other_question(
    settings: Settings, patched: Any
) -> None:
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    patched(FakeConnector({
        "CANCELLED": (["revenue"], [[1]]),
        "SELECT": (["revenue"], [[2]]),
    }))

    await maintenance.detect_conflicts(FakeDb([left, right]), settings, _connection())
    assert "revenue by month" in left.status_reason
    assert "monthly revenue" in right.status_reason


async def test_two_templates_that_agree_are_left_alone(
    settings: Settings, patched: Any
) -> None:
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    patched(FakeConnector({
        "CANCELLED": (["revenue"], [[481_220]]),
        "SELECT": (["revenue"], [[481_220]]),
    }))

    result = await maintenance.detect_conflicts(
        FakeDb([left, right]), settings, _connection()
    )

    assert result.conflicted == []
    assert left.status == right.status == str(TemplateStatus.ACTIVE)


async def test_a_conflict_that_no_longer_holds_is_cleared(
    settings: Settings, patched: Any
) -> None:
    """A store that can enter a bad state and never leave it is one nobody
    trusts — the same reasoning that revives a healed stale template."""
    left = _row(question="monthly revenue", sql=PLAIN_SQL,
                status=TemplateStatus.CONFLICTED, status_reason="two answers")
    right = _row(question="revenue by month", sql=CANCELLED_SQL,
                 status=TemplateStatus.CONFLICTED, status_reason="two answers")
    left.conflicts_with, right.conflicts_with = [right.id], [left.id]
    left.conflict_evidence = {"summary": "stale evidence"}
    patched(FakeConnector({
        "CANCELLED": (["revenue"], [[7]]),
        "SELECT": (["revenue"], [[7]]),
    }))

    result = await maintenance.detect_conflicts(
        FakeDb([left, right]), settings, _connection()
    )

    assert set(result.cleared) == {left.id, right.id}
    assert left.status == str(TemplateStatus.ACTIVE)
    assert left.conflicts_with == [] and left.conflict_evidence == {}


async def test_a_pair_that_cannot_be_probed_is_skipped_loudly(
    settings: Settings, patched: Any
) -> None:
    """Skipped, and the log names the slot.

    The same discipline `REJECTED_UNBOUND` follows on the ask path: guessing
    now to avoid a log line later is the wrong trade, and the log is how the
    next parameter that needs a value list gets found.
    """
    left = _row(
        question="revenue for {customer}",
        sql="SELECT SUM(amount) FROM public.orders WHERE region = :customer",
        params=[{"name": "customer", "type": "string", "comment": "the name"}],
    )
    right = _row(
        question="total revenue for {customer}",
        sql="SELECT SUM(amount) FROM public.orders WHERE status = :customer",
        params=[{"name": "customer", "type": "string", "comment": "the name"}],
    )
    connector = FakeConnector({})
    patched(connector)

    result = await maintenance.detect_conflicts(
        FakeDb([left, right]), settings, _connection()
    )

    assert result.pairs_considered == 1 and result.pairs_executed == 0
    assert result.conflicted == []
    assert connector.ran == []            # nothing ran against the database
    assert "customer" in result.skipped[0]


async def test_a_statement_that_will_not_run_is_not_evidence_of_a_conflict(
    settings: Settings, patched: Any
) -> None:
    # An error is a staleness question, and the sweep has already had its say.
    # Inventing a conflict from it would put two rows in amber for a reason the
    # evidence pane could not show.
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql="SELECT SUM(amount) FROM public.nope")
    patched(FakeConnector({"SELECT": (["revenue"], [[1]])}))

    result = await maintenance.detect_conflicts(
        FakeDb([left, right]), settings, _connection()
    )

    assert result.conflicted == []
    assert left.status == str(TemplateStatus.ACTIVE)
    assert result.skipped and "did not run" in result.skipped[0]


async def test_held_out_templates_are_never_compared(
    settings: Settings, patched: Any
) -> None:
    # §1.3: a row that exists to *measure* is not part of a fact about this
    # product's answers, and running it here would spend the customer's
    # database to reach a conclusion nobody can act on.
    left = _row(question="monthly revenue", sql=PLAIN_SQL, role=TemplateRole.HELD_OUT)
    right = _row(question="revenue by month", sql=CANCELLED_SQL,
                 role=TemplateRole.HELD_OUT)
    connector = FakeConnector({})
    patched(connector)

    result = await maintenance.detect_conflicts(
        FakeDb([left, right]), settings, _connection()
    )

    assert result.pairs_considered == 0
    assert connector.ran == []


async def test_the_connector_is_closed_on_every_exit(
    settings: Settings, patched: Any
) -> None:
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    connector = FakeConnector({
        "CANCELLED": (["revenue"], [[1]]), "SELECT": (["revenue"], [[1]]),
    })
    patched(connector)

    await maintenance.detect_conflicts(FakeDb([left, right]), settings, _connection())
    assert connector.closed


async def test_the_checker_runs_read_only_capped_statements(
    settings: Settings, patched: Any
) -> None:
    """Through `execute_saved_sql`, so the guard's rewriter applies.

    The row cap is `COMPARE_ROW_CAP` and not `connections.max_rows`: a conflict
    shows itself in the first page, and pulling ten thousand rows twice to
    prove it spends the customer's database to make a point already made.
    """
    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    connector = FakeConnector({
        "CANCELLED": (["revenue"], [[1]]), "SELECT": (["revenue"], [[1]]),
    })
    patched(connector)

    await maintenance.detect_conflicts(FakeDb([left, right]), settings, _connection())

    assert len(connector.ran) == 2
    assert all("LIMIT" in sql.upper() for sql in connector.ran)
    assert all(str(maintenance.COMPARE_ROW_CAP) in sql for sql in connector.ran)


# ── the off switch ───────────────────────────────────────────────────────
async def test_the_off_switch_stops_the_checker_before_a_connector_opens(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan's own risk register: *"it must be switchable off per
    connection"* — and checked before, not after, a connection is opened."""
    def explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a connector must not be opened when checks are off")

    monkeypatch.setattr(maintenance, "bind_connector", explode)

    left = _row(question="monthly revenue", sql=PLAIN_SQL)
    right = _row(question="revenue by month", sql=CANCELLED_SQL)
    result = await maintenance.run_maintenance(
        FakeDb([left, right]), settings, _connection(conflict_checks_enabled=False)
    )

    assert result.conflicts_checked is False
    assert result.conflicts.conflicted == []


async def test_the_off_switch_does_not_stop_the_staleness_sweep(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The two halves have different costs: one is a parse, the other executes
    # SQL on a customer's database. Only the second is what anyone objects to.
    monkeypatch.setattr(
        maintenance, "bind_connector",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no connector")),
    )
    row = _row(question="revenue for {region}", sql=REGION_SQL,
               params=[{"name": "region", "type": "string", "comment": ""}])

    result = await maintenance.run_maintenance(
        FakeDb([row], tables=TABLES_AFTER_RENAME),
        settings,
        _connection(conflict_checks_enabled=False),
    )

    assert result.staleness.staled == [row.id]
    assert row.status == str(TemplateStatus.STALE)


async def test_staleness_runs_before_conflicts(
    settings: Settings, patched: Any
) -> None:
    """Order matters: a template the schema broke cannot run at all, and
    marking it `CONFLICTED` would describe the wrong problem."""
    broken = _row(question="revenue for {region}", sql=REGION_SQL,
                  params=[{"name": "region", "type": "string", "comment": ""}])
    other = _row(question="revenue by {region}",
                 sql="SELECT SUM(amount) FROM public.orders WHERE region = :region",
                 params=[{"name": "region", "type": "string",
                          "comment": "one of: EMEA"}])
    connector = FakeConnector({})
    patched(connector)

    result = await maintenance.run_maintenance(
        FakeDb([broken, other], tables=TABLES_AFTER_RENAME), settings, _connection()
    )

    assert broken.status == str(TemplateStatus.STALE)
    assert other.status == str(TemplateStatus.STALE)
    # Both withdrawn by the sweep, so the conflict pass has nothing to compare
    # and never opened a connector.
    assert result.conflicts.pairs_considered == 0
    assert connector.ran == []


# ── health ───────────────────────────────────────────────────────────────
async def test_health_counts_stale_conflicted_and_unused(
    settings: Settings,
) -> None:
    old = utcnow() - timedelta(days=UNUSED_AFTER_DAYS + 1)
    stale = _row(question="a", sql=PLAIN_SQL, status=TemplateStatus.STALE)
    conflicted = _row(question="b", sql=PLAIN_SQL, status=TemplateStatus.CONFLICTED)
    unused = _row(question="c", sql=PLAIN_SQL, created_at=old, hit_count=0)
    used = _row(question="d", sql=PLAIN_SQL, created_at=old, hit_count=9)

    health = await KnowledgeService(
        FakeDb([stale, conflicted, unused, used]), settings
    ).health(_connection())

    assert health.stale == [stale.id]
    assert health.conflicted == [conflicted.id]
    assert health.unused == [unused.id]
    assert health.total == 4


async def test_a_template_written_this_morning_is_not_unused(
    settings: Settings,
) -> None:
    # It has not gone unused; it has not had a chance yet. Accusing it would
    # make the number meaningless on the day anybody starts curating.
    fresh = _row(question="a", sql=PLAIN_SQL, created_at=utcnow(), hit_count=0)
    health = await KnowledgeService(FakeDb([fresh]), settings).health(_connection())
    assert health.unused == []


async def test_pruning_is_surfaced_never_enforced(settings: Settings) -> None:
    """Genie caps instructions at 100 per agent; this shows a line instead.

    A template written for a question asked once a year is not waste, so
    nothing here archives, deletes or withdraws anything.
    """
    old = utcnow() - timedelta(days=UNUSED_AFTER_DAYS + 30)
    row = _row(question="the annual audit figure", sql=PLAIN_SQL,
               created_at=old, hit_count=0)
    db = FakeDb([row])

    health = await KnowledgeService(db, settings).health(_connection())

    assert health.unused == [row.id]
    assert row.status == str(TemplateStatus.ACTIVE)   # still answering questions
    assert row in db.rows


# ── the evidence shape the UI is built against ───────────────────────────
def test_an_empty_divergence_serialises_to_empty_lists() -> None:
    assert Divergence().as_dict() == {
        "summary": "", "left_columns": [], "right_columns": [],
        "left_rows": [], "right_rows": [],
    }
