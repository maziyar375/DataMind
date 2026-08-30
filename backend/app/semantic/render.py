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

**How the cap is spent.** The block is a budget allocation, not a paragraph
that happens to be trimmed. Over `max_chars` it is fitted *line by line*, in
priority order, and never truncated mid-sentence — half a metric definition is
a lie the model believes, and a whole section dropped is the feature switched
off. See `_fit_entities` for the tiers and why they are ordered that way.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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

_ENTITY_HEADER = (
    "What these tables mean (business names, grain, and defined "
    "measures — prefer a defined measure over writing your own):\n"
)
_JOIN_HEADER = "Join cautions:\n"
_GLOSSARY_HEADER = "Business terms:\n"

# Detail lines sit under their entity's head line.
_INDENT = "    "


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
    return render_with_coverage(
        doc, tables=tables, budget=budget, max_chars=max_chars
    )[0]


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
    words?". So the answer has to be what `render_with_coverage` *did*, not what
    the document contains — a table with an entry that renders to nothing has
    not been described, and neither has a column whose line did not fit under
    the cap.

    Which is why this is a projection of the render rather than a second walk
    over the document: two predicates that must agree forever are one
    predicate, or they drift and the model is told about `orders` twice.

    A table counts as covered only when the layer says something about the
    *table* — a label, a grain, a role, a date column, a synonym. An entity that
    renders solely because one of its columns did leaves the table itself
    undescribed, and its DDL comment is still the only sentence about it.
    """
    _, covered_tables, covered_columns = render_with_coverage(
        doc, tables=tables, budget=budget, max_chars=max_chars
    )
    return covered_tables, covered_columns


def render_with_coverage(
    doc: SemanticDocument,
    *,
    tables: list[str],
    budget: HintBudget,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, set[str], set[str]]:
    """The block, and the tables and columns it turned out to speak about.

    One function because they are one answer computed once: what fitted under
    the cap *is* what is covered. Callers that need both (`RetrievedContext`)
    take this; `render_semantic` and `covered_keys` are the two projections of
    it, kept for callers that need only one half.
    """
    if doc.is_empty:
        return "", set(), set()

    wanted = {t.lower() for t in tables}
    block = _Block(max_chars)

    # The global lines first, and they are atomic: each is one fact, so there
    # is no line-by-line fit to do inside them. They are also ~200 chars
    # against an 8,000-char cap, so they crowd out nothing worth having.
    #
    # `default_exclusions` is phrased as an instruction rather than a fact and
    # sits second deliberately: it is the one line here that changes the SQL
    # rather than the reading of it, so it goes where nothing can push it out.
    for part in _global_parts(doc):
        if not block.add(part):
            break
    if block.overflowed:
        # A single global part longer than the whole cap — pathological input,
        # and the only place a sentence is still cut short.
        return block.render()[:max_chars], set(), set()

    entities = [
        rendered
        for rendered in (_entity_lines(e, budget) for e in _scoped(doc, wanted))
        if rendered.speaks or rendered.columns or rendered.metrics
    ]
    kept = _fit_entities(entities, block.room_for(_ENTITY_HEADER))
    lines = _entity_section(entities, kept)
    if lines:
        block.add(_ENTITY_HEADER + "\n".join(lines))

    _fit_lines(block, _JOIN_HEADER, _render_joins(doc, wanted))
    _fit_lines(block, _GLOSSARY_HEADER, _render_glossary(doc))

    covered_tables = {
        entity.key
        for entity, keep in zip(entities, kept, strict=True)
        if keep.head and entity.speaks
    }
    covered_columns = {
        entity.columns[i][0]
        for entity, keep in zip(entities, kept, strict=True)
        for i in keep.columns
    }
    return block.render(), covered_tables, covered_columns


# ── fitting ──────────────────────────────────────────────────────────────
class _Block:
    """Parts joined by a blank line, never longer than `max_chars`."""

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max_chars
        self.parts: list[str] = []
        self.overflowed = False

    @property
    def _used(self) -> int:
        return len(self.render())

    def add(self, part: str) -> bool:
        """Append `part` if it fits whole. False when it does not."""
        cost = len(part) + (2 if self.parts else 0)
        if self._used + cost > self.max_chars:
            # The first part is added regardless, so a caller that produced
            # nothing else still gets the clipped fallback above rather than
            # an empty block where a 9,000-char `business_context` was.
            if not self.parts:
                self.parts.append(part)
                self.overflowed = True
            return False
        self.parts.append(part)
        return True

    def room_for(self, header: str) -> int:
        """Chars a section body may use, once its header and separator are paid."""
        return self.max_chars - self._used - (2 if self.parts else 0) - len(header)

    def render(self) -> str:
        return "\n\n".join(self.parts)


@dataclass
class _Entity:
    """One entity's lines, already indented, with the keys they cover."""

    key: str                              # "schema.table", lowered
    head: str
    speaks: bool                          # the head says something about the table
    columns: list[tuple[str, str]] = field(default_factory=list)   # (key, line)
    metrics: list[str] = field(default_factory=list)


@dataclass
class _Keep:
    """Which of one entity's lines fitted."""

    head: bool = False
    columns: set[int] = field(default_factory=set)
    metrics: set[int] = field(default_factory=set)


