"""ASGI app factory."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.context import set_correlation_id
from app.core.logging import configure_logging, get_logger
from app.infra.db.session import dispose_engine, get_sessionmaker
from app.services.bootstrap import ensure_admin
from app.workers.inprocess import InProcessRunExecutor
from app.workers.reconciler import reconcile_once, reconciler_loop

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(json_logs=not settings.debug, level="DEBUG" if settings.debug else "INFO")

    app.state.run_executor = InProcessRunExecutor(settings)

    async with get_sessionmaker()() as session:
        await ensure_admin(session, settings)

    # A process that died mid-run must not leave rows RUNNING forever.
    orphaned = await reconcile_once(settings)
    if orphaned:
        log.warning("startup_reconciled_orphans", count=orphaned)

    reconciler = asyncio.create_task(reconciler_loop(settings))
    log.info("raymand_started", environment=settings.environment)

    try:
        yield
    finally:
        reconciler.cancel()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Raymand",
        description="Conversational BI: ask a question, get a validated, auditable answer.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Last-Event-ID"],
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        cid = set_correlation_id(request.headers.get("X-Correlation-ID"))
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response

    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        from sqlalchemy import text

        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
