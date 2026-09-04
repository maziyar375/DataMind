"""Which provider embeds a knowledge store, and the dead end this replaced.

Phase 7 shipped an embedding matcher that **nobody could switch on.**
`_embedding_llm` resolved the owner's `llm_configs.is_default` row, and
`is_default` is written by nothing in this product: no route sets it, no
service sets it, no form offers it. So the lookup returned `None` for every
connection of every account, `PUT /knowledge/embeddings` answered *"Add a
default model provider first"* whatever the caller did, and the whole of §3.8
was unreachable from the interface.

The tests below are the fix stated as behaviour:

* a provider **declares** an embedding model (`llm_configs.embedding_model`),
  which is what makes it a candidate at all — Anthropic never is, because it
  has no embedding endpoint;
* the connection **names** the one that indexed it, so the answer to *"what
  made these vectors?"* is a row rather than an invisible global — the third
  leg of the pin beside the model id and the measured width;
* nothing depends on `is_default`, which still only *sorts* candidates.

And one rule the interface now depends on: **the embedder is resolved, never
chosen.** One provider serves the whole deployment — `embedding_provider` finds
it, `EmbeddingWrite` cannot name one, and the knowledge panel is a switch
rather than a form. The model a curator picks is the one that *answers*, and it
is offered where a question is asked; a store's vectors are only ever compared
inside that store, so nothing is bought by letting each connection embed
somewhere else, and a second endpoint to keep straight is what it would cost.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.infra.db.models import DatabaseConnection, LlmConfig
from app.services import knowledge_service
from app.services.knowledge_service import (
    _embedding_candidates,
    embedding_provider,
    set_embeddings,
)

OWNER = uuid4()


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)

    def scalar_one(self) -> Any:
        return self._rows[0] if self._rows else 0

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeDb:
    """Enough of an `AsyncSession` for the provider lookup and the switch.

    It answers a `select(LlmConfig)` with the configurations and everything
    else — the template read `index_embeddings` makes — with nothing.
    Deliberately **without** applying the query's `WHERE`: what a fake filters
    is what the test stops checking, and the filtering here is the point. The
    narrowing is SQL's; the decision is `can_embed`, which runs in Python on
    the rows that come back and is therefore what these tests exercise.
    """

    def __init__(self, configs: list[LlmConfig]) -> None:
        self.configs = configs
        self.flushed = 0
        #: Every statement it was handed. The fake applies no `WHERE`, so the
        #: narrowing SQL does is asserted on the query itself rather than on
        #: rows a fake chose to hand back.
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        entities = [
            description.get("entity")
            for description in getattr(statement, "column_descriptions", [])
        ]
        if LlmConfig in entities:
            return _Result(list(self.configs))
        return _Result([])

    async def get(self, _model: Any, key: Any) -> Any:
        return next((c for c in self.configs if c.id == key), None)

    async def flush(self) -> None:
        self.flushed += 1


def _config(
    *,
    name: str,
    embedding_model: str = "text-embedding-3-small",
    provider: str = "OpenAI-compatible",
    is_default: bool = False,
    created_at: int = 0,
    owner_id: Any = None,
) -> LlmConfig:
    return LlmConfig(
        id=uuid4(),
        owner_id=owner_id or OWNER,
        name=name,
        provider=provider,
        model="deepseek/deepseek-v4-flash",
        base_url="https://openrouter.ai/api/v1",
        temperature=0.2,
        max_tokens=2048,
        params={},
        embedding_model=embedding_model,
        embedding_params={},
        encrypted_api_key=None,
        is_default=is_default,
        created_at=created_at,
        status="OK",
        capabilities={},
    )


def _connection(**over: Any) -> DatabaseConnection:
    row = DatabaseConnection(
        id=uuid4(),
        owner_id=OWNER,
        name="Aurora Coffee",
        database_type="postgres",
        host="aurora",
        port=5432,
        database_name="aurora",
        username="analytics_ro",
        encrypted_password="ciphertext",
        schema_allowlist=["public"],
        disclosure_policy="SAMPLE",
        max_rows=1000,
        statement_timeout_ms=30_000,
        embedding_model="",
        embedding_dimension=0,
        embedding_llm_config_id=None,
    )
    for key, value in over.items():
        setattr(row, key, value)
    return row


# ── what counts as a provider that can embed ─────────────────────────────
@pytest.mark.asyncio
async def test_only_a_provider_that_declares_an_embedding_model_is_a_candidate() -> None:
    """A configuration is for completions until somebody says otherwise. The
    declaration is the opt-in, so adding a provider never quietly enrols it in
    a feature that spends the owner's budget."""
    with_model = _config(name="openrouter")
    without = _config(name="completions only", embedding_model="")
    db = FakeDb([with_model, without])

    assert [row.name for row in await _embedding_candidates(db, _connection())] == [
        "openrouter"
    ]


