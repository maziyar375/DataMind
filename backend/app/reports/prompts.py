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


# ── the time rules ───────────────────────────────────────────────────────
# Appended to `GENERATE_SYSTEM` for report blocks only, through
# `NodeDeps.extra_rules`. With no report in play the SQL prompts are
# byte-identical to what chat has always sent, which is why `PROMPT_VERSION`
# does not move for this.
#
# The whole feature turns on this paragraph. A report generated in Farvardin
# and re-run in Mehr must describe Mehr, and the only mechanism that achieves
# it without regenerating the SQL — which would break `sql_hash` comparison and
# the promise that the structure is preserved — is relative date arithmetic the
# *database* resolves at execution time.

#: How each engine writes "three months ago", verified against the guard rather
#: than assumed. Note Oracle: `ADD_MONTHS` parses to a node the AST allowlist
#: does not carry (`E_NODE_NOT_ALLOWED`), so the interval form is the one that
#: survives. If Oracle ever needs `ADD_MONTHS`, that is a guard change with its
#: own justification, not a prompt change.
DIALECT_DATE_ARITHMETIC = {
    "postgres": "order_date >= CURRENT_DATE - INTERVAL '3 months'",
    "mysql": "order_date >= DATE_SUB(CURRENT_DATE, INTERVAL 3 MONTH)",
    "mssql": "order_date >= DATEADD(month, -3, CAST(GETDATE() AS date))",
    "oracle": "order_date >= TRUNC(SYSDATE) - INTERVAL '3' MONTH",
}

#: The stored label, as a phrase a model can act on.
TIME_WINDOW_PHRASES = {
    "none": "not specified — the question stands on its own",
    "last_7_days": "the last 7 days",
    "last_30_days": "the last 30 days",
    "last_month": "last month",
    "last_3_months": "the last 3 months",
    "last_12_months": "the last 12 months",
    "previous_quarter": "the previous quarter",
    "ytd": "this year, to date",
    "custom": "described in the question itself",
}

REPORT_TIME_RULES = """\
This query is one block of a saved report. The same statement will be run \
again months from now, against this database with newer data, and it must \
describe the period *then* — not the period today.

- The window for this block is: {window}.
- Write the window as date arithmetic the database evaluates when the query \
runs. In {dialect} that looks like: {example}
- Never write a literal date, a year, a month name or a quarter as a constant. \
`WHERE order_date >= '2026-01-01'` is wrong here even though it is correct \
today, because next quarter it silently reports the wrong period.
- If the window is not specified, add a date filter only when the question \
itself names a period — and write that one the same relative way.{conventions}"""


def report_time_rules(
    *, database_type: str, time_window: str, conventions: str = ""
) -> str:
    """The rules a report block's SQL is generated under.

    `conventions` is the semantic layer's own time block — fiscal year start,
    week start, whether "last month" means a calendar month or a rolling one.
    It belongs per *connection*, not per report, which is why it arrives from
    there rather than being asked for again here: for a deployment whose fiscal
    year starts in Farvardin, that field is the only correct place for it.
    """
    return REPORT_TIME_RULES.format(
        window=TIME_WINDOW_PHRASES.get(time_window, time_window),
        dialect=database_type,
        example=DIALECT_DATE_ARITHMETIC.get(
            database_type, DIALECT_DATE_ARITHMETIC["postgres"]
        ),
        conventions=(f"\n- {conventions.strip()}" if conventions.strip() else ""),
    )
