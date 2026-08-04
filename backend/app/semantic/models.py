"""The semantic layer document.

The schema snapshot says what *exists*; this says what it *means*. It is the
difference between `orders(status varchar)` and "one row per customer order;
`status` is the fulfilment state and 'CANCELLED' orders are excluded from
revenue by definition".

Two audiences, one document:

* **the generator**, which reads a rendered text block (see `render.py`) —
  so every field has to earn its tokens;
* **a human in the UI**, who edits it — so every field has to be nameable in
  a form and safe to leave blank.

Nothing here is trusted on arrival. A document is *bound* to a schema snapshot
by `validate.py` before it is stored or rendered: a table that does not exist,
a column that was dropped, or a metric expression that does not parse is
flagged rather than silently handed to the model. A semantic layer that has
drifted from the schema is worse than none, because the model believes it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DOCUMENT_VERSION = 1

ColumnRole = Literal["key", "time", "dimension", "measure", "attribute"]
EntityRole = Literal["fact", "dimension", "bridge", "lookup", "unknown"]
Additivity = Literal["additive", "semi_additive", "non_additive"]
Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]


class Provenance(BaseModel):
    """Who wrote this entry, and does a human stand behind it.

    `edited` is what makes regeneration safe: a merge keeps every entry a
    person touched, so running "Generate" twice never costs a user their work.
    `reviewed` is stronger — it is the flag the UI filters on, and the only
    honest basis for telling the model "this definition is authoritative".
    """

    model_config = ConfigDict(extra="ignore")

    source: Literal["llm", "human", "derived"] = "llm"
    edited: bool = False
    reviewed: bool = False


class SemanticColumn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str                                   # physical column name
    label: str = ""                             # what a person calls it
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    role: ColumnRole = "attribute"
    unit: str = ""
    # Code → meaning, for columns whose values are opaque ('P' → 'pending').
    # Rendered only when the disclosure policy permits value lists, since the
    # keys are drawn from the data.
    value_meanings: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)

    # Set by the validator, never by the model or the UI.
    valid: bool = True
    issue: str = ""


class SemanticMetric(BaseModel):
    """A business measure bound to an exact SQL expression.

    This is the part of the layer that changes answers. "Revenue" stops being
    re-derived per question — including the `filters` that are *part of the
    definition* rather than part of the question ("cancelled orders are not
    revenue"), which is exactly what a model cannot infer from a schema.
    """

    model_config = ConfigDict(extra="ignore")

    name: str                                   # snake_case identifier
    label: str = ""
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    # A SQL aggregate over columns of this entity (and only this entity, plus
    # anything reachable through `required_joins`). Validated with sqlglot.
    expression: str = ""
    # Predicates the definition always carries, independent of the question.
    filters: list[str] = Field(default_factory=list)
    # Qualified table names this expression needs joined in.
    required_joins: list[str] = Field(default_factory=list)
    additive: Additivity = "additive"
    unit: str = ""
    format: str = ""
    provenance: Provenance = Field(default_factory=Provenance)

    valid: bool = True
    issue: str = ""


class SemanticEntity(BaseModel):
    """One physical table, described in business terms."""

    model_config = ConfigDict(extra="ignore")

    table: str                                  # "schema.name", as in the snapshot
    label: str = ""
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    # "one row per order line item" — the single most useful sentence in the
    # document, because grain is what makes fan-out reasoning possible.
    grain: str = ""
    role: EntityRole = "unknown"
    # Which column *is* "the" date for this entity. Facts routinely carry
    # three plausible timestamps and the model otherwise guesses.
    default_time_column: str = ""
    columns: list[SemanticColumn] = Field(default_factory=list)
    metrics: list[SemanticMetric] = Field(default_factory=list)
    # Excluded from the prompt entirely — deprecated tables, staging copies.
    exclude: bool = False
    provenance: Provenance = Field(default_factory=Provenance)

    valid: bool = True
    issue: str = ""

    @property
    def edited(self) -> bool:
        """True if a person touched this entity or anything inside it."""
        return (
            self.provenance.edited
            or any(c.provenance.edited for c in self.columns)
            or any(m.provenance.edited for m in self.metrics)
        )


class SemanticJoin(BaseModel):
    """A foreign key with the two things an FK edge does not tell you:
    which way it fans out, and what that breaks."""

    model_config = ConfigDict(extra="ignore")

    left: str                                   # qualified table
    right: str                                  # qualified table
    on: str                                     # "left.col = right.col"
    cardinality: Cardinality = "many_to_one"
    # "joining orders to order_items repeats orders rows — do not SUM
    # orders.total after this join". Derived, not invented.
    fan_out_warning: str = ""
    provenance: Provenance = Field(default_factory=lambda: Provenance(source="derived"))


class GlossaryTerm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    term: str
    meaning: str
    maps_to: list[str] = Field(default_factory=list)   # entities or metric names
    provenance: Provenance = Field(default_factory=Provenance)


class TimeSemantics(BaseModel):
    """House conventions for the phrases every business question contains.

    The eval's residual failures were rolling-vs-calendar window ambiguity;
    this block is the answer to that class, and it costs about 40 tokens.
    """

    model_config = ConfigDict(extra="ignore")

    fiscal_year_start_month: int = 1            # 1–12
    week_starts_on: Literal["monday", "sunday"] = "monday"
    timezone: str = "UTC"
    # What "last month" / "last 30 days" mean when the user does not say.
    relative_windows: Literal["calendar", "rolling"] = "calendar"
    notes: str = ""
    provenance: Provenance = Field(default_factory=Provenance)


class SemanticDocument(BaseModel):
    """The whole layer for one connection."""

    model_config = ConfigDict(extra="ignore")

    document_version: int = DOCUMENT_VERSION
    # Two or three sentences on what this database is for. Cheap, and it is
    # what lets the model read `dim_cust_x` as customers rather than a guess.
    business_context: str = ""
    # Rows that should not count unless the question asks for them: soft
    # deletes, test accounts, internal orders, staging duplicates.
    #
    # Free text, and deliberately not a list of predicates. A predicate needs a
    # table to bind to, and this is the one statement that applies across all
    # of them — `is_archived = false` is meaningless on the tables that lack
    # the column. `SemanticMetric.filters` already covers the bound case; this
    # covers the question that has no metric, which is where the damage is:
    # "how many customers do we have" is a COUNT(*) over every test account
    # ever created, and nothing in the layer could say otherwise.
    #
    # Advisory, like every other line of this document. The SQL guard enforces
    # what may run; the layer only says what the numbers mean.
    default_exclusions: str = ""
    time: TimeSemantics = Field(default_factory=TimeSemantics)
    entities: list[SemanticEntity] = Field(default_factory=list)
    joins: list[SemanticJoin] = Field(default_factory=list)
    glossary: list[GlossaryTerm] = Field(default_factory=list)

    def entity(self, table: str) -> SemanticEntity | None:
        key = table.lower()
        return next((e for e in self.entities if e.table.lower() == key), None)

    @property
    def metric_count(self) -> int:
        return sum(len(e.metrics) for e in self.entities)

    @property
    def reviewed_count(self) -> int:
        return sum(1 for e in self.entities if e.provenance.reviewed)

    @property
    def issue_count(self) -> int:
        return (
            sum(1 for e in self.entities if not e.valid)
            + sum(1 for e in self.entities for c in e.columns if not c.valid)
            + sum(1 for e in self.entities for m in e.metrics if not m.valid)
        )

    @property
    def is_empty(self) -> bool:
        """No entities and nothing global to say.

        The renderer checks this so that a connection without a layer produces
        a prompt byte-identical to the one the eval baseline was measured on.
        """
        return not self.entities and not self.business_context and not self.glossary