@pytest.mark.asyncio
async def test_anthropic_is_never_a_candidate_however_it_was_filled_in() -> None:
    """It has no embedding endpoint. `probe_embedding` refuses it without a
    network call and the parameter catalog offers it nothing; this is the same
    fact in the query that builds the picker, so the option never appears."""
    db = FakeDb(
        [_config(name="claude", provider="Anthropic", embedding_model="whatever")]
    )
    assert await _embedding_candidates(db, _connection()) == []


@pytest.mark.asyncio
async def test_the_pinned_provider_sorts_first_then_a_default_then_by_age() -> None:
    """Deterministic, because *"which provider indexed this store?"* must not
    depend on how a list came back."""
    oldest = _config(name="oldest", created_at=1)
    default = _config(name="default", is_default=True, created_at=2)
    pinned = _config(name="pinned", created_at=3)
    db = FakeDb([oldest, default, pinned])

    connection = _connection(embedding_llm_config_id=pinned.id)
    assert [row.name for row in await _embedding_candidates(db, connection)] == [
        "pinned",
        "default",
        "oldest",
    ]


# ── the dead end, stated as behaviour ────────────────────────────────────
@pytest.mark.asyncio
async def test_a_provider_is_found_even_though_nothing_ever_sets_is_default() -> None:
    """The regression this whole change exists for.

    `is_default` is a column with no writer, so resolving *only* on it meant
    the answer was always `None` — for everyone, on every connection, forever.
    """
    db = FakeDb([_config(name="openrouter", is_default=False)])
    found = await embedding_provider(db, _connection())
    assert found is not None and found.name == "openrouter"


def test_nothing_in_the_product_writes_is_default_on_a_provider() -> None:
    """Asserted on the parse, so the sentence above cannot quietly go stale.

    If a future change *does* give `is_default` a writer, this test fails and
    whoever wrote it gets to decide whether the fallback should become the
    primary rule again — rather than discovering years later that the docstring
    describes a world that moved.
    """
    writers: list[str] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "is_default"
                    # The mapper's own column declaration is not a write.
                    and not isinstance(node, ast.AnnAssign)
                ):
                    writers.append(f"{path}:{node.lineno}")
    assert writers == [], (
        "something now sets is_default; revisit embedding_provider, which "
        f"treats it as a sort key only. Writers: {writers}"
    )


class _Box:
    key_version = 1

    def decrypt(self, value: str, *, aad: str) -> str:
        return "key"


# ── a row declares what it is for ────────────────────────────────────────
def test_a_provider_may_serve_only_vectors() -> None:
    """The wart this replaced: `model` used to be required, so an endpoint that
    serves **no** chat model — a self-hosted TEI or Infinity server, an Ollama
    with one embedding model pulled — could only be configured by inventing a
    chat model, whose Test button would then fail against something that does
    not exist. A row now declares a chat model, an embedding model, or both."""
    from app.services.query_service import can_chat, can_embed

    vectors_only = _config(name="tei", embedding_model="bge-m3")
    vectors_only.model = ""
    assert can_embed(vectors_only) and not can_chat(vectors_only)

    answers_only = _config(name="flash", embedding_model="")
    assert can_chat(answers_only) and not can_embed(answers_only)

    both = _config(name="openrouter")
    assert can_chat(both) and can_embed(both)


