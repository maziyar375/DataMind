"""Request-scoped context and correlation id propagation."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(value: str | None = None) -> str:
    cid = value or uuid.uuid4().hex
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    return _correlation_id.get()


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Passed to every service and repository call. Scoping is not optional."""

    user_id: UUID
    email: str
    role: str
    session_id: UUID | None = None
    correlation_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"
