"""Folding an authorization answer into a query, instead of into a loop.

One function, and it is the reason `visible()` returns a shape rather than a
list of ids. A list endpoint asks *"which of these may they see?"* once and
composes the answer into the `SELECT` it was already going to run — one round
trip, one query plan, and `LIMIT`/`OFFSET` that count the rows the caller can
actually see.

The alternative is the anti-pattern this whole port exists to prevent: load a
page of rows, ask `allowed` about each one, drop the failures, and return a
page that is short by however many were dropped. That is not a style problem.
It is a pagination bug that only appears once somebody is sharing.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Select

from app.domain.ports.authz import Everything, Ids, Subquery, Visible


def restrict[S: Select[Any]](statement: S, column: Any, visible: Visible) -> S:
    """`statement`, narrowed to the ids `visible` names.

    `column` is the id column of the thing being listed — usually
    `Dashboard.id`, but for a query that lists *tiles* by their dashboard it is
    `DashboardTile.dashboard_id`, which is the whole reason it is a parameter
    rather than inferred.

    Three arms, matching the three shapes:

    * `Everything` adds no clause at all, so the wildcard case costs nothing —
      not even an `IN (SELECT ...)` the planner has to unwrap.
    * `Subquery` becomes `IN (SELECT id ...)`, correlated by the database.
    * `Ids` becomes a literal `IN (...)`, and an **empty** set becomes a clause
      that matches nothing. SQLAlchemy renders that as a false predicate rather
      than as invalid SQL, which is the honest answer to "you may see none of
      these" — an unfiltered query would have been the dangerous one.
    """
    if isinstance(visible, Everything):
        return statement
    if isinstance(visible, Subquery):
        return statement.where(column.in_(visible.select))  # type: ignore[return-value]
    if isinstance(visible, Ids):
        return statement.where(column.in_(visible.ids))  # type: ignore[return-value]
    raise TypeError(f"Not a Visible: {visible!r}")
