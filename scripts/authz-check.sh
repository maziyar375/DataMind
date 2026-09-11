#!/usr/bin/env bash
# The authorization gate — docs/plans/user-management-and-access-control.md §18.4.
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
# **It is blocking as of Phase 2.** It landed non-blocking in Phase 0 against
# ~40 lines and went green when the last of them was removed; a new one now
# fails the build. A gate that only ever passed would have told nobody anything,
# and one that never went green would have been deleted.
#
# One escape hatch, and it is deliberately noisy: a line carrying
# `# authz-ok: <reason>` **on that line** is exempt, and every exemption is
# printed at the end of every run. Two kinds are live today:
#
#   * The `unique (owner, name)` predicates. These ask about the row that is
#     about to be *written*, whose owner is the caller by construction, so they
#     are not access decisions at all. Same for the embedding-candidate query,
#     which picks whose provider pays rather than who may reach what.
#   * Three lines that say **retires in Phase 3** — `require_admin`,
#     `RequestContext.is_admin`, and `can_curate`'s administrator arm. They are
#     the `AdminDep` surface, and they go when `needs(capability)` replaces it.
#
# Anything else carrying the marker is a review comment waiting to happen, which
# is why they are all printed.
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

# `===` as well as `==`: this arm was written for Python and silently matched
# nothing in the SPA for two phases, because TypeScript spells it with three
# characters. A gate that cannot fail is a comment.
check "an ADMIN literal compared anywhere" \
      "role ===? ['\"]ADMIN" backend/app frontend/src

check "a worker acting as nobody" \
      "ctx=None" backend/app/workers

echo "── deliberate exemptions, each carrying its reason"
grep -rn "authz-ok:" backend/app frontend/src 2>/dev/null || echo "   none"

if [ "${fail}" -ne 0 ]; then
  echo
  echo "authz-check: the lines above decide access outside the authorizer."
  echo "Non-blocking until Phase 2 of docs/plans/user-management-and-access-control.md."
  exit 1
fi

echo
echo "authz-check: clean."
