"""Middleware: assigns a request ID, records the client IP, logs each request, adds the X-Request-ID header."""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx
from app.core.request_context import client_ip_ctx

logger = logging.getLogger("app.request")

# Only accept client-provided IDs that are short and made of safe characters.
# (Prevents someone injecting junk or fake log lines through the header.)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _get_request_id(header_value: str | None) -> str:
    if header_value and _SAFE_ID.match(header_value):
        return header_value
    return uuid.uuid4().hex


def _get_client_ip(request: Request) -> str | None:
    """The address of the direct TCP peer, cut to 45 chars (the audit column size).

    We deliberately do NOT read X-Forwarded-For: any client can fake that header.
    Behind a reverse proxy this is the proxy's address (see design notes).
    """
    return request.client.host[:45] if request.client else None


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _get_request_id(request.headers.get("X-Request-ID"))

        # Two places: request.state (for exception handlers) and the
        # ContextVar (for the logger).
        request.state.request_id = request_id
        id_token = request_id_ctx.set(request_id)
        ip_token = client_ip_ctx.set(_get_client_ip(request))
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Unexpected crash: the global handler logs the traceback.
            logger.error(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            return response
        finally:
            client_ip_ctx.reset(ip_token)
            request_id_ctx.reset(id_token)
