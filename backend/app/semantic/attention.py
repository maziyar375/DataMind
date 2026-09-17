"""What in a semantic layer needs a person, and why.

Phase 5 of `docs/plans/semantic-layer-model.md`. Every reason here is built from
something already known — the binder's verdicts, the snapshot, the version
history, the attribution counts, the tiers answers earned — so the editor's
*Needs attention* filter needs no job, no model call and no new table.

Pure, like the rest of the package: the service gathers the facts, and this
decides what they mean, so every rule is tested against a fixture rather than
a database.

Six reasons, in the order the list shows them:

    DRAFT_OLD              a draft nobody has touched in `DRAFT_DAYS`
    INVALID                an entity, column or metric the current snapshot breaks
    COLUMNS_CHANGED        the table's columns moved since the version that last
                           changed its entity
    METRIC_IGNORED         answers left part of a definition out more often
                           than they used it
    UNREVIEWED_RELIED_ON   a model's unreviewed description that Grounded
                           answers stood on
    UNDESCRIBED            a table with no entity

**Excluded entities need nothing.** They reach no prompt, so a broken or drifted
one misleads no answer; setting a table aside is itself the decision.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.semantic.models import SemanticDocument, SemanticEntity

DRAFT_OLD = "DRAFT_OLD"
INVALID = "INVALID"
COLUMNS_CHANGED = "COLUMNS_CHANGED"
METRIC_IGNORED = "METRIC_IGNORED"
UNREVIEWED_RELIED_ON = "UNREVIEWED_RELIED_ON"
UNDESCRIBED = "UNDESCRIBED"

#: The list's order, most urgent first.
REASONS = (
    DRAFT_OLD, INVALID, COLUMNS_CHANGED, METRIC_IGNORED, UNREVIEWED_RELIED_ON, UNDESCRIBED,
)

#: How long a draft may sit before it is worth a line. A week: long enough that
#: an edit in progress is not nagged about, short enough that a forgotten one —
#: whose edits no answer reads — is found while somebody still remembers it.
DRAFT_DAYS = 7


@dataclass(frozen=True, slots=True)
class Attention:
    """One thing that needs a person: a reason, where, and the counts behind it.

    `table` is the lower-cased qualified table, or `""` for the document; `item`
    is a metric name for `METRIC_IGNORED` and `""` otherwise. `detail` holds
    counts and schema names only — never a question, an answer or a value.
    """

    reason: str
    table: str = ""
    item: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason, "table": self.table,
            "item": self.item, "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class ColumnDrift:
    """How a table's columns moved between two snapshots. Names only."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    retyped: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.retyped)


@dataclass(frozen=True, slots=True)
class Baseline:
    """The version that last changed an entity, and its snapshot's tables."""

    version: int
    tables: Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class MetricCount:
    """One metric's `used` and `ignored` counts over the window."""

    metric: str
    entity: str
    used: int
    ignored: int


def column_drift(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]], table: str
) -> ColumnDrift | None:
    """How `table`'s columns differ between two snapshots' table lists.

    **Shape, not content**: a column added, removed, or whose declared type
    changed. Nullability, comments and statistics move on every sync without
    changing what a description says. `None` when the table is missing from
    either side — absent now is the binder's to flag, and absent then means
    there is no shape to compare against.
    """
    old, new = _columns(before, table), _columns(after, table)
    if old is None or new is None:
        return None
    return ColumnDrift(
        added=tuple(sorted(set(new) - set(old))),
        removed=tuple(sorted(set(old) - set(new))),
        retyped=tuple(sorted(
            name for name in set(old) & set(new)
            if old[name].lower() != new[name].lower()
        )),
    )


def needs_attention(
    doc: SemanticDocument,
    *,
    tables: Sequence[Mapping[str, Any]],
    baselines: Mapping[str, Baseline],
    metric_counts: Iterable[MetricCount],
    relied_on: Mapping[str, int],
    draft_updated_at: datetime | None,
    now: datetime,
    days: int,
) -> list[Attention]:
    """Every reason in `doc` that needs a person, most urgent first.

    `doc` must be **bound** to the snapshot `tables` belongs to: `INVALID` reads
    the binder's verdicts, and a stored flag would be as stale as the last save.
    `baselines` maps a lower-cased table to the version that last changed its
    entity; a table without one is not compared. `relied_on` maps a table to how
    many Grounded answers in the last `days` touched it. `draft_updated_at` is
    `None` when there is no draft.
    """
    found: list[Attention] = []

    if draft_updated_at is not None:
        age = (now - draft_updated_at).days
        if age >= DRAFT_DAYS:
            found.append(Attention(DRAFT_OLD, detail={"days": age}))

    live = [e for e in doc.entities if not e.exclude]
    for entity in live:
        broken = _broken(entity)
        if broken:
            found.append(Attention(INVALID, entity.table.lower(), detail=broken))

    for entity in live:
        key = entity.table.lower()
        baseline = baselines.get(key)
        if baseline is None or not entity.valid:
            continue
        drift = column_drift(baseline.tables, tables, key)
        if drift:
            found.append(Attention(COLUMNS_CHANGED, key, detail={
                "version": baseline.version,
                "added": list(drift.added),
                "removed": list(drift.removed),
                "retyped": list(drift.retyped),
            }))

    defined = {
        (e.table.lower(), m.name.lower()) for e in live for m in e.metrics
    }
    for count in sorted(metric_counts, key=lambda c: (c.entity.lower(), c.metric.lower())):
        pair = (count.entity.lower(), count.metric.lower())
        if count.ignored > count.used and pair in defined:
            found.append(Attention(
                METRIC_IGNORED, count.entity.lower(), count.metric,
                detail={"used": count.used, "ignored": count.ignored, "days": days},
            ))

    for entity in live:
        key = entity.table.lower()
        answers = relied_on.get(key, 0)
        if (
            answers
            and entity.provenance.source == "llm"
            and not entity.provenance.reviewed
        ):
            found.append(Attention(
                UNREVIEWED_RELIED_ON, key, detail={"answers": answers, "days": days},
            ))

    described = {e.table.lower() for e in doc.entities}
    for table in tables:
        key = _qualified(table)
        if key not in described:
            found.append(Attention(
                UNDESCRIBED, key, detail={"columns": len(table.get("columns") or [])},
            ))

    order = {reason: index for index, reason in enumerate(REASONS)}
    return sorted(found, key=lambda a: order[a.reason])


def _broken(entity: SemanticEntity) -> dict[str, Any]:
    """What the binder refused on this entity, counted. Empty when nothing."""
    columns = sum(1 for c in entity.columns if not c.valid)
    metrics = sum(1 for m in entity.metrics if not m.valid)
    if entity.valid and not columns and not metrics:
        return {}
    issue = entity.issue if not entity.valid else next(
        (x.issue for x in [*entity.metrics, *entity.columns] if not x.valid and x.issue), "",
    )
    return {
        "entity": not entity.valid, "columns": columns, "metrics": metrics, "issue": issue,
    }


def _columns(tables: Sequence[Mapping[str, Any]], table: str) -> dict[str, str] | None:
    for candidate in tables:
        if _qualified(candidate) == table.lower():
            return {
                str(c.get("name", "")).lower(): str(c.get("data_type", ""))
                for c in candidate.get("columns") or []
                if c.get("name")
            }
    return None


def _qualified(table: Mapping[str, Any]) -> str:
    return f"{table.get('schema', '')}.{table.get('name', '')}".lower()
