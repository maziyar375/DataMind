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

    # ── authorization ────────────────────────────────────────────────────
    # Which implementation of the `Authorizer` port answers "may they?".
    # `owner_only` is the rule the product shipped with — you may act on a row
    # if you own it — written down as a policy object so every call site could
    # be routed through the port before the policy changed. `rbac` adds grants,
    # teams, role scoped privileges and wildcards on top of ownership, and **is
    # the default as of Phase 6** of
    # `docs/user-management-and-access-control-plan.md`.
    #
    # The previous value stays a working rollback for one release, and the
    # rollback is a config flip in both directions rather than a migration: no
    # grant row is *read* under `owner_only`, and running under it creates none
    # — so flipping back narrows everybody to what they own, and flipping
    # forward restores every share exactly, with nothing to replay.
    authz_backend: Literal["owner_only", "rbac"] = "rbac"

    # Who verifies a human. **`local` is the only value today** and the enum
    # has one member on purpose: an unreachable branch is worse documentation
    # than an honest single value.
    #
    # Adding `oidc` is §20.3 of the access-control plan, and step 5 of that
    # recipe is the one that must not be skipped: an OIDC account is matched to
    # an existing user by a **verified** email exactly once, at first sign-in,
    # and from then on by `external_subject` — never by email again. Matching
    # on email on every sign-in makes an attacker-controlled address at the IdP
    # a takeover of the local account with the same address. Every grant, every
    # role assignment and every owned row points at `users.id`, which does not
    # change when an account is bound to an issuer, so nothing is rewritten.
    auth_provider: Literal["local"] = "local"

    # May a service user hold `user.manage`, `role.manage`,
    # `service_user.manage` or `settings.manage`? **No**, and the reason is
    # blast radius rather than tidiness: a leaked API key must not be able to
    # mint an administrator. It is a policy enforced in the service layer, not
    # an invariant enforced by the database, because an installation that runs
    # its own provisioning agent has a real reason to turn it on — and turning
    # it on writes an audit row.
    allow_privileged_service_users: bool = False

    # How long a newly minted service-user key lasts when the caller names no
    # expiry. A year, not forever: an unexpiring machine credential is the one
    # every published incident report has in common, and a default that expires
    # makes rotation a thing that happens rather than a thing that is planned.
    service_key_default_ttl_days: int = 365

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

    # ── report generation ────────────────────────────────────────────────
    # How many section paragraphs are written at once. A generation's wall
    # clock is almost entirely provider latency — one call per section, five to
    # twenty seconds each — so this is the dial that turns a nine-call document
    # from nine round trips into three.
    #
    # It is a *wave* size, not a fan-out: sections are written in groups of
    # this many, and each group is told what the groups before it established.
    # That is why the number matters rather than just being "as many as
    # possible". `1` is the old strictly-sequential behaviour, where every
    # section reads every section before it; a number at or above the section
    # count writes the whole document at once, and no section reads any other.
    # 4 keeps the second half of a report aware of the first while costing at
    # most two waves for the eight sections `outline.MAX_SECTIONS` allows.
    report_narration_concurrency: int = 4

    # ── sql guard / execution defaults ───────────────────────────────────
    default_max_rows: int = 1000
    default_statement_timeout_ms: int = 30_000
    hard_row_cap: int = 100_000

    # ── knowledge templates ──────────────────────────────────────────────
    # `curation_admin_only` lived here from Phase 8 of the learning-loop plan
    # until **Phase 6 of the access-control plan removed it**. It approximated
    # a reader/curator split with a flag because there was no way to *grant*
    # curation; now there is. `(knowledge, modify)` is that split — grantable
    # to a person or a team, on one connection, and visible in an access
    # review — so a setting that could only say "administrators and owners"
    # was strictly less expressive than the thing that replaced it.
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
    # What the *matcher* will wait for one question's vector, as opposed to
    # what an indexing pass will wait for a batch of them. Deliberately a small
    # fraction of the timeout above: the embedding matcher exists to be cheaper
    # than generating SQL, and `FallbackMatcher` answers on words the moment
    # this expires — so waiting the full sixty seconds only ever delayed an
    # answer the lexical matcher was always going to give. Indexing runs in a
    # worker and keeps the long one, where slow costs nothing.
    embedding_match_timeout_seconds: int = 5
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
