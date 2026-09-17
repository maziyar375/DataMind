"""*Grounded* cannot describe a layer the answer never saw.

`docs/plans/semantic-layer-model.md` §1.2.3. The chip reads *"every table it used
is described in your semantic layer"*, and `_all_described` decided it by reading
`semantic_layers.document` as a dict:

* it ignored `semantic_layer_enabled`, so an answer written with the layer off
  was still labelled Grounded;
* it trusted the stored `valid` flag, which is as old as the last save, so a
  table a re-sync dropped still counted as described.

Phase 0 made it read through `load_document`, the loader a run uses. Phase 1
gave runs a `semantic_layer_version`, and the chip now reads the version the
answer was written with: `0` is never Grounded, *n* is version *n* as bound when
it was published, and `NULL` — a run from before versions — keeps Phase 0's rule.
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
    SemanticLayerVersionRow,
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
        #: Published versions by number, as `semantic_layer_versions` holds them.
        self.versions: dict[int, dict[str, Any]] = {}

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
        if entity is SemanticLayerVersionRow:
            self.layer_reads += 1
            number = statement.compile().params.get("version_1")
            document = self.versions.get(number)
            return _Result([document] if document is not None else [])
        raise AssertionError(f"unexpected query: {entity}")


def _run(version: int | None = None) -> Run:
    return Run(
        id=uuid4(), connection_id=CONNECTION_ID, owner_id=uuid4(),
        semantic_layer_version=version,
    )


def _touching(*tables: str) -> list[Any]:
    return [SimpleNamespace(referenced_tables=list(tables))]


async def _tier(
    db: TierDb, *tables: str, version: int | None = None, **kwargs: Any
) -> str:
    knowledge = await conversations._knowledge(
        db, _run(version), _touching(*tables), **kwargs
    )
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


# ── against the version the run recorded (Phase 1) ───────────────────────
@pytest.mark.asyncio
async def test_a_run_answered_without_a_layer_is_never_grounded() -> None:
    # Version 0: nothing reached the prompt, whatever the layer says today.
    assert await _tier(TierDb(layer=DESCRIBED), "public.orders", version=0) == "GENERATED"


@pytest.mark.asyncio
async def test_describing_a_table_later_does_not_ground_an_old_answer() -> None:
    """The defect read-time grounding had: the chip moved with the layer."""
    db = TierDb(layer=DESCRIBED)  # today, both tables are described…
    db.versions[3] = _layer(SemanticEntity(table="public.orders", label="Orders"))
    # …but the answer was written with v3, which described only `orders`.
    assert await _tier(db, "public.orders", "public.customers", version=3) == "GENERATED"
    assert await _tier(db, "public.orders", version=3) == "GROUNDED"


@pytest.mark.asyncio
async def test_a_recorded_version_is_read_as_it_was_bound_when_published() -> None:
    # The switch and today's snapshot are about today; the version is about
    # the answer. An entity valid in v2 grounds a v2 answer after a re-sync.
    db = TierDb(layer=DESCRIBED, enabled=False, tables=[TABLES[0]])
    db.versions[2] = DESCRIBED
    assert await _tier(db, "public.customers", version=2) == "GROUNDED"


@pytest.mark.asyncio
async def test_an_entity_flagged_in_its_version_did_not_ground_that_answer() -> None:
    db = TierDb(layer=DESCRIBED)
    flagged = _layer(SemanticEntity(table="public.orders", label="Orders", valid=False))
    db.versions[4] = flagged
    assert await _tier(db, "public.orders", version=4) == "GENERATED"


@pytest.mark.asyncio
async def test_a_null_version_keeps_the_interim_rule() -> None:
    # A run from before migration 0032 recorded nothing: the current layer.
    assert await _tier(TierDb(layer=DESCRIBED), "public.orders", version=None) == "GROUNDED"
    assert await _tier(
        TierDb(layer=DESCRIBED, enabled=False), "public.orders", version=None
    ) == "GENERATED"


@pytest.mark.asyncio
async def test_a_transcript_reads_each_version_once() -> None:
    db = TierDb(layer=DESCRIBED)
    db.versions[1] = DESCRIBED
    memo: conversations._Described = {}
    for version in (1, 1, None, 1, None):
        await _tier(db, "public.orders", version=version, described=memo)
    assert db.layer_reads == 2
