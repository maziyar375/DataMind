from fastapi import APIRouter

from app.api.v1 import auth, connections, conversations, llm_configs, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(llm_configs.router)
api_router.include_router(connections.router)
api_router.include_router(conversations.router)
