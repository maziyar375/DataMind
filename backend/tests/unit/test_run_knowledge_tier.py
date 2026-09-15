"""*Grounded* cannot describe a layer the answer never saw.

`docs/plans/semantic-layer-model.md` §1.2.3. The chip reads *"every table it used
is described in your semantic layer"*, and `_all_described` decided it by reading
`semantic_layers.document` as a dict:

* it ignored `semantic_layer_enabled`, so an answer written with the layer off
  was still labelled Grounded;
* it trusted the stored `valid` flag, which is as old as the last save, so a
  table a re-sync dropped still counted as described.

It now reads through `load_document`, the loader a run uses. Still computed at
read time — Phase 1 replaces "the layer as it is now" with "the version the run
was written against" — but it can no longer claim what the run path would not
have rendered.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.api.v1 import conversations
from app.infra.db.models import (
    AnswerFeedback,
    DatabaseConnection,
    KnowledgeTemplateHit,
    Run,
    SchemaSnapshotRow,
    SemanticLayerRow,
)
from app.semantic import SemanticDocument, SemanticEntity

CONNECTION_ID = uuid4()

TABLES = [
    {"schema": "public", "name": "orders",
     "columns": [{"name": "id", "data_type": "bigint"}]},
    {"schema": "public", "name": "customers",
     "columns": [{"name": "id", "data_type": "bigint"}]},
]


def _layer(*entities: SemanticEntity) -> dict[str, Any]:
    # Serialised as `save` would have left it: every flag `valid=True`.
    return SemanticDocument(entities=list(entities)).model_dump(mode="json")


DESCRIBED = _layer(
    SemanticEntity(table="public.orders", label="Orders"),
    SemanticEntity(table="public.customers", label="Customers"),
)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class TierDb:
    def __init__(
        self,
        *,
        layer: dict[str, Any] | None,
        enabled: bool = True,
        tables: list[dict[str, Any]] = TABLES,
    ) -> None:
        self.connection = DatabaseConnection(
            id=CONNECTION_ID, owner_id=uuid4(), name="sales",
            database_type="postgres", semantic_layer_enabled=enabled,
        )
        self.snapshot = SchemaSnapshotRow(
            id=uuid4(), connection_id=CONNECTION_ID, version=2, dialect="postgres",
            tables=tables, relationships=[], catalog_meta={},
        )
        self.layer = (
            SemanticLayerRow(id=uuid4(), connection_id=CONNECTION_ID, document=layer)
            if layer is not None else None
        )
        self.layer_reads = 0

    async def get(self, model: Any, _key: Any) -> Any:
        return self.connection if model is DatabaseConnection else None

    async def execute(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0].get("entity")
        if entity in (KnowledgeTemplateHit, AnswerFeedback):
            return _Result([])
        if entity is SchemaSnapshotRow:
            return _Result([self.snapshot])
        if entity is SemanticLayerRow:
            self.layer_reads += 1
            return _Result([self.layer] if self.layer else [])
        raise AssertionError(f"unexpected query: {entity}")


def _run() -> Run:
    return Run(id=uuid4(), connection_id=CONNECTION_ID, owner_id=uuid4())


def _touching(*tables: str) -> list[Any]:
    return [SimpleNamespace(referenced_tables=list(tables))]


async def _tier(db: TierDb, *tables: str, **kwargs: Any) -> str:
    knowledge = await conversations._knowledge(db, _run(), _touching(*tables), **kwargs)
    return knowledge.tier


@pytest.mark.asyncio
async def test_every_touched_table_described_is_grounded() -> None:
    # The positive control: without it, every "not grounded" below could be a
    # fake that never grounds anything.
    assert await _tier(TierDb(layer=DESCRIBED), "public.orders", "public.customers") == "GROUNDED"


@pytest.mark.asyncio
async def test_an_answer_with_the_layer_switched_off_is_not_grounded() -> None:
    assert await _tier(
        TierDb(layer=DESCRIBED, enabled=False), "public.orders"
    ) == "GENERATED"


@pytest.mark.asyncio
async def test_an_entity_invalid_against_the_current_snapshot_is_not_described() -> None:
    # `customers` was dropped by a re-sync. Its stored flag still says valid.
    db = TierDb(layer=DESCRIBED, tables=[TABLES[0]])
    assert SemanticDocument.model_validate(DESCRIBED).entities[1].valid
    assert await _tier(db, "public.orders", "public.customers") == "GENERATED"
    assert await _tier(db, "public.orders") == "GROUNDED"


@pytest.mark.asyncio
async def test_an_excluded_entity_describes_nothing() -> None:
    # Excluded from the prompt entirely, so the model never read a word about it.
    layer = _layer(
        SemanticEntity(table="public.orders", label="Orders"),
        SemanticEntity(table="public.customers", label="Customers", exclude=True),
    )
    assert await _tier(TierDb(layer=layer), "public.customers") == "GENERATED"


@pytest.mark.asyncio
async def test_no_layer_and_no_touched_tables_are_both_generated() -> None:
    assert await _tier(TierDb(layer=None), "public.orders") == "GENERATED"
    assert await _tier(TierDb(layer=DESCRIBED)) == "GENERATED"


@pytest.mark.asyncio
async def test_a_transcript_binds_the_layer_once() -> None:
    db = TierDb(layer=DESCRIBED)
    memo: conversations._Described = {}
    for _ in range(5):
        assert await _tier(db, "public.orders", described=memo) == "GROUNDED"
    assert db.layer_reads == 1
