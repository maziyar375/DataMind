"""The words a semantic layer speaks, read off the typed model.

The knowledge backlog's *"words nothing recognised"* compares a question against
everything a connection can be said to know, and the layer is most of that. It
used to read the stored JSON as a dict and asked for `business_name` on
entities, columns and metrics and `synonyms` on glossary terms. None of those
keys ever existed, so every label a curator wrote was treated as an unknown
word, and the backlog suggested teaching words the layer already explained.

The read is typed here so that renaming a field breaks a test rather than
quietly breaking a feature. `app.knowledge` sits below `app.semantic` in the
layers contract and cannot import this; the service layer calls it and hands
the backlog plain strings.
"""
from __future__ import annotations

from app.semantic.models import SemanticDocument


def vocabulary_terms(doc: SemanticDocument) -> frozenset[str]:
    """Every name, label and synonym the layer contributes, as written.

    Phrases rather than words: the caller splits them the same way it splits
    physical names, so `"Net revenue"` and `public.order_items` are tokenised
    by one rule.

    **What the renderer keeps out of the prompt contributes nothing here.** An
    excluded entity, and an entity, column or metric the binder flagged, never
    reaches the model, so a word only they carry is a word retrieval had no way
    to resolve. Pass a document bound to the current snapshot; the stored flags
    are not current (`bind.py`).

    Glossary terms contribute their `term` and the names in `maps_to`. The
    `meaning` does not: it is a sentence of explanation, and every word in it
    would read as vocabulary the connection knows.
    """
    terms: set[str] = set()
    for entity in doc.entities:
        if entity.exclude or not entity.valid:
            continue
        terms.update((entity.table, entity.label, *entity.synonyms))
        for column in entity.columns:
            if column.valid:
                terms.update((column.name, column.label, *column.synonyms))
        for metric in entity.metrics:
            if metric.valid:
                terms.update((metric.name, metric.label, *metric.synonyms))
    for term in doc.glossary:
        terms.update((term.term, *term.maps_to))
    terms.discard("")
    return frozenset(terms)
