"""§5.2 — a template's literals are a disclosure, and the ladder covers them.

This is the part of the design the research says nobody else got right, and it
is not covered by the disclosure work that already exists. A connection
declares `NONE | AGGREGATE | SAMPLE | FULL`, and `HintBudget` gates what the
schema block may say about a column's *contents*. Under `NONE` and
`AGGREGATE`, `value_lists` is false: no literal read from a row reaches the
model, ever.

A template's SQL contains literals. Rendered into a prompt, `WHERE tier =
'ENTERPRISE' AND region = 'EMEA'` puts two column values in front of the model
on a connection whose policy says none may go — not through a bug, but because
the template travels on a path the ladder does not cover.

The rule, and the precedent it follows:

> **A template's literals travel with structure when a human wrote them, and
> are gated like sample values when a machine did.**

Catalog comments are already exempt from the gate for a stated reason — *a
comment is DDL a human wrote: it is not read from a row, it does not change
when the data changes, and it is exactly as much "customer data" as a column
name.* A hand-authored template meets all three tests. A `MODEL_DERIVED` one
does not.

**Phase 1 renders no template into any prompt.** This file lands with the store
anyway, because the decision has to be in the tree *before* the read path
exists — otherwise Phase 5 inherits a gate nobody wrote.
"""
from __future__ import annotations

import pytest

from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.knowledge import LiteralProvenance, TemplateSource, may_render_literals

GATED = (DisclosurePolicy.NONE, DisclosurePolicy.AGGREGATE)
OPEN = (DisclosurePolicy.SAMPLE, DisclosurePolicy.FULL)


def _budget(policy: str) -> HintBudget:
    return HintBudget.from_policy(policy)


# ── the rule ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("policy", GATED)
def test_a_model_derived_template_is_withheld_under_a_closed_policy(
    policy: str,
) -> None:
    # The headline claim: under NONE and AGGREGATE the model sees no value
    # read from a row, and a template mined from a generated statement is
    # exactly that, laundered through a store.
    assert not may_render_literals(LiteralProvenance.MODEL_DERIVED, _budget(policy))


@pytest.mark.parametrize("policy", OPEN)
def test_a_model_derived_template_travels_once_values_may(policy: str) -> None:
    assert may_render_literals(LiteralProvenance.MODEL_DERIVED, _budget(policy))


@pytest.mark.parametrize("policy", GATED + OPEN)
def test_a_human_authored_template_travels_with_structure(policy: str) -> None:
    """The catalog-comment precedent, applied.

    A literal a person typed is not read from a row, does not change when the
    data changes, and is exactly as much customer data as a column name — which
    is sent under `NONE` on every question. Gating it would make the store
    useless precisely where the model is most starved, and would do so on a
    reading the codebase has already rejected once.
    """
    assert may_render_literals(LiteralProvenance.HUMAN_AUTHORED, _budget(policy))


def test_the_gate_is_the_same_switch_the_schema_block_uses() -> None:
    # Not a second ladder that could drift: `value_lists` is the field that
    # decides whether a column's actual values may be listed, and a template's
    # literals are the same disclosure by another route.
    for policy in GATED + OPEN:
        budget = _budget(policy)
        assert may_render_literals(
            LiteralProvenance.MODEL_DERIVED, budget
        ) is budget.value_lists


def test_an_unrecognised_policy_fails_closed() -> None:
    # `HintBudget.from_policy` fails closed on anything it does not know, and
    # this rung inherits that rather than restating it.
    assert not may_render_literals(
        LiteralProvenance.MODEL_DERIVED, _budget("SOMETHING_NEW")
    )


# ── who gets which provenance ────────────────────────────────────────────
def test_the_source_decides_the_provenance_and_the_awkward_case_is_handled() -> None:
    """A human editing a statement whose literals a model chose.

    The awkward case is real: someone corrects a generated query's join but
    leaves `region = 'EMEA'` as the model wrote it, possibly from sampled
    values disclosed under a policy that has since been tightened. A
    tightening must take effect on the next question — the existing rule,
    enforced at render time — and a store that survived it would quietly undo
    it. So a confirmation is `MODEL_DERIVED` and only a correction the curator
    typed is `HUMAN_AUTHORED`.
    """
    from app.api.v1.knowledge import _provenance

    assert _provenance(TemplateSource.MANUAL) is LiteralProvenance.HUMAN_AUTHORED
    assert _provenance(TemplateSource.CHAT_CORRECTED) is (
        LiteralProvenance.HUMAN_AUTHORED
    )
    assert _provenance(TemplateSource.CHAT_CONFIRMED) is (
        LiteralProvenance.MODEL_DERIVED
    )
    assert _provenance(TemplateSource.TILE) is LiteralProvenance.MODEL_DERIVED
    assert _provenance(TemplateSource.REPORT_BLOCK) is LiteralProvenance.MODEL_DERIVED


def test_every_source_is_assigned_a_provenance() -> None:
    # A source added later without a decision would default to whatever the
    # `in` test happened to say, which is the quiet way a disclosure gate stops
    # covering something.
    from app.api.v1.knowledge import _provenance

    for source in TemplateSource:
        assert _provenance(source) in tuple(LiteralProvenance)


# ── the gate is evaluated late ───────────────────────────────────────────
def test_the_gate_is_a_function_of_the_policy_not_of_the_stored_row() -> None:
    """Render time, never write time.

    Tightening a connection's policy has to take effect on the next question
    without a re-sync — the discipline `disclose()`, `HintBudget` and
    `disclose_history()` all follow. A gate that had been baked into the row at
    save time would keep answering with yesterday's policy.
    """
    provenance = LiteralProvenance.MODEL_DERIVED
    assert may_render_literals(provenance, _budget(DisclosurePolicy.SAMPLE))
    # Same row, same provenance, tightened policy — and the answer changes.
    assert not may_render_literals(provenance, _budget(DisclosurePolicy.NONE))
