from __future__ import annotations

import base64
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _test_environment() -> None:
    """Deterministic settings so tests never touch a real deployment."""
    os.environ.setdefault(
        "SECRET_BOX_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode()
    )
    os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
    os.environ.setdefault("ENVIRONMENT", "ci")
