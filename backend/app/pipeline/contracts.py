"""Structured-output contracts the model must satisfy."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class SqlProposal(BaseModel):
    """What the model returns from `generate`, `REVIEW` and `REPAIR`.

    There was a `tables_used: list[str]` here and it is gone on purpose. It was
    read nowhere — the table list the platform trusts comes from the guard's
    AST (`validation_report.referenced_tables`), because a model's claim about
    which tables it used is not evidence — and as an **unbounded array in a
    strict `json_schema`** it was a place for a model to run away. Measured on
    a 42-table schema: the SQL completed in ~90 tokens, then `tables_used`
    filled with 1,350 entries (the same 42 tables repeated 61 times) until
    `max_tokens` cut the reply mid-string, so the JSON never closed and the
    whole proposal was lost as `E_LLM`. Raising `max_tokens` to 8192 did not
    help — it is a loop, not a budget shortfall — and `maxItems` was ignored by
    the provider's constrained decoder. Removing the field ended it at 90
    tokens.

    Keep every field here bounded: `reasoning` has a `max_length` for the same
    reason.
    """

    sql: str = Field(description="A single SELECT statement. No trailing semicolon.")
    reasoning: str = Field(default="", max_length=500)


class ClarificationProposal(BaseModel):
    """Whether a question can be answered at all without guessing.

    `answerable=True` is the overwhelmingly common outcome and the only one
    that costs nothing: the other fields are ignored and the run proceeds
    straight to `generate`.

    **Every field is required, and that is the point.** A Python default on
    `options` reads as a convenience, but the model never sees Python — it sees
    `model_json_schema()`, and a defaulted field drops out of `required`. Under
    a strict `json_schema` response format that is a licence to omit the key
    entirely, and the model takes it: measured against the configured model on
    one ambiguous question, five calls under the defaulted schema produced
    options on **1 of 3** replies that asked something, while the same prompt
    with all four fields required produced them on **4 of 4**. The user saw a
    clarifying question with no chips to click and nothing explaining why.

    So the fields are required *in the schema*, and `_fill_defaults` below keeps
    the parse forgiving anyway. The two are not in tension: the schema states
    what is wanted, the validator refuses to let a model that ignores it turn
    clarification off altogether.
    """

    answerable: bool = Field(
        description=(
            "True if the question can be turned into SQL over this schema "
            "without choosing between materially different readings."
        )
    )
    question: str = Field(
        max_length=300,
        description="What to ask the user. One sentence. Empty if answerable.",
    )
    options: list[str] = Field(
        description=(
            "2-4 concrete readings the user can pick as-is. Required whenever "
            "answerable is false; an empty list when it is true."
        ),
    )
    reasoning: str = Field(max_length=300)

    @model_validator(mode="before")
    @classmethod
    def _fill_defaults(cls, data: Any) -> Any:
        """Supply what a model left out, so a missing key never costs the run.

        `clarify` fails open in every direction, and a `ValidationError` here
        would reach it as "clarification unavailable" — the one node whose whole
        job is to notice ambiguity, switched off by a model that skipped a field
        it was told to send. Required in the schema, tolerated on the way in.
        """
        if not isinstance(data, dict):
            return data
        return {"question": "", "options": [], "reasoning": "", **data}
