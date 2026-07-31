#!/usr/bin/env bash
# Run the eval suite with a retry policy tuned for a rate-limiting provider.
#
# The Lightning free tier 429s hard enough that the default policy (4 retries,
# 2s base, 30s ceiling) exhausts mid-suite. An exhausted retry is scored
# OUTCOME_ERROR, which is indistinguishable in the report from the model
# getting the question wrong — so a rate limit silently reads as a worse model.
# This widens the retry envelope so a 429 costs wall-clock time instead of an
# eval point. It changes no prompt, no scoring, and no pipeline behaviour: the
# only knobs it touches are the transient-failure ones the gateway already
# honours (`LiteLLMGateway.from_settings`), and the provider's own `Retry-After`
# still wins over the computed backoff.
#
# Every value is overridable, so a run against a provider that does not
# rate-limit can just set LLM_MAX_RETRIES=4 and get the stock policy back.
#
#   scripts/eval_run.sh --suite sales_v1 --llm-config <uuid>
#   scripts/eval_run.sh --suite sales_v1 --limit 5          # smoke test
#   LLM_MAX_RETRIES=12 scripts/eval_run.sh --suite sales_v1 # more patient still
#
# Run from `backend/`. Every argument is passed through to the runner untouched.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="${ENV_FILE:-../.env}"

# The runner needs the app DB (to read the llm_config) and the AES key (to
# decrypt that config's API key). Read them from .env without exporting the
# whole file, so an unrelated stale key in .env cannot leak into the run.
if [[ -f "$ENV_FILE" ]]; then
  : "${SECRET_BOX_KEY:=$(grep -E '^SECRET_BOX_KEY=' "$ENV_FILE" | cut -d= -f2- || true)}"
  : "${DATABASE_URL:=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- || true)}"
fi
: "${DATABASE_URL:=postgresql+asyncpg://raymand:raymand@localhost:5432/raymand}"

if [[ -z "${SECRET_BOX_KEY:-}" ]]; then
  echo "SECRET_BOX_KEY is unset and not in $ENV_FILE — the llm_config's API key cannot be decrypted." >&2
  exit 2
fi

# 9 retries at base 3s doubling to a 90s ceiling is ~7.5 minutes of patience for
# a single call — far past a typical 429 window. Raise LLM_REQUEST_TIMEOUT_SECONDS
# too: a throttled provider is often slow before it is refusing.
export SECRET_BOX_KEY DATABASE_URL
export LLM_MAX_RETRIES="${LLM_MAX_RETRIES:-9}"
export LLM_RETRY_BASE_DELAY_SECONDS="${LLM_RETRY_BASE_DELAY_SECONDS:-3}"
export LLM_RETRY_MAX_DELAY_SECONDS="${LLM_RETRY_MAX_DELAY_SECONDS:-90}"
export LLM_REQUEST_TIMEOUT_SECONDS="${LLM_REQUEST_TIMEOUT_SECONDS:-120}"

# The deadline has to move with the retries or widening them achieves nothing:
# `pipeline.run` aborts the whole run at `deadline_at` regardless of how patient
# the gateway is willing to be, so at the stock 120s a backoff would still be
# mid-sleep when the run was already dead — scored ERROR, exactly the outcome
# this script exists to prevent. 600s clears the ~7.5min retry envelope with
# room for the rest of the pipeline.
export RUN_DEADLINE_SECONDS="${RUN_DEADLINE_SECONDS:-600}"

echo "retry policy: ${LLM_MAX_RETRIES} retries, ${LLM_RETRY_BASE_DELAY_SECONDS}s base -> ${LLM_RETRY_MAX_DELAY_SECONDS}s ceiling, ${LLM_REQUEST_TIMEOUT_SECONDS}s timeout, ${RUN_DEADLINE_SECONDS}s run deadline" >&2

exec python -m app.eval.runner "$@"
