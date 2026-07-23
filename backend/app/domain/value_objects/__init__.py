"""Value objects. No I/O, no framework imports."""
from __future__ import annotations

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


class StepName(StrEnum):
    ROUTE = "route"
    CLARIFY = "clarify"
    RETRIEVE = "retrieve"
    GENERATE = "generate"
    VALIDATE = "validate"
    EXECUTE = "execute"
    PRESENT = "present"


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
    CLARIFICATION = "CLARIFICATION"
    ERROR = "ERROR"
    SQL_SUMMARY = "SQL_SUMMARY"


class DisclosurePolicy(StrEnum):
    """What may leave the customer database and reach an external model."""

    NONE = "NONE"           # nothing; the model never sees result data
    AGGREGATE = "AGGREGATE"  # only counts and summary statistics
    SAMPLE = "SAMPLE"        # a bounded row sample
    FULL = "FULL"            # the whole (capped) result set


class RunEventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    SQL_GENERATED = "SQL_GENERATED"
    SQL_VALIDATED = "SQL_VALIDATED"
    SQL_REJECTED = "SQL_REJECTED"
    QUERY_COMPLETED = "QUERY_COMPLETED"
    ARTIFACT_CREATED = "ARTIFACT_CREATED"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    TEXT_DELTA = "TEXT_DELTA"
    ERROR = "ERROR"
    RUN_FINISHED = "RUN_FINISHED"
