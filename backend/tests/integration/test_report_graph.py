"""Two entries, one graph — and the differences that must survive the merge.

Phase 3 of [docs/langgraph-migration.md](../../../docs/langgraph-migration.md)
collapsed `_generate` and `_retry` into one compiled graph entered two ways.
`tests/integration/test_report_runs.py` is the equivalence proof: 43 tests that
drove the two hand-rolled drivers and now drive the graph, **unmodified**.

This file tests what that proof cannot. A merge of two drivers fails in one of
two directions, and the passing suite only rules out the first:

1. something the two shared stops working — caught there;
2. something the two deliberately did **differently** quietly becomes the same
   — which every test above would keep passing through, because each one drives
   one entry at a time.

The migration record names the two that matter, so they are named here too: a
retry reads the *whole* document as `established`, and a retry does **not**
rewrite the executive summary. Both are about not destroying writing.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from app.domain.value_objects import ReportRunStatus
from app.services import query_service
from app.workers import report as worker
from app.workers.report_graph import (
    CHECK_DISCLOSURE,
    CLEAR_SECTION,
    EXECUTE_BLOCKS,
    FINISH,
    NARRATE_SECTION,
    REPORT_GRAPH,
    RESOLVE_OUTLINE,
    SUMMARISE,
    WRITE_RESULTS,
    run_generation,
)
from tests.integration.test_report_runs import (
    OTHER_SECTION_ID,
    PROSE,
    SECTION_ID,
    SUMMARY_SECTION_ID,
    FakeConnector,
    FakeGateway,
    _connection,
    _readable,
    _retry,
    _section,
    _settings,
    _summary_section,
)
from tests.integration.test_report_runs import _generate as _run_generation

END = "__end__"
START = "__start__"

#: A third normal section, so a document can be longer than one wave.
THIRD_SECTION_ID = UUID("00000000-0000-0000-0000-0000000000d3")


def edges() -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in REPORT_GRAPH.get_graph().edges}


# The same two fixtures `test_report_runs.py` uses, for the same reason:
# nothing here dials a network, and narration is part of every run. Fixtures do
# not travel with an import, so they are declared rather than borrowed.
@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> FakeConnector:
    fake = FakeConnector()
    monkeypatch.setattr(query_service, "bind_connector", lambda *a, **k: fake)
    return fake


@pytest.fixture(autouse=True)
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    fake = FakeGateway()
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _cls, _s: fake)
    )
    return fake


# ── the two things that must keep differing ──────────────────────────────
async def test_a_retry_reads_the_whole_document_not_just_the_prefix(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """The first pass sees only what came before it; a retry sees everything.

    Section two, written first time round, is told nothing about section three
    because section three does not exist yet. Retried, it is told about it —
    that is the point of rewriting a paragraph *into* a document rather than
    into a gap, and it is a difference a merge would erase by handing both
    entries the same `established`.
    """
    db = _readable()
    await _run_generation(db)

    # The first section is written into an empty document: `established` is the
    # prose written *so far*, and nothing has been. (Its heading list names the
    # other sections — that is `other_headings`, a different argument, and it
    # carries no prose.)
    assert PROSE not in gateway.prompts[0]

    # Give the section written *after* it something only it could say.
    later = next(r for r in db.prose if r.section_id == OTHER_SECTION_ID)
    later.prose = "بخش سوم این را نوشت."

    gateway.calls.clear()  # `prompts` is a derived property; `calls` is the state
    await _retry(db, SECTION_ID)

    # Retried, it reads the paragraph that did not exist when it was first
    # written — which is the whole difference between the two entries.
    assert "بخش سوم این را نوشت." in gateway.prompts[-1]


async def test_a_retry_is_not_told_the_summary_that_already_contains_it(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """The whole document *minus* the summary.

    Handing a section the executive summary would be circular: the summary
    already states this section's own finding, so the section would dutifully
    avoid restating it and write around the very thing it exists to say.
    """
    db = _readable()
    await _run_generation(db)
    summary_row = next(r for r in db.prose if r.section_id == SUMMARY_SECTION_ID)
    summary_row.prose = "این خلاصه نباید به بخش بازنویسی‌شده داده شود."

    gateway.calls.clear()  # `prompts` is a derived property; `calls` is the state
    await _retry(db, SECTION_ID)

    assert "این خلاصه نباید" not in gateway.prompts[-1]


async def test_retrying_a_section_leaves_the_executive_summary_alone(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """A summary is a paragraph the user may have edited by hand.

    Silently rewriting it because a section below it was retried destroys
    writing, so a retry never touches it. A summary that *should* reflect the
    retry is one click away — the summary section can be retried on its own,
    which is why `write_results` routes to `summarise` for it.
    """
    db = _readable()
    await _run_generation(db)
    summary_row = next(r for r in db.prose if r.section_id == SUMMARY_SECTION_ID)
    summary_row.edited_prose = "خلاصه‌ای که کاربر خودش نوشته است."
    summary_id = summary_row.id

    await _retry(db, SECTION_ID)

    survivor = next(r for r in db.prose if r.section_id == SUMMARY_SECTION_ID)
    # The same row object, with the user's words still on it.
    assert survivor.id == summary_id
    assert survivor.edited_prose == "خلاصه‌ای که کاربر خودش نوشته است."
    assert len([r for r in db.prose if r.section_id == SUMMARY_SECTION_ID]) == 1


async def test_the_summary_section_can_still_be_retried_on_its_own(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """The branch that exists precisely because the rule above exists."""
    db = _readable()
    await _run_generation(db)
    before = next(r for r in db.prose if r.section_id == SUMMARY_SECTION_ID).id

    await _retry(db, SUMMARY_SECTION_ID)

    after = [r for r in db.prose if r.section_id == SUMMARY_SECTION_ID]
    assert len(after) == 1 and after[0].id != before


async def test_the_snapshots_a_document_stays_readable_by_survive_both_entries(
    connector: FakeConnector, gateway: FakeGateway
) -> None:
    """`heading_snapshot` and `title_snapshot` are copied onto every result row.

    They are what keeps a historical document readable after the section it
    came from is renamed or deleted, so they are worth asserting on their own
    rather than trusting them to ride along: a retry writes *new* rows, and a
    retry that dropped them would silently un-caption half a document.
    """
    db = _readable()
    await _run_generation(db)

    def captions() -> set[tuple[str, str]]:
        return {(r.heading_snapshot, r.title_snapshot) for r in db.results}

    first_pass = captions()
    assert first_pass and all(heading for heading, _title in first_pass)

    await _retry(db, SECTION_ID)

    assert captions() == first_pass


# ── cancellation is about not losing what was paid for ───────────────────
async def test_a_cancel_between_the_queries_and_their_rows_is_not_honoured(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector, gateway: FakeGateway
) -> None:
    """`execute_blocks` and `write_results` are one unit, and must stay one.

    The flag is routinely set *while the queries are in flight*, so a graph
    that checked cancellation before writing the rows down would discard
    results the customer's database had already been made to produce. This is
    the regression the equivalence suite caught when the check was naively put
    in front of every node, and it is worth its own test because the next
    person to add a node here will have to make the same decision.
    """
    cancelled = asyncio.Event()
    real = worker.execute_many

    async def then_cancel(*args: Any, **kwargs: Any) -> dict:
        results = await real(*args, **kwargs)
        cancelled.set()
        return results

    monkeypatch.setattr(worker, "execute_many", then_cancel)
    db = _readable()

    await _run_generation(db, cancelled)

    assert db.run is not None and db.run.status == ReportRunStatus.CANCELLED
    # Paid for, therefore kept — and no paragraph was written over them.
    assert len(db.results) == 2
    assert db.prose == []


# ── resume: the payoff, and the thing that needs no checkpoint ───────────
async def _crash_after_first_section(
    monkeypatch: pytest.MonkeyPatch, db: Any
) -> None:
    """Generate, but die the way a killed process dies: mid-narration.

    Driven through `run_generation` rather than `generate_run` on purpose. The
    facade's `except Exception` is what turns a *crash* into a FAILED run; a
    process that is killed never reaches it. So the exception is allowed to
    escape, which leaves exactly what `kill -9` leaves: the rows that were
    committed, and a run row still claiming to be RUNNING.
    """
    real = worker._narrate
    calls = {"n": 0}

    async def narrate(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("the process was killed")
        return await real(*args, **kwargs)

    monkeypatch.setattr(worker, "_narrate", narrate)
    with pytest.raises(RuntimeError, match="killed"):
        await run_generation(db, _settings(), db.run, asyncio.Event())
    # Restore just this patch — `monkeypatch.undo()` would also revert the
    # autouse gateway fixture and let the resume dial a real provider.
    monkeypatch.setattr(worker, "_narrate", real)


async def test_a_run_killed_mid_report_resumes_from_the_last_written_section(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector, gateway: FakeGateway
) -> None:
    """The Phase 4 exit criterion, minus the actual `kill -9`.

    Kill the process mid-report, start it again, and the run finishes from
    where it stopped instead of being swept to `FAILED`. What makes that work
    is not a checkpoint — it is that the rows already written *are* the
    progress, so resuming is a matter of reading them.
    """
    db = _readable()
    await _crash_after_first_section(monkeypatch, db)

    # What a killed process leaves: one paragraph, both query results, and a
    # run row still claiming to be RUNNING.
    assert len(db.prose) == 1
    survivor = db.prose[0].heading_snapshot
    assert len(db.results) == 2

    # Startup finds it and hands it back to the executor.
    db.run.status = ReportRunStatus.RUNNING
    await worker.resume_run(db, _settings(), db.run.id, asyncio.Event())

    assert db.run.status == ReportRunStatus.SUCCEEDED
    headings = [row.heading_snapshot for row in db.prose]
    assert sorted(headings) == sorted(["روند درآمد", "محصولات", "خلاصه مدیریتی"])
    # Written exactly once each: the survivor was not rewritten.
    assert len(headings) == len(set(headings))
    assert headings.count(survivor) == 1


async def test_a_resume_does_not_pay_for_the_work_that_survived(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector, gateway: FakeGateway
) -> None:
    """The whole reason to resume rather than regenerate.

    A report run is minutes of provider and database latency. Re-running the
    sections that already finished would charge the user twice for them, which
    is what failing the run and asking them to "generate again" used to do.
    """
    db = _readable()
    await _crash_after_first_section(monkeypatch, db)

    connector.calls.clear()
    gateway.calls.clear()  # `prompts` is a derived property; `calls` is the state
    db.run.status = ReportRunStatus.RUNNING
    await worker.resume_run(db, _settings(), db.run.id, asyncio.Event())

    # Two sections and a summary in the outline; one section survived, so the
    # resume writes the other section and the summary. Two calls, not three.
    assert len(gateway.prompts) == 2
    # And the queries that already ran are not re-run against the customer's
    # database either.
    assert connector.calls == []


async def test_resuming_twice_is_safe(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Idempotent by construction, which a checkpoint would not have been.

    Everything a resume skips, it skips because the row exists — so a resume
    that itself dies simply resumes again. There is no window in which the
    progress record and the document disagree, because they are the same thing.
    """
    db = _readable()
    await _crash_after_first_section(monkeypatch, db)
    db.run.status = ReportRunStatus.RUNNING
    await worker.resume_run(db, _settings(), db.run.id, asyncio.Event())

    rows_after_first = {(r.section_id, r.position) for r in db.prose}
    gateway.calls.clear()  # `prompts` is a derived property; `calls` is the state

    db.run.status = ReportRunStatus.RUNNING
    await worker.resume_run(db, _settings(), db.run.id, asyncio.Event())

    assert {(r.section_id, r.position) for r in db.prose} == rows_after_first
    assert len(db.prose) == 3
    # Nothing left to do, so nothing was asked of the model.
    assert gateway.prompts == []
    assert db.run.status == ReportRunStatus.SUCCEEDED


