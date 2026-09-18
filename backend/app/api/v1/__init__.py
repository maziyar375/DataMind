from fastapi import APIRouter

from app.api.v1 import (
    access_review,
    audit,
    auth,
    connections,
    conversations,
    dashboards,
    directory,
    drafts,
    knowledge,
    llm_configs,
    reports,
    roles,
    semantic,
    service_users,
    teams,
    usage,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
# A peer of `users`, and gated on `role.read` / `role.manage` rather than on
# being an administrator — which is the point: administering *people* and
# administering *what people can do* are separable jobs, and the DataMind
# Maintainer role exists because they should be.
api_router.include_router(roles.router)
# The third of the people-shaped routers. `team.read` is held by five of the
# eight seed roles because a person has to be able to see the teams they are
# in; `team.manage` is Administrator's alone.
api_router.include_router(teams.router)
# The fourth people-shaped router, and the only one whose principals are
# machines. Gated `service_user.manage`, which Administrator and DataMind
# Maintainer hold — running the installation and administering people are
# different jobs, and minting an agent belongs to the first.
api_router.include_router(service_users.router)
# `audit.read`, and a peer of all of them for the same reason: they are about
# principals rather than about a connection's data.
api_router.include_router(audit.router)
# `access.review`, beside the audit log for the same reason and because they
# answer the two halves of one question: the log says what happened, the
# review says what is possible.
api_router.include_router(access_review.router)
# Who you can share with. Not an administration router: any signed-in person
# may read it, because choosing who else sees your own dashboard is part of
# owning one — and it carries names and kinds, never addresses.
api_router.include_router(directory.router)
api_router.include_router(llm_configs.router)
api_router.include_router(connections.router)
# Before the connections router would also work; after is fine because the
# paths are disjoint. Kept adjacent to `connections` since it extends it.
api_router.include_router(semantic.router)
# A peer of the semantic layer, extending `connections` the same way and for
# the same reason: a template describes one connection's schema and dies with
# it. Disjoint paths, so the mount order is free.
api_router.include_router(knowledge.router)
api_router.include_router(conversations.router)
# Drafting SQL is not scoped to a connection the way the semantic layer is —
# the connection is an input to a draft, not its owner.
api_router.include_router(drafts.router)
api_router.include_router(dashboards.router)
# A peer of dashboards, sharing no table and no code path with it: deleting
# either feature would leave the other working.
api_router.include_router(reports.router)
# The read side of the token accounting the three tables above already write.
# A peer of `audit` rather than of any feature router: it answers a question
# about *principals* — what everybody's use of the product cost — and holds
# `usage.read` on two of its three routes for the same reason the log holds
# `audit.read`.
api_router.include_router(usage.router)
