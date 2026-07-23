"""Single source of truth for environment configuration."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── app ──────────────────────────────────────────────────────────────
    app_name: str = "raymand"
    environment: Literal["local", "ci", "staging", "production"] = "local"
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ── application database ─────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://raymand:raymand@localhost:5432/raymand"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # ── auth ─────────────────────────────────────────────────────────────
    jwt_secret: SecretStr = SecretStr("change-me-in-production")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900          # 15 minutes
    refresh_token_ttl_seconds: int = 1_209_600   # 14 days
    refresh_cookie_name: str = "raymand_refresh"
    refresh_cookie_secure: bool = False

    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536              # 64 MiB
    argon2_parallelism: int = 4

    admin_email: str = "admin@raymand.local"
    admin_password: SecretStr = SecretStr("raymand")
    admin_display_name: str = "Administrator"

    # ── secrets ──────────────────────────────────────────────────────────
    # 32-byte urlsafe-base64 key. Generate: python -c
    #   "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    secret_box_key: SecretStr = SecretStr("")
    secret_box_key_version: int = 1

    # ── run execution ────────────────────────────────────────────────────
    max_concurrent_runs: int = 8
    run_deadline_seconds: int = 120
    run_heartbeat_seconds: int = 10
    run_stale_after_seconds: int = 60
    reconciler_interval_seconds: int = 30

    # ── sql guard / execution defaults ───────────────────────────────────
    default_max_rows: int = 1000
    default_statement_timeout_ms: int = 30_000
    hard_row_cap: int = 100_000

    # ── llm ──────────────────────────────────────────────────────────────
    llm_request_timeout_seconds: int = 60
    prompt_version: str = "v1"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
