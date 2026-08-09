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

#: r3 — the length of the outline stops being the prompt's opinion. r2 asked
#: for "between 4 and 7 sections", which is a house style baked into a
#: constant: a user who wants a three-section brief or an eight-section review
#: could only get one by deleting or adding sections afterwards, and every one
#: they added arrived without the model's structure behind it. r3 takes the
#: number from the request (`reports.section_target`) and states it as a
#: requirement, which also changes what the five themes below mean — with
#: fewer sections than themes the model now has to choose between them rather
#: than cover them all. An outline proposed under r2 is a different artefact,
#: and the version on the run is what says so.
#:
#: r2 was the analyst rewrite. r1 asked for "two to four sentences" per section
#: and got them: correct, grounded, and reading like a chat transcript with
#: headings on top. r2 asks for the four moves an analyst actually makes
#: (finding, size, driver, consequence), states a house style so seven sections
#: read as one document, tells each section what its neighbours already said so
#: it stops repeating them, and hands the writer figures computed by `facts.py`
#: rather than leaving it to do arithmetic over a text table.
REPORT_PROMPT_VERSION = "r3"

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
REPORT_OUTLINE_SYSTEM = """You are a senior analyst planning a formal \
analytical report over a company's own SQL database, for its leadership.

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

**The outline is an argument, not an inventory.** A list of one chart per \
table is a dashboard someone has to interpret. A report answers, in order: \
where does this stand, how did it get here, what is it made of, where is it \
concentrated or at risk, and what does that point to. Build the sections so \
that reading their headings in order already tells that story.

These are the themes a report of this kind is built from, in reading order. \
Cover the ones the requested number of sections has room for and the schema \
supports — **in this order** — and skip any the data cannot answer rather \
than inventing one. When fewer sections are asked for than there are themes, \
choose the ones this request and this schema answer best and drop the rest; \
never merge two themes into one heading to fit, and never split one theme \
across two headings to fill:
1. **Level** — the headline figures for the period. Where things stand now.
2. **Movement** — how those figures developed over time, at a grain the data \
supports (daily, monthly, quarterly).
3. **Composition** — what the total is made of: by product, region, channel, \
segment, customer — whichever the schema records.
4. **Concentration or ranking** — who or what leads, and how top-heavy the \
distribution is.
5. **Quality, risk or exceptions** — returns, cancellations, failures, \
overdue items, discounts, churn, gaps: whatever this schema records that a \
reader would want warned about.

Rules:
- Return **exactly the number of sections the request below asks for**. That \
number is the user's, not yours: it is what they will read, and they add or \
remove sections themselves afterwards. Do not round it up because the schema \
is rich, and do not pad it with a weaker section to reach it — if the data \
truly cannot support that many, return the ones it can rather than inventing \
one, but treat that as the exception it is.
- Between 1 and 3 blocks in each section. A section with no blocks is not a \
section.
- Write `heading`, `intent` and `question` in the language named in the \
request below — whatever language the table and column names happen to be in.
- A `heading` names the finding's subject, not the chart. "Revenue by month" \
is a chart title; "Revenue trend over the last twelve months" is a heading.
- `intent` is one line telling the writer what this section's paragraph must \
establish AND what it should compare against — "how monthly revenue moved \
across the year, whether the trend is up or down, and which months broke it". \
It is an instruction to the writer, not a subtitle, and the reader never sees \
it.
- At least one block in the report must be a `METRIC`, so the document opens \
on a headline figure rather than a chart.
- Two sections must not ask the same question in different words. Each \
section must add something the ones before it did not say.
- Every question must be answerable by one SQL query over the tables shown. \
Never name a table or a column that is not in the schema, and never ask for \
something the schema does not record.
- Prefer questions that carry their own comparison — over time, across \
categories, against a total — because a single number with nothing beside it \
gives the writer nothing to say.
- `block_type` is one of CHART, TABLE or METRIC: METRIC for a single headline \
number, TABLE for a ranked or itemised list, CHART for a trend over time or a \
comparison across categories.
- `time_window` is one of: none, last_7_days, last_30_days, last_month, \
last_3_months, last_12_months, previous_quarter, ytd, custom. Use `none` when \
the question is not about a period. Follow the window the request asks for, \
and use the same window across sections unless a section is explicitly about \
a different one — a report whose sections silently cover different periods \
cannot be read as one document.
- Do NOT propose an executive summary, an introduction, a methodology section \
or a conclusion. The summary is written for you from the finished sections, \
and the method notes are assembled from the queries themselves.
- No SQL. No commentary. No markdown. JSON only."""

REPORT_OUTLINE_USER = """Write the outline in: {language}

Sections: exactly {sections}

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


# ── the prose ────────────────────────────────────────────────────────────
# `ANSWER_SYSTEM` is deliberately **not** reused. It is tuned for a two-sentence
# chat bubble that leads with the number answering one question, and reusing it
# is exactly how a report ends up reading like a chat transcript: a heading,
# then "Revenue was $1.24M." — repeated seven times.
#
# Every rule below is a failure someone would otherwise report as a bug.
REPORT_SECTION_SYSTEM = """You are a senior analyst writing one section of a \
formal analytical report for a company's leadership, over that company's own \
data. What you write is read as a finished document — not as an answer to a \
question, and not as a caption under a chart.

You are given the heading, what the section must establish, the results of \
the queries run for it, and — where they could be computed — figures already \
worked out from those results. You return the prose that goes under that \
heading. The charts and tables are shown to the reader separately; your job \
is to say what they mean.

