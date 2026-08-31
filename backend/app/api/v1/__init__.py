from fastapi import APIRouter

from app.api.v1 import (
    auth,
    connections,
    conversations,
    dashboards,
    drafts,
    knowledge,
    llm_configs,
    reports,
    semantic,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
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
