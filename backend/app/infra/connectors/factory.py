from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from app.domain.ports.database import DatabaseConnector
from app.domain.value_objects import DatabaseKind
from app.infra.connectors.mssql import MsSqlConnector
from app.infra.connectors.mysql import MySqlConnector
from app.infra.connectors.oracle import OracleConnector
from app.infra.connectors.postgres import PostgresConnector

# Every kind the domain knows must appear here; the mapping is what makes a
# missing connector a startup-visible gap rather than a runtime surprise.
_CONNECTORS: dict[DatabaseKind, Any] = {
    DatabaseKind.POSTGRES: PostgresConnector,
    DatabaseKind.MYSQL: MySqlConnector,
    DatabaseKind.MSSQL: MsSqlConnector,
    DatabaseKind.ORACLE: OracleConnector,
}


def build_connector(
    *, kind: str, host: str, port: int, database: str,
    username: str, password: str, ssl_mode: str | None = None,
) -> DatabaseConnector:
    try:
        connector = _CONNECTORS[DatabaseKind(kind)]
    except (KeyError, ValueError) as err:
        supported = ", ".join(sorted(k.value for k in _CONNECTORS))
        raise ValidationError(
            f"Database type {kind!r} is not supported. Supported: {supported}."
        ) from err

    return connector(
        host=host, port=port, database=database,
        username=username, password=password, ssl_mode=ssl_mode,
    )
