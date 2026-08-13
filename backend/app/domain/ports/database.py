from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.domain.value_objects import HintBudget


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None   # "schema.table.column"

    # Content hints captured at sync time. A connector may leave every one of
    # these unset — they are an accuracy aid, never a correctness dependency,
    # and only the Postgres connector populates them today. What reaches the
    # model is decided per run by `HintBudget`, not here; see
    # `domain/value_objects` for the capture floor and the policy ladder.
    distinct_count: int | None = None
    null_fraction: float | None = None
    sample_values: list[str] = field(default_factory=list)
    min_value: str | None = None
    max_value: str | None = None

    # The description the database's own catalog carries for this column —
    # `COMMENT ON COLUMN`, MySQL's `COLUMN_COMMENT`, an `MS_Description`
    # extended property, `ALL_COL_COMMENTS`. Unlike every field above it, this
    # is **not derived from the data**: a human wrote it in DDL, it does not
    # change when a row changes, and it is therefore exactly as much "customer
    # data" as `name` is. That is why it is captured regardless of `HintBudget`,
    # which gates only what was read out of the rows.
    comment: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The stored snapshot shape.

        Serialisation lives here rather than at each call site because it had
        already been copied into two of them, and the copies silently dropped
        every field added after they were written. Unset hints are omitted, so
        a snapshot with no statistics is byte-identical to the old format.
        """
        out: dict[str, Any] = {
            "name": self.name,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "is_primary_key": self.is_primary_key,
            "is_foreign_key": self.is_foreign_key,
            "references": self.references,
        }
        if self.distinct_count is not None:
            out["distinct_count"] = self.distinct_count
        if self.null_fraction is not None:
            out["null_fraction"] = self.null_fraction
        if self.sample_values:
            out["sample_values"] = list(self.sample_values)
        if self.min_value is not None:
            out["min_value"] = self.min_value
        if self.max_value is not None:
            out["max_value"] = self.max_value
        if self.comment:
            out["comment"] = self.comment
        return out


@dataclass(frozen=True, slots=True)
class TableInfo:
    schema: str
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    approx_row_count: int | None = None
    comment: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def as_dict(self) -> dict[str, Any]:
        # `comment` is emitted only when it is set, the same rule the column
        # hints follow: a snapshot from a database with no comments has to
        # serialise byte-identically to one taken before the field existed, or
        # every stored snapshot and the eval baseline drift on the same day.
        # (The field has been on this dataclass since the beginning and was
        # never populated *or* serialised — so it could not have survived a sync
        # even if a connector had set it.)
        out: dict[str, Any] = {
            "schema": self.schema,
            "name": self.name,
            "approx_row_count": self.approx_row_count,
            "columns": [c.as_dict() for c in self.columns],
        }
        if self.comment:
            out["comment"] = self.comment
        return out


@dataclass(frozen=True, slots=True)
class RelationshipInfo:
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    dialect: str
    tables: list[TableInfo] = field(default_factory=list)
    relationships: list[RelationshipInfo] = field(default_factory=list)
    server_version: str | None = None

    # Catalog descriptions one level up from a table. Table and column comments
    # ride inside `tables`, which is already stored as JSONB and needs no
    # migration; these two have nowhere to go and are what
    # `schema_snapshots.catalog_meta` is for (Phase 2).
    #
    # Only PostgreSQL and SQL Server have either — MySQL has no schema comment
    # outside MariaDB, and Oracle has neither — so nothing downstream may depend
    # on them being present. They are the natural seed for the semantic layer's
    # `business_context`, which stays fully generated on the other two.
    database_comment: str | None = None
    schema_comments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResultColumn:
    name: str
    db_type: str
    semantic_type: str = "nominal"   # quantitative | temporal | nominal | ordinal


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[ResultColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    duration_ms: int = 0
    rows_scanned_estimate: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectionProbe:
    ok: bool
    latency_ms: int
    server_version: str | None = None
    readonly_confirmed: bool = False
    message: str | None = None


class SchemaInspector(Protocol):
    async def introspect(
        self, *, schema_allowlist: list[str], hints: HintBudget = HintBudget()
    ) -> SchemaSnapshot:
        """`hints` caps what may be captured *about column contents*.

        It defaults to the closed budget so a caller that does not pass the
        connection's disclosure policy captures structure only, never values.
        """
        ...


class QueryExecutor(Protocol):
    """Executes only SQL that the guard has already approved."""

    async def execute(
        self, sql: str, *, max_rows: int, statement_timeout_ms: int
    ) -> QueryResult: ...

    async def explain(self, sql: str) -> int | None: ...


class DatabaseConnector(SchemaInspector, QueryExecutor, Protocol):
    dialect: str

    async def probe(self) -> ConnectionProbe: ...

    async def close(self) -> None: ...
