"""The suites and the loader parse and register correctly (no DB, no model)."""
from __future__ import annotations

import pytest

from app.eval import dataset


def test_gold_suite_loads_and_validates() -> None:
    suite = dataset.load_gold_suite("sales_v1")
    assert suite.suite == "sales" and suite.version == "v1"
    assert len(suite.records) == 50
    for r in suite.records:
        assert r.connection_fixture == "sales_pg"
        assert r.expected_tables
        assert r.gold_sql.strip()


def test_negative_suite_loads_and_never_expects_sql() -> None:
    suite = dataset.load_negative_suite("sales_v1_negative")
    assert len(suite.records) == 10
    for r in suite.records:
        assert r.expects_sql is False
        assert r.expected_route in {"ANALYTICAL", "METADATA", "CHITCHAT", "UNSUPPORTED"}


def test_is_negative_suite_distinguishes() -> None:
    assert dataset.is_negative_suite("sales_v1_negative") is True
    assert dataset.is_negative_suite("sales_v1") is False


def test_fixture_registry_maps_sales_pg() -> None:
    spec = dataset.fixture_for("sales_pg")
    assert spec.dialect == "postgres"
    assert spec.seed_path.name == "sales_seed.sql"
    assert spec.seed_path.exists()


def test_unknown_fixture_and_suite_raise() -> None:
    with pytest.raises(KeyError):
        dataset.fixture_for("nope")
    with pytest.raises(FileNotFoundError):
        dataset.load_gold_suite("does_not_exist")


def test_extra_fields_are_rejected_on_gold_records() -> None:
    from pydantic import ValidationError

    from app.eval.dataset import GoldRecord

    with pytest.raises(ValidationError):
        GoldRecord.model_validate({"id": "x", "surprise": 1})
