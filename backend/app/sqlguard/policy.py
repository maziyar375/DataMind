"""The allowlist. Everything not named here is rejected.

The rule is fail-closed: an unknown AST node type is a rejection, not a
warning. New SQLGlot versions introducing new expression classes therefore
cause false rejections, never bypasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlglot import expressions as exp

# ── Expression classes the guard will accept anywhere in the tree ────────
ALLOWED_NODES: frozenset[type[exp.Expression]] = frozenset({
    # statement + clauses
    exp.Select, exp.From, exp.Where, exp.Group, exp.Having, exp.Order,
    exp.Limit, exp.Offset, exp.Ordered, exp.Distinct, exp.Subquery,
    exp.With, exp.CTE, exp.Union, exp.Intersect, exp.Except, exp.Lateral,
    # joins
    exp.Join, exp.Table, exp.Alias, exp.TableAlias,
    # identifiers + literals
    exp.Column, exp.Identifier, exp.Literal, exp.Star, exp.Null,
    exp.Boolean, exp.Placeholder, exp.Parameter, exp.Tuple, exp.Array,
    # operators
    exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT,
    exp.LTE, exp.Is, exp.In, exp.Between, exp.Like, exp.ILike, exp.Add,
    exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg, exp.Paren, exp.Bracket,
    exp.Case, exp.If, exp.Coalesce, exp.Cast, exp.TryCast, exp.Exists,
    exp.Any, exp.All,
    # aggregates + common functions
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Abs, exp.Round,
    exp.Ceil, exp.Floor, exp.Length, exp.Lower, exp.Upper, exp.Trim,
    exp.Substring, exp.Concat, exp.Extract, exp.DateTrunc, exp.DateAdd,
    exp.DateDiff, exp.DateSub, exp.CurrentDate, exp.CurrentTimestamp,
    exp.Window, exp.WindowSpec, exp.RowNumber, exp.Rank,
    exp.DenseRank, exp.Interval, exp.Anonymous, exp.Dot, exp.Nullif,
    exp.Stddev, exp.Variance, exp.Sort, exp.Where, exp.Filter,
    # SQLGlot normalises several standard functions into dedicated classes;
    # rejecting these would reject ordinary analytics SQL.
    exp.TimestampTrunc, exp.TimestampAdd, exp.TimestampDiff, exp.TimestampSub,
    exp.DateStrToDate, exp.StrToTime, exp.TimeToStr, exp.TsOrDsToDate,
    exp.CurrentTime, exp.Var, exp.Quantile, exp.ApproxDistinct,
    exp.Cbrt, exp.Exp, exp.Pow, exp.Sqrt, exp.Escape, exp.Collate,
})

# ── Function names permitted inside exp.Anonymous ────────────────────────
# exp.Anonymous is how SQLGlot represents any function it does not model.
# Without this list, allowing Anonymous would allow pg_read_file().
ALLOWED_FUNCTIONS: frozenset[str] = frozenset({
    "abs", "avg", "cast", "ceil", "ceiling", "char_length", "coalesce",
    "concat", "concat_ws", "count", "date_part", "date_trunc", "extract",
    "floor", "greatest", "initcap", "least", "left", "length", "ln", "log",
    "lower", "lpad", "ltrim", "max", "min", "mod", "nullif", "percentile_cont",
    "position", "power", "rank", "regexp_replace", "repeat", "replace",
    "reverse", "right", "round", "row_number", "rpad", "rtrim", "sign",
    "split_part", "sqrt", "stddev", "stddev_pop", "stddev_samp", "strpos",
    "substr", "substring", "sum", "to_char", "to_date", "to_timestamp",
    "trim", "trunc", "upper", "variance", "var_pop", "var_samp",
    "dense_rank", "lag", "lead", "first_value", "last_value", "ntile",
    "age", "now", "current_date", "current_timestamp", "justify_interval",
})

# ── Identifiers that must never appear, in any position ──────────────────
FORBIDDEN_IDENTIFIER_PREFIXES: tuple[str, ...] = (
    "pg_", "information_schema", "sys.", "mysql.", "performance_schema",
    "sqlite_", "dbo.sys", "master.", "msdb.",
)

FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "pg_sleep", "pg_read_file", "pg_ls_dir", "lo_import", "lo_export",
    "dblink", "copy ", "into outfile", "load_file", "xp_cmdshell",
    "sp_executesql", "openrowset", "opendatasource", "bulk insert",
    "current_setting", "set_config", "pg_terminate", "pg_cancel",
)


@dataclass(slots=True)
class GuardPolicy:
    """Per-connection policy, materialised from the connection row + snapshot."""

    dialect: str = "postgres"
    max_rows: int = 1000
    allowed_tables: set[str] = field(default_factory=set)
    """Fully qualified `schema.table`, lowercased. Empty means 'nothing yet'."""
    allowed_columns: dict[str, set[str]] = field(default_factory=dict)
    """`schema.table` -> set of lowercased column names."""
    allow_star: bool = True
    max_joins: int = 10
    max_subquery_depth: int = 4
    require_limit: bool = True

    def table_known(self, qualified: str) -> bool:
        return qualified.lower() in self.allowed_tables

    def column_known(self, qualified_table: str, column: str) -> bool:
        cols = self.allowed_columns.get(qualified_table.lower())
        return bool(cols) and column.lower() in cols

    def resolve_unqualified(self, table_name: str) -> str | None:
        """Map a bare `orders` onto exactly one `schema.orders`, or fail."""
        name = table_name.lower()
        matches = [t for t in self.allowed_tables if t.split(".")[-1] == name]
        return matches[0] if len(matches) == 1 else None
