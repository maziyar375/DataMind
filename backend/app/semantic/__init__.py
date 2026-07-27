"""The semantic layer: what the schema *means*.

`app.sqlguard` decides whether a query is safe; this decides whether the model
had a chance of writing the right one. It is self-contained the same way —
no fastapi, no sqlalchemy, no litellm — so it can be exercised end to end
against a dict snapshot and a fake gateway.

    build_index / validate_document   bind a document to a schema snapshot
    derive_joins                      cardinality + fan-out, read off the catalog
    generate_document                 build one with a model, table by table
    render_semantic                   the block the generator prompt receives
"""
from __future__ import annotations

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
from app.semantic.render import render_semantic
from app.semantic.validate import (
    SchemaIndex,
    build_index,
    check_expression,
    derive_joins,
    entity_stub,
    merge_documents,
    validate_document,
)

__all__ = [
    "DOCUMENT_VERSION",
    "SEMANTIC_PROMPT_VERSION",
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
    "build_index",
    "check_expression",
    "derive_joins",
    "entity_stub",
    "generate_document",
    "merge_documents",
    "render_semantic",
    "validate_document",
]