def test_an_embeddings_only_provider_is_refused_at_the_one_funnel() -> None:
    """Checked in `resolve_llm` rather than at the eleven call sites that reach
    a provider through it — a run, a draft, a semantic layer, a report outline,
    a report section and a benchmark all arrive here. The refusal names the row,
    because *"model configuration not found"* would send somebody looking for a
    record that was never deleted."""
    from app.core.errors import ValidationError
    from app.services.query_service import CHAT, EMBEDDING, resolve_llm

    vectors_only = _config(name="tei", embedding_model="bge-m3")
    vectors_only.model = ""

    with pytest.raises(ValidationError) as err:
        resolve_llm(vectors_only, _Box(), purpose=CHAT)
    assert "tei" in str(err.value.message) and "embeddings only" in str(err.value.message)

    # And the same funnel refuses the other way round.
    with pytest.raises(ValidationError):
        resolve_llm(_config(name="flash", embedding_model=""), _Box(), purpose=EMBEDDING)

    # The embedding purpose is what the knowledge path asks for, and it passes.
    assert resolve_llm(vectors_only, _Box(), purpose=EMBEDDING).embedding_model == "bge-m3"


def test_the_chat_refusal_happens_before_a_run_exists() -> None:
    """`resolve_llm` refuses at the funnel, but by then the question has been
    written and a run row exists — and the refusal escapes the executor's
    failure handling, so the row sits `RUNNING` until the reconciler sweeps it.
    Observed, not theorised: that is exactly what one did.

    So `create_run` checks first, the way `_bind_connection` already refuses a
    released connection there rather than letting the run discover it.
    Asserted on the parse, because reproducing it needs a session, an executor
    and a provider."""
    import ast
    from pathlib import Path as _Path

    import app.services.run_service as run_service

    tree = ast.parse(_Path(run_service.__file__).read_text())
    create = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_run"
    )
    guards = [
        node for node in ast.walk(create)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "can_chat"
    ]
    assert guards, "create_run must refuse an embeddings-only provider up front"

    # And before the run row is added, or the check buys nothing.
    source = _Path(run_service.__file__).read_text()
    assert source.index("can_chat(llm_config)") < source.index("_bind_connection(")


# ── turning it on names the provider, turning it off releases it ─────────
class _Capability:
    def __init__(self, *, available: bool, model: str = "", dimension: int = 0,
                 reason: str = "") -> None:
        self.available = available
        self.model = model
        self.dimension = dimension
        self.reason = reason


@pytest.fixture
def _no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """A probe that answers without a provider, recording what it was asked."""
    seen: list[Any] = []

    async def probe(self: Any, llm: Any, *, model: str = "") -> _Capability:
        seen.append((llm, model))
        return _Capability(
            available=True,
            model=model or llm.embedding_model or "text-embedding-3-small",
            dimension=1536,
        )

    from app.infra.llm import litellm_gateway
    from app.services import query_service

    monkeypatch.setattr(litellm_gateway.LiteLLMGateway, "probe_embedding", probe)
    monkeypatch.setattr(
        litellm_gateway.LiteLLMGateway, "from_settings",
        classmethod(lambda cls, _settings: cls(timeout_seconds=1)),
    )
    monkeypatch.setattr(query_service, "secret_box", lambda _settings: _Box())
    return seen


@pytest.mark.asyncio
async def test_turning_it_on_records_which_provider_produced_the_index(
    _no_network: list[Any],
) -> None:
    """The pin is three things, not two: a store is only reproducible if the
    endpoint is known as well as the model name and the measured width."""
    config = _config(name="openrouter")
    connection = _connection()
    db = FakeDb([config])

    result, message = await set_embeddings(db, object(), connection, enabled=True)

    assert message == "", message
    assert connection.embedding_model == "text-embedding-3-small"
    assert connection.embedding_dimension == 1536
    assert connection.embedding_llm_config_id == config.id
    assert result.considered == 0, "an empty store indexes nothing and says so"


