from __future__ import annotations

from app.core.errors import ValidationError
from app.domain.ports.database import DatabaseConnector
from app.domain.value_objects import DatabaseKind
from app.infra.connectors.postgres import PostgresConnector


def build_connector(
    *, kind: str, host: str, port: int, database: str,
    username: str, password: str, ssl_mode: str | None = None,
) -> DatabaseConnector:
    if kind == DatabaseKind.POSTGRES:
        return PostgresConnector(
            host=host, port=port, database=database,
            username=username, password=password, ssl_mode=ssl_mode,
        )
    # MySQL and SQL Server land in phase 7, once the connector contract test
    # suite exists. Each is then a day of work rather than a week of surprises.
    raise ValidationError(
        f"Database type {kind!r} is not supported yet. PostgreSQL only in this release."
    )
