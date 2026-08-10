"""Answering a question about the schema itself, before any SQL is written.

A METADATA question halts before the generator, so nothing downstream of it
ever runs and the answer has to come out of what is already in memory: the
schema snapshot, and — where the connection has one — the semantic layer, which
is the only place the *meaning* of a table is written down. "What tables do I
have?" is answerable from names alone; "what is in this database?", "what does
`order_items` count?" and "how would I find revenue?" are not, and a list of
table names with row counts is a non-answer to all three.

This module is the deterministic half of that. It decides **which** tables a
schema question is about (`match_tables` when the question names one,
`select_tables` when the snapshot is too wide to send whole) and states the one
thing the schema block cannot state about itself (`census` — how many tables
there are in total, and the names of any the block left out). The `describe`
node in `nodes/` puts those in front of a model together with the semantic
layer, and the model writes the sentence the user reads.

`answer_metadata` is the floor underneath that: the whole answer this module
used to render on its own, kept as the fallback for a provider that fails
mid-sentence and for a snapshot with no tables in it, where there is nothing a
model could add. It answers at the granularity asked — an inventory (names,
sizes, shapes) unless the question names a table, then that table's columns —
because a full column dump buries an inventory question under ten times its
weight in detail (on a 42-table schema, ~600 column names, most of them the
same audit columns repeated on every table).

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
# How many names `census` will spell out for tables the schema block left out.
# A name is a few tokens and it is the entire answer to "is there a table for
# X?", so this is deliberately generous — but a catalogue with thousands of
# tables would otherwise turn the cheapest part of the prompt into the largest.
MAX_CENSUS_NAMES = 200

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


def _qualified(table: dict[str, Any]) -> str:
    return f"{table.get('schema', '')}.{table.get('name', '')}"


def table_chars(table: dict[str, Any]) -> int:
    """A table's weight in the schema block, estimated the way `retrieve` does.

    One estimator for both callers on purpose: `retrieve` decides whether the
    whole snapshot fits the budget with it, and `select_tables` then spends
    that same budget. Two different guesses would let a table be counted in
    and rendered out.
    """
    return 60 + 40 * len(table.get("columns") or [])


def select_tables(
    question: str, tables: list[dict[str, Any]], *, budget_chars: int
) -> list[dict[str, Any]]:
    """Which tables a schema question is described from, in snapshot order.

    Only reached when the snapshot is too wide to send whole. The generator's
    own selector is the wrong one here: it seeds on words the question shares
    with a table or column name, and "what is in this database?" shares none —
    which is exactly the question this path exists for.

    So: every table the question named, then the largest of the rest until the
    budget runs out. Size rather than alphabet because the unasked half of
    "what tables do I have?" is "where is the data?", and a 2,000-row fact
    table matters more than a six-row lookup that starts with 'b'. Nothing is
    silently dropped — `census` names what did not fit.
    """
    named = match_tables(question, tables)
    named_keys = {_qualified(t) for t in named}
    rest = sorted(
        (t for t in tables if _qualified(t) not in named_keys),
        key=lambda t: (-(t.get("approx_row_count") or 0), str(t.get("name", ""))),
    )

    picked: set[str] = set()
    spent = 0
    for table in [*named, *rest]:
        cost = table_chars(table)
        # At least one table always goes in, however wide: a block describing
        # nothing is worse than a block describing one thing.
        if picked and spent + cost > budget_chars:
            break
        picked.add(_qualified(table))
        spent += cost

    return [t for t in tables if _qualified(t) in picked]


def census(
    tables: list[dict[str, Any]], described: list[dict[str, Any]]
) -> str:
    """How much of the schema the block above is, in the block's own terms.

    A model handed twenty tables says the database has twenty tables, and on a
    schema too wide to send whole that is a wrong answer to the commonest
    schema question there is. So the total is stated as a fact, and the names
    that did not fit are listed: a name costs a handful of tokens and is the
    entire answer to "do I have a table for X?".

    Counts and names only — no row counts. Structure travels under every
    disclosure policy, and what is derived from the data does not; the row
    counts in the block above are already gated by `HintBudget`, and a total
    smuggled in here would be the one number that escaped it.
    """
    if not tables:
        return ""

    schemas = sorted({str(t.get("schema", "")) for t in tables if t.get("schema")})
    where = f" in {schemas[0]}" if len(schemas) == 1 else ""
    plural = "" if len(tables) == 1 else "s"
    head = f"This connection has {len(tables)} table{plural}{where}."

    shown = {_qualified(t) for t in described}
    missing = [t for t in tables if _qualified(t) not in shown]
    if not missing:
        return f"{head} Every one of them is described above."

    listed = missing[:MAX_CENSUS_NAMES]
    names = ", ".join(
        str(t.get("name", "")) if where else _qualified(t) for t in listed
    )
    if len(listed) < len(missing):
        names += f", and {len(missing) - len(listed)} more"
    covered = (
        f"{len(described)} of them is described above — the one the question "
        "named, or else the largest"
        if len(described) == 1
        else f"{len(described)} of them are described above — the ones the "
        "question named, and the largest of the rest"
    )
    rest = (
        "One more table exists but is not described; it is named"
        if len(missing) == 1
        else f"The other {len(missing)} exist but are not described; they are named"
    )
    return f"{head} {covered}. {rest}: {names}."


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
