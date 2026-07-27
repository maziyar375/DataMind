"""Column content hints, shared across every engine.

What the model is told about a column's *contents* has to mean the same thing
whether the connection is Postgres, MySQL, SQL Server or Oracle — otherwise a
value list is trustworthy on one engine and a guess on another, and the
generator has no way to tell which it is looking at. This module owns that
contract; each connector supplies only the engine-specific way of asking.

Two sources, in order of preference:

1. **The catalog.** Every engine keeps optimiser statistics, and three of the
   four expose distinct counts, null fractions and (for low-cardinality
   columns) the values themselves. This costs one extra query per sync and
   never touches user tables.
2. **A bounded probe.** Where the catalog has nothing — SQL Server without
   `VIEW DATABASE STATE`, a MySQL table with no histogram, any database that
   has never been analysed — one `SELECT DISTINCT … LIMIT n+1` per candidate
   column. It is capped hard because it is the only path here that reads user
   data, and skipped entirely for large tables.

**A value list is emitted only when it is known to be complete.** The probe
asks for one more value than the cap and discards the column if it gets it, so
`∈ {…}` never means "some of the values" — the generator is told to filter
using exactly these, and a truncated list would make that instruction wrong.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.value_objects import (
    HINT_MAX_CARDINALITY,
    HINT_MAX_VALUE_CHARS,
    HintBudget,
    is_sensitive_column,
)

# The probe is the only hint source that reads user rows, so it is bounded on
# three axes: how many columns per sync, how wide a table it will scan, and
# how long any single probe may run.
PROBE_MAX_COLUMNS = 120
PROBE_MAX_TABLE_ROWS = 2_000_000
PROBE_TIMEOUT_MS = 3_000

_ENUM_RE = re.compile(r"^(enum|set)\((.*)\)$", re.IGNORECASE | re.DOTALL)
_ENUM_MEMBER_RE = re.compile(r"'((?:[^']|'')*)'")


@dataclass(frozen=True, slots=True)
class ColumnHints:
    """What one capture source learned about one column. Engine-neutral."""

    distinct_count: int | None = None
    null_fraction: float | None = None
    sample_values: list[str] = field(default_factory=list)
    min_value: str | None = None
    max_value: str | None = None

    @property
    def empty(self) -> bool:
        return not (
            self.distinct_count is not None
            or self.null_fraction is not None
            or self.sample_values
            or self.min_value
            or self.max_value
        )

    def merged_with(self, other: ColumnHints) -> ColumnHints:
        """`self` wins; `other` fills only what `self` does not know.

        Used to let a cheap catalog read take precedence over a probe, so a
        probe never overwrites an authoritative statistic with a sampled one.
        """
        return ColumnHints(
            distinct_count=(
                self.distinct_count
                if self.distinct_count is not None
                else other.distinct_count
            ),
            null_fraction=(
                self.null_fraction
                if self.null_fraction is not None
                else other.null_fraction
            ),
            sample_values=self.sample_values or other.sample_values,
            min_value=self.min_value or other.min_value,
            max_value=self.max_value or other.max_value,
        )

    def as_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for `ColumnInfo`, omitting what is unknown."""
        out: dict[str, Any] = {}
        if self.distinct_count is not None:
            out["distinct_count"] = self.distinct_count
        if self.null_fraction is not None:
            out["null_fraction"] = self.null_fraction
        if self.sample_values:
            out["sample_values"] = self.sample_values
        if self.min_value:
            out["min_value"] = self.min_value
        if self.max_value:
            out["max_value"] = self.max_value
        return out


def clean_values(raw: Any) -> list[str]:
    """Normalise candidate values, or return empty if they are unusable.

    Rejects the whole list rather than trimming it: a partial `∈ {…}` would
    tell the generator to filter using values that are not all of them.
    """
    if not raw:
        return []
    values: list[str] = []
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        if not text or len(text) > HINT_MAX_VALUE_CHARS:
            return []          # prose, not a category
        values.append(text)
    unique = sorted(set(values))
    if not unique or len(unique) > HINT_MAX_CARDINALITY:
        return []
    return unique


def parse_enum_members(column_type: str | None) -> list[str]:
    """`enum('web','phone')` -> the members. MySQL's free, exact value list.

    The declared type *is* the domain here, so this is better than any
    statistic: complete by construction and costing nothing to read.
    """
    if not column_type:
        return []
    match = _ENUM_RE.match(column_type.strip())
    if not match:
        return []
    members = [m.replace("''", "'") for m in _ENUM_MEMBER_RE.findall(match.group(2))]
    return clean_values(members)


def null_fraction_from_counts(
    null_count: int | None, row_count: int | None
) -> float | None:
    """Oracle and SQL Server report a null *count*; the hint is a fraction."""
    if null_count is None or not row_count or row_count <= 0:
        return None
    return round(min(1.0, max(0.0, null_count / row_count)), 4)


