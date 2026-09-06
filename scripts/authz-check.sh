#!/usr/bin/env bash
# The authorization gate — docs/user-management-and-access-control-plan.md §18.4.
#
# Four greps, and each one names a way of deciding access that this codebase has
# agreed not to use. §18.4 says there are three enforcement shapes and no fourth:
# a declarative route guard, an in-service `authz.allowed(...)`, and a composed
# `authz.visible(...)`. Everything below is a fourth.
#
#   1. A bare ownership comparison in a route or a service. Ownership is a *fact
#      on a row*; whether it grants anything is the authorizer's answer, and a
#      service that decides for itself cannot later be shared.
#   2. `ctx.is_admin`. A role string standing in for a permission is exactly what
#      requirement 7 asks to be removed, and it is what makes "extensive
#      permissions over Knowledge without admin over everything" impossible.
#   3. `role == "ADMIN"` anywhere, backend or frontend. The UI renders affordances
#      from `GET /me/permissions` and `GET /{resource}/{id}/actions`, never from a
#      role it guessed.
#   4. A worker constructing a context out of nothing. There is no god context:
#      background work runs *as* a principal, through
#      `RequestContext.on_behalf_of`, or it is doing something the model does not
#      cover.
#
# **It fails today, on purpose.** It landed in CI as non-blocking in Phase 0 and
# flips to blocking at the end of Phase 2, when the last of those lines is gone.
# A gate that only ever passed would have told nobody anything.
#
# One escape hatch, and it is deliberately noisy: a line carrying
# `# authz-ok: <reason>` is exempt, and every exemption is printed at the end of
# every run. It exists for the one comparison that is *not* an access decision —
# the `unique (owner, name)` predicate behind `_refuse_duplicate_name`, which asks
# about the row that is about to be written and so can only be asked of that
# row's owner. Anything else carrying the marker is a review comment waiting to
# happen, which is why they are all printed.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0

check() {
  local label="$1" pattern="$2"
  shift 2
  echo "── ${label}"
  if grep -rnE "${pattern}" "$@" 2>/dev/null | grep -v "authz-ok:"; then
    fail=1
  else
    echo "   clean"
  fi
}

check "a service or route deciding ownership for itself" \
      "owner_id[[:space:]]*[!=]=" backend/app/api backend/app/services

check "a role string standing in for a permission" \
      "\.is_admin" backend/app/api backend/app/services backend/app/workers

check "an ADMIN literal compared anywhere" \
      "role == ['\"]ADMIN" backend/app frontend/src

check "a worker acting as nobody" \
      "ctx=None" backend/app/workers

echo "── deliberate exemptions, each carrying its reason"
grep -rn "authz-ok:" backend/app frontend/src 2>/dev/null || echo "   none"

if [ "${fail}" -ne 0 ]; then
  echo
  echo "authz-check: the lines above decide access outside the authorizer."
  echo "Non-blocking until Phase 2 of docs/user-management-and-access-control-plan.md."
  exit 1
fi

echo
echo "authz-check: clean."
