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
    # Also the worst-case latency of a cancel issued at another replica: the
    # owning process learns about `cancel_requested` on this timer.
    run_heartbeat_seconds: int = 10
    run_stale_after_seconds: int = 60
    reconciler_interval_seconds: int = 30
    # How often a replica looks for runs nobody is executing. Only ever finds
    # anything after a process died between committing a run and submitting
    # it, so it is deliberately slower than the heartbeat — the normal path is
    # the direct hand-off in `post_message`, which costs nothing.
    run_claim_interval_seconds: int = 15

    # ── sql guard / execution defaults ───────────────────────────────────
    default_max_rows: int = 1000
    default_statement_timeout_ms: int = 30_000
    hard_row_cap: int = 100_000

    # ── knowledge templates ──────────────────────────────────────────────
    # Who may teach this system a question. False — anyone signed in — is the
    # deliberate default while the product is single-player: the highest-value
    # correction comes from the person who knew the answer, and they are
    # usually not an administrator. Flipping this to true is Phase 8 of
    # `docs/learning-loop-plan.md`; every write path already asks
    # `services.policy.can_curate`, so the flip is this line and nothing else.
    curation_admin_only: bool = False
    # How often the store-health sweep runs: re-validate every live template,
    # then run near-duplicate pairs against each other and compare the rows.
    # Six hours, because a store rots on the schema's schedule rather than on
    # the request rate — a re-sync already sweeps staleness inline, and this is
    # the pass that catches the connection nobody has opened in a month. The
    # conflict half executes SQL on the customer's database and is switchable
    # off per connection (`connections.conflict_checks_enabled`).
    knowledge_maintenance_interval_seconds: int = 21_600

    # ── llm ──────────────────────────────────────────────────────────────
    llm_request_timeout_seconds: int = 60
    # Transient-failure retry (rate limits / 5xx). Bounded exponential backoff;
    # a permanent error (auth, bad request) is never retried. 0 disables it.
    llm_max_retries: int = 4
    llm_retry_base_delay_seconds: float = 2.0
    llm_retry_max_delay_seconds: float = 30.0
    # An OVERRIDE, and empty by default. What a run records is the version of
    # the prompt module that rendered it (`app.pipeline.prompts.PROMPT_VERSION`,
    # resolved by `RunService._prompt_version`) — this setting only exists so an
    # experiment can file its runs under a label of its own.
    #
    # It used to be the value itself, hardcoded to "v2" while the module moved
    # to v8, so every run in the database claimed a version it had never run.
    # The eval runner has always recorded the constant and ignores this.
    prompt_version: str | None = None
    # Default model for `python -m app.eval.runner` when --llm-config is omitted,
    # so the suite runs in one command (falls back to the sole config if unset).
    eval_llm_config_id: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
