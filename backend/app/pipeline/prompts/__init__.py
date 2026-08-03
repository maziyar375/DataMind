"""Versioned prompts. `prompt_version` is recorded on every run so a change in
wording never silently invalidates historical comparisons.
"""
from __future__ import annotations

from app.charts import (
    DUAL_AXIS_RATIO,
    MAX_HEATMAP_CELLS,
    MAX_PIE_SLICES,
    MAX_SERIES,
    MIN_HISTOGRAM_ROWS,
)

PROMPT_VERSION = "v7"
# v7: two changes, both about the *envelope* the SQL arrives in rather than the
# SQL itself. Together they turn "did not return valid SqlProposal JSON" from a
# routine failure on a wide schema into one I could not reproduce.
#
# The failure: a model handed a 28,892-char schema block writes a correct query
# in ~90 tokens and then keeps going — filling whatever unbounded field the
# contract offers until `max_tokens` cuts the reply mid-string. The JSON never
# closes, so `_parse_into` throws and a correct statement is discarded.
#
# 1. **`tables_used` removed from `SqlProposal`** and from the three "Return
#    JSON with keys" lines. Nothing read it: the table list the platform trusts
#    is the one `sqlguard` parses out of the SQL, because a model's claim about
#    which tables it used is not evidence. As an unbounded array it was where
#    the runaway went first — measured at 1,350 entries, the same 42 tables
#    repeated 61 times.
#
# 2. **`_OUTPUT_RULES`** appended to all three SQL prompts. With `tables_used`
#    gone the deliberation simply moved into the `sql` string ("-- but the
#    question might mean…", then another query). Telling the model to put the
#    statement and nothing else in that field is what actually stops it. See
#    the measurements at `_OUTPUT_RULES`.
#
# Raising `max_tokens` is **not** a fix and was measured: at 4,096 the failure
# rate was worse than at 2,048, and the median reply was exactly the cap at
# both sizes — the rambling expands to fill whatever it is given.
#
# The version moves because the rendered prompt does. A v6 and a v7 run are
# otherwise indistinguishable from the outside, and the difference between them
# is the wide-schema failure rate.
#
# v6: two changes to how the conversation reaches the model, one widening and
# one narrowing, both on the SQL-producing path.
#
# `route` can now be sent the history (`ROUTE_SYSTEM_WITH_HISTORY`). It used to
# classify the bare question, so a follow-up carrying no data noun of its own —
# "and by month?", "what about last year" — was judged on nine characters and
# could come back CHITCHAT or UNSUPPORTED, halting the run before any SQL was
# written. The history-free `ROUTE_SYSTEM` is unchanged and is still what a
# first turn sends, so a conversation's opening question is byte-identical to
# v5.
#
# The history itself is now filtered by the connection's disclosure policy
# before it is rendered (`disclosure.disclose_history`). Under SAMPLE and FULL
# that filter is the identity function and every SQL prompt is byte-identical
# to v5; under NONE and AGGREGATE an earlier answer's prose is replaced by a
# placeholder while its SQL survives. The version moves because the rendered
# prompt does, and a v5 and a v6 run on a narrow policy are otherwise
# indistinguishable from the outside.
#
# v5: the two repair prompts now *extend* the first-attempt prompt instead of
# replacing it. They previously carried only the feedback and the schema, so a
# repair attempt was never told "SELECT only", "never guess a name" or "do not
# add a LIMIT" — it could fix the flagged issue, break a rule it had not been
# shown, and be rejected a second time with a repair budget of 1 already spent.
# The mandatory rules now live in one `_SQL_RULES` constant used by all three.
# Both repair prompts also carry the conversation history the first attempt
# gets, and that history now includes the SQL each earlier answer ran, not only
# its prose: `state.answer` is what is persisted as the assistant message, so
# before this a follow-up ("now break that down by month") had to re-derive the
# previous query from a 300-character narration excerpt.
#
# This is an unconditional addition to the SQL-producing path, which eval Round
# 2 showed can cost accuracy on a small model (see the note under
# GENERATE_SYSTEM). It is measured with `repair_violations_by_rule`, which
# attributes each guard rejection to the attempt that raised it.
#
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

_ROUTE_LABELS = """You classify a user's question about a SQL database.

Return one of:
- ANALYTICAL: needs data from the database to answer.
- METADATA: asks about the schema itself (what tables/columns exist).
- CHITCHAT: greeting or small talk, no data needed.
- UNSUPPORTED: asks to modify data, or is outside the database's scope."""

ROUTE_SYSTEM = f"""{_ROUTE_LABELS}

Reply with the single word only."""

