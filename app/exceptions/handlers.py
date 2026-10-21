"""Global exception handlers: turn exceptions into the standard error JSON."""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions.errors import AppError

logger = logging.getLogger(__name__)

# Friendly error codes for common framework HTTP errors.
_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "TOO_MANY_REQUESTS",
}


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the standard error body: {"error": {code, message, request_id}}."""
    request_id = getattr(request.state, "request_id", None)

    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if details:
        error["details"] = details

    response_headers: dict[str, str] = {}
    if request_id:
        response_headers["X-Request-ID"] = request_id
    if headers:
        response_headers.update(headers)  # e.g. Retry-After
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=response_headers or None,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Our own business errors (InsufficientFundsError, etc.)."""
    logger.warning(
        "business_rule_violation",
        extra={
            "error_code": exc.code,
            "status_code": exc.status_code,
            "path": request.url.path,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return _error_response(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        headers=getattr(exc, "headers", None),  # only some errors carry headers
    )


async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """FastAPI/Pydantic rejected the request body or params (HTTP 422)."""
    # We list only field name + message. We deliberately do NOT echo the
    # submitted values back (they could contain passwords).
    details = [
        {
            "field": ".".join(str(part) for part in err["loc"][1:]),
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    logger.warning(
        "request_validation_failed",
        extra={
            "path": request.url.path,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return _error_response(
        request, 422, "VALIDATION_ERROR", "Request validation failed.", details
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Framework errors like 404 (unknown URL) or 405 (wrong method)."""
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    return _error_response(request, exc.status_code, code, str(exc.detail))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: a bug. Log full details, tell the client nothing sensitive."""
    logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={
            "path": request.url.path,
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    return _error_response(
        request, 500, "INTERNAL_ERROR", "An unexpected error occurred."
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all handlers to the app (called once in main.py)."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)