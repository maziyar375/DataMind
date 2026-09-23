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


# A phrase naming more than this many tables is not vocabulary, it is
# boilerplate — "name", "status", "created", the column labels every table in a
# warehouse carries. Keeping it would promote five tables into retrieval's
# business-term tier and push the ones the question actually named below them,
# which is the opposite of what an index is for. Four because a genuine business
# word can legitimately span a small family of tables ("order" naming `orders`,
# `order_items`, `order_promotions`, `order_status_history`), and nothing wider
# than that family narrows a 42-table schema at all.
TERM_MAX_TABLES = 4

# Below this a phrase is not a word anyone typed on purpose. The same floor
# `metadata._names_for` applies to physical names, for the same reason: a
# two-character token matches inside half the English language.
_TERM_MIN_CHARS = 3


def table_terms(doc: SemanticDocument) -> dict[str, frozenset[str]]:
    """Which business phrases name each table — retrieval's half of the layer.

    The layer's labels, synonyms, metric names and glossary already reach the
    *generate* prompt, where they explain a table the retriever has already
    chosen. This is the other direction: **the word someone wrote down is how
    the table gets chosen in the first place.** "Churn" finds
    `subscription_events` because a curator said so once, on the branch where
    retrieval has to choose at all.

    Keyed by `entity.table` lower-cased — "schema.name", as the snapshot spells
    it — so the caller can match against `_key(table)` without a second
    convention. Phrases are returned as written and lower-cased; the caller
    tokenises them exactly as it tokenises physical names, so one rule decides
    what "net revenue" and `order_items` both mean.

    **Physical spellings are deliberately absent.** `metadata.match_tables`
    already matches a table by its own name and by its columns' names, and
    repeating them here would let the layer re-assert a physical hit on the
    business tier — changing the ranking for a reason that has nothing to do
    with business vocabulary. The two sources are disjoint on purpose: that one
    owns how the database spells things, this one owns what people call them.
    A **metric** name is the exception and belongs here, because a metric is a
    layer identifier that appears nowhere in the schema — nothing else can
    match `net_revenue`.

    Binding is respected exactly as `vocabulary_terms` respects it: an excluded
    or invalid entity contributes nothing, and neither does an invalid column or
    metric. A layer entry the renderer keeps out of the prompt must not steer
    retrieval either, or the model is handed a table chosen by a word it was
    never shown. Pass a document bound to the current snapshot (`bind.py`); the
    stored flags are not current.

    A glossary term contributes to whatever its `maps_to` resolves to — an
    entity's table, or a metric name, in which case it names that metric's
    entity. A `maps_to` naming nothing in the document is dropped rather than
    guessed at. The `meaning` never contributes, for the reason
    `vocabulary_terms` gives: it is a sentence of explanation, and every word in
    it would read as a word the connection knows.
    """
    by_table: dict[str, set[str]] = {}

    def add(table: str, *phrases: str) -> None:
        if not table:
            return
        bucket = by_table.setdefault(table.lower(), set())
        for phrase in phrases:
            cleaned = " ".join(str(phrase).split()).lower()
            if len(cleaned) >= _TERM_MIN_CHARS:
                bucket.add(cleaned)

    # Where a metric name points, so a glossary term that maps to one lands on
    # the table it is measured over. First valid definition wins; a name
    # claimed twice is a layer the binder should already have flagged.
    metric_home: dict[str, str] = {}

    for entity in doc.entities:
        if entity.exclude or not entity.valid:
            continue
        add(entity.table, entity.label, *entity.synonyms)
        for column in entity.columns:
            if column.valid:
                add(entity.table, column.label, *column.synonyms)
        for metric in entity.metrics:
            if not metric.valid:
                continue
            add(entity.table, metric.name, metric.label, *metric.synonyms)
            metric_home.setdefault(metric.name.lower(), entity.table.lower())

    live = set(by_table)
    for term in doc.glossary:
        for target in term.maps_to:
            key = str(target).lower()
            home = key if key in live else metric_home.get(key)
            if home:
                add(home, term.term)

    # Drop what cannot narrow. Counted across the whole document rather than
    # per table, because the question is how many tables a phrase names — a
    # phrase on one table is a discriminator however common the word looks.
    spread: dict[str, int] = {}
    for phrases in by_table.values():
        for phrase in phrases:
            spread[phrase] = spread.get(phrase, 0) + 1
    too_wide = {p for p, n in spread.items() if n > TERM_MAX_TABLES}

    return {
        table: frozenset(kept)
        for table, phrases in by_table.items()
        if (kept := phrases - too_wide)
    }
