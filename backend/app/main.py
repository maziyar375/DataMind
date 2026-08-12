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
from app.infra.events.listener import RunEventListener
from app.services.bootstrap import ensure_admin
from app.workers.inprocess import InProcessRunExecutor
from app.workers.reconciler import reconcile_once, reconciler_loop
from app.workers.report import ReportRunExecutor
from app.workers.report import stranded_runs as stranded_report_runs
from app.workers.semantic import SemanticJobExecutor, sweep_orphans

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(json_logs=not settings.debug, level="DEBUG" if settings.debug else "INFO")

    app.state.run_executor = InProcessRunExecutor(settings)
    app.state.semantic_executor = SemanticJobExecutor(settings)
    app.state.report_executor = ReportRunExecutor(settings)

    async with get_sessionmaker()() as session:
        await ensure_admin(session, settings)

    # A process that died mid-run must not leave rows RUNNING forever.
    orphaned = await reconcile_once(settings)
    if orphaned:
        log.warning("startup_reconciled_orphans", count=orphaned)

    # Same for a generation: nothing was persisted, so the row is failed
    # rather than resumed, and the user is told it is safe to start again.
    stranded = await sweep_orphans()
    if stranded:
        log.warning("startup_failed_stranded_semantic_jobs", count=stranded)

    # A report run is minutes long, so a restart used to cost the user every
    # section that had not finished. It is resumed instead: the rows already
    # written say which blocks ran and which sections were narrated, so a
    # resumed run pays only for what is missing. Queued here rather than
    # awaited — startup must not block on minutes of generation.
    #
    # `stranded_report_runs` takes settings because it filters on the heartbeat
    # window: with more than one replica, "QUEUED or RUNNING at startup" also
    # describes a run another replica is generating right now, and resuming
    # that one would write every section twice.
    for run_id in await stranded_report_runs(settings):
        await app.state.report_executor.submit_resume(run_id)
        log.warning("startup_resumed_report_run", run_id=str(run_id))

    # Carries events from the process executing a run to the processes serving
    # the browsers watching it. Started before anything can be claimed.
    app.state.run_event_listener = RunEventListener(settings)
    app.state.run_event_listener.start()

    # The queue half of `RunService.claim`: picks up runs left unowned by a
    # process that died between committing the row and submitting it.
    app.state.run_executor.start_claiming()

    reconciler = asyncio.create_task(reconciler_loop(settings))
    log.info("raymand_started", environment=settings.environment)

    try:
        yield
    finally:
        reconciler.cancel()
        await app.state.run_executor.stop_claiming()
        await app.state.run_event_listener.stop()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DataMind",
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
