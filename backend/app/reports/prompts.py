"""Prompts for reports, versioned as one unit.

They live here rather than in `pipeline/prompts/` for the reason the semantic
ones live in `app/semantic/prompts.py`: reports sit *below* the pipeline — a
report reads a pipeline node, and a node knows nothing about a report.

`REPORT_PROMPT_VERSION` is recorded on every run, so a document generated
before a wording change is never silently compared with one generated after it.

The outline prompt is the one the whole feature turns on: everything downstream
— the SQL, the prose, the summary — is written against the structure this call
returns, and a structure the user has approved is the difference between a
report and a pile of charts.
"""
from __future__ import annotations

REPORT_PROMPT_VERSION = "r1"

# What a language code means to a model. The code alone ("fa") is understood by
# the strong models and guessed at by the rest; the endonym removes the guess.
LANGUAGE_NAMES = {
    "fa": "Persian (فارسی)",
    "en": "English",
}

# The windows a block may name. Kept beside the prompt that offers them so a
# new one cannot be added to the enum and quietly never proposed.
TIME_WINDOWS = (
    "none", "last_7_days", "last_30_days", "last_month", "last_3_months",
    "last_12_months", "previous_quarter", "ytd", "custom",
)


# ── the outline ──────────────────────────────────────────────────────────
# Not a `.format` template: the JSON shape below carries real braces, and the
# variables all live in the user message.
REPORT_OUTLINE_SYSTEM = """You plan the structure of a written analytical \
report over a SQL database.

You are given a request in the user's own words, the database schema, and — \
when one exists — what that schema means in business terms. You return the \
report's outline: the sections, in reading order.

A **section** is one heading and the questions answered under it. A **block** \
is ONE question that becomes ONE SQL query and ONE chart, table or number. So \
a block asks for exactly one thing, and a section groups the blocks that a \
single paragraph can narrate together — a trend and the top contributors to \
it belong under one heading, because the paragraph about them is one thought.

Return JSON in exactly this shape, and nothing else:

{"sections": [{"heading": "...", "intent": "...", "blocks": [{"question": \
"...", "block_type": "CHART", "time_window": "none"}]}]}

Rules:
- Between 3 and 6 sections. Between 1 and 3 blocks in each. A section with no \
blocks is not a section.
- Write `heading`, `intent` and `question` in the language named in the \
request below — whatever language the table and column names happen to be in.
- `intent` is one line saying what this section's paragraph should cover. It \
is an instruction to the writer, not a subtitle, and the reader never sees it.
- Every question must be answerable by one SQL query over the tables shown. \
Never name a table or a column that is not in the schema, and never ask for \
something the schema does not record.
- `block_type` is one of CHART, TABLE or METRIC: METRIC for a single headline \
number, TABLE for a ranked or itemised list, CHART for a trend over time or a \
comparison across categories.
- `time_window` is one of: none, last_7_days, last_30_days, last_month, \
last_3_months, last_12_months, previous_quarter, ytd, custom. Use `none` when \
the question is not about a period. Follow the window the request asks for.
- Do NOT propose an executive summary, an introduction or a conclusion. One \
summary is written for you, from the finished sections.
- No SQL. No commentary. No markdown. JSON only."""

REPORT_OUTLINE_USER = """Write the outline in: {language}

Dialect: {dialect}

The request, in the user's own words:
{request}

{schema}"""
