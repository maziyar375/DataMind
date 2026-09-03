"""The customer's own accuracy number, and the three rules that keep it honest.

Phase 6. A number a customer can quote is worth more than a number a developer
can, and it is also far easier to make dishonest — so most of this file is
about the ways it could flatter:

1. **A template is retrievable or benchmarkable, never both.** Creating a set
   moves every member's `role` off `RETRIEVABLE`, and the worker filters on
   `role` again when it loads them. A held-out question answered from its own
   stored SQL measures the store's ability to hold a string.
2. **A fixed fraction is `HELD_OUT` at creation**, deterministically, and that
   is the only number worth putting in front of anyone.
3. **The split is reported.** Accuracy on questions answered *from* a template
   and accuracy on questions answered *without* one are different numbers, and
   only the second moves for a reason. Genie shows one number; this shows two
   and says which to believe.

And two more this file insists on, because both are how an accuracy quietly
becomes a lie:

* **Nothing that did not run is in a denominator.** A member whose parameters
  could not be probed, or whose stored answer failed to execute, is counted in
  `total` and in neither accuracy.
* **No LLM judge.** Every label comes from `app/knowledge/compare.py`.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import ValidationError
from app.infra.db.models import (
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkSet,
    DatabaseConnection,
    KnowledgeTemplateRow,
)
from app.knowledge import TemplateRole, TemplateStatus, normalize_question
from app.services.benchmark_service import (
    DEFAULT_HELD_OUT_FRACTION,
    MIN_SET_SIZE,
    OUTCOME_ERROR,
    OUTCOME_MATCH,
    OUTCOME_MISMATCH,
    OUTCOME_NOT_PROBED,
    BenchmarkService,
    Score,
    held_out_split,
)

CONNECTION_ID = uuid4()
OWNER = uuid4()


def _connection() -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID, owner_id=OWNER, name="aurora",
        database_type="postgres", host="h", port=5432,
        database_name="aurora", username="ro", encrypted_password="x",
        max_rows=1000, statement_timeout_ms=30_000, disclosure_policy="SAMPLE",
        conflict_checks_enabled=True, knowledge_examples_enabled=False,
    )


def _template(
    question: str,
    *,
    role: TemplateRole = TemplateRole.RETRIEVABLE,
    status: TemplateStatus = TemplateStatus.ACTIVE,
    template_id: UUID | None = None,
) -> KnowledgeTemplateRow:
    now = utcnow()
    return KnowledgeTemplateRow(
        id=template_id or uuid4(), connection_id=CONNECTION_ID,
        question=question, question_normalized=normalize_question(question),
        sql="SELECT SUM(amount) FROM public.orders", params=[], note="",
        source="MANUAL", literal_provenance="HUMAN_AUTHORED",
        role=str(role), status=str(status), status_reason="",
        schema_version=1, referenced_tables=["public.orders"],
        conflicts_with=[], conflict_evidence={}, hit_count=0,
        created_at=now, updated_at=now,
    )


def _result(
    *, role: TemplateRole, outcome: str, from_template: bool = False
) -> BenchmarkResult:
    return BenchmarkResult(
        id=uuid4(), run_id=uuid4(), template_id=uuid4(),
        question="q", gold_sql="SELECT 1", candidate_sql="SELECT 1",
        role=str(role), outcome=outcome, from_template=from_template,
        duration_ms=1, failure_reason="",
    )


class FakeDb:
    """Enough of an `AsyncSession` for the benchmark service."""

    def __init__(self, templates: list[KnowledgeTemplateRow]) -> None:
        self.templates = templates
        self.sets: list[BenchmarkSet] = []
        self.runs: list[BenchmarkRun] = []
        self.deleted: list[Any] = []
        self.flushes = 0

    async def execute(self, statement: Any) -> Any:
        entity = statement.column_descriptions[0].get("entity")
        if entity is KnowledgeTemplateRow:
            return _Result(self._templates(statement))
        if entity is BenchmarkSet:
            return _Result(list(self.sets))
        if entity is BenchmarkRun:
            return _Result(self._runs(statement))
        if entity is BenchmarkResult:
            return _Result([])
        raise AssertionError(f"unexpected query: {entity}")

    def _templates(self, statement: Any) -> list[KnowledgeTemplateRow]:
        params = statement.compile().params
        rows = list(self.templates)
        for key, value in params.items():
            wanted = value if isinstance(value, (list, tuple)) else (value,)
            if key.startswith("status"):
                rows = [r for r in rows if r.status in wanted]
            elif key.startswith("role"):
                rows = [r for r in rows if r.role in wanted]
            elif key.startswith("id_"):
                rows = [r for r in rows if r.id in wanted]
        return rows

    def _runs(self, statement: Any) -> list[BenchmarkRun]:
        params = statement.compile().params
        rows = list(self.runs)
        wanted = [v for k, v in params.items() if k.startswith("status")]
        for value in wanted:
            if isinstance(value, (list, tuple)):
                rows = [r for r in rows if r.status in value]
        return rows

    def add(self, obj: Any) -> None:
        if isinstance(obj, BenchmarkSet):
            obj.created_at = obj.created_at or utcnow()
            obj.updated_at = obj.updated_at or utcnow()
            self.sets.append(obj)
        elif isinstance(obj, BenchmarkRun):
            obj.created_at = obj.created_at or utcnow()
            self.runs.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)
        if obj in self.sets:
            self.sets.remove(obj)

    async def get(self, model: Any, key: UUID) -> Any:
        pool = {
            BenchmarkSet: self.sets,
            BenchmarkRun: self.runs,
            KnowledgeTemplateRow: self.templates,
        }.get(model, [])
        return next((r for r in pool if r.id == key), None)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> Any:
        return _Scalars(self._rows)


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ── rule 2: the split, and that it is deterministic ──────────────────────
def test_the_held_out_split_is_deterministic() -> None:
    """Two sets that held out different questions are two different
    instruments, and a customer comparing their numbers would never find out."""
    ids = [uuid4() for _ in range(10)]
    assert held_out_split(ids, 0.4) == held_out_split(ids, 0.4)
    # And independent of the order the ids arrive in, because the set's own
    # membership list is what it is re-derived from years later.
    assert held_out_split(ids, 0.4) == held_out_split(list(reversed(ids)), 0.4)


def test_the_split_holds_back_roughly_the_fraction_asked_for() -> None:
    ids = [uuid4() for _ in range(20)]
    held = held_out_split(ids, 0.5)
    assert 8 <= len(held) <= 12


def test_a_zero_fraction_holds_nothing_back() -> None:
    assert held_out_split([uuid4() for _ in range(5)], 0.0) == set()


# ── rule 1: retrievable or benchmarkable, never both ─────────────────────
async def test_creating_a_set_withdraws_every_member_from_answering(
    settings: Settings,
) -> None:
    """The point of the phase, not a side effect.

    §1.3's rule is enforced in the query that builds the ask path's candidate
    set, which filters on `role == RETRIEVABLE`. The only way to keep a
    benchmark honest is for its members to stop being retrievable.
    """
    rows = [_template(f"question {i}") for i in range(6)]
    db = FakeDb(rows)

    await BenchmarkService(db, settings).create_set(
        _connection(), actor_id=OWNER, name="Q3",
        template_ids=[r.id for r in rows],
    )

    assert all(r.role != str(TemplateRole.RETRIEVABLE) for r in rows)
    assert {r.role for r in rows} == {
        str(TemplateRole.HELD_OUT), str(TemplateRole.BENCHMARK_ONLY)
    }


async def test_the_held_out_members_are_the_ones_the_split_named(
    settings: Settings,
) -> None:
    rows = [_template(f"question {i}") for i in range(8)]
    ids = [r.id for r in rows]
    db = FakeDb(rows)

    await BenchmarkService(db, settings).create_set(
        _connection(), actor_id=OWNER, name="Q3", template_ids=ids,
    )

    expected = held_out_split(ids, DEFAULT_HELD_OUT_FRACTION)
    assert {r.id for r in rows if r.role == str(TemplateRole.HELD_OUT)} == expected


async def test_deleting_a_set_gives_the_questions_back(settings: Settings) -> None:
    """The one place in this loop where DELETE really deletes.

    A set is an instrument, not somebody's knowledge — and the knowledge it was
    built from comes back intact and `RETRIEVABLE`.
    """
    rows = [_template(f"question {i}") for i in range(5)]
    db = FakeDb(rows)
    service = BenchmarkService(db, settings)
    connection = _connection()

    set_row = await service.create_set(
        connection, actor_id=OWNER, name="Q3", template_ids=[r.id for r in rows],
    )
    await service.release(connection, set_row.id)

    assert all(r.role == str(TemplateRole.RETRIEVABLE) for r in rows)
    assert all(r.question for r in rows)          # nothing was destroyed
    assert set_row in db.deleted


async def test_release_does_not_overrule_the_staleness_sweep(
    settings: Settings,
) -> None:
    # A member archived or gone stale while the set existed keeps its status:
    # this restores a *role*, and it is not the place to overrule a curator.
    rows = [_template(f"question {i}") for i in range(5)]
    db = FakeDb(rows)
    service = BenchmarkService(db, settings)
    connection = _connection()
    set_row = await service.create_set(
        connection, actor_id=OWNER, name="Q3", template_ids=[r.id for r in rows],
    )
    rows[0].status = str(TemplateStatus.STALE)

    await service.release(connection, set_row.id)
    assert rows[0].status == str(TemplateStatus.STALE)
    assert rows[0].role == str(TemplateRole.RETRIEVABLE)


async def test_the_candidate_query_excludes_stale_conflicted_and_committed(
    settings: Settings,
) -> None:
    """§1.3 in the query, not in a comment.

    A benchmark whose stored answer no longer runs measures the schema rather
    than the product, and a template already committed to a set cannot be held
    out of one instrument and answering questions for another.
    """
    live = _template("live")
    rows = [
        live,
        _template("stale", status=TemplateStatus.STALE),
        _template("conflicted", status=TemplateStatus.CONFLICTED),
        _template("archived", status=TemplateStatus.ARCHIVED),
        _template("already measuring", role=TemplateRole.HELD_OUT),
        _template("benchmark only", role=TemplateRole.BENCHMARK_ONLY),
    ]

    found = await BenchmarkService(FakeDb(rows), settings).candidates(_connection())
    assert [r.question for r in found] == ["live"]


# ── a set has to be big enough to mean something ─────────────────────────
async def test_a_tiny_set_is_refused(settings: Settings) -> None:
    """Fewer than four questions is noise with a percentage sign on it — and
    100%-on-three is exactly the number somebody would quote."""
    rows = [_template(f"question {i}") for i in range(MIN_SET_SIZE - 1)]
    with pytest.raises(ValidationError):
        await BenchmarkService(FakeDb(rows), settings).create_set(
            _connection(), actor_id=OWNER, name="tiny",
            template_ids=[r.id for r in rows],
        )
    # And nothing was withdrawn on the way to the refusal.
    assert all(r.role == str(TemplateRole.RETRIEVABLE) for r in rows)


async def test_a_set_needs_a_name(settings: Settings) -> None:
    rows = [_template(f"question {i}") for i in range(5)]
    with pytest.raises(ValidationError):
        await BenchmarkService(FakeDb(rows), settings).create_set(
            _connection(), actor_id=OWNER, name="   ",
            template_ids=[r.id for r in rows],
        )


async def test_a_template_from_another_connection_is_refused(
    settings: Settings,
) -> None:
    rows = [_template(f"question {i}") for i in range(5)]
    with pytest.raises(ValidationError):
        await BenchmarkService(FakeDb(rows), settings).create_set(
            _connection(), actor_id=OWNER, name="Q3",
            template_ids=[*[r.id for r in rows], uuid4()],
        )


# ── rule 3: two numbers, and what is in each denominator ─────────────────
def test_the_two_numbers_are_computed_from_different_populations() -> None:
    """§3.7 rule 3, verbatim.

    The held-out number counts members whose own SQL was never retrievable.
    The taught number counts results the run actually answered *from* a
    template — the observed fact, not a label — which is the number that goes
    up for the wrong reasons.
    """
    score = BenchmarkService.score([
        _result(role=TemplateRole.HELD_OUT, outcome=OUTCOME_MATCH),
        _result(role=TemplateRole.HELD_OUT, outcome=OUTCOME_MISMATCH),
        _result(role=TemplateRole.BENCHMARK_ONLY, outcome=OUTCOME_MATCH,
                from_template=True),
        _result(role=TemplateRole.BENCHMARK_ONLY, outcome=OUTCOME_MATCH,
                from_template=True),
    ])

    assert score.held_out_total == 2 and score.held_out_matched == 1
    assert score.taught_total == 2 and score.taught_matched == 2
    assert score.held_out_accuracy == 0.5
    assert score.taught_accuracy == 1.0


def test_a_question_that_did_not_run_is_in_neither_denominator() -> None:
    """An accuracy computed over the questions that happened to run is the
    classic silent lie, and it always flatters."""
    score = BenchmarkService.score([
        _result(role=TemplateRole.HELD_OUT, outcome=OUTCOME_MATCH),
        _result(role=TemplateRole.HELD_OUT, outcome=OUTCOME_NOT_PROBED),
        _result(role=TemplateRole.HELD_OUT, outcome=OUTCOME_ERROR),
    ])
    assert score.held_out_total == 1
    assert score.held_out_accuracy == 1.0


def test_no_held_out_question_means_no_held_out_accuracy() -> None:
    """`None`, not `0.0`.

    A run that scored no held-out question has *no* held-out accuracy, and
    printing 0% for it would be the loudest possible wrong answer.
    """
    score = BenchmarkService.score([
        _result(role=TemplateRole.BENCHMARK_ONLY, outcome=OUTCOME_MATCH,
                from_template=True),
    ])
    assert score.held_out_accuracy is None
    assert score.taught_accuracy == 1.0


def test_an_empty_run_has_neither_number() -> None:
    assert Score().held_out_accuracy is None
    assert Score().taught_accuracy is None


def test_a_held_out_question_answered_from_a_neighbour_counts_in_both() -> None:
    """Deliberate, and the reason `from_template` is observed rather than
    assigned.

    A held-out question's *own* row is never retrievable, but a neighbouring
    template can still match it — which is a real thing that happens on the ask
    path. It belongs in the held-out denominator because that is what its role
    says, and in the taught one because that is what actually happened.
    """
    score = BenchmarkService.score([
        _result(role=TemplateRole.HELD_OUT, outcome=OUTCOME_MATCH,
                from_template=True),
    ])
    assert score.held_out_total == 1
    assert score.taught_total == 1


# ── the tables are separate, and that is the whole point ─────────────────
def test_the_benchmark_tables_are_not_the_eval_tables() -> None:
    """MVP2 Part 5's meta-rule, asserted rather than remembered.

    *"The customer-facing instrument and the frozen developer suite must stay
    architecturally separate, or the two will contaminate each other within a
    month."* Sharing a table is how that starts.
    """
    from app.infra.db.models import EvalResult, EvalRun

    assert BenchmarkRun.__tablename__ == "benchmark_runs"
    assert BenchmarkResult.__tablename__ == "benchmark_results"
    assert EvalRun.__tablename__ == "eval_runs"
    assert EvalResult.__tablename__ == "eval_results"


def test_the_worker_does_not_import_the_eval_harness() -> None:
    """The contract, on the parse.

    `app.eval` is offline-only; this is the customer-facing instrument. They
    share a vocabulary and a comparator — one implementation, in
    `app.knowledge.compare` — and they share no import and no table.
    """
    import ast
    import pathlib

    import app.workers.benchmark as worker

    tree = ast.parse(pathlib.Path(worker.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("app.eval") for name in imported)
    assert "app.knowledge" in imported


def test_the_labels_come_from_the_comparator_and_not_from_a_model() -> None:
    """No LLM judge. Fabric fell back to one and gets true/false/unclear.

    Asserted on the parse rather than by reading: the worker may import a
    gateway to *run the pipeline*, but nothing in it may ask a model to score
    an answer, and the comparator it uses is the shared one.
    """
    import app.workers.benchmark as worker
    from app.knowledge.compare import result_sets_match

    assert worker.result_sets_match is result_sets_match


# ── the worker's own role filter ─────────────────────────────────────────
async def test_a_member_that_drifted_back_to_retrievable_is_not_scored(
    settings: Settings,
) -> None:
    """Excluded, not silently scored.

    A member a curator edited back to `RETRIEVABLE` can be answered from its
    own stored SQL, so scoring it would put a number in front of a customer
    that measures nothing. Excluding it is visible in `total`; scoring it would
    not be visible at all.
    """
    from app.workers.benchmark import _members

    kept = _template("kept", role=TemplateRole.HELD_OUT)
    drifted = _template("drifted", role=TemplateRole.RETRIEVABLE)
    also = _template("also kept", role=TemplateRole.BENCHMARK_ONLY)
    db = FakeDb([kept, drifted, also])
    set_row = BenchmarkSet(
        id=uuid4(), connection_id=CONNECTION_ID, name="Q3", description="",
        template_ids=[kept.id, drifted.id, also.id], held_out_fraction=0.4,
    )

    found = await _members(db, set_row)
    assert [r.question for r in found] == ["kept", "also kept"]


async def test_members_come_back_in_set_order(settings: Settings) -> None:
    # A customer reading two runs side by side should see the same questions in
    # the same places.
    from app.workers.benchmark import _members

    rows = [
        _template(f"q{i}", role=TemplateRole.BENCHMARK_ONLY) for i in range(4)
    ]
    ids = [rows[2].id, rows[0].id, rows[3].id, rows[1].id]
    set_row = BenchmarkSet(
        id=uuid4(), connection_id=CONNECTION_ID, name="Q3", description="",
        template_ids=ids, held_out_fraction=0.4,
    )
    found = await _members(FakeDb(rows), set_row)
    assert [r.id for r in found] == ids


# ── queueing ─────────────────────────────────────────────────────────────
async def test_a_second_run_is_refused_while_one_is_in_flight(
    settings: Settings,
) -> None:
    from app.core.errors import ConflictError

    rows = [_template(f"question {i}") for i in range(5)]
    db = FakeDb(rows)
    service = BenchmarkService(db, settings)
    connection = _connection()
    set_row = await service.create_set(
        connection, actor_id=OWNER, name="Q3", template_ids=[r.id for r in rows],
    )

    await service.queue_run(
        connection, set_row, actor_id=OWNER, llm_config_id=uuid4()
    )
    with pytest.raises(ConflictError):
        await service.queue_run(
            connection, set_row, actor_id=OWNER, llm_config_id=uuid4()
        )


async def test_a_queued_run_knows_how_many_questions_it_owes(
    settings: Settings,
) -> None:
    rows = [_template(f"question {i}") for i in range(7)]
    db = FakeDb(rows)
    service = BenchmarkService(db, settings)
    connection = _connection()
    set_row = await service.create_set(
        connection, actor_id=OWNER, name="Q3", template_ids=[r.id for r in rows],
    )

    run = await service.queue_run(
        connection, set_row, actor_id=OWNER, llm_config_id=uuid4()
    )
    assert run.total == 7 and run.status == "QUEUED"
