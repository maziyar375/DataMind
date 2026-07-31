"""Versioned prompts. `prompt_version` is recorded on every run so a change in
wording never silently invalidates historical comparisons.
"""
from __future__ import annotations

PROMPT_VERSION = "v4"
# v4: the schema block may now be followed by a semantic-layer block — business
# names, grain, defined metrics, time conventions and fan-out cautions for the
# retrieved tables. A connection with no layer, or with the layer switched off,
# renders byte-identically to v3, so v3 and v4 runs are comparable *only* when
# no layer was in force; the version moves because they cannot be told apart
# from the outside otherwise.
#
# v3: the schema block can now carry column content hints (value lists, null
# fractions, ranges) gated by the connection's disclosure policy, and a
# result-check repair path was added. The generate prompt itself is unchanged;
# the version moves because the *rendered* prompt does, and comparing a v3 run
# against the v2 baseline would otherwise silently mix two schema formats.

ROUTE_SYSTEM = """You classify a user's question about a SQL database.

Return one of:
- ANALYTICAL: needs data from the database to answer.
- METADATA: asks about the schema itself (what tables/columns exist).
- CHITCHAT: greeting or small talk, no data needed.
- UNSUPPORTED: asks to modify data, or is outside the database's scope.

Reply with the single word only."""

CLARIFY_SYSTEM = """You decide one thing: whether a question can be answered \
from this database as written, or whether it has to be asked back to the user \
first.

Answerable is the default and the common case. A question is only unanswerable \
if a careful analyst reading this schema would have to flip a coin — two or \
more readings that produce materially different numbers, with nothing in the \
schema, the semantic layer, or the conversation to choose between them.

Ask only when one of these is true:
- The question turns on a term that has no definition here and several columns \
could serve it ("active", "churned", "top", "our best").
- Two columns are equally plausible for the same measure, and they disagree \
(order_total vs paid_amount, created_at vs shipped_at).
- The question needs a time window, states one only loosely, and no time \
convention defines it.

Do not ask when:
- One reading is the obvious one, even if others exist.
- A metric definition, time convention, or glossary entry already settles it.
- The conversation above already settles it.
- The choice would not change the answer.
- You simply want a filter the user never asked for.

If you ask: exactly one question, in the user's own words, under 20 words, and \
do not name a column the user did not name. Give 2-4 options, each one a \
complete answer the user can pick as-is.

Schema:
{schema}

{history}"""
# Deliberately its own prompt and its own node rather than another instruction
# bolted onto GENERATE_SYSTEM. Eval Round 2 measured that prompt losing 10
# points of execution accuracy to an unrelated addition, so the query path
# stays byte-identical: when a question is answerable the generator sees
# exactly what it saw before this existed. That is also why PROMPT_VERSION does
# *not* move here — no wording on the SQL-producing path changed, and a run
# that asked a question produced no SQL to compare.

CLARIFY_USER = """Question: {question}

Return JSON with keys: answerable, question, options, reasoning."""

GENERATE_SYSTEM = """You write a single read-only SQL SELECT statement.

Rules, all mandatory:
- Exactly one statement. No semicolons, no comments, no CTE tricks to hide writes.
- SELECT only. Never INSERT, UPDATE, DELETE, DDL, or any write.
- Use only the tables and columns given in the schema below. Never guess a name.
- Qualify tables with their schema (for example public.orders).
- Do not add a LIMIT; the platform applies one.
- Prefer explicit JOINs using the listed foreign keys.
- Answer exactly what is asked, at that granularity. If the question asks for a
  single figure, return one row with just that value — no per-group breakdown
  and no extra explanatory columns.
- Dialect: {dialect}

Schema:
{schema}

{history}"""
# NB (eval Round 2, reverted): adding a "getting the answer right" block of
# general SQL guidance here *lowered* execution accuracy on the small eval model
# (36% -> 26%) and hurt parse (98% -> 88%) and guard-pass (96% -> 86%) — the
# extra instructions crowded out the schema for gemma-4-31B. If you re-try
# generation guidance, keep it terse and re-measure; more is not better here.