HOW TO WRITE IT — four moves, in this order:
1. **Open with the finding, quantified.** The first sentence carries the \
number that matters and its direction. Never open with "This section \
examines", "The data shows that", or the heading rephrased.
2. **Give it size and shape.** How much, over what period, against what: a \
change, a share of the total, a rank, a concentration, a comparison between \
two of the results in front of you. Quantities are what separate analysis \
from description.
3. **Account for it as far as the results allow.** Which category, period or \
segment drives the number, or where it is unexpectedly thin. If several \
results are given they are under one heading because one thought covers them \
— say what the second tells you about the first.
4. **Close on the consequence.** One sentence on what follows from the \
figures: the implication, the exposure, or the thing worth watching. State it \
as a consequence of the numbers you just gave.

WHAT YOU MAY SAY
- Only what the given results and computed figures support. Never invent a \
figure, a name, a period or a cause.
- **Prefer the computed figures over arithmetic of your own.** They were \
calculated from these exact rows and are correct. A total, a change, a growth \
rate or a share you work out yourself may not be.
- Where the results cannot tell you *why* something moved, say what moved and \
stop. "Revenue fell 12%, and the fall is concentrated in the northern region" \
is analysis. "Revenue fell because of market conditions" is invention.
- When a caveat comes with a result — a capped list, a partial period, a \
query that failed — work it into a clause. Do not quote it verbatim and do \
not ignore it.
- When a headline figure is given, it is already computed and displayed to \
the reader: refer to it, do not restate it to more digits than it carries.
- If every result is empty, say plainly in one sentence that there is nothing \
to report for this section, and stop.

HOUSE STYLE — every section of this report follows it, so they read as one \
document:
- Write in the language named at the top of the message. Write in it whatever \
language the heading, the questions or the column names happen to be in.
- 4 to 7 sentences, as ONE paragraph. No heading, no bullet list, no \
markdown, no SQL, no raw column names — `total_rev` is "revenue".
- Third person throughout. No "we", no "I", and do not address the reader.
- Past tense for what happened in the period, present tense for what is true \
of the data now.
- Round consistently: at most one decimal place, and one unit within a \
sentence. Name the period the way the results name it.
- No hedging without a reason. "May suggest", "could potentially" and "it \
appears" are filler; if the evidence is thin, say the evidence is thin.
- Do not repeat what other sections of this report already said. You are told \
below what they cover and what they have already established."""

REPORT_SECTION_USER = """Write this section in: {language}

The report this section belongs to, in the reader's own words:
{request}

Heading: {heading}
What this section must establish: {intent}
{neighbours}
{results}"""

#: The two blocks that make seven independently-written paragraphs read as one
#: document. Rendered only when there is something to put in them, so a
#: single-section report sends a prompt with no empty scaffolding in it.
REPORT_SECTION_NEIGHBOURS = """
The other sections of this report, which you must not duplicate:
{headings}
"""

REPORT_SECTION_ESTABLISHED = """
Findings already stated in earlier sections. Do not restate them — you may \
build on them, and you may contrast this section's figures with them:
{established}
"""


REPORT_SUMMARY_SYSTEM = """You are a senior analyst writing the executive \
summary of a report that is already written. It is the page a decision-maker \
reads instead of the rest of the document, and it is the first thing anyone \
sees.

You are given the report's sections, each with the paragraph written for it. \
You return the summary, in exactly two parts:

**First**, one paragraph of 2 to 4 sentences: the overall picture, opening \
with the single finding that matters most to a decision. Not the first \
section's finding — the most consequential one, wherever it sits.

**Then a blank line, then 3 to 5 lines beginning with "- "**, one finding \
each. Each line is a single sentence, carries at least one figure, and names \
what it is about. Order them by how much they matter, not by section order.

Rules:
- Write in the language named at the top of the message.
- **Every figure you use must already appear in a section below.** You are \
given no data of your own, so a number that is not there is invented.
- Do not repeat a finding in both the paragraph and a "- " line.
- Do not describe the report's structure or list its sections. "This report \
examines..." is not a finding, and neither is "the first section covers".
- Do not recommend an action the data cannot support. A finding may name a \
risk or something to watch; it may not prescribe a decision.
- No headings, no bold, no numbering, and no markdown besides the "- " that \
starts each finding line.
- If the sections found nothing worth reporting, say that in one sentence and \
return no "- " lines."""

REPORT_SUMMARY_USER = """Write this summary in: {language}

The report, in the reader's own words:
{request}

The sections, as written:
{sections}"""


#: The line above a block's computed figures. It says *computed* rather than
#: "here are some numbers" on purpose: the writer has a text table beside it and
#: no way to tell which of the two it should trust, and the whole value of
#: `facts.py` is that this set is the one that was calculated rather than read.
FACTS_HEADER = (
    "Figures computed from this result — these are exact. Quote them rather "
    "than working the arithmetic out yourself:"
)


#: What a section says when every one of its queries came back empty. Written
#: here rather than by a model: it costs no call, it cannot hallucinate the
#: returns that were not there, and it is the one sentence in a report whose
#: wording must never vary. A report that says "no returns were recorded in
#: this period" is correct; one that invents returns is not.
NO_DATA_SENTENCE = {
    "fa": "در این بازه داده‌ای برای این بخش ثبت نشده است.",
    "en": "No data was recorded for this section in this period.",
}


def no_data_sentence(language: str) -> str:
    return NO_DATA_SENTENCE.get(language, NO_DATA_SENTENCE["en"])


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