async def test_a_resume_still_re_checks_disclosure(
    monkeypatch: pytest.MonkeyPatch, connector: FakeConnector, gateway: FakeGateway
) -> None:
    """Every entry re-checks it, and a resume is an entry.

    The process was down; the policy may have been tightened while it was. A
    run that carried on writing paragraphs from values the model may no longer
    read would be the exact hole invariant #4 exists to close.
    """
    from app.domain.value_objects import DisclosurePolicy

    db = _readable()
    await _crash_after_first_section(monkeypatch, db)
    db.connection = _connection(DisclosurePolicy.NONE)
    db.run.status = ReportRunStatus.RUNNING

    await worker.resume_run(db, _settings(), db.run.id, asyncio.Event())

    assert db.run.status == ReportRunStatus.FAILED
    assert "disclosure" in (db.run.error_message or "").lower()


# ── the wiring ───────────────────────────────────────────────────────────
def test_the_graph_has_the_nodes_the_plan_named() -> None:
    assert set(REPORT_GRAPH.get_graph().nodes) == {
        START, CHECK_DISCLOSURE, RESOLVE_OUTLINE, CLEAR_SECTION, EXECUTE_BLOCKS,
        WRITE_RESULTS, NARRATE_SECTION, SUMMARISE, FINISH, END,
    }


