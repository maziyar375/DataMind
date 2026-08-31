"""Golden-set loading and the record schema.

The suites live as versioned JSON under `app/eval/suites/`. This module is the
one place that knows their shape: it parses them into typed records, and it maps
each `connection_fixture` name onto the fixture the runner must spin up.

Nothing here talks to a database or an LLM — it is pure data, so the schema can
be validated in a plain unit test.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SUITES_DIR = Path(__file__).resolve().parent / "suites"

ResultEquivalence = Literal["scalar_numeric", "set_unordered_by_columns", "ordered_rows"]
Difficulty = Literal["easy", "medium", "hard"]
Route = Literal["ANALYTICAL", "METADATA", "CHITCHAT", "UNSUPPORTED"]


class GoldRecord(BaseModel):
    """One analytical question and its reference answer."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    connection_fixture: str
    expected_tables: list[str]
    gold_sql: str
    result_equivalence: ResultEquivalence
    expected_chart_type: str
    tags: list[str]
    difficulty: Difficulty
    verification: Literal["dual_form", "single_form"] = "dual_form"


class NegativeRecord(BaseModel):
    """A non-analytical input; the correct outcome is a route, never SQL."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    connection_fixture: str
    expected_route: Route
    category: str
    expects_sql: Literal[False] = False
    rationale: str = ""


class GoldSuite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suite: str
    version: str
    description: str = ""
    result_equivalence_legend: dict[str, str] = Field(default_factory=dict)
    records: list[GoldRecord]

    @property
    def kind(self) -> Literal["gold"]:
        return "gold"


class NegativeSuite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suite: str
    version: str
    kind: Literal["negative"] = "negative"
    description: str = ""
    records: list[NegativeRecord]


# ── fixture registry ────────────────────────────────────────────────────────
# Maps a record's `connection_fixture` onto the seed the runner must load. The
# eval never touches a stored connection; it spins the fixture from this seed.


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    dialect: str                 # DatabaseKind value (postgres/mysql/mssql/oracle)
    image: str                   # container image the runner boots
    seed_path: Path              # SQL file loaded into the fresh container
    database: str = "sales"
    schema_allowlist: tuple[str, ...] = ("public",)
    # An OVERLAY of `COMMENT ON` statements, loaded after `seed_path` when the
    # runner is asked for the commented arm (`--comments`). A second fixture
    # entry would have been the obvious shape and is the wrong one: a record's
    # `connection_fixture` is part of the frozen golden set, so switching arms
    # would mean editing the suite — the one thing docs/eval.md forbids. The
    # arms are therefore the same fixture loaded two ways.
    comments_path: Path | None = None
    # The fixture's hand-written semantic layer, loaded when the runner is asked
    # for the layer-on arm (`--semantic on`). A file rather than a generated
    # document for the same reason the golden set is a file: an arm whose input
    # is regenerated per run measures the generator, not the layer. It is bound
    # to the live snapshot by `runner.load_semantic`, which refuses to run the
    # arm if any entry no longer resolves.
    semantic_path: Path | None = None


_FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures"

FIXTURES: dict[str, FixtureSpec] = {
    "sales_pg": FixtureSpec(
        name="sales_pg",
        dialect="postgres",
        image="postgres:16-alpine",
        seed_path=_FIXTURES_ROOT / "sales_seed.sql",
        comments_path=_FIXTURES_ROOT / "sales_comments.sql",
        semantic_path=_FIXTURES_ROOT / "sales_semantic.json",
    ),
}


def fixture_for(name: str) -> FixtureSpec:
    try:
        return FIXTURES[name]
    except KeyError as err:
        known = ", ".join(sorted(FIXTURES)) or "(none)"
        raise KeyError(f"Unknown connection_fixture {name!r}. Known: {known}.") from err


# ── loading ─────────────────────────────────────────────────────────────────


def _suite_path(name: str) -> Path:
    path = SUITES_DIR / (name if name.endswith(".json") else f"{name}.json")
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in SUITES_DIR.glob("*.json"))) or "(none)"
        raise FileNotFoundError(f"No suite {name!r} in {SUITES_DIR}. Available: {available}.")
    return path


def is_negative_suite(name: str) -> bool:
    data = json.loads(_suite_path(name).read_text())
    return data.get("kind") == "negative"


def load_gold_suite(name: str) -> GoldSuite:
    suite = GoldSuite.model_validate_json(_suite_path(name).read_text())
    _assert_unique_ids([r.id for r in suite.records])
    return suite


def load_negative_suite(name: str) -> NegativeSuite:
    suite = NegativeSuite.model_validate_json(_suite_path(name).read_text())
    _assert_unique_ids([r.id for r in suite.records])
    return suite


def _assert_unique_ids(ids: list[str]) -> None:
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"Duplicate record ids in suite: {dupes}")
