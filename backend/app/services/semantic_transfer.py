"""The portable form of a semantic layer: what leaves as a file, and what a file
may become.

Phase 4 of `docs/plans/semantic-layer-model.md`, beside `dashboard_transfer.py`
and on its rules. A layer is the most portable thing in the product — it names
tables and says what they mean — and the one part of it that is not a
description is `value_meanings`, which is keyed by values drawn from the data.

* **No ids, hosts or credentials leave.** The file names its source connection
  by display name and engine, so a reader knows where it came from and whether
  its SQL dialect matches; nothing else about the connection.
* **No values from the data leave unless asked** (D9). `value_meanings` are
  stripped unless the exporter ticks *Include value meanings*, and the audit
  row records which choice was made. This is `dashboard_transfer`'s *"a
  document carries no results"*, applied to the one field that carries some.
* **Nothing derived leaves.** `joins`, `valid` and `issue` are readings of *this*
  installation's snapshot. An importer re-derives all three against its own,
  through the one binder.

**An import lands in the draft**, through `bind_layer`, never in the published
document: importing is typing by another route, with the same privilege
(`modify`) and the same review before it answers anything. **It is not a guard
entry point.** Nothing executes a metric expression — it is prompt text, and the
SQL a model writes after reading it is guarded like any other. The hostile SQL
corpus is replayed through `check_expression` anyway (`test_semantic_transfer.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as SchemaError

from app.core.clock import utcnow
from app.core.errors import ValidationError
from app.semantic import SemanticDocument
from app.semantic import limits as semantic_limits

#: The two fields a reader checks before it trusts anything else in the file.
FILE_FORMAT = "datamind.semantic_layer"
FILE_VERSION = 1


class LayerFileSource(BaseModel):
    """Where a file came from, as much as a file may say."""

    model_config = ConfigDict(extra="ignore")

    connection: str = Field(default="", max_length=200)
    engine: str = Field(default="", max_length=40)
    version: int | None = None


class LayerFile(BaseModel):
    """The file. `document` is validated separately, after the format checks."""

    model_config = ConfigDict(extra="ignore")

    format: str = FILE_FORMAT
    format_version: int = FILE_VERSION
    exported_at: datetime | None = None
    source: LayerFileSource = Field(default_factory=LayerFileSource)
    value_meanings_included: bool = False
    document: dict[str, Any] = Field(default_factory=dict)


# ── export ───────────────────────────────────────────────────────────────
def build_file(
    doc: SemanticDocument,
    *,
    connection_name: str,
    engine: str,
    version: int | None,
    value_meanings: bool,
) -> LayerFile:
    """The file for `doc`: derived fields stripped, value meanings only if asked."""
    return LayerFile(
        exported_at=utcnow(),
        source=LayerFileSource(connection=connection_name, engine=engine, version=version),
        value_meanings_included=value_meanings,
        document=portable(doc, value_meanings=value_meanings),
    )


def portable(doc: SemanticDocument, *, value_meanings: bool) -> dict[str, Any]:
    """`doc` as JSON with everything derived removed.

    `joins`, and `valid`/`issue` on every entity, column and metric, are the
    binder's reading of one snapshot; `value_meanings` are values from the data.
    """
    data = doc.model_dump(mode="json")
    data.pop("joins", None)
    for entity in data.get("entities", []):
        _strip_verdict(entity)
        for column in entity.get("columns", []):
            _strip_verdict(column)
            if not value_meanings:
                column.pop("value_meanings", None)
        for metric in entity.get("metrics", []):
            _strip_verdict(metric)
    return data


def _strip_verdict(entry: dict[str, Any]) -> None:
    entry.pop("valid", None)
    entry.pop("issue", None)


def value_meaning_columns(doc: SemanticDocument) -> int:
    """How many columns carry value meanings — what the export checkbox counts."""
    return sum(1 for e in doc.entities for c in e.columns if c.value_meanings)


# ── import ───────────────────────────────────────────────────────────────
def read_file(raw: Any) -> tuple[LayerFile, SemanticDocument]:
    """Check a file in the order a reader loses confidence, then parse it.

    Is it an object, is it *this* kind of file, a version this build reads, a
    size a real layer has — and only then is the document shaped right and
    inside every limit. Refused with a sentence, never a stack of field errors.
    """
    if not isinstance(raw, dict):
        raise ValidationError("That is not a semantic layer file.")
    if raw.get("format") != FILE_FORMAT:
        raise ValidationError(
            "That is not a semantic layer file — it is missing the format marker."
        )
    version = raw.get("format_version")
    if not isinstance(version, int) or version < 1:
        raise ValidationError("That semantic layer file does not say which version it is.")
    if version > FILE_VERSION:
        raise ValidationError(
            "That file was written by a newer version of DataMind than this one can read."
        )
    document = raw.get("document")
    if not isinstance(document, dict):
        raise ValidationError("That semantic layer file has no document in it.")
    entities = document.get("entities")
    if isinstance(entities, list) and len(entities) > semantic_limits.MAX_ENTITIES:
        raise ValidationError(
            f"That file describes {len(entities):,} tables; the limit is "
            f"{semantic_limits.MAX_ENTITIES:,}."
        )
    try:
        file = LayerFile.model_validate(raw)
        doc = SemanticDocument.model_validate(document)
    except SchemaError as err:
        first = err.errors()[0] if err.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "the document"
        raise ValidationError(f"That semantic layer file is malformed at {where}.") from err
    found = semantic_limits.problems(doc)
    if found:
        raise ValidationError(" ".join(found))
    return file, doc


@dataclass(frozen=True, slots=True)
class ImportReport:
    """What an import resolved against *this* snapshot."""

    entities: int
    unresolved: int
    metrics: int
    invalid_metrics: int
    value_meanings_included: bool
    value_meaning_columns: int
    source_connection: str
    source_engine: str

    @classmethod
    def of(cls, file: LayerFile, bound: SemanticDocument) -> ImportReport:
        # A metric on a table this schema lacks cannot reach a prompt either,
        # whatever its own flag says: the entity's is what binding set.
        metrics = [(e, m) for e in bound.entities for m in e.metrics]
        return cls(
            entities=len(bound.entities),
            unresolved=sum(1 for e in bound.entities if not e.valid),
            metrics=len(metrics),
            invalid_metrics=sum(1 for e, m in metrics if not (e.valid and m.valid)),
            value_meanings_included=value_meaning_columns(bound) > 0,
            value_meaning_columns=value_meaning_columns(bound),
            source_connection=file.source.connection,
            source_engine=file.source.engine,
        )