def test_disclosure_is_the_first_node_on_both_entries() -> None:
    """Not a precondition hoisted into the caller: a policy tightened between
    creation and generation has to stop the run from inside, and a retry is a
    second generation of one section that gets no exemption."""
    assert (START, CHECK_DISCLOSURE) in edges()
    assert edges() >= {(CHECK_DISCLOSURE, RESOLVE_OUTLINE), (CHECK_DISCLOSURE, FINISH)}


def test_retry_is_a_second_entry_into_the_same_graph_not_a_call_on_one_node() -> None:
    """It runs the section's blocks *and* its paragraph, which is why it goes
    through `clear_section → execute_blocks → write_results` like everything
    else rather than jumping straight to the narrator."""
    assert (RESOLVE_OUTLINE, CLEAR_SECTION) in edges()
    assert (CLEAR_SECTION, EXECUTE_BLOCKS) in edges()
    assert (WRITE_RESULTS, NARRATE_SECTION) in edges()
    # A retried summary section takes the other branch.
    assert (WRITE_RESULTS, SUMMARISE) in edges()


def test_narration_loops_back_into_itself_and_is_not_a_send_fan_out() -> None:
    """Waves, not `Send`.

    A pass through this node writes several sections at once, but it is still a
    *loop*, and the loop is what carries `established` from one wave to the
    next. A `Send` fan-out would hand every section an empty document — the
    difference between "sections inside a wave cannot see each other" and "no
    section ever sees another", which is the whole quality dial.
    """
    assert (NARRATE_SECTION, NARRATE_SECTION) in edges()
    assert (NARRATE_SECTION, SUMMARISE) in edges()