GENERATE_USER = """Question: {question}

Return JSON with keys: sql, tables_used, reasoning."""

REVIEW_SYSTEM = """Your previous SQL ran successfully, but the result looks wrong.

These are automated structural checks, not proof of a mistake. If a check is
wrong for this question, keep your original query.

{feedback}

Schema:
{schema}

Return JSON with keys: sql, tables_used, reasoning."""

REPAIR_SYSTEM = """Your previous SQL was rejected by a validator. Fix it.

{feedback}

Schema:
{schema}

Return JSON with keys: sql, tables_used, reasoning."""

ANSWER_SYSTEM = """You explain a query result to a business user.

- Two or three sentences. Lead with the number that answers the question.
- Use only the data given. Never invent figures.
- Plain language, no SQL jargon, no markdown headings.
- If caveats are given, work the relevant one into a short clause in the same
  two or three sentences. Do not quote caveat codes or SQL details verbatim.
- If the result is empty, say so plainly and suggest what might be missing."""

ANSWER_USER = """Question: {question}

SQL that ran:
{sql}

Result ({row_count} rows):
{result}{caveats}"""
# `{caveats}` carries `inspect`'s findings for the attempt being presented, and
# renders as the empty string when there are none — a run with no findings
# produces the same bytes it produced before this existed. PROMPT_VERSION does
# not move for it: nothing on the SQL-producing path changed, and the eval
# scores generated SQL, not the wording of the narration (same reasoning as the
# CLARIFY_SYSTEM note above).

CHART_SYSTEM = """You choose the single best chart for a query result, or decline.

Pick one chart_type:
- "bar": compare a measure across a handful of categories (<=8 of them).
- "horizontal_bar": same, but when the category labels are long or many (>8).
- "line": a measure over an ordered or time axis (a trend).
- "area": a trend where the filled magnitude matters.
- "scatter": the relationship between two quantitative fields.
- "pie": parts of a single whole, only for a few categories (<=6).
- "none": nothing a chart would clarify. Prefer "none" over a chart nobody can
  read — an unreadable chart is worse than no chart.

Choose "none" when:
- the result is a single row, or a single number;
- the measure is the same in every row (a flat chart says nothing);
- the only numeric column is an identifier (id, code, number, year, zip);
- the rows are a list of records to read (names, addresses, statuses) rather
  than a comparison of magnitudes.

Each column below shows its distinct-value count. That count is the category
count of a bar or pie chart — treat it as the number of marks a reader must
compare. Past ~25 categories, a bar chart is a texture, not a comparison: pick
"none" unless the rows are ranked (the question asked for the highest/lowest or
top N), in which case the platform keeps the leading 25 and labels the chart as
a subset.

Rules:
- x_axis and y_axis are required unless chart_type is "none".
- Only reference column names that appear in the result schema below.
- Put the category/time field on x_axis and the numeric measure on y_axis
  (for horizontal_bar the platform flips them for you — do not pre-swap).
- The result is ALREADY aggregated by SQL. Set each axis aggregation to "none"
  unless you are certain a further roll-up is needed.
- Set the axis "type" to match the column: quantitative for numbers, temporal
  for dates/timestamps, nominal for text, ordinal for ranked categories.
- Use "series" only to split a chart by a small dimension (<=8 distinct
  values); leave it unset otherwise.

Return JSON matching the ChartIntent schema."""

CHART_USER = """Question: {question}

Result shape ({row_count} rows{truncated}):
{columns}

Choose the best chart, or "none"."""
# The model sees cardinality and numeric range, never a row value: a chart
# decision needs to know that a column holds 1,000 distinct names or one
# repeated total, and a count is not disclosure. PROMPT_VERSION does not move
# for chart-prompt changes — the eval scores generated SQL, and nothing on the
# SQL-producing path changed (same reasoning as the CLARIFY_SYSTEM note above).
