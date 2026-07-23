"""Versioned prompts. `prompt_version` is recorded on every run so a change in
wording never silently invalidates historical comparisons.
"""
from __future__ import annotations

PROMPT_VERSION = "v1"

ROUTE_SYSTEM = """You classify a user's question about a SQL database.

Return one of:
- ANALYTICAL: needs data from the database to answer.
- METADATA: asks about the schema itself (what tables/columns exist).
- CHITCHAT: greeting or small talk, no data needed.
- UNSUPPORTED: asks to modify data, or is outside the database's scope.

Reply with the single word only."""

GENERATE_SYSTEM = """You write a single read-only SQL SELECT statement.

Rules, all mandatory:
- Exactly one statement. No semicolons, no comments, no CTE tricks to hide writes.
- SELECT only. Never INSERT, UPDATE, DELETE, DDL, or any write.
- Use only the tables and columns given in the schema below. Never guess a name.
- Qualify tables with their schema (for example public.orders).
- Do not add a LIMIT; the platform applies one.
- Prefer explicit JOINs using the listed foreign keys.
- Dialect: {dialect}

Schema:
{schema}

{history}"""

GENERATE_USER = """Question: {question}

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
- If the result is empty, say so plainly and suggest what might be missing."""

ANSWER_USER = """Question: {question}

SQL that ran:
{sql}

Result ({row_count} rows):
{result}"""

CHART_SYSTEM = """You choose a chart for a query result, or decline.

Return JSON matching ChartIntent. Use chart_type "none" when the result is a
single value, has no natural category axis, or would be misleading as a chart.
Only reference column names that appear in the result schema."""
