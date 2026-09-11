"""One structured line per provider call, carrying counts and no content.

`runs` says what a question cost and `run_steps` says what each node cost.
Neither can say what one *call* cost, and the difference is not academic: a
`generate` that repaired is one step row and two provider requests, and only
this log says what each of them spent. Phase 6 of
[docs/plans/token-accounting.md](../../../docs/plans/token-accounting.md) buys
that grain on the logging pipeline that already exists — no new dependency, no
metrics stack, no exporter.

Two properties, and the second is the one worth a test file of its own:

* **the line is joinable and complete** — the correlation id `core/logging.py`
  already attaches to every event ties it back to the request that caused it,
  and the counts, latency, model, provider and cost are all on the one line so
  reading it needs no second lookup;
* **the line carries no prompt, no completion, no question and no schema
  content.** `services/audit.py`'s rule 3, applied here for the reason it
  gives: a log that became a second copy of what reached the provider is a
  second thing to secure, and the one place somebody would forget to. That is
  asserted on the emitted keys rather than trusted, because the tempting
  debugging addition is exactly a `reply_head` — and `structured()` already
  logs one on the failure paths beside this, which is what makes the rule easy
  to erode by analogy.

The redaction processor is the phase gate, so these run the **real**
`_add_correlation` and `_redact` over the captured events rather than reading
what the call site passed. A bypass added for these events would pass a test
that asserted on the arguments and fail the one below.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from structlog.testing import capture_logs

from app.core.context import set_correlation_id
from app.core.logging import _add_correlation, _redact, configure_logging
from app.domain.ports.llm import ChatMessage
from app.infra.llm import litellm_gateway
from tests.unit.test_token_accounting import (
    _Answer,
    _chunk,
    _gateway,
    _llm,
    _reply,
    _stream,
    _usage,
)

_MSG = [ChatMessage(role="user", content="what did we sell last March")]

#: Everything an `llm_call` line is allowed to carry, plus what structlog's own
#: processors add. Written out as a closed set rather than as a list of
#: forbidden words: a denylist only catches the leaks somebody thought of, and
#: the whole point of rule 3 is that the next addition is the one nobody
#: thought of.
ALLOWED = {
    "event", "log_level", "correlation_id",
    "operation", "provider", "model",
    "prompt_tokens", "completion_tokens", "latency_ms", "cost_usd",
}

#: The question, the answer and the system prompt, all of which pass through
#: the gateway on every call. If any appears in a rendered line, rule 3 is
#: broken however the field was spelled.
SECRETS = ("what did we sell last March", "orders", "You are")


def _lines(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("event") == "llm_call"]


@pytest.fixture(autouse=True)
def _audible(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make an `info` line from the gateway reach a capture at all.

    Two obstacles, both in the shipped configuration and neither worth changing
    production for:

    **The level.** `conftest.py` runs the suite at `WARNING`, and structlog's
    filtering wrapper drops an `info` call *before* any processor sees it — so
    at the suite's own level every assertion here would pass by finding
    nothing. `INFO` is what `main.py` configures (`DEBUG` under `debug`), so it
    is the level at which these lines actually ship.

    **The cache.** `configure_logging` sets `cache_logger_on_first_use=True`,
    a performance default: a module-level logger binds its processor chain on
    first use and keeps it. Any earlier test that exercises the gateway — the
    Phase 1 suite next door does — leaves `litellm_gateway.log` bound to the
    quiet chain, and a later `structlog.configure` cannot reach inside a proxy
    that has already resolved. The events then render to stdout instead of the
    capture, which looks *exactly* like no line having been emitted, and the
    file passes alone and fails in the suite.

    So the wrapper is reconfigured uncached and the module's logger is rebound
    for the duration. `monkeypatch` restores it, and the session's `WARNING`
    configuration is put back after each test rather than left for the next
    module to inherit.
    """
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )
    monkeypatch.setattr(
        litellm_gateway, "log", structlog.get_logger("app.infra.llm.litellm_gateway")
    )
    yield
    configure_logging(json_logs=True, level="WARNING")