def _fit_entities(entities: list[_Entity], room: int) -> list[_Keep]:
    """Which lines of which entities fit in `room`, and in what priority.

    The bug this replaces: the section was one part, so the moment it did not
    fit it was dropped whole and a 42-table layer reached the model as its
    `business_context` and nothing else. Fitting it line by line is only half
    the answer, though — filling in document order would spend the whole cap on
    tables 1–6 and say nothing about 7–42, which is the same failure wearing a
    smaller hat.

    So the lines are filled in **tiers**, and each tier goes **round-robin**
    across the entities:

    1. **Every table's head line** — its business name, grain, role, date
       column and synonyms. The highest value per character in the document:
       grain is what makes fan-out reasoning possible, and a table the model
       cannot name it cannot pick.
    2. **Metrics**, one per table per pass. These are the lines that change the
       SQL rather than the reading of it — a definition with its filters is
       exactly what a model cannot infer from a schema.
    3. **Column meanings**, one per table per pass.

    Round-robin because relevance is unknown here: under `FULL_SNAPSHOT` the
    retrieved order is catalog order, which carries no signal about the
    question, so a table with sixty described columns must not silently spend
    the budget forty other tables needed. Within a tier a line that does not
    fit is skipped rather than ending the walk, so one long metric cannot shut
    out the short ones behind it.

    Deterministic in every case: same document, same cap, same bytes.
    """
    keep = [_Keep() for _ in entities]
    if room <= 0:
        return keep
    used = 0

    def head_cost(i: int) -> int:
        return 0 if keep[i].head else len(entities[i].head) + (1 if used else 0)

    for i, entity in enumerate(entities):
        if not entity.speaks:
            continue
        cost = head_cost(i)
        if used + cost <= room:
            keep[i].head = True
            used += cost

    tiers = (
        ([e.metrics for e in entities], [k.metrics for k in keep]),
        ([[line for _, line in e.columns] for e in entities], [k.columns for k in keep]),
    )
    for lines_of, kept_of in tiers:
        depth = max((len(lines) for lines in lines_of), default=0)
        for n in range(depth):
            for i, lines in enumerate(lines_of):
                if n >= len(lines):
                    continue
                cost = head_cost(i) + len(lines[n]) + 1
                if used + cost > room:
                    continue
                keep[i].head = True
                kept_of[i].add(n)
                used += cost

    return keep


def _entity_section(entities: list[_Entity], kept: list[_Keep]) -> list[str]:
    """The kept lines, in document order — the fit decided *what*, not *how it reads*."""
    lines: list[str] = []
    for entity, keep in zip(entities, kept, strict=True):
        if not keep.head:
            continue
        lines.append(entity.head)
        lines += [line for i, (_, line) in enumerate(entity.columns) if i in keep.columns]
        lines += [line for i, line in enumerate(entity.metrics) if i in keep.metrics]
    return lines


def _fit_lines(block: _Block, header: str, lines: list[str]) -> None:
    """Append as many of `lines` as fit under `header`, or no section at all.

    Same rule as the entity section and for the same reason: these sections sit
    behind the tables because they are the least question-specific material in
    the document, but "behind" must mean "gets what is left", not "is deleted".
    """
    room = block.room_for(header)
    if room <= 0 or not lines:
        return
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (1 if used else 0)
        if used + cost > room:
            continue
        kept.append(line)
        used += cost
    if kept:
        block.add(header + "\n".join(kept))


# ── the lines themselves ─────────────────────────────────────────────────
def _global_parts(doc: SemanticDocument) -> list[str]:
    parts: list[str] = []
    if doc.business_context.strip():
        parts.append(f"About this database: {doc.business_context.strip()}")
    if doc.default_exclusions.strip():
        parts.append(
            "Rows to leave out unless the question asks for them: "
            + doc.default_exclusions.strip()
        )
    time_line = _render_time(doc)
    if time_line:
        parts.append(time_line)
    return parts


def _scoped(doc: SemanticDocument, wanted: set[str]) -> list[SemanticEntity]:
    """The entities this block is allowed to speak about, in document order."""
    return [
        e for e in doc.entities
        if e.valid and not e.exclude and e.table.lower() in wanted
    ]


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

    Split out so coverage can ask "does the layer speak about this table?" with
    the renderer's own answer rather than a second opinion. A head equal to
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


def _entity_lines(entity: SemanticEntity, budget: HintBudget) -> _Entity:
    """Everything renderable about one entity, costed as it will be emitted.

    Indented here rather than at emission so the fit prices the real line. An
    entity whose head says nothing and whose body is empty renders to nothing
    at all: a bare table name costs tokens and repeats the schema block.
    """
    key = entity.table.lower()
    head = _entity_head(entity)
    rendered = _Entity(key=key, head=head, speaks=head != f"- {entity.table}")

    for column in entity.columns:
        if not column.valid:
            continue
        described = _render_column(column, budget)
        if described:
            rendered.columns.append(
                (f"{key}.{column.name.lower()}", f"{_INDENT}{described}")
            )

    for metric in entity.metrics:
        if metric.valid:
            rendered.metrics.append(f"{_INDENT}{_render_metric(metric)}")

    return rendered


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


def _render_glossary(doc: SemanticDocument) -> list[str]:
    lines = [
        f"- {term.term}: {term.meaning.rstrip('.')}."
        for term in doc.glossary
        if term.term and term.meaning
    ]
    return lines[:20]
