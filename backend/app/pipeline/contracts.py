"""Structured-output contracts the model must satisfy."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SqlProposal(BaseModel):
    sql: str = Field(description="A single SELECT statement. No trailing semicolon.")
    tables_used: list[str] = Field(default_factory=list)
    reasoning: str = Field(default="", max_length=500)