def normalise_distinct(
    n_distinct: float | None, approx_rows: int | None
) -> int | None:
    """A distinct count that may be expressed as a negative fraction of rows.

    Postgres uses that encoding in `pg_stats.n_distinct` (-1 means every value
    is unique); the other engines report a plain count, which passes through.
    """
    if not n_distinct:
        return None
    if n_distinct > 0:
        return int(n_distinct)
    if approx_rows:
        return max(1, int(abs(n_distinct) * approx_rows))
    return None


def enforce_budget(
    hints: Mapping[tuple[str, str, str], ColumnHints], budget: HintBudget
) -> dict[tuple[str, str, str], ColumnHints]:
    """Drop at *capture* whatever this connection's policy could never emit.

    `RetrievedContext.render` already gates every hint on the policy in force
    at ask time, so nothing here can reach the model regardless. This is the
    second, stricter question: should DataMind's own database hold a copy of
    the customer's column values at all? For a connection whose policy says
    the model never sees its data, the answer is no — storing them anyway
    would honour the letter of the policy while ignoring what it was for.

    The asymmetry is deliberate. Tightening a policy takes effect on the very
    next question, because render filters what is already stored. *Loosening*
    one needs a re-sync, because the values were never captured. Failing in
    that direction is the right way round.
    """
    if not budget.stats:
        return {}
    kept = dict(hints)
    if not budget.value_lists:
        kept = {k: replace(v, sample_values=[]) for k, v in kept.items()}
    if not (budget.temporal_range or budget.numeric_range):
        kept = {k: replace(v, min_value=None, max_value=None) for k, v in kept.items()}
    return kept


def quote_ident(name: str, *, style: str = '"') -> str:
    """Quote a catalog identifier for interpolation into a probe statement.

    A probe names its table and column inline because an identifier cannot be
    bound as a parameter. The names come from the engine's own catalog rather
    than from user input, but "from the catalog" is not "safe": a customer may
    legitimately have a table called `weird"name`, and doubling the closing
    character is what keeps that from ending the quoted identifier early.
    """
    if style == "`":
        return "`" + name.replace("`", "``") + "`"
    if style == "[":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    schema: str
    table: str
    column: str

    def quoted(self, style: str = '"') -> tuple[str, str]:
        """(qualified table, column), both quoted for `style`."""
        return (
            f"{quote_ident(self.schema, style=style)}."
            f"{quote_ident(self.table, style=style)}",
            quote_ident(self.column, style=style),
        )


def plan_probes(
    *,
    columns: Mapping[tuple[str, str, str], str],
    known: Mapping[tuple[str, str, str], ColumnHints],
    row_counts: Mapping[tuple[str, str], int | None],
    text_types: frozenset[str],
    limit: int = PROBE_MAX_COLUMNS,
) -> list[ProbeTarget]:
    """Which columns are worth one `SELECT DISTINCT` each.

    Deliberately conservative — a probe is the only place this feature reads
    user rows, so a column has to clear every one of these before it earns
    one, and the whole plan is capped regardless.

    `columns` maps (schema, table, column) -> data type.
    """
    targets: list[ProbeTarget] = []

    for (schema, table, column), data_type in sorted(columns.items()):
        if len(targets) >= limit:
            break
        if _base_type(data_type) not in text_types:
            continue
        if is_sensitive_column(column):
            continue
        hints = known.get((schema, table, column), ColumnHints())
        if hints.sample_values:
            continue           # the catalog already answered
        if (
            hints.distinct_count is not None
            and hints.distinct_count > HINT_MAX_CARDINALITY
        ):
            continue           # known to be too wide to be an enum
        rows = row_counts.get((schema, table))
        if rows is not None and rows > PROBE_MAX_TABLE_ROWS:
            continue           # too big to scan for a nicety
        targets.append(ProbeTarget(schema=schema, table=table, column=column))

    return targets


def _base_type(data_type: str) -> str:
    """`varchar(50)` and `NVARCHAR2(20 CHAR)` -> the bare type name."""
    return data_type.split("(")[0].strip().lower()


def apply_probe(
    known: dict[tuple[str, str, str], ColumnHints],
    target: ProbeTarget,
    values: list[Any],
) -> None:
    """Fold one probe result into the hint map, in place.

    `values` must have been fetched with a limit of `HINT_MAX_CARDINALITY + 1`
    so that an over-long list is recognisable as incomplete and dropped.
    """
    key = (target.schema, target.table, target.column)
    cleaned = clean_values(values)
    if not cleaned:
        return
    current = known.get(key, ColumnHints())
    known[key] = replace(
        current,
        sample_values=cleaned,
        distinct_count=(
            current.distinct_count
            if current.distinct_count is not None
            else len(cleaned)
        ),
    )
