"""The semantic layer: what the schema *means*.

`app.sqlguard` decides whether a query is safe; this decides whether the model
had a chance of writing the right one. It is self-contained the same way —
no fastapi, no sqlalchemy, no litellm — so it can be exercised end to end
against a dict snapshot and a fake gateway.

    bind_layer                        the one binder every reader goes through
    needs_attention                   what in a layer needs a person, and why
    attribute                         which metric definitions a statement matched
    build_index / validate_document   bind a document to a schema snapshot
    derive_joins                      cardinality + fan-out, read off the catalog
    diff_documents                    what changed between two documents, per entry
    generate_document                 build one with a model, table by table
    render_semantic                   the block the generator prompt receives
    covered_keys                      which tables/columns that block speaks about
    render_with_coverage              both at once, from one fit under the cap
    vocabulary_terms                  every name, label and synonym the layer speaks
"""
from __future__ import annotations

from app.semantic.attention import (
    COLUMNS_CHANGED,
    DRAFT_DAYS,
    DRAFT_OLD,
    INVALID,
    METRIC_IGNORED,
    REASONS,
    UNDESCRIBED,
    UNREVIEWED_RELIED_ON,
    Attention,
    Baseline,
    ColumnDrift,
    MetricCount,
    column_drift,
    needs_attention,
)
from app.semantic.attribute import (
    IGNORED,
    UNKNOWN,
    USED,
    AttributionError,
    Verdict,
    attribute,
)
from app.semantic.bind import bind_layer
from app.semantic.diff import AFFECTS_SQL, KINDS, Change, diff_documents
from app.semantic.generator import (
    GenerationStats,
    Progress,
    ProgressFn,
    generate_document,
)
from app.semantic.models import (
    DOCUMENT_VERSION,
    GlossaryTerm,
    Provenance,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticJoin,
    SemanticMetric,
    TimeSemantics,
)
from app.semantic.prompts import SEMANTIC_PROMPT_VERSION
from app.semantic.render import (
    DEFAULT_MAX_CHARS,
    covered_keys,
    render_semantic,
    render_with_coverage,
)
from app.semantic.terms import vocabulary_terms
from app.semantic.validate import (
    SchemaIndex,
    build_index,
    check_expression,
    confine_to_tables,
    derive_joins,
    entity_stub,
    fill_entity,
    merge_documents,
    validate_document,
)

__all__ = [
    "AFFECTS_SQL",
    "COLUMNS_CHANGED",
    "DRAFT_DAYS",
    "DRAFT_OLD",
    "INVALID",
    "METRIC_IGNORED",
    "REASONS",
    "UNDESCRIBED",
    "UNREVIEWED_RELIED_ON",
    "Attention",
    "Baseline",
    "ColumnDrift",
    "MetricCount",
    "column_drift",
    "confine_to_tables",
    "fill_entity",
    "needs_attention",
    "IGNORED",
    "UNKNOWN",
    "USED",
    "AttributionError",
    "Verdict",
    "attribute",
    "DEFAULT_MAX_CHARS",
    "DOCUMENT_VERSION",
    "SEMANTIC_PROMPT_VERSION",
    "KINDS",
    "Change",
    "GenerationStats",
    "GlossaryTerm",
    "Progress",
    "ProgressFn",
    "Provenance",
    "SchemaIndex",
    "SemanticColumn",
    "SemanticDocument",
    "SemanticEntity",
    "SemanticJoin",
    "SemanticMetric",
    "TimeSemantics",
    "bind_layer",
    "build_index",
    "check_expression",
    "covered_keys",
    "derive_joins",
    "diff_documents",
    "entity_stub",
    "generate_document",
    "merge_documents",
    "render_semantic",
    "render_with_coverage",
    "validate_document",
    "vocabulary_terms",
]
