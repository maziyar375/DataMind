"""Deterministic rewrites applied only to already-valid trees.

The LIMIT is injected by us, never requested from the model. A model that
writes `LIMIT 1000000` is capped down; a model that writes no LIMIT gets one.
"""
from __future__ import annotations

from sqlglot import expressions as exp


def apply_row_limit(tree: exp.Expression, max_rows: int) -> tuple[str, int]:
    """Return (sql, effective_limit)."""
    node = tree.copy()
    existing = node.args.get("limit")

    effective = max_rows
    if isinstance(existing, exp.Limit):
        try:
            requested = int(existing.expression.name)
            effective = min(requested, max_rows)
        except (AttributeError, ValueError):
            effective = max_rows

    node.set("limit", exp.Limit(expression=exp.Literal.number(effective)))
    return node.sql(dialect=None), effective


def transpile(tree: exp.Expression, *, read: str, write: str) -> str:
    return tree.sql(dialect=write)


def render(tree: exp.Expression, dialect: str, max_rows: int) -> tuple[str, int]:
    node = tree.copy()
    existing = node.args.get("limit")

    effective = max_rows
    if isinstance(existing, exp.Limit):
        try:
            effective = min(int(existing.expression.name), max_rows)
        except (AttributeError, ValueError):
            effective = max_rows

    node.set("limit", exp.Limit(expression=exp.Literal.number(effective)))
    return node.sql(dialect=dialect), effective