ROUTE_SYSTEM_WITH_HISTORY = f"""{_ROUTE_LABELS}

Classify the last question, reading it in the light of the turns before it. A
follow-up rarely repeats what it is about: "and by month?" after a revenue
question is ANALYTICAL, and "what columns does it have?" after a table was
named is METADATA. The earlier turns are context for reading the question —
never themselves the thing being classified.

{{history}}

Reply with the single word only."""
# Two prompts rather than one with an empty `{history}`, so the opening
# question of a conversation sends the bytes it sent before follow-ups were
# understood — the eval suite is single-turn, and its baseline is this prompt.

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

_SQL_RULES = """\
- Exactly one statement. No semicolons, no comments, no CTE tricks to hide writes.
- SELECT only. Never INSERT, UPDATE, DELETE, DDL, or any write.
- Use only the tables and columns given in the schema below. Never guess a name.
- Qualify tables with their schema (for example public.orders).
- Do not add a LIMIT; the platform applies one.
- Prefer explicit JOINs using the listed foreign keys.
- Answer exactly what is asked, at that granularity. If the question asks for a
  single figure, return one row with just that value — no per-group breakdown
  and no extra explanatory columns.
- Dialect: {dialect}"""
# One copy, because three drifted. These rules bind every call that produces
# SQL, and the copy that silently lost them was the repair path — the one place
# where a second rejection ends the run. `{dialect}` stays a placeholder here:
# the f-strings below interpolate this block verbatim, so every prompt built
# from it is still formatted with `dialect=` at call time.
#
# Anything added here is paid for on *every* SQL call, first attempt included.
# That is the addition eval Round 2 measured as harmful; keep this list to
# constraints the guard actually enforces, and re-measure when it changes.

_OUTPUT_RULES = """\
Reply with the JSON object only. Put the statement in `sql` and nothing else —
no SQL comments, no alternatives, no commentary inside the string. Keep
`reasoning` under two sentences, or omit it."""
# This is about the *envelope*, not about the SQL, and it is the difference
# between a working feature and a broken one on a wide schema. Without it, a
# model handed an ambiguous question deliberates **inside the `sql` string** —
# writing a query, then `-- but the question might mean…`, then another query —
# until `max_tokens` truncates the reply mid-string and a correct statement is
# thrown away as `E_LLM`.
#
# Measured on the 42-table `sales` schema, "which customers spent the most last
# quarter", 6 trials each: without it 2/6 failed and the median reply was 750
# completion tokens (max 2,048 — the cap, i.e. truncated); with it 0/6 failed
# and the median was 95 (max 96). Raising `max_tokens` is *not* the fix and was
# measured too: at 4,096 the failure rate got worse (6/8 vs 5/8) because the
# rambling simply expands to fill whatever budget it is given — the median
# reply was exactly the cap at both sizes.
#
# It is deliberately about output shape only. The eval Round 2 note below is
# about adding *SQL guidance*, which crowded out the schema; this subtracts
# from the reply rather than adding to the instructions, and it is the one
# addition here that makes the prompt cheaper. Re-measure on the eval suite
# anyway — that note exists because this prompt has surprised us before.

GENERATE_SYSTEM = f"""You write a single read-only SQL SELECT statement.

Rules, all mandatory:
{_SQL_RULES}

Schema:
{{schema}}

{{history}}

{_OUTPUT_RULES}"""
# NB (eval Round 2, reverted): adding a "getting the answer right" block of
# general SQL guidance here *lowered* execution accuracy on the small eval model
# (36% -> 26%) and hurt parse (98% -> 88%) and guard-pass (96% -> 86%) — the
# extra instructions crowded out the schema for gemma-4-31B. If you re-try
# generation guidance, keep it terse and re-measure; more is not better here.

GENERATE_USER = """Question: {question}

Return JSON with keys: sql, reasoning."""

REVIEW_SYSTEM = f"""Your previous SQL ran successfully, but the result looks wrong.

These are automated structural checks, not proof of a mistake. If a check is
wrong for this question, keep your original query.

{{feedback}}

The rules have not changed:
{_SQL_RULES}

Schema:
{{schema}}

{{history}}

Return JSON with keys: sql, reasoning.
{_OUTPUT_RULES}"""
# The rules block is repeated rather than assumed remembered: this is a fresh
# two-message conversation, not a continuation, so the model has never seen
# GENERATE_SYSTEM. "The rules have not changed" frames it as a restatement, so
# a model that fixed the flagged issue correctly is not invited to re-read the
# constraints as new instructions and rewrite a query that was already fine.

REPAIR_SYSTEM = f"""Your previous SQL was rejected by a validator. Fix it.

{{feedback}}

The rules have not changed:
{_SQL_RULES}

Schema:
{{schema}}

{{history}}

Return JSON with keys: sql, reasoning.
{_OUTPUT_RULES}"""

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

