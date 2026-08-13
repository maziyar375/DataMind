"""Build a semantic document from a schema snapshot, with a model.

Shape of the work, and why:

* **Per table, not per schema.** One call describing forty tables returns
  forty one-line descriptions and no metrics. One call per table returns a
  grain statement and real expressions, which is the part that changes
  answers. Tables run concurrently under a small semaphore, so forty tables
  is a minute, not forty times one.
* **Joins are derived, not asked for.** Cardinality is readable off the
  catalog (`validate.derive_joins`); asking a model to guess it would be
  slower, dearer, and wrong more often.
* **Everything is checked before it is kept.** A generated metric whose
  expression does not parse, or names a column that does not exist, is
  dropped here rather than flagged — an invalid definition nobody wrote is
  noise, and the count is reported so the user can see the model struggled.
  (A definition a *human* wrote is flagged instead, never dropped: see
  `validate.validate_document`.)
* **One bad table does not fail the job.** A model that cannot produce valid
  JSON for `public.weird_table` costs that table's description and nothing
  else.

No I/O and no litellm: generation talks to the `LLMGateway` port, so a fake
gateway drives the whole thing in tests.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from app.core.errors import LLMError
from app.core.logging import get_logger
from app.domain.ports.llm import ChatMessage, LLMGateway, ResolvedLLM
from app.domain.value_objects import HintBudget
from app.semantic.models import (
    Additivity,
    ColumnRole,
    EntityRole,
    GlossaryTerm,
    Provenance,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    TimeSemantics,
)
from app.semantic.prompts import (
    GLOSSARY_SYSTEM,
    GLOSSARY_USER,
    OVERVIEW_SYSTEM,
    OVERVIEW_USER,
    TABLE_SYSTEM,
    TABLE_USER,
)
from app.semantic.validate import SchemaIndex, build_index, check_expression, derive_joins

log = get_logger(__name__)

# Wide enough to keep a big schema moving, narrow enough that a free-tier
# endpoint's rate limiter does not turn the whole job into 429s. The gateway's
# own bounded retry absorbs the rest.
DEFAULT_CONCURRENCY = 4

# Neighbours exist so a cross-table metric can name a real column. More than
# a handful is prompt bloat that pushes the table's own columns out of view.
MAX_NEIGHBOURS = 6


# ── what the model is asked to return ────────────────────────────────────
def _coerce(annotation: Any, value: Any) -> Any:
    """Bend a plausible answer into the shape the field declares.

    Only the confusions models actually make. The common one by far is a list
    written as prose — `"maps_to": "public.orders, public.customers"` where the
    schema says `list[str]` — which is what silently emptied the glossary.

    Commas are split on only when the string carries no bracket or quote: a
    name list is comma-separated, and so is `SUM(a, b)` or `status IN ('a','b')`
    — and cutting a SQL predicate in half would turn a recoverable answer into
    a wrong one. Newlines are always safe to split on.
    """
    if get_origin(annotation) is list:
        if isinstance(value, str):
            separator = r"[\n,;]" if not re.search(r"[('\"\[]", value) else r"[\n]"
            return [part.strip() for part in re.split(separator, value) if part.strip()]
        if not isinstance(value, list):
            return [value]
    if annotation is str and isinstance(value, (int, float, bool)):
        return str(value)
    return value


class _Draft(BaseModel):
    """A model answer read field by field, so one bad field costs one field.

    Defaults alone are not leniency. They cover a field the model *omitted*;
    they do nothing for a field it returned in the wrong shape, and pydantic
    fails the whole object on the first such field. That is how a single
    `maps_to` written as a string threw away an entire glossary, and how one
    malformed `synonyms` threw away a whole table's description — half of them,
    on the schema this was first run against.

    So each field is coerced, then validated on its own; anything that still
    does not fit is dropped and its default stands. Lists of nested drafts are
    salvaged element by element, because one unusable metric is not a reason to
    lose the other five.
    """

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _salvage(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        kept: dict[str, Any] = {}
        for name, field_info in cls.model_fields.items():
            if name not in data:
                continue
            annotation = field_info.annotation
            value = _coerce(annotation, data[name])
            item_type = (get_args(annotation) or (None,))[0]
            if (
                get_origin(annotation) is list
                and isinstance(value, list)
                and isinstance(item_type, type)
                and issubclass(item_type, BaseModel)
            ):
                value = [
                    item for item in value
                    if _fits(item_type, item)
                ]
            if _fits(annotation, value):
                kept[name] = value
        return kept


def _fits(annotation: Any, value: Any) -> bool:
    try:
        TypeAdapter(annotation).validate_python(value)
    except PydanticValidationError:
        return False
    return True


class _Overview(_Draft):
    business_context: str = ""
    industry: str = ""
    default_exclusions: str = ""
    fiscal_year_start_month: int = 1
    week_starts_on: str = "monday"
    timezone: str = "UTC"
    relative_windows: str = "calendar"
    notes: str = ""


class _ColumnDraft(_Draft):
    name: str = ""
    label: str = ""
    description: str = ""
    role: str = "attribute"
    unit: str = ""
    synonyms: list[str] = Field(default_factory=list)
    value_meanings: dict[str, str] = Field(default_factory=dict)


class _MetricDraft(_Draft):
    name: str = ""
    label: str = ""
    description: str = ""
    expression: str = ""
    filters: list[str] = Field(default_factory=list)
    required_joins: list[str] = Field(default_factory=list)
    additive: str = "additive"
    unit: str = ""
    synonyms: list[str] = Field(default_factory=list)


class _TableDraft(_Draft):
    label: str = ""
    description: str = ""
    grain: str = ""
    role: str = "unknown"
    synonyms: list[str] = Field(default_factory=list)
    default_time_column: str = ""
    columns: list[_ColumnDraft] = Field(default_factory=list)
    metrics: list[_MetricDraft] = Field(default_factory=list)


class _GlossaryDraft(_Draft):
    class Term(_Draft):
        term: str = ""
        meaning: str = ""
        maps_to: list[str] = Field(default_factory=list)

    terms: list[Term] = Field(default_factory=list)


# ── progress reporting ───────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Progress:
    phase: str          # human-readable, shown in the UI
    current: int
    total: int


ProgressFn = Callable[[Progress], Awaitable[None]]


@dataclass(slots=True)
class GenerationStats:
    tables_described: int = 0
    tables_failed: list[str] = field(default_factory=list)
    metrics_kept: int = 0
    metrics_dropped: int = 0
    #: The glossary pass ran and could not be used. Distinct from an empty
    #: glossary, which is a legitimate answer — "nothing here needs defining".
    #: Without the flag those two are the same absence on screen.
    glossary_failed: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tables_described": self.tables_described,
            "tables_failed": self.tables_failed,
            "metrics_kept": self.metrics_kept,
            "metrics_dropped": self.metrics_dropped,
            "glossary_failed": self.glossary_failed,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


# ── the generator ────────────────────────────────────────────────────────
async def generate_document(
    *,
    tables: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    dialect: str,
    gateway: LLMGateway,
    llm: ResolvedLLM,
    budget: HintBudget,
    catalog_meta: dict[str, Any] | None = None,
    only_tables: list[str] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: ProgressFn | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[SemanticDocument, GenerationStats]:
    """Describe `tables` and return a validated document.

    `only_tables` narrows the work to a subset (the UI offers this for a
    schema too big to describe in one sitting, and for re-describing a table
    whose meaning changed). `cancelled` is polled between tables so a user
    who changes their mind is not billed for the remainder.

    `catalog_meta` is `schema_snapshots.catalog_meta` — the database and schema
    descriptions the sync picked up. Table and column comments need no
    parameter: they ride inside `tables`, where the connectors put them.
    """
    index = build_index(tables, dialect)
    stats = GenerationStats()
    doc = SemanticDocument()
    catalog = catalog_meta or {}

    wanted = (
        [t for t in tables if _qualified(t) in {n.lower() for n in only_tables}]
        if only_tables
        else list(tables)
    )
    total = len(wanted) + 2  # the overview and the glossary are steps too

    async def report(phase: str, current: int) -> None:
        if on_progress is not None:
            await on_progress(Progress(phase=phase, current=current, total=total))

    # ── pass 1: orientation ──────────────────────────────────────────────
    await report("Reading the shape of the schema", 0)
    overview = await _overview(
        gateway, llm, tables, relationships, dialect, stats, catalog
    )
    # Seeded, not overridden: the model read the catalog description and was
    # asked to prefer it, so what it wrote is that sentence plus what the table
    # names add. The raw comment is the floor — including when the overview pass
    # failed outright, which is the case that used to leave a whole layer with
    # no business context at all.
    doc.business_context = (
        overview.business_context.strip()
        or str(catalog.get("database_comment") or "").strip()
    )
    doc.default_exclusions = overview.default_exclusions.strip()
    doc.time = TimeSemantics(
        fiscal_year_start_month=(
            overview.fiscal_year_start_month
            if 1 <= overview.fiscal_year_start_month <= 12
            else 1
        ),
        week_starts_on=(
            "sunday" if overview.week_starts_on.lower().startswith("sun") else "monday"
        ),
        timezone=overview.timezone.strip() or "UTC",
        relative_windows=(
            "rolling" if overview.relative_windows.lower().startswith("roll")
            else "calendar"
        ),
        notes=overview.notes.strip(),
    )

    # ── pass 2: one call per table, bounded concurrency ──────────────────
    neighbours = _neighbour_map(tables, relationships)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    done = 0
    lock = asyncio.Lock()

    async def describe(table: dict[str, Any]) -> SemanticEntity | None:
        nonlocal done
        if cancelled is not None and cancelled():
            return None
        async with semaphore:
            if cancelled is not None and cancelled():
                return None
            qualified = _qualified(table)
            try:
                entity = await _describe_table(
                    gateway=gateway,
                    llm=llm,
                    table=table,
                    neighbours=[
                        t for t in tables
                        if _qualified(t) in neighbours.get(qualified, set())
                    ][:MAX_NEIGHBOURS],
                    context=doc.business_context,
                    dialect=dialect,
                    budget=budget,
                    index=index,
                    stats=stats,
                )
            except LLMError as err:
                log.warning("semantic_table_failed", table=qualified, error=err.message)
                entity = None
            async with lock:
                done += 1
                if entity is None:
                    stats.tables_failed.append(qualified)
                else:
                    stats.tables_described += 1
                await report(f"Describing {qualified}", done)
            return entity

    described = await asyncio.gather(*(describe(t) for t in wanted))
    doc.entities = [e for e in described if e is not None]

    # ── joins, derived ───────────────────────────────────────────────────
    doc.joins = derive_joins(relationships, index)

    # ── pass 3: glossary ─────────────────────────────────────────────────
    if doc.entities and not (cancelled is not None and cancelled()):
        await report("Writing the glossary", total - 1)
        doc.glossary = await _glossary(gateway, llm, doc, stats)

    await report("Finished", total)
    return doc, stats


# ── pass implementations ─────────────────────────────────────────────────
async def _overview(
    gateway: LLMGateway,
    llm: ResolvedLLM,
    tables: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    dialect: str,
    stats: GenerationStats,
    catalog_meta: dict[str, Any],
) -> _Overview:
    table_lines = "\n".join(
        f"- {_qualified(t)}"
        + (f" (~{t['approx_row_count']:,} rows)" if t.get("approx_row_count") else "")
        + f", {len(t.get('columns', []))} columns"
        for t in tables[:200]
    )
    fk_lines = "\n".join(
        f"- {r['from_table']}.{r['from_column']} -> {r['to_table']}.{r['to_column']}"
        for r in relationships[:200]
    )
    try:
        return await gateway.structured(
            llm,
            [
                ChatMessage(role="system", content=OVERVIEW_SYSTEM),
                ChatMessage(
                    role="user",
                    content=OVERVIEW_USER.format(
                        dialect=dialect,
                        catalog=_catalog_block(catalog_meta),
                        tables=table_lines or "(none)",
                        relationships=fk_lines or "(none)",
                    ),
                ),
            ],
            _Overview,
        )
    except LLMError as err:
        # An orientation note is a nicety; the per-table work is the product.
        log.warning("semantic_overview_failed", error=err.message)
        return _Overview()


async def _describe_table(
    *,
    gateway: LLMGateway,
    llm: ResolvedLLM,
    table: dict[str, Any],
    neighbours: list[dict[str, Any]],
    context: str,
    dialect: str,
    budget: HintBudget,
    index: SchemaIndex,
    stats: GenerationStats,
) -> SemanticEntity | None:
    qualified = _qualified(table)
    draft = await gateway.structured(
        llm,
        [
            ChatMessage(role="system", content=TABLE_SYSTEM),
            ChatMessage(
                role="user",
                content=TABLE_USER.format(
                    dialect=dialect,
                    context=context or "(not established)",
                    table=qualified,
                    table_ddl=_ddl(table, budget),
                    neighbours=(
                        "\n".join(
                            _ddl(n, budget, column_comments=False)
                            for n in neighbours
                        )
                        or "(none)"
                    ),
                ),
            ),
        ],
        _TableDraft,
    )
    return _to_entity(draft, table=table, index=index, budget=budget, stats=stats)


async def _glossary(
    gateway: LLMGateway,
    llm: ResolvedLLM,
    doc: SemanticDocument,
    stats: GenerationStats,
) -> list[GlossaryTerm]:
    lines: list[str] = []
    for entity in doc.entities:
        label = f' "{entity.label}"' if entity.label else ""
        lines.append(f"- {entity.table}{label}: {entity.grain or entity.description}")
        for metric in entity.metrics:
            lines.append(f"    metric {metric.name}: {metric.description}")
    try:
        draft = await gateway.structured(
            llm,
            [
                ChatMessage(role="system", content=GLOSSARY_SYSTEM),
                ChatMessage(
                    role="user",
                    content=GLOSSARY_USER.format(
                        context=doc.business_context or "(not established)",
                        entities="\n".join(lines[:400]),
                    ),
                ),
            ],
            _GlossaryDraft,
        )
    except LLMError as err:
        log.warning("semantic_glossary_failed", error=err.message)
        stats.glossary_failed = True
        return []

    return [
        GlossaryTerm(
            term=t.term.strip().lower(),
            meaning=t.meaning.strip(),
            maps_to=[m.strip() for m in t.maps_to if m.strip()],
            provenance=Provenance(source="llm"),
        )
        for t in draft.terms
        if t.term.strip() and t.meaning.strip()
    ][:12]


# ── draft → entity, with every name checked ──────────────────────────────
def _to_entity(
    draft: _TableDraft,
    *,
    table: dict[str, Any],
    index: SchemaIndex,
    budget: HintBudget,
    stats: GenerationStats,
) -> SemanticEntity:
    qualified = _qualified(table)
    facts = index.table(qualified)
    known = facts.columns if facts else {}

    columns: list[SemanticColumn] = []
    seen: set[str] = set()
    for column in draft.columns:
        name = column.name.strip()
        if name.lower() not in known or name.lower() in seen:
            continue
        seen.add(name.lower())
        columns.append(
            SemanticColumn(
                name=name,
                label=column.label.strip(),
                description=column.description.strip(),
                synonyms=_clean_list(column.synonyms),
                role=_column_role(column.role),
                unit=column.unit.strip(),
                # Only meanings for values the snapshot actually holds; the
                # model cannot widen a disclosure by inventing a key.
                value_meanings=_known_values(column.value_meanings, table, name, budget),
                provenance=Provenance(source="llm"),
            )
        )

    metrics: list[SemanticMetric] = []
    metric_names: set[str] = set()
    for metric in draft.metrics:
        name = _slug(metric.name)
        if not name or name in metric_names:
            continue
        joins = [
            j for j in (index.resolve_suffix(r) for r in metric.required_joins)
            if j is not None
        ]
        valid, issue = check_expression(
            metric.expression,
            entity_table=qualified,
            index=index,
            extra_tables=joins,
        )
        if not valid:
            stats.metrics_dropped += 1
            log.info(
                "semantic_metric_dropped",
                table=qualified, metric=name, reason=issue,
            )
            continue
        filters_ok = all(
            check_expression(
                predicate, entity_table=qualified, index=index,
                extra_tables=joins, boolean=True,
            )[0]
            for predicate in metric.filters
        )
        if not filters_ok:
            stats.metrics_dropped += 1
            continue

        metric_names.add(name)
        stats.metrics_kept += 1
        metrics.append(
            SemanticMetric(
                name=name,
                label=metric.label.strip(),
                description=metric.description.strip(),
                synonyms=_clean_list(metric.synonyms),
                expression=metric.expression.strip().rstrip(";"),
                filters=[f.strip().rstrip(";") for f in metric.filters if f.strip()],
                required_joins=joins,
                additive=_additivity(metric.additive),
                unit=metric.unit.strip(),
                provenance=Provenance(source="llm"),
                issue=issue,
            )
        )

    time_column = draft.default_time_column.strip()
    if time_column.lower() not in known:
        time_column = ""

    entity = SemanticEntity(
        table=qualified,
        label=draft.label.strip(),
        description=draft.description.strip(),
        synonyms=_clean_list(draft.synonyms),
        grain=draft.grain.strip(),
        role=_entity_role(draft.role),
        default_time_column=time_column,
        columns=columns,
        metrics=metrics,
        provenance=Provenance(source="llm"),
    )
    _seed_from_catalog(entity, table)
    return entity


def _seed_from_catalog(entity: SemanticEntity, table: dict[str, Any]) -> None:
    """Fill the gaps the model left with the catalog's own descriptions.

    Prompting alone is not enough. It leaves the DBA's sentence invisible in the
    editor and dependent on the model echoing it, and a model that skipped a
    column — or a table it could say nothing about — drops the one piece of
    business-accurate documentation that already existed. Promoting the comment
    into the document is what makes the run-time suppression rule sound rather
    than lossy: nothing is lost by rendering the layer instead of the raw
    comment, because the layer is where the comment went.

    Gaps only. A description the model wrote is a description of *this* schema
    written with the comment in front of it; overwriting it with the raw comment
    would trade a considered sentence for its own input.

    `source = "derived"` rather than `"human"` — it is the value already used
    for facts read off the catalog rather than invented (`SemanticJoin`), and it
    leaves `provenance.edited` meaning exactly what it means today, *a person
    touched this in our UI*, so `merge_documents` and REPLACE keep working.
    """
    table_comment = str(table.get("comment") or "").strip()
    if table_comment and not entity.description:
        entity.description = table_comment
        entity.provenance = Provenance(source="derived")

    described = {c.name.lower(): c for c in entity.columns}
    for column in table.get("columns", []):
        comment = str(column.get("comment") or "").strip()
        name = str(column.get("name") or "").strip()
        if not comment or not name:
            continue
        existing = described.get(name.lower())
        if existing is None:
            # A column the model did not mention at all. The prompt tells it to
            # skip the self-evident ones — but a DBA who wrote a sentence about
            # this column had already decided it was not one of those.
            entity.columns.append(
                SemanticColumn(
                    name=name,
                    description=comment,
                    provenance=Provenance(source="derived"),
                )
            )
        elif not existing.description:
            existing.description = comment
            existing.provenance = Provenance(source="derived")


# ── prompt rendering ─────────────────────────────────────────────────────
def _ddl(
    table: dict[str, Any], budget: HintBudget, *, column_comments: bool = True
) -> str:
    """One table as the model sees it: names, types, keys, and gated hints.

    Deliberately the same information the generate prompt gets, so a semantic
    layer can never be built from data the disclosure policy would not have
    shown the model anyway. Catalog comments keep that invariant rather than
    bending it: a comment is DDL a human wrote, it does not change when a row
    changes, and it travels with structure under every policy — so it is
    exactly as available to the run prompt as it is here.

    `column_comments` is off for the neighbour tables. A neighbour is rendered
    so a cross-table metric can name a real column, not so the model can read
    about it, and six neighbours' worth of column prose is the difference
    between a prompt and a document.
    """
    lines = [f"{_qualified(table)}"]
    if budget.row_counts and table.get("approx_row_count"):
        lines[0] += f"  (~{table['approx_row_count']:,} rows)"
    if table.get("comment"):
        lines[0] += f' "{table["comment"]}"'
    for column in table.get("columns", []):
        bits = [f"  {column['name']} {column.get('data_type', '')}".rstrip()]
        if column.get("is_primary_key"):
            bits.append("PK")
        if column.get("is_foreign_key") and column.get("references"):
            bits.append(f"FK->{column['references']}")
        if not column.get("nullable", True):
            bits.append("NOT NULL")
        values = column.get("sample_values") or []
        if budget.value_lists and values:
            shown = list(values)[: budget.max_values]
            bits.append("values {" + ", ".join(shown) + "}")
        elif budget.stats and column.get("distinct_count") is not None:
            bits.append(f"{column['distinct_count']} distinct")
        if budget.temporal_range and column.get("min_value") and column.get("max_value"):
            bits.append(f"{column['min_value']}…{column['max_value']}")
        if column_comments and column.get("comment"):
            bits.append(f'"{column["comment"]}"')
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _catalog_block(catalog_meta: dict[str, Any]) -> str:
    """What the catalog says one level above a table, for the overview pass.

    Empty when the database carries neither — which is MySQL and Oracle always
    (neither engine has a database or schema comment at all), and every other
    database whose owner never wrote one. The overview prompt then reads exactly
    as it did before, minus its new rule.
    """
    lines: list[str] = []
    database = str(catalog_meta.get("database_comment") or "").strip()
    if database:
        lines.append(f"About this database (from the database catalog): {database}")
    schemas = catalog_meta.get("schema_comments") or {}
    if isinstance(schemas, dict) and schemas:
        described = [
            f"- {name}: {str(text).strip()}"
            for name, text in schemas.items()
            if str(text).strip()
        ]
        if described:
            lines.append(
                "Schemas (from the database catalog):\n" + "\n".join(described)
            )
    return "\n" + "\n\n".join(lines) + "\n" if lines else ""


def _neighbour_map(
    tables: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> dict[str, set[str]]:
    """One foreign-key hop in either direction, per table."""
    names = {_qualified(t) for t in tables}
    out: dict[str, set[str]] = {n: set() for n in names}
    for rel in relationships:
        left = str(rel.get("from_table", "")).lower()
        right = str(rel.get("to_table", "")).lower()
        if left in out and right in names:
            out[left].add(right)
        if right in out and left in names:
            out[right].add(left)
    return out


# ── coercion ─────────────────────────────────────────────────────────────
def _qualified(table: dict[str, Any]) -> str:
    return f"{table.get('schema', '')}.{table.get('name', '')}".lower()


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text.lower() not in {v.lower() for v in out}:
            out.append(text)
    return out[:8]


def _slug(name: str) -> str:
    cleaned = "".join(
        c if c.isalnum() else "_" for c in str(name).strip().lower()
    ).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned[:60]


def _column_role(value: str) -> ColumnRole:
    role = str(value).strip().lower()
    if role in {"key", "time", "dimension", "measure", "attribute"}:
        return role  # type: ignore[return-value]
    if role in {"date", "timestamp", "datetime"}:
        return "time"
    if role in {"metric", "fact", "numeric"}:
        return "measure"
    return "attribute"


def _entity_role(value: str) -> EntityRole:
    role = str(value).strip().lower()
    if role in {"fact", "dimension", "bridge", "lookup"}:
        return role  # type: ignore[return-value]
    if role in {"junction", "link", "association"}:
        return "bridge"
    return "unknown"


def _additivity(value: str) -> Additivity:
    text = str(value).strip().lower().replace("-", "_")
    if text in {"additive", "semi_additive", "non_additive"}:
        return text  # type: ignore[return-value]
    if text.startswith("semi"):
        return "semi_additive"
    if text.startswith("non"):
        return "non_additive"
    return "additive"


def _known_values(
    meanings: dict[str, str],
    table: dict[str, Any],
    column_name: str,
    budget: HintBudget,
) -> dict[str, str]:
    if not budget.value_lists or not meanings:
        return {}
    column = next(
        (c for c in table.get("columns", []) if c["name"].lower() == column_name.lower()),
        None,
    )
    known = {str(v) for v in (column or {}).get("sample_values") or []}
    if not known:
        return {}
    return {
        str(k): str(v).strip()
        for k, v in meanings.items()
        if str(k) in known and str(v).strip()
    }
