"""Structured-output contracts the model must satisfy."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SqlProposal(BaseModel):
    sql: str = Field(description="A single SELECT statement. No trailing semicolon.")
    tables_used: list[str] = Field(default_factory=list)
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