CHART_SYSTEM = f"""You choose the single best chart for a query result, or decline.

Pick one chart_type:
- "bar": compare a measure across categories. The platform decides which way
  the bars run and how they are sorted — do not try to control either.
- "line": a measure over a time or ordered axis (a trend). The column's grain
  is given below; a trend needs several points, not two.
- "area": the same shape as a line, when the filled magnitude is the point —
  volume over time, or composition over time when it is split and stacked.
- "scatter": the relationship between two measures, one per axis. Both axes
  must be numeric and neither may be a category.
- "pie": parts of a single whole, {MAX_PIE_SLICES} categories at most, and never
  with a negative value in the measure — a negative slice has no angle. Past
  that the platform draws bars instead.
- "heatmap": one measure across TWO dimensions at once — day by hour, region by
  category. Dimensions on x_axis and y_axis, the measure on "color". The notes
  below say what the two dimensions cross to; past {MAX_HEATMAP_CELLS} cells the
  squares are smaller than the eye resolves, and unlike a bar chart there is no
  honest way to keep only some of them, so pick something else.
- "histogram": how one measure is spread, when the rows are individual
  observations. Only x_axis; the count is derived, so name no y_axis. A column
  marked "one row per value" is a group key and its result is already
  aggregated — never a histogram. Needs {MIN_HISTOGRAM_ROWS} rows at least.
- "combo": bars for one measure with a line over them for another, when the two
  are on different scales — revenue and a percentage, volume and an average.
  y_axis is the bars, y2_axis is the line. The notes below say how far apart the
  measures are; separate axes come only past {DUAL_AXIS_RATIO}x, so a combo of
  two comparable measures just shares one.
- "none": nothing a chart would clarify. Prefer "none" over a chart nobody can
  read — an unreadable chart is worse than no chart.

Reach for "none" when the rows are a list of records to read — names,
addresses, statuses, one unique row each — rather than a comparison of
magnitudes, and when a category count is so high that the marks stop being
comparable. Results that could not be charted at all (a single row, a measure
identical in every row, an id as the only number) never reach you: the platform
checks the data before asking, so you are only ever shown a result something
could be drawn from.

Read the result block as facts, not hints. Each column carries its
distinct-value count, which for a bar or pie is exactly the number of marks a
reader has to compare; a date column also carries its grain and span. The
"shape notes" underneath are the arithmetic the rules above are stated in —
crossings, scale gaps, and what the platform will trim. Nothing there needs
recomputing.

Rules:
- x_axis and y_axis are required unless chart_type is "none" or "histogram".
- Only reference column names that appear in the result block below.
- Put the category/time field on x_axis and the numeric measure on y_axis,
  always — including for bars that will end up running sideways. The platform
  flips them for you; do not pre-swap.
- The result is ALREADY aggregated by SQL. Set each axis aggregation to "none"
  unless you are certain a further roll-up is needed.
- Set the axis "type" to match the column: quantitative for numbers, temporal
  for dates/timestamps, nominal for text, ordinal for ranked categories.
- Use "series" only to split a chart by a small dimension — {MAX_SERIES} distinct
  values at most; leave it unset otherwise.
- With a series on a bar or area chart, pick a "stack":
  - "stacked" (the default): the split parts add up to a meaningful total.
  - "grouped": the parts are being compared with each other, not summed.
  - "normalize": the question is about share or mix, not absolute size.
  Leave it alone unless the question points at one of the other two.
- Use "size" only on a scatter, and only for a third measure worth reading as
  magnitude — it makes the points a bubble chart. Never an id.

Return JSON matching the ChartIntent schema."""
# Every threshold above is interpolated from `app.charts`, never typed out. The
# budgets move — `MAX_HEATMAP_CELLS` and `DUAL_AXIS_RATIO` are both tuning
# constants — and a prompt quoting a stale number is worse than one quoting
# none: the model applies the rule it was given, `_fit` applies the rule in the
# code, and the difference shows up as a chart the platform "repaired" for no
# reason the reader can see.
#
# The same rule in the other direction: a chart type is not added or edited
# until it has a bullet here saying when to pick it and when not to, and until
# the result block carries the figure that bullet is compared against.
# `test_charts.py::test_every_chart_type_is_described` holds the first half of
# that; the second half is a reading, not a test.

CHART_USER = """Question: {question}

Result shape ({row_count} rows{truncated}):
{columns}

Choose the best chart, or "none"."""
# What crosses the wire here is shape, not data. Counts, ratios, a time grain
# and a span *length* are facts about the result rather than values out of it,
# and they go under every disclosure policy — a chart decision has to know that
# a column holds 1,000 distinct names or one repeated total, and a count is not
# disclosure. A numeric column's min/max is the exception, being one specific
# row's value, so it rides the same `HintBudget` gate as the schema block's
# column hints and appears only where result values already do. No rule above
# asks what the largest revenue *is*, only how it compares to the measure
# beside it, so the narrow block costs the decision nothing.
#
# PROMPT_VERSION does not move for chart-prompt changes — the eval scores
# generated SQL, and nothing on the SQL-producing path changed (same reasoning
# as the CLARIFY_SYSTEM note above).