def test_the_summary_is_an_explicit_edge_after_the_loop() -> None:
    """Not "last" by accident. It is skipped inside the loop, written after it,
    and then placed at its own position — usually first."""
    assert (SUMMARISE, FINISH) in edges()
    assert (SUMMARISE, NARRATE_SECTION) not in edges()


def test_every_path_ends_at_finish() -> None:
    """One place writes the run's terminal row, so the status derivation
    cannot be bypassed by a node that decides to stop early."""
    terminal = {source for source, target in edges() if target == END}
    assert terminal == {FINISH}


def test_the_graph_is_compiled_once() -> None:
    from app.workers import report_graph

    assert report_graph.REPORT_GRAPH is REPORT_GRAPH


# ── narration waves ──────────────────────────────────────────────────────
"""The wave is a latency/quality trade, and both halves of it are pinned here.

`report_narration_concurrency` decides how many sections are written at once.
The tests below fix what each end of that dial means, because the whole reason
the sections are written in *waves* rather than fanned out is that the number
buys speed by spending cross-section awareness — and a change that quietly took
the awareness without giving the speed, or the other way round, would look like
a passing suite.
"""


class OverlapGateway(FakeGateway):
    """A gateway that records how many calls were ever in flight at once.

    The sleep is what makes the measurement mean anything: without an await
    inside the call, four coroutines gathered together still run one after
    another to completion and a sequential implementation would score 4.
    """

    def __init__(self, *replies: Any) -> None:
        super().__init__(*replies)
        self.live = 0
        self.peak = 0

    async def complete(self, _llm: Any, messages: Any) -> Any:
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(0.01)
            return await FakeGateway.complete(self, _llm, messages)
        finally:
            self.live -= 1