@pytest.fixture
def captured() -> Iterator[list[dict[str, Any]]]:
    """Captured events, with the two processors the gate is about still applied.

    `capture_logs` disables the configured chain, so the real
    `_add_correlation` and `_redact` are handed back in — which is what makes
    "the correlation id is attached" and "redaction still runs" claims about
    the shipped pipeline rather than about this fixture.

    The level that makes an `info` line audible at all is `_audible`'s job.
    """
    set_correlation_id("req-1234")
    with capture_logs(processors=[_add_correlation, _redact]) as entries:
        yield entries


# ── the line is emitted, and it is joinable ──────────────────────────────
@pytest.mark.asyncio
async def test_a_completion_leaves_one_line_with_its_counts(
    captured: list[dict[str, Any]],
) -> None:
    """`complete()` has always *returned* its counts, and four callers dropped
    them until Phase 4. The log is what a dropped count still leaves behind."""

    async def once(**_: Any) -> Any:
        return _reply("we sold 41 units", usage=_usage(120, 8))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().complete(_llm(), _MSG)

    [line] = _lines(captured)
    assert line["operation"] == "complete"
    assert (line["prompt_tokens"], line["completion_tokens"]) == (120, 8)
    assert line["correlation_id"] == "req-1234"


@pytest.mark.asyncio
async def test_the_line_names_the_model_litellm_was_actually_asked_for(
    captured: list[dict[str, Any]],
) -> None:
    """The resolved name, post-prefix — the same one the sink reports and the
    same one a cost is keyed on. A line naming the unprefixed model would not
    join to a price, and its null cost would read as "unpriced model" rather
    than as "looked it up wrong"."""

    async def once(**_: Any) -> Any:
        return _reply("x", usage=_usage(1, 1))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().complete(_llm(), _MSG)

    [line] = _lines(captured)
    assert line["model"] == "openai/gpt-4o-mini"
    assert line["provider"] == "OpenAI-compatible"


@pytest.mark.asyncio
async def test_a_priced_model_carries_its_cost_and_an_unpriced_one_carries_none(
    captured: list[dict[str, Any]],
) -> None:
    """Null is the normal state for a self-hosted deployment, and it must stay
    null: §6's rule is that a null `cost_usd` is never coerced to zero, and a
    line claiming `0.0` for a real spend is where that coercion starts."""

    async def once(**_: Any) -> Any:
        return _reply("x", usage=_usage(1000, 500))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().complete(_llm(), _MSG)
        await _gateway().complete(_llm(model="a-model-nobody-prices"), _MSG)

    priced, unpriced = _lines(captured)
    assert priced["cost_usd"] > 0
    assert unpriced["cost_usd"] is None


