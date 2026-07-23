from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: str | None = None   # "schema.table.column"


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
    async def introspect(self, *, schema_allowlist: list[str]) -> SchemaSnapshot: ...


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
