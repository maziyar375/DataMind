"""The output-token floor for semantic generation.

A run writes one SELECT; a semantic-layer table description writes a label, a
description and a synonym list for every column plus several metrics with their
SQL, and a reasoning model spends the same budget on its scratchpad first. The
floor existed at 2048, which truncated wide tables mid-object — and a truncated
reply is unparseable, so the whole table was lost rather than shortened.
"""
from __future__ import annotations

from typing import Any

from app.infra.db.models import LlmConfig
from app.services.query_service import resolve_llm
from app.services.semantic_service import SEMANTIC_MIN_MAX_TOKENS


class _NoBox:
    """No stored key, so nothing to decrypt — the budget is what is under test."""

    def decrypt(self, blob: Any, *, aad: str) -> str:  # pragma: no cover - unused
        raise AssertionError("no key was configured")


def _config(max_tokens: int) -> LlmConfig:
    return LlmConfig(
        id="cfg", provider="OpenAI-compatible", model="deepseek/deepseek-chat",
        base_url=None, encrypted_api_key=None, temperature=0.2,
        max_tokens=max_tokens, capabilities={},
    )


def test_floor_lifts_a_run_sized_budget() -> None:
    llm = resolve_llm(
        _config(2048), _NoBox(), min_max_tokens=SEMANTIC_MIN_MAX_TOKENS
    )
    assert llm.max_tokens == SEMANTIC_MIN_MAX_TOKENS


def test_floor_is_a_floor_not_an_override() -> None:
    """A user who configured a larger budget keeps it."""
    llm = resolve_llm(
        _config(32000), _NoBox(), min_max_tokens=SEMANTIC_MIN_MAX_TOKENS
    )
    assert llm.max_tokens == 32000


def test_run_path_is_unchanged() -> None:
    """No floor by default — the pipeline's behaviour must not move."""
    assert resolve_llm(_config(2048), _NoBox()).max_tokens == 2048


def test_floor_clears_a_wide_table_description() -> None:
    """Sanity-check the number against the shape it has to hold: a 40-column
    table, ~90 output tokens per described column, plus metrics and overhead."""
    assert SEMANTIC_MIN_MAX_TOKENS >= 40 * 90 + 1500
