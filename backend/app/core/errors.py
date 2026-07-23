"""Framework-free error hierarchy. Mapped to RFC 7807 at the API edge."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class. `code` is the stable, machine-readable contract."""

    code: str = "E_INTERNAL"
    http_status: int = 500
    title: str = "Internal error"

    def __init__(self, message: str | None = None, **detail: Any) -> None:
        super().__init__(message or self.title)
        self.message = message or self.title
        self.detail = detail


class NotFoundError(AppError):
    code = "E_NOT_FOUND"
    http_status = 404
    title = "Resource not found"


class ValidationError(AppError):
    code = "E_VALIDATION"
    http_status = 422
    title = "Invalid request"


class AuthenticationError(AppError):
    code = "E_UNAUTHENTICATED"
    http_status = 401
    title = "Authentication required"


class ForbiddenError(AppError):
    code = "E_FORBIDDEN"
    http_status = 403
    title = "Not permitted"


class ConflictError(AppError):
    code = "E_CONFLICT"
    http_status = 409
    title = "Conflicting state"


class SqlRejectedError(AppError):
    """The SQL guard refused to let a statement through. Never reaches a driver."""

    code = "E_SQL_REJECTED"
    http_status = 422
    title = "Generated SQL rejected"


class TableNotAllowedError(SqlRejectedError):
    code = "E_TABLE_NOT_ALLOWED"


class ConnectorError(AppError):
    code = "E_CONNECTOR"
    http_status = 502
    title = "Target database error"


class LLMError(AppError):
    code = "E_LLM"
    http_status = 502
    title = "Model provider error"


class RunTimeoutError(AppError):
    code = "E_TIMEOUT"
    http_status = 504
    title = "Run exceeded its deadline"
