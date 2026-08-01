"""Structured-output contracts the model must satisfy."""
from __future__ import annotations

from pydantic import BaseModel, Field


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
    """

    answerable: bool = Field(
        description=(
            "True if the question can be turned into SQL over this schema "
            "without choosing between materially different readings."
        )
    )
    question: str = Field(
        default="",
        max_length=300,
        description="What to ask the user. One sentence. Empty if answerable.",
    )
    options: list[str] = Field(
        default_factory=list,
        description="2-4 concrete readings the user can pick between.",
    )
    reasoning: str = Field(default="", max_length=300)
