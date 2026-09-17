"""How long each text in a semantic layer may be, and how many of each list.

Phase 4 of `docs/plans/semantic-layer-model.md`. Once a layer can arrive as a
file, a hostile one can carry a forty-megabyte description or two hundred
thousand entities — and every word of a layer is prompt text. The same bound
applies to the editor, because prompt text should not be unbounded through
either door.

**Checked on write, not on parse.** The limits are declared beside the model
rather than as `Field(max_length=…)` on it, and that is a decision:

* a parse-time limit would make an over-long layer *already stored* fail to
  deserialise, and the loaders fail open — so it would silently leave every
  prompt, which is worse than a long description ever was;
* the generator builds these models from model output, and a parse-time limit
  would drop a whole table for one verbose sentence.

So a person's write — `PUT …/semantic`, `PUT …/semantic/draft`, `POST
…/semantic/import` — is refused with the first few problems named, and a
generation is clipped to the same limits before it is written (`clip`). The
real layers these were sized against are far inside them: the longest text in
the `sales` fixture and the demo `aurora` layer is a 411-character business
context.

`test_semantic_transfer.py` fails when a text field exists without a limit
here, so a field added to the model cannot quietly be unbounded.
"""
from __future__ import annotations

from typing import Any

from app.semantic.models import (
    GlossaryTerm,
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticJoin,
    SemanticMetric,
    TimeSemantics,
)

#: A ceiling on a hostile file, not on a real schema: the widest fixture here
#: has 42 tables.
MAX_ENTITIES = 2_000

#: Characters, per field, per model. A list of strings is limited per item, and
#: a `value_meanings` dict per meaning.
TEXT: dict[type, dict[str, int]] = {
    SemanticDocument: {"business_context": 10_000, "default_exclusions": 4_000},
    TimeSemantics: {"timezone": 64, "notes": 4_000},
    SemanticEntity: {
        "table": 512, "label": 200, "description": 4_000, "synonyms": 200,
        "grain": 1_000, "default_time_column": 256, "issue": 2_000,
    },
    SemanticColumn: {
        "name": 256, "label": 200, "description": 4_000, "synonyms": 200,
        "unit": 64, "value_meanings": 1_000, "issue": 2_000,
    },
    SemanticMetric: {
        "name": 256, "label": 200, "description": 4_000, "synonyms": 200,
        "expression": 4_000, "filters": 2_000, "required_joins": 512,
        "unit": 64, "format": 64, "issue": 2_000,
    },
    SemanticJoin: {"left": 512, "right": 512, "on": 2_000, "fan_out_warning": 2_000},
    GlossaryTerm: {"term": 200, "meaning": 4_000, "maps_to": 256},
}

#: Items, per list field, per model.
ITEMS: dict[type, dict[str, int]] = {
    SemanticDocument: {"entities": MAX_ENTITIES, "joins": 10_000, "glossary": 2_000},
    SemanticEntity: {"synonyms": 50, "columns": 1_600, "metrics": 500},
    SemanticColumn: {"synonyms": 50, "value_meanings": 1_000},
    SemanticMetric: {"synonyms": 50, "filters": 50, "required_joins": 50},
    GlossaryTerm: {"maps_to": 100},
}

#: How long a key of `value_meanings` may be — a code drawn from the data.
VALUE_KEY = 256


def problems(doc: SemanticDocument, *, limit: int = 5) -> list[str]:
    """The first `limit` ways `doc` exceeds a limit, as sentences. Empty when none."""
    out: list[str] = []

    def over(what: str, size: int, most: int, unit: str) -> None:
        if len(out) < limit:
            out.append(f"{what} is {size:,} {unit}; the limit is {most:,}.")

    def check(model: Any, where: str) -> None:
        for field, most in TEXT.get(type(model), {}).items():
            value = getattr(model, field)
            if isinstance(value, str):
                if len(value) > most:
                    over(f"{where} {field}", len(value), most, "characters")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and len(item) > most:
                        over(f"An entry in {where} {field}", len(item), most, "characters")
            elif isinstance(value, dict):
                for key, text in value.items():
                    key_size, text_size = len(str(key)), len(str(text))
                    if key_size > VALUE_KEY:
                        over(f"A key in {where} {field}", key_size, VALUE_KEY, "characters")
                    if text_size > most:
                        over(f"A meaning in {where} {field}", text_size, most, "characters")
        for field, most in ITEMS.get(type(model), {}).items():
            size = len(getattr(model, field))
            if size > most:
                over(f"{where} {field}", size, most, "entries")

    check(doc, "The layer's")
    check(doc.time, "The time conventions'")
    for entity in doc.entities[:MAX_ENTITIES]:
        name = f"`{entity.table[:80]}`"
        check(entity, name)
        for column in entity.columns:
            check(column, f"{name} column `{column.name[:80]}`")
        for metric in entity.metrics:
            check(metric, f"{name} metric `{metric.name[:80]}`")
    for join in doc.joins:
        check(join, "A join's")
    for term in doc.glossary:
        check(term, f"Glossary term `{term.term[:80]}`")
    return out


def clip(doc: SemanticDocument) -> SemanticDocument:
    """A copy of `doc` with every text cut to its limit — for a generation.

    A model's verbose sentence is shortened rather than refused: nobody can be
    asked to fix text they did not write, and a person's next save of the
    layer would otherwise be refused for it.
    """
    out = doc.model_copy(deep=True)

    def cut(model: Any) -> None:
        for field, most in TEXT.get(type(model), {}).items():
            value = getattr(model, field)
            if isinstance(value, str):
                setattr(model, field, value[:most])
            elif isinstance(value, list):
                setattr(model, field, [v[:most] if isinstance(v, str) else v for v in value])
            elif isinstance(value, dict):
                setattr(model, field, {
                    str(k)[:VALUE_KEY]: str(v)[:most] for k, v in value.items()
                })

    cut(out)
    cut(out.time)
    for entity in out.entities:
        cut(entity)
        for column in entity.columns:
            cut(column)
        for metric in entity.metrics:
            cut(metric)
    for join in out.joins:
        cut(join)
    for term in out.glossary:
        cut(term)
    return out
