from __future__ import annotations

import base64
import os

import pytest

from app.core.logging import configure_logging


@pytest.fixture(autouse=True, scope="session")
def _test_environment() -> None:
    """Deterministic settings so tests never touch a real deployment."""
    os.environ.setdefault(
        "SECRET_BOX_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
    )
    os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
    os.environ.setdefault("ENVIRONMENT", "ci")


@pytest.fixture(autouse=True, scope="session")
def _plain_logs() -> None:
    """JSON logs in tests, never structlog's rich console renderer.

    Not cosmetic. An unhandled exception inside a route is logged with
    `log.exception`, and the rich renderer walks every frame's locals — one of
    which, in an API test, is a SQLAlchemy `Select`. Rendering it took **over a
    minute per failure** and turned "one test asserts the wrong thing" into a
    suite that looked hung. The production default is JSON anyway; this only
    stops the default config from applying before `create_app` configures it.
    """
    configure_logging(json_logs=True, level="WARNING")