@pytest.mark.asyncio
async def test_a_repaired_structured_call_leaves_a_line_per_attempt(
    captured: list[dict[str, Any]],
) -> None:
    """Two requests were made and both were paid for.

    This is the grain that `run_steps` cannot express: the repair collapses
    into one step row, so a single line here would make the expensive failure
    mode the invisible one — the same reasoning that puts the usage sink inside
    the retry loop rather than after it.
    """
    calls = {"n": 0}

    async def twice(**_: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply("not json at all", usage=_usage(100, 5))
        return _reply('{"label": "orders"}', usage=_usage(150, 9))

    with patch("litellm.acompletion", side_effect=twice):
        await _gateway().structured(_llm(), _MSG, _Answer)

    first, second = _lines(captured)
    assert all(line["operation"] == "structured" for line in (first, second))
    assert (first["prompt_tokens"], second["prompt_tokens"]) == (100, 150)


@pytest.mark.asyncio
async def test_a_stream_leaves_one_line_with_the_trailing_usage_chunk(
    captured: list[dict[str, Any]],
) -> None:
    """And a provider that sends no usage chunk still leaves a line, of zeros.

    That is the honest record of §1.3's gap: the call happened and its cost is
    unknown. A missing line would be indistinguishable from a call that was
    never made.
    """

    async def streamed(**_: Any) -> Any:
        return _stream([_chunk(content="41"), _chunk(usage=_usage(70, 4))])

    async def silent(**_: Any) -> Any:
        return _stream([_chunk(content="41")])

    with patch("litellm.acompletion", side_effect=streamed):
        async for _ in _gateway().stream(_llm(), _MSG):
            pass
    with patch("litellm.acompletion", side_effect=silent):
        async for _ in _gateway().stream(_llm(), _MSG):
            pass

    reported, quiet = _lines(captured)
    assert (reported["prompt_tokens"], reported["completion_tokens"]) == (70, 4)
    assert (quiet["prompt_tokens"], quiet["completion_tokens"]) == (0, 0)
    assert quiet["cost_usd"] is None, "zero tokens are unpriced, never free"


@pytest.mark.asyncio
async def test_a_call_that_raises_leaves_no_line(
    captured: list[dict[str, Any]],
) -> None:
    """Nothing came back to read a count off, so there is nothing true to say.

    A line of zeros here would be a call that reported no tokens, which is a
    different fact from a call that never completed — and the two must not
    render the same, for the same reason a null token count is not a zero.
    """
    from app.core.errors import LLMError

    async def boom(**_: Any) -> Any:
        raise RuntimeError("connection reset")

    with patch("litellm.acompletion", side_effect=boom), pytest.raises(LLMError):
        await _gateway().complete(_llm(), _MSG)

    assert _lines(captured) == []


# ── rule 3: counts and identifiers, never content ────────────────────────
@pytest.mark.asyncio
async def test_no_field_carries_prompt_or_completion_text(
    captured: list[dict[str, Any]],
) -> None:
    """The phase's real subject, asserted on the keys rather than trusted.

    A closed allowlist and not a search for known-bad words: a denylist catches
    only the leaks somebody anticipated, and rule 3 exists because the next
    field added is the one nobody anticipated. Adding a genuinely harmless
    identifier here is one line and a deliberate act; adding a `reply_head` is
    the same one line, which is precisely why it should have to be argued for
    in a diff.
    """

    async def once(**_: Any) -> Any:
        return _reply('{"label": "orders"}', usage=_usage(120, 8))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().complete(_llm(), _MSG)
        await _gateway().structured(_llm(), _MSG, _Answer)

    for line in _lines(captured):
        assert set(line) <= ALLOWED, f"unexpected field: {set(line) - ALLOWED}"


@pytest.mark.asyncio
async def test_neither_the_question_nor_the_answer_appears_anywhere_in_a_line(
    captured: list[dict[str, Any]],
) -> None:
    """The same rule read off the rendered values rather than the keys.

    A field named `detail` or `context` would satisfy the allowlist test above
    only by being added to it; this one fails on the content regardless of what
    the field was called.
    """

    async def once(**_: Any) -> Any:
        return _reply('{"label": "orders"}', usage=_usage(120, 8))

    with patch("litellm.acompletion", side_effect=once):
        await _gateway().structured(_llm(), _MSG, _Answer)

    rendered = " ".join(
        str(value) for line in _lines(captured) for value in line.values()
    )
    for secret in SECRETS:
        assert secret not in rendered


@pytest.mark.asyncio
async def test_the_api_key_never_reaches_a_line_even_if_a_field_is_added(
    captured: list[dict[str, Any]],
) -> None:
    """The phase gate: the redaction processor still runs over these events.

    Asserted by sending one through it, because a bypass — a direct `print`, a
    logger configured apart, an event built after the chain — is invisible to
    any test that reads the call site's arguments instead of the pipeline's
    output.
    """
    from app.core.logging import get_logger

    get_logger(__name__).info("llm_call", model="m", api_key="sk-must-not-appear")

    [line] = _lines(captured)
    assert line["api_key"] == "[REDACTED]"
    assert "sk-must-not-appear" not in str(line)