def _overlapping(monkeypatch: pytest.MonkeyPatch, *replies: Any) -> OverlapGateway:
    fake = OverlapGateway(*replies)
    monkeypatch.setattr(
        worker.LiteLLMGateway, "from_settings", classmethod(lambda _cls, _s: fake)
    )
    return fake


async def test_a_wave_writes_its_sections_at_the_same_time(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the change: two sections, one provider round trip.

    A generation's wall clock is provider latency, and a document of eight
    sections used to be eight of them end to end.
    """
    fake = _overlapping(monkeypatch)
    db = _readable()

    await _run_generation(db, narration_concurrency=4)

    # Both sections in flight together; the summary is its own call afterwards,
    # which is why the peak is 2 rather than 3.
    assert fake.peak == 2
    assert len(fake.calls) == 3


async def test_concurrency_of_one_is_still_the_sequential_document(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dial's other end, and it has to keep meaning what it meant.

    One section at a time, each told what the one before it established — the
    behaviour every report was written with before the wave existed.
    """
    fake = _overlapping(monkeypatch, "بخش یکم این را گفت.", PROSE, PROSE)
    db = _readable()

    await _run_generation(db, narration_concurrency=1)

    assert fake.peak == 1
    # The second section reads the first. This is the sentence a fan-out loses.
    assert "بخش یکم این را گفت." in fake.prompts[1]


async def test_sections_in_one_wave_are_not_told_about_each_other(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stated rather than discovered later: this is what the speed costs.

    Two sections written together are two writers reading the same document,
    and neither can be shown a paragraph that does not exist yet. What they
    *are* still both given is `other_headings` — the outline — which is the
    half of "these paragraphs are one document" that survives the wave.
    """
    fake = _overlapping(monkeypatch, "بخش یکم این را گفت.", PROSE, PROSE)
    db = _readable()

    await _run_generation(db, narration_concurrency=4)

    assert "بخش یکم این را گفت." not in fake.prompts[1]
    # The neighbour's heading is still there, in both directions.
    assert "محصولات" in fake.prompts[0]
    assert "روند درآمد" in fake.prompts[1]


async def test_a_later_wave_reads_what_the_earlier_waves_established(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chain is coarser, not gone — which is why this is a wave and not a
    fan-out. Three sections at a wave size of two: the third reads both of the
    paragraphs written before it."""
    fake = _overlapping(monkeypatch, "یکم گفت.", "دوم گفت.", PROSE, PROSE)
    db = _readable(
        sections=[
            _summary_section(position=0),
            _section(position=1),
            _section(OTHER_SECTION_ID, position=2, heading="محصولات"),
            _section(THIRD_SECTION_ID, position=3, heading="مناطق"),
        ]
    )

    await _run_generation(db, narration_concurrency=2)

    assert fake.peak == 2
    third = fake.prompts[2]
    assert "یکم گفت." in third and "دوم گفت." in third


async def test_a_crash_in_one_section_keeps_the_paragraphs_beside_it(
    connector: FakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider *refusal* is already a FAILED row — `_narrate` catches it.

    This is the other kind: a genuine crash, which fails the run. It must not
    also throw away the paragraph that was written next to it, because the
    sequential loop it replaced kept everything it had committed.
    """
    real = worker._narrate

    async def narrate(settings: Any, **kwargs: Any) -> Any:
        if kwargs["section"].id == SECTION_ID:
            raise RuntimeError("narrator exploded")
        return await real(settings, **kwargs)

    monkeypatch.setattr(worker, "_narrate", narrate)
    db = _readable()

    await _run_generation(db, narration_concurrency=4)

    assert db.run is not None and db.run.status == ReportRunStatus.FAILED
    assert "narrator exploded" in (db.run.error_message or "")
    # The sibling's paragraph survived the crash it was gathered with.
    assert [row.heading_snapshot for row in db.prose] == ["محصولات"]
