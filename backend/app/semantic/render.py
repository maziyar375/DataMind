"""The semantic layer as the generator sees it.

Three rules the shape follows, all of them learned from the eval rather than
chosen for elegance:

1. **Scoped.** Only the entities that survived retrieval are rendered. The
   layer for a 42-table connection is several times the size of the schema
   block; sending all of it would blow the retrieve budget the fixtures were
   deliberately sized against.
2. **Silent when empty.** A connection with no layer produces *no* block at
   all — the rendered prompt stays byte-identical to the one the current
   baseline was measured on, so this feature cannot move the number until a
   user actually generates a layer.
3. **Terse.** Round 2 of the eval showed the generate prompt punishes
   unconditional additions: a block of general SQL guidance cost the small
   model ten points. Everything here is schema-specific and one line long;
   nothing here is advice.

Invalid entries are never rendered. A definition that no longer resolves
against the snapshot is exactly the thing that must not reach the model.
"""
from __future__ import annotations

from app.domain.value_objects import HintBudget
from app.semantic.models import (
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
)

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

# The cap `RetrievedContext` renders under. Named so `covered_keys` can default
# to the same one: the two answer the same question about the same block, and a
# caller that passed a different budget to each would be told a table was
# described when it had been trimmed away.
DEFAULT_MAX_CHARS = 8_000


