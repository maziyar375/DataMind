"""Every intermediate result is disclosed before any of it reaches a prompt.

docs/plans/deep-analysis-mode.md Phase 5's risk, stated plainly there: *a loop
has many more places to leak a raw result into a prompt than a straight line
does, and the leak is invisible in the output — the answer looks better.* A
deep run has three prompt-builders that read earlier results — the reviser,
the writer, and the next turn's history via the stored answer — and this file
checks each, **per step**, against the policy.

The method: every result row carries a sentinel string no prompt could
contain by accident, and after the run every byte sent to any model is
scanned for them (`deep_world.prompts`). A sentinel the policy withheld that
turns up anywhere is a leak.

**The scan is proven to work** by `test_the_scan_sees_a_leak_on_a_broken_build`:
the same run with `disclose()` swapped for a pass-through, and the scan must
find the rows. A leak test that has never failed is a leak test nobody knows
works.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domain.ports.database import ResultColumn
from app.domain.value_objects import DisclosurePolicy
from app.pipeline import evidence as evidence_module
from app.pipeline.disclosure import SAMPLE_ROWS
from app.pipeline.evidence import narration
from app.pipeline.state import (
    AnalysisPlan,
    DisclosedResult,
    ExecutionResult,
    PlanStep,
    StepEvidence,
    StepRevision,
)
from tests.unit.deep_world import (
    SQL_BY_STATUS,
    DeepConnector,
    DeepGateway,
    deep_state,
    drive,
    prompts,
    result,
)

COLUMNS = [
    ResultColumn(name="status", db_type="text", semantic_type="nominal"),
    ResultColumn(name="revenue", db_type="numeric", semantic_type="quantitative"),
]


def _rows(step: int, n: int) -> list[list[Any]]:
    """`n` rows, each labelled with a sentinel naming its step and position."""
    return [[f"SENTINEL-s{step}-r{i:03d}", float(1000 + i)] for i in range(n)]


def _plan() -> AnalysisPlan:
    """Three steps, the last depending on the first two — so the reviser reads
    two earlier results and the writer reads three."""
    return AnalysisPlan(restatement="why", stop_when="named", steps=[
        PlanStep(question="A?", intent="CONFIRM", why="", tool="SQL", depends_on=[]),
        PlanStep(question="B?", intent="DECOMPOSE", why="", tool="OUTLIERS", depends_on=[]),
        PlanStep(question="C?", intent="DRILL", why="", tool="SQL", depends_on=[0, 1]),
    ])


class Reviser(DeepGateway):
    """Keeps every step, but records that it was asked — the reviser is one of
    the three prompt-builders this file watches."""

    async def structured(self, llm, messages, schema, **kw):  # type: ignore[no-untyped-def]
        if schema is StepRevision:
            self.messages.append(("StepRevision", list(messages)))
            return StepRevision(keep=True, question="", why="", intent="DRILL", tool="SQL")
        return await super().structured(llm, messages, schema, **kw)


async def _run(policy: str, sizes: tuple[int, int, int]) -> tuple[Any, Reviser]:
    results = [result(COLUMNS, _rows(i, n)) for i, n in enumerate(sizes)]
    gateway = Reviser(plan=_plan(), sql=[SQL_BY_STATUS] * 3,
                      prose=("Revenue moved [1].",))
    state, _ = await drive(deep_state(policy=policy), gateway, DeepConnector(results))
    return state, gateway


def _leaked(sent: str, step: int, rows: range) -> list[str]:
    return [f"SENTINEL-s{step}-r{i:03d}" for i in rows if f"SENTINEL-s{step}-r{i:03d}" in sent]


# ── per policy, per step ─────────────────────────────────────────────────
@pytest.mark.parametrize("policy", [DisclosurePolicy.NONE, DisclosurePolicy.AGGREGATE])
async def test_under_a_narrow_policy_no_row_of_any_step_reaches_any_prompt(
    policy: str,
) -> None:
    state, gateway = await _run(policy, (5, 5, 5))
    sent = prompts(gateway)
    for step in range(3):
        assert _leaked(sent, step, range(5)) == [], f"step {step} leaked under {policy}"
    # And none of the three prompt-builders was even called with data: no
    # revision, no writer — the answer is the product's own.
    names = {name for name, _ in gateway.messages}
    assert "StepRevision" not in names and "stream" not in names
    assert [e.status for e in state.evidence] == ["DONE", "DONE", "DONE"]


async def test_under_sample_each_step_shares_its_first_fifty_rows_and_no_more() -> None:
    """Checked per step: 60 rows each, so rows 50–59 of *every* step must be
    absent from every prompt, and rows 0–49 are what the writer was given."""
    state, gateway = await _run(DisclosurePolicy.SAMPLE, (60, 60, 60))
    sent = prompts(gateway)
    for step in range(3):
        assert _leaked(sent, step, range(SAMPLE_ROWS, 60)) == [], f"step {step} leaked"
        assert _leaked(sent, step, range(SAMPLE_ROWS)) != [], f"step {step} was never shown"


async def test_figures_computed_from_withheld_rows_do_not_reach_the_writer() -> None:
    """Step two's OUTLIERS ran over all 60 rows; the writer saw 50, so it is
    told figures exist and never what they are."""
    state, gateway = await _run(DisclosurePolicy.SAMPLE, (60, 60, 60))
    computed = state.evidence[1].computed
    assert computed is not None and computed["ok"] and computed["complete"] is False
    sent = prompts(gateway)
    for fact in computed["facts"]:
        assert fact["text"] not in sent
    assert "not shared with you" in sent


async def test_figures_reach_the_writer_when_it_had_every_row() -> None:
    state, gateway = await _run(DisclosurePolicy.SAMPLE, (5, 5, 5))
    computed = state.evidence[1].computed
    assert computed is not None and computed["complete"] is True
    assert computed["facts"][0]["text"] in prompts(gateway)


async def test_under_full_the_rows_are_shared_which_is_what_the_scan_detects() -> None:
    """The widest policy is the positive control for the scanner. Under the
    writer's own 50-row prompt budget (`narrate.MAX_PROMPT_ROWS`), so every
    row of every step is expected."""
    _, gateway = await _run(DisclosurePolicy.FULL, (20, 20, 20))
    for step in range(3):
        assert len(_leaked(prompts(gateway), step, range(20))) == 20


async def test_a_plain_answer_never_carries_figures_from_withheld_rows() -> None:
    """The next turn is the third prompt-builder. Under SAMPLE the stored
    answer is replayed to the next question's model verbatim, so a model-free
    answer written after a failed writer must not quote a figure computed from
    rows past the fifty the model was allowed."""
    results = [result(COLUMNS, _rows(i, 60)) for i in range(3)]

    class Broken(Reviser):
        def stream(self, *_a: Any, **_kw: Any) -> Any:
            from app.core.errors import LLMError

            async def gen() -> Any:
                raise LLMError("provider down")
                yield  # pragma: no cover

            return gen()

    gateway = Broken(plan=_plan(), sql=[SQL_BY_STATUS] * 3)
    state, _ = await drive(deep_state(policy=DisclosurePolicy.SAMPLE), gateway,
                           DeepConnector(results))
    computed = state.evidence[1].computed
    assert computed is not None and computed["ok"]
    assert state.answer
    assert computed["summary"] not in state.answer
    assert all(f"SENTINEL-s{s}-r0" not in state.answer for s in range(3))


# ── the function that builds every block ─────────────────────────────────
def test_narration_never_reads_execution() -> None:
    """A step whose `execution` holds rows its `disclosed` does not: the block
    a prompt is built from carries only the disclosed ones."""
    execution = ExecutionResult(
        columns=COLUMNS, rows=[["SENTINEL-raw", 1.0], ["visible", 2.0]], row_count=2,
    )
    step = PlanStep(question="A?", intent="CONFIRM", why="", tool="SQL", depends_on=[])
    evidence = StepEvidence(
        index=0, step=step, execution=execution,
        disclosed=DisclosedResult(policy="SAMPLE", columns=["status", "revenue"],
                                  rows=[["visible", 2.0]]),
        row_count=2, column_types={"status": "nominal", "revenue": "quantitative"},
    )
    rendered = narration(evidence).render(1)
    assert "visible" in rendered
    assert "SENTINEL-raw" not in rendered


# ── the canary ───────────────────────────────────────────────────────────
async def test_the_scan_sees_a_leak_on_a_broken_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break disclosure on purpose — every row, whatever the policy — and the
    SAMPLE test's own assertion must now fail. If this ever passes with the
    scan finding nothing, the scan is blind and every test above is too."""

    def leak_everything(execution: ExecutionResult, policy: str) -> DisclosedResult:
        return DisclosedResult(
            policy=policy, columns=[c.name for c in execution.columns],
            rows=execution.rows,
        )

    monkeypatch.setattr(evidence_module, "disclose", leak_everything)
    _, gateway = await _run(DisclosurePolicy.SAMPLE, (60, 60, 60))
    assert _leaked(prompts(gateway), 0, range(SAMPLE_ROWS, 60)) != [], (
        "the leak scan did not see a deliberately broken disclose()"
    )
