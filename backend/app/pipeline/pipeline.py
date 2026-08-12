"""The run as an explicit state machine.

The state machine is now a compiled LangGraph — see
[`graph.py`](graph.py) and
[docs/langgraph-migration.md](../../../docs/langgraph-migration.md). The node
signatures were built LangGraph-shaped from the start, so adopting it was the
wiring change the architecture doc predicted rather than a rewrite: the ten
node functions are untouched, and what moved is the `while` loop that did index
arithmetic over `ORDER` to resolve both the backward repair edges and the two
forward restores.

This module stays as the import site every caller already uses —
`run_service`, `app.eval.runner`, `app.pipeline.__init__` — so nothing above
the pipeline had to change, and `tests/unit/test_clarify.py` can still read
`ORDER` to assert where `clarify` sits.
"""
from __future__ import annotations

from app.pipeline.graph import ORDER, AnalyticsPipeline, NodeFn

__all__ = ["AnalyticsPipeline", "ORDER", "NodeFn"]
