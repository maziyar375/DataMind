"""Answering "what tables do I have?" from the schema snapshot.

A METADATA question halts before any SQL, so the whole answer is a rendering of
the snapshot — which makes the rendering the entire product for these questions.

The rule this module exists to enforce is granularity: answer what was asked,
at the level it was asked. "What tables do I have?" wants an inventory — names,
sizes, shapes — and a full column dump buries that under ten times its weight
in detail (on a 42-table schema, ~600 column names, most of them the same audit
columns repeated on every table). "What columns does orders have?" wants the
opposite. One format cannot serve both, so there are two, chosen by matching the
question against the snapshot's own table names — no model call, in keeping with
a node that deliberately costs nothing.

Kept separate from the model-facing `_describe_schema`, which the follow-up
suggestions prompt uses and which *should* stay exhaustive: a model proposing
questions needs every column name, a person reading an answer does not.

Pure functions over the snapshot dict — no I/O, like `disclosure.py`.
"""
from __future__ import annotations

import re
from typing import Any

# Above this the inventory is itself a wall, so it is cut with a count of the
# rest. Well past any schema a person reads end to end, and far below the point
# where the answer stops being an answer.
MAX_LISTED_TABLES = 60
# Detail is bounded because "describe orders and customers and products" should
# not reproduce the wall this module exists to avoid.
MAX_DETAILED_TABLES = 3

_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(question: str) -> set[str]:
    return set(_WORD.findall(question.lower()))


def _names_for(table: dict[str, Any]) -> set[str]:
    """Every spelling of a table name a person might reasonably type.

    Snake case is a storage convention, not how anyone speaks: someone asking
    about `customer_addresses` writes "customer addresses", and someone asking
    about `orders` may write "order". Both are the same table.
    """
    name = str(table.get("name", "")).lower()
    if not name:
        return set()
    forms = {name, name.replace("_", " ")}
    forms.add(name[:-1] if name.endswith("s") else f"{name}s")
    return {f for f in forms if len(f) > 2}


def match_tables(question: str, tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tables the question names explicitly, in snapshot order."""
    asked = question.lower()
    tokens = _tokens(question)

    hits: list[tuple[dict[str, Any], set[str]]] = []
    for table in tables:
        forms = _names_for(table)
        # A single-word form must be a whole token — "id" must not match
        # inside "identity" — while a multi-word form is matched as a phrase.
        found = {f for f in forms if (f in tokens) or (" " in f and f in asked)}
        if found:
            hits.append((table, found))

    # The specific name wins over the general one it contains: "customer
    # addresses" names `customer_addresses`, and the `customers` hit inside it
    # is an artefact of the word, not something the user asked about.
    all_forms = [f for _, found in hits for f in found]
    return [
        table
        for table, found in hits
        if not all(
            any(f != other and f in other for other in all_forms) for f in found
        )
    ]


def _shape(table: dict[str, Any]) -> str:
    columns = table.get("columns") or []
    rows = table.get("approx_row_count")
    parts = []
    if rows:
        parts.append(f"~{rows:,} rows")
    parts.append(f"{len(columns)} column{'' if len(columns) == 1 else 's'}")
    return ", ".join(parts)


def _inventory(tables: list[dict[str, Any]]) -> str:
    """Names, sizes and shapes — one short line each, biggest first.

    Size order rather than alphabetical because the unasked half of "what
    tables do I have?" is "where is the data?", and a 2,000-row fact table
    matters more than a six-row lookup that happens to start with 'b'.
    """
    schemas = {str(t.get("schema", "")) for t in tables}
    where = f" in {schemas.pop()}" if len(schemas) == 1 else ""
    head = (
        f"You have {len(tables)} table{'' if len(tables) == 1 else 's'}{where}. "
        "Largest first:"
    )

    ordered = sorted(
        tables,
        key=lambda t: (-(t.get("approx_row_count") or 0), str(t.get("name", ""))),
    )
    shown, rest = ordered[:MAX_LISTED_TABLES], ordered[MAX_LISTED_TABLES:]

    lines = [head, ""]
    for table in shown:
        label = str(table.get("name", ""))
        if not where:  # several schemas in play, so qualify each name
            label = f"{table.get('schema', '')}.{label}"
        lines.append(f"- {label} — {_shape(table)}")
    if rest:
        lines.append(f"…and {len(rest)} more.")

    example = str(shown[0].get("name", "")) if shown else "a table"
    lines += ["", f'Ask "what columns does {example} have?" for any table\'s columns.']
    return "\n".join(lines)


def _column_line(column: dict[str, Any]) -> str:
    notes = []
    if column.get("is_primary_key"):
        notes.append("primary key")
    if column.get("references"):
        notes.append(f"→ {column['references']}")
    suffix = f" — {', '.join(notes)}" if notes else ""
    return f"  - {column.get('name')} ({column.get('data_type')}){suffix}"


def _detail(tables: list[dict[str, Any]]) -> str:
    blocks = []
    for table in tables[:MAX_DETAILED_TABLES]:
        header = f"{table.get('schema')}.{table.get('name')} — {_shape(table)}:"
        columns = table.get("columns") or []
        blocks.append("\n".join([header, *(_column_line(c) for c in columns)]))

    extra = tables[MAX_DETAILED_TABLES:]
    if extra:
        names = ", ".join(str(t.get("name")) for t in extra)
        blocks.append(f"You also asked about {names}; ask about those separately.")
    return "\n\n".join(blocks)


def answer_metadata(question: str, tables: list[dict[str, Any]]) -> str:
    """The user-facing answer to a schema question."""
    if not tables:
        return "This connection has no tables in its current schema snapshot."
    named = match_tables(question, tables)
    if named:
        return _detail(named)
    return _inventory(tables)
