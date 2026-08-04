"""Prompts for building a semantic layer.

These live here rather than in `pipeline/prompts/` because `app.semantic` sits
*below* the pipeline in the layer order — the pipeline reads the layer, the
layer knows nothing about a run. They are versioned the same way and for the
same reason: a wording change that alters what the model writes must not
silently reinterpret documents generated before it.

Three passes, deliberately. Asking one call to describe forty tables produces
forty one-line descriptions and no metrics; asking per table produces grain
statements and real expressions, and it is the metrics that change answers.
"""
from __future__ import annotations

SEMANTIC_PROMPT_VERSION = "s2"

OVERVIEW_SYSTEM = """You are a data analyst reading a database schema for the \
first time. You write the short orientation note a new analyst would want.

You are given every table name with its row count and the foreign keys between \
them. You are NOT given column detail — do not invent any.

Return JSON with these keys:
- business_context: 2-3 sentences. What business is this database for, what \
does it record, and which tables are the ones people actually ask about. Name \
real tables. No filler, no restating the question.
- industry: one or two words, or "" if it is not clear.
- default_exclusions: one sentence naming rows that should not count unless \
the question asks for them — soft-delete or archive flags, test or internal \
accounts, cancelled-by-definition states. Name the real column where you can \
see one ("rows where is_archived is true"). Return "" unless a column name \
makes it plain; a guessed exclusion silently changes every total.
- fiscal_year_start_month: 1-12. Use 1 unless a table name or column implies \
otherwise.
- week_starts_on: "monday" or "sunday".
- timezone: an IANA name, or "UTC" if nothing suggests another.
- relative_windows: "calendar" or "rolling" — what a question like "last \
month" should mean here. Prefer "calendar".
- notes: any other time convention worth stating in one sentence, or ""."""

OVERVIEW_USER = """Dialect: {dialect}

Tables:
{tables}

Foreign keys:
{relationships}"""


TABLE_SYSTEM = """You describe ONE table of a SQL database so that another \
model can write correct SQL against it without guessing.

You are given the table's full column list, the tables it is joined to by \
foreign key, and an orientation note about the database.

Return JSON with these keys:
- label: the business name a person would use. Singular or plural as natural.
- description: one or two sentences. What this table records and when a row \
appears. Do not restate the column list.
- grain: complete the sentence "one row per ...". Be exact — this is the most \
important field. If a row is per line item, say so; if it is a daily snapshot, \
say so.
- role: "fact", "dimension", "bridge", "lookup", or "unknown".
- synonyms: other words a business user would use for this table. [] if none.
- default_time_column: the column that answers "when did this happen" for this \
table. Exactly one column name from the list, or "" if the table has no \
meaningful date.
- columns: only the columns that need explaining — codes, abbreviations, \
amounts with a unit, anything whose name is not self-evident. Skip obvious \
ids and names. Each item has: name (exactly as given), label, description, \
role ("key", "time", "dimension", "measure", "attribute"), unit (e.g. "USD", \
"cents", "days", or ""), synonyms, and value_meanings (a map from a listed \
value to what it means, only for columns whose values were given to you).
- metrics: the business measures people ask this table for. Each has:
    name: snake_case identifier, e.g. total_revenue
    label: how a person says it
    description: one sentence
    expression: a SQL aggregate over columns of THIS table, written with the \
fully qualified table name, e.g. SUM(sales.order_items.quantity * \
sales.order_items.unit_price). Use only columns you were given. If it needs a \
column from a joined table, list that table in required_joins and qualify the \
column the same way.
    filters: predicates that are part of the DEFINITION, not the question — \
e.g. ["sales.orders.status <> 'CANCELLED'"]. [] if none.
    required_joins: qualified table names the expression needs. [] if none.
    additive: "additive" if it can be summed across any grouping, \
"semi_additive" if it can be summed across some dimensions but not time (a \
balance, a stock level), "non_additive" for ratios and averages.
    unit: "USD", "cents", "count", "days", "%", or "".
    synonyms: what a user would call it — "revenue", "GMV", "net sales".

Rules:
- Never invent a column or a table name. Every name you write must appear in \
what you were given.
- A metric must aggregate. A plain column reference is not a metric.
- Prefer three good metrics to ten weak ones. A pure lookup or junction table \
usually has none — return [] rather than padding.
- Say nothing you cannot support from the schema. "Unknown" is a valid answer \
and a wrong guess here poisons every query that follows."""

TABLE_USER = """Dialect: {dialect}

About this database: {context}

Describe this table: {table}
{table_ddl}

Tables it is joined to (for required_joins and cross-table metrics only):
{neighbours}"""


GLOSSARY_SYSTEM = """You write the glossary for a business intelligence tool.

You are given the tables of a database with their business names and the \
metrics defined on them. List the terms a user will type that are NOT already \
the name of a table or a metric — jargon, abbreviations, and derived concepts \
that need defining before a query can be written.

Return JSON with one key, "terms": a list of objects with:
- term: the word or phrase, lowercase
- meaning: one sentence, and where it is a rule ("active customer"), state the \
rule precisely enough to write SQL from
- maps_to: the qualified table names or metric names it resolves to

Rules:
- At most 12 terms. Fewer is better.
- Skip anything already obvious from a table or metric name.
- Do not invent business rules the schema does not support. If nothing needs \
defining, return an empty list."""

GLOSSARY_USER = """Database: {context}

Entities and their metrics:
{entities}"""
