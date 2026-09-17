"""One binder, and every reader of the layer goes through it.

A stored document carries `valid` and `issue` on every entity, column and
metric, and the renderer keeps a flagged entry out of the prompt by reading
them. Those flags are only as current as the snapshot they were computed
against. A re-sync that drops a column does not touch the stored document, so a
reader that trusts the flags renders a definition the editor already marks red.

So validity is derived at read time, every time, against the snapshot the
caller already holds. The stored flags stay in the JSON because the editor
displays them; no consumer trusts them. It is the rule the knowledge store
already follows: guarded on save, and guarded again on read.

Three steps, in this order, and they used to be written out three times
(`save`, `_persist_generated` and the eval's `load_semantic`):

1. deserialise the document;
2. derive the joins off the catalog, because a join is a reading of foreign
   keys and never something a client or a stored copy gets to assert;
3. validate every entry against the snapshot.
"""
from __future__ import annotations

from typing import Any

from app.semantic.models import SemanticDocument
from app.semantic.validate import build_index, derive_joins, validate_document


def bind_layer(
    raw: SemanticDocument | dict[str, Any],
    *,
    tables: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    dialect: str,
) -> SemanticDocument:
    """The document as it is true against this snapshot.

    Pure: the input is not mutated. A document already bound to the same
    snapshot comes back rendering the same bytes, which is what keeps binding
    on load from moving the prompt of any layer that has not drifted.

    Raises `pydantic.ValidationError` when `raw` does not deserialise. Whether
    that is a refusal (a save) or a dropped feature (a run) is the caller's
    posture to choose, not this function's.
    """
    doc = (
        raw.model_copy(deep=True)
        if isinstance(raw, SemanticDocument)
        else SemanticDocument.model_validate(raw)
    )
    index = build_index(tables, dialect)
    doc.joins = derive_joins(relationships, index)
    return validate_document(doc, index)
