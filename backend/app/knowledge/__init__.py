"""Knowledge templates: the questions this connection has been taught.

`app.sqlguard` decides whether a query is safe and `app.semantic` decides
whether the model had a chance of writing the right one. This package holds the
third thing: a question somebody already answered correctly, stored as a
parameterized question→SQL pair so it answers a *family* of askings rather than
one literal string.

Self-contained on the same terms as `sqlguard` and `semantic` — no fastapi, no
sqlalchemy, no litellm, no `app.infra`, no `app.services`. A package that
parses SQL and normalises strings has no business knowing what a session is,
and the contract in `pyproject.toml` is what keeps that true.

    normalize_question      question → the match key
    propose_params          AST walk → parameters, offered with reasons
    parameterize            replace the ticked literals with `:slots`
    validate_template       the guard's fifth entry point, plus slot agreement

Phase 2 adds `matcher.py` and `bind.py` beside these; Phase 6 adds
`compare.py`. See `docs/learning-loop-plan.md`.
"""
from __future__ import annotations

from app.knowledge.backlog import (
    RANK,
    STOPWORDS,
    Suggestion,
    SuggestionKind,
    build_vocabulary,
    rank_suggestions,
    unknown_words,
)
from app.knowledge.models import (
    KnowledgeTemplate,
    LiteralProvenance,
    ParamType,
    TemplateParam,
    TemplateRole,
    TemplateSource,
    TemplateStatus,
    may_render_literals,
)
from app.knowledge.normalize import (
    MASK,
    example_questions,
    normalize_question,
    slots,
)
from app.knowledge.params import (
    DEFAULT_SUGGESTED,
    ColumnFacts,
    ParamProposal,
    SchemaFacts,
    column_type,
    parameterize,
    placeholder,
    propose_params,
)
from app.knowledge.validate import (
    DRIFT_RULES,
    TemplateVerdict,
    declared_placeholders,
    policy_from_tables,
    validate_sql,
    validate_template,
)

__all__ = [
    "DEFAULT_SUGGESTED",
    "RANK",
    "STOPWORDS",
    "DRIFT_RULES",
    "MASK",
    "ColumnFacts",
    "KnowledgeTemplate",
    "LiteralProvenance",
    "ParamProposal",
    "ParamType",
    "SchemaFacts",
    "Suggestion",
    "SuggestionKind",
    "TemplateParam",
    "TemplateRole",
    "TemplateSource",
    "TemplateStatus",
    "TemplateVerdict",
    "build_vocabulary",
    "column_type",
    "may_render_literals",
    "declared_placeholders",
    "example_questions",
    "normalize_question",
    "parameterize",
    "placeholder",
    "policy_from_tables",
    "propose_params",
    "rank_suggestions",
    "slots",
    "unknown_words",
    "validate_sql",
    "validate_template",
]
