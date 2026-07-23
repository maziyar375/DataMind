"""AppError -> RFC 7807 application/problem+json.

The frontend branches on `code`, never on English strings.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.context import get_correlation_id
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)


def _problem(status: int, code: str, title: str, detail: str, **extra) -> JSONResponse:
    body = {
        "type": f"https://raymand.dev/errors/{code.lower()}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "correlation_id": get_correlation_id(),
    }
    body.update(extra)
    return JSONResponse(
        status_code=status, content=body, media_type="application/problem+json"
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, err: AppError) -> JSONResponse:
        return _problem(
            err.http_status, err.code, err.title, err.message, **err.detail
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, err: RequestValidationError) -> JSONResponse:
        return _problem(
            422, "E_VALIDATION", "Invalid request",
            "One or more fields are invalid.",
            errors=[
                {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                for e in err.errors()
            ],
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, err: Exception) -> JSONResponse:
        log.exception("unhandled_exception")
        return _problem(
            500, "E_INTERNAL", "Internal error",
            "Something went wrong on our side. The correlation id identifies this request.",
        )