def render_semantic(
    doc: SemanticDocument,
    *,
    tables: list[str],
    budget: HintBudget,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """The semantic block for `tables`, or `""` when there is nothing to say.

    `budget` is the same disclosure gate the schema block runs under: value
    meanings are keyed by real column values, so they travel under the policy
    that governs values, not the one that governs structure.
    """
    if doc.is_empty:
        return ""

    wanted = {t.lower() for t in tables}
    entities = _scoped(doc, wanted)

    parts: list[str] = []
    if doc.business_context.strip():
        parts.append(f"About this database: {doc.business_context.strip()}")

    # Second, and phrased as an instruction rather than a fact. It is the one
    # line here that changes the SQL rather than the reading of it, and the
    # tail of this function drops sections from the back when the block is over
    # budget — so a rule that silently doubles a total has to sit near the front.
    if doc.default_exclusions.strip():
        parts.append(
            "Rows to leave out unless the question asks for them: "
            + doc.default_exclusions.strip()
        )

    time_line = _render_time(doc)
    if time_line:
        parts.append(time_line)

    entity_lines = [_render_entity(e, budget) for e in entities]
    entity_lines = [line for line in entity_lines if line]
    if entity_lines:
        parts.append(
            "What these tables mean (business names, grain, and defined "
            "measures — prefer a defined measure over writing your own):\n"
            + "\n".join(entity_lines)
        )

    join_lines = _render_joins(doc, wanted)
    if join_lines:
        parts.append("Join cautions:\n" + "\n".join(join_lines))

    glossary = _render_glossary(doc)
    if glossary:
        parts.append("Business terms:\n" + glossary)

    if not parts:
        return ""

    block = "\n\n".join(parts)
    if len(block) <= max_chars:
        return block

    # Over budget: drop from the back, where the least question-specific
    # material lives, rather than truncating mid-sentence.
    while len(block) > max_chars and len(parts) > 1:
        parts.pop()
        block = "\n\n".join(parts)
    return block[:max_chars]


def _scoped(doc: SemanticDocument, wanted: set[str]) -> list[SemanticEntity]:
    """The entities this block is allowed to speak about, in document order."""
    return [
        e for e in doc.entities
        if e.valid and not e.exclude and e.table.lower() in wanted
    ]


def covered_keys(
    doc: SemanticDocument,
    *,
    tables: list[str],
    budget: HintBudget,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[set[str], set[str]]:
    """The tables and `"table.column"` keys the rendered block actually speaks about.

    The question the caller is really asking is "may I render the raw DDL
    comment for this table, or would that describe it twice in different
    words?". So the answer has to be what `render_semantic` *did*, not what the
    document contains — a table with an entry that renders to nothing has not
    been described, and neither has one whose section was trimmed off the back
    when the block went over budget.

    Which is why this renders the block and reads its own output back rather
    than re-deriving the rules: two predicates that must agree forever are one
    predicate, or they drift and the model is told about `orders` twice. The
    same reason `_entity_head` exists.

    A table counts as covered only when the layer says something about the
    *table* — a label, a grain, a role, a date column, a synonym. An entity that
    renders solely because one of its columns did leaves the table itself
    undescribed, and its DDL comment is still the only sentence about it.
    """
    if doc.is_empty:
        return set(), set()

    block = render_semantic(doc, tables=tables, budget=budget, max_chars=max_chars)
    if not block:
        return set(), set()

    covered_tables: set[str] = set()
    covered_columns: set[str] = set()
    for entity in _scoped(doc, {t.lower() for t in tables}):
        rendered = _render_entity(entity, budget)
        if not rendered or rendered not in block:
            continue
        key = entity.table.lower()
        if _entity_head(entity) != f"- {entity.table}":
            covered_tables.add(key)
        for column in entity.columns:
            if column.valid and _render_column(column, budget):
                covered_columns.add(f"{key}.{column.name.lower()}")
    return covered_tables, covered_columns


def _render_time(doc: SemanticDocument) -> str:
    time = doc.time
    bits: list[str] = []
    if time.fiscal_year_start_month != 1:
        bits.append(
            f"the fiscal year starts in {_MONTHS[time.fiscal_year_start_month - 1]}"
        )
    bits.append(f"weeks start on {time.week_starts_on.capitalize()}")
    if time.timezone and time.timezone != "UTC":
        bits.append(f"timestamps are {time.timezone}")
    bits.append(
        'phrases like "last month" mean whole calendar periods'
        if time.relative_windows == "calendar"
        else 'phrases like "last month" mean a rolling window ending today'
    )
    if time.notes.strip():
        bits.append(time.notes.strip().rstrip("."))
    return "Time conventions: " + "; ".join(bits) + "."


def _entity_head(entity: SemanticEntity) -> str:
    """The entity's own line — everything the layer says about the *table*.

    Split out so `covered_keys` can ask "does the layer speak about this table?"
    with the renderer's own answer rather than a second opinion. A head equal to
    `- schema.table` says nothing; anything longer does.
    """
    head = f"- {entity.table}"
    if entity.label:
        head += f' ("{entity.label}")'
    tail: list[str] = []
    if entity.grain:
        tail.append(entity.grain.rstrip("."))
    if entity.role != "unknown":
        tail.append(f"{entity.role} table")
    if entity.default_time_column:
        tail.append(f"date column: {entity.default_time_column}")
    if entity.synonyms:
        tail.append("also called " + ", ".join(entity.synonyms[:4]))
    if tail:
        head += ": " + "; ".join(tail) + "."
    return head


def _render_entity(entity: SemanticEntity, budget: HintBudget) -> str:
    head = _entity_head(entity)
    lines = [head]

    for column in entity.columns:
        if not column.valid:
            continue
        described = _render_column(column, budget)
        if described:
            lines.append(f"    {described}")

    for metric in entity.metrics:
        if not metric.valid:
            continue
        lines.append(f"    {_render_metric(metric)}")

    # A bare table name with nothing said about it is worse than nothing: it
    # costs tokens and repeats the schema block.
    return "\n".join(lines) if len(lines) > 1 or len(head) > len(f"- {entity.table}") else ""


def _render_column(column: SemanticColumn, budget: HintBudget) -> str:
    bits: list[str] = []
    if column.label and column.label.lower() != column.name.lower():
        bits.append(f'"{column.label}"')
    if column.description:
        bits.append(column.description.rstrip("."))
    if column.unit:
        bits.append(f"in {column.unit}")
    if column.synonyms:
        bits.append("also called " + ", ".join(column.synonyms[:3]))
    # Keys are drawn from the data, so they ride the value-list gate.
    if budget.value_lists and column.value_meanings:
        pairs = list(column.value_meanings.items())[: budget.max_values or 25]
        bits.append("values: " + ", ".join(f"{k} = {v}" for k, v in pairs))
    if not bits:
        return ""
    return f"{column.name}: " + "; ".join(bits) + "."


def _render_metric(metric: SemanticMetric) -> str:
    line = f"metric {metric.name} = {metric.expression}"
    if metric.filters:
        line += " WHERE " + " AND ".join(metric.filters)
    extras: list[str] = []
    if metric.required_joins:
        extras.append("needs " + ", ".join(metric.required_joins))
    if metric.additive != "additive":
        extras.append(metric.additive.replace("_", "-") + ": do not sum it")
    if metric.unit:
        extras.append(metric.unit)
    if metric.description:
        extras.append(metric.description.rstrip("."))
    if metric.synonyms:
        extras.append("asked for as " + ", ".join(metric.synonyms[:4]))
    if extras:
        line += " — " + "; ".join(extras)
    return line + "."


def _render_joins(doc: SemanticDocument, wanted: set[str]) -> list[str]:
    lines: list[str] = []
    for join in doc.joins:
        if not join.fan_out_warning:
            continue
        if join.left.lower() not in wanted or join.right.lower() not in wanted:
            continue
        lines.append(f"- {join.on}: {join.fan_out_warning}")
    return lines[:12]


def _render_glossary(doc: SemanticDocument) -> str:
    lines = [
        f"- {term.term}: {term.meaning.rstrip('.')}."
        for term in doc.glossary
        if term.term and term.meaning
    ]
    return "\n".join(lines[:20])
