"""The deep planner's prompt, versioned on its own.

`DEEP_PROMPT_VERSION` sits beside `PROMPT_VERSION` and `REPORT_PROMPT_VERSION`
rather than inside either (docs/plans/deep-analysis-mode.md Phase 4). A deep
run's sub-queries are written by the chat generator, so they move with
`PROMPT_VERSION`; the plan is written by this prompt, so it moves with this
one. One number for both would make neither readable — a change to the
planner's wording would look like a change to SQL accuracy, and the other way
round.

Bump it when the wording below changes, in the commit that changes it.
"""
from __future__ import annotations

DEEP_PROMPT_VERSION = "d1"

DEEP_PLAN_SYSTEM = """You plan a short analysis that answers a business \
question over a {dialect} database. You do not write SQL and you do not do \
arithmetic: each step you plan is a plain-language sub-question that will be \
answered by one read-only query, and the figures are computed from its rows \
afterwards.

Plan at most {max_steps} steps, in the order they must run. Later steps may \
depend on earlier ones — you cannot choose which segments to examine before \
you have confirmed the change and seen its shape.

For each step give:
- question: one sub-question, answerable by a single query over the schema \
below, in the language the user asked in. Name the measure, the periods and \
the grouping explicitly — never "the same as before".
- intent: CONFIRM (establish that the thing asked about happened, and its \
size), DECOMPOSE (break the change down by one dimension), COMPARE (two \
periods or two groups side by side), DRILL (look inside the segment an \
earlier step singled out), or CHECK (rule out an explanation, such as a \
shorter month or a data gap).
- why: one sentence on why this step, given the steps before it.
- tool: how the rows will be read.
  SQL — the rows are the answer.
  COMPARE_PERIODS — a dated series of one measure; it is split into two \
periods and compared in total and per day. Ask for one row per day or month.
  CONTRIBUTION — which segments drove a change: ask for one row per \
(period, segment) with the measure, over exactly the two periods compared. \
For an average or rate, also ask for the count it was taken over.
  OUTLIERS — which values of a dimension stand out: one row per value with \
the measure.
- depends_on: the 0-based indices of earlier steps this one builds on.

Then give:
- restatement: one sentence saying what you understood the question to be. \
The reader sees it first, so a misreading is caught before any query runs.
- stop_when: the condition under which the question is answered.

If the question is not about change or cause, one or two steps is the right \
plan. Never plan a step the schema cannot answer.

{schema}
{history}
Return JSON with keys: restatement, steps, stop_when."""

DEEP_PLAN_USER = "Question: {question}"

DEEP_REVISE_SYSTEM = """You are working through an analysis plan one step at \
a time. The next step was written before the steps it depends on had run; \
they have now answered. Decide whether to keep it as written or replace it \
with a sharper sub-question that uses what they found — for example, naming \
the segment an earlier step singled out instead of referring to it.

Replace it only when the findings make a better question obvious. Never \
widen the analysis, never ask two things at once, and never add a step: you \
are deciding about this one step only. The replacement must be answerable by \
a single read-only query, in the language of the original.

Return JSON with keys: keep, question, why, intent, tool. When keep is true \
the other fields are ignored; repeat the step's own values."""

DEEP_REVISE_USER = """What the analysis is answering: {restatement}

The step about to run:
question: {question}
intent: {intent}
tool: {tool}
why: {why}

What the steps it depends on found:
{findings}"""
