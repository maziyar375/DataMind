"""`runs.prompt_version` must name the prompt that rendered the run.

The column exists so that a change in wording never silently invalidates a
historical comparison — which it can only do if the recorded version is the one
the run actually used. It was written from `settings.prompt_version`, a
hardcoded default of `"v2"` that stayed put while
`app.pipeline.prompts.PROMPT_VERSION` moved to v8, so **every** run in the
database claimed a version none of them had run and any figure sliced by it was
fiction.

Phase 0 of [docs/learning-loop-plan.md](../../../docs/learning-loop-plan.md)
calls this "fixing the ruler": nothing measured after it means anything until
the label is true. These tests fail on the code as it was.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.clock import utcnow
from app.core.config import Settings
from app.domain.value_objects import DisclosurePolicy
from app.infra.db.models import Conversation, DatabaseConnection, LlmConfig, Run
from app.pipeline.prompts import PROMPT_VERSION
from app.services.run_service import RunService

OWNER = uuid4()
CONVERSATION_ID = uuid4()
CONNECTION_ID = uuid4()
LLM_ID = uuid4()


class FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeDb:
    """The four calls `create_run` makes, and the rows it adds.

    `get` is keyed by model because the service asks for a conversation, a
    connection and an llm config by id; `execute` answers the one query it
    makes (the next message seq) with an empty transcript.
    """

    def __init__(self, rows: dict[type, Any]) -> None:
        self._rows = rows
        self.added: list[Any] = []
        self.flushes = 0

    async def get(self, model: type, entity_id: UUID) -> Any:
        return self._rows.get(model)

    async def execute(self, _statement: Any) -> FakeResult:
        return FakeResult(None)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flushes += 1


def _settings(**overrides: Any) -> Settings:
    """Real settings, so the shipped default is what is under test."""
    return Settings(
        secret_box_key="x" * 43 + "=",
        jwt_secret="test-secret-not-for-production",
        **overrides,
    )


def _rows() -> dict[type, Any]:
    return {
        Conversation: Conversation(
            id=CONVERSATION_ID,
            owner_id=OWNER,
            title="New chat",
            default_connection_id=CONNECTION_ID,
            default_llm_config_id=LLM_ID,
            updated_at=utcnow(),
        ),
        DatabaseConnection: DatabaseConnection(
            id=CONNECTION_ID,
            owner_id=OWNER,
            name="sales",
            database_type="postgres",
            host="db",
            port=5432,
            database_name="sales",
            username="ro",
            encrypted_password="x",
            max_rows=1000,
            statement_timeout_ms=30_000,
            disclosure_policy=DisclosurePolicy.SAMPLE,
        ),
        LlmConfig: LlmConfig(
            id=LLM_ID,
            owner_id=OWNER,
            name="deepseek",
            provider="openai",
            model="m",
            temperature=0.0,
            max_tokens=1024,
            encrypted_api_key=None,
            capabilities={},
        ),
    }


async def _create(settings: Settings) -> Run:
    db = FakeDb(_rows())
    service = RunService(db, settings)  # type: ignore[arg-type]
    return await service.create_run(
        owner_id=OWNER,
        conversation_id=CONVERSATION_ID,
        content="revenue by month",
        connection_id=None,
        llm_config_id=None,
    )


@pytest.mark.asyncio
async def test_a_run_records_the_version_of_the_prompt_module() -> None:
    """The claim the whole eval leans on: the label names the bytes.

    Fails on the previous code, which wrote `settings.prompt_version` — "v2"
    against a prompt module at v8.
    """
    run = await _create(_settings())
    assert run.prompt_version == PROMPT_VERSION


def test_the_setting_is_an_override_and_is_empty_by_default() -> None:
    """Nothing in a stock deployment can push a stale label onto a run.

    The old default was itself the bug: a version string in config drifts from
    the module it names the moment anyone edits a prompt, and nothing fails.
    """
    assert _settings().prompt_version is None


@pytest.mark.asyncio
async def test_an_explicit_override_is_honoured() -> None:
    """The escape hatch the setting is kept for: an experiment files its runs
    under a label of its own, and says so out loud in config rather than by
    drifting."""
    run = await _create(_settings(prompt_version="v8-experiment"))
    assert run.prompt_version == "v8-experiment"


@pytest.mark.asyncio
async def test_blank_is_not_a_version() -> None:
    """`PROMPT_VERSION=` in an env file means "unset", not "record nothing"."""
    run = await _create(_settings(prompt_version="   "))
    assert run.prompt_version == PROMPT_VERSION


def test_the_eval_runner_records_the_same_constant() -> None:
    """The two places a version is recorded must agree, or an `eval_runs` row
    cannot be compared with the `runs` rows it is meant to predict."""
    from app.eval import runner

    assert runner.PROMPT_VERSION is PROMPT_VERSION


@pytest.mark.asyncio
async def test_the_run_row_is_stamped_before_the_pipeline_renders() -> None:
    """A queued run is re-stamped by the process that actually renders it.

    A run can be created by one replica and claimed by another after a deploy;
    the version that matters is the one in the process that built the prompt.
    Asserted on the resolver rather than a full `execute_run`, which needs a
    connector, a gateway and a live snapshot — `execute_run` calls exactly this.
    """
    service = RunService(FakeDb({}), _settings())  # type: ignore[arg-type]
    run = Run(
        id=uuid.uuid4(),
        conversation_id=CONVERSATION_ID,
        owner_id=OWNER,
        prompt_version="v2",
    )
    run.prompt_version = service._prompt_version()
    assert run.prompt_version == PROMPT_VERSION
