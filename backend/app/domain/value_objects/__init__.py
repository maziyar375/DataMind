"""Value objects. No I/O, no framework imports."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    DISABLED = "DISABLED"


class DatabaseKind(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MSSQL = "mssql"
    ORACLE = "oracle"

    @property
    def sqlglot_dialect(self) -> str:
        """What the guard parses and renders with.

        Only MSSQL differs from its own name; sqlglot calls that dialect
        `tsql`. Keeping the mapping here means a new kind cannot be added
        without deciding how its SQL is to be read.
        """
        return _SQLGLOT_DIALECTS[self]

    @property
    def default_port(self) -> int:
        return _DEFAULT_PORTS[self]


_SQLGLOT_DIALECTS = {
    DatabaseKind.POSTGRES: "postgres",
    DatabaseKind.MYSQL: "mysql",
    DatabaseKind.MSSQL: "tsql",
    DatabaseKind.ORACLE: "oracle",
}

_DEFAULT_PORTS = {
    DatabaseKind.POSTGRES: 5432,
    DatabaseKind.MYSQL: 3306,
    DatabaseKind.MSSQL: 1433,
    DatabaseKind.ORACLE: 1521,
}


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunStatus.SUCCEEDED, RunStatus.FAILED,
            RunStatus.CANCELLED, RunStatus.TIMED_OUT,
        }

    @property
    def is_in_flight(self) -> bool:
        """Whether the executor may still emit events for this run.

        Not the inverse of `is_terminal`: `NEEDS_CLARIFICATION` is neither.
        The run is over as far as the event stream is concerned — it wrote its
        question and closed — while the *exchange* is unfinished, which is what
        `is_terminal` speaks to (cancel still applies, the reconciler stays
        away). Anything deciding whether to wait for more events wants this one.
        """
        return self in {RunStatus.QUEUED, RunStatus.RUNNING}


class StepName(StrEnum):
    ROUTE = "route"
    CLARIFY = "clarify"
    RETRIEVE = "retrieve"
    DESCRIBE = "describe"
    GENERATE = "generate"
    VALIDATE = "validate"
    EXECUTE = "execute"
    INSPECT = "inspect"
    PRESENT = "present"
    CHART = "chart"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class ArtifactKind(StrEnum):
    TABLE = "TABLE"
    CHART = "CHART"
    # One number drawn big. Its own kind rather than a chart with an unusual
    # spec: it carries no Vega-Lite, and a reader that expected one would find
    # nothing to render.
    KPI = "KPI"
    CLARIFICATION = "CLARIFICATION"
    ERROR = "ERROR"
    SQL_SUMMARY = "SQL_SUMMARY"


# ── dashboards ───────────────────────────────────────────────────────────
class DashboardStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class TileType(StrEnum):
    CHART = "CHART"
    TABLE = "TABLE"
    METRIC = "METRIC"    # one row, one numeric column, drawn big
    TEXT = "TEXT"        # prose; no SQL, no connection


class SqlOrigin(StrEnum):
    """How a tile's SQL came to be. **Provenance only, never a trust signal.**

    The guard cannot tell these apart and must not try: a `HANDWRITTEN`
    statement is re-validated exactly as a `GENERATED` one is, on every single
    execution. This exists so the editor can show where the text came from and
    so "edit → re-ask" knows whether the draft is still the model's.
    """

    GENERATED = "GENERATED"
    GENERATED_EDITED = "GENERATED_EDITED"
    HANDWRITTEN = "HANDWRITTEN"


# ── reports ──────────────────────────────────────────────────────────────
class ReportStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ReportLanguage(StrEnum):
    """The language a report's prose is written in.

    Pinned at creation and stated explicitly in every prose prompt, never
    inferred per section — otherwise a section whose heading happens to be a
    metric name comes back in the other language.
    """

    FA = "fa"
    EN = "en"


class ReportSectionKind(StrEnum):
    NORMAL = "NORMAL"
    # Written last, from the finished sections' prose rather than from a query
    # of its own. Removable, which is why it is a kind and not a flag column.
    EXECUTIVE_SUMMARY = "EXECUTIVE_SUMMARY"


class ReportBlockType(StrEnum):
    """One question, one query, one rendering.

    No TEXT member, unlike `TileType`: prose in a report belongs to the
    section, and a block that ran no query would have nothing to narrate.
    """

    CHART = "CHART"
    TABLE = "TABLE"
    METRIC = "METRIC"


class ReportTimeWindow(StrEnum):
    """A *label*, and only a label.

    It drives the prompt when a block is checked and gives the UI something to
    show; it is never substituted into a statement at runtime. The window lives
    in the SQL itself as relative date arithmetic the database resolves on
    every run — see §6 of `docs/reports-plan.md`.
    """

    NONE = "none"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    LAST_MONTH = "last_month"
    LAST_3_MONTHS = "last_3_months"
    LAST_12_MONTHS = "last_12_months"
    PREVIOUS_QUARTER = "previous_quarter"
    YTD = "ytd"
    CUSTOM = "custom"


class ReportFeasibility(StrEnum):
    """Whether a block can be produced, answered mechanically by the guard."""

    UNCHECKED = "UNCHECKED"
    FEASIBLE = "FEASIBLE"
    # Valid SQL, no rows in the window. Not a failure, and not infeasible: the
    # query works and the answer is "nothing happened".
    EMPTY = "EMPTY"
    INFEASIBLE = "INFEASIBLE"


class ReportRunStatus(StrEnum):
    """A run is a set of independently-failable parts, so it has a state
    nothing else in this codebase has: `PARTIAL`.

    The status is *derived* from the run's section results rather than set, and
    that is what makes progressive rendering and per-section retry fall out
    without any resume machinery — a successful retry turns a PARTIAL run into
    a SUCCEEDED one with no state transition to write.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ReportRunStatus.SUCCEEDED, ReportRunStatus.PARTIAL,
            ReportRunStatus.FAILED, ReportRunStatus.CANCELLED,
        }


class ReportBlockResultStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"


class ReportSectionResultStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    # Every block under the heading came back empty. A report that says "no
    # returns were recorded in this period" is correct; one that invents
    # returns is not, so this is its own outcome rather than a failure.
    SKIPPED_NO_DATA = "SKIPPED_NO_DATA"


class DisclosurePolicy(StrEnum):
    """What may leave the customer database and reach an external model."""

    NONE = "NONE"           # nothing; the model never sees result data
    AGGREGATE = "AGGREGATE"  # only counts and summary statistics
    SAMPLE = "SAMPLE"        # a bounded row sample
    FULL = "FULL"            # the whole (capped) result set


# ── column hints: the second half of the disclosure policy ──────────────────
# `pipeline/disclosure.py` governs how much of a *result* reaches the model.
# The same customer data also leaks the other way — through the schema block,
# which carries per-column statistics captured at sync time. It lives here
# because both sides need it: infra connectors decide what to *capture*, and
# `RetrievedContext.render` decides what to *emit* for the policy in force at
# ask time. Capturing and rendering are deliberately separate: a connection
# downgraded from FULL to NONE must stop emitting hints immediately, without
# waiting for a re-sync.

# Never captured, at any policy. Low cardinality is not the same as
# non-sensitive: a 12-value `city` column is still a disclosure. This is a
# floor under the ladder below, not another rung of it.
SENSITIVE_COLUMN_TOKENS = (
    "name", "email", "mail", "phone", "tel", "mobile", "address", "addr",
    "street", "city", "postal", "zip", "ssn", "tax", "passport", "iban",
    "account", "card", "token", "secret", "password", "hash", "ref",
    "tracking", "url", "website", "note", "comment", "description", "body",
    "title", "subject", "lat", "lon", "ip",
)

_SENSITIVE_RE = re.compile(
    r"(^|_)(" + "|".join(SENSITIVE_COLUMN_TOKENS) + r")(e?s)?($|_)", re.IGNORECASE
)


def is_sensitive_column(name: str) -> bool:
    """True if a column's *name* alone disqualifies it from value capture.

    Token-boundary matching, so `city` and `billing_city` are excluded while
    `capacity` and `is_active` are not — a substring test would swallow both.
    The optional plural suffix matters more than it looks: without it `notes`
    and `addresses` slip past a list written in the singular.
    """
    return bool(_SENSITIVE_RE.search(name))


@dataclass(frozen=True, slots=True)
class HintBudget:
    """What the schema block may say about a column's *contents*.

    The ladder mirrors `disclose()`: NONE shares nothing derived from data,
    AGGREGATE shares counts but never a value, SAMPLE shares bounded values
    the way it shares bounded rows, FULL is the widest.
    """

    row_counts: bool = False        # approximate table cardinality
    stats: bool = False             # distinct counts, null fractions
    value_lists: bool = False       # the actual distinct values
    temporal_range: bool = False    # min/max of date and timestamp columns
    numeric_range: bool = False     # min/max of numeric columns
    max_values: int = 0             # cap on values emitted per column

    @classmethod
    def from_policy(cls, policy: str) -> HintBudget:
        if policy == DisclosurePolicy.AGGREGATE:
            return cls(row_counts=True, stats=True)
        if policy == DisclosurePolicy.SAMPLE:
            return cls(
                row_counts=True, stats=True, value_lists=True,
                temporal_range=True, max_values=25,
            )
        if policy == DisclosurePolicy.FULL:
            return cls(
                row_counts=True, stats=True, value_lists=True,
                temporal_range=True, numeric_range=True, max_values=50,
            )
        # NONE, and anything unrecognised — fail closed.
        return cls()


# A column with more distinct values than this is not an enum, and listing it
# would be both useless to the generator and a wide disclosure. Applied at
# capture time so the values are never stored in the first place.
HINT_MAX_CARDINALITY = 25

# Values longer than this are prose, not categories.
HINT_MAX_VALUE_CHARS = 40


class RunEventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    SQL_GENERATED = "SQL_GENERATED"
    SQL_VALIDATED = "SQL_VALIDATED"
    SQL_REJECTED = "SQL_REJECTED"
    QUERY_COMPLETED = "QUERY_COMPLETED"
    RESULT_CHECKED = "RESULT_CHECKED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    TEXT_DELTA = "TEXT_DELTA"
    # "Discard the answer text streamed so far, then render what follows."
    # Emitted only when narration fails part-way through, so the fallback
    # sentence does not arrive glued to half a sentence — live or on replay.
    TEXT_RESET = "TEXT_RESET"
    ERROR = "ERROR"
    RUN_FINISHED = "RUN_FINISHED"