@pytest.mark.asyncio
async def test_the_pinned_provider_keeps_the_store_it_indexed(
    _no_network: list[Any],
) -> None:
    """A second endpoint set up later must not silently re-mean vectors that
    are already sitting in the store. The pin wins over the head of the
    candidate list for exactly as long as it can still embed."""
    pinned = _config(name="pinned", embedding_model="nomic-embed-text", created_at=2)
    older = _config(name="older", created_at=1)
    connection = _connection(embedding_llm_config_id=pinned.id)
    db = FakeDb([older, pinned])

    await set_embeddings(db, object(), connection, enabled=True)

    assert connection.embedding_llm_config_id == pinned.id
    assert connection.embedding_model == "nomic-embed-text"


@pytest.mark.asyncio
async def test_one_embedder_serves_every_connection(
    _no_network: list[Any],
) -> None:
    """The rule, stated on two stores: switching embedding search on twice
    resolves the same provider both times, without anybody naming it."""
    config = _config(name="the embedder")
    db = FakeDb([config])
    first, second = _connection(), _connection()

    await set_embeddings(db, object(), first, enabled=True)
    await set_embeddings(db, object(), second, enabled=True)

    assert first.embedding_llm_config_id == config.id
    assert second.embedding_llm_config_id == config.id


def test_nothing_in_the_interface_can_name_a_provider_to_embed_with() -> None:
    """The choice is gone from the wire, not just from the screen.

    A picker removed from a component while the field stays on the request body
    is a choice that has only become invisible — the next form to be written
    finds it and offers it again. `EmbeddingWrite` carries `enabled` and an
    optional `model` (the escape hatch for a self-hosted endpoint serving a
    name the row does not declare), and nothing that identifies a provider.
    """
    import inspect

    from app.api.schemas import EmbeddingWrite

    assert set(EmbeddingWrite.model_fields) == {"enabled", "model"}
    assert "llm_config_id" not in inspect.signature(set_embeddings).parameters


@pytest.mark.asyncio
async def test_the_candidates_are_scoped_to_the_connections_owner() -> None:
    """Asserted on the query, because the fake deliberately applies no `WHERE`:
    with the id path gone, this scoping is the only thing standing between one
    account's store and another account's key."""
    db = FakeDb([_config(name="mine")])
    connection = _connection()

    await _embedding_candidates(db, connection)

    sql = str(db.statements[-1])
    assert "owner_id" in sql and "embedding_model" in sql


@pytest.mark.asyncio
async def test_with_no_provider_configured_the_refusal_says_what_to_do() -> None:
    """*"Unavailable"* is not a fix somebody can act on; *"give a provider an
    embedding model"* is. And the connection is left exactly as it was."""
    connection = _connection()
    db = FakeDb([_config(name="completions only", embedding_model="")])

    result, message = await set_embeddings(db, object(), connection, enabled=True)

    assert "LLM providers" in message and "embedding model" in message
    assert connection.embedding_model == ""
    assert connection.embedding_llm_config_id is None
    assert result.embedded == 0


@pytest.mark.asyncio
async def test_turning_it_off_releases_the_provider_with_the_vectors() -> None:
    """Off clears the whole pin. Leaving the provider named would describe an
    index that no longer exists."""
    config = _config(name="openrouter")
    connection = _connection(
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        embedding_llm_config_id=config.id,
    )
    db = FakeDb([config])

    _, message = await set_embeddings(db, object(), connection, enabled=False)

    assert message == ""
    assert connection.embedding_model == ""
    assert connection.embedding_dimension == 0
    assert connection.embedding_llm_config_id is None


def test_a_deleted_provider_releases_the_store_rather_than_deleting_it() -> None:
    """`SET NULL`, like every other reference to `llm_configs`. A knowledge
    store is months of somebody's curation; a provider is a form."""
    column = DatabaseConnection.__table__.c.embedding_llm_config_id
    assert column.nullable
    assert next(iter(column.foreign_keys)).ondelete == "SET NULL"


def test_the_switch_still_lives_behind_the_gateway_port() -> None:
    """`set_embeddings` may not import litellm at module scope — the one-module
    rule. It imports the gateway inside the function, as it always has."""
    source = Path(knowledge_service.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.col_offset == 0:
            assert node.module is not None and "litellm" not in node.module
